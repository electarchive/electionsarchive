import csv
import functools
import html
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import numpy
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.wien"
SITE = "https://www.wien.gv.at"
OLD = f"{SITE}/wahl/NET"
NEW = f"{SITE}/wahlergebnis"
WFS = (
    "https://data.wien.gv.at/daten/geo?service=WFS&version=1.1.0&request=GetFeature&srsName=EPSG:4326&outputFormat=json"
)
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))
TOLERANCE = 0.00003
DERIVED = 1
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Stadt Wien – data.wien.gv.at"
LAND_SCHEME = "at-9.land"
BEZIRK_SCHEME = "at-9.bezirk"
SPRENGEL_SCHEME = "at-9.sprengel"

# election year -> (election day, Gemeinderat file code, Bezirksvertretung file code)
ELECTIONS = {
    1996: (date(1996, 10, 13), "GR961", "BV961"),
    2001: (date(2001, 3, 25), "GR011", "BV011"),
    2005: (date(2005, 10, 23), "GR051", "BV051"),
    2010: (date(2010, 10, 10), "GR101", "BV101"),
    2015: (date(2015, 10, 11), "GR151", "BV161"),
    2020: (date(2020, 10, 11), "GR201", "BV201"),
    2025: (date(2025, 4, 27), "GR251", "BV251"),
}
SHAPE_YEARS = (2005, 2010, 2015, 2020, 2025)
# the polling-district shapes of each plan, as the city publishes them
SHAPE_LAYERS = {year: f"ogdwien:WAHLSPRGR{year}OGD" for year in SHAPE_YEARS}
BEZIRKE = "ogdwien:BEZIRKSGRENZEOGD"
# the 2025 files print the postal ballots of each district in rows of their own, one per kind of envelope
POSTAL = {
    "Wahlkartenstimmen OE": ("wk", "Wahlkarten"),
    "Briefwahlstimmen OE": ("bw", "Briefwahl"),
    "Briefwahlstimmen EU": ("bw-eu", "Briefwahl, Unionsbürger"),
    "Gelbe Wahlkuverts EU": ("gk-eu", "Gelbe Wahlkuverts, Unionsbürger"),
}

# the lists keyed once across elections; every other list is keyed per Gemeinderat election
PERSISTENT = {"SPÖ", "ÖVP", "FPÖ", "GRÜNE", "NEOS", "LIF"}
# parties kept per election that are still parties rather than one-off lists
PARTIES = PERSISTENT | {"KPÖ", "BZÖ", "SLP"}
# the codes the older files print for these lists, which their per-election keys keep
CODES = {"KPÖ": "KPOE", "BZÖ": "BZOE", "SÖZ": "SOEZ"}
DISPLAY = {
    "SPOE": "SPÖ",
    "OEVP": "ÖVP",
    "FPOE": "FPÖ",
    "GRUE": "GRÜNE",
    "SOEZ": "SÖZ",
    "KPOE": "KPÖ",
    "BZOE": "BZÖ",
    "BGOE": "BGÖ",
    "OENP": "ÖNP",
    "OEABP": "ÖABP",
}
FULL_NAMES = {
    "SPÖ": "Sozialdemokratische Partei Österreichs",
    "ÖVP": "Österreichische Volkspartei",
    "FPÖ": "Freiheitliche Partei Österreichs",
    "GRÜNE": "Die Grünen – Die Grüne Alternative",
    "LIF": "Liberales Forum",
    "NEOS": "NEOS – Das Neue Österreich und Liberales Forum",
    "KPÖ": "Kommunistische Partei Österreichs",
    "BZÖ": "Bündnis Zukunft Österreich",
    "SLP": "Sozialistische LinksPartei",
}
LONG_PREFIXES = (
    ("Sozialdemokratische Partei", "SPÖ"),
    ("SPÖ", "SPÖ"),
    ("Wiener Volkspartei", "ÖVP"),
    ("Österreichische Volkspartei", "ÖVP"),
    ("Die Grünen", "GRÜNE"),
    ("GRÜNE", "GRÜNE"),
    ("NEOS", "NEOS"),
    ("Freiheitliche Partei", "FPÖ"),
    ("KPÖ und LINKS", None),
)
METHOD = (
    "Counted by the Vienna election authorities per polling district (Sprengel), as published by the City of Vienna. "
    "Registered voters are the electoral roll, ballots cast are the ballots counted, and valid votes are the ballots "
    "cast less the invalid ones. Postal ballots and ballots cast with a voting card (Wahlkarte) are reported in "
    "separate units of each district, so the units add up to the printed totals. Vienna's Gemeinderat is also its "
    "Landtag, so one election elects both, and the 23 district councils (Bezirksvertretungen) are elected on the same "
    "day, where citizens of other EU member states may also vote. The City prints a zero for a list that did not run "
    "in a district, so a list is kept in a contest only when it has votes there."
)


def gr(year: int) -> str:
    return f"gr-{year}"


def bv(year: int) -> str:
    return f"bv-{year}"


def legacy_url(code: str, kind: str) -> str:
    if code.endswith("201"):
        return f"{NEW}/downloads/{code}/{code}_99999999_9999_{kind}.xls"
    return f"{OLD}/{code}/{code}_99999999_9999_{kind}.xls"


def csv_url(code: str) -> str:
    return f"{NEW}/ogd/{code}.csv"


def files(year: int, council: bool) -> list[str]:
    _, gr_code, bv_code = ELECTIONS[year]
    code = gr_code if council else bv_code
    if year == 2025:
        return [csv_url(code)]
    return [legacy_url(code, kind) for kind in ("SPR", "WVB")]


def pages(year: int, council: bool) -> list[str]:
    """The result pages that print the seats: the city page for the Gemeinderat and one page per district council.
    The 1996 pages print no seats."""
    if year == 1996:
        return []
    _, gr_code, bv_code = ELECTIONS[year]
    if year >= 2020:
        if council:
            return [f"{NEW}/de/{gr_code}/index.html"]
        return [f"{NEW}/de/{bv_code}/9{n:02d}.html" for n in range(1, 24)]
    if council:
        return [f"{OLD}/{gr_code}/index.htm"]
    return [f"{OLD}/{bv_code}/{bv_code}-3{n:02d}.htm" for n in range(1, 24)]


def page_url(year: int, council: bool, bz: int | None) -> str:
    found = pages(year, council)
    return found[0] if council else found[(bz or 1) - 1]


def wfs(layer: str) -> str:
    return f"{WFS}&typeName={layer}"


def fetch() -> None:
    for year in ELECTIONS:
        for council in (True, False):
            for url in files(year, council):
                raw.fetch(SOURCE, url, suffix=".csv" if url.endswith(".csv") else ".txt")
    for layer in (BEZIRKE, *SHAPE_LAYERS.values()):
        raw.fetch(SOURCE, wfs(layer), suffix=".json")
    for year in ELECTIONS:
        for council in (True, False):
            raw.fetch_many(SOURCE, pages(year, council), suffix=".html")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", "shapes")
BODY = key("body", "landtag")
LAND = key("area", LAND_SCHEME, "9")


def district(number: int) -> str:
    return key("area", BEZIRK_SCHEME, number)


def precinct(year: int, code: str) -> str:
    return key("area", SPRENGEL_SCHEME, f"{year}:{code}")


def council_body(number: int) -> str:
    return key("body", "bezirksvertretung", number)


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{gr(year)}" for year in ELECTIONS),
        *(f"{SOURCE}/{bv(year)}" for year in ELECTIONS),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in ELECTIONS:
        landtag(year).write()
        councils(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- reading the results


@dataclass
class Row:
    bz: int
    code: str  # the unit's code within its election day, such as 01-001 or 01-wk
    label: str  # the unit's name before its district
    kind: str  # standard or postal_district
    shape: str | None  # the polling district's code in the city's shapes
    registered: int | None
    cast: int
    invalid: int
    votes: dict[str, int | None]


@dataclass
class Figures:
    registered: int
    cast: int
    invalid: int
    votes: dict[str, int | None]


@dataclass
class Results:
    labels: list[str]
    rows: list[Row] = field(default_factory=list)
    declared: dict[int | None, Figures] = field(default_factory=dict)


def _int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def _table(code: str, kind: str) -> tuple[list[str], list[list[str]]]:
    path = raw.cached(SOURCE, legacy_url(code, kind)).path
    lines = path.read_text(encoding="latin-1").splitlines()
    header = [cell.strip() for cell in lines[0].split("\t")]
    body = [[cell.strip() for cell in line.split("\t")] for line in lines[1:] if line.strip()]
    return header, body


def _legacy(code: str) -> Results:
    """The older files are tab-separated text with zero-padded numbers, one line per polling district and one line
    per district for its postal ballots (polling district 000)."""
    header, rows = _table(code, "SPR")
    columns = {name: i for i, name in enumerate(header) if name}
    parties = [(name, i) for name, i in columns.items() if i > columns["UNG."]]
    result = Results([DISPLAY.get(name, name) for name, _ in parties])

    def figures(row: list[str]) -> Figures:
        return Figures(
            int(row[columns["WBER"]]),
            int(row[columns["ABG."]]),
            int(row[columns["UNG."]]),
            {DISPLAY.get(name, name): int(row[i]) for name, i in parties},
        )

    for row in rows:
        if row[0] != "4":
            continue
        found = figures(row)
        spr = row[columns["SPR"]]
        bz = int(row[columns["BZ"]])
        postal = spr == "000" or (found.registered == 0 and found.cast > 0)
        result.rows.append(
            Row(
                bz,
                f"{bz:02d}-{spr}",
                "Wahlkarten" if spr == "000" else f"Sprengel {int(spr)}",
                "postal_district" if postal else "standard",
                None if postal else f"{bz:02d}{spr}",
                None if postal else found.registered,
                found.cast,
                found.invalid,
                dict(found.votes),
            )
        )
    _, totals = _table(code, "WVB")
    for row in totals:
        if row[0] == "1" and code.startswith("GR"):
            result.declared[None] = figures(row)
        elif row[0] == "3" and code.startswith("BV"):
            result.declared[int(row[columns["BZ"]])] = figures(row)
    return result


def _name(text: str) -> str:
    for prefix, code in LONG_PREFIXES:
        if text.startswith(prefix):
            return code or text
    return text


def _modern(code: str) -> Results:
    path = raw.cached(SOURCE, csv_url(code)).path
    table = list(csv.reader(io.StringIO(path.read_bytes().decode("cp1252")), delimiter=";"))
    header = table[0]
    status = header.index("Status")
    names = {i: " ".join(header[i].split()) for i in range(16, status)}
    index = {name: i for i, name in enumerate(header[:16])}
    result = Results([_name(text) for text in names.values()])

    def figures(row: list[str]) -> Figures:
        return Figures(
            int(row[index["Wahlberechtigte"]]),
            int(row[index["Abgegebene Stimmen"]]),
            int(row[index["Ungültige Stimmen"]]),
            {_name(text): _int(row[i]) for i, text in names.items()},
        )

    for row in table[1:]:
        if not row or not row[0]:
            continue
        typ, kind = row[index["Typ"]], row[index["ErfassungseinheitTyp"]]
        if typ == "Sprengel":
            found = figures(row)
            bz = int(row[index["DistrictCode"]][1:3])
            number = row[index["Sprengelnummer"]]
            result.rows.append(
                Row(
                    bz,
                    f"{bz:02d}-{number}",
                    f"Sprengel {int(number)}",
                    "standard",
                    f"{bz:02d}{int(number):03d}",
                    found.registered,
                    found.cast,
                    found.invalid,
                    found.votes,
                )
            )
        elif typ == "Bezirk" and kind:
            found = figures(row)
            bz = int(row[index["DistrictCode"]][1:3])
            suffix, label = POSTAL[kind]
            result.rows.append(
                Row(
                    bz,
                    f"{bz:02d}-{suffix}",
                    label,
                    "postal_district",
                    None,
                    None,
                    found.cast,
                    found.invalid,
                    found.votes,
                )
            )
        elif typ == "Wien" and code.startswith("GR"):
            result.declared[None] = figures(row)
        elif typ == "Bezirk" and code.startswith("BV") and not row[index["Stimmtyp"]]:
            result.declared[int(row[index["DistrictCode"]][1:3])] = figures(row)
    return result


@functools.cache
def results(year: int, council: bool) -> Results:
    _, gr_code, bv_code = ELECTIONS[year]
    code = gr_code if council else bv_code
    return _modern(code) if year == 2025 else _legacy(code)


def _text(year: int, council: bool, bz: int | None) -> str:
    data = raw.cached(SOURCE, page_url(year, council, bz)).path.read_bytes()
    try:
        markup = data.decode("utf-8")
    except UnicodeDecodeError:
        markup = data.decode("cp1252")
    markup = re.sub(r"<script.*?</script>|<style.*?</style>", "", markup, flags=re.DOTALL)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup)))


def seats(year: int, council: bool, bz: int | None) -> tuple[int, list[tuple[float, int]]] | None:
    """The seats the city prints on a result page: the council's size and each list's vote share with its seats.
    Lists are matched by share, because the pages abbreviate their names."""
    if not pages(year, council):
        return None
    text = _text(year, council, bz)
    heading = list(re.finditer(r"Mandatsverteilung \((\d+) Mandate zu vergeben\)", text))
    if not heading:
        return None
    size = int(heading[0].group(1))
    if "Stimmen in Prozent" in text:
        shares = re.findall(r"(\d+,\d+)%", text.split("Stimmen in Prozent")[1].split("Änderung der Stimmen")[0])
        counts = re.match(r"((?: \d+)+)", text[heading[-1].end() :])
        assert counts is not None
        given = counts.group(1).split()
        if len(shares) != len(given):
            raise ValueError(f"{page_url(year, council, bz)}: {len(shares)} shares and {len(given)} seat counts")
        return size, [(float(a.replace(",", ".")), int(b)) for a, b in zip(shares, given, strict=True)]
    tail = text[heading[0].end() :].split("Endergebnis der Stadtwahlbehörde - Tabelle")[0]
    pairs = re.findall(r"(\d+) \([^)]*\) (.+?)(?= \d+ \([^)]*\) |$)", tail)
    diagram = re.sub(r"^.*?Diagramm(?: \d+%)*", "", text[: heading[0].start()].split("Diagramm : -->")[-1])
    share = {
        a.strip(): float(b.replace(",", "."))
        for a, b in re.findall(r"(.+?) (\d+,\d+)% (?:[+±-]\d+,\d+%|n\.t\.) ?", diagram)
    }
    return size, [(share[label.strip()], int(count)) for count, label in pairs if int(count) or label.strip() in share]


# ---------------------------------------------------------------- geography


@functools.cache
def _features(layer: str) -> list[tuple[dict[str, str | int | None], shapely.Geometry]]:
    data = json.loads(raw.cached(SOURCE, wfs(layer)).path.read_text(encoding="utf-8"))
    return [(feature["properties"], shapely.geometry.shape(feature["geometry"])) for feature in data["features"]]


def _multi(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _outline(p: Package, id: str, shape: shapely.Geometry, since: date | None, until: date | None) -> None:
    shape = _multi(shape)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        valid_to=until,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=SHAPES_ID,
    )


@functools.cache
def bezirke() -> dict[int, tuple[str, shapely.Geometry]]:
    found = {}
    for props, shape in _features(BEZIRKE):
        found[int(str(props["BEZNR"]))] = (str(props["NAMEK"]), shape)
    shapes = list(shapely.make_valid(shapely.coverage_simplify(numpy.array([s for _, s in found.values()]), TOLERANCE)))
    return {number: (found[number][0], shape) for number, shape in zip(sorted(found), shapes, strict=True)}


def districts() -> dict[int, str]:
    return {number: name for number, (name, _) in bezirke().items()}


@functools.cache
def sprengel(year: int) -> dict[str, shapely.Geometry]:
    """The published polling-district shapes of one plan, keyed by the five-digit code (district and number)."""
    grouped: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for props, shape in _features(SHAPE_LAYERS[year]):
        grouped[str(props["SPRENGEL"])].append(shape)
    codes = sorted(grouped)
    merged = [shapely.union_all(grouped[code]) for code in codes]
    simple = shapely.coverage_simplify(numpy.array(merged), TOLERANCE)
    return dict(zip(codes, shapely.make_valid(simple), strict=True))


def window(year: int) -> tuple[date | None, date | None]:
    """The days a plan's polling districts held for: from its election to the next one that has shapes."""
    later = [y for y in SHAPE_YEARS if y > year]
    return ELECTIONS[year][0], ELECTIONS[later[0]][0] if later else None


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Vienna, its 23 municipal districts, and the polling districts of the 2005 to 2025 elections, with shapes from "
        "the City of Vienna's open data",
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SHAPES_ID,
        kind="official_body",
        publisher="Stadt Wien",
        title="Open government data of the City of Vienna: district boundaries and polling districts",
        url="https://www.data.gv.at/katalog/organization/stadt-wien",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
    )
    for layer in (BEZIRKE, *SHAPE_LAYERS.values()):
        p.artifact(raw.cached(SOURCE, wfs(layer)), SHAPES_ID)
    for code, rank, kind, noun in (
        (LAND_SCHEME, 1, "administrative", "state"),
        (BEZIRK_SCHEME, 2, "administrative", "municipal district"),
        (SPRENGEL_SCHEME, 3, "precinct", "polling district"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind=kind, rank=rank, noun=noun, code_system=None)
    found = bezirke()
    p.add("area", id=LAND, scheme_code=LAND_SCHEME)
    p.add("area_code", area_id=LAND, code="9")
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Wien", "de", "official")
    p.name(LAND, "Vienna", "en", "official", False)
    _outline(p, LAND, shapely.coverage_union_all([shape for _, shape in found.values()]), None, None)
    taken = raw.cached(SOURCE, wfs(BEZIRKE)).retrieved_at
    for scheme in (LAND_SCHEME, BEZIRK_SCHEME):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, taken.year),
            scheme_code=scheme,
            vintage=str(taken.year),
            as_of=taken.date(),
        )
    for number, (name, shape) in sorted(found.items()):
        id = district(number)
        p.add("area", id=id, scheme_code=BEZIRK_SCHEME)
        p.add("area_code", area_id=id, code=f"{number:02d}")
        p.add("area_parent", area_id=id, parent_id=LAND)
        p.name(id, name, "de", "official")
        _outline(p, id, shape, None, None)
    for year in SHAPE_YEARS:
        since, until = window(year)
        p.add(
            "boundary_set",
            id=key("boundary_set", SPRENGEL_SCHEME, year),
            scheme_code=SPRENGEL_SCHEME,
            vintage=str(year),
            valid_from=since,
            valid_to=until,
            as_of=since,
        )
        for code, shape in sorted(sprengel(year).items()):
            number, spr = int(code[:-3]), code[-3:]
            id = precinct(year, code)
            p.add("area", id=id, scheme_code=SPRENGEL_SCHEME)
            p.add("area_code", area_id=id, code=f"{year}:{code}", valid_from=since, valid_to=until)
            p.add("area_parent", area_id=id, parent_id=district(number), valid_from=since, valid_to=until)
            p.name(id, f"{found[number][0]}, Sprengel {int(spr)} ({year})", "de", "official")
            _outline(p, id, shape, since, until)
    return p


# ---------------------------------------------------------------- registry


def party_key(year: int, label: str) -> str:
    if label in PERSISTENT:
        return label
    return f"{CODES.get(label, label) if year < 2025 else label}@{gr(year)}"


def party(year: int, label: str) -> str:
    return key("party", party_key(year, label))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Vienna's Gemeinderat and Landtag, its 23 district councils, and the lists on their ballots from the City of "
        "Vienna's election results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Stadt Wien, Magistratsabteilung 62",
        title="Wiener Wahlergebnisse: Gemeinderats- und Bezirksvertretungswahlen",
        url=f"{SITE}/politik/wahlergebnisse",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
    )
    for year in ELECTIONS:
        for council in (True, False):
            for url in [*files(year, council), *pages(year, council)]:
                p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=BODY, polity_id=AUSTRIA, kind="unicameral")
    p.name(BODY, "Viennese Landtag", "en", "official")
    p.name(BODY, "Wiener Landtag und Gemeinderat", "de", "native", False)
    for number, name in sorted(districts().items()):
        body = council_body(number)
        p.add("body", id=body, polity_id=AUSTRIA, kind="council")
        p.name(body, f"Bezirksvertretung {name}", "de", "official")
        p.name(body, f"District Council of {name}", "en", "official", False)
    seen: set[str] = set()
    for year in ELECTIONS:
        for council in (True, False):
            for label in results(year, council).labels:
                id = party(year, label)
                if id in seen:
                    continue
                seen.add(id)
                p.add("party", id=id, kind="party" if label in PARTIES else "list_vehicle", polity_id=AUSTRIA)
                p.add("external_id", entity_id=id, scheme="at.wien.party", value=party_key(year, label))
                p.name(id, FULL_NAMES.get(label, label), "de", "official")
                if label in FULL_NAMES:
                    p.name(id, label, "de", "abbreviation", False)
    return p


# ---------------------------------------------------------------- one election


def _ran(rows: list[Row], labels: list[str], modern: bool) -> list[str]:
    """The lists with votes in these rows; the older sheets print a zero for a list that did not run."""
    found = []
    for label in labels:
        totals = [row.votes.get(label) for row in rows]
        if any(total is not None for total in totals) if modern else any(totals):
            found.append(label)
    return found


def unit_id(year: int, code: str) -> str:
    """One unit per place and day, shared by the Gemeinderat and the district council counted there."""
    return key("unit", gr(year), code)


def _unit(p: Package, year: int, row: Row) -> None:
    day = ELECTIONS[year][0]
    shaped = year in SHAPE_YEARS and row.shape is not None and row.shape in sprengel(year)
    id = unit_id(year, row.code)
    p.add(
        "reporting_unit",
        id=id,
        polity_id=AUSTRIA,
        scheme_code=SPRENGEL_SCHEME,
        code=f"{day}:{row.code}",
        area_relation="equals" if shaped else "within",
        area_id=precinct(year, row.shape) if shaped and row.shape is not None else district(row.bz),
        kind=row.kind,
    )
    p.name(id, f"{row.label}, {districts()[row.bz]}", "de", "official")


def _contest(
    p: Package,
    year: int,
    council: bool,
    scope: int | None,
    election_id: str,
    rows: list[Row],
    found: Results,
) -> str:
    """One contest: the Gemeinderat (scope None) or the council of one district."""
    prefix = gr(year) if council else bv(year)
    contest = key("contest", prefix) if council else key("contest", prefix, scope)
    result_set = key("result_set", prefix) if council else key("result_set", prefix, scope)
    printed = seats(year, council, scope)
    declared = found.declared.get(scope)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        magnitude=printed[0] if printed else None,
        body_id=BODY if scope is None else council_body(scope),
        district_area_id=LAND if scope is None else district(scope),
        round_number=1,
        default_result_kind="counted",
    )
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
    options = {}
    for position, label in enumerate(_ran(rows, found.labels, year == 2025), 1):
        option = options[label] = key("option", prefix, label) if council else key("option", prefix, scope, label)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(year, label),
            ballot_position=position,
            ballot_label=label,
        )
    for row in rows:
        id = unit_id(year, row.code)
        for measure, value in ((2, row.registered), (3, row.cast), (5, row.invalid), (4, row.cast - row.invalid)):
            if value is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=id,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=DERIVED if measure == 4 else 0,
                value=value,
            )
        for label, option in options.items():
            value = row.votes.get(label)
            if value is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=id,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=value,
            )
    if declared is not None:
        for measure, value in (
            (2, declared.registered),
            (3, declared.cast),
            (5, declared.invalid),
            (4, declared.cast - declared.invalid),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for label, option in options.items():
            value = declared.votes.get(label)
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=value)
        if printed is not None:
            valid = declared.cast - declared.invalid
            for share, count in printed[1]:
                if not count:
                    continue
                match = [
                    label
                    for label in options
                    if (votes := declared.votes.get(label)) is not None and abs(100 * votes / valid - share) < 0.011
                ]
                if len(match) != 1:
                    raise ValueError(f"{prefix} {scope}: {share}% matches {match}")
                p.add("outcome", result_set_id=result_set, option_id=options[match[0]], kind="won", seats=count)
    if any(row.registered is not None and row.cast > row.registered for row in rows):
        p.add(
            "note",
            id=key("note", result_set, "voting-cards"),
            entity_id=result_set,
            kind="caveat",
            body_md="In some polling districts more ballots were cast than voters were on the roll, because voters "
            "with a voting card may vote in any polling district of their district. The figures are stored as printed.",
            lang="en",
        )
    return result_set


def _over(p: Package, rows: list[Row]) -> None:
    count = sum(1 for row in rows if row.registered is not None and row.cast > row.registered)
    if count:
        p.accept(
            "accounting.turnout",
            f"{count} polling-district rows print more ballots than registered voters, because voters with a voting "
            "card (Wahlkarte) may vote in any polling district of their district; stored as printed",
        )


def landtag(year: int) -> Package:
    day = ELECTIONS[year][0]
    found = results(year, True)
    p = Package(
        f"{SOURCE}/{gr(year)}",
        SOURCE,
        f"The {day} Viennese Gemeinderat and Landtag election by polling district from the city's election office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA}
    for url in [*files(year, True), *pages(year, True)]:
        p.uses(raw.cached(SOURCE, url))
    id = key("election", gr(year))
    p.add("election", id=id, polity_id=AUSTRIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Viennese Landtag election", "en", "official")
    p.name(id, f"Gemeinderatswahl {year}", "de", "native", False)
    path = f"at/{day}-wien-landtag"
    p.add("slug", path=path, entity_id=id, is_current=True)
    for row in found.rows:
        _unit(p, year, row)
    _contest(p, year, True, None, id, found.rows, found)
    contest = key("contest", gr(year))
    p.name(contest, f"{year} Viennese Landtag", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug('Landtag')}", entity_id=contest, is_current=True)
    _over(p, found.rows)
    return p


def councils(year: int) -> Package:
    day = ELECTIONS[year][0]
    found = results(year, False)
    names = districts()
    p = Package(
        f"{SOURCE}/{bv(year)}",
        SOURCE,
        f"The {day} elections of Vienna's 23 district councils (Bezirksvertretungen) by polling district from the "
        "city's election office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{gr(year)}"],
    )
    p.external |= {AUSTRIA}
    for url in [*files(year, False), *pages(year, False)]:
        p.uses(raw.cached(SOURCE, url))
    id = key("election", bv(year))
    p.add("election", id=id, polity_id=AUSTRIA, kind="local", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Vienna district council elections", "en", "official")
    p.name(id, f"Bezirksvertretungswahlen {year}", "de", "native", False)
    path = f"at/{day}-wien-bezirksvertretung"
    p.add("slug", path=path, entity_id=id, is_current=True)
    shared = {row.code for row in results(year, True).rows}
    for row in found.rows:
        if row.code not in shared:
            _unit(p, year, row)
            shared.add(row.code)
    for scope in sorted(names):
        rows = [row for row in found.rows if row.bz == scope]
        _contest(p, year, False, scope, id, rows, found)
        contest = key("contest", bv(year), scope)
        p.name(contest, f"Bezirksvertretung {names[scope]} {year}", "de", "official")
        p.add("slug", path=f"{path}/{changeset.slug(names[scope])}", entity_id=contest, is_current=True)
    _over(p, found.rows)
    return p
