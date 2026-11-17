"""Liechtenstein's municipal council and mayor (Vorsteher) elections from the government's results site."""

import functools
import html
import re
import time
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.li import regierung

SOURCE = "li.gemeindewahlen"
SITE = "https://www.gemeindewahlen.li"
LIECHTENSTEIN = str(uuid5(package.NAMESPACE, "polity\x1fLI"))
LI_GEO = f"{regierung.SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
# Site ids of the municipal elections, by election year.
IDS = {2003: 3, 2007: 2, 2011: 1, 2015: 9, 2019: 11, 2023: 14}
# Reporting units on the results site: 1-11 are the municipalities, 12 and 13 the electoral districts, 14 the country.
MUNICIPALITIES = range(1, 12)
COUNTRY_UNIT = 14
PAUSE = 0.3
FLAG_DERIVED = 1
MONTHS = {
    "Januar": 1,
    "Februar": 2,
    "März": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}
MAYOR_NOUN = {1: "Bürgermeister"}


def results_url(number: int) -> str:
    return f"{SITE}/resultate/{number}"


def unit_url(number: int, unit: int, first_round: str | None = None) -> str:
    return f"{SITE}/resultatgemeinde/{number}/{unit}" + (f"/?eid={first_round}" if first_round else "")


def listing_url(kind: str, number: int) -> str:
    return f"{SITE}/{kind}/{number}"


def protocol_url(eid: str) -> str:
    return f"{SITE}/wahlprotokoll.aspx?eid={eid}"


LISTINGS = ("vorsteher", "gemeinderaete", "kandidaten", "stimmbeteiligung")


def page(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8")


def fetch() -> None:
    def get(url: str) -> None:
        raw.fetch(SOURCE, url, suffix=".html")
        time.sleep(PAUSE)

    for number in IDS.values():
        get(results_url(number))
        for kind in LISTINGS:
            get(listing_url(kind, number))
        for unit in range(1, COUNTRY_UNIT + 1):
            get(unit_url(number, unit))
        for unit in MUNICIPALITIES:
            text = page(unit_url(number, unit))
            for eid in dict.fromkeys(re.findall(rf"/resultatgemeinde/{number}/{unit}/\?eid=(\d+)", text)):
                get(unit_url(number, unit, eid))
        for url in urls(number)[2]:
            get(url)


def urls(number: int) -> tuple[list[str], list[str], list[str]]:
    """The result pages, the first-round pages, and the protocols that one election's municipal pages link."""
    pages: list[str] = []
    rounds: list[str] = []
    protocols: list[str] = []
    for unit in MUNICIPALITIES:
        main = unit_url(number, unit)
        pages.append(main)
        text = page(main)
        found = list(dict.fromkeys(re.findall(rf"/resultatgemeinde/{number}/{unit}/\?eid=(\d+)", text)))
        for eid in found:
            rounds.append(unit_url(number, unit, eid))
            protocols += [protocol_url(e) for e in re.findall(r"wahlprotokoll\.aspx\?eid=(\d+)", page(rounds[-1]))]
        protocols += [protocol_url(e) for e in re.findall(r"wahlprotokoll\.aspx\?eid=(\d+)", text)]
    return pages, rounds, list(dict.fromkeys(protocols))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SYSTEM_COUNCIL = key("electoral_system", "council")
SYSTEM_MAYOR = key("electoral_system", "mayor")
SYSTEM_PLURALITY = key("electoral_system", "mayor-second-round")


def number(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text))


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def german_date(text: str) -> date:
    found = re.search(r"(\d{1,2})\. (\w+) (\d{4})", text)
    if found is None:
        raise LookupError(f"no date in {text!r}")
    return date(int(found.group(3)), MONTHS[found.group(2)], int(found.group(1)))


def election_day(year: int) -> date:
    found = re.search(r"Ergebnis vom (\d\d)\.(\d\d)\.(\d{4})", page(results_url(IDS[year])))
    if found is None:
        raise LookupError(f"no result date for {year}")
    return date(int(found.group(3)), int(found.group(2)), int(found.group(1)))


@dataclass
class Runner:
    id: str
    name: str
    party: str
    votes: int
    female: bool | None
    elected: bool


@dataclass
class Mayoral:
    noun: str
    runners: list[Runner]
    protocol: str | None
    no_majority: bool
    other_round: str | None
    second_round: date | None
    first_round: date | None


@dataclass
class Candidate:
    id: str
    name: str
    ballot_name: str
    party: str
    party_name: str
    votes: int
    elected: bool
    female: bool
    footnote: bool


@dataclass
class Origin:
    candidate_votes: int | None = None
    additional: int | None = None
    total: int | None = None
    unmodified: int | None = None
    modified: int | None = None


@dataclass
class Council:
    seats: dict[str, int]
    votes: dict[str, int]
    candidates: list[Candidate]
    origins: dict[str, Origin]
    protocol: str | None
    elected: list[tuple[str, str]] = field(default_factory=list)


def section(text: str, start: str, ends: tuple[str, ...]) -> str:
    begin = text.find(start)
    if begin < 0:
        return ""
    stops = [i for i in (text.find(end, begin + len(start)) for end in ends) if i >= 0]
    return text[begin : min(stops) if stops else len(text)]


def protocol_of(chunk: str) -> str | None:
    found = re.search(r"wahlprotokoll\.aspx\?eid=(\d+)", chunk)
    return found.group(1) if found else None


@functools.cache
def mayoral(year: int, unit: int, first_round: str | None = None) -> Mayoral:
    text = page(unit_url(IDS[year], unit, first_round))
    chunk = section(text, 'class="vsw', ("<!-- Mandate", '<div class="box">'))
    if not chunk:
        raise LookupError(f"no mayoral election on {year} unit {unit}")
    found = re.search(r'<h2 id="content_ucVorsteherWahl_h">([^<]*)<', chunk)
    if found is None:
        raise LookupError(f"no mayoral heading on {year} unit {unit}")
    heading = plain(found.group(1)).rstrip(" *")
    runners: list[Runner] = []
    for block in re.split(r'(?=<a href="/kandidat/\d+)', chunk)[1:]:
        identifier = re.match(r'<a href="/kandidat/(\d+)', block)
        label = re.search(r'(?:winner|opponent)__partybar"[^>]*>([^<]*)<', block)
        name = re.search(r'(?:winner__name|opponent__name)">([^<]*)<', block)
        votes = re.search(r'(?:winner__votes|opponent__votes)">([\d\'’]+) Stimmen', block)
        if identifier is None or label is None or name is None or votes is None:
            raise ValueError(f"unreadable mayoral candidate in {year} unit {unit}: {block[:200]!r}")
        kind = re.search(r'(?:winner__type|opponent__type)">([^<]*)<', block)
        runners.append(
            Runner(
                id=identifier.group(1),
                name=plain(name.group(1)),
                party=plain(label.group(1)),
                votes=number(votes.group(1)),
                female=kind.group(1).strip().endswith("in") if kind else None,
                elected='winner__voted">Gewählt' in block,
            )
        )
    if not runners:
        raise ValueError(f"no mayoral candidates in {year} unit {unit}")
    link = re.search(r'href="(/resultatgemeinde/\d+/\d+/\?eid=(\d+))"[^>]*>Zum (?:ersten|zweiten) Wahlgang', chunk)
    second = re.search(r"\*Zweiter Wahlgang vom (\d{1,2}\. \w+ \d{4})", chunk)
    first = re.search(r"Erster Wahlgang vom (\d{1,2}\. \w+ \d{4})", chunk)
    return Mayoral(
        noun=heading,
        runners=runners,
        protocol=protocol_of(chunk),
        no_majority="vsw--nomajority" in chunk,
        other_round=link.group(2) if link else None,
        second_round=german_date(second.group(1)) if second else None,
        first_round=german_date(first.group(1)) if first else None,
    )


@functools.cache
def council(year: int, unit: int) -> Council:
    text = page(unit_url(IDS[year], unit))
    head = section(text, 'class="grw"', ("<!-- Gemeinderat gew",))
    seats: dict[str, int] = {}
    votes: dict[str, int] = {}
    for row in re.findall(r'<tr class="datatable__row"[^>]*>(.*?)</tr>', head, re.DOTALL):
        party = re.search(r"<b>([^<]*)</b>", row)
        count = re.search(r'datatable__seatsnr">(\d+)<', row)
        cast = re.search(r'datatable__votes"><span>([^<]*)<', row)
        if party is None or count is None or cast is None:
            raise ValueError(f"unreadable list row in {year} unit {unit}: {row[:200]!r}")
        seats[plain(party.group(1))] = int(count.group(1))
        votes[plain(party.group(1))] = number(cast.group(1))
    table = section(text, "<!-- Gemeinderat Kandidaten", ("<!-- Stimmenherkunft", "</form>"))
    candidates: list[Candidate] = []
    for row in re.findall(
        r'<tr id="content_ucGemeinderatskandidaten_repCandidates_trRow_\d+"(.*?)</tr>', table, re.DOTALL
    ):
        link = re.search(r'href="/kandidat/(\d+)[^"]*"[^>]*>([^<]*)(<sup>[^<]*</sup>)?', row)
        label = re.search(r'data-name="([^"]*)"', row)
        kind = re.search(r'data-type="([^"]*)"', row)
        party_name = re.search(r'data-partyname="([^"]*)"', row)
        party = re.search(r"<td><span>([^<]*)</span></td>\s*<td><span id=\"[^\"]*lblStimmen", row)
        cast = re.search(r'lblStimmen_\d+">([^<]*)<', row)
        if None in (link, label, kind, party_name, party, cast):
            raise ValueError(f"unreadable candidate row in {year} unit {unit}: {row[:300]!r}")
        assert link and label and kind and party_name and party and cast
        candidates.append(
            Candidate(
                id=link.group(1),
                name=plain(label.group(1)),
                ballot_name=plain(link.group(2)),
                party=plain(party.group(1)),
                party_name=plain(party_name.group(1)),
                votes=number(cast.group(1)),
                elected="grk__voted" in row,
                female=kind.group(1).endswith("in"),
                footnote=link.group(3) is not None,
            )
        )
    origins: dict[str, Origin] = {}
    for block in re.split(r'(?=<div class="shk">)', text)[1:]:
        block = block.split("</table>", 1)[0]
        subtotal = re.search(r"Total ([^\s<]+)-Kandidatenstimmen[^<]*</span></td>\s*<td[^>]*><span>([^<]*)<", block)
        if subtotal is None:
            continue
        party = subtotal.group(1)
        origin = origins[party] = Origin(candidate_votes=number(subtotal.group(2)))
        extra = re.search(r"-Zusatzstimmen[^<]*</span></td>\s*<td[^>]*><span>([^<]*)<", block)
        total = re.search(r"<b>Total Stimmen</b></td>\s*<td[^>]*><span>([^<]*)<", block)
        origin.additional = number(extra.group(1)) if extra else None
        origin.total = number(total.group(1)) if total else None
        for count, modified, owner in re.findall(r"\.\.\.von (\d+) (un)?veränderten ([^\s-]+)-Stimmzetteln", block):
            if owner == party:
                if modified:
                    origin.unmodified = int(count)
                else:
                    origin.modified = int(count)
    return Council(
        seats=seats,
        votes=votes,
        candidates=candidates,
        origins=origins,
        protocol=protocol_of(head),
    )


# The protocol labels that carry each national accounting figure. The protocols of 2007 and 2011 print the same
# figures under different labels and, for the mayor's election, with some labels attached to the wrong line, so
# those two years keep only the electoral roll and the voting cards, which agree between the two protocols.
PROTOCOL_LABELS = {
    "registered": ("Stimmberechtigte",),
    "voters": ("Total abgegebene Stimmkarten", "Total Abgegebene Stimmkarten"),
    "cast": ("Eingelegte Stimmkuverts",),
    "invalid": ("davon ungültige Stimmen",),
    "blank": ("davon leere Stimmkuverts (Stimmen)",),
    "valid": ("davon gültige Stimmen",),
    "unmodified": ("Unveränderte Stimmzettel",),
    "modified": ("Veränderte Stimmzettel",),
}


def protocol(year: int, eid: str) -> dict[str, tuple[int, int]]:
    """One protocol's figures as {name: (value, flags)}."""
    text = page(protocol_url(eid))
    rows: dict[str, int] = {}
    for label, value in re.findall(r"<td[^>]*><span>([^<]*)</span></td>\s*<td[^>]*><span>([^<]*)</span>", text):
        if re.search(r"\d", value) and "%" not in value and plain(label) not in rows:
            rows[plain(label)] = number(value)
    found: dict[str, tuple[int, int]] = {}
    if year < 2015:
        if "Stimmberechtigte" in rows:
            found["registered"] = (rows["Stimmberechtigte"], 0)
        postal = next((v for k, v in rows.items() if k.startswith("Brieflich abgegebene")), None)
        person = next((v for k, v in rows.items() if k.startswith("Persönlich")), None)
        if "registered" in found and postal is not None and person is not None:
            found["voters"] = (postal + person, FLAG_DERIVED)
        return found
    for name, labels in PROTOCOL_LABELS.items():
        for label in labels:
            if label in rows:
                found[name] = (rows[label], 0)
                break
    return found


def national(year: int) -> dict[str, int]:
    """The authority's own seat totals by party, mayors included, from the results page."""
    text = page(results_url(IDS[year]))
    chunk = section(text, 'class="resultate__summary', ('class="resultate__groupresults"',))
    return {
        plain(party): int(count)
        for count, party in re.findall(r'class="seat__nr">(\d+)</div>.*?class="seat__short">([^<]*)<', chunk, re.DOTALL)
    }


def tally(year: int) -> dict[str, int]:
    """The same totals from the municipalities: council seats plus one seat for each mayor's party."""
    seats: dict[str, int] = {}
    for unit in MUNICIPALITIES:
        for party, count in council(year, unit).seats.items():
            seats[party] = seats.get(party, 0) + count
        winner = next(r for r in mayoral(year, unit).runners if r.elected)
        seats[winner.party] = seats.get(winner.party, 0) + 1
    return {party: count for party, count in seats.items() if count}


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{year}" for year in IDS)]


def parse() -> None:
    registry().write()
    for year in IDS:
        stated = {party: count for party, count in national(year).items() if count}
        if stated != tally(year):
            raise ValueError(f"{year}: party seats {tally(year)} differ from the authority's {stated}")
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def party_key(abbreviation: str) -> str:
    return key("party", abbreviation)


# The lists and parties of the municipal elections, with the names the site gives them and the colors it draws them
# in. PL (Parteilos) is a label on a mayoral candidate who ran without a party; it has no party row.
PARTIES = {
    "FBP": ("Fortschrittliche Bürgerpartei", "#4d4e50", "party"),
    "VU": ("Vaterländische Union", "#cc0033", "party"),
    "FL": ("Freie Liste", "#bbbbbb", "party"),
    "PU": ("Parteiunabhängig", "#9d358b", "independent_group"),
    "DU": ("Die Unabhängigen", "#ffcc00", "party"),
    "DPL": ("Demokraten Pro Liechtenstein", "#005fab", "party"),
    "JL": ("Junge Liste", "#98c01f", "party"),
}
NO_PARTY = {"PU", "PL"}
COUNCIL_NOTE = (
    "Each municipality elects a council, in addition to its mayor, by proportional representation from open lists: "
    "every voter has as many votes as the council has seats (without the mayor), may give them to candidates of any "
    "list with at most one vote per candidate, and the votes a ballot leaves unused go to its own list as additional "
    "votes. The seats of a list go to its candidates with the most votes."
)
MAYOR_NOTE = (
    "The mayor (Vorsteher; Bürgermeister in Vaduz) is elected by a vote for one candidate. A candidate with an "
    "absolute majority of the valid votes is elected in the first round. When none has one, a second round is held "
    "some weeks later, in which the candidate with the most votes is elected."
)


def office(unit: int) -> str:
    return key("office", unit)


def body(unit: int) -> str:
    return key("body", unit)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Liechtenstein's municipal councils and mayors, their electoral systems, and the parties of the municipal "
        "elections",
        depends=[LI_GEO],
    )
    p.external.add(LIECHTENSTEIN)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Information und Kommunikation der Regierung, Stabsstelle Regierungskanzlei",
        title="Gemeindewahlen: Ergebnisse",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        license_url=f"{SITE}/datenschutz",
        attribution="Regierung des Fürstentums Liechtenstein, gemeindewahlen.li",
    )
    for number_ in IDS.values():
        p.artifact(raw.cached(SOURCE, results_url(number_)), SOURCE_ID)
    for unit, (name, _, _) in regierung.PLACES.items():
        council_body = body(unit)
        p.add("body", id=council_body, polity_id=LIECHTENSTEIN, kind="council")
        p.name(council_body, f"Gemeinderat {name}", "de", "official")
        p.name(council_body, f"{name} Municipal Council", "en", "english", False)
        post = office(unit)
        p.add("office", id=post, polity_id=LIECHTENSTEIN, body_id=council_body, kind="mayor")
        noun = MAYOR_NOUN.get(unit, "Vorsteher")
        p.name(post, f"{noun} von {name}", "de", "official")
        p.name(post, f"Mayor of {name}", "en", "english", False)
    for id, rules, note in (
        (
            SYSTEM_COUNCIL,
            {"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "substitute"}},
            COUNCIL_NOTE,
        ),
        (SYSTEM_MAYOR, {"formula": "two_round", "majority": 0.5}, MAYOR_NOTE),
        (SYSTEM_PLURALITY, {"formula": "fptp"}, MAYOR_NOTE),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=LIECHTENSTEIN,
            valid_from=date(2003, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    for abbreviation, (name, color, kind) in PARTIES.items():
        id = party_key(abbreviation)
        p.add("party", id=id, kind=kind, polity_id=LIECHTENSTEIN)
        p.add("external_id", entity_id=id, scheme="li.gemeindewahlen.party", value=abbreviation)
        p.name(id, name, "de", "official")
        p.name(id, abbreviation, "de", "abbreviation", False)
        p.add("party_color", party_id=id, color=color, valid_from=None, valid_to=None, source_id=SOURCE_ID)
    return p


COUNCIL_METHOD = (
    "Counted by the municipalities and reported by the government's municipal elections site. Registered voters are "
    "the electoral roll (Stimmberechtigte). Voters are the voting cards cast (Stimmkarten), by post and in person; "
    "ballots cast are the voting envelopes put in the box (Stimmkuverts), each valid, invalid, or blank. List votes "
    "are the votes for a list's candidates plus its additional votes (Zusatzstimmen), the votes a ballot leaves "
    "unused and credits to its own list. Unmodified and changed ballots are those of the list. Seats are the council "
    "seats without the mayor; a candidate marked as elected holds one of them."
)
MAYOR_METHOD = (
    "Counted by the municipalities and reported by the government's municipal elections site. Registered voters are "
    "the electoral roll (Stimmberechtigte). Voters are the voting cards cast (Stimmkarten), by post and in person; "
    "ballots cast are the voting envelopes put in the box (Stimmkuverts), each valid, invalid, or blank. Each valid "
    "ballot is one vote for one candidate."
)
YEAR_NOTES = {
    2003: (
        " The 2003 pages publish no protocol, so the electoral roll, voting cards, and invalid and blank ballots are "
        "not stored for this year, and they do not split a list's votes into candidate and additional votes."
    ),
    2007: (
        " The protocols of 2007 and 2011 print the figures of the voting cards under labels that differ between "
        "years and, in 2011, sometimes against the wrong line, so only the electoral roll and the voting cards are "
        "stored. Voting cards are the sum of the cards cast by post and in person."
    ),
}
YEAR_NOTES[2011] = YEAR_NOTES[2007]
ACCOUNTING = (("registered", 2), ("voters", 14), ("cast", 3), ("valid", 4), ("invalid", 5), ("blank", 6))


def person_key(year: int, unit: int, name: str) -> str:
    return key("person", year, unit, name)


def election(year: int) -> Package:
    when = election_day(year)
    mayors = {unit: mayoral(year, unit) for unit in MUNICIPALITIES}
    rounds = {unit: m.other_round for unit, m in mayors.items() if m.other_round}
    second_day = {m.second_round for m in mayors.values() if m.second_round}
    if len(second_day) > 1:
        raise ValueError(f"{year} has several second-round days {second_day}")
    runoff_day = second_day.pop() if second_day else None
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Liechtenstein municipal council and mayoral elections by municipality",
        depends=[LI_GEO, REGISTRY],
    )
    p.external.add(LIECHTENSTEIN)
    number_ = IDS[year]
    pages, round_pages, protocols = urls(number_)
    for url in [results_url(number_), *(listing_url(kind, number_) for kind in LISTINGS), *pages, *round_pages]:
        p.uses(raw.cached(SOURCE, url))
    for url in protocols:
        p.uses(raw.cached(SOURCE, url))
    path = f"li/{when}-municipal"
    election_id = key("election", when)
    p.add(
        "election", id=election_id, polity_id=LIECHTENSTEIN, kind="local", date_from=when, date_to=when, status="held"
    )
    p.name(election_id, f"{year} Liechtenstein municipal elections", "en", "official")
    p.name(election_id, f"Gemeindewahlen {year}", "de", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    runoff_id = None
    if runoff_day:
        runoff_id = key("election", runoff_day)
        p.add(
            "election",
            id=runoff_id,
            polity_id=LIECHTENSTEIN,
            parent_id=election_id,
            kind="runoff",
            date_from=runoff_day,
            date_to=runoff_day,
            status="held",
        )
        p.name(runoff_id, f"{year} Liechtenstein mayoral elections, second round", "en", "official")
        p.name(runoff_id, f"Vorsteherwahlen {year}, zweiter Wahlgang", "de", "native", False)
        p.add("slug", path=f"li/{runoff_day}-municipal-runoff", entity_id=runoff_id, is_current=True)

    units: dict[tuple[str, int], str] = {}

    def unit_of(day: date, unit: int) -> str:
        if (str(day), unit) not in units:
            code = regierung.PLACES[unit][1]
            id = units[(str(day), unit)] = key("unit", day, code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=LIECHTENSTEIN,
                scheme_code="li.gemeinde",
                code=f"{day}:{code}",
                area_relation="equals",
                area_id=regierung.area("li.gemeinde", code),
                kind="standard",
            )
        return units[(str(day), unit)]

    def accounting(set_id: str, unit_id: str, eid: str | None, extra: dict[str, tuple[int, int]] | None = None) -> None:
        if eid is None:
            return
        found = {**protocol(year, eid), **(extra or {})}
        for name, measure in ACCOUNTING:
            if name in found:
                value, flags = found[name]
                p.add(
                    "result_value",
                    result_set_id=set_id,
                    unit_id=unit_id,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=flags,
                    value=value,
                )

    def value(set_id: str, unit_id: str, option: str, measure: int, count: int, flags: int = 0) -> None:
        p.add(
            "result_value",
            result_set_id=set_id,
            unit_id=unit_id,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=count,
        )

    def result_set(contest: str, election: str, method: str, set_id: str) -> None:
        p.add(
            "result_set",
            id=set_id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", set_id), entity_id=set_id, kind="methodology", body_md=method, lang="en")

    people: dict[tuple[int, str], str] = {}
    genders: dict[tuple[int, str], bool] = {}

    def person(unit: int, name: str, site_id: str, female: bool | None) -> str:
        found = people.get((unit, name))
        if found is None:
            found = people[(unit, name)] = person_key(year, unit, name)
            p.add("person", id=found, gender=None)
            p.add("external_id", entity_id=found, scheme="li.gemeindewahlen.candidate", value=site_id)
            p.name(found, name, "de", "official")
        if female is not None:
            genders[(unit, name)] = female
        return found

    for unit, (name, code, _) in regierung.PLACES.items():
        slug = changeset.slug(name)
        here = council(year, unit)
        # The council contest.
        contest = key("contest", when, unit, "council")
        seats = sum(here.seats.values())
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(unit),
            district_area_id=regierung.area("li.gemeinde", code),
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM_COUNCIL,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {name} municipal council", "en", "english")
        p.name(contest, f"Gemeinderatswahl {name} {year}", "de", "official", False)
        p.add("slug", path=f"{path}/{slug}-council", entity_id=contest, is_current=True)
        set_id = key("result_set", when, unit, "council")
        result_set(contest, election_id, COUNCIL_METHOD + YEAR_NOTES.get(year, ""), set_id)
        unit_id = unit_of(when, unit)
        lists = {abbreviation: key("option", when, unit, "list", abbreviation) for abbreviation in here.votes}
        for abbreviation, option in lists.items():
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_key(abbreviation),
                ballot_label=PARTIES[abbreviation][0],
            )
            if here.seats[abbreviation]:
                p.add("outcome", result_set_id=set_id, option_id=option, kind="won", seats=here.seats[abbreviation])
            value(set_id, unit_id, option, 8, here.votes[abbreviation])
            origin = here.origins.get(abbreviation)
            if origin is not None:
                if origin.total is not None and origin.total != here.votes[abbreviation]:
                    raise ValueError(f"{year} {name} {abbreviation}: origin total differs from list votes")
                if origin.additional is not None:
                    value(set_id, unit_id, option, 9, origin.additional)
                if origin.unmodified is not None:
                    value(set_id, unit_id, option, 22, origin.unmodified)
                if origin.modified is not None:
                    value(set_id, unit_id, option, 23, origin.modified)
        elected = 0
        sums: dict[str, int] = {}
        for candidate in here.candidates:
            if candidate.party_name != PARTIES[candidate.party][0]:
                raise ValueError(f"{year} {name}: {candidate.party} is {candidate.party_name!r}")
            option = key("option", when, unit, "candidate", candidate.id)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=lists[candidate.party],
                ballot_label=candidate.name,
            )
            who = key("person", candidate.id)
            p.add("person", id=who, gender="female" if candidate.female else "male")
            p.add("external_id", entity_id=who, scheme="li.gemeindewahlen.candidate", value=candidate.id)
            p.name(who, candidate.name, "de", "official")
            p.add(
                "candidacy",
                id=key("candidacy", when, candidate.id),
                person_id=who,
                option_id=option,
                role="list_member",
                ballot_name=candidate.ballot_name,
                member_of_party_id=None if candidate.party in NO_PARTY else party_key(candidate.party),
                is_independent=candidate.party in NO_PARTY,
            )
            value(set_id, unit_id, option, 10, candidate.votes)
            sums[candidate.party] = sums.get(candidate.party, 0) + candidate.votes
            if candidate.elected:
                elected += 1
                p.add("outcome", result_set_id=set_id, option_id=option, kind="elected")
        if elected != seats:
            raise ValueError(f"{year} {name}: {elected} elected candidates for {seats} seats")
        for abbreviation, origin in here.origins.items():
            if origin.candidate_votes != sums.get(abbreviation, 0):
                raise ValueError(f"{year} {name} {abbreviation}: candidate rows differ from the printed subtotal")
        accounting(set_id, unit_id, here.protocol)

        # The mayoral contest, first or only round.
        m = mayors[unit]
        first = mayoral(year, unit, rounds[unit]) if unit in rounds else m
        office_name = MAYOR_NOUN.get(unit, "Vorsteher")
        contest = key("contest", when, unit, "mayor")
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=office(unit),
            district_area_id=regierung.area("li.gemeinde", code),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM_MAYOR,
            default_result_kind="counted",
        )
        suffix = ", first round" if unit in rounds else ""
        p.name(contest, f"{year} mayor of {name}{suffix}", "en", "english")
        p.name(contest, f"{office_name}wahl {name} {year}", "de", "official", False)
        p.add("slug", path=f"{path}/{slug}-mayor", entity_id=contest, is_current=True)
        round_one = key("result_set", when, unit, "mayor")
        result_set(contest, election_id, MAYOR_METHOD + YEAR_NOTES.get(year, ""), round_one)
        later = {r.name for r in m.runners} if unit in rounds else set()
        options: dict[str, str] = {}
        for runner in first.runners:
            options[runner.name] = key("option", when, unit, "mayor", runner.name)
            if runner.party != "PL" and runner.party not in PARTIES:
                raise ValueError(f"{year} {name}: unknown party {runner.party}")
            p.add(
                "contest_option",
                id=options[runner.name],
                contest_id=contest,
                kind="candidate",
                ballot_label=runner.name,
            )
            who = person(unit, runner.name, runner.id, runner.female)
            p.add(
                "candidacy",
                id=key("candidacy", when, unit, "mayor", runner.name),
                person_id=who,
                option_id=options[runner.name],
                role="head",
                ballot_name=runner.name,
                member_of_party_id=None if runner.party in NO_PARTY else party_key(runner.party),
                is_independent=runner.party in NO_PARTY,
            )
            if unit in rounds:
                outcome = "advanced" if runner.name in later else "eliminated"
            else:
                outcome = "elected" if runner.elected else "lost"
            p.add("outcome", result_set_id=round_one, option_id=options[runner.name], kind=outcome)
        unit_one = unit_of(when, unit)
        for runner in first.runners:
            value(round_one, unit_one, options[runner.name], 1, runner.votes)
        accounting(round_one, unit_one, first.protocol)

        # The second round, a runoff election of its own.
        if unit in rounds and runoff_id and runoff_day:
            second = key("contest", runoff_day, unit, "mayor")
            p.add(
                "contest",
                id=second,
                election_id=runoff_id,
                kind="office",
                office_id=office(unit),
                district_area_id=regierung.area("li.gemeinde", code),
                magnitude=1,
                round_number=2,
                previous_round_id=contest,
                electoral_system_id=SYSTEM_PLURALITY,
                default_result_kind="counted",
            )
            p.name(second, f"{year} mayor of {name}, second round", "en", "english")
            p.name(second, f"{office_name}wahl {name} {year}, zweiter Wahlgang", "de", "official", False)
            p.add("slug", path=f"li/{runoff_day}-municipal-runoff/{slug}-mayor", entity_id=second, is_current=True)
            round_two = key("result_set", runoff_day, unit, "mayor")
            result_set(second, runoff_id, MAYOR_METHOD + YEAR_NOTES.get(year, ""), round_two)
            unit_two = unit_of(runoff_day, unit)
            for runner in m.runners:
                option = key("option", runoff_day, unit, "mayor", runner.name)
                p.add("contest_option", id=option, contest_id=second, kind="candidate", ballot_label=runner.name)
                who = person(unit, runner.name, runner.id, runner.female)
                p.add(
                    "candidacy",
                    id=key("candidacy", runoff_day, unit, "mayor", runner.name),
                    person_id=who,
                    option_id=option,
                    role="head",
                    ballot_name=runner.name,
                    member_of_party_id=None if runner.party in NO_PARTY else party_key(runner.party),
                    is_independent=runner.party in NO_PARTY,
                )
                p.add(
                    "outcome", result_set_id=round_two, option_id=option, kind="elected" if runner.elected else "lost"
                )
                value(round_two, unit_two, option, 1, runner.votes)
            accounting(round_two, unit_two, m.protocol)
    for (unit, name), female in genders.items():
        for row in p.rows["person"]:
            if row["id"] == people[(unit, name)]:
                row["gender"] = "female" if female else "male"
    if year >= 2015:
        reason = (
            "Each voter has as many votes as the council has seats, so the votes for a list's candidates and its "
            "additional votes never add up to the valid ballots. List votes are stored as list votes (measure 8), "
            "candidate votes as candidate votes (measure 10), and valid ballots as valid (measure 4)."
        )
        if year == 2023:
            reason += (
                " The 2023 protocol of the mayoral election in Vaduz prints 1,919 valid ballots against 1,903 votes "
                "for its two candidates (1,053 and 850); both are stored as printed."
            )
        p.accept("accounting.votes_valid", reason)
    return p
