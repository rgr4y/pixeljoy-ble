# pixeljoy-ble

Open protocol notes and a Python client for the **PixelJoy** flexible
Bluetooth LED matrix panels. These are the cheap "LED MATRIX PANEL" units
sold for car windows and shop signs, controlled by the *PixelJoy* app
(Shenzhen Wanshun Zhilian Technology) and a small IR remote. They are built
on a JieLi AC695x/AC6951C BLE SoC.

**Status: protocol extracted from the Android app by static analysis and
confirmed on a P0AF (32×16, firmware 1.8.5) panel: handshake, properties,
clock sync, text, image.** See [`docs/PROTOCOL.md`](docs/PROTOCOL.md).
CLI reference: [`docs/USAGE.md`](docs/USAGE.md).

## Prior art

As of Nov 2026 nothing public decodes PixelJoy specifically. Its protocol is
**not** iPixel Color, iDotMatrix, CoolLED, BK-Light or Badge Magic. It is
its own scheme:

* per-model GATT UUIDs `Exxx`/`Axxx`/`Fxxx`, where `xxx` is the model PID
* `A0 cmd len payload crc16-modbus` frames
* a `"CCHIP"` XOR challenge handshake
* an `A2` chunked transfer for files

The phone renders text into 1-bpp glyphs. Images go up as ordinary PNG
files and animations as GIFs.

## Quick start

```bash
pip install -e '.[render]'
python -m pixeljoy scan            # find the panel, prints name/UUIDs/model
python -m pixeljoy -v info         # handshake + READ_PROPERTY, logs every frame
python -m pixeljoy power off
python -m pixeljoy brightness 50
python -m pixeljoy -v text "HELLO" --color 00ff88
python -m pixeljoy -v image cat.png   # resized to the panel, uploaded as PNG
python -m pixeljoy raw 41 01          # send one raw A0 frame, print replies
```

Full CLI reference, exit codes and troubleshooting: [`docs/USAGE.md`](docs/USAGE.md).

`-v` logs every TX/RX frame in hex. Please capture that output on first
contact with a panel.

## Home Assistant

`custom_components/pixeljoy` is a Home Assistant integration built on the same library. Its structure is
adapted from [XTimt/LEDDMX](https://github.com/XTimt/LEDDMX) (MIT). It uses HA's own Bluetooth stack:
local adapters and ESPHome proxies both work.

- **Discovery:** panels are found by their scan UUID and model name (all 40 models). They can also be
  picked from the list of panels in range.
- **Light:** on/off and brightness. The panel's 1–100 % maps onto HA's 0–255. State is read from the
  panel when HA connects; until then it is shown as assumed.
- **Services:** `pixeljoy.show_text`, `show_clock` (style 1–10; higher styles are rejected because they
  corrupt the panel's RAM), `show_image` and `show_gif` (files must be under `allowlist_external_dirs`),
  and `sync_time` (uses HA's time zone).
- **Remote:** `event.<panel>_remote` fires for each key press on the 2.4 GHz `LZ` remote. These are
  passive BLE adverts with no connection, so presses arrive even when the panel is off. Repeated
  transmissions of the same press are dropped. Event types come from the firmware's key table:
  `power`, `off`, `left`, `right`, `brightness_up`/`brightness_down`, `flip`, `clock`, `carousel`, `timer_12h`, `button_1`..`button_3` and the six car signs (`sign_left`, `sign_right`, `sign_warning`, ...).
- **Connection:** the BLE link opens on demand and drops after 60 s idle, so the phone app can still connect.

Install by copying the component, following the library symlink:

```sh
rsync -aL custom_components/pixeljoy/ root@<ha-host>:/config/custom_components/pixeljoy/
```

Tests run against a real HA core with BLE mocked:
`pip install pytest-homeassistant-custom-component` then `pytest tests_ha -o asyncio_mode=auto`.

## Layout

| Path | What |
|---|---|
| `docs/USAGE.md` | CLI reference, troubleshooting, Python API example |
| `docs/PROTOCOL.md` | the spec, with references to the vendor classes it came from |
| `pixeljoy/protocol.py` | pure encoders/decoders (framing, CRC, handshake, multi-pack, text/PNG, replies) |
| `pixeljoy/client.py` | async `bleak` client: handshake, flow control, upload/show |
| `pixeljoy/render.py` | Pillow text → glyphs, image → panel-sized PNG |
| `pixeljoy/devices.json` | 40 known models (size, chip, UUIDs) from the app's `th_dev_cfg.json` |
| `tools/fetch_apk.sh` | re-download the vendor APK and decompile it with jadx (output is git-ignored) |
| `tools/patch_apk.py` | rebuild the vendor app without telemetry for personal use (`--offline` also drops INTERNET); see `docs/APP_PRIVACY.md` |
| `docs/APP_PRIVACY.md` | what the app sends home (hardware device ID on every request, IP geolocation, Tencent cloud speech) and what the patch removes |
| `tests/` | unit tests for the encoders (`pytest`) |
| `custom_components/pixeljoy/` | Home Assistant integration (`pixeljoy/` inside it is a symlink to the library) |
| `tests_ha/` | HA integration tests (`pytest-homeassistant-custom-component`) |
