"""Perintah pengembangan lintas platform (Windows tidak punya `make`).

python tasks.py setup    # buat .venv + pasang paket (sekali)
python tasks.py check    # lint + format-check + test (jalankan sebelum commit)
python tasks.py test     # pytest saja; argumen tambahan diteruskan, mis. -k config
python tasks.py lint     # ruff check
python tasks.py fmt      # ruff format + perbaikan otomatis
python tasks.py run      # agent dengan config/fertimata.dev.yaml (hal: fake)
"""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _run(*args: str) -> int:
    print("$", " ".join(args), flush=True)
    return subprocess.call(args, cwd=ROOT)


def setup() -> int:
    if not PY.exists():
        if sys.version_info[:2] != (3, 10):
            print(f"Peringatan: Jetson memakai Python 3.10, venv ini {sys.version.split()[0]}")
        venv.create(VENV, with_pip=True)
    return _run(str(PY), "-m", "pip", "install", "-e", ".[dev]")


def lint() -> int:
    return _run(str(PY), "-m", "ruff", "check", ".")


def fmt() -> int:
    return _run(str(PY), "-m", "ruff", "check", "--fix", ".") or _run(
        str(PY), "-m", "ruff", "format", "."
    )


def test(*extra: str) -> int:
    return _run(str(PY), "-m", "pytest", *extra)


def check() -> int:
    return lint() or _run(str(PY), "-m", "ruff", "format", "--check", ".") or test()


def run(*extra: str) -> int:
    return _run(str(PY), "-m", "fertimata_edge", "--config", "config/fertimata.dev.yaml", *extra)


COMMANDS = {"setup": setup, "lint": lint, "fmt": fmt, "test": test, "check": check, "run": run}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    name, *rest = sys.argv[1:]
    handler = COMMANDS[name]
    sys.exit(handler(*rest) if name in ("test", "run") else handler())
