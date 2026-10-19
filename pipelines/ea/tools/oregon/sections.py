import os
import re, sys
from pathlib import Path
from PIL import Image, ImageDraw

S = Path(os.environ["OR_WORK"])
c, pages, x0, x1 = sys.argv[1], [int(p) for p in sys.argv[2].split(",")], float(sys.argv[3]), float(sys.argv[4])
scale = float(sys.argv[5]) if len(sys.argv) > 5 else 0.5
tiles = []
for p in pages:
    ys = []
    for eng in ("vision", "tess"):
        f = S / "img" / f"{c}-{p}.png.{eng}.tsv"
        if not f.exists():
            continue
        if eng == "vision":
            ws = [(l.split("\t")[0], float(l.split("\t")[2])) for l in f.read_text().splitlines()]
        else:
            rows = [l.split("\t") for l in f.read_text().splitlines()[1:]]
            H = next(int(r[9]) for r in rows if r[0] == "1")
            ws = [(r[11], int(r[7]) / H) for r in rows if r[0] == "5" and len(r) > 11]
        ys += [
            y for (t, y), (t2, _) in zip(ws, ws[1:] + [("", 0)]) if t.lower() == "measure" and re.match(r"10[2-6]", t2)
        ]
    if not ys:
        print("none", p)
        continue
    im = Image.open(S / "img" / f"{c}-{p}.png")
    W, H = im.size
    crop = im.crop((int(x0 * W), int((min(ys) - 0.01) * H), int(x1 * W), int(min(1, max(ys) + 0.085) * H)))
    crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))
    tag = Image.new("L", (crop.width, 34), 200)
    ImageDraw.Draw(tag).text((4, 4), f"page {p}", fill=0, font_size=26)
    t = Image.new("L", (crop.width, crop.height + 34), 255)
    t.paste(tag, (0, 0))
    t.paste(crop, (0, 34))
    tiles.append(t)
k, part, n = 0, [], 0


def flush(part, n):
    out = Image.new("L", (max(t.width for t in part), sum(t.height + 6 for t in part)), 128)
    y = 0
    for t in part:
        out.paste(t, (0, y))
        y += t.height + 6
    out.save(S / f"sec-{c}-{n}.png")
    print(n, out.size)


h = 0
for t in tiles:
    if part and h + t.height > 2400:
        flush(part, n)
        n += 1
        part, h = [], 0
    part.append(t)
    h += t.height + 6
flush(part, n)
