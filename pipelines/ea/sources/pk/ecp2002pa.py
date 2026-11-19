"""Pakistan's Election Commission: the provincial assembly general elections of 2002, by constituency.

The four provincial assemblies were elected on 10 October 2002, the day of the National Assembly election that
`pk.ecp` already loads. The Commission published one detailed-results volume for each province, which prints every
constituency with its candidates, their parties and votes polled, then the valid, rejected, and total votes and the
registered voters, and a table of the seats each party won. The volumes have a text layer, which is read here line by
line. Every constituency is checked against its own printed totals: the candidates' votes must sum to the valid votes,
and valid and rejected votes must sum to the total votes."""

import re
import subprocess
from dataclasses import dataclass, field

from ea import raw
from ea.package import Package
from ea.sources.pk import ecp

SOURCE = ecp.SOURCE
PAGE = ecp.PAGE
WAYBACK = "https://web.archive.org/web/{stamp}id_/https://ecp.gov.pk/storage/files/1/ge2002-{name}.pdf"
VOLUMES = {
    "PF": WAYBACK.format(stamp="20240701044521", name="n2"),
    "PP": WAYBACK.format(stamp="20251202153110", name="n3"),
    "PS": WAYBACK.format(stamp="20240701044529", name="n4"),
    "PB": WAYBACK.format(stamp="20240701044533", name="n5"),
}
POSITIONS = WAYBACK.format(stamp="20241215163219", name="n7")
FILES = [*VOLUMES.values(), POSITIONS]
SEATS = {"PF": 99, "PP": 297, "PS": 130, "PB": 51}
PACKAGE = "pk.ecp/2002-10-10-pa"
REGISTRY = "pk.ecp/registry-pa2002"
ECP_SOURCE = ecp.key("source", "ecp-2002-pa")
SYSTEM = ecp.key("electoral_system", "provincial-assemblies-2002", "fptp")
VOTES, REGISTERED, CAST, VALID, INVALID = ecp.VOTES, ecp.REGISTERED, ecp.CAST, ecp.VALID, ecp.INVALID

METHOD = (
    "Counted and published by constituency by the Election Commission of Pakistan in its detailed results of the "
    "2002 general elections for the provincial assembly. Valid votes are the votes polled by the candidates, total "
    "votes are valid and rejected votes together, and registered voters are the voters on the electoral roll of the "
    "constituency. The Commission does not publish the count of each polling station in this volume, and it marks "
    "no winner, so none is stored."
)

# The volumes misspell some party names. Each is read through the squashed lowercase text of what is printed, after
# the common slips (`Leaque`, `Paksitan`, `Parlimentarian`) are corrected. The names follow the Commission's own table
# of party positions, or the spelling `pk.ecp` already holds for the party.
SLIPS = {
    "leaque": "league",
    "paksitan": "pakistan",
    "pakitan": "pakistan",
    "parlimentarian": "parliamentarian",
    "moment": "movement",
    "thereek": "tehreek",
}
PRINTED = {
    "Balochistan National Democratic Party": ["Baluchistan National Democratic Party"],
    "Independent": ["Independentd", "Independente"],
    "Jamhoori Wattan Party": ["Jamhoori Watan Party"],
    "Jamote Qaumi Movement": ["Jamoot Qaumi Movement", "Jamote Qaumi Movement"],
    "Kakar Jamhoori Party Pakistan": ["Kakar Jamhoori Party Pakistane"],
    "Mohajir Ittehad Tehrik": ["Mohajir Ittehad Tehreek"],
    "Muttahidda Majlis-e-Amal Pakistan": [
        "Muttahidda Majlis-e-Amal Pakistan Pakistan",
        "Muttahida Majlis-e-Amal Pakistan",
        "Muttahidda Majil-e-Amal Pakistan",
    ],
    "Muttahida Qaumi Movement": ["Muttahidda Qaumi Movement"],
    "National Alliance": ["National Aliance", "National Alliance."],
    "National Peoples Party Workers Group": [
        "National Peoples Party Worker Group",
        "Natonal Peoples Party Workers Group",
    ],
    "Nizam-e-Mustafa Party": ["Nazam-e-Mustafa Party", "Nizam-I-Mustafa Party"],
    "Pakistan Awami Tehreek": ["Pakistan Awami Tehreek"],
    "Pakistan Peoples Party Parliamentarians": [
        "Pakistan People Party Parliamentarian",
        "Pakistan Peoples Party Parliamentarian",
    ],
    "Pakistan Peoples Party (Shaheed Bhutto)": ["Pakistan Peoples Party(Sheheed Bhutto)"],
    "Pakistan Seriaki Party": ["Pakistan Saraiki Party"],
    "Pakistan Tehreek-e-Insaf": ["Pakistan Tehreek-Insaf"],
    "Sindh Urban-Rural Alliance": ["Sindh Urban- Rural Alliance"],
    "Sunni Tehreek": ["Suuni Tehreek"],
    "Tehrik-e-Istaqlal": ["Tehreek-e-Istiqlal", "Tehrik-e-Istaqlal (Matka )"],
    # The Commission's table of party positions lists no Pakistan Muslim League (Q); the leading party of that name is
    # the (QA).
    "Pakistan Muslim League (QA)": ["Pakistan Muslim League(Q)"],
    "Pak Wattan Party": ["Pakistan Wattan Party", "Pakistan Watan Party"],
    "Pakistan Awami Tehrik-e-Inqilab": ["Pakistan Awami Tehreek Inqilab", "Pakistan Awami Tehrik Inqilab"],
}
RETIRED = re.compile(r"\s*\(retired\)\s*", re.IGNORECASE)


def corrected(text: str) -> str:
    for wrong, right in SLIPS.items():
        text = re.sub(
            wrong,
            lambda m, right=right: right.capitalize() if m.group(0)[0].isupper() else right,
            text,
            flags=re.IGNORECASE,
        )
    return text


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", corrected(text).lower())


ALIASES = {squash(printed): name for name, printed_names in PRINTED.items() for printed in printed_names}


class Candidate(ecp.Candidate):
    retired = False


@dataclass
class Seat:
    prefix: str
    number: int
    name: str
    candidates: list[Candidate] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    @property
    def code(self) -> str:
        return f"{self.prefix}-{self.number}"


HEADER = re.compile(r"^\s*(PF|PP|PS|PB)\s*-\s*(\d+)[\s-]+(.+?)\s*$")
LINE = re.compile(r"^\s*(?:(\d+)\s{2,})?(\S.*?)\s{2,}(\d+)\s*$")
TOTAL = re.compile(r"^\s*(Valid Votes|Rejected Votes|Total Votes(?: Votes)?|Registered Voters)\s+(\d+)\s*$")


def party(text: str) -> str:
    return ALIASES.get(squash(text)) or ecp.party_name(corrected(text))


def volume_text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


# Lines whose name and party run together with a party spelling no other line prints.
RUN_TOGETHER = {
    "Mr.Muhammad Saddique ul Hasnat Dogar Advocate Sunni Tehrak": (
        "Mr.Muhammad Saddique ul Hasnat Dogar Advocate",
        "Sunni Tehreek",
    ),
}


def split(body: str, spellings: set[str]) -> tuple[str, str]:
    """A candidate's name and party from the text between the number and the votes."""
    cells = re.split(r"\s{2,}", body.strip())
    if len(cells) >= 2:
        return cells[0], " ".join(cells[1:])
    text = ecp.clean(cells[0])
    if text in RUN_TOGETHER:
        return RUN_TOGETHER[text]
    if text.lower() in spellings:
        return "", text  # the volume prints no name for the candidate
    for spelling in sorted(spellings, key=len, reverse=True):
        if text.lower().endswith(" " + spelling):
            return text[: -len(spelling)].strip(), text[-len(spelling) :]  # the name and party run together
    raise ValueError(f"cannot split a candidate line: {body!r}")


def seats(prefix: str) -> list[Seat]:
    lines = volume_text(VOLUMES[prefix]).splitlines()
    spellings: set[str] = set()
    for line in lines:
        if (row := LINE.match(line)) and not TOTAL.match(line):
            cells = re.split(r"\s{2,}", row.group(2).strip())
            if len(cells) == 2:
                spellings.add(ecp.clean(cells[1]).lower())
    found: list[Seat] = []
    for line in lines:
        if header := HEADER.match(line):
            found.append(Seat(header.group(1), int(header.group(2)), ecp.clean(header.group(3))))
        elif not found:
            continue
        elif total := TOTAL.match(line):
            found[-1].totals[total.group(1).replace(" Votes Votes", " Votes")] = int(total.group(2))
        elif row := LINE.match(line):
            name, printed = split(row.group(2), spellings)
            retired = bool(RETIRED.search(name) or RETIRED.search(printed))
            candidate = Candidate(
                int(row.group(1)) if row.group(1) else len(found[-1].candidates) + 1,
                ecp.clean(RETIRED.sub(" ", name)),
                party(ecp.clean(RETIRED.sub(" ", printed))),
                int(row.group(3)),
            )
            candidate.retired = retired
            found[-1].candidates.append(candidate)
    for seat in found:
        seat.candidates.sort(key=lambda c: c.position)
        for index, candidate in enumerate(seat.candidates, 1):
            candidate.position = index
        if len(seat.totals) != 4:
            raise ValueError(f"{seat.code}: totals are {seat.totals}")
        valid = sum(c.votes for c in seat.candidates)
        if valid != seat.totals["Valid Votes"]:
            raise ValueError(f"{seat.code}: candidates sum to {valid}, printed {seat.totals['Valid Votes']}")
        if seat.totals["Valid Votes"] + seat.totals["Rejected Votes"] != seat.totals["Total Votes"]:
            raise ValueError(f"{seat.code}: valid and rejected votes do not sum to the total")
    if [seat.number for seat in found] != list(range(1, SEATS[prefix] + 1)):
        raise ValueError(f"{prefix}: the volume does not list every constituency once, in order")
    return found


# The parties no earlier `pk.ecp` package holds, as the volumes print them. Every other party is already in the
# `pk.ecp` registry, under the id `ecp.party_id` gives its name.
NEW_PARTIES = [
    "Balochistan National Democratic Party",
    "Baluchistan National Congress",
    "Jamiat Ulema-e-Pakistan (Nifaz-e-Shariat)",
    "Jamote Qaumi Movement",
    "Markazi Jammat Ahlehadith",
    "Mohib-e-Watan Nowjawan Inqliabion Ki Anjuman",
    "Muttahidda Mahaz",
    "National Peoples Party Workers Group",
    "National Workers Party",
    "Pakhtoonkhwa Qaumi Party",
    "Pakistan Ghareeb Party",
    "Pakistan Jamhoori Aman Party",
    "Pakistan Muslim League (Jinnah)",
    "Qaumi Inqilabi Party",
    "Shan Pakistan Party",
    "Sindh Urban-Rural Alliance",
    "Tehreek Jamhoriat Pakistan",
]


def all_seats() -> list[Seat]:
    return [seat for prefix in SEATS for seat in seats(prefix)]


def packages() -> list[str]:
    return [REGISTRY, PACKAGE]


def fetch() -> None:
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    registry().write()
    build().write()


def emit() -> None:
    from ea import package

    package.run("load", packages())


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Election Commission's source for the 2002 provincial assembly results, and the parties that stood only "
        "there",
    )
    p.external.add(ecp.PAKISTAN)
    p.add(
        "source",
        id=ECP_SOURCE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="General Elections 2002: provincial assemblies, constituency-wise detailed results and party positions",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), ECP_SOURCE)
    p.add("electoral_system", id=SYSTEM, polity_id=ecp.PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=ecp.key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each general constituency of a provincial assembly elects one member by plurality. Seats reserved "
        "for women and non-Muslims are filled from party lists in proportion to the general seats won, and are not "
        "part of the constituency results stored here.",
        lang="en",
    )
    for name in sorted(NEW_PARTIES):
        id = ecp.party_id(name)
        p.add("party", id=id, kind="party", polity_id=ecp.PAKISTAN)
        p.name(id, name, "en", "official")
    return p


def build() -> Package:
    p = Package(
        PACKAGE,
        SOURCE,
        "The four Pakistani provincial assembly general elections of 2002 by constituency, from the Election "
        "Commission's detailed results",
        depends=[REGISTRY],
    )
    # The election, the four assemblies, the electoral system, and most parties are rows the `pk.ecp` packages hold.
    p.external.add(ecp.PAKISTAN)
    election = ecp.key("election", "2002")
    p.external.add(election)
    for prefix, assembly in ecp.ASSEMBLIES.items():
        if prefix != "NA":
            p.external.add(assembly.body)
            p.external.add(assembly.scheme)
    for url in FILES:
        p.uses(raw.cached(SOURCE, url))
    everything = all_seats()
    for seat in everything:
        add_seat(p, seat, election)
    named = {ecp.party_id(name) for name in NEW_PARTIES}
    for name in {c.party for seat in everything for c in seat.candidates} - {"Independent"}:
        if ecp.party_id(name) not in named:
            p.external.add(ecp.party_id(name))
    return p


def add_seat(p: Package, seat: Seat, election: str) -> None:
    assembly = ecp.ASSEMBLIES[seat.prefix]
    label = f"{seat.code} {seat.name}"
    contest = ecp.key("contest", "pa2002", seat.code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=assembly.body,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"2002 {assembly.label} Provincial Assembly election, {label}", "en", "official")
    p.add("slug", path=f"pk/2002-10-10-general/{seat.code.lower()}", entity_id=contest, is_current=True)
    unit = ecp.key("unit", "pa2002", seat.code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ecp.PAKISTAN,
        scheme_code=assembly.scheme,
        code=f"2002-10-10:{seat.code}",
        area_relation="unlocated",
        kind="standard",
    )
    p.name(unit, label, "en", "official")
    result_set = ecp.key("result_set", "pa2002", seat.code)
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
    p.add("note", id=ecp.key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    for c in seat.candidates:
        if not c.name:
            p.add(
                "note",
                id=ecp.key("note", result_set, "caveat", c.position),
                entity_id=result_set,
                kind="caveat",
                body_md=f"The volume prints no name for candidate {c.position}, who stood for {c.party}.",
                lang="en",
            )
    accounting = (
        (REGISTERED, seat.totals["Registered Voters"]),
        (CAST, seat.totals["Total Votes"]),
        (INVALID, seat.totals["Rejected Votes"]),
        (VALID, seat.totals["Valid Votes"]),
    )
    for measure, value in accounting:
        ecp.put(p, result_set, unit, None, measure, value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for c in seat.candidates:
        option = ecp.key("option", "pa2002", seat.code, c.position)
        independent = c.party == "Independent"
        person = ecp.key("person", "pa2002", seat.code, c.position)
        p.add("person", id=person)
        if c.name:
            p.name(person, c.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=c.position,
            ballot_label=c.name or None,
        )
        p.add(
            "candidacy",
            id=ecp.key("candidacy", "pa2002", seat.code, c.position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name or None,
            member_of_party_id=None if independent else ecp.party_id(c.party),
            is_independent=independent,
        )
        if c.retired:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="withdrew")
        ecp.put(p, result_set, unit, option, VOTES, c.votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.votes)
