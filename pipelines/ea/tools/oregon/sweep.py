import re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
SCANS = [
    "Baker",
    "Benton",
    "Clackamas",
    "Clatsop",
    "Coos",
    "Crook",
    "Curry",
    "Deschutes",
    "Douglas",
    "Gilliam",
    "Grant",
    "Harney",
    "Jefferson",
    "Lake",
    "Lincoln",
    "Malheur",
    "Morrow",
    "Polk",
    "Sherman",
    "Union",
    "Wallowa",
    "Wasco",
    "Washington",
    "Wheeler",
    "Yamhill",
    "Lane2",
]


def pages(name):
    info = subprocess.run(["pdfinfo", str(SRC / f"{name}.pdf")], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info).group(1))


def ocr(job):
    name, page = job
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "p"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "120",
                "-gray",
                "-png",
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                str(SRC / f"{name}.pdf"),
                str(img),
            ],
            check=True,
        )
        text = subprocess.run(
            ["tesseract", str(img) + ".png", "-", "--psm", "3"], capture_output=True, text=True
        ).stdout
    hits = sorted(set(re.findall(r"(?i)measure\s*(?:no\.?\s*)?(10[2-6])\b", text)))
    return name, page, hits, " ".join(text.split())[:160]


jobs = [(n, p) for n in SCANS for p in range(1, pages(n) + 1)]
with ThreadPoolExecutor(10) as pool, open(OUT, "w") as out:
    for name, page, hits, head in pool.map(ocr, jobs):
        out.write(f"{name}\t{page}\t{','.join(hits)}\t{head}\n")
