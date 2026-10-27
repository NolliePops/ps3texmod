import zlib, struct, hashlib

M32 = 0xFFFFFFFF

def fnv1a(b, seed=0x811C9DC5):
    h = seed
    for x in b:
        h ^= x; h = (h * 0x01000193) & M32
    return h

def fnv1(b, seed=0x811C9DC5):
    h = seed
    for x in b:
        h = (h * 0x01000193) & M32; h ^= x
    return h

def djb2(b, seed=5381):
    h = seed
    for x in b:
        h = ((h * 33) + x) & M32
    return h

def djb2x(b, seed=5381):
    h = seed
    for x in b:
        h = ((h * 33) ^ x) & M32
    return h

def sdbm(b, seed=0):
    h = seed
    for x in b:
        h = (x + (h << 6) + (h << 16) - h) & M32
    return h

def bkdr(b, seed=131):
    h = seed
    for x in b:
        h = (h * seed + x) & M32
    return h

def java(b, seed=0):
    h = 0
    for x in b:
        h = (h * 31 + x) & M32
    return h

def oat(b, seed=0):
    h = seed
    for x in b:
        h = (h + x) & M32
        h = (h + (h << 10)) & M32
        h ^= h >> 6
    h = (h + (h << 3)) & M32
    h ^= h >> 11
    h = (h + (h << 15)) & M32
    return h & M32

def elf(b, seed=0):
    h = seed
    for x in b:
        h = ((h << 4) + x) & M32
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & M32
    return h

def ap(b):
    h = 0xAAAAAAAA
    for i, x in enumerate(b):
        if i & 1 == 0:
            h ^= ((h << 7) ^ x * (h >> 3)) & M32
        else:
            h ^= (~((h << 11) + (x ^ (h >> 5)))) & M32
        h &= M32
    return h

def js(b):
    h = 1315423911
    for x in b:
        h ^= ((h << 5) + x + (h >> 2)) & M32
        h &= M32
    return h

def murmur3(b, seed=0):
    c1, c2 = 0xcc9e2d51, 0x1b873593
    h = seed
    n = len(b)
    for i in range(0, n - n % 4, 4):
        k = struct.unpack_from('<I', b, i)[0]
        k = (k * c1) & M32; k = ((k << 15) | (k >> 17)) & M32; k = (k * c2) & M32
        h ^= k; h = ((h << 13) | (h >> 19)) & M32; h = (h * 5 + 0xe6546b64) & M32
    k = 0
    tail = b[n - n % 4:]
    if len(tail) >= 3: k ^= tail[2] << 16
    if len(tail) >= 2: k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & M32; k = ((k << 15) | (k >> 17)) & M32; k = (k * c2) & M32
        h ^= k
    h ^= n
    h ^= h >> 16; h = (h * 0x85ebca6b) & M32
    h ^= h >> 13; h = (h * 0xc2b2ae35) & M32
    h ^= h >> 16
    return h

def crc32c(b):
    poly = 0x82F63B78
    crc = 0xFFFFFFFF
    for x in b:
        crc ^= x
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF

def crc32_radical(b, poly=0x04C11DB7):
    crc = 0xFFFFFFFF
    for x in b:
        crc ^= x << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (poly if crc & 0x80000000 else 0)) & M32
    return crc ^ 0xFFFFFFFF

def xxh32(b, seed=0):
    P1,P2,P3,P4,P5 = 2654435761,2246822519,3266489917,668265263,374761393
    def rotl(x,r): return ((x<<r)|(x>>(32-r)))&M32
    n=len(b); i=0
    if n>=16:
        v1=(seed+P1+P2)&M32; v2=(seed+P2)&M32; v3=seed; v4=(seed-P1)&M32
        while i<=n-16:
            for k,var in enumerate([1,2,3,4]):
                pass
            v1=(rotl((v1+struct.unpack_from('<I',b,i)[0]*P2)&M32,13)*P1)&M32; i+=4
            v2=(rotl((v2+struct.unpack_from('<I',b,i)[0]*P2)&M32,13)*P1)&M32; i+=4
            v3=(rotl((v3+struct.unpack_from('<I',b,i)[0]*P2)&M32,13)*P1)&M32; i+=4
            v4=(rotl((v4+struct.unpack_from('<I',b,i)[0]*P2)&M32,13)*P1)&M32; i+=4
        h=(rotl(v1,1)+rotl(v2,7)+rotl(v3,12)+rotl(v4,18))&M32
    else:
        h=(seed+P5)&M32
    h=(h+n)&M32
    while i<=n-4:
        h=(h+struct.unpack_from('<I',b,i)[0]*P3)&M32
        h=(rotl(h,17)*P4)&M32; i+=4
    while i<n:
        h=(h+b[i]*P5)&M32; h=(rotl(h,11)*P1)&M32; i+=1
    h^=h>>15; h=(h*P2)&M32; h^=h>>13; h=(h*P3)&M32; h^=h>>16
    return h

names = {
    'scripts\\credits_english.txt': 0xCE310C99,
    'scripts\\credits_french.txt': 0xA99C4C55,
    'scripts\\credits_italian.txt': 0x437F3629,
    'scripts\\credits_spanish.txt': 0x316D751D,
    'scripts\\frevents.lua': 0x34E40DC3,
    'scripts\\statsachievements_tod.p3d': 0xD1815F8C,
    'init.lua': 0x0FFD75FA,
}

funcs = [('crc32', lambda b: zlib.crc32(b) & M32), ('adler', lambda b: zlib.adler32(b) & M32),
         ('fnv1a', fnv1a), ('fnv1', fnv1), ('djb2', djb2), ('djb2x', djb2x), ('sdbm', sdbm),
         ('bkdr131', bkdr), ('java31', java), ('oat', oat), ('elf', elf), ('ap', ap), ('js', js),
         ('murmur3', murmur3), ('crc32c', crc32c), ('crc32be', crc32_radical), ('xxh32', xxh32)]

def variants(name):
    base = name
    fn = name.split('\\')[-1]
    stem = fn.rsplit('.', 1)[0]
    yield 'orig', base
    yield 'upper', base.upper()
    yield 'lower', base.lower()
    yield 'slash', base.replace('\\', '/')
    yield 'slash_upper', base.replace('\\', '/').upper()
    yield 'lead_slash', '/' + base.replace('\\', '/')
    yield 'lead_bslash', '\\' + base
    yield 'fn', fn
    yield 'fn_upper', fn.upper()
    yield 'stem', stem
    yield 'stem_lower', stem.lower()
    yield 'nul', base + '\x00'
    yield 'crlf', base + '\r\n'
    yield 'dbl', base.replace('\\', '\\\\')

targets = set(names.values())
print('targets:', [hex(t) for t in sorted(targets)])
found = []
for name, target in names.items():
    for vlabel, v in variants(name):
        for enc in ('ascii', 'utf-8', 'utf-16-le'):
            try:
                b = v.encode(enc)
            except Exception:
                continue
            for flabel, f in funcs:
                try:
                    h = f(b) & M32
                except Exception:
                    continue
                if h == target:
                    found.append((name, vlabel, enc, flabel, hex(h)))
                # also check with xor/not/add constants
                for clabel, c in (('~', (~h) & M32), ('+1', (h + 1) & M32), ('xorFFFFFFFF', h ^ M32)):
                    if c == target:
                        found.append((name, vlabel, enc, flabel + clabel, hex(c)))
print('matches:', found)

# also compute for the 3 credits files to see if hash differs only by name -> yes
print()
print('sanity: crc32 of names')
for n, t in names.items():
    print(f'  {n:40} target={t:08X} crc={zlib.crc32(n.encode()):08X} fnv1a={fnv1a(n.encode()):08X} murmur={murmur3(n.encode()):08X}')
