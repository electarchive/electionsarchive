"""Costa Rica's Tribunal Supremo de Elecciones: the "cómputo de votos" workbooks of the municipal elections of 2002,
2006, and 2010 (mayors, district síndicos and councilors, and district intendants)."""

import functools
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import fastexcel
import polars as pl

from ea import changeset, raw
from ea.package import Package
from ea.sources.cr import tse
from ea.sources.cr.tse import (
    CAST,
    PROVINCES,
    SKIP,
    SOURCE,
    VALID,
    Row,
    Table,
    cell,
    clean,
    fold,
    measure_of,
    nest,
    number,
    read,
    whole,
)

MAYOR, COUNCIL, INTENDANT = "mayor", "council", "intendant"


@dataclass(frozen=True)
class Day:
    day: date
    archive: str
    member: str  # regular expression with {province} for the province's workbook, or the one workbook of the day


DAYS = (
    Day(date(2002, 12, 1), "computovotos_diciembre2002", r"COMPUTO DE VOTOS {province}\s+DICIEMBRE 2002\s+MENU\.xls"),
    Day(date(2006, 12, 3), "computovotos_diciembre2006", r"COMPUTO DE VOTOS DICIEMBRE {province} MENU\.xls"),
    Day(date(2010, 12, 5), "computovotos_diciembre2010", r"computovotos_diciembre2010\.xlsm"),
)
PROVINCE_FILE = ("SAN JOS.", "ALAJUELA", "CARTAGO", "HEREDIA", "GUANACASTE", "PUNTARENAS", "LIM.N")


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, tse.zip_url(day.archive), suffix=".zip")


@functools.lru_cache(maxsize=16)
def sheets(day: Day, province: int) -> dict[str, pl.DataFrame]:
    """Every sheet of the workbook of a province (or, for 2010, of the whole country), cells as strings."""
    member = day.member.format(province=PROVINCE_FILE[province - 1] if province else "")
    path = raw.cached(SOURCE, tse.zip_url(day.archive)).path
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(member, n)]
        if len(names) != 1:
            raise LookupError(f"{day.archive}: {member} matches {names}")
        data = z.read(names[0])
    workbook = fastexcel.read_excel(data)
    return {
        name: workbook.load_sheet_by_name(name, header_row=None, dtypes="string").to_polars()
        for name in workbook.sheet_names
    }


KEYS = ("JUNTAS", "ELECTORADO", "TOTAL GENERAL", "TOTAL")


COLUMN_FIXES = {"TOTAL GENRAL": "TOTAL GENERAL"}
"""Misspelled headings of the accounting columns, by the folded heading."""


def table_of(sheet: str, frame: pl.DataFrame) -> Table | None:
    """The table of a sheet, with the misspelled accounting headings put right."""
    table = read(sheet, frame)
    if table is not None:
        table.columns = [COLUMN_FIXES.get(column, column) for column in table.columns]
        for row in table.rows:
            row.values = {COLUMN_FIXES.get(column, column): value for column, value in row.values.items()}
    return table


def blocks(frame: pl.DataFrame, table: Table) -> list[list[Row]]:
    """The rows of a table that a blank row separates: each is one district with its electoral districts."""
    marker = 1
    start = next(
        r
        for r in range(3, min(frame.height, 12))
        if cell(frame, r, marker).replace(".", "", 1).isdigit() and cell(frame, r, 0)
    )
    found: list[list[Row]] = []
    current: list[Row] = []
    for r in range(start, frame.height):
        first = cell(frame, r, marker)
        if not first.replace(".", "", 1).isdigit():
            if current:
                found.append(current)
                current = []
            continue
        values: dict[str, float] = {}
        for offset, label in enumerate(table.columns):
            if not label or "%" in label or label in SKIP:
                continue
            parsed = number(cell(frame, r, marker + offset))
            if parsed is not None:
                values[label] = parsed
        current.append(Row((clean(cell(frame, r, 0)),), values))
    if current:
        found.append(current)
    return found


def caption(frame: pl.DataFrame) -> str:
    """The folded caption of a table, which the sheet prints over its first rows."""
    return fold(" ".join(cell(frame, r, c) for r in range(min(4, frame.height)) for c in range(min(2, frame.width))))


@dataclass
class Found:
    """One printed table of results: the units of a contest, or of the cantons or districts of a province."""

    sheet: str
    kind: str
    province: int
    canton: str  # the canton the table is for, or "" for a table of a whole province
    columns: list[str]
    shown: list[str]
    total: dict[str, float]  # the printed total row
    leaves: list[tuple[tuple[str, ...], str, dict[str, float]]]  # names above the leaf, the leaf's name, its counts
    nodes: dict[tuple[str, ...], dict[str, float]] = field(default_factory=dict)  # the totals printed for each parent


def ancestors(ends: dict[int, int], j: int) -> list[int]:
    return [i for i in range(j) if ends[i] > i and j <= ends[i]]


def of_kind(text: str) -> str | None:
    """The contest a table's caption names, or None when the caption does not say."""
    if "INTENDENTES" in text:
        return INTENDANT
    if "CONCEJALES MUNICIPALES" in text:
        return "municipal"
    if "SINDIC" in text:
        return COUNCIL
    if "ALCALD" in text:
        return MAYOR
    return None


def tables(day: Day, province: int) -> list[Found]:
    """Every table of electoral districts in the workbook of a province, with its contest."""
    out: list[Found] = []
    numbered = {n: f for n, f in sheets(day, province).items() if re.fullmatch(r"C?\d+", n)}
    for name, frame in sorted(numbered.items(), key=lambda item: int(item[0].lstrip("C"))):
        text = caption(frame)
        if "JUNTAS" not in text or "CANDIDAT" in text or "DISTRITO ELECTORAL" not in text:
            continue
        table = table_of(name, frame)
        if table is None:
            continue
        found = Found(name, of_kind(text) or "", province, "", table.columns, table.shown, {}, [])
        parts = blocks(frame, table)
        if not parts or not fold(parts[0][0].path[0]).startswith("TOTAL "):
            raise ValueError(f"{name}: no total row")
        found.total = parts[0][0].values
        total_name = fold(parts[0][0].path[0])[len("TOTAL ") :]
        rows = [row for part in parts[1:] for row in part]
        ends = nest(rows, KEYS)
        for j, row in enumerate(rows):
            if ends[j] != j:
                continue
            chain = ancestors(ends, j)
            above = tuple(rows[i].path[0] for i in chain)
            found.leaves.append((above, row.path[0], row.values))
            for k, i in enumerate(chain):
                found.nodes[tuple(rows[c].path[0] for c in chain[: k + 1])] = rows[i].values
        if any(len(above) > 1 for above, _, _ in found.leaves):
            found.canton = ""
        else:
            found.canton = total_name
        out.append(found)
    return out


CANTON_ALIASES = {(6, "CENTRAL"): "PUNTARENAS", (2, "ALFARO RUIZ"): "ZARCERO"}
"""Cantons that the 2002 and 2006 tables name differently from the later workbooks, by province and folded name."""
DISTRICT_ALIASES = {
    (2, "ALAJUELA", "SAN MIGUEL DE SARAPIQUI"): "SARAPIQUI",
    (2, "SAN CARLOS", "CIUDAD QUESADA"): "QUESADA",
    (2, "ZARCERO", "TAPEZCO"): "TAPESCO",
    (3, "CARTAGO", "GUADALUPE"): "GUADALUPE O ARENILLA",
    (4, "SARAPIQUI", "LLANURAS DE GASPAR"): "LLANURAS DEL GASPAR",
    (5, "CANAS", "POROSAL"): "POROZAL",
    (3, "PARAISO", "LLANO SANTA LUCIA"): "LLANOS DE SANTA LUCIA",
}
"""Districts that the 2002 and 2006 tables name differently from the later workbooks, by province, canton, and name."""


def resolve(province: int, canton: str, district: str) -> tuple[int, int]:
    """The canton and district numbers of a canton and district as the 2002 and 2006 tables name them."""
    canton = CANTON_ALIASES.get((province, fold(canton)), fold(canton))
    district = DISTRICT_ALIASES.get((province, canton, fold(district)), district)
    return tse.locate(tse.numbers(), province, canton, district)


CT_DAY = DAYS[2]


@functools.cache
def district_codes() -> dict[tuple[int, int, str], int]:
    """The district number within its canton, by province, canton number, and folded name, from the 2010 code sheet."""
    frame = sheets(CT_DAY, 0)["CT"]
    found: dict[tuple[int, int, str], int] = {}
    for r in range(1, frame.height):
        province, canton, district = (int(cell(frame, r, c)) for c in (4, 5, 6))
        found[(province, canton, fold(cell(frame, r, 3)))] = district
    for (province, canton, name), district in list(found.items()):
        found.setdefault((province, canton, tse.bare(name)), district)
    return found


@functools.cache
def canton_codes() -> dict[tuple[int, str], int]:
    frame = sheets(CT_DAY, 0)["CT1"]
    return {(int(cell(frame, r, 2)), fold(cell(frame, r, 1))): int(cell(frame, r, 3)) for r in range(1, frame.height)}


@dataclass
class Unit:
    """One finest-level row of a contest: an electoral district, or in 2010 an administrative district."""

    district: str  # the TSE code of its administrative district: province, canton, and district numbers
    name: str
    values: dict[str, float]


@dataclass
class Contest:
    kind: str
    province: int
    canton: int
    district: int | None  # the district of a contest held in one district
    columns: list[str]
    shown: list[str]
    units: list[Unit]
    printed: dict[str, float] | None  # the total the table prints for the contest

    @property
    def code(self) -> str:
        return f"{self.province}{self.canton:02d}" + ("" if self.district is None else f"{self.district:02d}")


def pcd(province: int, canton: int, district: int) -> str:
    return f"{province}{canton:02d}{district:02d}"


def early(day: Day) -> list[Contest]:
    """The contests of 2002 and 2006, from the tables of electoral districts in the workbook of each province."""
    out: list[Contest] = []
    for province in range(1, 8):
        found = tables(day, province)
        mayors = {t.canton for t in found if t.kind == MAYOR}
        councils = {t.canton for t in found if t.kind == COUNCIL}
        for t in found:
            if not t.kind:  # a table whose caption is damaged: the one canton that has no table of one contest
                t.kind = MAYOR if t.canton not in mayors else COUNCIL if t.canton not in councils else ""
                if not t.kind:
                    raise ValueError(f"{day.day} {t.sheet}: cannot tell the contest")
                (mayors if t.kind == MAYOR else councils).add(t.canton)
            if t.kind == MAYOR:
                units = [
                    Unit(pcd(province, *resolve(province, t.canton, above[-1] if above else name)), name, values)
                    for above, name, values in t.leaves
                ]
                out.append(Contest(MAYOR, province, units_canton(units), None, t.columns, t.shown, units, t.total))
                continue
            groups: dict[tuple[str, ...], list[tuple[tuple[str, ...], str, dict[str, float]]]] = {}
            for leaf in t.leaves:
                above = leaf[0]
                key = above[-1:] if t.canton else above[:2] if len(above) > 1 else (above[0], leaf[1])
                groups.setdefault(key, []).append(leaf)
            for key, leaves in groups.items():
                canton = t.canton or key[0].strip()
                district = key[-1]
                number, number_district = resolve(province, canton, district)
                units = [Unit(pcd(province, number, number_district), name, values) for _, name, values in leaves]
                out.append(
                    Contest(
                        t.kind,
                        province,
                        number,
                        number_district,
                        t.columns,
                        t.shown,
                        units,
                        t.nodes.get(key),
                    )
                )
    return out


def units_canton(units: list[Unit]) -> int:
    cantons = {int(unit.district[1:3]) for unit in units}
    if len(cantons) != 1:
        raise ValueError(f"a canton's table spans cantons {sorted(cantons)}")
    return cantons.pop()


def modern(kind: str, names: list[str]) -> list[Contest]:
    """The contests of 2010, from the tables whose rows are the administrative districts under each canton."""
    out: list[Contest] = []
    cantons = canton_codes()
    districts = district_codes()
    for sheet in names:
        frame = sheets(CT_DAY, 0)[sheet]
        table = table_of(sheet, frame)
        if table is None:
            raise LookupError(sheet)
        province = canton = 0
        rows: dict[tuple[int, int], list[Unit]] = {}
        totals: dict[tuple[int, int], dict[str, float]] = {}
        for index, row in enumerate(table.rows):
            label = fold(row.path[0])
            bare_name = fold(re.sub(r"^PROVINCIA\s+", "", label))
            if label == "COSTA RICA" or (bare_name in PROVINCES and (index == 0 or kind == INTENDANT)):
                if label != "COSTA RICA":
                    province = PROVINCES.index(bare_name) + 1
                    canton = 0
                continue
            if found := re.match(r"^CANTON\s+(\d+)\s+(.*)$", label):
                canton = int(found[1])
                if cantons.get((province, fold(found[2]))) != canton:
                    raise ValueError(f"{sheet}: canton {label} is not canton {canton} of province {province}")
                totals[province, canton] = row.values
                rows[province, canton] = []
                continue
            if kind == INTENDANT:
                matches = [
                    (c, d)
                    for (p, c, name), d in districts.items()
                    if p == province and (label == name or label.startswith(f"{name} DE "))
                ]
                if len(matches) != 1:
                    raise LookupError(f"{sheet}: {label} matches {matches}")
                canton = matches[0][0]
                rows.setdefault((province, canton), [])
                rows[province, canton].append(Unit(pcd(province, canton, matches[0][1]), row.path[0], row.values))
                continue
            number = districts.get((province, canton, label)) or districts.get((province, canton, tse.bare(label)))
            if number is None:
                raise LookupError(f"{sheet}: district {label} of canton {canton} in province {province}")
            rows[province, canton].append(Unit(pcd(province, canton, number), row.path[0], row.values))
        for (province, canton), units in rows.items():
            if kind == MAYOR:
                out.append(
                    Contest(MAYOR, province, canton, None, table.columns, table.shown, units, totals[province, canton])
                )
            else:
                for unit in units:
                    out.append(
                        Contest(
                            kind,
                            province,
                            canton,
                            int(unit.district[3:]),
                            table.columns,
                            table.shown,
                            [unit],
                            None,
                        )
                    )
    return out


def contests(day: Day) -> list[Contest]:
    if day.day.year < 2010:
        return early(day)
    return [
        *modern(MAYOR, [f"2.{n}" for n in range(9, 16)]),
        *modern(COUNCIL, [f"3.{n}" for n in range(9, 16)]),
        *modern(INTENDANT, ["4.1"]),
    ]


# ---------------------------------------------------------------------------------------------------------------
# The packages

COSTA_RICA, COUNTRY, TSE_SOURCE, key, area, party = (
    tse.COSTA_RICA,
    tse.COUNTRY,
    tse.TSE_SOURCE,
    tse.key,
    tse.area,
    tse.party,
)
MAYOR_OFFICE = key("office", "mayor")
INTENDANT_OFFICE = key("office", "intendant")
DISTRICT_COUNCIL = key("body", "district_council")
"""The generic rows the first version of the municipal package held. No contest points at them any longer, but they
stay until a later version drops them, after the election packages that referenced them have been reloaded."""
MUNICIPAL_DISTRICT_COUNCIL = key("body", "municipal_district_council")
"""The body of the councilors a district with an intendency elects, which the registry holds no seat for."""
FAMILY = {MAYOR: tse.MAYOR, COUNCIL: tse.DISTRICT_COUNCIL, INTENDANT: tse.INTENDANT}
"""The registry family of the seat each kind of contest here fills."""


def seat_code(contest: Contest) -> str:
    """The code of the seat a contest fills: its canton for a mayor, its district for the others."""
    return contest.code


@functools.cache
def registry_seats() -> dict[str, dict[str, str]]:
    """The seats the municipal registry holds, by family: the code of each and its name."""
    return tse.municipal_places()


def seat_of(contest: Contest) -> str:
    if contest.kind not in FAMILY:
        return MUNICIPAL_DISTRICT_COUNCIL
    return tse.seat(FAMILY[contest.kind], seat_code(contest))


def missing_seats() -> dict[tuple[str, str], str]:
    """The seats the three elections fill that the registry lacks, by family and code, with the name of the place."""
    held = registry_seats()
    cantons, districts = names()
    found: dict[tuple[str, str], str] = {}
    for day in DAYS:
        for contest in contests_of(day.day):
            if contest.kind not in FAMILY:
                continue
            family, code = FAMILY[contest.kind], seat_code(contest)
            if code in held[family] or (family, code) in found:
                continue
            canton = cantons[f"{contest.province}{contest.canton:02d}"]
            found[family, code] = (
                canton
                if contest.district is None
                else f"{districts[pcd(contest.province, contest.canton, contest.district)]} ({canton})"
            )
    return found


MUNICIPAL_SOURCE_NOTE = (
    "Counted by the Tribunal Supremo de Elecciones, as published in its workbooks of the vote count (cómputo de "
    "votos) of the municipal elections. The registered electorate (electorado), the votes received (ballots cast), "
    "the valid votes by party, the null votes (nulos), and the blank votes (blancos) are stored for each unit as "
    "printed. Valid votes exclude the blank votes. The number of polling tables (juntas) in each unit is not stored"
)
ZEROS = (
    "The workbooks print a zero for every party that has no votes in a table, so a party appears as an option of a "
    "contest only where it has votes there. "
)
BALLOT = "The table of each canton lists the parties on its ballot, and every one of them is an option. "
ELECTORAL_DISTRICTS = (
    " The units are the electoral districts (distritos electorales) that the Tribunal prints under each administrative "
    "district, the finest level it publishes for this election."
)
ADMINISTRATIVE_DISTRICTS = (
    " The units are the administrative districts (distritos), the finest level the Tribunal publishes for this "
    "election."
)
TITLES = {
    MAYOR: ("Mayor of {place}", "Alcalde de {place}"),
    COUNCIL: ("District síndico and councilors: {place}", "Síndico y concejales de distrito: {place}"),
    INTENDANT: ("District intendant: {place}", "Intendente de distrito: {place}"),
    "municipal": ("Municipal district council: {place}", "Concejo municipal de distrito: {place}"),
}
KIND_NOTES = {
    MAYOR: " Voters in each canton choose a mayor (alcalde) with a vote for a party's candidate.",
    COUNCIL: (" Voters in each district mark one party for its síndico and councilors (concejales) of the district."),
    INTENDANT: (
        " Voters in a district that has an intendency choose an intendant (intendente) with a vote for a party's "
        "candidate{shared}"
    ),
    "municipal": " Voters in a district that has an intendency mark one party for its municipal district councilors.",
}


def method(day: date, kind: str) -> str:
    text = (
        MUNICIPAL_SOURCE_NOTE
        + ". "
        + (BALLOT if day.year < 2010 and kind == MAYOR else ZEROS)
        + KIND_NOTES[kind].strip()
    )
    if kind == INTENDANT:
        text = text.replace(
            "{shared}",
            ", which also elects that party's municipal district councilors; the table prints one count for both."
            if day.year == 2002
            else ".",
        )
    return text + (ADMINISTRATIVE_DISTRICTS if day.year == 2010 else ELECTORAL_DISTRICTS)


@functools.cache
def contests_of(day: date) -> list[Contest]:
    return contests(next(d for d in DAYS if d.day == day))


def national_ids() -> tuple[set[str], set[str]]:
    """The ids of the parties the national and municipal registries hold, and of the areas the geo package holds."""
    held = [tse.registry(), tse.municipal_registry()]
    parties = {str(r["id"]) for p in held for r in p.rows.get("party", [])}
    return parties, {str(r["id"]) for r in tse.geo().rows["area"]}


def names() -> tuple[dict[str, str], dict[str, str]]:
    _, cantons, districts = tse.places()
    return cantons, districts


def seen_columns() -> dict[str, str]:
    """Every party column of the three elections with the name the table prints."""
    found: dict[str, str] = {}
    for day in DAYS:
        for contest in contests_of(day.day):
            for column, text in zip(contest.columns, contest.shown, strict=True):
                if measure_of(column) is None:
                    found.setdefault(column, text)
    return found


def sources(p: Package) -> None:
    for day in DAYS:
        p.uses(raw.cached(SOURCE, tse.zip_url(day.archive)))


def municipal() -> Package:
    p = Package(
        f"{SOURCE}/municipal",
        SOURCE,
        "The offices, councils, parties, and places that Costa Rica's municipal elections of 2002, 2006, and 2010 add "
        "to the Tribunal Supremo de Elecciones' national records",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", tse.MUNICIPAL_REGISTRY],
    )
    p.external |= {COSTA_RICA, COUNTRY}
    sources(p)
    for day in DAYS:
        p.artifact(raw.cached(SOURCE, tse.zip_url(day.archive)), TSE_SOURCE)
    for id, text, native in (
        (MAYOR_OFFICE, "Mayor of a canton of Costa Rica", "Alcalde"),
        (INTENDANT_OFFICE, "Intendant of a district of Costa Rica", "Intendente de distrito"),
    ):
        p.add("office", id=id, polity_id=COSTA_RICA, kind="mayor")
        p.name(id, text, "en", "official")
        p.name(id, native, "es", "native", False)
    p.add("body", id=DISTRICT_COUNCIL, polity_id=COSTA_RICA, kind="council")
    p.name(DISTRICT_COUNCIL, "District council of Costa Rica", "en", "official")
    p.name(DISTRICT_COUNCIL, "Concejo de distrito", "es", "native", False)
    for (family, code), text in sorted(missing_seats().items()):
        english, native, kind = tse.AREAS[family]
        id = tse.seat(family, code)
        if kind == "council":
            p.add("body", id=id, polity_id=COSTA_RICA, kind="council")
        else:
            p.add("office", id=id, polity_id=COSTA_RICA, kind=kind)
        p.name(id, f"{english} of {text}", "en", "official")
        p.name(id, f"{native} de {text}", "es", "native", False)
    if any(c.kind not in FAMILY for day in DAYS for c in contests_of(day.day)):
        p.add("body", id=MUNICIPAL_DISTRICT_COUNCIL, polity_id=COSTA_RICA, kind="council")
        p.name(MUNICIPAL_DISTRICT_COUNCIL, "Municipal district council of Costa Rica", "en", "official")
        p.name(MUNICIPAL_DISTRICT_COUNCIL, "Concejo municipal de distrito", "es", "native", False)
    parties, areas = national_ids()
    for column, text in sorted(seen_columns().items()):
        id = party(column)
        if id not in parties:
            p.add("party", id=id, kind="party", polity_id=COSTA_RICA)
            p.name(id, tse.title(text), "es", "official")
    cantons, districts = names()
    seen: set[str] = set()
    for day in DAYS:
        for contest in contests_of(day.day):
            for unit in contest.units:
                code = unit.district
                for scheme, at, parent, names_ in (
                    ("cr.canton", code[:3], area("cr.provincia", code[:1]), cantons),
                    ("cr.distrito", code, area("cr.canton", code[:3]), districts),
                ):
                    id = area(scheme, at)
                    if id in areas or id in seen:
                        continue
                    seen.add(id)
                    p.add("area", id=id, scheme_code=scheme)
                    p.add("area_code", area_id=id, code=at)
                    p.add("area_parent", area_id=id, parent_id=parent)
                    p.name(id, names_.get(at, f"Code {at}"), "es", "official")
    return p


TOTALS: dict[str, dict[str, dict[str, int]]] = {}
GAPS: dict[str, dict[str, tuple[int, int]]] = {}
UNIT_SCHEME = {2010: ("cr.distrito", "equals")}


def label_measure(label: str) -> int | None:
    """The measure of a row of a printed total, or None for a party, or 0 for a row to leave out."""
    if label.startswith(("VOTOS VALIDOS", "VOTOS POR PARTIDO", "TOTAL VOTOS VALIDOS")):
        return VALID
    if label == "TOTAL VOTOS RECIBIDOS":
        return CAST
    if label.startswith("PORCENTAJE"):
        return 0
    return measure_of(label)


@functools.cache
def elected(year: int) -> dict[tuple[str, int, int, int], tuple[str, str, str]]:
    """The mayors and intendants the 2010 workbook lists as elected: by kind and codes, the party, name, and sex."""
    frame = sheets(CT_DAY, 0)["CE"]
    found: dict[tuple[str, int, int, int], tuple[str, str, str]] = {}
    for r in range(1, frame.height):
        kind = {"ALCALDE": MAYOR, "INTENDENTE": INTENDANT}.get(cell(frame, r, 14))
        if kind is None:
            continue
        province, canton, district = (int(cell(frame, r, c)) for c in (0, 1, 2))
        found[(kind, province, canton, 0 if kind == MAYOR else district)] = (
            cell(frame, r, 5),
            cell(frame, r, 9),
            cell(frame, r, 10),
        )
    return found


def election(day: date) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Costa Rica's municipal elections of {day:%-d %B %Y} by "
        f"{'administrative' if day.year == 2010 else 'electoral'} district from the Tribunal Supremo de Elecciones' "
        "vote count",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", tse.MUNICIPAL_REGISTRY, f"{SOURCE}/municipal"],
    )
    p.external |= {COSTA_RICA, COUNTRY}
    sources(p)
    cantons, districts = names()
    scheme, relation = UNIT_SCHEME.get(day.year, ("cr.distrito_electoral", "within"))
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=COSTA_RICA, kind="local", date_from=day, date_to=day, status="held")
    path = f"cr/{day}-municipal"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{day.year} Costa Rican municipal elections", "en", "official")
    p.name(election_id, f"Elecciones municipales {day.year}", "es", "native", False)
    units: dict[str, str] = {}
    unit_rows: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    slugs: set[str] = set()
    comparison: dict[str, dict[str, int]] = {}
    gaps: dict[str, tuple[int, int]] = {}
    winners = elected(day.year) if day.year == 2010 else {}
    for contest in contests_of(day):
        everywhere = day.year < 2010 and contest.kind == MAYOR
        columns = [
            (column, text)
            for column, text in zip(contest.columns, contest.shown, strict=True)
            if measure_of(column) is None
            and (everywhere or any(unit.values.get(column, 0) > 0 for unit in contest.units))
        ]
        code = contest.code
        contest_id = key("contest", day, contest.kind, code)
        result_set = key("result_set", day, contest.kind, code)
        canton_name = cantons[f"{contest.province}{contest.canton:02d}"]
        place = (
            canton_name
            if contest.district is None
            else f"{districts[pcd(contest.province, contest.canton, contest.district)]}, {canton_name}"
        )
        office = contest.kind in (MAYOR, INTENDANT)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="office" if office else "list_allocation",
            office_id=seat_of(contest) if office else None,
            body_id=None if office else seat_of(contest),
            district_area_id=area("cr.canton", f"{contest.province}{contest.canton:02d}")
            if contest.district is None
            else area("cr.distrito", pcd(contest.province, contest.canton, contest.district)),
            magnitude=1 if office else None,
            round_number=1,
            default_result_kind="counted",
        )
        english, spanish = TITLES[contest.kind]
        p.name(contest_id, english.format(place=place), "en", "official")
        p.name(contest_id, spanish.format(place=place), "es", "native", False)
        slug = f"{path}/{contest.kind}-{changeset.slug(place)}"
        if slug in slugs:
            slug = f"{slug}-{code}"
        slugs.add(slug)
        p.add("slug", path=slug, entity_id=contest_id, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=TSE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=method(day, contest.kind),
            lang="en",
        )
        options: dict[str, str] = {}
        for column, text in columns:
            option = options[column] = key("option", day, contest.kind, code, column)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="list",
                party_id=party(column),
                ballot_position=len(options),
                ballot_label=text,
            )
        sums: Counter[tuple[str | None, int]] = Counter()
        counts: Counter[str] = Counter()
        for unit in contest.units:
            base = f"{day}:{unit.district}:{fold(unit.name)}"
            counts[base] += 1
            unit_code = base if counts[base] == 1 else f"{base}#{counts[base]}"
            if unit_code not in units:
                unit_id = units[unit_code] = key("unit", unit_code)
                unit_rows.append(
                    {
                        "id": unit_id,
                        "polity_id": COSTA_RICA,
                        "scheme_code": scheme,
                        "code": unit_code,
                        "area_relation": relation,
                        "area_id": area("cr.distrito", unit.district),
                        "kind": "standard",
                        "name": tse.title(unit.name),
                    }
                )
            for column, value in unit.values.items():
                measure = label_measure(column)
                if measure == 0 or (measure is None and column not in options):
                    continue
                count = whole(value)
                option = options[column] if measure is None else None
                measure = 1 if measure is None else measure
                sums[(option, measure)] += count
                values.append(
                    {
                        "result_set_id": result_set,
                        "unit_id": units[unit_code],
                        "option_id": option,
                        "measure": measure,
                        "value": count,
                    }
                )
        for column, value in (contest.printed or {}).items():
            measure = label_measure(column)
            if measure == 0 or (measure is None and column not in options):
                continue
            option = options[column] if measure is None else None
            measure = 1 if measure is None else measure
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=whole(value))
            if sums[(option, measure)] != whole(value):
                gaps[f"{contest.kind}:{code}:{column}"] = (sums[(option, measure)], whole(value))
        total = comparison.setdefault(contest.kind, {})
        for (option, measure), count in sums.items():
            label = (
                {2: "registered", 3: "cast", 4: "valid", 5: "invalid", 6: "blank"}[measure]
                if option is None
                else next(c for c, o in options.items() if o == option)
            )
            total[label] = total.get(label, 0) + count
        win = winners.get(
            (contest.kind, contest.province, contest.canton, 0 if contest.district is None else contest.district)
        )
        if win is not None:
            column = next((c for c, _ in columns if c == fold(win[0])), None)
            if column is None:
                raise LookupError(f"{day} {contest.kind} {code}: elected party {win[0]} has no votes")
            person = key("person", day, contest.kind, code)
            p.add("person", id=person, gender={"M": "male", "F": "female"}.get(win[2]))
            p.name(person, tse.title(win[1]), "es", "official")
            candidacy = key("candidacy", day, contest.kind, code)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[column],
                role="head",
                ballot_name=win[1],
                member_of_party_id=party(column),
                is_independent=False,
            )
            p.add(
                "outcome", result_set_id=result_set, option_id=options[column], candidacy_id=candidacy, kind="elected"
            )
    if gaps:
        p.accept(
            "accounting.declared",
            "the Tribunal's own rows disagree on blank votes: "
            + "; ".join(f"{found[0]} sums to {found[1][0]} but prints {found[1][1]}" for found in sorted(gaps.items())),
        )
    p.frame("reporting_unit", pl.DataFrame(unit_rows, infer_schema_length=None).drop("name"))
    for row in unit_rows:
        p.name(str(row["id"]), str(row["name"]), "es", "official")
    frame = pl.DataFrame(values, infer_schema_length=None).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    TOTALS[str(day)] = comparison
    GAPS[str(day)] = gaps
    tse.over(p, frame)
    return p


def national(day: Day) -> dict[str, dict[str, float]]:
    """The national totals the Tribunal prints for each contest: by kind, for each accounting measure or party."""
    found: dict[str, dict[str, float]] = {}
    if day.day.year == 2010:
        for kind, sheet in ((MAYOR, "2.1"), (COUNCIL, "3.1")):
            found[kind] = across(sheets(day, 0)[sheet])
        table = table_of("4.1", sheets(day, 0)["4.1"])
        if table is None:
            raise LookupError("4.1")
        found[INTENDANT] = table.rows[0].values
        return found
    for frame in sheets(day, 1).values():
        text = caption(frame)
        if "COSTA RICA" in text and "POR PROVINCIA" in text and "JUNTAS" in text and "CANDIDAT" not in text:
            found[of_kind(text) or ""] = across(frame)
    return found


def across(frame: pl.DataFrame) -> dict[str, float]:
    """The Costa Rica column of a table whose columns are provinces, by the folded label of each row."""
    found: dict[str, float] = {}
    for r in range(6, frame.height):
        label = fold(cell(frame, r, 0))
        value = number(cell(frame, r, 1))
        if label and value is not None and "%" not in label and not label.startswith(("FUENTE", "NOTA")):
            found[COLUMN_FIXES.get(label, label)] = value
    return found


def compare(day: Day) -> dict[str, list[tuple[str, int, int]]]:
    """For each contest, the figures where the sums of the units differ from the Tribunal's national totals."""
    mine = TOTALS[str(day.day)]
    out: dict[str, list[tuple[str, int, int]]] = {}
    for kind, printed in national(day).items():
        sums = dict(mine.get(kind, {}))
        if kind == COUNCIL:  # in 2006 the national table adds the councils of districts that have an intendency
            for label, count in mine.get("municipal", {}).items():
                sums[label] = sums.get(label, 0) + count
        differences: list[tuple[str, int, int]] = []
        for label, value in printed.items():
            measure = label_measure(label)
            if measure == 0:
                continue
            name = {2: "registered", 3: "cast", 4: "valid", 5: "invalid", 6: "blank"}[measure] if measure else label
            if sums.get(name, 0) != round(value):
                differences.append((name, sums.get(name, 0), round(value)))
        out[kind] = differences
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/municipal", *(f"{SOURCE}/{day.day}" for day in DAYS)]


def parse() -> None:
    tse.geo().write()
    tse.registry().write()
    tse.municipal_registry().write()
    municipal().write()
    for day in DAYS:
        election(day.day).write()
    for day in DAYS:
        print(day.day, compare(day), GAPS[str(day.day)])


def emit() -> None:
    from ea import package

    package.run("load", packages())
