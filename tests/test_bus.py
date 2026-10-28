from __future__ import annotations

import asyncio

import pytest

from fertimata_edge.bus import BusManager, Priority
from fertimata_edge.hal import BusUnavailable, ModbusDeviceError, ModbusTimeout

from .helpers import drive


@pytest.fixture
async def bus(config, hal, clock):
    manager = BusManager(hal.bus, config, clock)
    await manager.start()
    yield manager
    await manager.stop()


async def test_owns_and_opens_port(bus, hal):
    assert hal.bus.is_open and bus.bus_available is True


async def test_read_and_write_by_device_name(bus, hal, clock):
    await drive(clock, bus.write_coil("relay_t", 4, True))
    assert hal.world.output_on("stirrer")
    assert await drive(clock, bus.read_coils("relay_t", 4, 1)) == [True]
    with pytest.raises(KeyError):
        await bus.read_coils("tidak_ada", 0, 1)


async def test_gap_between_transactions(bus, hal, clock, config):
    async def two_reads():
        await bus.read_coils("relay_p", 0, 1)
        first = clock.monotonic()
        await bus.read_coils("relay_p", 0, 1)
        return clock.monotonic() - first

    gap = await drive(clock, two_reads(), step=0.001)
    assert gap >= config.bus.gap_ms / 1000 - 1e-9


async def test_retries_then_marks_device_silent(bus, hal, clock, config):
    changes = []
    bus.device_listeners.append(lambda dev, ok: changes.append((dev, ok)))
    hal.world.set_device_online("air", False)
    attempts_per_request = 1 + config.bus.retries
    for i in range(config.bus.fail_threshold):
        with pytest.raises(ModbusTimeout):
            await drive(clock, bus.read_input_registers("air", 1, 2))
        expected = None if i < config.bus.fail_threshold - 1 else False
        assert bus.is_responding("air") is expected
    tries = [t for t in hal.bus.log if t.unit == 3]
    assert len(tries) == attempts_per_request * config.bus.fail_threshold
    assert changes == [("air", False)]

    hal.world.set_device_online("air", True)
    await drive(clock, bus.read_input_registers("air", 1, 2))
    assert bus.is_responding("air") is True
    assert changes == [("air", False), ("air", True)]


async def test_device_exception_counts_as_answer(bus, hal, clock):
    hal.world.by_name["adc"].strict_addresses = True
    with pytest.raises(ModbusDeviceError):
        await drive(clock, bus.read_holding_registers("adc", 0x0100, 1))
    assert bus.is_responding("adc") is True
    assert len([t for t in hal.bus.log if t.unit == 10]) == 1  # exception tidak diulang


async def test_s3_stop_goes_before_queued_polling(bus, hal, clock):
    """S3: STOP melompati antrean polling yang penuh."""
    polls = [asyncio.ensure_future(bus.read_input_registers("pzem", 0, 1)) for _ in range(5)]
    stop = asyncio.ensure_future(bus.write_coil("relay_p", 2, False, priority=Priority.STOP))
    await drive(clock, asyncio.gather(stop, *polls))
    ops = [(t.op, t.unit) for t in hal.bus.log]
    # Satu baca PZEM mungkin sudah berjalan saat STOP masuk; STOP harus tepat sesudahnya.
    assert ops.index(("write_coil", 20)) <= 1


async def test_s14_polling_paused_commands_still_run(bus, hal, clock):
    """S14: selama trolley bergerak polling ditunda, perintah dan STOP tetap jalan."""
    bus.pause_polling()
    poll = asyncio.ensure_future(bus.read_input_registers("pzem", 0, 1))
    await drive(clock, bus.write_coil("relay_p", 3, True))
    await drive(clock, bus.write_coil("relay_p", 2, False, priority=Priority.STOP))
    for _ in range(20):
        await clock.advance(0.01)
    assert not poll.done()
    assert [t.unit for t in hal.bus.log] == [20, 20]
    bus.resume_polling()
    await drive(clock, poll)
    assert hal.bus.log[-1].unit == 30


async def test_cancelled_request_is_skipped(bus, hal, clock):
    bus.pause_polling()
    poll = asyncio.ensure_future(bus.read_input_registers("pzem", 0, 1))
    await asyncio.sleep(0)
    poll.cancel()
    bus.resume_polling()
    await drive(clock, bus.read_coils("relay_p", 0, 1))
    assert all(t.unit != 30 for t in hal.bus.log)


async def test_port_lost_and_reopened(bus, hal, clock, config):
    states = []
    bus.bus_listeners.append(states.append)
    hal.world.port_present = False
    with pytest.raises(BusUnavailable):
        await drive(clock, bus.read_coils("relay_p", 0, 1))
    assert bus.bus_available is False
    # Sebelum reopen_s lewat, tidak mencoba membuka ulang.
    hal.world.port_present = True
    with pytest.raises(BusUnavailable):
        await drive(clock, bus.read_coils("relay_p", 0, 1))
    await clock.advance(config.bus.reopen_s)
    assert await drive(clock, bus.read_coils("relay_p", 0, 1)) == [False]
    assert states == [False, True]


async def test_missing_port_at_start_does_not_crash(config, hal, clock):
    hal.world.port_present = False
    manager = BusManager(hal.bus, config, clock)
    await manager.start()
    assert manager.bus_available is False
    await manager.stop()


async def test_stop_fails_pending_requests(config, hal, clock):
    manager = BusManager(hal.bus, config, clock)
    await manager.start()
    manager.pause_polling()
    poll = asyncio.ensure_future(manager.read_input_registers("pzem", 0, 1))
    await asyncio.sleep(0)
    await manager.stop()
    with pytest.raises(BusUnavailable):
        await poll
