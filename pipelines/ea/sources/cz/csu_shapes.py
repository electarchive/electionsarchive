"""Shapes of the First Republic's electoral regions, lands, and state, from OpenHistoricalMap.

OpenHistoricalMap (CC0) maps Czechoslovakia's borders as dated relations, the lands of Bohemia, Moravia, Silesia (and
from 1928 Moravia-Silesia) and Subcarpathian Ruthenia, the Těšín Silesia disputed until 1920, and the judicial districts
(soudní okresy) of Bohemia from which the Chamber of Deputies electoral law (Act 123/1920) builds the Bohemian regions.

The map has too few judicial districts of Moravia and Silesia, and none of Slovakia, to draw the regions there, so those
regions have no shape yet. Every shape here is read from those relations or built from them by union and difference:

- the state is each dated version of the Czechoslovak Republic relation;
- Bohemia, Moravia and Silesia, and Subcarpathian Ruthenia are their land relations; Slovakia is the state minus the
  other three lands;
- each Bohemian Chamber region is the union of the judicial districts the Act lists for it, and a gap that the mapped
  districts leave inside Bohemia goes to the region that encloses it or shares most of its edge, which is how the Act's
  districts that the map lacks (Prague's inner districts among them) are filled; Vitorazsko, ceded by Austria in 1920,
  is the part of Bohemia no mapped district covers along the Austrian border, and it belongs to the České Budějovice
  region by the Act;
- the Těšín region is the part of the disputed Těšín Silesia that lies within each dated outline of the state;
- each Senate region is the union of the Chamber regions that Act 124/1920 assigns to it, and the Užhorod regions of
  both chambers are the territory of Subcarpathian Ruthenia.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from urllib.parse import quote

import shapely
from pyproj import Geod
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from ea import raw

OVERPASS = "https://overpass-api.openhistoricalmap.org/api/interpreter?data="
QUERIES = {
    "state": "[out:json][timeout:600];relation(id:2835928);rel(r);out geom;",
    "lands": (
        "[out:json][timeout:600];relation(id:2856853,2856854,2857441,2857444,2857445,2857449,2857483,2747891);out geom;"
    ),
    "judicial": '[out:json][timeout:600];relation["boundary"="judicial"](47.6,12.0,51.1,24.7);out geom;',
}
BOHEMIA = (2856854, 2856853)
MORAVIA = (2857444, 2857445)
SILESIA = (2857441,)
MORAVIA_SILESIA = (2857449,)
RUTHENIA = (2857483,)
TESIN_SILESIA = 2747891

JUDICIAL: dict[str, tuple[str, ...]] = {
    "Praha": (
        "Bechyně", "Benešov", "Beroun", "Brandýs nad Labem", "Brod Český", "Brod Německý", "Čáslav", "Dobříš",
        "Habry", "Hora Kutná", "Hořovice", "Humpolec", "Chotěboř", "Janovice Uhlířské", "Jílové", "Kamenice nad Lipou",
        "Karlín", "Kolín", "Kostelec nad Černými Lesy", "Kouřim", "Kralovice Dolní", "Ledeč", "Milévsko", "Mirovice",
        "Neveklov", "Nusle", "Pacov", "Pelhřimov", "Počátky", "Polná", "Příbram", "Přibyslav", "Říčany", "Sedlčany",
        "Sedlec", "Smíchov", "Soběslav", "Štoky", "Tábor", "Vinohrady Královské", "Vlašim", "Votice", "Vožice Mladá",
        "Vršovice", "Zbraslav", "Žižkov",
    ),
    "Pardubice": (
        "Hlinsko", "Holice", "Chrudim", "Kostelec nad Orlicí", "Králíky", "Landškroun", "Litomyšl", "Mýto Vysoké",
        "Nasavrky", "Pardubice", "Polička", "Přelouč", "Skuteč", "Ústí nad Orlicí", "Žamberk",
    ),
    "Hradec Králové": (
        "Broumov", "Bydžov Nový", "Dvůr Králové", "Hořice", "Hostinné", "Hradec Králové", "Chlumec nad Cidlinou",
        "Jaroměř", "Maršov", "Náchod", "Nechanice", "Nové Město nad Metují", "Opočno", "Police", "Rokytnice",
        "Rychnov nad Kněžnou", "Skalice Česká", "Teplice", "Trutnov", "Úpice", "Žacléř",
    ),
    "Mladá Boleslav": (
        "Bělá pod Bezdězem", "Benátky Nové", "Boleslav Mladá", "Brod Železný", "Dub Český", "Fridland",
        "Hradiště Mnichovo", "Jablonec", "Jičín", "Jilemnice", "Libáň", "Liberec", "Lomnice nad Popelkou", "Mělník",
        "Městec Králové", "Nové Město pod Smrky", "Nymburk", "Paka Nová", "Poděbrady", "Roketnice", "Semily", "Sobotka",
        "Tanvald", "Turnov", "Vrchlabí", "Vysoké nad Jizerou",
    ),
    "Česká Lípa": (
        "Benešov", "Bor", "Cvikov", "Děčín", "Dubá", "Haňšpach", "Chabařovice", "Chrastava", "Jablonné Německé",
        "Kamenice Česká", "Lípa Česká", "Litoměřice", "Mimoň", "Ouštěk", "Rumburk", "Štětí", "Šluknov",
        "Ústí nad Labem", "Warnsdorf",
    ),
    "Louny": (
        "Bastiánperk", "Bílina", "Duchcov", "Hora Sv. Kateřiny", "Chomůtov", "Jirkov", "Kralupy", "Kladno",
        "Křivoklát", "Libochovice", "Litvínov Horní", "Louny", "Lovosice", "Most", "Postoloprty", "Rakovník",
        "Roudnice", "Slaný", "Strašecí Nové", "Teplice", "Unhošť", "Velvary", "Žatec",
    ),
    "Karlovy Vary": (
        "Aš", "Bečov", "Blatno", "Bochov", "Cheb", "Doupov", "Falknov", "Jáchymov", "Jesenice", "Kadaň",
        "Karlovy Vary", "Kynžvart", "Kraslice", "Loket", "Neydek", "Podbořany", "Přísečnice", "Vejprty", "Vildštejn",
        "Žlutice",
    ),
    "Plzeň": (
        "Bezdružice", "Blatná", "Blovice", "Březnice", "Dobřany", "Domažlice", "Hartmanice", "Hory Kašperské",
        "Horšův Týn", "Hostouň", "Kdýně Nová", "Klatovy", "Kralovice", "Manětín", "Marianské Lázně", "Nepomuky",
        "Nýrsko", "Planá", "Plánice", "Plzeň", "Přeštice", "Přimda", "Rokycany", "Ronšperk", "Stodo", "Stříbro",
        "Sušice", "Tachov", "Teplá", "Touškov", "Zbiroh",
    ),
    "České Budějovice": (
        "Brod Vyšší", "Budějovice České", "Bystřice Nová", "Hluboká", "Horažďovice", "Hradec Jindřichův",
        "Hrady Nové", "Chvalšiny", "Kaplice", "Krumlov", "Lišov", "Lomnice nad Lužnicí", "Netolice", "Písek",
        "Planá Horní", "Prachatice", "Strakonice", "Sviny Trhové", "Třeboň", "Týn nad Vltavou", "Veselí nad Lužnicí",
        "Vimperk", "Vodňany", "Volary", "Volyně", "Vitorazsko",
    ),
}  # fmt: skip
"""The Bohemian judicial districts of each Chamber region, as Act 123/1920 prints them (Prague itself aside)."""

GERMAN = {
    "Aš": "Asch", "Bastiánperk": "Sebastiansberg", "Bečov": "Petschau", "Bezdružice": "Weseritz", "Bílina": "Bilin",
    "Blatno": "Platten", "Bochov": "Buchau", "Bor": "Haida", "Brod Vyšší": "Hohenfurth", "Broumov": "Braunau",
    "Bystřice Nová": "Neubistritz", "Cvikov": "Zwickau", "Děčín": "Tetschen", "Doupov": "Duppau", "Dubá": "Dauba",
    "Duchcov": "Dux", "Falknov": "Falkenau", "Fridland": "Friedland", "Haňšpach": "Hainspach",
    "Hartmanice": "Hartmanitz", "Hora Sv. Kateřiny": "Katharinaberg", "Hory Kašperské": "Bergreichenstein",
    "Hostinné": "Arnau", "Hostouň": "Hostau", "Hrady Nové": "Gratzen", "Cheb": "Eger", "Chabařovice": "Karbitz",
    "Chomůtov": "Komotau", "Chrastava": "Kratzau", "Chvalšiny": "Kalsching", "Jablonec": "Gablonz an der Neiße",
    "Jablonné Německé": "Deutsch Gabel", "Jáchymov": "Sankt Joachimsthal", "Jesenice": "Jechnitz", "Jirkov": "Görkau",
    "Kadaň": "Kaaden", "Kaplice": "Kaplitz", "Karlovy Vary": "Karlsbad", "Kraslice": "Graslitz", "Krumlov": "Krumau",
    "Kynžvart": "Bad Königswart", "Landškroun": "Landskron", "Liberec": "Reichenberg", "Lípa Česká": "Böhmisch Leipa",
    "Litoměřice": "Leitmeritz", "Loket": "Elbogen", "Lovosice": "Lobositz", "Marianské Lázně": "Marienbad",
    "Maršov": "Marschendorf", "Mimoň": "Niemes", "Most": "Brüx", "Neydek": "Neudek", "Ouštěk": "Auscha",
    "Planá": "Plan", "Planá Horní": "Oberplan", "Podbořany": "Podersam", "Postoloprty": "Postelberg",
    "Přimda": "Pfraumberg", "Přísečnice": "Preßnitz", "Roketnice": "Rochlitz an der Iser", "Ronšperk": "Ronsperg",
    "Rumburk": "Rumburg", "Stodo": "Staab", "Stříbro": "Mies", "Šluknov": "Schluckenau", "Štětí": "Wegstädtl",
    "Tachov": "Tachau", "Tanvald": "Tannwald", "Teplá": "Tepl", "Touškov": "Tuschkau", "Trutnov": "Trautenau",
    "Ústí nad Labem": "Aussig", "Vildštejn": "Wildstein", "Vimperk": "Winterberg", "Volary": "Wallern",
    "Vrchlabí": "Hohenelbe", "Warnsdorf": "Warnsdorf", "Žacléř": "Schatzlar", "Žatec": "Saaz", "Žlutice": "Luditz",
}  # fmt: skip
"""Judicial districts that OpenHistoricalMap names in German, by the Act's Czech name."""
CZECH = {
    "Kdýně Nová": "Kdyně",
    "Nepomuky": "Nepomuk",
    "Milévsko": "Milevsko",
    "Horšův Týn": "Horšovský Týn",
    "Bělá pod Bezdězem": "Bělá",
    "Vysoké nad Jizerou": "Vysoké",
    "Lomnice nad Popelkou": "Lomnice",
    "Police": "Police nad Metují",
    "Rychnov nad Kněžnou": "Rychnov",
    "Kostelec nad Černými Lesy": "Kostelec nad Černými lesy",
}
"""Judicial districts that OpenHistoricalMap names differently in Czech, beyond the Act's inverted word order."""
HOMONYMS = {
    ("Česká Lípa", "Benešov"): "Gerichtsbezirk Bensen",
    ("Louny", "Teplice"): "Gerichtsbezirk Teplitz-Schönau",
    ("Hradec Králové", "Teplice"): "Gerichtsbezirk Wekelsdorf",
}
"""Names the Act gives to two districts: Benešov nad Ploučnicí, Teplice-Šanov, and Teplice nad Metují."""
SENATE = {
    "Praha": ("Praha",),
    "Hradec Králové": ("Pardubice", "Hradec Králové"),
    "Mladá Boleslav": ("Mladá Boleslav", "Česká Lípa"),
    "Louny": ("Louny", "Karlovy Vary"),
    "Plzeň": ("Plzeň", "České Budějovice"),
}
"""The Senate regions in Bohemia and the Chamber regions that make them up (Act 124/1920, § 5)."""
SPAN_DAY = date(1930, 1, 1)
"""A mapped district counts in the version that holds on this day, or failing that on the next."""
LATE_DAY = date(1921, 1, 1)
GEOD = Geod(ellps="WGS84")
SLIVER = 2.0
"""A gap of less than this many km² between mapped districts and the land border is a mapping sliver, not a missing
district, and goes to its neighbor."""


def url(name: str) -> str:
    return OVERPASS + quote(QUERIES[name], safe="")


def fetch(source: str) -> None:
    for name in QUERIES:
        raw.fetch(source, url(name), suffix=".json")


def urls() -> list[str]:
    return [url(name) for name in QUERIES]


def elements(source: str, name: str) -> list[dict[str, object]]:
    data = json.loads(raw.cached(source, url(name)).path.read_text())
    return list(data["elements"])


def _rings(ways: Iterable[list[tuple[float, float]]]) -> BaseGeometry:
    lines = [LineString(way) for way in ways if len(way) >= 2]
    return unary_union(list(polygonize(unary_union(lines))))


def shape(relation: dict[str, object]) -> BaseGeometry:
    """The polygons of a boundary relation, from the geometry of its outer and inner member ways."""
    outer: list[list[tuple[float, float]]] = []
    inner: list[list[tuple[float, float]]] = []
    members = relation.get("members")
    if not isinstance(members, list):
        raise TypeError(f"relation {relation.get('id')} has no members")
    for member in members:
        if not isinstance(member, dict) or member.get("type") != "way" or "geometry" not in member:
            continue
        points = [(float(node["lon"]), float(node["lat"])) for node in member["geometry"] if node]
        (inner if member.get("role") == "inner" else outer).append(points)
    geometry = _rings(outer)
    if inner:
        geometry = geometry.difference(_rings(inner))
    return geometry


def tags(relation: dict[str, object]) -> dict[str, str]:
    value = relation.get("tags", {})
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def day(text: str, default: date) -> date:
    if not text:
        return default
    parts = [int(part) for part in text.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


def km2(geometry: BaseGeometry) -> float:
    return abs(GEOD.geometry_area_perimeter(geometry)[0]) / 1e6


def clean(geometry: BaseGeometry) -> BaseGeometry:
    """The polygons of a geometry without slivers or pinholes under 0.05 km² left by unions and differences."""
    geometry = shapely.make_valid(geometry)
    parts: list[Polygon] = []
    for polygon in getattr(geometry, "geoms", [geometry]):
        if not isinstance(polygon, Polygon) or km2(polygon) < 0.05:
            continue
        holes = [ring for ring in polygon.interiors if km2(Polygon(ring)) >= 0.05]
        parts.append(Polygon(polygon.exterior, holes))
    return shapely.make_valid(unary_union(parts)) if parts else Polygon()


def multi(geometry: BaseGeometry) -> MultiPolygon:
    """A valid multipolygon in SRID 4326."""
    geometry = clean(geometry)
    polygons = [g for g in getattr(geometry, "geoms", [geometry]) if isinstance(g, Polygon) and not g.is_empty]
    return shapely.set_srid(MultiPolygon(polygons), 4326)


def wkb(geometry: BaseGeometry) -> str:
    return str(shapely.to_wkb(geometry, hex=True, include_srid=True))


def label(geometry: BaseGeometry) -> str:
    return wkb(shapely.set_srid(shapely.point_on_surface(geometry), 4326))


@dataclass(frozen=True)
class Version:
    start: date
    end: date
    geometry: BaseGeometry


def _versions(relations: Iterable[dict[str, object]]) -> list[Version]:
    out = []
    for relation in relations:
        t = tags(relation)
        out.append(
            Version(day(t.get("start_date", ""), date.min), day(t.get("end_date", ""), date.max), shape(relation))
        )
    return sorted(out, key=lambda v: v.start)


def _at(versions: list[Version], when: date) -> BaseGeometry:
    """The version in force on a day, or else the nearest one in time."""
    for version in versions:
        if version.start <= when < version.end:
            return version.geometry
    return min(versions, key=lambda v: min(abs((v.start - when).days), abs((v.end - when).days))).geometry


def _judicial(source: str) -> dict[str, list[tuple[date, date, dict[str, object]]]]:
    found: dict[str, list[tuple[date, date, dict[str, object]]]] = {}
    for relation in elements(source, "judicial"):
        t = tags(relation)
        start, end = day(t.get("start_date", ""), date.min), day(t.get("end_date", ""), date.max)
        found.setdefault(t.get("name", ""), []).append((start, end, relation))
    return found


def _pick(found: dict[str, list[tuple[date, date, dict[str, object]]]], name: str) -> dict[str, object] | None:
    for when in (SPAN_DAY, LATE_DAY):
        for start, end, relation in found.get(name, []):
            if start <= when < end:
                return relation
    return None


def _mapped(found: dict[str, list[tuple[date, date, dict[str, object]]]], region: str, act: str) -> str:
    if (region, act) in HOMONYMS:
        return HOMONYMS[(region, act)]
    if act in GERMAN:
        return f"Gerichtsbezirk {GERMAN[act]}"
    czech = CZECH.get(act, act)
    if _pick(found, f"Soudní okres {czech}") is not None:
        return f"Soudní okres {czech}"
    words = sorted(czech.split())
    for name in found:
        if name.startswith("Soudní okres ") and sorted(name.removeprefix("Soudní okres ").split()) == words:
            return name
    return f"Soudní okres {czech}"


@dataclass
class Bohemia:
    regions: dict[str, BaseGeometry]
    missing: dict[str, list[str]]
    filled: list[tuple[str, float]]


def bohemia(source: str, land: BaseGeometry) -> Bohemia:
    """The nine Bohemian Chamber regions as a partition of Bohemia, from the judicial districts the Act lists."""
    found = _judicial(source)
    regions: dict[str, BaseGeometry] = {}
    missing: dict[str, list[str]] = {}
    seen: dict[int, str] = {}
    for region, districts in JUDICIAL.items():
        parts = []
        for act in districts:
            relation = _pick(found, _mapped(found, region, act))
            if relation is None:
                missing.setdefault(region, []).append(act)
                continue
            rid = int(str(relation["id"]))
            if rid in seen:
                raise ValueError(f"{act}: relation {rid} already counted in {seen[rid]}")
            seen[rid] = region
            parts.append(shape(relation))
        regions[region] = unary_union(parts)
    names = list(regions)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            overlap = km2(regions[a].intersection(regions[b]))
            if overlap > 0.5:
                raise ValueError(f"regions {a} and {b} overlap by {overlap:.1f} km²")
    gaps = land.difference(unary_union(list(regions.values())))
    filled: list[tuple[str, float]] = []
    for gap in getattr(gaps, "geoms", [gaps]):
        if not isinstance(gap, Polygon) or km2(gap) < 0.05:
            continue
        edge = gap.boundary.buffer(1e-5)
        shared = {name: edge.intersection(geometry.boundary).length for name, geometry in regions.items()}
        owner = max(shared, key=lambda name: shared[name])
        if shared[owner] == 0:
            raise ValueError(f"gap of {km2(gap):.1f} km² touches no region")
        if owner not in missing and km2(gap) >= SLIVER:
            raise ValueError(f"gap of {km2(gap):.1f} km² goes to {owner}, which lacks no mapped district")
        regions[owner] = unary_union([regions[owner], gap])
        filled.append((owner, km2(gap)))
    return Bohemia({name: clean(geometry.intersection(land)) for name, geometry in regions.items()}, missing, filled)


@dataclass(frozen=True)
class Shapes:
    state: list[Version]
    """The state's own dated outlines."""
    areas: dict[tuple[str, str], list[Version]]
    """Each land, region, or variant by (scheme, key), as dated shapes merged where they don't change."""
    bohemia: Bohemia


def build(source: str, plan_from: date, plan_to: date, merge: date, chamber: str, senate: str) -> Shapes:
    """Every shape for each period between the plan's dates and the map's own dates: lands by land code, regions by
    name, each clipped to the state's outline of that period."""
    lands = {int(str(r["id"])): r for r in elements(source, "lands")}
    state = _versions(elements(source, "state"))
    bohemia_ = _versions(lands[i] for i in BOHEMIA)
    moravia = _versions(lands[i] for i in MORAVIA)
    silesia = _versions(lands[i] for i in SILESIA)
    joint = _versions(lands[i] for i in MORAVIA_SILESIA)
    ruthenia = _versions(lands[i] for i in RUTHENIA)
    tesin = shape(lands[TESIN_SILESIA])
    joint_from = joint[0].start
    cuts = {plan_from, plan_to, merge}
    for versions in (state, bohemia_, moravia, silesia, joint, ruthenia):
        for version in versions:
            cuts.update(d for d in (version.start, version.end) if plan_from < d < plan_to)
    days = sorted(cuts)
    regions = bohemia(source, _at(bohemia_, SPAN_DAY))
    areas: dict[tuple[str, str], list[Version]] = {}

    def put(key: tuple[str, str], start: date, end: date, geometry: BaseGeometry) -> None:
        geometry = clean(geometry)
        if geometry.is_empty:
            return
        versions = areas.setdefault(key, [])
        if versions and versions[-1].end == start and versions[-1].geometry.equals(geometry):
            versions[-1] = Version(versions[-1].start, end, versions[-1].geometry)
        else:
            versions.append(Version(start, end, geometry))

    for start, end in pairwise(days):
        outline = _at(state, start)
        cz = _at(bohemia_, start).intersection(outline)
        if start >= joint_from:
            ms = _at(joint, start)
        else:
            ms = unary_union([_at(moravia, start), _at(silesia, start)])
        ms = ms.intersection(outline)
        pr = _at(ruthenia, start).intersection(outline)
        sk = outline.difference(unary_union([cz, ms, pr]))
        for code, geometry in (("cechy", cz), ("morava-slezsko", ms), ("slovensko", sk), ("pr", pr)):
            put(("cz.ns1920_zeme", code), start, end, geometry)
        parts = {name: geometry.intersection(cz) for name, geometry in regions.regions.items()}
        for name, part in parts.items():
            put((chamber, name), start, end, part)
        for name, members in SENATE.items():
            put((senate, name), start, end, unary_union([parts[m] for m in members]))
        for scheme in (chamber, senate):
            put((scheme, "Užhorod"), start, end, pr)
            if start < merge:
                put((scheme, "Těšín"), start, end, tesin.intersection(outline))
    return Shapes(state, areas, regions)
