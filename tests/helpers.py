from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from fertimata_edge.hal import FakeClock

T = TypeVar("T")


async def drive(
    clock: FakeClock, awaitable: Awaitable[T], step: float = 0.01, limit: int = 10_000
) -> T:
    """Jalankan `awaitable` sambil memajukan jam palsu sedikit demi sedikit (jeda bus, retry)."""
    task = asyncio.ensure_future(awaitable)
    for _ in range(limit):
        for _ in range(5):
            await asyncio.sleep(0)
        if task.done():
            return task.result()
        await clock.advance(step)
    task.cancel()
    raise AssertionError("awaitable tidak selesai")
