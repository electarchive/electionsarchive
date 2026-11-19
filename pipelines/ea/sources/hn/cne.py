"""Honduras: the internal party primaries (elecciones internas y primarias) of 2021 and 2025 from the results
portals of the Consejo Nacional Electoral."""

import functools
import hashlib
import json
import os
import re
import unicodedata
import urllib.parse
from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl
import shapely
from shapely.geometry import shape

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "hn.cne"
GEO_SOURCE = "hn.geoboundaries"
# The portals answer only some clients, so their JSON endpoints were saved as newline-delimited bundles, one
# document per line as {"url": ..., "data": ...}. `stage` stores each bundle under the key it was saved with.
BUNDLES = {
    "https://resultadosprimarias2021.cne.hn:8000/Resultados/": (
        "6cae75bc45d838dc8caa396d80da451e726f16562fd0c13ebf65b0439c701afe"
    ),
    "https://resultadosprimarias2021.cne.hn:8000/Resultados/GetDiputadosMovimientosDepartamentosPartidosPoliticos": (
        "8cc0fc32d961eed2747c3a9f50dfc984f3776ee29370f1232c2b1a9e1311bfdd"
    ),
    "https://resultadosprimarias2025.cne.hn:8888/assets/data/": (
        "f77caa66009085e2b1e9dd50d64438692232e52346d82d22e5670b562a6ac1e7"
    ),
    "https://resultadosprimarias2025.cne.hn:8888/assets/data/totalized_results/sites/*/<id>.json": (
        "ff03bf0bc1fe62c6dafa4efeff2213f42060f8ff82be131ea45cb9912afe199a"
    ),
    "browser://20261119-130957-446b61/hn-2025-primarias-cne-sites-totalized-part2.ndjson": (
        "2da47ea6ec3472f9ab5767048b007343dbb37e328f73906e6795cba44cab392e"
    ),
    "browser://20261119-130957-446b61/hn-2025-primarias-cne-sites-totalized-part3.ndjson": (
        "5ac80a1d98184bb8b45b39378c035e2aebdd73c0858fa0fc23975192495c2642"
    ),
}
RETRIEVED = {
    "https://resultadosprimarias2021.cne.hn:8000/Resultados/": "2026-11-19T13:32:14+00:00",
    "https://resultadosprimarias2021.cne.hn:8000/Resultados/GetDiputadosMovimientosDepartamentosPartidosPoliticos": (
        "2026-11-19T13:36:04+00:00"
    ),
    "https://resultadosprimarias2025.cne.hn:8888/assets/data/": "2026-11-19T13:16:24+00:00",
    "https://resultadosprimarias2025.cne.hn:8888/assets/data/totalized_results/sites/*/<id>.json": (
        "2026-11-19T13:18:25+00:00"
    ),
    "browser://20261119-130957-446b61/hn-2025-primarias-cne-sites-totalized-part2.ndjson": "2026-11-19T13:20:59+00:00",
    "browser://20261119-130957-446b61/hn-2025-primarias-cne-sites-totalized-part3.ndjson": "2026-11-19T13:23:32+00:00",
}
# geoBoundaries gbOpen, pinned to the commits that hold each release.
ADM2 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/905b0ba/releaseData/gbOpen/HND/ADM2/"
    "geoBoundaries-HND-ADM2_simplified.geojson"
)
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/HND/ADM1/"
    "geoBoundaries-HND-ADM1_simplified.geojson"
)


def fetch() -> None:
    raw.fetch(GEO_SOURCE, ADM2, suffix=".geojson")
    raw.fetch(GEO_SOURCE, ADM1, suffix=".geojson")
    stage()


def stage() -> None:
    """Add the saved portal bundles to the raw cache from the folder named by EA_HN_BUNDLES, one file per bundle
    named by its SHA-256, and check each against the hash it was saved with."""
    folder = Path(os.environ.get("EA_HN_BUNDLES", str(DATA / "inbox" / "hn.cne")))
    for url, digest in BUNDLES.items():
        if url in raw.keys(SOURCE):
            continue
        content = (folder / f"{digest}.bin").read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"{digest}.bin does not match its hash")
        raw.store(
            SOURCE,
            url,
            content,
            suffix=".ndjson",
            retrieved_at=datetime.fromisoformat(RETRIEVED[url]),
            media_type="application/x-ndjson",
        )


HONDURAS = str(uuid5(package.NAMESPACE, "polity\x1fHN"))
TERRITORY = "cb803b01-409a-5e7f-85e4-77e956d8e68c"
DAYS = {2021: date(2021, 3, 14), 2025: date(2025, 3, 9)}
PORTAL_2021 = "https://resultadosprimarias2021.cne.hn:8000/Resultados/"
PORTAL_2025 = "https://resultadosprimarias2025.cne.hn:8888/assets/data/"
SITE_BUNDLES = [url for url in BUNDLES if "sites" in url or url.startswith("browser://")]
ACTA_BUNDLE = (
    "https://resultadosprimarias2021.cne.hn:8000/Resultados/GetDiputadosMovimientosDepartamentosPartidosPoliticos"
)
# party code (the 2025 portal's own), official name, English name, slug, color (the 2021 portal's)
PARTIES = {
    "01": ("PARTIDO LIBERAL DE HONDURAS", "Liberal Party of Honduras", "liberal", "#e02b1d"),
    "02": ("PARTIDO NACIONAL DE HONDURAS", "National Party of Honduras", "nacional", "#0075b5"),
    "07": ("PARTIDO LIBERTAD Y REFUNDACION", "Liberty and Refoundation Party", "libre", "#e34800"),
}
BY_NAME = {name: code for code, (name, *_) in PARTIES.items()}
SHORT = {"01": "Liberal Party", "02": "National Party", "07": "Libre Party"}
SYSTEM_FPTP = str(uuid5(package.NAMESPACE, "hn.cne\x1fsystem\x1ffptp"))
PRESIDENCY = str(uuid5(package.NAMESPACE, "hn.cne\x1foffice\x1fpresident"))
MAYORALTY = str(uuid5(package.NAMESPACE, "hn.cne\x1foffice\x1fmayor"))
CONGRESS = str(uuid5(package.NAMESPACE, "hn.cne\x1fbody\x1fcongress"))
COUNCILS = str(uuid5(package.NAMESPACE, "hn.cne\x1fbody\x1fcouncils"))
CNE_SOURCE = str(uuid5(package.NAMESPACE, "hn.cne\x1fsource\x1fcne"))
GEO_SOURCE_ID = str(uuid5(package.NAMESPACE, "hn.cne\x1fsource\x1fgeoboundaries-adm2"))
METHOD_2021 = (
    "Honduras' three parties with a primary held their internal elections on 14 March 2021. A voter took the ballot "
    "of one party. The presidential ballot marked a movement's candidate, the deputy ballot marked individual "
    "candidates of a department, and the municipal ballot marked a movement's plan of mayor, deputy mayor, and "
    "councilors. Votes are the counts of each acta (polling table) as the Consejo Nacional Electoral's results "
    "portal published them."
)
METHOD_2025 = (
    "Honduras' three parties with a primary held their internal elections on 9 March 2025. A voter took the ballot "
    "of one party. The presidential ballot marked a movement's candidate, the deputy ballot marked individual "
    "candidates of a department, and the municipal ballot marked a movement's plan of mayor, deputy mayor, and "
    "councilors. Votes are the counts of each polling center as the Consejo Nacional Electoral's results portal "
    "published them. Registered voters are the portal's expected voters of the center."
)


ACCEPTED = {
    2025: {
        "accounting.votes_valid": (
            "In the eight single-seat deputy contests of Gracias a Dios and Islas de la Bahía, the portal's valid "
            "ballots of a center count ballots that marked no candidate, so the candidates' votes fall short of them "
            "in 23 centers."
        ),
        "outcomes.seats_magnitude": (
            "In 22 municipal councils the portal's allocation leaves the last seats unassigned and flags them as "
            "tied, so the seats declared fall short of the council's size."
        ),
    },
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join(["hn.cne", kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.upper()).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", plain)


def title(text: str) -> str:
    """A name the portal prints in capitals, in capitals and lowercase with the Spanish particles lowercase."""
    words = text.title().split()
    return " ".join(
        w.lower() if i and w.lower() in {"de", "del", "la", "las", "los", "y"} else w for i, w in enumerate(words)
    )


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("Ã`", "Ñ").replace("Ã‘", "Ñ").replace("D¿", "D'")).strip()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/primarias-{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        primaries(year).write()


def emit() -> None:
    package.run("load", packages())


def documents(url: str) -> Iterator[tuple[str, Any]]:
    """The documents of a saved bundle, as (endpoint url, parsed body)."""
    with raw.cached(SOURCE, url).path.open(encoding="utf-8") as file:
        for line in file:
            entry = json.loads(line)
            yield entry["url"], entry["data"]


@functools.cache
def portal() -> dict[str, Any]:
    """The 2025 portal's small documents: its contests, regions, parties, adjudications, and region totals."""
    found: dict[str, Any] = {}
    for url, data in documents(PORTAL_2025):
        found[url.split("/assets/data/", 1)[1]] = data
    return found


@functools.cache
def names() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Department codes to names, and municipality codes (department and municipality digits) to (name, dept)."""
    departments: dict[str, str] = {}
    towns: dict[str, tuple[str, str]] = {}
    regions = portal()["regions.json"]
    for region in regions:
        code = region["cc"]
        if region["l"] == 3:
            departments[code[2:4]] = region["n"].split("-", 1)[1]
        elif region["l"] == 4:
            towns[code[2:6]] = (region["n"].split("-", 1)[1], code[2:4])
    return departments, towns


# geoBoundaries names that differ from the portal's, by municipality code
RENAMED = {
    "Santa Rosa de Aguan": "0207",
    "Cabana": "0402",
    "Trinidad de Copan": "0422",
    "San Juan de Flores": "0820",
    "Villa de San Francisco": "0827",
    "Vallecillo": "0828",
    "San Marcos de Sierra": "1013",
    "San Pedro Zacapa": "1623",
    "Ramón Villeda Morales": "0905",
}
DEPARTMENT_NAMES = {
    "Gracias a Dios": "09",
    "Bay Islands": "11",
    "Choluteca": "06",
    "Colón": "02",
    "El Paraíso": "07",
    "Olancho": "15",
    "Valle": "17",
    "Atlántida": "01",
    "Comayagua": "03",
    "Copán": "04",
    "Cortés": "05",
    "Francisco Morazán": "08",
    "Intibucá": "10",
    "La Paz": "12",
    "Lempira": "13",
    "Ocotepeque": "14",
    "Santa Bárbara": "16",
    "Yoro": "18",
}


def _geometry(feature: dict[str, object]) -> shapely.Geometry:
    found = shape(feature["geometry"])
    return shapely.make_valid(found) if not found.is_valid else found


@functools.cache
def shapes() -> dict[str, shapely.Geometry]:
    """Municipality shapes by municipality code, from geoBoundaries' 2019 open set. The set draws Lake Yojoa as
    a municipality of its own, which is left out, and lacks Nueva Frontera, which has no shape."""
    towns = names()[1]
    by_name = {(dept, fold(name)): code for code, (name, dept) in towns.items()}
    ones = {
        f["properties"]["shapeName"]: _geometry(f)
        for f in json.loads(raw.cached(GEO_SOURCE, ADM1).path.read_bytes())["features"]
    }
    out: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(GEO_SOURCE, ADM2).path.read_bytes())["features"]:
        name = feature["properties"]["shapeName"]
        geometry = _geometry(feature)
        if name == "Lago de Yojoa":
            continue
        point = geometry.representative_point()
        inside = [DEPARTMENT_NAMES[n] for n, outline in ones.items() if outline.contains(point)]
        code = RENAMED.get(name) or by_name.get((inside[0] if inside else "", fold(name)))
        if code is None or code in out:
            raise ValueError(f"municipality shape {name} matches no portal municipality")
        out[code] = geometry
    return out


def _multi(geometry: shapely.Geometry) -> shapely.Geometry:
    polygons = [g for g in shapely.get_parts(geometry) if g.geom_type == "Polygon" and not g.is_empty]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def geo() -> Package:
    departments, towns = names()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Honduras' 18 departments and 298 municipalities, with the municipal shapes of geoBoundaries' 2019 open set",
    )
    p.external |= {HONDURAS, TERRITORY}
    p.add(
        "source",
        id=GEO_SOURCE_ID,
        kind="academic",
        publisher="geoBoundaries (Instituto de Conservación Forestal, Áreas Protegidas y Vida Silvestre)",
        title="geoBoundaries gbOpen Honduras ADM2, 2019",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="ICF Honduras, through geoBoundaries (William & Mary geoLab)",
    )
    p.artifact(raw.cached(GEO_SOURCE, ADM2), GEO_SOURCE_ID)
    p.uses(raw.cached(GEO_SOURCE, ADM1))
    p.uses(raw.cached(SOURCE, PORTAL_2025))
    for code, rank, kind, noun in (
        ("hn.departamento", 1, "administrative", "department"),
        ("hn.municipio", 2, "administrative", "municipality"),
        ("hn.centro", 3, "precinct", "polling center"),
        ("hn.acta", 4, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=HONDURAS, kind=kind, rank=rank, noun=noun, code_system=None)
    for code in ("hn.departamento", "hn.municipio"):
        p.add(
            "boundary_set",
            id=key("boundary_set", code, 2019),
            scheme_code=code,
            vintage="2019",
            as_of=date(2019, 1, 1),
        )
    found = shapes()

    def outline(id: str, geometry: shapely.Geometry) -> None:
        whole = _multi(geometry)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(whole, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(whole), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=GEO_SOURCE_ID,
        )

    for code, text in sorted(departments.items()):
        id = area("hn.departamento", code)
        p.add("area", id=id, scheme_code="hn.departamento")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, text, "es", "official")
        members = [found[t] for t in sorted(found) if t[:2] == code]
        outline(id, shapely.union_all(members))
    for code, (text, department) in sorted(towns.items()):
        id = area("hn.municipio", code)
        p.add("area", id=id, scheme_code="hn.municipio")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("hn.departamento", department))
        p.name(id, text, "es", "official")
        if code in found:
            outline(id, found[code])
    return p


def movement_2025(pc: int) -> str:
    return key("party", "movement", 2025, pc)


def movement_2021(party: str, name: str) -> str:
    return key("party", "movement", 2021, party, fold(name))


def contests_2025() -> dict[int, dict[str, Any]]:
    return {int(k.split("/")[1][:-5]): v for k, v in portal().items() if k.startswith("contests/")}


@functools.cache
def load_2021() -> dict[str, Any]:
    """The 2021 portal's national lists, acta rows, and final integrations of the municipal corporations."""
    found: dict[str, Any] = {"national": {}, "pres": [], "mayor": [], "deputy": [], "integration": {}}
    for url, data in documents(PORTAL_2021):
        name = url.split("?", 1)[0].split("/Resultados/")[1].strip("/")
        query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&")) if "?" in url else {}
        if name == "GetMovimientosDepartamentosPartidosPoliticos":
            found["pres"] += data["data"]
        elif name == "GetCorporacionMovimientosDepartamentosPartidosPoliticos":
            found["mayor"] += data["data"]
        elif name.endswith("Regidores"):
            found["integration"][url] = (query, data["data"])
        else:
            found["national"][name] = data["Listado"]
    for _, data in documents(ACTA_BUNDLE):
        found["deputy"] += data["data"]
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Honduras' three parties with primaries, their movements of 2021 and 2025, the presidency, the National "
        "Congress, the municipal corporations, and the plurality rule, from the Consejo Nacional Electoral",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {HONDURAS, TERRITORY}
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral de Honduras",
        title="Results portals of the 2021 and 2025 internal and primary elections",
        url="https://www.cne.hn/",
        license="Official publication; reuse terms are not stated",
        attribution="Consejo Nacional Electoral de Honduras",
    )
    for url in BUNDLES:
        p.artifact(raw.cached(SOURCE, url), CNE_SOURCE)
    p.add("body", id=CONGRESS, polity_id=HONDURAS, kind="unicameral")
    p.name(CONGRESS, "National Congress of Honduras", "en", "official")
    p.name(CONGRESS, "Congreso Nacional de Honduras", "es", "native", False)
    p.add("body", id=COUNCILS, polity_id=HONDURAS, kind="council")
    p.name(COUNCILS, "Municipal corporations of Honduras", "en", "official")
    p.name(COUNCILS, "Corporaciones municipales de Honduras", "es", "native", False)
    for id, kind, english, spanish in (
        (PRESIDENCY, "head_of_state", "President of Honduras", "Presidente de la República de Honduras"),
        (MAYORALTY, "mayor", "Mayor of a municipality of Honduras", "Alcalde municipal de Honduras"),
    ):
        p.add("office", id=id, polity_id=HONDURAS, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    p.add("electoral_system", id=SYSTEM_FPTP, polity_id=HONDURAS, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_FPTP),
        entity_id=SYSTEM_FPTP,
        kind="methodology",
        body_md="The candidate with the most votes wins.",
        lang="en",
    )
    for code, (official, english, _, color) in PARTIES.items():
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=HONDURAS)
        p.add("external_id", entity_id=id, scheme="hn.cne.party", value=code)
        p.name(id, official, "es", "official")
        p.name(id, english, "en", "short", False)
        p.add("party_color", party_id=id, color=color, valid_from=None, valid_to=None, source_id=CNE_SOURCE)
    parties = portal()["parties.json"]
    named = {entry["pc"]: entry["pn"] for entry in parties}
    used = {option["pc"] for contest in contests_2025().values() for option in contest["bos"]}
    for pc in sorted(used):
        id = movement_2025(pc)
        p.add("party", id=id, kind="list_vehicle", polity_id=HONDURAS)
        p.add("external_id", entity_id=id, scheme="hn.cne.movement.2025", value=str(pc))
        p.name(id, clean(named[pc]), "es", "official")
    seen: set[tuple[str, str]] = set()
    data = load_2021()
    for section in ("pres", "mayor", "deputy"):
        for row in data[section]:
            seen.add((BY_NAME[row["Partido"]], clean(row["Movimiento"])))
    for code, name in sorted(seen):
        id = movement_2021(code, name)
        p.add("party", id=id, kind="list_vehicle", polity_id=HONDURAS)
        p.add("external_id", entity_id=id, scheme="hn.cne.movement.2021", value=f"{code}:{fold(name)}")
        p.name(id, name, "es", "official")
    return p


def primaries(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/primarias-{year}",
        SOURCE,
        f"The {year} internal and primary elections of Honduras' Liberal, National, and Libre parties for president, "
        f"deputies, and municipal corporations, by {'polling center' if year == 2025 else 'polling table'}, from "
        "the Consejo Nacional Electoral's results portal",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {HONDURAS}
    election = key("election", year)
    p.add("election", id=election, polity_id=HONDURAS, kind="primary", date_from=day, date_to=day, status="held")
    p.name(election, f"{year} Honduran party primaries", "en", "official")
    p.name(election, f"Elecciones internas y primarias {year}", "es", "native", False)
    path = f"hn/{day}-primary"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="methodology",
        body_md=METHOD_2025 if year == 2025 else METHOD_2021,
        lang="en",
    )
    for url in [PORTAL_2025, *SITE_BUNDLES] if year == 2025 else [PORTAL_2021, ACTA_BUNDLE]:
        p.uses(raw.cached(SOURCE, url))
    build = Build(p, year, election, path)
    (_from_2025 if year == 2025 else _from_2021)(build)
    for code, reason in ACCEPTED.get(year, {}).items():
        p.accept(code, reason)
    if build.values:
        p.frame("result_value", pl.concat(build.values))
    return p


class Build:
    """Rows shared by both years' primaries: contests, options, result sets, and the values frame."""

    def __init__(self, p: Package, year: int, election: str, path: str) -> None:
        self.p = p
        self.year = year
        self.election = election
        self.path = path
        self.sets: dict[object, str] = {}
        self.options: dict[tuple[object, ...], str] = {}
        self.values: list[pl.DataFrame] = []
        self.slugs: set[str] = set()

    def contest(
        self,
        ident: object,
        kind: str,
        english: str,
        spanish: str,
        slug: str,
        *,
        office: str | None = None,
        body: str | None = None,
        district: str | None = None,
        magnitude: int | None = None,
        system: str | None = None,
        party: str,
    ) -> str:
        p = self.p
        contest = key("contest", self.year, ident)
        p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
            party_scope_id=key("party", party),
        )
        p.name(contest, english, "en", "official")
        p.name(contest, spanish, "es", "native", False)
        if slug in self.slugs:
            raise ValueError(f"slug {slug} repeats")
        self.slugs.add(slug)
        p.add("slug", path=f"{self.path}/{slug}", entity_id=contest, is_current=True)
        result_set = self.sets[ident] = key("result_set", self.year, ident)
        p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        return contest

    def option(
        self,
        ident: tuple[object, ...],
        contest: str,
        kind: str,
        movement: str,
        label: str,
        position: int | None = None,
    ) -> str:
        option = self.options[ident] = key("option", self.year, *ident)
        self.p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind=kind,
            party_id=movement,
            ballot_position=position,
            ballot_label=label,
        )
        return option

    def candidacy(
        self,
        tag: str,
        option: str,
        name: str,
        role: str,
        position: int | None = None,
    ) -> str:
        person = key("person", self.year, tag)
        self.p.add("person", id=person, birth_precision=None)
        self.p.name(person, name, "es", "official")
        candidacy = key("candidacy", self.year, tag)
        self.p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role=role,
            list_position=position,
            ballot_name=name,
        )
        return candidacy

    def add_values(self, frame: pl.DataFrame) -> None:
        """Stage rows of result_set_id, unit_id, option_id, measure, and value as whole-count facts."""
        self.values.append(
            frame.select(
                pl.col("result_set_id"),
                pl.col("unit_id"),
                pl.col("option_id").cast(pl.String),
                pl.lit(0, pl.Int16).alias("channel"),
                pl.col("measure").cast(pl.Int16),
                pl.lit(0, pl.Int16).alias("count_round"),
                pl.lit(0, pl.Int16).alias("flags"),
                pl.col("value").cast(pl.Int64),
            )
        )


def _person(entry: dict[str, Any] | None) -> list[str]:
    """The names of a candidate entry of the 2025 portal; a field of several people is comma-separated."""
    if not entry or not entry.get("cn"):
        return []
    given = str(entry["cn"]).split(",")
    family = str(entry.get("cln") or "").split(",")
    return [clean(f"{g} {f}") for g, f in zip(given, family + [""] * len(given), strict=False) if clean(g)]


ACCOUNT_2025 = {
    "1": {2: "expected-voters", 4: "total-votes", 5: "VN", 6: "VB"},
    "3": {2: "expected-voters", 4: "total-votes", 5: "VN", 6: "VB"},
    "2": {2: "expected-voters", 4: "TPVG", 5: "TPNG", 6: "TPBG"},
}


def _from_2025(b: Build) -> None:
    p = b.p
    day = DAYS[2025]
    data = portal()
    contests = contests_2025()
    departments, towns = names()
    regions = {str(r["c"]): r for r in data["regions.json"]}
    totals: dict[int, dict[tuple[int, int], int]] = {}
    for name, doc in data.items():
        if name.startswith("totalized_results/regions/"):
            totals[doc["rc"]] = {(r["cc"], r["boc"]): r["v"] for r in doc["rs"]}
    home: dict[int, tuple[int, int]] = {}
    for region in regions.values():
        for c in region["cs"] or []:
            home[c] = min(home.get(c, (99, 0)), (region["l"], int(region["c"])))
    adjudicated = {int(k.split("/")[1][:-5]): v["rs"] for k, v in data.items() if k.startswith("adjudication/")}
    allocated = {
        int(k.split("/")[1][:-5]): v["rs"] for k, v in data.items() if k.startswith("adjudication_proportional/")
    }
    councilors = {v["cc"][1:]: c for c, v in contests.items() if v["abs"] == "REGIDORES"}
    # units: the polling centers, one for each center code, which the three party trees repeat
    centers: dict[str, tuple[str, str]] = {}
    site_unit: dict[int, str] = {}
    for name, doc in data.items():
        if name.startswith("sites/"):
            for site in doc:
                code = site["cc"][2:]
                text = clean(site["n"])
                if centers.setdefault(code, (text, code[:4]))[0] != text:
                    raise ValueError(f"polling center {code} has two names")
                site_unit[site["c"]] = key("unit", day, code)
    for code, (text, town) in sorted(centers.items()):
        id = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=HONDURAS,
            scheme_code="hn.centro",
            code=f"{day}:{code}",
            area_relation="within",
            area_id=area("hn.municipio", town),
            kind="standard",
        )
        p.name(id, text, "es", "official")
    main: list[tuple[int, int, str, str]] = []
    extra: list[tuple[int, int, str, str]] = []
    accounting_sets: dict[int, str] = {}
    declared: list[tuple[str, str, int]] = []
    for c in sorted(contests):
        contest = contests[c]
        cc = str(contest["cc"])
        kind, party = cc[0], cc[1:3]
        if kind not in "123" or party not in PARTIES:
            continue
        slug = PARTIES[party][2]
        short = SHORT[party]
        place_slug = ""
        if kind == "1":
            ident = c
            b.contest(
                ident,
                "office",
                f"{short} presidential primary",
                f"Elección interna presidencial del {title(PARTIES[party][0])}",
                f"{slug}-president",
                office=PRESIDENCY,
                magnitude=1,
                system=SYSTEM_FPTP,
                party=party,
            )
        elif kind == "2":
            dept = cc[3:5]
            department = title(departments[dept])
            b.contest(
                c,
                "body_seats",
                f"{short} deputy primary, {department}",
                f"Elección interna de diputados del {title(PARTIES[party][0])}, {department}",
                f"{slug}-deputies-{changeset.slug(departments[dept])}",
                body=CONGRESS,
                district=area("hn.departamento", dept),
                magnitude=int(contest["cea"]),
                party=party,
            )
        else:
            town = cc[3:7]
            text, dept = towns[town]
            place = f"{title(text)} ({title(departments[dept])})"
            place_slug = f"{changeset.slug(departments[dept])}-{changeset.slug(text)}"
            b.contest(
                c,
                "office",
                f"{short} mayoral primary, {place}",
                f"Elección interna de alcalde del {title(PARTIES[party][0])}, {place}",
                f"{slug}-mayor-{place_slug}",
                office=MAYORALTY,
                district=area("hn.municipio", town),
                magnitude=1,
                system=SYSTEM_FPTP,
                party=party,
            )
        accounting_sets[c] = b.sets[c]
        result_set = b.sets[c]
        top = totals.get(home[c][1], {})
        for entry in sorted(contest["bos"], key=lambda o: (o["to"], o["boc"])):
            boc = entry["boc"]
            movement = movement_2025(entry["pc"])
            ident = (c, boc)
            option = b.option(
                ident,
                key("contest", 2025, c),
                "candidate" if kind == "2" else "ticket",
                movement,
                clean(entry["bon"]),
                int(entry["to"]) if str(entry["to"]).isdigit() else None,
            )
            who = _person(entry["cp"])
            if who:
                b.candidacy(f"{c}:{boc}:head", option, who[0], "head", 1)
            extra_people = _person(entry["cs"])
            for number, text in enumerate(extra_people, 1):
                b.candidacy(
                    f"{c}:{boc}:support:{number}",
                    option,
                    text,
                    "substitute" if kind == "2" else "running_mate",
                    number + 1,
                )
            main.append((c, boc, option, result_set))
            if (c, boc) in top:
                declared.append((result_set, option, top[(c, boc)]))
        # outcomes
        if kind == "1":
            boc = adjudicated[c][0]["boc"]
            p.add("outcome", result_set_id=result_set, option_id=b.options[(c, boc)], kind="won")
        elif kind == "2":
            for row in adjudicated[c]:
                p.add("outcome", result_set_id=result_set, option_id=b.options[(c, row["boc"])], kind="elected")
        else:
            rows = allocated[c]
            winner = next(r for r in rows if r["crg"] == 1)
            p.add("outcome", result_set_id=result_set, option_id=b.options[(c, winner["boc"])], kind="won")
            r = councilors[cc[1:]]
            seats = contests[r]
            b.contest(
                r,
                "body_seats",
                f"{short} councilor primary, {place}",
                f"Elección interna de regidores del {title(PARTIES[party][0])}, {place}",
                f"{slug}-councilors-{place_slug}",
                body=COUNCILS,
                district=area("hn.municipio", town),
                magnitude=int(seats["cea"]),
                party=party,
            )
            lists: dict[int, str] = {}
            for entry in contest["bos"]:
                lists[entry["pc"]] = b.option(
                    ("list", r, entry["pc"]),
                    key("contest", 2025, r),
                    "list",
                    movement_2025(entry["pc"]),
                    clean(entry["bon"]),
                )
                extra.append((c, entry["boc"], lists[entry["pc"]], b.sets[r]))
                if (c, entry["boc"]) in top:
                    declared.append((b.sets[r], lists[entry["pc"]], top[(c, entry["boc"])]))
            for entry in seats["bos"]:
                if entry["pc"] not in lists:
                    raise ValueError(f"councilor option {entry['boc']} has no plan of its movement")
                who = _person(entry["cp"])
                if who:
                    b.candidacy(f"{r}:{entry['boc']}", lists[entry["pc"]], who[0], "list_member", int(entry["to"]))
            won: Counter[int] = Counter()
            members: list[tuple[str, int]] = []
            for row in rows:
                if row["crg"] < 3 or row["pc"] is None:
                    continue
                won[row["pc"]] += 1
                if row["c"] == r:
                    members.append((key("candidacy", 2025, f"{r}:{row['boc']}"), row["pc"]))
                else:
                    plan = next(o for o in contest["bos"] if o["boc"] == row["boc"])
                    who = _person(plan["cp"])
                    candidacy = b.candidacy(f"{r}:head:{row['boc']}", lists[row["pc"]], who[0], "head", 0)
                    members.append((candidacy, row["pc"]))
            for pc, n in won.items():
                p.add("outcome", result_set_id=b.sets[r], option_id=lists[pc], kind="won", seats=n)
            for candidacy, pc in members:
                p.add(
                    "outcome",
                    result_set_id=b.sets[r],
                    option_id=lists[pc],
                    candidacy_id=candidacy,
                    kind="elected",
                )
    for result_set, option, value in declared:
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=value)
    ids = pl.DataFrame(
        {
            "c": [m[0] for m in [*main, *extra]],
            "boc": [m[1] for m in [*main, *extra]],
            "option_id": [m[2] for m in [*main, *extra]],
            "result_set_id": [m[3] for m in [*main, *extra]],
        }
    )
    units = pl.DataFrame({"site": list(site_unit), "unit_id": list(site_unit.values())})
    sets = pl.DataFrame({"c": list(accounting_sets), "result_set_id": list(accounting_sets.values())})
    for url in SITE_BUNDLES:
        sites: list[int] = []
        cs: list[int] = []
        bocs: list[int] = []
        votes: list[int] = []
        a_site: list[int] = []
        a_c: list[int] = []
        a_measure: list[int] = []
        a_value: list[int] = []
        for _, doc in documents(url):
            site = doc["rc"]
            for row in doc["rs"]:
                sites.append(site)
                cs.append(row["cc"])
                bocs.append(row["boc"])
                votes.append(row["v"])
            seen: dict[tuple[int, str], str] = {(t["cc"], t["cn"]): t["cv"] for t in doc["ct"]}
            for c in {t["cc"] for t in doc["ct"]}:
                for measure, code in ACCOUNT_2025[str(contests[c]["cc"])[0]].items():
                    if (c, code) in seen:
                        a_site.append(site)
                        a_c.append(c)
                        a_measure.append(measure)
                        a_value.append(int(seen[(c, code)]))
        table = pl.DataFrame({"site": sites, "c": cs, "boc": bocs, "value": votes})
        b.add_values(table.join(ids, on=["c", "boc"]).join(units, on="site").with_columns(measure=pl.lit(1)))
        counts = pl.DataFrame({"site": a_site, "c": a_c, "measure": a_measure, "value": a_value})
        b.add_values(counts.join(sets, on="c").join(units, on="site").with_columns(option_id=pl.lit(None, pl.String)))


def _code(text: str) -> str:
    return text.split("-", 1)[0]


def _table(rows: list[dict[str, Any]], *keys: str) -> pl.DataFrame:
    frame = pl.DataFrame(rows, infer_schema_length=None)
    return frame.with_columns(pl.col(k).map_elements(clean, return_dtype=pl.String) for k in keys if k in frame.columns)


def _from_2021(b: Build) -> None:
    p = b.p
    day = DAYS[2021]
    data = load_2021()
    national = data["national"]
    departments, towns = names()
    text_keys = ("Partido", "Departamento", "Municipio", "Movimiento", "Candidato", "CentroVotacion")
    # units: the actas, which the three parties' ballots share
    acta_rows = [
        _table(data[section], *text_keys).select(["Departamento", "Municipio", "CentroVotacion", "Acta"])
        for section in ("pres", "mayor", "deputy")
    ]
    actas = pl.concat(acta_rows).unique()
    if actas.height != actas["Acta"].n_unique():
        raise ValueError("an acta number stands in two places")
    town_of = {}
    for row in actas.iter_rows(named=True):
        town = _code(row["Departamento"]) + _code(row["Municipio"])
        if town not in towns or fold(row["Municipio"].split("-", 1)[1]) != fold(towns[town][0]):
            raise ValueError(f"acta {row['Acta']} names an unknown municipality {row['Municipio']}")
        town_of[row["Acta"]] = town
        id = key("unit", day, row["Acta"])
        p.add(
            "reporting_unit",
            id=id,
            polity_id=HONDURAS,
            scheme_code="hn.acta",
            code=f"{day}:{row['Acta']}",
            area_relation="within",
            area_id=area("hn.municipio", town),
            kind="standard",
        )
        # the portal's accents are lost in some center names (the bytes of an accented capital arrive as "Ã" and a
        # stray character), so a name that shows it is left out
        center = row["CentroVotacion"]
        broken = any(mark in center for mark in ("Ã", "?", "¿", "�"))
        p.name(id, f"Acta {row['Acta']}" if broken else f"{center}, acta {row['Acta']}", "es", "official")
    units = pl.DataFrame({"Acta": list(town_of), "unit_id": [key("unit", day, a) for a in town_of]})
    declared: list[tuple[str, str, int]] = []

    def tally(rows: pl.DataFrame, on: list[str], ids: pl.DataFrame) -> None:
        joined = rows.join(ids, on=on, how="left")
        if joined["option_id"].null_count():
            raise ValueError(f"acta rows of {on} name an option the national list lacks")
        b.add_values(
            joined.join(units, on="Acta").select(
                "result_set_id", "unit_id", "option_id", pl.lit(1).alias("measure"), pl.col("Votos").alias("value")
            )
        )

    # presidents: one contest for each party, with an option for each movement
    for code, (official, *_rest) in PARTIES.items():
        slug = PARTIES[code][2]
        b.contest(
            ("pres", code),
            "office",
            f"{SHORT[code]} presidential primary",
            f"Elección interna presidencial del {official.title()}",
            f"{slug}-president",
            office=PRESIDENCY,
            magnitude=1,
            system=SYSTEM_FPTP,
            party=code,
        )
    pres_ids = []
    for row in sorted(national["GetMovimientosPartidosPoliticos"], key=lambda r: r["IdPrimerNivel"]):
        code = BY_NAME[row["Partido"]]
        option = b.option(
            ("pres", row["IdPrimerNivel"]),
            key("contest", 2021, ("pres", code)),
            "ticket",
            movement_2021(code, clean(row["Movimiento"])),
            clean(row["Movimiento"]),
            row["IdPrimerNivel"],
        )
        b.candidacy(f"pres:{row['IdPrimerNivel']}", option, clean(row["Candidato"]), "head", 1)
        declared.append((b.sets[("pres", code)], option, int(row["Votos"])))
        pres_ids.append(
            (clean(row["Partido"]), clean(row["Movimiento"]), clean(row["Candidato"]), option, b.sets[("pres", code)])
        )
    ids = pl.DataFrame(
        pres_ids, schema=["Partido", "Movimiento", "Candidato", "option_id", "result_set_id"], orient="row"
    )
    tally(_table(data["pres"], *text_keys), ["Partido", "Movimiento", "Candidato"], ids)
    # deputies: one contest for each party and department, with an option for each candidate
    dip_ids = []
    for row in sorted(national["GetDiputadosMovimientosPartidosPoliticos"], key=lambda r: r["IdSegundoNivel"]):
        code = BY_NAME[row["Partido"]]
        dept = _code(row["Departamento"])
        ident = ("dip", code, dept)
        if ident not in b.sets:
            department = title(departments[dept])
            b.contest(
                ident,
                "body_seats",
                f"{SHORT[code]} deputy primary, {department}",
                f"Elección interna de diputados del {title(PARTIES[code][0])}, {department}",
                f"{PARTIES[code][2]}-deputies-{changeset.slug(departments[dept])}",
                body=CONGRESS,
                district=area("hn.departamento", dept),
                party=code,
            )
        option = b.option(
            ("dip", row["IdSegundoNivel"]),
            key("contest", 2021, ident),
            "candidate",
            movement_2021(code, clean(row["Movimiento"])),
            clean(row["Candidato"]),
        )
        b.candidacy(f"dip:{row['IdSegundoNivel']}", option, clean(row["Candidato"]), "head", 1)
        declared.append((b.sets[ident], option, int(row["Votos"])))
        dip_ids.append(
            (
                clean(row["Partido"]),
                clean(row["Departamento"]),
                clean(row["Movimiento"]),
                clean(row["Candidato"]),
                option,
                b.sets[ident],
            )
        )
    ids = pl.DataFrame(
        dip_ids,
        schema=["Partido", "Departamento", "Movimiento", "Candidato", "option_id", "result_set_id"],
        orient="row",
    )
    tally(_table(data["deputy"], *text_keys), ["Partido", "Departamento", "Movimiento", "Candidato"], ids)
    # mayors and councilors: a plan for each movement, then the corporation the final integration declares
    integration: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for query, rows in data["integration"].values():
        party_name, department, municipality = (
            clean(urllib.parse.unquote(query[k]).replace("+", " ")) for k in ("Partido", "Departamento", "Municipio")
        )
        integration[(party_name, department, municipality)] = rows
    plans: dict[tuple[str, str, str], tuple[str, str]] = {}
    mayor_ids = []
    for row in sorted(national["GetAlcaldesMovimientosPartidosPoliticos"], key=lambda r: r["IdTercerNivel"]):
        code = BY_NAME[row["Partido"]]
        dept, town = _code(row["Departamento"]), _code(row["Municipio"])
        ident = ("mayor", code, dept + town)
        if ident not in b.sets:
            text, _ = towns[dept + town]
            place = f"{title(text)} ({title(departments[dept])})"
            place_slug = f"{changeset.slug(departments[dept])}-{changeset.slug(text)}"
            b.contest(
                ident,
                "office",
                f"{SHORT[code]} mayoral primary, {place}",
                f"Elección interna de alcalde del {title(PARTIES[code][0])}, {place}",
                f"{PARTIES[code][2]}-mayor-{place_slug}",
                office=MAYORALTY,
                district=area("hn.municipio", dept + town),
                magnitude=1,
                system=SYSTEM_FPTP,
                party=code,
            )
        movement = clean(row["Movimiento"])
        option = b.option(
            ("mayor", row["IdTercerNivel"]),
            key("contest", 2021, ident),
            "ticket",
            movement_2021(code, movement),
            movement,
        )
        b.candidacy(f"mayor:{row['IdTercerNivel']}", option, clean(row["Candidato"]), "head", 1)
        plans[(code, dept + town, fold(movement))] = (option, movement)
        declared.append((b.sets[ident], option, int(row["Votos"])))
        mayor_ids.append(
            (
                clean(row["Partido"]),
                clean(row["Departamento"]),
                clean(row["Municipio"]),
                movement,
                clean(row["Candidato"]),
                option,
                b.sets[ident],
            )
        )
    ids = pl.DataFrame(
        mayor_ids,
        schema=["Partido", "Departamento", "Municipio", "Movimiento", "Candidato", "option_id", "result_set_id"],
        orient="row",
    )
    tally(
        _table(data["mayor"], *text_keys),
        ["Partido", "Departamento", "Municipio", "Movimiento", "Candidato"],
        ids,
    )
    mayor_values = b.values[-1]
    extra: list[tuple[str, str, str]] = []
    for (party_name, department, municipality), rows in sorted(integration.items()):
        code = BY_NAME[party_name]
        town = _code(department) + _code(municipality)
        ident = ("mayor", code, town)
        won = next(r for r in rows if r["Cargo"] == "ALCALDE")
        winner = plans[(code, town, fold(clean(str(won["Movimiento"]))))][0]
        p.add("outcome", result_set_id=b.sets[ident], option_id=winner, kind="won")
        vice = [r for r in rows if r["Cargo"] == "VICEALCALDE"]
        if vice:
            b.candidacy(f"vice:{code}:{town}", winner, clean(str(vice[0]["Nombre"])), "running_mate", 2)
        seats = [r for r in rows if str(r["Cargo"]).startswith("REGIDOR")]
        text, dept = towns[town]
        place = f"{title(text)} ({title(departments[dept])})"
        place_slug = f"{changeset.slug(departments[dept])}-{changeset.slug(text)}"
        reg = ("councilors", code, town)
        b.contest(
            reg,
            "body_seats",
            f"{SHORT[code]} councilor primary, {place}",
            f"Elección interna de regidores del {title(PARTIES[code][0])}, {place}",
            f"{PARTIES[code][2]}-councilors-{place_slug}",
            body=COUNCILS,
            district=area("hn.municipio", town),
            magnitude=len(seats),
            party=code,
        )
        lists: dict[str, str] = {}
        for (c2, t2, folded), (plan, movement) in plans.items():
            if (c2, t2) != (code, town):
                continue
            lists[folded] = b.option(
                ("councilors", code, town, folded),
                key("contest", 2021, reg),
                "list",
                movement_2021(code, movement),
                movement,
            )
            extra.append((plan, lists[folded], b.sets[reg]))
        for row in seats:
            folded = fold(clean(str(row["Movimiento"])))
            number = int(str(row["Cargo"]).split("#")[1])
            candidacy = b.candidacy(
                f"councilor:{code}:{town}:{number}", lists[folded], clean(str(row["Nombre"])), "list_member", number
            )
            p.add("outcome", result_set_id=b.sets[reg], option_id=lists[folded], candidacy_id=candidacy, kind="elected")
        for folded, n in Counter(fold(clean(str(r["Movimiento"]))) for r in seats).items():
            p.add("outcome", result_set_id=b.sets[reg], option_id=lists[folded], kind="won", seats=n)
    mirror = pl.DataFrame(extra, schema=["option_id", "list_id", "reg_set"], orient="row")
    # the councilor lists carry their plan's votes in each acta
    b.add_values(
        mayor_values.join(mirror, on="option_id").select(
            pl.col("reg_set").alias("result_set_id"),
            "unit_id",
            pl.col("list_id").alias("option_id"),
            "measure",
            "value",
        )
    )
    totals = {option: value for _, option, value in declared}
    for plan, list_id, reg_set in extra:
        declared.append((reg_set, list_id, totals[plan]))
    for result_set, option, value in declared:
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=value)
