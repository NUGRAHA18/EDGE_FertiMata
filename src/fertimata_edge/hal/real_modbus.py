"""Bus Modbus RTU asli lewat pymodbus (Arsitektur §4.1).

Hanya modul `hal/real*` yang boleh mengimpor pymodbus. Retry diatur bus manager, jadi retry
internal pymodbus dimatikan. Timeout dipasang per transaksi: pymodbus membaca
`comm_params.timeout_connect` setiap kali menunggu jawaban, dan bus manager menjamin hanya ada
satu transaksi dalam satu waktu.
"""

from __future__ import annotations

import logging
from typing import Any

from pymodbus.client import AsyncModbusSerialClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

from .base import BusUnavailable, ModbusBus, ModbusDeviceError, ModbusTimeout

log = logging.getLogger(__name__)

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # galat dilaporkan lewat exception kita


class PymodbusBus(ModbusBus):
    def __init__(
        self,
        port: str,
        baudrate: int,
        parity: str,
        stopbits: int,
        timeout_s: float,
    ) -> None:
        self.port = port
        self.default_timeout_s = timeout_s
        self._client = AsyncModbusSerialClient(
            port,
            baudrate=baudrate,
            bytesize=8,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout_s,
            retries=0,
            reconnect_delay=0,  # membuka ulang diatur bus manager (bus.reopen_s)
        )

    async def open(self) -> None:
        try:
            connected = await self._client.connect()
        except Exception as err:  # pyserial bisa melempar banyak jenis galat OS
            raise BusUnavailable(f"{self.port}: {err}") from err
        if not connected:
            raise BusUnavailable(f"{self.port} tidak bisa dibuka")

    async def close(self) -> None:
        self._client.close()

    async def _call(self, unit: int, timeout_s: float | None, name: str, *args: Any, **kw: Any):
        if not self._client.connected:
            raise BusUnavailable(f"{self.port} tidak tersambung")
        self._client.ctx.comm_params.timeout_connect = timeout_s or self.default_timeout_s
        try:
            response = await getattr(self._client, name)(*args, device_id=unit, **kw)
        except ConnectionException as err:
            raise BusUnavailable(f"{self.port}: {err}") from err
        except ModbusIOException as err:
            raise ModbusTimeout(f"perangkat {unit} tidak menjawab") from err
        if response.isError():
            raise ModbusDeviceError(unit, getattr(response, "exception_code", 0))
        return response

    async def read_coils(self, unit, address, count, *, timeout_s=None):
        response = await self._call(unit, timeout_s, "read_coils", address, count=count)
        return list(response.bits[:count])

    async def read_discrete_inputs(self, unit, address, count, *, timeout_s=None):
        response = await self._call(unit, timeout_s, "read_discrete_inputs", address, count=count)
        return list(response.bits[:count])

    async def read_holding_registers(self, unit, address, count, *, timeout_s=None):
        response = await self._call(unit, timeout_s, "read_holding_registers", address, count=count)
        return list(response.registers)

    async def read_input_registers(self, unit, address, count, *, timeout_s=None):
        response = await self._call(unit, timeout_s, "read_input_registers", address, count=count)
        return list(response.registers)

    async def write_coil(self, unit, address, value, *, timeout_s=None):
        await self._call(unit, timeout_s, "write_coil", address, bool(value))

    async def write_register(self, unit, address, value, *, timeout_s=None):
        await self._call(unit, timeout_s, "write_register", address, int(value))
