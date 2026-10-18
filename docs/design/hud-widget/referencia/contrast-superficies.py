
def srgb_lin(c):
    c=c/255
    return c/12.92 if c<=0.04045 else ((c+0.055)/1.055)**2.4
def lum(p): 
    r,g,b=[srgb_lin(c) for c in p]; return 0.2126*r+0.7152*g+0.0722*b
def ratio(a,b):
    la,lb=lum(a),lum(b); hi,lo=max(la,lb),min(la,lb); return (hi+0.05)/(lo+0.05)
def over(fg,al,bg): return tuple(fg[i]*al+bg[i]*(1-al) for i in range(3))

INK=(30,27,38)
BD={"blanco puro":(255,255,255),"doc gris":(240,240,240),"wallpaper":(90,70,120),"editor":(22,22,28)}
print(f"{'superficie':>22} | " + " | ".join(f"{k:>12}" for k in BD))
for label, op in [("hud 0.72",0.72),("veil 0.50",0.50),("whisper 0.30",0.30),("halo ~0.62",0.62),("sin fondo 0",0.0)]:
    row=[]
    for bn,bd in BD.items():
        surf=over(INK,op,bd)
        txt=over((255,255,255),0.95,surf)
        r=ratio(txt,surf)
        row.append(f"{r:5.1f} {'OK ' if r>=4.5 else ('AA+' if r>=3 else 'BAJO')}")
    print(f"{label:>22} | " + " | ".join(f"{v:>12}" for v in row))
