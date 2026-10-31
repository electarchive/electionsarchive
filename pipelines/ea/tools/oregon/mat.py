import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
import re

from parsers import check, save
from scan3 import D, S, hitv
from words import ocr, region, reread


def tokens(c, p):
    return [w for e in ("vision", "tess") for _, ws in ocr(D / f"{c}.pdf", [p], e, S / "img") for w in ws]


def rowmatch(toks, vals, y):
    out, last = [], -1
    for v in vals:
        cand = sorted(
            [w for w in toks if abs((w[2] + w[4]) / 2 - y) < 0.007 and w[1] > last and hitv(w[0].replace(" ", ""), v)],
            key=lambda w: w[1],
        )
        out.append(cand[0] if cand else None)
        if cand:
            last = cand[0][1]
    return out


def locate(c, p, vals, below=None, xr=(0, 1)):
    toks = [w for w in tokens(c, p) if xr[0] <= w[1] <= xr[1]]
    ys = sorted(
        {
            round((w[2] + w[4]) / 2, 3)
            for w in toks
            if (below is None or (w[2] + w[4]) / 2 > below + 0.004) and any(hitv(w[0], v) for v in vals)
        }
    )
    best = max(((sum(x is not None for x in rowmatch(toks, vals, y)), -y, y) for y in ys), default=(0, 0, None))
    y = best[2]
    if y is None:
        return None, [None] * len(vals)
    got = rowmatch(toks, vals, y)
    if None in got:
        h = 0.012
        reg = [
            (t, *b) for _, t, b in region(S / "img" / f"{c}-{p}.png", (xr[0], y - h, xr[1], y + h), S / "img/regions")
        ]
        more = rowmatch(reg, vals, y)
        got = [a if a is not None else b for a, b in zip(got, more)]
    return y, got


def labelrows(c, p, meas):
    toks = tokens(c, p)
    mid = lambda w: (w[2] + w[4]) / 2
    heads = [
        mid(w)
        for w in toks
        if w[1] < 0.4
        and re.search(rf"{meas}\b", w[0])
        and any(abs(mid(v) - mid(w)) < 0.006 and re.match(r"(?i)(measure|question|meas)", v[0]) for v in toks)
    ]
    if not heads:
        return None, None
    h = min(heads)
    yes = sorted(mid(w) for w in toks if w[1] < 0.55 and re.fullmatch(r"(?i)yes", w[0]) and mid(w) > h)
    no = sorted(mid(w) for w in toks if w[1] < 0.55 and re.fullmatch(r"(?i)no", w[0]) and yes and mid(w) > yes[0])
    return (yes[0] if yes else None), (no[0] if no else None)


def run(c):
    rows, bad = {}, []
    recs = list(csv.reader(open(S / f"tr/{c}.mat"), delimiter="|"))
    cols = {}
    for r in recs:
        p, meas, labels, ys, ns = (
            int(r[0]),
            r[1],
            r[2].split(";"),
            [int(x) for x in r[3].split(";")],
            [int(x) for x in r[4].split(";")],
        )
        xr = tuple(map(float, r[5].split(";"))) if len(r) > 5 else (0, 1)
        y1, gy = locate(c, p, ys, xr=xr)
        y2, gn = locate(c, p, ns, below=y1, xr=xr)
        for k, w in enumerate(gy + gn):
            if w is not None:
                cols.setdefault((p, labels[k % len(labels)]), []).append(((w[1] + w[3]) / 2, w[3] - w[1]))
    for r in recs:
        p, meas, labels, ys, ns = (
            int(r[0]),
            r[1],
            r[2].split(";"),
            [int(x) for x in r[3].split(";")],
            [int(x) for x in r[4].split(";")],
        )
        xr = tuple(map(float, r[5].split(";"))) if len(r) > 5 else (0, 1)
        y1, gy = locate(c, p, ys, xr=xr)
        y2, gn = locate(c, p, ns, below=y1, xr=xr)
        ly, ln = labelrows(c, p, meas)
        for g, vals, yy, lyy in ((gy, ys, y1, ly), (gn, ns, y2, ln)):
            if None not in g:
                continue
            row = lyy if lyy is not None else yy
            if row is None:
                continue
            for k, lab in enumerate(labels):
                if g[k] is not None or (p, lab) not in cols:
                    continue
                xs = sorted(cols[(p, lab)])
                cx, wd = xs[len(xs) // 2]
                box = (cx - wd, row - 0.008, cx + wd, row + 0.008)
                if str(vals[k]) in reread(S / "img" / f"{c}-{p}.png", [box], S / "img/cells")[0]:
                    g[k] = (str(vals[k]), *box)
        if y2 is not None and y1 is not None and y2 - y1 > 0.05:
            bad.append((p, meas, "rows far", y1, y2))
        for lab, a, b, va, vb in zip(labels, gy, gn, ys, ns):
            if a is None or b is None:
                bad.append((p, meas, lab, "nomatch", va if a is None else "", vb if b is None else ""))
                continue
            rows[(meas, lab)] = {"YES": (va, a[1:], p), "NO": (vb, b[1:], p)}
    return rows, bad


if __name__ == "__main__":
    c = sys.argv[1]
    rows, bad = run(c)
    for b in bad:
        print("BAD", b)
    print(c, "OK" if check(c, rows) else "MISMATCH", len(rows))
    save(c, rows)
