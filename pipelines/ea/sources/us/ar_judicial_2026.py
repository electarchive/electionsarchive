"""Arkansas's 2026 nonpartisan judicial and prosecutor contests that went to a November 3 runoff, as the Secretary of
State's results site shows them once official.

Four contests were decided in a runoff: circuit judge in the 2nd judicial district (Division 4), the 4th (Division 2),
and the 16th (Division 2), and prosecuting attorney in the 11th West judicial district. The runoff contests, their
candidates, and the counties already exist, with election-night totals as the default. The `general` package attaches
a certified result set by county to each runoff and makes it the default. The `primary` package adds the March 3
first round of each contest, with the candidates the runoff lacks, as certified county results.

The results site marks both elections official. Its totals are the county totals the Secretary of State publishes.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid5

import duckdb

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "us.ar_judicial_2026"
PUBLISHER = "Arkansas Secretary of State"
SITE = "https://arkansas.tally-enr.com/"
FILES = "https://results.tally-enr.com/results/arkansas/{}/FullDataFile.json"
LISTS = "https://enr-results-api.totalresults.com/Contest/GetContestSearchList?cid=arkansas&electionID={}"
GENERAL = "11b07106-031e-4d7c-911a-d01fb473d6dd"
PRIMARY = "7f77a178-af02-40ec-92db-c5cc50882c68"
REFERENCE = DATA / "reference" / "us"
GENERAL_KEY = f"{SOURCE}/general-2026-11-03"
PRIMARY_KEY = f"{SOURCE}/primary-2026-03-03"
ELECTION = {"general": "e02d6494-fa8b-5df2-aed6-40e2949f9c90", "primary": "902f2b69-6f99-5949-89a7-5aba118fe0ed"}
POLITY = "01a13a9c-7914-7325-b24d-76c7ab7ec11b"

# The runoff contest in the archive, then the feed's contest in each election.
RACES = {
    "55e7d89f-52cc-57b7-a22f-a1ef68d67698": (
        "Circuit Judge, 2nd Judicial District, Division 4",
        "3e99ea0b-14a3-4bff-8f6e-588bdeaeb18b",
        "2528c7ed-375c-40f3-8787-0edfad67f291",
    ),
    "8341a3f0-76ae-51ae-9980-a0062aa39f5b": (
        "Circuit Judge, 4th Judicial District, Division 2",
        "0d9ca415-0339-4a96-ba23-37386ce5ae89",
        "4cd7f092-8121-4f9a-b950-9eec9ddf7724",
    ),
    "31cb6f0e-b0b7-5b3f-9b7e-ea3962d4839c": (
        "Circuit Judge, 16th Judicial District, Division 2",
        "12477bcb-53bf-405c-ab79-a826dfd1d15d",
        "5bcb9136-ac02-43e8-81a0-4817300c73d7",
    ),
    "8e29ba9c-afb6-5ba6-a65c-1be19b79fcf7": (
        "Prosecuting Attorney, 11th West Judicial District",
        "8bceaa73-e594-4579-add5-5f07a4770699",
        "480f4cd5-81f4-43b9-8cd8-a0828b13f6a1",
    ),
}
METHOD = {
    "general": (
        "County totals from the Secretary of State's results site, which marks the November 3, 2026 general election "
        "and nonpartisan judicial runoff official, with every precinct reporting. Each figure is the site's. The site "
        "flags no winner and reports no recount, so the candidate with more votes is recorded as elected. The site's "
        "contest total for each candidate is stored as the declared figure."
    ),
    "primary": (
        "County totals from the Secretary of State's results site, which marks the March 3, 2026 preferential primary "
        "and nonpartisan judicial general election official, with every precinct reporting. No candidate won "
        "outright, and the contest went to the November 3 runoff. The site's contest total "
        "for each candidate is stored as the declared figure."
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for election in (GENERAL, PRIMARY):
        for url in (LISTS.format(election), FILES.format(election)):
            raw.fetch(SOURCE, url, suffix=".json")


class Feed:
    def __init__(self, election: str) -> None:
        self.listing = raw.cached(SOURCE, LISTS.format(election))
        self.data = raw.cached(SOURCE, FILES.format(election))
        listing = json.loads(self.listing.path.read_text())
        if not listing["isOfficial"]:
            raise ValueError(f"{election}: the results are not official")
        self.stamp = datetime.fromisoformat(listing["lastUpdated"]).astimezone(UTC)
        self.names = {
            contest: {choice: info["name"] for choice, info in body["choices"].items()}
            for contest, body in listing["response"]["contests"].items()
        }
        self.titles = {contest: body["contestName"] for contest, body in listing["response"]["contests"].items()}
        full = json.loads(self.data.path.read_text())
        if not full["ElectionData"]["IsOfficial"]:
            raise ValueError(f"{election}: the data file is not official")
        self.contests: dict[str, dict[str, Any]] = {c["ContestID"]: c for c in full["ContestData"]}


class Held:
    """The rows production holds for the runoff contests."""

    def __init__(self) -> None:
        con = duckdb.connect()
        path = {
            name: str(REFERENCE / f"{name}.parquet")
            for name in ("contest", "contest_option", "candidacy", "reporting_unit")
        }
        ids = ", ".join(f"'{contest}'" for contest in RACES)
        self.contests = {
            row[0]: row[1:]
            for row in con.execute(
                f"select id, office_id, district_area_id, magnitude from '{path['contest']}' where id in ({ids})"
            ).fetchall()
        }
        if set(self.contests) != set(RACES):
            raise LookupError("the reference lacks a runoff contest")
        self.options: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
        for contest, option, label, person in con.execute(
            f"""select o.contest_id, o.id, o.ballot_label, c.person_id
                from '{path["contest_option"]}' o join '{path["candidacy"]}' c on c.option_id = o.id
                where o.contest_id in ({ids})"""
        ).fetchall():
            self.options[contest][label] = (option, person)
        self.units = {
            code: (unit, area)
            for code, unit, area in con.execute(
                f"""select code, id, area_id from '{path["reporting_unit"]}'
                    where scheme_code = 'us.county' and polity_id = '{POLITY}' and valid_from is null"""
            ).fetchall()
        }


def source(p: Package, feed: Feed, title: str) -> str:
    src = p.id("source", title)
    p.add(
        "source",
        id=src,
        kind="official_body",
        publisher=PUBLISHER,
        title=title,
        url=SITE,
        license=None,
        attribution=PUBLISHER,
    )
    p.artifact(feed.listing, src)
    p.artifact(feed.data, src)
    return src


def attach(
    p: Package,
    held: Held,
    feed: Feed,
    which: str,
    contest: str,
    contest_id: str,
    options: dict[str, str],
    src: str,
    name: str,
) -> None:
    """Add a certified result set by county for one contest, with its declared totals."""
    result_set = p.id("result_set", contest)
    election = ELECTION[which]
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=src,
        status="active",
        is_default=True,
        completeness=1.0,
        as_of=feed.stamp,
    )
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD[which],
        lang="en",
    )
    body = feed.contests[contest_id]
    if body["PrecinctsReporting"] != body["TotalPrecincts"]:
        raise ValueError(f"{name}: {body['PrecinctsReporting']} of {body['TotalPrecincts']} precincts")
    sums: dict[str, int] = defaultdict(int)
    for code, place in sorted(body["Locations"].items()):
        unit, area = held.units[code]
        p.external |= {unit, area}
        for choice in place["Choices"]:
            option = options[choice["ChoiceID"]]
            votes = int(choice["TotalVotes"])
            if "Votes" in choice and sum(int(kind["Votes"]) for kind in choice["Votes"]) != votes:
                raise ValueError(f"{name}: {code} vote types do not add up")
            p.add(
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
            sums[option] += votes
    winners = set()
    for choice in body["Choices"]:
        option = options[choice["ChoiceID"]]
        if int(choice["TotalVotes"]) != sums[option]:
            raise ValueError(f"{name}: {option} totals {choice['TotalVotes']}, not {sums[option]}")
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=int(choice["TotalVotes"]))
        if choice.get("IsWinner"):
            winners.add(option)
    if winners:
        raise ValueError(f"{name}: the feed flags a winner, which this module does not expect")
    ranked = sorted(sums.values(), reverse=True)
    if which == "general":
        if body["Recounts"] or ranked[0] == ranked[1]:
            raise ValueError(f"{name}: a recount or a tie")
        top = max(sums, key=lambda option: sums[option])
        for option in options.values():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if option == top else "lost",
            )


def general(held: Held) -> Package:
    feed = Feed(GENERAL)
    p = Package(
        GENERAL_KEY,
        SOURCE,
        "Certified county results of the four nonpartisan judicial and prosecutor runoffs on November 3, 2026",
    )
    src = source(p, feed, "Arkansas November 3, 2026 general election and nonpartisan judicial runoff results")
    p.external.add(ELECTION["general"])
    for runoff, (label, contest_id, _) in RACES.items():
        names = feed.names[contest_id]
        options: dict[str, str] = {}
        for choice, person in names.items():
            if person not in held.options[runoff]:
                raise LookupError(f"{label}: no option for {person}")
            options[choice] = held.options[runoff][person][0]
        if set(options.values()) != {option for option, _ in held.options[runoff].values()}:
            raise LookupError(f"{label}: the archive has options the results lack")
        p.external |= {runoff, *options.values()}
        attach(p, held, feed, "general", runoff, contest_id, options, src, label)
    return p


def primary(held: Held) -> Package:
    feed = Feed(PRIMARY)
    p = Package(
        PRIMARY_KEY,
        SOURCE,
        "Certified county results of the March 3, 2026 first round of the four judicial and prosecutor contests that "
        "went to a runoff",
    )
    src = source(
        p, feed, "Arkansas March 3, 2026 preferential primary and nonpartisan judicial general election results"
    )
    p.external |= {ELECTION["primary"], POLITY}
    for runoff, (label, _, contest_id) in RACES.items():
        office, area, magnitude = held.contests[runoff]
        p.external |= {office, area}
        contest = p.id("contest", runoff)
        p.add(
            "contest",
            id=contest,
            election_id=ELECTION["primary"],
            kind="office",
            office_id=office,
            district_area_id=area,
            magnitude=magnitude,
            term="full",
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"2026 AR {label} election", "en", "official")
        options: dict[str, str] = {}
        for position, (choice, person_name) in enumerate(feed.names[contest_id].items(), start=1):
            option = options[choice] = p.id("option", contest, person_name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=person_name,
            )
            held_person = held.options[runoff].get(person_name)
            if held_person:
                person = held_person[1]
                p.external.add(person)
            else:
                person = p.id("person", person_name, runoff)
                p.add("person", id=person)
                p.name(person, person_name, "en", "official")
            p.add(
                "candidacy",
                id=p.id("candidacy", contest, person_name),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=person_name,
                is_independent=False,
            )
        attach(p, held, feed, "primary", contest, contest_id, options, src, label)
    return p


def build() -> list[Package]:
    held = Held()
    return [primary(held), general(held)]


def packages() -> list[str]:
    return [PRIMARY_KEY, GENERAL_KEY]


def parse() -> None:
    fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
