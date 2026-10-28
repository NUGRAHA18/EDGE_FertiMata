"""sd_notify untuk unit systemd Type=notify + WatchdogSec (F10.2)."""

from __future__ import annotations

import asyncio
import os
import socket

import pytest

from fertimata_edge.app import Agent
from fertimata_edge.sdnotify import SystemdNotifier

from .helpers import drive


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, address: str, message: bytes) -> None:
        if self.fail:
            raise ConnectionRefusedError("socket hilang")
        self.sent.append((address, message.decode()))

    @property
    def states(self) -> list[str]:
        return [message for _, message in self.sent]


def test_from_env_without_systemd_is_silent():
    notifier = SystemdNotifier.from_env({})
    assert not notifier.enabled and not notifier.watchdog_enabled
    assert notifier.ready() is False


def test_from_env_reads_socket_and_watchdog():
    env = {"NOTIFY_SOCKET": "/run/systemd/notify", "WATCHDOG_USEC": "30000000"}
    notifier = SystemdNotifier.from_env(env)
    assert notifier.address == "/run/systemd/notify"
    assert notifier.watchdog_s == 30.0 and notifier.watchdog_enabled

    own = SystemdNotifier.from_env({**env, "WATCHDOG_PID": str(os.getpid())})
    assert own.watchdog_enabled


def test_watchdog_for_other_process_or_invalid_is_ignored():
    base = {"NOTIFY_SOCKET": "/run/systemd/notify"}
    other = SystemdNotifier.from_env({**base, "WATCHDOG_USEC": "30000000", "WATCHDOG_PID": "1"})
    assert other.enabled and not other.watchdog_enabled
    assert SystemdNotifier.from_env({**base, "WATCHDOG_USEC": "0"}).watchdog_s is None
    assert SystemdNotifier.from_env({**base, "WATCHDOG_USEC": "abc"}).watchdog_s is None
    assert SystemdNotifier.from_env({"WATCHDOG_USEC": "30000000"}).watchdog_enabled is False


def test_send_failure_does_not_raise(caplog):
    notifier = SystemdNotifier("/run/systemd/notify", send=Recorder(fail=True))
    assert notifier.notify("WATCHDOG=1") is False
    assert "sd_notify gagal" in caplog.text


async def test_watchdog_pings_every_half_interval(clock):
    rec = Recorder()
    notifier = SystemdNotifier("/run/systemd/notify", watchdog_s=30, send=rec)
    task = asyncio.create_task(notifier.run_watchdog(clock))
    await asyncio.sleep(0)
    assert rec.states == ["WATCHDOG=1"]
    await clock.advance(14.9)
    assert len(rec.states) == 1
    await clock.advance(0.2)
    assert len(rec.states) == 2
    await clock.advance(15)
    assert len(rec.states) == 3
    task.cancel()


async def test_agent_notifies_ready_watchdog_and_stopping(config, hal, clock):
    rec = Recorder()
    notifier = SystemdNotifier("/run/systemd/notify", watchdog_s=30, send=rec)
    agent = Agent(config, hal, notifier=notifier)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await drive(clock, agent.ready.wait())
    await asyncio.sleep(0)
    states = rec.states
    # Ping pertama sebelum READY: boot yang macet juga dihentikan systemd.
    assert states[0] == "WATCHDOG=1"
    assert sum(s.startswith("READY=1") for s in states) == 1
    assert "STOPPING=1" not in states

    before = states.count("WATCHDOG=1")
    await clock.advance(31)
    assert rec.states.count("WATCHDOG=1") >= before + 2

    stop.set()
    await asyncio.wait_for(task, 1)
    assert rec.states[-1] == "STOPPING=1"


async def test_agent_without_watchdog_does_not_stop_itself(config, hal, clock):
    # Tugas watchdog yang langsung selesai akan dianggap "tugas latar mati" oleh `_wait`.
    rec = Recorder()
    agent = Agent(config, hal, notifier=SystemdNotifier("/run/x", send=rec))
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await drive(clock, agent.ready.wait())
    await clock.advance(120)
    assert not task.done()
    assert "WATCHDOG=1" not in rec.states and rec.states[0].startswith("READY=1")
    stop.set()
    await asyncio.wait_for(task, 1)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX tidak ada (Windows)")
def test_real_datagram_reaches_socket(tmp_path):
    path = str(tmp_path / "notify")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(path)
        assert SystemdNotifier(path).ready()
        assert server.recv(100).startswith(b"READY=1")
