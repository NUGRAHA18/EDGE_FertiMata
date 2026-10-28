"""Bus Modbus palsu di atas `FakeWorld`."""

from __future__ import annotations

from dataclasses import dataclass

from ..base import BusUnavailable, ModbusBus, ModbusTimeout
from .devices import FakeModbusDevice, FakePzem016
from .world import FakeWorld


@dataclass(frozen=True)
class Transaction:
    op: str
    unit: int
    address: int
    value: object  # jumlah register untuk baca, nilai untuk tulis
    ok: bool


class FakeModbusBus(ModbusBus):
    """Menjawab dari perangkat palsu. Perangkat yang mati/diam → `ModbusTimeout` seketika.

    Semua transaksi dicatat di `log` supaya test bisa memeriksa urutan tulis (mis. S12).
    """

    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.is_open = False
        self.log: list[Transaction] = []

    async def open(self) -> None:
        if not self.world.port_present:
            raise BusUnavailable("port RS485 palsu tidak ada")
        self.is_open = True

    async def close(self) -> None:
        self.is_open = False

    def _device(self, op: str, unit: int, address: int, value: object) -> FakeModbusDevice:
        if not self.is_open or not self.world.port_present:
            self.log.append(Transaction(op, unit, address, value, False))
            raise BusUnavailable("port RS485 palsu tertutup atau hilang")
        device = self.world.devices.get(unit)
        responding = device is not None and device.online
        if isinstance(device, FakePzem016):
            responding = device.responding
        if not responding:
            self.log.append(Transaction(op, unit, address, value, False))
            raise ModbusTimeout(f"perangkat {unit} tidak menjawab")
        self.log.append(Transaction(op, unit, address, value, True))
        assert device is not None
        return device

    async def read_coils(self, unit, address, count, *, timeout_s=None):
        return self._device("read_coils", unit, address, count).read_coils(address, count)

    async def read_discrete_inputs(self, unit, address, count, *, timeout_s=None):
        device = self._device("read_discrete_inputs", unit, address, count)
        return device.read_discrete_inputs(address, count)

    async def read_holding_registers(self, unit, address, count, *, timeout_s=None):
        device = self._device("read_holding_registers", unit, address, count)
        return device.read_holding_registers(address, count)

    async def read_input_registers(self, unit, address, count, *, timeout_s=None):
        device = self._device("read_input_registers", unit, address, count)
        return device.read_input_registers(address, count)

    async def write_coil(self, unit, address, value, *, timeout_s=None):
        self._device("write_coil", unit, address, value).write_coil(address, value)

    async def write_register(self, unit, address, value, *, timeout_s=None):
        self._device("write_register", unit, address, value).write_register(address, value)
