"""Turn an ordinary image into a DDS that drops into a retail texture slot.

Console plans patch textures in place, so a replacement must be *exactly* as
long as the original DDS: same dimensions, same pixel format, same mip count.
This module enforces that instead of hoping: the caller passes the original
DDS as a template, and :func:`encode_for_template` copies its header, encodes
the image into the same block format, and verifies the final byte length.

Only uncompressed BMP input (24/32 bpp, BI_RGB) and DXT1 output are supported;
those cover Texmod exports and the PS3 diffuse textures used by this project.
"""

from __future__ import annotations

import os
import struct

from .dds import DdsError, parse_dds
from .util import sha256_bytes


class EncodeError(ValueError):
    """Raised when an image cannot be encoded into the requested slot."""


# ---------------------------------------------------------------------------
# BMP input
# ---------------------------------------------------------------------------

def read_bmp(raw: bytes) -> tuple[bytes, int, int]:
    """Decode a 24/32-bit uncompressed BMP into top-down RGBA bytes."""
    if len(raw) < 54 or raw[:2] != b"BM":
        raise EncodeError("not a BMP file")
    data_offset = struct.unpack_from("<I", raw, 10)[0]
    header_size = struct.unpack_from("<I", raw, 14)[0]
    if header_size < 40:
        raise EncodeError(f"unsupported BMP header size {header_size}")
    width, height, planes, bpp = struct.unpack_from("<iiHH", raw, 18)
    compression = struct.unpack_from("<I", raw, 30)[0]
    if planes != 1:
        raise EncodeError("BMP has an unexpected plane count")
    if compression != 0:
        raise EncodeError(
            f"compressed BMP (mode {compression}) is not supported; "
            "save the image as uncompressed 24-bit or 32-bit BMP"
        )
    if bpp not in (24, 32):
        raise EncodeError(f"{bpp}-bit BMP is not supported (need 24 or 32)")
    if width <= 0 or height == 0:
        raise EncodeError("BMP has invalid dimensions")
    top_down = height < 0
    height = abs(height)
    stride = ((width * bpp + 31) // 32) * 4
    need = data_offset + stride * height
    if len(raw) < need:
        raise EncodeError(
            f"truncated BMP: need {need} bytes, have {len(raw)}"
        )
    step = bpp // 8
    rgba = bytearray(width * height * 4)
    for y in range(height):
        src_row = data_offset + (y if top_down else height - 1 - y) * stride
        dst_row = y * width * 4
        for x in range(width):
            s = src_row + x * step
            d = dst_row + x * 4
            rgba[d] = raw[s + 2]
            rgba[d + 1] = raw[s + 1]
            rgba[d + 2] = raw[s]
            rgba[d + 3] = raw[s + 3] if step == 4 else 255
    return bytes(rgba), width, height


def write_bmp(rgba: bytes, width: int, height: int) -> bytes:
    """Encode RGBA pixels as a 24-bit bottom-up BMP (test/debug helper)."""
    stride = (width * 3 + 3) & ~3
    pixels = bytearray(stride * height)
    for y in range(height):
        src = y * width * 4
        dst = (height - 1 - y) * stride
        for x in range(width):
            s = src + x * 4
            d = dst + x * 3
            pixels[d] = rgba[s + 2]
            pixels[d + 1] = rgba[s + 1]
            pixels[d + 2] = rgba[s]
    size = 54 + len(pixels)
    header = bytearray(54)
    header[0:2] = b"BM"
    struct.pack_into("<I", header, 2, size)
    struct.pack_into("<I", header, 10, 54)
    struct.pack_into("<I", header, 14, 40)
    struct.pack_into("<iiHH", header, 18, width, height, 1, 24)
    struct.pack_into("<I", header, 34, len(pixels))
    struct.pack_into("<ii", header, 38, 2835, 2835)
    return bytes(header) + bytes(pixels)


# ---------------------------------------------------------------------------
# DXT1 encoder
# ---------------------------------------------------------------------------

def _to565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _from565(value: int) -> tuple[int, int, int]:
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _palette(c0: int, c1: int) -> list[tuple[int, int, int]]:
    p0 = _from565(c0)
    p1 = _from565(c1)
    if c0 > c1:
        p2 = tuple((2 * a + b) // 3 for a, b in zip(p0, p1))
        p3 = tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))
    else:
        p2 = tuple((a + b) // 2 for a, b in zip(p0, p1))
        p3 = (0, 0, 0)
    return [p0, p1, p2, p3]


def _encode_block(pixels: list[tuple[int, int, int]]) -> bytes:
    first = pixels[0]
    if all(p == first for p in pixels):
        c = _to565(*first)
        return struct.pack("<HHI", c, c, 0)

    # Endpoints: the farthest-apart pair of the 16 block colours.  Cheap and
    # noticeably better than min/max per channel on photographic atlases.
    best = -1
    c0 = c1 = 0
    for i in range(len(pixels)):
        ar, ag, ab = pixels[i]
        for j in range(i + 1, len(pixels)):
            br, bg, bb = pixels[j]
            d = (ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2
            if d > best:
                best = d
                c0 = _to565(ar, ag, ab)
                c1 = _to565(br, bg, bb)
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:
        return struct.pack("<HHI", c0, c1, 0)

    palette = _palette(c0, c1)
    bits = 0
    for index, (r, g, b) in enumerate(pixels):
        pick = 0
        closest = None
        for slot, (pr, pg, pb) in enumerate(palette):
            d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
            if closest is None or d < closest:
                closest = d
                pick = slot
                if d == 0:
                    break
        bits |= pick << (2 * index)
    return struct.pack("<HHI", c0, c1, bits)


def encode_dxt1(rgba: bytes, width: int, height: int) -> bytes:
    """Encode RGBA pixels as a DXT1 level (4x4 blocks, edge-clamped)."""
    if len(rgba) != width * height * 4:
        raise EncodeError("pixel buffer does not match the dimensions")
    out = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            pixels = []
            for dy in range(4):
                y = min(height - 1, by + dy)
                row = y * width * 4
                for dx in range(4):
                    x = min(width - 1, bx + dx)
                    p = row + x * 4
                    pixels.append((rgba[p], rgba[p + 1], rgba[p + 2]))
            out += _encode_block(pixels)
    return bytes(out)


def halve(rgba: bytes, width: int, height: int) -> tuple[bytes, int, int]:
    """Box-filter a level down by two (used to build the mip chain)."""
    nw = max(1, width // 2)
    nh = max(1, height // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0 = min(height - 1, y * 2) * width
        y1 = min(height - 1, y * 2 + 1) * width
        for x in range(nw):
            x0 = min(width - 1, x * 2)
            x1 = min(width - 1, x * 2 + 1)
            d = (y * nw + x) * 4
            for c in range(3):
                total = (rgba[(y0 + x0) * 4 + c] + rgba[(y0 + x1) * 4 + c]
                         + rgba[(y1 + x0) * 4 + c] + rgba[(y1 + x1) * 4 + c])
                out[d + c] = total // 4
            out[d + 3] = 255
    return bytes(out), nw, nh


# ---------------------------------------------------------------------------
# slot matching
# ---------------------------------------------------------------------------

def encode_levels(rgba: bytes, width: int, height: int, mips: int) -> bytes:
    """Encode ``mips`` DXT1 levels, halving the image between levels."""
    blob = bytearray()
    w, h = width, height
    data = rgba
    for level in range(mips):
        blob += encode_dxt1(data, w, h)
        if level + 1 < mips:
            data, w, h = halve(data, w, h)
    return bytes(blob)


def build_dds_like(template: bytes, rgba: bytes, width: int, height: int,
                   mips: int | None = None) -> bytes:
    """Return a DDS with ``template``'s header and freshly encoded pixels."""
    try:
        info = parse_dds(template)
    except DdsError as exc:
        raise EncodeError(f"template is not a usable DDS ({exc})")
    if not info.compressed:
        raise EncodeError(
            f"template format {info.format_name} is uncompressed; only "
            "block-compressed targets are supported"
        )
    if info.fourcc != b"DXT1":
        raise EncodeError(
            f"template format {info.format_name} is not supported; "
            "this encoder writes DXT1 only"
        )
    if (info.width, info.height) != (width, height):
        raise EncodeError(
            f"image is {width}x{height} but the target slot is "
            f"{info.width}x{info.height}; rescale the image first"
        )
    levels = info.mipmaps if mips is None else mips
    if levels < 1:
        raise EncodeError("mip count must be at least 1")

    header = bytearray(template[:128])
    flags = struct.unpack_from("<I", header, 8)[0]
    flags |= 0x1 | 0x2 | 0x4 | 0x1000          # caps | height | width | pixelsize
    if levels > 1:
        flags |= 0x20000                        # mipmap count
    else:
        flags &= ~0x20000
    struct.pack_into("<I", header, 8, flags)
    struct.pack_into("<ii", header, 12, height, width)
    level0 = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 8
    struct.pack_into("<I", header, 20, level0)
    struct.pack_into("<I", header, 28, levels)

    blob = bytes(header) + encode_levels(rgba, width, height, levels)
    try:
        produced = parse_dds(blob)
    except DdsError as exc:
        raise EncodeError(f"internal: encoded DDS is invalid ({exc})")
    expected = info.data_offset + info.data_size
    if len(blob) != expected:
        raise EncodeError(
            f"encoded texture is {len(blob)} bytes but the slot takes "
            f"{expected}; the target's mip chain could not be reproduced"
        )
    return blob


def encode_for_template(image_path: str, template_path: str,
                        out_path: str) -> dict:
    """Read ``image_path``, match ``template_path`` exactly, write ``out_path``."""
    with open(image_path, "rb") as f:
        image_raw = f.read()
    rgba, width, height = read_bmp(image_raw)
    with open(template_path, "rb") as f:
        template = f.read()
    blob = build_dds_like(template, rgba, width, height)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(blob)
    info = parse_dds(blob)
    return {
        "image": image_path,
        "template": template_path,
        "out": out_path,
        "width": info.width,
        "height": info.height,
        "format": info.format_name,
        "mipmaps": info.mipmaps,
        "size": len(blob),
        "sha256": sha256_bytes(blob),
    }
