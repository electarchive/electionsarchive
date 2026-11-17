"""Which seat-allocation district each reporting unit of a Belgian results file belongs to.

The federal election service's XML files list every tier of an election side by side, with no parent links. A unit
joins a district in one of two ways. Where the districts are electoral colleges, each list stands in one college, so
each of a unit's lists joins the college that prints the same list, and a Brussels canton is split between the Dutch
and French colleges. Elsewhere a unit stands in one district: the units are assigned whole, by an integer program that
makes each district's printed votes per list equal the sum of its units' votes as closely as the figures allow. A
district whose units then add up to more than a small fraction away from its printed votes takes no units from that
tier.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

# the largest share of a district's votes its units may differ from the district by and still be kept
TOLERANCE = 0.005
TIME_LIMIT = 20
# the largest share of a district's votes a finer, non-default set of its units may miss
PARTIAL = 0.05


@dataclass
class Unit:
    """One unit's lists, as (number, label, votes), with labels already folded, and the group of units it is assigned
    with, if any: units of one group always stand in the same district."""

    lists: list[tuple[str, str, int]]
    group: str | None = None

    @property
    def votes(self) -> Counter[str]:
        found: Counter[str] = Counter()
        for _, label, votes in self.lists:
            found[label] += votes
        return found


@dataclass
class Piece:
    """The part of a unit that belongs to one district: the indexes of its lists in the unit, or None for all."""

    unit: int
    lists: list[int] | None


def by_label(districts: Sequence[Unit], units: Sequence[Unit]) -> tuple[list[list[Piece]], int]:
    """Split each unit's lists among the districts that print the same label. A label that two districts print goes to
    the one the unit's other lists place it in. Returns the pieces of each district and the votes left unplaced."""
    where: dict[str, set[int]] = defaultdict(set)
    numbers: dict[str, set[int]] = defaultdict(set)
    for k, district in enumerate(districts):
        for nr, label, _ in district.lists:
            where[label].add(k)
            if nr:
                numbers[nr].add(k)
    pieces: list[list[Piece]] = [[] for _ in districts]
    lost = 0
    for i, unit in enumerate(units):
        pins = {next(iter(where[label])) for _, label, _ in unit.lists if len(where.get(label, ())) == 1}
        if not pins:
            common = set.intersection(*(where.get(label, set()) for _, label, _ in unit.lists)) if unit.lists else set()
            pins = common if len(common) == 1 else set()
        chosen: dict[int, list[int]] = defaultdict(list)
        for j, (nr, label, votes) in enumerate(unit.lists):
            found = where.get(label, set())
            if len(found) != 1:
                found = (found & pins) if found else (pins if len(pins) == 1 else pins & numbers.get(nr, set()))
            if len(found) == 1:
                chosen[next(iter(found))].append(j)
            else:
                lost += votes
        for k, lists in chosen.items():
            pieces[k].append(Piece(i, None if len(lists) == len(unit.lists) else lists))
    return pieces, lost


def whole(districts: Sequence[Unit], units: Sequence[Unit]) -> list[list[Piece]]:
    """Assign each unit whole to the district that makes the districts' votes per list fit best, keeping each group
    of units together."""
    members: dict[object, list[int]] = defaultdict(list)
    for i, unit in enumerate(units):
        members[unit.group if unit.group is not None else i].append(i)
    blocks = list(members.values())
    merged = [Unit([entry for i in block for entry in units[i].lists]) for block in blocks]
    pieces: list[list[Piece]] = [[] for _ in districts]
    for k, found in enumerate(_whole(districts, merged)):
        pieces[k] = [Piece(i, None) for piece in found for i in blocks[piece.unit]]
    return pieces


def _whole(districts: Sequence[Unit], units: Sequence[Unit]) -> list[list[Piece]]:
    want = [district.votes for district in districts]
    have = [unit.votes for unit in units]
    pairs: list[tuple[int, int]] = []
    for i, votes in enumerate(have):
        if not votes:
            continue
        fits = [k for k, district in enumerate(want) if set(votes) <= set(district)]
        if not fits:
            best = max(len(set(votes) & set(district)) for district in want)
            fits = [k for k, district in enumerate(want) if len(set(votes) & set(district)) == best]
        pairs += [(i, k) for k in fits]
    if not pairs:
        return [[] for _ in districts]
    placed = sorted({i for i, _ in pairs})
    row = {i: n for n, i in enumerate(placed)}
    keys = sorted(
        {(k, label) for k, votes in enumerate(want) for label in votes}
        | {(k, label) for i, k in pairs for label in have[i]}
    )
    column = {key: n for n, key in enumerate(keys)}
    nx, ne = len(pairs), len(keys)
    matrix = lil_matrix((len(placed) + ne, nx + 2 * ne))
    low = numpy.zeros(len(placed) + ne)
    high = numpy.zeros(len(placed) + ne)
    for n, (i, k) in enumerate(pairs):
        matrix[row[i], n] = 1
        for label, votes in have[i].items():
            matrix[len(placed) + column[(k, label)], n] = votes
    for m, (k, label) in enumerate(keys):
        r = len(placed) + m
        matrix[r, nx + m] = -1
        matrix[r, nx + ne + m] = 1
        low[r] = high[r] = want[k].get(label, 0)
    low[: len(placed)] = 1
    high[: len(placed)] = 1
    result = milp(
        numpy.r_[numpy.zeros(nx), numpy.ones(2 * ne)],
        constraints=LinearConstraint(matrix.tocsr(), low, high),
        integrality=numpy.r_[numpy.ones(nx), numpy.zeros(2 * ne)],
        bounds=Bounds(numpy.zeros(nx + 2 * ne), numpy.r_[numpy.ones(nx), numpy.full(2 * ne, numpy.inf)]),
        options={"time_limit": TIME_LIMIT},
    )
    pieces: list[list[Piece]] = [[] for _ in districts]
    if result.x is None:
        return pieces
    for n, (i, k) in enumerate(pairs):
        if result.x[n] > 0.5:
            pieces[k].append(Piece(i, None))
    return pieces


def gap(district: Unit, units: Sequence[Unit], pieces: list[Piece]) -> tuple[int, int]:
    """How many votes per list the pieces hold beyond the district's, and how many they fall short of it. A list whose
    label the district does not print counts as the district's list with the same number."""
    want = district.votes
    numbered = {nr: label for nr, label, _ in district.lists if nr}
    got: Counter[str] = Counter()
    for piece in pieces:
        lists = units[piece.unit].lists
        for j in range(len(lists)) if piece.lists is None else piece.lists:
            nr, label, votes = lists[j]
            got[label if label in want else numbered.get(nr, label)] += votes
    labels = set(got) | set(want)
    return (
        sum(max(got[label] - want[label], 0) for label in labels),
        sum(max(want[label] - got[label], 0) for label in labels),
    )


def assign(districts: Sequence[Unit], units: Sequence[Unit], split: bool) -> list[tuple[list[Piece], bool] | None]:
    """The pieces of each district, and whether they add up to it; a district whose pieces hold votes it does not
    print, or miss more than a twentieth of its votes (the votes cast abroad or by mail that only some tiers hold),
    gets None."""
    pieces = by_label(districts, units)[0] if split else whole(districts, units)
    kept: list[tuple[list[Piece], bool] | None] = []
    for district, found in zip(districts, pieces, strict=True):
        total = max(sum(district.votes.values()), 1)
        over, short = gap(district, units, found) if found else (total, total)
        if over + short <= TOLERANCE * total:
            kept.append((found, True))
        elif over <= TOLERANCE * total and short <= PARTIAL * total:
            kept.append((found, False))
        else:
            kept.append(None)
    return kept
