import re
import zipfile
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_pri_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.alabama.gov/sites/default/files/election-data/"
FILES = {
    date(2016, 3, 1): "2017-06/2016-Primary-PrecinctLevel.zip",
    date(2018, 6, 5): "2018-07/2018-Official-Primary-Precinct-Results_1.zip",
}
OFFICE = re.compile(
    r"(?:UNITED STATES REPRESENTATIVE,? (\d+)(?:ST|ND|RD|TH) CONGRESSIONAL DISTRICT|(UNITED STATES SENATOR)|(PRESIDENT OF THE UNITED STATES))\s*",
    re.IGNORECASE,
)
PARTIES = {"DEM": "democratic", "REP": "republican"}
PUBLISHER = "Alabama Secretary of State"
TITLE = "Primary election precinct-level results"


def fetch() -> None:
    for path in FILES.values():
        try:
            raw.cached(SOURCE, BASE + path)
        except LookupError:
            raw.fetch(SOURCE, BASE + path, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    rows = []
    for day, path in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, BASE + path).path) as archive:
            for name in archive.namelist():
                if not name.endswith(".xls"):
                    continue
                county = re.sub(r"^\d{4}-Primary-|\.xls$", "", name)
                workbook = fastexcel.read_excel(archive.read(name))
                sheet = workbook.load_sheet_by_name(workbook.sheet_names[0], header_row=None).to_polars()
                for line in sheet.iter_rows():
                    found = (
                        OFFICE.fullmatch(str(line[0] or ""))
                        if line[1] is not None and str(line[1]).strip() in PARTIES
                        else None
                    )
                    candidate = re.sub(r"\s+", " ", str(line[2] or "")).strip()
                    if found is None or candidate.lower() in ("over votes", "under votes"):
                        continue
                    chamber = "house" if found[1] else "senate" if found[2] else "president"
                    rows.append(
                        (
                            day,
                            f"{chamber}:{PARTIES[str(line[1]).strip()]}",
                            int(found[1]) if found[1] else None,
                            candidate.upper(),
                            counties[re.sub(r"[^a-z]", "", county.lower())],
                            sum(int(float(cell)) for cell in line[3:] if cell not in (None, "")),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "district", "candidate", "code")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("AL"),
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").str.contains("(?i)write"),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
    )
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "chamber", "district")
        .agg(counties=pl.col("code").n_unique(), votes=pl.col("votes").sum())
        .sort("day", "chamber", "district")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2016 and 2018 federal primaries by county",
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
