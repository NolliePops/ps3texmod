import struct, os, zlib, re, collections
root = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR'
p=os.path.join(root,'art.rcf')
f=open(p,'rb'); h=f.read(0x40)
idx_off,idx_sz,tab_off,tab_sz=struct.unpack_from('>4I',h,0x24)
cnt=struct.unpack_from('>I',h,0x38)[0]
f.seek(idx_off); idx=f.read(idx_sz)
tr=[struct.unpack_from('>III',idx,i*12) for i in range(cnt)]
f.seek(tab_off); tb=f.read(tab_sz)
names=[]; o=8
while len(names)<cnt:
    x,f1,f2,nlen=struct.unpack_from('<4I',tb,o)
    names.append(tb[o+16:o+16+nlen-1].decode('latin1')); o+=16+nlen+3
pairs=list(zip(names, sorted(tr, key=lambda t:t[1])))
targets=[n for n,_ in pairs if n.endswith('billboards.p3d.rz') or n.endswith('alex_fig.p3d.rz') or n.endswith('startup_shaders.p3d.rz')]
for t in targets:
    hsh,off,sz = dict(pairs)[t]
    f.seek(off); blob=f.read(sz)
    data = zlib.decompress(blob[16:]) if blob[:2]==b'RZ' else blob
    print('='*70)
    print(t, 'stored', sz, 'decomp', len(data), 'magic', data[:8])
    print('  first 96:', data[:96].hex(' '))
    for mag in (b'DDS ', b'Texture', b'Texture2D', b'GTF\x00', b'Shader', b'P3D'):
        pos=[]; s=0
        while True:
            i=data.find(mag,s)
            if i<0: break
            pos.append(i); s=i+1
            if len(pos)>=8: break
        print(f'  {mag!r}: {len(pos)} hits', [hex(x) for x in pos[:8]])
    # strings near start
    strs=[(m.start(), m.group().decode('latin1')) for m in re.finditer(rb'[ -~]{5,}', data[:0x600])]
    print('  early strings:', strs[:14])
