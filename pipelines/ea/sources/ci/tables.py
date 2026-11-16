"""Readers for the Commission Électorale Indépendante's results PDFs, which come as `pdftotext -layout` text."""

import re
from dataclasses import dataclass, field
from datetime import date

SPLIT = re.compile(r"\s{2,}")
PCT = re.compile(r"^\d+[.,]\d+\s*%$")
DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
MONTHS = {
    "JANVIER": 1,
    "FEVRIER": 2,
    "FÉVRIER": 2,
    "MARS": 3,
    "AVRIL": 4,
    "MAI": 5,
    "JUIN": 6,
    "JUILLET": 7,
    "AOUT": 8,
    "AOÛT": 8,
    "SEPTEMBRE": 9,
    "OCTOBRE": 10,
    "NOVEMBRE": 11,
    "DECEMBRE": 12,
    "DÉCEMBRE": 12,
}
INDEPENDENT = {"INDEPENDANT", "INDEP.", "INDEP", "INDÉPENDANT"}


def integer(text: str) -> int | None:
    digits = text.replace(" ", "").replace(",", "").replace(" ", "")
    return int(digits) if digits.isdigit() else None


def count(text: str) -> int | None:
    return 0 if text.strip() == "-" else integer(text)


def clean(text: str) -> str:
    return " ".join(text.split())


@dataclass
class Candidate:
    party: str
    name: str
    score: int
    elected: bool
    line: int
    column: int = 0
    percent: float | None = None
    score_column: int = 0
    members: list[str] = field(default_factory=list)  # a list's candidates, in ballot order


@dataclass
class Circumscription:
    number: int
    name: str
    day: date | None
    seats: int | None
    figures: dict[str, int]
    candidates: list[Candidate] = field(default_factory=list)
    line: int = 0
    excess: int = 0  # candidate votes beyond the printed valid votes, where the source's own figures disagree


KEYS = {
    "2025": ["bureaux", "registered", "cast", "invalid", "exprimes", "blank"],
    "2021": ["bureaux", "reg_m", "reg_f", "registered", "cast_m", "cast_f", "cast", "invalid", "blank", "exprimes"],
    "2016": ["seats", "bureaux", "registered", "cast_m", "cast_f", "cast", "invalid", "exprimes"],
    "2011": ["registered", "cast", "invalid", "exprimes"],
}
SKIP = re.compile(
    r"ELECTION DES DEPUTES|SCRUTINS? D|Page \d+|COMMISSION|ELECTORALE|INDEPENDANTE|RESULTATS? DES|LEGISLATURE|"
    r"REPUBLIQUE|Union|CANDIDATS? /|GROUPEMENTS|NB BV|TAUX DE|BULL\.|EXPRIMES|POPULATION|INTITULE|HOMMES|"
    r"POLITIQUES|SIEGES|PARTICIPA|GRP\. POL|RESULTAT NATIONAL|DATE DU|NULS|OBTENU|^\s*(REGI|ON|REGION)\s*$"
)
FIXES = {"FPI/ AFD/ UDPCI/ ARC-EN-CIELGOURENE": ("FPI/ AFD/ UDPCI/ ARC-EN-CIEL", "GOURENE")}
RESULT = re.compile(r"(?i)[EÉ]LU[SE]?(?:\(E\))?|BALLOTAGE")
MEMBER = re.compile(r"^\d{1,2} - ")


def _tail(segments: list[str]) -> tuple[Candidate, int] | None:
    """Read a candidate from the right-hand segments of a line; return it with the index of its party segment."""
    work = list(segments)
    elected = False
    if work and RESULT.fullmatch(work[-1]):
        elected = work[-1].upper().startswith(("ELU", "ÉLU"))
        work.pop()
    if len(work) >= 3 and not PCT.match(work[-1]) and count(work[-1]) is not None:
        # the percentage sits on a neighboring line
        score = count(work[-1])
        name, party = work[-2], work[-3]
        if (
            score is not None
            and len(party) <= 25
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9./\-]*(?: [A-Za-z0-9./\-]+)*", party)
            and re.search(r"[^\W\d_]{2}", name)
            and not re.fullmatch(r"[\d ,.\-]+", name)
            and not DATE.search(name)
        ):
            return Candidate(clean(party), clean(name), score, elected, 0), len(work) - 3
        return None
    if len(work) < 3 or not PCT.match(work[-1]):
        return None
    score = 0 if work[-2] == "-" else integer(work[-2])
    if score is None:
        return None
    letters = re.compile(r"[^\W\d_]")
    if len(work) >= 4 and letters.search(work[-3]) and not re.fullmatch(r"[\d ,.]+", work[-3]):
        name, party, index = work[-3], work[-4], len(work) - 4
        if "%" in party or not letters.search(party):
            name, party, index = "?", work[-3], len(work) - 3
    elif letters.search(work[-3]) and not re.fullmatch(r"[\d ,.]+", work[-3]):
        name, party, index = "?", work[-3], len(work) - 3
    else:
        return None
    percent = float(work[-1].replace("%", "").replace(",", ".").strip())
    return Candidate(clean(party), clean(name), score, elected, 0, 0, percent), index


def _stamp(text: str) -> date | None:
    found = re.search(r"(\d{1,2})\s+([A-ZÉÛ]+)\s+(\d{4})", text.upper())
    if found and found.group(2) in MONTHS:
        return date(int(found.group(3)), MONTHS[found.group(2)], int(found.group(1)))
    found = DATE.search(text)
    if found:
        return date(int(found.group(3)), int(found.group(2)), int(found.group(1)))
    return None


def _cells(raw: str) -> list[tuple[int, str]]:
    """The line's segments with their starting columns."""
    cells = []
    for found in re.finditer(r"\S+(?: \S+)*", raw):
        text = found.group()
        merged = re.fullmatch(r"(\d[\d ,]*\d) (?!VOLONTAIRES)([A-Z][A-Za-z.\-]*(?: ?[A-Z/\-]+)*)", text)
        if merged and integer(merged.group(1)) is not None:
            cells.append((found.start(), merged.group(1)))
            cells.append((found.start() + len(merged.group(1)) + 1, merged.group(2)))
        else:
            cells.append((found.start(), text))
    return cells


class Row:
    def __init__(self, line: int, number: int, first: str) -> None:
        self.line = line
        self.number = number
        self.first = first
        self.numbers: list[int] = []
        self.partial: list[int] = []
        self.day: date | None = None
        self.lines = [line]
        self.column = 0


def legislative(text: str, layout: str) -> tuple[list[Circumscription], list[int]]:
    """Read a CEI legislative results table, returning its circumscriptions and the national row's figures."""
    lines = text.splitlines()
    keys = KEYS[layout]
    rows: list[Row] = []
    pending: list[tuple[int, list[tuple[int, str]]]] = []
    candidates: list[Candidate] = []
    cells: dict[int, list[tuple[int, str]]] = {}
    total: list[int] = []
    open_row: Row | None = None
    split = _split(lines) if layout == "2011" and any("LISTE" in raw for raw in lines) else None
    ballot: list[Candidate | None] = [None]
    marks: list[tuple[int, str]] = []
    stray: list[tuple[int, int]] = []
    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        line = _cells(raw)
        everything = line
        if split is not None:
            _right(index, [cell for cell in line if cell[0] >= split], ballot, candidates)
            line = [cell for cell in line if cell[0] < split]
            if not line:
                cells[index] = everything
                continue
        cells[index] = everything
        segments = [text for _, text in line]
        if all(PCT.match(s) for s in segments):
            continue
        found = _tail(segments) if split is None else None
        if found is None and any(RESULT.fullmatch(t) for t in segments):
            # an "elected" mark printed on its own line
            marks.extend((index, t) for t in segments if RESULT.fullmatch(t))
            line = [cell for cell in line if not RESULT.fullmatch(cell[1])]
            segments = [t for _, t in line]
            if not segments:
                continue
        meta = segments
        if found is None and SKIP.search(raw):
            continue
        if found is not None:
            candidate, at = found
            if candidate.name != "?" and at + 1 < len(line) and line[at + 1][0] - line[at][0] > 40:
                # the "party" is a region label at the far left; the party is the next segment and the name wraps
                candidate.party, candidate.name = candidate.name, "?"
                at += 1
            candidate.line = index
            candidate.column = line[at][0]
            last = len(segments) - 1 - (1 if RESULT.fullmatch(segments[-1]) else 0)
            candidate.score_column = line[last - (1 if PCT.match(segments[last]) else 0)][0]
            candidates.append(candidate)
            meta = segments[:at]
        if meta and meta[0].upper().startswith("TOTAL") and not total:
            total = [n for s in meta[1:] if not PCT.match(s) and (n := integer(s)) is not None]
            continue
        # a circumscription's head: its number, alone or with the start of its name
        head = None
        for at, segment in enumerate(meta):
            combined = re.fullmatch(r"(?:[A-Z][A-Z' .\-]+ )?(\d{3})", segment)
            if (
                combined
                and all(integer(earlier) is None for earlier in meta[:at])
                and (at == 0 or len(segment) > 3 or split is None)
            ):
                head = (at, int(combined.group(1)), "")
                break
            regional = re.fullmatch(r"[A-Z][A-Z' .\-]+ (\d{3}) (\S.*)", segment)
            if regional and at == 0:
                head = (at, int(regional.group(1)), regional.group(2))
                break
            spaced = re.match(r"^(\d{3}) (?=[A-ZÉ])", segment)
            if spaced and all(integer(earlier) is None for earlier in meta[:at]) and at < 3:
                head = (at, int(spaced.group(1)), segment[4:])
                break
            dashed = re.match(r"^(\d{3}) ?- ?(.*)", segment)
            if dashed:
                head = (at, int(dashed.group(1)), dashed.group(2))
                break
        numbers_at = lambda items: [
            n for s in items if not PCT.match(s) and not DATE.fullmatch(s) and (n := count(s)) is not None
        ]
        if head is not None:
            at, number, first = head
            after = meta[at + 1 :]
            if rows and rows[-1].number == number:
                open_row = rows[-1]  # a row repeated at the top of the next page
                open_row.lines.append(index)
                if first and first not in open_row.first:
                    open_row.first = clean(f"{open_row.first} {first}")
                continue
            row = Row(index, number, first)
            row.column = line[at][0]
            rows.append(row)
            open_row = row
            tail = after
            if stray and index - stray[-1][0] <= 6 and sum(1 for s in after if count(s) is not None) < 3:
                # figures printed above the head, perhaps at the foot of the previous page
                row.lines.extend(at for at, _ in stray)
                row.partial = [n for _, n in stray]
                if len(row.partial) == len(keys):
                    row.numbers = row.partial
            stray = []
        elif (
            open_row is not None
            and not open_row.numbers
            and len(numbers_at(meta)) >= len(keys) - 1
            and sum(1 for s in meta if integer(s) is None and not PCT.match(s)) <= 1
        ):
            first_number = next(at for at, s in enumerate(meta) if integer(s) is not None)
            if first_number:
                pending.append((index, line[:first_number]))
            tail = meta[first_number:]
            open_row.lines.append(index)
        elif (
            split is not None
            and numbers_at(meta)
            and sum(1 for s in meta if integer(s) is None and not PCT.match(s)) <= 1
        ):
            # figures that the layout scattered over several lines
            numbers = numbers_at(meta)
            first_number = next(at for at, s in enumerate(meta) if count(s) is not None)
            if first_number:
                pending.append((index, line[:first_number]))
            if open_row is not None and not open_row.numbers:
                open_row.lines.append(index)
                open_row.partial += numbers
                if len(open_row.partial) == len(keys):
                    open_row.numbers = open_row.partial
            else:
                stray = [item for item in stray if index - item[0] <= 6] + [(index, n) for n in numbers]
            continue
        else:
            text_only = " ".join(s for s in meta if not PCT.match(s))
            dated = _stamp(text_only) is not None
            if (
                text_only
                and (dated or not re.search(r"\d{4,}|\d{1,3} \d{3}", text_only))
                and not (found is not None and not meta)
            ):
                pending.append((index, [cell for cell in line[: len(meta)] if not PCT.match(cell[1])]))
            continue
        row = open_row
        assert row is not None
        stamped: list[str] = []
        for segment in tail:
            found_date = DATE.search(segment)
            if found_date and not DATE.fullmatch(segment):
                row.day = _stamp(segment)
                segment = clean(segment.replace(found_date.group(), ""))
            stamped.append(segment)
        tail = [segment for segment in stamped if segment]
        for segment in tail:
            if count(segment) is not None or PCT.match(segment) or DATE.fullmatch(segment):
                break
            row.first = clean(f"{row.first} {segment}")
        if sum(1 for s in tail if count(s) is not None or PCT.match(s)) >= 3:
            for segment in tail:
                if DATE.fullmatch(segment):
                    row.day = _stamp(segment)
            row.numbers = numbers_at(tail)
    if split is None:
        _lone(candidates, cells)
    else:
        for candidate in candidates:
            if not candidate.party or candidate.percent is None:
                raise ValueError(f"list without a party or score at line {candidate.line}: {candidate.name}")
    for at, mark in marks:
        near = [c for c in candidates if abs(c.line - at) <= 2 and not c.elected]
        if near and mark.upper().startswith(("ELU", "ÉLU")):
            min(near, key=lambda c: abs(c.line - at)).elected = True
    for row in rows:
        if len(row.numbers) != len(keys):
            raise ValueError(
                f"circumscription {row.number}: {len(row.numbers)} figures, expected {len(keys)}: {row.numbers}"
            )
    _fragments(candidates, cells, lines)
    return _assemble(rows, pending, candidates, keys), total


def _split(lines: list[str]) -> int:
    """The column where the candidates' party names start, read from the header of a fixed-pitch layout."""
    return min(raw.index("GRP. POL.") for raw in lines if "GRP. POL." in raw) - 4


def _right(
    index: int, right: list[tuple[int, str]], ballot: list[Candidate | None], candidates: list[Candidate]
) -> None:
    """Read the candidate side of a line in the layouts where ballot lists wrap around their party and score."""
    texts = [t for _, t in right]
    if not texts:
        return
    if (
        ballot[0] is not None
        and len(texts) >= 2
        and not texts[0].upper().startswith("LISTE")
        and MEMBER.match(texts[1])
    ):
        # the list's party, and perhaps its score, share a line with one of its members
        candidate = ballot[0]
        rest = texts[1:]
        if RESULT.fullmatch(rest[-1]):
            candidate.elected = rest[-1].upper().startswith(("ELU", "ÉLU"))
            rest = rest[:-1]
        if len(rest) >= 3 and PCT.match(rest[-1]) and count(rest[-2]) is not None:
            candidate.score = count(rest[-2]) or 0
            candidate.percent = float(rest[-1].replace("%", "").replace(",", ".").strip())
            rest = rest[:-2]
        candidate.members.append(clean(" ".join(rest)))
        if not candidate.party:
            candidate.party = clean(texts[0])
        return
    found = _tail(texts)
    if found is not None and (found[0].name != "?" or ballot[0] is None or ballot[0].percent is not None):
        candidate, at = found
        candidate.line, candidate.column = index, right[at][0]
        candidate.score_column = right[-2][0]
        candidates.append(candidate)
        ballot[0] = None
        return
    if texts[0].upper().startswith("LISTE"):
        candidate = Candidate("", clean(texts[0]), 0, False, index, right[0][0], None)
        candidates.append(candidate)
        ballot[0] = candidate
        return
    candidate = ballot[0]
    if candidate is None:
        return
    if MEMBER.match(texts[0]):
        candidate.members.append(texts[0])
        return
    if len(texts) == 1 and candidate.party and candidate.members and not RESULT.fullmatch(texts[0]):
        candidate.members[-1] = clean(f"{candidate.members[-1]} {texts[0]}")
        return
    if RESULT.fullmatch(texts[-1]):
        candidate.elected = texts[-1].upper().startswith(("ELU", "ÉLU"))
        texts = texts[:-1]
    if len(texts) >= 2 and PCT.match(texts[-1]) and count(texts[-2]) is not None:
        candidate.score = count(texts[-2]) or 0
        candidate.percent = float(texts[-1].replace("%", "").replace(",", ".").strip())
        texts = texts[:-2]
    if (
        len(texts) == 1
        and not candidate.party
        and not candidate.members
        and candidate.percent is None
        and right[0][0] == candidate.column
    ):
        candidate.name = clean(f"{candidate.name} {texts[0]}")  # the list's title wraps
        return
    if texts and not candidate.party and re.search(r"[^\W\d_]", texts[0]):
        candidate.party = clean(texts[0])


def _lone(candidates: list[Candidate], cells: dict[int, list[tuple[int, str]]]) -> None:
    """Add candidates printed as a bare score and percentage, whose party and name sit on the lines around them."""
    regular = [c for c in candidates if c.name != "?" and c.percent is not None]
    if not regular:
        return
    taken = {c.line for c in candidates}
    found: list[Candidate] = []
    for index, line in cells.items():
        if index in taken or len(line) < 2:
            continue
        texts = [t for _, t in line]
        elected = False
        if RESULT.fullmatch(texts[-1]):
            elected = texts[-1].upper().startswith(("ELU", "ÉLU"))
            line, texts = line[:-1], texts[:-1]
        if len(texts) < 2 or not PCT.match(texts[-1]) or count(texts[-2]) is None:
            continue
        near = min(regular, key=lambda c: abs(c.line - index))
        if abs(near.line - index) > 12 or line[-2][0] < near.score_column - 6:
            continue
        score = count(texts[-2]) or 0
        percent = float(texts[-1].replace("%", "").replace(",", ".").strip())
        found.append(Candidate("?", "?", score, elected, index, near.column, percent, line[-2][0]))
    candidates.extend(found)
    candidates.sort(key=lambda c: c.line)


def _fragments(candidates: list[Candidate], cells: dict[int, list[tuple[int, str]]], lines: list[str]) -> None:
    """Complete candidates whose name wraps onto the lines around them, and gather the members of ballot lists."""
    regular = [c for c in candidates if c.name != "?" and c.party != "?"]
    for candidate in candidates:
        if candidate.party == "?":
            nearby = [
                i
                for i in range(candidate.line - 3, candidate.line + 4)
                if i != candidate.line and not any(c.line == i for c in candidates)
            ]
            column = candidate.column
            party_parts = [
                t for i in nearby for at, t in cells.get(i, []) if abs(at - column) <= 3 and not PCT.match(t)
            ]
            name_parts = [
                t
                for i in nearby
                for at, t in cells.get(i, [])
                if at > column + 3 and not PCT.match(t) and count(t) is None
            ]
            if not party_parts or not name_parts:
                raise ValueError(f"candidate without a party or name at line {candidate.line}")
            candidate.party, candidate.name = clean(party_parts[0]), clean(" ".join(name_parts))
            continue
        if candidate.name != "?":
            continue
        column = candidate.column
        party_column = min(regular, key=lambda c: abs(c.line - candidate.line)).column
        if column > party_column + 8:
            # the party wrapped onto the lines around this one, leaving it with the name's last words
            nearby = [
                i
                for i in range(candidate.line - 3, candidate.line + 4)
                if i != candidate.line and not any(c.line == i for c in candidates)
            ]
            party_parts = [text for i in nearby for at, text in cells.get(i, []) if abs(at - party_column) <= 3]
            name_parts = [
                (i, text)
                for i in nearby
                for at, text in cells.get(i, [])
                if abs(at - column) <= 3 and not PCT.match(text)
            ]
            if party_parts:
                party = party_parts[0]
                if "ARC-EN-CIEL" in party:
                    party, _, rest = party.partition("ARC-EN-CIEL")
                    party = f"{party}ARC-EN-CIEL"
                    name_parts.insert(0, (candidate.line - 4, rest))
                parts = [text for i, text in sorted(name_parts) if i < candidate.line] + [candidate.party]
                parts += [text for i, text in sorted(name_parts) if i > candidate.line]
                candidate.party = clean(party)
                candidate.name = clean(" ".join(p for p in parts if p))
                candidate.column = party_column
                continue

        def right(index: int) -> list[str]:
            return [
                text
                for at, text in cells.get(index, [])
                if at > column and not PCT.match(text) and integer(text) is None and not RESULT.fullmatch(text)
            ]

        def plain(index: int) -> bool:
            return not any(c.line == index for c in candidates)

        above = right(candidate.line - 1) if plain(candidate.line - 1) else []
        if above and above[0].startswith("LISTE"):
            candidate.name = clean(" ".join(above))
            at = candidate.line + 1
            while at < len(lines) and (not lines[at].strip() or (right(at) and MEMBER.match(right(at)[0]))):
                if right(at):
                    candidate.members.append(right(at)[0])
                at += 1
            continue
        below = right(candidate.line + 1) if plain(candidate.line + 1) else []
        candidate.name = clean(" ".join([*above, *below]))
        if not candidate.name:
            raise ValueError(f"candidate without a name at line {candidate.line}")


def _assemble(
    rows: list[Row], pending: list[tuple[int, list[tuple[int, str]]]], candidates: list[Candidate], keys: list[str]
) -> list[Circumscription]:
    circs: list[Circumscription] = []
    anchors: list[tuple[int, int]] = []
    columns: list[int] = []
    for i, row in enumerate(rows):
        values = dict(zip(keys, row.numbers, strict=True))
        seats = values.pop("seats", None)
        circs.append(Circumscription(row.number, row.first, row.day, seats, values, line=row.line))
        columns.append(row.column)
        anchors.extend((at, i) for at in row.lines)
    fragments: dict[int, list[tuple[int, str]]] = {i: [] for i in range(len(circs))}
    for line, items in pending:
        near, best = min((abs(at - line), i) for at, i in anchors)
        text = " ".join(t for column, t in items if column >= columns[best] - 2)
        if near <= 4 and text:
            fragments[best].append((line, text))
    for i, circ in enumerate(circs):
        pieces = sorted(fragments[i] + ([(circ.line, circ.name)] if circ.name else []))
        words: list[str] = []
        stamps: list[date] = []
        for _, piece in pieces:
            if piece in words:
                continue
            stamp = _stamp(piece)
            if stamp is not None and re.fullmatch(r"(?i)\(?\s*(?:scrutin du )?[\dA-ZÉÛ/ ]+\)?", piece.strip()):
                stamps.append(stamp)
                continue
            words.append(piece)
        if stamps:
            circ.day = max(stamps)
        circ.name = re.sub(r"-\s+(?=[A-ZÉÈÊ])", "-", clean(" ".join(words)))
    return _share(circs, candidates)


def _share(circs: list[Circumscription], candidates: list[Candidate], strict: bool = True) -> list[Circumscription]:
    """Cut the candidates, in printed order, into one run per circumscription.

    A run's scores add up to the circumscription's valid votes, and each printed percentage agrees with the
    circumscription's own total, which settles runs that a bare sum leaves open.
    """
    import functools
    import sys

    sys.setrecursionlimit(10000)
    n, m = len(candidates), len(circs)

    def fits(candidate: Candidate, circ: Circumscription) -> bool:
        if candidate.percent is None or not strict or not circ.figures["exprimes"]:
            return True
        return abs(candidate.score * 100 / circ.figures["exprimes"] - candidate.percent) < 0.0101

    def runs(i: int, j: int) -> list[int]:
        circ = circs[i]
        target = circ.figures["exprimes"] - circ.figures.get("blank", 0)
        total = 0
        exact: list[int] = []
        over: list[int] = []
        short: list[int] = []
        k = j
        while True:
            if total == target:
                exact.append(k)
            elif total > target and (k >= n or not fits(candidates[k], circ)):
                over.append(k)
            elif 0 < total < target and (k >= n or not fits(candidates[k], circ)):
                short.append(k)
            if k >= n or not fits(candidates[k], circ):
                break
            total += candidates[k].score
            k += 1
        return [*exact, *over, *short] if strict else exact

    @functools.cache
    def solve(i: int, j: int) -> tuple[int, ...] | None:
        if i == m:
            return () if j == n else None
        for end in runs(i, j):
            rest = solve(i + 1, end)
            if rest is not None:
                return (end, *rest)
        return None

    ends = solve(0, 0)
    if ends is None:
        reachable = {0}
        for i, circ in enumerate(circs):
            following = {k for j in reachable for k in runs(i, j)}
            if not following:
                if strict:
                    try:
                        return _share(circs, candidates, strict=False)
                    except ValueError as error:
                        raise ValueError(
                            f"circumscription {circ.number} (printed at line {circ.line}) cannot be filled; "
                            f"by sums alone: {error}"
                        ) from None
                lines = [candidates[j].line for j in sorted(reachable) if j < n]
                target = circ.figures["exprimes"] - circ.figures.get("blank", 0)
                raise ValueError(
                    f"circumscription {circ.number} (printed at line {circ.line}) cannot be filled from candidates "
                    f"at lines {lines[:6]}; valid votes {target}"
                )
            reachable = following
        if strict:
            return _share(circs, candidates, strict=False)  # a printed percentage may itself be a misprint
        raise ValueError("candidates cannot be shared out among the circumscriptions")
    start = 0
    for circ, end in zip(circs, ends, strict=True):
        circ.candidates = candidates[start:end]
        total = sum(c.score for c in circ.candidates)
        circ.excess = total - (circ.figures["exprimes"] - circ.figures.get("blank", 0))
        start = end
    return circs


def layout(path: str, pitch: float = 4.0, gap: float = 6.0) -> str:
    """Rebuild a PDF's text with every column at a fixed position on every page, from word coordinates.

    `pdftotext -layout` shifts columns from page to page; this keeps a column at the same character offset
    throughout, so the two sides of a wrapped table can be told apart by position.
    """
    import html
    import subprocess

    out = subprocess.run(["pdftotext", "-bbox", path, "-"], check=True, capture_output=True, text=True).stdout
    pages: list[list[tuple[float, float, float, str]]] = []
    for line in out.splitlines():
        if "<page " in line:
            pages.append([])
            continue
        found = re.match(r'\s*<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*)</word>', line)
        if found:
            x0, y0, x1, y1, word = found.groups()
            pages[-1].append((float(x0), (float(y0) + float(y1)) / 2, float(x1), html.unescape(word)))
    lines: list[str] = []
    for words in pages:
        rows: list[tuple[float, list[tuple[float, float, str]]]] = []
        for x0, y, x1, word in sorted(words, key=lambda w: (w[1], w[0])):
            if rows and abs(rows[-1][0] - y) <= 5.5:
                rows[-1][1].append((x0, x1, word))
            else:
                rows.append((y, [(x0, x1, word)]))
        last = None
        for y, row in rows:
            row.sort()
            starts: list[float] = []
            ends: list[float] = []
            words_in: list[str] = []
            for x0, x1, word in row:
                joined = bool(words_in) and x0 - ends[-1] < gap
                if joined:
                    last_word = words_in[-1].rsplit(" ", 1)[-1]
                    if (
                        PCT.match(last_word)
                        or PCT.match(word)
                        or (
                            re.fullmatch(r"[\d,.]+", last_word)
                            and re.match(r"[^\W\d_]", word)
                            and not re.match(r"^\d{1,2} -", words_in[-1])
                        )
                    ):
                        joined = False
                if joined:
                    ends[-1] = x1
                    words_in[-1] = f"{words_in[-1]} {word}"
                else:
                    starts.append(x0)
                    ends.append(x1)
                    words_in.append(word)
            text = ""
            for x0, word in zip(starts, words_in, strict=True):
                column = max(int(x0 / pitch), len(text) + 2 if text else 0)
                text = text.ljust(column) + word
            if last is not None and y - last > 14:
                lines.append("")
            lines.append(text)
            last = y
        lines.append("")
    return "\n".join(lines)


@dataclass
class Unit:
    region: str
    name: str
    figures: dict[str, int]
    votes: list[int]


@dataclass
class Presidential:
    units: list[Unit]
    total: Unit | None


PRESIDENTIAL = {
    "2025": ["bureaux", "registered", "staff", "cast", "invalid", "exprimes", "blank"],
    "2020": ["bureaux", "registered", "staff", "cast", "invalid", "blank", "exprimes"],
    "2010-1": ["bureaux", "registered", "cast", "invalid", "exprimes"],
    "2010-2": ["bureaux", "electorate", "registered", "cast", "unlisted", "invalid", "exprimes"],
}
CANDIDATES = {"2025": 5, "2020": 4, "2010-1": 14, "2010-2": 2}


def _numbers(line: str) -> tuple[list[str], list[int]]:
    """Split a table line into its labels and its integers, dropping percentages."""
    labels: list[str] = []
    values: list[int] = []
    for segment in SPLIT.split(line.strip()):
        segment = re.sub(r"\s*\d+[.,]\d+\s*%$", "", segment.strip())
        if not segment or PCT.match(segment):
            continue
        digits = segment.replace(" ", "")
        if digits.isdigit():
            values.append(int(digits))
        else:
            labels.append(segment.replace("♦", "").strip())
    return labels, values


def presidential(text: str, layout: str) -> Presidential:
    """Read a CEI presidential results table into its finest places and its printed national row."""
    keys = PRESIDENTIAL[layout]
    width = len(keys) + CANDIDATES[layout]
    lines = text.splitlines()
    start = next(i for i, raw in enumerate(lines) if "PAR DEPARTEMENT" in raw) if layout == "2010-1" else 0
    units: list[Unit] = []
    total: Unit | None = None
    region = ""
    parent: Unit | None = None  # the latest region row, kept as a place when it has none below it
    children = 0
    for raw in lines[start:]:
        if not raw.strip():
            continue
        labels, values = _numbers(raw)
        if len(values) != width:
            continue
        figures = dict(zip(keys, values[: len(keys)], strict=True))
        votes = values[len(keys) :]
        name = " ".join(labels).strip()
        if name.upper() in ("TOTAL", "TOTAL NATIONAL", "TERRITOIRE TOTAL") or (not labels and layout == "2025"):
            total = Unit("", "TOTAL", figures, votes)
            continue
        if not labels:
            continue
        if re.search(r"TOTAUX|SOUS[- ]?TOTAL|^TOTAL", name.upper()):
            continue
        indent = len(raw) - len(raw.lstrip())
        if layout in ("2025", "2020"):
            leaf = "♦" in raw if layout == "2020" else indent > 0
            if not leaf:
                if parent is not None and not children:
                    units.append(parent)
                region, parent, children = name, Unit(name, name, figures, votes), 0
                continue
            children += 1
            units.append(Unit(region, name, figures, votes))
            continue
        if len(labels) == 2:
            region = labels[0]
        units.append(Unit(region, labels[-1], figures, votes))
    if parent is not None and not children:
        units.append(parent)
    if layout == "2010-1":
        total = _tight_total(lines, keys, units)
    return Presidential(units, total)


def _tight_total(lines: list[str], keys: list[str], units: list[Unit]) -> Unit | None:
    """Read the national row, whose candidate columns touch, choosing the split that adds up."""
    raw = next((line for line in lines if "TOTAL NATIONAL" in line), None)
    if raw is None:
        return None
    segments = SPLIT.split(raw.strip())[1:]
    head = [int(s.replace(" ", "")) for s in segments[: len(keys)]]
    groups = " ".join(segments[len(keys) :]).split()
    figures = dict(zip(keys, head, strict=True))
    count = CANDIDATES["2010-1"]
    # the leaves' own sums say which split is right; the printed total omits the Bafing region's one row
    expected = [sum(u.votes[i] for u in units) for i in range(count)]

    def splits(at: int, left: int) -> list[list[int]]:
        if left == 0:
            return [[]] if at == len(groups) else []
        found: list[list[int]] = []
        number = ""
        for end in range(at, len(groups)):
            if end > at and len(groups[end]) != 3:
                break
            number += groups[end]
            for rest in splits(end + 1, left - 1):
                found.append([int(number), *rest])
        return found

    options = [o for o in splits(0, count) if sum(o) == figures["exprimes"]]
    if not options:
        return None
    best = min(options, key=lambda o: sum(abs(a - b) for a, b in zip(o, expected, strict=True)))
    return Unit("", "TOTAL", figures, best)
