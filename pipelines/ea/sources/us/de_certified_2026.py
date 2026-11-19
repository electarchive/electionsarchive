"""Delaware's November 3, 2026 general election as the Department of Elections certified it.

The Department's official-results files give every contest by county and by election district. The contests,
options, and county units already exist; the package attaches a certified result set at county grain (the default)
and one at election-district grain to each, and mints the 2026 election-district reporting units from the
2024 ones.
"""

import json
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, NamedTuple
from uuid import uuid5

import duckdb
import httpx

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "us.de_certified_2026"
ID = "GE2026"
HOSTS = ("https://elections.delaware.gov/reports/json/", "https://elections.delaware.gov/results/enr/")
FILES = {
    "statewide": f"Election_StatewideResults_ID_{ID}.json",
    "county": f"Election_ByCountyWithWilmington_ID_{ID}.json",
    "district": f"Election_ByRepresentativeDistrict_ID_{ID}.json",
    "precinct": f"Election_ByElectionDistrict_ID_{ID}.json",
}
PUBLISHER = "Delaware Department of Elections"
TITLE = "2026 General Election official results"
SITE = "https://elections.delaware.gov/results/enr/"
DAY = "2026-11-03"
REFERENCE = DATA / "reference" / "us"
COUNTIES = {"New Castle": "10003", "Kent": "10001", "Sussex": "10005"}
CHANNELS = (("Machine Votes", 1), ("Early Voting Votes", 2), ("Absentee Votes", 3))
SEAT = re.compile(r"State (Senator|Representative) District (\d+)")
COUNTY_OFFICE = re.compile(r"(New Castle|Kent|Sussex) County (.+)")
UNITS = f"{SOURCE}/units"
METHOD = (
    "County totals from the Department of Elections' official results by county, which it marks OFFICIAL RESULTS "
    "with all 530 election districts reporting (report time November 10, 2026). A contest appears only in the "
    "counties whose voters cast ballots in it. The Department's files mark no winner; the winner is the candidate "
    "with the most votes, and every figure is the Department's. Write-in votes are not in these files. The "
    "Department counts the city of Wilmington inside New Castle County; this set holds the county totals."
)
PRECINCT_METHOD = (
    "Votes by election district from the Department of Elections' official results by election district (marked "
    "OFFICIAL RESULTS, November 10, 2026), split by how the ballots were cast: machine (election day), early "
    "voting, and absentee. The district rows sum to the county totals of the default result set."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fetch() -> None:
    for name in FILES.values():
        for host in HOSTS:
            try:
                raw.fetch(SOURCE, host + name, suffix=".json")
                break
            except httpx.HTTPStatusError as failure:
                print(f"{host + name}: {failure.response.status_code}", flush=True)
        else:
            raise LookupError(f"no host serves {name}")


def artifact(name: str) -> raw.Artifact:
    for host in HOSTS:
        if host + name in raw.keys(SOURCE):
            return raw.cached(SOURCE, host + name)
    raise LookupError(f"{name} is not in the raw cache")


def load(which: str) -> list[dict[str, Any]]:
    items = json.loads(artifact(FILES[which]).path.read_text())
    for item in items:
        if item["Results Type"] != "OFFICIAL RESULTS" or item["Election Id"] != ID:
            raise ValueError(f"{FILES[which]}: {item['Election Id']} {item['Results Type']} is not official")
        item["Contest Title"] = item["Contest Title"].strip()
        item["Candidate Name"] = " ".join(item["Candidate Name"].split())
    return items


def number(text: Any) -> int:
    return int(str(text).replace(",", "") or 0)


class Reference(NamedTuple):
    polity: str
    counties: dict[str, tuple[str, str]]  # county name -> unit id, area id
    districts: dict[str, tuple[str, str]]  # election district -> 2024 unit's area id, county name
    contests: dict[str, tuple[str, str, str, dict[str, str]]]  # name -> contest id, election id, election name, options


def reference() -> Reference:
    con = duckdb.connect()
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in ("polity", "reporting_unit", "contest", "contest_option", "election")
    }
    (polity,) = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-DE'").fetchone() or (None,)
    if polity is None:
        raise LookupError("the reference holds no US-DE polity")
    counties: dict[str, tuple[str, str]] = {}
    for county, fips in COUNTIES.items():
        row = con.execute(
            f"""select id, area_id from '{path["reporting_unit"]}'
                where scheme_code = 'us.county' and polity_id = ? and code = ?""",
            [polity, fips],
        ).fetchone()
        if row is None:
            raise LookupError(f"the reference holds no unit for {county} County")
        counties[county] = (row[0], row[1])
    districts: dict[str, tuple[str, str]] = {}
    for code, area in con.execute(
        f"""select code, area_id from '{path["reporting_unit"]}'
            where polity_id = ? and pipeline = 'us.primaries' and scheme_code = 'us.precinct'""",
        [polity],
    ).fetchall():
        _, _, fips, county, district = code.split(":")
        districts[district] = (area, county.title())
        if COUNTIES[county.title()] != fips:
            raise ValueError(f"{code}: county code disagrees with its name")
    contests: dict[str, tuple[str, str, str, dict[str, str]]] = {}
    for contest, election, election_name, name, option, label in con.execute(
        f"""select c.id, c.election_id, e.name, c.name, o.id, o.ballot_label
            from '{path["contest"]}' c
            join '{path["election"]}' e on e.id = c.election_id
            join '{path["contest_option"]}' o on o.contest_id = c.id
            where e.date_from = date '{DAY}' and o.kind = 'candidate'
              and (e.parent_id in (select id from '{path["election"]}' where polity_id = ? and date_from = date '{DAY}'
                    and kind = 'general') or (e.polity_id = ? and e.kind = 'general'))""",
        [polity, polity],
    ).fetchall():
        entry = contests.setdefault(name, (contest, election, election_name, {}))
        if entry[0] != contest:
            raise ValueError(f"two contests are named {name}")
        entry[3][norm(label)] = option
    return Reference(polity, counties, districts, contests)


def contest_name(title: str) -> str:
    seat = SEAT.fullmatch(title)
    office = COUNTY_OFFICE.fullmatch(title)
    if title == "U.S. Senator":
        return "2026 U.S. Senate election in Delaware"
    if title == "Representative In Congress":
        return "2026 U.S. House election in Delaware's at-large congressional district"
    if title == "Attorney General":
        return "2026 Attorney General election"
    if title == "Auditor of Accounts":
        return "2026 Auditor election"
    if title == "State Treasurer":
        return "2026 Treasurer election"
    if seat:
        body = "Senate" if seat[1] == "Senator" else "House of Representatives"
        return f"2026 Delaware {body} election, District {seat[2]}"
    if office:
        county, rest = office[1], office[2]
        if council := re.fullmatch(r"Council District (\d+)", rest):
            return f"2026 {county} County Board of Commissioners election, District {council[1]}"
        if rest == "Levy Court At-Large":
            return f"2026 {county} County Levy Court at-large election"
        return f"2026 {county} County {rest} election"
    raise LookupError(f"no reference contest for {title}")


def source_id() -> str:
    return key("source", "results")


def election_district(name: str) -> str:
    return name.removeprefix("Election District ")


def district_unit(code: str) -> str:
    return key("unit", code)


def unit_code(county: str, district: str) -> str:
    return f"{DAY}:DE:{COUNTIES[county]}:{county.upper()}:{district}"


def units(ref: Reference) -> Package:
    p = Package(
        UNITS,
        SOURCE,
        "Delaware's election districts for November 3, 2026, with the Department of Elections as the source of the "
        "election's results",
    )
    src = source_id()
    p.add(
        "source",
        id=src,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=SITE,
        license=None,
        attribution=PUBLISHER,
    )
    for name in FILES.values():
        p.artifact(artifact(name), src)
    p.external |= {ref.polity, "us.precinct"}
    seen = {election_district(item["Election District"]) for item in load("precinct")}
    if seen != set(ref.districts):
        raise ValueError(f"election districts differ from the 2024 ones: {sorted(seen ^ set(ref.districts))}")
    for district in sorted(seen):
        area, county = ref.districts[district]
        parent, _ = ref.counties[county]
        p.external |= {parent, area}
        p.add(
            "reporting_unit",
            id=district_unit(unit_code(county, district)),
            polity_id=ref.polity,
            scheme_code="us.precinct",
            code=unit_code(county, district),
            parent_id=parent,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=date(2026, 11, 3),
            valid_to=date(2026, 11, 4),
        )
    return p


def value(p: Package, result_set: str, unit: str, option: str | None, channel: int, amount: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=channel,
        measure=1,
        count_round=0,
        flags=0,
        value=amount,
    )


def build() -> list[Package]:
    ref = reference()
    statewide = load("statewide")
    counties = load("county")
    by_district = load("district")
    precincts = load("precinct")
    stamp = datetime.fromisoformat(statewide[0]["ReportTime"])

    totals: dict[str, dict[str, int]] = defaultdict(dict)
    order: dict[str, int] = {}
    for item in statewide:
        if item["Precincts Reported"] != item["Total Precincts"]:
            raise ValueError(f"{item['Contest Title']}: {item['Precincts Reported']} of {item['Total Precincts']}")
        totals[item["Contest Title"]][item["Candidate Name"]] = number(item["Total Votes"])
        order[item["Contest Title"]] = item["Contest Sorting Order"]

    county_votes: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for item in counties:
        title, name = item["Contest Title"], item["Candidate Name"]
        for county in COUNTIES:
            county_votes[title][name][county] = number(item[county])
        if item["Wilmington"] + item["Rest of New Castle"] != item["New Castle"]:
            raise ValueError(f"{title} {name}: Wilmington and the rest of New Castle don't sum to the county")
        if sum(county_votes[title][name].values()) != item["State"] or item["State"] != totals[title][name]:
            raise ValueError(f"{title} {name}: counties don't sum to the statewide total")

    # Election-district lines: contest -> candidate -> district -> channel -> votes.
    grid: dict[str, dict[str, dict[str, dict[int, int]]]] = defaultdict(lambda: defaultdict(dict))
    for item in precincts:
        split = {channel: number(item[label]) for label, channel in CHANNELS}
        if sum(split.values()) != number(item["Total Votes"]):
            raise ValueError(f"{item['Election District']} {item['Contest Title']}: channels don't sum")
        district = election_district(item["Election District"])
        if district in grid[item["Contest Title"]][item["Candidate Name"]]:
            raise ValueError(f"{item['Election District']} {item['Contest Title']} {item['Candidate Name']} twice")
        grid[item["Contest Title"]][item["Candidate Name"]][district] = split

    for title, names in totals.items():
        for name in names:
            by_county: dict[str, int] = defaultdict(int)
            for district, split in grid[title][name].items():
                by_county[ref.districts[district][1]] += sum(split.values())
            for county in COUNTIES:
                if by_county[county] != county_votes[title][name][county]:
                    raise ValueError(f"{title} {name}: election districts disagree with {county}")
    rd_sum: dict[tuple[str, str, str], int] = defaultdict(int)
    for title, names in grid.items():
        for name, districts in names.items():
            for district, split in districts.items():
                rd_sum[(title, name, district.split("-")[0])] += sum(split.values())
    for item in by_district:
        district = item["Representative District"].removeprefix("Representative District ")
        if rd_sum[(item["Contest Title"], item["Candidate Name"], district)] != number(item["Total Votes"]):
            raise ValueError(f"{item['Contest Title']} {item['Candidate Name']}: district {district} disagrees")

    src = source_id()
    built = [units(ref)]
    grouped: dict[str, list[str]] = defaultdict(list)
    names_of: dict[str, str] = {}
    for title in sorted(totals, key=lambda t: order[t]):
        reference_name = contest_name(title)
        if reference_name not in ref.contests:
            raise LookupError(f"{title}: the reference has no contest named {reference_name}")
        election = ref.contests[reference_name][1]
        grouped[election].append(title)
        names_of[election] = ref.contests[reference_name][2]

    for election, titles in sorted(grouped.items(), key=lambda pair: names_of[pair[0]]):
        name = names_of[election]
        p = Package(
            f"{SOURCE}/{slug(name)}",
            SOURCE,
            f"Certified results of the {name} on November 3, 2026, by county and by election district",
            depends=[UNITS],
        )
        p.external.add(election)
        for which in FILES.values():
            p.uses(artifact(which))
        for title in titles:
            contest, _, _, options = ref.contests[contest_name(title)]
            candidates = totals[title]
            ids: dict[str, str] = {}
            for candidate in candidates:
                found = options.get(norm(candidate))
                if found is None:
                    raise LookupError(f"{title}: no option for {candidate}")
                ids[candidate] = found
            if set(options.values()) != set(ids.values()):
                raise LookupError(f"{title}: the reference has options the results lack")
            p.external |= {contest, *ids.values()}
            covered = {
                county
                for names in grid[title].values()
                for district in names
                for county in [ref.districts[district][1]]
            }
            county_set = key("result_set", title, "county")
            district_set = key("result_set", title, "district")
            for result_set, default, note in (
                (county_set, True, METHOD),
                (district_set, False, PRECINCT_METHOD),
            ):
                p.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    source_id=src,
                    status="active",
                    is_default=default,
                    completeness=1.0,
                    as_of=stamp,
                )
                p.add(
                    "note",
                    id=key("note", result_set),
                    entity_id=result_set,
                    kind="methodology",
                    body_md=note,
                    lang="en",
                )
            for county in sorted(covered):
                unit, _ = ref.counties[county]
                p.external.add(unit)
                for candidate in candidates:
                    value(p, county_set, unit, ids[candidate], 0, county_votes[title][candidate][county])
            for county in COUNTIES:
                if county not in covered and any(county_votes[title][c][county] for c in candidates):
                    raise ValueError(f"{title}: votes in {county}, which no election district reports")
            for candidate, districts in grid[title].items():
                for district, split in sorted(districts.items()):
                    unit = district_unit(unit_code(ref.districts[district][1], district))
                    for channel, amount in split.items():
                        value(p, district_set, unit, ids[candidate], channel, amount)
            for candidate, amount in candidates.items():
                p.add("declared", result_set_id=county_set, option_id=ids[candidate], measure=1, value=amount)
            top = max(candidates.values())
            winners = [candidate for candidate, amount in candidates.items() if amount == top]
            if len(winners) != 1:
                raise ValueError(f"{title}: {len(winners)} candidates tie for the most votes")
            for candidate in candidates:
                p.add(
                    "outcome",
                    result_set_id=county_set,
                    option_id=ids[candidate],
                    kind="elected" if candidate == winners[0] else "lost",
                )
        built.append(p)
    return built


def election_keys() -> list[str]:
    return [UNITS, *(p.key for p in build()[1:])]


def packages() -> list[str]:
    return election_keys()


def parse() -> None:
    fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
