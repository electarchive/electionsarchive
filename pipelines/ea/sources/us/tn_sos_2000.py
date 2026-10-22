import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.tn_sos_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Tennessee Secretary of State, Division of Elections"
TITLE = "November 7, 2000 general election results: State Senate and State House of Representatives"
ORIGINAL = "https://sharetngov.tnsosfiles.com/sos/election/results/2000-11/{name}.pdf"
FILES = {"senate": "senate1-33", "house": "house1-99"}
CITATION = (
    "Source: the Division of Elections' certified results for November 7, 2000 (senate1-33.pdf and house1-99.pdf)."
)
HEADER = re.compile(r"Tennessee (Senate|House of Representatives) District")
CANDIDATE = re.compile(r"^\s*(\d+) \. (.+)$")
ROW = re.compile(r"^([A-Z][A-Z .'&-]*?)\s{2,}(\d[\d ]*)$")


def url(name: str) -> str:
    return f"https://web.archive.org/web/2023id_/{ORIGINAL.format(name=name)}"


def fetch() -> None:
    for name in FILES.values():
        raw.fetch(SOURCE, url(name), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TN"]
    rows = []
    for chamber, name in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(name)).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        blocks: list[tuple[int, list[str], list[tuple[str, list[int]]], list[int]]] = []
        district: int | None = None
        waiting = False
        for line in text:
            if HEADER.search(line):
                district, waiting = None, True
            elif waiting and line.strip().isdigit():
                district, waiting = int(line.strip()), False
                blocks.append((district, [], [], []))
            elif district is None:
                continue
            elif match := CANDIDATE.match(line):
                blocks[-1][1].append(match.group(2).rsplit(" - ", 1)[0].strip())
            elif line.startswith("District Totals"):
                blocks[-1][3].extend(int(value) for value in line.removeprefix("District Totals").split())
            elif (match := ROW.match(line)) and not blocks[-1][3]:
                votes = [int(value) for value in match.group(2).split()]
                if len(votes) != len(blocks[-1][1]):
                    raise ValueError(f"{chamber} {district}: {line.strip()} has {len(votes)} columns")
                blocks[-1][2].append((match.group(1).strip(), votes))
        for district, candidates, counties, totals in blocks:
            sums = [sum(votes[column] for _, votes in counties) for column in range(len(candidates))]
            if totals != sums:
                raise ValueError(f"{chamber} {district}: counties add up to {sums} against {totals}")
            for county, votes in counties:
                rows.extend(
                    (chamber, district, county, candidate, count)
                    for candidate, count in zip(candidates, votes, strict=True)
                )
    frame = pl.DataFrame(rows, schema=["chamber", "district", "county", "candidate", "votes"], orient="row")
    frame = frame.with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(
            writein=pl.col("candidate").str.to_uppercase().str.starts_with("WRITE-IN"),
            candidate=pl.col("candidate").str.replace(r"^Write-Ins, _$", "WRITE-INS"),
        )
        .select(
            state=pl.lit("TN"),
            day=pl.lit("2000-11-07").str.to_date(),
            chamber=pl.concat_str(
                pl.col("chamber"),
                pl.col("district").cast(pl.String),
                pl.when((pl.col("chamber") == "senate") & (pl.col("district") == 25))
                .then(pl.lit("gs"))
                .otherwise(pl.lit("g")),
                separator=":",
            ),
            code=pl.col("code"),
            candidate=pl.col("candidate"),
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("writein"),
            votes=pl.col("votes").cast(pl.Int64),
            year=pl.lit(2000, pl.Int32),
            special=pl.lit(False),
            district=pl.lit(None, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("TN"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee's State Senate and State House general elections of November 7, 2000 by county, from the Division of Elections' certified results",
            polity="US-TN",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sharetngov.tnsosfiles.com/sos/election/results/2000-11/index.htm",
            license="Public record",
        )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, url(name)), source)
        medsl.attach(cs, conn, frame, {"TN": source}, extend=True, authoritative=CITATION)
        return cs.write()
