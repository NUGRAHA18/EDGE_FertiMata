"""Loop dosing tertutup berbasis EC (Arsitektur §8.2, F9), dipicu `RIO-TANK-01/DOSING-LOOP`.

Satu iterasi: aduk `stir_s` → aduk OFF → baca larutan (§6.3) berulang sampai EC stabil → ΔEC =
target − EC25. ΔEC ≤ toleransi → selesai (`DOSING_DONE`). Selain itu `fraction_per_iteration` × ΔEC
dibagi menurut rasio N:P:K fase, dipompa N → P → K satu per satu, lalu iterasi berikutnya. Iterasi
ke-`max_iterations` tidak lagi memberi dosis: belum sampai → `DOSING_NOT_CONVERGING`.

Keselamatan (docs/04-aturan-keselamatan.md):
- Hanya menambah pupuk, dan per iterasi kurang dari ΔEC: larutan pekat tidak bisa ditarik kembali.
  EC sudah di atas target → selesai tanpa dosis.
- S7 daya, S8 float, S4–S6 eksklusif, S19 rusak diperiksa lewat `safety` sebelum mulai, sebelum tiap
  pompa, dan tiap `guard_s` selama menunggu. Gagal → semua output dosing OFF + `DOSING_FAILED`.
- S9: bacaan yang tidak `valid_for_dosing` menghentikan loop.
- Selama loop sisi eksklusif lain (trolley, pompa siram) ditahan `safety.held`, juga di antara dua
  dosis; ON mentah ke pompa pupuk/aduk dijawab BUSY. OFF mentah tetap boleh (S3).
- Pompa memakai watchdog relay `min(t, max_on_s)` (S2); dosis yang lebih panjang dipotong
  (dosis kurang).
- STOP membatalkan tugas; semua output dosing dimatikan dengan prioritas STOP.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .analog import AnalogReader, AnalogReading
from .bus import Priority
from .config import Config
from .hal import Clock
from .logs import fields
from .outputs import Outputs
from .poller import TelemetrySink
from .safety import REASON_BUSY, REASON_INTERLOCK_EXCLUSIVE, Refusal, Safety

log = logging.getLogger(__name__)

NUTRIENTS = ("n", "p", "k")

# Kode alasan (awal errorMessage ACK / metadata.reason alert, Kontrak §4.3).
REASON_NOT_CONFIGURED = "NOT_CONFIGURED"
REASON_EC_INVALID = "EC_PH_INVALID"
REASON_EC_UNSTABLE = "EC_UNSTABLE"
REASON_INTERRUPTED = "DOSING_INTERRUPTED"
REASON_ERROR = "DOSING_ERROR"

DONE = "DONE"
NOT_CONVERGING = "NOT_CONVERGING"
FAILED = "FAILED"
STOPPED = "STOPPED"
RUNNING = "RUNNING"


class DosingAbort(Exception):
    def __init__(self, reason: str, text: str) -> None:
        super().__init__(f"{reason}: {text}")
        self.reason = reason
        self.text = text


@dataclass
class DosingRun:
    target_ec: float
    phase: str
    status: str = RUNNING
    iteration: int = 0
    ec_measured: float | None = None
    last_reading: AnalogReading | None = None
    total_ml: dict[str, float] = field(default_factory=lambda: dict.fromkeys(NUTRIENTS, 0.0))
    reason: str | None = None
    text: str | None = None


class DosingLoop:
    def __init__(
        self,
        config: Config,
        outputs: Outputs,
        safety: Safety,
        analog: AnalogReader,
        clock: Clock,
        sink: TelemetrySink | None = None,
    ) -> None:
        self.config = config
        self.cfg = config.dosing
        self.outputs = outputs
        self.safety = safety
        self.analog = analog
        self.clock = clock
        self.sink = sink
        self.pumps = {key: getattr(self.cfg.pumps, key) for key in NUTRIENTS}
        self.stirrer = self.cfg.stirrer_output
        # Urutan mati: pompa pupuk dulu, aduk terakhir.
        self.own = [*self.pumps.values(), self.stirrer]
        self.device = outputs.logical_of[config.outputs[self.stirrer].device]
        virtual = config.virtual_actuators.get("dosing_loop")
        self.actuator_code = virtual.actuator_code if virtual is not None else None
        self.partners = self._exclusive_partners()
        self.last: DosingRun | None = None
        self.listeners: list[Callable[[], None]] = []
        self._active = False
        self._task: asyncio.Task[None] | None = None

    def _exclusive_partners(self) -> frozenset[str]:
        """Output sisi lain `exclusive_groups` yang sebaris dengan output loop (S4, S5)."""
        safety, own = self.config.safety, set(self.own)
        partners: set[str] = set()
        for group in safety.exclusive_groups:
            sides = [safety.expand([side]) for side in group]
            for i, side in enumerate(sides):
                if side & own:
                    for j, other in enumerate(sides):
                        if j != i:
                            partners |= other
        return frozenset(partners - own)

    # --- state --------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._active

    def readiness(self) -> Refusal | None:
        """Alasan loop tidak bisa dimulai sekarang, untuk ACK `DOSE_TO_TARGET`. None = boleh."""
        if self._active:
            return Refusal(REASON_BUSY, "pemberian pupuk otomatis sedang berjalan")
        missing = self.cfg.missing + self.analog.missing_for_dosing()
        if missing:
            log.warning("loop dosing belum bisa dipakai", extra=fields(missing=",".join(missing)))
            return Refusal(
                REASON_NOT_CONFIGURED,
                "takaran pompa pupuk atau sensor larutan belum diatur, hubungi teknisi",
            )
        for name in self.own:
            refusal = self.safety.check(name, True, internal=True)
            if refusal is not None:
                return refusal
        return None

    # --- siklus hidup -------------------------------------------------------------------------

    def start(self, target_ec: float, phase: str) -> None:
        """Mulai loop di latar. Pemanggil sudah memeriksa `readiness()`."""
        run = DosingRun(target_ec=float(target_ec), phase=phase)
        self._active = True
        hold = Refusal(REASON_INTERLOCK_EXCLUSIVE, "pemberian pupuk otomatis sedang berjalan")
        for name in self.partners:
            self.safety.held[name] = hold
        for name in self.own:
            self.safety.managed_on[name] = "pemberian pupuk otomatis sedang berjalan"
        self._task = asyncio.create_task(self._run(run), name="dosing-loop")
        self._notify()

    async def stop(self) -> None:
        """STOP (tidak pernah ditolak): hentikan loop, lalu semua output dosing OFF."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._release()  # tugas yang dibatalkan sebelum sempat jalan tidak melewati `finally`
        await self._all_off("stop-dosing")

    def _release(self) -> None:
        if not self._active:
            return
        self._active = False
        for name in self.partners:
            self.safety.held.pop(name, None)
        for name in self.own:
            self.safety.managed_on.pop(name, None)
        self._notify()

    def _notify(self) -> None:
        for listener in self.listeners:
            listener()

    async def _all_off(self, cause: str) -> None:
        for name in self.own:
            if self.outputs.state(name) is not False:
                await self.outputs.set(name, False, priority=Priority.STOP, cause=cause)

    # --- loop §8.2 ----------------------------------------------------------------------------

    async def _run(self, run: DosingRun) -> None:
        log.info("loop dosing mulai", extra=fields(target=run.target_ec, phase=run.phase))
        try:
            await self._iterate(run)
        except DosingAbort as err:
            run.status, run.reason, run.text = FAILED, err.reason, err.text
        except Exception:
            # Galat tak terduga tidak boleh mematikan agent (S1 akan memutus irigasi).
            log.exception("loop dosing gagal karena galat tak terduga")
            run.status, run.reason = FAILED, REASON_ERROR
            run.text = "terjadi gangguan di alat kendali"
        finally:
            if run.status == RUNNING:  # dibatalkan: STOP dari aplikasi atau agent berhenti
                run.status = STOPPED
            try:
                await self._all_off("dosing-end")
            finally:
                self.last = run
                log.info(
                    "loop dosing selesai",
                    extra=fields(
                        status=run.status,
                        reason=run.reason,
                        iterations=run.iteration,
                        ec=run.ec_measured,
                    ),
                )
                self._alert_result(run)
                self._release()

    async def _iterate(self, run: DosingRun) -> None:
        cfg = self.cfg
        ratio = cfg.ratio[run.phase]
        for iteration in range(1, cfg.max_iterations + 1):
            run.iteration = iteration
            await self._stir()
            reading = await self._stable_reading()
            assert reading.ec_ms_cm is not None
            run.last_reading = reading
            run.ec_measured = reading.ec_ms_cm
            delta = run.target_ec - reading.ec_ms_cm
            dosed = dict.fromkeys(NUTRIENTS, 0.0)
            log.info(
                "iterasi dosing",
                extra=fields(iteration=iteration, ec=reading.ec_ms_cm, delta=round(delta, 3)),
            )
            if delta <= cfg.ec_tolerance_ms_cm:  # termasuk EC di atas target: tidak ditambah
                self._publish(run, dosed)
                run.status = DONE
                return
            if iteration == cfg.max_iterations:
                self._publish(run, dosed)
                run.status = NOT_CONVERGING
                return
            try:
                await self._dose(run, delta, ratio, dosed)
            finally:
                self._publish(run, dosed)

    def _guard(self) -> None:
        """Prasyarat §8.2 langkah 1 (S7, S8, S4–S5) lewat aturan pompa aduk di `safety`."""
        refusal = self.safety.check(self.stirrer, True, internal=True)
        if refusal is not None:
            raise DosingAbort(refusal.reason, refusal.text)

    async def _wait(self, seconds: float, expect_on: str | None = None) -> None:
        """Tunggu sambil memeriksa prasyarat tiap `guard_s`; output yang mestinya ON harus ON."""
        end = self.clock.monotonic() + seconds
        while True:
            remaining = end - self.clock.monotonic()
            if remaining <= 1e-9:
                return
            self._guard()
            if expect_on is not None and self.outputs.state(expect_on) is not True:
                raise DosingAbort(
                    REASON_INTERRUPTED, f"{self.outputs.label(expect_on)} mati di tengah jalan"
                )
            await self.clock.sleep(min(self.cfg.guard_s, remaining))

    async def _switch_on(self, name: str, seconds: float) -> None:
        self._guard()
        refusal = self.safety.check(name, True, internal=True)
        if refusal is not None:
            raise DosingAbort(refusal.reason, refusal.text)
        result = await self.outputs.set(name, True, duration_s=seconds, cause="dosing")
        if not result.ok:
            assert result.reason is not None and result.text is not None
            raise DosingAbort(result.reason, result.text)

    async def _switch_off(self, name: str) -> None:
        if self.outputs.state(name) is not False:
            await self.outputs.set(name, False, priority=Priority.STOP, cause="dosing")

    async def _stir(self) -> None:
        # §8.2 langkah 2/8: aduk dulu, lalu baca setelah aduk berhenti.
        await self._switch_on(self.stirrer, self.cfg.stir_s)
        await self._wait(self.cfg.stir_s, expect_on=self.stirrer)
        await self._switch_off(self.stirrer)

    async def _stable_reading(self) -> AnalogReading:
        """§8.2 langkah 8: baca ulang sampai dua bacaan berurutan berbeda ≤ `stable_delta`."""
        cfg = self.cfg
        previous: AnalogReading | None = None
        for _ in range(cfg.stable_max_reads):
            if previous is not None:
                await self._wait(cfg.stable_interval_s)
            self._guard()
            reading = await self.analog.measure()
            if not reading.valid_for_dosing or reading.ec_ms_cm is None:
                # S9: tanpa pH, EC, dan suhu yang masuk akal dosing dilarang.
                raise DosingAbort(
                    REASON_EC_INVALID,
                    "angka sensor larutan tidak wajar atau tidak terbaca, periksa probe",
                )
            if (
                previous is not None
                and previous.ec_ms_cm is not None
                and abs(reading.ec_ms_cm - previous.ec_ms_cm) <= cfg.stable_delta_ms_cm
            ):
                return reading
            previous = reading
        raise DosingAbort(
            REASON_EC_UNSTABLE,
            "angka sensor larutan tidak kunjung tenang, periksa probe dan pompa aduk",
        )

    def plan(self, delta_ec: float, ratio: tuple[float, float, float]) -> dict[str, float]:
        """Lama nyala tiap pompa (detik) untuk menaikkan EC sebesar `fraction` × ΔEC (§8.2 5–6)."""
        cfg = self.cfg
        assert cfg.drum_volume_l is not None
        step = delta_ec * cfg.fraction_per_iteration
        total = sum(ratio)
        plan: dict[str, float] = {}
        for key, part in zip(NUTRIENTS, ratio, strict=True):
            gain = getattr(cfg.ec_gain_ms_cm_per_ml_l, key)
            flow = getattr(cfg.pump_flow_ml_s, key)
            ml = step * part / total * cfg.drum_volume_l / gain
            seconds = ml / flow
            limit = self.config.outputs[self.pumps[key]].max_on_s
            if limit is not None and seconds > limit:
                seconds = limit  # dosis dipotong: kesalahan arah selalu ke dosis kurang
            plan[key] = seconds if seconds >= cfg.min_pump_s else 0.0
        return plan

    async def _dose(
        self,
        run: DosingRun,
        delta_ec: float,
        ratio: tuple[float, float, float],
        dosed: dict[str, float],
    ) -> None:
        # §8.2 langkah 7: N → P → K, satu pompa dalam satu waktu (S6).
        for key, seconds in self.plan(delta_ec, ratio).items():
            if seconds <= 0:
                continue
            name = self.pumps[key]
            flow = getattr(self.cfg.pump_flow_ml_s, key)
            await self._switch_on(name, seconds)
            started = self.clock.monotonic()
            try:
                await self._wait(seconds, expect_on=name)
            finally:
                ml = min(seconds, self.clock.monotonic() - started) * flow
                dosed[key] += ml
                run.total_ml[key] += ml
            await self._switch_off(name)

    # --- laporan (Kontrak §3, §4.4) -----------------------------------------------------------

    def _publish(self, run: DosingRun, dosed: dict[str, float]) -> None:
        """F9.4: satu pesan telemetri per iterasi (metrik katalog BE-4)."""
        if self.sink is None or run.ec_measured is None:
            return
        data: dict[str, float] = {
            "dosing.iteration": run.iteration,
            "dosing.ec_target": run.target_ec,
            "dosing.ec_measured": run.ec_measured,
        }
        for key in NUTRIENTS:
            data[f"dosing.{key}_ml"] = round(dosed[key], 1)
        self.sink.publish_telemetry(self.device, data)

    def _metadata(self, run: DosingRun) -> dict:
        reading = run.last_reading
        return {
            "phase": run.phase,
            "iterations": run.iteration,
            "ecTarget": run.target_ec,
            "ecFinal": run.ec_measured,
            "ph": reading.ph if reading is not None else None,
            "waterTemperature": reading.water_temperature_c if reading is not None else None,
            "nMl": round(run.total_ml["n"], 1),
            "pMl": round(run.total_ml["p"], 1),
            "kMl": round(run.total_ml["k"], 1),
        }

    def _alert_result(self, run: DosingRun) -> None:
        if self.sink is None or run.status == STOPPED:
            return  # STOP dari aplikasi bukan kegagalan; agent berhenti juga bukan
        metadata = self._metadata(run)
        if run.status == DONE:
            added = any(run.total_ml[key] > 0 for key in NUTRIENTS)
            message = (
                f"Kadar pupuk di drum sudah sesuai target setelah {run.iteration} kali periksa."
                if added
                else "Kadar pupuk di drum sudah cukup, tidak ada pupuk yang ditambahkan."
            )
            self._send("DOSING_DONE", "INFO", "Pupuk di drum sudah sesuai", message, metadata)
        elif run.status == NOT_CONVERGING:
            self._send(
                "DOSING_NOT_CONVERGING",
                "WARNING",
                "Pupuk di drum belum mencapai target",
                f"Setelah {run.iteration} kali periksa, kadar pupuk masih kurang. Periksa isi "
                "galon pupuk, selang pompa pupuk, dan kebersihan sensor larutan.",
                metadata,
            )
        else:
            self._send(
                "DOSING_FAILED",
                "WARNING",
                "Pemberian pupuk otomatis berhenti",
                f"Pemberian pupuk otomatis dihentikan: {run.text}.",
                {**metadata, "reason": run.reason},
            )

    def _send(self, type: str, severity: str, title: str, message: str, metadata: dict) -> None:
        assert self.sink is not None
        self.sink.publish_alert(
            self.device,
            type,
            severity,
            title,
            message,
            actuator_code=self.actuator_code,
            metadata=metadata,
        )
