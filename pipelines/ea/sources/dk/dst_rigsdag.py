"""Folketing elections 1849-1950 from Statistics Denmark's historical election volumes.

Statistics Denmark publishes its printed series of Folketing election reports (Statistisk Tabelværk and
Statistiske Meddelelser) as scanned PDFs, one volume per election or group of elections. The text layer of the
scans is too noisy for its figures to be used, so each table is transcribed from the page images into a
tab-separated file in `dst_rigsdag/`, checked against the totals the volume prints (every row against its own
total, every county block against its subtotal, every area against its total), and loaded from there. Each figure
keeps its page, and the box of its printed cell where the text layer locates it.

Two kinds of table are read:

- From April 1920, proportional representation: the per-nomination-district table "Stemmefordelingen" (Tabel 1 a-c
  of the volume's second part), one row per nomination district (opstillingskreds) with each party's votes.
- From 1901 to 1918, single-member constituencies: the constituency tables with the registered voters, the voters
  who voted, each candidate's votes, and the invalid and blank ballots, plus the by-elections the volume appends. In April 1918 the capital (Copenhagen and Frederiksberg) instead
  elected 24 members by list, which is its own table (`cph-1918-04-22.tsv`): each polling district's turnout and
  votes for each list. The turnout of each nomination district of April 1920 is a third table (`tab-1920-04-26.tsv`),
  read alongside its party votes.
"""

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from statistics import median
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.dk import dst_rigsdag_sheets as sheets

SOURCE = "dk.dst_rigsdag"
BASE = "https://www.dst.dk/pubfile"
PAGE = "https://www.dst.dk/da/Statistik/udgivelser/VisPub?cid={cid}"
DENMARK = str(uuid5(package.NAMESPACE, "polity\x1fDK"))
LICENSE = "Statistics Denmark data may be reused with attribution to Statistics Denmark as the source"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
DIGITIZED = 8
OPSTILLINGSKREDS = "dk.opstillingskreds"
VALGKREDS = "dk.folketingskreds"

# Every volume of the series from 1849 to 1950, by publication id; the first file is the election volume.
FILES = {
    20242: ["valg1849"],
    20241: ["valg1852-1853"],
    20240: ["valg1854-1861"],
    20239: ["valg1873"],
    20238: ["valg1876", "valgberet"],
    20236: ["valg1881"],
    20235: ["valg1887", "valgberet"],
    20233: ["valg1895"],
    20232: ["valg1901"],
    20231: ["valg1903"],
    20230: ["valg1906"],
    20229: ["valg1909"],
    20228: ["valg1910"],
    20227: ["valg1913"],
    20226: ["valg1915"],
    20225: ["valg1918"],
    20224: ["valg1920", "valgjuli", "valgsep"],
    20223: ["valg1924"],
    20222: ["valg1926"],
    20221: ["valg1929"],
    20220: ["valg1932"],
    20219: ["valg1935"],
    20218: ["valg1939"],
    20217: ["valg1943"],
    20216: ["valg1945"],
    20215: ["valg1947"],
    20214: ["valg1950"],
}


@dataclass(frozen=True)
class Volume:
    cid: int
    name: str
    title: str  # the volume's title as printed
    offset: int = 0  # the PDF page of a printed page, less the printed page number


# Proportional representation, from July 1920: election day -> the volume and its transcribed table.
PR = {
    date(1920, 4, 26): Volume(20224, "valg1920", "Folketingsvalget den 26. April 1920"),
    date(1920, 7, 6): Volume(20224, "valgjuli", "Rigsdagsvalgene i Juli-August 1920"),
    date(1920, 9, 21): Volume(20224, "valgsep", "Rigsdagsvalgene i September-Oktober 1920"),
    date(1924, 4, 11): Volume(20223, "valg1924", "Folketingsvalget den 11. April 1924"),
    date(1926, 12, 2): Volume(20222, "valg1926", "Folketingsvalget den 2. December 1926"),
    date(1929, 4, 24): Volume(20221, "valg1929", "Folketingsvalget den 24. April 1929"),
    date(1932, 11, 16): Volume(20220, "valg1932", "Folketingsvalget den 16. November 1932"),
    date(1935, 10, 22): Volume(20219, "valg1935", "Folketingsvalget den 22. Oktober 1935"),
    date(1939, 4, 3): Volume(20218, "valg1939", "Rigsdagsvalgene og Folkeafstemningen 1939"),
    date(1943, 3, 23): Volume(20217, "valg1943", "Rigsdagsvalgene 1943"),
    date(1945, 10, 30): Volume(20216, "valg1945", "Rigsdagsvalgene 1945"),
    date(1947, 10, 28): Volume(20215, "valg1947", "Folketingsvalget den 28. Oktober 1947"),
    date(1950, 9, 5): Volume(20214, "valg1950", "Folketingsvalget den 5. september 1950"),
}
# Single-member constituencies: the year of the volume -> the volume.
SINGLE = {
    1918: Volume(20225, "valg1918", "Rigsdagsvalgene April-Maj 1918 med Suppleringsvalg i Tiden 1915-18"),
    1920: Volume(20224, "valg1920", "Folketingsvalget den 26. April 1920"),
    1901: Volume(20232, "valg1901", "Folketingsvalgene den 3. April 1901 med Supplerings- og Omvalg 1898-1901"),
    1903: Volume(20231, "valg1903", "Folketingsvalgene 1903"),
    1906: Volume(20230, "valg1906", "Folketingsvalgene 1906"),
    1909: Volume(20229, "valg1909", "Folketingsvalgene 1909", -1),
    1910: Volume(20228, "valg1910", "Folketingsvalgene 1910"),
    1913: Volume(20227, "valg1913", "Folketingsvalgene 1913"),
    1915: Volume(20226, "valg1915", "Folketingsvalgene den 7de Maj 1915 med Suppleringsvalg 1913-15"),
}


def url(cid: int, name: str) -> str:
    return f"{BASE}/{cid}/{name}"


def fetch() -> None:
    for cid, names in FILES.items():
        for name in names:
            raw.fetch(SOURCE, url(cid, name), suffix=".pdf")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DST_SOURCE = key("source", "dst")
BODY = key("body", "folketing")
SYSTEM_SINGLE = key("electoral_system", "single")
SYSTEM_PR = key("electoral_system", "pr1920")


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("æ", "ae").replace("ø", "oe").replace("Æ", "Ae"))
    folded = folded.replace("Ø", "Oe").replace("å", "aa").replace("Å", "Aa")
    ascii_text = folded.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


# Parties, by the words of a printed column header or the party letter of a candidate.
PARTIES: list[tuple[str, str, str]] = [
    # (pattern on the folded header, party key, Danish name)
    (r"^centrum", "centrum", "Centrum"),
    (r"venstre ?social", "venstresocialisterne", "Venstresocialisterne"),
    (r"frie social", "frie-socialdemokrater", "Det frie Socialdemokrati"),
    (r"kommunist", "kommunistiske-parti", "Danmarks Kommunistiske Parti"),
    (r"national ?-?soc|nationalsoc", "dnsap", "Danmarks National-Socialistiske Arbejderparti"),
    (r"retsforb|retsparti", "retsforbundet", "Danmarks Retsforbund"),
    (r"frie folke", "frie-folkeparti", "Det frie Folkeparti"),
    (r"erhverv", "erhvervspartiet", "Erhvervspartiet"),
    (r"konserv", "konservative-folkeparti", "Det Konservative Folkeparti"),
    (r"radikal", "radikale-venstre", "Det Radikale Venstre"),
    (r"landmand", "landmandspartiet", "Landmandspartiet"),
    (r"selvstyre", "selvstyrepartiet", "Selvstyrepartiet"),
    (r"slesvig", "slesvigsk-parti", "Slesvigsk Parti"),
    (r"samfund", "samfundspartiet", "Samfundspartiet"),
    (r"bonde", "bondepartiet", "Bondepartiet"),
    (r"dansk samling", "dansk-samling", "Dansk Samling"),
    (r"samvirke", "nationalt-samvirke", "Nationalt Samvirke"),
    (r"socialdemokrat", "socialdemokratiet", "Socialdemokratiet"),
    (r"hovedstadens venstre", "hovedstadens-venstre", "Hovedstadens Venstre"),
    (r"^venstre$", "venstre", "Venstre"),
]


def party_of(header: str) -> tuple[str, str] | None:
    """The party key and name of a column header; None for lists outside the parties."""
    text = re.sub(r"^[A-Z] ", "", header).lower().replace("-", " ")
    text = " ".join(text.split())
    if text.startswith(("udenfor", "uden for")):
        return None
    for pattern, party, name in PARTIES:
        if re.search(pattern, text):
            return party, name
    raise LookupError(f"no party for column {header!r}")


@cache
def artifact(volume: Volume) -> raw.Artifact:
    return raw.cached(SOURCE, url(volume.cid, volume.name))


@cache
def page_sizes(pdf: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "9999", str(pdf)], capture_output=True, text=True, check=True)
    found: dict[int, tuple[float, float]] = {}
    for match in re.finditer(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", out.stdout):
        found[int(match[1])] = (float(match[2]), float(match[3]))
    return found


type Box = tuple[float, float, float, float]


@cache
def text_lines(pdf: Path, page: int) -> list[list[tuple[str, Box]]]:
    """The words of a page's text layer, grouped into printed lines."""
    xhtml = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    words = [
        (t, (float(a), float(b), float(c), float(d)))
        for a, b, c, d, t in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', xhtml
        )
    ]
    rows: list[tuple[float, list[tuple[str, Box]]]] = []
    for word in sorted(words, key=lambda w: (w[1][1] + w[1][3]) / 2):
        centre = (word[1][1] + word[1][3]) / 2
        if rows and abs(rows[-1][0] - centre) < max(2.6, (word[1][3] - word[1][1]) * 0.42):
            rows[-1][1].append(word)
        else:
            rows.append((centre, [word]))
    return [sorted(r, key=lambda w: w[1][0]) for _, r in rows]


def locate(pdf: Path, page: int, rows: list[list[int]]) -> list[list[Box | None]]:
    """Find each printed number of a page's transcribed rows in the text layer, to give it a box.

    A row is matched to the text-layer line that holds most of its numbers; a number the text layer misread takes
    the column's span (from the numbers it did read in that column) at the row's line.
    """
    lines = text_lines(pdf, page)
    tokens = [[(re.sub(r"\D", "", t), b) for t, b in line] for line in lines]
    found: list[list[Box | None]] = []
    used: set[int] = set()
    columns: dict[int, list[tuple[float, float]]] = {}
    matches: list[int | None] = []
    for values in rows:
        best, score = None, 0
        for i, line in enumerate(tokens):
            if i in used:
                continue
            here = sum(1 for v in values if v >= 10 and any(t == str(v) for t, _ in line))
            if here > score:
                best, score = i, here
        if best is not None and score >= max(1, sum(1 for v in values if v >= 10) // 3):
            used.add(best)
            matches.append(best)
        else:
            matches.append(None)
    for values, match in zip(rows, matches, strict=True):
        boxes: list[Box | None] = [None] * len(values)
        if match is not None:
            taken: set[int] = set()
            for k, v in enumerate(values):
                for j, (t, b) in enumerate(tokens[match]):
                    if j not in taken and v >= 10 and t == str(v):
                        boxes[k] = b
                        taken.add(j)
                        columns.setdefault(k, []).append((b[0], b[2]))
                        break
        found.append(boxes)
    width = max((len(v) for v in rows), default=0)
    known = {k: (median(a for a, _ in c), median(b for _, b in c)) for k, c in columns.items() if c}
    for k in range(width):
        if k in known:
            continue
        # a column the text layer never read (all dashes): place it between the nearest read columns
        before = max((j for j in known if j < k), default=None)
        after = min((j for j in known if j > k), default=None)
        if before is None or after is None:
            continue
        step = (known[after][1] - known[before][1]) / (after - before)
        right = known[before][1] + step * (k - before)
        known[k] = (right - step + 3, right)
    spans = {
        i: (min(b[1] for _, b in lines[m]), max(b[3] for _, b in lines[m]))
        for i, m in enumerate(matches)
        if m is not None
    }
    for i, (values, boxes) in enumerate(zip(rows, found, strict=True)):
        if i in spans:
            top, bottom = spans[i]
        else:
            # a row the text layer lost: place it between the nearest located rows above and below
            before = max((j for j in spans if j < i), default=None)
            after = min((j for j in spans if j > i), default=None)
            if before is None or after is None:
                continue
            share = (i - before) / (after - before)
            top = spans[before][0] + (spans[after][0] - spans[before][0]) * share
            bottom = top + (spans[before][1] - spans[before][0])
        for k in range(len(values)):
            if boxes[k] is None and k in known:
                left, right = known[k]
                boxes[k] = (min(left, right - 4), top, right, bottom)
    return found


@cache
def numbers_on(pdf: Path, page: int) -> dict[int, list[Box]]:
    """Every number of a page's text layer with its box; a thin-space thousands group is joined to its head."""
    found: dict[int, list[Box]] = {}
    for line in text_lines(pdf, page):
        words = [(re.sub(r"[.,:;]+$", "", t), b) for t, b in line]
        k = 0
        while k < len(words):
            text, box = words[k]
            if text.isdigit():
                if (
                    k + 1 < len(words)
                    and len(text) <= 3
                    and re.fullmatch(r"\d{3}", words[k + 1][0])
                    and words[k + 1][1][0] - box[2] < 4
                ):
                    joined = (box[0], box[1], words[k + 1][1][2], max(box[3], words[k + 1][1][3]))
                    found.setdefault(int(text + words[k + 1][0]), []).append(joined)
                found.setdefault(int(text), []).append(box)
            k += 1
    return found


def find(pdf: Path, page: int, value: int) -> Box | None:
    """The box of a printed number on a page, when the text layer read it exactly once."""
    boxes = numbers_on(pdf, page).get(value, [])
    return boxes[0] if len(boxes) == 1 and value >= 10 else None


def normalized(pdf: Path, page: int, box: Box | None) -> tuple[float, float, float, float]:
    width, height = page_sizes(pdf)[page]
    if box is None:
        return (0.0, 0.0, 1.0, 1.0)
    pad = 1.0
    return (
        max(0.0, (box[0] - pad) / width),
        max(0.0, (box[1] - pad) / height),
        min(1.0, (box[2] + pad) / width),
        min(1.0, (box[3] + pad) / height),
    )


# The party letters of the single-member tables, from each volume's legend (Anm.).
LETTERS: dict[str, tuple[str, str] | None] = {
    "A.": ("agrarer", "Agrarerne"),
    "F. V.": ("forhandlende-venstre", "Det forhandlende Venstre"),
    "H.": ("hoejre", "Højre"),
    "V. R.": ("venstrereformpartiet", "Venstrereformpartiet"),
    "S.": ("socialdemokratiet", "Socialdemokratiet"),
    "Fk.": ("frikonservative", "De Frikonservative"),
    "M. V.": ("moderate-venstre", "Moderate Venstre"),
    "R.": ("radikale-venstre", "Det Radikale Venstre"),
    "V.": ("venstre", "Venstre"),
    "K.": ("konservative-folkeparti", "Det Konservative Folkeparti"),
    "NH.": ("det-ny-hoejre", "Det ny Højre"),
    "U.": None,  # outside the parties
    "L.": None,  # independent (løsgænger)
    "M.": None,  # an opponent of the 1913 constitutional bill without a party
    "": None,
}


def letter_party(letter: str, year: int) -> tuple[str, str] | None:
    text = " ".join(letter.replace(".", ". ").split()).replace(" .", ".")
    if text.startswith(("V. (", "V.(")):
        # 1909: Venstre men by their stand on the defence plans; 1913: Venstre opposing the constitutional bill
        return LETTERS["V. R."] if year < 1910 else LETTERS["V."]
    if text == "V." and year < 1910:
        return LETTERS["V. R."]
    text = re.sub(r"^V\. R\. \(.*\)$", "V. R.", text)  # 1909: the wings of Venstrereformpartiet
    # anything else (a mixed letter such as "H. A." or "R. L.", or a description such as "Afholdskandidat") is
    # a candidate outside the parties
    return LETTERS.get(text)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Folketing of the Rigsdag, its parties, and its electoral systems, from Statistics Denmark's historical "
        "election volumes",
    )
    p.external |= {DENMARK}
    p.add(
        "source",
        id=DST_SOURCE,
        kind="official_body",
        publisher="Danmarks Statistik",
        title="Folketingsvalg (historiske): Statistisk Tabelværk and Statistiske Meddelelser election volumes",
        url=PAGE.format(cid=20242),
        license=LICENSE,
        attribution="Danmarks Statistik",
    )
    for volume in volumes():
        p.artifact(artifact(volume), DST_SOURCE)
    p.add("scheme", code=VALGKREDS, polity_id=DENMARK, kind="electoral_district", rank=3, noun="constituency")
    p.add("body", id=BODY, polity_id=DENMARK, kind="lower_house")
    p.name(BODY, "Folketing of the Rigsdag", "en", "official")
    p.name(BODY, "Rigsdagens Folketing", "da", "native", False)
    p.add(
        "note",
        id=key("note", BODY),
        entity_id=BODY,
        kind="methodology",
        lang="en",
        body_md="The lower chamber of the Rigsdag from 1849 until the Constitution of 1953 replaced the two-chamber "
        "Rigsdag with the single-chamber Folketing.",
    )
    systems = (
        (
            SYSTEM_SINGLE,
            {"formula": "fptp"},
            (
                "One member per constituency, elected by the most votes; a candidate with no opponent was declared "
                "elected without a poll. Voting was by show of hands with a poll on demand until the secret ballot of "
                "1901."
            ),
        ),
        (
            SYSTEM_PR,
            {"formula": "dhondt"},
            (
                "Proportional representation under the election law of 1920: constituency seats are allocated within "
                "each county constituency (amtskreds) or the capital's multi-member constituencies (storkredse) by the "
                "d'Hondt method, and supplementary seats nationally so that each party's seats match its national vote "
                "share. Votes are cast in nomination districts (opstillingskredse). Only the d'Hondt step is recorded "
                "here."
            ),
        ),
    )
    systems += (
        (
            SYSTEM_CAPITAL,
            {"formula": "dhondt"},
            (
                "The capital (the municipalities of Copenhagen and Frederiksberg) elected 24 members by list under "
                "the election law of 1915, allocated by the d'Hondt method; the other members were elected in "
                "single-member constituencies, with supplementary seats allocated nationally."
            ),
        ),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=DENMARK, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for party, name in sorted(parties().items()):
        id = key("party", party)
        p.add("party", id=id, kind="party", polity_id=DENMARK)
        p.name(id, name, "da", "official")
    return p


def volumes() -> list[Volume]:
    found = {v for v in PR.values()} | {v for y, v in SINGLE.items() if y in READY}
    return sorted(found, key=lambda v: (v.cid, v.name))


APRIL_1920 = date(1920, 4, 26)
CAPITAL_DAY = date(1918, 4, 22)
SYSTEM_CAPITAL = key("electoral_system", "capital1915")
CAPITAL_PATH = sheets.FOLDER / "cph-1918-04-22.tsv"
# The capital's lists of April 1918, by letter: party key and name.
CAPITAL_LISTS: dict[str, tuple[str, str]] = {
    "A": ("socialdemokratiet", "Socialdemokratiet"),
    "B": ("radikale-venstre", "Det Radikale Venstre"),
    "C": ("konservative-folkeparti", "Det Konservative Folkeparti"),
    "D": ("vaelgerforeningen-af-1918", "Vælgerforeningen af 1918"),
    "E": ("erhvervslisten", "Erhvervslisten"),
    "F": ("venstre", "Venstre"),
    "G": ("socialistisk-arbejderparti", "Socialistisk Arbejderparti"),
    "H": ("det-ny-hoejre", "Det ny Højre"),
    "I": ("det-uafhaengige-socialdemokrati", "Det uafhængige Socialdemokrati"),
}


def pr_path(day: date) -> Path:
    return sheets.FOLDER / f"pr-{day}.tsv"


# The single-member volumes whose transcription is complete and checked.
READY: set[int] = {1901, 1903, 1906, 1909, 1910, 1913, 1915, 1918, 1920}


def tab_path(day: date) -> Path:
    return sheets.FOLDER / f"tab-{day}.tsv"


def sm_path(year: int) -> Path:
    return sheets.FOLDER / f"sm-{year}.tsv"


@cache
def parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for day in PR:
        if not pr_path(day).exists():
            continue
        table = sheets.read(pr_path(day))
        for row in [r for b in table.blocks for r in b.rows]:
            for column in row.columns[:-1]:
                named = party_of(column)
                if named:
                    found[named[0]] = named[1]
    if CAPITAL_PATH.exists():
        for party, name in CAPITAL_LISTS.values():
            found[party] = name
    for year in SINGLE:
        if year not in READY:
            continue
        for c in sheets.constituencies(sm_path(year)):
            for candidate in c.candidates:
                named = letter_party(candidate.party, year)
                if named:
                    found[named[0]] = named[1]
    return found


def month(day: date) -> str:
    return [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ][day.month - 1]


DANISH_MONTHS = [
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
]


def danish(day: date) -> str:
    return f"{day.day}. {DANISH_MONTHS[day.month - 1]} {day.year}"


def pages_of(p: Package, volume: Volume, numbers: set[int]) -> dict[int, str]:
    stored = artifact(volume)
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{DST_SOURCE}\x1f{stored.storage_key}"))
    found: dict[int, str] = {}
    for number in sorted(numbers):
        width, height = page_sizes(stored.path)[number]
        found[number] = key("page", volume.cid, volume.name, number)
        p.add(
            "source_page",
            id=found[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    return found


class Facts:
    """Result values and their page locators, staged together."""

    def __init__(self, result_set: str) -> None:
        self.result_set = result_set
        self.values: list[tuple[str, str | None, int, int]] = []
        self.boxes: list[tuple[str, str | None, int, str, float, float, float, float]] = []

    def add(self, unit: str, option: str | None, measure: int, value: int, page: str, box: tuple[float, ...]) -> None:
        self.values.append((unit, option, measure, value))
        x0, y0, x1, y1 = box
        self.boxes.append((unit, option, measure, page, x0, y0, x1, y1))

    def write(self, p: Package) -> None:
        if not self.values:
            return
        p.frame(
            "result_value",
            pl.DataFrame(
                self.values,
                schema={"unit_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
                orient="row",
            ).with_columns(
                result_set_id=pl.lit(self.result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(DIGITIZED, pl.Int16),
            ),
        )
        p.frame(
            "value_locator",
            pl.DataFrame(
                self.boxes,
                schema={
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "page_id": pl.String,
                    "x0": pl.Float64,
                    "y0": pl.Float64,
                    "x1": pl.Float64,
                    "y1": pl.Float64,
                },
                orient="row",
            ).with_columns(
                result_set_id=pl.lit(self.result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
            ),
        )


PR_METHOD = (
    "Transcribed from the page images of the table of votes by nomination district (Tabel 1 a-c, "
    "\"Stemmefordelingen\") in Statistics Denmark's volume on this election, and checked against the volume's own "
    "totals: every district's party votes add up to its printed total, every county or capital constituency's "
    "districts add up to its printed subtotal, and the subtotals add up to the printed area totals. Valid votes are "
    "the district's printed total. Lists outside the parties are stored together as one option. The Faroe Islands, "
    "which voted on a different day, are not in this table."
)


def county_name(heading: str) -> str:
    """A block heading without its roman numeral and colon, e.g. 'Bornholms Amtskreds'."""
    text = re.sub(r"^[IVXL]+\.\s*", "", heading).strip().rstrip(":").strip()
    return " ".join(text.split())


def election_names(day: date, kind: str) -> tuple[str, str]:
    same_year = [d for d in PR if d.year == day.year]
    when = f"{month(day)} {day.year}" if len(same_year) > 1 else f"{day.year}"
    if kind == "by_election":
        return f"Danish Folketing by-elections of {day.day} {month(day)} {day.year}", f"Suppleringsvalg {danish(day)}"
    return f"{when} Danish Folketing election", f"Folketingsvalget {danish(day)}"


def pr_package(day: date) -> Package:
    volume = PR[day]
    table = sheets.read(pr_path(day))
    found = sheets.problems(table)
    if found:
        raise ValueError(f"{pr_path(day).name} does not add up: {found[:5]}")
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Folketing election of {day} by nomination district, from Statistics Denmark's volume",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {DENMARK, OPSTILLINGSKREDS}
    p.uses(artifact(volume))
    election = key("election", day)
    english, native = election_names(day, "general")
    extra = faroe_1920() if day == APRIL_1920 else []  # the Faroe Islands' later poll
    last = max([day, *(date.fromisoformat(c.day) for _, c in extra)])
    p.add("election", id=election, polity_id=DENMARK, kind="general", date_from=day, date_to=last, status="held")
    p.add("slug", path=f"dk/{day}-general", entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, native, "da", "native", False)
    contest = key("contest", day, "folketing")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=BODY,
        electoral_system_id=SYSTEM_PR,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.name(contest, native, "da", "native", False)
    p.add("slug", path=f"dk/{day}-general/folketing", entity_id=contest, is_current=True)
    result_set = key("result_set", day, "folketing")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DST_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    table_name = "Tabel 2 a-c" if day == APRIL_1920 else "Tabel 1 a-c"
    method = PR_METHOD.replace("Tabel 1 a-c", table_name)
    turnout = sheets.turnout(tab_path(day)) if tab_path(day).exists() else []
    if turnout:
        method += (
            " The registered voters, ballots cast, and invalid or blank ballots of each district are from the "
            "volume's turnout table (Tabel 1)."
        )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", lang="en", body_md=method)
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=DST_SOURCE,
        locator={"url": url(volume.cid, volume.name), "title": volume.title, "table": table_name},
    )
    options: dict[str | None, str] = {}

    def option(column: str) -> str | None:
        named = party_of(column)
        party = named[0] if named else None
        if party not in options:
            id = options[party] = key("option", day, party or "others")
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="list" if named else "others",
                party_id=key("party", party) if party else None,
                ballot_label=named[1] if named else "Uden for partierne",
            )
        return options[party]

    pdf = artifact(volume).path
    rows = [(b, r) for b in table.blocks for r in b.rows]
    pages = pages_of(p, volume, {r.page for _, r in rows} | {t.page for t in turnout})
    drawn: dict[tuple[int, str], sheets.Turnout] = {}
    if turnout:
        order = list(dict.fromkeys(t.storkreds for t in turnout))
        if len(order) != len(table.blocks):
            raise ValueError(f"{tab_path(day).name}: {len(order)} storkredse, the vote table has {len(table.blocks)}")
        for t in turnout:
            drawn[(order.index(t.storkreds), t.number)] = t
    boxes: dict[int, list[list[Box | None]]] = {}
    for number in pages:
        mine = [r for _, r in rows if r.page == number]
        boxes[number] = locate(pdf, number, [r.values for r in mine])
    facts = Facts(result_set)
    seen: dict[int, int] = {}
    index = {b.name: n for n, b in enumerate(table.blocks)}
    for block, row in rows:
        place = seen.get(row.page, 0)
        seen[row.page] = place + 1
        cells = boxes[row.page][place]
        county = county_name(block.name)
        unit = key("unit", day, county, row.number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=DENMARK,
            scheme_code=OPSTILLINGSKREDS,
            code=f"fv{day}:{slug(county)}:{row.number.replace(' ', '')}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, f"{row.name} ({county})", "da", "official")
        page = pages[row.page]
        merged: dict[str | None, tuple[int, Box | None]] = {}
        for k, column in enumerate(row.columns[:-1]):
            id = option(column)
            value, box = merged.get(id, (0, None))
            merged[id] = (value + row.values[k], box or cells[k])
        for id, (value, box) in merged.items():
            facts.add(unit, id, VOTES, value, page, normalized(pdf, row.page, box))
        facts.add(unit, None, VALID, row.values[-1], page, normalized(pdf, row.page, cells[-1]))
        if drawn:
            number = row.number.removeprefix("Frb. ") + ("Frb" if row.number.startswith("Frb") else "")
            t = drawn[(index[block.name], number)]
            where = pages[t.page]
            for measure, value in ((REGISTERED, t.registered), (CAST, t.voted), (INVALID, t.invalid)):
                facts.add(unit, None, measure, value, where, normalized(pdf, t.page, find(pdf, t.page, value)))
    facts.write(p)
    national = national_total(table)
    declared: dict[str | None, int] = {}
    for column, value in national.items():
        if column == "Tilsammen":
            continue
        id = option(column)
        declared[id] = declared.get(id, 0) + value
    for id, value in declared.items():
        p.add("declared", result_set_id=result_set, option_id=id, measure=VOTES, value=value)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=national["Tilsammen"])
    if turnout:
        p.accept(
            "accounting.ballots_sum",
            "Vejle Amtskreds, 6. Kreds (Vonsild): the volume prints 6,022 ballots cast with 4 invalid or blank in the "
            "turnout table, but 6,019 valid votes in the party table, one fewer than the 6,018 the turnout table implies",
        )
    if extra:
        # the Faroe Islands' own single-member poll, a contest of the same general election
        pages_single = single_pages(p, extra, uses=False)
        for year, c in extra:
            add_single(p, day, f"dk/{day}-general", year, c, pages_single)
    return p


def national_total(table: sheets.Table) -> dict[str, int]:
    """The printed national total row, or the sum of the printed area totals when no national row is printed."""
    areas: list[sheets.Row] = []
    start = 0
    for total, after in zip(table.totals, table.total_after, strict=True):
        if after > start:
            areas.append(total)
        else:
            return total.by_column()
        start = after
    return sheets.add(areas)


SM_METHOD = (
    "Transcribed from the page images of the constituency tables in Statistics Denmark's volume on this election. "
    "Registered voters are the constituency's printed total of voters (Vælgernes Antal), and the candidates' votes, "
    "the invalid ballots (Ugyldige Stemmesedler), and the blank ballots (Blankt afgivne Stemmesedler) are as printed; "
    "valid votes are the candidates' votes together. Ballots cast are the printed number who voted (Deraf stemte) "
    "where it equals the valid, invalid, and blank ballots together; where the volume also counts ballots handed out "
    "but not returned, that figure is left out. Where a sole candidate faced a poll, the votes against him "
    "(Nej-Stemmer) are stored as a vote against and count as valid. Where the volume prints invalid and blank "
    "ballots as one figure, it is stored as invalid. Each figure was checked against the printed "
    "number who voted."
)
TACIT = (
    "Elected without a poll (valgt uden Afstemning): no other candidate stood, so no votes were cast. The registered "
    "voters are as printed in Statistics Denmark's volume on this election."
)


@cache
def single() -> dict[date, list[tuple[int, sheets.Constituency]]]:
    """Every transcribed single-member result, by polling day, with the volume year it comes from."""
    found: dict[date, list[tuple[int, sheets.Constituency]]] = {}
    for year in sorted(READY):
        rows = sheets.constituencies(sm_path(year))
        problems = sheets.single_problems(rows)
        if problems:
            raise ValueError(f"{sm_path(year).name} does not add up: {problems[:5]}")
        for c in rows:  # the transcriptions cite printed page numbers; locate the pages of the PDF
            c.page += SINGLE[year].offset
            for candidate in c.candidates:
                candidate.page += SINGLE[year].offset
        if year == 1920:  # the April 1920 election is proportional; its Faroe seat joins that package
            rows = [c for c in rows if c.kind != "general"]
        days = [c.day for c in rows if c.kind == "general"]
        if not days:
            main = ""
        else:
            main = max(set(days), key=days.count)
        first = {(c.county, c.number) for c in rows if c.kind == "general" and c.day == main}
        for c in rows:
            # a general-election poll held on another day (the Faroe Islands) belongs to that general election;
            # a second poll in a constituency that already voted that day is a rerun, an election of its own
            day = c.day
            if c.kind == "general" and (c.day == main or (c.county, c.number) not in first):
                day = main
            elif c.kind == "general":
                c.kind = "by"
            found.setdefault(date.fromisoformat(day), []).append((year, c))
    return found


@cache
def faroe_1920() -> list[tuple[int, sheets.Constituency]]:
    """The Faroe Islands' single-member election of 26 May 1920, part of the April 1920 general election."""
    rows = [c for c in sheets.constituencies(sm_path(1920)) if c.kind == "general"]
    problems = sheets.single_problems(rows)
    if problems:
        raise ValueError(f"{sm_path(1920).name} does not add up: {problems[:5]}")
    return [(1920, c) for c in rows]


def constituency_name(c: sheets.Constituency) -> tuple[str, str]:
    county = c.county
    native = f"{county}s {c.number}. valgkreds" if county.endswith("Amt") else f"{county}, {c.number}. valgkreds"
    english = f"{county}, constituency {c.number}"
    return english, native


def single_pages(
    p: Package, entries: list[tuple[int, sheets.Constituency]], uses: bool = True
) -> dict[tuple[Volume, int], str]:
    """The source pages of single-member constituencies, staged once per volume and page."""
    used: dict[Volume, set[int]] = {}
    for year, c in entries:
        used.setdefault(SINGLE[year], set()).update({c.page, *(x.page for x in c.candidates)})
    pages: dict[tuple[Volume, int], str] = {}
    for volume, numbers in used.items():
        if uses:
            p.uses(artifact(volume))
        for number, id in pages_of(p, volume, numbers).items():
            pages[(volume, number)] = id
    return pages


def add_single(
    p: Package, day: date, path: str, year: int, c: sheets.Constituency, pages: dict[tuple[Volume, int], str]
) -> None:
    """One single-member constituency's contest, result set, units, options, and values."""
    volume = SINGLE[year]
    election = key("election", day)
    tag = f"{slug(c.county)}-{c.number}"
    contest = key("contest", day, tag)
    tacit = c.result == "unopposed"
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=BODY,
        magnitude=1,
        electoral_system_id=SYSTEM_SINGLE,
        round_number=1,
        default_result_kind="tacit" if tacit else "counted",
    )
    english_name, native_name = constituency_name(c)
    p.name(contest, english_name, "en", "official")
    p.name(contest, native_name, "da", "native", False)
    p.add("slug", path=f"{path}/{tag}", entity_id=contest, is_current=True)
    result_set = key("result_set", day, tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if tacit else "counted",
        source_id=DST_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        lang="en",
        body_md=TACIT if tacit else SM_METHOD,
    )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=DST_SOURCE,
        locator={
            "url": url(volume.cid, volume.name),
            "title": volume.title,
            "printed_page": c.page - volume.offset,
        },
    )
    unit = key("unit", day, tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=DENMARK,
        scheme_code=VALGKREDS,
        code=f"fv{day}:{tag}",
        area_relation="unlocated",
        area_id=None,
        kind="standard",
    )
    p.name(unit, native_name + (f" ({c.seat})" if c.seat else ""), "da", "official")
    facts = Facts(result_set)
    page = pages[(volume, c.page)]
    pdf = artifact(volume).path

    def at(number: int, value: int) -> tuple[float, float, float, float]:
        return normalized(pdf, number, find(pdf, number, value))

    if c.registered is not None:
        facts.add(unit, None, REGISTERED, c.registered, page, at(c.page, c.registered))
    for n, candidate in enumerate(c.candidates):
        ctag = f"{day}:{tag}:{n}"
        option = key("option", ctag)
        named = letter_party(candidate.party, year)
        party = key("party", named[0]) if named else None
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_label=candidate.name,
        )
        person = key("person", ctag)
        p.add("person", id=person)
        p.name(person, candidate.name, "da", "official")
        p.add(
            "candidacy",
            id=key("candidacy", ctag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.name,
            member_of_party_id=party,
            is_independent=party is None,
            is_incumbent=candidate.incumbent,
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="elected" if candidate.elected else "lost",
            seats=1 if candidate.elected else None,
        )
        if candidate.votes is not None:
            facts.add(
                unit,
                option,
                VOTES,
                candidate.votes,
                pages[(volume, candidate.page)],
                at(candidate.page, candidate.votes),
            )
    against = [(label, v) for label, v in c.lines if label.lower().startswith("nej")]
    if against:
        # a sole candidate at a poll: the ballot let voters vote against him
        option = key("option", f"{day}:{tag}:against")
        p.add("contest_option", id=option, contest_id=contest, kind="none_of_the_above", ballot_label="Nej")
        won = len(c.candidates) == 1 and against[0][1] > (c.candidates[0].votes or 0)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if won else "lost")
        facts.add(unit, option, VOTES, against[0][1], page, normalized(pdf, c.page, None))
    if not tacit:
        valid = sum(x.votes or 0 for x in c.candidates) + sum(v for _, v in against)
        invalid = sum(v for label, v in c.lines if "ugyld" in label.lower())
        blank = sum(v for label, v in c.lines if "blank" in label.lower() and "ugyld" not in label.lower())
        last = c.candidates[-1].page
        facts.add(unit, None, VALID, valid, pages[(volume, last)], normalized(pdf, last, None))
        if any("ugyld" in label.lower() for label, _ in c.lines):
            facts.add(unit, None, INVALID, invalid, pages[(volume, last)], at(last, invalid))
        if any("blank" in label.lower() and "ugyld" not in label.lower() for label, _ in c.lines):
            facts.add(unit, None, BLANK, blank, pages[(volume, last)], at(last, blank))
        if c.voted is not None and c.voted == valid + invalid + blank:
            facts.add(unit, None, CAST, c.voted, page, at(c.page, c.voted))
        if c.misprint:
            p.add(
                "note",
                id=key("note", result_set, "misprint"),
                entity_id=result_set,
                kind="caveat",
                lang="en",
                body_md="The volume's own figures for this constituency do not add up: the candidates' votes and "
                "the printed ballot lines differ from the printed number who voted. Every figure is stored as "
                "printed, and the number who voted is not stored as ballots cast.",
            )
    facts.write(p)


CAPITAL_METHOD = (
    "Transcribed from the page images of the capital's table (Tabel 1) in Statistics Denmark's volume on this "
    "election, and checked against its printed totals: every polling district's votes for the lists add up to its "
    "printed valid ballots, valid and invalid ballots add up to the ballots cast, and the districts add up to the "
    "printed subtotals for Copenhagen and Frederiksberg and to the total for the capital. Seats are the number of "
    "members each list's printed list of elected members (Folketingsmænd) names. Liste F stood in a list alliance "
    "(Listeforbund, Valglovens § 28), which is not modeled."
)


def add_capital(p: Package, election: str, path: str) -> None:
    """The capital's list contest of April 1918: 24 seats elected from nine lists in 18 polling districts."""
    table = sheets.capital(CAPITAL_PATH)
    problems = sheets.capital_problems(table)
    if problems:
        raise ValueError(f"{CAPITAL_PATH.name} does not add up: {problems[:5]}")
    volume = SINGLE[1918]
    pdf = artifact(volume).path
    pages = pages_of(p, volume, {52, 53})
    contest = key("contest", CAPITAL_DAY, "capital")
    seats = sum(n for _, _, n in table.lists)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=BODY,
        magnitude=seats,
        electoral_system_id=SYSTEM_CAPITAL,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "Capital (Copenhagen and Frederiksberg)", "en", "official")
    p.name(contest, "Hovedstaden (Københavns og Frederiksbergs Kommuner)", "da", "native", False)
    p.add("slug", path=f"{path}/capital", entity_id=contest, is_current=True)
    result_set = key("result_set", CAPITAL_DAY, "capital")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DST_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note", id=key("note", result_set), entity_id=result_set, kind="methodology", lang="en", body_md=CAPITAL_METHOD
    )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=DST_SOURCE,
        locator={"url": url(volume.cid, volume.name), "title": volume.title, "table": "Tabel 1", "printed_page": 52},
    )
    options: dict[str, str] = {}
    for letter, name, won in table.lists:
        party, _ = CAPITAL_LISTS[letter]
        options[letter] = key("option", CAPITAL_DAY, "capital", letter)
        p.add(
            "contest_option",
            id=options[letter],
            contest_id=contest,
            kind="list",
            party_id=key("party", party),
            ballot_label=f"Liste {letter}: {name}",
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[letter],
            kind="elected" if won else "lost",
            seats=won if won else None,
        )
    facts = Facts(result_set)
    for block in table.blocks:
        for d in block.districts:
            tag = f"{'frb' if block.name.startswith('Frederiksberg') else 'kbh'}-{d.number}"
            unit = key("unit", CAPITAL_DAY, "capital", tag)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=DENMARK,
                scheme_code=OPSTILLINGSKREDS,
                code=f"fv{CAPITAL_DAY}:capital:{tag}",
                area_relation="unlocated",
                area_id=None,
                kind="standard",
            )
            p.name(unit, f"{d.name} ({block.name})", "da", "official")
            turnout, ballots = pages[52], pages[53]

            def at(page: int, value: int) -> tuple[float, float, float, float]:
                return normalized(pdf, page, find(pdf, page, value))

            facts.add(unit, None, REGISTERED, d.registered, turnout, at(52, d.registered))
            facts.add(unit, None, VALID, d.valid, turnout, at(52, d.valid))
            facts.add(unit, None, INVALID, d.invalid, turnout, at(52, d.invalid))
            facts.add(unit, None, CAST, d.cast, turnout, at(52, d.cast))
            for (letter, _, _), votes in zip(table.lists, d.votes, strict=True):
                facts.add(unit, options[letter], VOTES, votes, ballots, at(53, votes))
    facts.write(p)
    assert table.total is not None
    for (letter, _, _), votes in zip(table.lists, table.total.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=options[letter], measure=VOTES, value=votes)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=table.total.valid)


def sm_package(day: date) -> Package:
    entries = single()[day]
    kind = "general" if all(c.kind == "general" for _, c in entries) else "by_election"
    if kind == "by_election" and any(c.kind == "general" for _, c in entries):
        raise ValueError(f"{day}: general election and by-elections on one day")
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Folketing {'election' if kind == 'general' else 'by-elections'} of {day} by constituency, from "
        "Statistics Denmark's volume",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {DENMARK, OPSTILLINGSKREDS} if day == CAPITAL_DAY else {DENMARK}
    election = key("election", day)
    word = "general" if kind == "general" else "by-election"
    path = f"dk/{day}-{word}"
    last = max(date.fromisoformat(c.day) for _, c in entries)
    p.add("election", id=election, polity_id=DENMARK, kind=kind, date_from=day, date_to=last, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    english, native = election_names(day, kind)
    p.name(election, english, "en", "official")
    p.name(election, native, "da", "native", False)
    pages = single_pages(p, entries)
    for year, c in entries:
        add_single(p, day, path, year, c, pages)
    if day == CAPITAL_DAY:
        add_capital(p, election, path)
    return p


def packages() -> list[str]:
    days = {day for day in PR if pr_path(day).exists()} | set(single())
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in sorted(days))]


def parse() -> None:
    registry().write()
    for day in sorted(PR):
        if pr_path(day).exists():
            pr_package(day).write()
    for day in sorted(single()):
        sm_package(day).write()


def emit() -> None:
    package.run("load", packages())
