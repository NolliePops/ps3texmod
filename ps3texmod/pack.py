"""Mod-pack format: authoring, packaging, validation.

A pack is a ZIP archive:

    manifest.json          machine readable description
    checksums.sha256       sha256 of every other file
    textures/<file>.dds    replacement textures (byte-identical size)

The manifest is deliberately explicit: every replacement records the target
archive, the entry's name hash, the offset inside the asset, the original
texture sha256 and the replacement sha256.  That makes installation verifiable
and reversible without guessing which game build is present.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import zipfile

from .catalog import extract_texture, make_patch
from .dds import DdsError, parse_dds
from .rcf import RcfArchive
from .util import fmt_hash, parse_hash, sha256_bytes

PACK_FORMAT = "ps3texmod/1"
MANIFEST = "manifest.json"
CHECKSUMS = "checksums.sha256"


class PackError(ValueError):
    pass


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "pack"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# target resolution
# ---------------------------------------------------------------------------

def _open_archives(game_dir: str) -> dict[str, RcfArchive]:
    from .catalog import find_archives
    out = {}
    for p in find_archives(game_dir):
        out[os.path.basename(p).lower()] = RcfArchive(p)
    return out


def resolve_token(archives: dict[str, RcfArchive], token: str,
                  archive_hint: str | None = None) -> dict:
    """Resolve ``archive:HASH[:OFFSET]``, ``HASH[:OFFSET]`` or a texture id."""
    token = token.strip()
    if not token:
        raise PackError("empty texture reference")
    parts = token.split(":")
    archive = None
    h = None
    offset = None

    if len(parts) >= 2 and parts[0].lower().endswith(".rcf"):
        archive = parts[0].lower()
        try:
            h = parse_hash(parts[1])
            if len(parts) >= 3:
                offset = int(parts[2], 16)
        except ValueError:
            raise PackError(f"cannot parse texture reference {token!r}")
    elif len(parts) == 1:
        try:
            h = parse_hash(parts[0])
        except ValueError:
            raise PackError(f"cannot parse texture reference {token!r}")
        archive = (archive_hint or "").lower() or None
    elif len(parts) == 2:
        try:
            h = parse_hash(parts[0])
            offset = int(parts[1], 16)
        except ValueError:
            raise PackError(f"cannot parse texture reference {token!r}")
        archive = (archive_hint or "").lower() or None
    else:
        raise PackError(f"cannot parse texture reference {token!r}")

    if archive is None:
        if len(archives) == 1:
            archive = next(iter(archives))
        else:
            raise PackError(
                f"archive not specified for {token!r}; use archive.rcf:HASH or --archive"
            )
    if archive not in archives:
        raise PackError(f"archive {archive} not found in game directory")
    ar = archives[archive]
    try:
        e = ar.entry(h)
    except KeyError:
        raise PackError(f"{token}: entry not found in {archive}")
    stored = ar.read_stored(e)
    from .rz import is_rz, unpack_rz
    payload = unpack_rz(stored) if is_rz(stored) else stored
    if offset is None:
        if e.ext == "dds" and not is_rz(stored):
            offset = 0
        elif e.ext in ("dds",) and is_rz(stored):
            offset = 0
        else:
            # find the first embedded DDS
            from .dds import iter_dds
            offs = [o for o, _ in iter_dds(payload)]
            if not offs:
                raise PackError(f"{token}: asset contains no DDS texture")
            offset = offs[0]
    blob = payload[offset:]
    try:
        info = parse_dds(blob)
    except DdsError as exc:
        raise PackError(f"{token}: no DDS at offset {offset:#x} ({exc})")
    orig = blob[:info.data_offset + info.data_size]
    rec = {
        "archive": ar.name,
        "entry_hash": fmt_hash(e.hash),
        "entry_name": e.name,
        "container": "dds" if (e.ext == "dds" and offset == 0) else "p3d",
        "offset_in_asset": offset,
        "width": info.width,
        "height": info.height,
        "format": info.format_name,
        "mipmaps": info.mipmaps,
        "size": len(orig),
        "original_sha256": sha256_bytes(orig),
    }
    rec["_archive"] = ar
    rec["_original"] = orig
    return rec


# ---------------------------------------------------------------------------
# definition files (Texmod compatible-ish)
# ---------------------------------------------------------------------------

def parse_definition(path: str) -> list[tuple[str, str]]:
    """Parse ``<id>|<texture file>`` lines; '#' starts a comment."""
    entries = []
    base = os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("//"):
                continue
            if "|" not in s:
                raise PackError(f"{path}:{lineno}: expected <id>|<file>")
            ident, filename = s.split("|", 1)
            ident = ident.strip()
            filename = filename.strip()
            if not ident or not filename:
                raise PackError(f"{path}:{lineno}: empty id or file")
            if not os.path.isabs(filename):
                filename = os.path.join(base, filename)
            entries.append((ident, filename))
    if not entries:
        raise PackError(f"{path}: no definitions found")
    return entries


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build_pack(out_path: str, replacements: list[dict], *, name: str,
               version: str = "1.0.0", author: str = "", description: str = "",
               title_id: str | None = None, game_dir: str | None = None,
               pack_id: str | None = None, log=print) -> dict:
    """Create a pack from resolved replacements.

    Each replacement dict must contain the resolved target fields plus a
    ``source_file`` path to the replacement DDS.
    """
    if not replacements:
        raise PackError("no replacements to pack")
    pid = pack_id or _slug(name)
    seen = set()
    files: dict[str, bytes] = {}
    manifest_reps = []
    for r in replacements:
        src = r["source_file"]
        with open(src, "rb") as f:
            new_blob = f.read()
        orig = r["_original"]
        if len(new_blob) != len(orig):
            raise PackError(
                f"{os.path.basename(src)}: size {len(new_blob)} != original {len(orig)} "
                f"({r['width']}x{r['height']} {r['format']} mips={r['mipmaps']})"
            )
        try:
            ni = parse_dds(new_blob)
        except DdsError as exc:
            raise PackError(f"{src}: {exc}")
        if (ni.width, ni.height) != (r["width"], r["height"]):
            raise PackError(
                f"{src}: dimensions {ni.width}x{ni.height} != "
                f"{r['width']}x{r['height']}"
            )
        if ni.format_name != r["format"]:
            raise PackError(
                f"{src}: format {ni.format_name} != {r['format']} "
                "(re-export with the original pixel format)"
            )
        if ni.mipmaps != r["mipmaps"]:
            raise PackError(
                f"{src}: mip count {ni.mipmaps} != {r['mipmaps']}"
            )
        key = (r["archive"].lower(), r["entry_hash"], r["offset_in_asset"])
        if key in seen:
            raise PackError(f"duplicate replacement for {r['entry_hash']}:{r['offset_in_asset']:X}")
        seen.add(key)
        arc_stem = os.path.splitext(r["archive"])[0]
        fname = (f"textures/{arc_stem}_{r['entry_hash']}_"
                 f"{int(r['offset_in_asset']):08X}.dds")
        while fname in files:
            fname = fname.replace(".dds", "_1.dds")
        files[fname] = new_blob
        manifest_reps.append({
            "texture_id": f"{r['archive']}:{r['entry_hash']}:{int(r['offset_in_asset']):08X}",
            "archive": r["archive"],
            "entry_hash": r["entry_hash"],
            "entry_name": r.get("entry_name", ""),
            "container": r["container"],
            "offset_in_asset": int(r["offset_in_asset"]),
            "name": r.get("entry_name") or f"{r['entry_hash']}.dds",
            "width": r["width"],
            "height": r["height"],
            "format": r["format"],
            "mipmaps": r["mipmaps"],
            "size": len(orig),
            "original_sha256": r["original_sha256"],
            "replacement_sha256": sha256_bytes(new_blob),
            "file": fname,
        })
        log(f"  + {fname}  ({r['width']}x{r['height']} {r['format']}) "
            f"<- {os.path.basename(src)}")

    archives = sorted({r["archive"] for r in manifest_reps})
    manifest = {
        "format": PACK_FORMAT,
        "id": pid,
        "name": name,
        "version": version,
        "author": author,
        "description": description,
        "created_utc": _now(),
        "game": {
            "game": "Prototype (PS3)",
            "title_id": title_id,
            "archives": archives,
            "game_dir_hint": os.path.abspath(game_dir) if game_dir else None,
        },
        "replacement_count": len(manifest_reps),
        "replacements": manifest_reps,
    }

    checks = []
    for fname, data in files.items():
        checks.append((sha256_bytes(data), fname))
    checks.append((sha256_bytes(_json_bytes(manifest)), MANIFEST))
    checks.sort(key=lambda t: t[1])
    ck_text = "".join(f"{h}  {n}\n" for h, n in checks)

    tmp = out_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(MANIFEST, _json_bytes(manifest))
        z.writestr(CHECKSUMS, ck_text.encode("utf-8"))
        for fname, data in files.items():
            z.writestr(fname, data)
    os.replace(tmp, out_path)
    return manifest


def _json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# load / validate
# ---------------------------------------------------------------------------

def load_pack(path: str) -> tuple[dict, dict[str, bytes]]:
    if not os.path.isfile(path):
        raise PackError(f"pack not found: {path}")
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if MANIFEST not in names:
                raise PackError("pack has no manifest.json")
            try:
                manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PackError(f"manifest.json is not valid JSON: {exc}") from exc
            raw = {n: z.read(n) for n in names if not n.endswith("/")}
    except zipfile.BadZipFile as exc:
        raise PackError(f"not a valid pack archive: {exc}") from exc
    _validate_manifest(manifest)
    # checksums
    if CHECKSUMS in raw:
        expected = {}
        for line in raw[CHECKSUMS].decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            h, _, n = line.partition("  ")
            expected[n] = h
        for n, h in expected.items():
            if n not in raw:
                raise PackError(f"checksums reference missing file {n}")
            if sha256_bytes(raw[n]) != h:
                raise PackError(f"checksum mismatch for {n}")
        for n in raw:
            if n not in expected and n != CHECKSUMS:
                raise PackError(f"unchecksummed file in pack: {n}")
    for r in manifest["replacements"]:
        if r["file"] not in raw:
            raise PackError(f"missing replacement file {r['file']}")
        if sha256_bytes(raw[r["file"]]) != r["replacement_sha256"]:
            raise PackError(f"replacement hash mismatch: {r['file']}")
    return manifest, raw


def _validate_manifest(m: dict) -> None:
    if not isinstance(m, dict):
        raise PackError("manifest is not an object")
    if m.get("format") != PACK_FORMAT:
        raise PackError(f"unsupported pack format {m.get('format')!r}")
    for k in ("id", "name", "version", "replacements"):
        if k not in m:
            raise PackError(f"manifest missing {k!r}")
    reps = m["replacements"]
    if not isinstance(reps, list) or not reps:
        raise PackError("manifest has no replacements")
    ids = set()
    for r in reps:
        for k in ("archive", "entry_hash", "offset_in_asset", "size",
                  "original_sha256", "replacement_sha256", "file",
                  "width", "height", "format", "mipmaps"):
            if k not in r:
                raise PackError(f"replacement missing {k!r}")
        key = (r["archive"].lower(), r["entry_hash"], int(r["offset_in_asset"]))
        if key in ids:
            raise PackError(f"duplicate replacement {key}")
        ids.add(key)
        int(r["entry_hash"], 16)


def pack_info(path: str) -> dict:
    m, raw = load_pack(path)
    return {
        "path": path,
        "id": m["id"],
        "name": m["name"],
        "version": m["version"],
        "author": m.get("author", ""),
        "description": m.get("description", ""),
        "archives": m["game"].get("archives", []),
        "replacements": m["replacement_count"],
        "bytes": os.path.getsize(path),
        "entries": [
            {
                "texture_id": r["texture_id"],
                "name": r.get("name", ""),
                "size": r["size"],
                "format": r["format"],
                "dims": f"{r['width']}x{r['height']}",
                "file": r["file"],
            }
            for r in m["replacements"]
        ],
    }


def validate_against_game(path: str, game_dir: str, log=print) -> dict:
    """Check that a pack can be installed on the given game directory."""
    m, raw = load_pack(path)
    from .catalog import find_archives
    paths = {os.path.basename(p).lower(): p for p in find_archives(game_dir)}
    problems = []
    checked = []
    cache = {}
    for r in m["replacements"]:
        a = r["archive"].lower()
        if a not in paths:
            problems.append(f"missing archive {r['archive']}")
            continue
        ar = cache.get(a)
        if ar is None:
            ar = cache[a] = RcfArchive(paths[a])
        try:
            e = ar.entry(int(r["entry_hash"], 16))
        except KeyError:
            problems.append(f"{r['entry_hash']}: entry not found in {r['archive']}")
            continue
        rec = {
            "id": r["texture_id"], "archive": ar.name,
            "entry_hash": r["entry_hash"], "entry_name": e.name,
            "container": r["container"], "offset_in_asset": int(r["offset_in_asset"]),
            "size": int(r["size"]),
        }
        try:
            cur = extract_texture(ar, rec)
        except Exception as exc:
            problems.append(f"{r['texture_id']}: {exc}")
            continue
        if sha256_bytes(cur) != r["original_sha256"]:
            problems.append(
                f"{r['texture_id']}: installed texture differs from the pack's "
                "expected original (different game build or already modded)"
            )
        checked.append(r["texture_id"])
    return {"ok": not problems, "problems": problems, "checked": checked, "manifest": m}
