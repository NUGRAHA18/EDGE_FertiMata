"""Protokol sd_notify systemd (F10.2): READY, STOPPING, dan ping watchdog.

Unit `fertimata-edge.service` memakai `Type=notify` + `WatchdogSec`. Ping dikirim dari tugas di
event loop, jadi loop yang macet (mis. pemanggilan blocking) berhenti mengirim ping dan systemd
me-restart agent → S1 mematikan semua relay (S20). Tugas latar yang mati sudah menghentikan agent
lewat `_wait`.

Ditulis sendiri (±30 baris) supaya tidak menambah dependensi. Tanpa `NOTIFY_SOCKET` (laptop, test,
dijalankan manual) semua pemanggilan diam.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable, Mapping

from .hal import Clock

log = logging.getLogger(__name__)


def _send_datagram(address: str, message: bytes) -> None:
    # Awalan "@" = abstract namespace Linux (systemd memakainya untuk socket notify tertentu).
    target = "\0" + address[1:] if address.startswith("@") else address
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:  # type: ignore[attr-defined]
        sock.connect(target)
        sock.sendall(message)


class SystemdNotifier:
    def __init__(
        self,
        address: str | None,
        watchdog_s: float | None = None,
        send: Callable[[str, bytes], None] = _send_datagram,
    ) -> None:
        self.address = address
        self.watchdog_s = watchdog_s
        self._send = send

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SystemdNotifier:
        env = os.environ if environ is None else environ
        address = env.get("NOTIFY_SOCKET") or None
        watchdog_s = None
        usec = env.get("WATCHDOG_USEC")
        pid = env.get("WATCHDOG_PID")
        # WATCHDOG_PID lain = variabel warisan proses induk, bukan untuk kita (sd_watchdog_enabled).
        if usec and usec.isdigit() and int(usec) > 0 and (not pid or pid == str(os.getpid())):
            watchdog_s = int(usec) / 1_000_000
        return cls(address, watchdog_s)

    @property
    def enabled(self) -> bool:
        return self.address is not None

    @property
    def watchdog_enabled(self) -> bool:
        return self.address is not None and self.watchdog_s is not None

    def notify(self, state: str) -> bool:
        if self.address is None:
            return False
        try:
            self._send(self.address, state.encode())
        except OSError as err:
            # Gagal notify tidak boleh menjatuhkan agent; systemd yang akan bereaksi (timeout).
            log.warning("sd_notify gagal: %s", err)
            return False
        return True

    def ready(self) -> bool:
        return self.notify("READY=1\nSTATUS=agent siap")

    def stopping(self) -> bool:
        return self.notify("STOPPING=1")

    async def run_watchdog(self, clock: Clock) -> None:
        """Ping setiap setengah `WatchdogSec` (sd_watchdog_enabled(3)). Tidak pernah selesai,
        jadi hanya dijalankan bila `watchdog_enabled`."""
        assert self.watchdog_s is not None
        interval = self.watchdog_s / 2
        while True:
            self.notify("WATCHDOG=1")
            await clock.sleep(interval)
