import zlib, struct

def fnv1a(data, seed=0x811C9DC5):
    h = seed
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h

def djb2(data, seed=5381):
    h = seed
    for b in data:
        h = ((h * 33) + b) & 0xFFFFFFFF
    return h

names = [r'\art\alex\alex.p3d.rz', r'\art\alex\alex_fig.p3d.rz', r'scripts\credits_english.txt']
vals = [0x5733C549, 0x0842D549, 0x7A6AD149]
for n, v in zip(names, vals):
    print(n, hex(v))
    for enc in ('ascii','utf-16le'):
        b = n.encode(enc)
        print('  crc32', hex(zlib.crc32(b)), 'fnv1a', hex(fnv1a(b)), 'djb2', hex(djb2(b)), 'sum', hex(sum(b)&0xFFFFFFFF))
    for pref in ('', '\\', '/', 'art.rcf', 'scripts.rcf'):
        b = (pref+n).encode()
        if zlib.crc32(b) & 0xFF == 0x49 or fnv1a(b) & 0xFF == 0x49:
            print('  HIT', pref, hex(zlib.crc32(b)), hex(fnv1a(b)))
# test archive filenames
for n in ('art.rcf','scripts.rcf','shaders.rcf'):
    b = n.encode()
    print(n, 'crc32', hex(zlib.crc32(b)), 'fnv1a', hex(fnv1a(b)), 'djb2', hex(djb2(b)))
