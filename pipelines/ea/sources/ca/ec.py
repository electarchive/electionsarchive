"""Canada's federal by-elections of 2007 to 2014, by polling division, from Elections Canada's official voting results.

Elections Canada's poll-by-poll files for these by-elections list candidates by name only, so each candidate's party
and each printed total come from the report's own tables. A candidate's poll sums must equal the votes the report
prints for that candidate, or the build stops.
"""

import csv
import html
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid5

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "ca.ec"
HOST = "https://www.elections.ca"
# Canada predates the uuid5 country ids: its record came from an earlier changeset, so it is referenced by that id.
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
LICENSE = "Open Government Licence – Canada"
LICENSE_URL = "https://open.canada.ca/en/open-government-licence-canada"
ACCOUNTING_FLAG = 32
INDEPENDENT = {"independent", "no affiliation", "no affiliation to a recognized party"}
METHOD = (
    "Each polling division's votes are Elections Canada's official poll-by-poll counts. The files name candidates but "
    "not parties, so party and the elected candidate come from the printed list of candidates and individual results, "
    "and every candidate's polling-division votes add up to the votes printed there. Votes from the special voting "
    "rules are reported in groups, not by polling division, and sit on aggregate units. A merged polling division "
    "carries its electors and no votes, which are counted at the polling division it merged with."
)


@dataclass(frozen=True)
class Event:
    report: str
    synopsis: str
    files: dict[str, str]


def _csv(folder: str, pattern: str, feds: tuple[str, ...]) -> dict[str, str]:
    return {fed: f"{HOST}/{folder}/{pattern.format(fed)}" for fed in feds}


def _synopsis(folder: str, document: str) -> str:
    return f"{HOST}/content.aspx?section=res&dir=rep/off/{folder}&document={document}&lang=e"


EVENTS: dict[date, Event] = {
    date(2007, 9, 17): Event(
        "ovr_2007",
        _synopsis("ovr_2007", "synopsis"),
        _csv("ele/pas/2007/csv", "pollbypoll{}.csv", ("24047", "24060", "24063")),
    ),
    date(2008, 3, 17): Event(
        "ovr_2008",
        _synopsis("ovr_2008", "synopsis"),
        _csv("ele/pas/2008/csv", "pollbypoll{}.csv", ("35093", "35100", "47003", "59033")),
    ),
    date(2009, 11, 9): Event(
        "ovr_2009",
        _synopsis("ovr_2009", "synopsis"),
        _csv("res/rep/off/ovr_2009/csv", "pollbypoll{}.csv", ("12007", "24021", "24058", "59017")),
    ),
    date(2010, 11, 29): Event(
        "ovr_2010",
        _synopsis("ovr_2010", "synopsis"),
        _csv("res/rep/off/ovr_2010/csv", "pollbypoll_bureauparbureau{}.csv", ("35096", "46004", "46012")),
    ),
    date(2012, 3, 19): Event(
        "ovr_2012",
        _synopsis("ovr_2012", "synopsis"),
        _csv("res/rep/off/ovr_2012", "pollbypoll_bureauparbureau{}.csv", ("35094",)),
    ),
    date(2012, 11, 26): Event(
        "ovr_2012b",
        _synopsis("ovr_2012b", "p4"),
        _csv("res/rep/off/ovr_2012b/csv", "{}_e.csv", ("35014", "48006", "59035")),
    ),
    date(2013, 5, 13): Event(
        "ovr_2013b",
        _synopsis("ovr_2013b", "p4"),
        _csv("res/rep/off/ovr_2013b/csv", "{}_e.csv", ("10004",)),
    ),
    date(2013, 11, 25): Event(
        "ovr_2013b2",
        _synopsis("ovr_2013b2", "p4"),
        _csv("res/rep/off/ovr_2013b2/csv", "{}_e.csv", ("24009", "35093", "46001", "46008")),
    ),
    date(2014, 6, 30): Event(
        "ovr_2014",
        _synopsis("ovr_2014", "p2"),
        _csv("res/rep/off/ovr_2014/csv", "{}.csv", ("35080", "35095", "48001", "48020")),
    ),
    date(2014, 11, 17): Event(
        "ovr_2014b",
        _synopsis("ovr_2014b", "p2"),
        _csv("res/rep/off/ovr_2014b/csv", "{}.csv", ("35099", "48028")),
    ),
}
PARTY_NAMES = {
    "bloc quebecois": "Bloc Québécois",
    "cap": "Canadian Action Party",
    "chp canada": "Christian Heritage Party of Canada",
    "green party": "Green Party of Canada",
    "marxist leninist party": "Marxist-Leninist Party of Canada",
    "marxist leninist party of canada": "Marxist-Leninist Party of Canada",
    "pc party": "Progressive Conservative Party of Canada",
    "progressive canadian party": "Progressive Canadian Party",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_NAME = "Elections Canada"
BODY = key("body", "house_of_commons")
SYSTEM = key("electoral_system", "fptp")


def packages() -> list[str]:
    return ["ca.ec/registry", *(f"ca.ec/by-{day}" for day in EVENTS)]


def fetch() -> None:
    for event in EVENTS.values():
        raw.fetch(SOURCE, event.synopsis, suffix=".html")
        for url in event.files.values():
            raw.fetch(SOURCE, url, suffix=".csv")


def decoded(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
        return data.decode("cp850") if "‚" in text else text


def norm(text: str) -> str:
    folded = (
        unicodedata.normalize("NFKD", re.sub(r"[\u2010-\u2015]", "-", text)).encode("ascii", "ignore").decode().lower()
    )
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


@dataclass
class Listed:
    index: int
    name: str
    party: str
    votes: int


def listed(page: str) -> list[Listed]:
    found: list[Listed] = []
    for index, row in enumerate(re.findall(r"<tr\b.*?</tr>", page, flags=re.DOTALL | re.IGNORECASE)):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
        for position, cell in enumerate(cells):
            parts = re.split(r"<br\s*/?>", cell, flags=re.IGNORECASE)
            if len(parts) == 2:
                name, party = clean(parts[0]), re.sub(r"\s*elected$", "", clean(parts[1])).strip("() ").strip()
            else:
                single = re.fullmatch(r"(.+?)\s*\(([^()]+)\)(?:\s*elected)?", clean(cell))
                if single is None:
                    continue
                name, party = single.group(1), single.group(2)
            votes = next(
                (
                    int(text.replace(",", ""))
                    for text in (clean(after) for after in cells[position + 1 :])
                    if re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", text)
                ),
                None,
            )
            if name and party and votes is not None:
                found.append(Listed(index, name, party, votes))
            break
    return found


def totals(page: str) -> dict[str, tuple[int, int, int]]:
    """Electors, valid ballots, and rejected ballots that the report prints for each district."""
    found: dict[str, tuple[int, int, int]] = {}
    for row in re.findall(r"<tr\b.*?</tr>", page, flags=re.DOTALL | re.IGNORECASE):
        cells = [
            clean(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
        ]
        if len(cells) < 6 or not re.fullmatch(r"\d+\.\d+", cells[-1]):
            continue
        numbers = [int(cell.replace(",", "")) for cell in cells[1:-1] if re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", cell)]
        if len(numbers) >= 4 and numbers[-1] == numbers[-2] + numbers[-3]:
            found[norm(cells[0])] = (numbers[-4], numbers[-3], numbers[-2])
    return found


@dataclass
class Poll:
    fed: str
    number: str
    name: str
    status: str
    host: str | None
    votes: list[int]
    rejected: int | None
    cast: int | None
    electors: int | None


@dataclass
class District:
    fed: str
    name: str
    candidates: list[str]
    polls: list[Poll]


HOST_NUMBER = re.compile(r"(?:No\.?\s*|n\W\s*)(\d+[A-Z]?(?:-\d+[A-Z]?)?)", re.IGNORECASE)


def district(fed: str, text: str) -> District:
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    header, *body = rows
    candidates = [name.strip() for name in header[4:-3]]
    polls: list[Poll] = []
    name = ""
    for row in body:
        if len(row) == len(header) - 1:
            row = [*row, ""]
        if len(row) != len(header):
            raise ValueError(f"{fed}: a row has {len(row)} cells, the header {len(header)}: {row}")
        name = row[1].split("/")[0].replace("--", "–").strip()
        number, label = row[2].strip(), row[3].split("/")[0].strip()
        cells = [cell.strip() for cell in row[4:-3]]
        status, host = "counted", None
        if number == "" and label.startswith("Group"):
            number = f"S/R {label.removeprefix('Group ').split()[0]}"
        if cells[0] and not cells[0].isdigit():
            lowered = cells[0].lower()
            if lowered.startswith("void"):
                status = "void"
            elif lowered.startswith("no poll"):
                status = "none"
            else:
                found = HOST_NUMBER.search(cells[0])
                if found is None:
                    raise ValueError(f"{fed}: poll {number} has the status {cells[0]!r}")
                status, host = "merged", found.group(1)
        elif any(not cell.isdigit() for cell in cells):
            raise ValueError(f"{fed}: poll {number} has blank votes: {cells}")
        polls.append(
            Poll(
                fed,
                number,
                label,
                status,
                host,
                [int(cell) for cell in cells] if status == "counted" else [],
                int(row[-3]) if status == "counted" and row[-3].strip().isdigit() else None,
                int(row[-2]) if status == "counted" and row[-2].strip().isdigit() else None,
                int(row[-1]) if row[-1].strip().isdigit() else None,
            )
        )
    return District(fed, name, candidates, polls)


def channel(poll: Poll) -> tuple[int, str]:
    if poll.number.startswith("S/R"):
        return 9, "aggregate"
    digits = re.match(r"\d+", poll.number)
    number = int(digits.group()) if digits else 0
    if 500 <= number < 600 or re.match(r"(Mobile|Bureau itin)", poll.name, re.IGNORECASE):
        return 6, "mobile"
    if 600 <= number < 700:
        return 2, "standard"
    return 1, "standard"


def party_name(text: str) -> str:
    return PARTY_NAMES.get(norm(text), text)


def is_independent(text: str) -> bool:
    return norm(text) in {norm(label) for label in INDEPENDENT}


def load(day: date) -> tuple[Event, list[District], list[Listed], dict[str, tuple[int, int, int]]]:
    event = EVENTS[day]
    page = decoded(raw.cached(SOURCE, event.synopsis).path.read_bytes())
    districts = [district(fed, decoded(raw.cached(SOURCE, url).path.read_bytes())) for fed, url in event.files.items()]
    return event, districts, listed(page), totals(page)


def matched(day: date, districts: list[District], rows: list[Listed]) -> dict[tuple[str, int], Listed]:
    """Pair each candidate in the poll files with the report's printed line, which must carry the same votes."""
    pairs: dict[tuple[str, int], Listed] = {}
    for item in districts:
        sums = [
            sum(poll.votes[i] for poll in item.polls if poll.status == "counted") for i in range(len(item.candidates))
        ]
        for i, candidate in enumerate(item.candidates):
            options = [row for row in rows if norm(row.name) == norm(candidate) and row.votes == sums[i]]
            if len(options) != 1:
                raise ValueError(f"{day} {item.fed}: {candidate} has {sums[i]} poll votes; report lines {options}")
            pairs[item.fed, i] = options[0]
        order = sorted(pairs[item.fed, i].index for i in range(len(item.candidates)))
        leader = max(range(len(item.candidates)), key=lambda i: sums[i])
        if pairs[item.fed, leader].index != order[0]:
            raise ValueError(f"{day} {item.fed}: the candidate listed first is not the one with the most votes")
    return pairs


def registry() -> Package:
    p = Package(
        "ca.ec/registry",
        SOURCE,
        "The House of Commons, its electoral system, and the parties on the ballot in Canada's federal by-elections of "
        "2007 to 2014",
    )
    p.external.add(CANADA)
    p.add("body", id=BODY, polity_id=CANADA, kind="lower_house")
    p.name(BODY, "House of Commons", "en", "official")
    p.name(BODY, "Chambre des communes", "fr", "native", False)
    p.add("electoral_system", id=SYSTEM, polity_id=CANADA, rules={"formula": "fptp"}, rules_version=1)
    p.name(SYSTEM, "First past the post", "en", "official")
    names: dict[str, str] = {}
    for day in EVENTS:
        _, _, rows, _ = load(day)
        for row in rows:
            if not is_independent(row.party):
                names.setdefault(norm(party_name(row.party)), party_name(row.party))
    for text, label in sorted(names.items()):
        id = key("party", text)
        p.add("party", id=id, kind="party", polity_id=CANADA)
        p.name(id, label, "en", "official")
    return p


def by_election(day: date) -> Package:
    event, districts, rows, printed = load(day)
    pairs = matched(day, districts, rows)
    spoken = f"{day:%B} {day.day}, {day.year}"
    p = Package(
        f"ca.ec/by-{day}",
        SOURCE,
        f"Canada's federal by-election{'s' if len(districts) > 1 else ''} of {spoken}, by polling division, from "
        "Elections Canada",
        depends=["ca.ec/registry"],
    )
    p.external |= {CANADA, "ca.pd"}
    results = key("source", day)
    p.add(
        "source",
        id=results,
        kind="official_body",
        publisher=SOURCE_NAME,
        title=f"Official Voting Results, federal by-election{'s' if len(districts) > 1 else ''} of {spoken}",
        url=f"{HOST}/content.aspx?section=res&dir=rep/off/{event.report}&document=index&lang=e",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Elections Canada",
    )
    for url in (event.synopsis, *event.files.values()):
        p.artifact(raw.cached(SOURCE, url), results)
    election = key("election", day)
    p.add("election", id=election, polity_id=CANADA, kind="by_election", date_from=day, date_to=day, status="held")
    p.name(
        election,
        f"Canadian federal by-elections, {spoken}"
        if len(districts) > 1
        else f"{districts[0].name} federal by-election, {spoken}",
        "en",
        "official",
    )
    path = f"ca/{day}-by-election"
    p.add("slug", path=path, entity_id=election, is_current=True)
    parties = {norm(party_name(row.party)): key("party", norm(party_name(row.party))) for row in rows}
    for item in districts:
        contest = key("contest", day, item.fed)
        result_set = key("result_set", day, item.fed)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODY,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{day.year} by-election in {item.name}", "en", "official")
        p.add("slug", path=f"{path}/{slug(item.name)}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            source_id=results,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        sums = [
            sum(poll.votes[i] for poll in item.polls if poll.status == "counted") for i in range(len(item.candidates))
        ]
        leader = max(range(len(item.candidates)), key=lambda i: sums[i])
        options: list[str] = []
        for i, candidate in enumerate(item.candidates):
            line = pairs[item.fed, i]
            tag = f"{day}:{item.fed}:{norm(candidate)}"
            person, option, candidacy = key("person", tag), key("option", tag), key("candidacy", tag)
            options.append(option)
            independent = is_independent(line.party)
            party = None if independent else parties[norm(party_name(line.party))]
            p.add("person", id=person)
            p.name(person, candidate, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=i + 1,
                ballot_label=candidate,
            )
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                member_of_party_id=party,
                is_independent=independent,
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=line.votes)
            if i == leader:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected",
                    seats=1,
                )
        figures = printed.get(norm(item.name))
        if figures is not None:
            electors, valid, rejected = figures
            for measure, value in ((5, rejected), (3, valid + rejected)):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            if electors:
                p.add("declared", result_set_id=result_set, option_id=None, measure=2, value=electors)
        units = {poll.number: key("unit", day, item.fed, poll.number) for poll in item.polls}
        for poll in item.polls:
            unit = units[poll.number]
            chan, kind = channel(poll)
            if poll.host is not None and poll.host not in units:
                raise ValueError(f"{day} {item.fed}: poll {poll.number} merged with {poll.host}, which is not listed")
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CANADA,
                scheme_code="ca.pd",
                code=f"{item.fed}:{poll.number}",
                counted_with_id=units[poll.host] if poll.host is not None else None,
                area_relation="unlocated",
                kind=kind,
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
            if poll.name:
                p.name(unit, poll.name, "en", "official")
            flags = ACCOUNTING_FLAG if kind == "aggregate" else 0
            if poll.status != "none" and poll.status != "void" and poll.electors:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=2,
                    count_round=0,
                    flags=0,
                    value=poll.electors,
                )
            if poll.status != "counted":
                continue
            if poll.cast != sum(poll.votes) + (poll.rejected or 0):
                raise ValueError(
                    f"{day} {item.fed}: poll {poll.number} casts {poll.cast}, not {sum(poll.votes)} + {poll.rejected}"
                )
            for measure, value in ((3, poll.cast), (5, poll.rejected)):
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=chan,
                        measure=measure,
                        count_round=0,
                        flags=flags,
                        value=value,
                    )
            for option, value in zip(options, poll.votes, strict=True):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=chan,
                    measure=1,
                    count_round=0,
                    flags=flags,
                    value=value,
                )
    over = [
        (item.fed, poll.number)
        for item in districts
        for poll in item.polls
        if poll.status == "counted" and poll.electors is not None and (poll.cast or 0) > poll.electors
    ]
    if over:
        p.accept(
            "accounting.turnout",
            f"{len(over)} polling divisions report more ballots cast than electors on their lists, because electors "
            "who registered or were vouched for at the poll on election day are not on the printed list of electors",
        )
    return p


def overlapping() -> list[date]:
    """By-elections that ca.elections already loads by polling division, whose units this pipeline must not repeat."""
    from ea.sources.ca.elections import BYELECTIONS

    return sorted(set(EVENTS) & set(BYELECTIONS))


def parse() -> None:
    if shared := overlapping():
        raise ValueError(f"ca.elections already loads the by-elections of {', '.join(map(str, shared))}")
    built = [registry(), *(by_election(day) for day in EVENTS)]
    seen: dict[tuple[object, object, object, object], str] = {}
    for p in built:
        for row in p.rows.get("reporting_unit", []):
            unit = (row["scheme_code"], row["code"], row["valid_from"], row["valid_to"])
            if unit in seen:
                raise ValueError(f"{p.key}: the reporting unit {unit} is also emitted by {seen[unit]}")
            seen[unit] = p.key
    for p in built:
        p.write()


def emit() -> None:
    package.run("load", packages())


def compare() -> None:
    """Print each district's poll sums beside the totals the report prints."""
    for day in EVENTS:
        _, districts, _, printed = load(day)
        for item in districts:
            counted = [poll for poll in item.polls if poll.status == "counted"]
            sums = (
                sum(poll.electors or 0 for poll in item.polls if poll.status in ("counted", "merged")),
                sum(sum(poll.votes) for poll in counted),
                sum(poll.rejected or 0 for poll in counted),
            )
            figures = printed.get(norm(item.name))
            print(day, item.fed, item.name, "polls", sums, "printed", figures, "OK" if figures == sums else "DIFF")
