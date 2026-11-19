"""The national and provincial deputy elections of 20 December 2023 in the Democratic Republic of the Congo, from the
Commission électorale nationale indépendante (CENI).

The CENI published its provisional results in January 2024 as PDF sheets with a text layer. For the national deputies
it printed the seat attribution sheet of every circonscription, which lists each candidate of the lists that reached
the national threshold with their votes and marks the elected. For the provincial deputies it printed only the
elected candidates of each circonscription, with their votes, and a threshold sheet per province with the voters,
the valid and blank ballots, and each list's votes across the province.

The circonscriptions are those of Loi n°23/025 of 15 June 2023: the territoire and the ville in the provinces, and in
Kinshasa four groupings of communes for the national deputies and the 24 communes for the provincial deputies.
Outlines come from geoBoundaries' open set: its ADM1 layer for the provinces and its ADM2 layer (OCHA's territories
and cities) for the circonscriptions it draws. OpenStreetMap's boundaries draw Kinshasa's communes (and so its
districts as their unions), the ville of Kalemie, and the territoire of Beni; OCHA's ADM3 layer draws the ville of
Kamina.
"""

import functools
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "cd.ceni"
CONGO = str(uuid5(package.NAMESPACE, "polity\x1fCD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fCOD"))
DAY = date(2023, 12, 20)
FILES = "https://www.ceni.cd/sites/default/files/"
# The seat attribution sheets of the national deputies: every candidate of the lists that reached the threshold.
NATIONAL = (
    FILES + "2024-01/CENI%20RDC%20-%20Liste%20Globale%20des%20Candidats%20a%CC%80%20la%20De%CC%81putation%20Nationale"
    "%20avec%20SCORE%20-%20Re%CC%81sultats%20Provisoire%20%281%29.pdf"
)
# The national threshold sheet: voters, valid, invalid, and blank ballots, and each list's national votes.
NATIONAL_THRESHOLD = (
    FILES + "2024-01/CENI%20RDC%20-%20Fiche%20De%CC%81termination%20Seuil%20de%20Repre%CC%81sentativite%CC%81%20-%20"
    "De%CC%81putation%20Nationale%20-%20Re%CC%81sultats%20Provis.pdf"
)
# The seats won by each list in the National Assembly.
NATIONAL_SEATS = (
    FILES + "2024-01/CENI%20RDC%20-%20Fiche%20D%C3%A9termination%20Seuil%20de%20Repr%C3%A9sentativit%C3%A9%20-%20"
    "D%C3%A9putation%20Nationale%20-%20R%C3%A9sultats%20Provis.pdf"
)
# The elected provincial deputies of each circonscription, with their votes.
PROVINCIAL = (
    FILES + "2024-01/CENI%20RDC%20-%20Liste%20des%20Elus%20%C3%A0%20la%20D%C3%A9putation%20Provinciale%20-%20"
    "R%C3%A9sultats%20Provisoires%20-%20Scrutins%20du%2020%20d%C3%A9.pdf"
)
# The provincial threshold sheets: voters, valid and blank ballots, and each list's votes in each province.
PROVINCIAL_THRESHOLD = (
    FILES + "2024-01/CENI%20RDC%20-%20Fiche%20D%C3%A9termination%20Seuil%20de%20Repr%C3%A9sentativit%C3%A9%20-%20"
    "D%C3%A9putation%20Provinciale%20-%20R%C3%A9sultats%20Prov.pdf"
)
# Loi n°23/025 of 15 June 2023, which lists the circonscriptions of both elections.
LAW = FILES + "2023-10/J.O.%20n%C2%B0%20sp%C3%A9cial%20du%2024%20juin%202023_REPARTITION%20DES%20SIEGES.pdf"
PAGES = {
    "national": "https://www.ceni.cd/depeches/2024/01/17/liste-globale-des-elus-la-deputation-nationale",
    "provincial": "https://www.ceni.cd/depeches/2024/01/28/la-liste-des-elus-la-deputation-provinciale-de-2023",
}
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/COD"
GB_PROVINCES = f"{GEOBOUNDARIES}/ADM1/geoBoundaries-COD-ADM1.geojson"
GB_TERRITORIES = f"{GEOBOUNDARIES}/ADM2/geoBoundaries-COD-ADM2.geojson"
SIMPLIFY = 0.002
# Outlines of towns and communes keep more detail than those of territoires.
FINE = 0.0002
# Closes the slivers between communes' simplified outlines when they are joined into a district.
SLIVER = 0.00005
# OpenStreetMap's administrative relations (admin_level 7) for Kinshasa's 24 communes, the ville of Kalemie, and the
# territoire of Beni, fetched in one lookup. Kinshasa's communes are keyed by their folded names.
OSM_COMMUNES = {
    "bandalungwa": 388091,
    "barumbu": 388104,
    "bumbu": 388134,
    "gombe": 388098,
    "kalamu": 388127,
    "kasavubu": 388094,
    "kimbanseke": 389614,
    "kinshasa": 388103,
    "kintambo": 388090,
    "kisenso": 389536,
    "lemba": 10089740,
    "limete": 388599,
    "lingwala": 388097,
    "makala": 388133,
    "maluku": 389746,
    "masina": 389694,
    "matete": 389334,
    "montngafula": 389761,
    "ndjili": 389659,
    "ngaba": 388139,
    "ngaliema": 389760,
    "ngiringiri": 10993823,
    "nsele": 389689,
    "selembao": 388135,
}
# Other circonscriptions drawn from OpenStreetMap, by code.
OSM_OTHERS = {"CD-TA:ville-de-kalemie": 9330398, "CD-NK:beni": 5327776}
OSM_IDS = sorted([*OSM_COMMUNES.values(), *OSM_OTHERS.values()])
OSM_LOOKUP = (
    "https://nominatim.openstreetmap.org/lookup?osm_ids="
    + ",".join(f"R{i}" for i in OSM_IDS)
    + "&format=geojson&polygon_geojson=1&extratags=1"
)
# OCHA DR Congo's COD-AB (CC BY 3.0 IGO), whose ADM3 layer draws the ville of Kamina.
OCHA = (
    "https://data.humdata.org/dataset/f42132b9-8cc6-4201-b020-9259c56e8868/resource/"
    "97260e2b-65b1-41e3-aef2-fb0e6874e406/download/cod_admin_boundaries.geojson.zip"
)
OCHA_KAMINA = ("CD-HL:ville-de-kamina", "CD730202")
# A territoire's outline covers more than this many square degrees; a ville's covers less.
VILLE_AREA = 0.3

# The CENI's spelling of each province, its ISO 3166-2 code, its name, and its name in geoBoundaries.
PROVINCES = {
    "BAS-UELE": ("CD-BU", "Bas-Uélé", "Lower Uele"),
    "EQUATEUR": ("CD-EQ", "Équateur", "Équateur"),
    "HAUT-KATANGA": ("CD-HK", "Haut-Katanga", "Haut-Katanga"),
    "HAUT-LOMAMI": ("CD-HL", "Haut-Lomami", "Haut-Lomami"),
    "HAUT-UELE": ("CD-HU", "Haut-Uélé", "Upper Uele"),
    "ITURI": ("CD-IT", "Ituri", "Ituri"),
    "KASAI": ("CD-KS", "Kasaï", "Kasai"),
    "KASAI CENTRAL": ("CD-KC", "Kasaï-Central", "Central Kasai"),
    "KASAI ORIENTAL": ("CD-KE", "Kasaï-Oriental", "Kasai-Oriental"),
    "KINSHASA": ("CD-KN", "Kinshasa", "Kinshasa"),
    "KONGO CENTRAL": ("CD-BC", "Kongo-Central", "Kongo-Central"),
    "KWANGO": ("CD-KG", "Kwango", "Kwango"),
    "KWILU": ("CD-KL", "Kwilu", "Kwilu"),
    "LOMAMI": ("CD-LO", "Lomami", "Lomami"),
    "LUALABA": ("CD-LU", "Lualaba", "Lualaba"),
    "MAI-NDOMBE": ("CD-MN", "Mai-Ndombe", "Mai-Ndombe"),
    "MANIEMA": ("CD-MA", "Maniema", "Maniema"),
    "MONGALA": ("CD-MO", "Mongala", "Mongala"),
    "NORD-KIVU": ("CD-NK", "Nord-Kivu", "North Kivu"),
    "NORD-UBANGI": ("CD-NU", "Nord-Ubangi", "Nord-Ubangi"),
    "SANKURU": ("CD-SA", "Sankuru", "Sankuru"),
    "SUD-KIVU": ("CD-SK", "Sud-Kivu", "South Kivu"),
    "SUD-UBANGI": ("CD-SU", "Sud-Ubangi", "Sud-Ubangi"),
    "TANGANYIKA": ("CD-TA", "Tanganyika", "Tanganyika"),
    "TSHOPO": ("CD-TO", "Tshopo", "Tshopo"),
    "TSHUAPA": ("CD-TU", "Tshuapa", "Tshuapa"),
}
# Kinshasa's national-deputy circonscriptions as the CENI's sheets name them, with their communes under Loi 23/025.
# The law numbers them differently from the CENI's sheets (its Kinshasa I is Lukunga and its Kinshasa II is Funa);
# the communes are those the law lists for each district.
KINSHASA = {
    "KINSHASA I FUNA": (
        "Funa",
        ("BANDALUNGWA", "BUMBU", "KALAMU", "KASA-VUBU", "MAKALA", "NGIRI-NGIRI", "SELEMBAO"),
    ),
    "KINSHASA II LUKUNGA": (
        "Lukunga",
        ("BARUMBU", "GOMBE", "KINSHASA", "KINTAMBO", "LINGWALA", "MONT-NGAFULA", "NGALIEMA"),
    ),
    "KINSHASA III MONT - AMBA": ("Mont-Amba", ("KISENSO", "LEMBA", "LIMETE", "MATETE", "NGABA")),
    "KINSHASA IV TSHANGU": ("Tshangu", ("KIMBANSEKE", "MALUKU", "MASINA", "NDJILI", "NSELE")),
}
# The communes' names, where the CENI's capitals drop an apostrophe.
COMMUNES = {"NDJILI": "N'Djili", "NSELE": "N'Sele"}
# Circonscriptions whose geoBoundaries name differs from the CENI's.
ADM2 = {"MANKANZA": "Makanza", "LUILU (MWENE-DITU)": "Luilu"}
# Circonscriptions whose name the CENI spells differently in its two elections' sheets, by the national spelling.
SPELLINGS = {"LUILU": "LUILU (MWENE-DITU)"}
GENDER = {"M": "male", "F": "female"}
DERIVED = 1
VOTES, BALLOTS, VALID, INVALID, BLANK, CANDIDATE_VOTES = 1, 3, 4, 5, 6, 10
ROW = re.compile(
    r"^\s*(\d+)\s+(\S.*?)\s{2,}([MF])\s{2,}(\S.*?)\s{2,}(\d{1,3}(?: \d{3})*)(?:\s+(Elu \(e\)))?\s*$",
)
CONTINUED = re.compile(r"^\s{3,}([A-ZÀ-Ü'][A-ZÀ-Ü' .\-()]*)\s*$")


def fetch() -> None:
    for url in (NATIONAL, NATIONAL_THRESHOLD, NATIONAL_SEATS, PROVINCIAL, PROVINCIAL_THRESHOLD, LAW):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for url in (GB_PROVINCES, GB_TERRITORIES, OSM_LOOKUP):
        raw.fetch(SOURCE, url, suffix=".geojson")
    raw.fetch(SOURCE, OCHA, suffix=".zip")


# ---------------------------------------------------------------- reading


@functools.cache
def text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def number(text: str) -> int:
    return int(text.replace(" ", ""))


@dataclass
class Candidate:
    rank: int
    name: str
    gender: str
    sigle: str
    votes: int
    elected: bool


@dataclass(frozen=True)
class Place:
    province: str
    name: str


def _rows(lines: list[str], into: list[Candidate]) -> None:
    """The candidate rows of a page, with the second line of a name that wraps."""
    previous = False
    for line in lines:
        found = ROW.match(line)
        if found:
            rank, name, gender, sigle, votes, elected = found.groups()
            into.append(Candidate(int(rank), name.strip(), gender, sigle.strip(), number(votes), bool(elected)))
            previous = True
            continue
        more = CONTINUED.match(line) if previous else None
        if more and into:
            into[-1].name = f"{into[-1].name} {more.group(1).strip()}"
            continue
        if re.match(r"^\s*\d+\s+[A-Z]", line):
            raise ValueError(f"{SOURCE}: unread candidate row {line!r}")
        previous = False


@functools.cache
def national() -> dict[Place, list[Candidate]]:
    """Each circonscription's candidates from the national seat attribution sheets, in the CENI's order."""
    found: dict[Place, list[Candidate]] = {}
    for page in text(NATIONAL).split("\f"):
        if not page.strip():
            continue
        head = re.search(r"PROVINCE:\s*(.+?)\s+CIRCONSCRIPTION ELECTORALE", page)
        foot = re.search(r"candidats des listes/\s*(.+?)\s*(?:Page \d+ sur \d+)?\s*$", page, re.MULTILINE)
        if head is None or foot is None:
            raise ValueError(f"{SOURCE}: a national sheet without its province or circonscription")
        place = Place(head.group(1).strip(), foot.group(1).strip())
        _rows(page.split("\n"), found.setdefault(place, []))
    for place, rows in found.items():
        if [row.rank for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError(f"{SOURCE}: {place.name} skips a candidate row")
    return found


@functools.cache
def provincial() -> dict[Place, list[Candidate]]:
    """Each circonscription's elected provincial deputies, in the CENI's order."""
    found: dict[Place, list[Candidate]] = {}
    province = ""
    current: list[Candidate] = []
    block: list[str] = []
    for line in text(PROVINCIAL).split("\n"):
        head = re.search(r"PROVINCE:\s*(.+?)\s*$", line)
        circ = re.search(r"CIRCONSCRIPTION ELECTORALE\s*:\s*(.+?)\s*$", line)
        if head or circ:
            _rows(block, current)
            block = []
            if head:
                province = head.group(1).strip()
            else:
                assert circ is not None
                current = found.setdefault(Place(province, circ.group(1).strip()), [])
            continue
        block.append(line)
    _rows(block, current)
    for place, rows in found.items():
        if not rows or not all(row.elected for row in rows):
            raise ValueError(f"{SOURCE}: {place.name} lists no elected deputy or an unelected one")
        if [row.rank for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError(f"{SOURCE}: {place.name} skips an elected deputy")
    return found


def _named(lines: list[str], pattern: re.Pattern[str]) -> dict[str, tuple[str, re.Match[str]]]:
    """Each list's sigle with its full name, which the sheets print above the row and finish on it."""
    found: dict[str, tuple[str, re.Match[str]]] = {}
    for at, line in enumerate(lines):
        row = pattern.match(line)
        if not row:
            continue
        above = lines[at - 1].strip() if at and lines[at - 1].startswith(" " * 6) else ""
        if "ORGANISATION" in above or "OBTENUES" in above or "SIGLE" in above:
            above = ""
        name = re.sub(r"\s+", " ", f"{above} {row.group('rest').strip()}").strip()
        found[row.group("sigle")] = (name.replace("/ ", "/"), row)
    return found


@dataclass(frozen=True)
class Totals:
    voters: int
    valid: int
    blank: int
    invalid: int | None


@functools.cache
def national_lists() -> tuple[Totals, dict[str, tuple[str, int]], dict[str, int]]:
    """The national accounting, each list's name and national votes, and each list's seats."""
    sheet = text(NATIONAL_THRESHOLD)
    totals = Totals(
        number(_field(sheet, r"Total votants:\s*([\d ]+?)\s{2,}")),
        number(_field(sheet, r"valablement exprimé:\s*([\d ]+?)\s*$")),
        number(_field(sheet, r"Bulletins blancs:\s*([\d ]+?)\s*$")),
        number(_field(sheet, r"annulés et des sanctionnés:\s*([\d ]+?)\s*$")),
    )
    votes = _named(
        [line for line in sheet.split("\n") if line.strip()],
        re.compile(
            r"^\s*\d{2}\s+(?P<rest>.*?)\s{2,}(?P<sigle>\S+)\s{2,}(?P<votes>\d{1,3}(?: \d{3})*)\s{2,}[\d,]+\s+(?:OUI|NON)\s*$"
        ),
    )
    lists = {sigle: (name, number(row.group("votes"))) for sigle, (name, row) in votes.items()}
    if sum(votes for _, votes in lists.values()) > totals.valid:
        raise ValueError(f"{SOURCE}: the lists' national votes exceed the valid ballots")
    seated = _named(
        [line for line in text(NATIONAL_SEATS).split("\n") if line.strip()],
        re.compile(r"^\s*\d+\s+(?P<rest>.*?)\s{3,}(?P<sigle>\S+)\s{3,}(?P<seats>\d+)\s*$"),
    )
    seats = {sigle: int(row.group("seats")) for sigle, (_, row) in seated.items()}
    printed = number(_field(text(NATIONAL_SEATS), r"Total si[ée]ges:\s*(\d+)"))
    if sum(seats.values()) != printed:
        raise ValueError(f"{SOURCE}: the lists' seats add up to {sum(seats.values())}, not {printed}")
    for sigle, (name, _) in seated.items():
        if sigle not in lists:
            lists[sigle] = (name, 0)
    return totals, lists, seats


def _field(sheet: str, pattern: str) -> str:
    found = re.search(pattern, sheet, re.MULTILINE)
    if found is None:
        raise ValueError(f"{SOURCE}: no {pattern!r} on a threshold sheet")
    return found.group(1)


@functools.cache
def provincial_totals() -> dict[str, Totals]:
    """Each province's voters, valid ballots, and blank ballots from its threshold sheet."""
    found: dict[str, Totals] = {}
    for page in text(PROVINCIAL_THRESHOLD).split("\f"):
        head = re.search(r"Province:\s*(.+?)\s*$", page, re.MULTILINE)
        if head is None:
            continue
        totals = Totals(
            number(_field(page, r"Total votants:\s*([\d ]+?)\s{2,}")),
            number(_field(page, r"valablement exprimé:\s*([\d ]+?)\s*$")),
            number(_field(page, r"Bulletins blancs:\s*([\d ]+?)\s{2,}")),
            None,
        )
        if found.setdefault(head.group(1).strip(), totals) != totals:
            raise ValueError(f"{SOURCE}: {head.group(1)} prints two different totals")
    return found


# ---------------------------------------------------------------- places


def title(text: str) -> str:
    """A name in the CENI's capitals in title case: AKETI → Aketi, KASA-VUBU → Kasa-Vubu."""
    words = re.split(r"([ \-'])", text.lower())
    return "".join(word if word in ("de", "d", " ", "-", "'") else word[:1].upper() + word[1:] for word in words)


@dataclass(frozen=True)
class Circ:
    """A circonscription: a territoire, a ville, a Kinshasa district, or a Kinshasa commune."""

    province: str
    code: str
    name: str
    scheme: str
    parent: str
    base: str
    ville: bool


def _circ(province: str, printed: str) -> Circ:
    iso = PROVINCES[province][0]
    if printed in KINSHASA:
        district, _ = KINSHASA[printed]
        return Circ(province, f"{iso}:{changeset.slug(district)}", district, SCHEME, area(PROVINCE, iso), "", False)
    if province == "KINSHASA":
        (grouping,) = [k for k, (_, communes) in KINSHASA.items() if printed in communes]
        name = COMMUNES.get(printed, title(printed))
        return Circ(
            province,
            f"{iso}:{changeset.slug(name)}",
            name,
            COMMUNE,
            area(SCHEME, _circ(province, grouping).code),
            "",
            False,
        )
    printed = SPELLINGS.get(printed, printed)
    ville = printed.endswith(" VILLE")
    base = printed.removesuffix(" VILLE")
    shown = title(re.sub(r"\s*\(.*\)", "", base))
    name = (f"Ville d'{shown}" if shown[:1] in "AEIOU" else f"Ville de {shown}") if ville else shown
    return Circ(province, f"{iso}:{changeset.slug(name)}", name, SCHEME, area(PROVINCE, iso), base, ville)


@functools.cache
def circs() -> dict[Place, Circ]:
    found = {place: _circ(place.province, place.name) for place in [*national(), *provincial()]}
    for grouping in KINSHASA:
        found.setdefault(Place("KINSHASA", grouping), _circ("KINSHASA", grouping))
    return found


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def _shape(geometry: dict[str, Any], tolerance: float = SIMPLIFY) -> shapely.Geometry:
    found = shapely.make_valid(shapely.simplify(shapely.geometry.shape(geometry), tolerance, preserve_topology=True))
    polygons = [
        polygon
        for part in shapely.get_parts(found)
        if shapely.get_type_id(part) in (3, 6)
        for polygon in shapely.get_parts(part)
    ]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def _features(url: str) -> dict[str, dict[str, Any]]:
    data = json.loads(raw.cached(SOURCE, url).path.read_text())
    return {fold(feature["properties"]["shapeName"]): feature["geometry"] for feature in data["features"]}


@functools.cache
def outlines() -> tuple[dict[str, shapely.Geometry], dict[str, shapely.Geometry]]:
    """Each province's outline by ISO code, and each circonscription's outline that geoBoundaries draws, by code."""
    provinces = _features(GB_PROVINCES)
    by_province = {iso: _shape(provinces[fold(adm1)]) for iso, _, adm1 in PROVINCES.values()}
    territories = _features(GB_TERRITORIES)
    drawn: dict[str, shapely.Geometry] = {}
    places = [c for c in circs().values() if c.scheme == SCHEME and c.base]
    for c in places:
        geometry = territories.get(fold(ADM2.get(c.base, c.base)))
        if geometry is None:
            continue
        shape = _shape(geometry)
        twins = [o for o in places if o.province == c.province and o.base == c.base and o.ville != c.ville]
        if twins and (shapely.area(shape) > VILLE_AREA) == c.ville:
            continue
        drawn[c.code] = shape
    return by_province, drawn


@functools.cache
def osm_outlines() -> dict[str, shapely.Geometry]:
    """OpenStreetMap's outline of each Kinshasa commune and of the other circonscriptions it draws, by code."""
    data = json.loads(raw.cached(SOURCE, OSM_LOOKUP).path.read_text())
    by_id = {int(f["properties"]["osm_id"]): f["geometry"] for f in data["features"]}
    found: dict[str, shapely.Geometry] = {}
    for c in {c.code: c for c in circs().values() if c.scheme == COMMUNE}.values():
        found[c.code] = _shape(by_id[OSM_COMMUNES[fold(c.name)]], FINE)
    for code, relation in OSM_OTHERS.items():
        found[code] = _shape(by_id[relation], FINE if code != "CD-NK:beni" else SIMPLIFY)
    return found


def district_outlines() -> dict[str, shapely.Geometry]:
    """Each Kinshasa district's outline, the union of the outlines of its communes under Loi 23/025."""
    communes = osm_outlines()
    found: dict[str, shapely.Geometry] = {}
    for printed, (_, members) in KINSHASA.items():
        shapes = [communes[_circ("KINSHASA", m).code] for m in members]
        union = shapely.union_all(shapes).buffer(SLIVER).buffer(-SLIVER)
        found[_circ("KINSHASA", printed).code] = _shape(shapely.geometry.mapping(union), FINE)
    return found


@functools.cache
def kamina() -> shapely.Geometry:
    """The ville of Kamina, the ADM3 unit of OCHA's COD-AB that carries its pcode."""
    with zipfile.ZipFile(raw.cached(SOURCE, OCHA).path) as archive:
        data = json.loads(archive.read("cod_admin3.geojson"))
    (feature,) = [f for f in data["features"] if f["properties"]["adm3_pcode"] == OCHA_KAMINA[1]]
    return _shape(feature["geometry"], FINE)


# ---------------------------------------------------------------- packages


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NATION, PROVINCE, SCHEME, COMMUNE = "cd.nation", "cd.province", "cd.circonscription", "cd.commune"
CENI = key("source", "ceni")
LAW_SOURCE = key("source", "loi-23-025")
GB_ADM1, GB_ADM2 = key("source", "geoboundaries-adm1"), key("source", "geoboundaries-adm2")
OSM = key("source", "openstreetmap")
OCHA_ADM3 = key("source", "ocha-cod-ab-adm3")
ASSEMBLY = key("body", "assemblee-nationale")
PR_NATIONAL = key("electoral_system", "national-pr")
PR_PROVINCIAL = key("electoral_system", "provincial-pr")
PLURALITY = key("electoral_system", "plurality")
NATIONAL_ELECTION = key("election", "an", 2023)
PROVINCIAL_ELECTION = key("election", "ap", 2023)
NATIONAL_PATH = "cd/2023-12-20-national-assembly"
PROVINCIAL_PATH = "cd/2023-12-20-provincial-assemblies"
PROVINCES_FROM = date(2015, 6, 30)
PLAN_FROM = date(2023, 6, 15)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit(scheme: str, code: str) -> str:
    return key("unit", scheme, code)


def party(sigle: str) -> str:
    return key("party", sigle)


def provincial_assembly(iso: str) -> str:
    return key("body", "assemblee-provinciale", iso)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/an-2023", f"{SOURCE}/ap-2023"]


def parse() -> None:
    for build in (geo(), registry(), national_election(), provincial_election()):
        build.write()


def emit() -> None:
    package.run("load", packages())


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=CENI,
        kind="official_body",
        publisher="Commission électorale nationale indépendante",
        title="Résultats provisoires des élections législatives nationales et provinciales du 20 décembre 2023",
        url="https://www.ceni.cd/depeches",
        license="No license stated; published by the electoral authority for public use",
        attribution="Commission électorale nationale indépendante (CENI), République démocratique du Congo",
    )
    p.add(
        "source",
        id=LAW_SOURCE,
        kind="official_body",
        publisher="Journal officiel de la République démocratique du Congo",
        title="Loi n°23/025 du 15 juin 2023 portant répartition des sièges par circonscription électorale",
        url=LAW,
        license="Public law; no license stated",
        attribution="Journal officiel de la République démocratique du Congo",
    )
    for id, title_, notes in (
        (GB_ADM1, "geoBoundaries gbOpen: Democratic Republic of the Congo ADM1 (provinces)", "Boundary year 2019."),
        (
            GB_ADM2,
            "geoBoundaries gbOpen: Democratic Republic of the Congo ADM2 (territories and cities)",
            "Boundary year 2019, from OCHA DR Congo's Référentiel géographique commun.",
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab)",
            title=title_,
            url="https://www.geoboundaries.org/",
            license="CC BY 3.0 IGO",
            license_url="https://creativecommons.org/licenses/by/3.0/igo/",
            attribution="geoBoundaries (William & Mary geoLab); OCHA DR Congo",
            notes=notes,
        )
    p.add(
        "source",
        id=OSM,
        kind="community",
        publisher="OpenStreetMap contributors",
        title="OpenStreetMap administrative boundaries of Kinshasa's 24 communes, the ville of Kalemie, and the "
        "territoire of Beni, through Nominatim",
        url="https://www.openstreetmap.org/",
        license="Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="© OpenStreetMap contributors",
        notes="Kinshasa's four districts are the unions of their communes' outlines under Loi 23/025.",
    )
    p.add(
        "source",
        id=OCHA_ADM3,
        kind="official_body",
        publisher="OCHA DR Congo",
        title="Common Operational Datasets - Administrative Boundaries: Democratic Republic of the Congo, ADM3",
        url="https://data.humdata.org/dataset/cod-ab-cod",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="OCHA DR Congo",
        notes="Draws the ville of Kamina as its ADM3 unit CD730202. Boundary year 2019.",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 26 provinces of the Democratic Republic of the Congo, the circonscriptions of its national and "
        "provincial deputies under Loi 23/025, and Kinshasa's communes, with outlines from geoBoundaries, "
        "OpenStreetMap, and OCHA",
    )
    p.external |= {CONGO, COUNTRY}
    _sources(p)
    p.artifact(raw.cached(SOURCE, LAW), LAW_SOURCE)
    p.artifact(raw.cached(SOURCE, GB_PROVINCES), GB_ADM1)
    p.artifact(raw.cached(SOURCE, GB_TERRITORIES), GB_ADM2)
    p.artifact(raw.cached(SOURCE, OSM_LOOKUP), OSM)
    p.artifact(raw.cached(SOURCE, OCHA), OCHA_ADM3)
    for code, rank, kind, noun in (
        (NATION, 0, "administrative", "country"),
        (PROVINCE, 1, "administrative", "province"),
        (SCHEME, 2, "electoral_district", "circonscription"),
        (COMMUNE, 3, "administrative", "commune"),
    ):
        p.add("scheme", code=code, polity_id=CONGO, kind=kind, rank=rank, noun=noun)
    for code, vintage in ((PROVINCE, "2019"), (SCHEME, "2023")):
        p.add(
            "boundary_set",
            id=key("boundary_set", code, vintage),
            scheme_code=code,
            vintage=vintage,
            as_of=date(2023, 1, 19),
        )
    p.add(
        "reporting_unit",
        id=unit(NATION, "CD"),
        polity_id=CONGO,
        scheme_code=NATION,
        code="CD",
        area_relation="equals",
        area_id=COUNTRY,
        kind="standard",
    )

    def place(scheme: str, code: str, name: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, name, "fr", "official")
        p.add(
            "reporting_unit",
            id=unit(scheme, code),
            polity_id=CONGO,
            scheme_code=scheme,
            code=code,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )

    def outline(id: str, shape: shapely.Geometry, since: date, source: str) -> None:
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=since,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm" if source == OSM else "academic",
            source_id=source,
        )

    by_province, drawn = outlines()
    osm, districts = osm_outlines(), district_outlines()
    for iso, name, _ in PROVINCES.values():
        place(PROVINCE, iso, name, COUNTRY)
        outline(area(PROVINCE, iso), by_province[iso], PROVINCES_FROM, GB_ADM1)
    ordered = sorted({c.code: c for c in circs().values()}.values(), key=lambda c: (c.scheme == COMMUNE, c.code))
    for c in ordered:
        place(c.scheme, c.code, c.name, c.parent)
        if c.code in drawn and c.scheme == SCHEME:
            outline(area(SCHEME, c.code), drawn[c.code], PLAN_FROM, GB_ADM2)
        elif c.code in districts:
            outline(area(c.scheme, c.code), districts[c.code], PLAN_FROM, OSM)
        elif c.code in osm:
            outline(area(c.scheme, c.code), osm[c.code], PLAN_FROM, OSM)
        elif c.code == OCHA_KAMINA[0]:
            outline(area(c.scheme, c.code), kamina(), PLAN_FROM, OCHA_ADM3)
    return p


PARTY_BLOCS = (
    "Alliance",
    "Action des Alliés",
    "Actions des Alliés",
    "Coalition",
    "Forces Politiques Alliés",
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The National Assembly and the 26 provincial assemblies of the Democratic Republic of the Congo, their "
        "electoral systems in 2023, and the parties and groupings whose lists won votes",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(CONGO)
    p.artifact(raw.cached(SOURCE, NATIONAL_THRESHOLD), CENI)
    p.artifact(raw.cached(SOURCE, NATIONAL_SEATS), CENI)
    p.add("body", id=ASSEMBLY, polity_id=CONGO, kind="lower_house")
    p.name(ASSEMBLY, "National Assembly", "en", "official")
    p.name(ASSEMBLY, "Assemblée nationale", "fr", "native", False)
    for iso, name, _ in PROVINCES.values():
        body = provincial_assembly(iso)
        p.add("body", id=body, polity_id=CONGO, kind="council")
        p.name(body, f"Provincial Assembly of {name}", "en", "official")
        p.name(body, f"Assemblée provinciale de {name}", "fr", "native", False)
    for id, rules, note in (
        (
            PR_NATIONAL,
            {"formula": "hare", "threshold": {"percent": 1, "level": "list", "scope": "national"}},
            (
                "National deputies in circonscriptions with more than one seat are elected from open lists by "
                "proportional representation with the largest remainder. Each voter chooses one candidate, and a list's "
                "votes are the sum of its candidates' votes. Only lists with at least 1 percent of the valid votes "
                "nationally take part in the allocation (Loi électorale, as amended in 2022), and a list's seats go to "
                "its candidates in the order of their votes."
            ),
        ),
        (
            PR_PROVINCIAL,
            {"formula": "hare"},
            (
                "Provincial deputies in circonscriptions with more than one seat are elected from open lists by "
                "proportional representation with the largest remainder, among the lists with at least 3 percent of the "
                "valid votes in the province. Each voter chooses one candidate, a list's votes are the sum of its "
                "candidates' votes, and a list's seats go to its candidates in the order of their votes."
            ),
        ),
        (
            PLURALITY,
            {"formula": "fptp"},
            "In a circonscription with one seat, the candidate with the most votes is elected.",
        ),
    ):
        p.add("electoral_system", id=id, polity_id=CONGO, valid_from=date(2022, 6, 29), rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    _, lists, _ = national_lists()
    used = {row.sigle for rows in [*national().values(), *provincial().values()] for row in rows}
    for sigle in sorted(used | set(lists)):
        id = party(sigle)
        name = lists.get(sigle, (sigle, 0))[0] or sigle
        kind = "bloc" if name.startswith(PARTY_BLOCS) or sigle.endswith("-A") else "party"
        p.add("party", id=id, kind=kind, polity_id=CONGO)
        p.name(id, name, "fr", "official")
        if name != sigle:
            p.name(id, sigle, "fr", "abbreviation", False)
    return p


def _value(
    p: Package, result_set: str, unit_id: str, option: str | None, measure: int, amount: int, flags: int = 0
) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit_id,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=flags,
        value=amount,
    )


def _accounting(p: Package, result_set: str, unit_id: str, totals: Totals, declared: bool = True) -> None:
    for measure, amount in (
        (BALLOTS, totals.voters),
        (VALID, totals.valid),
        (INVALID, totals.invalid),
        (BLANK, totals.blank),
    ):
        if amount is None:
            continue
        _value(p, result_set, unit_id, None, measure, amount)
        if declared:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount)


def _note(p: Package, entity: str, kind: str, body: str) -> None:
    p.add("note", id=key("note", entity, kind, body[:60]), entity_id=entity, kind=kind, body_md=body, lang="en")


def _contest(
    p: Package,
    tag: str,
    election: str,
    path: str,
    place: Place,
    rows: list[Candidate],
    body: str,
    proportional: str,
    method: str,
    list_votes: bool,
) -> None:
    c = circs()[place]
    id = key("contest", tag, c.code)
    elected = sum(row.elected for row in rows)
    p.add(
        "contest",
        id=id,
        election_id=election,
        kind="list_allocation",
        body_id=body,
        district_area_id=area(c.scheme, c.code),
        magnitude=elected or None,
        round_number=1,
        electoral_system_id=None if not elected else PLURALITY if elected == 1 else proportional,
        default_result_kind="counted",
    )
    p.name(id, c.name, "fr", "official")
    p.add("slug", path=f"{path}/{changeset.slug(c.name)}", entity_id=id, is_current=True)
    result_set = key("result_set", tag, c.code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=id,
        stage="official",
        kind="counted",
        source_id=CENI,
        status="active",
        is_default=True,
    )
    _note(p, result_set, "methodology", method)
    if not elected:
        _note(
            p,
            result_set,
            "caveat",
            "The CENI's provisional seat attribution sheet for this circonscription marks no candidate as elected.",
        )
    where = unit(c.scheme, c.code)
    by_list: dict[str, list[Candidate]] = defaultdict(list)
    for row in rows:
        by_list[row.sigle].append(row)
    for sigle, members in by_list.items():
        option = key("option", tag, c.code, sigle)
        p.add("contest_option", id=option, contest_id=id, kind="list", party_id=party(sigle), ballot_label=sigle)
        if list_votes:
            _value(p, result_set, where, option, VOTES, sum(row.votes for row in members), DERIVED)
        seats = sum(row.elected for row in members)
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
        for row in members:
            tagged = (tag, c.code, sigle, row.name)
            person = key("person", *tagged)
            p.add("person", id=person, gender=GENDER[row.gender])
            p.name(person, row.name, "fr", "official")
            child = key("option", *tagged)
            p.add(
                "contest_option",
                id=child,
                contest_id=id,
                kind="candidate",
                parent_option_id=option,
                party_id=party(sigle),
                ballot_label=row.name,
            )
            _value(p, result_set, where, child, CANDIDATE_VOTES, row.votes)
            candidacy = key("candidacy", *tagged)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=child,
                role="list_member",
                ballot_name=row.name,
                member_of_party_id=party(sigle),
                is_independent=False,
            )
            if row.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")


NATIONAL_METHOD = (
    "From the CENI's provisional seat attribution sheets of January 2024 (Liste globale des candidats à la "
    "députation nationale avec score). Each sheet lists every candidate of the lists that reached the national "
    "threshold of 1 percent, with their votes, and marks the candidates elected. Candidates of lists below the "
    "threshold, and the circonscription's valid, invalid, and blank ballots, are not printed, so the votes here do "
    "not add up to the circonscription's valid votes. A list's votes are the sum of its candidates' votes, as the "
    "electoral law counts them, and are marked as derived. The candidates are listed by votes, not in ballot order."
)
NATIONAL_CAVEATS = (
    (
        "These are the CENI's provisional results, which the Constitutional Court then ruled on. The CENI published "
        "the national totals and the votes of the lists that reached the 1 percent threshold; the circonscriptions "
        "hold only the candidates of those lists."
    ),
    (
        "Masisi, Rutshuru (Nord-Kivu), and Kwamouth (Mai-Ndombe) did not vote, and the CENI annulled the vote in "
        "Masi-Manimba (Kwilu) and Yakoma (Nord-Ubangi), which voted again on 15 December 2024. The provisional sheets "
        "hold no results for Bomongo (Équateur), and mark no winner in Mobayi-Mbongo (Nord-Ubangi). The CENI's seat "
        "table counts 477 seats in all; the sheets mark 473 candidates as elected."
    ),
)


def national_election() -> Package:
    p = Package(
        f"{SOURCE}/an-2023",
        SOURCE,
        "The 20 December 2023 election of the national deputies of the Democratic Republic of the Congo by "
        "circonscription, with the candidates of the lists above the threshold, from the CENI's provisional results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CONGO, COUNTRY}
    p.artifact(raw.cached(SOURCE, NATIONAL), CENI)
    p.artifact(raw.cached(SOURCE, PAGES["national"]), CENI)
    for url in (NATIONAL_THRESHOLD, NATIONAL_SEATS):
        p.uses(raw.cached(SOURCE, url))
    p.add("election", id=NATIONAL_ELECTION, polity_id=CONGO, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=NATIONAL_PATH, entity_id=NATIONAL_ELECTION, is_current=True)
    p.name(NATIONAL_ELECTION, "2023 Democratic Republic of the Congo National Assembly election", "en", "official")
    p.name(NATIONAL_ELECTION, "Élection des députés nationaux de 2023", "fr", "native", False)
    for body in NATIONAL_CAVEATS:
        _note(p, NATIONAL_ELECTION, "caveat", body)
    totals, _, _ = national_lists()
    whole = key("result_set", "an", "national")
    p.add(
        "result_set",
        id=whole,
        election_id=NATIONAL_ELECTION,
        stage="official",
        kind="counted",
        source_id=CENI,
        status="active",
        is_default=True,
    )
    _note(
        p,
        whole,
        "methodology",
        "The national voters (total votants), valid ballots (suffrage national valablement exprimé), invalid "
        "ballots (votes annulés et sanctionnés), and blank ballots from the CENI's national threshold sheet.",
    )
    _accounting(p, whole, unit(NATION, "CD"), totals)
    for place, rows in national().items():
        _contest(p, "an", NATIONAL_ELECTION, NATIONAL_PATH, place, rows, ASSEMBLY, PR_NATIONAL, NATIONAL_METHOD, True)
    return p


PROVINCIAL_METHOD = (
    "From the CENI's provisional list of elected provincial deputies of January 2024, which prints only the elected "
    "candidates of each circonscription with their votes. The votes of the other candidates, and the "
    "circonscription's valid, invalid, and blank ballots, are not published. A list's seats are the number of its "
    "candidates marked elected."
)


def provincial_election() -> Package:
    p = Package(
        f"{SOURCE}/ap-2023",
        SOURCE,
        "The 20 December 2023 elections of the provincial deputies of the Democratic Republic of the Congo by "
        "circonscription, with the elected candidates' votes and each province's totals, from the CENI's provisional "
        "results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CONGO}
    for url in (PROVINCIAL, PROVINCIAL_THRESHOLD):
        p.artifact(raw.cached(SOURCE, url), CENI)
    p.artifact(raw.cached(SOURCE, PAGES["provincial"]), CENI)
    p.add(
        "election", id=PROVINCIAL_ELECTION, polity_id=CONGO, kind="general", date_from=DAY, date_to=DAY, status="held"
    )
    p.add("slug", path=PROVINCIAL_PATH, entity_id=PROVINCIAL_ELECTION, is_current=True)
    p.name(PROVINCIAL_ELECTION, "2023 Democratic Republic of the Congo provincial assembly elections", "en", "official")
    p.name(PROVINCIAL_ELECTION, "Élections des députés provinciaux de 2023", "fr", "native", False)
    _note(
        p,
        PROVINCIAL_ELECTION,
        "caveat",
        "These are the CENI's provisional results, with only the elected candidates of each circonscription. "
        "Masisi, Rutshuru, and Kwamouth did not vote, the CENI annulled the vote in Masi-Manimba and Yakoma, and "
        "the provisional list holds no elected deputies for a few other circonscriptions. The customary chiefs "
        "co-opted to each assembly are not elected here.",
    )
    totals = provincial_totals()
    whole = key("result_set", "ap", "provinces")
    p.add(
        "result_set",
        id=whole,
        election_id=PROVINCIAL_ELECTION,
        stage="official",
        kind="counted",
        source_id=CENI,
        status="active",
        is_default=True,
    )
    _note(
        p,
        whole,
        "methodology",
        "Each province's voters (total votants), valid ballots, and blank ballots from the CENI's provincial "
        "threshold sheets; the sheets print no invalid ballots.",
    )
    for province, (iso, _, _) in PROVINCES.items():
        _accounting(p, whole, unit(PROVINCE, iso), totals[province], declared=False)
    by_province: dict[str, dict[Place, list[Candidate]]] = defaultdict(dict)
    for place, rows in provincial().items():
        by_province[place.province][place] = rows
    for province, (iso, name, _) in PROVINCES.items():
        election = key("election", "ap", 2023, iso)
        path = f"{PROVINCIAL_PATH}/{changeset.slug(name)}"
        p.add(
            "election",
            id=election,
            polity_id=CONGO,
            parent_id=PROVINCIAL_ELECTION,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            status="held",
        )
        p.add("slug", path=path, entity_id=election, is_current=True)
        p.name(election, f"2023 {name} provincial assembly election", "en", "official")
        p.name(election, f"Élection des députés provinciaux de 2023, {name}", "fr", "native", False)
        for place, rows in by_province[province].items():
            _contest(
                p, "ap", election, path, place, rows, provincial_assembly(iso), PR_PROVINCIAL, PROVINCIAL_METHOD, False
            )
    return p
