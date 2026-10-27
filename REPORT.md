# ps3texmod — technical report

**Project:** texmod-style texture modding for *Prototype* (2009) on PlayStation 3
**Target title:** `BLUS30145` (disc, USA/Korea, En/Fr) and `NPUB30389` (US PSN);
plans are pinned to archive bytes, not to the title id (§5.1)
**Date:** 2026-10-26
**Host:** Windows, Python 3.15.2 (32-bit), PS3DK v0.26.0 (GCC 16.1.0) for the
console build, no PS3 hardware

---

## 1. Executive summary

This project decodes the actual texture containers used by the PS3 release of
*Prototype* and provides two installers for them:

1. **PC tool** (`ps3texmod`, pure Python): parses RCF/"Cement" archives, dumps
   standalone and P3D-embedded DDS textures, decodes Texmod `.tpf` packages and
   re-encodes their images into exact PS3 DDS slots, builds validated ZIP mod packs,
   installs them with pristine backups and declarative enable/disable/remove, and
   builds/verifies/applies compact `.ps3patch` plans with rollback journals.
2. **PS3 homebrew** (`homebrew/`, C/PSL1GHT): a small on-console installer that
   verifies and applies those precomputed plans, with a controller UI and journaled
   rollback. It is compiled, signed and packaged here, but not executed on a
   console.

### What is confirmed

* The RCF header/index/name-table layout, the `RZ` zlib payload container, and DDS
  blobs embedded in both BE and byte-swapped LE P3D payloads, verified against the
  retail files.
* Texture extraction, DDS/PNG dumping, mod-pack build/validate/install/disable/
  enable/remove, and byte-exact restore — exercised on **copies** of the retail
  476,895,845-byte `art.rcf`.
* In-place patching **and** a forced full archive rebuild of that retail copy,
  including index-size updates, hash-sorted index preservation, name-table sanity,
  and byte-identity of every untouched payload.
* `.ps3patch` build/verify/apply/restore on the PC, including multi-journal
  restore and re-anchoring onto a different archive build when every target's
  stored bytes still match (§5.2).
* Texmod `.tpf` decoding (XOR + ZipCrypto, plain-ZIP wrapper, NUL-padded
  `texmod.def`) and image → DDS re-encoding that reproduces a retail texture's
  exact byte length; the conversion was taken through pack → plan → apply →
  deep-verify → restore on a copy of retail `art.rcf` (§4).
* 159 automated tests pass; the 44-check self-test passes; a 35-check real-file E2E
  passes (details in §7).
* The PS3 homebrew sources compile and link against real PSL1GHT headers with GCC
  16.1.0 (PS3DK v0.26.0), and produce `SCE\0` SELF files plus a package whose
  contents, ContentID and `PARAM.SFO` were verified (§9).

### What is **not** confirmed

* **The PS3 homebrew has not been run on a PS3.** It compiles, links, signs and
  packages, but no console was available: pad input, USB/HDD discovery, apply,
  journal restore and the rendered UI are all unverified at runtime (§10).
* **The `.pkg` has not been installed.** It exists and its structure was checked,
  but no CFW package installer or firmware behavior was tested (§9).
* **Nothing was tested on a PS3 or on CFW.** See §10.
* **No runtime texture injection.** The tool changes textures on disk. It does not
  attach to the game, patch `EBOOT.BIN`, or intercept RSX draws. No Texmod-style
  in-process replacement is implemented or claimed.
* **Texmod visual correctness is unverified.** PC and PS3 atlases are packed
  differently, so a `.tpf` image is mapped 1:1 by pixel layout, not by UV island.
  The patched bytes are valid and load, but nobody has looked at the result
  in-engine; treat the Sam Fisher plan as an experimental conversion (§4).

---

## 2. Target identification

From the retail `PARAM.SFO` (`PS3_GAME/PARAM.SFO`):

| Field | Value |
| --- | --- |
| `TITLE_ID` | `BLUS30145` (disc); the PSN build uses `NPUB30389` |
| `TITLE` | `[PROTOTYPE]™` |
| `APP_VER` | `01.00` |
| `CATEGORY` | `DG` (disc game) |
| `PS3_SYSTEM_VER` | `02.6000` |

`EBOOT.BIN` is an encrypted PS3 SELF. It is not parsed, patched, or required by
this tool. The console installer accepts either install layout: a jailbroken/CFW
**JB folder** (`.../PS3_GAME/USRDIR/*.rcf`) or a **package install**
(`/dev_hdd0/game/<TITLE_ID>/USRDIR/*.rcf`). It probes `BLUS30145` and
`NPUB30389`, then scans `/dev_hdd0/game` as a parent, so another regional id does
not require a rebuild. A disc-only or ISO-only install cannot be modified this
way. The homebrew package declares
`PS3_SYSTEM_VER 03.5500`, so its minimum firmware target is 3.55 CFW; no specific
CFW build or version has been tested.

Files examined (all read-only):

| Archive | Bytes | Entries | Kinds |
| --- | ---: | ---: | --- |
| `art.rcf` | 476,895,845 | 2,348 | 1,966 P3D, 360 GFX, 19 DDS, 3 PNG |
| `cached.rcf` | — | 529 | cached art |
| `woi9.rcf` | — | 16 | world data |
| `scripts.rcf` | — | 7 | Lua + stats P3D |
| `shaders.rcf` | — | 1 | shader bundle |

`art.rcf` SHA-256:
`e0eb529de3f666e928939c5154f9a15fd511b2640d1879e53574905fc3fdfdd1`.

---

## 3. Format findings

### 3.1 RCF ("ATG CORE CEMENT LIBRARY")

Confirmed on all five archives. All multi-byte header fields are **big-endian**.

| Offset | Size | Meaning |
| ---: | ---: | --- |
| `0x00` | 23 | magic `ATG CORE CEMENT LIBRARY` + NUL |
| `0x20` | 4 | version (`0x02010101` in these files) |
| `0x24` | 4 | index offset (usually `0x3C`) |
| `0x28` | 4 | index size in bytes = `12 * count` |
| `0x2C` | 4 | name-table offset |
| `0x30` | 4 | name-table size |
| `0x38` | 4 | entry count |

* Index: `count` big-endian triples `(hash, payload_offset, stored_size)`, sorted by
  hash and starting immediately at the index offset.
* Payload slots: every payload starts on a **0x800-byte boundary**. A slot ends at
  the next payload offset, or at EOF for the highest-offset payload. The slot is
  what can be overwritten in place, including padding.
* Name table: `tab_off = align_up(0x3C + 12*count, 0x800)`, payload data starts at
  `align_up(tab_off + tab_size, 0x800)`.
* Each name record is a 16-byte header (`u32 tag` ending in `0x49`, `0x800`, `0`,
  little-endian name length including NUL) followed by the NUL-terminated name and
  three fixed filler bytes.

**Name ↔ data mapping.** The proprietary name-hash function was not recovered; an
exhaustive set of candidate hashes (CRC-32, FNV, Jenkins, Murmur, PS3 variants,
case/backslash permutations) did not match. This does not block the tool: the name
records are stored in **payload-offset order**, so each record maps 1:1 to the index
triple whose offset is next in ascending order. The mapping was verified on 2,938
assets: every `.dds` record resolves to a DDS signature, `.gfx` to `GFX`, `.bik` to
`BIKi`, `.rz` to `RZ`, and `.png` to `PNG`.

### 3.2 Payload containers

* `RZ`: 16-byte header — ASCII `RZ`, 6 zero bytes, then a little-endian `u64`
  uncompressed size, followed by a zlib stream to the stored end. Used by most
  art/P3D payloads; rebuild recompresses with zlib at level 9.
* Everything else (`DDS`, `GFX`, `PNG`, `BIKi`, …) is stored raw.
* P3D payloads carry textures as embedded DDS blobs. `art.rcf` contains **1,493
  big-endian P3D** (`\xFFD3P`) and **473 byte-swapped LE P3D** (`P3D\xFF`) payloads.
  The dumper locates DDS headers inside the decompressed P3D and records the exact
  offset and size, so embedded replacements are byte-precise.

### 3.3 Standalone billboard set

19 standalone DDS files were found: 12 at 699,192 bytes (1024×1024, DXT1,
11 mip levels), including `94CCC0CC` =
`\art\billboards\billboards_1024x1024_01_diffuse.dds`, and 7 midground textures at
262,272 bytes. `billboards.p3d.rz` (`ADF438E0`) is an LE P3D holding four more DDS
images at offsets `0xBE`, `0xAACFD`, `0x15593C`, `0x20057B` (each 699,192 bytes).

---

## 4. Implementation

| Module | Role |
| --- | --- |
| `ps3texmod/rcf.py` | Header/index/name parsing, slot math, `read_stored`, in-place patch, full rebuild, deep validation |
| `ps3texmod/rz.py` | RZ pack/unpack (zlib), size probe |
| `ps3texmod/dds.py` | DDS header validation, mip size math, DXT1/3/5 decode, PNG encoder |
| `ps3texmod/tpf.py` | Texmod `.tpf` container: word XOR, ZipCrypto, plain-ZIP unwrap, `texmod.def` parser |
| `ps3texmod/encode.py` | BMP reader/writer, DXT1 block encoder with mip chain, exact-length DDS slot builder |
| `ps3texmod/catalog.py` | Archive scan, texture enumeration (standalone + P3D), extraction, patch blob build |
| `ps3texmod/pack.py` | ZIP pack format, manifest, definition parser, token resolution, validation |
| `ps3texmod/installer.py` | Declarative install, `.base` backups, state, enable/disable/remove, rollback |
| `ps3texmod/plan.py` | `.ps3patch` writer/reader, CRC verify/apply, `.journal` write/read/restore, multi-journal scan |
| `ps3texmod/selftest.py` | Self-contained fixture test (44 checks) + optional real-archive checks |
| `ps3texmod/cli.py` | 28 subcommands (`info` … `plan-status`) |

### Replacement-size policy

* Standalone DDS replacements must match the original width, height, pixel format
  and mip count. The resulting DDS byte length is identical, so all container sizes
  remain valid and the replacement is always in-place.
* Embedded P3D replacements are recompressed. If the new stored blob fits the
  existing aligned slot, it is patched in place; otherwise the PC installer rebuilds
  the archive (new offsets/sizes/name table), which was exercised on retail data.
* The console only supports in-place plans. `plan-build` rejects a pack whose
  compressed blob exceeds the slot and tells the user to use the PC rebuild path.

### Texmod conversion path (`.tpf` → DDS → plan)

Texmod is behavioural reference only; no DirectX interception is used or claimed.
The package is decoded offline, the image is re-encoded as a real PS3 DDS, and the
result goes through the same pack/plan pipeline as any other replacement.

**Container.** A `.tpf` is not a ZIP on disk. Every 32-bit little-endian word is
XORed with `0x3FA43FA4`, which produces a **ZipCrypto-encrypted ZIP**; the password
is the fixed 42-byte blob used by TexMod/OpenTexMod. The user's download
(`Sam Fisher (Blackwatch Commando)-3-Latest-1579801131.zip`, Nexus mod 3 by
KEVIN465M0RAN) is an ordinary ZIP wrapping `Sam Fisher V3.tpf`, so the reader
unwraps a single `.tpf` member first. Inside are
`ORIGINALPROTOTYPEF.EXE_0x35931490.bmp` (1024×256, 24 bpp, 786,486 bytes) and
`texmod.def` = `0x35931490|ORIGINALPROTOTYPEF.EXE_0x35931490.bmp` followed by NUL
padding, which the parser ignores.

**Target identification.** The Texmod id is a dead end by construction: `0x35931490`
is not an RCF hash, and it is not a CRC32 of any PS3 texture — checked against all
3,513 textures in retail `art.rcf` as header+payload, pixel block, mip 0 and decoded
RGBA (`artifacts/tex_probe.json`). Matching therefore had to be by dimensions and
role:

* `art.rcf` has 14 textures at 1024×256. Eight are DXT1/mips=11/174,920 bytes:
  `Soldier`, `female`, `pilot_disguise`, `NIS_1_3_c`, `DanaMercer` (×2),
  `karen_parker` (×2) and `MissionPed`. The rest are DXT5 variants and one HUD
  image. `pilot_disguise` (`82D48EBF:000000B6`) is byte-identical to the Soldier
  atlas, so both are the same body texture.
* The target is `3ED441FA:000DCB50` in
  `\art\packages\missions\Soldier\Soldier.p3d.rz`: the Blackwatch infantry body
  atlas, and the only 1024×256 soldier atlas. The Blackwatch *disguise* packages
  (`bwofficer`, `bwtrooper`, `commander`, `soldier_disguise`) are 768×256 and
  cannot take the mod 1:1 without rescaling, which the strict slot rule forbids.
  The other 1024×256 DXT1 atlases belong to `female`, `DanaMercer`,
  `karen_parker`, `MissionPed` and a NIS frame, so they are different characters.
* The mod's painted islands do not align with the Soldier atlas layout (or with any
  other PS3 4:1 atlas). A scan of `cached.rcf` + `woi*.rcf` found 44 four-to-one
  textures, all effectively blank; `artifacts/tex_41_scan.json` has the per-spot
  measurements.

**Consequence.** The conversion is a **1:1 pixel-layout replacement**, not a
layout-aware reassembly. The mod's four islands (green discs at (56,7) and (231,232),
a rimmed lens at (198,15)–(236,50), a goggle pair at (93,148)–(153,171)) are written
where the author put them in the 1024×256 image. PC and PS3 atlases are packed
differently, so the in-game appearance is **experimental and unverified**.

**Encoder strictness.** `encode` copies the template DDS header (rewriting only the
size fields), requires identical dimensions and DXT1, encodes a full mip chain, and
then re-parses the result: if the byte length differs from the retail texture by even
one byte it aborts. The Sam Fisher output is 1024×256 DXT1, 11 mips, 174,920 bytes —
exactly the retail length — with a mean absolute error of 4.36/255 over the 2,058
active source pixels (1,978 remain above the black threshold after DXT1).

**End-to-end result on PC.** `pack-build` → `plan-build` produced a 1-patch plan
(`old size 3,906,685 → new 3,799,312`, slot 3,907,584, CRC `33733199 → D402D28E`).
On a copy of retail `art.rcf`: apply wrote 1 slot and a journal, `plan-verify`
reported `already=1`, `verify --deep` passed 2,348/2,348 entries, the patched texture
decoded with the mod's green islands intact, `plan-restore` succeeded, and the
archive SHA-256 returned to `E0EB529D…FDFDD1`, byte-identical to the original.

### Backups and rollback

The PC installer writes `$GAME/.ps3texmod/backups/<archive>.base` before the first
write and records its SHA-256. Every change is applied from that pristine base plus
the ordered list of enabled packs, so disable/enable/remove are deterministic. On a
failed install the archive is restored and the previous state is kept.

The console writes a separate `<archive>.<n>.journal` per apply session. Each record
holds the **complete original 0x800-aligned slot**, not just the payload, so restore
is byte-exact even if slot padding is non-zero. A journal is fully written and
`fsync`ed before the archive is touched. Restore verifies archive size, index layout
and each slot's CRC, then writes newest journal first and deletes it after success.

---

## 5. Console plan format

`.ps3patch` is little-endian for a small C reader. Summary (full details in
`ps3texmod/plan.py`):

```
0x00  8  "PS3TPAT1"
0x08  4  version (1)
0x0C  4  header bytes (64)
0x10  4  archive-name length
0x14  4  plan-name length
0x18  4  patch count
0x1C  4  blob bytes
0x20  4  archive index offset
0x24  4  archive index count
0x28  8  archive size
0x30  4  flags (1 = in-place only)
0x34  4  string bytes
...      archive name + plan name + labels
...      48-byte records: hash, index, offset, old/new/slot sizes,
         blob offset, old/new CRC-32, label offset/length
...      replacement blobs (16-byte aligned)
...      16-byte trailer "PS3TPND1" + CRC-32 of everything before the trailer
```

Verification on the console cross-checks the archive size, index offset/count, the
index triple at the recorded position (hash, offset, stored size), the aligned slot
size, the CRC of the replacement in the plan, and the CRC of the current payload.

The sample plan verifies against retail `art.rcf`:

```
name  Billboard Warm Tint 1.0.0
hash  94CCC0CC   index 1309   offset 10,950,656 (0xA72B00)
size  699,192 -> 699,192     slot 700,416
CRC   2E064AE3 -> 54A3FFFE
```

The C plan and journal layouts were cross-checked against Python by
`artifacts/check_c_layout.py`. It asserts the header/record offsets and sizes used
by the C code against the real sample plan and a generated journal; the latest run
(`artifacts/check_c_layout.log`) reports:

```
plan OK: 1 record(s), blob_start=195, trailer=699395, archive=art.rcf
journal OK: header=36, record=28, records=1, end=4171
C layout check OK
```

This validates the file layouts, **not** the C compiler or runtime behavior.

### 5.1 Title ids are search hints

A plan contains no title id at all — only the archive name and the byte-level
facts above. That is deliberate: Prototype shipped as a disc release
(`BLUS30145`) and as a PSN download (`NPUB30389`), and other regional ids exist.
Two ids are only interchangeable if their `.rcf` bytes are; if the archive size,
index layout, entry hash or slot CRC differ, verify/apply refuses the plan.

The first homebrew build searched for `BLUS30145` by name, so on a `NPUB30389`
package install it found nothing to patch (the `PS3TEXMD1` app itself still ran,
but the game directory was never listed). The fix, in this revision:

* `/dev_hdd0/game` is scanned as a parent directory, so **any**
  `/dev_hdd0/game/<TITLE_ID>/USRDIR` containing `.rcf` files is discovered;
* `BLUS30145` and `NPUB30389` are also probed explicitly as a fallback when the
  directory listing is not readable;
* the same applies to `/dev_hdd0/GAMES`, `/dev_hdd0/games` and each USB root;
* plan search covers both ids' `ps3texmod/plans` directories, and the per-USRDIR
  search already finds the rest;
* the header label shows the tool version instead of a hardcoded game id.

A user whose archive does not match an existing plan should rebuild it from their
own dump (`plan-build --game-dir <their copy>`); no assumption is made that the
PSN data is identical to the disc data, because that was not checked here. The
disc archive every plan in this repo was built against is `art.rcf`,
476,895,845 bytes, SHA-256
`e0eb529de3f666e928939c5154f9a15fd511b2640d1879e53574905fc3fdfdd1`
(`artifacts/real_e2e_report.json`); a PSN file with the same size and hash needs
no rebuild, and anything else is rejected by exact `plan-verify`. A different
build can be checked with `--relocate`, described in §5.2.

The PC commands resolve all three layouts for `--game-dir`: a disc/JB root
(`<game>/PS3_GAME/USRDIR`), a package root (`<game>/USRDIR`, e.g. an extracted
`NPUB30389`), or the `USRDIR` itself. Before this revision only the first and last
were handled, so pointing `plan-verify` at a package root failed with
`art.rcf not found`; `tests/test_layout.py` now covers all three (plus an
`install`/`uninstall_all` cycle on the flat layout).

### 5.2 Different builds: re-anchoring, not blind apply

An RCF archive is a container: payload order, alignment padding, the index and
the name table can differ between two builds even when individual textures are
byte-identical. A different archive size therefore does **not** by itself prove
that a replacement would corrupt anything — but it also does not prove the
opposite. The plan records offsets and index positions for one specific layout,
so writing a valid DDS blob at those offsets in another layout would overwrite
whatever now occupies them: another texture, padding, or index/table data. The
DDS itself can be perfectly well formed and the archive still be damaged. That
is why the answer is not an "apply without verifying" button.

The revision adds a second verification mode, **re-anchor**, on both sides:

* PC: `plan-verify --relocate` / `plan-apply --relocate`.
* Console: the `L1` button (Cross/Triangle still use exact verify).

Re-anchor does not trust any recorded position. For each target it:

1. looks up the plan's 32-bit entry hash in the real index;
2. requires the entry's stored size to equal the plan's original `old_size` and
   its stored bytes to have the plan's original CRC-32 (or the patched
   `new_size`/CRC, in which case it counts as already applied);
3. requires the replacement to fit the **real** aligned slot (next payload
   offset minus this one, or EOF) and the slot to stay under the 64 MiB journal
   cap;
4. takes the real offset, real index position and real slot size for the write.

The journal header records the real archive size/index offset/count and each
record the real slot, so restore validates against the archive that was actually
modified and remains byte-exact. Any target that fails steps 1–3 refuses the
whole plan; re-anchor never applies a subset and never falls back to the
recorded offsets. Because the acceptance test is CRC-32 of the stored bytes,
this also covers embedded textures: the Sam Fisher plan's target is a texture
inside an RZ-compressed P3D, so its plan blob is the recompressed asset payload.
It is valid only when the entire original entry payload is identical, which is
exactly what the check enforces.

What re-anchor does not do: it does not make a plan for one game *version* apply
to art that is genuinely different, and it does not weaken the rule that every
target must match. If a target's bytes differ, the only correct action is to
rebuild the plan against that archive (`plan-build --game-dir <the install>`).

Real-data check on this machine (the disc archive, which is the plan's source):

```
python -m ps3texmod plan-verify artifacts/texmod_samfisher/samfisher.ps3patch \
    --game-dir Prototype --relocate
ok: true, pending: 1, already: 0, relocated: 0
```

`relocated: 0` is expected here because the archive is the exact build the plan
came from; a different build that passes will report how many targets moved.
The C implementation of this path (`install_verify_mode`/`install_apply_mode`)
compiled cleanly and was reviewed, but has not been run on a console.

---

## 6. Samples and artifacts

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `samples/prototype-billboard-tint.zip` | 534,070 | `ea572224d41a2ed33d48ae7922b49ce676845b6b7efaa2fb19680ac9d5984fdb` |
| `samples/prototype-billboard-tint.ps3patch` | 699,411 | `c0a4c45744af231052355eef28a6384e8f491a69057fd0ebf31f80331614b25f` |
| `samples/billboard-tint/94CCC0CC_billboards_01_tint.dds` | 699,192 | `844d07064198434e7131f66f1f9f81c39aeb14adc435e61db715afc8ee383399` |
| `homebrew/pkg/ICON0.PNG` | 2,527 | `6e59deb09199a26fe15b96820c26d6a9190170b39f378d01b5e2d2d93d52a123` |
| `homebrew/ps3texmod-1.0-ps3.zip` | 1,022,849 | `e23f634fbbca546c1ff56f8343c7abc62a3ab9a426760a07a3a3acad15911a15` |
| `artifacts/texmod_samfisher/ORIGINALPROTOTYPEF.EXE_0x35931490.bmp` | 786,486 | `59b7e24259903fbcea895189cc97a4f74990203609553c896f2c3ad4d374bd00` |
| `artifacts/texmod_samfisher/samfisher_3ED441FA_000DCB50.dds` | 174,920 | `399611370c95798c1687dcb4728f7b9fca45032bc022ee6150e730fe26551ed5` |
| `artifacts/texmod_samfisher/samfisher.ps3pack` | 3,977 | `68ee84ba1a9768b1b4d4a312eb9e93049a7c9fc89e3cd7f297cc3ea2ae36ed07` |
| `artifacts/texmod_samfisher/samfisher.ps3patch` | 3,799,524 | `824a89a47ccf3692e515700920114f76250b3ea10c09e0b09add1e20d9411011` |

`samples/billboards/` contains 16 retail billboard textures (DDS + PNG), a
`textures.json` index and a `def.template`.

The tint itself was generated by `tools/make_sample_pack.py` from the retail
billboard: mean RGBA changed from 109.5/107.5/107.8 to 131.4/86.9/65.2 (alpha 255),
with the DDS header, dimensions, DXT1 format and 11 mips preserved.

`artifacts/` holds the real-file E2E report/log, the independent build
verification report (`ps3_build_verify.json`) and the decoders/probes used
during format investigation (hand DXT block decoder, RCF layout probes, C layout
check).

`artifacts/texmod_samfisher/` is the worked Texmod conversion: decoded package
members (`.bmp`, `texmod.def`), the encoded slot DDS, the one-line `samfisher.def`,
`samfisher.ps3pack` and `samfisher.ps3patch`. It is derived from a third-party
Texmod by KEVIN465M0RAN and is kept as a local analysis artifact, not as a
redistributable sample; `samples/` contains only the tool's own billboard work.

---

## 7. Test evidence

### 7.1 Unit and integration suite

```
python -m unittest discover -s tests -t .
Ran 159 tests in 6.073s

OK (skipped=8)
```

| File | Tests |
| --- | ---: |
| `test_cli.py` | 9 |
| `test_dds.py` | 17 |
| `test_installer.py` | 11 |
| `test_layout.py` | 11 |
| `test_pack.py` | 21 |
| `test_plan.py` | 24 |
| `test_rcf.py` | 19 |
| `test_real_game.py` | 8 |
| `test_rz.py` | 7 |
| `test_tpf.py` | 12 |
| `test_encode.py` | 13 |
| `test_util.py` | 7 |
| **Total** | **159** |

`test_tpf.py` builds its own fixture: a ZipCrypto writer (test-only; `zipfile`
reads but cannot write that header) wrapped in the XOR layer, plus a plain-ZIP
wrapper case, so no retail package is redistributed. `test_encode.py` covers BMP
round-trips (bottom-up and top-down), DXT1 block/palette behaviour, mip halving,
and refusal to encode into a DXT5, uncompressed or differently sized slot.
`test_layout.py` checks that every `--game-dir` command resolves the three install
shapes (disc/JB root with `PS3_GAME/USRDIR`, package root with `USRDIR` such as
`NPUB30389`, and the `USRDIR` itself) for plan build/verify, archive scan and
install/uninstall.

Coverage includes RCF parse/build/rebuild, name-table round-trips, in-place vs
rebuild decisions, DDS validation/size math, RZ edge cases, pack manifest checks,
installer backup/rollback, plan CRC and journal restore (including newest-first
multi-journal restore and refusal to touch an archive when a journal is corrupt),
and CLI lifecycles. `test_plan.py` also covers re-anchoring: a plan built from one
archive applied to a rebuilt archive with identical texture bytes, refusal when a
target's bytes differ or the entry is absent, exact verify still rejecting the
other build, and an apply/restore cycle proving the journal uses the real offsets.
A separate case does the same for a texture embedded in an RZ-compressed P3D.

### 7.2 Self-test

```
python -m ps3texmod selftest --game-dir "Prototype (USA, Korea) (En,Fr)"
44 passed, 0 failed
```

Includes synthetic end-to-end mod install/uninstall, noisy/incompressible
replacement forcing a rebuild, and real `scripts.rcf` checks (entry count, English
credits offset/size, `init.lua` hash, deep validation).

### 7.3 Real retail-file E2E

```
python tests/real_e2e.py --workdir %TEMP%\ps3texmod-real-e2e
35 passed, 0 failed in 112.98s
```

The test copies retail `art.rcf` to a temp directory and never writes the original.
It performs:

1. In-place patch of standalone billboard `94CCC0CC` (699,192 bytes), verifying the
   archive still deeply validates, exactly one payload changed, the index size field
   was updated, and the archive size is unchanged.
2. Embedded P3D texture replacement that grows past its slot, forcing a full rebuild;
   the rebuilt archive is revalidated, the P3D payload is exactly as intended, the
   earlier standalone patch survives, all other payloads are byte identical, the
   index is still hash-sorted, and the name table still maps correctly.
3. Real pack install → disable → enable → remove, checking byte-exact restores and
   state/backup hashes.

Exact figures from the run are in `artifacts/real_e2e_report.json`; console output is
in `artifacts/real_e2e_run.log`. Timings: forced rebuild 4.21 s, pack install 16.54 s,
whole run 112.98 s (dominated by hashing the 476 MB archive and deep validation).

### 7.4 Build artifact verification

```
python tools/verify_ps3_build.py
74 passed, 0 failed
```

This is a separate Python implementation, not a wrapper around `pkg`, `readelf`
or `sfo`. It checks:

* ELF64 big-endian PowerPC64, OS/ABI `0x66`, entry `0x10000000`, 5 LOAD segments
  wholly inside the file;
* the CEX, fake and NPDRM SELF headers (magic, version, key revision, metadata
  offset, embedded stripped-ELF size);
* the debug package header, 0x60-byte footer, debug decryption, 4-entry item
  table, types and file matches, and the 16-byte EBOOT padding;
* the little-endian SFO (12 fields) against the expected title metadata;
* the finalized package flag, type and SHA-1 trailer.

The machine-readable result is `artifacts/ps3_build_verify.json`. This proves
file structure only; it cannot prove that a console will accept the package.

---

## 8. PS3 homebrew source

`homebrew/` contains a PSL1GHT C application (`PS3TEXMD1`, category `HG`):

* discovery of JB-folder and package installs on HDD/USB, plan discovery under
  `ps3texmod/plans`, controller UI (X exact verify + apply, Triangle verify,
  L1 re-anchor + apply, Square restore, Select rescan, Circle back, Start exit);
* two-pass verification (all patches checked before any write), an alternate
  re-anchor mode that resolves targets by entry hash and requires their stored
  bytes to match, journaled apply, multi-journal restore newest-first;
* direct CPU rendering into the RSX display buffer with a generated 3x5 font — no
  Cg shaders, no external data files except the icon;
* `Makefile`, `build.sh`, `build.ps1`, `pkg/sfo.xml`, `pkg/ICON0.PNG`.

The PSL1GHT APIs used were checked against the upstream headers (`rsxInit`,
`rsxFinish`, `rsxSetWriteBackendLabel`, `rsxSetWaitLabel`, `gcmSetDisplayBuffer`,
`gcmSetFlip`, `sysLv2Fs*`, `ioPad*`, `sysUtil*`, `videoConfigure`). Static review
found and fixed several issues (missing includes, a plan-name offset bug, journal
indexing, flip-status reset, a stray `SEEK_SET`).

### Build result

Compiled natively on Windows with PS3DK v0.26.0:

| Stage | Tool | Result |
| --- | --- | --- |
| compile 10 sources | `powerpc64-ps3-elf-gcc` 16.1.0 | 0 errors, 12 warnings |
| link | same, `-lrsx -lgcm_sys -lio -lsysutil -lrt -llv2 -lm` | `ps3texmod.elf` |
| ELF check | `readelf` | ELF64, big-endian, PowerPC64, entry `0x10000000` |
| strip / sprxlink | `powerpc64-ps3-elf-strip`, `sprxlinker` | ok |
| sign | `make_self`, `fself` | `SCE\0` SELF, versions `0x00010001` / `0x80000001` |
| package | `make_self_npdrm`, `sfo`, `pkg`, `package_finalize` | `ps3texmod.pkg`, `ps3texmod.gnpdrm.pkg` |

Section sizes: text 356,566 / data 17,260 / bss 152,552. The 12 warnings are 10
`-Wformat-truncation` on fixed-size path buffers and 2 deprecation warnings for
PS3DK's PSL1GHT-compat `sysUtil*` shims; none is fatal.

The build uses `homebrew/build.ps1`, which performs the `ppu_rules` stages
directly because the Make recipes require a POSIX shell. `Makefile`/`build.sh`
remain for Linux/PSL1GHT hosts. `make_self` is not deterministic: identical
inputs produce different SELF bytes on each invocation, so the `.self`/`.pkg`
hashes change on rebuild (the ELF and fake-signed SELF are reproducible).

---

## 9. `.pkg` status

**A `.pkg` was produced** with the supplied native-Windows PS3DK v0.26.0
toolchain (GCC 16.1.0). Toolchain provenance:

* Release: <https://github.com/FirebirdTA01/PS3DK/releases/tag/v0.26.0>
* Asset: `ps3-sdk-v0.26.0-windows-x86_64.zip`, 1,769,700,102 bytes, SHA-256
  `bd8889455a9ee6effe87e1180484544351bcd029420fac647f9ceee4deb0eddf`
  (matches the published `.sha256`).
* Local extract: `toolchain/ps3dk/ps3-sdk-v0.26.0-windows-x86_64`, without the
  `spu/` and `samples/` trees. Only the cross compiler (`ppu/`) and host tools
  (`bin/`) are used.

Artifacts in `homebrew/`:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `ps3texmod.elf` | 1,318,752 | `8f0feae86062050000199c512da0c293a644e2a580b66f9944b54e51d6fe8bac` |
| `ps3texmod.self` | 154,272 | `bf2d41363349df0ccb3a6ea5110b9d5dc7e3645949544a354d84d7a5e4a61772` |
| `ps3texmod.fake.self` | 531,392 | `1d0cd4a0e054c652e8b11a7a9bd9a2011548b098062850d036c239f0a4313d9f` |
| `ps3texmod.pkg` | 158,592 | `6a0d9fc68f741b33b1d2958d60c51a3c1c8086a2b74855fb25bf5fb82c0b9332` |
| `ps3texmod.gnpdrm.pkg` | 158,592 | `5a7fada18a213134c45a4e07e451d8bcdc2ea6b84bab376c3863360408142725` |

Package verification:

* `pkg -l homebrew/ps3texmod.pkg` reports `Type 00000001`, ContentID
  `UP0001-PS3TEXMD1_00-0000000000000000`, QA digest
  `8E53A05D3B7A7FCBEEC0997B25F25F4`, K Licensee all-zero, and four items:
  `ICON0.PNG` (2,527), `PARAM.SFO` (1,040), `USRDIR/`, `USRDIR/EBOOT.BIN`
  (154,416, NPDRM SELF).
* `sfo inspect` on the generated `PARAM.SFO`: `TITLE_ID=PS3TEXMD1`,
  `TITLE=ps3texmod - Prototype texture installer`, `CATEGORY=HG`,
  `APP_VER=01.00`, `PS3_SYSTEM_VER=03.5500`, `BOOTABLE=1`,
  `RESOLUTION=63`, `SOUND_FORMAT=279`, `PARENTAL_LEVEL=0`.
* `ps3texmod.gnpdrm.pkg` is the copy passed through `package_finalize`
  (geohot's tool, source in `toolchain/ps3tools-master/package_finalize.c`):
  the data section is re-encrypted with the retail package key and the byte at
  offset 4 is set to `0x80`. `pkg -l` then reports `Type 80000001` and
  `Unsupported Type`, which is expected for a finalized package. The file size is
  unchanged (158,592) and the trailing SHA-1 is valid: the 20 bytes at
  `len-0x20` equal `SHA1(file[0:len-0x20])`, verified independently with Python.
* Independently of `pkg`, `tools/verify_ps3_build.py` decrypted the debug
  package with the published debug-package scheme and re-parsed it: header
  (data at 0x140, 158,176 bytes, then the 0x60-byte zero footer), item table
  (4 entries, names at 0x80, data at 0xC0), and the little-endian 12-field SFO.
  It also parses the ELF program headers (5 LOAD segments), all three SELF
  headers, and confirms that the packaged `ICON0.PNG`, `PARAM.SFO` and
  `EBOOT.BIN` match the build tree (the stored EBOOT has 16 zero pad bytes).
  Result: **74 passed, 0 failed**; report `artifacts/ps3_build_verify.json`.
* `tools/make_release.py` assembles `homebrew/ps3texmod-1.0-ps3.zip` (1,022,849
  bytes, SHA-256 `e23f634fbbca546c1ff56f8343c7abc62a3ab9a426760a07a3a3acad15911a15`),
  re-reads the ZIP, and verifies all 8 member hashes plus the `SHA256SUMS.txt`
  manifest. It contains both packages, `folder-install/` (`PARAM.SFO`,
  `ICON0.PNG`, `USRDIR/EBOOT.BIN`), the sample plan, `INSTALL.txt` and the
  checksums.

What is **not** verified:

* Installation on a console. The finalized package is the intended CFW
  deliverable, and `ps3texmod.fake.self` / a JB-folder `USRDIR/EBOOT.BIN` are
  alternatives, but no installer or firmware was available to try any of them.
* Signing validity beyond the format: `make_self`/`fself` produced files with
  the expected `SCE\0` header and CEX/fake version fields, but only a console can
  accept or reject them.
* Byte reproducibility. `make_self` changes its output on every invocation even
  with identical input, so the SELF and package hashes above are a snapshot.
  Rebuild and re-verify the package with `pkg -l`; do not compare hashes.

---

## 10. Was it tested on a PS3?

**No.** There is no PS3 hardware or CFW environment available here. The homebrew
compiles, links, signs and packages; nothing ran on a console. Consequences:

* File-level PC behavior is tested against real retail data.
* Console plan/journal formats are tested on the PC and cross-checked against the C
  layout, but no console read/write, UI, pad input, USB/HDD discovery, or game
  loading was observed.
* The package was never installed, so no CFW installer or firmware acceptance is
  demonstrated.
* No claim is made about which CFW versions work, whether the game accepts modified
  archives from every install layout, or how the game behaves if an asset is cached.

---

## 11. Risks and limits

* **Large writes.** `art.rcf` is 476 MB. The PC installer writes it in place; the
  console plan writer does too. Always keep a pristine copy of the game.
* **Build-specific plans.** Exact verify pins the archive size, index layout,
  entry positions and CRCs, so a different regional/build version is rejected,
  not guessed. `--relocate` (PC) / `L1` (console) can re-anchor a plan onto a
  different build only when every target's stored bytes still match the plan;
  otherwise rebuild from that archive. There is no path that writes at
  plan-recorded offsets without checking them.
* **Console slot limit.** The C installer caps a single slot at 64 MiB (largest
  observed slot in `art.rcf`: 16,125,952 bytes). Larger slots would need a code
  change.
* **Journal integrity is CRC-32**, not a signature. It detects corruption, not
  tampering.
* **DDS authoring constraints.** No automatic format conversion; replacements must
  match dimensions, format and mips. This is by design to keep chunk sizes valid.
* **Texmod ports are layout-blind.** The `.tpf` image becomes the PS3 texture
  verbatim, so UV islands may not line up with the console mesh. Target choice is
  documented but the visual outcome is unverified on hardware.
* **No console test** — the main remaining gap. The package exists but has never
  been installed, and no on-device behavior has been observed.

---

## 12. Reproduction

```sh
# 1. Tests (no game needed)
python -m unittest discover -s tests -t .

# 2. Self-test (synthetic + real scripts.rcf when --game-dir is given)
python -m ps3texmod selftest --game-dir "$GAME"

# 3. Real E2E on a throw-away copy of retail art.rcf
python tests/real_e2e.py --workdir %TEMP%\ps3texmod-real-e2e

# 4. Rebuild the included sample pack/plan
python tools/make_sample_pack.py --game-dir "$GAME"

# 5. Verify the sample plan against the retail archive
python -m ps3texmod plan-verify samples/prototype-billboard-tint.ps3patch \
    --game-dir "$GAME"

# 5b. Check a different build without writing (re-anchor)
python -m ps3texmod plan-verify artifacts/texmod_samfisher/samfisher.ps3patch \
    --game-dir "$GAME" --relocate

# 6. Convert a Texmod package and rebuild its plan (needs the package + retail dump)
python -m ps3texmod tpf "Sam Fisher (Blackwatch Commando)-3-Latest-1579801131.zip" \
    -o artifacts/texmod_samfisher
python -m ps3texmod encode \
    --template work/soldier/3ED441FA_000DCB50_missle001_diffuse.dds \
    --image artifacts/texmod_samfisher/ORIGINALPROTOTYPEF.EXE_0x35931490.bmp \
    --out artifacts/texmod_samfisher/samfisher_3ED441FA_000DCB50.dds
python -m ps3texmod pack-build --game-dir "$GAME" \
    --def artifacts/texmod_samfisher/samfisher.def \
    --out artifacts/texmod_samfisher/samfisher.ps3pack \
    --name "Sam Fisher (Blackwatch Commando)"
python -m ps3texmod plan-build --pack artifacts/texmod_samfisher/samfisher.ps3pack \
    --game-dir "$GAME" --out artifacts/texmod_samfisher/samfisher.ps3patch

# 7. Console build: ELF, SELF, fake SELF and .pkg (needs PS3DK; not a Python test)
powershell -ExecutionPolicy Bypass -File homebrew\build.ps1
```

---

## 13. File inventory (source, excluding `__pycache__`)

```
README.md                     this report's companion / user guide
REPORT.md                     this document
ps3texmod/                    14 modules, 3,859 lines
tests/                        helpers + 12 test modules + real_e2e.py
homebrew/                     C sources, headers, Makefile, build scripts,
                              pkg assets, and the built .elf/.self/.pkg files
samples/                      retail billboard dumps, example pack, example plan
tools/                        gen_font.py, make_icon.py, make_sample_pack.py,
                              analysis/ (format investigation scripts)
artifacts/                    real_e2e_report.json, real_e2e_run.log,
                              independent decode/layout checks
```

The retail game directory is **not** part of the deliverable and is only read by
the tests when explicitly pointed at.
