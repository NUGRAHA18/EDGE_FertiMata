"""Profil `relay_mb`: tulis/baca kanal relay Modbus menurut `devices.<nama>.relay` (P-H4).

Profil A (coil) dan B (holding register) sama-sama didukung; mana yang benar untuk modul yang
dibeli belum terverifikasi, jadi pilihannya hanya ada di config (Arsitektur §4.5, §15 no. 1).
Galat bus (`ModbusTimeout`, `ModbusDeviceError`, `BusUnavailable`) diteruskan ke pemanggil.
"""

from __future__ import annotations

from ..bus import BusManager, Priority
from ..config import DeviceConfig, RelayMapConfig


class RelayProfile:
    def __init__(self, name: str, device: DeviceConfig) -> None:
        if device.relay is None or device.channels is None:
            raise ValueError(f"{name}: bukan perangkat relay_mb yang lengkap")
        self.name = name
        self.device = device
        self.map: RelayMapConfig = device.relay
        self.channels = device.channels

    async def write(self, bus: BusManager, ch: int, on: bool, priority: Priority) -> None:
        self._check_channel(ch)
        address = self.map.write_base + ch
        if self.map.write_fc == 5:
            await bus.write_coil(self.name, address, on, priority=priority)
        else:
            value = self.map.on_value if on else self.map.off_value
            assert value is not None  # dijamin validasi config
            await bus.write_register(self.name, address, value, priority=priority)

    async def read_states(self, bus: BusManager, priority: Priority) -> list[bool]:
        """State semua kanal R0 … R(n-1), hasil baca balik dari modul."""
        return await self._read_bits(
            bus, self.map.read_fc, self.map.read_base, self.channels, priority
        )

    async def read_input(self, bus: BusManager, number: int, priority: Priority) -> bool:
        """Input digital IN`number` (mulai 1). True = kontak tertutup."""
        if self.map.input_fc is None or self.map.input_base is None:
            raise ValueError(f"{self.name}: peta input belum diisi di config")
        if number < 1:
            raise ValueError("nomor input mulai dari 1")
        address = self.map.input_base + number - 1
        [closed] = await self._read_bits(bus, self.map.input_fc, address, 1, priority)
        return closed

    def _check_channel(self, ch: int) -> None:
        if not 0 <= ch < self.channels:
            raise ValueError(f"{self.name}: kanal R{ch} di luar R0–R{self.channels - 1}")

    async def _read_bits(
        self, bus: BusManager, fc: int, address: int, count: int, priority: Priority
    ) -> list[bool]:
        if fc == 1:
            return list(await bus.read_coils(self.name, address, count, priority=priority))
        if fc == 2:
            bits = await bus.read_discrete_inputs(self.name, address, count, priority=priority)
            return list(bits)
        read = bus.read_holding_registers if fc == 3 else bus.read_input_registers
        words = await read(self.name, address, count, priority=priority)
        return [word != 0 for word in words]


def build_relay_profiles(devices: dict[str, DeviceConfig]) -> dict[str, RelayProfile]:
    return {
        name: RelayProfile(name, device)
        for name, device in devices.items()
        if device.profile == "relay_mb"
    }
