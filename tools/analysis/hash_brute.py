import zlib

def fnv1a(b, seed=0x811C9DC5):
    h=seed
    for x in b:
        h ^= x; h=(h*0x01000193)&0xFFFFFFFF
    return h

def fnv1(b, seed=0x811C9DC5):
    h=seed
    for x in b:
        h=(h*0x01000193)&0xFFFFFFFF; h ^= x
    return h

def djb2(b, seed=5381):
    h=seed
    for x in b:
        h=((h*33)+x)&0xFFFFFFFF
    return h

def sdbm(b):
    h=0
    for x in b:
        h=(x+(h<<6)+(h<<16)-h)&0xFFFFFFFF
    return h

targets = {
    r'scripts\credits_english.txt': (0x7A6AD1, 0x7A6AD149),
    r'scripts\credits_french.txt': (0x7A6AD1, 0x7A6AD149),
    r'scripts\credits_italian.txt': (0x7A6AD1, 0x7A6AD149),
    r'scripts\credits_spanish.txt': (0x11B0DB, 0x11B0DB49),
    r'scripts\frevents.lua': (0x2BF2CB, 0x2BF2CB49),
    r'scripts\statsachievements_tod.p3d': (0x6D9FD6, 0x6D9FD649),
    'init.lua': (0xB89EDF, 0xB89EDF49),
}
for name,(t24,t32) in targets.items():
    print(name, 'target24', hex(t24), 'target32', hex(t32))
    variants = [name, name.replace('\\','/'), '\\'+name, '/'+name, name.upper(), name.lower(), name+'\0']
    for v in variants:
        b=v.encode()
        for label,h in [('crc',zlib.crc32(b)),('fnv1a',fnv1a(b)),('fnv1',fnv1(b)),('djb2',djb2(b)),('sdbm',sdbm(b)),('adler',zlib.adler32(b))]:
            for mask,label2 in ((0xFFFFFF,'24'),(0xFFFFFFFF,'32')):
                if (h & mask) == (t24 if mask==0xFFFFFF else t32):
                    print('  MATCH', label, label2, repr(v), hex(h))
