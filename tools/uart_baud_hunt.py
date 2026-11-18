#!/usr/bin/env python3
"""Sweep a range of baud rates and rank them by how clean the received text is.

Built for the PixelJoy debug UART (TX pad, JieLi SDK log at nominally 1,000,000 baud), whose lines look like
`[Info]: [CLOCK]5 10:1:35`. The score is the share of printable ASCII, plus a bonus for each `[Info]:` that
arrives intact.

    python3 tools/uart_baud_hunt.py                       # 800k..1.4M in 10k steps, 2.5 s each
    python3 tools/uart_baud_hunt.py --lo 950000 --hi 1050000 --step 2000 --dwell 4
    python3 tools/uart_baud_hunt.py --port /dev/cu.usbserial-0001

Close any other terminal (picocom, screen) on the port first; two readers split the bytes between them.
Many USB-serial chips (CP210x, CH340) only hit certain rates and round the rest, so neighbouring rates can score
the same. In that case the adapter rounded them to one real rate.
"""

import argparse
import glob
import sys
import time

TOKEN = b"[Info]:"


def score(buf: bytes) -> float:
    """0..1 printable share, +0.05 per intact `[Info]:`; 0 for an empty buffer."""
    if not buf:
        return 0.0
    good = sum(32 <= c < 127 or c in (9, 10, 13) for c in buf)
    return good / len(buf) + 0.05 * buf.count(TOKEN)


def find_port() -> str:
    for pat in ("/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB*", "/dev/cu.usbmodem*",
                "/dev/ttyUSB*", "/dev/ttyACM*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    sys.exit("no USB-serial adapter found; pass --port")


def sample(serial_mod, port: str, baud: int, dwell: float) -> bytes:
    with serial_mod.Serial(port, baud, timeout=0.1) as s:
        s.reset_input_buffer()
        buf, end = b"", time.time() + dwell
        while time.time() < end:
            buf += s.read(4096)
    return buf


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port")
    ap.add_argument("--lo", type=int, default=800_000)
    ap.add_argument("--hi", type=int, default=1_400_000)
    ap.add_argument("--step", type=int, default=10_000)
    ap.add_argument("--dwell", type=float, default=2.5, help="seconds per rate (the log prints about once a second)")
    ap.add_argument("--top", type=int, default=5)
    a = ap.parse_args()

    import serial  # pyserial

    port = a.port or find_port()
    rates = range(a.lo, a.hi + 1, a.step)
    print(f"{port}: {len(rates)} rates, {a.lo}..{a.hi} step {a.step}, {a.dwell}s each "
          f"(~{len(rates) * a.dwell:.0f}s)", flush=True)
    results = []
    for baud in rates:
        try:
            buf = sample(serial, port, baud, a.dwell)
        except (serial.SerialException, OSError) as e:
            print(f"{baud:>8}: skipped ({e})", flush=True)
            continue
        sc = score(buf)
        results.append((sc, baud, buf))
        print(f"{baud:>8}: {len(buf):5d} B  score {sc:5.2f}  {TOKEN.decode()}x{buf.count(TOKEN):<2}  "
              f"{buf[:60]!r}", flush=True)

    hits = sorted((r for r in results if r[2]), reverse=True)
    if not hits:
        sys.exit("no bytes at any rate: check TX->RX, GND, and that nothing else has the port open")
    print(f"\nbest {min(a.top, len(hits))}:")
    for sc, baud, buf in hits[:a.top]:
        print(f"  {baud:>8}  score {sc:5.2f}  {len(buf)} B")
    best = hits[0]
    print(f"\nsample at {best[1]}:\n{best[2][:400].decode('latin-1')}")


def _selftest():
    clean = b"[Info]: [CLOCK]5 10:1:35\r\n[Info]: [FRAME]frame rate: 50 fps.\r\n"
    noisy = b"[Info]:\xff[CLOCK]free\xa0physic\x9b\xcb\x83size:67456\r\n"
    assert score(b"") == 0.0
    assert score(clean) > score(noisy) > score(bytes(range(128, 256)))
    assert abs(score(clean) - (1.0 + 0.1)) < 1e-9          # all printable, 2 intact tokens
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(_selftest())
    main()
