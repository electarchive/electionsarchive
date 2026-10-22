import re
from datetime import date
from difflib import get_close_matches
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_house_history"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WORKBOOK = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/eaushouse1980-2012_0.xls"
LANDING = "https://www.sos.alabama.gov/alabama-votes/voter/election-data"
PUBLISHER = "Alabama Secretary of State"
TITLE = "Election data: U.S. House election history 1980-2012"
STACKED = {
    "Gen.80": (date(1980, 11, 4), "house"),
    "Gen82": (date(1982, 11, 2), "house"),
    "Gen. 84": (date(1984, 11, 6), "house"),
    "GEN86": (date(1986, 11, 4), "house"),
    "GEN90": (date(1990, 11, 6), "house"),
    "GEN92": (date(1992, 11, 3), "house"),
    "GEN96": (date(1996, 11, 5), "house"),
    "GEN98": (date(1998, 11, 3), "house"),
    "DEMPRI94": (date(1994, 6, 7), "house:democratic"),
    "REPPRI94": (date(1994, 6, 7), "house:republican"),
    "REPRO94": (date(1994, 6, 28), "house:republican"),
    "DEMPRI96": (date(1996, 6, 4), "house:democratic"),
    "REPPRI96": (date(1996, 6, 4), "house:republican"),
    "DEMPRIRO": (date(1996, 6, 25), "house:democratic"),
    "RPRIRO96": (date(1996, 6, 25), "house:republican"),
    "DEMPRI98": (date(1998, 6, 2), "house:democratic"),
    "DEMPRI00": (date(2000, 6, 6), "house:democratic"),
    "REPPRI00": (date(2000, 6, 6), "house:republican"),
    "Dem.Pri.02": (date(2002, 6, 4), "house:democratic"),
    "Rep.Pri.02": (date(2002, 6, 4), "house:republican"),
    "Rep.RO.02": (date(2002, 6, 25), "house:republican"),
}
SIDE = {
    "GEN00": (date(2000, 11, 7), "house"),
    "Gen.02.Hse.1&2": (date(2002, 11, 5), "house"),
    "Hse.3&4": (date(2002, 11, 5), "house"),
    "Hse.5&6": (date(2002, 11, 5), "house"),
    "Hse.7": (date(2002, 11, 5), "house"),
    "2004 General": (date(2004, 11, 2), "house"),
    "06Gen": (date(2006, 11, 7), "house"),
    "08Gen": (date(2008, 11, 4), "house"),
    "06Dem.Pri": (date(2006, 6, 6), "house:democratic"),
    "08Dem.Pri.": (date(2008, 6, 3), "house:democratic"),
    "12 Rep.Pri.": (date(2012, 3, 13), "house:republican"),
}
WRITEIN = re.compile(r"^(w/?i|write[- ]?ins?)$", re.IGNORECASE)
DISTRICT = re.compile(r"(?:district|dist|cong\w*)\s*(\d)\b", re.IGNORECASE)
HEADING = re.compile(r"(\d)(?:st|nd|rd|th) Congres|^District (\d)$")


def fetch() -> None:
    raw.fetch(SOURCE, WORKBOOK, suffix=".xls")


def cell(text: object) -> str:
    return " ".join(str(text or "").split())


def number(text: object) -> int | None:
    text = cell(text).replace(",", "")
    return int(float(text)) if re.fullmatch(r"\d+(\.0+)?", text) else None


def stacked(grid: list[tuple[object, ...]], county: dict[str, str]) -> list[tuple[int, str, str, bool, int]]:
    out = []
    r = 0
    while r < len(grid):
        found = next((m for c in grid[r] if (m := HEADING.search(cell(c)))), None)
        if found is None:
            r += 1
            continue
        district = int(found[1] or found[2])
        start = next((i for i in range(r + 1, len(grid)) if cell(grid[i][0]) in county), len(grid))
        columns = {
            i: " ".join(cell(row[i]) for row in grid[r:start] if cell(row[i]) and not HEADING.search(cell(row[i])))
            for i in range(1, len(grid[r]))
        }
        columns = {i: name for i, name in columns.items() if name and not name.lower().startswith("total")}
        sums = dict.fromkeys(columns, 0)
        block = []
        r = start
        while r < len(grid) and cell(grid[r][0]) in county:
            for i, name in columns.items():
                if (votes := number(grid[r][i])) is not None:
                    block.append((district, county[cell(grid[r][0])], name, bool(WRITEIN.match(name)), votes))
                    sums[i] += votes
            r += 1
        printed = {i: number(grid[r][i]) for i in columns} if r < len(grid) else {}
        if any(printed.get(i) is not None and printed[i] != sums[i] for i in columns):
            print(f"district {district}: county sums {sums} against printed {printed}, skipped")
        else:
            out.extend(block)
    return out


def side(grid: list[tuple[object, ...]], county: dict[str, str]) -> list[tuple[int, str, str, bool, int]]:
    rows = [r for r, row in enumerate(grid) if any(cell(c) in county for c in row[:2])]
    at = next(i for i in range(2) if cell(grid[rows[0]][i]) in county)
    head = next(
        (r for r in range(rows[0]) if cell(grid[r][0]) == "Ballot Name"),
        next(
            r
            for r in range(rows[0] - 1, -1, -1)
            if not any(re.fullmatch(r"[\d.,]+", cell(c)) for c in grid[r]) and any(cell(c) for c in grid[r][at + 1 :])
        ),
    )
    districts: dict[int, int] = {}
    current = None
    for i, text in enumerate(grid[0]):
        if i > at and (found := DISTRICT.search(cell(text))):
            current = int(found[1])
        if i > at and current is not None:
            districts[i] = current
    names = {i: cell(grid[head][i]) for i in districts if cell(grid[head][i])}
    out = []
    for r in rows:
        code = county[cell(grid[r][at])]
        for i, name in names.items():
            if (votes := number(grid[r][i])) is not None:
                out.append((districts[i], code, name, bool(WRITEIN.match(name)), votes))
    return out


def parse() -> None:
    with connect() as conn:
        names = {
            text: code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    path = raw.cached(SOURCE, WORKBOOK).path
    frames = []
    for sheet, (day, chamber) in {**STACKED, **SIDE}.items():
        grid = pl.read_excel(path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()
        labels = {cell(c) for row in grid for c in row[:2] if cell(c) and not number(c)}
        county = {
            label: names[match[0]]
            for label in labels
            if (match := get_close_matches(label.removesuffix(" County"), list(names), n=1, cutoff=0.85))
        }
        rows = stacked(grid, county) if sheet in STACKED else side(grid, county)
        frames.append(
            pl.DataFrame(
                rows, schema=["district", "code", "candidate", "writein", "votes"], orient="row"
            ).with_columns(day=pl.lit(day), chamber=pl.lit(chamber))
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.concat(frames)
        .with_columns(
            candidate=pl.when("writein")
            .then(pl.lit("WRITE-INS"))
            .otherwise(
                pl.col("candidate").str.replace(r"\s*(\(\w+\)|-\s*\w{1,3})$", "").str.to_uppercase()
            ),
        )
        .group_by("day", "chamber", "district", "code", "candidate")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("AL"),
            district=pl.col("district").cast(pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's U.S. House generals by county, 1992 and 1996-2008, and primaries and runoffs 1994-2012 (Secretary of State's election history)",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, WORKBOOK), source)
        medsl.attach(cs, conn, frame, {"AL": source}, strict=True)
        return cs.write()
