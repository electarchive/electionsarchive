"""Ecuador: outlines of urban parishes that three city governments publish, under the National Electoral Council's codes.

INEC's boundaries (see `ec.cod_ab`) draw one outline per cantonal seat, so the urban parishes of the larger cities,
which the Council lists one by one, have none there. Three municipal governments publish their own limits as public
feature services: Quito's Metropolitan District (its 32 urban parishes), Guayaquil's municipality (its urban parishes),
and Riobamba's municipal government (its five urban parishes, from its open-data catalog). None states a license, so
the outlines carry the same "reuse terms are not stated" note as the Council's own files.

Each outline is paired to a Council code by name. A name pairs only when exactly one Council parish that INEC's layer
leaves without an outline carries it, or, where the city's own name differs from the Council's, through the curated
table below. Quito's 32 parishes are exactly the Council's remaining ones that carry Quito's names, which confirms the
pairing. Guayaquil's Sucre and Tarqui, which the Council lists under several cities, stay without an outline until a
canton is attached to the Council's codes; Tarqui is also drawn in two pieces in the city's layer.
"""

import json
import unicodedata
from datetime import date

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ec import cne
from ea.sources.ec.cod_ab import multipolygon

SOURCE = "ec.parishes_urban"
GEO = f"{SOURCE}/geo"
SCHEME = "ec.parroquia"
# None of the layers names the day its limits were drawn; the outlines run from the date INEC's parish limits do.
SINCE = date(2017, 1, 1)
QUERY = "outFields=*&outSR=4326&f=geojson"
PAGES = {
    "quito": "https://services2.arcgis.com/tqd2tWTSZ9oONNsv/arcgis/rest/services/1_Base_Territorial_WFL1/FeatureServer",
    "guayaquil": "https://services7.arcgis.com/NWWHhu45fOJtCgG3/arcgis/rest/services/LIMITE_PARROQUIAS_URBANAS/FeatureServer",
    "riobamba": "https://services9.arcgis.com/WR0heBS35BiLFAuA/arcgis/rest/services/parroquias_urbanas/FeatureServer",
}


def query(city: str, layer: int, where: str) -> str:
    return f"{PAGES[city]}/{layer}/query?where={where}&{QUERY}"


# The layer's address, the field holding a parish's name, and the layer's title and publisher.
LAYERS = {
    "quito": (
        query("quito", 1, "tipo%3D%27Urbana%27"),
        "nom_par",
        "Quito Metropolitan District, territorial base: parish limits (urban parishes)",
        "Distrito Metropolitano de Quito",
    ),
    "guayaquil": (
        query("guayaquil", 0, "1%3D1"),
        "Nam",
        "Guayaquil: limits of the urban parishes",
        "Municipalidad de Guayaquil",
    ),
    "riobamba": (
        query("riobamba", 0, "1%3D1"),
        "PARROQUIA",
        "Riobamba: urban parishes (municipal open-data catalog 2022)",
        "GAD Municipal del Cantón Riobamba, Departamento de Ordenamiento Territorial",
    ),
}
# Layer names the Council spells differently, by the Council's code: Guayaquil's Bolívar (Sagrario), Olmedo (San
# Alejo), Carbo (Concepción), and Nueve de Octubre; Quito's La Magdalena, which the Council lists twice (5540 sits among
# the parishes it already listed in 2019, 4760 among those first listed in 2025).
ALIASES = {
    ("guayaquil", "bolivar"): "5125",
    ("guayaquil", "olmedo"): "5720",
    ("guayaquil", "carbo"): "5170",
    ("guayaquil", "nueve de octubre"): "5695",
    ("quito", "la magdalena"): "5540",
}
# Council parishes whose name more than one city's urban parishes carry, so the name pairs none of them.
AMBIGUOUS = {("guayaquil", "sucre"), ("guayaquil", "tarqui")}
# Iñaquito (7170) is skipped: INEC's layer already gives it the Quito seat outline for the same period, and a parish
# outline cannot overlap another package's. Its urban outline waits for a change to the INEC package.
# Council codes and names of the parishes the layers can fill (those without an outline in `ec.cod_ab`).
CODES = {
    "5880": "roca",
    "5890": "rocafuerte",
    "5055": "ayacucho",
    "5380": "garcia moreno",
    "6185": "urdaneta",
    "5585": "letamendi",
    "5355": "febres cordero",
    "6405": "pascuales",
    "6265": "ximena",
    "7130": "belisario quevedo",
    "7135": "carcelen",
    "7140": "centro historico",
    "7145": "cochapamba",
    "7150": "comite del pueblo",
    "5220": "cotocollao",
    "7155": "chilibulo",
    "5260": "chillogallo",
    "5015": "chimbacalle",
    "7160": "el condado",
    "7165": "guamani",
    "7175": "itchimbia",
    "7180": "jipijapa",
    "7185": "kennedy",
    "7190": "la argelia",
    "7195": "la concepcion",
    "7200": "la ecuatoriana",
    "7205": "la ferroviaria",
    "5535": "la libertad",
    "7210": "la mena",
    "7215": "mariscal sucre",
    "7220": "ponceano",
    "7225": "puengasi",
    "7230": "quitumbe",
    "7235": "rumipamba",
    "7240": "san bartolo",
    "7245": "san isidro del inca",
    "7250": "san juan",
    "7255": "solanda",
    "7260": "turubamba",
    "5590": "lizarzaburu",
    "5630": "maldonado",
    "6230": "veloz",
    "6220": "velasco",
    "6290": "yaruquies",
}


def plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return " ".join("".join(c for c in decomposed if unicodedata.category(c) != "Mn").replace(".", " ").split())


def fetch() -> None:
    for url, *_ in LAYERS.values():
        raw.fetch(SOURCE, url, suffix=".geojson")


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def pairs() -> dict[str, tuple[str, shapely.Geometry]]:
    """Each paired Council code's city and outline."""
    by_name = {name: code for code, name in CODES.items()}
    found: dict[str, tuple[str, shapely.Geometry]] = {}
    for city, (url, field, *_) in LAYERS.items():
        with open(raw.cached(SOURCE, url).path, encoding="utf-8") as handle:
            features = json.load(handle)["features"]
        for feature in features:
            name = plain(str(feature["properties"][field]))
            if (city, name) in AMBIGUOUS:
                continue
            code = ALIASES.get((city, name)) or by_name.get(name)
            if code is None and name == "inaquito":
                continue
            if code is None:
                raise ValueError(f"{city}: {name} pairs with no Council parish")
            if code in found:
                raise ValueError(f"Council parish {code} is drawn twice")
            found[code] = (city, shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
    return found


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Outlines of the urban parishes of Quito, Guayaquil, and Riobamba from their municipal governments, under the "
        "National Electoral Council's parish codes",
    )
    p.external.add(SCHEME)
    sources = {}
    for city, (url, _, title, publisher) in LAYERS.items():
        sources[city] = p.id("source", city)
        p.add(
            "source",
            id=sources[city],
            kind="official_body",
            publisher=publisher,
            title=title,
            url=PAGES[city],
            license="Official publication; reuse terms are not stated",
            attribution=publisher,
            notes="The layer names no day, so the outlines are dated from 1 January 2017.",
        )
        p.artifact(raw.cached(SOURCE, url), sources[city])
    found = pairs()
    for code, (city, shape) in sorted(found.items(), key=lambda item: int(item[0])):
        id = cne.area(SCHEME, code)
        p.external.add(id)
        outline = multipolygon(shape)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=SINCE,
            valid_to=None,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=sources[city],
        )
    return p
