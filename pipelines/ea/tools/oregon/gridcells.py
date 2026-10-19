import csv, os, sys

S = os.environ["OR_WORK"]
c = sys.argv[1]
edges = {k: tuple(map(float, v.split(":"))) for k, v in (a.split("=") for a in sys.argv[2:])}
groups = {}
for r in csv.reader(open(f"{S}/tr/{c}.csv")):
    groups.setdefault((r[0], r[1]), []).append(r[2])
out = []
for (page, meas), labels in groups.items():
    w = [l.split("\t") for l in open(f"{S}/img/{c}-{page}.png.vision.tsv").read().splitlines()]
    pts = []
    for i, label in enumerate(labels):
        a = [t for t in w if t[0] == label.split()[0] and float(t[1]) < float(os.environ.get("XMAX", "0.12"))]
        if len(a) == 1:
            pts.append((i, (float(a[0][2]) + float(a[0][4])) / 2))
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sum((p[0] - mx) ** 2 for p in pts)
    res = max(abs(p[1] - (my + b * (p[0] - mx))) for p in pts)
    print(page, meas, n, f"step={b:.4f} maxres={res:.4f}")
    ye, ne, wd, hh = edges[meas]
    for i, label in enumerate(labels):
        ym = dict(pts).get(i, my + b * (i - mx)) + float(os.environ.get("YOFF", "0"))
        for k, e in (("YES", ye), ("NO", ne)):
            out.append(f"{page},{meas},{label},{k},{e - wd:.4f},{ym - hh:.4f},{e + 0.004:.4f},{ym + hh:.4f}")
open(f"{S}/tr/{c}.cells", "w").write("\n".join(out) + "\n")
