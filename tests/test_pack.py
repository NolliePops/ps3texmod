import json
import os
import tempfile
import unittest
import zipfile

from ps3texmod.dds import parse_dds
from ps3texmod.pack import (CHECKSUMS, MANIFEST, PACK_FORMAT, PackError,
                            build_pack, load_pack, pack_info, parse_definition,
                            resolve_token, validate_against_game)
from ps3texmod.rcf import RcfArchive
from ps3texmod.util import sha256_bytes
from tests.helpers import default_assets, make_dds, make_game, make_p3d


class PackCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.game = make_game(self.dir)
        self.archive = os.path.join(self.game, "PS3_GAME", "USRDIR", "art.rcf")
        self.ar = RcfArchive(self.archive)

    def tearDown(self):
        self.tmp.cleanup()

    def replacement(self, width=64, height=64, fourcc=b"DXT1", mips=1,
                    fill=b"\x77\x66\x55\x44" * (2048 // 4)):
        return make_dds(width, height, fourcc, mips=mips, fill=fill)

    def resolve(self, token, source, **kw):
        rec = resolve_token({"art.rcf": self.ar}, token, **kw)
        rec["source_file"] = source
        return rec

    def write(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def build_pack(self, recs, name="Test Pack", path=None, **kw):
        path = path or os.path.join(self.dir, "pack.zip")
        manifest = build_pack(path, recs, name=name, game_dir=self.game,
                              log=lambda *a: None, **kw)
        return manifest, path


class TestResolveToken(PackCase):
    def test_standalone_by_hash(self):
        e = self.ar.entry(r"\art\test\standalone.dds")
        rec = resolve_token({"art.rcf": self.ar}, f"art.rcf:{e.hash:08X}")
        self.assertEqual(rec["entry_hash"], f"{e.hash:08X}")
        self.assertEqual(rec["offset_in_asset"], 0)
        self.assertEqual(rec["container"], "dds")
        self.assertEqual(rec["width"], 64)
        self.assertEqual(rec["format"], "DXT1")
        self.assertEqual(rec["original_sha256"],
                         sha256_bytes(self.ar.read_stored(e)))

    def test_embedded_by_hash_and_offset(self):
        e = self.ar.entry(r"\art\test\embedded.p3d.rz")
        payload = self.ar.read_payload(e)
        off = payload.find(b"DDS ")
        rec = resolve_token({"art.rcf": self.ar}, f"art.rcf:{e.hash:08X}:{off:08X}")
        self.assertEqual(rec["container"], "p3d")
        self.assertEqual(rec["offset_in_asset"], off)
        self.assertEqual(rec["format"], "DXT5")
        self.assertEqual(rec["mipmaps"], 2)

    def test_first_embedded_when_offset_omitted(self):
        e = self.ar.entry(r"\art\test\embedded.p3d.rz")
        rec = resolve_token({"art.rcf": self.ar}, f"art.rcf:{e.hash:08X}")
        self.assertEqual(rec["container"], "p3d")
        self.assertGreater(rec["offset_in_asset"], 0)

    def test_bare_hash_with_hint(self):
        e = self.ar.entry(r"\art\test\standalone.dds")
        rec = resolve_token({"art.rcf": self.ar}, f"{e.hash:08X}",
                            archive_hint="art.rcf")
        self.assertEqual(rec["entry_hash"], f"{e.hash:08X}")

    def test_errors(self):
        with self.assertRaises(PackError):
            resolve_token({"art.rcf": self.ar}, "")
        with self.assertRaises(PackError):
            resolve_token({"art.rcf": self.ar}, "missing.rcf:12345678")
        with self.assertRaises(PackError):
            resolve_token({"art.rcf": self.ar}, "0xZZZZ")
        with self.assertRaises(PackError):
            resolve_token({"art.rcf": self.ar}, "art.rcf:DEADBEEF")
        e = self.ar.entry(r"\art\test\notes.txt")
        with self.assertRaises(PackError):
            resolve_token({"art.rcf": self.ar}, f"art.rcf:{e.hash:08X}")

    def test_offset_not_dds(self):
        e = self.ar.entry(r"\art\test\embedded.p3d.rz")
        with self.assertRaises(PackError):
            resolve_token({"art.rcf": self.ar}, f"art.rcf:{e.hash:08X}:00000004")


class TestDefinitions(PackCase):
    def test_parse(self):
        dds = self.write("mod.dds", self.replacement())
        p = os.path.join(self.dir, "def.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# comment\n\n")
            f.write("art.rcf:04E56550|mod.dds\n")
            f.write("  art.rcf:16625ECF|mod.dds  \n")
            f.write("// another comment\n")
        entries = parse_definition(p)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "art.rcf:04E56550")
        self.assertEqual(entries[0][1], dds)
        self.assertEqual(entries[1][1], dds)

    def test_errors(self):
        p = os.path.join(self.dir, "bad.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("no separator here\n")
        with self.assertRaises(PackError):
            parse_definition(p)
        with open(p, "w", encoding="utf-8") as f:
            f.write("# only comments\n")
        with self.assertRaises(PackError):
            parse_definition(p)


class TestBuildAndLoad(PackCase):
    def test_build_and_load(self):
        src = self.write("mod.dds", self.replacement())
        e = self.ar.entry(r"\art\test\standalone.dds")
        rec = self.resolve(f"art.rcf:{e.hash:08X}", src)
        manifest, path = self.build_pack([rec], name="My Pack", version="2.1.0",
                                        author="tester", description="desc")
        self.assertEqual(manifest["format"], PACK_FORMAT)
        self.assertEqual(manifest["name"], "My Pack")
        self.assertEqual(manifest["replacement_count"], 1)
        self.assertEqual(manifest["game"]["archives"], ["art.rcf"])
        self.assertEqual(manifest["id"], "my-pack")

        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
        self.assertIn(MANIFEST, names)
        self.assertIn(CHECKSUMS, names)
        self.assertTrue(any(n.startswith("textures/") for n in names))

        m2, raw = load_pack(path)
        self.assertEqual(m2["id"], "my-pack")
        self.assertEqual(len(raw[m2["replacements"][0]["file"]]),
                         len(self.ar.read_stored(rec["entry_hash"])))

        info = pack_info(path)
        self.assertEqual(info["id"], "my-pack")
        self.assertEqual(info["replacements"], 1)
        self.assertEqual(info["entries"][0]["dims"], "64x64")

    def _one_rec(self):
        src = self.write("mod.dds", self.replacement())
        e = self.ar.entry(r"\art\test\standalone.dds")
        return self.resolve(f"art.rcf:{e.hash:08X}", src)

    def test_reject_size_change(self):
        src = self.write("bad.dds", make_dds(64, 64, b"DXT1", mips=2))
        rec = self._one_rec()
        rec["source_file"] = src
        with self.assertRaises(PackError):
            build_pack(os.path.join(self.dir, "p.zip"), [rec], name="x",
                       log=lambda *a: None)

    def test_reject_format_change(self):
        src = self.write("bad.dds", make_dds(64, 64, b"DXT5",
                                            fill=b"\x00" * (4096)))
        rec = self._one_rec()
        rec["source_file"] = src
        with self.assertRaises(PackError):
            build_pack(os.path.join(self.dir, "p.zip"), [rec], name="x",
                       log=lambda *a: None)

    def test_reject_dimension_change(self):
        src = self.write("bad.dds", make_dds(32, 32, b"DXT1"))
        rec = self._one_rec()
        rec["source_file"] = src
        with self.assertRaises(PackError):
            build_pack(os.path.join(self.dir, "p.zip"), [rec], name="x",
                       log=lambda *a: None)

    def test_reject_mip_change(self):
        src = self.write("bad.dds", make_dds(64, 64, b"DXT1", mips=2))
        rec = self._one_rec()
        rec["source_file"] = src
        with self.assertRaises(PackError):
            build_pack(os.path.join(self.dir, "p.zip"), [rec], name="x",
                       log=lambda *a: None)

    def test_reject_duplicate_target(self):
        src = self.write("mod.dds", self.replacement())
        e = self.ar.entry(r"\art\test\standalone.dds")
        rec = self.resolve(f"art.rcf:{e.hash:08X}", src)
        rec2 = dict(rec)
        with self.assertRaises(PackError):
            self.build_pack([rec, rec2])

    def test_no_replacements(self):
        with self.assertRaises(PackError):
            build_pack(os.path.join(self.dir, "p.zip"), [], name="x")

    def test_checksum_tamper_rejected(self):
        manifest, path = self.build_pack([self._one_rec()])
        bad = os.path.join(self.dir, "tampered.zip")
        with zipfile.ZipFile(path) as z:
            items = {n: z.read(n) for n in z.namelist()}
        target = manifest["replacements"][0]["file"]
        items[target] = items[target][:128] + b"\x00" * (len(items[target]) - 128)
        with zipfile.ZipFile(bad, "w") as z:
            for n, d in items.items():
                z.writestr(n, d)
        with self.assertRaises(PackError):
            load_pack(bad)

    def test_extra_unchecksummed_file_rejected(self):
        manifest, path = self.build_pack([self._one_rec()])
        bad = os.path.join(self.dir, "extra.zip")
        with zipfile.ZipFile(path) as z:
            items = {n: z.read(n) for n in z.namelist()}
        items["textures/extra.dds"] = b"junk"
        with zipfile.ZipFile(bad, "w") as z:
            for n, d in items.items():
                z.writestr(n, d)
        with self.assertRaises(PackError):
            load_pack(bad)

    def test_missing_manifest(self):
        bad = os.path.join(self.dir, "nomanifest.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("hello.txt", "no manifest")
        with self.assertRaises(PackError):
            load_pack(bad)

    def test_bad_format_field(self):
        manifest, path = self.build_pack([self._one_rec()])
        bad = os.path.join(self.dir, "badformat.zip")
        with zipfile.ZipFile(path) as z:
            items = {n: z.read(n) for n in z.namelist()}
        m = json.loads(items[MANIFEST])
        m["format"] = "something-else"
        items[MANIFEST] = json.dumps(m).encode()
        # rebuild checksums for the new manifest so only the format check fires
        lines = [ln for ln in items[CHECKSUMS].decode().splitlines() if ln]
        lines = [ln for ln in lines if not ln.endswith(MANIFEST)]
        lines.append(f"{sha256_bytes(items[MANIFEST])}  {MANIFEST}")
        items[CHECKSUMS] = ("\n".join(lines) + "\n").encode()
        with zipfile.ZipFile(bad, "w") as z:
            for n, d in items.items():
                z.writestr(n, d)
        with self.assertRaises(PackError):
            load_pack(bad)

    def test_validate_against_game(self):
        src = self.write("mod.dds", self.replacement())
        e = self.ar.entry(r"\art\test\standalone.dds")
        rec = self.resolve(f"art.rcf:{e.hash:08X}", src)
        _, path = self.build_pack([rec])
        res = validate_against_game(path, self.game, log=lambda *a: None)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(len(res["checked"]), 1)

    def test_validate_detects_different_build(self):
        src = self.write("mod.dds", self.replacement())
        e = self.ar.entry(r"\art\test\standalone.dds")
        rec = self.resolve(f"art.rcf:{e.hash:08X}", src)
        _, path = self.build_pack([rec])
        # change the installed texture, simulating another game build
        self.ar.apply_patches({e.hash: self.replacement(fill=b"\x01" * 2048)})
        res = validate_against_game(path, self.game, log=lambda *a: None)
        self.assertFalse(res["ok"])
        self.assertTrue(any("expected original" in p for p in res["problems"]))


if __name__ == "__main__":
    unittest.main()
