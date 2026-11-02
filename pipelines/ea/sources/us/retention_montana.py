import html
import re
import subprocess
from datetime import UTC, datetime

from ea import raw, scan
from ea.sources.us import mt_judicial
from ea.store import connect

SOURCE = "us.retention"
INDEX = "https://sosmt.gov/elections/results/"

NUMBER = re.compile(r"\d[\d,]*")
LINE = 12
QUESTIONS = (
    ("2008-11-04", 2008, 3, "Patricia Cotter", (1500, 1700), (1700, 1900)),
    ("2010-11-02", 2010, 6, "Mike Wheat", None, None),
    ("2012-11-06", 2012, 15, "Brian Morris", None, None),
    ("2016-11-08", 2016, 10, "Mike McGrath", None, None),
    ("2016-11-08", 2016, 12, "Jim Shea", None, None),
    ("2018-11-06", 2018, 6, "Beth Baker", None, None),
    ("2018-11-06", 2018, 7, "Ingrid Gustafson", None, None),
    ("2020-11-03", 2020, 12, "Jim Shea", None, None),
)
LATER = {
    2016: "2016-general/2016GeneralStatewideCanvass.pdf",
    2018: "2018-general/2018-General-Report-State-Canvass.pdf",
    2020: "2020-general/State_Canvass_Report.pdf",
    2022: "2022-general/2022General-StateCanvassReport.pdf",
    2024: "2024-general/2024-General-Election-Report-State-Canvass.pdf",
}
DAYS = {2016: "2016-11-08", 2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
HEADING = re.compile(
    r"Shall (?:Judge|Justice) (.+?) of(?: the)?(?: DISTRICT COURT JUDGE)?(?: the)? DISTRICT (\d+),? DEPT\.? (\d+)",
    re.IGNORECASE,
)


def lines(pdf, page):
    found: list[list[tuple[str, scan.Box]]] = []
    for word, box in sorted(
        ((html.unescape(w), b) for w, b in scan.layer(pdf, page)), key=lambda item: (item[1][1], item[1][0])
    ):
        if found and abs(found[-1][0][1][1] - box[1]) < LINE:
            found[-1].append((word, box))
        else:
            found.append([(word, box)])
    return [sorted(words, key=lambda item: item[1][0]) for words in found]


def number(word):
    return int(word.replace(",", ""))


def location(year):
    return mt_judicial.BASE + LATER[year].rsplit("/", 1)[1] if year in LATER else mt_judicial.location(year)


def fetch() -> None:
    for year, name in {**{y: n for y, (_, n) in mt_judicial.FILES.items()}, **LATER}.items():
        url = location(year)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = mt_judicial.INBOX / name
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def county_codes():
    with connect() as conn:
        return {
            mt_judicial.key(name): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%'"""
            ).fetchall()
        }


def districts(fips):
    found: list[dict[str, object]] = []
    for year, day in DAYS.items():
        url = location(year)
        pdf = raw.cached(SOURCE, url).path
        pages = int(
            subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True)
            .stdout.split("Pages:")[1]
            .split()[0]
        )
        for page in range(1, pages + 1):
            buffer: list[str] = []
            heading = None
            cells: dict[str, tuple[tuple[int, scan.Box], tuple[int, scan.Box]]] = {}
            for words in lines(pdf, page):
                texts = [w for w, _ in words]
                if [t.lower() for t in texts] == ["yes", "no"]:
                    heading = HEADING.search(" ".join(buffer))
                    cells = {}
                    continue
                figures = [(number(w), b) for w, b in words if NUMBER.fullmatch(w)]
                label = " ".join(w for w, b in words if not NUMBER.fullmatch(w))
                if len(figures) == 2 and heading is not None:
                    if label == "Total":
                        if len(cells) < 1:
                            raise ValueError(f"Montana {year} {heading[0]}: no counties")
                        sums = (sum(c[0][0] for c in cells.values()), sum(c[1][0] for c in cells.values()))
                        if sums != (figures[0][0], figures[1][0]):
                            raise ValueError(f"Montana {year} {heading[0]}: counties sum to {sums}, printed {figures}")
                        judge = re.sub(r'\s*"[^"]*"\s*', " ", heading[1]).strip()
                        for code, pair in cells.items():
                            for answer, (votes, box) in zip(("yes", "no"), pair, strict=True):
                                found.append(
                                    {
                                        "day": day,
                                        "state": "MT",
                                        "court": "district-court",
                                        "number": int(heading[2]),
                                        "post": int(heading[3]),
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
                                        "flags": 0,
                                    }
                                )
                        heading = None
                        cells = {}
                        buffer = []
                        continue
                    name = label + " clark" if mt_judicial.key(label + " clark") in fips else label
                    if mt_judicial.key(name) not in fips:
                        raise ValueError(f"Montana {year} page {page}: unknown county {label!r}")
                    cells[fips[mt_judicial.key(name)]] = (figures[0], figures[1])
                    continue
                buffer.append(" ".join(texts))
                if len(buffer) > 12:
                    del buffer[0]
    return found


def rows():
    fips = county_codes()
    found: list[dict[str, object]] = []
    for day, year, page, judge, yes_band, no_band in QUESTIONS:
        url = location(year)
        pdf = raw.cached(SOURCE, url).path
        cells: dict[str, tuple[tuple[int, scan.Box], tuple[int, scan.Box]]] = {}
        printed: tuple[int, int] | None = None
        for words in lines(pdf, page):
            if yes_band is not None and no_band is not None:
                yes = [(number(w), b) for w, b in words if NUMBER.fullmatch(w) and yes_band[0] <= b[2] <= yes_band[1]]
                no = [(number(w), b) for w, b in words if NUMBER.fullmatch(w) and no_band[0] <= b[2] <= no_band[1]]
                label = " ".join(w for w, b in words if b[2] < 300 and not NUMBER.fullmatch(w))
                if words[0][1][1] == 276 and len(yes) == 1 and len(no) == 1:
                    printed = (yes[0][0], no[0][0])
                    continue
            else:
                figures = [(number(w), b) for w, b in words if NUMBER.fullmatch(w)]
                label = " ".join(w for w, b in words if not NUMBER.fullmatch(w))
                yes, no = figures[:1], figures[1:2]
                if len(figures) != 2:
                    continue
                if label == "Total":
                    printed = (yes[0][0], no[0][0])
                    continue
            if len(yes) != 1 or len(no) != 1 or not label or label in {"County", "Total"}:
                continue
            key = mt_judicial.key(label)
            if key not in fips:
                continue
            cells[fips[key]] = (yes[0], no[0])
        if len(cells) != 56 or printed is None:
            raise ValueError(f"Montana {year} {judge}: {len(cells)} counties, printed {printed}")
        sums = (sum(c[0][0] for c in cells.values()), sum(c[1][0] for c in cells.values()))
        if sums != printed:
            raise ValueError(f"Montana {year} {judge}: counties sum to {sums}, printed {printed}")
        for code, pair in cells.items():
            for answer, (votes, box) in zip(("yes", "no"), pair, strict=True):
                found.append(
                    {
                        "day": day,
                        "state": "MT",
                        "court": "supreme-court",
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
                        "flags": 0,
                    }
                )
    return found + districts(fips)
