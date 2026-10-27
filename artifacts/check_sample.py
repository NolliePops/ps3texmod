import sys
sys.path.insert(0, r"C:\Users\wedde\Downloads\prototypeps3modlauncher")
from ps3texmod.dds import parse_dds, decode_level0_rgba
for p in [r"samples\billboards\94CCC0CC_00000000_billboards_1024x1024_01_diffuse.dds", r"samples\billboard-tint\94CCC0CC_billboards_01_tint.dds"]:
    raw = open(p,"rb").read(); info = parse_dds(raw)
    rgba, w, h = decode_level0_rgba(raw, info)
    n = w*h
    r = sum(rgba[0::4])/n; g = sum(rgba[1::4])/n; b = sum(rgba[2::4])/n; a = sum(rgba[3::4])/n
    print(p.split("\\")[-1], f"{w}x{h}", "mean RGBA", round(r,1), round(g,1), round(b,1), round(a,1))
