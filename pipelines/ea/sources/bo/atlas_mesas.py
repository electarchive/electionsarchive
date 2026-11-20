"""Bolivian party elections by polling table (mesa), from the Órgano Electoral Plurinacional's digital Atlas Electoral.

Three processes of the digital Atlas publish a `votos_totales.csv` file of party counts at each polling table:

- process 79, the uninominal seats of the Chamber of Deputies in the general election of 30 June 2002 (68
  circumscriptions, eleven lists). The Atlas's file of the same lists by municipality (process 58) adds up to the
  presidential count, whose national totals differ from this file's, so this file is the deputies' vote;
- process 62, the uninominal seats of the Chamber of Deputies in the general election of 12 October 2014 (63
  circumscriptions, five lists), likewise not the presidential count that `bo.oep_generales` holds;
- process 48, the regional election of the Gran Chaco on 29 March 2015 (three municipalities, six lists), whose party
  vote elected the Regional Assembly.

The files give the circumscription of each polling table (the deputies' files) or its municipality (the Gran Chaco
file), the polling place's code and name, and the registered voters and ballots. A circumscription's boundaries are
not part of the files, so each circumscription is an area without a shape.

The Atlas answers only inside Bolivia, so `fetch` reads the files from the archive's inbox (`EA_INBOX_URL`,
`EA_INBOX_USER`, `EA_INBOX_PASSWORD`).
"""

import csv
import functools
import os
from datetime import date, datetime
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.bo import oep_generales, oep_referendos
from ea.sources.bo.places import fold

SOURCE = "bo.atlas_mesas"
DIGITAL = oep_generales.DIGITAL
BOLIVIA = oep_generales.BOLIVIA
COUNTRY = oep_generales.COUNTRY
MESA, MUNICIPALITY, CIRCUMSCRIPTION = oep_generales.MESA, oep_generales.MUNICIPALITY, "bo.circunscripcion"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ACCOUNTS = ("VALIDOS", "BLANCOS", "NULOS", "EMITIDOS", "INSCRITOS")
SPECIAL = {"Codigo PAIS", "Nombre PAIS"}
FETCHED = "/fetched/6661/"


class Spec(NamedTuple):
    process: int
    day: date
    kind: str  # deputies or regional


SPECS = {
    79: Spec(79, date(2002, 6, 30), "deputies"),
    62: Spec(62, date(2014, 10, 12), "deputies"),
    48: Spec(48, date(2015, 3, 29), "regional"),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def process_url(n: int) -> str:
    return f"{DIGITAL}descarga/{n}/votos_totales.csv"


SOURCE_ID = key("source", DIGITAL)
CHAMBER = key("body", "chamber_of_deputies")
ASSEMBLY = key("body", "gran_chaco_regional_assembly")
GRAN_CHACO = oep_referendos.area("bo.eta", "Gran Chaco")
SYSTEMS = {
    "uninominal-2002": (
        date(1997, 6, 1),
        date(2009, 2, 7),
        (
            "Under the constitution of 1967 as amended in 1995, 68 of the 130 deputies were elected in single-member "
            "circumscriptions, each by the most votes, and the rest from lists in the departments."
        ),
    ),
    "uninominal-2014": (
        date(2009, 2, 7),
        None,
        (
            "Under the constitution of 2009 the Chamber of Deputies has 130 members: 63 elected in single-member "
            "circumscriptions by the most votes, 60 from departmental lists, and 7 in special indigenous circumscriptions."
        ),
    ),
}
METHOD = (
    "Counts of each polling table (mesa) from the digital Atlas Electoral's votos_totales file of the process. Valid "
    "votes are the votes for the lists; blank and null ballots are counted apart, and the ballots cast are the sum "
    "of the three. The lists are named by the abbreviation of their party or alliance, as the file names them. The "
    "file prints no national totals and no declared outcome, so the package declares neither."
)
DEPUTIES_NOTE = (
    " The file gives each polling table's circumscription; the archive holds one contest for each, with the lists "
    "that took at least one vote in it. The file names no candidates and declares no winner. It holds the polling "
    "tables inside Bolivia only."
)


def party(label: str) -> str:
    return key("party", fold(label))


def circumscription(day: date, code: str) -> str:
    return key("area", CIRCUMSCRIPTION, f"{day.year}:{code}")


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{spec.day}" for spec in SPECS.values()),
        f"{oep_generales.SOURCE}/registry",
        f"{oep_generales.SOURCE}/{SPECS[62].day}",
    ]


def fetch() -> None:
    """Add the process files to the raw cache from the archive's inbox."""
    have = raw.keys(SOURCE)
    wanted = [n for n in SPECS if process_url(n) not in have]
    if not wanted:
        return
    base = os.environ["EA_INBOX_URL"] + FETCHED
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
    geo().write()
    registry().write()
    for spec in SPECS.values():
        election(spec).write()


def emit() -> None:
    package.run("load", packages())


# ---- reading the polling tables ------------------------------------------------------------------------------------


class Mesa(NamedTuple):
    code: str
    group: str  # the circumscription's code, or the municipality's code in the Atlas
    place: str  # the polling place's code
    counts: dict[str, int]  # by list label
    registered: int
    cast: int
    valid: int
    blank: int
    null: int


@functools.cache
def mesas(n: int) -> tuple[list[str], list[Mesa]]:
    """The list labels and the polling tables of a process."""
    text = raw.cached(SOURCE, process_url(n)).path.read_text(encoding="utf-8-sig")
    reader = csv.reader(text.splitlines(), delimiter="|", quoting=csv.QUOTE_NONE)
    header = next(reader)
    group = "Codigo CIRCUNSCRIPCION" if "Codigo CIRCUNSCRIPCION" in header else "Codigo MUNICIPIO"
    fixed = SPECIAL | set(ACCOUNTS) | {c for c in header if c.startswith(("Codigo ", "Nombre "))}
    labels = [c for c in header if c not in fixed]
    out: list[Mesa] = []
    seen: set[str] = set()
    for row in reader:
        if len(row) != len(header):
            raise ValueError(f"process {n}: {len(row)} columns in {row[:8]}")
        cells = dict(zip(header, row, strict=True))
        counts = {label: int(cells[label]) for label in labels}
        valid, blank, null, cast, registered = (
            int(cells[c]) for c in ("VALIDOS", "BLANCOS", "NULOS", "EMITIDOS", "INSCRITOS")
        )
        code = cells["Codigo MESA"]
        if code in seen:
            raise ValueError(f"process {n}: mesa {code} twice")
        seen.add(code)
        if sum(counts.values()) != valid or valid + blank + null != cast:
            raise ValueError(f"process {n}: mesa {code} does not add up")
        out.append(Mesa(code, cells[group], cells["Codigo RECINTO"], counts, registered, cast, valid, blank, null))
    return labels, out


# ---- packages ------------------------------------------------------------------------------------------------------


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional de Bolivia",
        title="Atlas Electoral de Bolivia (digital), counts by polling table",
        url=DIGITAL,
        license=oep_referendos.LICENSE,
        attribution=oep_referendos.ATTRIBUTION,
    )


def groups(spec: Spec) -> list[str]:
    return sorted({m.group for m in mesas(spec.process)[1]})


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The uninominal circumscriptions of the Chamber of Deputies in 2002 and 2014, as the Atlas Electoral numbers "
        "them",
    )
    p.external |= {BOLIVIA, COUNTRY}
    p.add("scheme", code=CIRCUMSCRIPTION, polity_id=BOLIVIA, kind="electoral_district", rank=2, noun="circumscription")
    for spec in SPECS.values():
        if spec.kind != "deputies":
            continue
        for code in groups(spec):
            id = circumscription(spec.day, code)
            p.add("area", id=id, scheme_code=CIRCUMSCRIPTION)
            p.add("area_code", area_id=id, code=f"{spec.day.year}:{code}")
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            p.name(id, f"Circunscripción uninominal {code} ({spec.day.year})", "es", "official")
            p.name(id, f"Uninominal circumscription {code} ({spec.day.year})", "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Chamber of Deputies, the Regional Assembly of the Gran Chaco, their uninominal systems, and the lists "
        "of the 2002, 2014, and 2015 elections, from the Atlas Electoral",
    )
    p.external.add(BOLIVIA)
    _source(p)
    p.add("body", id=CHAMBER, polity_id=BOLIVIA, kind="lower_house")
    p.name(CHAMBER, "Chamber of Deputies", "en", "official")
    p.name(CHAMBER, "Cámara de Diputados", "es", "native", False)
    p.add("body", id=ASSEMBLY, polity_id=BOLIVIA, kind="unicameral")
    p.name(ASSEMBLY, "Regional Assembly of the Gran Chaco", "en", "official")
    p.name(ASSEMBLY, "Asamblea Regional del Gran Chaco", "es", "native", False)
    for name, (since, until, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=BOLIVIA,
            valid_from=since,
            valid_to=until,
            rules={"formula": "fptp"},
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    labels = sorted({label for spec in SPECS.values() for label in mesas(spec.process)[0]}, key=fold)
    for label in labels:
        p.add("party", id=party(label), kind="party", polity_id=BOLIVIA)
        p.name(party(label), label, "es", "abbreviation")
    for spec in SPECS.values():
        p.artifact(raw.cached(SOURCE, process_url(spec.process)), SOURCE_ID)
    return p


def election(spec: Spec) -> Package:
    day = spec.day
    labels, rows = mesas(spec.process)
    deputies = spec.kind == "deputies"
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        (
            f"The uninominal seats of the {day.year} Chamber of Deputies in Bolivia"
            if deputies
            else "The 2015 regional election of the Gran Chaco in Bolivia"
        )
        + " by polling table, from the Atlas Electoral de Bolivia",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"] if deputies else [f"{SOURCE}/registry"],
    )
    p.external |= {BOLIVIA, COUNTRY, MESA}
    p.uses(raw.cached(SOURCE, process_url(spec.process)))
    if deputies and day == SPECS[62].day:
        election_id = oep_generales.key("election", day)  # the general election that `bo.oep_generales` holds
        base = f"bo/{day}-general"
        p.external.add(election_id)
    else:
        election_id = key("election", day)
        p.add(
            "election",
            id=election_id,
            polity_id=BOLIVIA,
            kind="general" if deputies else "local",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        names = (
            (f"{day.year} Bolivian general election", f"Elecciones generales de {day.year}")
            if deputies
            else ("2015 Gran Chaco regional election", "Elecciones regionales del Gran Chaco de 2015")
        )
        p.name(election_id, names[0], "en", "official")
        p.name(election_id, names[1], "es", "native", False)
        base = f"bo/{day}-general" if deputies else f"bo/{day}-gran-chaco"
        p.add("slug", path=base, entity_id=election_id, is_current=True)
    method = METHOD + (DEPUTIES_NOTE if deputies else "")
    p.add(
        "note",
        id=key("note", election_id, "counts"),
        entity_id=election_id,
        kind="methodology",
        body_md=method,
        lang="en",
    )
    if deputies:
        p.external.update(circumscription(day, code) for code in groups(spec))
    else:
        p.external.add(GRAN_CHACO)
        p.external.update(oep_referendos.area(MUNICIPALITY, code) for code in oep_generales.towns().values())
    by_group: dict[str, list[Mesa]] = {}
    for mesa in rows:
        by_group.setdefault(mesa.group if deputies else "all", []).append(mesa)
    values: list[tuple[str, str, str | None, int, int]] = []
    over = []
    for group, members in sorted(by_group.items()):
        tag = f"{day}:{group}"
        contest = key("contest", tag)
        shown = [label for label in labels if any(m.counts[label] for m in members)]
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=CHAMBER if deputies else ASSEMBLY,
            district_area_id=circumscription(day, group) if deputies else GRAN_CHACO,
            magnitude=1 if deputies else None,
            round_number=1,
            electoral_system_id=key("electoral_system", f"uninominal-{day.year}") if deputies else None,
            default_result_kind="counted",
        )
        if deputies:
            p.name(contest, f"Chamber of Deputies, uninominal circumscription {group}", "en", "official")
            p.name(contest, f"Cámara de Diputados, circunscripción uninominal {group}", "es", "native", False)
            p.add("slug", path=f"{base}/deputies/{group.lower()}", entity_id=contest, is_current=True)
        else:
            p.name(contest, "Regional Assembly of the Gran Chaco", "en", "official")
            p.name(contest, "Asamblea Regional del Gran Chaco", "es", "native", False)
            p.add("slug", path=f"{base}/assembly", entity_id=contest, is_current=True)
        options = {}
        for position, label in enumerate(shown, 1):
            options[label] = key("option", tag, label)
            p.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind="list",
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
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=SOURCE_ID,
            locator={"files": [process_url(spec.process)]},
        )
        for mesa in members:
            unit = key("unit", MESA, f"{day}:{mesa.place}:{mesa.code}")
            area = (
                circumscription(day, mesa.group)
                if deputies
                else oep_referendos.area(MUNICIPALITY, oep_generales.towns()[mesa.group])
            )
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BOLIVIA,
                scheme_code=MESA,
                code=f"{day}:{mesa.place}:{mesa.code}",
                area_relation="within",
                area_id=area,
                kind="standard",
            )
            values += [(result_set, unit, options[label], VOTES, mesa.counts[label]) for label in shown]
            values += [
                (result_set, unit, None, BALLOTS, mesa.cast),
                (result_set, unit, None, VALID, mesa.valid),
                (result_set, unit, None, INVALID, mesa.null),
                (result_set, unit, None, BLANK, mesa.blank),
                (result_set, unit, None, REGISTERED, mesa.registered),
            ]
            if mesa.cast > mesa.registered:
                over.append(mesa.code)
    if not deputies and any(m.group not in oep_generales.towns() for m in rows):
        raise ValueError("a municipality of the Gran Chaco file has no area")
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
