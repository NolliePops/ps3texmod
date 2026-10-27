import contextlib
import io
import json
import os
import tempfile
import unittest

from ps3texmod.cli import main
from ps3texmod.rcf import RcfArchive
from ps3texmod.util import sha256_file
from tests.helpers import make_dds, make_game


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:  # pragma: no cover - defensive
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.game = make_game(self.dir)
        self.archive = os.path.join(self.game, "PS3_GAME", "USRDIR", "art.rcf")
        self.ar = RcfArchive(self.archive)
        self.target = self.ar.entry(r"\art\test\standalone.dds")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    # -- archive inspection ---------------------------------------------
    def test_info_list_stats_verify(self):
        rc, out, _ = run(["info", self.archive])
        self.assertEqual(rc, 0)
        self.assertIn("entries       3", out)
        self.assertIn("names mapped  True", out)

        rc, out, _ = run(["list", self.archive, "--kind", "dds"])
        self.assertEqual(rc, 0)
        self.assertIn("standalone.dds", out)
        self.assertIn("-- 1 entry", out)

        rc, out, _ = run(["stats", self.archive])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["entries"], 3)

        rc, out, _ = run(["verify", self.archive, "--deep"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["ok"])

        # corrupt a payload and check the non-zero exit code
        with open(self.archive, "r+b") as f:
            f.seek(self.target.offset)
            f.write(b"XXXX")
        rc, out, _ = run(["verify", self.archive, "--deep"])
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_extract_and_texture_info(self):
        out_dir = os.path.join(self.dir, "one")
        rc, out, _ = run(["extract", self.archive, "standalone.dds",
                          "-o", os.path.join(out_dir, "t.dds"), "--png"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "t.dds")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "t.png")))

        rc, out, _ = run(["texture-info", os.path.join(out_dir, "t.dds"), "--png"])
        self.assertEqual(rc, 0)
        d = json.loads(out[:out.rindex("}") + 1])
        self.assertEqual(d["width"], 64)
        self.assertEqual(d["format"], "DXT1")

        rc, out, _ = run(["extract-all", self.archive, "-o",
                          os.path.join(self.dir, "all"), "--kind", "dds"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "all",
                                                    "art", "test", "standalone.dds")))

    def test_textures_and_dump(self):
        rc, out, _ = run(["textures", self.archive, "--quiet"])
        self.assertEqual(rc, 0)
        self.assertIn("standalone.dds", out)
        self.assertIn("layer_a.dds", out)
        self.assertIn("-- 2 texture(s)", out)

        dump = os.path.join(self.dir, "dump")
        rc, out, _ = run(["dump", self.archive, "-o", dump, "--no-png", "--quiet"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(dump, "textures.json")))
        self.assertTrue(os.path.isfile(os.path.join(dump, "def.template")))
        with open(os.path.join(dump, "textures.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(len(meta["textures"]), 2)
        with open(os.path.join(dump, "def.template"), encoding="utf-8") as f:
            template = f.read()
        self.assertIn(f"art.rcf:{self.target.hash:08X}", template)

    def test_catalog(self):
        out_path = os.path.join(self.dir, "cat.json")
        rc, out, _ = run(["catalog", self.game, "-o", out_path,
                          "--archives", "art.rcf", "--quiet"])
        self.assertEqual(rc, 0)
        with open(out_path, encoding="utf-8") as f:
            cat = json.load(f)
        self.assertEqual(cat["format"], "ps3texmod-catalog/1")
        self.assertEqual(len(cat["archives"]), 1)
        self.assertEqual(len(cat["textures"]), 2)

    # -- pack + install lifecycle ---------------------------------------
    def test_pack_and_install_lifecycle(self):
        mod = self.write("mod.dds", make_dds(64, 64, b"DXT1",
                                             fill=b"\x5A" * 2048))
        definition = self.write(
            "def.txt",
            f"art.rcf:{self.target.hash:08X}|{mod}\n".encode())
        pack = os.path.join(self.dir, "cli-pack.zip")

        rc, out, _ = run(["pack-build", "--game-dir", self.game,
                          "--def", definition, "--out", pack,
                          "--name", "CLI Pack", "--author", "tests"])
        self.assertEqual(rc, 0, out)
        self.assertIn("built", out)

        rc, out, _ = run(["pack-info", pack])
        self.assertEqual(rc, 0)
        info = json.loads(out)
        self.assertEqual(info["id"], "cli-pack")
        self.assertEqual(info["replacements"], 1)

        rc, out, _ = run(["pack-validate", pack, "--game-dir", self.game])
        self.assertEqual(rc, 0)
        self.assertIn("pack OK", out)

        orig_sha = sha256_file(self.archive)
        rc, out, _ = run(["install", pack, "--game-dir", self.game])
        self.assertEqual(rc, 0, out)
        self.assertNotEqual(sha256_file(self.archive), orig_sha)

        rc, out, _ = run(["installed", "--game-dir", self.game])
        self.assertEqual(rc, 0)
        st = json.loads(out)
        self.assertEqual(len(st["packs"]), 1)
        self.assertTrue(st["backups"][0]["modified"])

        rc, _, _ = run(["disable", "cli-pack", "--game-dir", self.game])
        self.assertEqual(rc, 0)
        self.assertEqual(sha256_file(self.archive), orig_sha)

        rc, _, _ = run(["enable", "cli-pack", "--game-dir", self.game])
        self.assertEqual(rc, 0)
        self.assertNotEqual(sha256_file(self.archive), orig_sha)

        rc, _, _ = run(["remove", "cli-pack", "--game-dir", self.game])
        self.assertEqual(rc, 0)
        self.assertEqual(sha256_file(self.archive), orig_sha)

        rc, _, _ = run(["restore", "--game-dir", self.game])
        self.assertEqual(rc, 0)
        self.assertEqual(sha256_file(self.archive), orig_sha)

    def test_pack_validate_rejects_bad_pack(self):
        bad = os.path.join(self.dir, "bad.zip")
        with open(bad, "wb") as f:
            f.write(b"not a zip")
        rc, out, err = run(["pack-validate", bad])
        self.assertEqual(rc, 1)
        self.assertIn("INVALID", out + err)

    def test_error_paths(self):
        rc, _, _ = run(["texture-info", self.write("notdds.bin", b"hello")])
        self.assertEqual(rc, 1)

        rc, out, _ = run(["install", os.path.join(self.dir, "missing.zip"),
                          "--game-dir", self.game])
        self.assertEqual(rc, 1)

        rc, _, _ = run(["remove", "ghost", "--game-dir", self.game])
        self.assertEqual(rc, 1)

        rc, _, _ = run(["disable", "ghost", "--game-dir", self.game])
        self.assertEqual(rc, 1)

    def test_plan_commands(self):
        mod = self.write("planmod.dds", make_dds(64, 64, b"DXT1",
                                                 fill=b"\x3C" * 2048))
        definition = self.write(
            "plandef.txt",
            f"art.rcf:{self.target.hash:08X}|{mod}\n".encode())
        pack = os.path.join(self.dir, "plan-pack.zip")
        rc, out, _ = run(["pack-build", "--game-dir", self.game,
                          "--def", definition, "--out", pack,
                          "--name", "Plan Pack"])
        self.assertEqual(rc, 0, out)

        plan = os.path.join(self.dir, "cli.ps3patch")
        rc, out, _ = run(["plan-build", "--pack", pack, "--game-dir", self.game,
                          "--out", plan, "--name", "CLI Plan"])
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(plan))

        rc, out, _ = run(["plan-info", plan])
        self.assertEqual(rc, 0, out)
        info = json.loads(out)
        self.assertEqual(info["name"], "CLI Plan")
        self.assertEqual(info["archive"], "art.rcf")
        self.assertEqual(len(info["patches"]), 1)

        rc, out, _ = run(["plan-verify", plan, "--game-dir", self.game])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["pending"], 1)

        orig_sha = sha256_file(self.archive)
        journals = os.path.join(self.dir, "journals")
        rc, out, _ = run(["plan-apply", plan, "--game-dir", self.game,
                          "--journal-dir", journals])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out[out.index("{"):])["applied"], 1)
        self.assertNotEqual(sha256_file(self.archive), orig_sha)

        rc, out, _ = run(["plan-status", "--plan-dir", self.dir,
                          "--journal-dir", journals])
        self.assertEqual(rc, 0, out)
        st = json.loads(out)
        self.assertEqual(len(st["plans"]), 1)
        self.assertEqual(len(st["journals"]), 1)
        self.assertEqual(st["journals"][0]["archive"], "art.rcf")

        rc, out, _ = run(["plan-restore", "--plan", plan, "--game-dir", self.game,
                          "--journal-dir", journals])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out[out.index("{"):])["restored"], 1)
        self.assertEqual(sha256_file(self.archive), orig_sha)

        rc, out, _ = run(["plan-verify", plan, "--game-dir", self.game])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["pending"], 1)

        # Missing plan / bad arguments fail cleanly.
        rc, _, err = run(["plan-info", os.path.join(self.dir, "ghost.ps3patch")])
        self.assertEqual(rc, 1)
        self.assertIn("error", err)
        rc, _, err = run(["plan-verify", plan])
        self.assertEqual(rc, 1)
        self.assertIn("game-dir", err)

    def test_selftest_command(self):
        rc, out, _ = run(["selftest", "--quiet"])
        self.assertEqual(rc, 0, out)
        self.assertIn("passed", out.lower())


if __name__ == "__main__":
    unittest.main()
