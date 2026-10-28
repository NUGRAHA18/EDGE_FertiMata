from __future__ import annotations

import subprocess
import sys

import pytest

from .conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import modbus_probe as probe


@pytest.fixture
async def bus(hal):
    await hal.bus.open()
    return hal.bus


async def _run(bus, *argv):
    lines: list[str] = []

    async def no_sleep(_):
        return None

    code = await probe.run(bus, probe.parse_args(list(argv)), lines.append, sleep=no_sleep)
    return code, lines


async def test_read_pzem_shows_interpretations(bus, hal):
    code, lines = await _run(bus, "--unit", "30", "--fc", "4", "--address", "0", "--count", "3")
    assert code == 0
    assert lines[0] == "alamat 30 FC4 @0x0000 x3:"
    voltage = round(hal.world.pzem.voltage_v * 10)
    assert any(line.startswith("  0x0000 (    0)") and str(voltage) in line for line in lines)
    assert any("lo_hi" in line for line in lines)  # pasangan 32 bit untuk arus


def test_format_signed_and_32bit():
    lines = probe.format_registers(0, [0xFFDD, 0x0001])
    assert "-35" in lines[1]
    assert lines[-1].split()[-2:] == [str((0x0001 << 16) | 0xFFDD), str((0xFFDD << 16) | 0x0001)]


async def test_scan_finds_devices(bus):
    code, lines = await _run(bus, "--scan", "1-31", "--fc", "1", "--address", "0")
    assert code == 0
    # Relay menjawab coil; perangkat memori palsu juga menjawab; alamat kosong tidak.
    assert "alamat  20" in "\n".join(lines) and "alamat   2" not in "\n".join(lines)


async def test_timeout_message(bus, hal):
    hal.world.set_device_online("air", False)
    code, lines = await _run(bus, "--unit", "3", "--fc", "4", "--address", "1", "--count", "2")
    assert code == 1 and "tidak ada jawaban" in lines[0]


async def test_exception_message(bus, hal):
    hal.world.by_name["adc"].strict_addresses = True
    code, lines = await _run(bus, "--unit", "10", "--fc", "3", "--address", "0x10")
    assert code == 1 and "ILLEGAL DATA ADDRESS" in lines[0]


async def test_write_coil_reads_back(bus, hal):
    code, lines = await _run(
        bus, "--unit", "21", "--fc", "5", "--address", "4", "--value", "on", "--yes"
    )
    assert code == 0 and hal.world.output_on("stirrer")
    assert lines == ["tulis FC5 @0x0004 = ON: diterima", "baca balik FC1: [True]"]


async def test_repeat_reads(bus):
    code, lines = await _run(bus, "--unit", "30", "--fc", "4", "--repeat", "3")
    assert code == 0 and sum(line.startswith("alamat 30") for line in lines) == 3


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--unit", "20", "--fc", "5", "--address", "0", "--value", "on"], "--yes"),
        (["--unit", "20", "--fc", "6", "--yes"], "--value"),
        (["--unit", "20", "--fc", "6", "--value", "x", "--yes"], "angka"),
        (["--scan", "0-5", "--fc", "3"], "1–247"),
        (["--scan", "1-5", "--fc", "5", "--value", "on", "--yes"], "hanya untuk baca"),
        (["--fc", "3"], "--unit"),
    ],
)
def test_argument_errors(argv, message, capsys):
    with pytest.raises(SystemExit):
        probe.parse_args(argv)
    assert message in capsys.readouterr().err


def test_cli_with_fake_simulator():
    result = subprocess.run(
        [
            sys.executable,
            "tools/modbus_probe.py",
            "--fake",
            "--unit",
            "3",
            "--fc",
            "4",
            "--address",
            "1",
            "--count",
            "2",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "alamat 3 FC4 @0x0001 x2:" in result.stdout


def test_cli_missing_port():
    result = subprocess.run(
        [
            sys.executable,
            "tools/modbus_probe.py",
            "--port",
            "COM_TIDAK_ADA",
            "--unit",
            "1",
            "--fc",
            "3",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2 and "port tidak bisa dibuka" in result.stderr
