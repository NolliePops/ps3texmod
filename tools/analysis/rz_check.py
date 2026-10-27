import zlib, struct, os, hashlib
root = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR'
p = os.path.join(root, 'art.rcf')
f = open(p,'rb')
size = os.path.getsize(p)
hdr = f.read(0x40)
idx_off, idx_size, tab_off, tab_size = struct.unpack_from('>4I', hdr, 0x24)
count = struct.unpack_from('>I', hdr, 0x38)[0]
f.seek(idx_off); idx = f.read(idx_size)
triples = [struct.unpack_from('>III', idx, i*12) for i in range(count)]

ok = 0; bad = 0; types = {}
samples = []
for h, off, sz in triples:
    f.seek(off); blob = f.read(sz)
    if blob[:2] == b'RZ':
        usz = struct.unpack_from('<Q', blob, 8)[0]
        try:
            data = zlib.decompress(blob[16:])
        except Exception as e:
            bad += 1; continue
        if len(data) != usz:
            print('size mismatch', hex(h), usz, len(data))
        ok += 1
        t = data[:4]
        types[data[:2]] = types.get(data[:2], 0) + 1
        if len(samples) < 6:
            samples.append((h, off, sz, usz, data[:24]))
    else:
        types['raw:'+blob[:2].decode('latin1')] = types.get('raw:'+blob[:2].decode('latin1'), 0) + 1
        if len(samples) < 10:
            samples.append((h, off, sz, sz, blob[:24]))
print('RZ decompressed OK:', ok, 'bad:', bad, 'total:', len(triples))
print('payload first-2-byte histogram:', sorted(types.items(), key=lambda kv:-kv[1])[:12])
for s in samples:
    print(' ', hex(s[0]), hex(s[1]), 'stored', s[2], 'uncomp', s[3], s[4])

# EBOOT scan for known hashes and strings
eb = open(os.path.join(root, 'EBOOT.BIN'), 'rb').read()
print('\nEBOOT size', len(eb))
for label, needle in [('hash_english', bytes.fromhex('CE310C99')), ('hash_init', bytes.fromhex('0FFD75FA')),
                      ('group_credits', bytes.fromhex('7A6AD149')), ('grp_spanish', bytes.fromhex('11B0DB49')),
                      ('credits_english_str', b'credits_english'), ('art.rcf', b'art.rcf'),
                      ('fnvprime', bytes.fromhex('01000193')), ('crcpoly', bytes.fromhex('EDB88320')),
                      ('murmur2', bytes.fromhex('5BD1E995'))]:
    positions = []
    st = 0
    while True:
        i = eb.find(needle, st)
        if i < 0: break
        positions.append(i); st = i+1
        if len(positions) >= 6: break
    print(f'  {label:20} {len(positions)} hits', [hex(x) for x in positions[:6]])
