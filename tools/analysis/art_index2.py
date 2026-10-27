import struct
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf'
d = open(p,'rb').read(0x330000)
name_end = 0x2AC2B
idx_size = 0x6E10
idx = d[name_end:name_end+idx_size]
first = next(i for i,b in enumerate(idx) if b)
print('first nonzero index byte at', hex(first), 'value', idx[first])
start = first - (first % 12)
print('entry-aligned start', hex(start))
for i in range(start, min(start+240, idx_size), 12):
    t = struct.unpack_from('>III', idx, i)
    l = struct.unpack_from('<III', idx, i)
    print(f'{i//12:4} @{name_end+i:#08x} BE {t[0]:08X} {t[1]:08X} {t[2]:08X} | LE {l[0]:08X} {l[1]:08X} {l[2]:08X}')
print('\nnonzero count per 12-byte entry:')
nz = [i//12 for i in range(0, idx_size, 12) if any(idx[i:i+12])]
print('total', len(nz), 'first', nz[:10], 'last', nz[-10:])
# try interpret nonzero entries as offset/size
print('\nsample nonzero entries (BE):')
for k in nz[:20]:
    i=k*12
    a,b,c = struct.unpack_from('>III', idx, i)
    print(f'  {k}: {a:08X} {b:08X} {c:08X}  ({a}, {b}, {c})')
