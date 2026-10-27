import struct, re
p = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR\scripts.rcf'
d = open(p,'rb').read()
for mag in (b'P3D', b'\xffD3P', b'-- ', b'function', b'Stats', b'credits'):
    offs=[]
    s=0
    while True:
        i=d.find(mag,s)
        if i<0: break
        offs.append(i); s=i+1
        if len(offs)>20: break
    print(mag, [hex(x) for x in offs])

print('\nnon-zero regions from 0x990 to 0x1B000:')
i=0x990
while i<0x1B000:
    if d[i]!=0:
        j=i
        while j<0x1B000 and d[j]!=0: j+=1
        if j-i>=16:
            print(f'  {i:#07x}-{j:#07x} len={j-i}  {d[i:i+60]!r}')
        i=j
    else:
        i+=1
