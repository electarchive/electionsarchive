import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from generic import D, S, check
from words import pages

county, rx = sys.argv[2], sys.argv[3]
mrx = sys.argv[4] if len(sys.argv) > 4 else r"(?i)\b(?:state )?measure\s+(10[789]|110)\b(?!-)"
head = sys.argv[5] if len(sys.argv) > 5 else r"(?i)vote for"
rows = {}
meas = None
for pno, lines in enumerate(pages(D / f"{county}.pdf"), 1):
    for l in lines:
        text = " ".join(w[0] for w in l)
        if re.search(head, text):
            m = re.search(mrx, text)
            meas = m[1] if m else None
            continue
        if not meas or not (m := re.match(rx, text)):
            continue
        if county == "Columbia":
            nums = [w for w in l if re.fullmatch(r"[\d,]+", w[0])][-6:]
            v = [int(w[0].replace(",", "")) for w in nums]
            if len(v) != 6 or v[0] + v[1] != v[2] or v[2] + v[3] + v[4] != v[5]:
                print("skip", pno, text)
                continue
            y, n = nums[0], nums[1]
        else:
            pct = [i for i, w in enumerate(l) if w[0].endswith("%")]
            if len(pct) != 2:
                print("skip", pno, text)
                continue
            y, n = l[pct[0] - 1], l[pct[1] - 1]
        rows.setdefault(
            (meas, m[1]),
            {"YES": (int(y[0].replace(",", "")), pno, y[1:]), "NO": (int(n[0].replace(",", "")), pno, n[1:])},
        )
check(county, rows)
json.dump(
    [
        [county, m, p, v["YES"][0], v["NO"][0], v["YES"][1], v["YES"][2], v["NO"][1], v["NO"][2]]
        for (m, p), v in rows.items()
    ],
    open(S / f"out/{county}.json", "w"),
)
