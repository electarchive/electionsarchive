"""Lithuanian municipal council elections 1995-2015 and the direct mayoral elections of 2015, from the Central Electoral
Commission's archived results pages.

The Commission's archive prints, for every municipality, the votes each candidate list won at the polling stations and
by post, the council seats each list won, and the voters on the roll, the ballots cast, and the valid and invalid
ballots. It prints the same figures for every polling district (apylinkė). The pages of one election were saved
together in one file, each page headed by the address it came from. A file that stopped at the saving tool's page cap
holds only some of the polling-district pages; a municipality whose polling districts are not all in the file is
stored as one count for the whole municipality, and one whose polling districts add up to the printed municipality
figures is stored by polling district.

Each election is one package, `lt.vrk_council/<date>`. The second round of the 2015 mayoral elections is its own
election, two weeks after the first, in the same package. The packages sit on `lt.vrk_council/geo` (the 2017
municipal outlines) and `lt.vrk_council/registry` (parties and electoral systems), and use the municipalities,
council bodies, mayoral offices, and two-round system that `lt.vrk` holds.
"""

import functools
import json
import re
from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime
from typing import NamedTuple
from uuid import uuid5
from zoneinfo import ZoneInfo

import polars as pl
import shapely
from shapely.geometry import shape

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.lt import vrk
from ea.sources.lt.vrk_president import clean, read
from ea.store import DATA

SOURCE = "lt.vrk_council"
SITE = "https://www.vrk.lt"
BASE = f"{SITE}/statiniai/puslapiai"
LITHUANIA = vrk.LITHUANIA
TERRITORY = vrk.TERRITORY
VILNIUS = ZoneInfo("Europe/Vilnius")
INBOX = DATA / "inbox" / "manual" / SOURCE
GEO = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/LTU/ADM2/geoBoundaries-LTU-ADM2.geojson"
SCHEME = "lt.apylinke_pre2016"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


VRK_SOURCE = key("source", "results")
GEO_SOURCE = key("source", "geoboundaries")


class Year(NamedTuple):
    day: date
    bundle: str  # the saved file's address
    municipality: str  # the pattern of a municipality page's address
    station: str  # the pattern of a polling-district page's address
    kind: str  # the page layout: 1995, 1997, 2000, 2002, 2007, or 2011


YEARS = (
    Year(
        date(1995, 3, 25),
        f"{BASE}/rinkimai/1995/savivaldybes/rezultatai/rez_apg_sar_l_2.htm",
        r"/rez_apg_l_\d+\.htm$",
        r"/rez_apl_l_\d+\.htm$",
        "1995",
    ),
    Year(
        date(1997, 3, 23),
        f"{BASE}/n/rinkimai/19970323/index.html",
        r"/rapgpl\.htm-\d+\.htm$",
        r"/raplpl\.htm-\d+\.htm$",
        "1997",
    ),
    Year(
        date(2000, 3, 19),
        f"{BASE}/n/rinkimai/20000319/rapgsarl.htm-12.htm",
        r"/rapgpl\.htm-\d+\.htm$",
        r"/raplpl\.htm-\d+\.htm$",
        "2000",
    ),
    Year(
        date(2002, 12, 22),
        f"{BASE}/rinkimai/2002/savivaldybes/rezultatai/rapgsarl_15.htm",
        r"/rapgpl_\d+\.htm$",
        r"/raplpl_\d+\.htm$",
        "2000",
    ),
    Year(
        date(2007, 2, 25),
        f"{BASE}/2007_savivaldybiu_tarybu_rinkimai/rinkimu_rezultatai.html",
        r"/rinkimu_apygardos/rapgpl_\d+\.html$",
        r"/rinkimu_apylinkes/raplpl_\d+\.htm$",
        "2000",
    ),
    Year(
        date(2011, 2, 27),
        f"{BASE}/2011_savivaldybiu_tarybu_rinkimai/output_lt/rinkimu_diena/index.html",
        r"/output_lt/rezultatai_daugiamand_apygardose/apygardos_rezultatai\d+\.html$",
        r"/output_lt/rezultatai_daugiamand_apygardose/apylinkes\d+_rezultatai\.html$",
        "2011",
    ),
    Year(
        date(2015, 3, 1),
        f"{BASE}/2015_savivaldybiu_tarybu_rinkimai/output_lt/rinkimu_diena/index.html",
        r"/output_lt/rezultatai_daugiamand_apygardose/apygardos_rezultatai\d+\.html$",
        r"/output_lt/rezultatai_daugiamand_apygardose/apylinkes\d+_rezultatai\.html$",
        "2011",
    ),
)
MAYORS = YEARS[-1]
MAYOR_DAYS = (date(2015, 3, 1), date(2015, 3, 15))
MAYOR_PAGES = {
    1: (r"/output_lt/rezultatai_vienmand_apygardose/rezultatai_apylinke\d+visodesc1turas\.html$", "visodesc1turas"),
    2: (r"/output_lt/rezultatai_vienmand_apygardose2/rezultatai_apylinke\d+visodesc2turas\.html$", "visodesc2turas"),
}
MAYOR_ELECTED = r"/output_lt/savivaldybiu_tarybu_sudetis/merai_apygarda_ordered\.html$"
ENGLISH = {
    date(1995, 3, 25): "1995 Lithuanian municipal council elections",
    date(1997, 3, 23): "1997 Lithuanian municipal council elections",
    date(2000, 3, 19): "2000 Lithuanian municipal council elections",
    date(2002, 12, 22): "2002 Lithuanian municipal council elections",
    date(2007, 2, 25): "2007 Lithuanian municipal council elections",
    date(2011, 2, 27): "2011 Lithuanian municipal council elections",
    date(2015, 3, 1): "2015 Lithuanian municipal council and mayoral elections",
}
UPDATED = {  # the date each year's pages print as the time of their last update, where one page gives it
    "1995": r"Puslapis atnaujintas (\d{4}) (\d\d) (\d\d), (\d\d):(\d\d)",
    "2007": r"Puslapis atnaujintas (\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)",
    "2011": r"Puslapis atnaujintas (\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)",
}


class Row(NamedTuple):
    number: int | None  # the list's number on the ballot
    label: str
    box: int
    post: int
    total: int
    seats: int


class Count(NamedTuple):
    """One protocol: a municipality's, or a polling district's."""

    url: str
    sav: str  # the municipality, as `lt.vrk` names its area
    sav_number: int
    number: int | None  # the polling district's number in the municipality
    name: str | None
    registered: int | None
    cast: int | None
    valid: int | None
    invalid: int | None
    rows: list[Row]
    stamp: datetime | None


MARK = re.compile(r"(?m)^<!-- URL: (\S+) -->\n")


def pages_of(url: str) -> Iterator[tuple[str, str]]:
    """The pages of a saved bundle, as (address, html)."""
    content = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    parts = MARK.split(content)
    yield from zip(parts[1::2], parts[2::2], strict=True)


def number(text: str) -> int:
    """A count as the pages print it: digits, a dash or nothing for none."""
    text = clean(text).replace(" ", "").rstrip(".")
    if text in ("", "-", "–", "−"):
        return 0
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"not a count: {text!r}")
    return int(text)


def flat(text: str) -> str:
    text = re.sub(r"[−–]", "-", text)
    return re.sub(r"\s+([,.])", r"\1", re.sub(r"\s+", " ", text))


SAVIVALDYBES = {
    "Birštono miesto": "Birštono",
    "Druskininkų miesto": "Druskininkų",
    "Neringos miesto": "Neringos",
    "Visagino miesto": "Visagino",
    "Marijampolės miesto": "Marijampolės",
    "Kalvarijos miesto": "Kalvarijos",
    "Pagėgių miesto": "Pagėgių",
    "Rietavo miesto": "Rietavo",
    "Kazlų Rūdos miesto": "Kazlų Rūdos",
    "Elektrėnų miesto": "Elektrėnų",
    "Palangos": "Palangos miesto",
}
HISTORICAL = (
    "Marijampolės rajono savivaldybė"  # the district municipality of 1995-2000, before Kalvarija and Kazlų Rūda
)


@functools.cache
def areas() -> dict[str, str]:
    """`lt.vrk`'s municipality names, by the short name the Commission's pages use."""
    names = [
        "Akmenės rajono", "Alytaus miesto", "Alytaus rajono", "Anykščių rajono", "Birštono", "Biržų rajono",
        "Druskininkų", "Elektrėnų", "Ignalinos rajono", "Jonavos rajono", "Joniškio rajono", "Jurbarko rajono",
        "Kaišiadorių rajono", "Kalvarijos", "Kauno miesto", "Kauno rajono", "Kazlų Rūdos", "Kelmės rajono",
        "Klaipėdos miesto", "Klaipėdos rajono", "Kretingos rajono", "Kupiškio rajono", "Kėdainių rajono",
        "Lazdijų rajono", "Marijampolės", "Mažeikių rajono", "Molėtų rajono", "Neringos", "Pagėgių",
        "Pakruojo rajono", "Palangos miesto", "Panevėžio miesto", "Panevėžio rajono", "Pasvalio rajono",
        "Plungės rajono", "Prienų rajono", "Radviliškio rajono", "Raseinių rajono", "Rietavo", "Rokiškio rajono",
        "Skuodo rajono", "Tauragės rajono", "Telšių rajono", "Trakų rajono", "Ukmergės rajono", "Utenos rajono",
        "Varėnos rajono", "Vilkaviškio rajono", "Vilniaus miesto", "Vilniaus rajono", "Visagino", "Zarasų rajono",
        "Šakių rajono", "Šalčininkų rajono", "Šiaulių miesto", "Šiaulių rajono", "Šilalės rajono", "Šilutės rajono",
        "Širvintų rajono", "Švenčionių rajono",
    ]  # fmt: skip
    return {name: f"{name} savivaldybė" for name in names}


def municipality(name: str) -> str:
    """The `lt.vrk` area name of a municipality as a page spells it."""
    name = clean(name).replace("\xa0", " ")
    name = SAVIVALDYBES.get(name, name)
    if name == "Marijampolės rajono":
        return HISTORICAL
    known = areas()
    if name not in known:
        raise ValueError(f"unknown municipality {name!r}")
    return known[name]


def area(name: str) -> str:
    return key("area", "lt.sav_1995", name) if name == HISTORICAL else vrk.area("lt.sav", name)


SAV = re.compile(r"^(.+?) \(Nr\. ?(\d+)\) (?:rinkimų )?(?:apygarda|savivaldybė)$")
STATION = re.compile(r"^(.+?) \(Nr\. ?(\d+)\) (?:rinkimų )?apylinkė$")
# registered, cast, valid, invalid for a municipality page, by layout
MUNICIPAL = {
    "1995": r"turinčių piliečių - (\d+), rinkimuose dalyvavo - (\d+) \([\d.,]+%\), negaliojančių biuletenių - (\d+) \([\d.,]+%\), galiojančių biuletenių - (\d+)",
    "1997": r"Rinkimų apygardos rinkėjų skaičius: (\d+) Rinkimuose dalyvavusių apygardos rinkėjų skaičius: (\d+) \([^)]*\) Apygardoje negaliojančių biuletenių skaičius: (\d+) \([^)]*\) Apygardoje galiojančių biuletenių skaičius: (\d+)",
    "2000": r"[Rr]inkėjų skaičius: (\d+), (?:rinkimuose dalyvavo|dalyvavusiųjų rinkimuose skaičius): (\d+) \([^)]*\)\. Galiojančių biuletenių(?: skaičius)?: (\d+) \([^)]*\), negaliojančių(?: biuletenių skaičius)?: (\d+)",
    "2011": r"(?:turinčių piliečių|įrašyta rinkėjų) - (\d+), rinkimuose dalyvavo - (\d+) \([\d.,]+%\), negaliojančių biuletenių - (\d+) \([\d.,]+%\), galiojančių biuletenių - (\d+)",
}  # fmt: skip
INVALID_FIRST = {"1995", "1997", "2011"}
STATIONS = {
    "1995": r"piliečių apylinkėje - (\d+), rinkimuose dalyvavo - (\d+) \([^)]*\)\. Negaliojančių biuletenių - (\d+) \([^)]*\), galiojančių biuletenių - (\d+)",
    "2011": r"(?:turinčių piliečių|įrašyta rinkėjų) - (\d+), rinkimuose dalyvavo - (\d+) \([\d.,]+%\), negaliojančių biuletenių - (\d+) \([\d.,]+%\), galiojančių biuletenių - (\d+)",
}  # fmt: skip
STATION_1997 = (
    r"Rinkėjų skaičius rinkimų apylinkėje: (\d+) .*?Balsadėžėje rastų negaliojančių rinkimų biuletenių skaičius: (\d+) "
    r"Balsadėžėje rastų galiojančių rinkimų biuletenių skaičius: (\d+) Paštu gautų negaliojančių rinkimų biuletenių "
    r"skaičius: (\d+) Paštu gautų galiojančių rinkimų biuletenių skaičius: (\d+) Iš viso rinkimų apylinkėje "
    r"dalyvavusių rinkimuose rinkėjų skaičius: (\d+)"
)
STATION_2000 = (
    r"Rinkėjų skaičius apylinkėje - (\d+), apylinkės patalpose balsavo (\d+)\. Balsadėžėje rasta rinkimų biuletenių: "
    r"galiojančių - (\d+), negaliojančių - (\d+)\. Paštu gauta rinkimų biuletenių: galiojančių - (\d+), negaliojančių "
    r"- (\d+)\. Iš viso rinkimų apylinkėje dalyvavusių rinkimuose rinkėjų skaičius: (\d+)"
)


def stamp(kind: str, html: str) -> datetime | None:
    pattern = UPDATED.get(kind)
    found = re.search(pattern, flat(html)) if pattern else None
    if found is None:
        return None
    y, m, d, h, i = (int(g) for g in found.groups())
    return datetime(y, m, d, h, i, tzinfo=VILNIUS)


def lists(tables: list[list[list[str]]], layout: str) -> list[Row]:
    """The candidate lists of a page's results table, with the votes at the ballot box and by post."""
    found: list[Row] = []
    for table in tables:
        for cells in table:
            if len(cells) < 5 or not re.fullmatch(r"\d+\.?", cells[0]) or re.fullmatch(r"[\d.,%\s-]*", cells[1]):
                continue
            off = 3 if layout == "2011" else 2
            if len(cells) < off + 3 or not all(re.fullmatch(r"[\d\s-]*", c) for c in cells[off : off + 3]):
                continue
            seats = 0
            if len(cells) > off + 3 and "%" not in cells[-1] and cells[-1] != "":
                seats = number(cells[-1])
            box, post, total = (number(c) for c in cells[off : off + 3])
            found.append(Row(int(cells[0].rstrip(".")), cells[1], box, post, total, seats))
    return found


def municipal(url: str, html: str, kind: str) -> Count:
    page = read(url, html)
    text = flat(page.text)
    heading = next((m for line in page.lines for part in line.split(" | ") if (m := SAV.match(clean(part)))), None)
    if heading is None:
        raise ValueError(f"{url}: no municipality heading")
    found = re.search(MUNICIPAL[kind], text)
    cast = valid = invalid = None
    if found is not None:
        registered, cast, a, b = (int(g) for g in found.groups())
        invalid, valid = (a, b) if kind in INVALID_FIRST else (b, a)
    elif kind == "2011" and (found := re.search(r"įrašyta rinkėjų - (\d+), dalyvavo rinkėjų: ", text)):
        registered = int(found.group(1))  # the 2015 pages print turnout and invalid ballots only as percentages
    else:
        raise ValueError(f"{url}: no ballot figures")
    return Count(
        url,
        municipality(heading.group(1)),
        int(heading.group(2)),
        None,
        None,
        registered,
        cast,
        valid,
        invalid,
        lists(page.tables, kind),
        stamp(kind, html),
    )


def station(url: str, html: str, kind: str) -> Count:
    page = read(url, html)
    text = flat(page.text)
    lines = [clean(part) for line in page.lines for part in line.split(" | ")]
    sav = next((m for line in lines if (m := SAV.match(line))), None)
    stn = next((m for line in lines if (m := STATION.match(line))), None)
    special = next((line for line in lines if re.search(r"suskaičiuoti.*komisij|komisij.*suskaičiuoti", line)), None)
    if sav is None or (stn is None and special is None):
        raise ValueError(f"{url}: no heading")
    if special is not None and stn is None:
        found = re.search(
            r"Rinkimuose dalyvavo - (\d+)\. Negaliojančių biuletenių - (\d+) \([^)]*\), galiojančių biuletenių - (\d+)",
            text,
        )
        cast, invalid, valid = (int(g) for g in found.groups()) if found else (None, None, None)
        return Count(
            url, municipality(sav.group(1)), int(sav.group(2)), 0, special.rstrip("."), None, cast, valid, invalid,
            lists(page.tables, kind), stamp(kind, html),
        )  # fmt: skip
    assert stn is not None
    if kind in STATIONS:
        found = re.search(STATIONS[kind], text)
        if found is None:
            raise ValueError(f"{url}: no ballot figures")
        registered, cast, invalid, valid = (int(g) for g in found.groups())
    elif kind == "1997":
        found = re.search(STATION_1997, text)
        if found is None:
            raise ValueError(f"{url}: no ballot figures")
        registered, box_invalid, box_valid, post_invalid, post_valid, cast = (int(g) for g in found.groups())
        valid, invalid = box_valid + post_valid, box_invalid + post_invalid
    else:
        found = re.search(STATION_2000, text)
        if found is None:
            raise ValueError(f"{url}: no ballot figures")
        registered, _, box_valid, box_invalid, post_valid, post_invalid, cast = (int(g) for g in found.groups())
        valid, invalid = box_valid + post_valid, box_invalid + post_invalid
    return Count(
        url,
        municipality(sav.group(1)),
        int(sav.group(2)),
        int(stn.group(2)),
        stn.group(1),
        registered,
        cast,
        valid,
        invalid,
        lists(page.tables, kind),
        stamp(kind, html),
    )


class Council(NamedTuple):
    municipalities: dict[str, Count]  # by area name, for the election day
    stations: dict[str, list[Count]]
    annulled: set[str]  # municipalities whose count the Commission declared invalid
    rerun: dict[str, tuple[Count, list[Count]]]  # a municipality's repeat election, with its polling districts


def compatible(count: Count, municipal: Count) -> bool:
    """Whether a polling district's lists are the lists of this municipality page (the ballot numbers carry a label)."""
    labels = {r.number: r.label for r in municipal.rows}
    return all(labels.get(r.number) == r.label for r in count.rows)


@functools.cache
def council(day: date) -> Council:
    year = next(y for y in YEARS if y.day == day)
    found: dict[str, list[tuple[Count, bool]]] = {}
    stations: dict[str, list[Count]] = {}
    for url, html in pages_of(year.bundle):
        if re.search(year.municipality, url):
            count = municipal(url, html, year.kind)
            found.setdefault(count.sav, []).append((count, "pripažinti negaliojančiais" in html))
        elif re.search(year.station, url):
            count = station(url, html, year.kind)
            stations.setdefault(count.sav, []).append(count)
    municipalities: dict[str, Count] = {}
    annulled: set[str] = set()
    rerun: dict[str, tuple[Count, list[Count]]] = {}
    for sav, pages in found.items():
        if len(pages) == 1:
            municipalities[sav] = pages[0][0]
            continue
        # a municipality counted twice: the first count was declared invalid, and the election was repeated
        first = next(count for count, void in pages if void)
        second = next(count for count, void in pages if not void)
        municipalities[sav] = first
        annulled.add(sav)
        every = stations.pop(sav, [])
        stations[sav] = [x for x in every if compatible(x, first)]
        rerun[sav] = (second, [x for x in every if compatible(x, second) and not compatible(x, first)])
    return Council(municipalities, stations, annulled, rerun)


class Candidate(NamedTuple):
    name: str
    box: int
    post: int
    total: int


class Protocol(NamedTuple):
    url: str
    sav: str
    sav_number: int
    number: int
    name: str
    registered: int
    cast: int
    valid: int
    invalid: int
    rows: list[Candidate]


MAYOR_STATION = (
    r"Rinkėjų skaičius apylinkėje: (\d+), apylinkės patalpose balsavo: (\d+) Balsadėžėje rasta rinkimų biuletenių: "
    r"galiojančių - (\d+), negaliojančių - (\d+) Paštu gauta rinkimų biuletenių: galiojančių - (\d+), negaliojančių "
    r"- (\d+) Iš viso rinkimų apylinkėje dalyvavusių rinkimuose rinkėjų skaičius: (\d+)"
)


def protocol(url: str, html: str) -> Protocol:
    page = read(url, html)
    text = flat(page.text)
    lines = [clean(part) for line in page.lines for part in line.split(" | ")]
    sav = next((m for line in lines if (m := SAV.match(line))), None)
    stn = next((m for line in lines if (m := STATION.match(line))), None)
    found = re.search(MAYOR_STATION, text)
    if sav is None or stn is None or found is None:
        raise ValueError(f"{url}: not a mayoral polling-district page")
    registered, _, box_valid, box_invalid, post_valid, post_invalid, cast = (int(g) for g in found.groups())
    rows = [
        Candidate(cells[0], number(cells[1]), number(cells[2]), number(cells[3]))
        for table in page.tables
        for cells in table
        if len(cells) >= 4
        and not cells[0].startswith(("Iš viso", "Kandidat", "skaičius", "balsadėžėje"))
        and cells[0] != ""
        and all(re.fullmatch(r"[\d\s-]+", c) for c in cells[1:4])
    ]
    return Protocol(
        url, municipality(sav.group(1)), int(sav.group(2)), int(stn.group(2)), stn.group(1), registered, cast,
        box_valid + post_valid, box_invalid + post_invalid, rows,
    )  # fmt: skip


class Mayoral(NamedTuple):
    stations: dict[str, list[Protocol]]  # by area name
    declared: dict[str, tuple[int, int, int, int, int]]  # polling districts, registered, cast, invalid, valid
    elected: dict[str, tuple[str, str]]  # the mayor's name and list, by area name
    urls: list[str]


@functools.cache
def mayors(round_: int) -> Mayoral:
    pattern, _ = MAYOR_PAGES[round_]
    summary = (
        r"/output_lt/rezultatai_vienmand_apygardose"
        + ("2" if round_ == 2 else "")
        + r"/apygardos_rezultatai_apylinkese\d+\.html$"
    )
    stations: dict[str, list[Protocol]] = {}
    declared: dict[str, tuple[int, int, int, int, int]] = {}
    elected: dict[str, tuple[str, str]] = {}
    urls: list[str] = []
    for url, html in pages_of(MAYORS.bundle):
        if re.search(pattern, url):
            item = protocol(url, html)
            stations.setdefault(item.sav, []).append(item)
        elif re.search(summary, url):
            page = read(url, html)
            heading = next(
                (m for line in page.lines for part in line.split(" | ") if (m := SAV.match(clean(part)))), None
            )
            if heading is None:
                raise ValueError(f"{url}: no municipality heading")
            table = next(t for t in page.tables if t and t[0][:2] == ["Apylinkė", "Rinkėjų skaičius"])
            total = next(r for r in table if r[0].startswith("Iš viso"))
            polling = [r for r in table if re.match(r"\d+\. ", r[0])]
            declared[municipality(heading.group(1))] = (
                len(polling), number(total[1]), number(total[2]), number(total[4]), number(total[6])
            )  # fmt: skip
            urls.append(url)
        elif round_ == 1 and re.search(MAYOR_ELECTED, url):
            page = read(url, html)
            for table in page.tables:
                for cells in table:
                    if len(cells) == 3 and re.match(r"\d+\. ", cells[0]):
                        elected[municipality(re.sub(r"^\d+\. ", "", cells[0]))] = (cells[1], cells[2])
            urls.append(url)
    return Mayoral(stations, declared, elected, urls)


def fetch() -> None:
    """Take the saved results files from the manual inbox, and the municipal outlines from geoBoundaries.

    The Commission's results server answers scripted requests with a challenge page, so each election's pages were
    saved by hand into one file (every page headed by its address) and listed in `index.json`.
    """
    index = json.loads((INBOX / "index.json").read_text())
    for url, entry in index.items():
        if "statiniai/puslapiai" not in url:
            continue
        file = INBOX / entry["file"]
        raw.store(
            SOURCE,
            entry.get("source", url),
            file.read_bytes(),
            suffix=file.suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
    raw.fetch(SOURCE, GEO, suffix=".geojson")


# ---------------------------------------------------------------------------------------------------------------------
# packages

SYSTEMS = {
    "list_council": (
        {"formula": "hare", "lists": {"order": "flexible", "vacancy": "next_on_list"}},
        (
            "Municipal council seats are allocated among the candidate lists by the largest remainder method with the "
            "Hare quota (the quota the Commission's pages print as the seat-allocation quota). Voters may mark "
            "preferences for candidates within a list; the Commission's pages from 1995 and 1997 print none. The "
            "seat counts and thresholds are not recorded here, and the rules are the same in every year."
        ),
        date(1995, 1, 1),
    ),
    "two_round": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A mayoral candidate who wins more than half of the valid votes is elected; otherwise the two leading "
            "candidates go to a second round two weeks later. Mayors were elected directly from 2015; before that, the "
            "council chose its mayor."
        ),
        date(2015, 1, 1),
    ),
}
METHOD = (
    "Counted by the polling district commissions (apylinkių rinkimų komisijos) and published by the Central Electoral "
    "Commission, per polling district and for each municipality. Registered voters are the electoral roll, ballots "
    "cast are the voters who took part, and valid and invalid ballots are the ballots found valid or invalid. Votes "
    "for a candidate list are stored on the ballot-box (1) and postal (3) channels as the source splits them. Counts "
    "that a district or municipal election commission made itself, outside any polling district, are stored as units "
    "of their own."
)
WHOLE = (
    "The polling-district pages saved for this municipality do not add up to the municipality's own figures, so the "
    "municipality is stored as one count, as its page prints it."
)
ANNULLED = (
    "The Commission declared the results in this municipality invalid for gross violations of the law on municipal "
    "council elections (its decision of 29 March 1997, No. 149), and held the election again on 29 June 1997. The "
    "count is kept as published, with no seats."
)


def normal(name: str) -> str:
    return vrk.normal(name)


def personal(label: str) -> bool:
    """A self-nominated candidate stands as a list under their own name, with the surname in capitals."""
    return bool(re.search(r"\b[A-ZĄČĘĖĮŠŲŪŽ]{3,}\b", label))


def party(label: str) -> str:
    return key("party", normal(label))


@functools.cache
def parties() -> dict[str, str]:
    """Every party, coalition, or committee a list or a mayor names, by its normalized name."""
    found: dict[str, str] = {}
    for year in YEARS:
        data = council(year.day)
        everything = [*data.municipalities.values(), *(c for c, _ in data.rerun.values())]
        for count in everything:
            for row in count.rows:
                if not personal(row.label):
                    found.setdefault(normal(row.label), row.label)
    for _, label in mayors(1).elected.values():
        if label != "Save išsikėlęs":
            found.setdefault(normal(label), label)
    return found


def geo() -> Package:
    from ea.sources.lt import vrk_president

    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Lithuania's municipalities as they stood at the 1995-2015 council elections: the district municipality of "
        "Marijampolė before 2000, and the 2017 outlines of the municipalities",
    )
    p.external |= {LITHUANIA, TERRITORY, *(area(name) for name in areas().values())}
    p.add("scheme", code=SCHEME, polity_id=LITHUANIA, kind="precinct", rank=3, noun="polling district")
    p.add("scheme", code="lt.sav_1995", polity_id=LITHUANIA, kind="administrative", rank=1, noun="municipality")
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap boundaries)",
        title="geoBoundaries gbOpen Lithuania ADM2, 2017",
        url="https://www.geoboundaries.org/",
        license="Open Database License 1.0 (OpenStreetMap contributors), through geoBoundaries (CC BY 4.0)",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="OpenStreetMap contributors (Wambacher), through geoBoundaries (William & Mary geoLab)",
    )
    p.artifact(raw.cached(SOURCE, GEO), GEO_SOURCE)
    data = json.loads(raw.cached(SOURCE, GEO).path.read_bytes())
    found: dict[str, shapely.Geometry] = {}
    for feature in data["features"]:
        name = str(feature["properties"]["shapeName"])
        name = vrk_president.SHAPES.get(name, name.removesuffix(" savivaldybė"))
        try:
            found[municipality(name)] = feature_shape(feature["geometry"])
        except ValueError:
            raise ValueError(f"geoBoundaries municipality {name!r} has no name here") from None
    for name in areas().values():
        if name not in found:
            raise ValueError(f"no outline for {name}")
    old = shapely.union_all(
        [found[name] for name in ("Marijampolės savivaldybė", "Kalvarijos savivaldybė", "Kazlų Rūdos savivaldybė")]
    )
    old = shapely.set_srid(shapely.MultiPolygon([g for g in shapely.get_parts(old) if g.geom_type == "Polygon"]), 4326)
    id = area(HISTORICAL)
    p.add("area", id=id, scheme_code="lt.sav_1995")
    p.add("area_code", area_id=id, code=HISTORICAL, valid_from=None, valid_to=date(2000, 1, 1))
    p.add("area_parent", area_id=id, parent_id=TERRITORY)
    p.name(id, HISTORICAL, "lt", "official")
    shapes = {HISTORICAL: old, **found}
    for name, whole in shapes.items():
        p.add(
            "area_geometry",
            area_id=area(name),
            valid_from=None,
            valid_to=date(2000, 1, 1) if name == HISTORICAL else None,
            geom=shapely.to_wkb(whole, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(whole), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=GEO_SOURCE,
        )
    return p


def feature_shape(geometry: dict[str, object]) -> shapely.Geometry:
    whole = shape(geometry)
    if not whole.is_valid:
        whole = shapely.make_valid(whole)
    polygons = [g for g in shapely.get_parts(whole) if g.geom_type == "Polygon" and not g.is_empty]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def genitive(name: str) -> str:
    return name.removesuffix("savivaldybė") + "savivaldybės"


def council_body(name: str) -> str:
    return key("body", "council", name) if name == HISTORICAL else vrk.key("body", "council", name)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Lithuania's municipal councils and mayors, their electoral systems, and the parties and committees that stood "
        "at the 1995-2015 council elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {LITHUANIA, TERRITORY, *(vrk.key("body", "council", name) for name in areas().values())}
    p.external |= {vrk.key("office", "mayor", name) for name in areas().values()}
    p.add(
        "source",
        id=VRK_SOURCE,
        kind="official_body",
        publisher="Vyriausioji rinkimų komisija",
        title="Lietuvos Respublikos vyriausioji rinkimų komisija: savivaldybių tarybų ir merų rinkimų rezultatai",
        url=f"{SITE}/lt/rinkimai-ir-referendumai",
        license="Public record",
        attribution="Vyriausioji rinkimų komisija",
    )
    body = council_body(HISTORICAL)
    p.add("body", id=body, polity_id=LITHUANIA, kind="council")
    p.name(body, f"Council of {HISTORICAL}", "en", "official")
    p.name(body, f"{genitive(HISTORICAL)} taryba", "lt", "native", False)
    for code, (rules, method, start) in SYSTEMS.items():
        id = key("electoral_system", code)
        p.add(
            "electoral_system",
            id=id,
            polity_id=LITHUANIA,
            valid_from=start,
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for normalized, name in sorted(parties().items()):
        id = party(name)
        p.add(
            "party",
            id=id,
            kind="bloc" if "koalicij" in normalized else "list_vehicle" if "komitet" in normalized else "party",
            polity_id=LITHUANIA,
        )
        p.add("external_id", entity_id=id, scheme="lt.vrk_council.party", value=name)
        p.name(id, name, "lt", "official")
    return p


class State:
    def __init__(self) -> None:
        self.units: set[str] = set()
        self.people: set[str] = set()
        self.whole: list[str] = []
        self.stations = 0
        self.gaps: dict[str, list[str]] = {}

    def gap(self, code: str, text: str) -> None:
        self.gaps.setdefault(code, []).append(text)

    def __repr__(self) -> str:  # pragma: no cover
        return f"State({self.stations} stations, {len(self.whole)} whole)"


VALUES = {
    "result_set_id": pl.String,
    "unit_id": pl.String,
    "option_id": pl.String,
    "channel": pl.Int16,
    "measure": pl.Int16,
    "count_round": pl.Int16,
    "flags": pl.Int16,
    "value": pl.Int64,
}


def value(
    result_set: str, unit: str, option: str | None, channel: int, measure: int, amount: int
) -> tuple[object, ...]:
    return (result_set, unit, option, channel, measure, 0, 0, amount)


def add_unit(
    p: Package, state: State, day: date, sav: str, sav_number: int, number: int | None, name: str | None
) -> str:
    """One reporting unit: a polling district, a count an election commission made itself, or a whole municipality."""
    code = f"{day}:{sav_number}:{'all' if number is None else number}:{name or ''}"
    id = key("unit", code)
    if id not in state.units:
        state.units.add(id)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=LITHUANIA,
            scheme_code=SCHEME,
            code=code,
            area_relation="equals" if number in (None, 0) else "within",
            area_id=area(sav),
            kind="aggregate" if number in (None, 0) else "standard",
        )
    return id


def exact(whole: Count, stations: list[Count]) -> bool:
    """Whether the polling-district pages of a municipality add up to the municipality's own page."""
    if not stations or len({(x.number, x.name) for x in stations}) != len(stations):
        return False
    votes: Counter[int | None] = Counter()
    for x in stations:
        for row in x.rows:
            votes[row.number] += row.total
    registered = sum(x.registered or 0 for x in stations)
    return votes == Counter({r.number: r.total for r in whole.rows}) and registered == whole.registered


def add_council(
    p: Package,
    state: State,
    day: date,
    election: str,
    path: str,
    whole: Count,
    stations: list[Count],
    round_: int = 1,
    annulled: bool = False,
) -> None:
    sav = whole.sav
    contest = key("contest", day, "council", sav)
    seats = sum(r.seats for r in whole.rows)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=council_body(sav),
        district_area_id=area(sav),
        magnitude=None if annulled or seats == 0 else seats,
        round_number=round_,
        electoral_system_id=key("electoral_system", "list_council"),
        default_result_kind="counted",
    )
    place = sav.removesuffix(" savivaldybė")
    p.name(contest, f"Council of {sav}", "en", "official")
    p.name(contest, f"{genitive(sav)} taryba", "lt", "native", False)
    p.add("slug", path=f"{path}/council-{changeset.slug(place)}", entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    as_of = whole.stamp
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
    if annulled:
        p.add(
            "note",
            id=key("note", result_set, "annulled"),
            entity_id=result_set,
            kind="caveat",
            body_md=ANNULLED,
            lang="en",
        )
    by_station = exact(whole, stations)
    if not by_station:
        state.whole.append(f"{day} {sav}")
        p.add(
            "note", id=key("note", result_set, "whole"), entity_id=result_set, kind="caveat", body_md=WHOLE, lang="en"
        )
    options = {}
    for row in whole.rows:
        option = options[row.number] = key("option", contest, row.number)
        is_person = personal(row.label)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=None if is_person else party(row.label),
            ballot_position=row.number,
            ballot_label=row.label,
        )
        if row.seats and not annulled:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
    counts = stations if by_station else [whole]
    out: list[tuple[object, ...]] = []
    for count in counts:
        unit = add_unit(
            p,
            state,
            day,
            sav,
            whole.sav_number,
            count.number if by_station else None,
            count.name if by_station else None,
        )
        if by_station:
            state.stations += 1
        where = f"{sav.removesuffix(' savivaldybė')} {count.name or ''}".strip()
        votes = sum(r.total for r in count.rows)
        if count.valid is not None and votes != count.valid:
            state.gap("accounting.votes_valid", f"{where} ({votes} list votes, {count.valid} valid ballots)")
        if (
            count.cast is not None
            and count.valid is not None
            and count.invalid is not None
            and count.cast != count.valid + count.invalid
        ):
            state.gap(
                "accounting.ballots_sum", f"{where} ({count.cast} cast, {count.valid} valid, {count.invalid} invalid)"
            )
        if count.registered and count.cast and count.cast > count.registered:
            state.gap("accounting.turnout", f"{where} ({count.cast} cast, {count.registered} registered)")
        for measure, amount in ((2, count.registered), (3, count.cast), (4, count.valid), (5, count.invalid)):
            if amount is not None and not (count.number == 0 and amount == 0 and measure == 2):
                out.append(value(result_set, unit, None, 0, measure, amount))
        for row in count.rows:
            if row.number not in options:
                raise ValueError(f"{count.url}: list {row.number} is not on the municipality's page")
            out.append(value(result_set, unit, options[row.number], 1, 1, row.box))
            out.append(value(result_set, unit, options[row.number], 3, 1, row.post))
    p.frame("result_value", pl.DataFrame(out, schema=VALUES, orient="row"))
    for measure, amount in ((2, whole.registered), (3, whole.cast), (4, whole.valid), (5, whole.invalid)):
        if amount is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount)
            summed = sum(
                getattr(c, ("registered", "cast", "valid", "invalid")[measure - 2]) or 0
                for c in counts
                if not (c.number == 0 and measure == 2)
            )
            if summed != amount:
                name = ("registered voters", "ballots cast", "valid ballots", "invalid ballots")[measure - 2]
                state.gap(
                    "accounting.declared",
                    f"{sav.removesuffix(' savivaldybė')} {name} ({amount} printed, {summed} summed)",
                )
    for row in whole.rows:
        p.add("declared", result_set_id=result_set, option_id=options[row.number], measure=1, value=row.total)


def add_election(
    p: Package,
    day: date,
    english: str,
    native: str,
    path: str,
    kind: str,
    parent: str | None = None,
    rerun: str | None = None,
) -> str:
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=LITHUANIA,
        parent_id=parent,
        kind=kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
        rerun_of_id=rerun,
    )
    p.name(id, english, "en", "official")
    p.name(id, native, "lt", "native", False)
    p.add("slug", path=path, entity_id=id, is_current=True)
    return id


def artifacts(p: Package, *urls: str) -> None:
    for url in urls:
        p.artifact(raw.cached(SOURCE, url), VRK_SOURCE)


def build(year: Year) -> Package:
    day = year.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{ENGLISH[day]}, by polling district, from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {LITHUANIA, TERRITORY}
    p.external |= {area(name) for name in areas().values()}
    p.external |= {vrk.key("body", "council", name) for name in areas().values()}
    p.external |= {vrk.key("office", "mayor", name) for name in areas().values()}
    artifacts(p, year.bundle)
    state = State()
    data = council(day)
    path = f"lt/{day}-municipal"
    election = add_election(
        p,
        day,
        ENGLISH[day],
        f"{day.year} m. savivaldybių tarybų rinkimai",
        path,
        "local",
    )
    for sav in sorted(data.municipalities):
        add_council(
            p,
            state,
            day,
            election,
            path,
            data.municipalities[sav],
            data.stations.get(sav, []),
            annulled=sav in data.annulled,
        )
    if day == MAYORS.day:
        add_mayors(p, state, election, path)
    note(p, state, year)
    return p


def rerun() -> Package:
    """The repeat election in Švenčionys on 29 June 1997, after the Commission declared its March count invalid."""
    day = date(1997, 6, 29)
    first = council(date(1997, 3, 23))
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The repeat 1997 municipal council election in Švenčionys, by polling district, from the Central Electoral "
        "Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {LITHUANIA, TERRITORY}
    p.external |= {area(name) for name in areas().values()}
    p.external |= {vrk.key("body", "council", name) for name in areas().values()}
    artifacts(p, YEARS[1].bundle)
    state = State()
    election = add_election(
        p,
        day,
        "June 1997 Švenčionys municipal council repeat election",
        "1997 m. birželio 29 d. Švenčionių rajono savivaldybės tarybos pakartotiniai rinkimai",
        f"lt/{day}-municipal-by-election",
        "by_election",
        rerun=key("election", date(1997, 3, 23)),
    )
    p.external.add(key("election", date(1997, 3, 23)))
    for sav, (whole, stations) in sorted(first.rerun.items()):
        add_council(p, state, day, election, f"lt/{day}-municipal-by-election", whole, stations)
    note(p, state, YEARS[1])
    return p


def note(p: Package, state: State, year: Year) -> None:
    """Accept the accounting gaps the Commission's own printed figures cause, and say what each one is."""
    why = {
        "accounting.votes_valid": "list votes that differ from the valid ballots of the same count",
        "accounting.ballots_sum": "ballots cast that differ from the valid plus invalid ballots of the same count",
        "accounting.turnout": "more ballots cast than voters on the roll",
        "accounting.declared": (
            "a municipality figure that differs from the sum of its counts (a count made by the district election "
            "commission itself may print list votes but no ballot figures)"
        ),
    }
    for code, items in state.gaps.items():
        p.accept(
            code,
            f"{len(items)} in {year.day.year}: {why[code]}: "
            + "; ".join(items[:12])
            + (" ..." if len(items) > 12 else "")
            + ". They are the Commission's own printed figures, kept as published.",
        )


def add_mayors(p: Package, state: State, election: str, path: str) -> None:
    """The 2015 direct mayoral elections: every municipality's first round, and the second round where it was needed."""
    second_election = add_election(
        p,
        MAYOR_DAYS[1],
        "2015 Lithuanian mayoral elections, second round",
        "2015 m. merų rinkimai, pakartotinis balsavimas",
        f"lt/{MAYOR_DAYS[1]}-municipal-runoff",
        "runoff",
        parent=election,
    )
    first = mayors(1)
    second = mayors(2)
    previous: dict[str, str] = {}
    for round_, data in ((1, first), (2, second)):
        day = MAYOR_DAYS[round_ - 1]
        where = election if round_ == 1 else second_election
        for sav in sorted(data.declared):
            protocols = data.stations[sav]
            polling, registered, cast, invalid, valid = data.declared[sav]
            if len([x for x in protocols if x.number != 0]) != polling:
                raise ValueError(f"{day} {sav}: {len(protocols)} polling districts, the page prints {polling}")
            contest = key("contest", day, "mayor", sav)
            p.add(
                "contest",
                id=contest,
                election_id=where,
                kind="office",
                office_id=vrk.key("office", "mayor", sav),
                district_area_id=area(sav),
                magnitude=1,
                round_number=round_,
                previous_round_id=previous.get(sav) if round_ == 2 else None,
                electoral_system_id=key("electoral_system", "two_round"),
                default_result_kind="counted",
            )
            previous[sav] = contest
            place = sav.removesuffix(" savivaldybė")
            p.name(contest, f"Mayor of {sav}", "en", "official")
            p.name(contest, f"{place}savivaldybės meras", "lt", "native", False)
            p.add(
                "slug",
                path=f"{path if round_ == 1 else f'lt/{day}-municipal-runoff'}/mayor-{changeset.slug(place)}",
                entity_id=contest,
                is_current=True,
            )
            result_set = key("result_set", contest)
            p.add(
                "result_set",
                id=result_set,
                election_id=where,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=VRK_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en"
            )
            names = list(dict.fromkeys(c.name for x in protocols for c in x.rows))
            mayor, label = first.elected[sav]
            votes: Counter[str] = Counter()
            for x in protocols:
                for c in x.rows:
                    votes[c.name] += c.total
            # the Commission's table of mayors was updated after later repeat elections and replacements, so a
            # mayor counts as elected here only when the count's leader is the person it names
            won = (round_ == 2 or sav not in second.declared) and votes.most_common(1)[0][0] == mayor
            if (round_ == 2 or sav not in second.declared) and not won:
                state.whole.append(f"mayor {day} {sav}")
            options = {}
            for name in names:
                option = options[name] = key("option", contest, name)
                shown = vrk.title(name)
                p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=shown)
                person = key("person", MAYORS.day.year, "mayor", sav, name)
                if person not in state.people:
                    state.people.add(person)
                    p.add("person", id=person)
                    p.name(person, shown, "lt", "official")
                chosen = name == mayor
                independent = chosen and label == "Save išsikėlęs"
                listed = chosen and not independent and normal(label) in parties()
                candidacy = key("candidacy", contest, name)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=shown,
                    member_of_party_id=party(label) if listed else None,
                    is_independent=independent or None,
                )
                if chosen and won:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            out: list[tuple[object, ...]] = []
            for x in protocols:
                unit = add_unit(p, state, day, sav, x.sav_number, x.number, x.name)
                state.stations += 1
                for measure, amount in ((2, x.registered), (3, x.cast), (4, x.valid), (5, x.invalid)):
                    if not (x.number == 0 and amount == 0):
                        out.append(value(result_set, unit, None, 0, measure, amount))
                for c in x.rows:
                    out.append(value(result_set, unit, options[c.name], 1, 1, c.box))
                    out.append(value(result_set, unit, options[c.name], 3, 1, c.post))
            p.frame("result_value", pl.DataFrame(out, schema=VALUES, orient="row"))
            for measure, amount in ((2, registered), (3, cast), (4, valid), (5, invalid)):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{y.day}" for y in YEARS), f"{SOURCE}/1997-06-29"]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        build(year).write()
    rerun().write()


def emit() -> None:
    package.run("load", packages())
