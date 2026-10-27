"""Tests for the precomputed console plan (.ps3patch) and journal format."""

import os
import struct
import tempfile
import unittest

from ps3texmod.catalog import make_patch, scan_archive
from ps3texmod.pack import build_pack, resolve_token
from ps3texmod.plan import (JOURNAL_MAGIC, PLAN_MAGIC, PlanError, apply_plan,
                            build_plan, find_journals, journal_info,
                            load_plan, plan_from_pack, restore_journal,
                            verify_plan)
from ps3texmod.rcf import RcfArchive, build_archive
from tests.helpers import file_sha256, make_dds, make_game


class PlanCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.game = make_game(self.dir)
        self.usrdir = os.path.join(self.game, "PS3_GAME", "USRDIR")
        self.archive = os.path.join(self.usrdir, "art.rcf")
        self.ar = RcfArchive(self.archive)
        self.journals = os.path.join(self.dir, "backups")
        self.recs = {r["container"]: r for r in scan_archive(self.ar)}
        self.standalone = self.recs["dds"]
        self.embedded = self.recs["p3d"]

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------------
    def tinted(self, rec, seed=0x11, suffix=b""):
        """A same-shape replacement texture for a catalogue record."""
        return make_dds(
            rec["width"], rec["height"], rec["format"].encode(),
            mips=rec["mipmaps"],
            fill=bytes([seed, seed ^ 0x55, seed ^ 0xAA, 0xFF])
            * (rec["size"] // 4 + 1),
        ) + suffix

    def plan_for(self, rec=None, blob=None, name="unit", path=None):
        rec = rec or self.standalone
        blob = blob if blob is not None else self.tinted(rec)
        out = path or os.path.join(self.dir, f"{name}.ps3patch")
        return build_plan(self.ar, [(int(rec["entry_hash"], 16), blob, rec["id"])],
                          out, plan_name=name, log=lambda *a: None)

    def assert_plan_ok(self, plan, result):
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["problems"], [])

    # -- plan file ----------------------------------------------------------
    def test_build_and_load_roundtrip(self):
        plan = self.plan_for()
        self.assertTrue(os.path.isfile(plan.path))
        with open(plan.path, "rb") as f:
            raw = f.read()
        self.assertEqual(raw[:8], PLAN_MAGIC)
        self.assertEqual(raw[-16:-8], b"PS3TPND1")
        self.assertEqual(plan.archive, "art.rcf")
        self.assertEqual(plan.index_count, self.ar.info.count)
        self.assertEqual(plan.archive_size, os.path.getsize(self.archive))
        self.assertEqual(len(plan.patches), 1)

        loaded = load_plan(plan.path)
        self.assertEqual(loaded.name, "unit")
        self.assertEqual(loaded.archive, plan.archive)
        self.assertEqual(loaded.flags, 1)
        p = loaded.patches[0]
        self.assertEqual(p.entry_hash, int(self.standalone["entry_hash"], 16))
        self.assertEqual(p.old_size, self.ar.entry(p.entry_hash).size)
        self.assertEqual(p.new_size, len(p.blob))
        self.assertEqual(p.label, self.standalone["id"])
        self.assertEqual(p.new_crc32, __import__("zlib").crc32(p.blob) & 0xFFFFFFFF)

    def test_plan_rejects_corruption_and_truncation(self):
        plan = self.plan_for()
        with open(plan.path, "rb") as f:
            raw = bytearray(f.read())
        bad = os.path.join(self.dir, "bad.ps3patch")
        raw[70] ^= 0xFF
        with open(bad, "wb") as f:
            f.write(raw)
        with self.assertRaisesRegex(PlanError, "checksum"):
            load_plan(bad)

        trunc = os.path.join(self.dir, "trunc.ps3patch")
        with open(trunc, "wb") as f:
            f.write(raw[:40])
        with self.assertRaises(PlanError):
            load_plan(trunc)

        with open(trunc, "wb") as f:
            f.write(b"BADMAGIC" + b"\x00" * 200)
        with self.assertRaisesRegex(PlanError, "magic"):
            load_plan(trunc)

    def test_plan_rejects_duplicate_and_oversized_blob(self):
        h = int(self.standalone["entry_hash"], 16)
        blob = self.tinted(self.standalone)
        with self.assertRaisesRegex(PlanError, "duplicate"):
            build_plan(self.ar, [(h, blob, "a"), (h, blob, "b")],
                       os.path.join(self.dir, "dup.ps3patch"),
                       plan_name="dup", log=lambda *a: None)
        e = self.ar.entry(h)
        too_big = blob + bytes(e.slot_size)
        with self.assertRaisesRegex(PlanError, "slot"):
            build_plan(self.ar, [(h, too_big, "big")],
                       os.path.join(self.dir, "big.ps3patch"),
                       plan_name="big", log=lambda *a: None)
        with self.assertRaisesRegex(PlanError, "empty"):
            build_plan(self.ar, [(h, b"", "empty")],
                       os.path.join(self.dir, "empty.ps3patch"),
                       plan_name="empty", log=lambda *a: None)

    def test_embedded_rz_patch_roundtrip(self):
        blob = self.tinted(self.embedded)
        h, stored = make_patch(self.ar, {**self.embedded,
                                         "entry_hash": int(self.embedded["entry_hash"], 16)},
                               blob)
        self.assertLessEqual(len(stored), self.ar.entry(h).slot_size)
        plan = self.plan_for(self.embedded, stored, name="embedded")
        loaded = load_plan(plan.path)
        res = verify_plan(loaded, self.archive)
        self.assert_plan_ok(loaded, res)
        self.assertEqual(res["pending"], 1)

    # -- verification -------------------------------------------------------
    def test_verify_distinguishes_pending_already_and_mismatch(self):
        plan = self.plan_for()
        res = verify_plan(plan, self.archive)
        self.assert_plan_ok(plan, res)
        self.assertEqual((res["pending"], res["already"]), (1, 0))

        apply_plan(plan, self.archive, journal_dir=self.journals, log=lambda *a: None)
        res = verify_plan(plan, self.archive)
        self.assert_plan_ok(plan, res)
        self.assertEqual((res["pending"], res["already"]), (0, 1))

        # A different game build has a different archive size.
        other = os.path.join(self.dir, "other.rcf")
        with open(self.archive, "rb") as f:
            data = bytearray(f.read())
        with open(other, "wb") as f:
            f.write(data + b"\x00" * 0x800)
        res = verify_plan(plan, other)
        self.assertFalse(res["ok"])
        self.assertTrue(any("archive size" in p for p in res["problems"]))

        # Same size, modified original bytes.
        with open(self.archive, "r+b") as f:
            e = self.ar.entry(int(self.standalone["entry_hash"], 16))
            f.seek(e.offset + e.size - 1)
            f.write(b"\x42")
        res = verify_plan(plan, self.archive)
        self.assertFalse(res["ok"])
        self.assertTrue(any("original data differs" in p for p in res["problems"]))

    def test_verify_rejects_wrong_name_and_missing_entry(self):
        plan = self.plan_for()
        wrong = os.path.join(self.dir, "cached.rcf")
        with open(self.archive, "rb") as src, open(wrong, "wb") as dst:
            dst.write(src.read())
        res = verify_plan(plan, wrong)
        self.assertFalse(res["ok"])
        self.assertTrue(any("targets art.rcf" in p for p in res["problems"]))

        # Remove an entry from the index so the plan cannot resolve it.
        with open(self.archive, "r+b") as f:
            e = self.ar.entry(int(self.standalone["entry_hash"], 16))
            f.seek(self.ar.info.index_offset + e.index * 12)
            f.write(struct.pack(">III", 0xFFFFFFFF, e.offset, e.size))
        res = verify_plan(plan, self.archive)
        self.assertFalse(res["ok"])
        self.assertTrue(any("entry not present" in p for p in res["problems"]))

    # -- application and rollback ------------------------------------------
    def test_apply_is_in_place_and_idempotent(self):
        plan = self.plan_for()
        before_size = os.path.getsize(self.archive)
        res = apply_plan(plan, self.archive, journal_dir=self.journals,
                         log=lambda *a: None)
        self.assertEqual(res["applied"], 1)
        self.assertEqual(os.path.getsize(self.archive), before_size)

        e = self.ar.entry(int(self.standalone["entry_hash"], 16))
        with open(self.archive, "rb") as f:
            f.seek(e.offset)
            self.assertEqual(f.read(e.size), plan.patches[0].blob)
            f.seek(self.ar.info.index_offset + e.index * 12 + 8)
            self.assertEqual(struct.unpack(">I", f.read(4))[0], len(plan.patches[0].blob))

        res = apply_plan(plan, self.archive, journal_dir=self.journals,
                         log=lambda *a: None)
        self.assertEqual((res["applied"], res["already"]), (0, 1))

    def test_dry_run_writes_nothing(self):
        plan = self.plan_for()
        before = file_sha256(self.archive)
        res = apply_plan(plan, self.archive, journal_dir=self.journals,
                         dry_run=True, log=lambda *a: None)
        self.assertEqual(res["applied"], 0)
        self.assertIsNone(res["journal"])
        self.assertEqual(file_sha256(self.archive), before)
        self.assertFalse(os.path.exists(self.journals))

    def test_journal_restores_bytes_exactly(self):
        # Corrupt the slot padding first: restore must put those bytes back too.
        e = self.ar.entry(int(self.standalone["entry_hash"], 16))
        pad = e.slot_size - e.size
        self.assertGreater(pad, 0)
        with open(self.archive, "r+b") as f:
            f.seek(e.offset + e.size)
            f.write(b"\xA5" * pad)
        before = file_sha256(self.archive)

        plan = self.plan_for()
        apply_plan(plan, self.archive, journal_dir=self.journals, log=lambda *a: None)
        self.assertNotEqual(file_sha256(self.archive), before)

        journal = os.path.join(self.journals, "art.rcf.journal")
        self.assertTrue(os.path.isfile(journal))
        info = journal_info(journal)
        self.assertEqual(info["archive"], "art.rcf")
        self.assertEqual(info["records"], 1)
        self.assertEqual(info["bytes"], e.slot_size)

        res = restore_journal(journal, self.archive, log=lambda *a: None)
        self.assertEqual(res["restored"], 1)
        self.assertEqual(file_sha256(self.archive), before)
        self.assertFalse(os.path.exists(journal))

    def test_journal_accumulates_and_restores_multiple_patches(self):
        blob_a = self.tinted(self.standalone, seed=0x21)
        plan_a = self.plan_for(self.standalone, blob_a, name="a")
        blob_b = self.tinted(self.embedded, seed=0x31)
        _, stored_b = make_patch(self.ar, {**self.embedded,
                                           "entry_hash": int(self.embedded["entry_hash"], 16)},
                                 blob_b)
        plan_b = self.plan_for(self.embedded, stored_b, name="b")
        before = file_sha256(self.archive)

        apply_plan(plan_a, self.archive, journal_dir=self.journals, log=lambda *a: None)
        apply_plan(plan_b, self.archive, journal_dir=self.journals, log=lambda *a: None)
        journal = os.path.join(self.journals, "art.rcf.journal")
        self.assertEqual(journal_info(journal)["records"], 2)
        self.assertEqual(len(find_journals(self.journals)), 1)

        restore_journal(journal, self.archive, log=lambda *a: None)
        self.assertEqual(file_sha256(self.archive), before)

    def test_per_session_journals_restore_newest_first(self):
        from ps3texmod.plan import journal_scan, restore_journals

        plan_a = self.plan_for(self.standalone, self.tinted(self.standalone, seed=0x41),
                               name="a")
        blob_b = self.tinted(self.embedded, seed=0x51)
        _, stored_b = make_patch(self.ar, {**self.embedded,
                                           "entry_hash": int(self.embedded["entry_hash"], 16)},
                                 blob_b)
        plan_b = self.plan_for(self.embedded, stored_b, name="b")
        before = file_sha256(self.archive)

        apply_plan(plan_a, self.archive, journal_dir=self.journals, log=lambda *a: None)
        os.replace(os.path.join(self.journals, "art.rcf.journal"),
                   os.path.join(self.journals, "art.rcf.1.journal"))
        apply_plan(plan_b, self.archive, journal_dir=self.journals, log=lambda *a: None)
        os.replace(os.path.join(self.journals, "art.rcf.journal"),
                   os.path.join(self.journals, "art.rcf.2.journal"))
        self.assertNotEqual(file_sha256(self.archive), before)

        scanned = journal_scan(self.journals, "art.rcf")
        self.assertEqual([j["index"] for j in scanned], [2, 1])
        self.assertTrue(all(j["archive"] == "art.rcf" for j in scanned))
        # Other archives are not matched.
        self.assertEqual(journal_scan(self.journals, "cached.rcf"), [])

        res = restore_journals(self.journals, self.archive, log=lambda *a: None)
        self.assertEqual((res["files"], res["restored"]), (2, 2))
        self.assertEqual(file_sha256(self.archive), before)
        self.assertEqual(journal_scan(self.journals, "art.rcf"), [])

    def test_restore_journals_refuses_corrupt_before_writing(self):
        from ps3texmod.plan import restore_journals

        plan = self.plan_for()
        apply_plan(plan, self.archive, journal_dir=self.journals, log=lambda *a: None)
        good = os.path.join(self.journals, "art.rcf.1.journal")
        os.replace(os.path.join(self.journals, "art.rcf.journal"), good)
        with open(good, "rb") as f:
            raw = bytearray(f.read())
        raw[len(raw) // 2] ^= 0xFF
        with open(os.path.join(self.journals, "art.rcf.2.journal"), "wb") as f:
            f.write(raw)

        before = file_sha256(self.archive)
        with self.assertRaisesRegex(PlanError, "unreadable journal"):
            restore_journals(self.journals, self.archive, log=lambda *a: None)
        # Nothing was touched and both journals were kept.
        self.assertEqual(file_sha256(self.archive), before)
        self.assertEqual(len(os.listdir(self.journals)), 2)

    def test_restore_refuses_changed_archive(self):
        plan = self.plan_for()
        apply_plan(plan, self.archive, journal_dir=self.journals, log=lambda *a: None)
        journal = os.path.join(self.journals, "art.rcf.journal")
        with open(self.archive, "ab") as f:
            f.write(b"\x00" * 0x800)
        with self.assertRaisesRegex(PlanError, "size"):
            restore_journal(journal, self.archive, log=lambda *a: None)
        self.assertTrue(os.path.exists(journal))

    def test_journal_corruption_is_detected(self):
        plan = self.plan_for()
        apply_plan(plan, self.archive, journal_dir=self.journals, log=lambda *a: None)
        journal = os.path.join(self.journals, "art.rcf.journal")
        with open(journal, "rb") as f:
            raw = bytearray(f.read())
        self.assertEqual(bytes(raw[:8]), JOURNAL_MAGIC)
        raw[len(raw) // 2] ^= 0xFF
        with open(journal, "wb") as f:
            f.write(raw)
        with self.assertRaisesRegex(PlanError, "checksum"):
            journal_info(journal)

    # -- pack integration ---------------------------------------------------
    def test_plan_from_pack(self):
        blob = self.tinted(self.standalone, seed=0x5A)
        dds_path = os.path.join(self.dir, "replacement.dds")
        with open(dds_path, "wb") as f:
            f.write(blob)
        rec = resolve_token({"art.rcf": self.ar},
                            f"art.rcf:{int(self.standalone['entry_hash'], 16):08X}")
        rec["source_file"] = dds_path
        pack_path = os.path.join(self.dir, "tint.zip")
        build_pack(pack_path, [rec], name="Tint Pack", version="1.2.3",
                   game_dir=self.game, log=lambda *a: None)

        out = os.path.join(self.dir, "from-pack.ps3patch")
        plan = plan_from_pack(pack_path, self.game, out, log=lambda *a: None)
        self.assertEqual(plan.name, "Tint Pack 1.2.3")
        self.assertEqual(plan.archive, "art.rcf")
        self.assertEqual(plan.pack_id, "tint-pack")

        loaded = load_plan(out)
        res = verify_plan(loaded, self.archive)
        self.assert_plan_ok(loaded, res)
        self.assertEqual(res["pending"], 1)

    def test_plan_from_pack_rejects_multi_archive_pack(self):
        # A pack touching two archives must be split into one plan per archive.
        from ps3texmod.rcf import build_archive
        from tests.helpers import default_assets

        cached_path = os.path.join(self.usrdir, "cached.rcf")
        build_archive(cached_path, default_assets())
        cached = RcfArchive(cached_path)
        recs = []
        for ar, path in ((self.ar, self.archive), (cached, cached_path)):
            rec = resolve_token({os.path.basename(path): ar},
                                f"{os.path.basename(path)}:"
                                f"{int(self.standalone['entry_hash'], 16):08X}")
            blob = self.tinted(self.standalone, seed=0x77)
            dds_path = os.path.join(self.dir, os.path.basename(path) + ".dds")
            with open(dds_path, "wb") as f:
                f.write(blob)
            rec["source_file"] = dds_path
            recs.append(rec)
        pack_path = os.path.join(self.dir, "multi.zip")
        build_pack(pack_path, recs, name="Multi", game_dir=self.game,
                   log=lambda *a: None)
        with self.assertRaisesRegex(PlanError, "one plan per archive"):
            plan_from_pack(pack_path, self.game,
                           os.path.join(self.dir, "x.ps3patch"),
                           log=lambda *a: None)


class TestRelocate(unittest.TestCase):
    """Re-anchor a plan onto a rebuilt archive with identical texture bytes."""

    TARGET = r"\art\test\standalone.dds"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.game = make_game(self.dir, assets=[
            (self.TARGET, make_dds(64, 64, b"DXT1"), False),
        ])
        self.usrdir = os.path.join(self.game, "PS3_GAME", "USRDIR")
        self.archive = os.path.join(self.usrdir, "art.rcf")
        self.ar = RcfArchive(self.archive)
        self.entry = self.ar.entry(self.TARGET)
        self.new = make_dds(64, 64, b"DXT1", fill=b"\x10\x20\x30\x40" * 1024)
        self.plan_path = os.path.join(self.dir, "relocate.ps3patch")
        self.plan = build_plan(self.ar, [(self.entry.hash, self.new, "relocate")],
                               self.plan_path, plan_name="Relocate",
                               log=lambda *a: None)

    def tearDown(self):
        self.tmp.cleanup()

    def repack(self, name, assets):
        root = os.path.join(self.dir, name)
        usrdir = os.path.join(root, "PS3_GAME", "USRDIR")
        os.makedirs(usrdir)
        path = os.path.join(usrdir, "art.rcf")
        build_archive(path, assets)
        return path

    def other_build(self, target=None, extra=True):
        assets = [(self.TARGET, target or make_dds(64, 64, b"DXT1"), False)]
        if extra:
            assets.insert(0, (r"\art\extra\aaa.dds", make_dds(32, 32, b"DXT1"), False))
            assets.append((r"\art\extra\zzz.dds",
                           make_dds(128, 32, b"DXT5", mips=2), False))
        return self.repack("other" + str(len(os.listdir(self.dir))), assets)

    # -- verification -------------------------------------------------------
    def test_exact_verify_rejects_other_build(self):
        other = self.other_build()
        self.assertNotEqual(os.path.getsize(other), os.path.getsize(self.archive))
        exact = verify_plan(self.plan, other)
        self.assertFalse(exact["ok"])
        self.assertEqual(exact["pending"], 0)

    def test_relocate_verify_accepts_other_build(self):
        other = self.other_build()
        res = verify_plan(self.plan, other, relocate=True)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["pending"], 1)
        self.assertEqual(res["already"], 0)
        e = RcfArchive(other).entry(self.entry.hash)
        self.assertGreaterEqual(res["relocated"],
                                1 if (e.index, e.offset, e.slot_size) !=
                                (self.plan.patches[0].index, self.plan.patches[0].offset,
                                 self.plan.patches[0].slot_size) else 0)

    def test_relocate_verify_rejects_changed_texture(self):
        other = self.other_build(target=make_dds(64, 64, b"DXT1", fill=b"\xAA" * 2048))
        res = verify_plan(self.plan, other, relocate=True)
        self.assertFalse(res["ok"])
        self.assertTrue(any("differs" in p for p in res["problems"]), res["problems"])

    def test_relocate_verify_rejects_missing_entry(self):
        other = self.repack("missing", [(r"\art\extra\aaa.dds",
                                         make_dds(32, 32, b"DXT1"), False)])
        res = verify_plan(self.plan, other, relocate=True)
        self.assertFalse(res["ok"])
        self.assertTrue(any("not present" in p for p in res["problems"]), res["problems"])

    # -- apply / restore ----------------------------------------------------
    def test_apply_and_restore_other_build(self):
        other = self.other_build()
        before = file_sha256(other)
        journals = os.path.join(self.dir, "journals")
        res = apply_plan(self.plan, other, journal_dir=journals, relocate=True,
                         log=lambda *a: None)
        self.assertEqual(res["applied"], 1)

        ar2 = RcfArchive(other)
        e2 = ar2.entry(self.entry.hash)
        self.assertEqual(ar2.read_stored(e2), self.new)
        self.assertEqual(e2.size, len(self.new))
        self.assertTrue(verify_plan(self.plan, other, relocate=True)["already"] == 1)

        restore_journal(res["journal"], other, log=lambda *a: None)
        self.assertEqual(file_sha256(other), before)
        self.assertFalse(os.path.exists(res["journal"]))

    def test_apply_refuses_changed_texture(self):
        other = self.other_build(target=make_dds(64, 64, b"DXT1", fill=b"\xAA" * 2048))
        before = file_sha256(other)
        with self.assertRaises(PlanError):
            apply_plan(self.plan, other, journal_dir=os.path.join(self.dir, "j"),
                       relocate=True, log=lambda *a: None)
        self.assertEqual(file_sha256(other), before)

    def test_exact_apply_still_rejects_other_build(self):
        other = self.other_build()
        before = file_sha256(other)
        with self.assertRaises(PlanError):
            apply_plan(self.plan, other, journal_dir=os.path.join(self.dir, "j"),
                       log=lambda *a: None)
        self.assertEqual(file_sha256(other), before)


class TestRelocateP3D(unittest.TestCase):
    """Re-anchoring an embedded (RZ-compressed P3D) texture target."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.game = make_game(self.dir)          # default assets: dds + p3d + txt
        self.usrdir = os.path.join(self.game, "PS3_GAME", "USRDIR")
        self.archive = os.path.join(self.usrdir, "art.rcf")
        self.ar = RcfArchive(self.archive)
        self.rec = {r["container"]: r for r in scan_archive(self.ar)}["p3d"]
        self.replacement = make_dds(
            self.rec["width"], self.rec["height"], self.rec["format"].encode(),
            mips=self.rec["mipmaps"], fill=b"\x40\x30\x20\x10" * 1024)
        self.hash, self.stored = make_patch(self.ar, self.rec, self.replacement)
        self.plan_path = os.path.join(self.dir, "p3d.ps3patch")
        self.plan = build_plan(self.ar, [(self.hash, self.stored, "p3d embedded")],
                               self.plan_path, plan_name="P3D relocate",
                               log=lambda *a: None)

    def tearDown(self):
        self.tmp.cleanup()

    def other_build(self):
        from tests.helpers import default_assets
        root = os.path.join(self.dir, "other")
        usrdir = os.path.join(root, "PS3_GAME", "USRDIR")
        os.makedirs(usrdir)
        path = os.path.join(usrdir, "art.rcf")
        build_archive(path, default_assets() + [
            (r"\art\extra\aaa.dds", make_dds(32, 32, b"DXT1"), False),
        ])
        return path

    def test_p3d_plan_relocates_and_restores(self):
        other = self.other_build()
        res = verify_plan(self.plan, other, relocate=True)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["pending"], 1)

        before = file_sha256(other)
        journals = os.path.join(self.dir, "journals")
        out = apply_plan(self.plan, other, journal_dir=journals, relocate=True,
                         log=lambda *a: None)
        self.assertEqual(out["applied"], 1)
        self.assertEqual(RcfArchive(other).read_stored(self.hash), self.stored)
        restore_journal(out["journal"], other, log=lambda *a: None)
        self.assertEqual(file_sha256(other), before)


if __name__ == "__main__":
    unittest.main()
