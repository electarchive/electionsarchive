"""Transcribed vote tables from Statistics Denmark's historical election volumes, and their arithmetic checks.

Each table is a tab-separated file in `dst_rigsdag/`, transcribed from the page images of one volume. Records:

    columns   <page>  <column header>...  Tilsammen       the header of the table on that page
    block     <page>  <name>                              an amtskreds or storkreds heading
    row       <page>  <number>  <name>  <value>...        one nomination district (or constituency)
    subtotal  <page>  <label>  <value>...                 the printed "Tilsammen" row of the block
    total     <page>  <label>  <value>...                 a printed area or national total row

A dash in the table is written 0. Values follow the columns of the latest `columns` record, and the last
column is the row's own printed total.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

FOLDER = Path(__file__).with_name("dst_rigsdag")


@dataclass
class Row:
    page: int
    number: str
    name: str
    columns: tuple[str, ...]
    values: list[int]

    def by_column(self) -> dict[str, int]:
        return dict(zip(self.columns, self.values, strict=True))


@dataclass
class Block:
    page: int
    name: str
    rows: list[Row] = field(default_factory=list)
    subtotal: Row | None = None


@dataclass
class Table:
    path: Path
    blocks: list[Block] = field(default_factory=list)
    totals: list[Row] = field(default_factory=list)
    # each total's position: the number of blocks printed before it
    total_after: list[int] = field(default_factory=list)


def read(path: Path) -> Table:
    table = Table(path)
    columns: tuple[str, ...] = ()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        kind, page = parts[0], int(parts[1])
        where = f"{path.name}:{number}"
        if kind == "columns":
            columns = tuple(parts[2:])
            continue
        if kind == "block":
            table.blocks.append(Block(page, parts[2]))
            continue
        if kind == "row":
            label_number, name, cells = parts[2], parts[3], parts[4:]
        elif kind in ("subtotal", "total"):
            label_number, name, cells = "", parts[2], parts[3:]
        else:
            raise ValueError(f"{where}: unknown record {kind!r}")
        if len(cells) != len(columns):
            raise ValueError(f"{where}: {len(cells)} values for {len(columns)} columns")
        row = Row(page, label_number, name, columns, [int(c) for c in cells])
        if kind == "row":
            if not table.blocks:
                raise ValueError(f"{where}: a row before any block")
            table.blocks[-1].rows.append(row)
        elif kind == "subtotal":
            table.blocks[-1].subtotal = row
        else:
            table.totals.append(row)
            table.total_after.append(len(table.blocks))
    return table


def add(rows: list[Row]) -> dict[str, int]:
    found: dict[str, int] = {}
    for row in rows:
        for column, value in row.by_column().items():
            found[column] = found.get(column, 0) + value
    return found


def same(a: dict[str, int], b: dict[str, int]) -> list[str]:
    return [
        f"{k}: {a.get(k, 0)} != {b.get(k, 0)}" for k in sorted(set(a) | set(b), key=str) if a.get(k, 0) != b.get(k, 0)
    ]


def problems(table: Table) -> list[str]:
    """Every arithmetic disagreement in a transcribed table."""
    found: list[str] = []
    every = [r for b in table.blocks for r in b.rows + ([b.subtotal] if b.subtotal else [])] + table.totals
    for row in every:
        if sum(row.values[:-1]) != row.values[-1]:
            found.append(
                f"p{row.page} {row.number} {row.name}: parts add to {sum(row.values[:-1])}, total {row.values[-1]}"
            )
    for block in table.blocks:
        if block.subtotal is not None:
            for text in same(add(block.rows), block.subtotal.by_column()):
                found.append(f"p{block.page} block {block.name}: {text}")
        elif len(block.rows) > 1:
            found.append(f"p{block.page} block {block.name}: no subtotal")
    # an area total sums the blocks since the previous area total; a national total sums the area totals
    start = 0
    areas: list[Row] = []
    for total, after in zip(table.totals, table.total_after, strict=True):
        blocks = table.blocks[start:after]
        if blocks:
            parts = [b.subtotal or b.rows[0] for b in blocks]
            for text in same(add(parts), total.by_column()):
                found.append(f"p{total.page} {total.name}: {text}")
            areas.append(total)
        else:
            for text in same(add(areas), total.by_column()):
                found.append(f"p{total.page} {total.name} (sum of areas): {text}")
        start = after
    return found


@dataclass
class Candidate:
    page: int
    name: str
    title: str
    party: str
    residence: str
    votes: int | None
    incumbent: bool
    elected: bool


@dataclass
class Constituency:
    """One single-member constituency's result at a general election or a by-election."""

    page: int
    day: str
    kind: str  # general or by
    county: str
    number: str
    seat: str
    registered: int | None
    voted: int | None
    result: str  # poll or unopposed
    candidates: list[Candidate] = field(default_factory=list)
    lines: list[tuple[str, int]] = field(default_factory=list)  # other printed ballot lines, such as invalid
    misprint: str = ""  # the transcriber's note where the printed figures themselves do not add up


def number(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def constituencies(path: Path) -> list[Constituency]:
    """Read a single-member transcription (records: constituency, candidate, line)."""
    found: list[Constituency] = []
    for count, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("# misprint") and found:
            found[-1].misprint = line.removeprefix("# misprint:").strip()
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        kind, page = parts[0], int(parts[1])
        where = f"{path.name}:{count}"
        if kind == "constituency":
            if len(parts) != 10:
                raise ValueError(f"{where}: constituency needs 10 fields, has {len(parts)}")
            day, sort, county, kreds, seat, registered, voted, result = parts[2:10]
            if sort not in ("general", "by") or result not in ("poll", "unopposed"):
                raise ValueError(f"{where}: bad kind or result")
            found.append(Constituency(page, day, sort, county, kreds, seat, number(registered), number(voted), result))
        elif kind == "candidate":
            if len(parts) != 9:
                raise ValueError(f"{where}: candidate needs 9 fields, has {len(parts)}")
            name, title, party, residence, votes, incumbent, elected = parts[2:9]
            found[-1].candidates.append(
                Candidate(page, name, title, party, residence, number(votes), incumbent == "1", elected == "1")
            )
        elif kind == "line":
            found[-1].lines.append((parts[2], int(parts[3])))
        else:
            raise ValueError(f"{where}: unknown record {kind!r}")
    return found


def single_problems(found: list[Constituency]) -> list[str]:
    problems: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for c in found:
        name = f"p{c.page} {c.day} {c.county} {c.number}"
        key = (c.day, c.county, c.number)
        if key in seen:
            problems.append(f"{name}: listed twice")
        seen.add(key)
        elected = [x for x in c.candidates if x.elected]
        against = sum(v for label, v in c.lines if label.lower().startswith("nej"))
        rejected = len(c.candidates) == 1 and against > (c.candidates[0].votes or 0)
        if len(elected) != (0 if rejected else 1):
            problems.append(f"{name}: {len(elected)} candidates marked elected")
        if c.result == "unopposed":
            if len(c.candidates) != 1:
                problems.append(f"{name}: unopposed with {len(c.candidates)} candidates")
            continue
        if any(x.votes is None for x in c.candidates):
            problems.append(f"{name}: a candidate without votes in a poll")
            continue
        votes = sum(x.votes or 0 for x in c.candidates)
        others = sum(v for _, v in c.lines)
        returned = sum(v for label, v in c.lines if not label.lower().startswith("udlev"))
        if not c.misprint and c.voted is not None and c.voted not in (votes + others, votes + returned):
            problems.append(f"{name}: candidates {votes} + lines {others} (returned {returned}) != voted {c.voted}")
        top = max(c.candidates, key=lambda x: x.votes or 0)
        if elected and (top.votes or 0) > (elected[0].votes or 0):
            problems.append(f"{name}: the candidate marked elected does not have the most votes")
    return problems


@dataclass
class District:
    """One polling district of the capital's list election, with its turnout and each list's votes."""

    page: int
    number: str
    name: str
    inhabitants: int
    stations: int
    registered: int
    valid: int
    invalid: int
    cast: int
    votes: list[int]


@dataclass
class CapitalBlock:
    page: int
    name: str
    districts: list[District] = field(default_factory=list)
    subtotal: District | None = None


@dataclass
class Capital:
    """A list election in the capital: lists (letter, name, seats won), blocks of districts, and the printed total."""

    lists: list[tuple[str, str, int]] = field(default_factory=list)
    blocks: list[CapitalBlock] = field(default_factory=list)
    total: District | None = None


def capital(path: Path) -> Capital:
    """Read a capital list-election transcription (records: list, block, row, subtotal, total)."""
    found = Capital()
    for count, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        kind, page = parts[0], int(parts[1])
        if kind == "list":
            found.lists.append((parts[2], parts[3], int(parts[4])))
        elif kind == "block":
            found.blocks.append(CapitalBlock(page, parts[2]))
        elif kind in ("row", "subtotal", "total"):
            numbers = [int(x) for x in parts[4:]]
            if len(numbers) != 6 + len(found.lists):
                raise ValueError(f"{path.name}:{count}: {len(numbers)} figures")
            row = District(page, parts[2], parts[3], *numbers[:6], numbers[6:])
            if kind == "row":
                found.blocks[-1].districts.append(row)
            elif kind == "subtotal":
                found.blocks[-1].subtotal = row
            else:
                found.total = row
        else:
            raise ValueError(f"{path.name}:{count}: unknown record {kind!r}")
    return found


def figures(d: District) -> list[int]:
    return [d.inhabitants, d.stations, d.registered, d.valid, d.invalid, d.cast, *d.votes]


def capital_problems(c: Capital) -> list[str]:
    found: list[str] = []
    every = [d for b in c.blocks for d in [*b.districts, *([b.subtotal] if b.subtotal else [])]]
    for d in [*every, *([c.total] if c.total else [])]:
        if sum(d.votes) != d.valid:
            found.append(f"p{d.page} {d.name}: lists add to {sum(d.votes)}, valid {d.valid}")
        if d.valid + d.invalid != d.cast:
            found.append(f"p{d.page} {d.name}: valid and invalid add to {d.valid + d.invalid}, cast {d.cast}")
    for b in c.blocks:
        if b.subtotal is not None:
            mine = [sum(x) for x in zip(*(figures(d) for d in b.districts), strict=True)]
            for k, (a, p) in enumerate(zip(mine, figures(b.subtotal), strict=True)):
                if a != p:
                    found.append(f"p{b.page} block {b.name}: figure {k} adds to {a}, printed {p}")
    if c.total is not None:
        whole = [sum(x) for x in zip(*(figures(b.subtotal) for b in c.blocks if b.subtotal), strict=True)]
        for k, (a, p) in enumerate(zip(whole, figures(c.total), strict=True)):
            if a != p:
                found.append(f"total: figure {k} adds to {a}, printed {p}")
    return found


@dataclass
class Turnout:
    """One nomination district's registered voters, ballots cast, and invalid ballots from the turnout table."""

    page: int
    storkreds: str
    number: str
    name: str
    stations: int
    inhabitants: int
    registered: int
    voted: int
    invalid: int


def turnout(path: Path) -> list[Turnout]:
    """Read a turnout transcription (records: kreds, total)."""
    found: list[Turnout] = []
    for count, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if parts[0] == "kreds":
            if len(parts) != 10:
                raise ValueError(f"{path.name}:{count}: kreds needs 10 fields, has {len(parts)}")
            found.append(Turnout(int(parts[1]), parts[2], parts[3], parts[4], *(int(x) for x in parts[5:])))
        elif parts[0] != "total":
            raise ValueError(f"{path.name}:{count}: unknown record {parts[0]!r}")
    return found


if __name__ == "__main__":
    for name in sys.argv[1:]:
        if Path(name).name.startswith("cph-"):
            cap = capital(Path(name))
            print(f"{name}: {len(cap.blocks)} blocks, {sum(len(b.districts) for b in cap.blocks)} districts")
            for text in capital_problems(cap):
                print("  " + text)
            continue
        if Path(name).name.startswith("tab-"):
            print(f"{name}: {len(turnout(Path(name)))} nomination districts")
            continue
        if Path(name).name.startswith("sm-"):
            found = constituencies(Path(name))
            general = sum(1 for c in found if c.kind == "general")
            print(f"{name}: {general} general-election constituencies, {len(found) - general} by-elections")
            for text in single_problems(found):
                print("  " + text)
            continue
        table = read(Path(name))
        found = problems(table)
        districts = sum(len(b.rows) for b in table.blocks)
        print(f"{name}: {len(table.blocks)} blocks, {districts} districts, {len(table.totals)} totals")
        for text in found:
            print("  " + text)
        if not found:
            print("  every row, block, and total adds up")
