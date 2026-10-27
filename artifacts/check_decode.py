import struct, sys
sys.path.insert(0, r'C:\Users\wedde\Downloads\prototypeps3modlauncher')
from ps3texmod.rcf import RcfArchive
from ps3texmod.dds import parse_dds, decode_level0_rgba

ar = RcfArchive(r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf')
e = ar.entry(0x94CCC0CC)
blob = ar.read_stored(e)
info = parse_dds(blob)
print(info.summary())
rgba, w, h = decode_level0_rgba(blob, info)
print('rgba bytes', len(rgba), 'expected', w*h*4)
# colour statistics on mip 0
px = [rgba[i:i+4] for i in range(0, len(rgba), 4)]
uniq = len(set(px))
mean = [sum(p[c] for p in px)/len(px) for c in range(4)]
print('unique pixels', uniq, 'mean RGBA', [round(x,1) for x in mean])
print('alpha range', min(p[3] for p in px), max(p[3] for p in px))
print('first 4 decoded pixels', [tuple(p) for p in px[:4]])

# independent hand decode of the first DXT1 block
data = blob[info.data_offset:info.data_offset+8]
c0, c1, idx = struct.unpack('<HHI', data)
def rgb565(c):
    r=(c>>11)&31; g=(c>>5)&63; b=c&31
    return (round(r*255/31), round(g*255/63), round(b*255/31))
p0, p1 = rgb565(c0), rgb565(c1)
pal = [(*p0,255), (*p1,255),
       (round((2*p0[0]+p1[0])/3), round((2*p0[1]+p1[1])/3), round((2*p0[2]+p1[2])/3),255),
       (round((p0[0]+2*p1[0])/3), round((p0[1]+2*p1[1])/3), round((p0[2]+2*p1[2])/3),255)]
ref = [pal[(idx>>(2*i))&3] for i in range(16)]
print('c0=%04X c1=%04X' % (c0,c1))
for row in range(4):
    a=[tuple(px[row*4+x]) for x in range(4)]
    b=[ref[row*4+x] for x in range(4)]
    print(a, b, 'OK' if a==b else 'MISMATCH')
