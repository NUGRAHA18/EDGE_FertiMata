"""Siklus hidup agent: buka HAL, jalankan tugas latar, berhenti bersih.

Urutan boot (Konsep §4.1): HAL → bus manager → S1 (OFF semua relay + baca balik) → baca PZEM
(powerSource awal) → link MQTT → polling relay, penjaga limit, trolley, daya, SoC, larutan, poller
sensor, jadwal patroli.
Aturan §12.1: S1 selesai sebelum link MQTT dan tugas lain dimulai, jadi tidak ada perintah yang
bisa sampai ke relay sebelum relay terbukti OFF.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from . import __version__
from .analog import AnalogReader
from .bus import BusManager
from .captures import CaptureStore
from .commands import CommandHandler, CommandRouter, OutputCommandHandler
from .config import Config
from .dosing import DosingLoop
from .hal import Hal
from .health import DeviceHealth
from .link import Link, MqttTransport
from .logs import fields
from .outputs import Outputs
from .patrol import Patrol
from .poller import Poller
from .power import PowerMonitor
from .safety import Safety
from .sdnotify import SystemdNotifier
from .soc import SocMonitor
from .trolley import Trolley
from .virtual import VirtualActuators

log = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        config: Config,
        hal: Hal,
        transport: MqttTransport | None = None,
        handler: CommandHandler | None = None,
        notifier: SystemdNotifier | None = None,
    ) -> None:
        self.config = config
        self.hal = hal
        self.notifier = notifier or SystemdNotifier(None)
        self.health = DeviceHealth(config)
        self.bus = BusManager(hal.bus, config, hal.clock)
        self.bus.device_listeners.append(self.health.report)
        self.outputs = Outputs(config, self.bus, hal.clock)
        self.power = PowerMonitor(config, self.bus, self.outputs, hal.clock)
        self.safety = Safety(
            config, self.outputs, hal.gpio, hal.clock, power_source=lambda: self.power.source
        )
        self.gateway = next(
            (n for n, d in config.logical_devices.items() if "soc" in d.sources), None
        )
        # Aktuator virtual dipasang ke router setelah trolley/patroli dibuat (di bawah).
        self.router = CommandRouter(
            config, OutputCommandHandler(config, self.outputs, self.safety, hal.clock), None
        )
        self.link = (
            Link(
                config,
                transport,
                hal.clock,
                self.health,
                handler or self.router,
                status_extras=self.status_extras,
            )
            if transport is not None
            else None
        )
        self.outputs.sink = self.link
        self.outputs.listeners.append(self._on_relay_changed)
        self.poller = Poller(config, self.bus, hal.clock, self.link)
        self.power.sink = self.link
        self.power.listeners.append(self._on_power_changed)
        self.poller.read_listeners.append(self.power.on_read)
        self.bus.device_listeners.append(self.power.on_device)
        self.bus.bus_listeners.append(self.power.on_bus)
        self.soc = SocMonitor(config, hal.soc, hal.clock, self.link)
        self.trolley = (
            Trolley(config, self.outputs, self.safety, hal.gpio, hal.clock, self.link)
            if config.safety.travel is not None
            else None
        )
        self.analog = AnalogReader(
            config, self.bus, self.outputs, self.safety, hal.clock, self.link
        )
        self.patrol = (
            Patrol(
                config,
                self.trolley,
                self.outputs,
                self.safety,
                hal.camera,
                hal.clock,
                CaptureStore(config),
                power_source=lambda: self.power.source,
                sink=self.link,
            )
            if self.trolley is not None
            else None
        )
        if self.patrol is not None:
            self.poller.read_listeners.append(self.patrol.on_read)
        # Loop dosing F9 (Kontrak §5.1 DOSING-LOOP), dijalankan lewat aktuator virtual.
        self.dosing = (
            DosingLoop(config, self.outputs, self.safety, self.analog, hal.clock, self.link)
            if "dosing_loop" in config.virtual_actuators
            else None
        )
        self.virtual = (
            VirtualActuators(
                config,
                self.trolley,
                self.patrol,
                self.safety,
                hal.clock,
                sink=self.link,
                dosing=self.dosing,
            )
            if self.trolley is not None and config.virtual_actuators
            else None
        )
        if self.virtual is not None:
            self.router.virtual = self.virtual
            self.virtual.listeners.append(self._on_virtual_changed)
        self.ready = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def run(self, stop: asyncio.Event) -> None:
        log.info(
            "agent mulai",
            extra=fields(
                version=__version__, site=self.config.agent.site, hal=self.config.agent.hal
            ),
        )
        try:
            await self.hal.open()
            self._start_background()
            await self.bus.start()
            if not await self.outputs.all_off():
                log.error("S1: tidak semua relay terbukti OFF, lihat alert RELAY_MISMATCH/log")
            await self._initial_power()
            if self.link is not None:
                await self.link.start()
            self.outputs.start()
            self.safety.start()
            if self.trolley is not None:
                self.trolley.start()
            self.power.start()
            self.soc.start()
            self.analog.start()
            self.poller.start()
            if self.patrol is not None:
                self.patrol.start()
            self.ready.set()
            log.info("agent siap")
            # READY dikirim setelah S1 dan semua tugas jalan; broker MQTT tidak ditunggu (§4.1).
            self.notifier.ready()
            await self._wait(stop)
        finally:
            await self._shutdown()

    async def _initial_power(self) -> None:
        # Konsep §4.1 langkah 4: powerSource awal sebelum perintah diterima. PZEM yang diam baru
        # dinyatakan diam setelah `bus.fail_threshold` kegagalan, jadi dicoba sebanyak itu.
        pzem = self.config.power.pzem_device
        if pzem not in self.poller.profiles:
            return
        for _ in range(self.config.bus.fail_threshold):
            await self.poller.poll([pzem])
            if self.power.source is not None:
                break
        log.info("sumber daya awal", extra=fields(power=self.power.source))

    # --- status (Kontrak §4.2, F4.7) ----------------------------------------------------------

    def status_extras(self, device_id: str) -> dict:
        extras: dict = {}
        if device_id == self.gateway:
            extras["metadata"] = {"agentVersion": __version__}
            # Q7: tanpa data daya field dihilangkan; UNKNOWN akan memblokir semua perintah backend.
            if self.power.source is not None:
                extras["power_source"] = self.power.source
        actuators = self.outputs.actuator_states(device_id)
        if self.virtual is not None:
            actuators |= self.virtual.actuator_states(device_id)
        if actuators:
            extras["actuators"] = actuators
        return extras

    def _on_relay_changed(self, device: str) -> None:
        # State berubah (ACK, watchdog, darurat, relay mati sendiri): kirim status saat itu juga.
        if self.link is not None:
            logical = self.outputs.logical_of[device]
            self.link.publish_status(logical, **self.status_extras(logical))

    def _on_virtual_changed(self, device_id: str) -> None:
        # Kontrak §4.2/§5.1: state ON/OFF aktuator virtual dipakai interlock backend.
        if self.link is not None:
            self.link.publish_status(device_id, **self.status_extras(device_id))

    def _on_power_changed(self, source: str) -> None:
        if self.link is not None and self.gateway is not None:
            self.link.publish_status(self.gateway, **self.status_extras(self.gateway))

    def _start_background(self) -> None:
        world = self.hal.world
        if world is not None:
            self._spawn(world.run(self.hal.clock), "fake-world")
        # Ping mulai sebelum S1: boot yang macet juga di-restart systemd (F10.2).
        if self.notifier.watchdog_enabled:
            self._spawn(self.notifier.run_watchdog(self.hal.clock), "sd-watchdog")

    def _spawn(self, coro, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.append(task)

    async def _wait(self, stop: asyncio.Event) -> None:
        # Tugas latar yang mati karena galat menghentikan agent; systemd akan me-restart (S20 → S1).
        stop_task = asyncio.create_task(stop.wait(), name="stop")
        watched = [
            stop_task,
            *self._tasks,
            *(self.link.tasks if self.link else []),
            *self.poller.tasks,
            *self.outputs.tasks,
            *self.safety.tasks,
            *self.power.tasks,
            *self.soc.tasks,
            *self.analog.tasks,
            *(self.trolley.tasks if self.trolley else []),
            *(self.patrol.tasks if self.patrol else []),
            *([self.bus.task] if self.bus.task else []),
        ]
        try:
            done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_task.cancel()
        for task in done:
            if task is not stop_task and not task.cancelled() and task.exception():
                raise task.exception()  # type: ignore[misc]

    async def _shutdown(self) -> None:
        log.info("agent berhenti")
        self.notifier.stopping()
        await self.poller.stop()
        await self.analog.stop()
        if self.virtual is not None:
            await self.virtual.stop()
        if self.patrol is not None:
            await self.patrol.stop()
        if self.trolley is not None:
            await self.trolley.stop()
        await self.soc.stop()
        await self.power.stop()
        await self.safety.stop()
        await self.outputs.stop()
        if self.link is not None:
            with contextlib.suppress(Exception):
                await self.link.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        with contextlib.suppress(Exception):
            await self.bus.stop()
        await self.hal.close()
        log.info("agent selesai")
