"""Siklus baca larutan drum: pH, EC, suhu (Arsitektur §6.3, F6.3–F6.5).

Urutan (probe EC dan pH di larutan yang sama; eksitasi EC mengganggu pH):
R5 OFF → tunggu `settle_s` → median `samples` bacaan pH → R5 ON → tunggu → median EC → R5 OFF →
baca DS18B20. Selama siklus EC-POWER (R5) dikelola edge: perintah manual ditolak (Kontrak §5).

Konversi:
- EC mengikuti pustaka DFRobot DFR0300: dua nilai K (rentang rendah dari larutan 1413 µS/cm,
  rentang tinggi dari 12,88 mS/cm) dengan perpindahan rentang berhisteresis, lalu dikompensasi ke
  25 °C: `EC25 = EC / (1 + temp_coef × (T − 25))`. Tanpa suhu larutan EC tidak dikirim, karena
  katalog mendefinisikan `tank.ec` sudah terkompensasi (Kontrak §3).
- pH linear dari kalibrasi 2/3 titik (kuadrat terkecil), slope dikoreksi suhu (Nernst, sebanding
  suhu mutlak) di sekitar pH 7.

S9: nilai di luar `analog.valid` dibuang dari telemetri, alert `EC_PH_INVALID`, dan bacaan
ditandai tidak layak untuk dosing (`valid_for_dosing`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
from dataclasses import dataclass, field

from .bus import BusManager
from .config import Config, EcChannelConfig, PhChannelConfig
from .hal import Clock
from .logs import fields
from .outputs import Outputs
from .poller import TelemetrySink
from .profiles import RegisterMapProfile
from .safety import Safety

log = logging.getLogger(__name__)

KELVIN = 273.15
EC_CAL_LOW_MS_CM = 1.413  # larutan kalibrasi DFR0300 (Arsitektur §8.3)
EC_CAL_HIGH_MS_CM = 12.88
PH_PIVOT = 7.0
TEMPERATURE_FIELD = "water_temperature"


# --- konversi (fungsi murni) --------------------------------------------------------------------


def ec_k_values(cfg: EcChannelConfig) -> tuple[float, float]:
    """K rentang rendah dan tinggi. Titik yang belum dikalibrasi memakai titik lain, atau `k`."""
    cal = cfg.cal
    k_low = cfg.mv_per_ms_cm * EC_CAL_LOW_MS_CM / cal.v_1413 if cal.v_1413 else None
    k_high = cfg.mv_per_ms_cm * EC_CAL_HIGH_MS_CM / cal.v_12880 if cal.v_12880 else None
    if k_low is None and k_high is None:
        return cfg.k, cfg.k
    return (k_low or k_high, k_high or k_low)  # type: ignore[return-value]


def ec_from_mv(mv: float, cfg: EcChannelConfig, high_range: bool) -> tuple[float, bool]:
    """EC terukur (mS/cm, belum dikompensasi) dan rentang yang dipakai sesudahnya."""
    k_low, k_high = ec_k_values(cfg)
    raw = mv / cfg.mv_per_ms_cm
    low_switch, high_switch = cfg.range_switch_ms_cm
    value = raw * (k_high if high_range else k_low)
    if value > high_switch:
        high_range = True
    elif value < low_switch:
        high_range = False
    return raw * (k_high if high_range else k_low), high_range


def compensate_ec(ec_ms_cm: float, temperature_c: float, temp_coef: float) -> float:
    return ec_ms_cm / (1 + temp_coef * (temperature_c - 25))


def ph_from_mv(mv: float, cfg: PhChannelConfig, temperature_c: float | None) -> float | None:
    """pH dari tegangan; None bila titik kalibrasi kurang dari dua."""
    points = cfg.cal.points()
    if len(points) < 2:
        return None
    mean_v = sum(v for v, _ in points) / len(points)
    mean_p = sum(p for _, p in points) / len(points)
    slope = sum((v - mean_v) * (p - mean_p) for v, p in points) / sum(
        (v - mean_v) ** 2 for v, _ in points
    )
    intercept = mean_p - slope * mean_v
    if temperature_c is None:
        return intercept + slope * mv
    mv_pivot = (PH_PIVOT - intercept) / slope
    factor = (cfg.cal_temperature_c + KELVIN) / (temperature_c + KELVIN)
    return PH_PIVOT + slope * factor * (mv - mv_pivot)


# --- siklus -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalogReading:
    ph: float | None = None
    ec_ms_cm: float | None = None  # sudah dikompensasi ke 25 °C
    water_temperature_c: float | None = None
    invalid: dict[str, float] = field(default_factory=dict)  # nama → nilai di luar rentang
    at: float = 0.0  # monotonic

    @property
    def valid_for_dosing(self) -> bool:
        """S9: dosing hanya boleh dengan pH, EC, dan suhu yang terbaca dan masuk akal."""
        values = (self.ph, self.ec_ms_cm, self.water_temperature_c)
        return not self.invalid and all(v is not None for v in values)


class AnalogReader:
    def __init__(
        self,
        config: Config,
        bus: BusManager,
        outputs: Outputs,
        safety: Safety,
        clock: Clock,
        sink: TelemetrySink | None = None,
    ) -> None:
        self.config = config
        self.analog = config.analog
        self.bus = bus
        self.outputs = outputs
        self.safety = safety
        self.clock = clock
        self.sink = sink
        devices = config.devices
        self._adc = {
            name: RegisterMapProfile(name, devices[name])
            for name in {self.analog.ec.device, self.analog.ph.device}
        }
        temp_name = self.analog.temperature_device
        self._temperature = RegisterMapProfile(temp_name, devices[temp_name])
        self.last: AnalogReading | None = None
        self._high_range = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        for key in ("ph", "ec"):
            if not self._configured(getattr(self.analog, key)):
                log.warning("kanal %s belum punya register ADC di config (P-H8), tidak dibaca", key)
        if len(self.analog.ph.cal.points()) < 2:
            log.warning("kalibrasi pH kurang dari dua titik (P-H13): pH tidak dikirim")

    # --- siklus hidup -------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="analog")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return [self._task] if self._task is not None else []

    async def _loop(self) -> None:
        while True:
            await self.measure()
            await self.clock.sleep(self.config.polling.tank_s)

    # --- pengukuran ---------------------------------------------------------------------------

    def _configured(self, channel) -> bool:
        return f"a{channel.ch}" in self._adc[channel.device].registers

    async def _sample(self, channel) -> float | None:
        name = f"a{channel.ch}"
        values = []
        for _ in range(self.analog.samples):
            read = await self._adc[channel.device].read_fields(self.bus, [name])
            if name in read:
                values.append(read[name])
        # Median dari kurang dari separuh sampel tidak lagi tahan terhadap bacaan liar.
        if len(values) * 2 < self.analog.samples:
            log.warning("bacaan ADC gagal", extra=fields(channel=name, ok=len(values)))
            return None
        return statistics.median(values)

    async def _ec_power(self, on: bool) -> bool:
        result = await self.outputs.set(self.analog.power_output, on, cause="analog")
        return result.ok

    async def _read_voltages(
        self, want_ph: bool, want_ec: bool
    ) -> tuple[float | None, float | None]:
        """Urutan R5 §6.3 untuk kanal yang diminta. Selalu berakhir dengan R5 OFF."""
        ph_mv = ec_mv = None
        if not (want_ph or want_ec):
            return ph_mv, ec_mv
        power = self.analog.power_output
        self.safety.managed[power] = "sensor EC sedang dipakai pembacaan otomatis"
        try:
            if want_ph and await self._ec_power(False):
                await self.clock.sleep(self.analog.settle_s)
                ph_mv = await self._sample(self.analog.ph)
            if want_ec and await self._ec_power(True):
                await self.clock.sleep(self.analog.settle_s)
                ec_mv = await self._sample(self.analog.ec)
        finally:
            if self.outputs.state(power) is not False:
                await self._ec_power(False)
            self.safety.managed.pop(power, None)
        return ph_mv, ec_mv

    async def read_temperature(self) -> float | None:
        values = await self._temperature.read_fields(self.bus, [TEMPERATURE_FIELD])
        return values.get(TEMPERATURE_FIELD)

    async def measure(self) -> AnalogReading:
        """Satu siklus §6.3."""
        async with self._lock:
            ph_mv, ec_mv = await self._read_voltages(
                self._configured(self.analog.ph), self._configured(self.analog.ec)
            )
            reading = self._convert(ph_mv, ec_mv, await self.read_temperature())
        self.last = reading
        self._publish(reading, ph_mv, ec_mv)
        return reading

    async def read_raw(self, key: str) -> tuple[float | None, float | None]:
        """Tegangan mentah (mV, median) kanal `ph` atau `ec` dan suhu larutan, untuk kalibrasi."""
        if key not in ("ph", "ec"):
            raise ValueError("kanal harus 'ph' atau 'ec'")
        if not self._configured(getattr(self.analog, key)):
            raise ValueError(f"kanal {key} belum punya register ADC di config (P-H8)")
        async with self._lock:
            ph_mv, ec_mv = await self._read_voltages(key == "ph", key == "ec")
            temperature = await self.read_temperature()
        return (ph_mv if key == "ph" else ec_mv), temperature

    def _in_range(self, name: str, value: float) -> bool:
        low, high = getattr(self.analog.valid, name)
        return low <= value <= high

    def _convert(
        self, ph_mv: float | None, ec_mv: float | None, temperature: float | None
    ) -> AnalogReading:
        invalid: dict[str, float] = {}
        if temperature is not None and not self._in_range("water_temperature_c", temperature):
            invalid["water_temperature_c"] = temperature
            temperature = None
        ph = None
        if ph_mv is not None:
            ph = ph_from_mv(ph_mv, self.analog.ph, temperature)
            if ph is not None and not self._in_range("ph", ph):
                invalid["ph"] = round(ph, 2)
                ph = None
        ec25 = None
        if ec_mv is not None:
            measured, self._high_range = ec_from_mv(ec_mv, self.analog.ec, self._high_range)
            if temperature is None:
                log.warning("EC tidak dikirim: suhu larutan tidak terbaca untuk kompensasi")
            else:
                ec25 = compensate_ec(measured, temperature, self.analog.temp_coef)
                if not self._in_range("ec_ms_cm", ec25):
                    invalid["ec_ms_cm"] = round(ec25, 3)
                    ec25 = None
        return AnalogReading(
            ph=None if ph is None else round(ph, 2),
            ec_ms_cm=None if ec25 is None else round(ec25, 3),
            water_temperature_c=temperature,
            invalid=invalid,
            at=self.clock.monotonic(),
        )

    def _publish(self, reading: AnalogReading, ph_mv: float | None, ec_mv: float | None) -> None:
        log.info(
            "bacaan larutan",
            extra=fields(
                ph=reading.ph,
                ec=reading.ec_ms_cm,
                temp=reading.water_temperature_c,
                ph_mv=ph_mv,
                ec_mv=ec_mv,
                invalid=",".join(reading.invalid) or None,
            ),
        )
        if self.sink is None:
            return
        adc_cfg = self.config.devices[self.analog.ec.device]
        logical_of = self.outputs.logical_of
        per_logical: dict[str, dict[str, float]] = {}
        if reading.water_temperature_c is not None:
            per_logical.setdefault(logical_of[self._temperature.name], {})[
                self._temperature.metric(TEMPERATURE_FIELD)
            ] = reading.water_temperature_c
        tank = per_logical.setdefault(logical_of[self.analog.ec.device], {})
        if reading.ec_ms_cm is not None:
            tank[f"{adc_cfg.metric_prefix}.ec"] = reading.ec_ms_cm
        if reading.ph is not None:
            tank[f"{adc_cfg.metric_prefix}.ph"] = reading.ph
        for logical, data in per_logical.items():
            if data:
                self.sink.publish_telemetry(logical, data)
        if reading.invalid:
            self.sink.publish_alert(
                logical_of[self.analog.ec.device],
                "EC_PH_INVALID",
                "WARNING",
                "Angka sensor larutan pupuk tidak wajar",
                "Bacaan pH, EC, atau suhu larutan di luar batas wajar, jadi tidak dipakai. "
                "Pemberian pupuk otomatis ditahan sampai bacaan normal. Periksa probe dan kabel.",
                metadata={
                    "invalid": reading.invalid,
                    "phMillivolt": ph_mv,
                    "ecMillivolt": ec_mv,
                    "validRanges": self.analog.valid.model_dump(),
                },
            )
