import struct, os
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf'
size = os.path.getsize(p)
f = open(p,'rb')
hdr = f.read(0x40)
idx_off, idx_size, tab_off, tab_size, count = struct.unpack_from('>5I', hdr, 0x24)[:5]
count = struct.unpack_from('>I', hdr, 0x38)[0]
print('file size', size, hex(size))
print('idx_off', hex(idx_off), 'idx_size', idx_size, hex(idx_size), 'tab_off', hex(tab_off), 'tab_size', hex(tab_size), 'count', count)
f.seek(idx_off)
idx = f.read(idx_size)
triples = []
for i in range(count):
    h, off, sz = struct.unpack_from('>III', idx, i*12)
    triples.append((h, off, sz))
nz = [t for t in triples if t[0] or t[1] or t[2]]
print('nonzero triples:', len(nz), 'of', count)
print('first 12 triples:')
for i, t in enumerate(triples[:12]):
    print(f'  {i:4} hash={t[0]:08X} off={t[1]:08X} size={t[2]:08X}')
print('last 4 triples:')
for i, t in enumerate(triples[-4:], count-4):
    print(f'  {i:4} hash={t[0]:08X} off={t[1]:08X} size={t[2]:08X}')
offs = [t[1] for t in nz if t[1]]
sizes = [t[2] for t in nz if t[2]]
print('min off', hex(min(offs)), 'max off+size', hex(max(t[1]+t[2] for t in nz)))
print('min size', min(sizes), 'max size', max(sizes), 'sum sizes', sum(sizes))
print('sorted by hash?', all(triples[i][0] <= triples[i+1][0] for i in range(len(triples)-1)))
print('hash dupes:', len(triples) - len(set(t[0] for t in triples)))
# examine data at first few offsets
print()
for h, off, sz in nz[:10]:
    f.seek(off)
    b = f.read(min(sz, 48))
    print(f'hash={h:08X} off={off:#x} size={sz} first={b[:32].hex(" ")} | {b[:32]!r}')
print()
# scan all data for known magics at their offsets
f.seek(0)
allb = None
magics = {b'DDS ':0, b'\xffD3P':0, b'\x1bLuaQ':0, b'ATGC':0, b'GTF\x00':0, b'\x78\x9c':0, b'\x78\xda':0, b'\x78\x01':0}
hit = {k:0 for k in magics}
for h, off, sz in nz:
    if sz < 4 or off+4 > size: continue
    f.seek(off)
    b = f.read(4)
    for m in magics:
        if b.startswith(m):
            hit[m] += 1
print('magic hits at index offsets:', {k.decode("latin1"): v for k, v in hit.items()})
