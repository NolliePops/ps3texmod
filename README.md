# ps3texmod

Texture modding toolkit for **Prototype (2009), PS3** — retail disc `BLUS30145`
and PSN `NPUB30389` (other regional ids are handled by discovery; plans are
pinned to the archive bytes, not to the title id).

This project modifies the game's real **RCF / "Cement" archives** directly. It is a
file-level texture modder, not a runtime renderer hook: replacements are written
into the installed `.rcf` files (or a copy of them) and the game reads them on its
next load of that asset. That is the only replacement path that was actually
proven here. No DirectX/GUI interception is involved or claimed.

* Pure Python 3 standard library: no `pip`, no third-party packages.
* Parses real Prototype PS3 archives (not the PC format).
* Dumps every texture, including DDS images embedded inside P3D models.
* Reads Texmod `.tpf` packages and re-encodes their images into real PS3 DDS slots.
* Builds validated `.zip` mod packs with explicit texture IDs and hashes.
* Installs declaratively, with byte-exact pristine backups and enable/disable/remove.
* Builds small precomputed `.ps3patch` plans for the included PS3 homebrew installer.
* The PS3 homebrew is compiled, signed and packaged into a `.pkg` with PS3DK
  v0.26.0 (GCC 16.1.0), but it has **not been run on a PS3**. See
  [Limitations](#limitations). A ready-to-copy release ZIP (both packages, a
  JB-folder tree, sample plan, install notes) is at
  `homebrew/ps3texmod-1.0-ps3.zip`.

---

## Status at a glance

| Part | Status |
| --- | --- |
| RCF parsing / rebuilding / deep validation on retail files | **Confirmed** |
| Texture discovery + DDS/PNG dump, including embedded P3D textures | **Confirmed** |
| Texmod `.tpf` decode and image -> DDS slot encoding | **Confirmed** on the Sam Fisher package; visual result on console unverified |
| Mod packs, install / disable / enable / remove / restore | **Confirmed** on copies of retail `art.rcf` |
| In-place patch and full rebuild of retail `art.rcf` | **Confirmed** on copies |
| `.ps3patch` plan build/verify/apply/restore on PC | **Confirmed** |
| Re-anchor a plan onto a different archive build (PC `--relocate`, console `L1`) | **Confirmed on PC** by tests and a real-archive check; console path compiled, **not run on a PS3** |
| PS3 homebrew installer (C, PSL1GHT) | **Built and signed** (GCC 16.1.0 / PS3DK v0.26.0); **not run on a PS3** |
| PS3 package (`.pkg`) | **Built** - `homebrew/ps3texmod.gnpdrm.pkg` (155 KB); installation untested |
| Independent build verification | **74/74 checks passed** (`python tools/verify_ps3_build.py`) |
| Release ZIP | **Built** - `homebrew/ps3texmod-1.0-ps3.zip`; contents hash-verified, not installed |
| Runtime texture injection into the running game | **Not implemented, not claimed** |

Detailed evidence, commands and file inventory: [REPORT.md](REPORT.md).

---

## Requirements

* Windows/macOS/Linux with Python 3.10+ (written and tested with Python 3.15.2,
  32-bit, on Windows).
* No external libraries.
* For analyzing the retail game you need your own dump of the PS3 disc/USRDIR.
* For the console part: a jailbroken/CFW PS3 with Prototype installed, either as
  a **JB folder** (`.../PS3_GAME/USRDIR/*.rcf`) or as a **package install**
  (`/dev_hdd0/game/<TITLE_ID>/USRDIR/*.rcf` — for example `NPUB30389`), and a way
  to copy files (FTP or USB). The installer finds the game by scanning, but a plan
  only applies to the exact archive it was built from; see the plan check below.

Run everything from the repository root:

```sh
python -m ps3texmod --help
```

Below, `$GAME` is the directory containing `PS3_GAME`, for example
`Prototype (USA, Korea) (En,Fr)`.

---

## 1. Inspect the game

```sh
# Header, counts, name table sanity
python -m ps3texmod info "$GAME/PS3_GAME/USRDIR/art.rcf"

# Every index entry
python -m ps3texmod list "$GAME/PS3_GAME/USRDIR/art.rcf" --limit 20

# Payload kinds (p3d/gfx/dds/png/bik/rz) and totals
python -m ps3texmod stats "$GAME/PS3_GAME/USRDIR/art.rcf"

# Structural validation; --deep also checks payload signatures
python -m ps3texmod verify "$GAME/PS3_GAME/USRDIR/art.rcf" --deep
```

Archives seen in the retail US release:

| File | Entries | Contents |
| --- | ---: | --- |
| `art.rcf` | 2,348 | 1,966 P3D, 360 GFX, 19 standalone DDS, 3 PNG |
| `cached.rcf` | 529 | cached art |
| `woi9.rcf` | 16 | world data |
| `scripts.rcf` | 7 | Lua scripts + stats P3D |
| `shaders.rcf` | 1 | shader bundle |

`art.rcf` is 476,895,845 bytes. Both big-endian P3D (`\xFFD3P`) and byte-swapped
LE P3D (`P3D\xFF`) payloads occur; the dumper handles both.

---

## 2. Dump textures

```sh
# List discovered textures (standalone DDS + DDS inside P3D)
python -m ps3texmod textures "$GAME/PS3_GAME/USRDIR/art.rcf" --filter billboards

# Dump DDS + PNG previews + def.template
python -m ps3texmod dump "$GAME/PS3_GAME/USRDIR/art.rcf" \
    --filter billboards -o work/billboards

# Inspect one DDS
python -m ps3texmod texture-info work/billboards/94CCC0CC_....dds
```

Dumped names are stable IDs:

```
<entry hash>_<offset in asset>_<asset name>     e.g.
94CCC0CC_00000000_billboards_1024x1024_01_diffuse.dds
ADF438E0_000AACFD__billboards_1024x1024_02_diffuse.dds   (inside billboards.p3d)
```

The `def.template` lists `id|dds` lines you can copy into your own definition.

A ready-made example lives in [`samples/billboards/`](samples/billboards/):
16 billboard textures from retail `art.rcf`, with DDS, PNG and `textures.json`.

### Replacement rules

* **Standalone DDS** replacements must keep the **same width, height, format and
  mip count**. The DDS byte length is therefore identical, the P3D/RCF chunk sizes
  stay valid, and the patch stays in place.
* **Embedded P3D textures** are recompressed into the P3D (`RZ` for retail files).
  If the new stored blob fits the existing aligned slot it is patched in place;
  otherwise the PC installer rebuilds the archive.
* Alpha, DXT1/DXT3/DXT5 and mip chains are preserved as authored; the tool does not
  convert formats for you.

---

## 3. Convert a Texmod (`.tpf`) texture mod

Texmod packages are PC-side, but the image inside them is plain BMP, so a `.tpf`
can be turned into a PS3 replacement without any Texmod runtime.

```sh
# What is inside?
python -m ps3texmod tpf "Sam Fisher (Blackwatch Commando)-3-Latest-1579801131.zip" --list

# Extract texmod.def + images
python -m ps3texmod tpf "Sam Fisher (Blackwatch Commando)-3-Latest-1579801131.zip" \
    -o work/samfisher

# Re-encode an image into a real retail slot (dims/format/mips must match)
python -m ps3texmod encode \
    --template work/soldier/3ED441FA_000DCB50_missle001_diffuse.dds \
    --image    work/samfisher/ORIGINALPROTOTYPEF.EXE_0x35931490.bmp \
    --out      work/samfisher/samfisher.dds
```

What the reader handles, all verified against the real package:

* The `.tpf` on disk is **not** a ZIP: every 32-bit little-endian word is XORed
  with `0x3FA43FA4`, which yields a **ZipCrypto-encrypted ZIP**. The password is
  the fixed 42-byte blob TexMod ships with.
* Download sites (Nexus included) wrap that file in an ordinary ZIP; the reader
  unwraps a single `.tpf` member automatically.
* `texmod.def` is `0xHASH|image.bmp` per line and is usually **NUL-padded** after
  the final newline; the padding is ignored.

What it does **not** give you is a target. A Texmod hash is a Texmod/PC id: it is
not an RCF hash, and it is not a CRC32 of any PS3 texture — checked against all
3,513 textures in retail `art.rcf` (header, pixel block, mip 0 and decoded RGBA).
The PS3 target therefore has to be identified by dimensions and role, and that
choice is documented rather than guessed silently.

`encode` is deliberately strict so a P3D/RCF chunk never changes size:

* the template's DDS header is copied, only the size fields are rewritten;
* the image must already have the template's exact dimensions (no implicit
  rescale), and the template must be DXT1;
* the encoded mip chain must reproduce the template's byte length exactly, or the
  command fails instead of writing a short or over-long texture;
* the command prints the final dimensions, format, mip count, byte length and
  SHA-256, so the slot match is visible rather than assumed.

Worked example included: `artifacts/texmod_samfisher/` contains the decoded
`.tpf` members, the encoded 1024×256 DXT1 mips=11 DDS (174,920 bytes, byte length
identical to the retail slot), a 1-line `.def`, the `.ps3pack` and the resulting
`.ps3patch`. On that mod the encoder reproduces the painted pixels at a mean
absolute error of 4.36/255 over the active area, and requires no scaling: the
package is already 1024×256.

> **Honest caveat on visual correctness:** the PC and PS3 atlases for the same
> character are packed differently. The mod's four painted islands (two green
> discs, a rimmed lens, a goggle pair) do not align with any PS3 4:1 character
> atlas that exists in the retail data, so this conversion is a **1:1 pixel-layout
> replacement** of the Blackwatch soldier body atlas. The bytes are valid and the
> game will load them; whether the result *looks* right in-engine is unverified.

---

## 4. Build and install a mod pack

A pack is a ZIP with `manifest.json` plus one DDS per replacement. The manifest
records the target archive, entry hash, nested offset, dimensions, format, mip
count and SHA-256 of every file, so a pack can be validated before it touches an
archive.

Definition file format (`def.txt`), one replacement per line:

```
# comment
art.rcf:94CCC0CC|my_red_billboard.dds
art.rcf:ADF438E0:0xAACFD|my_embedded_texture.dds
```

A bare hash works when the game has only one archive or when `--archive <name>` is
given; with several `.rcf` files, qualify it as `archive.rcf:HASH[:OFFSET]`.

```sh
python -m ps3texmod pack-build \
    --game-dir "$GAME" --def samples/billboard-tint/def.txt \
    --out my-pack.zip --name "My Billboard Pack" --version 1.0.0 \
    --author "you"

python -m ps3texmod pack-info my-pack.zip
python -m ps3texmod pack-validate my-pack.zip --game-dir "$GAME"

python -m ps3texmod install my-pack.zip --game-dir "$GAME"
python -m ps3texmod installed --game-dir "$GAME"
python -m ps3texmod disable my-billboard-pack --game-dir "$GAME"
python -m ps3texmod enable  my-billboard-pack --game-dir "$GAME"
python -m ps3texmod remove  my-billboard-pack --game-dir "$GAME"
python -m ps3texmod restore --game-dir "$GAME"
```

State, pristine backups and installed pack copies live in `$GAME/.ps3texmod/`:

```
.ps3texmod/state.json
.ps3texmod/backups/art.rcf.base     <- byte-exact original
.ps3texmod/packs/<id>.zip
```

Installing is declarative and transactional:

1. every pack is validated against the current game files;
2. the first modification takes a byte-exact `.base` backup (SHA-256 recorded);
3. the archive is restored from `.base` and **all enabled packs are re-applied in
   order** (later packs win);
4. on failure the archive is restored and the state is rolled back.

`disable` restores the original bytes; `remove` deletes the pack and leaves the
base backup metadata in place; `restore` writes every base backup back.

Only use this on an install you can reinstall. This writes real archives (up to
476 MB).

---

## 5. Console plans (PS3 homebrew)

The console program is deliberately small: the PC does all RCF/DDS/RZ work and
writes a `.ps3patch` file containing the exact bytes to write. The PS3 code only
checks a few header fields, verifies CRCs, journals the original slot and writes
the replacement — no zlib, ZIP, JSON or RCF rebuilding on the console.

```sh
# Build a plan from a validated pack (one archive per plan)
python -m ps3texmod plan-build \
    --pack samples/prototype-billboard-tint.zip \
    --game-dir "$GAME" \
    --out samples/prototype-billboard-tint.ps3patch

python -m ps3texmod plan-info   samples/prototype-billboard-tint.ps3patch
python -m ps3texmod plan-verify samples/prototype-billboard-tint.ps3patch --game-dir "$GAME"
```

Copy `*.ps3patch` to the console:

```
/dev_hdd0/ps3texmod/plans/
```

The installer also looks in `<game>/ps3texmod/plans`, `<USRDIR>/ps3texmod/plans`
and `<usb>/ps3texmod/plans`. Journals are written to
`/dev_hdd0/ps3texmod/backups/` as `<archive>.<n>.journal`.

PC-side equivalents (same formats, useful for testing):

```sh
python -m ps3texmod plan-apply   my.ps3patch --game-dir "$GAME" --journal-dir backups
python -m ps3texmod plan-status  --plan-dir . --journal-dir backups
python -m ps3texmod plan-restore --plan my.ps3patch --journal-dir backups --game-dir "$GAME"
```

### Title IDs vs. plan targets

Prototype exists as a disc release (`BLUS30145`) and a PSN download
(`NPUB30389`), plus other regional ids. The plan **does not store a title id**.
It pins the archive name, archive size, index offset/count, entry hash, slot
offset and CRCs, so a plan applies to a copy only when those bytes match:

```sh
# Does your own dump match an existing plan?
python -m ps3texmod plan-verify my.ps3patch --game-dir "$MY_DUMP"

# On Windows, compare the console file directly (a copy is safer than patching
# anything you cannot reinstall):
Get-FileHash "NPUB30389\USRDIR\art.rcf" -Algorithm SHA256
```

The disc archive this repo was validated against is `art.rcf`
476,895,845 bytes, SHA-256
`e0eb529de3f666e928939c5154f9a15fd511b2640d1879e53574905fc3fdfdd1`. If your
`NPUB30389` file has the same size and hash, the existing plans apply unchanged.

If the archive size or hash differs, the normal verify refuses: it pins the
archive layout the plan was built from. For a different build of the same
archive, use **re-anchor** instead. It finds each target by entry hash in the real
index and only accepts it when the stored size and CRC-32 still match the plan's
original bytes; it then writes at the **real** offset and journals that offset,
index position and slot. Any mismatch refuses the whole plan.

```sh
# Size differs? Check first; no writes:
python -m ps3texmod plan-verify my.ps3patch --game-dir "$MY_DUMP" --relocate

# Apply only after that check passes:
python -m ps3texmod plan-apply  my.ps3patch --game-dir "$MY_DUMP" \
    --journal-dir backups --relocate
```

On the console the same check is the `L1` button. There is deliberately no
"apply without verifying" option: a plan records offsets for one archive layout,
so writing at them in a different layout would overwrite unrelated payload or
index data, and a journal taken from the wrong slot could not restore it. If
re-anchor refuses a target (its bytes differ), rebuild the plan from your own
copy with `plan-build`. `--game-dir` accepts a disc/JB folder root
(`PS3_GAME/USRDIR` inside it), a package-install root (`USRDIR` inside it, as in
an extracted `NPUB30389`), or the `USRDIR` itself, so an FTP'd PSN install works
without moving files around.
The console installer finds both layouts: it probes the known ids, then scans
`/dev_hdd0/game/<TITLE_ID>/USRDIR` generically, so a regional or PSN install does
not need a rebuild of the `.pkg`.

The homebrew source, build scripts, built artifacts and console UI documentation
are in [`homebrew/`](homebrew/README.md). It targets a jailbroken/CFW PS3 with a
JB folder or package install; it does **not** attach to the game process.

The console binary is built here with the native-Windows PS3DK toolchain
(`homebrew/build.ps1`, GCC 16.1.0): raw ELF, CEX SELF, fake-signed SELF, and a
package `homebrew/ps3texmod.gnpdrm.pkg` (ContentID
`UP0001-PS3TEXMD1_00-0000000000000000`, `PS3_SYSTEM_VER 03.5500`). It has not
been installed or executed on a console, so on-device behavior (including the
`L1` re-anchor path) is unverified.
`homebrew/ps3texmod-1.0-ps3.zip` bundles both packages, a JB-folder install tree
and the sample plan with `INSTALL.txt` and SHA-256 sums.

A real sample plan is included:
`samples/prototype-billboard-tint.ps3patch` (one 1024×1024 DXT1 billboard,
699,192-byte payload, verified against retail `art.rcf`).

---

## 6. Layout

```
ps3texmod/            Python package (RCF, RZ, DDS, packs, installer, plans, CLI)
homebrew/             PS3 PSL1GHT installer source, build scripts, sfo/icon,
                      built .elf/.self/.pkg artifacts
samples/              dumped retail billboards + example pack/plan
tests/                159 unit/integration tests + real-art.rcf E2E
tools/                dump/sample generators, build verifier, release builder
artifacts/            E2E and build-verification reports, analysis scripts
REPORT.md             technical report: evidence, limits, .pkg status
```

`toolchain/` is a local ~3 GB install of the build inputs, used only for the C
build: the PS3DK Windows SDK, downloaded host tools, and the supplied reference
files (`PSL1GHT-master`, `ps3tools-master`, `keys.conf`, ...). Nothing in
`ps3texmod/` needs it. `homebrew/build.ps1` locates a PS3DK by itself or takes
`-PS3DK <dir>`; `homebrew/README.md` documents the download and its checksum.

Module map:

| Module | Purpose |
| --- | --- |
| `ps3texmod/rcf.py` | RCF header/index/name table, slots, patch, rebuild, validate |
| `ps3texmod/rz.py` | `RZ` zlib container used by retail payloads |
| `ps3texmod/dds.py` | DDS parse, size math, DXT decode, PNG writer |
| `ps3texmod/tpf.py` | Texmod `.tpf` container (XOR + ZipCrypto + plain-ZIP wrapper) |
| `ps3texmod/encode.py` | BMP reader, DXT1 encoder with mips, exact-slot DDS builder |
| `ps3texmod/catalog.py` | texture scan, extraction, P3D embedded DDS, patch build |
| `ps3texmod/pack.py` | pack format, manifest, build/validate/load |
| `ps3texmod/installer.py` | declarative install, backups, state, rollback |
| `ps3texmod/plan.py` | `.ps3patch` and `.journal` formats, PC apply/restore (exact + re-anchor) |
| `ps3texmod/selftest.py` | self-contained end-to-end test (44 checks) |
| `ps3texmod/cli.py` | the command line documented above |

---

## 7. Tests

```sh
python -m unittest discover -s tests -t .
# Ran 159 tests ... OK (skipped=8)

python -m ps3texmod selftest --game-dir "$GAME"
# 44 passed, 0 failed

python tests/real_e2e.py --workdir %TEMP%\ps3texmod-real-e2e
# 35 passed, 0 failed, against a throw-away copy of retail art.rcf
```

The real E2E test never writes to the retail file: it copies `art.rcf`, patches a
standalone billboard in place, forces a full rebuild by growing an embedded P3D
texture, installs/disables/enables/removes a real pack, and verifies that every
other payload stays byte identical. See `artifacts/real_e2e_report.json`.

`selftest` runs against synthetic fixtures and (when `--game-dir` is given) also
checks real `scripts.rcf` hash/offset/size values and RCF validation.

Console build (not part of the Python tests; needs PS3DK, see
[`homebrew/README.md`](homebrew/README.md)):

```powershell
powershell -ExecutionPolicy Bypass -File homebrew\build.ps1
# exit 0, five artifacts; 12 warnings, 0 errors

python tools\verify_ps3_build.py
# 74 passed, 0 failed; writes artifacts/ps3_build_verify.json

python tools\make_release.py
# homebrew/ps3texmod-1.0-ps3.zip, 8 members, checksums re-read and verified
```

`verify_ps3_build.py` does not call `pkg`, `readelf` or `sfo`: it parses the ELF
program headers, the SELF headers, the debug-package crypto and item table, the
little-endian SFO, and the finalize trailer itself, then compares the packaged
files with the build tree.

---

## Limitations

* **No runtime injection.** Textures are replaced on disk. If the game has already
  cached an asset in memory, it becomes visible after the asset is reloaded or the
  game is restarted. This is not a Texmod-style in-process hook, and none is claimed.
* **No PS3 execution.** The homebrew is compiled, signed and packaged here
  (GCC 16.1.0 / PS3DK v0.26.0, 12 non-fatal warnings), but there is no PS3 or CFW
  console to run it on. On-device discovery, pad input, applying/restoring plans
  and the UI are unverified. Treat the console side as an untested build, not a
  proven installer.
* **The package is untested.** `homebrew/ps3texmod.gnpdrm.pkg` exists and its
  contents/metadata were verified with `pkg -l` and `sfo inspect`, but it has
  never been installed. On CFW the finalized package is the intended artifact;
  which installers/firmware accept it was not tested. The finalized package is
  not byte-reproducible (`make_self` changes per invocation).
* **Console plans are in-place only.** A replacement must fit the existing
  0x800-aligned payload slot. Packs that grow the stored blob need the PC installer's
  rebuild path; `plan-build` rejects them with an explanation.
* **Texmod conversion is layout-blind.** A `.tpf` image is mapped 1:1 onto a
  PS3 texture of the same dimensions; the PC and PS3 atlases are packed
  differently, so islands can land on the wrong polygon group. The bytes are
  valid, the visual result is not verified. A Texmod hash cannot be used to find
  the target: it matches no RCF hash and no CRC32 of any PS3 texture.
* **Retail data is required** and not included. The sample files contain only
  textures/diffs derived from the user's own dump.
* **Title/build specific.** Plans verify the full archive size, index offset, entry
  count, entry hash, payload offset, aligned slot size and CRCs. A different game
  build is rejected rather than patched.
* **Firmware.** The console target is CFW/jailbroken firmware that can run unsigned
  homebrew and has the game as a JB folder or a package install; the package
  declares a 3.55 minimum. Nothing here works on OFW, and no PS3 firmware/CFW
  version has been tested.

---

## Safety

Always keep an untouched copy of the game install. The PC installer creates
`.base` backups before its first write, and console applies write a journal with
the complete original 0x800-aligned slots before touching the archive. Restores
verify archive size, index layout and per-slot CRCs before writing, and restore
newest journal first.
