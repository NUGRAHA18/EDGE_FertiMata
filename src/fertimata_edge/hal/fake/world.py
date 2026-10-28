"""Simulator greenhouse untuk `agent.hal: fake`.

`FakeWorld` memegang semua perangkat palsu dan keadaan fisik yang menghubungkan mereka:
pompa distribusi menaikkan arus PZEM, relay RUN/DIR menggerakkan trolley, posisi trolley
menentukan reed/HOME/END, pompa pupuk menaikkan EC drum setelah teraduk (F9). Test memajukan
simulasi secara deterministik lewat `step(dt)`; agent di laptop menjalankan `run(clock)` sebagai
tugas latar.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable

from ...config import Config, DeviceConfig
from ...profiles.registers import encode
from ..base import Clock
from .devices import FakeModbusDevice, FakePzem016, FakeRelayModule, FakeXyMd02

log = logging.getLogger(__name__)

GPIO_LINES = ("reed", "home", "end")

# Nilai awal simulasi per nama field register, untuk perangkat yang peta registernya dari config
# (tanah, cahaya, DS18B20). Hanya agar dashboard terisi angka wajar.
SIM_VALUES = {
    "moisture": 34.5,
    "temperature": 27.8,
    "ec": 1250,
    "ph": 6.4,
    "nitrogen": 42,
    "phosphorus": 18,
    "potassium": 55,
    "illuminance": 18450,
    "water_temperature": 26.3,
}


class FakeTrolley:
    """Trolley di rel lurus. Posisi 0 = HOME (selatan), `rail_length_mm` = END (utara)."""

    def __init__(self, config: Config) -> None:
        trolley = config.trolley
        self.rail_length_mm = trolley.rail_length_mm
        self.speed_mm_s = trolley.speed_m_s * 1000.0
        self.magnets_mm = [
            trolley.first_station_mm + i * trolley.station_pitch_mm for i in range(trolley.stations)
        ]
        self.magnet_half_mm = config.fake.magnet_width_mm / 2
        self.position_mm = min(config.fake.start_position_mm, self.rail_length_mm)
        self.running = False
        self.north = False  # DIR ON = utara/END (§5.1)
        self.broken_wires: set[str] = set()

    def step(self, dt: float) -> None:
        if not self.running:
            return
        delta = self.speed_mm_s * dt * (1 if self.north else -1)
        # Ujung rel adalah batas mekanis; motor yang tidak dihentikan software hanya macet.
        self.position_mm = min(max(self.position_mm + delta, 0.0), self.rail_length_mm)

    def line_active(self, line: str) -> bool:
        if line == "reed":
            return any(abs(self.position_mm - m) <= self.magnet_half_mm for m in self.magnets_mm)
        # Limit memakai kontak NC: kabel putus terbaca sama dengan tertekan (§6.1).
        if line in self.broken_wires:
            return True
        if line == "home":
            return self.position_mm <= 0.0
        if line == "end":
            return self.position_mm >= self.rail_length_mm
        raise KeyError(line)

    def station_at(self) -> int | None:
        for index, magnet in enumerate(self.magnets_mm, start=1):
            if abs(self.position_mm - magnet) <= self.magnet_half_mm:
                return index
        return None


class FakeWorld:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.rng = random.Random(config.fake.seed)
        self.devices: dict[int, FakeModbusDevice] = {}
        self.by_name: dict[str, FakeModbusDevice] = {}
        self.port_present = True
        self.trolley = FakeTrolley(config)
        self.soc_temperature_c: float | None = config.fake.soc_temperature_c
        # Gangguan pompa untuk uji §7.5: SSR short (arus mengalir walau R0 OFF) dan pompa kering
        # (arus pompa hanya sebagian kecil nominal).
        self.ssr_shorted = False
        self.pump_dry = False
        self._gpio_listeners: list[Callable[[str, bool], None]] = []
        self._gpio_state = {line: self.trolley.line_active(line) for line in GPIO_LINES}

        for name, dev_cfg in config.devices.items():
            device = self._build_device(dev_cfg)
            if type(device) is FakeModbusDevice:
                for field_name, spec in dev_cfg.registers.items():
                    self.set_register_value(device, spec, SIM_VALUES.get(field_name, 1.0))
            self.devices[dev_cfg.address] = device
            self.by_name[name] = device

        float_cfg = config.inputs.float_low_ok
        float_relay = self.by_name[float_cfg.device]
        assert isinstance(float_relay, FakeRelayModule)
        float_relay.set_input(float_cfg.input, config.fake.float_low_ok)
        self._float_relay = float_relay

        # Larutan drum dan papan analog (§6.3): EC hanya keluar saat R5 ON, pH terganggu saat R5 ON.
        self.tank_ec_ms_cm = config.fake.tank_ec_ms_cm
        self.tank_ph = config.fake.tank_ph
        self.water_temperature_c = SIM_VALUES["water_temperature"]
        analog = config.analog
        if analog.power_output in config.outputs:
            self.relay_for(analog.power_output).on_change.append(
                lambda _ch, _v: self._update_analog()
            )
        self._update_analog()

        # Pompa pupuk menambah stok ke drum (perintah mentah DOSING-N/P/K); test boleh mengubah
        # debit untuk meniru stok habis atau selang tersumbat. Stok baru terbaca probe setelah
        # tercampur.
        fake = config.fake
        self.tank_volume_l = fake.tank_volume_l
        self.dose_flow_ml_s = {name: pump.flow_ml_s for name, pump in fake.dose_pumps.items()}
        self.dose_ec_gain = {
            name: pump.ec_gain_ms_cm_per_ml_l for name, pump in fake.dose_pumps.items()
        }
        self.dosed_ml: dict[str, float] = dict.fromkeys(self.dose_flow_ml_s, 0.0)
        self.unmixed_ec_ms_cm = 0.0

        for out_name in ("trolley_run", "trolley_dir"):
            if out_name in config.outputs:
                relay = self.relay_for(out_name)
                relay.on_change.append(lambda _ch, _v: self._sync_trolley())

    # --- konstruksi ---------------------------------------------------------------------------

    def _build_device(self, dev_cfg: DeviceConfig) -> FakeModbusDevice:
        fake = self.config.fake
        profile, unit = dev_cfg.profile, dev_cfg.address
        if profile == "relay_mb":
            assert dev_cfg.relay is not None and dev_cfg.channels is not None
            # Relay 8-ch tangki punya IN1 (§5.2); simulator memberi tiap relay beberapa input.
            return FakeRelayModule(unit, dev_cfg.channels, dev_cfg.relay, inputs=4)
        if profile == "pzem016":
            return FakePzem016(unit, fake.mains_voltage_v, self._panel_current)
        if profile == "xy_md02":
            return FakeXyMd02(unit, fake.air_temperature_c, fake.air_humidity_pct)
        return FakeModbusDevice(unit)

    # --- akses praktis untuk test dan simulasi ------------------------------------------------

    @staticmethod
    def set_register_value(device: FakeModbusDevice, spec, value: float) -> None:
        """Tulis nilai fisik ke register palsu sesuai `RegisterSpec` (kebalikan decode)."""
        table = device.input_regs if spec.fc == 4 else device.holding
        for offset, word in enumerate(encode(value, spec)):
            table[spec.address + offset] = word

    def relay_for(self, output_name: str) -> FakeRelayModule:
        out = self.config.outputs[output_name]
        relay = self.by_name[out.device]
        assert isinstance(relay, FakeRelayModule)
        return relay

    def output_on(self, output_name: str) -> bool:
        out = self.config.outputs[output_name]
        return self.relay_for(output_name).channel(out.ch)

    @property
    def pzem(self) -> FakePzem016 | None:
        for device in self.devices.values():
            if isinstance(device, FakePzem016):
                return device
        return None

    def set_mains(self, on: bool) -> None:
        """Simulasi PLN padam/pulih. PZEM ikut diam karena sisi ukurnya ditenagai PLN."""
        pzem = self.pzem
        if pzem is not None:
            pzem.mains_on = on
            pzem.step(0.0)
        log.info("simulasi: PLN %s", "menyala" if on else "padam")

    def set_device_online(self, name: str, online: bool) -> None:
        self.by_name[name].online = online

    def set_float_low_ok(self, closed: bool) -> None:
        self._float_relay.set_input(self.config.inputs.float_low_ok.input, closed)

    def break_limit_wire(self, line: str, broken: bool = True) -> None:
        if broken:
            self.trolley.broken_wires.add(line)
        else:
            self.trolley.broken_wires.discard(line)
        self._update_gpio()

    def add_gpio_listener(self, callback: Callable[[str, bool], None]) -> None:
        self._gpio_listeners.append(callback)

    def gpio_active(self, line: str) -> bool:
        return self._gpio_state[line]

    # --- fisika -------------------------------------------------------------------------------

    def _update_analog(self) -> None:
        cfg, fake = self.config, self.config.fake
        analog = cfg.analog
        temp_dev = cfg.devices.get(analog.temperature_device)
        if temp_dev is not None and "water_temperature" in temp_dev.registers:
            self.set_register_value(
                self.by_name[analog.temperature_device],
                temp_dev.registers["water_temperature"],
                self.water_temperature_c,
            )
        ec_power = analog.power_output in cfg.outputs and self.output_on(analog.power_output)
        ec_measured = self.tank_ec_ms_cm * (1 + analog.temp_coef * (self.water_temperature_c - 25))
        ph_mv = fake.ph_mv_at_7 + fake.ph_mv_per_ph * (self.tank_ph - 7)
        if ec_power:
            ph_mv += fake.ec_ph_interference_mv
        channels = {
            (analog.ec.device, analog.ec.ch): ec_measured * fake.ec_mv_per_ms_cm
            if ec_power
            else 0.0,
            (analog.ph.device, analog.ph.ch): ph_mv,
        }
        for (device, ch), mv in channels.items():
            spec = cfg.devices[device].registers.get(f"a{ch}")
            if spec is not None:
                self.set_register_value(self.by_name[device], spec, mv)

    def _panel_current(self) -> float:
        # CT PZEM membaca arus total panel (§7.5): baseline + pompa bila R0 ON.
        power = self.config.power
        current = power.pump_baseline_a or 0.0
        pump = power.pump_output
        if pump is not None and (self.output_on(pump) or self.ssr_shorted):
            current += (power.pump_nominal_a or 0.0) * (0.2 if self.pump_dry else 1.0)
        return current

    def _sync_trolley(self) -> None:
        outputs = self.config.outputs
        self.trolley.running = "trolley_run" in outputs and self.output_on("trolley_run")
        self.trolley.north = "trolley_dir" in outputs and self.output_on("trolley_dir")

    def step(self, dt: float) -> None:
        self._sync_trolley()
        # Sub-langkah supaya magnet selebar beberapa mm tidak terlewati pada dt besar.
        max_sub = self.trolley.magnet_half_mm / self.trolley.speed_mm_s
        remaining = dt
        while remaining > 0:
            sub = min(remaining, max_sub)
            self.trolley.step(sub)
            self._update_gpio()
            remaining -= sub
        self._update_gpio()  # juga menangkap posisi yang diubah langsung oleh test
        self._drift_sensors(dt)
        self._dose_tank(dt)
        self._update_analog()
        for device in self.devices.values():
            device.step(dt)

    def _dose_tank(self, dt: float) -> None:
        cfg = self.config
        for name, flow in self.dose_flow_ml_s.items():
            if name in cfg.outputs and self.output_on(name):
                ml = flow * dt
                self.dosed_ml[name] += ml
                if self.tank_volume_l > 0:
                    self.unmixed_ec_ms_cm += ml * self.dose_ec_gain[name] / self.tank_volume_l
        stirrer = cfg.fake.stirrer_output
        stirring = stirrer is not None and stirrer in cfg.outputs and self.output_on(stirrer)
        tau = cfg.fake.mix_tau_s if stirring else cfg.fake.mix_idle_tau_s
        moved = self.unmixed_ec_ms_cm * min(1.0, dt / tau)
        self.tank_ec_ms_cm += moved
        self.unmixed_ec_ms_cm -= moved

    def _drift_sensors(self, dt: float) -> None:
        # Nilai berubah pelan supaya grafik dashboard tidak datar.
        for device in self.devices.values():
            if isinstance(device, FakeXyMd02):
                device.temperature_c += self.rng.gauss(0, 0.02) * dt
                device.humidity_pct = min(
                    100.0, max(0.0, device.humidity_pct + self.rng.gauss(0, 0.05) * dt)
                )
            elif isinstance(device, FakePzem016) and device.mains_on:
                base = self.config.fake.mains_voltage_v
                device.voltage_v += (base - device.voltage_v) * 0.1 * dt + self.rng.gauss(
                    0, 0.2
                ) * dt

    def _update_gpio(self) -> None:
        for line in GPIO_LINES:
            active = self.trolley.line_active(line)
            if active != self._gpio_state[line]:
                self._gpio_state[line] = active
                for callback in self._gpio_listeners:
                    callback(line, active)

    async def run(self, clock: Clock) -> None:
        tick = self.config.fake.tick_s
        last = clock.monotonic()
        while True:
            await clock.sleep(tick)
            now = clock.monotonic()
            self.step(now - last)
            last = now
