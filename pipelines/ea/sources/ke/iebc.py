"""Kenya's electoral areas from the Independent Electoral and Boundaries Commission (IEBC).

The IEBC's "Registered voters per county assembly ward for the 2022 general election" lists the 47 counties, 290
constituencies, and 1,450 county assembly wards with their codes and registered voters. Their shapes come from
geoBoundaries' open set (2020 vintage), matched to the IEBC's areas by name within each parent area.

The IEBC's "4th March 2013 General Election Data" prints the presidential votes in each constituency and the votes in
the other five ballots of that day, each at its finest unit, with the same constituencies and wards as the roll.

The IEBC's "Data Report of 2017 Elections" prints every candidate's votes in the six ballots of 8 August 2017: president
by county, governor, senator, and woman representative by county, member of the National Assembly by constituency, and
member of the county assembly by ward. It also prints the 26 October 2017 fresh presidential election by county, as does
the commission's separate county summary. The 2022 result forms (34A, 35A, 36A, 37A, and the 34B and 34C tallies) are
published by the IEBC only as scanned images without a text layer, so no election package is built from them yet.
"""

import difflib
import functools
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ke.iebc"
SHAPES = "ke.geoboundaries"
ROLL = "https://www.iebc.or.ke/docs/rov_per_caw.pdf"
SHAPE_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/KEN/{0}/"
    "geoBoundaries-KEN-{0}.geojson"
)
KENYA = str(uuid5(package.NAMESPACE, "polity\x1fKE"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fKEN"))
IEBC_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fiebc-roll"))
SHAPE_SOURCES = {
    "ADM1": ("geoboundaries-ken-adm1", "Public Domain", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "ADM2": (
        "geoboundaries-ken-adm2",
        "CC BY 3.0 IGO",
        "https://creativecommons.org/licenses/by/3.0/igo/",
    ),
    "ADM3": ("geoboundaries-ken-adm3", "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
}
COUNTY, CONSTITUENCY, WARD = "ke.county", "ke.constituency", "ke.ward"
ROW = re.compile(r"\s*(\d{3}) (.+?)\s{2,}(\d{3}) (.+?)\s{2,}(\d{4}) (.+?)\s{2,}([\d,]+)\s*$")
TOLERANCE = 0.0005
# The IEBC and geoBoundaries spell a few areas differently; each pair is checked by the one-to-one match below.
COUNTY_NAMES = {"nairobicity": "nairobi", "tharakanithi": "tharaka"}
# Wards with no shape of their own in geoBoundaries: each is what its constituency has left after its sibling wards.
DERIVED = {"0118"}
WARD_NAMES = {"hirimani": "bura", "sala": "bullampya"}
CONSTITUENCY_NAMES = {
    "garissatownship": "dujis",
    "chukaigambangombe": "nithi",
    "mbeeresouth": "gachoka",
    "mbeerenorth": "siakago",
    "bureti": "buret",
    "dagorettinorth": "kilimani",
    "dagorettisouth": "dagoretti",
}


@dataclass(frozen=True)
class Ward:
    county: str
    county_name: str
    constituency: str
    constituency_name: str
    code: str
    name: str
    voters: int


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def titled(text: str) -> str:
    return re.sub(r"(?<=')[A-Z]", lambda m: m.group().lower(), text.title())


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def fetch() -> None:
    from ea.sources.ke import results, results2013

    raw.fetch(SOURCE, ROLL, suffix=".pdf")
    raw.fetch(SOURCE, results2013.DATA, suffix=".pdf")
    raw.fetch(SOURCE, results.REPORT, suffix=".pdf")
    raw.fetch(SOURCE, results.FRESH, suffix=".pdf")
    for level in SHAPE_SOURCES:
        raw.fetch(SHAPES, SHAPE_URL.format(level), suffix=".geojson")


@functools.cache
def roll() -> list[Ward]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, ROLL).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    rows = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match:
            c, cn, k, kn, w, wn, v = match.groups()
            rows.append(Ward(c, cn.strip(), k, kn.strip(), w, wn.strip(), int(v.replace(",", ""))))
    if (
        len(rows) != 1450
        or len({r.county for r in rows}) != 47
        or len({(r.county, r.constituency) for r in rows}) != 290
    ):
        raise ValueError("the IEBC roll no longer lists 47 counties, 290 constituencies, and 1,450 wards")
    if len({r.code for r in rows}) != 1450:
        raise ValueError("ward codes are not unique")
    return rows


def shapes(level: str) -> list[tuple[str, shapely.Geometry]]:
    data = json.loads(raw.cached(SHAPES, SHAPE_URL.format(level)).path.read_text())
    out = []
    for feature in data["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        out.append((str(feature["properties"]["shapeName"]), shape))
    return out


Pool = dict[int, tuple[str, shapely.Geometry]]


def pool_of(items: list[tuple[str, shapely.Geometry]]) -> Pool:
    return {i: (fold(name), shape) for i, (name, shape) in enumerate(items)}


def exact(
    wanted: dict[str, str], pool: Pool, near: Pool, out: dict[str, shapely.Geometry], aliases: dict[str, str]
) -> None:
    """Give each wanted code the shape with its folded name among `near`, and take that shape from `pool`."""
    for code, name in wanted.items():
        folded = aliases.get(fold(name), fold(name))
        hit = next((i for i, (n, _) in near.items() if n == folded and i in pool), None)
        if hit is not None:
            out[code] = pool.pop(hit)[1]


def closest(
    wanted: dict[str, str], pool: Pool, near: Pool, out: dict[str, shapely.Geometry], aliases: dict[str, str]
) -> None:
    """Give each still unmatched code the most similar name among the unused shapes of `near`."""
    for code, name in sorted(wanted.items()):
        folded = aliases.get(fold(name), fold(name))
        options = [i for i in near if i in pool]
        if code in out or not options:
            continue
        best = max(options, key=lambda i: difflib.SequenceMatcher(None, folded, pool[i][0]).ratio())
        if difflib.SequenceMatcher(None, folded, pool[best][0]).ratio() >= 0.5:
            out[code] = pool.pop(best)[1]


def near(shape: shapely.Geometry, pool: Pool, margin: float) -> Pool:
    wide = shapely.buffer(shape, margin)
    shapely.prepare(wide)
    ids = list(pool)
    points = shapely.point_on_surface([pool[i][1] for i in ids])
    return {i: pool[i] for i, hit in zip(ids, shapely.contains(wide, points), strict=True) if hit}


@functools.cache
def outlines() -> tuple[dict[str, shapely.Geometry], dict[str, shapely.Geometry], dict[str, shapely.Geometry]]:
    wards = roll()
    counties = {w.county: w.county_name for w in wards}
    county_shape: dict[str, shapely.Geometry] = {}
    counties_pool = pool_of(shapes("ADM1"))
    exact(counties, counties_pool, dict(counties_pool), county_shape, COUNTY_NAMES)
    closest(counties, counties_pool, dict(counties_pool), county_shape, COUNTY_NAMES)
    constituencies = {f"{w.county}/{w.constituency}": w.constituency_name for w in wards}
    const_pool = pool_of(shapes("ADM2"))
    const_shape: dict[str, shapely.Geometry] = {}
    around = {c: near(shape, const_pool, 0.2) for c, shape in county_shape.items()}
    for c, nearby in around.items():
        mine = {k: n for k, n in constituencies.items() if k.startswith(f"{c}/")}
        exact(mine, const_pool, nearby, const_shape, CONSTITUENCY_NAMES)
    for c, nearby in around.items():
        mine = {k: n for k, n in constituencies.items() if k.startswith(f"{c}/")}
        closest(mine, const_pool, nearby, const_shape, CONSTITUENCY_NAMES)
    ward_pool = pool_of(shapes("ADM3"))
    ward_shape: dict[str, shapely.Geometry] = {}
    by_const = {ck: [w for w in wards if f"{w.county}/{w.constituency}" == ck] for ck in const_shape}
    for ck, shape in const_shape.items():
        exact({w.code: w.name for w in by_const[ck]}, ward_pool, near(shape, ward_pool, 0.005), ward_shape, {})
    for c, shape in county_shape.items():
        nearby = near(shape, ward_pool, 0.02)
        mine = {w.code: w.name for w in wards if w.county == c}
        exact(mine, ward_pool, nearby, ward_shape, {})
        exact({k: v for k, v in mine.items() if k not in ward_shape}, ward_pool, nearby, ward_shape, WARD_NAMES)
        closest(mine, ward_pool, nearby, ward_shape, {})
    for code in [w.code for w in wards if w.code not in ward_shape]:
        # geoBoundaries has no ward by this name; the ward is what its constituency has left after its siblings.
        w = next(x for x in wards if x.code == code)
        key_ = f"{w.county}/{w.constituency}"
        siblings = [ward_shape[x.code] for x in by_const[key_] if x.code in ward_shape]
        rest = shapely.difference(const_shape[key_], shapely.union_all(siblings))
        if rest.area > 0.1 * const_shape[key_].area:
            ward_shape[code] = rest
            if code not in DERIVED:
                raise ValueError(f"ward {code} has no shape; derived wards are listed in DERIVED")
    missing = [w.code for w in wards if w.code not in ward_shape]
    lost = [k for k in constituencies if k not in const_shape]
    if missing or lost or len(county_shape) != 47:
        raise ValueError(f"no shape for wards {missing}, constituencies {lost}")
    return county_shape, const_shape, ward_shape


def outline(p: Package, id: str, shape: shapely.Geometry, level: str) -> None:
    shape = shapely.make_valid(shapely.simplify(shape, TOLERANCE))
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="synthetic" if level == "derived" else "academic",
        source_id=key("source", SHAPE_SOURCES["ADM3" if level == "derived" else level][0]),
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Kenya's 47 counties, 290 constituencies, and 1,450 county assembly wards from the IEBC's roll of 2022, with "
        "geoBoundaries shapes",
    )
    p.external |= {KENYA, TERRITORY}
    p.add(
        "source",
        id=IEBC_SOURCE,
        kind="official_body",
        publisher="Independent Electoral and Boundaries Commission",
        title="Registered voters per county assembly ward for the 2022 general election",
        url=ROLL,
    )
    p.artifact(raw.cached(SOURCE, ROLL), IEBC_SOURCE)
    for level, (slug, license, license_url) in SHAPE_SOURCES.items():
        sid = key("source", slug)
        p.add(
            "source",
            id=sid,
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab)",
            title=f"geoBoundaries gbOpen Kenya {level} (2020)",
            url=f"https://www.geoboundaries.org/api/current/gbOpen/KEN/{level}/",
            license=license,
            license_url=license_url,
            attribution="geoBoundaries, William & Mary geoLab",
        )
        p.artifact(raw.cached(SHAPES, SHAPE_URL.format(level)), sid)
    for code, rank, noun in (
        (COUNTY, 1, "county"),
        (CONSTITUENCY, 2, "constituency"),
        (WARD, 3, "county assembly ward"),
    ):
        p.add("scheme", code=code, polity_id=KENYA, kind="administrative", rank=rank, noun=noun, code_system="iebc")
        p.add(
            "boundary_set",
            id=key("boundary_set", code, 2022),
            scheme_code=code,
            vintage="2022",
            valid_from=date(2013, 3, 4),
            as_of=date(2022, 8, 9),
        )
    wards = roll()
    county_shape, const_shape, ward_shape = outlines()
    seen: set[str] = set()
    for w in wards:
        if w.county not in seen:
            seen.add(w.county)
            id = area(COUNTY, w.county)
            p.add("area", id=id, scheme_code=COUNTY)
            p.add("area_code", area_id=id, code=w.county)
            p.add("area_parent", area_id=id, parent_id=TERRITORY)
            p.name(id, titled(w.county_name), "en", "official")
            outline(p, id, county_shape[w.county], "ADM1")
        ck = f"{w.county}/{w.constituency}"
        if ck not in seen:
            seen.add(ck)
            id = area(CONSTITUENCY, ck)
            p.add("area", id=id, scheme_code=CONSTITUENCY)
            p.add("area_code", area_id=id, code=w.constituency)
            p.add("area_parent", area_id=id, parent_id=area(COUNTY, w.county))
            p.name(id, titled(w.constituency_name), "en", "official")
            outline(p, id, const_shape[ck], "ADM2")
        id = area(WARD, w.code)
        p.add("area", id=id, scheme_code=WARD)
        p.add("area_code", area_id=id, code=w.code)
        p.add("area_parent", area_id=id, parent_id=area(CONSTITUENCY, ck))
        p.name(id, titled(w.name), "en", "official")
        outline(p, id, ward_shape[w.code], "derived" if w.code in DERIVED else "ADM3")
    return p


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/registry-2013",
        f"{SOURCE}/general-2013",
        f"{SOURCE}/general-2017",
        f"{SOURCE}/fresh-presidential-2017",
    ]


def parse() -> None:
    from ea.sources.ke import results, results2013

    geo().write()
    results.registry().write()
    results2013.registry().write()
    results2013.general().write()
    results.general().write()
    results.rerun().write()


def emit() -> None:
    package.run("load", packages())
