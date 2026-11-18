"""Ethiopia's general elections of 1995 to 2015, from the National Election Board's page of past elections.

The Board's Amharic page prints, for each general election, a few HTML tables: the seats each party won in the House
of Peoples' Representatives and in the regional councils (with a split by sex in some years), and the voters
registered and voting in each region. The page prints no vote counts, so each result set holds declared seats, and the
accounting figures where the page has them. The page dates its elections by the Ethiopian calendar.

For 2010, where the page prints no seats, the House and regional council results come by constituency from the
Board's results articles of the time (see `nebe_2010`).
"""

import functools
import html
import re
from datetime import date
from typing import NamedTuple
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.et import nebe_2010

SOURCE = "et.nebe"
PAGE = "https://nebe.org.et/am/past-elections"
ETHIOPIA = str(uuid5(package.NAMESPACE, "polity\x1fET"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fETH"))


class Cell(NamedTuple):
    lines: list[str]
    rows: int
    cols: int

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    nebe_2010.fetch()


@functools.cache
def tables() -> list[list[list[Cell]]]:
    text = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf-8")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL)
    out: list[list[list[Cell]]] = []
    for table in re.findall(r"<table.*?</table>", text, flags=re.DOTALL):
        rows: list[list[Cell]] = []
        for row in re.findall(r"<tr.*?</tr>", table, flags=re.DOTALL):
            cells: list[Cell] = []
            for attrs, body in re.findall(r"<t[dh]([^>]*)>(.*?)</t[dh]>", row, flags=re.DOTALL):
                pieces = re.split(r"<br\s*/?>|</p>|</div>", body)
                lines = [
                    " ".join(html.unescape(re.sub(r"<[^>]+>", "", x)).replace("\u200b", "").split()) for x in pieces
                ]
                span = {k: int(v) for k, v in re.findall(r'(row|col)span="?(\d+)', attrs)}
                cells.append(Cell([x for x in lines if x], span.get("row", 1), span.get("col", 1)))
            rows.append(cells)
        out.append(rows)
    return out


class Seats(NamedTuple):
    region: str
    party: str
    house: tuple[int, int, int]
    council: tuple[int, int, int]


def number(text: str) -> int:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def seat_rows(index: int) -> list[tuple[str, str, list[int]]]:
    """Rows of a party-by-region seat table as (region, party, six numbers), with the region carried down and `ድምር`
    (sum) rows kept under the party `ድምር`."""
    out: list[tuple[str, str, list[int]]] = []
    region = ""
    for row in tables()[index]:
        if len(row) < 8:
            continue
        tail = row[-7:-1]
        head = row[:-7]
        if not all(re.fullmatch(r"[\d,\s]*", c.text) for c in tail):
            continue
        names = [c for c in head if c.text]
        parties = names[-1].lines if names else []
        if len(head) == 2 and head[0].text or len(head) == 1 and head[0].text and head[0].rows > 1:
            region = head[0].text
        if not parties:
            continue
        columns = [c.lines or [""] for c in tail]
        for i, party in enumerate(parties):
            out.append((region, party, [number(c[i]) if i < len(c) else 0 for c in columns]))
    return out


REGIONS = {
    "TG": ("Tigray", "ትግራይ"),
    "AF": ("Afar", "አፋር"),
    "AM": ("Amhara", "አማራ"),
    "OR": ("Oromia", "ኦሮሚያ"),
    "SO": ("Somali", "ሶማሌ"),
    "BG": ("Benishangul-Gumuz", "ቤንሻንጉል ጉሙዝ"),
    "SN": ("Southern Nations, Nationalities, and Peoples", "የደቡብ ብሔሮች፣ ብሔረሰቦችና ሕዝቦች"),
    "GA": ("Gambela", "ጋምቤላ"),
    "HA": ("Harari", "ሐረሪ"),
    "AA": ("Addis Ababa", "አዲስ አበባ"),
    "DD": ("Dire Dawa", "ድሬዳዋ"),
}
PREFIXES = (
    ("ትግራይ", "TG"),
    ("አፋር", "AF"),
    ("አማራ", "AM"),
    ("ኦሮሚያ", "OR"),
    ("ሶማሌ", "SO"),
    ("ቤንሻንጉል", "BG"),
    ("ቤኒሻንጉል", "BG"),
    ("ቤ/", "BG"),
    ("ደ/", "SN"),
    ("የደቡብ", "SN"),
    ("ጋምቤላ", "GA"),
    ("ሐረሪ", "HA"),
    ("ሐረር", "HA"),
    ("ድሬዳዋ", "DD"),
    ("አዲስ አበባ", "AA"),
    ("ክልል 14", "AA"),
)


def region(text: str) -> str:
    text = " ".join(text.split())
    for prefix, code in PREFIXES:
        if text.startswith(prefix):
            return code
    raise ValueError(f"unknown region {text!r}")


HOMOPHONES = str.maketrans(
    {
        **{chr(a + i): chr(0x1200 + i) for a in (0x1210, 0x1280) for i in range(8)},
        **{chr(0x1220 + i): chr(0x1230 + i) for i in range(8)},
        **{chr(0x12D0 + i): chr(0x12A0 + i) for i in range(8)},
        **{chr(0x1340 + i): chr(0x1338 + i) for i in range(8)},
    }
)
PREFERRED = {"ኢህአዴግ": "ኢህአዴግ", "ህወሀት": "ህወሓት"}
ALIASES = {"ህወሃት": "ህወሀት", "ጋሕአዴን": "ጋህዴን", "ጋህአዴን": "ጋህዴን"}
INDEPENDENTS = {"በግል", "የግል"}
SPECIAL = "ልዩ ወኪሎች"
FRONT = re.compile(r"\s*/\s*ኢህአዴግ$")


def abbreviation(text: str) -> str:
    """A party's abbreviation as printed, without its full name in parentheses or the front's suffix."""
    text = " ".join(text.split())
    match = re.fullmatch(r".*\(([^()]+)\)", text)
    text = match.group(1) if match else text
    return text if text == "ኢህአዴግ" else FRONT.sub("", text.replace("ኢሕአዴግ", "ኢህአዴግ")) if "/" in text else text


def party_key(text: str) -> str:
    """The key of a party as the page abbreviates it. Spellings that differ only in homophone letters, in spacing, or
    in the `/ኢህአዴግ` suffix that marks a member of the front are one key."""
    text = " ".join(text.replace(" ", " ").split()).translate(HOMOPHONES)
    text = text.replace(" /", "/").replace("/ ", "/")
    if text in INDEPENDENTS:
        return "በግል"
    if text != "ኢህአዴግ":
        text = FRONT.sub("", text)
    text = text.replace(" ", "") if text != SPECIAL else text
    return ALIASES.get(text, text)


CANDIDATE_SUFFIX = re.compile(r"\s*[(/]\s*ኢ[ሕህ]አዴግ\s*[)/]*\s*$")
# Spellings of one party in the 2010 results articles that differ by more than homophone letters: a vowel order, a
# dropped letter, or a slash between the letters of the abbreviation. `ፖ` closing an abbreviation is `ፓ`, party.
CANDIDATE_ALIASES = {
    "ኢፍዴሀግ": "ኢፍዴሃግ",
    "ኢፍዲሃግ": "ኢፍዴሃግ",
    "ኢፌዴሃግ": "ኢፍዴሃግ",
    "ኤፍዴሃግ": "ኢፍዴሃግ",
    "ኢፍዴሃግ(ኢዲህ)": "ኢፍዴሃግ",
    "ሲሀህደድ": "ሲሀህዴድ",
    "ሲሀህድድ": "ሲሀህዴድ",
    "ትውብዴአፓ": "ትወብዴአፓ",
    "ትወብድአፓ": "ትወብዴአፓ",
    "ኢደፓ": "ኢዴፓ",
    "ኤዴፓ": "ኢዴፓ",
    "ዳዱክዴድ": "ደዱክዴድ",
    "ደዱክህዴድ": "ደዱክዴድ",
    "ሶህደፓ": "ሶህዴፓ",
    "ሶህህዴፓ": "ሶህዴፓ",
    "ጌህደድ": "ጌህዴድ",
    "ወ/ቋ/ተ/ህ/ዴ/ን": "ወቋተህዴን",
    "ማኮልወህድአን": "ማኮልወህአን",
    "ኮህዴሀ": "ኮህዴህ",
    "ሶጐህዴድ": "ሶጎህዴድ",
    "ሽአህዴድ": "ሸአህዴድ",
    "ኤሰዴፓ": "ኢሰዴፓ",
    "ኢስዴፓ": "ኢሰዴፓ",
    "መድረክ/ኦ.ብ.ኮ/": "መድረክ",
}


def candidate_party(text: str) -> tuple[str, str]:
    """The key of a candidate's party in the 2010 results articles, and the abbreviation as printed without the
    front's suffix."""
    label = CANDIDATE_SUFFIX.sub("", " ".join(text.split()))
    key = party_key(label)
    key = CANDIDATE_ALIASES.get(key, key)
    if key.endswith("ፖ"):
        key = key[:-1] + "ፓ"
    return key, label


class Election(NamedTuple):
    year: int
    day: date
    precision: str
    printed: str | None
    registered: dict[str, int]
    voted: dict[str, int]
    national: tuple[int, int]
    house: dict[str, int]
    house_regions: dict[str, dict[str, int]]
    councils: dict[str, dict[str, int]]
    votes: dict[str, dict[str, int]]
    sizes: dict[str, int]
    labels: dict[str, str]
    notes: list[str]


@functools.cache
def paragraphs() -> list[str]:
    text = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf-8")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL)
    text = re.sub(r"<table.*?</table>", "", text, flags=re.DOTALL)
    return [
        " ".join(html.unescape(re.sub(r"<[^>]+>", "", m)).split())
        for m in re.findall(r"<p[^>]*>(.*?)</p>", text, flags=re.DOTALL)
    ]


def narrative(marker: str) -> tuple[int, int]:
    """The registered and voting totals the page prints in the paragraph that opens an election's section, or in the
    one after it."""
    text = paragraphs()
    for at, paragraph in enumerate(text):
        if marker in paragraph:
            found = [number(n) for n in re.findall(r"\((\d{1,3}(?:,\d{3}){2,})\)", " ".join(text[at : at + 2]))]
            if len(found) >= 2:
                return found[0], found[1]
    raise LookupError(marker)


def table_at(index: int, header: str) -> list[list[Cell]]:
    rows = tables()[index]
    if header not in " ".join(c.text for c in rows[0]):
        raise ValueError(f"table {index} is not {header!r}")
    return rows


def regional_figures(index: int, header: str, columns: dict[str, int]) -> dict[str, dict[str, int]]:
    """Per-region figures of a registration table, with the printed sum row kept under `ET`, and each row's men and
    women added up against its total."""
    out: dict[str, dict[str, int]] = {}
    for row in table_at(index, header):
        if len(row) < max(columns.values()) + 1 or not re.fullmatch(r"[\d,\s]+", row[1].text or "x"):
            continue
        name = row[0].text
        code = "ET" if name == "ድምር" else region(name)
        out[code] = {key: number(row[at].text) for key, at in columns.items()}
    return out


SEAT_TABLES = {1995: (12, 13, 14, 15, 16), 2000: (6, 7, 8, 9, 10)}
DAYS = {
    1995: (date(1995, 1, 1), "year", None),
    2000: (date(2000, 1, 1), "year", None),
    2005: (date(2005, 5, 15), "day", "ግንቦት 07 ቀን 1997 ዓ.ም."),
    2010: (date(2010, 5, 23), "day", "ግንቦት 15 ቀን 2002 ዓ.ም."),
    2015: (date(2015, 5, 24), "day", "ግንቦት 16 ቀን 2007 ዓ.ም."),
}
MARKERS = {
    1995: "በ1987 ዓ.ም.",
    2000: "በ1992 ሀገር",
    2005: "ግንቦት 07 ቀን 1997",
    2010: "ግንቦት 15 ቀን 2002",
    2015: "ግንቦት 16 ቀን 2007",
}
# Cells of the page that its own sums or columns show to be misprinted, with the figure they prove.
CORRECTED = {
    (2000, "AM", "ብአዴን", "house"): (
        134,
        (
            "The row prints 124 seats, but its 120 men and 14 women make 134, which is what the region's printed sum "
            "of 138 needs once the 1 of አህዴን and the 3 of አህዴድ are added."
        ),
    ),
}
# Printed region sums that do not match their own rows, left as printed in the source and not used.
DISCLOSED = {
    (2000, "AF", "council"): "The sum row prints 84 council seats, but its rows and its 85 men and 2 women make 87.",
    (2000, "SN", "council"): "The sum row prints 353 council seats, but the party rows above it make 350.",
}


def seats_by_region(
    year: int, notes: list[str]
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]], dict[str, str]]:
    house: dict[str, dict[str, int]] = {}
    council: dict[str, dict[str, int]] = {}
    labels: dict[str, str] = {}
    sums: dict[tuple[str, str], list[int]] = {}
    for index in SEAT_TABLES[year]:
        for place, party, n in seat_rows(index):
            if not place:
                if year == 1995 and party == "ድምር":
                    place, party = "ትግራይ", "ሕወሐት"
                else:
                    continue
            if party == "ጠቅላላ ድምር":
                continue
            code = region(place)
            if party == "ድምር":
                if any(n):
                    sums[(code, "")] = [a + b for a, b in zip(sums.get((code, ""), [0] * 6), n)]
                continue
            key = party_key(party)
            labels.setdefault(key, abbreviation(party))
            if (year, code, key, "house") in CORRECTED:
                n = [n[0], n[1], CORRECTED[(year, code, key, "house")][0], *n[3:]]
            if n[0] + n[1] != n[2] or n[3] + n[4] != n[5]:
                raise ValueError(f"{year} {code} {party}: men and women do not make the total: {n}")
            if n[2]:
                house.setdefault(code, {})[key] = house.get(code, {}).get(key, 0) + n[2]
            if n[5]:
                council.setdefault(code, {})[key] = council.get(code, {}).get(key, 0) + n[5]
    for (code, _), printed in sums.items():
        for kind, mine, at in (("house", house.get(code, {}), 2), ("council", council.get(code, {}), 5)):
            if sum(mine.values()) != printed[at]:
                why = DISCLOSED.get((year, code, kind))
                if why is None:
                    raise ValueError(
                        f"{year} {code} {kind} rows make {sum(mine.values())}, the page prints {printed[at]}"
                    )
                notes.append(f"{REGIONS[code][0]} {kind}: {why}")
    return house, council, labels


def registration(year: int) -> tuple[dict[str, int], dict[str, int]]:
    """Registered and voting citizens per region, with each row's men and women and the printed sum row checked."""
    if year == 1995:
        columns = {"registered": 1, "voted": 2}
        rows = regional_figures(11, "ክልል", columns)
    else:
        columns = {"rm": 1, "rf": 2, "registered": 3, "vm": 4, "vf": 5, "voted": 6}
        rows = regional_figures({2000: 5, 2005: 2}[year], "ክልል", columns)
    printed = rows.pop("ET")
    for code, row in rows.items():
        if "rm" in row and (row["rm"] + row["rf"] != row["registered"] or row["vm"] + row["vf"] != row["voted"]):
            raise ValueError(f"{year} {code}: men and women do not make the total: {row}")
    for key in ("registered", "voted"):
        if sum(row[key] for row in rows.values()) != printed[key]:
            raise ValueError(
                f"{year} {key}: regions make {sum(row[key] for row in rows.values())}, page prints {printed[key]}"
            )
    return {c: r["registered"] for c, r in rows.items()}, {c: r["voted"] for c, r in rows.items()}


def full_name(text: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.*?)\s*\(([^()]+)\)", text)
    return (party_key(match.group(2)), match.group(1)) if match else None


def house_2005(labels: dict[str, str], names: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    total = 0
    for row in table_at(4, "ተ.ቁ."):
        if len(row) == 3 and row[0].text.isdigit():
            party = full_name(row[1].text)
            key = party[0] if party else party_key(row[1].text)
            if party:
                names[key] = party[1]
            labels.setdefault(key, abbreviation(row[1].text))
            out[key] = out.get(key, 0) + number(row[2].text)
        elif len(row) == 2 and "ድምር" in row[0].text:
            total = number(row[1].text)
    if sum(out.values()) != total:
        raise ValueError(f"2005 House rows make {sum(out.values())}, page prints {total}")
    return out


def council_2005(labels: dict[str, str]) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    sizes: dict[str, int] = {}
    for row in table_at(3, "ተ.ቁ."):
        if len(row) != 6 or not row[0].text.isdigit():
            continue
        code = region(row[1].text)
        sizes[code] = number(row[2].text)
        parties = [re.sub(r"^\d+\.\s*", "", line) for line in row[3].lines]
        # A party printed across two lines, as `1. ኦህዴድ /` over `ኢህአዴግ`, is one party.
        joined: list[str] = []
        for line in row[3].lines:
            if re.match(r"\d+\.", line) or not joined:
                joined.append(re.sub(r"^\d+\.\s*", "", line))
            else:
                joined[-1] += " " + line
        parties = joined
        seats = [number(x) for x in row[4].lines]
        if len(parties) != len(seats):
            raise ValueError(f"2005 {code}: {len(parties)} parties and {len(seats)} seat counts")
        for party, n in zip(parties, seats, strict=True):
            key = party_key(party)
            labels.setdefault(key, abbreviation(party))
            out.setdefault(code, {})[key] = n
        if sum(seats) != sizes[code]:
            raise ValueError(f"2005 {code}: seats make {sum(seats)}, page prints {sizes[code]}")
    return out, sizes


def seats_2015(
    labels: dict[str, str],
) -> tuple[dict[str, int], dict[str, dict[str, int]], dict[str, dict[str, int]], dict[str, int], list[str]]:
    notes: list[str] = []
    house: dict[str, int] = {}
    totals: dict[str, int] = {}
    printed = (0, 0)
    for row in table_at(0, ""):
        if len(row) != 9 or not re.fullmatch(r"\d+", row[3].text):
            continue
        n = [number(c.text) for c in row[1:4]] + [number(c.text) for c in row[5:8]]
        if n[0] + n[1] != n[2]:
            raise ValueError(f"2015 {row[0].text}: men and women do not make the total: {n}")
        if row[0].text == "ድምር":
            printed = (n[2], n[5])
            continue
        key = party_key(row[0].text)
        labels.setdefault(key, abbreviation(row[0].text))
        if n[2]:
            house[key] = n[2]
        if n[5]:
            totals[key] = n[5]
    if (sum(house.values()), sum(totals.values())) != printed:
        raise ValueError(
            f"2015 party rows make {sum(house.values())} and {sum(totals.values())}, page prints {printed}"
        )
    council: dict[str, dict[str, int]] = {}
    votes: dict[str, dict[str, int]] = {}
    sizes: dict[str, int] = {}
    place = ""
    for row in table_at(1, "ክልል")[1:]:
        if len(row) == 6:
            place = region(row[0].text)
            sizes[place] = number(row[1].text)
            cells = row[3:]
        elif len(row) == 3:
            cells = row
        else:
            continue
        party = party_key(cells[2].text)
        labels.setdefault(party, abbreviation(cells[2].text))
        if place == "AF" and party == "ኦህዴድ":
            # Afar's second row names the Oromo People's Democratic Organization, but the party table above gives it
            # no seat there, and gives Afar's party አህዴድ 3 council seats, so the row is አህዴድ's.
            party = party_key("አህዴድ")
            labels.setdefault(party, "አህዴድ")
            notes.append(
                "Afar's second row names ኦህዴድ/ኢህአዴግ with 3 seats; the page's party table gives አህዴድ those 3 seats and "
                "ኦህዴድ none in Afar, and its totals for ኦህዴድ add up only without them, so the row is read as አህዴድ."
            )
        council.setdefault(place, {})[party] = number(cells[1].text)
        votes.setdefault(place, {})[party] = number(cells[0].text)
    for party, n in totals.items():
        if sum(r.get(party, 0) for r in council.values()) != n:
            raise ValueError(
                f"2015 {party}: regions make {sum(r.get(party, 0) for r in council.values())}, page prints {n}"
            )
    return house, council, votes, sizes, notes


@functools.cache
def elections() -> dict[int, Election]:
    out: dict[int, Election] = {}
    names: dict[str, str] = {}
    for year in (1995, 2000, 2005, 2010, 2015):
        day, precision, printed = DAYS[year]
        notes: list[str] = []
        labels: dict[str, str] = {}
        registered: dict[str, int] = {}
        voted: dict[str, int] = {}
        house_regions: dict[str, dict[str, int]] = {}
        votes: dict[str, dict[str, int]] = {}
        sizes: dict[str, int] = {}
        national = narrative(MARKERS[year])
        if year in SEAT_TABLES:
            house_regions, council, labels = seats_by_region(year, notes)
            registered, voted = registration(year)
            house: dict[str, int] = {}
            for parties in house_regions.values():
                for key, n in parties.items():
                    house[key] = house.get(key, 0) + n
        elif year == 2005:
            house = house_2005(labels, names)
            council, sizes = council_2005(labels)
            registered, voted = registration(year)
        elif year == 2015:
            house, council, votes, sizes, notes = seats_2015(labels)
        else:
            house, council = {}, {}
        if registered and (sum(registered.values()), sum(voted.values())) != national:
            raise ValueError(
                f"{year}: regions make {sum(registered.values())} and {sum(voted.values())}, page prints {national}"
            )
        labels = {key: PREFERRED.get(key, label) for key, label in labels.items()}
        out[year] = Election(
            year,
            day,
            precision,
            printed,
            registered,
            voted,
            national,
            house,
            house_regions,
            council,
            votes,
            sizes,
            labels,
            notes,
        )
    return out


@functools.cache
def candidate_parties() -> dict[str, str]:
    """The parties of the 2010 candidates, each with its most printed abbreviation."""
    seen: dict[str, dict[str, int]] = {}
    for body in nebe_2010.BUNDLES:
        for c in nebe_2010.constituencies(body):
            for candidate in c.candidates:
                name, label = candidate_party(candidate.party)
                if name not in {"በግል", SPECIAL}:
                    seen.setdefault(name, {})[label] = seen.get(name, {}).get(label, 0) + 1
    return {name: max(sorted(labels), key=lambda x: labels[x]) for name, labels in seen.items()}


def official_names() -> dict[str, str]:
    """The full names the page prints beside the party abbreviations of the 2005 House table."""
    names: dict[str, str] = {}
    house_2005({}, names)
    return names


ISO = {
    "TG": "ET-TI",
    "AF": "ET-AF",
    "AM": "ET-AM",
    "OR": "ET-OR",
    "SO": "ET-SO",
    "BG": "ET-BE",
    "SN": "ET-SN",
    "GA": "ET-GA",
    "HA": "ET-HA",
    "AA": "ET-AA",
    "DD": "ET-DD",
}
ENGLISH = {
    "ህወሀት": "Tigray People's Liberation Front",
    "ብአዴን": "Amhara National Democratic Movement",
    "ኦህዴድ": "Oromo People's Democratic Organization",
    "ደኢህዴን": "Southern Ethiopian Peoples' Democratic Movement",
    "ኢህአዴግ": "Ethiopian People's Revolutionary Democratic Front",
    "ቅንጅት": "Coalition for Unity and Democracy",
    "ኢዴሃህ": "United Ethiopian Democratic Forces",
    "ኦፌዴን": "Oromo Federalist Democratic Movement",
    "አብዴፓ": "Afar National Democratic Party",
    "ሀብሊ": "Harari National League",
    "ጋህዴን": "Gambela Peoples' Democratic Movement",
    "ቤጉህዴአግ": "Benishangul-Gumuz Peoples' Democratic Unity Front",
    "ቤጉህዴፓ": "Benishangul-Gumuz Peoples' Democratic Party",
    "ሶህዴፓ": "Somali Peoples' Democratic Party",
    "ኢሶህዴፓ": "Ethiopian Somali Peoples' Democratic Party",
    "መድረክ": "Ethiopian Federal Democratic Unity Forum",
    "መኢአድ": "All Ethiopian Unity Party",
    "ኢዴፓ": "Ethiopian Democratic Party",
}
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fnebe"))
SOURCE_2010 = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1felectionethiopia-2010"))
SCHEMES_2010 = {"house": "et.hopr_constituency_2010", "council": "et.rc_constituency_2010"}
HOUSE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fbody\x1fhouse"))
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fplurality"))
RULES = {"formula": "fptp"}
BLOCK = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fblock"))
BLOCK_NOTE = (
    "In 2010 a regional council constituency could elect more than one member. The candidates with the most votes "
    "fill its seats, and a party can field as many candidates as there are seats."
)
METHOD_2010 = (
    "Candidates and votes as printed in the National Election Board's 2010 results article for the zone, read from a "
    "web.archive.org capture of electionethiopia.org. The article prints each candidate's votes at the ordinary "
    "polling stations, at the stations of higher education institutions, and at the stations of defense training "
    "centers, and their total; each total was checked against its three parts, and only the total is stored. The "
    "article prints no registered voters, ballots cast, or invalid ballots for the constituency."
)
HOUSE_METHOD_2010 = (
    f"{METHOD_2010} The candidate the article ranks first, who leads every other candidate, is stored as elected."
)
COUNCIL_METHOD_2010 = (
    f"{METHOD_2010} The article does not say how many seats the constituency fills or who was elected, so no outcome "
    "is stored."
)
SYSTEM_NOTE = (
    "Each constituency of the House of Peoples' Representatives and of the regional councils elects one member by "
    "plurality. The Board's page reports only the seats each party won in a region, not the constituencies."
)
METHOD = (
    "Seats won by party as printed on the National Election Board's page of past elections. The page prints no vote "
    "counts for the constituencies, so the declared seats are the outcome. Registered voters are the citizens the page "
    "says registered, and voters are those it says voted. Where the page splits seats or voters by sex, only the "
    "totals are stored."
)

HOUSE_NOTES = {
    1995: [
        (
            "The page's last row prints 1,452 seats in the regional councils, but its 1,355 men and 77 women, and the "
            "regions' own rows, make 1,432, which is what is stored."
        )
    ],
    2000: [
        (
            "The page's tables list no seats for the Somali region, where it counts 1,480,660 registered voters and "
            "948,360 voters, so the party seats here are those of the other regions."
        ),
        (
            "The Amhara row of ብአዴን prints 124 seats, but its 120 men and 14 women make 134, which is also what the "
            "region's printed sum of 138 needs, so 134 is stored. The page's text repeats 124 for the party."
        ),
    ],
    2005: ["The page gives the House result only by party for the country, not by region."],
    2010: [
        (
            "For this election the page prints only the registered and voting totals and says that the front and its "
            "affiliated parties won 99.6% of the 547 constituencies, with one party member and one independent "
            "holding the rest. It prints no seats by party. The constituency results, with the elected members, are "
            "stored as contests of their own, from the Board's 2010 results articles."
        )
    ],
    2015: [
        (
            "The page prints 546 seats won of 547 constituencies. It gives the House result only by party for the "
            "country, not by region."
        )
    ],
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(code: str) -> str:
    return key("area", "et.region", code)


def party(name: str) -> str:
    return key("party", name)


def council(code: str) -> str:
    return key("body", "council", code)


def packages() -> list[str]:
    return ["et.nebe/geo", "et.nebe/registry", *(f"et.nebe/ge-{year}" for year in elections())]


def parse() -> None:
    geo().write()
    registry().write()
    for year in elections():
        general(year).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        "et.nebe/geo",
        SOURCE,
        "Ethiopia's nine regions and two city administrations, as the National Election Board's results tables list "
        "them, and the House and regional council constituencies of 2010, without boundaries",
    )
    p.external |= {ETHIOPIA, TERRITORY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="National Election Board of Ethiopia",
        title="Past elections (ቀደምት ምርጫዎች)",
        url=PAGE,
        license="No license stated; published by the electoral authority for public use",
        license_url="https://nebe.org.et/",
        attribution="National Election Board of Ethiopia, nebe.org.et",
    )
    p.artifact(raw.cached(SOURCE, PAGE), SOURCE_ID)
    p.add(
        "source",
        id=SOURCE_2010,
        kind="official_body",
        publisher="National Election Board of Ethiopia",
        title="Results of the 2002 E.C. general election (የ 2002 ጠቅላላ ምርጫ ውጤት), electionethiopia.org",
        url=nebe_2010.LISTINGS["house"],
        license="No license stated; published by the electoral authority for public use",
        license_url="http://www.electionethiopia.org/",
        attribution="National Election Board of Ethiopia, electionethiopia.org, read from web.archive.org captures",
    )
    for body in nebe_2010.BUNDLES:
        for artifact in nebe_2010.cited(body):
            p.artifact(artifact, SOURCE_2010)
    p.add(
        "scheme", code="et.region", polity_id=ETHIOPIA, kind="administrative", rank=1, noun="region", code_system=None
    )
    for code, (english, amharic) in REGIONS.items():
        id = area(code)
        p.add("area", id=id, scheme_code="et.region")
        p.add("area_code", area_id=id, code=ISO[code])
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, english, "en", "official")
        p.name(id, amharic, "am", "native", False)
    for body, scheme in SCHEMES_2010.items():
        p.add(
            "scheme",
            code=scheme,
            polity_id=ETHIOPIA,
            kind="electoral_district",
            rank=2,
            noun="constituency",
            code_system=None,
        )
        for c in nebe_2010.constituencies(body):
            id = constituency(c)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=c.code)
            p.add("area_parent", area_id=id, parent_id=area(c.region))
            p.name(id, c.name, "am", "official")
            p.name(id, nebe_2010.romanize(c.name).title(), "en", "transliteration", False)
    return p


def constituency(c: nebe_2010.Constituency) -> str:
    return key("area", SCHEMES_2010[c.body], c.code)


def registry() -> Package:
    p = Package(
        "et.nebe/registry",
        SOURCE,
        "The parties that won seats in Ethiopia's general elections of 1995 to 2015 and those that ran in 2010, the "
        "House of Peoples' Representatives, the regional councils, and the plurality rule, from the National "
        "Election Board's pages",
        depends=["et.nebe/geo"],
    )
    p.external |= {ETHIOPIA}
    p.uses(raw.cached(SOURCE, PAGE))
    p.add("body", id=HOUSE, polity_id=ETHIOPIA, kind="lower_house")
    p.name(HOUSE, "House of Peoples' Representatives", "en", "official")
    p.name(HOUSE, "የሕዝብ ተወካዮች ምክር ቤት", "am", "native", False)
    for code, (english, _) in REGIONS.items():
        body = council(code)
        p.add("body", id=body, polity_id=ETHIOPIA, kind="council")
        p.name(
            body, f"{english} City Council" if code in {"AA", "DD"} else f"{english} Regional Council", "en", "official"
        )
    p.add("electoral_system", id=SYSTEM, polity_id=ETHIOPIA, rules=RULES, rules_version=1)
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    p.add("electoral_system", id=BLOCK, polity_id=ETHIOPIA, rules={"formula": "block"}, rules_version=1)
    p.add("note", id=key("note", BLOCK), entity_id=BLOCK, kind="methodology", body_md=BLOCK_NOTE, lang="en")
    for body in nebe_2010.BUNDLES:
        for artifact in nebe_2010.cited(body):
            p.uses(artifact)
    labels: dict[str, str] = {}
    for election in elections().values():
        for name, label in election.labels.items():
            labels.setdefault(name, label)
    for name, label in candidate_parties().items():
        labels.setdefault(name, label)
    names = official_names()
    for name in sorted(labels):
        if name in {"በግል", SPECIAL}:
            continue
        id = party(name)
        p.add("party", id=id, kind="bloc" if name == "ኢህአዴግ" else "party", polity_id=ETHIOPIA)
        p.add("external_id", entity_id=id, scheme="et.nebe.party", value=name)
        if name in names:
            p.name(id, names[name], "am", "official")
            p.name(id, labels[name], "am", "abbreviation", False)
        else:
            p.name(id, labels[name], "am", "abbreviation")
        if name in ENGLISH:
            p.name(id, ENGLISH[name], "en", "official")
    return p


def general(year: int) -> Package:
    e = elections()[year]
    p = Package(
        f"et.nebe/ge-{year}",
        SOURCE,
        f"The {year} Ethiopian general election: seats won by party in the House of Peoples' Representatives and the "
        "regional councils, and voters by region, from the National Election Board's page",
        depends=["et.nebe/geo", "et.nebe/registry"],
    )
    p.external |= {ETHIOPIA, TERRITORY}
    p.uses(raw.cached(SOURCE, PAGE))
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=ETHIOPIA,
        kind="general",
        date_from=e.day,
        date_to=e.day if e.precision == "day" else date(year, 12, 31),
        date_precision=e.precision,
        original_text=e.printed,
        status="held",
    )
    if year == 2000:
        p.accept(
            "accounting.turnout",
            "The page prints 521,260 voters in Afar against 495,181 registered, and its 328,908 men voting against "
            "303,272 registered men, so the source's own figures exceed the roll.",
        )
    p.name(election, f"{year} Ethiopian general election", "en", "official")
    p.name(election, f"የ{year - 8} ዓ.ም. ሀገር አቀፍ ጠቅላላ ምርጫ", "am", "native", False)
    path = f"et/{e.day}-general" if e.precision == "day" else f"et/{year}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)
    units: dict[str, str] = {}

    def unit(code: str) -> str:
        if code not in units:
            units[code] = key("unit", year, code)
            p.add(
                "reporting_unit",
                id=units[code],
                polity_id=ETHIOPIA,
                scheme_code="et.region",
                code=f"{year}:{code}",
                area_relation="equals",
                area_id=TERRITORY if code == "ET" else area(code),
                kind="standard",
            )
        return units[code]

    def contest(name: str, slug: str, body: str, size: int | None, parties: dict[str, int], notes: list[str]) -> str:
        id = key("contest", year, slug)
        p.add(
            "contest",
            id=id,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=TERRITORY if body == HOUSE else area(slug.removeprefix("council-")),
            magnitude=size if size is not None and size == sum(parties.values()) else None,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(id, f"{year} {name}", "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(name)}", entity_id=id, is_current=True)
        result_set = key("result_set", year, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for at, text in enumerate(notes):
            p.add("note", id=key("note", result_set, at), entity_id=result_set, kind="caveat", body_md=text, lang="en")
        for at, (name_, seats) in enumerate(sorted(parties.items(), key=lambda item: (-item[1], item[0]))):
            option = key("option", year, slug, name_)
            other = name_ in {"በግል", SPECIAL}
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="others" if other else "list",
                party_id=None if other else party(name_),
                ballot_position=at + 1,
                ballot_label=e.labels.get(name_, name_),
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
        return result_set

    def value(result_set: str, slug: str, code: str, option: str | None, measure: int, n: int) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit(code),
            option_id=None if option is None else key("option", year, slug, option),
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=n,
        )

    def accounting(result_set: str, slug: str, codes: list[str]) -> None:
        for code in codes:
            if code in e.registered:
                value(result_set, slug, code, None, 2, e.registered[code])
                value(result_set, slug, code, None, 14, e.voted[code])

    house_notes = HOUSE_NOTES.get(year, [])
    codes = list(e.registered)
    if e.house or year == 2010:
        slug = "house"
        house = contest("House of Peoples' Representatives election", slug, HOUSE, None, e.house, house_notes)
        if e.registered:
            accounting(house, slug, codes)
        else:
            value(house, slug, "ET", None, 2, e.national[0])
            value(house, slug, "ET", None, 14, e.national[1])
        for code, parties in e.house_regions.items():
            for name_, seats in parties.items():
                value(house, slug, code, name_, 16, seats)
    if year == 2010:
        constituencies_2010(p, election, path)
    for code, parties in e.councils.items():
        english = REGIONS[code][0]
        title = f"{english} {'City' if code in {'AA', 'DD'} else 'Regional'} Council election"
        slug = f"council-{code}"
        notes = [text.split(": ", 1)[1] for text in e.notes if text.startswith(f"{english} council:")]
        if year == 2015 and code == "AF":
            notes = [text for text in e.notes if text.startswith("Afar's")]
        if year == 2015 and e.votes.get(code):
            notes.append(
                "The page prints the votes beside the parties that won seats, headed 'votes obtained'. It prints none "
                "for the other parties, so the votes stored are those of the parties with seats only."
            )
        if code in e.sizes and e.sizes[code] != sum(parties.values()):
            notes.append(f"The page lists {e.sizes[code]} seats for the council and {sum(parties.values())} seats won.")
        result_set = contest(title, slug, council(code), e.sizes.get(code), parties, notes)
        accounting(result_set, slug, [code])
        for name_, seats in parties.items():
            value(result_set, slug, code, name_, 16, seats)
            if name_ in e.votes.get(code, {}):
                value(result_set, slug, code, name_, 1, e.votes[code][name_])
    return p


def constituencies_2010(p: Package, election: str, path: str) -> None:
    """The House and regional council contests of 2010, one per constituency, from the Board's results articles."""
    for artifact in {a.url: a for body in nebe_2010.BUNDLES for a in nebe_2010.cited(body)}.values():
        p.uses(artifact)
    slugs: set[str] = set()
    for body in nebe_2010.BUNDLES:
        house = body == "house"
        for c in nebe_2010.constituencies(body):
            tag = f"{body}:{c.code}"
            contest = key("contest", 2010, tag)
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=HOUSE if house else council(c.region),
                district_area_id=constituency(c),
                magnitude=1 if house else None,
                round_number=1,
                electoral_system_id=SYSTEM if house else BLOCK,
                default_result_kind="counted",
            )
            roman = nebe_2010.romanize(c.name).title()
            p.name(contest, c.name, "am", "official")
            p.name(contest, roman, "en", "transliteration", False)
            zone = changeset.slug(nebe_2010.romanize(c.zone)) if c.zone else ""
            slug = "-".join(
                x
                for x in (
                    "hopr" if house else "council",
                    changeset.slug(REGIONS[c.region][0]),
                    zone,
                    changeset.slug(roman),
                    "" if c.seq == 1 else str(c.seq),
                )
                if x
            )
            if slug in slugs:
                raise ValueError(f"2010: slug {slug} repeats")
            slugs.add(slug)
            p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
            result_set = key("result_set", 2010, tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=SOURCE_2010,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            method = HOUSE_METHOD_2010 if house else COUNCIL_METHOD_2010
            p.add(
                "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
            )
            notes = list(c.notes)
            missing = [x for x in c.candidates if x.votes is None]
            for x in missing:
                notes.append(
                    f"The page's row for {x.name} ({x.party}), ranked {x.rank} of {len(c.candidates)}, prints two "
                    "vote figures where the other rows print four, so it does not say which figure is which, and no "
                    "votes are stored for this candidate."
                )
            for at, text in enumerate(notes):
                p.add(
                    "note", id=key("note", result_set, at), entity_id=result_set, kind="caveat", body_md=text, lang="en"
                )
            unit = key("unit", 2010, tag)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ETHIOPIA,
                scheme_code=SCHEMES_2010[body],
                code=f"2010:{c.code}",
                area_relation="equals",
                area_id=constituency(c),
                kind="standard",
            )
            for x in c.candidates:
                option = key("option", 2010, tag, x.row)
                name, _ = candidate_party(x.party)
                independent = name == "በግል"
                party_id = None if independent else party(name)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=x.name,
                )
                person = key("person", 2010, tag, x.row)
                p.add("person", id=person, gender=x.sex)
                p.name(person, x.name, "am", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", 2010, tag, x.row),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=x.name,
                    member_of_party_id=party_id,
                    is_independent=independent,
                )
                if house:
                    p.add(
                        "outcome", result_set_id=result_set, option_id=option, kind="elected" if x.rank == 1 else "lost"
                    )
                if x.votes is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=x.votes[3],
                    )
