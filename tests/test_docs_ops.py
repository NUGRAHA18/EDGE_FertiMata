"""Dokumen operasi (F10.6) dan lembar komisioning (F11.1/F11.2) cocok dengan kode dan config."""

from __future__ import annotations

import re

from fertimata_edge.cli import _parse_args
from fertimata_edge.config import load_config
from fertimata_edge.safety import (
    REASON_BUSY,
    REASON_INTERLOCK_EXCLUSIVE,
    REASON_INTERLOCK_FLOAT,
    REASON_INTERLOCK_LIMIT,
    REASON_INTERLOCK_MOVING,
    REASON_INTERLOCK_POWER,
    REASON_OUTPUT_FAULT,
)

from .conftest import EXAMPLE_CONFIG, ROOT
from .test_deploy import _install_vars

DOCS = ROOT / "docs"
OPS = DOCS / "08-operasi.md"
COMMISSIONING = DOCS / "komisioning.md"


def _text(path) -> str:
    return path.read_text(encoding="utf-8")


def _rows_starting_with(text: str, prefix: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(prefix)]


# --- 08-operasi.md (F10.6) ------------------------------------------------------------------------


def test_ops_paths_match_install_script():
    text = _text(OPS)
    variables = _install_vars()
    for key in ("CONF", "VENV", "DATA_DIR", "SERVICE", "SERVICE_USER"):
        assert variables[key] in text, key
    assert f"systemctl restart {variables['SERVICE']}" in text
    assert f"journalctl -u {variables['SERVICE']}" in text


def test_ops_commands_use_existing_cli_flags_and_tools():
    text = _text(OPS)
    args = _parse_args(["--config", "x.yaml", "--check-config", "--log-level", "DEBUG"])
    assert args.check_config and "--check-config" in text and "--log-level DEBUG" in text
    for tool in re.findall(r"tools/[a-z_]+\.py", text):
        assert (ROOT / tool).is_file(), tool


def test_ops_exit_codes_match_cli():
    from fertimata_edge import cli

    text = _text(OPS)
    for code in (cli.EXIT_OK, cli.EXIT_CONFIG, cli.EXIT_HAL):
        assert re.search(rf"^\| {code} \|", text, re.MULTILINE), code


def test_ops_quoted_log_messages_exist_in_code():
    # Operator mencari pesan ini di journalctl; pesan diganti di kode → dokumen ikut diganti.
    sources = "\n".join(_text(p) for p in (ROOT / "src" / "fertimata_edge").rglob("*.py"))
    table = _text(OPS).split("Pesan yang paling sering dicari", 1)[1].split("## 4.", 1)[0]
    messages = re.findall(r"^\| `([^`]+)`", table, re.MULTILINE)
    assert len(messages) >= 10
    for message in messages:
        # Pesan dengan placeholder dicek sampai bagian tetapnya saja.
        fixed = re.split(r" device=| power=|…|\(", message)[0].strip()
        if fixed == "perangkat tidak menjawab":
            fixed = "tidak menjawab"
        assert fixed in sources, message


def test_ops_never_instructs_software_poweroff():
    # S18: dokumen operasi tidak boleh mengajarkan mematikan/me-restart Jetson lewat perintah.
    commands = re.findall(r"```(?:bash)?\n(.*?)```", _text(OPS), re.DOTALL)
    for block in commands:
        assert not re.search(r"\b(shutdown|poweroff|reboot|halt)\b", block), block


# --- komisioning.md (F11.1, F11.2) ----------------------------------------------------------------


def test_commissioning_covers_every_safety_rule_once():
    text = _text(COMMISSIONING)
    section = text.split("## F11.2", 1)[1].split("## F11.3", 1)[0]
    rules = re.findall(r"^\| (S\d+) \|", section, re.MULTILINE)
    assert rules == [f"S{n}" for n in range(1, 21)]
    documented = re.findall(
        r"^\| (S\d+) \|", _text(DOCS / "04-aturan-keselamatan.md"), re.MULTILINE
    )
    assert rules == documented


def test_commissioning_lists_every_modbus_device_with_its_address():
    config = load_config(EXAMPLE_CONFIG)
    section = _text(COMMISSIONING).split("## F11.1", 1)[1].split("## F11.2", 1)[0]
    for name, device in config.devices.items():
        rows = [r for r in _rows_starting_with(section, f"| `{name}` ")]
        assert rows, name
        assert f"| {device.address} |" in rows[0], name


def test_commissioning_relay_table_matches_output_map():
    config = load_config(EXAMPLE_CONFIG)
    section = _text(COMMISSIONING).split("### Relay", 1)[1].split("### GPIO", 1)[0]
    for name, output in config.outputs.items():
        address = config.devices[output.device].address
        row = f"| {address} | R{output.ch} | `{output.actuator_code}` |"
        assert row in section, name


def test_commissioning_rejection_codes_exist():
    known = {
        REASON_BUSY,
        REASON_INTERLOCK_EXCLUSIVE,
        REASON_INTERLOCK_FLOAT,
        REASON_INTERLOCK_LIMIT,
        REASON_INTERLOCK_MOVING,
        REASON_INTERLOCK_POWER,
        REASON_OUTPUT_FAULT,
        "EXPIRED",
    }
    used = set(re.findall(r"`((?:INTERLOCK|OUTPUT)_[A-Z]+|EXPIRED|BUSY)`", _text(COMMISSIONING)))
    assert used - {"OUTPUT_TIMEOUT"} <= known


def test_commissioning_alert_types_exist_in_contract():
    contract = _text(DOCS / "02-kontrak-mqtt.md")
    for alert in set(
        re.findall(r"(?i:alert)(?: CRITICAL| INFO)? `([A-Z_]+)`", _text(COMMISSIONING))
    ):
        assert f"| `{alert}` |" in contract, alert


def test_ops_and_commissioning_relative_links_resolve():
    for path in (OPS, COMMISSIONING):
        for target in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", _text(path)):
            assert (path.parent / target).exists(), f"{path.name}: {target}"
