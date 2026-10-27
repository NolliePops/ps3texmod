"""Asset/texture catalogue and in-archive texture replacement."""

from __future__ import annotations

import os
from collections import OrderedDict

from .dds import DdsError, dds_to_png, parse_dds
from .rcf import RcfArchive
from .rz import is_rz, pack_rz, unpack_rz
from .util import fmt_hash, game_usrdir, human_size, sha256_bytes

DEFAULT_ARCHIVES = ("art.rcf", "cached.rcf")


# ---------------------------------------------------------------------------
# embedded texture discovery
# ---------------------------------------------------------------------------

def _embedded_name(payload: bytes, dds_offset: int, window: int = 768) -> str:
    """Find the length-prefixed .dds name stored shortly before a DDS blob."""
    lo = max(0, dds_offset - window)
    seg = payload[lo:dds_offset]
    idx = seg.lower().rfind(b".dds")
    if idx < 0:
        return ""
    end = idx + 4
    start = end
    while start > 0 and 32 <= seg[start - 1] < 127:
        start -= 1
    name = seg[start:end].decode("latin1", "replace")
    return name if len(name) >= 5 else ""


def scan_payload_textures(payload: bytes, entry_hash: int, entry_name: str,
                          archive_name: str) -> list[dict]:
    """Find every embedded DDS inside an asset payload."""
    from .dds import iter_dds
    out = []
    for off, info in iter_dds(payload):
        total = info.data_offset - off + info.data_size if info.data_offset >= off else 0
        # parse_dds() was called with absolute offset inside payload
        blob_start = off
        blob_end = info.data_offset + info.data_size
        blob = payload[blob_start:blob_end]
        if len(blob) != blob_end - blob_start:
            continue
        rec = {
            "id": f"{archive_name}:{fmt_hash(entry_hash)}:{off:08X}",
            "archive": archive_name,
            "entry_hash": fmt_hash(entry_hash),
            "entry_name": entry_name,
            "container": "p3d",
            "offset_in_asset": off,
            "name": _embedded_name(payload, off) or f"embedded_{off:08X}.dds",
            "width": info.width,
            "height": info.height,
            "format": info.format_name,
            "mipmaps": info.mipmaps,
            "size": len(blob),
            "sha256": sha256_bytes(blob),
        }
        out.append(rec)
    return out


def scan_archive(archive: RcfArchive, want=("dds", "p3d"), progress=None) -> list[dict]:
    """Return texture records for every DDS asset / embedded DDS."""
    recs: list[dict] = []
    entries = archive.entries
    for i, e in enumerate(entries):
        if progress and (i % 50 == 0 or i == len(entries) - 1):
            progress(i + 1, len(entries), e)
        kind = archive.kind(e)
        if kind not in want:
            continue
        try:
            stored = archive.read_stored(e)
        except OSError:
            continue
        payload = unpack_rz(stored) if is_rz(stored) else stored
        if kind == "dds":
            try:
                info = parse_dds(payload)
            except DdsError:
                continue
            blob = payload[:info.data_offset + info.data_size]
            recs.append({
                "id": f"{archive.name}:{fmt_hash(e.hash)}",
                "archive": archive.name,
                "entry_hash": fmt_hash(e.hash),
                "entry_name": e.name,
                "container": "dds",
                "offset_in_asset": 0,
                "name": e.name or f"{fmt_hash(e.hash)}.dds",
                "width": info.width,
                "height": info.height,
                "format": info.format_name,
                "mipmaps": info.mipmaps,
                "size": len(blob),
                "sha256": sha256_bytes(blob),
            })
        elif kind == "p3d":
            recs.extend(scan_payload_textures(payload, e.hash, e.name, archive.name))
    return recs


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def extract_texture(archive: RcfArchive, rec: dict) -> bytes:
    e = archive.entry(rec["entry_hash"])
    stored = archive.read_stored(e)
    payload = unpack_rz(stored) if is_rz(stored) else stored
    off = int(rec["offset_in_asset"])
    size = int(rec["size"])
    blob = payload[off:off + size]
    if len(blob) != size:
        raise ValueError(f"truncated texture {rec['id']}")
    return blob


def texture_png(blob: bytes) -> bytes:
    return dds_to_png(blob)


def save_texture(archive: RcfArchive, rec: dict, out_dir: str, png: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    blob = extract_texture(archive, rec)
    base = rec["name"].replace("\\", "/").rsplit("/", 1)[-1]
    stem = base[:-4] if base.lower().endswith(".dds") else base
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem) or "texture"
    fname = f"{rec['entry_hash']}_{int(rec['offset_in_asset']):08X}_{safe}.dds"
    path = os.path.join(out_dir, fname)
    with open(path, "wb") as f:
        f.write(blob)
    result = {"dds": path}
    if png:
        try:
            ppath = os.path.splitext(path)[0] + ".png"
            with open(ppath, "wb") as f:
                f.write(texture_png(blob))
            result["png"] = ppath
        except Exception as exc:  # preview is best-effort
            result["png_error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# replacement
# ---------------------------------------------------------------------------

def make_patch(archive: RcfArchive, rec: dict, new_blob: bytes) -> tuple[int, bytes]:
    """Build the new stored blob for an archive entry given replacement DDS bytes.

    The replacement must be byte-length identical to the original texture
    (same dimensions / format / mip chain), which keeps every P3D chunk size
    valid.  Enforced, not assumed.
    """
    old = extract_texture(archive, rec)
    if len(new_blob) != len(old):
        raise ValueError(
            f"replacement size {len(new_blob)} != original {len(old)} for {rec['id']}; "
            "re-export at the original dimensions/format/mip count"
        )
    old_info = parse_dds(old)
    new_info = parse_dds(new_blob)
    if (old_info.width, old_info.height) != (new_info.width, new_info.height):
        raise ValueError(
            f"dimension mismatch {new_info.width}x{new_info.height} != "
            f"{old_info.width}x{old_info.height}"
        )

    e = archive.entry(rec["entry_hash"])
    stored = archive.read_stored(e)
    if rec["container"] == "dds":
        if is_rz(stored):
            return e.hash, pack_rz(new_blob, header=stored[:8])
        return e.hash, new_blob

    payload = unpack_rz(stored) if is_rz(stored) else stored
    off = int(rec["offset_in_asset"])
    new_payload = payload[:off] + new_blob + payload[off + len(old):]
    if len(new_payload) != len(payload):
        raise ValueError("internal: payload length changed")
    if is_rz(stored):
        return e.hash, pack_rz(new_payload, header=stored[:8])
    return e.hash, new_payload


# ---------------------------------------------------------------------------
# catalogue over a game directory
# ---------------------------------------------------------------------------

def find_archives(game_dir: str) -> list[str]:
    usrdir = game_usrdir(game_dir)
    return sorted(
        os.path.join(usrdir, f)
        for f in os.listdir(usrdir)
        if f.lower().endswith(".rcf")
    )


def build_catalog(game_dir: str, archives=None, progress=None) -> dict:
    if archives is None:
        archives = DEFAULT_ARCHIVES
    paths = find_archives(game_dir)
    by_name = {os.path.basename(p).lower(): p for p in paths}
    chosen = []
    for a in archives:
        key = os.path.basename(a).lower()
        if key not in by_name:
            raise FileNotFoundError(f"archive not found: {a}")
        chosen.append(by_name[key])
    out = OrderedDict()
    out["format"] = "ps3texmod-catalog/1"
    out["game_dir"] = os.path.abspath(game_dir)
    out["archives"] = []
    out["textures"] = []
    for i, p in enumerate(chosen):
        if progress:
            progress("archive", i + 1, len(chosen), os.path.basename(p))
        ar = RcfArchive(p)
        recs = scan_archive(ar, progress=(lambda a, b, c, p=p: progress("scan", a, b, p))
                            if progress else None)
        out["archives"].append({
            **ar.stats(),
            "path": p,
            "textures": len(recs),
            "size_human": human_size(ar.file_size),
        })
        out["textures"].extend(recs)
    return out
