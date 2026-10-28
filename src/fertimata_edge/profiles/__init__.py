"""Profil perangkat Modbus: dari register mentah ke metrik bersatuan (Arsitektur §4.5).

Profil sensor membaca peta register gabungan: bawaan profil (hanya untuk yang terverifikasi di
manual) ditimpa/ditambah `devices.<nama>.registers` di config. Profil yang belum terverifikasi
tidak punya bawaan, jadi tidak di-poll sampai peta registernya diisi di config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..bus import BusManager, Priority
from ..config import Config, DeviceConfig, RegisterSpec
from ..hal import BusUnavailable, ModbusDeviceError, ModbusTimeout
from ..logs import fields
from .registers import decode, encode, read_blocks

log = logging.getLogger(__name__)

# PZEM-016: terverifikasi dari manual Peacefair PZEM-014/016 (Arsitektur §4.5).
# Energi 1 Wh → katalog memakai kWh, jadi scale 0,001 (Kontrak §3).
PZEM016_REGISTERS = {
    "voltage": RegisterSpec(fc=4, address=0x0000, scale=0.1),
    "current": RegisterSpec(fc=4, address=0x0001, type="u32", word_order="lo_hi", scale=0.001),
    "power": RegisterSpec(fc=4, address=0x0003, type="u32", word_order="lo_hi", scale=0.1),
    "energy": RegisterSpec(fc=4, address=0x0005, type="u32", word_order="lo_hi", scale=0.001),
    "frequency": RegisterSpec(fc=4, address=0x0007, scale=0.1),
    "power_factor": RegisterSpec(fc=4, address=0x0008, scale=0.01),
}

# SHT40 XY-MD02: pola keluarga XY-MD02 (Arsitektur §4.5), suhu int16 bertanda.
XY_MD02_REGISTERS = {
    "temperature": RegisterSpec(fc=4, address=0x0001, type="s16", scale=0.1),
    "humidity": RegisterSpec(fc=4, address=0x0002, scale=0.1),
}

DEFAULT_REGISTERS: dict[str, dict[str, RegisterSpec]] = {
    "pzem016": PZEM016_REGISTERS,
    "xy_md02": XY_MD02_REGISTERS,
    # soil_7in1 (P-H5), lux_rs485 (P-H6), ds18b20_mb (P-H7): sengaja kosong, hanya dari config.
}

# Batas jumlah register per transaksi baca (spesifikasi Modbus mengizinkan 125).
MAX_BLOCK = 32


@dataclass
class ReadResult:
    values: dict[str, float] = field(default_factory=dict)  # kunci metrik lengkap → nilai
    errors: list[str] = field(default_factory=list)  # blok yang gagal, untuk log

    @property
    def ok(self) -> bool:
        return not self.errors


class RegisterMapProfile:
    """Profil sensor generik berbasis peta register."""

    def __init__(self, name: str, device: DeviceConfig) -> None:
        self.name = name
        self.device = device
        self.registers = {**DEFAULT_REGISTERS.get(device.profile, {}), **device.registers}
        self.blocks = read_blocks(self.registers, MAX_BLOCK)

    @property
    def configured(self) -> bool:
        return bool(self.registers)

    def metric(self, field_name: str) -> str:
        return f"{self.device.metric_prefix}.{field_name}"

    async def read(self, bus: BusManager, priority: Priority = Priority.POLL) -> ReadResult:
        """Baca semua blok. Blok yang gagal menghilangkan metriknya saja (Kontrak §4.1)."""
        result = ReadResult()
        fields_, result.errors = await self._read_specs(bus, self.registers, self.blocks, priority)
        result.values = {self.metric(name): value for name, value in fields_.items()}
        return result

    async def read_fields(
        self, bus: BusManager, names: list[str], priority: Priority = Priority.POLL
    ) -> dict[str, float]:
        """Baca sebagian field (mis. satu kanal ADC). Field gagal/tak dipetakan dihilangkan."""
        specs = {n: self.registers[n] for n in names if n in self.registers}
        if not specs:
            return {}
        values, errors = await self._read_specs(bus, specs, read_blocks(specs, MAX_BLOCK), priority)
        if errors:
            log.debug("baca gagal", extra=fields(device=self.name, errors="; ".join(errors)))
        return values

    async def _read_specs(
        self,
        bus: BusManager,
        specs: dict[str, RegisterSpec],
        blocks: list[tuple[int, int, int]],
        priority: Priority,
    ) -> tuple[dict[str, float], list[str]]:
        errors: list[str] = []
        words: dict[tuple[int, int], int] = {}
        for fc, start, count in blocks:
            read = bus.read_input_registers if fc == 4 else bus.read_holding_registers
            try:
                block = await read(self.name, start, count, priority=priority)
            except (ModbusTimeout, ModbusDeviceError, BusUnavailable) as err:
                errors.append(f"fc{fc}@{start:#06x}x{count}: {err}")
                continue
            for offset, word in enumerate(block):
                words[(fc, start + offset)] = word
        values: dict[str, float] = {}
        for field_name, spec in specs.items():
            raw = [words.get((spec.fc, spec.address + i)) for i in range(spec.width)]
            if any(w is None for w in raw):
                continue
            values[field_name] = decode(raw, spec)  # type: ignore[arg-type]
        return values, errors


def build_sensor_profiles(config: Config) -> dict[str, RegisterMapProfile]:
    """Profil untuk setiap perangkat sensor yang di-poll terjadwal dan peta registernya lengkap.

    Sensor suhu larutan tidak termasuk: dibaca di siklus analog (§6.3) bersama EC/pH.
    """
    profiles: dict[str, RegisterMapProfile] = {}
    for name, device in config.devices.items():
        if device.metric_prefix is None or device.profile in ("relay_mb", "adc_7ch"):
            continue
        if name == config.analog.temperature_device:
            continue
        profile = RegisterMapProfile(name, device)
        if not profile.configured:
            log.warning(
                "%s (%s) belum punya peta register di config, tidak di-poll", name, device.profile
            )
            continue
        profiles[name] = profile
    return profiles


__all__ = [
    "DEFAULT_REGISTERS",
    "PZEM016_REGISTERS",
    "XY_MD02_REGISTERS",
    "ReadResult",
    "RegisterMapProfile",
    "build_sensor_profiles",
    "decode",
    "encode",
    "read_blocks",
]
