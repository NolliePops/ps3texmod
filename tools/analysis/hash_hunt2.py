import zlib, struct, hashlib
M = 0xFFFFFFFF
pairs = {
    'scripts\\credits_english.txt': 0xCE310C99,
    'scripts\\credits_french.txt': 0xA99C4C55,
    'scripts\\credits_italian.txt': 0x437F3629,
    'scripts\\credits_spanish.txt': 0x316D751D,
    'scripts\\frevents.lua': 0x34E40DC3,
    'scripts\\statsachievements_tod.p3d': 0xD1815F8C,
    'init.lua': 0x0FFD75FA,
}

def crc_reflected(b, poly=0xEDB88320, init=0xFFFFFFFF, xorout=0xFFFFFFFF):
    c = init
    for x in b:
        c ^= x
        for _ in range(8):
            c = (c >> 1) ^ (poly if c & 1 else 0)
    return c ^ xorout

def crc_normal(b, poly=0x04C11DB7, init=0xFFFFFFFF, xorout=0xFFFFFFFF):
    c = init
    for x in b:
        c ^= x << 24
        for _ in range(8):
            c = ((c << 1) ^ (poly if c & 0x80000000 else 0)) & M
    return c ^ xorout

def hashlittle(data, init=0):
    a = bb = c = (0xdeadbeef + len(data) + init) & M
    i = 0
    n = len(data)
    while n - i > 12:
        a = (a + int.from_bytes(data[i:i+4],'little')) & M
        bb = (bb + int.from_bytes(data[i+4:i+8],'little')) & M
        c = (c + int.from_bytes(data[i+8:i+12],'little')) & M
        a = (a - c) & M; a ^= ((c << 4) | (c >> 28)) & M; c = (c + bb) & M
        bb = (bb - a) & M; bb ^= ((a << 6) | (a >> 26)) & M; a = (a + c) & M
        c = (c - bb) & M; c ^= ((bb << 8) | (bb >> 24)) & M; bb = (bb + a) & M
        a = (a - c) & M; a ^= ((c << 16) | (c >> 16)) & M; c = (c + bb) & M
        bb = (bb - a) & M; bb ^= ((a << 19) | (a >> 13)) & M; a = (a + c) & M
        c = (c - bb) & M; c ^= ((bb << 4) | (bb >> 28)) & M; bb = (bb + a) & M
        i += 12
    rem = data[i:]
    if rem:
        for j, x in enumerate(rem):
            if j < 4: a = (a + (x << (8*j))) & M
            elif j < 8: bb = (bb + (x << (8*(j-4)))) & M
            else: c = (c + (x << (8*(j-8)))) & M
        c ^= bb; c = (c - (((bb << 14) | (bb >> 18)) & M)) & M
        a ^= c; a = (a - (((c << 11) | (c >> 21)) & M)) & M
        bb ^= a; bb = (bb - (((a << 25) | (a >> 7)) & M)) & M
        c ^= bb; c = (c - (((bb << 16) | (bb >> 16)) & M)) & M
        a ^= c; a = (a - (((c << 4) | (c >> 28)) & M)) & M
        bb ^= a; bb = (bb - (((a << 14) | (a >> 18)) & M)) & M
        c ^= bb; c = (c - (((bb << 24) | (bb >> 8)) & M)) & M
    return c

def superfasthash(b):
    h = len(b) & M
    i = 0
    while i + 4 <= len(b) - 1:
        h = (h + int.from_bytes(b[i:i+2],'little')) & M
        tmp = (int.from_bytes(b[i+2:i+4],'little') << 11) ^ h
        h = ((h << 16) ^ tmp) & M
        h = (h + (h >> 11)) & M
        i += 4
    if i < len(b):
        h = (h + b[i]) & M
        h ^= (h << 10) & M
        h = (h + (h >> 1)) & M
    h ^= (h << 3) & M
    h = (h + (h >> 5)) & M
    h ^= (h << 4) & M
    h = (h + (h >> 17)) & M
    h ^= (h << 25) & M
    h = (h + (h >> 6)) & M
    return h

def murmur2(b, seed=0):
    m = 0x5BD1E995; r = 24
    h = (seed ^ len(b)) & M
    i = 0
    while len(b) - i >= 4:
        k = int.from_bytes(b[i:i+4],'little')
        k = (k * m) & M; k ^= k >> r; k = (k * m) & M
        h = (h * m) & M; h ^= k
        i += 4
    tail = b[i:]
    if len(tail) == 3: h ^= tail[2] << 16
    if len(tail) >= 2: h ^= tail[1] << 8
    if len(tail) >= 1:
        h ^= tail[0]; h = (h * m) & M
    h ^= h >> 13; h = (h * m) & M; h ^= h >> 15
    return h

def fnv1a32(b, seed=0x811C9DC5):
    h = seed
    for x in b:
        h ^= x; h = (h * 0x01000193) & M
    return h

def fnv1_32(b, seed=0x811C9DC5):
    h = seed
    for x in b:
        h = (h * 0x01000193) & M; h ^= x
    return h

funcs = {
 'crc32': lambda b: zlib.crc32(b) & M,
 'crc32not': lambda b: (~zlib.crc32(b)) & M,
 'crc32bswap': lambda b: int.from_bytes((zlib.crc32(b)&M).to_bytes(4,'big'),'little'),
 'crc_normal': crc_normal,
 'crc_normal_bswap': lambda b: int.from_bytes(crc_normal(b).to_bytes(4,'big'),'little'),
 'crc_reflected0': lambda b: crc_reflected(b, init=0, xorout=0),
 'adler': lambda b: zlib.adler32(b) & M,
 'fnv1a': fnv1a32, 'fnv1': fnv1_32,
 'murmur2': murmur2, 'murmur2_1': lambda b: murmur2(b,1), 'murmur2_0x9747b28c': lambda b: murmur2(b,0x9747b28c),
 'superfast': superfasthash, 'lookup3': hashlittle,
 'md5_be': lambda b: int.from_bytes(hashlib.md5(b).digest()[:4],'big'),
 'md5_le': lambda b: int.from_bytes(hashlib.md5(b).digest()[:4],'little'),
 'md5_last_be': lambda b: int.from_bytes(hashlib.md5(b).digest()[-4:],'big'),
 'sha1_be': lambda b: int.from_bytes(hashlib.sha1(b).digest()[:4],'big'),
 'sha1_le': lambda b: int.from_bytes(hashlib.sha1(b).digest()[:4],'little'),
 'sha1_last_le': lambda b: int.from_bytes(hashlib.sha1(b).digest()[-4:],'little'),
 'sha256_be': lambda b: int.from_bytes(hashlib.sha256(b).digest()[:4],'big'),
 'sha256_le': lambda b: int.from_bytes(hashlib.sha256(b).digest()[:4],'little'),
 'sha256_last_be': lambda b: int.from_bytes(hashlib.sha256(b).digest()[-4:],'big'),
 'blake2b_le': lambda b: int.from_bytes(hashlib.blake2b(b, digest_size=8).digest()[:4],'little'),
}

prefixes = ['', '\\', '/', 'scripts.rcf\\', 'scripts.rcf:', 'usrdir\\', 'USRDIR\\', 'PS3_GAME\\USRDIR\\']
suffixes = ['', '\x00', '\x00\x00\x00\x00', '\r\n']
hits = []
for name, target in pairs.items():
    variants = set()
    for base in (name, name.upper(), name.lower(), name.replace('\\','/'), name.replace('\\','/').upper(),
                 name.replace('\\','/').lower(), name.split('\\')[-1], name.split('\\')[-1].lower(),
                 name.split('\\')[-1].rsplit('.',1)[0]):
        for pre in prefixes:
            for suf in suffixes:
                variants.add(pre + base + suf)
    for v in variants:
        for enc in ('latin-1', 'utf-16-le', 'utf-16-be'):
            try: b = v.encode(enc)
            except Exception: continue
            for fname, f in funcs.items():
                h = f(b) & M
                if h == target:
                    hits.append((name, v, enc, fname, hex(h)))
print('exact hits:', hits)

# seeded polynomial solve: h = seed*p^n + S  (mod 2^32)
def poly_solve(p, add=True):
    # compute S and p^n for each name; solve seed from first, verify rest
    results = []
    for enc in ('latin-1','utf-16-le'):
        Ss = []
        for name in pairs:
            b = name.encode(enc)
            S = 0
            for x in b:
                S = (S * p + (x if add else x)) & M
            Ss.append((S, pow(p, len(b), 1<<32)))
        # h = seed * p^n + S
        S0, pn0 = Ss[0]
        t0 = pairs[list(pairs)[0]]
        # solve seed: seed = (t0 - S0) * inv(pn0)
        try:
            inv = pow(pn0, -1, 1<<32)
        except ValueError:
            continue
        seed = ((t0 - S0) * inv) & M
        ok = True
        for (S, pn), name in zip(Ss[1:], list(pairs)[1:]):
            if (seed * pn + S) & M != pairs[name]:
                ok = False; break
        if ok:
            results.append((p, enc, seed, hex(seed)))
    return results

print('\npolynomial seed solves (add):')
for p in (31,33,37,131,65599,16777619,2654435761,16777619,1000003,0x1000193):
    r = poly_solve(p)
    if r: print(' ', r)
print('done')
