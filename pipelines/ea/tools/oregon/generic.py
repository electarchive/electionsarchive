import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from words import pages

D = Path(sys.argv[1])
S = Path(os.environ["OR_WORK"])
tot = json.load(open(S / "tot.json"))
want = {k.split("measure-")[1][:3]: v for k, v in tot.items()}
FIPS = dict(
    zip(
        [
            "Baker",
            "Benton",
            "Clackamas",
            "Clatsop",
            "Columbia",
            "Coos",
            "Crook",
            "Curry",
            "Deschutes",
            "Douglas",
            "Gilliam",
            "Grant",
            "Harney",
            "HoodRiver",
            "Jackson",
            "Jefferson",
            "Josephine",
            "Klamath",
            "Lake",
            "Lane",
            "Lincoln",
            "Linn",
            "Malheur",
            "Marion",
            "Morrow",
            "Multnomah",
            "Polk",
            "Sherman",
            "Tillamook",
            "Umatilla",
            "Union",
            "Wallowa",
            "Wasco",
            "Washington",
            "Wheeler",
            "Yamhill",
        ],
        [f"41{i:03d}" for i in range(1, 72, 2)],
    )
)
num = re.compile(r"^[\d,]+$")


def run(county, precinct_re, first=None):
    rows = {}
    prec, meas, hdr = first, None, False
    for pno, lines in enumerate(pages(D / f"{county}.pdf"), 1):
        for l in lines:
            text = " ".join(w[0] for w in l)
            if text == "All Precincts":
                prec = None
                continue
            if m := re.fullmatch(precinct_re, text):
                prec, meas = m[1], None
                continue
            m = re.search(r"(?i)\b(?:measure|question)\s+(?:no\.\s*)?(?:14-)?(10[789]|110)\b(?!-)", text)
            if m and (county == "Hood River" or "14-" not in m[0]):
                meas, hdr = m[1], True
                continue
            if re.match(r"(?i)(state |county |city )?(measure|question)\b", text):
                meas = None
                continue
            if re.search(r"(?i)vote for", text):
                if not hdr:
                    meas = None
                hdr = False
                continue
            hdr = False
            if meas and prec and l[0][0] in ("Yes", "No") and len(l) > 1 and num.match(l[1][0]):
                w = l[1]
                rows.setdefault((meas, prec), {}).setdefault(l[0][0].upper(), (int(w[0].replace(",", "")), pno, w[1:]))
    return rows


def check(county, rows):
    ok = True
    for m in sorted(want):
        got = {"YES": 0, "NO": 0}
        ps = [p for (mm, p) in rows if mm == m]
        for p in ps:
            for a in got:
                got[a] += rows[(m, p)].get(a, (0,))[0]
        good = got == want[m][FIPS[county.replace(" ", "")]]
        ok &= good
        print(county, m, len(ps), got, want[m][FIPS[county.replace(" ", "")]], "OK" if good else "MISMATCH")
    return ok


if __name__ == "__main__":
    county, rx = sys.argv[2], sys.argv[3]
    first = sys.argv[4] if len(sys.argv) > 4 else None
    rows = run(county, rx, first)
    check(county, rows)
    json.dump(
        [
            [county, m, p, v["YES"][0], v["NO"][0], v["YES"][1], v["YES"][2], v["NO"][1], v["NO"][2]]
            for (m, p), v in rows.items()
        ],
        open(S / f"out/{county}.json", "w"),
    )
