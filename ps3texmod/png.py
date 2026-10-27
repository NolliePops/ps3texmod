"""Minimal PNG reader and box resampler (pure stdlib).

Texmod dumps its replacements as 24/32-bit PNG. The slot encoder needs plain
RGBA8 at the *real* texture size, so this module decodes the PNG and can
rescale it to a power-of-two target. Only the subset Texmod and ordinary image
editors emit is supported: 8-bit greyscale/RGB/palette/RGBA, non-interlaced.

Nothing here assumes a game format; it is ordinary image plumbing.
"""

from __future__ import annotations

import struct
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class PngError(ValueError):
    """Raised when a PNG cannot be decoded."""


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------

def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(data: bytes, width: int, height: int, bpp: int) -> bytes:
    stride = width * bpp
    out = bytearray(stride * height)
    pos = 0
    prev = bytearray(stride)
    for y in range(height):
        if pos >= len(data):
            raise PngError("truncated image data")
        ftype = data[pos]
        pos += 1
        line = bytearray(data[pos:pos + stride])
        if len(line) != stride:
            raise PngError("truncated scanline")
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        else:
            raise PngError(f"unsupported filter type {ftype}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)


def read_png(raw: bytes) -> tuple[bytes, int, int]:
    """Decode a PNG into ``(rgba, width, height)`` with 8 bits per channel."""
    if raw[:8] != PNG_MAGIC:
        raise PngError("not a PNG file")
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    palette = b""
    trns = b""
    idat = bytearray()
    while pos + 8 <= len(raw):
        length, tag = struct.unpack_from(">I4s", raw, pos)
        pos += 8
        body = raw[pos:pos + length]
        if len(body) != length:
            raise PngError(f"truncated {tag!r} chunk")
        pos += length + 4  # skip CRC
        if tag == b"IHDR":
            (width, height, bit_depth, color_type, comp, filt, interlace) = \
                struct.unpack(">IIBBBBB", body)
            if comp != 0 or filt != 0:
                raise PngError("unsupported PNG compression/filter method")
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if width is None:
        raise PngError("missing IHDR chunk")
    if interlace != 0:
        raise PngError("interlaced PNG is not supported")
    if bit_depth != 8:
        raise PngError(f"{bit_depth}-bit PNG is not supported (need 8-bit)")
    if color_type not in _CHANNELS:
        raise PngError(f"unsupported PNG colour type {color_type}")

    bpp = _CHANNELS[color_type]
    pixels = _unfilter(zlib.decompress(bytes(idat)), width, height, bpp)
    out = bytearray(width * height * 4)

    if color_type == 6:
        out[:] = pixels
    elif color_type == 2:
        for i in range(width * height):
            out[i * 4:i * 4 + 3] = pixels[i * 3:i * 3 + 3]
            out[i * 4 + 3] = 255
    elif color_type == 0:
        for i in range(width * height):
            v = pixels[i]
            out[i * 4:i * 4 + 4] = bytes((v, v, v, 255))
    elif color_type == 4:
        for i in range(width * height):
            v = pixels[i * 2]
            out[i * 4:i * 4 + 4] = bytes((v, v, v, pixels[i * 2 + 1]))
    else:  # palette
        if not palette:
            raise PngError("palette PNG without a PLTE chunk")
        for i in range(width * height):
            index = pixels[i]
            base = index * 3
            if base + 3 > len(palette):
                raise PngError("palette index out of range")
            out[i * 4:i * 4 + 3] = palette[base:base + 3]
            out[i * 4 + 3] = trns[index] if index < len(trns) else 255
    return bytes(out), width, height


def read_png_file(path: str) -> tuple[bytes, int, int]:
    with open(path, "rb") as f:
        return read_png(f.read())


# ---------------------------------------------------------------------------
# resample
# ---------------------------------------------------------------------------

def _weights(src: int, dst: int) -> list[list[tuple[int, float]]]:
    """Area-average weights mapping ``dst`` samples onto ``src`` samples."""
    scale = src / dst
    table: list[list[tuple[int, float]]] = []
    for i in range(dst):
        start = i * scale
        end = start + scale
        first = int(start)
        last = min(src - 1, int(end - 1e-9))
        taps: list[tuple[int, float]] = []
        total = 0.0
        for j in range(first, last + 1):
            lo = max(start, j)
            hi = min(end, j + 1)
            if hi <= lo:
                continue
            w = hi - lo
            taps.append((j, w))
            total += w
        if not taps:
            taps = [(min(first, src - 1), 1.0)]
            total = 1.0
        table.append([(j, w / total) for j, w in taps])
    return table


def resize(rgba: bytes, width: int, height: int,
           new_width: int, new_height: int) -> bytes:
    """Area-average resample an RGBA8 image (works for up- and downscaling)."""
    if (width, height) == (new_width, new_height):
        return rgba
    if len(rgba) != width * height * 4:
        raise PngError("pixel buffer does not match the dimensions")
    if new_width <= 0 or new_height <= 0:
        raise PngError("target dimensions must be positive")

    wx = _weights(width, new_width)
    wy = _weights(height, new_height)

    # horizontal pass
    mid = bytearray(new_width * height * 4)
    for y in range(height):
        src_row = y * width * 4
        dst_row = y * new_width * 4
        for x in range(new_width):
            r = g = b = a = 0.0
            for sx, w in wx[x]:
                p = src_row + sx * 4
                r += rgba[p] * w
                g += rgba[p + 1] * w
                b += rgba[p + 2] * w
                a += rgba[p + 3] * w
            q = dst_row + x * 4
            mid[q] = int(r + 0.5)
            mid[q + 1] = int(g + 0.5)
            mid[q + 2] = int(b + 0.5)
            mid[q + 3] = int(a + 0.5)

    # vertical pass
    out = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        dst_row = y * new_width * 4
        taps = wy[y]
        for x in range(new_width):
            r = g = b = a = 0.0
            for sy, w in taps:
                p = sy * new_width * 4 + x * 4
                r += mid[p] * w
                g += mid[p + 1] * w
                b += mid[p + 2] * w
                a += mid[p + 3] * w
            q = dst_row + x * 4
            out[q] = int(r + 0.5)
            out[q + 1] = int(g + 0.5)
            out[q + 2] = int(b + 0.5)
            out[q + 3] = int(a + 0.5)
    return bytes(out)


def describe(raw: bytes) -> dict:
    """Header summary without decoding the pixels."""
    if raw[:8] != PNG_MAGIC:
        raise PngError("not a PNG file")
    width, height, bit_depth, color_type, _c, _f, interlace = \
        struct.unpack(">IIBBBBB", raw[16:29])
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "interlaced": bool(interlace),
        "size": len(raw),
    }
