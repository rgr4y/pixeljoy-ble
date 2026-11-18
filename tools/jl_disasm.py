#!/usr/bin/env python3
"""Disassemble a raw JieLi pi32v2 image (e.g. app.bin from the PixelJoy OTA) at a fixed load address.

Uses the instruction table and objdump of jvitkauskas/pi32v2-isa (https://github.com/jvitkauskas/pi32v2-isa,
MIT-0/CC0, pure Python 3.11+). Clone it somewhere and point PI32V2_ISA at the checkout.

    PI32V2_ISA=~/src/pi32v2-isa python3 tools/jl_disasm.py app.bin > app.dis
    PI32V2_ISA=... python3 tools/jl_disasm.py app.bin --base 0x1e00120 --mcpu r3 [--start A --end B]

app.bin from P0AF_fw_1.9.6 is cut out of the decrypted image (tools/jl_fw_decrypt.py) at file offset 0x120,
size 0x497c4. The default base 0x1E00120 is the code0 ORIGIN of JieLi's br23 linker script
(fw-AC63_BT_SDK cpu/br23/sdk_ld.c, USE_SDFILE_NEW); -mcpu=r3 is what that SDK's br23 Makefile builds with.
"""

import argparse
import os
import re
import sys
from types import SimpleNamespace

# `tbb [rN]` / `tbh [rN]` are followed inline by a byte / halfword table; case target = address after the
# tbb/tbh + 2 * entry (pi32v2-isa docs/COMPILER.md). objdump only skips tables it has `.GJTI*` symbols for.
TB = re.compile(r"^ *([0-9a-f]+):\s+(?:[0-9a-f]{2} ){2}\s+tb([bh]) \[r\d+\]", re.M)


BOUND = re.compile(r"if \(r\d+ > (\d+)\) goto")


def _tables(data, base, text):
    """{table offset: (kind, entries)} for every tb* in a listing. The entry count is the `if (rN > K)` range
    check a few lines up (K + 1); without one, the table runs until the lowest case target."""
    found = {}
    for m in TB.finditer(text):
        after = int(m.group(1), 16) + 2 - base
        step = 1 if m.group(2) == "b" else 2
        before = text[max(0, m.start() - 400):m.start()].splitlines()[-6:]
        bounds = [int(b.group(1)) for b in (BOUND.search(x) for x in before) if b]
        low, n = None, 0
        while after + (n + 1) * step <= len(data) and n < 256:
            v = int.from_bytes(data[after + n * step:after + (n + 1) * step], "little")
            t = after + 2 * v
            low = t if low is None else min(low, t)
            n += 1
            if after + n * step >= low:
                break
        found[after] = (step, bounds[-1] + 1 if bounds else n)
    return found


def _drop_nested(tables):
    """Forget tables whose tb* instruction sits inside another table (table bytes decoded as code)."""
    spans = [(o, o + s * n) for o, (s, n) in tables.items()]
    return {o: v for o, v in tables.items() if not any(a <= o - 2 < b for a, b in spans if a != o)}


def disassemble(data, base, mcpu="r3", start=None, end=None):
    sys.path.insert(0, os.path.expanduser(os.environ.get("PI32V2_ISA", "pi32v2-isa")))
    from pi32v2isa import elf, objdump, table

    class Dis(objdump.Disassembler):
        def table(self, s, lo, hi, step):     # print each case as its absolute target
            for a in range(lo, hi, step):
                v = int.from_bytes(self.raw(s, a, step), "little")
                self.out.append(f"{s.addr + a:8x}:    case {(a - lo) // step}: goto {self.tb_base[lo] + 2 * v:x}\n")
                self.size = step

    lo = (start - base) if start is not None else 0
    hi = (end - base) if end is not None else len(data)
    dec = objdump.decoder_for(table.load(), mcpu)
    tables = {}
    for _ in range(8):      # re-run until no new tables appear (a table can hide the next tb*)
        sec = elf.Section(".text", flags=elf.SHF_ALLOC | elf.SHF_EXECINSTR, data=bytearray(data[lo:hi]),
                          addr=base + lo, offset=lo, index=1)
        # `app` at the start, so branch annotations read `<app+0xN : absolute >`
        syms = [elf.Symbol(""), elf.Symbol("app", value=base, shndx=1, type=elf.STT_FUNC)]
        for off, (step, n) in sorted(tables.items()):
            syms.append(elf.Symbol((".GJTIS" if step == 1 else ".GJTIL") + f"{off:x}", value=base + off, shndx=1))
            end_off = off + ((n * step + 1) & ~1)
            syms.append(elf.Symbol(f".GJTIE{off:x}", value=base + end_off, shndx=1))
        f = SimpleNamespace(path="app.bin", data=bytes(data), sections=[None, sec], symbols=syms,
                            machine=elf.EM_PI32V2, obj=SimpleNamespace(e_type=2))
        d = Dis(f, dec, {})
        d.tb_base = {off - lo: base + off for off in tables}
        text = d.run([sec])
        merged = _drop_nested({**tables, **_tables(data, base, text)})
        if merged == tables:
            return text
        tables = merged
    return text


def _selftest():
    # tbb at 0x100 with a 3-case bound check: table bytes 1, 2, 3 -> targets 0x104, 0x106, 0x108
    text = "     fc:    00 fc 02 0a       \tif (r0 > 2) goto 20\n    100:    00 01             \ttbb [r0]\n"
    data = bytes(0x102) + bytes([1, 2, 3, 0])
    assert _tables(data, 0, text) == {0x102: (1, 3)}, _tables(data, 0, text)
    # without a bound the table stops at the lowest target (0x104 = 2 entries in)
    assert _tables(data, 0, text.splitlines()[1]) == {0x102: (1, 2)}
    # a tb* decoded from inside another table is dropped
    assert _drop_nested({0x102: (2, 4), 0x106: (1, 2)}) == {0x102: (2, 4)}
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(_selftest())
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0x1E00120)
    ap.add_argument("--mcpu", default="r3")
    ap.add_argument("--start", type=lambda s: int(s, 0))
    ap.add_argument("--end", type=lambda s: int(s, 0))
    a = ap.parse_args()
    sys.stdout.write(disassemble(open(a.image, "rb").read(), a.base, a.mcpu, a.start, a.end))
