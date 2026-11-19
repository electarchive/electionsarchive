"""Iraq's governorate council elections of 18 December 2023, from the Independent High Electoral Commission's results.

The Commission published the final results for the 15 governorates outside the Kurdistan Region as a 17-page scan of
the list tables (valid votes, seats by type, and votes and seats for each list) and one candidate file per governorate
(list, candidate number, name, sex, votes, and whether the candidate won). The candidate files are PDFs printed from a
spreadsheet whose text layer loses the glyphs it cannot map to a character (dotless skeletons, their separate dot
glyphs, and ligatures), so a candidate's name is read from the printed glyphs through the embedded fonts' own
substitution tables (see `glyphs`); every number comes from the text layer and is checked against the scanned tables.
"""

import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5
from xml.etree import ElementTree

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.iq import glyphs

SOURCE = "iq.ihec_gc_2023"
PAGE = "https://ihec.iq/29239-2/"
BASE = "https://ihec.iq/wp-content/uploads/2024/01/"
LISTS = f"{BASE}القوائم.pdf"
# Governorate file stem -> (ISO 3166-2 code, English name, Arabic name)
GOVERNORATES = {
    "الأنبار": ("IQ-AN", "Anbar", "الأنبار"),
    "البصرة": ("IQ-BA", "Basra", "البصرة"),
    "المثنى": ("IQ-MU", "Muthanna", "المثنى"),
    "القادسية": ("IQ-QA", "Al-Qadisiyyah", "القادسية"),
    "بابل": ("IQ-BB", "Babylon", "بابل"),
    "بغداد": ("IQ-BG", "Baghdad", "بغداد"),
    "ذي-قار": ("IQ-DQ", "Dhi Qar", "ذي قار"),
    "ديالى": ("IQ-DI", "Diyala", "ديالى"),
    "صلاح-الدين": ("IQ-SD", "Saladin", "صلاح الدين"),
    "كربلاء": ("IQ-KA", "Karbala", "كربلاء"),
    "كركوك": ("IQ-KI", "Kirkuk", "كركوك"),
    "ميسان": ("IQ-MA", "Maysan", "ميسان"),
    "نينوى": ("IQ-NI", "Nineveh", "نينوى"),
    "النجف": ("IQ-NA", "Najaf", "النجف"),
    "واسط": ("IQ-WA", "Wasit", "واسط"),
}


SYSTEM_PDF = (
    "https://ihec.iq/wp-content/uploads/2023/11/"
    "%D9%86%D8%B8%D8%A7%D9%85-%D8%AA%D9%88%D8%B2%D9%8A%D8%B9-%D8%A7%D9%84%D9%85%D9%82%D8%A7%D8%B9%D8%AF-"
    "%D9%84%D8%A7%D9%86%D8%AA%D8%AE%D8%A7%D8%A8%D8%A7%D8%AA-%D9%85%D8%AC%D8%A7%D9%84%D8%B3-"
    "%D8%A7%D9%84%D9%85%D8%AD%D8%A7%D9%81%D8%B8%D8%A7%D8%AA-%D8%BA%D9%8A%D8%B1-%D8%A7%D9%84%D9%85%D9%86%D8%AA%D8%B8"
    "%D9%85%D8%A9-%D8%A8%D8%A5%D9%82%D9%84%D9%8A%D9%85-%D8%B1%D9%82%D9%85-9-%D9%84%D8%B3%D9%86%D8%A9-2023.pdf"
)
GB_GOVERNORATES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/IRQ/ADM1/"
    "geoBoundaries-IRQ-ADM1.geojson"
)
IRAQ = str(uuid5(package.NAMESPACE, "polity\x1fIQ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIRQ"))
DAY = date(2023, 12, 18)
BOUNDARY_FROM = date(2014, 1, 1)
SCHEME = "iq.governorate"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, PREFERENCE = 1, 2, 3, 4, 5, 7
REGISTRY, GEO, ELECTION = f"{SOURCE}/registry", f"{SOURCE}/geo", f"{SOURCE}/gc-2023"
# geoBoundaries' name for each governorate's shape.
SHAPES = {
    "الأنبار": "Al-Anbar", "البصرة": "Al-Basrah", "المثنى": "Al-Muthanna", "القادسية": "Al-Qadisiyah",
    "بابل": "Babil", "بغداد": "Baghdad", "ذي-قار": "Dhi Qar", "ديالى": "Diyala", "صلاح-الدين": "Salah al-Din",
    "كربلاء": "Karbala", "كركوك": "Kirkuk", "ميسان": "Maysan", "نينوى": "Ninawa", "النجف": "An-Najaf",
    "واسط": "Wasit",
}  # fmt: skip


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_IHEC = key("source", "ihec-results")
SOURCE_GEO = key("source", "geoboundaries-adm1")
SYSTEM = key("electoral_system", "governorate-council-2023")


def url(stem: str) -> str:
    return f"{BASE}{stem}.pdf"


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for stem in GOVERNORATES:
        raw.fetch(SOURCE, url(stem), suffix=".pdf")
    raw.fetch(SOURCE, LISTS, suffix=".pdf")
    raw.fetch(SOURCE, SYSTEM_PDF, suffix=".pdf")
    raw.fetch(SOURCE, GB_GOVERNORATES, suffix=".geojson")


@dataclass
class Row:
    page: int
    y: float
    party_number: int
    party_votes: int
    number: int
    votes: int
    female: bool
    won: bool


# Column bounds in points on the 792-point-wide landscape page, right to left.
COL_GOV, COL_PARTY_NUMBER, COL_PARTY_NAME, COL_PARTY_VOTES, COL_NUMBER, COL_NAME, COL_SEX, COL_VOTES, COL_STATUS = (
    (683, 800),
    (635, 683),
    (491, 635),
    (433, 491),
    (383, 433),
    (239, 383),
    (200, 239),
    (130, 200),
    (70, 130),
)


MALE, FEMALE = {"ركذ"}, {"نا", "ىثنا", "ثنا"}


def words(path: str) -> list[list[tuple[float, float, float, float, str]]]:
    out = subprocess.run(["pdftotext", "-bbox", path, "-"], check=True, capture_output=True).stdout.decode()
    out = re.sub(r"<!DOCTYPE[^>]*>", "", out).replace('xmlns="http://www.w3.org/1999/xhtml"', "")
    pages = []
    for page in ElementTree.fromstring(out).iter("page"):
        pages.append(
            [
                (
                    float(w.get("xMin", 0)),
                    float(w.get("xMax", 0)),
                    float(w.get("yMin", 0)),
                    float(w.get("yMax", 0)),
                    w.text or "",
                )
                for w in page.iter("word")
            ]
        )
    return pages


def inside(word: tuple[float, float, float, float, str], column: tuple[int, int]) -> bool:
    centre = (word[0] + word[1]) / 2
    return column[0] <= centre < column[1]


def rows(path: str) -> list[Row]:
    found = []
    for number, page in enumerate(words(path), start=1):
        by_y: dict[float, list[tuple[float, float, float, float, str]]] = {}
        for word in page:
            by_y.setdefault(round(word[2], 1), []).append(word)
        for y, line in sorted(by_y.items()):

            def digits(column: tuple[int, int]) -> int | None:
                cell = [w[4] for w in line if inside(w, column) and w[4].isdigit()]
                return int(cell[0]) if len(cell) == 1 else None

            votes, cand, party_votes, party = (
                digits(COL_VOTES),
                digits(COL_NUMBER),
                digits(COL_PARTY_VOTES),
                digits(COL_PARTY_NUMBER),
            )
            if votes is None or cand is None or party_votes is None or party is None:
                continue
            sex = "".join(w[4] for w in line if inside(w, COL_SEX))
            status = "".join(w[4] for w in line if inside(w, COL_STATUS))
            if sex not in MALE | FEMALE:
                raise ValueError(f"{path} page {number}: unread sex {sex!r}")
            found.append(Row(number, y, party, party_votes, cand, votes, sex in FEMALE, status != ""))
    return found


ROW_HEIGHT = 9.5
NAME_COLUMN = (241, 381)


def _letters(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub("[\u064b-\u065f\u0640\u200c-\u200f\u202a-\u202e]", "", text)
    return re.sub("[^\u0621-\u064a]", "", re.sub("[أإآٱ]", "ا", text))


def _layer(words_in_row: list[tuple[float, float, float, float, str]]) -> str:
    """The letters the text layer kept of a name: whole words in visual order, so each is reversed to read it."""
    cell = sorted((w for w in words_in_row if inside(w, COL_NAME)), key=lambda w: -w[0])
    return "".join(_letters(w[4][::-1]) for w in cell)


def consistent(layer: str, read: str) -> bool:
    """Whether the letters the text layer kept are, in order, among the letters read from the glyphs."""
    letters = iter(_letters(read))
    return all(letter in letters for letter in layer)


@cache
def _decoder() -> glyphs.Decoder:
    return glyphs.Decoder(raw.cached(SOURCE, url(stem)).path for stem in GOVERNORATES)


@cache
def names(stem: str) -> dict[tuple[int, float], str]:
    """The candidate names of a governorate's file, read from the printed glyphs and checked against the text layer."""
    path = raw.cached(SOURCE, url(stem)).path
    found = rows(str(path))
    pages = _decoder().glyphs(path)
    read: dict[tuple[int, float], str] = {}
    for row in found:
        cell = [
            g
            for g in pages[row.page - 1]
            if NAME_COLUMN[0] <= g.x <= NAME_COLUMN[1] and row.y < g.y <= row.y + ROW_HEIGHT
        ]
        read[(row.page, row.y)] = glyphs.text(cell)
    layers: dict[tuple[int, float], str] = {}
    for number, page in enumerate(words(str(path)), start=1):
        by_y: dict[float, list[tuple[float, float, float, float, str]]] = {}
        for word in page:
            by_y.setdefault(round(word[2], 1), []).append(word)
        layers.update({(number, y): _layer(line) for y, line in by_y.items()})
    wrong = [f"{k}: {layers[k]!r} {v!r}" for k, v in read.items() if not v or not consistent(layers[k], v)]
    if wrong:
        raise ValueError(f"{stem}: names that disagree with the text layer:\n" + "\n".join(wrong))
    return read


@dataclass
class Entry:
    number: int
    name: str
    votes: int
    seats: int


@dataclass
class Table:
    stem: str
    valid: int
    men: int
    women: int
    components: int
    seats: int
    lists: list[Entry]


def tables() -> dict[str, Table]:
    """The list tables of the scan, as read page by page into the data file beside this module."""
    found: dict[str, Table] = {}
    current: Table | None = None
    for line in Path(__file__).with_name("ihec_gc_2023_lists.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        parts = line.split("|")
        if line.startswith("@"):
            current = found[parts[0][1:]] = Table(parts[0][1:], *(int(x) for x in parts[1:]), [])
        else:
            assert current is not None
            current.lists.append(Entry(int(parts[0]), parts[1], int(parts[2]), int(parts[3])))
    for table in found.values():
        if (
            sum(e.votes for e in table.lists) != table.valid
            or sum(e.seats for e in table.lists) != table.seats
            or table.men + table.women + table.components != table.seats
        ):
            raise ValueError(f"{table.stem}: the list table does not add up")
    if set(found) != set(GOVERNORATES):
        raise ValueError("the list tables do not cover the 15 governorates")
    return found


COMPONENTS = {
    "مسيحي": "Christian",
    "صابئي": "Sabean-Mandaean",
    "فيلي": "Feyli Kurd",
    "شبكي": "Shabak",
    "ايزيدي": "Yazidi",
}


def component(name: str) -> str | None:
    """The component a list stands for, named after a slash at the end of its name."""
    if "/" not in name:
        return None
    return COMPONENTS.get(name.rsplit("/", 1)[1].strip().replace("أ", "ا"))


@dataclass
class Candidate:
    stem: str
    list_number: int
    number: int
    name: str
    votes: int
    female: bool
    won: bool


def candidates(stem: str) -> list[Candidate]:
    path = str(raw.cached(SOURCE, url(stem)).path)
    read = names(stem)
    found = [
        Candidate(stem, r.party_number, r.number, read[(r.page, r.y)], r.votes, r.female, r.won) for r in rows(path)
    ]
    if len({(c.list_number, c.number) for c in found}) != len(found):
        raise ValueError(f"{stem}: a candidate number repeats within a list")
    return found


def english_slug(stem: str) -> str:
    return changeset.slug(GOVERNORATES[stem][1])


def area(stem: str) -> str:
    return key("area", GOVERNORATES[stem][0])


def packages() -> list[str]:
    return [GEO, REGISTRY, ELECTION]


def parse() -> None:
    for build in (geo(), registry(), election()):
        build.write()


def emit() -> None:
    package.run("load", packages())


def _shapes() -> dict[str, shapely.Geometry]:
    found: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, GB_GOVERNORATES).path.read_bytes())["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        parts = [piece for part in parts for piece in shapely.get_parts(part)]
        found[feature["properties"]["shapeName"]] = shapely.set_srid(shapely.MultiPolygon(parts), 4326)
    return found


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The 15 governorates of Iraq outside the Kurdistan Region that elected councils in 2023, from geoBoundaries",
    )
    p.external |= {IRAQ, COUNTRY}
    p.add(
        "source",
        id=SOURCE_GEO,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Iraq ADM1 (governorates)",
        url="https://www.geoboundaries.org/",
        license="CC0 1.0 Universal",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="geoBoundaries",
        notes="Boundary year 2022, from Wikimedia Commons; the file's license is the one the geoBoundaries API states "
        "for Iraq.",
    )
    p.artifact(raw.cached(SOURCE, GB_GOVERNORATES), SOURCE_GEO)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=IRAQ,
        kind="administrative",
        rank=1,
        noun="governorate",
        code_system="iso_3166_2",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, 2022),
        scheme_code=SCHEME,
        vintage="2022",
        valid_from=BOUNDARY_FROM,
        as_of=date(2023, 1, 19),
    )
    shapes = _shapes()
    for stem, (code, english, arabic) in GOVERNORATES.items():
        id = area(stem)
        shape = shapes[SHAPES[stem]]
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, english, "en", "official")
        p.name(id, arabic, "ar", "native", False)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=BOUNDARY_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=SOURCE_GEO,
        )
    return p


SYSTEM_NOTE = (
    "The seat allocation system of the Independent High Electoral Commission (No. 9 of 2023). Each governorate's "
    "regular seats are shared among open lists by the modified Sainte-Laguë method: a list's votes are divided by 1.7, "
    "3, 5, 7, and so on, and the seats go to the highest quotients. A list's seats go to its candidates with the most "
    "votes. A quarter of the regular seats in each governorate (75 of 275) are reserved for women, and the system "
    "replaces the lowest-ranked men elected on a list with its best-ranked women where the quota is not met. Ten "
    "further seats are reserved for components: Christians in Baghdad, Basra, Kirkuk, and Nineveh; Sabean-Mandaeans in "
    "Baghdad and Maysan; Feyli Kurds in Baghdad and Wasit; and Shabak and Yazidis in Nineveh. The rules here hold the "
    "formula and the list type only; the engine has no field for the women's quota or the component seats."
)
METHOD = (
    "The final results that the Commission's Board of Commissioners certified for the {name} council. The printed list "
    "table gives the valid votes, the seats by type ({men} for men, {women} for women, {components} for components), "
    "and each list's votes and seats. The candidate file gives each candidate's votes and whether the candidate won. "
    "The governorate is the finest unit the Commission publishes for this election, so the result is stored as one "
    "unit. A list's votes are the table's, which add up to the valid votes, and a candidate's votes, which the "
    "candidate file prints, are stored apart as preference votes. The table is a scan, read by hand and checked "
    "against the valid votes, the seats, and the winners in the candidate file.{extra}{gap}"
)


def registry() -> Package:
    p = Package(
        REGISTRY, SOURCE, "Iraq's governorate councils, their electoral system, and the lists of 2023", depends=[GEO]
    )
    p.external.add(IRAQ)
    p.add(
        "source",
        id=SOURCE_IHEC,
        kind="official_body",
        publisher="Independent High Electoral Commission of Iraq",
        title="Final results of the 2023 governorate council elections, certified by the Board of Commissioners",
        url=PAGE,
        license="No license stated; official results published by the electoral authority for public use",
        attribution="Independent High Electoral Commission of Iraq",
    )
    for artifact in (
        raw.cached(SOURCE, PAGE),
        raw.cached(SOURCE, LISTS),
        raw.cached(SOURCE, SYSTEM_PDF),
        *(raw.cached(SOURCE, url(stem)) for stem in GOVERNORATES),
    ):
        p.artifact(artifact, SOURCE_IHEC)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=IRAQ,
        rules={
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.7,
            "lists": {"order": "open", "vacancy": "next_by_votes"},
        },
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for stem, (_, english, arabic) in GOVERNORATES.items():
        body = key("body", stem)
        p.add("body", id=body, polity_id=IRAQ, kind="council")
        p.name(body, f"{english} Governorate Council", "en", "official")
        p.name(body, f"مجلس محافظة {arabic}", "ar", "native", False)
    # A list is a party when it fielded more than one candidate; a one-candidate list is a person standing alone.
    sizes: Counter[tuple[str, int]] = Counter()
    labels: defaultdict[int, set[str]] = defaultdict(set)
    for stem, table in tables().items():
        for c in candidates(stem):
            sizes[(stem, c.list_number)] += 1
        for entry in table.lists:
            labels[entry.number].add(entry.name)
    for number in sorted(labels):
        group = [sizes[(stem, number)] for stem in GOVERNORATES]
        if len(labels[number]) != 1:
            raise ValueError(f"list {number} has several names: {labels[number]}")
        if max(group) < 2:
            continue
        id = party(number)
        p.add("party", id=id, kind="list_vehicle", polity_id=IRAQ)
        p.add("external_id", entity_id=id, scheme="iq.ihec.list", value=str(number))
        p.name(id, next(iter(labels[number])), "ar", "official")
    return p


def party(number: int) -> str:
    return key("party", number)


def file_totals(stem: str) -> dict[int, int]:
    """The list totals the candidate file prints beside each candidate."""
    return {r.party_number: r.party_votes for r in rows(str(raw.cached(SOURCE, url(stem)).path))}


def election() -> Package:
    p = Package(
        ELECTION,
        SOURCE,
        "The 2023 governorate council elections in the 15 governorates outside the Kurdistan Region, with candidate "
        "votes, from the Commission's final results",
        depends=[GEO, REGISTRY],
    )
    p.external |= {IRAQ, COUNTRY}
    for artifact in (
        raw.cached(SOURCE, LISTS),
        *(raw.cached(SOURCE, url(stem)) for stem in GOVERNORATES),
    ):
        p.uses(artifact)
    election = key("election")
    p.add(
        "election",
        id=election,
        polity_id=IRAQ,
        kind="local",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "2023 Iraqi governorate council elections", "en", "official")
    p.name(election, "انتخابات مجالس المحافظات 2023", "ar", "native", False)
    path = f"iq/{DAY}-local"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="context",
        body_md="Councils of the governorates not organized in a region: 15 governorates, 285 seats, of which 275 are "
        "regular seats (75 reserved for women) and 10 are reserved for components. The Kurdistan Region's three "
        "governorates did not vote.",
        lang="en",
    )
    p.add(
        "note",
        id=key("note", election, "names"),
        entity_id=election,
        kind="caveat",
        body_md="The candidate files are printed from a spreadsheet whose text layer loses the letters drawn as dotless "
        "shapes with separate dots, so each candidate's name was read from the printed glyphs, through the embedded "
        "font's own tables, and checked against the letters the text layer kept. A name is spelled as printed, final "
        "yeh (ي) and alef maksura (ى) included. The list names, the votes, the seats, and who won come from the "
        "Commission's tables and the text layer.",
        lang="en",
    )
    singles = Counter[tuple[str, int]]()
    for stem in GOVERNORATES:
        for c in candidates(stem):
            singles[(stem, c.list_number)] += 1
    for stem, table in sorted(tables().items(), key=lambda item: GOVERNORATES[item[0]][1]):
        _, english, arabic = GOVERNORATES[stem]
        cands = candidates(stem)
        contest = key("contest", stem)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=key("body", stem),
            district_area_id=area(stem),
            magnitude=table.seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{english} Governorate Council", "en", "official")
        p.name(contest, f"مجلس محافظة {arabic}", "ar", "native", False)
        p.add("slug", path=f"{path}/{english_slug(stem)}", entity_id=contest, is_current=True)
        result_set = key("result_set", stem)
        totals = file_totals(stem)
        gap = sum(abs(e.votes - totals[e.number]) for e in table.lists if e.number in totals)
        winners = [e for e in table.lists if e.seats and component(e.name)]
        extra = (
            " The component seats went to the lists "
            + ", ".join(f"{e.name} ({component(e.name)})" for e in winners)
            + "."
            if winners
            else ""
        )
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_IHEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(
                name=f"{english} Governorate",
                men=table.men,
                women=table.women,
                components=table.components,
                extra=extra,
                gap=f" The candidate file also prints a list total beside each candidate, which differs from the "
                f"table's by {gap:,} votes across the lists; the table's total is the one stored."
                if gap
                else "",
            ),
            lang="en",
        )
        unit = key("unit", stem)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IRAQ,
            scheme_code=SCHEME,
            code=GOVERNORATES[stem][0],
            area_relation="equals",
            area_id=area(stem),
            kind="standard",
        )
        p.name(unit, f"{english} Governorate", "en", "official")
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=table.valid)
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=VALID,
            count_round=0,
            flags=0,
            value=table.valid,
        )
        options: dict[int, str] = {}
        for entry in table.lists:
            option = options[entry.number] = key("option", stem, entry.number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(entry.number) if singles[(stem, entry.number)] > 1 else None,
                ballot_position=entry.number,
                ballot_label=entry.name,
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=entry.votes)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=entry.votes,
            )
            if entry.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry.seats)
        for c in cands:
            tag = (stem, c.list_number, c.number)
            person = key("person", *tag)
            p.add("person", id=person, birth_precision=None, gender="female" if c.female else "male")
            p.name(person, c.name, "ar", "official")
            child = key("option", *tag, "candidate")
            p.add(
                "contest_option",
                id=child,
                contest_id=contest,
                kind="candidate",
                parent_option_id=options[c.list_number],
                ballot_position=c.number,
                ballot_label=c.name,
            )
            candidacy = key("candidacy", *tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[c.list_number],
                role="list_member",
                ballot_name=c.name,
            )
            p.add("declared", result_set_id=result_set, option_id=child, measure=PREFERENCE, value=c.votes)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=child,
                channel=0,
                measure=PREFERENCE,
                count_round=0,
                flags=0,
                value=c.votes,
            )
            if c.won:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[c.list_number],
                    candidacy_id=candidacy,
                    kind="elected",
                )
    return p
