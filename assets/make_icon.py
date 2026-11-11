"""Genera assets/icon.ico (logo de Pano360 Downloader). Solo hace falta si cambias el icono."""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((0, 0, S - 1, S - 1), radius=270, fill=(15, 157, 138, 255))
c, r, w = S // 2, 300, 52
d.ellipse((c - r, c - r, c + r, c + r), outline=(255, 255, 255, 140), width=w)
d.ellipse((c - r, c - 118, c + r, c + 118), outline=(255, 255, 255, 255), width=w + 6)
d.ellipse((c - 74, c - 74, c + 74, c + 74), fill=(255, 255, 255, 255))
im.save(os.path.join(HERE, "icon.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icono generado")
