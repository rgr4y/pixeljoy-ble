#!/usr/bin/env python3
"""Decrypt a PixelJoy (JieLi AC695x/AC637N) OTA firmware .bin.

JieLi "ENC" stream cipher (https://kagaimiq.github.io/jielie/misc/cipher.html):
CRC16-CCITT shift register, reset every 32 bytes to KEY ^ (offset >> 2).
The vendor kept a fixed key; 0xB107 was recovered from the P0AF 1.9.6 image
by matching keystream over flash padding.

    python tools/jl_fw_decrypt.py re/firmware/P0AF_fw_1.9.6.bin out.bin
"""

import struct
import sys

KEY = 0xB107


def jl_enc(data: bytes, key: int = KEY) -> bytes:
    out = bytearray(data)
    for blk in range(0, len(out), 32):
        k = key ^ ((blk >> 2) & 0xFFFF)
        for i in range(blk, min(blk + 32, len(out))):
            out[i] ^= k & 0xFF
            k = ((k << 1) & 0xFFFF) ^ (0x1021 if k & 0x8000 else 0)
    return bytes(out)


def entries(dec: bytes):
    """File table at the start: u16 hdr_crc, u16 data_crc, u32 offset, u32 size, u8 attr, u8, u16 index, name[16]."""
    for i in range(0, 0x120, 32):
        e = dec[i:i + 32]
        if e[:2] != b"\xff\xff":
            _, _, off, size, attr, _, idx = struct.unpack("<HHIIBBH", e[:16])
            yield e[16:].split(b"\0")[0].decode(), off, size, attr, idx


if __name__ == "__main__":
    src, dst = sys.argv[1:3]
    dec = jl_enc(open(src, "rb").read())
    assert dec[16:29] == b"app_area_head", "key does not fit this image"
    open(dst, "wb").write(dec)
    for name, off, size, attr, idx in entries(dec):
        print(f"{name:<14} off={off:#09x} size={size:#08x} attr={attr:#04x} idx={idx}")
