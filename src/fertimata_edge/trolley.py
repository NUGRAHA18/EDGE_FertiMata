"""Gerak trolley: homing, menuju stasiun, pengawasan gerak mentah (Arsitektur §9.2–§9.6, F7.2–F7.4).

- Jalan: R2 OFF → R3 = arah → jeda `dir_settle_ms` → R2 ON (S12, lewat `safety`). Berhenti: R2 OFF
  prioritas STOP. Semua interlock `safety` (daya, dosing, limit) tetap berlaku untuk gerak otomatis.
- `goto_station(n)` selalu mendekat dari selatan supaya titik berhenti berulang (§9.3); posisi
  dihitung dari tepi aktif reed.
- S15: tanpa pulsa reed `segment_timeout_s` atau total `travel_timeout_s` → berhenti + alert.
  Berlaku juga untuk TROLLEY-RUN mentah dari backend (F7.4); setelah gerak mentah posisi tidak
  diketahui sampai homing berikutnya.
- S13: END/HOME tak terduga → berhenti (penjaga limit di `safety`), lalu homing dan gagal (§9.6).
- S16: HOME dan END aktif bersamaan, atau limit yang baru ditinggalkan tetap aktif setelah pulsa
  reed berikutnya → kabel limit putus: trolley dikunci sampai agent di-restart.
- F7.3: `trolley.station`, `trolley.home_limit`, `trolley.end_limit` dikirim saat berubah dan
  tiap `polling.trolley_s`. Stasiun hanya dikirim saat trolley diam di stasiun/HOME yang diketahui.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .bus import Priority
from .config import Config
from .hal import Clock, GpioEvent, GpioLines
from .logs import fields
from .outputs import Outputs
from .poller import TelemetrySink
from .safety import Safety

log = logging.getLogger(__name__)

# Kode alasan (dipakai juga sebagai awalan pesan bila kelak dipicu perintah tingkat tinggi, F7.5).
REASON_LOCKED = "TROLLEY_LOCKED"
REASON_REFUSED = "TROLLEY_REFUSED"
REASON_STOPPED = "TROLLEY_STOPPED"
REASON_NO_REED = "TROLLEY_NO_REED"
REASON_TIMEOUT = "TROLLEY_TIMEOUT"
REASON_LIMIT = "TROLLEY_LIMIT_HIT"
REASON_INVALID = "INVALID"

LIMITS = ("home", "end")


class TrolleyError(Exception):
    def __init__(self, reason: str, text: str) -> None:
        super().__init__(f"{reason}: {text}")
        self.reason = reason
        self.text = text


class Trolley:
    def __init__(
        self,
        config: Config,
        outputs: Outputs,
        safety: Safety,
        gpio: GpioLines,
        clock: Clock,
        sink: TelemetrySink | None = None,
    ) -> None:
        travel = config.safety.travel
        if travel is None:
            raise ValueError("safety.travel wajib diisi untuk modul trolley")
        self.config = config
        self.cfg = config.trolley
        self.travel = travel
        self.outputs = outputs
        self.safety = safety
        self.gpio = gpio
        self.clock = clock
        self.sink = sink
        self.gateway = next(
            (n for n, d in config.logical_devices.items() if "soc" in d.sources), None
        )
        self.position: int | None = None  # 0 = HOME, n = magnet terakhir yang dilewati/dituju
        self.at_station = False  # diam tepat di magnet `position` (atau di HOME)
        self.locked: str | None = None
        self.busy = False  # gerak otomatis sedang berjalan
        self._holds = 0  # pemegang kunci perintah mentah (gerak otomatis, patroli)
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._last_report: dict[str, int] | None = None
        self._halt_task: asyncio.Future[None] | None = None
        outputs.listeners.append(self._on_relay_changed)

    # --- siklus hidup -------------------------------------------------------------------------

    def start(self) -> None:
        if not self.gpio.available:
            log.warning("GPIO asli belum tersedia (F7.1): modul trolley tidak aktif")
            return
        self._check_wires()
        self._tasks = [
            asyncio.create_task(self._watch(), name="trolley-watch"),
            asyncio.create_task(self._report_loop(), name="trolley-report"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return list(self._tasks)

    def _on_relay_changed(self, device: str) -> None:
        self._changed.set()

    @property
    def running(self) -> bool | None:
        return self.outputs.state(self.travel.run)

    # --- telemetri (F7.3) ---------------------------------------------------------------------

    def snapshot(self) -> dict[str, int]:
        data = {
            "trolley.home_limit": int(self.gpio.is_active("home")),
            "trolley.end_limit": int(self.gpio.is_active("end")),
        }
        if self.position is not None and self.at_station and self.running is False:
            data["trolley.station"] = self.position
        return data

    def report(self, force: bool = False) -> None:
        if self.sink is None or self.gateway is None or not self.gpio.available:
            return
        data = self.snapshot()
        if force or data != self._last_report:
            self._last_report = data
            self.sink.publish_telemetry(self.gateway, data)

    async def _report_loop(self) -> None:
        while True:
            self.report(force=True)
            await self.clock.sleep(self.config.polling.trolley_s)

    # --- pengawasan latar: kabel limit (S16) dan gerak mentah (F7.4) -------------------------

    async def _watch(self) -> None:
        queue = self.gpio.subscribe()
        raw_since: float | None = None  # monotonic pulsa reed terakhir selama gerak mentah
        try:
            while True:
                timeout = None
                if raw_since is not None:
                    timeout = self.cfg.segment_timeout_s - (self.clock.monotonic() - raw_since)
                event = await self._next_event(queue, timeout)
                if event is not None and event.line in LIMITS:
                    self._check_wires()
                manual = self.running is True and not self.busy
                if not manual:
                    raw_since = None
                elif raw_since is None:
                    # TROLLEY-RUN dari luar modul ini: posisi tidak lagi bisa dipercaya.
                    raw_since = self.clock.monotonic()
                    self.position, self.at_station = None, False
                    log.info("trolley digerakkan perintah mentah, posisi tidak diketahui")
                elif event is not None and event.line == "reed" and event.active:
                    raw_since = event.monotonic
                elif self.clock.monotonic() - raw_since >= self.cfg.segment_timeout_s:
                    await self._halt("no-reed")
                    self._alert_no_reed(automatic=False)
                    raw_since = None
                self.report()
        finally:
            self.gpio.unsubscribe(queue)

    def _check_wires(self) -> None:
        if self.gpio.is_active("home") and self.gpio.is_active("end"):
            self._lock_trolley("HOME dan END aktif bersamaan")

    def _lock_trolley(self, why: str) -> None:
        if self.locked is not None:
            return
        self.locked = why
        self.safety.travel_locked = (
            "sakelar ujung rel bermasalah, trolley dikunci sampai kabelnya diperiksa"
        )
        self.position, self.at_station = None, False
        log.error("trolley dikunci: kabel limit", extra=fields(why=why))
        if self.running is not False:
            self._halt_task = asyncio.ensure_future(self._halt("limit-wire"))
        if self.sink is not None and self.gateway is not None:
            self.sink.publish_alert(
                self.gateway,
                "TROLLEY_LIMIT_WIRE",
                "CRITICAL",
                "Kabel sakelar ujung rel bermasalah",
                "Trolley dikunci karena sakelar ujung rel terbaca tidak wajar (kemungkinan kabel "
                "putus). Periksa kabelnya, lalu nyalakan ulang alat kendali.",
                actuator_code=self.config.outputs[self.travel.run].actuator_code,
                metadata={
                    "reason": why,
                    "home": self.gpio.is_active("home"),
                    "end": self.gpio.is_active("end"),
                },
            )

    # --- gerak otomatis (F7.2) ----------------------------------------------------------------

    async def homing(self) -> None:
        async with self._lock:
            await self._homing()

    async def goto_station(self, station: int) -> None:
        if not 1 <= station <= self.cfg.stations:
            raise TrolleyError(REASON_INVALID, f"stasiun harus 1–{self.cfg.stations}")
        async with self._lock:
            if self.position == station and self.at_station:
                return
            if self.position is None or self.position >= station:
                await self._homing()  # §9.3: stasiun selalu didekati dari selatan
            await self._drive(north=True, target=station)

    async def _homing(self) -> None:
        self._ensure_usable()
        if self.gpio.is_active("home"):
            self.position, self.at_station = 0, True
            self.report()
            return
        await self._drive(north=False, target=0)

    def _ensure_usable(self) -> None:
        if not self.gpio.available:
            raise TrolleyError(REASON_LOCKED, "sensor posisi trolley belum terpasang")
        if self.locked is not None:
            raise TrolleyError(
                REASON_LOCKED, "trolley dikunci karena kabel sakelar ujung rel bermasalah"
            )

    async def _drive(self, *, north: bool, target: int) -> None:
        """Jalan ke arah `north` sampai stasiun `target` (utara) atau HOME (`target` 0)."""
        self._ensure_usable()
        cfg = self.cfg
        goal_limit = self.travel.limit_when_on if north else self.travel.limit_when_off
        queue = self.gpio.subscribe()
        self.busy = True
        self.hold_raw_commands()
        try:
            # Limit yang sedang aktif di belakang trolley harus padam setelah pulsa reed pertama.
            behind = [line for line in LIMITS if line != goal_limit and self.gpio.is_active(line)]
            await self._start(north)
            self.at_station = False
            start = last_reed = self.clock.monotonic()
            while True:
                now = self.clock.monotonic()
                total_left = cfg.travel_timeout_s - (now - start)
                segment_left = cfg.segment_timeout_s - (now - last_reed)
                if total_left <= 0:
                    await self._halt("travel-timeout")
                    self._alert_timeout(now - start)
                    raise TrolleyError(REASON_TIMEOUT, "perjalanan trolley terlalu lama")
                if segment_left <= 0:
                    await self._halt("no-reed")
                    self._alert_no_reed(automatic=True)
                    raise TrolleyError(REASON_NO_REED, "penanda stasiun tidak terbaca")
                if self.running is not True:
                    raise TrolleyError(REASON_STOPPED, "trolley dihentikan sebelum sampai")
                timeout = min(total_left, segment_left)
                event = await self._next_event(queue, timeout)
                if event is None or not event.active:
                    continue
                if event.line == "reed":
                    last_reed = event.monotonic
                    if self.position is not None:
                        self.position += 1 if north else -1
                    stuck = [line for line in behind if self.gpio.is_active(line)]
                    if stuck:
                        self._lock_trolley(f"{stuck[0].upper()} tetap aktif setelah ditinggalkan")
                        await self._halt("limit-wire")
                        raise TrolleyError(REASON_LOCKED, "kabel sakelar ujung rel bermasalah")
                    behind = []
                    if north and self.position == target:
                        await self._halt("arrived")
                        self.at_station = True
                        log.info("trolley sampai", extra=fields(station=target))
                        return
                elif event.line in LIMITS:
                    self._check_wires()
                    if self.locked is not None:
                        await self._halt("limit-wire")
                        raise TrolleyError(REASON_LOCKED, "kabel sakelar ujung rel bermasalah")
                    if event.line == goal_limit and target == 0:
                        await self._halt("home")
                        self.position, self.at_station = 0, True
                        log.info("homing selesai")
                        return
                    if event.line == goal_limit:
                        await self._unexpected_limit(event.line)
                    else:
                        # Limit di belakang aktif lagi padahal trolley menjauh: kabel bermasalah.
                        self._lock_trolley(f"{event.line.upper()} aktif saat trolley menjauh")
                        await self._halt("limit-wire")
                        raise TrolleyError(REASON_LOCKED, "kabel sakelar ujung rel bermasalah")
        finally:
            self.gpio.unsubscribe(queue)
            # Berhenti dulu sebelum melepas `busy`, supaya pengawas tidak mengira gerak mentah.
            if self.running is not False:
                await self._halt("abort")
            self.busy = False
            self.release_raw_commands()
            self.report()

    async def _unexpected_limit(self, line: str) -> None:
        # §9.6: END aktif saat menuju stasiun → berhenti, homing, tandai gagal.
        await self._halt(f"limit-{line}")
        self.position, self.at_station = None, False
        if self.sink is not None and self.gateway is not None:
            self.sink.publish_alert(
                self.gateway,
                "TROLLEY_LIMIT_HIT",
                "WARNING",
                "Trolley sampai di ujung rel tanpa diduga",
                "Trolley berhenti di ujung rel sebelum sampai stasiun tujuan dan kembali ke "
                "posisi awal. Periksa magnet penanda stasiun.",
                metadata={"limit": line},
            )
        with contextlib.suppress(TrolleyError):
            await self._drive(north=False, target=0)
        raise TrolleyError(REASON_LIMIT, "trolley sampai di ujung rel sebelum stasiun tujuan")

    def hold_raw_commands(self, text: str = "trolley sedang bergerak otomatis") -> None:
        """Tolak DIR/pan dan RUN ON dari backend (`BUSY`); RUN OFF tetap boleh (S3).

        Berhitung: patroli memegang kunci selama seluruh putaran, gerak otomatis di dalamnya
        memegang lagi, dan kunci baru dilepas setelah pemegang terakhir selesai.
        """
        self._holds += 1
        if self._holds > 1:
            return
        travel = self.travel
        names = [travel.direction, *self.config.safety.blocked_while_on]
        for name in dict.fromkeys(names):
            if name != travel.run:
                self.safety.managed[name] = text
        self.safety.managed_on[travel.run] = text

    def release_raw_commands(self) -> None:
        self._holds -= 1
        if self._holds > 0:
            return
        self._holds = 0
        travel = self.travel
        for name in dict.fromkeys([travel.direction, *self.config.safety.blocked_while_on]):
            if name != travel.run:
                self.safety.managed.pop(name, None)
        self.safety.managed_on.pop(travel.run, None)

    async def _start(self, north: bool) -> None:
        travel, safety = self.travel, self.safety
        if self.running is not False:
            await self._halt("restart")
        if self.outputs.state(travel.direction) is not north:
            refusal = safety.check(travel.direction, north, internal=True)
            if refusal is not None:
                raise TrolleyError(REASON_REFUSED, refusal.text)
            await self.clock.sleep(safety.settle_delay(travel.direction, north))
            result = await self.outputs.set(travel.direction, north, cause="trolley")
            if not result.ok:
                raise TrolleyError(REASON_REFUSED, result.text or "arah trolley tidak berubah")
        refusal = safety.check(travel.run, True, internal=True)
        if refusal is not None:
            raise TrolleyError(REASON_REFUSED, refusal.text)
        await self.clock.sleep(safety.settle_delay(travel.run, True))
        # Watchdog relay sebagai lapis kedua batas total perjalanan (S2, S15).
        result = await self.outputs.set(
            travel.run, True, duration_s=self.cfg.travel_timeout_s, cause="trolley"
        )
        if not result.ok:
            raise TrolleyError(REASON_REFUSED, result.text or "trolley tidak mau jalan")
        await safety.recheck_travel()

    async def halt(self, cause: str = "command") -> None:
        """RUN OFF prioritas STOP (S3). Gerak otomatis yang berjalan gagal `TROLLEY_STOPPED`."""
        await self._halt(cause)

    async def _halt(self, cause: str) -> None:
        if self.running is not False:
            await self.outputs.set(
                self.travel.run, False, priority=Priority.STOP, cause=f"trolley-{cause}"
            )

    async def _next_event(self, queue, timeout: float | None) -> GpioEvent | None:
        """Event GPIO berikutnya; None bila `timeout` habis atau state relay berubah."""
        if not queue.empty():
            return queue.get_nowait()
        self._changed.clear()
        waiters = [
            asyncio.ensure_future(queue.get()),
            asyncio.ensure_future(self._changed.wait()),
        ]
        if timeout is not None:
            waiters.append(asyncio.ensure_future(self.clock.sleep(max(0.0, timeout))))
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
        getter = waiters[0]
        if getter in done and not getter.cancelled():
            return getter.result()
        return None

    # --- alert --------------------------------------------------------------------------------

    def _alert_no_reed(self, *, automatic: bool) -> None:
        self.position, self.at_station = None, False
        log.error("tidak ada pulsa reed", extra=fields(automatic=automatic))
        if self.sink is None or self.gateway is None:
            return
        self.sink.publish_alert(
            self.gateway,
            "TROLLEY_NO_REED",
            "CRITICAL",
            "Trolley tidak menemukan penanda stasiun",
            f"Trolley dihentikan karena {self.cfg.segment_timeout_s:g} detik tidak melewati "
            "penanda stasiun. Periksa magnet penanda, sensornya, dan roda trolley.",
            actuator_code=self.config.outputs[self.travel.run].actuator_code,
            metadata={"automatic": automatic, "segmentTimeoutSeconds": self.cfg.segment_timeout_s},
        )

    def _alert_timeout(self, elapsed: float) -> None:
        self.position, self.at_station = None, False
        log.error("perjalanan trolley melewati batas", extra=fields(elapsed=elapsed))
        if self.sink is None or self.gateway is None:
            return
        self.sink.publish_alert(
            self.gateway,
            "TROLLEY_TIMEOUT",
            "WARNING",
            "Perjalanan trolley terlalu lama",
            f"Trolley dihentikan setelah {elapsed:.0f} detik tanpa sampai tujuan. Periksa rel, "
            "roda, dan sakelar ujung rel.",
            actuator_code=self.config.outputs[self.travel.run].actuator_code,
            metadata={"elapsedSeconds": round(elapsed, 1), "limit": self.cfg.travel_timeout_s},
        )
