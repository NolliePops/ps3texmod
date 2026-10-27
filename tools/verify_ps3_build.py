"""Verify the built PS3 artifacts without a console.

Checks the raw PPU ELF, the three SELF files, the package header/items and the
SFO metadata, and cross-checks the packaged files against the build directory.
The package is decrypted with the published debug-package scheme (as used by
geohot's ``package_finalize``) so its item table can be read independently of
``pkg.exe``.

Usage:
    python tools/verify_ps3_build.py
    python tools/verify_ps3_build.py --homebrew homebrew --json out.json

Exit status is 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

PKG_MAGIC = 0x7F504B47
SELF_MAGIC = b"SCE\x00"
SFO_MAGIC = b"\x00PSF"

ITEM_RAW = 0x80000003
ITEM_DIR = 0x80000004
ITEM_NPDRM_SELF = 0x80000001

EXPECTED_SFO = {
    "TITLE_ID": "PS3TEXMD1",
    "TITLE": "ps3texmod - Prototype texture installer",
    "CATEGORY": "HG",
    "APP_VER": "01.00",
    "VERSION": "01.00",
    "PS3_SYSTEM_VER": "03.5500",
    "BOOTABLE": 1,
    "PARENTAL_LEVEL": 0,
}

CONTENT_ID = "UP0001-PS3TEXMD1_00-0000000000000000"


class Checker:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if detail and len(detail) > 200:
            detail = detail[:197] + "..."
        self.results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS  " if ok else "FAIL  ") + name + (("  (" + detail + ")") if detail else ""))
        return ok

    def equal(self, name: str, got, want) -> bool:
        ok = got == want
        return self.check(name, ok, "" if ok else "got %r, expected %r" % (got, want))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r["ok"])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def elf_info(c: Checker, path: str) -> dict:
    data = read(path)
    c.check("elf: magic", data[:4] == b"\x7fELF", "size %d" % len(data))
    c.equal("elf: class ELF64", data[4], 2)
    c.equal("elf: big endian", data[5], 2)
    c.equal("elf: OS/ABI 0x66 (Cell LV2)", data[7], 0x66)
    e_type, e_machine = struct.unpack(">HH", data[16:20])
    c.equal("elf: type EXEC", e_type, 2)
    c.equal("elf: machine PowerPC64", e_machine, 21)
    entry = struct.unpack(">Q", data[24:32])[0]
    c.equal("elf: entry 0x10000000", entry, 0x10000000)
    phoff = struct.unpack(">Q", data[32:40])[0]
    phentsize, phnum = struct.unpack(">HH", data[54:58])
    c.check("elf: program headers present", phnum > 0, "%d entries" % phnum)

    loads = 0
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type = struct.unpack(">I", data[off:off + 4])[0]
        if p_type != 1:  # PT_LOAD
            continue
        loads += 1
        p_offset = struct.unpack(">Q", data[off + 8:off + 16])[0]
        p_filesz = struct.unpack(">Q", data[off + 32:off + 40])[0]
        if p_offset + p_filesz > len(data):
            c.check("elf: LOAD segment %d inside file" % i, False,
                    "%#x+%#x > %#x" % (p_offset, p_filesz, len(data)))
    c.check("elf: has LOAD segments", loads > 0, "%d LOAD" % loads)
    return {"size": len(data), "sha256": sha256(data), "entry": entry, "loads": loads}


def self_info(c: Checker, label: str, path: str, elf_size: int) -> dict:
    data = read(path)
    c.check("%s: magic" % label, data[:4] == SELF_MAGIC)
    version, key_rev, header_type = struct.unpack(">IHH", data[4:12])
    c.equal("%s: SCE version 2" % label, version, 2)
    c.check("%s: key revision" % label, key_rev in (0x0001, 0x8000), "%#06x" % key_rev)
    c.equal("%s: header type 1" % label, header_type, 1)
    meta_off = struct.unpack(">I", data[12:16])[0]
    header_size, embedded_elf = struct.unpack(">QQ", data[16:32])
    c.check("%s: metadata offset inside header" % label,
            0 < meta_off < header_size, "%#x < %#x" % (meta_off, header_size))
    c.check("%s: header smaller than file" % label, header_size < len(data),
            "%#x < %#x" % (header_size, len(data)))
    c.equal("%s: embedded ELF size" % label, embedded_elf, elf_size)
    return {
        "size": len(data),
        "sha256": sha256(data),
        "key_revision": key_rev,
        "header_size": header_size,
        "embedded_elf_size": embedded_elf,
    }


def decrypt_debug_pkg(data: bytes) -> tuple[int, int, bytes]:
    pkg_size, data_offset, data_size = struct.unpack(">QQQ", data[0x18:0x30])
    work = bytearray(data)
    sha = bytearray(0x40)
    sha[0x00:0x08] = work[0x60:0x68]
    sha[0x08:0x10] = work[0x60:0x68]
    sha[0x10:0x18] = work[0x68:0x70]
    sha[0x18:0x20] = work[0x68:0x70]
    counter = 0
    for pos in range(data_offset, data_offset + data_size, 0x10):
        digest = hashlib.sha1(bytes(sha)).digest()
        for i in range(0x10):
            work[pos + i] ^= digest[i]
        counter += 1
        sha[0x38:0x40] = struct.pack(">Q", counter)
    return pkg_size, data_offset, bytes(work[data_offset:data_offset + data_size])


def pkg_info(c: Checker, path: str, icon_path: str, sfo_path: str, eboot_path: str) -> dict:
    data = read(path)
    magic, pkg_type, info_off, unk1, head_size, item_count = struct.unpack(">IIIIII", data[:0x18])
    pkg_size, data_offset, data_size = struct.unpack(">QQQ", data[0x18:0x30])
    content_id = data[0x30:0x60].split(b"\x00")[0].decode("ascii", "replace")
    c.equal("pkg: magic", magic, PKG_MAGIC)
    c.equal("pkg: debug type", pkg_type, 1)
    c.equal("pkg: package size field", pkg_size, len(data))
    footer = len(data) - (data_offset + data_size)
    c.equal("pkg: 0x60-byte footer after data", footer, 0x60)
    c.equal("pkg: data_offset + data_size + footer == file", data_offset + data_size + footer, len(data))
    c.equal("pkg: content id", content_id, CONTENT_ID)
    c.check("pkg: header digest present", data[0x60:0x70] != b"\x00" * 16)
    c.check("pkg: data IV present", data[0x70:0x80] != b"\x00" * 16)
    c.check("pkg: debug footer is zero", data[data_offset + data_size:] == b"\x00" * footer)

    _, _, payload = decrypt_debug_pkg(data)
    c.check("pkg: decrypted payload size", len(payload) == data_size, str(len(payload)))

    items = {}
    for i in range(item_count):
        entry = payload[i * 0x20:(i + 1) * 0x20]
        name_off, name_len, reserved, item_off = struct.unpack(">IIII", entry[:16])
        item_size, item_type, item_pad = struct.unpack(">QII", entry[16:32])
        name = payload[name_off:name_off + name_len].decode("ascii")
        blob = payload[item_off:item_off + item_size]
        items[name] = {"offset": item_off, "size": item_size, "type": item_type,
                       "reserved": reserved, "pad": item_pad, "data": blob}
        c.check("pkg: item %r fits payload" % name, item_off + item_size <= data_size)

    c.equal("pkg: item count", len(items), 4)
    for name, kind in (("ICON0.PNG", ITEM_RAW), ("PARAM.SFO", ITEM_RAW),
                       ("USRDIR", ITEM_DIR), ("USRDIR/EBOOT.BIN", ITEM_NPDRM_SELF)):
        item = items.get(name)
        c.check("pkg: item %r present" % name, item is not None)
        if item is not None:
            c.equal("pkg: item %r type" % name, item["type"], kind)

    if "ICON0.PNG" in items:
        c.equal("pkg: ICON0.PNG matches pkg/ICON0.PNG", items["ICON0.PNG"]["data"], read(icon_path))
        c.check("pkg: ICON0.PNG is a PNG",
                items["ICON0.PNG"]["data"][:8] == b"\x89PNG\r\n\x1a\n")
    if "PARAM.SFO" in items:
        c.equal("pkg: PARAM.SFO matches build/pkg/PARAM.SFO",
                items["PARAM.SFO"]["data"], read(sfo_path))
    if "USRDIR/EBOOT.BIN" in items:
        eboot = read(eboot_path)
        blob = items["USRDIR/EBOOT.BIN"]["data"]
        c.check("pkg: EBOOT.BIN length", len(blob) >= len(eboot),
                "stored %d, file %d" % (len(blob), len(eboot)))
        c.equal("pkg: EBOOT.BIN prefix matches build EBOOT", blob[:len(eboot)], eboot)
        extra = blob[len(eboot):]
        ok = extra == b"\x00" * len(extra)
        c.check("pkg: EBOOT.BIN trailing pad is zero", ok,
                "" if ok else extra.hex())

    return {
        "size": len(data),
        "sha256": sha256(data),
        "content_id": content_id,
        "items": {name: {"size": items[name]["size"], "type": items[name]["type"]}
                  for name in items},
        "data_offset": data_offset,
        "data_size": data_size,
        "footer": footer,
    }


def sfo_fields(data: bytes) -> dict[str, object]:
    # PARAM.SFO is little-endian, unlike the big-endian ELF/SELF/PKG around it.
    magic, version, key_start, data_start, count = struct.unpack("<4sIIII", data[:20])
    if magic != SFO_MAGIC:
        raise ValueError("bad SFO magic %r" % magic)
    fields: dict[str, object] = {}
    for i in range(count):
        off = 20 + i * 16
        key_off, fmt, length, _max_len, data_off = struct.unpack("<HHIII", data[off:off + 16])
        key_end = data.index(b"\x00", key_start + key_off)
        key = data[key_start + key_off:key_end].decode("ascii")
        raw = data[data_start + data_off:data_start + data_off + length]
        if fmt == 0x0204:
            fields[key] = raw.split(b"\x00")[0].decode("ascii")
        elif fmt == 0x0404:
            fields[key] = struct.unpack("<I", raw)[0]
        else:
            fields[key] = raw
    return fields


def verify_sfo(c: Checker, path: str) -> dict:
    fields = sfo_fields(read(path))
    c.check("sfo: parsed", bool(fields), "%d fields" % len(fields))
    for key, want in EXPECTED_SFO.items():
        c.equal("sfo: %s" % key, fields.get(key), want)
    return {k: v for k, v in fields.items() if isinstance(v, (str, int))}


def verify_finalized(c: Checker, path: str, debug_pkg: bytes) -> dict:
    data = read(path)
    c.equal("gnpdrm: same size as debug pkg", len(data), len(debug_pkg))
    c.equal("gnpdrm: finalize flag at offset 4", data[4], 0x80)
    pkg_type = struct.unpack(">I", data[4:8])[0]
    c.equal("gnpdrm: type 0x80000001", pkg_type, 0x80000001)
    stored = data[-0x20:-0x0C]
    digest = hashlib.sha1(data[:-0x20]).digest()
    c.equal("gnpdrm: SHA-1 trailer", stored, digest)
    c.check("gnpdrm: trailer padding is zero", data[-0x0C:] == b"\x00" * 0x0C)
    return {"size": len(data), "sha256": sha256(data), "type": pkg_type}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--homebrew", default=os.path.join(ROOT, "homebrew"),
                    help="directory containing the built artifacts")
    ap.add_argument("--json", default=os.path.join(ROOT, "artifacts", "ps3_build_verify.json"),
                    help="write a JSON report here ('' to skip)")
    args = ap.parse_args(argv)

    hb = os.path.abspath(args.homebrew)
    elf = os.path.join(hb, "ps3texmod.elf")
    stripped = os.path.join(hb, "build", "self", "ps3texmod.elf")
    icon = os.path.join(hb, "pkg", "ICON0.PNG")
    sfo = os.path.join(hb, "build", "pkg", "PARAM.SFO")
    eboot = os.path.join(hb, "build", "pkg", "USRDIR", "EBOOT.BIN")

    missing = [p for p in (elf, stripped, icon, sfo, eboot,
                           os.path.join(hb, "ps3texmod.self"),
                           os.path.join(hb, "ps3texmod.fake.self"),
                           os.path.join(hb, "ps3texmod.pkg"),
                           os.path.join(hb, "ps3texmod.gnpdrm.pkg")) if not os.path.exists(p)]
    if missing:
        print("missing build outputs (run homebrew/build.ps1 first):", file=sys.stderr)
        for p in missing:
            print("  " + p, file=sys.stderr)
        return 2

    c = Checker()
    report: dict[str, object] = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    report["elf"] = elf_info(c, elf)
    stripped_size = len(read(stripped))
    c.check("strip: stripped ELF smaller than raw ELF", stripped_size < report["elf"]["size"],
            "%d < %d" % (stripped_size, report["elf"]["size"]))
    report["self"] = self_info(c, "self", os.path.join(hb, "ps3texmod.self"), stripped_size)
    report["fake_self"] = self_info(c, "fake.self", os.path.join(hb, "ps3texmod.fake.self"), stripped_size)
    report["eboot"] = self_info(c, "eboot", eboot, stripped_size)

    pkg_path = os.path.join(hb, "ps3texmod.pkg")
    report["pkg"] = pkg_info(c, pkg_path, icon, sfo, eboot)
    report["sfo"] = verify_sfo(c, sfo)
    report["gnpdrm"] = verify_finalized(c, os.path.join(hb, "ps3texmod.gnpdrm.pkg"), read(pkg_path))

    report["checks"] = c.results
    report["passed"] = len(c.results) - c.failed
    report["failed"] = c.failed

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")

    print()
    print("%d passed, %d failed" % (report["passed"], report["failed"]))
    if args.json:
        print("report: " + os.path.abspath(args.json))
    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
