import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from words import group, ocr

S = Path(os.environ["OR_WORK"])
c, p, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 25
for _, w in ocr(S / "src" / f"{c}.pdf", [p], "vision", S / "img"):
    for l in group(w)[:n]:
        print(" | ".join(x[0] for x in l)[:170])
