# Progress

## 2026-11-12: CLI hardening, help, faster connect, usage docs

**Outcome**
- Ctrl+C exits with code 130 after a bounded (5 s) disconnect. `PixelJoyError`, `BleakError`, `TimeoutError` and `OSError` print `error: …` and exit 1. `-v` adds the traceback.
- Full `--help` with descriptions and examples on every subcommand. Global options work before or after the subcommand. New options: `-q` and `--name`.
- Validated arguments: hex colour, brightness 1–255, raw cmd byte, raw payload hex.
- `info --json`. `raw --wait` prints replies. `text --rainbow` and a 100/600-char limit. `image --fit {stretch,contain,cover}`.
- `client.find()` stops scanning at the first match. `info` now takes about 4.4 s end to end; before, the scan alone always waited `--timeout`.
- Added `docs/USAGE.md` and linked it from the README.

**Changed files:** `pixeljoy/__main__.py`, `pixeljoy/client.py` (`find`, `drain`, safe `__aexit__`), `pixeljoy/render.py`, `README.md`, `docs/USAGE.md`, `docs/PROTOCOL.md`

**Verification (on P0AF_A1B2, fw 1.8.5)**
- `info`, `info --json`, `raw 41 01` (prints the `A1 41` firmware reply), `text --rainbow 1`, and the 601-char rejection (exit 1).
- `image --fit contain`: Rob saw the expected 16×16 image with 8 px black bars on each side.
- Python API example from USAGE.md runs.
- SIGINT during the scan and during a session both exit 130. After SIGINT mid-session, the next connect works right away.
- Clock sync on connect is correct on the panel (America/Chicago).
- The 100/600 limit was checked in the decompiled app (`AddOrEditTextActivity`: `isSupport600txt() ? 600 : 100`).
- 23 unit tests pass.
- `text --rainbow 1` shows rainbow text on the panel (Rob confirmed).
- `image --fit cover`: 96×32 R/G/B test image shows 8 px red, 16 px green, 8 px blue (Rob confirmed).
- Not checked: `gif`.
- From the desktop app, Claude's Bash tool gets SIGABRT on any BLE command (Bluetooth permission for the launching process?). The same commands work from Rob's terminal and via `!`.

**Follow-up**
- Unknown reply `a9 01 04 01` after the time sync (noted in PROTOCOL.md §7).
- Still open in PROTOCOL.md §6: brightness max, `SET_FLIP`, text speed/mode ranges, compressed PNG (`simple_png` still uses zlib level 0), `SHOW_CLOCK` and timers.
- `HANDOFF.md` sits in the repo root and its "next task" is now done. Delete it, or move the open items into `docs/`?
- BLE commands need to run outside the Claude sandbox, because CoreBluetooth reports "Bluetooth is unsupported" inside it.
