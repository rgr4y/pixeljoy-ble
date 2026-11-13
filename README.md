# pixeljoy-ble

Open protocol notes and a Python client for the **PixelJoy** flexible
Bluetooth LED matrix panels. These are the cheap "LED MATRIX PANEL" units
sold for car windows and shop signs, controlled by the *PixelJoy* app
(Shenzhen Wanshun Zhilian Technology) and a small IR remote. They are built
on a JieLi AC695x/AC6951C BLE SoC.

**Status: protocol extracted from the Android app by static analysis; not
yet run against real hardware.** See [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

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
python -m pixeljoy raw 11 01          # send one raw A0 frame (cmd 0x11, payload 01)
```

`-v` logs every TX/RX frame in hex. Please capture that output on first
contact with a panel.

## Layout

| Path | What |
|---|---|
| `docs/PROTOCOL.md` | the spec, with references to the vendor classes it came from |
| `pixeljoy/protocol.py` | pure encoders/decoders (framing, CRC, handshake, multi-pack, text/PNG, replies) |
| `pixeljoy/client.py` | async `bleak` client: handshake, flow control, upload/show |
| `pixeljoy/render.py` | Pillow text → glyphs, image → panel-sized PNG |
| `pixeljoy/devices.json` | 40 known models (size, chip, UUIDs) from the app's `th_dev_cfg.json` |
| `tools/fetch_apk.sh` | re-download the vendor APK and decompile it with jadx (output is git-ignored) |
| `tools/patch_apk.py` | rebuild the vendor app without telemetry for personal use (`--offline` also drops INTERNET); see `docs/APP_PRIVACY.md` |
| `docs/APP_PRIVACY.md` | what the app sends home (hardware device ID on every request, IP geolocation, Tencent cloud speech) and what the patch removes |
| `tests/` | unit tests for the encoders (`pytest`) |
