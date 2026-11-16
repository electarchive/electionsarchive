"""Liechtenstein's Landtag elections from the government's results site, by municipality."""

import functools
import html
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "li.regierung"
LANDTAG = "https://www.landtagswahlen.li"
LIECHTENSTEIN = str(uuid5(package.NAMESPACE, "polity\x1fLI"))
# Site ids of the Landtag elections, by election year.
LANDTAG_IDS = {2001: 5, 2005: 6, 2009: 7, 2013: 8, 2017: 10, 2021: 12, 2025: 16}
# Reporting units on the results site: 1-11 are the municipalities, 12 and 13 the electoral districts, 14 the country.
MUNICIPALITIES = range(1, 12)
DISTRICT_UNITS = {12: "Oberland", 13: "Unterland"}
COUNTRY_UNIT = 14
PAUSE = 0.3
# Candidate votes the site's municipality pages print wrongly, as {(year, unit, candidate): votes to add}. Each is
# proven by the printed totals: the 2005 Eschen page's VU candidate and additional votes fall 30 short of its VU party
# votes, and the Unterland page's total for candidate 770 is 30 above the sum over the municipalities.
CORRECTIONS = {(2005, 7, "770"): 30}
FLAG_DERIVED = 1

# Municipality by site unit: name, ISO 3166-2 code, electoral district.
PLACES = {
    1: ("Vaduz", "LI-11", "Oberland"),
    2: ("Balzers", "LI-01", "Oberland"),
    3: ("Planken", "LI-05", "Oberland"),
    4: ("Schaan", "LI-07", "Oberland"),
    5: ("Triesen", "LI-09", "Oberland"),
    6: ("Triesenberg", "LI-10", "Oberland"),
    7: ("Eschen", "LI-02", "Unterland"),
    8: ("Gamprin", "LI-03", "Unterland"),
    9: ("Mauren", "LI-04", "Unterland"),
    10: ("Ruggell", "LI-06", "Unterland"),
    11: ("Schellenberg", "LI-08", "Unterland"),
}
SEATS = {"Oberland": 15, "Unterland": 10}
PARTIES = {
    "FBP": "Fortschrittliche Bürgerpartei",
    "VU": "Vaterländische Union",
    "FL": "Freie Liste",
    "DU": "Die Unabhängigen",
    "DPL": "Demokraten Pro Liechtenstein",
}
# Party colors as the results site draws them.
COLORS = {"FBP": "#4d4e50", "VU": "#cc0033", "FL": "#bbbbbb", "DPL": "#005fab"}
METHOD = (
    "Counted by the municipalities and reported by the government's Landtag results site per municipality. "
    "Registered voters are the electoral roll (Stimmberechtigte). Voters are the voting cards cast (Stimmkarten), "
    "by post and in person where the protocol splits them. Ballots cast are the voting envelopes put in the box "
    "(Stimmkuverts); each is valid, invalid, or blank. Every voter has as many votes as the district has seats and "
    "may give them to candidates of any list. A list's party votes are the votes for its candidates plus its "
    "additional votes (Zusatzstimmen), the votes a ballot leaves unused and credits to its own list. Ballots are "
    "unchanged or changed where the site reports them. Candidates marked Abg. are elected members and candidates "
    "marked Stv. are elected deputies (Stellvertreter), who take a seat when a member leaves."
)
NOTES = {
    2001: (
        "The 2001 protocol publishes no split of voting cards by post and in person, no count of men and women, and "
        "no count of unchanged and changed ballots; those fields read zero on the site and are not stored. The site "
        "gives the total of invalid ballots as one figure, which this archive stores. It gives no additional votes "
        "by list. The district pages' candidate totals differ from the sums of the municipality pages for one Oberland "
        "FL candidate (105 votes) and five Unterland FBP candidates (the differences net to zero); the municipality "
        "values and the district totals are stored as printed."
    ),
    2005: (
        "On the Eschen page the votes for candidate 770 (VU) are 30 short of what the VU party votes and the Unterland "
        "total require; they are stored 30 higher than printed."
    ),
    2013: (
        "The 2013 protocol omits the count of valid ballots. It is stored as the sum of the unchanged and changed "
        "ballots, which the protocol publishes, and flagged as derived."
    ),
}


def result_url(number: int, unit: int) -> str:
    return f"{LANDTAG}/resultat/{number}/{unit}"


def protocol_url(number: int, unit: int) -> str:
    return f"{LANDTAG}/wahlprotokoll.aspx?eeid={number}&ukid={unit}"


def parliament_url(number: int) -> str:
    return f"{LANDTAG}/neuerlandtag/{number}"


def fetch() -> None:
    for number in LANDTAG_IDS.values():
        raw.fetch(SOURCE, parliament_url(number), suffix=".html")
        for unit in range(1, 15):
            for url in (result_url(number, unit), protocol_url(number, unit)):
                raw.fetch(SOURCE, url, suffix=".html")
                time.sleep(PAUSE)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


LI_SOURCE = key("source", SOURCE)
LANDTAG_BODY = key("body", "landtag")
NATION = key("area", "li.land", "LI")
SYSTEM = key("electoral_system", "landtag", 2001)


def page(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8")


def number(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text))


def first(pattern: str, text: str, flags: int = 0) -> str:
    found = re.search(pattern, text, flags)
    if found is None:
        raise LookupError(f"{pattern} not found")
    return found.group(1)


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


@dataclass
class Candidate:
    id: str
    surname: str
    given: str
    status: str
    votes: int


@dataclass
class Section:
    abbreviation: str
    name: str
    candidates: list[Candidate]
    candidate_votes: int
    additional: int | None
    party_votes: int | None
    ballots: dict[str, int] = field(default_factory=dict)


@dataclass
class Result:
    title: str
    party_votes: dict[str, int]
    sections: dict[str, Section]


def abbreviation_of(name: str) -> str:
    for abbreviation, full in PARTIES.items():
        if full == name:
            return abbreviation
    raise LookupError(f"unknown party {name!r}")


@functools.cache
def result(year: int, unit: int) -> Result:
    text = page(result_url(LANDTAG_IDS[year], unit))
    title = plain(first(r'class="main__title">([^<]*)<', text))
    bars = {
        plain(label): number(votes)
        for label, votes in re.findall(
            r'class="bars__label">([^<]*)</span>\s*<span class="bars__number">([^<]*)<', text
        )
    }
    sections: dict[str, Section] = {}
    for chunk in re.split(r'(?=<h3 [^>]*class="kdtb__bar")', text)[1:]:
        name = plain(first(r'kdtb__bartext">([^<]*)<', chunk))
        abbreviation = abbreviation_of(name)
        candidates = []
        for row in re.findall(r'<tr id="[^"]*" class="kdtb__row">(.*?)</tr>', chunk, re.DOTALL):
            link = re.search(r'href="/kandidat/(\d+)/?"[^>]*><span>([^<]*)</span>([^<]*)</a>', row)
            status = re.search(r'gewaehlttext">([^<]*)<', row)
            votes = re.search(r'kdtb__stimmen" data-sort-value="(\d+)"', row)
            if link is None or votes is None:
                raise ValueError(f"unreadable candidate row {row!r} in {year}/{unit}")
            candidates.append(
                Candidate(
                    id=link.group(1),
                    surname=plain(link.group(2)),
                    given=plain(link.group(3)),
                    status=plain(status.group(1)) if status else "",
                    votes=int(votes.group(1)),
                )
            )
        total = re.search(r"Total [A-Z]+ Kandidatenstimmen[^<]*</span></td>\s*<td[^>]*><span>([^<]*)<", chunk)
        extra = re.search(r"-Zusatzstimmen[^<]*</span></td>\s*<td[^>]*><span>([^<]*)<", chunk)
        party_total = re.search(r"<b>Total [A-Z]+-Parteistimmen[^<]*</b></span></td>\s*<td[^>]*><span>([^<]*)<", chunk)
        ballots: dict[str, int] = {}
        for count, kind, owner in re.findall(
            r'phstk__rowtext"[^>]*>\.\.\.von (\d+)(?: \([\d.]+%\))? (un)?veränderten ([A-Z]+)-Stimmzetteln<', chunk
        ):
            if owner == abbreviation:
                ballots["unmodified" if kind else "modified"] = int(count)
        sections[abbreviation] = Section(
            abbreviation=abbreviation,
            name=name,
            candidates=candidates,
            candidate_votes=number(total.group(1)) if total else sum(c.votes for c in candidates),
            additional=number(extra.group(1)) if extra else None,
            party_votes=number(party_total.group(1)) if party_total else None,
            ballots=ballots,
        )
    for (fixed, at, who), delta in CORRECTIONS.items():
        if (fixed, at) == (year, unit):
            for section in sections.values():
                for candidate in section.candidates:
                    if candidate.id == who:
                        candidate.votes += delta
                        section.candidate_votes += delta
    return Result(title=title, party_votes=bars, sections=sections)


def protocol(year: int, unit: int) -> dict[str, list[int]]:
    text = page(protocol_url(LANDTAG_IDS[year], unit))
    rows: dict[str, list[int]] = defaultdict(list)
    for label, value in re.findall(r"<td[^>]*><span>([^<]*)</span></td>\s*<td[^>]*><span>([^<]*)</span></td>", text):
        if re.search(r"\d", value) and "%" not in value:
            rows[plain(label)].append(number(value))
    return rows


LABELS = {
    "registered": ("Stimmberechtigte",),
    "voters": ("Abgegebene Stimmkarten", "Brieflich und persönlich an der Urne abgegebene Stimmk."),
    "postal": ("Brieflich abgegebene Stimmkarten", "Zahl der brieflich abgegebenen Stimmkarten"),
    "person": ("Persönlich abgegebene Stimmkarten", "Zahl der persönlich an der Urne abgegebenen Stimmkarten"),
    "cast": ("Eingelegte Stimmkuverts", "Zahl der eingelegten Stimmkuverts"),
    "invalid": ("Ungültige, eingelegte Simmen", "davon ungültige Stimmen"),
    "blank": ("Eingelegte, leere Stimmen", "davon leere Stimmkuverts (Stimmen)"),
    "valid": ("Total gültige Stimmen", "davon gültige Stimmen"),
    "unmodified": ("Unveränderte Stimmzettel", "davon unveränderte Stimmzettel"),
    "modified": ("Veränderte Stimmzettel", "davon veränderte Stimmzettel"),
}


def figures(year: int, unit: int) -> dict[str, tuple[int, int]]:
    """The protocol of one unit as {name: (value, flags)}."""
    rows = protocol(year, unit)
    found: dict[str, tuple[int, int]] = {}
    for name, labels in LABELS.items():
        for label in labels:
            if label in rows:
                found[name] = (rows[label][0], 0)
                break
    if year == 2001:
        found["invalid"] = (rows["Total ungültige Stimmen"][0], 0)
        for name in ("postal", "person", "unmodified", "modified"):
            found.pop(name, None)
    elif "valid" not in found and "unmodified" in found:
        found["valid"] = (found["unmodified"][0] + found["modified"][0], FLAG_DERIVED)
    return found


def day(year: int) -> date:
    text = page(parliament_url(LANDTAG_IDS[year]))
    found = re.search(r"Ergebnis vom (\d\d)\.(\d\d)\.(\d{4})", text)
    if found is None:
        raise LookupError(f"no result date for {year}")
    return date(int(found.group(3)), int(found.group(2)), int(found.group(1)))


def seats(year: int) -> dict[str, dict[str, int]]:
    """Seats won as {district: {party: seats}} from the new-parliament page."""
    text = page(parliament_url(LANDTAG_IDS[year]))
    table = first(r'<table class="mandmatrix">(.*?)</table>', text, re.DOTALL)
    parties = [plain(p) for p in re.findall(r'mandmatrix__th"[^>]*>([^<]+)<', table)]
    won: dict[str, dict[str, int]] = {}
    for name, available, cells in re.findall(
        r'<tr class="mandmatrix__row">\s*<td[^>]*><span class="mandmatrix__name">([^<]*)</span>'
        r'<span class="mandmatrix__available">(\d+)</span></td>(.*?)</tr>',
        table,
        re.DOTALL,
    ):
        counts = [int(c) for c in re.findall(r'mandmatrix__count">(\d+)<', cells)]
        if int(available) != SEATS[name] or sum(counts) != SEATS[name]:
            raise ValueError(f"{year} {name}: seat table {counts} does not fill {available} seats")
        won[name] = dict(zip(parties, counts, strict=True))
    return won


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day(year)}" for year in LANDTAG_IDS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in LANDTAG_IDS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party(abbreviation: str) -> str:
    return key("party", abbreviation)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Liechtenstein's two Landtag electoral districts and 11 municipalities, as the results site reports them",
    )
    p.external.add(LIECHTENSTEIN)
    for code, rank, kind, noun in (
        ("li.land", 0, "administrative", "country"),
        ("li.wahlkreis", 1, "electoral_district", "electoral district"),
        ("li.gemeinde", 2, "administrative", "municipality"),
    ):
        p.add("scheme", code=code, polity_id=LIECHTENSTEIN, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NATION, scheme_code="li.land")
    p.add("area_code", area_id=NATION, code="LI")
    p.name(NATION, "Liechtenstein", "de", "official")
    p.name(NATION, "Liechtenstein", "en", "official", False)
    for district in SEATS:
        id = area("li.wahlkreis", district)
        p.add("area", id=id, scheme_code="li.wahlkreis")
        p.add("area_code", area_id=id, code=district)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, f"Wahlkreis {district}", "de", "official")
        p.name(id, f"{district} electoral district", "en", "english", False)
    for unit, (name, code, district) in PLACES.items():
        id = area("li.gemeinde", code)
        p.add("area", id=id, scheme_code="li.gemeinde")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("li.wahlkreis", district))
        p.add("external_id", entity_id=id, scheme="li.regierung.unit", value=str(unit))
        p.name(id, name, "de", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Liechtenstein's Landtag, its electoral system, and the parties that ran in Landtag elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(LIECHTENSTEIN)
    p.add(
        "source",
        id=LI_SOURCE,
        kind="official_body",
        publisher="Information und Kommunikation der Regierung, Stabsstelle Regierungskanzlei",
        title="Landtagswahlen: Ergebnisse",
        url=LANDTAG,
        license="Official publication; reuse terms are not stated",
        license_url=f"{LANDTAG}/datenschutz",
        attribution="Regierung des Fürstentums Liechtenstein, landtagswahlen.li",
    )
    for number_ in LANDTAG_IDS.values():
        p.artifact(raw.cached(SOURCE, parliament_url(number_)), LI_SOURCE)
    p.add("body", id=LANDTAG_BODY, polity_id=LIECHTENSTEIN, kind="unicameral")
    p.name(LANDTAG_BODY, "Landtag", "de", "official")
    p.name(LANDTAG_BODY, "Landtag", "en", "official", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=LIECHTENSTEIN,
        valid_from=date(2001, 1, 1),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=SYSTEM_NOTE,
        lang="en",
    )
    for abbreviation, name in PARTIES.items():
        id = party(abbreviation)
        p.add("party", id=id, kind="party", polity_id=LIECHTENSTEIN)
        p.add("external_id", entity_id=id, scheme="li.regierung.party", value=abbreviation)
        p.name(id, name, "de", "official")
        p.name(id, abbreviation, "de", "abbreviation", False)
        if abbreviation in COLORS:
            p.add(
                "party_color",
                party_id=id,
                color=COLORS[abbreviation].lower(),
                valid_from=None,
                valid_to=None,
                source_id=LI_SOURCE,
            )
    return p


RULES: dict[str, object] = {
    "formula": "hagenbach_bischoff",
    "threshold": {"level": "list", "scope": "national", "percent": 8},
    "lists": {"order": "open", "vacancy": "substitute"},
}
SYSTEM_NOTE = (
    "The 25 members of the Landtag are elected in two districts: Oberland with 15 seats and Unterland with 10. "
    "Each voter has as many votes as the district has seats and may give them to candidates of any list, with at "
    "most one vote per candidate. A list's votes are its candidates' votes plus the additional votes of ballots "
    "that leave votes unused. Only lists with at least 8% of the votes nationwide share in the seats, which each "
    "district allocates by the Hagenbach-Bischoff method. Each list's seats go to its candidates with the most "
    "votes, and the next candidates are elected as deputies (Stellvertreter)."
)


def person_name(candidate: Candidate) -> str:
    return f"{candidate.given} {candidate.surname}".strip()


def election(year: int) -> Package:
    when = day(year)
    p = Package(
        f"{SOURCE}/{when}",
        SOURCE,
        f"The {year} Liechtenstein Landtag election by municipality",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(LIECHTENSTEIN)
    pages = [parliament_url(LANDTAG_IDS[year])]
    for unit in range(1, 15):
        pages += [result_url(LANDTAG_IDS[year], unit), protocol_url(LANDTAG_IDS[year], unit)]
    for url in pages:
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", when)
    p.add(
        "election", id=election_id, polity_id=LIECHTENSTEIN, kind="general", date_from=when, date_to=when, status="held"
    )
    p.name(election_id, f"{year} Liechtenstein parliamentary election", "en", "official")
    p.name(election_id, f"Landtagswahlen {year}", "de", "native", False)
    path = f"li/{when}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    won = seats(year)

    units: dict[int, str] = {}
    for unit, (name, code, _) in PLACES.items():
        id = units[unit] = key("unit", when, code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=LIECHTENSTEIN,
            scheme_code="li.gemeinde",
            code=f"{when}:{code}",
            area_relation="equals",
            area_id=area("li.gemeinde", code),
            kind="standard",
        )

    def accounting(set_id: str, unit: str | None, unit_number: int, declared: bool) -> None:
        found = figures(year, unit_number)
        rows: list[tuple[int, int, int, int]] = []
        for measure, name in ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"), (6, "blank")):
            if name in found:
                rows.append((measure, 0, *found[name]))
        rows.append((14, 0, *found["voters"]))
        for measure, name in ((22, "unmodified"), (23, "modified")):
            if name in found:
                rows.append((measure, 0, *found[name]))
        for measure, channel, value, flags in rows:
            if declared:
                p.add("declared", result_set_id=set_id, option_id=None, measure=measure, value=value)
            else:
                p.add(
                    "result_value",
                    result_set_id=set_id,
                    unit_id=unit,
                    option_id=None,
                    channel=channel,
                    measure=measure,
                    count_round=0,
                    flags=flags,
                    value=value,
                )

    candidates_seen: dict[str, Candidate] = {}
    for unit_number, district in DISTRICT_UNITS.items():
        district_result = result(year, unit_number)
        members = [u for u, (_, _, d) in PLACES.items() if d == district]
        contest = key("contest", when, district)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=LANDTAG_BODY,
            district_area_id=area("li.wahlkreis", district),
            magnitude=SEATS[district],
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Landtag, {district} electoral district", "en", "english")
        p.name(contest, f"Wahlkreis {district}", "de", "official", False)
        p.add("slug", path=f"{path}/{changeset.slug(district)}", entity_id=contest, is_current=True)
        set_id = key("result_set", when, district)
        p.add(
            "result_set",
            id=set_id,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=LI_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = METHOD + (" " + NOTES[year] if year in NOTES else "")
        p.add("note", id=key("note", set_id), entity_id=set_id, kind="methodology", body_md=method, lang="en")

        lists = {a: key("option", when, district, a) for a in district_result.sections}
        for abbreviation, option in lists.items():
            section = district_result.sections[abbreviation]
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(abbreviation),
                ballot_label=section.name,
            )
        elected = Counter()
        options: dict[tuple[str, str], str] = {}
        for abbreviation, section in district_result.sections.items():
            for candidate in section.candidates:
                if candidate.id in candidates_seen:
                    raise ValueError(f"candidate {candidate.id} listed twice in {year}")
                candidates_seen[candidate.id] = candidate
                option = options[(abbreviation, candidate.id)] = key(
                    "option", when, district, "candidate", candidate.id
                )
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=lists[abbreviation],
                    ballot_label=person_name(candidate),
                )
                person = key("person", candidate.id)
                p.add("person", id=person, birth_precision=None)
                p.add("external_id", entity_id=person, scheme="li.regierung.candidate", value=candidate.id)
                p.name(person, person_name(candidate), "de", "official")
                candidacy = key("candidacy", when, candidate.id)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="substitute" if candidate.status == "Stv." else "list_member",
                    ballot_name=f"{candidate.surname} {candidate.given}".strip(),
                    member_of_party_id=party(abbreviation),
                    is_independent=False,
                )
                if candidate.status == "Abg.":
                    elected[abbreviation] += 1
                    p.add("outcome", result_set_id=set_id, option_id=option, kind="elected")
        for abbreviation, count in won[district].items():
            if elected[abbreviation] != count:
                raise ValueError(
                    f"{year} {district} {abbreviation}: {elected[abbreviation]} elected, matrix says {count}"
                )
            if count:
                p.add("outcome", result_set_id=set_id, option_id=lists[abbreviation], kind="won", seats=count)

        # Municipality values.
        for unit in members:
            here = result(year, unit)
            accounting(set_id, units[unit], unit, False)
            for abbreviation, section in here.sections.items():
                option = lists[abbreviation]
                votes = section.party_votes if section.party_votes is not None else here.party_votes[abbreviation]
                if votes != here.party_votes[abbreviation]:
                    raise ValueError(f"{year} unit {unit} {abbreviation}: party votes {votes} against bars")
                values = [(8, votes)]
                if section.additional is not None:
                    values.append((9, section.additional))
                    if section.candidate_votes + section.additional != votes:
                        raise ValueError(f"{year} unit {unit} {abbreviation}: candidate and additional votes differ")
                elif section.candidate_votes > votes:
                    raise ValueError(f"{year} unit {unit} {abbreviation}: candidate votes exceed party votes")
                for measure, kind in ((22, "unmodified"), (23, "modified")):
                    if kind in section.ballots:
                        values.append((measure, section.ballots[kind]))
                for measure, value in values:
                    p.add(
                        "result_value",
                        result_set_id=set_id,
                        unit_id=units[unit],
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
                if sum(c.votes for c in section.candidates) != section.candidate_votes:
                    raise ValueError(f"{year} unit {unit} {abbreviation}: candidate rows do not add up")
                for candidate in section.candidates:
                    p.add(
                        "result_value",
                        result_set_id=set_id,
                        unit_id=units[unit],
                        option_id=options[(abbreviation, candidate.id)],
                        channel=0,
                        measure=10,
                        count_round=0,
                        flags=0,
                        value=candidate.votes,
                    )

        # The authority's own district totals.
        accounting(set_id, None, unit_number, True)
        for abbreviation, section in district_result.sections.items():
            p.add(
                "declared",
                result_set_id=set_id,
                option_id=lists[abbreviation],
                measure=8,
                value=district_result.party_votes[abbreviation],
            )
            for candidate in section.candidates:
                p.add(
                    "declared",
                    result_set_id=set_id,
                    option_id=options[(abbreviation, candidate.id)],
                    measure=10,
                    value=candidate.votes,
                )
    p.accept(
        "accounting.votes_valid",
        "Each voter has as many votes as the district has seats (15 in Oberland, 10 in Unterland), so the votes for "
        "lists never add up to the valid ballots. List votes are stored as list votes (measure 8) and valid ballots as "
        "valid (measure 4).",
    )
    if year == 2001:
        p.accept(
            "accounting.ballots_sum",
            "The 2001 protocol prints Planken with 0 envelopes and 182 blank ballots against 182 voting cards, 181 "
            "valid and 1 invalid, and Schaan with valid, invalid, and blank ballots 6 above its envelopes. The values "
            "are stored as printed.",
        )
        p.accept(
            "accounting.declared",
            "The 2001 Oberland page prints 1,575 votes for one FL candidate where its municipalities add up to 1,470, "
            "and the Unterland page prints five FBP candidates' totals that differ from their municipalities' sums "
            "(the differences net to zero). Municipality values and district totals are stored as printed.",
        )
    if year == 2013:
        p.accept(
            "accounting.ballots_sum",
            "The 2013 protocol omits valid ballots; they are stored as the sum of the printed unchanged and changed "
            "ballots, which exceeds Gamprin's envelopes less its invalid and blank ballots by 1.",
        )
    return p
