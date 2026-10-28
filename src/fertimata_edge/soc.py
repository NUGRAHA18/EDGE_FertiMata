"""Suhu SoC Jetson → telemetri `jetson.soc_temperature` dan alert `SOC_HOT` (F5.4).

Kunci metrik mengikuti katalog backend (Kontrak §3). Bacaan yang gagal tidak dikirim.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .config import Config
from .hal import Clock, HalError, SocSensor
from .logs import fields
from .poller import TelemetrySink

log = logging.getLogger(__name__)

SOC_METRIC = "jetson.soc_temperature"


class SocMonitor:
    def __init__(
        self, config: Config, soc: SocSensor, clock: Clock, sink: TelemetrySink | None
    ) -> None:
        self.config = config
        self.soc = soc
        self.clock = clock
        self.sink = sink
        self.gateway = next(
            (n for n, d in config.logical_devices.items() if "soc" in d.sources), None
        )
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.gateway is not None:
            self._task = asyncio.create_task(self._loop(), name="soc")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return [self._task] if self._task is not None else []

    async def _loop(self) -> None:
        while True:
            await self.read_once()
            await self.clock.sleep(self.config.polling.soc_s)

    async def read_once(self) -> float | None:
        try:
            temperature = round(await self.soc.read_temperature_c(), 1)
        except HalError as err:
            log.warning("suhu SoC tidak terbaca", extra=fields(err=err))
            return None
        if self.sink is None or self.gateway is None:
            return temperature
        self.sink.publish_telemetry(self.gateway, {SOC_METRIC: temperature})
        warn = self.config.power.soc_warn_c
        if temperature >= warn:
            self.sink.publish_alert(
                self.gateway,
                "SOC_HOT",
                "WARNING",
                "Alat kendali terlalu panas",
                f"Suhu prosesor {str(temperature).replace('.', ',')} °C. Pastikan kipas panel "
                "berputar dan lubang udara tidak tertutup.",
                metadata={"temperature": temperature, "warnAt": warn},
            )
        return temperature
