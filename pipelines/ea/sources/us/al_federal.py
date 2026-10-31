import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.alabama.gov/sites/default/files/election-data/"
FILES = {
    date(2020, 3, 3): "2020-06/2020%20Primary%20Precinct%20Results.zip",
    date(2020, 7, 14): "2020-08/2020%20Primary%20Runoff%20Precinct%20Results.zip",
    date(2022, 5, 24): "2022-06/2022%20Primary%20Precinct%20Results.zip",
    date(2022, 6, 21): "2022-07/2022%20Primary%20Runoff%20Precinct%20Results.zip",
    date(2024, 3, 5): "2024-04/2024%20Primary%20Precinct%20Results.ZIP",
    date(2024, 4, 16): "2024-07/2024%20Primary%20Runoff%20Precinct%20Results.zip",
}
RACE = re.compile(
    r"PRESIDENT OF THE UNITED STATES|UNITED STATES SENATOR|UNITED STATES REPRESENTATIVE, (\d+)(?:ST|ND|RD|TH) CONGRESSIONAL DISTRICT"
)
PUBLISHER = "Alabama Secretary of State"
TITLE = "Election data: primary precinct results"


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    tally: dict[tuple, int] = {}
    for day, path in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, BASE + path).path) as archive:
            for member in archive.namelist():
                if not member.lower().endswith((".xls", ".xlsx")):
                    continue
                code = counties[
                    re.sub(
                        r"[^a-z]",
                        "",
                        re.sub(r".*Primary-(?:Runoff-)?|\.xlsx?$", "", member, flags=re.IGNORECASE).lower(),
                    )
                ]
                sheet = fastexcel.read_excel(archive.read(member)).load_sheet(0, header_row=None).to_polars()
                for title, party, candidate, *cells in sheet.iter_rows():
                    found = RACE.fullmatch(re.sub(r"\s+", " ", title or "").strip())
                    candidate = re.sub(r"\s+", " ", candidate or "").strip()
                    if found is None or candidate in ("Over Votes", "Under Votes") or party not in ("DEM", "REP"):
                        continue
                    office = (
                        "president" if title.startswith("PRESIDENT") else "senate" if "SENATOR" in title else "house"
                    )
                    key = (
                        day,
                        f"{office}:{'democratic' if party == 'DEM' else 'republican'}",
                        int(found[1]) if found[1] else None,
                        code,
                        candidate.upper(),
                    )
                    tally[key] = tally.get(key, 0) + sum(
                        int(float(cell)) for cell in cells if cell is not None and str(cell).strip()
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            [(*key, votes) for key, votes in tally.items()],
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("AL"),
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by(
            "year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "kind", "source"
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2020, 2022, and 2024 president, U.S. Senate, and House primaries and runoffs by county",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True)
        return cs.write()
