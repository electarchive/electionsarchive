"""Bulgaria's National Assembly elections of 1931, 1938, 1939/40, and 1945 by region, from the National Statistical
Institute's scanned statistical yearbooks.

The NSI's statistical library (statlib.nsi.bg) serves the Direction of Statistics' "Статистически годишник на
Царство България" (Annuaire statistique du Royaume de Bulgarie) for 1931-1932, 1937-1938, 1939-1940, and the combined
volume for 1942-1945 and 1947. Each has a chapter on legislative elections with tables by electoral region:

- 1931 (XXIII Ordinary National Assembly, 21 June 1931): the electors, the voters, and the votes of every list in each
  of the sixteen electoral regions (окръзи), as preliminary results (pp. 370-373 of the 1931-1932 yearbook).
- 1938 (XXIV Assembly) and 1939/40 (XXV Assembly): the electors and voters in the towns and villages of each of the
  seven regions (области), with each region's candidates and elected deputies (pp. 731 and 743 of the 1937-1938 and
  1939-1940 yearbooks). These elections had no party lists, so the yearbooks print no votes by party.
- 1945 (XXVI Assembly): the electors, voters, candidates, and elected deputies of each of the nine regions, and the
  deputies by party affiliation (pp. 434-436 of the 1942-1945 yearbook).

The scans carry an OCR text layer too rough to read numbers from, so the tables were read from the page images and
transcribed into this module. Every row is checked against the table's own totals when a package is built: the
regions against the national row, and, for 1931, the lists' votes and the invalid ballots against each region's
voters.
"""

from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "bg.nsi_kingdom"
BULGARIA = str(uuid5(package.NAMESPACE, "polity\x1fBG"))
BASE = "https://statlib.nsi.bg/asset/default"
REGISTRY = f"{SOURCE}/registry"
VOTES, REGISTERED, VALID, INVALID, VOTERS, SEATS = 1, 2, 4, 5, 14, 16
DIGITIZED = 8
"""The bit of a fact's flags for a number read from a scan."""
OKRUG = "bg.kingdom_okrag"
OBLAST = "bg.kingdom_oblast"
PART = "bg.kingdom_town_village"
DPI = 200


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NSI_SOURCE = key("source", "statisticheski-godishnik")
ASSEMBLY = key("body", "ordinary-national-assembly")
Y1931 = "SG_6_1931_1932_OCR_BW.pdf"
Y1938 = "SG_6_1937_1938_OCR_BW.pdf"
Y1939 = "SG_6_1939_1940_OCR_BW.pdf"
Y1945 = "SG_6_1942-1945_1947_OCR_BW.pdf"
FILES = [Y1931, Y1938, Y1939, Y1945]
TITLES = {
    Y1931: "Статистически годишник на Царство България 1931-1932",
    Y1938: "Статистически годишник на Царство България 1937-1938",
    Y1939: "Статистически годишник на Царство България 1939-1940",
    Y1945: "Статистически годишник на Царство България 1942-1945 и 1947",
}


def url(file: str) -> str:
    return f"{BASE}/{file}"


# ---------------------------------------------------------------- transcriptions

# Each region: Latin name, Bulgarian name as printed (modernized spelling), then the figures.
# 1931 (pdf pages 396-399 = printed 370-373): electors, voters, then each list's votes, then invalid ballots.
LISTS_1931 = [
    (
        "Entente of the Democratic Entente and the National Liberals (unified, Smilov)",
        "Демократически сговор и Националлиберална (обединена — Смилов)",
    ),
    (
        (
            "Coalition of the Agrarian Union (Vrabcha), Democrats, Agrarian Union (Stara Zagora), National Liberals "
            "(G. Petrov), and Radicals"
        ),
        (
            "Бълг. земл. нар. съюз (Врабча), Демократическа, Б. з. н. съюз (Ст. Загора), Нац.-либерална (Г. Петров) и "
            "Радикална"
        ),
    ),
    (
        "Coalition of the Agrarian Union (Tomov), Artisans' Professional, and Radical-Democratic",
        "Бълг. земл. нар. съюз (Томов), Занаятчийска професионална и Радикал-демократическа",
    ),
    (
        "Liberal and Agrarian Union (Vrabcha, 24 February 1929, Pleven city)",
        "Либерална и Бълг. земл. нар. съюз (Врабча, 24.II.1929 гр. Плевен)",
    ),
    ("Popular Unified (V. Ganev)", "Обединена (В. Ганев)"),
    ("Left of the Agrarian Union (Vrabcha 1)", "Лявата при Бълг. земледелски народен съюз (Врабча 1)"),
    ("National Economic (Dr. St. Yagoridkov)", "Народно-стопанска (Д-р Ст. Ягориднов)"),
    (
        "National Association for Political Regeneration (Iv. Shkoynov)",
        "Национална задруга за политическо възраждане (Ив. Шкойнов)",
    ),
    ("Workers' Social Democratic", "Работническа социал-демократическа"),
    (
        "Federation of Workers' Social Democratic Organizations and Groups (Social Federation)",
        "Федерация на работническите социал-демократически организации и групи (Социална федерация)",
    ),
    ("Workers'", "Работническа"),
    ("Others", "Други"),
]
REGIONS_1931: list[tuple[str, str, tuple[int, ...]]] = [
    ("Burgas", "Бургас", (133585, 115424, 31721, 55264, 687, 298, 0, 0, 0, 619, 1506, 185, 23536, 0, 1608)),
    ("Varna", "Варна", (66057, 56664, 16894, 31777, 75, 170, 0, 0, 0, 258, 681, 43, 5986, 0, 780)),
    ("Vidin", "Видин", (78024, 69517, 16236, 41068, 2176, 14, 1797, 72, 0, 837, 1236, 0, 5202, 0, 879)),
    ("Vratsa", "Враца", (111756, 100159, 22741, 53684, 4382, 0, 426, 7, 0, 206, 3727, 0, 13433, 0, 1553)),
    ("Kyustendil", "Кюстендил", (68207, 57708, 15326, 32942, 462, 20, 277, 23, 34, 91, 1054, 218, 6560, 0, 701)),
    ("Mastanli", "Мъстанли", (37605, 33923, 19945, 12227, 88, 16, 130, 0, 0, 0, 648, 0, 0, 0, 869)),
    ("Pashmakli", "Пашмакли", (17850, 14760, 4295, 9211, 0, 0, 0, 0, 0, 0, 294, 0, 485, 0, 475)),
    ("Petrich", "Петрич", (47544, 41311, 14878, 0, 0, 0, 0, 0, 0, 0, 0, 24654, 0, 0, 1779)),
    ("Plovdiv", "Пловдив", (153641, 134276, 37735, 66532, 768, 701, 0, 24, 90, 9, 2223, 307, 21390, 2511, 1986)),
    ("Pleven", "Плевен", (127542, 113221, 40960, 49985, 1541, 50, 0, 245, 42, 649, 2426, 0, 15254, 0, 2069)),
    ("Ruse", "Русе", (96013, 83687, 25985, 39863, 2903, 154, 1202, 1, 54, 2211, 1335, 87, 8481, 0, 1411)),
    ("Sofia", "София", (187329, 153174, 47051, 74653, 4238, 1352, 2234, 16, 294, 728, 2599, 713, 16797, 269, 2230)),
    (
        "Stara Zagora",
        "Стара Загора",
        (92116, 80636, 29345, 31360, 665, 548, 603, 21, 53, 180, 2174, 17, 14225, 0, 1445),
    ),
    ("Tarnovo", "Търново", (151617, 114843, 36593, 55773, 1368, 195, 0, 0, 0, 57, 3882, 73, 15441, 0, 1461)),
    ("Haskovo", "Хасково", (75910, 60497, 18256, 26424, 617, 24, 0, 2, 11, 132, 2435, 101, 11356, 92, 1047)),
    ("Shumen", "Шумен", (99051, 85707, 25725, 44790, 835, 373, 1483, 0, 0, 677, 1103, 103, 9135, 0, 1485)),
]
MISMATCH_1931 = {"Shumen": 2}
"""Regions whose printed voters differ from the sum of their printed lists and invalid ballots, by that much."""
TOTAL_1931 = (1543847, 1315507, 403686, 625553, 20805, 3915, 8152, 411, 578, 6654, 27323, 26501, 167281, 2872, 21778)

# 1938 and 1939/40: per region the candidates, elected deputies, then voters and electors in the towns and in the
# villages.
REGIONS = ["Burgas", "Vratsa", "Plovdiv", "Pleven", "Sofia", "Stara Zagora", "Shumen"]
BULGARIAN = {
    "Burgas": "Бургас",
    "Vratsa": "Враца",
    "Plovdiv": "Пловдив",
    "Pleven": "Плевен",
    "Sofia": "София",
    "Stara Zagora": "Стара Загора",
    "Shumen": "Шумен",
    "Varna": "Варна",
    "Gorna Dzhumaya": "Горна Джумая",
    "Ruse": "Русе",
}
# candidates, elected, voters (town, village), electors (town, village)
R1938 = {
    "Burgas": (81, 14, (48532, 143282), (71026, 197885)),
    "Vratsa": (140, 19, (31902, 253127), (46367, 360228)),
    "Plovdiv": (134, 22, (73971, 212498), (104771, 317462)),
    "Pleven": (192, 26, (59228, 340408), (81585, 497821)),
    "Sofia": (250, 30, (149925, 266099), (218775, 390775)),
    "Stara Zagora": (149, 20, (49430, 234518), (75056, 334889)),
    "Shumen": (198, 27, (77575, 321367), (121675, 434568)),
}
TOTAL_1938 = (1144, 158, (490563, 1771299), (719255, 2533628))
R1939 = {
    "Burgas": (57, 14, (43955, 137661), (74702, 198248)),
    "Vratsa": (69, 19, (31112, 261241), (50881, 370721)),
    "Plovdiv": (75, 19, (66562, 184872), (104734, 274424)),
    "Pleven": (102, 26, (55573, 348628), (83176, 507777)),
    "Sofia": (131, 29, (133493, 247120), (239554, 381202)),
    "Stara Zagora": (67, 18, (44969, 234725), (73317, 325389)),
    "Shumen": (110, 27, (66902, 318350), (120960, 430833)),
}
MISMATCH_1939 = 50
"""The printed village electors of the regions add up to this much less than the printed national figure."""
TOTAL_1939 = (611, 152, (442566, 1732597), (747324, 2488644))

# 1945: per region the candidates (all elected), voters, and electors; the deputies by party affiliation.
R1945 = {
    "Burgas": (24, 347806, 384587),
    "Varna": (34, 472387, 555372),
    "Vratsa": (30, 471830, 536302),
    "Gorna Dzhumaya": (10, 128371, 140283),
    "Pleven": (41, 596418, 729475),
    "Plovdiv": (33, 453881, 540262),
    "Ruse": (21, 264025, 340153),
    "Sofia": (50, 677857, 812111),
    "Stara Zagora": (33, 453892, 519787),
}
TOTAL_1945 = (276, 3866467, 4558332)
AFFILIATIONS_1945 = [
    ("Bulgarian Workers' Party (Communists)", "Българска работническа партия (комунисти)", "БРП(к)", 94),
    ("Bulgarian Agrarian National Union", "Български земеделски народен съюз", "БЗНС", 94),
    (
        "Bulgarian Workers' Social Democratic Party",
        "Българска работническа социалдемократическа партия",
        "БРСДП",
        31,
    ),
    ("Zveno National Union", "Народен съюз „Звено“", "Звено", 44),
    ("Bulgarian Radical Party", "Българска радикална партия", "Радикална партия", 11),
]
INDEPENDENTS_1945 = 2


# ---------------------------------------------------------------- locators
# Boxes of the printed numbers, in pixels of the page rendered at the stated resolution, as (x0, x1) columns and the
# y of each row's center. They are converted to fractions of the page when written.


@dataclass(frozen=True)
class Layout:
    dpi: int
    left: tuple[tuple[int, int], ...]
    rows: tuple[int, ...]
    half: int = 8
    """Half the height of a row's box."""


L_1931_LEFT = Layout(
    110,
    ((258, 345), (350, 436), (440, 514), (517, 628), (632, 720), (722, 812), (815, 890)),
    (560, 645, 740, 855, 933, 1014, 1068, 1153, 1297),
)
L_1931_RIGHT = Layout(
    110,
    ((70, 135), (137, 205), (208, 285), (290, 365), (372, 445), (450, 530), (535, 600), (605, 680)),
    (559, 641, 738, 851, 932, 1013, 1066, 1148, 1290),
)
L_1931_LEFT_2 = Layout(L_1931_LEFT.dpi, L_1931_LEFT.left, (567, 665, 806, 903, 1030, 1113, 1239))
L_1931_RIGHT_2 = Layout(
    110,
    tuple((a + 4, b + 4) for a, b in L_1931_RIGHT.left),
    (542, 637, 779, 876, 1003, 1086, 1213),
)
PAGES_1931 = [(396, 397), (398, 399)]

# 1938 (pdf page 753, 200 dpi rows for the town, village, and total lines of each region)
ROWS_1938 = [
    (683, 703, 722),
    (750, 770, 791),
    (818, 839, 859),
    (887, 907, 927),
    (955, 975, 995),
    (1022, 1043, 1063),
    (1090, 1110, 1131),
]
ROWS_1939 = [
    (733, 753, 773),
    (801, 821, 841),
    (869, 889, 909),
    (937, 957, 977),
    (1005, 1025, 1045),
    (1073, 1093, 1113),
    (1141, 1161, 1181),
]
COLS_1938 = {"candidates": (470, 550), "elected": (565, 645), "voters": (650, 750), "electors": (755, 868)}
COLS_1939 = {"candidates": (440, 530), "elected": (560, 650), "voters": (660, 780), "electors": (790, 905)}
ROWS_1945 = {
    452: {
        "Burgas": 1993,
        "Varna": 2075,
        "Vratsa": 2156,
        "Gorna Dzhumaya": 2238,
        "Pleven": 2318,
        "Plovdiv": 2399,
        "Ruse": 2479,
    },
    453: {"Sofia": 662, "Stara Zagora": 743, "total": 851},
}
COLS_1945 = {
    452: {"candidates": (700, 765), "elected": (808, 872), "voters": (900, 1032), "electors": (1065, 1200)},
    453: {"candidates": (665, 735), "elected": (775, 850), "voters": (870, 1022), "electors": (1050, 1192)},
}
ROW_1945_AFFILIATION_PAGE = 454
"""The page printing the deputies by party affiliation (printed page 436)."""
AFFILIATION_ROW = (1170, 1170)


def _write_registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The National Statistical Institute's statistical yearbooks of the Kingdom of Bulgaria, with the regional "
        "schemes and the 1945 party affiliations of the deputies",
    )
    p.external.add(BULGARIA)
    p.add(
        "source",
        id=NSI_SOURCE,
        kind="official_body",
        publisher="Дирекция на статистиката (Национален статистически институт)",
        title="Статистически годишник на Царство България",
        url=url(Y1931),
        attribution="Национален статистически институт, статистическа библиотека",
    )
    for file in FILES:
        p.artifact(raw.cached(SOURCE, url(file)), NSI_SOURCE)
    p.add("body", id=ASSEMBLY, polity_id=BULGARIA, kind="unicameral")
    p.name(ASSEMBLY, "Ordinary National Assembly of Bulgaria (1879-1946)", "en", "official")
    p.name(ASSEMBLY, "Обикновено народно събрание", "bg", "native", False)
    p.add("scheme", code=OKRUG, polity_id=BULGARIA, kind="electoral_district", rank=2, noun="electoral region")
    p.add("scheme", code=OBLAST, polity_id=BULGARIA, kind="administrative", rank=2, noun="region")
    p.add("scheme", code=PART, polity_id=BULGARIA, kind="synthetic", rank=3, noun="part of a region")
    for latin, native, _short, _seats in AFFILIATIONS_1945:
        party = key("party", latin)
        p.add("party", id=party, kind="party", polity_id=BULGARIA)
        p.name(party, native, "bg", "official")
        p.name(party, latin, "en", "official", False)
    return p


def party_of(latin: str) -> str:
    return key("party", latin)


# ---------------------------------------------------------------- elections


@dataclass(frozen=True)
class Edition:
    key: str
    first: date
    last: date
    printed: str
    assembly: str
    file: str
    seats: int
    pages: tuple[int, ...]


EDITIONS = [
    Edition("1931", date(1931, 6, 21), date(1931, 6, 21), "21. VI. 1931", "XXIII", Y1931, 274, (396, 397, 398, 399)),
    Edition("1938", date(1938, 3, 6), date(1938, 3, 27), "6.III.—27.III. 1938", "XXIV", Y1938, 158, (753,)),
    Edition("1939-40", date(1939, 12, 24), date(1940, 1, 28), "24.XII.1939—28.I.1940", "XXV", Y1939, 152, (763,)),
    Edition("1945", date(1945, 11, 18), date(1945, 11, 18), "18. XI. 1945", "XXVI", Y1945, 276, (452, 453, 454)),
]
BY_KEY = {e.key: e for e in EDITIONS}


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    for file in FILES:
        raw.fetch(SOURCE, url(file), suffix=".pdf")


def parse() -> None:
    _write_registry().write()
    for e in EDITIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def check_1931() -> None:
    """Every region's lists and invalid ballots add up to its voters, and the regions to the printed national row."""
    for latin, _native, row in REGIONS_1931:
        votes, invalid = sum(row[2:14]), row[14]
        if votes + invalid != row[1] + MISMATCH_1931.get(latin, 0):
            raise ValueError(f"1931 {latin}: lists {votes} + invalid {invalid} differ from voters {row[1]}")
    for i, printed in enumerate(TOTAL_1931):
        if sum(r[2][i] for r in REGIONS_1931) != printed:
            raise ValueError(
                f"1931 national column {i}: regions add to {sum(r[2][i] for r in REGIONS_1931)}, not {printed}"
            )


def check_regions(label: str, rows: dict[str, tuple[int, int, tuple[int, int], tuple[int, int]]], total: tuple) -> None:
    candidates, elected, voters, electors = total
    if sum(r[0] for r in rows.values()) != candidates or sum(r[1] for r in rows.values()) != elected:
        raise ValueError(f"{label}: candidates or deputies differ from the printed total")
    for part in (0, 1):
        short = MISMATCH_1939 if label == "1939/40" and part == 1 else 0
        if (
            sum(r[2][part] for r in rows.values()) != voters[part]
            or sum(r[3][part] for r in rows.values()) + short != electors[part]
        ):
            raise ValueError(f"{label}: voters or electors of part {part} differ from the printed total")


def check_1945() -> None:
    candidates, voters, electors = TOTAL_1945
    if (
        sum(r[0] for r in R1945.values()) != candidates
        or sum(r[1] for r in R1945.values()) != voters
        or sum(r[2] for r in R1945.values()) != electors
    ):
        raise ValueError("1945: regions differ from the printed national row")
    if sum(a[3] for a in AFFILIATIONS_1945) + INDEPENDENTS_1945 != candidates:
        raise ValueError("1945: deputies by party differ from the total")


def artifact_id(storage_key: str) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{NSI_SOURCE}\x1f{storage_key}"))


def election(e: Edition) -> Package:
    if e.key == "1931":
        check_1931()
    elif e.key == "1938":
        check_regions("1938", R1938, TOTAL_1938)
    elif e.key == "1939-40":
        check_regions("1939/40", R1939, TOTAL_1939)
    else:
        check_1945()
    p = Package(
        f"{SOURCE}/{e.key}",
        SOURCE,
        f"The Bulgarian National Assembly election of {e.first} by region, from the Direction of Statistics' yearbook",
        depends=[REGISTRY],
    )
    p.external.add(BULGARIA)
    stored = raw.cached(SOURCE, url(e.file))
    p.uses(stored)
    election_id = key("election", e.key)
    p.add(
        "election",
        id=election_id,
        polity_id=BULGARIA,
        kind="general",
        date_from=e.first,
        date_to=e.last,
        date_precision="day",
        original_text=e.printed,
        status="held",
    )
    p.name(election_id, f"{e.first.year} Bulgarian National Assembly election", "en", "official")
    p.name(election_id, f"Избори за {e.assembly} Обикновено народно събрание", "bg", "native", False)
    path = f"bg/{e.first}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", e.key)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=ASSEMBLY,
        magnitude=e.seats,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{e.first.year} National Assembly", "en", "official")
    p.name(contest, f"{e.assembly} Обикновено народно събрание", "bg", "native", False)
    p.add("slug", path=f"{path}/national-assembly", entity_id=contest, is_current=True)
    result_set = key("result_set", e.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=NSI_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=NSI_SOURCE,
        locator={"url": url(e.file), "pdf_pages": list(e.pages)},
    )
    sizes = {n: (page_px(stored.path, n)) for n in e.pages}  # pixel size of each page at DPI
    page_ids: dict[int, str] = {}
    for n in e.pages:
        page_ids[n] = key("page", e.file, n)
        p.add(
            "source_page",
            id=page_ids[n],
            artifact_id=artifact_id(stored.storage_key),
            page_number=n,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{n}.png",
            width=sizes[n][0],
            height=sizes[n][1],
        )

    def fact(
        unit: str,
        option: str | None,
        measure: int,
        value: int,
        page: int,
        dpi: int,
        xs: tuple[int, int],
        ys: tuple[int, int],
    ) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=DIGITIZED,
            value=value,
        )
        width, height = (sizes[page][0] * dpi / DPI, sizes[page][1] * dpi / DPI)
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=page_ids[page],
            source_id=None,
            x0=round(xs[0] / width, 4),
            y0=round(ys[0] / height, 4),
            x1=round(xs[1] / width, 4),
            y1=round(ys[1] / height, 4),
        )

    def region_unit(
        scheme: str, code: str, latin: str, native: str, parent: str | None = None, label: str | None = None
    ) -> str:
        unit = key("unit", e.key, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BULGARIA,
            scheme_code=scheme,
            code=f"{e.first}:{code}",
            parent_id=parent,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, label or native, "bg", "official")
        p.name(unit, label or latin, "en", "official", False)
        return unit

    notes: list[str] = []
    declared: list[tuple[str | None, int, int]] = []
    if e.key == "1931":
        _write_1931(p, e, contest, result_set, region_unit, fact, declared, notes)
    elif e.key in ("1938", "1939-40"):
        _write_split(p, e, region_unit, fact, declared, notes)
    else:
        _write_1945(p, e, contest, result_set, region_unit, fact, declared, notes)
    for option, measure, value in declared:
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    for index, text in enumerate(notes):
        p.add("note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", lang="en", body_md=text)
    p.add(
        "note",
        id=key("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        lang="en",
        body_md=(
            f"Transcribed from the table of legislative elections by region in the Direction of Statistics' "
            f"yearbook (pages {', '.join(str(n) for n in e.pages)} of the scanned file {e.file}). The units are the "
            "regions the yearbook prints; no boundaries are attached to them. Each figure was checked against the "
            "table's own totals when the package was built."
        ),
    )
    return p


def page_px(pdf, number: int) -> tuple[int, int]:
    import re
    import subprocess

    out = subprocess.run(
        ["pdfinfo", "-f", str(number), "-l", str(number), str(pdf)], capture_output=True, text=True, check=True
    ).stdout
    m = re.search(rf"Page\s+{number} size:\s+([\d.]+) x ([\d.]+)", out)
    if m is None:
        raise ValueError(f"no size for page {number}")
    return round(float(m.group(1)) * DPI / 72), round(float(m.group(2)) * DPI / 72)


def _write_1931(p, e, contest, result_set, region_unit, fact, declared, notes) -> None:
    options = []
    for position, (latin, native) in enumerate(LISTS_1931, start=1):
        option = key("option", e.key, position)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="others" if position == len(LISTS_1931) else "list",
            ballot_position=position,
            ballot_label=native,
        )
        p.name(option, native, "bg", "official")
        p.name(option, latin, "en", "official", False)
    for index, (latin, native, row) in enumerate(REGIONS_1931):
        page_left, page_right = PAGES_1931[0] if index < 9 else PAGES_1931[1]
        left, right = (L_1931_LEFT, L_1931_RIGHT) if index < 9 else (L_1931_LEFT_2, L_1931_RIGHT_2)
        slot = index if index < 9 else index - 9
        unit = region_unit(OKRUG, f"{index + 1:02d}", f"{latin} region", native, label=None)
        y = left.rows[slot]
        yr = right.rows[slot]

        def at(page: int, layout: Layout, column: int, yy: int) -> tuple[tuple[int, int], tuple[int, int]]:
            return layout.left[column], (yy - layout.half, yy + layout.half)

        values = row
        xs, ys = at(page_left, left, 0, y)
        fact(unit, None, REGISTERED, values[0], page_left, 110, xs, ys)
        xs, ys = at(page_left, left, 1, y)
        fact(unit, None, VOTERS, values[1], page_left, 110, xs, ys)
        valid = 0
        for i in range(12):
            value = values[2 + i]
            valid += value
            if i < 5:
                xs, ys = at(page_left, left, 2 + i, y)
                page = page_left
            else:
                xs, ys = at(page_right, right, i - 5, yr)
                page = page_right
            if value:
                fact(unit, options[i], VOTES, value, page, 110, xs, ys)
        xs, ys = at(page_right, right, 7, yr)
        fact(unit, None, INVALID, values[14], page_right, 110, xs, ys)
    declared.append((None, REGISTERED, TOTAL_1931[0]))
    declared.append((None, VOTERS, TOTAL_1931[1]))
    for i, option in enumerate(options):
        declared.append((option, VOTES, TOTAL_1931[2 + i]))
    declared.append((None, INVALID, TOTAL_1931[14]))
    notes.append(
        "The yearbook prints these as preliminary results. The votes are those cast for each list of candidates, "
        "by electoral region (окръг); the yearbook also gives each of the 71 districts (околии), which are not "
        "transcribed here. A vote for a list that ran in only some regions counts only there. The lists are the "
        "yearbook's columns: coalitions and parties as it prints them, with every minor list in Others, so lists "
        "that appear under different names in the sources are not merged."
    )
    notes.append(
        "In fifteen of the sixteen regions the votes of the lists and the invalid ballots and empty envelopes add up "
        "to the region's voters. In Shumen region they add up to 85,709, two more than the 85,707 voters the "
        "yearbook prints (with a footnote), and the printed national voters (1,315,507) are likewise two less "
        "than the lists' national votes and the invalid ballots (1,315,509). Every figure is stored as printed."
    )
    notes.append(
        "The National Assembly had not verified the elections in several districts when the yearbook was printed "
        "(Oryahovo and Ferdinand in Vratsa region; Egri dere, Kochou Kavak, and Kardzhali in Mastanli region; "
        "Pazardzhik and Peshtera in Plovdiv region; Nikopol in Pleven region; Orhanie and Tran in Sofia region; "
        "Kazanlak, Nova Zagora, Stara Zagora, and Chirpan in Stara Zagora region; Gorna Oryahovitsa in Tarnovo "
        "region), and in some communes of Oryahovo, Ferdinand, Kochou Kavak, and Peshtera the elections were "
        "annulled and held again. The figures are the preliminary ones."
    )
    notes.append(
        "The 14,878 votes counted in Petrich region's first list column and the 24,654 in its Federation column "
        "are marked in the yearbook as the Independent Macedonian Group's. They are stored in the columns where "
        "they are printed."
    )
    notes.append(
        "The yearbook prints 274 deputies for this Assembly but no deputies by region or by list, so no seats are stored."
    )


def _write_split(p, e, region_unit, fact, declared, notes) -> None:
    table = R1938 if e.key == "1938" else R1939
    total = TOTAL_1938 if e.key == "1938" else TOTAL_1939
    rows = ROWS_1938 if e.key == "1938" else ROWS_1939
    cols = COLS_1938 if e.key == "1938" else COLS_1939
    candidates = []
    for index, name in enumerate(REGIONS):
        n_candidates, elected, voters, electors = table[name]
        native = BULGARIAN[name]
        region = region_unit(OBLAST, f"{index + 1:02d}", f"{name} region", f"{native} област")
        town = region_unit(PART, f"{index + 1:02d}-t", f"{name} region, towns", f"{native} област, градове", region)
        village = region_unit(PART, f"{index + 1:02d}-v", f"{name} region, villages", f"{native} област, села", region)
        town_y, village_y, total_y = rows[index]
        for unit, part, y in ((town, 0, town_y), (village, 1, village_y)):
            fact(
                unit,
                None,
                REGISTERED,
                electors[part],
                753 if e.key == "1938" else 763,
                DPI,
                cols["electors"],
                (y - 9, y + 9),
            )
            fact(unit, None, VOTERS, voters[part], 753 if e.key == "1938" else 763, DPI, cols["voters"], (y - 9, y + 9))
        fact(
            region,
            None,
            SEATS,
            elected,
            753 if e.key == "1938" else 763,
            DPI,
            cols["elected"],
            (total_y - 9, total_y + 9),
        )
        candidates.append(f"{name} {n_candidates}")
    declared.append((None, REGISTERED, sum(total[3])))
    declared.append((None, VOTERS, sum(total[2])))
    declared.append((None, SEATS, total[1]))
    notes.append(
        "Voters and electors are the yearbook's figures for the towns and the villages of each region. The "
        f"candidates per region were: {', '.join(candidates)}; in all {total[0]}. The election had no party lists, "
        "so the yearbook prints no votes by candidate or party, and none are stored."
    )
    if e.key == "1938":
        notes.append(
            "The yearbook prints the election's days as 6 to 27 March 1938, and the votes are those of the whole period."
        )
    else:
        p.accept(
            "accounting.declared",
            "The yearbook's own figures disagree: the village electors of the seven regions add up to 2,488,594, "
            "50 fewer than the printed national figure of 2,488,644, which agrees with the printed total of Shumen "
            "region's electors (551,843) but not with its printed town and village figures (120,960 and 430,833, "
            "which add up to 551,793).",
        )
        notes.append(
            "The yearbook's printed figures for Shumen region's electors disagree: its town and village electors "
            "(120,960 and 430,833) add up to 551,793, but the region's total and the national village total "
            "assume 551,843. The figures are stored as printed.",
        )
        notes.append(
            "The yearbook prints the election's days as 24 December 1939 to 28 January 1940, and the votes are "
            "those of the whole period."
        )


def _write_1945(p, e, contest, result_set, region_unit, fact, declared, notes) -> None:
    pages = ROWS_1945
    for index, name in enumerate(R1945):
        n_candidates, voters, electors = R1945[name]
        native = BULGARIAN[name]
        region = region_unit(OBLAST, f"{index + 1:02d}", f"{name} region", f"{native} област")
        page = 452 if name in pages[452] else 453
        y = pages[page][name]
        cols = COLS_1945[page]
        fact(region, None, REGISTERED, electors, page, DPI, cols["electors"], (y - 9, y + 9))
        fact(region, None, VOTERS, voters, page, DPI, cols["voters"], (y - 9, y + 9))
        fact(region, None, SEATS, n_candidates, page, DPI, cols["elected"], (y - 9, y + 9))
    declared.append((None, REGISTERED, TOTAL_1945[2]))
    declared.append((None, VOTERS, TOTAL_1945[1]))
    declared.append((None, SEATS, TOTAL_1945[0]))
    for position, (latin, native, short, seats) in enumerate(AFFILIATIONS_1945, start=1):
        option = key("option", e.key, short)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party_of(latin),
            ballot_position=position,
            ballot_label=native,
        )
        p.name(option, native, "bg", "official")
        p.name(option, latin, "en", "official", False)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=seats)
    option = key("option", e.key, "independents")
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="independents",
        ballot_position=len(AFFILIATIONS_1945) + 1,
        ballot_label="Независими",
    )
    p.name(option, "Независими", "bg", "official")
    p.name(option, "Independents", "en", "official", False)
    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=INDEPENDENTS_1945)
    notes.append(
        "Every candidate was elected (276 candidates for 276 seats). The yearbook prints no votes by party or "
        "candidate: the options are the deputies' party affiliations, with the seats of each, and the regions' "
        "voters, electors, and deputies. The deputies by party are national; the yearbook does not split them by "
        "region."
    )
    notes.append(
        "The yearbook also splits each region's voters and electors by sex; Sofia region's split leaves out "
        "249,743 voters and 313,968 electors of the city of Sofia that it did not distribute by sex, so only the "
        "regions' totals are stored."
    )
