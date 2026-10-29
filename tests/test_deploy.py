"""Berkas deployment Jetson (F10.1–F10.3, F10.5): konsisten satu sama lain dan dengan dokumen."""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fertimata_edge.config import load_config

from .conftest import EXAMPLE_CONFIG, ROOT

DEPLOY = ROOT / "deploy"
INSTALL = DEPLOY / "install.sh"
UNIT = DEPLOY / "systemd" / "fertimata-edge.service"
UDEV = DEPLOY / "udev" / "99-fertimata.rules"
JOURNALD = DEPLOY / "journald" / "fertimata.conf"


def _ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # kunci systemd peka huruf
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


def _install_vars() -> dict[str, str]:
    """Variabel jalur di install.sh, dengan `$NAMA` yang sudah diganti."""
    values: dict[str, str] = {}
    pattern = re.compile(r"^([A-Z_]+)=(\S+)$")
    for line in INSTALL.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match and not match.group(2).startswith(('"$(', "$(")):
            value = match.group(2).strip('"')
            for name, known in values.items():
                value = value.replace(f"${name}", known)
            values[match.group(1)] = value
    return values


def _usable_bash() -> str | None:
    bash = shutil.which("bash")
    # bash.exe di System32 adalah WSL: lingkungan lain, bukan bash untuk skrip ini.
    if bash is None or "system32" in bash.lower():
        return None
    return bash


needs_bash = pytest.mark.skipif(_usable_bash() is None, reason="bash tidak tersedia")


# --- unit systemd (F10.2) -------------------------------------------------------------------------


def test_unit_restarts_forever_with_watchdog():
    unit = _ini(UNIT)
    service = unit["Service"]
    assert service["Type"] == "notify"  # READY + ping dari sdnotify.py
    assert service["Restart"] == "always"
    assert int(service["WatchdogSec"]) > 0
    # S20: restart tidak boleh berhenti setelah beberapa kali gagal (StartLimitBurst bawaan).
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"


def test_unit_does_not_wait_for_network_or_broker():
    # S1 harus tetap jalan walau LAN/broker mati; link MQTT menyambung ulang sendiri.
    unit = _ini(UNIT)["Unit"]
    deps = " ".join(unit.get(key, "") for key in ("Requires", "Wants", "After", "BindsTo"))
    assert "network-online" not in deps and "mosquitto" not in deps and "docker" not in deps


def test_unit_runs_as_non_root_with_device_groups():
    service = _ini(UNIT)["Service"]
    assert service["User"] == _install_vars()["SERVICE_USER"] != "root"
    assert set(service["SupplementaryGroups"].split()) == {"dialout", "gpio", "video"}
    assert "PrivateDevices" not in service  # /dev/rs485, gpiochip, kamera dibutuhkan


def test_unit_paths_match_install_script():
    service = _ini(UNIT)["Service"]
    variables = _install_vars()
    assert service["ExecStart"].split() == [
        f"{variables['VENV']}/bin/python",
        "-m",
        "fertimata_edge",
        "--config",
        variables["CONF"],
    ]
    assert service["WorkingDirectory"] == variables["DATA_DIR"]
    assert variables["DATA_DIR"] in service["ReadWritePaths"].split()
    assert UNIT.stem == variables["SERVICE"]


def test_production_config_path_matches_docs():
    # F10.5 dan komentar config contoh: config produksi di luar repo.
    assert _install_vars()["CONF"] == "/etc/fertimata/fertimata.yaml"
    assert "/etc/fertimata/fertimata.yaml" in EXAMPLE_CONFIG.read_text(encoding="utf-8")


# --- udev (F10.1) ---------------------------------------------------------------------------------


def _active_rules() -> list[str]:
    lines = UDEV.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_udev_rs485_matches_architecture_doc():
    doc = (ROOT / "docs" / "referensi" / "Arsitektur-Sistem.md").read_text(encoding="utf-8")
    found = re.search(r"FT232RL, `([0-9a-f]{4}):([0-9a-f]{4})`", doc)
    assert found is not None
    vid, pid = found.groups()
    rs485 = [rule for rule in _active_rules() if 'SYMLINK+="rs485"' in rule]
    assert len(rs485) == 1
    rule = rs485[0]
    assert f'ATTRS{{idVendor}}=="{vid}"' in rule and f'ATTRS{{idProduct}}=="{pid}"' in rule
    assert 'SUBSYSTEM=="tty"' in rule and 'GROUP="dialout"' in rule


def test_udev_camera_waits_for_verified_vid_pid():
    # P-H10: VID:PID kamera belum terverifikasi, jadi rule-nya belum aktif (bukan angka tebakan).
    assert not any("cam-trolley" in rule for rule in _active_rules())
    assert 'SYMLINK+="cam-trolley"' in UDEV.read_text(encoding="utf-8")


def test_udev_gives_gpio_group_access():
    assert any('KERNEL=="gpiochip*"' in r and 'GROUP="gpio"' in r for r in _active_rules())


def test_device_paths_in_config_match_udev_symlinks():
    config = load_config(EXAMPLE_CONFIG)
    text = UDEV.read_text(encoding="utf-8")
    assert config.bus.port == "/dev/rs485" and 'SYMLINK+="rs485"' in text
    assert config.camera.device == "/dev/cam-trolley" and 'SYMLINK+="cam-trolley"' in text


# --- journald (F10.5) -----------------------------------------------------------------------------


def test_journald_is_persistent_and_bounded():
    journal = _ini(JOURNALD)["Journal"]
    assert journal["Storage"] == "persistent"
    assert journal["SystemMaxUse"].endswith(("M", "G"))
    assert journal["SystemKeepFree"].endswith(("M", "G"))


# --- skrip instal (F10.3) -------------------------------------------------------------------------


def test_deploy_files_use_lf_line_endings():
    # CRLF membuat bash gagal (`$'\r': command not found`) dan udev salah membaca rule.
    for path in DEPLOY.rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes(), path
    assert "deploy/** text eol=lf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_deploy_never_powers_off_jetson():
    # S18 juga berlaku untuk skrip deployment.
    pattern = re.compile(r"\b(shutdown|poweroff|reboot|halt)\b|systemctl\s+(kexec|suspend)")
    for path in DEPLOY.rglob("*"):
        if path.is_file():
            lines = path.read_text(encoding="utf-8").splitlines()
            code = "\n".join(line.split("#", 1)[0] for line in lines)
            assert not pattern.search(code), path


def test_install_never_overwrites_existing_config():
    script = INSTALL.read_text(encoding="utf-8")
    copy = script.index('"$REPO_DIR/config/fertimata.example.yaml" "$CONF"')
    guard = script.rindex('if [ -f "$CONF" ]', 0, copy)
    assert "return" in script[guard:copy]


@needs_bash
def test_install_script_syntax():
    subprocess.run([_usable_bash(), "-n", INSTALL.as_posix()], check=True)


@needs_bash
def test_install_dry_run_fresh_jetson_does_not_start_agent():
    if Path("/etc/fertimata/fertimata.yaml").exists():
        pytest.skip("dijalankan di Jetson yang sudah terpasang")
    env = {**os.environ, "PYTHON": Path(sys.executable).as_posix()}
    result = subprocess.run(
        [_usable_bash(), INSTALL.as_posix(), "--dry-run"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
        timeout=60,
    )
    steps = "\n".join(line for line in result.stdout.splitlines() if line.startswith("+ "))
    variables = _install_vars()
    assert f"--home-dir {variables['DATA_DIR']}" in steps
    assert "--shell /usr/sbin/nologin" in steps
    assert "/etc/udev/rules.d/99-fertimata.rules" in steps
    assert "/etc/systemd/journald.conf.d/fertimata.conf" in steps
    # Unit dipasang dan di-reload sebelum di-enable.
    assert steps.index("daemon-reload") < steps.index("systemctl enable fertimata-edge")
    # Config baru masih contoh (nilai belum terverifikasi): agent tidak dijalankan otomatis.
    assert "systemctl restart fertimata-edge" not in steps
    assert "systemctl start fertimata-edge" not in steps
    assert "BELUM dijalankan" in result.stdout


@needs_bash
def test_install_rejects_unknown_option():
    result = subprocess.run(
        [_usable_bash(), INSTALL.as_posix(), "--paksa"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2 and "tidak dikenal" in result.stderr


def test_stop_off_timeout_fits_systemd_stop_timeout():
    # Q9: OFF-semua saat berhenti harus selesai sebelum systemd mengirim SIGKILL.
    stop_timeout = int(_ini(UNIT)["Service"]["TimeoutStopSec"])
    assert load_config(EXAMPLE_CONFIG).agent.stop_off_timeout_s < stop_timeout
