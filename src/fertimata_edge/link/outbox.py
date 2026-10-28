"""Antrean pesan keluar selama broker tidak tersambung (TASKS F2.6).

Telemetri dan status boleh dibuang (yang tertua dulu) karena nilai baru akan menyusul. ACK, alert,
dan hasil AI tidak pernah dibuang: ACK adalah satu-satunya jalan command keluar dari SENT
(Konsep §4.3), dan alert bisa berisi bahaya.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)

DROPPABLE_KINDS = frozenset({"telemetry", "status"})


@dataclass(frozen=True)
class OutgoingMessage:
    kind: str  # telemetry / status / command_ack / alert / ai
    topic: str
    payload: bytes

    @property
    def droppable(self) -> bool:
        return self.kind in DROPPABLE_KINDS


class Outbox:
    def __init__(self, max_size: int) -> None:
        self.max_size = max_size
        self._items: deque[OutgoingMessage] = deque()
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._items)

    def push(self, message: OutgoingMessage) -> None:
        self._items.append(message)
        while len(self._items) > self.max_size:
            if not self._drop_oldest_droppable():
                # Sisa antrean semuanya penting: biarkan melebihi batas daripada membuang ACK.
                log.warning("antrean MQTT penuh oleh pesan penting (%d)", len(self._items))
                break

    def push_front(self, message: OutgoingMessage) -> None:
        """Kembalikan pesan yang gagal diserahkan ke klien MQTT, tanpa mengubah urutan."""
        self._items.appendleft(message)

    def pop(self) -> OutgoingMessage | None:
        return self._items.popleft() if self._items else None

    def _drop_oldest_droppable(self) -> bool:
        for index, item in enumerate(self._items):
            if item.droppable:
                del self._items[index]
                self.dropped += 1
                return True
        return False
