import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from scan3 import at, ballots, report, run, tyn, tynp

R = range


def filt(lo, hi, inner):
    return lambda toks: inner([w for w in toks if lo < w[1] < hi])


def lake():
    a = run(
        "Lake",
        [13, 14, 15],
        lambda t: None,
        anchor=r"^\d{9}",
        xr=(0.28, 0.45),
        lpre=r".*?\s",
        meas_of=lambda p: {13: "102", 14: "104", 15: "106"}[p],
    )
    b = run(
        "Lake",
        [13, 14],
        lambda t: None,
        anchor=r"^\d{9}",
        xr=(0.48, 0.64),
        lpre=r".*?\s",
        meas_of=lambda p: {13: "103", 14: "105"}[p],
    )
    return {**a[0], **b[0]}, a[1] + b[1]


def benton():
    a = run(
        "Benton",
        [23, 24, 25, 26],
        filt(0.2, 0.52, ballots(0)),
        xr=(0.2, 0.52),
        anchor=r"^\d",
        label=r"(\d\d)\b",
        meas_of=lambda p: {23: "103", 24: "103", 25: "105", 26: "105"}[p],
    )
    b = run(
        "Benton",
        R(21, 27),
        filt(0.52, 1, ballots(0)),
        xr=(0.52, 1),
        anchor=r"^\d",
        label=r"(\d\d)\b",
        meas_of=lambda p: {21: "102", 22: "102", 23: "104", 24: "104", 25: "106", 26: "106"}[p],
    )
    return {**a[0], **b[0]}, a[1] + b[1]


jobs = {
    "Douglas": lambda: run("Douglas", R(14, 19), tyn(2)),
    "Deschutes": lambda: run("Deschutes", R(38, 43), tyn(4)),
    "Clatsop": lambda: run("Clatsop", R(30, 35), at([0.273, 0.308]), anchor=r"^\d{4}", label=r"(\d{4})"),
    "Washington": lambda: run(
        "Washington", R(55, 70), tynp(1), anchor=r"(?i).*precinct", label=r".*?Precinct\s*(3\d\d|4[0-4]\d)\b"
    ),
    "Clackamas": lambda: run("Clackamas", R(165, 195), ballots(1), anchor=r"^\d{3}", label=r"(\d{3})"),
    "Benton": benton,
    "Lake": lake,
}
for c in sys.argv[1:]:
    rows, bad = jobs[c]()
    print(c, "OK" if report(c, rows, bad) else "MISMATCH", len(rows))
