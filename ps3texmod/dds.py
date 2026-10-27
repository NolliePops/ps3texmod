"""Minimal DDS reader/writer helpers (pure stdlib).

Supports the formats found in the retail Prototype PS3 assets:
DXT1/BC1, DXT3/BC2, DXT5/BC3, plus uncompressed 32/24/16-bit layouts.
Provides mip-chain size computation, RGBA decoding and PNG encoding.
"""

from __future__ import annotations

import struct
import zlib

DDS_MAGIC = b"DDS "

DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PITCH = 0x8
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000

DDPF_ALPHAPIXELS = 0x1
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_LUMINANCE = 0x20000

FOURCC_DXT1 = b"DXT1"
FOURCC_DXT3 = b"DXT3"
FOURCC_DXT5 = b"DXT5"
FOURCC_ATI1 = b"ATI1"
FOURCC_BC4U = b"BC4U"
FOURCC_ATI2 = b"ATI2"
FOURCC_BC5U = b"BC5U"
FOURCC_DX10 = b"DX10"

_COMPRESSED = {
    FOURCC_DXT1: 8,
    FOURCC_ATI1: 8,
    FOURCC_BC4U: 8,
    FOURCC_DXT3: 16,
    FOURCC_DXT5: 16,
    FOURCC_ATI2: 16,
    FOURCC_BC5U: 16,
}


class DdsError(ValueError):
    pass


class DdsInfo:
    __slots__ = (
        "width", "height", "depth", "mipmaps", "flags", "pitch",
        "fourcc", "pf_flags", "bit_count", "r_mask", "g_mask", "b_mask", "a_mask",
        "header_size", "data_offset", "data_size", "raw_header",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    # -- presentation helpers -------------------------------------------------
    @property
    def format_name(self) -> str:
        if self.fourcc:
            return self.fourcc.decode("latin1")
        if self.bit_count == 32:
            return "A8R8G8B8" if self.a_mask else "X8R8G8B8"
        if self.bit_count == 24:
            return "R8G8B8"
        if self.bit_count == 16:
            return "16-bit"
        if self.bit_count == 8:
            return "8-bit"
        return f"{self.bit_count}-bit"

    @property
    def compressed(self) -> bool:
        return self.fourcc in _COMPRESSED

    @property
    def block_bytes(self) -> int:
        return _COMPRESSED.get(self.fourcc, 0)

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "depth": self.depth,
            "mipmaps": self.mipmaps,
            "format": self.format_name,
            "fourcc": self.fourcc.decode("latin1") if self.fourcc else None,
            "compressed": self.compressed,
            "data_offset": self.data_offset,
            "data_size": self.data_size,
            "total_size": self.data_offset + self.data_size,
        }

    def summary(self) -> str:
        return (f"{self.width}x{self.height} {self.format_name} "
                f"mips={self.mipmaps} {self.data_size}B")


def _level_size(width: int, height: int, info: DdsInfo) -> int:
    if info.fourcc == FOURCC_DX10:
        raise DdsError("DX10 extended headers are not supported")
    if info.compressed:
        bw = max(1, (width + 3) // 4)
        bh = max(1, (height + 3) // 4)
        return bw * bh * info.block_bytes
    if info.bit_count in (8, 16, 24, 32):
        return width * height * (info.bit_count // 8)
    raise DdsError(f"unsupported pixel format ({info.bit_count} bpp)")


def parse_dds(raw: bytes, offset: int = 0, available: int | None = None) -> DdsInfo:
    """Parse a DDS header at ``offset`` and compute the exact payload length."""
    if available is None:
        available = len(raw) - offset
    if available < 128:
        raise DdsError("truncated DDS header")
    hdr = raw[offset:offset + 128]
    if hdr[:4] != DDS_MAGIC:
        raise DdsError("not a DDS file")
    (size, flags, height, width, pitch, depth, mips) = struct.unpack_from("<7I", hdr, 4)
    if size != 124:
        raise DdsError(f"unexpected DDS header size {size}")
    pf_size, pf_flags, fourcc_raw, bit_count = struct.unpack_from("<4I", hdr, 76)
    r_mask, g_mask, b_mask, a_mask = struct.unpack_from("<4I", hdr, 92)
    if pf_size != 32:
        raise DdsError(f"unexpected pixel format size {pf_size}")
    if width == 0 or height == 0:
        raise DdsError("zero dimensions")
    fourcc = fourcc_raw.to_bytes(4, "little")
    if fourcc in (b"\x00\x00\x00\x00", b"\x01\x00\x00\x00"):
        fourcc = b""

    info = DdsInfo(
        width=width, height=height, depth=depth or 1,
        mipmaps=max(1, mips if (flags & DDSD_MIPMAPCOUNT) else 1),
        flags=flags, pitch=pitch, fourcc=fourcc, pf_flags=pf_flags,
        bit_count=bit_count, r_mask=r_mask, g_mask=g_mask, b_mask=b_mask,
        a_mask=a_mask, header_size=128, data_offset=offset + 128, data_size=0,
        raw_header=hdr,
    )
    if fourcc == FOURCC_DX10:
        info.header_size = 148
        info.data_offset = offset + 148
        if available < 148:
            raise DdsError("truncated DX10 header")
        info.fourcc = b""

    total = 0
    w, h = width, height
    for _ in range(info.mipmaps):
        total += _level_size(w, h, info)
        w = max(1, w // 2)
        h = max(1, h // 2)
    info.data_size = total
    if total > available - info.header_size:
        raise DdsError(
            f"DDS payload overruns buffer (need {total}, have {available - info.header_size})"
        )
    return info


def parse_dds_file(path: str) -> tuple[bytes, DdsInfo]:
    with open(path, "rb") as f:
        raw = f.read()
    info = parse_dds(raw)
    info.data_size = min(info.data_size, len(raw) - info.data_offset)
    return raw, info


def iter_dds(raw: bytes, start: int = 0, end: int | None = None):
    """Yield (offset, DdsInfo) for every valid embedded DDS in raw[start:end]."""
    if end is None:
        end = len(raw)
    pos = start
    while True:
        i = raw.find(DDS_MAGIC, pos, end)
        if i < 0:
            return
        pos = i + 1
        if i + 128 > end:
            continue
        if raw[i + 4:i + 8] != b"\x7c\x00\x00\x00":
            continue
        try:
            info = parse_dds(raw, i, end - i)
        except DdsError:
            continue
        # sanity: dimensions must be powers of two-ish and not absurd
        if not (1 <= info.width <= 8192 and 1 <= info.height <= 8192):
            continue
        if info.data_size <= 0:
            continue
        yield i, info


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _rgb565(c: int) -> tuple[int, int, int]:
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _decode_dxt(block: bytes, fmt: bytes, out: bytearray, w: int, h: int, stride: int, x0: int, y0: int):
    """Decode one 4x4 block into the RGBA output buffer."""
    if fmt in (FOURCC_DXT3,):
        alpha_bits = int.from_bytes(block[0:8], "little")
        color = block[8:16]
        alpha = [((alpha_bits >> (4 * i)) & 0xF) * 17 for i in range(16)]
    elif fmt in (FOURCC_DXT5,):
        a0, a1 = block[0], block[1]
        bits = int.from_bytes(block[2:8], "little")
        alphas = [a0, a1]
        if a0 > a1:
            for i in range(2, 8):
                alphas.append(((8 - i) * a0 + (i - 1) * a1) // 7)
        else:
            for i in range(2, 6):
                alphas.append(((6 - i) * a0 + (i - 1) * a1) // 5)
            alphas += [0, 255]
        alpha = [alphas[(bits >> (3 * i)) & 0x7] for i in range(16)]
        color = block[8:16]
    elif fmt in (FOURCC_ATI2, FOURCC_BC5U):
        return  # normal maps are not previewed
    else:  # DXT1 / BC1 / ATI1
        color = block[0:8]
        alpha = None

    c0, c1 = struct.unpack_from("<HH", color, 0)
    idx = struct.unpack_from("<I", color, 4)[0]
    p0 = _rgb565(c0)
    p1 = _rgb565(c1)
    if c0 > c1 or fmt != FOURCC_DXT1:
        palette = [
            (*p0, 255),
            (*p1, 255),
            ((2 * p0[0] + p1[0]) // 3, (2 * p0[1] + p1[1]) // 3, (2 * p0[2] + p1[2]) // 3, 255),
            ((p0[0] + 2 * p1[0]) // 3, (p0[1] + 2 * p1[1]) // 3, (p0[2] + 2 * p1[2]) // 3, 255),
        ]
    else:
        palette = [
            (*p0, 255),
            (*p1, 255),
            ((p0[0] + p1[0]) // 2, (p0[1] + p1[1]) // 2, (p0[2] + p1[2]) // 2, 255),
            (0, 0, 0, 0),
        ]

    for i in range(16):
        px = i & 3
        py = i >> 2
        x = x0 + px
        y = y0 + py
        if x >= w or y >= h:
            continue
        a = 255 if alpha is None else alpha[i]
        r, g, b, _ = palette[(idx >> (2 * i)) & 0x3]
        o = y * stride + x * 4
        out[o] = r
        out[o + 1] = g
        out[o + 2] = b
        out[o + 3] = a


def _unpack_bits(pixel: int, mask: int) -> int:
    if not mask:
        return 0
    shift = (mask & -mask).bit_length() - 1
    bits = bin(mask >> shift).count("1")
    val = (pixel & mask) >> shift
    if bits == 8:
        return val
    if bits > 8:
        return val >> (bits - 8)
    return (val * 255) // ((1 << bits) - 1)


def decode_level0_rgba(raw: bytes, info: DdsInfo) -> tuple[bytes, int, int]:
    """Decode mip 0 to RGBA8. Returns (rgba, width, height)."""
    w, h = info.width, info.height
    src = raw[info.data_offset:info.data_offset + info.data_size]
    out = bytearray(w * h * 4)

    if info.compressed:
        bs = info.block_bytes
        stride = w * 4
        bw = max(1, (w + 3) // 4)
        bh = max(1, (h + 3) // 4)
        pos = 0
        for by in range(bh):
            for bx in range(bw):
                block = src[pos:pos + bs]
                if len(block) < bs:
                    break
                _decode_dxt(block, info.fourcc, out, w, h, stride, bx * 4, by * 4)
                pos += bs
        return bytes(out), w, h

    bpp = max(1, info.bit_count // 8)
    stride_in = info.pitch if (info.flags & DDSD_PITCH and info.pitch >= w * bpp) else w * bpp
    for y in range(h):
        row = src[y * stride_in:(y + 1) * stride_in]
        o = y * w * 4
        for x in range(w):
            if bpp == 4:
                px = int.from_bytes(row[x * 4:x * 4 + 4], "little")
            elif bpp == 3:
                b, g, r = row[x * 3:x * 3 + 3]
                out[o + x * 4:o + x * 4 + 4] = bytes((r, g, b, 255))
                continue
            elif bpp == 2:
                px = int.from_bytes(row[x * 2:x * 2 + 2], "little")
            else:
                px = row[x]
            r = _unpack_bits(px, info.r_mask) if info.r_mask else 255
            g = _unpack_bits(px, info.g_mask) if info.g_mask else 255
            b = _unpack_bits(px, info.b_mask) if info.b_mask else 255
            a = _unpack_bits(px, info.a_mask) if info.a_mask else 255
            out[o + x * 4:o + x * 4 + 4] = bytes((r, g, b, a))
    return bytes(out), w, h


def build_png(rgba: bytes, width: int, height: int, level: int = 9) -> bytes:
    """Encode RGBA8 to PNG using only zlib/struct."""
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA buffer size mismatch")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + chunk(b"IEND", b""))


def dds_to_png(raw: bytes, info: DdsInfo | None = None) -> bytes:
    if info is None:
        info = parse_dds(raw)
    rgba, w, h = decode_level0_rgba(raw, info)
    return build_png(rgba, w, h)


def describe(raw: bytes, info: DdsInfo | None = None) -> dict:
    if info is None:
        info = parse_dds(raw)
    d = info.to_dict()
    d["sha256"] = __import__("hashlib").sha256(raw[:info.data_offset + info.data_size]).hexdigest()
    return d
