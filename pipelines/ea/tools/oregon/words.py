import os
import html, re, subprocess
from pathlib import Path


def pages(pdf):
    xml = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    out = []
    for m in re.finditer(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', xml, re.S):
        w, h = float(m[1]), float(m[2])
        words = [
            (html.unescape(t), float(a) / w, float(b) / h, float(c) / w, float(d) / h)
            for a, b, c, d, t in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', m[3]
            )
        ]
        words.sort(key=lambda x: (round(x[2] * 400), x[1]))
        lines = []
        for wd in words:
            if lines and abs(lines[-1][0][2] - wd[2]) < 0.004:
                lines[-1].append(wd)
            else:
                lines.append([wd])
        out.append([sorted(l, key=lambda x: x[1]) for l in lines])
    return out


def group(words):
    words.sort(key=lambda x: ((x[2] + x[4]) / 2, x[1]))
    lines = []
    for wd in words:
        mid = (wd[2] + wd[4]) / 2
        if lines and abs(lines[-1][-1] - mid) < 0.005:
            lines[-1][0].append(wd)
        else:
            lines.append([[wd], mid])
    return [sorted(l, key=lambda x: x[1]) for l, _ in lines]


def ocr(pdf, pagenos, engine, cache):
    from concurrent.futures import ThreadPoolExecutor

    cache.mkdir(parents=True, exist_ok=True)

    def one(p):
        img = cache / f"{pdf.stem}-{p}.png"
        if not img.exists():
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "300",
                    "-gray",
                    "-png",
                    "-f",
                    str(p),
                    "-l",
                    str(p),
                    "-singlefile",
                    str(pdf),
                    str(img)[:-4],
                ],
                check=True,
            )
        if engine == "vision":
            tsv = Path(str(img) + ".vision.tsv")
            if not tsv.exists():
                subprocess.run([str(Path(os.environ["OR_WORK"]) / "vision/vision"), str(img)], check=True)
            words = [
                (t, float(a), float(b), float(c), float(d))
                for t, a, b, c, d in (l.split("\t") for l in tsv.read_text().splitlines())
            ]
        else:
            tsv = Path(str(img) + ".tess.tsv")
            if not tsv.exists():
                tsv.write_text(
                    subprocess.run(
                        ["tesseract", str(img), "-", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
                    ).stdout
                )
            rows = [l.split("\t") for l in tsv.read_text().splitlines()[1:]]
            W, H = next((int(r[8]), int(r[9])) for r in rows if r[0] == "1")
            words = [
                (r[11], int(r[6]) / W, int(r[7]) / H, (int(r[6]) + int(r[8])) / W, (int(r[7]) + int(r[9])) / H)
                for r in rows
                if r[0] == "5" and len(r) > 11 and r[11].strip()
            ]
        return p, words

    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(one, pagenos))


def anchored(words, anchor, left=0.2):
    import re

    heads = [w for w in words if re.fullmatch(anchor, w[0]) and w[1] < left]
    rows = {id(h): [] for h in heads}
    rest = []
    for w in words:
        mid = (w[2] + w[4]) / 2
        best = min(heads, key=lambda h: abs((h[2] + h[4]) / 2 - mid), default=None)
        if best is not None and abs((best[2] + best[4]) / 2 - mid) < (best[4] - best[2]) * 0.6:
            rows[id(best)].append(w)
        else:
            rest.append(w)
    lines = group(rest) + [sorted(r, key=lambda x: x[1]) for r in rows.values()]
    return sorted(lines, key=lambda l: min((w[2] + w[4]) / 2 for w in l))


def strips(img, bands, cache, scale=1):
    from PIL import Image
    from concurrent.futures import ThreadPoolExecutor

    im = Image.open(img)
    W, H = im.size
    cache.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (y0, y1) in enumerate(bands):
        pad = (y1 - y0) * 0.12
        a, b = max(0, int((y0 - pad) * H)), min(H, int((y1 + pad) * H))
        path = cache / f"{img.stem}-s{i}-{a}-{b}-x{scale}.png"
        if not path.exists():
            crop = im.crop((0, a, W, b))
            crop.resize((crop.width * scale, crop.height * scale)).save(path)
        paths.append((path, a, b))
    todo = [str(p) for p, _, _ in paths if not Path(str(p) + ".vision.tsv").exists()]
    for k in range(0, len(todo), 50):
        subprocess.run([str(Path(os.environ["OR_WORK"]) / "vision/vision"), *todo[k : k + 50]], check=True)

    def tess(path):
        tsv = Path(str(path) + ".tess.tsv")
        if not tsv.exists():
            tsv.write_text(
                subprocess.run(
                    ["tesseract", str(path), "-", "--psm", "7", "tsv"], capture_output=True, text=True, check=True
                ).stdout
            )
        return tsv.read_text()

    with ThreadPoolExecutor(8) as pool:
        tsvs = list(pool.map(tess, [p for p, _, _ in paths]))
    out = []
    for (path, a, b), ttxt in zip(paths, tsvs):
        h = (b - a) / H
        vis = [
            (t, float(x0), a / H + float(y0) * h, float(x1), a / H + float(y1) * h)
            for t, x0, y0, x1, y1 in (l.split("\t") for l in Path(str(path) + ".vision.tsv").read_text().splitlines())
        ]
        rows = [l.split("\t") for l in ttxt.splitlines()[1:]]
        tes = [
            (r[11], int(r[6]) / W, (a + int(r[7])) / H, (int(r[6]) + int(r[8])) / W, (a + int(r[7]) + int(r[9])) / H)
            for r in rows
            if r[0] == "5" and len(r) > 11 and r[11].strip()
        ]
        out.append({"vision": sorted(vis, key=lambda x: x[1]), "tess": sorted(tes, key=lambda x: x[1])})
    return out


def reread(img, boxes, cache):
    from PIL import Image
    from ea import scan

    W, H = Image.open(img).size
    cells = []
    for x0, y0, x1, y1 in boxes:
        h = (y1 - y0) * H
        cells.append(
            ((int(x0 * W) - 12, int(y0 * H - h * 0.08) - 5, int(x1 * W) + 9, int(y1 * H + h * 0.08) + 3), True)
        )
    return [[r.replace(",", "").replace(" ", "") for r in rs] for rs in scan.read(img, cells, "eng")]


def region(img, box, cache, scale=3):
    from PIL import Image, ImageOps

    im = Image.open(img).convert("L")
    W, H = im.size
    x0, y0, x1, y1 = box
    a, b, c, d = int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{img.stem}-r{a}-{b}-{c}-{d}-x{scale}.png"
    if not path.exists():
        crop = im.crop((a, b, c, d))
        ImageOps.expand(crop.resize((crop.width * scale, crop.height * scale)), border=30, fill=255).save(path)
    pw, ph = (c - a) * scale + 60, (d - b) * scale + 60

    def back(px0, py0, px1, py1):
        return (
            (a + (px0 - 30) / scale) / W,
            (b + (py0 - 30) / scale) / H,
            (a + (px1 - 30) / scale) / W,
            (b + (py1 - 30) / scale) / H,
        )

    out = []
    tsv = Path(str(path) + ".vision.tsv")
    if not tsv.exists():
        subprocess.run([str(Path(os.environ["OR_WORK"]) / "vision/vision"), str(path)], check=True)
    for l in tsv.read_text().splitlines():
        t, q0, r0, q1, r1 = l.split("\t")
        out.append(("vision", t, back(float(q0) * pw, float(r0) * ph, float(q1) * pw, float(r1) * ph)))
    for psm in ("6", "7"):
        rows = [
            l.split("\t")
            for l in subprocess.run(
                ["tesseract", str(path), "-", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789,", "tsv"],
                capture_output=True,
                text=True,
            ).stdout.splitlines()[1:]
        ]
        for r in rows:
            if r[0] == "5" and len(r) > 11 and r[11].strip():
                x, y, w, h = map(int, r[6:10])
                out.append(("tess", r[11], back(x, y, x + w, y + h)))
    return out
