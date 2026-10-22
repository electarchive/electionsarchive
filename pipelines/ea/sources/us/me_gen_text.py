import csv
import io
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.me_federal import COUNTIES, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.me_gen_text"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Election results: general election tabulations by municipality"
WAYBACK = "https://web.archive.org/web/{}"
FILES = [
    ("2012id_/http://www.maine.gov/sos/cec/elec/2000g/gen00sr.txt", date(2000, 11, 7), "house"),
    ("2012id_/http://www.maine.gov/sos/cec/elec/2000g/gen00ss.txt", date(2000, 11, 7), "senate"),
    ("2012id_/http://www.maine.gov/sos/cec/elec/2002g/gen02sr.txt", date(2002, 11, 5), "house"),
    ("2012id_/http://www.maine.gov/sos/cec/elec/2002g/gen02ss.txt", date(2002, 11, 5), "senate"),
    ("2012id_/http://www.maine.gov/sos/cec/elec/2006g/sr.txt", date(2006, 11, 7), "house"),
    ("2012id_/http://www.maine.gov/sos/cec/elec/2006g/ss.txt", date(2006, 11, 7), "senate"),
    ("2012id_/http://www.maine.gov/sos/cec/elec/2008/tabs-can-sr-11-08.txt", date(2008, 11, 4), "house"),
    ("2012id_/http://www.maine.gov/sos/cec/elec/2008/tabs-can-ss-11-08.txt", date(2008, 11, 4), "senate"),
    ("2015id_/http://www.maine.gov:80/sos/cec/elec/results/2010-11/staterep.txt", date(2010, 11, 2), "house"),
    ("2015id_/http://www.maine.gov:80/sos/cec/elec/results/2010-11/statesenator.txt", date(2010, 11, 2), "senate"),
]
UNLISTED = {"CHEBEAGUEISLAND": "CUM", "NO14": "WAS"}


def fetch() -> None:
    for path, *_ in FILES:
        raw.fetch(SOURCE, WAYBACK.format(path), suffix=".txt")


def parse() -> None:
    lines: list[tuple[date, str, int, str | None, str, str, int]] = []
    for path, day, chamber in FILES:
        text = raw.cached(SOURCE, WAYBACK.format(path)).path.read_text("latin-1")
        if day.year == 2000:
            for row in csv.reader(io.StringIO(text.split("-" * 53)[-1].strip())):
                for start in range(5, len(row) - 6, 7):
                    if row[start].strip():
                        last, first, middle, suffix = (part.strip() for part in row[start + 1 : start + 5])
                        name = f"{last}, {' '.join(part for part in (first, middle, suffix) if part)}"
                        lines.append((day, chamber, int(row[4]), row[1], row[2], name, int(row[start])))
            continue
        if day.year == 2010:
            names: list[str] = []
            heading = 0
            for row in csv.reader(io.StringIO(text), delimiter="|"):
                if len(row) > 5 and not any(cell.strip() for cell in row[:5]):
                    heading += 1
                    if heading == 1:
                        names = [cell.strip() for cell in row[5:]]
                    continue
                heading = 0
                if len(row) > 5 and row[1].strip().isdigit():
                    for name, votes in zip(names, row[5:], strict=False):
                        if name and name.upper() != "BLANK" and votes.strip():
                            lines.append(
                                (
                                    day,
                                    chamber,
                                    int(row[1]),
                                    row[2].strip() or None,
                                    row[3].strip(),
                                    name.upper(),
                                    int(votes.replace(",", "")),
                                )
                            )
            continue
        rows = list(csv.reader(io.StringIO(text)))
        header = [cell.strip().lower() for cell in rows[0]]
        for row in rows[1:]:
            cells = dict(zip(header, row, strict=False))
            numbered = [
                (cells.get(f"fullname{n}") or cells.get(f"can{n}_name"), cells.get(f"votes{n}"))
                for n in range(1, 10)
                if f"votes{n}" in cells
            ]
            for name, votes in numbered:
                if name and name.strip():
                    lines.append(
                        (
                            day,
                            chamber,
                            int(cells["district"]),
                            cells.get("county"),
                            cells.get("town") or cells["town_name"],
                            " ".join(name.split()),
                            int((votes or "0").replace(",", "")),
                        )
                    )
    frame = pl.DataFrame(
        lines,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "county": pl.String,
            "town": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        place=pl.col("town")
        .str.to_uppercase()
        .str.replace_all(r"\bST\b\.?", "SAINT")
        .str.replace_all(r"\bPLANTATION\b", "PLT")
        .str.replace_all(r"\b(?:UN\.? TWP\.? (?:OF )?|TWP\.?$)", "")
        .str.replace_all(r"[^A-Z0-9]", ""),
        county=pl.col("county").str.strip_chars(),
        uocava=pl.col("town").str.to_uppercase().str.contains("UOCAVA"),
    )
    homes = frame.filter(pl.col("county").is_not_null()).select("place", "county").unique()
    if homes.filter(pl.len().over("place") > 1).height:
        print("towns under two counties:", homes.filter(pl.len().over("place") > 1).sort("place").rows()[:10])
    homes = homes.filter(pl.len().over("place") == 1)
    frame = frame.join(homes.rename({"county": "home"}), on="place", how="left").with_columns(
        county=pl.coalesce("county", "home", pl.col("place").replace_strict(UNLISTED, default=None))
    )
    lost = frame.filter(pl.col("county").is_null(), ~pl.col("uocava"))
    if lost.height:
        raise ValueError(f"no county for {lost['town'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.select(
            year=pl.col("day").dt.year().cast(pl.Int32),
            chamber=pl.concat_str("chamber", pl.col("district").cast(pl.String), pl.lit("g"), separator=":"),
            day="day",
            special=pl.lit(False),
            state=pl.lit("ME"),
            district=pl.lit(None, pl.Int16),
            code=pl.when("uocava")
            .then(pl.lit("ME:uocava"))
            .otherwise(pl.col("county").replace_strict(COUNTIES, default=None)),
            kind=pl.when("uocava").then(pl.lit("aggregate")).otherwise(pl.lit("county")),
            candidate=pl.col("candidate").str.to_uppercase(),
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").str.contains("(?i)write|others"),
            votes="votes",
        )
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.lit("ME"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status <> 'withdrawn'
                   where x.scheme = 'us.contest' and x.value ~ '^20(00|02|06|08|10)-11-0[0-9]:ME:(house|senate):[0-9]+:g$'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.precinct', 'us.county', 'us.town', 'us.county_part'))"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2000, 2002, 2006, 2008, and 2010 State House and Senate generals by county",
            polity="US-ME",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.maine.gov/sos/cec/elec/results/index.html",
            license="Public record",
        )
        for path, *_ in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(path)), source)
        frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit("ME"), "chamber", separator=":").is_in(list(filled))
        )
        medsl.attach(cs, conn, frame, {"ME": source}, strict=True)
        return cs.write()
