"""North Dakota, November 3, 2026 general election: the certified county results from the Secretary of State's results site.

The site's API serves every contest of an election in one call, with the votes of each choice by county, and marks the
election official once the State Canvassing Board has certified it. This module builds one package that attaches a
certified result set to each statewide, congressional, and legislative contest the archive already holds, and to the
contests of the legislative districts' county parts. It adds a contest, options, and candidacies only where the
archive holds none.
"""

import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from uuid import uuid5

import duckdb

from ea import package, raw
from ea.package import NAMESPACE, Package
from ea.store import DATA

SOURCE = "us.nd_general_2026"
KEY = "us.nd_general_2026/general-2026"
DAY = date(2026, 11, 3)
BASE = "https://api.resultsnd.sos.nd.gov/"
CLIENT = "cId=north-dakota&eId=348"
ENDPOINTS = {
    "info": f"{BASE}Election/GetElectionInfo?{CLIENT}",
    "list": f"{BASE}Contest/GetContestSearchList?{CLIENT}",
    "results": f"{BASE}Contest/GetContestResults?{CLIENT}",
    "turnout": f"{BASE}Turnout/GetTurnout?{CLIENT}",
}
PAGE = "https://resultsnd.sos.nd.gov/#election=348"
REFERENCE = DATA / "reference" / "us"

ND = "01a13a9c-7915-7638-aa6d-42893e7e19b9"
ELECTION = "cca1930f-cb51-5fa2-baac-28efec23b54e"
REPUBLICAN = "01a13afb-20af-70c4-bd13-85c13fd28fab"
DEMOCRATIC = "01a13afb-20af-70c4-bd13-85bcf33685ce"
PARTIES = {"REP": REPUBLICAN, "DEM": DEMOCRATIC}
SUPERINTENDENT = "c4be8515-2d18-5aec-8df0-8525dae1cca1"

STATEWIDE = {
    "Representative in Congress": "2026 U.S. House election in North Dakota's at-large congressional district",
    "Secretary of State": "2026 Secretary of State election",
    "Attorney General": "2026 Attorney General election",
    "Agriculture Commissioner": "2026 Commissioner of Agriculture election",
    "Tax Commissioner": "2026 Tax Commissioner election",
    "Public Service Commissioner": "2026 North Dakota Public Service Commissioner election",
    "Public Service Commissioner Unexpired 2-Year Term": "2026 North Dakota Public Service Commissioner, unexpired term election",
    "Justice of the Supreme Court": "2026 ND Justice of the Supreme Court, Full term election",
    "Justice of the Supreme Court Unexpired 8-Year Term": "2026 ND Justice of the Supreme Court, Unexpired term election",
}
MEASURES = {
    re.compile(r"^Constitutional Measure No\. 1\b"): "Constitutional Measure No. 1",
    re.compile(r"^Initiated Constitutional Measure No\. 2\b"): "Initiated Constitutional Measure No. 2",
}
SUPERINTENDENT_TITLE = "Superintendent of Public Instruction Unexpired 2-Year Term"
LEGISLATIVE = re.compile(r"^State (Senator|Representative)( Unexpired 2-Year Term)? District (\d+)$")
REGISTERED, BALLOTS_CAST = 2, 3


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for url in ENDPOINTS.values():
        raw.fetch(SOURCE, url, suffix=".json")


def _load(name: str) -> dict:
    return json.loads(raw.cached(SOURCE, ENDPOINTS[name]).path.read_text())


def _words(name: str) -> str:
    text = re.sub(r"[^a-z ]", " ", name.lower().replace("-", " "))
    return " ".join(word for word in text.split() if word not in {"jr", "sr", "ii", "iii"})


def _same(a: str, b: str) -> bool:
    x, y = _words(a).split(), _words(b).split()
    return bool(x and y) and x[0] == y[0] and x[-1] == y[-1]


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


class Reference:
    """The rows production already holds for this election, read from the reference partition."""

    def __init__(self) -> None:
        con = duckdb.connect()

        def table(name: str) -> str:
            return f"read_parquet('{REFERENCE / name}.parquet')"

        self.contests = {
            row[1]: row
            for row in con.sql(
                f"select id, name, district_area_id, kind, office_id, body_id, magnitude "
                f"from {table('contest')} where election_id = '{ELECTION}'"
            ).fetchall()
        }
        self.options: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
        for contest, option, kind, label, candidacy in con.sql(
            f"select o.contest_id, o.id, o.kind, coalesce(a.ballot_name, o.ballot_label), a.id "
            f"from {table('contest_option')} o join {table('contest')} c on c.id = o.contest_id "
            f"left join {table('candidacy')} a on a.option_id = o.id "
            f"where c.election_id = '{ELECTION}' order by o.id"
        ).fetchall():
            self.options[contest].append((option, label or "", candidacy))
        self.counties = {
            row[0]: (row[1], row[2])
            for row in con.sql(
                f"select code, id, area_id from {table('reporting_unit')} "
                f"where polity_id = '{ND}' and scheme_code = 'us.county'"
            ).fetchall()
        }


def _numbers(response: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    return response["response"]["contests"], _load("list")["response"]["contests"]


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def build() -> Package:
    results, listing = _numbers(_load("results"))
    info = _load("info")
    turnout = _load("turnout")["response"]
    assert info["isOfficial"] is True, "the election is not marked official"
    as_of = datetime.fromisoformat(info["lastUpdated"].split(".")[0] + "+00:00").astimezone(UTC)
    ref = Reference()
    by_name = {row[1]: row for row in ref.contests.values()}

    p = Package(
        KEY,
        SOURCE,
        "The certified county results of North Dakota's November 3, 2026 general election from the Secretary of State",
    )
    p.external.add(ND)
    p.external.add(ELECTION)
    p.external.add("us.county_part")
    source = key("source", "results")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="North Dakota Secretary of State",
        title="North Dakota election results, 2026 General Election",
        url=PAGE,
        license="Public record of the State of North Dakota",
        attribution="North Dakota Secretary of State",
    )
    for url in ENDPOINTS.values():
        p.artifact(raw.cached(SOURCE, url), source)

    # The 53 counties, by FIPS code, and the county parts that the legislative contests report.
    counties = {fips: unit for fips, (unit, _) in ref.counties.items()}
    for unit, _ in ref.counties.values():
        p.external.add(unit)
    county_area = {fips: area for fips, (_, area) in ref.counties.items()}
    parts: dict[tuple[str, str], str] = {}
    part_areas: dict[tuple[str, str], set[str]] = defaultdict(set)

    def part(fips: str, district: str, area: str) -> str:
        found = (fips, district)
        if found not in parts:
            parts[found] = key("unit", fips, district)
            part_areas[found] = set()
        part_areas[found].add(area)
        return parts[found]

    totals: dict[str, int] = {}
    declared_totals: dict[str, int] = {}
    missing_contests: list[str] = []
    left_out: dict[str, int] = defaultdict(int)
    n_sets = n_values = 0

    for contest_id, head in listing.items():
        title = _clean(head["contestName"])
        code = head["contestTypeCode"]
        data = results[contest_id]
        existing = None
        scope = "county"
        district = None
        if code == "SW":
            if title in STATEWIDE:
                existing = by_name[STATEWIDE[title]]
            elif title == SUPERINTENDENT_TITLE:
                existing = None
            else:
                for pattern, name in MEASURES.items():
                    if pattern.match(title):
                        existing = by_name[name]
                        break
                else:
                    left_out["statewide contests with no match"] += 1
                    missing_contests.append(title)
                    continue
        elif code == "LEG":
            found = LEGISLATIVE.match(title)
            assert found, title
            chamber, special, district = found.groups()
            scope = "part"
            number = int(district)
            if chamber == "Senator":
                name = f"2026 North Dakota Senate election, District {number}"
            elif special:
                name = f"2026 North Dakota House of Representatives special election, District {number}"
            else:
                name = f"2026 North Dakota House of Representatives election, District {number}"
            existing = by_name[name]
        else:
            left_out[
                {
                    "CW": "county-wide",
                    "CON": "soil conservation district",
                    "JUD": "district court judge",
                    "MUN": "city measure",
                    "SCH": "school district",
                }.get(code, code)
            ] += 1
            continue

        if existing is None:
            # The superintendent's unexpired term is the one statewide contest the archive lacks.
            contest = key("contest", contest_id)
            state_area = by_name["2026 Attorney General election"][2]
            p.external.update({SUPERINTENDENT, state_area})
            p.add(
                "contest",
                id=contest,
                election_id=ELECTION,
                kind="office",
                office_id=SUPERINTENDENT,
                district_area_id=state_area,
                magnitude=1,
                round_number=1,
                default_result_kind="counted",
            )
            p.name(
                contest,
                "2026 North Dakota Superintendent of Public Instruction, unexpired term election",
                "en",
                "official",
            )
            area = state_area
            old: list[tuple[str, str, str | None]] = []
            is_question = False
        else:
            contest = existing[0]
            p.external.add(contest)
            area = existing[2]
            old = ref.options[contest]
            is_question = existing[3] == "ballot_question"
            if area:
                p.external.add(area)

        # Options: attach to the archive's own where the candidate matches; add the rest.
        option_of: dict[str, str] = {}
        candidacy_of: dict[str, str | None] = {}
        taken: set[str] = set()
        for position, (choice_id, choice) in enumerate(sorted(head["choices"].items(), key=lambda i: i[1]["order"]), 1):
            name = _clean(choice["name"])
            write_in = "write-in" in name.lower()
            if is_question:
                match = next((o for o in old if o[1].lower() == name.lower() and o[0] not in taken), None)
            elif write_in:
                match = next((o for o in old if "write" in o[1].lower() and o[0] not in taken), None)
            else:
                match = next((o for o in old if _same(o[1], name) and o[0] not in taken), None)
            if match:
                taken.add(match[0])
                option_of[choice_id] = match[0]
                candidacy_of[choice_id] = match[2]
                p.external.add(match[0])
                continue
            option = option_of[choice_id] = key("option", contest_id, choice_id)
            candidacy_of[choice_id] = None
            if is_question:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=name,
                )
            elif write_in:
                p.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-in")
            else:
                party = PARTIES.get(choice["partyID"])
                if party:
                    p.external.add(party)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=name,
                )
                person = key("person", contest_id, choice_id)
                candidacy = candidacy_of[choice_id] = key("candidacy", contest_id, choice_id)
                p.add("person", id=person)
                p.name(person, name, "en", "official")
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party,
                    is_independent=choice["partyID"] == "IND",
                )

        result_set = key("result_set", contest_id)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=contest,
            stage="certified",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
            as_of=as_of,
        )
        n_sets += 1
        for choice in data["choices"]:
            choice_id = choice["choiceID"]
            declared_totals[f"{contest_id}:{choice_id}"] = choice["totalVotes"]
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option_of[choice_id],
                measure=1,
                value=choice["totalVotes"],
            )
            if choice["isWinner"] and not is_question:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option_of[choice_id],
                    kind="elected",
                )
            elif choice["isWinner"]:
                p.add("outcome", result_set_id=result_set, option_id=option_of[choice_id], kind="won")
        for fips, loc in sorted(data["locations"].items()):
            if fips not in counties:
                raise ValueError(f"{title}: unknown county {fips}")
            if scope == "part":
                assert district is not None and area
                unit = part(fips, district, area)
            else:
                unit = counties[fips]
            for choice in loc["choices"]:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option_of[choice["choiceID"]],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=choice["totalVotes"],
                )
                n_values += 1
                totals[f"{contest_id}:{choice['choiceID']}"] = (
                    totals.get(f"{contest_id}:{choice['choiceID']}", 0) + choice["totalVotes"]
                )
            if scope == "county" and head["voteFor"] == 1 and not is_question:
                place = turnout["locations"].get(fips)
                if place:
                    for measure, value in (
                        (REGISTERED, place["registeredVoters"]),
                        (BALLOTS_CAST, place["totalBallotsCast"]),
                    ):
                        p.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                        n_values += 1

    # County parts of legislative districts.
    for (fips, district), unit in sorted(parts.items()):
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ND,
            scheme_code="us.county_part",
            code=f"{DAY:%Y-%m-%d}:{fips}:{district}",
            parent_id=counties[fips],
            area_relation="within",
            area_id=county_area[fips],
            kind="standard",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
        p.external.add(county_area[fips])
        for area in sorted(part_areas[(fips, district)]):
            p.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=area,
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
                partial=True,
                method="official_list",
            )

    mismatched = [k for k, v in declared_totals.items() if totals.get(k) != v]
    if mismatched:
        raise ValueError(
            f"{len(mismatched)} option totals differ from the sum of their county values: {mismatched[:5]}"
        )
    print(
        f"{n_sets} result sets, {n_values} values, {len(parts)} county parts; left out {dict(left_out)}; "
        f"unmatched {missing_contests}"
    )
    return p
