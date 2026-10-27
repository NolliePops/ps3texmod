"""Assert that the C homebrew constants match the Python plan/journal layout.

Run from the repository root:
    python artifacts/check_c_layout.py

This does not compile the C code (no toolchain is available).  It checks the
byte offsets and record sizes encoded in homebrew/include/*.h against files
produced by ps3texmod/plan.py, including a real sample plan and a generated
journal.
"""

from __future__ import annotations

import os
import struct
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ps3texmod.catalog import scan_archive  # noqa: E402
from ps3texmod.plan import (JOURNAL_HEADER, JOURNAL_RECORD, PLAN_HEADER,  # noqa: E402
                            PLAN_RECORD, apply_plan, build_plan)
from ps3texmod.rcf import RcfArchive  # noqa: E402
from tests.helpers import make_dds, make_game  # noqa: E402

# Constants from homebrew/include/common.h.
C_PLAN_HEADER = 64
C_PLAN_RECORD = 48
C_JOURNAL_HEADER = 36
C_JOURNAL_RECORD = 28

PLAN_MAGIC = b"PS3TPAT1"
PLAN_TRAILER = b"PS3TPND1"
JOURNAL_MAGIC = b"PS3TJRN1"
JOURNAL_TRAILER = b"PS3TJRE1"


def check_plan(path: str) -> None:
    data = open(path, "rb").read()
    assert (PLAN_HEADER, PLAN_RECORD) == (C_PLAN_HEADER, C_PLAN_RECORD)
    assert data[:8] == PLAN_MAGIC
    assert data[-16:-8] == PLAN_TRAILER
    version, header_bytes, alen, plen, count, blob = struct.unpack_from(
        "<IIIIII", data, 8)
    assert (version, header_bytes) == (1, C_PLAN_HEADER)
    index_offset, index_count = struct.unpack_from("<II", data, 0x20)
    archive_size, = struct.unpack_from("<Q", data, 0x28)
    flags, string_bytes = struct.unpack_from("<II", data, 0x30)
    assert flags == 1 and index_count > 0 and index_offset >= 0
    assert archive_size > 0
    names = data[PLAN_HEADER:PLAN_HEADER + string_bytes]
    assert names[:alen].decode() and plen > 0
    blob_start = PLAN_HEADER + string_bytes + PLAN_RECORD * count
    trailer = len(data) - 16
    assert blob_start + blob == trailer, (blob_start + blob, trailer)
    for i in range(count):
        rec = PLAN_HEADER + string_bytes + i * PLAN_RECORD
        h, index, offset, old, new, slot, boff, ocrc = struct.unpack_from(
            "<IIIIIIII", data, rec)
        ncrc, loff, llen = struct.unpack_from("<III", data, rec + 0x20)
        assert old > 0 and 0 < new <= slot
        assert PLAN_HEADER <= loff and loff + llen <= PLAN_HEADER + string_bytes
        assert blob_start <= boff and boff + new <= blob_start + blob
        assert index < index_count
    print(f"plan OK: {count} record(s), blob_start={blob_start}, "
          f"trailer={trailer}, archive={names[:alen].decode()}")


def check_journal(tmp: str) -> None:
    game = make_game(tmp)
    ar = RcfArchive(os.path.join(game, "PS3_GAME", "USRDIR", "art.rcf"))
    rec = next(r for r in scan_archive(ar) if r["container"] == "dds")
    blob = make_dds(rec["width"], rec["height"], rec["format"].encode(),
                    mips=rec["mipmaps"],
                    fill=b"\x12\x34\x56\x78" * (rec["size"] // 4 + 1))
    plan = build_plan(ar, [(int(rec["entry_hash"], 16), blob, "label")],
                      os.path.join(tmp, "t.ps3patch"), plan_name="unit",
                      log=lambda *a: None)
    res = apply_plan(plan, ar.path, journal_dir=os.path.join(tmp, "backups"),
                     log=lambda *a: None)
    jd = open(res["journal"], "rb").read()
    assert (JOURNAL_HEADER, JOURNAL_RECORD) == (C_JOURNAL_HEADER, C_JOURNAL_RECORD)
    assert jd[:8] == JOURNAL_MAGIC and jd[-16:-8] == JOURNAL_TRAILER
    jver, index_offset, index_count, alen = struct.unpack_from("<IIII", jd, 8)
    llen, = struct.unpack_from("<I", jd, 24)
    archive_size, = struct.unpack_from("<Q", jd, 28)
    assert jver == 1 and index_count > 0 and archive_size > 0
    pos = JOURNAL_HEADER + alen + llen
    trailer = len(jd) - 16
    assert jd[JOURNAL_HEADER:JOURNAL_HEADER + alen].decode() == ar.name
    records = 0
    while pos < trailer:
        assert jd[pos:pos + 4] == b"JREC"
        index, offset, slot, old, ocrc, ncrc = struct.unpack_from(
            "<IIIIII", jd, pos + 4)
        assert index < index_count and old > 0 and old <= slot
        pos += JOURNAL_RECORD + slot
        records += 1
    assert pos == trailer, (pos, trailer)
    print(f"journal OK: header={JOURNAL_HEADER}, record={JOURNAL_RECORD}, "
          f"records={records}, end={trailer}")


def main() -> int:
    check_plan(os.path.join(ROOT, "samples",
                            "prototype-billboard-tint.ps3patch"))
    tmp = tempfile.mkdtemp(prefix="ps3texmod-c-layout-")
    try:
        check_journal(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("C layout check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
