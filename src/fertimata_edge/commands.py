"""Penghubung perintah dari `link` ke eksekutor output (Kontrak §5).

`link` sudah memeriksa kontrak (dedup, `expiresAt`, aktuator dikenal). `OutputCommandHandler`
menjalankan sisanya: durasi wajib, interlock lokal (`safety`), urutan jeda S12, tulis + baca
balik + watchdog (`outputs`). ACK sukses hanya setelah baca balik cocok (Kontrak §4.3).
Perintah ke aktuator virtual (Kontrak §5.1) diteruskan `CommandRouter` ke `virtual.py`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .bus import Priority
from .logs import fields

if TYPE_CHECKING:  # hindari impor melingkar: link mengimpor modul ini
    from .config import Config
    from .hal import Clock
    from .link.payloads import ActuatorState, CommandPayload
    from .outputs import Outputs
    from .safety import Safety

log = logging.getLogger(__name__)

# Kode alasan di awal errorMessage (Kontrak §4.3). Teks setelahnya untuk petani.
REASON_EXPIRED = "EXPIRED"
REASON_UNSUPPORTED = "UNSUPPORTED"
REASON_INVALID = "INVALID"
REASON_NOT_READY = "NOT_READY"

# Kosakata relay mentah dan aktuator virtual (Kontrak §5, §5.1). Backend menolak yang lain (400);
# edge memeriksa lagi dan menjawab UNSUPPORTED.
RELAY_ACTIONS = frozenset({"ON", "OFF"})
VIRTUAL_ACTIONS: dict[str, frozenset[str]] = {
    "trolley_goto": frozenset({"HOME", "GOTO_STATION", "STOP"}),
    "trolley_patrol": frozenset({"START", "STOP"}),
}


def error_message(reason: str, text: str) -> str:
    return f"{reason}: {text}"


@dataclass(frozen=True)
class CommandResult:
    success: bool
    actuator_state: ActuatorState | None = None
    error_message: str | None = None

    @classmethod
    def failed(cls, reason: str, text: str, state: ActuatorState | None = None) -> CommandResult:
        return cls(False, state, error_message(reason, text))


@dataclass(frozen=True)
class Command:
    """Perintah yang sudah lolos pemeriksaan kontrak di `link`."""

    device_id: str  # dari topik, bukan payload (Kontrak §2)
    output: str  # nama output di config, atau jenis aktuator virtual (`virtual_actuators`)
    payload: CommandPayload

    @property
    def action(self) -> str:
        return self.payload.command.upper()


class CommandHandler(ABC):
    @abstractmethod
    async def execute(self, command: Command) -> CommandResult: ...


class NotReadyHandler(CommandHandler):
    """Eksekutor yang menolak semua perintah, mis. untuk menguji link tanpa relay."""

    async def execute(self, command: Command) -> CommandResult:
        return CommandResult.failed(
            REASON_NOT_READY, "alat belum bisa dikendalikan dari aplikasi, coba lagi nanti"
        )


class CommandRouter(CommandHandler):
    """Relay mentah → `relays`; aktuator virtual (`config.virtual_actuators`) → `virtual`."""

    def __init__(
        self, config: Config, relays: CommandHandler, virtual: CommandHandler | None
    ) -> None:
        self.config = config
        self.relays = relays
        self.virtual = virtual

    async def execute(self, command: Command) -> CommandResult:
        if command.output in self.config.virtual_actuators:
            if self.virtual is None:
                return CommandResult.failed(REASON_UNSUPPORTED, "alat ini belum bisa dipakai")
            return await self.virtual.execute(command)
        return await self.relays.execute(command)


def _state(on: bool | None) -> ActuatorState | None:
    return None if on is None else ("ON" if on else "OFF")


class OutputCommandHandler(CommandHandler):
    def __init__(self, config: Config, outputs: Outputs, safety: Safety, clock: Clock) -> None:
        self.config = config
        self.outputs = outputs
        self.safety = safety
        self.clock = clock

    async def execute(self, command: Command) -> CommandResult:
        name = command.output
        out = self.config.outputs[name]
        on = command.action == "ON"
        duration = command.payload.duration_s
        current = _state(self.outputs.state(name))
        if on:
            # Kontrak §5 aturan 4: lapis kedua setelah validasi backend.
            if out.requires_duration and duration is None:
                return CommandResult.failed(
                    REASON_INVALID, "lama menyala (detik) wajib diisi", current
                )
            if duration is not None and duration <= 0:
                return CommandResult.failed(
                    REASON_INVALID, "lama menyala harus lebih dari 0 detik", current
                )
        refusal = self.safety.check(name, on)
        if refusal is not None:
            log.info(
                "perintah ditolak interlock",
                extra=fields(output=name, on=on, reason=refusal.reason),
            )
            return CommandResult.failed(refusal.reason, refusal.text, current)
        delay = self.safety.settle_delay(name, on)
        if delay > 0:
            await self.clock.sleep(delay)
            # Keadaan bisa berubah selama jeda (mis. STOP darurat): periksa ulang.
            refusal = self.safety.check(name, on)
            if refusal is not None:
                return CommandResult.failed(
                    refusal.reason, refusal.text, _state(self.outputs.state(name))
                )
        # OFF memakai prioritas STOP: selalu didahulukan di antrean bus (S3).
        result = await self.outputs.set(
            name,
            on,
            duration_s=duration if on else None,
            priority=Priority.COMMAND if on else Priority.STOP,
        )
        if not result.ok:
            assert result.reason is not None and result.text is not None
            return CommandResult.failed(result.reason, result.text, _state(result.state))
        if on:
            await self.safety.recheck_travel()
            if self.outputs.state(name) is not True:
                return CommandResult.failed(
                    "INTERLOCK_LIMIT",
                    "trolley sudah di ujung rel ke arah itu",
                    _state(self.outputs.state(name)),
                )
        return CommandResult(True, _state(result.state))
