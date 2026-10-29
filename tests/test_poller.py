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


# --- PZEM: baca tiap pzem_s, kirim tiap pzem_publish_s (permintaan backend 29 Okt) ----------------


async def test_pzem_read_every_pzem_s_but_published_every_publish_s(setup, clock, config):
    poller, sink, _ = setup
    reads: list[float] = []
    poller.read_listeners.append(
        lambda name, values: reads.append(clock.monotonic()) if name == "pzem" and values else None
    )
    poller.start()
    span = 2 * config.polling.pzem_publish_s
    await drive(clock, _until(lambda: clock.monotonic() >= span + 0.5), step=0.5, limit=1000)

    # Deteksi lokal (daya, arus) tetap mendapat setiap bacaan.
    assert len(reads) >= span / config.polling.pzem_s
    # Backend hanya menerima satu pesan per interval kirim: t=0, 30, 60.
    sent = _of(sink, "PZEM-01")
    assert len(sent) == 3
    assert set(sent[-1]) >= {"mains.voltage", "mains.energy"}


async def test_pzem_publish_waits_for_next_good_read_after_failure(setup, clock, hal, config):
    poller, sink, _ = setup
    await drive(clock, poller.poll(["pzem"]))
    assert len(_of(sink, "PZEM-01")) == 1
    await clock.advance(config.polling.pzem_publish_s)
    hal.world.set_device_online("pzem", False)
    await drive(clock, poller.poll(["pzem"]))  # jatuh tempo, tapi bacaan gagal: tidak ada pesan
    assert len(_of(sink, "PZEM-01")) == 1
    hal.world.set_device_online("pzem", True)
    await drive(clock, poller.poll(["pzem"]))  # bacaan berhasil berikutnya langsung dikirim
    assert len(_of(sink, "PZEM-01")) == 2


async def test_other_groups_still_publish_every_read(setup, clock):
    poller, sink, _ = setup
    for _ in range(3):
        await drive(clock, poller.poll(["air", "light"]))
    assert len(_of(sink, "GREENHOUSE-01")) == 3


def test_contract_lists_pzem_publish_interval(config):
    from .conftest import ROOT

    contract = (ROOT / "docs" / "02-kontrak-mqtt.md").read_text(encoding="utf-8")
    row = next(line for line in contract.splitlines() if line.startswith("| `PZEM-01` | `pzem_"))
    assert f"| {config.polling.pzem_publish_s:g} s |" in row
    assert f"`pzem_s` {config.polling.pzem_s:g} s" in row
