"""Generate homebrew/pkg/ICON0.PNG (320x176 XMB icon).

The icon is drawn from the same 3x5 glyph table the console UI uses, so it
needs no external image library and no checked-in binary art.

Usage: python tools/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from gen_font import GLYPHS  # noqa: E402

OUT = os.path.join(HERE, "..", "homebrew", "pkg", "ICON0.PNG")
W, H = 320, 176


class Canvas:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h
        self.px = [[(0, 0, 0, 255)] * w for _ in range(h)]

    def pixel(self, x: int, y: int, color) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = color

    def rect(self, x: int, y: int, w: int, h: int, color) -> None:
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                self.px[yy][xx] = color

    def blend_rect(self, x: int, y: int, w: int, h: int, color, alpha: float) -> None:
        ar, ag, ab, _ = color
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                r, g, b, a = self.px[yy][xx]
                self.px[yy][xx] = (
                    int(r * (1 - alpha) + ar * alpha),
                    int(g * (1 - alpha) + ag * alpha),
                    int(b * (1 - alpha) + ab * alpha),
                    a,
                )

    def glyph(self, ch: str, x: int, y: int, scale: int, color) -> None:
        rows = GLYPHS.get(ch)
        if rows is None:
            rows = GLYPHS["?"]
        for ry, row in enumerate(rows):
            for rx, bit in enumerate(row):
                if bit == "#":
                    self.rect(x + rx * scale, y + ry * scale, scale, scale, color)

    def text(self, text: str, x: int, y: int, scale: int, color) -> int:
        cx = x
        for ch in text:
            self.glyph(ch, cx, y, scale, color)
            cx += 4 * scale
        return cx - x - scale


def text_width(text: str, scale: int) -> int:
    return len(text) * 4 * scale - scale


def main() -> int:
    c = Canvas(W, H)

    # Background gradient with a faint engineering grid.
    for y in range(H):
        t = y / (H - 1)
        c.rect(0, y, W, 1, (int(14 + 10 * (1 - t)), int(22 + 16 * (1 - t)),
                            int(34 + 22 * (1 - t)), 255))
    for x in range(0, W, 16):
        c.blend_rect(x, 0, 1, H, (60, 90, 120, 255), 0.16)
    for y in range(0, H, 16):
        c.blend_rect(0, y, W, 1, (60, 90, 120, 255), 0.16)

    # Amber header / footer rules.
    c.rect(0, 0, W, 5, (240, 176, 64, 255))
    c.rect(0, H - 5, W, 5, (240, 176, 64, 255))
    c.rect(12, 18, W - 24, 2, (240, 176, 64, 255))

    # Big title, centred.
    title = "PS3TEXMOD"
    ts = 6
    tw = text_width(title, ts)
    c.text(title, (W - tw) // 2, 52, ts, (236, 242, 248, 255))

    subtitle = "TEXTURE INSTALLER"
    ss = 2
    sw = text_width(subtitle, ss)
    c.text(subtitle, (W - sw) // 2, 100, ss, (240, 176, 64, 255))

    tag = "PROTOTYPE  PS3"
    gs = 2
    gw = text_width(tag, gs)
    c.text(tag, (W - gw) // 2, 126, gs, (144, 164, 184, 255))

    # Small texture-tile motif on the left and right.
    for i, (bx, by) in enumerate(((18, 24), (W - 34, 24))):
        for yy in range(4):
            for xx in range(4):
                shade = 70 + (xx + yy) * 18 + i * 10
                c.rect(bx + xx * 4, by + yy * 4, 3, 3, (shade, shade + 20, shade + 40, 255))

    # Encode as RGBA PNG.
    raw = bytearray()
    for row in c.px:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(png)
    print(f"wrote {os.path.relpath(OUT, os.path.join(HERE, '..'))} "
          f"({W}x{H}, {len(png)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
