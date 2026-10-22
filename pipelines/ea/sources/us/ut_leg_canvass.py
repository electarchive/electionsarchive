import re
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ut_house, ut_sos
from ea.store import DATA, connect

SOURCE = "us.ut_leg_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {2008: date(2008, 11, 4), 2010: date(2010, 11, 2), 2012: date(2012, 11, 6), 2014: date(2014, 11, 4)}
CORRECTED = {
    (2008, "house:29:g", "Phillip M. Conder"): (
        3562,
        2562,
        (
            "The canvass prints Phillip M. Conder's Salt Lake County vote as 3,562 but his total as 2,562; the total "
            "matches the printed office sum (7,113) and his printed share (36.0%), so the county cell is read as 2,562."
        ),
    ),
    (2008, "house:60:g", "Boyd Mcaffee"): (
        2857,
        2897,
        (
            "The canvass prints Boyd McAffee's Utah County vote as 2,857 but his total as 2,897; the total matches the "
            "printed office sum (7,464) and his printed share (38.8%), so the county cell is read as 2,897."
        ),
    ),
    (2008, "house:60:g", "Bradley M. Daw"): (
        6940,
        3940,
        (
            "The canvass prints Bradley M. Daw's Utah County vote as 6,940 but his total as 3,940; the total matches the "
            "printed office sum (7,464) and his printed share (52.8%), so the county cell is read as 3,940."
        ),
    ),
}
DISTRICT = re.compile(r"(?i)State (House|Senate)(?: District)?\s+(\d+)")


def cached(year: int) -> raw.Artifact:
    if year == 2014:
        return raw.cached(ut_house.SOURCE, ut_house.VOTE + ut_house.FILES[2014])
    stamp, path = ut_sos.FILES[(year, "general")]
    return raw.cached(ut_sos.SOURCE, path if stamp is None else ut_sos.WAYBACK.format(stamp, ut_sos.SITE + path))


def fetch() -> None:
    ut_house.fetch()


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '49%%'"""
            ).fetchall()
        }
    rows: list[tuple[date, str, str, str, int]] = []
    for year, day in DAYS.items():
        workbook = fastexcel.read_excel(cached(year).path)
        for sheet in workbook.sheet_names:
            if not re.search(r"(?i)(senate|house)", sheet) or re.match(r"(?i)U\.? ?S\.? ", sheet):
                continue
            table = [
                [(cell or "").strip() for cell in row]
                for row in workbook.load_sheet(sheet, header_row=None, dtypes="string").to_polars().rows()
            ]
            keys: dict[int, str] = {}
            key = None
            for column, cell in enumerate(table[0]):
                if column >= 4 and (match := DISTRICT.search(cell)):
                    key = f"{match[1].lower()}:{int(match[2])}:g"
                if column >= 4 and key is not None and table[1][column]:
                    keys[column] = key
            printed = next(row for row in table if row[0].upper() == "TOTAL")
            sums: dict[int, int] = {}
            for row in table[2:]:
                if row[0].upper() == "TOTAL":
                    break
                code = codes.get(re.sub(r"[^a-z]", "", row[0].lower()))
                if code is None:
                    raise ValueError(f"{year} {sheet}: no county {row[0]}")
                for column, key in keys.items():
                    if row[column]:
                        votes = int(float(row[column]))
                        candidate = re.sub(r'\s*(?:"[A-Z]+"|\([A-Z]+\))\s*$', "", " ".join(table[1][column].split()))
                        if (fix := CORRECTED.get((year, key, candidate))) and votes == fix[0]:
                            votes = fix[1]
                        rows.append((day, key, code, candidate, votes))
                        sums[column] = sums.get(column, 0) + votes
            for column, key in keys.items():
                if printed[column] and sums.get(column, 0) != int(float(printed[column])):
                    print(
                        f"{year} {key}: {table[1][column]} counties {sums.get(column)}, printed {printed[column]}, skipped"
                    )
                    rows = [row for row in rows if row[:2] != (day, key)]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row").select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("UT"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind=pl.lit("county"),
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("(?i)write"),
        votes="votes",
        source=pl.lit("UT"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        filled = [
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status <> 'withdrawn'
                   where x.scheme = 'us.contest' and x.value ~ '^20(08|10|12|14)-11-0[0-9]:UT:(house|senate):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k and u.scheme_code in ('us.precinct', 'us.county', 'us.county_part'))"""
            ).fetchall()
        ]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's 2008, 2010, 2012, and 2014 State House and Senate generals by county from the Office of "
            "Elections' official canvasses",
            polity="US-UT",
        )
        source = cs.source(ut_sos.PUBLISHER, ut_sos.TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {ut_sos.TITLE}")
        for year in DAYS:
            cs.artifact(cached(year), source)
        frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit("UT"), "chamber", separator=":").is_in(filled)
        )
        medsl.attach(cs, conn, frame, {"UT": source}, strict=True, canvass=True)
        for (year, key, _), (*_, caveat) in CORRECTED.items():
            result_set = cs.key(f"{SOURCE}.result_set", f"{DAYS[year]}:UT:{key}:county")
            if result_set in {row["id"] for row in cs.rows.get("result_set", [])}:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
        return cs.write()
