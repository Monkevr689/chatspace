"""Draws AppIcon.icns for the Mac app. Needs Pillow; only run it to change the icon.

    python3 make_icon.py
"""

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # macOS icon grid: 824 px rounded square centred on a 1024 canvas


def draw():
    big = S * 2  # draw at double size, then scale down for smooth edges
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    k = big / S

    def box(x0, y0, x1, y1):
        return [round(v * k) for v in (x0, y0, x1, y1)]

    # Background: rounded square with a vertical blue gradient.
    grad = Image.new("RGBA", (big, big))
    gd = ImageDraw.Draw(grad)
    top, bottom = (43, 140, 222), (14, 76, 150)
    for y in range(big):
        t = y / big
        gd.line([(0, y), (big, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box(100, 100, 924, 924), radius=round(185 * k), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # Document with a folded corner.
    d.polygon([(v * k) for v in (300, 220, 600, 220, 724, 344, 724, 800, 300, 800)],
              fill=(255, 255, 255))
    d.polygon([(v * k) for v in (600, 220, 600, 344, 724, 344)], fill=(200, 222, 243))
    for y, x1 in ((420, 640), (500, 640), (580, 560)):
        d.rounded_rectangle(box(370, y - 18, x1, y + 18), radius=round(18 * k), fill=(43, 120, 200))
    # Green download badge.
    d.ellipse(box(560, 580, 820, 840), fill=(30, 150, 85))
    d.line([(v * k) for v in (690, 640, 690, 770)], fill="white", width=round(34 * k))
    d.line([(v * k) for v in (632, 715, 690, 775, 748, 715)], fill="white", width=round(34 * k),
           joint="curve")
    return img.resize((S, S), Image.LANCZOS)


def icns(img):
    # Each entry is a PNG at the size macOS expects for that type code.
    types = [("icp4", 16), ("icp5", 32), ("ic11", 32), ("ic12", 64), ("ic07", 128),
             ("ic13", 256), ("ic08", 256), ("ic14", 512), ("ic09", 512), ("ic10", 1024)]
    chunks = b""
    for code, px in types:
        buf = io.BytesIO()
        img.resize((px, px), Image.LANCZOS).save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        chunks += code.encode() + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(chunks) + 8) + chunks


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    image = draw()
    (here / "AppIcon.icns").write_bytes(icns(image))
    print("wrote AppIcon.icns")
