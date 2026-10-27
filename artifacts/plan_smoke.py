import os, sys, tempfile, shutil
sys.path.insert(0, r"C:\Users\wedde\Downloads\prototypeps3modlauncher")
from tests.helpers import make_game, make_dds
from ps3texmod.rcf import RcfArchive
from ps3texmod.catalog import scan_archive
from ps3texmod.plan import build_plan, load_plan, verify_plan, apply_plan, restore_journal

tmp = tempfile.mkdtemp(prefix="plan-smoke-")
game = make_game(tmp)
ar = RcfArchive(os.path.join(game, "PS3_GAME", "USRDIR", "art.rcf"))
recs = scan_archive(ar)
print("textures:", [(r["id"], r["name"], r["container"], r["size"]) for r in recs])
target = next(r for r in recs if r["container"] == "dds")
new = make_dds(64, 64, b"DXT1", fill=bytes([0x11,0x22,0x33,0x44])*8192)
stored = new  # raw standalone dds
plan_path = os.path.join(tmp, "test.ps3patch")
plan = build_plan(ar, [(int(target["entry_hash"],16), stored, "tint")], plan_path, plan_name="smoke")
print("built", plan.to_dict())
q = load_plan(plan_path)
print("loaded", q.name, q.archive, len(q.patches), q.blob_bytes)
print("verify before:", verify_plan(q, ar.path))
jd = os.path.join(tmp, "backups")
print("apply:", apply_plan(q, ar.path, journal_dir=jd))
print("verify after:", verify_plan(q, ar.path))
print("journal:", os.listdir(jd))
print("restore:", restore_journal(os.path.join(jd, "art.rcf.journal"), ar.path))
print("verify restored:", verify_plan(q, ar.path))
shutil.rmtree(tmp, ignore_errors=True)
