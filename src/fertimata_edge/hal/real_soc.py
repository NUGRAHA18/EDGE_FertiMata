"""Suhu SoC Jetson dari sysfs (`/sys/class/thermal/thermal_zone*/temp`, satuan m°C).

Pola berkas ada di config (`power.soc_thermal_glob`). Nama zona di Jetson Orin Nano belum dicek,
jadi diambil nilai tertinggi dari semua zona yang cocok: yang terpanas yang menentukan peringatan.
"""

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
