import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from parsers import check, save, val
from words import pages

D = Path(sys.argv[1])
NUM = re.compile(r"^[\d,]+$")


def n(w):
    return int(w[0].replace(",", ""))


def umatilla():
    rows = {}
    for p, lines in enumerate(pages(D / "Umatilla.pdf"), 1):
        ms = []
        for l in lines:
            t = " ".join(w[0] for w in l)
            if "State Measure" in t:
                ms = re.findall(r"Measure (10[2-6])", t)
            elif ms and re.match(r"^\d{3}[A-Z]?-", l[0][0]):
                nums = [w for w in l if NUM.match(w[0])]
                lab = " ".join(w[0] for w in l[: l.index(nums[0])])
                for k, m in enumerate(ms):
                    y, no, tot = nums[6 * k : 6 * k + 3]
                    assert n(y) + n(no) == n(tot), (p, t)
                    rows[(m, lab)] = {"YES": (*val(y), p), "NO": (*val(no), p)}
    return rows


def marion():
    rows, meas = {}, None
    for p, lines in enumerate(pages(D / "Marion.pdf"), 1):
        for l in lines:
            t = " ".join(w[0] for w in l)
            if m := re.fullmatch(r"(?:State )?Measure (10[2-6])", t):
                meas = m[1]
            elif t.startswith("Totals"):
                meas = None
            elif meas and re.fullmatch(r"\d{3}", l[0][0]) and len(l) >= 4 and all(NUM.match(w[0]) for w in l[1:4]):
                y, no, tot = l[1:4]
                if n(y) + n(no) == n(tot):
                    rows[(meas, l[0][0])] = {"YES": (*val(y), p), "NO": (*val(no), p)}
                else:
                    print("skip", p, t)
    return rows


def columbia():
    rows, meas = {}, None
    for p, lines in enumerate(pages(D / "Columbia.pdf"), 1):
        for l in lines:
            t = " ".join(w[0] for w in l)
            if m := re.fullmatch(r"State Measure (10[2-6])", t):
                meas = m[1]
            elif meas and re.fullmatch(r"\d{4}", l[0][0]) and len(l) > 6 and all(NUM.match(w[0]) for w in l[-4:]):
                rows.setdefault(
                    (meas, " ".join(w[0] for w in l[:-4])), {"YES": (*val(l[-4]), p), "NO": (*val(l[-3]), p)}
                )
            elif (
                meas
                and re.match(r"^[A-Z]", t)
                and not re.match(r"(?i)^(\(vote|votes|yes|no\b|over|under|\(with)", t)
                and not re.match(r"^0\d = ", t)
            ):
                meas = None
    return rows


for c, f in {"Umatilla": umatilla, "Marion": marion, "Columbia": columbia}.items():
    rows = f()
    print(c, check(c, rows, quiet=False), len(rows))
    save(c, rows)
