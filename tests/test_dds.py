import struct
import unittest

from ps3texmod.dds import (DdsError, build_png, decode_level0_rgba, dds_to_png,
                           iter_dds, parse_dds)
from tests.helpers import make_dds, png_size


class TestDdsParse(unittest.TestCase):
    def test_dxt1_size(self):
        raw = make_dds(64, 64, b"DXT1")
        info = parse_dds(raw)
        self.assertEqual((info.width, info.height), (64, 64))
        self.assertEqual(info.format_name, "DXT1")
        self.assertTrue(info.compressed)
        self.assertEqual(info.data_size, 16 * 16 * 8)
        self.assertEqual(info.data_offset, 128)
        self.assertEqual(len(raw), 128 + info.data_size)

    def test_dxt5_mips(self):
        raw = make_dds(32, 32, b"DXT5", mips=3)
        info = parse_dds(raw)
        self.assertEqual(info.mipmaps, 3)
        self.assertEqual(info.data_size, 1024 + 256 + 64)

    def test_uncompressed_rgba(self):
        raw = make_dds(8, 8, b"", mips=2)
        info = parse_dds(raw)
        self.assertFalse(info.compressed)
        self.assertEqual(info.format_name, "A8R8G8B8")
        self.assertEqual(info.data_size, 8 * 8 * 4 + 4 * 4 * 4)

    def test_payload_overrun(self):
        raw = make_dds(64, 64, b"DXT1")
        with self.assertRaises(DdsError):
            parse_dds(raw[:-16])

    def test_truncated_header(self):
        with self.assertRaises(DdsError):
            parse_dds(b"DDS " + bytes(20))

    def test_bad_magic(self):
        raw = bytearray(make_dds(8, 8, b"DXT1"))
        raw[:4] = b"XXXX"
        with self.assertRaises(DdsError):
            parse_dds(bytes(raw))

    def test_bad_header_size(self):
        raw = bytearray(make_dds(8, 8, b"DXT1"))
        struct.pack_into("<I", raw, 4, 100)
        with self.assertRaises(DdsError):
            parse_dds(bytes(raw))

    def test_zero_dimensions(self):
        raw = bytearray(make_dds(8, 8, b"DXT1"))
        struct.pack_into("<I", raw, 12, 0)
        with self.assertRaises(DdsError):
            parse_dds(bytes(raw))

    def test_dx10_rejected_from_size_calc(self):
        raw = bytearray(make_dds(8, 8, b"DXT1"))
        struct.pack_into("<I", raw, 84, int.from_bytes(b"DX10", "little"))
        # parse succeeds structurally (header adjusts), but there are not
        # enough bytes for a DX10 header payload here
        with self.assertRaises(DdsError):
            parse_dds(bytes(raw), available=len(raw))

    def test_summary_and_dict(self):
        info = parse_dds(make_dds(16, 8, b"DXT1"))
        d = info.to_dict()
        self.assertEqual(d["format"], "DXT1")
        self.assertEqual(d["total_size"], 128 + info.data_size)
        self.assertIn("16x8", info.summary())


class TestDdsScan(unittest.TestCase):
    def test_iter_finds_embedded(self):
        dds = make_dds(8, 8, b"DXT1")
        payload = b"header garbage" * 4 + dds + b"trailer"
        hits = list(iter_dds(payload))
        self.assertEqual(len(hits), 1)
        off, info = hits[0]
        self.assertEqual(off, len(b"header garbage" * 4))
        self.assertEqual(info.width, 8)

    def test_iter_ignores_false_magic(self):
        payload = b"DDS " + bytes(200)  # no 0x7c header size
        self.assertEqual(list(iter_dds(payload)), [])

    def test_iter_multiple(self):
        a = make_dds(8, 8, b"DXT1")
        b = make_dds(16, 16, b"DXT5")
        payload = a + b"pad" + b
        offs = [off for off, _ in iter_dds(payload)]
        self.assertEqual(offs, [0, len(a) + 3])


class TestDdsDecode(unittest.TestCase):
    def test_dxt1_solid_red(self):
        raw = make_dds(4, 4, b"DXT1")
        # block written by make_dds: c0=red, c1=blue, indices=0
        info = parse_dds(raw)
        rgba, w, h = decode_level0_rgba(raw, info)
        self.assertEqual((w, h), (4, 4))
        self.assertEqual(tuple(rgba[0:4]), (255, 0, 0, 255))
        self.assertEqual(tuple(rgba[-4:]), (255, 0, 0, 255))

    def test_dxt5_alpha(self):
        raw = make_dds(4, 4, b"DXT5")
        info = parse_dds(raw)
        rgba, _, _ = decode_level0_rgba(raw, info)
        self.assertEqual(rgba[3], 255)
        self.assertEqual(rgba[0], 255)

    def test_png_roundtrip_header(self):
        raw = make_dds(32, 16, b"DXT5")
        png = dds_to_png(raw)
        self.assertEqual(png_size(png), (32, 16))
        # build_png validates buffer length
        with self.assertRaises(ValueError):
            build_png(b"\x00" * 10, 2, 2)

    def test_uncompressed_decode(self):
        raw = make_dds(2, 2, b"", fill=b"\x00\x00\xff\xff" * 4)
        info = parse_dds(raw)
        rgba, w, h = decode_level0_rgba(raw, info)
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(tuple(rgba[0:4]), (255, 0, 0, 255))


if __name__ == "__main__":
    unittest.main()
