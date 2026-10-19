import os
import csv, subprocess, re, sys
from pathlib import Path
from PIL import Image

S = Path(os.environ["OR_WORK"])
c = sys.argv[1]
recs = list(csv.reader(open(S / f"tr/{c}.csv")))
pm = {}
for r in recs:
    pm.setdefault(int(r[0]), []).append(r)


def rd(p, box):
    from PIL import ImageFilter, ImageOps

    im = Image.open(S / f"img/{c}-{p}.png").convert("L")
    W, H = im.size
    x0, y0, x1, y1 = box
    cr = im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
    cr = cr.resize((cr.width * 4, cr.height * 4))
    got = set()
    for vi, img in enumerate(
        (
            cr,
            cr.filter(ImageFilter.MinFilter(5)),
            cr.filter(ImageFilter.MinFilter(9)),
            cr.filter(ImageFilter.GaussianBlur(4)).point(lambda v: 0 if v < 200 else 255),
        )
    ):
        img = ImageOps.expand(img, 40, 255)
        path = S / "img/cells" / f"{c}-{p}-{x0}-{y0}-v{vi}.png"
        path.parent.mkdir(exist_ok=True)
        img.save(path)
        subprocess.run([str(S / "vision/vision"), str(path)], capture_output=True)
        v = Path(str(path) + ".vision.tsv").read_text().split("\t")[0].strip()
        t = subprocess.run(
            ["tesseract", str(path), "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789,"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        got |= {re.sub(r"[^\d]", "", v), re.sub(r"[^\d]", "", t)}
    return got


out = []
for p, rs in pm.items():
    w = [l.split("\t") for l in open(S / f"img/{c}-{p}.png.vision.tsv").read().splitlines()]
    w = [(t, *map(float, b)) for t, *b in w]
    est = sorted(
        [a[2] for a in w if a[0][:2] in ("To", "Ta", "lo", "To") and 0.09 < a[1] < 0.13]
        + [a[2] - 0.016 for a in w if a[0][:2] in ("Ov", "0v", "OV") and 0.05 < a[1] < 0.09]
        + [a[2] - 0.0305 for a in w if a[0][:2] in ("Un", "Ur") and 0.05 < a[1] < 0.09]
    )
    tot = []
    for e in est:
        if tot and e - tot[-1][-1] < 0.008:
            tot[-1].append(e)
        else:
            tot.append([e])
    tot = [("", 0, sorted(g)[len(g) // 2]) for g in tot]
    nums = [a[3] for a in w if a[0].replace(",", "").isdigit() and 0.5 < a[1] < 0.62]
    xm = sorted(nums)[len(nums) // 2]
    blocks = []
    for t in tot:
        bx = {}
        for k, off in (("YES", -0.0305), ("NO", -0.0150)):
            y = t[2] + off
            lab = [a for a in w if a[1] < 0.1 and abs(a[2] - y) < 0.006 and a[0][:1].upper() == k[0]]
            if lab:
                y = lab[0][2]
            bx[k] = (round(xm - 0.065, 4), round(y - 0.004, 4), round(xm + 0.01, 4), round(y + 0.014, 4))
        blocks.append((bx, {k: rd(p, b) for k, b in bx.items()}))
    idx = []
    for r in rs:
        hit = [i for i, (bx, rv) in enumerate(blocks) if r[3] in rv["YES"] and r[4] in rv["NO"]]
        idx.append(hit)
    offs = {h[0] - j for j, h in enumerate(idx) if len(h) == 1}
    part = [[i for i, (bx, rv) in enumerate(blocks) if r[3] in rv["YES"] or r[4] in rv["NO"]] for r in rs]
    offs |= {h[0] - j for j, h in enumerate(part) if len(h) == 1 and not idx[j]}
    ok = len(offs) == 1 and all(0 <= list(offs)[0] + j < len(blocks) for j in range(len(rs)))
    print(p, len(tot), idx, part, offs, "OK" if ok else "FAIL")
    if ok:
        o = list(offs)[0]
        for j, r in enumerate(rs):
            for k in ("YES", "NO"):
                out.append((p, r[1], r[2], k, *blocks[o + j][0][k]))
with open(S / f"tr/{c}.cells", "w") as f:
    for o in out:
        f.write(",".join(map(str, o)) + "\n")
