"""Nigeria's 2019 general election from the Independent National Electoral Commission's published report.

The report's annexures give the presidential result by candidate for the whole country, and for every other contest
(governor, senatorial district, federal constituency, state constituency, and the FCT's area councils and wards) the
party that won, with the constituency's counts of local government areas, registration areas, and polling units.
"""

import re
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ng import report, tables
from ea.sources.ng.tables import Candidate, Seat

SOURCE = "ng.inec"
NIGERIA = str(uuid5(package.NAMESPACE, "polity\x1fNG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNGA"))
REPORT = "https://www.inecnigeria.org/documents/resources/downloads/REPORT-OF-THE-2019-GENERAL-ELECTION.pdf"
PAGE = "https://www.inecnigeria.org/"
FEDERAL_DAY = date(2019, 2, 23)
STATE_DAYS = (date(2019, 3, 9), date(2019, 3, 23))
STATES = {
    "AB": "Abia",
    "AD": "Adamawa",
    "AK": "Akwa Ibom",
    "AN": "Anambra",
    "BA": "Bauchi",
    "BN": "Benue",
    "BO": "Borno",
    "BY": "Bayelsa",
    "CR": "Cross River",
    "DT": "Delta",
    "EB": "Ebonyi",
    "ED": "Edo",
    "EK": "Ekiti",
    "EN": "Enugu",
    "FC": "Federal Capital Territory",
    "GM": "Gombe",
    "IM": "Imo",
    "JG": "Jigawa",
    "KB": "Kebbi",
    "KD": "Kaduna",
    "KG": "Kogi",
    "KN": "Kano",
    "KT": "Katsina",
    "KW": "Kwara",
    "LA": "Lagos",
    "NG": "Niger",
    "NS": "Nasarawa",
    "OD": "Ondo",
    "OG": "Ogun",
    "OS": "Osun",
    "OY": "Oyo",
    "PL": "Plateau",
    "RV": "Rivers",
    "SO": "Sokoto",
    "TR": "Taraba",
    "YB": "Yobe",
    "ZM": "Zamfara",
}
SUFFIX = {"LG": "LA", "NW": "NS", "ON": "OD", "ZF": "ZM", "FCT": "FC"}
COUNCILS = ("Abaji", "Bwari", "Gwagwalada", "Kuje", "Kwali", "Abuja Municipal")
WARD_ORDER = (1, 2, 4, 5, 3, 6)
PARTY_NAMES = {
    "AD": "Alliance for Democracy",
    "LP": "Labour Party",
    "APN": "Alternative Party of Nigeria",
    "LM": "Liberation Movement",
    "MRDD": "Movement for the Restoration and Defence of Democracy",
    "UP": "United Patriots",
}
ALIASES = {"FRESH": "FDP"}
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f{SOURCE}"))
PRESIDENCY = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1foffice\x1fpresident"))
SENATE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fbody\x1fsenate"))
HOUSE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fbody\x1fhouse"))

SYSTEMS = {
    "president": (
        date(1999, 1, 1),
        {
            "formula": "two_round",
            "majority": 0,
            "advance": 2,
            "spread": {"scheme": "ng.state", "share": 0.25, "units": {"share": 2 / 3, "strict": False}},
        },
        (
            "The candidate with the most votes who also has at least a quarter of the votes in at least two thirds of "
            "the 36 states and the Federal Capital Territory is elected; if no one qualifies, the top candidate and "
            "the runner-up with the most states go to a second round."
        ),
    ),
    "fptp": (
        date(1999, 1, 1),
        {"formula": "fptp"},
        (
            "One member or officeholder for each constituency, elected by the most votes. A governor must also carry "
            "a quarter of the votes in two thirds of the state's local government areas; where the leader falls "
            "short, supplementary or second-round polls follow."
        ),
    ),
}
METHOD = (
    "The Commission's report gives each constituency's declared winning party, with the number of local government "
    "areas, registration areas, and polling units, and no vote counts or candidate names. The outcome is the party "
    "declared the winner after any supplementary or court-ordered polls the report folds into its annexure."
)
PRESIDENT_METHOD = (
    "The report prints each of the 73 candidates' national vote total and flags the elected candidate. It prints no "
    "state breakdown, and no registered, accredited, or rejected counts, in the annexure."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party(code: str) -> str:
    return key("party", ALIASES.get(code, code))


def state(suffix: str) -> str:
    return SUFFIX.get(suffix, suffix)


def title(text: str) -> str:
    text = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", text)).strip()
    return text if text != text.upper() else re.sub(r"[A-Za-z]+", lambda m: m[0].capitalize(), text)


def path() -> Path:
    return raw.DATA / raw.cached(SOURCE, REPORT).storage_key


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{FEDERAL_DAY}", f"{SOURCE}/{STATE_DAYS[0]}"]


def parse() -> None:
    candidates, seats = tables.read(path())
    checked(candidates, seats)
    geo(seats).write()
    registry(candidates, seats).write()
    federal(candidates, seats).write()
    states(seats).write()


def emit() -> None:
    package.run("load", packages())


def checked(candidates: list[Candidate], seats: list[Seat]) -> None:
    """Fail when the parse disagrees with what the report says it holds."""
    counts = {annex: sum(1 for s in seats if s.annex == annex) for annex in range(2, 8)}
    expected = {2: 29, 3: 109, 4: 360, 5: 991, 6: 6, 7: 62}
    if counts != expected:
        raise ValueError(f"annexure rows {counts} differ from {expected}")
    if [c.number for c in candidates] != list(range(1, 74)):
        raise ValueError("the presidential annexure does not hold candidates 1 to 73")
    if sum(c.elected for c in candidates) != 1:
        raise ValueError("the presidential annexure does not flag exactly one elected candidate")
    for seat in seats:
        if seat.annex in (3, 4, 5) and state(seat.state) not in STATES:
            raise ValueError(f"unknown state code in {seat.code}")
    for annex in (3, 4, 5):
        codes = [s.code for s in seats if s.annex == annex]
        if len(set(codes)) != len(codes):
            raise ValueError(f"annexure {annex} repeats a constituency code")
    wards = [s.number for s in seats if s.annex == 7]
    if sum(1 for n in wards if n == 1) != 6:
        raise ValueError("the councillorship annexure does not hold six area councils")


def source(p: Package) -> None:
    """Add the report's source and artifact to the package that owns them, `geo`."""
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Independent National Electoral Commission",
        title="Report of the 2019 General Election",
        url=REPORT,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent National Electoral Commission",
    )
    p.artifact(raw.cached(SOURCE, REPORT), SOURCE_ID)


def cite(p: Package) -> None:
    p.uses(raw.cached(SOURCE, REPORT))


def wards(seats: list[Seat]) -> list[tuple[int, Seat]]:
    rows: list[tuple[int, Seat]] = []
    group = -1
    for seat in (s for s in seats if s.annex == 7):
        if seat.number == 1:
            group += 1
        rows.append((WARD_ORDER[group], seat))
    return rows


def geo(seats: list[Seat]) -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Nigeria's 36 states and the Federal Capital Territory, 109 senatorial districts, 360 federal constituencies, "
        "991 state constituencies, 6 FCT area councils, and 62 FCT wards, as the Commission's 2019 report lists them",
    )
    p.external |= {NIGERIA, COUNTRY}
    source(p)
    for code, rank, kind, noun in (
        ("ng.state", 1, "administrative", "state"),
        ("ng.area_council", 2, "administrative", "area council"),
        ("ng.senatorial", 2, "electoral_district", "senatorial district"),
        ("ng.federal", 3, "electoral_district", "federal constituency"),
        ("ng.ward", 3, "electoral_district", "ward"),
        ("ng.state_constituency", 4, "electoral_district", "state constituency"),
        ("ng.tally", 9, "synthetic", "national tally"),
    ):
        p.add("scheme", code=code, polity_id=NIGERIA, kind=kind, rank=rank, noun=noun, code_system=None)

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "en", "official")

    for code, text in STATES.items():
        place("ng.state", code, text, COUNTRY)
    for seat in seats:
        home = area("ng.state", state(seat.state))
        if seat.annex == 3:
            label = STATES[state(seat.state)]
            name = label if seat.state == "FCT" else f"{label} {title(seat.name)}"
            place("ng.senatorial", seat.code, name, home)
        elif seat.annex == 4:
            place("ng.federal", seat.code, f"{title(seat.name)} ({STATES[state(seat.state)]})", home)
        elif seat.annex == 5:
            place("ng.state_constituency", seat.code, f"{title(seat.name)} ({STATES[state(seat.state)]})", home)
    fct = area("ng.state", "FC")
    for seat in (s for s in seats if s.annex == 6):
        place("ng.area_council", seat.code, f"{COUNCILS[int(seat.code) - 1]} Area Council", fct)
    for council, seat in wards(seats):
        place(
            "ng.ward",
            f"{council:02d}/{seat.code}",
            f"{title(seat.name)} ({COUNCILS[council - 1]})",
            area("ng.area_council", f"{council:02d}"),
        )
    return p


def registry(candidates: list[Candidate], seats: list[Seat]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Nigeria's parties, National Assembly, state assemblies, FCT area councils, governors, presidency, and "
        "electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NIGERIA, COUNTRY}
    cite(p)
    p.add("body", id=SENATE, polity_id=NIGERIA, kind="upper_house")
    p.name(SENATE, "Senate", "en", "official")
    p.add("body", id=HOUSE, polity_id=NIGERIA, kind="lower_house")
    p.name(HOUSE, "House of Representatives", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=NIGERIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Nigeria", "en", "official")
    for code, name in STATES.items():
        if code == "FC":
            continue
        body = key("body", "assembly", code)
        p.add("body", id=body, polity_id=NIGERIA, kind="unicameral")
        p.name(body, f"{name} State House of Assembly", "en", "official")
        office = key("office", "governor", code)
        p.add("office", id=office, polity_id=NIGERIA, kind="governor")
        p.name(office, f"Governor of {name} State", "en", "official")
    for number, name in enumerate(COUNCILS, 1):
        body = key("body", "council", number)
        p.add("body", id=body, polity_id=NIGERIA, kind="council")
        p.name(body, f"{name} Area Council", "en", "official")
        office = key("office", "chairman", number)
        p.add("office", id=office, polity_id=NIGERIA, kind="other", body_id=body)
        p.name(office, f"Chairman of {name} Area Council", "en", "official")
    for name, (since, rules, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add(
            "electoral_system", id=id, polity_id=NIGERIA, valid_from=since, valid_to=None, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names = {acronym: text for _, text, acronym in report.parties(path())}
    names.update(PARTY_NAMES)
    used = {ALIASES.get(c.party, c.party) for c in candidates} | {ALIASES.get(s.party, s.party) for s in seats}
    for acronym in sorted(used | set(names)):
        id = party(acronym)
        p.add("party", id=id, kind="party", polity_id=NIGERIA)
        p.add("external_id", entity_id=id, scheme="ng.inec.party", value=acronym)
        if acronym in names:
            p.name(id, names[acronym], "en", "official")
        p.name(id, acronym, "en", "abbreviation", False)
    if "FDP" in used:
        p.name(party("FDP"), "FRESH", "en", "abbreviation", False)
    return p


def contest(
    p: Package,
    election: str,
    tag: str,
    *,
    path: str,
    name: str,
    kind: str,
    district: str,
    system: str,
    body: str | None,
    office: str | None,
    winner: str,
) -> None:
    id = key("contest", tag)
    p.add(
        "contest",
        id=id,
        election_id=election,
        kind=kind,
        office_id=office,
        body_id=body,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(id, name, "en", "official")
    p.add("slug", path=path, entity_id=id, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=id,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    option = key("option", tag, winner)
    p.add(
        "contest_option",
        id=option,
        contest_id=id,
        kind="candidate",
        party_id=party(winner),
        ballot_label=winner,
    )
    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=1)


def election(p: Package, tag: str, first: date, last: date, text: str) -> tuple[str, str]:
    id = key("election", tag)
    path = f"ng/{first}-general"
    p.add("election", id=id, polity_id=NIGERIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(id, text, "en", "official")
    p.add("slug", path=path, entity_id=id, is_current=True)
    return id, path


def federal(candidates: list[Candidate], seats: list[Seat]) -> Package:
    p = Package(
        f"{SOURCE}/{FEDERAL_DAY}",
        SOURCE,
        "The 23 February 2019 presidential and National Assembly elections: national candidate votes for President "
        "and the winning party in each senatorial district and federal constituency",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NIGERIA, COUNTRY}
    cite(p)
    parent, path = election(
        p, "federal", FEDERAL_DAY, FEDERAL_DAY, "2019 Nigerian general election (President and National Assembly)"
    )
    plurality = key("electoral_system", "fptp")
    tag = "president"
    id = key("contest", tag)
    p.add(
        "contest",
        id=id,
        election_id=parent,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=COUNTRY,
        magnitude=1,
        round_number=1,
        electoral_system_id=key("electoral_system", "president"),
        default_result_kind="counted",
    )
    p.name(id, "2019 Nigerian presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=id, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=parent,
        contest_id=id,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=PRESIDENT_METHOD,
        lang="en",
    )
    unit = key("unit", tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=NIGERIA,
        scheme_code="ng.tally",
        code=f"{FEDERAL_DAY}:national",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    for c in candidates:
        option = key("option", tag, c.number)
        person = key("person", tag, c.number)
        p.add("person", id=person, gender="male" if c.gender == "M" else "female")
        p.name(person, c.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=id,
            kind="candidate",
            party_id=party(c.party),
            ballot_position=c.number,
            ballot_label=c.name,
        )
        candidacy = key("candidacy", tag, c.number)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=party(c.party),
            is_independent=False,
        )
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=c.votes,
        )
        if c.elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=1)
    for seat in seats:
        if seat.annex not in (3, 4):
            continue
        senate = seat.annex == 3
        home = STATES[state(seat.state)]
        label = title(seat.name)
        number = int(seat.code.split("/")[1])
        name = home if seat.state == "FCT" and senate else f"{home} {label}" if senate else label
        contest(
            p,
            parent,
            f"{'senate' if senate else 'house'}:{seat.code}",
            path=f"{path}/{'senate' if senate else 'house-of-representatives'}/{number:03d}-{changeset.slug(name)}",
            name=(
                f"2019 Senate election, {name}"
                if senate
                else f"2019 House of Representatives election, {label} ({home})"
            ),
            kind="body_seats",
            district=area("ng.senatorial" if senate else "ng.federal", seat.code),
            system=plurality,
            body=SENATE if senate else HOUSE,
            office=None,
            winner=ALIASES.get(seat.party, seat.party),
        )
    return p


def states(seats: list[Seat]) -> Package:
    p = Package(
        f"{SOURCE}/{STATE_DAYS[0]}",
        SOURCE,
        "The 9 March 2019 governorship, state assembly, and FCT area council elections, with their 23 March "
        "supplementary polls: the winning party in each contest",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NIGERIA, COUNTRY}
    cite(p)
    parent, path = election(
        p,
        "state",
        STATE_DAYS[0],
        STATE_DAYS[1],
        "2019 Nigerian general election (Governors, State Houses of Assembly, and FCT Area Councils)",
    )
    plurality = key("electoral_system", "fptp")
    by_name = {name.upper(): code for code, name in STATES.items()}
    for seat in (s for s in seats if s.annex == 2):
        code = by_name[seat.name]
        if code != state(seat.code):
            raise ValueError(f"governor code {seat.code} does not match {seat.name}")
        name = STATES[code]
        contest(
            p,
            parent,
            f"governor:{code}",
            path=f"{path}/governor/{changeset.slug(name)}",
            name=f"2019 {name} State governorship election",
            kind="office",
            district=area("ng.state", code),
            system=plurality,
            body=None,
            office=key("office", "governor", code),
            winner=seat.party,
        )
    for seat in (s for s in seats if s.annex == 5):
        code = state(seat.state)
        name = f"{title(seat.name)} ({STATES[code]})"
        number = int(seat.code.split("/")[1])
        contest(
            p,
            parent,
            f"assembly:{seat.code}",
            path=f"{path}/state-assembly/{number:03d}-{changeset.slug(name)}",
            name=f"2019 {STATES[code]} State House of Assembly election, {title(seat.name)}",
            kind="body_seats",
            district=area("ng.state_constituency", seat.code),
            system=plurality,
            body=key("body", "assembly", code),
            office=None,
            winner=seat.party,
        )
    for seat in (s for s in seats if s.annex == 6):
        number = int(seat.code)
        council = COUNCILS[number - 1]
        contest(
            p,
            parent,
            f"chairman:{number}",
            path=f"{path}/area-council-chairman/{changeset.slug(council)}",
            name=f"2019 {council} Area Council chairmanship election",
            kind="office",
            district=area("ng.area_council", f"{number:02d}"),
            system=plurality,
            body=None,
            office=key("office", "chairman", number),
            winner=seat.party,
        )
    for number, seat in wards(seats):
        council = COUNCILS[number - 1]
        name = title(seat.name)
        contest(
            p,
            parent,
            f"councillor:{number}:{seat.code}",
            path=f"{path}/area-council-councillor/{changeset.slug(council)}/{seat.code}-{changeset.slug(name)}",
            name=f"2019 {council} Area Council councillorship election, {name} ward",
            kind="body_seats",
            district=area("ng.ward", f"{number:02d}/{seat.code}"),
            system=plurality,
            body=key("body", "council", number),
            office=None,
            winner=seat.party,
        )
    return p
