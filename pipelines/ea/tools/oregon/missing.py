import os
import json, re, sys
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from words import group
from scan3 import between

S = Path(os.environ["OR_WORK"])
c, pages, x0, x1 = sys.argv[1], [int(p) for p in sys.argv[2].split(",")], float(sys.argv[3]), float(sys.argv[4])
labels = sys.argv[5].split(",") if len(sys.argv) > 5 else None
rows = json.load(open(S / f"out/{c}.json"))
allp = sorted({r[2] for r in rows}) if labels is None else labels
have = {(r[1], r[2]) for r in rows}
mrx = re.compile(r"(?i)measure\s+(10[2-6])")
tiles, miss = [], []
for p in pages:
    words = [
        (t, float(a), float(b), float(cc), float(d))
        for t, a, b, cc, d in (l.split("\t") for l in (S / "img" / f"{c}-{p}.png.vision.tsv").read_text().splitlines())
    ]
    lines = group(words)
    meas = next((m[1] for l in lines if (m := mrx.search(" ".join(w[0] for w in l)))), None)
    for lab in allp:
        if (meas, lab) in have:
            continue
        line = [
            l
            for l in lines
            if l[0][1] < 0.3 and re.match(rf"(?:.*?Precinct\s*)?{re.escape(lab)}\b", " ".join(w[0] for w in l))
        ]
        if len(line) != 1:
            line = between(lines, lab)
        if line is None or len(line) != 1:
            print("noline", p, meas, lab)
            continue
        y0, y1 = min(w[2] for w in line[0]), max(w[4] for w in line[0])
        im = Image.open(S / "img" / f"{c}-{p}.png")
        W, H = im.size
        pad = (y1 - y0) * 0.25
        crop = im.crop((int(x0 * W), int((y0 - pad) * H), int(x1 * W), int((y1 + pad) * H)))
        crop = crop.resize((int(crop.width * 0.6), int(crop.height * 0.6)))
        tag = Image.new("L", (200, crop.height), 255)
        ImageDraw.Draw(tag).text((4, 2), f"{p} {meas} {lab}", fill=0, font_size=22)
        row = Image.new("L", (tag.width + crop.width, crop.height), 255)
        row.paste(tag, (0, 0))
        row.paste(crop, (tag.width, 0))
        tiles.append(row)
        have.add((meas, lab))
for k in range(0, len(tiles), 40):
    part = tiles[k : k + 40]
    out = Image.new("L", (max(t.width for t in part), sum(t.height + 4 for t in part)), 128)
    y = 0
    for t in part:
        out.paste(t, (0, y))
        y += t.height + 4
    out.save(S / f"miss-{c}-{k // 40}.png")
    print(k // 40, out.size)
print(len(tiles))
