"""Bolivia's general elections of 2009, 2020, and 2025 by polling table (mesa), from the Órgano Electoral
Plurinacional's electoral atlas and its published count.

- 2009 (6 December): the presidential count at each polling table inside Bolivia, from the Atlas Electoral's
  `votos_totales.csv` of process 82. The file holds no table of the vote abroad.
- 2020 (18 October): the presidential count and the count of the 63 uninominal and 7 special indigenous deputy
  circumscriptions at every polling table, at home and abroad, from the count file a public repository saved of the
  Órgano's publication at 10:08 on 23 October 2020 (commit 65806ad of `mauforonda/elecciones2020`).
- 2025 (17 August): the same three counts from the Atlas's `EG2025_1v.csv` (process 134), read from the web archive's
  capture of 22 August 2026.

The archive's own machines saved the files, because the Órgano's sites answer only inside Bolivia, so `fetch` reads
them from the archive's inbox (`EA_INBOX_URL`, `EA_INBOX_USER`, `EA_INBOX_PASSWORD`).

The files of 2020 and 2025 give one row for each polling table and ballot (president, uninominal deputy, special
deputy), and a table's code differs from ballot to ballot, so each ballot's table is its own reporting unit. The
vote columns of the 2025 file are named `Voto1` to `Voto12`; they follow the order in which the Órgano lists the
organizations (see `ORGS_2025`).
"""

import csv
import functools
import io
import os
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.bo import atlas_mesas, oep_generales, oep_referendos
from ea.sources.bo.places import fold, variants

SOURCE = "bo.oep_generales_mesas"
DIGITAL = oep_generales.DIGITAL
BOLIVIA = oep_generales.BOLIVIA
COUNTRY = oep_generales.COUNTRY
MESA, MUNICIPALITY, CIRCUMSCRIPTION = oep_generales.MESA, oep_generales.MUNICIPALITY, atlas_mesas.CIRCUMSCRIPTION
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
FETCHED = "/fetched/4088/"
COMMIT_2020 = "65806ad5a0195b21224789dcdefec5c421a00991"
URL_2020 = f"https://raw.githubusercontent.com/mauforonda/elecciones2020/{COMMIT_2020}/datos/20201023_100800.csv"
INBOX_2020 = "https://raw.githubusercontent.com/mauforonda/elecciones2020/master/datos/20201023_100800.csv"
URL_2025 = "https://web.archive.org/web/20260822003941id_/https://atlaselectoral.oep.org.bo/descarga/134/EG2025_1v.csv"
URL_2009 = (
    "https://web.archive.org/web/20260830162651id_/https://atlaselectoral.oep.org.bo/descarga/82/votos_totales.csv"
)
# The page of each file's origin, for the citations.
ORIGIN = {
    URL_2020: "https://github.com/mauforonda/elecciones2020/blob/" + COMMIT_2020 + "/datos/20201023_100800.csv",
    URL_2025: "https://atlaselectoral.oep.org.bo/descarga/134/EG2025_1v.csv",
    URL_2009: "https://atlaselectoral.oep.org.bo/descarga/82/votos_totales.csv",
}

# The organizations of the 2025 file's twelve vote columns, in the order of the Órgano's list of organizations with
# candidacies (AP, ADN, APB-SUMATE, ..., PDC, BIA-YUQUI, OICH). Columns 4 and 8 hold no vote in any ballot, in any
# table: they are the two organizations of the list that did not stand (NGP and MORENA).
ORGS_2025 = [
    "AP",
    "LYP-ADN",
    "APB-SUMATE",
    None,
    "LIBRE",
    "FP",
    "MAS-IPSP",
    None,
    "UNIDAD",
    "PDC",
    "BIA-YUQUI",
    "OICH",
]
LABELS_2020 = {
    "CREEMOS": "CREEMOS",
    "ADN": "ADN",
    "MAS_IPSP": "MAS-IPSP",
    "FPV": "FPV",
    "PAN_BOL": "PAN-BOL",
    "LIBRE_21": "LIBRE 21",
    "CC": "CC",
    "JUNTOS": "JUNTOS",
    "APG": "APG",
    "CPEM_B": "CPEM-B",
    "CIDOB": "CIDOB",
    "BIA_YUQUI": "BIA-YUQUI",
    "CONAMAQ": "CONAMAQ",
    "OICH": "OICH",
    "ORCAWETA": "ORCAWETA",
    "YAMINAWA": "YAMINAWA",
}
KINDS = {
    "PRESIDENTE": "P",
    "DIPUTADO CIRCUNSCRIPCIÓN UNINOMINAL": "U",
    "DIPUTADO CIRCUNSCRIPCIÓN ESPECIAL": "E",
}
HOME = "32"  # the Órgano's country code for Bolivia


class Spec(NamedTuple):
    day: date
    url: str
    ballots: tuple[str, ...]


SPECS = {
    spec.day: spec
    for spec in (
        Spec(date(2009, 12, 6), URL_2009, ("P",)),
        Spec(date(2020, 10, 18), URL_2020, ("P", "U", "E")),
        Spec(date(2025, 8, 17), URL_2025, ("P", "U", "E")),
    )
}
SYSTEMS = {
    "president": (
        date(2009, 2, 7),
        None,
        (
            "Under the constitution of 2009 a ticket wins in the first round with more than half of the valid votes, "
            "or with at least 40% and a lead of ten points over the runner-up. Otherwise the two leading tickets go "
            "to a second round. The 40% rule is not stored in the rules."
        ),
    ),
}
METHOD = (
    "Counts of each polling table (mesa) from the Órgano Electoral Plurinacional's electoral atlas and published "
    "count. Valid votes are the votes for the organizations; blank and null ballots are counted apart, and the "
    "ballots cast are the sum of the three. The organizations are named by the abbreviation of their party or "
    "alliance, as the file names them; an organization with no vote in a ballot is not listed as an option of it."
)
PRIOR = {
    date(2009, 12, 6): (
        " The file holds the polling tables inside Bolivia only; it has no table of the vote abroad and prints no "
        "registered voters. It prints no national totals and no declared outcome, so the package declares neither."
    ),
    date(2020, 10, 18): (
        " The file is the count the Órgano published at 10:08 on 23 October 2020. Its presidential valid votes "
        "include the 989 votes cast for the two withdrawn tickets (ADN and LIBRE 21), which the Órgano's final "
        "count leaves out. It declares no winner."
    ),
    date(2025, 8, 17): (
        " The file names its vote columns only by position; their organizations follow the order of the Órgano's "
        "list of organizations with candidacies. It declares no winner."
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", DIGITAL)
CHAMBER = atlas_mesas.CHAMBER
PRESIDENCY = oep_generales.PRESIDENCY
PRESIDENT_SYSTEM = key("electoral_system", "president")
UNINOMINAL_SYSTEM = key("electoral_system", "uninominal-2014")


def party(label: str) -> str:
    return key("party", fold(label))


def circumscription(day: date, code: str) -> str:
    return atlas_mesas.circumscription(day, code)


def packages() -> list[str]:
    return [
        f"{oep_generales.SOURCE}/registry",
        f"{atlas_mesas.SOURCE}/geo",
        f"{atlas_mesas.SOURCE}/registry",
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in SPECS),
    ]


def fetch() -> None:
    """Add the files to the raw cache from the archive's inbox."""
    have = raw.keys(SOURCE)
    wanted = {url: url for url in (URL_2025, URL_2009) if url not in have}
    if URL_2020 not in have:
        wanted[URL_2020] = INBOX_2020
    if not wanted:
        return
    base = os.environ["EA_INBOX_URL"] + FETCHED
    auth = (os.environ["EA_INBOX_USER"], os.environ["EA_INBOX_PASSWORD"])
    index = httpx.get(base + "index.json", auth=auth, timeout=60).raise_for_status().json()
    for url, listed in wanted.items():
        entry = index[listed]
        content = httpx.get(base + entry["file"], auth=auth, timeout=600).raise_for_status().content
        stored = raw.store(
            SOURCE,
            url,
            content,
            suffix=".csv",
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
        if stored.sha256 != entry["sha256"]:
            raise ValueError(f"{url}: the inbox file does not match its hash")


def parse() -> None:
    geo().write()
    registry().write()
    for day in SPECS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


# ---- reading the polling tables ------------------------------------------------------------------------------------


class Mesa(NamedTuple):
    ballot: str  # P, U, or E
    code: str
    country: str
    dept: str
    town: str  # the municipality as the file names it
    group: str  # the circumscription's code, for the deputies
    counts: dict[str, int]  # by organization label
    registered: int | None
    cast: int
    valid: int
    blank: int
    null: int


@functools.cache
def towns() -> dict[tuple[str, str], str]:
    """The code of the municipality in the referendum source, for each department and name variant."""
    names: dict[tuple[str, str], set[str]] = defaultdict(set)
    with oep_generales.TOWNS.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            for name in variants(row["name"]):
                names[(str(int(row["atlas_code"][:2])), name)].add(row["area_code"])
    return {k: next(iter(v)) for k, v in names.items() if len(v) == 1}


def town_code(dept: str, name: str) -> str | None:
    found = {towns().get((dept, v)) for v in variants(name.strip().replace('""', '"'))} - {None}
    return found.pop() if len(found) == 1 else None


def _rows(url: str) -> tuple[list[str], list[list[str]]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    return header, [row for row in reader if row]


@functools.cache
def mesas(day: date) -> list[Mesa]:
    """The polling tables of an election's file, one for each ballot."""
    spec = SPECS[day]
    if day.year == 2009:
        return _mesas_2009(spec.url)
    header, rows = _rows(spec.url)
    new = day.year == 2025
    c = {
        "code": "CodigoMesa" if new else "CODIGO_MESA",
        "ballot": "Descripcion" if new else "CANDIDATURA",
        "country": "CodigoPais" if new else "ID_PAIS",
        "dept": "CodigoDepartamento" if new else "ID_DEPARTAMENTO",
        "town": "NombreMunicipio" if new else "MUNICIPIO",
        "uni": "CodigoCircunscripcionU" if new else "CIRCUNSCRIPCION_UNINOMINAL",
        "spe": "CodigoCircunscripcionE" if new else "CIRCUNSCRIPCION_ESPECIAL",
        "registered": "InscritosHabilitados" if new else "INSCRITOS_HABILITADOS",
        "valid": "VotoValido" if new else "VOTO_VALIDO",
        "blank": "VotoBlanco" if new else "VOTO_BLANCO",
        "null": "TotalVotoNulo" if new else "VOTO_NULO",
        "cast": "VotoEmitido" if new else "VOTO_EMITIDO",
    }
    if new:
        columns = {f"Voto{i}": label for i, label in enumerate(ORGS_2025, 1) if label is not None}
        unused = [f"Voto{i}" for i, label in enumerate(ORGS_2025, 1) if label is None]
    else:
        columns = {column: label for column, label in LABELS_2020.items()}
        unused = []
    out: list[Mesa] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if len(row) != len(header):
            raise ValueError(f"{day}: {len(row)} columns in {row[:3]}")
        cells = dict(zip(header, row, strict=True))
        ballot = KINDS[cells[c["ballot"]]]
        code = cells[c["code"]]
        if (ballot, code) in seen:
            raise ValueError(f"{day}: table {code} of ballot {ballot} twice")
        seen.add((ballot, code))
        if any(int(cells[column]) for column in unused):
            raise ValueError(f"{day}: table {code} has votes in an unused column")
        counts = {label: int(cells[column]) for column, label in columns.items()}
        valid, blank, null, cast = (int(cells[c[k]]) for k in ("valid", "blank", "null", "cast"))
        if sum(counts.values()) != valid or valid + blank + null != cast:
            raise ValueError(f"{day}: table {code} of ballot {ballot} does not add up")
        group = {"P": "", "U": cells[c["uni"]], "E": "E" + cells[c["spe"]]}[ballot]
        out.append(
            Mesa(
                ballot,
                code,
                cells[c["country"]],
                cells[c["dept"]],
                cells[c["town"]],
                group,
                counts,
                int(cells[c["registered"]]),
                cast,
                valid,
                blank,
                null,
            )
        )
    return out


def _mesas_2009(url: str) -> list[Mesa]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig")
    copies: list[tuple[list[str], list[list[str]]]] = []
    for row in csv.reader(io.StringIO(text), delimiter="|", quoting=csv.QUOTE_NONE):
        if not row:
            continue
        if row[0] == "Codigo PAIS":
            copies.append((row, []))
        elif len(row) != len(copies[-1][0]):
            raise ValueError(f"2009: {len(row)} columns in {row[:12]}")
        else:
            copies[-1][1].append(row)
    header, rows = copies[0]
    fixed = {c for c in header if c.startswith(("Codigo ", "Nombre "))} | {"BLANCOS", "EMITIDOS", "NULOS", "VALIDOS"}
    labels = [c for c in header if c not in fixed]
    out: list[Mesa] = []
    seen: set[str] = set()
    for row in rows:
        cells = dict(zip(header, row, strict=True))
        counts = {label: int(cells[label]) for label in labels}
        valid, blank, null, cast = (int(cells[c]) for c in ("VALIDOS", "BLANCOS", "NULOS", "EMITIDOS"))
        code = cells["Codigo MESA"]
        if code in seen:
            raise ValueError(f"2009: table {code} twice")
        seen.add(code)
        if sum(counts.values()) != valid or valid + blank + null != cast:
            raise ValueError(f"2009: table {code} does not add up")
        dept = str(int(cells["Codigo DEPARTAMENTO"]))
        town = cells["Codigo MUNICIPIO"]
        out.append(Mesa("P", code, HOME, dept, town, "", counts, None, cast, valid, blank, null))
    return out


def town_area(mesa: Mesa, day: date) -> str | None:
    """The code of a domestic table's municipality, or None when the name is not one the referendum source holds."""
    if day.year == 2009:
        return oep_generales.towns().get(mesa.town)
    return town_code(mesa.dept, mesa.town)


def labels(day: date, ballot: str) -> list[str]:
    """The organizations with at least one vote in a ballot, in their order in the file."""
    rows = [m for m in mesas(day) if m.ballot == ballot]
    return [label for label in rows[0].counts if any(m.counts[label] for m in rows)]


def circumscriptions(day: date) -> list[str]:
    return sorted({m.group for m in mesas(day) if m.group}, key=lambda g: (g.startswith("E"), int(g.lstrip("E"))))


# ---- packages ------------------------------------------------------------------------------------------------------


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional de Bolivia",
        title="Atlas Electoral de Bolivia and published counts of the general elections, by polling table",
        url=DIGITAL,
        license=oep_referendos.LICENSE,
        attribution=oep_referendos.ATTRIBUTION,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The uninominal and special circumscriptions of the Chamber of Deputies in 2020 and 2025",
        depends=[f"{atlas_mesas.SOURCE}/geo"],
    )
    p.external |= {BOLIVIA, COUNTRY, CIRCUMSCRIPTION}
    for day in SPECS:
        if day.year == 2009:
            continue
        for code in circumscriptions(day):
            id = circumscription(day, code)
            p.add("area", id=id, scheme_code=CIRCUMSCRIPTION)
            p.add("area_code", area_id=id, code=f"{day.year}:{code}")
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            if code.startswith("E"):
                p.name(id, f"Circunscripción especial {code[1:]} ({day.year})", "es", "official")
                p.name(id, f"Special circumscription {code[1:]} ({day.year})", "en", "english", False)
            else:
                p.name(id, f"Circunscripción uninominal {code} ({day.year})", "es", "official")
                p.name(id, f"Uninominal circumscription {code} ({day.year})", "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral systems and the organizations of the general elections of 2009, 2020, and 2025",
        depends=[f"{oep_generales.SOURCE}/registry", f"{atlas_mesas.SOURCE}/registry"],
    )
    p.external |= {BOLIVIA, PRESIDENCY, CHAMBER, atlas_mesas.key("electoral_system", "uninominal-2014")}
    _source(p)
    for name, (since, until, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=BOLIVIA,
            valid_from=since,
            valid_to=until,
            rules={"formula": "two_round", "majority": 0.5, "advance": 2},
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    every = {label for day in SPECS for ballot in SPECS[day].ballots for label in labels(day, ballot)}
    for label in sorted(every, key=fold):
        p.add("party", id=party(label), kind="party", polity_id=BOLIVIA)
        p.name(party(label), label, "es", "abbreviation")
    for spec in SPECS.values():
        p.artifact(raw.cached(SOURCE, spec.url), SOURCE_ID)
    return p


NAMES = {
    2009: ("2009 Bolivian general election", "Elecciones generales de 2009"),
    2020: ("2020 Bolivian general election", "Elecciones generales de 2020"),
    2025: ("2025 Bolivian general election", "Elecciones generales de 2025"),
}


def election(day: date) -> Package:
    spec = SPECS[day]
    rows = mesas(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day.year} general election of Bolivia by polling table, from the Órgano Electoral Plurinacional",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {BOLIVIA, COUNTRY, MESA, PRESIDENCY, CHAMBER, atlas_mesas.key("electoral_system", "uninominal-2014")}
    p.uses(raw.cached(SOURCE, spec.url))
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=BOLIVIA,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    en, es = NAMES[day.year]
    p.name(election_id, en, "en", "official")
    p.name(election_id, es, "es", "native", False)
    base = f"bo/{day}-general"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    method = (
        METHOD
        + PRIOR[day]
        + (
            " A polling table whose municipality name the archive could not match to a municipality sits inside its "
            "department."
            if day.year > 2009
            else ""
        )
    )
    towns_used: set[str] = set()
    depts = {str(i) for i in range(1, 10)}
    values: list[tuple[str, str, str | None, int, int]] = []
    over: list[str] = []
    elsewhere = Counter[str]()
    for ballot in spec.ballots:
        members = [m for m in rows if m.ballot == ballot]
        groups = sorted({m.group for m in members}, key=lambda g: (g.startswith("E"), int(g.lstrip("E") or 0)))
        shown = labels(day, ballot)
        for group in groups:
            part = [m for m in members if m.group == group]
            tag = f"{day}:{ballot}{group}"
            contest = key("contest", tag)
            if ballot == "P":
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="office",
                    office_id=PRESIDENCY,
                    district_area_id=COUNTRY,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=PRESIDENT_SYSTEM,
                    default_result_kind="counted",
                )
                p.name(contest, "President of Bolivia", "en", "official")
                p.name(contest, "Presidente y Vicepresidente del Estado", "es", "native", False)
                p.add("slug", path=f"{base}/president", entity_id=contest, is_current=True)
                kind = "ticket"
                these = shown
            else:
                special = group.startswith("E")
                number = group.lstrip("E")
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="body_seats",
                    body_id=CHAMBER,
                    district_area_id=circumscription(day, group),
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=atlas_mesas.key("electoral_system", "uninominal-2014"),
                    default_result_kind="counted",
                )
                if special:
                    p.name(contest, f"Chamber of Deputies, special circumscription {number}", "en", "official")
                    p.name(contest, f"Cámara de Diputados, circunscripción especial {number}", "es", "native", False)
                    p.add("slug", path=f"{base}/deputies/special-{number}", entity_id=contest, is_current=True)
                else:
                    p.name(contest, f"Chamber of Deputies, uninominal circumscription {number}", "en", "official")
                    p.name(contest, f"Cámara de Diputados, circunscripción uninominal {number}", "es", "native", False)
                    p.add("slug", path=f"{base}/deputies/{number}", entity_id=contest, is_current=True)
                kind = "list"
                these = [label for label in shown if any(m.counts[label] for m in part)]
            options = {}
            for position, label in enumerate(these, 1):
                options[label] = key("option", tag, label)
                p.add(
                    "contest_option",
                    id=options[label],
                    contest_id=contest,
                    kind=kind,
                    party_id=party(label),
                    ballot_position=position,
                    ballot_label=label,
                )
            result_set = key("result_set", tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
            )
            if group in ("", groups[0]):
                p.add(
                    "note",
                    id=key("note", result_set),
                    entity_id=result_set,
                    kind="methodology",
                    body_md=method,
                    lang="en",
                )
            p.add(
                "citation",
                id=key("citation", result_set),
                entity_id=result_set,
                source_id=SOURCE_ID,
                locator={"files": [ORIGIN[spec.url]]},
            )
            for mesa in part:
                code = f"{day}:{ballot}:{mesa.code}"
                unit = key("unit", MESA, code)
                if mesa.country != HOME:
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=BOLIVIA,
                        scheme_code=MESA,
                        code=code,
                        area_relation="unlocated",
                        kind="abroad_mission",
                    )
                else:
                    town = town_area(mesa, day)
                    if town is not None:
                        area = oep_referendos.area(MUNICIPALITY, town)
                        towns_used.add(town)
                    elif mesa.dept in depts:
                        area = oep_referendos.area("bo.departamento", mesa.dept)
                        elsewhere[mesa.town.strip()] += 1
                    else:
                        raise ValueError(f"{day}: table {mesa.code} is in department {mesa.dept}")
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=BOLIVIA,
                        scheme_code=MESA,
                        code=code,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                    )
                values += [(result_set, unit, options[label], VOTES, mesa.counts[label]) for label in these]
                values += [
                    (result_set, unit, None, BALLOTS, mesa.cast),
                    (result_set, unit, None, VALID, mesa.valid),
                    (result_set, unit, None, INVALID, mesa.null),
                    (result_set, unit, None, BLANK, mesa.blank),
                ]
                if mesa.registered is not None:
                    values.append((result_set, unit, None, REGISTERED, mesa.registered))
                    if mesa.cast > mesa.registered:
                        over.append(f"{ballot}{mesa.code}")
    p.external |= {oep_referendos.area(MUNICIPALITY, code) for code in towns_used}
    p.external |= {oep_referendos.area("bo.departamento", d) for d in depts if elsewhere}
    p.frame(
        "result_value",
        pl.DataFrame(
            values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    if over:
        p.accept(
            "accounting.turnout",
            f"The file gives more ballots cast than registered voters at {len(over)} polling tables, among them "
            + ", ".join(sorted(over)[:8])
            + "; its figures are stored as published.",
        )
    return p
