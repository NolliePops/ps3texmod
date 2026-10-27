import struct
d = open(r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\scripts.rcf','rb').read()

def runs(data, lo, hi, minlen=4):
    out=[]
    i=lo
    while i<hi:
        if data[i]!=0:
            j=i
            while j<hi and data[j]!=0:
                j+=1
            if j-i>=minlen:
                out.append((i,j-1,data[i:j]))
            i=j
        else:
            i+=1
    return out

print('nonzero runs 0x900..0x1000:')
for a,b,c in runs(d,0x900,0x1000,3):
    print(f'  {a:#06x}-{b:#06x} len={b-a+1}: {c[:80].hex(" ")} | {c[:80]!r}')

print('\nnonzero runs 0x93c..0x1000 (short too):')
for a,b,c in runs(d,0x93c,0x1000,1):
    print(f'  {a:#06x}-{b:#06x} len={b-a+1}: {c[:80].hex(" ")}')

print('\nfirst 0x100 bytes of data area:')
print(d[0x1000:0x1080])

# check data area size
print('\nlast 0x300 before 0x1b000:')
print(d[0x1ad00:0x1b000])

# parse entries
o=0x808
for i in range(7):
    h=d[o:o+4]
    f1=struct.unpack_from('<I',d,o+4)[0]
    f2=struct.unpack_from('<I',d,o+8)[0]
    nlen=struct.unpack_from('<I',d,o+12)[0]
    name=d[o+16:o+16+nlen]
    print(f'entry {i} @0x{o:X} hash={h.hex()} f1={f1:#x} f2={f2:#x} nlen={nlen} name={name!r}')
    o += 16+nlen+3
print('end', hex(o))
