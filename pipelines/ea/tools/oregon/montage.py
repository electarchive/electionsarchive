import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = Path(os.environ["OR_WORK"])
c, x0, x1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
sus = len(sys.argv) > 4
bad = [b for b in json.load(open(S / f"out/{c}.bad.json")) if sus or b[4] != "suspect"]
tiles = []
for b in bad:
    p, i, (y0, y1) = b[0], b[1], b[-1]
    im = Image.open(S / "img" / f"{c}-{p}.png")
    W, H = im.size
    pad = (y1 - y0) * 0.5
    crop = im.crop((int(x0 * W), int((y0 - pad) * H), int(x1 * W), int((y1 + pad) * H)))
    crop = crop.resize((int(crop.width * 0.6), int(crop.height * 0.6)))
    tag = Image.new("L", (190, crop.height), 255)
    ImageDraw.Draw(tag).text((4, 4), f"{p},{i} {b[2]}", fill=0, font_size=26)
    row = Image.new("L", (tag.width + crop.width, crop.height), 255)
    row.paste(tag, (0, 0))
    row.paste(crop, (tag.width, 0))
    tiles.append(row)
for k in range(0, len(tiles), 28):
    part = tiles[k : k + 28]
    out = Image.new("L", (max(t.width for t in part), sum(t.height + 6 for t in part)), 128)
    y = 0
    for t in part:
        out.paste(t, (0, y))
        y += t.height + 6
    out.save(S / f"look-{c}-{k // 28}.png")
    print(k // 28, out.size)
