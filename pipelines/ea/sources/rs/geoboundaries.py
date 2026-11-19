"""Serbia: outlines of the Commission's municipalities and administrative districts from geoBoundaries.

geoBoundaries' open set carries OpenStreetMap's boundaries of 2017: 145 units at the second level of Serbia (the
cities and municipalities, with Belgrade as one unit), 25 districts at the first, and 38 municipalities of Kosovo. The
Republic Electoral Commission's gazetteer in `rs.rik/geo` names the municipalities the Serbian state counts, so the
outlines are paired with them by name, and the Kosovo municipalities are drawn as the union of the layer's units that
were one municipality under the Serbian plan: Mitrovica with North Mitrovica, Prizren with Mamusha, Priština with
Gračanica, Kamenica with Ranilug, Vitina with Kllokot, Gnjilane with Partesh, Dečani with Junik, and Kačanik with Han
i Elezit. The five Kosovo districts are the unions of their municipalities; the others are the layer's districts.

The layer draws no outline for Belgrade's 17 city municipalities, for the five city municipalities of Niš, or for
Kostolac, Sevojno, and Vranjska Banja; they stay without a shape. Six Kosovo municipalities that the gazetteer took
from the 1997 tables (Podujevo, Glogovac, Kačanik, Štimlje, Dečani, and Đakovica) count toward their districts'
outlines but get none of their own until the gazetteer that holds them is loaded. Novi Sad appears in the gazetteer twice, as
"Novi Sad-grad" and as "Grad Novi Sad", and both take the city's outline.
"""

import json
import re
import unicodedata
from datetime import date

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik

SOURCE = "rs.geoboundaries"
GEO = f"{SOURCE}/geo"
RELEASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/"
LAYERS = {
    "srb2": f"{RELEASE}SRB/ADM2/geoBoundaries-SRB-ADM2.geojson",
    "srb1": f"{RELEASE}SRB/ADM1/geoBoundaries-SRB-ADM1.geojson",
    "xkx2": f"{RELEASE}XKX/ADM2/geoBoundaries-XKX-ADM2.geojson",
}
METAS = {name: url.replace(".geojson", "-metaData.json") for name, url in LAYERS.items()}
OKRUG, OPSTINA = "rs.okrug", "rs.opstina"
KOSOVO_OKRUZI = {"25", "26", "27", "28", "29"}
# The layer's district for each okrug of Serbia proper.
DISTRICTS = {
    "00": "Belgrade",
    "01": "North Backa District",
    "02": "Central Banat District",
    "03": "North Banat District",
    "04": "South Banat District",
    "05": "West Backa District",
    "06": "South Backa District",
    "07": "Syrmia District",
    "08": "Macva District",
    "09": "Kolubara District",
    "10": "Podunavlje District",
    "11": "Branicevo District",
    "12": "Sumadija District",
    "13": "Pomoravlje District",
    "14": "Bor District",
    "15": "Zajecar District",
    "16": "Zlatibor District",
    "17": "Moravica District",
    "18": "Raska District",
    "19": "Rasina District",
    "20": "Nisava District",
    "21": "Toplica District",
    "22": "Pirot District",
    "23": "Jablanica District",
    "24": "Pcinja District",
}
# The layer's Kosovo municipalities that make up each municipality of the Serbian plan, by the Commission's code.
KOSOVO = {
    "90018": ["Viti", "Kllokot"],
    "90026": ["Vushtrri"],
    "90034": ["Drenas"],
    "90042": ["Gjilan", "Partesh"],
    "90069": ["Deçan", "Junik"],
    "90085": ["Gjakova"],
    "90093": ["Zubin Potok"],
    "90107": ["Istog"],
    "90115": ["Kaçanik", "Han i Elezit"],
    "90123": ["Klina"],
    "90131": ["Fushë Kosovë"],
    "90140": ["Kamenica", "Ranillug"],
    "90158": ["Leposaviq"],
    "90166": ["Lipjan"],
    "90182": ["Novobërdë"],
    "90204": ["Obiliq"],
    "90212": ["Rahovec"],
    "90239": ["Peja"],
    "90247": ["Podujeva"],
    "90255": ["Prizren", "Mamusha"],
    "90263": ["Pristina", "Gracanica"],
    "90271": ["Skenderaj"],
    "90280": ["Suhareka"],
    "90298": ["Mitrovica", "North Mitrovica"],
    "90301": ["Ferizaj"],
    "90310": ["Shtime"],
    "90328": ["Shtërpcë"],
    "90336": ["Dragash"],
    "90352": ["Zveçan"],
}
# Places the layer draws no outline for: the city municipalities of Belgrade and Niš, and three others.
NO_SHAPE = {
    "70092", "70106", "70114", "70122", "70149", "70157", "70165", "70173", "70181", "70190", "70203", "70211",
    "70220", "70238", "70246", "70254", "71293", "71285", "71307", "71315", "71323", "71331", "71340", "71358",
    "71366",
}  # fmt: skip
# Kosovo municipalities the Commission's gazetteer gained from the 1997 tables after the loaded `rs.rik/geo`; their
# outlines still count toward their district's union, and are drawn once that gazetteer is loaded.
PENDING = {"90034", "90069", "90085", "90115", "90247", "90310"}
ALIAS = {"80284": "89010"}
GRID = 1e-6
CYRILLIC = dict(
    zip(
        "абвгдђежзијклљмнњопрстћуфхцчџш",
        [
            "a",
            "b",
            "v",
            "g",
            "d",
            "dj",
            "e",
            "z",
            "z",
            "i",
            "j",
            "k",
            "l",
            "lj",
            "m",
            "n",
            "nj",
            "o",
            "p",
            "r",
            "s",
            "t",
            "c",
            "u",
            "f",
            "h",
            "c",
            "c",
            "dz",
            "s",
        ],
        strict=True,
    )
)


def fetch() -> None:
    for url in (*LAYERS.values(), *METAS.values()):
        raw.fetch(SOURCE, url, suffix=".json" if url.endswith("metaData.json") else ".geojson")


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def _key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"municipality of|municipal\*|municipality|city|district", "", text)
    return re.sub(r"[^a-z]", "", text)


def _latin(text: str) -> str:
    text = re.sub(r"^(градска општина|град) ", "", text.lower())
    return re.sub(r"[^a-z]", "", "".join(CYRILLIC.get(c, c) for c in re.sub(r"\(.*\)|-град", "", text)))


def _layer(name: str) -> dict[str, shapely.Geometry]:
    features = json.loads(raw.cached(SOURCE, LAYERS[name]).path.read_text(encoding="utf-8"))["features"]
    found: dict[str, shapely.Geometry] = {}
    for feature in features:
        label = str(feature["properties"]["shapeName"])
        if label in found:
            raise ValueError(f"the {name} layer names {label} twice")
        found[label] = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
    return found


def _multipolygon(shape: shapely.Geometry) -> shapely.MultiPolygon:
    gridded = shapely.set_precision(shapely.make_valid(shape), GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    if not polygons:
        raise ValueError("an outline holds no polygon")
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


def _outline(p: Package, id: str, shape: shapely.Geometry, source: str) -> None:
    shape = _multipolygon(shape)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=None,
        valid_to=None,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=source,
    )


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Outlines of Serbia's municipalities and administrative districts, and of the municipalities and districts "
        "of Kosovo under the Serbian plan, from geoBoundaries (OpenStreetMap boundaries of 2017)",
        depends=[f"{rik.SOURCE}/geo"],
    )
    sources = {
        "srb": p.id("source", "gbopen-srb"),
        "xkx": p.id("source", "gbopen-xkx"),
    }
    for key, title, lic, url in (
        (
            "srb",
            "geoBoundaries gbOpen Serbia ADM1 and ADM2, 2017",
            "Open Data Commons Open Database License 1.0",
            "https://opendatacommons.org/licenses/odbl/1-0/",
        ),
        (
            "xkx",
            "geoBoundaries gbOpen Kosovo ADM2, 2017",
            "Creative Commons Attribution-ShareAlike 2.0",
            "https://creativecommons.org/licenses/by-sa/2.0/",
        ),
    ):
        p.add(
            "source",
            id=sources[key],
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab)",
            title=title,
            url="https://www.geoboundaries.org/",
            license=lic,
            license_url=url,
            attribution="geoBoundaries (William & Mary geoLab); © OpenStreetMap contributors",
            notes=(
                "OpenStreetMap boundaries of 2017 as geoBoundaries releases them. The outlines are not dated to a "
                "year of the Commission's plan: they stand for every election that uses the area."
            ),
        )
    for name, url in {**LAYERS, **METAS}.items():
        p.artifact(raw.cached(SOURCE, url), sources["xkx" if name.startswith("xkx") else "srb"])
    for scheme in (OKRUG, OPSTINA):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", scheme, "2017"),
            scheme_code=scheme,
            vintage="2017",
            valid_from=None,
            valid_to=None,
            as_of=date(2017, 1, 1),
        )
    srb2, srb1, xkx2 = _layer("srb2"), _layer("srb1"), _layer("xkx2")
    by_name = {_key(label): label for label in srb2}
    okruzi, opstine, names = rik.gazetteer()
    members: dict[str, list[shapely.Geometry]] = {}
    drawn: set[str] = set()
    for code, (okrug, _) in sorted(opstine.items()):
        if okrug in (rik.ABROAD, rik.PRISONS) or code in NO_SHAPE:
            continue
        if okrug in KOSOVO_OKRUZI:
            shape = shapely.union_all([xkx2[f"Municipality of {part}"] for part in KOSOVO[code]])
            source = sources["xkx"]
            drawn.update(KOSOVO[code])
            members.setdefault(okrug, []).append(shape)
            if code in PENDING:
                continue
        else:
            label = by_name.get(_latin(names[ALIAS.get(code, code)] if code in ALIAS else names[code]))
            if label is None:
                raise KeyError(f"no outline for {code} {names[code]}")
            shape = srb2[label]
            source = sources["srb"]
            drawn.add(label)
        id = rik.area(OPSTINA, code)
        p.external.add(id)
        _outline(p, id, shape, source)
    if set(KOSOVO) - set(opstine):
        raise KeyError("the Commission's gazetteer lacks a Kosovo municipality the table names")
    for code in sorted(okruzi):
        if code in (rik.ABROAD, rik.PRISONS):
            continue
        id = rik.area(OKRUG, code)
        p.external.add(id)
        if code in KOSOVO_OKRUZI:
            _outline(p, id, shapely.union_all(members[code]), sources["xkx"])
            p.add(
                "note",
                id=p.id("note", id),
                entity_id=id,
                kind="methodology",
                body_md="The outline is the union of the district's municipalities in geoBoundaries' Kosovo layer.",
                lang="en",
            )
        else:
            _outline(p, id, srb1[DISTRICTS[code]], sources["srb"])
    unused = set(srb2) - drawn - {"Belgrade"}
    if unused:
        raise ValueError(f"outlines left unpaired: {sorted(unused)}")
    return p
