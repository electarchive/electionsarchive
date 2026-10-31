import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.vi_archive"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://electionarchive.vivote.gov"
PUBLISHER = "Election System of the Virgin Islands"
TITLE = "Virgin Islands Elections Database, governor and lieutenant governor"
PARTIES = {"Democratic": "democratic-party", "Independent Citizens Movement": "VI:independent-citizens-movement"}
PSEUDO = {"Total Ballots Cast", "Total Votes Cast", "Registered Voters", "Blank Ballots", "Overvotes/Undervotes"}


def url() -> str:
    search = {
        "global": {"years": {"from": 2000, "to": 2026}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"id": 4}]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return f"{HOST}/api/download_search.csv?" + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def fetch() -> None:
    raw.fetch(SOURCE, url(), suffix=".csv")


def parse() -> None:
    everything = pl.read_csv(raw.cached(SOURCE, url()).path, infer_schema=False).filter(
        (pl.col("election_type") != "Primary") & (pl.col("office_id") == "4")
    )
    frame = everything.filter(pl.col("division_type") == "District")
    PARSED.mkdir(parents=True, exist_ok=True)
    everything.filter(pl.col("division_type") == "Precinct").with_columns(
        pl.col("votes").cast(pl.Int64), pl.col("election_date").str.slice(0, 10).str.to_date()
    ).group_by("election_date", "division_name", "candidate_name").agg(pl.col("votes").sum()).write_parquet(
        PARSED / "precincts.parquet"
    )
    (
        frame.with_columns(pl.col("votes").cast(pl.Int64), pl.col("election_date").str.slice(0, 10).str.to_date())
        .group_by("election_date", "election_type", "candidate_name", "candidate_party_name")
        .agg(pl.col("votes").sum())
        .sort("election_date", "votes", descending=[False, True])
        .write_parquet(PARSED / "candidates.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    precincts = pl.read_parquet(PARSED / "precincts.parquet").filter(pl.col("election_date").dt.year() < 2016)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virgin Islands governor general elections and runoffs 2010–2022 from the Elections System's archive",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=HOST,
            notes="Island totals (St. Croix and St. Thomas/St. John) summed to the territory; the archive's precinct rows agree with them.",
        )
        cs.artifact(raw.cached(SOURCE, url()), source)
        systems = {key: cs.key("us.electoral_system", key) for key in ("two_round",)}
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-VI' and u.scheme_code = 'us.state'"""
        ).fetchone()
        polity = cs.polity("US-VI")
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        office = cs.key("us.office", "governor:VI")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=polity, kind="governor")
            name(office, "Governor of the U.S. Virgin Islands")
        persons: dict[str, UUID] = {}
        generals: dict[int, UUID] = {}
        for (day, stage), rows in frame.group_by(["election_date", "election_type"], maintain_order=True):
            runoff = stage == "Runoff"
            election_key = f"{day}:VI"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind="runoff" if runoff else "general",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} U.S. Virgin Islands {'runoff' if runoff else 'general'} election")
                cs.add("slug", path=f"us/vi/{day}-{'runoff' if runoff else 'general'}", entity_id=election)
            contest_key = f"{day}:VI:governor"
            contest = cs.key("us.contest", contest_key)
            if runoff:
                previous = generals[day.year]
            else:
                generals[day.year] = contest
                previous = None
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=systems["two_round"],
                default_result_kind="counted",
            )
            name(contest, f"{day.year} U.S. Virgin Islands governor {'runoff' if runoff else 'election'}")
            cs.add("slug", path=f"us/vi/{day}-{'runoff' if runoff else 'general'}/governor", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            options: dict[str, UUID] = {}
            winner: UUID | None = None
            top = rows.filter(~pl.col("candidate_name").is_in([*PSEUDO, "Write-In Votes"]))["votes"].max()
            for candidate, party, votes in (
                rows.sort("votes", descending=True)
                .select("candidate_name", "candidate_party_name", "votes")
                .iter_rows()
            ):
                if candidate in PSEUDO:
                    continue
                if candidate == "Write-In Votes":
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    options[candidate] = option
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    head, _, mate = candidate.partition(" and ")
                    token = slug(candidate)
                    party_key = PARTIES.get(party)
                    party_id = None
                    if party_key is not None:
                        party_id = cs.key("us.party", party_key)
                        if party_key not in parties_known:
                            parties_known.add(party_key)
                            cs.add("party", id=party_id, kind="party", polity_id=polity)
                            name(party_id, party)
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    options[candidate] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=candidate,
                    )
                    for role, label in (("head", head), ("running_mate", mate)):
                        if not label:
                            continue
                        key = slug(label)
                        if key not in persons:
                            persons[key] = cs.key(f"{SOURCE}.person", f"VI:{key}")
                            if persons[key] in cs.minted:
                                cs.add("person", id=persons[key])
                                name(persons[key], label)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{token}:{role}"),
                            person_id=persons[key],
                            option_id=option,
                            role=role,
                            ballot_name=label if role == "head" else None,
                            is_independent=party_id is None,
                        )
                    if votes == top:
                        winner = option
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            if winner is not None:
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=None)
            by_precinct = precincts.filter(
                (pl.col("election_date") == day) & pl.col("candidate_name").is_in(list(options))
            )
            if by_precinct.height > 0:
                precinct_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:precinct")
                cs.add(
                    "result_set",
                    id=precinct_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=False,
                    completeness=1,
                )
                for precinct, candidate, votes in by_precinct.select(
                    "division_name", "candidate_name", "votes"
                ).iter_rows():
                    precinct_unit = cs.unit("us.precinct", f"{day.year}:VI:{precinct.lower()}")
                    if precinct_unit in cs.minted and not any(
                        r["id"] == precinct_unit for r in cs.rows.get("reporting_unit", [])
                    ):
                        cs.add(
                            "reporting_unit",
                            id=precinct_unit,
                            polity_id=polity,
                            scheme_code="us.precinct",
                            code=f"{day.year}:VI:{precinct.lower()}",
                            parent_id=unit,
                            area_relation="within",
                            area_id=area,
                            kind="standard",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                    cs.add(
                        "result_value",
                        result_set_id=precinct_set,
                        unit_id=precinct_unit,
                        option_id=options[candidate],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        return cs.write()
