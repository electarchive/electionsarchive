"""Russia's federal votes by precinct election commission (УИК), from the Central Election Commission's results.

The Commission publishes its results by precinct on izbirkom.ru, whose hosts do not answer scripted requests from
outside Russia. Sergey Shpilkin scraped those pages into one CSV file per federal vote, which Dmitry Kobak keeps in
the `data/` folder of the github.com/dkobak/elections repository. This module reads those files at one pinned commit
of the repository; the source of every figure is still the Commission, and the repository is where the file came from.

- 1 July 2020: the all-Russian vote on the amendments to the Constitution, by precinct, with the precinct's
  territorial commission (ТИК) and federal subject. The file leaves out the two pseudo-precincts that held the
  results of electronic voting in Moscow and Nizhny Novgorod Oblast; the repository's notes give their registered
  voters, and with them the file's sum matches the Commission's national total.

Precincts in the Republic of Crimea and Sevastopol are placed within the Ukrainian regions that hold them, since the
archive counts those territories under Ukraine; Baikonur's precincts are placed within Kazakhstan.
"""

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ru.cik_uik"
RUSSIA = str(uuid5(package.NAMESPACE, "polity\x1fRU"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fRUS"))
KAZAKHSTAN = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fKAZ"))
# The Ukrainian regions that `ua.cvk` loaded, under which the archive counts Crimea and Sevastopol.
CRIMEA = "c7d282eb-1cc1-5f2c-b42f-63c637765afe"
SEVASTOPOL = "4d0f8ac5-7276-51f1-ab07-b9c719257152"

REPOSITORY = "https://github.com/dkobak/elections"
COMMIT = "a6530610baf200b15442239511a1375c51ba1cb5"
FILES = f"https://raw.githubusercontent.com/dkobak/elections/{COMMIT}/data/"
NOTES = f"{FILES}README.md"
VOTE_2020 = f"{FILES}2020.csv.zip"
RESULTS_2020 = "http://www.vybory.izbirkom.ru/region/izbirkom?action=show&vrn=100100163596966&type=465"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/RUS/ADM1/"
LAYER = f"{GEOBOUNDARIES}geoBoundaries-RUS-ADM1_simplified.geojson"
META = f"{GEOBOUNDARIES}geoBoundaries-RUS-ADM1-metaData.json"
GRID = 1e-6

SUBJECT = "ru.subject"
TIK = "ru.tik"
UIK = "ru.uik"

# The plan of federal subjects since Zabaykalsky Krai was formed on 1 March 2008, the last of the mergers of
# autonomous okrugs into their neighbors.
PLAN_FROM = date(2008, 3, 1)
DAY_2020 = date(2020, 7, 1)
FIRST_DAY_2020 = date(2020, 6, 25)

REGISTERED, CAST, INVALID, VOTES, VOTERS = 2, 3, 5, 1, 14

# The 83 federal subjects within Russia's internationally recognized borders, by ISO 3166-2 suffix, with the name
# the Commission's files print.
SUBJECTS = {
    "AD": "Республика Адыгея (Адыгея)",
    "AL": "Республика Алтай",
    "BA": "Республика Башкортостан",
    "BU": "Республика Бурятия",
    "DA": "Республика Дагестан",
    "IN": "Республика Ингушетия",
    "KB": "Кабардино-Балкарская Республика",
    "KL": "Республика Калмыкия",
    "KC": "Карачаево-Черкесская Республика",
    "KR": "Республика Карелия",
    "KO": "Республика Коми",
    "ME": "Республика Марий Эл",
    "MO": "Республика Мордовия",
    "SA": "Республика Саха (Якутия)",
    "SE": "Республика Северная Осетия - Алания",
    "TA": "Республика Татарстан (Татарстан)",
    "TY": "Республика Тыва",
    "UD": "Удмуртская Республика",
    "KK": "Республика Хакасия",
    "CE": "Чеченская Республика",
    "CU": "Чувашская Республика - Чувашия",
    "ALT": "Алтайский край",
    "ZAB": "Забайкальский край",
    "KAM": "Камчатский край",
    "KDA": "Краснодарский край",
    "KYA": "Красноярский край",
    "PER": "Пермский край",
    "PRI": "Приморский край",
    "STA": "Ставропольский край",
    "KHA": "Хабаровский край",
    "AMU": "Амурская область",
    "ARK": "Архангельская область",
    "AST": "Астраханская область",
    "BEL": "Белгородская область",
    "BRY": "Брянская область",
    "VLA": "Владимирская область",
    "VGG": "Волгоградская область",
    "VLG": "Вологодская область",
    "VOR": "Воронежская область",
    "IVA": "Ивановская область",
    "IRK": "Иркутская область",
    "KGD": "Калининградская область",
    "KLU": "Калужская область",
    "KEM": "Кемеровская область - Кузбасс",
    "KIR": "Кировская область",
    "KOS": "Костромская область",
    "KGN": "Курганская область",
    "KRS": "Курская область",
    "LEN": "Ленинградская область",
    "LIP": "Липецкая область",
    "MAG": "Магаданская область",
    "MOS": "Московская область",
    "MUR": "Мурманская область",
    "NIZ": "Нижегородская область",
    "NGR": "Новгородская область",
    "NVS": "Новосибирская область",
    "OMS": "Омская область",
    "ORE": "Оренбургская область",
    "ORL": "Орловская область",
    "PNZ": "Пензенская область",
    "PSK": "Псковская область",
    "ROS": "Ростовская область",
    "RYA": "Рязанская область",
    "SAM": "Самарская область",
    "SAR": "Саратовская область",
    "SAK": "Сахалинская область",
    "SVE": "Свердловская область",
    "SMO": "Смоленская область",
    "TAM": "Тамбовская область",
    "TVE": "Тверская область",
    "TOM": "Томская область",
    "TUL": "Тульская область",
    "TYU": "Тюменская область",
    "ULY": "Ульяновская область",
    "CHE": "Челябинская область",
    "YAR": "Ярославская область",
    "MOW": "город Москва",
    "SPE": "город Санкт-Петербург",
    "YEV": "Еврейская автономная область",
    "NEN": "Ненецкий автономный округ",
    "KHM": "Ханты-Мансийский автономный округ - Югра",
    "CHU": "Чукотский автономный округ",
    "YAN": "Ямало-Ненецкий автономный округ",
}
BY_NAME = {name: iso for iso, name in SUBJECTS.items()}
# The Commission's other reporting regions: the subjects it holds on Ukrainian territory, the leased city of
# Baikonur, and the precincts abroad. Each maps to its unit code and the area its precincts lie within.
OTHER = {
    "Республика Крым": ("UA-43", CRIMEA),
    "город Севастополь": ("UA-40", SEVASTOPOL),
    "Город Байконур (Республика Казахстан)": ("baikonur", KAZAKHSTAN),
    "Территория за пределами РФ": ("abroad", None),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def subject_area(iso: str) -> str:
    return key("area", SUBJECT, iso)


CIK = key("source", "cik")
SHAPES = key("source", "geoboundaries")
VOTE_SYSTEM_2020 = key("electoral_system", "vote", 2020)

# ----------------------------------------------------------------------------------------------------------------
# Raw files


def fetch() -> None:
    raw.fetch(SOURCE, NOTES, suffix=".md")
    raw.fetch(SOURCE, VOTE_2020, suffix=".zip")
    raw.fetch(SOURCE, LAYER, suffix=".geojson")
    raw.fetch(SOURCE, META, suffix=".json")


def rows(url: str) -> list[dict[str, str]]:
    """The rows of the one CSV file a zipped file holds."""
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        (name,) = archive.namelist()
        text = archive.read(name).decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


# ----------------------------------------------------------------------------------------------------------------
# Packages


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/2020-07-01"]


def parse() -> None:
    geo().write()
    vote_2020().write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=CIK,
        kind="official_body",
        publisher="Центральная избирательная комиссия Российской Федерации",
        title="Результаты выборов и голосований по участковым избирательным комиссиям (izbirkom.ru)",
        url="http://www.izbirkom.ru/region/izbirkom",
        license="Official election results of a state body; not subject to copyright (Civil Code of the Russian "
        "Federation, art. 1259(6))",
        license_url="https://www.consultant.ru/document/cons_doc_LAW_64629/",
        attribution="Центральная избирательная комиссия Российской Федерации",
        notes=f"The Commission's precinct tables as Sergey Shpilkin scraped them, read from the data folder of "
        f"{REPOSITORY} at commit {COMMIT}; izbirkom.ru does not answer scripted requests from outside Russia.",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Russia's 83 federal subjects since 2008 with their outlines, and the schemes of the Central Election "
        "Commission's territorial and precinct commissions",
    )
    p.external |= {RUSSIA, TERRITORY}
    for code, kind, rank, noun, system in (
        (SUBJECT, "administrative", 1, "federal subject", "iso_3166_2"),
        (TIK, "precinct", 3, "territorial election commission", None),
        (UIK, "precinct", 4, "precinct election commission", None),
    ):
        p.add("scheme", code=code, polity_id=RUSSIA, kind=kind, rank=rank, noun=noun, code_system=system)
    p.add(
        "source",
        id=SHAPES,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Russia ADM1, 2017 (simplified)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab); © OpenStreetMap contributors",
        notes="OpenStreetMap boundaries of 2017 as geoBoundaries releases them; the federal subjects have not "
        "changed within Russia's recognized borders since 2008.",
    )
    p.artifact(raw.cached(SOURCE, LAYER), SHAPES)
    p.artifact(raw.cached(SOURCE, META), SHAPES)
    p.add(
        "boundary_set",
        id=key("boundary_set", SUBJECT, 2008),
        scheme_code=SUBJECT,
        vintage="2008",
        valid_from=PLAN_FROM,
        valid_to=None,
        as_of=DAY_2020,
    )
    shapes = outlines()
    for iso, name in SUBJECTS.items():
        id = subject_area(iso)
        p.add("area", id=id, scheme_code=SUBJECT)
        p.add("area_code", area_id=id, code=f"RU-{iso}", valid_from=PLAN_FROM, valid_to=None)
        p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=PLAN_FROM, valid_to=None)
        p.name(id, name, "ru", "official")
        _outline(p, id, shapes[iso])
    return p


def outlines() -> dict[str, shapely.Geometry]:
    """The layer's outlines by the ISO 3166-2 suffix it tags them with."""
    features = json.loads(raw.cached(SOURCE, LAYER).path.read_text(encoding="utf-8"))["features"]
    found: dict[str, shapely.Geometry] = {}
    for feature in features:
        iso = str(feature["properties"]["shapeISO"]).removeprefix("RU-")
        found[iso] = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
    missing = set(SUBJECTS) - set(found)
    if missing:
        raise LookupError(f"the geoBoundaries layer has no outline for {sorted(missing)}")
    return found


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    gridded = shapely.set_precision(shapely.make_valid(shape), GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    outline = shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=PLAN_FROM,
        valid_to=None,
        geom=shapely.to_wkb(outline, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
        ),
        quality="osm",
        source_id=SHAPES,
    )


# ----------------------------------------------------------------------------------------------------------------
# Units


@dataclass(frozen=True)
class Region:
    code: str  # the unit code's region part: an ISO 3166-2 code, or a word for the regions outside the subjects
    area: str | None
    abroad: bool


def region(name: str) -> Region:
    if name in BY_NAME:
        return Region(f"RU-{BY_NAME[name]}", subject_area(BY_NAME[name]), False)
    if name in OTHER:
        code, area = OTHER[name]
        return Region(code, area, area is None)
    raise LookupError(f"no federal subject or region matches {name!r}")


def precinct(text: str) -> str:
    found = re.fullmatch(r"УИК №\s*(\S+)", text.strip())
    if found is None:
        raise ValueError(f"no precinct number in {text!r}")
    return found.group(1)


class Units:
    """The reporting units of one vote: subject, territorial commission, and precinct, each kept once."""

    def __init__(self, p: Package, day: date) -> None:
        self.p = p
        self.day = day
        self.seen: set[str] = set()

    def _add(self, scheme: str, code: str, parent: str | None, where: Region, equals: bool = False) -> str:
        id = key("unit", scheme, f"{self.day}:{code}")
        if id in self.seen:
            return id
        self.seen.add(id)
        if where.area is None:
            relation, kind = "unlocated", "abroad_mission"
        else:
            relation, kind = ("equals" if equals else "within"), "standard"
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=RUSSIA,
            scheme_code=scheme,
            code=f"{self.day}:{code}",
            parent_id=parent,
            area_relation=relation,
            area_id=where.area,
            kind=kind,
        )
        return id

    def subject(self, where: Region) -> str:
        # A subject's unit is its area only for the 83 subjects the archive draws; the other regions lie within a
        # larger area or have none.
        return self._add(SUBJECT, where.code, None, where, equals=where.code.startswith("RU-"))

    def tik(self, where: Region, name: str) -> str:
        return self._add(TIK, f"{where.code}:{name}", self.subject(where), where)

    def uik(self, where: Region, tik: str, number: str) -> str:
        return self._add(UIK, f"{where.code}:{tik}:{number}", self.tik(where, tik), where)


# ----------------------------------------------------------------------------------------------------------------
# 1 July 2020

COLUMNS_2020 = (
    (
        REGISTERED,
        "Число участников голосования, включенных в список участников голосования на момент окончания голосования",
    ),
    (VOTERS, "Число бюллетеней, выданных участникам голосования"),
    (CAST, "Число бюллетеней, содержащихся в ящиках для голосования"),
    (INVALID, "Число недействительных бюллетеней"),
)
# The registered participants the repository's notes give for the two pseudo-precincts of electronic voting that the
# file leaves out, and the Commission's national total they complete.
ELECTRONIC_2020 = {"город Москва": 1_051_155, "Нижегородская область": 139_571}
NATIONAL_REGISTERED_2020 = 109_190_337

METHOD_2020 = (
    "The Central Election Commission's results by precinct election commission (УИК), as scraped from izbirkom.ru "
    "and kept in the data folder of github.com/dkobak/elections. Registered are the participants on the lists at the "
    "close of voting, voters are the ballots issued, ballots cast are the ballots found in the boxes (stationary and "
    "portable), and invalid are the ballots so counted; valid ballots are not a line of the protocol and are not "
    "stored. Voting ran from 25 June to 1 July 2020, and each precinct's protocol covers all its days. The file "
    "leaves out the two pseudo-precincts that held the results of electronic voting in Moscow (1,051,155 registered) "
    "and Nizhny Novgorod Oblast (139,571 registered), so this set's registered participants, 107,999,611, are "
    "98.9% of the Commission's national 109,190,337; completeness is that share. Precincts in the Republic of "
    "Crimea and Sevastopol are placed within the Ukrainian regions that hold them, and Baikonur's within Kazakhstan."
)


def vote_2020() -> Package:
    p = Package(
        f"{SOURCE}/2020-07-01",
        SOURCE,
        "The all-Russian vote of 1 July 2020 on the amendments to the Constitution, by precinct election commission, "
        "from the Central Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {RUSSIA, TERRITORY, KAZAKHSTAN, CRIMEA, SEVASTOPOL}
    _source(p)
    p.artifact(raw.cached(SOURCE, VOTE_2020), CIK)
    p.artifact(raw.cached(SOURCE, NOTES), CIK)
    p.add(
        "electoral_system",
        id=VOTE_SYSTEM_2020,
        polity_id=RUSSIA,
        valid_from=date(2020, 3, 14),
        valid_to=None,
        rules={"formula": "referendum", "minimum": {"share": 0.5, "strict": True, "of": "ballots"}},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", VOTE_SYSTEM_2020),
        entity_id=VOTE_SYSTEM_2020,
        kind="methodology",
        body_md="Under article 3 of Law No. 1-ФКЗ of 14 March 2020, the amendments were approved if more than half "
        "of the citizens who took part in the vote voted for them. No turnout was required.",
        lang="en",
    )
    path = "ru/2020-07-01-referendum"
    election = key("election", 2020)
    p.add(
        "election",
        id=election,
        polity_id=RUSSIA,
        kind="referendum",
        date_from=FIRST_DAY_2020,
        date_to=DAY_2020,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2020 Russian constitutional vote", "en", "official")
    p.name(
        election,
        "Общероссийское голосование по вопросу одобрения изменений в Конституцию Российской Федерации",
        "ru",
        "native",
        False,
    )
    contest = key("contest", 2020)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=TERRITORY,
        electoral_system_id=VOTE_SYSTEM_2020,
        default_result_kind="counted",
    )
    p.name(contest, "Одобрение изменений в Конституцию Российской Федерации", "ru", "official")
    p.add("slug", path=f"{path}/constitution", entity_id=contest, is_current=True)
    p.add("ballot_question", contest_id=contest, type="plebiscite")
    p.add(
        "ballot_question_text",
        id=key("question", 2020),
        contest_id=contest,
        kind="text",
        lang="ru",
        body="Вы одобряете изменения в Конституцию Российской Федерации?",
        source_id=CIK,
    )
    yes = key("option", 2020, "yes")
    no = key("option", 2020, "no")
    for option, position, label in ((yes, 1, "ДА"), (no, 2, "НЕТ")):
        p.add(
            "contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=label
        )
    data = rows(VOTE_2020)
    registered = sum(int(row[COLUMNS_2020[0][1]]) for row in data)
    if registered + sum(ELECTRONIC_2020.values()) != NATIONAL_REGISTERED_2020:
        raise ValueError(f"the file's {registered:,} registered participants no longer match the national total")
    result_set = key("result_set", 2020)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CIK,
        status="active",
        is_default=True,
        completeness=round(registered / NATIONAL_REGISTERED_2020, 6),
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD_2020, lang="en")
    p.add("outcome", result_set_id=result_set, option_id=yes, kind="won")
    units = Units(p, DAY_2020)
    for row in data:
        where = region(row["region"])
        unit = units.uik(where, row["tik"], precinct(row["uik"]))
        for measure, column in COLUMNS_2020:
            _value(p, result_set, unit, None, measure, int(row[column]))
        _value(p, result_set, unit, yes, VOTES, int(row["ДА"]))
        _value(p, result_set, unit, no, VOTES, int(row["НЕТ"]))
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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
