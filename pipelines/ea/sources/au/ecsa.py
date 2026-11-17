"""South Australian elections from the Electoral Commission SA (ECSA) and its published history of elections."""

import csv
import functools
import html
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.ecsa"
HOST = "https://www.ecsa.sa.gov.au"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
LICENSE = "Creative Commons Attribution 3.0 Australia"
LICENSE_URL = "https://creativecommons.org/licenses/by/3.0/au/"
PAGE = f"{HOST}/elections/past-state-election-results"

PARTIES = {
    "ALP": "Australian Labor Party",
    "LIB": "Liberal Party of Australia",
    "GRN": "The Greens",
    "NAT": "National Party of Australia",
    "ONP": "Pauline Hanson's One Nation",
    "FFP": "Family First Party",
    "AFP": "Australian Family Party",
    "AJP": "Animal Justice Party",
    "RCH": "Responsible Capitalist Party",
    "SAB": "SA Best",
    "LDP": "Liberal Democratic Party",
    "DEM": "Australian Democrats",
    "DIG": "Danig",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECSA = key("source", "ecsa")
HISTORY = key("source", "history")
ASSEMBLY = key("body", "house-of-assembly")
SYSTEM = key("electoral_system", "house-of-assembly", "irv")


def party_id(tag: str) -> str:
    return key("party", tag)


def independent(tag: str) -> bool:
    return tag in ("", "IND")


@dataclass
class Candidate:
    name: str
    party: str
    incumbent: bool = False


@dataclass
class Place:
    """One leaf of a district's count: a polling place, a declaration-vote aggregate, or the district itself."""

    name: str
    votes: list[int | None]
    formal: int | None = None
    informal: int | None = None
    total: int | None = None
    kind: str = "standard"
    runoff: list[int | None] | None = None  # votes for the two final candidates, in `District.finalists` order


@dataclass
class District:
    name: str
    candidates: list[Candidate]
    enrolled: int | None
    places: list[Place]
    declared: Place
    finalists: tuple[int, int] | None = None
    note: str | None = None


@dataclass
class Event:
    day: str  # election day, yyyy-mm-dd
    kind: str  # general or by_election
    files: list[str]  # raw urls this election cites
    districts: list[District] = field(default_factory=list)


def title(name: str) -> str:
    return " ".join(w if w.isupper() and len(w) <= 2 else w.capitalize() for w in name.split())


def person_name(raw_name: str) -> str:
    """'SANDERSON, Rachel' or 'SANDERSON Rachel' to 'Rachel Sanderson'."""
    if "," in raw_name:
        surname, _, given = raw_name.partition(",")
        return f"{given.strip()} {surname.strip().title()}".strip()
    words = raw_name.split()
    split = next((i for i, w in enumerate(words) if not w.replace("'", "").replace("-", "").isupper()), len(words))
    split = max(split, 1)
    surname = " ".join(w.title() if w.isupper() else w for w in words[:split])
    return f"{' '.join(words[split:])} {surname}".strip()


def number(text: str) -> int | None:
    text = text.replace(",", "").replace("\xa0", "").replace(" ", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


# 2022 state election and 2022 by-election: comma-separated files by district and polling place.

ELECTION_2022 = (
    f"{HOST}/component/edocman/244-2022se-house-of-assembly-first-preference-results-by-district-and-polling-place"
    "/download?Itemid=0"
)
TCP_2022 = (
    f"{HOST}/component/edocman/244-2022se-house-of-assembly-2cp-results-by-district-and-polling-place/download?Itemid=0"
)


def zip_files(url: str) -> dict[str, str]:
    """District name to the text of its file, for a zip of one comma-separated file per district."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as z:
        for info in z.infolist():
            if info.filename.endswith(".csv"):
                out[info.filename.rsplit("/", 1)[1].removesuffix(".csv")] = z.read(info).decode("utf-8-sig")
    return dict(sorted(out.items()))


CANDIDATE = re.compile(r"^(?P<name>.*?)(?: \((?P<party>[^()]*)\))?$")


def candidate(text: str) -> Candidate:
    found = CANDIDATE.match(text.strip())
    assert found
    return Candidate(found["name"].strip(), (found["party"] or "").strip())


def csv_number(cell: str) -> int | None:
    cell = cell.strip().replace(",", "")
    return int(cell) if re.fullmatch(r"\d+", cell) else None


def parse_2022(name: str, first: str, runoff: str | None) -> District:
    rows = list(csv.reader(io.StringIO(first)))
    enrolled = next(csv_number(r[3]) for r in rows if len(r) > 3 and r[1].strip() == "No. enrolled")
    head = next(i for i, r in enumerate(rows) if r and r[0].strip() in ("Polling Place", "Polling Location"))
    header = rows[head - 1]
    stop = next(i for i, c in enumerate(header) if c.strip() == "Formal Votes")
    candidates = [candidate(c) for c in header[1:stop] if c.strip()]
    width = len(candidates)
    places: list[Place] = []
    declared: Place | None = None
    for r in rows[head + 1 :]:
        if not r or not r[0].strip():
            continue
        cells = r[1:]
        votes = [csv_number(cells[2 * i]) for i in range(width)]
        base = 2 * width
        place = Place(
            r[0].strip(), votes, csv_number(cells[base]), csv_number(cells[base + 2]), csv_number(cells[base + 4])
        )
        label = place.name
        if label == "District Total":
            declared = place
        elif label in ("Ordinary Votes", "Polling Place Totals"):
            continue
        elif label in ("Declaration Votes", "Declaration Ballot Papers"):
            place.name = "Declaration Votes"
            place.kind = "aggregate"
            places.append(place)
        else:
            places.append(place)
    assert declared is not None
    district = District(name, candidates, enrolled, places, declared)
    if runoff is not None:
        _runoff(district, runoff)
    return district


def _runoff(district: District, text: str) -> None:
    rows = list(csv.reader(io.StringIO(text)))
    head = next(i for i, r in enumerate(rows) if r and r[0].strip() == "Polling Location")
    first, second = candidate(rows[head][1]), candidate(rows[head][3])
    names = [c.name for c in district.candidates]
    finalists = (names.index(first.name), names.index(second.name))
    district.finalists = finalists
    by_name = {p.name: p for p in district.places}
    for r in rows[head + 1 :]:
        if not r or not r[0].strip():
            continue
        label = r[0].strip()
        pair: list[int | None] = [csv_number(r[1]), csv_number(r[3])]
        if label == "Declaration Ballot Papers":
            label = "Declaration Votes"
        if label in by_name:
            by_name[label].runoff = pair
        elif label not in ("Polling Place Totals", "District Total"):
            raise ValueError(f"{district.name}: 2CP place {label!r} is not in the first preference file")
    district.declared.runoff = next(
        [csv_number(r[1]), csv_number(r[3])] for r in rows[head + 1 :] if r and r[0].strip() == "District Total"
    )


def event_2022() -> Event:
    first, runoff = zip_files(ELECTION_2022), zip_files(TCP_2022)
    event = Event("2022-03-19", "general", [ELECTION_2022, TCP_2022])
    for name, text in first.items():
        event.districts.append(parse_2022(name, text, runoff.get(name)))
    return event


# 2018 state election: one page per district and a second page per district for its polling places.

INDEX_2018 = f"{HOST}/elections/past-state-election-results?view=article&id=124:2018-state-election-results-breakdown&catid=12:elections"
NOT_DISTRICTS = {"House_of_Assembly", "Legislative_Council"}


def district_pages(index: str) -> list[str]:
    found = set(re.findall(r"/html/results/2018/([A-Za-z_]+)2?\.html", index)) - NOT_DISTRICTS
    return sorted(found)


def cells(row: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
    ]


def tables(text: str) -> list[list[list[str]]]:
    out = []
    for table in re.findall(r"<table class=\"table table-striped\">(.*?)</table>", text, re.S):
        out.append([cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)])
    return out


def html_candidate(cell: str) -> Candidate:
    parts = cell.split()
    return Candidate(" ".join(parts[:-1]), parts[-1])


def parse_html(name: str, main: str, booths: str) -> District:
    enrolled = number(re.search(r'electorsEnrolledNumber">([\d,]+)<', main).group(1))  # type: ignore[union-attr]
    first, runoff = tables(booths)
    head = first[0]
    stop = next(i for i, c in enumerate(head) if c.startswith("Frml") or c.startswith("Formal"))
    candidates = [html_candidate(c) for c in head[1:stop]]
    width = len(candidates)
    places: list[Place] = []
    declared: Place | None = None
    for r in first[1:]:
        label = r[0]
        if not label or len(r) < width + 5 or label.startswith("District Total %"):
            continue
        place = Place(
            label,
            [number(c) for c in r[1 : 1 + width]],
            number(r[1 + width]),
            number(r[2 + width]),
            number(r[4 + width]),
        )
        if label == "District Total":
            declared = place
        elif label == "Polling Place Totals":
            continue
        elif label.startswith("Declaration"):
            place.name = "Declaration Votes"
            place.kind = "aggregate"
            places.append(place)
        else:
            places.append(place)
    assert declared is not None
    district = District(name, candidates, enrolled, places, declared)
    rhead = runoff[0]
    pair = [html_candidate(c.removesuffix("Number")) for c in rhead[1:] if c.endswith("Number")]
    names = [c.name for c in candidates]
    district.finalists = (names.index(pair[0].name), names.index(pair[1].name))
    by_name = {p.name: p for p in places}
    for r in runoff[1:]:
        label = r[0]
        if len(r) < 5 or not label:
            continue
        counts = [number(r[1]), number(r[3])]
        label = "Declaration Votes" if label.startswith("Declaration") else label
        if label == "District Total":
            declared.runoff = counts
        elif label in by_name:
            by_name[label].runoff = counts
    return district


def event_2018() -> Event:
    index = raw.cached(SOURCE, INDEX_2018).path.read_text()
    pages = district_pages(index)
    event = Event("2018-03-17", "general", [INDEX_2018])
    for page in pages:
        main, booths = (f"{HOST}/html/results/2018/{page}{n}.html" for n in ("", "2"))
        event.files += [main, booths]
        event.districts.append(
            parse_html(
                page.replace("_", " "),
                raw.cached(SOURCE, main).path.read_text(),
                raw.cached(SOURCE, booths).path.read_text(),
            )
        )
    return event


EVENTS = {"2018-03-17": event_2018, "2022-03-19": event_2022}


def packages() -> list[str]:
    return ["au.ecsa/registry", *(f"au.ecsa/{day}" for day in EVENTS)]


def fetch() -> None:
    for url in (ELECTION_2022, TCP_2022):
        raw.fetch(SOURCE, url, suffix=".zip")
    index = raw.fetch(SOURCE, INDEX_2018, suffix=".html")
    for page in district_pages(index.path.read_text()):
        for n in ("", "2"):
            raw.fetch(SOURCE, f"{HOST}/html/results/2018/{page}{n}.html", suffix=".html")


def parse() -> None:
    registry().write()
    for day in EVENTS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def event(day: str) -> Event:
    return EVENTS[day]()


METHOD = (
    "Counted by the Electoral Commission of South Australia and published by polling place. Each district elects one "
    "member of the House of Assembly by full preferential voting. First preference votes are stored for each "
    "candidate. The commission distributes preferences to the final two candidates, and their votes after that "
    "distribution are given in a note on each district's result set, because the archive stores a count only at the first preference stage. Votes cast away from a polling place on the day are published as one "
    "line of declaration votes. Informal ballot papers are those that do not count, and valid votes are the formal "
    "ballot papers. Official outcomes are stored as declared."
)


def registry() -> Package:
    p = Package(
        "au.ecsa/registry",
        SOURCE,
        "South Australian parties, the House of Assembly, and its electoral system, from the Electoral Commission SA",
    )
    p.external.add(AUSTRALIA)
    p.add(
        "source",
        id=ECSA,
        kind="official_body",
        publisher="Electoral Commission SA",
        title="South Australian election results published by the Electoral Commission SA",
        url=PAGE,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Electoral Commission SA",
    )
    for day in EVENTS:
        for url in event(day).files:
            p.artifact(raw.cached(SOURCE, url), ECSA)
    p.add("scheme", code="au-sa.district", polity_id=AUSTRALIA, kind="electoral_district", rank=1, noun="district")
    p.add("scheme", code="au-sa.place", polity_id=AUSTRALIA, kind="precinct", rank=2, noun="polling place")
    p.add("body", id=ASSEMBLY, polity_id=AUSTRALIA, kind="lower_house")
    p.name(ASSEMBLY, "South Australian House of Assembly", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=AUSTRALIA, rules={"formula": "irv"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each of the 47 districts elects one member by full preferential voting: voters number every box, "
        "and the candidate with the fewest votes is excluded and their ballots passed on until one candidate holds "
        "a majority.",
        lang="en",
    )
    tags = {c.party for day in EVENTS for d in event(day).districts for c in d.candidates if not independent(c.party)}
    for tag in sorted(tags):
        p.add("party", id=party_id(tag), kind="party", polity_id=AUSTRALIA)
        if tag in PARTIES:
            p.name(party_id(tag), PARTIES[tag], "en", "official")
            p.name(party_id(tag), tag, "en", "abbreviation", False)
        else:
            p.name(party_id(tag), tag, "en", "abbreviation")
    return p


def _put(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None, count_round: int = 0
) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=count_round,
        flags=0,
        value=value,
    )


def build(day: str) -> Package:
    ev = event(day)
    first = date.fromisoformat(day)
    general = ev.kind == "general"
    p = Package(
        f"au.ecsa/{day}",
        SOURCE,
        f"The South Australian {'state election' if general else 'by-election'} of {first} for the House of Assembly",
        depends=["au.ecsa/registry"],
    )
    p.external.add(AUSTRALIA)
    for url in ev.files:
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    path = f"au-sa/{day}-{'state' if general else 'by-election'}"
    if general:
        p.add(
            "election", id=election, polity_id=AUSTRALIA, kind="general", date_from=first, date_to=first, status="held"
        )
        p.add("slug", path=path, entity_id=election, is_current=True)
        p.name(election, f"{first.year} South Australian state election", "en", "official")
    mismatches: Counter[str] = Counter()
    for d in ev.districts:
        name = title(d.name) if d.name.isupper() else d.name
        slug = changeset.slug(name)
        if not general:
            election = key("election", day, slug)
            path = f"au-sa/{day}-by-election-{slug}"
            p.add(
                "election",
                id=election,
                polity_id=AUSTRALIA,
                kind="by_election",
                date_from=first,
                date_to=first,
                status="held",
            )
            p.add("slug", path=path, entity_id=election, is_current=True)
            p.name(election, f"{first.year} {name} by-election", "en", "official")
        contest = key("contest", day, slug)
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
        p.name(contest, f"{name}, House of Assembly", "en", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECSA,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        if d.note:
            p.add(
                "note",
                id=key("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=d.note,
                lang="en",
            )
        options: list[str] = []
        for position, c in enumerate(d.candidates, 1):
            person = key("person", day, slug, position)
            p.add("person", id=person)
            p.name(person, person_name(c.name), "en", "official")
            option = key("option", day, slug, position)
            options.append(option)
            party = None if independent(c.party) else party_id(c.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=c.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, slug, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=party,
                is_independent=independent(c.party),
                is_incumbent=c.incumbent,
            )
        district_unit = key("unit", day, slug)
        p.add(
            "reporting_unit",
            id=district_unit,
            polity_id=AUSTRALIA,
            scheme_code="au-sa.district",
            code=f"{day}:{slug}",
            area_relation="unlocated",
            kind="aggregate" if d.places else "standard",
        )
        p.name(district_unit, f"District of {name}", "en", "official")
        leaves = d.places or [d.declared]
        seen: Counter[str] = Counter()
        for place in leaves:
            if d.places:
                seen[place.name] += 1
                code = f"{day}:{slug}:{changeset.slug(place.name)}" + (
                    f"-{seen[place.name]}" if seen[place.name] > 1 else ""
                )
                unit = key("unit", code)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=AUSTRALIA,
                    scheme_code="au-sa.place",
                    code=code,
                    parent_id=district_unit,
                    area_relation="unlocated",
                    kind=place.kind,
                )
                p.name(unit, f"{place.name} ({name})", "en", "official")
            else:
                unit = district_unit
            for option, value in zip(options, place.votes, strict=True):
                _put(p, result_set, unit, option, VOTES, value)
            _put(p, result_set, unit, None, VALID, place.formal)
            _put(p, result_set, unit, None, INVALID, place.informal)
            _put(p, result_set, unit, None, CAST, place.total)

        if d.enrolled is not None:
            if d.places:
                roll = key("unit", day, slug, "roll")
                p.add(
                    "reporting_unit",
                    id=roll,
                    polity_id=AUSTRALIA,
                    scheme_code="au-sa.district",
                    code=f"{day}:{slug}:roll",
                    parent_id=district_unit,
                    area_relation="unlocated",
                    kind="aggregate",
                )
                p.name(roll, f"District of {name}: electoral roll", "en", "official")
            else:
                roll = district_unit
            _put(p, result_set, roll, None, REGISTERED, d.enrolled)
            p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=d.enrolled)
        total = d.declared
        for measure, value in ((CAST, total.total), (INVALID, total.informal), (VALID, total.formal)):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, total.votes, strict=True):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        ranked = d.finalists
        if ranked and total.runoff:
            first, second = (d.candidates[i].name for i in ranked)
            p.add(
                "note",
                id=key("note", result_set, "final"),
                entity_id=result_set,
                kind="context",
                body_md=f"After the distribution of preferences the final two candidates held {first} "
                f"{total.runoff[0]:,} and {second} {total.runoff[1]:,} votes.",
                lang="en",
            )
            win = ranked[0] if total.runoff[0] > total.runoff[1] else ranked[1]
        else:
            win = max(range(len(options)), key=lambda i: total.votes[i] or 0)
        for index, option in enumerate(options):
            kind = "elected" if index == win else "lost" if ranked and index in ranked else "eliminated"
            if not ranked:
                kind = "elected" if index == win else "lost"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    return p
