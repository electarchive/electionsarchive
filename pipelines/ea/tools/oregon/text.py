import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from words import pages
from parsers import generic, matrix, check, save

D = Path(sys.argv[1])


def pg(c):
    return list(enumerate(pages(D / f"{c}.pdf"), 1))


def tillamook():
    out = []
    for p, lines in pg("Tillamook"):
        i = next(i for i, l in enumerate(lines) if " ".join(w[0] for w in l) == "November 6, 2018")
        lines[i + 1] = [("@@", 0, 0, 0, 0), *lines[i + 1]]
        out.append((p, lines))
    return generic(out, r"@@ (.+)")


runs = {
    "Hood River": lambda: generic(pg("Hood River"), r"Precinct (\S+)"),
    "Jackson": lambda: generic(pg("Jackson"), r"Precinct (\S+)"),
    "Linn": lambda: generic(pg("Linn"), r"Precinct (\S+)", mrx=r"^(10[2-6]) "),
    "Tillamook": tillamook,
    "Klamath": lambda: matrix(pg("Klamath"), r"^Precinct (\S+) ")[0],
    "Lane": lambda: matrix(pg("Lane"), r"^Precinct (\S+) ")[0],
}
for c, f in [(k, v) for k, v in runs.items() if len(sys.argv) < 3 or k == sys.argv[2]]:
    rows = f()
    print(c, check(c, rows, quiet=len(sys.argv) < 3), len(rows))
    save(c, rows)
