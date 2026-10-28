"""Antarmuka HAL: satu-satunya pintu logika ke hardware.

Logika agent tidak pernah mengimpor pymodbus, gpiod, atau cv2 (CLAUDE.md). Implementasi asli dan
palsu sama-sama memenuhi kelas di bawah, sehingga hampir semua logika bisa dites di laptop.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class HalError(Exception):
    """Galat dari lapisan hardware."""


class BusUnavailable(HalError):
    """Port serial tidak ada atau tidak bisa dibuka (mis. `/dev/rs485` hilang)."""


class ModbusTimeout(HalError):
    """Perangkat tidak menjawab dalam batas waktu (atau jawabannya rusak/CRC salah)."""


class ModbusDeviceError(HalError):
    """Perangkat menjawab dengan exception Modbus (fungsi/register tidak didukung)."""

    def __init__(self, unit: int, code: int, message: str = "") -> None:
        super().__init__(message or f"perangkat {unit} membalas exception Modbus {code}")
        self.unit = unit
        self.code = code


class CameraError(HalError):
    """Kamera tidak terbaca atau gagal mengambil gambar."""


class ModbusBus(ABC):
    """Akses mentah fungsi Modbus RTU. Penafsiran register adalah urusan profil (F3).

    Hanya bus manager yang boleh memegang objek ini (Arsitektur §4.6 aturan 1).
    `timeout_s=None` berarti memakai `bus.timeout_s` dari config.
    """

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def read_coils(
        self, unit: int, address: int, count: int, *, timeout_s: float | None = None
    ) -> list[bool]: ...

    @abstractmethod
    async def read_discrete_inputs(
        self, unit: int, address: int, count: int, *, timeout_s: float | None = None
    ) -> list[bool]: ...

    @abstractmethod
    async def read_holding_registers(
        self, unit: int, address: int, count: int, *, timeout_s: float | None = None
    ) -> list[int]: ...

    @abstractmethod
    async def read_input_registers(
        self, unit: int, address: int, count: int, *, timeout_s: float | None = None
    ) -> list[int]: ...

    @abstractmethod
    async def write_coil(
        self, unit: int, address: int, value: bool, *, timeout_s: float | None = None
    ) -> None: ...

    @abstractmethod
    async def write_register(
        self, unit: int, address: int, value: int, *, timeout_s: float | None = None
    ) -> None: ...


@dataclass(frozen=True)
class GpioEvent:
    line: str  # nama logis dari config: "reed", "home", "end"
    active: bool  # sudah memperhitungkan polaritas (active: low/high)
    monotonic: float


class GpioLines(ABC):
    """Input GPIO trolley. Nilai `active` sudah dibalik sesuai polaritas di config (§6.1)."""

    # False untuk pengganti sementara (belum ada GPIO asli): nilainya bukan bacaan sungguhan.
    available = True

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def is_active(self, line: str) -> bool: ...

    @abstractmethod
    def subscribe(self) -> asyncio.Queue[GpioEvent]:
        """Antrean baru yang menerima setiap perubahan (setelah debounce)."""

    @abstractmethod
    def unsubscribe(self, queue: asyncio.Queue[GpioEvent]) -> None: ...


@dataclass(frozen=True)
class Frame:
    data: bytes  # berkas terkode, sesuai `encoding`
    encoding: str  # "png" / "jpg"
    width: int
    height: int
    # Ketajaman = variance of Laplacian citra abu-abu (gerbang blur §13). Dihitung HAL karena
    # butuh piksel mentah (cv2); None bila HAL tidak bisa menghitungnya.
    sharpness: float | None = None


class Camera(ABC):
    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def capture(self) -> Frame:
        """Ambil satu gambar dengan setelan terkunci. Gagal → `CameraError`."""


class SocSensor(ABC):
    """Suhu SoC Jetson (Kontrak §3, `jetson.soc_temperature`)."""

    @abstractmethod
    async def read_temperature_c(self) -> float:
        """Suhu tertinggi dari zona termal SoC, °C. Gagal → `HalError`."""


class Clock(ABC):
    """Sumber waktu. Ditukar dengan `FakeClock` di test supaya timer bisa dimajukan manual."""

    @abstractmethod
    def now(self) -> datetime:
        """Waktu dinding UTC (timezone-aware)."""

    @abstractmethod
    def monotonic(self) -> float: ...

    @abstractmethod
    async def sleep(self, seconds: float) -> None: ...
