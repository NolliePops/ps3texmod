import struct, os, zlib
root = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR'
for fn in ('cached.rcf','woi9.rcf','shaders.rcf'):
    p=os.path.join(root,fn); sz=os.path.getsize(p)
    f=open(p,'rb'); h=f.read(0x40)
    magic=h[:24]
    idx_off,idx_sz,tab_off,tab_sz=struct.unpack_from('>4I',h,0x24)
    cnt=struct.unpack_from('>I',h,0x38)[0]
    print(fn, sz, 'magic', magic, 'ver', hex(struct.unpack_from('>I',h,0x20)[0]),
          'idx',hex(idx_off),idx_sz,'tab',hex(tab_off),hex(tab_sz),'count',cnt)
    # first few name records
    f.seek(tab_off); tb=f.read(min(tab_sz, 0x400))
    o=8; shown=0
    while shown<4 and o+16<=len(tb):
        x,f1,f2,nlen=struct.unpack_from('<4I',tb,o)
        if nlen<2 or nlen>400 or o+16+nlen>len(tb): break
        nm=tb[o+16:o+16+nlen]
        print('   rec', hex(x), hex(f1), hex(f2), nlen, nm)
        o+=16+nlen+3; shown+=1
    # first index triple and data peek
    f.seek(idx_off); tr=f.read(24)
    t=[struct.unpack_from('>III',tr,i) for i in (0,12) if i+12<=len(tr)]
    print('   first triples', [(hex(a),hex(b),c) for a,b,c in t])
    if t:
        h0,off0,sz0=t[0]
        f.seek(off0); b=f.read(min(sz0,64))
        print('   data', hex(off0), sz0, b[:32].hex(' '), b[:24])
        if b[:2]==b'RZ':
            f.seek(off0); blob=f.read(sz0)
            try:
                dec=zlib.decompress(blob[16:])
                print('   decomp', len(dec), dec[:64])
            except Exception as e: print('   zlib fail', e)
