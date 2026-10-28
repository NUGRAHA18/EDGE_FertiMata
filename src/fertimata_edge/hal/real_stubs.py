"""Pengganti GPIO dan kamera di mode `hal: real` sampai HAL aslinya selesai (F7, F8).

Dibuat supaya bus Modbus asli bisa diuji di Jetson (F3.9) sebelum papan GPIO dan kamera siap.
GPIO pengganti melaporkan HOME dan END **aktif**, sama dengan kabel limit putus (§6.1), sehingga
logika trolley menganggap trolley terkunci. Itu keadaan aman.
"""

from __future__ import annotations

import asyncio
import logging

from .base import Camera, CameraError, Frame, GpioEvent, GpioLines

log = logging.getLogger(__name__)


class LockedGpio(GpioLines):
    available = False

    async def open(self) -> None:
        log.warning(
            "GPIO asli belum tersedia (F7): limit HOME/END dianggap aktif, trolley terkunci"
        )

    async def close(self) -> None:
        pass

    def is_active(self, line: str) -> bool:
        if line not in ("reed", "home", "end"):
            raise KeyError(f"jalur GPIO tidak dikenal: {line}")
        return line != "reed"

    def subscribe(self) -> asyncio.Queue[GpioEvent]:
        return asyncio.Queue()

    def unsubscribe(self, queue: asyncio.Queue[GpioEvent]) -> None:
        pass


class UnavailableCamera(Camera):
    async def open(self) -> None:
        log.warning("kamera asli belum tersedia (F8)")

    async def close(self) -> None:
        pass

    async def capture(self) -> Frame:
        raise CameraError("kamera asli belum tersedia (F8)")
