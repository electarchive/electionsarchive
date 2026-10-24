import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.in_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Indiana Secretary of State"
TITLE = "Election report of Indiana"
IMAGES = {
    date(1996, 11, 5): [2919, 2920, 2921],
    date(1998, 11, 3): [3132, 3133, 3134],
}
EARLY = [
    (header.split(), block)
    for header, *block in (
        chunk.splitlines() for chunk in Path(__file__).with_name("in_usrep_early.txt").read_text().split("@ ")[1:]
    )
]
PAGES = sorted({*(page for pages in IMAGES.values() for page in pages), *(int(header[2]) for header, _ in EARLY)})
URL = "https://indianamemory.contentdm.oclc.org/iiif/2/p16066coll37:{}/full/1500,/0/default.jpg"
METHOD = (
    "County totals are from the Secretary of State's election report as scanned by the Indiana State Library, read "
    "from the page images. Each candidate's county sum equals the district total printed in the report. Where the "
    "report prints a county by township, the townships are added into the county."
)


def fetch() -> None:
    for page in PAGES:
        try:
            raw.cached(SOURCE, URL.format(page))
        except LookupError:
            raw.fetch(SOURCE, URL.format(page), suffix=".jpg")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '18%%'"""
            ).fetchall()
        }
    rows = []
    for line in Path(__file__).with_name("in_usrep.tsv").read_text().splitlines():
        day, district, name, column, votes = line.split("\t")
        rows.append((day, int(district), counties[re.sub(r"[^a-z]", "", name.lower())], column, int(votes)))
    for (day, district, _), block in EARLY:
        total = [int(value) for value in block[-1].split()[1:]]
        table = []
        for line in block[:-1]:
            name, *values = line.rsplit(" ", len(total))
            table.append((name, [None if value == "-" else int(value) for value in values]))
        if [sum(values[column] or 0 for _, values in table) for column in range(len(total))] != total:
            raise ValueError(f"{day} district {district}: rows do not tie the printed totals {total}")
        for name, values in table:
            for column, votes in enumerate(values):
                if votes is not None:
                    rows.append((day, int(district), counties[re.sub(r"[^a-z]", "", name.lower())], str(column), votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Indiana's U.S. House general elections of 1950 to 1998 by county from the Secretary of State's election reports",
        PUBLISHER,
        TITLE,
        "https://indianamemory.contentdm.oclc.org/digital/collection/p16066coll37",
        "IN",
        "18",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL.format(page)) for page in PAGES],
        lambda key: METHOD,
    )
