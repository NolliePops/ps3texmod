import zlib, struct
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf'
f = open(p,'rb')
f.seek(0x2A0000)
d = f.read(0x20000)
base = 0x2A0000
# find zlib headers
cands=[]
for i in range(len(d)-2):
    if d[i]==0x78 and d[i+1] in (0x01,0x5E,0x9C,0xDA,0x9B):
        try:
            o=zlib.decompressobj()
            out=o.decompress(d[i:i+0x20000])
            if len(out)>64:
                cands.append((base+i,len(out),o.eof,out[:60]))
                if len(cands)>=10: break
        except Exception as e:
            pass
for c in cands:
    print(hex(c[0]), c[1], c[2], c[3])
print('count',len(cands))

# dump first 128 bytes of the region
print(d[:160].hex(' '))
