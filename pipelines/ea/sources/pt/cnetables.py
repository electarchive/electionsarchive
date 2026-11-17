"""Results tables of the National Elections Commission for the 1999, 2002, and 2005 legislative elections.

The Ministry's results sites for these three elections print only the counts at home. The National Elections Commission
(CNE) publishes for each of them a table of the results by electoral circle, one workbook per election in a zip, which
carries the Europe and Outside Europe circles (two seats each) that the sites leave out. The tables give each circle's
registered voters, ballots cast, blank and invalid ballots, and for each list its votes and seats.
"""

import re
import zipfile
from dataclasses import dataclass
from datetime import date
from io import BytesIO

import fastexcel

from ea import raw

SOURCE = "pt.cne"
HOST = "https://www.cne.pt/sites/default/files/dl/eleicoes"
DAYS = {1999: date(1999, 10, 10), 2002: date(2002, 3, 17), 2005: date(2005, 2, 20)}
CIRCLES = {"21": "europa", "22": "fora-da-europa"}
ACCOUNTING = ("registered", "cast", "blank", "invalid")


@dataclass(frozen=True)
class Circle:
    registered: int
    cast: int
    blank: int
    invalid: int
    lines: tuple[tuple[str, int, int], ...]  # list label, votes, seats

    @property
    def seats(self) -> int:
        return sum(seats for _, _, seats in self.lines)


def url(year: int) -> str:
    return f"{HOST}/{year}_ar/docs_geral/{year}_ar_quadro_resultados.zip"


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".zip")


def artifact(year: int) -> raw.Artifact:
    return raw.cached(SOURCE, url(year))


def _number(cell: object) -> int | None:
    text = "" if cell is None else str(cell).strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def _rows(year: int) -> list[tuple[object, ...]]:
    with zipfile.ZipFile(artifact(year).path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".xlsx"))
        book = fastexcel.read_excel(BytesIO(archive.read(name)).getvalue())
    return book.load_sheet_by_idx(0, header_row=None).to_polars().rows()


def table(year: int) -> tuple[dict[str, Circle], Circle]:
    """Every circle's figures by its CNE number, and the printed national totals."""
    rows = _rows(year)
    head = next(row for row in rows if row[0] == "Círculos Eleitorais")
    parties = [(index, str(cell).strip()) for index, cell in enumerate(head) if index >= 9 and cell]

    def read(row: tuple[object, ...]) -> Circle:
        figures = [_number(row[i]) for i in (2, 3, 5, 7)]
        if None in figures:
            raise ValueError(f"{year}: incomplete figures in {row[:8]}")
        lines = []
        for index, label in parties:
            votes = _number(row[index])
            if votes is not None:
                lines.append((label, votes, _number(row[index + 2]) or 0))
        registered, cast, blank, invalid = (n or 0 for n in figures)
        return Circle(registered, cast, blank, invalid, tuple(lines))

    circles = {str(row[0]): read(row) for row in rows if str(row[0]).isdigit() and 1 <= int(str(row[0])) <= 22}
    if len(circles) != 22:
        raise ValueError(f"{year}: {len(circles)} circles")
    return circles, read(next(row for row in rows if row[0] == "Totais"))


def abroad(year: int) -> dict[str, Circle]:
    """The Europe and Outside Europe circles, by the circle codes the geo package uses."""
    circles, _ = table(year)
    return {slug: circles[number] for number, slug in CIRCLES.items()}
