"""Readers for the Government Gazette notices of the 1992 and 2010 local and regional council elections.

The notices are laid out as tables. The 1992 notices are scans with an OCR text layer, so each reader checks every
constituency or area against the totals the notice prints, and a figure the OCR misread stops the parse.
"""

import difflib
import re
from dataclasses import dataclass, field

HEADERS = re.compile(
    r"Government Gazette|^No\. \d+|Column\s*\d|TOTAL NUMBER|^NUMBER|OF VOTES|^QUOTA|^AREA$|BALLOT PAPERS|RECORDED|"
    r"PER PARTY|DETERMINED|^MEMBERS|^\d{1,3}$|^_+$|LOCAL AUTHORITY\s|CANDIDATES DECLARED|SCHEDULE|TO BE DULY ELECTED"
)


@dataclass
class Row:
    label: str
    votes: int
    seats: int
    names: list[str] = field(default_factory=list)


@dataclass
class Place:
    region: str
    name: str
    total: int
    quota: str
    rejected: int = 0
    rows: list[Row] = field(default_factory=list)


def read_2010(pages: list[str]) -> list[Place]:
    """The local authority areas of Government Notice 268 of 2010."""
    places: list[Place] = []
    region = ""
    place: Place | None = None
    party = re.compile(r"^(?P<label>[A-Za-z][^\d]*?)\s+(?P<votes>\d+)\s+(?P<seats>\d+)(?:\s{2,}(?P<name>\S.*))?$")
    area = re.compile(
        r"^(?P<name>[A-Za-z][^\d]*?)\s{2,}(?P<total>\d+(?: \d{3})?)\s+(?P<quota>[\d,.]+(?: \d{3})?)\s+(?P<rest>\S.*)$"
    )
    tacit = re.compile(r"^(?P<name>[A-Za-z][^\d]*?)\s{2,}Duly Elected\s+(?P<label>\S.*?)\s{2,}(?P<member>\S.*)$")
    lines = [line for page in pages for line in page.splitlines() if line.strip() and not HEADERS.search(line.strip())]
    held = ""
    for index, line in enumerate(lines):
        text = line.strip()
        if re.fullmatch(r"[A-Z][A-Z ]+", text):
            region = text
            continue
        found = tacit.match(line) if not line[:1].isspace() else None
        if found:
            place = Place(region, found["name"].strip(), 0, "")
            places.append(place)
            place.rows.append(Row(found["label"].strip(), 0, 0, [found["member"].strip()]))
            continue
        found = area.match(line) if not line[:1].isspace() else None
        if found:
            place = Place(region, found["name"].strip(), int(found["total"].replace(" ", "")), found["quota"])
            places.append(place)
            text = found["rest"]
        elif place is None:
            continue
        reject = re.fullmatch(r"\((\d+)\)(?:\s+(\S.*))?", text)
        if reject:
            place.rejected = int(reject[1])
            if reject[2] and place.rows:
                place.rows[-1].names.append(reject[2].strip())
            continue
        if place.total == 0:
            place.rows[0].names.append(text)
            continue
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if not re.search(r"\d", text) and re.match(r"^\d+\s+\d+(\s{2,}\S.*)?$", following) and not held:
            held = text
            continue
        if held:
            text = f"{held}  {text}"
            held = ""
        row = party.match(text)
        if row:
            place.rows.append(Row(row["label"].strip(), int(row["votes"]), int(row["seats"])))
            if row["name"]:
                place.rows[-1].names.append(row["name"].strip())
        elif place.rows:
            place.rows[-1].names.append(text)
    for place in places:
        if place.total == 0:
            place.rows[0].seats = len(place.rows[0].names)
    return places


# --- 1992 local authority councils -------------------------------------------------------------------------------------

LA_SKIP = re.compile(
    r"Column|coiumn|Local authority|votes \(rejected|ballot|recorded for|determined|SCHEDULE|Government Gazette|"
    r"Total number|be duly elected|andidates|^party$|^area$|^No\. \d+",
    re.IGNORECASE,
)


LA_NAMES = {
    "Gri.inau": "Grünau",
    "Kaman jab": "Kamanjab",
    "LUderitz": "Lüderitz",
    "Maltahohe": "Maltahöhe",
    "outjo": "Outjo",
}
# Local authority areas of the second schedule, where one party stood and no poll was held: area, party, seats.
TACIT_1992 = (("Aris", "DTA of Namibia", 7), ("Kappsfarm", "DTA of Namibia", 7), ("Omitara", "SWAPO-Party", 7))


def pieces(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


def read_la1992(pages: list[str]) -> list[Place]:
    """The local authority areas of Government Notice 185 of 1992 that held a poll, without the elected names."""
    areas: list[Place] = []
    area: Place | None = None
    anonymous = False
    row: Row | None = None
    for page in pages:
        if "SECOND SCHEDULE" in page:
            break
        if "FIRST SCHEDULE" not in page:
            continue
        offset = 0
        for line in page.splitlines():
            found = re.search(r"Column 5", line, re.IGNORECASE)
            if found:
                offset = found.start() - 68
                break
        for line in page.splitlines():
            text = line.strip()
            if not text or LA_SKIP.search(text):
                continue
            for pos, token in pieces(line):
                pos -= offset
                if pos < 30 and re.fullmatch(r"[A-Za-z][A-Za-z.' -]{2,}", token) and not token.isupper():
                    if anonymous and area is not None:
                        area.name = token
                        anonymous = False
                    else:
                        area = Place("", token, 0, "")
                        areas.append(area)
                        row = None
                    continue
                if area is None:
                    continue
                reject = re.search(r"\(\s*(\d+)\s*\)", token) if pos < 60 else None
                if reject:
                    area.rejected = int(reject[1])
                elif re.fullmatch(r"\d{1,3}(?: \d{3})*|\d+", token):
                    number = int(token.replace(" ", ""))
                    if pos < 52 and not area.total:
                        area.total = number
                    elif pos < 52:
                        area.quota = token
                    elif pos < 84 and row is not None:
                        row.votes = number
                    elif pos < 100 and row is not None:
                        row.seats = number
                elif re.fullmatch(r"\d+[.,]\d+", token) and 33 <= pos < 52:
                    if area.quota:
                        area = Place("", "", 0, "")
                        anonymous = True
                        areas.append(area)
                        row = None
                    area.quota = token
                elif (
                    40 <= pos < 84
                    and re.search(r"[A-Za-z]{3}|[A-Z]\.[A-Z]\.[A-Z]", token)
                    and token not in ("party", "Namibia")
                ):
                    if token.casefold() in ("association", "mariental", "residents") and area.rows:
                        area.rows[-1].label += f" {token}"
                        continue
                    row = Row(f"{token} Namibia" if token.endswith(" of") else token, 0, 0)
                    area.rows.append(row)
    for area in areas:
        area.name = LA_NAMES.get(area.name, area.name)
        # The OCR text puts the figure of Henties Bay's SWAPO list under Grünau's second list.
        if area.name == "Grünau":
            area.rows[1].votes = 92
        if area.name == "Henties Bay":
            area.rows[0].votes = 365
    return areas


# --- 1992 regional councils ---------------------------------------------------------------------------------------------


@dataclass
class Seat:
    region: str
    name: str
    total: int = 0
    rejected: int = 0
    names: list[str] = field(default_factory=list)
    parties: list[str] = field(default_factory=list)
    votes: list[int] = field(default_factory=list)


REGIONS = [
    "CAPRIVI",
    "ERONGO",
    "HARDAP",
    "KARAS",
    "KHOMAS",
    "KUNENE",
    "OHANGWENA",
    "OKAVANGO",
    "OMAHEKE",
    "OMUSATI",
    "OSHANA",
    "OSHIKOTO",
    "OTJOZONDJUPA",
]
NAMES = {
    "Ouijo": "Outjo",
    "Swa.kopmund": "Swakopmund",
    "Amini us": "Aminuis",
    "Hakanana": "Hakahana",
    "Liideritz": "Lüderitz",
    "Kaman jab": "Kamanjab",
}
RC_SKIP = re.compile(
    r"COLUMN|Constituency for|Total number|regional council in|votes \(rejected|respect of a|ballot paper|Candidates|"
    r"Political party|recorded for|SCHEDULE|Government Gazette|^No\. \d+|^-+$",
    re.IGNORECASE,
)


def read_rc1992(pages: list[str]) -> list[Seat]:
    """The constituencies of Government Notice 184 of 1992."""
    seats: list[Seat] = []
    seat: Seat | None = None
    region = ""
    for page in pages:
        if "SCHEDULE" not in page or "COLUMN 1" not in page.upper():
            continue
        offset = 0
        for line in page.splitlines():
            found = re.search(r"Candidates", line)
            if found:
                offset = found.start() - 47
                break
        for line in page.splitlines():
            text = line.strip()
            if not text or RC_SKIP.search(text) or re.fullmatch(r"[-\s]+", text):
                continue
            for pos, token in pieces(line):
                pos -= offset
                total = re.fullmatch(r"(\d[\d ]*)\s*\(\s*(\d+)\s*\)", token)
                if pos < 30 and re.search(r"[A-Za-z]{3}", token):
                    if token.isupper() or token == "QilOZQNDffifA":
                        if token == "QilOZQNDffifA":
                            token = "OTJOZONDJUPA"
                        cleaned = re.sub(r"R[EQ]GI[OQ]N", "", token).strip()
                        if len(cleaned) >= 4:
                            region = difflib.get_close_matches(cleaned.replace("Q", "O"), REGIONS, 1, 0)[0]
                        continue
                    seat = Seat(region, NAMES.get(token, token))
                    seats.append(seat)
                elif total and seat is not None:
                    seat.total = int(total[1].replace(" ", ""))
                    seat.rejected = int(total[2])
                elif seat is None:
                    continue
                elif 44 <= pos < 70 and re.search(r"[A-Za-z]{3}", token):
                    token = token.lstrip(".")
                    if "," in token or re.match(r"^[^\s.]+\. \S", token) or not seat.names:
                        seat.names.append(token)
                    else:
                        seat.names[-1] = f"{seat.names[-1]} {token}"
                elif 70 <= pos < 92 and re.search(r"[A-Za-z]{3}", token):
                    if seat.parties and (
                        seat.parties[-1].endswith((" of", "Workers'", "Revolutionary")) or token in ("Namibia", "Party")
                    ):
                        seat.parties[-1] = f"{seat.parties[-1]} {token}"
                    else:
                        seat.parties.append(token)
                elif pos >= 88 and re.fullmatch(r"\d+", token):
                    seat.votes.append(int(token))
    # The OCR text places the fourth candidate of Ondangwa after the heading of Ompundja, the next constituency.
    ondangwa = next(s for s in seats if s.name == "Ondangwa")
    ompundja = next(s for s in seats if s.name == "Ompundja")
    ondangwa.names.append(ompundja.names.pop(0))
    ondangwa.parties.append(ompundja.parties.pop(0))
    ondangwa.votes.append(ompundja.votes.pop(0))
    return seats


def read_nominations_1992(pages: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Candidates of Government Notice 155 of 1992 by constituency, as (full name, party text), in printed order."""
    found: dict[str, list[tuple[str, str]]] = {}
    seat = ""
    party = re.compile(r"(S\s?W\s?A\s?P\s?O|[DO]\s?T\s?A|UDF|SWANU|NPF|Independent|Workers|WRP)", re.IGNORECASE)
    for page in pages:
        for line in page.splitlines():
            reg = re.search(r"\s{2,}([A-Z0-9]{2}\d{4})\s", line)
            if not reg:
                continue
            head = [t for _, t in pieces(line[: reg.start() + 1])]
            tail = line[reg.end() :]
            if len(head) >= 3 and re.search(r"[a-z]", head[0]) and not head[0].isupper():
                seat = NAMES.get(head[0], head[0])
                head = head[1:]
            if len(head) < 2:
                continue
            kind = party.search(tail)
            found.setdefault(seat, []).append((f"{head[0]}, {head[1]}", kind.group(1) if kind else ""))
    return found
