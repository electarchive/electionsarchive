import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from parsers import check, save
from scan3 import blocks

c, n = sys.argv[1], int(sys.argv[2])
rows, bad, names = blocks(c, range(1, n + 1))
names.setdefault("2058", "05") if c == "Wasco" else None
rows = {(m, names.get(l[3:], l) if l.startswith("reg") else l): v for (m, l), v in rows.items()}
for b in bad:
    print("BAD", b)
print("names", names)
print(c, "OK" if check(c, rows) else "MISMATCH", len(rows))
save(c, rows)
