"""Kamera palsu: menghasilkan PNG uji tanpa OpenCV."""

from __future__ import annotations

import struct
import zlib

from ..base import Camera, CameraError, Frame
from .world import FakeWorld


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def make_test_png(width: int, height: int, tint: int = 0) -> bytes:
    """PNG RGB 8-bit berisi gradasi; `tint` membedakan foto antar stasiun."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        for x in range(width):
            rows += bytes(((x * 255) // max(1, width - 1), (y * 255) // max(1, height - 1), tint))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _png_chunk(b"IEND", b"")
    )


class FakeCamera(Camera):
    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.is_open = False
        self.fail = False  # uji CAMERA_FAIL
        self.captures = 0
        # Ketajaman foto berikutnya (uji gerbang blur); kosong = `fake.image_sharpness`.
        self.sharpness_queue: list[float] = []
        # Stasiun di bawah kamera saat tiap foto diambil (None = di antara magnet).
        self.log: list[int | None] = []

    async def open(self) -> None:
        self.is_open = True

    async def close(self) -> None:
        self.is_open = False

    async def capture(self) -> Frame:
        if not self.is_open or self.fail:
            raise CameraError("kamera palsu tidak terbaca")
        fake = self.world.config.fake
        station = self.world.trolley.station_at() or 0
        self.captures += 1
        self.log.append(self.world.trolley.station_at())
        sharpness = self.sharpness_queue.pop(0) if self.sharpness_queue else fake.image_sharpness
        data = make_test_png(fake.image_width, fake.image_height, tint=(station * 25) % 256)
        return Frame(data, "png", fake.image_width, fake.image_height, sharpness)
