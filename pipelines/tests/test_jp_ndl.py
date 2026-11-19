import unittest
from collections import Counter

from ea.sources.jp import ndl


def test_place_counts_from_the_uncut_side() -> None:
    # Seven rows reach into the tile and the last is cut: a read of six is the whole rows, from the top.
    assert ndl._place([0, 1, 2, 3, 4, 5, 6], [0, 1, 2, 3, 4, 5], 6, uncut_first=True) == [0, 1, 2, 3, 4, 5]
    # A lower band whose top row is cut but whose figures show: a read of nine takes every row.
    assert ndl._place(list(range(4, 13)), list(range(5, 13)), 9, uncut_first=False) == list(range(4, 13))
    # A read that dropped rows does not fit.
    assert ndl._place([0, 1, 2, 3, 4, 5, 6], [0, 1, 2, 3, 4, 5], 3, uncut_first=True) is None


def test_spaced_drops_the_weaker_of_two_close_rules() -> None:
    rules = [(100, 110, 20), (300, 305, 4), (330, 340, 21), (560, 570, 20)]
    assert ndl._spaced(rules) == [(100, 110, 20), (330, 340, 21), (560, 570, 20)]


def _cell(row: int, column: int, *values: int) -> ndl.Cell:
    cell = ndl.Cell(17, "right", 0, row, column, (0, 0, 1, 1))
    cell.whole = [("right00", number + 1, value) for number, value in enumerate(values)]
    if len(set(values)) == 1 and len(values) > 1:
        cell.status, cell.value = "agree", values[0]
    elif len(set(values)) > 1:
        cell.status = "conflict"
    return cell


def _table(rows: int) -> list[ndl.Table]:
    return [ndl.Table("right", (0, 100), [(0, 100)], [(i, i + 1) for i in range(rows)], True)]


def test_sums_settle_a_conflict_and_catch_agreeing_misreads() -> None:
    # 男 + 女 = 計 settles the 女 cell the reads disagree on; two candidates and the vote 計 hold.
    cells = {
        (0, 0, 0): _cell(0, 0, 2102, 2102),
        (0, 1, 0): _cell(1, 0, 2257, 2267),
        (0, 2, 0): _cell(2, 0, 4359, 4359),
        (0, 3, 0): _cell(3, 0, 1278, 1278),
        (0, 4, 0): _cell(4, 0, 502, 502),
        (0, 5, 0): _cell(5, 0, 1780, 1780),
    }
    problems: list[str] = []
    ndl._sums(_table(6), cells, {}, set(), problems, 17)
    assert cells[(0, 1, 0)].status == "settled"
    assert cells[(0, 1, 0)].value == 2257
    assert {cells[key].status for key in [(0, 0, 0), (0, 2, 0), (0, 3, 0), (0, 5, 0)]} == {"checked"}
    assert problems == []
    # Both reads agree on 2,103 where the page prints 2,102: the sum fails and every cell in it is held back.
    cells = {
        (0, 0, 0): _cell(0, 0, 2103, 2103),
        (0, 1, 0): _cell(1, 0, 2257, 2257),
        (0, 2, 0): _cell(2, 0, 4359, 4359),
        (0, 3, 0): _cell(3, 0, 1278, 1278),
        (0, 4, 0): _cell(4, 0, 502, 502),
        (0, 5, 0): _cell(5, 0, 1780, 1780),
    }
    ndl._sums(_table(6), cells, {}, set(), problems, 17)
    assert {cells[(0, row, 0)].status for row in range(3)} == {"failed"}
    assert len(problems) == 1


def test_sums_check_a_county_total_column() -> None:
    table = ndl.Table("right", (0, 300), [(200, 300), (100, 200), (0, 100)], [(i, i + 1) for i in range(6)], True)
    # Each column's own sums hold, but the county 計 of 女 is one more than its two municipalities.
    values = [[10, 20, 30], [11, 21, 33], [21, 41, 63], [5, 6, 11], [3, 4, 7], [8, 10, 18]]
    cells = {
        (0, row, column): _cell(row, column, value, value)
        for row in range(6)
        for column, value in enumerate(values[row])
    }
    names = {(0, 0): Counter({"小湊町": 2}), (0, 1): Counter({"蟹田町": 2}), (0, 2): Counter({"計": 2})}
    problems: list[str] = []
    ndl._sums([table], cells, names, {(0, 0)}, problems, 17)
    assert problems == ["17 right table 0 row 1 column 2: 11+21 != 33", "17 right table 0 row 2 column 2: 21+41 != 63"]
    assert cells[(0, 0, 2)].status == "checked"
    assert cells[(0, 1, 2)].status == "failed"


class Reconcile(unittest.TestCase):
    def test_place(self) -> None:
        test_place_counts_from_the_uncut_side()

    def test_spaced(self) -> None:
        test_spaced_drops_the_weaker_of_two_close_rules()

    def test_sums(self) -> None:
        test_sums_settle_a_conflict_and_catch_agreeing_misreads()

    def test_county(self) -> None:
        test_sums_check_a_county_total_column()


if __name__ == "__main__":
    unittest.main()
