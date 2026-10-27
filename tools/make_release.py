"""Assemble and self-verify the distributable ps3texmod release ZIP.

Contents: the finalized and debug packages, a JB-folder install tree built from
the fake-signed SELF, the sample plan, install notes and SHA-256 checksums.

Usage:
    python tools/make_release.py
    python tools/make_release.py --out homebrew/ps3texmod-1.0-ps3.zip

The script re-opens the ZIP it just wrote, hashes every entry, compares the
result with SHA256SUMS.txt and with the build outputs. It exits non-zero if
anything does not match.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

VERSION = "1.0"
TOP = "ps3texmod-%s-ps3" % VERSION

INSTALL_TXT = """\
ps3texmod %(version)s
Texture installer for Prototype (2009) on PS3
Disc id BLUS30145, PSN id NPUB30389; other regional ids are found by scanning.

STATUS
------
Built and structurally verified on a PC. NOT tested on a PS3, on CFW, or in the
game. There is no runtime texture injection: the tool rewrites texture data
inside the installed .rcf archives, and the game must reload the asset (usually
a restart) before the change is visible.

FIRMWARE
--------
CFW / jailbroken firmware with unsigned homebrew support. The package declares
PS3_SYSTEM_VER 03.5500. No firmware or CFW version has been tested. OFW is not
supported.

REQUIREMENTS
------------
- A Prototype install whose art.rcf matches the plan. The plan stores no title id:
  it checks the archive size, index offset, entry count, entry hash, aligned slot
  size and CRC-32. Exact verify rejects a different dump instead of patching it.
  BLUS30145 (disc) and NPUB30389 (PSN) are different ids for the same game; their
  archives are interchangeable only if the bytes match. Verify with
  "python -m ps3texmod plan-verify <plan> --game-dir <dump>" on a PC.
- If only the archive layout/size differs, re-anchor (L1 on the console, or
  "plan-verify --relocate" on a PC). It looks every target up by entry hash and
  applies only when its stored bytes still match the plan; otherwise it refuses
  the whole plan. There is no unverified apply mode.
- A .ps3patch plan made for that install. plans/ contains one sample; if
  re-anchor refuses your archive, rebuild the plan from your own dump.

A. INSTALL AS A PACKAGE (CFW)
-----------------------------
1. Copy ps3texmod.gnpdrm.pkg to the root of a USB drive (FAT32).
2. On the PS3: Package Manager -> Install Package Files -> USB -> select it.
3. Start "ps3texmod" from the XMB game column.

ps3texmod-debug.pkg is the same package before finalization. It is included for
inspection/testing with the pkg tools, not as the normal install file.

B. FOLDER INSTALL (alternative)
-------------------------------
Copy folder-install/ to /dev_hdd0/game/PS3TEXMD1/ so that these exist:

  /dev_hdd0/game/PS3TEXMD1/PARAM.SFO
  /dev_hdd0/game/PS3TEXMD1/ICON0.PNG
  /dev_hdd0/game/PS3TEXMD1/USRDIR/EBOOT.BIN

EBOOT.BIN here is the fake-signed SELF. Refresh the XMB or reboot afterwards.

PLANS AND JOURNALS
------------------
Put plans in /dev_hdd0/ps3texmod/plans/*.ps3patch. The app also searches:

  /dev_hdd0/game/BLUS30145/ps3texmod/plans/
  /dev_hdd0/game/NPUB30389/ps3texmod/plans/
  <game>/ps3texmod/plans/
  <USRDIR>/ps3texmod/plans/
  /dev_usbXXX/ps3texmod/plans/
  /dev_usbXXX/GAMES/<game>/PS3_GAME/USRDIR/ps3texmod/plans/

Journals are written to /dev_hdd0/ps3texmod/backups/<archive>.<n>.journal.
Square restores the selected plan's archive; restores run newest first and check
each slot CRC before writing.

CONTROLS
--------
Up / Down   move the selection
X           exact verify, then ask to apply
Triangle    exact verify only
L1          re-anchor verify, then ask to apply (different archive build)
Square      restore journals for the selected plan's archive
Select      rescan game installs and plans
Circle      back / cancel
Start       exit

SAFETY
------
Only use this on a game install you can reinstall. Applying writes the 476 MB
art.rcf in place. The journal stores every original 0x800-aligned slot before
the archive is touched, and restore verifies the archive layout first. Keep your
own untouched backup as well.

FILES
-----
ps3texmod.gnpdrm.pkg     finalized package (intended for CFW install)
ps3texmod-debug.pkg      debug package, for inspection with pkg tools
folder-install/          PARAM.SFO, ICON0.PNG, USRDIR/EBOOT.BIN (fake SELF)
plans/                   sample billboard-tint plan for the matching build
SHA256SUMS.txt           hashes of the files above
"""


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add(zf: zipfile.ZipFile, name: str, data: bytes, date) -> None:
    info = zipfile.ZipInfo(TOP + "/" + name, date)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--homebrew", default=os.path.join(ROOT, "homebrew"))
    ap.add_argument("--samples", default=os.path.join(ROOT, "samples"))
    ap.add_argument("--out", default=os.path.join(ROOT, "homebrew", "ps3texmod-%s-ps3.zip" % VERSION))
    args = ap.parse_args(argv)

    hb = os.path.abspath(args.homebrew)
    out = os.path.abspath(args.out)

    sources = {
        "ps3texmod.gnpdrm.pkg": os.path.join(hb, "ps3texmod.gnpdrm.pkg"),
        "ps3texmod-debug.pkg": os.path.join(hb, "ps3texmod.pkg"),
        "folder-install/PARAM.SFO": os.path.join(hb, "build", "pkg", "PARAM.SFO"),
        "folder-install/ICON0.PNG": os.path.join(hb, "pkg", "ICON0.PNG"),
        "folder-install/USRDIR/EBOOT.BIN": os.path.join(hb, "ps3texmod.fake.self"),
        "plans/prototype-billboard-tint.ps3patch":
            os.path.join(args.samples, "prototype-billboard-tint.ps3patch"),
    }
    missing = [p for p in sources.values() if not os.path.exists(p)]
    if missing:
        print("missing inputs (build them first):", file=sys.stderr)
        for p in missing:
            print("  " + p, file=sys.stderr)
        return 2

    files = {name: read(path) for name, path in sources.items()}
    files["INSTALL.txt"] = (INSTALL_TXT % {"version": VERSION}).encode("utf-8")

    sums = "".join("%s  %s\n" % (sha(data), name) for name, data in sorted(files.items()))
    files["SHA256SUMS.txt"] = sums.encode("ascii")

    date = (2026, 10, 26, 0, 0, 0)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in sorted(files):
            add(zf, name, files[name], date)

    # ---- independent re-read of the ZIP ----------------------------------
    errors: list[str] = []
    with zipfile.ZipFile(out) as zf:
        bad = zf.testzip()
        if bad is not None:
            errors.append("corrupt ZIP member: %s" % bad)
        names = set(zf.namelist())
        want = {TOP + "/" + n for n in files}
        if names != want:
            errors.append("member list differs: missing %r, extra %r" %
                          (sorted(want - names), sorted(names - want)))
        members = {}
        for info in zf.infolist():
            rel = info.filename[len(TOP) + 1:]
            members[rel] = zf.read(info)
        for name, data in files.items():
            if members.get(name) != data:
                errors.append("member does not round-trip: %s" % name)
        listed = {}
        for line in members.get("SHA256SUMS.txt", b"").decode("ascii").splitlines():
            digest, rel = line.split("  ", 1)
            listed[rel] = digest
        for name, data in files.items():
            if name == "SHA256SUMS.txt":
                continue
            if listed.get(name) != sha(data):
                errors.append("SHA256SUMS mismatch for %s" % name)
        if members.get("folder-install/USRDIR/EBOOT.BIN") != read(sources["folder-install/USRDIR/EBOOT.BIN"]):
            errors.append("folder EBOOT.BIN is not the fake SELF")

    print("release: " + out)
    print("size:    %d bytes" % os.path.getsize(out))
    print("sha256:  " + sha(read(out)))
    print("members:")
    for name in sorted(files):
        print("  %-42s %9d  %s" % (name, len(files[name]), sha(files[name])))

    if errors:
        print()
        for e in errors:
            print("FAIL  " + e, file=sys.stderr)
        return 1
    print()
    print("verified: %d members, checksums and folder-install EBOOT.BIN match" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
