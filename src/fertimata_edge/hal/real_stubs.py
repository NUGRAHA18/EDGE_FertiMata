"""Pengganti GPIO/kamera untuk `hal: real` sampai F7/F8 selesai (uji bus asli F3.9).
GPIO melaporkan HOME dan END aktif (= kabel putus, §6.1), jadi trolley terkunci: keadaan aman."""

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
