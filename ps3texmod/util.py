"""Shared helpers: hashing, formatting, safe file IO, logging."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_region(path: str, offset: int, size: int, chunk: int = 1 << 20) -> str:
    """Hash a byte range of a file without loading the whole file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(offset)
        left = size
        while left > 0:
            b = f.read(min(chunk, left))
            if not b:
                raise IOError(f"unexpected EOF hashing {path} at {offset}")
            h.update(b)
            left -= len(b)
    return h.hexdigest()


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0:
        raise ValueError("alignment must be positive")
    return (value + alignment - 1) // alignment * alignment


def human_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TiB"


def hexdump(data: bytes, offset: int = 0, limit: int | None = None) -> str:
    out = []
    end = len(data) if limit is None else min(len(data), limit)
    for i in range(0, end, 16):
        chunk = data[i:i + 16]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{offset + i:08X}  {hexs:<47}  {asc}")
    return "\n".join(out)


def read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Write data to path atomically (same directory temp file + replace)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ps3texmod-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, obj: dict) -> None:
    atomic_write_bytes(path, (json.dumps(obj, indent=2, sort_keys=False) + "\n").encode("utf-8"))


class Log:
    """Tiny logger with optional file output (mirrors Texmod's log file idea)."""

    def __init__(self, stream=None, path: str | None = None):
        self.stream = stream if stream is not None else sys.stdout
        self.path = path
        if path:
            d = os.path.dirname(os.path.abspath(path))
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("ps3texmod log\n")

    def __call__(self, *parts: object) -> None:
        msg = " ".join(str(p) for p in parts)
        print(msg, file=self.stream)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")


def norm_name(name: str) -> str:
    """Normalise an RCF asset path for comparison."""
    n = name.strip().replace("/", "\\")
    while n.startswith("\\"):
        n = n[1:]
    return n.lower()


def parse_hash(text: str) -> int:
    t = text.strip()
    if t.lower().startswith("0x"):
        t = t[2:]
    t = t.replace(":", "")
    return int(t, 16)


def game_usrdir(game_dir: str) -> str:
    """Return the directory that holds the ``.rcf`` files.

    Accepts the three layouts a Prototype install can have:

    * disc / JB folder root: ``<game>/PS3_GAME/USRDIR``
    * package install root:  ``<game>/USRDIR`` (e.g. ``NPUB30389``)
    * the ``USRDIR`` itself

    Falls back to ``game_dir`` so a bare archive folder still works.
    """
    for cand in (os.path.join(game_dir, "PS3_GAME", "USRDIR"),
                 os.path.join(game_dir, "USRDIR")):
        if os.path.isdir(cand):
            return cand
    return game_dir


def resolve_archive(game_dir: str, name: str) -> str | None:
    """Path of ``name`` inside the game's USRDIR, or None if missing."""
    path = os.path.join(game_usrdir(game_dir), name)
    return path if os.path.isfile(path) else None


def fmt_hash(h: int) -> str:
    return f"{h:08X}"
