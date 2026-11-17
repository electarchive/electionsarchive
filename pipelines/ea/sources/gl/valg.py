"""Greenland's elections from valg.gl, the Government of Greenland's election results site.

The site (a Blazor Server application) shows, for each election since 2005, the votes of every party and candidate at
every polling place, the seats and the substitutes, and the national totals of ballots counted and registered voters.
Its pages exist only inside a live browser session, so `crawl.mjs` drives a headless browser through the election and
polling-place dropdowns and keeps the rendered tables of each election in one JSON file, which `fetch` stores in the
raw cache. Three kinds of election are held: the Inatsisartut (called the Landsting until 2009), the two seats that
Greenland sends to the Danish Folketing, and the municipal councils.

A party's row in a results table holds the votes cast for the party's list alone, and the rows under it hold the
personal votes of its candidates. The list's own total, which the site prints in its summary tables, is the sum of
the two.
"""

import functools
import html
import json
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "gl.valg"
SITE = "https://www.valg.gl"
GREENLAND = str(uuid5(package.NAMESPACE, "polity\x1fGL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRL"))
VOTES, REGISTERED, BALLOTS, PREFERENCE, LEGEND = 1, 2, 3, 7, 9
CRAWLER = Path(__file__).with_name("crawl.mjs")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
INATSISARTUT = key("body", "inatsisartut")
FOLKETING = key("body", "folketing")
SYSTEM = key("electoral_system", "list")
RULES = {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}}
SYSTEM_NOTE = (
    "Seats are allocated among the lists by the D'Hondt method on each list's votes, which are the votes for the list "
    "and the personal votes for its candidates added together. Within a list, the candidates with the most personal "
    "votes take its seats, and those who follow them become its substitutes."
)


@dataclass(frozen=True)
class Election:
    label: str  # the site's name for the election
    family: str  # inatsisartut, folketing, or local
    day: date
    precision: str = "day"

    @property
    def stamp(self) -> str:
        return self.day.isoformat() if self.precision == "day" else f"{self.day.year}-local"

    @property
    def url(self) -> str:
        return f"{SITE}/resultater#{self.label.replace(' ', '-')}"


ELECTIONS = [
    Election("Landstingsvalg 2005", "inatsisartut", date(2005, 11, 15)),
    Election("Kommunevalg 2005", "local", date(2005, 4, 5)),
    Election("Folketingsvalg 2007", "folketing", date(2007, 11, 13)),
    Election("Kommunevalg 2008", "local", date(2008, 1, 1), "year"),
    Election("Landstingsvalg 2009", "inatsisartut", date(2009, 6, 2)),
    Election("Folketingsvalg 2011", "folketing", date(2011, 9, 15)),
    Election("Landstingsvalg 2013", "inatsisartut", date(2013, 3, 12)),
    Election("Kommunevalg 2013", "local", date(2013, 4, 2)),
    Election("Landstingsvalg 2014", "inatsisartut", date(2014, 11, 28)),
    Election("Folketingsvalg 2015", "folketing", date(2015, 6, 18)),
    Election("Kommunevalg 2017", "local", date(2017, 4, 4)),
    Election("Landstingsvalg 2018", "inatsisartut", date(2018, 4, 24)),
    Election("Folketingsvalg 2019", "folketing", date(2019, 6, 5)),
    Election("Inatsisartutvalg 2021", "inatsisartut", date(2021, 4, 6)),
    Election("Kommunevalg 2021", "local", date(2021, 4, 6)),
    Election("Folketingsvalg 2022", "folketing", date(2022, 11, 1)),
    Election("Inatsisartutvalg 2025", "inatsisartut", date(2025, 3, 11)),
    Election("Kommunevalg 2025", "local", date(2025, 4, 1)),
    Election("Folketingsvalg 2026", "folketing", date(2026, 3, 24)),
]
NAMES = {
    "inatsisartut": "{year} Greenlandic parliamentary election",
    "folketing": "{year} Danish general election in Greenland",
    "local": "{year} Greenlandic municipal elections",
}
NATIVE = {
    "inatsisartut": "Inatsisartutvalg {year}",
    "folketing": "Folketingsvalg {year}",
    "local": "Kommunevalg {year}",
}
SLUGS = {"inatsisartut": "inatsisartut", "folketing": "folketing", "local": "local"}
METHOD = (
    "Counted at each polling place and entered by the municipalities into the Government of Greenland's election "
    "results site, valg.gl. The site shows the votes for each party's list alone and the personal votes of each "
    "candidate; a list's votes here are the two added together. It does not show blank or invalid ballots by "
    "polling place."
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{stamp}" for stamp in stamps())]


def stamps() -> list[str]:
    return sorted({e.stamp for e in ELECTIONS})


def fetch() -> None:
    missing = [e for e in ELECTIONS if e.url not in raw.keys(SOURCE)]
    for attempt in range(3):
        if not missing:
            return
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(["node", str(CRAWLER), folder, *(e.label for e in missing)], check=True, timeout=7200)
            again = []
            for e in missing:
                content = (Path(folder) / f"{e.label.replace(' ', '-')}.json").read_bytes()
                try:
                    reconcile(load(content))
                except ValueError as error:
                    print(f"{e.label}: {error}", flush=True)
                    again.append(e)
                    continue
                when = datetime.fromisoformat(json.loads(content)["retrieved_at"])
                raw.store(SOURCE, e.url, content, suffix=".json", retrieved_at=when, media_type="application/json")
        missing = again
    if missing:
        raise ValueError(f"the polling places of {', '.join(e.label for e in missing)} do not add up to the totals")


def reconcile(data: Crawl) -> None:
    """Check that the polling places add up to the party totals the site prints for every municipality and for the
    country. The site draws a table a moment after it is chosen, so a crawl that read one too early shows here."""
    stations = {name: results(page) for name, page in data.stations.items()}
    for scope, page in data.totals.items():
        printed = {line.party: line.votes for line in results(page)}
        found: dict[str, int] = {}
        for name in data.kommuner.get(scope, stations):
            for line in stations[name]:
                found[line.party] = found.get(line.party, 0) + line.votes
        if found != printed:
            raise ValueError(f"{data.election}, {scope}: polling places add up to {found}, the site prints {printed}")


def parse() -> None:
    geo().write()
    registry().write()
    for stamp in stamps():
        election(stamp).write()


def emit() -> None:
    package.run("load", packages())


@dataclass(frozen=True)
class Crawl:
    election: str
    overview: str  # the overview page's HTML
    mandates: str  # the seats and substitutes page's HTML
    totals: dict[str, str]  # the results table of the whole country ("Alle") and of each municipality
    kommuner: dict[str, list[str]]  # each municipality's polling places
    stations: dict[str, str]  # each polling place's results table


def load(content: bytes) -> Crawl:
    found = json.loads(content)
    return Crawl(
        found["election"], found["overview"], found["mandates"], found["totals"], found["kommuner"], found["stations"]
    )


@functools.cache
def crawl(label: str) -> Crawl:
    e = next(e for e in ELECTIONS if e.label == label)
    return load(raw.cached(SOURCE, e.url).path.read_bytes())


# Reading the rendered pages.


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", html.unescape(re.sub(r"<[^>]+>", "", text)))).strip()


def number(text: str) -> int:
    return int(clean(text).replace(".", "").replace("\xa0", ""))


@dataclass(frozen=True)
class Line:
    party: str  # the site's letters for the party
    name: str  # the party's name
    own: int  # votes for the list alone
    candidates: tuple[tuple[str, str, int], ...]  # an id that tells apart two candidates of one name, the name, votes

    @property
    def votes(self) -> int:
        return self.own + sum(n for _, _, n in self.candidates)


def results(page: str) -> list[Line]:
    """The party rows of a results table, each with the candidate rows under it."""
    start = page.find("<table")
    if start < 0:
        return []
    lines: list[Line] = []
    code = name = ""
    own = 0
    people: list[tuple[str, str, int]] = []
    for kind, row in re.findall(
        r'<tr class="th +(party|small)">(.*?)</tr>', page[start : page.find("</table>")], flags=re.DOTALL
    ):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        count = re.search(r"<span>([\d.]+)</span>", cells[1]) if len(cells) > 1 else None
        if count is None and kind == "small":
            raise ValueError(f"candidate row without votes: {row[:200]}")
        votes = 0 if count is None else number(count.group(1))
        if kind == "party":
            if code:
                lines.append(Line(code, name, own, tuple(people)))
            tag = re.search(r'<span class="party-logo[^"]*">(.*?)</span>(.*)', cells[0], flags=re.DOTALL)
            if tag is None:
                raise ValueError(f"party row without a code: {row[:200]}")
            code, name, own, people = clean(tag.group(1)), clean(tag.group(2)), votes, []
        else:
            who = clean(cells[0])
            same = sum(1 for _, name_, _ in people if name_ == who)
            people.append((who if not same else f"{who} #{same + 1}", who, votes))
    if code:
        lines.append(Line(code, name, own, tuple(people)))
    return lines


@dataclass(frozen=True)
class Member:
    district: str
    party: str
    name: str
    votes: int


def members(page: str) -> tuple[list[Member], list[Member]]:
    """The elected members and the substitutes the site lists, by district and party."""
    out: dict[str, list[Member]] = {"Mandatliste": [], "Stedfortræderliste": []}
    for table in re.findall(r"<table>.*?</table>", page, flags=re.DOTALL):
        heading = re.search(r"<th[^>]*>(.*?)</th>", table, flags=re.DOTALL)
        title = clean(heading.group(1)) if heading else ""
        district = code = ""
        for cls, row in re.findall(r'<tr class="([^"]*)">(.*?)</tr>', table, flags=re.DOTALL):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
            if "district" in cls.split():
                district = clean(cells[0])
            elif "party" in cls.split():
                tag = re.search(r'<span class="party-logo[^"]*">(.*?)</span>', cells[0], flags=re.DOTALL)
                code = clean(tag.group(1)) if tag else ""
                code = "?" if code == "Others" else code  # the seat list's own letters for independent candidates
            else:
                out[title].append(Member(district, code, clean(cells[0]), number(cells[1])))
    return out["Mandatliste"], out["Stedfortræderliste"]


def totals(page: str) -> tuple[int, int | None] | None:
    """The ballots counted and the registered voters the overview prints; the registered voters of the first
    municipal elections are printed as 0, which stands for unknown."""
    text = clean(page)
    found = re.search(r"Stemmer optalt ([\d.]+) ud af ([\d.]+)", text)
    if found is None:
        return None
    return number(found.group(1)), number(found.group(2)) or None


def notices(page: str) -> list[str]:
    """The paragraphs the authority prints above the seat lists, such as a correction."""
    return [
        clean(text)
        for text in re.findall(r'<p style="padding-bottom:1rem">(.*?)</p>', page, flags=re.DOTALL)
        if clean(text)
    ]


def overview_votes(page: str) -> dict[str, int]:
    """The party totals the overview page prints, by the site's party letters."""
    found: dict[str, int] = {}
    for row in re.findall(r"<tr><td colspan=\"2\"><span class=\"party-logo[^>]*>.*?</tr>", page, flags=re.DOTALL):
        letters = re.search(r"party-logo[^>]*>(.*?)</span>", row, flags=re.DOTALL)
        count = re.search(r'election-text"><span>([\d.]+)</span>', row)
        if letters is not None and count is not None:
            found[clean(letters.group(1))] = number(count.group(1))
    return found


# Places.

OLD_MUNICIPALITIES_END = date(2009, 1, 1)
QAASUITSUP_END = date(2018, 1, 1)


def slug(text: str) -> str:
    return changeset.slug(text)


def place_name(text: str) -> str:
    return clean(text)


def area(code: str) -> str:
    return key("area", "gl.kommune", code)


def municipalities() -> dict[str, str]:
    """The municipality names the site lists in any election, by area code."""
    found: dict[str, str] = {}
    for e in ELECTIONS:
        for name in crawl(e.label).kommuner:
            found.setdefault(slug(place_name(str(name))), place_name(str(name)))
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Greenland's municipalities as the Government of Greenland's election results site names them",
    )
    p.external |= {GREENLAND, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Naalakkersuisut, Government of Greenland",
        title="valg.gl: election results by polling place",
        url=f"{SITE}/",
        license="Official election results of Greenland, published for public use; reuse with the source acknowledged",
        license_url=f"{SITE}/om-valg",
        attribution="Government of Greenland, Department of Municipalities, valg.gl",
    )
    for e in ELECTIONS:
        p.artifact(raw.cached(SOURCE, e.url), SOURCE_ID)
    p.add("scheme", code="gl.kommune", polity_id=GREENLAND, kind="administrative", rank=1, noun="municipality")
    p.add("scheme", code="gl.afstemningssted", polity_id=GREENLAND, kind="precinct", rank=2, noun="polling place")
    spans = municipality_spans()
    for code, text in sorted(municipalities().items()):
        id = area(code)
        p.add("area", id=id, scheme_code="gl.kommune")
        since, until = spans[code]
        p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=until)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=since, valid_to=until)
        p.name(id, text, "da", "official")
    return p


def municipality_spans() -> dict[str, tuple[date | None, date | None]]:
    """When a municipality's code stops being valid. The 18 municipalities of the first elections were merged into
    four on 1 January 2009, and Qaasuitsup was split into Avannaata and Qeqertalik on 1 January 2018. The site names a
    municipality in the elections held while it existed, and in later ones for the polling places it now covers."""
    last: dict[str, int] = {}
    for e in ELECTIONS:
        for name in crawl(e.label).kommuner:
            code = slug(place_name(name))
            last[code] = max(last.get(code, 0), e.day.year)
    spans: dict[str, tuple[date | None, date | None]] = {}
    for code, year in last.items():
        until = OLD_MUNICIPALITIES_END if year <= 2005 else QAASUITSUP_END if year < 2017 else None
        spans[code] = (None, until)
    return spans


# Parties and bodies.


def party_key(code: str, name: str) -> str:
    return key("party", code, name)


def parties() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for e in ELECTIONS:
        for scope in crawl(e.label).totals.values():
            for line in results(scope):
                if line.party != "?":
                    found[(line.party, line.name)] = party_key(line.party, line.name)
    return found


def council(code: str) -> str:
    return key("body", "council", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Inatsisartut, Greenland's seats in the Folketing, the municipal councils, their electoral system, and "
        "the parties on Greenlandic ballots from valg.gl",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GREENLAND, COUNTRY}
    for e in ELECTIONS:
        p.uses(raw.cached(SOURCE, e.url))
    p.add("body", id=INATSISARTUT, polity_id=GREENLAND, kind="unicameral")
    p.name(INATSISARTUT, "Parliament of Greenland", "en", "official")
    p.name(INATSISARTUT, "Inatsisartut", "kl", "native", False)
    p.name(INATSISARTUT, "Landstinget", "da", "historical", False)
    p.add("body", id=FOLKETING, polity_id=GREENLAND, kind="unicameral")
    p.name(FOLKETING, "Folketing (Greenland's seats)", "en", "official")
    p.name(FOLKETING, "Folketinget", "da", "native", False)
    for code, text in sorted(municipalities().items()):
        body = council(code)
        p.add("body", id=body, polity_id=GREENLAND, kind="council")
        p.name(body, f"{text} council", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=GREENLAND,
        valid_from=date(2005, 1, 1),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for (code, name), id in sorted(parties().items()):
        p.add("party", id=id, kind="party", polity_id=GREENLAND)
        p.add("external_id", entity_id=id, scheme="gl.valg.party", value=f"{code}:{name}")
        p.name(id, name, "da", "official")
        p.name(id, code, "da", "abbreviation", False)
    return p


# Elections.


def election(stamp: str) -> Package:
    held = [e for e in ELECTIONS if e.stamp == stamp]
    p = Package(
        f"{SOURCE}/{stamp}",
        SOURCE,
        "The " + " and ".join(NAMES[e.family].format(year=e.day.year) for e in held) + " by polling place from valg.gl",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {GREENLAND, COUNTRY}
    for e in held:
        p.uses(raw.cached(SOURCE, e.url))
    units: dict[str, str] = {}
    for e in held:
        build(p, e, units)
    return p


def build(p: Package, e: Election, units: dict[str, str]) -> None:
    data = crawl(e.label)
    year = e.day.year
    election = key("election", e.stamp, e.family)
    p.add(
        "election",
        id=election,
        polity_id=GREENLAND,
        kind="local" if e.family == "local" else "general",
        date_from=e.day if e.precision == "day" else date(year, 1, 1),
        date_to=e.day if e.precision == "day" else date(year, 12, 31),
        date_precision=e.precision,
        original_text=e.label,
        status="held",
    )
    p.name(election, NAMES[e.family].format(year=year), "en", "official")
    p.name(election, NATIVE[e.family].format(year=year), "da", "native", False)
    path = f"gl/{e.stamp}-{SLUGS[e.family]}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    stations = data.stations
    of_kommune = {station: place_name(kommune) for kommune, found in data.kommuner.items() for station in found}
    for station in stations:
        if station not in units:
            units[station] = key("unit", e.stamp, station)
            home = of_kommune.get(station)
            p.add(
                "reporting_unit",
                id=units[station],
                polity_id=GREENLAND,
                scheme_code="gl.afstemningssted",
                code=f"{e.stamp}:{station.strip()}",
                area_relation="within",
                area_id=area(slug(home)) if home else COUNTRY,
                kind="standard",
            )
    listed, _ = members(data.mandates)
    for n, text in enumerate(notices(data.mandates)):
        p.add(
            "note",
            id=key("note", election, "notice", n),
            entity_id=election,
            kind="caveat",
            body_md=f"The authority's results site prints this notice (in Danish): {text}",
            lang="en",
        )
    if e.family == "local":
        scopes = {place_name(k): k for k in data.kommuner}
    else:
        scopes = {"Grønland": "Alle"}
    overview = totals(data.overview)
    reconcile(data)
    printed = {line.party: line.votes for line in results(data.totals["Alle"])}
    if overview_votes(data.overview) != printed:
        raise ValueError(f"{e.label}: the overview and the results page print different party totals")
    for district, scope in scopes.items():
        contest(p, e, election, path, district, scope, data, units, of_kommune, listed, overview)
    if e.family == "local" and overview is not None:
        p.add(
            "note",
            id=key("note", election, "overview"),
            entity_id=election,
            kind="context",
            body_md=f"The site's overview counts {overview[0]:,} ballots"
            + ("" if overview[1] is None else f" from {overview[1]:,} registered voters")
            + " across all the municipal councils.",
            lang="en",
        )


def contest(
    p: Package,
    e: Election,
    election: str,
    path: str,
    district: str,
    scope: str,
    data: Crawl,
    units: dict[str, str],
    of_kommune: dict[str, str],
    listed: list[Member],
    overview: tuple[int, int | None] | None,
) -> None:
    code = slug(district)
    here = [m for m in listed if m.district == district]
    contest = key("contest", e.stamp, e.family, code)
    stations = data.stations
    if e.family == "local":
        body, area_id = council(code), area(code)
        in_scope = [s for s in stations if of_kommune.get(s) == district]
    else:
        body = INATSISARTUT if e.family == "inatsisartut" else FOLKETING
        area_id = COUNTRY
        in_scope = list(stations)
    seats = len(here)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=body,
        district_area_id=area_id,
        magnitude=seats or None,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    title = district if e.family == "local" else NAMES[e.family].format(year=e.day.year)
    p.name(contest, title, "da" if e.family == "local" else "en", "official")
    if e.family == "local":
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
    result_set = key("result_set", e.stamp, e.family, code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[tuple[str, str], str] = {}
    lines: dict[tuple[str, str, str], str] = {}
    persons: dict[tuple[str, str], str] = {}
    published: dict[str, list[Line]] = {station: results(stations[station]) for station in in_scope}
    named: dict[str, str] = {}
    for station in in_scope:
        for line in published[station]:
            named[line.party] = line.name
    for letters, name in sorted(named.items()):
        option = options[(letters, "")] = key("option", e.stamp, e.family, code, letters, name)
        if letters == "?":
            p.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label=name)
        else:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_key(letters, name),
                ballot_label=name,
            )
    for station in in_scope:
        for line in published[station]:
            for ident, person_name, _ in line.candidates:
                if (line.party, ident) in persons:
                    continue
                tag = f"{e.stamp}:{e.family}:{code}:{line.party}:{ident}"
                person = persons[(line.party, ident)] = key("person", tag)
                p.add("person", id=person)
                p.name(person, person_name, "da", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", tag),
                    person_id=person,
                    option_id=options[(line.party, "")],
                    role="list_member",
                    ballot_name=person_name,
                    member_of_party_id=None if line.party == "?" else party_key(line.party, line.name),
                    is_independent=line.party == "?",
                )
                lines[(line.party, ident, "")] = key("option", tag)
                p.add(
                    "contest_option",
                    id=lines[(line.party, ident, "")],
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=options[(line.party, "")],
                    ballot_label=person_name,
                )
    for station in in_scope:
        unit = units[station]
        for line in published[station]:
            for option, measure, value in (
                (options[(line.party, "")], VOTES, line.votes),
                (options[(line.party, "")], LEGEND, line.own),
                *((lines[(line.party, ident, "")], PREFERENCE, n) for ident, _, n in line.candidates),
            ):
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
    published_totals = results(data.totals[scope])
    for line in published_totals:
        option = options[(line.party, "")]
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=line.votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=LEGEND, value=line.own)
        for ident, _, n in line.candidates:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=lines[(line.party, ident, "")],
                measure=PREFERENCE,
                value=n,
            )
    if e.family != "local" and overview is not None:
        p.add("declared", result_set_id=result_set, option_id=None, measure=BALLOTS, value=overview[0])
        if overview[1] is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=overview[1])
        p.accept(
            "accounting.declared",
            "the site prints the ballots counted and the registered voters only as national totals, not for each "
            "polling place, so there are no polling-place values to add up to them",
        )
    won: dict[str, int] = {}
    personal = {(line.party, ident): (who, n) for line in published_totals for ident, who, n in line.candidates}
    for member in here:
        letters = member.party
        # The seat list adds the candidate's place to a name that two candidates share, as "Hansen, Asmus - Narsaq".
        who = [
            ident
            for (party, ident), (name, n) in personal.items()
            if party == letters and name in (member.name, member.name.split(" - ")[0].strip()) and n == member.votes
        ]
        if len(who) != 1:
            raise ValueError(f"{e.label} {district}: cannot place elected member {member.name} ({letters})")
        won[letters] = won.get(letters, 0) + 1
        tag = f"{e.stamp}:{e.family}:{code}:{letters}:{who[0]}"
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[(letters, "")],
            candidacy_id=key("candidacy", tag),
            kind="elected",
        )
    for letters, n in won.items():
        p.add("outcome", result_set_id=result_set, option_id=options[(letters, "")], kind="won", seats=n)
