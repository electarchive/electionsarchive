"""Local assembly elections in Serbia from the Republic Electoral Commission's register of local commissions' documents.

Since the 2022 Law on Local Elections each municipal and city electoral commission posts its documents in the
Commission's register (`/dokumenta`), one round of local elections at a time: decisions, lists, minutes, and the
report of the total results (чл. 60). The register is read through the JSON endpoint its page calls,
`/get-additional-documents`, page by page for each round. Most reports are scans; their figures were read from the
page images and are kept, with the page and the box each figure was found in, in ``lokalni_scans.json`` beside this
module (see ``ea.sources.rs.lokalni_scans``). A few commissions also posted their polling-station table as a
spreadsheet; those are read here directly.
"""

import json
import re
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl
import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik

SOURCE = "rs.lokalni"
GEOBOUNDARIES = "rs.geoboundaries"
BASE = rik.BASE
REGISTER = f"{BASE}/get-additional-documents"
SERBIA = str(uuid5(package.NAMESPACE, "polity\x1fRS"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = (
    rik.VOTES,
    rik.REGISTERED,
    rik.CAST,
    rik.VALID,
    rik.INVALID,
    rik.VOTERS,
)
GEO_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/SRB/ADM2/"
    "geoBoundaries-SRB-ADM2_simplified.geojson"
)
SCANS = Path(__file__).with_name("lokalni_scans.json")
UNIT_NAMES = Path(__file__).with_name("lokalni_units.json")

# The register's rounds of local elections: municipal and city assemblies, and city municipalities' assemblies.
ROUNDS: dict[int, str] = {
    42735: "Локални 2022",
    341149: "Локални 2023",
    572414: "Локални 2024",
    660578: "Локални избори 2025",
    668522: "Локални избори 2026.",
    559265: "Локални избори градске општине 2023",
    572417: "Локални избори градске општине 2024",
    668735: "Локални избори градске општине 2026",
}

METHOD = (
    "The figures are those of the local electoral commission's report of the total results of the election "
    "(укупан извештај о резултатима избора, Zakon o lokalnim izborima чл. 60), as the commission posted it in the "
    "Republic Electoral Commission's register of local commissions' documents. Most reports are scans: each figure "
    "was read from the page image, checked against the report's own arithmetic (the lists' votes against the valid "
    "ballots, and valid plus invalid ballots against the ballots in the boxes), and is stored with the page and the "
    "box it was read from. Registered voters are those in the voter register for the unit, voters are those who "
    "came out to vote, ballots cast are the ballots found in the boxes (where a report prints no such line, the voters "
    "who voted, бирачи који су гласали, which the commission counts from the ballots in the boxes), and valid votes "
    "are the valid ballots. Seats are as the "
    "commission declared them for each list in the same report; a list with no seats has no outcome."
)
METHOD_STATIONS = (
    " The commission also posted its table of results by polling station as a spreadsheet; the polling-station "
    "rows are read from it, and the unit's totals are those of its report."
)

METHOD_TABLE = (
    " The commission's table of results by polling station was read the same way, row by row; each row's list votes "
    "were checked against its valid ballots, and every column sums exactly to the report's totals for the lists "
    "and the valid ballots. Its figures keep the page of the table they are printed on."
)

LICENSE = rik.LICENSE
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-nd/3.0/rs/"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
GB_SOURCE = key("source", "geoboundaries", "SRB", "ADM2")
SYSTEM = key("electoral_system", "local")


# fetching -----------------------------------------------------------------------------------------


def register_url(round_id: int, page: int) -> str:
    query = urllib.parse.urlencode(
        {
            "page": page,
            "filters[document-type]": 1,
            "filters[election-round]": round_id,
            "election_type": 3,
        }
    )
    return f"{REGISTER}?{query}"


def document_url(link: str) -> str:
    return BASE + urllib.parse.quote(link)


def _page(round_id: int, page: int) -> dict[str, Any]:
    return json.loads(raw.fetch(SOURCE, register_url(round_id, page), suffix=".json").path.read_text())


def _pages(round_id: int) -> int:
    first = _page(round_id, 1)
    return max([1, *(int(n) for n in re.findall(r'data-page="(\d+)"', first["pagination"]))])


@dataclass(frozen=True)
class Record:
    round_id: int
    municipality: str
    name: str
    kind: str
    bearer: str
    posted: int
    links: tuple[str, ...]


def records(round_id: int) -> list[Record]:
    """Every document the register lists for a round, in the register's own order."""
    out: list[Record] = []
    for page in range(1, _pages(round_id) + 1):
        for row in _page(round_id, page)["records"]:
            links = tuple(
                re.sub(r"&amp;", "&", link)
                for html in row["extfiles_link"]
                for link in re.findall(r'href="([^"]+)"', html)
            )
            out.append(
                Record(
                    round_id,
                    str(row["municipality_display_name"]),
                    " ".join(str(row["document_name"]).replace("&#34;", '"').split()),
                    str(row.get("decision_type") or ""),
                    str(row.get("document_bearer") or ""),
                    int(row["datetime"]),
                    links,
                )
            )
    return out


WANTED = re.compile(
    r"ИЗВЕШТАЈ О (РЕЗ|СПРОВЕДЕН|ИЗБОРИМА)|IZVE[ŠS]TAJ O (REZ|SPROVED)|РЕЗУЛТАТИ ГЛАСАЊА (ПО|ЗА СВАКО)"
    r"|ПО БИРАЧКИМ МЕСТИМА|ЗА СВАКО БИРАЧКО|UKUPAN IZVE"
)
UNWANTED = re.compile(r"ПРЕЛИМИН|ОБРАЗ|КОНТРОЛ|ПРОВЕР|ПОНОВН")


def wanted(record: Record) -> bool:
    """A report of results or a table of results by polling station, not a preliminary result or a form."""
    name = record.name.upper()
    return bool(WANTED.search(name)) and not UNWANTED.search(name)


def fetch() -> None:
    for round_id in ROUNDS:
        urls = [document_url(link) for r in records(round_id) if wanted(r) for link in r.links]
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda url: raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1].lower()), urls))
    raw.fetch(GEOBOUNDARIES, GEO_URL, suffix=".geojson")


# units ---------------------------------------------------------------------------------------------

# The register names a few city municipalities differently from the Commission's list of municipalities.
ALIASES = {
    ("Палилула", 572417): "71323",
    ("Пантелеј (Ниш)", 572417): "71307",
    ("Савски венац", 572417): "70220",
    ("Стари град", 572417): "70246",
    ("Црвени Крст", 572417): "71315",
}


def _bare(name: str) -> str:
    return re.sub(r"^(Градска општина|Град) ", "", name)


@cache
def municipalities() -> dict[str, str]:
    """Each code in the Commission's list of municipalities and its name there, as ``rs.rik/geo`` holds them."""
    return json.loads(UNIT_NAMES.read_text())


def code_of(name: str, round_id: int) -> str:
    if (name, round_id) in ALIASES:
        return ALIASES[(name, round_id)]
    found = [code for code, full in municipalities().items() if _bare(full) == name]
    if len(found) != 1:
        raise LookupError(f"{name} ({round_id}): {found}")
    return found[0]


LATIN = dict(
    zip(
        "абвгдђежзијклљмнњопрстћуфхцчџшАБВГДЂЕЖЗИЈКЛЉМНЊОПРСТЋУФХЦЧЏШ",
        [
            *[
                "a",
                "b",
                "v",
                "g",
                "d",
                "đ",
                "e",
                "ž",
                "z",
                "i",
                "j",
                "k",
                "l",
                "lj",
                "m",
                "n",
                "nj",
                "o",
                "p",
                "r",
                "s",
                "t",
                "ć",
                "u",
                "f",
                "h",
                "c",
                "č",
                "dž",
                "š",
            ],
            *[
                "A",
                "B",
                "V",
                "G",
                "D",
                "Đ",
                "E",
                "Ž",
                "Z",
                "I",
                "J",
                "K",
                "L",
                "Lj",
                "M",
                "N",
                "Nj",
                "O",
                "P",
                "R",
                "S",
                "T",
                "Ć",
                "U",
                "F",
                "H",
                "C",
                "Č",
                "Dž",
                "Š",
            ],
        ],
        strict=True,
    )
)
ASCII = str.maketrans(
    {"đ": "dj", "ž": "z", "ć": "c", "č": "c", "š": "s", "Đ": "Dj", "Ž": "Z", "Ć": "C", "Č": "C", "Š": "S"}
)


def latin(text: str) -> str:
    return "".join(LATIN.get(ch, ch) for ch in text)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", latin(text).translate(ASCII).lower()).strip("-")


# reading -------------------------------------------------------------------------------------------

Box = tuple[int, float, float, float, float]  # page, x0, y0, x1, y1 in pixels of the page rendered at 100 dpi
TOTALS = {"registered": REGISTERED, "voters": VOTERS, "cast": CAST, "invalid": INVALID, "valid": VALID}


@dataclass
class ListRow:
    number: int
    name: str
    votes: int
    seats: int | None
    box: Box | None


@dataclass
class Station:
    number: int
    name: str | None
    figures: dict[int, int]
    lists: dict[int, int]  # votes by the list's ballot number
    page: int | None = None  # the page of the report's table the row is printed on


@dataclass
class Report:
    url: str  # the report the totals come from
    code: str  # the unit's code in the Commission's list of municipalities
    tier: str  # "jls" for a municipality or city, "go" for a city municipality
    body: str  # the assembly, as the report names it
    day: date
    totals: dict[int, tuple[int, Box | None]]
    lists: list[ListRow]
    stations_url: str | None = None  # the commission's spreadsheet of results by polling station
    table: list[dict[str, Any]] | None = None  # the rows of a scanned table of results by polling station
    table_url: str | None = None  # the scan that table is printed in
    note: str = ""

    @property
    def name(self) -> str:
        return _bare(municipalities()[self.code])

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def key(self) -> str:
        return f"{SOURCE}/{self.day}-{self.slug}"


# what a polling-station table needed beyond reading its cells, by election day and unit
TABLE_NOTES = {
    ("2024-06-02", "70254"): (
        " Three cells of the second list's column are partly broken in the scan (stations 41, 55, and 102); each "
        "keeps its legible digit, and the other is the one the station's printed valid ballots require."
    ),
}
REPEATED = (
    "The totals include the repeated vote held at some polling stations after election day, as the report states."
)


@cache
def scans() -> dict[str, dict[str, Any]]:
    return json.loads(SCANS.read_text())


def _box(where: object) -> Box | None:
    if not isinstance(where, list) or len(where) != 5:
        return None
    page, x0, y0, x1, y1 = where
    return (int(page), float(x0), float(y0), float(x1), float(y1))


@cache
def reports() -> list[Report]:
    out = [
        Report(
            url=url,
            code=row["code"],
            tier=row["tier"],
            body=row["body"],
            day=date.fromisoformat(row["day"]),
            totals={TOTALS[name]: (int(value), _box(where)) for name, (value, where) in row["totals"].items()},
            lists=[
                ListRow(int(r["n"]), r["name"], int(r["votes"][0]), r["seats"], _box(r["votes"][1]))
                for r in row["lists"]
            ],
            stations_url=row.get("stations"),
            table=row.get("table"),
            table_url=row.get("table_url"),
            note=REPEATED if row.get("repeat_vote") else "",
        )
        for url, row in scans().items()
    ]
    return sorted(out, key=lambda r: (r.day, r.code))


def _cells(url: str) -> list[list[str]]:
    import fastexcel

    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet(0, header_row=None).to_polars()
    return [["" if v is None else str(v).strip() for v in row] for row in frame.iter_rows()]


def _int(text: str) -> int:
    return int(float(text or 0))  # a blank cell is a zero


FIGURE_NAMES = {"registered": REGISTERED, "voters": VOTERS, "cast": CAST, "invalid": INVALID, "valid": VALID}


def stations(report: Report) -> list[Station]:
    """The report's polling stations: from the commission's spreadsheet, or from the scanned table read with it."""
    if report.table is not None and not report.stations_url:
        return [
            Station(
                int(row["number"]),
                row.get("name"),
                {FIGURE_NAMES[k]: int(row[k]) for k in FIGURE_NAMES if row.get(k) is not None},
                {int(n): int(v) for n, v in row["lists"].items()},
                int(row["page"]) if row.get("page") else None,
            )
            for row in report.table
        ]
    rows = _sheet_stations(report)
    # the spreadsheet's list columns are matched to the report's lists by their totals, else taken in order
    width = len(report.lists)
    sums = [sum(r[3][i] for r in rows) for i in range(width)]
    by_votes = {row.votes: row.number for row in report.lists}
    numbers = [by_votes.get(total) for total in sums]
    if None in numbers or len(set(numbers)) != width:
        numbers = [row.number for row in report.lists]
    return [
        Station(number, name, figures, {n: v for n, v in zip(numbers, votes, strict=True) if n is not None})
        for number, name, figures, votes in rows
    ]


def _sheet_stations(report: Report) -> list[tuple[int, str | None, dict[int, int], list[int]]]:
    """The rows of a commission's spreadsheet of results by polling station, in one of the layouts posted."""
    assert report.stations_url
    grid = _cells(report.stations_url)
    lists = len(report.lists)
    out: list[tuple[int, str | None, dict[int, int], list[int]]] = []
    head = " ".join(grid[0] + (grid[3] if len(grid) > 3 else []))
    if head.startswith("R.B. BIRAČKO MESTO"):  # number, place, station, address, lists, invalid, voters, registered
        for row in grid[1:]:
            if not row[0].isdigit():
                continue
            votes = [_int(v) for v in row[4 : 4 + lists]]
            invalid, voters, registered = (_int(v) for v in row[4 + lists : 7 + lists])
            figures = {REGISTERED: registered, VOTERS: voters, INVALID: invalid, VALID: sum(votes)}
            out.append((int(row[0]), f"{row[1]}, {row[2]}".title(), figures, votes))
    elif "Рбр. бирачког места" in head:  # number, registered, voters, cast, invalid, valid, lists
        for row in grid[4:]:
            if not row[0].isdigit():
                continue
            registered, voters, cast, invalid, valid = (_int(v) for v in row[1:6])
            figures = {REGISTERED: registered, VOTERS: voters, CAST: cast, INVALID: invalid, VALID: valid}
            out.append((int(row[0]), None, figures, [_int(v) for v in row[6 : 6 + lists]]))
    elif grid[0][2] == "Број бирача":  # БМ, number, registered, voters, %, cast, invalid, %, then votes and % per list
        for row in grid:
            if not row[1] or row[0] == "УКУПНО":
                continue
            registered, voters, cast, invalid = (_int(row[i]) for i in (2, 3, 5, 6))
            votes = [_int(row[8 + 2 * i]) for i in range(lists)]
            figures = {REGISTERED: registered, VOTERS: voters, CAST: cast, INVALID: invalid, VALID: cast - invalid}
            out.append((_int(row[1]), None, figures, votes))
    else:
        raise ValueError(f"{report.stations_url}: an unknown layout")
    return out


# geography -----------------------------------------------------------------------------------------

# geoBoundaries' municipalities that the Commission's list names differently
GB_NAMES = {"Nis City": "70858", "Novi Sad City": "89010", "Petrovac-na-Mlavi Municipality": "70912"}


def _gb_key(name: str) -> str:
    return slugify(re.sub(r" (Municipality|City|Municipal\*)$", "", name))


@cache
def shapes() -> dict[str, dict[str, Any]]:
    """geoBoundaries' outline of each municipality and city, by the Commission's code for it."""
    features = json.loads(raw.cached(GEOBOUNDARIES, GEO_URL).path.read_text())["features"]
    codes: dict[str, str] = {}
    for code, name in municipalities().items():
        bare = slugify(_bare(name))
        if code == "80284":  # the Commission's older code for the city of Novi Sad
            continue
        codes.setdefault(bare, code)
    out: dict[str, dict[str, Any]] = {}
    for feature in features:
        name = str(feature["properties"]["shapeName"])
        code = GB_NAMES.get(name) or codes.get(_gb_key(name))
        if code is None:
            continue
        out[code] = feature
    return out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Outlines of Serbia's municipalities and cities from geoBoundaries, for the areas of the Republic Electoral "
        "Commission's list of municipalities",
        depends=[f"{rik.SOURCE}/geo"],
    )
    p.external.add(SERBIA)
    artifact = raw.cached(GEOBOUNDARIES, GEO_URL)
    p.add(
        "source",
        id=GB_SOURCE,
        kind="academic",
        publisher="geoBoundaries (Open Data Watch)",
        title="geoBoundaries gbOpen Serbia ADM2 (municipalities and cities), 2017",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), Open Data Watch; © OpenStreetMap contributors",
    )
    p.artifact(artifact, GB_SOURCE)
    for code, feature in sorted(shapes().items()):
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        shape = shapely.MultiPolygon([g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3])
        shape = shapely.set_srid(shape, 4326)
        p.add(
            "area_geometry",
            area_id=rik.area("rs.opstina", code),
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=GB_SOURCE,
        )
    return p


# registry ------------------------------------------------------------------------------------------

KIND_EN = {"opstina": "Municipal Assembly", "grad": "City Assembly", "go": "City Municipality Assembly"}
KIND_SR = {"opstina": "Скупштина општине", "grad": "Скупштина града", "go": "Скупштина градске општине"}


def kind_of(report: Report) -> str:
    if report.tier == "go":
        return "go"
    return "grad" if municipalities()[report.code].startswith("Град ") else "opstina"


def body(code: str) -> str:
    return key("body", code)


def _genitive(report: Report) -> str:
    """The unit's name in the genitive, as the report names the assembly, or the nominative after a comma."""
    words = report.body.split()
    lead = KIND_SR[kind_of(report)].split()
    if [w.lower() for w in words[: len(lead)]] == [w.lower() for w in lead] and len(words) > len(lead):
        rest = " ".join(words[len(lead) :])
        return rest if rest.isupper() is False else rest.title()
    return report.name


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The municipal, city, and city municipality assemblies of Serbia whose local electoral commissions posted "
        "their results in the Republic Electoral Commission's register, and the local electoral system",
    )
    p.external.add(SERBIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Републичка изборна комисија",
        title="Documents of local electoral commissions, Republic Electoral Commission of Serbia",
        url=f"{BASE}/dokumenta/41985",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Републичка изборна комисија, rik.parlament.gov.rs; local electoral commissions",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=SERBIA,
        valid_from=None,
        valid_to=None,
        rules={"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Members of a municipal, city, or city municipality assembly are elected for four years from closed lists "
            "in one constituency covering the unit, by D'Hondt among the lists that win at least 3% of the voters "
            "who voted; lists of national minorities are exempt from the threshold (Zakon o lokalnim izborima, "
            "2022). The threshold is not recorded in the rules here, because it is a share of the voters, not of the "
            "valid votes."
        ),
        lang="en",
    )
    seen: dict[str, Report] = {}
    for report in reports():
        seen.setdefault(report.code, report)
    for code, report in sorted(seen.items()):
        id = body(code)
        kind = kind_of(report)
        p.add("body", id=id, polity_id=SERBIA, kind="council")
        p.name(id, f"{latin(report.name)} {KIND_EN[kind]}", "en", "official")
        p.name(id, f"{KIND_SR[kind]} {_genitive(report)}", "sr", "native", False)
    return p


# elections -----------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(r.key for r in reports())]


def parse() -> None:
    geo().write()
    registry().write()
    for report in reports():
        build(report).write()


def emit() -> None:
    package.run("load", packages())


@cache
def _sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "999", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def _page_box(url: str, number: int) -> Box:
    """The whole page, for a figure of a scanned table whose cell was not located."""
    width, height = _sizes(raw.cached(SOURCE, url).path)[number]
    return (number, 0.0, 0.0, round(width * 100 / 72, 1), round(height * 100 / 72, 1))


MONTHS = [
    "јануара",
    "фебруара",
    "марта",
    "априла",
    "маја",
    "јуна",
    "јула",
    "августа",
    "септембра",
    "октобра",
    "новембра",
    "децембра",
]


def _native_day(day: date) -> str:
    return f"{day.day}. {MONTHS[day.month - 1]} {day.year}."


def build(report: Report) -> Package:
    kind = kind_of(report)
    name = report.name
    p = Package(
        report.key,
        SOURCE,
        f"The {day_en(report.day)} election of the {latin(name)} {KIND_EN[kind]} from its electoral commission's "
        "report of the total results",
        depends=[f"{SOURCE}/registry", f"{rik.SOURCE}/geo"],
    )
    p.external.add(SERBIA)
    cited: dict[str, str] = {}

    def cite(url: str) -> str:
        if url not in cited:
            cited[url] = p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
        return cited[url]

    cite(report.url)
    if report.stations_url:
        cite(report.stations_url)
    tag = f"{report.day}:{report.code}"
    election = key("election", tag)
    path = f"rs/{report.day}-local-{report.slug}"
    title = f"{report.day.year} {latin(name)} {KIND_EN[kind].lower()} election"
    p.add(
        "election",
        id=election,
        polity_id=SERBIA,
        kind="local",
        date_from=report.day,
        date_to=report.day,
        status="held",
    )
    p.name(election, title, "en", "official")
    p.name(
        election,
        f"Избори за одборнике {KIND_SR[kind].replace('Скупштина', 'Скупштине')} {_genitive(report)} "
        f"{_native_day(report.day)}",
        "sr",
        "native",
        False,
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", tag)
    seats = [r.seats for r in report.lists]
    magnitude = sum(s or 0 for s in seats) if any(s is not None for s in seats) else None
    area = rik.area("rs.opstina", report.code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=body(report.code),
        district_area_id=area,
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{latin(name)} {KIND_EN[kind]}", "en", "official")
    p.add("slug", path=f"{path}/assembly", entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
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
    method = METHOD + (METHOD_STATIONS if report.stations_url else METHOD_TABLE if report.table else "")
    method += (f" {report.note}" if report.note else "") + TABLE_NOTES.get((str(report.day), report.code), "")
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options: dict[int, str] = {}
    for row in report.lists:
        option = options[row.number] = key("option", tag, row.number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=row.number,
            ballot_label=row.name,
        )
        if row.seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
    unit = key("unit", tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=SERBIA,
        scheme_code="rs.opstina",
        code=f"local:{tag}",
        area_relation="equals",
        area_id=area,
        kind="standard",
    )
    p.name(unit, name, "sr", "official")
    rows: list[tuple[str, str | None, int, int]] = []
    places: list[tuple[str, str | None, int, str, Box]] = []  # unit, option, measure, artifact url, box
    found = stations(report) if report.stations_url or report.table else []
    for measure, (value, box) in report.totals.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        if not found:
            rows.append((unit, None, measure, value))
            if box:
                places.append((unit, None, measure, report.url, box))
    for row in report.lists:
        p.add("declared", result_set_id=result_set, option_id=options[row.number], measure=VOTES, value=row.votes)
        if not found:
            rows.append((unit, options[row.number], VOTES, row.votes))
            if row.box:
                places.append((unit, options[row.number], VOTES, report.url, row.box))
    if report.table_url:
        cite(report.table_url)
    for s in found:
        station = key("unit", tag, s.number)
        p.add(
            "reporting_unit",
            id=station,
            polity_id=SERBIA,
            scheme_code="rs.biracko_mesto",
            code=f"local:{tag}:{s.number}",
            parent_id=unit,
            area_relation="within",
            area_id=area,
            kind="standard",
        )
        p.name(station, s.name or f"{name}, бирачко место {s.number}", "sr", "official")
        cells = [(None, measure, value) for measure, value in s.figures.items()]
        cells += [(options[n], VOTES, votes) for n, votes in s.lists.items()]
        for option, measure, value in cells:
            rows.append((station, option, measure, value))
            if report.table_url and s.page:
                places.append((station, option, measure, report.table_url, _page_box(report.table_url, s.page)))
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema=["unit_id", "option_id", "measure", "value"],
            orient="row",
            schema_overrides={"option_id": pl.String},
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    _accept(p, report, found)
    if places:
        pages: dict[tuple[str, int], str] = {}
        for _, _, _, url, (number, *_) in places:
            if (url, number) not in pages:
                artifact = raw.cached(SOURCE, url)
                page = pages[(url, number)] = p.id("page", artifact.sha256, number)
                width, height = _sizes(artifact.path)[number]
                p.add(
                    "source_page",
                    id=page,
                    artifact_id=cite(url),
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{artifact.sha256}-{number}.png",
                    width=round(width * 100 / 72),
                    height=round(height * 100 / 72),
                )
        p.frame(
            "value_locator",
            pl.DataFrame(
                [(u, o, m, pages[(url, b[0])], b[1], b[2], b[3], b[4]) for u, o, m, url, b in places],
                schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
                schema_overrides={"option_id": pl.String},
            ).with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
    return p


def _accept(p: Package, report: Report, found: list[Station]) -> None:
    """Accept the accounting findings that the commission's own printed figures cause, naming each one."""
    totals = {measure: value for measure, (value, _) in report.totals.items()}
    ballots: list[str] = []
    votes: list[str] = []

    def look(where: str, figures: dict[int, int], lists: list[int]) -> None:
        cast, valid, invalid = figures.get(CAST), figures.get(VALID), figures.get(INVALID)
        if cast is not None and valid is not None and invalid is not None and valid + invalid != cast:
            ballots.append(f"{where}: valid {valid} plus invalid {invalid} against {cast} ballots cast")
        if valid is not None and lists and sum(lists) != valid:
            votes.append(f"{where}: the lists' votes sum to {sum(lists)} against {valid} valid ballots")

    look("the unit's totals", totals, [r.votes for r in report.lists])
    for s in found:
        look(f"polling station {s.number}", s.figures, list(s.lists.values()))
    if ballots:
        p.accept(
            "accounting.ballots_sum",
            "The commission's report prints figures that do not add up; stored as printed: " + "; ".join(ballots[:6]),
        )
    if votes:
        p.accept(
            "accounting.votes_valid",
            "The commission's report prints list votes that do not add up to its valid ballots; stored as printed: "
            + "; ".join(votes[:6]),
        )
    if found:
        summed = {m: sum(s.figures.get(m, 0) for s in found) for m in totals if all(m in s.figures for s in found)}
        apart = [f"{m}: stations {summed[m]} against printed {totals[m]}" for m in summed if summed[m] != totals[m]]
        apart += [f"{m}: not printed for every polling station" for m in totals if m not in summed]
        if apart:
            p.accept(
                "accounting.declared",
                "The polling-station table and the report's printed totals differ as printed, or the table leaves a "
                "figure out (measure codes): " + "; ".join(apart),
            )


def day_en(day: date) -> str:
    return f"{day.day} {day.strftime('%B')} {day.year}"
