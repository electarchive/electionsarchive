import json, sys
from pathlib import Path

S = Path(sys.argv[1])
out = Path(sys.argv[2])
scanned = {p.stem for p in (S / "tr").glob("*.csv")}
rows = []
for p in sorted((S / "out").glob("*.json")):
    for c, m, pr, y, n, yp, yb, np_, nb in json.load(open(p)):
        rows.append([c, int(m), pr, y, n, yp, yb, np_, nb, c in scanned])
for c, m, pr, y, n in json.load(open(S / "csvs.json")):
    rows.append([c, int(m), pr, y, n, None, None, None, None, False])
keys = [(r[0], r[1], r[2]) for r in rows]
assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1][:5]
tot = json.load(open(S / "tot.json"))
fips = {}
for k, v in tot.items():
    m = int(k.split("measure-")[1][:3])
    got = {}
    for r in rows:
        if r[1] == m:
            got.setdefault(r[0], [0, 0])
            got[r[0]][0] += r[3]
            got[r[0]][1] += r[4]
    print(
        m,
        len(got),
        sum(a for a, _ in got.values()),
        sum(v2["YES"] for v2 in v.values()),
        sum(b for _, b in got.values()),
        sum(v2["NO"] for v2 in v.values()),
    )
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(rows))
print(len(rows), len({r[0] for r in rows}), sorted(scanned))
