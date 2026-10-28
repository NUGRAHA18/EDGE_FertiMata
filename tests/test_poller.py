from __future__ import annotations

import pytest

from fertimata_edge.bus import BusManager
from fertimata_edge.poller import Poller

from .fakes import RecordingSink
from .helpers import drive


@pytest.fixture
async def setup(config, hal, clock):
    bus = BusManager(hal.bus, config, clock)
    await bus.start()
    sink = RecordingSink()
    poller = Poller(config, bus, clock, sink)
    yield poller, sink, bus
    await poller.stop()
    await bus.stop()


async def test_groups_follow_polling_config(setup):
    poller, _, _ = setup
    assert poller.groups == {
        "soil_s": ["soil"],
        "air_light_s": ["air", "light"],
        "pzem_s": ["pzem"],
    }


async def test_one_message_per_logical_device(setup, clock):
    poller, sink, _ = setup
    await drive(clock, poller.poll(["air", "light"]))
    [(device, data)] = sink.telemetry
    assert device == "GREENHOUSE-01"
    assert set(data) == {"air.temperature", "air.humidity", "light.illuminance"}


async def test_silent_device_metrics_omitted_and_alerted(setup, clock, hal, config):
    poller, sink, _ = setup
    hal.world.set_device_online("light", False)
    for _ in range(config.bus.fail_threshold):
        await drive(clock, poller.poll(["air", "light"]))
    for _, data in sink.telemetry:
        assert "light.illuminance" not in data and "air.temperature" in data
    [alert] = sink.alerts
    assert alert["type"] == "MODBUS_TIMEOUT" and alert["device"] == "GREENHOUSE-01"
    assert alert["title"] == "Sensor cahaya tidak terbaca"
    assert alert["metadata"]["modbusAddress"] == 4


async def test_all_silent_sends_nothing_and_alerts_bus_down(setup, clock, hal, config):
    poller, sink, _ = setup
    for name in ("soil", "air", "light", "water_t", "pzem"):
        hal.world.set_device_online(name, False)
    for _ in range(config.bus.fail_threshold):
        await drive(clock, poller.poll(list(poller.profiles)))
    assert sink.telemetry == []
    bus_down = [a for a in sink.alerts if a["type"] == "BUS_DOWN"]
    assert bus_down and bus_down[0]["device"] == "JETSON-01"
    assert bus_down[0]["severity"] == "CRITICAL"


async def test_port_missing_alerts_bus_down(setup, clock, hal):
    poller, sink, _ = setup
    hal.world.port_present = False
    await drive(clock, poller.poll(["pzem"]))
    assert [a["type"] for a in sink.alerts] == ["BUS_DOWN"]
    assert sink.alerts[0]["metadata"]["portMissing"] is True


async def test_pzem_silent_while_relay_answers_is_not_bus_down(setup, clock, hal, config):
    # PLN padam (§7.4) bukan gangguan bus. Penentuan BATTERY dikerjakan modul daya (F5).
    poller, sink, _ = setup
    hal.world.set_mains(False)
    for _ in range(config.bus.fail_threshold):
        await drive(clock, poller.poll(["pzem", "air"]))
    assert {a["type"] for a in sink.alerts} == {"MODBUS_TIMEOUT"}


async def test_poll_loop_runs_on_interval(setup, clock, config):
    poller, sink, _ = setup
    poller.start()
    await drive(clock, _until(lambda: len(_of(sink, "PZEM-01")) >= 3), step=0.5)
    assert clock.monotonic() >= 2 * config.polling.pzem_s


async def test_health_follows_bus(config, hal, clock):
    from fertimata_edge.health import DeviceHealth

    health = DeviceHealth(config)
    bus = BusManager(hal.bus, config, clock)
    bus.device_listeners.append(health.report)
    await bus.start()
    poller = Poller(config, bus, clock, None)
    await drive(clock, poller.poll(["pzem"]))
    assert "PZEM-01" in health.alive_devices()
    hal.world.set_mains(False)
    for _ in range(config.bus.fail_threshold):
        await drive(clock, poller.poll(["pzem"]))
    assert "PZEM-01" not in health.alive_devices()
    await bus.stop()


def _of(sink, device):
    return [d for dev, d in sink.telemetry if dev == device]


async def _until(predicate):
    import asyncio

    while not predicate():  # noqa: ASYNC110 - jam palsu dimajukan oleh drive()
        await asyncio.sleep(0)
