"""Zambia's constituencies of the 2016 plan and wards of the 2021 plan, with their shapes, for `zm.ecz`.

The shapes come from OCHA's COD-AB dataset for Zambia, which the Disaster Management and Mitigation Unit compiled from
the Electoral Commission's delimitation: its third level holds the 156 constituencies and its fourth the 1,853 wards.
Each constituency matches the Commission's notices by name; three Solwezi seats carry their district's name in the
dataset. Each ward matches a notice's ward by name within its district, after the curated spellings below.
"""

import functools
import io
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
from shapely.geometry import shape
from shapely.ops import transform

from ea import package, raw
from ea.package import Package

SOURCE = "zm.ecz"
COD = (
    "https://data.humdata.org/dataset/01c71cfd-32ff-4a32-a1dd-5909702c12db/resource/"
    "7e9c310d-b9d1-4f82-8ad5-09c1ce744482/download/zmb_admin_boundaries.shp.zip"
)
# Two public-domain layers of the 150 constituencies that the notices of 1996 to 2015 report: the Central Statistical
# Office's wards of 1991 to 2006 as digitized by the Provincial Centre for Geographic Information Services, and the
# ward and constituency maps of 2006 to 2010 that the Electoral Commission provided to Nicholas Eubank.
STACKS = "https://stacks.stanford.edu/file/druid"
OLD = f"{STACKS}:rn812zx7730/WardAndConstituencyBoundaries_1991to2006."
NEW = f"{STACKS}:yc436vm9005/Zambia_ConstituenciesAndWards_2006to2010_UTM35S."
PARTS = ("shp", "shx", "dbf", "prj")
PLAN_1991 = date(1991, 1, 1)
PLAN_2006 = date(2006, 1, 1)
PLAN_150 = 150
# the 2008 notice's name (letters only) where a layer spells or splits it differently
OLD_NAMES = {
    "KABWECENTRAL": "KABWE",
    "MWEMBEZHI": "MWEMBESHI",
    "BWANANKUBWA": "BWANAMUKUBWA",
    "CHIPATACENTRAL": "CHIPATA",
    "PAMBASHE": "MPAMBASHE",
    "LUSAKACENTRAL": "LUSAKACANTRAL",
    "KASAMACENTRAL": "KASAMA",
    "LUBANSENSHI": "LUBANSESHI",
    "SENGAHILL": "SENGA",
    "MPIKA": "MPIKACENTRAL",
    "CHOMACENTRAL": "CHOMA",
    "DUNDUMWEZI": "DUNDUMWENZE",
    "KALOMOCENTRAL": "KALOMO",
    "MAZABUKACENTRAL": "MAZABUKA",
    "MONZECENTRAL": "MONZE",
    "NAMWALA": "NAMWALACENTRAL",
    "KALABOCENTRAL": "KALABO",
    "KAOMACENTRAL": "KAOMA",
    "MONGUCENTRAL": "MONGU",
}
NEW_NAMES = {
    "BWANANKUBWA": "BWANAMKUBWA",
    "NDOLACENTRAL": "NDOLA",
    "CHIPATACENTRAL": "CHIPATA",
    "MALAMBO": "MAMBWE",
    # the layer labels these two the other way round: each overlaps the other's 1991 shape by 98 percent or more
    "MANSACENTRAL": "CHEMBE",
    "CHEMBE": "MANSA",
    "CHIENGE": "CHIENGI",
    "BANGWEULU": "BANGEULU",
    "SHIWANGANDU": "SHIWANGADU",
    "ISOKAEAST": "MAFINGA",
    "ISOKAWEST": "ISOKA",
    "MWINILUNGAEAST": "MWINILUNGA",
    "MWINILUNGAWEST": "IKELENGE",
    "KASAMACENTRAL": "KASAMA",
    "LUBANSENSHI": "LUBASENSHI",
    "CHOMACENTRAL": "CHOMA",
    "CHIKANKATA": "CHIKANKANTA",
    "MAZABUKACENTRAL": "MAZABUKA",
    "MONZECENTRAL": "MONZE",
    "KALABOCENTRAL": "KALABO",
    "MONGUCENTRAL": "MONGU",
}
ZAMBIA = str(uuid5(package.NAMESPACE, "polity\x1fZM"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fZMB"))
# The 156 constituencies were first contested on 11 August 2016 and last on 12 August 2021; the 226 of the 2026
# delimitation replaced them on 13 August 2026. The wards in the dataset are those of the 2021 local elections.
PLAN_2016 = date(2016, 8, 11)
WARDS_2021 = date(2021, 8, 12)
PLAN_2026 = date(2026, 8, 13)
CONSTITUENCIES = 156
WARDS = 1853
TOLERANCE = 0.0001  # degrees, about 11 meters
# the dataset names three Solwezi seats after the districts they became
ALIASES = {"SOLWEZICENTRAL": "SOLWEZI", "SOLWEZIEAST": "MUSHINDAMO", "SOLWEZIWEST": "KALUMBILA"}
# a ward the notices spell differently from the dataset, as (district p-code, notice spelling): dataset spelling
SPELLINGS = {
    ("ZM102006", "KABUNDYA"): "KABUNDIA",
    ("ZM103003", "KAZAKALOBA"): "KAZAKALUBA",
    ("ZM107007", "MUSENGEZI"): "MUSENGENZI",
    ("ZM107009", "FUNGWA"): "FUNGWE",
    ("ZM108009", "LWITADILWATEMBO"): "LWITADILWATEMBU",
    ("ZM110013", "KATIMAMULILO"): "KATIMA",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def letters(name: str) -> str:
    return re.sub(r"[^A-Z]", "", name.upper().replace("Ó", "O"))


def constituency_area(number: int) -> str:
    return key("area", "zm.constituency", 2016, f"{number:03d}")


def ward_area(pcode: str) -> str:
    return key("area", "zm.ward", 2021, pcode)


@dataclass(frozen=True)
class Place:
    pcode: str
    name: str
    parent: str  # the constituency's p-code for a ward, the district's for a constituency
    district: str  # the district's p-code
    district_name: str
    shape: shapely.Geometry


def _reader(name: str) -> shapefile.Reader:
    archive = zipfile.ZipFile(raw.cached(SOURCE, COD).path)
    return shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{name}.shp")),
        shx=io.BytesIO(archive.read(f"{name}.shx")),
        dbf=io.BytesIO(archive.read(f"{name}.dbf")),
        encoding="utf-8",
    )


def constituency_area_150(number: int) -> str:
    return key("area", "zm.constituency", 1991, f"{number:03d}")


def _layer(base: str, field: str) -> dict[str, shapely.Geometry]:
    """A ward layer dissolved into constituencies by their letters, in degrees of WGS 84 and simplified as a
    coverage."""
    parts = {suffix: raw.cached(SOURCE, base + suffix).path for suffix in PARTS}
    reader = shapefile.Reader(
        shp=io.BytesIO(parts["shp"].read_bytes()),
        shx=io.BytesIO(parts["shx"].read_bytes()),
        dbf=io.BytesIO(parts["dbf"].read_bytes()),
        encoding="latin-1",
    )
    move = pyproj.Transformer.from_crs(pyproj.CRS.from_wkt(parts["prj"].read_text()), 4326, always_xy=True).transform
    grouped: dict[str, list[shapely.Geometry]] = {}
    for record, outline in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or outline is None:
            raise ValueError(f"{base} holds a record without a shape")
        grouped.setdefault(letters(str(record[field])), []).append(
            transform(move, shapely.make_valid(shape(outline.__geo_interface__)))
        )
    names = sorted(grouped)
    merged = [shapely.make_valid(shapely.union_all(grouped[name]).buffer(0)) for name in names]
    simple = shapely.coverage_simplify(numpy.array(merged), TOLERANCE)
    return {name: _multi(outline) for name, outline in zip(names, simple, strict=True)}


@functools.cache
def old_plan() -> dict[str, shapely.Geometry]:
    return _layer(OLD, "NAME2_")


@functools.cache
def new_plan() -> dict[str, shapely.Geometry]:
    return _layer(NEW, "constName")


def seat_150(name: str) -> str:
    """A 150-seat constituency's letters as the 2008 notice prints them, which the unit's name must match."""
    return letters(name)


def _multi(geometry: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(geometry)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _level(name: str, level: int) -> list[Place]:
    reader = _reader(name)
    rows: list[Mapping[str, object]] = []
    shapes: list[shapely.Geometry] = []
    for record, outline in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or outline is None:
            raise ValueError(f"{name} holds a record without a shape")
        rows.append(record.as_dict())
        shapes.append(shapely.make_valid(shape(outline.__geo_interface__)))
    simple = shapely.coverage_simplify(numpy.array(shapes), TOLERANCE)
    return [
        Place(
            str(row[f"adm{level}_pcode"]),
            str(row[f"adm{level}_name"]),
            str(row[f"adm{level - 1}_pcode"]),
            str(row["adm2_pcode"]),
            str(row["adm2_name"]),
            _multi(outline),
        )
        for row, outline in zip(rows, simple, strict=True)
    ]


@functools.cache
def constituencies() -> dict[str, Place]:
    """The 2016 plan's constituencies by their letters, as the notices spell them."""
    found = {letters(place.name): place for place in _level("zmb_admin3", 3)}
    if len(found) != CONSTITUENCIES:
        raise ValueError(f"the dataset holds {len(found)} distinct constituencies, not {CONSTITUENCIES}")
    return found


@functools.cache
def wards() -> list[Place]:
    found = _level("zmb_admin4", 4)
    if len(found) != WARDS:
        raise ValueError(f"the dataset holds {len(found)} wards, not {WARDS}")
    return found


@functools.cache
def _ward_index() -> dict[tuple[str, str], Place]:
    index: dict[tuple[str, str], Place] = {}
    for place in wards():
        slot = (place.district, letters(place.name))
        if slot in index:
            raise ValueError(f"two wards named {place.name} in district {place.district}")
        index[slot] = place
    return index


@functools.cache
def _district_codes() -> dict[str, str]:
    return {letters(place.district_name): place.district for place in wards()}


def constituency(name: str) -> Place | None:
    """The 2016 plan's constituency a notice names."""
    spelled = letters(name)
    return constituencies().get(ALIASES.get(spelled, spelled))


def assign(listed: dict[int, str]) -> dict[int, Place]:
    """Match the notice's numbered constituencies to the dataset's, one to one, and fail on any that do not match."""
    out: dict[int, Place] = {}
    for number, name in listed.items():
        place = constituency(name)
        if place is None:
            raise ValueError(f"constituency {number} {name!r} is not in the boundary dataset")
        out[number] = place
    if len({place.pcode for place in out.values()}) != CONSTITUENCIES or len(out) != CONSTITUENCIES:
        raise ValueError("the notice's constituencies do not match the dataset's one to one")
    return out


def ward(district: str | None, name: str, seat: str = "") -> Place | None:
    """The 2021 plan's ward that a notice names, in a district (its p-code, such as ZM101001) or else in the named
    constituency. A notice's district code predates the moves of Itezhi-Tezhi and Chama to other provinces, so
    their wards are found through their constituency."""
    spelled = letters(name)
    if district is not None:
        found = _ward_index().get((district, SPELLINGS.get((district, spelled), spelled)))
        if found is not None:
            return found
    parent = constituency(seat) if seat else None
    if parent is None:
        return None
    within = [place for place in wards() if place.parent == parent.pcode and letters(place.name) == spelled]
    return within[0] if len(within) == 1 else None


def district_code(name: str) -> str | None:
    spelled = letters(name)
    return _district_codes().get(spelled)


def _outline(p: Package, id: str, geometry: shapely.Geometry, source: str, start: date, end: date) -> None:
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=start,
        valid_to=end,
        geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=source,
    )


def geo(listed: dict[int, str], ward_names: dict[str, str], listed_150: dict[int, str]) -> Package:
    """The constituencies, `listed` by number as a notice prints them, and the wards, named by `ward_names` by p-code
    where a notice names them and by the dataset otherwise."""
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Zambia's 156 constituencies of 2016 to 2026 and its 1,853 wards of 2021 to 2026, with their shapes from the "
        "COD-AB dataset compiled by the Disaster Management and Mitigation Unit",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ZAMBIA, TERRITORY}
    cod = key("source", "cod-ab")
    p.add(
        "source",
        id=cod,
        kind="official_body",
        publisher="Disaster Management and Mitigation Unit (DMMU) Zambia, through OCHA",
        title="Zambia - Subnational Administrative Boundaries (COD-AB), version 02",
        url="https://data.humdata.org/dataset/cod-ab-zmb",
        license="CC BY-IGO 3.0",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="Disaster Management and Mitigation Unit (DMMU) Zambia; OCHA ROSEA; OCHA FISS",
        notes=(
            "Level 3 holds the constituencies and level 4 the wards. The shapes were created on 2020-11-26 and the "
            "dataset was valid for use from 2023-06-29. The shapes here are simplified to about 11 meters as one "
            "coverage, so neighbors still share their edges."
        ),
    )
    p.artifact(raw.cached(SOURCE, COD), cod)
    old = key("source", "pcgis-1991-2006")
    new = key("source", "eubank-2006-2010")
    p.add(
        "source",
        id=old,
        kind="academic",
        publisher="Provincial Centre for Geographic Information Services (Zambia), through Stanford Libraries",
        title="Ward and Constituency Boundaries: Zambia, 1991-2006",
        url="https://purl.stanford.edu/rn812zx7730",
        license="Public domain",
        attribution="Provincial Centre for Geographic Information Services (Zambia)",
        notes=(
            "Wards digitized from Central Statistical Office maps, each naming its constituency; dissolved here by "
            "constituency, moved from the Arc 1950 datum to WGS 84, and simplified to about 11 meters as one coverage."
        ),
    )
    p.add(
        "source",
        id=new,
        kind="academic",
        publisher="Nicholas Eubank, through Stanford Libraries",
        title="Zambian 2006 to 2010 Constituency and Ward Boundaries",
        url="https://purl.stanford.edu/yc436vm9005",
        license="Public domain",
        attribution="Nicholas Eubank",
        notes=(
            "Best estimates of the wards, georeferenced from printed maps the Electoral Commission of Zambia "
            "provided; dissolved here by constituency, moved from UTM zone 35S to WGS 84, and simplified to about "
            "11 meters as one coverage."
        ),
    )
    for base, source in ((OLD, old), (NEW, new)):
        for suffix in PARTS:
            p.artifact(raw.cached(SOURCE, base + suffix), source)
    plan = key("boundary_set", "zm.constituency", "1991")
    p.add(
        "boundary_set",
        id=plan,
        scheme_code="zm.constituency",
        vintage="1991",
        valid_from=PLAN_1991,
        valid_to=PLAN_2016,
        as_of=PLAN_1991,
    )
    for number, name in sorted(listed_150.items()):
        id = constituency_area_150(number)
        spelled = letters(name)
        p.add("area", id=id, scheme_code="zm.constituency")
        p.add("area_code", area_id=id, code=f"{number:03d}", valid_from=PLAN_1991, valid_to=PLAN_2016)
        p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=PLAN_1991, valid_to=PLAN_2016)
        p.name(id, name, "en", "official")
        _outline(p, id, old_plan()[OLD_NAMES.get(spelled, spelled)], old, PLAN_1991, PLAN_2006)
        _outline(p, id, new_plan()[NEW_NAMES.get(spelled, spelled)], new, PLAN_2006, PLAN_2016)
    for scheme, vintage, start in (("zm.constituency", "2016", PLAN_2016), ("zm.ward", "2021", WARDS_2021)):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, vintage),
            scheme_code=scheme,
            vintage=vintage,
            valid_from=start,
            valid_to=PLAN_2026,
            as_of=start,
        )
    seats = assign(listed)
    by_pcode: dict[str, str] = {}
    for number, place in sorted(seats.items()):
        id = constituency_area(number)
        by_pcode[place.pcode] = id
        p.add("area", id=id, scheme_code="zm.constituency")
        p.add("area_code", area_id=id, code=f"{number:03d}", valid_from=PLAN_2016, valid_to=PLAN_2026)
        p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=PLAN_2016, valid_to=PLAN_2026)
        p.add("external_id", entity_id=id, scheme="cod_ab", value=place.pcode)
        p.name(id, listed[number], "en", "official")
        _outline(p, id, place.shape, cod, PLAN_2016, PLAN_2026)
    for place in wards():
        id = ward_area(place.pcode)
        p.add("area", id=id, scheme_code="zm.ward")
        p.add("area_code", area_id=id, code=place.pcode, valid_from=WARDS_2021, valid_to=PLAN_2026)
        p.add("area_parent", area_id=id, parent_id=by_pcode[place.parent], valid_from=WARDS_2021, valid_to=PLAN_2026)
        p.name(id, ward_names.get(place.pcode, place.name), "en", "official")
        _outline(p, id, place.shape, cod, WARDS_2021, PLAN_2026)
    return p
