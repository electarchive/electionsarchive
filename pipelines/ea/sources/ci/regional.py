"""Côte d'Ivoire: regional council elections of 2013, 2018, and 2023 from the Commission Électorale Indépendante.

The commission publishes each election as a PDF table with one block per region: the region's registration and
turnout figures on the left and its competing lists (2013) or candidates heading lists (2018, 2023) on the right.
The blocks are laid out by position, so the parser reads word coordinates from the text layer, rebuilds each row, and
assigns candidate rows to regions by walking both in table order until each region's candidate votes equal its own
printed valid votes.

Each polling day is one package, `ci.cei/regional-<date>`, with one contest per region. The 21 July 2013 and 2 December
2023 polls reran regions whose first poll was annulled; each is its own election. Municipal councils were elected on
the same days, and one polling day is one election: where the municipal packages already hold a day's election, the
regional contests attach to it.
"""

import functools
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.ci.cei import SOURCE, key

COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fCI"))
UPLOADS = "https://www.cei.ci/wp-content/uploads"
PAGE = "https://www.cei.ci/resultats-recents/"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

FILES = {
    "2013-april": f"{UPLOADS}/2025/09/RESULTAT_REGIONALE-2013-FINAL.pdf",
    "2013-july": f"{UPLOADS}/2020/04/ELECTION-DES-CONSEILLERS-REGIONAUX-2013.pdf",
    "2018": f"{UPLOADS}/2020/04/1-ELECTION-DES-CONSEILLERS-REGIONAUX-1.pdf",
    "2023-september": f"{UPLOADS}/2023/09/Regionales_2023.pdf",
    "2023-december": f"{UPLOADS}/2023/12/RESULTATS-ER-PARTIELS-2023.pdf",
}
NUMBER = re.compile(r"^\d{1,3}$")
PERCENT = re.compile(r"^\d{1,3},\d{1,2}%$")


@dataclass(frozen=True)
class Layout:
    """Where each column of a table sits on the page, in points."""

    width: float  # a percentage right of this point is a candidate's, not a region's
    right: float  # the right block starts here
    head: float  # candidate names start here (list names lie between right and head)
    party: float  # party labels start here
    score: float  # votes start here
    dossier: bool = False  # the right block starts with a candidate dossier number
    header: float = 190  # words above this point can be headings
    blank_in_valid: bool = True  # the printed valid votes include the blank ballots


LAYOUTS = {
    "2013": Layout(700, 385, 535, 655, 700, header=130, blank_in_valid=False),
    "2013-july": Layout(700, 403, 553, 673, 720, header=145, blank_in_valid=False),
    "2018": Layout(1000, 650, 750, 650, 1000),
    "2023": Layout(1000, 672, 788, 715, 1025, header=212, dossier=True),
}
WORD = re.compile(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*)</word>')
ENTITIES = {"&apos;": "'", "&amp;": "&", "&quot;": '"', "&lt;": "<", "&gt;": ">"}
KNOWN_HEADERS = {"NUM", "DOS", "GRP./PART.", "POL.", "NOM", "CAND", "SCORE", "POUR", "RESULTAT", "%"}


@dataclass
class Word:
    page: int
    x0: float
    y0: float
    x1: float
    text: str


@dataclass
class Row:
    """One region's printed figures."""

    code: str
    name: str
    numbers: list[int]
    day: date | None = None
    y: float = 0.0
    page: int = 0


@dataclass
class Candidate:
    page: int
    y: float
    list_name: str
    name: str
    party: str
    score: int
    percent: float
    dossier: str
    result: str


@dataclass
class Region:
    code: str
    name: str
    figures: dict[str, int]
    candidates: list[Candidate] = field(default_factory=list)
    day: date | None = None
    y: float = 0.0


@functools.cache
def words(name: str) -> tuple[Word, ...]:
    path = raw.cached(SOURCE, FILES[name]).path
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    found: list[Word] = []
    page = 0
    for line in out.splitlines():
        if "<page " in line:
            page += 1
        match = WORD.search(line)
        if match:
            text = match[5]
            for entity, char in ENTITIES.items():
                text = text.replace(entity, char)
            found.append(Word(page, float(match[1]), float(match[2]), float(match[3]), text))
    return tuple(found)


def lines(items: list[Word], tolerance: float = 0.5) -> list[list[Word]]:
    """Group words that share a baseline, left to right, top to bottom."""
    rows: list[list[Word]] = []
    for word in sorted(items, key=lambda w: (w.page, w.y0, w.x0)):
        if rows and rows[-1][0].page == word.page and abs(rows[-1][0].y0 - word.y0) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w.x0) for row in rows]


def integers(row: list[Word]) -> list[str]:
    """Join the thousands groups of a printed number (`1`, `987`, `548` is 1987548) and keep other tokens."""
    out: list[str] = []
    previous: Word | None = None
    for word in row:
        digits = word.text.isdigit()
        if (
            digits
            and previous is not None
            and previous.text.isdigit()
            and len(word.text) == 3
            and word.x0 - previous.x1 < 4
            and out
            and out[-1].isdigit()
        ):
            out[-1] += word.text
        else:
            out.append(word.text)
        previous = word
    return out


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", plain.upper())


def fix(text: str) -> str:
    return " ".join(text.split())


HEADER = {
    "TOTAL", "TOTAL/POURCENTAGE", "POURCENTAGE", "Pourcentage", "Total", "REGION", "Region", "RESULTAT", "Resultat",
    "NOM", "CAND", "POL.", "GRP./PART.", "POUR", "SCORE", "Score", "NUM", "DOS", "INTITULE", "SUFFRAGES", "OBTENUS",
    "POURCENTA", "TETE",
}  # fmt: skip


def figures(layout_key: str, numbers: list[int]) -> dict[str, int]:
    """Name the printed figures of a region row, whose columns differ by table (percentages are already dropped)."""
    if layout_key.startswith("2013"):
        bureaus, registered, cast, invalid, valid = numbers
        return {"stations": bureaus, "registered": registered, "cast": cast, "invalid": invalid, "valid": valid}
    if layout_key == "2018":
        bureaus, registered, _, _, cast, invalid, blank, valid = numbers
        return {
            "stations": bureaus,
            "registered": registered,
            "cast": cast,
            "invalid": invalid,
            "blank": blank,
            "valid": valid,
        }
    bureaus, _, _, registered, _, _, cast, invalid, valid, blank = numbers
    return {
        "stations": bureaus,
        "registered": registered,
        "cast": cast,
        "invalid": invalid,
        "blank": blank,
        "valid": valid,
    }


MINIMUM = {"2013": 5, "2013-july": 5, "2018": 8, "2023": 10}
DAYS = (
    (re.compile(r"JUILLET 2013", re.IGNORECASE), date(2013, 7, 21)),
    (re.compile(r"Dec 2018", re.IGNORECASE), date(2018, 12, 16)),
)


def rows(name: str, layout_key: str) -> tuple[list[Row], dict[int, float]]:
    layout = LAYOUTS[layout_key]
    items = words(name)
    top: dict[int, float] = {}
    for word in items:
        if word.text in HEADER and word.y0 < layout.header:
            top[word.page] = max(top.get(word.page, 0), word.y0 + 5)
    left = [w for w in items if w.x0 < layout.right - 3 and w.y0 > top.get(w.page, 0)]
    found: list[Row] = []
    labels: list[tuple[int, float, str]] = []
    for row in lines(left):
        tokens = ["0" if t == "-" and layout_key in ("2018", "2023") else t for t in integers(row)]
        numbers = [int(t) for t in tokens if t.isdigit()]
        text = [t for t in tokens if not t.isdigit() and not PERCENT.match(t)]
        if len(numbers) < MINIMUM[layout_key]:
            if tokens and len(numbers) < 3:
                labels.append((row[0].page, row[0].y0, fix(" ".join(tokens))))
            continue
        code = ""
        if layout_key.startswith("2013"):
            if not tokens[0].isdigit() or len(tokens[0]) != 2:
                continue
            code, numbers = tokens[0], numbers[1:]
        found.append(Row(code, fix(" ".join(text)), numbers, None, row[0].y0, row[0].page))
    for row in found:
        near = [(abs(y - row.y), text) for page, y, text in labels if page == row.page and abs(y - row.y) < 15]
        for _, text in sorted(near):
            day = next((d for pattern, d in DAYS if pattern.search(text)), None)
            if day is not None:
                row.day = row.day or day
            elif not row.name and "(" not in text:
                row.name = text
    return found, top


def candidates(name: str, layout_key: str, top: dict[int, float]) -> list[Candidate]:
    layout = LAYOUTS[layout_key]
    right = [w for w in words(name) if w.x0 >= layout.right - 3 and w.y0 > top.get(w.page, 0)]
    scored: list[tuple[int, float, list[Word]]] = []
    for line in lines(right, 1.2):
        if any(PERCENT.match(w.text) and w.x0 >= layout.width for w in line):
            scored.append((line[0].page, line[0].y0, line))
    found: list[Candidate] = []
    for page, y, line in scored:
        tokens = integers([w for w in line if w.x0 >= layout.score - 6])
        percent = next(t for t in tokens if PERCENT.match(t))
        earlier = [t for t in tokens[: tokens.index(percent)] if t.isdigit()]
        if not earlier and percent == "0,00%":
            continue  # a list of a region that did not vote that day: the table prints a percentage and no votes
        score = int(earlier[-1])
        result = " ".join(t for t in tokens[tokens.index(percent) + 1 :])
        end = layout.head - 2 if layout.head > layout.party else layout.score - 6
        party = " ".join(w.text for w in line if layout.party - 6 <= w.x0 < end)
        dossier = " ".join(w.text for w in line if layout.dossier and w.x0 < layout.party - 6)
        found.append(
            Candidate(page, y, "", "", fix(party), score, float(percent[:-1].replace(",", ".")), dossier, result)
        )
    # wrapped names sit above or below the score line: give each word to the nearest score line on its page
    names: dict[int, list[tuple[float, Word]]] = {i: [] for i in range(len(found))}
    lists: dict[int, list[tuple[float, Word]]] = {i: [] for i in range(len(found))}
    listed = layout.head < layout.party  # the 2013 tables print a list's name before its head
    for word in right:
        if (
            word.x0 >= layout.score - 6
            or (listed and word.x0 >= layout.party - 6)
            or (not listed and word.x0 < layout.head - 2)
        ):
            continue
        near = [(abs(c.y - word.y0), i) for i, c in enumerate(found) if c.page == word.page]
        if not near or min(near)[0] > 25:
            continue
        (lists if word.x0 < layout.head - 2 else names)[min(near)[1]].append((word.y0 * 1000 + word.x0, word))
    for i, c in enumerate(found):
        c.name = fix(" ".join(w.text for _, w in sorted(names[i], key=lambda t: (t[1].y0, t[1].x0))))
        c.list_name = fix(" ".join(w.text for _, w in sorted(lists[i], key=lambda t: (t[1].y0, t[1].x0))))
    return found


def assign(found: list[Row], cands: list[Candidate], layout_key: str) -> list[Region]:
    """Split the candidate rows between the regions, in table order, so that each region's votes equal its own."""
    layout = LAYOUTS[layout_key]
    regions: list[Region] = []
    for row in found:
        regions.append(Region(row.code, row.name, figures(layout_key, row.numbers), [], row.day, row.y))
    targets = [r.figures["valid"] - (r.figures.get("blank", 0) if layout.blank_in_valid else 0) for r in regions]
    count, total = len(regions), len(cands)
    infinity = float("inf")
    # best[i][j]: the cheapest way to give the first j candidates to the first i regions
    best = [[infinity] * (total + 1) for _ in range(count + 1)]
    back = [[0] * (total + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for i in range(1, count + 1):
        for j in range(total + 1):
            for k in range(j + 1):
                if best[i - 1][k] == infinity:
                    continue
                chosen = cands[k:j]
                cost = best[i - 1][k] + 5 * abs(sum(c.score for c in chosen) - targets[i - 1])
                cost += sum(abs(c.y - regions[i - 1].y) / 20 for c in chosen)
                if cost < best[i][j]:
                    best[i][j], back[i][j] = cost, k
    j = total
    for i in range(count, 0, -1):
        k = back[i][j]
        regions[i - 1].candidates = cands[k:j]
        j = k
    return regions


def parse_table(name: str, layout_key: str) -> list[Region]:
    found, top = rows(name, layout_key)
    seen: set[str] = set()
    unique: list[Row] = []
    for row in found:
        label = row.code or fold(row.name)
        if label not in seen:
            seen.add(label)
            unique.append(row)
    return assign(unique, candidates(name, layout_key, top), layout_key)


def printed_total(name: str, layout_key: str) -> dict[str, int]:
    """The national row of a table, as printed."""
    layout = LAYOUTS[layout_key]
    first = [w for w in words(name) if w.x0 < layout.right - 3]
    for row in lines(first):
        tokens = integers(row)
        if tokens[0].upper().startswith("TOTAL"):
            numbers = [int(t) for t in tokens if t.isdigit()]
            if len(numbers) >= MINIMUM[layout_key]:
                return figures(layout_key, numbers[-MINIMUM[layout_key] :])
    raise ValueError(f"{name}: no national row")


def base(region: Region) -> int:
    """The votes a printed percentage divides: the table's valid votes, which from 2018 include the blank ballots."""
    return region.figures["valid"]


def listed(region: Region, layout_key: str) -> int:
    """The votes of a region's lists, which a table's valid votes must equal (2018 and 2023 count blanks apart)."""
    return region.figures["valid"] - (region.figures.get("blank", 0) if LAYOUTS[layout_key].blank_in_valid else 0)


NAMES = {
    "01": "Gbôklé",
    "02": "Nawa",
    "03": "San-Pédro",
    "04": "Indénié-Djuablin",
    "05": "Sud-Comoé",
    "06": "Folon",
    "07": "Kabadougou",
    "08": "Gôh",
    "09": "Lôh-Djiboua",
    "10": "Bélier",
    "11": "Iffou",
    "12": "Moronou",
    "13": "N'Zi",
    "14": "Agnéby-Tiassa",
    "15": "Grands-Ponts",
    "16": "La Mé",
    "17": "Cavally",
    "18": "Guémon",
    "19": "Tonkpi",
    "20": "Haut-Sassandra",
    "21": "Marahoué",
    "22": "Bagoué",
    "23": "Poro",
    "24": "Tchologo",
    "25": "Gbêkê",
    "26": "Hambol",
    "27": "Bafing",
    "28": "Béré",
    "29": "Worodougou",
    "30": "Bounkani",
    "31": "Gontougo",
}


@functools.cache
def codes() -> dict[str, str]:
    """Region codes, numbered as the commission's 2013 table numbers them, by region name without accents."""
    return {fold(row.name): row.code for row in rows("2013-april", "2013")[0] if row.code}


def region_code(region: Region) -> str:
    if region.code:
        return region.code
    return codes()[fold(region.name)]


@dataclass(frozen=True)
class Plan:
    day: date
    file: str
    layout: str
    kind: str  # local or by_election
    rerun_of: date | None = None
    revises: str | None = None  # a second table that disagrees with this one for some regions
    revises_layout: str | None = None


PLANS = [
    Plan(date(2013, 4, 21), "2013-april", "2013", "local", revises="2013-july", revises_layout="2013-july"),
    Plan(date(2013, 7, 21), "2013-july", "2013-july", "by_election", rerun_of=date(2013, 4, 21)),
    Plan(date(2018, 10, 13), "2018", "2018", "local"),
    Plan(date(2018, 12, 16), "2018", "2018", "by_election"),
    Plan(date(2023, 9, 2), "2023-september", "2023", "local"),
    Plan(date(2023, 12, 2), "2023-december", "2023", "by_election"),
]
LAYOUT_OF = {
    "2013-april": "2013",
    "2013-july": "2013-july",
    "2018": "2018",
    "2023-september": "2023",
    "2023-december": "2023",
}
TITLES = {
    "2013-april": "Election des conseillers régionaux, scrutin du 21 avril 2013: résultats du scrutin (25 avril 2013)",
    "2013-july": "Election des conseillers régionaux, scrutins des 21 avril et 21 juillet 2013: résultats des scrutins",
    "2018": "Election des conseillers régionaux, scrutins du 13 octobre et du 16 décembre 2018",
    "2023-september": "Election des conseillers régionaux, scrutin du 2 septembre 2023: résultats du scrutin",
    "2023-december": "Election des conseillers régionaux, scrutin partiel du 2 décembre 2023: résultats du scrutin",
}
BODY = key("body", "regional-council")
REGISTRY = "ci.cei/regional-registry"
GEO = "ci.cei/geo"
ALIASES = {"PPACI": "PPA-CI"}
UNAFFILIATED = {"INDEPENDANT", ""}
# polling days whose election the municipal packages already hold: one polling day is one election, so the regional
# packages attach their contests to it and leave its record, names, and slug to those packages
SHARED_DAYS = {date(2013, 4, 21), date(2018, 10, 13), date(2018, 12, 16), date(2023, 9, 2)}
# parties the commission's other tables already list under the same label, with the same id
SHARED_PARTIES = {"AIRD", "FPI", "PDCI-RDA", "PDCI-RDA/PPA-CI", "PPA-CI", "RDR", "REEL.CI", "RHDP", "UDPCI"}
METHOD = (
    "Counted by the Commission Électorale Indépendante per region, as printed in its results table. Each region "
    "elects its council in one circumscription, from lists that each name a head of list. Registered voters are the "
    "electoral roll, ballots cast are the voters, and invalid ballots are the null ballots. {blank}The table prints "
    "no seats."
)


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"ci.cei/regional-{plan.day}" for plan in PLANS)]


SHAPES = "ci.geoboundaries"
SHAPE_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/CIV/ADM2/"
    "geoBoundaries-CIV-ADM2_simplified.geojson"
)
SHAPE_SOURCE = key("source", "geoboundaries", "CIV", "ADM2")
SHAPE_ALIASES = {"ME": "LAME"}  # geoBoundaries drops the article of La Mé
PLAN_FROM = date(2011, 9, 28)  # the decree that set up the 31 regions


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SHAPES, SHAPE_URL, suffix=".geojson")


def shapes() -> dict[str, shapely.Geometry]:
    """The geoBoundaries outline of each region, by the commission's region code; the two autonomous districts hold no
    regional council and are left out."""
    data = json.loads(raw.cached(SHAPES, SHAPE_URL).path.read_text(encoding="utf-8"))
    by_name = {fold(name): code for code, name in NAMES.items()}
    out: dict[str, shapely.Geometry] = {}
    for feature in data["features"]:
        name = fold(feature["properties"]["shapeName"])
        code = by_name.get(SHAPE_ALIASES.get(name, name))
        if code is not None:
            out[code] = shapely.geometry.shape(feature["geometry"])
    assert set(out) == set(NAMES), sorted(set(NAMES) - set(out))
    return out


def parse() -> None:
    geo().write()
    registry().write()
    for plan in PLANS:
        build(plan).write()


def emit() -> None:
    package.run("load", packages())


def source_id(name: str) -> str:
    return key("source", "regional", name)


def area(code: str) -> str:
    return key("area", "ci.region", code)


def day_regions(plan: Plan) -> list[Region]:
    """The regions that voted on a plan's day: a table lists every region it covers and marks the ones that voted late."""
    regions = parse_table(plan.file, plan.layout)
    if plan.file == "2013-july":
        return [r for r in regions if r.day == plan.day]
    if plan.file == "2018":
        return [r for r in regions if (r.day or date(2018, 10, 13)) == plan.day]
    return regions


def party_label(text: str) -> str | None:
    label = ALIASES.get(" ".join(text.upper().split()), " ".join(text.upper().split()))
    return None if label in UNAFFILIATED else label


def party_labels() -> list[str]:
    found: set[str] = set()
    for plan in PLANS:
        for region in parse_table(plan.file, plan.layout):
            found |= {label for c in region.candidates if (label := party_label(c.party))}
    return sorted(found)


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Côte d'Ivoire's 31 regions, numbered as the Commission Électorale Indépendante's regional council tables "
        "number them",
    )
    p.external.add(COUNTRY)
    p.add("scheme", code="ci.region", polity_id=COUNTRY, kind="administrative", rank=1, noun="region")
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="aggregator",
        publisher="geoBoundaries (William & Mary geoLab), from World Bank data",
        title="Second-level administrative boundaries of Côte d'Ivoire (geoBoundaries gbOpen CIV ADM2, simplified)",
        url=SHAPE_URL,
        license="CC BY 4.0",
        attribution="geoBoundaries gbOpen; World Bank",
    )
    p.artifact(raw.cached(SHAPES, SHAPE_URL), SHAPE_SOURCE)
    outlines = shapes()
    for code, name in NAMES.items():
        id = area(code)
        p.add("area", id=id, scheme_code="ci.region")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "fr", "official")
        parts = [
            part
            for part in shapely.get_parts(shapely.make_valid(outlines[code]))
            if shapely.get_type_id(part) in (3, 6)
        ]
        wgs = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=PLAN_FROM,
            geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(wgs), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=SHAPE_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The regional councils of Côte d'Ivoire and the parties on their lists, with the Commission Électorale "
        "Indépendante's regional council tables as sources",
    )
    p.external.add(COUNTRY)
    for name, url in FILES.items():
        id = source_id(name)
        p.add(
            "source",
            id=id,
            kind="official_body",
            publisher="Commission Électorale Indépendante",
            title=TITLES[name],
            url=url,
            license="Official publication; reuse terms are not stated",
            attribution="Commission Électorale Indépendante de Côte d'Ivoire",
        )
        p.artifact(raw.cached(SOURCE, url), id)
    p.add("body", id=BODY, polity_id=COUNTRY, kind="council")
    p.name(BODY, "Regional councils of Côte d'Ivoire", "en", "official")
    p.name(BODY, "Conseils régionaux de Côte d'Ivoire", "fr", "native", False)
    for label in party_labels():
        if label in SHARED_PARTIES:
            continue
        id = p.id("party", label)
        p.add("party", id=id, kind="bloc" if "/" in label else "party", polity_id=COUNTRY)
        p.name(id, label, "fr", "abbreviation")
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def sig(region: Region) -> list[tuple[int, str, str, str]]:
    return [(c.score, c.name, c.party, c.list_name) for c in region.candidates]


def own_election(p: Package, plan: Plan, election: str, path: str) -> None:
    """Write the record, names, and slug of an election no other package holds."""
    main = plan.kind == "local"
    year = plan.day.year
    p.add(
        "election",
        id=election,
        polity_id=COUNTRY,
        kind=plan.kind,
        date_from=plan.day,
        date_to=plan.day,
        status="held",
        rerun_of_id=p.id("election", plan.rerun_of) if plan.rerun_of else None,
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    when = f"{plan.day.day} {plan.day.strftime('%B %Y')}"
    p.name(
        election,
        f"{year} Ivorian regional council elections" if main else f"{year} Ivorian regional council poll of {when}",
        "en",
        "official",
    )
    p.name(
        election,
        f"Élection des conseillers régionaux de {year}"
        if main
        else f"Élection des conseillers régionaux, scrutin du {plan.day.day:02d}/{plan.day.month:02d}/{year}",
        "fr",
        "native",
        False,
    )


def build(plan: Plan) -> Package:
    key_ = f"ci.cei/regional-{plan.day}"
    depends = [GEO, REGISTRY]
    if plan.rerun_of:
        depends.append(f"ci.cei/regional-{plan.rerun_of}")
    regions = day_regions(plan)
    revised: dict[str, Region] = {}
    if plan.revises and plan.revises_layout:
        before = {region_code(r): r for r in regions}
        for other in parse_table(plan.revises, plan.revises_layout):
            code = region_code(other)
            if (
                other.day is None
                and code in before
                and (other.figures, sig(other)) != (before[code].figures, sig(before[code]))
            ):
                revised[code] = other
    p = Package(
        key_,
        SOURCE,
        f"The {plan.day.year} regional council election{'s' if len(regions) > 2 else ''} of {plan.day} in "
        f"{len(regions)} region{'s' if len(regions) > 1 else ''} of Côte d'Ivoire, from the Commission Électorale "
        "Indépendante's results table",
        depends=depends,
    )
    p.external.add(COUNTRY)
    p.uses(raw.cached(SOURCE, FILES[plan.file]))
    if revised and plan.revises:
        p.uses(raw.cached(SOURCE, FILES[plan.revises]))
    election = p.id("election", plan.day)
    year = plan.day.year
    p.external.update(p.id("party", label) for label in SHARED_PARTIES)
    if plan.rerun_of in SHARED_DAYS:
        p.external.add(p.id("election", plan.rerun_of))
    path = f"ci/{plan.day}-regional"
    if plan.day in SHARED_DAYS:
        p.external.add(election)
    else:
        own_election(p, plan, election, path)
    for region in regions:
        code = region_code(region)
        name = NAMES[code]
        contest = p.id("contest", plan.day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODY,
            district_area_id=area(code),
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Ivorian regional council election, {name} region", "en", "official")
        p.name(contest, f"Conseil régional de {name}", "fr", "native", False)
        p.add("slug", path=f"{path}/{fold(name).lower()}", entity_id=contest, is_current=True)
        unit = p.id("unit", plan.day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=COUNTRY,
            scheme_code="ci.region",
            code=f"{plan.day}:{code}",
            area_relation="equals",
            area_id=area(code),
            kind="standard",
        )
        p.name(unit, name, "fr", "official")
        other = revised.get(code)
        first = result_set(p, plan, election, contest, unit, region, plan.file, code, superseded=other is not None)
        if other is not None and plan.revises:
            result_set(p, plan, election, contest, unit, other, plan.revises, code, replaces=first)
        if plan.rerun_of:
            p.add(
                "note",
                id=p.id("note", contest, "rerun"),
                entity_id=contest,
                kind="caveat",
                body_md=f"The region voted on {plan.rerun_of.day} {plan.rerun_of.strftime('%B %Y')} as well, and the "
                "commission's table of that poll prints a result for it. The poll of this day is stored apart.",
                lang="en",
            )
    off = [
        (NAMES[region_code(r)], r.figures)
        for r in regions
        if r.figures["cast"] != r.figures["invalid"] + r.figures["valid"]
    ]
    if off:
        p.accept(
            "accounting.ballots_sum",
            "; ".join(
                f"in {name} the table prints {f['invalid']:,} null ballots, but its ballots cast ({f['cast']:,}) less "
                f"its valid votes ({f['valid']:,}) leave {f['cast'] - f['valid']:,}"
                for name, f in off
            )
            + "; stored as printed",
        )
    if plan.file == "2023-september":
        p.add(
            "note",
            id=p.id("note", election, "guemon"),
            entity_id=election,
            kind="caveat",
            body_md="The table lists the candidates of the Guémon region without votes or figures. The region voted "
            "on 2 December 2023.",
            lang="en",
        )
    return p


def result_set(
    p: Package,
    plan: Plan,
    election: str,
    contest: str,
    unit: str,
    region: Region,
    file: str,
    code: str,
    superseded: bool = False,
    replaces: str | None = None,
) -> str:
    layout = LAYOUTS[LAYOUT_OF[file]]
    id = p.id("result_set", plan.day, code, file)
    p.add(
        "result_set",
        id=id,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source_id(file),
        status="superseded" if superseded else "active",
        is_default=not superseded,
        completeness=1.0,
        supersedes_id=replaces,
    )
    blank = (
        "Blank ballots are counted apart from the valid votes for lists; the percentages divide the valid votes and "
        "the blank ballots together. "
        if "blank" in region.figures
        else "The percentages divide the valid votes. "
    )
    p.add(
        "note",
        id=p.id("note", id),
        entity_id=id,
        kind="methodology",
        body_md=METHOD.format(blank=blank),
        lang="en",
    )
    if superseded or replaces:
        p.add(
            "note",
            id=p.id("note", id, "revision"),
            entity_id=id,
            kind="caveat",
            body_md=(
                "The commission published a later table, which also holds the poll of 21 July 2013, with other "
                "figures for this region; that table's result set supersedes this one."
                if superseded
                else "The commission's earlier table, dated 25 April 2013, prints other figures for this region. "
                "This later table, which also holds the poll of 21 July 2013, supersedes it."
            ),
            lang="en",
        )
    figures = region.figures
    listed_votes = figures["valid"] - (figures.get("blank", 0) if layout.blank_in_valid else 0)
    put(p, id, unit, None, 2, figures["registered"])
    put(p, id, unit, None, 3, figures["cast"])
    put(p, id, unit, None, 5, figures["invalid"])
    if "blank" in figures:
        put(p, id, unit, None, 6, figures["blank"])
    put(p, id, unit, None, 4, listed_votes)
    for position, candidate in enumerate(region.candidates, 1):
        option = p.id("option", plan.day, code, file, position)
        label = party_label(candidate.party)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=p.id("party", label) if label else None,
            ballot_position=position,
            ballot_label=candidate.list_name or candidate.name,
        )
        person = p.id("person", plan.day, code, file, position)
        p.add("person", id=person)
        p.name(person, candidate.name, "fr", "official")
        p.add(
            "candidacy",
            id=p.id("candidacy", plan.day, code, file, position),
            person_id=person,
            option_id=option,
            role="head",
            list_position=1,
            ballot_name=candidate.name,
            member_of_party_id=p.id("party", label) if label else None,
            is_independent=candidate.party.upper() == "INDEPENDANT",
        )
        if candidate.dossier:
            p.add("external_id", entity_id=option, scheme="ci.cei.dossier", value=candidate.dossier)
        put(p, id, unit, option, 1, candidate.score)
        if candidate.result:
            p.add(
                "outcome",
                result_set_id=id,
                option_id=option,
                kind="elected" if candidate.result.lower() == "vainqueur" else "lost",
            )
        elif any(c.result for c in region.candidates):
            p.add("outcome", result_set_id=id, option_id=option, kind="lost")
    return id


def totals() -> list[str]:
    """Compare each table's printed national row with the sum of its regions, for the run report."""
    out = []
    for name, layout_key in (
        ("2013-april", "2013"),
        ("2013-july", "2013-july"),
        ("2018", "2018"),
        ("2023-september", "2023"),
        ("2023-december", "2023"),
    ):
        regions = parse_table(name, layout_key)
        printed = printed_total(name, layout_key)
        diffs = {k: sum(r.figures[k] for r in regions) - v for k, v in printed.items()}
        diffs = {k: d for k, d in diffs.items() if d}
        out.append(f"{name}: {len(regions)} regions; {'equal' if not diffs else f'differs {diffs}'}")
    return out
