import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.nc_abstract_1996"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Abstract of Votes Cast in the General Election held on November 5, 1996"
BASE = "https://web.archive.org/web/20081126222630/www.app.sboe.state.nc.us/NCSBE/Elec/Results/PastElect/results9/"
PAGES = {"attygen": "Attorney General", "secstate": "Secretary of State"}
PARTIES = {"D": "DEMOCRATIC", "R": "REPUBLICAN", "L": "LIBERTARIAN", "NL": "NATURAL LAW"}


def fetch() -> None:
    for page in PAGES:
        raw.fetch(SOURCE, f"{BASE}{page}.htm", suffix=".htm")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%%'"""
            ).fetchall()
        }
    rows = []
    for page, title in PAGES.items():
        text = raw.cached(SOURCE, f"{BASE}{page}.htm").path.read_text(errors="ignore")
        table = re.findall(r"<tr.*?</tr>", text, flags=re.S)
        grid = [
            [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split()) for cell in re.findall(r"<td.*?</td>", row, flags=re.S)]
            for row in table
        ]
        grid = [[cell for cell in row if cell] for row in grid]
        names = next(row for row in grid if row[:1] == ["County"])[1:]
        parties = next(row for row in grid if row and re.fullmatch(r"\([A-Z]+\)", row[0]))
        parties = [PARTIES[p.strip("()")] for p in parties]
        summed = [0] * len(names)
        printed: list[int] = []
        for row in grid:
            if len(row) == len(names) and all(re.fullmatch(r"[\d,]+", v) for v in row):
                printed = [int(v.replace(",", "")) for v in row]
                continue
            if len(row) != len(names) + 1:
                continue
            values = [int(v.replace(",", "")) for v in row[1:] if re.fullmatch(r"[\d,]+", v)]
            if len(values) != len(names):
                continue
            if row[0] == "Grand Total":
                printed = values
            else:
                key = re.sub(r"[^a-z]", "", row[0].lower())
                if key not in counties:
                    raise ValueError(f"no county {row[0]}")
                for i, value in enumerate(values):
                    summed[i] += value
                    rows.append((title, counties[key], names[i].upper(), parties[i], value))
        if printed != summed:
            raise ValueError(f"{title}: counties sum to {summed}, printed {printed}")
    frame = pl.DataFrame(
        rows,
        schema={"title": pl.String, "county": pl.String, "candidate": pl.String, "party": pl.String, "votes": pl.Int64},
        orient="row",
    ).with_columns(
        year=pl.lit(1996, pl.Int32),
        day=pl.lit(date(1996, 11, 5)),
        state=pl.lit("NC"),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(100),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title").rows())


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
                url=BASE.replace("results9/", ""),
                license="Public record",
            )
        for page in PAGES:
            cs.artifact(raw.cached(SOURCE, f"{BASE}{page}.htm"), source)
        return {"NC": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="North Carolina's 1996 attorney general and secretary of state by county from the State Board of Elections' abstract",
        cite=cite,
        method=(
            "County totals come from the State Board of Elections' abstract of votes cast (a Wayback Machine copy of "
            "the board's former site), and every candidate's 100 county values add up to the Grand Total row printed "
            "under the column."
        ),
    )
