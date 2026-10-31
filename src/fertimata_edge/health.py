"""Kesehatan sumber per device logis (§4.2): device tanpa sumber hidup tidak diberi heartbeat.
gpio/soc selalu hidup; Modbus hidup sejak jawaban pertama; multi-sumber cukup satu menjawab (Q3)."""

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
