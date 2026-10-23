import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.sources.us.elstats import FORMER, plain
from ea.store import DATA, connect

SOURCE = "us.va_statewide_gen"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = (
    (2001, "Lieutenant Governor"),
    (2001, "Attorney General"),
    (2005, "Lieutenant Governor"),
    (2005, "Attorney General"),
)
PUBLISHER = "Virginia Department of Elections"
TITLE = "Election statistics archive: statewide offices"
SITE = "https://historical.elections.virginia.gov"
METHOD = (
    "County totals are the Department of Elections' returns by locality (counties and independent cities) from its "
    "election statistics archive. The winner shown is the candidate with the most votes in those totals."
)


def url(year: int, office: str) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": office}]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return "https://va2.elstats.civera.com/api/download_search.csv?" + urlencode(
        {"search": json.dumps(search, separators=(",", ":"))}
    )


def fetch() -> None:
    for year, office in OFFICES:
        raw.fetch(SOURCE, url(year, office), suffix=".csv")


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
    division = (
        pl.col("division_name")
        .str.replace(r"\s*\(.*\)$", "")
        .str.to_lowercase()
        .str.replace_all("saint ", "st ")
        .str.replace_all("&", "and")
        .str.replace_all(r"\bcounty\b", "")
        .str.replace_all(r"[^a-z]", "")
    )
    frames = []
    for year, office in OFFICES:
        frame = pl.read_csv(raw.cached(SOURCE, url(year, office)).path, infer_schema=False, truncate_ragged_lines=True)
        frames.append(
            frame.filter(
                (pl.col("office_name") == office)
                & (pl.col("election_type") == "General Election")
                & (pl.col("division_type") == "Locality")
            )
        )
    frame = pl.concat(frames).with_columns(
        code=division.replace_strict(counties, default=None),
        votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
        name=pl.col("candidate_name").fill_null("").str.replace_all(r"\s+", " ").str.strip_chars().str.to_uppercase(),
    )
    frame = frame.filter(pl.col("votes").is_not_null(), ~pl.col("name").str.contains("TOTAL VOTES|UNDERVOTE"))
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched localities: {lost['division_name'].unique().to_list()}")
    contest = ["year", "day", "state", "title", "post", "special"]
    returns = (
        frame.select(
            year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
            day=pl.col("election_date").str.slice(0, 10).str.to_date(),
            state=pl.lit("VA"),
            title=pl.col("office_name"),
            post=pl.lit(None, pl.String),
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            county=pl.col("code"),
            candidate=pl.when(pl.col("name").str.contains("WRITE")).then(pl.lit("WRITE-IN")).otherwise("name"),
            party=pl.when(~pl.col("name").str.contains("WRITE")).then(
                pl.col("candidate_party_name").str.to_uppercase().replace({"": None})
            ),
            writein=pl.col("name").str.contains("WRITE"),
            measure=pl.lit(1, pl.Int16),
            votes="votes",
        )
        .group_by(
            *contest,
            "magnitude",
            "kind",
            "code",
            "county",
            "candidate",
            "party",
            "writein",
            "measure",
            maintain_order=True,
        )
        .agg(pl.col("votes").sum())
    )
    races = returns.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(134),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(races.select(*contest[:4], "counties", pl.col("present").list.len()))
    print(
        returns.group_by("year", "title", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "votes")
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=SITE,
                license="Public record",
            )
        for year, office in OFFICES:
            cs.artifact(raw.cached(SOURCE, url(year, office)), source)
        return {"VA": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Virginia's 2001 and 2005 lieutenant governor and attorney general generals by locality",
        cite=cite,
        method=METHOD,
        close="The leading candidates were within half a percent in these totals, so no outcome is recorded from them.",
        absent="The source has no rows for {counties} in this race, so those votes are missing from these totals.",
    )
