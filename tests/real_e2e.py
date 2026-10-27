"""End-to-end test against a *copy* of the retail art.rcf.

The original game file is only ever opened read-only.  Everything happens in a
throw-away work directory (default: %TEMP%\\ps3texmod-real-e2e).

Usage:
    python tests/real_e2e.py [--game-dir <GAME>] [--workdir <DIR>] [--keep]

Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ps3texmod.catalog import extract_texture, scan_payload_textures
from ps3texmod.dds import parse_dds
from ps3texmod.installer import install, load_state, remove, set_enabled, status, usrdir
from ps3texmod.pack import build_pack, resolve_token, validate_against_game
from ps3texmod.rcf import RcfArchive
from ps3texmod.rz import is_rz, unpack_rz
from ps3texmod.util import human_size, sha256_file
from tests.helpers import stored_hashes

DEFAULT_GAME = os.path.join(ROOT, "Prototype (USA, Korea) (En,Fr)")
DEFAULT_WORK = os.path.join(tempfile.gettempdir(), "ps3texmod-real-e2e")

STANDALONE = 0x94CCC0CC      # \art\billboards\billboards_1024x1024_01_diffuse.dds
BILLBOARDS = 0xADF438E0      # \art\billboards\billboards.p3d.rz


class Checks:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def ok(self, cond, label, detail=""):
        if cond:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed += 1
            self.failures.append(label + (f" ({detail})" if detail else ""))
            print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))


def modified_texture(blob: bytes, seed: int) -> bytes:
    """Same byte length, valid header, deterministic pseudo-random pixels."""
    info = parse_dds(blob)
    out = bytearray(blob)
    rng = random.Random(seed)
    start = info.data_offset
    out[start:] = rng.randbytes(len(out) - start)
    return bytes(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=os.environ.get("PS3TEXMOD_GAME_DIR", DEFAULT_GAME))
    ap.add_argument("--workdir", default=DEFAULT_WORK)
    ap.add_argument("--keep", action="store_true", help="keep the work directory")
    args = ap.parse_args(argv)

    src = os.path.join(usrdir(args.game_dir), "art.rcf")
    if not os.path.isfile(src):
        print(f"error: art.rcf not found under {args.game_dir}")
        return 2

    work = os.path.abspath(args.workdir)
    game = os.path.join(work, "game")
    dst_dir = os.path.join(game, "PS3_GAME", "USRDIR")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "art.rcf")

    c = Checks()
    t0 = time.time()
    print(f"source : {src} ({human_size(os.path.getsize(src))})")
    print(f"work   : {work}")
    print()
    print("hashing the original (read only) ...")
    orig_sha = sha256_file(src)
    print(f"  sha256 {orig_sha}")

    # ---------------------------------------------------------------- step 1
    print()
    print("[1] copy archive and patch a standalone texture")
    if os.path.isfile(dst):
        os.unlink(dst)
    shutil.copy2(src, dst)
    c.ok(sha256_file(dst) == orig_sha, "copy is byte identical to the original")

    ar = RcfArchive(dst)
    size_before = os.path.getsize(dst)
    before = stored_hashes(ar)
    target = ar.entry(STANDALONE)
    c.ok(target.name.endswith("billboards_1024x1024_01_diffuse.dds"),
         "target entry resolved by hash", target.name)
    old = ar.read_stored(target)
    new = modified_texture(old, 1)
    c.ok(len(new) == len(old), "modified texture keeps its byte length")

    t = time.time()
    res = ar.apply_patches({STANDALONE: new})
    print(f"      mode={res['mode']} in {time.time() - t:.1f}s")
    c.ok(res["mode"] == "inplace", "standalone replacement used in-place patching")
    c.ok(os.path.getsize(dst) == size_before, "archive size unchanged after in-place patch")

    ar1 = RcfArchive(dst)
    v = ar1.validate(deep=True)
    c.ok(v["ok"], "patched archive validates deeply", "; ".join(v["problems"]))
    c.ok(ar1.read_stored(STANDALONE) == new, "standalone texture contains the new bytes")
    c.ok(ar1.entry(STANDALONE).size == len(new), "index size field was updated")
    after = stored_hashes(ar1)
    changed = [h for h in before if before[h] != after[h]]
    c.ok(changed == [STANDALONE], "exactly one payload changed", f"changed={changed}")

    # ---------------------------------------------------------------- step 2
    print()
    print("[2] patch an embedded texture inside a P3D (forces a rebuild)")
    bb = ar1.entry(BILLBOARDS)
    payload = ar1.read_payload(bb)
    c.ok(payload[:4] in (b"\xffD3P", b"P3D\xff"),
         "billboards payload is a P3D (BE or byte-swapped LE)", payload[:4].hex())
    recs = scan_payload_textures(payload, bb.hash, bb.name, ar1.name)
    c.ok(len(recs) == 4, "four embedded textures found", str(len(recs)))
    rec = recs[0]
    c.ok(rec["name"] == "$billboards_1024x1024_01_diffuse.dds",
         "embedded texture name recovered", rec["name"])
    old_emb = extract_texture(ar1, rec)
    c.ok(old_emb == payload[rec["offset_in_asset"]:
                            rec["offset_in_asset"] + rec["size"]],
         "embedded texture extraction is byte exact")
    new_emb = modified_texture(old_emb, 2)
    c.ok(len(new_emb) == len(old_emb), "embedded replacement keeps its byte length")

    from ps3texmod.catalog import make_patch
    h, stored = make_patch(ar1, rec, new_emb)
    c.ok(is_rz(stored), "rebuilt blob is still an RZ container")
    print(f"      old stored {human_size(len(ar1.read_stored(bb)))}, "
          f"new stored {human_size(len(stored))}, slot {human_size(bb.slot_size)}")
    t = time.time()
    res2 = ar1.apply_patches({h: stored})
    dt2 = time.time() - t
    print(f"      mode={res2['mode']} in {dt2:.1f}s")
    c.ok(res2["mode"].startswith("rebuild"),
         "random embedded texture grew past its slot and forced a rebuild", res2["mode"])

    ar2 = RcfArchive(dst)
    v = ar2.validate(deep=True)
    c.ok(v["ok"], "rebuilt archive validates deeply", "; ".join(v["problems"]))
    new_payload = ar2.read_payload(BILLBOARDS)
    expected = payload[:rec["offset_in_asset"]] + new_emb + \
        payload[rec["offset_in_asset"] + rec["size"]:]
    c.ok(new_payload == expected, "rebuilt P3D payload is exactly as intended")
    c.ok(ar2.read_payload(STANDALONE) == new, "earlier standalone patch survived the rebuild")
    after2 = stored_hashes(ar2)
    others = [hh for hh in after if hh != BILLBOARDS and after2[hh] != after[hh]]
    c.ok(others == [], "every other payload is byte identical after the rebuild", str(others))
    c.ok([e.hash for e in ar2.entries] == sorted(e.hash for e in ar2.entries),
         "rebuilt index is still hash sorted")
    c.ok(ar2.info.names_ok and ar2.entry(BILLBOARDS).name.endswith("billboards.p3d.rz"),
         "name table still maps correctly after the rebuild")

    # ---------------------------------------------------------------- step 3
    print()
    print("[3] install / disable / enable / remove a real mod pack")
    os.unlink(dst)
    shutil.copy2(src, dst)  # back to a pristine copy
    c.ok(sha256_file(dst) == orig_sha, "fresh copy restored from the original")

    ev = RcfArchive(dst).entry(STANDALONE)
    mod_dds = modified_texture(old, 3)
    mod_path = os.path.join(work, "billboards_01_mod.dds")
    with open(mod_path, "wb") as f:
        f.write(mod_dds)
    rec = resolve_token({"art.rcf": RcfArchive(dst)}, f"art.rcf:{ev.hash:08X}")
    rec["source_file"] = mod_path
    pack_path = os.path.join(work, "billboards-01.zip")
    build_pack(pack_path, [rec], name="Billboard 01 Test", version="1.0.0",
               author="real_e2e", game_dir=game, log=lambda *a: None)
    c.ok(os.path.getsize(pack_path) > 0, "pack built from retail data")
    val = validate_against_game(pack_path, game, log=lambda *a: None)
    c.ok(val["ok"], "pack validates against the retail copy", "; ".join(val["problems"]))

    t = time.time()
    res3 = install(pack_path, game, log=lambda *a: None)
    dt3 = time.time() - t
    print(f"      install in {dt3:.1f}s: {res3}")
    c.ok(res3["textures"] == 1, "one texture installed")
    ar3 = RcfArchive(dst)
    c.ok(extract_texture(ar3, {**rec, "id": rec.get("texture_id", "")}) == mod_dds,
         "installed archive returns the new texture")
    st = status(game)
    c.ok(len(st["packs"]) == 1 and st["packs"][0]["enabled"], "state lists one enabled pack")
    base = st["backups"][0]
    c.ok(base["backup_sha256"] == orig_sha, "pristine base backup hash matches the original")
    c.ok(base["modified"], "state reports the archive as modified")

    set_enabled(game, "billboard-01-test", False, log=lambda *a: None)
    c.ok(sha256_file(dst) == orig_sha, "disable restores the archive byte-for-byte")

    set_enabled(game, "billboard-01-test", True, log=lambda *a: None)
    ar4 = RcfArchive(dst)
    c.ok(extract_texture(ar4, {**rec, "id": ""}) == mod_dds, "enable re-applies the texture")

    remove(game, "billboard-01-test", log=lambda *a: None)
    c.ok(sha256_file(dst) == orig_sha, "remove restores the archive byte-for-byte")
    c.ok(status(game)["packs"] == [], "state is empty after remove")
    c.ok(load_state(game)["backups"], "backup metadata is retained")

    # ---------------------------------------------------------------- report
    report = {
        "source": src,
        "source_sha256": orig_sha,
        "workdir": work,
        "checks_passed": c.passed,
        "checks_failed": c.failed,
        "failures": c.failures,
        "elapsed_seconds": round(time.time() - t0, 2),
        "steps": {
            "inplace_patch": {"mode": res["mode"]},
            "embedded_rebuild": {"mode": res2["mode"], "seconds": round(dt2, 2)},
            "install": {"seconds": round(dt3, 2), **{k: res3[k] for k in ("packs", "textures")}},
        },
    }
    os.makedirs(os.path.join(ROOT, "artifacts"), exist_ok=True)
    report_path = os.path.join(ROOT, "artifacts", "real_e2e_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print()
    print(f"{c.passed} passed, {c.failed} failed in {report['elapsed_seconds']}s")
    print(f"report: {report_path}")
    if c.failures:
        for f in c.failures:
            print("  -", f)

    if not args.keep:
        # Keep the (small) report, drop the multi-GB work directory.
        shutil.rmtree(work, ignore_errors=True)
    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
