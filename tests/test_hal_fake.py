from __future__ import annotations

import asyncio
import struct
import zlib

import pytest

from fertimata_edge.hal import (
    BusUnavailable,
    CameraError,
    ModbusDeviceError,
    ModbusTimeout,
    create_hal,
)
from fertimata_edge.hal.fake import FakeRelayModule, FakeWorld

from .conftest import make_config


def _u32(lo: int, hi: int) -> int:
    return lo | (hi << 16)


def _s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


async def _open(hal) -> None:
    # Di agent, bus dibuka oleh bus manager; test HAL membukanya sendiri.
    await hal.open()
    await hal.bus.open()


async def test_real_hal_uses_pymodbus_and_locks_trolley(dev_data):
    from fertimata_edge.hal.real_modbus import PymodbusBus

    dev_data["agent"]["hal"] = "real"
    dev_data["bus"]["port"] = "COM_TIDAK_ADA"
    hal = create_hal(make_config(dev_data))
    assert isinstance(hal.bus, PymodbusBus)
    with pytest.raises(BusUnavailable):
        await hal.bus.open()
    await hal.open()
    # Tanpa GPIO asli (F7) limit dianggap aktif seperti kabel putus: trolley terkunci.
    assert hal.gpio.is_active("home") and hal.gpio.is_active("end")
    assert not hal.gpio.is_active("reed")
    with pytest.raises(CameraError):
        await hal.camera.capture()
    await hal.close()


# --- bus Modbus ---------------------------------------------------------------------------------


async def test_bus_must_be_opened(hal):
    with pytest.raises(BusUnavailable):
        await hal.bus.read_coils(20, 0, 4)


async def test_relay_keeps_state_and_logs_transactions(hal, config):
    await _open(hal)
    relay = config.devices["relay_t"].address
    await hal.bus.write_coil(relay, 4, True)
    assert await hal.bus.read_coils(relay, 0, 8) == [False] * 4 + [True] + [False] * 3
    assert hal.world.output_on("stirrer")
    ops = [(t.op, t.unit, t.address, t.value, t.ok) for t in hal.bus.log]
    assert ops == [("write_coil", 21, 4, True, True), ("read_coils", 21, 0, 8, True)]


async def test_relay_rejects_channel_outside_module(hal):
    await _open(hal)
    with pytest.raises(ModbusDeviceError):
        await hal.bus.write_coil(20, 4, True)  # relay panel hanya R0–R3


async def test_stuck_relay_channel_does_not_change(hal):
    await _open(hal)
    relay = hal.world.relay_for("pump_dist")
    relay.stuck[0] = False
    await hal.bus.write_coil(20, 0, True)
    assert await hal.bus.read_coils(20, 0, 1) == [False]


async def test_offline_and_unknown_devices_time_out(hal):
    await _open(hal)
    hal.world.set_device_online("air", False)
    with pytest.raises(ModbusTimeout):
        await hal.bus.read_input_registers(3, 1, 2)
    with pytest.raises(ModbusTimeout):
        await hal.bus.read_holding_registers(2, 0, 1)  # alamat 2 kosong di Rev A
    assert not hal.bus.log[-1].ok


async def test_port_disappears(hal):
    await _open(hal)
    hal.world.port_present = False
    with pytest.raises(BusUnavailable):
        await hal.bus.read_coils(20, 0, 4)


async def test_float_input_follows_world(hal, config):
    await _open(hal)
    number = config.inputs.float_low_ok.input
    assert await hal.bus.read_discrete_inputs(21, number - 1, 1) == [True]
    hal.world.set_float_low_ok(False)
    assert await hal.bus.read_discrete_inputs(21, number - 1, 1) == [False]


async def test_generic_device_is_plain_memory(hal):
    await _open(hal)
    await hal.bus.write_register(10, 5, 1234)
    assert await hal.bus.read_holding_registers(10, 5, 1) == [1234]
    hal.world.by_name["adc"].strict_addresses = True
    with pytest.raises(ModbusDeviceError):
        await hal.bus.read_holding_registers(10, 0x0100, 1)


# --- PZEM ---------------------------------------------------------------------------------------


async def test_pzem_registers_and_pump_current(hal, config):
    await _open(hal)
    regs = await hal.bus.read_input_registers(30, 0, 10)
    assert regs[0] / 10 == pytest.approx(config.fake.mains_voltage_v, abs=0.1)
    assert _u32(regs[1], regs[2]) / 1000 == pytest.approx(config.power.pump_baseline_a)
    assert regs[7] / 10 == 50.0

    await hal.bus.write_coil(20, config.outputs["pump_dist"].ch, True)
    hal.world.step(1.0)
    regs = await hal.bus.read_input_registers(30, 0, 10)
    expected = config.power.pump_baseline_a + config.power.pump_nominal_a
    assert _u32(regs[1], regs[2]) / 1000 == pytest.approx(expected)


async def test_pzem_energy_accumulates_in_wh(hal):
    await _open(hal)
    hal.world.step(3600.0)
    regs = await hal.bus.read_input_registers(30, 5, 2)
    pzem = hal.world.pzem
    assert _u32(*regs) == int(pzem.energy_wh)
    assert pzem.energy_wh > 0


async def test_mains_outage_silences_pzem_only(hal):
    await _open(hal)
    hal.world.set_mains(False)
    with pytest.raises(ModbusTimeout):
        await hal.bus.read_input_registers(30, 0, 1)
    # Relay panel tetap menjawab: tanda PLN padam, bukan bus putus (§7.4).
    assert await hal.bus.read_coils(20, 0, 4) == [False] * 4
    hal.world.set_mains(True)
    assert len(await hal.bus.read_input_registers(30, 0, 1)) == 1


async def test_xy_md02_signed_temperature(hal):
    await _open(hal)
    sensor = hal.world.by_name["air"]
    sensor.temperature_c = -3.5
    sensor.step(0)
    temp, rh = await hal.bus.read_input_registers(3, 1, 2)
    assert _s16(temp) / 10 == -3.5
    assert rh / 10 == pytest.approx(sensor.humidity_pct, abs=0.05)


def test_sensor_drift_is_seeded(config):
    a, b = FakeWorld(config), FakeWorld(config)
    for _ in range(10):
        a.step(1.0)
        b.step(1.0)
    assert a.by_name["air"].temperature_c == b.by_name["air"].temperature_c
    assert a.by_name["air"].temperature_c != config.fake.air_temperature_c


# --- trolley & GPIO -----------------------------------------------------------------------------


async def test_trolley_moves_with_relays_and_reed_pulses(hal, config):
    await _open(hal)
    world = hal.world
    world.trolley.position_mm = 0
    world.step(0)
    assert hal.gpio.is_active("home")
    events = hal.gpio.subscribe()

    run, direction = config.outputs["trolley_run"], config.outputs["trolley_dir"]
    await hal.bus.write_coil(20, direction.ch, True)  # utara
    await hal.bus.write_coil(20, run.ch, True)
    # S1 di 300 mm, S2 di 900 mm; 0,15 m/s → 7 s cukup melewati dua magnet.
    for _ in range(70):
        world.step(0.1)
    await hal.bus.write_coil(20, run.ch, False)

    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    reed_falls = [e for e in seen if e.line == "reed" and e.active]
    assert len(reed_falls) == 2
    assert ("home", False) in [(e.line, e.active) for e in seen]
    position = world.trolley.position_mm
    world.step(5.0)
    assert world.trolley.position_mm == position  # RUN OFF → berhenti


async def test_large_step_does_not_skip_magnets(hal, config):
    await _open(hal)
    world = hal.world
    world.trolley.position_mm = 0
    events = hal.gpio.subscribe()
    await hal.bus.write_coil(20, config.outputs["trolley_dir"].ch, True)
    await hal.bus.write_coil(20, config.outputs["trolley_run"].ch, True)
    world.step(45.0)  # satu langkah besar sepanjang rel
    reeds = [e for e in _drain(events) if e.line == "reed" and e.active]
    assert len(reeds) == config.trolley.stations
    assert hal.gpio.is_active("end")


async def test_trolley_heads_home_when_dir_off(hal, config):
    await _open(hal)
    world = hal.world
    world.trolley.position_mm = 500
    await hal.bus.write_coil(20, config.outputs["trolley_run"].ch, True)
    world.step(10.0)
    assert world.trolley.position_mm == 0
    assert hal.gpio.is_active("home")


async def test_broken_limit_wire_reads_active(hal):
    await _open(hal)
    events = hal.gpio.subscribe()
    assert not hal.gpio.is_active("end")
    hal.world.break_limit_wire("end")
    assert hal.gpio.is_active("end")
    assert [(e.line, e.active) for e in _drain(events)] == [("end", True)]


async def test_gpio_unknown_line(hal):
    with pytest.raises(KeyError):
        hal.gpio.is_active("pan")


async def test_world_runs_on_clock(hal, clock, config):
    await _open(hal)
    hal.world.trolley.position_mm = 0
    await hal.bus.write_coil(20, config.outputs["trolley_dir"].ch, True)
    await hal.bus.write_coil(20, config.outputs["trolley_run"].ch, True)
    task = asyncio.create_task(hal.world.run(clock))
    await asyncio.sleep(0)
    await clock.advance(2.0)
    task.cancel()
    assert hal.world.trolley.position_mm == pytest.approx(300, abs=10)


def _drain(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


# --- kamera -------------------------------------------------------------------------------------


async def test_camera_produces_valid_png(hal, config):
    await _open(hal)
    frame = await hal.camera.capture()
    assert frame.encoding == "png"
    assert frame.data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", frame.data[16:24])
    assert (width, height) == (config.fake.image_width, config.fake.image_height)
    # Setiap chunk punya CRC yang benar.
    offset = 8
    while offset < len(frame.data):
        (length,) = struct.unpack(">I", frame.data[offset : offset + 4])
        body = frame.data[offset + 4 : offset + 8 + length]
        (crc,) = struct.unpack(">I", frame.data[offset + 8 + length : offset + 12 + length])
        assert zlib.crc32(body) & 0xFFFFFFFF == crc
        offset += 12 + length


async def test_camera_failure(hal):
    await _open(hal)
    hal.camera.fail = True
    with pytest.raises(CameraError):
        await hal.camera.capture()


async def test_relay_module_type(hal):
    assert isinstance(hal.world.relay_for("pan_west"), FakeRelayModule)
