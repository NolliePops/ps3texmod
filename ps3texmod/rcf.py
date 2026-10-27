"""Cement RCF archive reader / patcher / rebuilder.

Format (verified against retail Prototype PS3 data)
---------------------------------------------------
0x00  24  "ATG CORE CEMENT LIBRARY"
0x18   8  zero
0x20   4  version (0x02010101)
0x24   4  index offset          (always 0x3C in observed files)
0x28   4  index byte size       (= 12 * entry_count)
0x2C   4  name-table offset     (0x800-aligned)
0x30   4  name-table byte size
0x34   4  zero
0x38   4  entry count

Index: entry_count big-endian triples (hash:u32, offset:u32, size:u32),
sorted ascending by hash.

Name table: 8-byte prefix (0x800, 0), then records:
    u32 hash_low24_with_0x49_tag   (little-endian when read as a word)
    u32 0x00000800
    u32 0x00000000
    u32 name_length_including_NUL  (little-endian)
    name bytes (NUL terminated)
    3 filler bytes

Key discovery: **the name table is stored in payload-offset order**.  Sorting
the index by offset and zipping it with the name list reproduces every name
correctly (verified on 2,938 assets across scripts/shaders/art/cached/woi9:
every .dds maps to a DDS header, .gfx to GFX, .bik to BIKi, .rz to RZ).

Payloads of `.rz` assets are RZ containers (zlib).  Everything else is raw.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from .rz import is_rz, pack_rz, unpack_rz
from .util import align_up, fmt_hash, norm_name, sha256_region

MAGIC = b"ATG CORE CEMENT LIBRARY\x00"
HEADER_SIZE = 0x3C
ALIGNMENT = 0x800


class RcfError(RuntimeError):
    pass


@dataclass
class RcfEntry:
    hash: int
    offset: int
    size: int
    index: int
    name: str = ""
    slot_end: int = 0

    @property
    def slot_size(self) -> int:
        return max(0, self.slot_end - self.offset)

    @property
    def ext(self) -> str:
        n = self.name.replace("\\", "/")
        base = n.rsplit("/", 1)[-1]
        if "." not in base:
            return ""
        return base.rsplit(".", 1)[-1].lower()

    def to_dict(self) -> dict:
        return {
            "hash": fmt_hash(self.hash),
            "offset": self.offset,
            "size": self.size,
            "name": self.name,
            "ext": self.ext,
        }


@dataclass
class RcfInfo:
    path: str
    version: int
    index_offset: int
    index_size: int
    table_offset: int
    table_size: int
    count: int
    file_size: int
    data_start: int
    names_ok: bool
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "path": self.path,
            "magic": MAGIC.rstrip(b"\x00").decode("ascii"),
            "version": f"{self.version:08X}",
            "index_offset": self.index_offset,
            "index_size": self.index_size,
            "table_offset": self.table_offset,
            "table_size": self.table_size,
            "count": self.count,
            "file_size": self.file_size,
            "data_start": self.data_start,
            "names_ok": self.names_ok,
        }
        d.update(self.extra)
        return d


class RcfArchive:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.file_size = os.path.getsize(self.path)
        self.info: RcfInfo | None = None
        self.entries: list[RcfEntry] = []          # index order (hash sorted)
        self._by_hash: dict[int, RcfEntry] = {}
        self._by_name: dict[str, RcfEntry] = {}
        self._layout: list[RcfEntry] = []          # offset order
        self._parse()

    # ------------------------------------------------------------------ parse
    def _parse(self) -> None:
        with open(self.path, "rb") as f:
            head = f.read(HEADER_SIZE)
            if len(head) < HEADER_SIZE:
                raise RcfError(f"{self.path}: file too small for RCF header")
            if head[:24] != MAGIC:
                raise RcfError(f"{self.path}: not an RCF archive (bad magic)")
            version, idx_off, idx_size, tab_off, tab_size, _zero, count = struct.unpack_from(
                ">7I", head, 0x20
            )
            if count == 0 or count > 2_000_000:
                raise RcfError(f"{self.path}: implausible entry count {count}")
            if idx_size != 12 * count:
                # tolerate but remember; use the smaller consistent value
                if idx_size // 12:
                    count = min(count, idx_size // 12)
            if idx_off + 12 * count > self.file_size:
                raise RcfError(f"{self.path}: index out of range")
            f.seek(idx_off)
            idx = f.read(12 * count)
            f.seek(tab_off)
            table = f.read(tab_size)

        entries: list[RcfEntry] = []
        for i in range(count):
            h, off, size = struct.unpack_from(">III", idx, i * 12)
            if off + size > self.file_size:
                raise RcfError(
                    f"{self.path}: entry {i} exceeds file (off={off:#x} size={size})"
                )
            entries.append(RcfEntry(h, off, size, i))

        names = _parse_name_table(table, count)
        if len(names) == len(entries) and len(entries) > 0:
            layout = sorted(entries, key=lambda e: (e.offset, e.index))
            for e, nm in zip(layout, names):
                e.name = nm
            names_ok = True
        else:
            layout = sorted(entries, key=lambda e: (e.offset, e.index))
            names_ok = False

        eof = self.file_size
        for j, e in enumerate(layout):
            e.slot_end = layout[j + 1].offset if j + 1 < len(layout) else eof

        self.entries = entries
        self._layout = layout
        self._by_hash = {e.hash: e for e in entries}
        self._by_name = {norm_name(e.name): e for e in entries if e.name}
        data_start = align_up(tab_off + tab_size, ALIGNMENT)
        self.info = RcfInfo(
            path=self.path,
            version=version,
            index_offset=idx_off,
            index_size=idx_size,
            table_offset=tab_off,
            table_size=tab_size,
            count=count,
            file_size=self.file_size,
            data_start=data_start,
            names_ok=names_ok,
        )

    # ------------------------------------------------------------- accessors
    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def entry(self, key) -> RcfEntry:
        if isinstance(key, RcfEntry):
            return key
        if isinstance(key, int):
            if key in self._by_hash:
                return self._by_hash[key]
            raise KeyError(f"no entry with hash {key:08X}")
        s = str(key).strip()
        if s.lower().startswith("0x"):
            s = s[2:]
        if len(s) == 8:
            try:
                h = int(s, 16)
            except ValueError:
                h = None
            if h is not None and h in self._by_hash:
                return self._by_hash[h]
        n = norm_name(s)
        if n in self._by_name:
            return self._by_name[n]
        # allow suffix match ("alex.p3d.rz")
        cands = [e for e in self.entries if e.name and norm_name(e.name).endswith(n)]
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            raise KeyError(f"ambiguous name {key!r} ({len(cands)} matches)")
        raise KeyError(f"entry not found: {key!r}")

    def read_stored(self, entry) -> bytes:
        e = self.entry(entry)
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            data = f.read(e.size)
        if len(data) != e.size:
            raise RcfError(f"short read for {e.name or fmt_hash(e.hash)}")
        return data

    def read_payload(self, entry) -> bytes:
        blob = self.read_stored(entry)
        if is_rz(blob):
            return unpack_rz(blob)
        return blob

    def kind(self, entry) -> str:
        e = self.entry(entry)
        st = e.ext
        if st == "dds":
            return "dds"
        if st == "gfx":
            return "gfx"
        if st == "bik":
            return "bik"
        if st == "png":
            return "png"
        if st == "rz" or st == "p3d":
            return "p3d"
        return st or "unknown"

    # ---------------------------------------------------------------- patching
    def apply_patches(self, patches: dict[int, bytes], out_path: str | None = None,
                      inplace_ok: bool = True) -> dict:
        """Apply hash -> new stored blob patches.

        When every new blob fits its original aligned slot the archive is
        patched in place (fast, keeps offsets).  Otherwise a full rebuild is
        written to ``out_path`` (default: same path, atomic replace).
        Returns a summary dict.
        """
        if not patches:
            return {"mode": "noop", "patched": []}
        prepared: dict[int, bytes] = {}
        for h, blob in patches.items():
            e = self.entry(h)
            if is_rz(self.read_stored(e)) and not is_rz(blob):
                raise RcfError(f"entry {fmt_hash(h)} expects an RZ container")
            prepared[e.hash] = blob

        fits = all(
            len(prepared[e.hash]) <= e.slot_size for e in self.entries if e.hash in prepared
        )
        if fits and inplace_ok:
            self._patch_inplace(prepared)
            return {
                "mode": "inplace",
                "patched": [fmt_hash(h) for h in prepared],
                "sizes": {fmt_hash(h): len(b) for h, b in prepared.items()},
            }
        target = out_path or self.path
        if target == self.path:
            return {"mode": "rebuild-inplace", **self._rebuild(prepared, target)}
        return {"mode": "rebuild", **self._rebuild(prepared, target)}

    def _patch_inplace(self, prepared: dict[int, bytes]) -> None:
        with open(self.path, "r+b") as f:
            for h, blob in prepared.items():
                e = self._by_hash[h]
                if len(blob) > e.slot_size:
                    raise RcfError("internal: patch does not fit")
                f.seek(e.offset)
                f.write(blob)
                remaining = e.slot_size - len(blob)
                if remaining:
                    f.write(b"\x00" * remaining)
                # update size field in the index
                f.seek(self.info.index_offset + e.index * 12 + 8)
                f.write(struct.pack(">I", len(blob)))
                e.size = len(blob)
        # refresh index cache
        self._parse()

    def _rebuild(self, prepared: dict[int, bytes], target: str) -> dict:
        """Rewrite the whole archive in layout order with fresh offsets."""
        import tempfile
        inplace = os.path.abspath(target) == self.path
        if inplace:
            fd, work = tempfile.mkstemp(prefix=".ps3texmod-rebuild-",
                                        dir=os.path.dirname(self.path))
            os.close(fd)
            os.unlink(work)
            out_path = work
        else:
            out_path = target
        data_start = self.info.data_start
        write_off = data_start
        layout = self._layout
        new_index = {}
        with open(self.path, "rb") as src, open(out_path, "wb") as dst:
            # copy everything up to the first payload (header+index+names+pad)
            src.seek(0)
            head = src.read(data_start)
            if len(head) != data_start:
                raise RcfError("cannot read archive head")
            dst.write(head)
            for e in layout:
                pad = write_off - dst.tell()
                if pad > 0:
                    dst.write(b"\x00" * pad)
                if e.hash in prepared:
                    blob = prepared[e.hash]
                else:
                    src.seek(e.offset)
                    blob = src.read(e.size)
                dst.write(blob)
                new_index[e.hash] = (write_off, len(blob))
                write_off = align_up(write_off + len(blob), ALIGNMENT)
            # trailing alignment padding omitted (not required)
            dst.truncate()
            # rewrite index triples with new offsets/sizes, preserving hashes
            dst.seek(self.info.index_offset)
            for e in self.entries:
                off, size = new_index[e.hash]
                dst.write(struct.pack(">III", e.hash, off, size))
            dst.flush()
            os.fsync(dst.fileno())
        if inplace:
            os.replace(out_path, self.path)
            self.file_size = os.path.getsize(self.path)
            self.entries = []
            self._parse()
        return {
            "entries": len(layout),
            "bytes_written": write_off,
            "target": target,
        }

    def rebuild_copy(self, target: str, patches: dict[int, bytes] | None = None) -> dict:
        dummy = RcfArchive.__new__(RcfArchive)
        dummy.path = self.path
        dummy.file_size = self.file_size
        dummy.info = self.info
        dummy.entries = self.entries
        dummy._by_hash = self._by_hash
        dummy._by_name = self._by_name
        dummy._layout = self._layout
        return dummy._rebuild(patches or {}, target)

    # ------------------------------------------------------------- validation
    def validate(self, deep: bool = False, limit: int | None = None) -> dict:
        problems: list[str] = []
        if not self.info.names_ok:
            problems.append("name table could not be mapped to entries")
        hashes = [e.hash for e in self.entries]
        if len(set(hashes)) != len(hashes):
            problems.append("duplicate entry hashes")
        if hashes != sorted(hashes):
            problems.append("index is not sorted by hash")
        overlaps = 0
        for a, b in zip(self._layout, self._layout[1:]):
            if a.offset + a.size > b.offset:
                overlaps += 1
                if len(problems) < 20:
                    problems.append(
                        f"overlap: {a.name or fmt_hash(a.hash)} end={a.offset + a.size:#x} "
                        f"> next {b.name or fmt_hash(b.hash)} start={b.offset:#x}"
                    )
        if overlaps:
            problems.append(f"{overlaps} overlapping payload slots")
        if self._layout and self._layout[0].offset < self.info.table_offset + self.info.table_size:
            problems.append("first payload starts inside the name table")
        if self._layout and self._layout[0].offset != self.info.data_start:
            problems.append(
                f"first payload at {self._layout[0].offset:#x}, expected {self.info.data_start:#x}"
            )
        bad_ext = 0
        checked = 0
        if deep:
            import zlib
            from .dds import DDS_MAGIC
            with open(self.path, "rb") as f:
                for e in self._layout:
                    if limit is not None and checked >= limit:
                        break
                    f.seek(e.offset)
                    b = f.read(16)
                    checked += 1
                    ext = e.ext
                    if ext == "dds" and b[:4] != DDS_MAGIC:
                        bad_ext += 1
                    elif ext == "gfx" and b[:3] != b"GFX":
                        bad_ext += 1
                    elif ext == "bik" and b[:4] not in (b"BIKi", b"BIK"):
                        bad_ext += 1
                    elif ext == "rz":
                        if b[:2] != b"RZ":
                            bad_ext += 1
                        else:
                            try:
                                hdr = struct.unpack_from("<Q", b, 8)[0]
                                if hdr == 0:
                                    bad_ext += 1
                            except struct.error:
                                bad_ext += 1
                    if bad_ext > 20:
                        break
        if bad_ext:
            problems.append(f"{bad_ext} payload/extension mismatch(es)")
        return {
            "path": self.path,
            "ok": not problems,
            "problems": problems,
            "entries": len(self.entries),
            "names": sum(1 for e in self.entries if e.name),
            "deep_checked": checked,
            "bad_ext": bad_ext,
        }

    def stats(self) -> dict:
        from collections import Counter
        kinds = Counter(self.kind(e) for e in self.entries)
        sizes = [e.size for e in self.entries]
        return {
            "archive": self.name,
            "entries": len(self.entries),
            "kinds": dict(kinds),
            "total_payload": sum(sizes),
            "min_size": min(sizes) if sizes else 0,
            "max_size": max(sizes) if sizes else 0,
            "named": sum(1 for e in self.entries if e.name),
        }


def build_archive(path: str, assets, version: int = 0x02010101,
                  alignment: int = ALIGNMENT) -> dict:
    """Create a Cement RCF archive.

    ``assets`` is a sequence of ``(name, payload, compressed, hash)`` tuples
    (``hash`` optional).  Layout matches the retail files: index at 0x3C,
    name table on the next 0x800 boundary, payloads from the next 0x800
    boundary, each payload 0x800-aligned.  The name table order equals the
    payload order, exactly like the shipping game.
    """
    import zlib
    from .rz import pack_rz

    items = []
    for a in assets:
        if len(a) == 3:
            name, payload, compressed = a
            h = zlib.crc32(name.encode("ascii")) & 0xFFFFFFFF
        else:
            name, payload, compressed, h = a
        items.append({
            "name": name,
            "payload": payload,
            "stored": pack_rz(payload) if compressed else payload,
            "hash": h & 0xFFFFFFFF,
        })
    n = len(items)
    if n == 0:
        raise RcfError("cannot build an empty archive")
    if len({i["hash"] for i in items}) != n:
        raise RcfError("duplicate hashes in build_archive input")

    idx_off = HEADER_SIZE
    tab_off = align_up(idx_off + 12 * n, alignment)

    table = bytearray()
    table += struct.pack("<II", 0x800, 0)
    for it in items:
        nb = it["name"].encode("ascii") + b"\x00"
        table += struct.pack("<IIII", 0x49, 0x800, 0, len(nb))
        table += nb
        table += b"\x00\x00\x00"
    tab_size = len(table)
    data_start = align_up(tab_off + tab_size, alignment)

    off = data_start
    for it in items:
        it["offset"] = off
        off = align_up(off + len(it["stored"]), alignment)
    total = off

    header = bytearray(HEADER_SIZE)
    header[0:24] = MAGIC
    struct.pack_into(">7I", header, 0x20, version, idx_off, 12 * n,
                     tab_off, tab_size, 0, n)

    with open(path, "wb") as f:
        f.write(header)
        # index (sorted by hash) immediately follows the 0x3C byte header
        for it in sorted(items, key=lambda x: x["hash"]):
            f.write(struct.pack(">III", it["hash"], it["offset"], len(it["stored"])))
        f.write(b"\x00" * (tab_off - (HEADER_SIZE + 12 * n)))
        f.seek(tab_off)
        f.write(table)
        for it in items:
            f.seek(it["offset"])
            f.write(it["stored"])
        f.truncate(total)
    return {
        "path": path,
        "entries": n,
        "size": total,
        "table_offset": tab_off,
        "data_start": data_start,
    }


def _parse_name_table(table: bytes, count: int) -> list[str]:
    """Parse the RCF name table. Returns [] when the layout is unexpected."""
    if len(table) < 8:
        return []
    names: list[str] = []
    o = 8
    n = len(table)
    while len(names) < count:
        if o + 16 > n:
            return []
        _tag, _f1, _f2, nlen = struct.unpack_from("<4I", table, o)
        if nlen < 2 or nlen > 512 or o + 16 + nlen > n:
            return []
        raw = table[o + 16:o + 16 + nlen]
        if not raw.endswith(b"\x00"):
            return []
        body = raw[:-1]
        if any(b < 9 or (13 < b < 32) or b > 126 for b in body):
            return []
        try:
            names.append(body.decode("ascii"))
        except UnicodeDecodeError:
            return []
        o += 16 + nlen + 3
    return names
