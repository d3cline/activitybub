"""Rebuild Activity Bub's original 32-pixel sunglasses icon; Pillow is dev-only."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/"activity_bub"/"assets"
# All coordinates lie on the same 32×32 pixel grid. No antialiasing.
RECTS=[
    (4,0,24,32,"#111923"),(0,4,32,24,"#111923"),(2,2,28,28,"#111923"),
    (2,10,28,3,"#b7ef6a"),(4,11,10,8,"#b7ef6a"),(6,19,6,2,"#b7ef6a"),
    (18,11,10,8,"#b7ef6a"),(20,19,6,2,"#b7ef6a"),(14,12,4,3,"#b7ef6a"),
    (6,13,6,5,"#182333"),(20,13,6,5,"#182333"),
    (7,13,2,2,"#f3ffff"),(9,15,2,2,"#6cdef1"),
    (21,13,2,2,"#f3ffff"),(23,15,2,2,"#6cdef1"),
    (26,4,2,6,"#6cdef1"),(24,6,6,2,"#6cdef1"),
]


def main():
    ASSETS.mkdir(parents=True,exist_ok=True)
    image=Image.new("RGBA",(32,32))
    draw=ImageDraw.Draw(image)
    for x,y,w,h,color in RECTS:
        draw.rectangle((x,y,x+w-1,y+h-1),fill=color)
    image.resize((512,512),Image.Resampling.NEAREST).save(ASSETS/"activity-bub.png")
    # Pillow's ICO writer accepts per-size source images, retaining crisp pixels.
    sizes=[16,32,48,64,128,256]
    frames=[image.resize((n,n),Image.Resampling.NEAREST) for n in sizes]
    frames[-1].save(ASSETS/"activity-bub.ico",format="ICO",sizes=[(n,n) for n in sizes],append_images=frames[:-1])
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 32 32" shape-rendering="crispEdges">'
    svg+=''.join(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{c}"/>' for x,y,w,h,c in RECTS)
    (ASSETS/"activity-bub.svg").write_text(svg+'</svg>\n')
    print("Built sunglasses PNG, ICO, and SVG")


if __name__=="__main__":
    main()
