"""Hungary's 1990 local elections (30 September and 14 October) from the National Election Office's archive.

The office's bundle for 1990 is partial by its own account: it holds the list of mayors, with the organizations that
nominated them, and the composition of each settlement council by organization. It publishes no vote counts, no
polling-station results, no Budapest Assembly, and no county assemblies, so the package stores outcomes and seats only.

The package is self-contained. It carries its own settlement and county areas (coded `1990:...`), bodies, offices, and
source row, because `hu.valasztas/geo` and `hu.valasztas/registry` are built from a full parse of every election and
hold only the entities those builds met.
"""

import re
from collections import defaultdict
from datetime import date

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.hu import valasztas as hu

SOURCE = hu.SOURCE
BUNDLE = "1990_onkormanyzati"
DAY = date(1990, 9, 30)
RUNOFF = date(1990, 10, 14)
KEY = f"{SOURCE}/{DAY}"
PATH = f"hu/{DAY}-local"

KNOWN_PARTIES = (
    "MAGYAR DEMOKRATA FÓRUM",
    "SZABAD DEMOKRATÁK SZÖVETSÉGE",
    "FIATAL DEMOKRATÁK SZÖVETSÉGE",
    "KERESZTÉNYDEMOKRATA NÉPPÁRT",
    "MAGYAR SZOCIALISTA PÁRT",
    "MAGYAR SZOCIALISTA MUNKÁSPÁRT",
    "FÜGGETLEN KISGAZDA",
    "AGRÁRSZÖVETSÉG",
    "VÁLLALKOZÓK PÁRTJA",
    "MAGYARORSZÁGI SZOCIÁLDEMOKRATA",
    "MAGYARORSZÁGI ZÖLD PÁRT",
    "MAGYAR NÉPPÁRT",
)
ABBREVIATIONS = re.compile(r"^(MDF|KDNP|FKGP|SZDSZ|FIDESZ|MSZP|MSZMP)( ?- ?(MDF|KDNP|FKGP|SZDSZ|FIDESZ|MSZP|MSZMP))*$")
INDEPENDENT = "FÜGGETLEN JELÖLT"
UNKNOWN = "NEM ISMERT"
MAYOR_METHOD = (
    "The National Election Office's archive lists the mayor of each settlement after the 1990 local elections, with the "
    "organizations that nominated them. It publishes no vote counts for 1990, so only the winner is stored. The archive "
    "does not say whether a mayor won on 30 September or in the 14 October runoff, or whether the settlement's voters or "
    "its council chose the mayor."
)
COUNCIL_METHOD = (
    "The National Election Office's archive lists, for each settlement council, the seats each organization (or "
    "organization group) held after the 1990 local elections, as published; seats of a national minority are listed "
    "separately. It states that it holds no complete information on the 1990 elections and publishes no vote counts, "
    "so only seats are stored. Independent candidates are published as one group per settlement."
)


def roman(n: int) -> str:
    out = ""
    for value, text in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += text
            n -= value
    return out


def place_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    found = re.fullmatch(r"BUDAPEST (\d+)", name)
    if found:
        return f"Budapest {roman(int(found[1]))}. kerület"
    return hu.person_text(name)


def place_key(maz: str, name: str) -> tuple[str, str]:
    return maz, hu.fold(name)


def fetch() -> None:
    raw.fetch(SOURCE, hu.ARCHIVE, suffix=".zip")


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    p = Package(
        KEY,
        SOURCE,
        "Hungary's 1990 local elections: mayors and the party composition of settlement councils, from the National "
        "Election Office's archive",
    )
    p.external |= {hu.HUNGARY, hu.TERRITORY, "hu.megye", "hu.telepules"}
    source = p.id("source", "nvi-1990-local")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Nemzeti Választási Iroda",
        title="Választási eredmények (valasztas.hu), 1990. évi önkormányzati választások",
        url=hu.ARCHIVE_PAGE,
        license="Public information under Act CXII of 2011; reuse with the source acknowledged",
        license_url="https://www.valasztas.hu/impresszum",
        attribution="Nemzeti Választási Iroda, valasztas.hu",
    )
    p.artifact(raw.cached(SOURCE, hu.ARCHIVE), source)

    election = p.id("election", "onk:1990")
    p.add(
        "election",
        id=election,
        polity_id=hu.HUNGARY,
        parent_id=None,
        kind="local",
        date_from=DAY,
        date_to=RUNOFF,
        status="held",
    )
    p.name(election, "1990 Hungarian local elections", "en", "official")
    p.name(election, "1990. évi helyi önkormányzati választások", "hu", "native", False)
    p.add("slug", path=PATH, entity_id=election, is_current=True)

    # Counties and settlements: the mayors list names every settlement the bundle covers.
    mayors = hu.piped(BUNDLE, "telep_polg_90.txt")
    councils = hu.piped(BUNDLE, "telep_onk90.txt")
    areas: dict[tuple[str, str], tuple[str, str, str]] = {}  # (maz, folded) -> (area id, code, display name)
    for maz, name in hu.COUNTIES.items():
        county = p.id("area", "county", maz)
        p.add("area", id=county, scheme_code="hu.megye")
        p.add("area_code", area_id=county, code=f"1990:{maz}")
        p.add("area_parent", area_id=county, parent_id=hu.TERRITORY)
        p.name(county, "Budapest" if maz == "01" else f"{name} vármegye", "hu", "official")
    codes: dict[str, tuple[str, str]] = {}
    for maz, name in [*((row[0], row[2]) for row in mayors), *((row[0], row[3]) for row in councils)]:
        index = place_key(maz, name)
        if index in areas:
            continue
        display = place_name(name)
        code = f"{maz}-{index[1]}"
        if code in codes:
            raise ValueError(f"settlements {codes[code]} and {index} share the code {code}")
        codes[code] = index
        area = p.id("area", "settlement", code)
        areas[index] = (area, code, display)
        p.add("area", id=area, scheme_code="hu.telepules")
        p.add("area_code", area_id=area, code=f"1990:{code}")
        p.add("area_parent", area_id=area, parent_id=p.id("area", "county", maz))
        p.name(area, display, "hu", "official")

    parties: dict[str, str] = {}
    paths: set[str] = set()

    def party(label: str) -> str | None:
        label = re.sub(r"\s+", " ", label).strip()
        if not (ABBREVIATIONS.match(label) or label.startswith(KNOWN_PARTIES)):
            return None
        if label not in parties:
            id = parties[label] = p.id("party", label)
            p.add("party", id=id, polity_id=hu.HUNGARY, kind="bloc" if ABBREVIATIONS.search(label) else "party")
            p.name(id, hu.person_text(label) if not ABBREVIATIONS.match(label) else label, "hu", "official")
        return parties[label]

    def contest(
        tag: str,
        kind: str,
        title: str,
        english: str,
        area: str,
        method: str,
        magnitude: int,
        office: str | None = None,
        body: str | None = None,
    ) -> tuple[str, str]:
        id = p.id("contest", tag)
        p.add(
            "contest",
            id=id,
            election_id=election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=area,
            magnitude=magnitude,
            round_number=1,
            previous_round_id=None,
            electoral_system_id=None,
            default_result_kind="counted",
        )
        p.name(id, title, "hu", "official")
        p.name(id, english, "en", "english", False)
        path = f"{PATH}/{changeset.slug(tag)}"
        while path in paths:
            path += "-2"
        paths.add(path)
        p.add("slug", path=path, entity_id=id, is_current=True)
        result_set = p.id("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=0.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        return id, result_set

    # Mayors
    people: set[str] = set()
    mayor_count = 0
    for maz, _, name, title, person, nominator in mayors:
        area, code, display = areas[place_key(maz, name)]
        office = p.id("office", "mayor", code)
        p.add("office", id=office, polity_id=hu.HUNGARY, kind="mayor")
        p.name(office, f"Mayor of {display}", "en", "official")
        p.name(office, f"{display} polgármestere", "hu", "native", False)
        contest_id, result_set = contest(
            f"mayor:{code}", "office", f"{display} polgármester", f"Mayor of {display}", area, MAYOR_METHOD, 1, office
        )
        label = re.sub(r"\s+", " ", f"{title} {person}").strip()
        option = p.id("option", f"mayor:{code}")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="candidate",
            party_id=None if nominator in (INDEPENDENT, UNKNOWN) else party(nominator),
            ballot_position=None,
            ballot_label=label,
        )
        who = p.id("person", f"mayor:{code}")
        if who not in people:
            people.add(who)
            p.add("person", id=who)
            p.name(who, hu.person_text(hu.TITLES.sub("", label)), "hu", "official")
        p.add(
            "candidacy",
            id=p.id("candidacy", f"mayor:{code}"),
            person_id=who,
            option_id=option,
            role="head",
            list_position=None,
            ballot_name=label,
            member_of_party_id=None,
            is_independent=nominator == INDEPENDENT,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected", seats=None)
        mayor_count += 1

    # Councils: the seats of each organization
    seats: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for maz, _, _, name, organization, minority, count in councils:
        seats[place_key(maz, name)].append((re.sub(r"\s+", " ", organization).strip(), minority, int(count)))
    total = 0
    for index, groups in sorted(seats.items()):
        area, code, display = areas[index]
        body = p.id("body", "council", code)
        p.add("body", id=body, polity_id=hu.HUNGARY, kind="council")
        p.name(body, f"{display} council", "en", "official")
        p.name(body, f"{display} képviselő-testülete", "hu", "native", False)
        size = sum(count for _, _, count in groups)
        contest_id, result_set = contest(
            f"council:{code}",
            "body_seats",
            f"{display} képviselő-testület",
            f"{display} council",
            area,
            COUNCIL_METHOD,
            size,
            body=body,
        )
        for organization, minority, count in groups:
            label = hu.person_text(organization) if organization != INDEPENDENT else "Független jelöltek"
            if minority:
                label = f"{label} ({minority} kisebbség)"
            option = p.id("option", f"council:{code}", organization, minority)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="list",
                party_id=None if minority else party(organization),
                ballot_position=None,
                ballot_label=label,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="won", seats=count)
        total += size
    print(f"{KEY}: {mayor_count:,} mayors, {len(seats):,} councils, {total:,} council seats", flush=True)
    p.write()


def emit() -> None:
    package.run("load", packages())
