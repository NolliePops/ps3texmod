import struct, sys
sys.path.insert(0, r'C:\Users\wedde\Downloads\prototypeps3modlauncher')
from ps3texmod.rcf import RcfArchive
from ps3texmod.dds import parse_dds, decode_level0_rgba, _decode_dxt, FOURCC_DXT1

ar = RcfArchive(r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf')
blob = ar.read_stored(0x94CCC0CC)
info = parse_dds(blob)
rgba, w, h = decode_level0_rgba(blob, info)
px = [tuple(rgba[i:i+4]) for i in range(0, 64, 4)]
print('first 16 pixels:', px)
out = bytearray(16)
_decode_dxt(blob[info.data_offset:info.data_offset+8], FOURCC_DXT1, out, 4, 4, 16, 0, 0)
blk = [tuple(out[i*4:i*4+4]) for i in range(16)]
print('block0        :', blk)
print('equal first16 :', px == blk)
# compare pixel (4,0) and (0,1) etc
print('pixel (0,0)', tuple(rgba[0:4]), 'pixel (1,0)', tuple(rgba[4:8]))
print('pixel (0,1)', tuple(rgba[w*4:w*4+4]), 'pixel (4,0)', tuple(rgba[(4*4):(4*4+4)]))
