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
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Abstract of Votes Cast in the General Election held on November 5, 1996"
BASE = "https://web.archive.org/web/20081126222630/www.app.sboe.state.nc.us/NCSBE/Elec/Results/PastElect/results9/"
PAGES = {
    "governor": "Governor",
    "ltgov": "Lieutenant Governor",
    "secstate": "Secretary of State",
    "auditor": "Auditor",
    "treasure": "Treasurer",
    "suptinst": "Superintendent of Public Instruction",
    "attygen": "Attorney General",
    "agricul": "Commissioner of Agriculture",
    "insuran": "Commissioner of Insurance",
    "labor": "Commissioner of Labor",
}
PARTIES = {
    "D": "DEMOCRATIC",
    "R": "REPUBLICAN",
    "L": "LIBERTARIAN",
    "NL": "NATURAL LAW",
    "WI": None,
    "I": "INDEPENDENT",
}


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
        table = re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL)
        grid = [
            [
                " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())
                for cell in re.findall(r"<td.*?</td>", row, flags=re.DOTALL)
            ]
            for row in table
        ]
        header = next(row for row in grid if row[:1] == ["County"])
        slots = [i for i, cell in enumerate(header) if i > 0 and cell]
        names = [header[i] for i in slots]
        parties_raw = [
            row[i].strip("()")
            for row in grid
            if row and row[0] == "" and any(re.fullmatch(r"\([A-Z]+\)", cell) for cell in row)
            for i in slots
        ]
        parties = [PARTIES[p] for p in parties_raw]
        writeins = [p == "WI" for p in parties_raw]
        summed = [0] * len(names)
        printed: list[int] = []
        for row in grid:
            cells = [row[i].replace(",", "") if i < len(row) else "" for i in slots]
            if not all(re.fullmatch(r"\d*", v) for v in cells) or not any(cells):
                continue
            values = [int(v or 0) for v in cells]
            label = row[0] if row else ""
            if label in ("Grand Total", "TOTAL", ""):
                printed = values
            else:
                key = re.sub(r"[^a-z]", "", label.lower())
                if key not in counties:
                    raise ValueError(f"no county {label}")
                for i, value in enumerate(values):
                    summed[i] += value
                    rows.append((title, counties[key], names[i].upper(), parties[i], writeins[i], value))
        if printed != summed:
            raise ValueError(f"{title}: counties sum to {summed}, printed {printed}")
    frame = pl.DataFrame(
        rows,
        schema={
            "title": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
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
        summary="North Carolina's 1996 Council of State contests by county from the State Board of Elections' abstract",
        cite=cite,
        method=(
            "County totals come from the State Board of Elections' abstract of votes cast (a Wayback Machine copy of "
            "the board's former site), and every candidate's 100 county values add up to the Grand Total row printed "
            "under the column."
        ),
        supersede=True,
    )
