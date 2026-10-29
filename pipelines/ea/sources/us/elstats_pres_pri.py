import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl
from ea.store import DATA, connect

SOURCE = "us.elstats_pres_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WANTED = {"CT": (2016, 2020), "VT": (2016, 2020)}


def url(state: str, year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": "President"}]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {"search": json.dumps(search, separators=(",", ":"))}
    )


def fetch() -> None:
    raw.fetch(elstats.SOURCE, elstats.GAZETTEER, suffix=".zip")
    for state, years in WANTED.items():
        for year in years:
            raw.fetch(SOURCE, url(state, year), suffix=".csv")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in(list(WANTED)))
    towns: dict[str, dict[str, str]] = {}
    for state, geoid, name in gazetteer.select("USPS", "GEOID", "NAME").iter_rows():
        names = towns.setdefault(state, {})
        names.setdefault(elstats.plain(name), geoid)
        names.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid)
    for (state, name), code in elstats.FORMER.items():
        if state in towns:
            towns[state][name] = code
    frames = []
    for state, years in WANTED.items():
        for year in years:
            frames.append(
                pl.read_csv(raw.cached(SOURCE, url(state, year)).path, infer_schema=False, truncate_ragged_lines=True)
                .filter(
                    pl.col("office_name") == "President",
                    pl.col("election_type").str.contains("Primary"),
                    pl.col("division_type") == "City/Town",
                )
                .with_columns(
                    state=pl.lit(state),
                    code=pl.col("division_name")
                    .map_elements(elstats.plain, return_dtype=pl.String)
                    .replace_strict(towns[state], default=None),
                )
            )
    frame = pl.concat(frames).with_columns(
        candidate=pl.col("candidate_name").fill_null("").str.replace_all(r"\s+", " ").str.strip_chars().str.to_uppercase(),
        votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
    ).filter(pl.col("votes").is_not_null(), ~pl.col("candidate").str.contains(r"^TOTAL |^NUMBER |^#|UNDERVOTE|OVERVOTE"))
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched towns: {lost.select('state', 'division_name').unique().rows()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = frame.select(
        year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
        chamber=pl.concat_str(pl.lit("president:"), pl.col("primary_party").str.to_lowercase()),
        day=pl.col("election_date").str.slice(0, 10).str.to_date(),
        special=pl.lit(False),
        state="state",
        district=pl.lit(None, pl.Int16),
        code="code",
        candidate=pl.when(pl.col("candidate").str.contains("WRITE")).then(pl.lit("WRITE-INS")).otherwise("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("WRITE"),
        votes="votes",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure").agg(
        pl.col("writein").any(), pl.col("votes").sum()
    ).with_columns(
        code=pl.when(pl.col("code") == "5000710675").then(pl.lit("5010675")).otherwise("code"),
        kind=pl.when(pl.col("code") == "5000710675").then(pl.lit("place")).otherwise(pl.lit("town")),
        source="state",
    )
    frame.write_parquet(PARSED / "towns.parquet")
    print(frame.group_by("state", "year", "chamber").agg(pl.col("code").n_unique(), pl.col("votes").sum()).sort("state", "year", "chamber").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "towns.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Connecticut's and Vermont's 2016 and 2020 Democratic and Republican presidential primaries by town from the states' election statistics archives",
            polity="US",
        )
        sources = {}
        for state in WANTED:
            _, publisher, site = elstats.TENANTS[state]
            sources[state] = cs.source(publisher, elstats.TITLE)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=elstats.TITLE,
                    url=site,
                    license="Public record",
                )
            for year in WANTED[state]:
                cs.artifact(raw.cached(SOURCE, url(state, year)), sources[state])
        gazetteer = cs.source("U.S. Census Bureau", "2020 Gazetteer Files")
        if gazetteer in cs.minted:
            cs.add(
                "source",
                id=gazetteer,
                kind="official_body",
                publisher="U.S. Census Bureau",
                title="2020 Gazetteer Files",
                url="https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html",
                license="Public domain",
            )
        cs.artifact(raw.cached(elstats.SOURCE, elstats.GAZETTEER), gazetteer)
        medsl.attach(cs, conn, frame, sources, grain="town", strict=True)
        return cs.write()
