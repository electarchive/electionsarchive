"""Shapes for the electoral districts of 2021 that are made of whole qadhas.

Law 9 of 2020 drew 83 electoral districts in its annex, each a group of qadhas, nahiyas, or registration centers
within one governorate. The annex itself (al-Waqai al-Iraqiya no. 4603, 9 November 2020) is not read here. What
places each district instead is the Commission's own polling-center table in its results report: every regular
polling center of 2021 with its electoral district and its coordinates. Each center falls in a qadha of the Central
Statistics Office and OCHA second-level boundaries (through geoBoundaries), and a qadha belongs to the district that
most of its centers vote in.

A district is drawn only when it is the union of whole qadhas: every qadha it takes sends at least 95% of the centers
its own governorate holds there to the district, and those qadhas hold at least 95% of the district's centers. The
shape is then the union of the qadhas' outlines. A district that shares a qadha with another district, as in every
city split by registration centers, gets no shape; drawing it would need the registration-center lists of the annex.
"""

import functools
import json
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import shapely
import shapely.geometry

from ea import raw
from ea.package import Package

BASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/IRQ/ADM2/"
ADM2 = BASE + "geoBoundaries-IRQ-ADM2.geojson"
ADM2_META = BASE + "geoBoundaries-IRQ-ADM2-metaData.json"
URLS = (ADM2, ADM2_META)
SHARE = 0.95
"""The least share of a qadha's centers, and of a district's centers, that must agree for a district to be drawn."""
NEAR = 0.02
"""How far, in degrees, a center outside every outline may lie from the nearest one and still count in it."""
LAW_IN_FORCE = date(2020, 11, 9)
"""Law 9 of 2020 was published in al-Waqai al-Iraqiya no. 4603 on this day."""
REGULAR = 1
"""The polling-center table's election type for regular voting."""


@dataclass(frozen=True)
class Center:
    district: str
    lon: float
    lat: float


@dataclass(frozen=True)
class District:
    edno: str
    qadhas: tuple[tuple[str, str], ...]
    """The (shapeID, shapeName) of each qadha in the district."""
    centers: int
    inside: int
    geom: shapely.Geometry


def governorate(edno: str) -> str:
    """Baghdad's two sides share one governorate."""
    return "01" if edno[:2] in ("01", "23") else edno[:2]


@functools.cache
def _qadhas() -> tuple[tuple[str, str, shapely.Geometry], ...]:
    data = json.loads(raw.cached("iq.ihec", ADM2).path.read_bytes())
    out = []
    for feature in data["features"]:
        props = feature["properties"]
        geom = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        out.append((str(props["shapeID"]), str(props["shapeName"]), geom))
    return tuple(out)


def districts(rows: list[dict[str, object]]) -> list[District]:
    """The districts made of whole qadhas, from the rows of the polling-center table."""
    qadhas = _qadhas()
    geoms = [geom for _, _, geom in qadhas]
    tree = shapely.STRtree(geoms)
    by_qadha: dict[int, Counter[str]] = defaultdict(Counter)
    by_district: dict[str, Counter[int | None]] = defaultdict(Counter)
    for row in rows:
        if row["electionType"] != REGULAR:
            continue
        edno = f"{int(str(row['governorateCode'])):02}{int(str(row['constituencyCode'])):02}"
        try:
            point = shapely.Point(float(str(row["longitude_final"])), float(str(row["latitude_final"])))
        except ValueError:
            by_district[edno][None] += 1
            continue
        hits = [int(i) for i in tree.query(point, predicate="within")]
        if not hits:
            near = tree.nearest(point)
            nearest = None if near is None else int(near)
            hits = [nearest] if nearest is not None and shapely.distance(geoms[nearest], point) < NEAR else []
        index = hits[0] if hits else None
        by_district[edno][index] += 1
        if index is not None:
            by_qadha[index][edno] += 1
    owner = {index: counts.most_common(1)[0][0] for index, counts in by_qadha.items()}
    found: list[District] = []
    for edno, counts in sorted(by_district.items()):
        mine = sorted(index for index, district in owner.items() if district == edno)
        if not mine:
            continue
        total = sum(counts.values())
        inside = sum(counts[index] for index in mine)
        pure = all(
            by_qadha[index][edno]
            >= SHARE * sum(n for d, n in by_qadha[index].items() if governorate(d) == governorate(edno))
            for index in mine
        )
        if pure and inside >= SHARE * total:
            geom = shapely.union_all([geoms[index] for index in mine])
            found.append(District(edno, tuple((qadhas[i][0], qadhas[i][1]) for i in mine), total, inside, geom))
    return found


def _multi(geometry: shapely.Geometry) -> shapely.Geometry:
    polygons = [g for g in shapely.get_parts(geometry) if g.geom_type == "Polygon" and not g.is_empty]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def add(
    p: Package,
    rows: list[dict[str, object]],
    pages: list[raw.Artifact],
    area: dict[str, str],
    adm2_source: str,
    centers_source: str,
    note_id: Callable[[str], str],
) -> list[District]:
    """Draw the districts made of whole qadhas into the geo package. `area` maps each district number to its area."""
    p.add(
        "source",
        id=adm2_source,
        kind="official_body",
        publisher="Iraq Central Statistics Office and OCHA Iraq, through geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Iraq ADM2 (qadhas), 2021",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="Iraq Central Statistics Office and OCHA Iraq, through geoBoundaries",
        notes="101 qadhas, source data of 19 January 2023, built 12 December 2023, geoBoundaries commit 9469f09.",
    )
    for url in URLS:
        p.artifact(raw.cached("iq.ihec", url), adm2_source)
    p.add(
        "source",
        id=centers_source,
        kind="official_body",
        publisher="Independent High Electoral Commission of Iraq",
        title="Polling centers of the 2021 Council of Representatives election, with coordinates (results report)",
        url="https://ihec.iq/result-2021/",
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent High Electoral Commission of Iraq",
        notes="The report's polling-center table: each center's electoral district, type of voting, and coordinates.",
    )
    for artifact in pages:
        p.artifact(artifact, centers_source)
    built = districts(rows)
    for d in built:
        whole = _multi(d.geom)
        p.add(
            "area_geometry",
            area_id=area[d.edno],
            valid_from=LAW_IN_FORCE,
            geom=shapely.to_wkb(whole, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(whole), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=adm2_source,
        )
        names = "; ".join(f"{name} (geoBoundaries {id})" for id, name in d.qadhas)
        p.add(
            "note",
            id=note_id(d.edno),
            entity_id=area[d.edno],
            kind="methodology",
            body_md=(
                "Drawn as the union of whole qadhas of the Central Statistics Office and OCHA boundaries: "
                f"{names}. Law 9 of 2020 (annex; al-Waqai al-Iraqiya no. 4603, 9 November 2020) defines the "
                "district. The qadhas are those its polling centers lie in, by the coordinates of the Commission's "
                f"polling-center table: {d.inside} of the district's {d.centers} regular polling centers fall in "
                f"them, and each sends at least {SHARE:.0%} of the centers its governorate holds there to this "
                "district."
            ),
            lang="en",
        )
    return built
