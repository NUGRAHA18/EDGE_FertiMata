"""Patroli kamera malam hari (Arsitektur §9.4–§9.6, §13; keputusan D1, D3; F8.3–F8.5).

Urutan: homing → untuk tiap stasiun: `goto_station(n)` → tunggu `settle_s` → pan timur → tunggu
`pan_settle_s` → foto → pan barat → tunggu → foto (stasiun genap dibalik bila `alternate_pan`) →
pan timur → homing.

- Gerak dan interlock sepenuhnya lewat `trolley` dan `safety`; patroli tidak menulis RUN/DIR
  sendiri. Selama patroli, DIR/pan/RUN ON dari backend ditolak `BUSY`; RUN OFF tetap boleh (S3).
- §9.6: kamera tidak terbaca → lewati foto, lanjut (alert `CAMERA_FAIL` di akhir). Kesalahan
  trolley atau PLN padam → patroli batal di tempat, tanpa homing tambahan (modul trolley sudah
  homing sendiri untuk END tak terduga).
- §13 gerbang blur: ketajaman di bawah `camera.blur_threshold` → tunggu getaran reda dan ambil
  ulang sekali. Yang lebih tajam disimpan dan ditandai `blurry` bila tetap di bawah ambang; data
  riset tidak dibuang, penyaringan akhir urusan layanan AI.
- D3: jadwal lokal (`camera.patrol_schedule`, waktu `agent.utc_offset_h`) supaya patroli tetap
  jalan walau backend mati. Pemicu manual: aktuator virtual `TROLLEY-PATROL` (`virtual.py`, F8.4)
  memanggil `run(trigger="manual")`; STOP memanggil `request_stop()` lalu menghentikan trolley.
- Hasil: `PATROL_DONE` (INFO) untuk patroli selesai atau dihentikan dari aplikasi, `PATROL_FAILED`
  (WARNING) untuk batal/tidak jalan (Kontrak §4.4).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .captures import CaptureStore
from .config import Config
from .hal import Camera, CameraError, Clock, Frame, to_iso_z
from .logs import fields
from .outputs import Outputs
from .poller import TelemetrySink
from .safety import REASON_INTERLOCK_POWER, Safety
from .trolley import REASON_LOCKED, Trolley, TrolleyError

log = logging.getLogger(__name__)

# Kontrak §5: CAMERA-PAN ON = barat, OFF = timur. Arti relay ini bagian kontrak, bukan wiring.
PAN_ON = {"timur": False, "barat": True}

REASON_BUSY = "PATROL_BUSY"
REASON_DISK_FULL = "DISK_FULL"
REASON_PAN = "PAN_FAILED"
REASON_ABORTED = "PATROL_ABORTED"
REASON_STOPPED = "PATROL_STOPPED"

# Profil sensor yang bacaannya ikut dicatat di metadata foto (Protokol §4: suhu, kelembapan;
# cahaya untuk memastikan lampu greenhouse menyala).
ENVIRONMENT_PROFILES = frozenset({"xy_md02", "lux_rs485"})


class PatrolAbort(Exception):
    def __init__(self, reason: str, text: str) -> None:
        super().__init__(f"{reason}: {text}")
        self.reason = reason
        self.text = text


@dataclass
class PatrolResult:
    patrol_id: str
    trigger: str
    started_at: datetime
    status: str = "RUNNING"  # OK / PARTIAL / STOPPED / FAILED / SKIPPED
    reason: str | None = None
    text: str | None = None
    finished_at: datetime | None = None
    station: int | None = None  # stasiun terakhir yang dicapai
    saved: list[str] = field(default_factory=list)
    blurry: list[str] = field(default_factory=list)
    missed: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "patrolId": self.patrol_id,
            "trigger": self.trigger,
            "status": self.status,
            "reason": self.reason,
            "message": self.text,
            "startedAt": to_iso_z(self.started_at),
            "finishedAt": to_iso_z(self.finished_at) if self.finished_at else None,
            "lastStation": self.station,
            "saved": self.saved,
            "blurry": self.blurry,
            "missed": self.missed,
        }


class Patrol:
    def __init__(
        self,
        config: Config,
        trolley: Trolley,
        outputs: Outputs,
        safety: Safety,
        camera: Camera,
        clock: Clock,
        store: CaptureStore,
        *,
        power_source: Callable[[], str | None],
        sink: TelemetrySink | None = None,
    ) -> None:
        self.config = config
        self.cfg = config.camera
        self.trolley = trolley
        self.outputs = outputs
        self.safety = safety
        self.camera = camera
        self.clock = clock
        self.store = store
        self.power_source = power_source
        self.sink = sink
        self.gateway = next(
            (n for n, d in config.logical_devices.items() if "soc" in d.sources), None
        )
        self.pan = self.cfg.pan_output
        self.running = False
        self.last: PatrolResult | None = None
        self._env: dict[str, tuple[float, float]] = {}  # metrik → (nilai, monotonic)
        self._env_devices = {
            name for name, dev in config.devices.items() if dev.profile in ENVIRONMENT_PROFILES
        }
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = False
        self._stop_event = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        # Dipanggil saat `running` berubah: status TROLLEY-PATROL ikut patroli terjadwal (§5.1).
        self.listeners: list[Callable[[], None]] = []

    # --- siklus hidup -------------------------------------------------------------------------

    def start(self) -> None:
        if not self.trolley.gpio.available:
            log.warning("GPIO asli belum tersedia (F7.1): jadwal patroli tidak aktif")
            return
        self.check_disk()
        if self.cfg.patrol_schedule:
            self._task = asyncio.create_task(self._schedule_loop(), name="patrol-schedule")
            log.info(
                "jadwal patroli aktif",
                extra=fields(times=",".join(self.cfg.patrol_schedule)),
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return [self._task] if self._task is not None else []

    def on_read(self, device: str, values: dict[str, float]) -> None:
        """Pendengar poller: simpan suhu/kelembapan/cahaya terakhir untuk metadata foto."""
        if device in self._env_devices:
            now = self.clock.monotonic()
            for key, value in values.items():
                self._env[key] = (value, now)

    # --- jadwal lokal (D3) --------------------------------------------------------------------

    def _slots(self, local_now: datetime) -> list[datetime]:
        """Jam patroli kemarin, hari ini, dan besok dalam waktu lokal."""
        slots = []
        for days in (-1, 0, 1):
            day = local_now.date() + timedelta(days=days)
            for text in self.cfg.patrol_schedule:
                hour, minute = (int(part) for part in text.split(":"))
                slots.append(
                    datetime(day.year, day.month, day.day, hour, minute, tzinfo=local_now.tzinfo)
                )
        return sorted(slots)

    async def _schedule_loop(self) -> None:
        # Jendela minimal satu menit: loop bangun tepat di jam patroli, tapi jam bisa meleset.
        window = timedelta(minutes=max(self.cfg.patrol_catchup_min, 1.0))
        done: set[datetime] = set()
        while True:
            now = self.store.local(self.clock.now())
            for slot in self._slots(now):
                # Jam mundur (koreksi NTP) tidak membuat jadwal yang sama jalan dua kali.
                if slot <= now < slot + window and slot not in done:
                    done.add(slot)
                    await self._run_scheduled(slot)
                    now = self.store.local(self.clock.now())
            done = {slot for slot in done if slot > now - timedelta(days=2)}
            upcoming = [slot for slot in self._slots(now) if slot > now]
            wait = (upcoming[0] - now).total_seconds() if upcoming else 60.0
            # Tidur paling lama 60 s: jam dinding bisa melompat (NTP) selama tidur monotonic.
            await self.clock.sleep(min(60.0, max(1.0, wait)))

    async def _run_scheduled(self, slot: datetime) -> None:
        log.info("patroli terjadwal", extra=fields(slot=f"{slot:%H:%M}"))
        try:
            await self.run(trigger="schedule")
        except Exception:
            # Galat tak terduga tidak boleh mematikan agent (S1 akan memutus irigasi).
            log.exception("patroli gagal karena galat tak terduga")

    # --- satu patroli -------------------------------------------------------------------------

    async def run(self, trigger: str = "manual") -> PatrolResult:
        started = self.clock.now()
        result = PatrolResult(f"{self.store.local(started):%Y%m%d-%H%M}", trigger, started)
        if self.running:
            result.status, result.reason = "SKIPPED", REASON_BUSY
            result.text = "patroli lain sedang berjalan"
            return result
        self._set_running(True)
        self._stop_requested = False
        self._stop_event.clear()
        log.info("patroli mulai", extra=fields(patrol=result.patrol_id, trigger=trigger))
        moved = False
        try:
            self._precheck()
            moved = True
            self.trolley.hold_raw_commands("patroli kamera sedang berjalan")
            try:
                await self._route(result)
            finally:
                await self._pan_rest()
                self.trolley.release_raw_commands()
            result.status = "PARTIAL" if result.missed else "OK"
        except (TrolleyError, PatrolAbort) as err:
            result.status = "FAILED" if moved else "SKIPPED"
            result.reason, result.text = err.reason, err.text
            if self._stop_requested:
                # Trolley yang dihentikan dari aplikasi melempar TROLLEY_STOPPED: bukan kegagalan.
                result.status, result.reason = "STOPPED", REASON_STOPPED
                result.text = "patroli dihentikan dari aplikasi"
            elif self.power_source() != "MAINS":
                # §9.6: PLN padam di tengah patroli → berhenti di tempat, batalkan.
                result.reason, result.text = REASON_INTERLOCK_POWER, "listrik PLN padam"
        finally:
            if result.status == "RUNNING":  # dibatalkan (agent berhenti) atau galat tak terduga
                result.status, result.reason = "FAILED", REASON_ABORTED
                result.text = "patroli terputus karena alat kendali dimatikan atau galat"
            result.finished_at = self.clock.now()
            self.last = result
            try:
                await self._finish(result)
            finally:
                self._set_running(False)
        return result

    def request_stop(self) -> bool:
        """Minta patroli yang berjalan berhenti di langkah berikutnya. Trolley yang sedang jalan
        dihentikan pemanggil (`trolley.halt`). False bila tidak ada patroli."""
        if not self.running:
            return False
        self._stop_requested = True
        self._stop_event.set()
        return True

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def readiness(self) -> PatrolAbort | None:
        """Alasan patroli tidak bisa dimulai sekarang, untuk ACK perintah START (F8.4)."""
        try:
            self._precheck()
        except PatrolAbort as err:
            return err
        return None

    def _set_running(self, running: bool) -> None:
        self.running = running
        if running:
            self._idle.clear()
        else:
            self._idle.set()
        for listener in self.listeners:
            listener()

    async def _pause(self, seconds: float) -> None:
        """Jeda yang langsung berakhir saat STOP diminta: backend menunggu ACK STOP (TTL 30 s)."""
        if seconds > 0 and not self._stop_requested:
            waiters = [
                asyncio.ensure_future(self.clock.sleep(seconds)),
                asyncio.ensure_future(self._stop_event.wait()),
            ]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
        self._check_stop()

    def _check_stop(self) -> None:
        if self._stop_requested:
            raise PatrolAbort(REASON_STOPPED, "patroli dihentikan dari aplikasi")

    def _precheck(self) -> None:
        if not self.trolley.gpio.available or self.trolley.locked is not None:
            raise PatrolAbort(REASON_LOCKED, "trolley tidak bisa dipakai")
        if self.power_source() != "MAINS":
            raise PatrolAbort(REASON_INTERLOCK_POWER, "listrik PLN tidak tersedia")
        if self.check_disk() == "full":
            raise PatrolAbort(REASON_DISK_FULL, "penyimpanan foto di alat kendali penuh")

    async def _route(self, result: PatrolResult) -> None:
        cfg = self.config.trolley
        await self.trolley.homing()
        for station in range(1, cfg.stations + 1):
            self._check_stop()
            if self.power_source() != "MAINS":
                raise PatrolAbort(REASON_INTERLOCK_POWER, "listrik PLN padam")
            await self.trolley.goto_station(station)
            result.station = station
            await self._pause(cfg.settle_s)  # §9.3: getaran reda sebelum memotret
            order = ["timur", "barat"]
            if self.cfg.alternate_pan and station % 2 == 0:
                order.reverse()
            for direction in order:
                self._check_stop()
                await self._shoot(result, station, direction)
        self._check_stop()
        await self._set_pan("timur")
        await self.trolley.homing()
        result.station = 0

    async def _set_pan(self, direction: str) -> None:
        want = PAN_ON[direction]
        if self.outputs.state(self.pan) is want:
            return
        refusal = self.safety.check(self.pan, want, internal=True)
        if refusal is not None:
            raise PatrolAbort(refusal.reason, refusal.text)
        await self.clock.sleep(self.safety.settle_delay(self.pan, want))
        outcome = await self.outputs.set(self.pan, want, cause="patrol")
        if not outcome.ok:
            # Foto dengan arah salah merusak label data riset, jadi patroli dihentikan.
            raise PatrolAbort(outcome.reason or REASON_PAN, outcome.text or "kamera tidak berputar")
        await self._pause(self.config.trolley.pan_settle_s)

    async def _pan_rest(self) -> None:
        # Kembalikan pan ke timur (posisi istirahat §9.4) bila trolley diam; gagal tidak masalah.
        if self.outputs.state(self.pan) is False:
            return
        if self.safety.check(self.pan, False, internal=True) is not None:
            return
        with contextlib.suppress(Exception):
            await self.clock.sleep(self.safety.settle_delay(self.pan, False))
            await self.outputs.set(self.pan, False, cause="patrol-end")

    async def _shoot(self, result: PatrolResult, station: int, direction: str) -> None:
        await self._set_pan(direction)
        shot = f"S{station:02d}_{direction}"
        try:
            frame = await self.camera.capture()
        except CameraError as err:
            log.warning("foto gagal", extra=fields(shot=shot, err=err))
            result.missed.append({"station": station, "direction": direction, "error": str(err)})
            return
        captured_at = self.clock.now()
        attempts = 1
        threshold = self.cfg.blur_threshold
        if self._is_blurry(frame):
            await self._pause(self.config.trolley.settle_s)
            attempts = 2
            try:
                retry = await self.camera.capture()
            except CameraError as err:
                log.warning("foto ulang gagal", extra=fields(shot=shot, err=err))
            else:
                if (retry.sharpness or 0.0) >= (frame.sharpness or 0.0):
                    frame, captured_at = retry, self.clock.now()
        blurry = self._is_blurry(frame)
        if self.check_disk() == "full":
            raise PatrolAbort(REASON_DISK_FULL, "penyimpanan foto di alat kendali penuh")
        saved = await self.store.save(
            frame,
            patrol_start=result.started_at,
            captured_at=captured_at,
            station=station,
            direction=direction,
            metadata=self._metadata(result, frame, captured_at, station, direction, attempts)
            | {"blurThreshold": threshold, "blurry": blurry},
        )
        result.saved.append(saved.capture_id)
        if blurry:
            result.blurry.append(saved.capture_id)
        log.info(
            "foto disimpan",
            extra=fields(capture=saved.capture_id, sharpness=frame.sharpness, blurry=blurry),
        )

    def _is_blurry(self, frame: Frame) -> bool:
        threshold = self.cfg.blur_threshold
        return threshold is not None and frame.sharpness is not None and frame.sharpness < threshold

    def _metadata(
        self,
        result: PatrolResult,
        frame: Frame,
        captured_at: datetime,
        station: int,
        direction: str,
        attempts: int,
    ) -> dict[str, Any]:
        cfg = self.cfg
        return {
            # Bentuk sama dengan payload AI (Kontrak §4.5) supaya layanan AI tinggal meneruskan.
            "stationCode": f"ST-{station:02d}",
            "station": station,
            "panDirection": direction.upper(),
            "capturedAt": to_iso_z(captured_at),
            "localTime": self.store.local(captured_at).replace(microsecond=0).isoformat(),
            "patrolId": result.patrol_id,
            "trigger": result.trigger,
            "camera": {
                "device": cfg.device,
                "encoding": frame.encoding,
                "width": frame.width,
                "height": frame.height,
                "exposure": cfg.exposure,
                "whiteBalance": cfg.white_balance,
            },
            "sharpness": frame.sharpness,
            "attempts": attempts,
            "environment": self._environment(),
        }

    def _environment(self) -> dict[str, float]:
        # Bacaan basi dihilangkan, sama seperti telemetri (tidak pernah angka palsu).
        max_age = 2 * self.config.polling.air_light_s
        now = self.clock.monotonic()
        return {key: value for key, (value, at) in self._env.items() if now - at <= max_age}

    # --- akhir patroli: ringkasan, alert ------------------------------------------------------

    async def _finish(self, result: PatrolResult) -> None:
        log.info(
            "patroli selesai",
            extra=fields(
                patrol=result.patrol_id,
                status=result.status,
                reason=result.reason,
                saved=len(result.saved),
                missed=len(result.missed),
            ),
        )
        try:
            await self.store.write_summary(result.started_at, result.patrol_id, result.summary())
        except OSError as err:
            log.error("ringkasan patroli tidak tersimpan", extra=fields(err=err))
        if result.missed:
            self._alert_camera(result)
        if result.status in ("FAILED", "SKIPPED"):
            self._alert_failed(result)
        else:
            self._alert_done(result)
        self.check_disk()

    def _alert(self, type: str, severity: str, title: str, message: str, **kwargs: Any) -> None:
        if self.sink is not None and self.gateway is not None:
            self.sink.publish_alert(self.gateway, type, severity, title, message, **kwargs)

    def _alert_camera(self, result: PatrolResult) -> None:
        total = len(result.saved) + len(result.missed)
        self._alert(
            "CAMERA_FAIL",
            "WARNING",
            "Kamera gagal mengambil foto",
            f"{len(result.missed)} dari {total} foto patroli tidak terambil. Periksa kabel kamera "
            "di trolley.",
            metadata={"patrolId": result.patrol_id, "missed": result.missed},
        )

    def _alert_done(self, result: PatrolResult) -> None:
        total = len(result.saved) + len(result.missed)
        if result.status == "STOPPED":
            title = "Patroli kamera dihentikan"
            message = (
                f"Patroli dihentikan dari aplikasi setelah {len(result.saved)} foto. Trolley "
                "berhenti di tempat."
            )
        else:
            title = "Patroli kamera selesai"
            message = f"{len(result.saved)} dari {total} foto tanaman tersimpan."
        self._alert(
            "PATROL_DONE",
            "INFO",
            title,
            message,
            actuator_code=self._patrol_code(),
            metadata={
                "patrolId": result.patrol_id,
                "status": result.status,
                "trigger": result.trigger,
                "lastStation": result.station,
                "saved": len(result.saved),
                "missed": len(result.missed),
                "blurry": len(result.blurry),
            },
        )

    def _patrol_code(self) -> str | None:
        virtual = self.config.virtual_actuators.get("trolley_patrol")
        return virtual.actuator_code if virtual is not None else None

    def _alert_failed(self, result: PatrolResult) -> None:
        verb = "tidak dijalankan" if result.status == "SKIPPED" else "berhenti di tengah jalan"
        self._alert(
            "PATROL_FAILED",
            "WARNING",
            "Patroli kamera tidak selesai",
            f"Patroli foto tanaman {verb}: {result.text}.",
            actuator_code=self.config.outputs[self.pan].actuator_code,
            metadata={
                "patrolId": result.patrol_id,
                "reason": result.reason,
                "trigger": result.trigger,
                "lastStation": result.station,
                "saved": len(result.saved),
            },
        )

    def check_disk(self) -> str | None:
        """Periksa ruang disk folder foto, alert bila menipis (F8.5). Tidak menghapus apa pun."""
        free = self.store.free_gb()
        level = self.store.disk_level(free)
        if level is None:
            return None
        meta = {
            "freeGb": round(free or 0.0, 2),
            "warnGb": self.cfg.disk_warn_free_gb,
            "stopGb": self.cfg.disk_stop_free_gb,
            "path": self.cfg.root,
        }
        if level == "full":
            log.error("ruang disk foto habis", extra=fields(free_gb=meta["freeGb"]))
            self._alert(
                "DISK_FULL",
                "CRITICAL",
                "Penyimpanan foto penuh",
                "Foto tanaman tidak disimpan lagi karena penyimpanan alat kendali hampir penuh. "
                "Pindahkan foto lama ke tempat lain.",
                metadata=meta,
            )
        else:
            log.warning("ruang disk foto menipis", extra=fields(free_gb=meta["freeGb"]))
            self._alert(
                "DISK_LOW",
                "WARNING",
                "Penyimpanan foto hampir penuh",
                f"Sisa ruang {meta['freeGb']:g} GB. Pindahkan foto lama ke tempat lain sebelum "
                "penuh.",
                metadata=meta,
            )
        return level
