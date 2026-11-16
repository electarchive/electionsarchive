"""Estonian Riigikogu and European Parliament elections from the open data of the State Electoral Office (valimised.ee).

Each election's county report holds every candidate's votes in every municipality, plus the votes cast abroad and,
for the Riigikogu, the electronic votes of each electoral district. Those are the finest figures the office publishes.
"""

import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5
from xml.etree import ElementTree

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ee.valimised"
SITE = "https://www.valimised.ee/sites/default/files/uploads/misc"
API = "https://opendata.valimised.ee/api"
ESTONIA = str(uuid5(package.NAMESPACE, "polity\x1fEE"))
CHAMBER_BODY = "riigikogu"
PARLIAMENT_BODY = "european-parliament"
LICENSE = "CC BY 4.0"
DISTRICT_NAMES = "RK_2023"


@dataclass(frozen=True)
class Spec:
    key: str
    kind: str
    day: date
    zip: str | None = None
    api: str | None = None
    seats: int | None = None

    @property
    def title(self) -> str:
        return ("Riigikogu" if self.kind == "rk" else "European Parliament") + f" election {self.day.year}"


ELECTIONS = {
    spec.key: spec
    for spec in (
        Spec("ep-2014", "ep", date(2014, 5, 25), zip="EP2014_election_result_data.zip", seats=6),
        Spec("rk-2015", "rk", date(2015, 3, 1), zip="RK2015_election_result_data.zip"),
        Spec("ep-2019", "ep", date(2019, 5, 26), zip="EP2019_election_result_data.zip", seats=7),
        Spec("rk-2019", "rk", date(2019, 3, 3), zip="RK2019_election_result_data.zip"),
        Spec("rk-2023", "rk", date(2023, 3, 5), api="RK_2023"),
        Spec("ep-2024", "ep", date(2024, 6, 9), api="EP_2024", seats=7),
    )
}
SYSTEMS = {
    "rk": (
        {
            "formula": "dhondt",
            "threshold": {"level": "party", "scope": "national", "percent": 5},
            "lists": {"order": "open", "vacancy": "next_by_votes"},
        },
        (
            "101 members are elected in 12 districts by open lists. A candidate who wins personal votes equal to the "
            "district's simple quota is elected outright. The district seats are then allocated among the lists that passed "
            "the 5% national threshold by a modified D'Hondt method, and the remaining seats are compensated nationally. "
            "The archive records the declared outcome and does not recompute it."
        ),
    ),
    "ep": (
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        (
            "Estonia is one constituency for the European Parliament, with open lists and no threshold. Seats go to the "
            "lists by the D'Hondt method, and each list's seats go to its candidates in order of personal votes. The "
            "archive records the declared outcome and does not recompute it."
        ),
    ),
}
METHOD = (
    "Counted and published by the State Electoral Office (Vabariigi Valimiskomisjon) in the open data of valimised.ee. "
    "Each municipality figure is what the county report publishes for that municipality; votes cast abroad and, for "
    "the Riigikogu, electronic votes are published only for the whole district and are kept as units of their own. "
    "Each candidate's votes are stored as preference votes under the list, and each list's votes as the list total "
    "the report prints. The source splits votes by paper and electronic ballot and by day; those splits are not "
    "stored here."
)
ABROAD_UNITS = {"Hääled välisriigist", "Välisriigid", "Alaliselt välisriigis elavad valijad"}
OTHER_DISTRICT = "Väljaspoolt ringkonda hääled"
TOTALS = {"Hääli kokku", "Ringkond kokku", "Maakond kokku", "Nimekiri kokku", "Kokku"}
SUBTOTALS = {"E-hääled", "Jaoskonna hääled"}
UNATTRIBUTED = "Votes not attributed to a municipality"
DERIVED = 1


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def url(spec: Spec, name: str) -> str:
    return f"{SITE}/{spec.zip}" if spec.zip else f"{API}/{spec.api}/{name}"


def files(spec: Spec) -> list[str]:
    if spec.zip:
        return [url(spec, "")]
    return [url(spec, name) for name in ("VOTING_RESULT_IN_COUNTIES.xml", "RESULTS.xml", "metadata.json")]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in ELECTIONS)]


def fetch() -> None:
    for spec in ELECTIONS.values():
        for file in files(spec):
            raw.fetch(SOURCE, file, suffix=".zip" if file.endswith(".zip") else ".json" if "json" in file else ".xml")


def parse() -> None:
    geo().write()
    registry().write()
    for name in ELECTIONS:
        election(name).write()


def emit() -> None:
    package.run("load", packages())


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def number(value: str | None) -> int | None:
    return int(value) if value is not None and re.fullmatch(r"-?\d+", value.strip()) else None


# ---------------------------------------------------------------- reading


def plain(data: bytes) -> ElementTree.Element:
    return ElementTree.fromstring(re.sub(rb'\sxmlns="[^"]*"', b"", data, count=1))


def member(spec: Spec, pattern: str) -> ElementTree.Element:
    if spec.zip:
        archive = zipfile.ZipFile(raw.cached(SOURCE, url(spec, "")).path)
        found = [name for name in archive.namelist() if re.search(pattern, name)]
        if len(found) != 1:
            raise LookupError(f"{spec.zip} has {len(found)} members matching {pattern}")
        return plain(archive.read(found[0]))
    name = "RESULTS.xml" if "ELECTION_RESULT" in pattern else "VOTING_RESULT_IN_COUNTIES.xml"
    return plain(raw.cached(SOURCE, url(spec, name)).path.read_bytes())


def text(element: ElementTree.Element | None, tag: str) -> str | None:
    if element is None:
        return None
    found = element.find(tag)
    return None if found is None else found.text


def row(element: ElementTree.Element | None) -> list[tuple[str, int | None]]:
    if element is None:
        return []
    return [(clean(text(c, "name")), number(text(c, "value"))) for c in element.iter("votesDistributionCell")]


def municipalities(spec: Spec) -> dict[str, str]:
    """Municipality name to the EHAK code the office gives it in this election."""
    if spec.api:
        meta = json.loads(raw.cached(SOURCE, url(spec, "metadata.json")).path.read_text())
        return {clean(item["name"]): item["ehak"] for item in meta["municipalities"]}
    archive = zipfile.ZipFile(raw.cached(SOURCE, url(spec, "")).path)
    found: dict[str, str] = {}
    for name in archive.namelist():
        if re.search(r"VOTING_RESULT_IN_PARISH", name):
            head = archive.read(name)[:1200].decode("utf-8", "replace")
            unit = re.search(r"<adminUnitName>([^<]*)</adminUnitName>\s*<ehakCode>([^<]*)</ehakCode>", head)
            if unit:
                found[clean(unit.group(1))] = unit.group(2).strip()
    return found


@dataclass
class Candidate:
    regno: int
    id: str
    forename: str
    surname: str
    votes: dict[str, int]


@dataclass
class Entry:
    code: str
    name: str
    total: dict[str, int]
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def independents(self) -> bool:
        return self.name.startswith("Üksik")


@dataclass
class Block:
    number: str
    name: str
    mandates: int | None
    valid: dict[str, int]
    registered: dict[str, int]
    entries: dict[str, Entry]
    stated_valid: int
    derived: set[tuple[str, int]] = field(default_factory=set)


def unit_cells(cells: list[tuple[str, int | None]], solo: str | None = None) -> tuple[int, dict[str, int]]:
    """A county, district or national row: one cell is the total, the others are its units.

    Electronic votes and the paper votes of polling stations are subtotals of the municipal figures, so they are not
    units."""
    total = next((value for name, value in cells if name in TOTALS), cells[0][1]) or 0
    units = {name: value or 0 for name, value in cells if name not in TOTALS and name not in SUBTOTALS}
    if not units and solo is not None:
        units = {solo: total}
    return total, units


def party_total(party: ElementTree.Element) -> list[tuple[str, int | None]]:
    single = party.find("totalRow")
    if single is not None:
        return row(single)
    for line in party.iter("totalRow"):
        if "kokku" in clean(text(line, "rowName")).lower():
            return row(line)
    if clean(text(party, "partyName")).startswith("Üksik"):
        return []
    raise LookupError(f"{text(party, 'partyName')} has no list total")


def special(name: str) -> bool:
    return name in ABROAD_UNITS or name == OTHER_DISTRICT


def entries_of(
    block: ElementTree.Element,
    tag: str,
    into: dict[str, Entry],
    keep: Callable[[str], bool] | None = None,
    solo: str | None = None,
) -> None:
    for party in block.find(tag) or []:
        name = clean(text(party, "partyName"))
        code = clean(text(party, "partyCode")) or name
        entry = into.setdefault(name, Entry(code, name, {}))
        totals = party_total(party)
        per_unit = unit_cells(totals, solo)[1] if totals else {}
        for unit, value in per_unit.items():
            if keep is not None and not keep(unit):
                continue
            entry.total[unit] = entry.total.get(unit, 0) + value
        index = {c.id: c for c in entry.candidates}
        for person in party.find("candidates") or []:
            ident = clean(text(person, "candidateId"))
            _, votes = unit_cells(row(person.find("votesDistributionRow")), solo)
            found = index.get(ident)
            if found is None:
                found = Candidate(
                    int(text(person, "candidateRegNumber") or 0),
                    ident,
                    clean(text(person, "forename")),
                    clean(text(person, "surname")),
                    {},
                )
                entry.candidates.append(found)
            for unit, value in votes.items():
                if keep is None or keep(unit):
                    found.votes[unit] = found.votes.get(unit, 0) + value


def abroad(county: ElementTree.Element, label: str, entries: dict[str, Entry]) -> None:
    """Voters living abroad are one block whose first cell is the unit's votes; the other cells are subtotals."""
    for party in county.find("votesDistributionByParties") or []:
        name = clean(text(party, "partyName"))
        entry = entries.setdefault(name, Entry(clean(text(party, "partyCode")) or name, name, {}))
        listed = party_total(party)
        if listed:
            entry.total[label] = unit_cells(listed)[0]
        for person in party.find("candidates") or []:
            ident = clean(text(person, "candidateId"))
            found = next((c for c in entry.candidates if c.id == ident), None)
            if found is None:
                found = Candidate(
                    int(text(person, "candidateRegNumber") or 0),
                    ident,
                    clean(text(person, "forename")),
                    clean(text(person, "surname")),
                    {},
                )
                entry.candidates.append(found)
            found.votes[label] = unit_cells(row(person.find("votesDistributionRow")))[0]


def valid_line(element: ElementTree.Element) -> ElementTree.Element:
    line = element.find("totalRow")
    if line is not None:
        return line
    return next(t for t in element.find("totalRows") or [] if "kokku" in clean(text(t, "rowName")).lower())


def stated(root: ElementTree.Element) -> dict[str, int]:
    return {clean(text(r, "name")): int(text(r, "value") or 0) for r in root.iter("statisticsRow")}


def blocks(spec: Spec) -> tuple[list[Block], dict[str, int]]:
    root = member(spec, r"VOTING_RESULT_IN_COUNTIES")
    result = root.find(".//votingResult")
    if result is None:
        raise LookupError(f"{spec.key} has no voting result")
    overall = stated(result)
    out: list[Block] = []
    if spec.kind == "rk":
        for district in result.iter("district"):
            number_ = clean(text(district, "districtNumber"))
            entries: dict[str, Entry] = {}
            entries_of(district, "votesDistributionByParties", entries, special)
            valid_total, shown = unit_cells(row(valid_line(district)))
            valid = {unit: value for unit, value in shown.items() if special(unit)}
            for county in district.find("counties") or []:
                entries_of(county, "votesDistributionByParties", entries)
                valid.update(unit_cells(row(valid_line(county)))[1])
            derived: set[tuple[str, int]] = set()
            if OTHER_DISTRICT not in valid and any(OTHER_DISTRICT in e.total for e in entries.values()):
                valid[OTHER_DISTRICT] = sum(e.total.get(OTHER_DISTRICT, 0) for e in entries.values())
                derived.add((OTHER_DISTRICT, VALID))
            residual(district.find("votesDistributionByParties"), valid_line(district), entries, valid)
            out.append(
                Block(
                    number_,
                    clean(text(district, "districtName")),
                    number(text(district, "districtNumberOfMandates")),
                    valid,
                    {},
                    entries,
                    valid_total,
                    derived,
                )
            )
    else:
        entries = {}
        valid: dict[str, int] = {}
        registered: dict[str, int] = {}
        for county in result.find("counties") or []:
            label = clean(text(county, "countyName"))
            if label in ABROAD_UNITS:
                abroad(county, label, entries)
                valid[label] = unit_cells(row(valid_line(county)))[0]
                continue
            entries_of(county, "votesDistributionByParties", entries, solo=label)
            for line in county.find("totalRows") or []:
                kind = clean(text(line, "rowName"))
                _, per_unit = unit_cells(row(line), label)
                if kind == "KÕIK KOKKU":
                    valid.update(per_unit)
                elif kind == "Valijate arv":
                    registered.update(per_unit)
        state = result.find("stateVotesDistributionByType")
        if state is not None:
            line = state.find("totalRow")
            if line is None:
                line = next(iter(state.find("totalRows") or []))
            residual(state.find("stateVotesDistributionByParties"), line, entries, valid)
        derived = set()
        gap = overall["Valijaid"] - sum(registered.values())
        if gap < 0:
            raise ValueError(f"municipalities hold {-gap} more voters than the national total")
        if gap:
            target = (
                UNATTRIBUTED if UNATTRIBUTED in valid else next((u for u in valid if u in ABROAD_UNITS), UNATTRIBUTED)
            )
            registered[target] = registered.get(target, 0) + gap
            derived.add((target, REGISTERED))
        out.append(
            Block(
                "1",
                "Estonia",
                spec.seats,
                valid,
                registered,
                entries,
                overall["Kehtivaid sedeleid"],
                derived,
            )
        )
    return out, overall


def residual(
    container: ElementTree.Element | None,
    line: ElementTree.Element,
    entries: dict[str, Entry],
    valid: dict[str, int],
) -> None:
    """The report prints each list's and candidate's total for the whole contest as well as the municipal figures.
    The difference is votes the report assigns to no municipality, and is stored as a derived unit of its own."""
    gap = unit_cells(row(line))[0] - sum(valid.values())
    if gap < 0:
        raise ValueError(f"municipalities hold {-gap} more votes than the contest total")
    if gap:
        valid[UNATTRIBUTED] = gap
    for party in container or []:
        entry = entries[clean(text(party, "partyName"))]
        listed = party_total(party)
        if listed:
            lost = unit_cells(listed)[0] - sum(entry.total.values())
            if lost < 0:
                raise ValueError(f"{entry.name}: municipalities hold {-lost} more votes than the contest total")
            if lost:
                entry.total[UNATTRIBUTED] = lost
        for person in party.find("candidates") or []:
            mine = next(c for c in entry.candidates if c.id == clean(text(person, "candidateId")))
            lost = unit_cells(row(person.find("votesDistributionRow")))[0] - sum(mine.votes.values())
            if lost < 0:
                raise ValueError(f"{mine.id}: municipalities hold {-lost} more votes than the contest total")
            if lost:
                mine.votes[UNATTRIBUTED] = lost


@dataclass
class Declared:
    votes: dict[str, int]
    elected: set[str]
    districts: dict[str, str]
    seats: dict[str, int]


def declared(spec: Spec) -> Declared:
    root = member(spec, r"ELECTION_RESULT")
    votes: dict[str, int] = {}
    elected: set[str] = set()
    districts: dict[str, str] = {}
    seats: dict[str, int] = {}
    for person in root.iter("candidate"):
        ident = text(person, "candidateId") or text(person, "applicationId")
        if ident is None or text(person, "reserved") is None:
            continue
        if text(person, "elected") == "true":
            elected.add(ident.strip())
        count = text(person, "candidateVotes") or text(person, "votes")
        if count is not None:
            votes[ident.strip()] = int(count)
        if text(person, "districtNumber") is not None:
            districts[ident.strip()] = clean(text(person, "districtNumber"))
    for party in root.iter("party"):
        name = clean(text(party, "partyName") or text(party, "name"))
        count = text(party, "partyNumberOfMandates") or text(party, "numberOfMandates")
        if name and count is not None:
            seats[name] = seats.get(name, 0) + int(count)
    return Declared(votes, elected, districts, seats)


# ---------------------------------------------------------------- packages

VOTES, REGISTERED, CAST, VALID, PREFERENCE = 1, 2, 3, 4, 7


def source_rows(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Riigi Valimisteenistus",
        title="Estonian election results, open data",
        url="https://www.valimised.ee/en/archive/open-data-estonian-elections",
        license=LICENSE,
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Riigi Valimisteenistus, valimised.ee",
    )


SOURCE_ID = key("source", "valimised")


def district_names() -> dict[str, str]:
    spec = ELECTIONS["rk-2023"]
    return {
        clean(text(d, "districtNumber")): clean(text(d, "districtName"))
        for d in member(spec, "COUNTIES").iter("district")
    }


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Estonian municipalities and Riigikogu electoral districts as the State Electoral Office names them",
    )
    p.external.add(ESTONIA)
    source_rows(p)
    for spec in ELECTIONS.values():
        for file in files(spec):
            p.artifact(raw.cached(SOURCE, file), SOURCE_ID)
    for code, rank, kind, noun, system in (
        ("ee.ringkond", 1, "electoral_district", "electoral district", None),
        ("ee.vald", 2, "administrative", "municipality", "ehak"),
        ("ee.unit", 3, "precinct", "reporting unit", None),
    ):
        p.add("scheme", code=code, polity_id=ESTONIA, kind=kind, rank=rank, noun=noun, code_system=system)
    for number_, name in sorted(district_names().items(), key=lambda item: int(item[0])):
        id = area("ee.ringkond", number_)
        p.add("area", id=id, scheme_code="ee.ringkond")
        p.add("area_code", area_id=id, code=number_)
        p.name(id, name, "et", "official")
    names: dict[str, dict[str, int]] = {}
    for spec in ELECTIONS.values():
        for name, code in municipalities(spec).items():
            seen = names.setdefault(code, {})
            seen[name] = max(seen.get(name, 0), spec.day.year)
    for code, found in sorted(names.items()):
        id = area("ee.vald", code)
        p.add("area", id=id, scheme_code="ee.vald")
        p.add("area_code", area_id=id, code=code)
        latest = max(found.values())
        for name, year in sorted(found.items()):
            p.name(id, name, "et", "official", year == latest)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Estonian parties, the Riigikogu, and electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(ESTONIA)
    for spec in ELECTIONS.values():
        for file in files(spec):
            p.uses(raw.cached(SOURCE, file))
    body = key("body", CHAMBER_BODY)
    p.add("body", id=body, polity_id=ESTONIA, kind="unicameral")
    p.name(body, "Riigikogu", "et", "official")
    p.name(body, "Parliament of Estonia", "en", "official", False)
    delegation = key("body", PARLIAMENT_BODY)
    p.add("body", id=delegation, polity_id=ESTONIA, kind="unicameral")
    p.name(delegation, "Estonian members of the European Parliament", "en", "official")
    p.name(delegation, "Euroopa Parlamendi Eesti liikmed", "et", "official", False)
    for kind, (rules, method) in SYSTEMS.items():
        id = key("electoral_system", kind)
        p.add(
            "electoral_system", id=id, polity_id=ESTONIA, valid_from=None, valid_to=None, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    parties: dict[str, dict[str, int]] = {}
    for spec in ELECTIONS.values():
        found, _ = blocks(spec)
        for block in found:
            for entry in block.entries.values():
                if not entry.independents:
                    seen = parties.setdefault(entry.code, {})
                    seen[entry.name] = max(seen.get(entry.name, 0), spec.day.year)
    for code, found in sorted(parties.items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=ESTONIA)
        p.add("external_id", entity_id=id, scheme="ee.valimised.party", value=code)
        latest = max(found.values())
        for name, year in sorted(found.items()):
            p.name(id, name, "et", "official", year == latest)
    return p


def party(code: str) -> str:
    return key("party", code)


def election(name: str) -> Package:
    spec = ELECTIONS[name]
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {spec.day.year} {'Riigikogu' if spec.kind == 'rk' else 'European Parliament'} election in Estonia by municipality",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ESTONIA)
    for file in files(spec):
        p.uses(raw.cached(SOURCE, file))
    found, overall = blocks(spec)
    result = declared(spec)
    codes = municipalities(spec)
    day = spec.day
    chamber = spec.kind == "rk"
    body = key("body", CHAMBER_BODY if chamber else PARLIAMENT_BODY)
    system = key("electoral_system", spec.kind)
    ident = key("election", name)
    p.add("election", id=ident, polity_id=ESTONIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(
        ident, f"{day.year} Estonian {'parliamentary' if chamber else 'European Parliament'} election", "en", "official"
    )
    p.name(ident, f"{'Riigikogu' if chamber else 'Euroopa Parlamendi'} valimised {day.year}", "et", "native", False)
    path = f"ee/{day}-{'riigikogu' if chamber else 'european-parliament'}"
    p.add("slug", path=path, entity_id=ident, is_current=True)
    p.add("note", id=key("note", ident), entity_id=ident, kind="methodology", body_md=METHOD, lang="en")
    units: dict[str, str] = {}

    def unit(district: str, label_: str) -> str:
        if label_ in codes:
            code, relation, place, kind = f"{day}:{codes[label_]}", "equals", area("ee.vald", codes[label_]), "standard"
        elif label_ in ABROAD_UNITS:
            code, relation, place, kind = f"{day}:abroad:{district}", "unlocated", None, "abroad_mission"
        elif label_ == UNATTRIBUTED:
            code, relation, place, kind = f"{day}:unattributed", "unlocated", None, "aggregate"
        elif label_ == OTHER_DISTRICT:
            code, relation, place, kind = (
                f"{day}:other-district:{district}",
                "within",
                area("ee.ringkond", district),
                "aggregate",
            )
        else:
            raise LookupError(f"{name}: unknown unit {label_!r}")
        id = units.get(code)
        if id is None:
            id = units[code] = key("unit", code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=ESTONIA,
                scheme_code="ee.unit",
                code=code,
                area_relation=relation,
                area_id=place,
                kind=kind,
            )
            p.name(id, label_, "et", "official")
        return id

    sets: dict[str, str] = {}
    national_valid = 0
    for block in found:
        number_ = block.number
        contest = key("contest", name, number_)
        elected = sum(1 for e in block.entries.values() for c in e.candidates if c.id in result.elected)
        if not chamber and elected != spec.seats:
            raise ValueError(f"{name}: {elected} members elected, expected {spec.seats}")
        p.add(
            "contest",
            id=contest,
            election_id=ident,
            kind="list_allocation",
            body_id=body,
            district_area_id=area("ee.ringkond", number_) if chamber else None,
            magnitude=elected,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        title = block.name
        p.name(contest, title, "et", "official")
        p.add(
            "slug",
            path=f"{path}/{changeset.slug(title)}" if chamber else f"{path}/estonia",
            entity_id=contest,
            is_current=True,
        )
        result_set = sets[number_] = key("result_set", name, number_)
        p.add(
            "result_set",
            id=result_set,
            election_id=ident,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        if chamber:
            p.add(
                "note",
                id=key("note", result_set, "seats"),
                entity_id=result_set,
                kind="methodology",
                body_md=(
                    f"The district's basic mandates were {block.mandates}. The seats stored here are the members "
                    "declared elected from the district's lists, which include the compensation mandates "
                    "allocated nationally."
                ),
                lang="en",
            )
        everything = {*block.valid, *block.registered}
        for label_ in everything:
            u = unit(number_, label_)
            if label_ in block.valid:
                put(p, result_set, u, None, VALID, block.valid[label_], derived(block, label_, VALID))
            if label_ in block.registered:
                put(p, result_set, u, None, REGISTERED, block.registered[label_], derived(block, label_, REGISTERED))
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=block.stated_valid)
        if block.registered:
            p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=overall["Valijaid"])
        national_valid += block.stated_valid
        for entry in block.entries.values():
            if entry.independents:
                parent = key("option", name, number_, "independents")
                p.add(
                    "contest_option",
                    id=parent,
                    contest_id=contest,
                    kind="independents",
                    ballot_label="Independent candidates",
                )
                wins = 0
                together: dict[str, int] = {}
                for person in entry.candidates:
                    for label_, value in person.votes.items():
                        together[label_] = together.get(label_, 0) + value
                for label_, value in together.items():
                    put(p, result_set, unit(number_, label_), parent, VOTES, value, True)
            else:
                parent = key("option", name, number_, "list", entry.code)
                p.add(
                    "contest_option",
                    id=parent,
                    contest_id=contest,
                    kind="list",
                    party_id=party(entry.code),
                    ballot_label=entry.name,
                )
                for label_, value in entry.total.items():
                    put(p, result_set, unit(number_, label_), parent, VOTES, value, label_ == UNATTRIBUTED)
                wins = 0
            for person in entry.candidates:
                tag = f"{name}:{person.id}"
                full = clean(f"{person.forename} {person.surname}").title()
                child = key("option", name, number_, "candidate", person.id)
                member = None if entry.independents else party(entry.code)
                p.add(
                    "contest_option",
                    id=child,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=parent,
                    party_id=member,
                    ballot_position=person.regno,
                    ballot_label=full,
                )
                who = key("person", tag)
                p.add("person", id=who, birth_precision=None)
                p.name(who, full, "et", "official")
                cand = key("candidacy", tag)
                p.add(
                    "candidacy",
                    id=cand,
                    person_id=who,
                    option_id=parent,
                    role="list_member",
                    list_position=person.regno,
                    ballot_name=full,
                    member_of_party_id=member,
                    is_independent=entry.independents,
                )
                for label_, value in person.votes.items():
                    put(p, result_set, unit(number_, label_), child, PREFERENCE, value, label_ == UNATTRIBUTED)
                if person.id in result.votes:
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=child,
                        measure=PREFERENCE,
                        value=result.votes[person.id],
                    )
                if person.id in result.elected:
                    wins += 1
                    p.add("outcome", result_set_id=result_set, option_id=parent, candidacy_id=cand, kind="elected")
            if wins:
                p.add("outcome", result_set_id=result_set, option_id=parent, kind="won", seats=wins)
    checks(spec, found, overall, result, national_valid)
    return p


def derived(block: Block, label: str, measure: int) -> bool:
    return label == UNATTRIBUTED or (label, measure) in block.derived


def put(p: Package, result_set: str, unit_id: str, option: str | None, measure: int, value: int, derived: bool) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit_id,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=DERIVED if derived else 0,
        value=value,
    )


def checks(spec: Spec, found: list[Block], overall: dict[str, int], result: Declared, valid: int) -> None:
    if valid != overall["Kehtivaid sedeleid"]:
        raise ValueError(
            f"{spec.key}: contests hold {valid} valid votes, the report states {overall['Kehtivaid sedeleid']}"
        )
    for block in found:
        listed = sum(block.valid.values())
        if listed != block.stated_valid:
            raise ValueError(
                f"{spec.key} {block.number}: units hold {listed} valid votes, the district total is {block.stated_valid}"
            )
        for entry in block.entries.values():
            for person in entry.candidates:
                declared_votes = result.votes.get(person.id)
                if declared_votes is not None and declared_votes != sum(person.votes.values()):
                    raise ValueError(
                        f"{spec.key} {person.id}: {sum(person.votes.values())} votes against {declared_votes}"
                    )
