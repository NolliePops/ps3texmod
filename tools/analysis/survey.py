import struct, sys, zlib, collections, os

ROOT = r"C:\Users\wedde\Downloads\prototypeps3modlauncher\Prototype (USA, Korea) (En,Fr)\PS3_GAME"

def read(p, n=None, off=0):
    with open(p, 'rb') as f:
        f.seek(off)
        return f.read() if n is None else f.read(n)

def hexdump(data, off=0, limit=512):
    out = []
    for i in range(0, min(len(data), limit), 16):
        chunk = data[i:i+16]
        hexs = ' '.join(f'{b:02X}' for b in chunk)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        out.append(f'{off+i:08X}  {hexs:<47}  {asc}')
    return '\n'.join(out)

def scan_magic(path, magics):
    data = read(path)
    hits = collections.defaultdict(list)
    for name, magic in magics.items():
        start = 0
        while True:
            i = data.find(magic, start)
            if i < 0:
                break
            hits[name].append(i)
            start = i + 1
            if len(hits[name]) >= 40:
                break
    return data, hits

def zlib_scan(data, limit=30):
    hits = []
    for i in range(len(data)-2):
        if data[i] == 0x78 and data[i+1] in (0x01, 0x5E, 0x9C, 0xDA):
            d = zlib.decompressobj()
            try:
                out = d.decompress(data[i:i+0x20000])
                if len(out) > 16:
                    hits.append((i, len(out), d.eof))
                    if len(hits) >= limit:
                        break
            except Exception:
                pass
    return hits

files = {
    'scripts.rcf': os.path.join(ROOT, 'USRDIR', 'scripts.rcf'),
    'shaders.rcf': os.path.join(ROOT, 'USRDIR', 'shaders.rcf'),
    'art.rcf.head': os.path.join(ROOT, 'USRDIR', 'art.rcf'),
    'cementfiles.p3d': os.path.join(ROOT, 'USRDIR', 'cementfiles.p3d'),
    'skuinfo.p3d': os.path.join(ROOT, 'USRDIR', 'skuinfo.p3d'),
    'frontendshaders.p3d': os.path.join(ROOT, 'USRDIR', 'art', 'frontendshaders.p3d'),
    'PS3_DISC.SFB': os.path.join(ROOT, 'PS3_DISC.SFB'),
    'EBOOT.BIN': os.path.join(ROOT, 'USRDIR', 'EBOOT.BIN'),
}

magics = {
    'GTF': b'GTF\x00',
    'DDS': b'DDS ',
    'DXT1': b'DXT1',
    'DXT5': b'DXT5',
    'P3D': b'P3D',
    'SCE': b'SCE\x00',
    'PNG': b'\x89PNG',
    'CEMENT': b'CEMENT',
    'Texture': b'Texture',
    'texture': b'texture',
}

for name, path in files.items():
    if not os.path.exists(path):
        print(f'--- {name}: MISSING')
        continue
    n = min(os.path.getsize(path), 2_000_000) if name == 'art.rcf.head' else None
    with open(path, 'rb') as f:
        data = f.read(n) if n else f.read()
    print(f'==== {name} size={os.path.getsize(path)} read={len(data)}')
    print(hexdump(data, 0, 320))
    hits = {}
    for mname, magic in magics.items():
        start = 0
        found = []
        while True:
            i = data.find(magic, start)
            if i < 0:
                break
            found.append(i)
            start = i + 1
            if len(found) >= 20:
                break
        if found:
            hits[mname] = found
    print('magics:', hits)
    if len(data) < 2_000_000:
        zh = zlib_scan(data, 20)
        print('zlib candidates:', zh)
        # printable strings >= 8
        strings = []
        cur = bytearray()
        for b in data:
            if 32 <= b < 127:
                cur.append(b)
            else:
                if len(cur) >= 8:
                    strings.append(cur.decode('ascii'))
                cur = bytearray()
        if len(cur) >= 8:
            strings.append(cur.decode('ascii'))
        print('strings (first 60):')
        for s in strings[:60]:
            print('   ', repr(s))
    print()
