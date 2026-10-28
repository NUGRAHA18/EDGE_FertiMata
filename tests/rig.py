"""Rangkaian bus + outputs + safety + eksekutor di atas HAL palsu, untuk test F4."""

from __future__ import annotations

from dataclasses import dataclass, field

from fertimata_edge.bus import BusManager
from fertimata_edge.commands import Command, CommandResult, OutputCommandHandler
from fertimata_edge.config import Config
from fertimata_edge.hal import FakeClock, Hal
from fertimata_edge.link.payloads import CommandPayload
from fertimata_edge.outputs import Outputs
from fertimata_edge.safety import Safety

from .fakes import RecordingSink
from .helpers import drive


@dataclass
class Rig:
    config: Config
    hal: Hal
    clock: FakeClock
    bus: BusManager
    outputs: Outputs
    safety: Safety
    handler: OutputCommandHandler
    sink: RecordingSink
    power: dict  # sumber daya yang dilihat safety; test boleh mengubahnya
    changes: list[str] = field(default_factory=list)

    @property
    def world(self):
        return self.hal.world

    def relay(self, output: str):
        return self.world.relay_for(output)

    def physical(self, output: str) -> bool:
        return self.world.output_on(output)

    async def run(self, awaitable, step: float = 0.01):
        return await drive(self.clock, awaitable, step=step)

    async def command(
        self, actuator: str, action: str, duration: float | None = None
    ) -> CommandResult:
        name = self.config.output_by_actuator(actuator)
        assert name is not None, actuator
        device = self.config.outputs[name].device
        logical = self.outputs.logical_of[device]
        params = {} if duration is None else {"durationSeconds": duration}
        payload = CommandPayload.model_validate(
            {"commandUid": "uid", "actuatorCode": actuator, "command": action, "parameters": params}
        )
        return await self.run(self.handler.execute(Command(logical, name, payload)))

    async def advance(self, seconds: float, step: float = 0.05) -> None:
        """Majukan jam dan simulator bersama-sama (trolley bergerak, GPIO berubah)."""
        elapsed = 0.0
        while elapsed < seconds - 1e-9:
            dt = min(step, seconds - elapsed)
            self.world.step(dt)
            await self.clock.advance(dt)
            elapsed += dt


async def build_rig(config: Config, hal: Hal, clock: FakeClock, *, boot: bool = True) -> Rig:
    bus = BusManager(hal.bus, config, clock)
    await bus.start()
    await hal.gpio.open()
    sink = RecordingSink()
    outputs = Outputs(config, bus, clock, sink)
    power = {"source": "MAINS"}
    safety = Safety(config, outputs, hal.gpio, clock, power_source=lambda: power["source"])
    handler = OutputCommandHandler(config, outputs, safety, clock)
    rig = Rig(config, hal, clock, bus, outputs, safety, handler, sink, power)
    outputs.listeners.append(rig.changes.append)
    if boot:
        assert await drive(clock, outputs.all_off())
        await drive(clock, outputs.poll())  # baca float sekali
        rig.changes.clear()
    return rig


async def close_rig(rig: Rig) -> None:
    await rig.safety.stop()
    await rig.outputs.stop()
    await rig.bus.stop()
