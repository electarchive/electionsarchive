"""Direct elections of mayors (Bürgermeister, Oberbürgermeister) in Germany, one Land at a time.

North Rhine-Westphalia is built from the Landeswahlleiterin's per-municipality result pages
(wahlergebnisse.nrw), which carry the registered voters, voters, invalid and valid votes, and every candidate's votes
in the first ballot and in the runoff (Stichwahl). Each page is one municipality; the pages for 2015, 2020, and 2025
are cached and parsed here.
"""

import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "de.buergermeister"
BASE = "https://www.wahlergebnisse.nrw/kommunalwahlen"
NRW = "01a13bb5-c563-741b-83f7-4b74611ca2d9"
NRW_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fnrw"))
SCHEME = "de.buergermeisterwahl"
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1ftwo_round"))
YEARS = (2015, 2020, 2025)
GENERIC = {
    "einzelbew.",
    "einzelbewerber",
    "einzelbewerberin",
    "einzelbewerber/-in",
    "wählergr.",
    "wählergruppe",
    "wgr",
    "ezb",
    "wg",
}
INDEPENDENT = {"einzelbew.", "einzelbewerber", "einzelbewerberin", "einzelbewerber/-in", "ezb"}
METHOD = (
    "Counted by the Landeswahlleiterin of North Rhine-Westphalia and published for each municipality. Registered "
    "voters are those entitled to vote, voters are those who took part, and valid votes are the valid votes for "
    "candidates. A candidate with more than half of the valid votes is elected in the first ballot; otherwise the two "
    "leading candidates meet in a runoff two weeks later, and the one with more votes is elected. The source "
    "prints each candidate's party or the label of a voters' group or independent candidacy."
)
AREAS = Path(__file__).with_name("buergermeister_nw_areas.tsv")


class Round(NamedTuple):
    day: date
    registered: int | None
    voters: int | None
    invalid: int | None
    valid: int | None
    candidates: list[tuple[str, int]]


class Page(NamedTuple):
    ags: str
    name: str
    rounds: list[Round]
    winner: str | None
    url: str


def areas() -> dict[str, str]:
    lines = AREAS.read_text().splitlines()[1:]
    return dict(line.split("\t") for line in lines)


def indexes(year: int) -> list[str]:
    return [f"{BASE}/{year}/index_bm.shtml", f"{BASE}/{year}/index_obb_lr.shtml"]


LINK = re.compile(
    r'href="(aktuell[\\/](?:b(\d{6})kw\d+\.shtml|(\d{6})_(?:KREISANGEHOERIGE_GEMEINDE|KREISFREIE_STADT)_XML_DATA\.html))"'
)


def pages(year: int) -> dict[str, str]:
    """The URL of each municipality's page, by its AGS."""
    known = areas()
    found: dict[str, str] = {}
    for index in indexes(year):
        text = raw.cached(SOURCE, index).path.read_text(errors="replace")
        for match in LINK.finditer(text):
            code = "05" + (match.group(2) or match.group(3))
            if code in known:
                found[code] = f"{BASE}/{year}/" + match.group(1).replace("\\", "/")
    return found


def fetch() -> None:
    for year in YEARS:
        for index in indexes(year):
            raw.fetch(SOURCE, index, suffix=".html")
        raw.fetch_many(SOURCE, sorted(pages(year).values()), suffix=".html")


class _Table(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)


def _count(text: str) -> int | None:
    text = text.strip()
    return int(text.replace(".", "")) if re.fullmatch(r"\d{1,3}(?:\.\d{3})*|\d+", text) else None


def _day(text: str) -> date:
    d, m, y = text.split(".")
    return date(int(y), int(m), int(d))


def page(url: str, source: str = SOURCE) -> Page:
    parser = _Table()
    parser.feed(raw.cached(source, url).path.read_text(errors="replace"))
    flat = " ".join(" ".join(parser.text).split())
    code = re.search(r"(?:/b|/)(\d{6})(?:kw|_)", url)
    if code is None:
        raise ValueError(f"{url} carries no municipality code")
    ags = "05" + code.group(1)
    named = re.search(r"Ergebnis für:\s*(?:\d{6}\s+)?(.+?)(?:\s*\(\d{6}\))?\s+zur Übersicht", flat)
    if named is None:
        raise ValueError(f"{url} names no municipality")
    head = next(row for row in parser.rows if row[0] == "Merkmal")
    days = [_day(cell) for cell in head if re.fullmatch(r"\d\d\.\d\d\.\d{4}", cell)]
    keys = {
        "Wahlberechtigte insgesamt": "registered",
        "Wähler/-innen": "voters",
        "Wähler/-innen insgesamt": "voters",
        "Ungültige Stimmen": "invalid",
        "Gültige Stimmen": "valid",
    }
    figures: dict[str, list[int | None]] = {}
    candidates: list[list[tuple[str, int]]] = [[] for _ in days]
    listing = False
    for row in parser.rows:
        label, cells = row[0], row[1:]
        if label.startswith("davon entfielen"):
            listing = True
            continue
        if listing:
            if len(cells) < 2 or label.startswith("Download"):
                continue
            for index in range(len(days)):
                value = _count(cells[2 * index]) if 2 * index < len(cells) else None
                if value is not None:
                    candidates[index].append((label, value))
        elif label in keys:
            figures[keys[label]] = [
                _count(cells[2 * index]) if 2 * index < len(cells) else None for index in range(len(days))
            ]
    winner = re.search(r"gewählt:\s*(.+?)\s+(?:\d\d\.\d\d\.\d{4}|mit )", flat)
    rounds = [
        Round(
            day,
            figures.get("registered", [None] * len(days))[index],
            figures.get("voters", [None] * len(days))[index],
            figures.get("invalid", [None] * len(days))[index],
            figures.get("valid", [None] * len(days))[index],
            candidates[index],
        )
        for index, day in enumerate(days)
    ]
    elected = re.sub(r"\s+,", ",", winner.group(1)) if winner else None
    if elected is not None and rounds and rounds[-1].candidates:
        top = max(rounds[-1].candidates, key=lambda item: item[1])[0]
        if re.sub(r"\s+,", ",", top) != elected:
            raise ValueError(f"{url}: the page names {elected} but {top} leads the last round")
    return Page(ags, named.group(1).split(",")[0].strip(), rounds, elected, url)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})
    return re.sub(r"[^a-z0-9]+", "-", text.translate(table).lower()).strip("-")


def polity(ags: str) -> str:
    return key("polity", ags)


def office(ags: str) -> str:
    return key("office", "mayor", ags)


def label(text: str) -> tuple[str, str | None]:
    found = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", text)
    return (found.group(1), found.group(2)) if found else (text, None)


def party(name: str) -> str | None:
    if name.lower() in GENERIC or "/" in name:
        return None
    return key("party", name)


def _all() -> dict[int, dict[str, Page]]:
    return {year: {ags: page(url) for ags, url in sorted(pages(year).items())} for year in YEARS}


def registry(found: dict[int, dict[str, Page]]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "North Rhine-Westphalian municipalities as polities with their mayoralties, the runoff system, and the "
        "parties and groups that put up mayoral candidates",
    )
    p.external |= {NRW, *areas().values()}
    p.add(
        "source",
        id=NRW_SOURCE,
        kind="official_body",
        publisher="Landeswahlleiterin des Landes Nordrhein-Westfalen",
        title="Wahlergebnisse in NRW: Kommunalwahlen",
        url="https://www.wahlergebnisse.nrw/kommunalwahlen/",
        license="Datenlizenz Deutschland, Zero, Version 2.0",
        license_url="https://www.govdata.de/dl-de/zero-2-0",
        attribution="Landeswahlleiterin NRW, wahlergebnisse.nrw",
    )
    for year in YEARS:
        for index in indexes(year):
            p.artifact(raw.cached(SOURCE, index), NRW_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=NRW,
        valid_from=date(2014, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")
    p.add("scheme", code=SCHEME, polity_id=NRW, kind="electoral_district", rank=1, noun="mayoral electorate")
    names: dict[str, str] = {}
    for year in YEARS:
        for ags, found_page in found[year].items():
            names[ags] = found_page.name
    for ags, text in sorted(names.items()):
        id = polity(ags)
        p.add("polity", id=id, kind="municipality", iso_code=None)
        p.add("polity_parent", polity_id=id, parent_id=NRW)
        p.add("polity_territory", polity_id=id, area_id=areas()[ags])
        p.name(id, text, "de", "official")
        p.add("external_id", entity_id=id, scheme="de.ags", value=ags)
        post = office(ags)
        p.add("office", id=post, polity_id=id, kind="mayor")
        p.name(post, f"Mayor of {text}", "en", "official")
        p.name(post, f"Bürgermeister von {text}", "de", "native", False)
    labels = {
        label(candidate)[1]
        for pages_ in found.values()
        for found_page in pages_.values()
        for round_ in found_page.rounds
        for candidate, _ in round_.candidates
    }
    for text in sorted(item for item in labels if item and party(item)):
        id = key("party", text)
        p.add("party", id=id, kind="party", polity_id=NRW)
        p.add("external_id", entity_id=id, scheme="de.buergermeister.party", value=text)
        p.name(id, text, "de", "official")
    return p


def election(year: int, found: dict[str, Page]) -> list[Package]:
    p = Package(
        f"{SOURCE}/nw-{year}",
        SOURCE,
        f"The {year} mayoral elections in North Rhine-Westphalia, first ballots and runoffs, by municipality",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NRW, *areas().values()}
    p.uses(raw.cached(SOURCE, indexes(year)[0]))
    p.uses(raw.cached(SOURCE, indexes(year)[1]))
    first: dict[date, str] = {}
    second: dict[date, str] = {}
    taken: set[tuple[str, str]] = set()
    for ags, found_page in sorted(found.items()):
        p.uses(raw.cached(SOURCE, found_page.url))
        previous: str | None = None
        for index, round_ in enumerate(found_page.rounds):
            number = index + 1
            pool = first if number == 1 else second
            if round_.day not in pool:
                id = pool[round_.day] = key("election", year, number, round_.day)
                path = f"de/{round_.day}-nw-mayoral" + ("" if number == 1 else "-runoff")
                p.add(
                    "election",
                    id=id,
                    polity_id=NRW,
                    kind="local" if number == 1 else "runoff",
                    date_from=round_.day,
                    date_to=round_.day,
                    status="held",
                )
                p.add("slug", path=path, entity_id=id, is_current=True)
                suffix = "" if number == 1 else " runoff"
                p.name(
                    id,
                    f"{round_.day:%B} {round_.day.day}, {round_.day.year} North Rhine-Westphalia mayoral elections{suffix}",
                    "en",
                    "official",
                )
                p.name(
                    id,
                    f"Bürgermeisterwahlen in Nordrhein-Westfalen am {round_.day:%d.%m.%Y}"
                    + ("" if number == 1 else ", Stichwahl"),
                    "de",
                    "native",
                    False,
                )
            election_id = pool[round_.day]
            election_path = f"de/{round_.day}-nw-mayoral" + ("" if number == 1 else "-runoff")
            contest = key("contest", ags, round_.day)
            unit = key("unit", ags, round_.day)
            result_set = key("result_set", ags, round_.day)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="office",
                office_id=office(ags),
                district_area_id=areas()[ags],
                magnitude=1,
                round_number=number,
                previous_round_id=previous,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
            )
            previous = contest
            text = found_page.name
            p.name(
                contest,
                f"{round_.day.year} mayoral election in {text}" + ("" if number == 1 else ", runoff"),
                "en",
                "official",
            )
            tail = slug(text)
            if (election_path, tail) in taken:
                tail = f"{tail}-{ags}"
            taken.add((election_path, tail))
            p.add("slug", path=f"{election_path}/{tail}", entity_id=contest, is_current=True)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NRW,
                scheme_code=SCHEME,
                code=f"{round_.day}:{ags}",
                area_relation="equals",
                area_id=areas()[ags],
                kind="standard",
            )
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=NRW_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en"
            )
            for measure, value in ((2, round_.registered), (3, round_.voters), (5, round_.invalid), (4, round_.valid)):
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            top = max(votes for _, votes in round_.candidates)
            last = number == len(found_page.rounds)
            total = sum(votes for _, votes in round_.candidates)
            ranked = sorted(round_.candidates, key=lambda item: -item[1])
            for position, (printed, votes) in enumerate(round_.candidates, start=1):
                name, party_label = label(printed)
                option = key("option", ags, round_.day, printed)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(party_label) if party_label else None,
                    ballot_position=position,
                    ballot_label=printed,
                )
                person = key("person", ags, round_.day, printed)
                p.add("person", id=person)
                p.name(person, name, "de", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", ags, round_.day, printed),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    is_independent=(party_label or "").lower() in INDEPENDENT,
                )
                if last:
                    kind = "elected" if votes == top else "lost"
                elif total and top * 2 > total and votes == top:
                    kind = "elected"
                elif (ranked[0][0] == printed or ranked[1][0] == printed) and not (top * 2 > total):
                    kind = "advanced"
                else:
                    kind = "lost" if top * 2 > total else "eliminated"
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
    p.accept(
        "accounting.votes_valid",
        "A single candidate stands against nobody, and the source's valid votes then include the votes against "
        "that candidate, which it prints as valid votes without a line of their own, so the candidate's votes are "
        "below the valid total.",
    )
    return [p]


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/nw-{year}" for year in YEARS)]


def parse() -> None:
    found = _all()
    registry(found).write()
    for year in YEARS:
        for built in election(year, found[year]):
            built.write()


def emit() -> None:
    package.run("load", packages())
