import struct
p = r"Prototype (USA, Korea) (En,Fr)\PS3_GAME\PARAM.SFO"
data = open(p, "rb").read()
magic, ver, keyoff, dataoff, count = struct.unpack_from("<4sIIII", data, 0)
for i in range(count):
    ko, fmt, dl, dmax, do = struct.unpack_from("<HHIII", data, 0x14 + i*16)
    k = data[keyoff+ko:data.index(b"\0", keyoff+ko)].decode()
    v = data[dataoff+do:dataoff+do+dl]
    print(f"{k:24} fmt=0x{fmt:04X} value={v!r}")
