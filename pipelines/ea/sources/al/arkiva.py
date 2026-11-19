"""Albania's parliamentary elections of 2013 and earlier from the Central Election Commission's archive.

The Commission's archive (arkiva.kqz.gov.al, formerly cec.org.al) holds each earlier parliamentary election as a page of
linked files. For 23 June 2013 it publishes the final result by electoral zone (the 12 counties, qarqe) as annex 1 of
decision 759 of 6 August 2013, the national result as annex 2, and one summary table for each county with the
accounting of the ballots. Each file is a PDF with a text layer.

Annex 1 prints, for each county, the votes of each of the 66 lists in the order of the ballot, the independent
candidates of the county, and the seats each list won. Annex 2 prints the same figures for the whole country. The
county tables print the registered voters, the voters, and the valid and invalid votes. The archive holds no table for
Vlora county, so its accounting is not stored.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl
import pymupdf
import shapely

from ea import package, raw
from ea.package import Package

SOURCE = "al.arkiva"
SITE = "https://arkiva.kqz.gov.al"
ALBANIA = str(uuid5(package.NAMESPACE, "polity\x1fAL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fALB"))
COUNTIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ALB/ADM1/"
    "geoBoundaries-ALB-ADM1.geojson"
)
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
ASSEMBLY = key("body", "assembly")
DAY = date(2013, 6, 23)
ELECTION_KEY = f"{SOURCE}/{DAY}"
NAMES = ("2013 Albanian parliamentary election", "Zgjedhjet për Kuvendin, 2013")

STAGE = "browser://20261117-205927-d7adf0"
ANNEX_1 = f"{STAGE}/lidhja1.pdf"
ANNEX_2 = f"{STAGE}/lidhja_2.pdf"
COUNTY_FILES = {
    "BERAT": "berat",
    "DIBËR": "diber",
    "DURRËS": "durresi",
    "ELBASAN": "elbasani",
    "FIER": "fieri",
    "GJIROKASTËR": "gjirokaster",
    "KORÇË": "korca",
    "KUKËS": "kukesi",
    "LEZHË": "lezha",
    "SHKODËR": "shkodra",
    "TIRANË": "tirana",
}
COUNTY_ORDER = (
    "BERAT",
    "DIBËR",
    "DURRËS",
    "ELBASAN",
    "FIER",
    "GJIROKASTËR",
    "KORÇË",
    "KUKËS",
    "LEZHË",
    "SHKODËR",
    "TIRANË",
    "VLORË",
)
METHOD = (
    "The final result of each of the 12 counties as the Commission approved it by decision 759 of 6 August 2013: the "
    "votes of each list and independent candidate and the seats each list won, stored as declared. Each county is one "
    "contest and its result is one aggregate unit. The county summary tables add the registered voters, the voters "
    "(participation), and the valid and invalid votes where a county's summary table agrees with the final result; "
    "the summary tables of Kukës and Lezhë print other votes than the final result, and the archive holds none for "
    "Vlora, so those figures are missing there. The votes by electoral zone below the county are not part of this package."
)


def url(county: str) -> str:
    return f"{STAGE}/{COUNTY_FILES[county]}.pdf"


def files() -> list[str]:
    return [ANNEX_1, ANNEX_2, *(url(c) for c in COUNTY_FILES)]


def fetch() -> None:
    raw.fetch(SOURCE, COUNTIES, suffix=".geojson")
    for address in files():
        raw.cached(SOURCE, address)  # the archive's own files are staged in the raw cache; see parse()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", ELECTION_KEY]


def parse() -> None:
    data = read()
    geo().write()
    registry(data).write()
    election(data).write()


def emit() -> None:
    package.run("load", packages())


# Reading the files.


def clean(text: object) -> str:
    return " ".join(str(text).replace("\xa0", " ").split())


def fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", clean(text)).upper().split())


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def title(text: str) -> str:
    return " ".join(word.capitalize() for word in fold(text).split())


def number(text: str) -> int:
    return int(text.replace(",", "").replace(".", ""))


@dataclass
class Line:
    position: int
    name: str
    abbreviation: str
    votes: int
    seats: int


@dataclass
class Table:
    county: str | None
    lines: list[Line] = field(default_factory=list)
    total: tuple[int, int] | None = None  # votes, seats of the line Gjithsej
    alliances: list[tuple[int, int]] = field(default_factory=list)  # the sums printed for the two alliances


def table(text: str) -> Table:
    """One results table: the lists in ballot order, each with its votes and seats, and the totals."""
    lines = [clean(x) for x in text.split("\n")]
    lines = [x for x in lines if x]
    found = re.search(r"QARKU ([^\n]+)", text)
    out = Table(fold(found.group(1)) if found else None)
    expected, i = 1, 0
    while i < len(lines):
        head = re.match(r"^(\d+) (.+)$", lines[i])
        if head and int(head.group(1)) == expected:
            name = [head.group(2)]
            i += 1
            while not re.fullmatch(r"\d+", lines[i]):
                name.append(lines[i])
                i += 1
            abbreviation = name.pop() if len(name) > 1 else ""
            votes, seats = int(lines[i]), int(lines[i + 2])
            out.lines.append(Line(expected, " ".join(name), abbreviation, votes, seats))
            expected += 1
            i += 3
        elif lines[i] in ("Shuma", "Gjithsej") and i + 3 < len(lines) and re.fullmatch(r"\d+", lines[i + 1]):
            if re.fullmatch(r"\d+", lines[i + 3]):
                pair = (int(lines[i + 1]), int(lines[i + 3]))
                if lines[i] == "Gjithsej":
                    out.total = pair
                else:
                    out.alliances.append(pair)
                i += 4
            else:
                i += 1
        else:
            i += 1
    return out


@dataclass
class Accounting:
    registered: int
    voters: int
    invalid: int
    valid: int
    votes: dict[int, int]  # the votes of each list as the county's own table prints them


def summary(county: str) -> Accounting:
    text = pymupdf.open(raw.cached(SOURCE, url(county)).path)[0].get_text()
    rows = [clean(x) for x in text.split("\n")]
    labels = {
        "Lista e Zgjedhësve",
        "Pjesëmarrja në votim",
        "Fletë votimi të papërdorura",
        "Fletë votimi të dëmtuara",
        "Fletë votimi të gjetura në kuti",
        "Vota të pavlefshme",
        "Vota të vlefshme",
        "Gjithsej",
    }
    start = rows.index("Gjithsej")
    figures: list[int] = []
    for x in rows[start + 1 :]:
        if re.fullmatch(r"\d+", x):
            figures.append(int(x))
        elif x not in labels:
            break
    if len(figures) not in (7, 8):
        raise ValueError(f"{county}: {len(figures)} accounting figures, expected 7 or 8")
    votes: dict[int, int] = {}
    after = rows[rows.index("Vota") + 1 :]
    i = 0
    while i < len(after) and len(votes) < 66:
        head = re.match(r"^(\d+) (.+)$", after[i])
        if head and int(head.group(1)) == len(votes) + 1:
            i += 1
            while not re.fullmatch(r"\d+", after[i]):
                i += 1
            votes[len(votes) + 1] = int(after[i])
        i += 1
    return Accounting(figures[0], figures[-6], figures[-2], figures[-1], votes)


@dataclass
class Data:
    counties: dict[str, Table]
    national: Table
    accounting: dict[str, Accounting]
    names: dict[int, tuple[str, str]]  # ballot position -> (name, abbreviation)


def read() -> Data:
    pages = pymupdf.open(raw.cached(SOURCE, ANNEX_1).path)
    counties: dict[str, Table] = {}
    for index in range(0, len(pages), 2):
        t = table(pages[index].get_text())
        if t.county is None:
            raise ValueError(f"annex 1 page {index + 1} names no county")
        counties[t.county] = t
    if sorted(counties) != sorted(COUNTY_ORDER):
        raise ValueError(f"annex 1 holds the counties {sorted(counties)}")
    national = table(pymupdf.open(raw.cached(SOURCE, ANNEX_2).path)[0].get_text())
    names: dict[int, tuple[str, str]] = {}
    for t in counties.values():
        for line in t.lines:
            if line.position <= 66:
                names.setdefault(line.position, (line.name, line.abbreviation))
    accounting = {county: summary(county) for county in COUNTY_FILES}
    for county in compare(counties, national, accounting):
        del accounting[county]
    return Data(counties, national, accounting, names)


def compare(counties: dict[str, Table], national: Table, accounting: dict[str, Accounting]) -> list[str]:
    """The counties against the national table, and the accounting tables against the counties' votes.

    Returns the counties whose summary table prints other votes than annex 1; those tables predate the final result,
    so their accounting is left out."""
    for t in counties.values():
        if t.total is None:
            t.total = (sum(x.votes for x in t.lines), sum(x.seats for x in t.lines))
        if t.total != (sum(x.votes for x in t.lines), sum(x.seats for x in t.lines)):
            raise ValueError(f"{t.county}: the lists add up to other totals than the table prints")
    for line in national.lines:
        votes = sum(x.votes for t in counties.values() for x in t.lines if x.position == line.position)
        seats = sum(x.seats for t in counties.values() for x in t.lines if x.position == line.position)
        if line.position <= 66 and (votes, seats) != (line.votes, line.seats):
            raise ValueError(f"list {line.position} has {votes} votes and {seats} seats in the counties, {line}")
    if national.total is None or national.total != (
        sum(t.total[0] for t in counties.values() if t.total),
        sum(t.total[1] for t in counties.values() if t.total),
    ):
        raise ValueError("the counties do not add up to the national totals")
    differ: list[str] = []
    for county, a in accounting.items():
        wrong = [
            position
            for position, votes in a.votes.items()
            if next(x.votes for x in counties[county].lines if x.position == position) != votes
        ]
        if wrong:
            print(f"{county}: the summary table differs from annex 1 in {len(wrong)} lists; its accounting is left out")
            differ.append(county)
    return differ


# Places.


def area(county: str) -> str:
    return key("area", "al.qarku", county)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Albania's 12 counties (qarqe) as the Central Election Commission's archive lists them for the parliamentary "
        "elections of 2013 and earlier",
    )
    p.external |= {ALBANIA, COUNTRY, "al.qarku"}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Komisioni Qendror i Zgjedhjeve",
        title="Archive of parliamentary elections",
        url=f"{SITE}/zgjedhje-parlamentare/",
        license="Official election results of the Republic of Albania, published for public use",
        license_url=f"{SITE}/",
        attribution="Central Election Commission of the Republic of Albania, arkiva.kqz.gov.al",
    )
    shapes = key("source", "geoboundaries-adm1")
    p.add(
        "source",
        id=shapes,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Albania ADM1 (counties)",
        url="https://www.geoboundaries.org/",
        license="Public Domain",
        license_url="https://www.geoboundaries.org/",
        attribution="geoBoundaries (William & Mary geoLab)",
        notes="Boundary year 2021, built December 2023, from Wikimedia Commons. Generalized shapes. "
        "The 12 counties have had these borders since 2000.",
    )
    p.artifact(raw.cached(SOURCE, COUNTIES), shapes)
    outlines: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, COUNTIES).path.read_bytes())["features"]:
        found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        polygons = [
            q
            for part in shapely.get_parts(found)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        outlines[fold(feature["properties"]["shapeName"])] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    if missing := set(COUNTY_ORDER) - set(outlines):
        raise ValueError(f"no shape for the counties {sorted(missing)}")
    for name in COUNTY_ORDER:
        id = area(name)
        p.add("area", id=id, scheme_code="al.qarku")
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=None,
            geom=shapely.to_wkb(outlines[name], hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outlines[name]), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=shapes,
        )
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, title(name), "sq", "official")
    return p


# Parties and the Assembly.


def party(name: str) -> str:
    return key("party", fold(name))


def registry(data: Data) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Assembly of Albania and the parties on parliamentary ballots from the Central Election Commission's archive",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ALBANIA, COUNTRY}
    p.add("body", id=ASSEMBLY, polity_id=ALBANIA, kind="unicameral")
    p.name(ASSEMBLY, "Assembly of the Republic of Albania", "en", "official")
    p.name(ASSEMBLY, "Kuvendi i Republikës së Shqipërisë", "sq", "native", False)
    for name, _ in sorted(data.names.values()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=ALBANIA)
        p.name(id, name, "sq", "official")
    return p


# The election.


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


def election(data: Data) -> Package:
    p = Package(
        ELECTION_KEY,
        SOURCE,
        f"The {NAMES[0]} by county from the Central Election Commission's archive",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ALBANIA, COUNTRY, "al.qarku"}
    for address in files():
        p.artifact(raw.cached(SOURCE, address), SOURCE_ID)
    election_id = key("election", DAY)
    p.add("election", id=election_id, polity_id=ALBANIA, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, NAMES[0], "en", "official")
    p.name(election_id, NAMES[1], "sq", "native", False)
    path = f"al/{DAY}-parliamentary"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Facts()
    for county in COUNTY_ORDER:
        t = data.counties[county]
        contest = key("contest", DAY, county)
        result_set = key("result_set", DAY, county)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=ASSEMBLY,
            district_area_id=area(county),
            magnitude=sum(x.seats for x in t.lines),
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{NAMES[0]}, {title(county)} County", "en", "official")
        p.name(contest, f"Zgjedhjet për Kuvendin, Qarku {title(county)}", "sq", "native", False)
        p.add("slug", path=f"{path}/{plain(county).replace(' ', '-')}", entity_id=contest, is_current=True)
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
        unit = key("unit", f"{DAY}:{county}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ALBANIA,
            scheme_code="al.qarku",
            code=f"{DAY}:{county}",
            area_relation="within",
            area_id=area(county),
            kind="aggregate",
        )
        accounting = data.accounting.get(county)
        if accounting:
            for measure, n in (
                (REGISTERED, accounting.registered),
                (VOTERS, accounting.voters),
                (VALID, accounting.valid),
                (INVALID, accounting.invalid),
            ):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=n)
                values.add(result_set, unit, None, measure, n)
        for line in t.lines:
            option = key("option", DAY, county, line.position)
            independent = line.position > 66
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="independents" if independent else "list",
                party_id=None if independent else party(line.name),
                ballot_position=line.position,
                ballot_label=line.name,
            )
            values.add(result_set, unit, option, VOTES, line.votes)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=line.votes)
            if line.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=line.seats)
    p.frame("result_value", values.frame())
    off = [
        f"{title(c)} ({sum(x.votes for x in data.counties[c].lines)} votes added up, {a.valid} printed as valid)"
        for c, a in data.accounting.items()
        if a.valid != sum(x.votes for x in data.counties[c].lines)
    ]
    if off:
        p.accept(
            "accounting.votes_valid",
            "The Commission's own county summary tables print a valid-votes total that differs from the sum of the "
            "lists' votes, which agree with its final result: " + ", ".join(off),
        )
    print(f"{ELECTION_KEY}: {len(data.counties)} counties", flush=True)
    return p
