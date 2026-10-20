import csv
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.mo_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/2025id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
FILES = {
    date(2004, 8, 3): [SITE + "CountyPrimary2004.pdf", SITE + "CountyPrimary2004Part2.pdf"],
}
SHEETS = {
    date(2012, 8, 7): "2012/20120807__mo__primary.csv",
    date(2014, 8, 5): "2014/20140805__mo__primary.csv",
}
PARTIES = {
    "DEM": "democratic",
    "REP": "republican",
    "LIB": "libertarian",
    "CST": "constitution",
    "CON": "constitution",
    "REF": "reform",
    "GRE": "green",
    "GRN": "green",
}
PUBLISHER = "Missouri Secretary of State"
TITLE = "County reporting: official primary election returns"
OE_TITLE = "Missouri primary results by county, 2012 and 2014"


def fetch() -> None:
    for urls in FILES.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".pdf")
    for path in SHEETS.values():
        raw.fetch(
            SOURCE,
            openelections.REPOSITORY.format(state="mo", commit=openelections.COMMITS["MO"], path=path),
            suffix=".csv",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%'"""
            ).fetchall()
        }
    codes |= {"stlouis": "29189", "stlouiscity": "29510", "stlouiscounty": "29189", "kansascity": "2938000"}
    rows: list[dict[str, object]] = []
    for day, urls in FILES.items():
        for url in urls:
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            county = None
            office: tuple[str, int | None] | None = None
            lines: list[dict[str, object]] = []
            for line in text.splitlines():
                if heading := re.fullmatch(r" {5}([A-Z][A-Z .']+)", line):
                    county, office = codes[re.sub(r"[^a-z]", "", heading[1].lower())], None
                elif re.fullmatch(r" {5}U\.S\. Senator", line):
                    office, lines = ("senate", None), []
                elif found := re.fullmatch(r" {5}U\.S\. Representative \W+ District (\d+)", line):
                    office, lines = ("house", int(found[1])), []
                elif re.fullmatch(r" {5}\S.*", line):
                    office = None
                elif office is not None and (
                    row := re.fullmatch(r"\s+(\S.*?)\s{2,}([A-Z]{2,3})\s+([\d,]+)(?:\s+[\d.]+%)?(?:\d+/\d+)?\s*", line)
                ):
                    last, _, first = row[1].partition(", ")
                    lines.append(
                        {
                            "day": day,
                            "chamber": f"{office[0]}:{PARTIES[row[2]]}",
                            "district": office[1],
                            "code": county,
                            "candidate": f"{first} {last}".strip().upper(),
                            "writein": False,
                            "votes": int(row[3].replace(",", "")),
                            "source": "MO",
                        }
                    )
                elif office is not None and (total := re.fullmatch(r"\s+Total Votes\s+([\d,]+)", line)):
                    if sum(int(r["votes"]) for r in lines) != int(total[1].replace(",", "")):
                        raise ValueError(f"{day} {county} {office}: rows do not sum to {total[1]}")
                    rows.extend(lines)
                    office = None
    for day, path in SHEETS.items():
        url = openelections.REPOSITORY.format(state="mo", commit=openelections.COMMITS["MO"], path=path)
        with raw.cached(SOURCE, url).path.open(newline="") as handle:
            for entry in csv.DictReader(handle):
                if entry["office"] not in ("U.S. House", "U.S. Senate"):
                    continue
                rows.append(
                    {
                        "day": day,
                        "chamber": f"{'house' if entry['office'] == 'U.S. House' else 'senate'}:{PARTIES[entry['party']]}",
                        "district": int(entry["district"]) if entry["district"] else None,
                        "code": codes[re.sub(r"[^a-z]", "", entry["county"].lower().replace("saint ", "st "))],
                        "candidate": entry["candidate"].strip().upper(),
                        "writein": False,
                        "votes": int(entry["votes"].replace(",", "") or 0),
                        "source": "OE",
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows)
        .group_by("day", "chamber", "district", "code", "candidate", "source")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year(),
            special=pl.lit(False),
            state=pl.lit("MO"),
            district=pl.col("district").cast(pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
        )
        .sort("day", "chamber", "district", "code", "candidate")
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's 2004, 2012, and 2014 House and Senate primaries by county",
            polity="US-MO",
        )
        sources = {"MO": cs.source(PUBLISHER, TITLE), "OE": cs.source("OpenElections", OE_TITLE)}
        cs.add(
            "source",
            id=sources["MO"],
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.mo.gov/elections/results",
            license="Public record",
        )
        cs.add(
            "source",
            id=sources["OE"],
            kind="community",
            publisher="OpenElections",
            title=OE_TITLE,
            url="https://github.com/openelections/openelections-data-mo",
            license="No license stated: facts published with attribution, raw files not redistributed",
        )
        for urls in FILES.values():
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources["MO"])
        for path in SHEETS.values():
            url = openelections.REPOSITORY.format(state="mo", commit=openelections.COMMITS["MO"], path=path)
            cs.artifact(raw.cached(SOURCE, url), sources["OE"])
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20(04|12|14)-08-[0-9]{2}:MO:(house:[0-9]+|senate):[a-z-]+$'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and s.publisher <> 'Federal Election Commission' and s.id <> all(%s))""",
                [list(sources.values())],
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.filter(
                ~pl.concat_str(
                    pl.col("day").cast(pl.String),
                    pl.lit("MO"),
                    pl.when(pl.col("district").is_not_null())
                    .then(pl.concat_str(pl.lit("house:"), pl.col("district").cast(pl.String)))
                    .otherwise(pl.lit("senate")),
                    pl.col("chamber").str.split(":").list.last(),
                    separator=":",
                ).is_in(list(held))
            ),
            sources,
            strict=True,
        )
        return cs.write()
