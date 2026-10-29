"""Model dan pemuat `fertimata.yaml`.

Semua angka hardware (alamat, kanal, batas waktu, ambang, kalibrasi) hanya hidup di berkas config
(Arsitektur §11). Modul ini memvalidasinya saat startup supaya kesalahan ketik tertangkap sebelum
satu relay pun disentuh. Kunci yang tidak dikenal ditolak (`extra="forbid"`).
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# Sumber device logis yang bukan perangkat Modbus (Kontrak §3: JETSON-01 = GPIO + SoC).
NON_MODBUS_SOURCES = frozenset({"gpio", "soc"})

DeviceProfile = Literal[
    "pzem016", "xy_md02", "soil_7in1", "lux_rs485", "ds18b20_mb", "adc_7ch", "relay_mb"
]


class ConfigError(Exception):
    """Config tidak bisa dibaca atau tidak valid. Pesannya menyebut kunci yang bermasalah."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------------
# Bagian-bagian config
# --------------------------------------------------------------------------------------------


class AgentConfig(_Model):
    site: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    hal: Literal["real", "fake"]
    # Zona waktu untuk hal yang dibaca manusia: jadwal patroli dan nama berkas foto (WIB = 7).
    # Payload MQTT tetap UTC.
    utc_offset_h: float = Field(ge=-12, le=14)
    # Q9: batas waktu mematikan semua relay saat agent berhenti; harus < TimeoutStopSec systemd.
    stop_off_timeout_s: float = Field(gt=0)


class TopicsConfig(_Model):
    telemetry: str
    status: str
    command: str
    command_ack: str
    ai: str
    alert: str

    @field_validator("*")
    @classmethod
    def _has_device_placeholder(cls, value: str) -> str:
        # Backend mempercayai deviceId dari topik, bukan dari payload (Kontrak §2).
        if "{deviceId}" not in value:
            raise ValueError("template topik wajib memuat {deviceId}")
        return value


class MqttConfig(_Model):
    host: str
    port: int = Field(ge=1, le=65535)
    client_id: str = Field(min_length=1)
    username: str | None = None
    password: str | None = None
    # Kontrak §1: QoS 1 untuk semua topik, sesi persisten.
    qos: Literal[1] = 1
    clean_start: bool = False
    heartbeat_s: float = Field(gt=0)
    keepalive_s: int = Field(default=60, ge=5)
    # MQTT v5: sesi dengan clean_start=false hanya bertahan selama Session Expiry Interval.
    session_expiry_s: int = Field(default=3600, ge=0)
    reconnect_min_s: float = Field(default=1, gt=0)
    reconnect_max_s: float = Field(default=30, gt=0)
    outbox_max: int = Field(default=1000, ge=10)
    dedup_size: int = Field(default=200, ge=1)
    alert_repeat_s: float = Field(default=600, ge=0)
    topics: TopicsConfig

    @model_validator(mode="after")
    def _reconnect_range(self) -> MqttConfig:
        if self.reconnect_max_s < self.reconnect_min_s:
            raise ValueError("reconnect_max_s harus >= reconnect_min_s")
        return self


class BusConfig(_Model):
    port: str
    baudrate: int = Field(gt=0)
    parity: Literal["N", "E", "O"]
    stopbits: Literal[1, 2]
    timeout_s: float = Field(gt=0)
    retries: int = Field(ge=0)
    gap_ms: float = Field(ge=0)
    fail_threshold: int = Field(ge=1)
    # Jeda mencoba membuka ulang port yang hilang (mis. adaptor USB dicabut).
    reopen_s: float = Field(default=5, gt=0)


class RegisterSpec(_Model):
    """Satu nilai di peta register perangkat (Arsitektur §4.5). Nilai = mentah × scale."""

    fc: Literal[3, 4]
    address: int = Field(ge=0, le=0xFFFF)
    type: Literal["u16", "s16", "u32", "s32"] = "u16"
    # Urutan word nilai 32 bit. Wajib ditulis untuk u32/s32 karena antar vendor berbeda
    # (PZEM: word rendah dulu; banyak sensor cahaya: word tinggi dulu).
    word_order: Literal["lo_hi", "hi_lo"] | None = None
    scale: float = 1.0

    @model_validator(mode="after")
    def _word_order_for_32bit(self) -> RegisterSpec:
        if self.type in ("u32", "s32") and self.word_order is None:
            raise ValueError("register 32 bit wajib punya word_order (lo_hi atau hi_lo)")
        return self

    @property
    def width(self) -> int:
        return 2 if self.type in ("u32", "s32") else 1


class RelayMapConfig(_Model):
    """Cara menulis/membaca kanal relay Modbus (Arsitektur §4.5, belum terverifikasi: P-H4).

    Dua pola umum: **A** coil (`write_fc: 5`, baca `read_fc: 1`) dan **B** holding register
    (`write_fc: 6`, nilai ON/OFF tertentu, mis. 0x0100/0x0200). Semua angka sengaja wajib ditulis
    di config karena belum dicek di modul yang dibeli. Kanal Rn ada di alamat `*_base + n`.
    """

    write_fc: Literal[5, 6]
    write_base: int = Field(ge=0, le=0xFFFF)
    on_value: int | None = Field(default=None, ge=0, le=0xFFFF)  # hanya write_fc 6
    off_value: int | None = Field(default=None, ge=0, le=0xFFFF)
    # Baca balik state kanal: 1 coil / 2 discrete input / 3–4 register (≠ 0 berarti ON).
    read_fc: Literal[1, 2, 3, 4]
    read_base: int = Field(ge=0, le=0xFFFF)
    # Input digital (IN1 …): 2 discrete input / 3–4 register (≠ 0 berarti kontak tertutup).
    input_fc: Literal[2, 3, 4] | None = None
    input_base: int | None = Field(default=None, ge=0, le=0xFFFF)  # alamat IN1

    @model_validator(mode="after")
    def _values_match_function(self) -> RelayMapConfig:
        if self.write_fc == 6:
            if self.on_value is None or self.off_value is None:
                raise ValueError("write_fc 6 wajib punya on_value dan off_value")
            if self.on_value == self.off_value:
                raise ValueError("on_value dan off_value tidak boleh sama")
        elif self.on_value is not None or self.off_value is not None:
            raise ValueError("on_value/off_value hanya dipakai bila write_fc 6")
        if (self.input_fc is None) != (self.input_base is None):
            raise ValueError("input_fc dan input_base harus diisi bersama")
        return self


# Profil yang menghasilkan telemetri lewat peta register (poller F3.7).
SENSOR_PROFILES = frozenset({"pzem016", "xy_md02", "soil_7in1", "lux_rs485", "ds18b20_mb"})


class DeviceConfig(_Model):
    address: int = Field(ge=1, le=247)
    profile: DeviceProfile
    timeout_s: float | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, ge=1)
    # Nama untuk teks yang dibaca petani (judul alert), mis. "sensor tanah".
    label: str | None = None
    # Awalan kunci metrik katalog backend, mis. "mains" → mains.voltage.
    metric_prefix: str | None = None
    # Menimpa/menambah peta register bawaan profil. Untuk profil yang belum terverifikasi
    # (soil_7in1, lux_rs485, ds18b20_mb) hanya peta ini yang dipakai.
    registers: dict[str, RegisterSpec] = Field(default_factory=dict)
    # Peta tulis/baca kanal untuk profil relay_mb (P-H4).
    relay: RelayMapConfig | None = None

    @model_validator(mode="after")
    def _profile_requirements(self) -> DeviceConfig:
        if self.profile == "relay_mb":
            if self.channels is None:
                raise ValueError("profil relay_mb wajib punya 'channels'")
            if self.relay is None:
                raise ValueError("profil relay_mb wajib punya 'relay' (peta tulis/baca kanal)")
        elif self.relay is not None:
            raise ValueError("'relay' hanya untuk profil relay_mb")
        if self.profile in SENSOR_PROFILES and not self.metric_prefix:
            raise ValueError(f"profil {self.profile} wajib punya 'metric_prefix'")
        return self


class LogicalDeviceConfig(_Model):
    sources: list[str] = Field(min_length=1)


class PollingConfig(_Model):
    pzem_s: float = Field(gt=0)
    relays_s: float = Field(gt=0)
    air_light_s: float = Field(gt=0)
    soil_s: float = Field(gt=0)
    tank_s: float = Field(gt=0)
    soc_s: float = Field(gt=0)
    trolley_s: float = Field(gt=0)  # telemetri trolley.* berkala (juga saat berubah)
    # Telemetri PZEM-01 dikirim lebih jarang daripada dibaca: deteksi lokal (S7, S10, S11) tetap
    # memakai setiap bacaan `pzem_s`, backend cukup menyimpan satu bacaan per interval ini.
    pzem_publish_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _publish_not_faster_than_read(self) -> PollingConfig:
        if self.pzem_publish_s < self.pzem_s:
            raise ValueError("pzem_publish_s tidak boleh lebih kecil dari pzem_s")
        return self


class OutputConfig(_Model):
    actuator_code: str = Field(min_length=1)
    device: str
    ch: int = Field(ge=0)
    max_on_s: float | None = Field(default=None, gt=0)
    requires_duration: bool = False
    # Nama untuk teks yang dibaca petani (alert, ACK gagal), mis. "pompa siram".
    label: str | None = None

    @model_validator(mode="after")
    def _duration_needs_limit(self) -> OutputConfig:
        # §5.4: output yang wajib berdurasi harus punya batas keras di edge (S2).
        if self.requires_duration and self.max_on_s is None:
            raise ValueError("requires_duration=true wajib disertai max_on_s")
        return self


class VirtualActuatorConfig(_Model):
    """Aktuator virtual backend (BE-1, Kontrak §5.1): perintah tingkat tinggi, tanpa relay."""

    actuator_code: str = Field(min_length=1)
    device: str = Field(min_length=1)  # device logis, mis. JETSON-01
    label: str = Field(min_length=1)


# Jenis aktuator virtual yang dikenal edge (Kontrak §5.1).
VirtualKind = Literal["trolley_goto", "trolley_patrol"]


class RelayInputConfig(_Model):
    device: str
    input: int = Field(ge=1)
    metric: str | None = None  # kunci metrik katalog, mis. tank.float_low_ok


class GpioInputConfig(_Model):
    # Nomor pin dari papan GPIO belum terverifikasi (P-H9), jadi boleh null sampai F7.
    gpio_chip: str | None = None
    gpio_line: int | None = Field(default=None, ge=0)
    active: Literal["low", "high"]
    debounce_ms: float = Field(ge=0)


class InputsConfig(_Model):
    float_low_ok: RelayInputConfig
    reed: GpioInputConfig
    home: GpioInputConfig
    end: GpioInputConfig


class TravelConfig(_Model):
    """Output jalan trolley dan limit di arah geraknya (S13, S16)."""

    run: str  # nama output RUN (R2)
    direction: str  # nama output DIR (R3)
    limit_when_on: Literal["home", "end"]  # limit yang dituju saat DIR ON
    limit_when_off: Literal["home", "end"]

    @model_validator(mode="after")
    def _different_limits(self) -> TravelConfig:
        if self.limit_when_on == self.limit_when_off:
            raise ValueError("limit_when_on dan limit_when_off harus berbeda")
        return self


class SafetyConfig(_Model):
    # Nama kelompok output, mis. "dosing" = dose_n + dose_p + dose_k + stirrer. Boleh dipakai di
    # exclusive_groups dan daftar blokir di bawah.
    output_groups: dict[str, list[str]] = Field(default_factory=dict)
    exclusive_groups: list[list[str]]
    blocked_on_backup_power: list[str]
    blocked_on_float_low: list[str]
    # Output kunci tidak boleh berubah (ON maupun OFF) selama salah satu output di daftarnya ON:
    # DIR saat RUN ON (S12), pan saat trolley jalan (S14, Q5). Mengubah arah bukan "berhenti",
    # jadi ini satu-satunya pengecualian aturan "OFF tidak pernah diblokir".
    blocked_while_on: dict[str, list[str]] = Field(default_factory=dict)
    travel: TravelConfig | None = None
    float_max_age_s: float = Field(gt=0)
    dir_settle_ms: float = Field(ge=0)
    stop_settle_ms: float = Field(ge=0)

    def expand(self, names: list[str]) -> frozenset[str]:
        """Ubah daftar nama output/kelompok menjadi himpunan nama output."""
        result: set[str] = set()
        for name in names:
            result.update(self.output_groups.get(name, [name]))
        return frozenset(result)


class EcCalibration(_Model):
    """Tegangan papan EC (mV, sudah dinormalkan ke 25 °C) di larutan kalibrasi DFR0300."""

    v_1413: float | None = Field(default=None, gt=0)
    v_12880: float | None = Field(default=None, gt=0)


class PhCalibration(_Model):
    """Tegangan papan pH (mV) di buffer 4,01 · 6,86 · 9,18. Minimal dua titik untuk konversi."""

    v_401: float | None = Field(default=None, gt=0)
    v_686: float | None = Field(default=None, gt=0)
    v_918: float | None = Field(default=None, gt=0)

    def points(self) -> list[tuple[float, float]]:
        """Pasangan (mV, pH) yang sudah diisi, urut pH naik."""
        pairs = ((self.v_401, 4.01), (self.v_686, 6.86), (self.v_918, 9.18))
        return [(mv, ph) for mv, ph in pairs if mv is not None]

    @model_validator(mode="after")
    def _monotonic(self) -> PhCalibration:
        # Tegangan elektroda berubah searah dengan pH; buffer tertukar atau probe rusak
        # terlihat sebagai urutan yang tidak monoton.
        voltages = [mv for mv, _ in self.points()]
        steps = [b - a for a, b in itertools.pairwise(voltages)]
        if steps and not (all(s > 0 for s in steps) or all(s < 0 for s in steps)):
            raise ValueError(
                "tegangan kalibrasi pH harus berurutan naik atau turun mengikuti pH buffer "
                "(tidak boleh sama)"
            )
        return self


class EcChannelConfig(_Model):
    device: str
    ch: int = Field(ge=1)
    # Tanpa kalibrasi: EC = mV / mv_per_ms_cm × k (pustaka DFRobot_EC: RES2 820 Ω × ECREF 200).
    k: float = Field(gt=0)
    mv_per_ms_cm: float = Field(gt=0)
    # Pindah rentang K rendah → tinggi di atas angka kedua, kembali di bawah angka pertama
    # (pustaka DFRobot_EC: 2,0 / 2,5 mS/cm).
    range_switch_ms_cm: tuple[float, float]
    # K hasil kalibrasi yang diterima alat bantu kalibrasi (F6.6). Jendela pustaka DFRobot_EC:
    # rawEC 0,9–1,9 (1413 µS/cm) dan 9–16,8 (12,88 mS/cm) ≈ K 0,75–1,5.
    cal_k_range: tuple[float, float]
    cal: EcCalibration = EcCalibration()

    @model_validator(mode="after")
    def _switch_order(self) -> EcChannelConfig:
        for name in ("range_switch_ms_cm", "cal_k_range"):
            low, high = getattr(self, name)
            if not 0 < low < high:
                raise ValueError(f"{name} harus [bawah, atas] dengan 0 < bawah < atas")
        return self


class PhChannelConfig(_Model):
    device: str
    ch: int = Field(ge=1)
    cal_temperature_c: float  # suhu buffer saat kalibrasi; slope dikoreksi terhadapnya
    cal: PhCalibration = PhCalibration()


class ValidRanges(_Model):
    """S9: rentang fisik yang masuk akal. Di luar itu bacaan dianggap rusak (EC_PH_INVALID)."""

    ec_ms_cm: tuple[float, float]
    ph: tuple[float, float]
    water_temperature_c: tuple[float, float]

    @model_validator(mode="after")
    def _ordered(self) -> ValidRanges:
        for name in ("ec_ms_cm", "ph", "water_temperature_c"):
            low, high = getattr(self, name)
            if low >= high:
                raise ValueError(f"{name}: batas bawah harus lebih kecil dari batas atas")
        return self


class AnalogConfig(_Model):
    # §6.3: R5 (EC-POWER) OFF saat membaca pH, ON saat membaca EC. Dikelola edge selama siklus.
    power_output: str
    temperature_device: str  # DS18B20 untuk kompensasi suhu
    ec: EcChannelConfig
    ph: PhChannelConfig
    settle_s: float = Field(ge=0)
    samples: int = Field(ge=1)
    temp_coef: float
    valid: ValidRanges


class TrolleyConfig(_Model):
    stations: int = Field(ge=1)
    station_pitch_mm: float = Field(gt=0)
    first_station_mm: float = Field(ge=0)
    speed_m_s: float = Field(gt=0)
    settle_s: float = Field(ge=0)
    pan_settle_s: float = Field(ge=0)
    segment_timeout_s: float = Field(gt=0)
    travel_timeout_s: float = Field(gt=0)

    @property
    def rail_length_mm(self) -> float:
        # Rel simetris: jarak S1 ke HOME sama dengan jarak stasiun terakhir ke END (§9.1).
        return 2 * self.first_station_mm + (self.stations - 1) * self.station_pitch_mm


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CameraConfig(_Model):
    device: str
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    exposure: float | None = None
    white_balance: float | None = None
    format: Literal["png", "jpg"]
    root: str
    # Output pan kamera (Kontrak §5: ON = barat, OFF = timur).
    pan_output: str
    # §9.4: di stasiun genap barat dulu, menghemat satu gerakan pan per stasiun.
    alternate_pan: bool = False
    # Gerbang blur §13 (variance of Laplacian dari HAL). Null = gerbang mati sampai ambangnya
    # diukur dengan kamera asli (P-H10).
    blur_threshold: float | None = Field(default=None, gt=0)
    patrol_schedule: list[str] = Field(default_factory=list)
    # Agent yang baru hidup setelah jam patroli masih menjalankannya bila belum lewat selama ini.
    patrol_catchup_min: float = Field(ge=0)
    # F8.5: di bawah `disk_warn_free_gb` alert; di bawah `disk_stop_free_gb` foto tidak disimpan
    # supaya sistem Jetson tidak kehabisan ruang. Data riset tidak pernah dihapus otomatis.
    disk_warn_free_gb: float = Field(gt=0)
    disk_stop_free_gb: float = Field(gt=0)

    @field_validator("patrol_schedule")
    @classmethod
    def _check_times(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _HHMM.match(item):
                raise ValueError(f"jam patroli '{item}' harus berformat \"HH:MM\" (WIB, dikutip)")
        if len(set(value)) != len(value):
            raise ValueError("jam patroli tidak boleh ganda")
        return value

    @model_validator(mode="after")
    def _disk_order(self) -> CameraConfig:
        if self.disk_stop_free_gb >= self.disk_warn_free_gb:
            raise ValueError("disk_stop_free_gb harus lebih kecil dari disk_warn_free_gb")
        return self


class PowerConfig(_Model):
    # Penentu powerSource (§7.4): PZEM diam sementara relay acuan menjawab = PLN padam.
    pzem_device: str
    reference_device: str
    outage_voltage_v: float = Field(gt=0)
    restore_voltage_v: float = Field(gt=0)
    # Pemantauan pompa lewat arus total panel (§7.5). Baseline/nominal belum terverifikasi
    # (P-H11); deteksi dimatikan selama salah satunya null.
    pump_output: str | None = None
    pump_baseline_a: float | None = Field(default=None, ge=0)
    pump_nominal_a: float | None = Field(default=None, gt=0)
    ssr_short_margin_a: float | None = Field(default=None, gt=0)  # Q4: cegah alarm palsu noise
    dry_run_ratio: float = Field(gt=0, lt=1)  # kenaikan arus < rasio × nominal = pompa kering
    current_settle_s: float = Field(ge=0)  # jeda setelah R0 berubah sebelum arus dinilai
    anomaly_reads: int = Field(ge=1)  # bacaan janggal berturut-turut sebelum bertindak
    soc_warn_c: float
    # Berkas suhu SoC Jetson (sysfs, nilai m°C). Yang tertinggi dipakai.
    soc_thermal_glob: str

    @model_validator(mode="after")
    def _hysteresis(self) -> PowerConfig:
        if self.restore_voltage_v <= self.outage_voltage_v:
            raise ValueError(
                "restore_voltage_v harus lebih besar dari outage_voltage_v (histeresis)"
            )
        return self


class FakeDosePump(_Model):
    """Pompa pupuk di simulator: debit dan kenaikan EC bila 1 mL stok masuk 1 L air."""

    flow_ml_s: float = Field(gt=0)
    ec_gain_ms_cm_per_ml_l: float = Field(gt=0)


class FakeConfig(_Model):
    """Parameter simulator untuk `agent.hal: fake`. Bukan angka hardware."""

    tick_s: float = Field(default=0.05, gt=0)
    seed: int | None = None
    magnet_width_mm: float = Field(default=20, gt=0)
    start_position_mm: float = Field(default=0, ge=0)
    mains_voltage_v: float = Field(default=221.0, gt=0)
    air_temperature_c: float = 29.0
    air_humidity_pct: float = Field(default=70.0, ge=0, le=100)
    float_low_ok: bool = True
    soc_temperature_c: float = 48.0
    # Larutan drum dan papan analog palsu (model fisik simulator, bukan angka hardware).
    tank_ec_ms_cm: float = Field(default=1.8, ge=0)
    tank_ph: float = Field(default=6.2, ge=0, le=14)
    ec_mv_per_ms_cm: float = Field(default=164.0, gt=0)
    ph_mv_at_7: float = Field(default=2500.0, gt=0)
    ph_mv_per_ph: float = -180.0
    # Eksitasi papan EC mengganggu pH saat R5 ON (alasan urutan §6.3).
    ec_ph_interference_mv: float = 150.0
    # Pompa pupuk (nama output → debit/kenaikan EC) menambah EC drum, supaya perintah mentah
    # DOSING-N/P/K dari layanan dosing tim AI terlihat di telemetri. Kosong = EC drum tetap.
    tank_volume_l: float = Field(default=150.0, gt=0)
    dose_pumps: dict[str, FakeDosePump] = Field(default_factory=dict)
    stirrer_output: str | None = None
    # Stok yang dipompa ke drum baru terbaca probe setelah tercampur: konstanta waktu campur saat
    # pompa aduk menyala dan saat diam (alasan Arsitektur §8.2 langkah 8 "tunggu EC stabil").
    mix_tau_s: float = Field(default=20.0, gt=0)
    mix_idle_tau_s: float = Field(default=600.0, gt=0)
    image_width: int = Field(default=64, gt=0)
    image_height: int = Field(default=48, gt=0)
    image_sharpness: float = Field(default=150.0, ge=0)  # variance of Laplacian foto palsu


# --------------------------------------------------------------------------------------------
# Akar config
# --------------------------------------------------------------------------------------------


class Config(_Model):
    agent: AgentConfig
    mqtt: MqttConfig
    bus: BusConfig
    devices: dict[str, DeviceConfig]
    logical_devices: dict[str, LogicalDeviceConfig]
    polling: PollingConfig
    outputs: dict[str, OutputConfig]
    virtual_actuators: dict[VirtualKind, VirtualActuatorConfig] = Field(default_factory=dict)
    inputs: InputsConfig
    safety: SafetyConfig
    analog: AnalogConfig
    trolley: TrolleyConfig
    camera: CameraConfig
    power: PowerConfig
    fake: FakeConfig = FakeConfig()

    @model_validator(mode="after")
    def _cross_references(self) -> Config:
        problems = list(self._find_reference_problems())
        if problems:
            raise ValueError("\n".join(problems))
        return self

    def _find_reference_problems(self):
        devices = self.devices

        seen_addresses: dict[int, str] = {}
        for name, dev in devices.items():
            if dev.address in seen_addresses:
                yield (
                    f"devices.{name}.address: alamat {dev.address} sudah dipakai "
                    f"devices.{seen_addresses[dev.address]}"
                )
            seen_addresses.setdefault(dev.address, name)

        for name, logical in self.logical_devices.items():
            for src in logical.sources:
                if src not in devices and src not in NON_MODBUS_SOURCES:
                    yield f"logical_devices.{name}.sources: '{src}' tidak ada di devices"

        seen_codes: dict[str, str] = {}
        seen_channels: dict[tuple[str, int], str] = {}
        for name, out in self.outputs.items():
            dev = devices.get(out.device)
            if dev is None:
                yield f"outputs.{name}.device: '{out.device}' tidak ada di devices"
            elif dev.profile != "relay_mb":
                yield f"outputs.{name}.device: '{out.device}' bukan relay (profil {dev.profile})"
            elif dev.channels is not None and out.ch >= dev.channels:
                yield (
                    f"outputs.{name}.ch: kanal {out.ch} di luar relay '{out.device}' "
                    f"({dev.channels} kanal, R0–R{dev.channels - 1})"
                )
            if out.actuator_code in seen_codes:
                yield (
                    f"outputs.{name}.actuator_code: '{out.actuator_code}' sudah dipakai "
                    f"{seen_codes[out.actuator_code]}"
                )
            seen_codes.setdefault(out.actuator_code, f"outputs.{name}")
            key = (out.device, out.ch)
            if key in seen_channels:
                yield f"outputs.{name}.ch: kanal sudah dipakai outputs.{seen_channels[key]}"
            seen_channels.setdefault(key, name)

        for kind, virtual in self.virtual_actuators.items():
            where = f"virtual_actuators.{kind}"
            if virtual.device not in self.logical_devices:
                yield f"{where}.device: '{virtual.device}' tidak ada di logical_devices"
            if virtual.actuator_code in seen_codes:
                yield (
                    f"{where}.actuator_code: '{virtual.actuator_code}' sudah dipakai "
                    f"{seen_codes[virtual.actuator_code]}"
                )
            seen_codes.setdefault(virtual.actuator_code, where)
            if kind in self.outputs:
                yield f"{where}: nama bentrok dengan nama output"
            if kind.startswith("trolley_") and self.safety.travel is None:
                yield f"{where}: butuh safety.travel (modul trolley)"

        float_dev = devices.get(self.inputs.float_low_ok.device)
        if float_dev is None or float_dev.profile != "relay_mb":
            yield (
                f"inputs.float_low_ok.device: '{self.inputs.float_low_ok.device}' "
                "harus perangkat relay di devices"
            )
        elif float_dev.relay is not None and float_dev.relay.input_fc is None:
            yield (
                f"devices.{self.inputs.float_low_ok.device}.relay: input_fc/input_base wajib "
                "diisi karena dipakai inputs.float_low_ok"
            )

        for group, members in self.safety.output_groups.items():
            if group in self.outputs:
                yield f"safety.output_groups.{group}: nama kelompok bentrok dengan nama output"
            for member in members:
                if member not in self.outputs:
                    yield f"safety.output_groups.{group}: '{member}' tidak ada di outputs"

        known = set(self.outputs) | set(self.safety.output_groups)
        named_lists = {
            "safety.blocked_on_backup_power": self.safety.blocked_on_backup_power,
            "safety.blocked_on_float_low": self.safety.blocked_on_float_low,
        }
        for i, group in enumerate(self.safety.exclusive_groups):
            named_lists[f"safety.exclusive_groups[{i}]"] = group
        for key, blockers in self.safety.blocked_while_on.items():
            if key not in self.outputs:
                yield f"safety.blocked_while_on: '{key}' tidak ada di outputs"
            named_lists[f"safety.blocked_while_on.{key}"] = blockers
        travel = self.safety.travel
        if travel is not None:
            for key in ("run", "direction"):
                if getattr(travel, key) not in self.outputs:
                    yield f"safety.travel.{key}: '{getattr(travel, key)}' tidak ada di outputs"
        for where, names in named_lists.items():
            for item in names:
                if item not in known:
                    yield f"{where}: '{item}' bukan nama output atau kelompok"

        power = self.power
        for key in ("pzem_device", "reference_device"):
            if getattr(power, key) not in devices:
                yield f"power.{key}: '{getattr(power, key)}' tidak ada di devices"
        if power.pzem_device in devices and devices[power.pzem_device].profile != "pzem016":
            yield f"power.pzem_device: '{power.pzem_device}' bukan PZEM-016"
        if power.reference_device == power.pzem_device:
            yield "power.reference_device: harus perangkat lain selain PZEM (§7.4)"
        if power.pump_output is not None and power.pump_output not in self.outputs:
            yield f"power.pump_output: '{power.pump_output}' tidak ada di outputs"

        for key in ("ec", "ph"):
            ch_cfg = getattr(self.analog, key)
            if ch_cfg.device not in devices:
                yield f"analog.{key}.device: '{ch_cfg.device}' tidak ada di devices"
            elif devices[ch_cfg.device].profile != "adc_7ch":
                yield f"analog.{key}.device: '{ch_cfg.device}' bukan ADC (profil adc_7ch)"
            elif not devices[ch_cfg.device].metric_prefix:
                yield f"devices.{ch_cfg.device}.metric_prefix: wajib diisi untuk analog.{key}"
        if self.analog.power_output not in self.outputs:
            yield f"analog.power_output: '{self.analog.power_output}' tidak ada di outputs"
        if self.camera.pan_output not in self.outputs:
            yield f"camera.pan_output: '{self.camera.pan_output}' tidak ada di outputs"
        fake = self.fake
        for name in [*fake.dose_pumps, *([fake.stirrer_output] if fake.stirrer_output else [])]:
            if name not in self.outputs:
                yield f"fake: output '{name}' tidak ada di outputs"

        temp_dev = devices.get(self.analog.temperature_device)
        if temp_dev is None or temp_dev.profile != "ds18b20_mb":
            yield (
                f"analog.temperature_device: '{self.analog.temperature_device}' "
                "harus perangkat ds18b20_mb di devices"
            )

    # Pencarian yang sering dipakai modul lain.
    def output_by_actuator(self, actuator_code: str) -> str | None:
        for name, out in self.outputs.items():
            if out.actuator_code == actuator_code:
                return name
        return None

    def virtual_by_actuator(self, actuator_code: str) -> str | None:
        for kind, virtual in self.virtual_actuators.items():
            if virtual.actuator_code == actuator_code:
                return kind
        return None


# --------------------------------------------------------------------------------------------
# Pemuat
# --------------------------------------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader yang menolak kunci ganda (PyYAML diam-diam memakai yang terakhir)."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"kunci ganda '{key}'", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _format_errors(err: ValidationError) -> str:
    lines = []
    for item in err.errors():
        loc = ".".join(str(p) for p in item["loc"])
        msg = item["msg"].removeprefix("Value error, ")
        if loc:
            lines.append(f"- {loc}: {msg}")
        else:
            lines.extend(f"- {part}" for part in msg.splitlines())
    return "\n".join(lines)


def parse_config(data: Any, source: str = "<config>") -> Config:
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: isi config harus berupa mapping YAML")
    try:
        return Config.model_validate(data)
    except ValidationError as err:
        raise ConfigError(f"{source} tidak valid:\n{_format_errors(err)}") from None


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ConfigError(f"{path}: tidak bisa dibaca ({err.strerror or err})") from None
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as err:
        raise ConfigError(f"{path}: YAML rusak: {err}") from None
    return parse_config(data, str(path))
