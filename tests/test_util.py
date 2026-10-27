import os
import tempfile
import unittest

from ps3texmod.util import (align_up, fmt_hash, human_size, norm_name,
                            parse_hash, sha256_bytes, atomic_write_bytes,
                            atomic_write_json, read_json, hexdump)


class TestUtil(unittest.TestCase):
    def test_align_up(self):
        self.assertEqual(align_up(0, 0x800), 0)
        self.assertEqual(align_up(1, 0x800), 0x800)
        self.assertEqual(align_up(0x800, 0x800), 0x800)
        self.assertEqual(align_up(0x801, 0x800), 0x1000)
        with self.assertRaises(ValueError):
            align_up(5, 0)

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1023), "1023 B")
        self.assertEqual(human_size(1024), "1.00 KiB")
        self.assertIn("MiB", human_size(5 * 1024 * 1024))

    def test_norm_name(self):
        self.assertEqual(norm_name(r"\ART\Foo.DDS"), r"art\foo.dds")
        self.assertEqual(norm_name("art/foo.dds"), r"art\foo.dds")
        self.assertEqual(norm_name("\\art\\foo.dds"), r"art\foo.dds")

    def test_parse_hash(self):
        self.assertEqual(parse_hash("0x0FFD75FA"), 0x0FFD75FA)
        self.assertEqual(parse_hash("0ffd75fa"), 0x0FFD75FA)
        self.assertEqual(parse_hash("0F:FD:75:FA"), 0x0FFD75FA)
        self.assertEqual(fmt_hash(0x0FFD75FA), "0FFD75FA")

    def test_sha256(self):
        self.assertEqual(
            sha256_bytes(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_atomic_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a", "b.json")
            atomic_write_json(p, {"x": 1})
            self.assertEqual(read_json(p), {"x": 1})
            atomic_write_bytes(p, b"raw")
            with open(p, "rb") as f:
                self.assertEqual(f.read(), b"raw")
            # no temp files left behind
            self.assertEqual(sorted(os.listdir(os.path.dirname(p))), ["b.json"])
            with open(p, "rb") as f:
                f.seek(0, 2)
                self.assertEqual(f.tell(), 3)

    def test_hexdump(self):
        text = hexdump(b"ABC", 0x10)
        self.assertIn("00000010", text)
        self.assertIn("41 42 43", text)
        self.assertTrue(text.endswith("ABC"))


if __name__ == "__main__":
    unittest.main()
