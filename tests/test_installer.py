import os
import tempfile
import unittest

from ps3texmod.catalog import extract_texture, scan_archive
from ps3texmod.installer import (InstallError, MOD_DIR, install, load_state,
                                 remove, set_enabled, status, uninstall_all,
                                 usrdir)
from ps3texmod.pack import build_pack, resolve_token
from ps3texmod.rcf import RcfArchive
from ps3texmod.util import sha256_bytes, sha256_file
from tests.helpers import file_sha256, make_dds, make_game


class InstallerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.game = make_game(self.dir)
        self.archive = os.path.join(usrdir(self.game), "art.rcf")
        self.orig_sha = sha256_file(self.archive)
        self.ar = RcfArchive(self.archive)
        self.target = self.ar.entry(r"\art\test\standalone.dds")

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def make_pack(self, name, fill=b"\x77\x66\x55\x44" * (2048 // 4),
                  pack_id=None):
        new = make_dds(64, 64, b"DXT1", fill=fill)
        src = os.path.join(self.dir, name.replace(" ", "_") + ".dds")
        with open(src, "wb") as f:
            f.write(new)
        ar = RcfArchive(self.archive)
        rec = resolve_token({"art.rcf": ar}, f"art.rcf:{self.target.hash:08X}")
        rec["source_file"] = src
        path = os.path.join(self.dir, name.replace(" ", "_") + ".zip")
        build_pack(path, [rec], name=name, game_dir=self.game,
                   pack_id=pack_id, log=lambda *a: None)
        return path, new

    def current_texture(self):
        ar = RcfArchive(self.archive)
        recs = {r["container"]: r for r in scan_archive(ar)}
        return extract_texture(ar, recs["dds"])

    def base_path(self, archive="art.rcf"):
        return os.path.join(self.game, MOD_DIR, "backups", archive + ".base")

    # -- tests -----------------------------------------------------------
    def test_install_applies_and_backs_up(self):
        pack, new = self.make_pack("Pack A")
        res = install(pack, self.game, log=lambda *a: None)
        self.assertEqual(res["textures"], 1)
        self.assertEqual(res["packs"], 1)
        self.assertEqual(self.current_texture(), new)
        self.assertTrue(os.path.isfile(self.base_path()))
        self.assertEqual(sha256_file(self.base_path()), self.orig_sha)

        st = status(self.game)
        self.assertEqual(len(st["packs"]), 1)
        self.assertTrue(st["packs"][0]["enabled"])
        self.assertEqual(len(st["backups"]), 1)
        self.assertTrue(st["backups"][0]["modified"])

        # state.json survives a reload and stays JSON-only
        raw = load_state(self.game)
        self.assertEqual(raw["format"], "ps3texmod-state/1")
        self.assertEqual(len(raw["packs"]), 1)

    def test_disable_enable_remove(self):
        pack, new = self.make_pack("Pack A")
        install(pack, self.game, log=lambda *a: None)

        set_enabled(self.game, "pack-a", False, log=lambda *a: None)
        self.assertEqual(sha256_file(self.archive), self.orig_sha)
        self.assertFalse(status(self.game)["packs"][0]["enabled"])
        self.assertFalse(status(self.game)["backups"][0]["modified"])

        set_enabled(self.game, "pack-a", True, log=lambda *a: None)
        self.assertEqual(self.current_texture(), new)

        remove(self.game, "pack-a", log=lambda *a: None)
        self.assertEqual(sha256_file(self.archive), self.orig_sha)
        self.assertEqual(status(self.game)["packs"], [])
        self.assertFalse(os.path.isfile(
            os.path.join(self.game, MOD_DIR, "packs", "pack-a.zip")))

    def test_uninstall_all(self):
        pack_a, _ = self.make_pack("Pack A")
        pack_b, _ = self.make_pack("Pack B")
        install(pack_a, self.game, log=lambda *a: None)
        install(pack_b, self.game, log=lambda *a: None)
        self.assertNotEqual(sha256_file(self.archive), self.orig_sha)
        uninstall_all(self.game, log=lambda *a: None)
        self.assertEqual(sha256_file(self.archive), self.orig_sha)
        self.assertEqual(status(self.game)["packs"], [])

    def test_stacking_last_wins(self):
        pack_a, new_a = self.make_pack("Pack A", fill=b"\x11" * 2048)
        pack_b, new_b = self.make_pack("Pack B", fill=b"\x22" * 2048)
        install(pack_a, self.game, log=lambda *a: None)
        install(pack_b, self.game, log=lambda *a: None)
        self.assertEqual(self.current_texture(), new_b)

        set_enabled(self.game, "pack-b", False, log=lambda *a: None)
        self.assertEqual(self.current_texture(), new_a)

        set_enabled(self.game, "pack-a", False, log=lambda *a: None)
        self.assertEqual(sha256_file(self.archive), self.orig_sha)

    def test_reinstall_replaces_entry(self):
        pack, new = self.make_pack("Pack A")
        install(pack, self.game, log=lambda *a: None)
        install(pack, self.game, log=lambda *a: None)
        self.assertEqual(len(status(self.game)["packs"]), 1)
        self.assertEqual(self.current_texture(), new)

    def test_install_disabled(self):
        pack, _ = self.make_pack("Pack A")
        res = install(pack, self.game, enable=False, log=lambda *a: None)
        self.assertFalse(res["enabled"])
        self.assertEqual(sha256_file(self.archive), self.orig_sha)
        self.assertEqual(status(self.game)["packs"][0]["enabled"], False)

    def test_wrong_game_build_rejected_and_rolled_back(self):
        pack, _ = self.make_pack("Pack A")
        # simulate a different build / already modded archive
        self.ar.apply_patches({self.target.hash: make_dds(
            64, 64, b"DXT1", fill=b"\x99" * 2048)})
        tampered_sha = sha256_file(self.archive)
        with self.assertRaises(InstallError):
            install(pack, self.game, log=lambda *a: None)
        st = status(self.game)
        self.assertEqual(st["packs"], [], "failed install must roll back state")
        self.assertFalse(os.path.isfile(self.base_path()),
                         "no pristine backup may be taken from a modded archive")
        self.assertEqual(sha256_file(self.archive), tampered_sha)
        self.assertFalse(os.path.isfile(
            os.path.join(self.game, MOD_DIR, "packs", "pack-a.zip")))

    def test_corrupt_backup_detected(self):
        pack, _ = self.make_pack("Pack A")
        install(pack, self.game, log=lambda *a: None)
        with open(self.base_path(), "r+b") as f:
            f.seek(0)
            f.write(b"XXXX")
        with self.assertRaises(InstallError):
            set_enabled(self.game, "pack-a", False, log=lambda *a: None)
        # enabled flag rolled back to what was on disk before the attempt
        self.assertTrue(status(self.game)["packs"][0]["enabled"])

    def test_remove_unknown_pack(self):
        with self.assertRaises(InstallError):
            remove(self.game, "nope", log=lambda *a: None)
        with self.assertRaises(InstallError):
            set_enabled(self.game, "nope", True, log=lambda *a: None)

    def test_backup_created_once(self):
        pack_a, _ = self.make_pack("Pack A")
        pack_b, _ = self.make_pack("Pack B", fill=b"\x33" * 2048)
        install(pack_a, self.game, log=lambda *a: None)
        install(pack_b, self.game, log=lambda *a: None)
        st = load_state(self.game)
        self.assertEqual(len(st["backups"]), 1)
        self.assertEqual(sha256_file(self.base_path()), self.orig_sha)

    def test_embedded_texture_pack(self):
        # target an embedded DDS inside the synthetic P3D (forces a rebuild)
        ar = RcfArchive(self.archive)
        p3d = ar.entry(r"\art\test\embedded.p3d.rz")
        payload = ar.read_payload(p3d)
        off = payload.find(b"DDS ")
        rec = resolve_token({"art.rcf": ar}, f"art.rcf:{p3d.hash:08X}:{off:08X}")
        new = make_dds(64, 64, b"DXT5", mips=2, fill=b"\x55" * 5120)
        src = os.path.join(self.dir, "embedded.dds")
        with open(src, "wb") as f:
            f.write(new)
        rec["source_file"] = src
        pack = os.path.join(self.dir, "embedded.zip")
        build_pack(pack, [rec], name="Embedded", game_dir=self.game,
                   log=lambda *a: None)
        install(pack, self.game, log=lambda *a: None)
        ar2 = RcfArchive(self.archive)
        self.assertTrue(ar2.validate(deep=True)["ok"])
        recs = {r["container"]: r for r in scan_archive(ar2)}
        self.assertEqual(extract_texture(ar2, recs["p3d"]), new)
        set_enabled(self.game, "embedded", False, log=lambda *a: None)
        self.assertEqual(sha256_file(self.archive), self.orig_sha)


if __name__ == "__main__":
    unittest.main()
