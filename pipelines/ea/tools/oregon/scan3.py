import os
import csv, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from words import ocr, group, strips, reread, region
from parsers import check, save, S, MEAS

D = S / "src"
NUM = re.compile(r"^\d[\d,]*$")


def iv(w):
    return int(w[0].replace(",", ""))


def nums(toks, skip=0):
    out = []
    for w in toks[skip:]:
        t = re.sub(r"[.:;)]$", "", w[0])
        t = "1" if re.fullmatch(r"[ilI|!]{1,3}|al|ul|it|di", t) else t
        if NUM.match(t):
            out.append((t, *w[1:]))
    return out


def tyn(skip=2):
    def f(toks):
        ns = nums(toks, skip)
        for i in range(len(ns) - 2):
            if iv(ns[i]) == iv(ns[i + 1]) + iv(ns[i + 2]):
                return [ns[i + 1], ns[i + 2], ns[i]]

    return f


def ynt(skip=2, gap=0):
    def f(toks):
        ns = nums(toks, skip)
        for i in range(len(ns) - 2 - gap):
            if iv(ns[i]) + iv(ns[i + 1]) == iv(ns[i + 2 + gap]):
                return [ns[i], ns[i + 1], ns[i + 2 + gap]]

    return f


def ballots(skip=1):
    def f(toks):
        ns = nums(toks, skip)
        v = [iv(w) for w in ns]
        if len(v) >= 5 and v[0] + v[1] == v[2] and any(sum(v[2:j]) == v[j] for j in range(4, min(len(v), 7))):
            return ns[:3]

    return f


def btyn(skip=2, back=1):
    def f(toks):
        ns = nums(toks, skip)
        v = [iv(w) for w in ns]
        for i in range(back, len(v) - 2):
            if v[i] == v[i + 1] + v[i + 2] and v[i - back] == v[i] + sum(v[i + 3 :]):
                return [ns[i + 1], ns[i + 2], ns[i]]

    return f


def tynp(skip=2):
    def f(toks):
        idx = [k for k, w in enumerate(toks[skip:], skip) if NUM.match(re.sub(r"[.:;)]$", "", w[0]))]
        for a, b, c in zip(idx, idx[1:], idx[2:]):
            t, y, n = (int(re.sub(r"\D", "", toks[k][0])) for k in (a, b, c))
            if t and t == y + n:
                pct = [re.sub(r"[^\d]", "", w[0]) for w in toks[b + 1 : c]]
                if any(p and abs(int(p) - round(10000 * y / t)) <= 1 for p in pct):
                    return [(str(y), *toks[b][1:]), (str(n), *toks[c][1:]), (str(t), *toks[a][1:])]

    return f


def at(xs, tol=0.012):
    def f(toks):
        out = []
        for x in xs:
            near = [w for w in toks if NUM.match(w[0]) and abs((w[1] + w[3]) / 2 - x) < tol]
            if len(near) != 1:
                return None
            out.append(near[0])
        return out

    return f


def hitv(t, v):
    t = t.rstrip("]}|)")
    return bool(
        NUM.match(t)
        and int(t.replace(",", "")) == v
        or re.fullmatch(rf"{v}\d{{1,2}}[.,]?\d\d.*", t)
        or re.search(rf"\d[.,]\d\d{v}\d{{1,2}}[.,]\d\d", t)
    )


def between(lines, lab):
    def find(n):
        hit = [
            l for l in lines if l[0][1] < 0.3 and re.match(rf"(?:.*?Precinct\s*)?0*{n}\b", " ".join(w[0] for w in l))
        ]
        return hit[0] if len(hit) == 1 else None

    if not lab.isdigit():
        return None
    a, b = find(int(lab) - 1), find(int(lab) + 1)
    if a is None or b is None:
        return None
    ya, yb = max(w[4] for w in a), min(w[2] for w in b)
    mid = [w for w in (w for l in lines for w in l) if ya - 0.002 < (w[2] + w[4]) / 2 < yb + 0.002]
    return [sorted(mid, key=lambda w: w[1])] if mid and yb - ya < 0.03 else None


def run(
    c,
    pagenos,
    pick,
    anchor=r"Precinct",
    label=r"Precinct (\S+)",
    mrx=MEAS,
    left=0.2,
    need=1,
    meas_of=None,
    xr=(0, 1),
    lpre=r"(?:.*?Precinct\s*)?",
):
    rows, bad = {}, []
    trp = S / f"tr/{c}.csv"
    recs = list(csv.reader(open(trp))) if trp.exists() else []
    tr = {(int(r[0]), int(r[1])): (r[2], r[3], int(r[4]), int(r[5])) for r in recs if r[1] != "L"}
    trl = [
        (int(r[0]), r[2], r[3], int(r[4]), int(r[5]), float(r[6]) if len(r) > 6 else None) for r in recs if r[1] == "L"
    ]
    done = {(r[1], r[2]) for r in trl}
    for p, words in ocr(D / f"{c}.pdf", pagenos, "vision", S / "img"):
        lines = group(words)
        heads = [(min(w[2] for w in l), re.search(mrx, " ".join(w[0] for w in l))) for l in lines]
        heads = [(y, m[1]) for y, m in heads if m]
        anchors = [l for l in lines if re.match(anchor, l[0][0]) and l[0][1] < left]
        bands = [(min(w[2] for w in l), max(w[4] for w in l)) for l in anchors]
        img = S / "img" / f"{c}-{p}.png"
        found = []
        for tp, tm, lab, ty, tn, ym in trl:
            if tp != p or meas_of and meas_of(p) != tm:
                continue
            line = (
                [sorted([w for w in words if abs((w[2] + w[4]) / 2 - ym) < 0.004], key=lambda w: w[1])]
                if ym is not None
                else [
                    l
                    for l in lines
                    if l[0][1] < 0.3 and re.match(rf"{lpre}{re.escape(lab)}\b", " ".join(w[0] for w in l))
                ]
            )
            if len(line) != 1:
                line = between(lines, lab)
            if line is None or len(line) != 1:
                tok = [
                    w
                    for w in words
                    if w[1] < 0.3
                    and re.fullmatch(rf".*\D?{re.escape(lab)}", w[0])
                    and not re.fullmatch(rf"\d+{re.escape(lab)}", w[0])
                ]
                if len(tok) == 1:
                    mid = (tok[0][2] + tok[0][4]) / 2
                    line = [
                        sorted(
                            [w for w in words if abs((w[2] + w[4]) / 2 - mid) < (tok[0][4] - tok[0][2]) * 0.6],
                            key=lambda w: w[1],
                        )
                    ]
            if line is None or len(line) != 1:
                bad.append((p, -1, tm, lab, "tr-noline", 0, (0, 0)))
                continue
            y0, y1 = (
                (ym - 0.005, ym + 0.005) if ym is not None else (min(w[2] for w in line[0]), max(w[4] for w in line[0]))
            )
            pool = [w for w in line[0] if xr[0] < w[1] < xr[1]]
            for attempt in (0, 1, 2):
                hit = []
                for v in (ty, tn):
                    cand = [w for w in pool if hitv(w[0], v) and (not hit or hit[-1] is None or w[3] >= hit[-1][3])]
                    hit.append(cand[0] if cand else None)
                if None not in hit:
                    break
                pad = (y1 - y0) * 0.3
                src = img
                if attempt == 1:
                    from PIL import Image, ImageFilter

                    src = img.with_suffix(".med.png")
                    if not src.exists():
                        Image.open(img).convert("L").filter(ImageFilter.MedianFilter(5)).save(src)
                pool = [
                    (t, *b)
                    for _, t, b in region(src, (xr[0], y0 - pad, xr[1], y1 + pad), S / "img/regions")
                    if xr[0] <= b[0] < xr[1]
                ]
            if None in hit:
                bad.append((p, -1, tm, lab, "tr-nomatch", ty, tn, (y0, y1)))
                continue
            rows[(tm, lab)] = {"YES": (ty, hit[0][1:], p), "NO": (tn, hit[1][1:], p)}
        for i, ((y0, y1), got) in enumerate(zip(bands, strips(img, bands, S / "img/strips"))):
            got["page"] = sorted(anchors[i], key=lambda w: w[1])
            meas = meas_of(p) if meas_of else max(((y, m) for y, m in heads if y < y0), default=(0, None))[1]
            if (p, i) in tr:
                tm, lab, ty, tn = tr[(p, i)]
                if tm == "-":
                    continue
                pool = [w for e in ("vision", "tess") for w in got[e]]
                for attempt in (0, 1, 2):
                    hit = []
                    for v in (ty, tn):
                        cand = [w for w in pool if hitv(w[0], v) and (not hit or hit[-1] is None or w[3] >= hit[-1][3])]
                        hit.append(cand[0] if cand else None)
                    if None not in hit:
                        break
                    pad = (y1 - y0) * 0.3
                    src = img
                    if attempt == 1:
                        from PIL import Image, ImageFilter

                        src = img.with_suffix(".med.png")
                        if not src.exists():
                            Image.open(img).convert("L").filter(ImageFilter.MedianFilter(5)).save(src)
                    pool = [(t, *b) for _, t, b in region(src, (0, y0 - pad, 1, y1 + pad), S / "img/regions")]
                if None in hit:
                    bad.append((p, i, tm, lab, "tr-nomatch", ty, tn, (y0, y1)))
                    continue
                rows[(tm, lab)] = {"YES": (ty, hit[0][1:], p), "NO": (tn, hit[1][1:], p)}
                continue
            if meas is None:
                continue
            for eng in ("vision", "page", "tess"):
                toks = got[eng]
                text = " ".join(w[0] for w in toks)
                m = re.match(label, text)
                pk = pick(toks)
                if m is not None and pk is not None:
                    break
            if m is None or pk is None:
                bad.append((p, i, meas, "unparsed", text, (y0, y1)))
                continue
            found.append((i, meas, m[1], pk, (y0, y1), got))
        checks = reread(img, [w[1:] for *_, pk, _, _ in found for w in pk], S / "img/cells")
        k = 0
        for i, meas, lab, pk, band, got in found:
            second = checks[k : k + len(pk)]
            k += len(pk)
            first = [str(iv(w)) for w in pk]
            votes = [sum(f == re.sub(r"[.:;)]$", "", r) for r in rs) for f, rs in zip(first, second)]
            if min(votes) < need:
                bad.append((p, i, meas, lab, "disagree", first, second, band))
                continue
            if (meas, lab) in done:
                continue
            if (meas, lab) in rows:
                bad.append((p, i, meas, lab, "dup", band))
                continue
            rows[(meas, lab)] = {"YES": (int(first[0]), pk[0][1:], p), "NO": (int(first[1]), pk[1][1:], p)}
            tess = [w[0].replace(",", "") for w in got["tess"]]
            if not all(any(t == f or re.fullmatch(rf"{f}\d{{1,2}}[.,]?\d\d.*", t) for t in tess) for f in first[:2]):
                bad.append((p, i, meas, lab, "suspect", first[:2], band))
    return rows, bad


def report(c, rows, bad):
    ok = check(c, rows, quiet=True)
    for b in bad:
        if ok and b[4] == "suspect":
            continue
        print("BAD", b[:-1])
    check(c, rows)
    bad = [b for b in bad if not ok or b[4] != "suspect"]
    save(c, rows)
    json.dump(bad, open(S / f"out/{c}.bad.json", "w"))
    return ok


def blocks(c, pagenos, prx=r"(?i)^precinct\s*(\d+)$", mrx=r"(?i)^measure\s+(10[2-6])\b"):
    rows, bad, names = {}, [], {}
    trp = S / f"tr/{c}.csv"
    trrows = list(csv.reader(open(trp))) if trp.exists() else []
    tr = {(r[1], r[2]): (int(r[3]), int(r[4])) for r in trrows}
    trpage = {(r[1], r[2]): [int(x) for x in r[0].split("/")] for r in trrows}
    found = []
    for eng in ("vision", "tess"):
        pending, reg, meas = None, None, None
        for p, words in ocr(D / f"{c}.pdf", pagenos, eng, S / "img"):
            for l in group(words):
                text = " ".join(w[0] for w in l)
                if m := re.match(prx, text):
                    pending = m[1]
                    continue
                if m := re.search(r"([\d,]+) registered voters", text):
                    reg = m[1].replace(",", "")
                    if pending is not None:
                        names.setdefault(reg, pending)
                        pending = None
                    continue
                if m := re.match(mrx, text):
                    meas = m[1]
                    continue
                if re.search(r"(?i)\(vote for", text):
                    meas = None
                    continue
                if meas and reg and re.match(r"(?i)^(yes|no)$", l[0][0]):
                    a = l[0]
                    mid, h = (a[2] + a[4]) / 2, a[4] - a[2]
                    near = sorted(
                        [w for w in words if w[1] > a[3] and abs((w[2] + w[4]) / 2 - mid) < h * 0.6], key=lambda w: w[1]
                    )
                    if near:
                        found.append((p, meas, reg, a[0].upper(), (re.sub(r"[.:;)]$", "", near[0][0]), *near[0][1:])))
    by_page = {}
    for f in found:
        by_page.setdefault(f[0], []).append(f)
    cells = {}
    for p, fs in by_page.items():
        checks = reread(S / "img" / f"{c}-{p}.png", [f[4][1:] for f in fs], S / "img/cells")
        for (p, meas, reg, ch, w), rs in zip(fs, checks):
            cells.setdefault((meas, reg, ch), []).append((p, w, [re.sub(r"[.:;)]$", "", r) for r in rs]))
    for meas, reg, ch in sorted({k for k in cells} | {(m, r, ch) for m, r in tr for ch in ("YES", "NO")}):
        key = (meas, names.get(reg, "reg" + reg))
        got = None
        if (meas, reg) in tr:
            v = str(tr[(meas, reg)][ch == "NO"])
            for p, w, rs in cells.get((meas, reg, ch), []):
                if w[0] == v or v in rs:
                    got = (int(v), w[1:], p)
                    break
            if got is None:
                for p, w, rs in cells.get((meas, reg, ch), []):
                    pool = region(
                        S / "img" / f"{c}-{p}.png",
                        (w[1] - 0.05, w[2] - 0.004, w[3] + 0.05, w[4] + 0.004),
                        S / "img/regions",
                    )
                    hit = [b for _, t, b in pool if hitv(t, int(v))]
                    if hit:
                        got = (int(v), hit[0], p)
                        break
            if got is None:
                other = cells.get((meas, reg, "NO" if ch == "YES" else "YES"), [])
                for p, w, rs in other:
                    dy = 0.013 if ch == "NO" else -0.013
                    pool = region(
                        S / "img" / f"{c}-{p}.png",
                        (w[1] - 0.05, w[2] + dy - 0.006, w[3] + 0.05, w[4] + dy + 0.006),
                        S / "img/regions",
                    )
                    hit = [b for _, t, b in pool if hitv(t, int(v))]
                    if hit:
                        got = (int(v), hit[0], p)
                        break
            if got is None:
                for p in trpage[(meas, reg)]:
                    toks = [
                        w
                        for e in ("vision", "tess")
                        for _, ws in ocr(D / f"{c}.pdf", [p], e, S / "img")
                        for w in ws
                        if 0.3 < w[1] < 0.42 and w[0] == v
                    ]
                    if len(toks) == 1 and v in reread(S / "img" / f"{c}-{p}.png", [toks[0][1:]], S / "img/cells")[0]:
                        got = (int(v), toks[0][1:], p)
                        break
            if got is None:
                bad.append((meas, reg, ch, "tr-nomatch", v))
                continue
        else:
            for p, w, rs in cells[(meas, reg, ch)]:
                if NUM.match(w[0]) and w[0] in rs:
                    got = (int(w[0]), w[1:], p)
                    break
            if got is None:
                bad.append((meas, reg, ch, "disagree", [(p, w[0], rs) for p, w, rs in cells[(meas, reg, ch)]]))
                continue
        rows.setdefault(key, {})[ch] = got
    for k in [k for k, v in rows.items() if len(v) < 2]:
        bad.append(("partial", k, rows.pop(k)))
    return rows, bad, names
