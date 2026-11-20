import io
import os
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

import numpy as np
import polars as pl
import shapely
import xlrd
from pyproj import Transformer

from ea import boundaries, package, raw
from ea.changeset import Changeset, slug
from ea.names import display
from ea.package import Draft
from ea.sources.pl.kbw_sejm import ITEMS
from ea.store import Connection, connect

SOURCE = "pl.kbw_senat"
VERSION = "1"
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze: wybory do Senatu"
PAGE = "https://danewyborcze.kbw.gov.pl/"
BASE = "https://danewyborcze.kbw.gov.pl/dane"
PARSED = raw.parsed(SOURCE)
ELECTIONS = {
    1991: date(1991, 10, 27),
    1993: date(1993, 9, 19),
    1997: date(1997, 9, 21),
    2001: date(2001, 9, 23),
    2005: date(2005, 9, 25),
    2007: date(2007, 10, 21),
    2011: date(2011, 10, 9),
    2015: date(2015, 10, 25),
    2019: date(2019, 10, 13),
    2023: date(2023, 10, 15),
}
FILES: dict[int, dict[str, str | tuple[str, ...]]] = {
    1991: {"candidates": "senat/KANSEN1991.xls"},
    1993: {"candidates": "senat/KANSEN1993.xls", "unit_candidates": "senat/senat1993.zip"},
    1997: {
        "districts": "senat/senat1997okr.xls",
        "candidates": "senat/ksenat1997.xls",
        "support": "senat/ksenat1997pop.xls",
        "unit_candidates": "senat/obwsenat97.zip",
        "gmina_candidates": "senat/senat1997gminy.zip",
    },
    2001: {
        "districts": "senat/okr%c4%99gi2001senat.xls",
        "candidates": "senat/2001-kan-sen.xls",
        "unit_candidates": "senat/parl2001OBWSEN.zip",
    },
    2005: {
        "districts": "senat/senat2005okr%c4%99gi.xls",
        "totals": "senat/senat2005zbiorcze.xls",
        "candidates": "senat/kandsen2005.xls",
        "unit_candidates": "senat/2005-sen-gl_kan_pow.zip",
    },
    2007: {
        "districts": "senat/okr2007senat.xlsx",
        "candidates": "senat/kandsen2007.xls",
        "unit_candidates": "senat/parl2007-sen-gl_kand_obw.zip",
    },
    2011: {
        "districts": "sejmsenat/parl2011okr%c4%99gi.xlsx",
        "candidates": "sejmsenat/2011-kand-sen.zip",
        "unit_candidates": "sejmsenat/2011-senat-g%c5%82osy-w-obwodach.zip",
    },
    2015: {
        "candidates": "senat/2015-kand-sen.xls",
        "units": "obwody2015parl.xlsx",
        "unit_candidates": tuple(f"senat/wyniki_okr_senat/{district:03}.xlsx" for district in range(1, 101)),
    },
    2019: {
        "districts": "sejmsenat/okregi_senat_csv.zip",
        "candidates": "sejmsenat/kandydaci_senat_csv.zip",
        "units": "sejmsenat/obwody_glosowania_csv.zip",
        "unit_candidates": "sejmsenat/wyniki_gl_na_kand_po_obwodach_senat_csv.zip",
    },
    2023: {
        "districts": "sejmsenat/okregi_senat_csv.zip",
        "candidates": "sejmsenat/kandydaci_senat_csv.zip",
        "units": "sejmsenat/obwody_glosowania_csv.zip",
        "unit_candidates": "sejmsenat/wyniki_gl_na_kandydatow_po_obwodach_senat_xlsx.zip",
        "protocols": "sejmsenat/protokoly_po_obwodach_senat_csv.zip",
    },
}


def urls(year: int, kind: str) -> list[str]:
    files = FILES[year][kind]
    return [f"{BASE}/{year}/{file}" for file in ([files] if isinstance(files, str) else files)]


def arcgis(layer: str, fields: str) -> str:
    return f"{layer}/query?where=1%3D1&outFields={fields}&returnGeometry=true&outSR=4326&f=geojson"


class City(NamedTuple):
    """A city that constituencies split by its auxiliary units: its gmina, the city's own layer of those units, and
    the field that names each unit."""

    gmina: str
    url: str
    suffix: str
    field: str
    publisher: str
    title: str
    page: str
    license: str


NO_LICENSE = "Official public record, no license stated"
CITIES = {
    "Wrocław": City(
        "026401",
        "https://geoportal.wroclaw.pl/www/pliki/osiedla/granice-osiedli.zip",
        ".zip",
        "NAZWAOSIED",
        "Urząd Miejski Wrocławia",
        "Granice osiedli Wrocławia (uchwała Rady Miejskiej Wrocławia nr XX/419/16)",
        "https://geoportal.wroclaw.pl/zasoby/?zasob=granice_osiedla",
        "No restrictions on use or processing (brak ograniczeń), as the publisher states",
    ),
    "Łódź": City(
        "106101",
        arcgis("https://mapa.lodz.pl/3/rest/services/ObwodyWyborcze/MapServer/7", "NAME"),
        ".geojson",
        "NAME",
        "Urząd Miasta Łodzi",
        "Jednostki pomocnicze (osiedla) Łodzi, mapa obwodów wyborczych",
        "https://nowa.mapa.lodz.pl/",
        NO_LICENSE,
    ),
    "Kraków": City(
        "126101",
        arcgis("https://msip.um.krakow.pl/arcgis/rest/services/JP/dane_refenc/MapServer/9", "nr_dzielni,nazwa"),
        ".geojson",
        "nr_dzielni",
        "Urząd Miasta Krakowa, Miejski System Informacji Przestrzennej",
        "Dzielnice administracyjne Krakowa",
        "https://msip.krakow.pl/",
        NO_LICENSE,
    ),
}


def fetch() -> None:
    for year in ELECTIONS:
        for kind in FILES[year]:
            for address in urls(year, kind):
                raw.fetch(SOURCE, address, suffix=Path(address).suffix)
    for city in CITIES.values():
        raw.fetch(SOURCE, city.url, suffix=city.suffix)


def text(cell: xlrd.sheet.Cell) -> str | None:
    """A cell as `pl.read_excel` gives it as text: a number to at most 9 decimal places, without trailing zeros."""
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return f"{cell.value:.9f}".rstrip("0").rstrip(".")
    return str(cell.value)


def legacy(body: bytes) -> dict[str, pl.DataFrame]:
    """Each sheet of an Excel 2-4 workbook, which `pl.read_excel` cannot open, read with xlrd into the frame
    `pl.read_excel(..., infer_schema_length=0)` gives for a newer one: the used range, its first row as the header
    (a blank header named `__UNNAMED__<column>`, a repeated one suffixed `_<n>`), its rows that hold a value, and
    text cells."""
    frames = {}
    book = xlrd.open_workbook(file_contents=body)
    for sheet in book.sheets():
        grid = [[text(sheet.cell(row, col)) for col in range(sheet.ncols)] for row in range(sheet.nrows)]
        used = [(row, col) for row, cells in enumerate(grid) for col, cell in enumerate(cells) if cell is not None]
        if not used:
            continue
        top, bottom = min(row for row, _ in used), max(row for row, _ in used)
        left, right = min(col for _, col in used), max(col for _, col in used)
        rows = [cells[left : right + 1] for cells in grid[top : bottom + 1]]
        header: list[str] = []
        seen: Counter[str] = Counter()
        for col, cell in enumerate(rows[0]):
            label = f"__UNNAMED__{col}" if cell is None else cell
            header.append(label if not seen[label] else f"{label}_{seen[label]}")
            seen[label] += 1
        body_rows = [cells for cells in rows[1:] if any(cell is not None for cell in cells)]
        frames[sheet.name] = pl.DataFrame(body_rows, schema=dict.fromkeys(header, pl.String), orient="row")
    return frames


def sheets(body: bytes) -> dict[str, pl.DataFrame]:
    if body.startswith((b"PK", b"\xd0\xcf\x11\xe0")):
        return pl.read_excel(io.BytesIO(body), sheet_id=0, infer_schema_length=0)
    return legacy(body)


def decoded(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("cp1250")


def clean(frame: pl.DataFrame) -> pl.DataFrame:
    if sum(column.startswith("__UNNAMED__") for column in frame.columns) > len(frame.columns) / 2:
        filled = frame.select(pl.sum_horizontal(pl.all().is_not_null())).to_series()
        start = next(i for i, width in enumerate(filled) if width > len(frame.columns) / 2)
        header = [str(value or f"__blank__{i}").strip() for i, value in enumerate(frame.row(start))]
        frame = frame.slice(start + 1).rename(dict(zip(frame.columns, header)))
    frame = frame.rename({column: column.lstrip("\ufeff") for column in frame.columns})
    return frame.with_columns(pl.col(pl.String).str.strip_chars().replace("", None))


def tables(year: int, kind: str) -> dict[str, pl.DataFrame]:
    frames = {}
    for address in urls(year, kind):
        path = raw.cached(SOURCE, address).path
        if path.suffix != ".zip":
            members = {Path(address).name: path.read_bytes()}
        else:
            archive = zipfile.ZipFile(path)
            members = {member: archive.read(member) for member in archive.namelist()}
        for member, body in members.items():
            if member.lower().endswith(".csv"):
                lines = decoded(body).splitlines()
                start = next(i for i, line in enumerate(lines) if line.count(";") >= 3)
                text = "\n".join(lines[start:])
                formulas = '"=""' in text
                frame = pl.read_csv(
                    io.StringIO(text.replace('"=""', "").replace('"""', "") if formulas else text),
                    separator=";",
                    infer_schema_length=0,
                    truncate_ragged_lines=True,
                    quote_char=None if formulas else '"',
                )
                if formulas:
                    frame = frame.rename({column: column.strip('"') for column in frame.columns})
                    frame = frame.with_columns(pl.col(pl.String).str.strip_chars('"'))
                frames[member] = clean(frame.select(column for column in frame.columns if column.strip()))
            elif member.lower().endswith((".xls", ".xlsx")):
                if member.endswith(".XLS") and member[:-4] + ".xlsx" in members:
                    continue
                for sheet, frame in sheets(body).items():
                    frames[f"{member}:{sheet}"] = clean(frame)
    return frames


def count(column: str) -> pl.Expr:
    return pl.col(column).str.replace_all(r"\s", "").replace(["-", "x", "X", "XXXXX", "Skreślony"], None).cast(pl.Int64)


def column(frame: pl.DataFrame, *names: str) -> str:
    for wanted in names:
        for header in frame.columns:
            if re.sub(r"^Senat - ", "", header).lower() == wanted.lower():
                return header
    raise KeyError(f"none of {names} in {frame.columns}")


ELECTORS = (
    "Liczba wyborców uprawnionych do głosowania",
    "Uprawnieni do głosowania",
    "L. uprawnionych",
    "Upr.",
    "Upr",
)
CAST = ("Liczba kart wyjętych z urny", "Karty wyjęte z urny", "L. kart wyjętych z urny", "Odd.", "Odd")
VALID = (
    "Liczba głosów ważnych oddanych łącznie na wszystkich kandydatów",
    "Liczba głosów ważnych",
    "Karty ważne z głosami ważnymi",
    "Głosy ważne",
    "Ważne",
)
COMMISSION = ("Nr komisji", "Numer", "Numer obwodu", "Nr obwodu głosowania", "Nr obwodu", "Nr obw.")
TERYT = ("TERYT Gminy", "Kod TERYT", "TERYT gminy", "Kod terytorialny gminy", "TERYT", "Kod gminy", "Kod gm.")


def lookup(frame: pl.DataFrame, names: tuple[str, ...]) -> str | None:
    try:
        return column(frame, *names)
    except KeyError:
        return None


def maybe(frame: pl.DataFrame, names: tuple[str, ...]) -> pl.Expr:
    try:
        return count(column(frame, *names))
    except KeyError:
        return pl.lit(None, pl.Int64)


def tokens(text: str) -> tuple[str, ...]:
    text = re.sub(r"^\d+\.\s*|\s*\([A-Z]\)$", "", text.split(" - ", 1)[0])
    return tuple(sorted(re.sub(r"[^\w-]", " ", text).upper().split()))


def place(frame: pl.DataFrame, old: bool = False) -> pl.Expr:
    code = pl.col(column(frame, *TERYT))
    if old:
        return pl.when(code.is_null()).then(pl.lit("zagranica")).otherwise(code.str.zfill(5)).alias("gmina")
    return (
        pl.when(code.is_null() | (code == "149901"))
        .then(pl.lit("zagranica"))
        .when(code == "229901")
        .then(pl.lit("statki"))
        .otherwise(code.str.zfill(6))
        .alias("gmina")
    )


def polling(
    year: int, district: int, frame: pl.DataFrame, ballot: pl.DataFrame, first: int
) -> tuple[pl.DataFrame, pl.DataFrame]:
    headers = [
        header
        for header in frame.columns[first:]
        if header != "Razem" and not header.startswith(("__blank__", "__UNNAMED__"))
    ]
    people = [
        (set(tokens(name)), number, votes)
        for name, number, votes in ballot.select("name", "number", "votes").iter_rows()
    ]
    numbers = []
    for header in list(headers):
        wanted = set(tokens(re.sub(r"_duplicated_\d+$", "", header)))
        total = frame.select(count(header).sum()).item()
        matches = [(number, votes) for names, number, votes in people if wanted <= names and number not in numbers]
        if len(matches) > 1:
            matches = [match for match in matches if match[1] == total] or matches
        if not matches:
            if total:
                raise ValueError(f"{year} district {district}: column {header!r} matches no candidate")
            headers.remove(header)
            continue
        numbers.append(matches[0][0])
    if sorted(numbers) != sorted(ballot["number"]):
        raise ValueError(f"{year} district {district}: {len(numbers)} columns for {ballot.height} candidates")
    units = (
        frame.filter(pl.col(column(frame, *COMMISSION)).is_not_null() | pl.col(column(frame, *TERYT)).is_not_null())
        .select(
            place(frame, year < 1999),
            pl.col(column(frame, *COMMISSION)).fill_null("combined").alias("commission"),
            *(
                maybe(frame, names).alias(label)
                for label, names in (("electors", ELECTORS), ("cast", CAST), ("valid", VALID))
            ),
            *(count(header).alias(str(number)) for header, number in zip(headers, numbers)),
        )
        .with_columns(district=pl.lit(district, pl.Int64))
    )
    votes = (
        units.drop("electors", "cast", "valid")
        .unpivot(index=["district", "gmina", "commission"], variable_name="number", value_name="votes")
        .with_columns(pl.col("number").cast(pl.Int64))
        .filter(pl.col("votes").is_not_null())
    )
    return units.select("district", "gmina", "commission", "electors", "cast", "valid"), votes


def ballots(
    frame: pl.DataFrame, district: str, name: pl.Expr, committee: str, sex: str, votes: str, elected: pl.Expr
) -> pl.DataFrame:
    return (
        frame.filter(pl.col(district).str.contains(r"^\d+$"))
        .select(
            count(district).alias("district"),
            name.str.replace_all(r"\s+", " ").alias("name"),
            pl.col(committee).alias("committee"),
            pl.col(sex).replace({"M": "male", "K": "female", "Mężczyzna": "male", "Kobieta": "female"}).alias("sex"),
            count(votes).alias("votes"),
            elected.fill_null(False).alias("elected"),
        )
        .with_columns(number=pl.int_range(1, pl.len() + 1).over("district"))
    )


def load_recent(year: int) -> dict[str, pl.DataFrame]:
    [people] = tables(year, "candidates").values()
    if year >= 2019:
        name = (
            pl.concat_str("Imiona", "Nazwisko", separator=" ")
            if "Imiona" in people.columns
            else pl.col("Nazwisko i imiona")
        )
        candidates = ballots(
            people,
            "Nr okręgu",
            name,
            "Nazwa komitetu",
            "Płeć",
            "Liczba głosów",
            pl.col("Czy przyznano mandat") == "Tak",
        )
    elif year == 2015:
        candidates = ballots(
            people,
            "Nr okr.",
            pl.concat_str("Imiona", "Nazwisko", separator=" "),
            "Komitet",
            "Płeć",
            "Głosy",
            pl.col("Mandat") == "T",
        )
    else:
        candidates = ballots(
            people,
            "Okręg",
            pl.concat_str("Imiona", "Nazwisko", separator=" "),
            "Komitet",
            "Płeć",
            "Liczba głosów",
            pl.col("Wybrany") == "T",
        )
    if year >= 2019:
        [sizes] = tables(year, "districts").values()
        districts = sizes.select(
            count("Numer okręgu").alias("district"),
            count("Liczba mandatów").alias("magnitude")
            if "Liczba mandatów" in sizes.columns
            else pl.lit(1, pl.Int64).alias("magnitude"),
            pl.col("Siedziba OKW").alias("seat"),
            pl.col("Opis granic").alias("boundary"),
        )
    elif year == 2011:
        [sizes] = tables(year, "districts").values()
        districts = sizes.filter(pl.col("Izba") == "Senat").select(
            count("Nr okręgu").alias("district"),
            count("Liczba mandatów").alias("magnitude"),
            pl.lit(None, pl.String).alias("seat"),
            pl.col("Granice okręgu wyborczego").alias("boundary"),
        )
    else:
        districts = pl.DataFrame(
            {"district": range(1, 101), "magnitude": [1] * 100, "seat": [None] * 100, "boundary": [None] * 100},
            schema={"district": pl.Int64, "magnitude": pl.Int64, "seat": pl.String, "boundary": pl.String},
        )
    units, votes = [], []
    for member, frame in tables(year, "unit_candidates").items():
        district = int(
            re.findall(r"\d+", member.rsplit("/", 1)[-1].split(":")[-1] if year == 2023 else member)[
                -1 if year != 2015 else 0
            ]
        )
        if year == 2023:
            first = frame.columns.index("Liczba uwzględnionych komisji") + 1
        else:
            first = (
                frame.columns.index(column(frame, *VALID)) + 1
                if year != 2011
                else frame.columns.index(column(frame, "Liczba kopert na kartę do głosowania wrzuconych do urny")) + 1
            )
        unit, vote = polling(year, district, frame, candidates.filter(pl.col("district") == district), first)
        units.append(unit)
        votes.append(vote)
    units = pl.concat(units)
    if year == 2023:
        [protocols] = tables(year, "protocols").values()
        accounting = protocols.select(
            place(protocols),
            pl.col("Nr komisji").alias("commission"),
            count("Nr okręgu").alias("district"),
            count(ELECTORS[0]).alias("electors"),
            count(CAST[0]).alias("cast"),
            count(VALID[0]).alias("valid"),
        )
        units = units.select("district", "gmina", "commission").join(
            accounting, on=["district", "gmina", "commission"], how="left"
        )
    return {"districts": districts, "candidates": candidates, "units": units, "unit_votes": pl.concat(votes)}


OLD = {
    2007: ("Okręg", ("Imiona", "Nazwisko"), "Komitet wyborczy", "Płeć", "Głosy", "Siedziba"),
    2005: ("Okręg", ("Imiona", "Nazwisko"), "Komitet wyborczy", "Płeć", "Głosy", None),
    2001: ("Nr\nokr.", ("1. Imię", "2. Imię", "Nazwisko"), "Komitet wyborczy", "Płeć", "Głosy", None),
    1997: ("Nr woj.", ("1. imię", "2. imię", "Nazwisko"), "Komitet", "Płeć", "Głosy", "Województwo"),
    1993: ("Nr\nwoj.", ("Imiona", "Nazwisko"), "Komitet wyborczy", "Płeć", "Liczba\ngłosów", "Województwo"),
    1991: ("Nr\nwoj.", ("Imiona", "Nazwisko"), "Komitet wyborczy", "Płeć", "Liczba\ngłosów", "Województwo"),
}


def load_old(year: int) -> dict[str, pl.DataFrame]:
    district, parts, committee, sex, votes, seat = OLD[year]
    [people] = tables(year, "support" if year == 1997 else "candidates").values()
    people = people.filter(pl.col(district).is_not_null())
    name = pl.concat_str(*parts, separator=" ", ignore_nulls=True)
    candidates = ballots(people, district, name, committee, sex, votes, pl.col("Mandat") == "T")
    seats = people.filter(pl.col(district).str.contains(r"^\d+$")).select(
        count(district).alias("district"), (pl.col(seat) if seat else pl.lit(None, pl.String)).alias("seat")
    )
    districts = (
        candidates.group_by("district")
        .agg(pl.col("elected").sum().cast(pl.Int64).alias("magnitude"))
        .join(seats.unique("district", keep="first"), on="district")
        .with_columns(boundary=pl.lit(None, pl.String))
        .sort("district")
    )
    if year == 2001:
        [frame] = tables(year, "districts").values()
        named = frame.filter(pl.col("Nr\nokręgu").str.contains(r"^\d+$")).select(
            count("Nr\nokręgu").alias("district"),
            pl.col("Siedziba Okręgowej Komisji Wyborczej").alias("seat"),
            pl.col("Obszar okręgu wyborczego").alias("boundary"),
        )
        districts = districts.drop("seat", "boundary").join(named, on="district")
    units, votes = [], []
    kinds = {
        2005: "unit_candidates",
        2007: "unit_candidates",
        2001: "unit_candidates",
        1997: "unit_candidates",
        1993: "unit_candidates",
    }
    if year in kinds:
        for member, frame in tables(year, kinds[year]).items():
            stem = member.split(":")[0]
            number = int(re.findall(r"\d+", Path(stem).stem)[-1 if year != 2005 else 0])
            if year == 2005:
                frame = frame.filter(pl.col("TERYT").is_not_null()).select(pl.col("TERYT").alias("Nr obw."), pl.all())
            first = 1 + max(
                frame.columns.index(header)
                for header in (lookup(frame, names) for names in (ELECTORS, CAST, VALID))
                if header
            )
            if year == 2005:
                first = frame.columns.index("Razem na kandydatów") + 1
            unit, vote = polling(year, number, frame, candidates.filter(pl.col("district") == number), first)
            units.append(unit)
            votes.append(vote)
    empty_units = pl.DataFrame(
        schema={
            "district": pl.Int64,
            "gmina": pl.String,
            "commission": pl.String,
            "electors": pl.Int64,
            "cast": pl.Int64,
            "valid": pl.Int64,
        }
    )
    empty_votes = pl.DataFrame(
        schema={
            "district": pl.Int64,
            "gmina": pl.String,
            "commission": pl.String,
            "number": pl.Int64,
            "votes": pl.Int64,
        }
    )
    return {
        "districts": districts,
        "candidates": candidates,
        "units": pl.concat(units) if units else empty_units,
        "unit_votes": pl.concat(votes) if votes else empty_votes,
    }


LOADERS = {year: (load_recent if year >= 2011 else load_old) for year in ELECTIONS}


def parse() -> None:
    """Parse the years in SENAT_YEAR (every year when it is unset), then build the package pl.kbw_senat/all from
    every year's parsed tables."""
    years = [int(year) for year in os.environ["SENAT_YEAR"].split(",")] if "SENAT_YEAR" in os.environ else ELECTIONS
    for year in years:
        folder = PARSED / str(year)
        folder.mkdir(parents=True, exist_ok=True)
        loaded = LOADERS[year](year)
        candidates, votes = loaded["candidates"], loaded["unit_votes"]
        if loaded["districts"]["magnitude"].sum() != 100 or candidates["elected"].sum() != 100:
            raise ValueError(f"{year}: {candidates['elected'].sum()} elected, not 100")
        if votes.height:
            sums = votes.group_by("district", "number").agg(pl.col("votes").sum().alias("summed"))
            joined = candidates.join(sums, on=["district", "number"], how="left")
            off = joined.filter(pl.col("summed").fill_null(0) != pl.col("votes"))
            print(f"{year}: {off.height} candidates whose unit votes differ from the printed total")
        for name, frame in loaded.items():
            frame.write_parquet(folder / f"{name}.parquet")
            print(f"{year} {name}: {frame.height} rows")
    print(build())


VINTAGES = {2011: (2023, date(2011, 10, 9), None), 2001: (2007, date(2001, 9, 23), date(2011, 10, 9))}
SOURCE_LICENSE = "Official public record, no license stated"
ATTRIBUTION = "Źródło danych: Państwowa Komisja Wyborcza, Krajowe Biuro Wyborcze."


def add_source(cs: Changeset) -> UUID:
    source = cs.source(PUBLISHER, TITLE)
    cs.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license=SOURCE_LICENSE,
        attribution=ATTRIBUTION,
    )
    return source


def area_code(vintage: int, number: int) -> str:
    return f"{vintage}:{number}"


def gmina_areas(conn: Connection) -> dict[str, UUID]:
    """The gmina area of each 6-digit TERYT prefix (the code without its type digit) that names one area. A gmina
    whose type changed, as a rural gmina that gained a town, holds both of its dated codes."""
    found: dict[str, set[UUID]] = {}
    for code, area in conn.execute(
        "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.gmina'"
    ).fetchall():
        found.setdefault(code[:6], set()).add(area)
    return {prefix: next(iter(areas)) for prefix, areas in found.items() if len(areas) == 1}


KEY = f"{SOURCE}/all"
SUMMARY = (
    "Poland's Senate elections of 1991-2023 by constituency, gmina, and polling district (2005 by powiat, 1991 by "
    "constituency), with candidates and elected senators, and the Senate constituencies of 2001-2007 (40) and since "
    "2011 (100), from the Krajowe Biuro Wyborcze's data files"
)
# Annex 2 of the Kodeks wyborczy names Łódź's osiedle Stoki, which the city has since renamed Stoki-Sikawa-Podgórze.
ALIASES = {"stoki": "stoki-sikawa-podgórze"}
# How far, in meters, a constituency's own units may reach past the city layer to the PRG outline of the city.
REACH = 50
# The smallest part, in square meters, a constituency keeps when it splits a city.
SLIVER = 100
PLANE = "EPSG:2180"


def unit_name(text: str) -> str:
    """An osiedle or dzielnica as Annex 2 and the city layers both write it: lower case, no `Osiedle` or `Dzielnica`
    prefix, no periods, and no spaces around hyphens (`Krzyki - Partynice` and `Krzyki-Partynice` are one)."""
    text = re.sub(r"^(osiedle|dzielnica)\s+", "", text.strip().lower().replace(".", ""))
    text = re.sub(r"\s+", " ", re.sub(r"\s*-\s*", "-", text))
    return ALIASES.get(text, text)


def city_parts(boundary: str, city: str) -> list[str]:
    """The osiedla or dzielnice of a city that a constituency's description in Annex 2 lists."""
    marker = f"część obszaru miasta na prawach powiatu {city}:"
    if marker not in boundary:
        return []
    return [unit_name(part) for part in boundary.split(marker, 1)[1].split(",") if part.strip()]


def polygons(shape: shapely.Geometry) -> shapely.MultiPolygon:
    """The polygons of an overlay's result, without the lines and points where two outlines only touch."""
    found = boundaries.polygonal(shape)
    if found is None:
        raise ValueError("an outline came out empty")
    return found


def project(shape: shapely.Geometry, transformer: Transformer) -> shapely.Geometry:
    return shapely.transform(shape, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def split_city(
    cs: Draft, name: str, city: City, outline: shapely.Geometry, sides: dict[int, list[str]]
) -> tuple[dict[int, shapely.Geometry], UUID]:
    """Split the PRG outline of a city between its two constituencies along the city's own layer of osiedla or
    dzielnice. The first constituency takes the part of the outline its units cover, reaching up to REACH meters past
    them to the outline's edge but never into the other constituency's units, and the second takes the rest, so the
    two tile the city's outline exactly and meet their neighbors on the PRG boundaries."""
    source = cs.source(city.publisher, city.title)
    cs.add(
        "source",
        id=source,
        kind="official_body",
        publisher=city.publisher,
        title=city.title,
        url=city.page,
        license=city.license,
    )
    artifact = raw.cached(SOURCE, city.url)
    cs.artifact(artifact, source)
    units: dict[str, shapely.Geometry] = {}
    for properties, shape in boundaries.read(artifact.path):
        units[unit_name(properties[city.field])] = shape
    listed = [part for parts in sides.values() for part in parts]
    if sorted(listed) != sorted(units) or len(set(listed)) != len(listed):
        raise ValueError(
            f"{name}: Annex 2 lists {sorted(set(listed) - set(units))} that the city layer lacks, and the layer has "
            f"{sorted(set(units) - set(listed))} that Annex 2 does not list"
        )
    forward = Transformer.from_crs("EPSG:4326", PLANE, always_xy=True)
    back = Transformer.from_crs(PLANE, "EPSG:4326", always_xy=True)
    first, second = sorted(sides)
    mine, theirs = (project(shapely.union_all([units[part] for part in sides[n]]), forward) for n in (first, second))
    # Only the reach is drawn in meters; the outline is cut in its own coordinates, so the city's edge keeps the PRG
    # vertices its neighbors share.
    outline = shapely.set_precision(outline, 1e-7)
    reach = project(shapely.difference(shapely.buffer(mine, REACH), theirs), back)
    # A sliver where the reach grazes the outline's edge goes to the other constituency.
    taken = shapely.MultiPolygon(
        [
            part
            for part in shapely.get_parts(polygons(shapely.intersection(outline, reach)))
            if shapely.area(project(part, forward)) >= SLIVER
        ]
    )
    shapes = {first: taken, second: polygons(shapely.difference(outline, taken))}
    whole = project(outline, forward)
    for number, own, other in ((first, mine, theirs), (second, theirs, mine)):
        flat = project(shapes[number], forward)
        print(
            f"{name}: constituency {number} {shapely.area(flat) / 1e6:.2f} km², its units "
            f"{shapely.area(shapely.intersection(own, whole)) / 1e6:.2f} km² within the city, "
            f"{shapely.area(shapely.intersection(flat, other)) / 1e4:.2f} ha of the other constituency's units, "
            f"parts of {', '.join(f'{shapely.area(part) / 1e4:.2f}' for part in shapely.get_parts(flat))} ha"
        )
    gap = shapely.difference(whole, shapely.union(mine, theirs))
    print(
        f"{name}: city outline {shapely.area(whole) / 1e6:.2f} km², {shapely.area(gap) / 1e4:.2f} ha outside the layer"
    )
    return shapes, source


def build_okregi(cs: Draft) -> set[str]:
    """The Senate constituencies of 2001-2007 and since 2011, with their outlines. An outline the changeset path built
    from the PRG gminy each constituency holds is carried as stored. The six constituencies since 2011 that split
    Wrocław, Łódź, and Kraków by osiedla or dzielnice (Annex 2 of the Kodeks wyborczy) take the PRG outline of the
    city's part, split along the city's own layer, plus the PRG outlines of their other gminy. Returns the codes of
    the constituencies with an outline."""
    conn = cs.conn
    poland = cs.polity("PL")
    source = add_source(cs)
    cs.add(
        "scheme",
        code="pl.okreg_senat",
        polity_id=poland,
        kind="electoral_district",
        rank=2,
        noun="Senate constituency",
    )
    stored = {
        code: row
        for code, *row in conn.execute(
            """select c.code, g.geom::text, g.label_point::text, g.quality::text, g.source_id
               from area a join area_code c on c.area_id = a.id join area_geometry g on g.area_id = a.id
               join changeset ch on ch.id = g.changeset_id
               where a.scheme_code = 'pl.okreg_senat' and ch.pipeline = %s and g.valid_from is null""",
            [SOURCE],
        ).fetchall()
    }
    by_area = dict(
        conn.execute(
            """select g.area_id, ST_AsBinary(g.geom) from area a join area_geometry g on g.area_id = a.id
               where a.scheme_code = 'pl.gmina' and g.valid_to is null"""
        ).fetchall()
    )
    outlines = {code: shapely.from_wkb(by_area[area]) for code, area in gmina_areas(conn).items() if area in by_area}
    regions = {
        code: area
        for code, area in conn.execute(
            "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.wojewodztwo'"
        ).fetchall()
    }
    [(nation_wkb,)] = conn.execute(
        "select ST_AsBinary(g.geom) from area a join area_geometry g on g.area_id = a.id where a.scheme_code = 'pl.nation'"
    ).fetchall()
    nation = shapely.from_wkb(nation_wkb)
    shaped: set[str] = set()
    for vintage, (year, start, end) in VINTAGES.items():
        for address in urls(year, "unit_candidates"):
            cs.artifact(raw.cached(SOURCE, address), source)
        districts = pl.read_parquet(PARSED / str(year) / "districts.parquet")
        members = (
            pl.read_parquet(PARSED / str(year) / "units.parquet")
            .filter(pl.col("gmina").str.contains(r"^\d{6}$"))
            .select("district", "gmina")
            .unique()
        )
        split = set(members.group_by("gmina").len().filter(pl.col("len") > 1)["gmina"])
        cut: dict[int, tuple[shapely.Geometry, UUID]] = {}
        if vintage == 2011:
            for name, city in CITIES.items():
                if city.gmina not in split:
                    raise ValueError(f"{name} is not split between constituencies in {year}")
                sides = {
                    number: parts
                    for number, boundary in districts.select("district", "boundary").iter_rows()
                    if (parts := city_parts(boundary, name))
                }
                holding = set(members.filter(pl.col("gmina") == city.gmina)["district"])
                if set(sides) != holding or len(sides) != 2:
                    raise ValueError(
                        f"{name}: Annex 2 splits it among {sorted(sides)}, the polling districts {holding}"
                    )
                halves, layer = split_city(cs, name, city, outlines[city.gmina], sides)
                cut.update({number: (shape, layer) for number, shape in halves.items()})
        total = 0.0
        for entry in districts.sort("district").iter_rows(named=True):
            number = entry["district"]
            code = area_code(vintage, number)
            parts = members.filter(pl.col("district") == number)["gmina"].to_list()
            area = cs.area("pl.okreg_senat", code)
            cs.add("area", id=area, scheme_code="pl.okreg_senat")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            seat = f" ({entry['seat']})" if entry["seat"] else ""
            for text, lang, kind in (
                (f"Okręg wyborczy nr {number} do Senatu{seat}, {vintage}", "pl", "official"),
                (f"Senate constituency {number}{seat}, {vintage}", "en", "english"),
            ):
                cs.add("name", entity_id=area, text=text, lang=lang, kind=kind, is_preferred=kind == "official")
            voivodeships = {code[:2] for code in parts}
            if len(voivodeships) == 1:
                cs.add("area_parent", area_id=area, parent_id=regions[voivodeships.pop()], valid_from=None)
            if code in stored:
                geom, label, quality, geom_source = stored[code]
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=None,
                    geom=geom,
                    label_point=label,
                    quality=quality,
                    source_id=geom_source,
                )
                total += shapely.area(shapely.from_wkb(geom))
                shaped.add(code)
                continue
            if number not in cut:
                if not split & set(parts):
                    raise ValueError(f"constituency {code} has no stored outline")
                continue
            city_part, layer = cut[number]
            others = [gmina for gmina in parts if gmina not in split]
            missing = [gmina for gmina in others if gmina not in outlines]
            if missing:
                raise ValueError(f"constituency {code}: no outline for gminy {missing}")
            shape = city_part
            if others:
                shape = polygons(shapely.union_all([city_part, *(outlines[gmina] for gmina in others)], grid_size=1e-7))
            shape = shapely.set_srid(shape, 4326)
            total += shapely.area(shape)
            shaped.add(code)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(max(shapely.get_parts(shape), key=shapely.area).representative_point(), 4326),
                    hex=True,
                    include_srid=True,
                ),
                quality="official",
                source_id=layer,
            )
            cs.add(
                "note",
                entity_id=area,
                kind="methodology",
                body_md=CITY_NOTE.format(
                    city=next(name for name, city in CITIES.items() if city.gmina in parts),
                    others=" and of the other gminy it holds" if others else "",
                ),
            )
        print(f"{vintage}: shaped constituencies cover {total / shapely.area(nation):.4f} of Poland")
        cs.add(
            "boundary_set",
            scheme_code="pl.okreg_senat",
            vintage=str(vintage),
            valid_from=start,
            valid_to=end,
            as_of=ELECTIONS[year],
        )
    return shaped


CITY_NOTE = (
    "The outline is the part of {city}'s PRG outline that the osiedla or dzielnice Annex 2 of the Kodeks wyborczy "
    "assigns to this constituency cover in the city's own layer of those units{others}. The city's two constituencies "
    "share its PRG outline with no gap or overlap. The city layer shows the units' current boundaries, which may "
    "differ slightly from those of 2011."
)


VOTES, REGISTERED, BALLOTS, VALID_BALLOTS, INVALID = 1, 2, 3, 4, 5
FPTP = (
    "100 senators, one from each of 100 single-member constituencies, elected by plurality: each voter marks one "
    "candidate (Kodeks wyborczy of 5 January 2011, arts. 259-268)."
)
BLOCK_1991 = (
    "100 senators from the 49 voivodeships, two from each and three from Warsaw and Katowice voivodeships, elected "
    "by plurality: each voter marks as many candidates as there are seats (Ustawa o wyborach do Senatu of 10 May "
    "1991)."
)
BLOCK_2001 = (
    "100 senators from 40 constituencies of 2 to 4 seats, elected by plurality: each voter marks as many candidates "
    "as there are seats (Ordynacja wyborcza do Sejmu i Senatu of 12 April 2001, arts. 189-208)."
)
DISTRICT_METHOD = (
    "Candidate votes and elected senators as the Krajowe Biuro Wyborcze's candidate file prints them for the "
    "constituency. Accounting is summed from the polling districts: voters entitled to vote, ballots taken from the "
    "urn, and valid votes (on valid ballots); invalid is the ballots taken from the urn less the valid votes."
)
BLOCK_NOTE = (
    " Each voter could mark as many candidates as there were seats, so candidate votes add up to more than the "
    "valid ballots, which are stored as the valid figure."
)
NO_UNITS = " The files print no counts below the constituency for this election, so there is no accounting."
POWIAT_NOTE = " Accounting is summed from the powiat table, the finest unit the file prints for this election."
GMINA_METHOD = (
    "Candidate votes and accounting summed from the polling districts of each gmina, with votes from abroad and ships "
    "each in one unit. Cities split between constituencies count only their part in this constituency."
)
OBWOD_METHOD = (
    "Candidate votes and accounting by polling district (obwód głosowania) as the file prints them. Polling "
    "districts are described by streets and addresses, not mapped, so they have no area."
)
POWIAT_METHOD = "Candidate votes and accounting by powiat and city with powiat rights as the file prints them."
OLD_CODES = (
    " Gmina codes before 1999 are those of the 49 voivodeships, which no longer exist, so these gminy have no area."
)


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package pl.kbw_senat/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them. The constituencies come first and the years newest first, the
    order the changeset path first wrote them in, so a party shared by several years is named from the year that
    first wrote it."""
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="PL")
        cs.accept(
            "accounting.votes_valid",
            "Constituency 44 (Warszawa and abroad) in 2019 and 2023 has polling districts abroad whose rows print no "
            "counts, so the valid votes summed from the polling districts fall short of the candidate votes the "
            "candidate file prints for the constituency; both are stored as printed, as the notes say.",
        )
        cs.accept(
            "accounting.turnout",
            "Two polling districts, in constituencies 43 (Warszawa) and 91 (Poznań), print more ballots taken from "
            "the urn than voters entitled to vote (2,037 against 2,031 and 1,410 against 1,409); the counts are "
            "stored as the Krajowe Biuro Wyborcze's files print them.",
        )
        cs.accept(
            "accounting.ballots_sum",
            "Two 2001 polling districts in constituency 6 (Lublin) print more valid votes than ballots taken from the "
            "urn (7,514 against 7,453 and 715 against 365), so they store no invalid figure.",
        )
        shaped = build_okregi(cs)
        # People of this pipeline whose row another pipeline has since written (a crosswalk match that added a
        # gender, say) keep that row.
        taken = {
            id
            for (id,) in conn.execute(
                """select p.id from person p join changeset c on c.id = p.changeset_id
                   join entity e on e.id = p.id join changeset o on o.id = e.changeset_id
                   where o.pipeline = %s and c.pipeline <> %s""",
                [SOURCE, SOURCE],
            )
        }
        written: set[UUID] = set()
        for year in sorted(ELECTIONS, reverse=True):
            build_year(cs, year, shaped, written, taken)
        return cs.write()


def build_year(cs: Draft, year: int, shaped: set[str], written: set[UUID], taken: set[UUID]) -> None:
    conn = cs.conn
    day = ELECTIONS[year]
    folder = PARSED / str(year)
    districts, candidates, units, unit_votes = (
        pl.read_parquet(folder / f"{name}.parquet") for name in ("districts", "candidates", "units", "unit_votes")
    )
    single = year >= 2011
    vintage = 2011 if single else 2001 if year >= 2001 else None

    def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
        cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

    poland = cs.polity("PL")
    senat = cs.key("pl.body", "senat")
    election = cs.key("pl.election", f"{day}:general")
    if senat in cs.minted or election in cs.minted:
        raise ValueError("publish pl.parlament and the pl.kbw_sejm election first")
    source = add_source(cs)
    for kind in FILES[year]:
        for address in urls(year, kind):
            cs.artifact(raw.cached(SOURCE, address), source)
    key_name = "senat" if single else "senat-block-2001" if year >= 2001 else "senat-block-1991"
    system = cs.key("pl.electoral_system", key_name)
    if system in cs.minted:
        cs.add(
            "electoral_system",
            id=system,
            polity_id=poland,
            rules={"formula": "fptp" if single else "block"},
            rules_version=1,
        )
        name(
            system,
            "Senate single-member plurality system"
            if single
            else f"Senate block vote ({'2001-2007' if year >= 2001 else '1991-1997'})",
        )
        cs.add(
            "note",
            entity_id=system,
            kind="methodology",
            body_md=FPTP if single else BLOCK_2001 if year >= 2001 else BLOCK_1991,
        )

    by_item = dict(
        conn.execute(
            """select x.value, x.entity_id from external_id x join party p on p.id = x.entity_id
               where x.scheme = 'wikidata' and p.polity_id = %s
               and not exists (select from entity_merge m where m.entity_id = p.id)""",
            (poland,),
        ).fetchall()
    )
    parties: dict[str, UUID] = {}

    def party_for(committee: str) -> UUID:
        label = " ".join(committee.split())
        if label in parties:
            return parties[label]
        # A committee the pipeline already keyed keeps its party, so a Wikidata party loaded later does not move
        # its candidates.
        item = next((code for text, code in ITEMS.items() if label.upper().rstrip('"').endswith(text)), None)
        if item is not None and item in by_item and ("pl.party", slug(label)) not in cs.own_keys:
            party = parties[label] = by_item[item]
            return party
        party = parties[label] = cs.key("pl.party", slug(label))
        if party in cs.minted and party not in written:
            cs.add("party", id=party, kind="party", polity_id=poland)
            name(party, label, "pl")
            written.add(party)
        return party

    areas = gmina_areas(conn)
    powiats = dict(
        conn.execute(
            "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.powiat'"
        ).fetchall()
    )
    unit_rows: dict[UUID, dict[str, str | None]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []

    def gmina_unit(code: str) -> UUID:
        unit = cs.unit("pl.gmina", code)
        if unit not in unit_rows:
            area = areas.get(code) if len(code) == 6 else None
            unit_rows[unit] = {
                "scheme_code": "pl.gmina",
                "code": code,
                "parent_id": None,
                "area_relation": "equals" if area is not None else "unlocated",
                "area_id": None if area is None else str(area),
                "kind": "standard" if code.isdigit() else "aggregate",
            }
        return unit

    def powiat_unit(code: str) -> UUID:
        short = code[:4] if code.isdigit() else code
        unit = cs.unit("pl.powiat", short)
        if unit not in unit_rows:
            area = powiats.get(short)
            unit_rows[unit] = {
                "scheme_code": "pl.powiat",
                "code": short,
                "parent_id": None,
                "area_relation": "equals" if area is not None else "unlocated",
                "area_id": None if area is None else str(area),
                "kind": "standard" if code.isdigit() else "aggregate",
            }
        return unit

    accounting = units.with_columns(unusable=pl.col("cast") - pl.col("valid"))
    figures = ((REGISTERED, "electors"), (BALLOTS, "cast"), (VALID_BALLOTS, "valid"), (INVALID, "unusable"))

    def put(result_set: UUID, unit: UUID, row: dict[str, int | None]) -> None:
        for measure, column_name in figures:
            figure = row.get(column_name)
            # Two 2001 polling districts print more valid votes than ballots taken from the urn, so they have no
            # invalid figure; their gmina and constituency still take the difference of the sums.
            if figure is not None and not (measure == INVALID and figure < 0):
                values.append((str(result_set), str(unit), None, measure, figure))

    def summed(frame: pl.DataFrame) -> dict[str, int | None]:
        return {
            column_name: None if frame[column_name].null_count() == frame.height else int(frame[column_name].sum())
            for _, column_name in figures
        }

    def make_set(contest: UUID, key: str, label: str, default: bool, completeness: float, note: str) -> UUID:
        result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=source,
            is_default=default,
            completeness=completeness,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
        return result_set

    path = f"pl/{day}-general"
    for entry in districts.sort("district").iter_rows(named=True):
        number = entry["district"]
        key = f"{day}:senat:{number}"
        seat = f" ({entry['seat']})" if entry["seat"] else ""
        code = area_code(vintage, number) if vintage else None
        area = cs.area("pl.okreg_senat", code) if code is not None and code in shaped else None
        contest = cs.key(f"{SOURCE}.contest", key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=senat,
            district_area_id=area,
            magnitude=entry["magnitude"],
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, f"Senate constituency {number}{seat}")
        name(contest, f"Okręg wyborczy nr {number} do Senatu{seat}", "pl", "native")
        cs.add("slug", path=f"{path}/senat/{number}", entity_id=contest)
        people = candidates.filter(pl.col("district") == number).sort("votes", descending=True)
        options: dict[int, UUID] = {}
        for position, row in enumerate(people.sort("number").iter_rows(named=True), 1):
            tag = f"{key}:{row['number']}"
            option = options[row["number"]] = cs.key(f"{SOURCE}.option", tag)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if row["committee"] is None else party_for(row["committee"]),
                ballot_position=position,
                ballot_label=display(row["name"], "pl"),
            )
            person = cs.key(f"{SOURCE}.person", tag)
            if person not in taken:
                cs.add("person", id=person, gender=row["sex"] if row["sex"] in ("male", "female") else None)
            name(person, display(row["name"], "pl"))
            cs.add(
                "candidacy",
                id=cs.key(f"{SOURCE}.candidacy", tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=row["name"],
                is_independent=False,
            )
        district_unit = cs.unit("pl.okreg_senat", f"{day}:{number}")
        unit_rows[district_unit] = {
            "scheme_code": "pl.okreg_senat",
            "code": f"{day}:{number}",
            "parent_id": None,
            "area_relation": "equals" if area is not None else "unlocated",
            "area_id": None if area is None else str(area),
            "kind": "standard",
        }
        here = accounting.filter(pl.col("district") == number)
        note = DISTRICT_METHOD + ("" if single else BLOCK_NOTE)
        if not here.height:
            note = note.split(" Accounting")[0] + NO_UNITS + ("" if single else BLOCK_NOTE)
        elif year == 2005:
            note += POWIAT_NOTE
        official = int(people["votes"].sum())
        district_set = make_set(contest, key, "district", True, 1, note)
        if here.height:
            put(district_set, district_unit, summed(here))
        winners = 0
        for row in people.iter_rows(named=True):
            values.append((str(district_set), str(district_unit), str(options[row["number"]]), VOTES, row["votes"]))
            if row["elected"]:
                winners += 1
                cs.add(
                    "outcome",
                    result_set_id=district_set,
                    option_id=options[row["number"]],
                    kind="elected",
                    seats=None,
                )
        if winners != entry["magnitude"]:
            raise ValueError(f"{key}: {winners} elected for {entry['magnitude']} seats")
        if not here.height:
            continue
        votes = unit_votes.filter(pl.col("district") == number)
        got = int(votes["votes"].sum())
        completeness = min(got / official, 1) if official else 1
        extra = OLD_CODES if year < 1999 else ""
        if got != official:
            extra += (
                f" The polling-district rows add up to {got:,} candidate votes against the constituency total of "
                f"{official:,}; both are stored as printed."
            )
        if year == 2005:
            powiat_set = make_set(contest, key, "powiat", False, completeness, POWIAT_METHOD + extra)
            for row in here.iter_rows(named=True):
                put(powiat_set, powiat_unit(row["gmina"]), row)
            for row in votes.iter_rows(named=True):
                values.append(
                    (
                        str(powiat_set),
                        str(powiat_unit(row["gmina"])),
                        str(options[row["number"]]),
                        VOTES,
                        row["votes"],
                    )
                )
            continue
        gmina_set = make_set(contest, key, "gmina", False, completeness, GMINA_METHOD + extra)
        obwod_set = make_set(contest, key, "obwod", False, completeness, OBWOD_METHOD + extra)
        for code_, frame in here.group_by("gmina"):
            put(gmina_set, gmina_unit(str(code_[0])), summed(frame))
        for row in votes.group_by("gmina", "number").agg(pl.col("votes").sum()).iter_rows(named=True):
            values.append(
                (str(gmina_set), str(gmina_unit(row["gmina"])), str(options[row["number"]]), VOTES, row["votes"])
            )
        lookup: dict[tuple[str, str], UUID] = {}
        for row in here.iter_rows(named=True):
            unit_code = f"{day}:{row['gmina']}:{row['commission']}"
            unit = lookup[(row["gmina"], row["commission"])] = cs.unit("pl.obwod", unit_code)
            unit_rows[unit] = {
                "scheme_code": "pl.obwod",
                "code": unit_code,
                "parent_id": str(gmina_unit(row["gmina"])),
                "area_relation": "unlocated",
                "area_id": None,
                "kind": "standard" if row["gmina"].isdigit() else "aggregate",
            }
            put(obwod_set, unit, row)
        for row in votes.iter_rows(named=True):
            values.append(
                (
                    str(obwod_set),
                    str(lookup[(row["gmina"], row["commission"])]),
                    str(options[row["number"]]),
                    VOTES,
                    row["votes"],
                )
            )

    minted = [
        {"id": str(unit), "polity_id": str(poland), **row}
        for unit, row in unit_rows.items()
        if unit in cs.minted or row["scheme_code"] == "pl.okreg_senat"
    ]
    cs.frame("reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String, "area_id": pl.String}))
    cs.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
