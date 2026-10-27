"""Compact precomputed patch plans for the console installer.

The PC tool does all format work (RCF parsing, DDS validation, RZ compression)
and records the exact bytes to write.  The PS3 homebrew then only has to:

    1. check the archive / index / original bytes,
    2. append a rollback record to a journal,
    3. write the replacement bytes and the new index size.

That keeps the C code small and removes any need for zlib, ZIP or JSON parsing
on the console.

``.ps3patch`` file layout (little-endian, C friendly)
-----------------------------------------------------
    0x00  8   b"PS3TPAT1"
    0x08  4   version                  (1)
    0x0C  4   header bytes             (64)
    0x10  4   archive name length      (bytes, UTF-8, no NUL)
    0x14  4   plan name length
    0x18  4   patch count
    0x1C  4   blob area size           (bytes, includes padding)
    0x20  4   index offset             (copy of the RCF header field)
    0x24  4   index count              (copy of the RCF entry count)
    0x28  8   archive size             (bytes, original file size)
    0x30  4   flags                    (bit0: every patch is in-place)
    0x34  4   string area size
    0x38  8   reserved
    then      archive name, plan name, per-patch labels
    then      patch_count * 48-byte records:

        0x00  4  entry hash
        0x04  4  index position (hash-sorted table)
        0x08  4  stored blob offset
        0x0C  4  old stored size
        0x10  4  new stored size
        0x14  4  slot size (offset .. next payload)
        0x18  4  blob offset (absolute file offset)
        0x1C  4  old crc32 (over [offset, offset+old_size))
        0x20  4  new crc32
        0x24  4  label offset (absolute)
        0x28  4  label length
        0x2C  4  reserved
    then      blob area (the new stored bytes)
    trailer   b"PS3TPND1", u32 crc32 of every byte before the trailer, u32 0

Journals (``.journal``) are written before the archive is touched.  A record
holds the complete original slot, so restoring the archive is byte exact::

    b"PS3TJRN1", version, index offset, index count, name len, label len,
    archive size u64, archive name, label,
    records: b"JREC", index, offset, slot size, old size, old crc32, new crc32,
             original slot bytes,
    b"PS3TJRE1", u32 crc32 of everything before the trailer, u32 0
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass, field

from .dds import DdsError, parse_dds
from .rcf import RcfArchive
from .util import atomic_write_bytes, fmt_hash, game_usrdir, resolve_archive, sha256_bytes

PLAN_MAGIC = b"PS3TPAT1"
PLAN_TRAILER = b"PS3TPND1"
PLAN_VERSION = 1
PLAN_HEADER = 64
PLAN_RECORD = 48
PLAN_FLAG_INPLACE = 1

JOURNAL_MAGIC = b"PS3TJRN1"
JOURNAL_TRAILER = b"PS3TJRE1"
JOURNAL_VERSION = 1
JOURNAL_HEADER = 36
JOURNAL_RECORD = 28

MAX_PLAN_BYTES = 512 * 1024 * 1024
MAX_NAME_BYTES = 4096
MAX_JOURNAL_SLOT_BYTES = 64 * 1024 * 1024


class PlanError(ValueError):
    pass


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _need(cond: bool, message: str) -> None:
    if not cond:
        raise PlanError(message)


def _encode(text: str) -> bytes:
    raw = (text or "").encode("utf-8", "replace")
    _need(len(raw) <= MAX_NAME_BYTES, f"name too long: {len(raw)} bytes")
    return raw


# ---------------------------------------------------------------------------
# plan model
# ---------------------------------------------------------------------------

@dataclass
class PlanPatch:
    entry_hash: int
    index: int
    offset: int
    old_size: int
    new_size: int
    slot_size: int
    old_crc32: int
    new_crc32: int
    label: str
    blob: bytes = field(repr=False)

    @property
    def texture_hash(self) -> str:
        return fmt_hash(self.entry_hash)


@dataclass
class PatchPlan:
    path: str
    name: str
    archive: str
    archive_size: int
    index_offset: int
    index_count: int
    flags: int
    patches: list[PlanPatch]

    @property
    def blob_bytes(self) -> int:
        return sum(len(p.blob) for p in self.patches)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "format": PLAN_MAGIC.decode("ascii"),
            "version": PLAN_VERSION,
            "name": self.name,
            "archive": self.archive,
            "archive_size": self.archive_size,
            "index_offset": self.index_offset,
            "index_count": self.index_count,
            "flags": self.flags,
            "patches": [
                {
                    "entry_hash": p.texture_hash,
                    "index": p.index,
                    "offset": p.offset,
                    "old_size": p.old_size,
                    "new_size": p.new_size,
                    "slot_size": p.slot_size,
                    "old_crc32": f"{p.old_crc32:08X}",
                    "new_crc32": f"{p.new_crc32:08X}",
                    "label": p.label,
                }
                for p in self.patches
            ],
        }


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------

def build_plan(archive: RcfArchive, patches: list[tuple[int, bytes, str]],
               out_path: str, *, plan_name: str, log=print) -> PatchPlan:
    """Write a plan from ``(entry_hash, new_stored_bytes, label)`` triples."""
    _need(patches, "plan has no patches")
    _need(len(patches) <= 100_000, "too many patches")
    _need(archive.info is not None, "archive was not parsed")

    records = []
    seen = set()
    for h, blob, label in patches:
        _need(len(blob) > 0, f"{fmt_hash(h)}: empty replacement")
        seen_key = int(h)
        _need(seen_key not in seen, f"duplicate patch for {fmt_hash(h)}")
        seen.add(seen_key)
        try:
            e = archive.entry(h)
        except KeyError:
            raise PlanError(f"entry {fmt_hash(h)} not found in {archive.name}")
        _need(len(blob) <= e.slot_size,
              f"{e.name or fmt_hash(h)}: replacement is {len(blob)} bytes but the "
              f"aligned slot only holds {e.slot_size}; rebuild it with the PC "
              f"installer instead (plan mode is in-place only)")
        old = archive.read_stored(e)
        records.append({
            "hash": e.hash,
            "index": e.index,
            "offset": e.offset,
            "old_size": e.size,
            "new_size": len(blob),
            "slot_size": e.slot_size,
            "old_crc32": _crc32(old),
            "new_crc32": _crc32(blob),
            "label": label or e.name or fmt_hash(e.hash),
            "blob": blob,
        })
    records.sort(key=lambda r: r["hash"])

    archive_name = _encode(archive.name)
    plan_name_raw = _encode(plan_name or os.path.basename(out_path))
    labels = [_encode(r["label"]) for r in records]

    strings = bytearray(archive_name + plan_name_raw)
    label_offsets = []
    for lab in labels:
        label_offsets.append(PLAN_HEADER + len(strings))
        strings += lab
    string_bytes = len(strings)

    record_start = PLAN_HEADER + string_bytes
    blob_start = record_start + PLAN_RECORD * len(records)

    blobs = bytearray()
    packed = []
    for r, lab, label_offset in zip(records, labels, label_offsets):
        packed.append((r, lab, label_offset, blob_start + len(blobs)))
        blobs += r["blob"]
        # keep every blob 16-byte aligned
        while len(blobs) % 16:
            blobs.append(0)

    header = bytearray(PLAN_HEADER)
    header[0:8] = PLAN_MAGIC
    struct.pack_into("<IIIIII", header, 8, PLAN_VERSION, PLAN_HEADER,
                     len(archive_name), len(plan_name_raw), len(records),
                     len(blobs))
    struct.pack_into("<II", header, 0x20, archive.info.index_offset,
                     archive.info.count)
    struct.pack_into("<Q", header, 0x28, archive.file_size)
    struct.pack_into("<II", header, 0x30, PLAN_FLAG_INPLACE, string_bytes)

    body = bytearray(header)
    body += strings
    for r, lab, label_offset, blob_offset in packed:
        rec = bytearray(PLAN_RECORD)
        struct.pack_into("<IIIIIIII", rec, 0,
                         r["hash"], r["index"], r["offset"], r["old_size"],
                         r["new_size"], r["slot_size"], blob_offset,
                         r["old_crc32"])
        struct.pack_into("<III", rec, 0x20, r["new_crc32"], label_offset, len(lab))
        body += rec
    while len(body) < blob_start:
        body.append(0)
    body += blobs
    crc = _crc32(bytes(body))
    body += PLAN_TRAILER
    body += struct.pack("<II", crc, 0)

    atomic_write_bytes(out_path, bytes(body))
    plan = PatchPlan(
        path=out_path, name=plan_name_raw.decode("utf-8", "replace"),
        archive=archive.name, archive_size=archive.file_size,
        index_offset=archive.info.index_offset, index_count=archive.info.count,
        flags=PLAN_FLAG_INPLACE,
        patches=[
            PlanPatch(r["hash"], r["index"], r["offset"], r["old_size"],
                      r["new_size"], r["slot_size"], r["old_crc32"],
                      r["new_crc32"], r["label"], r["blob"])
            for r in records
        ],
    )
    log(f"wrote {out_path}: {len(records)} patch(es) for {archive.name}, "
        f"{len(blobs)} blob bytes")
    return plan


def plan_from_pack(pack_path: str, game_dir: str, out_path: str, *,
                   name: str | None = None, log=print) -> PatchPlan:
    """Turn a validated mod pack into a precomputed console plan."""
    from .catalog import make_patch
    from .pack import load_pack

    manifest, raw = load_pack(pack_path)
    archives = sorted({r["archive"] for r in manifest["replacements"]})
    _need(len(archives) == 1,
          f"pack touches {len(archives)} archives ({', '.join(archives)}); "
          "build one plan per archive")
    archive_name = archives[0]
    archive_path = _find_archive(game_dir, archive_name)
    archive = RcfArchive(archive_path)
    patches = []
    for r in manifest["replacements"]:
        new_blob = raw[r["file"]]
        rec = {
            "id": r["texture_id"],
            "archive": archive.name,
            "entry_hash": r["entry_hash"],
            "entry_name": r.get("entry_name", ""),
            "container": r["container"],
            "offset_in_asset": int(r["offset_in_asset"]),
            "size": int(r["size"]),
        }
        try:
            h, stored = make_patch(archive, rec, new_blob)
        except (ValueError, DdsError) as exc:
            raise PlanError(f"{r['texture_id']}: {exc}")
        _need(len(stored) <= archive.entry(h).slot_size,
              f"{r['texture_id']}: compressed replacement grew to {len(stored)} "
              f"bytes, larger than the {archive.entry(h).slot_size}-byte slot; "
              "this pack needs the PC installer's rebuild path")
        patches.append((h, stored, r.get("name") or r["texture_id"]))
    plan = build_plan(
        archive, patches, out_path,
        plan_name=name or f"{manifest['name']} {manifest['version']}",
        log=log,
    )
    plan.pack_id = manifest["id"]  # type: ignore[attr-defined]
    return plan


def _find_archive(game_dir: str, archive_name: str) -> str:
    path = resolve_archive(game_dir, archive_name)
    if path is None:
        raise PlanError(f"{archive_name} not found in {game_usrdir(game_dir)}")
    return path


# ---------------------------------------------------------------------------
# reader
# ---------------------------------------------------------------------------

def load_plan(path: str) -> PatchPlan:
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_PLAN_BYTES + 1)
    except OSError as exc:
        raise PlanError(f"cannot read plan: {exc}")
    _need(len(data) <= MAX_PLAN_BYTES, "plan is unreasonably large")
    _need(len(data) >= PLAN_HEADER + 16, "plan is truncated")
    _need(data[:8] == PLAN_MAGIC, "not a ps3texmod plan (bad magic)")
    (version, header_bytes, archive_len, plan_len, patch_count, blob_bytes,
     index_offset, index_count) = struct.unpack_from("<IIIIIIII", data, 8)
    _need(version == PLAN_VERSION, f"unsupported plan version {version}")
    _need(header_bytes == PLAN_HEADER, f"unexpected plan header size {header_bytes}")
    _need(patch_count > 0, "plan contains no patches")
    _need(patch_count <= 100_000, "plan has an implausible patch count")
    _need(archive_len <= MAX_NAME_BYTES and plan_len <= MAX_NAME_BYTES,
          "plan name is too long")
    archive_size, = struct.unpack_from("<Q", data, 0x28)
    flags, string_bytes = struct.unpack_from("<II", data, 0x30)

    trailer = len(data) - 16
    _need(data[trailer:trailer + 8] == PLAN_TRAILER, "plan trailer is missing")
    stored_crc, = struct.unpack_from("<I", data, trailer + 8)
    _need(_crc32(data[:trailer]) == stored_crc, "plan checksum mismatch (corrupt file)")

    names_start = PLAN_HEADER
    record_start = names_start + string_bytes
    blob_start = record_start + PLAN_RECORD * patch_count
    _need(blob_start <= trailer, "plan records overrun the file")
    _need(blob_start + blob_bytes <= trailer, "plan blob area overruns the file")
    _need(archive_len + plan_len <= string_bytes, "plan strings overrun their area")

    archive = data[names_start:names_start + archive_len].decode("utf-8", "replace")
    plan_name = data[names_start + archive_len:
                     names_start + archive_len + plan_len].decode("utf-8", "replace")
    _need(archive, "plan has no archive name")

    patches = []
    seen = set()
    for i in range(patch_count):
        off = record_start + i * PLAN_RECORD
        (h, index, offset, old_size, new_size, slot_size, blob_offset,
         old_crc) = struct.unpack_from("<IIIIIIII", data, off)
        new_crc, label_offset, label_len = struct.unpack_from("<III", data, off + 0x20)
        _need(int(h) not in seen, f"duplicate patch for {fmt_hash(h)}")
        seen.add(int(h))
        _need(old_size > 0 and new_size > 0, "plan has an empty patch")
        _need(new_size <= slot_size, f"{fmt_hash(h)}: patch does not fit its slot")
        _need(blob_offset >= blob_start and
              blob_offset + new_size <= blob_start + blob_bytes,
              f"{fmt_hash(h)}: blob out of range")
        _need(label_offset >= names_start and label_offset + label_len <= record_start,
              f"{fmt_hash(h)}: label out of range")
        label = data[label_offset:label_offset + label_len].decode("utf-8", "replace")
        patches.append(PlanPatch(
            entry_hash=int(h), index=int(index), offset=int(offset),
            old_size=int(old_size), new_size=int(new_size),
            slot_size=int(slot_size), old_crc32=int(old_crc),
            new_crc32=int(new_crc), label=label,
            blob=bytes(data[blob_offset:blob_offset + new_size]),
        ))

    return PatchPlan(path=os.path.abspath(path), name=plan_name, archive=archive,
                     archive_size=archive_size, index_offset=index_offset,
                     index_count=index_count, flags=flags, patches=patches)


# ---------------------------------------------------------------------------
# verification / application
# ---------------------------------------------------------------------------

def verify_plan(plan: PatchPlan | str, archive_path: str, *,
                relocate: bool = False) -> dict:
    """Check that a plan can be applied to ``archive_path`` right now.

    ``relocate=True`` accepts a different archive build: every target is looked
    up by entry hash instead of by its recorded index position/offset, and is
    only accepted when its stored bytes are identical to the plan's original
    (same stored size and CRC-32) and the replacement fits the real slot.
    """
    if isinstance(plan, str):
        plan = load_plan(plan)
    problems: list[str] = []
    pending = 0
    already = 0
    relocated = 0
    try:
        ar = RcfArchive(archive_path)
    except Exception as exc:
        return {"ok": False, "problems": [str(exc)], "pending": 0,
                "already": 0, "relocated": 0}
    if os.path.basename(archive_path).lower() != plan.archive.lower():
        problems.append(f"plan targets {plan.archive}, got {os.path.basename(archive_path)}")
    if not relocate:
        if ar.info is None or ar.info.index_offset != plan.index_offset:
            problems.append("archive index offset differs from the plan")
        if ar.info is None or ar.info.count != plan.index_count:
            problems.append("archive entry count differs from the plan")
        if ar.file_size != plan.archive_size:
            problems.append(
                f"archive size {ar.file_size} != expected {plan.archive_size} "
                "(different game build?)"
            )
    for p in plan.patches:
        try:
            e = ar.entry(p.entry_hash)
        except KeyError:
            problems.append(f"{p.texture_hash}: entry not present")
            continue
        if relocate:
            cur = ar.read_stored(e)
            if len(cur) == p.new_size and _crc32(cur) == p.new_crc32:
                already += 1
            elif len(cur) != p.old_size or _crc32(cur) != p.old_crc32:
                problems.append(
                    f"{p.texture_hash}: stored texture differs from the plan "
                    "(different game build or already modded); rebuild the plan "
                    "from this archive"
                )
                continue
            elif e.slot_size < p.new_size:
                problems.append(
                    f"{p.texture_hash}: replacement ({p.new_size} bytes) does "
                    f"not fit the {e.slot_size}-byte slot"
                )
                continue
            else:
                pending += 1
            if (e.index, e.offset, e.slot_size) != (p.index, p.offset, p.slot_size):
                relocated += 1
            continue
        if e.index != p.index:
            problems.append(f"{p.texture_hash}: index position {e.index} != {p.index}")
        if e.offset != p.offset:
            problems.append(f"{p.texture_hash}: offset {e.offset:#x} != {p.offset:#x}")
            continue
        if e.slot_size < p.slot_size:
            problems.append(f"{p.texture_hash}: slot shrank")
            continue
        cur = ar.read_stored(e)
        if len(cur) == p.new_size and _crc32(cur) == p.new_crc32:
            already += 1
            continue
        if e.size != p.old_size:
            problems.append(f"{p.texture_hash}: stored size {e.size} != {p.old_size}")
            continue
        if _crc32(cur) != p.old_crc32:
            problems.append(
                f"{p.texture_hash}: original data differs from the plan "
                "(wrong build or the texture was already modded)"
            )
            continue
        pending += 1
    return {"ok": not problems, "problems": problems,
            "pending": pending, "already": already, "relocated": relocated}


def find_journals(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(os.path.join(directory, n) for n in os.listdir(directory)
                  if n.lower().endswith(".journal"))


def journal_scan(directory: str, archive: str | None = None) -> list[dict]:
    """List journals, newest first, optionally for one archive.

    Matches both the legacy ``<archive>.journal`` name written by the PC
    installer and the per-session ``<archive>.<n>.journal`` names written by
    the console installer.  Corrupt journals are returned with an ``error``
    key instead of being skipped silently; ``restore_journals`` refuses to
    write anything if any matching journal is unreadable.
    """
    if not os.path.isdir(directory):
        return []

    want = (archive or "").lower()
    legacy = f"{archive}.journal".lower() if archive else None
    prefix = f"{archive}.".lower() if archive else None
    suffix = ".journal"
    found: list[dict] = []

    for name in os.listdir(directory):
        if not name.lower().endswith(suffix):
            continue
        index = 0
        if archive:
            low = name.lower()
            if low == legacy:
                index = 0
            elif low.startswith(prefix) and low.endswith(suffix):
                middle = low[len(prefix):-len(suffix)]
                if not middle.isdigit():
                    continue
                index = int(middle)
            else:
                continue
        path = os.path.abspath(os.path.join(directory, name))
        try:
            info = journal_info(path)
        except PlanError as exc:
            info = {"path": path, "archive": archive, "error": str(exc)}
        if archive and info.get("archive") and info["archive"].lower() != want:
            continue
        info["index"] = index
        found.append(info)

    found.sort(key=lambda j: (j.get("index", 0), j.get("path", "")), reverse=True)
    return found


def restore_journals(directory: str, archive_path: str, log=print) -> dict:
    """Restore every journal for ``archive_path``, newest first.

    The console writes one journal per apply session so a crash while creating
    a journal cannot destroy earlier rollback data.  Restoring newest to oldest
    returns the archive to the state before the first apply.
    """
    archive = os.path.basename(archive_path)
    entries = journal_scan(directory, archive)
    if not entries:
        return {"restored": 0, "journals": [], "files": 0, "archive": archive}

    broken = [e for e in entries if e.get("error")]
    if broken:
        detail = "; ".join(f"{e['path']}: {e['error']}" for e in broken)
        raise PlanError(f"refusing to restore: unreadable journal(s): {detail}")

    slots = 0
    files = 0
    used: list[str] = []
    for entry in entries:
        res = restore_journal(entry["path"], archive_path, log=log)
        slots += int(res["restored"])
        files += 1
        used.append(entry["path"])
    return {"restored": slots, "journals": used, "files": files, "archive": archive}


def apply_plan(plan: PatchPlan | str, archive_path: str, *, journal_dir: str,
               dry_run: bool = False, relocate: bool = False,
               log=print) -> dict:
    """Apply a plan on the PC, using the same journal format as the console.

    With ``relocate=True`` a different archive build is accepted when every
    target's stored bytes still match the plan (see :func:`verify_plan`).  The
    recorded offsets, index positions and slot sizes then come from the real
    archive, and the journal records those values so restore stays exact.
    """
    if isinstance(plan, str):
        plan = load_plan(plan)
    check = verify_plan(plan, archive_path, relocate=relocate)
    if not check["ok"]:
        raise PlanError("; ".join(check["problems"]))
    if dry_run:
        return {**check, "applied": 0, "journal": None}

    ar = RcfArchive(archive_path)
    records = []
    placed = []
    for p in plan.patches:
        e = ar.entry(p.entry_hash)
        if relocate:
            index, offset, slot_size = e.index, e.offset, e.slot_size
        else:
            index, offset, slot_size = p.index, p.offset, p.slot_size
        cur = ar.read_stored(e)
        if len(cur) == p.new_size and _crc32(cur) == p.new_crc32:
            continue
        slot = _read_at(archive_path, offset, slot_size)
        if _crc32(slot[:p.old_size]) != p.old_crc32:
            raise PlanError(f"{p.texture_hash}: original data changed while applying")
        records.append({
            "index": index, "offset": offset, "slot_size": slot_size,
            "old_size": p.old_size, "old_crc32": p.old_crc32,
            "new_crc32": p.new_crc32, "old_slot": slot,
        })
        placed.append((p, index, offset, slot_size))

    journal_path = os.path.join(journal_dir, plan.archive + ".journal")
    os.makedirs(journal_dir, exist_ok=True)
    if records:
        _append_journal(journal_path, archive_path, plan.name, records)

    applied = 0
    if placed:
        with open(archive_path, "r+b") as f:
            for p, index, offset, slot_size in placed:
                f.seek(offset)
                f.write(p.blob)
                pad = slot_size - len(p.blob)
                if pad:
                    f.write(b"\x00" * pad)
                f.seek(ar.info.index_offset + index * 12 + 8)
                f.write(struct.pack(">I", p.new_size))
                applied += 1
            f.flush()
            os.fsync(f.fileno())
    log(f"{archive_path}: {applied} texture(s) written, "
        f"{check['already']} already applied; journal {journal_path}")
    return {"ok": True, "applied": applied, "already": check["already"],
            "journal": journal_path if records else None,
            "patches": check["pending"] + check["already"],
            "relocated": check.get("relocated", 0)}


def _read_at(path: str, offset: int, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(size)
    if len(data) != size:
        raise PlanError("short read from archive")
    return data


# ---------------------------------------------------------------------------
# journals
# ---------------------------------------------------------------------------

def _journal_header(archive_name: bytes, label: bytes, archive_size: int,
                    index_offset: int, index_count: int) -> bytes:
    head = bytearray(JOURNAL_HEADER)
    head[0:8] = JOURNAL_MAGIC
    struct.pack_into("<IIII", head, 8, JOURNAL_VERSION, index_offset,
                     index_count, len(archive_name))
    struct.pack_into("<I", head, 24, len(label))
    struct.pack_into("<Q", head, 28, archive_size)
    return bytes(head) + archive_name + label


def _parse_journal(path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    _need(len(data) >= JOURNAL_HEADER + 16, "journal is truncated")
    _need(data[:8] == JOURNAL_MAGIC, "not a ps3texmod journal")
    version, index_offset, index_count, archive_len = struct.unpack_from(
        "<IIII", data, 8)
    label_len, = struct.unpack_from("<I", data, 24)
    archive_size, = struct.unpack_from("<Q", data, 28)
    _need(version == JOURNAL_VERSION, f"unsupported journal version {version}")
    _need(JOURNAL_HEADER + archive_len + label_len <= len(data) - 16,
          "journal header overruns the file")
    trailer = len(data) - 16
    _need(data[trailer:trailer + 8] == JOURNAL_TRAILER, "journal trailer is missing")
    stored_crc, = struct.unpack_from("<I", data, trailer + 8)
    _need(_crc32(data[:trailer]) == stored_crc, "journal checksum mismatch")

    pos = JOURNAL_HEADER
    archive = data[pos:pos + archive_len].decode("utf-8", "replace")
    pos += archive_len
    label = data[pos:pos + label_len].decode("utf-8", "replace")
    pos += label_len
    records = []
    while pos < trailer:
        _need(pos + JOURNAL_RECORD <= trailer, "journal record overruns the file")
        _need(data[pos:pos + 4] == b"JREC", f"bad journal record at {pos:#x}")
        index, offset, slot_size, old_size, old_crc, new_crc = struct.unpack_from(
            "<IIIIII", data, pos + 4)
        pos += JOURNAL_RECORD
        _need(old_size > 0 and old_size <= slot_size, "journal record has bad sizes")
        _need(slot_size <= MAX_JOURNAL_SLOT_BYTES, "journal slot is too large")
        _need(pos + slot_size <= trailer, "journal slot overruns the file")
        slot = data[pos:pos + slot_size]
        pos += slot_size
        _need(_crc32(slot[:old_size]) == old_crc, "journal slot checksum mismatch")
        records.append({
            "index": index, "offset": offset, "slot_size": slot_size,
            "old_size": old_size, "old_crc32": old_crc,
            "new_crc32": new_crc, "old_slot": slot,
        })
    _need(pos == trailer, "journal has trailing bytes")
    return {"path": os.path.abspath(path), "archive": archive, "label": label,
            "archive_size": archive_size, "index_offset": index_offset,
            "index_count": index_count, "records": records}


def journal_info(path: str) -> dict:
    j = _parse_journal(path)
    return {
        "path": j["path"],
        "archive": j["archive"],
        "label": j["label"],
        "archive_size": j["archive_size"],
        "records": len(j["records"]),
        "bytes": sum(r["slot_size"] for r in j["records"]),
    }


def _append_journal(path: str, archive_path: str, label: str,
                    new_records: list[dict]) -> None:
    archive = os.path.basename(archive_path)
    size = os.path.getsize(archive_path)
    ar = RcfArchive(archive_path)
    if os.path.isfile(path):
        j = _parse_journal(path)
        _need(j["archive"].lower() == archive.lower(),
              f"journal belongs to {j['archive']}, not {archive}")
        _need(j["archive_size"] == size,
              "archive size changed since the journal was created")
        _need(j["index_offset"] == ar.info.index_offset and
              j["index_count"] == ar.info.count,
              "archive index layout changed since the journal was created")
        old_records = j["records"]
        label = j["label"] or label
    else:
        old_records = []
    all_records = old_records + new_records
    total = sum(r["slot_size"] for r in all_records)
    _need(total <= MAX_JOURNAL_SLOT_BYTES * 8, "journal would be too large")

    body = bytearray(_journal_header(archive.encode(), label.encode("utf-8", "replace"),
                                     size, ar.info.index_offset, ar.info.count))
    for r in all_records:
        body += b"JREC"
        body += struct.pack("<IIIIII", r["index"], r["offset"], r["slot_size"],
                            r["old_size"], r["old_crc32"], r["new_crc32"])
        body += r["old_slot"]
    crc = _crc32(bytes(body))
    body += JOURNAL_TRAILER
    body += struct.pack("<II", crc, 0)
    atomic_write_bytes(path, bytes(body))


def restore_journal(journal_path: str, archive_path: str, log=print) -> dict:
    j = _parse_journal(journal_path)
    if os.path.basename(archive_path).lower() != j["archive"].lower():
        raise PlanError(f"journal is for {j['archive']}, not {archive_path}")
    if os.path.getsize(archive_path) != j["archive_size"]:
        raise PlanError(
            f"archive size {os.path.getsize(archive_path)} != journal size "
            f"{j['archive_size']}; refusing to restore"
        )
    ar = RcfArchive(archive_path)
    if ar.info.index_offset != j["index_offset"] or ar.info.count != j["index_count"]:
        raise PlanError("archive index layout changed; refusing to restore")
    with open(archive_path, "r+b") as f:
        for r in reversed(j["records"]):
            f.seek(r["offset"])
            f.write(r["old_slot"])
            f.seek(ar.info.index_offset + r["index"] * 12 + 8)
            f.write(struct.pack(">I", r["old_size"]))
        f.flush()
        os.fsync(f.fileno())
    os.unlink(journal_path)
    log(f"restored {len(j['records'])} slot(s) in {archive_path}")
    return {"restored": len(j["records"]), "journal": journal_path}


__all__ = [
    "PlanError", "PatchPlan", "PlanPatch", "build_plan", "plan_from_pack",
    "load_plan", "verify_plan", "apply_plan", "restore_journal",
    "restore_journals", "journal_info", "journal_scan", "find_journals",
    "PLAN_MAGIC", "JOURNAL_MAGIC",
]

_ = sha256_bytes  # re-export friendly
