import re
import unicodedata
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/"
PUBLISHER = "Alabama Secretary of State"
TITLE = "Election data: state legislature results by county"
FILES = {
    2002: ("eastateleg02.xls", date(2002, 11, 5), {"General 02": "blocks"}),
    2006: (
        "eastateleg06.xls",
        date(2006, 11, 7),
        {"Senate General '06": "senate", "House General '06": "house"},
    ),
    2010: (
        "eastateleg10.xlsx",
        date(2010, 11, 2),
        {"Gen.10 Senate": "senate", "Gen.10 House": "house"},
    ),
}
HEADER = re.compile(r"\s*(senate|house)\s+(\d+)\s*", re.IGNORECASE)
DISTRICT = re.compile(r"\s*district\s+(\d+)\s*", re.IGNORECASE)


def fetch() -> None:
    for name, _, _ in FILES.values():
        raw.fetch(SOURCE, BASE + name, suffix=Path(name).suffix)


def name(cell: object) -> tuple[str, bool]:
    text = re.sub(r"\s*\(\s*[A-Z]{1,3}\s*\)\s*$", "", re.sub(r"\s+", " ", str(cell or "")).strip())
    if re.fullmatch(r"(?i)w/?i|write[- ]?ins?", text):
        return "WRITE-INS", True
    return text.upper(), False


def number(cell: object) -> int | None:
    try:
        return int(float(str(cell).replace(",", "")))
    except ValueError:
        return None


def parse() -> None:
    with connect() as conn:
        fips: dict[str, str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '01%%' order by u.code"""
        ).fetchall():
            fips.setdefault(re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()), code)

    def county(cell: object) -> str | None:
        return fips.get(re.sub(r"[^a-z]", "", str(cell or "").lower()))

    rows: list[tuple[int, str, date, str, str, int, bool]] = []
    for year, (file, day, sheets) in FILES.items():
        workbook = fastexcel.read_excel(raw.cached(SOURCE, BASE + file).path)
        for sheet, layout in sheets.items():
            table = workbook.load_sheet(sheet, header_row=None).to_polars().rows()
            if layout == "blocks":
                for index, row in enumerate(table):
                    for column, cell in enumerate(row):
                        found = HEADER.fullmatch(str(cell)) if cell else None
                        if not found or column == 0:
                            continue
                        chamber = f"{found[1].lower()}:{int(found[2])}:g"
                        names = []
                        for candidate in table[index + 1][column:]:
                            if not str(candidate or "").strip():
                                break
                            names.append(name(candidate))
                        lone = county(table[index + 1][column - 1])
                        for line in table[index + 2 :]:
                            code = lone or county(line[column - 1])
                            if code is None:
                                break
                            for offset, (candidate, writein) in enumerate(names):
                                votes = number(line[column + offset])
                                if votes is not None:
                                    rows.append((year, chamber, day, code, candidate, votes, writein))
                            if lone:
                                break
            elif layout == "senate":
                head = table[0]
                for column, cell in enumerate(head):
                    found = DISTRICT.fullmatch(str(cell)) if cell else None
                    if not found:
                        continue
                    chamber = f"senate:{int(found[1])}:g"
                    width = 1
                    while column + width < len(head) and not head[column + width]:
                        width += 1
                    for line in table[2:]:
                        code = county(line[0])
                        if code is None:
                            continue
                        for offset in range(width):
                            votes = number(line[column + offset])
                            candidate, writein = name(table[1][column + offset])
                            if votes is not None and candidate:
                                rows.append((year, chamber, day, code, candidate, votes, writein))
            else:
                top = next(
                    index for index, row in enumerate(table) if sum(county(cell) is not None for cell in row) > 3
                )
                columns = {index: county(cell) for index, cell in enumerate(table[top]) if index > 3 and county(cell)}
                chamber = ""
                for line in table[top + 1 :]:
                    found = DISTRICT.fullmatch(str(line[0])) if line[0] else None
                    if found:
                        chamber = f"house:{int(found[1])}:g"
                    candidate, writein = name(line[1])
                    if not chamber or not candidate:
                        continue
                    for column, code in columns.items():
                        votes = number(line[column])
                        if votes is not None:
                            rows.append((year, chamber, day, code, candidate, votes, writein))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "day": pl.Date,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
                "writein": pl.Boolean,
            },
            orient="row",
        )
        .group_by("year", "chamber", "day", "code", "candidate", "writein")
        .agg(pl.col("votes").sum())
        .with_columns(
            special=pl.lit(False),
            state=pl.lit("AL"),
            district=pl.lit(None, pl.Int16),
            measure=pl.lit(1, dtype=pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   where r.status = 'active' and x.value ~ '^(2002|2006|2010)-[0-9-]+:AL:'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))"""
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit("AL"), "chamber", separator=":").is_in(list(covered))
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2002, 2006, and 2010 state legislative general elections by county",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            license="Public record",
        )
        for name_, _, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + name_), source)
        medsl.attach(cs, conn, frame, {"AL": source}, strict=True)
        return cs.write()
