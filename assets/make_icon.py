"""Genera el logo de OpenView con fondo transparente: assets/icon.ico, logo.png y logo.svg.
Solo hace falta si cambias el logo."""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
TEAL = (15, 157, 138)       # #0f9d8a  (acento del tema claro)
MINT = (44, 194, 171)       # #2cc2ab  (acento del tema oscuro)

S, K = 1024, 4              # se dibuja 4 veces más grande y se reduce: bordes suaves
B = S * K
im = Image.new("RGBA", (B, B), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
c, r, w = B // 2, int(B * 0.43), int(B * 0.062)
d.ellipse((c - r, c - r, c + r, c + r), outline=TEAL + (255,), width=w)                    # esfera
ry = int(r * 0.39)
d.ellipse((c - r, c - ry, c + r, c + ry), outline=MINT + (255,), width=int(w * 1.1))       # horizonte
rd = int(r * 0.2)
d.ellipse((c - rd, c - rd, c + rd, c + rd), fill=MINT + (255,))                            # punto de vista
im = im.resize((S, S), Image.LANCZOS)

im.save(os.path.join(HERE, "logo.png"))
im.save(os.path.join(HERE, "icon.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="512" height="512">
  <circle cx="50" cy="50" r="43" fill="none" stroke="#0f9d8a" stroke-width="6.2"/>
  <ellipse cx="50" cy="50" rx="43" ry="16.8" fill="none" stroke="#2cc2ab" stroke-width="6.8"/>
  <circle cx="50" cy="50" r="8.6" fill="#2cc2ab"/>
</svg>
"""
with open(os.path.join(HERE, "logo.svg"), "w", encoding="utf-8", newline="\n") as f:
    f.write(svg)
print("logo generado")
