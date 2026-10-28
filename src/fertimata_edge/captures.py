"""Penyimpanan foto patroli + metadata (keputusan D1, Protokol §4, F8.2) dan ruang disk (F8.5).

Tata letak (D1): `<root>/YYYY-MM-DD/YYYYMMDD-HHMM_S{nn}_{timur|barat}.png`, waktu lokal
(`agent.utc_offset_h`, WIB). Folder memakai tanggal **mulai patroli** supaya satu patroli tidak
terbelah tengah malam. Nama berkas tanpa ekstensi = `captureId` di hasil AI (Kontrak §4.5).

Setiap foto punya sidecar `.json` (stasiun, arah, eksposur, suhu, kelembapan, ketajaman). Keduanya
ditulis ke berkas sementara lalu di-rename, dan sidecar ditulis **sesudah** foto: layanan AI (D6)
cukup menunggu `.json` muncul untuk tahu fotonya sudah utuh.

Data riset tidak pernah dihapus otomatis. Ruang disk hanya dilaporkan; di bawah batas kritis foto
tidak disimpan lagi supaya sistem Jetson tetap punya ruang (journald, paket).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .hal import Frame

GB = 1024**3


@dataclass(frozen=True)
class SavedCapture:
    capture_id: str
    image: Path
    sidecar: Path


def disk_free_bytes(path: Path) -> int:
    """Ruang kosong di sistem berkas tempat `path` (atau induk terdekat yang sudah ada) berada."""
    probe = path.resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


class CaptureStore:
    def __init__(
        self,
        config: Config,
        *,
        disk_free: Callable[[Path], int] = disk_free_bytes,
        offload: Callable[..., Awaitable[Any]] = asyncio.to_thread,
    ) -> None:
        self.cfg = config.camera
        self.root = Path(self.cfg.root)
        self.tz = timezone(timedelta(hours=config.agent.utc_offset_h))
        self._disk_free = disk_free
        # Test dengan jam palsu menulis langsung: thread sungguhan membuat urutan bergantung disk.
        self._offload = offload

    def local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.tz)

    def day_dir(self, patrol_start: datetime) -> Path:
        return self.root / f"{self.local(patrol_start):%Y-%m-%d}"

    # --- ruang disk (F8.5) --------------------------------------------------------------------

    def free_gb(self) -> float | None:
        try:
            return self._disk_free(self.root) / GB
        except OSError:
            return None

    def disk_level(self, free_gb: float | None) -> str | None:
        """None (cukup), "low" (di bawah ambang peringatan), atau "full" (foto tidak disimpan)."""
        if free_gb is None:
            return None
        if free_gb < self.cfg.disk_stop_free_gb:
            return "full"
        if free_gb < self.cfg.disk_warn_free_gb:
            return "low"
        return None

    # --- tulis berkas -------------------------------------------------------------------------

    async def save(
        self,
        frame: Frame,
        *,
        patrol_start: datetime,
        captured_at: datetime,
        station: int,
        direction: str,
        metadata: dict[str, Any],
    ) -> SavedCapture:
        base = f"{self.local(captured_at):%Y%m%d-%H%M}_S{station:02d}_{direction}"
        folder = self.day_dir(patrol_start)
        # Tulis berkas bisa memakan puluhan ms untuk PNG besar; jangan tahan loop (STOP trolley).
        return await self._offload(self._write, folder, base, frame, metadata)

    def _write(
        self, folder: Path, base: str, frame: Frame, metadata: dict[str, Any]
    ) -> SavedCapture:
        folder.mkdir(parents=True, exist_ok=True)
        capture_id = base
        suffix = 2
        # Patroli ulang di menit yang sama (mis. pemicu manual) tidak boleh menimpa data riset.
        while (folder / f"{capture_id}.{frame.encoding}").exists() or (
            folder / f"{capture_id}.json"
        ).exists():
            capture_id = f"{base}-{suffix}"
            suffix += 1
        image = folder / f"{capture_id}.{frame.encoding}"
        sidecar = folder / f"{capture_id}.json"
        _atomic_write(image, frame.data)
        body = {"captureId": capture_id, "file": image.name, **metadata}
        _atomic_write(sidecar, _json_bytes(body))
        return SavedCapture(capture_id, image, sidecar)

    async def write_summary(
        self, patrol_start: datetime, patrol_id: str, summary: dict[str, Any]
    ) -> Path:
        folder = self.day_dir(patrol_start)
        path = folder / f"patroli_{patrol_id}.json"

        def write() -> Path:
            folder.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, _json_bytes(summary))
            return path

        return await self._offload(write)


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
