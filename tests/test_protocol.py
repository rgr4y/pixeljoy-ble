import struct
import zlib

import pytest

from pixeljoy import protocol as P


def test_crc_check_value():
    assert P.crc16_modbus(b"123456789") == 0x4B37


def test_handshake_first_frame():
    f = P.handshake_first()
    assert f[:8] == bytes([0xA0, 0x00, 0x08]) + b"CCHIP"
    assert len(f) == 10
    assert struct.unpack("<H", f[-2:])[0] == P.crc16_modbus(f[:-2])


def _dev_frame(cmd, payload):
    return P.frame(cmd, payload, head=P.HEAD_DEV)


def test_handshake_second_xor():
    reply = P.parse_reply(_dev_frame(0x00, b"CCHIP\x11\x22"))
    f = P.handshake_second(reply.payload)
    assert f[1] == 0x01 and f[2] == 10
    assert f[3:10] == b"CCHIP" + bytes([0x11 ^ 0x24, 0x22 ^ 0x4C])
    assert P.handshake_second_ok(P.parse_reply(_dev_frame(0x01, b"CCHIP\x00")).payload)
    assert not P.handshake_second_ok(b"CCHIP\x01")


def test_parse_reply_rejects_bad_crc_and_len():
    good = bytearray(_dev_frame(0x10, b"\x01\x02"))
    assert P.parse_reply(bytes(good)).payload == b"\x01\x02"
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    with pytest.raises(P.FrameError):
        P.parse_reply(bytes(bad))
    bad = bytearray(good)
    bad[2] += 1
    with pytest.raises(P.FrameError):
        P.parse_reply(bytes(bad))


def test_frame_size_limit():
    P.frame(0x07, bytes(252))
    with pytest.raises(ValueError):
        P.frame(0x07, bytes(253))


def test_multi_packets_roundtrip():
    data = bytes(range(256)) * 5
    pkts = P.multi_packets(0x07, data, split=100, session=0x123)
    chunk = 100 - 11
    stream = b""
    for i, p in enumerate(pkts, 1):
        head, session, total, idx, ln = struct.unpack("<BHHHH", p[:9])
        assert (head, session, total, idx) == (0xA2, 0x123, len(pkts), i)
        part = p[9 : 9 + ln]
        assert len(p) == 9 + ln + 2
        assert struct.unpack("<H", p[-2:])[0] == P.crc16_modbus(part)
        assert ln <= chunk
        stream += part
    assert stream[0] == 0x07
    assert struct.unpack("<I", stream[1:5])[0] == len(data)
    assert stream[5:] == data


def test_multi_ack_lost_packet():
    raw = bytes([0xA4, 0, 0, 0, 0, 0, 0, 0, 0, 1, 7, 0]) + bytes(9)
    ack = P.parse_multi_ack(P.parse_reply(raw))
    assert ack.resend_from == 7 and not ack.next_group
    raw_ok = bytes([0xA4] + [0] * 8 + [0, 0, 0]) + bytes(9)
    assert P.parse_multi_ack(P.parse_reply(raw_ok)).next_group
    assert P.parse_multi_ack(P.parse_reply(bytes([0xA3, 0, 0, 0, 0]))).next_group


def test_send_file_split_rules():
    small = P.send_file_frames("img000001", bytes(100), mtu=512)
    assert small[0] is False and small[1][0][1] == 0x07
    big = P.send_file_frames("img000001", bytes(2000), mtu=512)
    assert big[0] is True
    assert all(len(p) <= 509 for p in big[1])
    # with a tiny MTU every write must still fit
    tiny = P.send_file_frames("img000001", bytes(300), mtu=23)
    assert tiny[0] is True and all(len(p) <= 20 for p in tiny[1])


def test_pack_1bpp_msb_first_and_padding():
    rows = [[1, 0, 0, 0, 0, 0, 0, 0, 1], [0] * 8 + [0]]
    assert P.pack_1bpp(rows) == bytes([0x80, 0x80, 0x00, 0x00])


def test_text_file_records():
    g = P.Glyph([[1, 1], [0, 1]])
    assert P.text_file([g]) == bytes([0x01, 2, 2, 0x01, 0xC0, 0x40])
    assert P.text_file([g], support_600_txt=True) == bytes([0x01, 2, 2, 0x01, 0x00, 0xC0, 0x40])


def test_show_text_payload_tlv():
    p = P.show_text_payload("txt123456", 3, P.TextStyle(color=(255, 0, 0)))
    assert p[0] == 0x02 and struct.unpack("<H", p[1:3])[0] == 5
    assert p[3:8] == bytes([0, 2, 255, 0, 0])
    tail = p[8:]
    assert tail[:3] == bytes([0x03, 1, 0])
    i = tail.index(0x0B)
    assert tail[i + 1] == 9 and tail[i + 2 : i + 11] == b"txt123456"
    rb = P.show_text_payload("t", 1, P.TextStyle(rainbow=4))
    assert rb[:3] == bytes([0x01, 0x01, 0x04])


def test_show_img_and_gif_payload():
    assert P.show_img_payload("img1") == b"\x01\x04img1" + bytes([2, 1, 0, 3, 2, 0, 0])
    g = P.show_gif_payload("dyn1", speed=600, sync_unix_ms=1)
    assert g[:6] == b"\x01\x04dyn1" and g[6:8] == bytes([3, 2]) and struct.unpack("<H", g[8:10])[0] == 450
    assert g[10:12] == bytes([5, 8]) and struct.unpack("<Q", g[12:20])[0] == 1


def test_simple_png_is_valid():
    px = [[(x * 8, y * 16, 0) for x in range(32)] for y in range(16)]
    png = P.simple_png(px)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, ctype = struct.unpack(">IIBB", png[16:26])
    assert (w, h, depth, ctype) == (32, 16, 8, 2)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41 : 41 + idat_len])
    assert len(raw) == 16 * (1 + 32 * 3)
    assert raw[1 + 3 * 5 : 1 + 3 * 6] == bytes([40, 0, 0])


def test_parse_properties():
    p = bytes([1, 0, 0, 2, 0, 0, 50, 0, 0b10100100, 4, 0b11000010, 0b00000101])
    props = P.parse_properties(p)
    assert props.power and props.mode == 2 and props.brightness == 50
    assert props.support_pack_v2 and props.support_no_response_a4 and props.support_600_txt
    assert not props.support_lock and props.preferred_pack_v2_num == 200
    assert props.support_splice and props.support_text_bg_color and props.support_text_align
    assert props.support_effect_text and props.support_flood_fill


def test_sync_time_layout():
    import datetime as dt

    t = dt.datetime(2026, 11, 12, 13, 14, 15)  # a Thursday
    f = P.sync_time(t)
    pl = f[3:-2]
    assert pl[:2] == bytes([0x07, 0xEA]) and list(pl[2:8]) == [11, 12, 13, 14, 15, 3]
    assert struct.unpack("<Q", pl[8:16])[0] == int(t.timestamp() * 1000)


def test_model_table_and_match():
    models = P.load_models()
    assert len(models) == 40
    m = P.match_model("P08F_1234", ["0000c08f-0000-1000-8000-00805f9b34fb"], models)
    assert m and (m.width, m.height) == (32, 16)
    assert m.write_uuid == "0000a08f-0000-1000-8000-00805f9b34fb"
    assert m.notify_uuid == "0000f08f-0000-1000-8000-00805f9b34fb"
    assert m.service_uuid == "0000e08f-0000-1000-8000-00805f9b34fb"
    assert P.match_model("P08F_1234", ["0000c088-0000-1000-8000-00805f9b34fb"], models) is None
    assert P.match_model("random", ["0000c08f-0000-1000-8000-00805f9b34fb"], models) is None
