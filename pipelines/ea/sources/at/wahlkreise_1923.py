"""Shapes of the 25 National Council constituencies of 1923 to 1930, added to the `at.parlament/geo` areas.

The Nationalratswahlordnung 1923 (BGBl. 1923/367) lists, in its Anhang zu § 3, what each constituency comprises. Seven
Vienna constituencies are unions of municipal districts. Four Lower Austrian, five Upper Austrian, and four Styrian
constituencies are unions of cities with their own statute and judicial districts (Gerichtsbezirke). The five others
are whole states (Salzburg, Tyrol, Vorarlberg, Carinthia, and Burgenland). The Gazette scan has no text layer; its
table was read from the page images.

The boundaries of the judicial districts of 1923 are not published in a form we can reuse. The shapes are therefore:

- the state constituencies (17, 18, 19, 24, 25): the state outlines of geoBoundaries (`official`);
- the Vienna constituencies 1 to 5: unions of the municipal districts' current outlines from the City of Vienna;
- constituencies 6 and 7: the same, except that Rudolfsheim and Fünfhaus (two districts in 1923, since 1938 one
  district) cannot be separated, so the whole of the 15th district goes to constituency 7, where Rudolfsheim lay, and
  Hietzing takes in Penzing, which belonged to it until 1938; and constituency 4 takes in Donaustadt, which belonged
  to Floridsdorf until 1938;
- the Lower Austrian, Upper Austrian, and Styrian constituencies: unions of the current political districts from
  geoBoundaries, each district assigned to the constituency that holds most of its judicial districts of 1923.
  Boundaries inside these constituencies and along the border with Vienna are therefore approximate.

The shapes of the approximated constituencies carry the quality `synthetic`, so the site flags them.
"""

import json
from datetime import date

import shapely
import shapely.geometry
from shapely.geometry.base import BaseGeometry

from ea import raw
from ea.package import Package

STATUTE_SOURCE = "at.parlament"
STATUTE = "https://alex.onb.ac.at/cgi-content/alex-pdf?aid=bgb&datum=19230004&pos=1241"
"""The Bundesgesetzblatt of 16 July 1923, 75th Stück, with Nr. 367 on pages 1189 to 1206 (Anhang on 1204 to 1206)."""
GEOBOUNDARIES = "at.geoboundaries"
_RELEASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/AUT"
STATES = f"{_RELEASE}/ADM1/geoBoundaries-AUT-ADM1_simplified.geojson"
DISTRICTS = f"{_RELEASE}/ADM2/geoBoundaries-AUT-ADM2_simplified.geojson"
VIENNA_SOURCE = "at.wien"
VIENNA = (
    "https://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature&version=1.1.0"
    "&typeName=ogdwien:BEZIRKSGRENZEOGD&srsName=EPSG:4326&outputFormat=json"
)
VALID_FROM = date(1923, 10, 21)
VALID_TO = date(1934, 5, 1)

STATE_NAMES = {17: "Salzburg", 18: "Tirol", 19: "Vorarlberg", 24: "Kärnten", 25: "Burgenland"}

# The municipal districts of each Vienna constituency, by their current number. Rudolfsheim and Fünfhaus are 15;
# Penzing (14) was part of Hietzing and Donaustadt (22) part of Floridsdorf.
VIENNA_DISTRICTS = {
    1: (1, 3, 4),
    2: (6, 7, 8),
    3: (9, 18, 19),
    4: (2, 20, 21, 22),
    5: (5, 10, 11),
    6: (12, 13, 14),
    7: (15, 16, 17),
}
VIENNA_EXACT = {1, 2, 3, 5}
"""Constituencies whose districts all had the same extent in 1923 as now, up to small boundary corrections."""

# The current political districts (geoBoundaries names) held by each constituency of the three states that the
# Gazette lists by judicial district. Only the judicial districts of 1923 are statutory; see the module docstring.
DISTRICT_OF = {
    8: (
        "Amstetten",
        "Lilienfeld",
        "Melk",
        "Sankt Pölten(Land)",
        "Sankt Pölten(Stadt)",
        "Scheibbs",
        "Tulln",
        "Waidhofen an der Ybbs(Stadt)",
    ),
    9: (
        "Baden",
        "Bruck an der Leitha",
        "Mödling",
        "Neunkirchen",
        "Wiener Neustadt(Land)",
        "Wiener Neustadt(Stadt)",
    ),
    10: ("Gmünd", "Horn", "Krems an der Donau(Stadt)", "Krems(Land)", "Waidhofen an der Thaya", "Zwettl"),
    11: ("Gänserndorf", "Hollabrunn", "Korneuburg", "Mistelbach"),
    12: ("Linz(Stadt)", "Linz-Land", "Urfahr-Umgebung"),
    13: ("Braunau am Inn", "Ried im Innkreis", "Schärding"),
    14: ("Eferding", "Grieskirchen", "Vöcklabruck", "Wels(Stadt)", "Wels-Land"),
    15: ("Gmunden", "Kirchdorf an der Krems", "Steyr(Stadt)", "Steyr-Land"),
    16: ("Freistadt", "Perg", "Rohrbach"),
    20: ("Graz(Stadt)", "Graz-Umgebung"),
    21: ("Deutschlandsberg", "Leibnitz", "Voitsberg"),
    22: ("Hartberg-Fürstenfeld", "Südoststeiermark", "Weiz"),
    23: ("Bruck-Mürzzuschlag", "Leoben", "Liezen", "Murau", "Murtal"),
}


def fetch() -> None:
    raw.fetch(GEOBOUNDARIES, STATES, suffix=".geojson")
    raw.fetch(GEOBOUNDARIES, DISTRICTS, suffix=".geojson")
    raw.fetch(VIENNA_SOURCE, VIENNA, suffix=".geojson")


def _features(artifact: raw.Artifact, field: str) -> dict[str, BaseGeometry]:
    data = json.loads(artifact.path.read_text(encoding="utf-8"))
    return {f["properties"][field]: shapely.geometry.shape(f["geometry"]) for f in data["features"]}


def _union(parts: list[BaseGeometry]) -> shapely.MultiPolygon:
    shape = shapely.make_valid(shapely.union_all(parts))
    polygons = [g for g in shapely.get_parts(shape) if shapely.get_type_id(g) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(polygons))), 4326)


def add(p: Package, area: dict[int, str]) -> None:
    """Add the sources and one `area_geometry` row for each of the 25 constituencies, by constituency number."""
    sid = {
        "s": p.id("source", "statute-1923"),
        "g": p.id("source", "geoboundaries"),
        "w": p.id("source", "wien-bezirke"),
    }
    p.add(
        "source",
        id=sid["s"],
        kind="official_body",
        publisher="Republik Österreich",
        title="Bundesgesetzblatt für die Republik Österreich, 75. Stück, Nr. 367: Nationalratswahlordnung, Anhang zu § 3",
        url=STATUTE,
        attribution="Österreichische Nationalbibliothek, ALEX",
    )
    p.artifact(raw.cached(STATUTE_SOURCE, STATUTE), sid["s"])
    p.add(
        "source",
        id=sid["g"],
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Austria ADM1 and ADM2 (simplified), derived from the Federal Office of Metrology and Survey",
        url="https://www.geoboundaries.org/",
        license="CC BY-SA 2.0",
        license_url="https://creativecommons.org/licenses/by-sa/2.0/",
        attribution="geoBoundaries (William & Mary geoLab); original data from the Bundesamt für Eich- und Vermessungswesen",
    )
    states = raw.cached(GEOBOUNDARIES, STATES)
    districts = raw.cached(GEOBOUNDARIES, DISTRICTS)
    p.artifact(states, sid["g"])
    p.artifact(districts, sid["g"])
    p.add(
        "source",
        id=sid["w"],
        kind="official_body",
        publisher="Stadt Wien",
        title="Bezirksgrenzen Wien (BEZIRKSGRENZEOGD)",
        url="https://www.data.gv.at/katalog/dataset/bezirksgrenzen-standardisierte-gebietsgrenzen-wien",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Datenquelle: Stadt Wien – data.wien.gv.at",
    )
    wien = raw.cached(VIENNA_SOURCE, VIENNA)
    p.artifact(wien, sid["w"])

    state_shapes = _features(states, "shapeName")
    district_shapes = _features(districts, "shapeName")
    wien_shapes = {
        int(f["properties"]["BEZNR"]): shapely.geometry.shape(f["geometry"])
        for f in json.loads(wien.path.read_text(encoding="utf-8"))["features"]
    }
    shapes: dict[int, tuple[shapely.MultiPolygon, str, str]] = {}
    for number, state in STATE_NAMES.items():
        shapes[number] = (_union([state_shapes[state]]), "official", sid["g"])
    for number, numbers in VIENNA_DISTRICTS.items():
        quality = "official" if number in VIENNA_EXACT else "synthetic"
        shapes[number] = (_union([wien_shapes[n] for n in numbers]), quality, sid["w"])
    for number, names in DISTRICT_OF.items():
        shapes[number] = (_union([district_shapes[n] for n in names]), "synthetic", sid["g"])
    used = [n for names in DISTRICT_OF.values() for n in names]
    if len(used) != len(set(used)):
        raise ValueError("a political district is assigned to two constituencies")
    if set(shapes) != set(area):
        raise ValueError("a constituency has no shape")
    # The three sources simplify their outlines differently, so neighbors overlap by slivers. Vienna's outlines come
    # first, then the states', and each later constituency gives up what an earlier one holds.
    taken: BaseGeometry | None = None
    for number in [*VIENNA_DISTRICTS, *STATE_NAMES, *DISTRICT_OF]:
        shape, quality, source_id = shapes[number]
        if taken is not None:
            shape = _union([shapely.difference(shape, taken)])
        taken = shape if taken is None else shapely.union(taken, shape)
        shapes[number] = (shape, quality, source_id)
    for number in sorted(shapes):
        shape, quality, source_id = shapes[number]
        p.add(
            "area_geometry",
            area_id=area[number],
            valid_from=VALID_FROM,
            valid_to=VALID_TO,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality=quality,
            source_id=source_id,
        )
