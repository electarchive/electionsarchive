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
ODIHR_2007 = "https://cdn.osce.org/sites/default/files/f/documents/d/d/26606.pdf"
ODIHR_ID = key("source", "osce.odihr")
ODIHR_2003 = "https://cdn.osce.org/sites/default/files/f/documents/1/2/42370.pdf"
NDI_1999 = "https://www.ndi.org/sites/default/files/337_am_1999final_5.pdf"
PRESIDENT_SYSTEM = key("electoral_system", "president")
REFERENDUM_SYSTEM = key("electoral_system", "referendum")
LIST_SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        key("electoral_system", "national_assembly", 1999),
        date(1999, 2, 17),
        date(2003, 1, 1),
        {"formula": "hare", "threshold": [{"percent": 5, "level": "party"}]},
        (
            "A mixed parallel system for 131 deputies under the Electoral Code signed on 17 February 1999: 75 elected "
            "in single-mandate constituencies by plurality, and 56 from national party and bloc lists in one "
            "constituency, allocated by the largest remainder method. A list needs 5% of the valid votes and "
            "inaccuracies to share in the list seats. The Hare quota with largest remainders reproduces the declared "
            "seats. The end of the period is the start of the year the 2002 amendments took effect by, as the "
            "amendments' own date is not printed in the sources."
        ),
    ),
    (
        key("electoral_system", "national_assembly", 2003),
        date(2003, 1, 1),
        date(2007, 1, 1),
        {"formula": "hare", "threshold": [{"percent": 5, "level": "party"}]},
        (
            "After the 2002 amendments to the Electoral Code, the proportions reversed: 75 deputies from national "
            "party and bloc lists in one constituency, for lists with more than 5% of the votes cast, and 56 in "
            "single-mandate constituencies by plurality in one round. The Hare quota with largest remainders "
            "reproduces the declared seats."
        ),
    ),
    (
        key("electoral_system", "national_assembly", 2007),
        date(2007, 1, 1),
        date(2012, 1, 1),
        {
            "formula": "hare",
            "threshold": [{"percent": 5, "level": "party"}, {"percent": 7, "level": "alliance"}],
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "A mixed parallel system for 131 deputies: 90 elected from closed party and bloc lists in one national "
            "constituency, and 41 in single-mandate districts by plurality. A party needs 5% of the valid votes to share "
            "in the list seats and a bloc 7%. The Hare quota with largest remainders reproduces the declared seats."
        ),
    ),
    (
        key("electoral_system", "national_assembly", 2012),
        date(2012, 1, 1),
        date(2017, 1, 1),
        {
            "formula": "hare",
            "threshold": [{"percent": 5, "level": "party"}, {"percent": 7, "level": "alliance"}],
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "Under the Electoral Code adopted in May 2011, the same parallel system: 90 deputies from closed lists in "
            "one national constituency, with a 5% threshold for a party and 7% for a bloc, and 41 deputies in "
            "single-mandate districts by plurality. The Hare quota with largest remainders reproduces the declared seats."
        ),
    ),
    (
        key("electoral_system", "national_assembly", 2017),
        date(2017, 1, 1),
        date(2021, 4, 27),
        {
            "formula": "hare",
            "threshold": [{"percent": 5, "level": "party"}, {"percent": 7, "level": "alliance"}],
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "A proportional system for at least 101 deputies from a closed national list and 13 open district lists, "
            "with a 5% threshold for a party and 7% for an alliance. A party's seats are first apportioned nationally; "
            "half go to its national list in order and half to the district candidates with the most preference votes. "
            "Up to four further seats are reserved for the four largest national minorities. A list with a majority "
            "below 54% of the seats receives additional seats to reach it, and a winner above two thirds of the seats "
            "leaves the other qualifying lists a third. Without a majority or a coalition within six days, the top "
            "two lists meet in a second round."
        ),
    ),
    (
        key("electoral_system", "national_assembly", 2021),
        date(2021, 4, 27),
        date(2021, 7, 1),
        {
            "formula": "hare",
            "threshold": [{"percent": 5, "level": "party"}, {"percent": 7, "level": "alliance"}],
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "After the amendments in force from 27 April 2021, at least 101 deputies from closed party and alliance lists "
            "in one national constituency, with a 5% threshold for a party and 7% for an alliance, four reserved "
            "minority seats, the 54% stable majority, and the second round. The May 2021 amendments did not apply to "
            "the June 2021 election."
        ),
    ),
    (
        key("electoral_system", "national_assembly", 2026),
        date(2021, 7, 1),
        None,
        {
            "formula": "hare",
            "threshold": [{"percent": 4, "level": "party"}, {"percent": 8, "level": "alliance"}],
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "Under the May 2021 amendments, 101 deputies from closed lists in one national constituency, allocated by "
            "the Hare quota with largest remainders, plus four seats for the four largest national minorities. The "
            "threshold is 4% for a party, 8% for an alliance of up to three parties, and 10% for an alliance of four "
            "or more; if fewer than three lists pass, the three nearest the threshold share the seats. A list with a "
            "majority below 52% of the seats receives additional seats to reach it. Without a majority or a coalition, "
            "the top two lists meet in a second round."
        ),
    ),
]


def list_system(day: date) -> str:
    return next(id for id, since, until, _, _ in LIST_SYSTEMS if since <= day and (until is None or day < until))


@dataclass(frozen=True)
class Election:
    day: date
    family: str  # parliament, president, referendum
    id: int  # the Commission's election number
    info: str  # the information table, relative to DOCS
    names: tuple[str, str]  # English, Armenian
    options: tuple[tuple[int, str], ...] = ()  # column in the information table and label, where the export has none
    protocol: str = ""  # the protocol registering the elected deputies, relative to DOCS
    seats: tuple[tuple[str, int], ...] = ()  # list seats each ballot label won, as the protocol registers them
    report: str = ""  # where the seats come from when the Commission posts no protocol: the observation report's URL


ELECTIONS = [
    Election(
        date(2007, 5, 12),
        "parliament",
        82,
        "doc/resultarm.xls",
        ("2007 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 2007"),
        report=ODIHR_2007,
        seats=(
            ("ՀԱՅԱՍՏԱՆԻ ՀԱՆՐԱՊԵՏԱԿԱՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 41),
            ("ԲԱՐԳԱՎԱՃ ՀԱՅԱՍՏԱՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 18),
            ("ՀԱՅ ՅԵՂԱՓՈԽԱԿԱՆ ԴԱՇՆԱԿՑՈՒԹՅՈՒՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 16),
            ("ՕՐԻՆԱՑ ԵՐԿԻՐ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 8),
            ("ԺԱՌԱՆԳՈՒԹՅՈՒՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 7),
        ),
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
        protocol="doc/290512v.pdf",
        seats=(
            ("ՀԱՅԱՍՏԱՆԻ ՀԱՆՐԱՊԵՏԱԿԱՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 40),
            ("ԲԱՐԳԱՎԱՃ ՀԱՅԱՍՏԱՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 28),
            ("ՀԱՅ ԱԶԳԱՅԻՆ ԿՈՆԳՐԵՍ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆՆԵՐԻ ԴԱՇԻՆՔ", 7),
            ("ՀԱՅ ՅԵՂԱՓՈԽԱԿԱՆ ԴԱՇՆԱԿՑՈՒԹՅՈՒՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 5),
            ("ԺԱՌԱՆԳՈՒԹՅՈՒՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 5),
            ("ՕՐԻՆԱՑ ԵՐԿԻՐ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 5),
        ),
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
        protocol="doc/030517v.pdf",
        seats=(
            ("ՀԱՅԱՍՏԱՆԻ ՀԱՆՐԱՊԵՏԱԿԱՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 58),
            ("«ԾԱՌՈՒԿՅԱՆ» ԿՈՒՍԱԿՑՈՒԹՅՈՒՆՆԵՐԻ ԴԱՇԻՆՔ", 31),
            ("«ԵԼՔ» ԿՈՒՍԱԿՑՈՒԹՅՈՒՆՆԵՐԻ ԴԱՇԻՆՔ", 9),
            ("«ՀԱՅ ՅԵՂԱՓՈԽԱԿԱՆ ԴԱՇՆԱԿՑՈՒԹՅՈՒՆ» ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 7),
        ),
    ),
    Election(
        date(2018, 12, 9),
        "parliament",
        27576,
        "doc/resultarm09.12.18v.xlsx",
        ("2018 Armenian early parliamentary election", "Ազգային ժողովի արտահերթ ընտրություններ, 2018"),
        protocol="doc/04.01.19v.pdf",
        seats=(
            ("ԻՄ ՔԱՅԼԸ ԴԱՇԻՆՔ", 88),
            ("«ԲԱՐԳԱՎԱՃ ՀԱՅԱՍՏԱՆ» ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 26),
            ("«ԼՈՒՍԱՎՈՐ ՀԱՅԱՍՏԱՆ» ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 18),
        ),
    ),
    Election(
        date(2021, 6, 20),
        "parliament",
        27697,
        "doc/resultsarm20.06.21v.xlsx",
        ("2021 Armenian early parliamentary election", "Ազգային ժողովի արտահերթ ընտրություններ, 2021"),
        protocol="doc/protocol22.07.21.pdf",
        seats=(
            ("«ՔԱՂԱՔԱՑԻԱԿԱՆ ՊԱՅՄԱՆԱԳԻՐ» ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 71),
            ("ՀԱՅԱՍՏԱՆ ԴԱՇԻՆՔ", 29),
            ("«ՊԱՏԻՎ ՈՒՆԵՄ» ԴԱՇԻՆՔ", 7),
        ),
    ),
    Election(
        date(2026, 6, 7),
        "parliament",
        28826,
        "forGenerals/NA2026/resultsarm07.06.26v_14-06-2026.xlsx",
        ("2026 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 2026"),
        protocol="Decisions/0-2026/21.07.26/protocol1_21-07-2026.pdf",
        seats=(
            ("«ՔԱՂԱՔԱՑԻԱԿԱՆ ՊԱՅՄԱՆԱԳԻՐ» ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 64),
            ("ՈՒԺԵՂ ՀԱՅԱՍՏԱՆ ԴԱՇԻՆՔ", 29),
            ("ՀԱՅԱՍՏԱՆ ԴԱՇԻՆՔ", 12),
        ),
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


def protocol_url(e: Election) -> str:
    return f"{DOCS}/{e.protocol}"


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
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(package_key(e) for e in ELECTIONS),
        *(early_key(d) for d in EARLY),
        *(old_key(o) for o in OLD),
    ]


def fetch() -> None:
    for e in ELECTIONS:
        raw.fetch(SOURCE, export_url(e), suffix=".xlsx")
        raw.fetch(SOURCE, stations_url(e), suffix=".xlsx")
        raw.fetch(SOURCE, info_url(e), suffix="." + e.info.rsplit(".", 1)[1])
        if e.protocol:
            raw.fetch(SOURCE, protocol_url(e), suffix=".pdf")
        if e.report:
            raw.fetch(SOURCE, e.report, suffix=".pdf")
    raw.fetch(SOURCE, ARCHIVE, suffix=".html")
    for d in EARLY:
        raw.fetch(SOURCE, d.report, suffix=".pdf")
    for e in ELECTIONS:
        page(e)


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
    for d in EARLY:
        early(d).write()
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
        *LIST_SYSTEMS,
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
    for d in EARLY:
        for name, _ in tally(d).parties:
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
    if e.protocol:
        p.artifact(raw.cached(SOURCE, protocol_url(e)), SOURCE_ID)
    if e.report:
        p.add(
            "source",
            id=ODIHR_ID,
            kind="official_body",
            publisher="OSCE Office for Democratic Institutions and Human Rights",
            title="Armenia, Parliamentary Elections, 12 May 2007: OSCE/ODIHR Election Observation Mission Final Report",
            url="https://odihr.osce.org/odihr/elections/armenia/26606",
            license="Published by the OSCE Office for Democratic Institutions and Human Rights; cited for the seat "
            "counts its annex prints",
            license_url="https://www.osce.org/",
            attribution="OSCE/ODIHR Election Observation Mission Final Report, Armenia, 12 May 2007",
        )
        p.artifact(raw.cached(SOURCE, e.report), ODIHR_ID)
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
        magnitude=1 if e.family == "president" else sum(n for _, n in e.seats) or None,
        round_number=1,
        electoral_system_id={
            "president": PRESIDENT_SYSTEM,
            "referendum": REFERENDUM_SYSTEM,
            "parliament": list_system(day) if e.seats else None,
        }[e.family],
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
    """The seats each list won, from the Commission's protocol; the winner of a presidential election and the answer
    a referendum carried, from its totals."""
    if e.family == "parliament":
        if not e.seats:
            return
        seats = {fold(name): n for name, n in e.seats}
        missing = seats.keys() - {fold(name) for name in options}
        if missing:
            raise LookupError(f"{e.day}: no ballot line for {sorted(missing)}")
        for name, option in options.items():
            n = seats.get(fold(name))
            p.add("outcome", result_set_id=result_set, option_id=option, kind="lost" if n is None else "won", seats=n)
        p.add(
            "note",
            id=key("note", result_set, "seats"),
            entity_id=result_set,
            kind="methodology",
            body_md=(
                "Seats are the deputies the OSCE/ODIHR final report's annex lists as won through the national "
                "proportional contest by each list. The Commission posts no protocol registering the 2007 deputies "
                "among its pages. The 41 single-mandate seats are in the district contests."
                if e.report
                else "Seats are the deputies the Commission's protocol registering the elected deputies assigns to "
                "each list, counted from the names the protocol registers under it."
            ),
            lang="en",
        )
        return
    votes = {name: declared.get(f"option:{name}", 0) for name in options}
    winner = max(votes, key=lambda name: votes[name])
    for name, option in options.items():
        kind = ("elected" if e.family == "president" else "won") if name == winner else "lost"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)


# The 1999 and 2003 elections: the national totals the Commission prints on its page for those years.

ARCHIVE = f"{SITE}/PageFor/Elections%201999-2005"


@dataclass(frozen=True)
class Early:
    day: date
    tab: str  # the id of the page's section for the election
    names: tuple[str, str]  # English, Armenian
    invalid: str  # the label that starts the invalid ballots row
    total: str  # the label that starts the row that sums the votes for the lists
    report: str  # the observation report the list seats come from
    seats: tuple[tuple[str, int], ...]  # list seats each ballot label won


EARLY = [
    Early(
        date(1999, 5, 30),
        "Tab6",
        ("1999 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 1999"),
        "Քվեատուփում առկա անվավեր",
        "Կուսակցությունների կամ կուսակցությունների դաշինքի օգտին",
        NDI_1999,
        (
            ("ՄԻԱՍՆՈՒԹՅՈՒՆ ԴԱՇԻՆՔ", 29),
            ("ՀԱՅԱՍՏԱՆԻ ԿՈՄՈՒՆԻՍՏԱԿԱՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 8),
            ("ԻՐԱՎՈՒՆՔ ԵՎ ՄԻԱԲԱՆՈՒԹՅՈՒՆ ԴԱՇԻՆՔ", 6),
            ("ՀԱՅ ՅԵՂԱՓՈԽԱԿԱՆ ԴԱՇՆԱԿՑՈՒԹՅՈՒՆ", 5),
            ("ՕՐԻՆԱՑ ԵՐԿԻՐ", 4),
            ("ԱԶԱՅԻՆ ԺՈՂՈՎՐԴԱԿԱՆ ՄԻՈՒԹՅՈՒՆ", 4),
        ),
    ),
    Early(
        date(2003, 5, 25),
        "Tab3",
        ("2003 Armenian parliamentary election", "Ազգային ժողովի ընտրություններ, 2003"),
        "Անվավեր քվեաթերթիկների",
        "Կողմ քվեարկված քվեաթերթիկների ընդհանուր",
        ODIHR_2003,
        (
            ("ՀԱՅԱՍՏԱՆԻ ՀԱՆՐԱՊԵՏԱԿԱՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 23),
            ("ԱՐԴԱՐՈՒԹՅԱՆ ԴԱՇԻՆՔ", 14),
            ("ՕՐԻՆԱՑ ԵՐԿԻՐ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 12),
            ("ՀԱՅ ՅԵՂԱՓՈԽԱԿԱՆ ԴԱՇՆԱԿՑՈՒԹՅՈՒՆ", 11),
            ("ԱԶԳԱՅԻՆ ՄԻԱԲԱՆՈՒԹՅՈՒՆ", 9),
            ("ՄԻԱՎՈՐՎԱԾ ԱՇԽԱՏԱՆՔԱՅԻՆ ԿՈՒՍԱԿՑՈՒԹՅՈՒՆ", 6),
        ),
    ),
]


def early_key(d: Early) -> str:
    return f"{SOURCE}/parliament-{d.day}"


@dataclass
class Tally:
    registered: int
    voters: int
    invalid: int
    against: int | None  # votes against every list
    total: int  # the votes for the lists, as the Commission sums them
    parties: list[tuple[str, int]]


@functools.cache
def tally(d: Early) -> Tally:
    """The national totals in the page's section for the election."""
    text = raw.cached(SOURCE, ARCHIVE).path.read_text(encoding="utf-8")
    start = text.index(f'id="{d.tab}"')
    end = text.find('id="Tab', start + 1)
    section = text[start : end if end > 0 else len(text)]
    table = section[section.index('class="election-result"') :]
    table = table[: table.index("</table>")]
    lines: dict[str, int] = {}
    parties: list[tuple[str, int]] = []
    listed = False
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL):
        cells = [
            clean(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        if len(cells) == 1 and cells[0].startswith("Յուրաքանչյուր"):
            listed = True
        elif len(cells) == 2 and (figure := number(cells[1])) is not None:
            if listed and not cells[0].startswith(d.total):
                parties.append((cells[0], figure))
            else:
                listed = False
                lines[cells[0]] = figure

    def line(prefix: str) -> int | None:
        found = [v for label, v in lines.items() if label.startswith(prefix)]
        return found[0] if len(found) == 1 else None

    registered = line("Ընտրողների ընդհանուր թիվը")
    voters = line("Գրանցված և քվեաթերթիկ ստացած")
    invalid = line(d.invalid)
    total = line(d.total)
    if registered is None or voters is None or invalid is None or total is None:
        raise LookupError(f"{d.day}: the page's totals are not as expected: {lines}")
    if sum(v for _, v in parties) != total:
        raise ValueError(f"{d.day}: the lists' votes sum to {sum(v for _, v in parties)}, not the printed {total}")
    return Tally(registered, voters, invalid, line("Բոլոր կուսակցություններին"), total, parties)


def early(d: Early) -> Package:
    p = Package(
        early_key(d),
        SOURCE,
        f"The {d.names[0]}, national totals from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARMENIA, COUNTRY}
    p.artifact(raw.cached(SOURCE, ARCHIVE), SOURCE_ID)
    report_id = key("source", "report", d.day)
    p.add(
        "source",
        id=report_id,
        kind="official_body" if d.report == ODIHR_2003 else "compendium",
        publisher=(
            "OSCE Office for Democratic Institutions and Human Rights"
            if d.report == ODIHR_2003
            else "National Democratic Institute for International Affairs"
        ),
        title=(
            "Republic of Armenia, Parliamentary Elections, 25 May 2003: OSCE/ODIHR Final Report"
            if d.report == ODIHR_2003
            else "The May 30, 1999 Parliamentary Elections in Armenia: final report"
        ),
        url=d.report,
        license="Published by its authors; cited for the list seat counts it prints",
        license_url="https://www.osce.org/" if d.report == ODIHR_2003 else "https://www.ndi.org/",
        attribution=(
            "OSCE/ODIHR Final Report, Armenia, 25 May 2003"
            if d.report == ODIHR_2003
            else "NDI final report on the 30 May 1999 parliamentary elections in Armenia"
        ),
    )
    p.artifact(raw.cached(SOURCE, d.report), report_id)
    data = tally(d)
    day = d.day
    election_id = key("election", day, "parliament")
    p.add("election", id=election_id, polity_id=ARMENIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, d.names[0], "en", "official")
    p.name(election_id, d.names[1], "hy", "native", False)
    path = f"am/{day}-parliamentary"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", day, "parliament")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=NATIONAL_ASSEMBLY,
        district_area_id=COUNTRY,
        magnitude=sum(n for _, n in d.seats),
        round_number=1,
        electoral_system_id=list_system(day),
        default_result_kind="counted",
    )
    p.name(contest, f"{d.names[0]}, national list", "en", "official")
    p.name(contest, f"{d.names[1]}, համամասնական ընտրակարգ", "hy", "native", False)
    p.add("slug", path=f"{path}/national-list", entity_id=contest, is_current=True)
    result_set = key("result_set", day, "parliament")
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
            "The Commission's page for this election prints only national totals: the registered voters, the voters "
            "registered by signature, the invalid ballots, and the votes for each list. They are held here as one "
            "unit that covers the whole country, and the same figures are stored as declared. The Commission's "
            "other printed figures, such as the ballots issued, are not stored."
        ),
        lang="en",
    )
    values = Facts()
    unit = key("unit", f"{day}:total")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ARMENIA,
        scheme_code="am.precinct",
        code=f"{day}:total",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    for measure, value in ((REGISTERED, data.registered), (VOTERS, data.voters), (INVALID, data.invalid)):
        values.add(result_set, unit, None, measure, value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    seats = {fold(name): n for name, n in d.seats}
    lines: list[tuple[str, int, bool]] = [(name, votes, True) for name, votes in data.parties]
    if data.against is not None:
        lines.append(("Against all lists", data.against, False))
    for position, (name, votes, listed) in enumerate(lines, 1):
        option = key("option", day, "parliament", name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list" if listed else "none_of_the_above",
            party_id=party(name) if listed else None,
            ballot_position=position,
            ballot_label=name,
        )
        values.add(result_set, unit, option, VOTES, votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        if listed:
            won = seats.pop(fold(name), None)
            p.add(
                "outcome", result_set_id=result_set, option_id=option, kind="lost" if won is None else "won", seats=won
            )
    if seats:
        raise LookupError(f"{day}: no ballot line for {sorted(seats)}")
    p.add(
        "note",
        id=key("note", result_set, "seats"),
        entity_id=result_set,
        kind="methodology",
        body_md=(
            "Seats are the list seats each list won, as the observation report prints them, "
            f"{sum(n for _, n in d.seats)} in all. The single-mandate seats are not in this contest."
        ),
        lang="en",
    )
    p.frame("result_value", values.frame())
    print(f"{early_key(d)}: {len(data.parties)} lists, total {data.total}", flush=True)
    return p


# The referendums of 2003 and 2005, whose only published results are the national totals on the Commission's page.


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
