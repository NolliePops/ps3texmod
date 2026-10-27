import struct
import unittest
import zlib

from ps3texmod.rz import (RzError, is_rz, pack_rz, rz_info, unpack_rz)


class TestRz(unittest.TestCase):
    def test_roundtrip(self):
        data = b"hello cement " * 100
        blob = pack_rz(data)
        self.assertTrue(is_rz(blob))
        self.assertEqual(unpack_rz(blob), data)
        info = rz_info(blob)
        self.assertEqual(info["uncompressed_size"], len(data))
        self.assertEqual(info["stored_size"], len(blob))
        self.assertLessEqual(info["ratio"], 1.0)

    def test_header_preserved(self):
        header = b"RZ" + b"\x01\x02\x03\x04\x05\x06"
        blob = pack_rz(b"x" * 1000, level=1, header=header)
        self.assertEqual(blob[:8], header)
        self.assertEqual(unpack_rz(blob), b"x" * 1000)

    def test_bad_header_template(self):
        with self.assertRaises(RzError):
            pack_rz(b"x", header=b"NO" + bytes(6))

    def test_not_rz(self):
        self.assertFalse(is_rz(b"P3D\xff" + bytes(20)))
        with self.assertRaises(RzError):
            unpack_rz(b"P3D\xff" + bytes(20))

    def test_size_mismatch(self):
        good = pack_rz(b"abcdef" * 10)
        bad = bytearray(good)
        struct.pack_into("<Q", bad, 8, 999)
        with self.assertRaises(RzError):
            unpack_rz(bytes(bad))

    def test_truncated_stream(self):
        good = pack_rz(b"abcdef" * 100)
        with self.assertRaises(RzError):
            unpack_rz(good[:20])

    def test_levels_produce_valid_streams(self):
        data = bytes(range(256)) * 8
        for level in (0, 1, 6, 9):
            blob = pack_rz(data, level=level)
            self.assertEqual(unpack_rz(blob), data)
            self.assertEqual(zlib.decompress(blob[16:]), data)


if __name__ == "__main__":
    unittest.main()
