import zlib, struct, hashlib
M=0xFFFFFFFF
P = {
 'scripts\\credits_english.txt':0xCE310C99,
 'scripts\\credits_french.txt':0xA99C4C55,
 'scripts\\credits_italian.txt':0x437F3629,
 'scripts\\credits_spanish.txt':0x316D751D,
 'scripts\\frevents.lua':0x34E40DC3,
 'scripts\\statsachievements_tod.p3d':0xD1815F8C,
 'init.lua':0x0FFD75FA,
}
def crc32r(b, poly=0xEDB88320, init=0xFFFFFFFF, xor=0xFFFFFFFF):
    c=init
    for x in b:
        c^=x
        for _ in range(8): c=(c>>1)^(poly if c&1 else 0)
    return (c^xor)&M
def crc32n(b, poly=0x04C11DB7, init=0xFFFFFFFF, xor=0xFFFFFFFF):
    c=init
    for x in b:
        c^=x<<24
        for _ in range(8): c=((c<<1)^(poly if c&0x80000000 else 0))&M
    return (c^xor)&M
def words(b, bo):
    n=len(b)//4
    return [int.from_bytes(b[i*4:i*4+4], bo) for i in range(n)], b[n*4:]
def oat_words(b, bo='big', seed=0):
    w,tail=words(b,bo); h=seed
    for x in w:
        h=(h+x)&M; h=(h+(h<<10))&M; h^=h>>6; h&=M
    for x in tail:
        h=(h+x)&M; h=(h+(h<<10))&M; h^=h>>6; h&=M
    h=(h+(h<<3))&M; h^=h>>11; h=(h+(h<<15))&M
    return h&M
def poly_words(b, p, bo='big', seed=0):
    w,tail=words(b,bo); h=seed
    for x in w: h=(h*p+x)&M
    for x in tail: h=(h*p+x)&M
    return h
def fnv_fold(b, seed=0x811C9DC5):
    h=seed
    for x in b: h^=x; h=(h*0x01000193)&M
    return (h^(h>>16))&M
def addhash32(b, bo='big'):
    w,tail=words(b,bo)
    h=len(b)
    for x in w: h=(h+x)&M
    return h&M
def rol(x,r): return ((x<<r)|(x>>(32-r)))&M

funcs={}
for name,poly in (('ref',0xEDB88320),('norm',0x04C11DB7),('c',0x82F63B78),('koop',0xEB31D82E),('q',0x814141AB),('jam',0xEDB88320)):
    for init in (0,0xFFFFFFFF):
        for xor in (0,0xFFFFFFFF):
            f=(lambda b,po=poly,i=init,x=xor: crc32r(b,po,i,x)) if name in ('ref','c','koop','q','jam') else (lambda b,po=poly,i=init,x=xor: crc32n(b,po,i,x))
            funcs[f'crc_{name}_{init:x}_{xor:x}']=f
funcs.update({
 'fnvfold':fnv_fold,
 'oat_wbe':lambda b: oat_words(b,'big'),
 'oat_wle':lambda b: oat_words(b,'little'),
 'add_be':lambda b: addhash32(b,'big'),
 'add_le':lambda b: addhash32(b,'little'),
 'fnv64_fold':lambda b: (lambda h: (h^(h>>32))&M)(__import__('functools').reduce(lambda h,x: ((h^x)*0x100000001b3)&0xFFFFFFFFFFFFFFFF, b, 0xcbf29ce484222325)),
 'djb2_0':lambda b: (lambda h: [h:=((h*33)+x)&M for x in b] and h)(0),
 'sdbm_0':lambda b: (lambda h: [h:=(x+(h<<6)+(h<<16)-h)&M for x in b] and h)(0),
 'rs':lambda b: (lambda h: [h:=(h*378551+x)&M for x in b] and h)(0),
 'js':lambda b: (lambda h: [h:=(h^(((h<<5)+x+(h>>2))&M))&M for x in b] and h)(1315423911),
 'pjw':lambda b: (lambda h: [ (h:=((h<<4)+x)&M) ] and h)(0),
})
for p in (31,37,131,1313,65599,1000003,16777619):
    for bo in ('big','little'):
        funcs[f'pw_{p}_{bo}']=lambda b,p=p,bo=bo: poly_words(b,p,bo)

def variants(n):
    fn=n.split('\\')[-1]
    stem=fn.rsplit('.',1)[0]
    dirn=n.rsplit('\\',1)[0] if '\\' in n else ''
    outs=set()
    for s in (n, n.upper(), n.lower(), n.replace('\\','/'), n.replace('\\','/').upper(),
              n.replace('\\','/').lower(), fn, fn.upper(), fn.lower(), stem, stem.lower(),
              '\\'+n, '/'+n.replace('\\','/'), dirn, dirn.lower()):
        for t in ('', '\x00', '\x00\x00', '\r\n', ' '):
            outs.add(s+t)
        for L in (8,16,32,64,128,256):
            if len(s)<L: outs.add(s+'\x00'*(L-len(s)))
    return outs

hits=[]
tested=0
for name,target in P.items():
    for v in variants(name):
        for enc in ('latin-1','utf-16-le'):
            b=v.encode(enc)
            for fn,f in funcs.items():
                tested+=1
                try: h=f(b)&M
                except Exception: continue
                if h==target: hits.append((name,v,enc,fn))
print('tested',tested,'hits',hits)
