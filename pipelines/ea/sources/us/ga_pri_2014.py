import csv
import io
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ga_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-331"
PAGE = "https://sos.ga.gov/page/historical-elections-results"
FILES = {
    date(2014, 5, 20): "may_20_2014_-_gen_primary_np_general.zip",
    date(2014, 7, 22): "july_22_2014_-_gen_primary_np_general_runoff.zip",
}
CONTEST = re.compile(r"^State (?P<office>Representative|Senator), District (?P<district>\d+) - (?P<party>REP|DEM)$")
OFFICES = {"Representative": "house", "Senator": "senate"}
PARTIES = {"DEM": "d", "REP": "r"}
STAGES = ("pfsettled", "pfunsettled", "pfpartunsettled", "p")
PUBLISHER = "Georgia Secretary of State"
TITLE = "2014 General Primary and Nonpartisan General Election: county summary results"


def url(file: str) -> str:
    return f"{PAGE}#{file}"


def fetch() -> None:
    for file in FILES.values():
        try:
            raw.cached(SOURCE, url(file))
        except LookupError:
            raw.store(
                SOURCE,
                url(file),
                (INBOX / file).read_bytes(),
                suffix=".zip",
                retrieved_at=datetime.fromtimestamp((INBOX / file).stat().st_mtime, UTC),
                media_type="application/zip",
            )


def parse() -> None:
    with connect() as conn:
        codes = openelections.counties(conn)["GA"]
        keys = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^2014-0[57]-[0-9]+:GA:(house|senate):'"
            ).fetchall()
        }
    rows: list[tuple[date, str, str, str, int]] = []
    for day, file in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url(file)).path) as outer:
            for name in sorted(outer.namelist()):
                if "/summary/" not in name or not name.endswith(".zip"):
                    continue
                county = re.sub(r"_\d+_\d+-summary\.zip$", "", name.rsplit("/", 1)[1])
                code = codes[re.sub(r"[^a-z]", "", county.replace("_", " ").lower().replace("saint ", "st "))]
                with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                    text = inner.read(inner.namelist()[0]).decode("latin-1")
                for entry in csv.DictReader(io.StringIO(text)):
                    match = CONTEST.match(entry["contest name"])
                    if match is None:
                        continue
                    base = f"{day}:GA:{OFFICES[match['office']]}:{int(match['district'])}:{PARTIES[match['party']]}"
                    stages = ("prunoff",) if day.month == 7 else STAGES
                    key = next((f"{base}{stage}" for stage in stages if f"{base}{stage}" in keys), None)
                    if key is None:
                        print(f"{base}: no contest, {entry['total votes']} skipped")
                        continue
                    rows.append(
                        (
                            day,
                            key.split(":", 2)[2],
                            code,
                            re.sub(r"\s+", " ", re.sub(r"\s*\(I\)\s*$", "", entry["choice name"])).strip().upper(),
                            int(entry["total votes"]),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row").with_columns(
        year=pl.lit(2014, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("GA"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.starts_with("WRITE"),
        kind=pl.lit("county"),
        source=pl.lit("state"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 2014 state House and Senate primaries and runoffs by county from the Secretary of State's county summary results",
            polity="US-GA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        for file in FILES.values():
            cs.artifact(raw.cached(SOURCE, url(file)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"state": source}, strict=True)
        return cs.write()
