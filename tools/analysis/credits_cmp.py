import struct, os, hashlib, zlib
root = r'Prototype (USA, Korea) (En,Fr)\PS3_GAME\USRDIR'
d = open(os.path.join(root,'scripts.rcf'),'rb').read()
names = [b'scripts\\credits_english.txt', b'scripts\\credits_french.txt', b'scripts\\credits_italian.txt', b'scripts\\credits_spanish.txt']
offs = {b'scripts\\credits_english.txt':0x1000, b'scripts\\credits_french.txt':0x6800, b'scripts\\credits_italian.txt':0xC000, b'scripts\\credits_spanish.txt':0x11800}
sizes = {b'scripts\\credits_english.txt':22457, b'scripts\\credits_french.txt':22457, b'scripts\\credits_italian.txt':22458, b'scripts\\credits_spanish.txt':22456}
for n in names:
    blob = d[offs[n]:offs[n]+sizes[n]]
    print(n, 'crc', hex(zlib.crc32(blob)), 'md5', hashlib.md5(blob).hexdigest()[:12], 'first40', blob[:40])
print()
print('english==french?', d[0x1000:0x1000+22457] == d[0x6800:0x6800+22457])
print('english[:60]', d[0x1000:0x103C])
print('french [:60]', d[0x6800:0x683C])
print('italian[:60]', d[0xC000:0xC03C])
print('spanish[:60]', d[0x11800:0x1183C])
