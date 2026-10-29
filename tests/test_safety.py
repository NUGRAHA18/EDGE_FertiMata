"""Interlock lokal dan eksekutor perintah: S3–S6, S8, S12–S14, S16, Kontrak §5."""

from __future__ import annotations

import pytest

from .rig import build_rig, close_rig


@pytest.fixture
async def rig(config, hal, clock):
    rig = await build_rig(config, hal, clock)
    yield rig
    await close_rig(rig)


def reason(result) -> str:
    assert not result.success, result
    return result.error_message.split(":", 1)[0]


# --- eksekutor (Kontrak §5) ---------------------------------------------------------------------


async def test_on_with_duration_acks_readback_state(rig):
    result = await rig.command("DOSING-N", "ON", 5)
    assert result.success and result.actuator_state == "ON" and rig.physical("dose_n")
    assert rig.outputs.states["dose_n"].deadline == pytest.approx(
        rig.clock.monotonic() + 5, abs=0.1
    )


async def test_on_without_required_duration_rejected(rig):
    result = await rig.command("DIST-PUMP", "ON")
    assert reason(result) == "INVALID" and result.actuator_state == "OFF"
    assert not rig.physical("pump_dist")


@pytest.mark.parametrize("duration", [0, -3])
async def test_non_positive_duration_rejected(rig, duration):
    assert reason(await rig.command("STIRRER", "ON", duration)) == "INVALID"


async def test_output_without_duration_rule_may_stay_on(rig):
    result = await rig.command("CAMERA-PAN", "ON")
    assert result.success and rig.outputs.states["pan_west"].timer is None


async def test_duration_capped_by_edge_limit(rig):
    # Batas yang lebih ketat dari keduanya berlaku (04-aturan-keselamatan, catatan).
    await rig.command("DOSING-P", "ON", 9999)
    assert rig.outputs.states["dose_p"].deadline == pytest.approx(
        rig.clock.monotonic() + rig.config.outputs["dose_p"].max_on_s, abs=0.1
    )


async def test_off_acks_off(rig):
    await rig.command("STIRRER", "ON", 30)
    result = await rig.command("STIRRER", "OFF")
    assert result.success and result.actuator_state == "OFF" and not rig.physical("stirrer")


async def test_silent_relay_gives_offline_ack(rig):
    rig.world.set_device_online("relay_t", False)
    result = await rig.command("STIRRER", "ON", 30)
    assert reason(result) == "DEVICE_OFFLINE"
    assert "relay kotak tangki" in result.error_message


async def test_faulty_output_refuses_on_but_accepts_off(rig):
    rig.relay("dose_k").stuck[2] = False
    assert reason(await rig.command("DOSING-K", "ON", 5)) == "RELAY_MISMATCH"
    assert reason(await rig.command("DOSING-K", "ON", 5)) == "OUTPUT_FAULT"
    assert (await rig.command("DOSING-K", "OFF")).success


# --- S3: OFF tidak pernah diblokir --------------------------------------------------------------


async def test_s3_off_allowed_while_interlocks_active(rig):
    await rig.command("DOSING-N", "ON", 30)
    rig.world.set_float_low_ok(False)
    await rig.run(rig.outputs._read_float("relay_t"))
    for actuator in ("DOSING-N", "DIST-PUMP", "STIRRER", "TROLLEY-RUN"):
        assert (await rig.command(actuator, "OFF")).success, actuator
    assert not rig.physical("dose_n")


# --- S4–S6: eksklusivitas -----------------------------------------------------------------------


async def test_s4_dosing_refused_while_trolley_runs(rig):
    assert (await rig.command("TROLLEY-RUN", "ON", 10)).success
    assert reason(await rig.command("DOSING-N", "ON", 5)) == "INTERLOCK_EXCLUSIVE"
    assert reason(await rig.command("STIRRER", "ON", 5)) == "INTERLOCK_EXCLUSIVE"
    assert not rig.physical("dose_n") and not rig.physical("stirrer")


async def test_s4_trolley_refused_while_dosing(rig):
    await rig.command("STIRRER", "ON", 30)
    result = await rig.command("TROLLEY-RUN", "ON", 10)
    assert reason(result) == "INTERLOCK_EXCLUSIVE"
    assert "pompa aduk" in result.error_message


async def test_s5_distribution_pump_refused_while_dosing(rig):
    await rig.command("DOSING-N", "ON", 30)
    assert reason(await rig.command("DIST-PUMP", "ON", 60)) == "INTERLOCK_EXCLUSIVE"
    await rig.command("DOSING-N", "OFF")
    assert (await rig.command("DIST-PUMP", "ON", 60)).success
    assert reason(await rig.command("STIRRER", "ON", 30)) == "INTERLOCK_EXCLUSIVE"


async def test_s6_one_peristaltic_at_a_time_but_stirrer_allowed(rig):
    assert (await rig.command("DOSING-N", "ON", 30)).success
    assert reason(await rig.command("DOSING-P", "ON", 30)) == "INTERLOCK_EXCLUSIVE"
    assert reason(await rig.command("DOSING-K", "ON", 30)) == "INTERLOCK_EXCLUSIVE"
    assert (await rig.command("STIRRER", "ON", 30)).success  # dosing + aduk memang bareng


async def test_unknown_state_counts_as_on(rig):
    rig.world.set_device_online("relay_t", False)
    await rig.command("DOSING-N", "OFF")  # gagal → state tidak diketahui
    assert rig.outputs.state("dose_n") is None
    assert reason(await rig.command("DIST-PUMP", "ON", 60)) == "INTERLOCK_EXCLUSIVE"


# --- S8: float ----------------------------------------------------------------------------------


async def test_s8_float_open_blocks_pumps(rig):
    rig.world.set_float_low_ok(False)
    await rig.run(rig.outputs._read_float("relay_t"))
    for actuator in ("DIST-PUMP", "DOSING-N", "STIRRER"):
        result = await rig.command(actuator, "ON", 5)
        assert reason(result) == "INTERLOCK_FLOAT", actuator
    assert "air di drum" in result.error_message
    assert (await rig.command("CAMERA-PAN", "ON")).success


async def test_s8_float_never_read_blocks(config, hal, clock):
    rig = await build_rig(config, hal, clock, boot=False)
    try:
        await rig.run(rig.outputs.all_off())
        assert reason(await rig.command("DIST-PUMP", "ON", 60)) == "INTERLOCK_FLOAT"
    finally:
        await close_rig(rig)


async def test_s8_stale_float_blocks(rig):
    await rig.clock.advance(rig.config.safety.float_max_age_s + 1)
    assert reason(await rig.command("DOSING-N", "ON", 5)) == "INTERLOCK_FLOAT"


# --- S12: DIR tidak berubah saat RUN ON ---------------------------------------------------------


@pytest.mark.parametrize("action", ["ON", "OFF"])
async def test_s12_dir_change_refused_while_running(rig, action):
    if action == "OFF":
        await rig.command("TROLLEY-DIR", "ON")
        await rig.clock.advance(1)
    assert (await rig.command("TROLLEY-RUN", "ON", 10)).success
    result = await rig.command("TROLLEY-DIR", action)
    assert reason(result) == "INTERLOCK_MOVING"
    assert rig.physical("trolley_dir") is (action == "OFF")


async def _write_times(rig) -> list[tuple[int, bool, float]]:
    times: list[tuple[int, bool, float]] = []
    rig.relay("trolley_run").on_change.append(
        lambda ch, v: times.append((ch, v, rig.clock.monotonic()))
    )
    return times


async def test_s12_run_waits_after_dir_change(rig):
    times = await _write_times(rig)
    await rig.command("TROLLEY-DIR", "ON")
    await rig.command("TROLLEY-RUN", "ON", 10)
    (dir_ch, _, t_dir), (run_ch, _, t_run) = times
    assert (dir_ch, run_ch) == (3, 2)
    assert t_run - t_dir >= rig.config.safety.dir_settle_ms / 1000


async def test_s12_dir_waits_after_stop(rig):
    times = await _write_times(rig)
    await rig.command("TROLLEY-RUN", "ON", 10)
    await rig.command("TROLLEY-RUN", "OFF")
    await rig.command("TROLLEY-DIR", "ON")
    t_stop = next(t for ch, v, t in times if ch == 2 and not v)
    t_dir = next(t for ch, v, t in times if ch == 3)
    assert t_dir - t_stop >= rig.config.safety.stop_settle_ms / 1000


# --- S13/S16: limit -----------------------------------------------------------------------------


async def test_s13_run_refused_toward_active_limit(rig):
    rig.world.trolley.position_mm = 0
    rig.world.step(0)  # HOME aktif
    assert reason(await rig.command("TROLLEY-RUN", "ON", 10)) == "INTERLOCK_LIMIT"  # DIR OFF = HOME
    await rig.command("TROLLEY-DIR", "ON")
    assert (await rig.command("TROLLEY-RUN", "ON", 10)).success  # menjauh dari HOME boleh


async def test_s16_both_limits_lock_trolley(rig):
    rig.world.break_limit_wire("end")
    rig.world.break_limit_wire("home")
    result = await rig.command("TROLLEY-RUN", "ON", 10)
    assert reason(result) == "INTERLOCK_LIMIT" and "dikunci" in result.error_message


async def test_s13_limit_hit_stops_running_trolley(rig):
    rig.safety.start()
    rig.world.trolley.position_mm = rig.world.trolley.rail_length_mm - 30
    await rig.command("TROLLEY-DIR", "ON")
    assert (await rig.command("TROLLEY-RUN", "ON", 10)).success
    await rig.advance(1.0)
    assert rig.world.gpio_active("end")
    assert not rig.physical("trolley_run") and rig.outputs.state("trolley_run") is False


async def test_s13_limit_between_check_and_write_still_stops(rig, monkeypatch):
    # Limit menjadi aktif setelah pemeriksaan awal lolos: pemeriksaan ulang sesudah tulis
    # tetap menghentikan trolley.
    rig.world.trolley.position_mm = 0
    rig.world.step(0)
    monkeypatch.setattr(rig.safety, "check", lambda name, on: None)
    result = await rig.command("TROLLEY-RUN", "ON", 10)
    assert reason(result) == "INTERLOCK_LIMIT"
    assert not rig.physical("trolley_run")


# --- S14: pan dilarang saat trolley jalan -------------------------------------------------------


async def test_s14_pan_refused_while_trolley_runs(rig):
    await rig.command("TROLLEY-RUN", "ON", 10)
    assert reason(await rig.command("CAMERA-PAN", "ON")) == "INTERLOCK_MOVING"
    await rig.command("TROLLEY-RUN", "OFF")
    assert (await rig.command("CAMERA-PAN", "ON")).success
