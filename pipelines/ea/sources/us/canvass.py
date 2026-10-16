import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RHODE_ISLAND = "https://s3.amazonaws.com/rigov/election/results/2010/general_election/{town}.json"
RI_STATEWIDE = "https://s3.amazonaws.com/rigov/election/results/2010/general_election/statewide.json"
RI_PUBLISHER = "Rhode Island Board of Elections"
RI_TITLE = "2010 General Election results"
KENTUCKY = (
    "https://raw.githubusercontent.com/openelections/openelections-data-ky/"
    "7ac99e9fb6ceb8d32dc85e4f20ad12818b0fc430/2023/General/20231107__ky__general{joint}{county}__precinct.csv"
)
KY_PUBLISHER = "OpenElections"
KY_TITLE = "Kentucky 2023 general election results by precinct"
TOWNS = {
    "44001": ["Barrington", "Bristol", "Warren"],
    "44003": ["Coventry", "East Greenwich", "Warwick", "West Greenwich", "West Warwick"],
    "44005": ["Jamestown", "Little Compton", "Middletown", "Newport", "Portsmouth", "Tiverton"],
    "44007": [
        "Burrillville",
        "Central Falls",
        "Cranston",
        "Cumberland",
        "East Providence",
        "Foster",
        "Glocester",
        "Johnston",
        "Lincoln",
        "North Providence",
        "North Smithfield",
        "Pawtucket",
        "Providence",
        "Scituate",
        "Smithfield",
        "Woonsocket",
    ],
    "44009": [
        "Charlestown",
        "Exeter",
        "Hopkinton",
        "Narragansett",
        "New Shoreham",
        "North Kingstown",
        "Richmond",
        "South Kingstown",
        "Westerly",
    ],
}
OFFICES = {
    "GOVERNOR": ("Governor", "governor"),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", "lieutenant-governor"),
    "SECRETARY OF STATE": ("Secretary of State", "secretary-of-state"),
    "ATTORNEY GENERAL": ("Attorney General", "attorney-general"),
    "GENERAL TREASURER": ("General Treasurer", "treasurer"),
    "Governor": ("Governor", "governor"),
    "Secretary of State": ("Secretary of State", "secretary-of-state"),
    "Attorney General": ("Attorney General", "attorney-general"),
    "Auditor of Public Accounts": ("Auditor of Public Accounts", "auditor"),
    "State Treasurer": ("Treasurer", "treasurer"),
    "Commissioner of Agriculture": ("Commissioner of Agriculture", "commissioner-of-agriculture"),
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "MOD": "RI:moderate-party",
    "CM": "RI:cool-moose-party",
}
NAMED = {"RI:moderate-party": "Moderate Party of Rhode Island", "RI:cool-moose-party": "Cool Moose Party"}
MISSPELLED = {'Kimberly "Kim" REEDER': 'Kimberley "Kim" REEDER'}
MISSING = ["Leslie", "Owsley", "Rockcastle", "Wolfe"]
JOINED = {
    "breathitt",
    "clay",
    "clinton",
    "elliott",
    "floyd",
    "jackson",
    "jefferson",
    "johnson",
    "kenton",
    "knott",
    "laurel",
    "lee",
    "letcher",
    "madison",
    "magoffin",
    "martin",
    "menifee",
    "morgan",
    "powell",
}
CAVEATS = {
    "KY": (
        "Totals come from OpenElections' precinct files, which cover 116 of Kentucky's 120 counties (Leslie, Owsley, "
        "Rockcastle, and Wolfe are missing). These files count write-in votes in only some counties, so the totals are "
        "lower than certified results."
    ),
    "RI": (
        "Rhode Island's statewide totals run a few dozen votes above the sum of its town results because some ballots "
        "were reported only statewide. Those votes appear here as a statewide line with no county."
    ),
}


def _towns() -> list[str]:
    return [slug(town).replace("-", "_") for towns in TOWNS.values() for town in towns]


def _counties() -> list[str]:
    with connect() as conn:
        return [
            slug(text).replace("-", "")
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%' order by 1"""
            ).fetchall()
            if text not in MISSING
        ]


def fetch() -> None:
    raw.fetch(SOURCE, RI_STATEWIDE, suffix=".json")
    for town in _towns():
        raw.fetch(SOURCE, RHODE_ISLAND.format(town=town), suffix=".json")
    for county in _counties():
        raw.fetch(SOURCE, KENTUCKY.format(county=county, joint="_" if county in JOINED else "__"), suffix=".csv")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for fips, towns in TOWNS.items():
        for town in towns:
            data = json.loads(
                raw.cached(SOURCE, RHODE_ISLAND.format(town=slug(town).replace("-", "_"))).path.read_text()
            )
            for contest in data["contests"]:
                if contest["name"] not in OFFICES:
                    continue
                for candidate in contest["candidates"]:
                    rows.append(
                        {
                            "state": "RI",
                            "day": date(2010, 11, 2),
                            "office": contest["name"],
                            "candidate": candidate["name"],
                            "mate": None,
                            "party": candidate["party_code"],
                            "fips": fips,
                            "votes": int(candidate["votes"]),
                        }
                    )
    statewide = {
        (contest["name"], candidate["name"]): int(candidate["votes"])
        for contest in json.loads(raw.cached(SOURCE, RI_STATEWIDE).path.read_text())["contests"]
        if contest["name"] in OFFICES
        for candidate in contest["candidates"]
    }
    summed: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        summed[(str(row["office"]), str(row["candidate"]))] += int(str(row["votes"]))
    if set(summed) != set(statewide) or any(statewide[key] < votes for key, votes in summed.items()):
        raise ValueError("Rhode Island's towns add up to more than its statewide totals")
    for (office, candidate), votes in statewide.items():
        party = next(row["party"] for row in rows if row["office"] == office and row["candidate"] == candidate)
        rows.append(
            {
                "state": "RI",
                "day": date(2010, 11, 2),
                "office": office,
                "candidate": candidate,
                "mate": None,
                "party": party,
                "fips": "RI:statewide",
                "votes": votes - summed[(office, candidate)],
            }
        )
    with connect() as conn:
        fips = {
            slug(text).replace("-", ""): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%'"""
            ).fetchall()
        }
    kentucky = (
        pl.concat(
            [
                pl.read_csv(
                    raw.cached(SOURCE, KENTUCKY.format(county=county, joint="_" if county in JOINED else "__")).path,
                    infer_schema_length=0,
                )
                .select("office", "party", "candidate", "votes")
                .with_columns(fips=pl.lit(fips[county]))
                for county in _counties()
            ]
        )
        .filter(pl.col("office").is_in(list(OFFICES)), pl.col("candidate").is_not_null())
        .with_columns(candidate=pl.col("candidate").replace(MISSPELLED), votes=pl.col("votes").cast(pl.Int64))
        .filter(
            pl.col("party").is_not_null()
            | (pl.col("fips").n_unique().over("office", "candidate") >= 50)
            & ~pl.col("candidate").str.contains(r"(?i)write|total|votes|not assigned|\(W\)")
        )
        .group_by("office", "candidate", "fips", maintain_order=True)
        .agg(pl.col("party").drop_nulls().first(), pl.col("votes").sum())
        .with_columns(pl.col("party").drop_nulls().first().over("office", "candidate"))
        .with_columns(
            state=pl.lit("KY"),
            day=pl.lit(date(2023, 11, 7)),
            mate=pl.col("candidate").str.split(" / ").list.get(1, null_on_oob=True),
            candidate=pl.col("candidate").str.split(" / ").list.first(),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(
        [
            pl.DataFrame(rows, schema_overrides={"mate": pl.String}),
            kentucky.select("state", "day", "office", "candidate", "mate", "party", "fips", "votes"),
        ]
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2010 and Kentucky's 2023 statewide general elections by county",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            text = re.sub(r"\b([A-Z][A-Z'.-]+)\b", lambda match: match[1].title(), text)
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text)

        board = cs.source(RI_PUBLISHER, RI_TITLE)
        cs.add(
            "source",
            id=board,
            kind="official_body",
            publisher=RI_PUBLISHER,
            title=RI_TITLE,
            url="https://www.ri.gov/election/results/2010/general_election/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, RI_STATEWIDE), board)
        for town in _towns():
            cs.artifact(raw.cached(SOURCE, RHODE_ISLAND.format(town=town)), board)
        community = cs.source(KY_PUBLISHER, KY_TITLE)
        cs.add(
            "source",
            id=community,
            kind="community",
            publisher=KY_PUBLISHER,
            title=KY_TITLE,
            url="https://github.com/openelections/openelections-data-ky",
            license=None,
        )
        for county in _counties():
            cs.artifact(
                raw.cached(SOURCE, KENTUCKY.format(county=county, joint="_" if county in JOINED else "__")), community
            )
        sources = {"RI": board, "KY": community}
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code in ('US-RI', 'US-KY')"""
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                """select substr(p.iso_code, 4), u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code in ('US-RI', 'US-KY')
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        )
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and left(code, 2) in ('21', '44')"
            ).fetchall()
        )
        cs.add(
            "reporting_unit",
            id=cs.unit("us.aggregate", "RI:statewide"),
            polity_id=cs.polity("US-RI"),
            scheme_code="us.aggregate",
            code="RI:statewide",
            parent_id=cs.unit("us.state", "44"),
            area_relation="within",
            area_id=areas["RI"],
            kind="aggregate",
            valid_from=None,
            valid_to=None,
        )
        counties["RI:statewide"] = cs.unit("us.aggregate", "RI:statewide")
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        existing: dict[str, tuple[list[tuple[UUID, set[str]]], list[tuple[object, ...]]]] = {}
        for key, contest in conn.execute(
            """select e.value, e.entity_id from external_id e
               where e.scheme = 'us.contest' and (e.value like '2010-11-02:RI:%%' or e.value like '2023-11-07:KY:%%')"""
        ).fetchall():
            options = [
                (option, set(slug(label or "").split("-")))
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and parent_option_id is null",
                    [contest],
                ).fetchall()
            ]
            defaults = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            existing[key] = (options, defaults)
        people: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        for state, person, ballot in conn.execute(
            """select distinct substr(p.iso_code, 4), cd.person_id, cd.ballot_name from candidacy cd
               join contest_option o on o.id = cd.option_id join contest ct on ct.id = o.contest_id
               join election e on e.id = ct.election_id join polity p on p.id = e.polity_id
               where p.iso_code in ('US-RI', 'US-KY') and cd.ballot_name is not null"""
        ).fetchall():
            words = [
                word
                for word in slug(re.sub(r'"[^"]*"|\([^)]*\)', "", ballot)).split("-")
                if word not in ("jr", "sr", "ii", "iii") and len(word) > 1
            ]
            if len(words) > 1:
                people[(state, f"{words[0]} {words[-1]}")].add(person)
        fptp = cs.key("us.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("the fptp system must exist")
        persons: dict[tuple[str, str], UUID] = {}

        def person(state: str, text: str) -> UUID:
            if (state, text) in persons:
                return persons[(state, text)]
            words = [
                word
                for word in slug(re.sub(r'"[^"]*"|\([^)]*\)', "", text)).split("-")
                if word not in ("jr", "sr", "ii", "iii") and len(word) > 1
            ]
            found = people.get((state, f"{words[0]} {words[-1]}"), set())
            if len(found) == 1:
                persons[(state, text)] = next(iter(found))
            else:
                persons[(state, text)] = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                if persons[(state, text)] in cs.minted:
                    cs.add("person", id=persons[(state, text)])
                    name(persons[(state, text)], text)
            return persons[(state, text)]

        def party(label: str | None) -> UUID | None:
            key = PARTIES.get(label or "")
            if key is None:
                return None
            found = cs.key("us.party", key)
            if found in cs.minted and key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=found, kind="party", polity_id=cs.polity(f"US-{key.split(':')[0]}"))
                name(found, NAMED[key])
            return found

        for (state, day, office_text), lines in frame.group_by("state", "day", "office", maintain_order=True):
            state, office_text = str(state), str(office_text)
            assert isinstance(day, date)
            office, office_slug = OFFICES[office_text]
            election_key = f"{day}:{state}"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {states[state]} general election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-general", entity_id=election)
            office_key = f"{office_slug}:{state}"
            office_id = cs.key("us.office", office_key)
            if office_key not in offices_known:
                offices_known.add(office_key)
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="governor" if office_slug == "governor" else "other",
                )
                name(office_id, f"{office} of {states[state]}")
            contest_key = f"{day}:{state}:{office_slug}"
            contest = cs.key("us.contest", contest_key)
            options, defaults = existing.get(contest_key, ([], []))
            if contest_key not in existing:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=areas[state],
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} {states[state]} {office} election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-general/{office_slug}", entity_id=contest)
            for row in defaults:
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            row,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1 if state == "RI" else None,
            )
            cs.add("note", entity_id=contest, kind="caveat", body_md=CAVEATS[state])
            totals: dict[UUID, int] = {}
            for (candidate, mate, label), cells in lines.group_by("candidate", "mate", "party", maintain_order=True):
                ballot = title(str(candidate))
                party_id = party(label)
                surname = [word for word in slug(ballot).split("-") if word not in ("jr", "sr", "ii", "iii")][-1]
                option = next((option for option, words in options if surname in words), None)
                if option is None:
                    head = person(state, ballot)
                    option = cs.key("us.option", f"{contest_key}:{head}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=ballot,
                    )
                    for role, text in (("head", ballot), ("running_mate", title(str(mate)) if mate else None)):
                        if text:
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{contest_key}:{head}:{role}"),
                                person_id=person(state, text),
                                option_id=option,
                                role=role,
                                ballot_name=text,
                                is_independent=label == "IND" or label is None,
                            )
                totals[option] = totals.get(option, 0) + int(cells["votes"].sum())
                for fips, votes in cells.group_by("fips", maintain_order=True).agg(pl.col("votes").sum()).iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[str(fips)],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=32 if fips == "RI:statewide" else 0,
                        value=votes,
                    )
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
        return cs.write()
