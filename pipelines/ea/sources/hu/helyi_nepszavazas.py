"""Hungary's local referendums (helyi népszavazások), 1999 to 2024, from the National Election Office.

The office keeps one page per year with a table of every local referendum held: the day, the settlement, the question
put to the voters, and whether the referendum was valid and effective. From 2008 each row links to the scanned
protocol of the local election committee, which prints the electors, voters, ballots, invalid ballots, and the votes for
and against. Those protocols are handwritten scans; their figures are digitized by reading each page image and
are kept in `helyi_nepszavazas_protocols.json` beside this module, with the page they come from. Pages before 2008
print only the table.
"""

import functools
import html
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import cast
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.hu import valasztas as hv

SOURCE = "hu.helyi_nepszavazas"
SITE = "https://www.valasztas.hu"
INDEX = f"{SITE}/teruleti-es-helyi-nepszavazasok"
DOCUMENTS = "https://portal-api.valasztas.hu"
# Page path -> year of the referendums it lists.
PAGES = {
    "2024-helyi-nepszavazasok": 2024,
    "2023-helyi-nepszavazasok": 2023,
    "2022-helyi-nepszavazasok": 2022,
    "4596": 2019,
    "4186": 2018,
    **{str(1811 + i): 2017 - i for i in range(19)},
}


def page_url(path: str) -> str:
    return f"{SITE}/{path}"


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    for path in PAGES:
        raw.fetch(SOURCE, page_url(path), suffix=".html")
    for row in rows():
        if row.document is not None:
            raw.fetch(SOURCE, row.document, suffix=".pdf")
    raw.fetch(SOURCE, LAU, suffix=".geojson")
    raw.fetch(SOURCE, GB_COUNTIES, suffix=".geojson")


class Table(HTMLParser):
    """The rows of the referendum table of one page, with row spans filled in."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, list[str], int]]] = []
        self.cell: list[str] | None = None
        self.links: list[str] = []
        self.span = 1
        self.row: list[tuple[str, list[str], int]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell, self.links = [], []
            self.span = int(values.get("rowspan") or 1)
        elif tag == "a" and self.cell is not None and values.get("href"):
            self.links.append(str(values["href"]))
        elif tag in ("br", "p") and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append((re.sub(r"\s+", " ", "".join(self.cell)).strip(), self.links, self.span))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


@dataclass(frozen=True)
class Row:
    year: int
    page: str
    date_text: str
    place: str
    county: str
    question: str
    status: str
    document: str | None


def grid(page: str) -> list[list[tuple[str, list[str]]]]:
    """The table of a page as full rows: a cell with a row span repeats in the rows it covers."""
    parser = Table()
    parser.feed(raw.cached(SOURCE, page_url(page)).path.read_text(encoding="utf-8"))
    out: list[list[tuple[str, list[str]]]] = []
    carried: dict[int, tuple[tuple[str, list[str]], int]] = {}
    for row in parser.rows:
        full: list[tuple[str, list[str]]] = []
        queue = list(row)
        column = 0
        while queue or column in carried:
            if column in carried:
                cell, left = carried[column]
                full.append(cell)
                if left <= 1:
                    del carried[column]
                else:
                    carried[column] = (cell, left - 1)
            else:
                text, links, span = queue.pop(0)
                full.append((text, links))
                if span > 1:
                    carried[column] = ((text, links), span - 1)
            column += 1
        out.append(full)
    return out


def clean(text: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12,
}  # fmt: skip


def parse_day(text: str, year: int) -> date:
    found = re.fullmatch(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?", text)
    if found:
        out = date(int(found[1]), int(found[2]), int(found[3]))
    else:
        found = re.fullmatch(r"(\d{4})\.\s*([a-záéíóöőúüű]+)\s+(\d{1,2})\.", text)
        if not found or found[2] not in MONTHS:
            raise ValueError(f"unreadable day {text!r}")
        out = date(int(found[1]), MONTHS[found[2]], int(found[3]))
    if out.year != year:
        raise ValueError(f"{text!r} is on the page of {year}")
    return out


# The county spellings the pages use -> the office's county code.
COUNTY_CODES = {
    "baranya": "02", "bács-kiskun": "03", "békés": "04", "borsod-abaúj-zemplén": "05", "borsod-a.-z.": "05",
    "csongrád": "06", "csongrád-csanád": "06", "fejér": "07", "győr-moson-sopron": "08", "hajdú-bihar": "09",
    "heves": "10", "jász-nagykun-szolnok": "11", "jász- nagykun- szolnok": "11", "komárom-esztergom": "12",
    "komárom-e.": "12", "nógrád": "13", "pest": "14", "somogy": "15", "szabolcs-szatmár-bereg": "16",
    "szabolcs-sz-b.": "16", "tolna": "17", "vas": "18", "veszprém": "19", "veszptrém": "19", "zala": "20",
    "budapest": "01", "pestt": "14", "jász-n.-szolnok": "11", "komárom": "12", "borsod-a.z.": "05", "győr-m.-s.": "08",
}  # fmt: skip


def county_code(text: str) -> str:
    plain = re.sub(r"\s+megy?e?$", "", clean(text).lower())
    plain = re.sub(r"\s+főváros$", "", plain)
    return COUNTY_CODES[plain]


def rows() -> list[Row]:
    out: list[Row] = []
    for page, year in PAGES.items():
        table = grid(page)
        head = [clean(cell[0]) for cell in table[0]]
        for row in table[1:]:
            cells = [clean(cell[0]) for cell in row]
            links = [link for cell in row for link in cell[1]]
            document = next((link for link in links if link.lower().endswith(".pdf")), None)
            if document is not None and document.startswith("/"):
                document = DOCUMENTS + document
            parse_day(cells[0], year)  # fail early on a day the page does not own
            if len(head) == 5:  # day, settlement, county, question or subject, outcome
                place, county, question, status = cells[1], cells[2], cells[3], cells[4]
            else:  # day, place with county, question or subject, outcome
                place, county = cells[1], ""
                question, status = cells[2], cells[3]
            out.append(Row(year, page, cells[0], place, county, question, status, document))
    return out


ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}


def roman(text: str) -> int:
    total = 0
    for char, after in zip(text, text[1:] + " ", strict=True):
        value = ROMAN[char]
        total += -value if after != " " and ROMAN.get(after, 0) > value else value
    return total


def split_place(row: Row) -> tuple[str, str]:
    """The settlement name and the county code a row names."""
    place, county = clean(row.place), clean(row.county)
    district = re.search(r"\b([IVXL]+)\.\s*[Kk]erület", place)
    if district:
        return f"Budapest {roman(district[1]):02d}. kerület", "01"
    if not county and "," in place:
        place, county = (part.strip() for part in place.rsplit(",", 1))
    return place, county_code(county) if county else ""


# ---------------------------------------------------------------- packages

HUNGARY = hv.HUNGARY
TERRITORY = hv.TERRITORY
PROTOCOLS = Path(__file__).with_name("helyi_nepszavazas_protocols.json")
LAU = "https://gisco-services.ec.europa.eu/distribution/v2/lau/geojson/LAU_RG_01M_2023_4326.geojson"
GB_COUNTIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/HUN/ADM1/geoBoundaries-HUN-ADM1.geojson"
UNIT_SCHEME = "hu.helyi_nepszavazas"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NVI = key("source", "nvi")
LAU_SOURCE = key("source", "gisco-lau")
GB_SOURCE = key("source", "geoboundaries-adm1")
SYSTEM = key("electoral_system", "local-referendum")
SYSTEM_NOTE = (
    "A local referendum is valid when more than half of the voters on the register take part, and its result binds "
    "the council when more than half of the valid votes give the same answer (Act XXXVI of 2013 on electoral "
    "procedure and earlier local-government law). The committee's protocol declares a referendum invalid "
    "(érvénytelen), valid but without a result (érvényes, de eredménytelen), or valid and with a result (érvényes és "
    "eredményes). The archive records that declaration and the printed figures, and does not recompute them."
)
CANCELED = ("megsemmisítette", "hatályon kívül", "elmaradt")


def settlement_area(code: str) -> str:
    return key("area", "hu.telepules", code)


def county_area(maz: str) -> str:
    return key("area", "hu.megye", maz)


@functools.cache
def places() -> hv.Places:
    for election in ("ogy2026", "ogy2022"):
        raw.fetch(hv.SOURCE, hv.vtr_url(election, "ver", "Telepulesek.json"), suffix=".json")
    return hv.places()


# Places the pages name unlike the office's list: (day text, place) -> (county code, settlement name).
PLACE_FIXES = {
    ("2002. 04.07.", "Köles-Kistormás"): ("17", "Kistormás"),
    ("2012. január 29.", "Balatonvilágos"): ("15", "Balatonvilágos"),
}


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    maz: str


def locate(row: Row) -> Place:
    name, maz = split_place(row)
    fixed = PLACE_FIXES.get((row.date_text, name))
    if fixed:
        maz, name = fixed
    found = places()
    code = found.by_name.get((maz, hv.fold(name))) if maz else None
    if code is None and not maz:
        options = found.national.get(hv.fold(name), set())
        code = next(iter(options)) if len(options) == 1 else None
    if code is None:
        raise LookupError(f"no settlement {name!r} ({row.county!r}) for {row.date_text}")
    return Place(code, found.names[code], code[:2])


def status_of(text: str) -> tuple[str, str]:
    """The election status and the declared result of a status cell."""
    plain = text.lower()
    if any(word in plain for word in CANCELED):
        return "canceled", "not held"
    if "érvénytelen" in plain:
        return "held", "invalid"
    if "eredménytelen" in plain:
        return "held", "valid_ineffective"
    if "eredményes" in plain:
        return "held", "valid_effective"
    if plain.startswith("érvényes"):
        return "held", "valid"
    raise ValueError(f"unknown status {text!r}")


@dataclass
class Event:
    day: date
    place: Place
    shown: str
    rows: list[Row]


def events() -> dict[int, list[Event]]:
    found: dict[tuple[date, str], Event] = {}
    for row in rows():
        day = parse_day(row.date_text, row.year)
        place = locate(row)
        event = found.setdefault((day, place.code), Event(day, place, clean(row.place.split(",")[0]), []))
        event.rows.append(row)
    out: dict[int, list[Event]] = defaultdict(list)
    for event in sorted(found.values(), key=lambda e: (e.day, e.place.code)):
        out[event.day.year].append(event)
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in sorted(events()))]


def hu_day(day: date) -> str:
    names = list(MONTHS)
    return f"{day.year}. {names[day.month - 1]} {day.day}."


def en_day(day: date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


def shapes() -> tuple[dict[str, shapely.Geometry], dict[str, shapely.Geometry]]:
    """County shapes by code and settlement shapes by settlement code, for the settlements the pages name."""

    def shape(feature: dict[str, object]) -> shapely.Geometry:
        found = shapely.make_valid(shapely.geometry.shape(cast(dict[str, object], feature["geometry"])))
        polygons = [
            part
            for member in shapely.get_parts(found)
            for part in shapely.get_parts(member)
            if shapely.get_type_id(part) == 3
        ]
        return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)

    counties: dict[str, shapely.Geometry] = {}
    by_name = {hv.fold(name): code for code, name in hv.COUNTIES.items()}
    for feature in json.loads(raw.cached(SOURCE, GB_COUNTIES).path.read_bytes())["features"]:
        counties[by_name[hv.fold(feature["properties"]["shapeName"])]] = shape(feature)
    lau = [
        feature
        for feature in json.loads(raw.cached(SOURCE, LAU).path.read_bytes())["features"]
        if feature["properties"]["CNTR_CODE"] == "HU"
    ]
    cities: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for feature in lau:
        cities[hv.fold(feature["properties"]["LAU_NAME"])].append(shape(feature))
    budapest = cities.get("budapest")
    if budapest:
        counties["01"] = budapest[0]
    areas: dict[str, shapely.Geometry] = {}
    for found in {event.place for group in events().values() for event in group}:
        if found.maz == "01":
            continue
        options = cities.get(hv.fold(found.name), [])
        if len(options) > 1 and found.maz in counties:
            options = [o for o in options if counties[found.maz].contains(shapely.point_on_surface(o))]
        if len(options) == 1:
            areas[found.code] = options[0]
    return counties, areas


def geometry(p: Package, area_id: str, shape: shapely.Geometry, source: str, quality: str) -> None:
    p.add(
        "area_geometry",
        area_id=area_id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality=quality,
        source_id=source,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The counties and settlements where Hungary's local referendums were held, with shapes where an open source "
        "has them",
    )
    p.external |= {HUNGARY, TERRITORY, "hu.megye", "hu.telepules"}
    p.add(
        "scheme",
        code=UNIT_SCHEME,
        polity_id=HUNGARY,
        kind="precinct",
        rank=5,
        noun="referendum count",
        code_system=None,
    )
    p.add(
        "source",
        id=LAU_SOURCE,
        kind="academic",
        publisher="Eurostat GISCO",
        title="Local administrative units (LAU) 2023, 1:1 million",
        url="https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/local-administrative-units",
        license="Free reuse with the source acknowledged (Eurostat GISCO, © EuroGeographics for the administrative "
        "boundaries)",
        license_url="https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units",
        attribution="© EuroGeographics for the administrative boundaries; Eurostat GISCO",
    )
    p.add(
        "source",
        id=GB_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Hungary ADM1 (counties)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries",
    )
    p.artifact(raw.cached(SOURCE, LAU), LAU_SOURCE)
    p.artifact(raw.cached(SOURCE, GB_COUNTIES), GB_SOURCE)
    counties, areas = shapes()
    used = {event.place for group in events().values() for event in group}
    for maz in sorted({place.maz for place in used}):
        id = county_area(maz)
        p.add("area", id=id, scheme_code="hu.megye")
        p.add("area_code", area_id=id, code=maz)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, "Budapest" if maz == "01" else f"{hv.COUNTIES[maz]} vármegye", "hu", "official")
        if maz in counties:
            geometry(
                p,
                id,
                counties[maz],
                LAU_SOURCE if maz == "01" else GB_SOURCE,
                "official" if maz == "01" else "academic",
            )
    for place in sorted(used, key=lambda x: x.code):
        id = settlement_area(place.code)
        p.add("area", id=id, scheme_code="hu.telepules")
        p.add("area_code", area_id=id, code=place.code)
        p.add("area_parent", area_id=id, parent_id=county_area(place.maz))
        p.name(id, hv.person_text(place.name) if place.name.isupper() else place.name, "hu", "official")
        if place.code in areas:
            geometry(p, id, areas[place.code], LAU_SOURCE, "official")
    for scheme, vintage, as_of in (("hu.megye", "2023", date(2023, 1, 1)), ("hu.telepules", "2023", date(2023, 1, 1))):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", scheme, vintage),
            scheme_code=scheme,
            vintage=vintage,
            as_of=as_of,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The source and electoral system of Hungary's local referendums",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(HUNGARY)
    p.add(
        "source",
        id=NVI,
        kind="official_body",
        publisher="Nemzeti Választási Iroda",
        title="Helyi népszavazások (valasztas.hu)",
        url=INDEX,
        license="Free reuse without alteration, with the source named (valasztas.hu impresszum)",
        license_url="https://www.valasztas.hu/impresszum",
        attribution="Nemzeti Választási Iroda, valasztas.hu",
    )
    p.add("electoral_system", id=SYSTEM, polity_id=HUNGARY, rules={"formula": "referendum"}, rules_version=1)
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


@functools.cache
def protocols() -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], json.loads(PROTOCOLS.read_text(encoding="utf-8")))


def protocol(row: Row) -> dict[str, object] | None:
    if row.document is None:
        return None
    return protocols().get(f"{row.document}|{row.date_text}|{row.place}|{row.question}")


@functools.cache
def page_sizes(path: str) -> dict[int, tuple[int, int]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "999", path], check=True, capture_output=True, text=True).stdout
    sizes: dict[int, tuple[int, int]] = {}
    for found in re.finditer(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", out):
        sizes[int(found[1])] = (round(float(found[2]) * 100 / 72), round(float(found[3]) * 100 / 72))
    return sizes


def short(text: str, limit: int = 160) -> str:
    plain = text.strip().strip('"„”“').strip()
    return plain if len(plain) <= limit else plain[: limit - 1].rstrip() + "…"


def election(year: int) -> Package:
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"Hungary's {year} local referendums",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(HUNGARY)
    page = next(path for path, y in PAGES.items() if y == year)
    p.artifact(raw.cached(SOURCE, page_url(page)), NVI)
    cited: dict[str, tuple[str, str]] = {}  # document url -> (artifact id, sha256)
    page_ids: dict[tuple[str, int], str] = {}

    def page_id(url: str, number: int) -> str:
        artifact = raw.cached(SOURCE, url)
        if url not in cited:
            cited[url] = (p.artifact(artifact, NVI), artifact.sha256)
        if (url, number) not in page_ids:
            width, height = page_sizes(str(artifact.path))[number]
            page_ids[(url, number)] = key("page", artifact.sha256, number)
            p.add(
                "source_page",
                id=page_ids[(url, number)],
                artifact_id=cited[url][0],
                page_number=number,
                image_key=f"pages/{SOURCE}/{artifact.sha256}-{number}.png",
                width=width,
                height=height,
            )
        return page_ids[(url, number)]

    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    units: dict[str, str] = {}
    for event in events()[year]:
        place, day = event.place, event.day
        name = hv.person_text(place.name) if place.name.isupper() else place.name
        statuses = [status_of(row.status) for row in event.rows]
        held = any(status[0] == "held" for status in statuses)
        ident = key("election", day, place.code)
        p.add(
            "election",
            id=ident,
            polity_id=HUNGARY,
            kind="referendum",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held" if held else "canceled",
        )
        p.name(ident, f"{name} local referendum, {en_day(day)}", "en", "english")
        p.name(ident, f"{name} helyi népszavazás, {hu_day(day)}", "hu", "official", False)
        base = f"hu/{day}-{changeset.slug(name)}-local-referendum"
        p.add("slug", path=base, entity_id=ident, is_current=True)
        shown = {clean(row.place) for row in event.rows}
        if len(shown) == 1 and clean(event.rows[0].place).split(",")[0] != name:
            p.add(
                "note",
                id=p.id("note", ident),
                entity_id=ident,
                kind="context",
                body_md=f"The office lists the place as “{clean(event.rows[0].place).split(',')[0]}”.",
                lang="en",
            )
        for number, (row, (status, declared)) in enumerate(zip(event.rows, statuses, strict=True), 1):
            contest = key("contest", day, place.code, number)
            p.add(
                "contest",
                id=contest,
                election_id=ident,
                kind="ballot_question",
                district_area_id=settlement_area(place.code),
                electoral_system_id=SYSTEM,
                round_number=1,
                default_result_kind="counted",
            )
            p.add("ballot_question", contest_id=contest, type="referendum")
            p.name(contest, short(row.question), "hu", "official")
            p.add("slug", path=f"{base}/{number}", entity_id=contest, is_current=True)
            found = protocol(row)
            lines = [f"Question or subject as listed by the office: {row.question.strip()}", ""]
            lines.append(f"Result as listed by the office: {clean(row.status)}.")
            stamp = found.get("stamp") if found else None
            if stamp and stamp != declared:
                lines.append(f"The committee's protocol marks the referendum as {str(stamp).replace('_', ' ')}.")
            if found and found.get("note"):
                lines.append(f"Reading note: {found['note']}")
            p.add(
                "note",
                id=p.id("note", contest, "listing"),
                entity_id=contest,
                kind="context",
                body_md="\n\n".join(line for line in lines if line),
                lang="hu",
            )
            yes = key("option", day, place.code, number, "yes")
            no = key("option", day, place.code, number, "no")
            p.add("contest_option", id=yes, contest_id=contest, kind="answer", ballot_position=1, ballot_label="Igen")
            p.add("contest_option", id=no, contest_id=contest, kind="answer", ballot_position=2, ballot_label="Nem")
            vals = cast(dict[str, dict[str, object]], found["vals"]) if found else {}
            if not vals and not (found and found.get("other")):
                continue
            options = {"yes": yes, "no": no}
            for index, other in enumerate(cast(list[dict[str, object]], found.get("other") or []), 1) if found else []:
                option = key("option", day, place.code, number, "alt", index)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=2 + index,
                    ballot_label=str(other["label"]),
                )
                options[f"alt{index}"] = option
            result_set = key("result_set", day, place.code, number)
            p.add(
                "result_set",
                id=result_set,
                election_id=ident,
                contest_id=contest,
                stage="certified",
                kind="counted",
                source_id=NVI,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            unit_code = f"{day}:{place.code}"
            if unit_code not in units:
                units[unit_code] = key("unit", unit_code)
                p.add(
                    "reporting_unit",
                    id=units[unit_code],
                    polity_id=HUNGARY,
                    scheme_code=UNIT_SCHEME,
                    code=unit_code,
                    area_relation="within" if any(PART.search(r.question) for r in event.rows) else "equals",
                    area_id=settlement_area(place.code),
                    kind="standard",
                )
                p.name(units[unit_code], name, "hu", "official")
            unit = units[unit_code]
            url = cast(str, found["document"]) if found else ""

            def put(option: str | None, measure: int, field: dict[str, object]) -> None:
                values.append((result_set, unit, option, measure, int(cast(int, field["v"]))))
                x0, y0, x1, y1 = cast(list[float], field["box"])
                locators.append(
                    (result_set, unit, option, measure, page_id(url, int(cast(int, field["page"]))), x0, y0, x1, y1)
                )

            if "electors" in vals:
                put(None, REGISTERED, vals["electors"])
            if "in_urn" in vals:
                put(None, CAST, vals["in_urn"])
            if "valid" in vals:
                put(None, VALID, vals["valid"])
            if "invalid" in vals:
                put(None, INVALID, vals["invalid"])
            if "yes" in vals:
                put(yes, VOTES, vals["yes"])
            if "no" in vals:
                put(no, VOTES, vals["no"])
            for index, other in enumerate(cast(list[dict[str, object]], found.get("other") or []), 1) if found else []:
                put(options[f"alt{index}"], VOTES, other)
            if declared == "valid_effective" and "yes" in vals and "no" in vals:
                yes_votes, no_votes = int(cast(int, vals["yes"]["v"])), int(cast(int, vals["no"]["v"]))
                if yes_votes != no_votes:
                    first, second = (yes, no) if yes_votes > no_votes else (no, yes)
                    p.add("outcome", result_set_id=result_set, option_id=first, kind="won")
                    p.add("outcome", result_set_id=result_set, option_id=second, kind="lost")
    if values:
        p.frame(
            "result_value",
            pl.DataFrame(
                values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
        p.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema=["result_set_id", "unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
    return p


# Questions that put a part of a settlement to the vote: the voters counted are those of that part.
PART = re.compile(
    r"településrész|területrész|városrész|lakott terület|Margitsziget|Szőlőhegy|dűlő|Nevelek|Oros|Bokros|Tapolca|Mözs",
    re.IGNORECASE,
)


def parse() -> None:
    geo().write()
    registry().write()
    for year in events():
        election(year).write()


def emit() -> None:
    package.run("load", packages())
