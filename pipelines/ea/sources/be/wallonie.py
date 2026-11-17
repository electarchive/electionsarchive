"""Walloon municipal and provincial council elections 2006-2018 from the Walloon Region's results sites.

The Region published each election's results on its own site, with one page per commune, province district, and
canton. The 2018 site has numbered pages per entity (`resultats-chiffres`, `votes-nominatifs`), the 2012 site has
`results_tab` pages, and the 2006 site has `res<id>i.html` pages that its frames load. Each parser reduces its site to
the same `Page` and `Candidate` rows, and one builder writes every election.
"""

import functools
import io
import itertools
import re
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from urllib.parse import quote
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "be.wallonie"
HOST = "https://electionslocales.wallonie.be/resultats"
STATBEL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/Statistische%20sectoren/"
    "sh_statbel_statistical_sectors_3812_20230101.shp.zip"
)
DAYS = {2006: date(2006, 10, 8), 2012: date(2012, 10, 14), 2018: date(2018, 10, 14)}
QUERY = "﹖"  # the 2018 site writes `?` as this lookalike in its file names


@dataclass
class Row:
    number: str | None
    label: str
    votes: int
    seats: int | None


@dataclass
class Page:
    code: str
    name: str
    rows: list[Row]
    registered: int | None = None
    cast: int | None = None
    blank: int | None = None
    valid: int | None = None


@dataclass
class Candidate:
    number: str
    label: str
    position: int
    name: str
    votes: int
    elected: bool


@dataclass
class Entities:
    communes: dict[str, str] = field(default_factory=dict)
    districts: dict[str, str] = field(default_factory=dict)
    cantons: dict[str, str] = field(default_factory=dict)
    district_of: dict[str, str] = field(default_factory=dict)


def cells(row: str) -> list[str]:
    return [
        unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
        for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
    ]


def rows_of(html: str) -> list[list[str]]:
    return [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.DOTALL | re.IGNORECASE)]


def number(text: str) -> int | None:
    text = text.strip().replace(".", "").replace(" ", "")
    return int(text) if re.fullmatch(r"\d+", text) else None


SEEN: list[str] = []


def read(source: str, url: str, encoding: str = "utf-8") -> str:
    SEEN.append(url)
    return raw.cached(source, url).path.read_bytes().decode(encoding, errors="replace")


# ---- 2018 -------------------------------------------------------------------------------------------------------


def u18(page: str, **params: str) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{HOST}/2018/fr/{page}{quote(QUERY)}{query}.html"


def index18() -> Entities:
    found = Entities()
    council = read(SOURCE, u18("election", el="CG"))
    for code, name in re.findall(
        r"resultats-chiffres%EF%B9%96el=CG&amp;id=(CGM\d+)\.html'>.*?<dt class=\"definition__header\">([^<]*)", council
    ):
        found.communes[code] = unescape(name).strip()
    province = read(SOURCE, u18("election", el="PR"))
    for block in province.split('<h3 class="definition__title">')[1:]:
        title = unescape(block.split("<", 1)[0]).strip()
        for code, name in re.findall(
            r"resultats-chiffres%EF%B9%96el=PR&amp;id=(PR[DK]\d+)\.html'>.*?<dt class=\"definition__header\">([^<]*)",
            block,
        ):
            if code.startswith("PRD"):
                found.districts[code] = unescape(name).strip()
            elif code.startswith("PRK"):
                found.cantons[code] = unescape(name).strip()
                found.district_of[code] = title
    return found


def fetch18() -> None:
    raw.fetch(SOURCE, u18("election", el="CG"), suffix=".html")
    raw.fetch(SOURCE, u18("election", el="PR"), suffix=".html")
    found = index18()
    pages = [u18("resultats-chiffres", el="CG", id=c) for c in found.communes]
    pages += [u18("resultats-chiffres", el="PR", id=c) for c in [*found.districts, *found.cantons]]
    raw.fetch_many(SOURCE, pages, suffix=".html")
    lists = []
    for code in found.communes:
        html = read(SOURCE, u18("resultats-chiffres", el="CG", id=code))
        for party in dict.fromkeys(
            re.findall(rf"votes-nominatifs%EF%B9%96el=CG&amp;id={code}&amp;party=(\d+)\.html", html)
        ):
            lists.append(u18("votes-nominatifs", el="CG", id=code, party=party))
    raw.fetch_many(SOURCE, lists, suffix=".html")


def seats_of(text: str) -> int | None:
    found = re.match(r"(\d+)\s*\(", text)
    return int(found.group(1)) if found else None


def totals(table: list[list[str]], page: Page) -> None:
    labels = {
        "Inscrits": "registered",
        "Bulletins déposés": "cast",
        "Blancs et nuls": "blank",
        "Votes valables": "valid",
    }
    for first, second in itertools.pairwise(table):
        if first and first[0] in labels and second and second[0] == "Total":
            setattr(page, labels[first[0]], number(second[1]))


def page18(code: str, name: str, html: str) -> Page:
    table = rows_of(html)
    page = Page(code, name, [])
    for cell in table:
        if len(cell) >= 5 and cell[1] and cell[3] not in ("", "2018") and cell[0] != "Inscrits" and cell[1] != "Liste":
            votes = number(cell[3])
            if votes is None or not re.fullmatch(r"\d+", cell[0]):
                continue
            page.rows.append(Row(cell[0], cell[1], votes, seats_of(cell[-1]) if len(cell) >= 9 else None))
    totals(table, page)
    return page


def candidates18(html: str) -> list[Candidate]:
    found: list[Candidate] = []
    number_, label = "", ""
    for cell in rows_of(html):
        if len(cell) == 1 and (head := re.fullmatch(r"(\d+)\s+(.+)", cell[0])):
            number_, label = head.group(1), head.group(2)
        elif len(cell) >= 6 and re.fullmatch(r"\d+", cell[0]) and number_ and (votes := number(cell[2])) is not None:
            found.append(Candidate(number_, label, int(cell[0]), cell[1], votes, bool(cell[5])))
    return found


@dataclass
class Council:
    code: str
    name: str
    page: Page
    candidates: list[Candidate] = field(default_factory=list)
    units: list[tuple[str, str, Page]] = field(default_factory=list)
    group: str = ""


@dataclass
class Year:
    day: date
    communes: list[Council]
    districts: list[Council]
    urls: list[str] = field(default_factory=list)


def load18() -> Year:
    SEEN.clear()
    found = index18()
    communes = []
    for code, name in found.communes.items():
        html = read(SOURCE, u18("resultats-chiffres", el="CG", id=code))
        council = Council(code[3:], name, page18(code, name, html))
        for party in dict.fromkeys(
            re.findall(rf"votes-nominatifs%EF%B9%96el=CG&amp;id={code}&amp;party=(\d+)\.html", html)
        ):
            council.candidates += candidates18(read(SOURCE, u18("votes-nominatifs", el="CG", id=code, party=party)))
        communes.append(council)
    districts = []
    for code, name in found.districts.items():
        page = page18(code, name, read(SOURCE, u18("resultats-chiffres", el="PR", id=code)))
        council = Council(code[3:], name, page, group=code[3])
        for canton, canton_name in found.cantons.items():
            if found.district_of[canton].removeprefix("Cantons - District de ") == name and canton[3] == code[3]:
                council.units.append(
                    (
                        canton[3:],
                        canton_name,
                        page18(canton, canton_name, read(SOURCE, u18("resultats-chiffres", el="PR", id=canton))),
                    )
                )
        districts.append(council)
    return Year(DAYS[2018], communes, districts, list(dict.fromkeys(SEEN)))


# ---- 2012 -------------------------------------------------------------------------------------------------------

SMALL = {"de", "du", "des", "la", "le", "les", "en", "sur", "lez", "d", "l", "et", "aux", "au"}


def proper(text: str) -> str:
    """Title-case the upper-case place names of the 2012 and 2006 sites."""
    if not text.isupper():
        return text

    def word(match: re.Match[str]) -> str:
        found = match.group(0)
        return found.lower() if found.lower() in SMALL and match.start() else found.capitalize()

    return re.sub(r"[^\W\d_]+", word, text)


def u12(part: str, name: str) -> str:
    return (
        f"{HOST}/2012/fr/{part}/results/{name}.html"
        if name.startswith("results")
        else f"{HOST}/2012/fr/{part}/{name}.html"
    )


def page12(code: str, name: str, html: str) -> Page:
    page = Page(code, name, [])
    figures: list[int | None] = []
    waiting = False
    for cell in rows_of(html):
        cell = [c for c in cell if c]
        if len(cell) >= 7 and re.fullmatch(r"\d+", cell[0]) and not re.fullmatch(r"[\d.]+", cell[1]):
            votes = number(cell[2])
            if votes is not None:
                page.rows.append(Row(cell[0], cell[1], votes, seats_of(cell[7]) if len(cell) >= 8 else None))
        elif cell[:1] == ["2012"] and len(cell) in (2, 4):
            waiting = True
        elif waiting and cell and re.fullmatch(r"[\d.]+", cell[0]):
            figures.append(number(cell[0]))
            waiting = False
    if len(figures) >= 4:
        page.registered, page.cast, page.blank, page.valid = figures[:4]
    return page


def candidates12(rows: list[Row], html: str) -> list[Candidate]:
    label = ""
    found: list[Candidate] = []
    by_label = {folded(r.label): r for r in rows}
    for cell in rows_of(html):
        if len(cell) >= 3 and cell[2].startswith("Choisissez une liste"):
            label = cell[1]
        elif (
            len(cell) >= 9
            and cell[0] == ""
            and re.fullmatch(r"\d+", cell[1])
            and (votes := number(cell[3])) is not None
        ):
            row = by_label.get(folded(label))
            if row is not None and row.number is not None:
                rank = number(cell[8])
                found.append(
                    Candidate(
                        row.number,
                        row.label,
                        int(cell[1]),
                        cell[2],
                        votes,
                        rank is not None and rank <= (row.seats or 0),
                    )
                )
    return found


def index12() -> Entities:
    found = Entities()
    council = read(SOURCE, u12("com", "results_start"))
    for code, name in re.findall(r'results_graph_(CGM\d+)\.html" >([^<]*)', council):
        found.communes[code] = proper(unescape(name).strip())
    province = read(SOURCE, u12("pro", "results_start"))
    for block in province.split("<strong>Cantons - District de ")[1:]:
        title = unescape(block.split("<", 1)[0]).strip()
        for code, name in re.findall(r'results_graph_(PRK\d+)\.html" >([^<]*)', block):
            found.cantons[code] = proper(unescape(name).strip())
            found.district_of[code] = title
    for code, name in re.findall(r'results_graph_(PRD\d+)\.html" >([^<]*)', province):
        found.districts[code] = proper(unescape(name).strip())
    return found


def tab12(part: str, code: str) -> str:
    return f"{HOST}/2012/fr/{part}/results/results_tab_{code}.html"


def fetch12() -> None:
    raw.fetch(SOURCE, f"{HOST}/2012/fr/com/results/results_start.html", suffix=".html")
    raw.fetch(SOURCE, f"{HOST}/2012/fr/pro/results/results_start.html", suffix=".html")
    raw.fetch(SOURCE, f"{HOST}/2012/fr/com/preferred/preferred_top.html", suffix=".html")
    found = index12()
    pages = [tab12("com", c) for c in found.communes]
    pages += [tab12("pro", c) for c in [*found.districts, *found.cantons]]
    raw.fetch_many(SOURCE, pages, suffix=".html")
    top = read(SOURCE, f"{HOST}/2012/fr/com/preferred/preferred_top.html")
    first = [
        f"{HOST}/2012/fr/com/preferred/preferred_{c}_{party}.html"
        for c, party in re.findall(r"preferred_(CGM\d+)_(\d+)\.html", top)
    ]
    raw.fetch_many(SOURCE, first, suffix=".html")
    rest = []
    for url in first:
        code = url.split("preferred_")[1].split("_")[0]
        for link in re.findall(rf'value="(preferred_{code}_\d+\.html)"', read(SOURCE, url)):
            rest.append(f"{HOST}/2012/fr/com/preferred/{link}")
    raw.fetch_many(SOURCE, list(dict.fromkeys(rest)), suffix=".html")


def load12() -> Year:
    SEEN.clear()
    found = index12()
    top = read(SOURCE, f"{HOST}/2012/fr/com/preferred/preferred_top.html")
    starts = dict(re.findall(r"preferred_(CGM\d+)_(\d+)\.html", top))
    communes = []
    for code, name in found.communes.items():
        page = page12(code, name, read(SOURCE, tab12("com", code)))
        council = Council(code[3:], name, page)
        if code in starts:
            first = f"{HOST}/2012/fr/com/preferred/preferred_{code}_{starts[code]}.html"
            html = read(SOURCE, first)
            for link in dict.fromkeys(re.findall(rf'value="(preferred_{code}_\d+\.html)"', html)):
                council.candidates += candidates12(page.rows, read(SOURCE, f"{HOST}/2012/fr/com/preferred/{link}"))
        communes.append(council)
    districts = []
    for code, name in found.districts.items():
        page = page12(code, name, read(SOURCE, tab12("pro", code)))
        council = Council(code[3:], name, page, group=code[3])
        for canton, canton_name in found.cantons.items():
            if proper(found.district_of[canton]) == name and canton[3] == code[3]:
                council.units.append(
                    (canton[3:], canton_name, page12(canton, canton_name, read(SOURCE, tab12("pro", canton))))
                )
        districts.append(council)
    return Year(DAYS[2012], communes, districts, list(dict.fromkeys(SEEN)))


# ---- 2006 -------------------------------------------------------------------------------------------------------

LATIN = "latin-1"


def u06(part: str, folder: str, name: str) -> str:
    return f"{HOST}/2006/fr/{part}/{folder}/{name}.html"


def entities06(part: str, start: str) -> dict[str, str]:
    html = read(SOURCE, u06(part, "listvotes", start), LATIN)
    block = re.search(r'<SELECT name="entities".*?</SELECT>', html, flags=re.DOTALL | re.IGNORECASE)
    if block is None:
        raise LookupError(f"{start} has no entity list")
    found = {}
    for value, name in re.findall(
        r'OPTION value="nav(\d+)i\.html"[^>]*>\s*([^<]*)', block.group(0), flags=re.IGNORECASE
    ):
        found[value] = unescape(name).strip()
    return found


def seats06(text: str) -> int | None:
    return int(text) if re.fullmatch(r"\d+", text) else None


def page06(code: str, name: str, html: str) -> Page:
    page = Page(code, name, [])
    votes_at, seats_at = 8, 5
    for cell in rows_of(html):
        if "Voix 2006" in cell and "Sièges 2006" in cell:
            votes_at, seats_at = cell.index("Voix 2006") + 1, cell.index("Sièges 2006") + 1
        elif len(cell) > votes_at and cell[1] and cell[0] != "Total" and (votes := number(cell[votes_at])) is not None:
            page.rows.append(Row(cell[0] or None, cell[1], votes, seats06(cell[seats_at])))
        elif cell and len(cell) >= 4:
            attribute = {
                "Participation": "cast",
                "Blancs ou nuls": "blank",
                "Valables": "valid",
                "Inscrits": "registered",
            }.get(cell[0])
            if attribute:
                setattr(page, attribute, number(cell[3]))
    return page


def candidates06(html: str) -> list[Candidate]:
    found: list[Candidate] = []
    head = ""
    titulars = False
    for cell in rows_of(html):
        if cell and (match := re.fullmatch(r"(\d+) - (.+)", cell[0])):
            head = match.group(1)
            label = match.group(2)
        elif cell and cell[0] == "Elus titulaires":
            titulars = True
        elif cell and cell[0] == "Elus suppléants":
            titulars = False
        elif titulars and len(cell) >= 7 and re.fullmatch(r"\d+", cell[0]) and (votes := number(cell[2])) is not None:
            found.append(Candidate(head, label, int(cell[0]), cell[1], votes, True))
    return found


def fold_name(text: str) -> str:
    return folded(re.sub(r"\bsaint\b", "st", re.sub(r"\s*\(.*?\)", "", text.lower())))


def fetch06() -> None:
    for part in ("local_wallonia", "province_wallonia"):
        raw.fetch(
            SOURCE, u06(part, "listvotes", "nav01423i" if part == "local_wallonia" else "nav03066i"), suffix=".html"
        )
    towns = entities06("local_wallonia", "nav01423i")
    districts = entities06("province_wallonia", "nav03066i")
    raw.fetch_many(SOURCE, [u06("local_wallonia", "listvotes", f"res{c}i") for c in towns], suffix=".html")
    raw.fetch_many(SOURCE, [u06("province_wallonia", "listvotes", f"res{c}i") for c in districts], suffix=".html")
    raw.fetch_many(SOURCE, [u06("local_wallonia", "preferencevotes", f"nav{c}") for c in towns], suffix=".html")
    pages = []
    for c in towns:
        html = read(SOURCE, u06("local_wallonia", "preferencevotes", f"nav{c}"), LATIN)
        pages += [
            u06("local_wallonia", "preferencevotes", v.removesuffix(".html"))
            for v in re.findall(rf'OPTION value="(res{c}x\d+\.html)"', html, flags=re.IGNORECASE)
        ]
    raw.fetch_many(SOURCE, list(dict.fromkeys(pages)), suffix=".html")


def load06() -> Year:
    nis = {fold_name(c.name): c.code for c in year(2012).communes}
    nis |= {fold_name(c.name): c.code for c in year(2018).communes}
    SEEN.clear()
    towns = entities06("local_wallonia", "nav01423i")
    communes = []
    for site, name in towns.items():
        code = nis.get(fold_name(name))
        if code is None:
            raise LookupError(f"2006 commune {name} has no code")
        page = page06(site, name, read(SOURCE, u06("local_wallonia", "listvotes", f"res{site}i"), LATIN))
        council = Council(code, name, page)
        html = read(SOURCE, u06("local_wallonia", "preferencevotes", f"nav{site}"), LATIN)
        for link in dict.fromkeys(re.findall(rf'OPTION value="(res{site}x\d+)\.html"', html, flags=re.IGNORECASE)):
            council.candidates += candidates06(read(SOURCE, u06("local_wallonia", "preferencevotes", link), LATIN))
        communes.append(council)
    seats = {fold_name(c.name): c.code for c in communes}
    districts = []
    for site, name in entities06("province_wallonia", "nav03066i").items():
        page = page06(site, name, read(SOURCE, u06("province_wallonia", "listvotes", f"res{site}i"), LATIN))
        seat = seats.get(fold_name(name)) or seats.get(fold_name(re.sub(r"\s*\(.*\)", "", name)))
        if seat is None:
            raise LookupError(f"2006 district {name} has no province")
        districts.append(Council(f"06{site}", name, page, units=[(f"06{site}", name, page)], group=seat[0]))
    return Year(DAYS[2006], communes, districts, list(dict.fromkeys(SEEN)))


# ---- identifiers ------------------------------------------------------------------------------------------------

BELGIUM = str(uuid5(package.NAMESPACE, "polity\x1fBE"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBEL"))
WALLONIA = "03000"
PROVINCES = {"2": "20002", "5": "50000", "6": "60000", "8": "80000", "9": "90000"}
PROVINCE_NAMES = {"2": "Brabant wallon", "5": "Hainaut", "6": "Liège", "8": "Luxembourg", "9": "Namur"}
MUNICIPALITY, PROVINCE, UNIT = "be.wallonie_municipality", "be.wallonie_province", "be.wallonie_unit"
REGISTERED, BALLOTS, VALID, INVALID, PREFERENCE = 2, 3, 4, 5, 7
RULES = {"formula": "dhondt"}
MIN_LISTS = 3
# Statbel renumbered these Hainaut municipalities on 1 January 2019, when the arrondissements were redrawn
RENUMBERED = {
    "56011": "58002",
    "54010": "57097",
    "55010": "51067",
    "56085": "58003",
    "55022": "58001",
    "55023": "51069",
    "52043": "55086",
    "56087": "58004",
    "54007": "57096",
    "52063": "55085",
    "55039": "51068",
}
RENUMBERED_ON = date(2019, 1, 1)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def folded(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


SOURCE_ID = key("source", "results")
STATBEL_ID = key("source", "statbel")
MUNICIPAL_BODY = key("body", "municipal-council")
PROVINCIAL_BODY = key("body", "provincial-council")
SYSTEM = key("electoral_system", "dhondt")


def party(text: str) -> str:
    return key("party", folded(text))


# ---- geography --------------------------------------------------------------------------------------------------


@dataclass
class Sector:
    municipality: str
    names: dict[str, str]
    province: str
    province_names: dict[str, str]
    shape: shapely.Geometry


@functools.cache
def sectors() -> list[Sector]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, STATBEL).path)
    stem = "sh_statbel_statistical_sectors_3812_20230101.shp/sh_statbel_statistical_sectors_3812_20230101"
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{stem}.shp")),
        shx=io.BytesIO(archive.read(f"{stem}.shx")),
        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
        encoding="utf-8",
    )
    found = []
    for record in reader.iterShapeRecords():
        if record.record is None or record.shape is None:
            continue
        row = record.record.as_dict()
        if row["CNIS_REGIO"] != WALLONIA:
            continue
        found.append(
            Sector(
                str(row["CNIS5_2023"]),
                {"fr": str(row["T_MUN_FR"]), "nl": str(row["T_MUN_NL"]), "de": str(row["T_MUN_DE"])},
                str(row["CNIS_PROVI"]),
                {"fr": str(row["T_PROVI_FR"]), "nl": str(row["T_PROVI_NL"]), "de": str(row["T_PROVI_DE"])},
                shapely.geometry.shape(record.shape.__geo_interface__),
            )
        )
    return found


def wgs(shape: shapely.Geometry) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(3812, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = wgs(shape)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=STATBEL_ID,
    )


def label(p: Package, id: str, names: dict[str, str]) -> None:
    for lang in ("fr", "nl", "de"):
        text = names[lang].strip()
        if text and (lang == "fr" or text != names["fr"].strip()):
            p.name(id, text, lang, "official", lang == "fr")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Wallonia's five provinces and its municipalities from Statbel's statistical sectors of 1 January 2023",
    )
    p.external |= {BELGIUM, TERRITORY}
    artifact = raw.cached(SOURCE, STATBEL)
    p.add(
        "source",
        id=STATBEL_ID,
        kind="official_body",
        publisher="Statbel (Algemene Directie Statistiek)",
        title="Statistical sectors 2023 (shapefile, Belgian Lambert 2008)",
        url="https://statbel.fgov.be/en/open-data/statistical-sectors-2023",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Statbel (Directorate-general Statistics - Statistics Belgium)",
    )
    p.artifact(artifact, STATBEL_ID)
    for code, rank, noun in ((PROVINCE, 1, "province"), (MUNICIPALITY, 2, "municipality")):
        p.add("scheme", code=code, polity_id=BELGIUM, kind="administrative", rank=rank, noun=noun, code_system="refnis")
        p.add(
            "boundary_set",
            id=key("boundary_set", code, 2023),
            scheme_code=code,
            vintage="2023",
            valid_from=date(1977, 1, 1),
            as_of=date(2023, 1, 1),
        )
    p.add(
        "scheme",
        code=UNIT,
        polity_id=BELGIUM,
        kind="electoral_district",
        rank=3,
        noun="reporting unit",
        code_system=None,
    )
    by_town: dict[str, list[Sector]] = {}
    for sector in sectors():
        by_town.setdefault(sector.municipality, []).append(sector)
    codes = sorted(by_town)
    towns = [shapely.union_all([s.shape for s in by_town[c]], grid_size=0.01) for c in codes]
    try:
        simple = list(shapely.make_valid(shapely.coverage_simplify(numpy.array(towns), 10.0)))
    except shapely.errors.GEOSException:
        simple = [shapely.make_valid(shapely.simplify(t, 10.0)) for t in towns]
    provinces: dict[str, list[shapely.Geometry]] = {}
    names: dict[str, dict[str, str]] = {}
    for code, shape in zip(codes, simple, strict=True):
        first = by_town[code][0]
        provinces.setdefault(first.province, []).append(shape)
        names[first.province] = first.province_names
    for code, members in sorted(provinces.items()):
        id = area(PROVINCE, code)
        p.add("area", id=id, scheme_code=PROVINCE)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        label(p, id, names[code])
        outline(p, id, shapely.coverage_union_all(members))
    for code, shape in zip(codes, simple, strict=True):
        first = by_town[code][0]
        id = area(MUNICIPALITY, code)
        p.add("area", id=id, scheme_code=MUNICIPALITY)
        old = next((o for o, n in RENUMBERED.items() if n == code), None)
        p.add("area_code", area_id=id, code=code, valid_from=RENUMBERED_ON if old else None, valid_to=None)
        if old:
            p.add("area_code", area_id=id, code=old, valid_from=None, valid_to=date(2018, 12, 31))
        p.add("area_parent", area_id=id, parent_id=area(PROVINCE, first.province))
        label(p, id, first.names)
        outline(p, id, shape)
    return p


# ---- registry ---------------------------------------------------------------------------------------------------

METHOD = (
    "Published by the Walloon Region on its results site for each election: list votes and seats per commune, "
    "per province district, and per judicial canton, with the registered voters, the ballots cast, the blank and "
    "invalid ballots, and the valid votes. The Region publishes blank and invalid ballots as one figure, which is "
    "stored as invalid ballots. Seats and elected candidates are as the Region declared them; the archive stores "
    "them and does not recompute them. Valid votes are the votes for lists. Where a page prints a list under a "
    "different label in another year, each year's label is kept. Each candidate's votes are the personal votes "
    "the page prints, before the Region moves list-head votes to candidates."
)
LOADERS: dict[int, Callable[[], Year]] = {2006: load06, 2012: load12, 2018: load18}
FETCHERS: dict[int, Callable[[], None]] = {2006: fetch06, 2012: fetch12, 2018: fetch18}


@functools.cache
def year(number: int) -> Year:
    return LOADERS[number]()


def lists_of(council: Council) -> list[Row]:
    return council.page.rows


def known_parties() -> dict[str, str]:
    seen: dict[str, Counter[str]] = {}
    counted: Counter[str] = Counter()
    for number in LOADERS:
        data = year(number)
        for council in [*data.communes, *data.districts]:
            for row in council.page.rows:
                seen.setdefault(folded(row.label), Counter())[row.label] += 1
                counted[folded(row.label)] += 1
    return {
        f: labels.most_common(1)[0][0]
        for f, labels in seen.items()
        if f and counted[f] >= MIN_LISTS and f != folded("Indépendants")
    }


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Walloon parties, municipal and provincial councils, and their electoral system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BELGIUM)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Service public de Wallonie",
        title="Elections communales et provinciales en Wallonie: résultats",
        url="https://electionslocales.wallonie.be/",
        attribution="Service public de Wallonie, Région wallonne",
    )
    for number in LOADERS:
        for url in year(number).urls:
            p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for id, english, french in (
        (MUNICIPAL_BODY, "Municipal council", "Conseil communal"),
        (PROVINCIAL_BODY, "Provincial council", "Conseil provincial"),
    ):
        p.add("body", id=id, polity_id=BELGIUM, kind="council")
        p.name(id, english, "en", "official")
        p.name(id, french, "fr", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=BELGIUM,
        valid_from=date(2006, 1, 1),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Seats are allocated among the lists of a commune or of a province district by the D'Hondt method. The "
            "Region declares the seats and the elected candidates, and the archive stores them as declared."
        ),
        lang="en",
    )
    for folded_label, text in sorted(known_parties().items()):
        id = party(text)
        p.add("party", id=id, kind="party", polity_id=BELGIUM)
        p.name(id, text, "und", "official")
    return p


# ---- elections --------------------------------------------------------------------------------------------------

FIGURES = (("registered", REGISTERED), ("cast", BALLOTS), ("blank", INVALID), ("valid", VALID))


def accounting(p: Package, result_set: str, unit: str, page: Page) -> None:
    for attribute, measure in FIGURES:
        value = getattr(page, attribute)
        if value is not None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )


@functools.cache
def names() -> dict[str, str]:
    old = {new: o for o, new in RENUMBERED.items()}
    found = {old.get(s.municipality, s.municipality): s.names["fr"] for s in sectors()}
    for council in year(2018).districts:
        found[f"PRD{council.code}"] = council.name
    return found


NOTES = {
    2006: (
        " The 2006 site names only the elected candidates, so each list's candidates here are its elected "
        "titular members, and its province districts are one reporting unit each, because the site does not say which "
        "cantons belong to which district."
    ),
}


def election(number: int) -> Package:
    data = year(number)
    for council in data.communes:
        council.name = names().get(council.code, council.name)
    for council in data.districts:
        council.name = names().get(f"PRD{council.code}", council.name)
    day = data.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day} Walloon municipal and provincial council elections from the Walloon Region's results site",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BELGIUM)
    for url in data.urls:
        p.uses(raw.cached(SOURCE, url))
    known = known_parties()
    base = f"be/{day}-walloon-local"
    id = key("election", day)
    p.add("election", id=id, polity_id=BELGIUM, kind="local", date_from=day, date_to=day, status="held")
    p.name(id, f"{day.year} Walloon local elections", "en", "official")
    p.name(id, f"Élections communales et provinciales {day.year} en Wallonie", "fr", "native", False)
    p.add("slug", path=base, entity_id=id, is_current=True)
    slugs: Counter[str] = Counter()

    def path(text: str) -> str:
        slugs[text] += 1
        return text if slugs[text] == 1 else f"{text}-{slugs[text]}"

    def contest(
        tag: str, council: Council, body: str, area_id: str | None, names: tuple[str, str], slug: str
    ) -> tuple[str, str, dict[str, str]]:
        seats = sum(row.seats or 0 for row in council.page.rows)
        c = key("contest", day, tag)
        p.add(
            "contest",
            id=c,
            election_id=id,
            kind="list_allocation",
            body_id=body,
            district_area_id=area_id,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(c, names[0], "en", "official")
        p.name(c, names[1], "fr", "native", False)
        p.add("slug", path=path(slug), entity_id=c, is_current=True)
        result_set = key("result_set", day, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + NOTES.get(day.year, ""),
            lang="en",
        )
        options: dict[str, str] = {}
        for row in council.page.rows:
            option = options[row.number or ""] = key("option", day, tag, row.number)
            fold = folded(row.label)
            p.add(
                "contest_option",
                id=option,
                contest_id=c,
                kind="list",
                party_id=party(known[fold]) if fold in known else None,
                ballot_position=int(row.number) if row.number and row.number.isdigit() else None,
                ballot_label=row.label,
            )
            if row.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
        return c, result_set, options

    for council in data.communes:
        town = area(MUNICIPALITY, RENUMBERED.get(council.code, council.code))
        c, result_set, options = contest(
            f"CGM{council.code}",
            council,
            MUNICIPAL_BODY,
            town,
            (f"{council.name} municipal council", f"Conseil communal de {council.name}"),
            f"{base}/municipal-councils/{changeset.slug(council.name)}",
        )
        unit = key("unit", day, f"CGM{council.code}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BELGIUM,
            scheme_code=UNIT,
            code=f"{day}:CGM{council.code}",
            area_relation="equals",
            area_id=town,
            kind="standard",
        )
        accounting(p, result_set, unit, council.page)
        for row in council.page.rows:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[row.number or ""],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=row.votes,
            )
        for cand in council.candidates:
            tag = f"{day}:{council.code}:{cand.number}:{cand.position}"
            person = key("person", tag)
            p.add("person", id=person, birth_precision=None)
            p.name(person, cand.name, "fr", "official")
            candidacy = key("candidacy", tag)
            list_option = options[cand.number]
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=list_option,
                role="list_member",
                list_position=cand.position,
                ballot_name=cand.name,
            )
            sub = key("option", day, f"CGM{council.code}", cand.number, cand.position, "candidate")
            p.add(
                "contest_option",
                id=sub,
                contest_id=c,
                kind="candidate",
                parent_option_id=list_option,
                ballot_position=cand.position,
                ballot_label=cand.name,
            )
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=sub,
                channel=0,
                measure=PREFERENCE,
                count_round=0,
                flags=0,
                value=cand.votes,
            )
            if cand.elected:
                p.add(
                    "outcome", result_set_id=result_set, option_id=list_option, candidacy_id=candidacy, kind="elected"
                )
    for council in data.districts:
        province = area(PROVINCE, PROVINCES[council.group])
        c, result_set, options = contest(
            f"PRD{council.code}",
            council,
            PROVINCIAL_BODY,
            None,
            (f"{council.name} provincial district", f"Conseil provincial, district de {council.name}"),
            f"{base}/provincial-councils/{changeset.slug(PROVINCE_NAMES[council.group])}/{changeset.slug(council.name)}",
        )
        for page_code, page_name, page in council.units:
            unit = key("unit", day, f"PRK{page_code}")
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BELGIUM,
                scheme_code=UNIT,
                code=f"{day}:PRK{page_code}",
                area_relation="within",
                area_id=province,
                kind="standard",
            )
            accounting(p, result_set, unit, page)
            for row in page.rows:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=options[row.number or ""],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=row.votes,
                )
        for attribute, measure in FIGURES:
            value = getattr(council.page, attribute)
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for row in council.page.rows:
            p.add("declared", result_set_id=result_set, option_id=options[row.number or ""], measure=1, value=row.votes)
    return p


# ---- pipeline entry points --------------------------------------------------------------------------------------


def fetch() -> None:
    raw.fetch(SOURCE, STATBEL, suffix=".zip")
    for number in sorted(FETCHERS):
        FETCHERS[number]()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[n]}" for n in sorted(LOADERS))]


def parse() -> None:
    geo().write()
    registry().write()
    for number in sorted(LOADERS):
        election(number).write()


def emit() -> None:
    package.run("load", packages())
