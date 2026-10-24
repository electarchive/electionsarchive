import difflib
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county, wv_bluebook
from ea.store import DATA, connect

SOURCE = "us.wv_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "West Virginia Legislature"
TITLE = "West Virginia Blue Book, General Election Returns"
LANDING = "https://www.wvlegislature.gov/legisdocs/publications/bluebook/"
OFFICES = [
    (r"PRESIDENT", "president"),
    (r"GOVERNOR", "governor"),
    (r"U\.?\s?S\.?\s?SENAT|UNITED STATES SENAT", "senate"),
]
METHOD = (
    "County totals are from the West Virginia Blue Book's general election returns, read from the scan's text layer. "
    "Each candidate's county sum equals the statewide total printed in the book."
)


def election_day(year: int) -> str:
    november = date(year, 11, 1)
    return str(november + timedelta(days=(7 - november.weekday()) % 7 + 1))


def cache(url: str) -> raw.Artifact | None:
    for source in (wv_bluebook.SOURCE, "us.wv_usrep"):
        try:
            return raw.cached(source, url)
        except LookupError:
            continue
    return None


def fetch() -> None:
    wv_bluebook.fetch()


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
    rows = []
    for edition in range(1917, 2001, 2):
        cached = cache(wv_bluebook.URL.format(edition))
        if cached is None:
            continue
        day = election_day(edition - 1)
        with connect() as conn:
            held = {
                key.rsplit(":", 1)[-1]: [int(total) for (total,) in conn.execute(
                    """select sum(v.value) from result_value v join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k join contest c on c.id = o.contest_id
                       where c.id = %s and v.measure = 1 and o.kind <> 'others' group by o.id""",
                    [entity],
                ).fetchall()]
                for key, entity in conn.execute(
                    "select value, entity_id from external_id where scheme = 'us.contest' and value ~ %s",
                    [f"^{day}:WV:(president|governor|senate)$"],
                ).fetchall()
            }
        lines = subprocess.run(
            ["pdftotext", "-layout", str(cached.path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        current: dict[str, list[int]] = {}
        last = -99
        for index, line in enumerate(lines):
            match = re.match(r"^\W*([A-Za-z][A-Za-z. ]{1,30}[A-Za-z])(\W.*)$", line)
            if match and (not re.search(r"\d", match[2]) or re.search(r"[A-Za-z]{3}", match[2])):
                match = None
            if match is None:
                continue
            key = re.sub(r"[^a-z]", "", match[1].lower())
            figures = [int(re.sub(r"[.,]", "", token)) for token in re.findall(r"\d[\d.,]*", match[2])]
            close = [key] if key in counties else difflib.get_close_matches(key, counties, 1, 0.8)
            if close:
                if index - last > 6 or close[0] in current:
                    current = {}
                current[close[0]] = figures
                last = index
            elif key.startswith("total") and current and index - last <= 3:
                sums = [sum(row[c] for row in current.values() if len(row) > c) for c in range(len(figures))]
                if len(current) >= 50:
                    ok = sums == figures and all(len(row) == len(figures) for row in current.values())
                    assigned = {}
                    for column, total in enumerate(figures):
                        near = [
                            (abs(value - total), office)
                            for office, values in held.items()
                            for value in values
                            if abs(value - total) * 300 <= total
                        ]
                        if len({office for _, office in near}) == 1:
                            assigned[column] = near[0][1]
                    if assigned:
                        print(edition, len(current), len(figures), "ok" if ok else f"sums {sums} printed {figures}", assigned)
                    if ok:
                        for county, values in current.items():
                            for column, office in assigned.items():
                                rows.append((day, office, counties[county], f"{index}-{column}", values[column]))
                current = {}
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district").agg(pl.col("county").n_unique()).sort("day", "district"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    years = {int(day[:4]) for day in frame["day"]}
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "West Virginia's president, governor, and U.S. Senate general elections by county from the Blue Book",
        PUBLISHER,
        TITLE,
        LANDING,
        "WV",
        "54",
        frame,
        [
            artifact
            for edition in range(1917, 2001, 2)
            if edition - 1 in years and (artifact := cache(wv_bluebook.URL.format(edition))) is not None
        ],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
