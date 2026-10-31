import io
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fl_sos, medsl
from ea.store import DATA, connect

SOURCE = "us.fl_state_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
DAYS = [
    date(1978, 9, 12),
    date(1980, 9, 9),
    date(1980, 10, 7),
    date(1982, 9, 7),
    date(1982, 10, 5),
    date(1984, 9, 4),
    date(1984, 10, 2),
    date(1986, 9, 2),
    date(1986, 9, 30),
    date(1988, 9, 6),
    date(1988, 10, 4),
    date(2014, 8, 26),
]
RUNOFFS = {date(1980, 10, 7), date(1982, 10, 5), date(1984, 10, 2), date(1986, 9, 30), date(1988, 10, 4)}
PARTIES = {"DEM": "dp", "REP": "rp"}


def fetch() -> None:
    for day in DAYS:
        url = fl_sos.EXTRACT.format(day=day)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            response = httpx.get(url, headers=HEADERS, timeout=120, follow_redirects=True)
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
            .filter(pl.col("RaceCode").is_in(["STR", "STS"]), pl.col("PartyCode").is_in(list(PARTIES)))
            .unique()
        )
        clean = [
            pl.col(column).fill_null("").str.replace_all(",", "").str.replace_all(r"\s+", " ").str.strip_chars()
            for column in ("CanNameFirst", "CanNameMiddle", "CanNameLast")
        ]
        frames.append(
            frame.select(
                day=pl.lit(day),
                chamber=pl.when(pl.col("RaceCode") == "STR").then(pl.lit("house")).otherwise(pl.lit("senate")),
                district=pl.col("Juris1num").str.strip_chars().str.replace(r"^0+", ""),
                post=pl.col("Juris2num").fill_null("").str.strip_chars().str.replace(r"^0+", ""),
                party=pl.col("PartyCode").replace_strict(PARTIES),
                code=pl.col("CountyName").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
                candidate=pl.concat_str(clean, separator=" ")
                .str.replace_all(r"\s+", " ")
                .str.strip_chars()
                .str.to_uppercase(),
                votes=pl.col("CanVotes").cast(pl.Int64),
            )
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
            summary="Florida's state House and Senate primaries by county, 1978–1988 and 2014",
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
        keys = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ ':FL:(house|senate):[0-9]+:'"
            ).fetchall()
        }
        held = {
            value
            for (value,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   join changeset c on c.id = r.changeset_id
                   where r.status = 'active' and c.pipeline <> %s and x.value ~ ':FL:(house|senate):[0-9]+:'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.precinct'))""",
                [SOURCE],
            ).fetchall()
        }
        lines = []
        for (day, chamber, district, post, party), part in frame.group_by(
            "day", "chamber", "district", "post", "party", maintain_order=True
        ):
            label = f"{day:%Y-%m}" if day.year < 2000 else str(day)
            seat = f"{chamber}:{district}" + (f":{post}" if post else "")
            chosen = next(
                (
                    f"{seat}:{party}{kind}"
                    for kind in (("runoff",) if day in RUNOFFS else ("", "fsettled", "funsettled", "fpartunsettled"))
                    if f"{label}:FL:{seat}:{party}{kind}" in keys
                ),
                None,
            )
            if chosen is None or f"{label}:FL:{chosen}" in held:
                continue
            lines.append(
                part.select(
                    year=pl.lit(day.year, pl.Int32),
                    chamber=pl.lit(chosen),
                    day=pl.lit(label),
                    special=pl.lit(False),
                    state=pl.lit("FL"),
                    district=pl.lit(None, pl.Int16),
                    code="code",
                    candidate="candidate",
                    measure=pl.lit(1, pl.Int16),
                    writein=pl.lit(False),
                    votes="votes",
                    kind=pl.lit("county"),
                    source=pl.lit("FL"),
                )
            )
        medsl.attach(cs, conn, pl.concat(lines), {"FL": source})
        return cs.write()
