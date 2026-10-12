"""Draw the original page-turn book mark; Pillow is needed only to regenerate."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets'
OUT.mkdir(exist_ok=True)
SCALE=4
image=Image.new('RGBA',(256*SCALE,256*SCALE))
draw=ImageDraw.Draw(image)
paths=[]

def path(commands,fill,stroke=None,width=3):
    points=[]; current=None; svg=[]
    for command in commands:
        op,*v=command; svg.append(op+' '.join(map(str,v)))
        if op in ('M','L'):
            current=tuple(v); points.append(current)
        elif op=='Q':
            start=current; control=tuple(v[:2]); end=tuple(v[2:])
            for i in range(1,41):
                t=i/40; u=1-t
                points.append(tuple(u*u*start[j]+2*u*t*control[j]+t*t*end[j] for j in range(2)))
            current=end
        elif op=='C':
            start=current; a=tuple(v[:2]); b=tuple(v[2:4]); end=tuple(v[4:])
            for i in range(1,41):
                t=i/40; u=1-t
                points.append(tuple(u**3*start[j]+3*u*u*t*a[j]+3*u*t*t*b[j]+t**3*end[j] for j in range(2)))
            current=end
        elif op=='Z': points.append(points[0])
    coords=[(round(x*SCALE),round(y*SCALE)) for x,y in points]
    if fill: draw.polygon(coords,fill=fill)
    if stroke: draw.line(coords,fill=stroke,width=width*SCALE,joint='curve')
    paths.append(f'<path d="{" ".join(svg)}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>')

path([('M',24,82),('Q',76,65,128,100),('Q',178,65,232,82),('L',232,211),
      ('Q',178,193,128,224),('Q',77,193,24,211),('Z',)],'#239e7c','#147058',4)
path([('M',30,69),('Q',76,51,124,91),('L',124,201),('Q',78,169,30,186),('Z',)],'#e5f4eb','#b0d9c5',3)
path([('M',132,91),('Q',178,51,226,69),('L',226,186),('Q',178,169,132,201),('Z',)],'#f4f6e9','#b0d9c5',3)
for y in (99,125,151):
    path([('M',49,y),('Q',75,y-3,103,y+13)],None,'#83bda6',5)
    path([('M',153,y+13),('Q',179,y-3,207,y)],None,'#83bda6',5)
path([('M',128,201),('L',128,91),('Q',137,53,181,29),('L',181,133),
      ('Q',150,152,128,201),('Z',)],'#fff9e8','#70b894',3)
path([('M',128,203),('L',128,218)],None,'#126c55',4)
svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'+''.join(paths)+'</svg>\n'
(OUT/'book-turn.svg').write_text(svg,encoding='utf-8')
image=image.resize((256,256),Image.Resampling.LANCZOS)
image.save(OUT/'book-turn.png')
image.save(OUT/'book-turn.ico',sizes=[(s,s) for s in (16,20,24,32,40,48,64,128,256)])
preview=Image.new('RGB',(560,200),'#101820')
for size,x in ((128,20),(64,200),(48,300),(32,380),(16,440)):
    icon=image.resize((size,size),Image.Resampling.LANCZOS)
    preview.paste(icon,(x,(200-size)//2),icon)
preview.save(OUT/'book-turn-preview.png')
print('ICON_OK: assets/book-turn.ico + png + svg',flush=True)
