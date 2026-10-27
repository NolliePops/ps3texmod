import zlib, struct
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\scripts.rcf'
d = open(p,'rb').read()
triples = []
for i in range(7):
    h, off, size = struct.unpack_from('>III', d, 0x3C + i*12)
    triples.append((h, off, size))
    print(f'{i}: hash={h:08X} off={off:08X} size={size} ({size})')
    blob = d[off:off+size]
    print('   crc32', hex(zlib.crc32(blob)), 'adler', hex(zlib.adler32(blob)), 'first', blob[:16])
print()
# compare every name-entry value with each triple hash
import binascii
names = []
o=0x808
for i in range(7):
    h=d[o:o+4]
    nlen=struct.unpack_from('<I',d,o+12)[0]
    name=d[o+16:o+16+nlen-1].decode()
    names.append((int.from_bytes(h,'big'), int.from_bytes(h,'little'), name))
    o += 16+nlen+3
for be,le,n in names:
    print(f'{n}  BE={be:08X} LE={le:08X}')
