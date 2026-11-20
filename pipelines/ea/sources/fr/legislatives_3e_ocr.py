"""Reads the printed results tables of a Third Republic election volume from its OCR text.

The volume prints one block per constituency: a header line with the constituency's name, its registered voters
(`i.`), valid votes (`s. e.`, suffrages exprimés), and spoiled ballots (`n.`), then one line per candidate with the
candidate's name, an incumbent mark (`d. s.`, député sortant), a party label, and the votes of each round. A block
whose columns are printed in two rounds carries a `1er Tour 2e Tour` line. The winner's line ends in `ÉLU`.

OCR garbles digits and wraps lines, so a block is kept only when its candidates' first-round votes add up to the
printed valid votes, after at most one repair of a single number that differs from its reading in one digit, and only when no other number could close the gap.
Every other block is returned in `rejected` with the reason.
"""

import re
from dataclasses import dataclass, field

NUMBER = re.compile(r"^\d{1,3}(?:\.\d{3})+$|^\d+$")
PAGE = re.compile(r"^[—–\-]?\s*\d{1,3}\s*[—–\-^]*$")
DEPARTMENT = re.compile(
    r"^(?P<name>.+?)\s*\(+\s*(?P<seats>[\dlIc]+)\s+députés?\)?\s*$|^(?P<bare>.+?)\s+(?P<seats2>\d{1,2})\s+députés?\)?\s*$"
)
HEADER = re.compile(
    r"^(?P<name>[^:;]{2,70}?)\s*(?:[(\[{]\s*(?P<circ>[^)\]}]{0,12})[)\]}]?)?\s*[:;]\s*(?P<rest>[iIl1!'‘].*;.*)$"
)
ELECTED = re.compile(r"\s*\b[ÉEé]LU\b[.,]?\s*$")
TOUR = re.compile(r"\bTour\b|\bTouR\b|\bTOUR\b", re.IGNORECASE)
TOKEN = re.compile(r"^[\d.]+$|^[vV][.,]?$")
INCUMBENT = re.compile(r"\bd\s*\.\s*s\s*\.?")


@dataclass
class Candidate:
    name: str
    label: str
    incumbent: bool
    first: int | None
    second: int | None
    elected: bool
    raw: str
    digits: str = ""


@dataclass
class Block:
    department: str
    seats: int
    name: str
    circ: str
    registered: int | None
    valid: int | None
    spoiled: int | None
    rounds: int
    candidates: list[Candidate] = field(default_factory=list)
    line: int = 0
    problem: str | None = None
    repair: str | None = None


def number(text: str) -> int | None:
    text = text.strip().strip(".,;:'` ")
    text = text.replace(" ", "")
    return int(text.replace(".", "")) if NUMBER.match(text) else None


def split(text: str) -> tuple[str, list[str], bool]:
    """Separate a candidate line into its text, its numbers, and whether it ends in ÉLU."""
    elected = bool(ELECTED.search(text))
    text = ELECTED.sub("", text).rstrip(" .,")
    parts = text.split(" ")
    numbers: list[str] = []
    while parts and (TOKEN.match(parts[-1]) or not parts[-1].strip(".")):
        token = parts.pop()
        if token[0] in "vV" or not token.strip("."):
            continue
        numbers.append(token)
    numbers.reverse()
    return " ".join(parts).strip(" ,"), numbers, elected


def identity(text: str) -> tuple[str, str, bool]:
    pieces = [piece.strip() for piece in text.split(",") if piece.strip()]
    incumbent = False
    kept: list[str] = []
    for piece in pieces:
        if INCUMBENT.fullmatch(piece):
            incumbent = True
            continue
        if INCUMBENT.search(piece):
            incumbent = True
            piece = INCUMBENT.sub("", piece).strip()
        kept.append(piece)
    if len(kept) <= 1:
        return (kept[0] if kept else "", "", incumbent)
    return kept[0] if len(kept) == 2 else ", ".join(kept[:-1]), kept[-1], incumbent


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def blocks(lines: list[str], start: int, stop: int) -> list[Block]:
    out: list[Block] = []
    department, seats = "", 0
    current: Block | None = None
    pending = ""
    skipping = False
    for index in range(start, stop):
        raw = lines[index]
        text = clean(raw)
        if not text:
            skipping = False
            continue
        if skipping or PAGE.match(text):
            continue
        if text.startswith(("R. P.", "R.P.", "Q.")) or re.match(r"^(La|Le|Les|Mêmes|Même)\b.*(siège|résultat)", text):
            skipping = True
            continue
        found = DEPARTMENT.match(text)
        if found and not text[0].isdigit() and "députés" in text and ":" not in text:
            department = clean(found["name"] or found["bare"]).strip("' ")
            count = (found["seats"] or found["seats2"]).replace("l", "1").replace("I", "1").replace("c", "6")
            seats = int(re.sub(r"\D", "", count) or 0)
            current = None
            continue
        if text.startswith(("ALGÉRIE", "ALGERIE", "COLONIES")) and text.upper() == text:
            current = None
            department = ""
            continue
        header = HEADER.match(text)
        if header and department:
            figures = [re.sub(r"^[^\d]*", "", part).strip() for part in header["rest"].split(";")]
            figures += [""] * (3 - len(figures))
            current = Block(
                department,
                seats,
                clean(header["name"]),
                clean(header["circ"] or ""),
                number(figures[0]),
                number(figures[1]),
                number(figures[2]),
                1,
                line=index + 1,
            )
            out.append(current)
            pending = ""
            continue
        if current is None:
            continue
        if TOUR.search(text) and len(text) < 40 and not re.search(r"\d{3}", text):
            current.rounds = 2
            continue
        if re.match(r"^[NnxX][.,]?\s+[\d.]+$", text) and current.spoiled is None and not current.candidates:
            current.spoiled = number(text.split(None, 1)[1])
            continue
        body, nums, elected = split(text)
        if not nums and not elected:
            pending = f"{pending} {body}".strip()
            continue
        full = f"{pending} {body}".strip() if pending else body
        pending = ""
        name, label, incumbent = identity(full)
        if not name:
            continue
        values = [number(n) for n in nums]
        first = values[0] if values else None
        second = values[1] if len(values) > 1 else None
        if re.match(r"^Blancs?\b", name):
            continue
        digits = re.sub(r"\D", "", nums[0]) if nums else ""
        current.candidates.append(Candidate(name, label, incumbent, first, second, elected, text, digits))
    return out


def diff(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b, strict=True)) if len(a) == len(b) else 99


def settle(block: Block) -> None:
    """Check the block against its own printed totals, repairing one misread number where that closes the gap."""
    people = block.candidates
    if not people:
        block.problem = "no candidates"
        return
    if block.valid is None:
        block.problem = "no valid-vote total"
        return
    if sum(person.elected for person in people) != 1:
        block.problem = "not exactly one candidate marked elected"
        return
    if any(person.first is None for person in people):
        block.problem = "a candidate has no readable number"
        return
    if block.rounds == 2:
        for person in people:
            others = sum(c.first or 0 for c in people if c is not person)
            if person.second is None and person.first is not None and person.elected and others == block.valid:
                person.first, person.second = None, person.first
    if block.rounds == 2 and not any(person.second is not None for person in people):
        block.rounds = 1
    winner = next(person for person in people if person.elected)
    if block.rounds == 2:
        if winner.second is None:
            block.problem = "winner has no second-round votes"
            return
        if winner.second < max(person.second or 0 for person in people):
            block.problem = "winner does not lead the second round"
            return
    total = sum(person.first or 0 for person in people)
    if total == block.valid:
        return
    gap = block.valid - total
    fits = [
        (person, person.first + gap)
        for person in people
        if person.first is not None and person.first + gap > 0 and diff(person.digits, f"{person.first + gap}") <= 1
    ]
    if len(fits) == 1:
        person, wanted = fits[0]
        block.repair = f"{person.name}: {person.digits} read for {wanted}"
        person.first = wanted
        return
    block.problem = f"first-round votes add up to {total}, printed valid votes {block.valid}"
