"""USRDIR discovery for disc, package-install and bare-folder layouts.

Prototype exists as a disc / JB folder (``<game>/PS3_GAME/USRDIR``) and as a
PSN package install (``<game>/USRDIR``, e.g. ``NPUB30389``).  Every PC command
that takes ``--game-dir`` must accept both, plus the USRDIR itself.
"""

import argparse
import os
import tempfile
import unittest

from ps3texmod.catalog import find_archives, scan_archive
from ps3texmod.cli import _resolve_archive
from ps3texmod.installer import install, load_state, uninstall_all, usrdir
from ps3texmod.pack import build_pack, resolve_token
from ps3texmod.plan import PlanError, plan_from_pack, verify_plan
from ps3texmod.rcf import RcfArchive
from ps3texmod.util import game_usrdir, resolve_archive
from tests.helpers import make_dds, make_game


def flatten(game_dir: str) -> str:
    """Turn ``<game>/PS3_GAME/USRDIR`` into ``<game>/USRDIR`` in place."""
    os.rename(os.path.join(game_dir, "PS3_GAME", "USRDIR"),
              os.path.join(game_dir, "USRDIR"))
    os.rmdir(os.path.join(game_dir, "PS3_GAME"))
    return game_dir


def disc_assets():
    """One standalone DDS so a replacement pack can be built."""
    return [(r"\art\test\standalone.dds", make_dds(64, 64, b"DXT1"), False)]


class LayoutCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def disc_game(self):
        return make_game(self.dir, assets=disc_assets())

    def package_game(self):
        return flatten(self.disc_game())

    def pack(self, game_dir: str) -> str:
        ar = RcfArchive(os.path.join(game_usrdir(game_dir), "art.rcf"))
        entry = ar.entry(r"\art\test\standalone.dds")
        rec = resolve_token({ar.name: ar}, "art.rcf:%08X" % entry.hash)
        rec["source_file"] = os.path.join(self.dir, "repl.dds")
        with open(rec["source_file"], "wb") as f:
            f.write(make_dds(64, 64, b"DXT1", fill=b"\x01\x02\x03\x04" * 1024))
        path = os.path.join(self.dir, "pack.zip")
        build_pack(path, [rec], name="Layout test", game_dir=game_dir,
                   log=lambda *a: None)
        return path

    # -- path helpers -------------------------------------------------------
    def test_usrdir_disc_root(self):
        game = self.disc_game()
        self.assertEqual(game_usrdir(game), os.path.join(game, "PS3_GAME", "USRDIR"))

    def test_usrdir_package_root(self):
        game = self.package_game()
        self.assertEqual(game_usrdir(game), os.path.join(game, "USRDIR"))
        self.assertEqual(usrdir(game), os.path.join(game, "USRDIR"))

    def test_usrdir_itself(self):
        game = self.package_game()
        flat = os.path.join(game, "USRDIR")
        self.assertEqual(game_usrdir(flat), flat)

    def test_usrdir_bare_folder(self):
        self.assertEqual(game_usrdir(self.dir), self.dir)
        self.assertIsNone(resolve_archive(self.dir, "art.rcf"))

    def test_find_archives_each_layout(self):
        disc = self.disc_game()
        self.assertEqual([os.path.basename(p) for p in find_archives(disc)], ["art.rcf"])

        other = tempfile.TemporaryDirectory()
        try:
            pkg = flatten(make_game(other.name, assets=disc_assets()))
            self.assertEqual([os.path.basename(p) for p in find_archives(pkg)], ["art.rcf"])
            self.assertEqual([os.path.basename(p) for p in find_archives(os.path.join(pkg, "USRDIR"))], ["art.rcf"])
        finally:
            other.cleanup()

    # -- commands -----------------------------------------------------------
    def test_cli_resolve_archive_package_root(self):
        game = self.package_game()
        args = argparse.Namespace(archive=None, game_dir=game)
        self.assertEqual(_resolve_archive("art.rcf", args),
                         os.path.join(game, "USRDIR", "art.rcf"))

    def test_plan_from_pack_package_root(self):
        game = self.package_game()
        plan_path = os.path.join(self.dir, "pkg.ps3patch")
        plan = plan_from_pack(self.pack(game), game, plan_path, log=lambda *a: None)
        self.assertTrue(os.path.isfile(plan.path))
        result = verify_plan(plan, os.path.join(game, "USRDIR", "art.rcf"))
        self.assertTrue(result["ok"], result["problems"])

    def test_plan_from_pack_disc_root(self):
        game = self.disc_game()
        plan_path = os.path.join(self.dir, "disc.ps3patch")
        plan = plan_from_pack(self.pack(game), game, plan_path, log=lambda *a: None)
        result = verify_plan(plan, os.path.join(game, "PS3_GAME", "USRDIR", "art.rcf"))
        self.assertTrue(result["ok"], result["problems"])

    def test_plan_from_pack_missing_archive(self):
        empty = os.path.join(self.dir, "empty")
        os.makedirs(empty)
        with self.assertRaises(PlanError):
            plan_from_pack(self.pack(self.disc_game()), empty,
                           os.path.join(self.dir, "bad.ps3patch"), log=lambda *a: None)

    def test_install_and_restore_package_root(self):
        game = self.package_game()
        archive = os.path.join(game, "USRDIR", "art.rcf")
        with open(archive, "rb") as f:
            before = f.read()
        res = install(self.pack(game), game, log=lambda *a: None)
        self.assertEqual(res["textures"], 1)
        self.assertTrue(load_state(game)["packs"][0]["enabled"])
        with open(archive, "rb") as f:
            self.assertNotEqual(f.read(), before)
        uninstall_all(game, log=lambda *a: None)
        with open(archive, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertTrue(os.path.isdir(os.path.join(game, ".ps3texmod", "backups")))

    def test_scan_package_root(self):
        game = self.package_game()
        ar = RcfArchive(os.path.join(game, "USRDIR", "art.rcf"))
        self.assertTrue(any(r["container"] == "dds" for r in scan_archive(ar)))


if __name__ == "__main__":
    unittest.main()
