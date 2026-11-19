import csv
import io
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, NamedTuple
from uuid import uuid5

import duckdb
import httpx

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "us.la_certified_2026"
BASE = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20261103/"
DATES = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=ElectionDates.htm"
RACES = BASE + "RacesCandidates_Multiparish.htm"
TOTALS = BASE + "Votes_Multiparish.htm"
PARISHES = [f"{number:02}" for number in range(1, 65)]


def parish_url(parish: str) -> str:
    return f"{BASE}VotesParish/Votes_{parish}.htm"


def precinct_url(race: str) -> str:
    return f"{BASE}csv/ByPrecinct_{race}.csv"


def many(value: Any) -> list[Any]:
    return [value] if isinstance(value, dict) else value or []


def races() -> list[dict[str, Any]]:
    return many(json.loads(raw.cached(SOURCE, RACES).path.read_text())["Races"]["Race"])


def fetch() -> None:
    raw.fetch(SOURCE, DATES, suffix=".json")
    raw.fetch(SOURCE, RACES, suffix=".json")
    raw.fetch(SOURCE, TOTALS, suffix=".json")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda parish: raw.fetch(SOURCE, parish_url(parish), suffix=".json"), PARISHES))

        def precinct(race: str) -> None:
            try:
                raw.fetch(SOURCE, precinct_url(race), suffix=".csv")
            except httpx.HTTPStatusError as failure:
                print(f"{precinct_url(race)}: {failure.response.status_code}", flush=True)

        list(pool.map(precinct, [race["ID"] for race in races()]))


PUBLISHER = "Louisiana Secretary of State"
TITLE = "Election Results Data: November 3, 2026"
SITE = "https://voterportal.sos.la.gov/ElectionResults/"
DAY = "2026-11-03"
REFERENCE = DATA / "reference" / "us"
ELECTED = {"Elected": "elected", "Runoff": "advanced"}
QUESTIONS = ("Constitutional Amendment", "Proposition")
METHOD = (
    "Parish totals as published in the Secretary of State's Election Results Data for November 3, 2026, which the "
    "portal flags as official. Where a race covers only part of a parish, the parish's value is the part of the "
    "parish inside the race's electorate, and the registered and ballots-cast figures are the portal's counts for "
    "that race in that parish. Every precinct in the race reported. A candidate the portal marks Runoff advances to "
    "the December 12, 2026 runoff; the other candidates in such a race are eliminated."
)
PRECINCT_METHOD = (
    "Precinct and early-voting lines from the Secretary of State's by-precinct export for the race. Early voting and "
    "provisional ballots appear as their own lines for each parish. The lines sum to the parish totals of the "
    "default result set for the contest."
)
UNITS = f"{SOURCE}/units"


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\s*\([A-Z]+\)\s*$", "", text.strip()).lower())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


class Reference(NamedTuple):
    polity: str
    counties: list[tuple[str, str, str, str]]  # code, name, unit id, area id
    precincts: dict[str, str]
    contests: dict[str, tuple[str, str, dict[str, str]]]  # contest id -> election id, name, {normalized label: option}


def reference() -> Reference:
    if not (REFERENCE / "contest.parquet").exists():
        raise FileNotFoundError(f"{REFERENCE} is missing the `us` reference partition")
    con = duckdb.connect()
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in ("polity", "reporting_unit", "area", "contest", "contest_option", "election")
    }
    polity = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-LA'").fetchone()
    if polity is None:
        raise LookupError("the reference holds no US-LA polity")
    counties = con.execute(
        f"""select u.code, replace(a.name, ' Parish, LA', ''), u.id, u.area_id
            from '{path["reporting_unit"]}' u join '{path["area"]}' a on a.id = u.area_id
            where u.scheme_code = 'us.county' and u.polity_id = ? and u.code like '22%' order by u.code""",
        [polity[0]],
    ).fetchall()
    precincts = dict(
        con.execute(
            f"""select code, id from '{path["reporting_unit"]}'
                where scheme_code = 'us.precinct' and polity_id = ? and valid_from <= date '{DAY}'
                  and valid_to > date '{DAY}'""",
            [polity[0]],
        ).fetchall()
    )
    contests: dict[str, tuple[str, str, dict[str, str]]] = {}
    for contest, election, name, option, label in con.execute(
        f"""select c.id, c.election_id, c.name, o.id, o.ballot_label from '{path["contest"]}' c
            join '{path["election"]}' e on e.id = c.election_id
            join '{path["contest_option"]}' o on o.contest_id = c.id
            where e.date_from = date '{DAY}' and c.pipeline in ('us.fec_2026', 'us.la_state_2026', 'us.la_judicial_2026',
              'us.calendar_2026', 'us.local_2026', 'us.measures_2026', 'us.la_propositions_2026', 'us.nov3_manual_2026')"""
    ).fetchall():
        contests.setdefault(contest, (election, name, {}))[2][label or ""] = option
    return Reference(polity[0], counties, precincts, contests)


def parish_table(ref: Reference) -> list[tuple[str, str, str, str]]:
    """The portal numbers parishes alphabetically, which is the order of their FIPS codes."""
    if len(ref.counties) != len(PARISHES):
        raise ValueError(f"expected {len(PARISHES)} parishes in the reference, found {len(ref.counties)}")
    return ref.counties


def matches(ref: Reference) -> dict[str, tuple[str, dict[str, str]]]:
    """Each portal race's contest and each choice's option in the reference."""
    bylabels: dict[frozenset[str], list[str]] = defaultdict(list)
    for contest, (_, _, options) in ref.contests.items():
        bylabels[frozenset(norm(label) for label in options)].append(contest)
    found: dict[str, tuple[str, dict[str, str]]] = {}
    pending = []
    for race in races():
        if race["GeneralTitle"] in QUESTIONS:
            pending.append(race)
            continue
        hits = bylabels.get(frozenset(norm(choice["Desc"]) for choice in race["Choice"]), [])
        if len(hits) != 1:
            raise LookupError(f"race {race['ID']} {race['SpecificTitle']} matches {len(hits)} contests")
        by_name = {norm(label): option for label, option in ref.contests[hits[0]][2].items()}
        found[race["ID"]] = (hits[0], {choice["ID"]: by_name[norm(choice["Desc"])] for choice in race["Choice"]})
    senate = next(race["ID"] for race in races() if race["GeneralTitle"] == "U.S. Senator")
    state = ref.contests[found[senate][0]][0]
    for race in pending:
        number = re.match(r"CA No\.\s+(\d+)", race["SpecificTitle"])
        hits = [
            contest
            for contest, (election, name_, _) in ref.contests.items()
            for name in [name_ or ""]
            if (
                (number and election == state and name == f"Proposed Amendment No. {number[1]}")
                or (not number and "Shreveport" in name and name.endswith("Proposition"))
            )
        ]
        if len(hits) != 1:
            raise LookupError(f"measure {race['ID']} {race['SpecificTitle']} matches {len(hits)} contests")
        options = {norm(label): option for label, option in ref.contests[hits[0]][2].items()}
        choice_option = {}
        for choice in race["Choice"]:
            words = {"yes": ("yes", "for"), "no": ("no", "against")}[choice["Desc"].lower()]
            (choice_option[choice["ID"]],) = [options[word] for word in words if word in options]
        found[race["ID"]] = (hits[0], choice_option)
    return found


class Lines(NamedTuple):
    parish: dict[str, dict[str, Any]]  # parish number -> race entry
    precincts: list[tuple[str, str, list[str]]]  # parish name, unit suffix, votes per choice


def parish_races() -> dict[str, dict[str, dict[str, Any]]]:
    found: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for parish in PARISHES:
        listed = json.loads(raw.cached(SOURCE, parish_url(parish)).path.read_text())
        for race in many((listed.get("Races") or {}).get("Race")):
            found[race["ID"]][parish] = race
    return found


def precinct_lines(race: dict[str, Any]) -> list[tuple[str, str, list[int]]]:
    text = raw.cached(SOURCE, precinct_url(race["ID"])).path.read_text()
    table = list(csv.reader(io.StringIO(text)))
    header = table[0]
    if header[4:] != [choice["Desc"] for choice in many(race["Choice"])]:
        raise ValueError(f"race {race['ID']}: precinct columns {header[4:]} differ from the candidates")
    return [
        (line[1], " ".join(part for part in (line[2], line[3]) if part).upper(), [int(value) for value in line[4:]])
        for line in table[1:]
    ]


def parish_codes(ref: Reference) -> dict[str, tuple[str, str, str, str]]:
    """Parish name as the precinct files print it -> its county's code, name, unit id, and area id."""
    return {norm(county[1]): county for county in parish_table(ref)}


def unit_code(county: str, suffix: str, exists: bool) -> str:
    return f"{'2026' if exists else DAY}:LA:{county}::{suffix}"


def precinct_unit(ref: Reference, county: tuple[str, str, str, str], suffix: str) -> tuple[str, bool]:
    code = unit_code(county[0], suffix, True)
    if code in ref.precincts:
        return ref.precincts[code], True
    return key("unit", unit_code(county[0], suffix, False)), False


def source_id() -> str:
    return key("source", "results")


def units(ref: Reference) -> Package:
    p = Package(
        UNITS,
        SOURCE,
        "Louisiana's precinct and early-voting reporting units for November 3, 2026 that the archive did not yet "
        "hold, with the Secretary of State as the source of the election's results",
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
    for url in [RACES, TOTALS, *(parish_url(parish) for parish in PARISHES), *(precinct_url(r["ID"]) for r in races())]:
        p.artifact(raw.cached(SOURCE, url), src)
    p.external |= {ref.polity, "us.precinct"}
    codes = parish_codes(ref)
    minted: dict[str, tuple[str, str, str, str]] = {}
    for race in races():
        for parish, suffix, _ in precinct_lines(race):
            county = codes[norm(parish)]
            unit, exists = precinct_unit(ref, county, suffix)
            if not exists and unit not in minted:
                minted[unit] = (county[2], county[3], unit_code(county[0], suffix, False), suffix)
    for unit, (parent, area, code, suffix) in sorted(minted.items()):
        p.external |= {parent, area}
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ref.polity,
            scheme_code="us.precinct",
            code=code,
            parent_id=parent,
            area_relation="within",
            area_id=area,
            kind="aggregate" if suffix in ("EARLY VOTING", "PROVISIONAL VOTES") else "standard",
            valid_from=date(2026, 11, 3),
            valid_to=date(2026, 11, 4),
        )
    return p


def value(p: Package, result_set: str, unit: str, option: str | None, measure: int, amount: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=amount,
    )


def election_package(
    ref: Reference,
    election: str,
    name: str,
    items: list[tuple[dict[str, Any], str, dict[str, str]]],
    totals: dict[str, dict[str, Any]],
    by_parish: dict[str, dict[str, dict[str, Any]]],
) -> Package:
    p = Package(
        f"{SOURCE}/{slug(name)}",
        SOURCE,
        f"Certified results of the {name} on November 3, 2026, by parish and by precinct",
        depends=[UNITS],
    )
    src = source_id()
    p.external.add(election)
    table = parish_table(ref)
    codes = parish_codes(ref)
    p.uses(raw.cached(SOURCE, RACES))
    p.uses(raw.cached(SOURCE, TOTALS))
    for race, contest, options in items:
        p.external |= {contest, *options.values()}
        used = by_parish[race["ID"]]
        for parish in used:
            p.uses(raw.cached(SOURCE, parish_url(parish)))
        p.uses(raw.cached(SOURCE, precinct_url(race["ID"])))
        total = totals[race["ID"]]
        choices = many(race["Choice"])
        question = race["GeneralTitle"] in QUESTIONS
        counts = {choice["ID"]: int(choice["VoteTotal"]) for choice in many(total["Choice"])}
        result_set = key("result_set", race["ID"], "parish")
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
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        parish_sum: dict[str, int] = defaultdict(int)
        by_name: dict[str, dict[str, int]] = {}
        for parish, entry in sorted(used.items()):
            county = table[int(parish) - 1]
            unit = county[2]
            p.external.add(unit)
            by_name[norm(county[1])] = {}
            for choice in many(entry["Choice"]):
                amount = int(choice["VoteTotal"])
                value(p, result_set, unit, options[choice["ID"]], 1, amount)
                parish_sum[choice["ID"]] += amount
                by_name[norm(county[1])][choice["ID"]] = amount
            for measure, field in ((2, "VoterCountQualified"), (3, "VoterCountVoted")):
                if int(entry[field]) >= 0:  # the portal prints -1 where it has no count
                    value(p, result_set, unit, None, measure, int(entry[field]))
        for choice in choices:
            if parish_sum[choice["ID"]] != counts[choice["ID"]]:
                raise ValueError(f"{race['SpecificTitle']}: parishes don't sum to the state total for {choice['Desc']}")
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[choice["ID"]],
                measure=1,
                value=counts[choice["ID"]],
            )
        runoff = any(choice["Outcome"] == "Runoff" for choice in many(total["Choice"]))
        if question:
            winner = max(counts, key=lambda choice_id: counts[choice_id])
            labeled = [choice["ID"] for choice in many(total["Choice"]) if choice["Outcome"]]
            if labeled != [winner]:
                raise ValueError(f"{race['SpecificTitle']}: the portal's outcome label is not on the leading answer")
        for choice in many(total["Choice"]):
            if question:
                kind = "won" if choice["ID"] == winner else "lost"
            elif choice["Outcome"] in ELECTED:
                kind = ELECTED[choice["Outcome"]]
            elif choice["Outcome"] == "Defeated":
                kind = "eliminated" if runoff else "lost"
            else:
                raise ValueError(f"{race['SpecificTitle']}: unexpected outcome {choice['Outcome']}")
            p.add("outcome", result_set_id=result_set, option_id=options[choice["ID"]], kind=kind)
        lines = precinct_lines(race)
        grid: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        precinct_set = key("result_set", race["ID"], "precinct")
        seen: set[tuple[str, str]] = set()
        for parish, suffix, amounts in lines:
            county = codes[norm(parish)]
            unit, exists = precinct_unit(ref, county, suffix)
            if exists:
                p.external.add(unit)
            if (unit, suffix) in seen:
                raise ValueError(f"{race['SpecificTitle']}: {parish} {suffix} appears twice")
            seen.add((unit, suffix))
            for choice, amount in zip(choices, amounts, strict=True):
                grid[norm(parish)][choice["ID"]] += amount
                value(p, precinct_set, unit, options[choice["ID"]], 1, amount)
        for parish, amounts in by_name.items():
            if dict(grid[parish]) != amounts:
                raise ValueError(f"{race['SpecificTitle']}: precinct lines for {parish} don't sum to the parish totals")
        if set(grid) != set(by_name):
            raise ValueError(f"{race['SpecificTitle']}: precinct and parish files cover different parishes")
        p.add(
            "result_set",
            id=precinct_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            source_id=src,
            status="active",
            is_default=False,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", precinct_set),
            entity_id=precinct_set,
            kind="methodology",
            body_md=PRECINCT_METHOD,
            lang="en",
        )
    return p


def build() -> list[Package]:
    ref = reference()
    found = matches(ref)
    totals = {
        race["ID"]: race for race in many(json.loads(raw.cached(SOURCE, TOTALS).path.read_text())["Races"]["Race"])
    }
    by_parish = parish_races()
    grouped: dict[str, list[tuple[dict[str, Any], str, dict[str, str]]]] = defaultdict(list)
    for race in races():
        contest, options = found[race["ID"]]
        grouped[ref.contests[contest][0]].append((race, contest, options))
    names = {
        row[0]: row[1]
        for row in duckdb.connect()
        .execute(
            f"select e.id, e.name from '{REFERENCE / 'election.parquet'}' e where e.id in (select unnest(?))",
            [list(grouped)],
        )
        .fetchall()
    }
    built = [units(ref)]
    for election, items in sorted(grouped.items(), key=lambda pair: names[pair[0]]):
        built.append(election_package(ref, election, names[election], items, totals, by_parish))
    return built


def packages() -> list[str]:
    return [UNITS, *(f"{SOURCE}/{slug(name)}" for name in election_names())]


def election_names() -> list[str]:
    ref = reference()
    found = matches(ref)
    elections = {ref.contests[contest][0] for contest, _ in found.values()}
    rows = (
        duckdb.connect()
        .execute(
            f"select name from '{REFERENCE / 'election.parquet'}' where id in (select unnest(?)) order by name",
            [list(elections)],
        )
        .fetchall()
    )
    return [row[0] for row in rows]


def parse() -> None:
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
