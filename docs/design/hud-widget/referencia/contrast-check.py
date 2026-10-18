def srgb_lin(c):
    c = c/255
    return c/12.92 if c <= 0.04045 else ((c+0.055)/1.055)**2.4

def lum(rgb):
    r,g,b = [srgb_lin(c) for c in rgb]
    return 0.2126*r + 0.7152*g + 0.0722*b

def ratio(a,b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la,lb), min(la,lb)
    return (hi+0.05)/(lo+0.05)

def over(fg, alpha, bg):
    return tuple(fg[i]*alpha + bg[i]*(1-alpha) for i in range(3))

TINT = (30,27,38)          # material HUD oscuro
BACKDROPS = {
    "blanco puro (peor caso)": (255,255,255),
    "documento gris claro":    (240,240,240),
    "wallpaper medio":         (90,70,120),
    "editor oscuro":           (22,22,28),
}
TEXT = {
    "primario   .95": 0.95,
    "secundario .58": 0.58,
    "terciario  .36": 0.36,
}

for op in (0.60, 0.72, 0.84):
    print(f"\n=== fill del vidrio a {int(op*100)}% ===")
    for bname, bd in BACKDROPS.items():
        surface = over(TINT, op, bd)
        row = []
        for tname, ta in TEXT.items():
            txt = over((255,255,255), ta, surface)
            r = ratio(txt, surface)
            flag = "OK " if r >= 4.5 else ("AA+" if r >= 3.0 else "BAJO")
            row.append(f"{tname}: {r:4.1f} {flag}")
        print(f"  {bname:26s} | " + " | ".join(row))

print("\n\n########## AJUSTE PROPUESTO ##########")
print("fill 72% · secundario .62 · terciario .48\n")
TEXT2 = {"primario   .95":0.95, "secundario .62":0.62, "terciario  .48":0.48}
for bname, bd in BACKDROPS.items():
    surface = over(TINT, 0.72, bd)
    row=[]
    for tname, ta in TEXT2.items():
        txt = over((255,255,255), ta, surface)
        r = ratio(txt, surface)
        flag = "OK " if r>=4.5 else ("AA+" if r>=3.0 else "BAJO")
        row.append(f"{tname}: {r:4.1f} {flag}")
    print(f"  {bname:26s} | " + " | ".join(row))
