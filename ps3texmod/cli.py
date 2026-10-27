"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__
from .catalog import (DEFAULT_ARCHIVES, build_catalog, extract_texture,
                      find_archives, save_texture, scan_archive)
from .dds import dds_to_png, parse_dds
from .installer import (InstallError, install, load_state, remove, restore_base,
                        set_enabled, status, uninstall_all, usrdir)
from .pack import (PackError, build_pack, load_pack, pack_info, parse_definition,
                   resolve_token, validate_against_game)
from .plan import (PlanError, apply_plan, find_journals, journal_info,
                   journal_scan, load_plan, plan_from_pack, restore_journal,
                   restore_journals, verify_plan)
from .rcf import RcfArchive, RcfError
from .util import Log, fmt_hash, game_usrdir, human_size, resolve_archive
from .rz import is_rz, rz_info, unpack_rz


def _log(args) -> Log:
    return Log(path=getattr(args, "log", None))


def _open(path: str) -> RcfArchive:
    try:
        return RcfArchive(path)
    except RcfError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# archive commands
# ---------------------------------------------------------------------------

def cmd_info(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    log(f"archive: {ar.path}")
    log(f"  size          {human_size(ar.file_size)} ({ar.file_size} bytes)")
    log(f"  version       0x{ar.info.version:08X}")
    log(f"  entries       {ar.info.count}")
    log(f"  index         offset=0x{ar.info.index_offset:X} size={ar.info.index_size}")
    log(f"  name table    offset=0x{ar.info.table_offset:X} size={ar.info.table_size}")
    log(f"  data start    0x{ar.info.data_start:X}")
    log(f"  names mapped  {ar.info.names_ok}")
    st = ar.stats()
    log(f"  kinds         {st['kinds']}")
    first = ar._layout[0] if ar._layout else None
    if first:
        log(f"  first asset   {first.name or fmt_hash(first.hash)} @0x{first.offset:X}")
        log(f"  last asset    {ar._layout[-1].name or fmt_hash(ar._layout[-1].hash)} "
            f"@0x{ar._layout[-1].offset:X}")
    return 0


def cmd_list(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    want = args.kind.lower().split(",") if args.kind else None
    pat = (args.filter or "").lower()
    n = 0
    for e in ar.entries:
        kind = ar.kind(e)
        if want and kind not in want:
            continue
        if pat and pat not in (e.name or "").lower():
            continue
        log(f"{fmt_hash(e.hash)}  {kind:4}  {e.size:>10}  {e.name}")
        n += 1
        if args.limit and n >= args.limit:
            break
    log(f"-- {n} entr{'y' if n == 1 else 'ies'}")
    return 0


def cmd_stats(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    log(json.dumps(ar.stats(), indent=2))
    return 0


def cmd_verify(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    res = ar.validate(deep=args.deep, limit=args.limit)
    log(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


def cmd_extract(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    e = ar.entry(args.target)
    payload = ar.read_payload(e)
    out = args.out or (os.path.basename(e.name) or fmt_hash(e.hash))
    if args.raw:
        data = payload
    else:
        data = payload
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)
    log(f"wrote {out} ({human_size(len(data))})")
    if args.png:
        try:
            info = parse_dds(data)
            with open(os.path.splitext(out)[0] + ".png", "wb") as f:
                f.write(dds_to_png(data, info))
            log(f"wrote {os.path.splitext(out)[0]}.png ({info.summary()})")
        except Exception as exc:
            log(f"png preview failed: {exc}")
    return 0


def cmd_extract_all(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    want = args.kind.lower().split(",") if args.kind else None
    os.makedirs(args.out, exist_ok=True)
    n = 0
    bytes_out = 0
    for e in ar.entries:
        kind = ar.kind(e)
        if want and kind not in want:
            continue
        data = ar.read_payload(e)
        rel = (e.name or f"{fmt_hash(e.hash)}.{kind}").replace("\\", "/").lstrip("/")
        path = os.path.join(args.out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        n += 1
        bytes_out += len(data)
        if args.limit and n >= args.limit:
            break
    log(f"extracted {n} asset(s), {human_size(bytes_out)} -> {args.out}")
    return 0


# ---------------------------------------------------------------------------
# texture commands
# ---------------------------------------------------------------------------

def cmd_textures(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    def prog(i, total, e):
        if i == total or i % 100 == 0:
            print(f"\r  scanning {i}/{total}", end="", file=sys.stderr, flush=True)
    recs = scan_archive(ar, progress=prog if not args.quiet else None)
    print("\r", end="", file=sys.stderr)
    if args.filter:
        f = args.filter.lower()
        recs = [r for r in recs if f in r["name"].lower() or f in r["entry_name"].lower()]
    for r in recs[: args.limit or None]:
        log(f"{r['id']:34} {r['width']}x{r['height']:<5} {r['format']:<8} "
            f"mips={r['mipmaps']:<2} {human_size(r['size']):>10}  {r['name']}")
    log(f"-- {len(recs)} texture(s)")
    return 0


def cmd_dump(args) -> int:
    log = _log(args)
    ar = _open(args.archive)
    os.makedirs(args.out, exist_ok=True)

    def prog(i, total, e):
        if i == total or i % 100 == 0:
            print(f"\r  scanning {i}/{total}", end="", file=sys.stderr, flush=True)
    recs = scan_archive(ar, progress=None if args.quiet else prog)
    print("\r", end="", file=sys.stderr)
    if args.filter:
        f = args.filter.lower()
        recs = [r for r in recs if f in r["name"].lower() or f in r["entry_name"].lower()]
    if args.limit:
        recs = recs[: args.limit]
    log(f"dumping {len(recs)} texture(s) to {args.out}")
    def_lines = []
    manifest = []
    for i, r in enumerate(recs, 1):
        try:
            res = save_texture(ar, r, args.out, png=not args.no_png)
        except Exception as exc:
            log(f"  ! {r['id']}: {exc}")
            continue
        manifest.append({**r, "files": res})
        def_lines.append(f"# {r['id']}|{res['dds']}")
        if i % 25 == 0 or i == len(recs):
            log(f"  {i}/{len(recs)}")
    with open(os.path.join(args.out, "textures.json"), "w", encoding="utf-8") as f:
        json.dump({"archive": ar.name, "textures": manifest}, f, indent=2)
    with open(os.path.join(args.out, "def.template"), "w", encoding="utf-8") as f:
        f.write("# ps3texmod definition file\n")
        f.write("# Uncomment a line and point it at your edited DDS, then run:\n")
        f.write("#   ps3texmod pack-build --game-dir <GAME> --def def.txt --out pack.zip --name ...\n")
        f.write("# <texture id>|<edited dds>\n")
        f.write("\n".join(def_lines) + "\n")
    log(f"wrote {os.path.join(args.out, 'textures.json')} and def.template")
    return 0


def cmd_texture_info(args) -> int:
    log = _log(args)
    with open(args.file, "rb") as f:
        raw = f.read()
    info = parse_dds(raw)
    d = info.to_dict()
    d["file"] = args.file
    d["file_size"] = len(raw)
    log(json.dumps(d, indent=2))
    if args.png:
        out = os.path.splitext(args.file)[0] + ".png"
        with open(out, "wb") as f:
            f.write(dds_to_png(raw, info))
        log(f"wrote {out}")
    return 0


def cmd_tpf(args) -> int:
    log = _log(args)
    from .tpf import TpfError, extract, list_tpf
    try:
        if args.list:
            items = list_tpf(args.file)
            for it in items:
                log(f"  {it['name']:<52} {it['size']:>9}  "
                    f"enc={it['encrypted']}  crc={it['crc32']}")
            log(f"-- {len(items)} member(s)")
            return 0
        out = args.out or (os.path.splitext(os.path.basename(args.file))[0]
                           + "_tpf")
        result = extract(args.file, out, log=log)
    except (TpfError, OSError) as exc:
        log(f"error: {exc}")
        return 1
    if args.json:
        log(json.dumps(result, indent=2))
    for d in result["definitions"]:
        log(f"  definition {d['hash']} -> {d['image']}")
    log(f"extracted {len(result['files'])} file(s) to {result['out_dir']}")
    return 0


def cmd_encode(args) -> int:
    log = _log(args)
    from .encode import EncodeError, encode_for_template
    try:
        result = encode_for_template(args.image, args.template, args.out)
    except (EncodeError, OSError) as exc:
        log(f"error: {exc}")
        return 1
    log(f"encoded {result['width']}x{result['height']} {result['format']} "
        f"mips={result['mipmaps']} -> {result['size']} bytes")
    log(f"  {result['out']}")
    log(f"  sha256 {result['sha256']}")
    return 0


def cmd_catalog(args) -> int:
    log = _log(args)
    archives = args.archives.split(",") if args.archives else list(DEFAULT_ARCHIVES)
    t0 = time.time()
    def prog(kind, i, total, e=None):
        if kind == "archive":
            print(f"\n[{i}/{total}] {e}")
        else:
            print(f"\r  {i}/{total}", end="", file=sys.stderr, flush=True)
    cat = build_catalog(args.game_dir, archives=archives, progress=None if args.quiet else prog)
    print("\r", end="", file=sys.stderr)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(cat, f, indent=2)
    log(f"catalog: {len(cat['textures'])} texture(s) from {len(cat['archives'])} archive(s) "
        f"in {time.time() - t0:.1f}s -> {args.out}")
    return 0


# ---------------------------------------------------------------------------
# pack commands
# ---------------------------------------------------------------------------

def cmd_pack_build(args) -> int:
    log = _log(args)
    from .pack import _open_archives
    archives = _open_archives(args.game_dir)
    defs = parse_definition(args.definition)
    reps = []
    for ident, filename in defs:
        if not os.path.isfile(filename):
            raise SystemExit(f"error: replacement file not found: {filename}")
        try:
            rec = resolve_token(archives, ident, archive_hint=args.archive)
        except (PackError, KeyError) as exc:
            raise SystemExit(f"error: {ident}: {exc}")
        rec["source_file"] = filename
        reps.append(rec)
        log(f"resolved {ident} -> {rec['archive']}:{rec['entry_hash']}:"
            f"{rec['offset_in_asset']:08X} ({rec['width']}x{rec['height']} {rec['format']})")
    manifest = build_pack(
        args.out, reps,
        name=args.name, version=args.version, author=args.author or "",
        description=args.description or "", title_id=args.title_id,
        game_dir=args.game_dir, pack_id=args.id, log=log,
    )
    log(f"built {args.out}: {manifest['replacement_count']} replacement(s), "
        f"{human_size(os.path.getsize(args.out))}")
    return 0


def cmd_pack_info(args) -> int:
    log = _log(args)
    try:
        info = pack_info(args.pack)
    except PackError as exc:
        raise SystemExit(f"error: {exc}")
    log(json.dumps(info, indent=2))
    return 0


def cmd_pack_validate(args) -> int:
    log = _log(args)
    try:
        manifest, raw = load_pack(args.pack)
    except PackError as exc:
        log(f"INVALID: {exc}")
        return 1
    log(f"pack OK: {manifest['name']} ({manifest['id']}) v{manifest['version']}")
    log(f"  replacements: {manifest['replacement_count']}")
    log(f"  archives: {', '.join(manifest['game'].get('archives', []))}")
    rc = 0
    if args.game_dir:
        res = validate_against_game(args.pack, args.game_dir, log=log)
        log(json.dumps({k: res[k] for k in ("ok", "problems", "checked")}, indent=2))
        rc = 0 if res["ok"] else 1
    return rc


# ---------------------------------------------------------------------------
# install commands
# ---------------------------------------------------------------------------

def cmd_install(args) -> int:
    log = _log(args)
    try:
        res = install(args.pack, args.game_dir, enable=not args.no_enable, log=log)
    except (InstallError, PackError) as exc:
        log(f"error: {exc}")
        return 1
    log(json.dumps(res, indent=2))
    return 0


def cmd_enable(args) -> int:
    log = _log(args)
    try:
        set_enabled(args.game_dir, args.pack_id, True, log=log)
    except InstallError as exc:
        log(f"error: {exc}")
        return 1
    return 0


def cmd_disable(args) -> int:
    log = _log(args)
    try:
        set_enabled(args.game_dir, args.pack_id, False, log=log)
    except InstallError as exc:
        log(f"error: {exc}")
        return 1
    return 0


def cmd_remove(args) -> int:
    log = _log(args)
    try:
        remove(args.game_dir, args.pack_id, purge=not args.keep_files, log=log)
    except InstallError as exc:
        log(f"error: {exc}")
        return 1
    return 0


def cmd_installed(args) -> int:
    log = _log(args)
    log(json.dumps(status(args.game_dir), indent=2))
    return 0


def cmd_restore(args) -> int:
    log = _log(args)
    try:
        uninstall_all(args.game_dir, log=log)
    except InstallError as exc:
        log(f"error: {exc}")
        return 1
    log("all archives restored from pristine backups")
    return 0


def cmd_selftest(args) -> int:
    from .selftest import run_selftest
    return run_selftest(args.game_dir, verbose=not args.quiet)


# ---------------------------------------------------------------------------
# console plan / journal commands
# ---------------------------------------------------------------------------

def _resolve_archive(archive_name: str, args) -> str:
    """Resolve an archive name from --archive or --game-dir."""
    if getattr(args, "archive", None):
        return args.archive
    game_dir = getattr(args, "game_dir", None)
    if not game_dir:
        raise PlanError("give either --archive <rcf> or --game-dir <game>")
    path = resolve_archive(game_dir, archive_name)
    if path is None:
        raise PlanError(f"{archive_name} not found in {game_usrdir(game_dir)}")
    return path


def _plan_archive(plan, args) -> str:
    """Resolve the archive a plan targets from --archive or --game-dir."""
    return _resolve_archive(plan.archive, args)


def cmd_plan_build(args) -> int:
    log = _log(args)
    plan = plan_from_pack(args.pack, args.game_dir, args.out,
                          name=args.name, log=log)
    log(f"plan {os.path.basename(args.out)}: {len(plan.patches)} patch(es), "
        f"{human_size(os.path.getsize(args.out))}, archive {plan.archive}")
    return 0


def cmd_plan_info(args) -> int:
    log = _log(args)
    plan = load_plan(args.plan)
    log(json.dumps(plan.to_dict(), indent=2))
    return 0


def cmd_plan_verify(args) -> int:
    log = _log(args)
    plan = load_plan(args.plan)
    res = verify_plan(plan, _plan_archive(plan, args), relocate=args.relocate)
    log(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


def cmd_plan_apply(args) -> int:
    log = _log(args)
    plan = load_plan(args.plan)
    archive = _plan_archive(plan, args)
    res = apply_plan(plan, archive, journal_dir=args.journal_dir,
                     dry_run=args.dry_run, relocate=args.relocate, log=log)
    log(json.dumps(res, indent=2))
    return 0


def cmd_plan_restore(args) -> int:
    log = _log(args)
    if args.journal:
        journal = args.journal
        if not os.path.isfile(journal):
            raise PlanError(f"journal not found: {journal}")
        archive = args.archive
        if not archive:
            info = journal_info(journal)
            if not args.game_dir:
                raise PlanError("give --archive <rcf> or --game-dir <game>")
            archive = _resolve_archive(info["archive"], args)
        res = restore_journal(journal, archive, log=log)
    else:
        if not args.plan:
            raise PlanError("give --journal <file> or --plan <plan> --journal-dir <dir>")
        plan = load_plan(args.plan)
        archive = _plan_archive(plan, args)
        res = restore_journals(args.journal_dir, archive, log=log)
    log(json.dumps(res, indent=2))
    return 0


def cmd_plan_status(args) -> int:
    log = _log(args)
    plans = []
    if os.path.isdir(args.plan_dir):
        plans = sorted(os.path.join(args.plan_dir, n) for n in os.listdir(args.plan_dir)
                       if n.lower().endswith(".ps3patch"))
    journals = journal_scan(args.journal_dir)
    out = {"plans": [], "journals": []}
    for p in plans:
        try:
            plan = load_plan(p)
            out["plans"].append({"path": p, "name": plan.name,
                                 "archive": plan.archive,
                                 "patches": len(plan.patches)})
        except PlanError as exc:
            out["plans"].append({"path": p, "error": str(exc)})
    out["journals"] = journals
    log(json.dumps(out, indent=2))
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ps3texmod",
        description="Texture modding toolkit for Prototype (PS3) RCF/Cement archives.",
    )
    p.add_argument("--version", action="version", version=f"ps3texmod {__version__}")
    p.add_argument("--log", help="also write output to this log file")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, func, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(func=func)
        return sp

    sp = add("info", cmd_info, "show archive header/structure")
    sp.add_argument("archive")

    sp = add("list", cmd_list, "list archive entries")
    sp.add_argument("archive")
    sp.add_argument("--kind", help="filter by kind (dds,p3d,gfx,bik,png)")
    sp.add_argument("--filter", help="substring match on name")
    sp.add_argument("--limit", type=int)

    sp = add("stats", cmd_stats, "archive statistics")
    sp.add_argument("archive")

    sp = add("verify", cmd_verify, "validate archive structure")
    sp.add_argument("archive")
    sp.add_argument("--deep", action="store_true", help="check payload signatures")
    sp.add_argument("--limit", type=int, help="max entries for deep check")

    sp = add("extract", cmd_extract, "extract one asset (decompressed)")
    sp.add_argument("archive")
    sp.add_argument("target", help="name, 8-hex hash, or suffix")
    sp.add_argument("-o", "--out")
    sp.add_argument("--png", action="store_true", help="also write a PNG preview")
    sp.add_argument("--raw", action="store_true", help="keep RZ/stored bytes")

    sp = add("extract-all", cmd_extract_all, "extract every asset")
    sp.add_argument("archive")
    sp.add_argument("-o", "--out", required=True)
    sp.add_argument("--kind")
    sp.add_argument("--limit", type=int)

    sp = add("textures", cmd_textures, "list textures (DDS + embedded in P3D)")
    sp.add_argument("archive")
    sp.add_argument("--filter")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--quiet", action="store_true")

    sp = add("dump", cmd_dump, "dump textures as DDS/PNG + def template")
    sp.add_argument("archive")
    sp.add_argument("-o", "--out", required=True)
    sp.add_argument("--filter")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--no-png", action="store_true")
    sp.add_argument("--quiet", action="store_true")

    sp = add("texture-info", cmd_texture_info, "inspect a DDS file")
    sp.add_argument("file")
    sp.add_argument("--png", action="store_true")

    sp = add("tpf", cmd_tpf, "list/extract a Texmod .tpf package")
    sp.add_argument("file")
    sp.add_argument("-o", "--out", help="output directory")
    sp.add_argument("--list", action="store_true", help="list members only")
    sp.add_argument("--json", action="store_true", help="print a JSON summary")

    sp = add("encode", cmd_encode, "encode an image into a DDS texture slot")
    sp.add_argument("--template", required=True,
                    help="original retail DDS to match (dims/format/mips)")
    sp.add_argument("--image", required=True, help="source BMP (24/32-bit)")
    sp.add_argument("--out", required=True, help="output DDS path")

    sp = add("catalog", cmd_catalog, "scan archives and write catalog.json")
    sp.add_argument("game_dir")
    sp.add_argument("-o", "--out", default="catalog.json")
    sp.add_argument("--archives", help=f"comma separated (default {','.join(DEFAULT_ARCHIVES)})")
    sp.add_argument("--quiet", action="store_true")

    sp = add("pack-build", cmd_pack_build, "build a mod pack from a definition file")
    sp.add_argument("--game-dir", required=True)
    sp.add_argument("--def", dest="definition", required=True, help="<texture id>|<dds file> lines")
    sp.add_argument("--out", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--version", default="1.0.0")
    sp.add_argument("--author")
    sp.add_argument("--description")
    sp.add_argument("--title-id")
    sp.add_argument("--id")
    sp.add_argument("--archive", help="default archive for bare hashes")

    sp = add("pack-info", cmd_pack_info, "show pack contents")
    sp.add_argument("pack")

    sp = add("pack-validate", cmd_pack_validate, "validate pack integrity")
    sp.add_argument("pack")
    sp.add_argument("--game-dir")

    sp = add("install", cmd_install, "install a pack")
    sp.add_argument("pack")
    sp.add_argument("--game-dir", required=True)
    sp.add_argument("--no-enable", action="store_true")

    sp = add("enable", cmd_enable, "enable an installed pack")
    sp.add_argument("pack_id")
    sp.add_argument("--game-dir", required=True)

    sp = add("disable", cmd_disable, "disable an installed pack")
    sp.add_argument("pack_id")
    sp.add_argument("--game-dir", required=True)

    sp = add("remove", cmd_remove, "remove an installed pack")
    sp.add_argument("pack_id")
    sp.add_argument("--game-dir", required=True)
    sp.add_argument("--keep-files", action="store_true")

    sp = add("installed", cmd_installed, "show install state and backups")
    sp.add_argument("--game-dir", required=True)

    sp = add("restore", cmd_restore, "restore all pristine archive backups")
    sp.add_argument("--game-dir", required=True)

    sp = add("selftest", cmd_selftest, "run the built-in end-to-end self test")
    sp.add_argument("--game-dir")
    sp.add_argument("--quiet", action="store_true")

    # -- console plans ------------------------------------------------------
    sp = add("plan-build", cmd_plan_build, "build a .ps3patch plan for the PS3 installer")
    sp.add_argument("--pack", required=True)
    sp.add_argument("--game-dir", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--name")

    sp = add("plan-info", cmd_plan_info, "show plan header and patches")
    sp.add_argument("plan")

    sp = add("plan-verify", cmd_plan_verify,
             "check a plan against an archive (no writes)")
    sp.add_argument("plan")
    sp.add_argument("--archive", help="path to the target .rcf")
    sp.add_argument("--game-dir", help="game root, archive found by name")
    sp.add_argument("--relocate", action="store_true",
                    help="accept a different build of the archive when every "
                         "target's stored bytes still match (re-anchor)")

    sp = add("plan-apply", cmd_plan_apply,
             "apply a plan with a rollback journal")
    sp.add_argument("plan")
    sp.add_argument("--archive")
    sp.add_argument("--game-dir")
    sp.add_argument("--journal-dir", default="backups")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--relocate", action="store_true",
                    help="accept a different build of the archive when every "
                         "target's stored bytes still match (re-anchor)")

    sp = add("plan-restore", cmd_plan_restore,
             "restore an archive from a plan journal")
    sp.add_argument("--journal", help="explicit .journal file")
    sp.add_argument("--plan", help="plan whose journal should be restored")
    sp.add_argument("--journal-dir", default="backups")
    sp.add_argument("--archive")
    sp.add_argument("--game-dir")

    sp = add("plan-status", cmd_plan_status,
             "list plans and pending rollback journals")
    sp.add_argument("--plan-dir", default=".")
    sp.add_argument("--journal-dir", default="backups")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RcfError, PackError, InstallError, PlanError, ValueError,
            FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
