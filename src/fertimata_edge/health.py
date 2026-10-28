"""Kesehatan sumber data per device logis, dasar keputusan heartbeat (Kontrak §4.2).

Device logis yang perangkatnya tidak menjawab tidak diberi heartbeat, sehingga backend
menandainya OFFLINE sendiri. Sumber non-Modbus (`gpio`, `soc`) ada di proses ini, jadi selalu
hidup selama agent hidup. Sumber Modbus dianggap belum hidup sampai bus manager (F3) melaporkan
jawaban pertama.

Device dengan beberapa sumber tetap hidup selama minimal satu sumber menjawab (usulan Q3 di
TASKS.md); sumber yang diam dilaporkan lewat alert `MODBUS_TIMEOUT` dan metriknya dihilangkan.
"""

from __future__ import annotations

from .config import NON_MODBUS_SOURCES, Config


class DeviceHealth:
    def __init__(self, config: Config) -> None:
        self._sources = {
            name: tuple(logical.sources) for name, logical in config.logical_devices.items()
        }
        self._alive: dict[str, bool] = {
            src: src in NON_MODBUS_SOURCES for sources in self._sources.values() for src in sources
        }

    @property
    def logical_devices(self) -> list[str]:
        return list(self._sources)

    def report(self, source: str, alive: bool) -> None:
        """Dipanggil bus manager setiap perangkat menjawab (True) atau dinyatakan diam (False)."""
        if source not in self._alive:
            raise KeyError(f"sumber tidak dikenal: {source}")
        self._alive[source] = alive

    def source_alive(self, source: str) -> bool:
        return self._alive[source]

    def device_alive(self, logical_device: str) -> bool:
        return any(self._alive[src] for src in self._sources[logical_device])

    def alive_devices(self) -> list[str]:
        return [name for name in self._sources if self.device_alive(name)]
