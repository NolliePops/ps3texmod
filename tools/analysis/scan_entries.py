import struct
d = open(r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\scripts.rcf','rb').read()

print('occurrences of 7a6ad149:')
start=0
while True:
    i=d.find(b'\x7a\x6a\xd1\x49', start)
    if i<0: break
    print(f'  {i:#06x}', 'next16:', d[i:i+16].hex(' '))
    start=i+1

print('\nraw 0x810-0x880 words BE / LE:')
for o in range(0x810, 0x880, 4):
    be = struct.unpack_from('>I', d, o)[0]
    le = struct.unpack_from('<I', d, o)[0]
    print(f'  {o:04X}: {be:08X} {le:08X}')

print('\nnames and their preceding 16 bytes:')
names = [b'scripts\\credits_english.txt', b'scripts\\credits_french.txt', b'scripts\\credits_italian.txt',
         b'scripts\\credits_spanish.txt', b'scripts\\frevents.lua', b'scripts\\statsachievements_tod.p3d', b'init.lua']
for n in names:
    p = d.find(n)
    print(f'{n!r} @ {p:#06x} header={d[p-16:p].hex(" ")}')
