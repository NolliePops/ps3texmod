import struct, os

ROOT = r"C:\Users\wedde\Downloads\prototypeps3modlauncher\Prototype (USA, Korea) (En,Fr)\PS3_GAME"

def be32(d, o): return struct.unpack_from('>I', d, o)[0]

def parse(path, table_off=None, count=None):
    d = open(path,'rb').read()
    print(f'==== {os.path.basename(path)} size={len(d)}')
    print('hdr words:', ' '.join(f'{be32(d,o):08X}' for o in range(0x20, 0x3C, 4)))
    if table_off is None:
        table_off = be32(d, 0x2C)
    if count is None:
        count = be32(d, 0x28)
    print(f'table_off={table_off} (0x{table_off:X}) count={count}')
    o = table_off
    prev_end = o
    for i in range(count):
        if o + 16 > len(d):
            print('  truncated at entry', i); break
        h, f1, f2, nlen = struct.unpack_from('>IIII', d, o)
        name_start = o + 16
        name_bytes = d[name_start:name_start+nlen]
        ok = len(name_bytes) == nlen
        try:
            name = name_bytes.decode('ascii')
        except Exception:
            name = name_bytes.decode('latin1', 'replace')
        print(f'  [{i:3}] @{o:06X} hash={h:08X} f1={f1:08X} f2={f2:08X} nlen={nlen:3} name={name!r} end={name_start+nlen:06X}')
        o = name_start + nlen
        if o % 4: o += 4 - (o % 4)
    print(f'  table parsed end={o:06X}, next bytes:', d[o:o+64].hex(' '))
    return d

scripts = os.path.join(ROOT,'USRDIR','scripts.rcf')
shaders = os.path.join(ROOT,'USRDIR','shaders.rcf')
art = os.path.join(ROOT,'USRDIR','art.rcf')
parse(scripts)
parse(shaders)
# for art only read the header/table region
with open(art,'rb') as f:
    head = f.read(0x200000)
print('==== art.rcf header')
print('hdr words:', ' '.join(f'{be32(head,o):08X}' for o in range(0x20, 0x3C, 4)))
print('strings in first 64K:')
import re
for m in re.finditer(rb'[ -~]{6,}', head[:0x10000]):
    print(f'  {m.start():06X}: {m.group().decode("ascii")!r}')
