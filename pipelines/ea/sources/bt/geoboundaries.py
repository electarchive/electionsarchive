"""Bhutan: outlines of the 205 gewogs from geoBoundaries, and of the 20 dzongkhags drawn from them.

geoBoundaries' open set carries OCHA's gewog layer of 2020 as Bhutan's second administrative level. It names each gewog
in its own romanization and says nothing of the dzongkhag. The table beside this module pairs each gewog of the
Election Commission's 2011 Local Government results, by its code in `bt.ecb_lg2011/geo`, with the layer's name for it.
Most pairs differ only in spelling (Boomdeling and Bumdeling); others pair a gewog's current name with the older one the
layer keeps (Norgaygang and Bara in Samtse, Samtenling and Bhur in Sarpang). The pairs were checked against the
points OpenStreetMap places the gewogs at: 189 of the 205 fall in the outline they are paired with, 13 found no point,
and the other three found a different place of the same name. The layer swaps the names of two gewogs of Monggar: the
outline it calls Narang holds Drametse town and OpenStreetMap's Dramedtse gewog, and the outline it calls Drametse
holds Narang's health unit and OpenStreetMap's Na-Rang gewog, so the table pairs each with the other's name.

A dzongkhag's outline is the union of its gewogs' outlines, so the two levels nest. The layer's own first level puts
the gewogs of Lhamoi Dzingkha and Nichula in Sarpang, where the Commission lists them under Dagana, so it is not used.
"""

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.bt import ecb_lg2011
from ea.sources.bt.tables import DZONGKHAGS

SOURCE = "bt.geoboundaries"
GEO = f"{SOURCE}/geo"
RELEASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/BTN/ADM2/"
LAYER = f"{RELEASE}geoBoundaries-BTN-ADM2.geojson"
META = f"{RELEASE}geoBoundaries-BTN-ADM2-metaData.json"
TABLE = Path(__file__).with_name("geoboundaries_gewogs.tsv")
GEWOG = "bt.gewog"
DZONGKHAG = "bt.dzongkhag"
# The gewogs the Commission's 2011 lists name are the 205 of the layer; their outlines are dated from that year.
SINCE = date(2011, 1, 1)
# Outlines are kept to a grid of about ten centimeters.
GRID = 1e-6


def fetch() -> None:
    raw.fetch(SOURCE, LAYER, suffix=".geojson")
    raw.fetch(SOURCE, META, suffix=".json")


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def table() -> list[dict[str, str]]:
    with TABLE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def layer() -> dict[str, shapely.Geometry]:
    """Each gewog's outline by the layer's name for it; the layer names no gewog twice."""
    features = json.loads(raw.cached(SOURCE, LAYER).path.read_text(encoding="utf-8"))["features"]
    found: dict[str, shapely.Geometry] = {}
    for feature in features:
        name = str(feature["properties"]["shapeName"])
        if name in found:
            raise ValueError(f"the layer names {name} twice")
        found[name] = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
    return found


def multipolygon(shape: shapely.Geometry) -> shapely.MultiPolygon:
    gridded = shapely.set_precision(shapely.make_valid(shape), GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    if not polygons:
        raise ValueError("an outline holds no polygon")
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


def outline(p: Package, id: str, shape: shapely.MultiPolygon, source: str, since: date | None) -> None:
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        valid_to=None,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=source,
    )


def crosswalk() -> list[tuple[str, int, str]]:
    """Each gewog's code, dzongkhag index, and layer name, checked against the gewogs the Commission's files name."""
    gewogs = ecb_lg2011.geography(ecb_lg2011.read()).gewogs
    named = {code: (place.dzongkhag, place.name) for place, code in gewogs.items()}
    rows = table()
    out: list[tuple[str, int, str]] = []
    for row in rows:
        code = row["code"]
        if code not in named:
            raise KeyError(f"{code} is not a gewog of the 2011 lists")
        dz, name = named[code]
        if (DZONGKHAGS[dz], name) != (row["dzongkhag"], row["gewog"]):
            raise ValueError(f"{code} is {name} in {DZONGKHAGS[dz]}, not {row['gewog']} in {row['dzongkhag']}")
        out.append((code, dz, row["layer"]))
    if {code for code, _, _ in out} != set(named) or len(out) != len(named):
        raise ValueError("the table must pair every gewog of the 2011 lists exactly once")
    return out


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Outlines of Bhutan's 205 gewogs from geoBoundaries (OCHA's layer of 2020), and of the 20 dzongkhags as the "
        "union of their gewogs",
        depends=[f"{ecb_lg2011.SOURCE}/geo", "bt.ecb/geo"],
    )
    source = p.id("source", "gbopen-btn-adm2")
    p.add(
        "source",
        id=source,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Bhutan ADM2 (gewogs), 2020",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab); OCHA Regional Office for Asia and the Pacific",
        notes=(
            "The gewog layer of OCHA's Bhutan administrative boundaries of 2020 (205 gewogs), as geoBoundaries "
            "releases it. The gewogs it draws are the 205 that the Election Commission's 2011 Local Government "
            "results name, so the outlines are dated from 1 January 2011."
        ),
    )
    p.artifact(raw.cached(SOURCE, LAYER), source)
    p.artifact(raw.cached(SOURCE, META), source)
    meta = json.loads(raw.cached(SOURCE, META).path.read_text(encoding="utf-8"))
    if (meta["boundaryYear"], meta["admUnitCount"]) != ("2020", "205"):
        raise ValueError(f"the layer is of {meta['boundaryYear']} with {meta['admUnitCount']} units")
    for scheme in (GEWOG, DZONGKHAG):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", scheme, "2020"),
            scheme_code=scheme,
            vintage="2020",
            valid_from=SINCE,
            valid_to=None,
            as_of=date(2020, 1, 1),
        )
    shapes = layer()
    rows = crosswalk()
    used = [name for _, _, name in rows]
    if len(set(used)) != len(used) or set(used) != set(shapes):
        raise ValueError("the table must pair every gewog of the layer exactly once")
    parts: dict[int, list[shapely.Geometry]] = defaultdict(list)
    for code, dz, name in rows:
        id = ecb_lg2011.area(GEWOG, code)
        p.external.add(id)
        outline(p, id, multipolygon(shapes[name]), source, SINCE)
        parts[dz].append(shapes[name])
    if set(parts) != set(range(len(DZONGKHAGS))):
        raise ValueError("a dzongkhag has no gewog")
    for dz, found in sorted(parts.items()):
        id = ecb_lg2011.dzongkhag_area(dz)
        p.external.add(id)
        outline(p, id, multipolygon(shapely.union_all(found)), source, SINCE)
        p.add(
            "note",
            id=p.id("note", id),
            entity_id=id,
            kind="methodology",
            body_md=(
                "The outline is the union of the dzongkhag's gewogs in geoBoundaries' gewog layer of 2020, taking "
                "each gewog in the dzongkhag the Election Commission lists it under in 2011."
            ),
            lang="en",
        )
    return p
