"""Suhu SoC Jetson dari sysfs (`power.soc_thermal_glob`, m°C); diambil zona terpanas."""

from __future__ import annotations

import asyncio
import glob

from .base import HalError, SocSensor


class SysfsSoc(SocSensor):
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern

    async def read_temperature_c(self) -> float:
        return await asyncio.get_running_loop().run_in_executor(None, self._read)

    def _read(self) -> float:
        values = []
        for path in sorted(glob.glob(self.pattern)):
            try:
                with open(path, encoding="ascii") as handle:
                    values.append(int(handle.read().strip()) / 1000.0)
            except (OSError, ValueError):
                continue  # zona yang tidak bisa dibaca (mis. sensor mati) dilewati
        if not values:
            raise HalError(f"tidak ada zona termal yang terbaca: {self.pattern}")
        return max(values)
