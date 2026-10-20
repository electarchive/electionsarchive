import re
import subprocess
from datetime import date
from difflib import get_close_matches
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.sc_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_"
REPORTS = {2000: "2000", 2002: "2002", 2004: "2004", 2006: "2006", 2008: "2008"}
PUBLISHER = "South Carolina State Election Commission"
TITLE = "Election Report"
COUNTIES = [
    "Abbeville",
    "Aiken",
    "Allendale",
    "Anderson",
    "Bamberg",
    "Barnwell",
    "Beaufort",
    "Berkeley",
    "Calhoun",
    "Charleston",
    "Cherokee",
    "Chester",
    "Chesterfield",
    "Clarendon",
    "Colleton",
    "Darlington",
    "Dillon",
    "Dorchester",
    "Edgefield",
    "Fairfield",
    "Florence",
    "Georgetown",
    "Greenville",
    "Greenwood",
    "Hampton",
    "Horry",
    "Jasper",
    "Kershaw",
    "Lancaster",
    "Laurens",
    "Lee",
    "Lexington",
    "Marion",
    "Marlboro",
    "McCormick",
    "Newberry",
    "Oconee",
    "Orangeburg",
    "Pickens",
    "Richland",
    "Saluda",
    "Spartanburg",
    "Sumter",
    "Union",
    "Williamsburg",
    "York",
]
DIGITS = str.maketrans("SlIiOBZ", "5111082")
NUMBER = r"[.':~?]?[\dSlIiOBZ][\dSlIOBZ.,;:]*"

D = date
CONTESTS: list[tuple[int, int, str, tuple[date, str, int | None], list[str]]] = [
    (
        2000,
        25,
        r"CONGRESS DISTRICT 001 - RUNOFF",
        (D(2000, 6, 13), "house:republican", 1),
        ["Henry Brown", "Van Jenerette", "Buck Limehouse", "Mike Seekings", "Charlie Thompson", "Wheeler Tillman"],
    ),
    (
        2000,
        25,
        r"CONGRESS +DISTRICT 004",
        (D(2000, 6, 13), "house:republican", 4),
        ["Jim DeMint", "Franklin D Raddish"],
    ),
    (
        2000,
        31,
        r"CONGRESS +DISTRICT 001",
        (D(2000, 6, 27), "house:republican", 1),
        ["Henry Brown", "Buck Limehouse"],
    ),
    (
        2002,
        12,
        r"CONGRESS +DISTRICT 006",
        (D(2002, 6, 11), "house:democratic", 6),
        ["James Clyburn", "Eben Frasier"],
    ),
    (
        2002,
        21,
        r"CONGRESS +DISTRICT 001",
        (D(2002, 6, 11), "house:republican", 1),
        ["Bob Batchelder", "Henry Brown"],
    ),
    (
        2002,
        21,
        r"CONGRESS +DISTRICT 003 - RUNOFF",
        (D(2002, 6, 11), "house:republican", 3),
        ["Gresham Barrett", "George Ducworth", "Stan Jackson", "Jim Klauber", "Michael Thompson", "Bob Waldrep"],
    ),
    (
        2002,
        21,
        r"CONGRESS +DISTRICT 004",
        (D(2002, 6, 11), "house:republican", 4),
        ["Phil Bradley", "Jim DeMint"],
    ),
    (
        2002,
        30,
        r"CONGRESS +DISTRICT 003",
        (D(2002, 6, 25), "house:republican", 3),
        ["Gresham Barrett", "Jim Klauber"],
    ),
    (
        2004,
        19,
        r"U.S. Senate",
        (D(2004, 6, 8), "senate:democratic", None),
        ["Ben Frasier", "Inez Tenenbaum"],
    ),
    (
        2004,
        20,
        r"DISTRICT 004",
        (D(2004, 6, 8), "house:democratic", 4),
        ["Brandon Brown", "Andrew Wittman"],
    ),
    (
        2004,
        31,
        r"U.S Senate",
        (D(2004, 6, 8), "senate:republican", None),
        ["David Beasley", "Orly Benny Davis", "Charlie Condon", "Jim DeMint", "Mark McBride", "Thomas Ravenel"],
    ),
    (
        2004,
        32,
        r"DISTRICT 001",
        (D(2004, 6, 8), "house:republican", 1),
        ["Bob Batchelder", "Henry Brown"],
    ),
    (
        2004,
        32,
        r"DISTRICT 004",
        (D(2004, 6, 8), "house:republican", 4),
        ["Jack Adams", "Bob Inglis", "Carole Wells"],
    ),
    (
        2004,
        32,
        r"DISTRICT 006",
        (D(2004, 6, 8), "house:republican", 6),
        ["Gary McLeod", "Michael Reino"],
    ),
    (
        2004,
        38,
        r"U.S. Senate",
        (D(2004, 6, 22), "senate:republican", None),
        ["David Beasley", "Jim DeMint"],
    ),
    (
        2006,
        17,
        r"DISTRICT 1\b",
        (D(2006, 6, 13), "house:democratic", 1),
        ["Ben Frasier", "Ralph Ledford", "Randy Maatta"],
    ),
    (
        2006,
        17,
        r"DISTRICT 2\b",
        (D(2006, 6, 13), "house:democratic", 2),
        ["Michael Ray Ellisor", "David F White III"],
    ),
    (
        2006,
        17,
        r"DISTRICT 3\b",
        (D(2006, 6, 13), "house:democratic", 3),
        ["Lee Ballenger", "Philip M Cheney"],
    ),
    (
        2006,
        22,
        r"DIST 1\b",
        (D(2006, 6, 27), "house:democratic", 1),
        ["Ben Frasier", "Randy Maatta"],
    ),
    (
        2008,
        20,
        r"Republican Presidential Preference Primary",
        (D(2008, 1, 19), "president:republican", None),
        [
            "Hugh Cort",
            "John Cox",
            "Cap Fendig",
            "Rudy Giuliani",
            "Mike Huckabee",
            "Duncan Hunter",
            "John McCain",
            "Ron Paul",
            "Mitt Romney",
            "Tom Tancredo",
            "Fred Thompson",
        ],
    ),
    (
        2008,
        25,
        r"Democratic Presidential Preference Primary",
        (D(2008, 1, 26), "president:democratic", None),
        [
            "Joe Biden",
            "Hillary Clinton",
            "Chris Dodd",
            "John Edwards",
            "Mike Gravel",
            "Dennis Kucinich",
            "Barack Obama",
            "Bill Richardson",
        ],
    ),
    (
        2008,
        30,
        r"U\.S\. Senate - REP",
        (D(2008, 6, 10), "senate:republican", None),
        ["Lindsey Graham", "Buddy Witherspoon"],
    ),
    (
        2008,
        31,
        r"District 1 - REP",
        (D(2008, 6, 10), "house:republican", 1),
        ["Henry E Brown Jr", "Katherine Jenerette", "Paul V Norris"],
    ),
    (2008, 31, r"District 2 - REP", (D(2008, 6, 10), "house:republican", 2), ["Phil Black", "Joe Wilson"]),
    (2008, 31, r"District 4 - REP", (D(2008, 6, 10), "house:republican", 4), ["Bob Inglis", "Charles Jeter"]),
    (2008, 43, r"U\.S\. Senate - DEM", (D(2008, 6, 10), "senate:democratic", None), ["Michael Cone", "Bob Conley"]),
    (2008, 44, r"District 1 - DEM", (D(2008, 6, 10), "house:democratic", 1), ["Ben Frasier", "Linda Ketner"]),
    (2008, 44, r"District 2 - DEM", (D(2008, 6, 10), "house:democratic", 2), ["Blaine Lotz", "Rob Miller"]),
    (
        2008,
        44,
        r"District 4 - DEM",
        (D(2008, 6, 10), "house:democratic", 4),
        ["Ted Christian", "Paul Corden", "Bryan McCanless"],
    ),
    (2008, 51, r"District 4 - DEM", (D(2008, 6, 24), "house:democratic", 4), ["Ted Christian", "Paul Corden"]),
]

# Marlboro's printed 13 sits under Ravenel, but both printed totals reconcile only with it under McBride.
READ_BY_EYE: dict[tuple[date, str, int | None], dict[str, list[int]]] = {
    (D(2004, 6, 8), "senate:republican", None): {"Marlboro": [87, 0, 14, 14, 13, 0]},
    (D(2004, 6, 22), "senate:republican", None): {
        "Abbeville": [383, 431],
        "Aiken": [2590, 5751],
        "Dorchester": [3528, 6678],
        "Greenwood": [2050, 2570],
        "Laurens": [1865, 2350],
        "Pickens": [4752, 5271],
    },
}


def fetch() -> None:
    for tag in REPORTS.values():
        raw.fetch(SOURCE, f"{BASE}{tag}.pdf", suffix=".pdf")


def number(token: str) -> int:
    return int(re.sub(r"\D", "", token.translate(DIGITS)))


def county(name: str) -> str | None:
    match = get_close_matches(
        re.sub(r"\s+County$", "", name.strip(), flags=re.IGNORECASE).title(), COUNTIES, n=1, cutoff=0.6
    )
    return match[0] if match else None


def block(page: str, anchor: str, width: int) -> tuple[dict[str, list[int]], list[int] | None]:
    lines = page.split("\n")
    start = next(i for i, line in enumerate(lines) if re.search(anchor, " ".join(line.split())))
    rows: dict[str, list[int]] = {}
    total: list[int] | None = None
    pending: tuple[str, list[int]] | None = None
    short: dict[str, tuple[list[int], list[int]]] = {}
    full: list[list[int]] = []
    for line in lines[start + 1 :]:
        line = re.sub(r"(\d),\s+(\d{3})", r"\1,\2", line)
        bare = re.fullmatch(rf"\s*((?:{NUMBER}\s*)+)", line)
        if bare is not None and pending is not None:
            values = pending[1] + [number(token) for token in bare[1].split()]
            if len(values) >= width:
                rows[pending[0]] = values[:width]
                short.pop(pending[0], None)
                pending = None
            continue
        match = re.match(rf"\s*([A-Za-z\"'.: ]+?)\s+((?:{NUMBER}\s*)+)$", line)
        if match is None:
            if pending is None:
                name = county(line) if re.fullmatch(r"\s*[A-Za-z. ]{3,16}\s*", line) else None
                if (
                    name is not None
                    and name.lower() not in ("lee",)
                    or (name == "Lee" and line.strip().upper() == "LEE")
                ):
                    pending = (name, [])
            continue
        values = [number(token) for token in match[2].split()]
        if re.match(r"(state\s*total|totals?:?)$", match[1].strip(), re.IGNORECASE):
            total = values
            break
        name = county(match[1].replace('"', "M"))
        if name is None:
            continue
        ends = [match.start(2) + m.end() for m in re.finditer(NUMBER, match[2])]
        if len(values) >= width:
            rows[name] = values[:width]
            full.append(ends[:width])
            pending = None
        else:
            short[name] = (ends, values)
            pending = (name, values)
    if short and rows:
        edges = [sorted(ends[i] for ends in full) for i in range(width)]
        for name, (ends, values) in short.items():
            filled = [0] * width
            for end, value in zip(ends, values, strict=True):
                filled[min(range(width), key=lambda i: abs(end - edges[i][len(edges[i]) // 2]))] = value
            rows[name] = filled
    return rows, total


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    texts: dict[int, list[str]] = {}
    out: list[tuple[date, str, int | None, str, str, int]] = []
    for year, page, anchor, (day, chamber, district), names in CONTESTS:
        if year not in texts:
            path = raw.cached(SOURCE, f"{BASE}{REPORTS[year]}.pdf").path
            texts[year] = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            ).stdout.split("\f")
        rows, total = block(texts[year][page - 1], anchor, len(names))
        rows.update(READ_BY_EYE.get((day, chamber, district), {}))
        sums = [sum(values[i] for values in rows.values()) for i in range(len(names))]
        if total is None or sums != total[: len(names)]:
            print(f"{day} {chamber} {district}: sums {sums} vs total {total} over {len(rows)} counties")
            continue
        print(f"{day} {chamber} {district}: ok, {len(rows)} counties")
        for name, values in rows.items():
            for candidate, votes in zip(names, values, strict=True):
                out.append(
                    (day, chamber, district, candidate.upper(), fips[re.sub(r"[^a-z]", "", name.lower())], votes)
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            out,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("SC"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("SC"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's 2000–2008 House, Senate, and presidential primaries by county",
            polity="US-SC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.scvotes.gov/election-results",
            license="Public record",
        )
        for tag in REPORTS.values():
            cs.artifact(raw.cached(SOURCE, f"{BASE}{tag}.pdf"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"SC": source}, strict=True)
        return cs.write()
