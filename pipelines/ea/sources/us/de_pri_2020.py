import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.de_pri_2020"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://elections.delaware.gov/reports/PR2020.html"
DAY = date(2020, 9, 15)
PUBLISHER = "Delaware Department of Elections"
TITLE = "2020 primary election results"
CONTESTS = {
    ("U.S. Senator", "Democratic"): ("senate:democratic", None),
    ("U.S. Senator", "Republican"): ("senate:republican", None),
    ("Representative in Congress", "Republican"): ("house:republican", 0),
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = raw.cached(SOURCE, URL).path.read_text(errors="replace")
    text = text[text.index('id="bycounty"') : text.index('id="bycountyw"')]
    with connect() as conn:
        counties = {
            name.removesuffix(", DE").removesuffix(" County").lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '10%%' and u.valid @> '2020-01-01'::date"""
            ).fetchall()
        }
    rows = []
    contest = party = None
    for tag, body in re.findall(r"<(h3|h4|table)[^>]*>(.*?)</\1>", text, re.DOTALL):
        if tag == "h3":
            contest = html.unescape(body).strip()
        elif tag == "h4":
            party = html.unescape(body).strip()
        elif (contest, party) in CONTESTS:
            lines = [
                [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                    for cell in re.findall(r"<t[hd].*?</t[hd]>", line, re.DOTALL)
                ]
                for line in re.findall(r"<tr.*?</tr>", body, re.DOTALL)
            ]
            names = lines[0][2:-2]
            for candidate, _, *votes, _, _ in lines[1:]:
                for name, value in zip(names, votes, strict=True):
                    rows.append(
                        (
                            *CONTESTS[(contest, party)],
                            counties[name.lower()],
                            candidate.title(),
                            int(value.replace(",", "")),
                        )
                    )
    frame = pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("chamber", "candidate")
        .agg(pl.col("code").n_unique(), pl.col("votes").sum())
        .sort("chamber", "candidate")
        .rows()
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Delaware's 2020 U.S. Senate primaries and Republican U.S. House primary by county from the Department of Elections' primary report",
            polity="US-DE",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.lit(2020, pl.Int32),
                chamber="chamber",
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("DE"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("DE"),
            ),
            {"DE": source},
            strict=True,
        )
        return cs.write()
