import csv
import difflib
import html
import io
import itertools
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ea import raw, scan
from ea.store import DATA, connect

SOURCE = "us.retention"
INDEX = "https://sos.iowa.gov/iowans/election-results-statistics"
INBOX = DATA / "inbox" / "manual" / "MF-507"
FILES = {
    "2000-11-07": (
        "2000judretention.pdf",
        "https://sos.iowa.gov/elections/pdf/results/2000s/2000judretention.pdf",
        "scan",
    ),
    "2002-11-05": (
        "JudicialCanvassSummary.pdf",
        "https://sos.iowa.gov/elections/pdf/2002/results/JudicialCanvassSummary.pdf",
        "side",
    ),
    "2004-11-02": (
        "JudicialCanvassSummary (1).pdf",
        "https://sos.iowa.gov/elections/pdf/2004/general/JudicialCanvassSummary.pdf",
        "side",
    ),
    "2006-11-07": ("JudResults.pdf", INDEX + "#JudResults.pdf", "measures"),
    "2008-11-04": ("OfficialCanvass2008General.pdf", INDEX + "#OfficialCanvass2008General.pdf", "yes-no"),
    "2010-11-02": ("judicialorr.pdf", "https://sos.iowa.gov/elections/pdf/2010/judicialorr.pdf", "report"),
    "2012-11-06": ("canvsummary.pdf", INDEX + "#canvsummary-2012.pdf", "absentee"),
    "2014-11-04": ("canvsummary (1).pdf", INDEX + "#canvsummary-2014.pdf", "absentee"),
    "2016-11-08": ("canvsummary (2).pdf", INDEX + "#canvsummary-2016.pdf", "absentee"),
    "2022-11-08": ("canvsummary (3).pdf", INDEX + "#canvsummary-2022.pdf", "absentee"),
}
HEADING = re.compile(
    r"(Supreme Court|Court of Appeals)\s*[-–]?\s*(?:(?:Justice|Judge)\s*[-–]?\s*)?([A-Z][A-Za-z.'’ -]*[a-z.])$"
)
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
COUNTY_NAMES = {"ADAIR", "WRIGHT", "JEFFERSON", "JASPER"}
COLUMNS = {"yes-no": 3, "measures": 3, "report": 5, "absentee": 5}

type Line = list[tuple[str, scan.Box]]
type Cell = tuple[int, int, scan.Box, scan.Box, int]


def fetch() -> None:
    for file, url, _ in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = INBOX / file
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def grouped(words: list[tuple[str, scan.Box]]) -> list[Line]:
    out: list[Line] = []
    for word, box in sorted(words, key=lambda item: (item[1][1], item[1][0])):
        if out and abs(out[-1][0][1][1] - box[1]) < 14:
            out[-1].append((word, box))
        else:
            out.append([(word, box)])
    for line in out:
        line.sort(key=lambda item: item[1][0])
    return out


def unglued(words: list[tuple[str, scan.Box]]) -> list[tuple[str, scan.Box]]:
    out: list[tuple[str, scan.Box]] = []
    for word, box in words:
        glued = re.fullmatch(r"([A-Za-z.]+)(\d[\d,]*)", word)
        if glued is None:
            out.append((word, box))
            continue
        cut = box[0] + round((box[2] - box[0]) * len(glued[1]) / len(word))
        out += [(glued[1], (box[0], box[1], cut, box[3])), (glued[2], (cut, box[1], box[2], box[3]))]
    return out


def page_lines(pdf: Path, sha: str, page: int, ocr: bool) -> list[Line]:
    if not ocr:
        return grouped(unglued([(html.unescape(word), box) for word, box in scan.layer(pdf, page)]))
    folder = DATA / "parsed" / SOURCE / "ocr"
    folder.mkdir(parents=True, exist_ok=True)
    return grouped([(word, box) for word, box, _, _ in scan.words(pdf, page, folder / f"ia-{sha}-{page}.tsv")])


def split(line: Line) -> tuple[str, list[tuple[int, scan.Box]]] | None:
    cut = next((index for index, (word, _) in enumerate(line) if NUMBER.fullmatch(word)), None)
    if cut is None or cut == 0 or not all(NUMBER.fullmatch(word) for word, _ in line[cut:]):
        return None
    return " ".join(word for word, _ in line[:cut]), [(int(word.replace(",", "")), box) for word, box in line[cut:]]


def names(line: Line) -> list[str]:
    groups: list[list[str]] = [[]]
    for index, (word, box) in enumerate(line):
        if index and box[0] - line[index - 1][1][2] > 70:
            groups.append([])
        groups[-1].append(word)
    return [" ".join(group) for group in groups]


def counties() -> dict[str, str]:
    with connect() as conn:
        return {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%%'
                   and (u.valid is null or u.valid @> date '2020-11-03')"""
            ).fetchall()
        }


SCAN_PAGES = {
    1: (0, "supreme-court", ["Mark S. Cady", "James H. Carter"]),
    2: (0, "supreme-court", ["Mark S. Cady", "James H. Carter"]),
    3: (90, "court-of-appeals", ["Daryl L. Hecht", "John C. Miller", "Anuradha Vaitheswaran", "Van D. Zimmer"]),
    4: (90, "court-of-appeals", ["Daryl L. Hecht", "John C. Miller", "Anuradha Vaitheswaran", "Van D. Zimmer"]),
}
SCAN_PASSES = [(300, False), (400, True), (300, True)]
LOOKALIKES = {"4": "1", "1": "4", "A": "41", "I": "1", "l": "1", "O": "0", "S": "5"}


def scan_lines(pdf: Path, sha: str, page: int, turn: int, dpi: int = 300, deskew: bool = False) -> list[Line]:
    folder = DATA / "parsed" / SOURCE / "ocr"
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder / f"ia-{sha}-{page}-r{turn}-{dpi}{'d' if deskew else ''}.tsv"
    if not cache.exists():
        with tempfile.TemporaryDirectory() as work:
            image = Path(work) / "page.png"
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    str(dpi),
                    "-gray",
                    "-png",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    str(pdf),
                    str(image.with_suffix("")),
                ],
                check=True,
            )
            if turn:
                subprocess.run(["magick", str(image), "-rotate", str(turn), str(image)], check=True)
            if deskew:
                subprocess.run(["magick", str(image), "-deskew", "40%", str(image)], check=True)
            cache.write_text(
                subprocess.run(
                    ["tesseract", str(image), "stdout", "--psm", "6", "-l", "eng", "tsv"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )
    found: dict[tuple[str, str, str], Line] = {}
    for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["text"].strip():
            left, top = int(row["left"]), int(row["top"])
            box = (left, top, left + int(row["width"]), top + int(row["height"]))
            found.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append((row["text"], box))
    return [sorted(line, key=lambda item: item[1][0]) for line in found.values()]


def readings(token: str) -> list[int]:
    text = re.sub(r"[^0-9A-Za-z]", "", token)
    if not text or any(not (char.isdigit() or char in LOOKALIKES) for char in text):
        return []
    options = [char + LOOKALIKES.get(char, "") if char.isdigit() else LOOKALIKES[char] for char in text]
    picks = sorted(itertools.product(*options), key=lambda pick: sum(a != b for a, b in zip(pick, text, strict=True)))
    return [int("".join(pick)) for pick in picks]


def balanced(tokens: list[str]) -> tuple[tuple[int, int, int], int] | None:
    yes, no, total = (readings(token) for token in tokens)
    if not yes or not no or not total:
        return None
    for a, b, c in itertools.product(yes, no, total):
        if a + b == c:
            return (a, b, c), 3
    digits = [re.sub(r"\D", "", token) for token in tokens]
    for a, b in itertools.product(yes, no):
        if difflib.SequenceMatcher(None, digits[2], str(a + b)).ratio() >= 0.5:
            return (a, b, a + b), 1
    for a, c in itertools.product(yes, total):
        if c > a and difflib.SequenceMatcher(None, digits[1], str(c - a)).ratio() >= 0.5:
            return (a, c - a, c), 1
    for b, c in itertools.product(no, total):
        if c > b and difflib.SequenceMatcher(None, digits[0], str(c - b)).ratio() >= 0.5:
            return (c - b, b, c), 1
    return None


def repaired(tokens: list[tuple[str, scan.Box]], judges: int) -> list[tuple[int, int]] | None:
    values = [readings(word) for word, _ in tokens]
    for missing in range(len(tokens) + 1):
        slots: list[list[list[int]]] = [[] for _ in range(judges)]
        for index, options in enumerate(values[:missing] + [[]] + values[missing:]):
            slots[index // 3].append(options)
        found: list[tuple[int, int]] = []
        for group in slots:
            if [] not in group:
                hit = (
                    next((a, b) for a, b, c in itertools.product(*group) if a + b == c)
                    if any(a + b == c for a, b, c in itertools.product(*group))
                    else None
                )
                if hit is None:
                    break
                found.append(hit)
            elif group[2] == []:
                break
            elif group[0] == []:
                found.append((group[2][0] - group[1][0], group[1][0]))
            else:
                found.append((group[0][0], group[2][0] - group[0][0]))
        else:
            return found
    return None


def scanned(
    pdf: Path, sha: str
) -> tuple[dict[tuple[str, str], dict[str, Cell]], dict[tuple[str, str], tuple[int, int]]]:
    fips = counties()
    options: dict[tuple[str, str], dict[str, dict[tuple[int, int, int], list[int | scan.Box]]]] = {}
    totals: dict[tuple[str, str], tuple[int, int]] = {}
    for page, (turn, court, judges) in SCAN_PAGES.items():
        rows: dict[str, list[list[tuple[str, scan.Box]]]] = {}
        for dpi, deskew in SCAN_PASSES:
            for line in scan_lines(pdf, sha, page, turn, dpi, deskew):
                cut = next((index for index, (word, _) in enumerate(line) if re.search(r"\d", word)), None)
                if cut is None or cut == 0:
                    continue
                label = re.sub(r"[^A-Z ]", "", " ".join(word for word, _ in line[:cut])).strip()
                label = next(
                    (
                        " ".join(words[start:])
                        for words in [label.split()]
                        for start in range(len(words))
                        if re.sub(r"[^a-z]", "", "".join(words[start:]).lower()) in fips
                    ),
                    "TOTALS" if label.endswith("TOTALS") else "",
                )
                tokens = [
                    (word, box) for word, box in line[cut:] if re.fullmatch(r"\W{0,2}[\d,.AIlOS]*\d[\d,.AIlOS]*", word)
                ]
                if label == "TOTALS" and len(tokens) == 3 * len(judges) - 1:
                    for index, pair in enumerate(repaired(tokens, len(judges)) or []):
                        totals.setdefault((court, judges[index]), pair)
                elif label and len(tokens) == 3 * len(judges):
                    rows.setdefault(label, []).append(tokens)
        for label, passes in rows.items():
            for index, judge in enumerate(judges):
                for tokens in passes:
                    group = tokens[3 * index : 3 * index + 3]
                    got = balanced([word for word, _ in group])
                    if got is None:
                        continue
                    if label == "TOTALS":
                        totals.setdefault((court, judge), (got[0][0], got[0][1]))
                        continue
                    slot = options.setdefault((court, judge), {}).setdefault(label, {})
                    entry = slot.setdefault(got[0], [0, group[0][1], group[1][1], page])
                    entry[0] = int(str(entry[0])) + got[1]
    cells: dict[tuple[str, str], dict[str, Cell]] = {}
    for key, by_label in options.items():
        ranked = {
            label: sorted(found.items(), key=lambda item: -int(str(item[1][0]))) for label, found in by_label.items()
        }
        pick = {label: 0 for label in ranked}
        if key in totals:
            ambiguous = [label for label, found in ranked.items() if len(found) > 1]
            base = [found[0][0] for found in ranked.values()]
            want = totals[key]
            if (sum(t[0] for t in base), sum(t[1] for t in base)) != want:
                best: tuple[int, ...] | None = None
                best_score = -1
                for combo in itertools.product(*(range(len(ranked[label])) for label in ambiguous)):
                    chosen = {label: ranked[label][pos][0] for label, pos in zip(ambiguous, combo, strict=True)}
                    yes = sum(chosen.get(label, found[0][0])[0] for label, found in ranked.items())
                    no = sum(chosen.get(label, found[0][0])[1] for label, found in ranked.items())
                    if (yes, no) == want:
                        score = sum(
                            int(str(ranked[label][pos][1][0])) for label, pos in zip(ambiguous, combo, strict=True)
                        )
                        if score > best_score:
                            best, best_score = combo, score
                if best is not None:
                    pick.update(dict(zip(ambiguous, best, strict=True)))
                else:
                    print(f"Iowa 2000 {key[1]}: no combination of readings reaches {want}")
        for label, found in ranked.items():
            triple, (_, yes_box, no_box, page) = found[pick[label]]
            if not (isinstance(yes_box, tuple) and isinstance(no_box, tuple)):
                raise TypeError(f"{key} {label}: boxes {yes_box!r} and {no_box!r} are not tuples")
            cells.setdefault(key, {})[label] = (triple[0], triple[1], yes_box, no_box, int(str(page)))
    return cells, totals


def read(
    pdf: Path, sha: str, kind: str
) -> tuple[dict[tuple[str, str], dict[str, Cell]], dict[tuple[str, str], tuple[int, int]]]:
    if kind == "scan":
        return scanned(pdf, sha)
    cells: dict[tuple[str, str], dict[str, Cell]] = {}
    totals: dict[tuple[str, str], tuple[int, int]] = {}
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    court = ""
    columns: list[tuple[str, str]] = []
    judge: tuple[str, str] | None = None
    county: str | None = None
    for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
        for line in page_lines(pdf, sha, page, kind == "measures"):
            text = " ".join(word for word, _ in line)
            if kind == "side":
                if text in ("Supreme Court Judge", "Court of Appeals"):
                    court = "supreme-court" if text.startswith("Supreme") else "court-of-appeals"
                    columns = []
                    continue
                if text.startswith(("District", "Associate")):
                    court = ""
                if not court:
                    continue
                if not columns:
                    if not re.search(r"\d|YES|ELECTION", text):
                        columns = [(court, who) for who in names(line)]
                    continue
                if text.startswith("Totals"):
                    values = [int(word.replace(",", "")) for word, _ in line[1:]]
                    for index, key in enumerate(columns):
                        totals[key] = (values[3 * index], values[3 * index + 1])
                    columns = []
                    continue
                got = split(line)
                if got is None or len(got[1]) != 3 * len(columns):
                    continue
                for index, key in enumerate(columns):
                    yes, no = got[1][3 * index], got[1][3 * index + 1]
                    cells.setdefault(key, {})[got[0]] = (yes[0], no[0], yes[1], no[1], page)
                continue
            found = HEADING.match(text.replace("Measure Name: ", ""))
            if found is None and text.startswith("Measure Name:"):
                judge = None
            if found:
                judge = ("supreme-court" if found[1].startswith("Supreme") else "court-of-appeals", found[2].strip())
                county = None
                continue
            got = split(line)
            if judge is None or got is None:
                continue
            label, numbers = got
            if kind == "absentee":
                if len(numbers) != 5:
                    continue
                if label.endswith("Election Day"):
                    county = label.removesuffix("Election Day").strip() or county
                elif label.endswith("Absentee"):
                    county = label.removesuffix("Absentee").strip() or county
                elif label.upper().endswith("TOTAL"):
                    label = label[: -len("Total")].strip() or (county or "")
                    if label and label.upper() != "TOTAL":
                        cells.setdefault(judge, {})[label] = (
                            numbers[0][0],
                            numbers[1][0],
                            numbers[0][1],
                            numbers[1][1],
                            page,
                        )
                    else:
                        totals[judge] = (numbers[0][0], numbers[1][0])
                        judge = None
                    county = None
                continue
            if len(numbers) != COLUMNS[kind]:
                continue
            if re.match(r"(Grand )?Totals?:?$", label):
                totals[judge] = (numbers[0][0], numbers[1][0])
                judge = None
            else:
                cells.setdefault(judge, {})[label] = (numbers[0][0], numbers[1][0], numbers[0][1], numbers[1][1], page)
    return cells, totals


def rows() -> list[dict[str, object]]:
    fips = counties()
    found: list[dict[str, object]] = []
    for day, (_, url, kind) in FILES.items():
        artifact = raw.cached(SOURCE, url)
        cells, totals = read(artifact.path, artifact.sha256, kind)
        for (court, judge), by_county in cells.items():
            codes: dict[str, Cell] = {}
            for label, cell in by_county.items():
                code = fips.get(re.sub(r"[^a-z]", "", label.lower()))
                if code is None:
                    raise ValueError(f"Iowa {day} {judge}: no county {label}")
                codes[code] = cell
            if len(codes) != 99:
                raise ValueError(f"Iowa {day} {judge}: {len(codes)} counties")
            if (court, judge) in totals:
                sums = (sum(cell[0] for cell in codes.values()), sum(cell[1] for cell in codes.values()))
                if sums != totals[(court, judge)]:
                    print(f"Iowa {day} {judge}: counties sum to {sums}, printed {totals[(court, judge)]}")
            else:
                print(f"Iowa {day} {judge}: no printed total")
            for code, (yes, no, yes_box, no_box, page) in codes.items():
                for answer, votes, box in (("yes", yes, yes_box), ("no", no, no_box)):
                    found.append(
                        {
                            "day": day,
                            "state": "IA",
                            "court": court,
                            "number": None,
                            "post": None,
                            "name": judge,
                            "answer": answer,
                            "votes": votes,
                            "page": page,
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                            "origin": url,
                            "county": code,
                            "flags": 8 if kind == "measures" else 0,
                        }
                    )
    return found
