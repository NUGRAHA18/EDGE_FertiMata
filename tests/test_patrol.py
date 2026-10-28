"""Patroli kamera (F8.2–F8.5): urutan §9.4, pemulihan §9.6, gerbang blur §13, jadwal D3, disk."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from fertimata_edge.bus import Priority
from fertimata_edge.captures import GB, CaptureStore, disk_free_bytes
from fertimata_edge.config import ConfigError
from fertimata_edge.hal import FakeClock, Frame, create_hal
from fertimata_edge.patrol import Patrol
from fertimata_edge.trolley import Trolley

from .conftest import make_config
from .rig import build_rig, close_rig

STATIONS = 3


class Disk:
    """Ruang disk palsu (GB) untuk F8.5."""

    def __init__(self, free_gb: float = 500.0) -> None:
        self.free_gb = free_gb

    def __call__(self, path) -> int:
        return int(self.free_gb * GB)


@pytest.fixture
def small_data(dev_data, tmp_path):
    dev_data["trolley"]["stations"] = STATIONS
    dev_data["camera"]["root"] = str(tmp_path / "citra")
    return dev_data


async def make_rig(data, clock: FakeClock | None = None):
    config = make_config(data)
    clock = clock or FakeClock()
    hal = create_hal(config, clock)
    await hal.camera.open()
    rig = await build_rig(config, hal, clock)
    rig.trolley = Trolley(config, rig.outputs, rig.safety, hal.gpio, clock, rig.sink)
    rig.disk = Disk()
    rig.store = CaptureStore(config, disk_free=rig.disk, offload=inline)
    rig.patrol = Patrol(
        config,
        rig.trolley,
        rig.outputs,
        rig.safety,
        hal.camera,
        clock,
        rig.store,
        power_source=lambda: rig.power["source"],
        sink=rig.sink,
    )
    rig.safety.start()
    rig.trolley.start()
    return rig


async def shutdown(rig) -> None:
    await rig.patrol.stop()
    await rig.trolley.stop()
    await close_rig(rig)


@pytest.fixture
async def rig(small_data):
    rig = await make_rig(small_data)
    yield rig
    await shutdown(rig)


async def inline(func, *args):
    """Pengganti `asyncio.to_thread`: tulis tanpa thread, hasil test tidak bergantung disk."""
    return func(*args)


async def until_done(rig, awaitable, limit_s: float = 200.0, step: float = 0.02):
    task = asyncio.ensure_future(awaitable)
    elapsed = 0.0
    while not task.done() and elapsed < limit_s:
        await rig.advance(step, step=step)
        elapsed += step
    if not task.done():
        # Batalkan supaya tugas tidak menggantung saat teardown. Penghentian trolley di `finally`
        # butuh jam palsu terus maju (jeda bus), jadi jam tetap dimajukan sampai tugas selesai.
        task.cancel()
        for _ in range(1000):
            if task.done():
                break
            await rig.advance(step, step=step)
        raise AssertionError("patroli tidak selesai")
    return task.result()


def day_dir(rig):
    return rig.store.day_dir(rig.clock.now())


def sidecar(rig, capture_id: str) -> dict:
    return json.loads((day_dir(rig) / f"{capture_id}.json").read_text(encoding="utf-8"))


def pan_writes(rig) -> list[bool]:
    ch = rig.config.outputs["pan_west"].ch
    return [
        t.value
        for t in rig.hal.bus.log
        if t.op == "write_coil" and t.unit == 20 and t.address == ch and t.ok
    ]


# --- F8.4 urutan patroli §9.4 -------------------------------------------------------------------


async def test_full_patrol_saves_two_photos_per_station(rig):
    result = await until_done(rig, rig.patrol.run(trigger="schedule"))
    assert result.status == "OK" and result.reason is None
    assert len(result.saved) == 2 * STATIONS and not result.missed
    # Mulai 08:00 UTC = 15:00 WIB (agent.utc_offset_h 7): folder dan nama berkas waktu lokal (D1).
    folder = day_dir(rig)
    assert folder.name == "2026-10-22"
    names = sorted(p.name for p in folder.iterdir())
    assert not [n for n in names if n.endswith(".tmp")]
    first = result.saved[0]
    assert first.startswith("20261022-15") and first.endswith("_S01_timur")
    assert f"{first}.png" in names and f"{first}.json" in names
    meta = sidecar(rig, first)
    assert meta["stationCode"] == "ST-01" and meta["panDirection"] == "TIMUR"
    assert meta["captureId"] == first and meta["file"] == f"{first}.png"
    assert meta["capturedAt"].endswith("Z") and meta["localTime"].endswith("+07:00")
    assert meta["patrolId"] == result.patrol_id and meta["trigger"] == "schedule"
    assert meta["camera"]["encoding"] == "png" and meta["blurry"] is False
    # Akhir patroli: pan kembali timur, trolley di HOME, RUN mati.
    assert not rig.physical("pan_west") and not rig.physical("trolley_run")
    assert rig.world.trolley.position_mm == 0 and rig.trolley.position == 0
    summary = json.loads((folder / f"patroli_{result.patrol_id}.json").read_text(encoding="utf-8"))
    assert summary["status"] == "OK" and summary["saved"] == result.saved
    # Hasil lewat satu alert INFO (Kontrak §4.4); tidak ada peringatan.
    assert [(a["type"], a["severity"]) for a in rig.sink.alerts] == [("PATROL_DONE", "INFO")]
    done = rig.sink.alerts[0]
    assert done["metadata"]["saved"] == 2 * STATIONS and done["metadata"]["status"] == "OK"
    assert done["metadata"]["trigger"] == "schedule"


async def test_photos_taken_only_when_stopped_at_station_after_settle(rig):
    seen = []
    stopped = []
    capture = rig.hal.camera.capture

    async def watched_capture():
        seen.append(
            (
                rig.world.trolley.station_at(),
                rig.physical("trolley_run"),
                rig.physical("pan_west"),
                rig.clock.monotonic() - rig.outputs.states["pan_west"].changed_at,
            )
        )
        stopped.append(rig.clock.monotonic() - rig.outputs.states["trolley_run"].changed_at)
        return await capture()

    rig.hal.camera.capture = watched_capture
    await until_done(rig, rig.patrol.run())
    assert [s for s, _, _, _ in seen] == [1, 1, 2, 2, 3, 3]
    assert not any(running for _, running, _, _ in seen)
    # Arah sesuai Kontrak §5 (ON = barat); stasiun genap barat dulu (alternate_pan).
    assert [west for _, _, west, _ in seen] == [False, True, True, False, False, True]
    settle = rig.config.trolley.pan_settle_s
    assert all(since >= settle - 1e-6 for _, _, _, since in seen[1:])
    # §9.3: tunggu getaran reda setelah berhenti sebelum foto pertama di stasiun.
    assert min(stopped) >= rig.config.trolley.settle_s - 1e-6


async def test_alternate_pan_saves_moves(rig):
    await until_done(rig, rig.patrol.run())
    # S1 timur→barat, S2 barat→timur, S3 timur→barat, lalu kembali timur: 4 gerakan, bukan 6.
    assert pan_writes(rig)[-4:] == [True, False, True, False]


async def test_raw_commands_busy_during_patrol_and_released_after(rig):
    original = rig.hal.camera.capture
    refusals = []

    async def capture_and_check():
        # Saat memotret trolley diam di antara dua perjalanan: kunci patroli harus tetap berlaku.
        refusals.append(
            [
                rig.safety.check("pan_west", True),
                rig.safety.check("trolley_dir", True),
                rig.safety.check("trolley_run", True),
            ]
        )
        assert rig.safety.check("trolley_run", False) is None  # S3: berhenti selalu boleh
        return await original()

    rig.hal.camera.capture = capture_and_check
    await until_done(rig, rig.patrol.run())
    assert len(refusals) == 2 * STATIONS
    assert all(r is not None and r.reason == "BUSY" for row in refusals for r in row)
    assert (await rig.command("CAMERA-PAN", "ON")).success


async def test_second_run_while_running_is_skipped(rig):
    first = asyncio.ensure_future(rig.patrol.run())
    await rig.advance(0.5)
    second = await rig.patrol.run()
    assert second.status == "SKIPPED" and second.reason == "PATROL_BUSY"
    assert (await until_done(rig, first)).status == "OK"


# --- §9.6 kesalahan dan pemulihan ---------------------------------------------------------------


async def test_camera_failure_skips_photo_and_continues(rig):
    original = rig.hal.camera.capture

    async def broken_at_station_2():
        rig.hal.camera.fail = rig.world.trolley.station_at() == 2
        return await original()

    rig.hal.camera.capture = broken_at_station_2
    result = await until_done(rig, rig.patrol.run())
    assert result.status == "PARTIAL"
    assert [m["station"] for m in result.missed] == [2, 2]
    assert len(result.saved) == 2 * (STATIONS - 1) and result.station == 0
    [alert] = rig.sink.of_type("CAMERA_FAIL")
    assert alert["severity"] == "WARNING" and alert["device"] == "JETSON-01"
    assert "2 dari 6" in alert["message"]
    assert rig.sink.of_type("PATROL_FAILED") == []


async def test_power_outage_mid_patrol_stops_in_place(rig):
    task = asyncio.ensure_future(rig.patrol.run())
    while rig.trolley.position != 1 or not rig.physical("trolley_run"):
        await rig.advance(0.02, step=0.02)
    # Modul power: S7 menghentikan trolley dan safety memblokirnya.
    rig.power["source"] = "BATTERY"
    await rig.run(rig.outputs.set("trolley_run", False, priority=Priority.STOP, cause="power"))
    result = await until_done(rig, task)
    assert result.status == "FAILED" and result.reason == "INTERLOCK_POWER"
    assert rig.world.trolley.position_mm > 0  # tidak homing saat PLN padam
    assert not rig.physical("trolley_run") and not rig.physical("pan_west")
    [alert] = rig.sink.of_type("PATROL_FAILED")
    assert "PLN" in alert["message"] and alert["metadata"]["reason"] == "INTERLOCK_POWER"


async def test_no_power_at_start_skips_without_moving(rig):
    rig.power["source"] = "BATTERY"
    writes = len(rig.hal.bus.log)
    result = await until_done(rig, rig.patrol.run(), limit_s=10)
    assert result.status == "SKIPPED" and result.reason == "INTERLOCK_POWER"
    assert not [t for t in rig.hal.bus.log[writes:] if t.op.startswith("write")]
    assert rig.sink.of_type("PATROL_FAILED")


async def test_trolley_error_fails_patrol(rig):
    rig.world.trolley.magnets_mm = rig.world.trolley.magnets_mm[:1]  # magnet S2, S3 lepas
    result = await until_done(rig, rig.patrol.run())
    assert result.status == "FAILED" and result.station == 1
    assert result.reason in ("TROLLEY_NO_REED", "TROLLEY_LIMIT_HIT")
    assert len(result.saved) == 2  # foto S1 tetap tersimpan
    assert rig.sink.of_type("PATROL_FAILED")
    assert (await rig.command("CAMERA-PAN", "ON")).success  # kunci perintah mentah dilepas


async def test_locked_trolley_skips(rig):
    rig.world.break_limit_wire("home")
    rig.world.break_limit_wire("end")
    await rig.advance(0.1)
    result = await until_done(rig, rig.patrol.run(), limit_s=10)
    assert result.status == "SKIPPED" and result.reason == "TROLLEY_LOCKED"


# --- F8.3 gerbang blur --------------------------------------------------------------------------


async def test_blurry_frame_is_retaken_and_sharper_one_kept(rig):
    rig.hal.camera.sharpness_queue = [10.0, 140.0]
    result = await until_done(rig, rig.patrol.run())
    meta = sidecar(rig, result.saved[0])
    assert meta["attempts"] == 2 and meta["sharpness"] == 140.0 and meta["blurry"] is False
    assert rig.hal.camera.captures == 2 * STATIONS + 1  # hanya diulang sekali
    assert result.blurry == []


async def test_still_blurry_after_retake_is_kept_and_flagged(rig):
    rig.hal.camera.sharpness_queue = [30.0, 5.0]
    result = await until_done(rig, rig.patrol.run())
    meta = sidecar(rig, result.saved[0])
    assert meta["sharpness"] == 30.0 and meta["blurry"] is True
    assert meta["blurThreshold"] == rig.config.camera.blur_threshold
    assert result.blurry == [result.saved[0]] and result.status == "OK"


async def test_blur_gate_off_without_threshold(small_data):
    small_data["camera"]["blur_threshold"] = None
    rig = await make_rig(small_data)
    try:
        rig.hal.camera.sharpness_queue = [1.0]
        result = await until_done(rig, rig.patrol.run())
        assert rig.hal.camera.captures == 2 * STATIONS
        assert sidecar(rig, result.saved[0])["blurry"] is False
    finally:
        await shutdown(rig)


# --- metadata lingkungan ------------------------------------------------------------------------


async def test_environment_in_metadata_and_stale_values_dropped(rig):
    rig.patrol.on_read("air", {"air.temperature": 28.4, "air.humidity": 71.0})
    rig.patrol.on_read("pzem", {"mains.voltage": 221.0})  # bukan sensor lingkungan
    result = await until_done(rig, rig.patrol.run())
    env = sidecar(rig, result.saved[0])["environment"]
    assert env == {"air.temperature": 28.4, "air.humidity": 71.0}

    await rig.clock.advance(2 * rig.config.polling.air_light_s + 1)
    result = await until_done(rig, rig.patrol.run())
    assert sidecar(rig, result.saved[0])["environment"] == {}


# --- F8.5 ruang disk ----------------------------------------------------------------------------


async def test_low_disk_warns_but_patrol_runs(rig):
    rig.disk.free_gb = rig.config.camera.disk_warn_free_gb - 1
    result = await until_done(rig, rig.patrol.run())
    assert result.status == "OK"
    [alert] = rig.sink.of_type("DISK_LOW")[:1]
    assert alert["severity"] == "WARNING" and alert["metadata"]["freeGb"] > 0


async def test_full_disk_skips_patrol_and_deletes_nothing(rig):
    old = rig.store.day_dir(rig.clock.now() - timedelta(days=3))
    old.mkdir(parents=True)
    (old / "lama.png").write_bytes(b"data riset")
    rig.disk.free_gb = rig.config.camera.disk_stop_free_gb - 1
    result = await until_done(rig, rig.patrol.run(), limit_s=10)
    assert result.status == "SKIPPED" and result.reason == "DISK_FULL"
    assert rig.sink.of_type("DISK_FULL")[0]["severity"] == "CRITICAL"
    assert (old / "lama.png").read_bytes() == b"data riset"


async def test_disk_filling_mid_patrol_stops_saving(rig):
    original = rig.hal.camera.capture

    async def capture_then_fill():
        frame = await original()
        if rig.hal.camera.captures == 3:
            rig.disk.free_gb = 0.5
        return frame

    rig.hal.camera.capture = capture_then_fill
    result = await until_done(rig, rig.patrol.run())
    assert result.status == "FAILED" and result.reason == "DISK_FULL"
    assert len(result.saved) == 2


# --- CaptureStore -------------------------------------------------------------------------------


async def test_store_never_overwrites_existing_capture(config, tmp_path, clock):
    store = CaptureStore(
        config.model_copy(
            update={"camera": config.camera.model_copy(update={"root": str(tmp_path)})}
        )
    )
    frame = Frame(b"png", "png", 1, 1, 1.0)
    now = clock.now()
    ids = [
        (
            await store.save(
                frame, patrol_start=now, captured_at=now, station=3, direction="barat", metadata={}
            )
        ).capture_id
        for _ in range(3)
    ]
    assert ids == [
        "20261022-1500_S03_barat",
        "20261022-1500_S03_barat-2",
        "20261022-1500_S03_barat-3",
    ]


def test_disk_free_uses_nearest_existing_parent(tmp_path):
    assert disk_free_bytes(tmp_path / "belum" / "ada") > 0


# --- D3 jadwal lokal ----------------------------------------------------------------------------


async def scheduled_runs(small_data, schedule, catchup, minutes):
    small_data["camera"]["patrol_schedule"] = schedule
    small_data["camera"]["patrol_catchup_min"] = catchup
    clock = FakeClock()  # 08:00 UTC = 15:00 WIB
    rig = await make_rig(small_data, clock)
    runs = []

    async def fake_run(trigger="manual"):
        runs.append((trigger, rig.store.local(clock.now()).strftime("%H:%M")))

    rig.patrol.run = fake_run
    try:
        rig.patrol.start()
        await clock.advance(0)  # tugas jadwal mulai di 15:00 tepat
        for _ in range(minutes):
            await clock.advance(60)
    finally:
        await shutdown(rig)
    return runs


async def test_schedule_fires_once_at_local_time(small_data):
    runs = await scheduled_runs(small_data, ["15:30"], 30, 90)
    assert runs == [("schedule", "15:30")]


async def test_schedule_catches_up_after_late_start(small_data):
    assert await scheduled_runs(small_data, ["14:45"], 30, 5) == [("schedule", "15:00")]


async def test_schedule_does_not_catch_up_too_late(small_data):
    assert await scheduled_runs(small_data, ["14:00"], 30, 5) == []


async def test_schedule_next_day(small_data):
    runs = await scheduled_runs(small_data, ["15:10"], 0, 24 * 60 + 15)
    assert runs == [("schedule", "15:10"), ("schedule", "15:10")]


async def test_schedule_crash_does_not_kill_loop(small_data):
    small_data["camera"]["patrol_schedule"] = ["15:01", "15:03"]
    clock = FakeClock()
    rig = await make_rig(small_data, clock)
    calls = []

    async def broken_run(trigger="manual"):
        calls.append(trigger)
        raise RuntimeError("bug")

    rig.patrol.run = broken_run
    try:
        rig.patrol.start()
        for _ in range(5):
            await clock.advance(60)
        assert calls == ["schedule", "schedule"] and not rig.patrol.tasks[0].done()
    finally:
        await shutdown(rig)


async def test_no_schedule_without_real_gpio(small_data):
    from fertimata_edge.hal.real_stubs import LockedGpio

    rig = await make_rig(small_data)
    try:
        rig.trolley.gpio = LockedGpio()
        rig.patrol.start()
        assert rig.patrol.tasks == []
        result = await until_done(rig, rig.patrol.run(), limit_s=10)
        assert result.status == "SKIPPED" and result.reason == "TROLLEY_LOCKED"
    finally:
        await shutdown(rig)


# --- config -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["camera"].update(pan_output="tidak_ada"), "camera.pan_output"),
        (lambda d: d["camera"].update(disk_stop_free_gb=30), "disk_stop_free_gb"),
        (lambda d: d["camera"].update(blur_threshold=0), "camera.blur_threshold"),
        (lambda d: d["camera"].update(patrol_schedule=["19:00", "19:00"]), "ganda"),
        (lambda d: d["agent"].update(utc_offset_h=20), "agent.utc_offset_h"),
    ],
)
def test_camera_config_rejected(dev_data, change, message):
    change(dev_data)
    with pytest.raises(ConfigError, match=message):
        make_config(dev_data)
