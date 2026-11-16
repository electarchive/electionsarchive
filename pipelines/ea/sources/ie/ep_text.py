"""Count-by-count tables from the Department's European Parliament results volumes.

The 2004, 2009, 2014, and 2019 volumes carry a text layer. Each constituency is a table with one row per candidate
and one column per count, printed as the running total after that count, with the transfer printed above or beside
it. A table too wide for the page continues in a second block of rows, in the same order, without names.
"""

import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

SIGNED = re.compile(r"[+\-−]\s?\d[\d,]*")
NUMBER = re.compile(r"^(?:\d[\d,]*|-|–)$")
COUNT = re.compile(r"\bCount\b", re.IGNORECASE)
START = re.compile(r"EUROPEAN (?:PARLIAMENT )?ELECTION\b")
HEADER = re.compile(r"constituency\s+of\s+(.+?)\s*(?:\s{2,}|$)", re.IGNORECASE)
NON_TRANSFERABLE = re.compile(r"^\s*non[\s–-]*transferable", re.IGNORECASE)
FIGURES = {
    "electorate": re.compile(r"TOTAL\s+ELECTORATE\s+([\d,]+)", re.IGNORECASE),
    "invalid": re.compile(r"INVALID\s+BALLOT\s+PAPERS\s+([\d,]+)", re.IGNORECASE),
    "valid": re.compile(r"VALID\s+POLL\s+([\d,]+)", re.IGNORECASE),
    "seats": re.compile(r"NUMBER\s+OF\s+SEATS\s+(\d+)", re.IGNORECASE),
    "quota": re.compile(r"QUOTA\s+([\d,]+)", re.IGNORECASE),
}


@dataclass
class Row:
    text: str
    values: list[int] = field(default_factory=list)
    kind: str = "candidate"


@dataclass
class Candidate:
    label: str
    party: str | None
    outgoing: bool
    votes: list[int]
    gender: str | None = None


@dataclass
class Table:
    constituency: str
    figures: dict[str, int]
    candidates: list[Candidate]
    exhausted: list[int]
    total: list[int]
    page: int
    derived: list[int] = field(default_factory=list)
    listed: str = ""


def layout(path: Path) -> list[str]:
    """One string per page, in reading order with the layout preserved."""
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True).stdout
    return out.split("\f")


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower().replace("’", "'")) if c.isalpha())


def number(token: str) -> int:
    return 0 if token in "-–" else int(token.replace(",", ""))


def split(line: str) -> tuple[str, list[int], bool]:
    """The text before a line's unsigned numeric tail, the tail, and whether the line has signed transfers."""
    clean = re.sub(r"(\d),\s(\d{3})\b", r"\1,\2", line)
    signed = bool(SIGNED.search(clean))
    clean = SIGNED.sub(lambda hit: " " * len(hit.group()), clean).rstrip()
    found = list(re.finditer(r"\S+", clean))
    cut = len(found)
    while cut and NUMBER.match(found[cut - 1].group()):
        cut -= 1
    text = clean[: found[cut].start()] if cut < len(found) else clean
    tail = [f.group() for f in found[cut:]]
    if signed and all(t in "-\u2013" for t in tail) and not NON_TRANSFERABLE.match(text):
        tail = []
    return text.strip(), [number(t) for t in tail], signed


def tables(pages: list[str]) -> list[Table]:
    lines = [(n + 1, line) for n, page in enumerate(pages) for line in page.splitlines()]
    starts = [i for i, (_, line) in enumerate(lines) if START.search(line) and "Results" not in line]
    # a table's own header is the nearest start before its "Constituency of" line
    marks = [i for i, (_, line) in enumerate(lines) if HEADER.search(line)]
    begins: list[int] = []
    for mark in marks:
        before = [s for s in starts if s <= mark]
        begin = before[-1] if before else mark
        if begin not in begins:
            begins.append(begin)
    found: dict[str, tuple[dict[str, int], list[tuple[list[Row], int]], int, list[str]]] = {}
    for n, begin in enumerate(begins):
        end = begins[n + 1] if n + 1 < len(begins) else len(lines)
        block = lines[begin:end]
        name, (figures, listed), sections = _sections(block)
        base = re.sub(r"\s*\(continued\)", "", name, flags=re.IGNORECASE)
        if base in found and name == base:
            continue  # the volume repeats a page
        have = found.setdefault(base, ({}, [], block[0][0], []))
        have[0].update({k: v for k, v in figures.items() if k not in have[0]})
        have[1].extend(sections)
        have[3].append(listed)
    return [
        _assemble(name, figures, sections, page, "\n".join(listed))
        for name, (figures, sections, page, listed) in found.items()
    ]


def _sections(
    block: list[tuple[int, str]],
) -> tuple[str, tuple[dict[str, int], str], list[tuple[list[Row], int]]]:
    text = "\n".join(line for _, line in block)
    name = next(hit for _, line in block if (hit := HEADER.search(line)))
    constituency = re.sub(r"\s+", " ", name.group(1)).strip()
    figures: dict[str, int] = {}
    for key, pattern in FIGURES.items():
        hit = pattern.search(text)
        if hit is not None:
            figures[key] = int(hit.group(1).replace(",", ""))
    listed = [line for _, line in block[:14]]
    for at, (_, line) in enumerate(block):
        if re.search(r"CANDIDATES\s+ELECTED", line, re.IGNORECASE):
            listed += [other for _, other in block[at : at + 9]]
    text_listed = "\n".join(listed)
    sections: list[list[Row]] = []
    widths: list[int] = []
    sizes: list[int] = []
    state = "head"
    pending: list[str] = []
    current: list[Row] = []
    since = 0
    for _, line in block:
        if not line.strip():
            continue
        if re.match(r"\s*ELECTION RESULTS \d{4}", line):
            state = "done"
        if state == "between":
            since += 1
            if since > 25:
                state = "done"
        if state == "done":
            continue
        if state in ("head", "between") and COUNT.search(line):
            if current:
                sections.append(current)
            current = []
            sizes.append(len(COUNT.findall(line)))
            widths.append(sizes[-1])
            state = "preamble"
            pending = []
            continue
        if state in ("head", "between"):
            continue
        prefix, tail, signed = split(line)
        if state == "preamble":
            if not signed and not tail:
                continue
            state = "rows"
        if re.match(r"\s*TOTAL\b", line):
            current.append(Row("TOTAL", tail, "total"))
            state = "between"
            since = 0
            continue
        if NON_TRANSFERABLE.match(prefix):
            if (
                current
                and not current[-1].text
                and len(current[-1].values) < widths[-1]
                and current[-1].kind == "candidate"
            ):
                tail = current.pop().values + tail
            current.append(Row(prefix, tail, "exhausted"))
            pending = []
            continue
        if current and current[-1].kind == "exhausted" and re.match(r"(?i)(papers\s+)?not effective", prefix):
            current[-1].values += tail
            continue
        if not prefix and not tail:
            continue
        if re.match(r"(?i)not effective", prefix):
            if tail and current:
                current[-1].values += tail
            continue
        if tail and not prefix and not pending:
            if current and len(current[-1].values) < widths[-1] and current[-1].kind != "total":
                current[-1].values += tail
            else:
                current.append(Row("", tail))
            continue
        if tail:
            current.append(Row(" ".join([*pending, prefix]), tail))
            pending = []
            continue
        pending.append(prefix)
    if current:
        sections.append(current)
    figures_and_text = (figures, text_listed)
    return constituency, figures_and_text, list(zip(sections, sizes, strict=True))


def _assemble(
    constituency: str, figures: dict[str, int], parts: list[tuple[list[Row], int]], page: int, listed: str
) -> Table:
    sections = [rows for rows, _ in parts]
    first = sections[0]
    for later in sections[1:]:
        if len(later) != len(first):
            raise ValueError(f"{constituency}: a continued table has {len(later)} rows, not {len(first)}")
        for row, extra in zip(first, later, strict=True):
            row.values += extra.values
    # a row printed once per column block only counts once, so widths are checked against the total row
    total = next(r for r in first if r.kind == "total").values
    rounds = len(total)
    exhausted = next((r.values for r in first if r.kind == "exhausted"), [0] * rounds)
    derived: list[int] = []
    if len(exhausted) == rounds - 1:
        # the volume leaves one dash out of the non-transferable row; it is the count where the others leave no room
        gaps = [sum(c.votes[k] for c in candidates_of(first, rounds)) for k in range(rounds)]
        want = [total[k] - gaps[k] for k in range(rounds)]
        fits = [k for k in range(rounds) if want[:k] + want[k + 1 :] == exhausted]
        if fits:
            at = fits[-1]
            exhausted = [*exhausted[:at], want[at], *exhausted[at:]]
            derived = [at + 1]
    candidates = candidates_of(first, rounds)
    for candidate in candidates:
        if len(candidate.votes) != rounds:
            raise ValueError(f"{constituency}: {candidate.label} has {len(candidate.votes)} counts, not {rounds}")
    if len(exhausted) != rounds:
        raise ValueError(f"{constituency}: non-transferable row has {len(exhausted)} counts, not {rounds}")
    return Table(constituency, figures, candidates, exhausted, total, page, derived, listed)


def candidates_of(rows: list[Row], rounds: int) -> list[Candidate]:
    return [_candidate(r.text, r.values) for r in rows if r.kind == "candidate" and r.text and r.values]


def _candidate(text: str, votes: list[int]) -> Candidate:
    outgoing = text.lstrip().startswith("*")
    text = text.lstrip().lstrip("*").strip()
    party: str | None = None
    gender: str | None = None
    hit = re.match(r"^(.*?)\s*\(([^()]*)\)\s*([MF])?$", text)
    if hit is not None:
        label, party, gender = hit.group(1).strip(), hit.group(2).strip(), hit.group(3)
    else:
        parts = re.split(r"\s{2,}", text)
        label = parts[0].strip()
        if len(parts) > 1 and parts[1].strip() in ("M", "F"):
            gender = parts[1].strip()
        elif len(parts) > 1:
            party = parts[1].strip()
        if len(parts) == 1:
            hit = re.match(r"^(.*\S)\s+((?:[A-Za-z0-9]{1,3}\.)+|Lab\.|Non-P\.)$", text)
            if hit is not None:
                label, party = hit.group(1), hit.group(2)
    return Candidate(re.sub(r"\s+", " ", label), party, outgoing, votes, gender)
