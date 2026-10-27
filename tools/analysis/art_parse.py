import struct
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf'
d = open(p,'rb').read(0x300000)
o = 0x7008
count = 0x6E10
ok = 0
bad = None
names = []
while ok < count:
    if o+16 > len(d):
        bad=('eof', o); break
    h = d[o:o+4]
    f1 = struct.unpack_from('<I', d, o+4)[0]
    f2 = struct.unpack_from('<I', d, o+8)[0]
    nlen = struct.unpack_from('<I', d, o+12)[0]
    if nlen < 2 or nlen > 400 or o+16+nlen > len(d):
        bad=('nlen', o, nlen); break
    nb = d[o+16:o+16+nlen]
    if not nb.endswith(b'\x00') or any(b < 9 or (13 < b < 32) or b > 126 for b in nb[:-1]):
        bad=('name', o, nlen, nb[:50]); break
    names.append((o, h.hex(), f1, f2, nb[:-1].decode('ascii')))
    o += 16 + nlen + 3
    ok += 1
print('parsed', ok, 'of', count, 'end', hex(o), 'bad', bad)
print('target 0x23C2B, delta', hex(0x23C2B - o) if ok else '')
for e in names[:5]: print(e)
print('...')
for e in names[-5:]: print(e)
print('data at table end:', d[o:o+64].hex(' '))
if 0x23C2B < len(d):
    print('at 0x23C2B:', d[0x23C2B:0x23C2B+64].hex(' '))
