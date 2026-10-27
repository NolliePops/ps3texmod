"""Built-in end-to-end self test (no game files required)."""

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import zlib

from .catalog import extract_texture, make_patch, scan_archive
from .dds import build_png, parse_dds
from .installer import install, load_state, remove, set_enabled, status, uninstall_all
from .pack import build_pack, load_pack, parse_definition, resolve_token, validate_against_game
from .rcf import RcfArchive, build_archive
from .rz import is_rz, pack_rz
from .util import sha256_bytes


def make_dds(width: int, height: int, fourcc: bytes, level: int = 0,
             mips: int = 1) -> bytes:
    if fourcc == b"DXT1":
        block = 8
    elif fourcc in (b"DXT3", b"DXT5"):
        block = 16
    else:
        raise ValueError("test writer supports DXT1/DXT3/DXT5")
    total = 0
    w, h = width, height
    for _ in range(mips):
        total += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
        w = max(1, w // 2)
        h = max(1, h // 2)
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    if mips > 1:
        flags |= 0x20000
    hdr = bytearray(128)
    hdr[0:4] = b"DDS "
    struct.pack_into("<7I", hdr, 4, 124, flags, height, width,
                     max(1, (width + 3) // 4) * block, 0, mips)
    struct.pack_into("<4I", hdr, 76, 32, 0x4, int.from_bytes(fourcc, "little"), 0)
    struct.pack_into("<I", hdr, 108, 0x1000)
    # DXT1 block: two colours (565) + 32-bit indices
    src = bytearray()
    if fourcc == b"DXT1":
        c0 = 0xF800 if level == 0 else 0x07E0
        c1 = 0x001F
        blk = struct.pack("<HHI", c0, c1, 0x00000000)
    elif fourcc == b"DXT3":
        blk = bytes(8) + struct.pack("<HHI", 0xF800, 0x001F, 0)
    else:
        blk = bytes(8) + struct.pack("<HHI", 0xF800, 0x001F, 0)
    src += blk * ((width // 4) * (height // 4))
    data = bytes(hdr) + bytes(src)
    assert len(data) == 128 + total, (len(data), 128 + total)
    return data


def _p3d_with_dds(name: str, dds: bytes) -> bytes:
    """Very small P3D-like container: header + name + padding + DDS."""
    nb = name.encode("ascii") + b"\x00"
    body = bytearray()
    body += b"\xffD3P" + struct.pack(">I", 12) + b"\x00\x00\x00\x00"
    body += struct.pack(">I", 0x0001100B) + struct.pack(">I", 85) + struct.pack(">I", 0)
    body += struct.pack(">I", len(nb)) + nb
    body += b"\x00" * 16
    dds_off = len(body)
    body += dds
    struct.pack_into(">I", body, 8, len(body))  # file size field
    return bytes(body), dds_off


class Checker:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.failures = []

    def ok(self, cond: bool, label: str, detail: str = ""):
        if cond:
            self.passed += 1
            if self.verbose:
                print(f"  PASS  {label}")
        else:
            self.failed += 1
            self.failures.append(label + (f" ({detail})" if detail else ""))
            print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))

    def eq(self, a, b, label):
        self.ok(a == b, label, f"{a!r} != {b!r}")


def run_selftest(game_dir: str | None = None, verbose: bool = True) -> int:
    c = Checker(verbose)
    tmp = tempfile.mkdtemp(prefix="ps3texmod-selftest-")
    try:
        print(f"ps3texmod self test (temp: {tmp})")
        gamedir = os.path.join(tmp, "game")
        usrdir = os.path.join(gamedir, "PS3_GAME", "USRDIR")
        os.makedirs(usrdir)

        # --- build a synthetic archive ------------------------------------
        dds_a = make_dds(64, 64, b"DXT1")
        dds_b = make_dds(64, 64, b"DXT5")
        p3d, embedded_off = _p3d_with_dds("test_embedded.dds", dds_b)
        archive = os.path.join(usrdir, "art.rcf")
        info = build_archive(archive, [
            (r"\art\test\standalone.dds", dds_a, False),
            (r"\art\test\embedded.p3d.rz", p3d, True),
            (r"\art\test\notes.txt", b"hello cement\n" * 40, True),
        ])
        c.ok(os.path.getsize(archive) == info["size"], "synthetic archive size")

        ar = RcfArchive(archive)
        c.eq(len(ar.entries), 3, "entry count")
        c.ok(ar.info.names_ok, "name table maps to entries")
        c.eq(ar.entry(r"\art\test\standalone.dds").ext, "dds", "name lookup by path")
        c.ok(ar.validate(deep=True)["ok"], "synthetic archive validates")

        # --- texture scan -------------------------------------------------
        recs = scan_archive(ar)
        c.eq(len(recs), 2, "two textures found")
        by_container = {r["container"]: r for r in recs}
        c.ok("dds" in by_container and "p3d" in by_container, "standalone + embedded texture")
        c.eq(by_container["p3d"]["offset_in_asset"], embedded_off, "embedded DDS offset")
        c.eq(by_container["p3d"]["name"], "test_embedded.dds", "embedded texture name")
        c.eq(by_container["dds"]["width"], 64, "standalone DDS width")
        c.eq(by_container["p3d"]["format"], "DXT5", "embedded DDS format")

        # --- extract ------------------------------------------------------
        blob = extract_texture(ar, by_container["dds"])
        c.eq(blob, dds_a, "extracted standalone texture is byte exact")
        png = build_png(*_rgba_of(blob))
        c.ok(png[:8] == b"\x89PNG\r\n\x1a\n", "PNG preview encodes")

        # --- author a modified texture -----------------------------------
        mod_dds = make_dds(64, 64, b"DXT1", level=1)  # different colour
        c.eq(len(mod_dds), len(dds_a), "modified DDS keeps byte length")
        def_path = os.path.join(tmp, "def.txt")
        with open(def_path, "w", encoding="utf-8") as f:
            f.write(f"art.rcf:{by_container['dds']['entry_hash']}|{_write(tmp, 'mod.dds', mod_dds)}\n")

        archives = {"art.rcf": RcfArchive(archive)}
        reps = []
        for ident, path in parse_definition(def_path):
            rec = resolve_token(archives, ident)
            rec["source_file"] = path
            reps.append(rec)
        c.eq(reps[0]["original_sha256"], sha256_bytes(dds_a), "resolved original hash")
        pack_path = os.path.join(tmp, "test-pack.zip")
        manifest = build_pack(pack_path, reps, name="Self Test Pack", version="1.0.0",
                              author="selftest", game_dir=gamedir, log=lambda *a: None)
        c.eq(manifest["replacement_count"], 1, "pack has one replacement")
        c.ok(os.path.getsize(pack_path) > 0, "pack written")

        # --- validate -----------------------------------------------------
        m2, raw = load_pack(pack_path)
        c.eq(m2["id"], manifest["id"], "pack loads")
        v = validate_against_game(pack_path, gamedir)
        c.ok(v["ok"], "pack validates against game", "; ".join(v["problems"]))

        # corrupt the pack and confirm rejection
        bad = os.path.join(tmp, "bad.zip")
        shutil.copy2(pack_path, bad)
        import zipfile
        with zipfile.ZipFile(bad) as z:
            items = {n: z.read(n) for n in z.namelist()}
        items[manifest["replacements"][0]["file"]] = b"\x00" * 8
        with zipfile.ZipFile(bad, "w") as z:
            for n, d in items.items():
                z.writestr(n, d)
        try:
            load_pack(bad)
            c.ok(False, "corrupt pack rejected")
        except Exception:
            c.ok(True, "corrupt pack rejected")

        # --- install / disable / enable / remove --------------------------
        res = install(pack_path, gamedir, log=lambda *a: None)
        c.eq(res["textures"], 1, "install applied one texture")
        ar2 = RcfArchive(archive)
        c.ok(ar2.validate(deep=True)["ok"], "patched archive still valid")
        recs2 = {r["container"]: r for r in scan_archive(ar2)}
        c.eq(extract_texture(ar2, recs2["dds"]), mod_dds, "installed texture is the new one")
        c.eq(extract_texture(ar2, recs2["p3d"]), dds_b, "other textures untouched")
        c.eq(sha256_bytes(extract_texture(ar2, recs2["p3d"])),
             by_container["p3d"]["sha256"], "untouched texture hash stable")

        st = status(gamedir)
        c.eq(len(st["packs"]), 1, "state lists one pack")
        c.ok(st["backups"][0]["modified"], "state reports archive modified")

        set_enabled(gamedir, manifest["id"], False, log=lambda *a: None)
        ar3 = RcfArchive(archive)
        recs3 = {r["container"]: r for r in scan_archive(ar3)}
        c.eq(extract_texture(ar3, recs3["dds"]), dds_a, "disable restores original")

        set_enabled(gamedir, manifest["id"], True, log=lambda *a: None)
        ar4 = RcfArchive(archive)
        recs4 = {r["container"]: r for r in scan_archive(ar4)}
        c.eq(extract_texture(ar4, recs4["dds"]), mod_dds, "enable re-applies")

        remove(gamedir, manifest["id"], log=lambda *a: None)
        ar5 = RcfArchive(archive)
        c.eq(ar5.read_payload(r"\art\test\standalone.dds"), dds_a, "remove restores original")
        c.ok(ar5.validate(deep=True)["ok"], "restored archive valid")

        # --- rebuild path (incompressible embedded replacement -> new layout) ---
        import random
        rng = random.Random(0xC0FFEE)
        noisy = bytearray(make_dds(64, 64, b"DXT5"))
        noisy[128:] = rng.randbytes(len(noisy) - 128)
        noisy = bytes(noisy)
        arx = RcfArchive(archive)
        rec_p3d = {r["container"]: r for r in scan_archive(arx)}["p3d"]
        c.eq(len(noisy), rec_p3d["size"], "noisy DDS keeps byte length")
        h, stored = make_patch(arx, rec_p3d, noisy)
        res2 = arx.apply_patches({h: stored})
        c.ok(res2["mode"].startswith("rebuild"), "incompressible replacement forces rebuild",
             f"mode={res2['mode']}")
        ar6 = RcfArchive(archive)
        c.ok(ar6.validate(deep=True)["ok"], "rebuilt archive valid")
        recs6 = {r["container"]: r for r in scan_archive(ar6)}
        c.eq(extract_texture(ar6, recs6["p3d"]), noisy, "rebuilt archive has new texture")
        c.eq(extract_texture(ar6, recs6["dds"]), dds_a, "rebuilt archive keeps other texture")
        c.eq([e.hash for e in ar6.entries], sorted(e.hash for e in ar6.entries),
             "rebuilt index still hash sorted")
        c.eq(ar6.read_payload(r"\art\test\notes.txt"), b"hello cement\n" * 40,
             "rebuilt archive keeps non-texture payload")

        uninstall_all(gamedir, log=lambda *a: None)
        ar7 = RcfArchive(archive)
        c.eq(ar7.read_payload(r"\art\test\standalone.dds"), dds_a, "uninstall restores all")

        # --- real game file (read only) ---------------------------------
        if game_dir:
            real = os.path.join(game_dir, "PS3_GAME", "USRDIR", "scripts.rcf")
            if not os.path.isfile(real):
                real = os.path.join(game_dir, "USRDIR", "scripts.rcf")
            if os.path.isfile(real):
                rr = RcfArchive(real)
                c.eq(len(rr.entries), 7, "real scripts.rcf entry count")
                c.eq(rr.entry(r"scripts\credits_english.txt").offset, 0x1000,
                     "real scripts.rcf english credits offset")
                c.eq(rr.entry(r"scripts\credits_english.txt").size, 22457,
                     "real scripts.rcf english credits size")
                c.eq(rr.entry(r"init.lua").hash, 0x0FFD75FA, "real hash for init.lua")
                c.ok(rr.validate(deep=True)["ok"], "real scripts.rcf validates")
            else:
                print(f"  skip  real game check (no scripts.rcf under {game_dir})")

        print()
        print(f"{c.passed} passed, {c.failed} failed")
        if c.failures:
            print("failures:")
            for f in c.failures:
                print("  -", f)
        return 0 if c.failed == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _rgba_of(dds: bytes):
    from .dds import decode_level0_rgba
    info = parse_dds(dds)
    rgba, w, h = decode_level0_rgba(dds, info)
    return rgba, w, h


def _write(tmp: str, name: str, data: bytes) -> str:
    p = os.path.join(tmp, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


if __name__ == "__main__":
    sys.exit(run_selftest(sys.argv[1] if len(sys.argv) > 1 else None))
