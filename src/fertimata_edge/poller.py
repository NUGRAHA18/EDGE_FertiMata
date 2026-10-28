"""Polling sensor terjadwal per kelompok → telemetri per device logis (Arsitektur §4.6, F3.7).

Metrik yang gagal dibaca dihilangkan, tidak diisi angka palsu (Kontrak §4.1). Alert
`MODBUS_TIMEOUT` (per perangkat) dan `BUS_DOWN` (seluruh bus) dikirim selama gangguan berlangsung;
link menahan pengulangannya sesuai `mqtt.alert_repeat_s` (F3.8).

Relay (`relays_s`) dibaca `outputs`; EC/pH lewat ADC punya urutan baca sendiri (F6).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any, Protocol

from .bus import BusManager
from .config import Config
from .hal import Clock
from .logs import fields
from .profiles import RegisterMapProfile, build_sensor_profiles

log = logging.getLogger(__name__)

# Kelompok polling (kunci di `polling`) untuk setiap profil sensor.
GROUP_OF_PROFILE = {
    "pzem016": "pzem_s",
    "xy_md02": "air_light_s",
    "lux_rs485": "air_light_s",
    "soil_7in1": "soil_s",
    "ds18b20_mb": "tank_s",
}


class TelemetrySink(Protocol):
    def publish_telemetry(self, device_id: str, data: dict[str, Any]) -> bool: ...

    def publish_alert(
        self, device_id: str, type: str, severity: str, title: str, message: str, **kwargs: Any
    ) -> bool: ...


def device_label(config: Config, name: str) -> str:
    return config.devices[name].label or f"alat {name}"


def alert_if_silent(
    config: Config, bus: BusManager, sink: TelemetrySink, name: str, logical: str
) -> None:
    """Alert `MODBUS_TIMEOUT` bila bus manager sudah menyatakan perangkat `name` diam."""
    if bus.is_responding(name) is not False:
        return
    device = config.devices[name]
    label = device_label(config, name)
    sink.publish_alert(
        logical,
        "MODBUS_TIMEOUT",
        "WARNING",
        f"{label[0].upper()}{label[1:]} tidak terbaca",
        f"Data dari {label} berhenti masuk. Periksa kabel dan daya alat tersebut.",
        metadata={
            "device": name,
            "modbusAddress": device.address,
            "profile": device.profile,
            "failures": bus.states[name].failures,
        },
    )


class Poller:
    def __init__(
        self, config: Config, bus: BusManager, clock: Clock, sink: TelemetrySink | None
    ) -> None:
        self.config = config
        self.bus = bus
        self.clock = clock
        self.sink = sink
        self.profiles: dict[str, RegisterMapProfile] = build_sensor_profiles(config)
        self.logical_of: dict[str, str] = {
            src: name for name, ld in config.logical_devices.items() for src in ld.sources
        }
        self.gateway = next(
            (n for n, d in config.logical_devices.items() if "soc" in d.sources), None
        )
        self.groups: dict[str, list[str]] = {}
        for name, profile in self.profiles.items():
            group = GROUP_OF_PROFILE[profile.device.profile]
            self.groups.setdefault(group, []).append(name)
        self._tasks: list[asyncio.Task[None]] = []
        # Dipanggil setelah setiap perangkat dibaca: (nama perangkat, metrik yang terbaca).
        self.read_listeners: list[Callable[[str, dict[str, float]], None]] = []

    # --- siklus hidup -------------------------------------------------------------------------

    def start(self) -> None:
        for group, names in self.groups.items():
            interval = getattr(self.config.polling, group)
            self._tasks.append(
                asyncio.create_task(self._loop(names, interval), name=f"poll-{group}")
            )
            log.info("polling dimulai", extra=fields(group=group, devices=",".join(names)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return list(self._tasks)

    async def _loop(self, names: list[str], interval: float) -> None:
        while True:
            await self.poll(names)
            await self.clock.sleep(interval)

    # --- satu siklus --------------------------------------------------------------------------

    async def poll(self, names: list[str]) -> dict[str, dict[str, float]]:
        """Baca perangkat `names`, kirim satu telemetri per device logis. Mengembalikan datanya."""
        per_logical: dict[str, dict[str, float]] = {}
        for name in names:
            result = await self.profiles[name].read(self.bus)
            if result.errors:
                log.debug("baca gagal", extra=fields(device=name, errors="; ".join(result.errors)))
                self._alert_if_silent(name)
            if result.values:
                per_logical.setdefault(self.logical_of[name], {}).update(result.values)
            for listener in self.read_listeners:
                listener(name, result.values)
        self._alert_if_bus_down()
        if self.sink is not None:
            for logical, data in per_logical.items():
                self.sink.publish_telemetry(logical, data)
        return per_logical

    def _alert_if_silent(self, name: str) -> None:
        if self.sink is not None:
            alert_if_silent(self.config, self.bus, self.sink, name, self.logical_of[name])

    def _alert_if_bus_down(self) -> None:
        if self.sink is None or self.gateway is None:
            return
        polled = list(self.profiles)
        port_missing = self.bus.bus_available is False
        all_silent = bool(polled) and all(self.bus.is_responding(n) is False for n in polled)
        if not (port_missing or all_silent):
            return
        self.sink.publish_alert(
            self.gateway,
            "BUS_DOWN",
            "CRITICAL",
            "Semua sensor dan relay tidak terbaca",
            "Periksa kabel data dan colokan adaptor USB di panel. Pompa tidak bisa dikendalikan "
            "sampai sambungan pulih.",
            metadata={
                "port": self.config.bus.port,
                "portMissing": port_missing,
                "silentDevices": [n for n in polled if self.bus.is_responding(n) is False],
            },
        )
