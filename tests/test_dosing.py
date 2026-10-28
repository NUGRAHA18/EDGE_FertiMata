"""Loop dosing tertutup (F9): algoritme §8.2, aktuator virtual DOSING-LOOP, telemetri dosing.*."""

from __future__ import annotations

import dataclasses

import pytest

from fertimata_edge.analog import AnalogReader
from fertimata_edge.commands import Command
from fertimata_edge.config import ConfigError
from fertimata_edge.dosing import DosingLoop
from fertimata_edge.link.payloads import CommandPayload
from fertimata_edge.virtual import VirtualActuators

from .conftest import EXAMPLE_CONFIG, make_config
from .rig import close_rig
from .test_patrol import STATIONS, make_rig
from .test_virtual import finish, until

PUMPS = ("dose_n", "dose_p", "dose_k")
OWN = (*PUMPS, "stirrer")
STEP = 0.1


@pytest.fixture
def dosing_data(dev_data, tmp_path):
    dev_data["trolley"]["stations"] = STATIONS
    dev_data["camera"]["root"] = str(tmp_path / "citra")
    # Waktu simulasi dipersingkat; algoritmenya sama.
    dev_data["analog"].update(settle_s=1, samples=3)
    dev_data["dosing"].update(stir_s=5, stable_interval_s=2)
    dev_data["fake"].update(mix_tau_s=1.0)  # aduk 5 s mencampur stok sepenuhnya
    return dev_data


async def build(data):
    rig = await make_rig(data)
    rig.outputs.start()  # baca float berkala (S8 basi setelah float_max_age_s)
    rig.analog = AnalogReader(rig.config, rig.bus, rig.outputs, rig.safety, rig.clock, rig.sink)
    rig.dosing = DosingLoop(rig.config, rig.outputs, rig.safety, rig.analog, rig.clock, rig.sink)
    rig.virtual = VirtualActuators(
        rig.config,
        rig.trolley,
        rig.patrol,
        rig.safety,
        rig.clock,
        sink=rig.sink,
        dosing=rig.dosing,
    )
    rig.notified = []
    rig.virtual.listeners.append(rig.notified.append)
    # Catat urutan pompa yang menyala secara fisik (S6, §8.2 langkah 7).
    rig.pump_log = []
    relay = rig.world.relay_for("dose_n")
    channels = {rig.config.outputs[name].ch: name for name in OWN}

    def on_change(ch, on):
        if ch in channels:
            rig.pump_log.append((channels[ch], on, [n for n in PUMPS if rig.physical(n)]))

    relay.on_change.append(on_change)
    return rig


async def shutdown(rig):
    await finish(rig, rig.virtual.stop(), step=STEP)
    await rig.patrol.stop()
    await rig.trolley.stop()
    await close_rig(rig)


@pytest.fixture
async def rig(dosing_data):
    rig = await build(dosing_data)
    yield rig
    await shutdown(rig)


async def send(rig, action: str, parameters: dict | None = None, code: str = "DOSING-LOOP"):
    kind = rig.config.virtual_by_actuator(code)
    payload = CommandPayload.model_validate(
        {"commandUid": "uid", "actuatorCode": code, "command": action, "parameters": parameters}
    )
    device = rig.config.virtual_actuators[kind].device
    return await finish(rig, rig.virtual.execute(Command(device, kind, payload)), step=STEP)


async def dose(rig, target: float = 2.2, phase: str = "vegetatif"):
    return await send(rig, "DOSE_TO_TARGET", {"targetEc": target, "phase": phase})


async def wait_done(rig, limit_s: float = 2000.0):
    await until(rig, lambda: not rig.dosing.running, limit_s=limit_s, step=STEP)


def tank_ec(rig) -> float:
    return rig.world.tank_ec_ms_cm + rig.world.unmixed_ec_ms_cm


def dosing_telemetry(rig) -> list[dict]:
    return [d for dev, d in rig.sink.telemetry if "dosing.iteration" in d and dev == "RIO-TANK-01"]


def all_off(rig) -> bool:
    return not any(rig.physical(name) for name in OWN)


def reason(result) -> str:
    return result.error_message.split(":")[0]


# --- F9.2 algoritme -----------------------------------------------------------------------------


async def test_dose_to_target_converges_without_overshoot(rig):
    ack = await dose(rig, 2.2)
    assert ack.success and ack.actuator_state == "ON"
    assert rig.virtual.actuator_states("RIO-TANK-01") == {"DOSING-LOOP": "ON"}
    assert rig.notified == ["RIO-TANK-01"]
    await wait_done(rig)

    assert rig.virtual.actuator_states("RIO-TANK-01") == {"DOSING-LOOP": "OFF"}
    assert rig.notified[-1] == "RIO-TANK-01"
    run = rig.dosing.last
    assert run.status == "DONE"
    # Hanya menambah, tidak pernah melewati target (larutan pekat tidak bisa ditarik).
    # Toleransi diukur pada EC terbaca (kalibrasi + pembulatan), bukan EC "sebenarnya" simulator.
    assert run.ec_measured >= 2.2 - 0.05
    assert 2.2 - 0.06 <= tank_ec(rig) <= 2.2
    assert all_off(rig) and rig.safety.held == {} and rig.safety.managed_on == {}
    [done] = rig.sink.of_type("DOSING_DONE")
    assert done["severity"] == "INFO" and done["actuator_code"] == "DOSING-LOOP"
    meta = done["metadata"]
    assert meta["phase"] == "vegetatif" and meta["iterations"] == run.iteration
    assert meta["ecFinal"] == pytest.approx(tank_ec(rig), abs=0.02)
    # Rasio vegetatif 5,4 : 1 : 6,3 (debit dan kenaikan EC sama untuk ketiga stok di simulator).
    assert meta["kMl"] / meta["nMl"] == pytest.approx(6.3 / 5.4, rel=0.05)
    assert meta["nMl"] / meta["pMl"] == pytest.approx(5.4, rel=0.05)
    assert rig.world.dosed_ml["dose_n"] == pytest.approx(meta["nMl"], abs=0.5)
    assert rig.sink.of_type("DOSING_NOT_CONVERGING") == []


async def test_telemetry_one_message_per_iteration(rig):
    assert (await dose(rig, 2.2)).success
    await wait_done(rig)
    sent = dosing_telemetry(rig)
    run = rig.dosing.last
    assert [d["dosing.iteration"] for d in sent] == list(range(1, run.iteration + 1))
    assert set(sent[0]) == {
        "dosing.iteration",
        "dosing.ec_target",
        "dosing.ec_measured",
        "dosing.n_ml",
        "dosing.p_ml",
        "dosing.k_ml",
    }
    assert all(d["dosing.ec_target"] == 2.2 for d in sent)
    assert sent[0]["dosing.ec_measured"] == pytest.approx(1.8, abs=0.01)
    assert sent[0]["dosing.n_ml"] > 0
    measured = [d["dosing.ec_measured"] for d in sent]
    assert measured == sorted(measured)  # EC naik tiap iterasi
    last = sent[-1]
    assert last["dosing.n_ml"] == last["dosing.p_ml"] == last["dosing.k_ml"] == 0


async def test_pumps_one_at_a_time_in_n_p_k_order(rig):
    assert (await dose(rig, 2.2)).success
    await wait_done(rig)
    ons = [name for name, on, _ in rig.pump_log if on and name in PUMPS]
    assert ons and len(ons) % 3 == 0
    assert ons == ["dose_n", "dose_p", "dose_k"] * (len(ons) // 3)
    # S6: tidak pernah dua pompa peristaltik menyala bersamaan.
    assert all(len(running) <= 1 for _, _, running in rig.pump_log)
    # Aduk tidak menyala bersamaan dengan pompa pupuk dalam urutan ini (aduk sesudah dosis).
    assert rig.pump_log[0][:2] == ("stirrer", True)


async def test_ec_already_at_or_above_target_doses_nothing(rig):
    rig.world.tank_ec_ms_cm = 2.5
    assert (await dose(rig, 2.2)).success
    await wait_done(rig)
    assert rig.dosing.last.status == "DONE" and rig.dosing.last.iteration == 1
    assert all(ml == 0 for ml in rig.world.dosed_ml.values())
    assert not any(on for name, on, _ in rig.pump_log if name in PUMPS)
    [done] = rig.sink.of_type("DOSING_DONE")
    assert "tidak ada pupuk yang ditambahkan" in done["message"]


async def test_not_converging_after_max_iterations(rig):
    for name in PUMPS:
        rig.world.dose_ec_gain[name] = 0.0  # stok habis: pompa jalan, EC tidak naik
    assert (await dose(rig, 2.2)).success
    await wait_done(rig)
    max_iterations = rig.config.dosing.max_iterations
    run = rig.dosing.last
    assert run.status == "NOT_CONVERGING" and run.iteration == max_iterations
    [alert] = rig.sink.of_type("DOSING_NOT_CONVERGING")
    assert alert["severity"] == "WARNING" and alert["metadata"]["iterations"] == max_iterations
    assert rig.sink.of_type("DOSING_DONE") == []
    # Iterasi terakhir hanya memeriksa: dosis diberikan max_iterations − 1 kali.
    ons = [name for name, on, _ in rig.pump_log if on and name == "dose_n"]
    assert len(ons) == max_iterations - 1
    assert len(dosing_telemetry(rig)) == max_iterations
    assert all_off(rig)


async def test_unstable_ec_stops_loop(rig, monkeypatch):
    original = rig.analog.measure
    flip = {"n": 0}

    async def jumpy():
        reading = await original()
        flip["n"] += 1
        return dataclasses.replace(reading, ec_ms_cm=1.8 + 0.1 * (flip["n"] % 2))

    monkeypatch.setattr(rig.analog, "measure", jumpy)
    assert (await dose(rig, 2.2)).success
    await wait_done(rig)
    assert flip["n"] == rig.config.dosing.stable_max_reads
    [failed] = rig.sink.of_type("DOSING_FAILED")
    assert failed["metadata"]["reason"] == "EC_UNSTABLE"
    assert all(ml == 0 for ml in rig.world.dosed_ml.values())


def test_plan_takes_only_a_fraction_and_respects_limits(config):
    from fertimata_edge.dosing import DosingLoop as Loop

    loop = Loop.__new__(Loop)
    loop.config, loop.cfg = config, config.dosing
    loop.pumps = {"n": "dose_n", "p": "dose_p", "k": "dose_k"}
    cfg = config.dosing
    ratio = cfg.ratio["vegetatif"]
    plan = loop.plan(0.4, ratio)
    rise = (
        sum(
            plan[k] * getattr(cfg.pump_flow_ml_s, k) * getattr(cfg.ec_gain_ms_cm_per_ml_l, k)
            for k in plan
        )
        / cfg.drum_volume_l
    )
    assert rise == pytest.approx(0.4 * cfg.fraction_per_iteration)
    # ΔEC besar: lama nyala dipotong max_on_s pompa (dosis kurang lebih aman).
    assert loop.plan(5.0, ratio)["k"] == config.outputs["dose_k"].max_on_s
    # ΔEC kecil: dosis di bawah min_pump_s dilewati.
    assert loop.plan(0.004, ratio)["p"] == 0.0


# --- prasyarat dan keselamatan ------------------------------------------------------------------


@pytest.mark.parametrize(
    "parameters",
    [
        {"phase": "vegetatif"},
        {"targetEc": "2.2", "phase": "vegetatif"},
        {"targetEc": True, "phase": "vegetatif"},
        {"targetEc": 0.2, "phase": "vegetatif"},
        {"targetEc": 4.5, "phase": "vegetatif"},
        {"targetEc": 2.2},
        {"targetEc": 2.2, "phase": "berbunga"},
    ],
)
async def test_invalid_parameters_refused(rig, parameters):
    ack = await send(rig, "DOSE_TO_TARGET", parameters)
    assert not ack.success and reason(ack) == "INVALID" and ack.actuator_state == "OFF"
    assert not rig.dosing.running


async def test_backup_power_refuses_start(rig):
    rig.power["source"] = "BATTERY"
    ack = await dose(rig)
    assert not ack.success and reason(ack) == "INTERLOCK_POWER"
    await rig.advance(1.0)
    assert all_off(rig) and not rig.dosing.running


async def test_float_low_refuses_start(rig):
    rig.world.set_float_low_ok(False)
    await rig.run(rig.outputs._read_float("relay_t"))
    ack = await dose(rig)
    assert not ack.success and reason(ack) == "INTERLOCK_FLOAT"


async def test_trolley_in_use_refuses_start(rig):
    assert (await send(rig, "GOTO_STATION", {"station": 2}, code="TROLLEY-GOTO")).success
    ack = await dose(rig)
    assert not ack.success and reason(ack) == "INTERLOCK_EXCLUSIVE"
    await until(rig, lambda: not rig.virtual.is_on("trolley_goto"), step=STEP)


async def test_second_start_is_busy(rig):
    assert (await dose(rig)).success
    again = await dose(rig)
    assert not again.success and reason(again) == "BUSY" and again.actuator_state == "ON"


@pytest.mark.parametrize("missing", ["volume", "flow", "ph_cal", "adc"])
async def test_missing_settings_refuse_with_clear_reason(dosing_data, missing):
    if missing == "volume":
        dosing_data["dosing"]["drum_volume_l"] = None
    elif missing == "flow":
        dosing_data["dosing"]["pump_flow_ml_s"]["p"] = None
    elif missing == "ph_cal":
        dosing_data["analog"]["ph"]["cal"] = {"v_401": None, "v_686": None, "v_918": None}
    else:
        dosing_data["devices"]["adc"]["registers"] = {}
    rig = await build(dosing_data)
    try:
        ack = await dose(rig)
        assert not ack.success and reason(ack) == "NOT_CONFIGURED"
        assert "belum diatur" in ack.error_message and not rig.dosing.running
    finally:
        await shutdown(rig)


async def test_loop_holds_trolley_and_raw_commands(rig):
    assert (await dose(rig)).success
    await until(rig, lambda: rig.physical("stirrer"), step=STEP)
    goto = await send(rig, "HOME", code="TROLLEY-GOTO")
    assert not goto.success and reason(goto) == "INTERLOCK_EXCLUSIVE"
    patrol = await send(rig, "START", code="TROLLEY-PATROL")
    assert not patrol.success and reason(patrol) == "INTERLOCK_EXCLUSIVE"
    for actuator, expected in [
        ("TROLLEY-RUN", "INTERLOCK_EXCLUSIVE"),
        ("DIST-PUMP", "INTERLOCK_EXCLUSIVE"),
        ("DOSING-N", "BUSY"),
        ("STIRRER", "BUSY"),
    ]:
        result = await rig.command(actuator, "ON", 5)
        assert not result.success and reason(result) == expected, actuator
    # Di antara dua dosis (tidak ada pompa menyala) trolley tetap ditahan (S4).
    await until(rig, lambda: not rig.physical("stirrer"), step=STEP)
    assert all_off(rig)
    result = await rig.command("TROLLEY-RUN", "ON", 5)
    assert not result.success and reason(result) == "INTERLOCK_EXCLUSIVE"
    await wait_done(rig)
    assert (await rig.command("DIST-PUMP", "ON", 5)).success  # tahanan dilepas


async def test_raw_stirrer_off_interrupts_loop(rig):
    assert (await dose(rig)).success
    await until(rig, lambda: rig.physical("stirrer"), step=STEP)
    assert (await rig.command("STIRRER", "OFF")).success  # OFF mentah tidak pernah diblokir (S3)
    await wait_done(rig, limit_s=5)
    [failed] = rig.sink.of_type("DOSING_FAILED")
    assert failed["metadata"]["reason"] == "DOSING_INTERRUPTED"
    assert all_off(rig)


async def test_power_loss_mid_dose_stops_promptly(rig):
    assert (await dose(rig)).success
    await until(rig, lambda: rig.physical("dose_n"), step=STEP)
    rig.power["source"] = "BATTERY"
    started = rig.clock.monotonic()
    await wait_done(rig, limit_s=5)
    assert rig.clock.monotonic() - started <= rig.config.dosing.guard_s + 0.5
    assert all_off(rig)
    [failed] = rig.sink.of_type("DOSING_FAILED")
    assert failed["severity"] == "WARNING" and failed["metadata"]["reason"] == "INTERLOCK_POWER"
    assert "listrik PLN padam" in failed["message"]
    assert rig.dosing.last.total_ml["n"] > 0  # dosis yang sempat masuk tetap tercatat


async def test_float_open_mid_loop_stops(rig):
    assert (await dose(rig)).success
    await until(rig, lambda: rig.physical("stirrer"), step=STEP)
    rig.world.set_float_low_ok(False)
    await wait_done(rig, limit_s=rig.config.polling.relays_s + 5)
    assert all_off(rig)
    assert rig.sink.of_type("DOSING_FAILED")[0]["metadata"]["reason"] == "INTERLOCK_FLOAT"


async def test_s9_invalid_reading_stops_before_any_dose(rig):
    rig.world.tank_ph = 15.5
    assert (await dose(rig)).success
    await wait_done(rig)
    [failed] = rig.sink.of_type("DOSING_FAILED")
    assert failed["metadata"]["reason"] == "EC_PH_INVALID"
    assert all(ml == 0 for ml in rig.world.dosed_ml.values())
    assert rig.sink.of_type("EC_PH_INVALID")  # alert sensor dari siklus analog


# --- STOP ---------------------------------------------------------------------------------------


async def test_stop_mid_pump_turns_everything_off_quickly(rig):
    assert (await dose(rig)).success
    await until(rig, lambda: rig.physical("dose_n"), step=STEP)
    started = rig.clock.monotonic()
    ack = await send(rig, "STOP")
    assert ack.success and ack.actuator_state == "OFF"
    assert rig.clock.monotonic() - started < 1.0
    assert all_off(rig) and not rig.dosing.running
    assert rig.dosing.last.status == "STOPPED"
    assert rig.virtual.actuator_states("RIO-TANK-01") == {"DOSING-LOOP": "OFF"}
    assert rig.safety.held == {} and rig.safety.managed_on == {}
    # Dihentikan operator: bukan kegagalan, bukan selesai.
    assert not [a for a in rig.sink.alerts if a["type"].startswith("DOSING")]
    await rig.advance(30)
    assert all_off(rig)


async def test_stop_while_reading_sensor(rig):
    assert (await dose(rig)).success
    await until(rig, lambda: rig.physical("ec_power"), step=STEP)
    assert (await send(rig, "STOP")).success
    assert all_off(rig) and not rig.physical("ec_power")
    assert rig.safety.managed == {}


async def test_stop_is_never_refused_and_turns_raw_pumps_off(rig):
    rig.power["source"] = "BATTERY"
    idle = await send(rig, "STOP")
    assert idle.success and idle.actuator_state == "OFF"
    rig.power["source"] = "MAINS"
    assert (await rig.command("DOSING-N", "ON", 30)).success
    assert (await rig.command("STIRRER", "ON", 30)).success
    assert (await send(rig, "STOP")).success
    assert all_off(rig)


async def test_shutdown_during_loop_turns_outputs_off(rig):
    assert (await dose(rig)).success
    await until(rig, lambda: rig.physical("stirrer"), step=STEP)
    await finish(rig, rig.virtual.stop(), step=STEP)
    assert all_off(rig) and not rig.dosing.running


# --- config (F9.1, F9.3) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["dosing"].update(fraction_per_iteration=0.6), "fraction_per_iteration"),
        (lambda d: d["dosing"].update(fraction_per_iteration=0.2), "fraction_per_iteration"),
        (lambda d: d["dosing"].update(max_iterations=9), "dosing.max_iterations"),
        (lambda d: d["dosing"]["pumps"].update(p="pompa_p"), "dosing.pumps.p"),
        (lambda d: d["dosing"]["pumps"].update(k="dose_n"), "dipakai dua kali"),
        (lambda d: d["dosing"].update(stir_s=900), "melebihi max_on_s"),
        (lambda d: d["dosing"]["ratio"].update(vegetatif=[0, 0, 0]), "rasio fase"),
        (lambda d: d["dosing"].update(target_ec_ms_cm=[4.0, 0.5]), "target_ec_ms_cm"),
    ],
)
def test_broken_dosing_config_names_the_key(dosing_data, mutate, expected):
    mutate(dosing_data)
    with pytest.raises(ConfigError) as info:
        make_config(dosing_data)
    assert expected in str(info.value)


def test_example_leaves_p_h12_values_empty():
    from fertimata_edge.config import load_config

    example = load_config(EXAMPLE_CONFIG)
    assert "drum_volume_l" in example.dosing.missing
    assert len(example.dosing.missing) == 7
    # Kode fase sama dengan yang dikirim backend (Kontrak §5.1).
    assert set(example.dosing.ratio) == {"vegetatif", "buah_muda", "pembesaran_buah"}
    assert example.virtual_actuators["dosing_loop"].actuator_code == "DOSING-LOOP"
