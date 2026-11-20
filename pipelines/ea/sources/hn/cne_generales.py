"""Honduras: the general elections of 1980 to 2021 from the electoral authority's published summaries.

The Consejo Nacional Electoral (before 2019 the Tribunal Supremo Electoral, before 1981 the Tribunal Nacional de
Elecciones) kept the results of its past general elections as PDF tables by department and municipality, and published
the 2021 count as JSON files by department. The authority's own hosts no longer answer, so the files are the copies
that web.archive.org captured. Each election is one package, `hn.cne/generales-<year>`, built on `hn.cne/geo` and
`hn.cne/generales-registry`.
"""

import hashlib
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.hn import cne
from ea.store import DATA

SOURCE = cne.SOURCE
HONDURAS = cne.HONDURAS
FILES: dict[str, dict[str, str]] = json.loads((Path(__file__).parent / "cne_generales_files.json").read_text())
WAYBACK = "https://web.archive.org/web/"
CNE_PDF = "https://cne.hn/documentos/estadisticas_y_proce_elec/"
PORTAL_2021 = "https://resultadosgenerales2021.cne.hn:8080/resultados/HN/"
PRESIDENCY = cne.PRESIDENCY
MAYORALTY = cne.MAYORALTY
CONGRESS = cne.CONGRESS
SYSTEM_FPTP = cne.SYSTEM_FPTP
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6


def k(kind: str, *parts: object) -> str:
    return cne.key("g-" + kind, *parts)


GENERAL_SOURCE = k("source", "cne-generales")
HERALDO_SOURCE = k("source", "tse-declaratoria-2017")
SCHEME_ABROAD = "hn.exterior"
SCHEME_COUNTRY = "hn.pais"
CONSTITUENT = k("body", "constituent-assembly-1980")
PARLACEN = k("body", "parlacen")
OFFICIAL = {
    "PL": ("Partido Liberal de Honduras", "Liberal Party of Honduras"),
    "PN": ("Partido Nacional de Honduras", "National Party of Honduras"),
    "LIBRE": ("Partido Libertad y Refundación", "Liberty and Refoundation Party"),
    "PINU": ("Partido Innovación y Unidad Social Demócrata", "Innovation and Unity Social Democratic Party"),
    "DC": ("Partido Demócrata Cristiano de Honduras", "Christian Democratic Party of Honduras"),
    "UD": ("Partido Unificación Democrática", "Democratic Unification Party"),
    "PAC": ("Partido Anticorrupción", "Anticorruption Party"),
    "AP": ("Partido Alianza Patriótica Hondureña", "Honduran Patriotic Alliance Party"),
    "FA": ("Partido Frente Amplio", "Broad Front Party"),
    "PCSC": ("Partido de Centro Social Cristiano Va; Movimiento Solidario", "Christian Social Center Party VA"),
    "PNR": ("Partido Nueva Ruta de Honduras", "New Route of Honduras Party"),
    "PLD": ("Partido Liberación Democrático de Honduras", "Democratic Liberation Party of Honduras"),
    "PTSH": ("Partido Todos Somos Honduras", "We Are All Honduras Party"),
    "MIDE": ("Movimiento Independiente Dignidad y Esperanza", "Independent Movement Dignity and Hope"),
    "MBHH": ("Milton Benítez Honduras Humana", "Milton Benítez Humane Honduras"),
    "UNOH": ("Unidad Nacional Opositora de Honduras", "National Opposition Unity of Honduras"),
    "AOD": ("Alianza de Oposición Contra la Dictadura (Libre-PINU)", "Opposition Alliance Against the Dictatorship"),
    "VA": ("Va Movimiento Solidario", "Va Solidarity Movement"),
}
# the parties of cne.PARTIES, whose rows the primaries' registry already holds
EXISTING = {"PL": "01", "PN": "02", "LIBRE": "07"}
# the codes the 2021 portal gives the parties; the first three are cne.PARTIES' own
CODES = {
    "1": "PL",
    "2": "PN",
    "4": "DC",
    "5": "UD",
    "6": "PAC",
    "7": "LIBRE",
    "8": "AP",
    "9": "FA",
    "10": "PCSC",
    "11": "PNR",
    "13": "PLD",
    "14": "PTSH",
    "45": "MIDE",
    "47": "MBHH",
    "58": "UNOH",
}
VEHICLES = {"MIDE", "MBHH", "UNOH", "AOD"}
# the column heads of the PDF tables, as the printed tables name them
HEADS = {
    "LIBERAL": "PL",
    "P.LIBERAL": "PL",
    "PL": "PL",
    "NACIONAL": "PN",
    "P.NACIONAL": "PN",
    "PN": "PN",
    "PINU": "PINU",
    "PINU-SD": "PINU",
    "P.D.C.H.": "DC",
    "PDCH": "DC",
    "DC": "DC",
    "P.U.D.H.": "UD",
    "UD": "UD",
    "INDEPEND.": "IND",
    "B-M": "IND:B-M",
    "J-M": "IND:J-M",
    "R-D": "IND:R-D",
    "T.VALIDOS": "valid",
    "VALIDOS": "valid",
    "NULOS": "null",
    "BLANCOS": "blank",
    "TOT.VOTOS": "cast",
    "ESCRUTADOS": "cast",
    "CENSADOS": "registered",
    "INSCRITOS": "registered",
    "ABSTENCION": "skip",
    "ELECTORES": "cast",
    "CENSO": "registered",
}
PARTY_COLUMNS = set(CODES.values()) | {"IND", "IND:B-M", "IND:J-M", "IND:R-D", "PINU"}
DAYS = {
    1980: date(1980, 4, 20),
    1981: date(1981, 11, 29),
    1985: date(1985, 11, 24),
    1989: date(1989, 11, 26),
    1993: date(1993, 11, 28),
    1997: date(1997, 11, 30),
    2001: date(2001, 11, 25),
    2005: date(2005, 11, 27),
    2017: date(2017, 11, 26),
    2021: date(2021, 11, 28),
}


def inbox() -> Path:
    return Path(os.environ.get("EA_HN_GENERALES", str(DATA / "inbox" / "hn.cne_generales")))


def fetch() -> None:
    """Add the saved copies of the authority's files to the raw cache from the folder named by EA_HN_GENERALES, one
    file per hash named by its SHA-256, and check each against the hash it was recorded with."""
    for url, entry in FILES.items():
        if url in raw.keys(SOURCE):
            continue
        content = (inbox() / f"{entry['sha256']}{entry['suffix']}").read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{url} does not match its hash")
        raw.store(
            SOURCE,
            url,
            content,
            suffix=entry["suffix"],
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def pdf(name: str) -> str:
    """The key of a PDF copy of the authority's, by the end of its address."""
    found = [url for url in FILES if url.endswith(name)]
    if len(found) != 1:
        raise ValueError(f"{name} names {len(found)} files")
    return found[0]


PDFS = {
    1980: ["ELECCIONES%201980.pdf"],
    1981: ["ELECCIONES%201981.pdf"],
    1985: ["ELECCIONES%201985.pdf"],
    1989: ["ELECCIONES%201989.pdf"],
    1993: ["ELECCIONES%201993.pdf"],
    1997: ["ELECCIONES%201997.pdf"],
    2001: ["Presidente%202001.pdf", "Diputados%202001.pdf", "Corporaciones%20Municipales%202001.pdf"],
    2005: ["Presidente_2005.pdf", "Corporaciones%202005.pdf"],
}


def text(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def number(token: str) -> int | None:
    """A count printed with a period or comma between thousands; None for a decimal such as a percentage."""
    if token == "-":
        return 0
    if re.fullmatch(r"\d+", token):
        return int(token)
    if re.fullmatch(r"\d{1,4}[.,]\d{3}([.,]\d{3})*", token):
        return int(re.sub(r"[.,]", "", token))
    return None


ROW = re.compile(r"^\s*(\d\d)\s+(\d\d)\s+(\S.*?)\s*$")


class Table:
    """The rows of one printed table: by municipality, with the national total the table prints."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, int]] = {}
        self.names: dict[str, str] = {}
        self.total: dict[str, int] = {}


def heads(line: str) -> list[tuple[str, int]] | None:
    """The columns a header line names with the position where each ends, or None when the line is no header."""
    found = [(m.group(0), m.end()) for m in re.finditer(r"\S+", line)]
    if not any(t in ("T.VALIDOS", "VALIDOS") for t, _ in found):
        return None
    return [(HEADS[t], end) for t, end in found if t in HEADS]


def place(body: str, offset: int, columns: list[tuple[str, int]], fixed: bool = False) -> tuple[str, dict[str, str]]:
    """A row's name and its numbers, each placed under the column whose head ends nearest the number's end, so a
    column the row leaves empty stays empty. A row with a number for every column is read in order."""
    tokens = [(m.group(0), m.end() + offset) for m in re.finditer(r"\S+", body)]
    numbers: list[tuple[str, int]] = []
    while tokens and re.fullmatch(r"\d+([.,]\d+)*|-", tokens[-1][0]):
        numbers.insert(0, tokens.pop())
    name = " ".join(t for t, _ in tokens)
    placed: dict[str, str] = {}
    if len(numbers) == len(columns):
        return name, {c: t for (c, _), (t, _) in zip(columns, numbers, strict=True)}
    if fixed and len(numbers) == len(columns) - 1 and columns[-1][0] == "skip":
        return name, {c: t for (c, _), (t, _) in zip(columns, numbers, strict=False)}
    for token, end in numbers:
        column = min(columns, key=lambda c: abs(c[1] - end))[0]
        if column in placed:
            raise ValueError(f"{name}: two numbers under {column}")
        placed[column] = token
    return name, placed


FIXED_2005 = ["registered", "PINU", "PN", "PL", "UD", "DC", "valid", "null", "blank", "cast", "skip"]


def municipal(lines: list[str], fixed: list[str] | None = None) -> Table:
    """Read the municipal rows of a layout where a header line precedes each department's rows. With `fixed`, the
    header lines are not read and every row holds one number for each of the named columns."""
    table = Table()
    columns: list[tuple[str, int]] = [(c, 1000 * i) for i, c in enumerate(fixed or [])]
    for line in lines:
        found = None if fixed else heads(line)
        if found is not None:
            columns = found
            continue
        match = ROW.match(line)
        if match is not None and columns:
            name, placed = place(line[match.start(3) :], match.start(3), columns, fixed is not None)
            if not placed:
                continue  # a municipality the table prints without a count
            code = match.group(1) + match.group(2)
            values = {c: number(n) for c, n in placed.items() if c != "skip"}
            if any(v is None for v in values.values()):
                raise ValueError(f"{code} {name}: unreadable number in {placed}")
            if code in table.rows:
                raise ValueError(f"{code} {name} is printed twice")
            table.rows[code] = {c: int(v) for c, v in values.items() if v is not None}
            table.names[code] = name
        elif not table.total and re.match(r"^\s*TOTAL( GENERAL| NACIONAL)?\s+\d", line) and columns:
            total = re.match(r"^\s*TOTAL( GENERAL| NACIONAL)?", line)
            assert total is not None
            _, placed = place(line[total.end() :], total.end(), columns)
            table.total = {c: int(v) for c, n in placed.items() if c != "skip" and (v := number(n)) is not None}
    return table


def departments_1997(lines: list[str]) -> dict[str, Table]:
    """The three department summaries of 1997, by section title, each with its rows keyed by department code."""
    out: dict[str, Table] = {}
    section = ""
    columns: list[tuple[str, int]] = []
    for line in lines:
        title = re.match(r"^\s*RESUMEN PARA (PRESIDENTE|DIPUTADOS|CORPORACIONES MUNICIPALES) POR DEPARTAMENTO", line)
        if title is not None:
            section = title.group(1)
            out[section] = Table()
            continue
        found = heads(line)
        if found is not None and section:
            columns = found
            continue
        if not section or not columns:
            continue
        match = re.match(r"^\s+([A-Z][A-Z .]+?)\s{2,}(\d.*)$", line)
        if match is None:
            continue
        _name, placed = place(" " + match.group(2), match.start(2) - 1, columns)
        values = {c: int(v) for c, t in placed.items() if c != "skip" and (v := number(t)) is not None}
        table = out[section]
        if table.total:
            continue
        if match.group(1) == "TOTALES":
            table.total = values
        else:
            code = f"{len(table.rows) + 1:02d}"
            table.rows[code] = values
            table.names[code] = match.group(1)
    for section, table in out.items():
        if len(table.rows) != 18 or not table.total:
            raise ValueError(f"the 1997 {section} summary holds {len(table.rows)} departments")
    return out


YEARS = (1980, 1981, 1985, 1989, 1993, 1997, 2001, 2005, 2017, 2021)
NAMES: dict[str, Any] = {}


def geo_names() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    if not NAMES:
        NAMES["names"] = cne.names()
    return NAMES["names"]


def party_id(code: str) -> str | None:
    """The id of a party; None for an independent candidate."""
    if code.startswith("IND"):
        return None
    return cne.key("party", EXISTING[code]) if code in EXISTING else k("party", code)


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/generales-registry",
        *(f"{SOURCE}/generales-{year}" for year in YEARS),
    ]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/generales-registry",
        SOURCE,
        "The sources, parties, bodies, and unit schemes of Honduras' general elections of 1980 to 2021",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {HONDURAS}
    p.add(
        "source",
        id=GENERAL_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral de Honduras",
        title="Results of the general elections of 1980 to 2021",
        url="https://www.cne.hn/",
        license="Official publication; reuse terms are not stated",
        attribution="Consejo Nacional Electoral de Honduras",
        notes=(
            "The authority's own addresses no longer answer. The files are its published tables, as web.archive.org "
            "captured them (the PDFs on 18 January 2026, the 2021 count's JSON files on 19 December 2024 and 3 "
            "January 2024). The 1980 to 1993 tables are those of the Tribunal Nacional de Elecciones, the 1997 to 2005 "
            "tables those of the Tribunal Supremo Electoral."
        ),
    )
    p.add(
        "source",
        id=HERALDO_SOURCE,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de Honduras",
        title="Declaratoria de Elecciones Generales 2017 (Acuerdo No. 22-2017)",
        url="https://www.elheraldo.hn/binrepository/DeclaratoriaEG2017integrada-final_EH1135836_MG203296225.pdf",
        license="Official publication; reuse terms are not stated",
        attribution="Tribunal Supremo Electoral de Honduras",
        notes="The authority's declaration of 17 December 2017, as the newspaper El Heraldo published its PDF.",
    )
    p.add(
        "scheme",
        code=SCHEME_COUNTRY,
        polity_id=HONDURAS,
        kind="administrative",
        rank=0,
        noun="country",
        code_system=None,
    )
    p.add(
        "scheme",
        code=SCHEME_ABROAD,
        polity_id=HONDURAS,
        kind="precinct",
        rank=5,
        noun="voting place abroad",
        code_system=None,
    )
    p.add("body", id=CONSTITUENT, polity_id=HONDURAS, kind="constituent_assembly")
    p.name(CONSTITUENT, "National Constituent Assembly of Honduras (1980)", "en", "official")
    p.name(CONSTITUENT, "Asamblea Nacional Constituyente de Honduras", "es", "native", False)
    p.add("body", id=PARLACEN, polity_id=HONDURAS, kind="unicameral")
    p.name(PARLACEN, "Central American Parliament (deputies for Honduras)", "en", "official")
    p.name(PARLACEN, "Parlamento Centroamericano, diputados por Honduras", "es", "native", False)
    for code in sorted(OFFICIAL):
        if code in EXISTING:
            continue
        id = k("party", code)
        spanish, english = OFFICIAL[code]
        p.add("party", id=id, kind="list_vehicle" if code in VEHICLES else "party", polity_id=HONDURAS)
        p.name(id, spanish, "es", "official")
        p.name(id, english, "en", "short", False)
        number_of = {v: c for c, v in CODES.items()}.get(code)
        if number_of is not None:
            p.add("external_id", entity_id=id, scheme="hn.cne.party", value=f"{int(number_of):02d}")
    return p


class Build:
    """Rows shared by every election's package: contests, options, units, result sets, and the values frame."""

    def __init__(self, p: Package, year: int) -> None:
        self.p = p
        self.year = year
        self.day = DAYS[year]
        self.election = k("election", year)
        self.path = f"hn/{self.day}-general"
        self.units: dict[str, str] = {}
        self.slugs: set[str] = set()
        self.values: list[pl.DataFrame] = []
        self.source = GENERAL_SOURCE
        self.sets: dict[object, str] = {}
        self.options: dict[tuple[object, str], str] = {}
        p.external |= {HONDURAS}
        p.add(
            "election",
            id=self.election,
            polity_id=HONDURAS,
            kind="general",
            date_from=self.day,
            date_to=self.day,
            status="held",
        )
        p.name(self.election, f"{year} Honduran general election", "en", "official")
        p.name(self.election, f"Elecciones generales {year}", "es", "native", False)
        p.add("slug", path=self.path, entity_id=self.election, is_current=True)

    def note(self, body: str) -> None:
        self.p.add(
            "note",
            id=k("note", self.election),
            entity_id=self.election,
            kind="methodology",
            body_md=body,
            lang="en",
        )

    def unit(self, code: str, name: str, scheme: str, area: str | None, kind: str = "standard") -> str:
        id = self.units.get(code)
        if id is None:
            id = self.units[code] = k("unit", self.day, code)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=HONDURAS,
                scheme_code=scheme,
                code=f"{self.day}:{code}",
                area_relation="equals" if area else "unlocated",
                area_id=area,
                kind=kind,
            )
            self.p.name(id, name, "es", "official")
        return id

    def contest(
        self,
        ident: object,
        kind: str,
        english: str,
        spanish: str,
        slug: str,
        *,
        office: str | None = None,
        body: str | None = None,
        district: str | None = None,
        magnitude: int | None = None,
        system: str | None = None,
        completeness: float | None = 1.0,
    ) -> str:
        p = self.p
        contest = k("contest", self.year, ident)
        p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, english, "en", "official")
        p.name(contest, spanish, "es", "native", False)
        if slug in self.slugs:
            raise ValueError(f"slug {slug} repeats")
        self.slugs.add(slug)
        p.add("slug", path=f"{self.path}/{slug}", entity_id=contest, is_current=True)
        self.sets[ident] = k("result_set", self.year, ident)
        p.add(
            "result_set",
            id=self.sets[ident],
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=self.source,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        return contest

    def option(self, ident: object, contest: str, column: str, kind: str, label: str, position: int) -> str:
        id = self.options[(ident, column)] = k("option", self.year, ident, column)
        self.p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind=kind,
            party_id=party_id(column),
            ballot_position=position,
            ballot_label=label,
        )
        return id

    def tally(
        self,
        ident: object,
        kind: str,
        rows: dict[str, dict[str, int]],
        unit_of: dict[str, str],
        contest: str,
        labels: dict[str, str],
        order: list[str],
        declared: dict[str, int] | None = None,
    ) -> None:
        """Add the options of a contest and the values of its rows, `rows` being the cells by unit code."""
        present = [c for c in order if any(c in row for row in rows.values())]
        for position, column in enumerate(present, start=1):
            self.option(ident, contest, column, kind, labels[column], position)
        result_set = self.sets[ident]
        values: list[tuple[str, str | None, int, int]] = []
        for code, row in rows.items():
            unit = unit_of[code]
            for column, value in row.items():
                if column in present:
                    values.append((unit, self.options[(ident, column)], VOTES, value))
                elif column in ACCOUNTS:
                    values.append((unit, None, ACCOUNTS[column], value))
        frame = pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row")
        self.add_values(result_set, frame)
        if declared:
            self.declare(ident, declared, present)

    def declare(self, ident: object, total: dict[str, int], present: list[str]) -> None:
        for column, value in total.items():
            if column in present:
                option, measure = self.options[(ident, column)], VOTES
            elif column in ACCOUNTS:
                option, measure = None, ACCOUNTS[column]
            else:
                continue
            self.p.add("declared", result_set_id=self.sets[ident], option_id=option, measure=measure, value=value)

    def add_values(self, result_set: str, frame: pl.DataFrame) -> None:
        self.values.append(
            frame.select(
                pl.lit(result_set).alias("result_set_id"),
                pl.col("unit_id"),
                pl.col("option_id").cast(pl.String),
                pl.lit(0, pl.Int16).alias("channel"),
                pl.col("measure").cast(pl.Int16),
                pl.lit(0, pl.Int16).alias("count_round"),
                pl.lit(0, pl.Int16).alias("flags"),
                pl.col("value").cast(pl.Int64),
            )
        )

    def finish(self) -> Package:
        if self.values:
            self.p.frame("result_value", pl.concat(self.values))
        return self.p


ACCOUNTS = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "null": INVALID, "blank": BLANK}
ORDER = ["PL", "PN", "LIBRE", "PINU", "DC", "UD", "IND", "IND:B-M", "IND:J-M", "IND:R-D"]
LABELS = {
    "PL": "Partido Liberal",
    "PN": "Partido Nacional",
    "LIBRE": "Partido Libertad y Refundación",
    "PINU": "PINU-SD",
    "DC": "Partido Demócrata Cristiano",
    "UD": "Partido Unificación Democrática",
    "IND": "Candidato independiente",
    "IND:B-M": "Candidato independiente B-M",
    "IND:J-M": "Candidato independiente J-M",
    "IND:R-D": "Candidato independiente R-D",
}


def town_names() -> dict[str, tuple[str, str]]:
    return geo_names()[1]


def department_names() -> dict[str, str]:
    return geo_names()[0]


def municipal_units(b: Build, table: Table) -> dict[str, str]:
    """A reporting unit for each municipality a table prints, and for each place abroad."""
    towns = town_names()
    out: dict[str, str] = {}
    for code in table.rows:
        if code[:2] == "20":
            out[code] = b.unit(code, cne.title(table.names[code]), SCHEME_ABROAD, None, "abroad_mission")
        elif code in towns:
            out[code] = b.unit(code, cne.title(towns[code][0]), "hn.municipio", cne.area("hn.municipio", code))
        else:
            raise ValueError(f"{code} {table.names[code]} is no municipality of the geo package")
    return out


def subset(table: Table, department: str) -> dict[str, dict[str, int]]:
    return {c: r for c, r in table.rows.items() if c[:2] == department}


def president(b: Build, rows: dict[str, dict[str, int]], units: dict[str, str], total: dict[str, int]) -> None:
    contest = b.contest(
        "pres",
        "office",
        "President of Honduras",
        "Presidente de la República",
        "president",
        office=PRESIDENCY,
        magnitude=1,
        system=SYSTEM_FPTP,
    )
    b.tally("pres", "ticket", rows, units, contest, LABELS, ORDER, total)


def deputies(b: Build, rows: dict[str, dict[str, int]], units: dict[str, str], body: str, word: str) -> None:
    """One contest for each department, its values by the units of its rows (a department has a municipal row for
    each of its municipalities, or its own row when only department totals are printed)."""
    names = department_names()
    for dept in sorted({code[:2] for code in rows}):
        if dept == "20":
            continue
        english = word
        name = cne.title(names[dept])
        contest = b.contest(
            ("dip", dept),
            "body_seats",
            f"{english}, {name}",
            f"{'Asamblea Nacional Constituyente' if body == CONSTITUENT else 'Congreso Nacional'}, {name}",
            f"deputies-{changeset.slug(names[dept])}",
            body=body,
            district=cne.area("hn.departamento", dept),
        )
        mine = {c: r for c, r in rows.items() if c[:2] == dept}
        b.tally(("dip", dept), "list", mine, units, contest, LABELS, ORDER)


def mayors(b: Build, rows: dict[str, dict[str, int]], units: dict[str, str]) -> None:
    towns = town_names()
    for code, row in rows.items():
        if code[:2] == "20":
            continue
        name = cne.title(towns[code][0])
        contest = b.contest(
            ("mayor", code),
            "office",
            f"Mayor of {name}",
            f"Alcalde de {name}",
            f"mayor-{code}-{changeset.slug(towns[code][0])}",
            office=MAYORALTY,
            magnitude=1,
            district=cne.area("hn.municipio", code),
        )
        b.tally(("mayor", code), "ticket", {code: row}, units, contest, LABELS, ORDER)


SINGLE_TABLE = (
    "The Tribunal Nacional de Elecciones printed one table of each party's votes by department and municipality, "
    "headed as the summary for {contests}, so the same votes are stored for each of those contests. A municipality "
    "printed without a count is left out."
)
GENERAL_NOTE = (
    "Votes are those of the authority's printed summary tables by municipality. Cells are as printed, including the "
    "typing slips a department's own printed total shows."
)


def slips(table: Table, towns: dict[str, tuple[str, str]]) -> dict[str, list[str]]:
    """The municipalities whose printed cells contradict each other, by what they contradict."""
    found: dict[str, list[str]] = defaultdict(list)
    for code, row in table.rows.items():
        if code[:2] == "20":
            continue
        name = cne.title(towns[code][0])
        parties = sum(v for c, v in row.items() if c in PARTY_COLUMNS)
        if "valid" in row and row["valid"] != parties:
            found["valid"].append(f"{name} ({row['valid']:,} valid against {parties:,} for the parties)")
        if (
            all(c in row for c in ("valid", "null", "blank", "cast"))
            and row["valid"] + row["null"] + row["blank"] != row["cast"]
        ):
            found["ballots"].append(name)
        if "registered" in row and "cast" in row and row["cast"] > row["registered"]:
            found["turnout"].append(f"{name} ({row['cast']:,} ballots, {row['registered']:,} registered)")
    return found


def disagreements(table: Table) -> list[str]:
    """Where the national total a table prints differs from what its municipal rows add up to."""
    out = []
    for column, printed in table.total.items():
        summed = sum(row.get(column, 0) for row in table.rows.values())
        if summed != printed:
            out.append(f"{column} {printed:,} printed against {summed:,}")
    return out


def accept_slips(p: Package, *tables: Table, national: Table | None = None) -> None:
    """Accept the accounting findings that a printed table's own cells cause, each with the cells named."""
    towns = town_names()
    found: dict[str, list[str]] = defaultdict(list)
    for table in tables:
        for kind, names in slips(table, towns).items():
            found[kind] += [n for n in names if n not in found[kind]]
    if found["valid"]:
        p.accept(
            "accounting.votes_valid",
            "The printed table's valid-vote cell differs from the sum of the municipality's party votes in "
            + "; ".join(found["valid"])
            + ". The cells are stored as printed.",
        )
    if found["ballots"]:
        p.accept(
            "accounting.ballots_sum",
            "The printed valid, null, and blank cells do not add up to the ballots cast in "
            + ", ".join(found["ballots"])
            + ". The cells are stored as printed.",
        )
    if found["turnout"]:
        p.accept(
            "accounting.turnout",
            "The printed ballots cast exceed the registered voters in " + "; ".join(found["turnout"]) + ".",
        )
    if national is not None:
        diffs = disagreements(national)
        if diffs:
            p.accept(
                "accounting.declared",
                "The national total the table prints differs from the sum of its municipal rows: "
                + "; ".join(diffs)
                + ". The printed tables do not show which cells account for it.",
            )


def election_1980() -> Package:
    p = Package(
        f"{SOURCE}/generales-1980",
        SOURCE,
        "The 1980 election of the National Constituent Assembly of Honduras, by municipality, from the Tribunal "
        "Nacional de Elecciones' printed tables",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/generales-registry"],
    )
    b = Build(p, 1980)
    url = pdf(PDFS[1980][0])
    p.artifact(raw.cached(SOURCE, url), GENERAL_SOURCE)
    table = municipal(text(url))
    b.note(
        "Honduras elected a National Constituent Assembly on 20 April 1980. The table prints each party's votes by "
        "municipality, and the votes of three independent candidates, printed by their initials, in Cortés. "
        + GENERAL_NOTE
    )
    units = municipal_units(b, table)
    deputies(b, table.rows, units, CONSTITUENT, "Constituent Assembly")
    return b.finish()


def single_ballot(year: int, with_mayors: bool) -> Package:
    p = Package(
        f"{SOURCE}/generales-{year}",
        SOURCE,
        f"The {year} general election of Honduras, by municipality, from the authority's printed tables",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/generales-registry"],
    )
    b = Build(p, year)
    url = pdf(PDFS[year][0])
    p.artifact(raw.cached(SOURCE, url), GENERAL_SOURCE)
    table = municipal(text(url))
    contests = "president, deputies, and mayors" if with_mayors else "president and deputies"
    b.note(SINGLE_TABLE.format(contests=contests) + " " + GENERAL_NOTE)
    units = municipal_units(b, table)
    president(b, table.rows, units, table.total)
    deputies(b, table.rows, units, CONGRESS, "National Congress")
    if with_mayors:
        mayors(b, table.rows, units)
    accept_slips(p, table, national=table)
    return b.finish()


def election_1997() -> Package:
    p = Package(
        f"{SOURCE}/generales-1997",
        SOURCE,
        "The 1997 general election of Honduras, by department, from the authority's printed summaries",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/generales-registry"],
    )
    b = Build(p, 1997)
    url = pdf(PDFS[1997][0])
    p.artifact(raw.cached(SOURCE, url), GENERAL_SOURCE)
    tables = departments_1997(text(url))
    b.note(
        "The authority's 1997 summaries print the votes for president, deputies, and municipal corporations by "
        "department. The municipal corporations' votes, which add the votes of every mayoral contest in a "
        "department, are not stored as contests. " + GENERAL_NOTE
    )
    names = department_names()
    pres = tables["PRESIDENTE"]
    units = {
        code: b.unit(code, cne.title(names[code]), "hn.departamento", cne.area("hn.departamento", code))
        for code in pres.rows
    }
    president(b, pres.rows, units, pres.total)
    deputies(b, tables["DIPUTADOS"].rows, units, CONGRESS, "National Congress")
    return b.finish()


def election_2001() -> Package:
    p = Package(
        f"{SOURCE}/generales-2001",
        SOURCE,
        "The 2001 general election of Honduras (president, deputies, and mayors), by municipality, from the "
        "Tribunal Supremo Electoral's printed tables",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/generales-registry"],
    )
    b = Build(p, 2001)
    sources = [pdf(name) for name in PDFS[2001]]
    for url in sources:
        p.artifact(raw.cached(SOURCE, url), GENERAL_SOURCE)
    pres, dips, corp = (municipal(text(url)) for url in sources)
    b.note(
        "The authority printed the votes for president, for deputies of each department, and for the municipal "
        "corporations in three tables, each by municipality. The presidential table also prints the vote abroad "
        "by city. " + GENERAL_NOTE
    )
    units = municipal_units(b, pres)
    president(b, pres.rows, units, pres.total)
    deputies(b, dips.rows, units, CONGRESS, "National Congress")
    mayors(b, corp.rows, units)
    accept_slips(p, pres, dips, corp, national=pres)
    return b.finish()


def election_2005() -> Package:
    p = Package(
        f"{SOURCE}/generales-2005",
        SOURCE,
        "The 2005 general election of Honduras (president and mayors), by municipality, from the Tribunal Supremo "
        "Electoral's escrutinio general",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/generales-registry"],
    )
    b = Build(p, 2005)
    pres_url, corp_url = (pdf(name) for name in PDFS[2005])
    for url in (pres_url, corp_url):
        p.artifact(raw.cached(SOURCE, url), GENERAL_SOURCE)
    pres = municipal(text(pres_url), FIXED_2005)
    corp = municipal(text(corp_url), FIXED_2005)
    b.note(
        "The authority's escrutinio general prints the votes for president and for the municipal corporations by "
        "municipality, with the vote in the United States by city. The deputies' tables print votes for individual "
        "candidates by their position on the list and are not stored. " + GENERAL_NOTE
    )
    units = municipal_units(b, pres)
    president(b, pres.rows, units, pres.total)
    mayors(b, corp.rows, units)
    accept_slips(p, pres, corp, national=pres)
    return b.finish()


CANDIDATES_2017 = [
    ("PN", "Juan Orlando Hernández Alvarado"),
    ("AOD", "Salvador Alejandro Cesar Nasralla Salum"),
    ("PL", "Luis Orlando Zelaya Medrano"),
    ("AP", "Romeo Orlando Vásquez Velásquez"),
    ("PAC", "Marlene Elizabeth Alvarenga Castellanos"),
    ("DC", "Lucas Evangelisto Aguilera Pineda"),
    ("UD", "José Alfonso Díaz Narváez"),
    ("FA", "Isaías Fonseca Aguilar"),
    ("VA", "Eliseo Vallecillo Reyes"),
]


def election_2017() -> Package:
    p = Package(
        f"{SOURCE}/generales-2017",
        SOURCE,
        "The 2017 presidential election of Honduras, national totals and the declared winner, from the Tribunal "
        "Supremo Electoral's declaration",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/generales-registry"],
    )
    b = Build(p, 2017)
    p.external |= {cne.TERRITORY}
    b.source = HERALDO_SOURCE
    url = pdf("DeclaratoriaEG2017integrada-final_EH1135836_MG203296225.pdf")
    p.artifact(raw.cached(SOURCE, url), HERALDO_SOURCE)
    lines = text(url)
    start = next(i for i, line in enumerate(lines) if "NIVEL PRESIDENCIAL" in line)
    block = lines[start : start + 60]
    votes = [int(m.group(1)) for line in block if (m := re.search(r"\d{4}-\d{4}-\d{5}\s+(\d{4,8})\s+\d+\.\d\d%", line))]
    printed: dict[str, int] = {}
    for line in block:
        for label, column in (
            ("Total Votos Válidos", "valid"),
            ("Total Votos en blanco", "blank"),
            ("Total Votos Nulos", "null"),
            ("Gran total de votos", "cast"),
        ):
            found = re.search(re.escape(label) + r"\s+(\d+)\s", line)
            if found is not None:
                printed[column] = int(found.group(1))
    if len(votes) != len(CANDIDATES_2017) or sum(votes) != printed["valid"]:
        raise ValueError(f"the 2017 declaration's candidates add up to {sum(votes)}, not {printed.get('valid')}")
    if printed["valid"] + printed["blank"] + printed["null"] != printed["cast"]:
        raise ValueError("the 2017 declaration's totals do not add up")
    b.note(
        "The Tribunal Supremo Electoral declared the 2017 general election on 17 December 2017 after its special "
        "scrutiny of 5,759 polling tables. Its declaration prints the presidential votes of each candidate for the "
        "whole country only, and no finer count. The candidate of the Partido Nacional was declared elected."
    )
    unit = b.unit("HN", "Honduras", SCHEME_COUNTRY, cne.TERRITORY)
    contest = b.contest(
        "pres",
        "office",
        "President of Honduras",
        "Presidente de la República",
        "president",
        office=PRESIDENCY,
        magnitude=1,
        system=SYSTEM_FPTP,
    )
    row = {code: v for (code, _), v in zip(CANDIDATES_2017, votes, strict=True)} | printed
    b.tally(
        "pres",
        "candidate",
        {"HN": row},
        {"HN": unit},
        contest,
        {c: n for c, n in CANDIDATES_2017} | LABELS,
        [c for c, _ in CANDIDATES_2017],
        row,
    )
    for code, name in CANDIDATES_2017:
        person = k("person", 2017, code)
        p.add("person", id=person, birth_precision=None)
        p.name(person, name, "es", "official")
        candidacy = k("candidacy", 2017, code)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=b.options[("pres", code)],
            role="head",
            list_position=1,
            ballot_name=name,
        )
        if code == "PN":
            p.add(
                "outcome",
                result_set_id=b.sets["pres"],
                option_id=b.options[("pres", code)],
                kind="won",
            )
    return b.finish()


def portal(name: str) -> tuple[str, Any]:
    found = [url for url in FILES if url.endswith(f"/resultados/HN/{name}")]
    if len(found) != 1:
        raise ValueError(f"{name} names {len(found)} files")
    return found[0], json.loads(raw.cached(SOURCE, found[0]).path.read_text(encoding="utf-8"))


def count(document: dict[str, Any]) -> dict[str, int]:
    """The cells of one 2021 presidential count: each party's votes by its party column, and the accounting."""
    out: dict[str, int] = {}
    for row in document["resultados"]:
        ident = row["id_candidatura"]
        if ident == "BLC":
            out["blank"] = int(row["cant_votos"])
        elif ident == "NUL":
            out["null"] = int(row["cant_votos"])
        else:
            out[CODES[ident]] = int(row["cant_votos"])
    out["cast"] = int(document["cant_votantes"])
    out["valid"] = sum(v for c, v in out.items() if c in CODES.values())
    if out["valid"] != int(document["cant_votos_positivos"]) - out["blank"]:
        raise ValueError(f"{document['id_ubicacion']}: the positive votes are not the list votes and the blank ones")
    if out["valid"] + out["null"] + out["blank"] != out["cast"]:
        raise ValueError(f"{document['id_ubicacion']}: the votes do not add up to the voters")
    return out


ABROAD_2021 = (
    "The department files add up to fewer voters and votes than the national file, which counts the vote abroad: "
    "{voters:,} voters, {libre:,} of the votes for the Libertad y Refundación party, and the votes of the other "
    "parties that the department files leave out. The count by place abroad is not in the files."
)


def election_2021() -> Package:
    p = Package(
        f"{SOURCE}/generales-2021",
        SOURCE,
        "The 2021 presidential election of Honduras, by department, and the seats to the Central American "
        "Parliament, from the Consejo Nacional Electoral's final count",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/generales-registry"],
    )
    b = Build(p, 2021)
    national_url, national = portal("PRE.json")
    p.artifact(raw.cached(SOURCE, national_url), GENERAL_SOURCE)
    csv_url = next(url for url in FILES if url.endswith("/resultados/HN/PRE.csv"))
    p.artifact(raw.cached(SOURCE, csv_url), GENERAL_SOURCE)
    seats_url, seats = portal("PAR_integracion.json")
    p.artifact(raw.cached(SOURCE, seats_url), GENERAL_SOURCE)
    names = department_names()
    rows: dict[str, dict[str, int]] = {}
    units: dict[str, str] = {}
    for number in range(1, 19):
        url, document = portal(f"{number}/PRE.json")
        p.artifact(raw.cached(SOURCE, url), GENERAL_SOURCE)
        code = f"{number:02d}"
        if document["id_ubicacion"] != f"HN.{number}" or document["hora_proceso"] != national["hora_proceso"]:
            raise ValueError(f"{url} is not department {code} of the same count")
        rows[code] = count(document)
        units[code] = b.unit(code, cne.title(names[code]), "hn.departamento", cne.area("hn.departamento", code))
    total = count(national)
    sums = {c: sum(r.get(c, 0) for r in rows.values()) for c in total}
    short = {c: total[c] - sums[c] for c in total if total[c] != sums[c]}
    if short["cast"] != 375 or short["LIBRE"] != 171:
        raise ValueError(f"the department files fall short of the national file by {short}")
    csv_total = {}
    for line in raw.cached(SOURCE, csv_url).path.read_text(encoding="utf-8").splitlines()[1:]:
        cells = line.split(",")
        csv_total[cells[11]] = int(cells[13])
    if {c: csv_total.get(i) for i, c in CODES.items()} != {c: total[c] for c in CODES.values()}:
        raise ValueError("the national CSV and JSON files disagree")
    b.note(
        "The Consejo Nacional Electoral's final count of 28 November 2021 (processed 30 December 2021) by department. "
        "The files print the presidential votes of each party; no candidate is named in them. The department files "
        "leave out the vote abroad, which only the national file counts. The 20 deputies to the Central American "
        "Parliament are the lists the integration file names, as the parties' shares of the presidential votes "
        "gave them."
    )
    contest = b.contest(
        "pres",
        "office",
        "President of Honduras",
        "Presidente de la República",
        "president",
        office=PRESIDENCY,
        magnitude=1,
        system=SYSTEM_FPTP,
    )
    labels = {c: OFFICIAL[c][0].upper() for c in CODES.values()}
    labels["PCSC"] = OFFICIAL["PCSC"][0].upper()
    order = [CODES[i] for i in sorted(CODES, key=int)]
    b.tally("pres", "ticket", rows, units, contest, labels, order, total)
    p.accept(
        "accounting.declared",
        ABROAD_2021.format(voters=short["cast"], libre=short["LIBRE"])
        + " Every party's figure and the voters, blank, and null totals equal the national file's.",
    )
    parlacen(b, seats)
    return b.finish()


def parlacen(b: Build, document: dict[str, Any]) -> None:
    """The Central American Parliament's 20 deputies for Honduras: seats by party and the proprietors elected."""
    p = b.p
    contest = b.contest(
        "parlacen",
        "body_seats",
        "Central American Parliament, deputies for Honduras",
        "Parlamento Centroamericano, diputados por Honduras",
        "parlacen",
        body=PARLACEN,
        magnitude=len(document["podio"]),
        completeness=None,
    )
    by_party: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in document["podio"]:
        by_party[CODES[entry["id_candidatura"].split(".")[0]]].append(entry)
    result_set = b.sets["parlacen"]
    for position, (code, entries) in enumerate(sorted(by_party.items(), key=lambda kv: -len(kv[1])), start=1):
        option = b.option("parlacen", contest, code, "list", OFFICIAL[code][0], position)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=len(entries))
        for entry in sorted(entries, key=lambda e: e["orden_cargo"]):
            tag = f"parlacen:{entry['id_candidatura']}"
            person = k("person", 2021, tag)
            name = " ".join(entry["nombre"].split())
            p.add("person", id=person, birth_precision=None)
            p.name(person, name, "es", "official")
            candidacy = k("candidacy", 2021, tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=int(entry["orden_cargo"]),
                ballot_name=name,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")


def parse() -> None:
    fetch()
    cne.geo().write()
    cne.registry().write()
    registry().write()
    election_1980().write()
    for year in (1981, 1985, 1989):
        single_ballot(year, True).write()
    single_ballot(1993, False).write()
    election_1997().write()
    election_2001().write()
    election_2005().write()
    election_2017().write()
    election_2021().write()


def emit() -> None:
    package.run("load", packages())
