import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.oh_channels"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Franklin County Board of Elections"
TITLE = "Official canvass by ballot type, 2022 and 2024 general elections"
COUNTY = "39049"
SITE = "https://vote.franklincountyohio.gov/getmedia/"
LANDING = "https://vote.franklincountyohio.gov/election-info/archives"
FILES = {
    date(2022, 11, 8): SITE + "99139294-843a-4cb6-8b54-a0d81d09e865/Franklin-Official-Group-Detail_amended",
    date(2024, 11, 5): SITE + "e5b5f9e3-9468-4020-8ea7-e98354add00f/Franklin-s-Official-Canvass",
}
COLUMNS = [3, 2, 1, 5]
CONTESTS = {
    "President and Vice President": ("president", False),
    "Governor and Lieutenant Governor": ("governor", False),
    "Attorney General": ("attorney-general", False),
    "Auditor of State": ("auditor", False),
    "Secretary of State": ("secretary-of-state", False),
    "Treasurer of State": ("treasurer", False),
    "U.S. Senator": ("senate", False),
    "Chief Justice of the Supreme Court": ("chief-justice-of-the-supreme-court:term-commencing-01-01-2023", False),
    "Justice of the Supreme Court (1/1)": ("justice-of-the-supreme-court:term-commencing-01-01-2023", False),
    "Justice of the Supreme Court (1/2)": ("justice-of-the-supreme-court:term-commencing-01-02-2023", False),
    "Justice of the Supreme Court (1-1-25)": ("justice-of-the-supreme-court:term-commencing-01-01-2025", False),
    "Justice of the Supreme Court (1-2-25)": ("justice-of-the-supreme-court:term-commencing-01-02-2025", False),
    "Justice of the Supreme Court (UTE 12-31-26)": (
        "justice-of-the-supreme-court:unexpired-term-ending-12-31-2026",
        True,
    ),
}
HEADING = re.compile(r"^For (.+?)(?: -)?\s*$")
ROW = re.compile(
    r"^\s*(?:(?:Dem|Rep|Lib|Grn|Ind|Npc|OPC|Con|Soc|Gre|Non)\s+)?(?P<name>\S.*?)\s{2,}"
    r"(?P<total>[\d,]+)\s+[\d.]+%\s+(?P<mail>[\d,]+)\s+(?P<office>[\d,]+)\s+(?P<day>[\d,]+)\s+(?P<prov>[\d,]+)\s*$"
)
PARTY = re.compile(r"^(?:Dem|Rep|Lib|Grn|Ind|Npc|OPC|Con|Soc|Gre|Non)\s+")


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def parse() -> None:
    records = []
    for day, url in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        current = None
        for line in text.splitlines():
            if heading := HEADING.match(line):
                current = CONTESTS.get(heading[1].strip())
                continue
            if current is None or line.strip().startswith(("Write-In:", "Precincts")):
                continue
            row = ROW.match(line)
            if row is None:
                if re.match(r"^\s+(Overvotes|Undervotes)", line):
                    numbers = re.findall(r"[\d,]+", line)
                    if len(numbers) == 5:
                        records.extend(split(day, current, line.split()[0].upper(), numbers))
                continue
            name = PARTY.sub("", row["name"]).strip()
            name = re.split(r"\s+and\s+", name)[0]
            if name == "Candidate Withdrawn":
                continue
            values = [row["total"], row["mail"], row["office"], row["day"], row["prov"]]
            records.extend(split(day, current, name, values))
    frame = pl.DataFrame(records, infer_schema_length=None)
    frame = (
        frame.with_columns(
            candidate=pl.col("name").str.to_uppercase().str.strip_chars(),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("day", "chamber", "special", "candidate", "measure", "channel")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.lit(None, pl.Int16),
            code=pl.lit(COUNTY),
            state=pl.lit("OH"),
            kind=pl.lit("county"),
            source=pl.lit("OH"),
        )
        .sort("day", "chamber", "candidate", "channel")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "channels.parquet")
    print(frame.group_by("year", "channel").agg(pl.col("votes").sum()).sort("year", "channel").rows())


def split(day: date, current: tuple[str, bool], name: str, values: list[str]) -> list[dict[str, object]]:
    total = number(values[0])
    parts = [number(value) for value in values[1:]]
    if sum(parts) != total:
        raise ValueError(f"{day} {current[0]} {name}: channels {sum(parts)} != total {total}")
    return [
        {"day": day, "chamber": current[0], "special": current[1], "name": name, "channel": channel, "votes": count}
        for channel, count in zip(COLUMNS, parts, strict=True)
    ]


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "channels.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Franklin County, Ohio's 2022 and 2024 general elections for president, Senate, statewide offices, and the Supreme Court by ballot type (election day, in-office absentee, mail absentee, and provisional), from the Board of Elections' official canvass",
            polity="US-OH",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, frame, {"OH": source})
        return cs.write()
