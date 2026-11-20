"""What the two autonomous provinces of Trentino-Alto Adige share: their polities, areas, councils, and outlines.

Trentino-Alto Adige's regional council was elected directly from 1948 to 1968. Since 1973 the councils of its two
autonomous provinces, Trento and Bolzano, have been elected together, and their members sit together as the
regional council. The archive models each province as its own polity (IT-TN and IT-BZ, kind county, as the other
provinces are) under the region's polity IT-32, with its own council, and holds each provincial election as an
election of that polity. A direct regional council election, as in 1948-1968, belongs to the polity IT-32.

The provinces' areas join the `it.provincia` scheme with the ISTAT province codes 021 (Bolzano) and 022 (Trento),
under the region's area, and their outlines come from ISTAT's administrative boundaries of 2023 as geoBoundaries
republishes them (CC BY 3.0).
"""

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import shapely
import shapely.geometry

from ea import raw
from ea.package import NAMESPACE, Package

ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
# Rows production holds: the region's polity (written by it.comunali) and its area (written by it.interno).
REGION = "b5c6e0aa-6b80-501c-844f-ac295335e800"
REGION_AREA = "01a13bf7-2f89-7329-a8e6-bbf888038fc6"
REGION_PATH = "it/trentino-alto-adige"
SCHEME = "it.provincia"
GEOBOUNDARIES = "it.geoboundaries"
ADM3 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ITA/ADM3/"
    "geoBoundaries-ITA-ADM3_simplified.geojson"
)
# The municipalities of the two provinces that production holds (written by it.interno, and by it.comunali for those
# merged away since 2009), with their reporting units, from the reference partition of Italy.
COMUNI = Path(__file__).with_name("taa_comuni.tsv")
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)


@dataclass(frozen=True)
class Province:
    iso: str
    code: str
    outline: str
    native: str
    german: str | None
    english: str
    path: str
    council_native: str
    council_german: str | None
    council_english: str

    @property
    def polity(self) -> str:
        return str(uuid5(NAMESPACE, f"polity\x1f{self.iso}"))


TRENTO = Province(
    iso="IT-TN",
    code="022",
    outline="Trento",
    native="Provincia autonoma di Trento",
    german=None,
    english="Autonomous Province of Trento",
    path=f"{REGION_PATH}/provincia-autonoma-di-trento",
    council_native="Consiglio della Provincia autonoma di Trento",
    council_german=None,
    council_english="Provincial Council of Trento",
)
BOLZANO = Province(
    iso="IT-BZ",
    code="021",
    outline="Bolzano",
    native="Provincia autonoma di Bolzano - Alto Adige",
    german="Autonome Provinz Bozen - Südtirol",
    english="Autonomous Province of Bolzano - South Tyrol",
    path=f"{REGION_PATH}/provincia-autonoma-di-bolzano",
    council_native="Consiglio della Provincia autonoma di Bolzano",
    council_german="Südtiroler Landtag",
    council_english="Landtag of South Tyrol",
)


def fetch() -> None:
    raw.fetch(GEOBOUNDARIES, ADM3, suffix=".geojson")


def outline(name: str) -> shapely.MultiPolygon:
    features: list[dict[str, Any]] = json.loads(raw.cached(GEOBOUNDARIES, ADM3).path.read_text(encoding="utf-8"))[
        "features"
    ]
    found = [f["geometry"] for f in features if f["properties"]["shapeName"] == name]
    if len(found) != 1:
        raise ValueError(f"expected one outline named {name}, found {len(found)}")
    geometry = shapely.make_valid(shapely.geometry.shape(found[0]))
    parts = [g for part in shapely.get_parts(geometry) for g in shapely.get_parts(part) if g.geom_type == "Polygon"]
    return shapely.set_srid(shapely.MultiPolygon(parts), 4326)


def geo(p: Package, province: Province) -> str:
    """Add the province's area, under the region's, with its outline; return the area's id."""
    source = p.id("source", "geoboundaries")
    p.add(
        "source",
        id=source,
        kind="academic",
        publisher="geoBoundaries (ISTAT, Istituto nazionale di statistica)",
        title="geoBoundaries gbOpen Italy ADM3 (provinces), 2023",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 (ISTAT)",
        license_url="https://creativecommons.org/licenses/by/3.0/",
        attribution="geoBoundaries (William & Mary geoLab), from ISTAT's administrative boundaries",
    )
    p.artifact(raw.cached(GEOBOUNDARIES, ADM3), source)
    area = str(uuid5(NAMESPACE, f"area\x1f{SCHEME}\x1f{province.code}"))
    p.external |= {REGION_AREA, SCHEME}
    p.add("area", id=area, scheme_code=SCHEME)
    p.add("area_code", area_id=area, code=province.code)
    p.add("area_parent", area_id=area, parent_id=REGION_AREA)
    p.name(area, province.native, "it", "official")
    p.name(area, province.english, "en", "english", False)
    if province.german is not None:
        p.name(area, province.german, "de", "native", False)
    shape = outline(province.outline)
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=date(1948, 1, 1),
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=source,
    )
    return area


def area_of(province: Province) -> str:
    return str(uuid5(NAMESPACE, f"area\x1f{SCHEME}\x1f{province.code}"))


def polity(p: Package, province: Province, council: str, seats: int) -> None:
    """Add the province's polity under the region's, and its council of `seats` members."""
    p.external |= {ITALY, REGION}
    p.add("polity", id=province.polity, kind="county", iso_code=province.iso)
    p.add("polity_parent", polity_id=province.polity, parent_id=REGION)
    p.add("polity_territory", polity_id=province.polity, area_id=area_of(province))
    p.add("slug", path=province.path, entity_id=province.polity, is_current=True)
    p.name(province.polity, province.native, "it", "official")
    p.name(province.polity, province.english, "en", "english", False)
    if province.german is not None:
        p.name(province.polity, province.german, "de", "native", False)
    p.add("body", id=council, polity_id=province.polity, kind="unicameral")
    p.add("body_size", body_id=council, seats=seats, valid_from=date(1948, 1, 1))
    p.name(council, province.council_english, "en", "official")
    p.name(council, province.council_native, "it", "native", False)
    if province.council_german is not None:
        p.name(council, province.council_german, "de", "native", False)


def units() -> dict[str, tuple[str, str, date | None]]:
    """Each municipality's ISTAT code, with its name, its reporting unit, and the day it ended, if it has."""
    with COMUNI.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file, delimiter="\t"))
    return {
        row["code"]: (row["name"], row["unit_id"], date.fromisoformat(row["valid_to"]) if row["valid_to"] else None)
        for row in rows
    }


def unit(code: str, day: date, known: dict[str, tuple[str, str, date | None]]) -> str:
    if code not in known:
        raise KeyError(f"municipality {code} is not one production holds")
    _, unit_id, ended = known[code]
    if ended is not None and ended <= day:
        raise ValueError(f"municipality {code} ended on {ended}, before {day}")
    return unit_id
