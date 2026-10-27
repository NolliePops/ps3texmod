import struct, os
root = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR'
def parse(fn, name_limit=0x400000):
    p=os.path.join(root,fn)
    with open(p,'rb') as f:
        h=f.read(0x40)
        idx_off,idx_sz,tab_off,tab_sz=struct.unpack_from('>4I',h,0x24)
        cnt=struct.unpack_from('>I',h,0x38)[0]
        f.seek(idx_off); idx=f.read(idx_sz)
        triples=[struct.unpack_from('>III',idx,i*12) for i in range(cnt)]
        f.seek(tab_off); tb=f.read(tab_sz)
    names=[]; o=8
    while len(names)<cnt and o+16<=len(tb):
        x,f1,f2,nlen=struct.unpack_from('<4I',tb,o)
        if nlen<2 or nlen>512 or o+16+nlen>len(tb): break
        nm=tb[o+16:o+16+nlen]
        if not nm.endswith(b'\x00'): break
        names.append(nm[:-1].decode('latin1'))
        o+=16+nlen+3
    return triples, names, (idx_off,idx_sz,tab_off,tab_sz,cnt)

for fn in ('scripts.rcf','shaders.rcf','woi9.rcf','art.rcf','cached.rcf'):
    tr, nm, hdr = parse(fn)
    print(f'== {fn}: entries={len(tr)} names={len(nm)} hdr={hdr}')
    if len(tr)!=len(nm):
        print('   COUNT MISMATCH'); continue
    by_off=sorted(tr, key=lambda t:t[1])
    offs=[t[1] for t in by_off]
    print('   unique offsets:', len(set(offs)), 'unique hashes:', len(set(t[0] for t in tr)))
    print('   first 6 name/offset pairs:')
    for n,(h,off,sz) in list(zip(nm,by_off))[:6]:
        print(f'      {n!r:60} hash={h:08X} off={off:#x} size={sz}')
    # consistency: extension vs payload magic
    p=os.path.join(root,fn)
    import collections
    stats=collections.Counter(); bad=[]
    with open(p,'rb') as f:
        for n,(h,off,sz) in zip(nm,by_off):
            f.seek(off); b=f.read(24)
            ext=n.rsplit('.',1)[-1].lower()
            magic='?'
            if b[:2]==b'RZ': magic='rz'
            elif b[:4]==b'DDS ': magic='dds'
            elif b[:4]==b'\xffD3P' or b[:4]==b'P3D\xff': magic='p3d'
            elif b[:3]==b'GFX': magic='gfx'
            elif b[:4]==b'BIKi' or b[:4]==b'BIK': magic='bik'
            elif b[:3]==b'ID3' or b[:4]==b'\xffD3P': magic='p3d'
            stats[(ext,magic)]+=1
            if (ext=='dds' and magic!='dds') or (ext=='gfx' and magic!='gfx') or (ext=='bik' and magic!='bik'):
                if len(bad)<8: bad.append((n,magic,hex(h),hex(off),sz))
    print('   ext/magic pairs:', stats.most_common(10))
    if bad: print('   MISMATCHES:', bad)
