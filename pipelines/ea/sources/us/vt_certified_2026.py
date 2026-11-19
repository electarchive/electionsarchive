"""Vermont's November 3, 2026 general election as the Secretary of State's results site shows it once certified.

The site marks the 2026 general election official and serves its results as JSON, one file each for the federal
race, the statewide offices, the state senate, the state house, and the county offices, with a row for every part
of a town that a house district splits. The federal, statewide, senate, and house contests and their candidates
already exist; the packages attach a certified result set at town-part grain (the default) to each, and add a
write-in option to each. The county offices have no contests yet, so the county package mints their offices,
contests, options, candidacies, and people. The town parts are new reporting units for the day, minted in their own
package.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, NamedTuple
from uuid import uuid5
from zoneinfo import ZoneInfo

import duckdb

from ea import package, raw
from ea.package import Package
from ea.sources.us.enr_vt import AGGREGATE, BASE
from ea.store import DATA

SOURCE = "us.vt_certified_2026"
GUID = "68d61e96-1d28-4210-8c1d-8e90512ae105"
PUBLISHER = "Vermont Secretary of State, Elections Division"
TITLE = "2026 General Election results"
SITE = "https://electionresults.vermont.gov/"
DAY = date(2026, 11, 3)
REFERENCE = DATA / "reference" / "us"
UNITS = f"{SOURCE}/units"
PARTS = {
    "federal": "f",
    "stateWide": "s",
    "senate": "sn",
    "house": "h",
    "county": "cty",
}
COUNTIES = {
    "ADDISON": "50001",
    "BENNINGTON": "50003",
    "CALEDONIA": "50005",
    "CHITTENDEN": "50007",
    "ESSEX": "50009",
    "FRANKLIN": "50011",
    "GRAND ISLE": "50013",
    "LAMOILLE": "50015",
    "ORANGE": "50017",
    "ORLEANS": "50019",
    "RUTLAND": "50021",
    "WASHINGTON": "50023",
    "WINDHAM": "50025",
    "WINDSOR": "50027",
}
STATEWIDE = {
    "REPRESENTATIVE TO CONGRESS": "2026 U.S. House election in Vermont's at-large congressional district",
    "GOVERNOR": "2026 Vermont gubernatorial election",
    "LIEUTENANT GOVERNOR": "2026 Lieutenant Governor election",
    "STATE TREASURER": "2026 Treasurer election",
    "SECRETARY OF STATE": "2026 Secretary of State election",
    "AUDITOR OF ACCOUNTS": "2026 Auditor election",
    "ATTORNEY GENERAL": "2026 Attorney General election",
}
COUNTY_OFFICES = {
    "PROBATE JUDGE": "Probate Judge",
    "ASSISTANT JUDGE": "Assistant Judge",
    "STATE'S ATTORNEY": "State's Attorney",
    "SHERIFF": "Sheriff",
    "HIGH BAILIFF": "High Bailiff",
}
ALIASES = {"essextown": "essex", "essexjunctioncity": "essexjunction"}
PARTY_ROOTS = {"DEM": "DEMOCRATIC", "REP": "REPUBLICAN", "PROG": "PROGRESSIVE"}
METHOD = (
    "Town-part totals from the Secretary of State's results site, which marks the 2026 general election official "
    "(last updated November 17, 2026, 6:58 p.m. Eastern). Each row is a town, or the part of a town that one state "
    "house district covers, and every figure is the Secretary of State's. Winners are as the site flags them. "
    "Write-in votes, named or not, are one option. The site's own statewide, district, or county total for the "
    "contest is stored as the declared figure."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fetch() -> None:
    listing = raw.fetch(SOURCE, f"{BASE}elections/elections.json", suffix=".json")
    entry = next(e for e in json.loads(listing.path.read_text()) if e["electionGuid"] == GUID)
    if not entry["isOfficial"]:
        raise LookupError("the 2026 general election is not marked official yet")
    election = raw.fetch(SOURCE, f"{BASE}elections/{GUID}.json", suffix=".json")
    details = json.loads(election.path.read_text())
    for part in PARTS:
        raw.fetch(SOURCE, BASE + details[part]["path"].replace("\\", "/"), suffix=".json")


class Feed(NamedTuple):
    artifacts: list[raw.Artifact]
    stamp: datetime
    files: dict[str, dict[str, Any]]


def feed() -> Feed:
    election = raw.cached(SOURCE, f"{BASE}elections/{GUID}.json")
    details = json.loads(election.path.read_text())
    if not details["electionDetails"]["isOfficial"] or details["townsReporting"] != "247/247":
        raise ValueError("the election file is not official and complete")
    stamp = datetime.strptime(details["lastUpdatedDate"], "%m/%d/%Y %I:%M %p").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    artifacts = [raw.cached(SOURCE, f"{BASE}elections/elections.json"), election]
    files: dict[str, dict[str, Any]] = {}
    for part, short in PARTS.items():
        artifact = raw.cached(SOURCE, BASE + details[part]["path"].replace("\\", "/"))
        artifacts.append(artifact)
        files[short] = json.loads(artifact.path.read_text())["d"][0]
    return Feed(artifacts, stamp, files)


@dataclass
class Row:
    town: str
    district: str
    senate: str
    county: str
    votes: dict[int, int]
    write_ins: int


@dataclass
class Race:
    name: str
    label: str
    seats: int
    county: str | None
    candidates: dict[int, tuple[str, str]]  # feed id -> name, party
    winners: set[int]
    rows: list[Row] = field(default_factory=list)
    total: dict[int, int] = field(default_factory=dict)
    total_write_ins: int = 0


def races(files: dict[str, dict[str, Any]]) -> list[Race]:
    towns: dict[str, str] = {}
    for office in files["cty"]["co"]:
        for part in office["cs"]:
            if part["tn"].upper() not in AGGREGATE:
                towns.setdefault(part["tn"].upper(), part["ctyn"].upper())
    out: list[Race] = []
    for short, file in files.items():
        for office in file.get("o") or file["co"]:
            name = office["on"].strip()
            label = (office.get("dn") or "").rstrip("_")
            county = office.get("ctyn") if short == "cty" else None
            race = Race(name, label, int(office["vf"]), county, {}, set())
            for part in office["cs"]:
                tn = part["tn"].upper()
                for choice in part["rc"]:
                    race.candidates[choice["cid"]] = (
                        " ".join(choice["cn"].split()),
                        (choice.get("pn") or "").strip(),
                    )
                    if choice["isWinner"]:
                        race.winners.add(choice["cid"])
                write_ins = sum(int(choice["vc"] or 0) for choice in part["wc"])
                votes = {choice["cid"]: int(choice["vc"] or 0) for choice in part["rc"]}
                if tn in AGGREGATE:
                    race.total = votes
                    race.total_write_ins = write_ins
                    continue
                if tn not in towns:
                    raise LookupError(f"{tn}: no county")
                race.rows.append(Row(tn, part["repd"], part["send"].rstrip("_"), towns[tn], votes, write_ins))
            seen = {(row.town, row.district, row.senate) for row in race.rows}
            if len(seen) != len(race.rows):
                raise ValueError(f"{name} {label}: a town part appears twice")
            if not race.total:
                raise ValueError(f"{name} {label}: the site gives no total")
            out.append(race)
    return out


class Reference(NamedTuple):
    polity: str
    election: str
    counties: dict[str, tuple[str, str]]  # feed county -> unit id, area id
    towns: dict[str, str]  # normalized name -> area id
    contests: dict[str, tuple[str, dict[str, str]]]  # name -> contest id, options by normalized label


def reference() -> Reference:
    con = duckdb.connect()
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in ("polity", "reporting_unit", "area", "contest", "contest_option", "election")
    }
    row = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-VT'").fetchone()
    if row is None:
        raise LookupError("the reference holds no US-VT polity")
    polity = row[0]
    found = con.execute(
        f"""select id from '{path["election"]}' where polity_id = ? and date_from = date '{DAY}' and kind = 'general'""",
        [polity],
    ).fetchall()
    if len(found) != 1:
        raise LookupError(f"{len(found)} Vermont general elections on {DAY}")
    election = found[0][0]
    counties: dict[str, tuple[str, str]] = {}
    for county, fips in COUNTIES.items():
        unit = con.execute(
            f"""select id, area_id from '{path["reporting_unit"]}'
                where scheme_code = 'us.county' and polity_id = ? and code = ?""",
            [polity, fips],
        ).fetchone()
        if unit is None:
            raise LookupError(f"the reference holds no unit for {county} County")
        counties[county] = (unit[0], unit[1])
    towns: dict[str, str] = {}
    for area, name in con.execute(
        f"""select id, name from '{path["area"]}'
            where name like '%, VT' and (scheme_code = 'us.town' or id = '12baa2a4-7b09-534e-8004-82a4d93da917')"""
    ).fetchall():
        towns[norm(name.removesuffix(", VT"))] = area
    contests: dict[str, tuple[str, dict[str, str]]] = {}
    for contest, name, option, label in con.execute(
        f"""select c.id, c.name, o.id, o.ballot_label
            from '{path["contest"]}' c join '{path["contest_option"]}' o on o.contest_id = c.id
            where c.election_id = ? and o.kind = 'candidate'""",
        [election],
    ).fetchall():
        entry = contests.setdefault(name, (contest, {}))
        entry[1][norm(label)] = option
    return Reference(polity, election, counties, towns, contests)


def party_ids(ref: Reference, all_races: list[Race]) -> dict[str, str | None]:
    """Each feed party's id, taken from the reference's own options so a party keeps the id production holds."""
    con = duckdb.connect()
    path = {name: str(REFERENCE / f"{name}.parquet") for name in ("contest", "contest_option")}
    held: dict[tuple[str, str], str | None] = {}
    for contest, label, party in con.execute(
        f"""select c.id, o.ballot_label, o.party_id from '{path["contest"]}' c
            join '{path["contest_option"]}' o on o.contest_id = c.id
            where c.election_id = ? and o.kind = 'candidate'""",
        [ref.election],
    ).fetchall():
        held[(contest, norm(label))] = party
    seen: dict[str, set[str | None]] = defaultdict(set)
    for race in all_races:
        contest = matched(ref, race)[0]
        for name, party in race.candidates.values():
            if (contest, norm(name)) in held:
                seen[party].add(held[(contest, norm(name))])
    out: dict[str, str | None] = {}
    for party, ids in seen.items():
        if len(ids) != 1:
            raise ValueError(f"{party}: {len(ids)} party ids in the reference")
        out[party] = next(iter(ids))
    out[""] = None
    out["INDEPENDENT"] = None
    for party in {party for race in all_races for _, party in race.candidates.values()} - set(out):
        root = PARTY_ROOTS.get(party.split("/")[0])
        if root is None or root not in out:
            raise LookupError(f"no party id for {party}")
        out[party] = out[root]
    return out


def title(name: str) -> str:
    def word(text: str) -> str:
        if text.startswith(('"', "(")):
            return text[0] + word(text[1:])
        if re.fullmatch(r"(II|III|IV|[A-Z]\.?)", text):
            return text
        lower = text.lower()
        if lower.startswith("mc") and len(lower) > 2:
            return "Mc" + lower[2:].capitalize()
        return "-".join(part.capitalize() for part in lower.split("-")).replace("'S", "'s")

    return " ".join(word(text) for text in name.split())


def contest_name(race: Race) -> str:
    if race.name in STATEWIDE:
        return STATEWIDE[race.name]
    if race.name == "STATE SENATOR":
        return f"2026 Vermont Senate election, {title(race.label.replace('-', ' '))}"
    if race.name == "STATE REPRESENTATIVE":
        return f"2026 Vermont House of Representatives election, {title(race.label)}"
    raise LookupError(f"no contest name for {race.name}")


def matched(ref: Reference, race: Race) -> tuple[str, dict[str, str]]:
    want = norm(contest_name(race))
    hits = [value for name, value in ref.contests.items() if norm(name) == want]
    if len(hits) != 1:
        raise LookupError(f"{contest_name(race)}: {len(hits)} matching contests in the reference")
    return hits[0]


def source_id() -> str:
    return key("source", "results")


def town_unit(town: str, district: str, senate: str) -> str:
    return key("unit", unit_code(town, district, senate))


def unit_code(town: str, district: str, senate: str) -> str:
    return f"{DAY}:VT:{town}:{district}:{senate}"


def parts_of(all_races: list[Race]) -> dict[tuple[str, str, str], str]:
    parts: dict[tuple[str, str, str], str] = {}
    for race in all_races:
        for row in race.rows:
            if parts.setdefault((row.town, row.district, row.senate), row.county) != row.county:
                raise ValueError(f"{row.town} {row.district}: two counties")
    return parts


def units(ref: Reference, data: Feed, all_races: list[Race]) -> Package:
    p = Package(UNITS, SOURCE, "Vermont's towns for November 3, 2026, with the Secretary of State as the source")
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
    for artifact in data.artifacts:
        p.artifact(artifact, src)
    p.external |= {ref.polity, "us.precinct"}
    parts = parts_of(all_races)
    whole = defaultdict(int)
    for town, _, _ in parts:
        whole[town] += 1
    for (town, district, senate), county in sorted(parts.items()):
        parent, county_area = ref.counties[county]
        simple = norm(town.replace("SAINT", "ST."))
        area = ref.towns.get(ALIASES.get(simple, simple)) if town != "BURLINGTON" else ref.towns.get("burlington")
        p.external |= {parent, area or county_area}
        p.add(
            "reporting_unit",
            id=town_unit(town, district, senate),
            polity_id=ref.polity,
            scheme_code="us.precinct",
            code=unit_code(town, district, senate),
            parent_id=parent,
            area_relation="equals" if area and whole[town] == 1 else "within",
            area_id=area or county_area,
            kind="standard",
            valid_from=DAY,
            valid_to=date(2026, 11, 4),
        )
    return p


def value(p: Package, result_set: str, unit: str, option: str, amount: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=1,
        count_round=0,
        flags=0,
        value=amount,
    )


def attach(
    p: Package,
    ref: Reference,
    data: Feed,
    race: Race,
    contest: str,
    options: dict[int, str],
    write_in: str,
    name: str,
) -> None:
    """Add the certified result set of one contest: its town-part values, declared totals, and winners."""
    src = source_id()
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=ref.election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=src,
        status="active",
        is_default=True,
        completeness=1.0,
        as_of=data.stamp,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD,
        lang="en",
    )
    sums: dict[int, int] = defaultdict(int)
    write_ins = 0
    for row in race.rows:
        unit = town_unit(row.town, row.district, row.senate)
        for candidate, amount in row.votes.items():
            value(p, result_set, unit, options[candidate], amount)
            sums[candidate] += amount
        value(p, result_set, unit, write_in, row.write_ins)
        write_ins += row.write_ins
    for candidate, option in options.items():
        if race.total.get(candidate) != sums[candidate]:
            raise ValueError(
                f"{name}: {race.candidates[candidate][0]} totals {race.total.get(candidate)}, not {sums[candidate]}"
            )
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=race.total[candidate])
    if race.total_write_ins != write_ins:
        raise ValueError(f"{name}: write-ins total {race.total_write_ins}, not {write_ins}")
    p.add("declared", result_set_id=result_set, option_id=write_in, measure=1, value=race.total_write_ins)
    if len(race.winners) != race.seats:
        raise ValueError(f"{name}: {len(race.winners)} winners for {race.seats} seats")
    ranked = sorted(sums, key=lambda c: -sums[c])
    if set(ranked[: race.seats]) != race.winners:
        raise ValueError(f"{name}: the flagged winners are not the top {race.seats} by votes")
    for candidate, option in options.items():
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="elected" if candidate in race.winners else "lost",
        )


def write_in_option(p: Package, contest: str) -> str:
    option = key("option", contest, "write_in")
    p.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
    return option


def existing(ref: Reference, data: Feed, group: list[Race], pkey: str, about: str) -> Package:
    p = Package(pkey, SOURCE, about, depends=[UNITS])
    p.external.add(ref.election)
    for artifact in data.artifacts:
        p.uses(artifact)
    for race in group:
        name = contest_name(race)
        contest, labels = matched(ref, race)
        options: dict[int, str] = {}
        for candidate, (person, _) in race.candidates.items():
            found = labels.get(norm(person))
            if found is None:
                raise LookupError(f"{name}: no option for {person}")
            options[candidate] = found
        if set(labels.values()) != set(options.values()):
            raise LookupError(f"{name}: the reference has options the results lack")
        p.external |= {contest, *options.values()}
        attach(p, ref, data, race, contest, options, write_in_option(p, contest), name)
    return p


def county_package(ref: Reference, data: Feed, group: list[Race], parties: dict[str, str | None]) -> Package:
    p = Package(
        f"{SOURCE}/county-offices",
        SOURCE,
        "Certified results of Vermont's county offices on November 3, 2026, by town",
        depends=[UNITS],
    )
    p.external.add(ref.election)
    for artifact in data.artifacts:
        p.uses(artifact)
    offices: dict[str, str] = {}
    for race in sorted(group, key=lambda r: (r.county or "", r.name)):
        county = (race.county or "").upper()
        office_name = COUNTY_OFFICES[race.name]
        county_name = title(county)
        area = ref.counties[county][1]
        p.external |= {ref.polity, area}
        office = offices.get(f"{county}:{race.name}")
        if office is None:
            office = offices[f"{county}:{race.name}"] = key("office", county, race.name)
            p.add("office", id=office, polity_id=ref.polity, kind="other")
            p.name(office, f"{county_name} County {office_name}", "en", "official")
        name = f"2026 {county_name} County {office_name} election"
        contest = key("contest", county, race.name)
        p.add(
            "contest",
            id=contest,
            election_id=ref.election,
            kind="office",
            office_id=office,
            district_area_id=area,
            magnitude=race.seats,
            default_result_kind="counted",
        )
        p.name(contest, name, "en", "official")
        options: dict[int, str] = {}
        for candidate, (person_name, party) in race.candidates.items():
            label = title(person_name)
            option = options[candidate] = key("option", contest, candidate)
            party_id = parties[party]
            if party_id:
                p.external.add(party_id)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_label=label,
            )
            person = key("person", candidate, contest)
            p.add("person", id=person)
            p.name(person, label, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", contest, candidate),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=party_id,
                is_independent=party == "INDEPENDENT",
            )
        attach(p, ref, data, race, contest, options, write_in_option(p, contest), name)
    return p


def build() -> list[Package]:
    data = feed()
    all_races = races(data.files)
    ref = reference()
    parties = party_ids(ref, [race for race in all_races if race.name not in COUNTY_OFFICES])
    parties |= party_ids_for_county(parties, all_races)
    by_kind: dict[str, list[Race]] = defaultdict(list)
    for race in all_races:
        by_kind["county" if race.name in COUNTY_OFFICES else race.name].append(race)
    federal = [race for race in all_races if race.name in STATEWIDE]
    built = [units(ref, data, all_races)]
    built.append(
        existing(
            ref,
            data,
            federal,
            f"{SOURCE}/statewide",
            "Certified results of Vermont's U.S. House race and statewide offices on November 3, 2026, by town",
        )
    )
    built.append(
        existing(
            ref,
            data,
            by_kind["STATE SENATOR"],
            f"{SOURCE}/senate",
            "Certified results of the 2026 Vermont Senate elections on November 3, 2026, by town",
        )
    )
    built.append(
        existing(
            ref,
            data,
            by_kind["STATE REPRESENTATIVE"],
            f"{SOURCE}/house",
            "Certified results of the 2026 Vermont House of Representatives elections on November 3, 2026, by town",
        )
    )
    built.append(county_package(ref, data, by_kind["county"], parties))
    return built


def party_ids_for_county(parties: dict[str, str | None], all_races: list[Race]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for race in all_races:
        if race.name not in COUNTY_OFFICES:
            continue
        for _, party in race.candidates.values():
            if party in parties:
                continue
            root = PARTY_ROOTS.get(party.split("/")[0])
            if root is None or root not in parties:
                raise LookupError(f"no party id for {party}")
            out[party] = parties[root]
    return out


def packages() -> list[str]:
    return [UNITS, f"{SOURCE}/statewide", f"{SOURCE}/senate", f"{SOURCE}/house", f"{SOURCE}/county-offices"]


def parse() -> None:
    fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
