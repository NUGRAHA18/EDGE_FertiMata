"""Penjaga lokal sebelum relay ditulis (Aturan keselamatan S4–S8, S12–S14, S16).

Edge adalah lapis terakhir sebelum relay: aturan di sini berlaku walau backend mati. Output yang
state-nya tidak diketahui (relay tidak menjawab) dianggap **mungkin ON**, sehingga ikut memblokir.

S7 memakai sumber daya dari modul `power`: selain `MAINS` (termasuk belum diketahui) output
`blocked_on_backup_power` ditolak. S9 (EC/pH tidak valid) menyusul bersama F6.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .bus import Priority
from .config import Config
from .hal import Clock, GpioLines
from .logs import fields
from .outputs import Outputs

log = logging.getLogger(__name__)

# Kode alasan penolakan (awal errorMessage ACK, Kontrak §4.3).
REASON_INTERLOCK_EXCLUSIVE = "INTERLOCK_EXCLUSIVE"
REASON_INTERLOCK_FLOAT = "INTERLOCK_FLOAT"
REASON_INTERLOCK_MOVING = "INTERLOCK_MOVING"
REASON_INTERLOCK_LIMIT = "INTERLOCK_LIMIT"
REASON_INTERLOCK_POWER = "INTERLOCK_POWER"
REASON_BUSY = "BUSY"
REASON_OUTPUT_FAULT = "OUTPUT_FAULT"


@dataclass(frozen=True)
class Refusal:
    reason: str
    text: str  # untuk petani


class Safety:
    def __init__(
        self,
        config: Config,
        outputs: Outputs,
        gpio: GpioLines,
        clock: Clock,
        *,
        power_source: Callable[[], str | None],
    ) -> None:
        self.config = config
        self.safety = config.safety
        self.outputs = outputs
        self.gpio = gpio
        self.clock = clock
        self.power_source = power_source
        self._power_blocked = self.safety.expand(self.safety.blocked_on_backup_power)
        self._float_blocked = self.safety.expand(self.safety.blocked_on_float_low)
        self._blocked_by = {
            key: self.safety.expand(names) for key, names in self.safety.blocked_while_on.items()
        }
        # Output yang sedang dikelola edge sendiri (mis. EC-POWER selama siklus analog, Kontrak §5):
        # nama output → teks alasan. Perintah manual ON maupun OFF ditolak.
        self.managed: dict[str, str] = {}
        # Sama, tetapi hanya ON yang ditolak: RUN selama gerakan otomatis trolley tetap bisa
        # dihentikan (S3).
        self.managed_on: dict[str, str] = {}
        # Diisi modul trolley saat kabel limit terdeteksi putus (S16): jalan ditolak sampai restart.
        self.travel_locked: str | None = None
        self._task: asyncio.Task[None] | None = None

    # --- pemeriksaan sebelum tulis ------------------------------------------------------------

    def check(self, name: str, on: bool, *, internal: bool = False) -> Refusal | None:
        """None bila `name` boleh dibuat ON/OFF sekarang.

        OFF hanya bisa ditolak oleh `blocked_while_on`: mengubah arah trolley atau pan saat
        trolley jalan bukan tindakan berhenti (S12, S14). `internal=True` untuk modul edge yang
        sedang memegang output itu sendiri (kunci `managed` tidak berlaku baginya).
        """
        if not internal:
            if name in self.managed:
                return Refusal(REASON_BUSY, self.managed[name])
            if on and name in self.managed_on:
                return Refusal(REASON_BUSY, self.managed_on[name])
        refusal = self._check_blocked_while_on(name)
        if refusal is not None or not on:
            return refusal
        if self.outputs.states[name].faulty:
            return Refusal(
                REASON_OUTPUT_FAULT,
                f"saklar {self.outputs.label(name)} dikunci karena pernah tidak menurut, "
                "periksa relay di panel",
            )
        for check in (
            self._check_power,
            self._check_exclusive,
            self._check_float,
            self._check_travel,
        ):
            refusal = check(name)
            if refusal is not None:
                return refusal
        return None

    def check_activation(self, name: str) -> Refusal | None:
        """Pemeriksaan awal perintah tingkat tinggi (F7.5, F8.4) yang nanti menyalakan `name`.

        Hanya aturan yang tidak bergantung arah/posisi (rusak, daya S7, eksklusif S4–S6): cek
        limit dan arah dilakukan modul trolley saat benar-benar mulai jalan.
        """
        if self.outputs.states[name].faulty:
            return Refusal(
                REASON_OUTPUT_FAULT,
                f"saklar {self.outputs.label(name)} dikunci karena pernah tidak menurut, "
                "periksa relay di panel",
            )
        return self._check_power(name) or self._check_exclusive(name)

    def _maybe_on(self, name: str) -> bool:
        return self.outputs.states[name].on is not False

    def _check_blocked_while_on(self, name: str) -> Refusal | None:
        for blocker in sorted(self._blocked_by.get(name, ())):
            if self._maybe_on(blocker):
                return Refusal(
                    REASON_INTERLOCK_MOVING,
                    f"{self.outputs.label(name)} tidak bisa diubah selama "
                    f"{self.outputs.label(blocker)} berjalan",
                )
        return None

    def _check_exclusive(self, name: str) -> Refusal | None:
        # S4–S6: tiap baris exclusive_groups berisi beberapa sisi; output dari sisi lain tidak
        # boleh ON bersamaan. Anggota satu sisi boleh (dosing + aduk).
        for group in self.safety.exclusive_groups:
            sides = [self.safety.expand([side]) for side in group]
            if not any(name in side for side in sides):
                continue
            for side in sides:
                if name in side:
                    continue
                for other in sorted(side):
                    if self._maybe_on(other):
                        return Refusal(
                            REASON_INTERLOCK_EXCLUSIVE,
                            f"{self.outputs.label(name)} tidak boleh menyala bersamaan dengan "
                            f"{self.outputs.label(other)}",
                        )
        return None

    def _check_power(self, name: str) -> Refusal | None:
        if name not in self._power_blocked:
            return None
        source = self.power_source()
        if source == "MAINS":
            return None
        text = {
            "BATTERY": "listrik PLN padam, alat berjalan dengan aki",
            "UNKNOWN": "keadaan listrik tidak terbaca, periksa kabel data di panel",
        }.get(source or "", "keadaan listrik belum diketahui, coba lagi sebentar")
        return Refusal(REASON_INTERLOCK_POWER, text)

    def _check_float(self, name: str) -> Refusal | None:
        if name in self._float_blocked and not self.outputs.float_ok():
            return Refusal(
                REASON_INTERLOCK_FLOAT,
                "air di drum hampir habis atau pelampung tidak terbaca",
            )
        return None

    def _check_travel(self, name: str) -> Refusal | None:
        travel = self.safety.travel
        if travel is None or name != travel.run:
            return None
        if self.travel_locked is not None:
            return Refusal(REASON_INTERLOCK_LIMIT, self.travel_locked)
        direction = self.outputs.state(travel.direction)
        if direction is None:
            return Refusal(REASON_INTERLOCK_LIMIT, "arah trolley belum diketahui")
        # S16: kontak NC, jadi kabel putus terbaca seperti limit tertekan (§6.1).
        if self.gpio.is_active("home") and self.gpio.is_active("end"):
            return Refusal(
                REASON_INTERLOCK_LIMIT,
                "sakelar ujung rel bermasalah, trolley dikunci sampai kabelnya diperiksa",
            )
        target = travel.limit_when_on if direction else travel.limit_when_off
        if self.gpio.is_active(target):
            return Refusal(REASON_INTERLOCK_LIMIT, "trolley sudah di ujung rel ke arah itu")
        return None

    def settle_delay(self, name: str, on: bool) -> float:
        """Sisa jeda (detik) sebelum `name` boleh ditulis, urutan S12 / Arsitektur §5.3.1.

        Output yang diblokir (DIR, pan) menunggu `stop_settle_ms` sejak RUN OFF; RUN menunggu
        `dir_settle_ms` sejak DIR/pan terakhir berubah.
        """
        now = self.clock.monotonic()
        wait = 0.0
        for blocker in self._blocked_by.get(name, ()):
            since = now - self.outputs.states[blocker].changed_at
            wait = max(wait, self.safety.stop_settle_ms / 1000.0 - since)
        if on:
            for key, blockers in self._blocked_by.items():
                if name in blockers:
                    since = now - self.outputs.states[key].changed_at
                    wait = max(wait, self.safety.dir_settle_ms / 1000.0 - since)
        return max(0.0, wait)

    # --- penjaga limit selama trolley jalan (S13) ---------------------------------------------

    def start(self) -> None:
        if self.safety.travel is not None:
            self._task = asyncio.create_task(self._guard_limits(), name="limit-guard")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return [self._task] if self._task is not None else []

    async def _guard_limits(self) -> None:
        travel = self.safety.travel
        assert travel is not None
        queue = self.gpio.subscribe()
        try:
            while True:
                event = await queue.get()
                if event.line not in ("home", "end") or not event.active:
                    continue
                if self.outputs.state(travel.run) is False:
                    continue
                log.warning(
                    "limit aktif saat trolley jalan, berhenti", extra=fields(line=event.line)
                )
                await self.outputs.set(
                    travel.run, False, priority=Priority.STOP, cause=f"limit-{event.line}"
                )
        finally:
            self.gpio.unsubscribe(queue)

    async def recheck_travel(self) -> None:
        """Dipanggil setelah RUN ON: limit yang aktif di antara cek dan tulis tetap menghentikan."""
        travel = self.safety.travel
        if travel is None or self.outputs.state(travel.run) is not True:
            return
        if self._check_travel(travel.run) is not None:
            await self.outputs.set(travel.run, False, priority=Priority.STOP, cause="limit")
