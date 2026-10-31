import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ia_caucus_2024"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "https://iowagop.org/2024-caucus-results-by-county/"
DAY = date(2024, 1, 15)
PUBLISHER = "Republican Party of Iowa"
TITLE = "2024 Caucus Results by County"


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            name.removesuffix(", IA").removesuffix(" County").lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '19%%' and u.valid @> '2024-01-01'::date"""
            ).fetchall()
        }
    text = raw.cached(SOURCE, PAGE).path.read_text(errors="replace")
    table = max(re.findall(r"<table.*?</table>", text, re.DOTALL), key=len)
    lines = [
        [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
            for cell in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", line, re.DOTALL)
        ]
        for line in re.findall(r"<tr.*?</tr>", table, re.DOTALL)
    ]
    names = lines[0][2:-1]
    rows = []
    for county, _, *votes in lines[1:]:
        if len(votes) != len(names) + 1 or sum(int(v) for v in votes[:-1]) != int(votes[-1]):
            raise ValueError(f"{county}: row does not sum to its total")
        for candidate, value in zip(names, votes[:-1], strict=True):
            rows.append((counties[county.lower().replace("’", "'")], candidate, int(value)))
    frame = (
        pl.DataFrame(rows, schema={"code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row")
        .group_by("code", "candidate")
        .agg(pl.col("votes").sum())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame["code"].n_unique(), frame.group_by("candidate").agg(pl.col("votes").sum()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 2024 Republican caucus by county, summed from the Republican Party of Iowa's precinct results",
            polity="US-IA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.lit(DAY.year, pl.Int32),
                chamber=pl.lit("president:republican"),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("IA"),
                district=pl.lit(None, pl.Int16),
                code="code",
                candidate=pl.when(pl.col("candidate") == "Other").then(pl.lit("OTHER")).otherwise("candidate"),
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("IA"),
            ),
            {"IA": source},
            strict=True,
        )
        return cs.write()
