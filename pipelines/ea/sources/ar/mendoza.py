"""Mendoza province (Argentina): definitive counts of the provincial and municipal elections of 2023 and 2025, and
the people and seats the Junta proclaimed after the 2023 general elections.

The Junta Electoral de la Provincia de Mendoza publishes each definitive count (escrutinio definitivo) as PDFs of
signed tables, one table per contest and department (and per electoral section for the legislature). The finest unit
it publishes is the department, so that is the reporting unit. The tables are scans with a text layer of uneven
quality: `mendoza_tables.json` holds every table as it was read from the page, and `parse` checks each one against the
arithmetic the Junta prints (the lists add up to the positive votes, and positive, null and blank votes add up to the
voters) before anything is built. The proclamation act of 6 November 2023 names, for each contest of the two
general-election days, the party and the people declared elected; `mendoza_proclamation.json` holds it as it was
read from the page, and `parse` checks that every party it names ran in its contest and that each mayor's list
led the vote. The Junta has published no act for the 2025 election.
"""

import functools
import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import FACTS, Package

SOURCE = "ar.mendoza"
SITE = "https://jusmendoza.gob.ar"
PAGES = [
    f"{SITE}/escrutinios-definitivos-elecciones-generales-de-mendoza/",
    f"{SITE}/escrutinios-definitivos-%c2%b7-elecciones-municipales-%c2%b7-provincia-de-mendoza/",
    f"{SITE}/junta-electoral-de-mendoza/",
]
GEO_SOURCE = "ar.geoboundaries"
GEO_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ARG/ADM2/"
    "geoBoundaries-ARG-ADM2_simplified.geojson"
)
GEO_PAGE = "https://www.geoboundaries.org/api/current/gbOpen/ARG/ADM2/"
ACT_PAGE = f"{SITE}/actas-de-proclamacion/"
ACT_URL = f"{SITE}/wp-content/uploads/2024/12/Acta-de-Proclamacion-de-Candidatos-03-09-2023-y-24-09-2023.pdf"
TABLES = Path(__file__).with_name("mendoza_tables.json")
PROCLAMATION = Path(__file__).with_name("mendoza_proclamation.json")
# The two general-election days the act proclaims. A party's name in the act can differ from the Junta's name for
# the list it ran under in the count.
ACT_DAYS = ("2023-09-03", "2023-09-24")
ACT_PARTY = {"Cambia Mendoza - Nuevos Rumbos": "Cambia Mendoza", "Partido Sembrar": "Sembrar"}
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARG"))

DEPARTMENTS = [
    "Capital",
    "Godoy Cruz",
    "Guaymallén",
    "Las Heras",
    "Lavalle",
    "Maipú",
    "Luján de Cuyo",
    "Tupungato",
    "San Martín",
    "Junín",
    "Rivadavia",
    "Tunuyán",
    "San Carlos",
    "Santa Rosa",
    "La Paz",
    "San Rafael",
    "Malargüe",
    "General Alvear",
]
# The four electoral sections that elect the legislature, with the departments each one holds. The department
# groups were found from the published section totals, which each equal the sum of exactly one group of departments.
SECTIONS = {1: [1, 3, 4, 5], 2: [6, 9, 10, 11, 14, 15], 3: [2, 7, 8, 12, 13], 4: [16, 17, 18]}
SECTION_OF = {department: section for section, members in SECTIONS.items() for department in members}
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
ORDINAL_ES = {1: "1ra", 2: "2da", 3: "3ra", 4: "4ta"}

ELECTIONS = {
    "2023-04-30": ("primary", "2023 Mendoza municipal primaries (7 departments)"),
    "2023-06-11": ("primary", "2023 Mendoza provincial and municipal primaries"),
    "2023-09-03": ("general", "2023 Mendoza municipal general elections (7 departments)"),
    "2023-09-24": ("general", "2023 Mendoza provincial and municipal general elections"),
    "2025-10-26": ("general", "2025 Mendoza provincial legislative and municipal council elections"),
}
# Parties that run under the same number in every provincial contest of a cycle. The Junta numbers the lists of a
# municipal contest again department by department, so those are keyed by department below.
PROVINCIAL = {
    2023: {
        "181": "Partido Verde",
        "501": "Cambia Mendoza",
        "502": "Elegí Mendoza",
        "503": "Frente de Izquierda y de Trabajadores - Unidad",
        "504": "La Unión Mendocina",
    },
    2025: {
        "222": "Protectora Fuerza Política",
        "322": "Partido de los Jubilados",
        "501": "Fuerza Justicialista Mendoza",
        "502": "Frente Verde",
        "503": "Alianza Provincias Unidas - Defendamos Mendoza",
        "504": "Frente de Izquierda y de los Trabajadores - Unidad",
        "505": "La Libertad Avanza + Cambia Mendoza",
        "506": "Frente Libertario Demócrata",
    },
}
LOCAL = {
    (2023, 4, "325"): "Haciendo Las Heras",
    (2023, 5, "503"): "Frente Libertario Demócrata y Jubilados",
    (2023, 5, "506"): "Elegí Gestión Lavalle",
    (2023, 6, "303"): "Partido Parti.C.I.P.AR",
    (2023, 6, "502"): "Frente de Izquierda y de Trabajadores - Unidad",
    (2023, 6, "503"): "Frente Libertario Demócrata y Jubilados",
    (2023, 6, "506"): "Frente Elegí Maipú Crece",
    (2023, 8, "320"): "Nuevo Tupungato",
    (2023, 11, "328"): "Sembrar",
    (2023, 12, "502"): "Frente de Izquierda y de Trabajadores - Unidad",
    (2023, 12, "505"): "Frente Unidos por Tunuyán",
    (2023, 12, "506"): "Elegí Gestión Tunuyán",
    (2023, 13, "504"): "Frente Encuentro por San Carlos",
    (2023, 13, "506"): "Elegí San Carlos",
    (2023, 14, "503"): "Frente Libertario Demócrata y Jubilados",
    (2023, 14, "506"): "Elegí Santa Rosa",
    (2023, 15, "506"): "Frente Elegí Gestión La Paz",
    (2023, 16, "327"): "Partido Valle Grande",
    (2023, 16, "502"): "Frente de Izquierda y de Trabajadores - Unidad",
    (2023, 16, "503"): "Frente Libertario Demócrata y Jubilados",
    (2023, 16, "506"): "Elegí San Rafael Avanza",
    (2023, 17, "329"): "Reconstruyendo Malargüe",
    (2025, 4, "330"): "Vientos de Cambio",
    (2025, 8, "320"): "Partido Nuevo Tupungato",
    (2025, 8, "332"): "Partido Renueva Tupungato",
    (2025, 13, "321"): "Nuevos Rumbos",
}
METHOD = (
    "The definitive count (escrutinio definitivo) of the Junta Electoral de la Provincia de Mendoza, as signed "
    "tables by contest and department; the finest unit the Junta publishes is the department. Registered voters are "
    "the citizens on the roll, ballots cast are the citizens who voted, valid votes are the positive votes (votes "
    "for lists), and blank and null votes are counted apart. The tables are scans, so every number was read from the "
    "page and checked against the sums the Junta prints. In a primary, each internal list is an option of its own "
    "(its party is the front it belongs to), and votes are stored on the list. "
    "Department votes of a legislative contest are those of the department's part of the electoral section."
)


def key(kind: str, *parts: object) -> str:
    return _key(kind, *(str(part) for part in parts))


@functools.cache
def _key(kind: str, *parts: str) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *parts])))


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode())


def pdf_links() -> list[str]:
    links: list[str] = []
    for page in PAGES:
        html = raw.cached(SOURCE, page).path.read_text(errors="replace")
        for href in re.findall(r'href="([^"]*\.pdf)"', html):
            href = href.replace("&#038;", "&").replace("&amp;", "&")
            if re.search(r"Escrutinio", href, re.IGNORECASE):
                url = quote(href, safe=":/%")
                if url not in links:
                    links.append(url)
    return links


def fetch() -> None:
    for page in PAGES:
        raw.fetch(SOURCE, page, suffix=".html")
    for url in pdf_links():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, ACT_PAGE, suffix=".html")
    raw.fetch(SOURCE, ACT_URL, suffix=".pdf")
    raw.fetch(GEO_SOURCE, GEO_URL, suffix=".geojson")
    raw.fetch(GEO_SOURCE, GEO_PAGE, suffix=".json")


def packages() -> list[str]:
    return ["ar.mendoza/geo", "ar.mendoza/registry", *(f"ar.mendoza/{day}" for day in ELECTIONS)]


def parse() -> None:
    check_tables()
    geo().write()
    registry().write()
    for day in ELECTIONS:
        election(day).write()
    named = sum(len(entry["people"]) for entries in proclamation().values() for entry in entries)
    if sum(_PROCLAIMED.values()) != named:
        raise ValueError(f"the packages hold {sum(_PROCLAIMED.values())} of the {named} people the act names")


def emit() -> None:
    package.run("load", packages())


@functools.cache
def tables() -> list[dict]:
    return json.loads(TABLES.read_text())


@functools.cache
def proclamation() -> dict[tuple[str, int], list[dict]]:
    """The act's entries by contest (`gobernador`, `senador`, `diputado`, `intendente`, `concejal`) and unit: 0 for
    the province, the section number for the legislature, the department number for a municipal contest."""
    found: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for entry in json.loads(PROCLAMATION.read_text())["entries"]:
        found[(entry["contest"], entry["unit"])].append(entry)
    return dict(found)


def check_tables() -> None:
    """Refuse a table that disagrees with the arithmetic printed on its own page."""
    for t in tables():
        where = f"{t['pdf']} p{t['page']}"
        if sum(row["votes"] for row in t["rows"]) != t["positivos"]:
            raise ValueError(f"{where}: lists do not add up to the positive votes")
        if t["voted"] is not None and t["voted"] != t["positivos"] + t["nulos"] + t["blancos"]:
            raise ValueError(f"{where}: positive, null and blank votes do not add up to the voters")
        keys = [(row["code"], row["list"]) for row in t["rows"]]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{where}: a list is repeated")


def url_of(name: str) -> str:
    for url in raw.keys(SOURCE):
        if url.endswith("/" + name):
            return url
    raise LookupError(name)


# Geography ---------------------------------------------------------------------------------------------------------

GEO_ARTIFACT = key("source", "geoboundaries")
SOURCE_ID = key("source", "junta")


def _departments() -> dict[int, shapely.Geometry]:
    data = json.loads(raw.cached(GEO_SOURCE, GEO_URL).path.read_text())
    wanted = {fold(name): number for number, name in enumerate(DEPARTMENTS, 1)}
    found: dict[int, shapely.Geometry] = {}
    for feature in data["features"]:
        number = wanted.get(fold(feature["properties"]["shapeName"]))
        if number is None:
            continue
        shape = shapely.geometry.shape(feature["geometry"])
        point = shape.representative_point()
        if not (-70.7 < point.x < -66.4 and -37.7 < point.y < -32.0):
            continue  # a department of the same name in another province
        found[number] = shapely.make_valid(shape)
    if sorted(found) != list(range(1, 19)):
        raise LookupError(f"found departments {sorted(found)}")
    return found


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=GEO_ARTIFACT,
    )


def geo() -> Package:
    p = Package(
        "ar.mendoza/geo",
        SOURCE,
        "Mendoza province, its four electoral sections and its 18 departments, with boundaries from geoBoundaries",
    )
    p.external |= {ARGENTINA, COUNTRY}
    artifact = raw.cached(GEO_SOURCE, GEO_URL)
    p.add(
        "source",
        id=GEO_ARTIFACT,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab), from the Instituto Geográfico Nacional",
        title="geoBoundaries gbOpen Argentina ADM2 (simplified)",
        url=GEO_PAGE,
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries; Instituto Geográfico Nacional (Argentina) and UNHCR, OCHA ROLAC",
    )
    p.artifact(artifact, GEO_ARTIFACT)
    p.uses(raw.cached(GEO_SOURCE, GEO_PAGE))
    for code, rank, kind, noun in (
        ("ar.mendoza_provincia", 1, "administrative", "province"),
        ("ar.mendoza_seccion", 2, "electoral_district", "electoral section"),
        ("ar.mendoza_departamento", 3, "administrative", "department"),
    ):
        p.add("scheme", code=code, polity_id=ARGENTINA, kind=kind, rank=rank, noun=noun, code_system=None)
        p.add(
            "boundary_set",
            id=key("boundary_set", code, 2020),
            scheme_code=code,
            vintage="2020",
            as_of=artifact.retrieved_at.date(),
        )
    shapes = _departments()
    province = area("ar.mendoza_provincia", "mendoza")
    p.add("area", id=province, scheme_code="ar.mendoza_provincia")
    p.add("area_code", area_id=province, code="mendoza")
    p.add("area_parent", area_id=province, parent_id=COUNTRY)
    p.name(province, "Mendoza", "es", "official")
    p.name(province, "Mendoza", "en", "official", False)
    _outline(p, province, shapely.union_all(list(shapes.values()), grid_size=0.0001))
    for section, members in SECTIONS.items():
        id = area("ar.mendoza_seccion", section)
        p.add("area", id=id, scheme_code="ar.mendoza_seccion")
        p.add("area_code", area_id=id, code=str(section))
        p.add("area_parent", area_id=id, parent_id=province)
        p.name(id, f"{ORDINAL_ES[section]} Sección Electoral", "es", "official")
        p.name(id, f"{ORDINAL[section]} Electoral Section", "en", "official", False)
        _outline(p, id, shapely.union_all([shapes[m] for m in members], grid_size=0.0001))
    for number, name in enumerate(DEPARTMENTS, 1):
        id = area("ar.mendoza_departamento", number)
        p.add("area", id=id, scheme_code="ar.mendoza_departamento")
        p.add("area_code", area_id=id, code=f"{number:02d}")
        p.add("area_parent", area_id=id, parent_id=area("ar.mendoza_seccion", SECTION_OF[number]))
        p.name(id, name, "es", "official")
        _outline(p, id, shapes[number])
    return p


# Registry ----------------------------------------------------------------------------------------------------------

GOVERNOR = key("office", "governor")
SENATE = key("body", "senate")
DEPUTIES = key("body", "deputies")


def mayor(department: int) -> str:
    return key("office", "mayor", department)


def council(department: int) -> str:
    return key("body", "council", department)


def party_key(cycle: int, department: int | None, code: str) -> tuple[int, int | None, str]:
    if department is not None and (cycle, department, code) in LOCAL:
        return (cycle, department, code)
    if code in PROVINCIAL[cycle]:
        return (cycle, None, code)
    raise KeyError(f"no party for {cycle} department {department} list {code}")


def party_name(pk: tuple[int, int | None, str]) -> str:
    cycle, department, code = pk
    return PROVINCIAL[cycle][code] if department is None else LOCAL[(cycle, department, code)]


def party(pk: tuple[int, int | None, str]) -> str:
    cycle, department, code = pk
    if department is None:
        return key("party", cycle, code)
    return key("party", cycle, department, code)


def _used_parties() -> set[tuple[int, int | None, str]]:
    used: set[tuple[int, int | None, str]] = set()
    for t in tables():
        cycle = int(t["election"][:4])
        for row in t["rows"]:
            used.add(party_key(cycle, *_scope(t), row["code"]))
    return used


def _scope(t: dict) -> tuple[int | None]:
    """The department a table's list numbers belong to: municipal contests number their lists per department."""
    if t["contest"] in ("intendente", "concejal"):
        return (t["unit"][1],)
    return (None,)


def registry() -> Package:
    p = Package(
        "ar.mendoza/registry",
        SOURCE,
        "Mendoza's parties and fronts, governorship, mayoralties, provincial legislature and municipal councils",
        depends=["ar.mendoza/geo"],
    )
    p.external |= {ARGENTINA}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Honorable Junta Electoral de la Provincia de Mendoza",
        title="Escrutinios definitivos",
        url=f"{SITE}/escrutinios-definitivos-elecciones-generales-de-mendoza/",
        license="Official publication of the Junta Electoral; no license stated",
        license_url=f"{SITE}/",
        attribution="Honorable Junta Electoral de la Provincia de Mendoza, Poder Judicial de Mendoza",
    )
    p.add("office", id=GOVERNOR, polity_id=ARGENTINA, kind="governor")
    p.name(GOVERNOR, "Governor of Mendoza", "en", "official")
    p.name(GOVERNOR, "Gobernador de la Provincia de Mendoza", "es", "native", False)
    p.add("body", id=SENATE, polity_id=ARGENTINA, kind="upper_house")
    p.name(SENATE, "Senate of Mendoza", "en", "official")
    p.name(SENATE, "Honorable Senado de la Provincia de Mendoza", "es", "native", False)
    p.add("body", id=DEPUTIES, polity_id=ARGENTINA, kind="lower_house")
    p.name(DEPUTIES, "Chamber of Deputies of Mendoza", "en", "official")
    p.name(DEPUTIES, "Honorable Cámara de Diputados de la Provincia de Mendoza", "es", "native", False)
    for number, name in enumerate(DEPARTMENTS, 1):
        p.add("office", id=mayor(number), polity_id=ARGENTINA, kind="mayor")
        p.name(mayor(number), f"Mayor of {name}", "en", "official")
        p.name(mayor(number), f"Intendente de {name}", "es", "native", False)
        p.add("body", id=council(number), polity_id=ARGENTINA, kind="council")
        p.name(council(number), f"{name} Municipal Council", "en", "official")
        p.name(council(number), f"Honorable Concejo Deliberante de {name}", "es", "native", False)
    for pk in sorted(_used_parties(), key=lambda k: (k[0], k[1] or 0, k[2])):
        id = party(pk)
        p.add("party", id=id, kind="party", polity_id=ARGENTINA)
        p.name(id, party_name(pk), "es", "official")
    return p


# Elections ---------------------------------------------------------------------------------------------------------


def _words(url: str, page: int) -> tuple[float, float, dict[str, list[tuple[float, float, float, float]]]]:
    """Each word of a page with its box, from the PDF's text layer, as fractions of the page."""
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(raw.cached(SOURCE, url).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    size = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', out)
    if size is None:
        raise LookupError(f"{url} page {page} has no size")
    width, height = float(size.group(1)), float(size.group(2))
    found: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    for x0, y0, x1, y1, word in re.findall(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', out
    ):
        found[word].append((float(x0) / width, float(y0) / height, float(x1) / width, float(y1) / height))
    return width, height, found


def _label(text: str, via: str) -> str:
    if via == "text":
        found = re.search(
            r"L\S{0,3}sta\s+\d{3}\s*(?:[A-Za-z](?:\s*[-—_–]\s*[A-Za-z]{1,2})?(?![A-Za-z]))?\s*[-—]?\s*(.*)$", text
        )
        text = found.group(1) if found else ""
        text = re.sub(r"\s{2,}.*", "", text)
        text = re.sub(r"\s+\d[\d.,' ]*$", "", text)
    return " ".join(text.split()).strip(" -—")


_PROCLAIMED: dict[str, int] = {}


def _proclaim(
    p: Package,
    day: str,
    cycle: int,
    contest: tuple[str, int],
    result_set: str,
    options: dict[tuple[str, str], str],
    cells: list[dict],
    named: list[dict],
    url: str,
) -> int:
    """Add the seats the act declares for one contest and the people it names; returns the number of people.

    A list contest declares each list's seats and elects its members; a governor's ticket and a mayor's list
    each win the one seat, and their candidacies carry the head and the running mate."""
    c, scope = contest
    department = scope if c in ("intendente", "concejal") else None
    by_party: dict[str, tuple[str, str]] = {}
    for (code, list_name), option in options.items():
        if not list_name:
            by_party[fold(party_name(party_key(cycle, department, code)))] = (code, option)
    totals: Counter[str] = Counter()
    for t in cells:
        for row in t["rows"]:
            totals[options[(row["code"], row["list"])]] += row["votes"]
    named_options: set[str] = set()
    people = 0
    for entry in named:
        found = by_party.get(fold(ACT_PARTY.get(entry["party"], entry["party"])))
        if found is None:
            raise KeyError(f"{day} {c} {scope}: the act names {entry['party']!r}, which has no list in the count")
        code, option = found
        if option in named_options:
            raise ValueError(f"{day} {c} {scope}: the act names {entry['party']!r} twice")
        named_options.add(option)
        member = party(party_key(cycle, department, code))
        if c in ("gobernador", "intendente") and len(entry["people"]) != (2 if c == "gobernador" else 1):
            raise ValueError(f"{day} {c} {scope}: a ticket or mayor names the wrong number of people")
        if c in ("gobernador", "intendente") and totals[option] != max(totals.values()):
            raise ValueError(f"{day} {c} {scope}: the proclaimed list did not lead the vote")
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="won",
            seats=1 if c == "gobernador" else len(entry["people"]),
        )
        for index, printed in enumerate(entry["people"], 1):
            tag = f"{day}:{c}:{scope}:{code}:{index}"
            person = key("person", tag)
            p.add("person", id=person)
            p.name(person, printed.title(), "es", "official")
            candidacy = key("candidacy", tag)
            if c == "gobernador":
                role = "head" if index == 1 else "running_mate"
            else:
                role = "head" if c == "intendente" else "list_member"
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role=role,
                ballot_name=printed,
                member_of_party_id=member,
                is_independent=False,
            )
            if c in ("senador", "diputado", "concejal"):
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
            people += 1
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=SOURCE_ID,
        locator={"url": url, "pages": sorted({entry["page"] for entry in named})},
    )
    return people


def election(day: str) -> Package:
    kind, title = ELECTIONS[day]
    cycle = int(day[:4])
    p = Package(
        f"ar.mendoza/{day}",
        SOURCE,
        f"The {title} by department from the Junta Electoral's definitive count",
        depends=["ar.mendoza/geo", "ar.mendoza/registry"],
    )
    p.external |= {ARGENTINA}
    mine = [t for t in tables() if t["election"] == day]
    urls = {name: url_of(name) for name in {t["pdf"] for t in mine}}
    artifacts = {url: raw.cached(SOURCE, url) for url in set(urls.values())}
    artifact_ids = {url: p.artifact(a, SOURCE_ID) for url, a in artifacts.items()}
    act_url = url_of(json.loads(PROCLAMATION.read_text())["pdf"])
    if day in ACT_DAYS:
        p.artifact(raw.cached(SOURCE, act_url), SOURCE_ID)
    proclaimed = 0
    election_id = key("election", day)
    path = f"ar/{day}-mendoza-{kind}"
    p.add(
        "election",
        id=election_id,
        polity_id=ARGENTINA,
        kind=kind,
        date_from=date.fromisoformat(day),
        date_to=date.fromisoformat(day),
        date_precision="day",
        status="held",
    )
    p.name(election_id, title[0].upper() + title[1:], "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)

    def contest_of(t: dict) -> tuple[str, int]:
        c = t["contest"]
        if c == "gobernador":
            return ("gobernador", 0)
        if c in ("senador", "diputado"):
            return (c, t["unit"][1] if t["unit"][0] == "section" else SECTION_OF[t["unit"][1]])
        return (c, t["unit"][1])

    contests: dict[tuple[str, int], dict] = {}
    for t in mine:
        contests.setdefault(contest_of(t), {"cells": [], "declared": []})
        if t["contest"] == "gobernador" and t["unit"][0] == "section":
            continue  # the province total below is the declared figure; its sections only repeat the departments
        slot = "declared" if t["unit"][0] in ("section", "province") else "cells"
        contests[contest_of(t)][slot].append(t)
    unit_ids: dict[int, str] = {}
    for department in sorted({t["unit"][1] for t in mine if t["unit"][0] == "dept"}):
        unit_ids[department] = key("unit", day, department)
        p.add(
            "reporting_unit",
            id=unit_ids[department],
            polity_id=ARGENTINA,
            scheme_code="ar.mendoza_departamento",
            code=f"{day}:{department:02d}",
            area_relation="equals",
            area_id=area("ar.mendoza_departamento", department),
            kind="aggregate",
        )
    pages: dict[tuple[str, int], str] = {}

    def page_id(name: str, number: int) -> str:
        if (name, number) not in pages:
            url = urls[name]
            pages[(name, number)] = key("page", day, name, number)
            width, height, _ = _words(url, number)
            p.add(
                "source_page",
                id=pages[(name, number)],
                artifact_id=artifact_ids[url],
                page_number=number,
                image_key=f"pages/{SOURCE}/{artifacts[url].sha256}-{number}.png",
                width=round(width),
                height=round(height),
            )
        return pages[(name, number)]

    labels: dict[tuple, Counter[str]] = defaultdict(Counter)
    for (c, scope), body in contests.items():
        for t in [*body["cells"], *body["declared"]]:
            for row in t["rows"]:
                label = _label(row["label"], t["via"])
                if len(label) > 2 and not re.search(r"\d{3}", label):
                    labels[(c, scope, row["code"], row["list"])][label] += 1
    values: list[dict] = []
    locators: list[dict] = []
    for (c, scope), body in sorted(contests.items(), key=lambda item: item[0]):
        contest = key("contest", day, c, scope)
        if c == "gobernador":
            name_en, name_es, tail = "Governor of Mendoza", "Gobernador de la Provincia de Mendoza", "governor"
            extra = {
                "kind": "office",
                "office_id": GOVERNOR,
                "district_area_id": area("ar.mendoza_provincia", "mendoza"),
                "magnitude": 1,
            }
            option_kind = "ticket"
        elif c in ("senador", "diputado"):
            senate = c == "senador"
            name_en = f"{'Senate' if senate else 'Chamber of Deputies'} of Mendoza, {ORDINAL[scope]} Electoral Section"
            name_es = f"{'Senadores' if senate else 'Diputados'} provinciales, {ORDINAL_ES[scope]} Sección Electoral"
            tail = f"{'senate' if senate else 'deputies'}-section-{scope}"
            extra = {
                "kind": "list_allocation",
                "body_id": SENATE if senate else DEPUTIES,
                "district_area_id": area("ar.mendoza_seccion", scope),
            }
            option_kind = "list"
        elif c == "intendente":
            name_en = f"Mayor of {DEPARTMENTS[scope - 1]}"
            name_es = f"Intendente de {DEPARTMENTS[scope - 1]}"
            tail = f"mayor-{changeset.slug(DEPARTMENTS[scope - 1])}"
            extra = {
                "kind": "office",
                "office_id": mayor(scope),
                "district_area_id": area("ar.mendoza_departamento", scope),
                "magnitude": 1,
            }
            option_kind = "candidate"
        else:
            name_en = f"{DEPARTMENTS[scope - 1]} Municipal Council"
            name_es = f"Concejo Deliberante de {DEPARTMENTS[scope - 1]}"
            tail = f"council-{changeset.slug(DEPARTMENTS[scope - 1])}"
            extra = {
                "kind": "list_allocation",
                "body_id": council(scope),
                "district_area_id": area("ar.mendoza_departamento", scope),
            }
            option_kind = "list"
        named = proclamation().get((c, scope), []) if day in ACT_DAYS else []
        if named and extra["kind"] == "list_allocation":
            extra = {**extra, "magnitude": sum(len(entry["people"]) for entry in named)}
        p.add("contest", id=contest, election_id=election_id, round_number=1, default_result_kind="counted", **extra)
        p.name(contest, name_en, "en", "official")
        p.name(contest, name_es, "es", "native", False)
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, c, scope)
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
        department = scope if c in ("intendente", "concejal") else None
        options: dict[tuple[str, str], str] = {}
        for t in [*body["cells"], *body["declared"]]:
            for row in t["rows"]:
                k = (row["code"], row["list"])
                if k in options:
                    continue
                options[k] = key("option", day, c, scope, *k)
                pk = party_key(cycle, department, row["code"])
                counted = labels.get((c, scope, *k))
                label = counted.most_common(1)[0][0] if counted else party_name(pk)
                if row["list"]:
                    label = f"{label} (lista {row['code']} {row['list']})" if not counted else label
                p.add(
                    "contest_option",
                    id=options[k],
                    contest_id=contest,
                    kind=option_kind,
                    party_id=party(pk),
                    ballot_label=label,
                )

        if named:
            proclaimed += _proclaim(p, day, cycle, (c, scope), result_set, options, body["cells"], named, act_url)

        def add(unit: str | None, option: str | None, measure: int, value: int, flags: int = 8) -> None:
            values.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "flags": flags,
                    "value": value,
                }
            )

        for t in body["cells"]:
            unit = unit_ids[t["unit"][1]]
            page = page_id(t["pdf"], t["page"])
            _, _, words = _words(urls[t["pdf"]], t["page"])

            def locate(option: str | None, measure: int, value: int, unit: str = unit, page: str = page, words=words):
                found = words.get(str(value), [])
                if len(found) == 1:
                    x0, y0, x1, y1 = found[0]
                    locators.append(
                        {
                            "result_set_id": result_set,
                            "unit_id": unit,
                            "option_id": option,
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "page_id": page,
                            "source_id": SOURCE_ID,
                            "x0": x0,
                            "y0": y0,
                            "x1": x1,
                            "y1": y1,
                        }
                    )

            for row in t["rows"]:
                add(unit, options[(row["code"], row["list"])], 1, row["votes"])
                locate(options[(row["code"], row["list"])], 1, row["votes"])
            for measure, value in (
                (2, t["registered"]),
                (3, t["voted"]),
                (4, t["positivos"]),
                (5, t["nulos"]),
                (6, t["blancos"]),
            ):
                if value is None:
                    continue
                add(unit, None, measure, value, 9 if measure == 6 and t["derived_blank"] else 8)
                locate(None, measure, value)
        for t in body["declared"]:
            for row in t["rows"]:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=options[(row["code"], row["list"])],
                    measure=1,
                    value=row["votes"],
                )
            for measure, value in ((4, t["positivos"]), (5, t["nulos"]), (6, t["blancos"])):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    if day in ACT_DAYS:
        _PROCLAIMED[day] = proclaimed
    p.frame("result_value", pl.DataFrame(values, schema=_VALUE_SCHEMA, orient="row"))
    if locators:
        p.frame("value_locator", pl.DataFrame(locators, schema=_LOCATOR_SCHEMA, orient="row"))
    return p


_VALUE_SCHEMA = dict(FACTS["result_value"])
_LOCATOR_SCHEMA = dict(FACTS["value_locator"])
