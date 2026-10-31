import difflib
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.ms_lublin"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Mississippi Department of Archives and History"
TITLE = "Precinct data from Mississippi general elections (David Lublin), U.S. House"
URL = "https://da.mdah.ms.gov/series-files/lublin/pdf/MS{}.pdf"
YEARS = {1992: (date(1992, 11, 3), 8, 3), 1994: (date(1994, 11, 8), 6, 2), 1996: (date(1996, 11, 5), 8, 5)}
METHOD = (
    "County totals are the sums of David Lublin's precinct data (American University, with the Mississippi Secretary of "
    "State's Elections Division, funded by the National Science Foundation), prepared from the official returns and "
    "held by the Mississippi Department of Archives and History, which gives no guarantee of accuracy. The file carries "
    "the Democratic, Republican, and independent House columns only."
)
ALIASES = {"jeffdavis": "jeffersondavis"}


def fetch() -> None:
    for year in YEARS:
        try:
            raw.cached(SOURCE, URL.format(year))
        except LookupError:
            body = subprocess.run(
                ["curl", "-s", "-m", "300", "-A", "Mozilla/5.0", URL.format(year)], capture_output=True, check=True
            ).stdout
            raw.store(
                SOURCE,
                URL.format(year),
                body,
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
                   where u.scheme_code = 'us.county' and u.code like '28%%'"""
            ).fetchall()
        }
    rows = []
    for year, (day, width, first) in YEARS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, URL.format(year)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        token = r"(?:\d+\.?|\.)"
        pattern = re.compile(
            r"^\s*(?:\d+\s+)?([A-Z][A-Z .'-]*?)\s{2,}\S.*?\s+((?:%s\s+){%d}%s)\s*$" % (token, width - 1, token)
        )
        lines = []
        for line in text.splitlines():
            match = pattern.match(line)
            if match and match[1] != "COUNTY" and match[2].split()[-1] != ".":
                lines.append(
                    (match[1], [None if value == "." else int(value.rstrip(".")) for value in match[2].split()])
                )
        sums: dict[tuple[str, int], list[int]] = {}
        printed: set[tuple[int, int]] = set()
        for name, values in lines:
            key = re.sub(r"[^a-z]", "", name.lower())
            key = ALIASES.get(key, key)
            code = counties[key if key in counties else difflib.get_close_matches(key, counties, 1, 0.75)[0]]
            house = values[first:-1]
            total = sums.setdefault((code, values[-1]), [0] * len(house))
            for index, value in enumerate(house):
                total[index] += value or 0
                if value is not None:
                    printed.add((values[-1], index))
        print(year, len(lines), len({code for code, _ in sums}), sorted({district for _, district in sums}))
        for (code, district), house in sums.items():
            for column, votes in enumerate(house):
                if (district, column) in printed:
                    rows.append((str(day), district, code, str(column), votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Mississippi's U.S. House general elections of 1992, 1994, and 1996 by county from David Lublin's precinct data",
        PUBLISHER,
        TITLE,
        "https://da.mdah.ms.gov/series/lublin",
        "MS",
        "28",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL.format(year)) for year in YEARS],
        lambda key: METHOD,
    )
