"""Aktuator virtual (Kontrak §5.1): TROLLEY-GOTO (F7.5) dan TROLLEY-PATROL (F8.4)."""

from __future__ import annotations

import asyncio

import pytest

from fertimata_edge.commands import Command
from fertimata_edge.link.payloads import CommandPayload
from fertimata_edge.virtual import VirtualActuators

from .rig import close_rig
from .test_patrol import STATIONS, make_rig


@pytest.fixture
def small_data(dev_data, tmp_path):
    dev_data["trolley"]["stations"] = STATIONS
    dev_data["camera"]["root"] = str(tmp_path / "citra")
    return dev_data


@pytest.fixture
async def rig(small_data):
    rig = await make_rig(small_data)
    rig.virtual = VirtualActuators(
        rig.config, rig.trolley, rig.patrol, rig.safety, rig.clock, sink=rig.sink
    )
    rig.notified = []
    rig.virtual.listeners.append(rig.notified.append)
    yield rig
    await finish(rig, rig.virtual.stop())
    await rig.patrol.stop()
    await rig.trolley.stop()
    await close_rig(rig)


async def finish(rig, awaitable, limit_s: float = 200.0, step: float = 0.02):
    """Jalankan `awaitable` sambil memajukan jam dan simulator (trolley bergerak)."""
    task = asyncio.ensure_future(awaitable)
    elapsed = 0.0
    while not task.done() and elapsed < limit_s:
        await rig.advance(step, step=step)
        elapsed += step
    assert task.done(), "tidak selesai"
    return task.result()


async def until(rig, predicate, limit_s: float = 200.0, step: float = 0.02) -> None:
    elapsed = 0.0
    while not predicate() and elapsed < limit_s:
        await rig.advance(step, step=step)
        elapsed += step
    assert predicate(), "kondisi tidak tercapai"


async def send(rig, code: str, action: str, parameters: dict | None = None):
    kind = rig.config.virtual_by_actuator(code)
    assert kind is not None, code
    payload = CommandPayload.model_validate(
        {
            "commandUid": "uid",
            "actuatorCode": code,
            "command": action,
            "parameters": parameters or {},
        }
    )
    return await finish(rig, rig.virtual.execute(Command("JETSON-01", kind, payload)))


def states(rig) -> dict[str, str]:
    return rig.virtual.actuator_states("JETSON-01")


def goto_alerts(rig) -> list[str]:
    return [a["type"] for a in rig.sink.alerts]


# --- TROLLEY-GOTO (F7.5) --------------------------------------------------------------------------


async def test_goto_station_acks_on_start_and_reports_arrival(rig):
    ack = await send(rig, "TROLLEY-GOTO", "GOTO_STATION", {"station": 2})
    assert ack.success and ack.actuator_state == "ON"
    assert states(rig) == {"TROLLEY-GOTO": "ON", "TROLLEY-PATROL": "OFF"}
    assert rig.notified == ["JETSON-01"]
    # Selama jalan, perintah arah/pan mentah dari backend ditolak BUSY; RUN OFF tetap boleh.
    busy = await rig.command("CAMERA-PAN", "ON")
    assert not busy.success and busy.error_message.startswith("BUSY")

    await until(rig, lambda: not rig.virtual.is_on("trolley_goto"))
    assert rig.trolley.position == 2 and rig.trolley.at_station
    assert not rig.physical("trolley_run")
    assert states(rig)["TROLLEY-GOTO"] == "OFF" and rig.notified[-1] == "JETSON-01"
    [arrived] = rig.sink.alerts
    assert arrived["type"] == "TROLLEY_ARRIVED" and arrived["severity"] == "INFO"
    assert arrived["actuator_code"] == "TROLLEY-GOTO" and arrived["metadata"] == {"station": 2}
    assert (await rig.command("CAMERA-PAN", "ON")).success  # kunci dilepas


async def test_home_returns_trolley_to_start(rig):
    rig.world.trolley.position_mm = rig.world.trolley.magnets_mm[1] + 200
    ack = await send(rig, "TROLLEY-GOTO", "HOME")
    assert ack.success
    await until(rig, lambda: not rig.virtual.is_on("trolley_goto"))
    assert rig.world.trolley.position_mm == 0 and rig.trolley.position == 0
    assert rig.sink.alerts[-1]["message"] == "Trolley sudah kembali ke posisi awal."


@pytest.mark.parametrize(
    "parameters",
    [{}, {"station": "2"}, {"station": 2.0}, {"station": True}, {"station": 0}, {"station": 99}],
)
async def test_goto_station_needs_valid_integer(rig, parameters):
    ack = await send(rig, "TROLLEY-GOTO", "GOTO_STATION", parameters)
    assert not ack.success and ack.error_message.startswith("INVALID")
    assert f"1–{STATIONS}" in ack.error_message
    assert not rig.virtual.is_on("trolley_goto") and not rig.physical("trolley_run")


async def test_stop_halts_goto_without_failure_alert(rig):
    assert (await send(rig, "TROLLEY-GOTO", "GOTO_STATION", {"station": 3})).success
    await until(rig, lambda: rig.physical("trolley_run"))
    await rig.advance(1.0)
    ack = await send(rig, "TROLLEY-GOTO", "STOP")
    assert ack.success and ack.actuator_state == "OFF"
    assert not rig.physical("trolley_run") and not rig.virtual.is_on("trolley_goto")
    assert rig.sink.alerts == []  # dihentikan operator: bukan kegagalan, bukan "sampai"
    assert (await rig.command("CAMERA-PAN", "ON")).success


async def test_stop_is_never_refused_and_halts_raw_run(rig):
    rig.power["source"] = "BATTERY"
    idle = await send(rig, "TROLLEY-GOTO", "STOP")
    assert idle.success and idle.actuator_state == "OFF"
    rig.power["source"] = "MAINS"
    assert (await rig.command("TROLLEY-DIR", "ON")).success
    assert (await rig.command("TROLLEY-RUN", "ON", duration=30)).success
    await rig.advance(0.5)
    # S3: STOP berarti RUN OFF, walaupun gerak mentah bukan urutan virtual.
    assert (await send(rig, "TROLLEY-PATROL", "STOP")).success is True
    assert (await send(rig, "TROLLEY-GOTO", "STOP")).success
    assert not rig.physical("trolley_run")


async def test_raw_run_off_during_goto_reports_failure(rig):
    assert (await send(rig, "TROLLEY-GOTO", "GOTO_STATION", {"station": 3})).success
    await until(rig, lambda: rig.physical("trolley_run"))
    await rig.advance(0.5)
    assert (await rig.command("TROLLEY-RUN", "OFF")).success  # STOP dari tombol relay mentah
    await until(rig, lambda: not rig.virtual.is_on("trolley_goto"))
    [failed] = rig.sink.alerts
    assert failed["type"] == "TROLLEY_GOTO_FAILED" and failed["severity"] == "WARNING"
    assert failed["metadata"] == {"station": 3, "reason": "TROLLEY_STOPPED"}


async def test_goto_failure_already_alerted_by_trolley_is_not_doubled(rig):
    rig.world.trolley.magnets_mm = rig.world.trolley.magnets_mm[:1]  # magnet S2, S3 lepas
    assert (await send(rig, "TROLLEY-GOTO", "GOTO_STATION", {"station": 3})).success
    await until(rig, lambda: not rig.virtual.is_on("trolley_goto"))
    assert "TROLLEY_GOTO_FAILED" not in goto_alerts(rig)
    assert set(goto_alerts(rig)) & {"TROLLEY_NO_REED", "TROLLEY_LIMIT_HIT"}
    assert not rig.physical("trolley_run")


async def test_second_trolley_command_is_busy(rig):
    assert (await send(rig, "TROLLEY-GOTO", "GOTO_STATION", {"station": 3})).success
    again = await send(rig, "TROLLEY-GOTO", "HOME")
    assert not again.success and again.error_message.startswith("BUSY")
    assert again.actuator_state == "ON"
    patrol = await send(rig, "TROLLEY-PATROL", "START")
    assert not patrol.success and patrol.error_message.startswith("BUSY")
    await until(rig, lambda: not rig.virtual.is_on("trolley_goto"))


async def test_raw_movement_in_progress_is_busy(rig):
    assert (await rig.command("TROLLEY-DIR", "ON")).success
    assert (await rig.command("TROLLEY-RUN", "ON", duration=30)).success
    ack = await send(rig, "TROLLEY-GOTO", "HOME")
    assert not ack.success and ack.error_message == "BUSY: trolley sedang berjalan"


@pytest.mark.parametrize(
    ("code", "action", "params"),
    [("TROLLEY-GOTO", "GOTO_STATION", {"station": 1}), ("TROLLEY-PATROL", "START", None)],
)
async def test_backup_power_refuses_start(rig, code, action, params):
    rig.power["source"] = "BATTERY"
    ack = await send(rig, code, action, params)
    assert not ack.success and ack.error_message.startswith("INTERLOCK_POWER")
    await rig.advance(1.0)
    assert not rig.physical("trolley_run") and states(rig)[code] == "OFF"


async def test_dosing_running_refuses_trolley(rig):
    # S4–S6: [dosing, trolley_run] eksklusif di config.
    assert (await rig.command("STIRRER", "ON", duration=60)).success
    ack = await send(rig, "TROLLEY-GOTO", "HOME")
    assert not ack.success and ack.error_message.startswith("INTERLOCK_EXCLUSIVE")


async def test_locked_trolley_refuses(rig):
    rig.world.break_limit_wire("home")
    rig.world.break_limit_wire("end")
    await rig.advance(0.1)
    ack = await send(rig, "TROLLEY-GOTO", "HOME")
    assert not ack.success and ack.error_message.startswith("TROLLEY_LOCKED")


# --- TROLLEY-PATROL (F8.4) ------------------------------------------------------------------------


async def test_manual_patrol_runs_and_reports_done(rig):
    ack = await send(rig, "TROLLEY-PATROL", "START")
    assert ack.success and ack.actuator_state == "ON"
    assert states(rig) == {"TROLLEY-GOTO": "OFF", "TROLLEY-PATROL": "ON"}
    await until(rig, lambda: rig.patrol.running)
    assert states(rig)["TROLLEY-PATROL"] == "ON"
    await until(rig, lambda: not rig.virtual.is_on("trolley_patrol"))
    result = rig.patrol.last
    assert result.status == "OK" and result.trigger == "manual"
    assert len(result.saved) == 2 * STATIONS
    [done] = rig.sink.alerts
    assert done["type"] == "PATROL_DONE" and done["severity"] == "INFO"
    assert done["actuator_code"] == "TROLLEY-PATROL" and done["metadata"]["trigger"] == "manual"
    assert states(rig)["TROLLEY-PATROL"] == "OFF" and rig.notified[-1] == "JETSON-01"


async def test_stop_patrol_stops_trolley_in_place(rig):
    assert (await send(rig, "TROLLEY-PATROL", "START")).success
    await until(rig, lambda: rig.trolley.position == 2 and rig.physical("trolley_run"))
    ack = await send(rig, "TROLLEY-PATROL", "STOP")
    # ACK OFF baru dikirim setelah patroli benar-benar berhenti.
    assert ack.success and ack.actuator_state == "OFF"
    assert not rig.patrol.running and not rig.physical("trolley_run")
    assert rig.world.trolley.position_mm > 0  # berhenti di tempat, tidak pulang
    result = rig.patrol.last
    assert result.status == "STOPPED" and result.reason == "PATROL_STOPPED"
    assert goto_alerts(rig) == ["PATROL_DONE"]
    assert rig.sink.alerts[0]["title"] == "Patroli kamera dihentikan"
    assert (await rig.command("TROLLEY-DIR", "OFF")).success  # kunci dilepas


async def test_stop_patrol_while_taking_photos(rig):
    assert (await send(rig, "TROLLEY-PATROL", "START")).success
    # Diam di S1 menunggu getaran reda / memotret: tidak ada trolley yang perlu dihentikan.
    await until(
        rig,
        lambda: (
            rig.trolley.position == 1 and rig.trolley.at_station and not rig.physical("trolley_run")
        ),
    )
    assert (await send(rig, "TROLLEY-PATROL", "STOP")).success
    result = rig.patrol.last
    assert result.status == "STOPPED" and result.station == 1
    # STOP saat menunggu getaran reda: tidak ada foto lagi yang diambil sesudahnya.
    assert result.saved == [] and rig.hal.camera.log == []


@pytest.mark.parametrize("phase", ["settle", "pan"])
async def test_stop_ack_is_quick_during_waits(rig, phase):
    # Backend menunggu ACK STOP (TTL perintah 30 s): jeda getaran/pan tidak boleh menahannya.
    assert (await send(rig, "TROLLEY-PATROL", "START")).success
    if phase == "settle":
        await until(rig, lambda: rig.trolley.at_station and rig.trolley.position == 1)
    else:
        await until(rig, lambda: rig.physical("pan_west"))  # baru berputar ke barat
    started = rig.clock.monotonic()
    ack = await send(rig, "TROLLEY-PATROL", "STOP")
    assert ack.success and rig.clock.monotonic() - started < 1.0
    assert rig.patrol.last.status == "STOPPED"


async def test_scheduled_patrol_is_reported_on(rig):
    task = asyncio.ensure_future(rig.patrol.run(trigger="schedule"))
    await until(rig, lambda: rig.patrol.running)
    assert states(rig)["TROLLEY-PATROL"] == "ON"
    again = await send(rig, "TROLLEY-PATROL", "START")
    assert not again.success and again.error_message.startswith("BUSY")
    await until(rig, task.done)
    assert states(rig)["TROLLEY-PATROL"] == "OFF"


async def test_disk_full_refuses_manual_patrol(rig):
    rig.disk.free_gb = 0.1
    ack = await send(rig, "TROLLEY-PATROL", "START")
    assert not ack.success and ack.error_message.startswith("DISK_FULL")
    assert not rig.virtual.is_on("trolley_patrol")


async def test_shutdown_cancels_running_goto(rig):
    assert (await send(rig, "TROLLEY-GOTO", "GOTO_STATION", {"station": 3})).success
    await until(rig, lambda: rig.physical("trolley_run"))
    await finish(rig, rig.virtual.stop())
    assert not rig.physical("trolley_run") and not rig.virtual.is_on("trolley_goto")
