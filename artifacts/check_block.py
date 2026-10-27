import struct, sys
sys.path.insert(0, r'C:\Users\wedde\Downloads\prototypeps3modlauncher')
from ps3texmod.rcf import RcfArchive
from ps3texmod.dds import parse_dds, _decode_dxt, _rgb565, FOURCC_DXT1

ar = RcfArchive(r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf')
blob = ar.read_stored(0x94CCC0CC)
info = parse_dds(blob)
src = blob[info.data_offset:info.data_offset+info.data_size]
blk = src[:8]
c0, c1 = struct.unpack_from('<HH', blk, 0)
idx = struct.unpack_from('<I', blk, 4)[0]
print('block', blk.hex(), 'c0=%04X c1=%04X idx=%08X' % (c0,c1,idx))
p0, p1 = _rgb565(c0), _rgb565(c1)
print('p0', p0, 'p1', p1)
pal = [(*p0,255),(*p1,255),
       ((2*p0[0]+p1[0])//3,(2*p0[1]+p1[1])//3,(2*p0[2]+p1[2])//3,255),
       ((p0[0]+2*p1[0])//3,(p0[1]+2*p1[1])//3,(p0[2]+2*p1[2])//3,255)]
print('palette', pal)
out = bytearray(4*4*4)
_decode_dxt(blk, FOURCC_DXT1, out, 4, 4, 16, 0, 0)
ours = [tuple(out[i*4:i*4+4]) for i in range(16)]
ref = [pal[(idx>>(2*i))&3] for i in range(16)]
print('ours')
for r in range(4): print(' ', ours[r*4:r*4+4])
print('ref')
for r in range(4): print(' ', ref[r*4:r*4+4])
print('equal', ours==ref)
