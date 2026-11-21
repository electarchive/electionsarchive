"""Georgia's local elections of 2010 and 2014 by municipality, from the results sites of the Central Election
Commission.

The Commission published the results of each local election on a site of its own (results2010.cec.gov.ge and
results2014.cec.gov.ge), which no longer answers scripted clients. The pages read here are copies of them (the
Internet Archive's captures of 2019 and 2020 and pages saved in a browser from those captures), stored in the cache
under the address they were fetched from.

- The 2014 landing page prints, for Tbilisi, the mayoral votes of each of its ten districts, and for the eleven
  self-governing cities, the mayoral votes of each precinct.
- The 2014 page of the party-list vote prints each district's votes by list, one row for each sakrebulo: ten districts
  of Tbilisi, and for every other municipality the sakrebulo of the municipality, with a second row for the city's own
  sakrebulo where a self-governing city lies in it.
- The 2014 gamgebeli page prints each precinct's votes for the candidates for gamgebeli of the 59 municipalities.
- The 2010 pages print the votes by list of each district's sakrebulo and the votes for the mayor of Tbilisi by
  district.

A reporting unit is a district of the Commission: the sakrebulo district or, for a mayor or a gamgebeli, the
municipality or city the candidate governs. Where the Commission prints precincts, the unit's votes are their sum.
The pages print the votes and the share each won, and no turnout, no valid votes, and no seats, so these are the
only figures stored.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ge import cec_1990s, respublika

SOURCE = "ge.cec_local"
GEORGIA = str(uuid5(package.NAMESPACE, "polity\x1fGE"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGEO"))
AREA_SCHEME = "ge.municipality_2010"
UNIT_SCHEME = "ge.local_district"
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
PLAN_FROM, PLAN_TO = date(2010, 5, 30), date(2017, 10, 21)
"""The plan the shapes belong to: the municipalities of the local elections of 2010 and 2014."""
VOTES = 1
DAY_2010, DAY_2014 = date(2010, 5, 30), date(2014, 6, 15)
HOME_2010, HOME_2014 = "https://results2010.cec.gov.ge/", "https://results2014.cec.gov.ge/"
ADM2, OSM = respublika.ADM2, respublika.OSM


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC = key("source", "cec-results-sites")
GEO_SOURCE = key("source", "geoboundaries-adm2")
OSM_SOURCE = key("source", "osm-cities")
COUNCILS = key("body", "municipal-councils")
MAYOR = key("office", "mayor")
GAMGEBELI = key("office", "gamgebeli")
BOUNDARY_SET = key("boundary_set", AREA_SCHEME)
TBILISI_AREA = key("area", AREA_SCHEME, "tbilisi")
ELECTIONS = {
    DAY_2010: cec_1990s.key("election", "ge/2010-05-30-local"),
    DAY_2014: cec_1990s.key("election", "ge/2014-06-15-local"),
}
"""The elections of the Commission's volume, which the contests of this module attach to."""
CITIES_2014 = {
    17: "თელავი",
    20: "რუსთავი",
    27: "მცხეთა",
    32: "გორი",
    37: "ახალციხე",
    44: "ამბროლაური",
    59: "ქუთაისი",
    60: "ოზურგეთი",
    67: "ზუგდიდი",
    70: "ფოთი",
    79: "ბათუმი",
}
"""The self-governing cities whose mayors were elected on 15 June 2014."""


def packages() -> list[str]:
    return [GEO, REGISTRY, f"{SOURCE}/{DAY_2010}", f"{SOURCE}/{DAY_2014}"]


# ---------------------------------------------------------------------------------------------------------------------
# Reading the pages


def page(name: str) -> raw.Artifact:
    """The cached copy of a page of the Commission's sites: `name` is its address, or the end of it."""
    found = sorted(k for k in raw.keys(SOURCE) if k == name or k.endswith((f"/{name}", f"{name}")))
    exact = [k for k in found if k == name] or found
    if not exact:
        raise KeyError(f"no cached page matches {name}")
    # A page may be cached twice, as a capture of the Internet Archive and as a page saved from one: read the later.
    return max((raw.cached(SOURCE, k) for k in exact), key=lambda a: a.retrieved_at)


def read(name: str) -> str:
    return page(name).path.read_text(encoding="utf-8", errors="replace")


def clean(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ")).strip()


def sections(html: str) -> list[tuple[str, list[list[str]]]]:
    """Each table of the page as (the text of its first row, its rows as lists of cells' markup)."""
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
    found = []
    for table in re.split(r"(?=<table)", html):
        rows = [
            re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.DOTALL)
        ]
        found.append((clean(table), rows))
    return found


def votes(cell: str) -> int | None:
    match = re.match(r"\s*(\d+)", clean(cell))
    return int(match.group(1)) if match else None


def candidate(cell: str) -> tuple[int, str | None] | None:
    """A header cell's ballot number and the name of the candidate or list in its tooltip."""
    number = clean(cell)
    if not number.isdigit():
        return None
    title = re.search(r'title="([^"]*)"', cell)
    return int(number), re.sub(r"\s+", " ", title.group(1)).strip() if title else None


@dataclass(frozen=True)
class Line:
    district: int
    name: str
    part: str | None  # "city" or "community" where the page says which council or executive
    votes: dict[int, int]


@dataclass(frozen=True)
class Table:
    header: dict[int, str | None]
    lines: list[Line]


def district_table(html: str, kind: bool) -> Table:
    """A table of district rows (`11. Sagarejo`) with the votes of each numbered list or candidate."""
    header: dict[int, str | None] = {}
    lines: list[Line] = []
    start = 0
    for _, rows in sections(html):
        for cells in rows:
            head = [candidate(cell) for cell in cells]
            if sum(h is not None for h in head) >= 3 and not lines:
                header = {h[0]: h[1] for h in head if h is not None}
                start = next(i for i, h in enumerate(head) if h is not None)
                columns = [h[0] if h is not None else None for h in head]
                continue
            match = re.match(r"(\d+)\.\s*(.+)", clean(cells[0])) if cells else None
            if not match or not header or len(cells) != len(columns):
                continue
            part = None
            if kind:
                part = {"ქალაქის": "city", "თემის": "community"}[clean(cells[1])]
            counted = {
                number: value
                for number, cell in zip(columns[start:], cells[start:], strict=True)
                if number is not None and (value := votes(cell)) is not None
            }
            lines.append(Line(int(match.group(1)), match.group(2).strip(), part, counted))
    return Table(header, lines)


def precinct_tables(html: str) -> dict[str, tuple[dict[int, str | None], dict[int, int], int]]:
    """The tables of precinct rows: by the name that heads each, its candidates, its summed votes, and the precincts
    it prints (the heading says how many the Commission counted)."""
    found: dict[str, tuple[dict[int, str | None], dict[int, int], int]] = {}
    name = ""
    expected = 0
    for text, rows in sections(html):
        head = re.findall(r"(\S+) \( მონაცემები (\d+) უბნის", text)
        if head:
            name, expected = head[-1][0], int(head[-1][1])
        if not rows:
            continue
        header = [candidate(cell) for cell in rows[0]]
        if not name or sum(h is not None for h in header) < 1 or clean(rows[0][-1]) != "ოქმი":
            continue
        columns = [h[0] if h is not None else None for h in header]
        total: dict[int, int] = {}
        counted = marked = empty = 0
        for cells in rows[1:]:
            if len(cells) != len(columns) or not clean(cells[0]):
                continue
            seen = False
            for number, cell in zip(columns, cells, strict=True):
                if number is not None and (value := votes(cell)) is not None:
                    total[number] = total.get(number, 0) + value
                    seen = True
            if seen:
                counted += 1
            elif re.search("გაბათილ|არ შედგა", clean(cells[0])):
                marked += 1  # a precinct annulled, or where no vote was held, is counted in the heading
            else:
                empty += 1  # a precinct attached to another prints an empty row
        if expected not in (counted, counted + marked, counted + marked + empty):
            raise ValueError(f"{name}: {counted} precinct rows, the heading says {expected}")
        count = counted
        found[name] = ({h[0]: h[1] for h in header if h is not None}, total, count)
        name = ""
    return found


def tbilisi_mayor(html: str) -> Table:
    return district_table(html, False)


# ---------------------------------------------------------------------------------------------------------------------
# What the pages hold


def list_2014() -> Table:
    return district_table(read("proporciuli.html"), True)


def list_2010() -> Table:
    return district_table(read("olqebi_prop.html"), False)


def mayor_tbilisi_2014() -> Table:
    return district_table(read("meri_tbilisi.html"), False)


def mayor_tbilisi_2010() -> Table:
    return district_table(read("olqebi_meri.html"), False)


def city_mayors_2014() -> dict[int, tuple[dict[int, str | None], dict[int, int], int]]:
    """The mayoral votes of the eleven self-governing cities, by district number."""
    tables = precinct_tables(read(HOME_2014_BROWSER))
    by_name = {name: number for number, name in CITIES_2014.items()}
    return {by_name[name]: table for name, table in tables.items() if name in by_name}


HOME_2014_BROWSER = "http://results2014.cec.gov.ge/"


def gamgebeli_2014() -> dict[int, tuple[dict[int, str | None], dict[int, int], int]]:
    """The votes for gamgebeli of each municipality, by district number, with the candidates' names from the pages of
    the municipalities that print them."""
    names = {line.name: line.district for line in list_2014().lines if line.part == "community"}
    tables = precinct_tables(read("ge-2014-local-gamgebeli-municipalities.html"))
    out = {}
    for name, (header, total, count) in tables.items():
        if name == "გამგებელი":
            continue
        out[names[name]] = (header, total, count)
    for district in sorted(out):
        try:
            more = precinct_tables(read(f"gamgebeli_{district}.html"))
        except KeyError:
            continue
        for named, (header, _, _) in more.items():
            if names.get(named) == district:
                out[district] = (
                    {n: header.get(n) or out[district][0].get(n) for n in out[district][0]},
                    *out[district][1:],
                )
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Areas and units


def english(district: int) -> str:
    if district in respublika.TBILISI_DISTRICTS:
        return f"{respublika.TBILISI_DISTRICTS[district]} district"
    if district in respublika.CITIES:
        return respublika.CITIES[district][0]
    return respublika.RAYONS[district][1]


def area(district: int) -> str:
    return key("area", AREA_SCHEME, district)


def areas() -> dict[int, str]:
    """The Commission's district numbers that voted in 2010 or 2014, with their names as the pages print them."""
    names: dict[int, str] = {}
    for table in (list_2014(), list_2010()):
        for line in table.lines:
            names.setdefault(line.district, line.name)
    return names


def unit(day: date, district: int, part: str | None) -> str:
    return key("unit", day, district, part)


def code(day: date, district: int, part: str | None) -> str:
    return f"{day.year}:{district}" + (f".{part}" if part else "")


def osm_shapes() -> dict[int, shapely.Geometry]:
    districts = {str(rel): d for d, rel in respublika.OSM_RELATIONS.items()}
    out: dict[int, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, OSM).path.read_text(encoding="utf-8"))["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        out[districts[str(feature["properties"]["osm_id"])]] = respublika.polygons(shape)
    if set(out) != set(respublika.OSM_RELATIONS):
        raise ValueError(f"OpenStreetMap outlines for {sorted(out)}, expected {sorted(respublika.OSM_RELATIONS)}")
    return out


def fetch() -> None:
    # The Commission's sites refuse scripted clients; its pages reached the cache with raw.store from copies.
    raw.fetch(SOURCE, ADM2, suffix=".geojson")
    raw.fetch(SOURCE, OSM, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    for day in (DAY_2010, DAY_2014):
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The municipalities of Georgia's local elections of 2010 and 2014 as outlined by geoBoundaries, with the "
        "districts of Tbilisi and four cities outlined from OpenStreetMap",
    )
    p.external.add(GEORGIA)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Georgia ADM2",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), CC BY 4.0",
    )
    adm2 = raw.cached(SOURCE, ADM2)
    p.artifact(adm2, GEO_SOURCE)
    p.add(
        "source",
        id=OSM_SOURCE,
        kind="academic",
        publisher="OpenStreetMap contributors",
        title="OpenStreetMap administrative boundaries of the districts of Tbilisi and the cities of Rustavi, Kutaisi, "
        "Poti, and Batumi",
        url="https://www.openstreetmap.org/",
        license="ODbL 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1.0/",
        attribution="© OpenStreetMap contributors, ODbL",
    )
    p.artifact(raw.cached(SOURCE, OSM), OSM_SOURCE)
    p.add(
        "scheme",
        code=AREA_SCHEME,
        polity_id=GEORGIA,
        kind="administrative",
        rank=1,
        noun="municipality",
        code_system=None,
    )
    outlines: dict[str, shapely.Geometry] = {}
    for feature in json.loads(adm2.path.read_text(encoding="utf-8"))["features"]:
        name = feature["properties"]["shapeName"]
        if name in outlines:
            raise ValueError(f"geoBoundaries holds two ADM2 outlines named {name}")
        outlines[name] = shapely.geometry.shape(feature["geometry"])
    cities = osm_shapes()
    taken = shapely.union_all(list(cities.values()))
    for name, outline in outlines.items():
        outlines[name] = respublika.carve(outline, taken)

    def shape(id: str, outline: shapely.Geometry, source: str, quality: str) -> None:
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=PLAN_FROM,
            valid_to=PLAN_TO,
            geom=shapely.to_wkb(shapely.set_srid(outline, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.point_on_surface(shapely.set_srid(outline, 4326)), hex=True, include_srid=True
            ),
            quality=quality,
            source_id=source,
        )

    for district, name in sorted(areas().items()):
        id = area(district)
        p.add("area", id=id, scheme_code=AREA_SCHEME)
        p.add("area_code", area_id=id, code=str(district), valid_from=None, valid_to=None)
        p.name(id, english(district), "en", "english")
        p.name(id, name, "ka", "official")
        if district in cities:
            shape(id, cities[district], OSM_SOURCE, "osm")
        else:
            shape(id, outlines[respublika.RAYONS[district][0]], GEO_SOURCE, "academic")
    p.add("area", id=TBILISI_AREA, scheme_code=AREA_SCHEME)
    p.add("area_code", area_id=TBILISI_AREA, code="tbilisi", valid_from=None, valid_to=None)
    p.name(TBILISI_AREA, "Tbilisi", "en", "english")
    p.name(TBILISI_AREA, "თბილისი", "ka", "official")
    shape(TBILISI_AREA, shapely.union_all([cities[d] for d in respublika.TBILISI_DISTRICTS]), OSM_SOURCE, "osm")
    p.add(
        "boundary_set",
        id=BOUNDARY_SET,
        scheme_code=AREA_SCHEME,
        vintage="geoBoundaries gbOpen ADM2, read as the municipalities of the local elections of 2010 and 2014, with "
        "the cities and the districts of Tbilisi outlined from OpenStreetMap and cut out of them",
        valid_from=PLAN_FROM,
        valid_to=PLAN_TO,
        as_of=PLAN_FROM,
    )
    return p


def pages() -> Iterable[tuple[str, raw.Artifact]]:
    for name in (
        "proporciuli.html",
        "meri_tbilisi.html",
        HOME_2014_BROWSER,
        "ge-2014-local-gamgebeli-municipalities.html",
        "olqebi_prop.html",
        "olqebi_meri.html",
    ):
        yield name, page(name)
    for district in range(11, 16):
        yield f"gamgebeli_{district}.html", page(f"gamgebeli_{district}.html")


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Georgia's local election districts of 2010 and 2014, the municipal councils, the offices of mayor and "
        "gamgebeli, and the lists of 2014",
        depends=[GEO],
    )
    p.external |= {GEORGIA, COUNTRY}
    p.add(
        "source",
        id=CEC,
        kind="official_body",
        publisher="Central Election Commission of Georgia",
        title="Results of the local elections of 30 May 2010 and 15 June 2014 (results2010.cec.gov.ge and "
        "results2014.cec.gov.ge), as archived pages",
        url=HOME_2014,
        license="No license stated; published by the electoral authority for public use",
        attribution="Central Election Commission of Georgia",
    )
    for _, artifact in pages():
        p.artifact(artifact, CEC)
    p.add("body", id=COUNCILS, polity_id=GEORGIA, kind="council")
    p.name(COUNCILS, "Municipal councils (sakrebulo) of Georgia", "en", "english")
    p.name(COUNCILS, "მუნიციპალიტეტების საკრებულოები", "ka", "official")
    for id, text_en, text_ka in (
        (MAYOR, "Mayor", "მერი"),
        (GAMGEBELI, "Gamgebeli of a municipality", "მუნიციპალიტეტის გამგებელი"),
    ):
        p.add("office", id=id, polity_id=GEORGIA, kind="mayor")
        p.name(id, text_en, "en", "english")
        p.name(id, text_ka, "ka", "official")
    p.add(
        "scheme",
        code=UNIT_SCHEME,
        polity_id=GEORGIA,
        kind="electoral_district",
        rank=2,
        noun="district",
        code_system=None,
    )
    for number, name in sorted(list_2014().header.items()):
        if name is None:
            continue
        id = party(number)
        p.add("party", id=id, kind="party", polity_id=GEORGIA)
        p.name(id, name, "ka", "official")
    return p


def party(number: int) -> str:
    return key("party", 2014, number)


# ---------------------------------------------------------------------------------------------------------------------
# The elections

PART_EN = {"city": "city", "community": "community", None: ""}
PART_KA = {"city": "ქალაქი", "community": "თემი", None: ""}


def build(day: date) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The local election of {day:%-d %B %Y} by municipality from the Central Election Commission's results site",
        depends=[GEO, REGISTRY],
    )
    p.external |= {GEORGIA, COUNTRY, ELECTIONS[day]}
    election = ELECTIONS[day]
    units: dict[tuple[int, str | None], str] = {}

    def need(district: int, part: str | None) -> None:
        units.setdefault((district, part), unit(day, district, part))

    tables: list[Entry] = []
    names = areas()
    tbilisi = set(respublika.TBILISI_DISTRICTS)
    if day == DAY_2014:
        sakrebulo = list_2014()
        city_part = "city"
    else:
        sakrebulo = list_2010()
        city_part = None
    for line in sakrebulo.lines:
        need(line.district, line.part)
    capital = [line for line in sakrebulo.lines if line.district in tbilisi]
    tables.append(Entry("pr", "tbilisi", Table(sakrebulo.header, capital), None, None))
    for line in sakrebulo.lines:
        if line.district not in tbilisi:
            tag = f"{line.district}{'.' + line.part if line.part else ''}"
            tables.append(Entry("pr", tag, Table(sakrebulo.header, [line]), line.district, line.part))
    if day == DAY_2014:
        mayor = mayor_tbilisi_2014()
        tables.append(Entry("mayor", "tbilisi", mayor, None, None))
        for district, precincts in city_mayors_2014().items():
            need(district, "city")
            tables.append(Entry("mayor", f"{district}", precincts, district, "city"))
        for district, precincts in gamgebeli_2014().items():
            need(district, "community")
            tables.append(Entry("gamgebeli", f"{district}", precincts, district, "community"))
    else:
        tables.append(Entry("mayor", "tbilisi", mayor_tbilisi_2010(), None, None))
    del city_part
    for district, part in sorted(units, key=lambda k: (k[0], k[1] or "")):
        dual = [k for k in units if k[0] == district]
        p.add(
            "reporting_unit",
            id=units[(district, part)],
            polity_id=GEORGIA,
            scheme_code=UNIT_SCHEME,
            code=code(day, district, part),
            area_relation="equals" if len(dual) == 1 else "within",
            area_id=area(district),
            kind="standard",
            valid_from=day,
            valid_to=day + timedelta(days=1),
        )
        label = names[district]
        suffix_en, suffix_ka = (f" ({PART_EN[part]})", f" ({PART_KA[part]})") if len(dual) > 1 else ("", "")
        p.name(units[(district, part)], f"{english(district)}{suffix_en}", "en", "english")
        p.name(units[(district, part)], f"{label}{suffix_ka}", "ka", "official")
    for _, artifact in pages():
        p.uses(artifact)
    value_frames: list[pl.DataFrame] = []
    for entry in tables:
        value_frames.append(contest(p, day, election, entry, units, names))
    p.frame(
        "result_value",
        pl.concat(value_frames).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
        ),
    )
    return p


@dataclass(frozen=True)
class Entry:
    role: str  # pr, mayor, or gamgebeli
    tag: str
    table: Table | tuple[dict[int, str | None], dict[int, int], int]
    district: int | None  # None for Tbilisi
    part: str | None


def contest(
    p: Package, day: date, election: str, entry: Entry, units: dict[tuple[int, str | None], str], names: dict[int, str]
) -> pl.DataFrame:
    """One contest of one council or executive with its result set, options, and the votes of its units."""
    role, tag, table, district, part = entry.role, entry.tag, entry.table, entry.district, entry.part
    if isinstance(table, Table):
        header = table.header
        rows = [
            ((line.district, line.part or ("city" if day == DAY_2014 else None)), line.votes) for line in table.lines
        ]
    else:
        header = table[0]
        rows = [((district or 0, part), table[1])]
    contest_id = key("contest", day, role, tag)
    scope = names[district] if district is not None else "Tbilisi"
    scope_ka = scope if district is None else names[district]
    english_scope = english(district) if district is not None else "Tbilisi"
    if district is not None and part is not None and len({k for k in units if k[0] == district}) > 1:
        english_scope += f" {PART_EN[part]}"
        scope_ka += f" ({PART_KA[part]})"
    if district is None:
        scope_ka = "თბილისი"
    election_year = day.year
    if role == "pr":
        title_en, title_ka = (
            f"{election_year} sakrebulo of {english_scope}, party lists",
            f"{scope_ka}ს საკრებულო, პარტიული სიები",
        )
    elif role == "mayor":
        title_en, title_ka = f"{election_year} mayor of {english_scope}", f"{scope_ka}ს მერი"
    else:
        title_en, title_ka = f"{election_year} gamgebeli of {english_scope}", f"{scope_ka}ს გამგებელი"
    p.add(
        "contest",
        id=contest_id,
        election_id=election,
        kind="list_allocation" if role == "pr" else "office",
        office_id=MAYOR if role == "mayor" else GAMGEBELI if role == "gamgebeli" else None,
        body_id=COUNCILS if role == "pr" else None,
        district_area_id=TBILISI_AREA if district is None else area(district),
        magnitude=None if role == "pr" else 1,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest_id, title_en, "en", "english")
    p.name(contest_id, title_ka, "ka", "official")
    p.add(
        "slug",
        path=f"ge/{day}-municipal-{role}-{tag.replace('.', '-')}",
        entity_id=contest_id,
        is_current=True,
    )
    result_set = key("result_set", contest_id)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest_id,
        stage="preliminary",
        kind="counted",
        source_id=CEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    grain = (
        "the votes the Commission prints for the district"
        if isinstance(table, Table)
        else "the sum of the precinct protocols the Commission prints"
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=f"The Commission's preliminary results page: {grain}. The page prints no turnout figures, valid votes, "
        "or seats, so only the votes for each list or candidate are stored.",
        lang="en",
    )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=CEC,
        locator={"page": HOME_2014 if day == DAY_2014 else HOME_2010},
    )
    used = sorted({number for _, counted in rows for number in counted})
    options: dict[int, str] = {}
    for position, number in enumerate(used, 1):
        option = options[number] = key("option", contest_id, number)
        name = header.get(number)
        is_list = role == "pr"
        known = day == DAY_2014 and header_is_party(number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="list" if is_list else "candidate",
            party_id=party(number) if known else None,
            ballot_position=position,
            ballot_label=name or (f"No. {number}"),
        )
        if not is_list:
            person = key("person", contest_id, number)
            p.add("person", id=person)
            if name:
                p.name(person, name, "ka", "official")
            p.add(
                "candidacy",
                id=key("candidacy", contest_id, number),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party(number) if known else None,
                is_independent=False,
            )
    frames = []
    for (d, pt), counted in rows:
        frames.append(
            pl.DataFrame(
                {
                    "result_set_id": [result_set] * len(counted),
                    "unit_id": [units[(d, pt)]] * len(counted),
                    "option_id": [options[n] for n in counted],
                    "measure": pl.Series([VOTES] * len(counted), dtype=pl.Int16),
                    "value": list(counted.values()),
                }
            )
        )
    return pl.concat(frames)


def header_is_party(number: int) -> bool:
    return any(n == number and name for n, name in list_2014().header.items())
