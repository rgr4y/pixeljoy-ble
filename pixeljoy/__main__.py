"""CLI: python -m pixeljoy {scan,gatt,info,time,power,brightness,text,image,gif,raw}"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import traceback
from dataclasses import asdict

from . import protocol as P

# Text limits by READ_PROPERTY's support600Txt flag (char indices are u8 vs u16).
TEXT_MAX = {False: 100, True: 600}

EXIT_ERROR, EXIT_INTERRUPTED = 1, 130


# -- argument types -----------------------------------------------------------

def hex_color(s: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", s):
        raise argparse.ArgumentTypeError(f"{s!r} is not a colour; use rrggbb or #rrggbb")
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def int_range(lo: int, hi: int):
    def parse(s: str) -> int:
        try:
            v = int(s, 0)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{s!r} is not an integer") from None
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError(f"{v} is out of range {lo}-{hi}")
        return v
    parse.__name__ = f"integer {lo}-{hi}"
    return parse


def hex_byte(s: str) -> int:
    try:
        v = int(s, 16)
    except ValueError:
        v = -1
    if not 0 <= v <= 0xFF:
        raise argparse.ArgumentTypeError(f"{s!r} is not a hex byte 00-FF")
    return v


def hex_bytes(s: str) -> bytes:
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s!r} is not hex (e.g. '01 ff' or '01ff')") from None


# -- commands -----------------------------------------------------------------

def _say(args, msg: str) -> None:
    if not args.quiet:
        print(msg)


async def _pick(args):
    from .client import find

    f = await find(args.timeout, args.address, args.name)
    _say(args, f"using {f.name} {f.device.address} -> {f.model.name} {f.model.width}x{f.model.height}")
    return f


async def main_async(args) -> None:
    from .client import PixelJoy, PixelJoyError, scan

    if args.cmd == "scan":
        for f in await scan(args.timeout, show_all=args.all):
            m = f"{f.model.name} {f.model.width}x{f.model.height}" if f.model else "-"
            mfr = {k: v.hex() for k, v in f.manufacturer_data.items()}
            print(f"{f.device.address}  rssi={f.rssi}  name={f.name!r}  model={m}\n    uuids={f.service_uuids}\n    mfr={mfr}")
        return

    f = await _pick(args)
    if args.cmd == "gatt":
        from .client import dump_gatt

        await dump_gatt(f.device)
        return
    async with PixelJoy(f.device, f.model, auto_time_sync=not args.no_time_sync, tz_name=args.tz) as pj:
        if args.cmd == "time":
            if args.no_time_sync:  # otherwise connect() already did it
                await pj.sync_time(args.tz)
            _say(args, f"clock synced ({args.tz or P._local_tz_name()})")
        elif args.cmd == "info":
            info = {
                "name": f.name, "address": f.device.address,
                "model": f.model.name, "size": f"{f.model.width}x{f.model.height}",
                "firmware": await pj.firmware_version(),
                "properties": {k: (v.hex() if isinstance(v, bytes) else v) for k, v in asdict(pj.properties).items()},
            }
            if args.json:
                print(json.dumps(info, indent=2))
            else:
                for k, v in info.items():
                    if k != "properties":
                        print(f"{k:<10} {v}")
                print("properties:")
                for k, v in info["properties"].items():
                    print(f"  {k:<28} {v}")
        elif args.cmd == "power":
            await pj.power(args.state == "on")
        elif args.cmd == "brightness":
            await pj.brightness(args.level)
        elif args.cmd == "text":
            from .render import render_glyphs

            limit = TEXT_MAX[bool(pj.properties and pj.properties.support_600_txt)]
            if len(args.text) > limit:
                raise PixelJoyError(f"text is {len(args.text)} chars; this panel takes at most {limit}")
            style = P.TextStyle(color=args.color, rainbow=args.rainbow, speed=args.speed)
            await pj.show_text(render_glyphs(args.text, f.model.height, args.font), style)
        elif args.cmd == "image":
            from .render import image_to_png

            await pj.show_png(image_to_png(args.path, f.model.width, f.model.height, args.fit))
        elif args.cmd == "gif":
            await pj.show_gif(args.path.read(), args.speed)
        elif args.cmd == "raw":
            await pj._write(P.frame(args.cmd_byte, b"".join(args.payload)))
            for r in await pj.drain(args.wait):
                print(f"{r.head:02X} {r.cmd:02X}  {r.payload.hex(' ')}{'' if r.crc_ok else '  (bad crc)'}")
            return
        await asyncio.sleep(0.5)  # let the last write-without-response flush before disconnect


# -- parser -------------------------------------------------------------------

def _add_globals(p: argparse.ArgumentParser, suppress: bool) -> None:
    """Global options. Subparsers get SUPPRESS defaults so they don't clobber values given before the subcommand."""
    d = (lambda v: argparse.SUPPRESS) if suppress else (lambda v: v)
    g = p.add_argument_group("global options")
    g.add_argument("-v", "--verbose", action="store_true", default=d(False), help="log every TX/RX frame; full tracebacks on error")
    g.add_argument("-q", "--quiet", action="store_true", default=d(False), help="only print command output and errors")
    g.add_argument("--address", default=d(None), help="use the panel with this BLE address (macOS: CoreBluetooth UUID)")
    g.add_argument("--name", default=d(None), help="use the first panel whose name contains this (case-insensitive)")
    g.add_argument("--timeout", type=float, default=d(8.0), metavar="SECONDS", help="max scan time (default: 8)")
    g.add_argument("--tz", default=d(None), metavar="ZONE", help="IANA time zone for the panel clock (default: this machine's)")
    g.add_argument("--no-time-sync", action="store_true", default=d(False), help="don't sync the panel clock on connect")


def build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    ap = argparse.ArgumentParser(
        prog="pixeljoy", formatter_class=fmt,
        description="Control PixelJoy BLE LED matrix panels.\n\n"
                    "Every command except `scan` connects to the first matching panel, handshakes,\n"
                    "syncs the panel clock (unless --no-time-sync), runs, and disconnects.\n"
                    "Close the phone app first: the panel accepts one connection at a time.",
        epilog="examples:\n"
               "  pixeljoy scan\n"
               "  pixeljoy info --json\n"
               "  pixeljoy text 'HELLO' --color '#00ff88'\n"
               "  pixeljoy -v --name A1B2 image cat.png --fit cover\n\n"
               "exit codes: 0 ok, 1 error, 2 bad arguments, 130 interrupted\n"
               "`pixeljoy COMMAND -h` for command help. Docs: docs/USAGE.md")
    _add_globals(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    def cmd(name: str, help: str, description: str, examples: list[str]) -> argparse.ArgumentParser:
        s = sub.add_parser(name, help=help, description=description, formatter_class=fmt,
                           epilog="examples:\n" + "\n".join(f"  pixeljoy {e}" for e in examples))
        _add_globals(s, suppress=True)
        return s

    s = cmd("scan", "list nearby panels", "Scan for --timeout seconds and list PixelJoy panels with their model.",
            ["scan", "scan --all --timeout 15"])
    s.add_argument("--all", action="store_true", help="show non-PixelJoy devices too")

    cmd("gatt", "dump GATT services/characteristics",
        "Connect without handshaking and print every service, characteristic and descriptor.", ["gatt"])

    s = cmd("info", "show model, firmware and READ_PROPERTY flags",
            "Handshake, read properties and firmware version.", ["info", "info --json | jq .properties"])
    s.add_argument("--json", action="store_true", help="print JSON")

    cmd("time", "sync the panel clock",
        "Send time zone + DST rules, wall clock and start (this also happens on every connect).",
        ["time", "time --tz Europe/London"])

    s = cmd("power", "turn the display on or off", "Turn the display on or off.", ["power off", "power on"])
    s.add_argument("state", choices=["on", "off"])

    s = cmd("brightness", "set brightness (percent, 1-100)",
            "Set brightness as a percentage, 1-100. This is the same scale as the app's\n"
            "brightness slider (0-100). The panel reads 0 back as 1, so 1 is the minimum.",
            ["brightness 1     # dimmest", "brightness 50", "brightness 100   # full"])
    s.add_argument("level", type=int_range(1, 100), metavar="PERCENT", help="1-100")

    s = cmd("text", "scroll text",
            "Render text on this machine (the panel has no fonts), upload it and show it.\n"
            "Max length is 600 chars if the panel reports support600Txt, else 100.",
            ["text 'HELLO'", "text 'SALE' --color ff0000 --speed 80", "text 'party' --rainbow 1",
             "text 'Hi' --font /System/Library/Fonts/Supplemental/Arial.ttf"])
    s.add_argument("text")
    c = s.add_mutually_exclusive_group()
    c.add_argument("--color", type=hex_color, default=(255, 255, 255), metavar="RRGGBB", help="text colour (default: ffffff)")
    c.add_argument("--rainbow", type=int_range(0, 255), metavar="N", help="rainbow colour scheme index (scheme range unverified)")
    s.add_argument("--speed", type=int_range(0, 255), default=50, help="scroll speed 0-255 (default: 50)")
    s.add_argument("--font", metavar="TTF", help="path to a .ttf (default: Pillow's built-in font)")

    s = cmd("image", "show a still image",
            "Resize an image to the panel, upload it as PNG and show it.",
            ["image cat.png", "image logo.png --fit contain"])
    s.add_argument("path", type=argparse.FileType("rb"), help="any image Pillow can open")
    s.add_argument("--fit", choices=["stretch", "contain", "cover"], default="stretch",
                   help="stretch: ignore aspect; contain: letterbox in black; cover: crop to fill (default: stretch)")

    s = cmd("gif", "show an animated GIF",
            "Upload a GIF as-is and play it. Size it to the panel first; it is not resized.",
            ["gif nyan.gif", "gif nyan.gif --speed 900"])
    s.add_argument("path", type=argparse.FileType("rb"), help="GIF file")
    s.add_argument("--speed", type=int_range(0, 1050), default=600, help="0-1050, higher is faster (default: 600)")

    s = cmd("raw", "send one raw A0 frame",
            "Frame CMD + PAYLOAD as A0 cmd len payload crc, send it, and print replies for --wait seconds.",
            ["raw 2b", "raw 11 01", "raw 13 '0c'", "raw 0f --wait 5"])
    s.add_argument("cmd_byte", type=hex_byte, metavar="CMD", help="command byte, hex 00-FF")
    s.add_argument("payload", type=hex_bytes, nargs="*", help="payload hex; spaces allowed")
    s.add_argument("--wait", type=float, default=2.0, metavar="SECONDS", help="how long to print replies (default: 2)")
    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    if args.cmd is None:
        ap.print_help(sys.stderr)
        sys.exit(2)
    level = logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    if not args.verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)

    from bleak.exc import BleakError
    from .client import PixelJoyError

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(EXIT_INTERRUPTED)
    except (PixelJoyError, BleakError, asyncio.TimeoutError, TimeoutError, OSError) as e:
        if args.verbose:
            traceback.print_exc()
        print(f"error: {e or type(e).__name__}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


if __name__ == "__main__":
    main()
