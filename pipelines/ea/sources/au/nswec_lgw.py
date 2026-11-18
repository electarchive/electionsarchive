"""NSW local government elections of 2021 and 2024: ward councillor contests and direct mayoral contests.

The commission's results site publishes, for each council, a page per contest: the councillor contest of the whole
council or of each of its wards, and the election of a mayor where the mayor is elected directly. A ward contest has a
report of the first preferences at each voting venue (a page per venue), a report of the totals by group and
candidate, the candidates in sequence of election, and general statistics. A mayoral contest has one report that lists
the first preferences of every candidate at every venue and the formal and informal ballots. The councillor contests
of whole councils are read in `ea.sources.au.nswec`."""

import html
import re
from datetime import date
from typing import NamedTuple

from ea import raw
from ea.package import Package
from ea.sources.au.nswec import (
    AUSTRALIA,
    COUNCIL_SYSTEM,
    COUNTRY,
    LOCAL,
    NO_PARTY,
    NSW,
    NSWEC,
    PAST,
    SOURCE,
    Values,
    area,
    key,
    local_party_code,
    unit_kind,
)
from ea.sources.au.nswec_lg2008 import number, slug
from ea.sources.au.nswec_lge import (
    Entry,
    Reported,
    Venue,
    cells,
    html_venue,
    parse_rows,
    party_name,
    person_name,
    read,
    sort_key,
    split_ballot,
    split_declared,
    text_of,
)

CODES = {2021: "LG2101", 2024: "LG2401"}
TAG = {2021: "lg2021", 2024: "lg2024"}
TACIT_NOTE = (
    "The candidates nominated did not outnumber the vacancies, so each was declared elected unopposed and no votes "
    "were cast."
)
WARD_METHOD = (
    "First preference votes for the councillor election of one ward of a council, from the NSW Electoral "
    "Commission's results pages for the %s New South Wales local government elections. The commission publishes the "
    "votes at each polling place, pre-poll venue, and declaration vote type, and the figures are stored for each of "
    "them. A voter marks a group above the line or candidates below the line: each group's above-the-line votes are "
    "stored on the group's list option and each candidate's first preferences on the candidate's option, so the two "
    "together add up to the formal votes. Where the contest has no groups, only candidates are counted. The "
    "commission's candidates in sequence of election are stored as outcomes; the distribution of preferences is not "
    "stored here. The set is the commission's final count."
)
MAYOR_METHOD = (
    "First preference votes for the election of the mayor of one council, from the NSW Electoral Commission's results "
    "pages for the %s New South Wales local government elections. The commission publishes each candidate's votes at "
    "each polling place, pre-poll venue, and declaration vote type, and the figures are stored for each of them. The "
    "candidate the commission declares elected is stored as an outcome; the distribution of preferences is not "
    "stored here. The set is the commission's final count."
)
MAYOR_SYSTEM = key("electoral_system", "mayor", "opv")
MAYOR_RULES = (
    "A mayor elected by the voters of the whole council is chosen by optional preferential voting: voters number as "
    "many boxes as they choose, and the candidate with the fewest votes is excluded and their ballots passed on "
    "until one candidate holds a majority of the ballots still in the count."
)


def base(year: int) -> str:
    return f"{PAST}/{CODES[year]}"


class Ward(NamedTuple):
    year: int
    council: str  # the council's slug
    council_name: str
    body_name: str
    ward: str  # the ward's slug
    ward_name: str
    vacancies: int
    elected: list[str]
    entries: list[Entry]
    venues: list[Venue]
    reported: Reported | None  # None when the contest was uncontested
    urls: list[str]


class MayorVenue(NamedTuple):
    name: str
    votes: list[int]
    formal: int
    informal: int


class Mayor(NamedTuple):
    year: int
    council: str
    council_name: str
    body_name: str
    candidates: list[tuple[str, str]]  # the ballot name and the party as printed
    declared: list[int]
    formal: int
    informal: int
    venues: list[MayorVenue]
    elected: str
    urls: list[str]


# Fetching --------------------------------------------------------------------------------------------------------


def council_slugs(year: int) -> list[str]:
    page = read(f"{base(year)}/index")
    return sorted(set(re.findall(rf'href="/{CODES[year]}/([^/"]+)/results"', page)))


def contest_links(year: int, council: str) -> list[str]:
    """The addresses of a council's contest pages, relative to its folder."""
    page = read(f"{base(year)}/{council}/results")
    found = set(re.findall(rf'href="/{CODES[year]}/{re.escape(council)}/([^"]+)"', page))
    return sorted(found - {"results"})


WARD_REPORTS = ("general-statistics", "candidates-in-sequence", "list", "fp-by-grp-and-candidate-by-vote-type")


def venue_links(folder: str) -> list[tuple[str, str]]:
    page = read(f"{folder}/list")
    found = []
    for m in re.finditer(r'(?s)<a[^>]*href="\./(pp-[^"/]+)"[^>]*>(.*?)</a>', page):
        found.append((f"{folder}/{html.unescape(m[1])}", text_of(m[2])))
    return found


def fetch(year: int | None = None) -> None:
    if year is None:
        for y in CODES:
            fetch(y)
        return
    raw.fetch(SOURCE, f"{base(year)}/index", suffix=".htm")
    slugs = council_slugs(year)
    if len(raw.fetch_many(SOURCE, [f"{base(year)}/{s}/results" for s in slugs], suffix=".htm", workers=6)) != len(
        slugs
    ):
        raise RuntimeError("a council page could not be fetched")
    wards = [
        f"{base(year)}/{c}/{link}" for c in slugs for link in contest_links(year, c) if link.endswith("/councillor")
    ]
    mayors = [f"{base(year)}/{c}/mayoral" for c in slugs if "mayoral" in contest_links(year, c)]
    raw.fetch_many(SOURCE, [*wards, *mayors], suffix=".htm", workers=6)
    pages = [f"{w}/{r}" for w in wards for r in WARD_REPORTS]
    got = raw.fetch_many(SOURCE, pages, suffix=".htm", workers=6)
    venues = sorted({u for w in wards if f"{w}/list" in got for u, _ in venue_links(w)})
    if len(raw.fetch_many(SOURCE, venues, suffix=".htm", workers=6)) != len(venues):
        raise RuntimeError("a venue page could not be fetched")
    raw.fetch_many(SOURCE, [f"{m}/mayoral-fp-by-candidate" for m in mayors], suffix=".htm", workers=6)


# Reading ---------------------------------------------------------------------------------------------------------


def heading(page: str) -> tuple[str, str, str]:
    """The council's name, its full name, and the contest's ward as the contest report's comment gives them."""
    council = re.search(r"<h1>([^<]+)</h1>", page)
    meta = re.search(r'Area: "([^"]*)", Name: "([^"]*)", Ward: "([^"]*)"', page)
    if council is None:
        raise ValueError("no contest heading")
    if meta is None:  # an uncontested contest's page prints "Council - Ward" and no comment
        name, _, ward = text_of(council.group(1)).partition(" - ")
        if not ward:
            raise ValueError("no contest heading")
        return name, f"{name} Council", ward
    return text_of(council.group(1)), text_of(meta.group(2)), text_of(meta.group(3))


def statistic(page: str, label: str) -> int:
    if label.endswith(":"):  # a note above the tables
        match = re.search(rf"{re.escape(label)}\s*</strong>\s*([\d,]+)", page)
        if match is not None:
            return number(match.group(1))
    for row in cells(page):
        if len(row) == 2 and row[0] == label and re.fullmatch(r"[\d,]+", row[1]):
            return number(row[1])
    raise ValueError(f"no {label}")


def sequence(page: str) -> list[str]:
    rows = cells(page)
    return [re.sub(r"\s*\*$", "", r[0]).strip() for r in rows[1:] if r and r[0]]


def ward_contest(year: int, council: str, ward: str) -> Ward | None:
    folder = f"{base(year)}/{council}/{ward}/councillor"
    got = raw.keys(SOURCE)
    if f"{folder}/list" not in got:
        if folder not in got:
            return None
        page = read(folder)
        if "was UNCONTESTED" not in page:
            return None
        name, body, ward_name = heading(page)
        names = [text_of(m) for m in re.findall(r'<span class="candidate-name">(.*?)</span>', page)]
        entries = [Entry("", "candidate", n) for n in names]
        return Ward(year, council, name, body, ward, ward_name, len(names), names, entries, [], None, [folder])
    urls = [f"{folder}/{r}" for r in WARD_REPORTS]
    stats = read(urls[0])
    name, body, ward_name = heading(stats)
    vacancies = statistic(stats, "Candidates to be Elected:")
    elected = sequence(read(urls[1]))
    venues: list[Venue] = []
    for url, venue in venue_links(folder):
        urls.append(url)
        venues.append(Venue(venue, html_venue(url)))
    if not venues:
        raise ValueError(f"{folder}: no venues")
    entries = venues[0].count.entries
    for v in venues:
        if sort_key(v.count.entries) != sort_key(entries):
            raise ValueError(f"{folder}: {v.name} lists different candidates")
    rows, figures, _ = parse_rows(cells(read(urls[3])))
    if sort_key(rows) != sort_key(entries):
        raise ValueError(f"{folder}: the totals report lists different candidates")
    if any(f[0] + f[1] != f[2] for f in figures):
        raise ValueError(f"{folder}: ordinary and declaration votes do not add up")
    formal, informal = statistic(stats, "Total Formal Votes"), statistic(stats, "Total Informal Ballot Papers")
    reported = Reported(rows, [f[2] for f in figures], formal, informal)
    return Ward(year, council, name, body, ward, ward_name, vacancies, elected, entries, venues, reported, urls)


def ward_contests(year: int) -> list[Ward]:
    found = []
    for c in council_slugs(year):
        for link in contest_links(year, c):
            if link.endswith("/councillor"):
                w = ward_contest(year, c, link.split("/")[0])
                if w is not None:
                    found.append(w)
    return found


def mayor_contest(year: int, council: str) -> Mayor | None:
    main = f"{base(year)}/{council}/mayoral"
    report = f"{main}/mayoral-fp-by-candidate"
    if report not in raw.keys(SOURCE):
        return None
    page = read(report)
    name, body, _ = heading(page)
    tables = re.findall(r"(?s)<table>(.*?)</table>", page)
    rows = [
        [text_of(c) for c in re.findall(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        for tr in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", tables[0])
    ]
    candidates = [(r[0], r[1]) for r in rows[1:] if len(r) >= 4 and r[0] and not r[0].startswith("TOTAL")]
    declared = [number(r[2]) for r in rows[1:] if len(r) >= 4 and r[0] and not r[0].startswith("TOTAL")]
    total = next(number(r[2]) for r in rows if r and r[0].startswith("TOTAL FORMAL"))
    table = [
        [text_of(c) for c in re.findall(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        for tr in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", tables[1])
    ]
    n = len(candidates)
    venues: list[MayorVenue] = []
    grand: list[int] | None = None
    for r in table[1:]:
        if len(r) != n + 4 or not re.fullmatch(r"[\d,]+", r[1]):
            continue
        figures = [number(x) for x in r[1:]]
        if r[0] == "Total Votes / Ballot Papers":
            grand = figures
        elif not r[0].startswith(("Total", "%")):
            venues.append(MayorVenue(r[0], figures[:n], figures[n], figures[n + 1]))
    if grand is None or not venues:
        raise ValueError(f"{report}: no venues or totals")
    names = [v.name for v in venues]
    if len(set(names)) != len(names):
        raise ValueError(f"{report}: repeated venue names")
    if grand[:n] != declared or grand[n] != total:
        raise ValueError(f"{report}: the venue table and the summary disagree")
    winner = re.search(r'class="candidate-name">([^<]+)</span>', read(main))
    if winner is None:
        raise ValueError(f"{main}: no declared winner")
    return Mayor(
        year,
        council,
        name,
        body,
        candidates,
        declared,
        total,
        grand[n + 1],
        venues,
        text_of(winner.group(1)),
        [main, report],
    )


def mayor_contests(year: int) -> list[Mayor]:
    found = []
    for c in council_slugs(year):
        m = mayor_contest(year, c)
        if m is not None:
            found.append(m)
    return found


def words(name: str) -> list[str]:
    return sorted(w.upper() for w in re.sub(r"[^A-Za-z' -]", "", name).split())


def same_person(ballot: str, declared: str) -> bool:
    return words(ballot) == words(declared)


# Packages --------------------------------------------------------------------------------------------------------


def council_area(slug_: str) -> str:
    return area("au-nsw.council", slug_)


def ward_area(year: int, council: str, ward: str) -> str:
    return area("au-nsw.ward", f"{year}:{council}:{ward}")


def party_codes(wards: list[Ward], mayors: list[Mayor]) -> dict[str, str]:
    found: dict[str, str] = {}
    for w in wards:
        for e in w.entries:
            if e.kind == "group" and e.label:
                found.setdefault(local_party_code(e.label), party_name(e.label))
    for m in mayors:
        for _, party in m.candidates:
            if party not in NO_PARTY:
                found.setdefault(local_party_code(party), party_name(party))
    return found


def mayor_office(council: str) -> str:
    return key("office", "mayor", council)


def all_councils(wards: dict[int, list[Ward]], mayors: dict[int, list[Mayor]]) -> dict[str, tuple[str, str]]:
    """The slug of each council with a ward or mayoral contest, with its name and its body's name."""
    found = {w.council: (w.council_name, w.body_name) for items in wards.values() for w in items}
    found |= {m.council: (m.council_name, m.body_name) for items in mayors.values() for m in items}
    return found


def missing_councils(found: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """The councils the whole-council packages do not hold, because every contest of theirs is by ward or for mayor."""
    from ea.sources.au import nswec

    known = nswec.councils(sorted(LOCAL))
    return {slug_: names for slug_, names in found.items() if slug_ not in known}


def geo_councils(wards: dict[int, list[Ward]], mayors: dict[int, list[Mayor]]) -> Package:
    found = missing_councils(all_councils(wards, mayors))
    p = Package(
        f"{SOURCE}/geo-lgw",
        SOURCE,
        f"The {len(found)} councils whose 2021 and 2024 local government elections are by ward or for mayor only, as "
        "the NSW Electoral Commission names them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    for year in CODES:
        p.artifact(raw.cached(SOURCE, f"{base(year)}/index"), NSWEC)
    for slug_, (name, _) in sorted(found.items()):
        id = council_area(slug_)
        p.add("area", id=id, scheme_code="au-nsw.council")
        p.add("area_code", area_id=id, code=slug_)
        p.add("area_parent", area_id=id, parent_id=NSW)
        p.name(id, name, "en", "official")
    return p


def geo(year: int, wards: list[Ward]) -> Package:
    day = LOCAL[year].day
    councils = {w.council: w.council_name for w in wards}
    p = Package(
        f"{SOURCE}/geo-{TAG[year]}",
        SOURCE,
        f"The {len(wards)} wards of the {day:%-d %B %Y} New South Wales local government elections, as the NSW "
        "Electoral Commission names them",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/geo-lgw", f"{SOURCE}/geo-lg2008"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    p.add(
        "boundary_set",
        id=key("boundary_set", "au-nsw.ward", year),
        scheme_code="au-nsw.ward",
        vintage=str(year),
        as_of=day,
    )
    for w in sorted(wards, key=lambda w: (w.council, w.ward)):
        id = ward_area(year, w.council, w.ward)
        p.add("area", id=id, scheme_code="au-nsw.ward")
        p.add("area_code", area_id=id, code=f"{year}:{w.council}:{w.ward}")
        p.add("area_parent", area_id=id, parent_id=council_area(w.council))
        p.name(id, f"{councils[w.council]}, {w.ward_name}", "en", "official")
    return p


def new_parties(found: dict[str, str]) -> dict[str, str]:
    """The groups and parties the commission's whole-council contests did not already name."""
    from ea.sources.au import nswec

    known = nswec.local_parties(sorted(LOCAL))
    return {code: name for code, name in found.items() if code not in known}


def registry(wards: dict[int, list[Ward]], mayors: dict[int, list[Mayor]]) -> Package:
    councils = all_councils(wards, mayors)
    offices = {m.council: m.council_name for items in mayors.values() for m in items}
    found = new_parties(party_codes([w for i in wards.values() for w in i], [m for i in mayors.values() for m in i]))
    p = Package(
        f"{SOURCE}/registry-lgw",
        SOURCE,
        f"The mayors' offices and {len(found)} further parties and groups on the NSW Electoral Commission's results "
        "for the 2021 and 2024 New South Wales local government elections",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/geo-lgw"],
    )
    p.external |= {AUSTRALIA}
    for council, name in sorted(offices.items()):
        id = mayor_office(council)
        p.add("office", id=id, polity_id=AUSTRALIA, kind="mayor")
        p.name(id, f"Mayor of {name}", "en", "official")
    for council, (_, body) in sorted(missing_councils(councils).items()):
        id = key("body", "council", council)
        p.add("body", id=id, polity_id=AUSTRALIA, kind="council")
        p.name(id, body, "en", "official")
    p.add(
        "electoral_system",
        id=MAYOR_SYSTEM,
        polity_id=AUSTRALIA,
        valid_from=date(1993, 1, 1),
        valid_to=None,
        rules={"formula": "irv"},
        rules_version=1,
    )
    p.add(
        "note", id=key("note", MAYOR_SYSTEM), entity_id=MAYOR_SYSTEM, kind="methodology", body_md=MAYOR_RULES, lang="en"
    )
    for code, name in sorted(found.items()):
        id = key("party", "local", code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    return p


def add_contests(p: Package, year: int, election_id: str, path: str, values: Values) -> None:
    """Add the ward and mayoral contests of a year to its election package."""
    day = LOCAL[year].day
    for w in sorted(ward_contests(year), key=lambda w: (w.council, w.ward)):
        for url in w.urls:
            p.artifact(raw.cached(SOURCE, url), NSWEC)
        code = f"{w.council}:{w.ward}"
        label = f"{w.council_name}, {w.ward_name}"
        tacit = w.reported is None
        contest = key("contest", day, "ward", code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=key("body", "council", w.council),
            district_area_id=ward_area(year, w.council, w.ward),
            magnitude=w.vacancies,
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="tacit" if tacit else "counted",
        )
        p.name(contest, f"{label} councillors", "en", "official")
        p.add("slug", path=f"{path}/council-{w.council}/ward-{slug(w.ward_name)}", entity_id=contest, is_current=True)
        group_option: dict[str, str] = {}
        group_party: dict[str, str | None] = {}
        for e in w.entries:
            if e.kind == "group":
                group_party[e.group] = key("party", "local", local_party_code(e.label)) if e.label else None
        options: list[str] = []
        labels = [e.label for e in w.entries if e.kind == "candidate"]
        wanted = {e: [i for i, x in enumerate(labels) if x == e] for e in w.elected}
        if any(len(v) != 1 for v in wanted.values()):
            raise ValueError(f"{label}: an elected candidate does not match one candidate")
        elected: set[str] = set()
        for i, e in enumerate(w.entries, 1):
            if e.kind == "group":
                id = group_option[e.group] = key("option", day, "ward", code, "group", e.group)
                p.add(
                    "contest_option",
                    id=id,
                    contest_id=contest,
                    kind="list",
                    party_id=group_party[e.group],
                    ballot_position=i,
                    ballot_label=f"Group {e.group}",
                )
                options.append(id)
                continue
            id = key("option", day, "ward", code, "candidate", i, e.label)
            pid = group_party.get(e.group) if e.group else None
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                parent_option_id=group_option.get(e.group) if e.group else None,
                party_id=pid,
                ballot_position=i,
                ballot_label=e.label,
            )
            who = key("person", day, "ward", code, i, e.label)
            surname, given = split_declared(e.label) if tacit else split_ballot(e.label)
            p.add("person", id=who)
            p.name(who, person_name(surname, given), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, "ward", code, i, e.label),
                person_id=who,
                option_id=id,
                role="head",
                ballot_name=e.label,
                member_of_party_id=pid,
                is_independent=pid is None,
            )
            if e.label in wanted:
                elected.add(id)
            options.append(id)
        if len(elected) != len(w.elected):
            raise ValueError(f"{label}: elected candidates not found")
        result_set = key("result_set", contest, "official")
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="tacit" if tacit else "counted",
            source_id=NSWEC,
            status="active",
            is_default=True,
            completeness=None if tacit else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=TACIT_NOTE if tacit else WARD_METHOD % f"{day:%-d %B %Y}",
            lang="en",
        )
        for id in sorted(elected):
            p.add("outcome", result_set_id=result_set, option_id=id, kind="elected")
        if w.reported is None:
            continue
        for option, votes in zip(options, w.reported.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=w.reported.formal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=w.reported.informal)
        for v in w.venues:
            kind = unit_kind("", v.name)
            id = key("unit", day, "ward", code, v.name)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=AUSTRALIA,
                scheme_code="au-nsw.council_venue",
                code=f"{day}:{code}:{v.name}",
                area_relation="within",
                area_id=ward_area(year, w.council, w.ward),
                kind=kind,
            )
            p.name(id, v.name if kind == "standard" else f"{label}: {v.name}", "en", "official")
            for option, votes in zip(options, v.count.votes, strict=True):
                values.add(result_set, id, option, 1, votes)
            values.add(result_set, id, None, 4, v.count.formal)
            values.add(result_set, id, None, 5, v.count.informal)
            values.add(result_set, id, None, 3, v.count.formal + v.count.informal)
    for m in sorted(mayor_contests(year), key=lambda m: m.council):
        for url in m.urls:
            p.artifact(raw.cached(SOURCE, url), NSWEC)
        contest = key("contest", day, "mayor", m.council)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=mayor_office(m.council),
            district_area_id=council_area(m.council),
            round_number=1,
            electoral_system_id=MAYOR_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{m.council_name} mayor", "en", "official")
        p.add("slug", path=f"{path}/council-{m.council}/mayor", entity_id=contest, is_current=True)
        result_set = key("result_set", contest, "official")
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NSWEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=MAYOR_METHOD % f"{day:%-d %B %Y}",
            lang="en",
        )
        options: list[str] = []
        winners = 0
        for i, (name, party) in enumerate(m.candidates, 1):
            id = key("option", day, "mayor", m.council, i, name)
            pid = None if party in NO_PARTY else key("party", "local", local_party_code(party))
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                party_id=pid,
                ballot_position=i,
                ballot_label=name,
            )
            who = key("person", day, "mayor", m.council, i, name)
            surname, given = split_ballot(name)
            p.add("person", id=who)
            p.name(who, person_name(surname, given), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, "mayor", m.council, i, name),
                person_id=who,
                option_id=id,
                role="head",
                ballot_name=name,
                member_of_party_id=pid,
                is_independent=pid is None,
            )
            if same_person(name, m.elected):
                p.add("outcome", result_set_id=result_set, option_id=id, kind="elected")
                winners += 1
            options.append(id)
        if winners != 1:
            raise ValueError(f"{m.council_name}: {m.elected} matches {winners} candidates")
        for option, votes in zip(options, m.declared, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=m.formal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=m.informal)
        for v in m.venues:
            kind = unit_kind("", v.name)
            id = key("unit", day, "mayor", m.council, v.name)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=AUSTRALIA,
                scheme_code="au-nsw.council_venue",
                code=f"{day}:{m.council}:mayoral:{v.name}",
                area_relation="within",
                area_id=council_area(m.council),
                kind=kind,
            )
            p.name(id, v.name if kind == "standard" else f"{m.council_name}: {v.name}", "en", "official")
            for option, votes in zip(options, v.votes, strict=True):
                values.add(result_set, id, option, 1, votes)
            values.add(result_set, id, None, 4, v.formal)
            values.add(result_set, id, None, 5, v.informal)
            values.add(result_set, id, None, 3, v.formal + v.informal)
