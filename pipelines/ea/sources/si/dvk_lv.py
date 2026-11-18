"""Slovenian local elections of 2006-2022: municipal councils and mayors, from the State Election Commission's
archived result sites (https://www.dvk-rs.si/arhivi/lv<year>/ and /arhivi/volitve2022/lv2022/).
"""

import difflib
import html
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Any, cast

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.si.dvk import ARCHIVE, SLOVENIA, SOURCE, key

HTML_YEARS = {
    2006: f"{ARCHIVE}/lv2006",
    2010: f"{ARCHIVE}/lv2010",
    2014: f"{ARCHIVE}/lv2014",
    2018: f"{ARCHIVE}/lv2018",
}
LV2022 = f"{ARCHIVE}/volitve2022/lv2022/data"


def _text(url: str, encoding: str = "cp1250") -> str:
    return raw.cached(SOURCE, url).path.read_bytes().decode(encoding, errors="replace")


@cache
def pages(year: int) -> list[str]:
    """The result page of every municipality, in the order of the commission's list."""
    listing = f"{HTML_YEARS[year]}/rezultati/seznam_obcin.html"
    found = re.findall(r"href=[\"'](obcina_[a-z0-9_-]+\.html)[\"']", _text(listing))
    return [f"{HTML_YEARS[year]}/rezultati/{name}" for name in dict.fromkeys(found) if not name.endswith("_ckvs.html")]


def codes_2022() -> list[int]:
    data = json.loads(raw.cached(SOURCE, f"{LV2022}/prvikrog/data.json").path.read_bytes())
    return sorted(int(row["st"]) for row in data["slovenija"]["obcine"])


def runoffs_2022() -> list[int]:
    data = json.loads(raw.cached(SOURCE, f"{LV2022}/data.json").path.read_bytes())
    return sorted(int(code) for code in data["nastavitve"]["drugi_krog_obcine"])


def json_urls() -> list[str]:
    urls = [f"{LV2022}/prvikrog/data.json", f"{LV2022}/data.json"]
    urls += [f"{LV2022}/prvikrog/obcine-data/data_{code:03d}.json" for code in codes_2022()]
    return urls + [f"{LV2022}/obcine-data/data_{code:03d}.json" for code in runoffs_2022()]


def fetch() -> None:
    for year, base in HTML_YEARS.items():
        for page in ("seznam_obcin", "zbirni_podatki"):
            raw.fetch(SOURCE, f"{base}/rezultati/{page}.html", suffix=".html")
        raw.fetch_many(SOURCE, pages(year), suffix=".html", workers=6)
    for urls in EARLY.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".html")
    raw.fetch(SOURCE, RUNOFF_1998, suffix=".html")
    raw.fetch(SOURCE, INDEX_1998, suffix=".html")
    for name in ("prvikrog/data.json", "data.json"):
        raw.fetch(SOURCE, f"{LV2022}/{name}", suffix=".json")
    urls = [f"{LV2022}/prvikrog/obcine-data/data_{code:03d}.json" for code in codes_2022()]
    urls += [f"{LV2022}/obcine-data/data_{code:03d}.json" for code in runoffs_2022()]
    raw.fetch_many(SOURCE, urls, suffix=".json", workers=6)
    raw.fetch(SOURCE, GEOBOUNDARIES, suffix=".geojson")


# parsing -----------------------------------------------------------------------------------------------------------


@dataclass
class Candidate:
    name: str
    proposer: str
    votes: int
    status: str | None  # elected, advanced, lot, or None
    code: str | None = None  # the commission's party code (2022)


@dataclass
class ListRow:
    name: str
    votes: int
    seats: int
    code: str | None = None


@dataclass
class Turnout:
    registered: int | None = None
    voters: int | None = None  # voted by the roll (HTML) or in all (2022)
    certificates: int | None = None
    cast: int | None = None
    valid: int | None = None
    invalid: int | None = None


@dataclass
class Section:
    kind: str  # mayor, council, italian, hungarian, roma, slovene
    round: int = 1
    unit: int = 1
    candidates: list[Candidate] = field(default_factory=list)
    lists: list[ListRow] = field(default_factory=list)


@dataclass
class Municipality:
    year: int
    code: int
    name: str
    url: str
    turnout: dict[int, Turnout] = field(default_factory=dict)
    days: dict[int, date] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    runoff_url: str | None = None
    urban: bool = False
    counted: dict[int, float] = field(default_factory=dict)


ROUNDS = {
    1998: (date(1998, 11, 22), date(1998, 12, 6)),
    2002: (date(2002, 11, 10), date(2002, 11, 24)),
    2006: (date(2006, 10, 22), date(2006, 11, 12)),
    2010: (date(2010, 10, 10), date(2010, 10, 24)),
    2014: (date(2014, 10, 5), date(2014, 10, 19)),
    2018: (date(2018, 11, 18), date(2018, 12, 2)),
    2022: (date(2022, 11, 20), date(2022, 12, 4)),
}
COMMUNITY = {
    "italijanska": "italian",
    "madžarska": "hungarian",
    "romske": "roma",
    "slovenskega naroda": "slovene",
}
STATUS = {"vodilni": "elected", "drugiKrog": "advanced", "zreb": "lot"}


def clean(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ").split())


def number(text: str) -> int:
    digits = clean(text).replace(".", "")
    if not re.fullmatch(r"\d+", digits):
        raise ValueError(f"not a count: {text!r}")
    return int(digits)


def fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("Đ", "D").replace("đ", "d")).encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", " ", folded.decode().lower()).strip()


def _status(row: str) -> str | None:
    found = re.match(r'<tr\s+class="([^"]*)"', row)
    return STATUS.get(found.group(1).split()[0]) if found and found.group(1).strip() else None


def _section(heading: str) -> Section:
    text = heading.lower()
    if "župan" in text:
        return Section("mayor", 2 if "drugi krog" in text else 1)
    if "svet" in text:
        found = re.search(r"enot[ae]\s*(\d+)", text)
        return Section("council", unit=int(found.group(1)) if found else 1)
    for word, kind in COMMUNITY.items():
        if word in text:
            return Section(kind)
    raise ValueError(f"unknown section {heading!r}")


TURNOUT_ROWS = {
    "število volilnih upravičencev": "registered",
    "skupaj glasovalo po imeniku": "voters",
    "število oddanih glasov (za župane)": "cast",
    "število neveljavnih glasov (za župane)": "invalid",
    "glasovalo s potrdilom (za župane)": "certificates",
}


LOST = "ČŠŽčšžĆćĐđÁÉÍÓÖŐÚÜŰáéíóöőúüű"
BROKEN = re.compile(r"(?:[^\W\d_]|\ufffd)*\ufffd(?:[^\W\d_]|\ufffd)*")
WORD = re.compile(r"[^\W\d_]+")


PAIRS: Counter[str] = Counter()


@cache
def lexicon() -> dict[str, Counter[str]]:
    """Every word the other years' pages and the 2022 data print, by its lowercase form with the letters the 2018
    site lost folded away, so a word of 2018 can be restored from the same word printed elsewhere."""
    words: dict[str, Counter[str]] = {}
    texts = [
        raw.cached(SOURCE, url).path.read_bytes().decode("cp1250", errors="replace")
        for year in (2006, 2010, 2014)
        for url in pages(year)
    ]
    texts += [raw.cached(SOURCE, url).path.read_text(encoding="utf-8") for url in json_urls()]
    texts += [
        raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", errors="replace").replace("\ufffd", " ")
        for url in pages(2018)
    ]
    for text in texts:
        for word in WORD.findall(html.unescape(text) if "<" in text else text):
            padded = f"^{word.lower()}$"
            for index in range(len(padded) - 1):
                PAIRS[padded[index : index + 2]] += 1
            if any(letter in LOST for letter in word):
                lower = word.lower()
                words.setdefault(re.sub(f"[{LOST.lower()}]", "\ufffd", lower), Counter())[lower] += 1
    return words


def _guess(lower: str) -> str:
    """Spell a word no other page prints: each lost letter is the one most often found beside the same neighbors."""
    lexicon()
    padded = list(f"^{lower}$")
    for index, letter in enumerate(padded):
        if letter == "\ufffd":
            before, after = padded[index - 1], padded[index + 1]
            padded[index] = max(
                LOST.lower()[:6],
                key=lambda new: (
                    (PAIRS[before + new] + 1) * (PAIRS[new + after] + 1) if after != "\ufffd" else PAIRS[before + new]
                ),
            )
    return "".join(padded[1:-1])


def restore(word: str) -> str:
    """Restore the letters of one word that the 2018 site printed as U+FFFD: the commonest spelling of the same word
    elsewhere in the commission's local-election pages, else the letter most often printed beside the same
    neighbors."""
    lower = word.lower()
    key = re.sub(f"[{LOST.lower()}]", "\ufffd", lower)
    fits = [
        (n, spelled)
        for spelled, n in lexicon().get(key, Counter()).items()
        if all(old in ("\ufffd", new) for old, new in zip(lower, spelled, strict=True))
    ]
    spelled = max(fits)[1] if fits else _guess(lower)
    letters = word.replace("\ufffd", "")
    if len(word) > 1 and letters and letters.isupper():
        return spelled.upper()
    out = []
    for index, (old, new) in enumerate(zip(word, spelled, strict=True)):
        upper = old.isupper() or (old == "\ufffd" and index == 0 and not word[1:2].isupper() and bool(letters))
        out.append(new.upper() if upper else new)
    return "".join(out)


def parse_page(year: int, url: str) -> tuple[str, Municipality]:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8" if year == 2018 else "cp1250", errors="replace")
    if year == 2018:
        text = BROKEN.sub(lambda found: restore(found.group(0)), text)
    name = clean(_found(r'(?is)class="obcina"[^>]*>(.*?)</', text))
    urban = bool(re.match(r"mestna\s", name, flags=re.IGNORECASE))
    name = re.sub(r"^(mestna\s+)?ob[čc]ina\s+", "", name, flags=re.IGNORECASE)
    town = Municipality(year, 0, name, url, urban=urban)
    turnout = text[text.lower().find("volilna udele") :]
    turnout = turnout[: turnout.find("LEGENDA")]
    cells = [clean(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", turnout, flags=re.DOTALL)]
    days = [date(int(y), int(m), int(d)) for d, m, y in re.findall(r"(\d\d?)\.\s*(\d\d?)\.\s*(\d{4})", " ".join(cells))]
    if not days or days[0] != ROUNDS[year][0] or any(day != ROUNDS[year][1] for day in days[1:]):
        raise ValueError(f"{url}: turnout dates {days}")
    for index, day in enumerate(days):
        town.days[index + 1] = day
        town.turnout[index + 1] = Turnout()
    for position, cell in enumerate(cells):
        field_name = TURNOUT_ROWS.get(cell.lower().rstrip(": "))
        if field_name is None:
            continue
        for index in range(len(days)):
            setattr(town.turnout[index + 1], field_name, number(cells[position + 1 + index]))
    for heading, table in re.findall(
        r"<h2>(.*?)</h2>\s*(?:<div[^>]*>\s*)?(<table.*?</table>)", text, flags=re.DOTALL | re.IGNORECASE
    ):
        heading = clean(heading)
        if "udeležba" in heading.lower():
            continue
        section = _section(heading)
        header = [clean(c).lower() for c in re.findall(r"<th[^>]*>(.*?)</th>", table, flags=re.DOTALL)]
        for row in re.findall(r"<tr[^>]*>.*?</tr>", table, flags=re.DOTALL | re.IGNORECASE):
            cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)]
            if not cells:
                continue
            if len(cells) != len(header):
                raise ValueError(f"{url}: {heading}: row {cells} against {header}")
            if "št. mandatov" in header:
                section.lists.append(ListRow(cells[1], number(cells[2]), number(cells[4])))
            else:
                section.candidates.append(Candidate(cells[1], cells[2], number(cells[3]), _status(row)))
        town.sections.append(section)
    slug = url.rsplit("/", 1)[1].removeprefix("obcina_").removesuffix(".html")
    return slug, town


# Names the 2022 data shortens.
FULL_NAMES = {116: "SVETI JURIJ OB ŠČAVNICI"}
MINORITY = {"503": "italian", "504": "hungarian", "505": "roma", "506": "slovene"}
JSON_STATUS = {1: "elected", 3: "advanced"}


def _load(url: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(raw.cached(SOURCE, url).path.read_bytes()))


def _candidates(rows: list[dict[str, Any]]) -> list[Candidate]:
    return [
        Candidate(
            " ".join(str(row["naz"]).split()),
            " ".join(str(row["pred"]).split()),
            int(row["gl"]),
            JSON_STATUS.get(int(row["tip_izv"])) if row.get("tip_izv") is not None else None,
            row["st"],
        )
        for row in rows
    ]


def _turnout(data: dict[str, Any]) -> Turnout:
    return Turnout(
        registered=data["udel"]["upr"],
        voters=data["udel"]["gl"],
        cast=data["rez"]["glas"],
        valid=data["rez"]["velj"],
        invalid=data["rez"]["nev"],
    )


def parse_2022() -> list[Municipality]:
    names = {
        int(row["st"]): " ".join(row["naz"].split())
        for row in _load(f"{LV2022}/prvikrog/data.json")["slovenija"]["obcine"]
    }
    runoffs = set(runoffs_2022())
    towns: list[Municipality] = []
    for code in codes_2022():
        url = f"{LV2022}/prvikrog/obcine-data/data_{code:03d}.json"
        data = _load(url)
        town = Municipality(2022, code, names[code], url)
        town.days[1] = ROUNDS[2022][0]
        town.turnout[1] = _turnout(data)
        town.sections.append(Section("mayor", 1, candidates=_candidates(data["zup"]["kand_rez"])))
        council = data["os"]
        for unit in council["enote"]:
            number_ = int(unit["st"]) if unit["st"] else 1
            section = Section("council", unit=number_)
            if council["tip_vs"] == "2":
                section.lists = [
                    ListRow(" ".join(str(row["naz"]).split()), int(row["gl"]), int(row["man"]), row["st"])
                    for row in unit["rez"]
                ]
            else:
                section.candidates = _candidates(unit["kand_rez"])
            town.sections.append(section)
        for community in data.get("manjs", []):
            kind = MINORITY[community["tip"]]
            for unit in community["enote"]:
                section = Section(kind, unit=int(unit["st"]) if unit["st"] else 1)
                section.candidates = _candidates(unit["kand_rez"])
                town.sections.append(section)
        if code in runoffs:
            second = f"{LV2022}/obcine-data/data_{code:03d}.json"
            data = _load(second)
            town.runoff_url = second
            town.days[2] = ROUNDS[2022][1]
            town.turnout[2] = _turnout(data)
            town.sections.append(Section("mayor", 2, candidates=_candidates(data["zup"]["kand_rez"])))
        towns.append(town)
    return towns


@cache
def municipalities() -> dict[str, int]:
    """The commission's number of each municipality, by its name folded to ASCII, from the 2022 data."""
    found = {fold(row["naz"]): int(row["st"]) for row in _load(f"{LV2022}/prvikrog/data.json")["slovenija"]["obcine"]}
    return found | {fold(name): code for code, name in FULL_NAMES.items()}


@cache
def towns(year: int) -> list[Municipality]:
    if year == 2022:
        return parse_2022()
    if year in EARLY:
        return parse_early(year)
    found: list[Municipality] = []
    for url in pages(year):
        slug, town = parse_page(year, url)
        code = municipalities().get(fold(town.name)) or municipalities().get(fold(slug.replace("_", " ")))
        if code is None:
            raise LookupError(f"{year}: no number for municipality {town.name!r} ({slug})")
        town.code = code
        found.append(town)
    if len({town.code for town in found}) != len(found):
        raise ValueError(f"{year}: two pages map to one municipality")
    return found


# packages ----------------------------------------------------------------------------------------------------------

GEO_KEY = f"{SOURCE}/lv-geo"
REGISTRY_KEY = f"{SOURCE}/lv-registry"
YEARS = (1998, 2002, 2006, 2010, 2014, 2018, 2022)
EARLY = {
    1998: [f"{ARCHIVE}/lv1998/rez_z{part}.html" for part in range(1, 5)],
    2002: [f"{ARCHIVE}/volitve2002/V3_ZUPRez.html"],
}
RUNOFF_1998 = f"{ARCHIVE}/lv1998/rez_z2k.html"
INDEX_1998 = f"{ARCHIVE}/lv1998/"
GEOBOUNDARIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/SVN/ADM2/"
    "geoBoundaries-SVN-ADM2_simplified.geojson"
)
MUNICIPALITY = "si.obcina"
COUNCIL_UNIT = "si.obcina_ve"
LV_SOURCE = key("source", "lv")
GEO_SOURCE = key("source", "geoboundaries-svn")
SYSTEM_LIST = key("electoral_system", "lv-council-list")
SYSTEM_PLURALITY = key("electoral_system", "lv-council-plurality")
SYSTEM_MAYOR = key("electoral_system", "lv-mayor")
# Municipalities whose territory the 2011 founding of Ankaran (from Koper) and Mirna (from Trebnje) changed: the
# outline here is the territory since then.
SPLIT_2011 = {50, 130, 212, 213}
SHAPES_FROM = date(2006, 10, 1)
SPLIT_FROM = date(2011, 1, 1)
SMALL_WORDS = {"ob", "pri", "na", "v", "pod", "nad", "in", "mesto", "vas", "polju", "goricah", "slov", "polje"}
COMMUNITY_EN = {
    "roma": ("Roma community councillor", "the Roma community's councillor"),
    "italian": ("Italian national community councillors", "the Italian national community's councillors"),
    "hungarian": ("Hungarian national community councillors", "the Hungarian national community's councillors"),
    "slovene": ("Slovene nation councillor", "the councillor of the members of the Slovene nation"),
}
COMMUNITY_SL = {
    "roma": "predstavnik romske skupnosti",
    "italian": "predstavniki italijanske narodne skupnosti",
    "hungarian": "predstavniki madžarske narodne skupnosti",
    "slovene": "predstavnik pripadnikov slovenskega naroda",
}
VOTER_GROUP = re.compile(r"skupin\w* volil|skupin\w* volivc|skupna volivc|samostojn", re.IGNORECASE)


def packages() -> list[str]:
    rounds = [(year, r) for year in YEARS for r in (1, 2) if not (year == 2002 and r == 2)]
    return [GEO_KEY, REGISTRY_KEY, *(election_key(year, r) for year, r in rounds)]


def election_key(year: int, round_number: int) -> str:
    return f"{SOURCE}/lv-{ROUNDS[year][round_number - 1]}"


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        for round_number in (1, 2):
            if election_key(year, round_number) in packages():
                election(year, round_number).write()
    for year in YEARS:
        print(f"{year}: {totals(year)}", flush=True)


def proper(name: str) -> str:
    """A municipality's name in the case the commission's mixed-case pages use."""
    words = re.split(r"(\s+|-)", name.lower())
    out = [word if index and word in SMALL_WORDS else word[:1].upper() + word[1:] for index, word in enumerate(words)]
    return "".join(out).replace("Slov.", "Slov.")


@cache
def names() -> dict[int, str]:
    """Each municipality's name: the latest the commission prints, in proper case."""
    found: dict[int, str] = {}
    for year in YEARS:
        for town in towns(year):
            found[town.code] = proper(FULL_NAMES.get(town.code, town.name))
    return found


BILINGUAL = re.compile(r" (capodistria|isola|pirano|ancarano)$")


def _shapes() -> dict[int, shapely.MultiPolygon]:
    """The outline of each municipality from geoBoundaries, matched by name: the file's names lost their letters with
    diacritics, so each is matched to the closest commission name, and every match must be the best both ways."""
    features = json.loads(raw.cached(SOURCE, GEOBOUNDARIES).path.read_bytes())["features"]
    ours = {fold(name): code for code, name in names().items()}
    theirs = {re.sub(BILINGUAL, "", fold(str(f["properties"]["shapeName"]))): f for f in features}
    found: dict[int, shapely.MultiPolygon] = {}
    for name, feature in theirs.items():
        best = max(ours, key=lambda mine: difflib.SequenceMatcher(None, name, mine).ratio())
        back = max(theirs, key=lambda other: difflib.SequenceMatcher(None, other, best).ratio())
        if back != name or difflib.SequenceMatcher(None, name, best).ratio() < 0.7:
            raise ValueError(f"geoBoundaries {name!r} has no clear match ({best!r})")
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [
            q
            for part in shapely.get_parts(shape)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        found[ours[best]] = shapely.set_srid(shapely.MultiPolygon(parts), 4326)
    if set(found) != set(names()):
        raise ValueError(f"municipalities without an outline: {sorted(set(names()) - set(found))}")
    return found


@cache
def urban() -> set[int]:
    """The urban municipalities (mestne občine), as the latest archived pages head them."""
    return {town.code for town in towns(2018) if town.urban}


def area(code: int) -> str:
    return key("area", MUNICIPALITY, code)


def geo() -> Package:
    p = Package(
        GEO_KEY,
        SOURCE,
        "Slovenia's 212 municipalities (občine), numbered as the State Election Commission numbers them, with "
        "outlines from geoBoundaries",
    )
    p.external.add(SLOVENIA)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Slovenia ADM2, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    p.artifact(raw.cached(SOURCE, GEOBOUNDARIES), GEO_SOURCE)
    for code, kind, rank, noun in (
        (MUNICIPALITY, "administrative", 1, "municipality"),
        (COUNCIL_UNIT, "electoral_district", 2, "municipal council electoral unit"),
    ):
        p.add("scheme", code=code, polity_id=SLOVENIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add(
        "boundary_set",
        id=key("boundary_set", MUNICIPALITY, "2017"),
        scheme_code=MUNICIPALITY,
        vintage="2017",
        valid_from=SHAPES_FROM,
        as_of=date(2017, 1, 1),
    )
    shapes = _shapes()
    for code, name in sorted(names().items()):
        id = area(code)
        p.add("area", id=id, scheme_code=MUNICIPALITY)
        p.add("area_code", area_id=id, code=str(code))
        p.name(id, name, "sl", "official")
        shape = shapes[code]
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=SPLIT_FROM if code in SPLIT_2011 else SHAPES_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=GEO_SOURCE,
        )
    return p


def body(code: int) -> str:
    return key("body", "council", code)


def office(code: int) -> str:
    return key("office", "mayor", code)


# parties -----------------------------------------------------------------------------------------------------------


def _label(text: str) -> tuple[str, str | None]:
    """A proposer's name without the party abbreviation printed before or after it (SDS - SLOVENSKA DEMOKRATSKA
    STRANKA, SLOVENSKA DEMOKRATSKA STRANKA - SDS): an abbreviation is a short capitalized token, not a word of a
    name, that begins with the first letter of the name beside it."""
    text = " ".join(text.split())
    for gap in re.finditer(r"\s*(?:[-,]\s*|\s+)\(?", text[:9]):
        token, rest = text[: gap.start()], text[gap.end() :].removesuffix(")")
        if re.fullmatch(r"[A-ZČŠŽ][A-Za-zČŠŽčšž.+-]{1,6}", token) and len(rest) >= 4 and _abbreviation(token, rest):
            return _label(rest)[0], token
    found = re.match(r"^(.{4,}?)\s*[-,(]?\s*([A-ZČŠŽ][A-Za-zČŠŽčšž.+-]{1,5})\)?$", text)
    if found and _abbreviation(found.group(2), found.group(1)):
        return found.group(1), found.group(2)
    return text, None


NOT_ABBREVIATIONS = {"LISTA", "ZA", "NOVA", "NAŠA", "NAŠE", "ZVEZA", "SKUPAJ", "GLAS", "VEČ", "STRANKA", "SLOGA"}


def _abbreviation(token: str, name: str) -> bool:
    return (
        token == token.upper()
        and token.strip(".-") not in NOT_ABBREVIATIONS
        and " " in name
        and name[0].upper() == token[0]
        and token[-1] != "-"
    )


GENERIC = {"KOALICIJA STRANK", "DEMOKRATIČNA STRANKA"}
PARTY_WORDS = re.compile(r"STRANK|PARTIJ|DEMOKRA|GIBANJE|ZVEZA|LEVICA|ZELEN|UNIJA|ZAVEZNI|DESUS|SOCIALNI|SLOVENIJ")


def independent(proposer: str) -> bool:
    return bool(VOTER_GROUP.search(proposer))


@cache
def national_codes() -> dict[str, str]:
    """The 2022 party codes the commission prints, with each party's national name."""
    found: dict[str, str] = {}
    for path in ("prvikrog/data.json", "data.json"):
        rez = _load(f"{LV2022}/{path}")["slovenija"]["rez"]
        for listing in ("rez_zup", "rez_os", "rez_os_vec"):
            for row in rez.get(listing, []):
                found.setdefault(str(row["st"]), " ".join(str(row["naz"]).split()))
    return found


@dataclass
class Party:
    id: str
    name: str
    short: str | None
    kind: str


def _proposers(year: int) -> list[tuple[int, str, str | None]]:
    """Every proposer of a list or candidate as printed, with its municipality and the 2022 party code."""
    printed: list[tuple[int, str, str | None]] = []
    for town in towns(year):
        for section in town.sections:
            rows = [(c.proposer, c.code) for c in section.candidates] + [(r.name, r.code) for r in section.lists]
            for name, code in rows:
                if name and not (independent(name) and not section.lists):
                    printed.append((town.code, name, code))
    return printed


@cache
def national() -> set[str]:
    """The names of the parties that stood across the country: a party's name (one with a word such as stranka or
    gibanje) printed in three or more municipalities in one year, any name printed in ten or more, and each party
    the commission codes in 2022."""
    found = {_label(name)[0].upper() for name in national_codes().values()}
    for year in YEARS:
        seen: dict[str, set[int]] = {}
        for town_code, name, _ in _proposers(year):
            seen.setdefault(_label(name)[0].upper(), set()).add(town_code)
        found |= {
            label
            for label, places in seen.items()
            if (len(places) >= 10 or (len(places) >= 3 and PARTY_WORDS.search(label))) and label not in GENERIC
        }
    return found


def canonical(label: str) -> str | None:
    """The national party a proposer is a branch of: its name begins with the party's name, followed only by the
    branch's own words (a committee, a conference, the party's abbreviation), and names no other party."""
    upper = label.upper()
    if upper in national():
        return upper
    best = None
    for name in national():
        if upper.startswith(name) and upper[len(name) : len(name) + 1] in (",", " ", "-"):
            rest = upper[len(name) :]
            if (
                " IN " in f"{rest} "
                or "KOALICIJ" in rest
                or any(other in rest for other in national() if len(other) > 6)
            ):
                continue
            if best is None or len(name) > len(best):
                best = name
    return best


@cache
def parties(year: int) -> dict[tuple[int, str], Party]:
    """Each proposer of a list or candidate by (municipality, name as printed). A proposer is a national party when
    the commission codes it (2022) or its name is, or begins with, the name of a party printed in three or more
    municipalities (see `national`); any other proposer is local to its municipality."""
    found: dict[tuple[int, str], Party] = {}
    for town_code, name, code in _proposers(year):
        label, short = _label(name)
        party = canonical(_label(national_codes().get(code, label))[0] if code else label)
        if party is not None:
            found[(town_code, name)] = Party(key("party", party), party, short, "party")
        else:
            group = independent(name) or year == 2022
            found[(town_code, name)] = Party(
                key("party", town_code, label.upper()), name, short, "independent_group" if group else "party"
            )
    return found


def registry() -> Package:
    p = Package(
        REGISTRY_KEY,
        SOURCE,
        "The State Election Commission's local-election archive as a source, the council and the mayor of each "
        "municipality, the local electoral systems, and the proposers of lists and candidates of 2006-2022",
        depends=[GEO_KEY],
    )
    p.external.add(SLOVENIA)
    p.add(
        "source",
        id=LV_SOURCE,
        kind="official_body",
        publisher="Državna volilna komisija",
        title="Local elections: results by municipality (lokalne volitve, rezultati po občinah)",
        url="https://www.dvk-rs.si/arhiv-volitev/",
        license="Public information of the State Election Commission",
        license_url="https://www.dvk-rs.si/informacije-javnega-znacaja/",
        attribution="Državna volilna komisija Republike Slovenije; občinske volilne komisije",
    )
    cities = urban()
    for code, name in sorted(names().items()):
        council = body(code)
        p.add("body", id=council, polity_id=SLOVENIA, kind="council")
        mayor = office(code)
        p.add("office", id=mayor, polity_id=SLOVENIA, body_id=council, kind="mayor")
        p.name(mayor, f"Mayor of {name}", "en", "official")
        if code in cities:
            p.name(council, f"{name} City Council", "en", "official")
            p.name(council, f"Mestni svet Mestne občine {name}", "sl", "native", False)
            p.name(mayor, f"Župan Mestne občine {name}", "sl", "native", False)
        else:
            p.name(council, f"{name} Municipal Council", "en", "official")
            p.name(council, f"Občinski svet Občine {name}", "sl", "native", False)
            p.name(mayor, f"Župan Občine {name}", "sl", "native", False)
    for id, rules, english, native, note in (
        (SYSTEM_LIST, {"formula": "dhondt"}, "Municipal council, list system", "Proporcionalni sistem", NOTE_LIST),
        (SYSTEM_PLURALITY, {"formula": "block"}, "Municipal council, plurality", "Večinski sistem", NOTE_PLURALITY),
        (SYSTEM_MAYOR, {"formula": "two_round", "majority": 0.5, "advance": 2}, "Mayor", "Volitve župana", NOTE_MAYOR),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=SLOVENIA,
            valid_from=date(1994, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.name(id, f"Slovenian local elections: {english.lower()}", "en", "official")
        p.name(id, f"Lokalne volitve: {native.lower()}", "sl", "native", False)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    done: set[str] = set()
    for year in YEARS:
        for party in parties(year).values():
            if party.id in done:
                continue
            done.add(party.id)
            p.add("party", id=party.id, kind=party.kind, polity_id=SLOVENIA)
            p.name(party.id, party.name, "sl", "official")
            if party.short:
                p.name(party.id, party.short, "sl", "short", False)
    return p


NOTE_LIST = (
    "A council of 12 or more members is elected by list proportional representation (ZLV): the lists' votes in all "
    "the municipality's electoral units are summed, seats are divided among the lists by the D'Hondt method, and "
    "each list's seats are then given to its candidates in the units by their share of the votes; a voter may also "
    "give one candidate of the chosen list a preference vote. Each electoral unit is a contest here, with the seats "
    "the commission prints for each list in it."
)
NOTE_PLURALITY = (
    "A council of fewer than 12 members is elected by plurality (ZLV): the municipality is divided into electoral "
    "units of one or more seats, each voter may vote for as many candidates as the unit elects, and the candidates "
    "with the most votes win. The councillors of the Roma community, of the Italian and Hungarian national "
    "communities, and of the members of the Slovene nation in the ethnically mixed area are elected separately by "
    "their own electors at the same time and are stored as contests of the council under this system."
)
NOTE_MAYOR = (
    "The mayor is elected directly (ZLV): a candidate who wins a majority of the valid votes is elected; otherwise "
    "the two candidates with the most votes meet in a second round two weeks later, and the one with more votes wins."
)


# elections ---------------------------------------------------------------------------------------------------------

METHOD_HTML = (
    "Compiled by the State Election Commission (Državna volilna komisija) from what the municipal election "
    "commissions reported, and published on its archived local-election site; the site marks the figures as "
    "unofficial (neuradni) except the tables it heads as official (URADNI). The mayor's page of each municipality "
    "prints its registered voters, the voters who voted by the roll and with a certificate (stored together as "
    "voters), the ballots cast for the mayor, and the invalid ones; valid votes are not printed. The councils' pages "
    "print votes only."
)
METHOD_2022 = (
    "Published by the State Election Commission (Državna volilna komisija) in the final data of its 2022 "
    "local-election site, compiled from what the municipal election commissions reported. For the mayor each "
    "municipality's registered voters, voters, ballots cast, valid votes, and invalid ballots are stored; the "
    "councils' figures are votes only."
)
METHOD_2018 = (
    " The 2018 site printed some letters with diacritics (Č, Š, Ž, and others) as a replacement character. Each "
    "such word is restored from the same word printed on the commission's other local-election pages of 2006-2022; "
    "about one word in forty that appears nowhere else is restored with the letter most often printed between the "
    "same neighbors, which can be wrong. The figures are not affected."
)
METHOD_KIND = {
    "mayor": "",
    "list": " Each list's votes in the electoral unit and the seats the commission prints for it are stored.",
    "plurality": (
        " Each candidate's votes in the electoral unit are stored; each voter may vote for as many candidates as "
        "the unit elects, so the votes sum to more than the voters. The candidates the commission marks elected "
        "are stored as elected."
    ),
    "community": (
        " The councillors of a community are elected by its own electors on separate ballots; each candidate's "
        "votes as the commission prints them are stored, with the candidates it marks elected."
    ),
}


def election(year: int, round_number: int) -> Package:
    day = ROUNDS[year][round_number - 1]
    first = election_key(year, 1)
    p = Package(
        election_key(year, round_number),
        SOURCE,
        f"The {year} Slovenian local elections"
        + (": municipal councils and mayors, first round" if round_number == 1 else ": mayors, second round")
        + ", by municipality and council electoral unit",
        depends=[GEO_KEY, REGISTRY_KEY, *([first] if round_number == 2 else [])],
    )
    p.external.add(SLOVENIA)
    cited: dict[str, str] = {}

    def cite(url: str) -> None:
        if url in cited:
            return
        if round_number == 2 and year in HTML_YEARS:
            # The page is the one the first round's package already cites.
            p.uses(raw.cached(SOURCE, url))
            cited[url] = url
        else:
            cited[url] = p.artifact(raw.cached(SOURCE, url), LV_SOURCE)

    if round_number == 1 and year not in EARLY:
        cite(summary_url(year))
    if round_number == 2 and year == 1998:
        cite(INDEX_1998)
    election_id = key("election", "lv", day)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="local" if round_number == 1 else "runoff",
        date_from=day,
        date_to=day,
        status="held",
    )
    path = f"si/{day}-local" + ("" if round_number == 1 else "-runoff")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    if round_number == 1:
        p.name(election_id, f"{year} Slovenian local elections", "en", "official")
        p.name(election_id, f"Lokalne volitve {year}", "sl", "native", False)
    else:
        p.name(election_id, f"{year} Slovenian local elections, mayoral second round", "en", "official")
        p.name(election_id, f"Lokalne volitve {year}, drugi krog volitev županov", "sl", "native", False)
    known = parties(year)
    people: set[str] = set()
    facts: list[tuple[str, str, str | None, int, int]] = []
    for town in towns(year):
        sections = [
            s
            for s in town.sections
            if (s.kind == "mayor" and s.round == round_number) or round_number == 1 and s.kind != "mayor"
        ]
        if not sections:
            continue
        cite(town.runoff_url if round_number == 2 and town.runoff_url else town.url)
        name = names()[town.code]
        place = changeset.slug(name)
        here = key("unit", day, town.code)
        p.add(
            "reporting_unit",
            id=here,
            polity_id=SLOVENIA,
            scheme_code=MUNICIPALITY,
            code=f"lv:{day}:{town.code}",
            area_relation="equals",
            area_id=area(town.code),
            kind="standard",
            valid_from=day,
        )
        p.name(here, name, "sl", "official")
        counts = Counter((s.kind, s.round) for s in sections)
        for section in sections:
            many = counts[(section.kind, section.round)] > 1
            tag = f"{day}:{town.code}:{section.kind}:{section.unit}"
            unit = here
            if section.kind != "mayor" and many:
                unit = key("unit", day, town.code, section.kind, section.unit)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=SLOVENIA,
                    scheme_code=COUNCIL_UNIT,
                    code=f"lv:{day}:{town.code}:{section.kind}:{section.unit}",
                    parent_id=here,
                    area_relation="within",
                    area_id=area(town.code),
                    kind="standard",
                    valid_from=day,
                )
                p.name(unit, f"{name}, volilna enota {section.unit}", "sl", "official")
            contest = key("contest", tag)
            if section.kind == "mayor":
                style, system, kind, magnitude = "mayor", SYSTEM_MAYOR, "office", 1
            elif section.kind == "council" and section.lists:
                style, system, kind = "list", SYSTEM_LIST, "list_allocation"
                magnitude = sum(row.seats for row in section.lists) or None
            else:
                style = "plurality" if section.kind == "council" else "community"
                system, kind = SYSTEM_PLURALITY, "body_seats"
                elected = sum(c.status == "elected" for c in section.candidates)
                lot = any(c.status == "lot" for c in section.candidates)
                magnitude = elected if elected and not lot else None
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind=kind,
                office_id=office(town.code) if section.kind == "mayor" else None,
                body_id=None if section.kind == "mayor" else body(town.code),
                district_area_id=area(town.code),
                magnitude=magnitude,
                round_number=section.round,
                previous_round_id=key("contest", f"{ROUNDS[year][0]}:{town.code}:mayor:1")
                if section.round == 2
                else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            english, native, leaf = _contest_names(name, section, many)
            p.name(contest, english, "en", "official")
            p.name(contest, native, "sl", "native", False)
            p.add("slug", path=f"{path}/{place}/{leaf}", entity_id=contest, is_current=True)
            result_set = key("result_set", tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official" if year == 2022 else "preliminary",
                kind="counted",
                source_id=LV_SOURCE,
                status="active",
                is_default=True,
                completeness=town.counted.get(section.round, 1.0),
            )
            method = METHOD_2022 if year == 2022 else METHOD_EARLY[year] if year in EARLY else METHOD_HTML
            method += METHOD_KIND[style]
            method += METHOD_2018 if year == 2018 else ""
            p.add(
                "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
            )
            if section.kind == "mayor":
                turnout = town.turnout.get(section.round)
                if turnout is not None:
                    voters = turnout.voters
                    if year != 2022 and voters is not None:
                        voters += turnout.certificates or 0
                    for measure, value in (
                        (2, turnout.registered),
                        (14, voters),
                        (3, turnout.cast),
                        (4, turnout.valid),
                        (5, turnout.invalid),
                    ):
                        if value is not None:
                            facts.append((result_set, unit, None, measure, value))
            if section.lists:
                for index, row in enumerate(section.lists):
                    option = key("option", tag, index)
                    party = known.get((town.code, row.name))
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party.id if party else None,
                        ballot_label=row.name,
                    )
                    facts.append((result_set, unit, option, 1, row.votes))
                    if row.seats:
                        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
                continue
            decided = any(c.status in ("elected", "advanced") for c in section.candidates)
            anyone = any(c.status == "elected" for c in section.candidates)
            seen: Counter[str] = Counter()
            for candidate in section.candidates:
                folded = fold(candidate.name)
                seen[folded] += 1
                option = key("option", tag, folded, seen[folded])
                group = independent(candidate.proposer) or not candidate.proposer
                party = None if group else known.get((town.code, candidate.proposer))
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party.id if party else None,
                    ballot_label=candidate.name,
                )
                person = key("person", year, town.code, folded, seen[folded])
                if person not in people and (round_number == 1 or not _in_first(year, town.code, folded)):
                    people.add(person)
                    p.add("person", id=person)
                    p.name(person, candidate.name, "sl", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", tag, folded, seen[folded]),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate.name,
                    is_independent=group,
                )
                facts.append((result_set, unit, option, 1, candidate.votes))
                if not decided or candidate.status == "lot":
                    continue
                if candidate.status in ("elected", "advanced"):
                    outcome = candidate.status
                elif section.kind == "mayor" and section.round == 1 and not anyone:
                    outcome = "eliminated"
                else:
                    outcome = "lost"
                p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
    _frame(p, facts)
    return p


@cache
def _first_people(year: int) -> set[tuple[int, str]]:
    return {
        (town.code, fold(c.name))
        for town in towns(year)
        for section in town.sections
        if not (section.kind == "mayor" and section.round == 2)
        for c in section.candidates
    }


def _in_first(year: int, code: int, folded: str) -> bool:
    return (code, folded) in _first_people(year)


def _contest_names(name: str, section: Section, many: bool) -> tuple[str, str, str]:
    unit = f", electoral unit {section.unit}" if many else ""
    enota = f", {section.unit}. volilna enota" if many else ""
    leaf_unit = f"-unit-{section.unit}" if many else ""
    if section.kind == "mayor":
        second = section.round == 2
        return (
            f"Mayor of {name}" + (", second round" if second else ""),
            f"Župan občine {name}" + (", drugi krog" if second else ""),
            "mayor",
        )
    if section.kind == "council":
        return f"{name} Municipal Council{unit}", f"Občinski svet občine {name}{enota}", f"council{leaf_unit}"
    english, _ = COMMUNITY_EN[section.kind]
    return (
        f"{name} Municipal Council, {english}{unit}",
        f"Občinski svet občine {name}, {COMMUNITY_SL[section.kind]}{enota}",
        f"council-{section.kind}{leaf_unit}",
    )


def _frame(p: Package, facts: list[tuple[str, str, str | None, int, int]]) -> None:
    size = len(facts)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": [f[0] for f in facts],
                "unit_id": [f[1] for f in facts],
                "option_id": [f[2] for f in facts],
                "channel": [0] * size,
                "measure": [f[3] for f in facts],
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": [f[4] for f in facts],
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )


def _found(pattern: str, text: str) -> str:
    found = re.search(pattern, text)
    if found is None:
        raise LookupError(f"no {pattern!r} in the summary")
    return found.group(1)


def summary_url(year: int) -> str:
    return f"{HTML_YEARS[year]}/rezultati/zbirni_podatki.html" if year in HTML_YEARS else f"{LV2022}/prvikrog/data.json"


def totals(year: int) -> dict[str, object]:
    """Compare the parsed municipalities with the commission's national summary: the municipalities whose mayor was
    elected in the first round and those that went to a second round, and in 2022 each coded party's list votes
    and seats."""
    first = sum(
        1
        for town in towns(year)
        for s in town.sections
        if s.kind == "mayor" and s.round == 1 and any(c.status == "elected" for c in s.candidates)
    )
    second = sum(1 for town in towns(year) for s in town.sections if s.kind == "mayor" and s.round == 2)
    if year in EARLY:
        return _early_totals(year, second)
    if year in HTML_YEARS:
        text = clean(_text(summary_url(year), "utf-8" if year == 2018 else "cp1250"))
        printed = [
            int(_found(pattern + r":?\s*(\d+)", text))
            for pattern in ("izvoljenim kandidatom v prvem krogu", "brez kandidata s potrebno večino v prvem krogu")
        ]
    else:
        stat = _load(summary_url(year))["slovenija"]["rez"]["stat_zup"]
        printed = [sum(stat["izv"].values()), sum(stat["neizv"].values())]
    if printed != [first, second]:
        raise ValueError(f"{year}: mayors elected and runoffs {[first, second]}, the commission prints {printed}")
    found: dict[str, object] = {"mayors_first_round": first, "runoffs": second, "matches_printed": True}
    if year == 2022:
        votes: Counter[str] = Counter()
        seats: Counter[str] = Counter()
        for town in towns(year):
            for section in town.sections:
                for row in section.lists:
                    if row.code:
                        votes[row.code] += row.votes
                        seats[row.code] += row.seats
        rez = _load(summary_url(year))["slovenija"]["rez"]["rez_os"]
        coded = [row for row in rez if str(row["st"]) in national_codes() and row["st"] is not None]
        wrong = [
            f"{row['naz']}: votes {votes[str(row['st'])]:,} against {row['gl']:,}, seats {seats[str(row['st'])]} "
            f"against {row['man']}"
            for row in coded
            if (row["gl"], row["man"]) != (votes[str(row["st"])], seats[str(row["st"])])
        ]
        found["party_list_totals"] = (
            f"{len(coded) - len(wrong)} of {len(coded)} coded parties' list votes and seats equal the national summary"
        )
        found["party_list_differences"] = wrong
    found["vote_sums_off"] = vote_sums(year)
    found["list_seats"] = sum(row.seats for town in towns(year) for s in town.sections for row in s.lists)
    found["plurality_and_community_seats"] = sum(
        c.status == "elected" for town in towns(year) for s in town.sections if s.kind != "mayor" for c in s.candidates
    )
    return found


METHOD_EARLY = {
    1998: (
        "From the State Election Commission's archived site of the 1998 local elections, which marks every figure as "
        "unofficial (neuradni). The first round prints each municipality's registered voters, ballots cast, and "
        "invalid ballots, and each candidate's votes. The second round's page is the count on election night, at "
        "21:50: it prints the voters, the ballots counted, and the invalid ones, and the result set's completeness is "
        "the share of ballots counted. The site does not mark who was elected, so no outcome is stored. The site "
        "printed some letters with diacritics as a replacement character; each such word is restored from the same "
        "word printed on the commission's later local-election pages, or else with the letter most often printed "
        "between the same neighbors. The figures are not affected."
    ),
    2002: (
        "From the State Election Commission's archived site of the 2002 elections, which prints each candidate's "
        "votes in the first round of the mayoral elections by municipality; it prints neither turnout nor who was "
        "elected, so no outcome is stored, and it has no second-round results."
    ),
}


def _early_text(url: str) -> str:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", errors="replace")
    return BROKEN.sub(lambda found: restore(found.group(0)), text)


# Names the 1998 and 2002 pages print for municipalities whose names the 2022 data spell otherwise.
EARLY_NAMES = {
    "ravne": "ravne na koroskem",
    "sveti andraz v slovenskih goricah": "sveti andraz v slov goricah",
    "sv andraz v slov goricah": "sveti andraz v slov goricah",
    "sentjur pri celju": "sentjur",
    "miklavz na dr polju": "miklavz na dravskem polju",
    "selnica": "selnica ob dravi",
    "sv jurij ob scavnici": "sveti jurij ob scavnici",
}


def _place(year: int, name: str) -> int:
    code = municipalities().get(EARLY_NAMES.get(fold(name), fold(name)))
    if code is None:
        raise LookupError(f"{year}: no number for municipality {name!r}")
    return code


def parse_early(year: int) -> list[Municipality]:
    """The mayoral results of 1998 (both rounds) and 2002 (first round) from the archived pages."""
    found: dict[int, Municipality] = {}
    for url in EARLY[year]:
        text = _early_text(url)
        if year == 1998:
            blocks = re.split(r"<a name=\"\d+\">", text)[1:]
        else:
            blocks = re.split(r"<A name=Obc\d+>", text)[1:]
        for block in blocks:
            cells = [
                clean(c) for c in re.findall(r"<td[^>]*>(.*?)(?=<td|</tr|<tr)", block, flags=re.DOTALL | re.IGNORECASE)
            ]
            if year == 1998:
                head = clean(block[: block.find("<table")])
                if "NI VOLITEV" in block[:800]:
                    continue  # Horjul and Koper did not vote in 1998.
                name = _found(r"Ob\w+:\s*(.*?)\s*\(", head)
                town = Municipality(year, _place(year, name), name, url)
                town.turnout[1] = Turnout(
                    registered=int(_found(r"upravi\w+:\s*(\d+)", head)),
                    cast=int(_found(r"oddanih glasov:\s*(\d+)", head)),
                    invalid=int(_found(r"neveljavnih glasov:\s*(\d+)", head)),
                )
                rows = [cells[i : i + 5] for i in range(5, len(cells) - 4, 5)]
                candidates = [Candidate(r[1], r[2], number(r[3]), None) for r in rows if re.fullmatch(r"\d+\.", r[0])]
            else:
                name = cells[0]
                town = Municipality(year, _place(year, name), name, url)
                candidates = []
                for index in range(len(cells) - 2):
                    entry = re.fullmatch(r"\d+\. - (.+)", cells[index])
                    votes = re.fullmatch(r"(\d+) \([\d,]+ %\)", cells[index + 2]) if entry else None
                    if entry and votes:
                        candidates.append(Candidate(entry.group(1), cells[index + 1], int(votes.group(1)), None))
            if not candidates:
                raise ValueError(f"{url}: no candidates for {name}")
            if town.code in found:
                # The 2002 page splits a few municipalities' candidates over two blocks.
                if year != 2002:
                    raise ValueError(f"{year}: {name} printed twice")
                found[town.code].sections[0].candidates += candidates
                continue
            town.days[1] = ROUNDS[year][0]
            town.sections.append(Section("mayor", 1, candidates=candidates))
            found[town.code] = town
    if year == 1998:
        _runoff_1998(found)
    return [found[code] for code in sorted(found)]


def _runoff_1998(found: dict[int, Municipality]) -> None:
    text = _early_text(RUNOFF_1998)
    for block in re.split(r"<tr><td[^>]*rowspan=\"\d+\"[^>]*>", text, flags=re.IGNORECASE)[1:]:
        head = block[: block.find("</td>")]
        name = clean(_found(r"(?is)<strong>(.*?)</strong>", head))
        town = found[_place(1998, name)]
        town.days[2] = ROUNDS[1998][1]
        town.runoff_url = RUNOFF_1998
        counted = _found(r"(?s)Pre\w+:\s*</small>\s*(\d+)", head)
        town.turnout[2] = Turnout(
            voters=int(_found(r"(?s)Udele\w+:\s*</small>\s*(\d+)", head)),
            cast=int(counted),
            invalid=int(_found(r"(?s)Neveljavnih glasov:\s*</small>\s*(\d+)", head)),
        )
        town.counted[2] = float(_found(r"(?s)Pre\w+:\s*</small>\s*\d+ \(([\d,]+) %\)", head).replace(",", ".")) / 100
        candidates = []
        for votes, person, proposer in re.findall(
            r"(?is)<td[^>]*>(\d+)<BR>[\d,]+ %</td>\s*<td[^>]*>.*?<strong>(.*?)</strong>.*?<BR>(.*?)</td>", block
        ):
            candidates.append(Candidate(clean(person), clean(proposer), int(votes), None))
        if len(candidates) != 2:
            raise ValueError(f"1998: second round in {name} has {len(candidates)} candidates")
        town.sections.append(Section("mayor", 2, candidates=candidates))


def _early_totals(year: int, second: int) -> dict[str, object]:
    """1998: the second rounds against the number the site's front page announces; both years: each municipality's
    candidate votes against its ballots cast less the invalid ones, where the site prints them."""
    found: dict[str, object] = {"municipalities": len(towns(year)), "runoffs": second}
    if year == 1998:
        announced = int(_found(r"drugem krogu bodo v\s+(\d+)", clean(_early_text(INDEX_1998))))
        if announced != second:
            raise ValueError(f"1998: {second} second rounds parsed, the site announces {announced}")
        found["runoffs_match_announced"] = True
    found["vote_sums_off"] = vote_sums(year)
    return found


def vote_sums(year: int) -> list[str]:
    """The mayoral contests whose candidate votes differ from the ballots cast less the invalid ones."""
    off: list[str] = []
    for town in towns(year):
        for section in town.sections:
            turnout = town.turnout.get(section.round)
            if section.kind != "mayor" or turnout is None or turnout.cast is None or turnout.invalid is None:
                continue
            votes, net = sum(c.votes for c in section.candidates), turnout.cast - turnout.invalid
            if votes != net:
                off.append(f"{town.name} round {section.round}: votes {votes}, cast less invalid {net}")
    return off
