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

SOURCE = "us.fl_extract"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {
    date(2001, 10, 16): "special",
    date(2004, 3, 9): "president",
    date(2008, 1, 29): "president",
    date(2010, 4, 13): "special",
    date(2012, 1, 31): "president",
    date(2014, 3, 11): "special",
    date(2014, 6, 24): "special",
    date(2016, 3, 15): "president",
    date(2016, 8, 30): "senate",
    date(2020, 3, 17): "president",
    date(2025, 4, 1): "special",
}
PARTIES = {"DEM": "democratic", "REP": "republican", "LPF": "libertarian"}


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
    for day, kind in DAYS.items():
        frame = (
            pl.read_csv(
                io.BytesIO(raw.cached(SOURCE, fl_sos.EXTRACT.format(day=day)).path.read_bytes().decode("cp1252").encode()),
                separator="\t",
                infer_schema_length=0,
                quote_char=None,
            )
            .filter(
                pl.col("RaceCode").is_in({"special": ["USR"], "president": ["PRE", "PRC", "PPC"], "senate": ["USS"]}[kind]),
                pl.col("PartyCode").is_in(list(PARTIES)) if kind != "special" else pl.lit(True),
                pl.col("PartyCode") == "LPF" if kind == "senate" else pl.lit(True),
            )
            .unique()
        )
        if (frame["RaceCode"] == "PRE").any():
            frame = frame.filter(pl.col("RaceCode") == "PRE")
        clean = [
            pl.col(column).fill_null("").str.replace_all(",", "").str.replace_all(r"\s+", " ").str.strip_chars()
            for column in ("CanNameFirst", "CanNameMiddle", "CanNameLast")
        ]
        frames.append(
            frame.select(
                day=pl.lit(day),
                kind=pl.lit(kind),
                district=pl.col("Juris1num").str.strip_chars().str.replace(r"^0+", "") if kind == "special" else pl.lit(""),
                party=pl.col("PartyCode").replace(PARTIES) if kind != "special" else pl.lit(""),
                code=pl.col("CountyName").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
                candidate=pl.concat_str(clean, separator=" ").str.replace_all(r"\s+", " ").str.strip_chars().str.to_uppercase(),
                votes=pl.col("CanVotes").cast(pl.Int64),
            )
            .group_by("day", "kind", "district", "party", "code", "candidate", maintain_order=True)
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
            summary="Florida's presidential primaries 2004–2020, U.S. House specials, and the 2016 Libertarian Senate primary by county",
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
        lines = []
        for (day, kind, district, party), part in frame.group_by("day", "kind", "district", "party", maintain_order=True):
            lines.append(
                part.select(
                    year=pl.lit(day.year, pl.Int32),
                    chamber=pl.lit("house" if kind == "special" else f"{kind}:{party}"),
                    day=pl.lit(day),
                    special=pl.lit(kind == "special"),
                    state=pl.lit("FL"),
                    district=pl.lit(int(district) if district else None, pl.Int16),
                    code="code",
                    candidate="candidate",
                    measure=pl.lit(1, pl.Int16),
                    writein=pl.col("candidate").str.contains("WRITE"),
                    votes="votes",
                    kind=pl.lit("county"),
                    source=pl.lit("FL"),
                )
            )
        medsl.attach(cs, conn, pl.concat(lines), {"FL": source}, strict=True)
        return cs.write()
