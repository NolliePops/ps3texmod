import struct, os

ROOT = r"C:\Users\wedde\Downloads\prototypeps3modlauncher\Prototype (USA, Korea) (En,Fr)\PS3_GAME"

def parse(path, max_entries=12, tail=0):
    d = open(path,'rb').read()
    hdr = struct.unpack_from('>7I', d, 0x20)
    version, hsize, count, table_off, f30, f34, f38 = hdr
    print(f'==== {os.path.basename(path)} size={len(d)}')
    print(f'version={version:08X} hsize={hsize} count={count} table_off=0x{table_off:X} f30=0x{f30:X} f34={f34} f38=0x{f38:X}')
    print(f'table prefix: {d[table_off:table_off+8].hex(" ")}')
    o = table_off + 8
    entries = []
    ok_all = True
    for i in range(count):
        if o + 16 > len(d):
            print('  ! truncated at', i); ok_all=False; break
        h, f1, f2, nlen = struct.unpack_from('>IIII', d, o)
        if nlen == 0 or nlen > 300 or o+16+nlen > len(d):
            print(f'  ! bad entry {i} @0x{o:X} h={h:08X} f1={f1:08X} f2={f2:08X} nlen={nlen}')
            ok_all=False; break
        raw = d[o+16:o+16+nlen]
        name = raw.decode('ascii', 'replace')
        entries.append((o, h, f1, f2, name))
        o += 16 + nlen
        if o % 4: o += 4 - (o % 4)
    print(f'parsed {len(entries)}/{count} entries, end=0x{o:X}, ok={ok_all}')
    show = entries[:max_entries] + (entries[-tail:] if tail and len(entries)>max_entries else [])
    for o,h,f1,f2,name in show:
        print(f'  @{o:06X} h={h:08X} f1={f1:08X} ({f1>>16:04X}:{f1&0xFFFF:04X}) f2={f2:08X} {name!r}')
    # data region start guess
    print(f'  data begins at 0x{o:X}? next 32: {d[o:o+32].hex(" ")}')
    return d, hdr, entries, o

scripts = os.path.join(ROOT,'USRDIR','scripts.rcf')
shaders = os.path.join(ROOT,'USRDIR','shaders.rcf')
art = os.path.join(ROOT,'USRDIR','art.rcf')
for p, mx, tl in ((scripts,10,3),(shaders,12,0),(art,12,6)):
    parse(p, mx, tl)
    print()
