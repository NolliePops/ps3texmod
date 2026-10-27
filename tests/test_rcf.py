import os
import random
import struct
import tempfile
import unittest
import zlib

from ps3texmod.rcf import RcfArchive, RcfError, build_archive
from ps3texmod.rz import is_rz, pack_rz
from ps3texmod.util import align_up
from tests.helpers import (default_assets, make_dds, make_p3d,
                           stored_hashes)


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.path = os.path.join(self.dir, "test.rcf")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, assets=None, **kw):
        build_archive(self.path, assets or default_assets(), **kw)
        return RcfArchive(self.path)


class TestBuildAndParse(ArchiveCase):
    def test_header_fields(self):
        ar = self.build()
        with open(self.path, "rb") as f:
            head = f.read(0x3C)
        self.assertEqual(head[:24], b"ATG CORE CEMENT LIBRARY\x00")
        version, idx_off, idx_size, tab_off, tab_size, zero, count = struct.unpack_from(
            ">7I", head, 0x20)
        self.assertEqual(version, 0x02010101)
        self.assertEqual(idx_off, 0x3C)
        self.assertEqual(idx_size, 12 * 3)
        self.assertEqual(tab_off, 0x800)
        self.assertEqual(zero, 0)
        self.assertEqual(count, 3)
        self.assertEqual(ar.info.data_start, align_up(tab_off + tab_size, 0x800))
        self.assertEqual(ar.info.count, 3)

    def test_name_order_matches_payload_order(self):
        assets = [
            (r"\z\last.dds", make_dds(8, 8, b"DXT1"), False),
            (r"\a\middle.p3d.rz", make_p3d([("x.dds", make_dds(8, 8, b"DXT5"))])[0], True),
            (r"\m\first.txt", b"text" * 100, True),
        ]
        ar = self.build(assets)
        self.assertTrue(ar.info.names_ok)
        kinds = [(e.name, ar.kind(e)) for e in ar._layout]
        self.assertEqual(kinds[0][0], r"\z\last.dds")
        self.assertEqual(kinds[0][1], "dds")
        self.assertEqual(kinds[1][0], r"\a\middle.p3d.rz")
        self.assertEqual(kinds[1][1], "p3d")
        self.assertEqual(kinds[2][0], r"\m\first.txt")
        self.assertEqual(kinds[2][1], "txt")

    def test_index_sorted_and_unique(self):
        ar = self.build()
        hashes = [e.hash for e in ar.entries]
        self.assertEqual(hashes, sorted(hashes))
        self.assertEqual(len(set(hashes)), 3)

    def test_payloads_800_aligned(self):
        ar = self.build()
        for e in ar._layout:
            self.assertEqual(e.offset % 0x800, 0, f"{e.name} not aligned")

    def test_validate_deep(self):
        ar = self.build()
        res = ar.validate(deep=True)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["names"], 3)
        self.assertEqual(res["bad_ext"], 0)

    def test_lookup_forms(self):
        ar = self.build()
        e = ar.entry(r"\art\test\standalone.dds")
        self.assertEqual(ar.entry(e.hash), e)
        self.assertEqual(ar.entry(f"{e.hash:08X}"), e)
        self.assertEqual(ar.entry(f"0x{e.hash:08X}"), e)
        self.assertEqual(ar.entry("standalone.dds"), e)
        self.assertEqual(ar.entry(r"ART\TEST\STANDALONE.DDS"), e)
        with self.assertRaises(KeyError):
            ar.entry("does-not-exist.dds")

    def test_ambiguous_suffix(self):
        assets = [
            (r"\a\shared.dds", make_dds(8, 8, b"DXT1"), False),
            (r"\b\shared.dds", make_dds(8, 8, b"DXT1"), False),
        ]
        ar = self.build(assets)
        with self.assertRaises(KeyError):
            ar.entry("shared.dds")
        self.assertEqual(ar.entry(r"\a\shared.dds").name, r"\a\shared.dds")

    def test_read_payload_rz_and_raw(self):
        ar = self.build()
        raw = ar.read_payload(r"\art\test\standalone.dds")
        self.assertTrue(raw.startswith(b"DDS "))
        rz = ar.read_stored(r"\art\test\embedded.p3d.rz")
        self.assertTrue(is_rz(rz))
        self.assertTrue(ar.read_payload(r"\art\test\embedded.p3d.rz").startswith(b"\xffD3P"))

    def test_build_errors(self):
        with self.assertRaises(RcfError):
            build_archive(self.path, [])
        dds = make_dds(8, 8, b"DXT1")
        with self.assertRaises(RcfError):
            build_archive(self.path, [
                ("a.dds", dds, False, 0x1234),
                ("b.dds", dds, False, 0x1234),
            ])

    def test_bad_files(self):
        with open(self.path, "wb") as f:
            f.write(b"NOT AN ARCHIVE" + bytes(100))
        with self.assertRaises(RcfError):
            RcfArchive(self.path)

        small = os.path.join(self.dir, "small.rcf")
        with open(small, "wb") as f:
            f.write(b"ATG CORE CEMENT LIBRARY\x00")
        with self.assertRaises(RcfError):
            RcfArchive(small)

    def test_stats(self):
        ar = self.build()
        st = ar.stats()
        self.assertEqual(st["entries"], 3)
        self.assertEqual(st["kinds"], {"dds": 1, "p3d": 1, "txt": 1})
        self.assertEqual(st["named"], 3)


class TestPatching(ArchiveCase):
    def test_inplace_patch(self):
        ar = self.build()
        before = stored_hashes(ar)
        size_before = os.path.getsize(self.path)
        e = ar.entry(r"\art\test\standalone.dds")
        new = make_dds(64, 64, b"DXT1", fill=b"\x11\x22\x33\x44" * (2048 // 4))
        self.assertEqual(len(new), e.size)
        res = ar.apply_patches({e.hash: new})
        self.assertEqual(res["mode"], "inplace")
        self.assertEqual(os.path.getsize(self.path), size_before)

        ar2 = RcfArchive(self.path)
        self.assertTrue(ar2.validate(deep=True)["ok"])
        self.assertEqual(ar2.read_stored(r"\art\test\standalone.dds"), new)
        self.assertEqual(ar2.entry(r"\art\test\standalone.dds").size, len(new))
        after = stored_hashes(ar2)
        for h, v in before.items():
            if h != e.hash:
                self.assertEqual(after[h], v, "untouched entry changed")

    def test_rebuild_when_blob_grows(self):
        ar = self.build()
        before = stored_hashes(ar)
        e = ar.entry(r"\art\test\embedded.p3d.rz")
        payload = ar.read_payload(e)
        old_dds = payload[payload.find(b"DDS "):]
        info_probe = make_dds(64, 64, b"DXT5", mips=2)
        self.assertTrue(old_dds.startswith(b"DDS "))
        rng = random.Random(7)
        noisy = bytearray(make_dds(64, 64, b"DXT5", mips=2))
        noisy[128:] = rng.randbytes(len(noisy) - 128)
        noisy = bytes(noisy)
        new_p3d = payload[:payload.find(b"DDS ")] + noisy
        new_stored = pack_rz(new_p3d)
        old_stored = ar.read_stored(e)
        self.assertGreater(len(new_stored), e.slot_size,
                           "test must force a rebuild")
        res = ar.apply_patches({e.hash: new_stored})
        self.assertTrue(res["mode"].startswith("rebuild"), res["mode"])

        ar2 = RcfArchive(self.path)
        self.assertTrue(ar2.validate(deep=True)["ok"])
        self.assertEqual(ar2.read_payload(e.hash), new_p3d)
        self.assertEqual(ar2.entry(e.hash).hash, e.hash)
        after = stored_hashes(ar2)
        for h, v in before.items():
            if h != e.hash:
                self.assertEqual(after[h], v, "untouched entry changed across rebuild")
        self.assertEqual([x.hash for x in ar2.entries],
                         sorted(x.hash for x in ar2.entries))
        self.assertTrue(ar2.info.names_ok)

    def test_rebuild_to_new_path_keeps_original(self):
        ar = self.build()
        before = stored_hashes(ar)
        e = ar.entry(r"\art\test\standalone.dds")
        out = os.path.join(self.dir, "copy.rcf")
        new = make_dds(128, 128, b"DXT1")
        res = ar.apply_patches({e.hash: new}, out_path=out)
        self.assertTrue(res["mode"].startswith("rebuild"), res["mode"])
        self.assertEqual(stored_hashes(RcfArchive(self.path)), before)
        cp = RcfArchive(out)
        self.assertTrue(cp.validate(deep=True)["ok"])
        self.assertEqual(cp.read_stored(e.hash), new)

    def test_rzi_guard(self):
        ar = self.build()
        e = ar.entry(r"\art\test\embedded.p3d.rz")
        with self.assertRaises(RcfError):
            ar.apply_patches({e.hash: b"P3D\xff" + bytes(100)})

    def test_no_patches_noop(self):
        ar = self.build()
        self.assertEqual(ar.apply_patches({})["mode"], "noop")

    def test_rebuild_copy_helper(self):
        ar = self.build()
        out = os.path.join(self.dir, "rebuilt.rcf")
        info = ar.rebuild_copy(out)
        self.assertEqual(info["entries"], 3)
        cp = RcfArchive(out)
        self.assertTrue(cp.validate(deep=True)["ok"])
        self.assertEqual(cp.read_payload(r"\art\test\embedded.p3d.rz"),
                         ar.read_payload(r"\art\test\embedded.p3d.rz"))

    def test_tampered_payload_detected_deep(self):
        ar = self.build()
        e = ar.entry(r"\art\test\standalone.dds")
        with open(self.path, "r+b") as f:
            f.seek(e.offset)
            f.write(b"XXXX")
        ar2 = RcfArchive(self.path)
        res = ar2.validate(deep=True)
        self.assertFalse(res["ok"])
        self.assertGreater(res["bad_ext"], 0)

    def test_overlap_detected(self):
        ar = self.build()
        e = ar._layout[1]
        first = ar._layout[0]
        with open(self.path, "r+b") as f:
            f.seek(ar.info.index_offset + e.index * 12 + 4)
            f.write(struct.pack(">I", first.offset))  # second entry over first
        ar2 = RcfArchive(self.path)
        res = ar2.validate()
        self.assertFalse(res["ok"])
        self.assertTrue(any("overlap" in p for p in res["problems"]))


if __name__ == "__main__":
    unittest.main()
