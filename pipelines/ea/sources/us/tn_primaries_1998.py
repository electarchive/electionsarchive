import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.tn_primaries_1998"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-332"
DAY = date(1998, 8, 6)
PUBLISHER = "Tennessee Secretary of State, Division of Elections"
TITLE = "August 6, 1998 primary election results: U.S. House of Representatives by county"
FILES = {
    "democratic": (
        "https://tnelections.tnsosfiles.com/sharetngov/archived/election/results/1998-08/USHDemAug1998.pdf",
        "USHDemAug1998.pdf",
    ),
    "republican": (
        "https://tnelections.tnsosfiles.com/sharetngov/archived/election/results/1998-08/USHRepAug1998.pdf",
        "USHRepAug1998.pdf",
    ),
}
DISTRICT = re.compile(r"^\s*District (\d+)\s*$")
NUMBER = re.compile(r"\d[\d,]*")
WORD = re.compile(r"\S+(?: \S+)*")


def fetch() -> None:
    for url, file in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / file).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp((INBOX / file).stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def columns(first: str, header: str) -> list[tuple[int, str]]:
    offset = header.index("COUNTY") + 6
    spans = [(match.start() + offset, match.end() + offset, match.group()) for match in WORD.finditer(header[offset:])]
    firsts = [(match.start(), match.end(), match.group()) for match in WORD.finditer(first)]
    named = []
    for start, end, surname in spans:
        if surname == "Write-Ins":
            named.append((end, "Write-Ins"))
            continue
        center = (start + end) / 2
        given = min(
            firsts,
            key=lambda item: abs((item[0] + item[1]) / 2 - center),
            default=None,
        )
        named.append(
            (end, f"{given[2]} {surname}" if given and abs((given[0] + given[1]) / 2 - center) < 14 else surname)
        )
    return named


def read(party: str, frame: list[tuple[int, str, str, int]], fips: dict[str, str]) -> None:
    url, _ = FILES[party]
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    lines = text.splitlines()
    district = 0
    cols: list[tuple[int, str]] = []
    totals: dict[tuple[int, str], int] = {}
    sums: dict[tuple[int, str], int] = {}
    for index, line in enumerate(lines):
        found = DISTRICT.match(line)
        if found:
            district = int(found.group(1))
            continue
        if line.startswith("COUNTY"):
            cols = columns(lines[index - 1], line)
            continue
        if not cols or not line.strip() or line.startswith(("Tuesday", "State of", "Democratic", "Republican", " ")):
            continue
        county = re.match(r"^([A-Za-z .'-]+?)(?=\s{2,}|\s+\d|$)", line)
        if county is None:
            continue
        name = county.group(1).strip()
        for number in NUMBER.finditer(line, county.end()):
            end = number.end()
            _, candidate = min(cols, key=lambda col: abs(col[0] - end))
            value = int(number.group().replace(",", ""))
            key = (district, candidate)
            if name == "District Total":
                totals[key] = value
            else:
                sums[key] = sums.get(key, 0) + value
                frame.append((district, candidate, name, value))
    for key, value in totals.items():
        if sums.get(key) != value:
            raise ValueError(f"{party} {key}: counties sum to {sums.get(key)}, printed {value}")
    for key in sums:
        if key not in totals:
            raise ValueError(f"{party} {key}: no printed total")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TN"]
    rows: list[tuple[str, int, str, str, int]] = []
    for party in FILES:
        part: list[tuple[int, str, str, int]] = []
        read(party, part, fips)
        rows.extend((party, *row) for row in part)
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
    frame.select(
        year=pl.lit(1998, pl.Int32),
        chamber=pl.concat_str(pl.lit("house"), pl.col("party"), separator=":"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("TN"),
        district=pl.col("district").cast(pl.Int16),
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
            summary="Tennessee's August 6, 1998 U.S. House primaries by county, from the Division of Elections' official results",
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
        for url, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, frame, {"TN": source}, strict=True)
        return cs.write()
