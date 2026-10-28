from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from fertimata_edge.config import Config, load_config, parse_config
from fertimata_edge.hal import FakeClock, Hal, create_hal

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = ROOT / "config" / "fertimata.example.yaml"
DEV_CONFIG = ROOT / "config" / "fertimata.dev.yaml"


@pytest.fixture
def dev_data() -> dict:
    """Isi mentah config dev, boleh diubah test lalu di-parse ulang."""
    return copy.deepcopy(yaml.safe_load(DEV_CONFIG.read_text(encoding="utf-8")))


@pytest.fixture
def config() -> Config:
    return load_config(DEV_CONFIG)


def make_config(data: dict) -> Config:
    return parse_config(data, "test")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def hal(config: Config, clock: FakeClock) -> Hal:
    return create_hal(config, clock)
