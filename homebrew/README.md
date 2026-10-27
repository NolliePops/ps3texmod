# ps3texmod console installer (PS3 homebrew)

This directory contains the PS3 side of ps3texmod: a small PSL1GHT homebrew
application that applies **precomputed** `.ps3patch` plans to a jailbroken /
CFW Prototype install.

It is **not** a runtime texture injector. It does not attach to the game
process, patch the ELF, or intercept RSX draws. It rewrites texture payloads
inside the installed `.rcf` archives on disk (the same file-level approach as
the PC installer), with a rollback journal.

> **Build status:** compiled, linked, signed and packaged on this machine with
> **PS3DK v0.26.0** (GCC 16.1.0 for `powerpc64-ps3-elf`) on Windows. The
> artifacts are in this directory: `ps3texmod.elf`, `ps3texmod.self`,
> `ps3texmod.fake.self`, `ps3texmod.pkg`, `ps3texmod.gnpdrm.pkg`, plus a
> distributable `ps3texmod-1.0-ps3.zip`.
>
> The code has **not been run on a PS3 or checked on a TV**. Console discovery,
> pad input, applying a plan, the journal and the UI are compiled and statically
> reviewed but unverified at runtime. See [Building](#building) and
> [Status](#status-of-what-was-verified).

## What it does

1. Finds a Prototype install on internal HDD or USB: any `USRDIR` containing `.rcf`
   files, both JB folders (`PS3_GAME/USRDIR`) and package installs under
   `/dev_hdd0/game/<TITLE_ID>`. Known ids (`BLUS30145`, `NPUB30389`) are probed
   first, then the whole `/dev_hdd0/game` tree is scanned, so a different regional
   or PSN id is found without a rebuild.
2. Finds `.ps3patch` plans under `ps3texmod/plans`.
3. Verifies each plan against the real archive in one of two modes:
   * **Exact verify** (Cross/Triangle) requires the archive the plan was built
     from: same size, index offset/count, entry hash, index position, payload
     offset, aligned slot size and original payload CRC-32 (or the replacement
     already applied).
   * **Re-anchor** (L1) accepts a different build of the same archive. It finds
     every target by entry hash in the real index, and applies only when the
     stored size and CRC-32 still equal the plan's original bytes and the
     replacement fits the real slot. The recorded offset, index position and
     slot then come from that archive.
4. On confirmation, writes a journal with the complete original 0x800-aligned
   slots, then writes the replacement bytes and updates the 4-byte stored size
   in the RCF index. A re-anchored apply journals the **real** offsets, index
   positions and slots, so restore remains byte-exact.
5. Restores any journal byte-exactly, newest first, then deletes the journal.

No archive is ever rebuilt or resized. This is why a plan is rejected when a
replacement would not fit the existing aligned slot; use the PC installer's
rebuild path for those packs.

There is deliberately **no "apply without verifying" button**. Writing at a
plan's recorded offsets in a differently laid-out archive would overwrite
whatever now lives there — usually another texture, or payload/index data — and
the journal would then contain the wrong slot for rollback. Re-anchor is the
supported answer to a size mismatch: it still checks every target's bytes and
refuses the whole plan on any mismatch. A texture inside an RZ-compressed P3D is
handled the same way, because the plan stores the recompressed asset payload; it
is only valid when the original entry bytes are identical.

## Layout expected on the console

```
/dev_hdd0/ps3texmod/plans/*.ps3patch       (read)
/dev_hdd0/ps3texmod/backups/                (created, journals)
```

Plans are also discovered in these locations:

```
/dev_hdd0/game/BLUS30145/ps3texmod/plans/
/dev_hdd0/game/NPUB30389/ps3texmod/plans/
<game-root>/ps3texmod/plans/
<USRDIR>/ps3texmod/plans/
/dev_usbXXX/ps3texmod/plans/
/dev_usbXXX/GAMES/<game>/PS3_GAME/USRDIR/ps3texmod/plans/
```

Copy plans with FTP/USB. The console app never downloads anything.

## Controls

| Button | Action |
| --- | --- |
| Up / Down | Move the selection (hold to repeat) |
| Cross (X) | Exact verify, then ask to apply |
| Triangle | Exact verify only |
| L1 | Re-anchor verify, then ask to apply (different archive build) |
| Square | Find journals for the selected plan's archive and restore |
| Select | Rescan game installs and plans |
| Circle | Back / cancel |
| Start | Exit |

The UI draws a software framebuffer directly into the RSX display buffer
(the PSL1GHT `rsx.h` quick guide documents CPU writes to the display buffer).
There are no Cg shaders, no font library, and no data files beyond the icon.

## Source map

| File | Purpose |
| --- | --- |
| `source/main.c` | discovery, screens, pad input, actions |
| `source/install.c` | two-pass verification (exact and re-anchor), transactional apply |
| `source/plan.c` | `.ps3patch` parser (little-endian, CRC-32 trailer) |
| `source/journal.c` | journal writer/reader/restore, per-session naming |
| `source/rcf.c` | RCF header + big-endian index parsing and slot lookup |
| `source/io.c` | lv2 file helpers (`sysLv2Fs*`), no stdio buffering |
| `source/crc32.c` | zlib-compatible CRC-32 |
| `source/ui.c` | software framebuffer + 3x5 font renderer |
| `source/util.c` | strings/errors |
| `include/font3x5.h` | generated by `tools/gen_font.py` |

## Building

### Windows, native (used for the artifacts in this directory)

PS3DK ships a prebuilt Windows cross-compiler and native host tools, so no WSL,
MSYS2, `make` or POSIX shell is required.

1. Download `ps3-sdk-v0.26.0-windows-x86_64.zip` from
   <https://github.com/FirebirdTA01/PS3DK/releases/tag/v0.26.0>. The asset used
   here is 1,769,700,102 bytes with SHA-256
   `bd8889455a9ee6effe87e1180484544351bcd029420fac647f9ceee4deb0eddf`
   (checked against the published `.sha256`).
2. Extract it anywhere. This repository keeps a copy in
   `../toolchain/ps3dk/ps3-sdk-v0.26.0-windows-x86_64`, extracted without the
   `spu/` and `samples/` trees to save space.
3. Build:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
# if the SDK is elsewhere:
.\build.ps1 -PS3DK C:\ps3sdk
```

The script resolves the SDK from `-PS3DK`, then `$env:PS3DK`, then
`..\toolchain\ps3dk\ps3-sdk-*-windows-x86_64`. PSL1GHT's `ppu_rules` recipes
need a POSIX shell, so the script performs the same stages directly:

1. compile the 10 `source/*.c` files with
   `-O2 -Wall -Wextra -Wno-unused-parameter -mcpu=cell -mhard-float
   -fmodulo-sched -ffunction-sections -fdata-sections -fcommon`;
2. link with `-lrsx -lgcm_sys -lio -lsysutil -lrt -llv2 -lm` into
   `ps3texmod.elf` (plus a linker map in `build/`);
3. `strip`, then `sprxlinker`;
4. `make_self` → `ps3texmod.self` (CEX SELF), `fself` →
   `ps3texmod.fake.self`;
5. `make_self_npdrm` → `build/pkg/USRDIR/EBOOT.BIN`, `sfo` → `PARAM.SFO`,
   `pkg --contentid ...` → `ps3texmod.pkg`, then `package_finalize` →
   `ps3texmod.gnpdrm.pkg`.

Artifacts of the build in this repository (GCC 16.1.0, PS3DK v0.26.0):

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `ps3texmod.elf` | 1,318,752 | `8f0feae86062050000199c512da0c293a644e2a580b66f9944b54e51d6fe8bac` |
| `ps3texmod.self` | 154,272 | `bf2d41363349df0ccb3a6ea5110b9d5dc7e3645949544a354d84d7a5e4a61772` |
| `ps3texmod.fake.self` | 531,392 | `1d0cd4a0e054c652e8b11a7a9bd9a2011548b098062850d036c239f0a4313d9f` |
| `ps3texmod.pkg` | 158,592 | `6a0d9fc68f741b33b1d2958d60c51a3c1c8086a2b74855fb25bf5fb82c0b9332` |
| `ps3texmod.gnpdrm.pkg` | 158,592 | `5a7fada18a213134c45a4e07e451d8bcdc2ea6b84bab376c3863360408142725` |

`ps3texmod.elf` and `ps3texmod.fake.self` are byte-reproducible from this tree.
The CEX `.self` and both `.pkg` files are not: `make_self` injects data that
changes per invocation (two runs on the same stripped ELF differ), so anything
that embeds its output differs on rebuild. Verify the package by listing the
unfinalized one with `pkg -l`, not by comparing hashes with the table above.

Independent of `pkg`/`readelf`, `python tools/verify_ps3_build.py` re-reads every
artifact and checks the ELF program headers, the three SELF headers, the
debug-package decryption and item table, the SFO fields, and the finalize
trailer. It also confirms that the packaged `ICON0.PNG`, `PARAM.SFO` and
`EBOOT.BIN` match the build tree byte-for-byte. Result for this build: **74
passed, 0 failed** (`artifacts/ps3_build_verify.json`).

`python tools/make_release.py` assembles the distributable ZIP described below
from these artifacts, then re-opens it and verifies every SHA-256:

```
homebrew/ps3texmod-1.0-ps3.zip   1,022,849 bytes
sha256 e23f634fbbca546c1ff56f8343c7abc62a3ab9a426760a07a3a3acad15911a15
```

The ZIP contains `ps3texmod.gnpdrm.pkg`, `ps3texmod-debug.pkg`,
`folder-install/` (`PARAM.SFO`, `ICON0.PNG`, `USRDIR/EBOOT.BIN`), the sample
plan, `INSTALL.txt` and `SHA256SUMS.txt`. Like the artifacts it contains, it has
not been installed on a console.

The build prints 12 warnings, none fatal:

* 10 × `-Wformat-truncation` on `snprintf("%s/%s", ...)` path joins in
  `main.c` and `journal.c`. The destinations are fixed 512-byte buffers;
  over-long paths are truncated, not overflowed.
* 2 × `-Wdeprecated-declarations` for PS3DK's PSL1GHT-compat
  `sysUtilRegisterCallback` / `sysUtilCheckCallback` shims (they forward to
  `cellSysutil*`).

### Linux/macOS/WSL with PSL1GHT

```sh
export PS3DEV=/usr/local/ps3dev
export PSL1GHT=$PS3DEV/psl1ght
./build.sh          # .elf + .self + .fake.self
./build.sh --pkg    # also ps3texmod.pkg
```

`make pkg` copies `pkg/ICON0.PNG`, builds `PARAM.SFO` from `pkg/sfo.xml`,
signs `EBOOT.BIN` with `make_self_npdrm` and packages with `pkg`, then finalizes
a copy. The result is `ps3texmod.pkg` plus `ps3texmod.gnpdrm.pkg`.

### Package contents

`ps3texmod.pkg` is the debug-format package (`Type 00000001`) and lists as:

```
ICON0.PNG              2,527
PARAM.SFO              1,040
USRDIR/                    0
USRDIR/EBOOT.BIN     154,416   (NPDRM SELF)
```

Content ID `UP0001-PS3TEXMD1_00-0000000000000000`. `pkg -l` also reports
`QA_Digest 8E53A05D3B7A7FCBEEC0997B25F25F4` and `K Licensee` all-zero. `PARAM.SFO`:
`TITLE_ID=PS3TEXMD1`, `CATEGORY=HG`, `APP_VER=01.00`,
`PS3_SYSTEM_VER=03.5500`, `BOOTABLE=1`.

`ps3texmod.gnpdrm.pkg` is that package after `package_finalize`: its data
section is re-encrypted with the retail package key and the finalize flag is
set, so `pkg -l` reports `Type 80000001` / `Unsupported Type`. That is expected
for a finalized package, and this is the file a CFW package installer would be
given. Neither file has been installed on a console, and no installer on CFW
has been tested.

The title ID is `PS3TEXMD1`, category `HG`. The installer targets CFW systems
that can run unsigned homebrew; it is not a PSN-signed application.

### Installing on a console (not tested)

* **Package:** copy `ps3texmod.gnpdrm.pkg` to the root of a FAT32 USB drive and
  install it with Package Manager on CFW. `ps3texmod-debug.pkg` is the
  pre-finalize package, for inspection only.
* **JB folder:** copy `PARAM.SFO`, `ICON0.PNG` and `USRDIR/EBOOT.BIN` (the
  fake-signed SELF) to `/dev_hdd0/game/PS3TEXMD1/`.

Neither path has been tried on hardware, and no installer or firmware version is
known to accept the package. `homebrew/ps3texmod-1.0-ps3.zip` contains both
layouts plus `INSTALL.txt` and checksums.

## Safety notes

* Only use this on a game install you can reinstall. A 476 MB `art.rcf` is
  written in place.
* Applying verifies every patch before the first byte is written. If any patch
  does not match, nothing is written.
* A journal is fully written and flushed before the archive is touched.
* If an apply fails after the journal is written, the journal is kept so the
  restore action can roll back.
* Restoring checks the archive size and index layout before writing, and
  verifies each journal slot's CRC before writing it.
* The journal restores the **entire aligned slot**, including padding, so the
  restore is byte-exact even if a slot's pad bytes were not zero.

## Status of what was verified

| Check | Result |
| --- | --- |
| All 10 C sources compile / link (GCC 16.1.0, `-O2`, PSL1GHT headers) | **Passed** (12 warnings, 0 errors) |
| `ps3texmod.elf` is ELF64, big-endian, PowerPC64, entry `0x10000000` | **Verified** with `readelf` |
| `make_self` / `fself` / `make_self_npdrm` produce `SCE\0` SELF files | **Verified** (magic, header version) |
| `pkg` builds a package with the expected 4 items and ContentID | **Verified** with `pkg -l` |
| Reproduced package structure without `pkg` (debug decrypt, item table, SFO, finalize trailer) | **74/74 checks passed** (`tools/verify_ps3_build.py`) |
| `PARAM.SFO` fields (`TITLE_ID`, `CATEGORY=HG`, `PS3_SYSTEM_VER=03.5500`) | **Verified** with `sfo inspect` |
| `package_finalize` sets the retail/finalize flag (`Type 80000001`) | **Verified** |
| Release ZIP contents and SHA-256 manifest | **Verified** by re-reading the ZIP (`tools/make_release.py`) |
| Re-anchor verify/apply (L1) | **Compiled and statically reviewed**; not exercised on a console |
| Runs on a PS3, pad input, USB/HDD discovery, applying/restoring a plan, UI | **Not tested** — no console |

The plan/journal byte layout used by this code was separately cross-checked on
the PC side (`artifacts/check_c_layout.py`, and a real apply/verify/restore run
on a copy of retail `art.rcf`), but that does not substitute for a console test.
