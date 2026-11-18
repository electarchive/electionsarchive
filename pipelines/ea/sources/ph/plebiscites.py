"""The Philippines' local and autonomy plebiscites that the Commission on Elections reports with a result: the
plebiscite on the Bangsamoro Organic Law (21 January 2019) and seven plebiscites of 2019 to 2024 that converted a
municipality into a city, renamed or divided a province, or ratified a city's barangay boundaries.

The Commission posts a page for each plebiscite with its resolutions. Where the board of canvassers' totals are
printed on the page, they are keyed here, and `parse` checks that every figure appears in the cached page. Pages
without a printed result (the plebiscites before 2018, and most barangay plebiscites) are not read. The shapes of the
provinces and municipalities are the 2020 outlines of the national mapping agency as redistributed by geoBoundaries
(CC BY 3.0 IGO).
"""

import functools
import json
import re
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ph.plebiscites"
GEOSOURCE = "ph.plebiscites.geoboundaries"
PHILIPPINES = str(uuid5(package.NAMESPACE, "polity\x1fPH"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPHL"))
VOTES, REGISTERED, CAST = 1, 2, 3
SITE = "https://www.comelec.gov.ph/html-tpls/References/ComelecResolutions/Plebiscites/"
INDEX = "https://www.comelec.gov.ph/html-tpls/References/ComelecResolutions/Plebiscites.html"
BOL = SITE + "PlebiscitesOrganicLaw2018/NPBCReports.html"
BOL_PAGE = SITE + "PlebiscitesOrganicLaw2018.html"
BOL_CERTIFICATE = SITE + "PlebiscitesOrganicLaw2018/CertificationofRatificationofRA11054.html"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/"
ADM2 = GEOBOUNDARIES + "41af8f1/releaseData/gbOpen/PHL/ADM2/geoBoundaries-PHL-ADM2_simplified.geojson"
ADM3 = GEOBOUNDARIES + "9469f09/releaseData/gbOpen/PHL/ADM3/geoBoundaries-PHL-ADM3_simplified.geojson"
VINTAGE = date(2020, 1, 1)
RULES = {"formula": "referendum"}
YES, NO = "YES/OO", "NO/HINDI"


class Place(NamedTuple):
    scheme: str  # ph.adm2 (province or equivalent), ph.adm3 (city or municipality), or ph.armm
    code: str
    name: str
    parts: tuple[str, ...]  # the geoBoundaries shape names (and parents) that make it up
    parent: str | None = None  # the province a city or municipality lies in, for its shape's match
    former: str | None = None


PLACES = {
    "armm": Place(
        "ph.armm",
        "ARMM-2019",
        "Autonomous Region in Muslim Mindanao (the core territory, 2019)",
        ("Basilan", "Lanao del Sur", "Maguindanao", "Sulu", "Tawi-Tawi"),
    ),
    "basilan": Place("ph.adm2", "Basilan", "Basilan", ("Basilan",)),
    "isabela-city": Place("ph.adm2", "City of Isabela", "Isabela City", ("City of Isabela",)),
    "cotabato-city": Place("ph.adm2", "Cotabato City", "Cotabato City", ("Cotabato City",)),
    "maguindanao": Place("ph.adm2", "Maguindanao", "Maguindanao", ("Maguindanao",)),
    "davao-de-oro": Place(
        "ph.adm2", "Compostela Valley", "Davao de Oro", ("Compostela Valley",), former="Compostela Valley"
    ),
    "sto-tomas": Place("ph.adm3", "Santo Tomas|Batangas", "Santo Tomas", ("Santo Tomas",), "Batangas"),
    "calaca": Place("ph.adm3", "Calaca|Batangas", "Calaca", ("Calaca",), "Batangas"),
    "baliwag": Place("ph.adm3", "Baliuag|Bulacan", "Baliwag", ("Baliuag",), "Bulacan"),
    "carmona": Place("ph.adm3", "Carmona|Cavite", "Carmona", ("Carmona",), "Cavite"),
    "las-pinas": Place("ph.adm3", "City of Las Piñas|NCR", "Las Piñas", ("City of Las Piñas",), "NCR, Fourth District"),
}


class Tally(NamedTuple):
    place: str
    registered: int
    cast: int | None
    yes: int
    no: int
    parent: str | None = None  # the place of the unit that contains this one


class Question(NamedTuple):
    slug: str
    name: str
    page: str
    tallies: tuple[Tally, ...]
    won: bool | None  # whether the page declares the question carried, or None where it declares nothing
    note: str


class Spec(NamedTuple):
    day: date
    slug: str
    name: str
    questions: tuple[Question, ...]
    page: str
    note: str


ELECTIONS = (
    Spec(
        date(2019, 1, 21),
        "bangsamoro-organic-law",
        "2019 plebiscite on the Bangsamoro Organic Law",
        (
            Question(
                "ratification",
                "Ratification of Republic Act No. 11054, the Organic Law for the Bangsamoro Autonomous Region in "
                "Muslim Mindanao",
                BOL,
                (
                    Tally("armm", 1980441, 1738767, 1540017, 198750),
                    Tally("basilan", 190861, 156468, 144640, 8487, "armm"),
                ),
                True,
                "The National Plebiscite Board of Canvassers' second report lists the region's total and, below it, "
                "Basilan, where the line carries the question on Isabela City's inclusion; the figures are read as "
                "Basilan's vote on the Organic Law, as the printed total of the region counts it.",
            ),
            Question(
                "isabela-city",
                "Inclusion of Isabela City, Basilan, in the Bangsamoro Autonomous Region",
                BOL,
                (Tally("isabela-city", 71124, 41748, 19032, 22441),),
                None,
                "The board's report prints the city's tally under the question on its inclusion.",
            ),
            Question(
                "cotabato-city",
                "Inclusion of Cotabato City in the Bangsamoro Autonomous Region",
                BOL,
                (Tally("cotabato-city", 113751, 58806, 36682, 24994),),
                True,
                "The board's report marks the registered voters as based on the project of precincts and the voters "
                "who voted as incomplete, from election returns that the city's board had not yet fully canvassed.",
            ),
        ),
        BOL,
        "Report No. 2 of the National Plebiscite Board of Canvassers, posted on 26 January 2019, which gives the "
        "totals of the core Autonomous Region in Muslim Mindanao and of Isabela City and Cotabato City. The "
        "Commission's page on the plebiscite records that on 25 January 2019 the board proclaimed the ratification "
        "of the Organic Law and the inclusion of Cotabato City; the second part of the plebiscite, on 6 February "
        "2019, has no totals on the Commission's pages.",
    ),
    Spec(
        date(2019, 9, 7),
        "sto-tomas-city",
        "2019 plebiscite on the conversion of Sto. Tomas, Batangas, into a city",
        (
            Question(
                "conversion",
                "Conversion of the Municipality of Sto. Tomas, Batangas, into a component city (Republic Act No. 11086)",
                SITE + "PlebiscitesStoTomas2019.html",
                (Tally("sto-tomas", 98583, 23291, 22858, 431),),
                None,
                "",
            ),
        ),
        SITE + "PlebiscitesStoTomas2019.html",
        "The totals printed on the Commission's page on the plebiscite.",
    ),
    Spec(
        date(2019, 12, 7),
        "davao-de-oro",
        "2019 plebiscite on renaming Compostela Valley as Davao de Oro",
        (
            Question(
                "renaming",
                "Renaming of the Province of Compostela Valley as the Province of Davao de Oro (Republic Act No. 11297)",
                SITE + "PlebiscitesComValleytoDavaodeOro2019.html",
                (Tally("davao-de-oro", 410261, 179953, 174442, 5020),),
                None,
                "",
            ),
        ),
        SITE + "PlebiscitesComValleytoDavaodeOro2019.html",
        "The totals printed on the Commission's page on the plebiscite, from the canvass of 11 municipalities.",
    ),
    Spec(
        date(2022, 9, 3),
        "calaca-city",
        "2022 plebiscite on the conversion of Calaca, Batangas, into a city",
        (
            Question(
                "conversion",
                "Conversion of the Municipality of Calaca, Batangas, into a component city (Republic Act No. 11544)",
                SITE + "PlebisciteCityofCalaca.html",
                (Tally("calaca", 58881, None, 29424, 3781),),
                None,
                "",
            ),
        ),
        SITE + "PlebisciteCityofCalaca.html",
        "The totals printed on the Commission's page on the plebiscite. The page leaves the number of voters who "
        "voted blank.",
    ),
    Spec(
        date(2022, 9, 17),
        "maguindanao-division",
        "2022 plebiscite on the division of Maguindanao",
        (
            Question(
                "division",
                "Division of the Province of Maguindanao into Maguindanao del Norte and Maguindanao del Sur "
                "(Republic Act No. 11550)",
                SITE + "PlebisciteMaguindanaodelNorteMaguindanaodelSur.html",
                (Tally("maguindanao", 818791, 711767, 706558, 5209),),
                None,
                "",
            ),
        ),
        SITE + "PlebisciteMaguindanaodelNorteMaguindanaodelSur.html",
        "The totals printed on the Commission's page on the plebiscite, from the canvass of 36 municipalities.",
    ),
    Spec(
        date(2022, 12, 17),
        "baliwag-city",
        "2022 plebiscite on the conversion of Baliwag, Bulacan, into a city",
        (
            Question(
                "conversion",
                "Conversion of the Municipality of Baliwag, Bulacan, into a component city (Republic Act No. 11929)",
                SITE + "PlebisciteCityofBaliwag.html",
                (Tally("baliwag", 108572, 23562, 17814, 5702),),
                None,
                "",
            ),
        ),
        SITE + "PlebisciteCityofBaliwag.html",
        "The totals printed on the Commission's page on the plebiscite.",
    ),
    Spec(
        date(2023, 7, 8),
        "carmona-city",
        "2023 plebiscite on the conversion of Carmona, Cavite, into a city",
        (
            Question(
                "conversion",
                "Conversion of the Municipality of Carmona, Cavite, into a component city (Republic Act No. 11938)",
                SITE + "PlebisciteCityofCarmona.html",
                (Tally("carmona", 58691, 31379, 30363, 1016),),
                True,
                "",
            ),
        ),
        SITE + "PlebisciteCityofCarmona.html",
        "The totals printed on the Commission's page on the plebiscite, which declares it ratified.",
    ),
    Spec(
        date(2024, 6, 29),
        "las-pinas-barangays",
        "2024 plebiscite on the barangay boundaries of Las Piñas",
        (
            Question(
                "boundaries",
                "Ratification of City Ordinance No. 1941-23 delineating the territorial boundaries of the twenty "
                "barangays of Las Piñas City",
                SITE + "PlebisciteLasPinasCity.html",
                (Tally("las-pinas", 308059, 61237, 41493, 19498),),
                True,
                "",
            ),
        ),
        SITE + "PlebisciteLasPinasCity.html",
        "The canvass of the city plebiscite board of canvassers, as the Commission's page prints it; the page "
        "declares the ordinance ratified and approved.",
    ),
)
PAGES = [INDEX, BOL_PAGE, BOL_CERTIFICATE, *dict.fromkeys(e.page for e in ELECTIONS)]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(name: str) -> str:
    return key("area", name)


SYSTEM = key("electoral_system", "plebiscite")
SOURCE_ID = key("source", "comelec")
GEO_SOURCE = key("source", "geoboundaries")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def fetch() -> None:
    for url in PAGES:
        raw.fetch(SOURCE, url, suffix=".html")
    for url in (ADM2, ADM3):
        raw.fetch(GEOSOURCE, url, suffix=".geojson")


def text(url: str) -> str:
    page = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    page = re.sub(r"<(script|style).*?</\1>", "", page, flags=re.DOTALL)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))


def check_page(spec: Spec) -> None:
    for question in spec.questions:
        body = text(question.page)
        for tally in question.tallies:
            for number in (tally.registered, tally.cast, tally.yes, tally.no):
                if number is None:
                    continue
                if not re.search(rf"(?<![\d,]){number:,}(?![\d,])", body):
                    raise ValueError(f"{spec.day} {question.slug}: {number:,} is not on {question.page}")
            if tally.place != "cotabato-city" and tally.cast is not None and tally.yes + tally.no > tally.cast:
                raise ValueError(f"{spec.day} {question.slug}: {tally.place} yes and no exceed the voters")
    if spec.day == date(2019, 1, 21):
        body = text(BOL_PAGE) + text(BOL_CERTIFICATE)
        for phrase in ("officially proclaimed", "ratification of Republic Act No. 11054", "inclusion of Cotabato City"):
            if phrase not in body:
                raise ValueError(f"the proclamation text lacks {phrase!r}")


def parse() -> None:
    for spec in ELECTIONS:
        check_page(spec)
    geo().write()
    registry().write()
    for spec in ELECTIONS:
        build(spec).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------------------
# Shapes


def _features(url: str) -> list[tuple[str, shapely.Geometry]]:
    data = json.loads(raw.cached(GEOSOURCE, url).path.read_text(encoding="utf-8"))
    return [(f["properties"]["shapeName"], shapely.geometry.shape(f["geometry"])) for f in data["features"]]


@functools.cache
def shapes() -> dict[str, shapely.Geometry]:
    provinces = _features(ADM2)
    towns = _features(ADM3)
    found: dict[str, shapely.Geometry] = {}
    for name, place in PLACES.items():
        if place.scheme in ("ph.adm2", "ph.armm"):
            parts = [shape for shape_name, shape in provinces if shape_name in place.parts]
            if len(parts) != len(place.parts):
                raise ValueError(f"{name}: {len(parts)} outlines for {place.parts}")
            found[name] = shapely.union_all(parts) if len(parts) > 1 else parts[0]
            continue
        parent = [shape for shape_name, shape in provinces if shape_name == place.parent]
        if len(parent) != 1:
            raise ValueError(f"{name}: no single province {place.parent}")
        inside = [
            shape
            for shape_name, shape in towns
            if shape_name in place.parts and parent[0].contains(shapely.point_on_surface(shape))
        ]
        if len(inside) != 1:
            raise ValueError(f"{name}: {len(inside)} outlines in {place.parent}")
        found[name] = inside[0]
    return found


def multipolygon(geometry: shapely.Geometry) -> shapely.Geometry:
    if geometry.geom_type == "Polygon":
        return shapely.MultiPolygon([geometry])
    return geometry


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The provinces, cities, and municipalities that voted in the plebiscites of 2019 to 2024, and the core "
        "territory of the Autonomous Region in Muslim Mindanao, with their 2020 outlines",
    )
    p.external |= {PHILIPPINES, COUNTRY}
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (NAMRIA, PSA, and OCHA Philippines boundaries)",
        title="geoBoundaries gbOpen Philippines ADM2 and ADM3, 2020",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO (the national mapping agency's boundaries, as geoBoundaries redistributes them)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab), from NAMRIA, PSA, and OCHA Philippines",
    )
    for url in (ADM2, ADM3):
        p.artifact(raw.cached(GEOSOURCE, url), GEO_SOURCE)
    for code, noun in (("ph.adm2", "province"), ("ph.adm3", "city or municipality"), ("ph.armm", "autonomous region")):
        p.add("scheme", code=code, polity_id=PHILIPPINES, kind="administrative", rank=1, noun=noun)
        p.add(
            "boundary_set",
            id=key("boundary_set", code, "2020"),
            scheme_code=code,
            vintage="2020",
            valid_from=VINTAGE,
            as_of=VINTAGE,
        )
    geometries = shapes()
    for name, place in PLACES.items():
        id = area(name)
        p.add("area", id=id, scheme_code=place.scheme)
        p.add("area_code", area_id=id, code=place.code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, place.name, "en", "official")
        if place.former:
            p.name(id, place.former, "en", "historical", False)
        shape = multipolygon(geometries[name])
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=VINTAGE,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=GEO_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The electoral system of the Philippines' plebiscites")
    p.external |= {PHILIPPINES}
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=PHILIPPINES,
        valid_from=date(1987, 2, 2),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "A plebiscite carries when a majority of the votes cast in the units it affects approve it. The "
            "archive records each unit's totals as the board of canvassers declared them."
        ),
        lang="en",
    )
    return p


# ---------------------------------------------------------------------------------------------------------------
# The elections


def build(spec: Spec) -> Package:
    p = Package(
        f"{SOURCE}/{spec.day}",
        SOURCE,
        f"The {spec.name[0].lower()}{spec.name[1:]}, from the Commission on Elections",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PHILIPPINES, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Commission on Elections",
        title="Plebiscites (COMELEC Resolutions)",
        url=INDEX,
        license=None,
        license_url=None,
        attribution="Commission on Elections",
    )
    for url in dict.fromkeys(q.page for q in spec.questions):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    election = p.id("election", spec.day)
    p.add(
        "election",
        id=election,
        polity_id=PHILIPPINES,
        kind="referendum",
        date_from=spec.day,
        date_to=spec.day,
        date_precision="day",
        status="held",
    )
    p.name(election, spec.name, "en", "official")
    base = f"ph/{spec.day}-{spec.slug}"
    p.add("slug", path=base, entity_id=election, is_current=True)
    rows: list[tuple[str, str, str | None, int, int]] = []
    for question in spec.questions:
        contest = p.id("contest", spec.day, question.slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=area(question.tallies[0].place),
            electoral_system_id=SYSTEM,
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="plebiscite")
        p.name(contest, question.name, "en", "official")
        p.add("slug", path=f"{base}/{question.slug}", entity_id=contest, is_current=True)
        options = []
        for position, label in enumerate((YES, NO), 1):
            option = p.id("option", spec.day, question.slug, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        result_set = p.id("result_set", spec.day, question.slug)
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
            completeness=None if spec.day.year == 2019 and spec.day.month == 1 else 1.0,
        )
        p.add(
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=" ".join(part for part in (spec.note, question.note) if part),
            lang="en",
        )
        if question.won:
            p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")
        for tally in question.tallies:
            unit = p.id("unit", spec.day, tally.place)
            where = PLACES[tally.place]
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=PHILIPPINES,
                scheme_code=where.scheme,
                code=where.code,
                parent_id=p.id("unit", spec.day, tally.parent) if tally.parent else None,
                area_relation="equals",
                area_id=area(tally.place),
                kind="standard",
            )
            rows += [
                (result_set, unit, options[0], VOTES, tally.yes),
                (result_set, unit, options[1], VOTES, tally.no),
                (result_set, unit, None, REGISTERED, tally.registered),
            ]
            if tally.cast is not None:
                rows.append((result_set, unit, None, CAST, tally.cast))
    p.frame(
        "result_value",
        pl.DataFrame(
            rows, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
