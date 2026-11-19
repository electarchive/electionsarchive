"""Bolivia's presidential elections of 2005 and 2014 by polling table, from the Órgano Electoral Plurinacional's
digital Atlas Electoral.

The Atlas publishes one `votos_totales.csv` file per electoral process, with the party counts, the blank, null, and
valid ballots, and the ballots cast at each polling table (mesa), and, for later processes, the registered voters.
A file holds its table more than once, each copy under its own header line, so the package reads the first copy and
checks that the other copies add up to the same figures.

The archive's own machines saved the files, because the Atlas answers only inside Bolivia, so `fetch` reads them from
the archive's inbox (`EA_INBOX_URL`, `EA_INBOX_USER`, `EA_INBOX_PASSWORD`). The municipalities and the department
areas are the ones the referendum source (`bo.oep_referendos`) already holds, so each polling table sits inside the
municipality that source keys by its name.
"""

import csv
import functools
import io
import os
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.bo import oep_referendos
from ea.sources.bo.places import fold

SOURCE = "bo.oep_generales"
DIGITAL = "https://atlaselectoral.oep.org.bo/"
INDEX = "https://atlaselectoral.oep.org.bo/"
BOLIVIA = str(uuid5(package.NAMESPACE, "polity\x1fBO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBOL"))
MESA, MUNICIPALITY = "bo.mesa", "bo.municipio"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
FIXED = {
    "Codigo PAIS",
    "Nombre PAIS",
    "Codigo DEPARTAMENTO",
    "Nombre DEPARTAMENTO",
    "Codigo PROVINCIA",
    "Nombre PROVINCIA",
    "Codigo MUNICIPIO",
    "Nombre MUNICIPIO",
    "Codigo RECINTO",
    "Nombre RECINTO",
    "Codigo MESA",
    "Nombre MESA",
    "BLANCOS",
    "EMITIDOS",
    "INSCRITOS",
    "NULOS",
    "VALIDOS",
}
TOWNS = Path(__file__).with_name("atlas_municipios.tsv")


class Spec(NamedTuple):
    process: int
    day: date
    en: str
    es: str
    slug: str
    system: str


ELECTIONS = {
    date(2005, 12, 18): Spec(
        19,
        date(2005, 12, 18),
        "2005 Bolivian general election: President",
        "Elecciones generales de 2005: Presidente y Vicepresidente",
        "general-2005",
        "president-2005",
    ),
    date(2014, 10, 12): Spec(
        17,
        date(2014, 10, 12),
        "2014 Bolivian general election: President",
        "Elecciones generales de 2014: Presidente y Vicepresidente",
        "general-2014",
        "president-2014",
    ),
}
SYSTEMS = {
    "president-2005": (
        date(2005, 1, 1),
        date(2009, 2, 7),
        (
            "Under the constitution of 1967, as amended, a ticket that won more than half of the valid votes took the "
            "presidency. Otherwise the new Congress chose between the two leading tickets (the Atlas's count of 2005 "
            "is the popular vote only). The rule that Congress decides is not stored in the rules."
        ),
    ),
    "president-2014": (
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
    "Counts of each polling table (mesa) from the digital Atlas Electoral's votos_totales file of the process. Valid "
    "votes are the votes for the tickets; blank and null ballots are counted apart, and the ballots cast are the sum "
    "of the three. The tickets are named by the abbreviation of their party or alliance, as the file names them. The "
    "file prints no national totals and no declared outcome, so the package declares neither."
)
ABROAD = (
    " The file holds the polling tables inside Bolivia only; it has no table of the vote abroad, which the "
    "electorate of 2014 included for the first time."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def process_url(n: int) -> str:
    return f"{DIGITAL}descarga/{n}/votos_totales.csv"


SOURCE_ID = key("source", DIGITAL)
PRESIDENCY = key("office", "president")


def party(label: str) -> str:
    return key("party", fold(label))


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in ELECTIONS)]


def fetch() -> None:
    """Add the process files to the raw cache from the archive's inbox."""
    have = raw.keys(SOURCE)
    wanted = [spec.process for spec in ELECTIONS.values() if process_url(spec.process) not in have]
    if not wanted:
        return
    base = os.environ["EA_INBOX_URL"] + "/fetched/5700/"
    auth = (os.environ["EA_INBOX_USER"], os.environ["EA_INBOX_PASSWORD"])
    index = httpx.get(base + "index.json", auth=auth, timeout=60).raise_for_status().json()
    for n in wanted:
        entry = index[process_url(n)]
        content = httpx.get(base + entry["file"], auth=auth, timeout=600).raise_for_status().content
        stored = raw.store(
            SOURCE,
            process_url(n),
            content,
            suffix=".csv",
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
        if stored.sha256 != entry["sha256"]:
            raise ValueError(f"process {n}: the inbox file does not match its hash")


def parse() -> None:
    registry().write()
    for day in ELECTIONS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


# ---- reading the polling tables ------------------------------------------------------------------------------------


class Mesa(NamedTuple):
    code: str
    town: str  # the Atlas's municipality code
    counts: dict[str, int]  # by party label
    registered: int | None
    cast: int
    valid: int
    blank: int
    null: int


@functools.cache
def towns() -> dict[str, str]:
    """The code of the municipality in the referendum source, for each municipality code the Atlas prints."""
    with TOWNS.open(encoding="utf-8", newline="") as file:
        return {row["atlas_code"]: row["area_code"] for row in csv.DictReader(file, delimiter="\t")}


def _copies(n: int) -> list[tuple[list[str], list[list[str]]]]:
    text = raw.cached(SOURCE, process_url(n)).path.read_text(encoding="utf-8-sig")
    copies: list[tuple[list[str], list[list[str]]]] = []
    for row in csv.reader(io.StringIO(text), delimiter="|", quoting=csv.QUOTE_NONE):
        if not row:
            continue
        if row[0] == "Codigo PAIS":
            copies.append((row, []))
        elif not copies:
            raise ValueError(f"process {n}: a row before the first header")
        elif len(row) != len(copies[-1][0]):
            raise ValueError(f"process {n}: {len(row)} columns in {row[:12]}")
        else:
            copies[-1][1].append(row)
    return copies


@functools.cache
def mesas(n: int) -> tuple[list[str], list[Mesa]]:
    """The party labels and the polling tables of a process, from the first copy of its table."""
    copies = _copies(n)
    header, rows = copies[0]
    labels = [column for column in header if column not in FIXED]
    out: list[Mesa] = []
    seen: set[str] = set()
    for row in rows:
        cells = dict(zip(header, row, strict=True))
        counts = {label: int(cells[label]) for label in labels}
        valid, blank, null, cast = (int(cells[c]) for c in ("VALIDOS", "BLANCOS", "NULOS", "EMITIDOS"))
        code = cells["Codigo MESA"]
        if code in seen:
            raise ValueError(f"process {n}: mesa {code} twice")
        seen.add(code)
        if sum(counts.values()) != valid or valid + blank + null != cast:
            raise ValueError(f"process {n}: mesa {code} does not add up")
        if cells["Codigo MUNICIPIO"] not in towns():
            raise ValueError(f"process {n}: municipality {cells['Codigo MUNICIPIO']} {cells['Nombre MUNICIPIO']}")
        registered = int(cells["INSCRITOS"]) if "INSCRITOS" in cells else None
        out.append(Mesa(code, cells["Codigo MUNICIPIO"], counts, registered, cast, valid, blank, null))
    # Each later copy must hold the same figures, as party totals and ballot accounts.
    for other_header, other in copies[1:]:
        total = {
            label: sum(int(row[other_header.index(label)]) for row in other)
            for label in other_header
            if label in labels or label in ("VALIDOS", "BLANCOS", "NULOS", "EMITIDOS")
        }
        mine = {label: sum(m.counts[label] for m in out) for label in labels}
        mine |= {
            "VALIDOS": sum(m.valid for m in out),
            "BLANCOS": sum(m.blank for m in out),
            "NULOS": sum(m.null for m in out),
            "EMITIDOS": sum(m.cast for m in out),
        }
        if {k: v for k, v in total.items() if v or k in mine} != {k: v for k, v in mine.items() if v or k in total}:
            raise ValueError(f"process {n}: a later copy of the table adds up differently")
    return labels, out


# ---- packages ------------------------------------------------------------------------------------------------------


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional de Bolivia",
        title="Atlas Electoral de Bolivia (digital), counts by polling table",
        url=INDEX,
        license=oep_referendos.LICENSE,
        attribution=oep_referendos.ATTRIBUTION,
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Bolivian presidency, its electoral systems of 2005 and 2014, and the tickets of those elections, from "
        "the Atlas Electoral",
    )
    p.external.add(BOLIVIA)
    _source(p)
    p.add("office", id=PRESIDENCY, polity_id=BOLIVIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Bolivia", "en", "official")
    p.name(PRESIDENCY, "Presidente del Estado Plurinacional de Bolivia", "es", "native", False)
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
    labels = sorted({label for spec in ELECTIONS.values() for label in mesas(spec.process)[0]}, key=fold)
    for label in labels:
        p.add("party", id=party(label), kind="party", polity_id=BOLIVIA)
        p.name(party(label), label, "es", "abbreviation")
    for spec in ELECTIONS.values():
        p.artifact(raw.cached(SOURCE, process_url(spec.process)), SOURCE_ID)
    return p


def election(day: date) -> Package:
    spec = ELECTIONS[day]
    labels, rows = mesas(spec.process)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{spec.en} by polling table, from the Atlas Electoral de Bolivia",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {BOLIVIA, COUNTRY, MESA}
    p.uses(raw.cached(SOURCE, process_url(spec.process)))
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
    p.name(election_id, spec.en.split(":")[0], "en", "official")
    p.name(election_id, spec.es.split(":")[0], "es", "native", False)
    base = f"bo/{day}-{spec.slug.split('-')[0]}"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    contest = key("contest", day, "president")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=COUNTRY,
        magnitude=1,
        round_number=1,
        electoral_system_id=key("electoral_system", spec.system),
        default_result_kind="counted",
    )
    p.name(contest, "President of Bolivia", "en", "official")
    p.name(contest, "Presidente y Vicepresidente del Estado", "es", "native", False)
    p.add("slug", path=f"{base}/president", entity_id=contest, is_current=True)
    options = {}
    for label in labels:
        options[label] = key("option", day, label)
        p.add(
            "contest_option",
            id=options[label],
            contest_id=contest,
            kind="ticket",
            party_id=party(label),
            ballot_label=label,
        )
    result_set = key("result_set", day, "president", "mesa")
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
    method = METHOD + (ABROAD if day.year >= 2014 else "")
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=SOURCE_ID,
        locator={"files": [process_url(spec.process)]},
    )
    areas = towns()
    external_areas = {oep_referendos.area(MUNICIPALITY, code) for code in areas.values()}
    p.external |= external_areas
    values: list[tuple[str, str | None, int, int]] = []
    over = []
    for mesa in rows:
        unit = key("unit", MESA, f"{day}:{mesa.code}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BOLIVIA,
            scheme_code=MESA,
            code=f"{day}:{mesa.code}",
            area_relation="within",
            area_id=oep_referendos.area(MUNICIPALITY, areas[mesa.town]),
            kind="standard",
        )
        values += [(unit, options[label], VOTES, mesa.counts[label]) for label in labels]
        values += [
            (unit, None, BALLOTS, mesa.cast),
            (unit, None, VALID, mesa.valid),
            (unit, None, INVALID, mesa.null),
            (unit, None, BLANK, mesa.blank),
        ]
        if mesa.registered is not None:
            values.append((unit, None, REGISTERED, mesa.registered))
            if mesa.cast > mesa.registered:
                over.append(mesa.code)
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    if over:
        p.accept(
            "accounting.turnout",
            f"The file gives more ballots cast than registered voters at {len(over)} polling tables ("
            + ", ".join(sorted(over))
            + "); its figures are stored as published.",
        )
    return p
