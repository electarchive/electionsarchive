"""Estonian Riigikogu and European Parliament elections from the open data of the State Electoral Office (valimised.ee).

Each election's county report holds every candidate's votes in every municipality, plus the votes cast abroad and,
for the Riigikogu, the electronic votes of each electoral district. Those are the finest figures the office publishes.
"""

import html
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from uuid import uuid5
from xml.etree import ElementTree

import shapely

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ee import plans

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
    html: bool = False

    @property
    def title(self) -> str:
        return ("Riigikogu" if self.kind == "rk" else "European Parliament") + f" election {self.day.year}"


ELECTIONS = {
    spec.key: spec
    for spec in (
        Spec("rk-1992", "rk", date(1992, 9, 20), seats=101, html=True),
        Spec("rk-1995", "rk", date(1995, 3, 5), seats=101, html=True),
        Spec("rk-1999", "rk", date(1999, 3, 7), seats=101, html=True),
        Spec("rk-2003", "rk", date(2003, 3, 2), seats=101, html=True),
        Spec("rk-2007", "rk", date(2007, 3, 4), seats=101, html=True),
        Spec("rk-2011", "rk", date(2011, 3, 6), seats=101, html=True),
        Spec("ep-2004", "ep", date(2004, 6, 13), seats=6, html=True),
        Spec("ep-2009", "ep", date(2009, 6, 7), seats=6, html=True),
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
VVK = "https://www.vvk.ee/varasemad"
EP2004 = f"{VVK}/ep04/tulemus"
EP2009 = f"{VVK}/ep09/index.php?id="
COUNTIES_2009 = (
    "0037", "0039", "0044", "0049", "0051", "0057", "0059", "0065", "0067", "0070", "0074", "0078", "0082", "0084",
    "0086", "0784", "0795",
)  # fmt: skip
TALLINN, TARTU = "0784", "0795"
MERGED_2005 = {
    "Jõhvi linn": "0251",
    "Türi linn": "0835",
    "Tapa linn": "0790",
    "Tamsalu linn": "0786",
    "Kilingi-Nõmme linn": "0710",
    "Suure-Jaani linn": "0758",
    "Loksa vald": "0353",
    "Kabala vald": "0835",
    "Lehtse vald": "0790",
    "Oisu vald": "0835",
    "Avanduse vald": "0926",
    "Saksi vald": "0790",
    "Tali vald": "0710",
    "Olustvere vald": "0758",
    "Vastemõisa vald": "0758",
}  # 2004 units abolished in the 2005 mergers, to the EHAK code of the municipality that took them in
EVOTES = "E-hääled"
ABROAD_2009 = "Alaliselt välisriigis elavad valijad"
ABROAD_2004 = "Välisriigid"
INDEPENDENTS = "Üksikkandidaat"
METHOD_RK_VVK = (
    "Counted and published by the National Electoral Committee (Vabariigi Valimiskomisjon) on vvk.ee. Each "
    "candidate's votes are stored as preference votes under the list, and each list's votes as the list total the "
    "pages print. Votes are stored in the finest units the pages print: municipalities (in 2011 the polling "
    "divisions within them), the polling divisions of the one city or city district whose divisions the pages list "
    "in earlier years, city districts, and the votes cast abroad. Electronic votes, from 2007, are published only "
    "for the whole district and are kept as a unit of their own. Where the pages disagree with one another, the "
    "archive follows the district tables, which add up to the declared totals. The electoral-system record is "
    "shared with later Riigikogu elections and describes its present rules; the archive records each election's "
    "declared outcome and does not recompute it."
)
METHOD_VVK = (
    "Counted and published by the National Electoral Committee (Vabariigi Valimiskomisjon) on vvk.ee. Each "
    "municipality figure is what the committee's result tables print for that municipality. Tallinn is split into its "
    "city districts in 2009, and Tartu into its polling divisions in both years. Votes cast by voters living "
    "abroad, and in 2009 the electronic votes, are published only for the whole country and are kept as units of "
    "their own. Each list's votes are stored as the list total the tables print. Where the tables print preference "
    "votes for the candidates of a list, they are stored under it."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def district_area(spec: Spec, number: str) -> str:
    """The Riigikogu districts of 2003 on are those of the 2023 plan; earlier plans have areas of their own."""
    return area("ee.ringkond", number if spec.day.year >= 2003 else f"{spec.day.year}:{number}")


def url(spec: Spec, name: str) -> str:
    return f"{SITE}/{spec.zip}" if spec.zip else f"{API}/{spec.api}/{name}"


def files(spec: Spec) -> list[str]:
    if spec.html:
        return html_files(spec)
    if spec.zip:
        return [url(spec, "")]
    return [url(spec, name) for name in ("VOTING_RESULT_IN_COUNTIES.xml", "RESULTS.xml", "metadata.json")]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in ELECTIONS)]


def fetch() -> None:
    for file in plans.urls():
        raw.fetch(SOURCE, file, suffix=".zip")
    for spec in ELECTIONS.values():
        if spec.html and spec.kind == "rk" and spec.day.year == 2011:
            for file in layout(spec).pages:
                raw.fetch(SOURCE, file, suffix=".html")
        for file in files(spec):
            suffix = (
                ".html" if spec.html else ".zip" if file.endswith(".zip") else ".json" if "json" in file else ".xml"
            )
            raw.fetch(SOURCE, file, suffix=suffix)


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
    if spec.html:
        return html_municipalities(spec)
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
    stated_valid: int | None
    derived: set[tuple[str, int]] = field(default_factory=set)
    netted: set[str] = field(default_factory=set)
    places: dict[str, tuple[str, str, str | None, str]] = field(default_factory=dict)
    withheld: list[str] = field(default_factory=list)
    cast: dict[str, int] = field(default_factory=dict)


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


@cache
def blocks(spec: Spec) -> tuple[list[Block], dict[str, int]]:
    if spec.html:
        return html_blocks(spec)
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
    lists: dict[str, int] = field(default_factory=dict)


def declared(spec: Spec) -> Declared:
    if spec.html:
        return html_declared(spec)
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


# ---------------------------------------------------------------- reading the committee's pages


def html_files(spec: Spec) -> list[str]:
    if spec.kind == "rk":
        return rk_files(spec)
    if spec.day.year == 2004:
        pages = ("osavott", "tulemus", "mandaat_jaotus", "liikmed")
        return [
            *(f"{EP2004}/{page}.html" for page in pages),
            *(f"{EP2004}/{n}000000_bottom.html" for n in range(1, 18)),
        ]
    return [
        f"{VVK}/ep09/",
        *(f"{EP2009}{page}" for page in (11191, 11204)),
        *(f"{EP2009}11204&aunit_c={code}&popup=1" for code in COUNTIES_2009),
        *(f"{EP2009}{page}" for page in (11226, 11229, 11250)),
    ]


def old_page(spec: Spec, url: str) -> str:
    data = raw.cached(SOURCE, url).path.read_bytes()
    return data.decode("latin-1" if spec.day.year == 2004 else "utf-8")


def lines(page: str) -> list[str]:
    stripped = re.sub(r"(?s)<(script|style).*?</\1>", "", page, flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", stripped))
    return [line for line in (clean(line) for line in text.split("\n")) if line]


def table_rows(page: str) -> list[list[str]]:
    return [
        [
            clean(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
        ]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.DOTALL | re.IGNORECASE)
    ]


def count(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def after(found: list[str], label: str) -> int:
    return count(found[found.index(label) + 1])


def person(full: str, regno: int, ident: str) -> Candidate:
    words = full.split()
    return Candidate(regno, ident, " ".join(words[:-1]), words[-1], {})


def candidate_of(entry: Entry, regno: int, ident: str, full: str) -> Candidate:
    found = next((c for c in entry.candidates if c.id == ident), None)
    if found is None:
        found = person(full, regno, ident)
        entry.candidates.append(found)
    return found


@cache
def town_codes_2009() -> tuple[dict[str, str], dict[str, str]]:
    """The municipalities and the Tallinn city districts of 2009 with the codes the committee's pages give them."""
    spec = ELECTIONS["ep-2009"]
    towns: dict[str, str] = {}
    districts: dict[str, str] = {}
    for code in COUNTIES_2009:
        page = old_page(spec, f"{EP2009}11204&aunit_c={code}&popup=1")
        options = re.findall(r'<option\s+value="[^"]*aunit_c=(\d+)[^"]*"[^>]*>([^<]*)</option>', page)
        for index, (ehak, name) in enumerate(options):
            name = clean(html.unescape(name))
            if index == 0 and name.endswith("maakond"):
                continue
            (districts if code == TALLINN and index > 0 else towns)[name] = ehak
    return towns, districts


def old_head(spec: Spec, n: int) -> list[str]:
    rows = [r for r in table_rows(old_page(spec, f"{EP2004}/{n}000000_bottom.html")) if len(r) > 2]
    return rows[0]


def html_municipalities(spec: Spec) -> dict[str, str]:
    if spec.kind == "rk":
        return rk_municipalities(spec)
    towns = town_codes_2009()[0]
    if spec.day.year == 2009:
        return dict(towns)
    names = {"Tallinn"}
    for n in range(1, 16):
        names.update(old_head(spec, n)[3:])
    return {name: towns[name] for name in sorted(names) if name in towns}


def unit_values(n: int, row: list[str]) -> list[int]:
    """The 2004 table of Tallinn prints the votes in the city with those cast abroad as 'of which'."""
    if n == 16:
        total, away = count(row[2]), count(row[3])
        return [total - away, away]
    return [count(cell) for cell in row[3:]]


def mandate_rows(spec: Spec) -> list[tuple[int, str, int, int]]:
    """Registration number, list abbreviation, comparison number, and personal votes in the 2004 allocation table."""
    found = lines(old_page(spec, f"{EP2004}/mandaat_jaotus.html"))
    out: list[tuple[int, str, int, int]] = []
    for i, line in enumerate(found):
        match = re.fullmatch(r"nr\. (\d+)", line)
        if match:
            out.append((int(match.group(1)), found[i - 2], count(found[i - 3].split(",")[0]), count(found[i - 1])))
    return out


def legend_2009(spec: Spec) -> dict[str, str]:
    page = old_page(spec, f"{EP2009}11191")
    return {
        clean(code): clean(html.unescape(name))
        for code, name in re.findall(r'<td><b>([^<]+)</b></td><td class="chart_name">([^<]+)</td>', page)
    }


@cache
def known_codes() -> dict[str, str]:
    """List name to the code the office prints for it, from the open data where the list stood again later."""
    out: dict[str, str] = {}
    for spec in ELECTIONS.values():
        if not spec.html:
            for entry in blocks(spec)[0][0].entries.values():
                if not entry.independents:
                    out[entry.name] = entry.code
    for code, name in legend_2009(ELECTIONS["ep-2009"]).items():
        if code.isupper():
            out.setdefault(name, code)
    return out


def old_entry(entries: dict[str, Entry], name: str) -> Entry:
    return entries.setdefault(name, Entry("", name, {}))


def read_2004(spec: Spec) -> Block:
    entries: dict[str, Entry] = {}
    valid: dict[str, int] = {}
    places: dict[str, tuple[str, str, str | None, str]] = {}
    codes = municipalities(spec)
    day = spec.day
    for n in range(1, 18):
        rows = [r for r in table_rows(old_page(spec, f"{EP2004}/{n}000000_bottom.html")) if len(r) > 2]
        head, body = rows[0], rows[1:]
        if n == 16:
            labels = ["Tallinn", ABROAD_2004]
        elif n == 17:
            labels = [f"Tartu linn {label}" for label in head[3:]]
            for label in labels:
                places[label] = (f"{day}:{TARTU}:{label.split()[-1]}", "within", area("ee.vald", TARTU), "standard")
        else:
            labels = head[3:]
        pending: list[list[str]] = []
        for row in body:
            name = row[1]
            if row[0].isdigit():
                pending.append(row)
                continue
            if name.startswith("Osav"):
                continue
            per = dict(zip(labels, unit_values(n, row), strict=True))
            if name == "KOKKU:":
                valid.update(per)
                name = INDEPENDENTS
                entry = old_entry(entries, name)
                entry.code = "Üksik."
            else:
                entry = old_entry(entries, name)
                entry.total.update(per)
            for line in pending:
                found = candidate_of(entry, int(line[0]), line[0], line[1])
                found.votes.update(zip(labels, unit_values(n, line), strict=True))
            pending = []
    for label in valid:
        if label in codes or label in places or label in ABROAD_UNITS:
            continue
        if label in MERGED_2005:
            places[label] = (f"{day}:{label}", "within", area("ee.vald", MERGED_2005[label]), "standard")
        else:
            places[label] = (f"{day}:{label}", "unlocated", None, "standard")
    hints = {regno: code for regno, code, _, _ in mandate_rows(spec)}
    known = known_codes()
    for entry in entries.values():
        if not entry.independents:
            entry.code = known.get(entry.name) or hints[entry.candidates[0].regno]
    stated = after(lines(old_page(spec, f"{EP2004}/tulemus.html")), "Kehtivaid sedeleid:")
    return Block("1", "Estonia", spec.seats, valid, {}, entries, stated, {("Tallinn", VALID)}, {"Tallinn"}, places)


def read_2009(spec: Spec) -> Block:
    names = legend_2009(spec)
    persons = {name for code, name in names.items() if not code.isupper()}
    entries: dict[str, Entry] = {}
    independents = Entry("Üksik.", INDEPENDENTS, {})
    valid: dict[str, int] = {}
    places: dict[str, tuple[str, str, str | None, str]] = {}
    day = spec.day
    districts = town_codes_2009()[1]

    def add(name: str, per: dict[str, int]) -> None:
        if name in persons:
            candidate_of(independents, 0, changeset.slug(name), name).votes.update(per)
        else:
            old_entry(entries, name).total.update(per)

    for code in COUNTIES_2009:
        page = old_page(spec, f"{EP2009}11204&aunit_c={code}&popup=1")
        rows = table_rows(page[page.index('class="ep_res"') :])
        head = rows[0]
        if code == TARTU:
            labels, start = [f"Tartu linn {label}" for label in head[1:]], 1
            for label in labels:
                places[label] = (f"{day}:{TARTU}:{label.split()[-1]}", "within", area("ee.vald", TARTU), "standard")
        else:
            labels, start = head[2:], 2
        if code == TALLINN:
            for label in labels:
                places[label] = (f"{day}:{districts[label]}", "within", area("ee.vald", TALLINN), "standard")
        for row in rows[1:]:
            if row[0].startswith("Osav"):
                continue
            per = dict(zip(labels, (count(cell) for cell in row[start:]), strict=True))
            if row[0] == "Kokku:":
                valid.update(per)
            else:
                add(row[0], per)
    for page_id, label in ((11226, EVOTES), (11229, ABROAD_2009)):
        page = old_page(spec, f"{EP2009}{page_id}")
        for row in table_rows(page[page.index("Erakond või üksikkandidaat") :]):
            if len(row) == 2 and re.fullmatch(r"\d+", row[1]):
                if row[0] == "Kokku:":
                    valid[label] = count(row[1])
                else:
                    add(row[0], {label: count(row[1])})
    known = known_codes()
    for entry in entries.values():
        entry.code = known[entry.name]
    for full, party_name in elected_2009(spec):
        entry = independents if party_name == INDEPENDENTS else entries[party_name]
        candidate_of(entry, 0, changeset.slug(full), full)
    entries[INDEPENDENTS] = independents
    stated = after(lines(old_page(spec, f"{EP2009}11204")), "Kehtivaid sedeleid:")
    return Block("1", "Estonia", spec.seats, valid, {}, entries, stated, set(), set(), places)


def elected_2009(spec: Spec) -> list[tuple[str, str]]:
    """The members the committee names, each with the list that won the seat."""
    text = " ".join(lines(old_page(spec, f"{EP2009}11250")))
    text = text[text.rindex("Euroopa Parlamendi liikmed") :]
    parties = [name for code, name in legend_2009(spec).items() if code.isupper()] + [INDEPENDENTS]
    pattern = "(" + "|".join(re.escape(name) for name in sorted(parties, key=len, reverse=True)) + ")"
    out: list[tuple[str, str]] = []
    current = ""
    for chunk in re.split(pattern, text):
        if chunk in parties:
            current = chunk
            continue
        out.extend((name.strip(), current) for name in re.findall(r"\d\)\s*([^\d)]+?)\s*(?=\d\)|$)", chunk))
    return out


def html_blocks(spec: Spec) -> tuple[list[Block], dict[str, int]]:
    if spec.kind == "rk":
        return rk_blocks(spec)
    block = read_2004(spec) if spec.day.year == 2004 else read_2009(spec)
    return [block], {"Kehtivaid sedeleid": block.stated_valid or 0}


def html_declared(spec: Spec) -> Declared:
    if spec.kind == "rk":
        return rk_declared(spec)
    block = blocks(spec)[0][0]
    if spec.day.year == 2009:
        page = old_page(spec, f"{EP2009}11191")
        legend = legend_2009(spec)
        codes = re.findall(r'align="center"><b>([^<]+)</b></td>', page)
        votes = [int(v) for v in re.findall(r'class="dataTxt(?:Normal|Gray)" align="center">(\d+)</td>', page)]
        cast = dict(zip((clean(c) for c in codes), votes, strict=True))
        lists = {legend[c]: v for c, v in cast.items() if c.isupper()}
        people = {changeset.slug(legend[c]): v for c, v in cast.items() if not c.isupper()}
        members = {changeset.slug(full) for full, _ in elected_2009(spec)}
        return Declared(people, members, {}, {}, lists)
    rows = mandate_rows(spec)
    people = {str(regno): personal for regno, _, _, personal in rows}
    first: dict[str, int] = {}
    for _, code, comparison, _ in rows:
        first.setdefault(code, comparison)
    hints = {regno: code for regno, code, _, _ in rows}
    lists = {
        entry.name: first[hints[entry.candidates[0].regno]]
        for entry in block.entries.values()
        if not entry.independents
    }
    page = lines(old_page(spec, f"{EP2004}/liikmed.html"))
    names = [clean(line.split(")", 1)[1]) for line in page if re.match(r"\d\) ", line)]
    by_name = {clean(f"{c.forename} {c.surname}"): c.id for entry in block.entries.values() for c in entry.candidates}
    return Declared(people, {by_name[name] for name in names}, {}, {}, lists)


# ---------------------------------------------------------------- reading the committee's Riigikogu pages, 1992-2011

RK_PAGES = {
    1992: ("r92", "htl_start.stm", ("vtl_start.stm", "vtl_liikmed.stm")),
    1995: ("r95", "htl_start.stm", ("vtl_start.stm", "vtl_liikmed.stm")),
    1999: ("r99", "htl_start.stm", ("vtl_start.stm", "vtl_liikmed.stm")),
    2003: ("r03/tulemus", "eesti_tulemus.html", ("liikmed.html",)),
    2007: ("r07/tulemus", "eesti_tulemus.html", ("liikmed.html",)),
    2011: ("rk2011", "election_result.html", ("chart_0.html", "mandate_distribution.html")),
}
TALLINN_PAGES = {1992: (1, 2, 3, 4), 1995: (1, 2, 3), 1999: (1, 3, 5), 2003: (1, 2, 3), 2007: (1, 2, 3)}
MATRICES = {
    1992: (1000000, 2010000, 2020000, 3010000, 3020000, 3030000, 4020000, 5010000, 5020000, 6010000, 6020000)
    + (7010000, 7020000, 8010000, 8020000, 8030000, 9010000),
    1995: (1000000, 2010000, 2020000, 3010000, 3020000, 3030000, 4010000, 4020000, 5010000, 5020000, 6010000)
    + (6020000, 7010000, 8010000, 8020000, 8030000, 9010000),
    2003: (1010100, 2010000, 2020000, 3010000, 3020000, 3030000, 4010000, 5010000, 6010000, 6020000, 7010000)
    + (7020000, 8010000, 9010000, 9020000, 9030000, 10010000),
}
MATRICES[1999] = MATRICES[1995]
MATRICES[2007] = MATRICES[2003]
DISTRICTS_2011 = tuple(range(5376, 5388))
COUNTIES_2011 = (
    "0037", "0039", "0044", "0049", "0051", "0057", "0059", "0065", "0067", "0070", "0074", "0078", "0082", "0084",
    "0086",
)  # fmt: skip
ROMAN = re.compile(r"[IVX]+")
DIVISION = re.compile(r"J\d+")
OLD_TALLINN = {"Lõuna-Tallinna linnaosa", "Lääne-Tallinna linnaosa", "Ida-Tallinna linnaosa"}


@dataclass(frozen=True)
class Layout:
    start: str
    extra: list[str]
    districts: list[str]
    matrices: list[str]

    @property
    def pages(self) -> list[str]:
        return [self.start, *self.extra, *self.districts, *self.matrices]


def layout(spec: Spec) -> Layout:
    """The committee's pages for one election of 1992-2011, in a fixed order."""
    year = spec.day.year
    folder, start, extra = RK_PAGES[year]
    base = f"{VVK}/{folder}"
    if year == 2011:
        return Layout(
            f"{base}/{start}",
            [f"{base}/{name}" for name in extra],
            [f"{base}/election_result_di_{n}.html" for n in DISTRICTS_2011],
            [f"{base}/election_result_au2_{code}.html" for code in COUNTIES_2011],
        )
    if year < 2003:
        tallinn = [f"htl_1{n:02d}0000.stm" for n in TALLINN_PAGES[year]]
        rest = [f"htl_{n}000000.stm" for n in range(2, 10)]
        found = [f"htl_{n}.html" for n in MATRICES[year]]
    else:
        tallinn = [f"1{n:02d}0000.html" for n in TALLINN_PAGES[year]]
        rest = [f"{n}000000.html" for n in range(2, 11)]
        found = [f"{n}_bottom.html" for n in MATRICES[year]]
    return Layout(
        f"{base}/{start}",
        [f"{base}/{name}" for name in extra],
        [f"{base}/{name}" for name in (*tallinn, *rest)],
        [f"{base}/{name}" for name in found],
    )


def rk_files(spec: Spec) -> list[str]:
    found = layout(spec)
    pages = found.pages
    if spec.day.year == 2011:
        for url in (*found.districts, *found.matrices):
            for name in sorted(set(re.findall(r'href="(election_result_au3_\d+\.html)"', vvk_page(url)))):
                if (full := f"{VVK}/rk2011/{name}") not in pages:
                    pages.append(full)
    return pages


def vvk_page(url: str) -> str:
    data = raw.cached(SOURCE, url).path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


@dataclass
class Matrix:
    """A table of candidates against municipalities or polling divisions, as the committee prints it."""

    labels: list[str]
    votes: dict[str, tuple[str, list[int]]]
    groups: list[tuple[str, list[str], list[int]]]
    valid: list[int] | None
    registered: list[int] | None = None
    cast: list[int] | None = None


def matrix_of(page: str) -> Matrix | None:
    rows = [r for r in table_rows(page) if any(r)]
    head = next((r for r in rows if r[0].startswith("Registr")), None)
    if head is None:
        return None
    labels: list[str] = []
    for label in head[2:]:
        labels.append(label if label not in labels else f"{label} ({labels.count(label) + 1})")
    found = Matrix(labels, {}, [], None)
    waiting: list[str] = []
    for r in rows:
        if r is head or len(r) != len(head) or not all(re.fullmatch(r"[\d ]+", c) for c in r[2:]):
            continue
        values = [count(c) for c in r[2:]]
        name = r[1].upper().rstrip(":")
        if r[0].isdigit():
            found.votes[r[0]] = (r[1], values)
            waiting.append(r[0])
        elif r[0] == "" and (name == "KOKKU" or name.startswith("KEHTIVAID")):
            found.valid = values
        elif r[0] == "" and name.startswith("VALIJAID KOKKU"):
            found.registered = values
        elif r[0] == "" and name.startswith("OSALEJAID KOKKU"):
            found.cast = values
        elif r[0] == "" and r[1] and not name.startswith("OSAV"):
            found.groups.append((r[1], waiting, values))
            waiting = []
    if waiting:
        found.groups.append(
            (INDEPENDENTS, waiting, [sum(found.votes[r][1][i] for r in waiting) for i in range(len(found.labels))])
        )
    return found


@dataclass
class Listed:
    name: str
    candidates: list[tuple[str, str, int, list[int]]]
    total: list[int] = field(default_factory=list)


@dataclass
class District:
    areas: list[str]
    mandates: int
    lists: list[Listed]
    voters: list[int | None] = field(default_factory=list)
    cast: list[int] = field(default_factory=list)


def district_of(page: str) -> District:
    areas: list[str] = []
    voters: list[int | None] = []
    cast: list[int] = []
    lists: list[Listed] = []
    quota = re.search(r"Kvoot = [\d\s]+/\s*(\d+)\s*=", clean(html.unescape(re.sub(r"<[^>]+>", " ", page))))
    mandates = int(quota.group(1)) if quota else 0
    for r in table_rows(page):
        if not r:
            continue
        if not lists and len(r) == 5 and ROMAN.fullmatch(r[0]) and r[1]:
            areas.append(r[1])
            voters.append(count(r[2]) if r[2] else None)
            cast.append(count(r[3]))
        elif len(r) > 1 and not ROMAN.fullmatch(r[0]) and all(ROMAN.fullmatch(c) for c in r[1:]):
            lists.append(Listed(r[0], []))
        elif lists and re.fullmatch(r"\d+\)\s*nr\.?", r[0]):
            lists[-1].candidates.append((r[1], r[2], count(r[4]), [count(c) for c in r[6::2]]))
        elif lists and re.match(r"(Nimekiri|Üksikkandidaadid) KOKKU", r[0]):
            lists[-1].total = [count(c) for c in r[3::2]]
    return District(areas, mandates, lists, voters, cast)


def kind_of(area_: str) -> str:
    if area_.lower().startswith("välisriigid"):
        return "abroad"
    return "evotes" if area_.startswith("E-hääled") else "geo"


def label_of(area_: str, label: str) -> str:
    return f"{area_} {label}" if DIVISION.fullmatch(label) else label


@cache
def lookup() -> dict[str, str]:
    """Municipality names to the codes the committee's pages and the open data give them."""
    out = dict(town_codes_2009()[0])
    for spec in ELECTIONS.values():
        if not (spec.html and spec.kind == "rk"):
            for name, code in municipalities(spec).items():
                out.setdefault(name, code)
    return out


def whereabouts(day: date, label: str, relation: str = "within") -> tuple[str, str, str | None, str]:
    """The reporting-unit code, relation, area and kind of a municipality, a city district, or a division of either."""
    districts = town_codes_2009()[1]
    names = lookup()
    parent, _, division = label.rpartition(" ")
    if not DIVISION.fullmatch(division):
        parent, division = label, ""
    suffix = f":{division}" if division else ""
    if parent in names:
        return f"{day}:{names[parent]}{suffix}", relation, area("ee.vald", names[parent]), "standard"
    if parent in districts:
        return f"{day}:{districts[parent]}{suffix}", "within", area("ee.vald", TALLINN), "standard"
    if parent in OLD_TALLINN:
        return f"{day}:tallinn:{changeset.slug(parent)}{suffix}", "within", area("ee.vald", TALLINN), "standard"
    return f"{day}:{label}", "unlocated", None, "standard"


def rk_code(name: str) -> str:
    """The code the open data gives a list that stood again later, else a key made from the name."""
    wanted = name.casefold()
    for known, code in known_codes().items():
        if known.casefold() == wanted:
            return code
    return f"vvk:{changeset.slug(name)}"


def candidate_in(entry: Entry, number: str, regno: str, full: str) -> Candidate:
    return candidate_of(entry, int(regno), f"{number}:{regno}", full)


def old_blocks(spec: Spec) -> tuple[list[Block], dict[str, int]]:
    found = layout(spec)
    overall = {"Kehtivaid sedeleid": after(lines(vvk_page(found.start)), "Kehtivaid sedeleid:")}
    spare = {url: m for url in found.matrices if (m := matrix_of(vvk_page(url))) is not None and m.votes}
    names = {g.casefold(): g for m in spare.values() for g, _, _ in m.groups}
    out: list[Block] = []
    for number, url in enumerate(found.districts, 1):
        d = district_of(vvk_page(url))
        n = str(number)
        kinds = [kind_of(a) for a in d.areas]
        everyone = {c[0]: c for item in d.lists for c in item.candidates}
        assigned: dict[int, Matrix] = {}
        withheld: list[str] = []
        for link, m in list(spare.items()):
            if not set(m.votes) <= set(everyone):
                continue
            scores = {
                i: sum(sum(m.votes[r][1]) == everyone[r][3][i] for r in m.votes)
                for i, kind in enumerate(kinds)
                if kind == "geo" and i not in assigned
            }
            best = max(scores, key=lambda i: scores[i], default=None)
            if best is None or scores[best] * 2 < len(m.votes):
                continue
            spare.pop(link)
            whole = scores[best] == len(m.votes) == len(everyone)
            tables = {item.name: item.total[best] for item in d.lists if item.total}
            rows = {names.get(g.casefold(), g): sum(v) for g, _, v in m.groups}
            lists = all(rows.get(names.get(k.casefold(), k)) in (None, t) for k, t in tables.items())
            columns = [sum(col) for col in zip(*(v for _, _, v in m.groups), strict=True)]
            if whole and lists and m.valid in (None, columns):
                assigned[best] = m
            else:
                withheld.append(d.areas[best])
        entries: dict[str, Entry] = {}
        valid: dict[str, int] = {}
        registered: dict[str, int] = {}
        cast: dict[str, int] = {}
        for i, matrix in assigned.items():
            if matrix.valid is not None:
                valid.update(zip([label_of(d.areas[i], x) for x in matrix.labels], matrix.valid, strict=True))
        for i, area_ in enumerate(d.areas):
            if i not in assigned:
                if d.voters[i] is not None:
                    registered[unit_of(area_, kinds[i])] = d.voters[i] or 0
                cast[unit_of(area_, kinds[i])] = d.cast[i]
        for item in d.lists:
            independent = item.name.startswith("Üksik")
            name = INDEPENDENTS if independent else names.get(item.name.casefold(), item.name)
            entry = Entry("Üksik." if independent else rk_code(name), name, {})
            sums = [0] * len(d.areas)
            for regno, full, total, per_area in item.candidates:
                who = candidate_in(entry, n, regno, full)
                for i, area_ in enumerate(d.areas):
                    if i in assigned:
                        labels = [label_of(area_, x) for x in assigned[i].labels]
                        who.votes.update(zip(labels, assigned[i].votes[regno][1], strict=True))
                    else:
                        who.votes[unit_of(area_, kinds[i])] = per_area[i]
                    sums[i] += per_area[i]
                if sum(who.votes.values()) != total:
                    raise ValueError(f"{spec.key} {n}:{regno}: {sum(who.votes.values())} votes against {total}")
            listed = item.total or sums
            for i, area_ in enumerate(d.areas):
                if i in assigned:
                    row = next(
                        (v for g, _, v in assigned[i].groups if names.get(g.casefold(), g) == name),
                        [0] * len(assigned[i].labels),
                    )
                    entry.total.update(zip([label_of(area_, x) for x in assigned[i].labels], row, strict=True))
                else:
                    entry.total[unit_of(area_, kinds[i])] = listed[i]
            entries[entry.name] = entry
        title = district_title(spec, n, d)
        block = Block(
            n, title, d.mandates, valid, registered, entries, None, set(), set(), unit_places(spec, entries, valid)
        )
        block.withheld = withheld
        block.cast = cast
        out.append(block)
    if spare:
        raise LookupError(f"{spec.key}: matrices matching no district area: {sorted(spare)}")
    return out, overall


def unit_of(area_: str, kind: str) -> str:
    return ABROAD_2004 if kind == "abroad" else EVOTES if kind == "evotes" else area_


def district_title(spec: Spec, number: str, d: District) -> str:
    if spec.day.year >= 2003:
        return district_names()[number]
    return ", ".join(a for a in d.areas if kind_of(a) == "geo")


def unit_places(
    spec: Spec, entries: dict[str, Entry], valid: dict[str, int]
) -> dict[str, tuple[str, str, str | None, str]]:
    labels = {lab for e in entries.values() for c in e.candidates for lab in c.votes} | set(valid)
    return {lab: whereabouts(spec.day, lab) for lab in sorted(labels) if lab not in (ABROAD_2004, EVOTES)}


def rk_municipalities(spec: Spec) -> dict[str, str]:
    if spec.day.year != 2011:
        return {}
    districts = set(town_codes_2009()[1].values())
    return {name: code for code, _, name, _ in au3_pages(spec) if code not in districts}


@cache
def au3_pages(spec: Spec) -> list[tuple[str, str, str, str]]:
    """The code, district number, name and address of each municipality or city district page of 2011."""
    out: list[tuple[str, str, str, str]] = []
    for url in rk_files(spec):
        code = re.search(r"au3_(\d+)\.html$", url)
        if code:
            page = vvk_page(url)
            head = re.search(r"<h2><span>VALIMISRINGKOND NR (\d+)</span><br />&raquo;\s*([^<]+)</h2>", page)
            if head is None:
                raise LookupError(f"{url} names no district")
            out.append((code.group(1), head.group(1), clean(html.unescape(head.group(2))), url))
    return out


def tabs_2011(page: str) -> tuple[dict[str, tuple[int, dict[str, int]]], int]:
    """Each tab of a district page, as the votes of its lists, and the district's mandates."""
    mandates = re.search(r"Kvoot = [\d\s]+/\s*(\d+)\s*=", page)
    labels = dict(re.findall(r'<li><a href="#tab-(\w+)">([^<]+)</a></li>', page))
    out: dict[str, tuple[int, dict[str, int]]] = {}
    for chunk in page.split('<div id="tab-')[1:]:
        ident = chunk[: chunk.index('"')]
        total = re.search(r'class="total" colspan="2">((?:\d|\s|&nbsp;)+)</td>', chunk)
        votes = {
            clean(html.unescape(name)): count(value)
            for name, value in re.findall(
                r'<h4>([^<]*)</h4>.*?<div class="votes">((?:\d|\s|&nbsp;)+)</div>', chunk, flags=re.DOTALL
            )
        }
        if total is None:
            raise LookupError(f"tab {ident} has no total")
        out[clean(html.unescape(labels[ident]))] = (count(total.group(1)), votes)
    return out, int(mandates.group(1) if mandates else 0)


def blocks_2011(spec: Spec) -> tuple[list[Block], dict[str, int]]:
    found = layout(spec)
    overall = {"Kehtivaid sedeleid": after(lines(vvk_page(found.start)), "Kehtivaid sedeleid:")}
    pages: dict[str, list[tuple[str, str, Matrix]]] = {}
    for code, number, name, url in au3_pages(spec):
        matrix = matrix_of(vvk_page(url))
        if matrix is None:
            raise LookupError(f"{url} holds no results")
        pages.setdefault(number, []).append((code, name, matrix))
    out: list[Block] = []
    for number, url in enumerate(found.districts, 1):
        n = str(number)
        tabs, mandates = tabs_2011(vvk_page(url))
        entries: dict[str, Entry] = {}
        valid: dict[str, int] = {}
        registered: dict[str, int] = {}
        cast: dict[str, int] = {}
        places: dict[str, tuple[str, str, str | None, str]] = {}
        for code, name, matrix in pages[n]:
            labels = [f"{name} {x}" for x in matrix.labels]
            for label, x in zip(labels, matrix.labels, strict=True):
                places[label] = (f"{spec.day}:{code}:{x}", "within", area("ee.vald", code), "standard")
            for target, cells in ((valid, matrix.valid), (registered, matrix.registered), (cast, matrix.cast)):
                if cells is not None:
                    target.update(zip(labels, cells, strict=True))
            for group, regnos, totals in matrix.groups:
                independent = group.startswith("Üksik")
                entry = entries.setdefault(
                    group,
                    Entry("Üksik." if independent else rk_code(group), INDEPENDENTS if independent else group, {}),
                )
                for regno in regnos:
                    who = candidate_in(entry, n, regno, matrix.votes[regno][0])
                    who.votes.update(zip(labels, matrix.votes[regno][1], strict=True))
                for label, value in zip(labels, totals, strict=True):
                    entry.total[label] = value
                if [sum(matrix.votes[r][1][i] for r in regnos) for i in range(len(labels))] != totals:
                    raise ValueError(f"{spec.key} {n} {name} {group}: candidates do not add up to the list row")
        table = district_of(vvk_page(url))
        for i, area_ in enumerate(table.areas):
            if kind_of(area_) != "geo":
                if table.voters[i] is not None:
                    registered[unit_of(area_, kind_of(area_))] = table.voters[i] or 0
                cast[unit_of(area_, kind_of(area_))] = table.cast[i]
        for label, kind in (("Välisriigid", ABROAD_2004), ("E-hääled", EVOTES)):
            tab = tabs.get(label)
            if tab is None:
                raise LookupError(f"{spec.key} {n}: no {label} tab")
            valid[kind] = tab[0]
            if sum(tab[1].values()) != tab[0]:
                raise ValueError(f"{spec.key} {n} {label}: lists hold {sum(tab[1].values())} votes, tab says {tab[0]}")
            for name, value in tab[1].items():
                entries[name].total[kind] = value
        block = Block(n, district_names()[n], mandates, valid, registered, entries, None, set(), set(), places)
        block.cast = cast
        out.append(block)
    return out, overall


def rk_blocks(spec: Spec) -> tuple[list[Block], dict[str, int]]:
    return blocks_2011(spec) if spec.day.year == 2011 else old_blocks(spec)


def member_names(spec: Spec) -> list[tuple[str, str]]:
    """The members the committee names, each with the list heading it is printed under."""
    page = lines(vvk_page(layout(spec).extra[-1 if spec.day.year == 2011 else 1 if spec.day.year < 2003 else 0]))
    out: list[tuple[str, str]] = []
    heading = ""
    if spec.day.year == 2011:
        for i, line in enumerate(page):
            if i + 2 < len(page) and page[i + 2] == "mandaati":
                heading = line
            elif heading and i > 0 and line not in {",", "mandaati"} and not re.fullmatch(r"\d+", line):
                out.append((heading, line))
        return out
    start = next(i for i, line in enumerate(page) if "asendusliikmed" in line.lower())
    for line in page[start + 1 :]:
        match = re.fullmatch(r"\d+\)\s*(.+)", line)
        if match:
            out.append((heading, clean(match.group(1))))
        else:
            heading = line
    return out


def rk_declared(spec: Spec) -> Declared:
    found, _ = blocks(spec)
    votes: dict[str, int] = {}
    if spec.day.year < 2011:
        for number, url in enumerate(layout(spec).districts, 1):
            for item in district_of(vvk_page(url)).lists:
                for regno, _, total, _ in item.candidates:
                    votes[f"{number}:{regno}"] = total
    index: dict[tuple[str, str], list[str]] = {}
    for block in found:
        for entry in block.entries.values():
            for who in entry.candidates:
                heading = "üksik" if entry.independents else entry.name.casefold()
                index.setdefault((heading, clean(f"{who.forename} {who.surname}").casefold()), []).append(who.id)
    elected: set[str] = set()
    missing: list[tuple[str, str]] = []
    for heading, name in member_names(spec):
        key_ = ("üksik" if heading.startswith("Üksik") else heading.casefold(), name.casefold())
        ids = index.get(key_, [])
        if len(ids) != 1:
            missing.append((heading, name))
        else:
            elected.add(ids[0])
    if missing or len(elected) != spec.seats:
        raise LookupError(f"{spec.key}: {len(elected)} members matched, unmatched: {missing}")
    return Declared(votes, elected, {}, {})


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
    p.add(
        "source",
        id=HTML_SOURCE_ID,
        kind="official_body",
        publisher="Vabariigi Valimiskomisjon",
        title="Results of the 1992-2011 Riigikogu and the 2004 and 2009 European Parliament elections in Estonia",
        url="https://www.vvk.ee/varasemad/ep09/",
        license="Published by the National Electoral Committee on vvk.ee; no license is stated, reuse with the source cited",
        attribution="Vabariigi Valimiskomisjon, vvk.ee",
    )


SOURCE_ID = key("source", "valimised")
HTML_SOURCE_ID = key("source", "vvk")
SHAPES_ID = key("source", "maaruum")


def shape_source(p: Package) -> None:
    p.add(
        "source",
        id=SHAPES_ID,
        kind="official_body",
        publisher="Maa- ja Ruumiamet",
        title="Administrative and settlement division of Estonia: municipalities (omavalitsused) and city districts, "
        "by date",
        url="https://geoportaal.maaruum.ee/est/ruumiandmed/haldus-ja-asustusjaotus/ajalooline-haldusjaotus-p827.html",
        license="Open data of the Land and Spatial Development Board, reuse with the source cited",
        attribution="Maa- ja Ruumiamet",
    )


def put_shape(p: Package, area_id: str, shape: shapely.Geometry, start: date | None, end: date | None) -> None:
    p.add(
        "area_geometry",
        area_id=area_id,
        valid_from=start,
        valid_to=end,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=SHAPES_ID,
    )


def shapes(p: Package, names: dict[str, dict[str, int]]) -> None:
    """Each municipality and city district with the outlines the Land Board's dated files give it.

    A code joins a file's unit when the unit has the same name, since the office's old codes were reissued. The
    board's municipal files go back to 1994; its files of settlement units hold the city districts."""
    shape_source(p)
    municipal = plans.municipal_plans(SOURCE)
    settled = plans.settlement_plans(SOURCE)
    for plan in (*municipal, *settled):
        p.artifact(raw.cached(SOURCE, plan.url), SHAPES_ID)
    for i, plan in enumerate(municipal):
        p.add(
            "boundary_set",
            id=key("boundary_set", "ee.vald", plan.day),
            scheme_code="ee.vald",
            vintage=f"Land Board plan of {plan.day}",
            valid_from=None if i == 0 else plan.day,
            valid_to=municipal[i + 1].day if i + 1 < len(municipal) else None,
            as_of=plan.day,
        )
    by_municipality = [plans.municipalities(SOURCE, plan.url) for plan in municipal]
    by_district = [plans.city_districts(SOURCE, plan.url) for plan in settled]
    for code, found in sorted(names.items()):
        district = any(name.endswith("linnaosa") for name in found)
        series, features = (settled, by_district) if district else (municipal, by_municipality)
        rows = plans.runs(series, features, code, set(found), set(names))
        if not rows:
            continue
        for run in rows:
            shape = plans.outlines(SOURCE, series[run.plan].url, district)[run.code]
            put_shape(p, area("ee.vald", code), shape, run.start, run.end)


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
            p.artifact(raw.cached(SOURCE, file), HTML_SOURCE_ID if spec.html else SOURCE_ID)
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
    for spec in ELECTIONS.values():
        if spec.html and spec.kind == "rk" and spec.day.year < 2003:
            for block in blocks(spec)[0]:
                id = district_area(spec, block.number)
                p.add("area", id=id, scheme_code="ee.ringkond")
                p.add("area_code", area_id=id, code=f"{spec.day.year}:{block.number}")
                p.name(id, block.name, "et", "official")
    for code, found in sorted(names.items()):
        id = area("ee.vald", code)
        p.add("area", id=id, scheme_code="ee.vald")
        p.add("area_code", area_id=id, code=code)
        latest = max(found.values())
        for name, year in sorted(found.items()):
            p.name(id, name, "et", "official", year == latest)
    shapes(p, names)
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
    legacy = spec.html and chamber
    method = METHOD_RK_VVK if legacy else METHOD_VVK if spec.html else METHOD
    source_id = HTML_SOURCE_ID if spec.html else SOURCE_ID
    relation = "within" if spec.day.year == 2004 else "equals"
    places = {label_: place for block in found for label_, place in block.places.items()}
    p.add("note", id=key("note", ident), entity_id=ident, kind="methodology", body_md=method, lang="en")
    units: dict[str, str] = {}

    def unit(district: str, label_: str) -> str:
        if label_ in codes:
            code, how, place, kind = f"{day}:{codes[label_]}", relation, area("ee.vald", codes[label_]), "standard"
        elif label_ in places:
            code, how, place, kind = places[label_]
        elif label_ == EVOTES:
            code, how, place, kind = (
                f"{day}:evotes:{district}" if legacy else f"{day}:evotes",
                "unlocated",
                None,
                "aggregate",
            )
        elif label_ in ABROAD_UNITS:
            code, how, place, kind = f"{day}:abroad:{district}", "unlocated", None, "abroad_mission"
        elif label_ == UNATTRIBUTED:
            code, how, place, kind = f"{day}:unattributed", "unlocated", None, "aggregate"
        elif label_ == OTHER_DISTRICT:
            code, how, place, kind = (
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
                area_relation=how,
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
            district_area_id=district_area(spec, number_) if chamber else None,
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
            source_id=source_id,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
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
        everything = {*block.valid, *block.registered, *block.cast}
        for label_ in everything:
            u = unit(number_, label_)
            if label_ in block.valid:
                put(p, result_set, u, None, VALID, block.valid[label_], derived(block, label_, VALID))
            if label_ in block.cast:
                put(p, result_set, u, None, CAST, block.cast[label_], False)
            if label_ in block.registered:
                put(p, result_set, u, None, REGISTERED, block.registered[label_], derived(block, label_, REGISTERED))
        if block.stated_valid is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=block.stated_valid)
            national_valid += block.stated_valid
        if block.registered and not legacy:
            p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=overall["Valijaid"])
        for note in block.withheld:
            p.add(
                "note",
                id=key("note", result_set, "withheld", note),
                entity_id=result_set,
                kind="methodology",
                body_md=(
                    f"The committee's table of candidates by municipality for {note} disagrees with its own table "
                    "of the district's results, which adds up to the declared totals. The archive stores that "
                    "area as a single unit with the district table's figures and leaves the municipal figures out."
                ),
                lang="en",
            )
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
                for label_ in together.keys() | entry.total.keys():
                    value = entry.total.get(label_, together.get(label_, 0))
                    put(p, result_set, unit(number_, label_), parent, VOTES, value, label_ not in entry.total)
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
                    put(
                        p,
                        result_set,
                        unit(number_, label_),
                        parent,
                        VOTES,
                        value,
                        label_ == UNATTRIBUTED or label_ in block.netted,
                    )
                if entry.name in result.lists:
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=parent,
                        measure=VOTES,
                        value=result.lists[entry.name],
                    )
                wins = 0
            for person in entry.candidates:
                tag = f"{name}:{person.id}"
                full = clean(f"{person.forename} {person.surname}").title()
                child = key("option", name, number_, "candidate", person.id)
                member = None if entry.independents else party(entry.code)
                if person.votes:
                    p.add(
                        "contest_option",
                        id=child,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=parent,
                        party_id=member,
                        ballot_position=person.regno or None,
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
                    list_position=person.regno or None,
                    ballot_name=full,
                    member_of_party_id=member,
                    is_independent=entry.independents,
                )
                for label_, value in person.votes.items():
                    flagged = label_ == UNATTRIBUTED or label_ in block.netted
                    put(p, result_set, unit(number_, label_), child, PREFERENCE, value, flagged)
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
    if legacy:
        rk_checks(spec, found, overall, result)
    else:
        checks(spec, found, overall, result, national_valid)
    return p


def national_votes(spec: Spec) -> dict[str, int]:
    """The votes each list won in the country, as the committee prints them."""
    year = spec.day.year
    out: dict[str, int] = {}
    if year < 2003:
        for r in table_rows(vvk_page(layout(spec).extra[0])):
            if len(r) == 4 and r[1].isdigit() and r[3].isdigit():
                out[r[0]] = int(r[1])
    elif year == 2011:
        page = lines(vvk_page(layout(spec).extra[0]))
        for i, line in enumerate(page):
            if i + 3 < len(page) and re.fullmatch(r"[\d ]+", page[i + 3]) and page[i + 2][:1] in "+-0123456789":
                out[line] = count(page[i + 3])
    return out


def rk_checks(spec: Spec, found: list[Block], overall: dict[str, int], result: Declared) -> None:
    """Each election's votes, candidates, and members against the totals the committee prints for the country."""
    lists: dict[str, int] = {}
    for block in found:
        for entry in block.entries.values():
            lists[entry.name] = lists.get(entry.name, 0) + sum(entry.total.values())
            for person in entry.candidates:
                declared_votes = result.votes.get(person.id)
                if declared_votes is not None and declared_votes != sum(person.votes.values()):
                    raise ValueError(
                        f"{spec.key} {person.id}: {sum(person.votes.values())} votes against {declared_votes}"
                    )
        for label, value in block.valid.items():
            counted = sum(e.total.get(label, 0) for e in block.entries.values())
            if counted != value:
                raise ValueError(
                    f"{spec.key} {block.number} {label}: lists hold {counted} votes, valid ballots {value}"
                )
    if sum(lists.values()) != overall["Kehtivaid sedeleid"]:
        raise ValueError(f"{spec.key}: lists hold {sum(lists.values())} votes, the committee states {overall}")
    for name, value in national_votes(spec).items():
        if not name:
            mine = value if value in lists.values() else None
        else:
            mine = next(
                (v for k, v in lists.items() if k.casefold() == name.casefold() or k[:5] == name[:5] == "Üksik"),
                None,
            )
        if mine != value:
            raise ValueError(f"{spec.key} {name}: {mine} votes against the committee's {value}")


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
        listed_votes = result.lists.get(entry.name)
        if listed_votes is not None and listed_votes != sum(entry.total.values()):
            raise ValueError(f"{spec.key} {entry.name}: {sum(entry.total.values())} votes against {listed_votes}")
