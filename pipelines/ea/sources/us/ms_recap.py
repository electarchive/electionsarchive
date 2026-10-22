import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ms_recap"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.ms.gov/elections/electionResults/"
FILES = {
    "2020%20State%20Democratic%20Party%20Certified%20Primary%20Election%20Results.pdf": (
        date(2020, 3, 10),
        "democratic",
        {"US House Of Rep 03-3rd Congressional": 3},
    ),
}
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Official recapitulation of the party primaries"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.removesuffix(", MS").removesuffix(" County").lower()): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '28%%' and u.valid @> '2020-01-01'::date"""
            ).fetchall()
        }
    codes["jeffdavis"] = codes["jeffersondavis"]
    rows = []
    for name, (day, party, contests) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        totals: dict[str, int] = {}
        for page in text.split("\f"):
            lines = page.splitlines()
            start = next((index for index, line in enumerate(lines) if re.match(r"\s*day of ", line)), None)
            if start is None:
                continue
            end = next(
                index
                for index, line in enumerate(lines)
                if index > start and re.match(r"\s+US |\s+\w.*Congressional", line)
            )
            phrases = sorted(
                ((found.start() + found.end()) / 2, found[0])
                for line in lines[start + 1 : end]
                for found in re.finditer(r"\S+(?: \S+)*", line)
            )
            counties = [phrase for _, phrase in phrases]
            contest = None
            for line in lines[end:]:
                title = line.strip()
                if title in contests:
                    contest = contests[title]
                    continue
                if title.startswith("US ") or "Congressional" in title:
                    contest = None
                    continue
                found = re.match(r"(\S.*?)\s{2,}(Democrat|Republican)\s+(.*)$", line)
                if contest is None or found is None:
                    continue
                values = found[3].split()
                if len(values) != len(counties):
                    raise ValueError(f"{day} district {contest} {found[1]}: {len(values)} values for {counties}")
                for county, value in zip(counties, values, strict=True):
                    if value == "X":
                        continue
                    if county == "TOTAL":
                        totals[found[1]] = int(value.replace(",", ""))
                        continue
                    rows.append(
                        (
                            day,
                            party,
                            contest,
                            codes[re.sub(r"[^a-z]", "", county.lower())],
                            found[1],
                            int(value.replace(",", "")),
                        )
                    )
        for candidate, total in totals.items():
            summed = sum(row[5] for row in rows if row[0] == day and row[4] == candidate)
            if summed != total:
                raise ValueError(f"{day} {candidate}: counties sum to {summed}, printed {total}")
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "party": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    if frame.select("day", "district", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("day", "district", "candidate").agg(pl.col("votes").sum(), pl.len()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's 2020 Third District Democratic primary by county from the Secretary of State's official "
            "recapitulation",
            polity="US-MS",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.ms.gov/elections-voting/election-results",
                license="Public record",
            )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber=pl.lit("house:") + pl.col("party"),
                day="day",
                special=pl.lit(False),
                state=pl.lit("MS"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("MS"),
            ),
            {"MS": source},
            strict=True,
        )
        return cs.write()
