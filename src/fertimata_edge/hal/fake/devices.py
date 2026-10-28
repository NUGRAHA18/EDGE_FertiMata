"""Perangkat Modbus palsu untuk simulator.

Setiap perangkat menyimpan empat tabel Modbus (coil, discrete input, holding, input register).
Perilaku default-nya memori biasa: yang ditulis bisa dibaca lagi. Subkelas menambah perilaku
fisik (relay, PZEM, XY-MD02). Peta register perangkat yang belum terverifikasi (tanah, cahaya,
DS18B20, ADC) sengaja tidak ditebak di sini; test atau config F3 mengisinya lewat `set_*`.
"""

from __future__ import annotations

from collections.abc import Callable

from ...config import RelayMapConfig
from ..base import ModbusDeviceError

# Kode exception Modbus standar (spesifikasi Modbus Application Protocol).
ILLEGAL_FUNCTION = 1
ILLEGAL_DATA_ADDRESS = 2
ILLEGAL_DATA_VALUE = 3


class FakeModbusDevice:
    def __init__(self, unit: int) -> None:
        self.unit = unit
        self.online = True
        self.coils: dict[int, bool] = {}
        self.discrete_inputs: dict[int, bool] = {}
        self.holding: dict[int, int] = {}
        self.input_regs: dict[int, int] = {}
        # Bila True, alamat yang belum pernah diisi dijawab exception (seperti perangkat nyata).
        self.strict_addresses = False

    # --- dipanggil FakeModbusBus -------------------------------------------------------------

    def read_coils(self, address: int, count: int) -> list[bool]:
        return self._read(self.coils, address, count, False)

    def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        return self._read(self.discrete_inputs, address, count, False)

    def read_holding_registers(self, address: int, count: int) -> list[int]:
        return self._read(self.holding, address, count, 0)

    def read_input_registers(self, address: int, count: int) -> list[int]:
        return self._read(self.input_regs, address, count, 0)

    def write_coil(self, address: int, value: bool) -> None:
        self.coils[address] = bool(value)

    def write_register(self, address: int, value: int) -> None:
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"nilai register di luar 16 bit: {value}")
        self.holding[address] = value

    # --- dipanggil simulator ------------------------------------------------------------------

    def step(self, dt: float) -> None:
        """Perbarui nilai simulasi. Default: tidak ada."""

    def _read(self, table: dict, address: int, count: int, default):
        if self.strict_addresses and any(a not in table for a in range(address, address + count)):
            raise ModbusDeviceError(self.unit, ILLEGAL_DATA_ADDRESS)
        return [table.get(a, default) for a in range(address, address + count)]


class FakeRelayModule(FakeModbusDevice):
    """Relay Modbus palsu dengan state per kanal dan input digital (IN1 …).

    Menjawab sesuai peta `relay` di config (profil A coil / B holding, P-H4), sehingga profil
    yang salah ketik di config gagal juga di laptop: fungsi yang tidak dipakai profil dijawab
    exception ILLEGAL_FUNCTION, alamat di luar kanal dijawab ILLEGAL_DATA_ADDRESS.
    """

    def __init__(
        self, unit: int, channels: int, relay_map: RelayMapConfig, inputs: int = 0
    ) -> None:
        super().__init__(unit)
        self.channels = channels
        self.map = relay_map
        self.state = [False] * channels
        self.inputs = [False] * inputs
        # Kanal yang "macet": tulisan diterima tapi state fisik tidak berubah (uji S19).
        self.stuck: dict[int, bool] = {}
        self.on_change: list[Callable[[int, bool], None]] = []

    def channel(self, ch: int) -> bool:
        return self.state[ch]

    def set_input(self, number: int, closed: bool) -> None:
        self.inputs[number - 1] = closed

    def force_channel(self, ch: int, on: bool) -> None:
        """Ubah state tanpa lewat bus (mis. relay ON sisa proses sebelumnya, uji S1)."""
        self._apply(ch, on)

    # --- tulis --------------------------------------------------------------------------------

    def write_coil(self, address: int, value: bool) -> None:
        if self.map.write_fc != 5:
            raise ModbusDeviceError(self.unit, ILLEGAL_FUNCTION)
        self._apply(self._write_channel(address), bool(value))

    def write_register(self, address: int, value: int) -> None:
        if self.map.write_fc != 6:
            raise ModbusDeviceError(self.unit, ILLEGAL_FUNCTION)
        ch = self._write_channel(address)
        if value == self.map.on_value:
            self._apply(ch, True)
        elif value == self.map.off_value:
            self._apply(ch, False)
        else:
            raise ModbusDeviceError(self.unit, ILLEGAL_DATA_VALUE)

    def _write_channel(self, address: int) -> int:
        ch = address - self.map.write_base
        if not 0 <= ch < self.channels:
            raise ModbusDeviceError(self.unit, ILLEGAL_DATA_ADDRESS)
        return ch

    def _apply(self, ch: int, value: bool) -> None:
        if ch in self.stuck:
            value = self.stuck[ch]
        before = self.state[ch]
        self.state[ch] = value
        if before != value:
            for callback in self.on_change:
                callback(ch, value)

    # --- baca ---------------------------------------------------------------------------------

    def _table(self, fc: int) -> dict[int, bool]:
        table: dict[int, bool] = {}
        if self.map.read_fc == fc:
            table.update({self.map.read_base + i: v for i, v in enumerate(self.state)})
        if self.map.input_fc == fc and self.map.input_base is not None:
            table.update({self.map.input_base + i: v for i, v in enumerate(self.inputs)})
        if not table:
            raise ModbusDeviceError(self.unit, ILLEGAL_FUNCTION)
        return table

    def _read_bits(self, fc: int, address: int, count: int) -> list[bool]:
        table = self._table(fc)
        if any(a not in table for a in range(address, address + count)):
            raise ModbusDeviceError(self.unit, ILLEGAL_DATA_ADDRESS)
        return [table[a] for a in range(address, address + count)]

    def read_coils(self, address: int, count: int) -> list[bool]:
        return self._read_bits(1, address, count)

    def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        return self._read_bits(2, address, count)

    def read_holding_registers(self, address: int, count: int) -> list[int]:
        return [int(v) for v in self._read_bits(3, address, count)]

    def read_input_registers(self, address: int, count: int) -> list[int]:
        return [int(v) for v in self._read_bits(4, address, count)]


def _split32(value: int) -> tuple[int, int]:
    value &= 0xFFFFFFFF
    return value & 0xFFFF, (value >> 16) & 0xFFFF


class FakePzem016(FakeModbusDevice):
    """PZEM-016 palsu. Peta register terverifikasi dari manual Peacefair (Arsitektur §4.5).

    Sisi ukur PZEM ditenagai PLN, jadi saat `mains_on` False perangkat ini diam (§7.4).
    """

    def __init__(self, unit: int, voltage_v: float, current_fn: Callable[[], float]) -> None:
        super().__init__(unit)
        self.mains_on = True
        self.voltage_v = voltage_v
        self.frequency_hz = 50.0
        self.power_factor = 0.95
        self.energy_wh = 0.0
        self._current_fn = current_fn
        self._refresh()

    @property
    def responding(self) -> bool:
        return self.online and self.mains_on

    def step(self, dt: float) -> None:
        if self.mains_on:
            self.energy_wh += self.power_w * dt / 3600.0
        self._refresh()

    @property
    def current_a(self) -> float:
        return self._current_fn() if self.mains_on else 0.0

    @property
    def power_w(self) -> float:
        return self.voltage_v * self.current_a * self.power_factor

    def _refresh(self) -> None:
        current_lo, current_hi = _split32(round(self.current_a * 1000))
        power_lo, power_hi = _split32(round(self.power_w * 10))
        energy_lo, energy_hi = _split32(int(self.energy_wh))
        self.input_regs.update(
            {
                0x0000: round(self.voltage_v * 10) & 0xFFFF,
                0x0001: current_lo,
                0x0002: current_hi,
                0x0003: power_lo,
                0x0004: power_hi,
                0x0005: energy_lo,
                0x0006: energy_hi,
                0x0007: round(self.frequency_hz * 10) & 0xFFFF,
                0x0008: round(self.power_factor * 100) & 0xFFFF,
                0x0009: 0x0000,
            }
        )


class FakeXyMd02(FakeModbusDevice):
    """SHT40 XY-MD02 palsu: suhu int16 bertanda dan RH, satuan 0,1 (Arsitektur §4.5)."""

    def __init__(self, unit: int, temperature_c: float, humidity_pct: float) -> None:
        super().__init__(unit)
        self.temperature_c = temperature_c
        self.humidity_pct = humidity_pct
        self._refresh()

    def step(self, dt: float) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.input_regs[0x0001] = round(self.temperature_c * 10) & 0xFFFF
        self.input_regs[0x0002] = round(self.humidity_pct * 10) & 0xFFFF
