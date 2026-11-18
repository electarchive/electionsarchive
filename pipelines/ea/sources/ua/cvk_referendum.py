"""Ukraine's all-Ukrainian referendum of 16 April 2000 from the Central Election Commission's results site.

The site is a static copy of the commission's old information system. It prints the four ballot questions' results per
oblast (27 regions: the Autonomous Republic of Crimea, 24 oblasts, and the cities of Kyiv and Sevastopol) and for the
country. Its commission pages list the district commissions but print no district vote counts, so the oblast is the
finest reporting unit.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import date
from functools import cache
from html import unescape
from urllib.parse import urljoin
from uuid import uuid5

import shapely
from shapely.geometry import shape

from ea import package, raw
from ea.package import Package
from ea.sources.ua.cvk_old import ISO

SOURCE = "ua.cvk_referendum"
BASE = "https://www.cvk.gov.ua/pls/r001"
ROOT = f"{BASE}/webproc17r3b6b.html?kodvib=300"
DAY = date(2000, 4, 16)
DELAY = 0.4

SHAPES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/UKR/ADM1/"
    "geoBoundaries-UKR-ADM1.geojson"
)

UKRAINE = str(uuid5(package.NAMESPACE, "polity\x1fUA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fUKR"))
REGION_SCHEME = "ua.region_referendum"
VOTES, REGISTERED, CAST, INVALID, RECEIVED = 1, 2, 3, 5, 14


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)


# ---------------------------------------------------------------------------------------------------------------------
# Fetching


def cached(url: str) -> raw.Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return None


def get(url: str) -> raw.Artifact:
    found = cached(url)
    if found is not None:
        return found
    time.sleep(DELAY)
    return raw.fetch(SOURCE, url, suffix=".html")


def text(artifact: raw.Artifact) -> str:
    return artifact.path.read_bytes().decode("windows-1251", errors="replace")


def links(url: str, pattern: str) -> list[str]:
    """The absolute links on a cached page that match the pattern, in page order and without repeats."""
    artifact = cached(url)
    if artifact is None:
        raise LookupError(f"{url} is not in the cache")
    found = (
        unescape(urljoin(url, href)) for href in re.findall(r'href\s*=\s*"([^"]+)"', text(artifact), re.IGNORECASE)
    )
    return list(dict.fromkeys(link for link in found if re.search(pattern, link)))


def region_pages() -> dict[int, str]:
    """Each region's summary page by the commission's region code, from the by-region page."""
    found: dict[int, str] = {}
    (index,) = links(ROOT, r"webproc46r")
    get(index)
    for link in links(index, r"webproc49r[\w-]+\.html\?kodvib=300&kodreg=\d+$"):
        found[int(link.rsplit("=", 1)[1])] = link
    return found


def fetch() -> None:
    if cached(SHAPES) is None:
        raw.fetch(SOURCE, SHAPES, suffix=".geojson")
    get(ROOT)
    for link in links(ROOT, r"webproc(?:21|46)r"):
        get(link)
    for code, link in region_pages().items():
        get(link)
        for question in links(link, r"webproc56r[\w-]+\.html\?kodvib=300&kodpit=\d+&kodreg=\d+$"):
            get(question)
        print(f"{SOURCE}: region {code} fetched", flush=True)
    for link in links(ROOT, r"webproc21r"):
        for by_region in links(link, r"webproc97r"):
            get(by_region)


def pages() -> list[str]:
    found = [ROOT, *links(ROOT, r"webproc(?:21|46)r")]
    for link in links(ROOT, r"webproc21r"):
        found += links(link, r"webproc97r")
    for link in region_pages().values():
        found.append(link)
        found += links(link, r"webproc56r[\w-]+\.html\?kodvib=300&kodpit=\d+&kodreg=\d+$")
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------------------------------------------------
# Parsing


def flat(artifact: raw.Artifact) -> list[str]:
    """A page's text lines, without markup, scripts, or empty lines."""
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>|<!--.*?-->", " ", text(artifact))
    body = re.sub(r"(?i)<br\s*/?>|</?(?:tr|p|table|div)\b[^>]*>", "\n", body)
    body = unescape(re.sub(r"<[^>]*>", " ", body)).replace("\xa0", " ")
    return [line for raw_line in body.splitlines() if (line := " ".join(raw_line.split()))]


def number(value: str) -> int:
    return int(re.sub(r"\s", "", value))


@dataclass(frozen=True)
class Tally:
    """The figures the site prints for one question in one place (a region or the country)."""

    registered: int
    received: int
    voted: int
    yes: int
    no: int
    invalid: int


def tally(url: str) -> tuple[str, str, Tally]:
    """A question page's text, the place's name, and its figures."""
    artifact = get_cached(url)
    lines = flat(artifact)
    joined = "\n".join(lines)

    def after(label: str) -> int:
        match = re.search(rf"{label}[^\d\n]*\n?\s*(\d[\d ]*)", joined)
        if match is None:
            raise ValueError(f"{url} prints no {label!r}")
        return number(match.group(1))

    question = ""
    place = ""
    for i, line in enumerate(lines):
        if re.fullmatch(r"\d\.", line) and i + 1 < len(lines):
            question = lines[i + 1]
            place = lines[i + 2]
            break
    return (
        question,
        place,
        Tally(
            after("право брати участь в голосуванні"),
            after("одержали бюлетені"),
            after("Голосувало:"),
            after('ЗА":'),
            after('ПРОТИ":'),
            after("Недійсних бюлетенів:"),
        ),
    )


def get_cached(url: str) -> raw.Artifact:
    artifact = cached(url)
    if artifact is None:
        raise LookupError(f"{url} is not in the cache")
    return artifact


@dataclass(frozen=True)
class Region:
    code: int
    name: str
    url: str
    questions: dict[int, Tally]


@cache
def national() -> tuple[dict[int, str], dict[int, Tally]]:
    """Each question's text and the country's figures for it."""
    texts: dict[int, str] = {}
    tallies: dict[int, Tally] = {}
    for link in links(ROOT, r"webproc21r[\w-]+\.html\?kodvib=300&kodpit=\d+$"):
        n = int(link.rsplit("=", 1)[1]) - 1
        question, _, found = tally(link)
        texts[n], tallies[n] = question, found
    if sorted(texts) != [1, 2, 3, 4]:
        raise ValueError(f"the country pages cover questions {sorted(texts)}")
    return texts, tallies


@cache
def regions() -> list[Region]:
    found: list[Region] = []
    for code, url in sorted(region_pages().items()):
        by_question: dict[int, Tally] = {}
        name = ""
        for link in links(url, r"webproc56r[\w-]+\.html\?kodvib=300&kodpit=\d+&kodreg=\d+$"):
            n = int(link.split("kodpit=")[1].split("&")[0]) - 1
            _, name, by_question[n] = tally(link)
        if sorted(by_question) != [1, 2, 3, 4]:
            raise ValueError(f"{url} covers questions {sorted(by_question)}")
        found.append(Region(code, name, url, by_question))
    return found


def area(code: object) -> str:
    return key("area", REGION_SCHEME, code)


def region_shapes() -> dict[str, shapely.Geometry]:
    """The outline of each first-level unit by its ISO 3166-2 code, as a multipolygon in SRID 4326."""
    found: dict[str, shapely.Geometry] = {}
    for feature in json.loads(get_cached(SHAPES).path.read_bytes())["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            geometry = shapely.make_valid(geometry)
        if geometry.geom_type == "Polygon":
            geometry = shapely.MultiPolygon([geometry])
        found[feature["properties"]["shapeISO"]] = shapely.set_srid(geometry, 4326)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 27 regions of Ukraine that reported the 2000 referendum, as the Central Election Commission lists them",
    )
    p.external.add(UKRAINE)
    p.add(
        "scheme", code=REGION_SCHEME, polity_id=UKRAINE, kind="administrative", rank=1, noun="region", code_system=None
    )
    outlines = region_shapes()
    shapes_id = key("source", "geoboundaries")
    p.add(
        "source",
        id=shapes_id,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries Open: Ukraine ADM1",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries, William & Mary geoLab",
        notes="The first-level units of Ukraine, built from OpenStreetMap boundaries (release of 2023-12-12).",
    )
    p.artifact(get_cached(SHAPES), shapes_id)
    p.add(
        "boundary_set",
        id=key("boundary_set", REGION_SCHEME, "2017"),
        scheme_code=REGION_SCHEME,
        vintage="2017",
        valid_from=date(1991, 12, 1),
        valid_to=None,
        as_of=DAY,
    )
    for region in regions():
        id = area(region.code)
        p.add("area", id=id, scheme_code=REGION_SCHEME)
        p.add("area_code", area_id=id, code=str(region.code))
        p.name(id, region.name, "uk", "official")
        outline = outlines[ISO[norm(region.name)]]
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=date(1991, 12, 1),
            valid_to=None,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=shapes_id,
        )
    return p


def norm(name: str) -> str:
    """A region's key in the ISO table: its name in lower case with the city prefix closed up."""
    return re.sub(r"^м\.\s*", "м.", name.strip().casefold())


METHOD = (
    "Counted by the Central Election Commission and published on its results site per region (the Autonomous Republic "
    "of Crimea, the oblasts, and the cities of Kyiv and Sevastopol), which is the finest level the site gives: its "
    "commission pages list the district commissions but print no district results. Registered voters are the voters "
    "on the roll, voters who received a ballot and voters who took part are as printed, and invalid ballots are those "
    "declared invalid. Votes for and against are as printed; the site prints no total of valid ballots, so none is "
    "stored."
)

LICENSE = "Free reuse with the source cited, as the commission's results sites state"


def election() -> Package:
    texts, totals = national()
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The four questions of the 16 April 2000 all-Ukrainian referendum by region from the Central Election "
        "Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {UKRAINE, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Центральна виборча комісія",
        title="All-Ukrainian referendum of 16 April 2000, Central Election Commission of Ukraine",
        url=BASE + "/webproc0r.html",
        license=LICENSE,
        license_url="https://www.cvk.gov.ua/",
        attribution="Центральна виборча комісія",
    )
    for url in pages():
        p.artifact(get_cached(url), SOURCE_ID)
    id = key("election", DAY)
    p.add("election", id=id, polity_id=UKRAINE, kind="referendum", date_from=DAY, date_to=DAY, status="held")
    p.name(id, "2000 Ukrainian referendum", "en", "official")
    p.name(id, "Всеукраїнський референдум 16 квітня 2000 року", "uk", "native", False)
    path = f"ua/{DAY}-referendum"
    p.add("slug", path=path, entity_id=id, is_current=True)
    units: dict[int, str] = {}
    for region in regions():
        units[region.code] = key("unit", DAY, region.code)
        p.add(
            "reporting_unit",
            id=units[region.code],
            polity_id=UKRAINE,
            scheme_code=REGION_SCHEME,
            code=f"{DAY}:{region.code}",
            area_relation="equals",
            area_id=area(region.code),
            kind="standard",
        )
    for n in (1, 2, 3, 4):
        contest = key("contest", DAY, n)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="ballot_question",
            district_area_id=COUNTRY,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"Question {n}", "en", "official")
        p.name(contest, f"{n} питання", "uk", "native", False)
        p.add("slug", path=f"{path}/question-{n}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="initiative")
        rs = key("result_set", DAY, n)
        p.add(
            "result_set",
            id=rs,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", rs),
            entity_id=rs,
            kind="methodology",
            body_md=f"The question on the ballot: {texts[n]} " + METHOD,
            lang="en",
        )
        yes, no = key("option", DAY, n, "yes"), key("option", DAY, n, "no")
        p.add("contest_option", id=yes, contest_id=contest, kind="answer", ballot_position=1, ballot_label="ЗА")
        p.add("contest_option", id=no, contest_id=contest, kind="answer", ballot_position=2, ballot_label="ПРОТИ")
        nation = totals[n]
        for option, amount in ((yes, nation.yes), (no, nation.no)):
            p.add("declared", result_set_id=rs, option_id=option, measure=VOTES, value=amount)
        for measure, amount in (
            (REGISTERED, nation.registered),
            (RECEIVED, nation.received),
            (CAST, nation.voted),
            (INVALID, nation.invalid),
        ):
            p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=amount)
        for region in regions():
            t = region.questions[n]
            for option, measure, amount in (
                (yes, VOTES, t.yes),
                (no, VOTES, t.no),
                (None, REGISTERED, t.registered),
                (None, RECEIVED, t.received),
                (None, CAST, t.voted),
                (None, INVALID, t.invalid),
            ):
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=units[region.code],
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=amount,
                )
    return p


def parse() -> None:
    geo().write()
    election().write()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/{DAY}"]


def emit() -> None:
    package.run("load", packages())
