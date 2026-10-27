import struct
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf'
d = open(p,'rb').read(0x330000)
name_end = 0x2AC2B
idx_size = 0x6E10
idx = d[name_end:name_end+idx_size]
print('index nonzero count:', sum(1 for b in idx if b))
print('first 128:', idx[:128].hex(' '))
print('at data start 0x31A3B:', d[name_end+idx_size:name_end+idx_size+64].hex(' '))
# try interpret as triples
for i in range(0, min(96, idx_size), 12):
    a,b,c = struct.unpack_from('>III', idx, i)
    print(i//12, f'@{name_end+i:#x}', hex(a), hex(b), hex(c))
