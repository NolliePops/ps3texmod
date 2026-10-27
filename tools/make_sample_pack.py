"""Build the bundled sample mod pack and console plan.

The sample recolours the standalone 1024x1024 DXT1 billboard atlas
``\\art\\billboards\\billboards_1024x1024_01_diffuse.dds`` (entry 94CCC0CC)
by transforming the two RGB565 endpoint colours of every DXT1 block.  Block
indices and the block count are untouched, so the DDS stays exactly 699,192
bytes and the P3D/RCF sizes do not change.

This is deliberately not an image importer: the PC tool accepts any edited DDS
of the same shape, and this script only exists so the repository ships a pack
and a plan that were produced from real retail data.

Usage (from the repository root):

    python tools/make_sample_pack.py --game-dir "Prototype (USA, Korea) (En,Fr)"
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ps3texmod.dds import dds_to_png, parse_dds  # noqa: E402
from ps3texmod.pack import build_pack, resolve_token  # noqa: E402
from ps3texmod.plan import plan_from_pack  # noqa: E402
from ps3texmod.rcf import RcfArchive  # noqa: E402
from ps3texmod.util import sha256_bytes  # noqa: E402

ENTRY_HASH = 0x94CCC0CC
SOURCE = os.path.join(ROOT, "samples", "billboards",
                      "94CCC0CC_00000000_billboards_1024x1024_01_diffuse.dds")
OUT_DIR = os.path.join(ROOT, "samples", "billboard-tint")
TINTED = os.path.join(OUT_DIR, "94CCC0CC_billboards_01_tint.dds")
PACK = os.path.join(ROOT, "samples", "prototype-billboard-tint.zip")
PLAN = os.path.join(ROOT, "samples", "prototype-billboard-tint.ps3patch")

# warm tint: more red, less green/blue
GAIN = (1.28, 0.82, 0.62)


def _unpack565(c: int) -> tuple[int, int, int]:
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return r * 255 // 31, g * 255 // 63, b * 255 // 31


def _pack565(r: int, g: int, b: int) -> int:
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return ((r * 31 + 127) // 255 << 11) | ((g * 63 + 127) // 255 << 5) | (b * 31 + 127) // 255


def tint_block(block: bytes) -> bytes:
    c0, c1, bits = struct.unpack("<HHI", block)
    n0 = _pack565(*(int(v * k) for v, k in zip(_unpack565(c0), GAIN)))
    n1 = _pack565(*(int(v * k) for v, k in zip(_unpack565(c1), GAIN)))

    # DXT1 uses c0 > c1 for the opaque 4-colour mode and c0 <= c1 for the
    # 3-colour + 1-bit-alpha mode.  Preserve the mode the original block used.
    four_colour = c0 > c1
    if four_colour and n0 <= n1:
        if n1 < 0xFFFF:
            n0 = n1 + 1
        else:
            n0, n1 = 0xFFFF, 0xFFFE
    elif not four_colour and n0 > n1:
        if n1 > 0:
            n0 = n1 - 1
        else:
            n0, n1 = 0, 1
    return struct.pack("<HHI", n0, n1, bits)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-dir", required=True)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    log = (lambda *a: None) if args.quiet else print

    with open(SOURCE, "rb") as f:
        original = f.read()
    info = parse_dds(original)
    if info.format_name != "DXT1":
        raise SystemExit(f"sample source is {info.format_name}, expected DXT1")

    body = bytearray(original[:info.data_offset])
    blocks = memoryview(original)[info.data_offset:]
    for off in range(0, len(blocks), 8):
        body += tint_block(bytes(blocks[off:off + 8]))
    tinted = bytes(body)
    if len(tinted) != len(original):
        raise SystemExit("internal error: tinted DDS changed size")

    # Re-parse to prove the output is still a valid DDS of the same shape.
    out_info = parse_dds(tinted)
    if (out_info.width, out_info.height, out_info.mipmaps, out_info.format_name) != (
            info.width, info.height, info.mipmaps, info.format_name):
        raise SystemExit("internal error: tinted DDS shape changed")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(TINTED, "wb") as f:
        f.write(tinted)
    with open(os.path.splitext(TINTED)[0] + ".png", "wb") as f:
        f.write(dds_to_png(tinted, out_info))

    definition = os.path.join(OUT_DIR, "def.txt")
    with open(definition, "w", encoding="utf-8") as f:
        f.write("# Sample pack definition: warm-tinted billboard atlas.\n")
        f.write(f"art.rcf:{ENTRY_HASH:08X}|{os.path.basename(TINTED)}\n")

    archive_path = os.path.join(args.game_dir, "PS3_GAME", "USRDIR", "art.rcf")
    if not os.path.isfile(archive_path):
        archive_path = os.path.join(args.game_dir, "art.rcf")
    archive = RcfArchive(archive_path)
    rec = resolve_token({"art.rcf": archive}, f"art.rcf:{ENTRY_HASH:08X}")
    rec["source_file"] = TINTED
    build_pack(PACK, [rec], name="Billboard Warm Tint", version="1.0.0",
               author="ps3texmod sample", game_dir=args.game_dir,
               description="Warm colour grade of the 1024x1024 billboard atlas "
                           "(DXT1 endpoint transform, same byte length).",
               pack_id="billboard-warm-tint", log=log)
    plan_from_pack(PACK, args.game_dir, PLAN, log=log)

    log(f"source   {os.path.relpath(SOURCE, ROOT)}")
    log(f"tinted   {os.path.relpath(TINTED, ROOT)}  "
        f"{out_info.width}x{out_info.height} {out_info.format_name} "
        f"mips={out_info.mipmaps} {len(tinted)} bytes")
    log(f"          sha256 {sha256_bytes(tinted)}")
    log(f"pack     {os.path.relpath(PACK, ROOT)}  {os.path.getsize(PACK)} bytes")
    log(f"plan     {os.path.relpath(PLAN, ROOT)}  {os.path.getsize(PLAN)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
