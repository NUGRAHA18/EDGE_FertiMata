from __future__ import annotations

import pytest
from pydantic import ValidationError

from fertimata_edge.bus import BusManager
from fertimata_edge.config import RegisterSpec
from fertimata_edge.profiles import (
    PZEM016_REGISTERS,
    RegisterMapProfile,
    build_sensor_profiles,
    decode,
    encode,
    read_blocks,
)

from .conftest import make_config
from .helpers import drive


@pytest.mark.parametrize(
    ("words", "spec", "expected"),
    [
        ([2214], RegisterSpec(fc=4, address=0, scale=0.1), 221.4),
        ([0xFFDD], RegisterSpec(fc=4, address=0, type="s16", scale=0.1), -3.5),
        ([0xFFDD], RegisterSpec(fc=4, address=0, type="u16"), 65501),
        # PZEM: word rendah dulu. 0x0001_86A0 = 100000 → 100,000 A × 0,001
        (
            [0x86A0, 0x0001],
            RegisterSpec(fc=4, address=0, type="u32", word_order="lo_hi", scale=0.001),
            100.0,
        ),
        ([0x0001, 0x86A0], RegisterSpec(fc=3, address=0, type="u32", word_order="hi_lo"), 100000),
        ([0xFFFF, 0xFFFE], RegisterSpec(fc=3, address=0, type="s32", word_order="hi_lo"), -2),
    ],
)
def test_decode(words, spec, expected):
    assert decode(words, spec) == pytest.approx(expected)
    assert decode(encode(expected, spec), spec) == pytest.approx(expected)


def test_32bit_needs_word_order():
    with pytest.raises(ValidationError, match="word_order"):
        RegisterSpec(fc=3, address=0, type="u32")


def test_read_blocks_groups_contiguous_registers():
    assert read_blocks(PZEM016_REGISTERS, 32) == [(4, 0, 9)]
    specs = {
        "a": RegisterSpec(fc=3, address=0),
        "b": RegisterSpec(fc=3, address=1),
        "c": RegisterSpec(fc=3, address=100),
        "d": RegisterSpec(fc=4, address=5, type="u32", word_order="hi_lo"),
    }
    assert read_blocks(specs, 32) == [(3, 0, 2), (3, 100, 1), (4, 5, 2)]


def test_unverified_profiles_skipped_without_registers():
    from fertimata_edge.config import load_config

    from .conftest import EXAMPLE_CONFIG

    profiles = build_sensor_profiles(load_config(EXAMPLE_CONFIG))
    assert sorted(profiles) == ["air", "pzem"]


def test_config_overrides_default_register(dev_data):
    dev_data["devices"]["pzem"]["registers"] = {"voltage": {"fc": 4, "address": 0, "scale": 1}}
    profile = RegisterMapProfile("pzem", make_config(dev_data).devices["pzem"])
    assert profile.registers["voltage"].scale == 1
    assert "current" in profile.registers  # bawaan lain tetap


async def test_pzem_profile_reads_catalog_metrics(config, hal, clock):
    bus = BusManager(hal.bus, config, clock)
    await bus.start()
    hal.world.pzem.energy_wh = 12345
    hal.world.pzem.step(0)
    result = await drive(clock, RegisterMapProfile("pzem", config.devices["pzem"]).read(bus))
    assert result.ok
    values = result.values
    assert set(values) == {
        "mains.voltage",
        "mains.current",
        "mains.power",
        "mains.energy",
        "mains.frequency",
        "mains.power_factor",
    }
    assert values["mains.voltage"] == pytest.approx(config.fake.mains_voltage_v, abs=0.1)
    assert values["mains.energy"] == pytest.approx(12.345)  # Wh → kWh
    assert values["mains.current"] == pytest.approx(config.power.pump_baseline_a)
    assert bus.transactions == 1  # satu blok untuk sepuluh register
    await bus.stop()


async def test_xy_md02_and_config_mapped_sensors(config, hal, clock):
    bus = BusManager(hal.bus, config, clock)
    await bus.start()
    hal.world.by_name["air"].temperature_c = -1.5
    hal.world.by_name["air"].step(0)
    air = await drive(clock, RegisterMapProfile("air", config.devices["air"]).read(bus))
    assert air.values["air.temperature"] == -1.5
    light = await drive(clock, RegisterMapProfile("light", config.devices["light"]).read(bus))
    assert light.values == {"light.illuminance": 18450}
    soil = await drive(clock, RegisterMapProfile("soil", config.devices["soil"]).read(bus))
    assert soil.values["soil_a.ph"] == 6.4 and len(soil.values) == 7
    # Kontrak §3: EC tanah dalam mS/cm (register µS/cm × 0,001), semesta fuzzy irigasi AI 0–4.
    assert soil.values["soil_a.ec"] == pytest.approx(1.25)
    await bus.stop()


async def test_failed_block_drops_only_its_metrics(config, hal, clock):
    bus = BusManager(hal.bus, config, clock)
    await bus.start()
    hal.world.set_device_online("air", False)
    result = await drive(clock, RegisterMapProfile("air", config.devices["air"]).read(bus))
    assert result.values == {} and len(result.errors) == 1
    await bus.stop()
