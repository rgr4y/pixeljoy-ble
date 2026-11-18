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

## 2026-11-12: Firmware 1.9.6 disassembly and BLE command map

**Outcome**
- Found a working pi32v2 disassembler: jvitkauskas/pi32v2-isa (pure Python; its instruction table is checked against JieLi's objdump at `-mcpu=r3`). Kagaimiq's Ghidra module marks pi32v2 "very early stage". Quarkslab's fork needs Ghidra, which is not installed.
- `app.bin` loads at `0x1E00120`: the br23 `code0` ORIGIN in JieLi's AC63 SDK, confirmed by `IHDR`, `CCHIP`, `1.9.6` and the CRC-table cross-references.
- Mapped the receive path: BLE write callback `0x1E3454A` → `msg_task` `0x1E24498` (CRC/length check, silent drop) → dispatcher `0x1E22B14` (gates, then `tbh` on cmd). Every case is in `docs/FIRMWARE.md` §3.
- Answers, from static analysis only:
  - `a9 01 04 01` is the time request from the `ts_sync` task that `START_SYNC_TIME` starts.
  - Clock date/time alternation is fixed at 300/100 frames and only the `date` flag enables it.
  - Clock style 10 has no bounds check: its digits are written about 13 KB past the framebuffer (a RAM corruption bug).
  - SET_PARTITION has no handler in 1.9.6.
  - Drawing commands switch to their own mode 7, which clears the screen.
  - Brightness output is `20 + min(b,100)*4/5`.
  - LodePNG with a full inflater is present.
  - The material-pack parser verifies no checksum.

**Changed files**
- `tools/jl_disasm.py` (new): a raw-image loader around pi32v2-isa that resolves `tbb`/`tbh` jump tables; `--selftest`.
- `docs/FIRMWARE.md` (new).
- `docs/PROTOCOL.md` §8: link to FIRMWARE.md.

**Verification**
- `python3 tools/jl_disasm.py --selftest` passes and `ruff check` is clean.
- After the refactor, the full disassembly is byte-identical to the pre-refactor run.
- Every claim in FIRMWARE.md cites an address. None was exercised on the panel, and nothing was sent to it.
- Caveat: the panel runs 1.8.5 and this is 1.9.6. READ_PROPERTY byte 10 already differs (`0xEA` vs `0x00`).

**Follow-up**
- Never send SHOW_CLOCK style > 9 (out-of-bounds RAM writes). Done in the follow-up entry below.
- PROTOCOL.md §6/§7 still list the `A9` reply, the PNG question and the brightness/flip ranges as open; they can now point at FIRMWARE.md.

## 2026-11-12: Guard SHOW_CLOCK style

**Outcome:** `protocol.show_clock()` raises `ValueError` for styles outside 0–9. The CLI's `--style 1-10` already limited it, but API callers could send style 10, which makes the firmware write outside its framebuffer (FIRMWARE.md §5a). Brightness needed no change: the CLI takes 1–100 and `set_brightness` clamps. The earlier "CLI accepts 1–255" note was wrong.

**Changed files:** `pixeljoy/protocol.py`, `tests/test_protocol.py`, `docs/FIRMWARE.md`, `PROGRESS.md`

**Verification:** pytest and ruff pass. Not run on the panel.

## 2026-11-13: OTA format, cfg_tool.bin, board pads, UART log

**Outcome**
- OTA transfer traced: the app's `OTAManager` and firmware msg_task use a vendor `B1`/`B2`/`B3` protocol (material `B4`–`B6`). The sender supplies both checks (standard CRC-32 and a byte sum). There is no signature. All 4 CRC16s inside the image reproduce, so a patched image can be built. Still untraced: what JieLi's `dw_update` does after the check.
- `cfg_tool.bin` is byte-identical to JieLi's stock SDK default (earbud template). Nothing in it is vendor-specific.
- Board photos: `VCC DM DP GND` pads (USB, the route to the mask-ROM loader), `5V RX TX GND` pads (UART), U15 64 Mbit SPI NOR (probably the file system).
- UART confirmed on the 1.8.5 panel: the TX pad carries the debug log at 1,000,000 baud. It reports `frame rate: 50 fps`, which gives about 12 s of time and 4 s of date on the clock.

**Changed files**
- `docs/FIRMWARE.md`: OTA protocol, §7 cfg_tool and board, UART and frame rate.

**Verification**
- CRCs were recomputed and match the image.
- The UART log was read live, but the connection is flaky. The garbled bytes were partly caused by two readers on the port.
- Nothing was flashed or sent to the panel.

**Follow-up**
- Solder a header or use pogo pins on `TX`/`GND` for a stable log. Then capture the boot log.
- Before any custom flash: trace `dw_update`, then set up USB recovery (DM/DP + USB_KEY) or a SOIC-8 dump of U15.

## 2026-11-13: LZ remote decoded; Home Assistant integration (branch `ha-integration`)

**Outcome**
- Remote: a 2.4 GHz "BLE" remote at `11:22:33:44:55:66`.
  - Captured on a Linux host with the nRF52840 (Zephyr HCI, hci1).
  - Packet: name `LZ`, then manufacturer data `CC 02 | seq | id(2) | key | 00 00 | chk(2)`.
  - The firmware maps key codes to actions (table at 0x1E1EB7E).
  - The panel ignores the trailing check bytes. Any `LZ` advert is accepted until a remote is paired.
- New `custom_components/pixeljoy`, adapted from XTimt/LEDDMX (MIT):
  - Bluetooth discovery (40 scan UUIDs plus a model-name check)
  - light (on/off, brightness 1–100 %)
  - services: show_text, show_clock (1–10, bounded), show_image, show_gif, sync_time
  - an event entity for remote presses (passive adverts, repeats dropped by seq)
  - BLE through HA's stack via `bleak-retry-connector`, disconnecting after 60 s idle
- Library: `protocol.parse_remote_advert` and `REMOTE_KEYS`; a `PixelJoy._open()` hook so HA supplies the connection.

**Changed files**
- `pixeljoy/protocol.py`, `pixeljoy/client.py`, `tests/test_protocol.py`
- `custom_components/pixeljoy/*` (new)
- `tests_ha/*` (new)
- `README.md`, `docs/FIRMWARE.md`, `docs/PROTOCOL.md`

**Verification**
- `pytest tests` passes (25).
- `pytest tests_ha` passes (5) against HA 2026.2.3 via pytest-homeassistant-custom-component, with BLE mocked. It covers discovery, rejecting a non-panel name, brightness mapping, the clock style bound, and remote events firing once per seq.
- `ruff` is clean.
- The HA tests caught one real bug: the remote callback needs `connectable=False`.
- Not run on a live HA instance or against the panel.

**Follow-up**
- Deploy to HA (<ha-host>) and test against the panel.
- `custom_components/pixeljoy/pixeljoy` is a symlink: fine for an rsync `-L` install, but it breaks a HACS install.
- Check on hardware whether one press sends several seqs. If it does, remote events need a time debounce.

## 2026-11-18 — Packaging for pipx/pip, history scrub

**Outcome**
- `requirements.txt` is the runtime dependency source (`bleak>=0.22`, `Pillow>=10`); `pyproject.toml` reads it via `[tool.setuptools.dynamic]`.
- Pillow moved from the `render` extra to core, so a plain `pipx install` can run `text`/`image`/`gif`. `render` extra removed.
- Added `readme` and `[project.urls]` metadata. README has a new Install section (pipx, pip as library, dev); quick start uses the `pixeljoy` command. `docs/USAGE.md` install points at pipx.
- Git history rewritten with `git filter-repo --replace-text` and force-pushed (`main`, `ha-integration`):
  HA host IP → `<ha-host>`, capture hostname → "a Linux host", panel name suffix → placeholder,
  remote MAC and its derived bytes → placeholders. Commit messages too.

**Verification**
- `python -m build`: wheel has `pixeljoy/*` + `devices.json`, no tests/HA component; sdist has `requirements.txt` and `README.md`.
- Wheel installed with pipx into an isolated `PIPX_HOME`; `pixeljoy --help`, `load_models()` (40) and `render_glyphs` work from outside the repo.
- `pytest tests`: 26 passed on the rewritten tip. `tests_ha` not run.
- Full-history rescan after rewrite: no IPs, no hostnames, only placeholder MACs (`11:22:33:44:55:66`, `AA:BB:CC:DD:EE:01`).

**Follow-up**
- Repo is private again. Old SHAs stay fetchable on GitHub by hash (PR #1 history, caches) until GitHub support purges them or the repo is recreated.
- Local branches `main` and `claude/practical-babbage-1sqw9e` still hold pre-rewrite commits; delete or reset them.
- No LICENSE file. Pick one before going public.
- Commit author email is in every commit; rewrite with `--mailmap` if it should not be public.

## 2026-11-18 — HA pairing messages, PyPI publish workflow

**Outcome**
- `ha-pairing`: the manual "Add" flow now says why the list is empty: all panels already configured (`all_configured`), seen only by passive scanners (`no_connectable`), or nothing seen (`no_devices_found`, with a hint to close the phone app).
- `pypi-publish`: `.github/workflows/publish.yml` builds, tests and publishes on `v*` tags via trusted publishing (environment `pypi`).

**Root cause** (pairing): the panel was already set up by Bluetooth auto-discovery. The user step drops configured addresses and then reported "No PixelJoy panels in range". The advert itself (name + `c0af` UUID) was fine, and a connectable proxy saw it.

**Verification**
- `pytest tests_ha tests`: 36 passed (HA 2026.2.3 harness). New user-step tests fail on the old config flow (2 of 4).
- `actionlint` clean on the workflow; it has not run on GitHub yet.
- Not deployed to HA.

**Follow-up**
- `tests_ha` also needs HA's bluetooth/usb component requirements installed; the README line alone is not enough.
