import re
p = r'tools\analysis\hash_hunt2.py'
s = open(p, encoding='utf-8').read()
start = s.index('def hashlittle(')
end = s.index('def superfasthash(')
new = '''def hashlittle(data, init=0):
    a = bb = c = (0xdeadbeef + len(data) + init) & M
    i = 0
    n = len(data)
    while n - i > 12:
        a = (a + int.from_bytes(data[i:i+4],'little')) & M
        bb = (bb + int.from_bytes(data[i+4:i+8],'little')) & M
        c = (c + int.from_bytes(data[i+8:i+12],'little')) & M
        a = (a - c) & M; a ^= ((c << 4) | (c >> 28)) & M; c = (c + bb) & M
        bb = (bb - a) & M; bb ^= ((a << 6) | (a >> 26)) & M; a = (a + c) & M
        c = (c - bb) & M; c ^= ((bb << 8) | (bb >> 24)) & M; bb = (bb + a) & M
        a = (a - c) & M; a ^= ((c << 16) | (c >> 16)) & M; c = (c + bb) & M
        bb = (bb - a) & M; bb ^= ((a << 19) | (a >> 13)) & M; a = (a + c) & M
        c = (c - bb) & M; c ^= ((bb << 4) | (bb >> 28)) & M; bb = (bb + a) & M
        i += 12
    rem = data[i:]
    if rem:
        for j, x in enumerate(rem):
            if j < 4: a = (a + (x << (8*j))) & M
            elif j < 8: bb = (bb + (x << (8*(j-4)))) & M
            else: c = (c + (x << (8*(j-8)))) & M
        c ^= bb; c = (c - (((bb << 14) | (bb >> 18)) & M)) & M
        a ^= c; a = (a - (((c << 11) | (c >> 21)) & M)) & M
        bb ^= a; bb = (bb - (((a << 25) | (a >> 7)) & M)) & M
        c ^= bb; c = (c - (((bb << 16) | (bb >> 16)) & M)) & M
        a ^= c; a = (a - (((c << 4) | (c >> 28)) & M)) & M
        bb ^= a; bb = (bb - (((a << 14) | (a >> 18)) & M)) & M
        c ^= bb; c = (c - (((bb << 24) | (bb >> 8)) & M)) & M
    return c

'''
open(p,'w',encoding='utf-8').write(s[:start] + new + s[end:])
print('patched')
