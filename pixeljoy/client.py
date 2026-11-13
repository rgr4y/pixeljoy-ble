"""Async BLE client for PixelJoy panels (bleak).

Every TX/RX frame is logged at DEBUG level (``-v`` on the CLI).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from . import protocol as P

log = logging.getLogger("pixeljoy")


class PixelJoyError(RuntimeError):
    pass


@dataclass
class Found:
    device: BLEDevice
    name: str | None
    rssi: int | None
    service_uuids: list[str]
    manufacturer_data: dict[int, bytes]
    model: P.Model | None


async def scan(timeout: float = 8.0, show_all: bool = False) -> list[Found]:
    models = P.load_models()
    seen = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in seen.values():
        model = P.match_model(adv.local_name or dev.name, adv.service_uuids, models)
        if model or show_all:
            out.append(Found(dev, adv.local_name or dev.name, adv.rssi, list(adv.service_uuids),
                             dict(adv.manufacturer_data), model))
    return out


async def find(timeout: float = 8.0, address: str | None = None, name: str | None = None) -> Found:
    """Return the first matching panel; stops scanning as soon as one is seen."""
    models = P.load_models()
    hit: list[Found] = []

    def match(dev: BLEDevice, adv) -> bool:
        adv_name = adv.local_name or dev.name
        if address and dev.address.lower() != address.lower():
            return False
        if name and name.lower() not in (adv_name or "").lower():
            return False
        model = P.match_model(adv_name, adv.service_uuids, models)
        if model:
            hit.append(Found(dev, adv_name, adv.rssi, list(adv.service_uuids), dict(adv.manufacturer_data), model))
        return model is not None

    if await BleakScanner.find_device_by_filter(match, timeout=timeout) is None:
        raise PixelJoyError("no PixelJoy panel found (is the phone app connected? try `scan --all`)")
    return hit[-1]



async def dump_gatt(address: str | BLEDevice) -> None:
    """Print every service/characteristic/descriptor with its properties."""
    async with BleakClient(address) as c:
        print(f"mtu={getattr(c, 'mtu_size', '?')}")
        for s in c.services:
            print(f"service {s.uuid}  {s.description}")
            for ch in s.characteristics:
                print(f"  char {ch.uuid}  props={ch.properties}  max_write_wo_resp={getattr(ch, 'max_write_without_response_size', '?')}")
                for d in ch.descriptors:
                    print(f"    desc {d.uuid}  {d.description}")


class PixelJoy:
    def __init__(self, address: str | BLEDevice, model: P.Model, auto_time_sync: bool = True, tz_name: str | None = None):
        self.model = model
        self.auto_time_sync = auto_time_sync
        self.tz_name = tz_name
        self._client = BleakClient(address)
        self._rx: asyncio.Queue[P.Reply] = asyncio.Queue()
        self.properties: P.Properties | None = None
        self.pack_v2_num = 1
        self._write_response = False
        self._max_write: int | None = None

    # -- plumbing ---------------------------------------------------------

    @property
    def mtu(self) -> int:
        return getattr(self._client, "mtu_size", 23) or 23

    async def __aenter__(self) -> "PixelJoy":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        # Cleanup must not hang or mask the original error (e.g. on Ctrl+C).
        try:
            await asyncio.wait_for(self._client.disconnect(), 5)
        except Exception as e:  # noqa: BLE001
            log.debug("disconnect failed: %r", e)

    def _on_notify(self, _char, data: bytearray) -> None:
        log.debug("RX %s", bytes(data).hex(" "))
        try:
            reply = P.parse_reply(bytes(data))
        except P.FrameError as e:
            log.warning("unparsed notification: %s", e)
            return
        if reply.head == P.HEAD_GET_TIME:
            asyncio.get_running_loop().create_task(self._write(P.time_reply()))
            return
        self._rx.put_nowait(reply)

    async def _write(self, data: bytes) -> None:
        log.debug("TX %s", data.hex(" "))
        # Chunk at ATT payload size; firmware reassembles A0 frames by length.
        step = max(20, self._max_write or (self.mtu - 3))
        for i in range(0, len(data), step):
            await self._client.write_gatt_char(self.model.write_uuid, data[i : i + step], response=self._write_response)

    async def _expect(self, *, cmd: int | None = None, heads: tuple[int, ...] = (P.HEAD_DEV,), timeout: float = 5.0) -> P.Reply:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PixelJoyError(f"timeout waiting for cmd={cmd!r} heads={[hex(h) for h in heads]}")
            reply = await asyncio.wait_for(self._rx.get(), remaining)
            if reply.head in heads and (cmd is None or reply.cmd == cmd):
                return reply
            log.debug("ignoring reply head=%02X cmd=%02X", reply.head, reply.cmd)

    async def drain(self, seconds: float) -> list[P.Reply]:
        """Collect every reply that arrives within ``seconds``."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        out = []
        while (remaining := deadline - loop.time()) > 0:
            try:
                out.append(await asyncio.wait_for(self._rx.get(), remaining))
            except asyncio.TimeoutError:
                break
        return out

    async def request(self, data: bytes, cmd: int, timeout: float = 5.0) -> P.Reply:
        await self._write(data)
        return await self._expect(cmd=cmd, timeout=timeout)

    # -- session ----------------------------------------------------------

    async def _open(self) -> None:
        """Connect the BLE link. Hosts with their own connection logic (Home Assistant) override this."""
        await self._client.connect()

    async def connect(self) -> None:
        await self._open()
        log.info("connected, mtu=%s", self.mtu)
        char = self._client.services.get_characteristic(self.model.write_uuid)
        if char is None:
            raise PixelJoyError(f"write characteristic {self.model.write_uuid} not found; run `python -m pixeljoy gatt`")
        # Match Android's default (the vendor app never sets a write type): if the
        # characteristic supports write-without-response, that is what gets used.
        self._write_response = "write-without-response" not in char.properties
        self._max_write = getattr(char, "max_write_without_response_size", None) if not self._write_response else None
        log.info("write char %s properties=%s -> response=%s max_write=%s",
                 char.uuid, char.properties, self._write_response, self._max_write)
        await self._client.start_notify(self.model.notify_uuid, self._on_notify)
        await self.handshake()
        self.properties = await self.read_properties()
        self.pack_v2_num = self.properties.preferred_pack_v2_num
        if self.properties.support_pack_v2 and self.properties.pack_v2_num != self.pack_v2_num:
            await self._write(P.set_multi_pack_v2(True, self.pack_v2_num))
        if self.auto_time_sync:
            await self.sync_time(self.tz_name)

    async def handshake(self, attempts: int = 3) -> None:
        for _ in range(attempts):
            await self._write(P.handshake_first())
            try:
                r1 = await self._expect(cmd=P.Cmd.SHAKE_HAND_FIRST, timeout=1.0)
                break
            except PixelJoyError:
                continue
        else:
            raise PixelJoyError("no reply to handshake 1")
        log.info("handshake 1 reply payload: %s", r1.payload.hex(" "))
        r2 = await self.request(P.handshake_second(r1.payload), P.Cmd.SHAKE_HAND_SECOND, timeout=3.0)
        log.info("handshake 2 reply payload: %s", r2.payload.hex(" "))
        if not P.handshake_second_ok(r2.payload):
            raise PixelJoyError(f"handshake rejected: {r2.payload.hex(' ')}")

    async def read_properties(self) -> P.Properties:
        r = await self.request(P.read_property(), P.Cmd.READ_PROPERTY)
        return P.parse_properties(r.payload)

    # -- commands ---------------------------------------------------------

    async def power(self, on: bool) -> None:
        await self._write(P.switch_light(on))

    async def show_clock(self, style: int = 0, h24: bool = True, date: bool = False,
                         color: tuple[int, int, int] = (255, 255, 255)) -> None:
        await self._write(P.show_clock(style, h24, date, color))

    async def brightness(self, level: int) -> None:
        await self._write(P.set_brightness(level))

    async def sync_time(self, tz_name: str | None = None) -> None:
        """Same sequence the app sends after connecting: time zone/DST, wall clock, start."""
        tz_payload = P.time_zone_payload(tz_name)
        log.info("time sync: tz=%s payload=%s", tz_name or P._local_tz_name(), tz_payload.hex(" "))
        await self._write(P.frame(P.Cmd.SET_TIME_ZONE, tz_payload))
        await self._write(P.sync_time())
        await self._write(P.start_sync_time())

    async def firmware_version(self) -> str:
        return P.parse_version((await self.request(P.get_firmware_version(), P.Cmd.GET_FIRMWARE_VERSION)).payload)

    async def send_multi(self, packets: list[bytes], expect_file_reply: bool) -> None:
        """Run the A2 flow-control loop (MultiPackUtilV2)."""
        n = max(1, self.pack_v2_num)
        no_wait = bool(self.properties and self.properties.support_no_response_a4 and expect_file_reply)
        i = 0
        while i < len(packets):
            group_end = min(i + n, len(packets))
            for j in range(i, group_end):
                await self._write(packets[j])
            i = group_end
            if no_wait:
                await asyncio.sleep(0.02)
                # Still honour a lost-packet request if one is queued.
                while not self._rx.empty():
                    r = self._rx.get_nowait()
                    if r.head == P.HEAD_MULTI_ACK_V2:
                        ack = P.parse_multi_ack(r)
                        if ack.resend_from:
                            i = ack.resend_from - 1
                    else:
                        self._rx.put_nowait(r)
                        break
                continue
            if i >= len(packets) and expect_file_reply:
                break  # final reply is A1 07
            r = await self._expect(heads=(P.HEAD_MULTI_ACK, P.HEAD_MULTI_ACK_V2), timeout=10.0)
            ack = P.parse_multi_ack(r)
            if ack.resend_from:
                log.warning("device requested resend from packet %d", ack.resend_from)
                i = ack.resend_from - 1

    async def upload(self, name: str, data: bytes) -> None:
        multi, frames = P.send_file_frames(name, data, self.mtu)
        log.info("upload %s: %d bytes in %d frame(s)", name, len(data), len(frames))
        if multi:
            await self.send_multi(frames, expect_file_reply=True)
        else:
            await self._write(frames[0])
        r = await self._expect(cmd=P.Cmd.SEND_FILE, timeout=15.0)
        if not r.payload or r.payload[0] != 1:
            await self._write(P.delete_file(name))
            raise PixelJoyError(f"upload failed: {r.payload.hex(' ')}")

    async def _send_cmd(self, cmd: int, payload: bytes) -> None:
        multi, frames = P.cmd_frames(cmd, payload, self.mtu)
        if multi:
            await self.send_multi(frames, expect_file_reply=False)
        else:
            await self._write(frames[0])

    async def show_png(self, png: bytes, name: str | None = None) -> None:
        name = name or P.file_name("img")
        await self.upload(name, png)
        await self._send_cmd(P.Cmd.SHOW_IMG, P.show_img_payload(name))

    async def show_gif(self, gif: bytes, speed: int = 600, name: str | None = None) -> None:
        name = name or P.file_name("gif")
        await self.upload(name, gif)
        await self._send_cmd(P.Cmd.SHOW_GIF, P.show_gif_payload(name, speed))

    async def show_text(self, glyphs: list[P.Glyph], style: P.TextStyle = P.TextStyle(), name: str | None = None) -> None:
        props = self.properties
        s600 = bool(props and props.support_600_txt)
        name = name or P.file_name("text")
        await self.upload(name, P.text_file(glyphs, s600))
        if props and not props.support_text_bg_color:
            style.bg_color = None
        if props and not props.support_text_align:
            style.align = None
        await self._send_cmd(P.Cmd.SHOW_TEXT, P.show_text_payload(name, len(glyphs), style, s600))
