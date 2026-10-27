"""Shared fixtures for the ps3texmod test suite.

Everything here is synthetic: no retail game data is required.  The layout
helpers mirror the real formats documented in ``ps3texmod.rcf`` and
``ps3texmod.dds``.
"""

from __future__ import annotations

import os
import struct
import zlib

BLOCK_BYTES = {
    b"DXT1": 8, b"ATI1": 8, b"BC4U": 8,
    b"DXT3": 16, b"DXT5": 16, b"ATI2": 16, b"BC5U": 16,
}


def make_dds(width: int, height: int, fourcc: bytes = b"DXT1", mips: int = 1,
             fill: bytes | None = None) -> bytes:
    """Build a valid DDS file of the requested size.

    ``fill`` overrides the pixel bytes (must be at least as long as needed).
    """
    if fourcc in BLOCK_BYTES:
        block = BLOCK_BYTES[fourcc]
        if len(fill or b"") and len(fill) < 128:
            raise ValueError("fill too short")
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
        struct.pack_into("<4I", hdr, 76, 32, 0x4,
                         int.from_bytes(fourcc, "little"), 0)
        struct.pack_into("<I", hdr, 108, 0x1000)
        if fill is not None:
            data = fill[:total]
            if len(data) < total:
                data = data + bytes(total - len(data))
        else:
            # A simple colour ramp keeps textures compressible and valid.
            block_data = bytearray()
            if fourcc == b"DXT1":
                block_data += struct.pack("<HHI", 0xF800, 0x001F, 0)
            elif fourcc == b"DXT3":
                block_data += bytes(8) + struct.pack("<HHI", 0xF800, 0x001F, 0)
            elif fourcc == b"DXT5":
                block_data += bytes([255, 0]) + bytes(6) + struct.pack(
                    "<HHI", 0xF800, 0x001F, 0)
            else:
                block_data += bytes(block)
            data = bytes(block_data) * (total // block)
        return bytes(hdr) + data

    if fourcc not in (b"", b"\x00\x00\x00\x00"):
        raise ValueError(f"unsupported test format {fourcc!r}")
    flags = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000
    if mips > 1:
        flags |= 0x20000
    hdr = bytearray(128)
    hdr[0:4] = b"DDS "
    struct.pack_into("<7I", hdr, 4, 124, flags, height, width, width * 4, 0, mips)
    struct.pack_into("<4I", hdr, 76, 32, 0x41, 0, 32)
    struct.pack_into("<4I", hdr, 92, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    struct.pack_into("<I", hdr, 108, 0x1000)
    total = 0
    w, h = width, height
    for _ in range(mips):
        total += w * h * 4
        w = max(1, w // 2)
        h = max(1, h // 2)
    if fill is not None:
        data = fill[:total]
        if len(data) < total:
            data = data + bytes(total - len(data))
    else:
        data = bytes([0x20, 0x80, 0xE0, 0xFF]) * (total // 4)
    return bytes(hdr) + data


def make_p3d(embedded: list[tuple[str, bytes]], trailer: bytes = b"") -> tuple[bytes, list[int]]:
    """Build a small P3D-like blob that embeds DDS textures.

    Returns ``(payload, offsets)``.  Each embedded texture is preceded by a
    length-prefixed name, matching the retail layout closely enough for the
    scanner (which searches for the ``DDS `` magic and reads the ASCII name
    immediately before it).
    """
    body = bytearray()
    body += b"\xffD3P" + struct.pack(">I", 12) + b"\x00\x00\x00\x00"
    body += struct.pack(">I", 0x0001100B) + struct.pack(">I", 85) + struct.pack(">I", 0)
    offsets = []
    for name, dds in embedded:
        nb = name.encode("ascii") + b"\x00"
        body += struct.pack(">I", len(nb)) + nb
        body += b"\x00" * 8
        offsets.append(len(body))
        body += dds
    body += trailer
    struct.pack_into(">I", body, 8, len(body))
    return bytes(body), offsets


def make_game(tmpdir: str, archive_name: str = "art.rcf", assets=None) -> str:
    """Create ``<tmpdir>/game/PS3_GAME/USRDIR/<archive_name>``."""
    from ps3texmod.rcf import build_archive
    usrdir = os.path.join(tmpdir, "game", "PS3_GAME", "USRDIR")
    os.makedirs(usrdir, exist_ok=True)
    path = os.path.join(usrdir, archive_name)
    build_archive(path, assets or default_assets())
    return os.path.dirname(os.path.dirname(usrdir))


def default_assets():
    dds_a = make_dds(64, 64, b"DXT1")
    dds_b = make_dds(64, 64, b"DXT5", mips=2)
    p3d, _ = make_p3d([("layer_a.dds", dds_b)])
    return [
        (r"\art\test\standalone.dds", dds_a, False),
        (r"\art\test\embedded.p3d.rz", p3d, True),
        (r"\art\test\notes.txt", b"hello cement\n" * 40, True),
    ]


def file_sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stored_hashes(archive) -> dict[int, str]:
    """Map entry hash -> sha256 of the stored bytes (layout independent)."""
    import hashlib
    out = {}
    with open(archive.path, "rb") as f:
        for e in archive.entries:
            f.seek(e.offset)
            h = hashlib.sha256(f.read(e.size)).hexdigest()
            out[e.hash] = h
    return out


def png_size(blob: bytes) -> tuple[int, int]:
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    w, h = struct.unpack(">II", blob[16:24])
    return w, h


_ = zlib  # keep the import for callers that exec this module's globals
