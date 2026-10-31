import html
import re
import subprocess
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


def read(
    pdf: Path, sha: str, kind: str
) -> tuple[dict[tuple[str, str], dict[str, Cell]], dict[tuple[str, str], tuple[int, int]]]:
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
        if kind == "scan":
            continue
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
