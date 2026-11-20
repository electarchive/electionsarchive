"""Boundaries for the local authorities of Palestine's 2026 local elections.

The Central Elections Commission names each of the 380 local authorities (a council of a municipality or a village)
but publishes no shapes. OCHA's village boundaries of the West Bank (Palestinian Ministry of Planning, 2019) and
the municipal boundaries of the Gaza Strip (Gaza Housing Council, 2019) carry transliterated names only, so the
authorities were paired with their polygons once, by hand-reviewed matching of the Commission's Arabic names to
Arabic place names inside each polygon and to the polygon's transliterated name, within the governorate. The pairs
live in `local_boundaries_match.tsv`: one row per authority that has a shape (a joint council gets the union of
its villages' polygons). Authorities without a row have no polygon in either dataset and are left unplaced; the
OCHA administrative layer (COD-AB) stops at the governorates.

The package adds one area per matched authority under `ps.local_authority`, parented to its governorate, with the
polygon dated to the dataset, and, for the authorities that were polled, a membership that places the authority's
reporting unit on the area. Councils elected by acclamation have areas too; their units join once the archive holds them.
"""

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5

import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ps import cec_local

SOURCE = "ps.local_boundaries"
MATCHES = Path(__file__).with_name("local_boundaries_match.tsv")
WEST_BANK = (
    "https://data.humdata.org/dataset/8e88b6e5-c8e8-45b3-afa1-268530aadae1/resource/"
    "70c36308-91ed-4e21-9a25-e0aefb4e756f/download/villageboundary.zip"
)
GAZA = (
    "https://data.humdata.org/dataset/6b79f7ab-9e13-48e5-9f58-dc45b4a9222c/resource/"
    "f131956a-8cc6-424c-a831-5b8b0d6956b9/download/gazastrip_municipalboundaries.zip"
)
# prefix in the match table -> (url, shapefile stem, name field, dataset date, source key)
DATASETS = {
    "wb": (WEST_BANK, "VillageBoundary", "VNAME", date(2019, 7, 24)),
    "gz": (GAZA, "GazaStrip_MunicipalBoundaries", "NAME", date(2019, 7, 18)),
}
PUBLISHERS = {
    "wb": (
        "State of Palestine, Ministry of Planning, via OCHA oPt",
        "State of Palestine - Village boundary in the West Bank",
    ),
    "gz": ("Gaza Housing Council, via OCHA oPt", "State of Palestine - Gaza Strip Municipal Boundaries"),
}
PAGES = {
    "wb": "https://data.humdata.org/dataset/state-of-palestine-village-boundary-in-the-west-bank",
    "gz": "https://data.humdata.org/dataset/gaza-stripmunicipal-boundaries",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


@dataclass(frozen=True)
class Match:
    unit: str
    name: str
    polled: bool
    polygons: list[tuple[str, int]]
    names: list[str]

    @property
    def district(self) -> str:
        return self.unit.split(":", 1)[0]


def matches() -> list[Match]:
    out: list[Match] = []
    with MATCHES.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            polygons = [(ref.split(":")[0], int(ref.split(":")[1])) for ref in row["polygons"].split(";")]
            out.append(Match(row["unit"], row["name"], row["polled"] == "1", polygons, row["names"].split(";")))
    return out


def fetch() -> None:
    for url, _, _, _ in DATASETS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


@cache
def polygons(prefix: str) -> list[tuple[str, shapely.Geometry]]:
    url, stem, field, _ = DATASETS[prefix]
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{stem}.shp")),
        shx=io.BytesIO(archive.read(f"{stem}.shx")),
        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
        encoding="utf-8",
    )
    names = [f[0] for f in reader.fields[1:]]
    out: list[tuple[str, shapely.Geometry]] = []
    for record, outline in zip(reader.records(), reader.shapes(), strict=True):
        if outline is None or record is None:
            raise ValueError(f"{stem} has a record without a shape")
        row = dict(zip(names, record, strict=True))
        out.append((str(row[field]), shapely.make_valid(shapely.geometry.shape(outline.__geo_interface__))))
    return out


def multipolygon(shape: shapely.Geometry) -> shapely.MultiPolygon:
    parts = [g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3]
    if not parts:
        parts = [p for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 6 for p in shapely.get_parts(g)]
    return shapely.MultiPolygon(parts)


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries of the local authorities of the 2026 local elections, from the OCHA village boundaries of the "
        "West Bank and municipal boundaries of the Gaza Strip",
        depends=[f"{cec_local.SOURCE}/geo"],
    )
    found = matches()
    p.external |= {cec_local.PALESTINE, cec_local.COUNTRY}
    p.external |= {cec_local.governorate(m.district) for m in found}
    p.external |= {cec_local.key("unit", m.unit) for m in found if m.polled}
    sources: dict[str, str] = {}
    for prefix, (url, _, _, day) in DATASETS.items():
        publisher, title = PUBLISHERS[prefix]
        sources[prefix] = key("source", prefix)
        p.add(
            "source",
            id=sources[prefix],
            kind="official_body",
            publisher=publisher,
            title=title,
            url=PAGES[prefix],
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution=f"{publisher}, Humanitarian Data Exchange, {day.year}",
        )
        p.artifact(raw.cached(SOURCE, url), sources[prefix])
        p.add(
            "boundary_set",
            id=key("boundary_set", "ps.local_authority", prefix),
            scheme_code="ps.local_authority",
            vintage=f"{day.year}-{prefix}",
            valid_from=day,
            as_of=day,
        )
    for m in found:
        area = key("area", m.unit)
        prefix = m.polygons[0][0]
        day = DATASETS[prefix][3]
        shape = shapely.union_all([polygons(pre)[i][1] for pre, i in m.polygons])
        shape = shapely.set_srid(multipolygon(shape), 4326)
        p.add("area", id=area, scheme_code="ps.local_authority")
        p.add("area_code", area_id=area, code=m.unit)
        p.add("area_parent", area_id=area, parent_id=cec_local.governorate(m.district))
        p.name(area, m.name, "ar", "official")
        for english in m.names:
            p.name(area, english.title() if english.isupper() else english, "en", "transliteration", False)
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=day,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=sources[prefix],
        )
        if not m.polled:
            continue
        p.add(
            "reporting_unit_area",
            unit_id=cec_local.key("unit", m.unit),
            area_id=area,
            method="official_list",
            approximate=False,
            partial=False,
        )
    return p
