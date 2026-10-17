import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.sd_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://sdsos.gov/elections-voting/election-resources/election-history/"
PUBLISHER = "South Dakota Secretary of State"
TITLE = "Election history"
WIDE = {
    "2000/2000_general_election_official_returns_public_utilities_commission.aspx": (
        "PUBLIC UTILITIES COMMISSIONER",
        False,
    ),
    "2002/2002_attorney_general_official_returns.aspx": ("ATTORNEY GENERAL", False),
    "2002/2002_commissioner_school_public_lands_official _returns.aspx": (
        "COMMISSIONER OF SCHOOL AND PUBLIC LANDS",
        False,
    ),
    "2002/2002_public_utilities_commissioner_4_year_term.aspx": ("PUBLIC UTILITIES COMMISSIONER", True),
    "2002/2002_public_utilities_commissioner_6_year_term.aspx": ("PUBLIC UTILITIES COMMISSIONER", False),
    "2002/2002_secretary_of_state_official_returns.aspx": ("SECRETARY OF STATE", False),
    "2002/2002_state_auditor_official_returns.aspx": ("AUDITOR", False),
    "2002/2002_state_treasurer_official_returns.aspx": ("TREASURER", False),
    "2004/2004_general_election_official_returns_public_utilities_commission.aspx": (
        "PUBLIC UTILITIES COMMISSIONER",
        False,
    ),
    "2006/2006_general_election_official_returns_attorney_general.aspx": ("ATTORNEY GENERAL", False),
    "2006/2006_general_election_official_returns_auditor.aspx": ("AUDITOR", False),
    "2006/2006_general_election_official_returns_commissioner_school_public_lands.aspx": (
        "COMMISSIONER OF SCHOOL AND PUBLIC LANDS",
        False,
    ),
    "2006/2006_general_election_official_returns_public_utilities_commissioner.aspx": (
        "PUBLIC UTILITIES COMMISSIONER",
        False,
    ),
    "2006/2006_general_election_official_returns_secretary_of_state.aspx": ("SECRETARY OF STATE", False),
    "2006/2006_general_election_official_returns_treasurer.aspx": ("TREASURER", False),
}
LONG = {
    "2008/2008_general_election_statewide_races_county_totals.aspx": "2008/2008_general_election_statewide_races.aspx",
    "2010/2010_general_election_statewide_candidates_by_county.aspx": "2010/2010_general_election_statewide_candidates.aspx",
}
PARTIES = {
    "R": "REPUBLICAN",
    "REP": "REPUBLICAN",
    "D": "DEMOCRATIC",
    "DEM": "DEMOCRATIC",
    "L": "LIBERTARIAN",
    "LIB": "LIBERTARIAN",
    "CON": "CONSTITUTION",
    "I": "INDEPENDENT",
    "IND": "INDEPENDENT",
}
METHOD = (
    "County totals are the South Dakota Secretary of State's official returns from its election history pages. Each "
    "candidate's county votes add up to the statewide total the Secretary of State printed."
)
MISSING = "The Secretary of State's table has no row for {counties} in this race, so those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def url(path: str) -> str:
    return SITE + path.replace(" ", "%20")


def table(path: str) -> list[list[str]]:
    return [
        [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        for row in re.findall(
            r"<tr[^>]*>(.*?)</tr>", raw.cached(SOURCE, url(path)).path.read_text(errors="replace"), re.DOTALL
        )
    ]


def fetch() -> None:
    for path in [*WIDE, *LONG, *LONG.values()]:
        raw.fetch(SOURCE, url(path), suffix=".html")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["SD"]
    rows: list[tuple[str, bool, str, str, str | None, int, str]] = []
    unknown: set[str] = set()

    def county(name: str) -> str | None:
        code = fips.get(re.sub(r"[^a-z]", "", name.lower()))
        if code is None:
            unknown.add(name)
        return code

    for path, (office, special) in WIDE.items():
        cells = table(path)
        header = [
            re.fullmatch(r"(.+?)\s*-\s*(\w+)", cell) or re.fullmatch(r"(.+)()", cell) for cell in cells[0][1:] if cell
        ]
        candidates = [(found[1], PARTIES.get(found[2].upper())) for found in header if found is not None]
        if len(candidates) != len(cells[0][1:]):
            raise ValueError(f"{path}: header {cells[0]}")
        body = [row for row in cells[1:] if row and row[0] and not row[0].upper().startswith(("TOTAL", "PERCENT"))]
        (total,) = [row for row in cells if row and row[0].upper().startswith("TOTAL")]
        printed = [int(number) for number in re.findall(r"\d+", " ".join(total))]
        sums = [sum(int(row[index + 1] or 0) for row in body) for index in range(len(candidates))]
        if printed[1:] != sums or printed[0] != sum(sums):
            raise ValueError(f"{path}: columns sum to {sums}, printed {printed}")
        for row in body:
            code = county(row[0])
            for (name, party), votes in zip(candidates, row[1:], strict=False):
                if code is not None:
                    rows.append((path[:4], special, office, name, party, int(votes or 0), code))
    for path, totals in LONG.items():
        expected = {
            (race, candidate): int(votes) for race, _, candidate, votes, *_ in table(totals)[1:] if votes.isdigit()
        }
        sums: dict[tuple[str, str], int] = {}
        for race, name, party, candidate, votes, *_ in table(path)[1:]:
            sums[(race, candidate)] = sums.get((race, candidate), 0) + int(votes)
            code = county(name)
            if code is not None:
                rows.append((path[:4], False, race.upper(), candidate, PARTIES.get(party.upper()), int(votes), code))
        if sums != expected:
            raise ValueError(f"{path}: {sorted(set(sums.items()) ^ set(expected.items()))}")
    if unknown:
        raise ValueError(f"unknown counties: {sorted(unknown)}")
    named = []
    frame = pl.DataFrame(
        rows, schema=["year", "special", "office", "candidate", "party", "votes", "county"], orient="row"
    )
    for (office,) in frame.select("office").unique().iter_rows():
        for pattern, title in medsl_statewide.OFFICES:
            if re.fullmatch(pattern, office) and title in (
                medsl_statewide.EXECUTIVE | {"Commissioner of School and Public Lands", "Public Utilities Commissioner"}
            ) - {"Governor", "Lieutenant Governor"}:
                named.append((office, title))
                break
    year = pl.col("year").cast(pl.Int32)
    frame = frame.join(pl.DataFrame(named, schema=["office", "title"], orient="row"), on="office").select(
        year=year,
        day=pl.struct(year=year).map_elements(
            lambda row: date(row["year"], 11, 2 + (-date(row["year"], 11, 1).weekday()) % 7), return_dtype=pl.Date
        ),
        state=pl.lit("SD"),
        title="title",
        post=pl.lit(None, pl.String),
        special="special",
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase(),
        party="party",
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(len(set(fips.values()))),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "title", "special").agg(pl.col("votes").sum()).sort("year", "title").rows())


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
                url=SITE + "election-history-search.aspx",
                license="Public record",
            )
        for path in [*WIDE, *LONG, *LONG.values()]:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        return {"SD": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="South Dakota's statewide executive offices and public utilities commissioners 2000–2010 by county from "
        "the Secretary of State's official returns",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
    )
