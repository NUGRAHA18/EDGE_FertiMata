"""Decode/encode nilai register Modbus sesuai `RegisterSpec`."""

from __future__ import annotations

from ..config import RegisterSpec


def combine(words: list[int], spec: RegisterSpec) -> int:
    """Gabungkan 1–2 word mentah menjadi bilangan bulat sesuai tipe dan urutan word."""
    if spec.width == 1:
        raw = words[0] & 0xFFFF
        return raw - 0x10000 if spec.type == "s16" and raw & 0x8000 else raw
    first, second = words[0] & 0xFFFF, words[1] & 0xFFFF
    low, high = (first, second) if spec.word_order == "lo_hi" else (second, first)
    raw = (high << 16) | low
    return raw - 0x1_0000_0000 if spec.type == "s32" and raw & 0x8000_0000 else raw


def decode(words: list[int], spec: RegisterSpec) -> float:
    # Pembulatan menghapus ekor biner (0,1 × 3 = 0,30000000000000004) tanpa membuang presisi sensor.
    return round(combine(words, spec) * spec.scale, 6)


def encode(value: float, spec: RegisterSpec) -> list[int]:
    """Kebalikan `decode`, dipakai simulator dan test."""
    raw = round(value / spec.scale)
    if spec.width == 1:
        return [raw & 0xFFFF]
    raw &= 0xFFFF_FFFF
    low, high = raw & 0xFFFF, (raw >> 16) & 0xFFFF
    return [low, high] if spec.word_order == "lo_hi" else [high, low]


def read_blocks(specs: dict[str, RegisterSpec], max_span: int) -> list[tuple[int, int, int]]:
    """Kelompokkan register menjadi blok baca (fc, alamat awal, jumlah) supaya transaksi sedikit.

    Register dengan FC sama yang berdekatan dibaca sekali; blok tidak melebihi `max_span` word.
    """
    blocks: list[tuple[int, int, int]] = []
    by_fc: dict[int, list[tuple[int, int]]] = {}
    for spec in specs.values():
        by_fc.setdefault(spec.fc, []).append((spec.address, spec.address + spec.width))
    for fc in sorted(by_fc):
        start = end = None
        for lo, hi in sorted(by_fc[fc]):
            if start is not None and max(end, hi) - start <= max_span:
                end = max(end, hi)
                continue
            if start is not None:
                blocks.append((fc, start, end - start))
            start, end = lo, hi
        blocks.append((fc, start, end - start))
    return blocks
