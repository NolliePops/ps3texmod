import struct, os
root = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR'
for fn in ('cementfiles.p3d','skuinfo.p3d'):
    d=open(os.path.join(root,fn),'rb').read()
    print('='*70); print(fn, len(d))
    for o in range(0,len(d),16):
        c=d[o:o+16]
        print(f'{o:04X}  {c.hex(" "):<47} | {"".join(chr(b) if 32<=b<127 else "." for b in c)}')
