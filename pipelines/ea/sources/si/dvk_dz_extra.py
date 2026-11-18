"""Slovenian National Assembly: the 1992 election, and the national-community seats of 2000, 2004, and 2008, from the
State Election Commission's archived result sites (https://www.dvk-rs.si/arhivi/dz<year>/).

`si.dvk/dz-1992-12-06` holds the 1992 election. Its archived site prints each list's votes for Slovenia as a whole and
the registered voters and ballots cast of each of the 8 electoral units, and nothing finer, so the lists' votes are the
declared figures of one national contest and the units carry the turnout. The district packages of
`ea.sources.si.dvk_okraji` hold 1996-2008. The Italian and Hungarian national communities each elect one deputy
separately; the district packages leave those two seats out, and `si.dvk/dz-<date>-communities` adds them to the same
election for 2000, 2004, and 2008. The 1996 and 1992 sites do not print them.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from itertools import chain

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.si.dvk import (
    CHAMBER_BODY,
    DVK_SOURCE,
    ELECTORAL_UNITS,
    SLOVENIA,
    SOURCE,
    area,
    clean,
    key,
    title,
)
from ea.sources.si.dvk_okraji import (
    ARCHIVE,
    SYSTEM_1992,
    SYSTEMS_KEY,
    UNIT_NAMES,
    _Tables,
    number,
    person_name,
    text,
)

DAY_1992 = date(1992, 12, 6)
PAGES_1992 = {"index": "dz1992/index.html", "lists": "dz1992/rezultati.html"}
COMMUNITIES = {
    date(2000, 10, 15): "dz2000/rez_ns.html",
    date(2004, 10, 3): "dz2004/html/rez_ns.html",
    date(2008, 9, 21): "dz2008/rezultati/izidi_skupnosti.html",
}
STAGES = {date(2000, 10, 15): "preliminary", date(2004, 10, 3): "official", date(2008, 9, 21): "official"}
KINDS = {9: ("Italian", "italijanske", "italijanska"), 10: ("Hungarian", "madžarske", "madžarska")}


def url(page: str) -> str:
    return f"{ARCHIVE}/{page}"


def packages() -> list[str]:
    return [slug_1992(), *(slug_communities(day) for day in COMMUNITIES)]


def slug_1992() -> str:
    return f"si.dvk/dz-{DAY_1992}"


def slug_communities(day: date) -> str:
    return f"si.dvk/dz-{day}-communities"


def fetch() -> None:
    for page in chain(PAGES_1992.values(), COMMUNITIES.values()):
        raw.fetch(SOURCE, url(page), suffix=".html")


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    lists = lists_1992()
    turnout = units_1992()
    found = reconcile_1992(lists, turnout)
    print(f"{slug_1992()}: {found}", flush=True)
    election_1992(lists, turnout).write()
    for day in COMMUNITIES:
        parsed = communities(day)
        for community in parsed:
            print(f"{slug_communities(day)}: {community.name}: {community.points}", flush=True)
        election_communities(day, parsed).write()


# 1992 ----------------------------------------------------------------------------------------------------------------


@dataclass
class Turnout:
    unit: int
    name: str
    registered: int
    cast: int


def _tables(page: str) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(text(raw.cached(SOURCE, url(page)).path))
    return parser.tables


def lists_1992() -> dict[str, int]:
    """Each list's votes in Slovenia, by the list's name as printed, and the printed total under "Skupaj"."""
    found: dict[str, int] = {}
    for table in _tables(PAGES_1992["lists"]):
        if not table or table[0][:1] != ["Kandidatna lista"]:
            continue
        for row in table[1:]:
            if len(row) >= 2 and re.match(r"\d", row[1]):
                found[clean(row[0])] = number(row[1])
    if "Skupaj" not in found or len(found) != 27:
        raise LookupError(f"{PAGES_1992['lists']}: found {len(found)} rows")
    return found


def units_1992() -> list[Turnout]:
    """Each electoral unit's registered voters and ballots cast, and Slovenia's under unit 0."""
    found: list[Turnout] = []
    for table in _tables(PAGES_1992["index"]):
        for row in table:
            head = re.match(r"Volilna enota (\d+)\s*/\s*(.+)$", row[0]) if len(row) >= 3 else None
            if head:
                found.append(Turnout(int(head.group(1)), clean(head.group(2)), number(row[1]), number(row[2])))
            elif len(row) >= 3 and row[0] == "Skupaj":
                found.append(Turnout(0, "Slovenija", number(row[1]), number(row[2])))
    if sorted(unit.unit for unit in found) != [0, *ELECTORAL_UNITS]:
        raise LookupError(f"{PAGES_1992['index']}: found units {[unit.unit for unit in found]}")
    return sorted(found, key=lambda unit: unit.unit)


def national_1992() -> dict[str, int]:
    """The ballots cast, valid ballots, and registered voters printed for Slovenia on the 1992 index page."""
    page = text(raw.cached(SOURCE, url(PAGES_1992["index"])).path)
    plain = clean(re.sub(r"<[^>]+>", " ", page))
    figures = {
        "registered": r"pravico voliti skupaj ([\d.]+) volilcev",
        "cast": r"oddano ([\d.]+) glasovnic",
        "valid": r"Veljavnih glasovnic je bilo ([\d.]+)",
    }
    found: dict[str, int] = {}
    for name, pattern in figures.items():
        match = re.search(pattern, plain)
        if match is None:
            raise LookupError(f"{PAGES_1992['index']}: no {name}")
        found[name] = number(match.group(1))
    return found


def reconcile_1992(lists: dict[str, int], turnout: list[Turnout]) -> dict[str, object]:
    """Compare the sums of the list rows and the unit rows with the printed totals."""
    national = national_1992()
    problems: list[str] = []
    votes = sum(value for name, value in lists.items() if name != "Skupaj")
    if votes != lists["Skupaj"] or votes != national["valid"]:
        problems.append(f"lists sum to {votes:,}, printed {lists['Skupaj']:,} and {national['valid']:,}")
    whole, units = turnout[0], turnout[1:]
    got = sum(unit.registered for unit in units)
    if got != whole.registered or got != national["registered"]:
        problems.append(f"registered: units {got:,}, printed {whole.registered:,} and {national['registered']:,}")
    if whole.cast != national["cast"]:
        problems.append(f"cast: table {whole.cast:,}, text {national['cast']:,}")
    if problems:
        raise ValueError(f"1992: {problems}")
    return {
        "lists": len(lists) - 1,
        "valid": votes,
        "registered": whole.registered,
        "cast": whole.cast,
        # The units' Glasovalo figures sum to more than the national row the same column prints.
        "units_voted": sum(unit.cast for unit in units),
    }


METHOD_1992 = (
    "Published by the State Election Commission (Državna volilna komisija) on its archived results site for the 1992 "
    "election. The site prints each list's votes for Slovenia as a whole, and the registered voters and ballots cast "
    "of each of the 8 electoral units, and nothing finer, so the lists' votes are stored as the declared figures of "
    "this national contest, and each electoral unit is a reporting unit that equals its electoral unit area and "
    "holds its registered voters and the voters the site's Glasovalo column prints. The units' voters sum to 2,648 "
    "more than the 1,277,604 the same column prints for Slovenia, which equals the ballots cast the site states, "
    "so the national row is stored as declared voters and ballots cast and the unit figures as printed. The printed "
    "valid ballots are Slovenia's only valid figure, and invalid ballots are not printed. The seats each list "
    "won are shown on the site only as a picture of the chamber, so they are not stored. The 88 deputies chosen by "
    "list were elected in the 8 electoral units; the two seats of the Italian and Hungarian national communities "
    "were elected separately and are not here. Votes for the commission's group of independent candidates are one "
    "line, as printed."
)


def election_1992(lists: dict[str, int], turnout: list[Turnout]) -> Package:
    p = Package(
        slug_1992(),
        SOURCE,
        "The 1992 Slovenian National Assembly election by electoral unit",
        depends=["si.dvk/geo", "si.dvk/registry", SYSTEMS_KEY],
    )
    p.external.add(SLOVENIA)
    for page in PAGES_1992.values():
        p.artifact(raw.cached(SOURCE, url(page)), DVK_SOURCE)
    national = national_1992()
    election_id = key("election", "dz", DAY_1992)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="general",
        date_from=DAY_1992,
        date_to=DAY_1992,
        status="held",
    )
    p.name(election_id, "1992 Slovenian parliamentary election", "en", "official")
    p.name(election_id, "Volitve v Državni zbor 1992", "sl", "native", False)
    p.add("slug", path=f"si/{DAY_1992}-general", entity_id=election_id, is_current=True)
    contest = key("contest", DAY_1992, "national")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=CHAMBER_BODY,
        district_area_id=None,
        magnitude=88,
        round_number=1,
        electoral_system_id=SYSTEM_1992,
        default_result_kind="counted",
    )
    p.name(contest, "Slovenia (list votes)", "en", "official")
    p.name(contest, "Slovenija (glasovi za liste)", "sl", "native", False)
    p.add("slug", path=f"si/{DAY_1992}-general/slovenia", entity_id=contest, is_current=True)
    result_set = key("result_set", f"{DAY_1992}:national")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DVK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD_1992, lang="en")
    for measure, value in (
        (2, national["registered"]),
        (14, turnout[0].cast),
        (3, national["cast"]),
        (4, national["valid"]),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for position, (name, votes) in enumerate(((n, v) for n, v in lists.items() if n != "Skupaj"), start=1):
        party = key("party", DAY_1992, name)
        p.add("party", id=party, kind="party", polity_id=SLOVENIA)
        p.name(party, title(name), "sl", "official")
        option = key("option", DAY_1992, name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party,
            ballot_position=position,
            ballot_label=title(name),
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
    sets: list[str] = []
    unit_ids: list[str] = []
    measures: list[int] = []
    values: list[int] = []
    for unit in turnout[1:]:
        unit_id = key("unit", f"{DAY_1992}:{unit.unit}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=SLOVENIA,
            scheme_code="si.ve",
            code=f"{DAY_1992}:{unit.unit}",
            area_relation="equals",
            area_id=area("si.ve", str(unit.unit)),
            kind="aggregate",
        )
        p.name(unit_id, f"{unit.unit}. volilna enota ({UNIT_NAMES[unit.unit]})", "sl", "official")
        for measure, value in ((2, unit.registered), (14, unit.cast)):
            sets.append(result_set)
            unit_ids.append(unit_id)
            measures.append(measure)
            values.append(value)
    p.accept(
        "accounting.declared",
        "the commission prints each list's votes and the valid ballots for Slovenia only, so the declared list "
        "votes, ballots cast, and valid ballots have no unit values to sum, and the units' printed voters sum to "
        "2,648 more than the national row of the same column; the unit rows hold the registered voters, which sum "
        "to the printed national figure, and the voters as printed",
    )
    _frame(p, sets, unit_ids, [None] * len(values), measures, values)
    return p


def _frame(
    p: Package,
    sets: list[str],
    unit_ids: list[str],
    option_ids: list[str | None],
    measures: list[int],
    values: list[int],
) -> None:
    size = len(values)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": unit_ids,
                "option_id": option_ids,
                "channel": [0] * size,
                "measure": measures,
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": values,
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )


# National communities ------------------------------------------------------------------------------------------------


@dataclass
class Community:
    unit: int
    name: str
    candidates: list[tuple[str, int]] = field(default_factory=list)
    """Each candidate with the points the commission printed, in the order printed."""
    registered: int | None = None
    voters: int | None = None
    cast: int | None = None
    invalid: int | None = None
    valid: int | None = None
    elected: str | None = None

    @property
    def points(self) -> dict[str, int]:
        return dict(self.candidates)


def _int(value: str) -> int:
    return int(value.replace(".", ""))


def _new(unit: int) -> Community:
    return Community(unit, KINDS[unit][0])


def communities(day: date) -> list[Community]:
    page = text(raw.cached(SOURCE, url(COMMUNITIES[day])).path)
    found = {2000: _communities_2000, 2004: _communities_2004, 2008: _communities_2008}[day.year](page)
    if [community.unit for community in found] != [9, 10]:
        raise LookupError(f"{day}: found communities {[community.unit for community in found]}")
    for community in found:
        top = max(community.candidates, key=lambda candidate: candidate[1])
        community.elected = top[0]
        if sum(1 for _, points in community.candidates if points == top[1]) != 1:
            raise ValueError(f"{day} {community.name}: the top points are tied")
    return found


def _communities_2000(page: str) -> list[Community]:
    parser = _Tables()
    parser.feed(page)
    found: list[Community] = []
    names = re.findall(r"(MADŽARSKA|ITALIJANSKA)\s+NARODNA\s+SKUPNOST", clean(re.sub(r"<[^>]+>", " ", page)))
    boards = [table for table in parser.tables if table and table[0][:1] == ["Kandidat"]]
    if len(names) != 2 or len(boards) != 2:
        raise LookupError(f"found {names} and {len(boards)} boards")
    for label, board in zip(names, boards, strict=True):
        community = _new(10 if label == "MADŽARSKA" else 9)
        community.candidates = [(clean(row[0]), number(row[1])) for row in board[1:]]
        found.append(community)
    return sorted(found, key=lambda community: community.unit)


def _communities_2004(page: str) -> list[Community]:
    plain = re.sub(r"<[^>]+>", " ", page)
    found: list[Community] = []
    for unit, chunk in (
        (9, re.search(r"9\. VOLILNA ENOTA.*?(?=10\. VOLILNA ENOTA)", plain, re.DOTALL)),
        (10, re.search(r"10\. VOLILNA ENOTA.*", plain, re.DOTALL)),
    ):
        if chunk is None:
            raise LookupError(f"2004 unit {unit} not found")
        body = clean(chunk.group(0))
        community = _new(unit)
        enrolled = re.search(r"vpisanih: (\d+) volivcev", body)
        certificates = re.search(r"(?:s potrdilom|s potrdili) (?:so |je )?glasovali?:? (\d+)", body)
        counted = {
            "voters": r"SKUPAJ GLASOVALO: (\d+) volivcev",
            "cast": r"Oddanih glasovnic je bilo:? (\d+)",
            "invalid": r"Neveljavnih glasovnic je bilo:? (\d+)",
            "valid": r"Veljavnih glasovnic je bilo:? (\d+)",
        }
        for name, pattern in counted.items():
            match = re.search(pattern, body)
            if match is None:
                raise LookupError(f"2004 unit {unit}: no {name}")
            setattr(community, name, int(match.group(1)))
        if enrolled is None or certificates is None:
            raise LookupError(f"2004 unit {unit}: no roll")
        community.registered = int(enrolled.group(1)) + int(certificates.group(1))
        if unit == 9:
            single = re.search(
                r"PREDNOSTNI VRSTNI RED KANDIDATA: (.+?) je označen kot prvi ([\d.]+) krat, kar znaša", body
            )
            if single is None:
                raise LookupError("2004 unit 9: no candidate")
            community.candidates = [(clean(single.group(1)), _int(single.group(2)))]
        else:
            for match in re.finditer(r"\d\. ([^\d]+?) a\)\. .*?in je (?:dobil|dobila): ([\d.]+) točk", body):
                community.candidates.append((clean(match.group(1)), _int(match.group(2))))
            if len(community.candidates) != 5:
                raise LookupError(f"2004 unit 10: found {len(community.candidates)} candidates")
        found.append(community)
    return found


def _communities_2008(page: str) -> list[Community]:
    plain = clean(re.sub(r"<[^>]+>", " ", page))
    found: list[Community] = []
    for unit, chunk in (
        (9, re.search(r"IX\. VOLILNA ENOTA.*?(?=(?<!I)X\. VOLILNA ENOTA)", plain)),
        (10, re.search(r"(?<!I)X\. VOLILNA ENOTA.*", plain)),
    ):
        if chunk is None:
            raise LookupError(f"2008 unit {unit} not found")
        body = chunk.group(0)
        community = _new(unit)
        roll = re.search(
            r"vpisanih\s+(\d+) volivcev\. S potrdil\w+ pristojnega organa \w+ glasoval\w* (\d+) voliv", body
        )
        if roll is None:
            raise LookupError(f"2008 unit {unit}: no roll")
        community.registered = int(roll.group(1)) + int(roll.group(2))
        for name, pattern in {
            "voters": r"Glasovalo je:? (\d+) volivcev",
            "cast": r"Število oddanih glasovnic: (\d+)",
            "invalid": r"Število neveljavnih glasovnic: (\d+)",
            "valid": r"Število veljavnih glasovnic: (\d+)",
        }.items():
            match = re.search(pattern, body)
            if match is None:
                raise LookupError(f"2008 unit {unit}: no {name}")
            setattr(community, name, int(match.group(1)))
        board = re.search(r"število točk:(.*?)Za poslanca", body)
        if board is None:
            raise LookupError(f"2008 unit {unit}: no points")
        for match in re.finditer(r"([^\d(]+?):? ([\d.]+) točk \([\d,]+ ?%\)", board.group(1)):
            community.candidates.append((clean(match.group(1)), _int(match.group(2))))
        if len(community.candidates) != 5:
            raise LookupError(f"2008 unit {unit}: found {len(community.candidates)} candidates")
        found.append(community)
    return found


def method_communities(day: date, community: Community) -> str:
    text = (
        "Published by the State Election Commission (Državna volilna komisija) on its archived results site for the "
        f"{day.year} election. The {KINDS[community.unit][0]} national community elects one deputy of the National "
        f"Assembly in its own electoral unit, the {'ninth' if community.unit == 9 else 'tenth'}, from a special roll "
        "of its members. Voters rank the candidates, a first place earns the most points, and the candidate with "
        "the most points is elected, so the points stored here are points, not votes, and they sum to more than the "
        "valid ballots. "
    )
    if day.year == 2000:
        text += (
            "The 2000 site prints the candidates' points only, as the commission's unofficial count, which was never "
            "replaced by the official one, so these are stored as a preliminary tabulation with no ballot figures. "
        )
    else:
        text += (
            "The registered voters are the special roll plus the voters holding a certificate of the competent "
            "authority, and the voters are all who voted. The unit has no area of its own, so its reporting unit "
            "has none. "
        )
    return text + "The district packages of the same election hold the other 88 seats."


def election_communities(day: date, found: list[Community]) -> Package:
    election_id = key("election", "dz", day)
    p = Package(
        slug_communities(day),
        SOURCE,
        f"The {day.year} Slovenian National Assembly seats of the Italian and Hungarian national communities",
        depends=["si.dvk/geo", "si.dvk/registry", f"si.dvk/dz-{day}"],
    )
    p.external.add(SLOVENIA)
    p.artifact(raw.cached(SOURCE, url(COMMUNITIES[day])), DVK_SOURCE)
    stage = STAGES[day]
    for community in found:
        english, native, adjective = KINDS[community.unit]
        label = f"{english} national community"
        contest = key("contest", day, "community", community.unit)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=CHAMBER_BODY,
            district_area_id=None,
            magnitude=1,
            round_number=1,
            electoral_system_id=None,
            default_result_kind="counted",
        )
        p.name(contest, f"{label} seat", "en", "official")
        p.name(contest, f"Poslanec {native} narodne skupnosti", "sl", "native", False)
        p.add(
            "slug",
            path=f"si/{day}-general/{changeset.slug(label)}",
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", f"{day}:community:{community.unit}")
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage=stage,
            kind="counted",
            source_id=DVK_SOURCE,
            status="active",
            is_default=True,
            completeness=None if stage == "preliminary" else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=method_communities(day, community),
            lang="en",
        )
        unit_id = key("unit", f"{day}:community:{community.unit}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=SLOVENIA,
            scheme_code="si.ve",
            code=f"{day}:{community.unit}",
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        roman = "IX" if community.unit == 9 else "X"
        p.name(unit_id, f"{roman}. volilna enota ({adjective} narodna skupnost)", "sl", "official")
        sets: list[str] = []
        unit_ids: list[str] = []
        option_ids: list[str | None] = []
        measures: list[int] = []
        values: list[int] = []
        for measure, value in (
            (2, community.registered),
            (14, community.voters),
            (3, community.cast),
            (5, community.invalid),
            (4, community.valid),
        ):
            if value is not None:
                sets.append(result_set)
                unit_ids.append(unit_id)
                option_ids.append(None)
                measures.append(measure)
                values.append(value)
        for position, (name, points) in enumerate(community.candidates, start=1):
            shown = person_name(name)
            person = key("person", f"dz:{day.year}:community:{community.unit}:{shown}")
            option = key("option", day, "community", community.unit, position)
            candidacy = key("candidacy", day, "community", community.unit, position)
            p.add("person", id=person)
            p.name(person, shown, "sl", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=shown,
            )
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=shown,
            )
            sets.append(result_set)
            unit_ids.append(unit_id)
            option_ids.append(option)
            measures.append(1)
            values.append(points)
            if name == community.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=1)
        _frame(p, sets, unit_ids, option_ids, measures, values)
    p.accept(
        "accounting.votes_valid",
        "the candidates' stored values are the points of a ranked ballot, not votes, so they sum to more than the "
        "valid ballots the commission prints; stored as printed",
    )
    return p
