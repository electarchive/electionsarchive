"""The shapes of Western Australia's Legislative Assembly districts and Legislative Council regions, for `au.waec`.

The Australian Bureau of Statistics publishes State Electoral Divisions (SEDs) as digital boundary files under
CC BY 4.0, built from its census units (collection districts in 2006, mesh blocks from 2011) to approximate each
state's districts as they stood on the reference date. Five editions hold five of the Western Australian boundary
determinations:

- SED 2006 (census of 8 August 2006) holds the 2003 determination, first used at the 2005 election;
- SED 2011 (ASGS July 2011) the 2007 determination, first used in 2008;
- SED 2016 (ASGS July 2016) the 2015 determination, first used in 2017;
- SED 2021 (ASGS Edition 3) the 2019 determination, first used in 2021;
- SED 2024 (ASGS Edition 3) the 2023 determination, first used in 2025.

Each edition's districts match the districts the Western Australian Electoral Commission's reports name for that
determination, one to one, after one spelling (SED 2006 prints Serpentine-Jarrahdale as Jarrahdale - Serpentine).

No SED edition holds the 2011 determination, which the 2013 election used. The Electoral Distribution Commissioners'
enrolment workbook of 9 March 2015, which counts the electors of each 2011 statistical area level 1 (SA1) by the
districts then in force, assigns 5,384 of the 5,508 Western Australian SA1s to districts. Each 2011 district here is
the union of the ABS 2011 SA1s the workbook places in it: an SA1 the workbook splits between districts (55 of them,
with 1.3% of the electors) goes to the district holding most of its electors, an SA1 with no electors goes to the
neighboring district it shares the longest edge with, and an island touching no other SA1 to the district of the
nearest one. Those shapes are an approximation, marked synthetic. Built the same way from the workbook of the 2015
determination, they overlap the SED 2016 districts with a median intersection over union of 0.999 and place 95% of
the state's land in the right district, but nine of 59 districts fall below 0.9 where a large outer SA1 is split.
The 1994 determination (the 1996 and 2001 elections) has no published open boundary file; its districts stay
without shapes.

A Legislative Council region of the 2003 to 2019 determinations is the union of its districts, as each election
report places them; the SED names, which carry each district's region, agree. All shapes of a determination are
simplified together as one coverage to about 11 meters, so neighbors still share their edges.
"""

import functools
import io
import re
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy
import polars as pl
import shapefile
import shapely
from shapely.geometry import shape

from ea import changeset, raw
from ea.package import Package

SOURCE = "au.waec"
ABS = "https://www.abs.gov.au"
ED3 = (
    f"{ABS}/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/"
    "access-and-downloads/digital-boundary-files"
)
TOLERANCE = 0.0001  # degrees, about 11 meters


@dataclass(frozen=True)
class Edition:
    plan: str  # the boundary determination
    title: str
    page: str
    url: str


EDITIONS = [
    Edition(
        "2003",
        "State Electoral Divisions (SED) 2006 digital boundaries, Census Geographic Areas (2923.0.30.001)",
        f"{ABS}/AUSSTATS/abs@.nsf/DetailsPage/2923.0.30.0012006?OpenDocument",
        f"{ABS}/AUSSTATS/subscriber.nsf/log?openagent&2923030001sed06aaust.zip&2923.0.30.001&Data%20Cubes&"
        "243230139A63E6C4CA25731A0021DC58&0&2006&17.07.2007&Latest",
    ),
    Edition(
        "2007",
        "State Electoral Divisions (SED) 2011 digital boundaries, ASGS Volume 3 (1270.0.55.003)",
        f"{ABS}/AUSSTATS/abs@.nsf/DetailsPage/1270.0.55.003July%202011?OpenDocument",
        f"{ABS}/AUSSTATS/subscriber.nsf/log?openagent&1270055003_sed_2011_aust_shape.zip&1270.0.55.003&"
        "Data%20Cubes&1F692001AC7E460DCA2578D40013567C&0&July%202011&22.07.2011&Previous",
    ),
    Edition(
        "2015",
        "State Electoral Divisions (SED) 2016 digital boundaries, ASGS Volume 3 (1270.0.55.003)",
        f"{ABS}/AUSSTATS/abs@.nsf/DetailsPage/1270.0.55.003July%202016?OpenDocument",
        f"{ABS}/ausstats/subscriber.nsf/log?openagent&1270055003_sed_2016_aust_shp.zip&1270.0.55.003&"
        "Data%20Cubes&0158A8AE4969AC0BCA25828300129E54&0&July%202016&07.05.2018&Previous",
    ),
    Edition(
        "2019",
        "State Electoral Divisions (SED) 2021 digital boundaries, ASGS Edition 3",
        ED3,
        f"{ED3}/SED_2021_AUST_GDA2020_SHP.zip",
    ),
    Edition(
        "2023",
        "State Electoral Divisions (SED) 2024 digital boundaries, ASGS Edition 3",
        ED3,
        f"{ED3}/SED_2024_AUST_GDA2020.zip",
    ),
]
SA1 = Edition(
    "2011",
    "Statistical Area Level 1 (SA1) 2011 digital boundaries, ASGS Volume 1 (1270.0.55.001)",
    f"{ABS}/AUSSTATS/abs@.nsf/DetailsPage/1270.0.55.001July%202011?OpenDocument",
    f"{ABS}/ausstats/subscriber.nsf/log?openagent&1270055001_sa1_2011_aust_shape.zip&1270.0.55.001&Data%20Cubes&"
    "24A18E7B88E716BDCA257801000D0AF1&0&July%202011&23.12.2010&Latest",
)
BOUNDARIES = "https://www.boundaries.wa.gov.au"
WORKBOOK = (
    f"{BOUNDARIES}/sites/default/files/documents/2015/enrolment-statistics/"
    "Enrolment_Statistics_by_District_LG_Locality_SA1_9Mar2015.xlsx"
)
# SED names that differ from the commission's reports, as slugs: report slug
SPELLINGS = {"jarrahdale-serpentine": "serpentine-jarrahdale"}
SA1_COUNT = 5508  # Western Australia's 2011 SA1s with a shape
WORKBOOK_SA1S = 5384


def files() -> list[str]:
    return [*(e.url for e in EDITIONS), SA1.url, WORKBOOK]


def fetch() -> None:
    for e in EDITIONS:
        raw.fetch(SOURCE, e.url, suffix=".zip")
    raw.fetch(SOURCE, SA1.url, suffix=".zip")
    raw.fetch(SOURCE, WORKBOOK, suffix=".xlsx")


def _multi(geometry: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(geometry)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _reader(url: str) -> shapefile.Reader:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    shps = [n for n in archive.namelist() if n.lower().endswith(".shp") and "_none" not in n.lower()]
    if len(shps) != 1:
        raise ValueError(f"{url}: expected one polygon layer, found {shps}")
    base = shps[0][:-4]
    return shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{base}.shp")),
        shx=io.BytesIO(archive.read(f"{base}.shx")),
        dbf=io.BytesIO(archive.read(f"{base}.dbf")),
        encoding="latin-1",
    )


def _wa_rows(url: str) -> list[tuple[str, shapely.Geometry]]:
    """Each Western Australian SED of an edition as (name, shape), without the non-spatial rows."""
    reader = _reader(url)
    fields = [f[0] for f in reader.fields[1:]]
    code = next(f for f in fields if f.upper().startswith("SED") and "NAME" not in f.upper())
    name = next(f for f in fields if "NAME" in f.upper() and not f.upper().startswith(("STE", "AUS")))
    out: list[tuple[str, shapely.Geometry]] = []
    for record, outline in zip(reader.records(), reader.shapes(), strict=True):
        if record is None:
            raise ValueError(f"{url}: a shape without a record")
        row = record.as_dict()
        if not str(row[code]).startswith("5") or str(row[code])[1:3] in ("94", "97"):
            continue  # another state, or no usual address and migratory, offshore, and shipping
        if outline is None or not outline.points:
            raise ValueError(f"{url}: {row[name]} has no shape")
        out.append((str(row[name]), shapely.make_valid(shape(outline.__geo_interface__))))
    return out


def _split(printed: str) -> tuple[str, str | None]:
    """A SED name's district slug and the region it names in brackets, if any."""
    found = re.fullmatch(r"(.+?)\s*\((.+)\)", printed)
    district, region = (found[1], found[2]) if found else (printed, None)
    slug = changeset.slug(re.sub(r"\s*-\s*", "-", district))
    return SPELLINGS.get(slug, slug), region


def region_slug(name: str) -> str:
    return changeset.slug(name.replace("&", "and"))


def _simplified(shapes: dict[str, shapely.Geometry]) -> dict[str, shapely.Geometry]:
    keys = sorted(shapes)
    simple = shapely.coverage_simplify(numpy.array([shapes[k] for k in keys]), TOLERANCE)
    return {k: _multi(g) for k, g in zip(keys, simple, strict=True)}


@dataclass(frozen=True)
class Plan:
    plan: str
    districts: dict[str, shapely.Geometry]  # by slug
    regions: dict[str, str]  # each district's region slug as the SED names it, where it does
    synthetic: bool
    url: str


@functools.cache
def sed(plan: str) -> Plan:
    edition = next(e for e in EDITIONS if e.plan == plan)
    found: dict[str, shapely.Geometry] = {}
    regions: dict[str, str] = {}
    for printed, geometry in _wa_rows(edition.url):
        slug, region = _split(printed)
        if slug in found:
            raise ValueError(f"SED for {plan}: two districts named {slug}")
        found[slug] = geometry
        if region is not None:
            regions[slug] = region_slug(region)
    return Plan(plan, _simplified(found), regions, False, edition.url)


@functools.cache
def sa1_shapes() -> dict[str, shapely.Geometry]:
    """Western Australia's 2011 SA1s by their seven-digit code."""
    reader = _reader(SA1.url)
    out: dict[str, shapely.Geometry] = {}
    for record, outline in zip(reader.records(), reader.shapes(), strict=True):
        if record is None:
            raise ValueError("the SA1 file holds a shape without a record")
        code = str(record.as_dict()["SA1_7DIG11"])
        if not code.startswith("5") or outline is None or not outline.points:
            continue
        out[code] = shapely.make_valid(shape(outline.__geo_interface__))
    if len(out) != SA1_COUNT:
        raise ValueError(f"the SA1 file holds {len(out)} Western Australian SA1s with shapes, not {SA1_COUNT}")
    return out


def sa1_assignment(workbook: str) -> tuple[dict[str, str], dict[str, str]]:
    """Each SA1's district (the one holding most of its electors) and each district's region, from the workbook."""
    frame = pl.read_excel(raw.cached(SOURCE, workbook).path, sheet_name="Source Data")
    frame = frame.select(
        pl.col("Region").cast(pl.Utf8),
        pl.col("District").cast(pl.Utf8),
        pl.col("SA1").cast(pl.Utf8),
        pl.col("Electors").cast(pl.Int64),
    ).filter(pl.col("SA1").is_not_null())
    regions: dict[str, str] = {}
    for region, district in frame.select("Region", "District").unique().iter_rows():
        slug = changeset.slug(district)
        if regions.setdefault(slug, region_slug(region)) != region_slug(region):
            raise ValueError(f"the workbook places {district} in two regions")
    totals = (
        frame.group_by("SA1", "District")
        .agg(pl.col("Electors").sum())
        .sort(["SA1", "Electors", "District"], descending=[False, True, False])
    )
    out: dict[str, str] = {}
    for sa1, district, _ in totals.iter_rows():
        out.setdefault(str(sa1), changeset.slug(district))
    return out, regions


def dissolved(assignment: dict[str, str]) -> dict[str, shapely.Geometry]:
    """The districts as unions of their SA1s, with each SA1 the assignment leaves out given to the neighboring
    district it shares the longest edge with."""
    shapes = sa1_shapes()
    if not set(assignment) <= set(shapes):
        raise ValueError(f"the workbook names SA1s the SA1 file lacks: {sorted(set(assignment) - set(shapes))[:5]}")
    owner = dict(assignment)
    codes = sorted(shapes)
    tree = shapely.STRtree([shapes[c] for c in codes])
    left = [c for c in codes if c not in owner]
    while left:
        progress: dict[str, str] = {}
        for code in left:
            edges: dict[str, float] = defaultdict(float)
            for index in tree.query(shapes[code], predicate="intersects"):
                other = codes[int(index)]
                if other in owner:
                    shared = shapely.intersection(shapely.boundary(shapes[code]), shapely.boundary(shapes[other]))
                    edges[owner[other]] += shapely.length(shared)
            if edges:
                progress[code] = max(sorted(edges), key=lambda d: edges[d])
        if not progress:  # islands that touch no assigned SA1 go to the district of the nearest one
            assigned = [c for c in codes if c in owner]
            near = shapely.STRtree([shapes[c] for c in assigned])
            for code in left:
                nearest = near.nearest(shapes[code])
                if nearest is None:
                    raise ValueError(f"SA1 {code} has no nearest SA1")
                progress[code] = owner[assigned[int(nearest)]]
        owner.update(progress)
        left = [c for c in left if c not in owner]
    groups: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for code, district in owner.items():
        groups[district].append(shapes[code])
    return {d: shapely.union_all(g, grid_size=1e-7) for d, g in groups.items()}


@functools.cache
def plan_2011() -> Plan:
    assignment, regions = sa1_assignment(WORKBOOK)
    if len(assignment) != WORKBOOK_SA1S:
        raise ValueError(f"the workbook assigns {len(assignment)} SA1s, not {WORKBOOK_SA1S}")
    return Plan("2011", _simplified(dissolved(assignment)), regions, True, SA1.url)


def plans() -> list[Plan]:
    return [*(sed(e.plan) for e in EDITIONS if e.plan < "2011"), plan_2011(), *(sed(e.plan) for e in EDITIONS[2:])]


def _outline(
    p: Package, id: str, geometry: shapely.Geometry, source: str, start: date, end: date | None, quality: str
) -> None:
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=start,
        valid_to=end,
        geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
        ),
        quality=quality,
        source_id=source,
    )


def add(
    p: Package,
    key: Callable[..., str],
    districts: dict[str, dict[str, str]],
    regions: dict[str, dict[str, str]],
    spans: dict[str, tuple[date, date | None]],
) -> None:
    """Add the sources and shapes to `p`. `districts` maps each plan to its district slugs' area ids, `regions` each
    plan to its districts' region slugs, and `spans` each plan to the days its shapes are valid between."""
    abs_id = key("source", "abs-asgs")
    edc = key("source", "edc")
    p.add(
        "source",
        id=abs_id,
        kind="official_body",
        publisher="Australian Bureau of Statistics",
        title="State Electoral Division and Statistical Area Level 1 digital boundary files",
        url=f"{ABS}/statistics/standards/australian-statistical-geography-standard-asgs-edition-3",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Australian Bureau of Statistics",
        notes=(
            "State Electoral Divisions approximate each state's electoral districts on the edition's reference "
            "date from the Bureau's census units. Shapes here are simplified to about 11 meters as one coverage per "
            "boundary determination."
        ),
    )
    p.add(
        "source",
        id=edc,
        kind="official_body",
        publisher="Western Australian Electoral Distribution Commissioners",
        title="Enrolment statistics by district, local government, locality, and SA1, 9 March 2015",
        url=f"{BOUNDARIES}/boundaries/2015",
        license=(
            "All rights reserved (State of Western Australia): the assignment of SA1s to districts is used as fact "
            "with attribution; the workbook is not redistributed"
        ),
        license_url=f"{BOUNDARIES}/copyright",
        attribution="Electoral Boundaries WA",
    )
    for e in EDITIONS:
        p.artifact(raw.cached(SOURCE, e.url), abs_id)
    p.artifact(raw.cached(SOURCE, SA1.url), abs_id)
    p.artifact(raw.cached(SOURCE, WORKBOOK), edc)
    for plan in plans():
        ids = districts[plan.plan]
        if set(plan.districts) != set(ids):
            raise ValueError(
                f"{plan.plan}: shapes {sorted(set(plan.districts) - set(ids))} and districts "
                f"{sorted(set(ids) - set(plan.districts))} do not match"
            )
        placed = regions.get(plan.plan, {})
        for slug, region in plan.regions.items():
            if placed and placed[slug] != region:
                raise ValueError(f"{plan.plan} {slug}: the boundary file says {region}, the report {placed[slug]}")
        start, end = spans[plan.plan]
        quality = "synthetic" if plan.synthetic else "official"
        for slug, geometry in sorted(plan.districts.items()):
            _outline(p, ids[slug], geometry, abs_id, start, end, quality)
        if not placed:
            continue
        members: dict[str, list[shapely.Geometry]] = defaultdict(list)
        for slug, geometry in plan.districts.items():
            members[placed[slug]].append(geometry)
        p.add(
            "boundary_set",
            id=key("boundary_set", "au-wa.region", plan.plan),
            scheme_code="au-wa.region",
            vintage=plan.plan,
            as_of=start,
        )
        for region, parts in sorted(members.items()):
            geometry = _multi(shapely.union_all(parts, grid_size=1e-7))
            _outline(p, key("area", "au-wa.region", f"{plan.plan}:{region}"), geometry, abs_id, start, end, quality)
