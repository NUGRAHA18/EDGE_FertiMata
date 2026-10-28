"""Suhu SoC palsu dari `FakeWorld`."""

from __future__ import annotations

from ..base import HalError, SocSensor
from .world import FakeWorld


class FakeSoc(SocSensor):
    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    async def read_temperature_c(self) -> float:
        if self.world.soc_temperature_c is None:
            raise HalError("zona termal palsu tidak terbaca")
        return self.world.soc_temperature_c
