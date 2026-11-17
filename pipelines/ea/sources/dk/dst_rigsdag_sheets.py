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


def number(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def constituencies(path: Path) -> list[Constituency]:
    """Read a single-member transcription (records: constituency, candidate, line)."""
    found: list[Constituency] = []
    for count, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
        if len(elected) != 1:
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
        if c.voted is not None and c.voted not in (votes + others, votes + returned):
            problems.append(f"{name}: candidates {votes} + lines {others} (returned {returned}) != voted {c.voted}")
        top = max(c.candidates, key=lambda x: x.votes or 0)
        if elected and (top.votes or 0) > (elected[0].votes or 0):
            problems.append(f"{name}: the candidate marked elected does not have the most votes")
    return problems


if __name__ == "__main__":
    for name in sys.argv[1:]:
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
