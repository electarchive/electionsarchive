import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.md_pres_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://elections.maryland.gov/elections/archive/{year}/results/primary/gen_detail_results_{year}_3_{party}001-.html"
DAYS = {2016: date(2016, 4, 26), 2020: date(2020, 6, 2)}
PARTIES = {"DEM": "democratic", "REP": "republican"}
PUBLISHER = "Maryland State Board of Elections"
TITLE = "Election results archive: presidential primaries"


def pages() -> list[tuple[int, str, str]]:
    return [(year, party, ARCHIVE.format(year=year, party=party)) for year in DAYS for party in PARTIES]


def fetch() -> None:
    for _, _, url in pages():
        raw.fetch(SOURCE, url, suffix=".html")
        if "Totals" not in raw.cached(SOURCE, url).path.read_text(errors="replace"):
            raise ValueError(f"{url}: no results table")


def parse() -> None:
    with connect() as conn:
        counties = {
            name.removesuffix(", MD").removesuffix(" County").lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '24%%' and u.valid @> '2016-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for year, party, url in pages():
        text = raw.cached(SOURCE, url).path.read_text(errors="replace")
        for table in re.findall(r"<table.*?</table>", text, re.DOTALL):
            lines = [
                [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                    for cell in re.findall(r"<t[hd].*?</t[hd]>", line, re.DOTALL)
                ]
                for line in re.findall(r"<tr.*?</tr>", table, re.DOTALL)
            ]
            if len(lines) < 20 or lines[0][0] not in ("", "Jurisdiction"):
                continue
            names = [re.sub(rf"\s+{PARTIES[party]}$", "", cell, flags=re.IGNORECASE) for cell in lines[0][1:]]
            for county, *votes in lines[1:]:
                if county == "Totals":
                    continue
                if len(votes) != len(names):
                    raise ValueError(f"{year} {party} {county}: {len(votes)} values for {len(names)} candidates")
                for candidate, value in zip(names, votes, strict=True):
                    rows.append(
                        (year, PARTIES[party], counties[county.lower()], candidate, int(value.replace(",", "")))
                    )
    frame = pl.DataFrame(
        rows,
        schema={"year": pl.Int32, "party": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    )
    if frame.select("year", "party", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("year", "party")
        .agg(pl.col("code").n_unique(), pl.col("candidate").n_unique(), pl.col("votes").sum())
        .sort("year", "party")
        .rows()
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's 2016 and 2020 Democratic and Republican presidential primaries by county from the State Board of Elections' results archive",
            polity="US-MD",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.maryland.gov/elections/archive/index.html",
            license="Public record",
        )
        for _, _, url in pages():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                "year",
                chamber=pl.lit("president:") + pl.col("party"),
                day=pl.col("year").replace_strict({year: day for year, day in DAYS.items()}, return_dtype=pl.Date),
                special=pl.lit(False),
                state=pl.lit("MD"),
                district=pl.lit(None, pl.Int16),
                code="code",
                candidate=pl.col("candidate"),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("MD"),
            ),
            {"MD": source},
            strict=True,
        )
        return cs.write()
