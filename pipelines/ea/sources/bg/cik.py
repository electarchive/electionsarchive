"""Bulgaria's Central Election Commission (CIK): every election it publishes, by polling section.

The CIK publishes, for each election since 2013, a bundle of semicolon-separated text files (`sections`, `protocols`,
`votes`, `preferences`, party and candidate lists) that it calls open data. Older elections (the 2005 National
Assembly, the 2007 European Parliament, and the 2011 presidential rounds) come as differently shaped text files, and
the National Assembly and presidential elections of 1991 to 2001 come from the CIK's archive of block-structured text
files. This module reads each bundle from the raw cache and builds one package per election.
The `geo` package holds the provinces, municipalities, and settlements of the National Statistical Institute's
register (EKATTE), and each polling section is placed in its settlement, so results roll up to the municipality.
"""

import functools
import html
import json
import re
import zipfile
from collections import defaultdict
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bg import cik_local

SOURCE = "bg.cik"
BULGARIA = str(uuid5(package.NAMESPACE, "polity\x1fBG"))
BASE = "https://results.cik.bg"

# Measure codes of the archive's measure table.
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK, VOTERS = 1, 2, 3, 4, 5, 6, 14
MEASURES = {"reg": REGISTERED, "voters": VOTERS, "box": BALLOTS, "valid": VALID, "inv": INVALID}

MIR = {
    1: ("Blagoevgrad", "Благоевград"),
    2: ("Burgas", "Бургас"),
    3: ("Varna", "Варна"),
    4: ("Veliko Tarnovo", "Велико Търново"),
    5: ("Vidin", "Видин"),
    6: ("Vratsa", "Враца"),
    7: ("Gabrovo", "Габрово"),
    8: ("Dobrich", "Добрич"),
    9: ("Kardzhali", "Кърджали"),
    10: ("Kyustendil", "Кюстендил"),
    11: ("Lovech", "Ловеч"),
    12: ("Montana", "Монтана"),
    13: ("Pazardzhik", "Пазарджик"),
    14: ("Pernik", "Перник"),
    15: ("Pleven", "Плевен"),
    16: ("Plovdiv city", "Пловдив град"),
    17: ("Plovdiv province", "Пловдив област"),
    18: ("Razgrad", "Разград"),
    19: ("Ruse", "Русе"),
    20: ("Silistra", "Силистра"),
    21: ("Sliven", "Сливен"),
    22: ("Smolyan", "Смолян"),
    23: ("Sofia 23", "София 23 МИР"),
    24: ("Sofia 24", "София 24 МИР"),
    25: ("Sofia 25", "София 25 МИР"),
    26: ("Sofia province", "София област"),
    27: ("Stara Zagora", "Стара Загора"),
    28: ("Targovishte", "Търговище"),
    29: ("Haskovo", "Хасково"),
    30: ("Shumen", "Шумен"),
    31: ("Yambol", "Ямбол"),
}
ABROAD = ("Abroad", "Извън страната")
METHOD = (
    "Counted by the polling section commissions and published by the Central Election Commission (CIK) as section "
    "protocols and party votes in its open data. Registered voters are the voters on the roll, voters are the "
    "signatures on the roll, ballots cast are the ballots found in the box, and valid ballots are those marked for "
    "one list or for the no-one-supported option. Votes cast by machine are added to the paper votes of the same "
    "section. Preference votes for candidates inside a list are in the same files and are not stored here yet."
)


REFERENDUM_METHOD = (
    "Counted by the polling section commissions and published by the Central Election Commission (CIK) as section "
    "protocols. Registered voters are the voters on the roll, voters are the signatures on the roll, ballots cast "
    "are the ballots or envelopes found in the box, and valid ballots are the sum of the yes and no ballots."
)


class Election(NamedTuple):
    id: str
    kind: str  # ns, ep, pres, ref
    day: date
    url: str
    layout: str
    folder: int | None = None
    round: int = 1
    note: str = ""


Z = BASE + "/"
FIRST_ROUND = {
    "pr-2016-11-13": "pr-2016-11-06",
    "pr-2011-10-30": "pr-2011-10-23",
    "pr-2006-10-29": "pr-2006-10-22",
    "pr-2021-11-21": "pr-2021-11-14",
    "pr-2026-11-01": "pr-2026-10-25",
}
SECOND_ROUND = {first: second for second, first in FIRST_ROUND.items()}
# Packages that share a bundle with an earlier package they depend on, which holds the artifact row.
SHARED_BUNDLE = {"pr-2016-11-13": "pr-2016-11-06", "ns-2021-11-14": "pr-2021-11-14"}
# Rounds whose first-round package is already loaded and is not rebuilt here.
KEEP_FIRST = {"pr-2026-10-25"}
ELECTIONS = [
    Election("ns-2005-06-25", "ns", date(2005, 6, 25), "http://pi2005.cik.bg/results/export.zip", "ns2005"),
    Election("pr-2011-10-23", "pres", date(2011, 10, 23), Z + "mipvr2011/el2011_t1.zip", "pres2011", None, 1),
    Election("pr-2011-10-30", "pres", date(2011, 10, 30), Z + "mipvr2011/el2011_t2.zip", "pres2011", None, 2),
    Election(
        "pr-2006-10-22",
        "pres",
        date(2006, 10, 22),
        "https://pvr2006.cik.bg/results_1/export_t1.zip",
        "pres2006",
        None,
        1,
    ),
    Election(
        "pr-2006-10-29",
        "pres",
        date(2006, 10, 29),
        "https://pvr2006.cik.bg/results_2/export_t2.zip",
        "pres2006",
        None,
        2,
    ),
    Election("ns-2009-07-05", "ns", date(2009, 7, 5), "https://pi2009.cik.bg/results/exports.zip", "ns2009"),
    Election("ep-2009-06-07", "ep", date(2009, 6, 7), "https://ep2009.cik.bg/exports.zip", "ep2009"),
    Election("ns-2013-05-12", "ns", date(2013, 5, 12), Z + "pi2013/export.zip", "ns2013"),
    Election("ref-2013-01-27", "ref", date(2013, 1, 27), Z + "referendum/export.zip", "ref2013"),
    Election("ep-2014-05-25", "ep", date(2014, 5, 25), Z + "ep2014/export.zip", "ep2014"),
    Election("ns-2014-10-05", "ns", date(2014, 10, 5), Z + "pi2014/csv.zip", "ns2014"),
    Election("ref-2015-10-25", "ref", date(2015, 10, 25), Z + "minr2015/tur1/nr2015.zip", "ref2015"),
    Election("ref-2016-11-06", "ref", date(2016, 11, 6), Z + "pvrnr2016/tur1/export_nr.zip", "ref2016"),
    Election("pr-2016-11-06", "pres", date(2016, 11, 6), Z + "pvrnr2016/tur1/export.zip", "pres2016", 0, 1),
    Election("pr-2016-11-13", "pres", date(2016, 11, 13), Z + "pvrnr2016/tur1/export.zip", "pres2016", 1, 2),
    Election("ns-2017-03-26", "ns", date(2017, 3, 26), Z + "pi2017/export.zip", "ns2017"),
    Election("ep-2019-05-26", "ep", date(2019, 5, 26), Z + "ep2019/csv.zip", "ep2019"),
    Election("ns-2021-07-11", "ns", date(2021, 7, 11), Z + "pi2021_07/export.zip", "ns2021a"),
    Election("pr-2021-11-14", "pres", date(2021, 11, 14), Z + "pvrns2021/tur1/export.zip", "pres2021", 1, 1),
    Election("ns-2021-11-14", "ns", date(2021, 11, 14), Z + "pvrns2021/tur1/export.zip", "ns2022", 0),
    Election("pr-2021-11-21", "pres", date(2021, 11, 21), Z + "pvrns2021/tur2/export.zip", "pres2021", None, 2),
    Election("ns-2022-10-02", "ns", date(2022, 10, 2), Z + "ns2022/export.zip", "ns2022"),
    Election("ns-2023-04-02", "ns", date(2023, 4, 2), Z + "ns2023/export.zip", "ns2023", 0),
    Election("ep-2024-06-09", "ep", date(2024, 6, 9), Z + "europe2024/opendata/export.zip", "ns2024a", 0),
    Election("ns-2024-06-09", "ns", date(2024, 6, 9), Z + "europe2024/opendata/export.zip", "ns2024a", 1),
    Election("ns-2024-10-27", "ns", date(2024, 10, 27), Z + "pe202410/opendata/export.zip", "ns2024"),
    Election("ns-2026-04-19", "ns", date(2026, 4, 19), Z + "pe202604/opendata/export.zip", "ns2024"),
    Election("pr-2026-10-25", "pres", date(2026, 10, 25), Z + "pvr202610/tur1/opendata/export.zip", "pres2026"),
    Election(
        "pr-2026-11-01", "pres", date(2026, 11, 1), Z + "pvr202610/tur2/opendata/export.zip", "pres2026b", None, 2
    ),
]
# Pages the 2009 build reads besides its bulk export: the national table that names the lists.
LISTS_2009 = "https://pi2009.cik.bg/results/proportional/index.html"
# Files the CIK publishes that this module does not read yet: the 2009 candidate registers and the 2006 results of
# each of the 32 constituencies, which the bulk exports and national pages do not carry.
BLOCKED = [
    "https://pi2009.cik.bg/listi/index.html",
    # The results pages that carry the CIK's decisions on the elected members; the bulk exports hold no seats from
    # 2014 on, and these pages answered with a 403 to automated requests.
    *(
        f"{Z}{name}/rezultati/index.html"
        for name in (
            "ep2014",
            "pi2014",
            "pi2017",
            "pi2021_07",
            "ns2022",
            "ns2023",
            "europe2024",
            "pe202410",
            "pe202604",
        )
    ),
    *(f"https://pvr2006.cik.bg/results_{round}/rik_{n}.html" for round in (1, 2) for n in range(1, 33)),
]


class Layout(NamedTuple):
    form: int | None  # zero-based column holding the protocol form
    section: int  # zero-based column holding the section code
    forms: dict[str, dict[str, tuple[int, ...]]]  # form -> measure -> one-based columns, summed (first for roll counts)
    votes: str
    width: int = 2  # fields per party in the votes file after the section columns
    none: bool = False  # whether the protocol holds the "no one supported" votes in a `none` entry
    flags: int = 2  # flag columns at the end of the sections file (mobile, ship, machine)


def _same(spec: dict[str, tuple[int, ...]], *forms: str) -> dict[str, dict[str, tuple[int, ...]]]:
    return {form: spec for form in forms}


LAYOUTS: dict[str, Layout] = {
    "ns2013": Layout(
        0,
        1,
        {
            "": {"reg": (5,), "voters": (8,), "box": (25,), "inv": (33,), "valid": (34,)},
            "Д": {"reg": (3,), "voters": (5,), "box": (7,), "inv": (15,), "valid": (16,)},
            "*": {"reg": (5,), "voters": (8,), "box": (25,), "inv": (33,), "valid": (34,)},
        },
        "blank_pairs",
    ),
    "ns2014": Layout(
        None,
        0,
        {"*": {"reg": (4,), "voters": (6,), "box": (16,), "inv": (17,), "valid": (18,)}},
        "order_pairs",
        flags=3,
    ),
    "ep2014": Layout(
        None,
        0,
        {"*": {"reg": (4,), "voters": (8,), "box": (17,), "inv": (18,), "valid": (19,)}},
        "order_pairs",
        flags=3,
    ),
    "ns2017": Layout(
        0,
        1,
        _same({"reg": (6,), "voters": (8,), "box": (15,), "inv": (16,), "valid": (17,), "none": (19,)}, "1", "7", "*"),
        "triples",
        3,
        True,
    ),
    "ep2019": Layout(
        0,
        1,
        _same(
            {"reg": (6,), "voters": (8,), "box": (17,), "inv": (20,), "valid": (21,), "none": (25,)}, "1", "7", "8", "*"
        ),
        "triples",
        3,
        True,
        3,
    ),
    "ep2009": Layout(
        None,
        0,
        {"*": {"reg": (3,), "voters": (14,), "box": (15,), "inv": (19,), "valid": (23,)}},
        "ep2009",
        0,
        False,
        0,
    ),
    "ns2005": Layout(None, 0, {"*": {"box": (10,), "inv": (14,), "valid": (15,)}}, "ns2005", 0),
    # The 2009 protocol holds one ballot count for the majoritarian race (points 11 to 18) and one for the lists
    # (points 20 to 27). Columns are one-based and count the section code as column 1, so point 1 is column 2.
    "ns2009": Layout(
        None,
        0,
        {"*": {"reg": (2,), "voters": (10,), "box": (20,), "inv": (26,), "valid": (27,)}},
        "ns2009",
        0,
        False,
        0,
    ),
    "ns2009m": Layout(
        None,
        0,
        {"*": {"reg": (2,), "voters": (10,), "box": (12,), "inv": (18,), "valid": (19,)}},
        "ns2009",
        0,
        False,
        0,
    ),
    # The 2006 protocol lists points A, B, and C before point 1; column 5 is point 1 (the main roll) and column 6 point 2
    # (the additional roll), which add up to the registered voters. The ticket columns follow point 12.
    "pres2006": Layout(
        None,
        0,
        {"*": {"reg": (5, 6), "voters": (7,), "box": (10,), "inv": (15,), "valid": (16,)}},
        "pres2006",
        0,
        False,
        0,
    ),
    "pres2011": Layout(
        0,
        1,
        {"*": {"reg": (3,), "voters": (7,), "box": (20,), "inv": (26,), "valid": (27,)}},
        "blank_pairs",
        2,
        False,
        0,
    ),
    "pres2016": Layout(
        0,
        1,
        _same(
            {"reg": (6,), "voters": (8,), "box": (17,), "inv": (20,), "valid": (23,), "none": (27,)}, "1", "7", "8", "*"
        ),
        "quint",
        5,
        True,
        3,
    ),
    "ns2021a": Layout(
        0,
        1,
        {
            **_same(
                {"reg": (6,), "voters": (8,), "box": (12,), "inv": (14,), "valid": (15,), "none": (17,)}, "24", "28"
            ),
            **_same(
                {"reg": (6,), "voters": (8,), "box": (11,), "inv": (14,), "valid": (15,), "none": (17,)}, "26", "30"
            ),
            **_same({"valid": (15,), "none": (17,)}, "27", "31", "32", "41"),
            **_same({"reg": (6,), "voters": (8,), "box": (13,)}, "25", "29"),
        },
        "form_pairs",
        2,
        True,
        3,
    ),
    "ns2022": Layout(
        0,
        1,
        {
            **_same(
                {"reg": (8,), "voters": (10,), "box": (14,), "inv": (16,), "valid": (17,), "none": (19,)}, "24", "28"
            ),
            **_same(
                {"reg": (8,), "voters": (10,), "box": (13,), "inv": (16,), "valid": (17,), "none": (19,)}, "26", "30"
            ),
            **_same({"valid": (17,), "none": (19,)}, "27", "31", "32", "41"),
            **_same({"reg": (8,), "voters": (10,), "box": (15,)}, "25", "29"),
        },
        "form_pairs",
        2,
        True,
        3,
    ),
    "ns2023": Layout(
        0,
        1,
        {
            **_same(
                {"reg": (8,), "voters": (10,), "box": (13,), "inv": (16,), "valid": (17,), "none": (23,)}, "24", "28"
            ),
            **_same(
                {"reg": (8,), "voters": (10,), "box": (15,), "inv": (16,), "valid": (19,), "none": (25,)}, "26", "30"
            ),
        },
        "form_pairs",
        4,
        True,
        3,
    ),
    "ns2024a": Layout(
        0,
        1,
        _same(
            {"reg": (8,), "voters": (12,), "box": (12,), "inv": (16, 17, 20), "valid": (18, 21)}, "24", "26", "28", "30"
        ),
        "form_pairs",
        4,
        False,
        3,
    ),
    "ns2024": Layout(
        0,
        1,
        _same(
            {"reg": (8,), "voters": (10,), "box": (10,), "inv": (14, 15, 18), "valid": (16, 19)}, "24", "26", "28", "30"
        ),
        "form_pairs",
        4,
        False,
        3,
    ),
    # The Constitutional Court's recount database: the protocol points sit where they do in `ns2024`, the sections file
    # gains a last column naming the recount ordered for the section, and each list has a fifth field with the votes
    # added by the Court's decision.
    "ns2024k": Layout(
        0,
        1,
        _same(
            {"reg": (8,), "voters": (10,), "box": (10,), "inv": (14, 15, 18), "valid": (16, 19)}, "24", "26", "28", "30"
        ),
        "form_pairs",
        5,
        False,
        4,
    ),
    "pres2026": Layout(
        0,
        1,
        {
            **_same(
                {"reg": (9,), "voters": (11,), "box": (12,), "inv": (13,), "valid": (15,), "none": (17,)}, "24", "28"
            ),
            **_same(
                {"reg": (9,), "voters": (11,), "box": (12, 22), "inv": (13,), "valid": (15, 22), "none": (17, 24)},
                "26",
                "30",
            ),
            **_same({"reg": (9,), "voters": (11,), "box": (14,)}, "25", "29"),
            **_same({"valid": (26,), "none": (28,)}, "32", "41"),
        },
        "triples",
        6,
        True,
        3,
    ),
}


# The National Statistical Institute's register of places (EKATTE), in its 2025 edition: the provinces, the
# municipalities, and every settlement with the municipality it lies in.
EKATTE_URL = "https://www.nsi.bg/nrnm/ekatte/archive/json/Ekatte-2025-json.zip/download"


def url_of(name: str) -> str:
    return f"{BASE}/{name}"


def fetch() -> None:
    pages = [page_of(e) for e in ELECTIONS if e.layout == "pres2006"]
    early = [EP2007.url, EP2007_PAGE, *(b.url for b in BLOCKS), *pages]
    for url in dict.fromkeys([*(e.url for e in ELECTIONS), RECOUNT_URL, RECOUNT_PAGE, LISTS_2009, *early, EKATTE_URL]):
        try:
            raw.fetch(SOURCE, url, suffix=".zip" if url.endswith((".zip", "/download")) else ".html")
        except httpx.HTTPError as failure:
            print(f"{url}: {failure}", flush=True)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CIK_SOURCE = key("source", SOURCE)
NSI_SOURCE = key("source", "nsi")
NATION = key("area", "bg.country", "BG")
PRESIDENCY = key("office", "president")
ASSEMBLY = key("body", "national-assembly")
SYSTEM_HARE = key("electoral_system", "ns", 2013)
SYSTEM_2009 = key("electoral_system", "ns", 2009)
SYSTEM_2009_SMD = key("electoral_system", "ns-majoritarian", 2009)
SYSTEM_EP = key("electoral_system", "ep", 2014)
SYSTEM_2005 = key("electoral_system", "ns", 2005)
SYSTEM_PRES = key("electoral_system", "president", 1992)
SYSTEM_REF = key("electoral_system", "referendum", 2013)


def area(code: int) -> str:
    return key("area", "bg.mir", f"{code:02d}")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


@functools.cache
def _archive(url: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(raw.cached(SOURCE, url).path)


ROLES = {
    "sections": r"sections",
    "protocols": r"(?<!experimental_)protocols",
    "votes": r"(?<![a-z])votes(?!_mv)",
    "parties": r"(?<!local_)(cik_?)?parties",
    "local_parties": r"local_parties",
    "local_candidates": r"local_candidates",
    "candidates": r"(cik_)?candidates",
    "result": r"result",
}


def _members(e: Election) -> dict[str, str]:
    archive = _archive(e.url)
    names = [i.filename for i in archive.infolist() if not i.is_dir() and not i.filename.startswith("suemg/")]
    if e.layout == "ns2005":
        return {
            "sections": "sikproto.txt",
            "protocols": "sikproto.txt",
            "votes": "sikproto.txt",
            "header": "pe2005_export.csv",
        }
    if e.layout == "ns2009":
        return {
            "sections": "pe2009_sections.txt",
            "protocols": "pe2009_protocols.txt",
            "votes": "pe2009_partyvotes.txt",
            "major": "pe2009_majorvotes.txt",
        }
    if e.layout == "ep2009":
        return {
            "sections": "euro2009_sections.txt",
            "protocols": "euro2009_protocols.txt",
            "votes": "euro2009_protocols.txt",
        }
    if e.layout == "pres2006":
        by_name = {n.rpartition("/")[2].lower(): n for n in names}
        return {
            "sections": next(n for b, n in by_name.items() if b.endswith("_sections.txt")),
            "protocols": next(n for b, n in by_name.items() if b.endswith("_protocols.txt")),
            "readme": by_name["readme.txt"],
        }
    if e.layout == "pres2011":
        names = [n for n in names if "_president_" in n.rpartition("/")[2]]
    folders = list(dict.fromkeys(n.rpartition("/")[0] for n in names))
    if e.folder is not None:
        names = [n for n in names if n.rpartition("/")[0] == folders[e.folder]]
    out: dict[str, str] = {}
    for role, pattern in ROLES.items():
        for name in names:
            base = name.rpartition("/")[2].lower()
            if base.endswith((".txt", ".csv")) and re.search(pattern, base) and "readme" not in base:
                out.setdefault(role, name)
    if "local_candidates" in out:
        out["candidates"] = out["local_candidates"]
    return out


def rows(e: Election, role: str) -> list[list[str]]:
    name = _members(e).get(role)
    if name is None:
        return []
    text = _decode(_archive(e.url).read(name))
    return [line.split(";") for line in text.replace("\r", "").split("\n") if line.strip()]


def cite(p: Package, e: Election) -> None:
    artifact = raw.cached(SOURCE, e.url)
    if e.id in SHARED_BUNDLE:
        p.uses(artifact)  # the package this one depends on already holds the artifact row
    else:
        p.artifact(artifact, CIK_SOURCE)


def num(row: list[str], index: int) -> int | None:
    if index > len(row):
        return None
    text = row[index - 1].strip()
    return int(text) if text.lstrip("-").isdigit() else None


class Section(NamedTuple):
    code: str
    place: str
    ekatte: str
    mobile: bool
    ship: bool


def sections(e: Election) -> dict[str, Section]:
    layout = LAYOUTS[e.layout]
    out: dict[str, Section] = {}
    for r in rows(e, "sections"):
        if e.layout in ("ns2013", "ref2013", "pres2011"):
            code, place, ekatte, mobile, ship = r[1], r[4], r[5], r[0] == "П", r[0] == "К"
        elif e.layout == "ns2005":
            code, place, ekatte, mobile, ship = r[0], r[1], "", False, False
        elif e.layout in ("ep2009", "ns2009", "pres2006"):
            code, place, ekatte, mobile, ship = r[0], r[1], r[2], False, False
        elif layout.votes in ("order_pairs",):
            code, place, ekatte = r[0], r[1], r[2]
            mobile, ship = r[3] == "1", r[4] == "1"
        else:
            code, place, ekatte = r[0], r[4], r[3]
            tail = r[-layout.flags :] if layout.flags else []
            mobile = len(tail) > 0 and tail[0] == "1"
            ship = len(tail) > 1 and tail[1] == "1"
        out[code] = Section(code, place.strip(), ekatte.strip(), mobile, ship)
    return out


def protocol_figures(e: Election, layout_name: str | None = None) -> dict[str, dict[str, int]]:
    layout = LAYOUTS[layout_name or e.layout]
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows(e, "protocols"):
        form = r[layout.form] if layout.form is not None else ""
        spec = layout.forms.get(form) or layout.forms.get("*")
        if spec is None:
            continue
        # A starred row of the 2009 files holds the electronic votes of a section; they add to that section's ballots.
        starred = r[layout.section].startswith("*")
        figures = out[r[layout.section].lstrip("*")]
        for name, columns in spec.items():
            found = [v for v in (num(r, c) for c in columns) if v is not None]
            if not found:
                continue
            if name in ("reg", "voters"):
                if starred:
                    continue
                figures.setdefault(name, sum(found) if e.layout == "pres2006" else found[0])
            else:
                figures[name] = figures.get(name, 0) + sum(found)
    return out


def party_votes(e: Election) -> dict[str, dict[str, int]]:
    """Valid votes per section and list number, summed over every row a section has."""
    layout = LAYOUTS[e.layout]
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows(e, "votes"):
        if layout.votes == "ns2005":
            for index, text in enumerate(r[15:], start=1):
                out[r[0]][str(index)] += int(text) if text.strip().isdigit() else 0
            continue
        if layout.votes == "ep2009":
            for index, text in enumerate(r[24:], start=1):
                if text.strip().isdigit():
                    out[r[0]][str(index)] += int(text)
            continue
        if layout.votes == "blank_pairs":
            section, body, step, at = r[1], r[2:], 2, 1
        elif layout.votes == "order_pairs":
            section, body = r[0], [x for x in r[1:]]
            while body and body[-1] == "":
                body.pop()
            for index in range(0, len(body) - 1, 2):
                out[section][str(index // 2 + 1)] += int(body[index] or 0)
            continue
        elif layout.votes in ("triples", "quint"):
            section, body, step, at = r[0], r[2:], layout.width, 1
        else:
            section, body, step, at = r[1], r[3:], layout.width, 1
        if layout.votes == "quint":
            for index in range(0, len(body) - 4, step):
                out[section][body[index]] += int(body[index + 1] or 0)
            continue
        for index in range(0, len(body) - step + 1, step):
            out[section][body[index]] += int(body[index + at] or 0)
    return out


# The 2026 second round: a hybrid section's protocol also holds the machine votes (points 9 and 11), and a section
# that votes by machine alone has its votes in the machine data forms.
_HYBRID = {
    "reg": (9,),
    "voters": (11,),
    "box": (12, 18, 22),
    "inv": (13, 21, 25),
    "valid": (15, 19, 20, 23, 24),
    "none": (17, 20, 24),
}
LAYOUTS["pres2026b"] = Layout(
    0,
    1,
    {
        **_same({"reg": (9,), "voters": (11,), "box": (12,), "inv": (13,), "valid": (15,), "none": (17,)}, "24", "28"),
        **_same(_HYBRID, "26", "30"),
        **_same({"reg": (9,), "voters": (11,), "box": (14,)}, "25", "29"),
        **_same({"valid": (27, 28), "none": (28,)}, "32", "41"),
    },
    "triples",
    6,
    True,
    3,
)
LAYOUTS["pres2021"] = LAYOUTS["ns2022"]  # the 2021 presidential protocols number their points like the 2022 ones
LAYOUTS["ref2013"] = Layout(0, 1, {}, "none")
LAYOUTS["ref2015"] = Layout(0, 1, {}, "none", flags=3)
LAYOUTS["ref2016"] = Layout(0, 1, {}, "none", flags=3)

SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        SYSTEM_2009,
        date(2009, 1, 1),
        date(2011, 1, 1),
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "209 of the 240 seats were allocated among the lists that passed a 4% national threshold, in each of the "
            "31 multi-member constituencies, by the Hare quota and largest remainders. Voters chose a list and could "
            "not reorder it. The other 31 seats were won one per constituency by plurality, on a second ballot."
        ),
    ),
    (
        SYSTEM_2009_SMD,
        date(2009, 1, 1),
        date(2011, 1, 1),
        {"formula": "fptp"},
        "The candidate with the most votes in the constituency won its single-member seat.",
    ),
    (
        SYSTEM_HARE,
        date(2011, 1, 1),
        None,
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "Seats are allocated among the lists that pass a 4% national threshold, in each of the multi-member "
            "constituencies, by the Hare quota and largest remainders. Preference votes can move a candidate up a list."
        ),
    ),
    (
        SYSTEM_2005,
        date(2005, 1, 1),
        date(2009, 1, 1),
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "Seats were allocated among the lists that passed a 4% national threshold, in each of the 31 multi-member "
            "constituencies, by the Hare quota and largest remainders. Voters chose a list and could not reorder it."
        ),
    ),
    (
        SYSTEM_EP,
        date(2007, 1, 1),
        None,
        {"formula": "hare", "lists": {"order": "flexible", "vacancy": "next_on_list"}},
        (
            "Bulgaria's members of the European Parliament are elected from lists in one national constituency by the "
            "Hare quota and largest remainders. Preference votes can move a candidate up a list."
        ),
    ),
    (
        SYSTEM_PRES,
        date(1992, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "The president and vice president are elected on one ticket. A ticket with more than half of the valid votes "
            "wins in the first round; otherwise the two leading tickets go to a second round a week later."
        ),
    ),
    (
        SYSTEM_REF,
        date(2009, 1, 1),
        None,
        {"formula": "referendum", "majority": {"share": 0.5, "strict": True}},
        "A national referendum question is decided by a majority of the valid ballots.",
    ),
]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bulgaria's National Assembly, presidency, electoral systems, and the CIK as a source",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BULGARIA)
    p.add(
        "source",
        id=CIK_SOURCE,
        kind="official_body",
        publisher="Централна избирателна комисия",
        title="CIK election results open data",
        url="https://results.cik.bg/",
        license="Published for re-use as public sector information; the CIK states no further license terms",
        license_url="https://results.cik.bg/",
        attribution="Централна избирателна комисия (Central Election Commission of Bulgaria)",
    )
    p.add("body", id=ASSEMBLY, polity_id=BULGARIA, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly", "en", "official")
    p.name(ASSEMBLY, "Народно събрание", "bg", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=BULGARIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Bulgaria", "en", "official")
    p.name(PRESIDENCY, "Президент на Република България", "bg", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=BULGARIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return p


class Ekatte(NamedTuple):
    oblasts: dict[str, dict[str, object]]  # NSI province code -> row
    municipalities: dict[str, dict[str, object]]  # NSI municipality code -> row
    settlements: dict[str, dict[str, object]]  # five-digit EKATTE code -> row


@functools.cache
def ekatte() -> Ekatte:
    """The province, municipality, and settlement tables of the 2025 EKATTE edition (the last row of each file is
    a note on when the file was made)."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, EKATTE_URL).path)

    def table(name: str, field: str) -> list[dict[str, object]]:
        return [row for row in json.loads(archive.read(name)) if field in row]

    return Ekatte(
        {str(r["oblast"]): r for r in table("ek_obl.json", "oblast")},
        {str(r["obshtina"]): r for r in table("ek_obst.json", "obshtina")},
        {str(r["ekatte"]): r for r in table("ek_atte.json", "ekatte")},
    )


def settlement(code: str) -> str:
    return key("area", "bg.ekatte", code)


def ekatte_code(text: str) -> str:
    """The five-digit code of a section's settlement, or an empty string when the CIK gives none."""
    return text.strip().zfill(5) if text.strip().isdigit() and int(text) else ""


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bulgaria's country area, its 31 multi-member constituencies, its provinces, municipalities, and settlements, "
        "and the scheme of polling sections",
    )
    p.external.add(BULGARIA)
    p.add(
        "source",
        id=NSI_SOURCE,
        kind="official_body",
        publisher="Национален статистически институт",
        title="Единен класификатор на административно-териториалните и териториалните единици (ЕКАТТЕ)",
        url="https://www.nsi.bg/nrnm/ekatte/",
        license="Published under the National Statistical Institute's license for the statistical information it produces and distributes",
        license_url="https://www.nsi.bg/nrnm/ekatte/index",
        attribution="Национален статистически институт (National Statistical Institute of Bulgaria)",
    )
    p.artifact(raw.cached(SOURCE, EKATTE_URL), NSI_SOURCE)
    for code, rank, kind, noun in (
        ("bg.country", 0, "administrative", "country"),
        ("bg.mir", 1, "electoral_district", "multi-member constituency"),
        ("bg.oblast", 1, "administrative", "province"),
        ("bg.obshtina", 2, "administrative", "municipality"),
        ("bg.ekatte", 3, "administrative", "settlement"),
        ("bg.sik", 4, "precinct", "polling section"),
    ):
        p.add("scheme", code=code, polity_id=BULGARIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NATION, scheme_code="bg.country")
    p.add("area_code", area_id=NATION, code="BG")
    p.name(NATION, "Bulgaria", "en", "official")
    p.name(NATION, "България", "bg", "native", False)
    for code, (english, native) in MIR.items():
        id = area(code)
        p.add("area", id=id, scheme_code="bg.mir")
        p.add("area_code", area_id=id, code=f"{code:02d}")
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, f"{code:02d}. {english}", "en", "official")
        p.name(id, f"{code:02d}. {native}", "bg", "native", False)
    # The provinces, municipalities, and settlements. The ids of the first two are the ones the local-election
    # packages use, so that both families of elections roll up to the same areas.
    where = ekatte()
    for number, (nsi_code, english, native) in cik_local.OBLASTS.items():
        id = cik_local.oblast(number)
        p.add("area", id=id, scheme_code="bg.oblast")
        p.add("area_code", area_id=id, code=nsi_code)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.add("external_id", entity_id=id, scheme="bg.cik.oblast", value=f"{number:02d}")
        p.name(id, english, "en", "official")
        p.name(id, native, "bg", "native", False)
    for nsi_code, row in sorted(where.municipalities.items()):
        code = cik_local.cik_code(nsi_code)
        id = cik_local.obshtina(code)
        p.add("area", id=id, scheme_code="bg.obshtina")
        p.add("area_code", area_id=id, code=nsi_code)
        p.add("area_parent", area_id=id, parent_id=cik_local.oblast(int(code[:2])))
        p.add("external_id", entity_id=id, scheme="bg.cik.obshtina", value=code)
        p.name(id, str(row["name_en"]), "en", "official")
        p.name(id, str(row["name"]), "bg", "native", False)
    for code, row in sorted(where.settlements.items()):
        id = settlement(code)
        p.add("area", id=id, scheme_code="bg.ekatte")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=cik_local.obshtina(cik_local.cik_code(str(row["obshtina"]))))
        p.name(id, str(row["name_en"]), "en", "official")
        p.name(id, str(row["name"]), "bg", "native", False)
    return p


def mir_of(e: Election, code: str) -> int:
    if e.kind in ("ep", "pres") or e.layout in ("ref2013", "ref2015", "ref2016"):
        return 0
    return int(code[:2])


def abroad(e: Election, code: str) -> bool:
    return code.startswith("29" if e.layout in ("ref2015", "pres2011") else "32")


def page_of(e: Election) -> str:
    """The CIK's national results page of a 2006 round, which sits beside its export."""
    return e.url.rpartition("/")[0] + "/"


def party_kind(name: str) -> str:
    low = name.lower()
    if low.startswith(("инициативен комитет", "ик ")):
        return "independent_group"
    if low.startswith(("коалиция", "кп ")):
        return "bloc"
    return "party"


def parties(e: Election) -> dict[str, str]:
    if e.layout == "ep2009":
        text = _decode(_archive(e.url).read("ReadMe.txt"))
        found = (re.match(r"\s*\d+\)\s*Гласове за:\s*(\d+)\.\s*(.+?)\s*$", line) for line in text.splitlines())
        return {m[1]: m[2] for m in found if m}
    if e.layout == "ns2005":
        found = (re.match(r"(\d+)\.\s*(.+)$", c.strip()) for c in (rows(e, "header") or [[]])[0])
        return {m[1]: m[2].strip() for m in found if m}
    return {r[-2].strip(): r[-1].strip() for r in rows(e, "parties") if len(r) >= 2 and r[-2].strip().isdigit()}


class Candidate(NamedTuple):
    mir: int
    party: str
    position: int
    name: str
    elected: bool
    party_name: str


def candidates(e: Election) -> list[Candidate]:
    out: list[Candidate] = []
    for r in rows(e, "candidates"):
        r = [c.strip() for c in r]
        if e.layout == "pres2011":
            out.append(Candidate(0, r[0], int(r[0]), f"{r[1]} и {r[2]}", False, r[3]))
        elif e.layout == "ns2013":
            out.append(Candidate(int(r[0]), r[1], int(r[2]), r[3], False, r[4]))
        elif e.layout == "ns2014":
            out.append(Candidate(int(r[0]), r[1], int(r[3]), r[4], False, r[2]))
        elif e.layout == "ep2014":
            out.append(Candidate(0, r[0], int(r[2]), r[3], False, r[1]))
        elif e.kind == "ns":
            out.append(Candidate(int(r[0]), r[2], int(r[4]), r[5], False, r[3]))
        else:
            out.append(Candidate(0, r[0], int(r[2]), r[3], len(r) > 4 and r[4] == "1", r[1]))
    return out


def local_lists(e: Election) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    for r in rows(e, "local_parties"):
        out[int(r[0])].append(r[2].strip())
    if not out:
        for c in candidates(e):
            if c.party not in out[c.mir]:
                out[c.mir].append(c.party)
    return out


def label(e: Election, day: date) -> tuple[str, str]:
    stem = {"ns": "parliamentary election", "ep": "European Parliament election", "pres": "presidential election"}
    native = {
        "ns": "Избори за народни представители",
        "ep": "Избори за членове на Европейския парламент",
        "pres": "Избори за президент и вицепрезидент",
        "ref": "Национален референдум",
    }
    english = stem.get(e.kind, "national referendum")
    text = f"{day.day} {day.strftime('%B')} {day.year}"
    suffix = ", second round" if e.kind == "pres" and e.round == 2 else ""
    return f"Bulgarian {english}, {text}{suffix}", f"{native[e.kind]}, {day:%d.%m.%Y}"


def values_frame(
    columns: dict[str, list[object]],
) -> pl.DataFrame:
    return pl.DataFrame(columns, schema_overrides={"channel": pl.Int16, "measure": pl.Int16}, strict=False)


def link_units(p: Package, e: Election, unit_of: dict[str, str], secs: dict[str, Section]) -> dict[str, int]:
    """Place each polling section in its settlement, so that results roll up to the municipality and the province.

    The sections files give the EKATTE code of the settlement a section lies in. A section whose code the register
    does not list (a city neighborhood or resort the CIK numbers itself, or a settlement since abolished) is placed
    in its municipality instead, when every other section that shares its first four digits, the CIK's code for the
    municipality, lies in one municipality. Returns how many sections took each route."""
    where = ekatte()
    municipality: dict[str, set[str]] = defaultdict(set)
    chosen: dict[str, str] = {}
    for code in unit_of:
        known = ekatte_code(secs[code].ekatte) if code in secs and not abroad(e, code) else ""
        if known in where.settlements:
            chosen[code] = known
            municipality[code[:4]].add(str(where.settlements[known]["obshtina"]))
    counts = {"settlement": 0, "municipality": 0, "unplaced": 0}
    for code, unit in unit_of.items():
        if abroad(e, code):
            continue
        if code in chosen:
            area_id, method = settlement(chosen[code]), "source_code"
            counts["settlement"] += 1
        elif len(municipality[code[:4]]) == 1:
            nsi_code = next(iter(municipality[code[:4]]))
            area_id, method = cik_local.obshtina(cik_local.cik_code(nsi_code)), "source_code"
            counts["municipality"] += 1
        else:
            counts["unplaced"] += 1
            continue
        p.add("reporting_unit_area", unit_id=unit, area_id=area_id, approximate=False, partial=False, method=method)
    return counts


def build(e: Election) -> Package:
    if e.kind == "ref":
        return referendum(e)
    if e.layout == "ns2009":
        return ns2009(e)
    if e.layout == "pres2006":
        return pres2006(e)
    p = Package(
        f"{SOURCE}/{e.id}",
        SOURCE,
        f"The {e.day:%d %B %Y} Bulgarian {'parliamentary' if e.kind == 'ns' else 'European Parliament' if e.kind == 'ep' else 'presidential'} "
        "election by polling section from the CIK's open data",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            *([f"{SOURCE}/{FIRST_ROUND[e.id]}"] if e.id in FIRST_ROUND else []),
            *([f"{SOURCE}/{SHARED_BUNDLE[e.id]}"] if e.id in SHARED_BUNDLE and e.id not in FIRST_ROUND else []),
        ],
    )
    p.external.add(BULGARIA)
    cite(p, e)
    secs, figures, votes = sections(e), protocol_figures(e), party_votes(e)
    codes = sorted(set(secs) | set(figures) | set(votes))
    day = e.day
    english, native = label(e, day)
    election = key("election", e.id)
    p.add(
        "election",
        id=election,
        polity_id=BULGARIA,
        kind="runoff" if e.round == 2 else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election, english, "en", "official")
    p.name(election, native, "bg", "native", False)
    slugs = {"ns": "general", "ep": "european-parliament", "pres": "presidential"}
    path = f"bg/{day}-{slugs[e.kind]}"
    p.add("slug", path=path, entity_id=election, is_current=True)

    names = parties(e)
    cands = candidates(e)
    contests: list[tuple[int, str, str, str]] = []  # mir, key, english, native
    if e.kind == "ns":
        for m in sorted({mir_of(e, c) for c in codes}):
            en, bg = MIR.get(m, ABROAD)
            contests.append((m, f"{m:02d}", f"{m:02d}. {en}", f"{m:02d}. {bg}"))
    elif e.kind == "ep":
        contests.append((0, "national", english.replace("Bulgarian ", "Bulgaria's "), native))
    else:
        contests.append((0, "president", english, native))

    # lists per contest
    running: dict[int, list[str]] = {}
    if e.kind == "ns":
        local = local_lists(e)
        for m, *_ in contests:
            running[m] = list(local.get(m, []))
    elif e.kind == "ep":
        running[0] = sorted(names, key=int)
    else:
        running[0] = [c.party for c in sorted(cands, key=lambda c: c.party and int(c.party))]
    seen: dict[int, set[str]] = defaultdict(set)
    for section, per in votes.items():
        m = mir_of(e, section)
        for number, count in per.items():
            if count or e.layout != "ns2005":  # a 2005 list with no votes in a constituency did not stand there
                seen[m].add(number)
    for m, listed in running.items():
        running[m] = [*listed, *sorted(seen[m] - set(listed), key=int)]
    nominators = {c.party: c.party_name for c in cands}
    all_numbers = sorted({n for numbers in running.values() for n in numbers}, key=lambda x: int(x))
    independent = {n for n in all_numbers if e.layout == "ns2005" and int(n) > len(names)}
    party_id = {n: p.id("party", e.id, n) for n in all_numbers if n not in independent}
    for n in all_numbers:
        if n in independent:
            continue
        name = names.get(n) or nominators.get(n) or f"List {n}"
        p.add("party", id=party_id[n], kind=party_kind(name), polity_id=BULGARIA)
        p.add("external_id", entity_id=party_id[n], scheme="bg.cik.party", value=f"{e.id}:{n}")
        p.name(party_id[n], name, "bg", "official")

    has_none = LAYOUTS[e.layout].none
    sets: dict[int, str] = {}
    options: dict[tuple[int, str], str] = {}
    none_option: dict[int, str] = {}
    ids = {"contest": [], "option": []}
    system = {"ns": SYSTEM_2005 if e.layout == "ns2005" else SYSTEM_HARE, "ep": SYSTEM_EP, "pres": SYSTEM_PRES}[e.kind]
    previous = key("contest", FIRST_ROUND[e.id], "president") if e.id in FIRST_ROUND else None
    for m, ckey, en, bg in contests:
        contest = key("contest", e.id, ckey)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office" if e.kind == "pres" else "list_allocation",
            office_id=PRESIDENCY if e.kind == "pres" else None,
            body_id=ASSEMBLY if e.kind == "ns" else None,
            district_area_id=area(m) if e.kind == "ns" and m in MIR else (NATION if e.kind != "ns" else None),
            magnitude=1 if e.kind == "pres" else None,
            round_number=e.round if e.kind == "pres" else 1,
            previous_round_id=previous,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, en, "en", "official")
        p.name(contest, bg, "bg", "native", False)
        slug = (
            "president"
            if e.kind == "pres"
            else "national"
            if e.kind == "ep"
            else f"{m:02d}-{changeset.slug(MIR.get(m, ABROAD)[0])}"
        )
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = sets[m] = key("result_set", e.id, ckey)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CIK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for n in running[m]:
            option = options[(m, n)] = key("option", e.id, ckey, n)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="ticket" if e.kind == "pres" else "candidate" if n in independent else "list",
                party_id=party_id.get(n),
                ballot_position=None if n in independent else int(n),
                ballot_label=f"Независим кандидат {int(n) - len(names)}"
                if n in independent
                else names.get(n) or nominators.get(n) or f"List {n}",
            )
        if has_none:
            option = none_option[m] = key("option", e.id, ckey, "none")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="none_of_the_above",
                ballot_label="Не подкрепям никого",
            )
        ids["contest"].append(contest)

    # people, candidacies and outcomes
    people: list[dict[str, object]] = []
    named: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []
    elected: dict[tuple[int, str], list[str]] = defaultdict(list)
    for c in cands:
        m = c.mir if e.kind == "ns" else 0
        option = options.get((m, c.party))
        if option is None:
            continue
        tag = f"{m}:{c.party}:{c.position}"
        person = p.id("person", e.id, tag)
        candidacy = p.id("candidacy", e.id, tag)
        if e.kind == "pres":
            head, _, mate = c.name.partition(" и ")
            people.append({"id": person})
            named.append(
                {
                    "id": p.id("name", person),
                    "entity_id": person,
                    "text": head,
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
            runs.append(
                {"id": candidacy, "person_id": person, "option_id": option, "role": "head", "ballot_name": head}
            )
            if mate:
                mate_id = p.id("person", e.id, tag, "vice")
                people.append({"id": mate_id})
                named.append(
                    {
                        "id": p.id("name", mate_id),
                        "entity_id": mate_id,
                        "text": mate,
                        "lang": "bg",
                        "kind": "official",
                        "is_preferred": True,
                    }
                )
                runs.append(
                    {
                        "id": p.id("candidacy", e.id, tag, "vice"),
                        "person_id": mate_id,
                        "option_id": option,
                        "role": "running_mate",
                        "ballot_name": mate,
                    }
                )
        else:
            people.append({"id": person})
            named.append(
                {
                    "id": p.id("name", person),
                    "entity_id": person,
                    "text": c.name,
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
            runs.append(
                {
                    "id": candidacy,
                    "person_id": person,
                    "option_id": option,
                    "role": "list_member",
                    "list_position": c.position,
                    "ballot_name": c.name,
                }
            )
        if c.elected:
            elected[(m, c.party)].append(candidacy)
    if people:
        p.frame("person", pl.DataFrame(people))
        p.frame("name", pl.DataFrame(named))
        p.frame("candidacy", pl.DataFrame(runs))
    if e.layout == "ns2013":
        seats = defaultdict(int)
        for r in rows(e, "result"):
            number = r[1].strip()
            for m, n in zip(r[3::2], r[4::2], strict=False):
                if m.strip().isdigit() and (int(m), number) in options:
                    seats[(int(m), number)] += int(n)
                    p.add(
                        "outcome",
                        result_set_id=sets[int(m)],
                        option_id=options[(int(m), number)],
                        kind="won",
                        seats=int(n),
                    )
        for m, rs in sets.items():
            total = sum(n for (mir, _), n in seats.items() if mir == m)
            if total:
                p.rows["contest"][[c["id"] for c in p.rows["contest"]].index(key("contest", e.id, f"{m:02d}"))][
                    "magnitude"
                ] = total
    if e.layout == "pres2011":
        declared = {r[1].strip(): (r[0].strip(), int(r[4])) for r in rows(e, "result") if len(r) >= 5}
        for c in cands:
            status = declared.get(c.party, ("", 0))[0]
            kind = (
                "elected"
                if status == "И"
                else "advanced"
                if status == "Б"
                else "lost"
                if e.round == 2
                else "eliminated"
            )
            p.add("outcome", result_set_id=sets[0], option_id=options[(0, c.party)], kind=kind)
        for number, (_, count) in declared.items():
            p.add("declared", result_set_id=sets[0], option_id=options[(0, number)], measure=VOTES, value=count)
    elif e.kind == "pres" and e.round == 2 and any(c.elected for c in cands):
        for c in cands:
            kind = "elected" if c.elected else "lost"
            p.add("outcome", result_set_id=sets[0], option_id=options[(0, c.party)], kind=kind)
    elif e.kind == "ep":
        for (m, n), members in elected.items():
            p.add("outcome", result_set_id=sets[m], option_id=options[(m, n)], kind="won", seats=len(members))
            for candidacy in members:
                p.add(
                    "outcome", result_set_id=sets[m], option_id=options[(m, n)], candidacy_id=candidacy, kind="elected"
                )
    second = next((x for x in ELECTIONS if x.id == SECOND_ROUND.get(e.id)), None)
    if second is not None and e.id not in KEEP_FIRST:
        finalists = {x.name for x in candidates(second)}
        for c in cands:
            kind = "advanced" if c.name in finalists else "eliminated"
            p.add("outcome", result_set_id=sets[0], option_id=options[(0, c.party)], kind=kind)

    # units and values
    unit_rows: list[dict[str, object]] = []
    unit_names: list[dict[str, object]] = []
    unit_of: dict[str, str] = {}
    for code in codes:
        section = secs.get(code, Section(code, "", "", False, False))
        unit = unit_of[code] = p.id("unit", e.id, code)
        out = abroad(e, code)
        m = mir_of(e, code) if e.kind == "ns" else 0
        in_mir = e.kind == "ns" and m in MIR and not out
        unit_rows.append(
            {
                "id": unit,
                "polity_id": BULGARIA,
                "scheme_code": "bg.sik",
                "code": f"{e.id}:{code}",
                "area_relation": "unlocated" if out else "within",
                "area_id": None if out else (area(m) if in_mir else NATION),
                "kind": "abroad_mission" if out else "mobile" if section.mobile or section.ship else "standard",
            }
        )
        if section.place:
            unit_names.append(
                {
                    "id": p.id("name", unit),
                    "entity_id": unit,
                    "text": f"{section.place}, {code}",
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame("name", pl.DataFrame(unit_names))
    link_units(p, e, unit_of, secs)
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}

    def put(rs: str, unit: str, option: str | None, measure: int, value: int) -> None:
        cols["result_set_id"].append(rs)
        cols["unit_id"].append(unit)
        cols["option_id"].append(option)
        cols["measure"].append(measure)
        cols["value"].append(value)

    for code in codes:
        m = mir_of(e, code)
        rs, unit = sets[m], unit_of[code]
        f = figures.get(code, {})
        for name, measure in MEASURES.items():
            if name in f:
                put(rs, unit, None, measure, f[name])
        for n, count in votes.get(code, {}).items():
            option = options.get((m, n))
            if option is not None:
                put(rs, unit, option, VOTES, count)
        if has_none and "none" in f:
            put(rs, unit, none_option[m], VOTES, f["none"])
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    accept(p, e, figures, votes, secs)
    return p


RECOUNT_ID = "ns-2024-10-27-recount"
RECOUNT_URL = Z + "pe202410_ks/opendata/export.zip"
RECOUNT_PAGE = Z + "pe202410_ks/opendata/index.html"
RECOUNT_METHOD = (
    "The Central Election Commission (CIK) published this database after it entered the Constitutional Court's "
    "decision on the 27 October 2024 election, which ordered the section commissions' paper and machine ballots, or "
    "their invalid votes, counted again in the sections the Court named. It uses the same files as the original "
    "count. Registered voters, voters, ballots in the box, and invalid ballots are the figures printed in the section "
    "protocols; votes for each list are the list totals of the votes file, which include any votes the decision "
    "added. The valid-ballots point of the protocol is stored as printed, and it was not updated for the "
    "recount in every section. Preference votes are not stored here yet."
)
RECOUNT_FLAGS = {"0": "not recounted", "1": "paper and machine ballots recounted", "2": "invalid votes recounted"}


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.2f}"


def recount() -> Package:
    """The October 2024 election after the Constitutional Court's recount, as a result set that supersedes each
    constituency's official one. It holds the same sections, lists, and contests as the original count."""
    official = next(x for x in ELECTIONS if x.id == "ns-2024-10-27")
    revised = official._replace(url=RECOUNT_URL, layout="ns2024k")
    e = official
    p = Package(
        f"{SOURCE}/{RECOUNT_ID}",
        SOURCE,
        "The 27 October 2024 Bulgarian parliamentary election by polling section after the Constitutional Court's "
        "recount, from the CIK's open data",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{e.id}"],
    )
    p.artifact(raw.cached(SOURCE, RECOUNT_URL), CIK_SOURCE)
    p.uses(raw.cached(SOURCE, RECOUNT_PAGE))
    p.uses(raw.cached(SOURCE, e.url))

    secs, figures, votes = sections(revised), protocol_figures(revised), party_votes(revised)
    old_secs, old_figures, old_votes = sections(e), protocol_figures(e), party_votes(e)
    if set(secs) != set(old_secs) or set(figures) != set(old_figures) or set(votes) != set(old_votes):
        raise ValueError("the recount database does not hold the same sections as the original count")
    if any(secs[c] != old_secs[c] for c in secs):
        raise ValueError("the recount database describes a section differently from the original count")
    decided = {r[0]: r[-1] for r in rows(revised, "sections")}
    codes = sorted(secs)
    names = parties(e)
    cands = candidates(e)
    nominators = {c.party: c.party_name for c in cands}

    # The options of the original count: a list is an option where the constituency's lists or its votes name it.
    local = local_lists(e)
    seen: dict[int, set[str]] = defaultdict(set)
    for code, per in old_votes.items():
        seen[mir_of(e, code)].update(per)
    constituencies = sorted(seen)
    options: dict[tuple[int, str], str] = {}
    for m in constituencies:
        for n in [*local.get(m, []), *sorted(seen[m] - set(local.get(m, [])), key=int)]:
            options[(m, n)] = key("option", e.id, f"{m:02d}", n)
    for code, per in votes.items():
        for n in per:
            if (mir_of(e, code), n) not in options:
                raise ValueError(f"section {code} holds votes for list {n}, which the original count does not have")

    def name_of(n: str) -> str:
        return names.get(n) or nominators.get(n) or f"List {n}"

    # What changed, section by section and in each constituency.
    def figure(code: str, kind: str, source: dict[str, dict[str, int]]) -> int:
        return source[code].get(kind, 0)

    changed: dict[str, list[str]] = {}
    for code in codes:
        what = [k for k in MEASURES if figure(code, k, figures) != figure(code, k, old_figures)]
        if any(votes[code].get(n, 0) != old_votes[code].get(n, 0) for n in {*votes[code], *old_votes[code]}):
            what.append("votes")
        if what:
            changed[code] = what
    if any(decided[c] == "0" for c in changed):
        raise ValueError("a section the decision did not name differs from the original count")

    def total(source: dict[str, dict[str, int]], kind: str, within: list[str]) -> int:
        return sum(source[c].get(kind, 0) for c in within)

    def list_total(source: dict[str, dict[str, int]], within: list[str]) -> int:
        return sum(sum(source[c].values()) for c in within)

    by_mir: dict[int, list[str]] = defaultdict(list)
    for code in codes:
        by_mir[mir_of(e, code)].append(code)

    def story(within: list[str], where: str) -> str:
        moved = [c for c in within if c in changed]
        named = [c for c in within if decided[c] != "0"]
        paper = sum(1 for c in named if decided[c] == "1")
        votes_before, votes_after = list_total(old_votes, within), list_total(votes, within)
        invalid_before, invalid_after = total(old_figures, "inv", within), total(figures, "inv", within)
        lists: dict[str, int] = defaultdict(int)
        for c in moved:
            for n in {*votes[c], *old_votes[c]}:
                lists[n] += votes[c].get(n, 0) - old_votes[c].get(n, 0)
        gains = sorted(lists.items(), key=lambda kv: (-abs(kv[1]), int(kv[0])))[:5]
        stale = sum(1 for c in within if figure(c, "valid", figures) != sum(votes[c].values()))
        old_stale = sum(1 for c in within if figure(c, "valid", old_figures) != sum(old_votes[c].values()))
        text = (
            f"{where}: the decision names {len(named):,} of {len(within):,} sections ({paper:,} for a recount of "
            f"paper and machine ballots, {len(named) - paper:,} for a recount of invalid votes), and {len(moved):,} "
            f"of them differ from the original count in a figure or a list's votes. Votes for lists go from "
            f"{votes_before:,} to {votes_after:,} ({votes_after - votes_before:+,}); invalid ballots from "
            f"{invalid_before:,} to {invalid_after:,} ({invalid_after - invalid_before:+,}). "
            f"Registered voters, voters, and ballots in the box are the same in every section"
        )
        if any(figure(c, k, figures) != figure(c, k, old_figures) for c in within for k in ("reg", "voters", "box")):
            raise ValueError(f"{where}: the recount moves a figure the note says it keeps")
        text += (
            f". The valid ballots printed in the protocol differ from the list votes in {stale:,} sections "
            f"({old_stale:,} in the original count)."
        )
        if gains and moved:
            text += " Largest net changes by list: " + "; ".join(f"{name_of(n)} {d:+,}" for n, d in gains if d) + "."
        return text

    election_note = story(codes, "Nationally")
    sets: dict[int, str] = {}
    for m in constituencies:
        ckey = f"{m:02d}"
        contest = key("contest", e.id, ckey)
        original = key("result_set", e.id, ckey)
        revised_set = sets[m] = key("result_set", RECOUNT_ID, ckey)
        # The original set stays in its own package, which holds its facts; this one points at it.
        p.add(
            "result_set",
            id=revised_set,
            election_id=key("election", e.id),
            contest_id=contest,
            stage="court_revised",
            kind="counted",
            source_id=CIK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
            supersedes_id=original,
        )
        p.add(
            "note",
            id=key("note", revised_set),
            entity_id=revised_set,
            kind="methodology",
            body_md=RECOUNT_METHOD,
            lang="en",
        )
        en = MIR.get(m, ABROAD)[0]
        p.add(
            "note",
            id=key("note", revised_set, "changes"),
            entity_id=revised_set,
            kind="caveat",
            body_md=(
                "This set supersedes the original count of the constituency. "
                + story(by_mir[m], f"{m:02d}. {en}")
                + " "
                + election_note
            ),
            lang="en",
        )

    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}

    def put(rs: str, unit: str, option: str | None, measure: int, value: int) -> None:
        cols["result_set_id"].append(rs)
        cols["unit_id"].append(unit)
        cols["option_id"].append(option)
        cols["measure"].append(measure)
        cols["value"].append(value)

    for code in codes:
        m = mir_of(e, code)
        rs, unit = sets[m], p.id("unit", e.id, code)
        for name, measure in MEASURES.items():
            if name in figures[code]:
                put(rs, unit, None, measure, figures[code][name])
        for n, count in votes[code].items():
            put(rs, unit, options[(m, n)], VOTES, count)
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    accept(p, revised, figures, votes, secs)
    stale = sum(1 for c in codes if figure(c, "valid", figures) != sum(votes[c].values()))
    p.accept(
        "accounting.votes_valid",
        f"in {stale:,} sections the votes printed for the lists do not add up to the valid ballots printed in the "
        "protocol: the Constitutional Court's database changes the list votes and the invalid ballots of the "
        "sections it named but leaves the protocol's valid-ballots point as it was in the original count; "
        "stored as printed",
    )
    return p


# The lists of the 2009 files are columns, not numbers: the n-th populated column of a section's party votes is the
# n-th list on the national table, which skips ballot numbers 9 and 14. Column totals match that table's votes.
RESULTS_2009 = re.compile(
    r"<tr><td>(\d+)</td><td>(.*?)</td><td class=\"int\">(\d+)</td><td class=\"int\">[\d.]+%</td></tr>"
)
INDEPENDENT_2009 = 19  # the majoritarian files have one column after the 18 lists, for candidates no list nominated


def lists_2009() -> list[tuple[str, str, int]]:
    """Ballot number, name, and votes of each list, from the CIK's national table of the 2009 election."""
    text = _decode(raw.cached(SOURCE, LISTS_2009).path.read_bytes())
    return [(m[1], html.unescape(m[2]).strip(), int(m[3])) for m in RESULTS_2009.finditer(text)]


def cell_votes(e: Election, role: str) -> dict[str, dict[int, int]]:
    """Votes per section and column of a 2009 votes file; a starred row adds to its section, an empty cell is absent."""
    out: dict[str, dict[int, int]] = defaultdict(dict)
    for r in rows(e, role):
        into = out[r[0].lstrip("*")]
        for column, text in enumerate(r[1:], start=1):
            if text.strip().isdigit():
                into[column] = into.get(column, 0) + int(text)
    return out


def accept_figures(p: Package, figures: dict[str, dict[str, int]], votes: dict[str, dict[int, int]], what: str) -> None:
    over = sum(1 for f in figures.values() if "box" in f and "valid" in f and f["box"] != f.get("inv", 0) + f["valid"])
    short = sum(1 for c, f in figures.items() if "valid" in f and sum(votes.get(c, {}).values()) != f["valid"])
    if over:
        p.accept(
            "accounting.ballots_sum",
            f"the protocols of {over:,} sections print {what} ballots in the box that differ from invalid plus valid "
            "ballots; stored as printed",
        )
    if short:
        p.accept(
            "accounting.votes_valid",
            f"in {short:,} sections the {what} votes printed do not add up to the valid ballots printed in the "
            "protocol; stored as printed",
        )


def ns2009(e: Election) -> Package:
    """The July 2009 National Assembly: a list contest and a single-member race in each of 31 constituencies."""
    p = Package(
        f"{SOURCE}/{e.id}",
        SOURCE,
        "The 5 July 2009 Bulgarian parliamentary election by polling section from the CIK's open data",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BULGARIA)
    cite(p, e)
    p.artifact(raw.cached(SOURCE, LISTS_2009), CIK_SOURCE)
    secs = sections(e)
    list_figures, major_figures = protocol_figures(e), protocol_figures(e, "ns2009m")
    list_votes, major_votes = cell_votes(e, "votes"), cell_votes(e, "major")
    codes = sorted(set(secs) | set(list_figures) | set(list_votes))
    lists = lists_2009()
    assert len(lists) == 18, lists
    english, native = label(e, e.day)
    election = key("election", e.id)
    p.add("election", id=election, polity_id=BULGARIA, kind="general", date_from=e.day, date_to=e.day, status="held")
    p.name(election, english, "en", "official")
    p.name(election, native, "bg", "native", False)
    path = f"bg/{e.day}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)

    party_id = {n: p.id("party", e.id, n) for n, _, _ in lists}
    for n, name, _ in lists:
        p.add(
            "party", id=party_id[n], kind="bloc" if "коалиция" in name.lower() else party_kind(name), polity_id=BULGARIA
        )
        p.add("external_id", entity_id=party_id[n], scheme="bg.cik.party", value=f"{e.id}:{n}")
        p.name(party_id[n], name, "bg", "official")

    mirs = sorted({int(c[:2]) for c in codes})
    present: dict[int, set[int]] = defaultdict(set)  # constituency -> list columns on its ballot
    major_present: dict[int, set[int]] = defaultdict(set)
    for code, per in list_votes.items():
        present[int(code[:2])] |= set(per)
    for code, per in major_votes.items():
        major_present[int(code[:2])] |= set(per)

    sets: dict[tuple[int, str], str] = {}
    options: dict[tuple[int, str, int], str] = {}
    ids: list[str] = []
    for m in mirs:
        en, bg = MIR.get(m, ABROAD)
        slug = f"{m:02d}-{changeset.slug(en)}"
        races = [("list", present[m], f"{m:02d}. {en}", f"{m:02d}. {bg}", slug)]
        if m in MIR:
            races.append(
                (
                    "major",
                    major_present[m],
                    f"{m:02d}. {en}, single-member seat",
                    f"{m:02d}. {bg}, мажоритарен вот",
                    f"{slug}-single-member",
                )
            )
        for race, columns, name_en, name_bg, tail in races:
            contest = key("contest", e.id, f"{m:02d}" if race == "list" else f"{m:02d}-major")
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation" if race == "list" else "body_seats",
                body_id=ASSEMBLY,
                district_area_id=area(m) if m in MIR else None,
                magnitude=None if race == "list" else 1,
                round_number=1,
                electoral_system_id=SYSTEM_2009 if race == "list" else SYSTEM_2009_SMD,
                default_result_kind="counted",
            )
            p.name(contest, name_en, "en", "official")
            p.name(contest, name_bg, "bg", "native", False)
            p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
            result_set = sets[(m, race)] = key("result_set", e.id, m, race)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=CIK_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD_2009[race],
                lang="en",
            )
            ids.append(contest)
            for column in sorted(columns):
                number = lists[column - 1][0] if column <= len(lists) else None
                option = options[(m, race, column)] = key("option", e.id, m, race, column)
                if race == "list":
                    label_text = lists[column - 1][1]
                elif number is not None:
                    label_text = f"Кандидат на {lists[column - 1][1]}"
                else:
                    label_text = "Независим кандидат"
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list" if race == "list" else "candidate",
                    party_id=party_id[number] if number is not None else None,
                    ballot_position=int(number) if number is not None else None,
                    ballot_label=label_text,
                )

    unit_rows: list[dict[str, object]] = []
    unit_names: list[dict[str, object]] = []
    unit_of: dict[str, str] = {}
    for code in codes:
        section = secs.get(code, Section(code, "", "", False, False))
        unit = unit_of[code] = p.id("unit", e.id, code)
        m, out = int(code[:2]), code.startswith("32")
        unit_rows.append(
            {
                "id": unit,
                "polity_id": BULGARIA,
                "scheme_code": "bg.sik",
                "code": f"{e.id}:{code}",
                "area_relation": "unlocated" if out else "within",
                "area_id": None if out else area(m),
                "kind": "abroad_mission" if out else "standard",
            }
        )
        if section.place:
            unit_names.append(
                {
                    "id": p.id("name", unit),
                    "entity_id": unit,
                    "text": f"{section.place}, {code}",
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame("name", pl.DataFrame(unit_names))
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    for code in codes:
        m = int(code[:2])
        for race, figures, votes in (("list", list_figures, list_votes), ("major", major_figures, major_votes)):
            if (m, race) not in sets:
                continue
            rs, unit = sets[(m, race)], unit_of[code]
            for name, measure in MEASURES.items():
                if name in figures.get(code, {}):
                    if name == "reg" and not figures[code][name] and figures[code].get("voters"):
                        continue  # a mission abroad has no roll; its voters register on the day
                    cols["result_set_id"].append(rs)
                    cols["unit_id"].append(unit)
                    cols["option_id"].append(None)
                    cols["measure"].append(measure)
                    cols["value"].append(figures[code][name])
            for column, count in votes.get(code, {}).items():
                cols["result_set_id"].append(rs)
                cols["unit_id"].append(unit)
                cols["option_id"].append(options[(m, race, column)])
                cols["measure"].append(VOTES)
                cols["value"].append(count)
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    accept_figures(p, list_figures, list_votes, "list")
    accept_figures(p, {c: f for c, f in major_figures.items() if not c.startswith("32")}, major_votes, "majoritarian")
    if any("reg" in f and "voters" in f and f["voters"] > f["reg"] for f in list_figures.values()):
        p.accept(
            "accounting.turnout",
            "some sections report more voters than the roll holds, from voters added on the day under the line of "
            "the roll or with voting certificates; stored as printed",
        )
    return p


METHOD_2009 = {
    "list": (
        "Counted by the polling section commissions and published by the Central Election Commission (CIK) as section "
        "protocols and list votes in its open data. Registered voters are the voters on the main and supplementary "
        "rolls, voters are the signatures on the roll, ballots cast are the list ballots found in the box, and valid "
        "ballots are those marked for one list. The 531 votes cast electronically in nine Sofia sections are added to "
        "the paper votes of the same section. The files number lists by column, and the names come from the CIK's "
        "national results table."
    ),
    "major": (
        "Counted by the polling section commissions and published by the Central Election Commission (CIK) as section "
        "protocols and majoritarian votes in its open data. Ballots cast are the majoritarian ballots found in the "
        "box. Each column is the candidate a list nominated in the constituency, in the order of the national lists; "
        "a column after the last list holds a candidate no list nominated. The files do not name the candidates."
    ),
}


class Ticket(NamedTuple):
    number: int
    head: str
    mate: str
    votes: int


def pres2006_page(url: str) -> tuple[list[Ticket], dict[int, int]]:
    """The tickets with their votes and the numbered protocol points of a 2006 national results page."""
    text = _decode(raw.cached(SOURCE, url).path.read_bytes())
    tickets = [
        Ticket(int(m[1]), html.unescape(m[2]).strip(), html.unescape(m[3]).strip(), int(m[4]))
        for m in re.finditer(
            r"<td>(\d+)\.</td>\s*<td>\s*<strong>(.*?)</strong> - кандидат за президент<br />\s*"
            r"<strong>(.*?)</strong> - кандидат за вицепрезидент\s*</td>\s*<td class=\"int\">(\d+)</td>",
            text,
        )
    ]
    points = {
        int(m[1]): int(m[2])
        for m in re.finditer(r"<td class=\"int\">(\d+)</td>\s*<td>[^<]*</td>\s*<td class=\"int\">(\d+)</td>", text)
    }
    return tickets, points


def pres2006_columns(e: Election) -> list[int]:
    """The ticket numbers of the vote columns that follow point 12 of the protocol, in file order."""
    text = _decode(_archive(e.url).read(_members(e)["readme"]))
    return [int(m[1]) for m in re.finditer(r"^\s*\d+\)\s*Гласове за\s+(\d+)\.", text, re.MULTILINE)]


def pres2006_votes(e: Election) -> dict[str, dict[int, int]]:
    """Votes per section and ticket number; a section with no protocol has no entry."""
    numbers = pres2006_columns(e)
    out: dict[str, dict[int, int]] = {}
    for r in rows(e, "protocols"):
        cells = [c.strip() for c in r[16:] if c.strip()]
        assert len(cells) == len(numbers), (e.id, r[0], len(cells))
        out[r[0]] = {n: int(c) for n, c in zip(numbers, cells, strict=True)}
    return out


def pres2006(e: Election) -> Package:
    """A 2006 presidential round by polling section, with the national totals of the CIK's results page declared."""
    first = FIRST_ROUND.get(e.id)
    p = Package(
        f"{SOURCE}/{e.id}",
        SOURCE,
        f"The {e.day:%d %B %Y} Bulgarian presidential election by polling section from the CIK's results export",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/{first}"] if first else [])],
    )
    p.external.add(BULGARIA)
    page = page_of(e)
    p.artifact(raw.cached(SOURCE, e.url), CIK_SOURCE)
    p.artifact(raw.cached(SOURCE, page), CIK_SOURCE)
    tickets, points = pres2006_page(page)
    assert tickets and sum(t.votes for t in tickets) > 0, page
    secs, figures, votes = sections(e), protocol_figures(e), pres2006_votes(e)
    assert set(secs) == set(figures) == set(votes), (e.id, len(secs), len(figures), len(votes))
    assert set(pres2006_columns(e)) == {t.number for t in tickets}, e.id
    # The page's totals of ballots, invalid votes, and valid votes are the sums of the section protocols. Its roll and
    # voter totals are not: they count the abroad ballots as voters, which the protocols leave at zero.
    for name, point in (("box", 8), ("inv", 13), ("valid", 14)):
        assert sum(f[name] for f in figures.values()) == points[point], (e.id, name)
    english, native = label(e, e.day)
    election = key("election", e.id)
    p.add(
        "election",
        id=election,
        polity_id=BULGARIA,
        kind="runoff" if e.round == 2 else "general",
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    p.name(election, english, "en", "official")
    p.name(election, native, "bg", "native", False)
    path = f"bg/{e.day}-presidential"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", e.id, "president")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=NATION,
        magnitude=1,
        round_number=e.round,
        previous_round_id=key("contest", first, "president") if first else None,
        electoral_system_id=SYSTEM_PRES,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.name(contest, native, "bg", "native", False)
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", e.id, "president")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CIK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=(
            "Counted by the 11,809 polling section commissions and published by the Central Election Commission (CIK) "
            "as section protocols and ticket votes. Registered voters are the voters on the main and additional rolls, "
            "voters are the signatures on the roll, ballots cast are the ballots found in the box, and valid ballots "
            "are those marked for one ticket. The national totals of the CIK's results page are stored as declared."
        ),
        lang="en",
    )
    finalists = {t.number for t in pres2006_page(SECOND_URL)[0]}
    unit_rows: list[dict[str, object]] = []
    unit_names: list[dict[str, object]] = []
    unit_of: dict[str, str] = {}
    for code in sorted(secs):
        section = secs[code]
        unit = unit_of[code] = p.id("unit", e.id, code)
        out = abroad(e, code)
        unit_rows.append(
            {
                "id": unit,
                "polity_id": BULGARIA,
                "scheme_code": "bg.sik",
                "code": f"{e.id}:{code}",
                "area_relation": "unlocated" if out else "within",
                "area_id": None if out else NATION,
                "kind": "abroad_mission" if out else "standard",
            }
        )
        if section.place:
            unit_names.append(
                {
                    "id": p.id("name", unit),
                    "entity_id": unit,
                    "text": f"{section.place}, {code}",
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame("name", pl.DataFrame(unit_names))
    link_units(p, e, unit_of, secs)
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}

    def put(unit: str, option: str | None, measure: int, value: int) -> None:
        cols["result_set_id"].append(result_set)
        cols["unit_id"].append(unit)
        cols["option_id"].append(option)
        cols["measure"].append(measure)
        cols["value"].append(value)

    options = {t.number: key("option", e.id, "president", t.number) for t in tickets}
    for code in sorted(secs):
        for name, measure in MEASURES.items():
            if abroad(e, code) and name in ("reg", "voters") and not figures[code][name]:
                continue  # abroad there is no roll; the protocol prints zeros that are not counts of voters
            put(unit_of[code], None, measure, figures[code][name])
        for number, count in votes[code].items():
            put(unit_of[code], options[number], VOTES, count)
    for t in tickets:
        option = key("option", e.id, "president", t.number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            ballot_position=t.number,
            ballot_label=f"{t.head} и {t.mate}",
        )
        head, mate = p.id("person", e.id, t.number), p.id("person", e.id, t.number, "vice")
        for person, who, role in ((head, t.head, "head"), (mate, t.mate, "running_mate")):
            p.add("person", id=person)
            p.add(
                "name",
                id=p.id("name", person),
                entity_id=person,
                text=who,
                lang="bg",
                kind="official",
                is_preferred=True,
            )
            p.add(
                "candidacy",
                id=p.id("candidacy", e.id, t.number, role),
                person_id=person,
                option_id=option,
                role=role,
                ballot_name=who,
            )
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=t.votes)
        if e.round == 2:
            kind = "elected" if t.votes == max(x.votes for x in tickets) else "lost"
        else:
            kind = "advanced" if t.number in finalists else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    accept(p, e, figures, {code: {str(n): c for n, c in v.items()} for code, v in votes.items()}, secs)
    return p


SECOND_URL = "https://pvr2006.cik.bg/results_2/"


def accept(
    p: Package,
    e: Election,
    figures: dict[str, dict[str, int]],
    votes: dict[str, dict[str, int]],
    secs: dict[str, Section],
) -> None:
    layout = LAYOUTS[e.layout]
    over = sum(1 for f in figures.values() if "box" in f and "valid" in f and f["box"] != f.get("inv", 0) + f["valid"])
    short = 0
    for code, f in figures.items():
        if "valid" in f and sum(votes.get(code, {}).values()) + (f.get("none", 0) if layout.none else 0) != f["valid"]:
            short += 1
    if over:
        p.accept(
            "accounting.ballots_sum",
            f"the protocols of {over:,} sections print ballots in the box that differ from invalid plus valid "
            "ballots; stored as printed",
        )
    if short:
        p.accept(
            "accounting.votes_valid",
            f"in {short:,} sections the votes printed for the lists do not add up to the valid ballots printed in "
            "the protocol; stored as printed",
        )
    if any("reg" in f and "voters" in f and f["voters"] > f["reg"] for f in figures.values()):
        p.accept(
            "accounting.turnout",
            "some sections report more voters than the roll holds, from voters added on the day under the line of "
            "the roll or with voting certificates; stored as printed",
        )


QUESTIONS: dict[str, list[tuple[str, str]]] = {
    "ref2013": [
        (
            "Should Bulgaria develop nuclear energy by building a new nuclear power plant?",
            "Трябва ли в България да се развива ядрената енергетика чрез изграждане на нова атомна електроцентрала?",
        )
    ],
    "ref2015": [
        (
            "Do you support the introduction of remote electronic voting in elections and referendums?",
            "Подкрепяте ли в изборите и референдумите да се гласува и дистанционно по електронен път?",
        )
    ],
    "ref2016": [(f"Question {n}", f"Въпрос {n}") for n in (1, 2, 3)],
}


def referendum_figures(e: Election) -> dict[str, list[dict[str, int]]]:
    """Per section and question: reg, voters, box, yes, no, valid, inv (and blank where printed)."""
    out: dict[str, list[dict[str, int]]] = {}
    for r in rows(e, "protocols"):
        if e.layout == "ref2013":
            section, base = r[1], {"reg": num(r, 3), "voters": num(r, 4), "box": num(r, 5), "inv": num(r, 11)}
            asked = [(num(r, 9), num(r, 10), None)]
        elif e.layout == "ref2015":
            section, base = r[1], {"reg": num(r, 5), "voters": num(r, 6), "box": num(r, 7), "inv": num(r, 12)}
            asked = [(num(r, 10), num(r, 11), None)]
        else:
            section, base = r[1], {"reg": num(r, 5), "voters": num(r, 6), "box": num(r, 7), "inv": num(r, 18)}
            asked = [(num(r, y), num(r, n), num(r, b)) for y, n, b in ((9, 10, 11), (12, 13, 14), (15, 16, 17))]
        figures = []
        for yes, no, blank in asked:
            f = {k: v for k, v in base.items() if v is not None}
            if yes is not None and no is not None:
                f.update(yes=yes, no=no, valid=yes + no)
            if blank is not None:
                f["blank"] = blank
            figures.append(f)
        out[section] = figures
    return out


def referendum(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/{e.id}",
        SOURCE,
        f"The {e.day:%d %B %Y} Bulgarian national referendum by polling section from the CIK's open data",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BULGARIA)
    cite(p, e)
    secs, figures = sections(e), referendum_figures(e)
    codes = sorted(set(secs) | set(figures))
    english, native = label(e, e.day)
    election = key("election", e.id)
    p.add(
        "election",
        id=election,
        polity_id=BULGARIA,
        kind="referendum",
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    p.name(election, english, "en", "official")
    p.name(election, native, "bg", "native", False)
    path = f"bg/{e.day}-referendum"
    p.add("slug", path=path, entity_id=election, is_current=True)
    questions = QUESTIONS[e.layout]
    sets: list[str] = []
    yes: list[str] = []
    no: list[str] = []
    for index, (en, bg) in enumerate(questions):
        contest = key("contest", e.id, index + 1)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=NATION,
            round_number=1,
            electoral_system_id=SYSTEM_REF,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, en, "en", "official")
        p.name(contest, bg, "bg", "native", False)
        p.add("slug", path=f"{path}/question-{index + 1}", entity_id=contest, is_current=True)
        result_set = key("result_set", e.id, index + 1)
        sets.append(result_set)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CIK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=REFERENDUM_METHOD,
            lang="en",
        )
        for position, (answer, label_, bucket) in enumerate((("yes", "Да", yes), ("no", "Не", no)), start=1):
            option = key("option", e.id, index + 1, answer)
            bucket.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label_,
            )
    unit_rows: list[dict[str, object]] = []
    unit_names: list[dict[str, object]] = []
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    totals: dict[str, int] = defaultdict(int)
    for code in codes:
        section = secs.get(code, Section(code, "", "", False, False))
        unit = p.id("unit", e.id, code)
        out = abroad(e, code)
        unit_rows.append(
            {
                "id": unit,
                "polity_id": BULGARIA,
                "scheme_code": "bg.sik",
                "code": f"{e.id}:{code}",
                "area_relation": "unlocated" if out else "within",
                "area_id": None if out else NATION,
                "kind": "abroad_mission" if out else "mobile" if section.mobile or section.ship else "standard",
            }
        )
        if section.place:
            unit_names.append(
                {
                    "id": p.id("name", unit),
                    "entity_id": unit,
                    "text": f"{section.place}, {code}",
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
        for index, f in enumerate(figures.get(code, [])):
            for name, measure in (*MEASURES.items(), ("blank", BLANK)):
                if name in f:
                    cols["result_set_id"].append(sets[index])
                    cols["unit_id"].append(unit)
                    cols["option_id"].append(None)
                    cols["measure"].append(measure)
                    cols["value"].append(f[name])
            for answer, option in (("yes", yes[index]), ("no", no[index])):
                if answer in f:
                    cols["result_set_id"].append(sets[index])
                    cols["unit_id"].append(unit)
                    cols["option_id"].append(option)
                    cols["measure"].append(VOTES)
                    cols["value"].append(f[answer])
                    if index == 0:
                        totals[answer] += f[answer]
            if index == 0 and "box" in f:
                totals["box"] += f["box"]
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame("name", pl.DataFrame(unit_names))
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    if e.layout == "ref2013":
        declared = [int(x) for x in rows(e, "result")[0] if x.strip()]
        _, voted, yes_votes, no_votes = declared
        p.add("declared", result_set_id=sets[0], option_id=None, measure=BALLOTS, value=voted)
        p.add("declared", result_set_id=sets[0], option_id=yes[0], measure=VOTES, value=yes_votes)
        p.add("declared", result_set_id=sets[0], option_id=no[0], measure=VOTES, value=no_votes)
    p.accept(
        "accounting.ballots_sum",
        "some protocols print ballots in the box that differ from valid, invalid, and blank ballots; stored as printed",
    )
    p.accept(
        "accounting.turnout",
        "some sections report more voters than the roll holds, from voters added on the day; stored as printed",
    )
    return p


# The elections of 1991 to 2001 come from the CIK's archive of the files that the "Izbori" computing team kept for
# each election: text files in blocks (`[PROP]`, `[MAJ]`, `[LISTI]`, `[PROT]`, `[AGGR]`), one file per constituency and
# one for the country, each documented in the archive's README. The 1991-1999 files are in the Bulgarian DOS
# code page (MIK); the 2001 bundles also carry a Windows (cp1251) copy, which this module reads.
BEFORE_2003 = BASE + "/before_2003/"
ARCHIVE_1999 = BEFORE_2003 + "1991_1999.zip"
ARCHIVE_2001_NS = BEFORE_2003 + "2001_parlament.zip"
ARCHIVE_2001_PR = BEFORE_2003 + "2001_prezident.zip"


class Blocks(NamedTuple):
    id: str
    kind: str  # ns or pres
    day: date
    round: int
    url: str
    folder: str  # folder of the bundle holding the files
    ext: str  # file extension: the round, then the last two digits of the year
    mik: bool  # whether the files are in the MIK code page rather than cp1251
    points: dict[str, tuple[int, ...]]  # measure -> one-based protocol points, summed


_NS91: dict[str, tuple[int, ...]] = {"reg": (1,), "voters": (5,), "box": (6,), "inv": (7, 8), "valid": (10,)}
_PR92: dict[str, tuple[int, ...]] = {"reg": (1,), "voters": (5,), "box": (6,), "inv": (7,), "valid": (8,)}
_NS94: dict[str, tuple[int, ...]] = {"reg": (1,), "voters": (6,), "box": (7,), "inv": (8,), "valid": (12,)}
_PR96: dict[str, tuple[int, ...]] = {"reg": (1, 2), "voters": (3,), "box": (5,), "inv": (12,), "valid": (13,)}
_NS97: dict[str, tuple[int, ...]] = {"reg": (1, 2, 3), "voters": (5,), "box": (7,), "inv": (14,), "valid": (15,)}
_NS01: dict[str, tuple[int, ...]] = {"reg": (1, 2), "voters": (3,), "box": (4,), "inv": (7,), "valid": (8,)}
_PR01: dict[str, tuple[int, ...]] = {"reg": (1, 2), "voters": (3,), "box": (4,), "inv": (7,), "valid": (9,)}
_OLD = "1991-1999/DATA/"
BLOCKS = [
    Blocks("ns-1991-10-13", "ns", date(1991, 10, 13), 1, ARCHIVE_1999, _OLD + "1991/", ".191", True, _NS91),
    Blocks("pr-1992-01-12", "pres", date(1992, 1, 12), 1, ARCHIVE_1999, _OLD + "1992/", ".192", True, _PR92),
    Blocks("pr-1992-01-19", "pres", date(1992, 1, 19), 2, ARCHIVE_1999, _OLD + "1992/", ".292", True, _PR92),
    Blocks("ns-1994-12-18", "ns", date(1994, 12, 18), 1, ARCHIVE_1999, _OLD + "1994/", ".194", True, _NS94),
    Blocks("pr-1996-10-27", "pres", date(1996, 10, 27), 1, ARCHIVE_1999, _OLD + "1996/", ".196", True, _PR96),
    Blocks("pr-1996-11-03", "pres", date(1996, 11, 3), 2, ARCHIVE_1999, _OLD + "1996/", ".296", True, _PR96),
    Blocks("ns-1997-04-19", "ns", date(1997, 4, 19), 1, ARCHIVE_1999, _OLD + "1997/", ".197", True, _NS97),
    Blocks("ns-2001-06-17", "ns", date(2001, 6, 17), 1, ARCHIVE_2001_NS, "DATA/IZB2001_/", ".101", False, _NS01),
    Blocks(
        "pr-2001-11-11", "pres", date(2001, 11, 11), 1, ARCHIVE_2001_PR, "DATA/izb01pr_/tur1/", ".201", False, _PR01
    ),
    Blocks(
        "pr-2001-11-18", "pres", date(2001, 11, 18), 2, ARCHIVE_2001_PR, "DATA/izb01pr_/tur2/", ".301", False, _PR01
    ),
]
# The package that holds the artifact row of each bundle; the others that read it depend on that package.
BUNDLE_HOLDER = {ARCHIVE_1999: "ns-1991-10-13", ARCHIVE_2001_NS: "ns-2001-06-17", ARCHIVE_2001_PR: "pr-2001-11-11"}
SYSTEM_1991 = key("electoral_system", "ns", 1991)
SYSTEMS.append(
    (
        SYSTEM_1991,
        date(1991, 1, 1),
        date(2005, 1, 1),
        {
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "The 240 seats were allocated by the d'Hondt method among the lists that passed a 4% national threshold, "
            "and distributed among the 31 multi-member constituencies. Voters chose a list and could not reorder it."
        ),
    )
)
# MIK, the Bulgarian DOS code page, puts the lowercase letters р to я where cp866 has box-drawing characters.
_MIK = str.maketrans({bytes([b]).decode("cp866"): chr(0x440 + b - 0xB0) for b in range(0xB0, 0xC0)})


def blocks_text(data: bytes, mik: bool) -> dict[str, list[list[str]]]:
    """The blocks of one archive file: block name -> records, each split into fields."""
    text = data.decode("cp866").translate(_MIK) if mik else data.decode("cp1251")
    out: dict[str, list[list[str]]] = {}
    current: list[list[str]] | None = None
    for line in text.replace("\r", "").split("\n"):
        line = line.rstrip()
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            current = None if name == "END" else out.setdefault(name, [])
        elif current is not None and line and not line.startswith("%"):
            current.append(line.split(";"))
    return out


def block_files(b: Blocks) -> dict[int, dict[str, list[list[str]]]]:
    """Constituency number (0 for the country) -> the blocks of its file."""
    return _block_files(b.url, b.folder, b.ext, b.mik)


@functools.cache
def _block_files(url: str, at: str, ext: str, mik: bool) -> dict[int, dict[str, list[list[str]]]]:
    archive = _archive(url)
    out: dict[int, dict[str, list[list[str]]]] = {}
    for name in archive.namelist():
        folder, _, base = name.rpartition("/")
        found = re.fullmatch(r"(\d\d)0000Z0" + re.escape(ext), base, re.IGNORECASE)
        if folder + "/" == at and found:
            out[int(found[1])] = blocks_text(archive.read(name), mik)
    assert 0 in out and len(out) >= 32, (at, ext, sorted(out))
    return out


# Seat counts the constituency files print wrong, with the count the national file and the constituency's own
# magnitude prove, and the note that discloses the correction.
SEAT_FIXES: dict[tuple[str, int, str], tuple[int, str]] = {
    ("ns-1991-10-13", 12, "01"): (
        3,
        (
            "The constituency file gives the Union of Democratic Forces 4 of Montana's 6 seats, which with the 3 of "
            "the Socialist pre-election union makes 7. The national file gives the Union 110 seats in all, which "
            "holds only with 3 here, so 3 is stored."
        ),
    ),
}
INDEPENDENT = "00"  # the ballot number the files give a candidate no party nominated


def ballot(r: list[str]) -> str:
    """The ballot number of a [PROP] record: the third field, or the fifth where the third is blank (2001)."""
    return r[2].strip() or r[4].strip()


def plus(field: str) -> list[int | None]:
    return [int(x) if x.strip().lstrip("-").isdigit() else None for x in field.split("+")]


class Old(NamedTuple):
    code: str
    mir: int
    place: str
    points: list[int | None]
    votes: list[int | None]


def section_code(mir: str, municipality: str, section: str) -> str:
    """Constituency (2 digits), municipality (2, or 4 with the city district in 2001), and section (3)."""
    town = municipality.strip()
    return f"{int(mir):02d}{town if len(town) == 4 else town.zfill(2)}{int(section):03d}"


def old_sections(b: Blocks) -> list[Old]:
    out: list[Old] = []
    for mir, file in sorted(block_files(b).items()):
        if not mir:
            continue
        places = {(r[0].strip(), r[1].strip()): r[2].strip() for r in file.get("NM", []) if len(r) > 2}
        where: dict[str, str] = {}
        for r in file.get("SEC", []):
            code = section_code(r[0], r[1], r[2])
            names = [places.get((code[:4], n.strip()), "") for n in r[4].split("+")]
            where[code] = ", ".join(dict.fromkeys(n for n in names if n))
        for r in file.get("PROT", []):
            assert int(r[1]) == mir, (b.id, r)
            code = section_code(r[1], r[2], r[3])
            out.append(Old(code, mir, where.get(code, ""), plus(r[4]), plus(r[5]) if len(r) > 5 else []))
    return out


def old_figures(b: Blocks, s: Old) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, points in b.points.items():
        found = [s.points[i - 1] for i in points if i <= len(s.points) and s.points[i - 1] is not None]
        if found:
            out[name] = sum(v for v in found if v is not None)
    return out


def _ticket_people(label_text: str) -> list[tuple[str, str]]:
    """The role and name of each person on a printed ticket whose name is written out in full; abbreviated names
    (with initials) are skipped."""
    parts = [x.strip() for x in re.split(r",| и ", label_text) if x.strip()]
    roles = ("head", "running_mate")
    return [(role, x) for role, x in zip(roles, parts, strict=False) if "." not in x and len(x.split()) >= 2]


OLD_METHOD = (
    "Counted by the polling section commissions and kept, with the protocols of every section, in the files of the "
    "elections of {years} that the Central Election Commission (CIK) publishes in its archive. Registered voters are "
    "the voters on the rolls{extra}, voters are the signatures on the rolls, ballots cast are the envelopes found in "
    "the box, and invalid and valid votes are the protocol's own counts. Polling sections are placed in the "
    "multi-member constituency (or, for presidential elections, the constituency of the regional commission) that "
    "counted them; their boundaries differed from today's."
)


def old_build(b: Blocks) -> Package:
    files = block_files(b)
    nation = files[0]
    first = next((x for x in BLOCKS if x.kind == "pres" and x.day.year == b.day.year and x.round == 1), None)
    holder = BUNDLE_HOLDER[b.url]
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    if b.round == 2 and first is not None:
        depends.append(f"{SOURCE}/{first.id}")
    if holder != b.id and f"{SOURCE}/{holder}" not in depends:
        depends.append(f"{SOURCE}/{holder}")
    what = "parliamentary" if b.kind == "ns" else "presidential"
    p = Package(
        f"{SOURCE}/{b.id}",
        SOURCE,
        f"The {b.day:%d %B %Y} Bulgarian {what} election by polling section from the CIK's archive of 1991 to 2001",
        depends=depends,
    )
    p.external.add(BULGARIA)
    artifact = raw.cached(SOURCE, b.url)
    if holder == b.id:
        p.artifact(artifact, CIK_SOURCE)
    else:
        p.uses(artifact)
    e = Election(b.id, b.kind, b.day, b.url, "blocks", None, b.round)
    english, native = label(e, b.day)
    election = key("election", b.id)
    p.add(
        "election",
        id=election,
        polity_id=BULGARIA,
        kind="runoff" if b.round == 2 else "general",
        date_from=b.day,
        date_to=b.day,
        status="held",
    )
    p.name(election, english, "en", "official")
    p.name(election, native, "bg", "native", False)
    path = f"bg/{b.day}-{'general' if b.kind == 'ns' else 'presidential'}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    stated = re.sub(r"\b(\d)\.", r"0\1.", nation["IZB"][0][4].strip())
    assert stated == f"{b.day:%d.%m.%Y}", (b.id, stated)

    sections = old_sections(b)
    mirs = sorted({s.mir for s in sections})
    sets: dict[int, str] = {}
    order: dict[int, list[str]] = {}  # constituency -> ballot numbers in the order of the protocol's vote fields
    options: dict[tuple[int, str], str] = {}
    declared_votes: dict[tuple[int, str], int] = {}
    seats: dict[tuple[int, str], int] = {}
    if b.kind == "ns":
        national = {ballot(r): r for r in nation["PROP"] if ballot(r) != INDEPENDENT}
        party_id = {n: p.id("party", b.id, n) for n in national}
        for n, r in national.items():
            name = " ".join(r[3].split())
            p.add("party", id=party_id[n], kind=party_kind(name), polity_id=BULGARIA)
            p.add("external_id", entity_id=party_id[n], scheme="bg.cik.party", value=f"{b.id}:{n}")
            p.name(party_id[n], name, "bg", "official")
            if r[5].strip() and r[5].strip() != name:
                p.name(party_id[n], r[5].strip(), "bg", "short", False)
        for m in mirs:
            file = files[m]
            en, bg = MIR.get(m, ABROAD)
            contest = key("contest", b.id, f"{m:02d}")
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=ASSEMBLY,
                district_area_id=area(m),
                magnitude=int(file["IZB"][0][5]),
                round_number=1,
                electoral_system_id=SYSTEM_1991,
                default_result_kind="counted",
            )
            p.name(contest, f"{m:02d}. {en}", "en", "official")
            p.name(contest, f"{m:02d}. {bg}", "bg", "native", False)
            p.add("slug", path=f"{path}/{m:02d}-{changeset.slug(en)}", entity_id=contest, is_current=True)
            order[m] = []
            for index, r in enumerate(file["PROP"], start=1):
                assert not r[10].strip() or int(r[10]) == index, (b.id, m, r)
                n = ballot(r)
                independent = n not in national
                tag = f"{n}-{index}" if independent else n
                order[m].append(tag)
                option = options[(m, tag)] = key("option", b.id, f"{m:02d}", tag)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if independent else "list",
                    party_id=None if independent else party_id[n],
                    ballot_position=int(n) if int(n) else None,
                    ballot_label=" ".join((r[3] if independent else national[n][3]).split()),
                )
                declared_votes[(m, tag)] = int(r[8])
                won = SEAT_FIXES.get((b.id, m, tag), (int(r[9]) if r[9].strip() else 0, ""))[0]
                if won:
                    seats[(m, tag)] = won
            sets[m] = key("result_set", b.id, f"{m:02d}")
        # candidate lists, tied to their list by its place in the constituency's [PROP] block
        people: list[dict[str, object]] = []
        named: list[dict[str, object]] = []
        runs: list[dict[str, object]] = []
        for m in mirs:
            listed: list[list[str]] = []
            for r in files[m].get("LISTI", []):
                if r[5].strip().isdigit():
                    listed.append(list(r))
                else:  # a long name carried over to a record of its own (1997)
                    listed[-1][6] = listed[-1][6].strip() + r[5].strip()
            for r in listed:
                tag = order[m][int(r[8]) - 1]
                position, who = int(r[5]), " ".join(r[6].split())
                assert not r[2].strip() or int(tag.split("-")[0]) == int(r[2]), (b.id, m, r, tag)
                if not who:
                    continue
                run = f"{m}:{tag}:{position}"
                person = p.id("person", b.id, run)
                people.append({"id": person})
                named.append(
                    {
                        "id": p.id("name", person),
                        "entity_id": person,
                        "text": who,
                        "lang": "bg",
                        "kind": "official",
                        "is_preferred": True,
                    }
                )
                runs.append(
                    {
                        "id": p.id("candidacy", b.id, run),
                        "person_id": person,
                        "option_id": options[(m, tag)],
                        "role": "list_member",
                        "list_position": position,
                        "ballot_name": who,
                    }
                )
        if people:
            p.frame("person", pl.DataFrame(people))
            p.frame("name", pl.DataFrame(named))
            p.frame("candidacy", pl.DataFrame(runs))
        assert sum(seats.values()) == 240, (b.id, sum(seats.values()))
    else:
        contest = key("contest", b.id, "president")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=PRESIDENCY,
            district_area_id=NATION,
            magnitude=1,
            round_number=b.round,
            previous_round_id=key("contest", first.id, "president") if b.round == 2 and first else None,
            electoral_system_id=SYSTEM_PRES,
            default_result_kind="counted",
        )
        p.name(contest, english, "en", "official")
        p.name(contest, native, "bg", "native", False)
        p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
        for m in [0, *mirs]:
            sets[m] = key("result_set", b.id, "president")
            order[m] = [r[3].strip() for r in files[m]["MAJ"]] if m else []
        for r in nation["MAJ"]:
            n, printed = r[3].strip(), " ".join(r[6].split())
            option = options[(0, n)] = key("option", b.id, "president", n)
            for m in mirs:
                options[(m, n)] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="ticket",
                ballot_position=int(n),
                ballot_label=printed,
            )
            declared_votes[(0, n)] = int(r[9])
            for role, who in _ticket_people(printed):
                person = p.id("person", b.id, n, role)
                p.add("person", id=person)
                p.add(
                    "name",
                    id=p.id("name", person),
                    entity_id=person,
                    text=who,
                    lang="bg",
                    kind="official",
                    is_preferred=True,
                )
                p.add(
                    "candidacy",
                    id=p.id("candidacy", b.id, n, role),
                    person_id=person,
                    option_id=option,
                    role=role,
                    ballot_name=who,
                )
            result = r[10].strip()
            kind = (
                "elected"
                if result == "1"
                else "advanced"
                if result == "2"
                else "lost"
                if b.round == 2
                else "eliminated"
            )
            p.add("outcome", result_set_id=sets[0], option_id=option, kind=kind)

    years = "1991 to 1999" if b.url == ARCHIVE_1999 else "2001"
    extra = " (main and additional, with those voting on a certificate)" if len(b.points["reg"]) > 1 else ""
    method = OLD_METHOD.format(years=years, extra=extra)
    if b.kind == "ns" and b.url == ARCHIVE_1999:
        method += (
            " The files give the candidate lists of the lists that won seats in the constituency and of a few others, "
            "so the other lists are stored without their candidates."
        )
    if b.id == "ns-1991-10-13":
        method += (
            " The national file's totals for seven small lists differ from the sum of the constituency files, by "
            "1,919 votes for the Preobrazhenie political forum and by 6 or fewer for the others; the section counts "
            "here add up to the constituency files."
        )
    if b.kind == "pres" and b.url == ARCHIVE_1999:
        method += (
            " The national file prints each ticket with names often shortened to initials; a candidate is stored as a "
            "person only where the file writes the name out in full."
        )
    if b.id == "ns-1991-10-13":
        method += (
            " Invalid votes are the invalid ballots plus the empty envelopes of the protocol's inventory; the "
            "ballots destroyed under article 76 are not counted."
        )
    if b.id == "pr-1992-01-19":
        method += (
            " The files include section 32-25-003 (St. John's, Canada), whose protocol arrived too late to be counted "
            "in the official results."
        )
    notes: dict[str, str] = {}
    for m in sorted(set(sets) if b.kind == "ns" else {0}):
        rs = sets[m]
        contest = key("contest", b.id, f"{m:02d}") if b.kind == "ns" else key("contest", b.id, "president")
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CIK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        notes[rs] = method
    for (id, m, _), (_, why) in SEAT_FIXES.items():
        if id == b.id:
            notes[sets[m]] += " " + why
    for rs, body in notes.items():
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
    for (m, n), count in declared_votes.items():
        p.add("declared", result_set_id=sets[m], option_id=options[(m, n)], measure=VOTES, value=count)
    for (m, n), won in seats.items():
        p.add("outcome", result_set_id=sets[m], option_id=options[(m, n)], kind="won", seats=won)

    unit_rows: list[dict[str, object]] = []
    unit_names: list[dict[str, object]] = []
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    seen: set[str] = set()
    over = short = above = 0
    for s in sections:
        assert s.code not in seen, (b.id, s.code)
        seen.add(s.code)
        unit = p.id("unit", b.id, s.code)
        out = s.mir not in MIR
        unit_rows.append(
            {
                "id": unit,
                "polity_id": BULGARIA,
                "scheme_code": "bg.sik",
                "code": f"{b.id}:{s.code}",
                "area_relation": "unlocated" if out else "within",
                "area_id": None if out else area(s.mir),
                "kind": "abroad_mission" if out else "standard",
            }
        )
        if s.place:
            unit_names.append(
                {
                    "id": p.id("name", unit),
                    "entity_id": unit,
                    "text": f"{s.place}, {s.code}",
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
        rs = sets[s.mir]
        f = old_figures(b, s)
        for name, measure in MEASURES.items():
            if name not in f:
                continue
            if name == "reg" and not f[name] and (f.get("voters") or f.get("box")):
                continue  # a mission abroad has no roll; its voters register on the day
            cols["result_set_id"].append(rs)
            cols["unit_id"].append(unit)
            cols["option_id"].append(None)
            cols["measure"].append(measure)
            cols["value"].append(f[name])
        numbers = order[s.mir]
        assert len(s.votes) <= len(numbers), (b.id, s.code, len(s.votes), len(numbers))
        total = 0
        for n, count in zip(numbers, s.votes, strict=False):
            if count is None:
                continue
            total += count
            cols["result_set_id"].append(rs)
            cols["unit_id"].append(unit)
            cols["option_id"].append(options[(s.mir, n)])
            cols["measure"].append(VOTES)
            cols["value"].append(count)
        if "box" in f and "valid" in f and f["box"] != f.get("inv", 0) + f["valid"]:
            over += 1
        if "valid" in f and total != f["valid"]:
            short += 1
        if f.get("reg") and max(f.get("voters", 0), f.get("box", 0)) > f["reg"]:
            above += 1
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame("name", pl.DataFrame(unit_names))
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    if over:
        p.accept(
            "accounting.ballots_sum",
            f"the protocols of {over:,} sections print envelopes in the box that differ from invalid plus valid "
            "votes; stored as printed",
        )
    if short:
        p.accept(
            "accounting.votes_valid",
            f"in {short:,} sections the votes printed for the {'lists' if b.kind == 'ns' else 'tickets'} do not add "
            "up to the valid votes printed in the protocol; stored as printed",
        )
    if above:
        p.accept(
            "accounting.turnout",
            "some sections report more voters or envelopes than the roll holds, from voters added on the day "
            "or voting on a certificate; stored as printed",
        )
    return p


def old_totals(b: Blocks) -> dict[str, tuple[int, int]]:
    """Each ballot number's votes summed over the sections, against the national file's own total."""
    files = block_files(b)
    block = "PROP" if b.kind == "ns" else "MAJ"
    number = ballot if b.kind == "ns" else (lambda r: r[3].strip())
    column = 8 if b.kind == "ns" else 9
    listed = {number(r) for r in files[0][block]}
    sums: dict[str, int] = defaultdict(int)
    for s in old_sections(b):
        for r, count in zip(files[s.mir][block], s.votes, strict=False):
            sums[number(r) if number(r) in listed else INDEPENDENT] += count or 0
    return {number(r): (sums.get(number(r), 0), int(r[column])) for r in files[0][block]}


# The European Parliament election of 20 May 2007: the CIK's export of section protocols and preference counts, and
# its national results page, which names the lists, their votes, and the candidates of each list with their
# preference votes (in alphabetical order, not list order).
EP2007 = Election("ep-2007-05-20", "ep", date(2007, 5, 20), "https://ep2007.cik.bg/results/export.zip", "ep2007")
EP2007_PAGE = "https://ep2007.cik.bg/results/"
EP2007_INDEPENDENT = {3, 13}  # the two ballot numbers that the readme and the results page name as people, not lists
EP2007_POINTS = {"reg": 2, "voters": 14, "box": 15, "inv": 19, "valid": 23}
EP2007_VOTES = 24  # one-based column of the first list's votes in the protocols file
EP2007_SEATS = 18


def ep2007_page() -> tuple[dict[int, tuple[str, int]], dict[str, list[tuple[str, int]]], dict[int, int]]:
    """The lists (number -> name, votes), each list's candidates with their preference votes, and the national
    protocol points from the CIK's results page."""
    text = raw.cached(SOURCE, EP2007_PAGE).path.read_text(encoding="utf-8")
    lists = {
        int(m[1]): (html.unescape(m[2]).strip(), int(m[3]))
        for m in re.finditer(
            r'<tr><td class="int">(\d+)</td><td>([^<]*)</td><td class="int">(\d+)</td><td class="int">[\d.]+%</td>',
            text,
        )
    }
    points = {
        int(m[1]): int(m[2])
        for m in re.finditer(
            r'<tr><td class="int">(\d+)</td><td>Брой(?:[^<]|<br />)*</td><td class="int">(\d+)</td></tr>', text
        )
    }
    candidates: dict[str, list[tuple[str, int]]] = {}
    for table in re.finditer(r'<th colspan="2">([^<]*)</th>.*?</thead>\s*<tbody>(.*?)</tbody>', text, re.DOTALL):
        candidates[html.unescape(table[1]).strip()] = [
            (" ".join(html.unescape(m[1]).split()), int(m[2]))
            for m in re.finditer(r'<tr><td>([^<]*)</td><td class="int">(\d+)</td></tr>', table[2])
        ]
    return lists, candidates, points


def ep2007_members(text: str) -> list[list[str]]:
    return [line.split(";") for line in text.replace("\r", "").split("\n") if line.strip()]


def ep2007_positions(
    lists: dict[int, tuple[str, int]], candidates: dict[str, list[tuple[str, int]]], columns: list[int]
) -> dict[int, list[tuple[str, int | None]]]:
    """Each list's candidates with their place on the list where it is certain.

    The preferences file gives each candidate's count in list order, the page gives the same counts by name in
    alphabetical order. A candidate is placed when no one else on the list has the same national count."""
    out: dict[int, list[tuple[str, int | None]]] = {}
    at = 0
    for number, (name, _) in sorted(lists.items()):
        if number in EP2007_INDEPENDENT:
            continue
        people = candidates[name]
        totals = columns[at : at + len(people)]
        at += len(people)
        assert sorted(totals) == sorted(v for _, v in people), (name, totals)
        placed: list[tuple[str, int | None]] = []
        for who, count in people:
            same = [i for i, v in enumerate(totals) if v == count]
            placed.append((who, same[0] + 1 if len(same) == 1 else None))
        out[number] = placed
    assert at == len(columns), (at, len(columns))
    return out


def ep2007() -> Package:
    e = EP2007
    p = Package(
        f"{SOURCE}/{e.id}",
        SOURCE,
        "The 20 May 2007 Bulgarian European Parliament election by polling section from the CIK's open data",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BULGARIA)
    archive = _archive(e.url)
    p.artifact(raw.cached(SOURCE, e.url), CIK_SOURCE)
    p.artifact(raw.cached(SOURCE, EP2007_PAGE), CIK_SOURCE)
    lists, candidates, points = ep2007_page()
    readme = _decode(archive.read("Readme.txt"))
    named = {
        int(m[1]): m[2]
        for m in (re.match(r"\s*\d+\)\s*Гласове за:\s*(\d+)\.\s*(.+?)\s*$", line) for line in readme.splitlines())
        if m
    }
    assert {n: x for n, (x, _) in lists.items()} == named, (lists, named)
    secs = {r[0]: r[1].strip() for r in ep2007_members(_decode(archive.read("euro2007_sections.txt")))}
    protocols = ep2007_members(_decode(archive.read("euro2007_protocols.txt")))
    preferences = ep2007_members(_decode(archive.read("euro2007_prefereces.txt")))
    width = len(preferences[0]) - 2
    columns = [sum(int(r[1 + i]) for r in preferences) for i in range(width)]
    placed = ep2007_positions(lists, candidates, columns)

    english, native = label(e, e.day)
    election = key("election", e.id)
    p.add("election", id=election, polity_id=BULGARIA, kind="general", date_from=e.day, date_to=e.day, status="held")
    p.name(election, english, "en", "official")
    p.name(election, native, "bg", "native", False)
    path = f"bg/{e.day}-european-parliament"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", e.id, "national")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        district_area_id=NATION,
        magnitude=EP2007_SEATS,
        round_number=1,
        electoral_system_id=SYSTEM_EP,
        default_result_kind="counted",
    )
    p.name(contest, english.replace("Bulgarian ", "Bulgaria's "), "en", "official")
    p.name(contest, native, "bg", "native", False)
    p.add("slug", path=f"{path}/national", entity_id=contest, is_current=True)
    result_set = key("result_set", e.id, "national")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CIK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=(
            "Counted by the polling section commissions and published by the Central Election Commission (CIK) as "
            "section protocols in its open data. Registered voters are the voters on the main and additional rolls, "
            "voters are the signatures on the rolls, ballots cast are the envelopes found in the box, and invalid and "
            "valid votes are the protocol's own counts. The names of the lists and candidates come from the CIK's "
            "national results page, which lists each list's candidates alphabetically; a candidate's place on the "
            "list is taken from the order of the section preference counts where the candidate's national preference "
            "total is unique on the list, and is left blank otherwise. Preference votes are not stored here yet."
        ),
        lang="en",
    )
    options: dict[int, str] = {}
    for number, (name, votes) in sorted(lists.items()):
        option = options[number] = key("option", e.id, "national", number)
        if number in EP2007_INDEPENDENT:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=number,
                ballot_label=name,
            )
            person = p.id("person", e.id, number)
            p.add("person", id=person)
            p.name(person, name, "bg", "official")
            p.add(
                "candidacy",
                id=p.id("candidacy", e.id, number),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
            )
        else:
            party = p.id("party", e.id, number)
            p.add("party", id=party, kind=party_kind(name), polity_id=BULGARIA)
            p.add("external_id", entity_id=party, scheme="bg.cik.party", value=f"{e.id}:{number}")
            p.name(party, name, "bg", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=number,
                ballot_label=name,
            )
            for who, position in placed[number]:
                tag = f"{number}:{who}"
                person = p.id("person", e.id, tag)
                p.add("person", id=person)
                p.name(person, who, "bg", "official")
                p.add(
                    "candidacy",
                    id=p.id("candidacy", e.id, tag),
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=position,
                    ballot_name=who,
                )
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    for measure, point in ((REGISTERED, 3), (VOTERS, 7), (BALLOTS, 8), (INVALID, 12), (VALID, 16)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=points[point])

    unit_rows: list[dict[str, object]] = []
    unit_names: list[dict[str, object]] = []
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    over = short = above = 0
    for r in protocols:
        code = r[0]
        m = int(code[:2])
        out = m not in MIR
        unit = p.id("unit", e.id, code)
        unit_rows.append(
            {
                "id": unit,
                "polity_id": BULGARIA,
                "scheme_code": "bg.sik",
                "code": f"{e.id}:{code}",
                "area_relation": "unlocated" if out else "within",
                "area_id": None if out else area(m),
                "kind": "abroad_mission" if out else "standard",
            }
        )
        if secs.get(code):
            unit_names.append(
                {
                    "id": p.id("name", unit),
                    "entity_id": unit,
                    "text": f"{secs[code]}, {code}",
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
        f = {name: num(r, column) for name, column in EP2007_POINTS.items()}
        for name, measure in MEASURES.items():
            value = f.get(name)
            if value is None or (name == "reg" and not value and (f.get("voters") or f.get("box"))):
                continue
            cols["result_set_id"].append(result_set)
            cols["unit_id"].append(unit)
            cols["option_id"].append(None)
            cols["measure"].append(measure)
            cols["value"].append(value)
        total = 0
        for number in sorted(lists):
            count = num(r, EP2007_VOTES + number - 1)
            if count is None:
                continue
            total += count
            cols["result_set_id"].append(result_set)
            cols["unit_id"].append(unit)
            cols["option_id"].append(options[number])
            cols["measure"].append(VOTES)
            cols["value"].append(count)
        box, inv, valid, reg, voters = (f.get(x) for x in ("box", "inv", "valid", "reg", "voters"))
        if box is not None and valid is not None and box != (inv or 0) + valid:
            over += 1
        if valid is not None and total != valid:
            short += 1
        if reg and max(voters or 0, box or 0) > reg:
            above += 1
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame("name", pl.DataFrame(unit_names))
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    if over:
        p.accept(
            "accounting.ballots_sum",
            f"the protocols of {over:,} sections print envelopes in the box that differ from invalid plus valid "
            "votes; stored as printed",
        )
    if short:
        p.accept(
            "accounting.votes_valid",
            f"in {short:,} sections the votes printed for the lists do not add up to the valid votes printed in the "
            "protocol; stored as printed",
        )
    if above:
        p.accept(
            "accounting.turnout",
            "some sections report more voters or envelopes than the roll holds, from voters added on the day "
            "or voting on a certificate; stored as printed",
        )
    return p


EARLY = [EP2007.id, *(b.id for b in BLOCKS)]


def build_early(id: str) -> Package:
    return ep2007() if id == EP2007.id else old_build(next(b for b in BLOCKS if b.id == id))


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{x}" for x in [*(e.id for e in ELECTIONS), RECOUNT_ID, *EARLY]),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        build(e).write()
    recount().write()
    for id in EARLY:
        build_early(id).write()


def emit() -> None:
    package.run("load", packages())
