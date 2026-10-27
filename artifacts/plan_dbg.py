import os, sys, tempfile, struct
sys.path.insert(0, r"C:\Users\wedde\Downloads\prototypeps3modlauncher")
from tests.helpers import make_game, make_dds, file_sha256
from ps3texmod.rcf import RcfArchive
from ps3texmod.catalog import scan_archive, make_patch
from ps3texmod.pack import resolve_token, build_pack
from ps3texmod.plan import plan_from_pack, PLAN_HEADER, PLAN_RECORD
import zlib

tmp = tempfile.mkdtemp(prefix="plan-dbg-")
game = make_game(tmp); arch = os.path.join(game,"PS3_GAME","USRDIR","art.rcf")
ar = RcfArchive(arch); recs = scan_archive(ar)
st = next(r for r in recs if r["container"]=="dds")
print("rec key types", {k:type(v).__name__ for k,v in st.items()})
blob = make_dds(st["width"], st["height"], b"DXT1", mips=st["mipmaps"], fill=bytes([0x5A,0x0F,0xA5,0xFF])*(st["size"]//4+1))
p = os.path.join(tmp,"r.dds"); open(p,"wb").write(blob)
r = resolve_token({"art.rcf": ar}, f"art.rcf:{int(st['entry_hash'],16):08X}"); r["source_file"]=p
pk = os.path.join(tmp,"t.zip"); build_pack(pk,[r],name="T",version="1",game_dir=game,log=lambda *a:None)
out = os.path.join(tmp,"o.ps3patch")
plan = plan_from_pack(pk, game, out, log=lambda *a:None)
data = open(out,"rb").read()
print("len", len(data), "magic", data[:8])
vals = struct.unpack_from("<IIIIII", data, 8); print("hdr", vals)
print("archive/count", struct.unpack_from("<II", data, 0x20), "size", struct.unpack_from("<Q", data, 0x28), "flags/strings", struct.unpack_from("<II", data, 0x30))
(sb,) = struct.unpack_from("<I", data, 0x34)
rec_start = PLAN_HEADER + sb
print("string_bytes", sb, "record_start", rec_start, "blob_start", (rec_start+PLAN_RECORD) & ~15)
print("record", struct.unpack_from("<IIIIIIII", data, reclen:=rec_start), "tail", struct.unpack_from("<III", data, rec_start+0x20))
print("plan patches", [(x.offset, x.new_size, x.old_size, x.slot_size) for x in plan.patches])
