"""RZ container used inside Prototype RCF archives.

Layout (verified against retail art.rcf / cached.rcf data):

    offset  size  meaning
    0x00    2     ASCII "RZ"
    0x02    6     zero (reserved / flags, preserved verbatim)
    0x08    8     uncompressed size, little-endian u64
    0x10    ..    zlib stream (78 DA = level 9)

A few assets in art.rcf are stored as raw little-endian P3D ("P3D\\xff")
without an RZ wrapper; callers must handle those separately.
"""

from __future__ import annotations

import struct
import zlib

RZ_MAGIC = b"RZ"
RZ_HEADER = 16


class RzError(ValueError):
    pass


def is_rz(blob: bytes) -> bool:
    return len(blob) >= RZ_HEADER and blob[:2] == RZ_MAGIC


def unpack_rz(blob: bytes) -> bytes:
    """Return the decompressed payload of an RZ blob."""
    if not is_rz(blob):
        raise RzError("not an RZ container")
    usize = struct.unpack_from("<Q", blob, 8)[0]
    try:
        data = zlib.decompress(blob[RZ_HEADER:])
    except zlib.error as exc:
        raise RzError(f"zlib error: {exc}") from exc
    if len(data) != usize:
        raise RzError(f"size mismatch: header says {usize}, got {len(data)}")
    return data


def pack_rz(data: bytes, level: int = 9, header: bytes | None = None) -> bytes:
    """Wrap data in an RZ container.

    ``header`` may be the first 8 bytes of an existing container so that
    reserved bytes are preserved.
    """
    if header is None:
        head = b"RZ" + b"\x00" * 6
    else:
        if len(header) != 8 or header[:2] != RZ_MAGIC:
            raise RzError("bad RZ header template")
        head = header
    return head + struct.pack("<Q", len(data)) + zlib.compress(data, level)


def rz_info(blob: bytes) -> dict:
    if not is_rz(blob):
        raise RzError("not an RZ container")
    usize = struct.unpack_from("<Q", blob, 8)[0]
    return {
        "stored_size": len(blob),
        "uncompressed_size": usize,
        "header": blob[:8].hex(),
        "ratio": (len(blob) / usize) if usize else 0.0,
    }
