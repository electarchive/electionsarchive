"""Seats and elected members of Finland's parliamentary elections of 1983 to 1999, from Statistics Finland's publications.

The StatFin tables that `statfin_eduskunta` builds from hold votes only. The seats come from Statistics Finland's own
election publications:

- 1983, 1987, and 1991: the printed table of the number of persons elected by party and constituency in the
  publication of each election (Suomen virallinen tilasto, Kansanedustajain vaalit). The scans carry a text layer too
  damaged to read, so `statfin_eduskunta_seats.tsv` holds the transcription of those three tables. A build refuses it
  unless every constituency's seats add up to the constituency's size and every party's seats to the party's national
  total, both as printed in the same table.
- 1995 and 1999: the seat tables Statistics Finland published with each election's statistics, which are read at
  build time, and its list of candidates with their votes and comparison figures, in which the elected carry an
  asterisk. The elected are read from that list, which a build checks against the seat table.

Parties in the seat tables are matched to the options of the vote tables by the table's party code. The lists that the
vote tables put in their residual groups are matched by their votes.
"""

import functools
import html
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import xlrd

from ea import raw

SOURCE = "fi.statfin_eduskunta"
OTOS = "https://otos.stat.fi/bitstreams"
STAT = "https://stat.fi/tk/he/vaalit"
YEARS = (1983, 1987, 1991, 1995, 1999)

# One printed publication for each of the three elections whose seats are transcribed
PUBLICATIONS = {
    1983: f"{OTOS}/f8b99943-5e7c-4ea5-af5e-5f4986b4a4f9/download",
    1987: f"{OTOS}/c5100ee0-7140-4ba5-8a61-028f410936cc/download",
    1991: f"{OTOS}/6ab50613-de16-4cd2-8cee-3a3fe93fd379/download",
}
# The printed page of the transcribed table in each publication
PAGES = {1983: "table 3, page 87", 1987: "table 2, page 134", 1991: "table 3, page 164"}
SEAT_PAGES = {1995: f"{STAT}/vaalit95/edpaikatev95.html", 1999: f"{STAT}/vaalit99/vlkm99.html"}
LISTS = {1995: f"{STAT}/vaalit95/risvaa.xls", 1999: f"{STAT}/vaalit99/ehdokas.xls"}
NATIONAL = f"{STAT}/vaalit99/edpaikat4599.html"

# Seat-table columns to the vote tables' party codes (see `statfin_eduskunta.parties`). SMP, the Rural Party, is code
# 02, which the vote tables name for its successor; SKDL, the democratic league, is code 06, the Left Alliance's.
# DEVA, the Democratic Alternative, is in the vote tables' residual group 44, and the lists the seat tables call
# "others" are in group 99.
CODES: dict[int, dict[str, str]] = {
    1983: {"SDP": "01", "KOK": "03", "KESK": "04", "SKDL": "06", "SMP": "02", "RKP": "07", "SKL": "08", "POP": "77"},
    1987: {
        "SDP": "01",
        "KOK": "03",
        "SKDL": "06",
        "KESK": "04",
        "SKL": "08",
        "SMP": "02",
        "RKP": "07",
        "DEVA": "44",
        "VIHR": "05",
    },
    1991: {
        "KESK": "04",
        "SDP": "01",
        "KOK": "03",
        "VAS": "06",
        "SMP": "02",
        "RKP": "07",
        "SKL": "08",
        "LKP": "46",
        "VIHR": "05",
    },
    1995: {
        "SDP": "01",
        "KESK": "04",
        "KOK": "03",
        "VAS": "06",
        "RKP": "07",
        "VIHR": "05",
        "SKL": "08",
        "NUORS": "49",
        "SMP": "02",
    },
    1999: {
        "SDP": "01",
        "KESK": "04",
        "KOK": "03",
        "VAS": "06",
        "VIHR": "05",
        "RKP": "07",
        "SKL": "08",
        "PS": "02",
        "REM": "51",
    },
}
# The residual column of each table, whose seats go to the group of the party that won them
RESIDUAL = {1983: "MUUT", 1987: "MUUT", 1991: "ALAND", 1995: "MUUT", 1999: "MUUT"}
OTHERS = "99"
ABOVE = {1995: {"PIR": "50"}}  # 1995: the Ecological Party's seat in Pirkanmaa, by its list's votes

TABLE = Path(__file__).with_name("statfin_eduskunta_seats.tsv")

# Seat-table columns of the two web pages, after the total
COLUMNS = {
    1995: ["SDP", "KESK", "KOK", "VAS", "RKP", "VIHR", "SKL", "NUORS", "SMP", "MUUT"],
    1999: ["SDP", "KESK", "KOK", "VAS", "VIHR", "RKP", "SKL", "PS", "REM", "MUUT"],
}

# Lists in the candidate files to the vote tables' party codes
LIST_CODES = (
    ("SOSIALIDEMOKRAAT", "01"),
    ("KESKUSTA", "04"),
    ("KOKOOMUS", "03"),
    ("VASEMMISTOLIITTO", "06"),
    ("RUOTSALAINEN", "07"),
    ("VIHRE", "05"),
    ("KRISTILLINEN", "08"),
    ("MAASEUDUN", "02"),
    ("PERUSSUOMALAISET", "02"),
    ("NUORSUOMALAINEN", "49"),
    ("EKOLOGINEN", "50"),
    ("REMONTTI", "51"),
    ("MUUT", "99"),
)

# The words by which a heading names a constituency, in the order they are tried, with the constituency code
HEADINGS = (
    ("ahvenanmaa", "AHV"),
    ("helsinki", "HEL"),
    ("helsingin", "HEL"),
    ("uudenmaan", "UUS"),
    ("uusimaa", "UUS"),
    ("varsinais", "VAR"),
    ("turun läänin etel", "VAR"),
    ("turun läänin pohj", "SAT"),
    ("satakunta", "SAT"),
    ("turku south", "VAR"),
    ("turku north", "SAT"),
    ("pirkanmaa", "PIR"),
    ("hämeen läänin pohj", "PIR"),
    ("häme north", "PIR"),
    ("hämeen läänin etel", "HÄM"),
    ("häme south", "HÄM"),
    ("häme - tavast", "HÄM"),
    ("häme-tavast", "HÄM"),
    ("kymen", "KYM"),
    ("kymi", "KYM"),
    ("mikkeli", "ESA"),
    ("mikkelin", "ESA"),
    ("pohjois-karjala", "KAR"),
    ("north karelia", "KAR"),
    ("kuopio", "PSA"),
    ("kuopion", "PSA"),
    ("keski-suomi", "KES"),
    ("keski-suomen", "KES"),
    ("central finland", "KES"),
    ("vaasa", "VAA"),
    ("vaasan", "VAA"),
    ("oulu", "OUL"),
    ("oulun", "OUL"),
    ("lapin", "LAP"),
    ("lappi", "LAP"),
    ("lapland", "LAP"),
)


def constituency(text: str) -> str | None:
    lowered = text.lower()
    for words, code in HEADINGS:
        if lowered.startswith(words) or (len(words) > 9 and words in lowered):
            return code
    return None


def sources() -> dict[int, list[str]]:
    """The URLs of every file that holds a year's seats or elected, in the order they are cited."""
    out: dict[int, list[str]] = {}
    for year in YEARS:
        out[year] = [PUBLICATIONS[year]] if year in PUBLICATIONS else [SEAT_PAGES[year], LISTS[year]]
        out[year].append(NATIONAL)
    return out


def fetch() -> None:
    for urls in sources().values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=Path(url).suffix if url.endswith((".html", ".xls")) else ".pdf")


def cited(year: int) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in sources()[year]]


# ---------------------------------------------------------------------------------------------------------------------
# National seats of all elections


def _cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
    ]


def _page(url: str) -> list[list[str]]:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
    return [_cells(row) for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL | re.IGNORECASE)]


def _number(text: str) -> int:
    return 0 if text in ("", "-", "_") else int(text)


@functools.cache
def national() -> dict[int, dict[str, int]]:
    """Each party's national seats in each of the five elections, as the table of 1945 to 1999 prints them."""
    rows = _page(NATIONAL)
    header = next(row for row in rows if len(row) > 2 and "Vuosi" in row[1])
    names = [re.split(r"[ ]", re.sub(r"\d\)", "", cell).strip())[0].upper().rstrip(".") for cell in header[2:]]
    out: dict[int, dict[str, int]] = {}
    for row in rows:
        if row and row[0].isdigit() and int(row[0]) in YEARS:
            out[int(row[0])] = dict(zip(names, (_number(cell) for cell in row[1:]), strict=True))
    return out


# What each seat column of an election counts as in the national table of 1945 to 1999, and the columns that table
# does not split by party, which it sums as `Ål.saml.` and `Muut`
NAMED = {
    1983: {"SDP": "SDP", "KOK": "KOK", "KESK": "KESK", "SKDL": "VAS", "SMP": "PS", "RKP": "RKP", "SKL": "SKL"},
    1987: {
        "SDP": "SDP",
        "KOK": "KOK",
        "SKDL": "VAS",
        "DEVA": "VAS",
        "KESK": "KESK",
        "SKL": "SKL",
        "SMP": "PS",
        "RKP": "RKP",
        "VIHR": "VIHR",
    },
    1991: {
        "KESK": "KESK",
        "SDP": "SDP",
        "KOK": "KOK",
        "VAS": "VAS",
        "SMP": "PS",
        "RKP": "RKP",
        "SKL": "SKL",
        "LKP": "LKP",
        "VIHR": "VIHR",
    },
    1995: {
        "SDP": "SDP",
        "KESK": "KESK",
        "KOK": "KOK",
        "VAS": "VAS",
        "RKP": "RKP",
        "VIHR": "VIHR",
        "SKL": "SKL",
        "NUORS": "NUORS",
        "SMP": "PS",
    },
    1999: {
        "SDP": "SDP",
        "KESK": "KESK",
        "KOK": "KOK",
        "VAS": "VAS",
        "VIHR": "VIHR",
        "RKP": "RKP",
        "SKL": "SKL",
        "PS": "PS",
    },
}


def check_national(year: int) -> None:
    """Refuse a year whose seats by party differ from the national table's."""
    ours: Counter[str] = Counter()
    for column, seats in column_totals(year).items():
        ours[NAMED[year].get(column, "REST")] += seats
    theirs = {name: seats for name, seats in national()[year].items() if seats}
    rest = sum(seats for name, seats in theirs.items() if name in ("ÅL", "ÅL.SAML", "MUUT"))
    named = {name: seats for name, seats in theirs.items() if name not in ("ÅL", "ÅL.SAML", "MUUT")}
    if {**ours, "REST": 0} != {**named, "REST": 0} and {k: v for k, v in ours.items() if k != "REST"} != named:
        raise ValueError(f"{year}: seats by party {dict(ours)} differ from the national table's {theirs}")
    if ours["REST"] != rest:
        raise ValueError(f"{year}: other seats {ours['REST']} differ from the national table's {rest}")


# ---------------------------------------------------------------------------------------------------------------------
# Seats


@functools.cache
def printed(year: int) -> dict[tuple[str, str], int]:
    """The seats of each party column in each constituency, as the source prints them, keyed by (constituency, column)."""
    if year in PUBLICATIONS:
        frame = pl.read_csv(TABLE, separator="\t", schema_overrides={"year": pl.Int64, "seats": pl.Int64})
        rows = frame.filter(pl.col("year") == year)
        return {(r["constituency"], r["party"]): r["seats"] for r in rows.iter_rows(named=True)}
    out: dict[tuple[str, str], int] = {}
    heading: str | None = None
    seen: set[str] = set()
    for row in _page(SEAT_PAGES[year]):
        if len(row) == 1:
            heading = constituency(row[0])  # 1999 names each constituency in a row of its own
            continue
        label = row[0]
        if year == 1995 and len(row) >= 12 and label and not label.startswith("Koko"):
            heading = constituency(label)
        elif not (year == 1999 and label.startswith("Yhteens")):
            continue
        if heading is None or heading in seen:
            continue
        seen.add(heading)
        total = _number(row[1])
        values = [_number(cell) for cell in row[2:]]
        if sum(values) != total:
            raise ValueError(f"{year} {heading}: seats {values} do not add up to {total}")
        for column, value in zip(COLUMNS[year], values, strict=True):
            if value:
                out[(heading, column)] = value
        heading = None
    if len(seen) != 15:
        raise ValueError(f"{year}: the seat table has {len(seen)} constituencies")
    return out


def column_totals(year: int) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for (_, column), seats in printed(year).items():
        totals[column] += seats
    return dict(totals)


@functools.cache
def sizes(year: int) -> dict[str, int]:
    """The number of seats of each constituency, which the seat table fixes."""
    out: Counter[str] = Counter()
    for (code, _), seats in printed(year).items():
        out[code] += seats
    return dict(out)


# ---------------------------------------------------------------------------------------------------------------------
# Elected members of 1995 and 1999


@dataclass(frozen=True)
class Member:
    constituency: str
    party: str  # the vote tables' party code
    name: str  # as printed, family name first
    votes: int
    comparison: str
    position: int  # in the order the list is printed, which is by votes


@functools.cache
def candidates(year: int) -> tuple[list[Member], dict[tuple[str, str], int]]:
    """The candidates marked as elected in the list of candidates, and the votes of each list, summed over its
    candidates, by (constituency, party code)."""
    sheet = xlrd.open_workbook(str(raw.cached(SOURCE, LISTS[year]).path)).sheet_by_index(0)
    here: str | None = None
    party = ""
    kind = "blank"
    position = 0
    elected: list[Member] = []
    votes: defaultdict[tuple[str, str], int] = defaultdict(int)
    for index in range(4, sheet.nrows):
        first, second, third = (sheet.row_values(index) + ["", "", ""])[:3]
        text = first.strip() if isinstance(first, str) else ""
        if isinstance(second, float):
            if here is None or not party:
                raise ValueError(f"{year}: a candidate on row {index + 1} stands on no known list")
            position += 1
            votes[(here, party)] += int(second)
            if text.startswith("*"):
                elected.append(Member(here, party, text.lstrip("*").strip(), int(second), str(third).strip(), position))
            kind = "candidate"
            continue
        if not text:
            kind = "blank"
            continue
        if kind != "header" and text.isupper():
            found = constituency(text)
            if found is not None:
                here, party = found, ""
            else:
                party = next((code for word, code in LIST_CODES if word in text), "?" + text)
                position = 0
        kind = "header"
    return elected, dict(votes)


def outcomes(year: int) -> dict[str, dict[str, int]]:
    """The seats of each constituency by the vote tables' party code."""
    out: defaultdict[str, dict[str, int]] = defaultdict(dict)
    for (code, column), n in printed(year).items():
        if column in CODES[year]:
            option = CODES[year][column]
        elif column == RESIDUAL[year]:
            option = ABOVE.get(year, {}).get(code, OTHERS)
        else:
            raise ValueError(f"{year}: no party code for the seat column {column}")
        out[code][option] = out[code].get(option, 0) + n
    return dict(out)


def person_name(printed_name: str) -> str:
    """A candidate's name in the order given name first, from the printed family-name-first form."""
    name = re.sub(r"\s*\([^)]*\)", "", printed_name).strip()
    parts = name.split()
    take = 1
    while take < len(parts) - 1 and parts[take - 1].islower():
        take += 1
    surname, given = parts[:take], parts[take:]
    return " ".join([*given, *surname])
