"""Read-only checks against the retail game files.

These tests never write to the game directory.  Set ``PS3TEXMOD_GAME_DIR`` to
the folder that contains ``PS3_GAME`` (the default layout shipped in this
workspace is detected automatically).  The whole module is skipped when the
game files are not present.
"""

import os
import unittest

from ps3texmod.catalog import scan_payload_textures
from ps3texmod.dds import parse_dds
from ps3texmod.rcf import RcfArchive
from ps3texmod.util import sha256_bytes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_GAME = os.path.join(ROOT, "Prototype (USA, Korea) (En,Fr)")
GAME = os.environ.get("PS3TEXMOD_GAME_DIR", DEFAULT_GAME)


def usrdir(game):
    cand = os.path.join(game, "PS3_GAME", "USRDIR")
    return cand if os.path.isdir(cand) else game


HAVE_GAME = os.path.isdir(usrdir(GAME))


@unittest.skipUnless(HAVE_GAME, f"retail game files not found under {GAME}")
class TestRealArchives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = usrdir(GAME)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_scripts_rcf(self):
        ar = RcfArchive(self.path("scripts.rcf"))
        self.assertEqual(len(ar.entries), 7)
        self.assertTrue(ar.info.names_ok)
        e = ar.entry(r"scripts\credits_english.txt")
        self.assertEqual(e.offset, 0x1000)
        self.assertEqual(e.size, 22457)
        self.assertEqual(e.hash, 0xCE310C99)
        self.assertEqual(ar.entry("init.lua").hash, 0x0FFD75FA)
        self.assertEqual(ar.entry(r"scripts\statsachievements_tod.p3d").hash,
                         0xD1815F8C)
        self.assertTrue(ar.validate(deep=True)["ok"])

    def test_shaders_rcf(self):
        ar = RcfArchive(self.path("shaders.rcf"))
        self.assertEqual(len(ar.entries), 1)
        self.assertEqual(ar.entries[0].name, r"atg\shaders\bink.p3d")
        self.assertTrue(ar.validate(deep=True)["ok"])

    def test_woi9_rcf(self):
        ar = RcfArchive(self.path("woi9.rcf"))
        self.assertEqual(len(ar.entries), 16)
        self.assertTrue(all(ar.kind(e) == "bik" for e in ar.entries))
        self.assertTrue(ar.validate(deep=True)["ok"])

    def test_cached_rcf(self):
        ar = RcfArchive(self.path("cached.rcf"))
        self.assertEqual(len(ar.entries), 529)
        st = ar.stats()
        self.assertEqual(st["kinds"], {"p3d": 520, "bik": 9})
        self.assertTrue(ar.info.names_ok)
        self.assertTrue(ar.validate(deep=True)["ok"])

    def test_art_rcf_header_and_index(self):
        ar = RcfArchive(self.path("art.rcf"))
        self.assertEqual(ar.file_size, 476895845)
        self.assertEqual(ar.info.count, 2348)
        self.assertTrue(ar.info.names_ok)
        self.assertEqual(ar.info.index_offset, 0x3C)
        self.assertEqual(ar.info.index_size, 12 * 2348)
        self.assertEqual(ar.info.table_offset, 0x7000)
        self.assertEqual(ar.info.data_start, 0x2B000)
        self.assertEqual(ar.entries[0].hash, 0x004A70E7)
        self.assertEqual(ar.entries[-1].hash, 0xFFCB33D6)
        self.assertEqual(ar._layout[0].name, r"\art\alex\alex.p3d.rz")
        self.assertEqual(ar._layout[0].offset, 0x2B000)
        st = ar.stats()
        self.assertEqual(st["kinds"], {"p3d": 1966, "gfx": 360, "dds": 19, "png": 3})
        self.assertEqual(st["named"], 2348)

    def test_art_rcf_deep_validation(self):
        ar = RcfArchive(self.path("art.rcf"))
        res = ar.validate(deep=True)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["deep_checked"], 2348)
        self.assertEqual(res["bad_ext"], 0)

    def test_standalone_texture(self):
        ar = RcfArchive(self.path("art.rcf"))
        e = ar.entry(r"\art\billboards\billboards_1024x1024_01_diffuse.dds")
        self.assertEqual(e.hash, 0x94CCC0CC)
        self.assertEqual(e.size, 699192)
        blob = ar.read_stored(e)
        self.assertTrue(blob.startswith(b"DDS "))
        info = parse_dds(blob)
        self.assertEqual((info.width, info.height), (1024, 1024))
        self.assertEqual(info.format_name, "DXT1")
        self.assertEqual(info.mipmaps, 11)
        self.assertEqual(len(blob), info.data_offset + info.data_size)

    def test_embedded_textures_in_p3d(self):
        ar = RcfArchive(self.path("art.rcf"))
        e = ar.entry(r"\art\billboards\billboards.p3d.rz")
        self.assertEqual(e.hash, 0xADF438E0)
        payload = ar.read_payload(e)
        recs = scan_payload_textures(payload, e.hash, e.name, ar.name)
        self.assertEqual(len(recs), 4)
        names = [r["name"] for r in recs]
        self.assertEqual(names[0], "$billboards_1024x1024_01_diffuse.dds")
        self.assertEqual([r["offset_in_asset"] for r in recs],
                         [0xBE, 0xAACFD, 0x15593C, 0x20057B])
        self.assertTrue(all(r["format"] == "DXT1" for r in recs))
        self.assertTrue(all(r["size"] == 699192 for r in recs))
        # the extracted bytes must match the payload slice exactly
        r0 = recs[0]
        self.assertEqual(payload[r0["offset_in_asset"]:
                                 r0["offset_in_asset"] + r0["size"]][:4], b"DDS ")
        self.assertEqual(r0["sha256"], sha256_bytes(
            payload[r0["offset_in_asset"]:r0["offset_in_asset"] + r0["size"]]))


if __name__ == "__main__":
    unittest.main()
