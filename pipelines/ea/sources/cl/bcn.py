"""Chile's commune, region, electoral district, and senatorial constituency boundaries from the BCN's vector maps.

The Biblioteca del Congreso Nacional publishes the commune and region outlines. Servel's list of territories says which
district each commune belongs to, so a district is the union of its communes, and a senatorial constituency is the union
of the region it covers.
"""

import csv
import html
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.cl import servel

SOURCE = "cl.bcn"
BCN = "https://www.bcn.cl/obtienearchivo?id=repositorio/10221/"
COMUNAS = BCN + "10396/5/comunas_final.zip"
REGIONES = BCN + "10398/2/Regiones.zip"
PAGE = "https://www.bcn.cl/siit/mapas_vectoriales"
LEY = "https://www.bcn.cl/leychile/consulta/obtxml?opt=7&idNorma="
CIRCUNSCRIPCIONES_LAW = LEY + "30204"  # Ley 18.828 (D.O. 30 August 1989), articles 180 and 181 of Ley 18.700
DISTRICTS_LAW = (
    "https://www.puntaarenas.cl/transparencia2010/normativa/L18700.pdf"  # Ley 18.700 with Ley 18.799's article 179
)
DISTRICTS_FILE = Path(__file__).with_name("distritos_1989.tsv")
CORE_FILE = Path(__file__).with_name("circ_provincial.tsv")
CORE_ELECTIONS = {"2013": date(2013, 11, 17), "2017": date(2017, 11, 19), "2021": date(2021, 11, 21)}
CORE_PATTERNS = {
    "2013": r"2022/12/\d\d_Resultados_Mesa_Cores_Ter\.xlsx",
    "2017": r"2022/12/\d\d_Resultados_Mesa_Cores_Ter-1\.xlsx",
    "2021": r"2022/12/CORE-1\.zip",
}
HISTORIAL = "https://historial.servel.cl/SitioHistorico/paginas/2005/diputados"
LAW_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fley-18700-distritos"))
CIRC_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fley-18828-circunscripciones"))
BCN_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fmapas-vectoriales"))
TOLERANCE = 60.0  # meters in Web Mercator, which stretches distances by about 1.2 at Chile's latitudes
FROM = date(2017, 1, 1)  # the districts and constituencies of the 2017 reform
DISTRICTS_FROM = date(1989, 5, 26)  # Ley 18.799 fixed the 60 districts (Diario Oficial of 26 May 1989)
CIRCS_FROM = date(1989, 8, 30)  # Ley 18.828 fixed the 19 senatorial constituencies (Diario Oficial of 30 August 1989)
LAST_ELECTION = date(2013, 11, 17)  # the last election held on the 60 districts and 19 constituencies
# The districts of the whole regions that Ley 18.828 names as constituencies.
REGION_DISTRICTS = {
    "I": (1, 2),
    "II": (3, 4),
    "III": (5, 6),
    "IV": (7, 8, 9),
    "VI": (32, 33, 34, 35),
    "XI": (59,),
    "XII": (60,),
}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/geo_1989", f"{SOURCE}/geo_core"]


def fetch() -> None:
    raw.fetch(SOURCE, COMUNAS, suffix=".zip")
    raw.fetch(SOURCE, REGIONES, suffix=".zip")
    raw.fetch("cl.servel", servel.url(servel.TERRITORIES), suffix=".xlsx")
    raw.fetch(SOURCE, CIRCUNSCRIPCIONES_LAW, suffix=".xml")
    raw.fetch(SOURCE, DISTRICTS_LAW, suffix=".pdf")


def parse() -> None:
    geo().write()
    geo_1989().write()
    geo_core().write()


def emit() -> None:
    package.run("load", packages())


def _read(url: str) -> tuple[list[dict[str, object]], list[shapely.Geometry]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    base = next(name[:-4] for name in archive.namelist() if name.endswith(".shp"))
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{base}.shp")),
        shx=io.BytesIO(archive.read(f"{base}.shx")),
        dbf=io.BytesIO(archive.read(f"{base}.dbf")),
        encoding="utf-8",
    )
    records: list[dict[str, object]] = [dict(record.as_dict()) for record in reader.records() if record is not None]
    shapes = [shapely.geometry.shape(shape.__geo_interface__) for shape in reader.shapes() if shape is not None]
    simple = shapely.make_valid(shapely.coverage_simplify(numpy.array(shapes), TOLERANCE))
    return records, list(simple)


def _wgs(shape: shapely.Geometry) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(3857, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries of Chile's 16 regions, 345 communes, 28 electoral districts, and 16 senatorial constituencies from "
        "the Biblioteca del Congreso Nacional's vector maps",
        depends=[f"{servel.SOURCE}/registry"],
    )
    communes = raw.cached(SOURCE, COMUNAS)
    regions = raw.cached(SOURCE, REGIONES)
    p.add(
        "source",
        id=BCN_SOURCE,
        kind="official_body",
        publisher="Biblioteca del Congreso Nacional de Chile",
        title="Mapas vectoriales: comunas y regiones",
        url=PAGE,
        license="Creative Commons Attribution 3.0 Chile (CC BY 3.0 CL)",
        license_url="https://creativecommons.org/licenses/by/3.0/cl/",
        attribution="Biblioteca del Congreso Nacional de Chile / BCN",
    )
    p.artifact(communes, BCN_SOURCE)
    p.artifact(regions, BCN_SOURCE)
    p.uses(raw.cached(servel.SOURCE, servel.url(servel.TERRITORIES)))
    for code in ("cl.region", "cl.comuna", "cl.distrito", "cl.circunscripcion"):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", code, communes.retrieved_at.year),
            scheme_code=code,
            vintage=str(communes.retrieved_at.year),
            as_of=communes.retrieved_at.date(),
        )

    def place(scheme: str, code: str, shape: shapely.Geometry, since: date | None) -> None:
        shape = _wgs(shape)
        p.add(
            "area_geometry",
            area_id=servel.area(scheme, code),
            valid_from=since,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=BCN_SOURCE,
        )

    territories = servel.territories()
    by_district: dict[int, list[shapely.Geometry]] = {}
    by_region: dict[int, list[shapely.Geometry]] = {}
    rows, outlines = _read(COMUNAS)
    seen: set[str] = set()
    for row, shape in zip(rows, outlines, strict=True):
        name = servel.comuna(str(row["Comuna"]))
        if name is None:
            print(f"left out: {row['Comuna']} is not one of Servel's communes", flush=True)
            continue
        _, region, district = territories[name]
        seen.add(name)
        place("cl.comuna", name, shape, None)
        by_district.setdefault(district, []).append(shape)
        by_region.setdefault(region, []).append(shape)
    for name in sorted(set(territories) - seen):
        print(f"no shape: {name} has no outline in the BCN's commune map", flush=True)
    for district, members in sorted(by_district.items()):
        place("cl.distrito", str(district), shapely.union_all(members), FROM)
    rows, outlines = _read(REGIONES)
    for row, shape in zip(rows, outlines, strict=True):
        code = int(str(row["codregion"]))
        if code not in servel.REGIONS:
            print(f"left out: {row['Region']}", flush=True)
            continue
        place("cl.region", str(code), shape, None)
    for number, region in servel.CS_REGION.items():
        place("cl.circunscripcion", str(number), shapely.union_all(by_region[region]), FROM)
    return p


class Membership:
    """One commune's district of 1989-2013 and how that district is known."""

    def __init__(self, district: int, commune: str, name: str, basis: str) -> None:
        self.district = district
        self.commune = commune
        self.name = name
        self.basis = basis


def memberships() -> list[Membership]:
    """The 346 communes with their district of 1989-2013, from `distritos_1989.tsv`.

    The `law` rows are the communes that article 179 of Ley 18.700 (as Ley 18.799 wrote it) lists in each district.
    Communes created afterward are not in the law's list. The `results` rows place each of them in the district whose
    candidates its page of Servel's 2005 deputies results lists: that page's candidates all stand in this district's
    results and in no other district's.
    """
    with DISTRICTS_FILE.open(encoding="utf-8", newline="") as file:
        return [
            Membership(int(r["district"]), r["commune"], r["name"], r["basis"])
            for r in csv.DictReader(file, delimiter="\t")
        ]


def results_pages(commune: str, district: int) -> list[str]:
    """The Servel pages that place a commune created after 1989 in its district: its own page and the district's."""
    from ea.sources.cl import historial

    code = next(code for code, name in historial.communes().items() if servel.comuna(name) == commune)
    return [
        f"{HISTORIAL}/comunas/candidatos/total/{code}.htm",
        f"{HISTORIAL}/distritos/candidatos/total/{400 + district}.htm",
    ]


def circunscripciones() -> dict[int, tuple[int, ...]]:
    """Each senatorial constituency of 1989-2013 with its districts, read from article 181 of Ley 18.700 as Ley 18.828
    wrote it. A constituency is a whole region or a list of that region's districts."""
    xml = raw.cached(SOURCE, CIRCUNSCRIPCIONES_LAW).path.read_text(encoding="utf-8")
    law = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", xml)).split())
    article = law[law.index("Artículo 181.-") : law.index("6.- Modifícase su artículo 1°")]
    found: dict[int, tuple[int, ...]] = {}
    pieces = re.split(r"(\d+)a\. Circunscripción, constituida por ", article)
    for number, piece in zip(pieces[1::2], pieces[2::2], strict=True):
        piece = piece.split(";")[0]
        if piece.startswith("los distritos electorales"):
            found[int(number)] = tuple(int(n) for n in re.findall(r"\d+", piece.split(" de la ")[0]))
        else:
            region = re.match(r"la ([IVX]+) Región", piece)
            if region is None or region.group(1) not in REGION_DISTRICTS:
                raise ValueError(f"constituency {number}: unrecognized {piece!r}")
            found[int(number)] = REGION_DISTRICTS[region.group(1)]
    districts = sorted(n for members in found.values() for n in members)
    if sorted(found) != list(range(1, 20)) or districts != list(range(1, 61)):
        raise ValueError(f"article 181 does not cover constituencies 1-19 and districts 1-60 once: {found}")
    return found


def geo_1989() -> Package:
    p = Package(
        f"{SOURCE}/geo_1989",
        SOURCE,
        "Boundaries of Chile's 60 electoral districts and 19 senatorial constituencies of 1989-2013, dissolved from "
        "the Biblioteca del Congreso Nacional's commune outlines by the communes each law assigns to them",
        depends=[f"{servel.SOURCE}/registry"],
    )
    communes = raw.cached(SOURCE, COMUNAS)
    p.add(
        "source",
        id=BCN_SOURCE,
        kind="official_body",
        publisher="Biblioteca del Congreso Nacional de Chile",
        title="Mapas vectoriales: comunas y regiones",
        url=PAGE,
        license="Creative Commons Attribution 3.0 Chile (CC BY 3.0 CL)",
        license_url="https://creativecommons.org/licenses/by/3.0/cl/",
        attribution="Biblioteca del Congreso Nacional de Chile / BCN",
    )
    p.add(
        "source",
        id=LAW_SOURCE,
        kind="official_body",
        publisher="Ministerio del Interior de Chile",
        title="Ley 18.700, orgánica constitucional sobre votaciones populares y escrutinios: artículo 179, los distritos "
        "electorales (Ley 18.799)",
        url=DISTRICTS_LAW,
        license="No license stated: official legal text; facts published with attribution",
        license_url=None,
        attribution="Ministerio del Interior de Chile",
    )
    p.add(
        "source",
        id=CIRC_SOURCE,
        kind="official_body",
        publisher="Ministerio del Interior de Chile",
        title="Ley 18.828, que modifica la Ley 18.700: artículos 180 y 181, las circunscripciones senatoriales",
        url="https://www.bcn.cl/leychile/navegar?idNorma=30204",
        license="No license stated: official legal text; facts published with attribution",
        license_url=None,
        attribution="Biblioteca del Congreso Nacional de Chile / Ministerio del Interior",
    )
    p.artifact(communes, BCN_SOURCE)
    p.artifact(raw.cached(SOURCE, DISTRICTS_LAW), LAW_SOURCE)
    p.artifact(raw.cached(SOURCE, CIRCUNSCRIPCIONES_LAW), CIRC_SOURCE)
    p.uses(raw.cached(servel.SOURCE, servel.url(servel.TERRITORIES)))
    for code, since in (("cl.distrito_1989", DISTRICTS_FROM), ("cl.circunscripcion_1989", CIRCS_FROM)):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", code, "1989"),
            scheme_code=code,
            vintage="1989",
            valid_from=since,
            valid_to=FROM,
            as_of=LAST_ELECTION,
        )

    members = memberships()
    later = [m for m in members if m.basis == "results"]
    for m in later:
        for url in results_pages(m.commune, m.district):
            p.uses(raw.cached("cl.historial", url))

    shapes: dict[str, shapely.Geometry] = {}
    rows, outlines = _read(COMUNAS)
    for row, shape in zip(rows, outlines, strict=True):
        name = servel.comuna(str(row["Comuna"]))
        if name is not None:
            shapes[name] = shape
    by_district: dict[int, list[shapely.Geometry]] = {}
    for m in members:
        if m.commune in shapes:
            by_district.setdefault(m.district, []).append(shapes[m.commune])
        else:
            print(f"no shape: {m.name} has no outline in the BCN's commune map", flush=True)
    if sorted(by_district) != list(range(1, 61)):
        raise ValueError("a district has no commune outline")

    def place(scheme: str, number: int, shape: shapely.Geometry, since: date, text: str) -> None:
        area = servel.area(scheme, str(number))
        shape = _wgs(shape)
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=since,
            valid_to=FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=BCN_SOURCE,
        )
        p.add("note", id=p.id("note", area, "methodology"), entity_id=area, kind="methodology", body_md=text, lang="en")

    method = (
        "Dissolved from the Biblioteca del Congreso Nacional's current commune outlines. {law} "
        "The outlines are today's, so a boundary that moved between communes of different districts after 1989 is "
        "not reflected."
    )
    for number, shapes_ in sorted(by_district.items()):
        mine = [m for m in members if m.district == number]
        law = "Ley 18.799 lists the district's communes."
        extra = [m for m in mine if m.basis == "results"]
        if extra:
            law += (
                " Communes created after 1989 are not in that list: "
                + ", ".join(m.name for m in extra)
                + ". Each is placed in this district because its page in Servel's results for the 2005 deputies"
                " election lists candidates of this district alone."
            )
        place("cl.distrito_1989", number, shapely.union_all(shapes_), DISTRICTS_FROM, method.format(law=law))
    for number, districts in sorted(circunscripciones().items()):
        law = "Ley 18.828 makes the constituency up of " + (
            f"district{'s' if len(districts) > 1 else ''} " + ", ".join(map(str, districts)) + "."
        )
        place(
            "cl.circunscripcion_1989",
            number,
            shapely.union_all([shape for district in districts for shape in by_district[district]]),
            CIRCS_FROM,
            method.format(law=law),
        )
    return p


def core_memberships() -> dict[str, dict[str, list[str]]]:
    """Each provincial constituency's communes by election, from `circ_provincial.tsv`.

    Servel's results by table for the regional council elections name each table's commune and provincial constituency.
    The file lists every pair that appears in the results of 2013, 2017, and 2021.
    """
    found: dict[str, dict[str, list[str]]] = {}
    with CORE_FILE.open(encoding="utf-8", newline="") as file:
        for r in csv.DictReader(file, delimiter="\t"):
            found.setdefault(r["code"], {}).setdefault(r["election"], []).append(r["commune"])
    return found


def geo_core() -> Package:
    p = Package(
        f"{SOURCE}/geo_core",
        SOURCE,
        "Boundaries of Chile's provincial constituencies for the regional council elections of 2013, 2017, and 2021, "
        "dissolved from the Biblioteca del Congreso Nacional's commune outlines by the communes of each constituency's "
        "voting tables",
        depends=[f"{servel.SOURCE}/registry"],
    )
    communes = raw.cached(SOURCE, COMUNAS)
    p.add(
        "source",
        id=BCN_SOURCE,
        kind="official_body",
        publisher="Biblioteca del Congreso Nacional de Chile",
        title="Mapas vectoriales: comunas y regiones",
        url=PAGE,
        license="Creative Commons Attribution 3.0 Chile (CC BY 3.0 CL)",
        license_url="https://creativecommons.org/licenses/by/3.0/cl/",
        attribution="Biblioteca del Congreso Nacional de Chile / BCN",
    )
    p.artifact(communes, BCN_SOURCE)
    p.uses(raw.cached(servel.SOURCE, servel.url(servel.TERRITORIES)))
    for pattern in CORE_PATTERNS.values():
        for path in servel.paths(servel.Group("core", pattern)):
            p.uses(raw.cached(servel.SOURCE, servel.url(path)))
    members = core_memberships()
    days = sorted(CORE_ELECTIONS)
    for vintage, since, until in (("2013", "2013", "2021"), ("2021", "2021", None)):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", "cl.circ_provincial", vintage),
            scheme_code="cl.circ_provincial",
            vintage=vintage,
            valid_from=CORE_ELECTIONS[since],
            valid_to=CORE_ELECTIONS[until] if until else None,
            as_of=CORE_ELECTIONS["2017" if vintage == "2013" else "2021"],
        )
    shapes: dict[str, shapely.Geometry] = {}
    rows, outlines = _read(COMUNAS)
    for row, shape in zip(rows, outlines, strict=True):
        name = servel.comuna(str(row["Comuna"]))
        if name is not None:
            shapes[name] = shape
    for code, by_election in sorted(members.items(), key=lambda item: (int(item[0].split(":")[0]), item[0])):
        names = sorted({name for names in by_election.values() for name in names})
        missing = [name for name in names if name not in shapes]
        for name in missing:
            print(f"no shape: {name} has no outline in the BCN's commune map ({code})", flush=True)
        outline = [shapes[name] for name in names if name in shapes]
        if not outline:
            raise ValueError(f"{code} has no commune outline")
        used = sorted(by_election)
        later = [day for day in days if day > used[-1]]
        area = servel.area("cl.circ_provincial", code)
        shape = _wgs(shapely.union_all(outline))
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=CORE_ELECTIONS[used[0]],
            valid_to=CORE_ELECTIONS[later[0]] if later else None,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=BCN_SOURCE,
        )
        text = (
            "Dissolved from the Biblioteca del Congreso Nacional's current commune outlines. Servel's results by table "
            f"for the regional council elections of {', '.join(used)} place the constituency's tables in "
            f"{', '.join(names)}."
        )
        if missing:
            text += " The commune map has no separate outline for " + ", ".join(missing) + "."
        p.add("note", id=p.id("note", area, "methodology"), entity_id=area, kind="methodology", body_md=text, lang="en")
    return p
