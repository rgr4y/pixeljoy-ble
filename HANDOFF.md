# Handoff: continue locally

The cloud session is moving to a local machine with the panel present. Branch:
`claude/practical-babbage-1sqw9e`.

## Where things stand

**Confirmed on hardware** (P0AF_A1B2, 32×16, firmware 1.8.5, macOS):
- scan and model match (advertised UUID `c0af`, no manufacturer data)
- GATT: the write characteristic `a0af` is **write-without-response only**
  (509 bytes max on macOS)
- handshake: reply `CCHIP 04 05`, then `CCHIP 00` for success
- `READ_PROPERTY` decoded (captures are in `docs/PROTOCOL.md` §7 and in tests)
- `GET_FIRMWARE_VERSION` returns 1.8.5

**Implemented but not yet run on the panel:**
- clock sync on connect: `SET_TIME_ZONE` (0x8F) → `SYNC_TIME` (0x31) →
  `START_SYNC_TIME` (0x4A). The DST encoding is verified byte-for-byte against
  `java.time`. Test with `python -m pixeljoy -v time`, then check the clock
  on the panel. If it's off by exactly your UTC offset, the firmware is using
  the Unix-ms field rather than the local fields.
- power, brightness, text upload/show, PNG and GIF upload/show,
  multi-packet (`A2`) flow control

## Next task (was in progress when handed off)

Nothing has been edited for this yet; the working tree was clean at handoff.

1. **Ctrl+C handling** in `pixeljoy/__main__.py`:
   - catch `KeyboardInterrupt` around `asyncio.run`, print `interrupted`,
     and exit 130
   - in `PixelJoy.__aexit__` (`pixeljoy/client.py`), wrap the disconnect in
     `asyncio.wait_for(..., 5)` and swallow errors, so cleanup can't hang
   - map `PixelJoyError`, `BleakError`, `TimeoutError` and `OSError` to
     `error: …` with exit 1. Show the full traceback only with `-v`.
2. **Full `--help`**:
   - `RawDescriptionHelpFormatter`, with a description and an examples epilog
     on the main parser and on every subcommand
   - global options (`-v`, `-q`, `--address`, `--name`, `--timeout`, `--tz`,
     `--no-time-sync`) accepted before *or* after the subcommand: put them in
     a parent parser, with `default=argparse.SUPPRESS` on the subparsers
   - validated types: hex colour (`#rrggbb` or `rrggbb`), brightness 1–255,
     raw cmd byte `00`–`FF`, raw payload hex with spaces allowed
   - `info`: readable output plus `--json`
   - `raw`: add `--wait SECONDS` and print any `A1` replies (needs a small
     reply-drain helper in the client)
   - `text`: `--rainbow N`; enforce 100 or 600 chars depending on `support_600_txt`
   - `image`: `--fit {stretch,contain,cover}`
3. **Faster connect**: in `_pick`, use `BleakScanner.find_device_by_filter`
   so the scan stops at the first matching panel instead of always waiting
   `--timeout` seconds.
4. **`docs/USAGE.md`**, linked from the README:
   - install; macOS Bluetooth permission for the terminal app
   - every command with examples; global options; exit codes
   - troubleshooting: close the phone app, since the panel takes one central
     at a time; "no panel found"; time zone
   - a short Python API example

## Also open

- `docs/PROTOCOL.md` §6 checklist:
  - brightness range (the panel reported 12)
  - `SET_FLIP` values
  - `SHOW_TEXT` speed and mode ranges
  - whether the PNG decoder needs zlib level 0
  - `SHOW_CLOCK` (0x0D) and the timer commands are not yet traced
- Patched app (`tools/patch_apk.py`, `docs/APP_PRIVACY.md`): built and
  verified statically, but not yet installed on a phone. Regenerate the
  signing key locally rather than reusing the one from the cloud session.

## Re-deriving anything from the vendor app

`tools/fetch_apk.sh` downloads the APK and decompiles it into `re/` (git-ignored).
The key classes are listed at the top of `docs/PROTOCOL.md`.
