#!/usr/bin/env python3
"""Unpack a PixelJoy material ("lib") pack into files, one per record.

Format (docs/FIRMWARE.md §5f): "lib" + version(3) + 0x00, then chained 10-byte records
``id(1) tag(3) offset(u32 LE) len(u16 LE)`` each followed by its payload; next record = offset + len.

    python3 tools/material_extract.py re/firmware/P0AF_material_1.2.1.bin out/
    -> out/rem_0.gif ... (tag_index.ext; ext from the payload's magic bytes)

The remote's six car-sign presets are rem_0..rem_5 (left, no high beams, right, warning, baby, no horn).
"""

import struct
import sys
from pathlib import Path

MAGIC = {b"GIF8": "gif", b"\x89PNG": "png", b"BM": "bmp"}


def records(pack: bytes):
    """Yield (id, tag, offset, length) for every record."""
    if pack[:3] != b"lib":
        raise ValueError("not a material pack (no 'lib' header)")
    pos = 7
    while pos + 10 <= len(pack):
        rid, tag, off, ln = pack[pos], pack[pos + 1 : pos + 4], *struct.unpack_from("<IH", pack, pos + 4)
        if rid == 0xFF:  # erased-flash padding after the last record
            break
        if off == 0 and ln == 0 and pos > 7 and rid == 0:
            break
        yield rid, tag.decode("latin-1"), off, ln
        if off + ln > len(pack) or (off == 0 and ln == 0 and rid != 0x43):
            break
        pos = (off + ln) if off else pos + 10


def ext(data: bytes) -> str:
    return next((e for m, e in MAGIC.items() if data.startswith(m)), "bin")


def extract(pack: bytes, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    seen: dict[str, int] = {}
    written = []
    for rid, tag, off, ln in records(pack):
        if ln == 0:
            continue
        n = seen.get(tag, 0)
        seen[tag] = n + 1
        data = pack[off : off + ln]
        path = out / f"{tag}_{n}.{ext(data)}"
        path.write_bytes(data)
        written.append(path)
        print(f"id={rid:#04x} {tag}[{n}] off={off:#08x} len={ln:6d} -> {path.name}", flush=True)
    return written


def _selftest():
    gif = b"GIF89a" + b"\x00" * 4
    rec1 = bytes([0x37]) + b"rem" + struct.pack("<IH", 7 + 10, len(gif))
    pack = b"lib\x01\x02\x01\x00" + rec1 + gif
    assert list(records(pack)) == [(0x37, "rem", 17, len(gif))]
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(_selftest())
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    extract(Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]))
