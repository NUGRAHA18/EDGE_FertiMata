"""Link MQTT: kirim telemetri/status/ACK/alert/AI, terima perintah (Kontrak MQTT).

Alur perintah (Kontrak §5): topik → device, JSON → payload, buang duplikat `commandUid`,
tolak yang kedaluwarsa (`EXPIRED`, keputusan D5), tolak device/aktuator/perintah asing
(`UNSUPPORTED`), lalu teruskan ke `CommandHandler` dan balas satu ACK. Perintah diproses satu per
satu sesuai urutan datang, supaya ON lalu OFF tidak tertukar.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .. import __version__
from ..commands import (
    REASON_EXPIRED,
    REASON_INVALID,
    REASON_UNSUPPORTED,
    RELAY_ACTIONS,
    VIRTUAL_ACTIONS,
    Command,
    CommandHandler,
    CommandResult,
)
from ..config import Config
from ..hal import Clock
from ..health import DeviceHealth
from ..logs import fields
from .outbox import Outbox, OutgoingMessage
from .payloads import (
    AckPayload,
    AiPayload,
    AlertPayload,
    CommandPayload,
    StatusPayload,
    TelemetryPayload,
)
from .topics import Topics
from .transport import MqttTransport

log = logging.getLogger(__name__)

# Isi tambahan pesan status per device: actuators (F4), powerSource (F5), metadata.
StatusExtras = Callable[[str], dict[str, Any]]


class Link:
    def __init__(
        self,
        config: Config,
        transport: MqttTransport,
        clock: Clock,
        health: DeviceHealth,
        handler: CommandHandler,
        status_extras: StatusExtras | None = None,
    ) -> None:
        self.config = config
        self.mqtt = config.mqtt
        self.transport = transport
        self.clock = clock
        self.health = health
        self.handler = handler
        self.status_extras = status_extras or self._default_status_extras
        self.topics = Topics(config.mqtt.topics)
        self.outbox = Outbox(config.mqtt.outbox_max)
        self._seen_uids: OrderedDict[str, None] = OrderedDict()
        self._alert_sent: dict[tuple[str, str], float] = {}
        self._inbound: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self._wake_sender = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._gateway = next(
            (n for n, d in config.logical_devices.items() if "soc" in d.sources), None
        )

    # --- siklus hidup -------------------------------------------------------------------------

    async def start(self) -> None:
        self.transport.on_connect = self._on_connect
        self.transport.on_disconnect = self._on_disconnect
        self.transport.on_message = self._on_message
        self._tasks = [
            asyncio.create_task(self._sender(), name="mqtt-sender"),
            asyncio.create_task(self._command_worker(), name="mqtt-commands"),
            asyncio.create_task(self._heartbeat(), name="mqtt-heartbeat"),
        ]
        await self.transport.start([self.topics.command_subscription])

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        await self.transport.stop()

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return list(self._tasks)

    # --- kirim --------------------------------------------------------------------------------

    def publish_telemetry(self, device_id: str, data: dict[str, Any]) -> bool:
        payload = TelemetryPayload(timestamp=self.clock.now(), data=data)
        if not payload.data:
            return False  # semua metrik gagal dibaca: tidak ada yang dikirim
        self._enqueue("telemetry", self.topics.telemetry(device_id), payload.to_bytes())
        return True

    def publish_status(self, device_id: str, **extras: Any) -> None:
        payload = StatusPayload(timestamp=self.clock.now(), **extras)
        self._enqueue("status", self.topics.status(device_id), payload.to_bytes())

    def publish_ack(self, device_id: str, command_uid: str, result: CommandResult) -> None:
        payload = AckPayload(
            command_uid=command_uid,
            success=result.success,
            actuator_state=result.actuator_state,
            executed_at=self.clock.now(),
            error_message=None if result.success else result.error_message,
        )
        self._enqueue("command_ack", self.topics.command_ack(device_id), payload.to_bytes())
        log.info(
            "ACK dikirim",
            extra=fields(
                device=device_id,
                uid=command_uid,
                success=result.success,
                error=result.error_message,
            ),
        )

    def publish_alert(
        self,
        device_id: str,
        type: str,
        severity: str,
        title: str,
        message: str,
        *,
        actuator_code: str | None = None,
        sensor_code: str | None = None,
        metadata: dict[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        """Kirim alert, kecuali tipe yang sama untuk device ini baru saja dikirim (Kontrak §4.4).

        INFO tidak dijeda: isinya hasil satu kejadian (patroli selesai, trolley tiba), bukan kondisi
        yang berulang, dan backend mencatat setiap INFO tanpa menahan duplikat.
        """
        key = (device_id, type)
        now = self.clock.monotonic()
        last = self._alert_sent.get(key)
        spaced = severity != "INFO" and not force
        if spaced and last is not None and now - last < self.mqtt.alert_repeat_s:
            return False
        payload = AlertPayload(
            type=type,
            severity=severity,
            title=title,
            message=message,
            timestamp=self.clock.now(),
            actuator_code=actuator_code,
            sensor_code=sensor_code,
            metadata=metadata,
        )
        self._alert_sent[key] = now
        self._enqueue("alert", self.topics.alert(device_id), payload.to_bytes())
        log.warning("alert", extra=fields(device=device_id, type=type, severity=severity))
        return True

    def publish_ai(self, device_id: str, payload: AiPayload) -> None:
        self._enqueue("ai", self.topics.ai(device_id), payload.to_bytes())

    def _enqueue(self, kind: str, topic: str, payload: bytes) -> None:
        self.outbox.push(OutgoingMessage(kind, topic, payload))
        self._wake_sender.set()

    async def _sender(self) -> None:
        while True:
            await self._wake_sender.wait()
            self._wake_sender.clear()
            while self.transport.connected:
                message = self.outbox.pop()
                if message is None:
                    break
                if not self.transport.publish(message.topic, message.payload):
                    self.outbox.push_front(message)
                    break

    # --- heartbeat ----------------------------------------------------------------------------

    def _default_status_extras(self, device_id: str) -> dict[str, Any]:
        if device_id == self._gateway:
            return {"metadata": {"agentVersion": __version__}}
        return {}

    def send_heartbeats(self) -> list[str]:
        """Satu putaran status untuk setiap device logis yang masih menjawab."""
        sent = []
        for device_id in self.health.alive_devices():
            self.publish_status(device_id, **self.status_extras(device_id))
            sent.append(device_id)
        return sent

    async def _heartbeat(self) -> None:
        while True:
            self.send_heartbeats()
            await self.clock.sleep(self.mqtt.heartbeat_s)

    # --- callback transport -------------------------------------------------------------------

    def _on_connect(self, session_present: bool) -> None:
        log.info(
            "MQTT tersambung",
            extra=fields(session_present=session_present, queued=len(self.outbox)),
        )
        self._wake_sender.set()

    def _on_disconnect(self, reason: str) -> None:
        log.warning("MQTT terputus", extra=fields(reason=reason))

    def _on_message(self, topic: str, payload: bytes) -> None:
        self._inbound.put_nowait((topic, payload))

    # --- perintah -----------------------------------------------------------------------------

    async def _command_worker(self) -> None:
        while True:
            topic, payload = await self._inbound.get()
            try:
                await self.handle_command_message(topic, payload)
            except Exception:
                log.exception("galat saat memproses perintah", extra=fields(topic=topic))

    async def handle_command_message(self, topic: str, raw: bytes) -> None:
        device_id = self.topics.device_from_command(topic)
        if device_id is None:
            log.warning("pesan di topik bukan command", extra=fields(topic=topic))
            return
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("perintah bukan JSON, diabaikan", extra=fields(device=device_id))
            return
        uid = data.get("commandUid") if isinstance(data, dict) else None
        if not isinstance(uid, str) or not uid:
            log.warning("perintah tanpa commandUid, diabaikan", extra=fields(device=device_id))
            return
        if not self._remember(uid):
            log.info("perintah ganda diabaikan", extra=fields(device=device_id, uid=uid))
            return

        try:
            payload = CommandPayload.model_validate(data)
        except (ValidationError, ValueError) as err:
            log.warning("perintah tidak valid", extra=fields(device=device_id, uid=uid, err=err))
            self.publish_ack(
                device_id, uid, CommandResult.failed(REASON_INVALID, "isi perintah tidak lengkap")
            )
            return

        result = self._precheck(device_id, payload)
        if result is None:
            target = self.config.output_by_actuator(
                payload.actuator_code
            ) or self.config.virtual_by_actuator(payload.actuator_code)
            command = Command(device_id, target or "", payload)
            log.info(
                "perintah diterima",
                extra=fields(
                    device=device_id,
                    uid=uid,
                    actuator=payload.actuator_code,
                    command=command.action,
                    duration=payload.duration_s,
                ),
            )
            try:
                result = await self.handler.execute(command)
            except Exception:
                log.exception("eksekutor perintah gagal", extra=fields(uid=uid))
                result = CommandResult.failed("ERROR", "terjadi gangguan di alat, coba lagi")
        self.publish_ack(device_id, uid, result)

    def _precheck(self, device_id: str, payload: CommandPayload) -> CommandResult | None:
        """Pemeriksaan kontrak sebelum eksekutor. None = boleh diteruskan."""
        if payload.device_id and payload.device_id != device_id:
            # Backend mempercayai topik; field payload hanya dicatat.
            log.warning(
                "deviceId payload beda dengan topik",
                extra=fields(topic_device=device_id, payload_device=payload.device_id),
            )
        if payload.expires_at is not None and self.clock.now() > payload.expires_at:
            return CommandResult.failed(REASON_EXPIRED, "perintah sudah kedaluwarsa")
        logical = self.config.logical_devices.get(device_id)
        if logical is None:
            return CommandResult.failed(REASON_UNSUPPORTED, "alat ini tidak dikenal")
        action = payload.command.upper()
        output = self.config.output_by_actuator(payload.actuator_code)
        if output is not None:
            if self.config.outputs[output].device not in logical.sources:
                return CommandResult.failed(REASON_UNSUPPORTED, "alat ini tidak dikenal")
            actions = RELAY_ACTIONS
        else:
            # Aktuator virtual (Kontrak §5.1) terikat ke satu device logis, bukan ke relay.
            kind = self.config.virtual_by_actuator(payload.actuator_code)
            if kind is None or self.config.virtual_actuators[kind].device != device_id:
                return CommandResult.failed(REASON_UNSUPPORTED, "alat ini tidak dikenal")
            actions = VIRTUAL_ACTIONS.get(kind, frozenset())
        if action not in actions:
            return CommandResult.failed(REASON_UNSUPPORTED, "jenis perintah tidak dikenal")
        return None

    def _remember(self, uid: str) -> bool:
        """True bila UID baru. Menyimpan `dedup_size` UID terakhir (Kontrak §5 aturan 6)."""
        if uid in self._seen_uids:
            self._seen_uids.move_to_end(uid)
            return False
        self._seen_uids[uid] = None
        while len(self._seen_uids) > self.mqtt.dedup_size:
            self._seen_uids.popitem(last=False)
        return True
