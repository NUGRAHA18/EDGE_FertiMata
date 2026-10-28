"""Hardware abstraction layer: antarmuka, galat, dan pembuat HAL sesuai `agent.hal`."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from .base import (
    BusUnavailable,
    Camera,
    CameraError,
    Clock,
    Frame,
    GpioEvent,
    GpioLines,
    HalError,
    ModbusBus,
    ModbusDeviceError,
    ModbusTimeout,
    SocSensor,
)
from .clock import FakeClock, SystemClock, parse_iso, to_iso_z


@dataclass
class Hal:
    bus: ModbusBus
    gpio: GpioLines
    camera: Camera
    clock: Clock
    soc: SocSensor
    world: object | None = None  # FakeWorld bila hal: fake

    async def open(self) -> None:
        """Buka GPIO dan kamera. Bus dibuka oleh bus manager, pemilik tunggalnya (§4.6)."""
        await self.gpio.open()
        await self.camera.open()

    async def close(self) -> None:
        # Tutup dalam urutan terbalik; galat satu bagian tidak menghalangi yang lain.
        for part in (self.camera, self.gpio, self.bus):
            try:
                await part.close()
            except Exception:
                pass


def create_hal(config: Config, clock: Clock | None = None) -> Hal:
    clock = clock or SystemClock()
    if config.agent.hal == "fake":
        from .fake import FakeCamera, FakeGpio, FakeModbusBus, FakeSoc, FakeWorld

        world = FakeWorld(config)
        return Hal(
            bus=FakeModbusBus(world),
            gpio=FakeGpio(world, clock),
            camera=FakeCamera(world),
            clock=clock,
            soc=FakeSoc(world),
            world=world,
        )
    from .real_modbus import PymodbusBus
    from .real_soc import SysfsSoc
    from .real_stubs import LockedGpio, UnavailableCamera

    bus_cfg = config.bus
    # GPIO (F7) dan kamera (F8) asli belum ada; penggantinya mengunci trolley.
    return Hal(
        bus=PymodbusBus(
            bus_cfg.port, bus_cfg.baudrate, bus_cfg.parity, bus_cfg.stopbits, bus_cfg.timeout_s
        ),
        gpio=LockedGpio(),
        camera=UnavailableCamera(),
        clock=clock,
        soc=SysfsSoc(config.power.soc_thermal_glob),
    )


__all__ = [
    "BusUnavailable",
    "Camera",
    "CameraError",
    "Clock",
    "FakeClock",
    "Frame",
    "GpioEvent",
    "GpioLines",
    "Hal",
    "HalError",
    "ModbusBus",
    "ModbusDeviceError",
    "ModbusTimeout",
    "SocSensor",
    "SystemClock",
    "create_hal",
    "parse_iso",
    "to_iso_z",
]
