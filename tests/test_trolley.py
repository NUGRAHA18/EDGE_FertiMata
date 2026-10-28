"""Trolley: homing, menuju stasiun (F7.2), telemetri (F7.3), perintah mentah (F7.4), S13/S15/S16."""

from __future__ import annotations

import asyncio

import pytest

from fertimata_edge.trolley import Trolley, TrolleyError

from .rig import build_rig, close_rig

MAGNET_TOLERANCE_MM = 30  # berhenti sesaat setelah tepi reed; magnet simulator selebar 20 mm


@pytest.fixture
async def rig(config, hal, clock):
    rig = await build_rig(config, hal, clock)
    rig.trolley = Trolley(config, rig.outputs, rig.safety, hal.gpio, clock, rig.sink)
    rig.safety.start()
    rig.trolley.start()
    yield rig
    await rig.trolley.stop()
    await close_rig(rig)


async def until_done(rig, awaitable, limit_s: float = 150.0, step: float = 0.02):
    task = asyncio.ensure_future(awaitable)
    elapsed = 0.0
    while not task.done() and elapsed < limit_s:
        await rig.advance(step, step=step)
        elapsed += step
    assert task.done(), "gerak trolley tidak selesai"
    return task.result()


def magnet_mm(rig, station: int) -> float:
    return rig.world.trolley.magnets_mm[station - 1]


def run_writes(rig) -> list[tuple[int, bool]]:
    chans = {rig.config.outputs[n].ch for n in ("trolley_run", "trolley_dir")}
    return [
        (t.address, t.value)
        for t in rig.hal.bus.log
        if t.op == "write_coil" and t.unit == 20 and t.address in chans and t.ok
    ]


# --- F7.2 homing dan menuju stasiun -------------------------------------------------------------


async def test_homing_from_middle_of_rail(rig):
    assert rig.trolley.position is None
    await until_done(rig, rig.trolley.homing())
    assert rig.world.trolley.position_mm == 0 and rig.world.gpio_active("home")
    assert rig.trolley.position == 0 and rig.trolley.at_station
    assert not rig.physical("trolley_run") and not rig.physical("trolley_dir")


async def test_homing_at_home_does_not_move(rig):
    rig.world.trolley.position_mm = 0
    rig.world.step(0)
    before = len(run_writes(rig))
    await until_done(rig, rig.trolley.homing())
    assert len(run_writes(rig)) == before and rig.trolley.position == 0


async def test_goto_station_stops_at_magnet(rig):
    await until_done(rig, rig.trolley.goto_station(3))
    assert abs(rig.world.trolley.position_mm - magnet_mm(rig, 3)) <= MAGNET_TOLERANCE_MM
    assert rig.trolley.position == 3 and rig.trolley.at_station
    assert rig.trolley.snapshot()["trolley.station"] == 3
    assert not rig.bus.polling_paused  # S14: polling kembali setelah berhenti


async def test_s12_dir_set_before_run_and_settle_respected(rig):
    times: list[tuple[int, bool, float]] = []
    rig.relay("trolley_run").on_change.append(
        lambda ch, v: times.append((ch, v, rig.clock.monotonic()))
    )
    await until_done(rig, rig.trolley.goto_station(1))
    dir_ch, run_ch = rig.config.outputs["trolley_dir"].ch, rig.config.outputs["trolley_run"].ch
    for i, (ch, value, at) in enumerate(times):
        if ch == run_ch and value:  # setiap RUN ON
            last_dir = max((t for c, _, t in times[:i] if c == dir_ch), default=None)
            if last_dir is not None:
                assert at - last_dir >= rig.config.safety.dir_settle_ms / 1000
        if ch == dir_ch:  # DIR tidak pernah berubah saat RUN ON
            run_on = [v for c, v, _ in times[:i] if c == run_ch]
            assert not run_on or run_on[-1] is False


async def test_lower_station_is_approached_from_south(rig):
    await until_done(rig, rig.trolley.goto_station(4))
    await until_done(rig, rig.trolley.goto_station(2))
    assert abs(rig.world.trolley.position_mm - magnet_mm(rig, 2)) <= MAGNET_TOLERANCE_MM
    dir_ch = rig.config.outputs["trolley_dir"].ch
    dirs = [v for ch, v in run_writes(rig) if ch == dir_ch]
    assert dirs[-2:] == [False, True]  # homing ke selatan, lalu mendekat dari selatan


async def test_same_station_again_does_nothing(rig):
    await until_done(rig, rig.trolley.goto_station(2))
    before = len(run_writes(rig))
    await until_done(rig, rig.trolley.goto_station(2))
    assert len(run_writes(rig)) == before


async def test_invalid_station_rejected(rig):
    with pytest.raises(TrolleyError, match="INVALID"):
        await rig.trolley.goto_station(rig.config.trolley.stations + 1)


async def test_interlocks_still_apply_to_automatic_moves(rig):
    await rig.command("STIRRER", "ON", 120)
    with pytest.raises(TrolleyError) as info:
        await until_done(rig, rig.trolley.goto_station(1))
    assert info.value.reason == "TROLLEY_REFUSED" and "pompa aduk" in info.value.text


# --- S15 batas waktu ----------------------------------------------------------------------------


async def test_s15_no_reed_stops_and_alerts(rig):
    await until_done(rig, rig.trolley.homing())
    rig.world.trolley.magnets_mm = []  # magnet lepas / reed rusak
    with pytest.raises(TrolleyError) as info:
        await until_done(rig, rig.trolley.goto_station(1))
    assert info.value.reason == "TROLLEY_NO_REED"
    assert not rig.physical("trolley_run") and rig.trolley.position is None
    [alert] = rig.sink.of_type("TROLLEY_NO_REED")
    assert alert["severity"] == "CRITICAL" and alert["device"] == "JETSON-01"
    assert rig.clock.monotonic() < 60  # dihentikan oleh batas segmen, bukan batas total


async def test_s15_total_travel_limit(rig):
    await until_done(rig, rig.trolley.homing())
    rig.world.trolley.speed_mm_s = 80  # pulsa reed tiap 7,5 s, tapi S10 butuh > 60 s
    with pytest.raises(TrolleyError) as info:
        await until_done(rig, rig.trolley.goto_station(10))
    assert info.value.reason == "TROLLEY_TIMEOUT"
    assert not rig.physical("trolley_run")
    assert rig.sink.of_type("TROLLEY_TIMEOUT")


# --- S13 / S16 limit ----------------------------------------------------------------------------


async def test_s13_unexpected_end_stops_homes_and_fails(rig):
    await until_done(rig, rig.trolley.homing())
    rig.world.trolley.magnets_mm = rig.world.trolley.magnets_mm[:-1]  # magnet S10 hilang
    with pytest.raises(TrolleyError) as info:
        await until_done(rig, rig.trolley.goto_station(10))
    assert info.value.reason == "TROLLEY_LIMIT_HIT"
    assert rig.sink.of_type("TROLLEY_LIMIT_HIT")
    assert rig.world.trolley.position_mm == 0 and rig.trolley.position == 0  # sudah homing


async def test_s16_both_limits_lock_trolley(rig):
    rig.world.break_limit_wire("end")
    await rig.advance(0.1)
    rig.world.break_limit_wire("home")
    await rig.advance(0.1)
    assert rig.trolley.locked is not None
    [alert] = rig.sink.of_type("TROLLEY_LIMIT_WIRE")
    assert alert["severity"] == "CRITICAL"
    with pytest.raises(TrolleyError, match="TROLLEY_LOCKED"):
        await rig.trolley.goto_station(1)
    result = await rig.command("TROLLEY-RUN", "ON", 10)
    assert not result.success and "dikunci" in result.error_message
    rig.world.break_limit_wire("end", False)
    await rig.advance(0.1)
    # Kunci bertahan sampai agent di-restart, walau kabel sempat terbaca normal lagi.
    assert not (await rig.command("TROLLEY-RUN", "ON", 10)).success


async def test_s16_limit_stuck_after_leaving_locks(rig):
    rig.world.trolley.position_mm = 0
    rig.world.break_limit_wire("home")  # HOME terbaca aktif terus
    await rig.advance(0.1)
    with pytest.raises(TrolleyError, match="TROLLEY_LOCKED"):
        await until_done(rig, rig.trolley.goto_station(2))
    assert rig.trolley.locked is not None and not rig.physical("trolley_run")
    assert abs(rig.world.trolley.position_mm - magnet_mm(rig, 1)) <= MAGNET_TOLERANCE_MM


# --- F7.3 telemetri -----------------------------------------------------------------------------


async def test_telemetry_on_change_and_station_only_when_known(rig):
    rig.sink.telemetry.clear()
    rig.trolley.report(force=True)
    [(device, data)] = rig.sink.telemetry
    assert device == "JETSON-01"
    assert data == {"trolley.home_limit": 0, "trolley.end_limit": 0}  # posisi belum diketahui
    await until_done(rig, rig.trolley.homing())
    sent = [d for _, d in rig.sink.telemetry]
    assert {"trolley.home_limit": 1, "trolley.end_limit": 0, "trolley.station": 0} in sent


# --- F7.4 perintah mentah -----------------------------------------------------------------------


async def test_raw_run_makes_position_unknown(rig):
    await until_done(rig, rig.trolley.goto_station(2))
    await rig.command("TROLLEY-DIR", "ON")
    assert (await rig.command("TROLLEY-RUN", "ON", 2)).success
    await rig.advance(0.2)
    assert rig.trolley.position is None
    await rig.advance(2.5)
    assert not rig.physical("trolley_run")
    assert "trolley.station" not in rig.trolley.snapshot()


async def test_raw_run_without_reed_stopped_by_s15(rig):
    rig.world.trolley.magnets_mm = []
    await rig.command("TROLLEY-DIR", "ON")
    assert (await rig.command("TROLLEY-RUN", "ON", 30)).success
    await rig.advance(rig.config.trolley.segment_timeout_s + 0.5)
    assert not rig.physical("trolley_run")
    [alert] = rig.sink.of_type("TROLLEY_NO_REED")
    assert alert["metadata"]["automatic"] is False


async def test_raw_commands_held_during_automatic_move_but_stop_allowed(rig):
    await until_done(rig, rig.trolley.homing())
    task = asyncio.ensure_future(rig.trolley.goto_station(8))
    await rig.advance(1.0)
    assert rig.physical("trolley_run")
    for actuator, action in (("TROLLEY-DIR", "OFF"), ("CAMERA-PAN", "ON"), ("TROLLEY-RUN", "ON")):
        result = await rig.command(actuator, action, 10 if actuator == "TROLLEY-RUN" else None)
        assert not result.success and result.error_message.startswith("BUSY"), actuator
    assert (await rig.command("TROLLEY-RUN", "OFF")).success  # S3: berhenti selalu boleh
    with pytest.raises(TrolleyError, match="TROLLEY_STOPPED"):
        await until_done(rig, task)
    assert (await rig.command("CAMERA-PAN", "ON")).success  # kunci dilepas setelah berhenti


async def test_unavailable_gpio_disables_trolley(dev_data, clock):
    from fertimata_edge.hal import create_hal
    from fertimata_edge.hal.real_stubs import LockedGpio

    from .conftest import make_config

    config = make_config(dev_data)
    hal = create_hal(config, clock)
    hal.gpio = LockedGpio()
    rig = await build_rig(config, hal, clock)
    try:
        trolley = Trolley(config, rig.outputs, rig.safety, hal.gpio, clock, rig.sink)
        trolley.start()
        assert trolley.tasks == [] and rig.sink.of_type("TROLLEY_LIMIT_WIRE") == []
        with pytest.raises(TrolleyError, match="belum terpasang"):
            await trolley.homing()
    finally:
        await close_rig(rig)
