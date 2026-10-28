from __future__ import annotations

import asyncio
import io
import logging
import signal
import subprocess
import sys

import pytest

from fertimata_edge import __version__
from fertimata_edge.app import Agent
from fertimata_edge.cli import EXIT_CONFIG, install_stop_handlers, main
from fertimata_edge.logs import KeyValueFormatter, fields, setup_logging

from .conftest import DEV_CONFIG, EXAMPLE_CONFIG, ROOT
from .fakes import FakeTransport
from .helpers import drive


async def test_agent_starts_and_stops_cleanly(config, hal, clock):
    agent = Agent(config, hal)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await drive(clock, agent.ready.wait())
    assert hal.bus.is_open and hal.gpio.is_open and hal.camera.is_open

    hal.world.trolley.position_mm = 0
    await hal.bus.write_coil(20, config.outputs["trolley_dir"].ch, True)
    await hal.bus.write_coil(20, config.outputs["trolley_run"].ch, True)
    await clock.advance(1.0)
    assert hal.world.trolley.position_mm > 0  # simulator ikut berjalan

    stop.set()
    await asyncio.wait_for(task, 1)
    assert not hal.bus.is_open and not hal.gpio.is_open and not hal.camera.is_open


async def test_agent_runs_link_and_stops_it(config, hal, clock):
    transport = FakeTransport()
    agent = Agent(config, hal, transport=transport)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await drive(clock, agent.ready.wait())
    for _ in range(10):
        await asyncio.sleep(0)
    assert transport.subscriptions == ["fertimata/+/command"]
    assert "fertimata/JETSON-01/status" in [t for t, _ in transport.published]
    stop.set()
    await asyncio.wait_for(task, 1)
    assert not transport.started


async def test_background_crash_stops_agent(config, hal, clock):
    def boom(dt: float) -> None:
        raise RuntimeError("simulator rusak")

    hal.world.step = boom
    agent = Agent(config, hal)
    task = asyncio.create_task(agent.run(asyncio.Event()))
    await drive(clock, agent.ready.wait())
    await clock.advance(1.0)
    with pytest.raises(RuntimeError, match="simulator rusak"):
        await asyncio.wait_for(task, 1)
    assert not hal.bus.is_open


async def test_stop_handler_sets_event():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        install_stop_handlers(loop, stop)
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(stop.wait(), 1)
    finally:
        for sig, handler in previous.items():
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError):
                pass
            signal.signal(sig, handler)


def test_main_rejects_bad_config(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("agent: {site: x, hal: fake}\n", encoding="utf-8")
    assert main(["--config", str(path)]) == EXIT_CONFIG
    err = capsys.readouterr().err
    assert "Config ditolak" in err and "mqtt" in err


def test_main_check_config(capsys):
    assert main(["--config", str(EXAMPLE_CONFIG), "--check-config"]) == 0
    assert "Config valid" in capsys.readouterr().out


def test_module_entry_point_runs():
    result = subprocess.run(
        [sys.executable, "-m", "fertimata_edge", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == __version__


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM ke subprocess hanya di POSIX")
def test_sigterm_stops_process():
    proc = subprocess.Popen(
        [sys.executable, "-m", "fertimata_edge", "--config", str(DEV_CONFIG)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        for line in proc.stdout:
            if "agent siap" in line:
                break
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()


# --- logging ------------------------------------------------------------------------------------


def test_log_format_has_utc_time_and_fields():
    stream = io.StringIO()
    setup_logging("DEBUG", stream)
    logging.getLogger("fertimata_edge.test").info(
        "relay ditulis", extra=fields(unit=20, ch=0, note="baca balik ok")
    )
    line = stream.getvalue().strip()
    stamp, level, name, rest = line.split(" ", 3)
    assert stamp.endswith("Z") and "T" in stamp
    assert (level, name) == ("INFO", "fertimata_edge.test")
    assert rest == 'relay ditulis unit=20 ch=0 note="baca balik ok"'


def test_log_level_filters():
    stream = io.StringIO()
    setup_logging("WARNING", stream)
    logging.getLogger("x").info("tidak tampil")
    logging.getLogger("x").warning("tampil")
    assert stream.getvalue().count("\n") == 1


def test_formatter_includes_exception():
    formatter = KeyValueFormatter()
    try:
        raise ValueError("rusak")
    except ValueError:
        record = logging.getLogger("x").makeRecord(
            "x", logging.ERROR, __file__, 1, "gagal", None, sys.exc_info()
        )
    assert "ValueError: rusak" in formatter.format(record)


# --- F4: S1 sebelum link, perintah lewat link sampai relay --------------------------------------


class _RecordingStartTransport(FakeTransport):
    def __init__(self, world) -> None:
        super().__init__()
        self.world = world
        self.relays_at_start: list[bool] | None = None

    async def start(self, subscriptions):
        relays = [d for d in self.world.devices.values() if hasattr(d, "state")]
        self.relays_at_start = [on for relay in relays for on in relay.state]
        await super().start(subscriptions)


async def test_s1_all_relays_off_before_mqtt_starts(config, hal, clock):
    hal.world.relay_for("pump_dist").force_channel(0, True)
    hal.world.relay_for("stirrer").force_channel(4, True)
    transport = _RecordingStartTransport(hal.world)
    agent = Agent(config, hal, transport=transport)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await drive(clock, agent.ready.wait())
    assert transport.relays_at_start is not None and not any(transport.relays_at_start)
    stop.set()
    await asyncio.wait_for(task, 1)


async def _running_agent(config, hal, clock):
    transport = FakeTransport()
    agent = Agent(config, hal, transport=transport)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await drive(clock, agent.ready.wait())
    await clock.advance(0.1)  # polling relay pertama: float terbaca
    return agent, transport, stop, task


async def test_command_reaches_relay_and_acks(config, hal, clock):
    _, transport, stop, task = await _running_agent(config, hal, clock)
    try:
        transport.deliver(
            "fertimata/RIO-TANK-01/command",
            {
                "commandUid": "c-1",
                "actuatorCode": "DOSING-N",
                "command": "ON",
                "parameters": {"durationSeconds": 5},
                "expiresAt": "2026-10-22T08:05:00Z",
            },
        )
        await clock.advance(0.2)
        [(_, ack)] = transport.messages("/command/ack")
        assert ack["success"] is True and ack["actuatorState"] == "ON"
        assert hal.world.output_on("dose_n")
        statuses = [p for t, p in transport.messages("RIO-TANK-01/status")]
        assert statuses[-1]["actuators"]["DOSING-N"] == "ON"

        # Timer edge mematikan sendiri → status tanpa ACK (Kontrak §4.2).
        await clock.advance(5.1)
        assert not hal.world.output_on("dose_n")
        statuses = [p for t, p in transport.messages("RIO-TANK-01/status")]
        assert statuses[-1]["actuators"]["DOSING-N"] == "OFF"
        assert len(transport.messages("/command/ack")) == 1
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


async def test_heartbeat_reports_relay_states(config, hal, clock):
    _, transport, stop, task = await _running_agent(config, hal, clock)
    try:
        await clock.advance(config.mqtt.heartbeat_s)
        panel = [p for t, p in transport.messages("PANEL-01/status")]
        assert panel and panel[-1]["actuators"] == {
            "DIST-PUMP": "OFF",
            "CAMERA-PAN": "OFF",
            "TROLLEY-RUN": "OFF",
            "TROLLEY-DIR": "OFF",
        }
        jetson = [p for t, p in transport.messages("JETSON-01/status")]
        # JETSON-01 hanya punya aktuator virtual (Kontrak §5.1), dilaporkan OFF saat diam.
        assert jetson[-1]["actuators"] == {"TROLLEY-GOTO": "OFF", "TROLLEY-PATROL": "OFF"}
        assert jetson[-1]["metadata"]["agentVersion"]
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


# --- F5: daya ------------------------------------------------------------------------------------


def _jetson_statuses(transport) -> list[dict]:
    return [p for _, p in transport.messages("JETSON-01/status")]


async def test_f5_5_outage_stops_pump_and_recovers(config, hal, clock):
    _, transport, stop, task = await _running_agent(config, hal, clock)
    try:
        assert _jetson_statuses(transport)[-1]["powerSource"] == "MAINS"
        transport.deliver(
            "fertimata/PANEL-01/command",
            {
                "commandUid": "p-1",
                "actuatorCode": "DIST-PUMP",
                "command": "ON",
                "parameters": {"durationSeconds": 600},
            },
        )
        await clock.advance(0.2)
        assert hal.world.output_on("pump_dist")

        hal.world.set_mains(False)  # cabut PLN
        await clock.advance(config.polling.pzem_s * (config.bus.fail_threshold + 1))
        assert not hal.world.output_on("pump_dist")
        assert _jetson_statuses(transport)[-1]["powerSource"] == "BATTERY"
        panel = [p for _, p in transport.messages("PANEL-01/status")]
        assert panel[-1]["actuators"]["DIST-PUMP"] == "OFF"

        transport.deliver(
            "fertimata/PANEL-01/command",
            {
                "commandUid": "p-2",
                "actuatorCode": "DIST-PUMP",
                "command": "ON",
                "parameters": {"durationSeconds": 60},
            },
        )
        await clock.advance(0.2)
        ack = transport.messages("/command/ack")[-1][1]
        assert ack["success"] is False and ack["errorMessage"].startswith("INTERLOCK_POWER")

        hal.world.set_mains(True)  # PLN kembali
        await clock.advance(config.polling.pzem_s * 2)
        assert _jetson_statuses(transport)[-1]["powerSource"] == "MAINS"
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


async def test_power_source_known_before_mqtt_when_booting_on_battery(config, hal, clock):
    hal.world.set_mains(False)
    _, transport, stop, task = await _running_agent(config, hal, clock)
    try:
        first = _jetson_statuses(transport)[0]
        assert first["powerSource"] == "BATTERY"
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


async def test_soc_temperature_reported(config, hal, clock):
    _, transport, stop, task = await _running_agent(config, hal, clock)
    try:
        data = [p["data"] for _, p in transport.messages("JETSON-01/telemetry")]
        soc = [d["jetson.soc_temperature"] for d in data if "jetson.soc_temperature" in d]
        assert soc and soc[0] == 48.0
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


async def test_tank_solution_reported_after_boot(config, hal, clock):
    _, transport, stop, task = await _running_agent(config, hal, clock)
    try:
        await clock.advance(2 * config.analog.settle_s + 1)
        data = [p["data"] for _, p in transport.messages("RIO-TANK-01/telemetry")]
        merged = {k: v for d in data for k, v in d.items()}
        assert {"tank.ph", "tank.ec", "tank.water_temperature", "tank.float_low_ok"} <= set(merged)
        assert not hal.world.output_on("ec_power")
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


async def test_trolley_limits_reported_and_homing_works_in_agent(config, hal, clock):
    agent, transport, stop, task = await _running_agent(config, hal, clock)
    try:
        data = [p["data"] for _, p in transport.messages("JETSON-01/telemetry")]
        assert any(d.get("trolley.home_limit") == 0 for d in data)
        homing = asyncio.ensure_future(agent.trolley.homing())
        for _ in range(400):  # simulator agent bergerak sendiri mengikuti jam palsu
            if homing.done():
                break
            await clock.advance(0.05)
        await homing
        data = [p["data"] for _, p in transport.messages("JETSON-01/telemetry")]
        assert data[-1] == {"trolley.home_limit": 1, "trolley.end_limit": 0, "trolley.station": 0}
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


async def test_agent_wires_patrol_schedule(config, hal, clock):
    agent = Agent(config, hal)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await drive(clock, agent.ready.wait())
    assert agent.patrol is not None and len(agent.patrol.tasks) == 1  # jadwal D3 aktif
    assert agent.patrol.on_read in agent.poller.read_listeners
    stop.set()
    await asyncio.wait_for(task, 1)
    assert agent.patrol.tasks == []


async def test_agent_executes_virtual_trolley_command(config, hal, clock):
    # Kontrak §5.1 ujung ke ujung: ACK saat mulai, status ON → OFF, hasil lewat alert INFO.
    transport = FakeTransport()
    agent = Agent(config, hal, transport=transport)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    try:
        await drive(clock, agent.ready.wait())
        transport.published.clear()
        transport.deliver(
            "fertimata/JETSON-01/command",
            {
                "commandUid": "goto-1",
                "actuatorCode": "TROLLEY-GOTO",
                "command": "GOTO_STATION",
                "parameters": {"station": 1},
            },
        )
        await drive(clock, _until(lambda: transport.messages("/alert")), step=0.02)
        [(_, ack)] = transport.messages("JETSON-01/command/ack")
        assert ack["success"] is True and ack["actuatorState"] == "ON"
        states = [p["actuators"]["TROLLEY-GOTO"] for _, p in transport.messages("JETSON-01/status")]
        assert states[0] == "ON" and states[-1] == "OFF"
        [(_, alert)] = transport.messages("JETSON-01/alert")
        assert alert["type"] == "TROLLEY_ARRIVED" and alert["severity"] == "INFO"
        assert agent.trolley is not None and agent.trolley.position == 1

        transport.deliver(
            "fertimata/RIO-TANK-01/command",
            {"commandUid": "dose-1", "actuatorCode": "DOSING-LOOP", "command": "DOSE_TO_TARGET"},
        )
        await drive(clock, _until(lambda: transport.messages("RIO-TANK-01/command/ack")))
        [(_, ack)] = transport.messages("RIO-TANK-01/command/ack")
        # Loop dosing dipindah ke layanan tim AI (28 Okt): DOSING-LOOP bukan aktuator edge lagi.
        assert ack["success"] is False and ack["errorMessage"].startswith("UNSUPPORTED")
    finally:
        stop.set()
        await asyncio.wait_for(task, 1)


async def _until(predicate) -> None:
    """Tunggu kondisi; dipakai di dalam `drive` yang memajukan jam palsu."""
    for _ in range(1_000_000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("kondisi tidak tercapai")
