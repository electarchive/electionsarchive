"""Bosnia and Herzegovina's 1996 to 2000 elections from the Central Election Commission's archived result PDFs.

The Commission's results archive keeps the reports of the OSCE Provisional Election Commission, which ran the
elections from 1996 to 2000:

- 1996 general elections: the certified results as the Federation's official gazette printed them (Službene novine
  Federacije BiH 20/1996), with the Presidency, both delegations of the state House of Representatives, the
  Federation's House of Representatives, and the ten cantonal assemblies, each for its whole district.
- 1997 municipal elections: one report per municipality, with each list's votes cast in the municipality and the
  votes cast outside it (by displaced voters and refugees), counted in the Counting Centre.
- 1998 general elections: the vote count summary of every race, with valid and invalid ballots, and the seat
  allocation by party, for the state, entity, and cantonal races and the eleven municipalities that voted for
  their councils that year.
- 2000 municipal elections: one final report per municipality, with each list's votes in and out of the
  municipality and its seats.
- 2000 general elections: the final report of each race, with each list's votes in and out of the municipality,
  and the candidate vote reports, which name the elected candidates of each list and so give its seats.

Each is a text PDF, read here with poppler's pdftotext. The 1996 and 1998 files spell Bosnian letters in the 7-bit
YUSCII code page ("[" for Š, "~" for č, and so on), which is decoded before parsing.
"""

import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote, unquote, urljoin
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ba.cik_1996_2000"
SHAPES = "ba.geoboundaries"
BIH = str(uuid5(package.NAMESPACE, "polity\x1fBA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBIH"))
VOTES, BALLOTS, VALID, INVALID = 1, 3, 4, 5
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", SHAPES)
SITE = "https://www.izbori.ba/"
ARCHIVE = f"{SITE}Documents/Documents/Rezultati%20izbora%2096-2002/"
CATEGORIES = (221, 222, 223, 224, 225)  # the archive's pages for 1996, 1997, 1998, 2000 general, 2000 municipal
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/f549eab/releaseData/gbOpen/BIH/"
OUTLINES = {level: f"{GEOBOUNDARIES}{level}/geoBoundaries-BIH-{level}_simplified.geojson" for level in ("ADM1", "ADM2")}
OUTLINES["ADM3"] = f"{GEOBOUNDARIES}ADM3/geoBoundaries-BIH-ADM3_simplified.geojson"

ENTITY, CANTON, MUNICIPALITY, UNIT = "ba.pec_entity", "ba.pec_canton", "ba.pec_municipality", "ba.pec_count"


def category_url(category: int) -> str:
    return f"{SITE}Default.aspx?CategoryID={category}&Lang=3"


def links(category: int) -> list[str]:
    """The PDFs a category page of the archive links to, as absolute, percent-encoded URLs."""
    text = raw.cached(SOURCE, category_url(category)).path.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    for href in re.findall(r"""href=['"]([^'"]+\.pdf)['"]""", text, flags=re.IGNORECASE):
        url = urljoin(SITE, quote(unquote(href), safe="/:"))
        if url not in out:
            out.append(url)
    return out


def fetch() -> None:
    for category in CATEGORIES:
        raw.fetch(SOURCE, category_url(category), suffix=".html")
        urls = links(category)
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".pdf")
        print(f"{SOURCE}: category {category}: {len(urls)} files", flush=True)
    for url in UNLINKED:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in OUTLINES.values():
        raw.fetch(SHAPES, url, suffix=".geojson")


def doc(path: str) -> str:
    return ARCHIVE + quote(path, safe="/")


def pdf_text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


YUSCII = str.maketrans({"[": "Š", "{": "š", "]": "Ć", "}": "ć", "^": "Č", "~": "č", "@": "Ž", "`": "ž", "\\": "Đ"})
YUSCII[ord("|")] = "đ"
YUSCII[ord("‘")] = "ž"


def yuscii(text: str) -> str:
    return text.translate(YUSCII)


def spaced(text: str) -> str:
    return " ".join(text.split())


# The parsed results.


@dataclass
class Option:
    label: str  # the list's name as printed, or for a candidate the party printed with the candidate
    votes: dict[str, int]  # vote source ("all", "in", "out") to votes
    seats: int | None = None
    candidate: str | None = None  # a candidate's name, as printed
    elected: bool = False


@dataclass
class Contest:
    key: str  # unique within the election
    race: str  # presidency, bih_hor, fbih_hor, rs_na, rs_president, canton, council
    place: tuple[str, str]  # (scheme, code) of the district
    name: str  # the district's name as printed
    options: list[Option]
    url: str  # the file the votes come from
    accounting: dict[str, dict[int, int]] = field(default_factory=dict)  # vote source to measure to count
    magnitude: int | None = None
    member: str | None = None  # the Presidency member's people (Bosniak, Croat, Serb)
    seats_url: str | None = None  # the file the seats come from, when another

    def total(self, option: Option) -> int:
        return sum(option.votes.values())


@dataclass(frozen=True)
class Election:
    slug: str
    day: date
    kind: str
    title: str
    summary: str


ELECTIONS = (
    Election(
        "1996-09-14",
        date(1996, 9, 14),
        "general",
        "1996 Bosnian general election",
        "the Presidency, the state and Federation Houses of Representatives, and the cantonal assemblies, "
        "from the certified results in the Federation's official gazette",
    ),
    Election(
        "1997-09-14",
        date(1997, 9, 14),
        "local",
        "1997 Bosnian municipal elections",
        "every municipal council, with the votes cast in and out of each municipality",
    ),
    Election(
        "1998-09-13",
        date(1998, 9, 13),
        "general",
        "1998 Bosnian general election",
        "every race's vote count summary and seat allocation, and the eleven municipal councils elected that year",
    ),
    Election(
        "2000-04-08",
        date(2000, 4, 8),
        "local",
        "2000 Bosnian municipal elections",
        "every municipal council, with the votes cast in and out of each municipality and the seats won",
    ),
    Election(
        "2000-11-11",
        date(2000, 11, 11),
        "general",
        "2000 Bosnian general election",
        "the state and entity Houses of Representatives, the cantonal assemblies, and Srebrenica's municipal council, "
        "with the votes cast in and out of each municipality and the seats won",
    ),
)

ENTITIES = {"1": "Federation of Bosnia and Herzegovina", "2": "Republika Srpska"}
ENTITY_BS = {"1": "Federacija Bosne i Hercegovine", "2": "Republika Srpska"}
CANTONS = {
    "1": ("Una-Sana Canton", "Unsko-sanski kanton", "Una-Sana Canton"),
    "2": ("Posavina Canton", "Posavski kanton", "Posavina Canton"),
    "3": ("Tuzla Canton", "Tuzlanski kanton", "Tuzla Canton"),
    "4": ("Zenica-Doboj Canton", "Zeničko-dobojski kanton", "Zenica-Doboj Canton"),
    "5": ("Bosnian-Podrinje Canton Goražde", "Bosansko-podrinjski kanton Goražde", "Bosnian-Podrinje Canton Goražde"),
    "6": ("Central Bosnia Canton", "Srednjobosanski kanton", "Central Bosnia Canton"),
    "7": ("Herzegovina-Neretva Canton", "Hercegovačko-neretvanski kanton", "Herzegovina-Neretva Canton"),
    "8": ("West Herzegovina Canton", "Zapadnohercegovački kanton", "West Herzegovina Canton"),
    "9": ("Sarajevo Canton", "Kanton Sarajevo", "Sarajevo Canton"),
    "10": ("Canton 10", "Kanton 10", "Canton 10"),
}
RACES = {
    "presidency": ("Presidency of Bosnia and Herzegovina", "Predsjedništvo Bosne i Hercegovine"),
    "bih_hor": (
        "House of Representatives of the Parliamentary Assembly of Bosnia and Herzegovina",
        "Predstavnički dom Parlamentarne skupštine Bosne i Hercegovine",
    ),
    "fbih_hor": (
        "House of Representatives of the Parliament of the Federation of Bosnia and Herzegovina",
        "Predstavnički dom Parlamenta Federacije Bosne i Hercegovine",
    ),
    "rs_na": ("National Assembly of Republika Srpska", "Narodna skupština Republike Srpske"),
    "rs_president": ("President of Republika Srpska", "Predsjednik Republike Srpske"),
    "canton": ("Cantonal assembly", "Skupština kantona"),
    "council": ("Municipal council", "Općinsko vijeće"),
    "city_council": ("Mostar City Council", "Gradsko vijeće Grada Mostara"),
}
MOSTAR = "157"  # the City of Mostar, whose council the city-wide list and the six city areas elect
MOSTAR_AREAS = {str(250 + n): str(150 + n) for n in range(1, 7)}  # a city area's council race to its municipality
MEMBERS = {"bosniak": "Bosniak", "croat": "Croat", "serb": "Serb"}
MEMBER_WORDS = {"bosniak": "Bošnjački član", "croat": "Hrvatski član", "serb": "Srpski član"}


def current_word(member: str) -> str:
    return MEMBER_WORDS[member]


MEMBERS_BS = {"bosniak": "bošnjački član", "croat": "hrvatski član", "serb": "srpski član"}


# 1996: the official gazette.

GAZETTE = doc("96results/FD96_20H.PDF")
TOTALS_1996 = {"važećih": VALID, "nevažećih": INVALID}


def number(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


def parse_1996() -> list[Contest]:
    lines = [yuscii(line) for line in pdf_text(GAZETTE).splitlines()]
    out: list[Contest] = []
    current: Contest | None = None
    heading: tuple[str, str, tuple[str, str], str] | None = None
    member: str | None = None
    prefix = ""
    seats: int | None = None
    rows: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        text = spaced(line)
        if not text:
            continue
        if text.startswith("LISTA IZABRANIH"):
            current, heading, member = None, None, None
            continue
        if text.startswith("PREDSJEDNIŠTVO BOSNE I HERCEGOVINE"):
            heading = ("presidency", "presidency", ("", ""), "")
            continue
        for word, code in (("Bošnjački član", "bosniak"), ("Hrvatski član", "croat"), ("Srpski član", "serb")):
            if text == word and heading and heading[0] == "presidency":
                member = code
                entity = "2" if code == "serb" else "1"
                current = Contest(
                    f"presidency:{code}", "presidency", (ENTITY, entity), ENTITIES[entity], [], GAZETTE, member=code
                )
                current.magnitude = 1
                out.append(current)
                prefix = ""
                rows[current.key] = []
        if member and current is not None and current.member == member and text == current_word(member):
            continue
        if text.startswith("S TERITORIJA FEDERACIJE"):
            heading = ("bih_hor", "bih_hor:1", (ENTITY, "1"), ENTITIES["1"])
        elif text.startswith("S TERITORIJE REPUBLIKE SRPSKE"):
            heading = ("bih_hor", "bih_hor:2", (ENTITY, "2"), ENTITIES["2"])
        elif text.startswith("ZASTUPNIČKI DOM FEDERACIJE"):
            heading = ("fbih_hor", "fbih_hor", (ENTITY, "1"), ENTITIES["1"])
        elif m := re.fullmatch(r"Županija (\d+)", text):
            heading = ("canton", f"canton:{m[1]}", (CANTON, m[1]), CANTONS[m[1]][0])
        if m := re.match(r"Broj raspoloživih zastupničkih mjesta: (\d+)", text):
            if heading is None:
                raise ValueError(f"1996: seats without a heading: {text}")
            race, ckey, place, name = heading
            seats = int(m[1])
            current = Contest(ckey, race, place, name, [], GAZETTE, magnitude=seats)
            out.append(current)
            prefix = ""
            continue
        if current is None or text.startswith(("Naziv stranke", "Stranka/koalicija ili")):
            continue
        if m := re.match(r"Ukup\w+ (?:broj )?(\w+) glasova: (\d+)", text):
            which = m[1]
            measure = TOTALS_1996.get(which, BALLOTS if which in {"svih", "glasova"} else None)
            current.accounting.setdefault("all", {})[measure or BALLOTS] = int(m[2])
            if not measure:
                current = None
            continue
        if m := re.match(r"Ukup\w+ broj glasova: (\d+)", text):
            current.accounting.setdefault("all", {})[BALLOTS] = int(m[1])
            current = None
            continue
        if text.startswith(("Broj ", "Nedjelja", "SLUŽBENE")):
            continue
        cells = re.split(r"\s{2,}", line.strip())
        if current.race == "presidency":
            if not re.fullmatch(r"\d+", cells[-1]):
                prefix = spaced(f"{prefix} {text}")
                continue
            rows[current.key].append((prefix, line))
            prefix = ""
        else:
            if len(cells) < 2 or not (re.fullmatch(r"\d+", cells[-1]) and re.fullmatch(r"\d+", cells[-2])):
                prefix = spaced(f"{prefix} {text}")
                continue
            label = spaced(f"{prefix} {' '.join(cells[:-2])}")
            current.options.append(Option(label, {"all": int(cells[-1])}, seats=int(cells[-2])))
            prefix = ""
    for c in out:
        if c.race == "presidency":
            # The party and candidate columns start where most rows put the candidate; a long party name can
            # run up to the candidate with a single space.
            starts = Counter(
                len(line) - len(line.lstrip()) + m.start(1)
                for _, line in rows[c.key]
                if (m := re.match(r"\S.*?\s{2,}(\S)", line.strip()))
            )
            at = min(n for n, _ in starts.most_common(2))
            for prefix, line in rows[c.key]:
                label = spaced(f"{prefix} {line[:at]}")
                person = spaced(line[at:].rsplit(None, 1)[0])
                if not label or not person:
                    raise ValueError(f"1996 presidency row: {line!r}")
                c.options.append(Option(label, {"all": int(line.split()[-1])}, candidate=person))
            best = max(c.options, key=lambda o: o.votes["all"])
            best.elected = True
    return [c for c in out if c.options]


# 1998: the vote count summary and the seat allocation.

RESULTS_1998 = doc("98results/results98.pdf")
SEATS_1998 = doc("98results/seats_allocation_all.pdf")
ELECTED_1998 = doc("98results/electedcandidates98.pdf")
RACE_CODES = {
    "300": ("rs_na", (ENTITY, "2")),
    "400": ("fbih_hor", (ENTITY, "1")),
    "501": ("bih_hor", (ENTITY, "1")),
    "502": ("bih_hor", (ENTITY, "2")),
    "600": ("rs_president", (ENTITY, "2")),
    "701": ("presidency", (ENTITY, "1")),
    "702": ("presidency", (ENTITY, "1")),
    "703": ("presidency", (ENTITY, "2")),
}
PRESIDENCY_CODES = {"701": "bosniak", "702": "croat", "703": "serb"}


def race_of(code: str, name: str) -> tuple[str, tuple[str, str], str]:
    """A race code's race, district, and district name: 2xx are the cantons, 3xx to 7xx the state and entity
    races, and any other code a municipality."""
    if code in RACE_CODES:
        race, place = RACE_CODES[code]
        return race, place, ENTITIES[place[1]]
    if re.fullmatch(r"2(0\d|10)", code):
        canton = str(int(code) - 200)
        return "canton", (CANTON, canton), CANTONS[canton][0]
    if re.fullmatch(r"4(0\d|1[0-2])", code):
        return "fbih_hor_unit", (ENTITY, "1"), name
    return "council", (MUNICIPALITY, code), name


def contest_key(code: str) -> str:
    race, place, _ = race_of(code, "")
    if race == "presidency":
        return f"presidency:{PRESIDENCY_CODES[code]}"
    if race == "bih_hor":
        return f"bih_hor:{place[1]}"
    if race in {"council", "canton"}:
        return f"{race}:{place[1]}"
    return race


def parse_1998() -> list[Contest]:
    lines = [yuscii(line) for line in pdf_text(RESULTS_1998).splitlines()]
    out: list[Contest] = []
    current: Contest | None = None
    reading = False
    for line in lines:
        text = spaced(line)
        if m := re.match(r"Level / Nivo / Razina / Nivo (\d{3}) (.+)", text):
            code, name = m[1], m[2]
            race, place, district = race_of(code, name)
            current = Contest(contest_key(code), race, place, district, [], RESULTS_1998)
            if race == "presidency":
                current.member = PRESIDENCY_CODES[code]
                current.magnitude = 1
            if race == "rs_president":
                current.magnitude = 1
            out.append(current)
            reading = False
            continue
        if current is None or not text:
            continue
        if m := re.match(r"(Valid|Invalid) Ballots .* (\d+)$", text):
            current.accounting.setdefault("all", {})[VALID if m[1] == "Valid" else INVALID] = int(m[2])
            continue
        if m := re.match(r"Total Ballots Cast .* (\d+)$", text):
            current.accounting.setdefault("all", {})[BALLOTS] = int(m[1])
            continue
        if text.endswith(" Ukupno"):
            reading = True
            continue
        if not reading:
            continue
        if m := re.fullmatch(r"Total (\d+)", text):
            if sum(current.total(o) for o in current.options) != int(m[1]):
                raise ValueError(f"1998 {current.key}: the options do not sum to {m[1]}")
            reading = False
            continue
        if re.match(r"\d+/\d+/98", text):
            continue
        if m := re.fullmatch(r"(.+?) (\d+)", text):
            current.options.append(Option(m[1], {"all": int(m[2])}))
        else:  # a long name wraps onto the next line, after its votes
            current.options[-1].label = spaced(f"{current.options[-1].label} {text}")
    return [c for c in out if c.options]


@dataclass
class Allocated:
    code: str  # the party's number on the ballot
    label: str
    seats: int
    members: list[Allocated] = field(default_factory=list)


def seats_1998() -> dict[str, list[Allocated]]:
    """Each race's seats by party; an alliance's seats are listed again under its member parties."""
    out: dict[str, list[Allocated]] = {}
    race: str | None = None
    last: Allocated | None = None
    top: Allocated | None = None
    for line in pdf_text(SEATS_1998).splitlines():
        line = yuscii(line)
        text = spaced(line)
        if m := re.match(r"(\d{3}) {3,}(.+)$", line):
            race = m[1]
            out[race] = []
            last = top = None
            continue
        if race is None or not text or text.startswith(("Electoral Unit", "Seat Allocation")) or "-98" in text:
            continue
        if m := re.match(r"( +)(\d{2}) +(.+?) {2,}(\d+)$", line):
            row = Allocated(m[2], spaced(m[3]), int(m[4]))
            if len(m[1]) >= 20 and top is not None:
                top.members.append(row)
            else:
                out[race].append(row)
                top = row
            last = row
            continue
        if last is not None:
            last.label = spaced(f"{last.label} {text}")
    return out


def elected_1998() -> dict[str, str]:
    """The elected candidate of each single-seat race (the Presidency members and the RS President)."""
    out: dict[str, str] = {}
    race: str | None = None
    for line in pdf_text(ELECTED_1998).splitlines():
        line = yuscii(line)
        if m := re.match(r"(\d{3}) {3,}(.+)$", line):
            race = m[1]
            continue
        single = race in PRESIDENCY_CODES or race == "600"
        if single and race not in out and (m := re.fullmatch(r" {20,}1 +(\S.+)", line)):
            out[race] = spaced(m[1])
    return out


def merge_seats(contests: list[Contest], seats: dict[str, list[Allocated]]) -> None:
    """Puts each party's seats on its option, matching the names, with an alliance's seats on its members."""
    by_key = {c.key: c for c in contests}
    for code, rows in seats.items():
        c = by_key.get(contest_key(code))
        if c is None or c.race in {"presidency", "rs_president"}:
            continue
        c.seats_url = SEATS_1998
        options = {fold(o.label): o for o in c.options}
        for o in c.options:
            o.seats = 0
        for row in rows:
            for part in row.members or [row]:
                found = options.get(fold(part.label)) or next(
                    (o for k, o in options.items() if k.startswith(fold(part.label)) or fold(part.label).startswith(k)),
                    None,
                )
                if found is None:
                    raise ValueError(f"1998 {code}: no votes for {part.label}")
                found.seats = (found.seats or 0) + part.seats
        c.magnitude = sum(o.seats or 0 for o in c.options)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.upper())
    return re.sub(r"[\W_]+", "", "".join(ch for ch in text if not unicodedata.combining(ch)))


# 1997, 2000: votes cast in and out of the municipality.


@dataclass
class Block:
    """One race of a report: its code, name, seats, and each list's votes in and out of the municipality."""

    code: str
    name: str
    magnitude: int | None
    options: list[Option]
    totals: dict[str, int]
    earlier: list[Option] = field(default_factory=list)  # the 1998 column of the 2000 general reports


SOURCES = {"in": re.compile(r"Votes cast in municipality"), "out": re.compile(r"Votes cast out of municipality")}
TOTALS = {
    "in": re.compile(r"^\s*(?:Total Votes Cast in Municipality|Total in municipality)"),
    "out": re.compile(r"^\s*(?:Total Votes Cast out of Municipality|Total out of municipality)"),
    "all": re.compile(
        r"^\s*(?:Total No of Votes by Electoral Race|Total Votes for Electoral Race|Total for the Elect)"
    ),
}
NUMBERS = re.compile(r"(\d+)\s+(\d+(?:\.\d+)?\s?%)")


def columns(line: str, split: int | None) -> tuple[int | None, int | None]:
    """The count in the line, or with two columns (1998 and 2000) each column's count, told apart by position."""
    found = [(m.start(), int(m[1])) for m in NUMBERS.finditer(line)]
    if split is None:
        return (found[-1][1] if found else None), None
    earlier = next((n for at, n in found if at < split), None)
    later = next((n for at, n in found if at >= split), None)
    return later, earlier


def read_blocks(text: str, *, header: re.Pattern[str]) -> list[Block]:
    """Reads the vote source reports: a race heading, then each list's name and its votes in, out, and in total."""
    blocks: list[Block] = []
    block: Block | None = None
    name_lines: list[str] = []
    option: Option | None = None
    earlier: Option | None = None
    split: int | None = None
    prior = ""
    heading = False
    for line in text.splitlines():
        text_line = spaced(line)
        if not text_line:
            continue
        if m := header.match(line):
            code, name = m["code"], spaced(m["name"])
            if block is None or block.code != code:
                block = Block(code, name, None, [], {})
                blocks.append(block)
            name_lines, option, earlier = [], None, None
            heading = True
            continue
        if m := re.search(r"Number of Seats / Broj mandata:\s+(\d+)", line):
            prior = m[1]
            continue
        if block is None:
            continue
        if prior and block.magnitude is None:
            block.magnitude = int(prior)
        if (m := re.search(r"\b(199[78])\b.*\b2000\b", line)) and "Number" not in line:
            split = (line.index(m[1]) + line.index("2000")) // 2
            continue
        if re.match(r"^\s*(Party/Coalition|Political Party|Pol\. Party)", line):
            heading = False
            continue
        if heading:
            continue
        matched = False
        for tag, pattern in TOTALS.items():
            if pattern.search(line):
                later, before = columns(line, split)
                if later is not None:
                    block.totals[tag] = later
                if before is not None:
                    block.totals[f"1998:{tag}"] = before
                matched = True
        if matched:
            name_lines = []
            continue
        if (
            text_line == "%"
            or re.match(r"^\s*(Pol\. |Politi|Political|Party/|Pol\. Party|\* |Page |Izvor|Number)", line)
            or re.search(
                r"(Page \d+ of \d+|\d{1,2}:\d{2}(:\d{2})?( [AP]M)?\s*$|Učestvuje|imenon|same name|Ukupan broj|Ukupno )",
                line,
            )
        ):
            continue
        for tag, pattern in SOURCES.items():
            if pattern.search(line):
                later, before = columns(line, split)
                if name_lines:
                    seat = pending_seat(name_lines)
                    label = spaced(" ".join(re.sub(r" {5,}(\d+|Yes|No)\s*$", "", x) for x in name_lines))
                    option = Option(label, {}, seats=int(seat) if seat else None)
                    earlier = Option(option.label, {})
                    block.options.append(option)
                    block.earlier.append(earlier)
                    name_lines = []
                if option is None or earlier is None:
                    raise ValueError(f"votes before any list: {line!r}")
                if later is not None:
                    option.votes[tag] = later
                if before is not None:
                    earlier.votes[tag] = before
                matched = True
        if matched:
            continue
        if re.match(r"^\s*Total:?\s", line):
            later, before = columns(line, split)
            if option is not None and later is not None and sum(option.votes.values()) != later:
                raise ValueError(f"{block.code} {option.label}: in and out do not sum to {later}")
            if earlier is not None and before is not None and sum(earlier.votes.values()) != before:
                raise ValueError(f"{block.code} {earlier.label} (1998): in and out do not sum to {before}")
            option = earlier = None
            continue
        if option is not None and earlier is not None and not name_lines:
            option = earlier = None
        name_lines.append(line)
    for b in blocks:
        b.earlier = [o for o in b.earlier if o.votes]
        b.options = [o for o in b.options if o.votes or o.seats]
    return blocks


def pending_seat(lines: list[str]) -> str | None:
    """The seats printed at the right of a list's name (2000 municipal reports) or "Yes"/"No" (1997)."""
    m = re.search(r" {5,}(\d+)\s*$", lines[0])
    return m[1] if m else None


HEADER_1997 = re.compile(r"^(?P<code>\d{3}) (?P<name>\S.*?)\s*$")
HEADER_2000 = re.compile(r"^(?P<code>\d{3}) (?P<name>\S.*?)\s*$")
HEADER_GEN = re.compile(r"^(?P<code>\d{3}) {3,}(?P<name>\S.*?)\s*$")


def files(category: int, pattern: str) -> list[str]:
    return [url for url in links(category) if re.search(pattern, unquote(url))]


def parse_1997() -> list[Contest]:
    out: list[Contest] = []
    for url in files(222, r"/\d{3} 1997 ME Results"):
        text = pdf_text(url)
        # The heading's name can wrap: "254 OPĆINA MOSTAR SJEVER ZA GRADSKO VIJEĆE GRADA" / "MOSTARA".
        text = re.sub(r"(?m)^(\d{3} .+GRADA)\n(\S.*)$", r"\1 \2", text)
        for b in read_blocks(text, header=HEADER_1997):
            out.append(council(b, url))
    return out


def council(b: Block, url: str) -> Contest:
    totals = {tag: {VALID: n} for tag, n in b.totals.items() if tag in {"in", "out"}}
    for o in b.options:
        o.votes = {tag: o.votes.get(tag, 0) for tag in ("in", "out")}
    race, code = "council", b.code
    if b.code in MOSTAR_AREAS or b.code == MOSTAR:
        race, code = "city_council", MOSTAR_AREAS.get(b.code, MOSTAR)
    c = Contest(f"council:{b.code}", race, (MUNICIPALITY, code), place_name(b.name), b.options, url, totals)
    c.magnitude = b.magnitude
    check_totals(c, b)
    return c


def place_name(text: str) -> str:
    """A municipality's name as the reports print it, without "Općina" and the city council's suffix."""
    text = re.sub(r"(?i)^OPĆINA\s+", "", spaced(text))
    return re.sub(r"(?i)\s+ZA GRADSKO VIJEĆE.*$", "", text)


def check_totals(c: Contest, b: Block) -> None:
    for tag in ("in", "out"):
        printed = b.totals.get(tag)
        summed = sum(o.votes.get(tag, 0) for o in c.options)
        if printed is not None and printed != summed:
            raise ValueError(f"{c.key}: the lists' {tag} votes sum to {summed}, the report prints {printed}")
    if "all" in b.totals and b.totals["all"] != sum(c.total(o) for o in c.options):
        raise ValueError(f"{c.key}: the lists' votes do not sum to the race's {b.totals['all']}")


def parse_2000_municipal() -> list[Contest]:
    out: list[Contest] = []
    for url in files(225, r"/FinalPartyVotes\d{3}"):
        text = pdf_text(url)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Electoral race / Izborna utrka") and i + 1 < len(lines):
                seats = re.search(r"(\d+)\s*$", line)
                lines[i] = f"Number of Seats / Broj mandata: {seats[1]}" if seats else ""
        for b in read_blocks("\n".join(lines), header=HEADER_2000):
            out.append(council(b, url))
    return out


GENERAL_2000 = {
    "300": "300_nars.pdf",
    "400": "400_horfbih.pdf",
    "501": "501_horbih_fed.pdf",
    "502": "502_horbih_rs.pdf",
    **{str(200 + n): f"{200 + n}_canton{n}.pdf" for n in range(1, 11) if n != 7},  # Canton 7's file is empty
    "105": "105_muni.pdf",
}
CANDIDATES_2000 = {code: name.replace(".pdf", "_cand.pdf") for code, name in GENERAL_2000.items()}
UNLINKED = [doc("2000gen/300_nars_cand.pdf")]  # on the server, but not linked from the archive's page


def parse_2000_general() -> tuple[list[Contest], list[Contest]]:
    """The 2000 races, and the 1998 races as the 2000 reports print them for comparison."""
    now: list[Contest] = []
    then: list[Contest] = []
    for code, name in GENERAL_2000.items():
        url = doc(f"2000gen/{name}")
        blocks = read_blocks(pdf_text(url), header=HEADER_GEN)
        if len(blocks) != 1 or blocks[0].code != code:
            raise ValueError(f"2000 {name}: expected race {code}, read {[b.code for b in blocks]}")
        b = blocks[0]
        race, place, district = race_of(code, place_name(b.name))
        for o in b.options:
            o.votes = {tag: o.votes.get(tag, 0) for tag in ("in", "out")}
        c = Contest(contest_key(code), race, place, district, b.options, url)
        c.accounting = {tag: {VALID: b.totals[tag]} for tag in ("in", "out") if tag in b.totals}
        check_totals(c, b)
        now.append(c)
        if b.earlier:
            for o in b.earlier:
                o.votes = {tag: o.votes.get(tag, 0) for tag in ("in", "out")}
            then.append(
                Contest(
                    contest_key(code),
                    race,
                    place,
                    district,
                    b.earlier,
                    url,
                    {tag: {VALID: b.totals[f"1998:{tag}"]} for tag in ("in", "out") if f"1998:{tag}" in b.totals},
                )
            )
    return now, then


def seats_2000(code: str) -> tuple[str, dict[str, int]]:
    """A race's elected candidates counted by list, from its candidate vote report (regular and compensation
    seats alike)."""
    url = doc(f"2000gen/{CANDIDATES_2000[code]}")
    seats: Counter[str] = Counter()
    party: list[str] = []
    reading = False
    elected = False
    for line in pdf_text(url).splitlines():
        text = spaced(line)
        if m := re.match(r"Party List:\s+(.+)", text):
            party = [m[1]]
            reading, elected = True, False
            continue
        wraps = reading and not elected and text and not text.startswith(("Total No.", "Name "))
        if wraps and re.match(r"^ {20,}\S", line) and not re.search(r"\d+$", text):
            party.append(text)  # a long list name wraps onto the next line
            continue
        if text.startswith("Total No."):
            reading = False
            continue
        if text == "ELECTED":
            elected = True
            continue
        if text == "NOT ELECTED" or text.startswith(("General Elections", "Candidate Votes", "Electoral Race")):
            elected = False
            continue
        if elected and party and re.search(r"[^\W\d_].* \d+$", text) and "Page" not in text:
            seats[spaced(" ".join(party))] += 1
    return url, dict(seats)


# The contests of each election.


def contests(e: Election) -> list[Contest]:
    if e.slug == "1996-09-14":
        return parse_1996()
    if e.slug == "1997-09-14":
        return parse_1997()
    if e.slug == "1998-09-13":
        out = parse_1998()
        merge_seats(out, seats_1998())
        winners = elected_1998()
        for c in out:
            if c.race in {"presidency", "rs_president"}:
                code = next(k for k, v in PRESIDENCY_CODES.items() if v == c.member) if c.member else "600"
                winner = winners.get(code)
                best = max(c.options, key=c.total)
                best.elected = True
                if winner:
                    best.candidate = winner
        return out
    if e.slug == "2000-04-08":
        return parse_2000_municipal()
    out, _ = parse_2000_general()
    for c in out:
        code = next(k for k in GENERAL_2000 if contest_key(k) == c.key)
        url, seats = seats_2000(code)
        c.seats_url = url
        options = {fold(o.label): o for o in c.options}
        for o in c.options:
            o.seats = 0
        for label, n in seats.items():
            found = options.get(fold(label)) or next(
                (o for k, o in options.items() if k.startswith(fold(label)) or fold(label).startswith(k)), None
            )
            if found is None:
                raise ValueError(f"2000 {code}: elected candidates of {label}, which has no votes")
            found.seats = (found.seats or 0) + n
        c.magnitude = sum(o.seats or 0 for o in c.options)
    return out


_CACHE: dict[str, list[Contest]] = {}


def everything(e: Election) -> list[Contest]:
    if e.slug not in _CACHE:
        _CACHE[e.slug] = contests(e)
    return _CACHE[e.slug]


# Places.

# The geoBoundaries outline of each municipality whose name differs, by the Commission's code; the municipalities the
# entity line split are named by their Republika Srpska part after a slash, matched by that name otherwise.
OUTLINE_NAMES = {
    "006": "Kupra na Uni",
    "007": "Novi Grad@west",
    "016": "Brod",
    "023": "Donji Žabar",
    "028": "Brcko District",
    "033": "Oštra Luka",
    "036": "Doboj East",
    "058": "Istočni Drvar",
    "084": "Bosansko Grahovo",
    "107": "Kupres (BiH)",
    "108": "Kupres",
    "110": "Gornji Vakuf-Uskoplje",
    "132": "Istočna Ilidža",
    "133": "Novi Grad@east",
    "136": "Centar",
    "137": "Stari Grad",
    "138": "Istočni Stari Grad",
    "140": "Istočno Novo Sarajevo",
    "141": "Trnovo (BiH)",
    "143": "Pale-Prača",
    "144": "Pale",
    "157": "Mostar",
    "158": "Istočni Mostar",
    "165": "Foča-Ustikolina",
    "166": "Foča",
    "168": "Ustiprača",
}
NO_OUTLINE = {"147": "Višegrad", **{str(150 + n): "a Mostar city municipality" for n in range(1, 7)}}
CANTON_OUTLINES = {
    "1": "Una-Sana Canton",
    "2": "Posavina Canton",
    "3": "Tuzla Canton",
    "4": "Zenica-Doboj Canton",
    "5": "Bosnian-Podrinje Canton Goražde",
    "6": "Central Bosnia Canton",
    "7": "Herzegovina-Neretva Canton",
    "8": "West Herzegovina Canton",
    "9": "Sarajevo Canton",
    "10": "Canton 10",
}
ENTITY_OUTLINES = {"1": "Entity Federation of Bosnia and Herzegovina", "2": "Republika Srpska"}
PLAN_FROM = date(1996, 9, 14)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def fold_latin(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.upper().replace("Đ", "DJ"))
    return re.sub(r"[^A-Z]", "", "".join(ch for ch in text if not unicodedata.combining(ch)))


def proper(text: str) -> str:
    """A municipality's name in title case, from the reports' capitals ("PRNjAVOR" and "BOSANSKI NOVI")."""
    words = []
    for word in spaced(text).upper().split(" "):
        if words and word in {"NA", "I", "U"}:
            words.append(word.lower())
        elif word in {"/", "-"} or re.fullmatch(r"\(.*\)", word):
            words.append(word.replace("FBIH", "FBiH"))
        else:
            words.append("-".join(part[:1] + part[1:].lower() for part in word.split("-")))
    return " ".join(words)


def municipalities() -> dict[str, str]:
    """Each municipality's name by its code, as the 1997 and 2000 municipal reports print it."""
    names: dict[str, str] = {}
    for e in ELECTIONS:
        for c in everything(e):
            if c.place[0] == MUNICIPALITY and c.race == "council":
                names.setdefault(c.place[1], proper(c.name))
    names[MOSTAR] = "Grad Mostar"
    return dict(sorted(names.items()))


def outlines(level: str) -> list[tuple[str, shapely.Geometry]]:
    text = raw.cached(SHAPES, OUTLINES[level]).path.read_text(encoding="utf-8")
    return [(f["properties"]["shapeName"], shapely.geometry.shape(f["geometry"])) for f in json.loads(text)["features"]]


def multipolygon(shape: shapely.Geometry) -> shapely.Geometry:
    shape = shapely.make_valid(shape)
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def municipal_outlines() -> dict[str, shapely.Geometry]:
    shapes = outlines("ADM3")
    by_name: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for name, shape in shapes:
        by_name[fold_latin(name)].append(shape)
    out: dict[str, shapely.Geometry] = {}
    for code, name in municipalities().items():
        if code in NO_OUTLINE:
            continue
        wanted = OUTLINE_NAMES.get(code) or name.split(" / ")[-1]
        wanted, _, side = wanted.partition("@")
        found = by_name.get(fold_latin(wanted), [])
        if side:
            found = sorted(found, key=lambda g: shapely.centroid(g).x)
            found = found[:1] if side == "west" else found[-1:]
        if len(found) != 1:
            raise ValueError(f"municipality {code} {name}: {len(found)} outlines named {wanted}")
        out[code] = found[0]
    return out


def source_rows(p: Package, *, shapes: bool = False) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Centralna izborna komisija Bosne i Hercegovine",
        title="Results of the 1996 to 2000 elections of the OSCE Provisional Election Commission (archive)",
        url=category_url(221),
        license="Official election results of Bosnia and Herzegovina, published for public use",
        license_url=SITE,
        attribution="Central Election Commission of Bosnia and Herzegovina, izbori.ba",
    )
    if shapes:
        p.add(
            "source",
            id=SHAPES_ID,
            kind="academic",
            publisher="geoBoundaries",
            title="geoBoundaries gbOpen Bosnia and Herzegovina ADM1, ADM2, and ADM3 (simplified, 2013), from Wikimedia "
            "Commons",
            url="https://www.geoboundaries.org/",
            license="Public domain",
            license_url="https://www.geoboundaries.org/",
            attribution="geoBoundaries (William & Mary geoLab); original data from Wikimedia Commons",
        )


def geometry(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = multipolygon(shape)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=PLAN_FROM,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=SHAPES_ID,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bosnia and Herzegovina's entities, cantons, and municipalities as the Provisional Election Commission's 1996 "
        "to 2000 results name them, with outlines from geoBoundaries, and the scheme of their counting units",
    )
    p.external |= {BIH, COUNTRY}
    source_rows(p, shapes=True)
    for url in OUTLINES.values():
        p.artifact(raw.cached(SHAPES, url), SHAPES_ID)
    for code, rank, kind, noun in (
        (ENTITY, 1, "administrative", "entity"),
        (CANTON, 2, "administrative", "canton"),
        (MUNICIPALITY, 3, "administrative", "municipality"),
        (UNIT, 4, "synthetic", "counting unit"),
    ):
        p.add("scheme", code=code, polity_id=BIH, kind=kind, rank=rank, noun=noun, code_system=None)
    for scheme in (ENTITY, CANTON, MUNICIPALITY):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "1996"),
            scheme_code=scheme,
            vintage="1996",
            valid_from=PLAN_FROM,
            as_of=date(2000, 11, 11),
        )
    entity_shapes = dict(outlines("ADM1"))
    for code, text in ENTITIES.items():
        id = area(ENTITY, code)
        p.add("area", id=id, scheme_code=ENTITY)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "en", "official")
        p.name(id, ENTITY_BS[code], "bs", "native", False)
        geometry(p, id, entity_shapes[ENTITY_OUTLINES[code]])
    canton_shapes = dict(outlines("ADM2"))
    for code, (en, bs, _) in CANTONS.items():
        id = area(CANTON, code)
        p.add("area", id=id, scheme_code=CANTON)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area(ENTITY, "1"))
        p.name(id, en, "en", "official")
        p.name(id, bs, "bs", "native", False)
        geometry(p, id, canton_shapes[CANTON_OUTLINES[code]])
    shapes = municipal_outlines()
    for code, text in municipalities().items():
        id = area(MUNICIPALITY, code)
        p.add("area", id=id, scheme_code=MUNICIPALITY)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent(code, shapes.get(code), canton_shapes, entity_shapes))
        p.name(id, "City of Mostar" if code == MOSTAR else text, "en" if code == MOSTAR else "bs", "official")
        if code == MOSTAR:
            p.name(id, text, "bs", "native", False)
        if code in shapes:
            geometry(p, id, shapes[code])
    return p


def parent(
    code: str,
    shape: shapely.Geometry | None,
    cantons: dict[str, shapely.Geometry],
    entities: dict[str, shapely.Geometry],
) -> str:
    """A municipality's canton or entity, from where its outline lies; the Mostar city municipalities lie in the
    City of Mostar, Višegrad in Republika Srpska, and Brčko, split by the entity line until 2000, in the country."""
    if code in MOSTAR_AREAS.values():
        return area(MUNICIPALITY, MOSTAR)
    if code == "147":
        return area(ENTITY, "2")
    if code == "028" or shape is None:
        return COUNTRY
    point = shapely.point_on_surface(shape)
    for canton, name in CANTON_OUTLINES.items():
        if shapely.contains(cantons[name], point):
            return area(CANTON, canton)
    for entity, name in ENTITY_OUTLINES.items():
        if shapely.contains(entities[name], point):
            return area(ENTITY, entity)
    return COUNTRY


# Bodies, offices, and parties.


def body(c: Contest) -> str | None:
    if c.race in {"bih_hor", "fbih_hor", "rs_na"}:
        return key("body", c.race)
    if c.race in {"canton", "council", "city_council"}:
        return key("body", c.race, c.place[1] if c.race != "city_council" else MOSTAR)
    return None


def office(c: Contest) -> str | None:
    return key("office", c.race) if c.race in {"presidency", "rs_president"} else None


def party(label: str) -> str:
    return key("party", fold(label))


def all_contests() -> list[tuple[Election, Contest]]:
    return [(e, c) for e in ELECTIONS for c in everything(e)]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bosnia and Herzegovina's legislatures, assemblies, councils, and elected offices from 1996 to 2000, and the "
        "parties and lists on the Provisional Election Commission's ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BIH}
    names = municipalities()
    bodies: dict[str, tuple[str, str, str, str]] = {}
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for _, c in all_contests():
        id = body(c)
        if id and id not in bodies:
            en, bs = RACES[c.race]
            if c.race == "canton":
                en, bs = f"Assembly of {CANTONS[c.place[1]][0]}", f"Skupština {CANTONS[c.place[1]][1]}"
            elif c.race == "council":
                en, bs = f"Municipal council of {names[c.place[1]]}", f"Općinsko vijeće {names[c.place[1]]}"
            kind = {"bih_hor": "lower_house", "fbih_hor": "lower_house"}.get(c.race, "unicameral")
            bodies[id] = (en, bs, "council" if c.race in {"council", "city_council"} else kind, c.race)
        for o in c.options:
            spellings[fold(o.label)][o.label] += 1
    for id, (en, bs, kind, _) in sorted(bodies.items()):
        p.add("body", id=id, polity_id=BIH, kind=kind)
        p.name(id, en, "en", "official")
        p.name(id, bs, "bs", "native", False)
    p.add("office", id=key("office", "presidency"), polity_id=BIH, kind="head_of_state")
    p.name(key("office", "presidency"), "Member of the Presidency of Bosnia and Herzegovina", "en", "official")
    p.name(key("office", "presidency"), "Član Predsjedništva Bosne i Hercegovine", "bs", "native", False)
    p.add("office", id=key("office", "rs_president"), polity_id=BIH, kind="other")
    p.name(key("office", "rs_president"), RACES["rs_president"][0], "en", "official")
    p.name(key("office", "rs_president"), RACES["rs_president"][1], "bs", "native", False)
    for folded, texts in sorted(spellings.items()):
        id = key("party", folded)
        p.add("party", id=id, kind="party", polity_id=BIH)
        best = texts.most_common(1)[0][0]
        for text in sorted(texts):
            p.name(id, text, "bs", "official", text == best)
    return p


# Elections.

TAGS = {"all": "all votes", "in": "votes cast in the municipality", "out": "votes cast out of the municipality"}
METHODS = {
    "1996-09-14": (
        "The certified results as the Provisional Election Commission published them in the Federation's official "
        "gazette (Službene novine Federacije BiH 20/1996): each list's or candidate's votes and seats for the whole "
        "district, with its valid, invalid, and total ballots. The gazette prints one invalid count for the "
        "Federation's Presidency ballot, under both its Bosniak and its Croat member."
    ),
    "1997-09-14": (
        "The Provisional Election Commission's final results for the municipality: each list's votes cast in the "
        "municipality, and its votes cast out of it (by displaced voters and refugees, counted in the Counting "
        "Centre), each as one counting unit. The reports print no invalid ballots and no seats."
    ),
    "1998-09-13": (
        "The Provisional Election Commission's vote count summary for the whole race, with its valid, invalid, and "
        "total ballots, and its seat allocation by party. Where parties ran as a technical alliance for seats, the "
        "allocation lists each member party's seats, which are stored on that party's list."
    ),
    "2000-04-08": (
        "The Provisional Election Commission's final results for the municipality: each list's votes cast in the "
        "municipality, and its votes cast out of it (counted in the Counting Centre), each as one counting unit, "
        "with the seats it won. The reports print no invalid ballots."
    ),
    "2000-11-11": (
        "The Provisional Election Commission's final results for the whole race: each list's votes cast in the "
        "municipalities and out of them (counted in the Counting Centre), each as one counting unit. Its seats are "
        "its elected candidates, regular and compensatory, counted from the race's candidate vote report; the "
        "multi-member constituencies' own votes are not published. The reports print no invalid ballots."
    ),
}
ACCOUNTING = (VALID, INVALID, BALLOTS)


def package_key(e: Election) -> str:
    return f"{SOURCE}/{e.slug}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(e) for e in ELECTIONS)]


def contest_name(c: Contest) -> tuple[str, str]:
    en, bs = RACES[c.race]
    names = municipalities()
    if c.race == "presidency" and c.member:
        return f"{en}, {MEMBERS[c.member]} member", f"{bs}, {MEMBERS_BS[c.member]}"
    if c.race == "bih_hor":
        return f"{en}, members elected in the {c.name}", f"{bs}, {ENTITY_BS[c.place[1]]}"
    if c.race == "canton":
        return f"Assembly of {CANTONS[c.place[1]][0]}", f"Skupština {CANTONS[c.place[1]][1]}"
    if c.race == "council":
        return f"Municipal council of {names[c.place[1]]}", f"Općinsko vijeće {names[c.place[1]]}"
    if c.race == "city_council" and c.place[1] != MOSTAR:
        return f"{en}, {names[c.place[1]]}", f"{bs}, {names[c.place[1]]}"
    if c.race == "city_council":
        return f"{en}, city-wide list", f"{bs}, lista na nivou grada"
    return en, bs


@dataclass
class Report:
    contests: int = 0
    values: int = 0
    totals: list[str] = field(default_factory=list)


def election(e: Election, report: Report) -> Package:
    p = Package(
        package_key(e),
        SOURCE,
        f"The {e.title}: {e.summary}",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BIH}
    election_id = key("election", e.slug)
    start = {"1997-09-14": date(1997, 9, 13), "1998-09-13": date(1998, 9, 12)}.get(e.slug, e.day)
    p.add("election", id=election_id, polity_id=BIH, kind=e.kind, date_from=start, date_to=e.day, status="held")
    p.name(election_id, e.title, "en", "official")
    path = f"ba/{e.slug}-{changeset.slug(e.kind)}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    rows: dict[str, list[object]] = {k: [] for k in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    files: set[str] = set()
    seen: set[str] = set()
    people: set[str] = set()
    over: list[str] = []
    short: list[str] = []
    for c in everything(e):
        files.add(c.url)
        if c.seats_url:
            files.add(c.seats_url)
        contest = key("contest", e.slug, c.key)
        result_set = key("result_set", e.slug, c.key)
        en, bs = contest_name(c)
        candidates = c.race in {"presidency", "rs_president"}
        place = area(c.place[0], MOSTAR_AREAS.get(c.place[1], c.place[1]))
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office" if candidates else "list_allocation",
            office_id=office(c),
            body_id=body(c),
            district_area_id=place,
            magnitude=c.magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, en, "en", "official")
        p.name(contest, bs, "bs", "native", False)
        tail = changeset.slug(en)
        if tail in seen:
            tail = f"{tail}-{changeset.slug(c.key)}"
        seen.add(tail)
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHODS[e.slug],
            lang="en",
        )
        report.contests += 1
        units = {
            tag: key("unit", e.slug, c.key, tag)
            for tag in ("all", "in", "out")
            if any(tag in o.votes for o in c.options) or tag in c.accounting
        }
        for tag, unit in units.items():
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BIH,
                scheme_code=UNIT,
                code=f"{e.slug}:{c.key}:{tag}",
                area_relation="within",
                area_id=place,
                kind="aggregate",
            )
        options: set[str] = set()
        for position, o in enumerate(c.options, 1):
            label = f"{o.candidate} - {o.label}" if o.candidate else o.label
            option = key("option", e.slug, c.key, label)
            if option in options:
                raise ValueError(f"{e.slug} {c.key}: {label} is listed twice")
            options.add(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate" if candidates else "list",
                party_id=party(o.label),
                ballot_position=position,
                ballot_label=label,
            )
            if candidates and o.candidate:
                person = key("person", c.race, fold(o.candidate))
                if person not in people:
                    people.add(person)
                    p.add("person", id=person)
                    p.name(person, proper(o.candidate), "bs", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", e.slug, c.key, label),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=o.candidate,
                    member_of_party_id=party(o.label),
                    is_independent=None,
                )
            if candidates and o.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
            if not candidates and o.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=o.seats)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.total(o))
            for tag, n in o.votes.items():
                for column, value in zip(rows, (result_set, units[tag], option, VOTES, n), strict=True):
                    rows[column].append(value)
        declared: dict[int, int] = {}
        for tag, counts in c.accounting.items():
            for measure, n in counts.items():
                declared[measure] = declared.get(measure, 0) + n
                for column, value in zip(rows, (result_set, units[tag], None, measure, n), strict=True):
                    rows[column].append(value)
        for measure in ACCOUNTING:
            if measure in declared:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=declared[measure])
        won = sum(o.seats or 0 for o in c.options)
        if not candidates and c.magnitude and won and won != c.magnitude:
            short.append(f"{en}, {c.magnitude} seats printed and {won} allocated")
        summed = sum(c.total(o) for o in c.options)
        if VALID in declared and declared[VALID] != summed:
            over.append(f"{en}: the lists' votes sum to {summed:,}, the valid ballots printed are {declared[VALID]:,}")
        if c.race not in {"council", "city_council"}:
            report.totals.append(
                f"{e.slug} {en}: {summed:,} votes for the options"
                + (f", {declared[VALID]:,} valid ballots printed" if VALID in declared else "")
                + (", match" if declared.get(VALID, summed) == summed else ", differ")
            )
    for url in sorted(files):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    if over:
        why = "The Commission's own figures disagree: " + "; ".join(over)
        p.accept("accounting.votes_valid", why)
    if short:
        p.accept(
            "outcomes.seats_magnitude",
            "The Commission's final report prints the council's size and allocates fewer seats among the lists: "
            + "; ".join(short),
        )
    frame = pl.DataFrame(
        rows,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
    ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))
    report.values += frame.height
    p.frame("result_value", frame)
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        report = Report()
        election(e, report).write()
        for line in report.totals:
            print(f"  {line}", flush=True)
        print(f"{e.slug}: {report.contests} contests, {report.values:,} values", flush=True)


def emit() -> None:
    raise SystemExit(f"{SOURCE} builds packages; load them with `ea package load`")
