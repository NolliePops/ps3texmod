"""Tests for the BMP -> DDS encoder (no retail data required)."""

import struct
import unittest

from ps3texmod.dds import decode_level0_rgba, parse_dds
from ps3texmod.encode import (EncodeError, build_dds_like, encode_dxt1,
                              halve, read_bmp, write_bmp)
from tests.helpers import make_dds


def solid(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    return bytes((*rgb, 255)) * (width * height)


class TestBmp(unittest.TestCase):
    def test_roundtrip_24bit(self):
        rgba = bytearray()
        for y in range(3):
            for x in range(5):
                rgba += bytes((x * 40, y * 60, 128, 255))
        raw = write_bmp(bytes(rgba), 5, 3)
        self.assertEqual(raw[:2], b"BM")
        back, w, h = read_bmp(raw)
        self.assertEqual((w, h), (5, 3))
        self.assertEqual(back, bytes(rgba))

    def test_top_down_bmp(self):
        # negative height = top-down rows
        raw = bytearray(write_bmp(bytes((10, 20, 30, 255)) * 16, 4, 4))
        struct.pack_into("<i", raw, 22, -4)
        back, w, h = read_bmp(bytes(raw))
        self.assertEqual((w, h), (4, 4))
        self.assertEqual(back[:4], bytes((10, 20, 30, 255)))

    def test_rejects_compressed(self):
        raw = bytearray(write_bmp(bytes((0, 0, 0, 255)) * 16, 4, 4))
        struct.pack_into("<I", raw, 30, 1)  # BI_RLE8
        with self.assertRaises(EncodeError):
            read_bmp(bytes(raw))

    def test_rejects_non_bmp(self):
        with self.assertRaises(EncodeError):
            read_bmp(b"\x89PNG\r\n\x1a\n" + bytes(64))


class TestDxt1(unittest.TestCase):
    def test_uniform_black_is_zeroes(self):
        blob = encode_dxt1(solid(4, 4, (0, 0, 0)), 4, 4)
        self.assertEqual(blob, bytes(8))

    def test_block_count_and_edges(self):
        blob = encode_dxt1(solid(7, 5, (255, 255, 255)), 7, 5)
        self.assertEqual(len(blob), 2 * 2 * 8)

    def test_colour_survives_roundtrip(self):
        rgba = solid(8, 8, (30, 200, 60))
        blob = encode_dxt1(rgba, 8, 8)
        dds = make_dds(8, 8, b"DXT1", mips=1, fill=blob + bytes(128))
        back, w, h = decode_level0_rgba(dds, parse_dds(dds))
        self.assertEqual((w, h), (8, 8))
        for i in range(0, len(back), 4):
            self.assertLessEqual(abs(back[i] - 30), 8)
            self.assertLessEqual(abs(back[i + 1] - 200), 8)
            self.assertLessEqual(abs(back[i + 2] - 60), 8)

    def test_halve_averages(self):
        row0 = bytes((0, 0, 0, 255)) * 2
        row1 = bytes((255, 255, 255, 255)) * 2
        out, w, h = halve(row0 + row1, 2, 2)
        self.assertEqual((w, h), (1, 1))
        self.assertEqual(out, bytes((127, 127, 127, 255)))

    def test_halve_odd_size_keeps_one_pixel(self):
        out, w, h = halve(solid(1, 1, (1, 2, 3)), 1, 1)
        self.assertEqual((w, h), (1, 1))
        self.assertEqual(out, bytes((1, 2, 3, 255)))


class TestSlotMatch(unittest.TestCase):
    def test_matches_template_size_and_header(self):
        template = make_dds(64, 32, b"DXT1", mips=3)
        blob = build_dds_like(template, solid(64, 32, (200, 40, 40)), 64, 32)
        self.assertEqual(len(blob), len(template))
        info = parse_dds(blob)
        self.assertEqual((info.width, info.height), (64, 32))
        self.assertEqual(info.format_name, "DXT1")
        self.assertEqual(info.mipmaps, 3)
        # header was copied, only the size fields change
        self.assertEqual(blob[4:12], template[4:12])
        self.assertEqual(blob[84:104], template[84:104])

    def test_dimension_mismatch_rejected(self):
        template = make_dds(64, 32, b"DXT1", mips=1)
        with self.assertRaises(EncodeError) as ctx:
            build_dds_like(template, solid(32, 16, (0, 0, 0)), 32, 16)
        self.assertIn("64x32", str(ctx.exception))

    def test_dxt5_template_rejected(self):
        template = make_dds(64, 64, b"DXT5", mips=1)
        with self.assertRaises(EncodeError) as ctx:
            build_dds_like(template, solid(64, 64, (0, 0, 0)), 64, 64)
        self.assertIn("DXT1", str(ctx.exception))

    def test_uncompressed_template_rejected(self):
        template = make_dds(8, 8, b"", mips=1)
        with self.assertRaises(EncodeError):
            build_dds_like(template, solid(8, 8, (0, 0, 0)), 8, 8)


if __name__ == "__main__":
    unittest.main()
