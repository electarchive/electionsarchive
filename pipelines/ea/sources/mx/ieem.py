"""Municipal council (ayuntamiento) elections of the State of México from the Instituto Electoral del Estado de México.

The institute publishes, for every cycle since 1990, a workbook of the vote for each of the state's municipalities and,
for the cycles of 2015 to 2024, a workbook of the vote at each polling station (casilla). The workbooks differ by cycle
in layout, and the cycles from 2009 on report a planilla (the slate of president, síndico, and regidores) that a
coalition or common candidacy of several parties fields as one column, beside the columns of the single parties and of
every subset of a coalition's parties. Each reader below turns one cycle's workbooks into the same `Cycle`.
"""

import functools
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "mx.ieem"
SITE = "https://www.ieem.org.mx/"
RESULTS = SITE + "assets/docs/procesos-electorales/resultados/"
PAGE = SITE + "procesos-electorales/historico-de-procesos-electorales/elecciones-locales.html"
MEXICO = str(uuid5(package.NAMESPACE, "polity\x1fMX"))
STATE = str(uuid5(package.NAMESPACE, "polity\x1fMX-MEX"))
REFERENCE = Path(__file__).resolve().parents[4] / "data" / "reference" / "mx"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5

FILES: dict[int, dict[str, tuple[str, str]]] = {
    2012: {
        "town": (
            SITE + "proceso_2012/re2012/ayuntamientos2012_TEEM.xlsx",
            (
                "Resultados de la elección de ayuntamientos 2012, con base en las actas de cómputo municipales, con las "
                "resoluciones del tribunal electoral"
            ),
        ),
    },
    2000: {
        "town": (RESULTS + "locales/Ayunta2000/TM_AYUNTA2000.xls", "Elección de ayuntamientos 2000 por municipio"),
    },
    1996: {
        "town": (RESULTS + "locales/MUN96/AYUXMU96.xls", "Elección de ayuntamientos 1996 por municipio"),
    },
    1993: {
        "town": (RESULTS + "locales/MUN93/AYUXMU93.xls", "Elección de ayuntamientos 1993 por municipio"),
    },
    1990: {
        "town": (RESULTS + "locales/MUN90/AYUXMU90.xls", "Elección de ayuntamientos 1990 por municipio"),
    },
    2015: {
        "town": (
            SITE + "2015/resultados_2015/Ayuntamientos/Computo_MUNICIPAL_2015.xlsx",
            (
                "Resultados de los cómputos de la elección de miembros de los ayuntamientos 2015, realizados por los "
                "consejos municipales"
            ),
        ),
        "casilla": (
            SITE + "2015/resultados_2015/Ayuntamientos/totcas_computo2015_municipal.xlsx",
            "Resultados por casilla de ayuntamientos 2015, cómputos municipales",
        ),
    },
    2021: {
        "town": (
            RESULTS
            + "2021/"
            + quote("RESULTADOS DEFINITIVOS INTEGRANTES DE LOS AYUNTAMIENTOS/RESULTADOS AYUNTAMIENTOS 2021.xlsx"),
            "Resultados definitivos de la elección de integrantes de los ayuntamientos 2021, por municipio",
        ),
        "casilla": (
            RESULTS
            + "2021/"
            + quote(
                "RESULTADOS DEFINITIVOS INTEGRANTES DE LOS AYUNTAMIENTOS/RESULTADOS POR CASILLA ELECCION AYUNTAMIENTOS 2021.xlsx"
            ),
            "Resultados definitivos por casilla de la elección de ayuntamientos 2021",
        ),
    },
    2018: {
        "town": (
            RESULTS + "2018/2018_SEE_AYUN_MEX_MUNCAND.xlsx",
            "Resultados definitivos por candidato ganador de la elección de miembros de los ayuntamientos 2018",
        ),
        "casilla": (
            RESULTS + "2018/2018_SEE_AYUN_MEX_CAS.xlsx",
            "Resultados definitivos por casilla de la elección de miembros de los ayuntamientos 2018",
        ),
    },
    2024: {
        "town": (
            RESULTS + "2024/Ayuntamientos/Resultados_definitivos_ayu_candidatura.xlsx",
            "Resultados de la elección de ayuntamientos, proceso electoral 2024, por municipio y candidatura",
        ),
        "casilla": (
            RESULTS + "2024/Ayuntamientos/Resultados_definitivos_ayu_casilla.xlsx",
            "Resultados definitivos por casilla de la elección de ayuntamientos 2024",
        ),
    },
}
# Days known to the day; the cycle of 1993 is dated to the month.
MONTHS = {1993: (1993, 11)}
DAYS = {
    1990: date(1990, 11, 11),
    2012: date(2012, 7, 1),
    2009: date(2009, 7, 5),
    2000: date(2000, 7, 2),
    1996: date(1996, 11, 10),
    2015: date(2015, 6, 7),
    2024: date(2024, 6, 2),
    2021: date(2021, 6, 6),
    2018: date(2018, 7, 1),
}

PARTIES = {
    "PAN": "Partido Acción Nacional",
    "PRI": "Partido Revolucionario Institucional",
    "PRD": "Partido de la Revolución Democrática",
    "PVEM": "Partido Verde Ecologista de México",
    "PT": "Partido del Trabajo",
    "MC": "Movimiento Ciudadano",
    "MORENA": "Morena",
    "NAEM": "Nueva Alianza Estado de México",
    "NA": "Nueva Alianza",
    "PES": "Partido Encuentro Social",
    "ES": "Encuentro Social",
    "RSP": "Redes Sociales Progresistas",
    "FXM": "Fuerza por México",
    "VR": "Vía Radical",
    "PH": "Partido Humanista",
    "PFD": "Partido Futuro Democrático",
    "PPS": "Partido Popular Socialista",
    "PFCRN": "Partido del Frente Cardenista de Reconstrucción Nacional",
    "PARM": "Partido Auténtico de la Revolución Mexicana",
    "PDM": "Partido Demócrata Mexicano",
    "PC": "Partido Cardenista",
    "CD": "Convergencia por la Democracia",
    "PCD": "Partido de Centro Democrático",
    "PSN": "Partido de la Sociedad Nacionalista",
    "PAS": "Partido Alianza Social",
    "DS": "Democracia Social",
    "C": "Convergencia",
}
KINDS = {"party": "party", "coalition": "coalition", "common": "common candidacy", "independent": "independent"}


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " ".join(plain.upper().split())


def number(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TypeError(value)
    if isinstance(value, int | float):
        if value != int(value):
            raise ValueError(f"{value!r} is not whole")
        return int(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+(\.0+)?", text):
        return int(float(text))
    raise ValueError(f"{value!r} is not a count")


@functools.cache
def sheet(url: str, index: int = 0) -> list[list[object]]:
    path = raw.cached(SOURCE, url).path
    book = fastexcel.read_excel(str(path))
    frame = book.load_sheet_by_idx(index, header_row=None).to_polars()
    return [list(row) for row in frame.iter_rows()]


@dataclass(frozen=True)
class Slate:
    """One planilla on the ballot: a single party, a coalition or common candidacy, or an independent."""

    parties: tuple[str, ...]
    kind: str  # a key of KINDS

    @property
    def label(self) -> str:
        return "-".join(self.parties)


@dataclass
class Casilla:
    section: int
    kind: str
    number: int
    extension: int
    votes: dict[str, int]
    unregistered: int | None
    null: int | None
    total: int | None
    registered: int | None
    status: str = ""


@dataclass
class Town:
    code: int
    name: str
    slates: dict[str, Slate] = field(default_factory=dict)
    votes: dict[str, int] = field(default_factory=dict)
    unregistered: int | None = None
    null: int | None = None
    total: int | None = None
    registered: int | None = None
    winner: str | None = None
    casillas: list[Casilla] = field(default_factory=list)


@dataclass
class Cycle:
    year: int
    towns: dict[int, Town]


def slate_of(header: object) -> Slate | None:
    """The slate a column header names, or None for a header that names none."""
    text = fold(header).replace("_", "-")
    if match := re.fullmatch(r"CAND-?\s?IND-?(\d+)", text):
        return Slate((f"IND{int(match.group(1))}",), "independent")
    kind = "party"
    if text.startswith("COALICION"):
        kind, text = "coalition", text.removeprefix("COALICION")
    elif text.startswith(("CANDIDATURA COMUN", "CAND. COMUN", "CAND COMUN")):
        kind, text = "common", re.sub(r"^(CANDIDATURA COMUN|CAND\.? COMUN)", "", text)
    elif text.startswith("CC-"):
        kind, text = "common", text.removeprefix("CC-")
    elif text.endswith("-CC"):
        kind, text = "common", text.removesuffix("-CC")
    parties = tuple(part for part in re.split(r"[-\s]+", text.strip()) if part)
    if not parties:
        return None
    if len(parties) > 1 and kind == "party":
        kind = "coalition"
    return Slate(parties, kind)


def header_at(rows: list[list[object]], first: str) -> int:
    for i, row in enumerate(rows):
        if row and fold(row[0]).replace(" ", "_") == first:
            return i
    raise LookupError(f"no header row starting {first}")


def position(header: list[object], name: str, nth: int = 0) -> int:
    hits = [i for i, h in enumerate(header) if h is not None and fold(h).replace(" ", "_") == name]
    if len(hits) <= nth:
        raise LookupError(f"no column {name}")
    return hits[nth]


def is_count(value: object) -> bool:
    return value is not None and re.fullmatch(r"\d+(\.0+)?", str(value).strip()) is not None


def town_cycle_wide(
    year: int,
    url: str,
    first: str,
    start: str,
    stop: str | int,
    fields: dict[str, str | int],
    winner: int | None = 0,
) -> dict[int, Town]:
    """A municipal workbook with one column per slate between the `start` column and the `stop` column.

    A field or `stop` given as a negative number is an offset from the first "Siglas" column, for the workbooks that
    leave the headers of the vote columns after the slates blank.
    """
    rows = sheet(url)
    at = header_at(rows, first)
    header = rows[at]
    winners = [i for i, h in enumerate(header) if h is not None and fold(h) == "SIGLAS"]

    def where(spec: str | int) -> int:
        return winners[0] + spec if isinstance(spec, int) else position(header, spec)

    first_slate, last_slate = position(header, start) + 1, where(stop)
    slates = [(i, slate_of("NA" if header[i] is None else header[i])) for i in range(first_slate, last_slate)]
    columns = {name: where(column) for name, column in fields.items()}
    id_column = position(header, "ID_MUNICIPIO")
    towns: dict[int, Town] = {}
    for row in rows[at + 1 :]:
        if not is_count(row[id_column]) or row[columns["total"]] is None:
            continue  # a footnote numbered like a municipality
        code = int(float(str(row[id_column])))
        town = Town(code, str(row[columns["name"]]).strip())
        for i, slate in slates:
            if slate is None:
                raise LookupError(f"{year}: header {header[i]!r} names no slate")
            votes = number(row[i])
            if votes is None:
                continue
            if slate.label in town.slates:
                raise ValueError(f"{year} {town.name}: {slate.label} twice")
            town.slates[slate.label] = slate
            town.votes[slate.label] = votes
        for name in ("unregistered", "null", "total", "registered"):
            if name in columns:
                setattr(town, name, number(row[columns[name]]))
        if winner is not None:
            named = slate_of(row[winners[winner]])
            if named is None or named.label not in town.slates:
                raise ValueError(f"{year} {town.name}: winner {row[winners[winner]]!r} is not on the ballot")
            town.winner = named.label
        towns[code] = town
    return towns


def casilla_cycle_wide(
    year: int,
    url: str,
    towns: dict[int, Town],
    identifiers: dict[str, str],
    stop: str,
    fields: dict[str, str],
) -> None:
    """Attach the polling stations of a workbook with one column per slate or subset of a coalition's parties."""
    rows = sheet(url)
    at = header_at(rows, identifiers["first"])
    header = rows[at]
    ids = {name: position(header, column) for name, column in identifiers.items() if name != "first"}
    last = position(header, stop)
    known = {position(header, c) for c in identifiers.values()}
    columns = [
        (i, slate_of("NA" if header[i] is None else header[i]))
        for i in range(last)
        if i not in known and i >= max(known)
    ]
    extra = {name: position(header, column) for name, column in fields.items()}
    for row in rows[at + 1 :]:
        if row[ids["town"]] is None or not re.fullmatch(r"\d+(\.0+)?", str(row[ids["town"]]).strip()):
            continue
        town = towns[int(float(str(row[ids["town"]])))]
        votes: dict[str, int] = {}
        for i, slate in columns:
            count = number(row[i])
            if not count:
                continue
            if slate is None:
                raise LookupError(f"{year}: header {header[i]!r} names no slate")
            home = home_slate(town, slate)
            if home is None:
                raise ValueError(f"{year} {town.name} section {row[ids['section']]}: {header[i]!r} has {count} votes")
            votes[home] = votes.get(home, 0) + count
        town.casillas.append(
            Casilla(
                section=int(float(str(row[ids["section"]]))),
                kind=str(row[ids["kind"]]).strip(),
                number=number(row[ids["number"]]) or 0,
                extension=number(row[ids["extension"]]) or 0 if "extension" in ids else 0,
                votes=votes,
                unregistered=number(row[extra["unregistered"]]) if "unregistered" in extra else None,
                null=number(row[extra["null"]]) if "null" in extra else None,
                total=number(row[extra["total"]]) if "total" in extra else None,
                registered=number(row[extra["registered"]]) if "registered" in extra else None,
                status=str(row[extra["status"]] or "").strip().upper() if "status" in extra else "",
            )
        )


def home_slate(town: Town, column: Slate) -> str | None:
    """The label of the slate that a column's votes belong to in a municipality, or None when none runs."""
    parties = set(column.parties)
    runs = [s for s in town.slates.values() if parties <= set(s.parties)]
    if len(runs) > 1:
        exact = [s for s in runs if set(s.parties) == parties and s.kind == column.kind]
        runs = exact or [s for s in runs if set(s.parties) == parties] or [s for s in runs if s.kind == column.kind]
    return runs[0].label if len(runs) == 1 else None


CASILLA_IDS = {
    "first": "ID_ESTADO",
    "town": "ID_MUNICIPIO",
    "section": "SECCION",
    "kind": "TIPO_CASILLA",
    "number": "ID_CASILLA",
    "extension": "EXT_CONTIGUA",
    "state_name": "NOMBRE_ESTADO",
    "district": "ID_DISTRITO_LOCAL",
    "seat": "CABECERA_DISTRITAL_LOCAL",
    "town_name": "MUNICIPIO",
}
CASILLA_FIELDS = {
    "unregistered": "NUM_VOTOS_CAN_NREG",
    "null": "NUM_VOTOS_NULOS",
    "total": "TOTAL_VOTOS",
    "registered": "LISTA_NOMINAL",
    "status": "TRIBUNAL",
}


def cycle_2015() -> Cycle:
    """The 2015 workbooks give each party's own column, a column for each combination of the parties of a coalition,
    and the coalition's summed votes. The institute's column of Nueva Alianza has an empty header."""
    rows = sheet(FILES[2015]["town"][0])
    at = header_at(rows, "MUNICIPIO")
    singles = {6: "PAN", 7: "PRI", 8: "PRD", 9: "PT", 10: "PVEM", 11: "MC", 12: "NA", 13: "MORENA", 14: "PH", 15: "ES"}
    singles[16] = "PFD"
    sums = {28: ("PRI", "PVEM", "NA"), 29: ("PAN", "PT")}
    towns: dict[int, Town] = {}
    for row in rows[at + 1 :]:
        if not is_count(row[0]) or number(row[26]) is None:
            continue
        code = int(float(str(row[0])))
        town = Town(code, str(row[1]).strip(), registered=number(row[2]), unregistered=number(row[24]))
        town.null, town.total = number(row[25]), number(row[26])
        taken: set[str] = set()
        for column, parties in sums.items():
            if (votes := number(row[column])) is not None:
                slate = Slate(parties, "coalition")
                town.slates[slate.label], town.votes[slate.label] = slate, votes
                taken |= set(parties)
        for column, party in singles.items():
            if party not in taken and (votes := number(row[column])) is not None:
                town.slates[party], town.votes[party] = Slate((party,), "party"), votes
        for n, column in enumerate((22, 23), 1):
            if (votes := number(row[column])) is not None:
                town.slates[f"IND{n}"], town.votes[f"IND{n}"] = Slate((f"IND{n}",), "independent"), votes
        named = slate_of(row[32])
        if named is None or named.label not in town.slates:
            raise ValueError(f"2015 {town.name}: winner {row[32]!r} is not on the ballot")
        town.winner = named.label
        towns[code] = town
    casillas = sheet(FILES[2015]["casilla"][0])
    at = header_at(casillas, "MUNICIPIO")
    header = casillas[at]
    votes_at: list[tuple[int, Slate | None]] = []
    independents = 0
    for i in range(4, position(header, "NO_REGISTRADOS")):
        text = "NA" if header[i] is None else str(header[i])
        if fold(text) == "CANDIDATO INDEPENDIENTE":
            independents += 1
            text = f"CAND_IND{independents}"
        votes_at.append((i, slate_of(re.sub(r"\bPES\b", "ES", fold(text)))))
    for row in casillas[at + 1 :]:
        if not is_count(row[0]) or row[3] is None:
            continue
        town = towns[int(float(str(row[0])))]
        votes: dict[str, int] = {}
        for i, slate in votes_at:
            count = number(row[i])
            if not count:
                continue
            assert slate is not None
            home = home_slate(town, slate)
            if home is None:
                raise ValueError(f"2015 {town.name} section {row[2]}: {header[i]!r} has {count} votes")
            votes[home] = votes.get(home, 0) + count
        match = re.fullmatch(r"([A-Z])(\d*)(?:C(\d+))?", str(row[3]).strip())
        if match is None:
            raise ValueError(f"2015: casilla {row[3]!r}")
        town.casillas.append(
            Casilla(
                section=int(float(str(row[2]))),
                kind=match.group(1),
                number=int(match.group(2) or 1),
                extension=int(match.group(3) or 0),
                votes=votes,
                unregistered=number(row[position(header, "NO_REGISTRADOS")]),
                null=number(row[position(header, "NULOS")]),
                total=number(row[position(header, "TOTAL")]),
                registered=None,
            )
        )
    return Cycle(2015, towns)


def simple_cycle(
    year: int,
    header_row: int,
    skip_zero: bool = True,
    extra: dict[str, str] | None = None,
) -> dict[int, Town]:
    """A municipal workbook with the slates as columns, whose headers sit in one row; a column may be one of a pair
    of votes and percent."""
    rows = sheet(FILES[year]["town"][0])
    header = rows[header_row]
    slates: dict[int, Slate] = {}
    fields: dict[str, int] = {}
    names = {"LISTA NOMINAL": "registered", "NO REG.": "unregistered", "NO REGISTRADOS": "unregistered"}
    names |= {"VALIDOS": "valid", "NULOS": "null", "VOTOS NULOS": "null", "TOTAL": "total", "VOTACION": "total"}
    for i, h in enumerate(header):
        if h is None or i < 2 or fold(h) in ("CABECERA",):
            continue
        text = fold(h)
        if text in names:
            fields[names[text]] = i
        elif text == "OTROS":
            slates[i] = Slate(("OTROS",), "others")
        elif i < max(fields.get("unregistered", 99), fields.get("valid", 99), 99):
            if (slate := slate_of(text)) is None:
                raise LookupError(f"{year}: header {h!r} names no slate")
            slates[i] = slate
    towns: dict[int, Town] = {}
    for row in rows[header_row + 1 :]:
        if not is_count(row[0]) or not isinstance(row[1], str):
            continue
        town = Town(int(float(str(row[0]))), str(row[1]).strip())
        for i, slate in slates.items():
            votes = number(row[i])
            if votes is None or (skip_zero and votes == 0):
                continue
            town.slates[slate.label], town.votes[slate.label] = slate, votes
        for name, i in fields.items():
            if name in ("registered", "unregistered", "null", "total"):
                setattr(town, name, number(row[i]))
        towns[town.code] = town
    return towns


def cycle_simple(year: int, header_row: int) -> Cycle:
    return Cycle(year, simple_cycle(year, header_row))


def cycle_2012() -> Cycle:
    rows = sheet(FILES[2012]["town"][0])
    header = rows[8]
    columns = {i: slate_of(str(h).replace("M C", "MC")) for i, h in enumerate(header) if 2 <= i <= 19 and h}
    towns: dict[int, Town] = {}
    for row in rows[12:]:
        match = re.fullmatch(r"(\d+) - (.+)", str(row[0] or "").strip())
        if match is None:
            continue
        town = Town(int(match.group(1)), match.group(2).strip(), registered=number(row[1]))
        for i, slate in columns.items():
            if slate is None or (votes := number(row[i])) is None:
                continue
            town.slates[slate.label], town.votes[slate.label] = slate, votes
        town.unregistered, town.null, town.total = number(row[20]), number(row[24]), number(row[26])
        named = slate_of(row[37])
        if named is None or named.label not in town.slates:
            raise ValueError(f"2012 {town.name}: winner {row[37]!r} is not on the ballot")
        town.winner = named.label
        towns[town.code] = town
    return Cycle(2012, towns)


def cycle_2024() -> Cycle:
    towns = town_cycle_wide(
        2024,
        FILES[2024]["town"][0],
        "ID_MUNICIPIO",
        "LISTA_NOMINAL",
        "VOTOS_VALIDOS",
        {
            "name": "MUNICIPIO",
            "unregistered": "VOTOS_NO_REGISTRADOS",
            "null": "VOTOS_NULOS",
            "total": "TOTAL_VOTOS",
            "registered": "LISTA_NOMINAL",
        },
    )
    casilla_cycle_wide(
        2024,
        FILES[2024]["casilla"][0],
        towns,
        {**CASILLA_IDS, "first": "CIRCUNSCRIPCION", "act": "ACTA_CASILLA-MEC", "urn": "URNA_ELECTRONICA"},
        "NUM_VOTOS_VALIDOS",
        CASILLA_FIELDS,
    )
    return Cycle(2024, towns)


def cycle_2021() -> Cycle:
    towns = town_cycle_wide(
        2021,
        FILES[2021]["town"][0],
        "ID_MUNICIPIO",
        "LISTA_NOMINAL",
        -5,
        {"name": "MUNICIPIO", "unregistered": -4, "null": -3, "total": -2, "registered": "LISTA_NOMINAL"},
    )
    casilla_cycle_wide(
        2021, FILES[2021]["casilla"][0], towns, {**CASILLA_IDS, "label": "CASILLA"}, "NUM_VOTOS_VALIDOS", CASILLA_FIELDS
    )
    return Cycle(2021, towns)


def cycle_2018() -> Cycle:
    towns = town_cycle_wide(
        2018,
        FILES[2018]["town"][0],
        "ID_ESTADO",
        "CASILLAS",
        "NUM_VOTOS_VALIDOS",
        {
            "name": "MUNICIPIO",
            "unregistered": "NUM_VOTOS_CAN_NREG",
            "null": "NUM_VOTOS_NULOS",
            "total": "TOTAL_VOTOS",
            "registered": "LISTA_NOMINAL",
        },
        winner=None,
    )
    casilla_cycle_wide(
        2018,
        FILES[2018]["casilla"][0],
        towns,
        {**CASILLA_IDS, "first": "CIRCUNSCRIPCION", "label": "CASILLA"},
        "NUM_VOTOS_VALIDOS",
        CASILLA_FIELDS,
    )
    return Cycle(2018, towns)


# The institute's spreadsheet readers take the header "NA" (Nueva Alianza) for an empty cell, so an empty header among
# the vote columns stands for it.

# The packages.


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area_id(scheme: str, code: str) -> str:
    return str(uuid5(package.NAMESPACE, f"area:{scheme}@None\x1f{code}"))


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "planilla")
PUBLISHER = "Instituto Electoral del Estado de México"
LICENSE = "Public information the institute publishes for download on its website; no license stated"


@functools.cache
def reference_names() -> dict[int, str]:
    """The municipalities' current names, from the archive's own list of the state's municipalities."""
    try:
        areas = pl.read_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "mx.municipio")
        codes = pl.read_parquet(REFERENCE / "area_code.parquet")
    except FileNotFoundError:
        return {}
    joined = areas.join(codes, left_on="id", right_on="area_id").filter(pl.col("code").str.starts_with("15"))
    return {int(code[2:]): name for code, name in joined.select("code", "name").iter_rows() if name}


@functools.cache
def reference_sections() -> frozenset[str]:
    try:
        codes = pl.read_parquet(REFERENCE / "area_code.parquet")
        areas = pl.read_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "mx.seccion")
    except FileNotFoundError:
        return frozenset()
    joined = areas.join(codes, left_on="id", right_on="area_id")
    return frozenset(code for code in joined["code"].to_list() if code.startswith("15"))


def town_name(code: int, fallback: str) -> str:
    return reference_names().get(code) or " ".join(w.capitalize() for w in fallback.lower().split())


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text).lower()).strip("-")


def party_key(abbrev: str) -> str:
    return key("party", abbrev)


# Names the older workbooks print differently from the municipalities' current names.
RENAMED = {
    "JALATLACO": "XALATLACO",
    "MEXICALCINGO": "MEXICALTZINGO",
    "VILLA DEL C.": "VILLA DEL CARBON",
    "V. DE CHALCO SOLIDARIDAD": "VALLE DE CHALCO SOLIDARIDAD",
}


def inegi_codes(cycle: Cycle) -> Cycle:
    """Key a cycle's municipalities by the INEGI code of the municipality; the institute numbers them its own way."""
    names = {fold(name): code for code, name in reference_names().items()}
    if not names:
        raise FileNotFoundError(f"the reference list of municipalities is missing from {REFERENCE}")
    towns: dict[int, Town] = {}
    for town in cycle.towns.values():
        plain = re.sub(r"\s+\d+$", "", fold(town.name))
        plain = RENAMED.get(plain, plain)
        hits = [plain] if plain in names else [n for n in names if n.startswith(plain) or plain.startswith(n)]
        if len(hits) != 1:
            raise LookupError(f"{cycle.year}: no single municipality named {town.name!r}: {hits}")
        town.code = names[hits[0]]
        if town.code in towns:
            raise ValueError(f"{cycle.year}: {town.name} twice")
        towns[town.code] = town
    return Cycle(cycle.year, towns)


@functools.cache
def cycles() -> dict[int, Cycle]:
    return {y: inegi_codes(c) for y, c in raw_cycles().items()}


def raw_cycles() -> dict[int, Cycle]:
    return {
        2024: cycle_2024(),
        2021: cycle_2021(),
        2018: cycle_2018(),
        2015: cycle_2015(),
        2012: cycle_2012(),
        2000: cycle_simple(2000, 4),
        1996: cycle_simple(1996, 4),
        1993: cycle_simple(1993, 4),
        1990: cycle_simple(1990, 4),
    }


def package_key(year: int) -> str:
    return f"{SOURCE}/{year}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(package_key(year) for year in sorted(cycles()))]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The municipal councils, municipal presidencies, and municipalities of the State of México, the parties on "
        "the institute's ballots, and the planilla electoral system",
    )
    p.external |= {MEXICO, STATE, "mx.municipio"}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher=PUBLISHER,
        title="Resultados de las elecciones locales (ayuntamientos)",
        url=PAGE,
        license=LICENSE,
        license_url=PAGE,
        attribution="Instituto Electoral del Estado de México (IEEM)",
    )
    seen: set[str] = set()
    for cycle in cycles().values():
        for town in cycle.towns.values():
            for slate in town.slates.values():
                seen.update(slate.parties)
    for abbrev in sorted(a for a in seen if not re.fullmatch(r"IND\d+", a) and a != "OTROS"):
        id = party_key(abbrev)
        p.add("party", id=id, kind="party", polity_id=MEXICO)
        p.name(id, PARTIES.get(abbrev, abbrev), "es", "official")
        p.add("external_id", entity_id=id, scheme="mx.ieem.party", value=abbrev)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MEXICO,
        valid_from=date(1990, 1, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Each municipality elects its ayuntamiento by one planilla: a municipal president, one or more síndicos, "
            "and the regidores of relative majority, each with a substitute. The planilla with the most votes in the "
            "municipality wins, with no runoff. The institute also fills the remaining regidurías by proportional "
            "representation from the lists of the parties that took part; those seats are not modeled here."
        ),
        lang="en",
    )
    towns: dict[int, str] = {}
    for cycle in cycles().values():
        for code, town in cycle.towns.items():
            towns.setdefault(code, town.name)
    for code, fallback in sorted(towns.items()):
        name = town_name(code, fallback)
        body, office = key("body", code), key("office", code)
        p.add("body", id=body, polity_id=STATE, kind="council")
        p.name(body, f"Ayuntamiento of {name}", "en", "english")
        p.name(body, f"Ayuntamiento de {name}", "es", "official", False)
        p.add("office", id=office, polity_id=STATE, body_id=body, kind="mayor")
        p.name(office, f"Municipal President of {name}", "en", "english")
        p.name(office, f"Presidencia municipal de {name}", "es", "official", False)
        p.add(
            "reporting_unit",
            id=key("unit", "town", code),
            polity_id=MEXICO,
            scheme_code="mx.municipio",
            code=f"15{code:03d}",
            area_relation="equals",
            area_id=area_id("mx.municipio", f"15{code:03d}"),
            kind="standard",
        )
        p.external.add(area_id("mx.municipio", f"15{code:03d}"))
    return p


class Facts:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str | None, int, int]] = []

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is not None:
            self.rows.append((result_set, unit, option, measure, value))

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


TOWN_METHOD = (
    "The institute's count of the municipal councils' tallies (cómputos municipales), after the rulings of the "
    "electoral courts. A planilla's votes are those of its column, which holds the votes cast for the coalition or "
    "common candidacy, or the single party, whose planilla it is; the votes marked for several of a coalition's "
    "parties are included. The total of votes is the valid votes for planillas, the votes for candidates who are not "
    "registered, and the null votes. Valid votes here include the votes for unregistered candidates."
)
CASILLA_METHOD = (
    "The institute's count at each polling station (casilla), as published after the courts' rulings. The votes "
    "marked for several of a coalition's parties are added to the coalition's planilla. Polling stations whose "
    "vote the electoral courts annulled are left out, because the final municipal count does not include them, so "
    "the stations add up to the municipal count except where a court changed the count of a station without "
    "annulling it."
)


def election(cycle: Cycle) -> Package:
    year = cycle.year
    day = DAYS.get(year) or date(*MONTHS[year], 1)
    precision = "day" if year in DAYS else "month"
    files = FILES[year]
    p = Package(
        package_key(year),
        SOURCE,
        f"The {year} municipal council elections of the State of México by municipality and polling station from the "
        "Instituto Electoral del Estado de México",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {MEXICO, STATE, "mx.municipio", "mx.casilla"}
    for url, _ in files.values():
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    election_id = key("election", year)
    path = f"mx/mex/{day.isoformat() if precision == 'day' else f'{year}'}-ayuntamientos"
    p.add(
        "election",
        id=election_id,
        polity_id=STATE,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision=precision,
        status="held",
    )
    p.name(election_id, f"{year} State of México municipal elections", "en", "official")
    p.name(election_id, f"Elección de ayuntamientos del Estado de México {year}", "es", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Facts()
    alliances: dict[tuple[str, ...], str] = {}
    sections = reference_sections()
    units: set[str] = set()
    off_totals: list[str] = []
    over: list[str] = []
    for code, town in sorted(cycle.towns.items()):
        name = town_name(code, town.name)
        contest = key("contest", year, code)
        municipio = area_id("mx.municipio", f"15{code:03d}")
        p.external.add(municipio)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=key("office", code),
            district_area_id=municipio,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {name} municipal president election", "en", "english")
        p.name(contest, f"Elección de la presidencia municipal de {name} {year}", "es", "native", False)
        p.add("slug", path=f"{path}/{slugify(name)}", entity_id=contest, is_current=True)
        options: dict[str, str] = {}
        for position, (label, slate) in enumerate(sorted(town.slates.items(), key=lambda kv: -town.votes[kv[0]]), 1):
            option = key("option", year, code, label)
            options[label] = option
            alliance = None
            if len(slate.parties) > 1:
                alliance = alliances.setdefault(slate.parties, key("alliance", year, *slate.parties))
            shown = "Otros partidos" if slate.kind == "others" else "–".join(slate.parties)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if slate.kind == "others" else "candidate",
                party_id=party_key(slate.parties[0]) if slate.kind == "party" else None,
                alliance_id=alliance,
                ballot_position=position,
                ballot_label=shown if slate.kind != "independent" else f"Candidatura independiente {label[3:]}",
            )
        write_in = key("option", year, code, "unregistered")
        p.add(
            "contest_option", id=write_in, contest_id=contest, kind="write_in", ballot_label="Candidatos no registrados"
        )
        town_unit = key("unit", "town", code)
        town_set = key("result_set", year, code, "town")
        p.add(
            "result_set",
            id=town_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", town_set), entity_id=town_set, kind="methodology", body_md=TOWN_METHOD, lang="en")
        for label, count in town.votes.items():
            values.add(town_set, town_unit, options[label], VOTES, count)
            p.add("declared", result_set_id=town_set, option_id=options[label], measure=VOTES, value=count)
        planillas = sum(town.votes.values())
        unregistered, null = town.unregistered or 0, town.null or 0
        values.add(town_set, town_unit, write_in, VOTES, unregistered)
        values.add(town_set, town_unit, None, VALID, planillas + unregistered)
        values.add(town_set, town_unit, None, INVALID, null)
        values.add(town_set, town_unit, None, BALLOTS, planillas + unregistered + null)
        values.add(town_set, town_unit, None, REGISTERED, town.registered or None)
        if town.total is not None and town.total != planillas + unregistered + null:
            off_totals.append(f"{name} ({town.total} printed, {planillas + unregistered + null} summed)")
        if town.total is not None:
            p.add("declared", result_set_id=town_set, option_id=None, measure=BALLOTS, value=town.total)
        if town.winner is not None:
            p.add("outcome", result_set_id=town_set, option_id=options[town.winner], kind="elected", seats=1)
        if town.casillas:
            casilla_set = key("result_set", year, code, "casilla")
            counted = [c for c in town.casillas if c.status != "ANULADA"]
            valid_sum = 0
            for c in counted:
                kind_code = "B" if c.kind == "B" else f"{c.kind}{c.number}" + (f"C{c.extension}" if c.extension else "")
                unit_code = f"{year}:15-{code:03d}-{c.section:04d}-{kind_code}"
                unit = key("unit", "casilla", unit_code)
                if unit in units:
                    raise ValueError(f"{year}: casilla {unit_code} twice")
                units.add(unit)
                located = f"15{c.section:04d}" in sections
                section = area_id("mx.seccion", f"15{c.section:04d}") if located else municipio
                if located:
                    p.external.add(section)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=MEXICO,
                    scheme_code="mx.casilla",
                    code=unit_code,
                    parent_id=town_unit,
                    area_relation="within",
                    area_id=section,
                    kind="standard" if c.section else "aggregate",
                )
                for label, count in c.votes.items():
                    values.add(casilla_set, unit, options[label], VOTES, count)
                values.add(casilla_set, unit, write_in, VOTES, c.unregistered)
                nonvotes = (c.unregistered or 0) + (c.null or 0)
                values.add(casilla_set, unit, None, VALID, sum(c.votes.values()) + (c.unregistered or 0))
                values.add(casilla_set, unit, None, INVALID, c.null)
                values.add(casilla_set, unit, None, BALLOTS, sum(c.votes.values()) + nonvotes)
                values.add(casilla_set, unit, None, REGISTERED, c.registered or None)
                valid_sum += sum(c.votes.values())
                if c.registered and sum(c.votes.values()) + nonvotes > c.registered:
                    over.append(f"{unit_code} ({sum(c.votes.values()) + nonvotes} ballots, {c.registered} registered)")
            share = min(1.0, round(valid_sum / planillas, 6)) if planillas else 1.0
            p.add(
                "result_set",
                id=casilla_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=False,
                completeness=share,
            )
            p.add(
                "note",
                id=key("note", casilla_set),
                entity_id=casilla_set,
                kind="methodology",
                body_md=CASILLA_METHOD,
                lang="en",
            )
    for parties, id in alliances.items():
        p.add("alliance", id=id, kind="electoral_alliance", election_id=election_id)
        for member in parties:
            p.add("alliance_member", alliance_id=id, party_id=party_key(member), role="member")
    p.frame("result_value", values.frame())
    if off_totals:
        p.accept(
            "accounting.declared",
            "The institute prints a total that differs from its columns at " + "; ".join(off_totals),
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"The institute's own count prints more ballots than registered voters at {len(over)} polling stations: "
            + "; ".join(over),
        )
    print(f"{package_key(year)}: {len(cycle.towns)} municipalities", flush=True)
    return p


def audit() -> None:
    for year, cycle in sorted(cycles().items()):
        for town in cycle.towns.values():
            total = sum(town.votes.values()) + (town.unregistered or 0) + (town.null or 0)
            if town.total is not None and total != town.total:
                print(year, town.code, town.name, "total", town.total, "summed", total, file=sys.stderr)
        bad = 0
        for town in cycle.towns.values():
            sums: dict[str, int] = {}
            for c in town.casillas:
                if c.status == "ANULADA":
                    continue
                for k, v in c.votes.items():
                    sums[k] = sums.get(k, 0) + v
            off = {k: (town.votes[k], sums.get(k, 0)) for k in town.votes if town.votes[k] != sums.get(k, 0)}
            if off and town.casillas:
                bad += 1
                print(year, town.code, town.name, off, file=sys.stderr)
        print(year, len(cycle.towns), "towns", bad, "disagree", file=sys.stderr)


def fetch() -> None:
    for files in FILES.values():
        for url, _ in files.values():
            raw.fetch(SOURCE, url, suffix=Path(url).suffix.lower())


def parse() -> None:
    registry().write()
    for year, cycle in sorted(cycles().items()):
        election(cycle).write()


def emit() -> None:
    package.run("load", packages())


def main() -> None:
    if "audit" in sys.argv:
        audit()
        return
    parse()


if __name__ == "__main__":
    main()
