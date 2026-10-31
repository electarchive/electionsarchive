import difflib
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.tn_primaries_1996"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(1996, 8, 1)
PUBLISHER = "Tennessee Secretary of State, Division of Elections"
TITLE = "August 1, 1996 primary election results: U.S. House of Representatives and U.S. Senate by county"
BASE = "https://sos-tn-gov-files.s3.amazonaws.com/"
FILES = {
    "democratic": BASE + "1996%20-%20August%20-%20State%20Democratic%20Primary.pdf",
    "republican": BASE + "1996%20-%20August%20-%20State%20Republican%20Primary.pdf",
    "senate": BASE + "1996%20-%20August%20-%20US%20Senate.pdf",
}
HOUSE = re.compile(r"^\s*DISTRICT\s+(\d+)\s*$")
CANDIDATE = re.compile(r"(\d{1,2})\s*\.+\s+([A-Za-z][^\n]*?)(?=\s{3,}\d{1,2}\s*\.|\s*$)")
Row = list[int]
ROWS: dict[tuple[str, int, str], Row] = {
    ("republican", 8, "Lake"): [10, 11, 28, 71, 0, 0, 0, 0, 0, 0, 0, 0],
    ("democratic", 4, "Lawrence"): [1510, 459, 0, 0, 0, 0, 0],
    ("democratic", 5, "Davidson"): [25632, 0, 0, 0, 0, 0, 0],
    ("democratic", 5, "Robertson"): [2083, 0, 0, 0, 0, 0, 0],
    ("democratic", 6, "Marshall"): [144, 2403, 0, 0, 0, 0, 0],
    ("democratic", 6, "Rutherford"): [614, 4253, 0, 0, 0, 0, 1],
    ("democratic", 9, "Shelby"): [35844, 63888, 5244, 88, 312, 292, 0],
    ("senate", 0, "Loudon"): [462, 264, 0, 104, 1928, 0],
    ("senate", 0, "Putnam"): [2913, 1067, 0, 157, 3963, 0],
    ("senate", 0, "Crockett"): [203, 51, 0, 4, 213, 0],
    ("senate", 0, "Fayette"): [1048, 521, 0, 81, 1777, 0],
}
TOTALS: dict[tuple[str, int], Row] = {
    ("democratic", 5): [27715, 0, 0, 0, 0, 0, 0],
    ("democratic", 9): [35844, 63888, 5244, 88, 312, 292, 0],
    ("democratic", 4): [23577, 5734, 2, 0, 0, 0, 5],
    ("democratic", 6): [4673, 37324, 0, 0, 0, 0, 4],
    ("senate", 0): [156704, 89887, 40, 16715, 266549, 23],
}
ERRATA = {("democratic", 3, 0), ("democratic", 4, 1)}
SENATE = {
    "democratic": ["Houston Gordon", "Ashley M. King", "Write-Ins"],
    "republican": ["Jim F. Counts", "Fred Thompson", "Write-Ins"],
}


def fetch() -> None:
    for url in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=".pdf")


def text(party: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FILES[party]).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def clean(line: str) -> str:
    line = re.sub(r"(?<=\d)[,'.]\s+(?=\d)", ",", line)
    line = re.sub(r"(?<=\s)l\.(?=\d)", "1", line)
    line = re.sub(r"(?<=\s)[cCDQ](?=\s|$)", "0", line)

    def fix(match: re.Match[str]) -> str:
        token = match.group(0)
        if re.fullmatch(r"[oOlIa]+", token) or (re.search(r"\d", token) and re.fullmatch(r"[\dolIOa,]+", token)):
            return token.translate(str.maketrans("olIOa", "01100"))
        return token

    return re.sub(r"(?<![A-Za-z])[\dolIOa,]+(?![A-Za-z])", fix, line)


def county(name: str, names: dict[str, str]) -> str | None:
    key = re.sub(r"[^A-Za-z ]", "~", name.strip().upper().replace("'I'R", "TT").replace("'I'r", "TT"))
    if key in names:
        return names[key]
    close = difflib.get_close_matches(key, list(names), 1, 0.55)
    return names[close[0]] if close and len(key) > 3 else None


def place(columns: list[int], values: list[tuple[int, int]]) -> dict[int, int] | None:
    count, width = len(values), len(columns)
    if count > width:
        return None
    if count == width:
        return {k: values[k][1] for k in range(width)}
    best = [[10**9] * (width + 1) for _ in range(count + 1)]
    for j in range(width + 1):
        best[0][j] = 0
    for i in range(1, count + 1):
        for j in range(i, width + 1):
            best[i][j] = min(best[i][j - 1], best[i - 1][j - 1] + abs(columns[j - 1] - values[i - 1][0]))
    placed: dict[int, int] = {}
    j = width
    for i in range(count, 0, -1):
        while best[i][j] == best[i][j - 1] and j > i:
            j -= 1
        placed[j - 1] = values[i - 1][1]
        j -= 1
    return placed


def numbers(cleaned: str, start: int) -> list[tuple[int, int]]:
    return [(start + m.end(), int(m.group().replace(",", ""))) for m in re.finditer(r"\d[\d,]*", cleaned[start:])]


def read_house(party: str, names: dict[str, str]) -> list[tuple[str, int, str, str, int]]:
    out: list[tuple[str, int, str, str, int]] = []
    lines = text(party).splitlines()
    district = 0
    tables: dict[int, dict[str, object]] = {}
    columns: list[int] = []
    for line in lines:
        if re.match(r"^\s*(TENNESSEE HOUSE|TENNESSEE SENATE)", line):
            break
        found = HOUSE.match(line)
        if found:
            district = int(found.group(1))
            tables[district] = {"names": {}, "rows": {}, "total": None}
            continue
        if not district:
            continue
        if re.search(r"CANDIDATES|^\s{10,}\d{1,2}\s*\.", line):
            for number, name in CANDIDATE.findall(line.replace("CANDIDATES:", "          ")):
                tables[district]["names"][int(number)] = re.sub(r"\s+", " ", name).strip()  # ty: ignore
            continue
        if line.lstrip().startswith("COUNTY"):
            body = line.lstrip()[6:]
            lead = len(line) - len(line.lstrip()) + 6
            columns = [lead + m.end() for m in re.finditer(r"\S+", body)]
            continue
        cleaned = clean(line)
        match = re.match(r"^\s*(\S[^\d]*?)\s{2,}(\d.*)$", cleaned)
        if not match or not columns:
            continue
        label = match.group(1).strip()
        values = numbers(cleaned, match.start(2))
        if label.upper().startswith("DISTRICT TOTAL"):
            tables[district]["total"] = place(columns, values)
            continue
        name = county(label, names)
        if name is None or re.search(r"\.frm|PAGE|COUNTY", label, re.IGNORECASE):
            continue
        placed = place(columns, values)
        if placed is not None:
            tables[district]["rows"][name] = [placed.get(k, 0) for k in range(len(columns))]  # ty: ignore
    for number, table in tables.items():
        rows: dict[str, Row] = table["rows"]  # ty: ignore
        for key, row in ROWS.items():
            if key[0] == party and key[1] == number:
                rows[key[2]] = row
        width = max((len(r) for r in rows.values()), default=0)
        total: dict[int, int] | None = table["total"]  # ty: ignore
        printed = TOTALS.get((party, number)) or ([total.get(k, 0) for k in range(width)] if total else None)
        if printed is None:
            raise ValueError(f"{party} {number}: no printed total")
        for k in range(width):
            summed = sum(r[k] for r in rows.values())
            if summed != printed[k] and (party, number, k) not in ERRATA:
                raise ValueError(f"{party} {number} column {k + 1}: counties sum to {summed}, printed {printed[k]}")
        candidates: dict[int, str] = table["names"]  # ty: ignore
        for name, row in rows.items():
            for k, votes in enumerate(row):
                label = "Write-Ins" if k == width - 1 else candidates.get(k + 1)
                if label is None:
                    if votes:
                        print(f"{party} {number} {name}: {votes} votes in unnamed column {k + 1}, left out")
                    continue
                out.append((party, number, label, name, votes))
    return out


def read_senate(names: dict[str, str]) -> list[tuple[str, int, str, str, int]]:
    rows: dict[str, Row] = {}
    for line in text("senate").splitlines():
        cleaned = clean(line)
        match = re.match(r"^\s*(\S[^\d]*?)\s{2,}(\d.*)$", cleaned)
        if not match:
            continue
        label = match.group(1).strip()
        if re.match(r"(STATE|TOTAL|PAGE)", label, re.IGNORECASE):
            continue
        name = county(label, names)
        if name is None:
            continue
        values = [v for _, v in numbers(cleaned, match.start(2))]
        if len(values) == 6:
            rows[name] = values
    for key, row in ROWS.items():
        if key[0] == "senate":
            rows[key[2]] = row
    printed = TOTALS[("senate", 0)]
    if len(rows) != 95:
        raise ValueError(f"senate: {len(rows)} counties")
    for k in range(6):
        if sum(r[k] for r in rows.values()) != printed[k]:
            raise ValueError(f"senate column {k + 1}: counties sum to {sum(r[k] for r in rows.values())}")
    out = []
    for name, row in rows.items():
        for k, votes in enumerate(row):
            party = "democratic" if k < 3 else "republican"
            out.append((party, 0, SENATE[party][k % 3], name, votes))
    return out


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TN"]
    names = {
        re.sub(r"[^A-Za-z ]", "~", n.upper()): n
        for n in [
            "Anderson",
            "Bedford",
            "Benton",
            "Bledsoe",
            "Blount",
            "Bradley",
            "Campbell",
            "Cannon",
            "Carroll",
            "Carter",
            "Cheatham",
            "Chester",
            "Claiborne",
            "Clay",
            "Cocke",
            "Coffee",
            "Crockett",
            "Cumberland",
            "Davidson",
            "Decatur",
            "DeKalb",
            "Dickson",
            "Dyer",
            "Fayette",
            "Fentress",
            "Franklin",
            "Gibson",
            "Giles",
            "Grainger",
            "Greene",
            "Grundy",
            "Hamblen",
            "Hamilton",
            "Hancock",
            "Hardeman",
            "Hardin",
            "Hawkins",
            "Haywood",
            "Henderson",
            "Henry",
            "Hickman",
            "Houston",
            "Humphreys",
            "Jackson",
            "Jefferson",
            "Johnson",
            "Knox",
            "Lake",
            "Lauderdale",
            "Lawrence",
            "Lewis",
            "Lincoln",
            "Loudon",
            "Macon",
            "Madison",
            "Marion",
            "Marshall",
            "Maury",
            "McMinn",
            "McNairy",
            "Meigs",
            "Monroe",
            "Montgomery",
            "Moore",
            "Morgan",
            "Obion",
            "Overton",
            "Perry",
            "Pickett",
            "Polk",
            "Putnam",
            "Rhea",
            "Roane",
            "Robertson",
            "Rutherford",
            "Scott",
            "Sequatchie",
            "Sevier",
            "Shelby",
            "Smith",
            "Stewart",
            "Sullivan",
            "Sumner",
            "Tipton",
            "Trousdale",
            "Unicoi",
            "Union",
            "Warren",
            "Washington",
            "Wayne",
            "Weakley",
            "White",
            "Williamson",
            "Wilson",
        ]
    }
    names["VAN BUREN"] = "Van Buren"
    rows = [*read_house("democratic", names), *read_house("republican", names), *read_senate(names)]
    frame = pl.DataFrame(
        rows,
        schema={
            "party": pl.String,
            "district": pl.Int64,
            "candidate": pl.String,
            "county": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    senate = pl.col("district") == 0
    frame.select(
        year=pl.lit(1996, pl.Int32),
        chamber=pl.when(senate)
        .then(pl.concat_str(pl.lit("senate"), pl.col("party"), separator=":"))
        .otherwise(pl.concat_str(pl.lit("house"), pl.col("party"), separator=":")),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("TN"),
        district=pl.when(senate).then(None).otherwise(pl.col("district")).cast(pl.Int16),
        code=pl.col("code"),
        candidate=pl.when(pl.col("candidate") == "Write-Ins")
        .then(pl.lit("WRITE-INS"))
        .otherwise(pl.col("candidate").str.to_uppercase()),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate") == "Write-Ins",
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("TN"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee's August 1, 1996 U.S. House and U.S. Senate primaries by county, from the Division of "
            "Elections' official results",
            polity="US-TN",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.tn.gov/elections/results",
            license="Public record",
        )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, frame, {"TN": source}, strict=True)
        return cs.write()
