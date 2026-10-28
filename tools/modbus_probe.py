"""Baca/tulis register satu perangkat Modbus RTU dari command line (TASKS F3.1).

Dipakai untuk cek lapangan P-H3 … P-H8 sebelum hasilnya ditulis ke `fertimata.yaml`.
Panduan lengkap: docs/03-persiapan.md bagian "Cara cek register dengan modbus_probe".

PENTING: hentikan agent dulu (`sudo systemctl stop fertimata-edge`). Hanya satu program yang
boleh memakai port RS485 dalam satu waktu (Arsitektur §4.6).

Contoh:
    # PZEM-016 alamat 30: 10 input register mulai 0x0000
    python tools/modbus_probe.py --port /dev/rs485 --unit 30 --fc 4 --address 0 --count 10
    # Cari alamat yang menjawab (P-H3)
    python tools/modbus_probe.py --port /dev/rs485 --scan 1-30 --fc 3 --address 0
    # Relay 20 kanal R1 ON lewat coil (uji profil A, P-H4) — menyalakan beban sungguhan!
    python tools/modbus_probe.py --port /dev/rs485 --unit 20 --fc 5 --address 1 --value on --yes
    # Latihan tanpa hardware (simulator config dev)
    python tools/modbus_probe.py --fake --unit 30 --fc 4 --address 0 --count 10
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from fertimata_edge.hal import BusUnavailable, ModbusBus, ModbusDeviceError, ModbusTimeout

FC_NAMES = {
    1: "read_coils",
    2: "read_discrete_inputs",
    3: "read_holding_registers",
    4: "read_input_registers",
    5: "write_coil",
    6: "write_register",
}
EXCEPTION_NAMES = {
    1: "ILLEGAL FUNCTION (fungsi tidak didukung)",
    2: "ILLEGAL DATA ADDRESS (register tidak ada)",
    3: "ILLEGAL DATA VALUE",
    4: "SLAVE DEVICE FAILURE",
}

Out = Callable[[str], None]


def _int(text: str) -> int:
    return int(text, 0)  # menerima 30, 0x1E, 0o36


def _units(text: str) -> list[int]:
    lo, _, hi = text.partition("-")
    first, last = _int(lo), _int(hi or lo)
    if not 1 <= first <= last <= 247:
        raise argparse.ArgumentTypeError("rentang alamat harus di 1–247, mis. 1-30")
    return list(range(first, last + 1))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="modbus_probe",
        description="Baca/tulis register Modbus RTU untuk verifikasi perangkat (P-H3..P-H8).",
        epilog="Hentikan agent dulu: hanya satu program boleh memakai port RS485.",
    )
    conn = parser.add_argument_group("koneksi")
    conn.add_argument("--port", default="/dev/rs485", help="port serial (Windows: COM5)")
    conn.add_argument("--baud", type=int, default=9600)
    conn.add_argument("--parity", choices=["N", "E", "O"], default="N")
    conn.add_argument("--stopbits", type=int, choices=[1, 2], default=1)
    conn.add_argument("--timeout", type=float, default=1.0, help="detik menunggu jawaban")
    conn.add_argument("--fake", action="store_true", help="pakai simulator config dev")

    req = parser.add_argument_group("permintaan")
    target = req.add_mutually_exclusive_group(required=True)
    target.add_argument("--unit", type=_int, help="alamat perangkat 1–247")
    target.add_argument("--scan", type=_units, help="coba banyak alamat, mis. 1-30")
    req.add_argument("--fc", type=int, choices=sorted(FC_NAMES), required=True)
    req.add_argument("--address", type=_int, default=0, help="register/coil awal (boleh 0x..)")
    req.add_argument("--count", type=int, default=1, help="jumlah register/coil yang dibaca")
    req.add_argument("--value", help="nilai tulis: FC5 on/off, FC6 angka (boleh 0x0100)")
    req.add_argument(
        "--yes", action="store_true", help="wajib untuk tulis: beban sungguhan bisa menyala"
    )
    req.add_argument("--repeat", type=int, default=1, help="ulangi baca N kali")
    req.add_argument("--interval", type=float, default=1.0, help="jeda antar ulangan (detik)")
    args = parser.parse_args(argv)

    if args.fc in (5, 6):
        if args.scan:
            parser.error("--scan hanya untuk baca")
        if args.value is None:
            parser.error(f"FC{args.fc} butuh --value")
        if not args.yes:
            parser.error("tulis bisa menyalakan pompa/motor sungguhan; tambahkan --yes bila yakin")
        args.write_value = _parse_value(parser, args.fc, args.value)
    if not 1 <= args.count <= 125:
        parser.error("--count harus 1–125")
    return args


def _parse_value(parser: argparse.ArgumentParser, fc: int, text: str) -> int | bool:
    if fc == 5:
        lowered = text.lower()
        if lowered in ("on", "1", "true"):
            return True
        if lowered in ("off", "0", "false"):
            return False
        parser.error("FC5 --value harus on/off")
    try:
        value = _int(text)
    except ValueError:
        parser.error("FC6 --value harus angka, mis. 256 atau 0x0100")
    if not 0 <= value <= 0xFFFF:
        parser.error("FC6 --value harus 0–65535")
    return value


# --------------------------------------------------------------------------------------------
# Format keluaran
# --------------------------------------------------------------------------------------------


def format_registers(start: int, words: list[int]) -> list[str]:
    """Tampilkan tiap register dalam beberapa tafsiran supaya skala/tipe mudah ditebak."""
    lines = ["  register         u16     s16     hex"]
    for i, word in enumerate(words):
        signed = word - 0x10000 if word & 0x8000 else word
        lines.append(f"  0x{start + i:04X} ({start + i:>5})  {word:>6}  {signed:>6}  0x{word:04X}")
    if len(words) >= 2:
        lines.append("  pasangan 32 bit    lo_hi (word rendah dulu)   hi_lo (word tinggi dulu)")
        for i in range(len(words) - 1):
            a, b = words[i], words[i + 1]
            pair = f"0x{start + i:04X}+0x{start + i + 1:04X}"
            lines.append(f"  {pair}   {(b << 16) | a:>24}   {(a << 16) | b:>24}")
    return lines


def format_bits(start: int, bits: list[bool]) -> list[str]:
    return [
        f"  0x{start + i:04X} ({start + i:>5})  {'ON / tertutup' if bit else 'OFF / terbuka'}"
        for i, bit in enumerate(bits)
    ]


def describe_error(err: Exception) -> str:
    if isinstance(err, ModbusDeviceError):
        return f"perangkat menjawab exception {err.code}: {EXCEPTION_NAMES.get(err.code, '?')}"
    if isinstance(err, ModbusTimeout):
        return "tidak ada jawaban (alamat salah, A/B tertukar, baud beda, atau kabel/daya)"
    return str(err)


# --------------------------------------------------------------------------------------------
# Eksekusi
# --------------------------------------------------------------------------------------------


async def read_once(bus: ModbusBus, unit: int, fc: int, address: int, count: int, timeout: float):
    return await getattr(bus, FC_NAMES[fc])(unit, address, count, timeout_s=timeout)


async def run(bus: ModbusBus, args: argparse.Namespace, out: Out, sleep=asyncio.sleep) -> int:
    """Jalankan permintaan. Kode keluar: 0 sukses, 1 ada yang gagal."""
    if args.scan:
        return await _scan(bus, args, out)
    if args.fc in (5, 6):
        return await _write(bus, args, out)
    failed = False
    for n in range(args.repeat):
        if n:
            await sleep(args.interval)
        try:
            data = await read_once(bus, args.unit, args.fc, args.address, args.count, args.timeout)
        except (ModbusTimeout, ModbusDeviceError) as err:
            out(f"alamat {args.unit} FC{args.fc} @0x{args.address:04X}: {describe_error(err)}")
            failed = True
            continue
        out(f"alamat {args.unit} FC{args.fc} @0x{args.address:04X} x{args.count}:")
        for line in (
            format_bits(args.address, data)
            if args.fc in (1, 2)
            else format_registers(args.address, data)
        ):
            out(line)
    return 1 if failed else 0


async def _scan(bus: ModbusBus, args: argparse.Namespace, out: Out) -> int:
    found = []
    out(
        f"memindai alamat {args.scan[0]}–{args.scan[-1]} dengan FC{args.fc} @0x{args.address:04X} …"
    )
    for unit in args.scan:
        try:
            data = await read_once(bus, unit, args.fc, args.address, 1, args.timeout)
        except ModbusTimeout:
            continue
        except ModbusDeviceError as err:
            # Exception berarti perangkatnya ADA, hanya register/fungsinya tidak cocok.
            out(f"  alamat {unit:>3}: ada, {describe_error(err)}")
            found.append(unit)
            continue
        out(f"  alamat {unit:>3}: menjawab {data}")
        found.append(unit)
    out(f"ditemukan {len(found)} perangkat: {found}" if found else "tidak ada yang menjawab")
    return 0 if found else 1


async def _write(bus: ModbusBus, args: argparse.Namespace, out: Out) -> int:
    value = args.write_value
    shown = ("ON" if value else "OFF") if args.fc == 5 else f"{value} (0x{value:04X})"
    try:
        if args.fc == 5:
            await bus.write_coil(args.unit, args.address, value, timeout_s=args.timeout)
        else:
            await bus.write_register(args.unit, args.address, value, timeout_s=args.timeout)
    except (ModbusTimeout, ModbusDeviceError) as err:
        out(f"tulis FC{args.fc} @0x{args.address:04X} = {shown}: {describe_error(err)}")
        return 1
    out(f"tulis FC{args.fc} @0x{args.address:04X} = {shown}: diterima")
    # Baca balik: profil relay A memakai coil (FC1), profil B holding register (FC3).
    back_fc = 1 if args.fc == 5 else 3
    try:
        data = await read_once(bus, args.unit, back_fc, args.address, 1, args.timeout)
        out(f"baca balik FC{back_fc}: {data}")
    except (ModbusTimeout, ModbusDeviceError) as err:
        out(f"baca balik FC{back_fc} gagal: {describe_error(err)}")
    return 0


def make_bus(args: argparse.Namespace) -> ModbusBus:
    if args.fake:
        from fertimata_edge.config import load_config
        from fertimata_edge.hal import create_hal

        dev = Path(__file__).resolve().parents[1] / "config" / "fertimata.dev.yaml"
        return create_hal(load_config(dev)).bus
    from fertimata_edge.hal.real_modbus import PymodbusBus

    return PymodbusBus(args.port, args.baud, args.parity, args.stopbits, args.timeout)


async def _main(args: argparse.Namespace) -> int:
    bus = make_bus(args)
    try:
        await bus.open()
    except BusUnavailable as err:
        print(f"port tidak bisa dibuka: {err}", file=sys.stderr)
        return 2
    try:
        return await run(bus, args, print)
    finally:
        await bus.close()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
