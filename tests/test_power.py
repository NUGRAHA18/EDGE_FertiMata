"""Daya: penentu MAINS/BATTERY/UNKNOWN (F5.1), S7 (F5.2), S10/S11 (F5.3), suhu SoC (F5.4)."""

from __future__ import annotations

import pytest

from fertimata_edge.hal import create_hal
from fertimata_edge.poller import Poller
from fertimata_edge.power import BATTERY, MAINS, UNKNOWN, PowerMonitor
from fertimata_edge.soc import SocMonitor

from .conftest import make_config
from .fakes import RecordingSink
from .rig import build_rig, close_rig


async def _power_rig(config, hal, clock):
    rig = await build_rig(config, hal, clock)
    rig.poller = Poller(config, rig.bus, clock, rig.sink)
    pm = PowerMonitor(config, rig.bus, rig.outputs, clock, rig.sink)
    rig.poller.read_listeners.append(pm.on_read)
    rig.bus.device_listeners.append(pm.on_device)
    rig.bus.bus_listeners.append(pm.on_bus)
    rig.safety.power_source = lambda: pm.source
    rig.pm = pm
    pm.start()
    return rig


@pytest.fixture
async def rig(config, hal, clock):
    rig = await _power_rig(config, hal, clock)
    yield rig
    await rig.pm.stop()
    await close_rig(rig)


async def poll_pzem(rig, times: int = 1) -> None:
    for _ in range(times):
        await rig.run(rig.poller.poll(["pzem"]))
    await rig.run(rig.clock.advance(0.05))  # beri waktu tugas daya bertindak


def set_voltage(rig, volts: float) -> None:
    rig.world.pzem.voltage_v = volts
    rig.world.pzem.step(0)


# --- F5.1 penentu sumber daya -------------------------------------------------------------------


async def test_unknown_until_first_reading(rig):
    assert rig.pm.source is None
    await poll_pzem(rig)
    assert rig.pm.source == MAINS


async def test_voltage_hysteresis(rig):
    await poll_pzem(rig)
    for volts, expected in [(190, MAINS), (179, BATTERY), (195, BATTERY), (200, MAINS)]:
        set_voltage(rig, volts)
        await poll_pzem(rig)
        assert rig.pm.source == expected, volts


async def test_between_thresholds_without_history_is_backup(rig):
    set_voltage(rig, 190)
    await poll_pzem(rig)
    assert rig.pm.source == BATTERY


async def test_silent_pzem_with_panel_relay_answering_is_battery(rig):
    await poll_pzem(rig)
    changes = []
    rig.pm.listeners.append(changes.append)
    rig.world.set_mains(False)
    await poll_pzem(rig, rig.config.bus.fail_threshold)
    assert rig.pm.source == BATTERY and changes == [BATTERY]


async def test_pzem_and_panel_silent_is_unknown(rig):
    await poll_pzem(rig)
    rig.world.set_device_online("relay_p", False)
    for _ in range(rig.config.bus.fail_threshold):
        await rig.run(rig.outputs.poll())
    rig.world.set_device_online("pzem", False)
    await poll_pzem(rig, rig.config.bus.fail_threshold)
    assert rig.pm.source == UNKNOWN


async def test_restore_needs_fresh_reading(rig):
    await poll_pzem(rig)
    rig.world.set_mains(False)
    await poll_pzem(rig, rig.config.bus.fail_threshold)
    rig.world.set_mains(True)
    # Tegangan 220 V dari sebelum padam tidak dipakai lagi; bacaan baru yang menentukan.
    set_voltage(rig, 190)
    await poll_pzem(rig)
    assert rig.pm.source == BATTERY
    set_voltage(rig, 221)
    await poll_pzem(rig)
    assert rig.pm.source == MAINS


# --- F5.2 respons padam (S7) --------------------------------------------------------------------


async def test_s7_outage_stops_and_blocks_until_mains(rig):
    await poll_pzem(rig)
    assert (await rig.command("DIST-PUMP", "ON", 600)).success
    assert (await rig.command("CAMERA-PAN", "ON")).success
    rig.world.set_mains(False)
    await poll_pzem(rig, rig.config.bus.fail_threshold)
    assert not rig.physical("pump_dist")
    assert rig.physical("pan_west")  # pan tidak termasuk blocked_on_backup_power

    result = await rig.command("STIRRER", "ON", 30)
    assert not result.success and result.error_message.startswith("INTERLOCK_POWER: listrik PLN")
    assert (await rig.command("CAMERA-PAN", "OFF")).success

    rig.world.set_mains(True)
    await poll_pzem(rig)
    assert rig.pm.source == MAINS
    assert (await rig.command("STIRRER", "ON", 30)).success


async def test_s7_unknown_power_blocks_commands(rig):
    result = await rig.command("DOSING-N", "ON", 5)  # belum ada bacaan PZEM
    assert not result.success and result.error_message.startswith("INTERLOCK_POWER")
    assert "belum diketahui" in result.error_message


async def test_s7_failed_stop_is_retried(rig):
    await poll_pzem(rig)
    await rig.command("DOSING-N", "ON", 60)
    rig.world.set_device_online("relay_t", False)
    rig.world.set_mains(False)
    await poll_pzem(rig, rig.config.bus.fail_threshold)
    assert rig.physical("dose_n") is True  # relay tangki diam, belum bisa dimatikan
    rig.world.set_device_online("relay_t", True)
    await poll_pzem(rig)
    assert not rig.physical("dose_n")


# --- F5.3 arus pompa (S10, S11) -----------------------------------------------------------------


async def test_s10_ssr_short_alert_after_repeated_reads(rig):
    await poll_pzem(rig)
    rig.world.ssr_shorted = True
    rig.world.pzem.step(0)
    await poll_pzem(rig)
    assert rig.sink.of_type("SSR_SHORT") == []  # satu bacaan belum cukup (anomaly_reads)
    await poll_pzem(rig)
    [alert] = rig.sink.of_type("SSR_SHORT")
    assert alert["device"] == "PANEL-01" and alert["severity"] == "CRITICAL"
    assert alert["actuator_code"] == "DIST-PUMP" and "RCBO" in alert["message"]
    assert alert["metadata"]["current"] == pytest.approx(1.85)


async def test_s11_dry_pump_stopped_with_alert(rig):
    await poll_pzem(rig)
    await rig.command("DIST-PUMP", "ON", 600)
    rig.world.pump_dry = True
    rig.world.pzem.step(0)
    await rig.clock.advance(rig.config.power.current_settle_s)
    await poll_pzem(rig, rig.config.power.anomaly_reads)
    assert not rig.physical("pump_dist")
    [alert] = rig.sink.of_type("PUMP_DRY_RUN")
    assert alert["title"] == "Pompa siram jalan tanpa air"


async def test_normal_pump_and_settle_window_raise_nothing(rig):
    await poll_pzem(rig)
    await rig.command("DIST-PUMP", "ON", 600)
    rig.world.pump_dry = True
    rig.world.pzem.step(0)
    await poll_pzem(rig, 3)  # masih dalam current_settle_s: tidak dinilai
    rig.world.pump_dry = False
    rig.world.pzem.step(0)
    await rig.clock.advance(rig.config.power.current_settle_s)
    await poll_pzem(rig, 3)
    await rig.command("DIST-PUMP", "OFF")
    rig.world.pzem.step(0)  # register PZEM palsu hanya diperbarui saat step
    await rig.clock.advance(rig.config.power.current_settle_s)
    await poll_pzem(rig, 3)
    assert rig.physical("pump_dist") is False
    assert rig.sink.of_type("PUMP_DRY_RUN") == [] and rig.sink.of_type("SSR_SHORT") == []


async def test_current_checks_off_without_baseline(dev_data, clock):
    dev_data["power"]["pump_baseline_a"] = None
    config = make_config(dev_data)
    rig = await _power_rig(config, create_hal(config, clock), clock)
    try:
        await poll_pzem(rig)
        rig.world.ssr_shorted = True
        rig.world.pzem.step(0)
        await poll_pzem(rig, 3)
        assert rig.sink.of_type("SSR_SHORT") == []
    finally:
        await rig.pm.stop()
        await close_rig(rig)


# --- F5.4 suhu SoC ------------------------------------------------------------------------------


async def test_soc_temperature_telemetry_and_hot_alert(config, hal, clock):
    sink = RecordingSink()
    soc = SocMonitor(config, hal.soc, clock, sink)
    assert await soc.read_once() == 48.0
    assert sink.telemetry == [("JETSON-01", {"jetson.soc_temperature": 48.0})]
    assert sink.alerts == []
    hal.world.soc_temperature_c = 83.46
    await soc.read_once()
    [alert] = sink.of_type("SOC_HOT")
    assert alert["device"] == "JETSON-01" and "83,5 °C" in alert["message"]


async def test_soc_read_failure_sends_nothing(config, hal, clock):
    sink = RecordingSink()
    hal.world.soc_temperature_c = None
    assert await SocMonitor(config, hal.soc, clock, sink).read_once() is None
    assert sink.telemetry == []


async def test_sysfs_soc_takes_hottest_zone(tmp_path):
    from fertimata_edge.hal import HalError
    from fertimata_edge.hal.real_soc import SysfsSoc

    for i, value in enumerate(["45500", "51250", "rusak"]):
        zone = tmp_path / f"thermal_zone{i}"
        zone.mkdir()
        (zone / "temp").write_text(value + "\n", encoding="ascii")
    assert await SysfsSoc(str(tmp_path / "thermal_zone*" / "temp")).read_temperature_c() == 51.25
    with pytest.raises(HalError):
        await SysfsSoc(str(tmp_path / "tidak_ada*")).read_temperature_c()
