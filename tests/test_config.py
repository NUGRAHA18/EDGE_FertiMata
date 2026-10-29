from __future__ import annotations

import pytest

from fertimata_edge.config import ConfigError, load_config

from .conftest import DEV_CONFIG, EXAMPLE_CONFIG, make_config


def test_example_config_is_valid():
    config = load_config(EXAMPLE_CONFIG)
    assert config.agent.hal == "real"
    assert config.devices["pzem"].address == 30
    assert config.outputs["pump_dist"].actuator_code == "DIST-PUMP"
    assert config.camera.patrol_schedule == ["19:00"]


def test_dev_config_is_valid_and_fake():
    config = load_config(DEV_CONFIG)
    assert config.agent.hal == "fake"
    assert config.fake.seed == 42


def test_dev_config_keeps_same_io_map_as_example():
    # Logika yang dites di laptop harus memakai peta I/O yang sama dengan Jetson.
    example, dev = load_config(EXAMPLE_CONFIG), load_config(DEV_CONFIG)
    for section in ("mqtt", "bus", "logical_devices", "outputs", "inputs", "safety"):
        assert getattr(example, section) == getattr(dev, section), section
    # Pengecualian: dev memberi peta register simulasi untuk perangkat yang belum terverifikasi.
    strip = {"registers"}
    assert {k: v.model_dump(exclude=strip) for k, v in example.devices.items()} == {
        k: v.model_dump(exclude=strip) for k, v in dev.devices.items()
    }
    for name, device in example.devices.items():
        if device.profile in ("pzem016", "xy_md02"):  # terverifikasi: harus identik
            assert dev.devices[name].registers == device.registers, name


def test_example_has_no_unverified_register_maps():
    # Peta register tanah/cahaya/DS18B20 hanya boleh diisi setelah cek P-H5..P-H7.
    example = load_config(EXAMPLE_CONFIG)
    for name, device in example.devices.items():
        if device.profile in ("soil_7in1", "lux_rs485", "ds18b20_mb"):
            assert device.registers == {}, name


def test_missing_file_gives_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="tidak bisa dibaca"):
        load_config(tmp_path / "tidak-ada.yaml")


def test_broken_yaml_rejected(tmp_path):
    path = tmp_path / "rusak.yaml"
    path.write_text("agent: [tidak ditutup\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML rusak"):
        load_config(path)


def test_duplicate_key_rejected(tmp_path):
    path = tmp_path / "ganda.yaml"
    path.write_text("agent:\n  site: a\n  site: b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="kunci ganda 'site'"):
        load_config(path)


RELAY_A = {"write_fc": 5, "write_base": 0, "read_fc": 1, "read_base": 0}


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["devices"]["soil"].update(address=300), "devices.soil.address"),
        (lambda d: d["devices"]["air"].update(profile="sht20"), "devices.air.profile"),
        (lambda d: d["devices"]["relay_p"].pop("channels"), "wajib punya 'channels'"),
        (lambda d: d["bus"].pop("port"), "bus.port"),
        (lambda d: d["bus"].update(baud=9600), "bus.baud"),  # kunci salah ketik
        (lambda d: d["mqtt"].update(qos=2), "mqtt.qos"),
        (lambda d: d["mqtt"]["topics"].update(status="fertimata/status"), "mqtt.topics.status"),
        (lambda d: d["agent"].update(hal="simulasi"), "agent.hal"),
        (lambda d: d["outputs"]["pump_dist"].update(device="relay_x"), "outputs.pump_dist.device"),
        (lambda d: d["outputs"]["pump_dist"].update(device="pzem"), "bukan relay"),
        (lambda d: d["outputs"]["trolley_dir"].update(ch=4), "outputs.trolley_dir.ch"),
        (lambda d: d["outputs"]["dose_p"].update(ch=0), "kanal sudah dipakai outputs.dose_n"),
        (lambda d: d["outputs"]["dose_p"].update(actuator_code="DOSING-N"), "sudah dipakai"),
        (lambda d: d["outputs"]["stirrer"].pop("max_on_s"), "outputs.stirrer"),
        (lambda d: d["devices"]["air"].update(address=1), "alamat 1 sudah dipakai"),
        (
            lambda d: d["logical_devices"]["PZEM-01"].update(sources=["pzm"]),
            "logical_devices.PZEM-01",
        ),
        (lambda d: d["safety"]["exclusive_groups"].append(["pompa"]), "safety.exclusive_groups[3]"),
        (lambda d: d["safety"]["blocked_on_float_low"].append("x"), "safety.blocked_on_float_low"),
        (lambda d: d["safety"]["output_groups"].update(g=["nope"]), "safety.output_groups.g"),
        (lambda d: d["inputs"]["float_low_ok"].update(device="adc"), "inputs.float_low_ok.device"),
        (lambda d: d["analog"]["ec"].update(device="adcx"), "analog.ec.device"),
        (lambda d: d["power"].update(restore_voltage_v=170), "histeresis"),
        (lambda d: d["camera"].update(patrol_schedule=[1140]), "camera.patrol_schedule"),
        (lambda d: d["camera"].update(patrol_schedule=["7:00"]), "HH:MM"),
        (
            lambda d: d["fake"]["dose_pumps"].update(
                dose_x={"flow_ml_s": 1, "ec_gain_ms_cm_per_ml_l": 1}
            ),
            "dose_x",
        ),
        (lambda d: d["devices"]["relay_p"].pop("relay"), "wajib punya 'relay'"),
        (lambda d: d["devices"]["air"].update(relay=RELAY_A), "hanya untuk profil relay_mb"),
        (lambda d: d["devices"]["relay_p"]["relay"].update(write_fc=6), "on_value dan off_value"),
        (lambda d: d["devices"]["relay_p"]["relay"].update(on_value=1), "hanya dipakai bila"),
        (lambda d: d["devices"]["relay_p"]["relay"].update(read_fc=5), "relay.read_fc"),
        (lambda d: d["devices"]["relay_t"]["relay"].pop("input_base"), "harus diisi bersama"),
        (
            lambda d: [d["devices"]["relay_t"]["relay"].pop(k) for k in ("input_fc", "input_base")],
            "dipakai inputs.float_low_ok",
        ),
        (lambda d: d["safety"]["blocked_while_on"].update(x=["trolley_run"]), "blocked_while_on"),
        (lambda d: d["safety"]["travel"].update(run="motor"), "safety.travel.run"),
        (lambda d: d["safety"]["travel"].update(limit_when_on="home"), "harus berbeda"),
        (lambda d: d["analog"].update(power_output="r5"), "analog.power_output"),
        (lambda d: d["analog"].update(temperature_device="air"), "analog.temperature_device"),
        (lambda d: d["analog"]["ph"].update(device="pzem"), "bukan ADC"),
        (lambda d: d["devices"]["adc"].pop("metric_prefix"), "devices.adc.metric_prefix"),
        (lambda d: d["analog"]["ec"].update(range_switch_ms_cm=[2.5, 2.0]), "range_switch_ms_cm"),
        (lambda d: d["analog"]["valid"].update(ph=[14, 0]), "batas bawah"),
        (lambda d: d["analog"]["ec"]["cal"].update(v_1413=0), "analog.ec.cal.v_1413"),
        (lambda d: d["polling"].update(pzem_publish_s=1), "pzem_publish_s"),
    ],
)
def test_broken_config_names_the_key(dev_data, mutate, expected):
    mutate(dev_data)
    with pytest.raises(ConfigError) as info:
        make_config(dev_data)
    assert expected in str(info.value)


def test_unquoted_on_is_rejected_where_string_expected(dev_data):
    # YAML 1.1: ON tanpa kutip menjadi boolean True. Kolom string harus menolaknya.
    dev_data["outputs"]["pump_dist"]["actuator_code"] = True
    with pytest.raises(ConfigError, match=r"outputs.pump_dist.actuator_code"):
        make_config(dev_data)


def test_all_problems_reported_at_once(dev_data):
    dev_data["outputs"]["pump_dist"]["device"] = "relay_x"
    dev_data["analog"]["ph"]["device"] = "adcx"
    with pytest.raises(ConfigError) as info:
        make_config(dev_data)
    assert "outputs.pump_dist.device" in str(info.value)
    assert "analog.ph.device" in str(info.value)


def test_safety_expand_groups(config):
    expanded = config.safety.expand(["dosing", "pump_dist"])
    assert expanded == {"dose_n", "dose_p", "dose_k", "stirrer", "pump_dist"}


def test_output_by_actuator(config):
    assert config.output_by_actuator("TROLLEY-DIR") == "trolley_dir"
    assert config.output_by_actuator("TIDAK-ADA") is None


def test_rail_length_matches_rev_a(config):
    # §9.1: rel 6 m, S1 di 300 mm, 10 stasiun tiap 600 mm.
    assert config.trolley.rail_length_mm == 6000


def test_profile_b_relay_map_accepted(dev_data):
    dev_data["devices"]["relay_p"]["relay"] = {
        "write_fc": 6,
        "write_base": 1,
        "on_value": 0x0100,
        "off_value": 0x0200,
        "read_fc": 3,
        "read_base": 1,
    }
    relay = make_config(dev_data).devices["relay_p"].relay
    assert (relay.write_fc, relay.on_value, relay.off_value) == (6, 0x0100, 0x0200)


def test_example_has_no_unverified_analog_values():
    # Register ADC (P-H8) dan kalibrasi EC/pH (P-H13) hanya diisi setelah dicek.
    example = load_config(EXAMPLE_CONFIG)
    assert example.devices["adc"].registers == {}
    assert example.analog.ec.cal.v_1413 is None and example.analog.ph.cal.points() == []


def test_dosing_limit_covers_ai_fuzzy_range():
    # Keputusan user 29 Okt: fuzzy pupuk tim AI memberi durasi 0–150 s, katalog backend 150 s.
    # Batas edge lebih kecil memotong dosis yang sah; lebih besar melonggarkan pengaman S2.
    for path in (EXAMPLE_CONFIG, DEV_CONFIG):
        outputs = load_config(path).outputs
        assert [outputs[n].max_on_s for n in ("dose_n", "dose_p", "dose_k")] == [150] * 3
