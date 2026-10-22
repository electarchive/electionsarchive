import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ut_sos
from ea.store import DATA, connect

SOURCE = "us.ut_leg_pdf"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {2000: date(2000, 11, 7), 2004: date(2004, 11, 2), 2006: date(2006, 11, 7)}
NUMBER = re.compile(r"\d[\d,]*")


def cached(year: int) -> raw.Artifact:
    stamp, path = ut_sos.FILES[(year, "general")]
    return raw.cached(ut_sos.SOURCE, ut_sos.WAYBACK.format(stamp, ut_sos.SITE + path))


def fetch() -> None:
    ut_sos.fetch()


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '49%%'"""
            ).fetchall()
        }
        held: dict[tuple[date, int], list[tuple[str, str]]] = {}
        for key, label, votes in conn.execute(
            """select x.value, o.ballot_label, sum(v.value)::bigint from external_id x
               join result_set r on r.contest_id = x.entity_id and r.is_default
               join result_value v on v.result_set_k = r.k and v.measure = 1
               join contest_option o on o.k = v.option_k and o.kind = 'candidate'
               where x.scheme = 'us.contest' and x.value ~ '^20(00|04|06)-11-0[0-9]:UT:(house|senate):\\d+:g$'
               group by 1, 2"""
        ).fetchall():
            day = date.fromisoformat(key[:10])
            held.setdefault((day, int(votes)), []).append((key.split(":", 2)[2], label))
    rows: list[tuple[date, str, str, str, int]] = []
    for year, day in DAYS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(cached(year).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for number, page in enumerate(text.split("\f"), 1):
            lines = page.splitlines()
            total = next((line for line in lines if re.match(r"\s*total\b", line, re.IGNORECASE)), None)
            if total is None or not re.search(r"(?i)senate|house", page):
                continue
            anchors = [match.end() for match in NUMBER.finditer(total)]
            printed = [int(match[0].replace(",", "")) for match in NUMBER.finditer(total)]
            cells: dict[int, list[tuple[str, int]]] = {}
            for line in lines:
                label = re.match(r"\s*([A-Za-z][A-Za-z .]*?)\s{2,}\d", line)
                if label is None or (code := codes.get(re.sub(r"[^a-z]", "", label[1].lower()))) is None:
                    continue
                for match in NUMBER.finditer(line):
                    column = min(range(len(anchors)), key=lambda index: abs(anchors[index] - match.end()))
                    if abs(anchors[column] - match.end()) <= 4:
                        cells.setdefault(column, []).append((code, int(match[0].replace(",", ""))))
            for column in range(2, len(anchors)):
                found = held.get((day, printed[column]), [])
                if sum(votes for _, votes in cells.get(column, [])) != printed[column]:
                    print(f"{year} page {number} column {column}: counties do not sum to {printed[column]}, skipped")
                elif len(found) != 1:
                    print(f"{year} page {number} column {column}: {len(found)} held options total {printed[column]}")
                else:
                    key, label = found[0]
                    rows.extend((day, key, code, label, votes) for code, votes in cells[column])
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row").unique().select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("UT"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind=pl.lit("county"),
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes="votes",
        source=pl.lit("UT"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        filled = [
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status <> 'withdrawn'
                   where x.scheme = 'us.contest' and x.value ~ '^20(00|04|06)-11-0[0-9]:UT:(house|senate):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k and u.scheme_code in ('us.precinct', 'us.county', 'us.county_part'))"""
            ).fetchall()
        ]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's 2000, 2004, and 2006 State House and Senate generals by county from the Office of "
            "Elections' official canvasses, columns identified by their printed totals",
            polity="US-UT",
        )
        source = cs.source(ut_sos.PUBLISHER, ut_sos.TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {ut_sos.TITLE}")
        for year in DAYS:
            cs.artifact(cached(year), source)
        frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit("UT"), "chamber", separator=":").is_in(filled)
        )
        medsl.attach(cs, conn, frame, {"UT": source}, strict=True)
        return cs.write()
