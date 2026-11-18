"""Readers for the Excel workbooks and tables the Mongolian General Election Commission publishes."""

import re
from collections.abc import Iterator

import fastexcel
import xlrd

from ea import raw

SOURCE = "mn.gec"
BASE = "https://e-election.mn/storage/uploads/process/"
EARLIER_BASE = "browser://20261116-225258-c41682/"


def url(name: str) -> str:
    """The cache key of a workbook, named by the first eight characters of its file name. Workbooks first cached from a
    browser session carry a browser key; those cached from the portal's own address carry that address."""
    keys = raw.keys(SOURCE)
    found = [key for key in keys if key.startswith(BASE) and key[len(BASE) :].startswith(name[:8])]
    if not found:
        found = [key for key in keys if key.startswith(EARLIER_BASE) and key[len(EARLIER_BASE) :].startswith(name[:8])]
    if len(found) != 1:
        raise LookupError(f"{name}: {found}")
    return found[0]


def sheet_rows(name: str, sheet: str | int | None = None) -> list[list[str]]:
    """One worksheet as rows of stripped strings; blank cells are empty strings."""
    path = raw.cached(SOURCE, url(name)).path
    if path.suffix == ".xls":
        book = xlrd.open_workbook(path)
        target = (
            book.sheet_by_index(0)
            if sheet is None
            else (book.sheet_by_name(sheet) if isinstance(sheet, str) else book.sheet_by_index(sheet))
        )
        return [[cell(v) for v in target.row_values(i)] for i in range(target.nrows)]
    book = fastexcel.read_excel(path)
    chosen = book.sheet_names[0] if sheet is None else (book.sheet_names[sheet] if isinstance(sheet, int) else sheet)
    frame = book.load_sheet_by_name(chosen, header_row=None, dtypes="string").to_polars()
    return [[cell(v) for v in row] for row in frame.iter_rows()]


def sheet_names(name: str) -> list[str]:
    path = raw.cached(SOURCE, url(name)).path
    if path.suffix == ".xls":
        return list(xlrd.open_workbook(path).sheet_names())
    return list(fastexcel.read_excel(path).sheet_names)


def cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value == int(value) else repr(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def number(value: str) -> int | None:
    text = value.replace(",", "").replace(" ", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def is_index(value: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.0+)?", value.strip()))


def records(rows: list[list[str]], first: int) -> Iterator[tuple[int, list[str]]]:
    for index, row in enumerate(rows[first:], start=first):
        if any(row):
            yield index, row
