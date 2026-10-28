"""Aturan kode yang bisa dicek statis (CLAUDE.md, S18)."""

from __future__ import annotations

import re

from .conftest import ROOT

SRC = ROOT / "src" / "fertimata_edge"


def _sources():
    return sorted(SRC.rglob("*.py"))


def test_s18_no_shutdown_or_reboot_calls():
    # S18: Jetson tidak pernah di-shutdown oleh software (Arsitektur §7.4).
    pattern = re.compile(r"\b(shutdown|poweroff|reboot|halt)\b\s*['\"\s-]", re.IGNORECASE)
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if pattern.search(code) or re.search(r"systemctl\s+(poweroff|reboot|halt)", code):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == []


def test_logic_does_not_import_hardware_libraries():
    # Hanya implementasi HAL asli (hal/real*) yang boleh mengimpor pustaka hardware.
    banned = re.compile(r"^\s*(import|from)\s+(pymodbus|gpiod|cv2)\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in _sources()
        if not path.relative_to(SRC).as_posix().startswith("hal/real")
        and banned.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_python_311_only_features():
    # Jetson memakai Python 3.10 (Ubuntu 22.04).
    banned = re.compile(
        r"\b(asyncio\.TaskGroup|asyncio\.timeout\(|ExceptionGroup|except\*|tomllib|"
        r"datetime\.UTC|typing\.Self|from typing import .*\bSelf\b|StrEnum)"
    )
    offenders = [
        str(path.relative_to(ROOT))
        for path in _sources()
        if banned.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
