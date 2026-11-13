# PixelJoy P0AF firmware: BLE command handler

Static analysis of the **1.9.6** OTA image (`P0AF_fw_1.9.6.bin`, decrypted with `tools/jl_fw_decrypt.py`).
The test panel runs **1.8.5**, so anything here can differ on that panel. Nothing was flashed or sent to
the panel for this work.

How claims are marked:

* **Confirmed** means read in the 1.9.6 disassembly. It comes with an address and, where it matters, an
  excerpt. It was not exercised on hardware unless noted.
* **Guess** means an inference. The reasoning is given.

Addresses are runtime addresses. `g` is the main firmware state struct at RAM `0xCE70` (decimal `52848`);
the code loads it constantly. `clk` is a second struct at RAM `0x4F30`.

---

## 1. Toolchain and load address

| Item | Value | Source |
|---|---|---|
| CPU | JieLi **pi32v2**, `-mcpu=r3` | The chip is BR23 (AC695N/AC635N) per [kagaimiq's notes](https://kagaimiq.github.io/jielie/) (`chips/br23`: "CPU Core: pi32v2"). The firmware's SDK is `AC637N_MX(BLE)`; JieLi's [fw-AC63_BT_SDK](https://github.com/Jieli-Tech/fw-AC63_BT_SDK) `apps/*/board/br23/Makefile` builds with `-target pi32v2 -mcpu=r3`. |
| Disassembler | [jvitkauskas/pi32v2-isa](https://github.com/jvitkauskas/pi32v2-isa) @ `3608ed1` (2026-11-09), pure Python | Its instruction table is checked against JieLi's own objdump for every form at `-mcpu=r3`. Kagaimiq's [ghidra-jieli](https://github.com/kagaimiq/ghidra-jieli) marks pi32v2 "very early stage". Quarkslab's [fork](https://github.com/quarkslab/ghidra-jieli) covers "most instructions", but needs Ghidra, which is not installed here. |
| Loader | `tools/jl_disasm.py` | Wraps app.bin in an in-memory ELF section for pi32v2-isa's objdump. It also finds the compiler's inline `tbb`/`tbh` jump tables (target = address after the `tbb`/`tbh` + 2 × entry) and prints each case's target. |
| Load address | **`0x1E00120`** | The `code0` ORIGIN in the SDK's `cpu/br23/sdk_ld.c` (`USE_SDFILE_NEW`). It matches the file table's `app_area_head` and is the SFC flash mapping in [kagaimiq's BR23 memory map](https://kagaimiq.github.io/jielie/) (`0x1000000–0x1FFFFFF`). |
| RAM code | Flash `0x1E44228`, `0x5694` bytes, copied to RAM `0x0` | Startup at `0x1E00182`: `r1 = 0x1E44228`, `r2 = 22164`, `rep` copy to `r4 = 0`. Calls into it from flash wrap the 25-bit bus (`call … 0x200204E` = RAM `0x204E`). `0x21127xx` is the mask ROM (`0x1127xx`: memcpy, memset, memcmp, strlen). |

The base address is **confirmed** by string cross-references. Loaded at `0x1E00120`:

```
1e1a746:  c1 ff 33 c1 e3 01   r1 = 0x1E3C133      "IHDR" (PNG decoder, 15 refs)
1e22cbc:  01 e1 76 81         r1 = r8 + 374       r8 = 0x1E3C040 -> 0x1E3C1B6 "CCHIP" (handshake memcmp)
1e2361a:  02 e1 7c 81         r2 = r8 + 380       -> 0x1E3C1BC "1.9.6" (GET_FIRMWARE_VERSION reply)
1e138ce:  c3 ff 40 b0 e3 01   r3 = 0x1E3B040      CRC-16/MODBUS table (0000 C0C1 C181 0140 …)
```

Many strings are reached as `rN + offset` from a merged-constant base (`0x1E3C040`), not by absolute address.

Reproduce:

```bash
git clone https://github.com/jvitkauskas/pi32v2-isa /tmp/pi32v2-isa
python3 -c "d=open('re/firmware/P0AF_fw_1.9.6.dec.bin','rb').read(); open('/tmp/app.bin','wb').write(d[0x120:0x120+0x497c4])"
PI32V2_ISA=/tmp/pi32v2-isa python3 tools/jl_disasm.py /tmp/app.bin > /tmp/app.dis
```

The full listing is about 110k lines and takes about 3 s. 1636 words still decode as `<unknown instruction>`; they are data
(pointer tables, strings) inside `.text`.

---

## 2. Receive path

```
BLE GATT write callback   0x1E3454A   switch (att_handle - 6): case 0 = the Axxx write characteristic
 ├─ data[0]==A0 && data[1]==0x51  -> RHYTHMIC_SPECTRUM handled inline (no CRC check, no reply)
 ├─ data[0]==AA                   -> time reply handled inline (no CRC check), see §5d
 └─ else  malloc(len) + memcpy, post to task "msg_task"          (0x1E346F2..0x1E34708)

msg_task loop             0x1E24498   pend on queue, buf[0] selects:
 ├─ A0  -> len>=4, crc16_modbus(buf, n-2) == LE u16 at end, buf[2] == n-2,
 │         then dispatch(cmd=buf[1], len=buf[2]-3, payload=buf+3)   0x1E244E2..0x1E24524
 ├─ A2  -> multi-packet reassembly (§4)
 ├─ A7, B1..B7  internal messages (BLE scan results, task control)
 └─ anything else dropped

dispatch()                0x1E22B14   gates, then `tbh` switch on cmd 0x00..0x4B, then 0x7A/0x8E/0x8F/0xF1
send_a1(cmd, len, buf)    0x1E13A18   builds A1 cmd len payload crc (most handlers end at 0x1E23AF4: len 1)
crc16_modbus(buf, len)    0x1E138EE   init 0xFFFF; crc16_update(crc, buf, len) at 0x1E138CC
```

**Confirmed: bad frames are dropped silently.** On a CRC mismatch, a wrong `len` byte or `n < 4`, the
code jumps straight to the loop end and sends no `A1`.

```
1e244f6:  r6 = r5 + -2                     ; n-2
1e244fa:  r4 = b[r7+r6] (u) ... insert     ; stored crc, LE
1e2450a:  call crc16_modbus(r7, r6)
1e2450e:  if (r4 != r0) goto 1e24c70       ; drop
1e24514:  r1 = b[r7+2]
1e24516:  if (r6 != r1) goto 1e24c70       ; drop
1e2451c:  r2 = r7 + 3 ; r1 = (r1 - 3) & 0xff ; r0 = b[r7+1]
1e24524:  call dispatch
```

### 2.1 Gates in `dispatch()` (0x1E22B36..0x1E22C16), confirmed

1. **Password lock.** When the lock flag `g+0x6F4` is 1 and the password has not been checked (`g+0xF`
   is 0), only these commands are accepted: `00`, `01`, `06`, `10`, `31`, `45`, `49`, `82`, `8F`. Everything
   else is dropped without a reply.
2. **Handshake.** The firmware drops every command except `00`, `01` and `7A` until handshake 2 succeeds
   (that sets `g+0xE = 1`), unless `g+0x10` is set. PROTOCOL.md marked this unverified.
   ```
   1e22b40: r1 = b[r15+16]; if (r1 != 0) goto ok        ; 7A bypass flag
   1e22b46: r1 = b[r15+14]; if (r1 != 0) goto ok        ; handshake done
   1e22b4c: if (r0 < 2) goto ok
   1e22b50: if (r0 != 122) goto exit                    ; 0x7A
   ```
3. **Power off.** While power (`g+0x5B5`, READ_PROPERTY byte 0) is 0, only these commands are accepted:
   `00 01 10 11 1F 20 23 2A 31 33 34 35 3D 41 42 49 8F`. The firmware tests them as two bitmasks:
   `0x0103101D` over `cmd-0x31` and `0x04098003` over `cmd-0x10`.

Unknown commands, and commands with no case in the table, end at `0x1E23AFA` and send **no reply**.

---

## 3. Command table (1.9.6)

The reply column shows the `A1` payload. `[1]` means the shared tail sends a 1-byte status of 1.
"Mode" is the display mode byte `g+0x5BE`, which READ_PROPERTY reports as byte 3. A handler that sets
it calls `mode_switch` at `0x1E1E01C`.

| Cmd | Name (app) | Handler | Payload as parsed | Reply | Notes |
|---|---|---|---|---|---|
| 00 | handshake 1 | 1E22CBC | memcmp(p, "CCHIP", 5) | `"CCHIP" c1 c2` (7) | `c1 = [g+0xA0] & 7`, `c2 = ([g+0xA0]/100) & 7`, saved at `g+0x11/0x12`. A mismatch gets no reply. The locked-mode variant is at 1E22BB6 and uses `g+0xC/0xD`. |
| 01 | handshake 2 | 1E22E60 | "CCHIP", `p[5]==c1^0x24`, `p[6]==c2^0x4C` | `"CCHIP" 00`, or `"CCHIP" 01` on a mismatch | Sets `g+0xE = 1` and logs "[COMMAND]shake success!" |
| 02 | CLEAR_DRAWING_BOARD | 1E22ECE | ignored | `[1]` | Switches to mode 7, then memsets the framebuffer `0xAF18` to 0 (w·h·4 bytes) |
| 03 | FILL_RECT | 1E22F0C | `r g b x1 y1 x2 y2`, all u8 | `[1]` | Mode 7. `fill_rect` at 1E20CD2 is inclusive and **does no bounds check** |
| 04 | DRAW_PIXEL | 1E22F52 | `r g b x y`, u8 | `[1]` | Mode 7. 1E20D08: `fb[(y*W + x)*4] = 0x00RRGGBB`, no bounds check |
| 05 | FILL_REGION_MASK | none | | none | No case, and not handled in the A2 path either. Matches property flag floodFill = 0 |
| 06 | ? | 1E22D4A | ignored | 2 bytes from `clk+6` | Not in the app. Allowed while locked. |
| 07 | SEND_FILE | 1E22F92 (single), 1E245F6 (A2 stream) | `name_len name data` | `[1]` | Streams to flash with 4 KiB erases |
| 08 | DELETE_FILE | 1E23016 | `name_len name` (name copied into a 20-byte buffer) | `[1]` | |
| 09 | ASK_FILE_EXIST | 1E2306A | `name_len name` | `[1]` exists, `[0]` not | Looks through 3 open-file slots at `g+0xDF8` |
| 0A | SHOW_TEXT | 1E230CE → `text_tlv` 1E20E5C | TLV, tags 01..10 (§5e) | `[ok]` | Mode 1 |
| 0B | SHOW_IMG | 1E230E4 | TLV: 01 name, 02 mode u8, 03 delay u16, 04 u8, 05 bytes → `g+0x4AC`, 06 u8 | `[ok]` | Mode 8. Logs "[STATIC_PIC]frame delay:%d" |
| 0C | SHOW_GIF | 1E231A6 | TLV: 01 name, 03 interval u16, 05 8-byte sync time | `[ok]` | Mode 2. Tags 02/04 are skipped |
| 0D | SHOW_CLOCK | 1E2324E → 1E20668 | `style 24h date r g b` (6 bytes, nothing checked) | `[1]` | Mode 4 (§5a) |
| 0E | STOPWATCH | 1E23270 | `op` 1..4 | 1: `[1 state h m s]`; 2 start: `[2 h m s]`; 3 pause: `[3 h m s]`; 4 reset: `[4 1]` | Mode 5 |
| 0F | PLAY_CAROUSEL | 1E232D0 → 1E21CA0 (single), A2 stream | list | `[1]` | Mode 3 |
| 10 | READ_PROPERTY | 1E1F786 | ignored | 39 bytes (§5e) | |
| 11 | SWITCH_LIGHT | 1E232F2 → 1E1E248 | `on` | `[1]` | |
| 13 | SEND_BRIGHTNESS | 1E23306 | `b` stored raw at `g+0x5C1` | `[1]` | §5e |
| 15 | SET_FLIP | 1E2331E | `f` stored raw at `g+0x5C7` | `[1]` | §5e |
| 17 | ? | 1E23336 | ignored | `[1]` | Ack only |
| 1A | MUSIC_RHYTHM | 1E23344 | `on [level]` | `[1]` | `on`≠0: `g+0x5C3=1`, `g+0x5BF=p[1]`; `on`=0: `g+0x5C3=0`. Switches to mode 6 either way unless it is already in mode 6 (then only `on`=1 re-enters it) |
| 1C | ? | 1E23362 | `p[0]==1` sets `g+0xB8`, otherwise calls 1E13C9A | `[1]` | Not in the app |
| 1D | ADD_TO_CAROUSEL | 1E23380 | `type(1..5) name_len(1..14) name` | `[ok]` | Larger values are rejected with `[0]` |
| 1E | DELETE_FROM_CAROUSEL | 1E233D4 | `name_len(1..14) name` | `[ok]` | |
| 1F | READ_CAROUSEL_LIST | 1E2346E → 1E22262 | | built inside | |
| 20 | READ_CAROUSEL_FILE_FOLDER | 1E23474 → 1E22350 | | built inside | |
| 21 | CLEAR_CAROUSEL | 1E2347A | | `[1]` | |
| 23 | READ_EXTERNAL_FLASH_SPACE | 1E2349E | | `u32 total = 0x680000`, `u32 free` | `free = 0x680000 - used_blocks*4096` |
| 24, 25, 26 | SET_PARTITION, ADD_PARTITION_TO_LOOP, (26 as a single frame) | none | | none | See §5b. 26 works only as an A2 stream |
| 27 | SET_SPLICE_PARAMS | 1E234C0 | `w h x y`, u16 LE | `[1]` | |
| 28 | ? | 1E23500 | `p[0]` → `g+0x2B6`; if it is 0, 6 more bytes → `g+0x2B7` | `[1]` | Not in the app |
| 29 | SHOW_EFFECT_TEXT | 1E23528 → 1E218F0 | | `[r]` | |
| 2A | ? | 1E2354A → 1E21BB6 | | built inside | |
| 30 | timer enable | 1E23554 | `idx en` | `[1]` | slot `g+0x6C2 + idx*10` |
| 31 | SYNC_TIME | 1E2357E → 1E20C1A | as in PROTOCOL.md | `[1]` | |
| 33 | delete timers | 1E23592 | list of idx | `[1]` | |
| 34 | set timer | 1E235BC | `idx` + 9 bytes | `[1]` | |
| 35 | read timers | 1E235E8 → 1E1F8A2 | | built inside | |
| 36 | COUNTDOWN | 1E235EE | `op` 1..5; 2 and 5 take `h m s` | `[op …]` | Mode 9 |
| 3D | READ_CAROUSEL_EXTEND_PARAM | 1E23614 → 1E22572 | | built inside | |
| 41 | GET_FIRMWARE_VERSION | 1E2361A | | `"1.9.6"` (5 ASCII bytes) | |
| 42 | GET_RESOURCE_VERSION | 1E23624 | | sprintf "%d.%d.%d" of the material header | |
| 43 | ? | 1E23648 | | `[1]` | Swaps the draw callback for 1E30C46 |
| 44 | SET_DEVICE_LOCK | 1E2366C | `1 len pwd…` / `0` | `[1]` | |
| 45 | SET_MULTI_PACK_V2 | 1E236A0 | `en n` | `[1]` | |
| 48 | SET_REMOTE | 1E236C6 | `1 x mode` / `2` (forget the learned remote) | `[1]` | |
| 49 | READ_REMOTE | 1E236F6 | | 8 bytes | |
| 4A | START_SYNC_TIME | 1E23728 | | `[1]` | Starts the `ts_sync` task (§5d) |
| 4B | SHOW_SPLICE_NUM | 1E23750 | `0` = restore; `1 len text` | `[1]` | |
| 4C | READ_COMMUNICATION_VERSION | none | | **none** | 0x4C > 0x4B, and the overflow chain checks only 7A/8E/8F/F1 |
| 51 | RHYTHMIC_SPECTRUM | BLE callback 1E346AA | 16 level bytes in `buf[3..18]`, scaled ×height/255 | none | Never reaches msg_task |
| 7A | ? | 1E2378E | `p[0]` → `g+0x10` | `[p0]` | Non-zero **bypasses the handshake gate**. Accepted before the handshake |
| 82 | CHECK_DEVICE_PWD | 1E22E2E | password | `[1]` match / `[0]` | |
| 8E | RESET_DEVICE | 1E2379C | `1` = 1E22624; `2` = format the file system, rebuild `/set`, then call RAM 0x010A | `[1]` | Guess: 0x010A is a reboot |
| 8F | SET_TIME_ZONE | 1E237BA | `std_h std_m dst [9 rule bytes]` | `[1]` (sent first) | `tz_min = std_h*60 + std_m` → `h[g+0x26E]` |
| F1 | ? | 1E22D3E | | `u32 [g+0xA0]` | Returns the seed of the handshake challenge (post-handshake only) |

Display modes (`mode_switch` at 1E1E01C, `tbh` cases 1..14): 1 text, 2 GIF, 3 carousel, 4 clock, 5 stopwatch,
6 music rhythm, 7 drawing board, 8 static image, 9 countdown, 12 and 14 internal (14 restores
"CT_STORE"). READ_PROPERTY mode 4 in the §7 capture of PROTOCOL.md means the clock.

---

## 4. Multi-packet (`A2`) streams

msg_task (0x1E24540..) checks session, total and index. When a stream completes, it handles only these
inner commands: **`07`** (SEND_FILE), **`26`** (DRAW_EDIT_DIY_PIC: palette → framebuffer, mode 7), **`0F`**
(PLAY_CAROUSEL) and **`0A`** (SHOW_TEXT). Any other inner command, including `05`, `24` and `29`, is dropped
with no reply (`1E249A8`: `if (r0 == 15) …; if (r0 != 10) goto end`). An out-of-order packet makes the device
send a 21-byte `A4` frame (0x1E24760): byte 9 = 1, bytes 10–11 = the expected 1-based index (u16 LE), and a
CRC over bytes 9..18 at bytes 19–20. That matches the app's A4 parser in PROTOCOL.md §3.3.

---

## 5. Answers

### a. SHOW_CLOCK (0x0D)

**Confirmed: the payload is stored with no validation.** 0x1E20668:

```
r1=b[r0+0] -> b[clk+12]  style      r1=b[r0+3] -> b[clk+10]  r
r1=b[r0+1] -> b[clk+13]  24h        r1=b[r0+4] -> b[clk+9]   g
r1=b[r0+2] -> b[clk+14]  date       r0=b[r0+5] -> b[clk+8]   b
```

The dispatcher then sets mode 4. `mode_switch` case 4 (0x1E1E09A) clears the phase bytes and sets the
first countdown `h[g+0x6C] = 250`. It also clears the two 2 KiB frame buffers and installs the draw callback
`clock_tick` at 0x1E1FDAA.

**Date/time alternation: confirmed. No payload byte or setting changes the timing; only `date` enables it.**
0x1E20590 runs once per clock frame:

```
1e20590: h[g+108] -= 1
1e2059a: if ((h[g+108] & 0x8000) == 0) goto done      ; not expired yet
1e2059e: r0 = b[clk+14] (date flag) ; h[g+108] = 300
1e205aa: if (r0 != 1) goto done                       ; date off: stay on the time
1e205bc: if (phase != 1) -> phase = 1, open style GIF  ; back to the time, 300 frames
         else           -> h[g+108] = 100, phase = 2, open "dat" GIF ([g+0x210], h[g+0x214]) ; date, 100 frames
```

The time shows for **300 clock frames** (250 the first time), then the date for **100**. `clock_tick`
renders only every second display-loop pass (`b[0xCE3A]` counter, 0x1E1FDB4). The display task
(0x1E290EA..0x1E292E0) runs the callback, then `os_time_dly(1)`, and logs "[FRAME]frame rate: %d fps" every
100 passes. So the period depends on the frame rate, not wall-clock time.

**Guess:** the hardware showed about 5 s of date every 10–15 s. That fits about 40–50 ms per clock frame
(about 20–25 ms per display pass). The UART log line "[FRAME]frame rate" would pin it down.

**Styles → GIFs: confirmed.** `clock_tick` (0x1E1FDF4 and 0x1E20610):

```
r0 = b[clk+12] * 12 + g
r1 = [r0 + 858*4]      ; = g + 0xD68 + style*12  -> u32 offset
r0 = h[r0 + 1718*2]    ; = g + 0xD6C + style*12  -> u16 length
call gif_select(3); call gif_open(&{off,len}, 0)   ; on failure logs "[CLOCK_VIEW]open gif error!"
```

`g + 0xD64` is the array of `tim` records that the material-pack parser fills (§5f). Style N is the N-th
`tim` record in the pack, in file order. In material 1.2.1 those sit at pack offsets `0x3C4D, 0x3EE2, 0x4811,
0x5126, 0x580C, 0x58FC, 0x5DE0, 0x692B, 0x6ABC, 0x6CF2` (record header; the GIF follows 10 bytes later).
The date view uses the single `dat` record (`0x6EA1`).

Where the digits go is a separate table, per style, at flash `0x1E3C705`, two bytes each (column, row)
(0x1E1FF06–0x1E1FF26):

| style | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| (col,row) | 1,1 | 1,1 | 1,1 | 1,1 | 1,3 | 1,1 | 1,1 | 1,5 | 1,1 | 1,1 |

**Style 10: confirmed, there is no bounds check anywhere.** That explains the blank screen, and it is also a memory bug:

* The GIF entry is read from `g + 0xD68 + 120`, past the 10 `tim` entries. That memory is the SEND_FILE
  state (`g + 0xDDC`), so the GIF open gets garbage. That fails, or worse.
* The digit origin is read past the table, from the next string's bytes `76 62` ("vb" of
  `"vbg_adc_value"`): column 118, row 98. Digits are written to `fb + (98*32 + 118)*4`, about 13 KB past
  the 2 KB framebuffer at `0xAF18`. That is **out-of-bounds writes into other RAM** on every frame.
  Nothing shows because nothing lands in the visible buffer. **Don't send styles > 9.** Guess: a panel
  left on style 10 can crash or corrupt state.

**12-hour mode: confirmed** at 0x1E1FED6: `hour = hour % 12` when `24h != 1`. So 12:xx shows as `00:xx`.
**Guess**, not checked on the panel: noon and midnight display as `00` in 12 h mode.

### b. SET_PARTITION (0x24)

**Confirmed: not implemented in 1.9.6.** Cases 0x24 and 0x25 in the dispatcher's `tbh` table point at the
default exit 0x1E23AFA (no reply). The A2 stream path handles only 07/0A/0F/26 (§4). No other compare
against 0x24 exists in the protocol code; the only other one, at 0x1E0DC18, is in the BLE stack. So the
firmware gives no zone content types to recover. READ_PROPERTY still hardcodes the `supportMultiScreen`
bit (§5e).

**Guess:** the app's partition feature (types 2/3/6/7) targets other models, or firmware other than this
1.9.6. Whether 1.8.5, which runs on the panel, has a handler is unknown.

### c. Drawing board (02/03/04)

**Confirmed: they switch to their own mode (7). They do not draw over the GIF or clock.** Each handler starts with

```
if (b[g+0x5BE] != 7) { b[g+0x5BE] = 7; mode_switch(); }
```

`mode_switch` case 7 (0x1E1E0FC) sets `b[g+0x19C] = 0` and installs the callback 0x1E28B4E. On its first
run that callback memsets the framebuffer `0xAF18` to black and sets the flag (0x1E28B7E..0x1E28B9E). Later
runs only request a refresh every 6th pass. The GIF/clock callback is replaced, so the previous content
stops and is wiped.

**Guess (race):** the handler writes its pixel/rect into the framebuffer right after `mode_switch`, in
msg_task. The first-pass clear runs later, in the display task. So the first DRAW_PIXEL/FILL_RECT after
another mode can be erased. Send `02` first, wait for its ack and a frame, then draw.

Pixel format: `u32 0x00RRGGBB` at `0xAF18 + (y*W + x)*4`, with `W = b[g+0x5B7]` (panel width). x and y are
u8 on this firmware; the app's u16-x variant for wide panels is not parsed here.

### d. `a9 01 04 01`

**Confirmed: it is the device's time request, sent once by `START_SYNC_TIME`.** `4A` starts a task
named `ts_sync` (0x1E23728: `os_task_create(0x1E300FE, 0, prio 3, stack 128, 0, "ts_sync")`). That task
(0x1E300FE):

```
1e30128: r4 = g + 7160 (tx buffer)
1e3012c: b[r4+0] = 0xA9 ; b[r4+1] = 1 ; b[r4+2] = 4 ; b[r4+3] = 1
1e3013c: crc = crc16_modbus(r4, 4) ; b[r4+4] = lo ; b[r4+5] = hi
1e30148: send(6)
1e3014c: d[g+504] = now_ms + timer_frac        ; remembers when it asked
1e30178: tail-calls task_kill("ts_sync")
```

`A9`, cmd `01`, len 4, payload `01` are all constants. The app's answer is
`AA 01 0B <unix_ms u64 LE> crc`. The BLE callback handles it at 0x1E34584 and **does not check the CRC or
the length**. It reads `unix_ms` from bytes 3..10, adds half the round trip (`(now - d[g+504]) >> 1`), stores
the result in `d[g+464]`, and calls `set_rtc_ms` at 0x1E20AE2. No reply.

### e. Value ranges

**Brightness (`13`): confirmed.** It is stored raw (u8) and READ_PROPERTY byte 6 echoes it. Both LED
output paths compute `level = 20 + min(b,100)*4/5` (0x1E28686 and 0x1E288F8):

```
1e2868a: if (r1 >= 100) r1 = 100
1e28690: r1 = r1 << 2 ; r1 = r1 / 5 ; r5 = r1 + 20
```

So 0 is not off: it gives the 20 % floor. Values above 100 act like 100. The IR remote steps it ±10 within
10..100 (0x1E1ED84 / 0x1E1EDA6). Sending 101..255 is pointless, and the CLI currently allows 1–255.

**SET_FLIP (`15`): partly.** The byte is stored raw at `g+0x5C7`. READ_PROPERTY byte 7 echoes it, it is
saved to `/set` (0x1E1E90A), and the IR remote **toggles it between 0 and 1** (0x1E1ED96). I found no
renderer that reads it. The search covered immediate-offset and `g+0x5BE`-relative reads, so it was not
exhaustive. **Guess:** 0 means normal and 1 means flipped; other values are undefined. 1.9.6 also sets
`horizontalReverse` (property byte 10 b3), but where that is applied was not found.

**SHOW_TEXT: confirmed for parsing; consumers partly traced.** `text_tlv` (0x1E20E5C) is a `tbb` over tags 1..16
and stores every value raw, with no clamping:

| Tag | Stored at | Notes |
|---|---|---|
| 01 | g+0x80D (+ flag 0x80C) | rainbow / colour scheme |
| 02 | g+0x24E4 | colour runs, u16 length |
| 03 | g+0x812 | |
| 04 | g+0x813 | |
| 05 | g+0x814 → render copy g+0x751 | **mode: 13-way switch 0..12** at 0x1E2BF54 (`if (r0 > 12) goto default`), second switch 1..11 at 0x1E2FBC4. The IR remote sets 1 or 2. PROTOCOL.md calls this "font mode, 2 = RTL" (from the app); the firmware treats it as a display/animation mode selector. |
| 06 | g+0x815 → g+0x752 | **speed: per frame `acc += speed`, `step = acc / 25` px** (0x1E2D88A, 0x1E2DB1C, 0x1E2E2D8). 0 does not move, 25 is 1 px/frame, any u8 is accepted. One path uses `51 - speed` as a hold threshold (0x1E2D420). |
| 07 | g+0x816 | |
| 08 | g+0x819 (+ border-on flag 0x818) | |
| 09, 0A | g+0x81A, g+0x81B | |
| 0B | g+0x81C | file name, copied with the length byte into a 20-byte field (**no length check**) |
| 0C | g+0x817 | |
| 0D | g+0x73C (+ flag 0x738) | copied with the length byte; used as an 8-byte start timestamp (0x1E1CE98), like GIF tag 05 |
| 0E | g+0x9D8 | |
| 0F | g+0x83C.. | background RGB |
| 10 | g+0x844 | alignment (only when the length is non-zero) |

**Guess:** speed acts on frames, not milliseconds, so the real speed depends on the frame rate, as with the
clock in (a).

**PNG compressed IDAT: confirmed that the code is present.** The decoder is LodePNG (its error strings are
at 0x1E412E2..0x1E424D5). It includes the full inflater. At 0x1E18C72 the block-type switch handles BTYPE 0
(stored), 1 (fixed) and 2 (dynamic) and rejects 3. It indexes LENGTHBASE/LENGTHEXTRA/DISTANCEBASE/DISTANCEEXTRA
at 0x1E40854.. (`r5 + 1412/1528/1644/1764` from base 0x1E402D0). Compressed IDAT should decode. Not tested on
the panel; the decoder needs heap for the inflated scanlines.

### f. Material pack and OTA (read only)

**Pack parser: confirmed**, at 0x1E37DE0..0x1E38500 (`r10 = g`, `r8 = 0x1E3C040`):

* The header is checked only with `memcmp(hdr, "lib", 3)`. Bytes 3..5 are the version (`01 02 01` = 1.2.1).
  They are logged, and `GET_RESOURCE_VERSION` returns them (`g+0x37..0x39`).
* The records are chained: `[id u8][tag 3][offset u32][len u16]`, then the payload. Next record = this record + 10 + `len`.
  The parser dispatches on the id byte and then confirms the tag with memcmp:

  | id | tag | stored in | count byte |
  |---|---|---|---|
  | 0x67 | `spl` | g+0x200 | g+1 |
  | 0x83 | `tim` | **g+0xD64 + n*12** (clock styles) | g+2 |
  | 0x92 | `dat` | g+0x20C (date view) | g+3 |
  | 0xCE | `sto` | g+0x218 | g+4 |
  | 0x3D | `cou` | g+0x224 | g+5 |
  | 0x5F | `mar` | g+0x149C + n*12 | g+6 |
  | 0x63 | `mag` | g+0x1A18 + n*12 | g+7 |
  | 0x37 | `tip` | g+0x7D0 + n*12 | g+8 |
  | 0x58 | `rem` | g+0x13AC + n*12 | g+9 |
  | 0x61 | `def` | g+0x230 | g+10 |
  | 0x62 | `cbg` | parsed by 1E37D2A | |
  | 0x43 | `C` + layout `4x2`/`1x6`/`1x4`/`1x2` | resets the counters | |

* **No checksum or CRC is verified in the parser.** This is a bounded claim covering this function only.
  The array counters are also unbounded: a pack with more than 10 `tim` records would overwrite the
  SEND_FILE state at g+0xDDC.

**OTA receive path:** not traced. The firmware contains JieLi's `update` / `sdfile` code (strings `update`,
`res.bin`, `sdfile`), but this protocol's dispatcher has no OTA command, so OTA probably goes over JieLi's
own service or another path. Unknown.

---

## 6. Still unknown

* The panel runs **1.8.5**; all of this is from 1.9.6. Known difference: READ_PROPERTY byte 10 is `0xEA` in
  1.9.6 (splice, horizontalReverse, carouselSwitchAnim, textBgColor, textAlign) but was `0x00` on the 1.8.5
  panel. Byte 11 is `0x01` (effectText). 1.9.6 builds 39 reply bytes; 40 were captured on 1.8.5. Bytes 8 bits 4–7
  (remote, noRespA4, multiScreen, 600Txt) are hard-coded on.
* The display frame rate, which sets the clock alternation and text speed in seconds.
* Where flip and horizontal-reverse are applied in rendering.
* Commands not in the app: `06`, `17`, `1C`, `28`, `2A`, `43`, `7A` (handshake bypass), `F1` (challenge seed).
* What `[g+0xA0]` is (the handshake challenge seed).
* What text tags 03/04/07/09/0A/0C/0E mean in rendering. Only their storage was traced.
* The SET_PARTITION protocol (no handler in 1.9.6).
* The OTA path, and whether OTA flashing checks the MD5/CRC.
