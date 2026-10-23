import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nj_gen_senate"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Official list of candidates for U.S. Senate, with county tallies"
BASE = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
FILES = {
    date(2000, 11, 7): "2000/2000-general-elect-senate-candidate-tallies.pdf",
    date(2002, 11, 5): "2002/2002gen-elect_senate-candidate_tally.pdf",
    date(2006, 11, 7): "2006/2006_official-senate_tallies.pdf",
    date(2008, 11, 4): "2008/2008-official-gen-elect-tallies-senate-120208.pdf",
}
ROW = re.compile(r"(?:^(\S.*?)\s{2,}.*?)?\b(Atlantic|Bergen|Burlington|Camden|Cape May|Cumberland|Essex|Gloucester|Hudson|Hunterdon|Mercer|Middlesex|Monmouth|Morris|Ocean|Passaic|Salem|Somerset|Sussex|Union|Warren)\b.*?\s([\d,]+)\s*$")


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            text.lower(): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '34%%'"""
            ).fetchall()
        }
    rows = []
    for day, path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        candidate = None
        found: list[tuple[str, int]] = []
        for line in text.splitlines():
            if match := ROW.search(line):
                if match[1] and not found:
                    candidate = re.sub(r"\s*(\(w\)|\*)", "", match[1]).strip()
                found.append((fips[match[2].lower()], int(match[3].replace(",", ""))))
            elif total := re.match(r"\s*Total\s+([\d,]+)\s*$", line):
                if sum(votes for _, votes in found) != int(total[1].replace(",", "")) or len(found) != 21:
                    raise ValueError(f"{day} {candidate}: {len(found)} counties sum to {sum(v for _, v in found)}, printed {total[1]}")
                rows.extend((day, code, candidate, votes) for code, votes in found)
                found = []
    frame = pl.DataFrame(rows, schema=["day", "code", "candidate", "votes"], orient="row")
    if not frame["code"].str.starts_with("34").all():
        raise ValueError("unmapped county")
    with pl.Config(tbl_rows=80):
        print(frame.group_by("day", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("day", "votes"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber=pl.lit("senate"),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("NJ"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("NJ"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's U.S. Senate generals of 2000, 2002, 2006, and 2008 by county from the Division of "
            "Elections' official candidate tallies",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.nj.gov/state/elections/election-information-results.shtml",
                license="Public record",
            )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(
            cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NJ": source}, strict=True, extend=True
        )
        return cs.write()
