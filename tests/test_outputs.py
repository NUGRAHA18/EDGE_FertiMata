"""Model output: S1, S2, S8 (reaksi float), S14 (jeda polling), S19, dan profil relay A/B."""

from __future__ import annotations

import asyncio

import pytest

from fertimata_edge.bus import Priority
from fertimata_edge.hal import ModbusDeviceError, create_hal

from .conftest import make_config
from .rig import build_rig, close_rig


@pytest.fixture
async def rig(config, hal, clock):
    rig = await build_rig(config, hal, clock)
    yield rig
    await close_rig(rig)


@pytest.fixture
async def cold_rig(config, hal, clock):
    """Rig yang belum menjalankan S1."""
    rig = await build_rig(config, hal, clock, boot=False)
    yield rig
    await close_rig(rig)


# --- S1 -----------------------------------------------------------------------------------------


async def test_s1_turns_every_channel_off_and_reads_back(cold_rig):
    rig = cold_rig
    rig.relay("pump_dist").force_channel(0, True)
    rig.relay("dose_n").force_channel(7, True)  # kanal cadangan juga dimatikan
    assert await rig.run(rig.outputs.all_off())
    assert not any(rig.relay("pump_dist").state) and not any(rig.relay("dose_n").state)
    assert all(st.on is False for st in rig.outputs.states.values())
    assert rig.sink.alerts == []


async def test_s1_stuck_channel_alerts_and_locks_output(cold_rig):
    rig = cold_rig
    relay = rig.relay("pump_dist")
    relay.force_channel(0, True)
    relay.stuck[0] = True
    assert not await rig.run(rig.outputs.all_off())
    [alert] = rig.sink.of_type("RELAY_MISMATCH")
    assert alert["device"] == "PANEL-01" and alert["severity"] == "CRITICAL"
    assert alert["actuator_code"] == "DIST-PUMP"
    assert alert["metadata"]["cause"] == "boot"
    assert rig.outputs.states["pump_dist"].faulty
    assert rig.outputs.state("pump_dist") is True  # dilaporkan apa adanya, bukan hasil tulisan


async def test_s1_for_relay_that_answers_later(cold_rig):
    rig = cold_rig
    rig.world.set_device_online("relay_t", False)
    assert not await rig.run(rig.outputs.all_off())
    assert rig.outputs.state("dose_n") is None
    rig.world.set_device_online("relay_t", True)
    rig.relay("dose_n").force_channel(0, True)
    await rig.run(rig.outputs.poll())
    assert rig.outputs.initialized["relay_t"]
    assert not rig.physical("dose_n") and rig.outputs.state("dose_n") is False


# --- tulis + baca balik (S19) -------------------------------------------------------------------


async def test_set_reads_back_and_notifies(rig):
    result = await rig.run(rig.outputs.set("pan_west", True))
    assert result.ok and result.state is True and rig.physical("pan_west")
    assert rig.changes == ["relay_p"]
    ops = [(t.op, t.unit) for t in rig.hal.bus.log[-2:]]
    assert ops == [("write_coil", 20), ("read_coils", 20)]


async def test_cancelled_write_leaves_state_unknown(rig):
    # Agent berhenti tepat setelah relay ditulis ON, sebelum baca balik: state lama (OFF) tidak
    # boleh dipercaya, supaya pemanggil tetap mematikannya dan safety menganggapnya mungkin ON.
    task = asyncio.ensure_future(rig.outputs.set("stirrer", True, duration_s=60))
    for _ in range(1000):
        if rig.physical("stirrer"):
            break
        await rig.clock.advance(0.001)
    assert rig.physical("stirrer") and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.outputs.state("stirrer") is None
    assert (await rig.run(rig.outputs.set("stirrer", False, priority=Priority.STOP))).ok
    assert not rig.physical("stirrer") and rig.outputs.state("stirrer") is False


async def test_s19_mismatch_tries_off_alerts_and_locks(rig):
    rig.relay("dose_k").stuck[2] = False
    result = await rig.run(rig.outputs.set("dose_k", True, duration_s=5))
    assert not result.ok and result.reason == "RELAY_MISMATCH"
    assert rig.outputs.states["dose_k"].faulty
    [alert] = rig.sink.of_type("RELAY_MISMATCH")
    assert alert["device"] == "RIO-TANK-01" and alert["metadata"]["wanted"] == "ON"
    assert "pompa pupuk K" in alert["title"]
    # Percobaan OFF setelah tidak cocok ikut tercatat di bus.
    writes = [t for t in rig.hal.bus.log if t.op == "write_coil" and t.unit == 21]
    assert [w.value for w in writes[-2:]] == [True, False]


async def test_write_to_silent_relay_fails_with_offline_reason(rig):
    rig.world.set_device_online("relay_p", False)
    result = await rig.run(rig.outputs.set("pan_west", True))
    assert not result.ok and result.reason == "DEVICE_OFFLINE"
    assert "relay panel" in result.text
    assert rig.outputs.state("pan_west") is None


async def test_poll_turns_off_channel_on_without_command(rig):
    rig.relay("stirrer").force_channel(4, True)
    await rig.run(rig.outputs.poll())
    assert not rig.physical("stirrer")
    [alert] = rig.sink.of_type("RELAY_MISMATCH")
    assert alert["metadata"]["cause"] == "poll"


async def test_poll_after_failed_write_turns_off_without_locking(rig):
    rig.world.set_device_online("relay_p", False)
    await rig.run(rig.outputs.set("pan_west", True))
    rig.world.set_device_online("relay_p", True)
    rig.relay("pan_west").force_channel(1, True)  # tulisan ternyata sampai, jawabannya hilang
    await rig.run(rig.outputs.poll())
    assert not rig.physical("pan_west")
    assert not rig.outputs.states["pan_west"].faulty
    assert rig.sink.of_type("RELAY_MISMATCH") == []


async def test_poll_adopts_relay_that_turned_off_by_itself(rig):
    await rig.run(rig.outputs.set("dose_n", True, duration_s=60))
    rig.relay("dose_n").force_channel(0, False)  # modul relay sempat kehilangan daya
    rig.changes.clear()
    await rig.run(rig.outputs.poll())
    assert rig.outputs.state("dose_n") is False and rig.changes == ["relay_t"]
    assert rig.outputs.states["dose_n"].timer is None
    assert rig.sink.of_type("RELAY_MISMATCH") == []


# --- watchdog (S2) ------------------------------------------------------------------------------


async def test_s2_duration_turns_off_without_alert(rig):
    await rig.run(rig.outputs.set("dose_n", True, duration_s=5))
    await rig.clock.advance(4.9)
    assert rig.physical("dose_n")
    await rig.run(rig.clock.advance(0.2))
    assert not rig.physical("dose_n") and rig.outputs.state("dose_n") is False
    assert rig.changes == ["relay_t", "relay_t"]  # ON lalu OFF oleh timer → status terkirim
    assert rig.sink.of_type("OUTPUT_TIMEOUT") == []


async def test_s2_max_on_cuts_longer_request_and_alerts(rig):
    await rig.run(rig.outputs.set("dose_n", True, duration_s=500))
    await rig.run(rig.clock.advance(120.05))
    assert not rig.physical("dose_n")
    [alert] = rig.sink.of_type("OUTPUT_TIMEOUT")
    assert alert["severity"] == "WARNING" and alert["actuator_code"] == "DOSING-N"
    assert alert["metadata"]["maxOnSeconds"] == 120 and alert["metadata"]["requestedSeconds"] == 500


async def test_s2_watchdog_stop_uses_stop_priority(rig, monkeypatch):
    calls = []
    original = rig.outputs.set

    async def spy(name, on, **kwargs):
        calls.append((name, on, kwargs.get("priority"), kwargs.get("cause")))
        return await original(name, on, **kwargs)

    monkeypatch.setattr(rig.outputs, "set", spy)
    await rig.run(original("pump_dist", True, duration_s=1))
    await rig.run(rig.clock.advance(1.05))
    assert not rig.physical("pump_dist")
    assert calls == [("pump_dist", False, Priority.STOP, "timer")]


async def test_off_cancels_watchdog(rig):
    await rig.run(rig.outputs.set("stirrer", True, duration_s=10))
    timer = rig.outputs.states["stirrer"].timer
    await rig.run(rig.outputs.set("stirrer", False))
    await rig.run(rig.clock.advance(0.01))
    assert timer.cancelled() and rig.outputs.states["stirrer"].timer is None


async def test_on_again_restarts_watchdog(rig):
    await rig.run(rig.outputs.set("stirrer", True, duration_s=10))
    await rig.clock.advance(8)
    await rig.run(rig.outputs.set("stirrer", True, duration_s=10))
    await rig.clock.advance(5)
    assert rig.physical("stirrer")
    await rig.run(rig.clock.advance(5.1))
    assert not rig.physical("stirrer")


# --- float (S8) dan telemetri IN1 (Q2) ----------------------------------------------------------


async def test_float_sent_as_number_every_relay_poll(rig):
    rig.sink.telemetry.clear()
    await rig.run(rig.outputs.poll())
    assert rig.sink.telemetry == [("RIO-TANK-01", {"tank.float_low_ok": 1})]
    rig.world.set_float_low_ok(False)
    await rig.run(rig.outputs.poll())
    assert rig.sink.telemetry[-1] == ("RIO-TANK-01", {"tank.float_low_ok": 0})
    [alert] = rig.sink.of_type("FLOAT_LOW")
    assert alert["title"] == "Air di drum hampir habis"


async def test_s8_float_open_stops_pumps(rig):
    await rig.run(rig.outputs.set("pump_dist", True, duration_s=600))
    await rig.run(rig.outputs.set("pan_west", True))
    rig.world.set_float_low_ok(False)
    await rig.run(rig.outputs.poll())
    assert not rig.physical("pump_dist")
    assert rig.physical("pan_west")  # bukan output yang diblokir float


async def test_s8_stale_float_stops_pumps(rig):
    await rig.run(rig.outputs.set("pump_dist", True, duration_s=900))
    rig.world.set_device_online("relay_t", False)
    await rig.clock.advance(rig.config.safety.float_max_age_s + 1)
    await rig.run(rig.outputs.poll())
    assert not rig.physical("pump_dist")


# --- jeda polling saat trolley jalan (S14) ------------------------------------------------------


async def test_s14_polling_paused_while_trolley_runs(rig):
    await rig.run(rig.outputs.set("trolley_run", True, duration_s=5))
    assert rig.bus.polling_paused
    await rig.run(rig.outputs.set("trolley_run", False, priority=Priority.STOP))
    assert not rig.bus.polling_paused


async def test_s14_unknown_run_state_does_not_pause_forever(rig):
    await rig.run(rig.outputs.set("trolley_run", True, duration_s=5))
    rig.world.set_device_online("relay_p", False)
    await rig.run(rig.outputs.set("trolley_run", False, priority=Priority.STOP))
    assert rig.outputs.state("trolley_run") is None
    assert not rig.bus.polling_paused


# --- profil B (holding register) ----------------------------------------------------------------


@pytest.fixture
def profile_b_config(dev_data):
    dev_data["devices"]["relay_p"]["relay"] = {
        "write_fc": 6,
        "write_base": 1,
        "on_value": 0x0100,
        "off_value": 0x0200,
        "read_fc": 3,
        "read_base": 1,
    }
    return make_config(dev_data)


async def test_profile_b_writes_registers_and_reads_back(profile_b_config, clock):
    hal = create_hal(profile_b_config, clock)
    rig = await build_rig(profile_b_config, hal, clock)
    try:
        result = await rig.run(rig.outputs.set("pan_west", True))
        assert result.ok and rig.physical("pan_west")
        writes = [t for t in hal.bus.log if t.op == "write_register" and t.unit == 20]
        # R1 di register 2 karena kanal mulai dari 1 (write_base), nilai ON 0x0100.
        assert (writes[-1].address, writes[-1].value) == (2, 0x0100)
        with pytest.raises(ModbusDeviceError):
            await hal.bus.write_coil(20, 1, False)  # profil B tidak menerima FC5
    finally:
        await close_rig(rig)
