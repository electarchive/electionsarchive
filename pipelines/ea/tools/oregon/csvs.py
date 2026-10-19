import csv, json, sys
from pathlib import Path

D = Path(sys.argv[1])
S = Path(sys.argv[2])
tot = json.load(open(S / "tot.json"))
want = {k.split("measure-")[1][:3]: v for k, v in tot.items()}
FIPS = {"Multnomah": "41051", "Umatilla": "41059", "Yamhill": "41071"}
n = lambda s: int(s.replace(",", ""))
out = {}
for r in csv.reader(open(D / "Multnomah.csv")):
    if r[1].startswith("Measure 1") and r[1][8:11] in want:
        out.setdefault(("Multnomah", r[1][8:11], r[4]), {})[r[3].upper()] = n(r[6])
for r in csv.reader(open(D / "Yamhill.csv")):
    if len(r) > 12 and r[6] in [f"Measure {m}" for m in want]:
        out.setdefault(("Yamhill", r[6][8:], r[0]), {})[r[9].upper()] = n(r[12])
rows = list(csv.reader(open(D / "Umatilla.csv")))
for i, r in enumerate(rows):
    if len(r) > 7 and r[1].startswith("State Measure"):
        ms = [r[1][-3:], r[7][-3:]]
        j = i + 4
        while rows[j][0] != "Totals":
            for k, m in enumerate(ms):
                out.setdefault(("Umatilla", m, rows[j][0]), {}).update(
                    YES=n(rows[j][1 + 6 * k]), NO=n(rows[j][2 + 6 * k])
                )
            j += 1
for c, f in FIPS.items():
    for m in sorted(want):
        got = {"YES": 0, "NO": 0}
        cnt = 0
        for (cc, mm, p), v in out.items():
            if cc == c and mm == m:
                cnt += 1
                for a in got:
                    got[a] += v[a]
        print(c, m, cnt, got, want[m][f], "OK" if got == want[m][f] else "MISMATCH")
json.dump([[*k, v["YES"], v["NO"]] for k, v in out.items()], open(S / "csvs.json", "w"))
