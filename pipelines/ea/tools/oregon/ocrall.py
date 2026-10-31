import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["OR_WORK"])))
from words import ocr

S = Path(os.environ["OR_WORK"])
for c in sys.argv[1:]:
    n = int(
        re.search(
            r"Pages:\s+(\d+)",
            subprocess.run(
                ["pdfinfo", str(S / "src" / f"{c}.pdf")], capture_output=True, text=True, check=False
            ).stdout,
        )[1]
    )
    ocr(S / "src" / f"{c}.pdf", range(1, n + 1), "vision", S / "img")
    print(c, n)
