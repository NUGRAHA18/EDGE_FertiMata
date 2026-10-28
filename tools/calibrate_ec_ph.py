"""Kalibrasi EC/pH dan simpan hasilnya ke `fertimata.yaml` (TASKS F6.6, Arsitektur §8.3).

Satu perintah untuk satu larutan: celupkan probe, tunggu stabil, lalu jalankan dengan `--point`.
Alat ini membaca tegangan median dengan urutan R5 yang sama seperti agent (§6.3), memeriksa
hasilnya, dan dengan `--write` mengganti nilai itu saja di berkas config (komentar tetap utuh,
salinan lama disimpan sebagai `.bak`).

PENTING: hentikan agent dulu (`sudo systemctl stop fertimata-edge`). Hanya satu program yang boleh
memakai port RS485 (Arsitektur §4.6). Saat mulai, alat ini mematikan semua relay (S1).

Contoh:
    # EC: larutan 1413 µS/cm, lalu 12,88 mS/cm (bilas probe dengan aquades di antaranya)
    python tools/calibrate_ec_ph.py --config /etc/fertimata/fertimata.yaml --point ec1413
    python tools/calibrate_ec_ph.py --config /etc/fertimata/fertimata.yaml --point ec1413 --write
    # pH: buffer 6,86 dulu, lalu 4,01 dan/atau 9,18
    python tools/calibrate_ec_ph.py --config /etc/fertimata/fertimata.yaml --point ph686 --write
    # Sensor suhu larutan belum terpasang: isi suhu dari termometer
    python tools/calibrate_ec_ph.py --config ... --point ec1413 --temperature 26.5 --write
    # Latihan tanpa hardware (simulator config dev)
    python tools/calibrate_ec_ph.py --config config/fertimata.dev.yaml --point ph686
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from fertimata_edge.analog import AnalogReader
from fertimata_edge.bus import BusManager
from fertimata_edge.config import Config, ConfigError, PhCalibration, load_config, parse_config
from fertimata_edge.hal import Clock, SystemClock, create_hal
from fertimata_edge.outputs import Outputs
from fertimata_edge.safety import Safety

# titik → (kanal, kunci di config, nilai larutan). Nilai larutan dari kit DFR0300/buffer (§8.3).
POINTS = {
    "ec1413": ("ec", "v_1413", 1.413),
    "ec12880": ("ec", "v_12880", 12.88),
    "ph401": ("ph", "v_401", 4.01),
    "ph686": ("ph", "v_686", 6.86),
    "ph918": ("ph", "v_918", 9.18),
}

Out = Callable[[str], None]


@dataclass
class Evaluation:
    ok: bool
    updates: dict[str, float] = field(default_factory=dict)  # kunci YAML → nilai baru
    lines: list[str] = field(default_factory=list)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="calibrate_ec_ph",
        description="Ukur satu larutan kalibrasi EC/pH dan simpan tegangannya ke config.",
        epilog="Hentikan agent dulu: hanya satu program boleh memakai port RS485.",
    )
    parser.add_argument("--config", required=True, help="berkas fertimata.yaml")
    parser.add_argument("--point", required=True, choices=sorted(POINTS))
    parser.add_argument(
        "--temperature", type=float, help="suhu larutan °C bila sensor suhu tidak terbaca"
    )
    parser.add_argument("--write", action="store_true", help="simpan hasil ke berkas config")
    return parser.parse_args(argv)


# --- pengukuran ---------------------------------------------------------------------------------


async def measure(config: Config, channel: str, clock: Clock) -> tuple[float | None, float | None]:
    """Tegangan median kanal dan suhu larutan, dengan bus milik alat ini sendiri."""
    hal = create_hal(config, clock)
    bus = BusManager(hal.bus, config, clock)
    await bus.start()
    try:
        outputs = Outputs(config, bus, clock)
        await outputs.all_off()  # S1: alat ini sekarang satu-satunya pemilik bus
        # Hanya R5 yang ditulis, dan R5 tidak termasuk daftar blokir daya.
        safety = Safety(config, outputs, hal.gpio, clock, power_source=lambda: None)
        reader = AnalogReader(config, bus, outputs, safety, clock)
        return await reader.read_raw(channel)
    finally:
        await bus.stop()
        await hal.close()


# --- penilaian ----------------------------------------------------------------------------------


def evaluate(config: Config, point: str, mv: float | None, temperature: float | None) -> Evaluation:
    channel, key, solution = POINTS[point]
    analog = config.analog
    if mv is None:
        return Evaluation(False, lines=["Tegangan tidak terbaca: periksa papan ADC dan kabelnya."])
    lines = [f"Tegangan median {mv:.1f} mV, suhu larutan {_temp(temperature)}"]
    if channel == "ec":
        if temperature is None:
            lines.append("Suhu larutan wajib untuk EC: isi --temperature dari termometer.")
            return Evaluation(False, lines=lines)
        # Nilai disimpan seolah diukur pada 25 °C, sama dengan kompensasi saat membaca (§6.3).
        v25 = mv / (1 + analog.temp_coef * (temperature - 25))
        k = analog.ec.mv_per_ms_cm * solution / v25
        low, high = analog.ec.cal_k_range
        lines.append(f"Tegangan setara 25 °C {v25:.1f} mV → K = {k:.3f} (wajar {low}–{high})")
        if not low <= k <= high:
            lines.append(
                "K di luar batas: pastikan larutan benar, probe bersih, dan terendam penuh."
            )
            return Evaluation(False, lines=lines)
        return Evaluation(True, {key: round(v25, 1)}, lines)

    updates: dict[str, float] = {key: round(mv, 1)}
    if temperature is not None:
        updates["cal_temperature_c"] = round(temperature, 1)
    current = analog.ph.cal.model_dump()
    current[key] = updates[key]
    try:
        cal = PhCalibration.model_validate(current)
    except ValidationError as err:
        lines.append(f"Titik ini tidak cocok dengan titik lain: {err.errors()[0]['msg']}")
        lines.append("Periksa buffer (mungkin tertukar) lalu ukur ulang.")
        return Evaluation(False, lines=lines)
    points = cal.points()
    if len(points) >= 2:
        (v1, p1), (v2, p2) = points[0], points[-1]
        lines.append(f"Kemiringan {(v2 - v1) / (p2 - p1):.1f} mV per pH dari {len(points)} titik")
    else:
        lines.append("Baru satu titik pH; ukur buffer kedua supaya pH bisa dihitung.")
    return Evaluation(True, updates, lines)


def _temp(value: float | None) -> str:
    return "tidak terbaca" if value is None else f"{value:.1f} °C"


# --- tulis config -------------------------------------------------------------------------------


def apply_updates(text: str, updates: dict[str, float]) -> str:
    """Ganti nilai kunci (unik di berkas) tanpa menyentuh bagian lain, termasuk komentar."""
    for key, value in updates.items():
        pattern = re.compile(rf"(\b{re.escape(key)}:[ \t]*)([^,}}\s#]+)")
        matches = pattern.findall(text)
        if len(matches) != 1:
            raise ConfigError(f"kunci '{key}' harus muncul tepat sekali di config ({len(matches)})")
        text = pattern.sub(lambda m, v=value: f"{m.group(1)}{v:g}", text)
    return text


def write_config(path: Path, updates: dict[str, float]) -> Path:
    original = path.read_text(encoding="utf-8")
    updated = apply_updates(original, updates)
    parse_config(yaml.safe_load(updated), str(path))  # tolak sebelum berkas diubah
    backup = path.with_name(path.name + ".bak")
    shutil.copyfile(path, backup)
    path.write_text(updated, encoding="utf-8")
    return backup


# --- program ------------------------------------------------------------------------------------


async def run(args: argparse.Namespace, out: Out, clock: Clock | None = None) -> int:
    path = Path(args.config)
    try:
        config = load_config(path)
    except ConfigError as err:
        out(f"Config ditolak:\n{err}")
        return 2
    channel, key, solution = POINTS[args.point]
    out(f"Titik {args.point}: kanal {channel}, larutan {solution:g}, disimpan ke {key}")
    try:
        mv, temperature = await measure(config, channel, clock or SystemClock())
    except ValueError as err:
        out(f"Tidak bisa mengukur: {err}")
        return 1
    if args.temperature is not None:
        temperature = args.temperature
    result = evaluate(config, args.point, mv, temperature)
    for line in result.lines:
        out(line)
    if not result.ok:
        return 1
    changes = ", ".join(f"{k}: {v:g}" for k, v in result.updates.items())
    if not args.write:
        out(f"Belum disimpan. Jalankan lagi dengan --write untuk menyimpan {changes}")
        return 0
    try:
        backup = write_config(path, result.updates)
    except ConfigError as err:
        out(f"Config tidak diubah: {err}")
        return 2
    out(f"Disimpan ke {path}: {changes} (salinan lama: {backup.name})")
    out("Nyalakan lagi agent supaya nilai baru dipakai.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv), print))


if __name__ == "__main__":
    sys.exit(main())
