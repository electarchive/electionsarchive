import io
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fl_sos, fl_state_primaries, medsl
from ea.store import DATA, connect

SOURCE = "us.fl_pri_9x"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [date(1992, 9, 1), date(1994, 9, 8), date(1994, 10, 4), date(1996, 9, 3), date(1996, 10, 1), date(1998, 9, 1)]
PARTIES = {"DEM": "democratic", "REP": "republican"}


def fetch() -> None:
    for day in DAYS:
        url = fl_sos.EXTRACT.format(day=day)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            response = httpx.get(url, headers=fl_state_primaries.HEADERS, timeout=120, follow_redirects=True)
            response.raise_for_status()
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".txt",
                retrieved_at=datetime.now(UTC),
                media_type="text/tab-separated-values",
            )
            time.sleep(1)


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '12%%'"""
            ).fetchall()
        }
    fips["dade"] = fips["miamidade"]
    frames = []
    for day in DAYS:
        frame = (
            pl.read_csv(
                io.BytesIO(
                    raw.cached(SOURCE, fl_sos.EXTRACT.format(day=day)).path.read_bytes().decode("cp1252").encode()
                ),
                separator="\t",
                infer_schema_length=0,
                quote_char=None,
            )
            .filter(pl.col("RaceCode").is_in(["USR", "USS"]), pl.col("PartyCode").is_in(list(PARTIES)))
            .unique()
        )
        clean = [
            pl.col(column).fill_null("").str.replace_all(",", "").str.replace_all(r"\s+", " ").str.strip_chars()
            for column in ("CanNameFirst", "CanNameMiddle", "CanNameLast")
        ]
        frames.append(
            frame.select(
                day=pl.lit(day),
                chamber=pl.when(pl.col("RaceCode") == "USR").then(pl.lit("house")).otherwise(pl.lit("senate")),
                district=pl.col("Juris1num").str.strip_chars().str.replace(r"^0+", ""),
                party=pl.col("PartyCode").replace_strict(PARTIES),
                code=pl.col("CountyName").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
                candidate=pl.concat_str(clean, separator=" ")
                .str.replace_all(r"\s+", " ")
                .str.strip_chars()
                .str.to_uppercase(),
                votes=pl.col("CanVotes").cast(pl.Int64),
            )
            .group_by("day", "chamber", "district", "party", "code", "candidate", maintain_order=True)
            .agg(pl.col("votes").sum())
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida's U.S. House and Senate primaries and runoffs, 1992–1998, by county",
            polity="US-FL",
        )
        source = cs.source(fl_sos.PUBLISHER, fl_sos.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=fl_sos.PUBLISHER,
            title=fl_sos.TITLE,
            url="https://results.elections.myflorida.com/",
            license="Public record",
        )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, fl_sos.EXTRACT.format(day=day)), source)
        lines = frame.select(
            year=pl.col("day").dt.year().cast(pl.Int32),
            chamber=pl.col("chamber") + ":" + pl.col("party"),
            day="day",
            special=pl.lit(False),
            state=pl.lit("FL"),
            district=pl.when(pl.col("chamber") == "house").then(pl.col("district").cast(pl.Int16)).otherwise(None),
            code="code",
            candidate="candidate",
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").str.contains("WRITE"),
            votes="votes",
            kind=pl.lit("county"),
            source=pl.lit("FL"),
        )
        medsl.attach(cs, conn, lines, {"FL": source}, strict=True)
        return cs.write()
