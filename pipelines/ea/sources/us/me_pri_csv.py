import csv
import io
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import me_federal, medsl
from ea.store import DATA, connect

SOURCE = "us.me_pri_csv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/http://www.maine.gov/sos/cec/elec/{}"
FILES = [
    (date(2002, 6, 11), "20030728201528", "2002p/pri02c1d.txt", "democratic"),
    (date(2002, 6, 11), "20030728201703", "2002p/pri02c1r.txt", "republican"),
    (date(2002, 6, 11), "20030912070242", "2002p/pri02c2d.txt", "democratic"),
    (date(2002, 6, 11), "20030728202334", "2002p/pri02c2r.txt", "republican"),
    (date(2002, 6, 11), "20030728205741", "2002p/pri02sd.txt", "democratic"),
    (date(2002, 6, 11), "20030728210240", "2002p/pri02sr.txt", "republican"),
    (date(2004, 6, 8), "20040816152147", "2004/pri04cd.txt", "democratic"),
    (date(2004, 6, 8), "20040816160545", "2004/pri04cr.txt", "republican"),
    (date(2006, 6, 13), "20061104163036", "2006p/pri06c1d.txt", "democratic"),
    (date(2006, 6, 13), "20061104162934", "2006p/pri06c1r.txt", "republican"),
    (date(2006, 6, 13), "20061104162527", "2006p/pri06c2d.txt", "democratic"),
    (date(2006, 6, 13), "20061104162741", "2006p/pri06c2r.txt", "republican"),
    (date(2006, 6, 13), "20061104163114", "2006p/pri06sd.txt", "democratic"),
    (date(2006, 6, 13), "20061104163019", "2006p/pri06sr.txt", "republican"),
]


def fetch() -> None:
    for _, stamp, path, _ in FILES:
        url = WAYBACK.format(stamp, path)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfL", "--resolve", "web.archive.org:443:207.241.237.2", "-A", raw.USER_AGENT, url],
                capture_output=True,
                check=True,
            ).stdout
            raw.store(
                SOURCE, url, body, suffix=".txt", retrieved_at=datetime.now(UTC), media_type="text/plain"
            )


def parse() -> None:
    rows = []
    for day, stamp, path, party in FILES:
        text = raw.cached(SOURCE, WAYBACK.format(stamp, path)).path.read_text(encoding="latin-1")
        for record in csv.DictReader(io.StringIO(text)):
            record = {key.strip().lower(): (value or "").strip() for key, value in record.items() if key}
            office = record["office"]
            district = int(record["district"]) if record["district"] else None
            for number in [k.removeprefix("fullname") for k in record if k.startswith("fullname")]:
                name = record[f"fullname{number}"]
                if name:
                    rows.append(
                        (
                            day,
                            f"{'senate' if office == 'US' else 'house'}:{party}",
                            district,
                            record["county"],
                            record["town"],
                            name.upper(),
                            int(record[f"votes{number}"] or 0),
                        )
                    )
    frame = pl.DataFrame(
        rows,
        schema=["day", "chamber", "district", "county", "town", "candidate", "votes"],
        orient="row",
    )
    homes = (
        frame.filter(pl.col("county") != "")
        .group_by("town")
        .agg(pl.col("county").mode().first().alias("home"))
    )
    frame = frame.join(homes, on="town", how="left").with_columns(
        pl.when(pl.col("county") != "").then(pl.col("county")).otherwise(pl.col("home")).alias("county")
    )
    lost = frame.filter(~pl.col("county").is_in(list(me_federal.COUNTIES)))
    if lost.height:
        raise ValueError(f"unplaced towns: {lost['town'].unique().to_list()[:10]}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("ME"),
        code=pl.col("county").replace_strict(me_federal.COUNTIES),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("ME"),
        district=pl.col("district").cast(pl.Int16),
    ).group_by(
        "year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein", "kind", "source"
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2002, 2004, and 2006 U.S. Senate and House primaries by county, summed from the Secretary of State's town and ward files",
            polity="US-ME",
        )
        source = cs.source(me_federal.PUBLISHER, me_federal.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=me_federal.PUBLISHER,
            title=me_federal.TITLE,
            url="https://www.maine.gov/sos/cec/elec/results/",
            license="Public record",
        )
        for _, stamp, path, _ in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, path)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ME": source}, strict=True)
        return cs.write()
