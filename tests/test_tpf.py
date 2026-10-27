"""Tests for the Texmod .tpf container.

The fixture is built here from scratch: a ZipCrypto-encrypted ZIP (the same
scheme TexMod uses) wrapped in the 0x3FA43FA4 XOR layer.  No retail data is
needed, and no sample package is redistributed.
"""

import io
import struct
import unittest
import zipfile
import zlib

from ps3texmod.tpf import (DEF_NAME, TPF_PASSWORD, TpfError, decrypt, encrypt,
                           extract, list_tpf, parse_texdef, read_tpf)


# ---------------------------------------------------------------------------
# minimal ZipCrypto writer (test-only; zipfile can read but not write these)
# ---------------------------------------------------------------------------

def _crc_table():
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    return table


_CRC = _crc_table()


def _crc32_step(crc, byte):
    return (crc >> 8) ^ _CRC[(crc ^ byte) & 0xFF]


class _Crypto:
    def __init__(self, password: bytes):
        self.k0, self.k1, self.k2 = 0x12345678, 0x23456789, 0x34567890
        for b in password:
            self._update(b)

    def _update(self, b):
        self.k0 = _crc32_step(self.k0, b)
        self.k1 = ((self.k1 + (self.k0 & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        self.k2 = _crc32_step(self.k2, self.k1 >> 24)

    def _byte(self):
        t = (self.k2 | 2) & 0xFFFF
        return ((t * (t ^ 1)) >> 8) & 0xFF

    def encrypt(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            out.append(b ^ self._byte())
            self._update(b)
        return bytes(out)


def make_zipcrypto_zip(files: dict[str, bytes], password: bytes) -> bytes:
    body = bytearray()
    central = bytearray()
    for name, data in files.items():
        crc = zlib.crc32(data) & 0xFFFFFFFF
        name_b = name.encode("utf-8")
        keys = _Crypto(password)
        header = bytearray(b"\x00" * 11) + bytes((crc >> 24,))
        payload = keys.encrypt(bytes(header) + data)
        offset = len(body)
        body += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0001, 0, 0, 0,
                            crc, len(payload), len(data), len(name_b), 0)
        body += name_b + payload
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20,
                               0x0001, 0, 0, 0, crc, len(payload), len(data),
                               len(name_b), 0, 0, 0, 0, 0, offset)
        central += name_b
    cd_offset = len(body)
    body += central
    body += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(files), len(files),
                        len(central), cd_offset, 0)
    return bytes(body)


def make_tpf(files: dict[str, bytes]) -> bytes:
    return encrypt(make_zipcrypto_zip(files, TPF_PASSWORD))


BMP = (b"BM" + bytes(52))


class TestTexdef(unittest.TestCase):
    def test_parses_real_world_padding(self):
        text = "0x35931490|tex_0x35931490.bmp\r\n\x00"
        self.assertEqual(parse_texdef(text),
                         [(0x35931490, "tex_0x35931490.bmp")])

    def test_skips_comments_and_blanks(self):
        text = "# note\n\n0x10|a.bmp\n0x20|b.bmp\n"
        self.assertEqual([h for h, _ in parse_texdef(text)], [0x10, 0x20])

    def test_rejects_missing_pipe(self):
        with self.assertRaises(TpfError):
            parse_texdef("0x10 a.bmp\n")

    def test_rejects_bad_hash(self):
        with self.assertRaises(TpfError):
            parse_texdef("nope|a.bmp\n")

    def test_rejects_empty(self):
        with self.assertRaises(TpfError):
            parse_texdef("# nothing here\n")


class TestPackage(unittest.TestCase):
    def setUp(self):
        self.raw = make_tpf({DEF_NAME: b"0x35931490|tex.bmp\r\n", "tex.bmp": BMP})
        self.tmp = None

    def _write(self, blob: bytes) -> str:
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".tpf")
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        self.addCleanup(os.unlink, path)
        return path

    def test_xor_is_symmetric(self):
        blob = b"Texmod layer test" * 3
        self.assertEqual(encrypt(decrypt(blob)), blob)

    def test_reads_members(self):
        path = self._write(self.raw)
        files = read_tpf(path)
        self.assertEqual(files["tex.bmp"], BMP)
        self.assertIn(DEF_NAME, files)

    def test_lists_members(self):
        path = self._write(self.raw)
        items = list_tpf(path)
        names = [i["name"] for i in items]
        self.assertEqual(names, [DEF_NAME, "tex.bmp"])
        self.assertTrue(all(i["encrypted"] for i in items))

    def test_plain_zip_wrapper(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Sam Fisher V3.tpf", self.raw)
            zf.writestr("readme.txt", b"hi")
        path = self._write(buf.getvalue())
        self.assertEqual(read_tpf(path)["tex.bmp"], BMP)

    def test_wrapper_without_tpf_is_an_error(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", b"no package here")
        path = self._write(buf.getvalue())
        with self.assertRaises(TpfError):
            read_tpf(path)

    def test_not_a_package(self):
        path = self._write(b"just some bytes, not a tpf at all" * 4)
        with self.assertRaises(TpfError):
            read_tpf(path)

    def test_extract_writes_files(self):
        import os
        import tempfile
        path = self._write(self.raw)
        out = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(out, True))
        result = extract(path, out, log=lambda *_: None)
        self.assertEqual(len(result["files"]), 2)
        self.assertTrue(os.path.isfile(os.path.join(out, "tex.bmp")))
        self.assertEqual(result["definitions"],
                         [{"hash": "0x35931490", "image": "tex.bmp"}])


if __name__ == "__main__":
    unittest.main()

