"""Greek national elections 2004-2009 by electoral constituency, from the Ministry of Interior's results site.

The ministry's results site (run for it by its contractor) published each election's counts for the whole state and
for each of the 56 parliamentary constituencies; the European Parliament count adds a 57th unit for votes cast abroad.
No finer unit (municipality or polling station) is published, only the number of polling stations counted. The
parliamentary pages are the following archived sites:

- 2004: `voul2004/el`, one page per constituency, party, and the state seat allocation (unofficial figures).
- 2007 and 2009 parliamentary and 2009 European: `v2007`, `v2009`, `e2009`, each a set of `dyn/ep_<n>.js` data files
  (one per constituency) plus the national `epik.js`.

Each election is one package, `gr.ypes_national/<key>`, built on `gr.ypes_national/geo` (the constituencies) and
`gr.ypes_national/registry` (the parliament and the parties). A parliamentary election holds one contest per
constituency, with the seats won there, and one contest for the twelve state seats, whose votes are the national totals.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "gr.ypes_national"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
HOST = "https://ekloges-prev.singularlogic.eu"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ACCOUNTING = ((REGISTERED, "registered"), (VALID, "valid"), (INVALID, "invalid"), (BLANK, "blank"))
ABROAD = 57
STATE_SEATS = 12


def independents(name: str) -> bool:
    """The aggregate line for independent candidates, which is not a party."""
    return name.startswith("Ανεξ")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MINISTRY_SOURCE = key("source", SOURCE)
PARLIAMENT = key("body", "parliament")


def area(code: object) -> str:
    return key("area", "gr.electoral_district", code)


@dataclass(frozen=True)
class Election:
    key: str
    family: str
    day: date
    bundle: str
    names: str
    stations: bool = False


ELECTIONS = [
    Election("ps-2004", "parliament", date(2004, 3, 7), f"{HOST}/voul2004/el/", ""),
    Election(
        "ps-2007",
        "parliament",
        date(2007, 9, 16),
        f"{HOST}/v2007/dyn/",
        "https://web.archive.org/web/20250629045734id_/http://ekloges-prev.singularlogic.eu/v2007/stat/ep_party.js",
    ),
    Election(
        "ep-2009",
        "european",
        date(2009, 6, 7),
        f"{HOST}/e2009/dyn/",
        "https://web.archive.org/web/20260908033220id_/http://ekloges-prev.singularlogic.eu/e2009/stat/ep_party.js",
    ),
    Election(
        "ps-2009",
        "parliament",
        date(2009, 10, 4),
        f"{HOST}/v2009/dyn/",
        "https://web.archive.org/web/20261009071442id_/http://ekloges-prev.singularlogic.eu/v2009/stat/ep_party.js",
    ),
]
NAMES = {
    "parliament": ("parliamentary", "parliamentary election", "Βουλευτικές εκλογές"),
    "european": ("european-parliament", "European Parliament election", "Ευρωεκλογές"),
}
EN = [
    "Evros",
    "Rhodope",
    "Xanthi",
    "Drama",
    "Kavala",
    "Thessaloniki A",
    "Thessaloniki B",
    "Serres",
    "Chalkidiki",
    "Kilkis",
    "Pella",
    "Imathia",
    "Pieria",
    "Florina",
    "Kozani",
    "Kastoria",
    "Grevena",
    "Ioannina",
    "Arta",
    "Thesprotia",
    "Preveza",
    "Larissa",
    "Magnesia",
    "Trikala",
    "Karditsa",
    "Corfu",
    "Lefkada",
    "Kefalonia",
    "Zakynthos",
    "Aetolia-Acarnania",
    "Achaea",
    "Elis",
    "Phthiotis",
    "Evrytania",
    "Phocis",
    "Boeotia",
    "Euboea",
    "Athens A",
    "Athens B",
    "Piraeus A",
    "Piraeus B",
    "Attica",
    "Corinthia",
    "Argolis",
    "Arcadia",
    "Messenia",
    "Laconia",
    "Lesbos",
    "Chios",
    "Samos",
    "Cyclades",
    "Dodecanese",
    "Chania",
    "Rethymno",
    "Heraklion",
    "Lasithi",
]
METHOD = (
    "Counted by the Ministry of Interior and published on its results site for each electoral constituency. "
    "Registered voters are the electoral roll, valid votes are the ballots that count for a party or independent "
    "candidate, and blank ballots are counted apart from valid ones. Ballots cast are the sum of valid, invalid, and "
    "blank ballots. The site prints no figures below the constituency, only the number of polling stations counted."
)
SEAT_METHOD = (
    "Seats are those the site prints for the constituency after the national allocation. Of the 300 seats of the "
    "Hellenic Parliament, 288 are won in the 56 constituencies and 12 are state seats allocated on the national vote; "
    "the state seats are held in their own contest."
)
STATE_METHOD = (
    "The twelve state seats are allocated to parties on their votes in the whole country. The votes are the national "
    "totals the site prints, which are the sums of the constituency counts."
)
UNOFFICIAL = (
    "The results site presents these figures as unofficial and provisional, taken from its contractor's central "
    "system once every polling station had been incorporated."
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in ELECTIONS)]


def fetch() -> None:
    for e in ELECTIONS:
        for url in (e.bundle, e.names):
            if url:
                raw.cached(SOURCE, url)


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def _number(text: str) -> int | None:
    text = text.strip().replace(".", "").replace("\xa0", "")
    return int(text) if text not in ("", "-") else None


@dataclass
class Count:
    registered: int
    valid: int
    invalid: int
    blank: int
    stations: tuple[int, int]
    votes: dict[str, int] = field(default_factory=dict)
    seats: dict[str, int] = field(default_factory=dict)

    @property
    def cast(self) -> int:
        return self.valid + self.invalid + self.blank


@dataclass
class Data:
    national: Count
    units: dict[int, Count]
    state: dict[str, int]
    parties: dict[str, str]
    places: dict[int, str]
    spelling: dict[str, str] = field(default_factory=dict)


def _text(artifact: raw.Artifact) -> str:
    return artifact.path.read_text(encoding="utf-8")


def _pages(artifact: raw.Artifact, pattern: str) -> dict[str, str]:
    parts = re.split(pattern, _text(artifact))
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _js(text: str) -> object:
    body = re.sub(r"^\s*var\s+\w+\s*=\s*", "", text.strip()).rstrip(";").strip()
    body = re.sub(r"new Date\((\d+)\)", r"\1", body)
    body = re.sub(r"(?<=[{,])\s*([A-Za-z_]\w*|\d+)\s*:", r'"\1":', body)
    return json.loads(body)


def _vars(text: str) -> dict[str, object]:
    return {
        name: _js(f"var {name}={value}") for name, value in re.findall(r"(?m)^var (\w+)=(.*?);\s*$", text, re.DOTALL)
    }


def _names(url: str) -> tuple[dict[int, str], dict[str, str]]:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("cp1253")
    places = {
        int(n): descr.replace("\\'", "'").strip()
        for n, descr in re.findall(r"(?:^|[{\n])(\d+):\{nomos_id:\d+,descr:'(.*?)',counttm", text)
    }
    parties = {
        n: descr.replace("\\'", "'").strip()
        for n, descr in re.findall(r"(?:^|[{\n])(\d+):\{seq_id:\d+,major:\d+,descr:'(.*?)'", text)
    }
    return places, parties


def _js_data(e: Election) -> Data:
    pages = _pages(raw.cached(SOURCE, e.bundle), r"(?m)^/\* ===== (\S+) ===== \*/\n")
    folder = e.bundle
    top = _vars(pages[f"{folder}epik.js"])
    first = top["epik1"]
    assert isinstance(first, dict)
    votes = top["epik2"]
    assert isinstance(votes, dict)
    national = Count(
        first["gramenoi"], first["egkyra"], first["akyra"], first["leyka"], (first["counttm"], first["numtm"])
    )
    state: dict[str, int] = {}
    for party, row in votes.items():
        national.votes[party] = row["votes"]
        if row["edres"] + row["edresepik"]:
            national.seats[party] = row["edres"] + row["edresepik"]
        if row["edresepik"]:
            state[party] = row["edresepik"]
    units: dict[int, Count] = {}
    for n in range(1, ABROAD + 1):
        name = f"{folder}ep_{n}.js"
        if name not in pages:
            continue
        found = _vars(pages[name])
        head, rows = found["ep1"], found["ep2"]
        assert isinstance(head, dict) and isinstance(rows, list)
        unit = units[n] = Count(
            head["gramenoi"], head["egkyra"], head["akyra"], head["leyka"], (head["numtm"], head["numtm"])
        )
        for row in rows:
            party = str(row["party_id"])
            if row["votes"]:
                unit.votes[party] = row["votes"]
            if row["edres"]:
                unit.seats[party] = row["edres"]
    places, parties = _names(e.names)
    return Data(national, units, state, parties, places)


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in re.findall(r'<td align="right">([^<]*)</td>', row)]


def _count(page: str) -> Count:
    table = page.split('<tr class="oddRow">', 1)[1].split("</tr>", 1)[0]
    _, stations, registered, _, valid, invalid, blank = _cells(table)
    done, total = (_number(part) or 0 for part in stations.split("/"))
    return Count(
        _number(registered) or 0, _number(valid) or 0, _number(invalid) or 0, _number(blank) or 0, (done, total)
    )


def _rows(page: str) -> list[tuple[str, str, list[str]]]:
    rows = []
    for block in re.split(r'<tr height="17" class="(?:odd|even)Row">', page)[1:]:
        found = re.search(r'part_detl(\d+)\.htm">([^<]*)</a>', block)
        if found is not None:
            rows.append((found.group(1), " ".join(found.group(2).split()), _cells(block)))
    return rows


def _html_data(e: Election) -> Data:
    pages = _pages(raw.cached(SOURCE, e.bundle), r"(?m)^<!-- ===== (\S+) ===== -->\n")
    parties: dict[str, str] = {}
    places: dict[int, str] = {}
    units: dict[int, Count] = {}
    for n in range(1, ABROAD):
        page = pages[f"{e.bundle}pref_detl{n}.htm"]
        found = re.search(r"Εκλογική περιφέρεια ([^<]*)<", page)
        assert found is not None
        places[n] = " ".join(found.group(1).split())
        unit = units[n] = _count(page)
        for party, name, cells in _rows(page):
            parties[party] = name
            _share, votes, seats = cells[-3:] if len(cells) == 3 else cells[1:]
            if _number(votes):
                unit.votes[party] = _number(votes) or 0
            if _number(seats):
                unit.seats[party] = _number(seats) or 0
    page = pages[f"{e.bundle}cntr_detl.htm"]
    national = _count(page)
    for party, name, cells in _rows(page):
        parties[party] = name
        _, votes, seats = cells[-3:] if len(cells) == 3 else cells[1:]
        national.votes[party] = _number(votes) or 0
        if _number(seats):
            national.seats[party] = _number(seats) or 0
    state: dict[str, int] = {}
    for party, _, cells in _rows(pages[f"{e.bundle}cntr_seat.htm"]):
        total, first, second, third, equalized, at_large = cells[:6]
        parts = [_number(cell) or 0 for cell in (first, second, third, equalized)]
        if sum(parts) + (_number(at_large) or 0) != (_number(total) or 0):
            raise ValueError(f"{e.key}: party {party} seat stages do not add up")
        if _number(at_large):
            state[party] = _number(at_large) or 0
    return Data(national, units, state, parties, places)


@functools.cache
def data(key_: str) -> Data:
    e = next(e for e in ELECTIONS if e.key == key_)
    result = _html_data(e) if e.key == "ps-2004" else _js_data(e)
    _verify(e, result)
    return result


def _verify(e: Election, d: Data) -> None:
    """The constituency counts must add up to the national totals the site prints."""
    total = Count(0, 0, 0, 0, (0, 0))
    for n, unit in d.units.items():
        if unit.stations[0] != unit.stations[1]:
            raise ValueError(f"{e.key}: constituency {n} is not fully counted")
        total.registered += unit.registered
        total.valid += unit.valid
        total.invalid += unit.invalid
        total.blank += unit.blank
        for party, votes in unit.votes.items():
            total.votes[party] = total.votes.get(party, 0) + votes
        for party, seats in unit.seats.items():
            total.seats[party] = total.seats.get(party, 0) + seats
    off = {
        name: (getattr(total, name), getattr(d.national, name))
        for name in ("registered", "valid", "invalid", "blank")
        if getattr(total, name) != getattr(d.national, name)
    }
    off_votes = {
        party: (total.votes.get(party, 0), d.national.votes.get(party, 0))
        for party in set(total.votes) | set(d.national.votes)
        if total.votes.get(party, 0) != d.national.votes.get(party, 0)
    }
    print(
        f"{e.key}: {len(d.units)} units; national totals: accounting differences {off or 'none'}, "
        f"party vote differences {off_votes or 'none'}",
        flush=True,
    )


def _place_names() -> dict[int, tuple[str, str]]:
    places, _ = _names(ELECTIONS[-1].names)
    return {n: (greek, EN[n - 1]) for n, greek in places.items() if n <= len(EN)}


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=MINISTRY_SOURCE,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών",
        title="Εκλογικά αποτελέσματα (results site of the Ministry of Interior)",
        url=HOST + "/",
        license="Published by the Ministry of Interior for public use; no license is stated on the site",
        license_url=HOST + "/",
        attribution="Hellenic Republic, Ministry of Interior",
    )


def _artifacts(p: Package, wanted: list[Election], define: bool = False) -> None:
    """Cite the raw files; the geo package alone defines their source_artifact rows."""
    for e in wanted:
        for url in (e.bundle, e.names):
            if url:
                artifact = raw.cached(SOURCE, url)
                if define:
                    p.artifact(artifact, MINISTRY_SOURCE)
                else:
                    p.uses(artifact)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Greece's 56 parliamentary electoral constituencies as named in the Ministry of Interior's results",
    )
    p.external |= {GREECE, COUNTRY}
    add_source(p)
    _artifacts(p, ELECTIONS, True)
    p.add(
        "scheme",
        code="gr.electoral_district",
        polity_id=GREECE,
        kind="electoral_district",
        rank=1,
        noun="electoral constituency",
        code_system=None,
    )
    for n, (greek, english) in sorted(_place_names().items()):
        id = area(n)
        p.add("area", id=id, scheme_code="gr.electoral_district")
        p.add("area_code", area_id=id, code=str(n))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, greek, "el", "official")
        p.name(id, english, "en", "english", False)
    return p


def _party_names() -> dict[str, list[str]]:
    """Every party id with its names, newest election first."""
    found: dict[str, list[str]] = {}
    for e in reversed(ELECTIONS):
        for party, name in data(e.key).parties.items():
            names = found.setdefault(party, [])
            if name not in names:
                names.append(name)
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Hellenic Parliament and the parties named in the Ministry of Interior's 2004-2009 results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GREECE, COUNTRY}
    _artifacts(p, ELECTIONS)
    p.add("body", id=PARLIAMENT, polity_id=GREECE, kind="unicameral")
    p.name(PARLIAMENT, "Hellenic Parliament", "en", "official")
    p.name(PARLIAMENT, "Βουλή των Ελλήνων", "el", "native", False)
    for code, names in sorted(_party_names().items(), key=lambda item: int(item[0])):
        if independents(names[0]):
            continue
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=GREECE)
        p.add("external_id", entity_id=id, scheme="gr.ypes.party", value=code)
        for index, name in enumerate(names):
            p.name(id, name, "el", "official" if index == 0 else "historical", index == 0)
    return p


def _values(p: Package, rows: list[tuple[str, str, str | None, int, int]]) -> None:
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )


def _accounting(rows: list[tuple[str, str, str | None, int, int]], result_set: str, unit: str, count: Count) -> None:
    for measure, name in ACCOUNTING:
        rows.append((result_set, unit, None, measure, getattr(count, name)))
    rows.append((result_set, unit, None, CAST, count.cast))


def _option(p: Package, contest: str, election: str, scope: object, party: str, position: int, d: Data) -> str:
    option = key("option", election, scope, party)
    label = d.parties.get(party, party)
    independent = independents(label)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="others" if independent else "list",
        party_id=None if independent else key("party", party),
        ballot_position=position,
        ballot_label=label,
    )
    return option


def _contest(
    p: Package,
    e: Election,
    path: str,
    scope: object,
    title: str,
    greek: str | None,
    district: str,
    magnitude: int,
    method: str,
    body: bool,
) -> tuple[str, str]:
    election = key("election", e.key)
    contest = key("contest", e.key, scope)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=PARLIAMENT if body else None,
        district_area_id=district,
        magnitude=magnitude,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, title, "en", "official")
    if greek:
        p.name(contest, greek, "el", "native", False)
    p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
    result_set = key("result_set", e.key, scope)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official" if e.key == "ps-2009" else "preliminary",
        kind="counted",
        source_id=MINISTRY_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    if e.key == "ps-2004":
        p.add(
            "note",
            id=key("note", result_set, "status"),
            entity_id=result_set,
            kind="caveat",
            body_md=UNOFFICIAL,
            lang="en",
        )
    return contest, result_set


def election(e: Election) -> Package:
    d = data(e.key)
    slug, english_kind, greek_kind = NAMES[e.family]
    p = Package(
        f"{SOURCE}/{e.key}",
        SOURCE,
        f"The {e.day.year} Greek {english_kind} by electoral constituency from the Ministry of Interior",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {GREECE, COUNTRY}
    _artifacts(p, [e])
    election_id = key("election", e.key)
    english = f"{e.day.year} Greek {english_kind}"
    p.add("election", id=election_id, polity_id=GREECE, kind="general", date_from=e.day, date_to=e.day, status="held")
    p.name(election_id, english, "en", "official")
    p.name(election_id, f"{greek_kind} {e.day.year}", "el", "native", False)
    path = f"gr/{e.day}-{slug}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    places = _place_names()
    rows: list[tuple[str, str, str | None, int, int]] = []
    parliament = e.family == "parliament"
    ordered = sorted(d.national.votes, key=lambda party: (-d.national.votes[party], int(party)))
    position = {party: index for index, party in enumerate(ordered, 1)}
    if parliament:
        for n, unit in sorted(d.units.items()):
            greek, name = places[n]
            contest, result_set = _contest(
                p, e, path, n, name, greek, area(n), sum(unit.seats.values()), METHOD + " " + SEAT_METHOD, True
            )
            code = f"{e.day}:{n}"
            unit_id = key("unit", e.key, n)
            p.add(
                "reporting_unit",
                id=unit_id,
                polity_id=GREECE,
                scheme_code="gr.electoral_district",
                code=code,
                area_relation="equals",
                area_id=area(n),
                kind="standard",
            )
            _accounting(rows, result_set, unit_id, unit)
            for party in sorted(unit.votes, key=lambda party: position[party]):
                option = _option(p, contest, e.key, n, party, position[party], d)
                rows.append((result_set, unit_id, option, VOTES, unit.votes[party]))
                if party in unit.seats:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=unit.seats[party])
        contest, result_set = _contest(
            p, e, path, "state", "State seats", "Έδρες επικρατείας", COUNTRY, sum(d.state.values()), STATE_METHOD, True
        )
        national = key("unit", e.key, "state")
        p.add(
            "reporting_unit",
            id=national,
            polity_id=GREECE,
            scheme_code="gr.electoral_district",
            code=f"{e.day}:state",
            area_relation="equals",
            area_id=COUNTRY,
            kind="aggregate",
        )
        _accounting(rows, result_set, national, d.national)
        for party in ordered:
            option = _option(p, contest, e.key, "state", party, position[party], d)
            rows.append((result_set, national, option, VOTES, d.national.votes[party]))
            if party in d.state:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=d.state[party])
        _compare_seats(e, d)
    else:
        contest, result_set = _contest(
            p, e, path, "greece", "Greek seats", "Έδρες Ελλάδας", COUNTRY, sum(d.state.values()), METHOD, False
        )
        options = {party: _option(p, contest, e.key, "greece", party, position[party], d) for party in ordered}
        for n, unit in sorted(d.units.items()):
            unit_id = key("unit", e.key, n)
            abroad = n == ABROAD
            p.add(
                "reporting_unit",
                id=unit_id,
                polity_id=GREECE,
                scheme_code="gr.electoral_district",
                code=f"{e.day}:{n}",
                area_relation="unlocated" if abroad else "equals",
                area_id=None if abroad else area(n),
                kind="abroad_mission" if abroad else "standard",
            )
            if abroad:
                p.name(unit_id, d.places[n], "el", "official")
            _accounting(rows, result_set, unit_id, unit)
            for party, votes in unit.votes.items():
                rows.append((result_set, unit_id, options[party], VOTES, votes))
        for party, seats in d.state.items():
            p.add("outcome", result_set_id=result_set, option_id=options[party], kind="won", seats=seats)
        for party in ordered:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[party],
                measure=VOTES,
                value=d.national.votes[party],
            )
    _values(p, rows)
    return p


def _compare_seats(e: Election, d: Data) -> None:
    total: dict[str, int] = {}
    for unit in d.units.values():
        for party, seats in unit.seats.items():
            total[party] = total.get(party, 0) + seats
    for party, seats in d.state.items():
        total[party] = total.get(party, 0) + seats
    printed = d.national.seats
    if total != printed:
        raise ValueError(f"{e.key}: constituency and state seats {total} differ from the printed totals {printed}")
    print(
        f"{e.key}: constituency plus state seats equal the printed national seats ({sum(total.values())})", flush=True
    )
