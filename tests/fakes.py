from __future__ import annotations

import json
from typing import Any

from fertimata_edge.commands import Command, CommandHandler, CommandResult
from fertimata_edge.link import MqttTransport


class FakeTransport(MqttTransport):
    """Broker palsu: menyimpan pesan terbit, test memicu sambung/putus/pesan masuk."""

    def __init__(self, connected: bool = True) -> None:
        super().__init__()
        self._connected = False
        self._auto_connect = connected
        self.published: list[tuple[str, bytes]] = []
        self.subscriptions: list[str] = []
        self.fail_publish = False
        self.started = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self, subscriptions: list[str]) -> None:
        self.started = True
        self.subscriptions = list(subscriptions)
        if self._auto_connect:
            self.connect()

    async def stop(self) -> None:
        self.started = False
        self._connected = False

    def publish(self, topic: str, payload: bytes) -> bool:
        if not self._connected or self.fail_publish:
            return False
        self.published.append((topic, payload))
        return True

    def connect(self, session_present: bool = False) -> None:
        self._connected = True
        self.on_connect(session_present)

    def disconnect(self) -> None:
        self._connected = False
        self.on_disconnect("test")

    def deliver(self, topic: str, payload: dict | bytes) -> None:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.on_message(topic, raw)

    def messages(self, suffix: str = "") -> list[tuple[str, dict]]:
        return [(t, json.loads(p)) for t, p in self.published if t.endswith(suffix)]


class RecordingHandler(CommandHandler):
    def __init__(self, result: CommandResult | None = None) -> None:
        self.result = result or CommandResult(True, "ON")
        self.commands: list[Command] = []

    async def execute(self, command: Command) -> CommandResult:
        self.commands.append(command)
        return self.result


class RecordingSink:
    """Pengganti link untuk modul yang hanya mengirim telemetri dan alert."""

    def __init__(self) -> None:
        self.telemetry: list[tuple[str, dict[str, Any]]] = []
        self.alerts: list[dict[str, Any]] = []

    def publish_telemetry(self, device_id, data):
        self.telemetry.append((device_id, dict(data)))
        return True

    def publish_alert(self, device_id, type, severity, title, message, **kwargs):
        self.alerts.append(
            {
                "device": device_id,
                "type": type,
                "severity": severity,
                "title": title,
                "message": message,
                **kwargs,
            }
        )
        return True

    def of_type(self, type: str) -> list[dict[str, Any]]:
        return [a for a in self.alerts if a["type"] == type]
