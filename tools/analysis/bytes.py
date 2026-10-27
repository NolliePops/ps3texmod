import sys
d = open(r"Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf", 'rb').read(0x7070)
for o in range(0x7000, 0x7050):
    b = d[o]
    c = chr(b) if 32 <= b < 127 else '.'
    print(f'{o:04X}: {b:02X} {c}')
