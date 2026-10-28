from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from fertimata_edge import __version__
from fertimata_edge.commands import CommandResult, NotReadyHandler
from fertimata_edge.hal.clock import to_iso_z
from fertimata_edge.health import DeviceHealth
from fertimata_edge.link import Link, Outbox, OutgoingMessage

from .fakes import FakeTransport, RecordingHandler


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.fixture
def health(config) -> DeviceHealth:
    return DeviceHealth(config)


@pytest.fixture
async def setup(config, clock, health):
    transport = FakeTransport()
    handler = RecordingHandler()
    link = Link(config, transport, clock, health, handler)
    await link.start()
    await _settle()
    transport.published.clear()  # buang heartbeat awal
    yield link, transport, handler
    await link.stop()


def _command(clock, **overrides) -> dict:
    body = {
        "commandUid": "uid-1",
        "deviceId": "RIO-TANK-01",
        "actuatorCode": "DOSING-N",
        "command": "ON",
        "parameters": {"durationSeconds": 5},
        "issuedAt": to_iso_z(clock.now()),
        "expiresAt": to_iso_z(clock.now() + timedelta(seconds=30)),
    }
    body.update(overrides)
    return body


# --- outbox (F2.6) ------------------------------------------------------------------------------


def test_outbox_drops_oldest_telemetry_never_ack_or_alert():
    box = Outbox(3)
    box.push(OutgoingMessage("command_ack", "a", b"1"))
    box.push(OutgoingMessage("telemetry", "t", b"old"))
    box.push(OutgoingMessage("alert", "x", b"2"))
    box.push(OutgoingMessage("telemetry", "t", b"new"))
    assert [m.payload for m in _drain(box)] == [b"1", b"2", b"new"]
    assert box.dropped == 1


def test_outbox_keeps_important_messages_over_limit():
    box = Outbox(2)
    for i in range(4):
        box.push(OutgoingMessage("command_ack", "a", str(i).encode()))
    assert len(box) == 4


def _drain(box):
    items = []
    while (m := box.pop()) is not None:
        items.append(m)
    return items


async def test_messages_queued_while_disconnected_then_flushed_in_order(config, clock, health):
    transport = FakeTransport(connected=False)
    link = Link(config, transport, clock, health, RecordingHandler())
    await link.start()
    link.publish_telemetry("PZEM-01", {"mains.voltage": 221.0})
    link.publish_alert("PANEL-01", "RELAY_MISMATCH", "CRITICAL", "Relay tidak menurut", "Cek panel")
    await _settle()
    assert transport.published == []
    transport.connect()
    await _settle()
    topics = [t for t, _ in transport.published]
    assert topics == [
        "fertimata/PZEM-01/telemetry",
        "fertimata/PANEL-01/alert",
        "fertimata/JETSON-01/status",  # heartbeat pertama ikut antre di belakang
    ]
    await link.stop()


async def test_failed_publish_is_retried_after_reconnect(setup):
    link, transport, _h = setup
    transport.fail_publish = True
    link.publish_telemetry("PZEM-01", {"mains.voltage": 220.0})
    await _settle()
    assert transport.published == [] and len(link.outbox) == 1
    transport.fail_publish = False
    transport.disconnect()
    transport.connect()
    await _settle()
    assert transport.messages("/telemetry")[0][1]["data"] == {"mains.voltage": 220.0}


async def test_telemetry_without_values_is_not_sent(setup):
    link, transport, _h = setup
    assert link.publish_telemetry("GREENHOUSE-01", {"air.temperature": None}) is False
    await _settle()
    assert transport.published == []


# --- heartbeat (F2.4) ---------------------------------------------------------------------------


async def test_heartbeat_only_for_alive_devices(setup, clock, health):
    _, transport, _h = setup
    await clock.advance(30)
    await _settle()
    assert [t for t, _ in transport.published] == ["fertimata/JETSON-01/status"]
    assert transport.messages()[0][1]["metadata"] == {"agentVersion": __version__}

    health.report("pzem", True)
    health.report("water_t", True)  # satu sumber cukup untuk RIO-TANK-01 (Q3)
    transport.published.clear()
    await clock.advance(30)
    await _settle()
    assert sorted(t for t, _ in transport.published) == [
        "fertimata/JETSON-01/status",
        "fertimata/PZEM-01/status",
        "fertimata/RIO-TANK-01/status",
    ]
    # Tanpa data power (F5) powerSource tidak dikirim: UNKNOWN akan memicu blokir di backend.
    assert all("powerSource" not in body for _, body in transport.messages())

    health.report("pzem", False)
    transport.published.clear()
    await clock.advance(30)
    await _settle()
    assert "fertimata/PZEM-01/status" not in [t for t, _ in transport.published]


async def test_heartbeat_interval_follows_config(setup, clock, config):
    _, transport, _h = setup
    await clock.advance(config.mqtt.heartbeat_s * 3)
    await _settle()
    assert len(transport.published) == 3


async def test_status_extras_hook(config, clock, health):
    transport = FakeTransport()
    link = Link(
        config,
        transport,
        clock,
        health,
        RecordingHandler(),
        status_extras=lambda d: {"power_source": "MAINS", "actuators": {"X": "OFF"}},
    )
    await link.start()
    await _settle()
    body = transport.messages("/status")[0][1]
    assert body["powerSource"] == "MAINS" and body["actuators"] == {"X": "OFF"}
    await link.stop()


def test_health_rules(config):
    health = DeviceHealth(config)
    assert health.alive_devices() == ["JETSON-01"]
    with pytest.raises(KeyError):
        health.report("tidak-ada", True)


# --- alert --------------------------------------------------------------------------------------


async def test_alert_repeat_is_rate_limited(setup, clock, config):
    link, transport, _h = setup
    args = ("RIO-TANK-01", "FLOAT_LOW", "WARNING", "Air di drum hampir habis", "Isi drum")
    assert link.publish_alert(*args) is True
    assert link.publish_alert(*args) is False
    assert link.publish_alert("PANEL-01", *args[1:]) is True  # device lain tidak tertahan
    await clock.advance(config.mqtt.alert_repeat_s)
    assert link.publish_alert(*args) is True
    assert link.publish_alert(*args, force=True) is True
    await _settle()
    assert len(transport.messages("/alert")) == 4


async def test_info_alerts_are_never_rate_limited(setup):
    # Backend mencatat setiap INFO; hasil patroli/trolley berurutan tidak boleh hilang.
    link, transport, _h = setup
    args = ("JETSON-01", "TROLLEY_ARRIVED", "INFO", "Trolley sampai", "Trolley di stasiun 3")
    assert link.publish_alert(*args) is True
    assert link.publish_alert(*args) is True
    await _settle()
    assert len(transport.messages("/alert")) == 2


# --- perintah (F2.5) ----------------------------------------------------------------------------


async def test_command_forwarded_and_acked(setup, clock):
    _, transport, handler = setup
    transport.deliver("fertimata/RIO-TANK-01/command", _command(clock))
    await _settle()
    assert len(handler.commands) == 1
    cmd = handler.commands[0]
    assert (cmd.device_id, cmd.output, cmd.action) == ("RIO-TANK-01", "dose_n", "ON")
    assert cmd.payload.duration_s == 5
    [(topic, ack)] = transport.messages("/command/ack")
    assert topic == "fertimata/RIO-TANK-01/command/ack"
    assert ack == {
        "commandUid": "uid-1",
        "success": True,
        "actuatorState": "ON",
        "executedAt": to_iso_z(clock.now()),
        "errorMessage": None,
    }


async def test_s17_expired_command_not_executed(setup, clock):
    _, transport, handler = setup
    body = _command(clock, expiresAt=to_iso_z(clock.now() - timedelta(seconds=1)))
    transport.deliver("fertimata/RIO-TANK-01/command", body)
    await _settle()
    assert handler.commands == []
    ack = transport.messages("/command/ack")[0][1]
    assert ack["success"] is False
    assert ack["errorMessage"].startswith("EXPIRED: ")


async def test_command_without_expiry_is_allowed(setup, clock):
    _, transport, handler = setup
    transport.deliver("fertimata/RIO-TANK-01/command", _command(clock, expiresAt=None))
    await _settle()
    assert len(handler.commands) == 1


async def test_duplicate_uid_executed_and_acked_once(setup, clock):
    _, transport, handler = setup
    for _ in range(3):
        transport.deliver("fertimata/RIO-TANK-01/command", _command(clock))
    await _settle()
    assert len(handler.commands) == 1
    assert len(transport.messages("/command/ack")) == 1


async def test_dedup_memory_is_bounded(setup, clock, config):
    _, transport, handler = setup
    size = config.mqtt.dedup_size
    for i in range(size + 1):
        transport.deliver("fertimata/RIO-TANK-01/command", _command(clock, commandUid=f"u{i}"))
    transport.deliver("fertimata/RIO-TANK-01/command", _command(clock, commandUid="u0"))
    await _settle()
    assert len(handler.commands) == size + 2  # u0 sudah terlupa, jadi dijalankan lagi


@pytest.mark.parametrize(
    ("topic", "overrides", "reason"),
    [
        ("fertimata/RIO-TANK-02/command", {}, "UNSUPPORTED"),
        ("fertimata/RIO-TANK-01/command", {"actuatorCode": "LED"}, "UNSUPPORTED"),
        ("fertimata/PANEL-01/command", {}, "UNSUPPORTED"),  # DOSING-N bukan milik PANEL-01
        ("fertimata/RIO-TANK-01/command", {"command": "TOGGLE"}, "UNSUPPORTED"),
        # Kontrak §5.1: kosakata per jenis aktuator; aktuator virtual terikat satu device logis.
        ("fertimata/RIO-TANK-01/command", {"command": "START"}, "UNSUPPORTED"),
        (
            "fertimata/JETSON-01/command",
            {"actuatorCode": "TROLLEY-GOTO", "command": "ON"},
            "UNSUPPORTED",
        ),
        (
            "fertimata/RIO-TANK-01/command",
            {"actuatorCode": "TROLLEY-GOTO", "command": "HOME"},
            "UNSUPPORTED",
        ),
        (
            "fertimata/RIO-TANK-01/command",
            {"actuatorCode": "DOSING-LOOP", "command": "START"},
            "UNSUPPORTED",
        ),
        (
            "fertimata/JETSON-01/command",
            {"actuatorCode": "DOSING-LOOP", "command": "DOSE_TO_TARGET"},
            "UNSUPPORTED",
        ),
        ("fertimata/RIO-TANK-01/command", {"actuatorCode": None}, "INVALID"),
        ("fertimata/RIO-TANK-01/command", {"expiresAt": "kemarin"}, "INVALID"),
    ],
)
async def test_rejected_commands(setup, clock, topic, overrides, reason):
    _, transport, handler = setup
    transport.deliver(topic, _command(clock, **overrides))
    await _settle()
    assert handler.commands == []
    [(ack_topic, ack)] = transport.messages("/command/ack")
    assert ack_topic == topic + "/ack"
    assert ack["success"] is False and ack["errorMessage"].startswith(reason + ": ")


async def test_virtual_command_forwarded_with_kind(setup, clock):
    _, transport, handler = setup
    body = _command(
        clock,
        deviceId="JETSON-01",
        actuatorCode="TROLLEY-GOTO",
        command="goto_station",
        parameters={"station": 2},
    )
    transport.deliver("fertimata/JETSON-01/command", body)
    await _settle()
    [command] = handler.commands
    assert command.output == "trolley_goto" and command.action == "GOTO_STATION"
    assert command.payload.parameters == {"station": 2}


async def test_lowercase_command_accepted(setup, clock):
    _, transport, handler = setup
    transport.deliver("fertimata/RIO-TANK-01/command", _command(clock, command="off"))
    await _settle()
    assert handler.commands[0].action == "OFF"


@pytest.mark.parametrize("raw", [b"bukan json", b"[1,2]", b'{"actuatorCode":"STIRRER"}'])
async def test_unanswerable_messages_ignored(setup, raw):
    _, transport, handler = setup
    transport.deliver("fertimata/RIO-TANK-01/command", raw)
    await _settle()
    assert handler.commands == [] and transport.published == []


async def test_payload_device_id_does_not_override_topic(setup, clock):
    _, transport, handler = setup
    transport.deliver("fertimata/RIO-TANK-01/command", _command(clock, deviceId="PANEL-01"))
    await _settle()
    assert handler.commands[0].device_id == "RIO-TANK-01"


async def test_handler_crash_gives_failed_ack(config, clock, health):
    class Boom(RecordingHandler):
        async def execute(self, command):
            raise RuntimeError("rusak")

    transport = FakeTransport()
    link = Link(config, transport, clock, health, Boom())
    await link.start()
    transport.deliver("fertimata/RIO-TANK-01/command", _command(clock))
    await _settle()
    ack = transport.messages("/command/ack")[0][1]
    assert ack["success"] is False and ack["errorMessage"].startswith("ERROR: ")
    assert not link.tasks[1].done()  # worker tetap hidup
    await link.stop()


async def test_failed_result_from_handler(config, clock, health):
    transport = FakeTransport()
    link = Link(config, transport, clock, health, NotReadyHandler())
    await link.start()
    transport.deliver("fertimata/PANEL-01/command", _command(clock, actuatorCode="DIST-PUMP"))
    await _settle()
    ack = transport.messages("/command/ack")[0][1]
    assert ack["success"] is False and ack["errorMessage"].startswith("NOT_READY: ")
    await link.stop()


async def test_commands_processed_in_arrival_order(config, clock, health):
    order: list[str] = []

    class Slow(RecordingHandler):
        async def execute(self, command):
            order.append(f"start {command.action}")
            await asyncio.sleep(0.01)
            order.append(f"end {command.action}")
            return CommandResult(True, command.action)

    transport = FakeTransport()
    link = Link(config, transport, clock, health, Slow())
    await link.start()
    transport.deliver("fertimata/RIO-TANK-01/command", _command(clock, commandUid="a"))
    transport.deliver(
        "fertimata/RIO-TANK-01/command", _command(clock, commandUid="b", command="OFF")
    )
    await asyncio.sleep(0.05)
    assert order == ["start ON", "end ON", "start OFF", "end OFF"]
    await link.stop()


async def test_subscribes_to_all_command_topics(setup):
    _, transport, _h = setup
    assert transport.subscriptions == ["fertimata/+/command"]
