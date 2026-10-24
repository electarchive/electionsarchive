import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ar_pri_cert"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = (
    "https://web.archive.org/web/20170210id_/http://www.sos.arkansas.gov/elections/historicalElectionResults/Documents/"
)
FILES = {
    "2002_Preferential_Primary_Non-Partisan_Judicial.pdf": date(2002, 5, 21),
    "2004_Preferential_Primary_and_Non-Partisan_Judicial_Election_Certification_Report.pdf": date(2004, 5, 18),
}
PARTIES = {"Democrat": "democratic", "Republican": "republican"}
TITLES = re.compile(
    r"^(?:U\.S\. Senator|Sen\.|State Senator|State Representative|Representative|Congressman|Congresswoman)\s+"
)
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Preferential primary certification reports by county"


def fetch() -> None:
    for name in FILES:
        try:
            raw.cached(SOURCE, BASE + name)
        except LookupError:
            raw.store(
                SOURCE,
                BASE + name,
                (DATA / "inbox" / "manual" / "MF-048" / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 23, tzinfo=UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"\W", "", name.removesuffix(", AR").removesuffix(" County")).lower().replace("saint", "st"): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '05%%' and u.valid @> '2002-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for name, day in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        party = county = contest = None
        summaries = 0
        for line in text.splitlines():
            line = line.strip()
            if header := re.fullmatch(
                r"\d{4} Preferential Primary .* - (Republican|Democrat|Non Partisan Judicial)", line
            ):
                party = PARTIES.get(header[1])
                summaries += line.startswith("2002") and header[1] == "Republican"
                contest = None
                continue
            if day.year == 2002:
                if summaries != 2:
                    continue
                if found := re.fullmatch(r"(.+) County", line):
                    county, contest = codes[re.sub(r"\W", "", found[1]).lower().replace("saint", "st")], None
                    continue
                if re.fullmatch(r"U\.S\. Senate|U\.S\. Congress District 0\d", line):
                    contest = line
                    continue
            elif found := re.fullmatch(
                r"(U\.S\. Senate|U\.S\. Congress District 0\d) - (Democrat|Republican) \((.+) County\)", line
            ):
                contest, party = found[1], PARTIES[found[2]]
                county = codes[re.sub(r"\W", "", found[3]).lower().replace("saint", "st")]
                continue
            if contest is None or county is None or party is None:
                continue
            if found := re.fullmatch(r"(.+?)\s+(\d[\d,]*)\s+[\d.]+%", line):
                candidate = TITLES.sub("", re.sub(r" - (Democrat|Republican)$", "", found[1].strip()))
                rows.append((day, party, contest, county, candidate, int(found[2].replace(",", ""))))
            elif line and not re.match(r"(Total )?(over|under|Over|Under)", line):
                contest = None
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "party": pl.String,
                "contest": pl.String,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .unique(maintain_order=True)
        .with_columns(
            district=pl.col("contest").str.extract(r"District 0(\d)").cast(pl.Int16),
        )
    )
    if frame.select("day", "party", "contest", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    for row in (
        frame.group_by("day", "party", "contest", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum(), pl.len())
        .sort("day", "contest", "party")
        .rows()
    ):
        print(row)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's 2002 and 2004 U.S. Senate and House primaries by county from the Secretary of State's certification reports; the files were saved from Wayback captures by a person in a browser, because the live copies are truncated",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.arkansas.gov/elections/research/election-results",
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber=pl.when(pl.col("district").is_null()).then(pl.lit("senate:")).otherwise(pl.lit("house:"))
                + pl.col("party"),
                day="day",
                special=pl.lit(False),
                state=pl.lit("AR"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("AR"),
            ),
            {"AR": source},
            strict=True,
        )
        return cs.write()
