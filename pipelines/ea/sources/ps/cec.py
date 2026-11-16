"""Palestine's national elections from the Central Elections Commission (elections.ps).

The Commission publishes the results of its three national polls as PDF tables, by electoral district, the finest
unit it prints: the first general elections of 1996 (the presidency, and the winners of each district's seats), the
presidential election of 2005, and the second legislative elections of 2006 (the national lists, with district
tables). The 2005 and 1996 tables have a text layer, which is read here; the 2006 district tables are keyed from the
printed pages and checked against the totals the Commission prints.
"""

import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "ps.cec"
SITE = "https://www.elections.ps"
PDF = f"{SITE}/Portals/30/pdf"
PALESTINE = str(uuid5(package.NAMESPACE, "polity\x1fPS"))
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK, SHARE = 1, 2, 3, 4, 5, 6, 13
SHARE_ONLY = 4

PAGES = {
    "1996": f"{SITE}/tabid/642/language/ar-PS/Default.aspx",
    "2005": f"{SITE}/tabid/640/language/ar-PS/Default.aspx",
    "2006": f"{SITE}/tabid/639/language/ar-PS/Default.aspx",
}
FILES = {
    "1996": f"{PDF}/1996_GeneralElectionsResults_AR.pdf",
    "2005-summary": f"{PDF}/PE2005-ResultsSummary_AR.pdf",
    "2005-districts": f"{PDF}/PE2005-ResultsSummaryOnDistrictLevel-VotersListAndCivilRegistry-AR.pdf",
    "2006-lists": f"{PDF}/PLC2006-ResultsVotesNOForLists-NationalLevel_AR.pdf",
    "2006-districts": f"{PDF}/PLC2006-ResultsVotesNOForLists-DistLevel_AR.pdf",
    "2006-ballots": f"{PDF}/PLC2006-ResultsBallotsStatisticsOnDistLevelForListsElections_AR.pdf",
    "2006-voters": f"{PDF}/PLC2006-ResultsVotersStatisticsOnDistLevel_AR.pdf",
}
DAYS = {"1996-01-20": date(1996, 1, 20), "2005-01-09": date(2005, 1, 9), "2006-01-25": date(2006, 1, 25)}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
COUNCIL = key("body", "legislative_council")
PRESIDENCY = key("office", "president")
SYSTEM_PRESIDENT = key("electoral_system", "president")
SYSTEM_BLOCK = key("electoral_system", "district_block")
SYSTEM_LIST = key("electoral_system", "list_2006")
FATAH = key("party", "fatah")
PARTIES = {
    "fatah": ("حركة فتح", "party"),
    "fida": ("حزب فدا", "party"),
    "dflp": ("الجبهة الديمقراطية لتحرير فلسطين", "party"),
    "ppp": ("حزب الشعب", "party"),
    "freedom": ("كتلة الحرية والاستقلال", "bloc"),
    "national": ("الائتلاف الوطني الديموقراطي", "bloc"),
}
LIST_NAMES = {
    1: "قائمة البديل (الائتلاف الجبهة الديمقراطية وحزب الشعب وفدا ومستقلين)",
    2: "قائمة فلسطين المستقلة (مصطفى البرغوثي ومستقلين)",
    3: "قائمة الشهيد أبو علي مصطفى",
    4: "قائمة الشهيد أبو العباس",
    5: "قائمة الحرية والعدالة الاجتماعية",
    6: "قائمة التغيير والإصلاح",
    7: "قائمة الائتلاف الوطني للعدالة والديمقراطية (وعد)",
    8: "قائمة الطريق الثالث",
    9: "قائمة الحرية والاستقلال",
    10: "قائمة العدالة الفلسطينية",
    11: "قائمة حركة فتح",
}
DISTRICTS_2006 = {
    1: "القدس",
    2: "جنين",
    3: "طولكرم",
    4: "طوباس",
    5: "نابلس",
    6: "قلقيلية",
    7: "سلفيت",
    8: "رام الله والبيرة",
    9: "أريحا",
    10: "بيت لحم",
    11: "الخليل",
    12: "شمال غزة",
    13: "مدينة غزة",
    14: "دير البلح",
    15: "خانيونس",
    16: "رفح",
}
# 2006 votes by district for lists 1 to 11, as printed in the district tables (which run from list 6 down to list 1
# on the first page and from list 7 up to list 11 on the second).
LISTS_2006 = {
    1: (2282, 1986, 2135, 336, 410, 16035, 116, 904, 233, 177, 13698),
    2: (2629, 2322, 3657, 331, 440, 28196, 157, 2402, 379, 255, 33796),
    3: (2110, 1253, 1919, 124, 764, 22883, 87, 2372, 328, 68, 20667),
    4: (808, 563, 709, 120, 64, 6323, 107, 741, 288, 208, 6673),
    5: (3892, 2359, 5024, 222, 711, 44000, 71, 4051, 199, 253, 37635),
    6: (756, 523, 783, 34, 338, 8349, 29, 870, 243, 58, 13765),
    7: (1387, 1201, 1019, 72, 840, 7501, 93, 1462, 296, 37, 7278),
    8: (3609, 3210, 5278, 384, 637, 33686, 84, 1701, 136, 77, 30939),
    9: (437, 513, 421, 102, 133, 3861, 14, 309, 24, 19, 5833),
    10: (3191, 2008, 4519, 114, 227, 15140, 80, 1346, 100, 72, 21255),
    11: (3190, 4996, 4103, 393, 1138, 61433, 222, 3322, 527, 145, 44668),
    12: (1266, 1261, 4973, 127, 153, 35781, 73, 755, 203, 61, 31559),
    13: (1266, 2116, 2497, 227, 378, 74816, 302, 1520, 348, 96, 48328),
    14: (656, 857, 2328, 102, 257, 26550, 206, 549, 290, 42, 28681),
    15: (909, 1174, 1888, 229, 369, 35070, 106, 1027, 559, 71, 38384),
    16: (585, 567, 848, 94, 268, 20785, 59, 531, 245, 84, 27395),
}
# Per district: registered, ballots cast, valid, invalid, blank.
BALLOTS_2006 = {
    1: (66599, 41006, 38312, 1250, 1444),
    2: (101664, 79317, 74564, 2704, 2049),
    3: (73340, 55728, 52575, 1863, 1290),
    4: (20775, 17479, 16604, 523, 352),
    5: (129379, 103362, 98417, 2876, 2069),
    6: (33701, 27250, 25748, 1000, 502),
    7: (26641, 22237, 21186, 532, 519),
    8: (106061, 83153, 79741, 1808, 1604),
    9: (16073, 12338, 11666, 443, 229),
    10: (69210, 51435, 48052, 1422, 1961),
    11: (186054, 131011, 124137, 3767, 3107),
    12: (98535, 79775, 76212, 2233, 1330),
    13: (174379, 136551, 131894, 2904, 1753),
    14: (79051, 63532, 60518, 1918, 1096),
    15: (103376, 83789, 79786, 2492, 1511),
    16: (65817, 54461, 51461, 2129, 871),
}
NATIONAL_2006 = {  # list: votes, seats, as printed in the national table
    1: (28973, 2),
    2: (26909, 2),
    3: (42101, 3),
    4: (3011, 0),
    5: (7127, 0),
    6: (440409, 29),
    7: (1806, 0),
    8: (23862, 2),
    9: (4398, 0),
    10: (1723, 0),
    11: (410554, 28),
}
NATIONAL_BALLOTS_2006 = (1350655, 1042424, 990873, 29864, 21687)

CANDIDATES_2005 = (
    ("محمود عباس - أبو مازن", "fatah"),
    ("مصطفى البرغوثي", None),
    ("تيسير خالد", "dflp"),
    ("عبد الحليم الأشقر", None),
    ("بسام الصالحي", "ppp"),
    ("السيد بركة", None),
    ("عبد الكريم شبير", None),
)
# 2005 district columns, in the printed order from the left edge: Rafah to Jerusalem.
DISTRICTS_2005 = ("رفح", "خانيونس", "دير البلح", "غزة", "شمال غزة", "الخليل", "بيت لحم", "أريحا", "رام الله",
                  "سلفيت", "قلقيلية", "نابلس", "طوباس", "طولكرم", "جنين", "القدس")  # fmt: skip
NATIONAL_2005 = (501448, 156227, 26848, 22171, 21429, 10406, 5717, 30672, 27159, 802077, 1760481)

METHOD = (
    "Counted by the Central Elections Commission and published as tables by electoral district, the finest unit it "
    "prints. The Commission does not publish results by polling station. Registered voters, ballots cast, valid, "
    "invalid, and blank ballots are as printed."
)


def clean(text: object) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub("[\u202a-\u202e\u200e\u200f]", "", text).replace("\xa0", " ").replace("االله", "الله")
    return " ".join(text.split())


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def text(name: str) -> str:
    path: Path = raw.cached(SOURCE, FILES[name]).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    body = unicodedata.normalize("NFKC", done.stdout)
    return re.sub("[\u202a-\u202e\u200e\u200f]", "", body).replace("االله", "الله")


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


# --- 1996 -----------------------------------------------------------------------------------------------------------


def read_1996() -> tuple[dict[int, int], dict[int, list[tuple[str, str, int, str]]]]:
    """Each district's seats, and its winners (party label, name, votes, reserved seat) in printed order."""
    body = text("1996")
    seats: dict[int, int] = {}
    winners: dict[int, list[tuple[str, str, int, str]]] = {}
    labels = sorted((label for label, _ in PARTIES.values()), key=len, reverse=True) + ["مستقلة", "مستقل"]
    current = 0
    for line in body.split("أما نتائج الانتخابات")[1].splitlines():
        line = line.strip()
        head = re.fullmatch(r"\.\s*(\d+)\s*(.+?):", line)
        if head:
            current = int(head[1])
            winners[current] = []
            continue
        plain = re.search(r"عدد المقاعد\s*(\d+)\s*:", line)
        if plain:
            seats[current] = int(plain[1])
            continue
        votes = re.search(r"(?<!\d)(\d{4,6})(?!\d)", line)
        if not votes or not current or "الأصوات" in line:
            continue
        rest = line.replace(votes[1], " ", 1)
        label = next((x for x in labels if x in rest), None)
        if label is None:
            raise ValueError(f"1996 line has no party: {line}")
        rest = rest.replace(label, " ", 1)
        reserved = "christian" if "المقعد المسيحي" in rest else "samaritan" if "المقعد السامري" in rest else ""
        rest = re.sub(r"[()][^()]*[()]", " ", rest).replace('"', " ")
        if len(re.findall(r"(?<!\d)\d{1,2}(?!\d)", rest)) != 1:
            raise ValueError(f"1996 line has no rank: {line}")
        winners[current].append((label, clean(re.sub(r"\d+", " ", rest)), int(votes[1]), reserved))
    return seats, winners


NAMES_1996 = {
    1: "القدس", 2: "أريحا", 3: "بيت لحم", 4: "جنين", 5: "الخليل", 6: "رام الله", 7: "سلفيت", 8: "طوباس",
    9: "طولكرم", 10: "قلقيلية", 11: "نابلس", 12: "شمال غزة (جباليا)", 13: "دير البلح", 14: "غزة",
    15: "خانيونس", 16: "رفح",
}  # fmt: skip
PARTY_KEYS = {label: name for name, (label, _) in PARTIES.items()}


# --- 2005 -----------------------------------------------------------------------------------------------------------


def read_2005() -> list[list[int]]:
    """The district table's 11 rows (seven candidates, invalid, blank, voters, registered), each the national
    figure followed by the sixteen districts from Rafah to Jerusalem."""
    body = re.sub(r"\s+", " ", re.sub(r"\n\s*(?=,\d)", "", text("2005-districts")))
    body = re.sub(r"\d+ ?\.\d+ ?%", " ", body)
    tokens = [int(x.replace(" ", "").replace(",", "")) for x in re.findall(r"\d{1,3}(?: ?,\d{3})*", body)]
    start = tokens.index(NATIONAL_2005[0])
    flat = tokens[start : start + 11 * 17]
    rows = [flat[i * 17 : (i + 1) * 17] for i in range(11)]
    for row in rows:
        if sum(row[1:]) != row[0]:
            raise ValueError(f"2005 row does not add up: {row}")
    if tuple(row[0] for row in rows) != NATIONAL_2005[:7] + (30672, 27159, 802077, 1760481):
        raise ValueError("2005 totals differ from the printed summary")
    for column in range(17):
        if sum(rows[i][column] for i in range(7)) + rows[7][column] + rows[8][column] != rows[9][column]:
            raise ValueError(f"2005 column {column} does not add up to its voters")
    return rows


# --- packages -------------------------------------------------------------------------------------------------------


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Palestinian parties, the Legislative Council, the presidency, and their electoral systems, from the Central "
        "Elections Commission's results tables",
    )
    p.external.add(PALESTINE)
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="لجنة الانتخابات المركزية - فلسطين (Central Elections Commission, Palestine)",
        title="Results of the 1996, 2005, and 2006 elections published by the Central Elections Commission",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        attribution="Central Elections Commission, Palestine",
    )
    for url in (*PAGES.values(), *FILES.values()):
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    p.add("scheme", code="ps.district", polity_id=PALESTINE, kind="electoral_district", rank=1, noun="district")
    p.add("body", id=COUNCIL, polity_id=PALESTINE, kind="unicameral")
    p.name(COUNCIL, "Palestinian Legislative Council", "en", "official")
    p.name(COUNCIL, "المجلس التشريعي الفلسطيني", "ar", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=PALESTINE, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Palestinian Authority", "en", "official")
    p.name(PRESIDENCY, "رئيس السلطة الوطنية الفلسطينية", "ar", "native", False)
    systems = (
        (
            SYSTEM_PRESIDENT,
            {"formula": "fptp"},
            "The presidency is won by the candidate with the most votes in a single national round.",
        ),
        (
            SYSTEM_BLOCK,
            {"formula": "block"},
            (
                "In 1996, each of sixteen districts elected its seats by plurality block voting, and the law "
                "reserved some seats in some districts for Christians and for Samaritans."
            ),
        ),
        (
            SYSTEM_LIST,
            {"formula": "sainte_lague", "threshold": {"percent": 2, "level": "list", "scope": "national"}},
            (
                "In 2006, half of the 132 seats were elected from national party lists by the Sainte-Laguë method, "
                "among lists with at least two percent of the valid votes, and half in sixteen districts by block "
                "voting."
            ),
        ),
    )
    for id, rules, body in systems:
        p.add("electoral_system", id=id, polity_id=PALESTINE, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    for name, (label, kind) in PARTIES.items():
        p.add("party", id=key("party", name), kind=kind, polity_id=PALESTINE)
        p.name(key("party", name), label, "ar", "official")
    for number, label in LIST_NAMES.items():
        if number == 11:
            continue
        p.add("party", id=key("party", "list2006", number), kind="list_vehicle", polity_id=PALESTINE)
        p.name(key("party", "list2006", number), label, "ar", "official")
    return p


def build(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Palestinian elections of {first} by electoral district, from the Central Elections Commission's tables",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(PALESTINE)
    {"1996-01-20": general_1996, "2005-01-09": president_2005, "2006-01-25": legislative_2006}[day](p, first)
    return p


def _uses(p: Package, *names: str) -> None:
    for name in names:
        p.uses(raw.cached(SOURCE, FILES[name]))


def _election(p: Package, name: str, day: date, english: str, arabic: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=PALESTINE, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=f"ps/{day}-general", entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, arabic, "ar", "native", False)
    return election


def _contest(p: Package, name: str, election: str, path: str, english: str, arabic: str, **fields: object) -> str:
    contest = key("contest", name)
    kind = fields.pop("kind", "office")
    row = {"round_number": 1, "default_result_kind": "counted", **fields}
    p.add("contest", id=contest, election_id=election, kind=kind, **row)
    p.name(contest, english, "en", "official")
    p.name(contest, arabic, "ar", "native", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    return contest


def _result_set(p: Package, election: str, contest: str, name: str, note: str = METHOD) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    return result_set


def _unit(p: Package, day: date, code: str, name: str, kind: str = "standard") -> str:
    unit = key("unit", day, code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=PALESTINE,
        scheme_code="ps.district",
        code=f"{day}:{code}",
        area_relation="unlocated",
        area_id=None,
        kind=kind,
    )
    p.name(unit, name, "ar", "official")
    return unit


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int, flags: int = 0) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=flags,
        value=value,
    )


def _declare(p: Package, result_set: str, option: str | None, measure: int, value: int) -> None:
    p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)


def _person(p: Package, tag: str, label: str) -> str:
    person = key("person", tag)
    p.add("person", id=person)
    p.name(person, label, "ar", "official")
    return person


def general_1996(p: Package, day: date) -> None:
    _uses(p, "1996")
    p.uses(raw.cached(SOURCE, PAGES["1996"]))
    election = _election(
        p, "1996", day, "1996 Palestinian general election", "الانتخابات الفلسطينية العامة الأولى 1996"
    )
    contest = _contest(
        p,
        "1996:president",
        election,
        f"ps/{day}-general/president",
        "1996 Palestinian presidential election",
        "الانتخابات الرئاسية 1996",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    note = (
        "The Commission's summary prints no vote counts for the presidential race. It prints the number of voters "
        "(736,825) and each candidate's share of that total, and the shares of invalid and blank ballots. The shares "
        "are stored as printed, in hundredths of a percent, and no counts are derived from them."
    )
    result_set = _result_set(p, election, contest, "1996:president", note)
    national = _unit(p, day, "national", "فلسطين", "aggregate")
    for position, (name, party, share) in enumerate((("ياسر عرفات", "fatah", 8728), ("سميحة خليل", None, 989)), 1):
        option = key("option", "1996:president", position)
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
            id=key("candidacy", "1996:president", position),
            person_id=_person(p, f"1996:president:{position}", name),
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=key("party", party) if party else None,
            is_independent=party is None,
        )
        _put(p, result_set, national, option, SHARE, share, SHARE_ONLY)
        if position == 1:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    _put(p, result_set, national, None, CAST, 736825)
    seats, winners = read_1996()
    note = (
        "The Commission prints only the winners of each district, with their votes: it does not print the votes of "
        "the candidates who lost, nor the district's valid, invalid, or blank ballots. The sixteen districts "
        "elected 88 members in all, and the law reserved seven of those seats, six for Christians and one for "
        "Samaritans."
    )
    for number, magnitude in seats.items():
        if len(winners[number]) != magnitude:
            raise ValueError(f"1996 district {number} lists {len(winners[number])} winners for {magnitude} seats")
        name = NAMES_1996[number]
        contest = _contest(
            p,
            f"1996:district:{number}",
            election,
            f"ps/{day}-general/legislative-council-{number:02d}",
            f"1996 Palestinian Legislative Council election, {number:02d}",
            f"انتخابات المجلس التشريعي 1996، دائرة {name}",
            kind="body_seats",
            body_id=COUNCIL,
            magnitude=magnitude,
            electoral_system_id=SYSTEM_BLOCK,
        )
        result_set = _result_set(p, election, contest, f"1996:district:{number}", note)
        unit = _unit(p, day, f"{number:02d}", name)
        for position, (label, person_name, votes, reserved) in enumerate(winners[number], 1):
            tag = f"1996:district:{number}"
            option = key("option", tag, position)
            party = PARTY_KEYS.get(label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=person_name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag, position),
                person_id=_person(p, f"{tag}:{position}", person_name),
                option_id=option,
                role="head",
                ballot_name=person_name,
                member_of_party_id=key("party", party) if party else None,
                is_independent=party is None,
            )
            _put(p, result_set, unit, option, VOTES, votes)
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")


def president_2005(p: Package, day: date) -> None:
    _uses(p, "2005-summary", "2005-districts")
    p.uses(raw.cached(SOURCE, PAGES["2005"]))
    rows = read_2005()
    election = _election(
        p, "2005", day, "2005 Palestinian presidential election", "الانتخابات الرئاسية الفلسطينية 2005"
    )
    contest = _contest(
        p,
        "2005:president",
        election,
        f"ps/{day}-general/president",
        "2005 Palestinian presidential election",
        "الانتخابات الرئاسية 2005",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    note = (
        f"{METHOD} The Commission prints this table twice more, for the voter list alone and for the civil registry "
        "alone. This is the table for both registers together, in which registered voters are the 1,760,481 electors "
        "on the voter list and civil registry combined, and voters are everyone who took part, so they include "
        "invalid and blank ballots. Jerusalem's figures cover the voters recorded in its district."
    )
    result_set = _result_set(p, election, contest, "2005:president", note)
    options = []
    for position, (name, party) in enumerate(CANDIDATES_2005, 1):
        option = key("option", "2005:president", position)
        options.append(option)
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
            id=key("candidacy", "2005:president", position),
            person_id=_person(p, f"2005:president:{position}", name),
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=key("party", party) if party else None,
            is_independent=party is None,
        )
        if position == 1:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    for index, name in enumerate(DISTRICTS_2005):
        number = 16 - index
        unit = _unit(p, day, f"{number:02d}", name)
        column = index + 1
        for option, row in zip(options, rows[:7], strict=True):
            _put(p, result_set, unit, option, VOTES, row[column])
        invalid, blank, voters, registered = (rows[i][column] for i in range(7, 11))
        _put(p, result_set, unit, None, VALID, voters - invalid - blank)
        _put(p, result_set, unit, None, INVALID, invalid)
        _put(p, result_set, unit, None, BLANK, blank)
        _put(p, result_set, unit, None, CAST, voters)
        _put(p, result_set, unit, None, REGISTERED, registered)
    for option, row in zip(options, rows[:7], strict=True):
        _declare(p, result_set, option, VOTES, row[0])
    for measure, row in ((INVALID, 7), (BLANK, 8), (CAST, 9), (REGISTERED, 10)):
        _declare(p, result_set, None, measure, rows[row][0])


def legislative_2006(p: Package, day: date) -> None:
    _uses(p, "2006-lists", "2006-districts", "2006-ballots", "2006-voters")
    p.uses(raw.cached(SOURCE, PAGES["2006"]))
    election = _election(
        p, "2006", day, "2006 Palestinian legislative election", "الانتخابات التشريعية الفلسطينية الثانية 2006"
    )
    contest = _contest(
        p,
        "2006:lists",
        election,
        f"ps/{day}-general/legislative-council-lists",
        "2006 Palestinian Legislative Council election, national lists",
        "انتخابات المجلس التشريعي 2006، القوائم",
        kind="list_allocation",
        body_id=COUNCIL,
        magnitude=66,
        electoral_system_id=SYSTEM_LIST,
    )
    note = (
        f"{METHOD} This is the national list vote, which elected 66 of the 132 members. The Commission prints the "
        "votes of the 66 members elected in the districts by candidate in separate tables, which this package "
        "does not hold. Registered voters are the electors on the register in each district."
    )
    result_set = _result_set(p, election, contest, "2006:lists", note)
    options: dict[int, str] = {}
    for number, label in LIST_NAMES.items():
        option = options[number] = key("option", "2006:lists", number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=FATAH if number == 11 else key("party", "list2006", number),
            ballot_position=number,
            ballot_label=label,
        )
    for number, (votes, seats) in NATIONAL_2006.items():
        _declare(p, result_set, options[number], VOTES, votes)
        p.add("outcome", result_set_id=result_set, option_id=options[number], kind="won", seats=seats)
    for measure, value in zip((REGISTERED, CAST, VALID, INVALID, BLANK), NATIONAL_BALLOTS_2006, strict=True):
        _declare(p, result_set, None, measure, value)
    for number, name in DISTRICTS_2006.items():
        unit = _unit(p, day, f"{number:02d}", name)
        for list_number, votes in zip(LIST_NAMES, LISTS_2006[number], strict=True):
            _put(p, result_set, unit, options[list_number], VOTES, votes)
        for measure, value in zip((REGISTERED, CAST, VALID, INVALID, BLANK), BALLOTS_2006[number], strict=True):
            _put(p, result_set, unit, None, measure, value)
