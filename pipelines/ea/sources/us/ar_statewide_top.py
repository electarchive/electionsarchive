import io
import re
import zipfile
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.ar_statewide import FILES, METHOD, PUBLISHER, SOURCE as CACHE, TITLE
from ea.store import DATA, connect

SOURCE = "us.ar_statewide_top"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = {"Governor": "governor", "U.S. Senate": "senate"}
PARTIES = ("Democrat", "Republican", "Independent", "Reform", "Libertarian", "Green", "Write-In")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%%'"""
            ).fetchall()
        }
    rows = []
    for day, url in FILES.items():
        data = raw.cached(CACHE, url).path.read_bytes()
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                data = archive.read(next(name for name in archive.namelist() if name.lower().endswith(".xls")))
        table = fastexcel.read_excel(data).load_sheet("Summary", header_row=None).to_polars().rows()
        header = [str(cell) for cell in table[0]]
        columns = [index for index, name in enumerate(header) if re.sub(r"[^a-z]", "", name.lower()) in codes]
        total = header.index("Grand Totals")
        office = None
        for row in table[1:]:
            filled = [cell for cell in row if cell is not None and str(cell).strip()]
            if len(filled) == 1:
                office = OFFICES.get(str(filled[0]).strip())
                continue
            if office is None or row[1] is None or str(row[1]).strip() not in PARTIES:
                continue
            votes = [int(float(row[column])) for column in columns]
            if sum(votes) != int(float(row[total])):
                raise ValueError(f"{day} {office} {row[0]}: counties sum to {sum(votes)}, printed {row[total]}")
            name = str(row[0]).strip()
            for column, count in zip(columns, votes, strict=True):
                rows.append((day, office, codes[re.sub(r"[^a-z]", "", header[column].lower())], name, count))
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district", "column").agg(pl.col("votes").sum()).sort("day", "district"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Arkansas's 1994 governor and 1998 governor and U.S. Senate general elections by county from the Secretary of State's summaries",
        PUBLISHER,
        TITLE,
        "https://sos.arkansas.gov/elections/research/election-results",
        "AR",
        "05",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(CACHE, url) for url in FILES.values()],
        lambda key: METHOD,
        statewide=True,
    )
