"""Jam sistem dan jam palsu, plus format waktu payload."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import re
import time
from datetime import datetime, timedelta, timezone

from .base import Clock


def to_iso_z(moment: datetime) -> str:
    """UTC ISO-8601 dengan akhiran `Z`, presisi detik (Kontrak §1)."""
    if moment.tzinfo is None:
        raise ValueError("waktu harus timezone-aware")
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


_FRACTION = re.compile(r"\.(\d+)")


def parse_iso(text: str) -> datetime:
    """Baca waktu ISO-8601 dari backend (`...Z` atau offset).

    Python 3.10 belum menerima `Z` dan hanya menerima pecahan 3 atau 6 digit, padahal `Instant`
    Java bisa menulis 1–9 digit (mis. `.123456789Z`). Pecahan dinormalkan ke 6 digit.
    """
    text = text.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"waktu tanpa zona: {text}")
    return moment.astimezone(timezone.utc)


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


class FakeClock(Clock):
    """Jam yang hanya maju lewat `advance()`. `sleep()` menunggu sampai jam dimajukan."""

    def __init__(self, start: datetime | None = None) -> None:
        self._start = start or datetime(2026, 10, 22, 8, 0, 0, tzinfo=timezone.utc)
        self._mono = 0.0
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._mono)

    def monotonic(self) -> float:
        return self._mono

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self._mono + seconds, next(self._seq), future))
        await future

    async def advance(self, seconds: float) -> None:
        """Majukan jam, membangunkan setiap `sleep` sesuai urutan tenggatnya.

        Setelah tiap kebangunan, loop diberi kesempatan berjalan supaya tugas yang langsung
        tidur lagi (mis. ticker) ikut terjadwal dengan benar di dalam rentang yang sama.
        """
        target = self._mono + seconds
        while self._sleepers and self._sleepers[0][0] <= target:
            deadline, _, future = heapq.heappop(self._sleepers)
            self._mono = max(self._mono, deadline)
            if not future.done():
                future.set_result(None)
            await _yield_loop()
        self._mono = target
        await _yield_loop()

    @property
    def pending_sleepers(self) -> int:
        return sum(1 for _, _, f in self._sleepers if not f.done())


async def _yield_loop(rounds: int = 5) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)
