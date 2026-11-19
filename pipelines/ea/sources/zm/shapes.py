"""Zambia's constituencies of the 2016 and 2026 plans and wards of the 2021 plan, with their shapes, for `zm.ecz`.

The shapes come from OCHA's COD-AB dataset for Zambia, which the Disaster Management and Mitigation Unit compiled from
the Electoral Commission's delimitation: its third level holds the 156 constituencies and its fourth the 1,853 wards.
Each constituency matches the Commission's notices by name; three Solwezi seats carry their district's name in the
dataset. Each ward matches a notice's ward by name within its district, after the curated spellings below.

The 226 constituencies of the 2026 plan are drawn from the same wards. The Commission's delimitation schedules list
the wards of each new or reviewed constituency; every other constituency keeps the wards it held in 2016, less any
that a schedule gives to another. Solwezi Central and Solwezi North share six wards cut from the dataset's Rodwell
Mwepu ward, so those two have no shape.
"""

import functools
import io
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
from shapely.geometry import shape
from shapely.ops import transform

from ea import package, raw
from ea.package import Package
from ea.sources.zm import text

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
UPLOADS = "https://www.elections.org.zm/wp-content/uploads"
DELIMITATION = "https://www.elections.org.zm/?p=5529"
PROVINCES = (
    "Central",
    "Copperbelt",
    "Eastern",
    "Luapula",
    "Lusaka",
    "Muchinga",
    "North-Western",
    "Northern",
    "Southern",
    "Western",
)
SCHEDULES = [f"{UPLOADS}/{province}-Province-1.pdf" for province in PROVINCES]
SEATS_2026 = 226
# a ward a 2026 schedule spells differently from the dataset, as (district letters, schedule letters): dataset letters
SCHEDULE_SPELLINGS = {
    ("LUFWANYAMA", "KABUNDYA"): "KABUNDIA",
    ("NDOLA", "DAGHAMMERSKJOELD"): "DAGHAMMERSJOELD",
    ("ZAMBEZI", "LWITADILWATEMBO"): "LWITADILWATEMBU",
    ("MPULUNGU", "MUSENGEZI"): "MUSENGENZI",
    ("SESHEKE", "KATIMAMULILO"): "KATIMA",
}
# an unscheduled constituency of 2026 that the 2021 notice names otherwise, as 2026 letters: 2021 letters
RENAMED = {"LUANO": "MKUSHISOUTH", "MUSHINDAMO": "SOLWEZIEAST"}
# the schedule's wards that the dataset holds as one ward, Rodwell Mwepu, which they split between two constituencies
SPLIT = {"KYAWAMA", "KIMALE", "KAZHIBA", "KIVUKU", "KYAFUKUMA", "KIFUBWA"}
CONSTITUENCIES = 156
WARDS = 1853
WARD_MATCH = 0.85  # the least likeness of two ward spellings that counts as one ward
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


def constituency_area_2026(number: int) -> str:
    return key("area", "zm.constituency", 2026, f"{number:03d}")


def ward_area(pcode: str) -> str:
    return key("area", "zm.ward", 2021, pcode)


def ward_area_2006(constituency: str, name: str) -> str:
    return key("area", "zm.ward", 2006, constituency, name)


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


@dataclass(frozen=True)
class OldWard:
    constituency: str  # the layer's constituency letters
    name: str
    shape: shapely.Geometry


@functools.cache
def wards_2006() -> dict[tuple[str, str], OldWard]:
    """The wards of the 2006 to 2010 maps by their constituency's and their own letters, simplified as one coverage.
    A ward the layer splits into several parts is one ward, so its parts are dissolved."""
    parts = {suffix: raw.cached(SOURCE, NEW + suffix).path for suffix in PARTS}
    reader = shapefile.Reader(
        shp=io.BytesIO(parts["shp"].read_bytes()),
        shx=io.BytesIO(parts["shx"].read_bytes()),
        dbf=io.BytesIO(parts["dbf"].read_bytes()),
        encoding="latin-1",
    )
    move = pyproj.Transformer.from_crs(pyproj.CRS.from_wkt(parts["prj"].read_text()), 4326, always_xy=True).transform
    grouped: dict[tuple[str, str], list[shapely.Geometry]] = {}
    names: dict[tuple[str, str], str] = {}
    for record, outline in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or outline is None:
            raise ValueError("the 2006 to 2010 layer holds a record without a shape")
        found = (letters(str(record["constName"])), letters(str(record["wardName"])))
        names.setdefault(found, " ".join(str(record["wardName"]).split()))
        grouped.setdefault(found, []).append(transform(move, shapely.make_valid(shape(outline.__geo_interface__))))
    order = sorted(grouped)
    merged = [shapely.make_valid(shapely.union_all(grouped[found]).buffer(0)) for found in order]
    simple = shapely.coverage_simplify(numpy.array(merged), TOLERANCE)
    return {
        found: OldWard(found[0], names[found], _multi(outline)) for found, outline in zip(order, simple, strict=True)
    }


def ward_constituency(notice: str, listed: str) -> str | None:
    """The 2006 to 2010 layer's letters for the constituency a 2011 notice names, or else the 2008 list's name for
    its number, which a later notice may have renamed. The layer's ward labels follow the notices' own spellings,
    unlike its swapped shapes for Mansa and Chembe. None where the layer holds no wards for either."""
    held = {found[0] for found in wards_2006()}
    for name in (letters(notice), letters(listed)):
        for spelled in (name, NEW_NAMES.get(name)):
            if spelled in held:
                return spelled
    return None


def match_wards_2006(wanted: Mapping[str, tuple[str, str]]) -> dict[str, OldWard]:
    """The layer's ward for each notice ward, by code, as (constituency letters, ward name): a ward of the same
    spelling in the same constituency first, then, among the wards still unclaimed, the one spelled alike enough that
    no other in the constituency comes close. Each layer ward goes to at most one notice ward."""
    layer = wards_2006()
    found: dict[str, OldWard] = {}
    claimed: set[tuple[str, str]] = set()
    for code, (constituency, name) in wanted.items():
        key_ = (constituency, letters(name))
        if key_ in layer and key_ not in claimed:
            found[code] = layer[key_]
            claimed.add(key_)
    for code, (constituency, name) in wanted.items():
        if code in found:
            continue
        spelled = letters(name)
        scored = sorted(
            (
                (SequenceMatcher(None, spelled, other[1]).ratio(), other)
                for other in layer
                if other[0] == constituency and other not in claimed
            ),
            reverse=True,
        )
        if scored and scored[0][0] >= WARD_MATCH and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.1):
            found[code] = layer[scored[0][1]]
            claimed.add(scored[0][1])
    return found


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


@functools.cache
def schedule() -> list[tuple[str, str, str]]:
    """Every row of the ten provincial delimitation schedules of 2026, as (district, constituency, ward)."""
    return [row for url in SCHEDULES for row in text.read_schedule(raw.cached(SOURCE, url).path)]


def plan_2026(listed: dict[int, str], listed_2016: dict[int, str]) -> dict[int, shapely.Geometry]:
    """The shapes of the 2026 plan's constituencies, `listed` by number as the 2026 notice prints them, from the
    dataset's wards: a scheduled constituency takes the wards its schedule lists, and any other keeps the wards of the
    2016 constituency of its number (`listed_2016`) that no schedule gives away. A constituency with a ward the dataset
    does not hold apart is left out."""
    by_name = {letters(name): number for number, name in listed.items()}
    if len(by_name) != SEATS_2026 or len(listed) != SEATS_2026:
        raise ValueError(f"the 2026 notice lists {len(listed)} constituencies, not {SEATS_2026}")
    by_district: dict[str, list[Place]] = {}
    for place in wards():
        by_district.setdefault(letters(place.district_name), []).append(place)
    members: dict[int, list[Place]] = {}
    split: set[int] = set()
    claimed: dict[str, int] = {}
    for district, seat, name in schedule():
        number = by_name.get(letters(seat))
        if number is None:
            raise ValueError(f"the schedule's constituency {seat!r} is not in the 2026 notice")
        members.setdefault(number, [])
        spelled = SCHEDULE_SPELLINGS.get((letters(district), letters(name)), letters(name))
        if spelled in SPLIT:
            split.add(number)
            continue
        found = [place for place in by_district.get(letters(district), []) if letters(place.name) == spelled]
        if not found:
            found = [place for place in wards() if letters(place.name) == spelled]
        if len(found) != 1:
            raise ValueError(f"the schedule's ward {name!r} of {seat} matches {len(found)} wards in the dataset")
        if found[0].pcode in claimed:
            raise ValueError(f"the schedules give ward {name!r} to two constituencies")
        claimed[found[0].pcode] = number
        members[number].append(found[0])
    old = assign(listed_2016)
    for number, name in listed.items():
        if number in members:
            continue
        if number not in old:
            raise ValueError(f"new constituency {number} {name} has no schedule")
        if RENAMED.get(letters(name), letters(name)) != letters(listed_2016[number]):
            raise ValueError(f"constituency {number} {name} was {listed_2016[number]} in 2016")
        kept = [place for place in wards() if place.parent == old[number].pcode and place.pcode not in claimed]
        if not kept:
            raise ValueError(f"constituency {number} {name} keeps none of its 2016 wards")
        members[number] = kept
        claimed.update((place.pcode, number) for place in kept)
    left = [place for place in wards() if place.pcode not in claimed]
    shared = {old[number].pcode for number in split if number in old}
    if any(place.parent not in shared for place in left):
        raise ValueError(f"{len(left)} wards of the dataset fall in no 2026 constituency")
    return {
        number: _multi(shapely.union_all([place.shape for place in found]))
        for number, found in sorted(members.items())
        if number not in split
    }


def district_code(name: str) -> str | None:
    spelled = letters(name)
    return _district_codes().get(spelled)


def _outline(p: Package, id: str, geometry: shapely.Geometry, source: str, start: date, end: date | None) -> None:
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


@dataclass(frozen=True)
class Ward2006:
    """A ward of the 2011 councillor notice that the 2006 to 2010 layer holds: its notice code, name, and
    constituency number, and the layer's ward."""

    code: str
    name: str
    constituency: int
    found: OldWard


def geo(
    listed: dict[int, str],
    ward_names: dict[str, str],
    listed_150: dict[int, str],
    listed_2026: dict[int, str],
    placed_2006: list[Ward2006],
) -> Package:
    """The constituencies, `listed` by number as a notice prints them, and the wards, named by `ward_names` by p-code
    where a notice names them and by the dataset otherwise."""
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Zambia's 150 constituencies of 1991 to 2016, its 156 of 2016 to 2026, its 226 from 2026, its wards of 2006 to "
        "2016 that the 2006 to 2010 ward maps hold, and its 1,853 wards of 2021 to 2026, with their shapes from public-domain ward layers, the COD-AB dataset compiled by the "
        "Disaster Management and Mitigation Unit, and the Electoral Commission's 2026 delimitation schedules",
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
            "provided; moved from UTM zone 35S to WGS 84 and simplified to about 11 meters as one coverage, "
            "dissolved by constituency for the constituencies and kept as wards for the wards of the 2011 notice."
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
    p.add(
        "boundary_set",
        id=key("boundary_set", "zm.ward", "2006"),
        scheme_code="zm.ward",
        vintage="2006",
        valid_from=PLAN_2006,
        valid_to=PLAN_2016,
        as_of=PLAN_2006,
    )
    for ward_2006 in placed_2006:
        id = ward_area_2006(ward_2006.found.constituency, letters(ward_2006.found.name))
        p.add("area", id=id, scheme_code="zm.ward")
        p.add("area_code", area_id=id, code=ward_2006.code, valid_from=PLAN_2006, valid_to=PLAN_2016)
        p.add(
            "area_parent",
            area_id=id,
            parent_id=constituency_area_150(ward_2006.constituency),
            valid_from=PLAN_2006,
            valid_to=PLAN_2016,
        )
        p.name(id, ward_2006.name, "en", "official")
        _outline(p, id, ward_2006.found.shape, new, PLAN_2006, PLAN_2016)
    schedules = key("source", "delimitation-2026")
    p.add(
        "source",
        id=schedules,
        kind="official_body",
        publisher="Electoral Commission of Zambia",
        title="2026 Delimitation of Constituencies: Schedule of New and Reviewed Constituencies with Wards",
        url=DELIMITATION,
        license="No license stated; published by the electoral authority for public use",
        attribution="Electoral Commission of Zambia",
        notes=(
            "Ten provincial schedules, announced on 16 April 2026, listing the wards of each new or reviewed "
            "constituency. Each 2026 constituency here is the union of its wards' shapes in the COD-AB dataset; a "
            "constituency the schedules leave out keeps the wards of its 2016 namesake that no schedule gives "
            "away. Solwezi Central and Solwezi North divide six wards that the dataset holds as one, Rodwell "
            "Mwepu, so they have no shape."
        ),
    )
    for url in SCHEDULES:
        p.artifact(raw.cached(SOURCE, url), schedules)
    p.add(
        "boundary_set",
        id=key("boundary_set", "zm.constituency", "2026"),
        scheme_code="zm.constituency",
        vintage="2026",
        valid_from=PLAN_2026,
        as_of=PLAN_2026,
    )
    outlines = plan_2026(listed_2026, listed)
    for number, name in sorted(listed_2026.items()):
        id = constituency_area_2026(number)
        p.add("area", id=id, scheme_code="zm.constituency")
        p.add("area_code", area_id=id, code=f"{number:03d}", valid_from=PLAN_2026)
        p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=PLAN_2026)
        p.name(id, name, "en", "official")
        if number in outlines:
            _outline(p, id, outlines[number], schedules, PLAN_2026, None)
    return p
