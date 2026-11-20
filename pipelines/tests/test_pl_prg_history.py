"""`pl.prg` follows gminas through GUGiK's yearly sets: a change of type keeps the gmina, an abolished gmina's land goes
to the gmina that covers it, and an outline gets a new period only where the gmina's area changes."""

import unittest
from datetime import date

import polars as pl
import shapely

from ea.sources.pl import prg


def square(x0: int, x1: int) -> str:
    return shapely.box(x0, 0, x1, 1).wkb_hex


def gminas(year: int | None, rows: list[tuple[str, str, int, int]]) -> pl.DataFrame:
    frame = pl.DataFrame(
        [(code, name, (x1 - x0) * 1e6, square(x0, x1), shapely.Point(x0, 0.5).wkb_hex) for code, name, x0, x1 in rows],
        schema={"code": pl.String, "name": pl.String, "area": pl.Float64, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    return frame if year is None else frame.with_columns(year=pl.lit(year, pl.Int32))


class History(unittest.TestCase):
    def setUp(self) -> None:
        before = [("3203042", "Ostrowice", 0, 1), ("3203012", "Drawsko Pomorskie", 1, 2), ("3203052", "Wieś", 2, 3)]
        recoded = [*before[:2], ("3203053", "Wieś", 2, 3)]
        after = [("3203012", "Drawsko Pomorskie", 0, 2), ("3203053", "Wieś", 2, 3)]
        vintages = pl.concat([gminas(2017, before), gminas(2018, recoded), gminas(2019, after)])
        self.gminas, self.events = prg._history(vintages, gminas(None, after))
        self.by = {gmina.code: gmina for gmina in self.gminas}

    def test_type_change_keeps_the_gmina(self) -> None:
        village = self.by["3203053"]
        self.assertEqual(
            [(p.code, p.start, p.end) for p in village.periods],
            [("3203052", None, date(2018, 1, 1)), ("3203053", date(2018, 1, 1), None)],
        )
        self.assertEqual(len(village.outlines), 1)

    def test_abolished_gmina_merges_into_its_heir(self) -> None:
        gone, heir = self.by["3203042"], self.by["3203012"]
        self.assertEqual(gone.end, date(2019, 1, 1))
        merged = [event for event in self.events if event.kind == "merged"]
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0].date, merged[0].before, merged[0].after), (date(2019, 1, 1), [gone, heir], [heir]))
        self.assertEqual(
            [(start, end) for *_, start, end in heir.outlines], [(None, date(2019, 1, 1)), (date(2019, 1, 1), None)]
        )

    def test_events(self) -> None:
        self.assertEqual(sorted(event.kind for event in self.events), ["merged", "recoded"])


if __name__ == "__main__":
    unittest.main()
