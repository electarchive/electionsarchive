"""The State Electoral Commission's county reports naming the council members and executives elected in local elections.

One PDF per county and year prints, for the county assembly, each town and municipal council, and each mayoral or
župan contest, the printed totals and candidates' votes, then the lists with the seats each won and the members elected
from them, or the candidate declared elected. The text is read with pdftotext and keeps the authority's spelling.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

NOISE = (
    "Informacija o izborima",
    "Informacija o rezultatima izbora",
    "općinskih načelnika, gradonačelnika i župana",
    "i općinskih načelnika, gradonačelnika i župana",
)
COUNTS = re.compile(r"^\s*(\d+)\.?\s+(.*?)\s{2,}([\d.]+)\s+glas\w*(?:\s+[\d,]+\s*%)?\s*$")
ENTRY = re.compile(r"^\s*(\d+)\.\s+(\S.*)$")
SEATS = re.compile(r"^\s*(\d+)\s+mjest\w+\s*$")
ELECTED = re.compile(r"^\s*za\s+(.+?)\s+izabran(?:a)?\s+je\s*$")
STATS = re.compile(r"^(Broj birača na obrađenim|Ukupno birača)")
ROUND = re.compile(r"\((prvi|drugi|tre[ćc]i) krug", re.IGNORECASE)
ROUNDS = {"prvi": 1, "drugi": 2, "treći": 3, "treci": 3}


@dataclass
class Entry:
    rank: int
    text: str  # the lines printed above the votes: the party names, or a candidate
    votes: int
    head: str = ""  # a list's "Nositelj liste", or an executive candidate's name


@dataclass
class Member:
    position: int
    name: str


@dataclass
class Block:
    rank: int
    names: str
    seats: int
    head: str = ""  # the "Nositelj liste" of a list that won additional seats
    members: list[Member] = field(default_factory=list)


@dataclass
class Section:
    kind: str  # "council" or "executive"
    round: int
    title: str
    registered: int
    valid: int
    entries: list[Entry] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    extra: list[Block] = field(default_factory=list)  # lists given additional members for a national minority
    elected: list[str] = field(default_factory=list)  # the executive declared elected, then the deputies
    elected_text: str = ""
    advanced: list[str] = field(default_factory=list)  # the two candidates the first round sent to a runoff
    start: int = 0


def count(text: str) -> int:
    return int(text.replace(".", ""))


def text_of(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def clean_lines(body: str) -> list[str]:
    """The lines without page headers and footers."""
    out = []
    for line in body.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in NOISE):
            continue
        if re.match(r"^(\d+\s{3,}[A-ZČĆŠŽĐ\- ]+|[A-ZČĆŠŽĐ\- ]+\s{3,}\d+)$", stripped):
            continue
        out.append(line.rstrip())
    return out


def sections(body: str) -> list[Section]:
    lines = clean_lines(body)
    starts = [i for i, line in enumerate(lines) if STATS.match(line.strip())]
    out: list[Section] = []
    for n, at in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        # The heading is the block of lines just above the totals, back to a blank gap after the previous section.
        back = []
        for x in reversed(lines[max(0, at - 6) : at]):
            if not x.strip():
                if back:
                    break
                continue
            back.append(x.strip())
        title = " ".join(reversed(back))
        chunk = lines[at:end]
        registered = valid = 0
        for line in chunk[:10]:
            if STATS.match(line.strip()):
                registered = count(re.findall(r"[\d.]+", line.split(":", 1)[1])[0])
            elif line.strip().startswith("Važeć"):
                valid = count(re.findall(r"[\d.]+", line.split(":", 1)[1])[0])
        council = any("Pojedine kandidacijske liste" in line for line in chunk)
        found = ROUND.search(title)
        section = Section(
            "council" if council else "executive",
            ROUNDS[found.group(1).lower()] if found else 1,
            title,
            registered,
            valid,
            start=at,
        )
        read(section, chunk)
        out.append(section)
    return out


def read(section: Section, chunk: list[str]) -> None:
    mode = ""
    current: Block | None = None
    entry: Entry | None = None
    seats_head: list[str] = []
    head = ""
    pending: int | None = None
    previous = ""
    for line in chunk:
        stripped = line.strip()
        if "Pojedine kandidacijske liste" in stripped or "dobili su sljedeći broj glasova" in stripped:
            mode = "votes"
            entry = None
            continue
        if mode in ("seats", "extra") and re.search(r"pravo na dodatne [čc]lanove", stripped):
            mode = "extra"
            current = None
            seats_head = []
            continue
        if "po kandidacijskim listama izabrani su" in stripped:
            mode = "seats"
            current = None
            seats_head = []
            continue
        found = ELECTED.match(line)
        if found or (
            previous.startswith("za ")
            and (
                re.search(r"\bizabran(?:a)? je$", stripped)
                or (stripped == "je" and re.search(r"\bizabran(?:a)?$", previous))
            )
        ):
            mode = "elected"
            section.elected_text = found.group(1) if found else previous
            previous = ""
            continue
        if stripped:
            previous = stripped
        if mode == "votes":
            m = COUNTS.match(line)
            if m:
                first = re.sub(r"^Kandidat(?:kinja)?\s+", "", clean(m.group(2)))
                entry = Entry(int(m.group(1)), first, count(m.group(3)), head=first)
                section.entries.append(entry)
                continue
            bare = re.match(r"^([\d.]+)\s+glas\w*(?:\s+[\d,]+\s*%)?$", stripped)
            if bare:  # the votes of an entry whose name is on the other side of a page break
                if entry is not None and entry.votes < 0:
                    entry.votes = count(bare.group(1))
                else:
                    pending = count(bare.group(1))
                continue
            m = ENTRY.match(line)
            if m and int(m.group(1)) == len(section.entries) + 1 and not stripped.endswith("%"):
                first = re.sub(r"^Kandidat(?:kinja)?\s+", "", clean(m.group(2)))
                entry = Entry(int(m.group(1)), first, pending if pending is not None else -1, head=first)
                pending = None
                section.entries.append(entry)
                continue
            if "U drugom krugu glasovanja" in stripped:
                mode = "advanced"
                entry = None
                continue
            if re.match(r"^(Na izborima|više od)", stripped):
                entry = None
                continue
            if entry is None or not stripped:
                continue
            m = re.match(r"^Nositelj(?:ica)? liste:\s*(.+)$", stripped)
            if m:
                entry.head = clean(m.group(1))
            elif not re.match(r"^(Kandidat(?:kinja)? za zamjenik|Zamjenik kandidata)", stripped):
                entry.text += " " + stripped
        elif mode == "seats":
            m = SEATS.match(line)
            if m and (current is None or len(current.members) >= current.seats):
                lead = re.match(r"^(\d+)\.?\s", seats_head[0]) if seats_head else None
                if lead is None:
                    raise ValueError(f"{section.title}: seats without a list number: {seats_head}")
                rank = int(lead.group(1))
                names = " ".join(re.sub(r"^\d+\.?\s+", "", h) if i == 0 else h for i, h in enumerate(seats_head))
                current = Block(rank, clean(names), int(m.group(1)))
                section.blocks.append(current)
                seats_head = []
                continue
            if not stripped:
                continue
            if current is not None and len(current.members) < current.seats:
                m = ENTRY.match(line)
                if m:
                    current.members.append(Member(int(m.group(1)), clean(m.group(2))))
                else:  # a member's name wrapped onto a second line
                    current.members[-1].name = clean(current.members[-1].name + " " + stripped)
                continue
            seats_head.append(stripped)
        elif mode == "extra":
            m = SEATS.match(line)
            if m and (current is None or len(current.members) >= current.seats):
                current = Block(0, clean(" ".join(seats_head)), int(m.group(1)), head)
                section.extra.append(current)
                seats_head, head = [], ""
                continue
            if not stripped:
                continue
            if current is not None and len(current.members) < current.seats:
                m = ENTRY.match(line)
                if m:
                    current.members.append(Member(int(m.group(1)), clean(m.group(2))))
                else:
                    current.members[-1].name = clean(current.members[-1].name + " " + stripped)
                continue
            m = re.match(r"^Nositelj(?:ica)? liste:\s*(.+)$", stripped)
            if m:
                head = clean(m.group(1))
            elif re.match(r"^\d+\.\s", stripped):
                seats_head = [re.sub(r"^\d+\.\s+", "", stripped)]
            elif seats_head and not head:
                seats_head.append(stripped)
        elif mode == "advanced":
            m = ENTRY.match(line)
            if m:
                section.advanced.append(clean(m.group(2)))
        elif mode == "elected":
            m = ENTRY.match(line)
            if m:
                section.elected.append(clean(m.group(2)))


def clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())
