d = open(r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\art.rcf','rb').read(0x2B040)
for o in range(0x2AC20, 0x2B040, 16):
    c = d[o:o+16]
    print(f'{o:06X}:', c.hex(' '), '|', ''.join(chr(b) if 32<=b<127 else '.' for b in c))
