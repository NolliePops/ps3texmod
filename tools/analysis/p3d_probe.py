import struct, os, re
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art\frontendshaders.p3d'
d = open(p,'rb').read()
print('size', len(d), hex(len(d)))
print('first 64:', d[:64].hex(' '))
print('as text:', d[:64])
# strings
print('\nstrings:')
for m in re.finditer(rb'[ -~]{4,}', d):
    print(f'  {m.start():06X}: {m.group()[:80]!r}')
