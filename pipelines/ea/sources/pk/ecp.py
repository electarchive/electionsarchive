"""Pakistan's Election Commission: the 2002 National Assembly general election, by constituency."""

import re
import subprocess
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "pk.ecp"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
PAGE = "https://ecp.gov.pk/general-elections-2002"
DAY = date(2002, 10, 10)
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
CONSTITUENCIES = "https://web.archive.org/web/20240701044518id_/https://ecp.gov.pk/storage/files/1/ge2002-n1.pdf"
PARTIES = "https://web.archive.org/web/20240701044536id_/https://ecp.gov.pk/storage/files/1/ge2002-n6.pdf"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECP_SOURCE = key("source", "ecp")
ASSEMBLY = key("body", "national-assembly")
SYSTEM = key("electoral_system", "national-assembly", "fptp")

METHOD = (
    "Counted and published by constituency by the Election Commission of Pakistan in its detailed results of the "
    "2002 general elections. Valid votes are the votes polled by the candidates, total votes are valid and rejected "
    "votes together, and registered voters are the voters on the electoral roll of the constituency. The Commission "
    "does not publish the count of each polling station in this volume."
)

# Spellings the printed volume uses for one party, by the squashed lowercase text.
PARTY_NAMES = {
    "awamiqaidatparty": "Awami Qiadat Party",
    "balohistannationalmovement": "Balochistan National Movement",
    "independent": "Independent",
    "independentt": "Independent",
    "independentretired": "Independent",
    "hazaraquamimahaz": "Hazara Quami Mahaz",
    "hazaraqaumimahaz": "Hazara Quami Mahaz",
    "laboyrpartypakistan": "Labour Party Pakistan",
    "labourpartypakistankrandi": "Labour Party Pakistan (Krandi)",
    "muttahiddamajliseamal": "Muttahidda Majlis-e-Amal Pakistan",
    "pakmuslimalliance": "Pak Muslim Alliance",
    "pakistamuslimleaguej": "Pakistan Muslim League (J)",
    "pakistanpeoplespartyparliamentarian": "Pakistan Peoples Party Parliamentarians",
    "pakistanmazdoorkissanparty": "Pakistan Mazdoor Kissan Party",
    "pakistanawamiparty": "Pakistan Awami Party",
    "indepndent": "Independent",
    "indeindependentdent": "Independent",
    "indeindependentdente": "Independent",
    "muthidaqaumimovement": "Muttahida Qaumi Movement",
    "muttahidaqaumimoement": "Muttahida Qaumi Movement",
    "muttahidaqaumimoment": "Muttahida Qaumi Movement",
    "muttahidaqaumimovement": "Muttahida Qaumi Movement",
    "muttahiddamajliseamalpakistan": "Muttahidda Majlis-e-Amal Pakistan",
    "pakistandemocraticparty": "Pakistan Democratic Party",
    "pakistanmulimleagueqa": "Pakistan Muslim League (QA)",
    "pakistanmuslimleagueqa": "Pakistan Muslim League (QA)",
    "pakistanmuslimleaguen": "Pakistan Muslim League (N)",
    "pakistanmuslimleaguef": "Pakistan Muslim League (F)",
    "pakistanmuslimleaguej": "Pakistan Muslim League (J)",
    "pakistanmuslimleaguez": "Pakistan Muslim League (Z)",
    "pakistanpeoplespartyparliamentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplespartyparlimentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplespartysheedbhutto": "Pakistan Peoples Party (Shaheed Bhutto)",
    "pakistanpeoplespartyshaheedbhutto": "Pakistan Peoples Party (Shaheed Bhutto)",
    "pakistanpeoplespartysherpao": "Pakistan Peoples Party (Sherpao)",
    "pakistantahreekeinsaf": "Pakistan Tehreek-e-Insaf",
    "pakistantehreekeinsaf": "Pakistan Tehreek-e-Insaf",
    "pakistansocialdemocraticparty": "Pakistan Social Democratic Party",
    "nazimemistafa": "Nizam-e-Mustafa Party",
    "nizamemustafaparty": "Nizam-e-Mustafa Party",
    "pakistanawamitehrikeinqilab": "Pakistan Awami Tehrik-e-Inqilab",
}


def clean(text: str) -> str:
    return " ".join(text.split())


def party_name(text: str) -> str:
    squashed = re.sub(r"[^a-z]", "", text.lower())
    return PARTY_NAMES.get(squashed, clean(text))


def party_id(name: str) -> str:
    return key("party", name.casefold())


class Candidate:
    def __init__(self, position: int, name: str, party: str, votes: int) -> None:
        self.position = position
        self.name = name
        self.party = party
        self.votes = votes


class Constituency:
    def __init__(self, number: int, name: str) -> None:
        self.number = number
        self.name = name
        self.candidates: list[Candidate] = []
        self.totals: dict[str, int] = {}


def text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


HEADER = re.compile(r"^\s*NA-(\d+)[\s-]+(.+?)\s*$")
CANDIDATE = re.compile(r"^\s*(\d+)?\s*(\S.*?)\s{2,}(.+?)\s{2,}(\d+)\s*$")
TOTAL = re.compile(r"^\s*(Valid Votes|Rejected Votes|Total Votes|Registered Voters)\s+(\d+)\s*$")


# Candidates whose printed line wraps in the volume, so that their votes sit on a line of their own, by
# constituency number and ballot position.
WRAPPED = {
    (51, 6): ("Syed Mozam Ali Rizvi", "Pak Wattan Party", 533),
    (89, 11): ("Lt. Comdr ® Sohaib Farooq", "Pakistan Muslim League(QA)", 1415),
}


# A line whose party is run into the candidate's name, by constituency number and votes.
RUN_TOGETHER = {(157, 12572): ("Mr.Sahibzada Muhammad Aziz-ur-Rehman Hamdi", "Muttahidda Majlis-e-Amal Pakistan")}


def constituencies() -> list[Constituency]:
    found: list[Constituency] = []
    for line in text(CONSTITUENCIES).splitlines():
        if header := HEADER.match(line):
            found.append(Constituency(int(header.group(1)), clean(header.group(2))))
        elif not found:
            continue
        elif total := TOTAL.match(line):
            found[-1].totals[total.group(1)] = int(total.group(2))
        elif candidate := CANDIDATE.match(line):
            last = found[-1].candidates[-1].position if found[-1].candidates else 0
            position = int(candidate.group(1)) if candidate.group(1) else last + 1
            found[-1].candidates.append(
                Candidate(position, clean(candidate.group(2)), party_name(candidate.group(3)), int(candidate.group(4)))
            )
    for seat in found:
        for candidate in seat.candidates:
            if (seat.number, candidate.votes) in RUN_TOGETHER:
                candidate.name, candidate.party = RUN_TOGETHER[seat.number, candidate.votes]
                candidate.party = party_name(candidate.party)
        for (number, position), (name, party, votes) in WRAPPED.items():
            if seat.number == number:
                seat.candidates.append(Candidate(position, name, party_name(party), votes))
        seat.candidates.sort(key=lambda c: c.position)
        numbers = [c.position for c in seat.candidates]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"NA-{seat.number}: candidate numbering is {numbers}")
        for index, candidate in enumerate(seat.candidates, 1):
            candidate.position = index  # the volume skips a printed number in a few constituencies
        if len(seat.totals) != 4:
            raise ValueError(f"NA-{seat.number}: totals are {seat.totals}")
        if sum(c.votes for c in seat.candidates) != seat.totals["Valid Votes"]:
            raise ValueError(f"NA-{seat.number}: candidates sum to {sum(c.votes for c in seat.candidates)}")
    if [seat.number for seat in found] != list(range(1, 273)):
        raise ValueError("the volume does not list NA-1 to NA-272 once each, in order")
    return found


def printed_totals() -> dict[str, int]:
    """The printed national row of the party table: total seats and total votes."""
    for line in text(PARTIES).splitlines():
        if match := re.match(r"^\s*TOTAL\s+(\d+)\s+(\d+)\s*$", line):
            return {"seats": int(match.group(1)), "votes": int(match.group(2))}
    raise ValueError("no TOTAL row in the party table")


def packages() -> list[str]:
    return ["pk.ecp/registry", "pk.ecp/2002-10-10"]


def fetch() -> None:
    for url in (CONSTITUENCIES, PARTIES):
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        "pk.ecp/registry",
        SOURCE,
        "Pakistani parties, the National Assembly, and its electoral system, from the Election Commission's "
        "detailed results of 2002",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=ECP_SOURCE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="General Elections 2002: constituency-wise detailed results",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in (CONSTITUENCIES, PARTIES):
        p.artifact(raw.cached(SOURCE, url), ECP_SOURCE)
    p.add("scheme", code="pk.na", polity_id=PAKISTAN, kind="electoral_district", rank=1, noun="constituency")
    p.add("body", id=ASSEMBLY, polity_id=PAKISTAN, kind="lower_house")
    p.name(ASSEMBLY, "National Assembly of Pakistan", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each general constituency elects one member by plurality. Seats reserved for women and "
        "non-Muslims are filled from party lists in proportion to the general seats won, and are not part of "
        "the constituency results stored here.",
        lang="en",
    )
    names = {party_name(c.party): None for seat in constituencies() for c in seat.candidates}
    for name in sorted(names):
        if name == "Independent":
            continue
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=PAKISTAN)
        p.name(id, name, "en", "official")
    return p


def build() -> Package:
    p = Package(
        "pk.ecp/2002-10-10",
        SOURCE,
        "The Pakistani National Assembly general election of 2002 by constituency, from the Election "
        "Commission's detailed results",
        depends=["pk.ecp/registry"],
    )
    p.external.add(PAKISTAN)
    for url in (CONSTITUENCIES, PARTIES):
        p.uses(raw.cached(SOURCE, url))
    election = key("election", "2002")
    p.add("election", id=election, polity_id=PAKISTAN, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path="pk/2002-10-10-general", entity_id=election, is_current=True)
    p.name(election, "2002 Pakistani general election", "en", "official")
    for seat in constituencies():
        contest = key("contest", seat.number)
        label = f"NA-{seat.number} {seat.name}"
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"2002 Pakistani National Assembly election, {label}", "en", "official")
        p.add("slug", path=f"pk/2002-10-10-general/na-{seat.number}", entity_id=contest, is_current=True)
        unit = key("unit", seat.number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PAKISTAN,
            scheme_code="pk.na",
            code=f"2002-10-10:NA-{seat.number}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        result_set = key("result_set", seat.number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECP_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        accounting = (
            (REGISTERED, seat.totals["Registered Voters"]),
            (CAST, seat.totals["Total Votes"]),
            (INVALID, seat.totals["Rejected Votes"]),
            (VALID, seat.totals["Valid Votes"]),
        )
        for measure, value in accounting:
            put(p, result_set, unit, None, measure, value)
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for c in seat.candidates:
            option = key("option", seat.number, c.position)
            independent = c.party == "Independent"
            person = key("person", seat.number, c.position)
            p.add("person", id=person)
            p.name(person, c.name, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=c.position,
                ballot_label=c.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", seat.number, c.position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=None if independent else party_id(c.party),
                is_independent=independent,
            )
            put(p, result_set, unit, option, VOTES, c.votes)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.votes)
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
