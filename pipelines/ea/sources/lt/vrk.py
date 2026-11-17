"""Lithuanian elections and referendums since 2017 by polling district (apylinkė) from the Central Electoral Commission.

Each election date is one package, `lt.vrk/<date>`, built on `lt.vrk/geo` (municipalities and the Seimas single-member
constituencies) and `lt.vrk/registry` (parties, bodies, offices, and electoral systems). The Commission's results
site publishes one tab-separated file per kind of result and round: candidate votes (`VN`), list votes (`DG`), and
answers to a referendum question (`VNREF`), each with one row per polling district and option. The second round
of an election is its own election, a week or two later, in the same package as the first.
"""

import functools
import io
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import NamedTuple
from uuid import uuid5
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.lt import summary

SOURCE = "lt.vrk"
SITE = "https://www.vrk.lt"
BASE = f"{SITE}/statiniai/puslapiai"
LITHUANIA = str(uuid5(package.NAMESPACE, "polity\x1fLT"))
TERRITORY = "a7f8801f-6490-5c0f-9839-91e16d1df8f0"
VILNIUS = ZoneInfo("Europe/Vilnius")
NUMBERS = ("RINKEJU_SKAICIUS", "VISO_DALYVAVO", "BALSADEZEJE_GALIOJANTYS", "BALSADEZEJE_NEGALIOJANTYS")
POSTAL = ("PASTU_GALIOJANTYS", "PASTU_NEGALIOJANTYS")
ABROAD = ("ambasad", "konsulat", "atstovyb", "pasiuntin")

METHOD = (
    "Counted by the polling district commissions (apylinkių rinkimų komisijos) and published by the Central "
    "Electoral Commission per polling district. Registered voters are the electoral roll, ballots cast are the "
    "voters who took part, and valid and invalid ballots are the ballots found valid or invalid. The source "
    "splits each count between the ballot box and ballots it labels postal (pašto); the two are added here. A "
    "polling district that filed a supplementary protocol has its votes and ballots added to its main protocol; "
    "the roll and turnout come from the main protocol. Polling stations abroad, at embassies and consulates, have "
    "no area."
)


class Part(NamedTuple):
    family: str  # parliamentary, municipal, presidential, referendum, european
    kind: str  # smd, mayor, council, president, list, ep, referendum
    prefix: str  # VN, DG, or VNREF
    files: frozenset[int] | None = None  # the Commission's file numbers, when a home page holds several
    label: str = ""  # a referendum's question


@dataclass(frozen=True)
class Config:
    id: int
    first: date
    second: date | None
    parts: tuple[Part, ...]
    en: dict[str, str]
    by: bool = False  # a new or early election, not a regular one
    index: str = "index"  # the name of the home page
    folder: str = "home"  # the folder of the home page
    first_round: int = 1  # the Commission's round number of the first date
    scope: str = ""  # tells a day's polling districts apart from those of another package on the same day


def part(family: str, kind: str, prefix: str, *files: int, label: str = "") -> Part:
    return Part(family, kind, prefix, frozenset(files) if files else None, label)


ELECTIONS = (
    Config(
        748,
        date(2017, 4, 23),
        date(2017, 5, 7),
        (part("municipal", "mayor", "VN", 1350, 1348), part("parliamentary", "smd", "VN", 1386, 1384)),
        {
            "municipal": "April 2017 new mayoral elections in Jonava and Šakiai district municipalities",
            "parliamentary": "April 2017 new Seimas election in the Anykščiai-Panevėžys constituency",
        },
        True,
    ),
    Config(
        784,
        date(2017, 9, 10),
        date(2017, 9, 24),
        (part("municipal", "mayor", "VN"),),
        {"municipal": "September 2017 new mayoral election in Marijampolė municipality"},
        True,
    ),
    Config(
        826,
        date(2018, 9, 16),
        date(2018, 9, 30),
        (part("parliamentary", "smd", "VN"),),
        {"parliamentary": "September 2018 new Seimas election in the Zanavykai constituency"},
        True,
    ),
    Config(
        864,
        date(2019, 3, 3),
        date(2019, 3, 17),
        (part("municipal", "mayor", "VN"), part("municipal", "council", "DG")),
        {"municipal": "2019 Lithuanian municipal elections"},
    ),
    Config(
        904,
        date(2019, 5, 12),
        date(2019, 5, 26),
        (
            part("presidential", "president", "VN"),
            part(
                "referendum",
                "referendum",
                "VNREF",
                1664,
                label="Referendum on amending Article 12 of the Constitution (dual citizenship)",
            ),
            part(
                "referendum",
                "referendum",
                "VNREF",
                1666,
                label="Referendum on amending Article 55 of the Constitution (number of Seimas members)",
            ),
        ),
        {
            "presidential": "2019 Lithuanian presidential election",
            "referendum": "2019 Lithuanian constitutional referendums",
        },
        index="indexPreRef",
    ),
    Config(
        904,
        date(2019, 5, 26),
        None,
        (part("european", "ep", "DG"),),
        {"european": "2019 European Parliament election in Lithuania"},
        index="indexEp",
        folder="homeEp",
        first_round=2,
        scope="ep",
    ),
    Config(
        1066,
        date(2019, 9, 8),
        date(2019, 9, 22),
        (part("parliamentary", "smd", "VN"),),
        {"parliamentary": "September 2019 new Seimas elections in the Žirmūnai, Gargždai and Žiemgala constituencies"},
        True,
    ),
    Config(
        1104,
        date(2020, 10, 11),
        date(2020, 10, 25),
        (part("parliamentary", "smd", "VN"), part("parliamentary", "list", "DG")),
        {"parliamentary": "2020 Lithuanian parliamentary election"},
    ),
    Config(
        1144,
        date(2021, 4, 11),
        date(2021, 4, 25),
        (part("municipal", "mayor", "VN"),),
        {"municipal": "April 2021 new mayoral election in Radviliškis district municipality"},
        True,
    ),
    Config(
        1184,
        date(2021, 10, 10),
        date(2021, 10, 24),
        (part("municipal", "mayor", "VN"),),
        {"municipal": "October 2021 new mayoral elections in Kelmė and Trakai district municipalities"},
        True,
    ),
    Config(
        1304,
        date(2023, 3, 5),
        date(2023, 3, 19),
        (part("municipal", "mayor", "VN"), part("municipal", "council", "DG")),
        {"municipal": "2023 Lithuanian municipal elections"},
    ),
    Config(
        1344,
        date(2023, 5, 7),
        None,
        (part("municipal", "mayor", "VN"),),
        {"municipal": "May 2023 repeat mayoral vote in Visaginas municipality"},
        True,
    ),
    Config(
        1384,
        date(2023, 9, 3),
        date(2023, 9, 17),
        (part("parliamentary", "smd", "VN"),),
        {"parliamentary": "September 2023 early Seimas election in the Raseiniai-Kėdainiai constituency"},
        True,
    ),
    Config(
        1426,
        date(2023, 10, 8),
        date(2023, 10, 22),
        (part("municipal", "mayor", "VN"),),
        {"municipal": "October 2023 early mayoral election in Kupiškis district municipality"},
        True,
    ),
    Config(
        1504,
        date(2024, 5, 12),
        date(2024, 5, 26),
        (
            part("presidential", "president", "VN"),
            part(
                "referendum",
                "referendum",
                "VNREF",
                label="Referendum on amending Article 12 of the Constitution (dual citizenship)",
            ),
        ),
        {
            "presidential": "2024 Lithuanian presidential election",
            "referendum": "2024 Lithuanian constitutional referendum",
        },
    ),
    Config(
        1546,
        date(2024, 6, 9),
        None,
        (part("european", "ep", "DG"),),
        {"european": "2024 European Parliament election in Lithuania"},
    ),
    Config(
        1544,
        date(2024, 10, 13),
        date(2024, 10, 27),
        (part("parliamentary", "smd", "VN"), part("parliamentary", "list", "DG")),
        {"parliamentary": "2024 Lithuanian parliamentary election"},
    ),
    Config(
        1586,
        date(2025, 3, 16),
        date(2025, 3, 30),
        (part("municipal", "mayor", "VN"),),
        {"municipal": "March 2025 early mayoral elections in Joniškis district and Panevėžys city municipalities"},
        True,
    ),
    Config(
        1666,
        date(2026, 11, 8),
        None,
        (part("parliamentary", "smd", "VN"),),
        {"parliamentary": "November 2026 early Seimas election in the Nalšia North constituency"},
        True,
    ),
)
FAMILIES = {
    "parliamentary": ("parliamentary", "Lietuvos Respublikos Seimo rinkimai", "Seimas election"),
    "municipal": ("municipal", "savivaldybių rinkimai", "municipal election"),
    "presidential": ("presidential", "Respublikos Prezidento rinkimai", "presidential election"),
    "referendum": ("referendum", "referendumas", "referendum"),
    "european": ("european", "rinkimai į Europos Parlamentą", "European Parliament election"),
}
VINTAGES = (2016, 2020, 2024)
MONTHS = {
    "sausio": 1,
    "vasario": 2,
    "kovo": 3,
    "balandžio": 4,
    "gegužės": 5,
    "birželio": 6,
    "liepos": 7,
    "rugpjūčio": 8,
    "rugsėjo": 9,
    "spalio": 10,
    "lapkričio": 11,
    "gruodžio": 12,
}

SYSTEMS = {
    "list_seimas": (
        {
            "formula": "hare",
            "threshold": [
                {"level": "party", "scope": "national", "percent": 5},
                {"level": "coalition", "scope": "national", "percent": 7},
            ],
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "70 of the 141 Seimas seats are allocated in one national constituency among the lists that pass the "
            "threshold, 5% of the voters for a party and 7% for a coalition, by the largest remainder method with "
            "the Hare quota. Voters may also mark preferences for candidates within a list. The other 71 seats are "
            "single-member constituencies."
        ),
    ),
    "list_ep": (
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "Lithuania's seats in the European Parliament are allocated in one national constituency among the lists "
            "that pass a 5% threshold, by the largest remainder method with the Hare quota. Voters may mark "
            "preferences for candidates within a list."
        ),
    ),
    "list_council": (
        {"formula": "hare", "lists": {"order": "flexible", "vacancy": "next_on_list"}},
        (
            "Municipal council seats are allocated among the lists by the largest remainder method with the Hare "
            "quota. Voters may mark preferences for candidates within a list. The seat counts and thresholds are not "
            "recorded here."
        ),
    ),
    "two_round": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate who wins more than half of the valid votes is elected; otherwise the two leading candidates "
            "go to a second round two weeks later. This applies to the presidency, mayors, and the Seimas "
            "single-member constituencies."
        ),
    ),
    "referendum": ({"formula": "referendum"}, "A referendum question is put to all voters on the roll."),
}
SYSTEM_FOR = {
    "smd": "two_round",
    "mayor": "two_round",
    "president": "two_round",
    "list": "list_seimas",
    "ep": "list_ep",
    "council": "list_council",
    "referendum": "referendum",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


VRK_SOURCE = key("source", "results")


def home(config: Config) -> str:
    return f"{BASE}/rinkimai/{config.id}/{config.folder}/lt/{config.index}.html"


def listing(config: Config) -> list[tuple[str, int, int, str]]:
    """The result files a home page links, as (prefix, round, file number, url)."""
    html = raw.cached(SOURCE, home(config)).path.read_text(encoding="utf-8")
    found = re.findall(r'data-direct-href="(/rinkimai/\d+/(\d)/(\d+)/rezultatai/([A-Z]+)_\d+_\d_\d+\.csv)"', html)
    return [(prefix, int(round_), int(number), f"{BASE}{path}") for path, round_, number, prefix in found]


def get(url: str, suffix: str) -> None:
    """Fetch one file into the raw cache, trying again when the connection drops; a missing file is reported."""
    for attempt in range(6):
        try:
            raw.fetch(SOURCE, url, suffix=suffix)
        except httpx.HTTPStatusError as failure:
            print(f"{url}: {failure.response.status_code}", flush=True)
            return
        except httpx.TransportError:
            time.sleep(2**attempt)
        else:
            return
    raise ConnectionError(f"{url} did not download in six tries")


def fetch() -> None:
    for config in ELECTIONS:
        get(home(config), ".html")
        for prefix, _, _, url in listing(config):
            if prefix in ("VN", "DG", "VNREF"):
                get(url, ".csv")
        for item in config.parts:
            if item.kind in PAGES:
                for url in summaries(config, item).values():
                    get(url, ".html")
        fetch_summaries(config)


# The summary pages a home page links, by what each one declares.
SUMMARY_PAGES = {
    "constituencies": r"rezultataiVienm\.html",
    "members": r"rezultataiIsrinktiNariai\.html",
    "ep_members": r"rezultataiEpIsrinktiNariai\.html",
    "municipalities": r"rezultatai(?:Sav)?TarNariaiMeraiSavivaldybese\.html",
}


def linked(config: Config, kind: str) -> list[str]:
    """The paths of the home page's summary pages of one kind, in the order the page links them."""
    html = raw.cached(SOURCE, home(config)).path.read_text(encoding="utf-8")
    return [
        path
        for path in summary.links(html)
        if path.startswith(f"/rinkimai/{config.id}/") and re.search(SUMMARY_PAGES[kind] + "$", path)
    ]


def places(path: str) -> list[tuple[str, str]]:
    """The municipality pages a listing links, as (municipality number, path)."""
    html = summary.cached(path)
    if html is None:
        return []
    found = []
    for found_table in summary.tables(html):
        for row in found_table:
            code = summary.code_of(row[0].text)
            if code and row[0].href and "Savivaldybeje_rpgId" in row[0].href:
                found.append((code, row[0].href))
    return list(dict.fromkeys(found))


def fetch_summaries(config: Config) -> None:
    """Download the summary pages the home page links, and the pages those link to for each contest."""
    kinds = {item.kind for item in config.parts}
    for kind in ("constituencies", "members", "ep_members", "municipalities"):
        for path in linked(config, kind):
            if kind == "constituencies" and "smd" not in kinds:
                continue
            if kind == "members" and "list" not in kinds:
                continue
            if kind == "ep_members" and "ep" not in kinds:
                continue
            if kind == "municipalities" and not kinds & {"mayor", "council"}:
                continue
            get(summary.url(path), ".html")
            html = summary.cached(path)
            if html is None:
                continue
            if kind == "constituencies":
                for found in summary.links(html):
                    if found.endswith("rezultataiVienmIsrinkti.html"):
                        get(summary.url(found), ".html")
            if kind == "municipalities":
                for _, found in places(path):
                    get(summary.url(found), ".html")


# The summary page whose national figures each national contest is compared with.
PAGES = {
    "list": "rezultataiDaugmVrt",
    "ep": "rezultataiEpDaugmVrt",
    "president": "rezultataiPreLietuvoje",
    "referendum": "rezultataiRefSavivaldybese",
}


def selected(config: Config, item: Part) -> list[tuple[int, int, str]]:
    return [
        (round_, number, url)
        for prefix, round_, number, url in listing(config)
        if prefix == item.prefix and (item.files is None or number in item.files)
    ]


def summaries(config: Config, item: Part) -> dict[int, str]:
    found = {}
    for round_, _, url in selected(config, item):
        found[round_] = url.rsplit("/rezultatai/", 1)[0] + f"/rezultatai/lt/{PAGES[item.kind]}.html"
    return found


def read(url: str) -> pl.DataFrame:
    data = raw.cached(SOURCE, url).path.read_bytes()
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig")
    return pl.read_csv(io.StringIO(text), separator="\t", infer_schema_length=0)


def stamp(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=VILNIUS).astimezone(UTC)


def count(series: pl.Series) -> pl.Series:
    return series.str.strip_chars().cast(pl.Int64, strict=False)


class Frame(NamedTuple):
    rows: pl.DataFrame
    stamp: datetime | None


def blank(frame: pl.DataFrame, *columns: str) -> pl.DataFrame:
    for column in columns:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, pl.String).alias(column))
    return frame


def normalize(url: str, prefix: str) -> Frame:
    """One row per polling district, protocol, and option, with the same columns for every kind of file."""
    frame = blank(read(url), "SAV", "PAPILDOMO_PROTOKOLO_POZYMIS", "RKND_V_AR_ISSIKELE_PATS")
    frame = blank(frame, "Sugeneravimo data")
    base = pl.DataFrame(
        {
            "ap": frame["APYGARDOS_NR"].str.strip_chars(),
            "ap_name": frame["APYGARDOS_PAVADINIMAS"].str.strip_chars(),
            "number": frame["APYLINKES_NR"].str.strip_chars(),
            "name": frame["APYLINKES_PAVADINIMAS"].str.strip_chars(),
            "sav": frame["SAV"].str.strip_chars().replace("", None),
            "supplementary": frame["PAPILDOMO_PROTOKOLO_POZYMIS"].fill_null("").str.strip_chars() == "T",
            "self_nominated": frame["RKND_V_AR_ISSIKELE_PATS"].fill_null("").str.strip_chars() == "T",
            "generated": frame["Sugeneravimo data"],
            "registered": count(frame["RINKEJU_SKAICIUS"]),
            "cast": count(frame["VISO_DALYVAVO"]),
            "box_valid": count(frame["BALSADEZEJE_GALIOJANTYS"]),
            "box_invalid": count(frame["BALSADEZEJE_NEGALIOJANTYS"]),
            "post_valid": count(frame["PASTU_GALIOJANTYS"]),
            "post_invalid": count(frame["PASTU_NEGALIOJANTYS"]),
        }
    )
    nothing = pl.lit(None, pl.String)
    if prefix == "VN":
        party_name = frame["PARTIJA"].fill_null("").str.strip_chars()
        rows = base.with_columns(
            option=pl.Series(
                [f"{a}|{b}|{c}" for a, b, c in zip(frame["VARDAS"], frame["PAVARDE"], party_name, strict=True)]
            ),
            first=frame["VARDAS"].str.strip_chars(),
            last=frame["PAVARDE"].str.strip_chars(),
            party=party_name.replace("", None),
            label=nothing,
            number_on_list=pl.lit(None, pl.Int64),
            box=count(frame["BALSU_BALSADEZEJE"]),
            post=count(frame["BALSU_PASTU"]),
        )
    elif prefix == "DG":
        names = frame["SARASO_PAVADINIMAS"].str.strip_chars()
        numbers = frame["SARASO_NUMERIS"].str.strip_chars()
        rows = base.with_columns(
            option=numbers,
            first=nothing,
            last=nothing,
            party=names,
            label=names,
            number_on_list=numbers.cast(pl.Int64),
            box=count(frame["BALSU_BALSADEZEJE"]),
            post=count(frame["BALSU_PASTU"]),
        )
    else:
        # A referendum file has its answers in columns, and the Commission repeats a column name in one file, so
        # they are read by position: box yes, box no, postal yes, postal no, then the totals.
        answers = [frame[column] for column in frame.columns[10:14]]
        rows = pl.concat(
            [
                base.with_columns(
                    option=pl.lit(answer),
                    first=nothing,
                    last=nothing,
                    party=nothing,
                    label=pl.lit(answer),
                    number_on_list=pl.lit(None, pl.Int64),
                    box=count(box),
                    post=count(post),
                )
                for answer, box, post in (("Yes", answers[0], answers[2]), ("No", answers[1], answers[3]))
            ]
        )
    found = [value for value in map(stamp, rows["generated"].drop_nulls().unique().to_list()) if value is not None]
    return Frame(rows.drop("generated"), max(found) if found else None)


def title(text: str) -> str:
    return " ".join("-".join(word.capitalize() for word in piece.split("-")) for piece in text.split())


def packages() -> list[str]:
    return ["lt.vrk/geo", "lt.vrk/registry", *(f"lt.vrk/{config.first}" for config in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for config in ELECTIONS:
        build(config).write()


def emit() -> None:
    package.run("load", packages())


LOADED: dict[tuple[date, Part], dict[int, list[Frame]]] = {}


def files(config: Config, item: Part) -> dict[int, list[Frame]]:
    """The cached files of one part by round."""
    if (config.first, item) not in LOADED:
        cached = raw.keys(SOURCE)
        found: dict[int, list[Frame]] = {}
        for round_, _, url in selected(config, item):
            if url in cached:
                found.setdefault(round_, []).append(normalize(url, item.prefix))
        LOADED[(config.first, item)] = found
    return LOADED[(config.first, item)]


def vintage(day: date) -> int:
    return max(year for year in VINTAGES if year <= day.year)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def abroad(name: str, sav: str | None) -> bool:
    return sav is None and any(word in name.lower() for word in ABROAD)


@functools.cache
def everything() -> list[tuple[Config, Part, int, pl.DataFrame]]:
    return [
        (config, item, round_, frame.rows)
        for config in ELECTIONS
        for item in config.parts
        for round_, frames in files(config, item).items()
        for frame in frames
    ]


SOURCE_ROW = {
    "id": VRK_SOURCE,
    "kind": "official_body",
    "publisher": "Vyriausioji rinkimų komisija",
    "title": "Lietuvos Respublikos vyriausioji rinkimų komisija: rinkimų rezultatai",
    "url": f"{SITE}/rinkimai",
    "license": "Public record",
    "attribution": "Vyriausioji rinkimų komisija",
}


def geo() -> Package:
    p = Package(
        "lt.vrk/geo",
        SOURCE,
        "Lithuania's municipalities, polling district scheme, and Seimas single-member constituencies, as the "
        "Central Electoral Commission names them in its results",
    )
    p.external |= {LITHUANIA, TERRITORY}
    p.add("scheme", code="lt.sav", polity_id=LITHUANIA, kind="administrative", rank=1, noun="municipality")
    p.add("scheme", code="lt.apylinke", polity_id=LITHUANIA, kind="precinct", rank=3, noun="polling district")
    p.add(
        "scheme",
        code="lt.seimas",
        polity_id=LITHUANIA,
        kind="electoral_district",
        rank=2,
        noun="Seimas single-member constituency",
    )
    municipalities: set[str] = set()
    constituencies: dict[str, str] = {}
    for config, item, _, rows in everything():
        municipalities |= set(rows["sav"].drop_nulls().unique())
        if item.kind == "smd":
            for number, name in rows.select("ap", "ap_name").unique().iter_rows():
                constituencies[f"{vintage(config.first)}:{number}"] = name
    for name in sorted(municipalities):
        id = area("lt.sav", name)
        p.add("area", id=id, scheme_code="lt.sav")
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, name, "lt", "official")
    for code, name in sorted(
        constituencies.items(), key=lambda pair: (pair[0].split(":")[0], int(pair[0].split(":")[1]))
    ):
        year, number = code.split(":")
        id = area("lt.seimas", code)
        p.add("area", id=id, scheme_code="lt.seimas")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, f"{name} (Nr. {number}), {year}", "lt", "official")
    return p


def normal(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\s*[-–—]\s*", "-", name)).strip().casefold()


def party(name: str) -> str:
    return key("party", normal(name))


@functools.cache
def lists() -> dict[str, str]:
    """Every party, coalition, or committee a list or candidate names, by its normalized name."""
    names: dict[str, str] = {}
    for _, _, _, rows in everything():
        for name in rows["party"].drop_nulls().unique().sort().to_list():
            names.setdefault(normal(name), name)
    return names


def registry() -> Package:
    p = Package(
        "lt.vrk/registry",
        SOURCE,
        "Lithuanian parties, the Seimas, municipal councils, mayors, the presidency, and their electoral systems",
        depends=["lt.vrk/geo"],
    )
    p.external |= {LITHUANIA, TERRITORY}
    p.add("source", **SOURCE_ROW)
    seimas = key("body", "seimas")
    p.add("body", id=seimas, polity_id=LITHUANIA, kind="unicameral")
    p.name(seimas, "Seimas of the Republic of Lithuania", "en", "official")
    p.name(seimas, "Lietuvos Respublikos Seimas", "lt", "native", False)
    presidency = key("office", "president")
    p.add("office", id=presidency, polity_id=LITHUANIA, kind="head_of_state")
    p.name(presidency, "President of the Republic of Lithuania", "en", "official")
    p.name(presidency, "Lietuvos Respublikos Prezidentas", "lt", "native", False)
    municipalities = sorted({name for _, _, _, rows in everything() for name in rows["sav"].drop_nulls().unique()})
    for name in municipalities:
        genitive = name.removesuffix("savivaldybė") + "savivaldybės"
        council = key("body", "council", name)
        p.add("body", id=council, polity_id=LITHUANIA, kind="council")
        p.name(council, f"Council of {name}", "en", "official")
        p.name(council, f"{genitive} taryba", "lt", "native", False)
        mayor = key("office", "mayor", name)
        p.add("office", id=mayor, polity_id=LITHUANIA, kind="mayor")
        p.name(mayor, f"Mayor of {name}", "en", "official")
        p.name(mayor, f"{genitive} meras", "lt", "native", False)
    for code, (rules, method) in SYSTEMS.items():
        id = key("electoral_system", code)
        p.add(
            "electoral_system",
            id=id,
            polity_id=LITHUANIA,
            valid_from=date(2015, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for normalized, name in sorted(lists().items()):
        id = party(name)
        p.add(
            "party",
            id=id,
            kind="bloc" if "koalicij" in normalized else "list_vehicle" if "komitet" in normalized else "party",
            polity_id=LITHUANIA,
        )
        p.add("external_id", entity_id=id, scheme="lt.vrk.party", value=name)
        p.name(id, name, "lt", "official")
    return p


class Names(NamedTuple):
    en: str
    lt: str


def contest_names(item: Part, number: str, name: str, sav: str | None, day: date) -> Names:
    place = (sav or name).removesuffix("savivaldybė")
    if item.kind == "smd":
        return Names(f"Constituency {number}, {name}", f"{name} vienmandatė apygarda (Nr. {number})")
    if item.kind == "mayor":
        return Names(f"Mayor of {sav or name}", f"{place}savivaldybės meras")
    if item.kind == "council":
        return Names(f"Council of {sav or name}", f"{place}savivaldybės taryba")
    if item.kind == "president":
        return Names(f"{day.year} presidential election", f"{day.year} m. Respublikos Prezidento rinkimai")
    if item.kind == "referendum":
        return Names(item.label, item.label)
    if item.kind == "ep":
        return Names("Lithuania's seats in the European Parliament", "Lietuvos vietos Europos Parlamente")
    return Names("Seimas list seats", "Seimo daugiamandatė apygarda")


class State:
    def __init__(self) -> None:
        self.elections: dict[tuple[str, int], str] = {}
        self.units: set[str] = set()
        self.previous: dict[tuple[str, str], str] = {}
        self.people: set[str] = set()
        self.paths: dict[str, str] = {}
        self.gaps = 0
        self.over = 0
        self.over_abroad = 0
        self.gap_net = 0
        self.held: list[tuple[int, int, str]] = []
        self.cited: set[str] = set()
        self.differ: list[tuple[str, str | None, int, int, int]] = []


def build(config: Config) -> Package:
    p = Package(
        f"lt.vrk/{config.first}",
        SOURCE,
        f"{', '.join(dict.fromkeys(config.en.values()))}, by polling district, from the Central Electoral Commission",
        depends=["lt.vrk/geo", "lt.vrk/registry"],
    )
    p.external |= {LITHUANIA, TERRITORY}
    p.artifact(raw.cached(SOURCE, home(config)), VRK_SOURCE)
    dates = {config.first_round: config.first}
    if config.second is not None and config.first_round == 1:
        dates[2] = config.second
    state = State()
    ordered = sorted(config.parts, key=lambda item: item.kind == "referendum")
    for item in ordered:
        byround = files(config, item)
        for round_ in sorted(byround):
            day = dates.get(round_)
            if day is None:
                continue
            frames = byround[round_]
            rows = pl.concat([f.rows for f in frames])
            as_of = max((f.stamp for f in frames if f.stamp is not None), default=None)
            for number_round, _, url in selected(config, item):
                if number_round == round_ and url in raw.keys(SOURCE):
                    p.artifact(raw.cached(SOURCE, url), VRK_SOURCE)
            page_url = summaries(config, item).get(round_) if item.kind in PAGES else None
            page = None
            if page_url is not None and page_url in raw.keys(SOURCE):
                page = raw.cached(SOURCE, page_url)
                p.artifact(page, VRK_SOURCE)
            facts = collect(config, item, round_)
            for address in dict.fromkeys(facts.urls):
                if address not in state.cited:
                    state.cited.add(address)
                    p.artifact(raw.cached(SOURCE, address), VRK_SOURCE)
            number = 1 if day == config.first else 2
            election = state.elections.get((item.family, number))
            if election is None:
                election = state.elections[(item.family, number)] = elect(p, state, config, item, number, day)
            contests(
                p,
                state,
                config,
                item,
                number,
                day,
                election,
                rows,
                as_of,
                page.path.read_text() if page else None,
                facts,
            )
    if state.gaps:
        p.accept(
            "accounting.ballots_sum",
            f"{state.gaps:,} polling-district counts have a ballots-cast figure that differs from their valid plus "
            f"invalid ballots (net {state.gap_net:+,} over the package): the Commission's own protocol figures, "
            "which the national totals it publishes are built from.",
        )
    if state.over:
        p.accept(
            "accounting.turnout",
            f"{state.over:,} polling-district counts ({state.over_abroad:,} of them at polling stations abroad) report "
            "more ballots cast than registered voters. They are the Commission's own protocol figures, kept as "
            "published.",
        )
    reasons = []
    if state.held:
        names = {
            "president": "presidential election",
            "referendum": "referendum",
            "list": "Seimas list vote",
            "ep": "European Parliament election",
            "smd": "single-member constituencies",
            "mayor": "mayoral elections",
            "council": "council elections",
        }
        kinds = " and ".join(names[kind] for kind in dict.fromkeys(kind for _, _, kind in state.held))
        reasons.append(
            f"The Commission's summary pages for the {kinds} leave out the "
            f"supplementary protocols that its result files carry ({sum(n for n, _, _ in state.held):,} protocols "
            f"with {sum(v for _, v, _ in state.held):,} valid ballots). Every figure the pages print agrees with the "
            "files to the vote when those protocols are left out."
        )
    if state.differ:
        raise ValueError(f"declared figures differ from the files in {sorted({w for w, *_ in state.differ})}")
    if reasons:
        p.accept("accounting.declared", " ".join(reasons))
    return p


def elect(p: Package, state: State, config: Config, item: Part, round_: int, day: date) -> str:
    slug, native, english = FAMILIES[item.family]
    id = key("election", day, item.family)
    if item.family == "referendum":
        kind = "referendum"
    elif round_ == 2:
        kind = "runoff"
    else:
        kind = "by_election" if config.by else "local" if item.family == "municipal" else "general"
    p.add("election", id=id, polity_id=LITHUANIA, kind=kind, date_from=day, date_to=day, status="held")
    en = config.en.get(item.family, f"{day.year} Lithuanian {english}")
    p.name(id, en + (", second round" if round_ == 2 else ""), "en", "official")
    p.name(id, f"{day.year} m. {native}" + (", pakartotinis balsavimas" if round_ == 2 else ""), "lt", "native", False)
    path = f"lt/{day}-{slug}" + ("-runoff" if round_ == 2 else "-by-election" if config.by else "")
    p.add("slug", path=path, entity_id=id, is_current=True)
    state.paths[id] = path
    return id


def contests(
    p: Package,
    state: State,
    config: Config,
    item: Part,
    round_: int,
    day: date,
    election: str,
    rows: pl.DataFrame,
    as_of: datetime | None,
    page: str | None,
    facts: Facts,
) -> None:
    path = state.paths[election]
    district = item.kind in ("smd", "mayor", "council")
    rows = rows.with_columns(
        group=pl.col("ap") if district else pl.lit("all"),
        unit=pl.concat_str("ap", "number", "name", separator=":"),
    )
    for group, here in rows.group_by("group", maintain_order=True):
        code = str(group[0])
        first = here.row(0, named=True)
        sav = first["sav"] if item.kind in ("mayor", "council") else None
        names = contest_names(item, code, first["ap_name"], sav, day)
        contest = key("contest", day, item.family, item.kind, item.label, code)
        if item.kind == "smd":
            place = area("lt.seimas", f"{vintage(config.first)}:{code}")
        elif sav is not None:
            place = area("lt.sav", sav)
        else:
            place = TERRITORY
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question"
            if item.kind == "referendum"
            else "list_allocation"
            if item.kind in ("list", "ep", "council")
            else "office",
            office_id=key("office", "president")
            if item.kind == "president"
            else key("office", "mayor", sav)
            if item.kind == "mayor" and sav
            else None,
            body_id=key("body", "seimas")
            if item.kind == "list"
            else key("body", "council", sav)
            if item.kind == "council" and sav
            else None,
            district_area_id=place,
            magnitude={"list": 70, "ep": 11, "council": None}.get(item.kind, 1),
            round_number=round_,
            previous_round_id=state.previous.get((item.kind + item.label, code)) if round_ == 2 else None,
            electoral_system_id=key("electoral_system", SYSTEM_FOR[item.kind]),
            default_result_kind="counted",
        )
        state.previous[(item.kind + item.label, code)] = contest
        if item.kind == "referendum":
            p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, names.en, "en", "official")
        if names.lt != names.en:
            p.name(contest, names.lt, "lt", "native", False)
        slug_text = (
            f"{item.kind} {code} {first['ap_name']}" if district else item.label[:40] if item.label else item.kind
        )
        p.add("slug", path=f"{path}/{changeset.slug(slug_text)}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VRK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
            as_of=as_of,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options = options_for(p, state, config, item, contest, here)
        ids = unit_rows(p, state, day, f"{config.scope}:" if config.scope else "", result_set, here)
        value_rows(p, result_set, here, options, ids)
        if page is not None:
            declared, seats = declare(item, here, page)
            reconcile(p, state, result_set, here, options, declared, seats, item.kind)
        if code in facts.contests:
            attach(p, state, item, result_set, here, options, code, facts.contests[code])
        if facts.members:
            seat_members(p, state, config, item, result_set, here, options, facts.members)


class Facts(NamedTuple):
    contests: dict[str, summary.Contest]  # by the contest's constituency or municipality number
    members: list[summary.Member]  # the members elected from the lists of a list contest
    urls: list[str]  # the summary pages that were read


def collect(config: Config, item: Part, round_: int) -> Facts:
    """What the summary pages declare for the contests of one part and round."""
    found: dict[str, summary.Contest] = {}
    members: list[summary.Member] = []
    urls: list[str] = []

    def page(path: str) -> str | None:
        html = summary.cached(path)
        if html is not None:
            urls.append(summary.url(path))
        return html

    if item.kind == "smd":
        for number_round, _, csv in selected(config, item):
            if number_round != round_:
                continue
            path = csv.removeprefix(BASE).rsplit("/rezultatai/", 1)[0] + "/rezultatai/lt/rezultataiVienm.html"
            html = page(path)
            if html is None:
                continue
            found = summary.constituencies(html)
            for linked_path in summary.links(html):
                if linked_path.endswith("rezultataiVienmIsrinkti.html") and (elected := page(linked_path)):
                    for member in summary.members(elected):
                        code = summary.code_of(member.area)
                        if code is not None:
                            found.setdefault(code, summary.Contest()).elected.append(member.name)
    elif item.kind in ("mayor", "council"):
        for listing_path in linked(config, "municipalities"):
            if summary.round_of(listing_path) != round_ or page(listing_path) is None:
                continue
            for code, path in places(listing_path):
                html = page(path)
                if html is None:
                    continue
                mayor, council = summary.municipality(html)
                contest = mayor if item.kind == "mayor" else council
                if contest is not None:
                    found[code] = contest
    elif item.kind in ("list", "ep"):
        for path in linked(config, "members" if item.kind == "list" else "ep_members"):
            html = page(path)
            if html is None:
                continue
            if item.kind == "list":
                members = [m for m in summary.members(html) if m.area == "Daugiamandatė"]
            else:
                members = summary.members(html, (0, 1, None))
    return Facts(found, members, urls)


def attach(
    p: Package,
    state: State,
    item: Part,
    result_set: str,
    here: pl.DataFrame,
    options: dict[str, str],
    code: str,
    found: summary.Contest,
) -> None:
    """Store the figures a summary page declares for one contest, and its elected candidates and council seats."""
    distinct = here.select("option", "first", "last", "number_on_list").unique(subset="option")
    names = {summary.named(f"{r['first']} {r['last']}"): r["option"] for r in distinct.iter_rows(named=True)}
    numbers = {str(r["number_on_list"]): r["option"] for r in distinct.iter_rows(named=True)}

    def option_of(label: str) -> str:
        option = (numbers if item.kind == "council" else names).get(
            label if item.kind == "council" else summary.named(label)
        )
        if option is None:
            raise ValueError(f"lt.vrk {item.kind} {code}: {label!r} is not an option of the contest")
        return option

    declared: list[tuple[str | None, int, int]] = [(None, m, v) for m, v in sorted(found.totals.items())]
    declared += [(option_of(label), 1, votes) for label, votes in found.votes.items()]
    reconcile(
        p,
        state,
        result_set,
        here,
        options,
        declared,
        [(option_of(n), seats) for n, seats in found.seats.items()],
        f"{item.kind} {code}",
    )
    for name in found.elected:
        p.add("outcome", result_set_id=result_set, option_id=options[option_of(name)], kind="elected")


def seat_members(
    p: Package,
    state: State,
    config: Config,
    item: Part,
    result_set: str,
    here: pl.DataFrame,
    options: dict[str, str],
    members: list[summary.Member],
) -> None:
    """The people elected from a list contest's lists: each becomes a candidacy with an elected outcome."""
    labels = {
        normal(r["label"]): r["option"] for r in here.select("option", "label").unique("option").iter_rows(named=True)
    }
    for member in members:
        option = labels.get(normal(member.party if item.kind == "list" else member.area))
        if option is None:
            raise ValueError(f"lt.vrk {item.kind}: {member.name!r} is elected from an unknown list {member.party!r}")
        person = key("person", config.first, item.kind, "member", summary.named(member.name))
        if person not in state.people:
            state.people.add(person)
            p.add("person", id=person)
            p.name(person, title(member.name), "lt", "official")
        candidacy = key("candidacy", result_set, summary.named(member.name))
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[option],
            role="list_member",
            ballot_name=title(member.name),
            is_independent=False,
        )
        p.add("outcome", result_set_id=result_set, option_id=options[option], candidacy_id=candidacy, kind="elected")


def options_for(
    p: Package, state: State, config: Config, item: Part, contest: str, here: pl.DataFrame
) -> dict[str, str]:
    options: dict[str, str] = {}
    distinct = here.select("option", "first", "last", "party", "label", "number_on_list").unique(
        subset="option", keep="first", maintain_order=True
    )
    known = lists()
    for row in distinct.iter_rows(named=True):
        option = options[row["option"]] = key("option", contest, row["option"])
        listed = row["party"] is not None and normal(row["party"]) in known
        if item.kind in ("smd", "mayor", "president"):
            name = title(f"{row['first']} {row['last']}")
            p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=name)
            person = key(
                "person", config.first, item.kind, here["ap"][0] if item.kind != "president" else "", row["option"]
            )
            if person not in state.people:
                state.people.add(person)
                p.add("person", id=person)
                p.name(person, name, "lt", "official")
            p.add(
                "candidacy",
                id=key("candidacy", contest, row["option"]),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party(row["party"]) if listed else None,
                is_independent=row["party"] is None,
            )
        elif item.kind == "referendum":
            p.add("contest_option", id=option, contest_id=contest, kind="answer", ballot_label=row["label"])
        else:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(row["party"]) if listed else None,
                ballot_position=row["number_on_list"],
                ballot_label=row["label"],
            )
    return options


def merge(here: pl.DataFrame) -> pl.DataFrame:
    """One row per polling district: the votes and ballots of its protocols added up.

    The roll comes from the main protocol. Its turnout covers the supplementary protocol only when the main
    protocol's valid and invalid ballots fall short of it by that much; otherwise the supplementary turnout is added.
    """
    base = here.select(
        "unit",
        "supplementary",
        "number",
        "name",
        "sav",
        "registered",
        "cast",
        "box_valid",
        "box_invalid",
        "post_valid",
        "post_invalid",
    ).unique(subset=["unit", "supplementary"], keep="first", maintain_order=True)
    counts = ("box_valid", "box_invalid", "post_valid", "post_invalid")
    extra = (
        base.filter(pl.col("supplementary"))
        .group_by("unit", maintain_order=True)
        .agg(
            pl.col("number").first().alias("x_number"),
            pl.col("name").first().alias("x_name"),
            pl.col("sav").first().alias("x_sav"),
            pl.col("registered").first().alias("x_registered"),
            pl.col("cast").sum().alias("x_cast"),
            *(pl.col(column).sum().alias(f"x_{column}") for column in counts),
        )
    )
    joined = (
        base.filter(~pl.col("supplementary")).drop("supplementary").join(extra, on="unit", how="full", coalesce=True)
    )
    covered = pl.col("cast") == pl.sum_horizontal(*counts)
    return joined.select(
        "unit",
        pl.coalesce("number", "x_number").alias("number"),
        pl.coalesce("name", "x_name").alias("name"),
        pl.coalesce("sav", "x_sav").alias("sav"),
        pl.coalesce("registered", "x_registered").alias("registered"),
        pl.when(pl.col("cast").is_not_null() & covered & (pl.col("x_cast").fill_null(0) > 0))
        .then(pl.col("cast") + pl.col("x_cast"))
        .otherwise(pl.coalesce("cast", "x_cast"))
        .alias("cast"),
        *(pl.sum_horizontal(column, f"x_{column}").alias(column) for column in counts),
    )


def figures(here: pl.DataFrame) -> dict[tuple[str | None, int], int]:
    """What the unit rows of these protocols add up to, by option (None for the ballots) and measure."""
    merged = merge(here)
    found: dict[tuple[str | None, int], int] = {
        (None, 2): int(merged["registered"].sum() or 0),
        (None, 3): int(merged["cast"].sum() or 0),
        (None, 4): int(merged.select((pl.col("box_valid") + pl.col("post_valid")).sum()).item() or 0),
        (None, 5): int(merged.select((pl.col("box_invalid") + pl.col("post_invalid")).sum()).item() or 0),
    }
    for option, box, post in here.group_by("option").agg(pl.col("box").sum(), pl.col("post").sum()).iter_rows():
        found[(option, 1)] = int((box or 0) + (post or 0))
    return found


def unit_rows(p: Package, state: State, day: date, scope: str, result_set: str, here: pl.DataFrame) -> dict[str, str]:
    """Add the polling districts and their ballot accounting; return each district's id by its source key."""
    merged = merge(here)
    short = merged.filter(
        pl.col("cast") != pl.col("box_valid") + pl.col("post_valid") + pl.col("box_invalid") + pl.col("post_invalid")
    )
    state.gaps += short.height
    over = merged.filter(pl.col("cast") > pl.col("registered"))
    state.over += over.height
    state.over_abroad += sum(abroad(row["name"] or "", row["sav"]) for row in over.iter_rows(named=True))
    state.gap_net += int(
        short.select(
            (
                pl.col("cast")
                - pl.col("box_valid")
                - pl.col("post_valid")
                - pl.col("box_invalid")
                - pl.col("post_invalid")
            ).sum()
        ).item()
        or 0
    )
    ids = {}
    for row in merged.iter_rows(named=True):
        code = f"{day}:{scope}{row['unit']}"
        id = ids[row["unit"]] = key("unit", code)
        if id in state.units:
            continue
        state.units.add(id)
        outside = abroad(row["name"] or "", row["sav"])
        p.add(
            "reporting_unit",
            id=id,
            polity_id=LITHUANIA,
            scheme_code="lt.apylinke",
            code=code,
            area_relation="unlocated" if outside else "within",
            area_id=None if outside else area("lt.sav", row["sav"]) if row["sav"] else TERRITORY,
            kind="abroad_mission" if outside else "standard",
        )
    totals = merged.with_columns(
        id=pl.col("unit").replace_strict(ids),
        valid=pl.sum_horizontal("box_valid", "post_valid"),
        invalid=pl.sum_horizontal("box_invalid", "post_invalid"),
    )
    out = []
    for column, measure in (("registered", 2), ("cast", 3), ("valid", 4), ("invalid", 5)):
        out.append(
            totals.select(
                result_set_id=pl.lit(result_set),
                unit_id="id",
                option_id=pl.lit(None, pl.String),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(measure, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value=pl.col(column),
            ).drop_nulls("value")
        )
    p.frame("result_value", pl.concat(out))
    return ids


def value_rows(p: Package, result_set: str, here: pl.DataFrame, options: dict[str, str], ids: dict[str, str]) -> None:
    votes = here.group_by("unit", "option").agg(pl.col("box").sum(), pl.col("post").sum())
    p.frame(
        "result_value",
        votes.select(
            result_set_id=pl.lit(result_set),
            unit_id=pl.col("unit").replace_strict(ids),
            option_id=pl.col("option").replace_strict(options),
            channel=pl.lit(0, pl.Int16),
            measure=pl.lit(1, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value=pl.sum_horizontal("box", "post"),
        ),
    )


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").replace("﻿", " "))


TOTALS = (
    (2, r"rinkėjų sąraš\w+ įrašyta rinkėjų\s*[–-]\s*(\d+)"),
    (3, r"(?:rinkimuose|referendume) dalyvavo\s*[–-]\s*(\d+)"),
    (5, r"negaliojančių biuletenių\s*[–-]\s*(\d+)"),
    (4, r"(?<!ne)galiojančių biuletenių\s*[–-]\s*(\d+)"),
)


def pattern(text: str) -> str:
    return r"\s+".join(re.escape(word) for word in text.split())


def declare(
    item: Part, here: pl.DataFrame, html: str
) -> tuple[list[tuple[str | None, int, int]], list[tuple[str, int]]]:
    """The national figures the Commission's summary page prints, which the unit rows must add up to."""
    text = flat(html)
    figures_: list[tuple[str | None, int, int]] = []
    seats: list[tuple[str, int]] = []
    for measure, expression in TOTALS:
        found = re.search(expression, text)
        if found:
            figures_.append((None, measure, int(found.group(1))))
    distinct = here.select("option", "first", "last", "label", "number_on_list").unique(subset="option")
    if item.kind == "referendum":
        found = re.search(r"(\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (?:\d+,\d+ ){4}", text)
        if found:
            for row in distinct.iter_rows(named=True):
                figures_.append((row["option"], 1, int(found.group(5 if row["option"] == "Yes" else 6))))
        return figures_, seats
    for row in distinct.iter_rows(named=True):
        if item.kind == "president":
            found = re.search(
                rf"{pattern(f'{row["first"]} {row["last"]}')}\s+(\d+)\s+(\d+)\s+(\d+)\s", text, re.IGNORECASE
            )
            if found:
                figures_.append((row["option"], 1, int(found.group(3))))
        else:
            found = re.search(
                rf"(?<!\d){row['number_on_list']}\s+{pattern(row['label'])}\s+pirm\.\s*(?:\(csv\)\s*)?"
                r"(\d+)\s+(\d+)\s+(\d+)\s+(?:\d+,\d+\s+)+(\d+)",
                text,
                re.IGNORECASE,
            )
            if found:
                figures_.append((row["option"], 1, int(found.group(3))))
                if int(found.group(4)) > 0:
                    seats.append((row["option"], int(found.group(4))))
    return figures_, seats


def reconcile(
    p: Package,
    state: State,
    result_set: str,
    here: pl.DataFrame,
    options: dict[str, str],
    declared: list[tuple[str | None, int, int]],
    seats: list[tuple[str, int]],
    where: str = "",
) -> None:
    """Store the Commission's figures, and note when they differ from what the polling-district files add up to."""
    for option, measure, value in declared:
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=options[option] if option is not None else None,
            measure=measure,
            value=value,
        )
    for option, n in seats:
        p.add("outcome", result_set_id=result_set, option_id=options[option], kind="won", seats=n)
    ours = figures(here)
    if all(ours.get((option, measure)) == value for option, measure, value in declared):
        return
    main = figures(here.filter(~pl.col("supplementary")))
    if all(main.get((option, measure)) == value for option, measure, value in declared):
        extra = here.filter(pl.col("supplementary"))
        state.held.append((extra.select("unit").n_unique(), figures(extra)[(None, 4)], where.split()[0]))
        return
    for option, measure, value in declared:
        if ours.get((option, measure)) != value:
            state.differ.append((where, option, measure, value, ours.get((option, measure), 0)))
