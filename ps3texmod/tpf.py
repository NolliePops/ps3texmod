"""Read Texmod ``.tpf`` packages.

A ``.tpf`` file is not a ZIP: every 32-bit little-endian word is XORed with
``0x3FA43FA4`` first, which turns the file into a ZipCrypto-encrypted ZIP
archive.  The ZIP password is a fixed 42-byte blob baked into TexMod and
OpenTexMod.  Inside the archive are ``texmod.def`` (``0xHASH|image`` lines)
and one image per replacement.

Only the container is implemented here; nothing about the game is assumed.
Texmod texture hashes are *not* RCF hashes, so a hash from a ``.tpf`` has to be
matched to a PS3 texture by dimension/content, not by looking the hash up.
"""

from __future__ import annotations

import io
import os
import struct
import zipfile

TPF_XOR = 0x3FA43FA4
TPF_KEY = struct.pack("<I", TPF_XOR)

# Hard-coded password used by TexMod/OpenTexMod for every .tpf archive.
TPF_PASSWORD = bytes((
    0x73, 0x2A, 0x63, 0x7D, 0x5F, 0x0A, 0xA6, 0xBD,
    0x7D, 0x65, 0x7E, 0x67, 0x61, 0x2A, 0x7F, 0x7F,
    0x74, 0x61, 0x67, 0x5B, 0x60, 0x70, 0x45, 0x74,
    0x5C, 0x22, 0x74, 0x5D, 0x6E, 0x6A, 0x73, 0x41,
    0x77, 0x6E, 0x46, 0x47, 0x77, 0x49, 0x0C, 0x4B,
    0x46, 0x6F,
))

DEF_NAME = "texmod.def"


class TpfError(ValueError):
    """Raised when a .tpf file cannot be decoded."""


# ---------------------------------------------------------------------------
# container
# ---------------------------------------------------------------------------

def decrypt(raw: bytes) -> bytes:
    """XOR the container back into a password-protected ZIP."""
    key = TPF_KEY
    out = bytearray(len(raw))
    for i, value in enumerate(raw):
        out[i] = value ^ key[i & 3]
    return bytes(out)


def encrypt(zip_bytes: bytes) -> bytes:
    """Inverse of :func:`decrypt` (used by the tests)."""
    return decrypt(zip_bytes)


def _unwrap(raw: bytes) -> bytes:
    """Accept a plain ZIP wrapper around a ``.tpf`` (common on download sites)."""
    if raw[:4] != b"PK\x03\x04":
        return raw
    try:
        outer = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return raw
    names = [n for n in outer.namelist() if n.lower().endswith(".tpf")]
    if len(names) != 1:
        if not names:
            raise TpfError("the ZIP contains no .tpf package")
        raise TpfError(f"the ZIP contains {len(names)} .tpf packages; "
                       "extract it first and pick one")
    return outer.read(names[0])


def _open_zip(raw: bytes) -> zipfile.ZipFile:
    raw = _unwrap(raw)
    if len(raw) < 4:
        raise TpfError("file is too short to be a .tpf package")
    try:
        zf = zipfile.ZipFile(io.BytesIO(decrypt(raw)))
    except zipfile.BadZipFile:
        raise TpfError(
            "not a Texmod .tpf package (XOR layer did not yield a ZIP archive)"
        )
    zf.setpassword(TPF_PASSWORD)
    return zf


def read_tpf(path: str) -> dict[str, bytes]:
    """Return every member of a ``.tpf`` as ``name -> bytes``."""
    with open(path, "rb") as f:
        raw = f.read()
    zf = _open_zip(raw)
    try:
        names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise TpfError(f"{path}: damaged archive ({exc})")
    out: dict[str, bytes] = {}
    for name in names:
        try:
            out[name] = zf.read(name)
        except RuntimeError as exc:
            raise TpfError(f"{path}: cannot decrypt {name!r} ({exc})")
    return out


def list_tpf(path: str) -> list[dict]:
    """Describe the members without decrypting the payloads."""
    with open(path, "rb") as f:
        raw = f.read()
    zf = _open_zip(raw)
    items = []
    for info in zf.infolist():
        items.append({
            "name": info.filename,
            "size": info.file_size,
            "compressed": info.compress_size,
            "crc32": f"{info.CRC:08X}",
            "encrypted": bool(info.flag_bits & 0x1),
        })
    return items


def parse_texdef(text: str) -> list[tuple[int, str]]:
    """Parse ``texmod.def`` content into ``(hash, image name)`` pairs."""
    entries: list[tuple[int, str]] = []
    # Texmod pads the definition file with NUL bytes; treat them as padding.
    for lineno, line in enumerate(text.replace("\x00", "").splitlines(), 1):
        s = line.strip().lstrip("\ufeff")
        if not s or s.startswith("#"):
            continue
        if "|" not in s:
            raise TpfError(f"{DEF_NAME}:{lineno}: expected <hash>|<image>")
        ident, name = s.split("|", 1)
        ident = ident.strip()
        name = name.strip()
        try:
            value = int(ident, 0)
        except ValueError:
            raise TpfError(f"{DEF_NAME}:{lineno}: bad texture id {ident!r}")
        if not name:
            raise TpfError(f"{DEF_NAME}:{lineno}: empty image name")
        entries.append((value, name))
    if not entries:
        raise TpfError(f"{DEF_NAME}: no definitions found")
    return entries


def package(path: str) -> dict:
    """Decode a package into ``{files, definitions}``."""
    files = read_tpf(path)
    if DEF_NAME in files:
        text = files[DEF_NAME].decode("latin-1")
        defs = parse_texdef(text)
    else:
        defs = []
    return {"files": files, "definitions": defs}


def extract(path: str, out_dir: str, log=print) -> dict:
    """Write every member to ``out_dir`` and return a summary."""
    pkg = package(path)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, blob in pkg["files"].items():
        safe = os.path.basename(name.replace("\\", "/")) or "member"
        target = os.path.join(out_dir, safe)
        with open(target, "wb") as f:
            f.write(blob)
        written.append({"name": name, "path": target, "size": len(blob)})
        log(f"  {safe}  {len(blob)} bytes")
    return {
        "tpf": path,
        "out_dir": out_dir,
        "files": written,
        "definitions": [
            {"hash": f"0x{h:08X}", "image": n} for h, n in pkg["definitions"]
        ],
    }
