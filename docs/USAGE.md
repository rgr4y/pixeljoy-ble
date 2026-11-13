# Using the `pixeljoy` CLI

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[render]'      # Pillow is needed for text and image
pixeljoy --help                 # same as: python -m pixeljoy --help
```

**macOS:** the first BLE scan asks for Bluetooth permission for the app that
runs Python (Terminal, iTerm, VS Code, …). If you denied it, re-enable it in
System Settings → Privacy & Security → Bluetooth. Without it, bleak reports
`Bluetooth is unsupported`. The same error appears inside a sandbox that
blocks CoreBluetooth.

## How a command runs

Every command except `scan` does this:

1. Scan until the first matching panel appears (at most `--timeout` seconds).
2. Connect, run the `CCHIP` handshake, and read the panel properties.
3. Sync the panel clock (time zone, DST rules, wall clock). Skip it with `--no-time-sync`.
4. Run the command, then disconnect.

The panel accepts **one connection at a time**. Close the PixelJoy phone app,
or turn off the phone's Bluetooth, before you use the CLI.

## Global options

You can put these before or after the command (`pixeljoy -v info` and
`pixeljoy info -v` are the same).

| Option | Meaning |
|---|---|
| `-v`, `--verbose` | Log every TX/RX frame in hex. Print full tracebacks on error. |
| `-q`, `--quiet` | Print only command output and errors. |
| `--address ADDR` | Use the panel with this address. On macOS this is a CoreBluetooth UUID, not a MAC. |
| `--name TEXT` | Use the first panel whose name contains `TEXT` (case-insensitive), e.g. `--name A1B2`. |
| `--timeout SECONDS` | Maximum scan time (default 8). The scan stops at the first match. |
| `--tz ZONE` | IANA time zone for the panel clock (default: this machine's). |
| `--no-time-sync` | Don't sync the clock on connect. |

## Commands

```bash
pixeljoy scan                          # list panels: address, RSSI, name, model
pixeljoy scan --all --timeout 15       # include non-PixelJoy devices

pixeljoy info                          # model, firmware, READ_PROPERTY flags
pixeljoy info --json | jq .properties

pixeljoy time                          # sync the clock (also done on every connect)
pixeljoy time --tz Europe/London

pixeljoy clock                         # live clock drawn by the panel (style 1, 24 h, white)
pixeljoy clock --style 3 --12h --date --color ff8800

pixeljoy power off
pixeljoy power on
pixeljoy brightness 50                 # percent, 1-100 (same scale as the app slider)

pixeljoy text 'HELLO'
pixeljoy text 'SALE' --color ff0000 --speed 80     # or --color '#ff0000'
pixeljoy text 'party' --rainbow 1                  # rainbow scheme index; replaces --color
pixeljoy text 'Hi' --font /System/Library/Fonts/Supplemental/Arial.ttf

pixeljoy image cat.png                 # --fit stretch (default)
pixeljoy image logo.png --fit contain  # keep aspect, letterbox in black
pixeljoy image photo.jpg --fit cover   # keep aspect, crop to fill

pixeljoy gif nyan.gif                  # uploaded as-is: size it to the panel first
pixeljoy gif nyan.gif --speed 900      # 0-1050, higher is faster

pixeljoy gatt                          # dump services and characteristics (no handshake)

pixeljoy raw 41 01                     # send A0 frame cmd 0x41 payload 01, print replies
pixeljoy raw 0f '01 02 03' --wait 5    # payload hex may contain spaces; listen 5 s
```

The text length limit is 600 characters if the panel reports `support600Txt`,
otherwise 100. This is the same limit the vendor app enforces.

`raw` prints each reply as `HEAD CMD  payload`, for example `A1 41  31 2e 38 2e 35`
(firmware "1.8.5"). Replies from the clock sync on connect show up too.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Error: no panel found, BLE failure, timeout, or a rejected command. Prints `error: …`; add `-v` for the traceback. |
| 2 | Bad arguments, or no command (prints the full help) |
| 130 | Interrupted (Ctrl+C). The client disconnects first, so you can reconnect right away. |

## Troubleshooting

- **`no PixelJoy panel found`**: the phone app is probably still connected.
  Close it. Then run `pixeljoy scan --all` to see if the panel advertises at all.
  Its name looks like `P0AF_A1B2`.
- **Several panels**: use `--name` or `--address`. Get both from `scan`.
- **Clock is wrong**: check `--tz`. If the clock is off by exactly your UTC
  offset, report it. That would mean the firmware uses the Unix-ms field
  instead of the local fields.
- **Something odd**: rerun with `-v` and keep the TX/RX log.

## Python API

```python
import asyncio
from pixeljoy.client import PixelJoy, find
from pixeljoy.render import render_glyphs
from pixeljoy import protocol as P

async def main():
    f = await find(timeout=8, name="A1B2")
    async with PixelJoy(f.device, f.model) as pj:   # handshake + clock sync
        print(await pj.firmware_version())
        await pj.brightness(20)
        await pj.show_text(render_glyphs("HI", f.model.height), P.TextStyle(color=(0, 255, 136)))
        await asyncio.sleep(0.5)  # let the last write-without-response flush

asyncio.run(main())
```
