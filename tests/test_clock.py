from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from fertimata_edge.hal import FakeClock, SystemClock, parse_iso, to_iso_z


def test_iso_z_format():
    moment = datetime(2026, 10, 22, 15, 0, 0, 750000, tzinfo=timezone(timedelta(hours=7)))
    assert to_iso_z(moment) == "2026-10-22T08:00:00Z"


def test_iso_z_rejects_naive():
    with pytest.raises(ValueError):
        to_iso_z(datetime(2026, 10, 22, 8, 0, 0))


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-22T08:00:30Z",
        "2026-10-22T08:00:30.123Z",
        "2026-10-22T08:00:30.1Z",
        "2026-10-22T08:00:30.123456789Z",  # Instant Java presisi nano
        "2026-10-22T15:00:30+07:00",
    ],
)
def test_parse_iso_variants(text):
    assert parse_iso(text).replace(microsecond=0) == datetime(
        2026, 10, 22, 8, 0, 30, tzinfo=timezone.utc
    )


def test_parse_iso_rejects_naive():
    with pytest.raises(ValueError):
        parse_iso("2026-10-22T08:00:30")


def test_system_clock_is_utc():
    assert SystemClock().now().utcoffset() == timedelta(0)


async def test_fake_clock_wakes_sleepers_in_order():
    clock = FakeClock()
    woke: list[tuple[str, float]] = []

    async def sleeper(name: str, seconds: float) -> None:
        await clock.sleep(seconds)
        woke.append((name, clock.monotonic()))

    tasks = [asyncio.create_task(sleeper("b", 5)), asyncio.create_task(sleeper("a", 2))]
    await asyncio.sleep(0)
    await clock.advance(1)
    assert woke == []
    await clock.advance(10)
    assert woke == [("a", 2), ("b", 5)]
    assert clock.monotonic() == 11
    await asyncio.gather(*tasks)


async def test_fake_clock_repeated_ticker():
    clock = FakeClock()
    ticks: list[float] = []

    async def ticker() -> None:
        while True:
            await clock.sleep(1)
            ticks.append(clock.monotonic())

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    await clock.advance(3.5)
    task.cancel()
    assert ticks == [1, 2, 3]
    assert clock.now() == FakeClock().now() + timedelta(seconds=3.5)
