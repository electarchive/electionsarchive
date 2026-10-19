import os
import csv, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from words import ocr, group, strips, region
from parsers import check, S, save

D = Path(sys.argv[1])
NUM = re.compile(r"^\d[\d,]*$")


def norm(t):
    m = re.fullmatch(r"(\d[\d,.]*?)[^\d.,%]*", t)
    return m[1].replace(",", "").replace(".", "") if m and not re.search(r"\.\d\d$", m[1]) else None


def reconcile(c, mode="row", left=0.3, top=0.0):
    recs = list(csv.reader(open(S / f"tr/{c}.csv")))
    pages = sorted({int(r[0]) for r in recs})
    vis = dict(ocr(D / f"{c}.pdf", pages, "vision", S / "img"))
    rows, bad = {}, []
    cells = {}
    if (S / f"tr/{c}.cells").exists():
        from PIL import Image

        for page, meas, label, ch, *box in csv.reader(open(S / f"tr/{c}.cells")):
            ip = box[4] if len(box) > 4 else page
            x0, y0, x1, y1 = map(float, box[:4])
            im = Image.open(S / "img" / f"{c}-{ip}.png")
            W, H = im.size
            crop = im.convert("L").crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
            crop = crop.resize((crop.width * 4, crop.height * 4))
            from PIL import ImageFilter, ImageOps
            import subprocess

            vr, tr = [], []
            for vi, img in enumerate(
                (
                    crop,
                    crop.filter(ImageFilter.MinFilter(5)),
                    crop.filter(ImageFilter.MinFilter(9)),
                    crop.filter(ImageFilter.GaussianBlur(4)).point(lambda v: 0 if v < 200 else 255),
                )
            ):
                path = S / "img/cells" / f"{c}-{ip}-{x0}-{y0}-v{vi}.png"
                path.parent.mkdir(exist_ok=True)
                ImageOps.expand(img, 40, 255).save(path)
                subprocess.run([str(S / "vision/vision"), str(path)], capture_output=True)
                vtoks = [l.split("\t")[0].strip() for l in Path(str(path) + ".vision.tsv").read_text().splitlines()]
                tes = subprocess.run(
                    ["tesseract", str(path), "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789,"],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                vr.extend(norm(t) for t in vtoks if t)
                tr.append(norm(tes) if tes else None)
            cells[(page, meas, label, ch)] = (vr, tr, (x0, y0, x1, y1), int(ip))
    for page, meas, label, y, n, *rest in recs:
        xmin = float(rest[0]) if rest and mode == "row" else 0
        words = vis[int(page)]
        mid = lambda w: (w[2] + w[4]) / 2
        if mode == "row":
            lab = label.split()
            anchors = [w for w in words if w[0] == lab[-1] and w[1] < left and w[2] > top]
            if len(lab) > 1 and len(anchors) > 1:
                fold = lambda t: t.upper().replace("1", "I").replace("L", "I")
                anchors = [
                    a
                    for a in anchors
                    if any(
                        fold(v[0]) == fold(lab[0]) and abs(mid(v) - mid(a)) < 0.006 and v[3] <= a[1] + 0.01
                        for v in words
                    )
                ]
            if len(anchors) != 1:
                cs = {k: cells.get((page, meas, label, k)) for k in ("YES", "NO")}
                if all(cs[k] and any(v in r for r in cs[k][:2]) for k, v in (("YES", y), ("NO", n))):
                    rows[(meas, label)] = {k: (int(v), cs[k][2], cs[k][3]) for k, v in (("YES", y), ("NO", n))}
                else:
                    bad.append((page, meas, label, "anchor", len(anchors)))
                continue
            a = anchors[0]
            row = sorted(
                [w for w in words if abs(mid(w) - mid(a)) < (a[4] - a[2]) * 0.7 and w[1] > a[3]], key=lambda w: w[1]
            )
            got = {}
            for attempt in (0, 1, 3):
                got = {}
                for key, v in (("YES", y), ("NO", n)):
                    hits = [w for w in row if norm(w[0]) == v and w not in got.values() and w[1] >= xmin]
                    if hits:
                        got[key] = hits[0]
                if len(got) == 2 or attempt == 3:
                    break
                band = (a[2], a[4])
                row = row + [
                    w
                    for w in strips(S / "img" / f"{c}-{page}.png", [band], S / "img/strips", {0: 1, 1: 3}[attempt])[0][
                        "vision"
                    ]
                    if w[1] > a[3]
                ]
            for key, v in (("YES", y), ("NO", n)):
                cell = cells.get((page, meas, label, key))
                if key not in got and cell and any(v in r for r in cell[:2]):
                    got[key] = ("cell", *cell[2])
            for key, v in (("YES", y), ("NO", n)):
                if key not in got:
                    bad.append((page, meas, label, key, v, " ".join(w[0] for w in row)))
            if len(got) == 2 and got["YES"][1] < got["NO"][1] - 0.01:
                rows[(meas, label)] = {
                    k: (int(v), got[k][1:], cells[(page, meas, label, k)][3] if got[k][0] == "cell" else int(page))
                    for k, v in (("YES", y), ("NO", n))
                }
            elif len(got) == 2:
                bad.append((page, meas, label, "order"))
        elif mode == "below":
            pat = rest[0] if rest else r"(?i)measure\s+(?:no\.?\s*)?{}\b"
            lines = group(list(words))
            hdrs = [l for l in lines if re.search(pat.format(meas), " ".join(w[0] for w in l))]
            if len(hdrs) != 1:
                cs = {k: cells.get((page, meas, label, k)) for k in ("YES", "NO")}
                if all(cs[k] and any(v in r for r in cs[k][:2]) for k, v in (("YES", y), ("NO", n))):
                    rows[(meas, label)] = {k: (int(v), cs[k][2], cs[k][3]) for k, v in (("YES", y), ("NO", n))}
                else:
                    bad.append((page, meas, label, "below-anchor", len(hdrs), {k: cs[k] and cs[k][:2] for k in cs}))
                continue
            y0 = hdrs[0][0][2]
            got = {}
            for key, v in (("YES", y), ("NO", n)):
                after = [
                    l
                    for l in lines
                    if l[0][2] > y0 + 0.002
                    and re.fullmatch({"YES": r"Y[EO]S", "NO": r"N[O0]"}[key], l[0][0].rstrip(".:").upper())
                ]
                if not after:
                    bad.append((page, meas, label, key, "nolabel"))
                    continue
                lw = after[0][0]
                l = sorted(
                    [w for w in words if abs(mid(w) - mid(lw)) < (lw[4] - lw[2]) * 0.9 and w[1] > lw[3]],
                    key=lambda w: w[1],
                )
                nums = [w for w in l if norm(w[0]) is not None]
                if nums and norm(nums[0][0]) == v:
                    got[key] = nums[0]
                    continue
                h = lw[4] - lw[2]
                reads = region(
                    S / "img" / f"{c}-{page}.png",
                    (
                        lw[3],
                        lw[2] - h * 0.45,
                        min(1, lw[3] + float(rest[1]) if len(rest) > 1 else 0.75),
                        lw[4] + h * 0.45,
                    ),
                    S / "img/regions",
                )
                hits = [r for r in reads if norm(r[1]) == v]
                if not hits:
                    vx = [w for w in words if norm(w[0]) is not None and w[2] > y0 and w[1] > 0.3]
                    cx = sorted(w[3] for w in vx)[len(vx) // 2] if vx else 0.6
                    for sc in (4, 2):
                        reads = region(
                            S / "img" / f"{c}-{page}.png",
                            (cx - 0.08, lw[2] - h * 0.5, cx + 0.015, lw[4] + h * 0.5),
                            S / "img/regions",
                            sc,
                        )
                        hits = [r for r in reads if norm(r[1]) == v]
                        if hits:
                            break
                if hits:
                    got[key] = (hits[0][1], *hits[0][2])
                else:
                    bad.append((page, meas, label, key, v, [w[0] for w in l]))
            for key, v in (("YES", y), ("NO", n)):
                cell = cells.get((page, meas, label, key))
                if key not in got and cell and any(v in r for r in cell[:2]):
                    got[key] = ("cell", *cell[2])
                    bad = [b for b in bad if b[:4] != (page, meas, label, key)]
            if len(got) == 2:
                rows[(meas, label)] = {
                    k: (int(v), got[k][1:], cells[(page, meas, label, k)][3] if got[k][0] == "cell" else int(page))
                    for k, v in (("YES", y), ("NO", n))
                }
        elif mode == "grid":
            heads, cols = rest[0], rest[1].split()
            hdr = [
                w
                for w in words
                if re.search(
                    heads.format(meas),
                    " ".join(v[0] for v in words if abs(mid(v) - mid(w)) < 0.004 and v[1] <= w[1] + 0.3),
                )
                and re.fullmatch(heads.format(meas).split()[0], w[0])
            ]
            hdr = sorted(hdr, key=lambda w: w[2])
            ctoks = sorted([w for w in words if w[0] in cols], key=lambda w: w[1])
            ctoks = [w for w in ctoks if abs(mid(w) - mid(ctoks[0])) < 0.006] if ctoks else []
            if cols[0].startswith("@"):
                ctoks = [None] * int(cols[0][1:])
            if not hdr or len(ctoks) != len(cols) and not cols[0].startswith("@"):
                bad.append((page, meas, label, "grid-anchor", len(hdr), [w[0] for w in ctoks]))
                continue
            idx = int(label.rsplit(":", 1)[1]) if cols[0].startswith("@") else [w[0] for w in ctoks].index(label)
            got = {}
            for key, v in (("YES", y), ("NO", n)):
                labs = sorted([w for w in words if w[0].upper() == key and w[2] > hdr[0][2]], key=lambda w: w[2])
                if not labs:
                    bad.append((page, meas, label, key, "nolabel"))
                    continue
                lw = labs[0]
                row = sorted(
                    [
                        w
                        for w in words
                        if abs(mid(w) - mid(lw)) < (lw[4] - lw[2]) * 0.8 and w[1] > lw[3] and norm(w[0]) is not None
                    ],
                    key=lambda w: w[1],
                )
                if idx < len(row) and norm(row[idx][0]) == v:
                    got[key] = row[idx]
                else:
                    bad.append((page, meas, label, key, v, [w[0] for w in row]))
            if len(got) == 2:
                rows[(meas, label)] = {
                    k: (int(v), got[k][1:], cells[(page, meas, label, k)][3] if got[k][0] == "cell" else int(page))
                    for k, v in (("YES", y), ("NO", n))
                }
    return rows, bad


if __name__ == "__main__":
    c = sys.argv[2]
    rows, bad = reconcile(c, *sys.argv[3:4], *map(float, sys.argv[4:]))
    for b in bad:
        print("BAD", b)
    print(len(rows))
    if check(c, rows):
        save(c, rows)
