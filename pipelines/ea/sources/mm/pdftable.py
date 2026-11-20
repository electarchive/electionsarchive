"""Reader for the UEC's results tables, as `pdftotext -layout` prints them: one constituency line with its totals,
then one line per candidate with the name, the party, the ordinary votes, the advance votes, and the total."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

NUMBER = re.compile(r"-?[\d,]+(?:\.\d+)?\s*%?")
SKIP = ("qE´rJ&&SdrItajctae", "rJay;olOD;a&", "BudKwifrJ", "ygwD^wpfoD;yk", "&mcdkifEIef;", "rJvufrSwf")
REGION = re.compile(r"(jynfe\s?,f|a'oBuD;|wkdif;)$")
SEAT = re.compile(r"Nr[dK]{1,2}Ue\s?,f$|rJqE´e\s?,f\s*(\(\d+\))?$|rJqE´e\s?,ftrSwf|rJqE´e\s?,frsm;")


@dataclass
class Row:
    line: int
    name: str
    party: str
    ordinary: int
    advance: int
    total: int


@dataclass
class Seat:
    line: int
    state: str
    name: str
    totals: list[float]
    ordinal: int = 0
    cancelled: bool = False
    rows: list[Row] = field(default_factory=list)


def text(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True).stdout.decode()


def integers(tokens: list[str]) -> list[int]:
    return [int(t.replace(",", "")) for t in tokens if "." not in t and "%" not in t]


def seats(layout: str, header: int = 8) -> tuple[list[Seat], list[tuple[int, str]]]:
    """Every seat with its candidate rows, and the lines that fit no pattern."""
    out: list[Seat] = []
    odd: list[tuple[int, str]] = []
    state = ""
    pre: list[list[str]] = []
    last: Row | None = None
    for no, line in enumerate(layout.split("\n"), 1):
        s = line.strip()
        if (
            not s
            or any(k in s for k in SKIP)
            or re.match(r"pOf(\s|$)", s)
            or s.startswith("rJqE´")
            or re.fullmatch(r"\d{1,3}", s)
        ):
            continue
        s = re.sub(r"(\d)\s+%", r"\1%", s)
        tokens = [u for t in re.split(r"\s{2,}", s) for u in (t.split() if re.fullmatch(r"[\d,.%\s-]+", t) else [t])]
        tokens = [re.sub(r"\s+%", "%", t) for t in tokens]
        if len(tokens) >= 2 and re.fullmatch(r"\d{1,3}", tokens[0]) is None:
            m = re.match(r"^(\d{1,3})\s(\S.*)$", tokens[0])
            if m and not NUMBER.fullmatch(tokens[0]):
                tokens[0:1] = [m[1], m[2]]
        numbers = [t for t in tokens if NUMBER.fullmatch(t)]
        words = [t for t in tokens if not NUMBER.fullmatch(t)]
        words = [w for w in words if not re.fullmatch(r"\d{1,3}", w)]
        if len(words) == 1 and REGION.search(words[0].replace(" ", "")) and numbers and out:
            state = words[0]
            last, pre = None, []
            continue
        place = [w for w in words if SEAT.search(w.replace(" ", ""))]
        words = [w for w in words if not SEAT.search(w.replace(" ", ""))]
        if len(numbers) >= header:
            name = place[0] if place else words[0]
            if place or len(numbers) >= header:
                head = [float(n.replace(",", "").rstrip("%")) for n in numbers]
                first = int(tokens[0]) if re.fullmatch(r"\d{1,3}", tokens[0]) else 0
                out.append(Seat(no, state, name, head[1:] if first else head, first))
                last, pre = None, []
                continue
        if not numbers and not place:
            if len(words) == 1 and REGION.search(words[0].replace(" ", "")):
                state = words[0]
                last, pre = None, []
                continue
            if words and not out and len(words) == 1:
                state = words[0]
            elif len(words) == 1 and last and last.party == "" and out and not pre:
                last.party = words[0]
            elif len(words) == 1 and not words[0].endswith("(c)") and last and out and not pre:
                last.name += words[0]
            elif words and out:
                pre.append(words)
            elif words:
                state = words[0]
            continue
        if place and not words and len(numbers) < 2 and not pre:
            out.append(Seat(no, state, place[0], [], cancelled=True))
            last = None
            continue
        if not out:
            odd.append((no, s))
            continue
        ints = integers(numbers)
        if len(ints) == 1:
            ints = [ints[0], 0, ints[0]]
        elif len(ints) == 2:
            ints = [ints[0], ints[1] - ints[0], ints[1]]
        if len(ints) < 3 or not (pre or words):
            odd.append((no, s))
            continue
        ints = ints[-3:]
        parts: list[str] = []
        for p in pre:
            parts.extend(p)
        parts.extend(words)
        pre = []
        if len(parts) < 2:
            row = Row(no, parts[0], "", *ints)
            out[-1].rows.append(row)
            last = row
            continue
        row = Row(no, "".join(parts[:-1]), parts[-1], *ints)
        out[-1].rows.append(row)
        last = row
    return out, odd
