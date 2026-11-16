"""Dominica's Electoral Office: every general election and by-election it publishes results for, 1970 to 2022."""

import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "dm.eo"
SITE = "https://electoraloffice.gov.dm"
PAGE = f"{SITE}/past-general-elections"
DOMINICA = str(uuid5(package.NAMESPACE, "polity\x1fDM"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

# kind: "general" or "by_election"; level: "winner" (the winner of each constituency only), "candidates" (every
# candidate's votes by constituency), or "stations" (every candidate's votes by polling station).
ELECTIONS: dict[str, tuple[str, str, str]] = {
    "1970-10-26": ("general", "winner", "1970-general-election-results"),
    "1975-03-24": ("general", "winner", "1975-general-election-results"),
    "1980-07-21": ("general", "winner", "1980-general-election-results"),
    "1985-07-01": ("general", "winner", "1985-general-election-results"),
    "1990-05-28": ("general", "winner", "1990-general-election-results"),
    "1992-07-27": ("by_election", "winner", "1992-by-election-results"),
    "1993-12-20": ("by_election", "winner", "1993-by-election-results"),
    "1995-06-12": ("general", "winner", "1995-general-election-results"),
    "1996-08-12": ("by_election", "winner", "1996-by-election-results"),
    "2000-01-31": ("general", "winner", "2000-general-election-results"),
    "2005-05-05": ("general", "winner", "2005-general-election-results"),
    "2009-12-18": ("general", "candidates", "2009-general-election-results"),
    "2010-07-09": ("by_election", "candidates", "2010-by-election-results"),
    "2014-12-08": ("general", "stations", "2014-general-elections-results"),
    "2016-06-07": ("by_election", "stations", "2016-soufriere-by-election-results"),
    "2019-12-06": ("general", "stations", "2019-general-elections-results"),
    "2021-11-25": ("by_election", "stations", "2021-grand-bay-by-election-results-final-count"),
    "2022-12-06": ("general", "stations", "2022-general-election-results"),
}
JSON_YEARS = ("2014", "2019", "2022")
INDEX = [PAGE, f"{PAGE}?start=10"]
UNIT_SCHEME = "dm.polling_station"
COUNT_SCHEME = "dm.constituency_count"
AREA_SCHEME = "dm.constituency"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DEO = key("source", "electoral-office")
HOUSE = key("body", "house-of-assembly")
SYSTEM = key("electoral_system", "house", "fptp")

METHOD = (
    "Published by the Electoral Office of the Commonwealth of Dominica. Registered voters are the electors on the "
    "roll for the constituency or polling station. Valid votes are the votes counted for candidates, rejected "
    "ballots are stored as invalid ballots, and ballots cast are valid votes plus rejected ballots. Spoilt ballots, "
    "which the Office lists separately and leaves out of its turnout, are not stored."
)
WINNER_METHOD = (
    "Published by the Electoral Office of the Commonwealth of Dominica as a table of constituencies with the "
    "electorate, the votes cast, and the candidate elected. It gives no votes for individual candidates, so only the "
    "winner is listed."
)


def url(name: str) -> str:
    return f"{PAGE}/{name}"


def page_url(day: str) -> str:
    return url(ELECTIONS[day][2])


def json_url(year: str, name: str) -> str:
    return f"{SITE}/images/json_files/{year}/{name}.json"


def script_url(year: str) -> str:
    return f"{SITE}/includes/_wz/wz_elec_results_{year}.js"


def clean(text: object) -> str:
    return " ".join(html.unescape(str(text or "")).replace("\xa0", " ").split())


def fetch_text(link: str, suffix: str = ".html") -> str:
    return raw.fetch(SOURCE, link, suffix=suffix).path.read_text(encoding="utf-8", errors="replace")


def letters(day: str) -> list[str]:
    text = fetch_text(page_url(day))
    return sorted(set(re.findall(r"(?:prelimresults|duly_elected)\('([A-Z])'\)", text)))


def fetch() -> None:
    for link in INDEX:
        raw.fetch(SOURCE, link, suffix=".html")
    for day, (_, level, _) in ELECTIONS.items():
        raw.fetch(SOURCE, page_url(day), suffix=".html")
        year = day[:4]
        if level == "stations" and year in JSON_YEARS:
            raw.fetch(SOURCE, script_url(year), suffix=".js")
            for letter in letters(day):
                if year == "2022" and letter in DULY:
                    continue
                raw.fetch(SOURCE, json_url(year, letter), suffix=".json")
                raw.fetch(SOURCE, json_url(year, f"{letter}_breakdown"), suffix=".json")


# 2022: constituencies whose single candidate was returned without a poll; the page shows only a name and a photo.
DULY = {"B", "C", "J", "K", "P", "U"}


# Parsing ---------------------------------------------------------------------------------------------------------


def article(text: str) -> str:
    start = text.find('itemprop="articleBody"')
    end = text.rfind("<section", start, text.find('id="sp-bottom"', start))
    return text[start:end]


def cells(row: str) -> list[str]:
    return [
        clean(re.sub(r"<[^>]+>", " ", cell)) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
    ]


def lines(text: str) -> list[str]:
    body = article(text)
    body = re.sub(r"</t[dh]>\s*", "\t", body)
    body = re.sub(r"</tr>|<br\s*/?>|</p>|</h\d>|</div>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    return [line for line in (re.sub(r"[ \xa0]+", " ", raw_line).strip(" \t") for raw_line in body.split("\n")) if line]


def number(text: object) -> int | None:
    found = re.sub(r"[^\d]", "", str(text or ""))
    return int(found) if found else None


@dataclass
class Candidate:
    name: str
    party: str | None  # the printed party name, or None for an independent
    abbr: str | None = None
    votes: int | None = None
    elected: bool = False


@dataclass
class Station:
    code: str
    name: str
    surnames: str
    district: str
    registered: int | None
    rejected: int | None
    spoilt: int | None
    counted: int | None
    votes: dict[str, int]  # by candidate position


@dataclass
class Seat:
    name: str  # the constituency as printed
    registered: int | None = None
    cast: int | None = None
    candidates: list[Candidate] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)
    declared_rejected: int | None = None
    declared_votes: dict[int, int] = field(default_factory=dict)  # candidate position, as the Office totals them


def winners(day: str) -> tuple[list[Seat], dict[str, int]]:
    text = fetch_text(page_url(day))
    seats = []
    for row in re.findall(r"<tr>(.*?)</tr>", article(text), flags=re.DOTALL):
        c = cells(row)
        if len(c) == 6 and c[0] != "Electoral District" and not c[1].startswith("Total"):
            party = c[5]
            seat = Seat(c[0], number(c[1]), number(c[3]))
            seat.candidates.append(Candidate(c[4], party, elected=True))
            seats.append(seat)
    summary: dict[str, int] = {}
    last = lines(text)[-1] if lines(text) else ""
    if " | " in last or re.search(r"[A-Za-z] [-–] \d+$", last):
        for part in last.replace("–", "-").split("|"):
            name, _, count = part.rpartition("-")
            if number(count) is not None and name.strip():
                summary[clean(name)] = number(count) or 0
    return seats, summary


def by_constituency(day: str) -> list[Seat]:
    text = fetch_text(page_url(day))
    seats: list[Seat] = []
    for row in re.findall(r"<tr>(.*?)</tr>", article(text), flags=re.DOTALL):
        c = cells(row)
        if len(c) == 1 and c[0]:
            seats.append(Seat(c[0]))
        elif len(c) == 3 and c[0] != "Candidate name":
            name, party, votes = c
            independent = clean(party) == clean(name)
            seats[-1].candidates.append(
                Candidate(clean(name), None if independent else clean(party), None, number(votes))
            )
    return seats


def tagged(label: str) -> tuple[str, str | None]:
    found = re.fullmatch(r"(.*?)\s*\(([A-Za-z]+)\)", clean(label))
    return (clean(found.group(1)), found.group(2)) if found else (clean(label), None)


def match(candidates: list[Candidate], name: str, abbr: str | None) -> int:
    same = [i for i, c in enumerate(candidates) if c.abbr == abbr]
    if len(same) == 1:
        return same[0]
    words = set(re.findall(r"\w+", name.casefold()))
    best = [
        i for i in same or range(len(candidates)) if words <= set(re.findall(r"\w+", candidates[i].name.casefold()))
    ]
    if len(best) != 1:
        best = [i for i in same or range(len(candidates)) if clean(candidates[i].name) == name]
    if len(best) != 1:
        raise ValueError(f"cannot match {name!r} ({abbr}) among {[c.name for c in candidates]}")
    return best[0]


def json_seats(day: str) -> list[Seat]:
    year = day[:4]
    seats: list[Seat] = []
    names: dict[str, str] = {}
    for letter in letters(day):
        if year == "2022" and letter in DULY:
            continue
        top = json.loads(raw.cached(SOURCE, json_url(year, letter)).path.read_text(encoding="utf-8"))["candidates"]
        stations = json.loads(
            raw.cached(SOURCE, json_url(year, f"{letter}_breakdown")).path.read_text(encoding="utf-8")
        )["station"]
        seat = Seat(clean(top[0]["constit_name"]), int(top[0]["numElectors"]) if "numElectors" in top[0] else None)
        names[letter] = seat.name
        for position, c in enumerate(top):
            party = clean(c["partyname"])
            seat.candidates.append(
                Candidate(
                    clean(c["name"]),
                    None if c["partyabbr"] == "IND" or party == clean(c["name"]) else party,
                    c["partyabbr"],
                    int(c["votes"]),
                )
            )
            seat.declared_votes[position] = int(c["votes"])
        seat.declared_rejected = None
        for s in stations:
            votes: dict[str, int] = {}
            for c in s["candidates"]:
                name, abbr = tagged(c["name"])
                position = match(seat.candidates, name, abbr)
                votes[str(position)] = int(c["votes"])
            seat.stations.append(
                Station(
                    s["polling_station_id"],
                    f"{clean(s['area'])} | {clean(s['location'])}",
                    clean(s["surnames"]),
                    clean(s["polling_district_id"]),
                    int(s["num_electors"]),
                    int(s["rejected_ballots"]),
                    int(s["spoilt_ballots"]),
                    int(s["ballots_counted"]),
                    votes,
                )
            )
        seat.cast = None
        seats.append(seat)
    return seats


PARTY_NAMES = {"DLP": "Dominica Labour Party", "UWP": "United Workers Party", "IND": None}


def page_seat(day: str, name: str) -> Seat:
    """A by-election page that lists every polling station in turn."""
    rows = lines(fetch_text(page_url(day)))
    seat = Seat(name)
    abbrs: list[str] = []
    totals = rows.index("Constituency Totals") if "Constituency Totals" in rows else len(rows)
    if day == "2016-06-07":
        for row in rows[: rows.index("Polling Station\tSurnames")]:
            found = re.fullmatch(r"([^\t]+?) \(([A-Z]+)\)", row)
            if found:
                abbrs.append(found.group(2))
                seat.candidates.append(Candidate(found.group(1), PARTY_NAMES[found.group(2)], found.group(2)))
        for row, count in zip(rows[totals + 1 : -8], rows[totals + 2 : -8], strict=False):
            found = re.fullmatch(r"([^\t]+?) \(([A-Z]+)\)", row)
            if found and found.group(2) in abbrs:
                seat.declared_votes[abbrs.index(found.group(2))] = number(count) or 0
        tail = {
            row: number(rows[i + 1])
            for i, row in enumerate(rows)
            if i > totals and i + 1 < len(rows) and "\t" not in row and not row.isdigit()
        }
        seat.registered = tail["Number of Electors"]
        seat.cast = tail["Total Ballots Counted"]
        seat.declared_rejected = tail["Total Rejected Ballots"]
    else:
        for i, row in enumerate(rows):
            if row == "Votes" and rows[i - 1].endswith(")"):
                found = re.fullmatch(r"(.*?) \(([A-Z]+)\)", rows[i - 1])
                if not found:
                    raise ValueError(f"{day}: no party in {rows[i - 1]!r}")
                party = clean(found.group(1))
                abbrs.append(found.group(2))
                seat.candidates.append(
                    Candidate(clean(rows[i - 2]), None if found.group(2) == "IND" else party, found.group(2))
                )
                seat.declared_votes[len(abbrs) - 1] = number(rows[i + 1]) or 0
    current: Station | None = None
    fields = {
        "Rejected Ballots": "rejected",
        "Spoilt Ballots": "spoilt",
        "Ballots Counted": "counted",
        "Number of Electors": "registered",
    }
    for row in rows:
        header = (
            re.fullmatch(r"(.+) \(([A-Z]\d+-\d+)\)\t(.+)", row)
            if day == "2016-06-07"
            else re.fullmatch(r"(.+ \| .+) \| Surnames: (.+) \| Reported: (.+)", row)
        )
        if header:
            number_in_page = len(seat.stations) + 1
            code = header.group(2) if day == "2016-06-07" else f"{number_in_page:02d}"
            surnames = header.group(3) if day == "2016-06-07" else header.group(2)
            current = Station(
                code, header.group(1), surnames, code[:3] if day == "2016-06-07" else "", None, None, None, None, {}
            )
            seat.stations.append(current)
        elif current is not None:
            found = re.fullmatch(r"([^\t]+?) \(([A-Z]+)\)\t(\d+)(?: votes)?", row)
            if found and found.group(2) in abbrs:
                position = match(seat.candidates, clean(found.group(1)), found.group(2))
                current.votes[str(position)] = int(found.group(3))
            elif "\t" in row:
                label, value = row.split("\t")[:2]
                if label in fields and number(value) is not None and getattr(current, fields[label]) is None:
                    setattr(current, fields[label], number(value))
    for position, votes in seat.declared_votes.items():
        seat.candidates[position].votes = votes
    return seat


# Loading ---------------------------------------------------------------------------------------------------------

BY_ELECTION_SEAT = {"2016-06-07": "Soufriere", "2021-11-25": "Grand Bay"}
SAME = {"laplaine": "la-plaine", "le-blanc-labour-party": "leblanc-labour-party"}
PARTY_ABBR = {"dlp": "Dominica Labour Party", "uwp": "United Workers Party", "dfp": "Dominica Freedom Party"}


def tidy_party(name: str | None) -> str | None:
    if name is None:
        return None
    return "Leblanc Labour Party" if slug(name) == "le-blanc-labour-party" else clean(name)


def area_code(name: str) -> str:
    code = slug(name)
    return SAME.get(code, code)


def area_name(name: str) -> str:
    return re.sub(r"\s*-\s*", " ", clean(name)).replace("Laplaine", "La Plaine")


def duly_elected(day: str) -> list[Seat]:
    text = fetch_text(script_url(day[:4]), ".js")
    names = {
        letter: clean(
            json.loads(raw.cached(SOURCE, json_url("2019", letter)).path.read_text(encoding="utf-8"))["candidates"][0][
                "constit_name"
            ]
        )
        for letter in DULY
    }
    seats = []
    for letter, person, image in re.findall(
        r"x === '(\w)'\)\s*\{\s*de_cand_name = '([^']+)';\s*de_img_src = '[^']*/([a-z]+)_", text
    ):
        party = PARTY_ABBR[image]
        seats.append(Seat(names[letter], candidates=[Candidate(clean(person), party, image.upper(), elected=True)]))
    return seats


def load(day: str) -> tuple[list[Seat], dict[str, int]]:
    level = ELECTIONS[day][1]
    if level == "winner":
        return winners(day)
    if level == "candidates":
        return by_constituency(day), {}
    if day[:4] in JSON_YEARS:
        seats = json_seats(day)
        if day[:4] == "2022":
            seats += duly_elected(day)
        return seats, {}
    return [page_seat(day, BY_ELECTION_SEAT[day])], {}


# Packages --------------------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in ELECTIONS)]


def parse() -> None:
    registry().write()
    for day in ELECTIONS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def area_id(name: str) -> str:
    return key("area", area_code(name))


def party_id(name: str) -> str:
    return key("party", slug(name))


def all_seats() -> dict[str, list[Seat]]:
    return {day: load(day)[0] for day in ELECTIONS}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Dominica's constituencies, parties, House of Assembly, and electoral system, from the Electoral Office's "
        "published results",
    )
    p.external.add(DOMINICA)
    p.add(
        "source",
        id=DEO,
        kind="official_body",
        publisher="Electoral Office of the Commonwealth of Dominica",
        title="Past general elections and by-elections",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Office of the Commonwealth of Dominica",
    )
    for link in raw.keys(SOURCE):
        p.artifact(raw.cached(SOURCE, link), DEO)
    for code, kind, rank, noun in (
        (AREA_SCHEME, "electoral_district", 1, "constituency"),
        (COUNT_SCHEME, "synthetic", 2, "constituency count"),
        (UNIT_SCHEME, "precinct", 3, "polling station"),
    ):
        p.add("scheme", code=code, polity_id=DOMINICA, kind=kind, rank=rank, noun=noun)
    p.add("body", id=HOUSE, polity_id=DOMINICA, kind="unicameral")
    p.name(HOUSE, "House of Assembly of Dominica", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=DOMINICA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each constituency elects one member of the House of Assembly, and the candidate with the most votes "
        "is elected. The appointed and ex officio members of the House are not elected and are not stored.",
        lang="en",
    )
    areas: dict[str, str] = {}
    parties: dict[str, str] = {}
    for seats in all_seats().values():
        for seat in seats:
            areas[area_code(seat.name)] = area_name(seat.name)
            for c in seat.candidates:
                if tidy_party(c.party):
                    parties[slug(tidy_party(c.party) or "")] = tidy_party(c.party) or ""
    for code, name in sorted(areas.items()):
        id = key("area", code)
        p.add("area", id=id, scheme_code=AREA_SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "en", "official")
    for code, name in sorted(parties.items()):
        p.add("party", id=key("party", code), kind="party", polity_id=DOMINICA)
        p.name(key("party", code), name, "en", "official")
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def result_set(
    p: Package, election: str, contest: str, name: str, kind: str, default: bool, note: str, complete: bool = True
) -> str:
    id = key("result_set", name)
    p.add(
        "result_set",
        id=id,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind=kind,
        source_id=DEO,
        status="active",
        is_default=default,
        completeness=1.0 if kind == "counted" and complete else None,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    return id


def unit(p: Package, day: str, scheme: str, code: str, seat: Seat, name: str, kind: str, relation: str) -> str:
    id = key("unit", day, scheme, code)
    p.add(
        "reporting_unit",
        id=id,
        polity_id=DOMINICA,
        scheme_code=scheme,
        code=f"{day}:{code}",
        area_relation=relation,
        area_id=area_id(seat.name),
        kind=kind,
    )
    p.name(id, name, "en", "official")
    return id


def title(day: str, seats: list[Seat]) -> str:
    year = day[:4]
    if ELECTIONS[day][0] == "general":
        return f"{year} Dominican general election"
    names = " and ".join(area_name(seat.name) for seat in seats)
    return f"{year} {names} by-election" if len(seats) == 1 else f"{year} by-elections in {names}"


def winner_index(seat: Seat) -> int:
    votes = [c.votes for c in seat.candidates]
    if all(v is None for v in votes):
        return next(i for i, c in enumerate(seat.candidates) if c.elected)
    best = max(v or 0 for v in votes)
    leaders = [i for i, v in enumerate(votes) if v == best]
    if len(leaders) != 1:
        raise ValueError(f"{seat.name}: a tie for first place")
    return leaders[0]


def build(day: str) -> Package:
    kind, level, _ = ELECTIONS[day]
    seats, summary = load(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Dominican {'general election' if kind == 'general' else 'by-election'} of {day}, "
        + {
            "winner": "with the electorate, votes cast, and winner of each constituency",
            "candidates": "with every candidate's votes in each constituency",
            "stations": "with every candidate's votes at each polling station",
        }[level]
        + ", from the Electoral Office",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(DOMINICA)
    p.uses(raw.cached(SOURCE, page_url(day)))
    if level == "stations" and day[:4] in JSON_YEARS:
        p.uses(raw.cached(SOURCE, script_url(day[:4])))
        for letter in letters(day):
            names = [letter] if day[:4] != "2022" or letter not in DULY else []
            for name in names:
                p.uses(raw.cached(SOURCE, json_url(day[:4], name)))
                p.uses(raw.cached(SOURCE, json_url(day[:4], f"{name}_breakdown")))
    election = key("election", day)
    label = "general" if kind == "general" else "by-election"
    p.add(
        "election",
        id=election,
        polity_id=DOMINICA,
        kind=kind,
        date_from=date.fromisoformat(day),
        date_to=date.fromisoformat(day),
        date_precision="day",
        status="held",
    )
    p.name(election, title(day, seats), "en", "official")
    p.add("slug", path=f"dm/{day}-{label}", entity_id=election, is_current=True)
    elected = Counter(tidy_party(seat.candidates[winner_index(seat)].party) or "Independent" for seat in seats)
    printed = Counter({tidy_party(name) or "": count for name, count in summary.items()})
    if summary and printed != elected:
        p.add(
            "note",
            id=key("note", election, "seats"),
            entity_id=election,
            kind="caveat",
            body_md="The page's own line of seats by party reads "
            + ", ".join(f"{name} {count}" for name, count in summary.items())
            + ", which does not agree with its table of constituencies. The table gives "
            + ", ".join(f"{name or 'Independent'} {count}" for name, count in elected.most_common())
            + ". The seats stored here follow the table.",
            lang="en",
        )
    for seat in sorted(seats, key=lambda s: area_code(s.name)):
        code = area_code(seat.name)
        contest = key("contest", day, code)
        tacit = not seat.stations and seat.candidates[0].votes is None and seat.registered is None
        tacit = tacit and level == "stations"
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=area_id(seat.name),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="tacit" if tacit else "counted",
        )
        p.name(contest, f"House of Assembly, {area_name(seat.name)}", "en", "official")
        p.add("slug", path=f"dm/{day}-{label}/{code}", entity_id=contest, is_current=True)
        options: list[str] = []
        for position, c in enumerate(seat.candidates):
            party = tidy_party(c.party)
            option = key("option", day, code, position)
            person = key("person", day, code, slug(c.name))
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id(party) if party else None,
                ballot_position=None if level == "winner" else position + 1,
                ballot_label=c.name,
            )
            p.add("person", id=person)
            p.name(person, c.name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=party_id(party) if party else None,
                is_independent=party is None,
            )
        winner = winner_index(seat)
        if tacit:
            rs = result_set(
                p,
                election,
                contest,
                f"{day}:{code}:tacit",
                "tacit",
                True,
                "The only candidate nominated was declared elected without a poll. The Electoral Office lists no votes.",
            )
            p.add("outcome", result_set_id=rs, option_id=options[0], kind="elected", seats=1)
            continue
        official = result_set(
            p,
            election,
            contest,
            f"{day}:{code}:constituency",
            "counted",
            True,
            WINNER_METHOD if level == "winner" else METHOD,
            complete=level != "winner",
        )
        count = unit(p, day, COUNT_SCHEME, code, seat, f"{area_name(seat.name)} constituency", "standard", "equals")
        put(p, official, count, None, REGISTERED, seat.registered)
        put(p, official, count, None, CAST, seat.cast)
        for position, c in enumerate(seat.candidates):
            put(p, official, count, options[position], VOTES, seat.declared_votes.get(position, c.votes))
        for position, option in enumerate(options):
            p.add(
                "outcome",
                result_set_id=official,
                option_id=option,
                kind="elected" if position == winner else "lost",
                seats=1 if position == winner else None,
            )
        if seat.declared_rejected is not None:
            put(p, official, count, None, INVALID, seat.declared_rejected)
        if not seat.stations:
            continue
        finer = result_set(p, election, contest, f"{day}:{code}:station", "counted", False, METHOD)
        for st in seat.stations:
            sid = unit(
                p,
                day,
                UNIT_SCHEME,
                st.code,
                seat,
                f"{st.name} (station {st.code}, surnames {st.surnames})",
                "standard",
                "within",
            )
            put(p, finer, sid, None, REGISTERED, st.registered)
            put(p, finer, sid, None, VALID, st.counted)
            put(p, finer, sid, None, INVALID, st.rejected)
            if st.counted is not None and st.rejected is not None:
                put(p, finer, sid, None, CAST, st.counted + st.rejected)
            for position, option in enumerate(options):
                put(p, finer, sid, option, VOTES, st.votes.get(str(position), 0))
        for position, option in enumerate(options):
            p.add("declared", result_set_id=finer, option_id=option, measure=VOTES, value=seat.declared_votes[position])
        if seat.registered is not None:
            p.add("declared", result_set_id=finer, option_id=None, measure=REGISTERED, value=seat.registered)
        if seat.declared_rejected is not None:
            p.add("declared", result_set_id=finer, option_id=None, measure=INVALID, value=seat.declared_rejected)
        if seat.cast is not None:
            p.add("declared", result_set_id=finer, option_id=None, measure=CAST, value=seat.cast)
    return p
