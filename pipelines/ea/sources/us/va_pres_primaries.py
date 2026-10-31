import json
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.elstats import FORMER, plain
from ea.store import DATA, connect

SOURCE = "us.va_pres_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (2004, 2008, 2012, 2016, 2020)
PUBLISHER = "Virginia Department of Elections"
TITLE = "Election statistics archive: presidential primaries"
SITE = "https://historical.elections.virginia.gov"


def url(year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": "President"}]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return "https://va2.elstats.civera.com/api/download_search.csv?" + urlencode(
        {"search": json.dumps(search, separators=(",", ":"))}
    )


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, url(year), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties: dict[str, str] = {}
        for code, text in conn.execute(
            "select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short' "
            "where u.scheme_code = 'us.county' and u.code like '51%' and u.code <> '51515' order by u.code"
        ).fetchall():
            if int(code[2:]) >= 500:
                counties[f"{plain(text)}city"] = code
            counties.setdefault(plain(text), code)
    counties.update({name: code for (state, name), code in FORMER.items() if state == "VA"})
    frames = []
    for year in YEARS:
        frame = pl.read_csv(raw.cached(SOURCE, url(year)).path, infer_schema=False, truncate_ragged_lines=True)
        frames.append(
            frame.filter(
                (pl.col("office_name") == "President")
                & pl.col("election_type").str.contains("Primary")
                & (pl.col("division_type") == "Locality")
            )
        )
    frame = pl.concat(frames)
    division = (
        pl.col("division_name")
        .str.replace(r"\s*\(.*\)$", "")
        .str.to_lowercase()
        .str.replace_all("saint ", "st ")
        .str.replace_all("&", "and")
        .str.replace_all(r"\bcounty\b", "")
        .str.replace_all(r"[^a-z]", "")
    )
    frame = frame.with_columns(
        code=division.replace_strict(counties, default=None),
        votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
        candidate=pl.col("candidate_name")
        .fill_null("")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .str.to_uppercase(),
    ).filter(pl.col("votes").is_not_null(), ~pl.col("candidate").str.contains("TOTAL VOTES|UNDERVOTE"))
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched localities: {lost['division_name'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
        chamber=pl.concat_str(pl.lit("president:"), pl.col("primary_party").str.to_lowercase()),
        day=pl.col("election_date").str.slice(0, 10).str.to_date(),
        special=pl.lit(False),
        state=pl.lit("VA"),
        district=pl.lit(None, pl.Int16),
        code="code",
        candidate=pl.when(pl.col("candidate").str.contains("WRITE")).then(pl.lit("WRITE-INS")).otherwise("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("WRITE"),
        votes="votes",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure").agg(
        pl.col("writein").any(), pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("VA")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia's Democratic and Republican presidential primaries, 2004 to 2020, by locality from the Department of Elections' election statistics archive",
            polity="US-VA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        medsl.attach(cs, conn, frame, {"VA": source}, strict=True)
        return cs.write()
