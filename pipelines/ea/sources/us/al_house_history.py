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
STACKED = {"GEN92": date(1992, 11, 3), "GEN96": date(1996, 11, 5), "GEN98": date(1998, 11, 3)}
SIDE = {
    "GEN00": date(2000, 11, 7),
    "Gen.02.Hse.1&2": date(2002, 11, 5),
    "Hse.3&4": date(2002, 11, 5),
    "Hse.5&6": date(2002, 11, 5),
    "Hse.7": date(2002, 11, 5),
    "2004 General": date(2004, 11, 2),
    "06Gen": date(2006, 11, 7),
    "08Gen": date(2008, 11, 4),
}
WRITEIN = re.compile(r"^(w/?i|write[- ]?ins?)$", re.IGNORECASE)
DISTRICT = re.compile(r"(?:district|congress)\s*(\d)\b", re.IGNORECASE)


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
        found = next(
            (m for c in grid[r] if (m := re.search(r"(\d)(?:st|nd|rd|th) Congression", cell(c)))), None
        )
        if found is None:
            r += 1
            continue
        district = int(found[1])
        first, last = grid[r + 1], grid[r + 2]
        columns = {
            i: cell(f"{cell(first[i])} {cell(last[i])}")
            for i in range(1, len(last))
            if cell(first[i]) or cell(last[i])
        }
        total = next(i for i, name in columns.items() if name.lower().startswith("total"))
        columns.pop(total)
        sums = dict.fromkeys(columns, 0)
        r += 3
        while r < len(grid) and cell(grid[r][0]):
            code = county[cell(grid[r][0])]
            for i, name in columns.items():
                if (votes := number(grid[r][i])) is not None:
                    out.append((district, code, name, bool(WRITEIN.match(name)), votes))
                    sums[i] += votes
            r += 1
        printed = {i: number(grid[r][i]) for i in columns}
        if any(printed[i] is not None and printed[i] != sums[i] for i in columns):
            raise ValueError(f"district {district}: county sums {sums} against printed {printed}")
    return out


def side(grid: list[tuple[object, ...]], county: dict[str, str]) -> list[tuple[int, str, str, bool, int]]:
    rows = [r for r, row in enumerate(grid) if any(cell(c) in county for c in row[:2])]
    at = next(i for i in range(2) if cell(grid[rows[0]][i]) in county)
    head = next(
        r
        for r in range(rows[0] - 1, -1, -1)
        if not any(re.fullmatch(r"[\d.,]+", cell(c)) for c in grid[r]) and any(cell(c) for c in grid[r][at + 1 :])
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
    for sheet, day in {**STACKED, **SIDE}.items():
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
            ).with_columns(day=pl.lit(day))
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
        .group_by("day", "district", "code", "candidate")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            chamber=pl.lit("house"),
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
            summary="Alabama's U.S. House generals by county, 1992 and 1996-2008 (Secretary of State's election history)",
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
