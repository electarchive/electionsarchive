import re
import subprocess
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.or_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Oregon Secretary of State"
TITLE = "Official abstract of votes, general election"
FILES = {
    date(1992, 11, 3): "https://records.sos.state.or.us/ORSOSWebDrawer/Record/8411064/File/document",
    date(1994, 11, 8): "https://digitalcollections.library.oregon.gov/assets/downloadwiz/343327",
}
PAGE_1998 = (
    "https://web.archive.org/web/20001018053233if_/http://www.sos.state.or.us/elections/nov398/other.info/usrep.htm"
)
METHOD_1998 = (
    "County totals are from the Secretary of State's official results page for the November 3, 1998 general "
    "election (Wayback Machine capture of 2000-10-18). Each candidate's county sum equals the district total printed "
    "on the page; the page's Misc. column is the write-in count."
)
METHOD = (
    "County totals are from the Secretary of State's official abstract of votes for the general election (a scan "
    "held by the Oregon State Library for 1994), read from the page images. Each candidate's county sum equals the "
    "district total printed in the abstract. The 1994 first district uses the automatic recount certification."
)


def fetch() -> None:
    try:
        raw.cached(SOURCE, PAGE_1998)
    except LookupError:
        page = subprocess.run(["curl", "-sL", "-m", "120", PAGE_1998], capture_output=True, check=True).stdout
        raw.store(SOURCE, PAGE_1998, page, suffix=".htm", retrieved_at=datetime.now(UTC), media_type="text/html")
    for url in FILES.values():
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "file.pdf"
            for _ in range(10):
                subprocess.run(
                    ["curl", "-sL", "-m", "300", "-A", "Mozilla/5.0", "-C", "-", "-o", str(target), url], check=False
                )
                if subprocess.run(["pdfinfo", str(target)], capture_output=True, check=False).returncode == 0:
                    break
            else:
                raise RuntimeError(f"{url} did not download whole")
            raw.store(
                SOURCE,
                url,
                target.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%%'"""
            ).fetchall()
        }
    rows = []
    for line in Path(__file__).with_name("or_usrep.tsv").read_text().splitlines():
        day, district, name, column, votes = line.split("\t")
        rows.append((day, int(district), counties[re.sub(r"[^a-z]", "", name.lower())], column, int(votes)))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Oregon's U.S. House general elections of 1992, 1994, and 1998 by county from the Secretary of State's abstracts",
        PUBLISHER,
        TITLE,
        "https://sos.oregon.gov/elections/pages/historical-data.aspx",
        "OR",
        "41",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url) for url in FILES.values()] + [raw.cached(SOURCE, PAGE_1998)],
        lambda key: METHOD_1998 if key.startswith("1998") else METHOD,
    )
