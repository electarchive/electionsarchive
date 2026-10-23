import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.oh_cuyahoga_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Cuyahoga County Board of Elections"
TITLE = "Canvass report, November 5, 2002 general election, official results"
URL = "https://boe.cuyahogacounty.gov/docs/default-source/boe/elections/2002-11-02-general-election-results.pdf?sfvrsn=4f5a083b_1"
DAY = date(2002, 11, 5)
FIPS = "39035"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    rows: dict[tuple[int, str, str], int] = {}
    printed: dict[tuple[int, str], int] = {}
    district = 0
    names: list[str] = []
    expected = 0
    for line in text.splitlines():
        if found := re.match(r"CONGRESS - (10|11)TH DISTRICT", line):
            district, names, expected = int(found[1]), [], 0
            continue
        if not district:
            continue
        if re.match(r"\s+-{10,}", line):
            expected = len(names)
            continue
        if not expected:
            for number, name, votes in re.findall(
                r"(\d\d) = ([A-Z][A-Z.' -]*?)(?: \([A-Z]+\))?\s{2,}([\d,]+)\s", line + " "
            ):
                names.append(name.strip())
                printed[(district, name.strip())] = int(votes.replace(",", ""))
            for number, name in re.findall(r"(\d\d) = ([A-Z][A-Z.' -]*[A-Z.])\s*$", line):
                names.append(name.strip())
            continue
        row = re.fullmatch(r"(\d{4}) (.+?)\s{2,}((?:\d+\s*)+)", line.strip())
        if row is None:
            if re.search(r"[A-Z]{4}", line) and not re.search(
                r"CITY|WARD|VILLAGE|TOWNSHIP|CONTINUED|CANVASS|OFFICIAL|GENERAL|RUN DATE|REPORT|VOTES|PERCENT|TOTAL", line
            ):
                district = 0
            continue
        cells = [int(cell) for cell in row[3].split()]
        if len(cells) != expected:
            raise ValueError(f"{row[1]}: {cells} for {names}")
        for name, votes in zip(names, cells, strict=True):
            rows[(district, row[1], name)] = votes
    frame = pl.DataFrame(
        [(district, precinct, name, votes) for (district, precinct, name), votes in rows.items()],
        schema=["district", "precinct", "candidate", "votes"],
        orient="row",
    )
    sums = frame.group_by("district", "candidate").agg(pl.col("votes").sum())
    for district, name, votes in sums.iter_rows():
        total = next(
            (value for (d, n), value in printed.items() if d == district and n.endswith(name.split()[-1])), None
        )
        print(district, name, votes, total)
        if total is not None and total != votes:
            raise ValueError(f"{district} {name}: precincts add up to {votes}, printed {total}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.lit(2002, pl.Int32),
        chamber=pl.lit("house"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("OH"),
        district=pl.col("district").cast(pl.Int16),
        kind=pl.lit("precinct"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        fips=pl.lit(FIPS),
        code=pl.concat_str(pl.lit(f"2002:OH:{FIPS}::"), "precinct"),
    ).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's 2002 10th and 11th District generals by precinct from the Cuyahoga County Board of "
            "Elections' official canvass report",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://boe.cuyahogacounty.gov/elections/election-information-archive",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        frame = pl.read_parquet(PARSED / "precincts.parquet").with_columns(source=pl.lit("OH"))
        medsl.attach(cs, conn, frame.drop("fips", "precinct"), {"OH": source}, grain="precinct", strict=True)
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        county, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()
        units = (
            frame.group_by("code")
            .agg(pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)) & pl.col("id").is_in([str(unit) for unit in cs.minted]))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-OH"))),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.lit(str(county)),
                area_relation=pl.lit("within"),
                area_id=pl.lit(str(area)),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{units.height} new precinct units")
        return cs.write()
