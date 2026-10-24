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

SOURCE = "us.sd_sos_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "South Dakota Secretary of State"
TITLE = "General Election Returns for Statewide Offices, 1990-1998"
BASE = "https://sdsos.gov/elections-voting/election-resources/election-history/"
PAGES = {
    1990: BASE + "1990/1990-general-election-returns-statewide-offices.aspx",
    1994: BASE + "1994/1994_general_election_returns_statewide_offices.aspx",
    1998: BASE + "1998/1998_general_election_official_canvass_statewide_candidates.aspx",
}
DAYS = {1990: date(1990, 11, 6), 1994: date(1994, 11, 8), 1998: date(1998, 11, 3)}
OFFICES = {
    "governor/lt. governor": "Governor",
    "secretary of state": "Secretary of State",
    "attorney general": "Attorney General",
    "state auditor": "State Auditor",
    "state treasurer": "State Treasurer",
    "commissioner of school and public lands": "Commissioner of School and Public Lands",
    "public utilities commissioner": "Public Utilities Commissioner",
}
PARTIES = {"D": "DEMOCRATIC", "R": "REPUBLICAN", "L": "LIBERTARIAN", "I": "INDEPENDENT"}


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")


def cells(row: str) -> list[str]:
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split()) for cell in re.findall(r"<t[dh].*?</t[dh]>", row, flags=re.S)]


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '46%%'"""
            ).fetchall()
        }
    counties["shannon"] = "46102"
    rows = []
    for year, url in PAGES.items():
        page = raw.cached(SOURCE, url).path.read_text(errors="ignore")
        tables = re.findall(r"<table.*?</table>", page, flags=re.S)
        parts = re.split(r"<table.*?</table>", page, flags=re.S)
        sequence = []
        for index, table in enumerate(tables):
            body = re.findall(r"<tr.*?</tr>", table, flags=re.S)
            heading = " ".join(
                html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style).*?</\1>", "", parts[index], flags=re.S))).split()
            )
            head = cells(body[0])
            label = head[0].lower() if head[0].lower() in OFFICES else heading.lower().rsplit("top of page ", 1)[-1]
            sequence.append((label, head, body))
        order = [label for label, _, _ in sequence]
        offices = [
            ("State Auditor" if label == "attorney general" and order[:i].count(label) == 1 else OFFICES.get(label), head, body)
            for i, (label, head, body) in enumerate(sequence)
        ]
        for title, head, body in offices:
            if title is None:
                continue
            names = [name for name in head[1:] if name]
            printed: list[int] | None = None
            summed = [0] * len(names)
            for row in body[1:]:
                values = cells(row)
                text = values[0]
                if text.upper().startswith("TOTAL"):
                    printed = [int(v.replace(",", "")) for v in values[-len(names) :]]
                elif text and not text.upper().startswith("PERCENT"):
                    key = re.sub(r"[^a-z]", "", text.lower().replace(" county", ""))
                    if key not in counties:
                        raise ValueError(f"{year}: no county {text}")
                    for position, value in enumerate(values[-len(names) :]):
                        number = int(value.replace(",", "") or 0)
                        summed[position] += number
                        match = re.match(r"^(.*?)\s*-\s*([DRLI])$", names[position])
                        name = (match[1] if match else names[position]).split("/")[0].strip()
                        rows.append((year, title, counties[key], name, PARTIES[match[2]] if match else "", number))
            if printed != summed:
                raise ValueError(f"{year} {title}: counties sum to {summed}, printed {printed}")
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "title": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        day=pl.col("year").replace_strict(DAYS),
        state=pl.lit("SD"),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        candidate=pl.col("candidate").str.to_uppercase(),
        party=pl.col("party").replace("", None),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(66),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "title", "candidate", "party").agg(pl.col("votes").sum()).sort("year", "title").rows())


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
                url=BASE + "election-history-search.aspx",
                license="Public record",
            )
        for url in PAGES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        return {"SD": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="South Dakota's 1990, 1994, and 1998 governor and statewide executive general elections by county from "
        "the Secretary of State's returns pages",
        cite=cite,
        method=(
            "County totals come from the Secretary of State's general election returns page for the year, and every "
            "candidate's 66 county values add up to the TOTALS row printed under the column. The 1990 page labels two "
            "tables Attorney General; the second, whose candidates ran against each other for the auditor's office in "
            "the 1994 and 1998 pages' order, is stored as State Auditor."
        ),
    )
