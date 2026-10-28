"""Alat bantu kalibrasi EC/pH (F6.6)."""

from __future__ import annotations

import sys

import pytest
import yaml

from fertimata_edge.config import ConfigError, load_config

from .conftest import DEV_CONFIG, ROOT
from .helpers import drive

sys.path.insert(0, str(ROOT / "tools"))
import calibrate_ec_ph as cal


@pytest.fixture
def cfg_path(tmp_path):
    """Salinan config dev; kalibrasi dikosongkan seperti Jetson baru."""
    text = DEV_CONFIG.read_text(encoding="utf-8")
    text = text.replace("v_1413: 231.7, v_12880: 2112.3", "v_1413: null, v_12880: null")
    text = text.replace(
        "v_401: 3038.2, v_686: 2525.2, v_918: 2107.6", "v_401: null, v_686: null, v_918: null"
    )
    path = tmp_path / "fertimata.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def set_solution(path, **fake) -> None:
    """Larutan simulator (fake.tank_ph / fake.tank_ec_ms_cm) tempat probe dicelup."""
    text = path.read_text(encoding="utf-8")
    extra = "".join(f"  {key}: {value}\n" for key, value in fake.items())
    path.write_text(text.replace("fake:", "fake:\n" + extra.rstrip("\n"), 1), encoding="utf-8")


async def _run(clock, *argv):
    lines: list[str] = []
    code = await drive(clock, cal.run(cal.parse_args(list(argv)), lines.append, clock), step=0.1)
    return code, lines


async def test_ec_point_measured_but_not_written_without_flag(cfg_path, clock):
    set_solution(cfg_path, tank_ec_ms_cm=1.413)
    before = cfg_path.read_text(encoding="utf-8")
    code, lines = await _run(clock, "--config", str(cfg_path), "--point", "ec1413")
    assert code == 0
    [k_line] = [line for line in lines if "K = " in line]
    assert float(k_line.split("K = ")[1].split()[0]) == pytest.approx(1.0, abs=0.01)
    assert "--write" in lines[-1]
    assert cfg_path.read_text(encoding="utf-8") == before


async def test_ec_point_written_normalised_to_25c_and_comments_kept(cfg_path, clock):
    set_solution(cfg_path, tank_ec_ms_cm=1.413)
    code, lines = await _run(clock, "--config", str(cfg_path), "--point", "ec1413", "--write")
    assert code == 0, lines
    config = load_config(cfg_path)
    # Simulator: 1,413 mS/cm pada 26,3 °C; nilai tersimpan setara 25 °C = 1,413 × 164 mV.
    # ADC simulator beresolusi 1 mV, jadi toleransi ±1 mV.
    assert config.analog.ec.cal.v_1413 == pytest.approx(1.413 * 164, abs=1.0)
    assert config.analog.ec.cal.v_12880 is None
    text = cfg_path.read_text(encoding="utf-8")
    assert "# jendela kalibrasi pustaka DFRobot_EC" in text  # komentar tidak hilang
    assert (cfg_path.parent / "fertimata.yaml.bak").exists()


async def test_wrong_solution_rejected_by_k_window(cfg_path, clock):
    set_solution(cfg_path, tank_ec_ms_cm=0.5)  # probe di air biasa, bukan larutan 1413
    code, lines = await _run(clock, "--config", str(cfg_path), "--point", "ec1413", "--write")
    assert code == 1 and any("K di luar batas" in line for line in lines)
    assert load_config(cfg_path).analog.ec.cal.v_1413 is None


async def test_temperature_override_when_sensor_missing(cfg_path, clock):
    set_solution(cfg_path, tank_ec_ms_cm=1.413)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["devices"]["water_t"]["registers"]
    text = cfg_path.read_text(encoding="utf-8").replace(
        "      water_temperature: {fc: 3, address: 0x0000, type: s16, scale: 0.1}\n", ""
    )
    cfg_path.write_text(text.replace("    registers:\n  adc:", "  adc:"), encoding="utf-8")
    code, lines = await _run(clock, "--config", str(cfg_path), "--point", "ec1413")
    assert code == 1 and any("--temperature" in line for line in lines)
    code, lines = await _run(
        clock, "--config", str(cfg_path), "--point", "ec1413", "--temperature", "26.3"
    )
    assert code == 0, lines


async def test_ph_points_build_working_calibration(cfg_path, clock):
    for point, ph in (("ph686", 6.86), ("ph401", 4.01)):
        text = cfg_path.read_text(encoding="utf-8")
        cfg_path.write_text(text.replace("  tank_ph:", "  old_tank_ph:"), encoding="utf-8")
        set_solution(cfg_path, tank_ph=ph)
        text = cfg_path.read_text(encoding="utf-8")
        cfg_path.write_text(
            "\n".join(line for line in text.split("\n") if "old_tank_ph" not in line),
            encoding="utf-8",
        )
        code, lines = await _run(clock, "--config", str(cfg_path), "--point", point, "--write")
        assert code == 0, lines
    config = load_config(cfg_path)
    assert config.analog.ph.cal.v_686 == pytest.approx(2525.2, abs=1.0)
    assert config.analog.ph.cal.v_401 == pytest.approx(3038.2, abs=1.0)
    assert config.analog.ph.cal_temperature_c == pytest.approx(26.3)
    [slope] = [line for line in lines if "Kemiringan" in line]
    assert float(slope.split()[1]) == pytest.approx(-180.0, abs=1.0)


async def test_swapped_ph_buffer_rejected(cfg_path, clock):
    text = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        text.replace("v_401: null, v_686: null", "v_401: 3038.2, v_686: 2525.2"), encoding="utf-8"
    )
    set_solution(cfg_path, tank_ph=4.01)  # salah celup: buffer 4,01 diukur sebagai 9,18
    code, lines = await _run(clock, "--config", str(cfg_path), "--point", "ph918", "--write")
    assert code == 1 and any("tertukar" in line for line in lines)
    assert load_config(cfg_path).analog.ph.cal.v_918 is None


def test_apply_updates_replaces_only_the_key():
    text = (
        "ec: {k: 1.0, cal: {v_1413: null, v_12880: 2000}}  # catatan\nph: {cal_temperature_c: 25}\n"
    )
    out = cal.apply_updates(text, {"v_1413": 231.7, "cal_temperature_c": 26.3})
    assert out == (
        "ec: {k: 1.0, cal: {v_1413: 231.7, v_12880: 2000}}  # catatan\n"
        "ph: {cal_temperature_c: 26.3}\n"
    )


def test_apply_updates_refuses_ambiguous_key():
    with pytest.raises(ConfigError, match="tepat sekali"):
        cal.apply_updates("a: {v_401: 1}\nb: {v_401: 2}\n", {"v_401": 3})
