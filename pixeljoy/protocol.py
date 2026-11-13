"""Pure (transport-free) encoding/decoding for the PixelJoy BLE LED panel protocol.

See docs/PROTOCOL.md for the specification and where each piece was found
in the decompiled vendor app.
"""

from __future__ import annotations

import datetime as _dt
import functools
import json
import random
import struct
import zlib
from dataclasses import dataclass, field
from importlib import resources
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEAD_APP = 0xA0  # app -> device command
HEAD_DEV = 0xA1  # device -> app reply
HEAD_MULTI = 0xA2  # app -> device multi-packet chunk
HEAD_MULTI_ACK = 0xA3  # device -> app: send next group
HEAD_MULTI_ACK_V2 = 0xA4  # device -> app: status / lost packet
HEAD_GET_TIME = 0xA9  # device -> app: please send time
HEAD_SEND_TIME = 0xAA  # app -> device: time reply

SINGLE_PACK_MAX = 247  # max payload the app puts into one A0 frame
MULTI_HEADER_OVERHEAD = 11  # A2 header (9) + chunk CRC (2)

HANDSHAKE_MAGIC = b"CCHIP"


class Cmd:
    SHAKE_HAND_FIRST = 0x00
    SHAKE_HAND_SECOND = 0x01
    CLEAR_DRAWING_BOARD = 0x02
    FILL_RECT = 0x03
    DRAW_PIXEL = 0x04
    FILL_REGION_MASK = 0x05
    SEND_FILE = 0x07
    DELETE_FILE = 0x08
    ASK_FILE_EXIST = 0x09
    SHOW_TEXT = 0x0A
    SHOW_IMG = 0x0B
    SHOW_GIF = 0x0C
    SHOW_CLOCK = 0x0D
    STOPWATCH = 0x0E
    PLAY_CAROUSEL = 0x0F
    READ_PROPERTY = 0x10
    SWITCH_LIGHT = 0x11
    SEND_BRIGHTNESS = 0x13
    SET_FLIP = 0x15
    MUSIC_RHYTHM = 0x1A
    ADD_TO_CAROUSEL = 0x1D
    DELETE_FROM_CAROUSEL = 0x1E
    READ_CAROUSEL_LIST = 0x1F
    READ_CAROUSEL_FILE_FOLDER = 0x20
    CLEAR_CAROUSEL = 0x21
    READ_EXTERNAL_FLASH_SPACE = 0x23
    SET_PARTITION = 0x24
    ADD_PARTITION_TO_LOOP = 0x25
    DRAW_EDIT_DIY_PIC = 0x26
    SET_SPLICE_PARAMS = 0x27
    SHOW_EFFECT_TEXT = 0x29
    ENABLE_DISABLE_TIMED = 0x30
    SYNC_TIME = 0x31
    DELETE_TIMER = 0x33
    SET_TIMER_SWITCH = 0x34
    TIMER = 0x35
    COUNTDOWN = 0x36
    READ_CAROUSEL_EXTEND_PARAM = 0x3D
    GET_FIRMWARE_VERSION = 0x41
    GET_RESOURCE_VERSION = 0x42
    SET_DEVICE_LOCK = 0x44
    SET_MULTI_PACK_V2 = 0x45
    SET_REMOTE = 0x48
    READ_REMOTE = 0x49
    START_SYNC_TIME = 0x4A
    SHOW_SPLICE_NUM = 0x4B
    READ_COMMUNICATION_VERSION = 0x4C
    RHYTHMIC_SPECTRUM = 0x51
    CHECK_DEVICE_PWD = 0x82
    RESET_DEVICE = 0x8E
    SET_TIME_ZONE = 0x8F


# ---------------------------------------------------------------------------
# CRC / framing
# ---------------------------------------------------------------------------


def crc16_modbus(data: bytes | bytearray | Iterable[int]) -> int:
    """CRC-16/MODBUS (poly 0xA001 reflected, init 0xFFFF). check("123456789") == 0x4B37."""
    crc = 0xFFFF
    for b in data:
        crc ^= b & 0xFF
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame(cmd: int, payload: bytes = b"", head: int = HEAD_APP) -> bytes:
    """Build a short frame: head | cmd | len | payload | crc16 (LE)."""
    payload = bytes(payload)
    if len(payload) + 3 > 0xFF:
        raise ValueError(f"payload too large for a single frame ({len(payload)} bytes); use multi-pack")
    body = bytes([head, cmd & 0xFF, len(payload) + 3]) + payload
    return body + struct.pack("<H", crc16_modbus(body))


def _lv(name: str) -> bytes:
    raw = name.encode("utf-8")
    if len(raw) > 0xFF:
        raise ValueError("name too long")
    return bytes([len(raw)]) + raw


@dataclass
class Reply:
    head: int
    cmd: int
    payload: bytes
    raw: bytes
    crc_ok: bool = True


class FrameError(ValueError):
    pass


def parse_reply(data: bytes) -> Reply:
    """Parse one notification from the device.

    A1 frames are fully validated (length + CRC). A3/A4/A9 are returned with
    their raw bytes; the vendor app's CRC checks on those look buggy, so we
    report ``crc_ok`` but do not reject them.
    """
    data = bytes(data)
    if len(data) <= 2:
        raise FrameError("too short")
    head = data[0]
    if head == HEAD_DEV:
        if len(data) < 5 or data[2] != len(data) - 2:
            raise FrameError(f"bad length byte in {data.hex(' ')}")
        crc = struct.unpack("<H", data[-2:])[0]
        if crc != crc16_modbus(data[:-2]):
            raise FrameError(f"bad CRC in {data.hex(' ')}")
        return Reply(head, data[1], data[3:-2], data)
    if head in (HEAD_MULTI_ACK, HEAD_MULTI_ACK_V2, HEAD_GET_TIME):
        return Reply(head, data[1], data[2:], data)
    raise FrameError(f"unknown head 0x{head:02X} in {data.hex(' ')}")


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------


def handshake_first() -> bytes:
    return frame(Cmd.SHAKE_HAND_FIRST, HANDSHAKE_MAGIC)


def handshake_second(first_reply_payload: bytes) -> bytes:
    """Answer the device's challenge: bytes [5] and [6] of the first reply's payload."""
    if len(first_reply_payload) < 7:
        raise FrameError(f"handshake reply too short: {first_reply_payload.hex(' ')}")
    x, y = first_reply_payload[5], first_reply_payload[6]
    return frame(Cmd.SHAKE_HAND_SECOND, HANDSHAKE_MAGIC + bytes([x ^ 0x24, y ^ 0x4C]))


def handshake_second_ok(second_reply_payload: bytes) -> bool:
    return len(second_reply_payload) > 5 and second_reply_payload[5] == 0


# ---------------------------------------------------------------------------
# Multi-packet (A2)
# ---------------------------------------------------------------------------


def multi_stream(cmd: int, data: bytes) -> bytes:
    return bytes([cmd & 0xFF]) + struct.pack("<I", len(data)) + bytes(data)


def multi_packets(cmd: int, data: bytes, split: int = SINGLE_PACK_MAX, session: int | None = None) -> list[bytes]:
    """Wrap ``cmd``+``data`` into A2 chunks.

    ``split`` is the per-write size the app uses: min(mtu-3, 509) for SEND_FILE,
    min(mtu-3, 247) otherwise.
    """
    stream = multi_stream(cmd, data)
    chunk = split - MULTI_HEADER_OVERHEAD
    if chunk <= 0:
        raise ValueError("split too small")
    total = (len(stream) + chunk - 1) // chunk
    if session is None:
        session = random.randrange(500)
    out = []
    for i in range(total):
        part = stream[i * chunk : (i + 1) * chunk]
        hdr = struct.pack("<BHHHH", HEAD_MULTI, session, total, i + 1, len(part))
        out.append(hdr + part + struct.pack("<H", crc16_modbus(part)))
    return out


@dataclass
class MultiAck:
    """Interpretation of an A3/A4 notification during a multi-packet transfer."""

    next_group: bool
    resend_from: int | None = None  # 1-based packet index


def parse_multi_ack(reply: Reply) -> MultiAck:
    raw = reply.raw
    if reply.head == HEAD_MULTI_ACK:
        return MultiAck(next_group=True)
    if reply.head == HEAD_MULTI_ACK_V2 and len(raw) >= 12 and raw[9] == 1:
        idx = raw[10] | (raw[11] << 8)
        if idx > 0:
            return MultiAck(next_group=False, resend_from=idx)
    return MultiAck(next_group=True)


# ---------------------------------------------------------------------------
# Simple commands
# ---------------------------------------------------------------------------


def read_property() -> bytes:
    return frame(Cmd.READ_PROPERTY, b"\x01")


CLOCK_STYLES = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def show_clock(style: int = 0, h24: bool = True, date: bool = False, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """Firmware-drawn clock (ClockActivity.buildClockDisplayData). ``style`` is 0-based (0-9)."""
    # Firmware has no bounds check: style >= 10 writes digits outside the framebuffer (docs/FIRMWARE.md §5a)
    if not 0 <= style < len(CLOCK_STYLES):
        raise ValueError(f"clock style {style} out of range 0-{len(CLOCK_STYLES) - 1}")
    return frame(Cmd.SHOW_CLOCK, bytes([style, int(h24), int(date), *color]))


def switch_light(on: bool) -> bytes:
    return frame(Cmd.SWITCH_LIGHT, bytes([1 if on else 0]))


def set_brightness(level: int) -> bytes:
    return frame(Cmd.SEND_BRIGHTNESS, bytes([max(1, min(100, level))]))  # percent; app slider max is 100


def set_flip(flip_type: int) -> bytes:
    return frame(Cmd.SET_FLIP, bytes([flip_type & 0xFF]))


def set_multi_pack_v2(enable: bool, n: int) -> bytes:
    return frame(Cmd.SET_MULTI_PACK_V2, bytes([1 if enable else 0, n & 0xFF]))


def get_firmware_version() -> bytes:
    return frame(Cmd.GET_FIRMWARE_VERSION, b"\x01")


def get_resource_version() -> bytes:
    return frame(Cmd.GET_RESOURCE_VERSION, b"\x01")


def read_communication_version() -> bytes:
    return frame(Cmd.READ_COMMUNICATION_VERSION, b"\x01")


def read_flash_space() -> bytes:
    return frame(Cmd.READ_EXTERNAL_FLASH_SPACE, b"\x01")


def clear_drawing_board() -> bytes:
    return frame(Cmd.CLEAR_DRAWING_BOARD, b"\x01")


def _x(x: int, wide: bool) -> bytes:
    return struct.pack("<H", x) if wide else bytes([x & 0xFF])


def draw_pixel(r: int, g: int, b: int, x: int, y: int, panel_width: int = 32) -> bytes:
    wide = panel_width > 256
    return frame(Cmd.DRAW_PIXEL, bytes([r, g, b]) + _x(x, wide) + bytes([y]))


def fill_rect(r: int, g: int, b: int, x1: int, y1: int, x2: int, y2: int, panel_width: int = 32) -> bytes:
    wide = panel_width > 256
    return frame(Cmd.FILL_RECT, bytes([r, g, b]) + _x(x1, wide) + bytes([y1]) + _x(x2, wide) + bytes([y2]))


def delete_file(name: str) -> bytes:
    return frame(Cmd.DELETE_FILE, _lv(name))


def ask_file_exist(name: str) -> bytes:
    return frame(Cmd.ASK_FILE_EXIST, _lv(name))


def reset_file_system() -> bytes:
    return frame(Cmd.RESET_DEVICE, b"\x02")


def sync_time(now: _dt.datetime | None = None) -> bytes:
    now = now or _dt.datetime.now()
    unix_ms = int(now.timestamp() * 1000)
    payload = struct.pack(">H", now.year) + bytes(
        [now.month, now.day, now.hour, now.minute, now.second, now.weekday()]  # Mon=0..Sun=6
    ) + struct.pack("<Q", unix_ms)
    return frame(Cmd.SYNC_TIME, payload)


def start_sync_time() -> bytes:
    return frame(Cmd.START_SYNC_TIME, b"\x01")


def _java_div(a: int, b: int) -> int:
    """Java int division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _java_rem(a: int, b: int) -> int:
    return a - _java_div(a, b) * b


def _find_transitions(tz, year: int):
    """(dst_start, dst_end) as (local_dt_after, offset_before_s, offset_after_s), like DaylightSavingUtil."""
    utc = _dt.timezone.utc
    t = _dt.datetime(year, 1, 1, tzinfo=tz).astimezone(utc)
    end = _dt.datetime(year + 1, 1, 1, tzinfo=tz).astimezone(utc)
    off = lambda u: int(u.astimezone(tz).utcoffset().total_seconds())  # noqa: E731
    start_tr = end_tr = None
    prev = off(t)
    step = _dt.timedelta(hours=1)
    while t < end:
        nxt = t + step
        o = off(nxt)
        if o != prev:
            lo, hi = t, nxt  # binary search to the second
            while hi - lo > _dt.timedelta(seconds=1):
                mid = lo + (hi - lo) / 2
                if off(mid) == prev:
                    lo = mid
                else:
                    hi = mid
            inst = hi.replace(microsecond=0)
            # Java: transition.getDateTimeAfter() = instant expressed in the *after* offset
            local_after = (inst + _dt.timedelta(seconds=o)).replace(tzinfo=None)
            if o > prev:
                start_tr = (local_after, prev, o)
            else:
                end_tr = (local_after, prev, o)
            prev = o
        t = nxt
    return start_tr, end_tr


def _protocol_rule(month: int, week: int, dow: int, hour: int, shift: int, is_dst: bool) -> tuple[int, int, int, int]:
    """DaylightSavingUtil.getProtocolInfo: shift hour by +-dst offset with day/week wrap."""
    if is_dst and shift != 0:
        hour += shift
        if hour >= 24:
            hour -= 24
            if dow == 7:
                week, dow = week + 1, 1
            else:
                dow += 1
        elif hour < 0:
            hour += 24
            if dow == 1:
                week, dow = week - 1, 7
            else:
                dow -= 1
    return month, week, dow, hour


def time_zone_payload(tz_name: str | None = None, now: _dt.datetime | None = None) -> bytes:
    """SET_TIME_ZONE (0x8F) payload, replicating DaylightSavingUtil + DeviceCommand.setTimeZone.

    std_h std_m is_dst | s_mon s_week s_dow s_hour | e_mon e_week e_dow e_hour | dst_h
    week = ISO aligned week of month ((day-1)//7+1), dow Mon=1..Sun=7, hours are the
    wall-clock time just before each transition.
    """
    from zoneinfo import ZoneInfo

    if tz_name is None:
        tz_name = _local_tz_name()
    tz = ZoneInfo(tz_name)
    now = now.astimezone(tz) if now else _dt.datetime.now(tz)
    jan1 = _dt.datetime(now.year, 1, 1, tzinfo=tz)
    std = int((jan1.utcoffset() - (jan1.dst() or _dt.timedelta())).total_seconds())
    std_h, std_m = _java_div(std, 3600), _java_div(_java_rem(std, 3600), 60)
    start, end = _find_transitions(tz, now.year)
    is_dst = start is not None and end is not None
    dst_h = _java_div(start[2] - start[1], 3600) if start else 0

    def rule(tr):
        if tr is None:
            return 0, 0, 0, 0
        d = tr[0]
        return d.month, (d.day - 1) // 7 + 1, d.isoweekday(), d.hour

    s = _protocol_rule(*rule(start), -dst_h, is_dst)
    e = _protocol_rule(*rule(end), dst_h, is_dst)
    vals = [std_h, std_m, 1 if is_dst else 0,
            s[0], s[1], s[2], s[3] if is_dst else 0,
            e[0], e[1], e[2], e[3] if is_dst else 0,
            dst_h]
    return bytes(v & 0xFF for v in vals)


def set_time_zone(tz_name: str | None = None, now: _dt.datetime | None = None) -> bytes:
    return frame(Cmd.SET_TIME_ZONE, time_zone_payload(tz_name, now))


def _local_tz_name() -> str:
    import os

    if os.environ.get("TZ"):
        return os.environ["TZ"]
    try:  # macOS / most Linux: /etc/localtime -> .../zoneinfo/Area/City
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def time_reply(unix_ms: int | None = None) -> bytes:
    """Response to an A9 'get time' request from the device."""
    if unix_ms is None:
        unix_ms = int(_dt.datetime.now().timestamp() * 1000)
    return frame(0x01, struct.pack("<Q", unix_ms), head=HEAD_SEND_TIME)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def send_file_payload(name: str, data: bytes) -> bytes:
    return _lv(name) + bytes(data)


def send_file_frames(name: str, data: bytes, mtu: int = 512) -> tuple[bool, list[bytes]]:
    """Return (is_multi, frames) for uploading a file, following the app's rules."""
    payload = send_file_payload(name, data)
    write_split = min(mtu - 3, 509)
    if len(payload) <= min(write_split, SINGLE_PACK_MAX):
        return False, [frame(Cmd.SEND_FILE, payload)]
    return True, multi_packets(Cmd.SEND_FILE, payload, split=write_split)


def file_name(kind: str) -> str:
    """App-style file names: txt/img/dyn/CT + 6 digits."""
    prefix = {"text": "txt", "img": "img", "gif": "dyn", "effect": "CT"}[kind]
    return f"{prefix}{random.randrange(1_000_000):06d}"


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


@dataclass
class Glyph:
    """One rendered character: rows[y][x] truthy = lit."""

    rows: Sequence[Sequence[int]]

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    @property
    def height(self) -> int:
        return len(self.rows)


def pack_1bpp(rows: Sequence[Sequence[int]]) -> bytes:
    """Row-major, MSB = leftmost pixel, each row padded to a whole byte."""
    out = bytearray()
    for row in rows:
        w = len(row)
        for bx in range(0, w, 8):
            b = 0
            for i in range(8):
                if bx + i < w and row[bx + i]:
                    b |= 1 << (7 - i)
            out.append(b)
    return bytes(out)


def text_file(glyphs: Sequence[Glyph], support_600_txt: bool = False) -> bytes:
    count = b"\x01\x00" if support_600_txt else b"\x01"
    out = bytearray()
    for g in glyphs:
        out += bytes([0x01, g.width, g.height]) + count + pack_1bpp(g.rows)
    return bytes(out)


@dataclass
class TextStyle:
    color: tuple[int, int, int] = (255, 255, 255)
    rainbow: int | None = None  # tag 01: iridescent scheme index; overrides color
    rotate: int = 0
    spacing: int = 1
    font_mode: int = 0
    speed: int = 50
    moving_spacing: int = 0
    border_type: int = 0
    border_effect: int = 0
    border_speed: int = 0
    bg_color: tuple[int, int, int] | None = None  # tag 0F, only if supportTextBgColor
    align: int | None = None  # tag 10, only if supportTextAlign
    color_runs: list[tuple[int, int, tuple[int, int, int]]] = field(default_factory=list)


def show_text_payload(file: str, n_chars: int, style: TextStyle = TextStyle(), support_600_txt: bool = False) -> bytes:
    p = bytearray()
    if style.rainbow is not None:
        p += bytes([0x01, 0x01, style.rainbow & 0xFF])
    else:
        runs = style.color_runs or [(0, max(0, n_chars - 1), style.color)]
        body = bytearray()
        for start, end, (r, g, b) in runs:
            if support_600_txt:
                body += struct.pack("<HH", start, end)
            else:
                body += bytes([start & 0xFF, end & 0xFF])
            body += bytes([r, g, b])
        p += b"\x02" + struct.pack("<H", len(body)) + body
    p += bytes([0x03, 1, style.rotate, 0x04, 1, style.spacing, 0x05, 1, style.font_mode,
                0x06, 1, style.speed, 0x07, 1, style.moving_spacing,
                0x08, 1, style.border_type, 0x09, 1, style.border_effect, 0x0A, 1, style.border_speed])
    p += b"\x0b" + _lv(file)
    if style.bg_color is not None:
        p += bytes([0x0F, 3, *style.bg_color])
    if style.align is not None:
        p += bytes([0x10, 1, style.align])
    return bytes(p)


def cmd_frames(cmd: int, payload: bytes, mtu: int = 512) -> tuple[bool, list[bytes]]:
    """Single A0 frame if it fits (app rule), else A2 multi-pack."""
    split = min(mtu - 3, SINGLE_PACK_MAX)
    if len(payload) <= split:
        return False, [frame(cmd, payload)]
    return True, multi_packets(cmd, payload, split=split)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def show_img_payload(name: str, mode: int = 0, interval: int = 0) -> bytes:
    return b"\x01" + _lv(name) + bytes([0x02, 0x01, mode & 0xFF, 0x03, 0x02]) + struct.pack("<H", interval)


def show_gif_payload(name: str, speed: int = 600, sync_unix_ms: int = 0) -> bytes:
    p = b"\x01" + _lv(name) + bytes([0x03, 0x02]) + struct.pack("<H", max(0, 1050 - speed))
    if sync_unix_ms > 0:
        p += bytes([0x05, 0x08]) + struct.pack("<Q", sync_unix_ms)
    return p


def simple_png(pixels: Sequence[Sequence[tuple[int, int, int]]]) -> bytes:
    """8-bit RGB PNG, filter 0, zlib level 0 (same shape as the app's saveAsSimplePng)."""
    h = len(pixels)
    w = len(pixels[0]) if h else 0

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b in row:
            raw += bytes([r, g, b])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 0)) + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


@dataclass
class Properties:
    power: bool
    mode: int
    brightness: int
    vertical_flip: bool
    support_lock: bool
    locked: bool
    support_pack_v2: bool
    pack_v2_set: bool
    support_set_remote: bool
    support_no_response_a4: bool
    support_multi_screen: bool
    support_600_txt: bool
    pack_v2_num: int
    support_splice: bool
    support_horizontal_reverse: bool
    support_carousel_switch_anim: bool
    support_text_bg_color: bool
    support_text_align: bool
    support_effect_text: bool
    support_flood_fill: bool
    raw: bytes

    @property
    def preferred_pack_v2_num(self) -> int:
        if not self.support_pack_v2:
            return 1
        return 200 if self.support_no_response_a4 else 4


def parse_properties(p: bytes) -> Properties:
    if len(p) < 11:
        raise FrameError(f"property reply too short: {p.hex(' ')}")
    bit = lambda v, i: bool((v >> i) & 1)  # noqa: E731
    f1, f2 = p[8], p[10]
    f3 = p[11] if len(p) > 11 else 0
    return Properties(
        power=p[0] == 1, mode=p[3], brightness=p[6] or 1, vertical_flip=p[7] == 1,
        support_lock=bit(f1, 0), locked=bit(f1, 1), support_pack_v2=bit(f1, 2), pack_v2_set=bit(f1, 3),
        support_set_remote=bit(f1, 4), support_no_response_a4=bit(f1, 5), support_multi_screen=bit(f1, 6),
        support_600_txt=bit(f1, 7), pack_v2_num=p[9],
        support_splice=bit(f2, 1), support_horizontal_reverse=bit(f2, 3), support_carousel_switch_anim=bit(f2, 5),
        support_text_bg_color=bit(f2, 6), support_text_align=bit(f2, 7),
        support_effect_text=bit(f3, 0), support_flood_fill=bit(f3, 2), raw=bytes(p),
    )


def parse_version(p: bytes) -> str:
    if len(p) == 3:
        return f"{p[0]}.{p[1]}.{p[2]}"
    return p.decode("utf-8", "replace").replace("v", "").replace("V", "")


# ---------------------------------------------------------------------------
# Model table
# ---------------------------------------------------------------------------


def uuid16(short: str) -> str:
    return f"0000{short.lower()}-0000-1000-8000-00805f9b34fb"


@dataclass
class Model:
    name: str
    pid: str
    width: int
    height: int
    chip: str
    scan_uuid: str
    service_uuid: str
    write_uuid: str
    notify_uuid: str
    more_type: int
    dual_chips: int


@functools.cache
def load_models() -> list[Model]:
    """Model table (cached; first call reads devices.json, so hosts with an event loop call it in a worker)."""
    data = json.loads(resources.files(__package__).joinpath("devices.json").read_text())
    return [
        Model(e["name"], e["pid"], e["width"], e["height"], e["chip"], uuid16(e["scan_uuid16"]),
              uuid16(e["service_uuid16"]), uuid16(e["write_uuid16"]), uuid16(e["notify_uuid16"]),
              e["more_type"], e["dual_chips"])
        for e in data
    ]


def match_model(adv_name: str | None, service_uuids: Iterable[str], models: list[Model] | None = None) -> Model | None:
    """Same rule as the app: name == deviceName or contains PID, AND adv UUIDs contain the scan UUID."""
    if not adv_name:
        return None
    uuids = " ".join(u.lower() for u in service_uuids)
    for m in models or load_models():
        if (adv_name == m.name or m.pid in adv_name) and m.scan_uuid[4:8] in uuids:
            return m
    return None


# ---------------------------------------------------------------------------
# "LZ" 2.4 GHz remote (BLE adverts, no connection) -- docs/FIRMWARE.md §9
# ---------------------------------------------------------------------------

REMOTE_LOCAL_NAME = "LZ"
REMOTE_COMPANY_ID = 0x02CC  # manufacturer-data company id the remote uses

# key code -> action, from the firmware's type-3 ("LZ") table at 0x1E1EB7E. Names with "?" are guesses.
REMOTE_KEYS: dict[int, str] = {
    0x01: "power", 0x02: "carousel", 0x03: "key_03", 0x04: "brightness_up", 0x05: "flip",
    0x06: "text_mode_2", 0x07: "brightness_down", 0x08: "clock", 0x09: "text_mode_1", 0x0A: "key_0a",
    0x0B: "timer_8h", 0x0C: "timer_2h", 0x0D: "key_0d", 0x0E: "key_0e", 0x0F: "key_0f",
    0x10: "preset_1", 0x11: "preset_2", 0x12: "preset_3", 0x13: "preset_4", 0x14: "preset_5", 0x15: "preset_6",
}


@dataclass(frozen=True)
class RemotePress:
    remote_id: str  # 4 hex bytes the panel pairs on (low address bytes, as sent: e.g. "66554433")
    seq: int        # rolling counter; one press is repeated with the same or rising seq
    key: int

    @property
    def action(self) -> str:
        return REMOTE_KEYS.get(self.key, f"key_{self.key:02x}")


def parse_remote_advert(local_name: str | None, manufacturer_data: dict[int, bytes], address: str) -> RemotePress | None:
    """Decode an LZ remote advert: name "LZ", company 0x02CC data ``seq id(2) key 00 00 chk(2)``.

    ``address`` is the advertiser's MAC (``11:22:33:44:55:66``); the panel matches the 4 bytes
    after the first two, low byte first. Returns None for anything that is not an LZ key packet.
    """
    data = manufacturer_data.get(REMOTE_COMPANY_ID)
    if local_name != REMOTE_LOCAL_NAME or not data or len(data) < 4:
        return None
    mac = bytes.fromhex(address.replace(":", "").replace("-", ""))[::-1]  # LE order as on air
    return RemotePress(remote_id=mac[:4].hex(), seq=data[0], key=data[3])
