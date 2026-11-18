"""The Zanzibar Electoral Commission's reports on the general elections of 1995, 2000, 2005, 2010, 2020, and 2025.

Each report prints, as annexes, the results of three elections held on one day: the President of Zanzibar, the members
of the House of Representatives by constituency (jimbo), and the councillors by ward (wadi). The President's annex
gives every candidate's votes in each constituency, grouped by district (wilaya), and the House and councillor annexes
give every candidate's votes with the valid, rejected, and total votes and the registered voters of each constituency
or ward. The constituency and the ward are the finest units the Commission publishes.

The reports of 1995 to 2010 are scanned page images. Their annexes were read from the pages at 200 dpi. Each number's
box is Tesseract's box for a word with the same digits in the number's line and column; where Tesseract found none, it
is the number's cell, from its line and its column, or else its whole line. Where no printed total checks a contest,
every number Tesseract did not find was read a second time, blind; the two readings agree on all of them but two, which
are left out. The readings, with the page and box of every number, are kept in zec_returns/{year}.json. Those annexes
print fewer figures than the later ones: the House and councillor tables give each candidate's votes, with the
contest's total only in the 1995 House table and in both 2000 tables, and every year but 2005 marks the candidate
declared elected. In 2000 the region of Mjini Magharibi voted again on 5 November, after its 29 October poll was
annulled, and the annexes give the counts declared after that day.
"""

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "tz.zec"
COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fTZ"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fTZA"))
HOST = "https://www.zec.go.tz/admin_panel/public/taarifa-uchaguzi"
REPORTS = {
    1995: f"{HOST}/1771928397_Ripoti%20ya%20uchaguzi%20mkuu.pdf",
    2000: f"{HOST}/1771928457_Ripoti%20ya%20uchaguzi%20mkuu%202000.pdf",
    2005: f"{HOST}/1770188715_Ripoti%20ya%20Uchaguzi%20Mkuu%202005.pdf",
    2010: f"{HOST}/1770188689_Ripoti%20ya%20Uchaguzi%20Mkuu%202010.pdf",
    2020: f"{HOST}/1752063954_RIPOTI%20YA%20UCHAGUZI%20MKUU%20ZANZIBAR%202020.._compressed.pdf",
    2025: f"{HOST}/1789389706_RIPOTI%20YA%20UCHAGUZI%202025%20FINAL.pdf",
}
DAYS = {
    1995: date(1995, 10, 22),
    2000: date(2000, 10, 29),
    2005: date(2005, 10, 30),
    2010: date(2010, 10, 31),
    2020: date(2020, 10, 28),
    2025: date(2025, 10, 29),
}
# The day each year's constituencies and wards gave way to the next set; 2015's election is not in this module.
UNTIL = {1995: DAYS[2000], 2000: DAYS[2005], 2005: DAYS[2010], 2010: date(2015, 10, 25), 2020: DAYS[2025]}
# The reports of 1995 to 2010 are scans: their annexes were read from the page images and are kept, with the page
# and the box of each number, in zec_returns/{year}.json.
SCANNED = (1995, 2000, 2005, 2010)
RETURNS = Path(__file__).with_name("zec_returns")
DPI = 200
DIGITIZED = 8
DERIVED = 1
# The region of Mjini Magharibi voted again on 5 November 2000 (the report's section 17.7).
LATE = {2000: date(2000, 11, 5)}
EARLY = {2020: date(2020, 10, 27), 2025: date(2025, 10, 28)}
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

# Each annex runs from its title to the next one; a title is found at the head of a line, past the table of contents.
ANNEXES = {
    2020: {
        "house": (r"Kiambatanisho Nam\. 4: MATOKEO YA UCHAGUZI WA UJUMBE", r"Kiambatanisho Nam\. 5: MATOKEO"),
        "ward": (r"Kiambatanisho Nam\. 5: MATOKEO YA UCHAGUZI WA MADIWANI", r"Kiambatanisho Nam\. 6: MATOKEO"),
        "president": (r"Kiambatanisho Nam\. 6: MATOKEO YA UCHAGUZI WA RAIS", r"Kiambatanisho Nam\. 7: "),
    },
    2025: {
        "house": (r"KIAMBATANISHO NAM\. 9: Matokeo ya Uchaguzi wa Ujumbe", r"KIAMBATANISHO NAM\. 10: "),
        "ward": (r"KIAMBATANISHO NAM\. 10: Matokeo ya Uchaguzi wa Madiwani", None),
        "president": (r"KIAMBATANISHO NAM\. 8: Matokeo ya Uchaguzi wa Rais", r"KIAMBATANISHO NAM\. 9: "),
    },
}
# The national results of the presidential election, as the reports print them in their text.
NATIONAL = {
    2020: {
        "votes": {
            "AAFP": 285,
            "ACT-Wazalendo": 99103,
            "ADA-TADEA": 392,
            "ADC": 996,
            "CCK": 2235,
            "CCM": 380402,
            "CHADEMA": 1702,
            "CHAUMMA": 249,
            "CUF": 1428,
            "DP": 106,
            "MAKINI": 133,
            "NRA": 160,
            "NLD": 122,
            "SAU": 102,
            "TLP": 142,
            "UMD": 104,
            "UPDP": 181,
        },
        "invalid": 10944,
        "registered": 566352,
    },
    2025: {
        "votes": {
            "CCM": 448892,
            "NLD": 1301,
            "AAFP": 1238,
            "TLP": 1065,
            "NRA": 1161,
            "NCCR-Mageuzi": 1643,
            "ACT-Wazalendo": 139399,
            "ADA-TADEA": 1455,
            "MAKINI": 1435,
            "ADC": 1345,
            "UPDP": 1299,
        },
        "invalid": 8863,
        "registered": 717557,
    },
}

PARTY_NAMES = {
    "AAFP": "African Alliance for Farmers Party",
    "APPT-Maendeleo": "African Progressive Party of Tanzania-Maendeleo",
    "CHAUSTA": "Chama cha Haki na Ustawi",
    "Jahazi Asilia": "Chama cha Jahazi Asilia",
    "PONA": "Popular National Party",
    "TADEA": "Tanzania Democratic Alliance",
    "ACT-Wazalendo": "Alliance for Change and Transparency",
    "ADA-TADEA": "African Democratic Alliance",
    "ADC": "Alliance for Democratic Change",
    "CCK": "Chama cha Kijamii",
    "CCM": "Chama cha Mapinduzi",
    "CHADEMA": "Chama cha Demokrasia na Maendeleo",
    "CHAUMMA": "Chama cha Ukombozi wa Umma",
    "CUF": "The Civic United Front",
    "DP": "Democratic Party",
    "MAKINI": "Chama cha Makini",
    "NCCR-Mageuzi": "National Convention for Construction and Reform",
    "NLD": "National League for Democracy",
    "NRA": "National Reconstruction Alliance",
    "SAU": "Sauti ya Umma",
    "TLP": "Tanzania Labour Party",
    "UDP": "United Democratic Party",
    "UMD": "Union for Multiparty Democracy",
    "UPDP": "United People Democratic Party",
}
# The reports spell some party abbreviations in more than one way.
PARTY_SPELLINGS = {
    "ACT": "ACT-Wazalendo",
    "ACT-WAZALENDO": "ACT-Wazalendo",
    "ADA-TADEA": "ADA-TADEA",
    "N.R.A": "NRA",
    "NCCR_MAGEUZI": "NCCR-Mageuzi",
    "NCCR-MAGEUZI": "NCCR-Mageuzi",
    "NCCR": "NCCR-Mageuzi",
    "CHAUMA": "CHAUMMA",
}
TOTALS = (
    (re.compile(r"^(?:jumla\s+ya\s+)?kura\s+halali", re.IGNORECASE), "valid"),
    (re.compile(r"^(?:jumla\s+ya\s+)?kura\s+zilizoharibika", re.IGNORECASE), "invalid"),
    (re.compile(r"^jumla\s+ya\s+kura\s+zote", re.IGNORECASE), "cast"),
    (re.compile(r"^idadi\s+ya\s+wapiga\s+kura", re.IGNORECASE), "registered"),
)
JUNK = re.compile(r"^(?:ripoti\s+ya\s+uchaguzi|tume\s+ya\s+uchaguzi|wilaya/|jimbo\b|\d+$)", re.IGNORECASE)
NUMBER = r"\d[\d,]*"


@dataclass
class Result:
    """One constituency's or ward's printed result."""

    district: str
    constituency: str
    ward: str
    candidates: list[tuple[str, str, int | None]] = field(default_factory=list)
    valid: int | None = None
    invalid: int | None = None
    cast: int | None = None
    registered: int | None = None
    uncontested: bool = False
    # For a scanned report: the page each figure is printed on and its box there, as fractions of the page.
    page: int | None = None
    elected: str | None = None
    boxes: dict[str, list[float]] = field(default_factory=dict)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold().replace("’", "").replace("'", "")).strip("-")


def clean(text: str) -> str:
    return " ".join(text.replace("“", "").replace("”", "").replace('"', "").replace("’", "'").replace("‘", "'").split())


def district_name(text: str) -> str:
    """A district's name without the quotation marks that the reports put around its letter."""
    return clean(text).replace("'", "")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def party(label: str) -> str:
    text = clean(label)
    return PARTY_SPELLINGS.get(text.upper(), text)


@cache
def text_of(year: int) -> str:
    path = raw.cached(SOURCE, REPORTS[year]).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return done.stdout


def annex(year: int, name: str) -> list[str]:
    start, end = ANNEXES[year][name]
    text = text_of(year)
    heads = [m.start() for m in re.finditer(r"^[ \t\f]*" + start, text, re.MULTILINE)]
    if not heads:
        raise ValueError(f"annex {name} of {year} not found")
    begin = heads[-1]
    stop = len(text)
    if end is not None:
        tail = re.search(r"^[ \t\f]*" + end, text[begin + 10 :], re.MULTILINE)
        if tail is None:
            raise ValueError(f"end of annex {name} of {year} not found")
        stop = begin + 10 + tail.start()
    lines: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for page in text[begin:stop].split("\f"):
        # The 2020 report prints the pages of the Mjini councillors twice, and once more at the end of the annex.
        body = tuple(line.strip() for line in page.splitlines() if line.strip() and not JUNK.match(line.strip()))
        if len(body) > 12 and body in seen:
            continue
        seen.add(body)
        lines.extend(page.splitlines())
    return lines


PARTIES = "|".join(
    re.escape(label)
    for label in sorted({*PARTY_NAMES, *PARTY_SPELLINGS, "NCCR_Mageuzi", "NCCR-Mageuzi"}, key=len, reverse=True)
)
CANDIDATE = re.compile(
    rf"^(?P<lead>.*?)(?<![\w,.])(?P<sn>\d{{1,2}})(?:\s+(?P<name>[^\d\s][^\d]*?))?\s+(?P<party>{PARTIES})(?![\w.\-])"
    rf"(?:\s+(?:(?P<votes>{NUMBER}|-)\s+(?P<pct>[\d.]+|-)|Mgombea\s+pekee\**))?\s*$",
    re.IGNORECASE,
)


def fragment(raw_line: str) -> bool:
    """A line holding only part of a candidate's name, which the report wraps around the candidate's row."""
    text = raw_line.strip()
    return (
        bool(text)
        and not re.search(r"\d", text.replace("(Bhaa)", ""))
        and len(raw_line) - len(raw_line.lstrip()) >= 18
        and not text.startswith(("Jumla", "Kura", "Idadi", "Ripoti", "TUME", "Ameteuliwa", "kupingwa", "Wilaya", "*"))
        and "Jina la Mgombea" not in text
    )


@cache
def returns(year: int) -> dict[str, Any]:
    return json.loads((RETURNS / f"{year}.json").read_text())


def scanned(year: int, name: str) -> list[Result]:
    found = []
    for row in returns(year)["house" if name == "house" else "wards"]:
        found.append(
            Result(
                district=row["district"],
                constituency=row.get("constituency") or "",
                ward=row.get("ward") or "",
                candidates=[(c["name"], party(c["party"]), c["votes"]) for c in row["candidates"]],
                valid=row.get("valid"),
                invalid=row.get("invalid"),
                cast=row.get("cast"),
                registered=row.get("registered"),
                uncontested=row.get("uncontested", False),
                page=row["page"],
                elected=row.get("elected"),
                boxes=row["boxes"],
            )
        )
    return found


@cache
def contests(year: int, name: str) -> list[Result]:
    """The House or councillor results: a constituency per contest in the House annex, a ward in the councillors'."""
    if year in SCANNED:
        return scanned(year, name)
    ward_table = name == "ward"
    known = {r.constituency for r in contests(year, "house")} if ward_table else set()
    found: list[Result] = []
    district = constituency = ""
    current: Result | None = None
    lines = [line.rstrip() for line in annex(year, name)[2:]]
    prefix: list[str] = []
    last: list[str] | None = None  # the name parts of the latest candidate, a row's name before and after its fragments
    last_prefixed = False
    names: list[tuple[Result, int, list[str]]] = []
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or JUNK.match(text) or "Jina la Mgombea" in text or text.startswith(("Ameteuliwa", "kupingwa")):
            continue
        if text.startswith("*") and "mgombea pekee" in text:
            break  # the footnote that explains the uncontested seats ends the table
        if fragment(line) and re.search(r"[A-Za-z]", text):
            following = next((x for x in lines[index + 1 :] if x.strip()), "")
            if last is not None and last_prefixed:
                last.append(text)
                last_prefixed = False
            elif CANDIDATE.match(following) is not None:
                prefix.append(text)
            elif last is not None:
                last.append(text)
            else:
                raise ValueError(f"stray name part {text!r}")
            continue
        if re.match(r"^wilaya\s+ya\s+", text, re.IGNORECASE):
            district = district_name(re.sub(r"^wilaya\s+ya\s+", "", text, flags=re.IGNORECASE))
            continue
        total = next((field_name for pattern, field_name in TOTALS if pattern.match(text)), None)
        if total is not None:
            if current is None:
                raise ValueError(f"total before a candidate: {text!r}")
            value = re.search(rf"({NUMBER})", re.sub(r"^[^\d]*", "", text) or "")
            if value is None:
                raise ValueError(f"no total in {text!r}")
            setattr(current, total, number(value.group(1)))
            if total == "registered":
                found.append(current)
                current = None
            last = None
            continue
        match = CANDIDATE.match(line)
        if match is None:
            if re.fullmatch(r"[^\d]+", text):
                constituency = clean(text)  # a constituency named on its own line
                continue
            raise ValueError(f"unreadable line in annex {name} of {year}: {text!r}")
        leads = [clean(part) for part in re.split(r"\s{2,}", match["lead"].strip()) if part.strip()]
        if int(match["sn"]) == 1:
            if ward_table:
                ward_name = leads[-1]
                if len(leads) > 1:
                    constituency = leads[0]
                elif len(line) - len(line.lstrip()) < 8 and len(leads) == 1:
                    # A constituency and its first ward, set one space apart: split on the constituency's name.
                    named = [k for k in known if leads[0].casefold().startswith(k.casefold() + " ")]
                    if named:
                        constituency = max(named, key=len)
                        ward_name = leads[0][len(constituency) :].strip()
            else:
                ward_name = ""
                if leads:
                    constituency = leads[-1]
            if current is not None:
                raise ValueError(f"contest {current.constituency}/{current.ward} has no totals")
            current = Result(district, constituency, ward_name)
        if current is None:
            raise ValueError(f"candidate outside a contest: {text!r}")
        votes = None if match["votes"] is None else 0 if match["votes"] == "-" else number(match["votes"])
        parts = [*prefix, *([match["name"]] if match["name"] else [])]
        last_prefixed = bool(prefix)
        prefix = []
        current.candidates.append(("", party(match["party"]), votes))
        last = parts
        names.append((current, len(current.candidates) - 1, parts))
        if votes is None:
            current.uncontested = True
            found.append(current)
            current = None
    if current is not None:
        raise ValueError("the last contest has no totals")
    for result, position, parts in names:
        _, label, votes = result.candidates[position]
        result.candidates[position] = (clean(" ".join(parts)), label, votes)
    return found


@dataclass
class Block:
    """One district's table in the President's annex: a column of votes for each of its constituencies."""

    district: str
    constituencies: list[str]
    rows: list[list[int]] = field(default_factory=list)  # each candidate's total, then a cell for each constituency
    totals: dict[str, list[int]] = field(default_factory=dict)  # the same, for the valid, rejected, cast, registered
    header: list[str] = field(default_factory=list)


HEADER = re.compile(r"^(?P<district>[A-Z][A-Z \u201c\u201d\"]+?)\s{2,}CHAMA\s+KURA\s+ASILIMIA\s+(?P<columns>.+)$")
FIGURE = re.compile(r"^(?:\d[\d,]*(?:\.\d+)?|-)$")
PRESIDENTS = {
    2020: [
        ("Said Soud Said", "AAFP"),
        ("Seif Sharif Hamad", "ACT-Wazalendo"),
        ("Juma Ali Khatib", "ADA-TADEA"),
        ("Hamad Rashid Mohamed", "ADC"),
        ("Othman Rashid Khamis", "CCK"),
        ("Hussein Ali Mwinyi", "CCM"),
        ("Said Issa Mohammed", "CHADEMA"),
        ("Ali Omar Juma", "CHAUMMA"),
        ("Mussa Haji Kombo", "CUF"),
        ("Shafi Hassan Suleiman", "DP"),
        ("Ameir Hassan Ameir", "MAKINI"),
        ("Khamis Faki Mgau", "NRA"),
        ("Mfaume Khamis Hassan", "NLD"),
        ("Issa Mohammed Zonga", "SAU"),
        ("Hussein Juma Salum", "TLP"),
        ("Mohammed Omar Shaame", "UMD"),
        ("Hamad Mohammed Ibrahim", "UPDP"),
    ],
    2025: [
        ("Hussein Ali Mwinyi", "CCM"),
        ("Mfaume Khamis Hassan", "NLD"),
        ("Said Soud Said", "AAFP"),
        ("Hussein Juma Salim", "TLP"),
        ("Khamis Faki Mgau", "NRA"),
        ("Laila Rajab Khamis", "NCCR-Mageuzi"),
        ("Othman Masoud Othman", "ACT-Wazalendo"),
        ("Juma Ali Khatib", "ADA-TADEA"),
        ("Ameir Hassan Ameir", "MAKINI"),
        ("Hamad Rashid Mohamed", "ADC"),
        ("Hamad Mohammed Ibrahim", "UPDP"),
    ],
}


def figures(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group()) for m in re.finditer(r"\S+", line) if FIGURE.match(m.group())]


Row = tuple[str, list[tuple[int, str]]]


@cache
def presidents(year: int) -> list[Block]:
    """The President's results by constituency, in the district tables of the annex.

    The report wraps long names over several lines, prints the abbreviation of ACT-Wazalendo on two, and sets a cell
    that holds no votes as a dash on a line of its own, so a candidate's row is found by its figures. The rows of a table
    run in the order of PRESIDENTS.
    """
    layout: dict[str, list[str]] = {}
    canon: dict[str, str] = {}
    registered: dict[tuple[str, int], list[str]] = {}
    for result in contests(year, "house"):
        layout.setdefault(result.district.casefold(), []).append(result.constituency)
        canon[result.district.casefold()] = result.district
        registered.setdefault((result.district.casefold(), result.registered or 0), []).append(result.constituency)
    blocks: list[Block] = []
    current: Block | None = None
    rows: list[Row] = []
    carry: list[tuple[int, str]] = []

    def want(label: str) -> int:
        assert current is not None
        return len(current.constituencies) + (1 if label == "registered" else 2)

    def close() -> None:
        nonlocal rows, carry
        if current is not None:
            for label, cells_of in rows:
                if len(cells_of) != want(label):
                    raise ValueError(
                        f"{year} {current.district} {label or 'candidate'}: {len(cells_of)} cells, wanted {want(label)}"
                    )
                texts = [text for _, text in sorted(cells_of)]
                if label != "registered":
                    del texts[1]  # the percentage follows the total
                values = [0 if text == "-" else number(text) for text in texts]
                if label == "":
                    current.rows.append(values)
                else:
                    current.totals[label] = values
        rows, carry = [], []

    for line in annex(year, "president")[1:]:
        text = line.strip()
        if not text or text.startswith(("Ripoti", "Mwaka", "TUME")):
            continue
        header = HEADER.match(text)
        if header is not None:
            close()
            district = district_name(header["district"])
            if district.casefold() not in layout:
                raise ValueError(f"{year}: no district {district!r} in the House annex")
            # The header abbreviates some names and runs others together; the columns are named once the rows are read.
            current = Block(district, layout[district.casefold()])
            current.header = [clean(c) for c in re.split(r"\s{2,}", header["columns"].strip()) if c.strip()]
            blocks.append(current)
            continue
        found = figures(line)
        if current is None or not found:
            continue
        if all(token == "-" for _, token in found) and not re.search(
            rf"(?<![\w-])(?:{PARTIES})(?![\w-])", text, re.IGNORECASE
        ):
            if rows and want(rows[-1][0]) - len(rows[-1][1]) == len(found):
                rows[-1][1].extend(found)
            else:
                carry = found
            continue
        label = next((name for pattern, name in TOTALS if pattern.match(text)), "")
        if rows and not re.search(r"[A-Za-z]", text) and want(rows[-1][0]) - len(rows[-1][1]) == len(found):
            rows[-1][1].extend(found)  # the cells that a row's line has no room for, set on a line of their own
            continue
        if label == "" and len(found) < 3:
            continue
        rows.append((label, [*found, *carry]))
        carry = []
    close()
    for block in blocks:
        block.district = canon[block.district.casefold()]
        # The header abbreviates some names and orders them differently from the House annex, so each column is the
        # constituency whose registered voters it shows: the roll is the same for both elections.
        by_name = {name.casefold(): name for name in block.constituencies}
        if len(block.header) == len(block.constituencies) and all(h.casefold() in by_name for h in block.header):
            block.constituencies = [by_name[h.casefold()] for h in block.header]
            continue
        names: list[str | None] = []
        for cell in block.totals["registered"][1:]:
            match = registered.get((block.district.casefold(), cell), [])
            names.append(match[0] if len(match) == 1 else None)
        left = [name for name in block.constituencies if name not in names]
        if names.count(None) != len(left) or len(left) > 1:
            raise ValueError(f"{year} {block.district}: columns {names} do not fit {block.constituencies}")
        named = [left[0] if name is None else name for name in names]  # an uncontested House seat prints no roll
        if sorted(named) != sorted(block.constituencies):
            raise ValueError(f"{year} {block.district}: columns {named} differ from {block.constituencies}")
        block.constituencies = named
    return blocks


PRESIDENT_METHOD = (
    "Printed in the Commission's report on the {year} general election as the annex of the President's results: for each "
    "district, every candidate's votes in each of its constituencies, with the valid and rejected votes, the ballots "
    "cast, and the registered voters. The votes of the constituencies add up to the national result the report prints. "
    "Figures are stored as printed."
)
HOUSE_METHOD = (
    "Printed in the Commission's report on the {year} general election as the annex of the House of Representatives "
    "results: each candidate's votes in the constituency, with the valid and rejected votes, the ballots cast, and the "
    "registered voters. The annex marks no winner; the elected member is the candidate with the most votes. Figures are "
    "stored as printed."
)
WARD_METHOD = (
    "Printed in the Commission's report on the {year} general election as the annex of the councillors' results: each "
    "candidate's votes in the ward, with the valid and rejected votes, the ballots cast, and the registered voters. The "
    "annex marks no winner; the elected councillor is the candidate with the most votes. Figures are stored as printed."
)
UNCONTESTED_METHOD = (
    "The Commission's report on the {year} general election prints the sole candidate as nominated unopposed and "
    "declared elected. No votes were cast, and the report gives no voter figures."
)
SCANNED_PRESIDENT_METHOD = (
    "Read from the page images of the Commission's report on the {year} general election, from its annex of the "
    "President's results by constituency. Each number keeps its page and its box on the page. In every constituency "
    "the candidates' votes add up to the valid votes and the valid and rejected votes to the ballots cast, and the "
    "constituencies add up to the national result the annex prints. Figures are stored as printed.{extra}"
)
PRESIDENT_EXTRA = {
    1995: " Bumbwini and Donge print their ballots cast in the column of registered voters.",
    2010: " The scan cuts the Rahaleo line through its digits: its figures are the national line less the other "
    "constituencies, flagged as derived, and they agree with the tops of the digits that show.",
}
SCANNED_METHOD = (
    "Read from the page images of the Commission's report on the {year} general election, from its annex of the "
    "{kind} results. Each number keeps its page and its box on the page. The candidate stored as elected is the one "
    "the annex marks as elected, or, where it marks none, the candidate with the most votes. Figures are stored as "
    "printed; where they do not add up, the package's accepted findings name the contests."
)
SCANNED_UNCONTESTED_METHOD = (
    "The Commission's report prints the sole candidate as unopposed and elected. No votes were cast in the contest."
)
SIGNIFICANT = {"valid": VALID, "invalid": INVALID, "cast": CAST, "registered": REGISTERED}

SOURCE_ID = key("source", "zec")
SYSTEM = key("electoral_system", "plurality")
HOUSE = key("body", "house-of-representatives")
COUNCILS = key("body", "councils")
PRESIDENCY = key("office", "president")
ZANZIBAR = key("area", "zanzibar")
SCHEMES = (
    ("tz.zanzibar", 1, "administrative", "territory"),
    ("tz.district", 2, "administrative", "district"),
    ("tz.constituency", 3, "electoral_district", "constituency"),
    ("tz.ward", 4, "electoral_district", "ward"),
)


Unit = Callable[[str, str, str, str], str]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in REPORTS)]


def fetch() -> None:
    for url in REPORTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    for year in REPORTS:
        if year in SCANNED:
            check_scanned(year)
        else:
            check_national(year)
    geo().write()
    registry().write()
    for year in REPORTS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


def check_national(year: int) -> None:
    """Fail unless the constituencies add up to the national presidential result the report prints."""
    blocks = presidents(year)
    national = NATIONAL[year]
    candidates = PRESIDENTS[year]
    votes = {party: sum(block.rows[i][0] for block in blocks) for i, (_, party) in enumerate(candidates)}
    if votes != national["votes"]:
        raise ValueError(f"{year}: candidate totals differ from the report's national table")
    for label in ("invalid", "registered"):
        if sum(block.totals[label][0] for block in blocks) != national[label]:
            raise ValueError(f"{year}: {label} total differs from the report's national table")


def district_area(district: str) -> str:
    return key("area", "district", slug(district))


def constituency_area(year: int, district: str, name: str) -> str:
    return key("area", "constituency", year, slug(district), slug(name))


def ward_area(year: int, district: str, constituency: str, ward: str) -> str:
    return key("area", "ward", year, slug(district), slug(constituency), slug(ward))


def constituency_code(year: int, district: str, name: str) -> str:
    return f"{year}:{slug(district)}:{slug(name)}"


def ward_code(year: int, district: str, constituency: str, ward: str) -> str:
    return f"{constituency_code(year, district, constituency)}:{slug(ward)}"


def districts() -> list[str]:
    seen: dict[str, None] = {}
    for year in REPORTS:
        for result in contests(year, "house"):
            seen.setdefault(result.district)
    return list(seen)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The districts, House of Representatives constituencies, and councillor wards of Zanzibar from 1995 to 2025, by name",
    )
    p.external |= {COUNTRY, TERRITORY}
    for code, rank, kind, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=COUNTRY, kind=kind, rank=rank, noun=noun)
    p.add("area", id=ZANZIBAR, scheme_code="tz.zanzibar")
    p.add("area_code", area_id=ZANZIBAR, code="zanzibar")
    p.name(ZANZIBAR, "Zanzibar", "en", "official")
    p.add("area_parent", area_id=ZANZIBAR, parent_id=TERRITORY)
    for district in districts():
        area = district_area(district)
        p.add("area", id=area, scheme_code="tz.district")
        p.add("area_code", area_id=area, code=slug(district))
        p.name(area, district, "sw", "official")
        p.add("area_parent", area_id=area, parent_id=ZANZIBAR)
    for year in REPORTS:
        since = DAYS[year]
        until = UNTIL.get(year)
        for result in contests(year, "house"):
            area = constituency_area(year, result.district, result.constituency)
            p.add("area", id=area, scheme_code="tz.constituency")
            p.add(
                "area_code",
                area_id=area,
                code=constituency_code(year, result.district, result.constituency),
                valid_from=since,
                valid_to=until,
            )
            p.name(area, result.constituency, "sw", "official")
            p.add(
                "area_parent", area_id=area, parent_id=district_area(result.district), valid_from=since, valid_to=until
            )
        for result in contests(year, "ward"):
            area = ward_area(year, result.district, result.constituency, result.ward)
            p.add("area", id=area, scheme_code="tz.ward")
            p.add(
                "area_code",
                area_id=area,
                code=ward_code(year, result.district, result.constituency, result.ward),
                valid_from=since,
                valid_to=until,
            )
            p.name(area, result.ward, "sw", "official")
            p.add(
                "area_parent",
                area_id=area,
                parent_id=constituency_area(year, result.district, result.constituency)
                if result.constituency
                else district_area(result.district),
                valid_from=since,
                valid_to=until,
            )
    return p


def party_labels() -> list[str]:
    labels = {label for year in REPORTS for _, label in presidents_of(year)}
    for year in REPORTS:
        for name in ("house", "ward"):
            labels |= {label for result in contests(year, name) for _, label, _ in result.candidates}
    return sorted(labels)


def party_id(label: str) -> str:
    return key("party", slug(label))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties, the House of Representatives, the councils, and the presidency of Zanzibar",
    )
    p.external.add(COUNTRY)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Zanzibar Electoral Commission",
        title="Reports on the general elections of Zanzibar, 1995 to 2025",
        url="https://www.zec.go.tz/",
        license="Official publication; reuse terms are not stated",
        attribution="Zanzibar Electoral Commission",
    )
    for url in REPORTS.values():
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=HOUSE, polity_id=COUNTRY, kind="unicameral")
    p.name(HOUSE, "House of Representatives of Zanzibar", "en", "official")
    p.name(HOUSE, "Baraza la Wawakilishi la Zanzibar", "sw", "native", False)
    p.add("body", id=COUNCILS, polity_id=COUNTRY, kind="council")
    p.name(COUNCILS, "Councils of Zanzibar", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=COUNTRY, kind="head_of_government")
    p.name(PRESIDENCY, "President of Zanzibar", "en", "official")
    p.name(PRESIDENCY, "Rais wa Zanzibar", "sw", "native", False)
    p.add("electoral_system", id=SYSTEM, polity_id=COUNTRY, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each office and each seat goes to the candidate with the most votes.",
        lang="en",
    )
    for label in party_labels():
        party = party_id(label)
        p.add("party", id=party, kind="party", polity_id=COUNTRY)
        p.name(party, PARTY_NAMES.get(label, label), "en", "official")
        p.name(party, label, "en", "abbreviation", False)
    return p


def build(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The Zanzibar general election of {day.day} {day.strftime('%B')} {year}: the President by constituency, the "
        "House of Representatives by constituency, and the councillors by ward",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {COUNTRY, TERRITORY}
    p.uses(raw.cached(SOURCE, REPORTS[year]))
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=COUNTRY,
        kind="general",
        date_from=EARLY.get(year, day),
        date_to=LATE.get(year, day),
        status="held",
    )
    path = f"tz/{day}-zanzibar"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Zanzibar general election", "en", "official")
    p.name(election, f"Uchaguzi Mkuu wa Zanzibar {year}", "sw", "native", False)
    units: dict[str, str] = {}

    def unit(code: str, scheme: str, area: str, name: str) -> str:
        if code not in units:
            id = units[code] = key("unit", code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=COUNTRY,
                scheme_code=scheme,
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
            )
            p.name(id, name, "sw", "official")
        return units[code]

    if year in SCANNED:
        scanned_president(p, year, election, path, unit)
        artifact = raw.cached(SOURCE, REPORTS[year])
        artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{SOURCE_ID}\x1f{artifact.storage_key}"))
        for number, (width, height) in sorted(sizes(year).items()):
            if number in pages_of(year):
                p.add(
                    "source_page",
                    id=key("page", year, number),
                    artifact_id=artifact_id,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{artifact.sha256}/{number}.png",
                    width=width,
                    height=height,
                )
    else:
        president_contest(p, year, election, path, unit)
    for result in contests(year, "house"):
        local_contest(p, year, election, path, unit, result, ward=False)
    for result in contests(year, "ward"):
        local_contest(p, year, election, path, unit, result, ward=True)
    accept(p, year)
    return p


def accept(p: Package, year: int) -> None:
    """Accept the findings that the report's own figures cause, each with the contests it concerns."""
    found: dict[str, list[str]] = {}
    for name in ("house", "ward"):
        for result in contests(year, name):
            if result.uncontested:
                continue
            place = f"{result.ward or result.constituency} ({result.district})"
            votes = sum(v or 0 for _, _, v in result.candidates)
            if result.valid is not None and votes != result.valid:
                found.setdefault("accounting.votes_valid", []).append(
                    f"{place}: the candidates' votes add to {votes}, the valid total printed is {result.valid}"
                )
            if (
                result.valid is not None
                and result.invalid is not None
                and result.cast is not None
                and result.valid + result.invalid != result.cast
            ):
                found.setdefault("accounting.ballots_sum", []).append(
                    f"{place}: valid {result.valid} and rejected {result.invalid} votes do not add to the {result.cast} cast"
                )
            if result.cast is not None and result.registered is not None and result.cast > result.registered:
                found.setdefault("accounting.turnout", []).append(
                    f"{place}: {result.cast} ballots cast against {result.registered} registered voters"
                )
    for code, items in found.items():
        p.accept(code, "The report prints these figures as they are; stored as printed. " + "; ".join(items))


# A scanned figure's year, page, and box on the page as fractions of its width and height.
type Where = tuple[int, int, list[float]]


def put(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    value: int,
    where: Where | None = None,
    derived: bool = False,
) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=(0 if where is None else DIGITIZED) | (DERIVED if derived else 0),
        value=value,
    )
    if where is None:
        return
    year, page, box = where
    width, height = sizes(year)[page]
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=key("page", year, page),
        x0=round(box[0] * width, 1),
        y0=round(box[1] * height, 1),
        x1=round(box[2] * width, 1),
        y1=round(box[3] * height, 1),
    )


def located(year: int, result: Result, slot: str) -> Where | None:
    return None if result.page is None else at(year, result.boxes[slot])


def at(year: int, box: list[float]) -> Where:
    """A scanned figure's place: its box holds the corners as fractions of the page, OCR's agreement, and the page."""
    return (year, int(box[5]), box[:4])


@cache
def sizes(year: int) -> dict[int, tuple[int, int]]:
    """Each page's size in pixels at the resolution its boxes were read at."""
    out = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "9999", str(raw.cached(SOURCE, REPORTS[year]).path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        int(number): (round(float(width) * DPI / 72), round(float(height) * DPI / 72))
        for number, width, height in re.findall(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", out)
    }


def contest_set(p: Package, election: str, contest: str, code: str, method: str, completeness: float = 1.0) -> str:
    result_set = key("result_set", code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=completeness,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


def candidate(p: Package, contest: str, code: str, rank: int, name: str, label: str, elected: bool) -> tuple[str, str]:
    option = key("option", code, rank)
    person = key("person", code, rank)
    party = party_id(label)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=party,
        ballot_position=rank,
        ballot_label=name,
    )
    p.add("person", id=person)
    p.name(person, name, "en", "official")
    p.add(
        "candidacy",
        id=key("candidacy", code, rank),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=name,
        member_of_party_id=party,
    )
    return option, party


def president_contest(p: Package, year: int, election: str, path: str, unit: Unit) -> None:
    code = f"{year}:president"
    contest = key("contest", code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=ZANZIBAR,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Zanzibar presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = contest_set(p, election, contest, code, PRESIDENT_METHOD.format(year=year))
    blocks = presidents(year)
    totals = [sum(block.rows[i][0] for block in blocks) for i in range(len(PRESIDENTS[year]))]
    winner = max(range(len(totals)), key=totals.__getitem__)
    options = []
    for rank, (name, label) in enumerate(PRESIDENTS[year], 1):
        option, _ = candidate(p, contest, code, rank, name, label, rank - 1 == winner)
        options.append(option)
        if rank - 1 == winner:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
    for block in blocks:
        for column, name in enumerate(block.constituencies, 1):
            id = unit(
                constituency_code(year, block.district, name),
                "tz.constituency",
                constituency_area(year, block.district, name),
                name,
            )
            for option, row in zip(options, block.rows, strict=True):
                put(p, result_set, id, option, VOTES, row[column])
            for label, measure in SIGNIFICANT.items():
                put(p, result_set, id, None, measure, block.totals[label][column])


def local_contest(p: Package, year: int, election: str, path: str, unit: Unit, result: Result, ward: bool) -> None:
    kind = "ward" if ward else "house"
    code = f"{year}:{kind}:{slug(result.district)}:{slug(result.constituency)}" + (
        f":{slug(result.ward)}" if ward else ""
    )
    place = f"{result.ward} ward, {result.constituency} constituency" if ward else f"{result.constituency} constituency"
    area = (
        ward_area(year, result.district, result.constituency, result.ward)
        if ward
        else constituency_area(year, result.district, result.constituency)
    )
    contest = key("contest", code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=COUNCILS if ward else HOUSE,
        district_area_id=area,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    title = "councillor election" if ward else "House of Representatives election"
    p.name(contest, f"{year} {title}, {place}, {result.district} district", "en", "official")
    where = f"{slug(result.district)}-{slug(result.constituency)}" + (f"-{slug(result.ward)}" if ward else "")
    p.add("slug", path=f"{path}/{'wards' if ward else 'house'}/{where}", entity_id=contest, is_current=True)
    method = UNCONTESTED_METHOD if result.uncontested else (WARD_METHOD if ward else HOUSE_METHOD)
    if year in SCANNED:
        method = SCANNED_UNCONTESTED_METHOD if result.uncontested else SCANNED_METHOD.format(kind=title, year=year)
    result_set = contest_set(p, election, contest, code, method.format(year=year), 0.0 if result.uncontested else 1.0)
    id = unit(
        ward_code(year, result.district, result.constituency, result.ward)
        if ward
        else constituency_code(year, result.district, result.constituency),
        "tz.ward" if ward else "tz.constituency",
        area,
        result.ward if ward else result.constituency,
    )
    if not result.uncontested:
        for label, measure in SIGNIFICANT.items():
            if getattr(result, label) is not None:
                put(p, result_set, id, None, measure, getattr(result, label), located(year, result, label))
    votes = [v or 0 for _, _, v in result.candidates]
    winner = max(range(len(votes)), key=votes.__getitem__)
    if result.elected is not None:
        # A scanned annex marks the candidate it declares elected; the declaration is stored as printed.
        winner = [name for name, _, _ in result.candidates].index(result.elected)
    elif len(votes) > 1 and votes.count(max(votes)) > 1:
        raise ValueError(f"tie in {code}")
    for rank, (name, label, count) in enumerate(result.candidates, 1):
        option, _ = candidate(p, contest, code, rank, name, label, rank - 1 == winner)
        if rank - 1 == winner:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        if count is not None:
            put(p, result_set, id, option, VOTES, count, located(year, result, f"cand:{rank - 1}"))


@dataclass
class Line:
    """One constituency's line in a scanned President's annex."""

    district: str
    constituency: str
    votes: dict[str, int | None]
    totals: dict[str, int | None]
    page: int
    boxes: dict[str, list[float]]
    derived: bool = False


def presidents_of(year: int) -> list[tuple[str, str]]:
    if year in SCANNED:
        return [(c["name"], party(c["party"])) for c in returns(year)["president"]["candidates"]]
    return PRESIDENTS[year]


def scanned_lines(year: int) -> list[Line]:
    return [
        Line(
            row["district"],
            row["constituency"],
            {party(label): votes for label, votes in row["votes"].items()},
            {label: row.get(label) for label in SIGNIFICANT},
            row["page"],
            row["boxes"],
            row.get("derived", False),
        )
        for row in returns(year)["president"]["rows"]
    ]


def pages_of(year: int) -> set[int]:
    data = returns(year)
    rows = data["president"]["rows"] + data["house"] + data["wards"]
    return {int(box[5]) for row in rows for box in row["boxes"].values()}


# Where a scanned annex's own national line differs from the sum of its constituencies, and why.
PRINTED_GAPS = {
    (1995, "registered"): "Bumbwini and Donge print their ballots cast in the registered column",
}


def check_scanned(year: int) -> None:
    """Fail unless the constituencies add up to the national presidential result the report prints."""
    national = returns(year)["president"]["national"]
    rows = scanned_lines(year)
    for label, printed in national["votes"].items():
        if sum(row.votes.get(party(label)) or 0 for row in rows) != printed:
            raise ValueError(f"{year}: {label}'s constituencies differ from the report's national total")
    for label in SIGNIFICANT:
        total = sum(row.totals[label] or 0 for row in rows)
        if national.get(label) is not None and total != national[label] and (year, label) not in PRINTED_GAPS:
            raise ValueError(
                f"{year}: the constituencies' {label} ({total}) differ from the national {national[label]}"
            )


def scanned_president(p: Package, year: int, election: str, path: str, unit: Unit) -> None:
    code = f"{year}:president"
    contest = key("contest", code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=ZANZIBAR,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Zanzibar presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = contest_set(
        p, election, contest, code, SCANNED_PRESIDENT_METHOD.format(year=year, extra=PRESIDENT_EXTRA.get(year, ""))
    )
    rows = scanned_lines(year)
    candidates = presidents_of(year)
    totals = [sum(row.votes.get(label) or 0 for row in rows) for _, label in candidates]
    winner = max(range(len(totals)), key=totals.__getitem__)
    options = []
    for rank, (name, label) in enumerate(candidates, 1):
        option, _ = candidate(p, contest, code, rank, name, label, rank - 1 == winner)
        options.append(option)
        if rank - 1 == winner:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
    for row in rows:
        id = unit(
            constituency_code(year, row.district, row.constituency),
            "tz.constituency",
            constituency_area(year, row.district, row.constituency),
            row.constituency,
        )
        for option, (_, label) in zip(options, candidates, strict=True):
            count = row.votes.get(label)
            if count is not None:
                put(p, result_set, id, option, VOTES, count, at(year, row.boxes[f"votes:{label}"]), row.derived)
        for label, measure in SIGNIFICANT.items():
            figure = row.totals[label]
            if figure is not None:
                put(p, result_set, id, None, measure, figure, at(year, row.boxes[label]), row.derived)
