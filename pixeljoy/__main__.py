"""CLI: python -m pixeljoy {scan,info,power,brightness,text,image,gif,raw}"""

from __future__ import annotations

import argparse
import asyncio
import logging

from . import protocol as P


async def _pick(args):
    from .client import scan

    found = await scan(args.timeout)
    if args.address:
        found = [f for f in found if f.device.address.lower() == args.address.lower()]
    if not found:
        raise SystemExit("no PixelJoy panel found (try `scan --all`)")
    f = found[0]
    print(f"using {f.name} {f.device.address} -> {f.model.name} {f.model.width}x{f.model.height}")
    return f


async def main_async(args) -> None:
    from .client import PixelJoy, scan

    if args.cmd == "scan":
        for f in await scan(args.timeout, show_all=args.all):
            m = f"{f.model.name} {f.model.width}x{f.model.height}" if f.model else "-"
            mfr = {k: v.hex() for k, v in f.manufacturer_data.items()}
            print(f"{f.device.address}  rssi={f.rssi}  name={f.name!r}  model={m}\n    uuids={f.service_uuids}\n    mfr={mfr}")
        return

    f = await _pick(args)
    async with PixelJoy(f.device, f.model) as pj:
        if args.cmd == "info":
            print(pj.properties)
            print("firmware:", await pj.firmware_version())
        elif args.cmd == "power":
            await pj.power(args.state == "on")
        elif args.cmd == "brightness":
            await pj.brightness(args.level)
        elif args.cmd == "text":
            from .render import render_glyphs

            style = P.TextStyle(color=tuple(int(args.color[i:i + 2], 16) for i in (0, 2, 4)), speed=args.speed)
            await pj.show_text(render_glyphs(args.text, f.model.height, args.font), style)
        elif args.cmd == "image":
            from .render import image_to_png

            await pj.show_png(image_to_png(args.path, f.model.width, f.model.height))
        elif args.cmd == "gif":
            with open(args.path, "rb") as fh:
                await pj.show_gif(fh.read(), args.speed)
        elif args.cmd == "raw":
            await pj._write(P.frame(int(args.cmd_byte, 16), bytes.fromhex(args.payload)))
            await asyncio.sleep(2)
        await asyncio.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(prog="pixeljoy")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every TX/RX frame")
    ap.add_argument("--address", help="BLE address to use when several panels are present")
    ap.add_argument("--timeout", type=float, default=8.0, help="scan seconds")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--all", action="store_true", help="show non-PixelJoy devices too")
    sub.add_parser("info")
    s = sub.add_parser("power")
    s.add_argument("state", choices=["on", "off"])
    s = sub.add_parser("brightness")
    s.add_argument("level", type=int)
    s = sub.add_parser("text")
    s.add_argument("text")
    s.add_argument("--color", default="ffffff")
    s.add_argument("--speed", type=int, default=50)
    s.add_argument("--font", help="path to a .ttf")
    s = sub.add_parser("image")
    s.add_argument("path")
    s = sub.add_parser("gif")
    s.add_argument("path")
    s.add_argument("--speed", type=int, default=600)
    s = sub.add_parser("raw", help="send one A0 frame: raw <cmd hex> <payload hex>")
    s.add_argument("cmd_byte")
    s.add_argument("payload", nargs="?", default="")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
