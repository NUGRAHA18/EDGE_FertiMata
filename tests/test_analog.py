"""Larutan drum: urutan baca §6.3 (F6.3), konversi EC/pH (F6.4), validasi S9 (F6.5)."""

from __future__ import annotations

import asyncio

import pytest

from fertimata_edge.analog import (
    AnalogReader,
    compensate_ec,
    ec_from_mv,
    ec_k_values,
    ph_from_mv,
)
from fertimata_edge.config import EcChannelConfig, PhChannelConfig
from fertimata_edge.hal import create_hal

from .conftest import make_config
from .rig import build_rig, close_rig


def ec_cfg(**cal) -> EcChannelConfig:
    return EcChannelConfig(
        device="adc",
        ch=1,
        k=1.0,
        mv_per_ms_cm=164.0,
        range_switch_ms_cm=(2.0, 2.5),
        cal_k_range=(0.75, 1.5),
        cal=cal,
    )


def ph_cfg(**cal) -> PhChannelConfig:
    return PhChannelConfig(device="adc", ch=2, cal_temperature_c=25, cal=cal)


# --- F6.4 konversi ------------------------------------------------------------------------------


def test_ec_k_values_fall_back_to_other_point_or_k():
    assert ec_k_values(ec_cfg()) == (1.0, 1.0)
    low, high = ec_k_values(ec_cfg(v_1413=200.0))
    assert low == high == pytest.approx(164 * 1.413 / 200)
    low, high = ec_k_values(ec_cfg(v_1413=231.7, v_12880=2000.0))
    assert (low, high) == (pytest.approx(1.0001, abs=1e-3), pytest.approx(164 * 12.88 / 2000))


def test_ec_calibration_points_reproduce_solutions():
    cfg = ec_cfg(v_1413=250.0, v_12880=2000.0)
    assert ec_from_mv(250.0, cfg, False) == (pytest.approx(1.413), False)
    assert ec_from_mv(2000.0, cfg, False) == (pytest.approx(12.88), True)


def test_ec_range_switch_has_hysteresis():
    cfg = ec_cfg(v_1413=231.7, v_12880=2000.0)  # K rendah ≈ 1,0, K tinggi ≈ 1,056
    mv = 2.2 * 164  # di antara 2,0 dan 2,5 mS/cm: rentang lama dipertahankan
    assert ec_from_mv(mv, cfg, False)[1] is False
    assert ec_from_mv(mv, cfg, True)[1] is True
    assert ec_from_mv(1.5 * 164, cfg, True)[1] is False


def test_ec_temperature_compensation_to_25c():
    assert compensate_ec(2.2, 30.0, 0.02) == pytest.approx(2.0)
    assert compensate_ec(2.0, 25.0, 0.02) == 2.0


def test_ph_two_point_line():
    cfg = ph_cfg(v_401=3038.2, v_686=2525.2)
    assert ph_from_mv(3038.2, cfg, 25.0) == pytest.approx(4.01)
    assert ph_from_mv(2525.2, cfg, 25.0) == pytest.approx(6.86)


def test_ph_three_points_least_squares():
    cfg = ph_cfg(v_401=3040.0, v_686=2525.2, v_918=2105.0)
    for mv, expected in [(3040.0, 4.01), (2525.2, 6.86), (2105.0, 9.18)]:
        assert ph_from_mv(mv, cfg, 25.0) == pytest.approx(expected, abs=0.02)


def test_ph_slope_corrected_for_temperature():
    cfg = ph_cfg(v_401=3038.2, v_686=2525.2, v_918=2107.6)
    mv_at_4 = 3038.2
    hot = ph_from_mv(mv_at_4, cfg, 35.0)
    # Slope elektroda naik dengan suhu mutlak: tegangan yang sama berarti pH lebih dekat ke 7.
    assert hot == pytest.approx(7 - (7 - 4.01) * 298.15 / 308.15, abs=0.01)
    assert ph_from_mv(mv_at_4, cfg, None) == pytest.approx(4.01, abs=0.01)


def test_ph_needs_two_points():
    assert ph_from_mv(2500.0, ph_cfg(v_686=2525.2), 25.0) is None


def test_duplicate_ph_voltage_rejected():
    with pytest.raises(ValueError, match="tidak boleh sama"):
        ph_cfg(v_401=2500.0, v_686=2500.0)


# --- F6.3 urutan baca ---------------------------------------------------------------------------


async def _analog_rig(config, hal, clock):
    rig = await build_rig(config, hal, clock)
    rig.analog = AnalogReader(config, rig.bus, rig.outputs, rig.safety, clock, rig.sink)
    return rig


@pytest.fixture
async def rig(config, hal, clock):
    rig = await _analog_rig(config, hal, clock)
    yield rig
    await close_rig(rig)


async def measure(rig):
    return await rig.run(rig.analog.measure(), step=0.1)


def r5_writes(rig) -> list[bool]:
    ch = rig.config.outputs["ec_power"].ch
    return [
        t.value
        for t in rig.hal.bus.log
        if t.op == "write_coil" and t.unit == 21 and t.address == ch and t.ok
    ]


async def test_cycle_reads_tank_and_publishes_catalog_metrics(rig):
    reading = await measure(rig)
    assert reading.ph == pytest.approx(6.2, abs=0.01)
    assert reading.ec_ms_cm == pytest.approx(1.8, abs=0.01)
    assert reading.water_temperature_c == pytest.approx(26.3)
    assert reading.valid_for_dosing
    [(device, data)] = [t for t in rig.sink.telemetry if "tank.ph" in t[1]]
    assert device == "RIO-TANK-01"
    assert set(data) == {"tank.ph", "tank.ec", "tank.water_temperature"}


async def test_ph_read_with_ec_board_off_then_ec_with_board_on(rig):
    await measure(rig)
    assert r5_writes(rig)[-3:] == [False, True, False]  # pH → EC → kembali OFF
    assert rig.outputs.state("ec_power") is False
    # Simulator menggeser pH 150 mV saat papan EC menyala; urutan yang benar menghindarinya.
    assert rig.analog.last.ph == pytest.approx(6.2, abs=0.01)


async def test_manual_ec_power_refused_during_cycle(rig):
    task = asyncio.ensure_future(rig.analog.measure())
    await rig.clock.advance(1.0)
    result = await rig.command("EC-POWER", "ON")
    assert not result.success and result.error_message.startswith("BUSY")
    await rig.run(task, step=0.1)
    assert (await rig.command("EC-POWER", "ON")).success


async def test_median_ignores_spike(rig, monkeypatch):
    profile = rig.analog._adc["adc"]
    original = profile.read_fields
    calls = {"n": 0}

    async def spiky(bus, names, priority=None):
        values = await original(bus, names)
        calls["n"] += 1
        if calls["n"] == 2 and "a2" in values:
            values["a2"] = 9000.0  # satu bacaan liar
        return values

    monkeypatch.setattr(profile, "read_fields", spiky)
    reading = await measure(rig)
    assert reading.ph == pytest.approx(6.2, abs=0.01)


async def test_silent_adc_omits_ec_and_ph_keeps_temperature(rig):
    rig.world.set_device_online("adc", False)
    reading = await measure(rig)
    assert reading.ph is None and reading.ec_ms_cm is None
    assert reading.water_temperature_c == pytest.approx(26.3)
    assert not reading.valid_for_dosing
    assert rig.outputs.state("ec_power") is False


async def test_missing_temperature_drops_ec_only(rig):
    rig.world.set_device_online("water_t", False)
    reading = await measure(rig)
    assert reading.ec_ms_cm is None and reading.water_temperature_c is None
    assert reading.ph == pytest.approx(6.2, abs=0.05)  # tanpa koreksi suhu
    assert rig.sink.of_type("EC_PH_INVALID") == []


async def test_uncalibrated_ph_not_sent(dev_data, clock):
    dev_data["analog"]["ph"]["cal"] = {"v_401": None, "v_686": None, "v_918": None}
    config = make_config(dev_data)
    rig = await _analog_rig(config, create_hal(config, clock), clock)
    try:
        reading = await measure(rig)
        assert reading.ph is None and reading.ec_ms_cm is not None
        assert rig.sink.of_type("EC_PH_INVALID") == []
    finally:
        await close_rig(rig)


async def test_without_adc_map_ec_board_untouched(dev_data, clock):
    dev_data["devices"]["adc"]["registers"] = {}
    config = make_config(dev_data)
    rig = await _analog_rig(config, create_hal(config, clock), clock)
    try:
        before = len(r5_writes(rig))
        reading = await measure(rig)
        assert len(r5_writes(rig)) == before
        assert reading.water_temperature_c is not None and reading.ph is None
    finally:
        await close_rig(rig)


# --- F6.5 validasi (S9) -------------------------------------------------------------------------


async def test_s9_out_of_range_dropped_alerted_and_not_valid_for_dosing(rig):
    rig.world.tank_ph = 15.5
    rig.world._update_analog()
    reading = await measure(rig)
    assert reading.ph is None and "ph" in reading.invalid
    assert not reading.valid_for_dosing
    [alert] = rig.sink.of_type("EC_PH_INVALID")
    assert alert["device"] == "RIO-TANK-01" and alert["severity"] == "WARNING"
    assert alert["metadata"]["invalid"]["ph"] > 14
    sent = [d for _, d in rig.sink.telemetry if "tank.ec" in d]
    assert sent and "tank.ph" not in sent[-1]


async def test_s9_bad_temperature_blocks_ec_too(rig):
    rig.world.water_temperature_c = 85.0
    rig.world._update_analog()
    reading = await measure(rig)
    assert "water_temperature_c" in reading.invalid
    assert reading.ec_ms_cm is None and reading.water_temperature_c is None
