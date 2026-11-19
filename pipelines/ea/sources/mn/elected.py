"""Who the Commission declared elected: the elected-member lists and the elected-representatives tables it publishes
on its open-data portal, read as declared winners to attach to the vote tables."""

import csv
import io
import zipfile
from dataclasses import dataclass

import fastexcel

from ea import raw
from ea.sources.mn import local, sheets

SOURCE = "mn.gec"
LEGACY_FILES = "browser://20261119-020132-55e100/opendata-gov-mn-pkg103-legacy-original-files.zip"
COUNCIL_TABLES = "https://opendata.gov.mn/apiv1/dataset-group/103/download-zip?fileType=csv"
MEMBERS_2024 = "9653/УИХ-ын 2024 оны сонгууль.xlsx"
MEMBERS_2020 = "9653/УИХ-ын 2020 оны сонгууль — Сонгогдсон нэр дэвшигчийн судалгаа УИХ 2020.xlsx"
LIST = "Жагсаалт"


@dataclass(frozen=True)
class Member2024:
    party: str
    constituency: int | None
    patronymic: str
    given: str


@dataclass(frozen=True)
class Member2020:
    constituency: int
    votes: int
    party: str


@dataclass(frozen=True)
class Councillor:
    council: str
    constituency: int
    name: str
    party: str
    votes: int


def _sheet(member: str) -> list[list[str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, LEGACY_FILES).path) as archive:
        book = fastexcel.read_excel(archive.read(member))
    frame = book.load_sheet_by_idx(0, header_row=None, dtypes="string").to_polars()
    return [[sheets.cell(v) for v in row] for row in frame.iter_rows()]


def members_2024() -> list[Member2024]:
    """The 126 members of the 2024 State Great Khural, from the Commission's study of the elected members dated
    1 July 2024: each with a party, a constituency number or "Жагсаалт" for the party list, and a name."""
    out = []
    for row in _sheet(MEMBERS_2024):
        if not sheets.is_index(row[0]) or not row[1]:
            continue
        number = None if row[2] == LIST else int(float(row[2]))
        out.append(Member2024(row[1].strip(), number, row[3], row[4]))
    return out


def members_2020() -> list[Member2020]:
    """The 76 members elected in 2020, from the Commission's list of elected candidates dated 30 June 2020."""
    out = []
    for row in _sheet(MEMBERS_2020):
        if sheets.is_index(row[0]) and row[1].strip().endswith("тойрог"):
            out.append(Member2020(int(row[1].split()[0]), int(float(row[4])), row[6]))
    return out


def councillors_2024() -> list[Councillor]:
    """The 8,031 council members elected in 2024, from the Commission's two tables of elected representatives. The
    tables print a repeated place as "null"; it takes the value above it."""
    out = []
    with zipfile.ZipFile(raw.cached(SOURCE, COUNCIL_TABLES).path) as archive:
        for name in sorted(archive.namelist()):
            capital_or_aimag = "аймаг" in name
            previous = ["", "", ""]
            for row in list(csv.reader(io.StringIO(archive.read(name).decode("utf-8-sig"))))[1:]:
                cells = [last if cell == "null" else cell for cell, last in zip(row[2:5], previous, strict=True)]
                previous = cells
                aimag, soum, number = cells
                if capital_or_aimag:
                    council = (
                        local.Council("capital", None, None)
                        if aimag == "Улаанбаатар"
                        else local.Council("aimag", aimag, None)
                    )
                else:
                    council = (
                        local.Council("district", None, soum)
                        if aimag == "Улаанбаатар"
                        else local.Council("soum", aimag, soum)
                    )
                out.append(Councillor(council.key, int(number), row[6], row[5], int(row[9])))
    return out
