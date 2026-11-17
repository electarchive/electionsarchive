"""China's provincial-level units (31 provinces, autonomous regions, and municipalities) and their GB/T 2260 codes.

The National Bureau of Statistics' list of statistical division codes names the 31 units in mainland China and gives
each its two-digit code, the leading digits of the six-digit administrative division code of GB/T 2260. The list
leaves out Taiwan, Hong Kong, and Macao, so the scheme does too. Boundaries come from geoBoundaries' open set.
"""

import json
import re
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "cn.nbs"
CHINA = str(uuid5(package.NAMESPACE, "polity\x1fCN"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fCHN"))
SCHEME = "cn.province"
LIST_URL = (
    "https://web.archive.org/web/20240619163501id_/https://www.stats.gov.cn/sj/tjbz/tjyqhdmhcxhfdm/2023/index.html"
)
SHAPES_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/CHN/ADM1/"
    "geoBoundaries-CHN-ADM1.geojson"
)
EXPECTED = {"province": 22, "autonomous_region": 5, "municipality": 4}
ENGLISH = {
    "11": "Beijing",
    "12": "Tianjin",
    "13": "Hebei",
    "14": "Shanxi",
    "15": "Inner Mongolia",
    "21": "Liaoning",
    "22": "Jilin",
    "23": "Heilongjiang",
    "31": "Shanghai",
    "32": "Jiangsu",
    "33": "Zhejiang",
    "34": "Anhui",
    "35": "Fujian",
    "36": "Jiangxi",
    "37": "Shandong",
    "41": "Henan",
    "42": "Hubei",
    "43": "Hunan",
    "44": "Guangdong",
    "45": "Guangxi",
    "46": "Hainan",
    "50": "Chongqing",
    "51": "Sichuan",
    "52": "Guizhou",
    "53": "Yunnan",
    "54": "Tibet",
    "61": "Shaanxi",
    "62": "Gansu",
    "63": "Qinghai",
    "64": "Ningxia",
    "65": "Xinjiang",
}
# geoBoundaries' name for the one unit its layer does not name by its English name (Guangdong, mislabeled).
SHAPE_NAMES = {"44": "Guangzhou Province"}
NOUNS = {"province": "province", "autonomous_region": "autonomous region", "municipality": "municipality"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", "geoboundaries.chn.adm1")


def fetch() -> None:
    raw.fetch(SOURCE, SHAPES_URL, suffix=".geojson")
    try:
        raw.cached(SOURCE, LIST_URL)
    except LookupError, KeyError:
        raw.fetch(SOURCE, LIST_URL, suffix=".html")


def units() -> list[tuple[str, str, str]]:
    """The units as (two-digit code, Chinese name, kind), in code order."""
    text = raw.cached(SOURCE, LIST_URL).path.read_text(encoding="utf-8")
    found = re.findall(r"""<a href=["']?(\d{2})\.html["']?[^>]*>([^<]+)<""", text)
    out: list[tuple[str, str, str]] = []
    for code, name in sorted(set(found)):
        name = name.strip()
        kind = "autonomous_region" if name.endswith("自治区") else "municipality" if name.endswith("市") else "province"
        out.append((code, name, kind))
    counts = {kind: sum(1 for _, _, k in out if k == kind) for kind in EXPECTED}
    if counts != EXPECTED or {code for code, _, _ in out} != set(ENGLISH):
        raise ValueError(f"the list holds {counts}, not {EXPECTED}")
    return out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "China's 31 provincial-level units (provinces, autonomous regions, and municipalities) with their GB/T 2260 "
        "codes from the National Bureau of Statistics' division code list, and their outlines",
    )
    p.external |= {CHINA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="国家统计局",
        title="2023年统计用区划代码和城乡划分代码 (statistical division codes and urban-rural classification codes, 2023)",
        url="https://www.stats.gov.cn/sj/tjbz/tjyqhdmhcxhfdm/2023/index.html",
        license="Official statistical standard of the People's Republic of China, published for public use",
        license_url="https://www.stats.gov.cn/sj/tjbz/",
        attribution="National Bureau of Statistics of China",
    )
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen China ADM1 (provincial outlines, 2019)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), from Wikimedia Commons",
        notes="The China layer's own metadata marks its source layer as public domain.",
    )
    listing = raw.cached(SOURCE, LIST_URL)
    shapes = raw.cached(SOURCE, SHAPES_URL)
    p.artifact(listing, SOURCE_ID)
    p.artifact(shapes, SHAPES_ID)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=CHINA,
        kind="administrative",
        rank=1,
        noun="province",
        kinds=NOUNS,
        code_system="gb_t_2260",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, 2023),
        scheme_code=SCHEME,
        vintage="2023",
        as_of=shapes.retrieved_at.date(),
    )
    outlines = {
        feature["properties"]["shapeName"]: shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]
    }
    for code, zh, kind in units():
        id = key("area", SCHEME, code)
        english = ENGLISH[code]
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=f"{code}0000")
        p.add("area_kind", area_id=id, kind=kind, source_id=SOURCE_ID)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, zh, "zh", "official")
        p.name(id, english, "en", "english", False)
        found = [n for n in outlines if n.startswith(english + " ")] or [SHAPE_NAMES.get(code, "")]
        shape = found[0] if len(found) == 1 else ""
        outline = outlines.get(shape)
        if outline is None:
            raise ValueError(f"no outline for {english} ({code})")
        outline = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(shapely.make_valid(outline)))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPES_ID,
        )
    return p


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", [f"{SOURCE}/geo"])
