"""The State Electoral Commission's decisions naming the members elected to the Sabor, one PDF per contest.

A district's decision prints the lists that won seats and, under each, the candidates elected from it. A minority
unit's decision prints the candidates elected. The text is read with pdftotext; names keep the authority's spelling.
"""

import re
import subprocess
from dataclasses import dataclass, field

from ea import raw

SOURCE = "hr.izbori"
DATA = "https://www.izbori.hr/arhiva-izbora/data/parlament"


def url(year: int, contest: str) -> str:
    if contest.startswith("12-"):
        return f"{DATA}/{year}/izabrani/i_{contest[3:]}3_012_0000.pdf"
    return f"{DATA}/{year}/izabrani/i_02_{int(contest):03d}_0000.pdf"


@dataclass
class Member:
    name: str
    position: int | None  # the candidate's number on the list, when the decision prints it


@dataclass
class Block:
    names: list[str]  # the parties and the list's own name, as printed above the seat count
    seats: int
    members: list[Member] = field(default_factory=list)


def text(path: str) -> str:
    return subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True, text=True).stdout


def _lines(body: str) -> list[str]:
    return [line.rstrip() for line in body.splitlines()]


def _end(line: str) -> bool:
    return bool(re.match(r"^\s*predsjednik\b", line, re.IGNORECASE))


def lists(body: str) -> list[Block]:
    """The lists that won seats and the members elected from each, from section IV of a district's decision."""
    lines = _lines(body)
    start = next((i for i, line in enumerate(lines) if re.match(r"^\s*IV\.\s", line)), None)
    if start is None:
        raise ValueError("no section IV")
    blocks: list[Block] = []
    names: list[str] = []
    current: Block | None = None
    for line in lines[start + 1 :]:
        if _end(line):
            break
        stripped = line.strip()
        if not stripped or re.fullmatch(r"\d{1,3}", stripped):
            continue
        seats = re.search(r"dobil[ao] (?:je )?(\d+) mjest", stripped)
        if seats:
            if not any(re.match(r"\d+\.\s", n) for n in names):
                names = [n for n in names if n == n.upper()]  # a single list's decision has no numbered header
            current = Block([re.sub(r"^\d+\.\s+", "", n) for n in names], int(seats.group(1)))
            blocks.append(current)
            names = []
            continue
        numbered = re.match(r"^(\d+)\.\s+(\S.*)$", stripped)
        if current is not None and len(current.members) < current.seats:
            if numbered:
                member = re.match(
                    r"^(.*?)\s*\((?:kandidat\w*) na listi pod rednim brojem (\d+)\.\)\s*$", numbered.group(2)
                )
                if member:
                    current.members.append(Member(member.group(1).strip(), int(member.group(2))))
                else:
                    current.members.append(Member(numbered.group(2).strip(), None))
            elif current.members:
                current.members[-1].name += " " + stripped  # a name wrapped onto a second line
            continue
        if re.match(r"^te\b", stripped):
            continue
        if numbered and int(numbered.group(1)) == len(blocks) + 1:
            names = [stripped]
        else:
            names.append(stripped)
    if any(len(block.members) != block.seats for block in blocks):
        raise ValueError("a list's elected members do not match its seats")
    return blocks


def minority(body: str) -> list[str]:
    """The lines of a minority decision's section III, which name the candidates elected and their deputies."""
    lines = _lines(body)
    start = next((i for i, line in enumerate(lines) if re.match(r"^\s*III\.\s", line)), None)
    if start is None:
        raise ValueError("no section III")
    out = [re.sub(r"^\s*III\.\s*", "", lines[start])]
    for line in lines[start + 1 :]:
        if _end(line):
            break
        if line.strip() and not re.fullmatch(r"\s*\d{1,3}\s*", line):
            out.append(line.strip())
    return out


def read(year: int, contest: str) -> str | None:
    try:
        artifact = raw.cached(SOURCE, url(year, contest))
    except LookupError:
        return None
    return text(str(artifact.path))
