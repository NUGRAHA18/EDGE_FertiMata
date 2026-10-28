"""Aktuator virtual dari backend (Kontrak §5.1, BE-1, keputusan D2): perintah tingkat tinggi.

- `TROLLEY-GOTO` (F7.5): `HOME`, `GOTO_STATION {"station": n}`, `STOP`.
- `TROLLEY-PATROL` (F8.4): `START` (patroli manual, `Patrol.run(trigger="manual")`), `STOP`.
- `DOSING-LOOP` (F9): `DOSE_TO_TARGET {"targetEc": x, "phase": "..."}` (loop §8.2, `dosing.py`),
  `STOP` (loop berhenti, semua pompa pupuk dan aduk OFF).

ACK sukses dengan state `ON` begitu urutan **dimulai** (Kontrak §4.3); urutan berjalan sebagai tugas
latar. Selesai atau batal → status `actuators` `OFF` tanpa ACK baru, hasil lewat alert INFO
(`TROLLEY_ARRIVED`, `PATROL_DONE`, `DOSING_DONE`) atau WARNING. Prasyarat yang bisa diperiksa di
depan (trolley terkunci/dipakai, daya S7, float S8, eksklusif S4–S6, disk penuh, angka P-H12 belum
diisi) menolak perintah lewat ACK gagal.

`STOP` tidak pernah ditolak (S3): selalu mematikan RUN (trolley) atau semua output dosing dengan
prioritas STOP, walaupun tidak ada urutan virtual yang berjalan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from .commands import (
    REASON_INVALID,
    REASON_UNSUPPORTED,
    Command,
    CommandHandler,
    CommandResult,
)
from .config import Config
from .dosing import DosingLoop
from .hal import Clock
from .logs import fields
from .patrol import Patrol
from .poller import TelemetrySink
from .safety import REASON_BUSY, REASON_INTERLOCK_EXCLUSIVE, Safety
from .trolley import (
    REASON_LIMIT,
    REASON_LOCKED,
    REASON_NO_REED,
    REASON_TIMEOUT,
    Trolley,
    TrolleyError,
)

log = logging.getLogger(__name__)

GOTO = "trolley_goto"
PATROL = "trolley_patrol"
DOSING = "dosing_loop"

# Kegagalan yang sudah punya alert sendiri dari modul trolley (jangan dobel di dashboard).
_ALERTED_BY_TROLLEY = frozenset({REASON_NO_REED, REASON_TIMEOUT, REASON_LIMIT})


class VirtualActuators(CommandHandler):
    def __init__(
        self,
        config: Config,
        trolley: Trolley,
        patrol: Patrol | None,
        safety: Safety,
        clock: Clock,
        sink: TelemetrySink | None = None,
        dosing: DosingLoop | None = None,
    ) -> None:
        self.config = config
        self.dosing = dosing
        self.trolley = trolley
        self.patrol = patrol
        self.safety = safety
        self.clock = clock
        self.sink = sink
        self._goto_task: asyncio.Task[None] | None = None
        self._goto_active = False
        self._goto_stopping = False
        self._patrol_task: asyncio.Task[None] | None = None
        self._patrol_starting = False  # START diterima, `Patrol.run` belum mulai
        # Dipanggil dengan device logis yang state aktuator virtualnya berubah (status §4.2).
        self.listeners: list[Callable[[str], None]] = []
        if patrol is not None:
            patrol.listeners.append(self._on_patrol_changed)
        if dosing is not None:
            dosing.listeners.append(lambda: self._notify(DOSING))

    # --- state (Kontrak §4.2) -----------------------------------------------------------------

    def is_on(self, kind: str) -> bool:
        if kind == GOTO:
            return self._goto_active
        if kind == PATROL:
            # Patroli terjadwal ikut dilaporkan: backend memakai state ini untuk interlock.
            return self._patrol_starting or (self.patrol is not None and self.patrol.running)
        if kind == DOSING:
            return self.dosing is not None and self.dosing.running
        return False

    def actuator_states(self, device_id: str) -> dict[str, str]:
        return {
            virtual.actuator_code: "ON" if self.is_on(kind) else "OFF"
            for kind, virtual in self.config.virtual_actuators.items()
            if virtual.device == device_id
        }

    def _notify(self, kind: str) -> None:
        virtual = self.config.virtual_actuators.get(kind)  # type: ignore[call-overload]
        if virtual is None:
            return
        for listener in self.listeners:
            listener(virtual.device)

    def _on_patrol_changed(self) -> None:
        if self.patrol is not None and self.patrol.running:
            self._patrol_starting = False
        self._notify(PATROL)

    # --- siklus hidup -------------------------------------------------------------------------

    async def stop(self) -> None:
        """Agent berhenti: batalkan urutan yang berjalan (trolley dihentikan di `finally`-nya)."""
        for task in (self._goto_task, self._patrol_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self.dosing is not None:
            await self.dosing.stop()

    # --- perintah -----------------------------------------------------------------------------

    async def execute(self, command: Command) -> CommandResult:
        kind, action = command.output, command.action
        if action == "STOP":
            if kind == PATROL:
                await self._stop_patrol()
            elif kind == DOSING:
                if self.dosing is not None:
                    await self.dosing.stop()
            else:
                await self._stop_goto()
            return CommandResult(True, "OFF")
        if kind == DOSING and action == "DOSE_TO_TARGET" and self.dosing is not None:
            return self._start_dosing(command)
        if kind == GOTO and action in ("HOME", "GOTO_STATION"):
            return self._start_goto(command)
        if kind == PATROL and action == "START" and self.patrol is not None:
            return self._start_patrol()
        return CommandResult.failed(REASON_UNSUPPORTED, "jenis perintah tidak dikenal")

    def _current(self, kind: str) -> str:
        return "ON" if self.is_on(kind) else "OFF"

    def _refuse_trolley(self, kind: str) -> CommandResult | None:
        """Prasyarat bersama HOME/GOTO_STATION/START. None = boleh mulai."""
        trolley = self.trolley
        state = self._current(kind)
        if not trolley.gpio.available or trolley.locked is not None:
            return CommandResult.failed(
                REASON_LOCKED, "trolley tidak bisa dipakai, periksa sensor di rel", state
            )
        if self.is_on(PATROL):
            return CommandResult.failed(REASON_BUSY, "patroli kamera sedang berjalan", state)
        if self._goto_active or trolley.busy:
            return CommandResult.failed(REASON_BUSY, "trolley sedang menuju stasiun", state)
        if trolley.running is not False:
            text = (
                "trolley sedang berjalan"
                if trolley.running
                else "keadaan trolley belum terbaca, coba lagi sebentar"
            )
            return CommandResult.failed(REASON_BUSY, text, state)
        refusal = self.safety.check_activation(trolley.travel.run)
        if refusal is not None:
            return CommandResult.failed(refusal.reason, refusal.text, state)
        return None

    # --- TROLLEY-GOTO (F7.5) ------------------------------------------------------------------

    def _start_goto(self, command: Command) -> CommandResult:
        target = 0
        if command.action == "GOTO_STATION":
            stations = self.config.trolley.stations
            station = command.payload.parameters.get("station")
            # Kontrak §5.1: integer JSON. bool adalah int di Python, jadi ditolak terpisah.
            if isinstance(station, bool) or not isinstance(station, int):
                return CommandResult.failed(
                    REASON_INVALID, f"nomor stasiun wajib diisi (1–{stations})", "OFF"
                )
            if not 1 <= station <= stations:
                return CommandResult.failed(
                    REASON_INVALID, f"nomor stasiun harus 1–{stations}", "OFF"
                )
            target = station
        refusal = self._refuse_trolley(GOTO)
        if refusal is not None:
            return refusal
        self._goto_active = True
        self._goto_stopping = False
        self._goto_task = asyncio.create_task(self._run_goto(target), name="trolley-goto")
        self._notify(GOTO)
        return CommandResult(True, "ON")

    async def _run_goto(self, target: int) -> None:
        log.info("trolley dikirim", extra=fields(target=target))
        # Kunci DIR/pan/RUN ON mentah selama seluruh urutan (homing + jalan), bukan per segmen.
        self.trolley.hold_raw_commands("trolley sedang menuju stasiun")
        try:
            if target == 0:
                await self.trolley.homing()
            else:
                await self.trolley.goto_station(target)
        except TrolleyError as err:
            log.warning("trolley tidak sampai", extra=fields(target=target, reason=err.reason))
            if not self._goto_stopping:
                self._alert_goto_failed(target, err)
        except Exception:
            # Galat tak terduga tidak boleh mematikan agent (S1 akan memutus irigasi).
            log.exception("perintah trolley gagal karena galat tak terduga")
        else:
            self._alert_arrived(target)
        finally:
            self.trolley.release_raw_commands()
            self._goto_active = False
            self._notify(GOTO)

    async def _stop_goto(self) -> None:
        self._goto_stopping = self._goto_active
        await self.trolley.halt("stop-goto")
        task = self._goto_task
        if task is not None and not task.done():
            await task

    # --- TROLLEY-PATROL (F8.4) ----------------------------------------------------------------

    def _start_patrol(self) -> CommandResult:
        assert self.patrol is not None
        refusal = self._refuse_trolley(PATROL)
        if refusal is not None:
            return refusal
        problem = self.patrol.readiness()
        if problem is not None:
            return CommandResult.failed(problem.reason, problem.text, "OFF")
        self._patrol_starting = True
        self._patrol_task = asyncio.create_task(self._run_patrol(), name="patrol-manual")
        self._notify(PATROL)
        return CommandResult(True, "ON")

    async def _run_patrol(self) -> None:
        assert self.patrol is not None
        try:
            await self.patrol.run(trigger="manual")
        except Exception:
            log.exception("patroli manual gagal karena galat tak terduga")
        finally:
            if self._patrol_starting:  # `run` keluar sebelum patroli benar-benar berjalan
                self._patrol_starting = False
                self._notify(PATROL)

    async def _stop_patrol(self) -> None:
        task = self._patrol_task
        if self._patrol_starting and task is not None and not task.done():
            # Belum mulai sama sekali: cukup dibatalkan, belum ada yang bergerak.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.patrol is not None and self.patrol.request_stop():
            await self.trolley.halt("stop-patrol")
            await self.patrol.wait_idle()
        elif self.trolley.busy:
            # STOP tetap berarti RUN OFF walau yang berjalan bukan patroli (S3).
            await self.trolley.halt("stop-patrol")

    # --- DOSING-LOOP (F9) ----------------------------------------------------------------------

    def _start_dosing(self, command: Command) -> CommandResult:
        assert self.dosing is not None
        cfg = self.config.dosing
        params = command.payload.parameters
        target, phase = params.get("targetEc"), params.get("phase")
        low, high = cfg.target_ec_ms_cm
        if (
            isinstance(target, bool)
            or not isinstance(target, (int, float))
            or not low <= target <= high
        ):
            return CommandResult.failed(
                REASON_INVALID, f"target kadar pupuk (EC) wajib diisi {low:g}–{high:g}", "OFF"
            )
        if not isinstance(phase, str) or phase not in cfg.ratio:
            return CommandResult.failed(
                REASON_INVALID, f"fase tanaman harus salah satu dari: {', '.join(cfg.ratio)}", "OFF"
            )
        state = self._current(DOSING)
        if self.dosing.running:
            return CommandResult.failed(
                REASON_BUSY, "pemberian pupuk otomatis sedang berjalan", state
            )
        # §8.2 langkah 1: trolley diam. Gerak otomatis/patroli yang sedang berhenti di stasiun
        # juga dihitung, karena sebentar lagi jalan lagi (S4).
        trolley = self.trolley
        if self.is_on(PATROL) or self._goto_active or trolley.busy or trolley.running is not False:
            return CommandResult.failed(
                REASON_INTERLOCK_EXCLUSIVE,
                "trolley sedang dipakai, tunggu sampai berhenti",
                state,
            )
        refusal = self.dosing.readiness()
        if refusal is not None:
            return CommandResult.failed(refusal.reason, refusal.text, state)
        self.dosing.start(target, phase)
        return CommandResult(True, "ON")

    # --- alert hasil (Kontrak §4.4) -----------------------------------------------------------

    def _alert(self, type: str, severity: str, title: str, message: str, **kwargs) -> None:
        virtual = self.config.virtual_actuators.get(GOTO)
        if self.sink is not None and virtual is not None:
            self.sink.publish_alert(
                virtual.device,
                type,
                severity,
                title,
                message,
                actuator_code=virtual.actuator_code,
                **kwargs,
            )

    def _alert_arrived(self, target: int) -> None:
        message = (
            "Trolley sudah kembali ke posisi awal."
            if target == 0
            else f"Trolley berhenti di stasiun {target}."
        )
        self._alert(
            "TROLLEY_ARRIVED", "INFO", "Trolley sudah sampai", message, metadata={"station": target}
        )

    def _alert_goto_failed(self, target: int, err: TrolleyError) -> None:
        if err.reason in _ALERTED_BY_TROLLEY or self.trolley.locked is not None:
            return
        where = "posisi awal" if target == 0 else f"stasiun {target}"
        self._alert(
            "TROLLEY_GOTO_FAILED",
            "WARNING",
            "Trolley tidak sampai tujuan",
            f"Trolley tidak sampai ke {where}: {err.text}.",
            metadata={"station": target, "reason": err.reason},
        )
