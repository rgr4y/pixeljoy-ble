# PixelJoy BLE LED panel protocol

Reverse-engineered from the official Android app **PixelJoy v1.4.1**
(`com.th.pixeljoy`, Shenzhen Wanshun Zhilian Technology). The APK came from
the vendor's own download URL behind the QR code on the retail box:

```
https://pixeljoy.smartthingsapp.com/app/QRcode  ->  https://pixeljoyapk.smartthingsapp.com/APK/PixelJoy-V1.4.1.apk
sha256 adc27db0311e083ede308f7dadc83a745d3bcbbedbf96a625aa12ff6960628e1
```

The protocol code is plain Kotlin (no native library, no packer, no
encryption). The relevant classes are:

| Class | Role |
|---|---|
| `com.th.common.utils.BleConnectUtil` | connect, MTU, subscribe, reply dispatcher |
| `com.th.common.utils.BleDataUtils` | `A0` frame builder |
| `com.th.common.protocol.command.AppCommandUtil` | handshake, multi-packet (`A2`) builder |
| `com.th.common.protocol.command.MultiPackUtilV2` | multi-packet flow control / retransmit |
| `com.th.common.protocol.pixel.DeviceCommand` | every command builder |
| `com.th.common.protocol.pixel.PixelProtocol` | reply parser |
| `assets/th_dev_cfg.json` | built-in model table (sizes, UUIDs) |

> **Status:** derived from static analysis. Connection, handshake and
> `READ_PROPERTY` are **confirmed on hardware** (P0AF_A1B2, 32×16, firmware
> 1.8.5; see §7). Everything else is not yet exercised on a panel. Items
> marked **(unverified)** are inferences. Anything not marked comes straight
> from the decompiled code.

---

## 1. Hardware / discovery

* SoC: JieLi **AC695x / AC6951C** (from `deviceChip` in the model table).
  JieLi's stock RCSP/OTA protocol is **not** used. This is a custom vendor protocol.
* The app identifies a panel by **both**:
  1. the advertised name **equals** `deviceName` (e.g. `P08F_XXX`) **or contains** `deviceMoldPid` (e.g. `P08F`), **and**
  2. the advertised service-UUID list **contains** the scan UUID `0000Cxxx-0000-1000-8000-00805f9b34fb`.
* All 16-bit IDs expand on the Bluetooth base UUID: `0000XXXX-0000-1000-8000-00805f9b34fb`.

Each model has its own UUID set derived from its PID `Pxxx`:

| Purpose | UUID (16-bit) |
|---|---|
| Advertised scan UUID | `Cxxx` |
| GATT service | `Exxx` |
| Write characteristic | `Axxx` |
| Notify characteristic | `Fxxx` |

### 32×16 models (matches the box: "Pixel 16x32", 5V 2A)

| Name | PID | Chip | Service | Write | Notify | Scan |
|---|---|---|---|---|---|---|
| `P088_XXX` | P088 | "088" | `E088` | `A088` | `F088` | `C088` |
| `P08F_XXX` | P08F | AC6951C | `E08F` | `A08F` | `F08F` | `C08F` |
| `P0AF_XXX` | P0AF | AC6951C | `E0AF` | `A0AF` | `F0AF` | `C0AF` |

`P0AF` has `deviceMoreType: 2`. The other two have `1`. What that field
means is not yet known. The full 40-entry table is in
[`pixeljoy/devices.json`](../pixeljoy/devices.json). It covers sizes from 32×16 to 448×32, plus
192×64 and 64×64.

Observed on hardware: the panel advertises as `P0AF_A1B2` with the single
service UUID `0000c0af-…` and no manufacturer data. The suffix is presumably
derived from the MAC. Its write characteristic `A0AF` offers only
`write-without-response`, with a max of 509 bytes on macOS.

---

## 2. Connection sequence

1. Connect and request **MTU 512**. Writes use **write-without-response**: the app never sets a write type, so Android uses the characteristic default. A with-response write gets ATT error 3, "Write Not Permitted" (confirmed on a P0AF). On success the app sets its write split
   size to `min(mtu-3, 509)`. If the MTU request fails, it continues anyway.
2. Enable notifications on `Fxxx`.
3. **Handshake 1**: send `A0 00 08 'C' 'C' 'H' 'I' 'P' crc`. The app resends
   it every 1 s, up to 3 times.
4. The device replies with an `A1` frame, cmd `0x00`. Call its payload `p`.
   The payload is `"CCHIP"` echoed plus 2 challenge bytes `p[5]`, `p[6]`.
   Confirmed on hardware: `43 43 48 49 50 04 05`.
5. **Handshake 2**: send cmd `0x01` with payload
   `"CCHIP" + (p[5] ^ 0x24) + (p[6] ^ 0x4C)`. The XOR constants are the
   command bytes `SET_PARTITION` (0x24) and `READ_COMMUNICATION_VERSION`
   (0x4C).
6. The device replies with `A1` cmd `0x01`, payload `"CCHIP" + status`.
   Status `0` means success. Confirmed on hardware: `43 43 48 49 50 00`.
7. The app then sends `READ_PROPERTY` (cmd `0x10`, payload `01`). See §5.1.
8. After the property reply, the app syncs the clock (`MainActivity.syncMoreDeviceInfo`):
   `SET_TIME_ZONE` (0x8F) → `SYNC_TIME` (0x31) → `GET_FIRMWARE_VERSION` →
   `GET_RESOURCE_VERSION` → `START_SYNC_TIME` (0x4A, payload `01`).
   It skips this for splice/same-size groups.

### 2.1 `SET_TIME_ZONE` payload (from `DaylightSavingUtil`)

```
std_h std_m is_dst | s_mon s_wk s_dow s_hr | e_mon e_wk e_dow e_hr | dst_h      (all i8)
```

* `std_h`, `std_m`: the zone's *standard* offset. Java int division, so −3:30
  is `FD E2` (−3, −30).
* The two rules are the year's offset-increase and offset-decrease transitions.
  * `wk`: ALIGNED_WEEK_OF_MONTH, i.e. `(day-1)//7+1`. "Last Sunday" can be 4 or 5.
  * `dow`: Mon=1 … Sun=7.
  * `hr`: the wall-clock hour *before* the jump (e.g. 02 for US spring-forward
    and fall-back). The app derives it from the post-transition local time
    shifted by ∓`dst_h`, with day/week wrap.
* If the zone has no DST: `is_dst=0` and all rule bytes are 0.
* Example, America/Los_Angeles 2026: `F8 00 01 03 02 07 02 0B 01 07 02 01`.

`pixeljoy.protocol.time_zone_payload()` reproduces this. It was checked
byte-for-byte against the same algorithm in `java.time` for LA, London,
Sydney, Kolkata, Phoenix, St John's and Chatham.

Sending commands before the handshake completes probably gets them ignored
**(unverified)**.

---

## 3. Framing

All multi-byte integers are **little-endian**. The one exception is the
year in `SYNC_TIME`.

### 3.1 CRC

CRC-16/MODBUS: poly `0xA001` (reflected `0x8005`), init `0xFFFF`, no final
XOR. Check value: `crc("123456789") = 0x4B37`. It is appended **low byte first**.

### 3.2 Short frame: app → device (`A0`), device → app (`A1`)

```
+------+-----+-----+-----------------+--------+--------+
| head | cmd | len | payload (len-3) | crc_lo | crc_hi |
+------+-----+-----+-----------------+--------+--------+
  A0/A1         len = 3 + len(payload)    crc over head..payload
```

`len` is one byte. The app uses this frame whenever `len(payload) <= 247`
(and `<= mtu-3` if the MTU is smaller). With a small MTU, FastBle splits one
frame across several ATT writes, and the firmware reassembles it.

Other heads seen in the code:

| Head | Direction | Meaning |
|---|---|---|
| `A0` | app→dev | command |
| `A1` | dev→app | command reply (same cmd byte) |
| `A2` | app→dev | multi-packet chunk (§3.3) |
| `A3` | dev→app | multi-packet "send next group" |
| `A4` | dev→app | multi-packet status / lost-packet request |
| `A9` | dev→app | device asks for the time |
| `AA` | app→dev | time reply: `AA 01 0B <unix_ms u64 LE> crc` |

### 3.3 Long transfers: multi-packet (`A2`)

Large payloads (file uploads, long text properties, carousel lists) are
wrapped like this. First, build a *stream*:

```
stream = cmd(1) | total_len(u32 LE) | data
```

Then cut it into chunks of `split - 11` bytes. `split` is `min(mtu-3, 509)`
for `SEND_FILE` (`useSplitWriteNum=true`). Everything else uses
`min(mtu-3, 247)`. Each chunk is sent as:

```
A2 | session(u16) | total_packets(u16) | index(u16, 1-based) | chunk_len(u16) | chunk | crc16(chunk)(u16)
```

`session` is a random number in `[0, 500)`, fixed for the whole transfer.
**The CRC covers only the chunk bytes**, not the header.

**Flow control** (`MultiPackUtilV2`):

* The packets are grouped into groups of `packV2Num`. That value comes from
  `READ_PROPERTY`: **4** normally, **200** if the device sets the
  `supportNoResponseSendA4` flag. The app pins it with `SET_MULTI_PACK_V2`
  (`0x45`, payload `[1, n]`).
* Within a group, send one packet per write-complete callback.
* After a group, wait for the device:
  * `A3 …` → send the next group.
  * `A4 …` with byte `[9] == 1` → packet lost. Bytes `[10..11]` (u16 LE)
    give the 1-based index to resume from.
  * `A4 …` with byte `[9] != 1` → send the next group.
  * If `supportNoResponseSendA4` is set and this is a file transfer, don't
    wait. Sleep ~20 ms between groups.
* Watchdog: 10 s without a response fails the transfer. On failure the app
  sends `DELETE_FILE` for the partial file.
* At the end of a `SEND_FILE`, the device sends `A1 07` with payload
  `[1]` for OK, or `[0, reason]` on failure (reason 1 or 2, meaning unknown).

The `A3` CRC check in the app is odd (CRC of the 2 bytes before the CRC), and
`A4` checks a CRC over bytes `[9..18]`. The reference client therefore
treats `A3`/`A4` as advisory and doesn't reject them on CRC.

---

## 4. Commands (app → device, `A0` frames)

Some payloads exceed the single-frame limit (`len(payload) > 247`). Those go
through §3.3 with the same cmd byte.

| Cmd | Name | Payload |
|---|---|---|
| `00` | handshake 1 | `"CCHIP"` |
| `01` | handshake 2 | `"CCHIP" x^0x24 y^0x4C` |
| `02` | CLEAR_DRAWING_BOARD | `01` |
| `03` | FILL_RECT / PAINT_BUCKET | `r g b x1 y1 x2 y2` (x as u16 LE if width > 256) |
| `04` | DRAW_PIXEL | `r g b x y` (x as u16 LE if width > 256) |
| `05` | FILL_REGION_MASK | multi-pack: `w u16, h u16, r g b, bitmask[(w*h+7)/8]` (LSB-first) |
| `07` | SEND_FILE | `name_len name file_bytes` |
| `08` | DELETE_FILE | `name_len name` |
| `09` | ASK_FILE_EXIST | `name_len name` |
| `0A` | SHOW_TEXT | TLV, §4.2 |
| `0B` | SHOW_IMG | TLV, §4.3 |
| `0C` | SHOW_GIF | TLV, §4.3 |
| `0D` | SHOW_CLOCK | `style 24h date r g b`: style 0–9 (the app's 10 clock styles; `clock_*_gif3216.gif` are only picker previews), `24h` 1 = 24-hour, `date` 1 = show the date, then the digit colour (`ClockActivity.buildClockDisplayData`). The firmware draws the time from its own RTC. Confirmed on P0AF: style 2 with 12 h and date on shows the real time in 12-hour format, plays the style's animation along the bottom (as in its preview GIF), and swaps to the date for about 5 s every 10–15 s (rough, by eye). The payload has no field for the date timing. Style 10 (one past the app's range) is acked with `A1 0D 01` but leaves the screen blank, so an ack does not mean the style exists. |
| `0E` | STOPWATCH | `1`..`5` (start/pause/reset/…, exact mapping unverified) |
| `0F` | PLAY_CAROUSEL | repeated `name_len name type play_cnt [FF switch_anim]` |
| `10` | READ_PROPERTY | `01` |
| `11` | SWITCH_LIGHT | `01` on / `00` off |
| `13` | SEND_BRIGHTNESS | `level`: percent, 1–100. The app sends the raw progress of a SeekBar with `android:max="100"` (`app_fragment_my.xml`). Read-back 0 is treated as 1. |
| `15` | SET_FLIP | `flip_type` |
| `1A` | MUSIC_RHYTHM | (not yet traced) |
| `1D` | ADD_TO_CAROUSEL | `file_type name_len name` |
| `1E` | DELETE_FROM_CAROUSEL | `name_len name` |
| `1F` | READ_CAROUSEL_LIST | `01` |
| `20` | READ_CAROUSEL_FILE_FOLDER | `01` |
| `21` | CLEAR_CAROUSEL | `01` |
| `23` | READ_EXTERNAL_FLASH_SPACE | `01` |
| `24` | SET_PARTITION | (split-screen; not yet traced) |
| `25` | ADD_PARTITION_TO_LOOP | `01` |
| `26` | DRAW_EDIT_DIY_PIC | multi-pack: `w u16, h u16, n_colors, n×rgb, w*h palette indices` |
| `27` | SET_SPLICE_PARAMS | `w u16, h u16, x u16, y u16` (multi-panel splice) |
| `29` | SHOW_EFFECT_TEXT | (not yet traced) |
| `30`–`36` | timers / countdown | `36` COUNTDOWN: `state 01` |
| `31` | SYNC_TIME | `year(u16 **BE**) mon day hh mm ss dow unix_ms(u64 LE)`; `dow` Mon=0 … Sun=6 |
| `3D` | READ_CAROUSEL_EXTEND_PARAM | `01` |
| `41` | GET_FIRMWARE_VERSION | `01` |
| `42` | GET_RESOURCE_VERSION | `01` |
| `44` | SET_DEVICE_LOCK | `01 06 <6-char pwd>` lock / `00` unlock |
| `45` | SET_MULTI_PACK_V2 | `enable n` |
| `48` | SET_REMOTE | `id 01 param` (IR remote) |
| `49` | READ_REMOTE | `01` |
| `4A` | START_SYNC_TIME | `01` |
| `4B` | SHOW_SPLICE_NUM | |
| `4C` | READ_COMMUNICATION_VERSION | `01` |
| `51` | RHYTHMIC_SPECTRUM | |
| `82` | CHECK_DEVICE_PWD | `pwd` |
| `8E` | RESET_DEVICE (file system) | `02` |
| `8F` | SET_TIME_ZONE | 12 bytes, see §2.1 |

### 4.1 Showing content = upload a file, then reference it by name

The firmware keeps content in a flash file system. Almost every display mode
works the same way:

1. `SEND_FILE` (`07`) with a short name such as `txt123456`, `img123456`,
   `dyn123456` or `CT123456`. The app uses the prefix plus the last 6 digits
   of `nanoTime`.
2. Wait for `A1 07 01`.
3. Send the matching `SHOW_*` command that refers to that name.

### 4.2 Text

The phone rasterises text. The firmware has no font. The file uploaded by
`SEND_FILE` is a concatenation of per-character records:

```
glyph : 01 | w | h | count | bitmap   (1 bpp, row-major, MSB = leftmost, each row padded to a byte)
emoji : 02 | w | h | count | rgb      (w*h*3 bytes, RGB888)
```

`count` is `01` (u8) normally, and `01 00` (u16 LE) if the panel sets the
`support600Txt` property flag. The best guess is a frame or glyph count,
always 1 here **(unverified)**. The app caps text at 600 chars when the
flag is set, else 100 (`AddOrEditTextActivity`: `isSupport600txt() ? 600 : 100`).

Then send `SHOW_TEXT` (`0A`) with TLV properties. Each tag is followed by a
1-byte length, **except tag 02, whose length is u16 LE**:

| Tag | Len | Value |
|---|---|---|
| `01` | 1 | rainbow ("iridescent") mode, colour-scheme index. *Either* `01` or `02`. |
| `02` | u16 | colour runs: `[start end r g b]…`; start/end are char indices (u8, or u16 LE if `support600Txt`); emoji runs use `0 0 0` |
| `03` | 1 | rotation |
| `04` | 1 | char spacing |
| `05` | 1 | font mode (`2` = RTL ordering for Arabic) |
| `06` | 1 | speed |
| `07` | 1 | scroll gap |
| `08` | 1 | border type (0 = none) |
| `09` | 1 | border effect |
| `0A` | 1 | border speed |
| `0B` | n | file name (the uploaded text file) |
| `0F` | 3 | background RGB (only if the `supportTextBgColor` flag is set) |
| `10` | 1 | alignment (only if the `supportTextAlign` flag is set) |

Glyph heights the app uses: 12, 14, 16, 24, 30, 32, 64. A 16-tall panel
uses 16 px glyphs.

### 4.3 Images and GIFs

Custom images are uploaded as **ordinary PNG files**, sized exactly to the
panel: 8-bit RGB, no alpha, zlib level 0. They come from `BitmapConverter.saveAsSimplePng`.
On panels with ≥ 3072 pixels the app may use an 8-bit BMP instead. Custom
animations are uploaded as GIFs.

```
SHOW_IMG (0B): 01 <n> <name> | 02 01 <mode> | 03 02 <(1050 - speed) u16>
                gallery images send mode 0, interval 0
SHOW_GIF (0C): 01 <n> <name> | 03 02 <(1050 - speed) u16> [| 05 08 <sync_unix_ms u64>]
```

DIY animations send interval `450` and no mode tag.

---

## 5. Replies (device → app, `A1` frames)

### 5.1 `READ_PROPERTY` (`10`)

| Byte | Meaning |
|---|---|
| 0 | power (1 = on) |
| 1 | panel width (observed `0x20` = 32; the app ignores it) |
| 2 | panel height (observed `0x10` = 16; the app ignores it) |
| 3 | current mode |
| 6 | brightness (0 → treat as 1) |
| 7 | vertical flip |
| 8 | flags: b0 supportLock, b1 locked, b2 supportPackV2, b3 packV2Set, b4 supportSetRemote, b5 supportNoResponseSendA4, b6 supportMultiScreen, b7 support600Txt |
| 9 | current packV2Num |
| 10 | flags: b1 splice, b3 horizontalReverse, b5 carouselSwitchAnim, b6 textBgColor, b7 textAlign |
| 11 | (optional) flags: b0 effectText, b2 floodFill |

### 5.2 Other replies

* `07`: file upload result (§3.3).
* `09`: file exists?
* `08`: delete result.
* `0A`/`29`: text shown.
* `0B`/`0C`: image or GIF shown.
* `41`: firmware version. Either 3 bytes `maj min patch` or an ASCII string.
* `42`: resource version (ASCII).
* `4C`: communication version (ASCII; drop the last byte if the length is 6).
* `0E`/`36`/`35`: stopwatch / countdown / timer state pushes.
* `A9` (head): the device wants the time. Reply `AA 01 0B <unix_ms u64 LE> crc`.

---

## 6. Open questions / next steps on hardware

- [x] Capture the advertisement: `P0AF_A1B2`, UUID `c0af`, no manufacturer data.
- [x] Confirm the handshake reply layout (`p[5]`, `p[6]`).
- [x] Dump a `READ_PROPERTY` reply and decode the flags for this unit (§7).
- [x] Brightness range: 0–100 percent (app slider max 100).
- [ ] Find the `SET_FLIP` values, and the `SHOW_TEXT` speed/mode ranges.
- [ ] Check whether the firmware's PNG decoder accepts compressed IDAT or only stored blocks (level 0).
- [x] `SHOW_CLOCK` (0x0D): confirmed on hardware.
- [ ] Trace timers (0x30–0x35) and the partition/effect-text commands.
- [ ] IR remote (`SET_REMOTE`/`READ_REMOTE`) semantics.

---

## 7. Hardware captures

**P0AF_A1B2**: 32×16, `GET_FIRMWARE_VERSION` → 1.8.5, macOS/CoreBluetooth, MTU 512.

```
handshake 1 reply payload : 43 43 48 49 50 04 05
handshake 2 reply payload : 43 43 48 49 50 00
READ_PROPERTY payload     : 01 20 10 04 00 00 0c 00 fd c8 00 00 … (40 bytes, rest 00)
```

Decoded: power on, size 32×16, mode 4, brightness 12, no flip. Flags
`0xFD` mean: lock supported (not locked), packV2 supported and set,
remote supported, no-response A4, multi-screen, 600-char text. packV2Num
is `0xC8` = 200. Bytes 10 and 11 are 0: no splice, text background colour,
text alignment, effect text or flood fill.

So for this unit:
* text files use the 2-byte `count` and u16 colour-run indices
  (`support600Txt`)
* SHOW_TEXT must not send tags `0F` or `10`
* multi-packet transfers send groups of 200 without waiting for `A3`/`A4`

Clock sync on connect (`SET_TIME_ZONE` → `SYNC_TIME` → `START_SYNC_TIME`)
is confirmed: the panel shows the right local time (America/Chicago). Each
step is acked with `A1 <cmd> 01`. The handshake-1 reply's `p[5]` changes per
connection (`04`, `00`, `05` seen).

Unknown: right after the time sync, the panel sent `a9 01 04 01 b3 78`
(head `A9`, cmd `01`, payload `01`) once. Not yet traced.

---

## 8. Firmware and resource ("material") updates

The app looks for updates with `POST https://pixeljoyapi.smartthingsapp.com/public/app/ota/getOTA`.
The body is JSON; form encoding returns `code 1017`. Fields:
`{"otaClass": N, "otaDevice": "<deviceMoldPid, e.g. P0AF>", "otaNumber": "<current version>"}`
(`DeviceRequest.checkFirmwareVersion`). `otaClass` 1 = firmware, 2 = material
(resource pack), 3 = communication version. The response's `otaSrc` points at an
S3 `.bin`, and `otaMD5` is that file's MD5.

On 2026-11-12, for P0AF with `otaNumber` 1.0.0:

| Class | Latest | Size | MD5 | Contents |
|---|---|---|---|---|
| 1 firmware | 1.9.6 (panel runs 1.8.5) | 302 KB | `e5731d33…` | encrypted or scrambled (no readable strings); typical for JieLi |
| 2 material | 1.2.1 | 822 KB | `2cb29106…` | plain container of 73 GIF89a files, see below |
| 3 comms | none | – | – | `updateEnable` 2 |

The files are kept in `re/firmware/` (git-ignored). Nothing has been flashed.

**Material pack layout (partly decoded):** header `6c 69 62 01 02 01 00 "C4x2"…`, then a
`gspl` block, then entries `"im" u32 offset u16 length` that each point at a GIF. Contents:

* 34 GIFs at 32×16:
  * the PixelJoy boot logo
  * the 10 clock-style animations: art on the bottom rows, black space at the top where the firmware draws the digits
  * car-window presets: `<<<LEFT`, `RIGHT>>>`, hazard triangles, "BABY IN CAR", "TURN OFF YOU…", eyes, fire, and others
* 39 GIFs at 96×16: colour gradients and stripes. These are probably the `SHOW_TEXT` rainbow (tag `01`) schemes, used as a texture behind the glyphs (unverified).

So the clock animations are swappable GIFs, but the only way to change them is a
material OTA. That needs the rest of the container format (checksums?) and the BLE OTA
transfer. A bad flash may brick the panel.
