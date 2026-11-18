"""North Macedonia: the presidential elections of 1994 to 2024 from the State Election Commission.

The Commission's documents come from its electoral archive: reports and announcements, most of them scans, and two
tables that machines can read. What each election yields:

- 1994: the report to the Assembly, with the votes of the two candidates who received any and the registered voters.
- 1999: the report to the Assembly, with the first round, the second round of 14 November, and the second round as
  repeated at some polling stations on 5 December.
- 2004: the report to the Assembly: the two candidates who advanced and the second round in full.
- 2009: the Commission's tables by municipality for the first round, its national table for the second, and the
  announcement of the final result.
- 2014: the conclusion on the first round, and the table of the second round by polling station.
- 2019: the Commission's two conclusions, one for each round.
- 2024: the Commission's final report, with the final results of both rounds.

The Commission's results service, which publishes the polling-station results of 2019 and 2024 and of the first
round of 2014, does not answer outside North Macedonia, so those elections are built from their national figures.
Each figure read from a scan is stored with its page.
"""

import csv
import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.mk import sec, sobranie_president

SOURCE = "mk.sec_president"
MACEDONIA = str(uuid5(package.NAMESPACE, "polity\x1fMK"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMKD"))
HOME = "https://www.sec.mk/"
ARCHIVE = "https://izbornaarhiva.mk/wp-content/uploads/"
S_NATION, S_MUNI, S_STATION = "mk.nation", "mk.opstina_pretsedatel", "mk.izbiracko_mesto_pretsedatel"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
PLAN = sec.PLAN
RULES = {
    "formula": "two_round",
    "majority": 0.5,
    "advance": 2,
    "minimum": {"share": 0.5, "of": "registered"},
}

FILES = {
    "scan94": "2021/10/Извештај-од-претседателски-избори-1994_Собрание_оригинал_скен.pdf",
    "scan99": "2021/10/Извештај-за-претседателски-избори-199_ДИК.pdf",
    "scan04": "2021/10/Извештај-од-претседателски-избори-2004_Собрание_оригинал_скен.pdf",
    "r1_09": "2021/10/Конечни-резултати-претседателски-избори-2009-прв-круг.pdf",
    "muni_09": "2021/10/Конечни-резултати-претседателски-избори-2009-прв-круг-по-општини.pdf",
    "r2_09": "2021/10/Конечни-резултати-претседателски-избори-2009-втор-круг.pdf",
    "final_09": "2021/10/Објава-на-конечни-резултати-за-избор-на-претседател-на-РМ_2009.pdf",
    "r1_14": "2021/10/Заклучок-за-конечните-резултати-од-прв-круг-за-претседател_2014_ДИК_оригинал.pdf",
    "csv_14": "2021/10/Резултати-од-Претседателски-избори-2014-втор-круг-по-избирачки-места.csv",
    "r1_19": "2021/10/Коригирани-резултати-од-прв-круг-претседателски-избори.pdf",
    "r2_19": "2021/10/Конечни-резултати-втор-круг-претседателски-избори.pdf",
    "report24": "2025/09/10-3410_01_konechen-izveshtaј-za-sprovedenite-izbori-za-pretsedatel-i-izbori-za-prateniczi-vo-sobranie"
    "-na-rsm-2024-godina-1.pdf",
    "r2_24": "2025/09/zakluchok-za-konechnost-na-rezultati-od-glasaњeto-za-izbor-na-pretsedatel-na-rsm.pdf",
}
SUFFIX = {"csv_14": ".csv"}


def url(name: str) -> str:
    return ARCHIVE + urllib.parse.quote(FILES[name])


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SEC = key("source", "sec")
SHAPE_SOURCE = key("source", "geoboundaries")
SYSTEM = key("electoral_system", "president")
PRESIDENCY = sobranie_president.PRESIDENCY


@dataclass(frozen=True)
class Fig:
    value: int
    page: int = 1


@dataclass(frozen=True)
class Round:
    number: int
    first: date
    last: date
    file: str
    candidates: tuple[str, ...]  # in ballot order
    votes: dict[str, Fig]
    registered: Fig | None = None
    voted: Fig | None = None
    valid: Fig | None = None
    invalid: Fig | None = None
    winner: str | None = None
    advanced: tuple[str, ...] = ()
    completeness: float = 1.0
    scanned: bool = True
    note: str = ""
    superseded: Round | None = None  # the earlier count of the same round, if a repeat replaced it
    label: str = ""


@dataclass(frozen=True)
class Election:
    year: int
    rounds: tuple[Round, ...]
    extra: tuple[str, ...] = ()  # further files the notes cite


GLIGOROV, GEORGIEVSKI = "Киро Глигоров", "Љубиша Георгиевски"
NECIPI, TUPURKOVSKI, HALILI = "Муарем Неџипи", "Васил Тупурковски", "Мухамед Халили"
PETKOVSKI, ANDOV, TRAJKOVSKI = "Тито Петковски", "Стојан Андов", "Борис Трајковски"
CELILI, CRVENKOVSKI, OSTRENI, KEDEV = "Зиди Целили", "Бранко Црвенковски", "Гзим Острени", "Сашко Кедев"
SELJMANI, HODZA, RUZHIN = "Имер Сељмани", "Мируше Хоџа", "Нано Ружин"
IVANOV, BOSHKOSKI, BUDZAKU = "Ѓорге Иванов", "Љубе Бошкоски", "Агрон Буџаку"
DANAILOV = "Љубомир Данаилов-Фрчкоски"
HALIMI, PENDAROVSKI, POPOVSKI = "Илјаз Халими", "Стево Пендаровски", "Зоран Поповски"
REXHA, SILJANOVSKA = "Блерим Река", "Гордана Силјановска-Давкова"
JAKIMOVSKI, OSMANI = "Стевчо Јакимовски", "Бујар Османи"
VANKOVSKA, TARAVARI, DIMITRIEVSKI = "Билјана Ванковска-Цветковска", "Арбен Таравари", "Максим Димитриевски"

R99_2 = Round(
    2,
    date(1999, 11, 14),
    date(1999, 11, 14),
    "scan99",
    (PETKOVSKI, TRAJKOVSKI),
    {PETKOVSKI: Fig(514_599, 7), TRAJKOVSKI: Fig(591_972, 7)},
    registered=Fig(1_610_340, 6),
    voted=Fig(1_120_087, 6),
    invalid=Fig(17_271, 7),
    winner=TRAJKOVSKI,
    label="as counted on 14 November",
)
ELECTIONS = (
    Election(
        1994,
        (
            Round(
                1,
                date(1994, 10, 16),
                date(1994, 10, 30),
                "scan94",
                (GLIGOROV, GEORGIEVSKI),
                {GLIGOROV: Fig(715_774, 4), GEORGIEVSKI: Fig(197_210, 4)},
                registered=Fig(1_360_729, 4),
                winner=GLIGOROV,
                note="The Commission's report gives the votes of two candidates, and its date for the voting as 16 and "
                "30 October 1994. Four candidates were on the lists received by 25 September; the report gives votes "
                "for Gligorov and Georgievski only.",
            ),
        ),
    ),
    Election(
        1999,
        (
            Round(
                1,
                date(1999, 10, 31),
                date(1999, 10, 31),
                "scan99",
                (NECIPI, TUPURKOVSKI, HALILI, PETKOVSKI, ANDOV, TRAJKOVSKI),
                {
                    NECIPI: Fig(155_978, 5),
                    TUPURKOVSKI: Fig(163_206, 5),
                    HALILI: Fig(45_731, 5),
                    PETKOVSKI: Fig(343_606, 6),
                    ANDOV: Fig(111_983, 6),
                    TRAJKOVSKI: Fig(219_098, 6),
                },
                registered=Fig(1_610_340, 5),
                voted=Fig(1_050_449, 5),
                invalid=Fig(11_013, 5),
                advanced=(PETKOVSKI, TRAJKOVSKI),
            ),
            Round(
                2,
                date(1999, 11, 14),
                date(1999, 12, 5),
                "scan99",
                (PETKOVSKI, TRAJKOVSKI),
                {PETKOVSKI: Fig(513_614, 9), TRAJKOVSKI: Fig(582_808, 10)},
                registered=Fig(1_610_340, 9),
                voted=Fig(1_112_153, 9),
                invalid=Fig(16_281, 9),
                winner=TRAJKOVSKI,
                superseded=R99_2,
                label="final, with the repeat voting of 5 December",
                note="Voting was repeated on 5 December at the polling stations of 24 electoral units, after the "
                "Commission annulled the count there. These are the final figures of the Commission's report.",
            ),
        ),
    ),
    Election(
        2004,
        (
            Round(
                1,
                date(2004, 4, 14),
                date(2004, 4, 14),
                "scan04",
                (CELILI, CRVENKOVSKI, OSTRENI, KEDEV),
                {CRVENKOVSKI: Fig(385_347, 5), KEDEV: Fig(309_132, 5)},
                registered=Fig(1_695_103, 4),
                advanced=(CRVENKOVSKI, KEDEV),
                note="The pages of the report that give the other two candidates' votes and the turnout of the first "
                "round are not in the file the archive publishes.",
            ),
            Round(
                2,
                date(2004, 4, 28),
                date(2004, 4, 28),
                "scan04",
                (CRVENKOVSKI, KEDEV),
                {CRVENKOVSKI: Fig(550_317, 6), KEDEV: Fig(329_179, 6)},
                registered=Fig(1_695_103, 6),
                voted=Fig(909_289, 6),
                invalid=Fig(29_793, 6),
                winner=CRVENKOVSKI,
            ),
        ),
    ),
    Election(
        2009,
        (
            Round(
                1,
                date(2009, 3, 22),
                date(2009, 3, 22),
                "r1_09",
                (SELJMANI, HODZA, RUZHIN, IVANOV, BOSHKOSKI, BUDZAKU, DANAILOV),
                {},
                advanced=(IVANOV, DANAILOV),
                completeness=0.956,
                scanned=False,
                note="The Commission's tables of 31 March 2009 hold the data of 2,845 of the 2,976 polling stations "
                "(95.6%). The results by municipality sum to the national table.",
            ),
            Round(
                2,
                date(2009, 4, 5),
                date(2009, 4, 5),
                "r2_09",
                (IVANOV, DANAILOV),
                {},
                winner=IVANOV,
                scanned=False,
            ),
        ),
        extra=("muni_09", "final_09"),
    ),
    Election(
        2014,
        (
            Round(
                1,
                date(2014, 4, 12),
                date(2014, 4, 13),
                "r1_14",
                (IVANOV, HALIMI, PENDAROVSKI, POPOVSKI),
                {IVANOV: Fig(449_442), HALIMI: Fig(38_966), PENDAROVSKI: Fig(326_164), POPOVSKI: Fig(31_368)},
                registered=Fig(1_779_572),
                voted=Fig(869_547),
                valid=Fig(845_940),
                invalid=Fig(23_677),
                advanced=(IVANOV, PENDAROVSKI),
            ),
            Round(
                2,
                date(2014, 4, 27),
                date(2014, 4, 27),
                "csv_14",
                (IVANOV, PENDAROVSKI),
                {},
                winner=IVANOV,
                scanned=False,
            ),
        ),
    ),
    Election(
        2019,
        (
            Round(
                1,
                date(2019, 4, 21),
                date(2019, 4, 21),
                "r1_19",
                (REXHA, SILJANOVSKA, PENDAROVSKI),
                {REXHA: Fig(79_888), SILJANOVSKA: Fig(318_341), PENDAROVSKI: Fig(322_581)},
                registered=Fig(1_808_131),
                voted=Fig(753_556),
                advanced=(SILJANOVSKA, PENDAROVSKI),
            ),
            Round(
                2,
                date(2019, 5, 5),
                date(2019, 5, 5),
                "r2_19",
                (PENDAROVSKI, SILJANOVSKA),
                {PENDAROVSKI: Fig(435_656, 2), SILJANOVSKA: Fig(377_446, 2)},
                registered=Fig(1_808_131),
                voted=Fig(843_508),
                winner=PENDAROVSKI,
            ),
        ),
    ),
    Election(
        2024,
        (
            Round(
                1,
                date(2024, 4, 23),
                date(2024, 4, 24),
                "report24",
                (PENDAROVSKI, SILJANOVSKA, JAKIMOVSKI, OSMANI, VANKOVSKA, TARAVARI, DIMITRIEVSKI),
                {
                    PENDAROVSKI: Fig(180_499, 185),
                    SILJANOVSKA: Fig(363_085, 185),
                    JAKIMOVSKI: Fig(8_121, 185),
                    OSMANI: Fig(120_811, 185),
                    VANKOVSKA: Fig(41_331, 185),
                    TARAVARI: Fig(83_337, 185),
                    DIMITRIEVSKI: Fig(83_855, 185),
                },
                registered=Fig(1_814_317, 185),
                voted=Fig(905_622, 185),
                valid=Fig(881_040, 185),
                invalid=Fig(24_560, 185),
                advanced=(PENDAROVSKI, SILJANOVSKA),
            ),
            Round(
                2,
                date(2024, 5, 7),
                date(2024, 5, 8),
                "report24",
                (PENDAROVSKI, SILJANOVSKA),
                {PENDAROVSKI: Fig(251_899, 186), SILJANOVSKA: Fig(561_000, 186)},
                registered=Fig(1_814_317, 186),
                voted=Fig(861_218, 186),
                valid=Fig(812_899, 186),
                invalid=Fig(48_289, 186),
                winner=SILJANOVSKA,
            ),
        ),
        extra=("r2_24",),
    ),
)
NAMES = sorted({name for election in ELECTIONS for rnd in election.rounds for name in rnd.candidates})


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix=SUFFIX.get(name, ".pdf"))
    raw.fetch(SOURCE, sec.SHAPES, suffix=".geojson")


def day(election: Election) -> date:
    return election.rounds[0].first


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day(e)}" for e in ELECTIONS)]


def parse() -> None:
    sobranie_president.parse()
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def cached(name: str) -> raw.Artifact:
    return raw.cached(SOURCE, url(name))


def text(name: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(cached(name).path), "-"], check=True, capture_output=True, text=True
    ).stdout


def number(cell: str) -> int:
    return int(cell.replace(".", "").replace(",", "").replace(" ", ""))


# Geography.

MERGED = {"ВРАНЕШТИЦА": "Кичево", "ДРУГОВО": "Кичево", "ЗАЈАС": "Кичево", "ОСЛОМЕЈ": "Кичево"}
CHECKED_NAMES = {name.casefold(): name for name in sec.MUNICIPALITIES}


def muni_area(name: str) -> str:
    return key("area", S_MUNI, name)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 80 municipalities of North Macedonia's 2013 plan, with outlines, and the schemes of the presidential "
        "elections' municipalities and polling stations",
    )
    p.external |= {MACEDONIA, COUNTRY}
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen North Macedonia ADM2 (municipalities, 2016)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), from EuroGeographics and NTES data",
        notes="Boundary year 2016, built December 2023. The outlines of Drugovo, Oslomej, Vraneshtitsa, and Zajas are "
        "merged into Kichevo, as the 2013 plan did.",
    )
    shapes = raw.cached(SOURCE, sec.SHAPES)
    p.artifact(shapes, SHAPE_SOURCE)
    for code, rank, kind, noun in (
        (S_MUNI, 2, "administrative", "municipality"),
        (S_STATION, 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=MACEDONIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add(
        "boundary_set",
        id=key("boundary_set", S_MUNI, 2013),
        scheme_code=S_MUNI,
        vintage="2013",
        valid_from=PLAN,
        as_of=shapes.retrieved_at.date(),
    )
    pieces: dict[str, list[shapely.Geometry]] = {}
    for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]:
        name = feature["properties"]["shapeName"]
        target = "Kichevo" if name in sec.MERGED_INTO_KICHEVO else name
        pieces.setdefault(target, []).append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
    for name, english in sorted(sec.MUNICIPALITIES.items()):
        id = muni_area(name)
        p.add("area", id=id, scheme_code=S_MUNI)
        p.add("area_code", area_id=id, code=sec.slug(english), valid_from=PLAN)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=PLAN)
        p.name(id, name, "mk", "official")
        p.name(id, english, "en", "english", False)
        parts = pieces.get(english)
        if parts is None:
            raise ValueError(f"no outline for {english}")
        outline = shapely.union_all(parts)
        polygons = [
            part
            for geometry in shapely.get_parts(outline)
            if shapely.get_type_id(geometry) in (3, 6)
            for part in shapely.get_parts(geometry)
        ]
        multi = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=PLAN,
            geom=shapely.to_wkb(multi, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(multi), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system of North Macedonia's presidential elections, the State Election Commission as a source, "
        "and the candidates of 1994 to 2024",
        depends=["mk.sobranie_president/registry"],
    )
    p.external |= {MACEDONIA}
    p.add(
        "source",
        id=SEC,
        kind="official_body",
        publisher="Државна изборна комисија",
        title="Presidential elections: reports, announcements, and results",
        url=HOME,
        license="No license stated; published by the State Election Commission for public use",
        attribution="Државна изборна комисија на Република Македонија",
        notes="The Commission's reports to the Assembly, its announcements of final results, and its tables of "
        "results, as kept in the electoral archive at izbornaarhiva.mk.",
    )
    p.add(
        "electoral_system", id=SYSTEM, polity_id=MACEDONIA, valid_from=date(1991, 11, 17), rules=RULES, rules_version=1
    )
    p.name(SYSTEM, "Direct election of the President in up to two rounds", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Since the Constitution of 1991 the President is elected directly for five years. A candidate who "
        "receives the votes of a majority of all registered voters is elected in the first round. Otherwise the two "
        "candidates with the most votes stand in a second round, held two weeks later, where the candidate with the "
        "most votes is elected if at least 40% of registered voters take part; the rules store only the first "
        "round's threshold.",
        lang="en",
    )
    for name in NAMES:
        person = key("person", name)
        p.add("person", id=person)
        p.name(person, name, "mk", "official")
    return p


# Reading the tables.


@dataclass(frozen=True)
class Place:
    code: str
    name: str | None
    parent: str | None
    kind: str  # municipality, station, or abroad
    area: str | None  # the municipality's name on the 2013 plan
    registered: int | None
    voted: int | None
    valid: int | None
    invalid: int | None
    votes: tuple[int, ...]
    page: int = 0


NATIONAL_SUMMARY = re.compile(
    r"\n\s+([\d.]+)\s+(\d[\d.]*)\s+[\d,]+ %\s+([\d.]+)\s+[\d,]+ %\s+[\d,]+ %\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\n"
)
CANDIDATE_LINE = re.compile(r"\n\s+(\d)\s+(.+?)\s{2,}([\d.]+)\s+[\d,]+ %\s+[\d,]+ %")


def read_national(name: str, count: int) -> dict[str, object]:
    body = text(name)
    found = NATIONAL_SUMMARY.search(body)
    if found is None:
        raise ValueError(f"{name}: no summary line")
    votes = {int(m[1]): number(m[3]) for m in CANDIDATE_LINE.finditer(body)}
    if sorted(votes) != list(range(1, count + 1)):
        raise ValueError(f"{name}: candidates {sorted(votes)}")
    total = re.search(r"Вкупно гласови:\s+([\d.]+)", body)
    if total is None or number(total[1]) != sum(votes.values()):
        raise ValueError(f"{name}: the candidates do not sum to the printed total")
    return {
        "registered": number(found[1]),
        "voted": number(found[3]),
        "valid": number(found[4]),
        "invalid": number(found[5]),
        "votes": tuple(votes[n] for n in range(1, count + 1)),
    }


def municipalities_2009() -> list[Place]:
    body = text("muni_09")
    places: list[Place] = []
    for block in re.split(r"\n\s*Општина\s+", body)[1:]:
        name = block.split("\n")[0].strip()
        found = re.search(
            r"\n\s+([\d.]+)\s+(\d+)\s+[\d,]+ %\s+(\d+)\s+[\d,]+ %\s+[\d,]+ %\s+(\d+)\s+(\d+)\s+(\d+)\s*\n", block
        )
        if found is None:
            raise ValueError(f"no summary for {name}")
        votes = {int(m[1]): number(m[3]) for m in CANDIDATE_LINE.finditer(block)}
        if sorted(votes) != list(range(1, 8)):
            raise ValueError(f"{name}: candidates {sorted(votes)}")
        target = MERGED.get(name)
        area = target if target is not None else sec.municipality(name)
        places.append(
            Place(
                f"m:{name}",
                name,
                None,
                "municipality",
                area,
                number(found[1]),
                number(found[3]),
                number(found[4]),
                number(found[5]),
                tuple(votes[n] for n in range(1, 8)),
            )
        )
    if len(places) != 84:
        raise ValueError(f"{len(places)} municipalities, not 84")
    return places


def stations_2014() -> list[Place]:
    rows = list(csv.reader(cached("csv_14").path.read_text(encoding="utf-8").splitlines()))
    found: list[Place] = []
    seen_munis: dict[str, str] = {}
    places: list[Place] = []
    for row in rows[1:]:
        n = [int(cell) for cell in row[1:]]
        match = re.fullmatch(r"(\S+) - (.+)", row[0])
        if match is not None:
            code, area = match[1], sec.municipality(match[2])
            parent = seen_munis.setdefault(area, f"m:{area}")
            kind, name = "station", None
        else:
            code, kind, parent, area, name = sec.slug_city(row[0]), "abroad", None, None, row[0]
        found.append(Place(f"s:{code}", name, parent, kind, area, n[0], n[1], n[2], n[3], (n[4], n[5])))
    for area, code in seen_munis.items():
        places.append(Place(code, area, None, "municipality", area, None, None, None, None, ()))
    codes = [place.code for place in found]
    if len(set(codes)) != len(codes):
        raise ValueError("a polling station repeats")
    if len(found) != 3512:
        raise ValueError(f"{len(found)} stations, not 3512")
    return [*places, *found]


# Elections.


USED: dict[str, set[str]] = {}


def use(p: Package, name: str) -> str:
    """Record the artifact of a raw file once per package."""
    stored = cached(name)
    if stored.sha256 not in USED.setdefault(p.key, set()):
        USED[p.key].add(stored.sha256)
        return p.artifact(stored, SEC)
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{SEC}\x1f{stored.storage_key}"))


def page_ids(p: Package, name: str, pages: set[int]) -> dict[int, str]:
    stored = cached(name)
    artifact = use(p, name)
    found: dict[int, str] = {}
    for number_ in sorted(pages):
        found[number_] = key("page", stored.sha256, number_)
        if found[number_] in USED[p.key]:
            continue
        USED[p.key].add(found[number_])
        width, height = sec.page_size(stored.path, number_)
        p.add(
            "source_page",
            id=found[number_],
            artifact_id=artifact,
            page_number=number_,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{number_}.png",
            width=width,
            height=height,
        )
    return found


FACT_SCHEMA = sec.FACT_SCHEMA
LOCATOR_SCHEMA = sec.LOCATOR_SCHEMA


@dataclass
class Staged:
    rows: list[dict[str, object]] = field(default_factory=list)
    locators: list[dict[str, object]] = field(default_factory=list)


def build(election: Election) -> Package:
    first = day(election)
    p = Package(
        f"{SOURCE}/{first}",
        SOURCE,
        f"The {election.year} presidential election of North Macedonia"
        + (", both rounds" if len(election.rounds) > 1 else "")
        + ", from the State Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", "mk.sobranie_president/registry"],
    )
    p.external |= {MACEDONIA, COUNTRY}
    staged = Staged()
    previous: str | None = None
    for rnd in election.rounds:
        previous = build_round(p, election, rnd, previous, staged)
    for name in election.extra:
        use(p, name)
    p.frame("result_value", pl.DataFrame(staged.rows, schema=FACT_SCHEMA))
    if staged.locators:
        p.frame("value_locator", pl.DataFrame(staged.locators, schema=LOCATOR_SCHEMA))
    accept(p, election.year)
    return p


def accept(p: Package, year: int) -> None:
    reasons = {
        2014: {
            "accounting.ballots_sum": "The conclusion on the first round prints 869,547 voters but 845,940 valid and "
            "23,677 invalid ballots, which add to 869,617; five polling stations of the second round also print more "
            "voters than valid plus invalid ballots. The Commission's own figures are stored as printed",
        },
        2024: {
            "accounting.ballots_sum": "The final report's voters who voted exceed its valid plus invalid ballots by 22 in "
            "the first round and by 30 in the second; its own figures are stored as printed",
            "accounting.votes_valid": "The first round's candidates add to 881,039 and the report prints 881,040 valid "
            "ballots; its own figures are stored as printed",
        },
        2009: {
            "accounting.votes_valid": "The Commission's tables print 986,882 valid ballots in the first round and "
            "718,450 in the second, and candidates' votes of 986,862 and 718,444; its own figures are stored as "
            "printed",
        },
    }
    for code, reason in reasons.get(year, {}).items():
        p.accept(code, reason)


def unit(p: Package, code: str, scheme: str, area: str | None, relation: str, kind: str, parent: str | None) -> str:
    id = key("unit", scheme, code)
    p.add(
        "reporting_unit",
        id=id,
        polity_id=MACEDONIA,
        scheme_code=scheme,
        code=code,
        parent_id=parent,
        area_relation=relation,
        area_id=area,
        kind=kind,
    )
    return id


def build_round(p: Package, election: Election, rnd: Round, previous: str | None, staged: Staged) -> str:
    year = election.year
    tag = str(rnd.first)
    second = rnd.number == 2
    eid = key("election", tag)
    p.add(
        "election",
        id=eid,
        polity_id=MACEDONIA,
        kind="runoff" if second else "general",
        date_from=rnd.first,
        date_to=rnd.last,
        status="held",
    )
    path = f"mk/{rnd.first}-presidential" + ("-runoff" if second else "")
    p.add("slug", path=path, entity_id=eid, is_current=True)
    adjective = "Macedonian" if year < 2019 else "North Macedonian"
    ordinal = "second" if second else "first"
    suffix = ", second round" if second else (", first round" if len(election.rounds) > 1 else "")
    p.name(eid, f"{year} {adjective} presidential election{suffix}", "en", "official")
    p.name(
        eid,
        f"Претседателски избори {year}" + (f", {'втор' if second else 'прв'} круг" if len(election.rounds) > 1 else ""),
        "mk",
        "native",
        False,
    )
    cid = key("contest", tag)
    p.add(
        "contest",
        id=cid,
        election_id=eid,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=COUNTRY,
        magnitude=1,
        round_number=rnd.number,
        previous_round_id=previous,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(
        cid,
        f"{year} presidential election" + (f", {ordinal} round" if len(election.rounds) > 1 else ""),
        "en",
        "official",
    )
    p.add("slug", path=f"{path}/president", entity_id=cid, is_current=True)
    options: dict[str, str] = {}
    for position, name in enumerate(rnd.candidates, 1):
        option = options[name] = key("option", tag, name)
        p.add(
            "contest_option", id=option, contest_id=cid, kind="candidate", ballot_position=position, ballot_label=name
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, name),
            person_id=key("person", name),
            option_id=option,
            role="head",
            ballot_name=name,
            is_independent=False,
        )
    national = unit(p, f"{tag}:MK", S_NATION, COUNTRY, "equals", "aggregate", None)
    p.name(national, "North Macedonia", "en", "official")
    sets = []
    if rnd.superseded is not None:
        sets.append((rnd.superseded, False, None))
    earlier = key("result_set", tag, rnd.superseded.label) if rnd.superseded is not None else None
    sets.append((rnd, True, earlier))
    for item, default, supersedes in sets:
        set_id = key("result_set", tag, item.label)
        p.add(
            "result_set",
            id=set_id,
            election_id=eid,
            contest_id=cid,
            stage="official",
            kind="counted",
            source_id=SEC,
            status="active" if default else "superseded",
            is_default=default,
            supersedes_id=supersedes,
            completeness=item.completeness,
        )
        outcomes(p, set_id, item, options)
        figures(p, tag, set_id, item, options, national, staged, election)
    return cid


def outcomes(p: Package, set_id: str, rnd: Round, options: dict[str, str]) -> None:
    for name, option in options.items():
        if rnd.winner is not None:
            kind = "elected" if name == rnd.winner else "lost"
        else:
            kind = "advanced" if name in rnd.advanced else "eliminated"
        p.add("outcome", result_set_id=set_id, option_id=option, kind=kind)


def fact(rs: str, unit_id: str, option: str | None, measure: int, value: int, flags: int = 0) -> dict[str, object]:
    return sec.fact(rs, unit_id, option, measure, value, flags)


def figures(
    p: Package,
    tag: str,
    rs: str,
    rnd: Round,
    options: dict[str, str],
    national: str,
    staged: Staged,
    election: Election,
) -> None:
    year = election.year
    methodology = (
        f"The {rnd.label + ' ' if rnd.label else ''}result of the {'second' if rnd.number == 2 else 'first'} round "
        "from the State Election Commission's documents. " + rnd.note
    )
    if rnd.scanned:
        figs: dict[tuple[str | None, int], Fig] = {}
        for measure, fig in (
            (REGISTERED, rnd.registered),
            (BALLOTS, rnd.voted),
            (VALID, rnd.valid),
            (INVALID, rnd.invalid),
        ):
            if fig is not None:
                figs[(None, measure)] = fig
        for name, fig in rnd.votes.items():
            figs[(options[name], VOTES)] = fig
        pages = page_ids(p, rnd.file, {fig.page for fig in figs.values()})
        for (option, measure), fig in figs.items():
            staged.rows.append(fact(rs, national, option, measure, fig.value, DIGITIZED))
            p.add("declared", result_set_id=rs, option_id=option, measure=measure, value=fig.value)
            staged.locators.append(
                {
                    "result_set_id": rs,
                    "unit_id": national,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "page_id": pages[fig.page],
                    "source_id": None,
                    "x0": 0.0,
                    "y0": 0.0,
                    "x1": 1.0,
                    "y1": 1.0,
                }
            )
        methodology += (
            " Each figure was read from the page of the scan it is stored with; the locator's box is the whole page."
        )
    elif (year, rnd.number) == (2009, 1):
        use(p, "r1_09")
        total = read_national("r1_09", 7)
        places = municipalities_2009()
        sums = [
            sum(getattr(place, field_) for place in places) for field_ in ("registered", "voted", "valid", "invalid")
        ]
        if sums != [total["registered"], total["voted"], total["valid"], total["invalid"]]:
            raise ValueError("the municipalities do not sum to the national table")
        if tuple(sum(place.votes[i] for place in places) for i in range(7)) != total["votes"]:
            raise ValueError("the municipalities' votes do not sum to the national table")
        declare(p, rs, options, rnd, national, total, staged, units=places)
    elif (year, rnd.number) == (2009, 2):
        use(p, "r2_09")
        total = read_national("r2_09", 2)
        declare(p, rs, options, rnd, national, total, staged)
        methodology += (
            " The Commission's announcement of 13 April 2009 gives the same votes, 453,616 and 264,828, and "
            "45,589 invalid ballots."
        )
    else:
        places = stations_2014()
        use(p, "csv_14")
        declare(p, rs, options, rnd, national, None, staged, units=places)
        methodology += (
            " The table gives each polling station's registered voters, voters who voted, valid and invalid "
            "ballots, and each candidate's votes. The Commission's national figures for this round are not in the "
            "files within reach, so none is declared."
        )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=methodology, lang="en")


def declare(
    p: Package,
    rs: str,
    options: dict[str, str],
    rnd: Round,
    national: str,
    total: dict[str, object] | None,
    staged: Staged,
    units: list[Place] | None = None,
) -> None:
    names = list(options)
    if total is not None:
        for measure, field_ in ((REGISTERED, "registered"), (BALLOTS, "voted"), (VALID, "valid"), (INVALID, "invalid")):
            p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=total[field_])
        votes = total["votes"]
        assert isinstance(votes, tuple)
        for name, value in zip(names, votes, strict=True):
            p.add("declared", result_set_id=rs, option_id=options[name], measure=VOTES, value=value)
    if units is None:
        assert total is not None
        for measure, field_ in ((REGISTERED, "registered"), (BALLOTS, "voted"), (VALID, "valid"), (INVALID, "invalid")):
            staged.rows.append(fact(rs, national, None, measure, int(str(total[field_]))))
        votes = total["votes"]
        assert isinstance(votes, tuple)
        for name, value in zip(names, votes, strict=True):
            staged.rows.append(fact(rs, national, options[name], VOTES, value))
        return
    ids: dict[str, str] = {}
    for place in units:
        if place.kind == "municipality":
            if place.area is None:
                raise ValueError("a municipality without an area")
            merged = place.name in MERGED or place.area == "Кичево"
            ids[place.code] = unit(
                p,
                f"{rnd.first}:{place.code}",
                S_MUNI,
                muni_area(place.area),
                "within" if merged else "equals",
                "aggregate",
                national,
            )
            p.name(ids[place.code], place.name or place.area, "mk", "official")
        else:
            parent = national if place.parent is None else ids[place.parent]
            if place.kind == "abroad":
                ids[place.code] = unit(
                    p, f"{rnd.first}:{place.code}", S_STATION, None, "unlocated", "abroad_mission", parent
                )
            else:
                assert place.area is not None
                ids[place.code] = unit(
                    p,
                    f"{rnd.first}:{place.code}",
                    S_STATION,
                    muni_area(place.area),
                    "within",
                    "standard",
                    parent,
                )
            if place.name is not None:
                p.name(ids[place.code], place.name, "mk", "official")
        if place.registered is None:
            continue
        for measure, value in (
            (REGISTERED, place.registered),
            (BALLOTS, place.voted),
            (VALID, place.valid),
            (INVALID, place.invalid),
        ):
            if value is not None:
                staged.rows.append(fact(rs, ids[place.code], None, measure, value))
        for name, value in zip(names, place.votes, strict=True):
            staged.rows.append(fact(rs, ids[place.code], options[name], VOTES, value))
