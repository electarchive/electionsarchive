import html
import re
import subprocess
import time
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ga_sos_pages"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "https://web.archive.org/web/2008id_/http://sos.georgia.gov/elections/election_results/{folder}/{number:07}.htm"
ELECTIONS = {
    date(1996, 11, 5): ("1996_1105", range(6, 253)),
    date(1998, 11, 3): ("1998_1103", range(12, 259)),
}
PUBLISHER = "Georgia Secretary of State"
TITLE = "Georgia Election Results: official results of the general election"


def fetch() -> None:
    for day, (folder, pages) in ELECTIONS.items():
        for page in pages:
            url = PAGE.format(folder=folder, number=page * 100)
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                done = subprocess.run(
                    ["curl", "-sfL", "-m", "120", "--resolve", "web.archive.org:443:207.241.237.2", url],
                    capture_output=True,
                )
                if done.returncode != 0:
                    print(f"{url}: curl exited {done.returncode}, stopping; run the fetch again later")
                    return
                content = done.stdout
                raw.store(
                    SOURCE,
                    url,
                    content,
                    suffix=".htm",
                    retrieved_at=datetime.now(UTC),
                    media_type="text/html",
                )
                time.sleep(1.5)


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%'"""
            ).fetchall()
        }
    rows = []
    for day, (folder, pages) in ELECTIONS.items():
        for page in pages:
            text = raw.cached(SOURCE, PAGE.format(folder=folder, number=page * 100)).path.read_text(errors="replace")
            heading = re.search(
                r"<strong><font face=\"Arial\">(UNITED STATES REPRESENTATIVE - (\d+)\w* DISTRICT|STATE (SENATOR|REPRESENTATIVE) - DISTRICT (\d+))</font>",
                text,
            )
            if heading is None:
                print(f"{day} page {page}: not a legislative page, skipped")
                continue
            district = int(heading[2] or heading[4])
            chamber = (
                "house"
                if heading[2]
                else f"{'senate' if heading[3] == 'SENATOR' else 'house'}:{district}:g"
            )
            body = html.unescape(re.sub(r"<[^>]+>", "", re.search(r"<pre>(.*?)</pre>", text, re.S)[1])).splitlines()
            start = next(i for i, line in enumerate(body) if line.startswith("County"))
            names = re.split(r"\s{2,}", body[start - 4].strip())
            printed = [int(cell.replace(",", "")) for cell in body[start - 2].split()]
            if len(names) != len(printed):
                raise ValueError(f"{day} {heading[1]}: {len(names)} names for {len(printed)} totals")
            summed = [0] * len(names)
            for line in body[start + 1 :]:
                found = re.fullmatch(r"(\D+?)\s+\d+\s+\d+\s+([\d,\s]+)", line.strip())
                if found is None:
                    continue
                votes = [int(cell.replace(",", "")) for cell in found[2].split()]
                if len(votes) != len(names):
                    raise ValueError(f"{day} {district} {found[1]}: {len(votes)} votes for {len(names)} names")
                code = counties[re.sub(r"[^a-z]", "", found[1].lower())]
                for index, (name, count) in enumerate(zip(names, votes, strict=True)):
                    summed[index] += count
                    rows.append((day.year, day, chamber, district if heading[2] else None, code, name.upper(), count))
            if summed != printed:
                raise ValueError(f"{day} district {district}: counties sum to {summed}, printed {printed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["year", "day", "chamber", "district", "code", "candidate", "votes"], orient="row").select(
        year=pl.col("year").cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("GA"),
        district=pl.col("district").cast(pl.Int16),
        code="code",
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("WRITE"),
        votes="votes",
        kind=pl.lit("county"),
        source=pl.lit("GA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 1996 and 1998 U.S. House, state Senate, and state House general elections by county from the Secretary of State's official results pages",
            polity="US-GA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.ga.gov/page/historical-elections-results",
            license="Public record",
        )
        for day, (folder, pages) in ELECTIONS.items():
            for page in pages:
                cs.artifact(raw.cached(SOURCE, PAGE.format(folder=folder, number=page * 100)), source)
        medsl.attach(cs, conn, frame, {"GA": source})
        return cs.write()
