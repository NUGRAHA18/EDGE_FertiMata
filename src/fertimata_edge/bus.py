"""Bus manager: satu-satunya pemilik `/dev/rs485` (Arsitektur §4.6 aturan 1).

- Antrean berprioritas STOP > perintah > polling. STOP tidak bisa memotong transaksi yang sedang
  berjalan (maks. satu timeout), tetapi selalu menjadi transaksi berikutnya (S3).
- Jeda ≥ `bus.gap_ms` antar transaksi, timeout per perangkat, ulang `bus.retries` kali
  (§4.6 aturan 2).
- Perangkat dinyatakan "tidak menjawab" setelah `bus.fail_threshold` permintaan gagal
  berturut-turut, dan kembali "menjawab" pada jawaban pertama berikutnya.
- Polling bisa dijeda selama trolley bergerak; STOP dan perintah tetap jalan (S14, §9.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .config import Config
from .hal import BusUnavailable, Clock, ModbusBus, ModbusDeviceError, ModbusTimeout
from .logs import fields

log = logging.getLogger(__name__)


class Priority(IntEnum):
    STOP = 0
    COMMAND = 1
    POLL = 2


@dataclass
class DeviceState:
    responding: bool | None = None  # None = belum pernah dicoba
    failures: int = 0  # permintaan gagal berturut-turut (setelah semua retry)


@dataclass
class _Request:
    priority: Priority
    device: str
    op: str
    address: int
    arg: Any  # jumlah (baca) atau nilai (tulis)
    future: asyncio.Future[Any] = field(repr=False)


DeviceListener = Callable[[str, bool], None]
BusListener = Callable[[bool], None]


class BusManager:
    def __init__(self, bus: ModbusBus, config: Config, clock: Clock) -> None:
        self._bus = bus
        self._config = config
        self._clock = clock
        self._queues: dict[Priority, deque[_Request]] = {p: deque() for p in Priority}
        self._wake = asyncio.Event()
        self._polling_paused = False
        self._last_tx = -math.inf
        self._last_reopen = -math.inf
        self._bus_available: bool | None = None
        self._worker: asyncio.Task[None] | None = None
        self.states: dict[str, DeviceState] = {name: DeviceState() for name in config.devices}
        self.device_listeners: list[DeviceListener] = []
        self.bus_listeners: list[BusListener] = []
        self.transactions = 0

    # --- siklus hidup -------------------------------------------------------------------------

    async def start(self) -> None:
        await self._try_open()
        self._worker = asyncio.create_task(self._run(), name="bus-manager")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        for queue in self._queues.values():
            while queue:
                req = queue.popleft()
                if not req.future.done():
                    req.future.set_exception(BusUnavailable("bus manager berhenti"))
        await self._bus.close()

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._worker

    # --- status -------------------------------------------------------------------------------

    @property
    def bus_available(self) -> bool | None:
        return self._bus_available

    def is_responding(self, device: str) -> bool | None:
        return self.states[device].responding

    @property
    def polling_paused(self) -> bool:
        return self._polling_paused

    def pause_polling(self) -> None:
        """Mode trolley (S14): permintaan POLL menunggu, STOP/perintah tetap jalan."""
        if not self._polling_paused:
            self._polling_paused = True
            log.info("polling dijeda (trolley bergerak)")

    def resume_polling(self) -> None:
        if self._polling_paused:
            self._polling_paused = False
            log.info("polling dilanjutkan")
            self._wake.set()

    def queued(self, priority: Priority) -> int:
        return len(self._queues[priority])

    # --- permintaan ---------------------------------------------------------------------------

    async def read_coils(self, device: str, address: int, count: int, *, priority=Priority.POLL):
        return await self._submit(priority, device, "read_coils", address, count)

    async def read_discrete_inputs(
        self, device: str, address: int, count: int, *, priority=Priority.POLL
    ):
        return await self._submit(priority, device, "read_discrete_inputs", address, count)

    async def read_holding_registers(
        self, device: str, address: int, count: int, *, priority=Priority.POLL
    ):
        return await self._submit(priority, device, "read_holding_registers", address, count)

    async def read_input_registers(
        self, device: str, address: int, count: int, *, priority=Priority.POLL
    ):
        return await self._submit(priority, device, "read_input_registers", address, count)

    async def write_coil(
        self, device: str, address: int, value: bool, *, priority=Priority.COMMAND
    ):
        return await self._submit(priority, device, "write_coil", address, value)

    async def write_register(
        self, device: str, address: int, value: int, *, priority=Priority.COMMAND
    ):
        return await self._submit(priority, device, "write_register", address, value)

    async def _submit(self, priority: Priority, device: str, op: str, address: int, arg: Any):
        if device not in self._config.devices:
            raise KeyError(f"perangkat tidak dikenal: {device}")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._queues[Priority(priority)].append(
            _Request(Priority(priority), device, op, address, arg, future)
        )
        self._wake.set()
        return await future

    # --- pekerja ------------------------------------------------------------------------------

    def _next(self) -> _Request | None:
        for priority in Priority:
            if priority is Priority.POLL and self._polling_paused:
                continue
            queue = self._queues[priority]
            while queue:
                req = queue.popleft()
                if not req.future.done():  # pemanggil sudah batal → lewati
                    return req
        return None

    async def _run(self) -> None:
        while True:
            req = self._next()
            if req is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            try:
                result = await self._execute(req)
            except (ModbusTimeout, ModbusDeviceError, BusUnavailable) as err:
                if not req.future.done():
                    req.future.set_exception(err)
            else:
                if not req.future.done():
                    req.future.set_result(result)

    async def _execute(self, req: _Request) -> Any:
        dev = self._config.devices[req.device]
        timeout_s = dev.timeout_s or self._config.bus.timeout_s
        if not self._bus_available and not await self._try_open():
            raise BusUnavailable("port RS485 tidak tersedia")
        attempts = 1 + self._config.bus.retries
        last_error: ModbusTimeout | None = None
        for _ in range(attempts):
            await self._gap()
            method = getattr(self._bus, req.op)
            try:
                result = await method(dev.address, req.address, req.arg, timeout_s=timeout_s)
            except ModbusTimeout as err:
                last_error = err
                continue
            except ModbusDeviceError:
                self._mark(req.device, True)  # perangkat menjawab, hanya menolak permintaan
                raise
            except BusUnavailable:
                self._set_bus_available(False)
                raise
            finally:
                self._last_tx = self._clock.monotonic()
                self.transactions += 1
            self._mark(req.device, True)
            return result
        self._mark(req.device, False)
        assert last_error is not None
        raise last_error

    async def _gap(self) -> None:
        gap = self._config.bus.gap_ms / 1000.0
        elapsed = self._clock.monotonic() - self._last_tx
        if elapsed < gap:
            await self._clock.sleep(gap - elapsed)

    async def _try_open(self) -> bool:
        now = self._clock.monotonic()
        if self._bus_available is False and now - self._last_reopen < self._config.bus.reopen_s:
            return False
        self._last_reopen = now
        try:
            await self._bus.open()
        except BusUnavailable as err:
            log.error("port RS485 tidak bisa dibuka", extra=fields(err=err))
            self._set_bus_available(False)
            return False
        self._set_bus_available(True)
        return True

    def _set_bus_available(self, available: bool) -> None:
        if self._bus_available == available:
            return
        self._bus_available = available
        log.log(
            logging.INFO if available else logging.ERROR,
            "bus RS485 %s",
            "tersedia" if available else "hilang",
        )
        for listener in self.bus_listeners:
            listener(available)

    def _mark(self, device: str, answered: bool) -> None:
        state = self.states[device]
        if answered:
            state.failures = 0
            changed = state.responding is not True
            state.responding = True
        else:
            state.failures += 1
            reached = state.failures >= self._config.bus.fail_threshold
            changed = reached and state.responding is not False
            if reached:
                state.responding = False
        if changed:
            log.log(
                logging.INFO if state.responding else logging.WARNING,
                "perangkat %s",
                "menjawab" if state.responding else "tidak menjawab",
                extra=fields(device=device, address=self._config.devices[device].address),
            )
            for listener in self.device_listeners:
                listener(device, bool(state.responding))
