"""GPIO palsu: reed/HOME/END mengikuti posisi trolley di `FakeWorld`."""

from __future__ import annotations

import asyncio

from ..base import Clock, GpioEvent, GpioLines
from .world import GPIO_LINES, FakeWorld


class FakeGpio(GpioLines):
    def __init__(self, world: FakeWorld, clock: Clock) -> None:
        self.world = world
        self.clock = clock
        self.is_open = False
        self._queues: list[asyncio.Queue[GpioEvent]] = []
        world.add_gpio_listener(self._on_change)

    async def open(self) -> None:
        self.is_open = True

    async def close(self) -> None:
        self.is_open = False

    def is_active(self, line: str) -> bool:
        if line not in GPIO_LINES:
            raise KeyError(f"jalur GPIO tidak dikenal: {line}")
        return self.world.gpio_active(line)

    def subscribe(self) -> asyncio.Queue[GpioEvent]:
        queue: asyncio.Queue[GpioEvent] = asyncio.Queue()
        self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[GpioEvent]) -> None:
        if queue in self._queues:
            self._queues.remove(queue)

    def _on_change(self, line: str, active: bool) -> None:
        if not self.is_open:
            return
        event = GpioEvent(line, active, self.clock.monotonic())
        for queue in self._queues:
            queue.put_nowait(event)
