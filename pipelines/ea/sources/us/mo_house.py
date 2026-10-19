import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mo_house"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/2025id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
FILES = {
    date(1996, 11, 5): SITE + "CountyGeneral1996.pdf",
    date(1998, 11, 3): SITE + "CountyGeneral1998.pdf",
}
PUBLISHER = "Missouri Secretary of State"
TITLE = "County reporting: official election returns"


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%'"""
            ).fetchall()
        }
    codes |= {"stlouiscity": "29510", "stlouiscounty": "29189", "kansascity": codes["jackson"]}
    rows: list[dict[str, object]] = []
    for day, url in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        county = None
        district = None
        lines: list[dict[str, object]] = []
        for line in text.splitlines():
            if heading := re.fullmatch(r" {5}([A-Z][A-Z .']+)", line):
                county, district = codes[re.sub(r"[^a-z]", "", heading[1].lower())], None
            elif office := re.fullmatch(r" {5}U\.S\. Representative \W+ District (\d+)", line):
                district, lines = int(office[1]), []
            elif district is not None and (
                row := re.fullmatch(r"\s+(\S.*?)\s{2,}([A-Z]{2,3})\s+([\d,]+)(?:\s+[\d.]+%)?", line)
            ):
                last, _, first = row[1].partition(", ")
                lines.append(
                    {
                        "day": day,
                        "district": district,
                        "code": county,
                        "candidate": f"{first} {last}".strip().upper(),
                        "writein": row[2] == "WI",
                        "votes": int(row[3].replace(",", "")),
                    }
                )
            elif district is not None and (total := re.fullmatch(r"\s+Total Votes\s+([\d,]+)", line)):
                if sum(int(r["votes"]) for r in lines) != int(total[1].replace(",", "")):
                    raise ValueError(f"{day} {county} district {district}: rows do not sum to {total[1]}")
                rows.extend(lines)
                district = None
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows)
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by("day", "district", "code", "candidate")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year(),
            chamber=pl.lit("house"),
            special=pl.lit(False),
            state=pl.lit("MO"),
            district=pl.col("district").cast(pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("MO"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's 1996 and 1998 House general elections by county",
            polity="US-MO",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.mo.gov/elections/results",
            license="Public record",
        )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, frame, {"MO": source}, strict=True)
        return cs.write()
