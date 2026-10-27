"""Install / enable / disable / remove mod packs, with verified backups.

Design
------
* ``<game>/.ps3texmod/`` holds state, pristine archive backups and pack copies.
* The first time an archive is modified a byte-exact ``.base`` backup is taken.
* Applying mods is *declarative*: the archive is restored from its base backup
  and every enabled pack is re-applied in list order (later packs win on
  conflicts).  This makes enable/disable/remove idempotent and keeps stacking
  deterministic.
* Every patch is verified against the texture's pristine sha256 before the
  first pack touches it, so a wrong game build is detected instead of silently
  corrupting the archive.
"""

from __future__ import annotations

import copy
import datetime
import os
import shutil

from .catalog import extract_texture, make_patch
from .pack import load_pack
from .rcf import RcfArchive
from .util import atomic_write_json, game_usrdir, read_json, sha256_file, sha256_bytes

STATE_FORMAT = "ps3texmod-state/1"
MOD_DIR = ".ps3texmod"


class InstallError(RuntimeError):
    pass


def _mod_root(game_dir: str) -> str:
    return os.path.join(os.path.abspath(game_dir), MOD_DIR)


def _paths(game_dir: str) -> dict:
    root = _mod_root(game_dir)
    return {
        "root": root,
        "state": os.path.join(root, "state.json"),
        "backups": os.path.join(root, "backups"),
        "packs": os.path.join(root, "packs"),
    }


def usrdir(game_dir: str) -> str:
    return game_usrdir(game_dir)


def _archive_path(game_dir: str, name: str) -> str:
    return os.path.join(usrdir(game_dir), name)


def load_state(game_dir: str) -> dict:
    p = _paths(game_dir)["state"]
    if os.path.isfile(p):
        st = read_json(p)
        if st.get("format") != STATE_FORMAT:
            raise InstallError(f"{p}: unsupported state format")
        return st
    return {"format": STATE_FORMAT, "game_dir": os.path.abspath(game_dir), "packs": []}


def save_state(game_dir: str, st: dict) -> None:
    p = _paths(game_dir)
    os.makedirs(p["root"], exist_ok=True)
    atomic_write_json(p["state"], st)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# backups
# ---------------------------------------------------------------------------

def ensure_base_backup(game_dir: str, st: dict, archive_name: str, log=print) -> str:
    p = _paths(game_dir)
    os.makedirs(p["backups"], exist_ok=True)
    base = os.path.join(p["backups"], archive_name + ".base")
    src = _archive_path(game_dir, archive_name)
    if not os.path.isfile(src):
        raise InstallError(f"archive not found: {src}")
    if not os.path.isfile(base):
        log(f"  backup {archive_name} -> {os.path.relpath(base, game_dir)}")
        shutil.copy2(src, base)
        st.setdefault("backups", {})[archive_name] = {
            "path": base,
            "sha256": sha256_file(base),
            "created_utc": _now(),
        }
        save_state(game_dir, st)
    else:
        want = st.get("backups", {}).get(archive_name, {}).get("sha256")
        if want and sha256_file(base) != want:
            raise InstallError(f"base backup for {archive_name} is corrupt (hash mismatch)")
    return base


def restore_base(game_dir: str, st: dict, archive_name: str, log=print) -> bool:
    p = _paths(game_dir)
    base = os.path.join(p["backups"], archive_name + ".base")
    dst = _archive_path(game_dir, archive_name)
    if not os.path.isfile(base):
        return False
    want = st.get("backups", {}).get(archive_name, {}).get("sha256")
    if want and sha256_file(base) != want:
        raise InstallError(f"base backup for {archive_name} is corrupt")
    if os.path.isfile(dst) and sha256_file(dst) == sha256_file(base):
        return False
    log(f"  restore {archive_name}")
    tmp = dst + ".ps3texmod-restore"
    shutil.copy2(base, tmp)
    os.replace(tmp, dst)
    return True


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------

def _apply_pack_to_archive(archive_path: str, manifest: dict, raw: dict,
                           archive_name: str, already: set, log=print) -> int:
    ar = RcfArchive(archive_path)
    patches = {}
    count = 0
    for r in manifest["replacements"]:
        if r["archive"].lower() != archive_name.lower():
            continue
        key = (archive_name.lower(), r["entry_hash"], int(r["offset_in_asset"]))
        try:
            e = ar.entry(int(r["entry_hash"], 16))
        except KeyError:
            raise InstallError(
                f"{manifest['id']}: entry {r['entry_hash']} not present in {archive_name}"
            )
        rec = {
            "id": r["texture_id"], "archive": ar.name,
            "entry_hash": r["entry_hash"], "entry_name": e.name,
            "container": r["container"], "offset_in_asset": int(r["offset_in_asset"]),
            "size": int(r["size"]),
        }
        if key not in already:
            cur = extract_texture(ar, rec)
            if sha256_bytes(cur) != r["original_sha256"]:
                raise InstallError(
                    f"{r['texture_id']}: archive does not contain the expected original "
                    "texture (wrong game version, or the archive was modded manually). "
                    "Restore the game files first."
                )
        new_blob = raw[r["file"]]
        h, stored = make_patch(ar, rec, new_blob)
        if h in patches:
            raise InstallError(f"{archive_name}: two replacements for entry {r['entry_hash']}")
        patches[h] = stored
        already.add(key)
        count += 1
    if patches:
        res = ar.apply_patches(patches)
        log(f"  {archive_name}: {count} texture(s) applied ({res['mode']})")
    return count


def _validate_targets(game_dir: str, packs: list, archive: str, log=print) -> None:
    """Check every target texture against its expected original hash.

    Runs *before* a pristine base backup is taken, so a wrong game build or a
    hand-modified archive is rejected without polluting the backup store.
    """
    ar = RcfArchive(_archive_path(game_dir, archive))
    for p in packs:
        for r in p["manifest"]["replacements"]:
            if r["archive"].lower() != archive.lower():
                continue
            try:
                e = ar.entry(int(r["entry_hash"], 16))
            except KeyError:
                raise InstallError(
                    f"{p['id']}: entry {r['entry_hash']} not present in {archive}"
                )
            rec = {
                "id": r["texture_id"], "archive": ar.name,
                "entry_hash": r["entry_hash"], "entry_name": e.name,
                "container": r["container"], "offset_in_asset": int(r["offset_in_asset"]),
                "size": int(r["size"]),
            }
            try:
                cur = extract_texture(ar, rec)
            except Exception as exc:
                raise InstallError(f"{r['texture_id']}: {exc}")
            if sha256_bytes(cur) != r["original_sha256"]:
                raise InstallError(
                    f"{r['texture_id']}: archive does not contain the expected original "
                    "texture (wrong game version, or the archive was modded manually). "
                    "Restore the game files first."
                )
    log(f"  {archive}: {sum(len(p['manifest']['replacements']) for p in packs)} "
        "target(s) verified against pristine data")


def apply_state(game_dir: str, st: dict, log=print) -> dict:
    """Materialise the enabled pack list onto disk."""
    enabled = [p for p in st["packs"] if p.get("enabled", True)]
    touched = {r["archive"] for p in enabled for r in p["manifest"]["replacements"]}
    # restore every archive that has a base backup but is not needed
    for archive in list(st.get("backups", {})):
        if archive not in touched:
            restore_base(game_dir, st, archive, log=log)
    # verify brand-new archives before taking the pristine backup
    for archive in sorted(touched):
        base = os.path.join(_paths(game_dir)["backups"], archive + ".base")
        if not os.path.isfile(base):
            _validate_targets(game_dir, enabled, archive, log=log)
    # restore the archives we are about to rebuild
    for archive in sorted(touched):
        ensure_base_backup(game_dir, st, archive, log=log)
        restore_base(game_dir, st, archive, log=log)
    already: set = set()
    total = 0
    raws: dict[str, dict] = {}
    for p in enabled:
        raw = raws.get(p["id"])
        if raw is None:
            path = p.get("pack_path") or os.path.join(_paths(game_dir)["packs"], p["id"] + ".zip")
            if not os.path.isfile(path):
                raise InstallError(f"pack file missing for {p['id']}: {path}")
            _manifest, raw = load_pack(path)
            raws[p["id"]] = raw
        for archive in sorted({r["archive"] for r in p["manifest"]["replacements"]}):
            total += _apply_pack_to_archive(
                _archive_path(game_dir, archive), p["manifest"], raw,
                archive, already, log=log,
            )
    save_state(game_dir, st)
    return {"packs": len(enabled), "textures": total}


# ---------------------------------------------------------------------------
# public operations
# ---------------------------------------------------------------------------

def _rollback(game_dir: str, st: dict, prev_packs: list, log=print) -> None:
    """Undo state changes and restore every archive that has a base backup."""
    st["packs"] = prev_packs
    save_state(game_dir, st)
    for archive in list(st.get("backups", {})):
        try:
            restore_base(game_dir, st, archive, log=log)
        except InstallError as exc:  # keep the original failure visible
            log(f"  rollback warning: {exc}")


def install(pack_path: str, game_dir: str, enable: bool = True, log=print) -> dict:
    manifest, raw = load_pack(pack_path)
    game_dir = os.path.abspath(game_dir)
    p = _paths(game_dir)
    os.makedirs(p["packs"], exist_ok=True)
    st = load_state(game_dir)
    prev = copy.deepcopy(st["packs"])
    dest = os.path.join(p["packs"], manifest["id"] + ".zip")
    existed = os.path.isfile(dest)
    shutil.copy2(pack_path, dest)
    entry = {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "enabled": bool(enable),
        "pack_path": dest,
        "installed_utc": _now(),
        "manifest": manifest,
    }
    st["packs"] = [x for x in st["packs"] if x["id"] != manifest["id"]]
    st["packs"].append(entry)
    save_state(game_dir, st)
    log(f"installed {manifest['name']} ({manifest['id']}) "
        f"with {manifest['replacement_count']} replacement(s)")
    try:
        res = apply_state(game_dir, st, log=log)
    except BaseException:
        _rollback(game_dir, st, prev, log=log)
        if not existed and os.path.isfile(dest):
            os.unlink(dest)
        raise
    return {"id": manifest["id"], "enabled": entry["enabled"], **res}


def set_enabled(game_dir: str, pack_id: str, enabled: bool, log=print) -> dict:
    st = load_state(game_dir)
    prev = copy.deepcopy(st["packs"])
    found = False
    for p in st["packs"]:
        if p["id"] == pack_id:
            p["enabled"] = enabled
            found = True
    if not found:
        raise InstallError(f"pack not installed: {pack_id}")
    save_state(game_dir, st)
    log(f"{'enabled' if enabled else 'disabled'} {pack_id}")
    try:
        return apply_state(game_dir, st, log=log)
    except BaseException:
        _rollback(game_dir, st, prev, log=log)
        raise


def remove(game_dir: str, pack_id: str, purge: bool = True, log=print) -> dict:
    st = load_state(game_dir)
    prev = copy.deepcopy(st["packs"])
    before = len(st["packs"])
    st["packs"] = [p for p in st["packs"] if p["id"] != pack_id]
    if len(st["packs"]) == before:
        raise InstallError(f"pack not installed: {pack_id}")
    purged = None
    if purge:
        pth = os.path.join(_paths(game_dir)["packs"], pack_id + ".zip")
        if os.path.isfile(pth):
            os.replace(pth, pth + ".removing")
            purged = (pth, pth + ".removing")
    save_state(game_dir, st)
    log(f"removed {pack_id}")
    try:
        res = apply_state(game_dir, st, log=log)
    except BaseException:
        _rollback(game_dir, st, prev, log=log)
        if purged:
            os.replace(purged[1], purged[0])
        raise
    if purged:
        os.unlink(purged[1])
    return res


def uninstall_all(game_dir: str, log=print) -> dict:
    st = load_state(game_dir)
    st["packs"] = []
    for archive in list(st.get("backups", {})):
        restore_base(game_dir, st, archive, log=log)
    save_state(game_dir, st)
    return {"packs": 0, "textures": 0}


def status(game_dir: str) -> dict:
    st = load_state(game_dir)
    p = _paths(game_dir)
    out = {"game_dir": os.path.abspath(game_dir), "packs": [], "backups": []}
    for pack in st["packs"]:
        out["packs"].append({
            "id": pack["id"],
            "name": pack["name"],
            "version": pack["version"],
            "enabled": pack.get("enabled", True),
            "installed_utc": pack.get("installed_utc"),
            "replacements": pack["manifest"]["replacement_count"],
        })
    for archive, info in st.get("backups", {}).items():
        cur = _archive_path(game_dir, archive)
        out["backups"].append({
            "archive": archive,
            "backup": info["path"],
            "backup_sha256": info["sha256"],
            "current_sha256": sha256_file(cur) if os.path.isfile(cur) else None,
            "modified": (sha256_file(cur) != info["sha256"]) if os.path.isfile(cur) else None,
        })
    return out
