import json
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.elstats import FORMER, SEARCH, TENANTS, TITLE, plain
from ea.store import DATA, connect

SOURCE = "us.elstats_president"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
STATES = {"CO": "08", "ID": "16", "VA": "51"}
DAYS = {1992: "1992-11-03", 1996: "1996-11-05"}
METHOD = "County totals are the state's election statistics archive's county-level presidential returns."


def url(tenant: str, year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": "President"}]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return SEARCH.format(tenant=tenant) + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def fetch() -> None:
    for state in STATES:
        for year in DAYS:
            raw.fetch(SOURCE, url(TENANTS[state][0], year), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        names: dict[tuple[str, str], str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and left(u.code, 2) in ('08', '16', '51') and u.code <> '51515' order by u.code"""
        ).fetchall():
            if code[:2] == "51" and int(code[2:]) >= 500:
                names[(code[:2], f"{plain(text)}city")] = code
            names.setdefault((code[:2], plain(text)), code)
        for (state, name), code in FORMER.items():
            if state == "VA":
                names[("51", name)] = code
    frames = []
    for state, prefix in STATES.items():
        for year, day in DAYS.items():
            frame = pl.read_csv(
                raw.cached(SOURCE, url(TENANTS[state][0], year)).path, infer_schema=False, truncate_ragged_lines=True
            ).filter(
                (pl.col("office_name") == "President")
                & pl.col("election_type").str.contains("(?i)general")
                & pl.col("division_type").is_in(["County", "Locality"])
                & ~pl.col("candidate_name").str.contains(r"(?i)^(registered|total|blank|void|undervote|overvote)")
            )
            frames.append(
                frame.select(
                    pl.lit(day).alias("day"),
                    pl.lit("president").alias("district"),
                    pl.col("division_name")
                    .str.strip_chars()
                    .str.replace(r"\s*\(.*\)$", "")
                    .str.replace(r"(?i)\s+county$", "")
                    .map_elements(plain, return_dtype=pl.String)
                    .alias("name"),
                    pl.col("candidate_name").str.to_uppercase().str.strip_chars().alias("column"),
                    pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64).alias("votes"),
                ).with_columns(pl.lit(prefix).alias("prefix"), pl.lit(state).alias("state"))
            )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    frame = frame.with_columns(
        county=pl.struct("prefix", "name").map_elements(
            lambda row: names.get((row["prefix"], row["name"])),
            return_dtype=pl.String,
        )
    )
    lost = frame.filter(pl.col("county").is_null())
    if lost.height:
        print(lost.group_by("prefix", "name").agg(pl.col("votes").sum()))
    PARSED.mkdir(parents=True, exist_ok=True)
    out = frame.filter(pl.col("county").is_not_null()).filter(pl.col("votes").sum().over("day", "state", "column") > 0).group_by("day", "district", "state", "county", "column").agg(pl.col("votes").sum())
    out.write_parquet(PARSED / "lines.parquet")
    print(out.group_by("day", "state").agg(pl.col("county").n_unique(), pl.col("votes").sum()))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Colorado, Idaho, and Virginia presidential elections of 1992 and 1996 by county from the states' election statistics archives",
        "State election statistics archives",
        TITLE,
        "https://historicalelectiondata.coloradosos.gov",
        "",
        "",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url(TENANTS[state][0], year)) for state in STATES for year in DAYS],
        lambda key: METHOD,
        statewide=True,
    )
