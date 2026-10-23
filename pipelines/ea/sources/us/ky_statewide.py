import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ky_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/2020id_/https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2000-2009/{}/General%20Election/{}.txt"
YEARS = {2003: "2003-11-04", 2007: "2007-11-06"}
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "Official election night tally results by county"
OFFICES = {
    "D05": "Governor",
    "D07": "Secretary of State",
    "D08": "Attorney General",
    "D09": "Auditor",
    "D10": "Treasurer",
    "D12": "Commissioner of Agriculture",
}
PARTIES = {"D": "DEMOCRATIC", "R": "REPUBLICAN", "I": "INDEPENDENT"}
METHOD = (
    "County totals come from the State Board of Elections' official election night tally reports by county, "
    "with each candidate's party from its statewide-by-office report."
)


def address(year: int, kind: str) -> str:
    return SITE.format(year, {"county": "statewidebycounty", "office": "statewidebyoffice"}[kind])


def fetch() -> None:
    for year in YEARS:
        for kind in ("county", "office"):
            raw.fetch(SOURCE, address(year, kind), suffix=".txt")


def parse() -> None:
    with connect() as conn:
        names = sorted(
            (re.sub(r"^mc", "m!", re.sub(r"[^a-z]", "", text.lower())), code)
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%'"""
            ).fetchall()
        )
    rows = []
    for year, day in YEARS.items():
        parties = {}
        office = None
        for line in raw.cached(SOURCE, address(year, "office")).path.read_text(errors="replace").splitlines():
            if match := re.match(r"\s*OFF/DST/DIV: (D\d\d)/", line):
                office = match[1]
            elif office in OFFICES and (year, office) != (2007, "D05") and (match := re.match(r"\s+([DRI])\s{2,}(\S.*?)\s{2,}[\d,]+\s+[\d.]+%", line)):
                parties[(office, re.sub(r"\s+", " ", match[2]).split(" & ")[0].upper())] = PARTIES[match[1]]
        office = None
        totals: dict[tuple[str, str], int] = {}
        columns: list[str] = []
        pending: list[tuple[str, str]] = []
        abbreviations: list[str] = []
        for line in raw.cached(SOURCE, address(year, "county")).path.read_text(errors="replace").splitlines():
            if match := re.search(r"OFFICE: (\w\d\d)/", line):
                office = match[1] if match[1] in OFFICES and (year, match[1]) != (2007, "D05") else None
                columns = []
                continue
            if office is None:
                continue
            if "*" in line and not line[:30].strip():
                columns = re.findall(r"\*(\S+)", line)
                if office == "D07":
                    abbreviations.extend(columns)
                continue
            if not line.strip():
                continue
            split = re.match(r"\s*(.*?\S)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$", line)
            if split is None and re.search(r"KENTUCKY STATE BOARD|REPORT OF|TIME OF REPORT", line):
                continue
            if split is None:
                raise ValueError(f"{year} {office}: unreadable line {line!r}")
            cells = split[2].split()
            candidate = re.sub(r"\s+", " ", split[1]).split(" & ")[0].upper()
            if len(cells) == 1 and len(columns) > 1:
                totals[(office, candidate)] = int(cells[0].replace(",", ""))
                continue
            if len(cells) != len(columns):
                raise ValueError(f"{year} {office} {candidate}: {len(cells)} cells for {len(columns)} counties")
            for abbreviation, votes in zip(columns, cells, strict=True):
                rows.append((year, day, office, candidate, abbreviation, int(votes.replace(",", ""))))
        if len(abbreviations) != 120 or len(set(abbreviations)) != 120:
            raise ValueError(f"{year}: {len(abbreviations)} county columns")
        order = {abbreviation: index for index, abbreviation in enumerate(abbreviations)}
        year_rows = [row for row in rows if row[0] == year]
        for abbreviation, index in order.items():
            if not names[index][0].replace("m!", "mc", 1).startswith(abbreviation[:3].lower()):
                raise ValueError(f"{year}: {abbreviation} lines up with {names[index][0]}")
        mapped = {abbreviation: names[index][1] for abbreviation, index in order.items()}
        rows = [row for row in rows if row[0] != year] + [(*row[:4], mapped[row[4]], row[5]) for row in year_rows]
        for key in {(row[2], row[3]) for row in year_rows}:
            summed = sum(row[5] for row in year_rows if (row[2], row[3]) == key)
            if totals.get(key) != summed:
                raise ValueError(f"{year} {key}: counties sum to {summed}, printed total {totals.get(key)}")
            if key not in parties:
                raise ValueError(f"{year}: no party for {key}")
        PARSED.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            [(*row, parties[(row[2], row[3])]) for row in rows if row[0] == year],
            schema=["year", "day", "office", "candidate", "county", "votes", "party"],
            orient="row",
        ).write_parquet(PARSED / f"{year}.parquet")
    frame = pl.concat([pl.read_parquet(PARSED / f"{year}.parquet") for year in YEARS]).select(
        year=pl.col("year").cast(pl.Int32),
        day="day",
        state=pl.lit("KY"),
        title=pl.col("office").replace_strict(OFFICES),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party="party",
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    frame.filter(pl.col("title") == "Governor").write_parquet(PARSED / "governor.parquet")
    frame = frame.filter(pl.col("title") != "Governor")
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(120),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "title", "candidate", "party").agg(pl.col("votes").sum()).sort("year", "title", "votes").rows())
    print(frame.group_by("year", "title").agg(pl.col("county").n_unique()).sort("year", "title").rows())


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elect.ky.gov/results/",
                license="Public record",
            )
        for year in YEARS:
            for kind in ("county", "office"):
                cs.artifact(raw.cached(SOURCE, address(year, kind)), source)
        return {"KY": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Kentucky's 2003 and 2007 secretary of state, attorney general, auditor, treasurer, and commissioner "
        "of agriculture by county from the State Board of Elections' official results",
        cite=cite,
        method=METHOD,
        renamed={"2003-11-04:KY": "2003:KY"},
    )
