import re
import sys

from ea import raw

SOURCE = "eg.nea"
BASE = "https://www.elections.eg/static"
YEARS = (2020, 2025)
STAGES = ("stage_1_round_1", "stage_1_round_2", "stage_2_round_1", "stage_2_round_2")
LISTS = ("99901", "99902", "99903", "99904")
OPTION = re.compile(r"<option value='(\d+)' class='\d+'>")
# The scripted requests the static 2020 archive pages refuse are answered by the same pages on the main site. Their
# labels are crossed: the "stage 2 round 1" page lists the constituencies of the second stage's governorates and the
# "stage 2 round 2" page those of the first stage's, and each lists every constituency once for both rounds.
ARCHIVE_2020 = {
    "stage_2": "https://www.elections.eg/archive/house-2020/hor20-results/hor-20-stage2-round1-results",
    "stage_1": "https://www.elections.eg/archive/house-2020/hor20-results/hor-20-stage2-round2-results",
}


def archive_url(year: int, stage: str) -> str:
    return f"{BASE}/house_{year}/results/{stage}_results/"


def fragment_url(year: int, stage: str, code: str) -> str:
    return f"{BASE}/house_{year}/results/{stage}_results/html/{code}.html"


def codes(year: int) -> dict[str, list[str]]:
    """Individual constituency codes per stage, read from the option values of whichever archive pages answered."""
    found: dict[str, set[str]] = {"stage_1": set(), "stage_2": set()}
    for stage in STAGES:
        try:
            page = raw.cached(SOURCE, archive_url(year, stage)).path.read_text("utf-8")
        except LookupError:
            continue
        found[stage[:7]] |= set(OPTION.findall(page))
    if year == 2020:
        for stage, url in ARCHIVE_2020.items():
            found[stage] |= set(OPTION.findall(raw.cached(SOURCE, url).path.read_text("utf-8")))
    return {stage: sorted(values, key=int) for stage, values in found.items()}


def fetch() -> None:
    for url in ARCHIVE_2020.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for year in YEARS:
        for stage in STAGES:
            try:
                raw.fetch(SOURCE, archive_url(year, stage), suffix=".html")
            except Exception as failure:
                print(f"{archive_url(year, stage)}: {failure}", file=sys.stderr, flush=True)
    urls: list[str] = []
    for year in YEARS:
        known = codes(year)
        for stage in STAGES:
            urls += [fragment_url(year, stage, code) for code in (*known[stage[:7]], *LISTS)]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=4)
    senate_fetch()
    raw.fetch(GEOBOUNDARIES, ADM1, suffix=".geojson")
    raw.fetch(GEOBOUNDARIES, ADM2, suffix=".geojson")
    raw.fetch(SOURCE, LAW_2025, suffix=".pdf")
    raw.fetch(SOURCE, GAZETTE_2020, suffix=".pdf")
    presidential_fetch()


import functools
import html
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package
from ea.package import Package

EGYPT = str(uuid5(package.NAMESPACE, "polity\x1fEG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fEGY"))
GEOBOUNDARIES = "eg.geoboundaries"
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/EGY/ADM1/"
    "geoBoundaries-EGY-ADM1_simplified.geojson"
)
ADM2 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/EGY/ADM2/"
    "geoBoundaries-EGY-ADM2_simplified.geojson"
)
# Law 174 of 2020 divides the country into constituencies. The Authority's copy carries the tables as Law 85 of 2025
# replaced them, which the 2025 election used; the Official Gazette of 3 September 2020 carries the original tables.
LAW_2025 = "https://www.elections.eg/images/pdfs/laws/senate_2025/174-2020.pdf"
GAZETTE_2020 = "https://manshurat.org/file/79219/download?token=e4H7FhnK"
DISTRICTS = Path(__file__).with_name("house_districts.tsv")
UNITS = Path(__file__).with_name("house_units.tsv")
FIRST_DAY = {2020: date(2020, 10, 24), 2025: date(2025, 11, 10)}
# The governorate codes the authority uses, with the ISO 3166-2 code that geoBoundaries gives the same governorate.
GOVERNORATES = {
    "19": "EG-C", "27": "EG-ALX", "35": "EG-PTS", "43": "EG-SUZ", "51": "EG-DT", "61": "EG-DK", "78": "EG-SHR",
    "86": "EG-KB", "94": "EG-KFS", "108": "EG-GH", "116": "EG-MNF", "124": "EG-BH", "132": "EG-IS", "141": "EG-GZ",
    "159": "EG-BNS", "167": "EG-FYM", "175": "EG-MN", "183": "EG-AST", "191": "EG-SHG", "205": "EG-KN",
    "213": "EG-ASN", "221": "EG-BA", "231": "EG-WAD", "248": "EG-MT", "256": "EG-SIN", "264": "EG-JS",
    "311": "EG-LX",
}  # fmt: skip
SECTORS = {
    "99901": "Cairo, South and Middle Delta Sector",
    "99902": "North, Middle and South Upper Egypt Sector",
    "99903": "East Delta Sector",
    "99904": "West Delta Sector",
}
# The governorates each list district joins, from the second table of Law 174 of 2020 (the same in both versions).
SECTOR_GOVERNORATES = {
    "99901": ("19", "86", "61", "116", "108", "94"),
    "99902": ("141", "167", "159", "175", "183", "231", "191", "205", "311", "213", "221"),
    "99903": ("78", "51", "35", "132", "43", "256", "264"),
    "99904": ("27", "124", "248"),
}
# A constituency that is a whole governorate takes the governorate's own shape.
WHOLE_GOVERNORATE = {"4301": "43"}
INDEPENDENT = "مستقل"
METHOD = (
    "Published by the National Elections Authority as a results page for each constituency. The pages name the "
    "winners and the candidates sent to a runoff, and a list district's winning list with its candidates. They do not "
    "name candidates who lost in the first round, and they carry no turnout, valid, or invalid counts. Votes appear "
    "only where the page prints them: the votes of the individual-seat winners and the votes for and against the "
    "winning list in the 2025 first round. Runoff candidates and runoff winners carry no votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def cells(row: str) -> list[str]:
    return [clean(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]


def number(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass
class Page:
    title: str = ""
    winners: list[list[str]] = field(default_factory=list)
    runoff: list[list[str]] = field(default_factory=list)
    remaining: int | None = None
    list_name: str = ""
    list_votes: tuple[int, int] | None = None
    members: list[list[str]] = field(default_factory=list)


@functools.cache
def page(year: int, stage: str, code: str) -> Page | None:
    try:
        text = raw.cached(SOURCE, fragment_url(year, stage, code)).path.read_text("utf-8")
    except LookupError:
        return None
    return parse_fragment(text, f"{year} {stage} {code}")


def parse_fragment(text: str, label: str) -> Page:
    year, stage, code = label.split(" ")
    heading = re.search(r"<h2[^>]*>(.*?)</h2>", text, re.DOTALL)
    if heading is None:
        raise ValueError(f"{year} {stage} {code}: no heading")
    out = Page(title=clean(heading.group(1)))
    for section in re.split(r'<h3 class="winner_header red">', text)[1:]:
        head, _, body = section.partition("</h3>")
        head = clean(head)
        if head == "القائمة الفائزة":
            main, _, sub = body.partition("class='sub-list'")
            row = next(r for r in re.findall(r"<tr>(.*?)</tr>", main, re.DOTALL) if "candidate_list_link" in r)
            found = cells(row)
            out.list_name = re.sub(r"\s*\(\s*عرض المرشحين\s*\)\s*$", "", found[1])
            if len(found) >= 4:
                out.list_votes = (number(found[2]), number(found[3]))
            out.members = [c for c in (cells(r) for r in re.findall(r"<tr>(.*?)</tr>", sub, re.DOTALL)) if len(c) == 4]
            continue
        match = re.search(r"result_number'>(\d+)<", body)
        if match:
            out.remaining = int(match.group(1))
        rows = [c for c in (cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL)) if c]
        if head == "المرشحين الفائزين":
            out.winners = rows
        elif head.startswith("المرشحين فى مرحلة"):
            out.runoff = rows
        else:
            raise ValueError(f"{year} {stage} {code}: unknown section {head!r}")
    return out


def stage_codes(year: int) -> list[tuple[str, str]]:
    known = codes(year)
    return [
        (stage, code)
        for stage in ("stage_1", "stage_2")
        for code in known[stage]
        if page(year, f"{stage}_round_2", code)
    ]


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/hor-{year}" for year in YEARS),
        f"{SOURCE}/senate-registry",
        *(f"{SOURCE}/sen-{year}" for year in YEARS),
        f"{SOURCE}/pres-registry",
        *(f"{SOURCE}/pres-{year}" for year in PRES_YEARS),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        house(year).write()
    senate_registry().write()
    for year in YEARS:
        senate(year).write()
    pres_registry().write()
    for year in PRES_YEARS:
        presidential(year).write()


def emit() -> None:
    package.run("load", packages())


def governorate_of(code: str) -> str:
    return code[:-2]


def constituency_area(year: int, code: str) -> str:
    return key("area", "eg.constituency", year, code)


def governorate_area(code: str) -> str:
    return key("area", "eg.governorate", code)


def sector_area(year: int, code: str) -> str:
    return key("area", "eg.sector", year, code)


def _polygons(shape: shapely.Geometry) -> shapely.MultiPolygon:
    shape = shapely.make_valid(shape)
    parts = [
        piece
        for part in shapely.get_parts(shape)
        if shapely.get_type_id(part) in (3, 6)
        for piece in shapely.get_parts(part)
    ]
    return shapely.set_srid(shapely.MultiPolygon(parts), 4326)


def _governorate_shapes(artifact: raw.Artifact) -> dict[str, shapely.Geometry]:
    iso = {value: code for code, value in GOVERNORATES.items()}
    return {
        iso[str(f["properties"]["shapeISO"])]: shapely.make_valid(shapely.geometry.shape(f["geometry"]))
        for f in json.loads(artifact.path.read_text("utf-8"))["features"]
    }


@dataclass
class Unit:
    name: str
    governorate: str
    shape: shapely.Geometry


def _units(governorates: dict[str, shapely.Geometry]) -> list[Unit]:
    """geoBoundaries' second-level units, each placed in the governorate that holds most of it."""
    found: list[Unit] = []
    for f in json.loads(raw.cached(GEOBOUNDARIES, ADM2).path.read_text("utf-8"))["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(f["geometry"]))
        holder = max(governorates, key=lambda code: shape.intersection(governorates[code]).area)
        found.append(Unit(str(f["properties"]["shapeName"]), holder, shape))
    return found


def _pick(units: list[Unit], token: str) -> Unit:
    """One unit by name, as `Name`, `Name@governorate`, or `Name#M` or `Name#Q` for the larger or smaller of two."""
    match = re.fullmatch(r"(.*?)(?:@(\d+))?(?:#([MQ]))?", token)
    assert match is not None
    name, governorate, size = match.groups()
    found = [u for u in units if u.name == name and (governorate is None or u.governorate == governorate)]
    if size:
        if len(found) != 2:
            raise LookupError(f"{token}: {len(found)} units, not two")
        found.sort(key=lambda u: u.shape.area)
        return found[0] if size == "Q" else found[1]
    if len(found) != 1:
        raise LookupError(f"{token}: {len(found)} units")
    return found[0]


def district_components() -> dict[tuple[int, str], list[str]]:
    """The administrative units, qisms (q) and markazes (m), that the law gives each individual constituency."""
    found: dict[tuple[int, str], list[str]] = {}
    for line in DISTRICTS.read_text("utf-8").splitlines()[1:]:
        year, code, components = line.split("\t")
        found[(int(year), code)] = components.split(" | ")
    return found


def district_shapes(
    governorates: dict[str, shapely.Geometry],
) -> tuple[dict[tuple[int, str], shapely.Geometry], dict[tuple[int, str], list[str]]]:
    """Each constituency's shape as the union of its law components' geoBoundaries units, and what is left out.

    geoBoundaries has no polygon for some units the law names (it is coarser, and some units were created later). A
    constituency whose component has none keeps no shape, unless a point inside that component is given and lies in the
    union of the constituency's other units; the Authority and the mapping agency publish no constituency shapes.
    """
    units = _units(governorates)
    mapping: dict[tuple[str, str], tuple[list[str], tuple[float, float] | None]] = {}
    for line in UNITS.read_text("utf-8").splitlines()[1:]:
        governorate, component, tokens, inside = line.split("\t")
        lon, _, lat = inside.partition(",")
        mapping[(governorate, component)] = (tokens.split("|"), (float(lon), float(lat)) if inside else None)
    shapes: dict[tuple[int, str], shapely.Geometry] = {}
    left: dict[tuple[int, str], list[str]] = {}
    for (year, code), components in district_components().items():
        governorate = code[:-2]
        if code in WHOLE_GOVERNORATE:
            shapes[(year, code)] = governorates[WHOLE_GOVERNORATE[code]]
            continue
        chosen: list[Unit] = []
        absent: list[tuple[str, tuple[float, float] | None]] = []
        for component in components:
            tokens, point = mapping[(governorate, component)]
            if tokens == ["-"]:
                absent.append((component, point))
            else:
                chosen += [_pick(units, token) for token in tokens]
        if not chosen:
            left[(year, code)] = [component for component, _ in absent]
            continue
        union = shapely.union_all([u.shape for u in chosen])
        outside = [c for c, point in absent if point is None or not union.buffer(0.005).contains(shapely.Point(point))]
        if outside:
            left[(year, code)] = outside
            continue
        shapes[(year, code)] = union
    return shapes, left


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Egypt's 27 governorates from geoBoundaries, with the House of Representatives constituencies and list "
        "districts of 2020 and 2025 placed within them and shaped from the units the law gives each",
    )
    p.external |= {EGYPT, COUNTRY}
    artifact = raw.cached(GEOBOUNDARIES, ADM1)
    source = key("source", "geoboundaries")
    p.add(
        "source",
        id=source,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Egypt ADM1, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
        notes=(
            "The governorates are the ADM1 units. A constituency's shape is the union of the ADM2 units (markazes and "
            "qisms) that the law names for it, so it follows the source's units, which are coarser than the law's in "
            "places; a constituency with a named unit that has no polygon in the source has no shape. A list "
            "district's shape is the union of its governorates."
        ),
    )
    p.artifact(artifact, source)
    for code, rank, kind, noun in (
        ("eg.governorate", 1, "administrative", "governorate"),
        ("eg.constituency", 2, "electoral_district", "constituency"),
        ("eg.sector", 2, "electoral_district", "list district"),
    ):
        p.add("scheme", code=code, polity_id=EGYPT, kind=kind, rank=rank, noun=noun)
    p.add(
        "boundary_set",
        id=key("boundary_set", "eg.governorate", "2017"),
        scheme_code="eg.governorate",
        vintage="2017",
        as_of=artifact.retrieved_at.date(),
    )
    shapes = {
        str(f["properties"]["shapeISO"]): shapely.geometry.shape(f["geometry"])
        for f in json.loads(artifact.path.read_text("utf-8"))["features"]
    }
    arabic = _governorate_names()
    english = {
        str(f["properties"]["shapeISO"]): re.sub(r" Gover?nate$|rnorate$", "", str(f["properties"]["shapeName"]))
        for f in json.loads(artifact.path.read_text("utf-8"))["features"]
    }
    for gov, iso in GOVERNORATES.items():
        id = governorate_area(gov)
        p.add("area", id=id, scheme_code="eg.governorate")
        p.add("area_code", area_id=id, code=gov)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, arabic[gov], "ar", "official")
        p.name(id, re.sub(r" Governorate$| Governate$", "", english[iso]), "en", "english", False)
        shape = shapely.make_valid(shapes[iso])
        shape = shapely.MultiPolygon([g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3])
        shape = shapely.set_srid(shape, 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=source,
        )
    adm2 = raw.cached(GEOBOUNDARIES, ADM2)
    p.artifact(adm2, source)
    law = key("source", "law-174-2020")
    p.add(
        "source",
        id=law,
        kind="official_body",
        publisher="الجريدة الرسمية (Official Gazette of Egypt) and the National Elections Authority",
        title="Law 174 of 2020 dividing the electoral districts of the House of Representatives, with its tables",
        url="https://www.elections.eg/images/pdfs/laws/senate_2025/174-2020.pdf",
        license="Text of a law, published with no license stated",
        license_url="https://www.elections.eg/",
        attribution="Arab Republic of Egypt, Official Gazette no. 36 (supplement) of 3 September 2020; Law 85 of 2025",
        notes=(
            "The tables of 2025 are the Authority's copy of the law as Law 85 of 2025 replaced them; the tables of "
            "2020 are the original in the Official Gazette of 3 September 2020. Each names the administrative units "
            "(qisms and markazes) of every individual-seat constituency, and the governorates of each list district."
        ),
    )
    p.artifact(raw.cached(SOURCE, LAW_2025), law)
    p.artifact(raw.cached(SOURCE, GAZETTE_2020), law)
    governorate_shapes = _governorate_shapes(artifact)
    district_shape, _ = district_shapes(governorate_shapes)

    def shaped(id: str, shape: shapely.Geometry) -> None:
        shape = _polygons(shape)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=source,
        )

    for year in YEARS:
        for stage, code in stage_codes(year):
            found = page(year, f"{stage}_round_2", code)
            assert found is not None
            id = constituency_area(year, code)
            p.add("area", id=id, scheme_code="eg.constituency")
            p.add("area_code", area_id=id, code=f"{year}:{code}")
            p.add("area_parent", area_id=id, parent_id=governorate_area(governorate_of(code)))
            p.name(id, found.title, "ar", "official")
            if (year, code) in district_shape:
                shaped(id, district_shape[(year, code)])
        for code, title in SECTORS.items():
            sector = page(year, _sector_stage(year, code), code)
            assert sector is not None
            id = sector_area(year, code)
            p.add("area", id=id, scheme_code="eg.sector")
            p.add("area_code", area_id=id, code=f"{year}:{code}")
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            p.name(id, sector.title, "ar", "official")
            p.name(id, title, "en", "english", False)
            shaped(id, shapely.union_all([governorate_shapes[g] for g in SECTOR_GOVERNORATES[code]]))
    for scheme in ("eg.constituency", "eg.sector"):
        for year in YEARS:
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, year),
                scheme_code=scheme,
                vintage=str(year),
                as_of=FIRST_DAY[year],
            )
    return p


def _governorate_names() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        for stage in STAGES:
            try:
                text = raw.cached(SOURCE, archive_url(year, stage)).path.read_text("utf-8")
            except LookupError:
                continue
            for code, name in re.findall(r"<option value='(\d+)'>([^<]+)</option>", text):
                if code in GOVERNORATES:
                    found.setdefault(code, clean(name))
    missing = set(GOVERNORATES) - set(found)
    if missing:
        raise LookupError(f"no Arabic name for governorates {sorted(missing)}")
    return found


def _sector_stage(year: int, code: str) -> str:
    for stage in ("stage_1", "stage_2"):
        if page(year, f"{stage}_round_1", code) is not None:
            return f"{stage}_round_1"
    raise LookupError(f"{year}: no page for list district {code}")


SOURCE_ID = key("source", SOURCE)
BODY = key("body", "house")
SYSTEM_INDIVIDUAL = key("electoral_system", "individual")
SYSTEM_LIST = key("electoral_system", "list")


def party_name(text: str) -> str:
    text = clean(text)
    return text.removeprefix("حزب ").strip()


def party_id(text: str) -> str | None:
    name = party_name(text)
    return None if name in ("", "-", INDEPENDENT) else key("party", name)


def person_key(text: str) -> str:
    return clean(text)


def party_names() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        for stage, code in stage_codes(year):
            final = page(year, f"{stage}_round_2", code)
            for row in final.winners if final else []:
                found.setdefault(party_name(row[2]), clean(row[2]))
        for code in SECTORS:
            for stage in ("stage_1", "stage_2"):
                found_page = page(year, f"{stage}_round_1", code)
                for member in found_page.members if found_page else []:
                    found.setdefault(party_name(member[2]), clean(member[2]))
    found.pop(INDEPENDENT, None)
    found.pop("", None)
    found.pop("-", None)
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry", SOURCE, "Egypt's House of Representatives, its parties, and its electoral systems"
    )
    p.external.add(EGYPT)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="الهيئة الوطنية للانتخابات (National Elections Authority)",
        title="House of Representatives results, elections.eg",
        url="https://www.elections.eg/",
        license="Published by the authority with no license stated",
        license_url="https://www.elections.eg/",
        attribution="National Elections Authority, Egypt",
    )
    for year in YEARS:
        for stage in STAGES:
            for url in (archive_url(year, stage),):
                try:
                    p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
                except LookupError:
                    pass
    p.add("body", id=BODY, polity_id=EGYPT, kind="lower_house")
    p.name(BODY, "House of Representatives", "en", "official")
    p.name(BODY, "مجلس النواب", "ar", "native", False)
    for id, rules, text in (
        (
            SYSTEM_INDIVIDUAL,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "Individual seats are filled in constituencies of one or more seats. A candidate who wins more than half "
                "of the valid votes is elected in the first round; the remaining seats go to a runoff among the leading "
                "candidates, in a number twice the seats left, and the candidates with the most votes win."
            ),
        ),
        (
            SYSTEM_LIST,
            {"formula": "winner_take_all"},
            (
                "List seats are filled in four large closed-list districts. The list that wins more than half of the "
                "valid votes takes every seat of its district."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=EGYPT,
            valid_from=date(2020, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for name, full in sorted(party_names().items()):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=EGYPT)
        p.add("external_id", entity_id=id, scheme="eg.nea.party", value=name)
        p.name(id, name, "ar", "official")
    return p


def house(year: int) -> Package:
    first = FIRST_DAY[year]
    p = Package(
        f"{SOURCE}/hor-{year}",
        SOURCE,
        f"The {year} Egyptian House of Representatives election by constituency, with its runoffs",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {EGYPT, COUNTRY}
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=EGYPT,
        kind="general",
        date_from=first,
        date_to=None,
        date_precision="month",
        status="held",
    )
    p.name(election, f"{year} Egyptian parliamentary election", "en", "official")
    p.name(election, f"انتخابات مجلس النواب {year}", "ar", "native", False)
    path = f"eg/{first}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)
    known = party_names()
    people: dict[str, str] = {}

    def person(scope: str, name: str, nickname: str = "") -> str:
        id = key("person", year, scope, person_key(name))
        if id not in people.values():
            p.add("person", id=id)
            p.name(id, clean(name), "ar", "official")
            if nickname and nickname != "-" and nickname != clean(name):
                p.name(id, nickname, "ar", "ballot", False)
            people[id] = id
        return id

    def result_set(contest: str, tag: str) -> str:
        id = key("result_set", year, tag)
        p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=METHOD, lang="en")
        return id

    def unit(tag: str, title: str, area: str) -> str:
        id = key("unit", year, tag)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=EGYPT,
            scheme_code="eg.constituency",
            code=f"{year}:{tag}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(id, title, "ar", "official")
        return id

    counted: set[str] = set()

    def value(rs: str, unit_id: str, option: str, amount: int) -> None:
        counted.add(rs)
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit_id,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=amount,
        )

    tally: Counter[str] = Counter()
    for stage, code in stage_codes(year):
        one = page(year, f"{stage}_round_1", code)
        two = page(year, f"{stage}_round_2", code)
        assert two is not None
        if one is None:
            raise LookupError(f"{year} {stage} {code}: no first-round page")
        slug = f"{path}/{code}"
        area = constituency_area(year, code)
        final = {clean(row[0]): row for row in two.winners}
        round_one = {clean(row[1]): row for row in one.winners}
        runoff = {clean(row[1]): row for row in one.runoff}
        elected_later = set(final) - set(round_one)
        if not set(round_one) <= set(final) or elected_later - set(runoff):
            raise ValueError(f"{year} {code}: round 1 and final winners disagree")
        seats = len(final)
        if not final and not round_one and not runoff:
            print(f"{year} {code} {one.title}: no winners and no runoff published; no contest built", flush=True)
            continue
        if one.remaining is not None and len(round_one) + one.remaining != seats:
            raise ValueError(f"{year} {code}: {len(round_one)} winners and {one.remaining} seats left, not {seats}")
        previous: str | None = None
        for round_number in (1, 2):
            if round_number == 2 and not runoff:
                break
            contest = key("contest", year, code, round_number)
            suffix = "" if round_number == 1 else ", runoff"
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=BODY,
                district_area_id=area,
                magnitude=seats if round_number == 1 else len(elected_later),
                round_number=round_number,
                previous_round_id=previous,
                electoral_system_id=SYSTEM_INDIVIDUAL,
                default_result_kind="counted",
            )
            previous = contest
            p.name(contest, f"{year} Egyptian parliamentary election, {one.title}{suffix}", "en", "official")
            p.name(contest, f"{one.title}{' (الإعادة)' if round_number == 2 else ''}", "ar", "native", False)
            p.add("slug", path=slug + ("" if round_number == 1 else "-runoff"), entity_id=contest, is_current=True)
            rs = result_set(contest, f"{code}:{round_number}")
            unit_id = unit(f"{code}:{round_number}", one.title, area)
            entries = (
                [(name, "elected", row) for name, row in round_one.items()]
                + [(name, "advanced", row) for name, row in runoff.items()]
                if round_number == 1
                else [(name, "elected" if name in elected_later else "lost", row) for name, row in runoff.items()]
            )
            for position, (name, outcome, row) in enumerate(entries, 1):
                option = key("option", year, code, round_number, name)
                member = final.get(name)
                party = party_id(member[2]) if member else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=name,
                )
                candidacy = key("candidacy", year, code, round_number, name)
                independent = member is not None and party_name(member[2]) == INDEPENDENT
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person(code, name, member[1] if member else ""),
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party if party in {key("party", n) for n in known} else None,
                    is_independent=independent,
                )
                p.add("outcome", result_set_id=rs, option_id=option, kind=outcome)
                if round_number == 1 and name in round_one and len(row) == 3:
                    value(rs, unit_id, option, number(row[2]))
                    tally[f"votes{year}"] += number(row[2])
            tally["individual_seats"] += len(entries) if round_number == 1 else 0
    for code, title in SECTORS.items():
        stage = _sector_stage(year, code)
        one = page(year, stage, code)
        two = page(year, stage.replace("round_1", "round_2"), code)
        assert one is not None
        if two is not None and (
            two.list_name != one.list_name or two.members != one.members and len(two.members) != len(one.members)
        ):
            raise ValueError(f"{year} {code}: list winners differ between rounds")
        area = sector_area(year, code)
        contest = key("contest", year, code)
        seats = len(one.members)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODY,
            district_area_id=area,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Egyptian parliamentary election, {title}", "en", "official")
        p.name(contest, one.title, "ar", "native", False)
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        rs = result_set(contest, code)
        unit_id = unit(code, one.title, area)
        option = key("option", year, code, "list")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=1,
            ballot_label=one.list_name,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=seats)
        if one.list_votes is not None:
            against = key("option", year, code, "against")
            p.add(
                "contest_option",
                id=against,
                contest_id=contest,
                kind="none_of_the_above",
                ballot_position=2,
                ballot_label="الأصوات التي لم تنتخب القائمة",
            )
            p.add("outcome", result_set_id=rs, option_id=against, kind="lost")
            value(rs, unit_id, option, one.list_votes[0])
            value(rs, unit_id, against, one.list_votes[1])
            tally["list_for"] += one.list_votes[0]
            tally["list_against"] += one.list_votes[1]
        for member in one.members:
            name = member[1]
            candidacy = key("candidacy", year, code, "list", member[0])
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person(f"list{code}", name),
                option_id=option,
                role="list_member",
                list_position=int(member[0]),
                ballot_name=name,
                member_of_party_id=party_id(member[2]),
                is_independent=party_name(member[2]) == INDEPENDENT,
            )
            p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")
        tally["list_seats"] += seats
    for stage in STAGES:
        for code in (*(c for s, c in stage_codes(year) if s == stage[:7]), *SECTORS):
            if page(year, stage, code) is not None:
                p.uses(raw.cached(SOURCE, fragment_url(year, stage, code)))
    for row in p.rows["result_set"]:
        if row["id"] not in counted:
            row["completeness"] = None
    print(f"{p.key}: {dict(tally)}", flush=True)
    return p


SENATE_ROUNDS = ("stage_1_round_1", "stage_1_round_2")


def senate_archive_url(year: int, stage: str) -> str:
    return f"{BASE}/senate_{year}/results/{stage}_results/"


def senate_fragment_url(year: int, stage: str, code: str) -> str:
    return f"{BASE}/senate_{year}/results/{stage}_results/html/{code}.html"


def senate_codes() -> list[str]:
    """The 27 individual districts, one per governorate, in the order the archive pages list them."""
    return sorted(GOVERNORATES, key=int)


def senate_fetch() -> None:
    for year in YEARS:
        for stage in SENATE_ROUNDS:
            try:
                raw.fetch(SOURCE, senate_archive_url(year, stage), suffix=".html")
            except Exception as failure:
                print(f"{senate_archive_url(year, stage)}: {failure}", file=sys.stderr, flush=True)
    urls = [
        senate_fragment_url(year, stage, code)
        for year in YEARS
        for stage in SENATE_ROUNDS
        for code in (*senate_codes(), *LISTS)
    ]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=4)


SENATE_FIRST_DAY = {2020: date(2020, 8, 9), 2025: date(2025, 8, 1)}
SENATE_BODY = key("body", "senate")
SENATE_METHOD = (
    "Published by the National Elections Authority as a results page for each district: one individual district for "
    "each governorate and four list districts. The pages name the winners and the candidates sent to a runoff, and a "
    "list district's winning list with its candidates. They do not name candidates who lost in the first round. The "
    "2020 pages carry each district's registered voters, ballots cast, valid, and invalid ballots but no candidate "
    "votes; the 2025 first-round pages carry the votes of the individual-seat winners and the votes for and against "
    "the winning list but no turnout. Runoff candidates carry no votes. The seats the President appoints are not "
    "part of this election."
)
SENATE_STATS = {"registered": 2, "cast": 3, "valid": 4, "invalid": 5}


def senate_page(year: int, stage: str, code: str) -> Page | None:
    try:
        text = raw.cached(SOURCE, senate_fragment_url(year, stage, code)).path.read_text("utf-8")
    except LookupError:
        return None
    return parse_fragment(text, f"{year} {stage} {code}")


def senate_stats(year: int, code: str) -> tuple[int | None, dict[int, int]]:
    """The seat count and the accounting figures printed above the winners on a first-round page, when it has them."""
    text = raw.cached(SOURCE, senate_fragment_url(year, "stage_1_round_1", code)).path.read_text("utf-8")
    head = re.split(r'<h3 class="winner_header red">', text)[0]
    labels = [clean(m) for m in re.findall(r'<p class="enteries">(.*?)</p>', head, re.DOTALL)]
    seats: int | None = None
    figures: dict[int, int] = {}
    for line in labels:
        match = re.search(r"([\d,]+)", line.split(":")[-1] if "عدد المقاعد" in line else line)
        if match is None:
            continue
        if "عدد المقاعد" in line:
            seats = number(match.group(1))
        elif "المقيدين" in line:
            figures[2] = number(match.group(1))
        elif "الحضور" in line:
            figures[3] = number(match.group(1))
        elif "الصحيحة" in line:
            figures[4] = number(match.group(1))
        elif "الباطله" in line:
            figures[5] = number(match.group(1))
    return seats, figures


def senate_party_names() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        for stage in SENATE_ROUNDS:
            for code in (*senate_codes(), *LISTS):
                found_page = senate_page(year, stage, code)
                if found_page is None:
                    continue
                for row in found_page.members:
                    found.setdefault(party_name(row[2]), clean(row[2]))
                if stage.endswith("round_2"):
                    for row in found_page.winners:
                        found.setdefault(party_name(row[2]), clean(row[2]))
    for name in (INDEPENDENT, "", "-"):
        found.pop(name, None)
    return found


def senate_registry() -> Package:
    p = Package(
        f"{SOURCE}/senate-registry",
        SOURCE,
        "Egypt's Senate and the parties that appear only in its results",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(EGYPT)
    for year in YEARS:
        for stage in SENATE_ROUNDS:
            try:
                p.artifact(raw.cached(SOURCE, senate_archive_url(year, stage)), SOURCE_ID)
            except LookupError:
                pass
    p.add("body", id=SENATE_BODY, polity_id=EGYPT, kind="upper_house")
    p.name(SENATE_BODY, "Senate", "en", "official")
    p.name(SENATE_BODY, "مجلس الشيوخ", "ar", "native", False)
    existing = set(party_names())
    for name in sorted(set(senate_party_names()) - existing):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=EGYPT)
        p.add("external_id", entity_id=id, scheme="eg.nea.party", value=name)
        p.name(id, name, "ar", "official")
    return p


def senate(year: int) -> Package:
    first = SENATE_FIRST_DAY[year]
    p = Package(
        f"{SOURCE}/sen-{year}",
        SOURCE,
        f"The {year} Egyptian Senate election by district, with its runoffs",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/senate-registry"],
    )
    p.external |= {EGYPT, COUNTRY}
    election = key("election", "senate", year)
    p.add(
        "election",
        id=election,
        polity_id=EGYPT,
        kind="general",
        date_from=first,
        date_to=None,
        date_precision="month",
        status="held",
    )
    p.name(election, f"{year} Egyptian Senate election", "en", "official")
    p.name(election, f"انتخابات مجلس الشيوخ {year}", "ar", "native", False)
    path = f"eg/{first}-senate"
    p.add("slug", path=path, entity_id=election, is_current=True)
    known = {key("party", name) for name in (*party_names(), *senate_party_names())}
    people: set[str] = set()

    def person(scope: str, name: str, nickname: str = "") -> str:
        id = key("person", "senate", year, scope, person_key(name))
        if id not in people:
            people.add(id)
            p.add("person", id=id)
            p.name(id, clean(name), "ar", "official")
            if nickname and nickname != "-" and nickname != clean(name):
                p.name(id, nickname, "ar", "ballot", False)
        return id

    def party_of(text: str) -> str | None:
        id = party_id(text)
        return id if id in known else None

    counted: set[str] = set()

    def value(rs: str, unit_id: str, option: str | None, measure: int, amount: int) -> None:
        counted.add(rs)
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit_id,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=amount,
        )

    def result_set(contest: str, tag: str) -> str:
        id = key("result_set", "senate", year, tag)
        p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=SENATE_METHOD, lang="en")
        return id

    def unit(tag: str, title: str, area: str, scheme: str) -> str:
        id = key("unit", "senate", year, tag)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=EGYPT,
            scheme_code=scheme,
            code=f"senate-{year}:{tag}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(id, title, "ar", "official")
        return id

    tally: Counter[str] = Counter()
    for code in senate_codes():
        one = senate_page(year, "stage_1_round_1", code)
        two = senate_page(year, "stage_1_round_2", code)
        if one is None:
            raise LookupError(f"{year} {code}: no first-round page")
        area = governorate_area(code)
        final = {clean(row[0]): row for row in two.winners} if two else {}
        round_one = {clean(row[1]): row for row in one.winners}
        runoff = {clean(row[1]): row for row in one.runoff}
        stated, figures = senate_stats(year, code)
        seats = len(final) if two else stated
        if seats is None:
            raise ValueError(f"{year} {code}: no seat count")
        if two:
            elected_later = set(final) - set(round_one)
            if not set(round_one) <= set(final) or elected_later - set(runoff):
                raise ValueError(f"{year} {code}: round 1 and final winners disagree")
        else:
            elected_later = set()
        if stated is not None and stated != seats:
            raise ValueError(f"{year} {code}: {stated} seats stated, {seats} found")
        if len(round_one) + (one.remaining or 0) != seats:
            raise ValueError(f"{year} {code}: {len(round_one)} winners and {one.remaining} left, not {seats}")
        previous: str | None = None
        for round_number in (1, 2):
            if round_number == 2 and not (runoff and two):
                break
            contest = key("contest", "senate", year, code, round_number)
            suffix = "" if round_number == 1 else ", runoff"
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=SENATE_BODY,
                district_area_id=area,
                magnitude=seats if round_number == 1 else len(elected_later),
                round_number=round_number,
                previous_round_id=previous,
                electoral_system_id=SYSTEM_INDIVIDUAL,
                default_result_kind="counted",
            )
            previous = contest
            p.name(contest, f"{year} Egyptian Senate election, {one.title}{suffix}", "en", "official")
            p.name(contest, f"{one.title}{' (الإعادة)' if round_number == 2 else ''}", "ar", "native", False)
            p.add(
                "slug",
                path=f"{path}/{code}" + ("" if round_number == 1 else "-runoff"),
                entity_id=contest,
                is_current=True,
            )
            rs = result_set(contest, f"{code}:{round_number}")
            unit_id = unit(f"{code}:{round_number}", one.title, area, "eg.governorate")
            if round_number == 1:
                for measure, amount in figures.items():
                    value(rs, unit_id, None, measure, amount)
                entries = [(n, "elected", r) for n, r in round_one.items()] + [
                    (n, "advanced", r) for n, r in runoff.items()
                ]
            else:
                entries = [(n, "elected" if n in elected_later else "lost", r) for n, r in runoff.items()]
            for position, (name, outcome, row) in enumerate(entries, 1):
                option = key("option", "senate", year, code, round_number, name)
                member = final.get(name)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=name,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", "senate", year, code, round_number, name),
                    person_id=person(code, name, member[1] if member else ""),
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party_of(member[2]) if member else None,
                    is_independent=member is not None and party_name(member[2]) == INDEPENDENT,
                )
                p.add("outcome", result_set_id=rs, option_id=option, kind=outcome)
                if round_number == 1 and name in round_one and len(row) == 3:
                    value(rs, unit_id, option, 1, number(row[2]))
                    tally[f"votes{year}"] += number(row[2])
            if round_number == 1:
                tally["individual_seats"] += seats
                for measure, amount in figures.items():
                    tally[f"measure{measure}"] += amount
    for code, title in SECTORS.items():
        one = senate_page(year, "stage_1_round_1", code)
        two = senate_page(year, "stage_1_round_2", code)
        if one is None:
            raise LookupError(f"{year} {code}: no list page")
        if two is not None and (two.list_name != one.list_name or len(two.members) != len(one.members)):
            raise ValueError(f"{year} {code}: list winners differ between rounds")
        house_sector = page(year, _sector_stage(year, code), code)
        if house_sector is None or one.title != house_sector.title:
            raise ValueError(f"{year} {code}: title differs from the House list district")
        area = sector_area(year, code)
        contest = key("contest", "senate", year, code)
        seats = len(one.members)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SENATE_BODY,
            district_area_id=area,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Egyptian Senate election, {title}", "en", "official")
        p.name(contest, one.title, "ar", "native", False)
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        rs = result_set(contest, code)
        unit_id = unit(code, one.title, area, "eg.sector")
        _, figures = senate_stats(year, code)
        for measure, amount in figures.items():
            value(rs, unit_id, None, measure, amount)
            tally[f"list_measure{measure}"] += amount
        option = key("option", "senate", year, code, "list")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=1,
            ballot_label=one.list_name,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=seats)
        if one.list_votes is not None:
            against = key("option", "senate", year, code, "against")
            p.add(
                "contest_option",
                id=against,
                contest_id=contest,
                kind="none_of_the_above",
                ballot_position=2,
                ballot_label="الأصوات التي لم تنتخب القائمة",
            )
            p.add("outcome", result_set_id=rs, option_id=against, kind="lost")
            value(rs, unit_id, option, 1, one.list_votes[0])
            value(rs, unit_id, against, 1, one.list_votes[1])
            tally["list_for"] += one.list_votes[0]
            tally["list_against"] += one.list_votes[1]
        for member in one.members:
            candidacy = key("candidacy", "senate", year, code, "list", member[0])
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person(f"list{code}", member[1]),
                option_id=option,
                role="list_member",
                list_position=int(member[0]),
                ballot_name=member[1],
                member_of_party_id=party_of(member[2]),
                is_independent=party_name(member[2]) == INDEPENDENT,
            )
            p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")
        tally["list_seats"] += seats
    for stage in SENATE_ROUNDS:
        for code in (*senate_codes(), *LISTS):
            try:
                p.uses(raw.cached(SOURCE, senate_fragment_url(year, stage, code)))
            except LookupError:
                pass
    for row in p.rows["result_set"]:
        if row["id"] not in counted:
            row["completeness"] = None
    print(f"{p.key}: {dict(tally)}", flush=True)
    if year == 2020:
        p.accept(
            "accounting.votes_valid",
            "The 2020 pages print each single-seat district's valid ballots but not the winner's votes, "
            "so no candidate votes exist to match them.",
        )
    return p


# The presidential elections, announced as a national page for 2018 and a national page plus a fragment for each
# governorate for 2024 (the vote held on 10 to 12 December 2023).

PRES_PAGE = {
    2018: "https://www.elections.eg/archive/pres-2018/pres-results-2018",
    2024: "https://www.elections.eg/archive/pres-2024/pres-2024-result",
}
PRES_BY_GOV = "https://www.elections.eg/archive/pres-2024/pres-2024-result-by-gov"
PRES_FRAGMENT = "https://www.elections.eg/static/pres_2024_result_by_gov/data/{}.html"


def presidential_codes() -> list[str]:
    text = raw.cached(SOURCE, PRES_BY_GOV).path.read_text("utf-8")
    return [code for code in re.findall(r"<option value='(\w+)'>", text) if code != "EG"]


def presidential_fetch() -> None:
    for url in (*PRES_PAGE.values(), PRES_BY_GOV):
        raw.fetch(SOURCE, url, suffix=".html")
    raw.fetch_many(
        SOURCE, [PRES_FRAGMENT.format(code) for code in ("EG", *presidential_codes())], suffix=".html", workers=4
    )


PRES_YEARS = (2018, 2024)
PRES_DAYS = {2018: (date(2018, 3, 16), date(2018, 3, 28)), 2024: (date(2023, 12, 1), date(2023, 12, 12))}
PRES_VOTE_DAY = {2018: date(2018, 3, 26), 2024: date(2023, 12, 10)}
PRES_NAMES = {
    2018: "2018 Egyptian presidential election",
    2024: "2023 Egyptian presidential election",
}
PRES_ARABIC = {2018: "انتخابات رئاسة الجمهورية 2018", 2024: "انتخابات رئاسة الجمهورية 2023"}
PRES_ENGLISH = {
    "عبد الفتاح سعيد حسين خليل السيسي": "Abdel Fattah el-Sisi",
    "موسي مصطفي موسي محمد": "Mostafa Moussa Mostafa",
    "حازم محمد سليمان محمد عمر": "Hazem Omar",
    "محمد فريد سعد زهران": "Farid Zahran",
    "عبد السند حسن محمد يمامه": "Abdel-Sanad Yamama",
}
PRES_SYSTEM = key("electoral_system", "president")
PRESIDENCY = key("office", "president")
PRES_METHOD = (
    "Published by the National Elections Authority as a national results page{gov}. Each page prints the valid votes "
    "of every candidate{figures}. The pages print no polling-station results. The authority names the candidate "
    "with the most votes as the winner."
)


@dataclass
class PresPage:
    figures: dict[str, int]
    votes: dict[str, int]


def pres_clean(text: str) -> str:
    return " ".join(clean(text).replace("ـ", "").split())


def pres_number(text: str) -> int:
    return number(text.strip())


def pres_parse(year: int, code: str | None = None) -> PresPage:
    """The turnout figures and the candidates' votes of a national page or governorate fragment, in print order."""
    span = r"<span>(?!\s*نسبة)(.*?)</span>\s*([\d,]+)\s*</p>"
    if code is None and year == 2018:
        text = raw.cached(SOURCE, PRES_PAGE[year]).path.read_text("utf-8").split("<body>", 1)[1].split("</body>", 1)[0]
        rows = [
            (pres_clean(label), pres_number(value))
            for label, value in re.findall(r"<p>\s*([^<\d]*?)\s*([\d,]{4,})\s*</p>", text)
        ]
        rows += [
            (pres_clean(label), pres_number(value))
            for value, label in re.findall(r"<p>\s*([\d,]{4,})\s*([^<\d]+?)\s*</p>", text)
        ]
        rows = [(label, value) for label, value in rows if label]
    else:
        url = PRES_PAGE[year] if code is None else PRES_FRAGMENT.format(code)
        text = raw.cached(SOURCE, url).path.read_text("utf-8")
        if code is None:
            text = text.split("statsic-2018", 1)[1]
        text = text.split("<script", 1)[0]
        rows = [(pres_clean(label), pres_number(value)) for label, value in re.findall(span, text, re.DOTALL)]
    labels = {
        "عدد المسجلين": "registered",
        "عدد الحضور": "cast",
        "عدد الأصوات الباطلة": "invalid",
        "عدد الأصوات الصحيحة": "valid",
    }
    figures = {labels[label]: value for label, value in rows if label in labels}
    votes = {label: value for label, value in rows if label not in labels}
    return PresPage(figures, votes)


def pres_chart(year: int) -> dict[str, int]:
    """The candidates and votes in the chart data of the national page, which gives each name its own pair."""
    text = raw.cached(SOURCE, PRES_PAGE[year]).path.read_text("utf-8")
    block = re.search(r"arrayToDataTable\(\[\[.*?\](.*?)\]\);", text, re.DOTALL)
    if block is None:
        raise ValueError(f"{year}: no chart data")
    return {pres_clean(name): int(count) for name, count in re.findall(r'\["([^"]+)",\s*(\d+)\]', block.group(1))}


def pres_national(year: int) -> PresPage:
    found = pres_parse(year)
    chart = pres_chart(year)
    if found.votes != chart:
        raise ValueError(f"{year}: the page text and chart disagree: {found.votes} against {chart}")
    return found


PRES_MEASURES = {"registered": 2, "cast": 3, "valid": 4, "invalid": 5}


def presidential_source() -> dict[str, object]:
    return {
        "id": key("source", "president"),
        "kind": "official_body",
        "publisher": "الهيئة الوطنية للانتخابات (National Elections Authority)",
        "title": "Presidential election results, elections.eg",
        "url": "https://www.elections.eg/",
        "license": "Published by the authority with no license stated",
        "license_url": "https://www.elections.eg/",
        "attribution": "National Elections Authority, Egypt",
    }


def pres_registry() -> Package:
    p = Package(
        f"{SOURCE}/pres-registry",
        SOURCE,
        "Egypt's presidency and the electoral system of its direct election",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(EGYPT)
    p.add("scheme", code="eg.nation", polity_id=EGYPT, kind="administrative", rank=0, noun="nation")
    p.add("office", id=PRESIDENCY, polity_id=EGYPT, kind="head_of_state")
    p.name(PRESIDENCY, "President of Egypt", "en", "official")
    p.name(PRESIDENCY, "رئيس جمهورية مصر العربية", "ar", "official", False)
    p.add(
        "electoral_system",
        id=PRES_SYSTEM,
        polity_id=EGYPT,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", PRES_SYSTEM),
        entity_id=PRES_SYSTEM,
        kind="methodology",
        body_md="The President is elected directly. A candidate with more than half the valid votes wins in the "
        "first round; otherwise the two leading candidates face a runoff.",
        lang="en",
    )
    return p


def presidential(year: int) -> Package:
    p = Package(
        f"{SOURCE}/pres-{year}",
        SOURCE,
        f"The {PRES_NAMES[year]}, nationally" + (" and by governorate" if year == 2024 else ""),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/pres-registry"],
    )
    p.external |= {EGYPT, COUNTRY}
    source = str(presidential_source()["id"])
    p.add("source", **{**presidential_source(), "title": f"{PRES_NAMES[year]} results, elections.eg"})
    page_url = PRES_PAGE[year]
    p.artifact(raw.cached(SOURCE, page_url), source)
    national = pres_national(year)
    by_gov: dict[str, PresPage] = {}
    if year == 2024:
        p.artifact(raw.cached(SOURCE, PRES_BY_GOV), source)
        p.artifact(raw.cached(SOURCE, PRES_FRAGMENT.format("EG")), source)
        for code in presidential_codes():
            p.artifact(raw.cached(SOURCE, PRES_FRAGMENT.format(code)), source)
            by_gov[code] = pres_parse(year, code)
        everywhere = pres_parse(year, "EG")
        if everywhere.figures != national.figures or everywhere.votes != national.votes:
            raise ValueError("2024: the national page and the all-governorates fragment disagree")
        if set(by_gov) != set(GOVERNORATES):
            raise ValueError(f"2024: governorate fragments {sorted(by_gov)} differ from the 27 governorates")
        for name in national.votes:
            if any(set(g.votes) != set(national.votes) for g in by_gov.values()):
                raise ValueError("2024: a governorate lists different candidates")
        for label in (*national.votes, *PRES_MEASURES):
            total = sum(g.votes.get(label, g.figures.get(label, 0)) for g in by_gov.values())
            expected = national.votes.get(label, national.figures.get(label))
            if label != "registered" and total != expected:
                print(f"{year}: {label}: governorates add to {total}, nationally {expected}", flush=True)
    stated = national.figures
    if stated["valid"] + stated["invalid"] != stated["cast"] or sum(national.votes.values()) != stated["valid"]:
        raise ValueError(f"{year}: the national figures do not add up")
    election = key("election", "president", year)
    first, last = PRES_DAYS[year]
    p.add(
        "election",
        id=election,
        polity_id=EGYPT,
        kind="general",
        date_from=first,
        date_to=last,
        status="held",
    )
    p.name(election, PRES_NAMES[year], "en", "official")
    p.name(election, PRES_ARABIC[year], "ar", "native", False)
    path = f"eg/{PRES_VOTE_DAY[year]}-presidential"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", "president", year)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=COUNTRY,
        magnitude=1,
        round_number=1,
        electoral_system_id=PRES_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, PRES_NAMES[year], "en", "official")
    p.name(contest, PRES_ARABIC[year], "ar", "native", False)
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    rs = key("result_set", "president", year)
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    gov = " and a page for each of the 27 governorates" if year == 2024 else ""
    figures = ", and the registered voters, ballots cast, valid, and invalid ballots of the nation" + (
        ""
        if year == 2018
        else ", and for each governorate the ballots cast, valid, and invalid ballots, but not its registered voters"
    )
    p.add(
        "note",
        id=key("note", rs),
        entity_id=rs,
        kind="methodology",
        body_md=PRES_METHOD.format(gov=gov, figures=figures),
        lang="en",
    )
    options: dict[str, str] = {}
    winner = max(national.votes, key=lambda name: national.votes[name])
    for position, name in enumerate(national.votes, 1):
        english = PRES_ENGLISH.get(name)
        if english is None:
            raise KeyError(f"no English name for candidate {name}")
        person = key("person", "president", year, name)
        p.add("person", id=person, gender="male")
        p.name(person, name, "ar", "official")
        p.name(person, english, "en", "english", False)
        option = options[name] = key("option", "president", year, name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", "president", year, name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="elected" if name == winner else "lost")
        p.add("declared", result_set_id=rs, option_id=option, measure=1, value=national.votes[name])
    for label, measure in PRES_MEASURES.items():
        p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=stated[label])
    values: list[dict[str, object]] = []

    def put(unit: str, option: str | None, measure: int, amount: int) -> None:
        values.append(
            {
                "result_set_id": rs,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": 0,
                "value": amount,
            }
        )

    def unit(tag: str, scheme: str, title: str, area: str, english: str | None = None) -> str:
        id = key("unit", "president", year, tag)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=EGYPT,
            scheme_code=scheme,
            code=f"president-{year}:{tag}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(id, title, "ar", "official")
        if english:
            p.name(id, english, "en", "english", False)
        return id

    if year == 2024:
        names = dict(
            re.findall(
                r"<option value='(\d+)'>([^<]+)</option>", raw.cached(SOURCE, PRES_BY_GOV).path.read_text("utf-8")
            )
        )
        for code, found in sorted(by_gov.items(), key=lambda item: int(item[0])):
            title = pres_clean(names[code])
            u = unit(code, "eg.governorate", title, governorate_area(code))
            for label, measure in PRES_MEASURES.items():
                if label in found.figures:
                    put(u, None, measure, found.figures[label])
            for name, option in options.items():
                put(u, option, 1, found.votes[name])
    else:
        u = unit("EG", "eg.nation", "جمهورية مصر العربية", COUNTRY, "Egypt")
        for label, measure in PRES_MEASURES.items():
            put(u, None, measure, stated[label])
        for name, option in options.items():
            put(u, option, 1, national.votes[name])
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None))
    if year == 2024:
        p.accept(
            "accounting.declared",
            "The authority prints the 67,032,438 registered voters only for the nation; its governorate fragments "
            "carry no registered voters, so no governorate figure exists to add up to the national roll.",
        )
    print(f"{p.key}: {len(values)} values; winner {PRES_ENGLISH[winner]} {national.votes[winner]}", flush=True)
    return p
