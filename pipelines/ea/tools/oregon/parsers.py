import json
import os
import re
from pathlib import Path

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
MEAS = r"(?i)\b(?:state )?(?:measure|question)\s+(?:no\.\s*)?(10[2-6])\b(?!-)"


def val(w):
    return (int(w[0].replace(",", "")), w[1:])


def generic(pages, precinct_re, first=None, mrx=MEAS):
    rows = {}
    prec, meas, hdr = first, None, False
    for pno, lines in pages:
        for l in lines:
            text = " ".join(w[0] for w in l)
            if text == "All Precincts":
                prec = None
                continue
            if m := re.fullmatch(precinct_re, text):
                prec, meas = m[1], None
                continue
            if m := re.search(mrx, text):
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
                rows.setdefault((meas, prec), {}).setdefault(l[0][0].upper(), (*val(l[1]), pno))
    return rows


def matrix(pages, rx, mrx=MEAS, head=r"(?i)vote for", pick=None):
    rows, skips = {}, []
    meas = None
    for pno, lines in pages:
        for l in lines:
            text = " ".join(w[0] for w in l)
            if re.search(head, text):
                m = re.search(mrx, text)
                meas = m[1] if m else None
                continue
            if not meas or not (m := re.match(rx, text)):
                continue
            got = pick(l) if pick else None
            if pick is None:
                pct = [i for i, w in enumerate(l) if w[0].endswith("%")]
                if (
                    len(pct) == 2
                    and pct[0] >= 2
                    and all(num.match(l[i - 1][0]) for i in pct)
                    and num.match(l[pct[0] - 2][0])
                ):
                    y, n, t = (int(l[i][0].replace(",", "")) for i in (pct[0] - 1, pct[1] - 1, pct[0] - 2))
                    if y + n == t:
                        got = l[pct[0] - 1], l[pct[1] - 1]
            if got is None:
                skips.append((pno, text))
                continue
            rows.setdefault((meas, m[1]), {"YES": (*val(got[0]), pno), "NO": (*val(got[1]), pno)})
    return rows, skips


def check(county, rows, quiet=False):
    ok = True
    for m in sorted(want):
        got = {"YES": 0, "NO": 0}
        ps = [p for (mm, p) in rows if mm == m]
        for p in ps:
            for a in got:
                got[a] += rows[(m, p)].get(a, (0,))[0]
        exp = want[m][FIPS[county.replace(" ", "")]]
        ok &= got == exp
        if not quiet:
            print(county, m, len(ps), got, exp, "OK" if got == exp else "MISMATCH")
    return ok


def save(county, rows):
    json.dump(
        [
            [county, m, p, v["YES"][0], v["NO"][0], v["YES"][2], v["YES"][1], v["NO"][2], v["NO"][1]]
            for (m, p), v in sorted(rows.items())
        ],
        open(S / f"out/{county}.json", "w"),
    )
