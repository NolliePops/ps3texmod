import struct, collections, os

def parse_names(path, limit_bytes=0x400000):
    d = open(path,'rb').read(limit_bytes)
    idx_off, idx_size, tab_off, tab_size, count = struct.unpack_from('>5I', d, 0x24)[0:5]
    count = struct.unpack_from('>I', d, 0x38)[0]
    o = tab_off + 8
    out = []
    while o + 16 <= tab_off + tab_size:
        x, f1, f2, nlen = struct.unpack_from('<4I', d, o)
        if nlen < 2 or nlen > 400: break
        raw = d[o+16:o+16+nlen]
        if not raw.endswith(b'\x00'): break
        name = raw[:-1].decode('latin1')
        out.append((x, f1, f2, name, o))
        o += 16 + nlen + 3
        if len(out) >= count: break
    return out, (idx_off, idx_size, tab_off, tab_size, count)

root = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR'
sc, hdr_s = parse_names(os.path.join(root,'scripts.rcf'))
ar, hdr_a = parse_names(os.path.join(root,'art.rcf'), 0x300000)
print('scripts entries:', len(sc), 'hdr', [hex(v) for v in hdr_s])
for x,f1,f2,n,o in sc:
    print(f'  {x:08X} {f1:08X} {f2:08X} {n!r}')
print()
print('art entries:', len(ar), 'hdr', [hex(v) for v in hdr_a])
print('unique X:', len(set(x for x,_,_,_,_ in ar)))
c = collections.Counter(x for x,_,_,_,_ in ar)
print('most common X:', c.most_common(8))
# all names sharing top values
for val, cnt in c.most_common(4):
    print(f'--- X={val:08X} count={cnt}')
    for x,f1,f2,n,o in ar:
        if x == val:
            print('   ', n)
            if cnt > 12 and ar.index((x,f1,f2,n,o)) > 5: pass
print()
print('unique f1:', set(f1 for _,f1,_,_,_ in ar), 'unique f2:', set(f2 for _,f2,_,_,_ in ar))
lowbytes = collections.Counter(x & 0xFF for x,_,_,_,_ in ar)
print('low byte distribution:', lowbytes.most_common(5))
