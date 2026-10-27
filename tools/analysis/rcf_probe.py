import struct, os

ROOT = r"C:\Users\wedde\Downloads\prototypeps3modlauncher\Prototype (USA, Korea) (En,Fr)\PS3_GAME"
P = os.path.join(ROOT, 'USRDIR', 'scripts.rcf')
data = open(P, 'rb').read()

def u32(off):
    return struct.unpack_from('>I', data, off)[0]

print('size', len(data))
print('header words:')
for off in range(0x18, 0x80, 4):
    print(f'  {off:04X}: {u32(off):08X} ({u32(off)})')

# locate strings and preceding bytes
needles = [b'scripts\\credits_english.txt', b'scripts\\frevents.lua', b'init.lua', b'EXECUTIVE PRODUCER']
for n in needles:
    pos = data.find(n)
    print(f'\n{n!r} at {pos} (0x{pos:X})')
    start = max(0, pos-32)
    print('  before:', data[start:pos].hex(' '))
    print('  after :', data[pos+len(n):pos+len(n)+32].hex(' '))

# dump around 0x800 and 0x13c
for off in (0x7C0, 0x7E0, 0x100, 0x120, 0x130, 0x140, 0x150):
    print(f'\n@{off:04X}:')
    chunk = data[off:off+160]
    for i in range(0, len(chunk), 16):
        c = chunk[i:i+16]
        print(f'  {off+i:05X}  {c.hex(" "):<47}  {"".join(chr(b) if 32<=b<127 else "." for b in c)}')

# Last 512 bytes
print('\n--- tail ---')
chunk = data[-512:]
for i in range(0, len(chunk), 16):
    c = chunk[i:i+16]
    print(f'  {len(data)-512+i:06X}  {c.hex(" "):<47}  {"".join(chr(b) if 32<=b<127 else "." for b in c)}')
