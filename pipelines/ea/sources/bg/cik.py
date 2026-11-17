"""Bulgaria's Central Election Commission (CIK): every election it publishes, by polling section.

The CIK publishes, for each election since 2013, a bundle of semicolon-separated text files (`sections`, `protocols`,
`votes`, `preferences`, party and candidate lists) that it calls open data. Older elections (the 2005 National
Assembly and the 2011 presidential rounds) come as differently shaped text files. This module reads each bundle from the raw cache and builds one package per election.
"""

import functools
import html
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
    Election("pr-2006-10-22", "pres", date(2006, 10, 22), "https://pvr2006.cik.bg/results_1/", "pres2006", None, 1),
    Election("pr-2006-10-29", "pres", date(2006, 10, 29), "https://pvr2006.cik.bg/results_2/", "pres2006", None, 2),
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


def url_of(name: str) -> str:
    return f"{BASE}/{name}"


def fetch() -> None:
    for url in dict.fromkeys([*(e.url for e in ELECTIONS), LISTS_2009]):
        try:
            raw.fetch(SOURCE, url, suffix=".zip" if url.endswith(".zip") else ".html")
        except httpx.HTTPError as failure:
            print(f"{url}: {failure}", flush=True)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CIK_SOURCE = key("source", SOURCE)
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
        elif e.layout in ("ep2009", "ns2009"):
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
                figures.setdefault(name, found[0])
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


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bulgaria's country area, its 31 multi-member constituencies, and the scheme of polling sections",
    )
    p.external.add(BULGARIA)
    for code, rank, kind, noun in (
        ("bg.country", 0, "administrative", "country"),
        ("bg.mir", 1, "electoral_district", "multi-member constituency"),
        ("bg.sik", 2, "precinct", "polling section"),
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
    return p


def mir_of(e: Election, code: str) -> int:
    if e.kind in ("ep", "pres") or e.layout in ("ref2013", "ref2015", "ref2016"):
        return 0
    return int(code[:2])


def abroad(e: Election, code: str) -> bool:
    return code.startswith("29" if e.layout in ("ref2015", "pres2011") else "32")


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


def pres2006(e: Election) -> Package:
    """A 2006 presidential round: the national totals of the CIK's results page, as one aggregate unit."""
    first = FIRST_ROUND.get(e.id)
    p = Package(
        f"{SOURCE}/{e.id}",
        SOURCE,
        f"The {e.day:%d %B %Y} Bulgarian presidential election, national totals from the CIK's results page",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/{first}"] if first else [])],
    )
    p.external.add(BULGARIA)
    p.artifact(raw.cached(SOURCE, e.url), CIK_SOURCE)
    tickets, points = pres2006_page(e.url)
    assert tickets and sum(t.votes for t in tickets) > 0, e.url
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
            "The CIK's results page for the whole country, which counts the protocols of all 11,809 polling sections. "
            "Registered voters are the voters on the main and additional rolls, voters are the signatures on the roll, "
            "ballots cast are the ballots found in the boxes, and valid ballots are those marked for one ticket. The "
            "results for each constituency and section are not stored here yet."
        ),
        lang="en",
    )
    finalists = {t.number for t in pres2006_page(SECOND_URL)[0]}
    unit = p.id("unit", e.id, "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=BULGARIA,
        scheme_code="bg.country",
        code=f"{e.id}:national",
        area_relation="equals",
        area_id=NATION,
        kind="aggregate",
    )
    p.name(unit, "Bulgaria, all sections", "en", "official")
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}

    def put(option: str | None, measure: int, value: int) -> None:
        cols["result_set_id"].append(result_set)
        cols["unit_id"].append(unit)
        cols["option_id"].append(option)
        cols["measure"].append(measure)
        cols["value"].append(value)

    put(None, REGISTERED, points[3] + points[4])
    put(None, VOTERS, points[5])
    put(None, BALLOTS, points[8])
    put(None, INVALID, points[13])
    put(None, VALID, points[14])
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
        put(option, VOTES, t.votes)
        if e.round == 2:
            kind = "elected" if t.votes == max(x.votes for x in tickets) else "lost"
        else:
            kind = "advanced" if t.number in finalists else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    if points[8] != points[13] + points[14]:
        p.accept(
            "accounting.ballots_sum",
            "the national protocol totals print ballots in the box that differ from invalid plus valid ballots; "
            "stored as printed",
        )
    if sum(t.votes for t in tickets) != points[14]:
        p.accept(
            "accounting.votes_valid",
            "the votes printed for the tickets do not add up to the valid votes printed in the national totals; "
            "stored as printed",
        )
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


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.id}" for e in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        build(e).write()


def emit() -> None:
    package.run("load", packages())
