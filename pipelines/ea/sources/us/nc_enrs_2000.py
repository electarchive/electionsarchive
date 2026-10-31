import html
import re
import unicodedata
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, nc_statewide
from ea.store import DATA, connect

SOURCE = "us.nc_enrs_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = "https://web.archive.org/web/2008id_/http://www.app.sboe.state.nc.us/NCSBE/Elec/Results/y2000elect/enrs_contest_by_county/{}.htm"
PAGES = ["lgov", "attgen", "audit", "agr", "insu", "labor", "secsta", "spuni", "tres"]
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Official results by county for the general election of 2000"
METHOD = (
    "County totals come from the State Board of Elections' official results by county for the 2000 general election, "
    "archived from its election results site, with each column checked against the printed contest total."
)


def fetch() -> None:
    for page in PAGES:
        raw.fetch(SOURCE, FILE.format(page), suffix=".htm")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    rows = []
    for page in PAGES:
        text = raw.cached(SOURCE, FILE.format(page)).path.read_text(errors="replace")
        table = []
        for row in re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.DOTALL | re.IGNORECASE):
            cells = [
                html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<TD[^>]*>(.*?)</TD>", row, re.DOTALL | re.IGNORECASE)
            ]
            if cells := [re.sub(r"\s+", " ", cell) for cell in cells if cell]:
                table.append(cells)
        office = table[2][0].upper()
        parties = table[3]
        names = table[4]
        if len(parties) != len(names):
            raise ValueError(f"{page}: {len(parties)} parties for {len(names)} candidates")
        totals = next(cells for cells in table if cells[0] == "Contest Total:")
        printed = [int(cell.replace(",", "")) for cell in totals[1:] if not cell.endswith("%")]
        seen = []
        for cells in table[5:]:
            key = re.sub(r"[^a-z]", "", cells[0].lower()).replace("tyrell", "tyrrell")
            if key not in counties:
                continue
            votes = [int(cell.replace(",", "")) for cell in cells[1:] if not cell.endswith("%")]
            if len(votes) != len(names):
                raise ValueError(f"{page} {cells[0]}: {len(votes)} votes for {len(names)} candidates")
            seen.append(key)
            for name, party, count in zip(names, parties, votes, strict=True):
                rows.append((office, name, party, counties[key], count))
        if len(set(seen)) != 100:
            raise ValueError(f"{page}: {len(set(seen))} counties")
        for index, name in enumerate(names):
            summed = sum(row[4] for row in rows if row[0] == office and row[1] == name)
            if summed != printed[index]:
                raise ValueError(f"{page} {name}: counties sum to {summed}, printed {printed[index]}")
    frame = pl.DataFrame(rows, schema=["office", "candidate", "party", "county", "votes"], orient="row").select(
        year=pl.lit(2000, pl.Int32),
        day=pl.lit("2000-11-07"),
        state=pl.lit("NC"),
        title=pl.col("office").replace_strict(nc_statewide.OFFICES),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase().replace(nc_statewide.ALIASES),
        party=pl.col("party").str.to_uppercase().replace_strict(nc_statewide.PARTIES),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(100),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes").rows())


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
                url="https://www.ncsbe.gov/results-data",
                license="Public record",
            )
        for page in PAGES:
            cs.artifact(raw.cached(SOURCE, FILE.format(page)), source)
        return {"NC": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="North Carolina's 2000 Council of State elections by county from the State Board of Elections' "
        "official results by county",
        cite=cite,
        method=METHOD,
        declared={
            "2000-11-07:NC:commissioner-of-labor": (
                "Cherie Berry",
                (
                    "The race was within 0.5% in these totals. The State Board of Elections certified Cherie Berry "
                    "the winner."
                ),
            )
        },
    )
