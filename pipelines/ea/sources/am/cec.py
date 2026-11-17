"""Armenia's national elections from the Central Electoral Commission (elections.am).

The Commission publishes, for each election, two spreadsheets by polling station: its results table (an "ElectionResult"
export, with the voters, participants, invalid ballots, and one column per list) and the official information table
on res.elections.am (which also carries the votes cast by electronic voting and the national totals). Presidential
and referendum exports hold no votes by option, so those votes come from the information table. A third spreadsheet
lists each polling station's region and community.
"""

import functools
import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid5

import fastexcel
import httpx
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "am.cec"
SITE = "https://www.elections.am"
DOCS = "https://res.elections.am/images"
ARMENIA = str(uuid5(package.NAMESPACE, "polity\x1fAM"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARM"))
VOTES, REGISTERED, INVALID, VOTERS = 1, 2, 5, 14


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
NATIONAL_ASSEMBLY = key("body", "national_assembly")
PRESIDENCY = key("office", "president")
PRESIDENT_SYSTEM = key("electoral_system", "president")
REFERENDUM_SYSTEM = key("electoral_system", "referendum")


@dataclass(frozen=True)
class Election:
    day: date
    family: str  # parliament, president, referendum
    id: int  # the Commission's election number
    info: str  # the information table, relative to DOCS
    names: tuple[str, str]  # English, Armenian
    options: tuple[tuple[int, str], ...] = ()  # column in the information table and label, where the export has none


ELECTIONS = [
    Election(
        date(2007, 5, 12),
        "parliament",
        82,
        "doc/resultarm.xls",
        ("2007 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 2007"),
    ),
    Election(
        date(2008, 2, 19),
        "president",
        22052,
        "doc/resultarm2008.xls",
        ("2008 Armenian presidential election", "Հանրապետության Նախագահի ընտրություններ, 2008"),
        tuple((9 + i, "") for i in range(9)),
    ),
    Election(
        date(2012, 5, 6),
        "parliament",
        24104,
        "doc/resultarm06.05.12.xls",
        ("2012 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 2012"),
    ),
    Election(
        date(2013, 2, 18),
        "president",
        25547,
        "doc/resultarm18.02.13v.xls",
        ("2013 Armenian presidential election", "Հանրապետության Նախագահի ընտրություններ, 2013"),
        tuple((17 + i, "") for i in range(7)),
    ),
    Election(
        date(2015, 12, 6),
        "referendum",
        26015,
        "doc/resultarm06.12.15v.xlsx",
        ("2015 Armenian constitutional referendum", "Սահմանադրական հանրաքվե, 2015"),
        ((16, "Yes"), (17, "No")),
    ),
    Election(
        date(2017, 4, 2),
        "parliament",
        27339,
        "doc/resultarm02.04.17v.xlsx",
        ("2017 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 2017"),
    ),
    Election(
        date(2018, 12, 9),
        "parliament",
        27576,
        "doc/resultarm09.12.18v.xlsx",
        ("2018 Armenian early parliamentary election", "Ազգային ժողովի արտահերթ ընտրություններ, 2018"),
    ),
    Election(
        date(2021, 6, 20),
        "parliament",
        27697,
        "doc/resultsarm20.06.21v.xlsx",
        ("2021 Armenian early parliamentary election", "Ազգային ժողովի արտահերթ ընտրություններ, 2021"),
    ),
    Election(
        date(2026, 6, 7),
        "parliament",
        28826,
        "forGenerals/NA2026/resultsarm07.06.26v_14-06-2026.xlsx",
        ("2026 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 2026"),
    ),
]

MARZ = {
    "Երևան": "Yerevan",
    "Արագածոտն": "Aragatsotn",
    "Արարատ": "Ararat",
    "Արմավիր": "Armavir",
    "Գեղարքունիք": "Gegharkunik",
    "Լոռի": "Lori",
    "Կոտայք": "Kotayk",
    "Շիրակ": "Shirak",
    "Սյունիք": "Syunik",
    "Վայոց ձոր": "Vayots Dzor",
    "Տավուշ": "Tavush",
}
METHOD = (
    "Counted by the Central Electoral Commission per polling station, as published in its results table and its "
    "official information table. Registered voters are the electors in the polling station's main and supplementary "
    "lists, voters are the participants in the vote, and invalid ballots are as printed. The Commission does not print "
    "a count of valid ballots by station. Votes cast by electronic voting are reported by the Commission as one "
    "separate row and are held here as one unit that lies in no area. The national totals the Commission prints are "
    "stored as declared."
)


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def export_url(e: Election) -> str:
    return f"{SITE}/File/ElectionResult?electionId={e.id}"


def stations_url(e: Election) -> str:
    return f"{SITE}/File/SubDistrictsToExcel?electionId={e.id}"


def info_url(e: Election) -> str:
    return f"{DOCS}/{e.info}"


def page_url(e: Election) -> str:
    section = {
        "parliament": "Elections/ParliamentaryProportional",
        "president": "Elections/Presidential",
        "referendum": "Referendum/Referendum",
    }[e.family]
    return f"{SITE}/{section}#electionId={e.id}"


def package_key(e: Election) -> str:
    return f"{SOURCE}/{e.family}-{e.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(e) for e in ELECTIONS), *(old_key(o) for o in OLD)]


def fetch() -> None:
    for e in ELECTIONS:
        raw.fetch(SOURCE, export_url(e), suffix=".xlsx")
        raw.fetch(SOURCE, stations_url(e), suffix=".xlsx")
        raw.fetch(SOURCE, info_url(e), suffix="." + e.info.rsplit(".", 1)[1])
    for e in ELECTIONS:
        page(e)
    raw.fetch(SOURCE, ARCHIVE, suffix=".html")


def page(e: Election) -> raw.Artifact:
    """The national totals the Commission prints on the election's page, which only a form post returns."""
    url = page_url(e)
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    section = url.removeprefix(f"{SITE}/").split("#")[0]
    headers = {"User-Agent": raw.USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=120) as client:
        form = client.get(f"{SITE}/{section}").text
        token = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', form)
        kind = re.search(r'name="Input.ElectionTypeId" value="(\d+)"', form)
        if token is None or kind is None:
            raise LookupError(f"{SITE}/Elections/{section} has no form")
        response = client.post(
            f"{SITE}/{section}",
            data={
                "__RequestVerificationToken": token.group(1),
                "Culture": "hy",
                "Input.ElectionTypeId": kind.group(1),
                "Input.ElectionId": str(e.id),
                "Current.ElectionId": str(e.id),
                "Input.RegionCode": "",
                "Input.CommunityCode": "",
                "Input.SubDistrictId": "",
                "Input.Tab1": "active-tab",
            },
        )
        response.raise_for_status()
    return raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", "text/html"),
    )


@functools.cache
def printed(e: Election) -> dict[str, int]:
    """The national totals in the page's result table, by label."""
    text = page(e).path.read_text(encoding="utf-8")
    start = text.index('class="election-result"')
    out: dict[str, int] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text[start : text.index("</table>", start)], re.DOTALL):
        cells = [
            clean(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        if len(cells) >= 2 and (figure := number(cells[1].replace(" ", ""))) is not None:
            out.setdefault(cells[0], figure)
    return out


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()
    for o in OLD:
        old_referendum(o).write()


def emit() -> None:
    package.run("load", packages())


# Reading the spreadsheets.


@functools.cache
def sheet(url: str) -> list[list[str]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_idx(0, header_row=None, dtypes="string").to_polars()
    return [[clean(cell) for cell in row] for row in frame.rows()]


def number(text: str) -> int | None:
    if text in ("", "-"):
        return None
    try:
        return int(float(text.replace(" ", "").replace(",", ".")))
    except ValueError:
        return None


def code(text: str) -> str:
    """A polling station's number, as '<commission>/<station>' without leading zeros."""
    return "/".join(str(int(part)) for part in text.split("/"))


STATION = re.compile(r"\d+/\d+")


@dataclass
class Station:
    code: str
    registered: int | None
    voters: int | None
    invalid: int | None
    options: dict[str, int]


@dataclass
class Table:
    stations: dict[str, Station]
    columns: dict[str, list[int | None]]  # registered, voters, invalid, and each option, in station order
    order: list[str]  # the option labels, in column order


@functools.cache
def export(e: Election) -> Table:
    rows = sheet(export_url(e))
    head = next(i for i, row in enumerate(rows) if row[0] == "ՏԸՀ N")
    names = rows[head]
    registered = names.index("Ընտրողների ընդհանուր թիվը")
    voters = names.index("Քվեարկության մասնակիցների ընդհանուր թիվը")
    invalid = next(i for i, name in enumerate(names) if name.startswith("Անվավեր"))
    end = next(i for i, name in enumerate(names) if name.startswith("Անճշտություն"))
    options = list(range(invalid + 1, end))
    stations: dict[str, Station] = {}
    for row in rows[head + 1 :]:
        if not STATION.fullmatch(row[1]):
            continue
        votes = {names[i]: v for i in options if (v := number(row[i])) is not None}
        stations[code(row[1])] = Station(
            code(row[1]), number(row[registered]), number(row[voters]), number(row[invalid]), votes
        )
    order = [names[i] for i in options]
    columns: dict[str, list[int | None]] = {
        "registered": [s.registered for s in stations.values()],
        "voters": [s.voters for s in stations.values()],
        "invalid": [s.invalid for s in stations.values()],
    }
    for name in order:
        columns[f"option:{name}"] = [s.options.get(name) for s in stations.values()]
    return Table(stations, columns, order)


@dataclass
class Info:
    columns: dict[int, dict[str, int | None]]  # column -> station code, "evote", "total" -> value
    labels: dict[int, str]


@functools.cache
def info(e: Election) -> Info:
    rows = sheet(info_url(e))
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    start = 1 + max(j for row in rows for j in range(3) if STATION.fullmatch(row[j]))
    columns: dict[int, dict[str, int | None]] = {j: {} for j in range(width)}
    seen = False
    for row in rows:
        place = next((c for c in row[:3] if STATION.fullmatch(c)), None)
        lead = next((c for c in row[:start] if c), "").lower()
        if place is not None:
            where = code(place)
        elif lead.startswith("էլ"):
            where, seen = "evote", True
        elif lead.startswith("ընդամենը") or (seen and lead.startswith("ընդ")):
            where = "total"
        else:
            continue
        for j in range(start, width):
            columns[j][where] = number(row[j])
    labels: dict[int, str] = {}
    for row in rows[:8]:
        for j, text in enumerate(row):
            if text and not text.isdigit() and j not in labels:
                labels[j] = text
    return Info(columns, labels)


def matched(table: Table, data: Info) -> dict[str, int]:
    """The information table's column for each of the export's columns, found by equal values at every station."""
    by_vector: dict[tuple[int, ...], list[int]] = {}
    present = {c for values in data.columns.values() for c in values}
    codes = [c for c in table.stations if c in present]
    for j, values in data.columns.items():
        if any(c in values for c in codes):
            by_vector.setdefault(tuple(values.get(c) or 0 for c in codes), []).append(j)
    found: dict[str, int] = {}
    used: set[int] = set()
    for name, values in table.columns.items():
        row = tuple(v or 0 for c, v in zip(table.stations, values, strict=True) if c in present)
        options = [j for j in by_vector.get(row, []) if j not in used]
        if not options:
            continue
        found[name] = options[0]
        used.add(options[0])
    return found


# Places.


def area(scheme: str, text: str) -> str:
    return key("area", scheme, text)


@functools.cache
def places() -> dict[str, tuple[str, str]]:
    """Every polling station's (region, community) by election and normalized number."""
    out: dict[str, tuple[str, str]] = {}
    for e in ELECTIONS:
        rows = sheet(stations_url(e))
        for row in rows[1:]:
            if STATION.fullmatch(row[4]):
                out[f"{e.day}:{code(row[4])}"] = (row[0], row[1])
    return out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Armenia's 11 regions (marzer) and the communities named in the Central Electoral Commission's polling "
        "station lists",
    )
    p.external |= {ARMENIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Հայաստանի Հանրապետության կենտրոնական ընտրական հանձնաժողով",
        title="Elections: results by polling station, information tables, and polling station lists",
        url=f"{SITE}/",
        license="Official results of the Republic of Armenia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Central Electoral Commission of the Republic of Armenia, elections.am",
    )
    for e in ELECTIONS:
        p.artifact(raw.cached(SOURCE, stations_url(e)), SOURCE_ID)
    for scheme, rank, kind, noun in (
        ("am.marz", 1, "administrative", "region"),
        ("am.community", 2, "administrative", "community"),
        ("am.precinct", 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=scheme, polity_id=ARMENIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for marz, english in sorted(MARZ.items()):
        id = area("am.marz", marz)
        p.add("area", id=id, scheme_code="am.marz")
        p.add("area_code", area_id=id, code=marz)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, marz, "hy", "official")
        p.name(id, english, "en", "english", False)
    communities = sorted({place for place in places().values()})
    for marz, community in communities:
        id = area("am.community", f"{marz}:{community}")
        p.add("area", id=id, scheme_code="am.community")
        p.add("area_code", area_id=id, code=f"{marz}:{community}")
        p.add("area_parent", area_id=id, parent_id=area("am.marz", marz))
        p.name(id, community, "hy", "official")
    return p


# Parties, people, bodies, and systems.


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).upper()
    return " ".join(re.sub(r"[«»\"“”'`´՛]", " ", text).split())


def party(name: str) -> str:
    return key("party", fold(name))


def party_kind(name: str) -> str:
    return "bloc" if "ԴԱՇԻՆՔ" in fold(name) and "ԴԱՇՆԱԿՑՈՒԹՅՈՒՆ" not in fold(name) else "party"


def list_names(e: Election) -> list[str]:
    return export(e).order


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The National Assembly, the presidency, the referendum and presidential electoral systems, and the parties "
        "and alliances on Armenian parliamentary ballots from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ARMENIA, COUNTRY}
    p.add("body", id=NATIONAL_ASSEMBLY, polity_id=ARMENIA, kind="unicameral")
    p.name(NATIONAL_ASSEMBLY, "National Assembly", "en", "official")
    p.name(NATIONAL_ASSEMBLY, "Ազգային ժողով", "hy", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=ARMENIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Armenia", "en", "official")
    p.name(PRESIDENCY, "Հայաստանի Հանրապետության Նախագահ", "hy", "native", False)
    for id, since, until, rules, note in (
        (
            PRESIDENT_SYSTEM,
            date(1996, 1, 1),
            date(2018, 1, 1),
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate who wins more than half of the votes cast is elected; otherwise the two leading "
                "candidates go to a second round."
            ),
        ),
        (
            REFERENDUM_SYSTEM,
            date(1995, 1, 1),
            None,
            {"formula": "referendum"},
            "A referendum question is decided by the votes for and against it.",
        ),
    ):
        p.add(
            "electoral_system", id=id, polity_id=ARMENIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    names: dict[str, str] = {}
    for e in ELECTIONS:
        if e.family != "parliament":
            continue
        for name in list_names(e):
            names.setdefault(party(name), name)
    for id, name in sorted(names.items()):
        p.add("party", id=id, kind=party_kind(name), polity_id=ARMENIA)
        p.add("external_id", entity_id=id, scheme="am.cec.party", value=fold(name))
        p.name(id, name, "hy", "official")
    return p


# One package per election day.


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


QUESTION = (
    "Whether to adopt the amendments to the Constitution of the Republic of Armenia put to the 2015 referendum",
    "Ընդունու՞մ եք արդյոք Հայաստանի Հանրապետության Սահմանադրության փոփոխությունների նախագիծը",
)


def election(e: Election) -> Package:
    p = Package(
        package_key(e),
        SOURCE,
        f"The {e.names[0]} by polling station from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARMENIA, COUNTRY}
    p.artifact(raw.cached(SOURCE, export_url(e)), SOURCE_ID)
    p.artifact(raw.cached(SOURCE, info_url(e)), SOURCE_ID)
    p.uses(raw.cached(SOURCE, stations_url(e)))
    table = export(e)
    data = info(e)
    found = matched(table, data)
    where = places()
    day = e.day
    election_id = key("election", day, e.family)
    p.add(
        "election",
        id=election_id,
        polity_id=ARMENIA,
        kind="referendum" if e.family == "referendum" else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election_id, e.names[0], "en", "official")
    p.name(election_id, e.names[1], "hy", "native", False)
    path = (
        f"am/{day}-"
        + {"parliament": "parliamentary", "president": "presidential", "referendum": "referendum"}[e.family]
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", day, e.family)
    english, native, slug = {
        "parliament": (f"{e.names[0]}, national list", f"{e.names[1]}, համամասնական ընտրակարգ", "national-list"),
        "president": (e.names[0], e.names[1], "president"),
        "referendum": (QUESTION[0], QUESTION[1], "constitutional-amendments"),
    }[e.family]
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind={"parliament": "list_allocation", "president": "office", "referendum": "ballot_question"}[e.family],
        office_id=PRESIDENCY if e.family == "president" else None,
        body_id=NATIONAL_ASSEMBLY if e.family == "parliament" else None,
        district_area_id=COUNTRY,
        magnitude=1 if e.family == "president" else None,
        round_number=1,
        electoral_system_id={"president": PRESIDENT_SYSTEM, "referendum": REFERENDUM_SYSTEM}.get(e.family),
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.name(contest, native, "hy", "native", False)
    p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
    if e.family == "referendum":
        p.add("ballot_question", contest_id=contest, type="referendum")
    result_set = key("result_set", day, e.family)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    # The options, with the column in the information table that holds each one's votes.
    columns: dict[str, int] = {}
    labels: dict[str, str] = {}
    if e.family == "parliament":
        for name in table.order:
            if f"option:{name}" in found:
                columns[name] = found[f"option:{name}"]
            labels[name] = name
    else:
        for j, label in e.options:
            text = label or data.labels[j]
            columns[text] = j
            labels[text] = text
    options: dict[str, str] = {}
    for position, name in enumerate(labels, 1):
        option = options[name] = key("option", day, e.family, name)
        if e.family == "parliament":
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(name),
                ballot_position=position,
                ballot_label=name,
            )
        elif e.family == "president":
            person = key("person", day, name)
            p.add("person", id=person)
            p.name(person, name, "hy", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, name),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
            )
        else:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=name,
            )
            p.name(option, name, "en", "official")
    values = Facts()
    accounting = {"registered": REGISTERED, "voters": VOTERS, "invalid": INVALID}
    codes = [*table.stations, *(c for c in data.columns[next(iter(columns.values()))] if STATION.fullmatch(c))]
    codes = list(dict.fromkeys(codes))
    unplaced = 0
    for station in codes:
        unit = key("unit", f"{day}:{station}")
        marz_name, community = where.get(f"{day}:{station}", (None, None))
        if marz_name is None:
            unplaced += 1
        place = (
            area("am.community", f"{marz_name}:{community}")
            if marz_name is not None and community is not None
            else COUNTRY
        )
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ARMENIA,
            scheme_code="am.precinct",
            code=f"{day}:{station}",
            area_relation="within",
            area_id=place,
            kind="standard",
        )
        row = table.stations.get(station)
        if row is not None:
            values.add(result_set, unit, None, REGISTERED, row.registered)
            values.add(result_set, unit, None, VOTERS, row.voters)
            values.add(result_set, unit, None, INVALID, row.invalid)
        for name, column in columns.items():
            if e.family == "parliament":
                values.add(result_set, unit, options[name], VOTES, row.options.get(name) if row else None)
            else:
                values.add(result_set, unit, options[name], VOTES, data.columns[column].get(station))
    if unplaced:
        print(f"{package_key(e)}: {unplaced} stations in no listed community", flush=True)
    # Votes cast by electronic voting, and the national totals the Commission prints.
    evote = key("unit", f"{day}:evote")
    p.add(
        "reporting_unit",
        id=evote,
        polity_id=ARMENIA,
        scheme_code="am.precinct",
        code=f"{day}:evote",
        area_relation="unlocated",
        area_id=None,
        kind="aggregate",
    )
    declared = totals(e, data, found, columns)
    if not any("total" in values for values in data.columns.values()):
        p.artifact(page(e), SOURCE_ID)
    for name, measure in accounting.items():
        if name in found:
            values.add(result_set, evote, None, measure, data.columns[found[name]].get("evote"))
        if name in declared:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=declared[name])
    for name, column in columns.items():
        values.add(result_set, evote, options[name], VOTES, data.columns[column].get("evote"))
        if f"option:{name}" in declared:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[name],
                measure=VOTES,
                value=declared[f"option:{name}"],
            )
    p.frame("result_value", values.frame())
    over = [
        (station, row.registered, row.voters)
        for station, row in table.stations.items()
        if row.registered is not None and row.voters is not None and row.voters > row.registered
    ]
    if over:
        listed = ", ".join(
            f"{station} ({voters} participants, {registered} registered)" for station, registered, voters in over
        )
        p.accept(
            "accounting.turnout",
            f"The Commission's own results table prints more participants than registered voters at {listed}",
        )
    print(f"{package_key(e)}: {len(codes)} stations, {len(columns)} options, {len(declared)} totals", flush=True)
    _outcomes(p, e, result_set, options, declared)
    return p


def totals(e: Election, data: Info, found: dict[str, int], columns: dict[str, int]) -> dict[str, int]:
    """The national totals the Commission prints: its table's last row where it has one, else its page."""
    out: dict[str, int] = {}
    if any("total" in values for values in data.columns.values()):
        for name in ("registered", "voters", "invalid"):
            if name in found and (total := data.columns[found[name]].get("total")) is not None:
                out[name] = total
        for name, column in columns.items():
            if (total := data.columns[column].get("total")) is not None:
                out[f"option:{name}"] = total
        return out
    folded = {fold(name): name for name in columns}
    for label, value in printed(e).items():
        if label in (
            "Ընտրողների ընդհանուր թիվը",
            "Հանրաքվեին մասնակցելու իրավունք ունեցող քաղաքացիների ընդհանուր թիվը",
        ):
            out["registered"] = value
        elif label.startswith("Գրանցված և քվեաթերթիկ ստացած") or label == "Քվեարկության մասնակիցների թիվը":
            out["voters"] = value
        elif label.startswith('"ԱՅՈ"'):
            out["option:Yes"] = value
        elif label.startswith('"ՈՉ"'):
            out["option:No"] = value
        elif label == "Անվավեր քվեաթերթիկների թիվը":
            out["invalid"] = value
        elif fold(label) in folded:
            out[f"option:{folded[fold(label)]}"] = value
    return out


def _outcomes(p: Package, e: Election, result_set: str, options: dict[str, str], declared: dict[str, int]) -> None:
    """The winner of a presidential election and the answer a referendum carried, from the Commission's totals."""
    if e.family == "parliament":
        return
    votes = {name: declared.get(f"option:{name}", 0) for name in options}
    winner = max(votes, key=lambda name: votes[name])
    for name, option in options.items():
        kind = ("elected" if e.family == "president" else "won") if name == winner else "lost"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)


# The referendums of 2003 and 2005, whose only published results are the national totals on the Commission's page.

ARCHIVE = f"{SITE}/PageFor/Elections%201999-2005"


@dataclass(frozen=True)
class Old:
    day: date
    heading: str  # the start of the section's heading on the page
    names: tuple[str, str]  # English, Armenian


OLD = [
    Old(
        date(2003, 5, 25),
        "25 Մայիսի 2003",
        ("2003 Armenian constitutional referendum", "Սահմանադրական հանրաքվե, 2003"),
    ),
    Old(
        date(2005, 11, 27),
        "27 Նոյեմբեր 2005",
        ("2005 Armenian constitutional referendum", "Սահմանադրական հանրաքվե, 2005"),
    ),
]


def old_key(o: Old) -> str:
    return f"{SOURCE}/referendum-{o.day}"


@functools.cache
def old_totals(o: Old) -> dict[str, int]:
    """The national totals printed under the referendum's heading, by label."""
    text = raw.cached(SOURCE, ARCHIVE).path.read_text(encoding="utf-8")
    lines = [
        clean(html.unescape(line))
        for line in re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", text)).split("\n")
    ]
    lines = [line for line in lines if line]
    start = next(i for i, line in enumerate(lines) if line.startswith(o.heading) and "Հանրաքվե" in line)
    out: dict[str, int] = {}
    i = start + 1
    while i + 1 < len(lines) and (value := number(lines[i + 1])) is not None and number(lines[i]) is None:
        out[lines[i]] = value
        i += 2
    return out


def old_referendum(o: Old) -> Package:
    p = Package(
        old_key(o),
        SOURCE,
        f"The {o.names[0]}, national totals from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARMENIA, COUNTRY}
    p.artifact(raw.cached(SOURCE, ARCHIVE), SOURCE_ID)
    printed = old_totals(o)
    day = o.day
    election_id = key("election", day, "referendum")
    p.add("election", id=election_id, polity_id=ARMENIA, kind="referendum", date_from=day, date_to=day, status="held")
    p.name(election_id, o.names[0], "en", "official")
    p.name(election_id, o.names[1], "hy", "native", False)
    path = f"am/{day}-referendum"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", day, "referendum")
    english = f"Whether to adopt the amendments to the Constitution of the Republic of Armenia put to the {day.year} referendum"
    native = "Սահմանադրությունում փոփոխություններ կատարելու մասին"
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="ballot_question",
        office_id=None,
        body_id=None,
        district_area_id=COUNTRY,
        magnitude=None,
        round_number=1,
        electoral_system_id=REFERENDUM_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.name(contest, native, "hy", "native", False)
    p.add("slug", path=f"{path}/constitutional-amendments", entity_id=contest, is_current=True)
    p.add("ballot_question", contest_id=contest, type="referendum")
    result_set = key("result_set", day, "referendum")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=(
            "The Commission's archive page prints only national totals for this referendum, so the result is "
            "held as one unit for the whole country. Registered voters are the electors on the voter lists; voters "
            "are those registered and issued a ballot, by signature. Votes by answer and invalid ballots are as "
            "printed. The Commission also prints a figure for inaccuracies, which is not stored."
        ),
        lang="en",
    )
    options: dict[str, str] = {}
    for position, (label, prefix) in enumerate((("Yes", "1.Այո"), ("No", "2.Ոչ")), 1):
        option = options[label] = key("option", day, "referendum", label)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
        p.name(option, label, "en", "official")
    unit = key("unit", f"{day}:national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ARMENIA,
        scheme_code="am.precinct",
        code=f"{day}:national",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    values = Facts()
    figures: dict[tuple[str | None, int], int] = {}
    for label, value in printed.items():
        if label.startswith("Ընտրողների ընդհանուր թիվը"):
            figures[None, REGISTERED] = value
        elif label.startswith("Գրանցված և քվեաթերթիկ ստացած"):
            figures[None, VOTERS] = value
        elif label.startswith("Անվավեր"):
            figures[None, INVALID] = value
        elif label.startswith("1.Այո"):
            figures["Yes", VOTES] = value
        elif label.startswith("2.Ոչ"):
            figures["No", VOTES] = value
    for (label, measure), value in figures.items():
        option = options[label] if label else None
        values.add(result_set, unit, option, measure, value)
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    p.frame("result_value", values.frame())
    votes = {label: figures[label, VOTES] for label in options}
    winner = max(votes, key=lambda label: votes[label])
    for label, option in options.items():
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if label == winner else "lost")
    print(f"{old_key(o)}: {len(figures)} totals", flush=True)
    return p
