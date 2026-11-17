"""Guatemala's popular consultations (consultas populares) of 1994 and 2018 by municipality.

The Tribunal Supremo Electoral's memoria of each consultation prints the result in every municipality, and the
Distrito Central apart from the rest of the department of Guatemala: the votes for yes (SÍ) and no, the null and
blank votes, and the votes cast. The 2018 memoria is typeset with a text layer, read here with word boxes. The 1994
memoria is a scan, read by OCR in `gt.consulta_scan`, with every municipality's figures checked against the
arithmetic the memoria itself prints (yes plus no make the valid votes, which with the null and blank votes make the
votes cast). Each stored number keeps its page and its box on the page.

The 1999 consultation, whose memoria is a scan of four questions by municipality, is not read yet.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.gt import memoria as base
from ea.sources.gt import tse
from ea.sources.gt.memoria import Word

SOURCE = "gt.consulta"
GUATEMALA = tse.GUATEMALA
COUNTRY = tse.COUNTRY
SITE = tse.SITE
LICENSE = tse.LICENSE
FILES = "images/memoriaselec/me"

VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6
FIELDS = ("yes", "no", "null", "blank", "cast")
"""A municipality's printed cells: yes, no, null, blank, and votes cast."""
MEASURES = {"null": INVALID, "blank": BLANK, "cast": CAST, "registered": REGISTERED}


class Memoria(NamedTuple):
    day: date
    file: str
    pages: tuple[int, int]
    """The first and last page of the memoria's results by municipality."""
    layout: str


MEMORIAS = {
    1994: Memoria(date(1994, 1, 30), "1993-1994_Consulta_Popular_-_%20Eleccion_de_Diputados", (60, 240), "scan1994"),
    2018: Memoria(date(2018, 4, 15), "2018_Memoria_Consulta_Popular", (212, 226), "text"),
}


def url(year: int) -> str:
    return f"{SITE}/{FILES}/{MEMORIAS[year].file}.pdf"


def path(year: int) -> Path:
    return raw.cached(SOURCE, url(year)).path


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for year in MEMORIAS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def _scans() -> ModuleType:
    from ea.sources.gt import consulta_scan

    return consulta_scan


# What a memoria prints ---------------------------------------------------------------------------------------------


@dataclass
class Tally:
    """One ballot question's results as a memoria prints them."""

    cells: dict[str, dict[str, Word]] = field(default_factory=dict)
    """Each municipality's cells by field; a municipality is its TSE code, and 0101 is the Distrito Central."""
    registered: dict[str, Word] = field(default_factory=dict)
    declared: dict[str, int] = field(default_factory=dict)
    """The national totals the memoria prints, by field."""
    departments: dict[int, dict[str, int]] = field(default_factory=dict)
    """The department totals the memoria prints, by department code; department 1 leaves out the Distrito Central."""
    left_out: list[str] = field(default_factory=list)
    """Municipalities whose figures could not be read so that they add up, with the reason."""


ACCOUNT = re.compile(r"[^A-Z]")


def _read2018() -> dict[str, Tally]:
    first, last = MEMORIAS[2018].pages
    pages = base.words(path(2018), first, last)
    tally = Tally()
    section = ""
    rows: dict[str, list[tuple[str, dict[str, Word], int]]] = {}
    for number in sorted(pages):
        for row in base.lines(pages[number]):
            text = base.text(row)
            heading = re.fullmatch(r"Departamento de (.+)", text)
            if text.startswith("Resumen de resultados"):
                section = ""
            if heading:
                section = heading.group(1)
                continue
            numbers = [w for w in row if base.is_number(w.text)]
            if len(numbers) != 6:
                continue
            name = " ".join(w.text for w in row if not base.is_number(w.text))
            values = dict(zip((*FIELDS[:4], "invalid", "cast"), numbers, strict=True))
            printed = {k: int(w.text.replace(",", "")) for k, w in values.items()}
            folded = base.fold(name)
            if folded == "RESULTADO NACIONAL":
                tally.declared = printed
            elif folded == "TOTAL DEPARTAMENTO":
                tally.departments[base.department_code(section)] = printed
            elif folded == "DISTRITO CENTRAL":
                tally.cells["0101"] = values
            elif folded == "TOTAL NACIONAL" or not section:
                continue
            else:
                rows.setdefault(section, []).append((name, values, number))
    for section, found in rows.items():
        dep = base.department_code(section)
        expected = [
            (town, code) for town, code in base.municipalities()[base.department(section) or ""] if code != "0101"
        ]
        if len(found) != len(expected):
            raise ValueError(f"{section}: {len(found)} rows for {len(expected)} municipalities")
        for (name, values, number), (town, code) in zip(found, expected, strict=True):
            folded = base.fold(name)
            if not (
                town.startswith(folded) or folded.startswith(town) or SequenceMatcher(None, folded, town).ratio() > 0.8
            ):
                raise ValueError(f"page {number}: {name!r} is not {town!r} ({dep})")
            tally.cells[code] = values
    return {"question": tally}


QUESTIONS = {
    1994: [
        (
            "question",
            "Ratificación de las reformas a la Constitución Política",
            "Ratification of the amendments to the Constitution",
            "reformas-constitucionales",
            "mandatory_referendum",
        )
    ],
    2018: [
        (
            "question",
            "Diferendo territorial, insular y marítimo entre Guatemala y Belice",
            "Territorial, insular, and maritime dispute between Guatemala and Belize",
            "diferendo-belice",
            "referendum",
        )
    ],
}
ELECTION = {
    1994: (
        "1994 Guatemalan popular consultation on constitutional amendments",
        "Consulta Popular de 1994: reformas a la Constitución",
    ),
    2018: (
        "2018 Guatemalan popular consultation on the dispute with Belize",
        "Consulta Popular de 2018: diferendo territorial con Belice",
    ),
}
TEXTS = {
    2018: (
        "¿Está usted de acuerdo que cualquier reclamo legal de República de Guatemala en contra de Belice sobre "
        "territorios continentales e insulares y cualesquiera áreas marítimas correspondientes a dichos territorios "
        "sea sometido a la Corte Internacional de Justicia para su resolución definitiva y que ésta determine las "
        "fronteras de los respectivos territorios y áreas de las Partes?"
    ),
}
METHOD = {
    1994: (
        "Read by OCR from the scanned memoria of the Tribunal Supremo Electoral, which prints a card for each "
        "municipality and for the Distrito Central. The consultation of 30 January 1994 asked voters to ratify the "
        "amendments to the Constitution that Congress had approved in November 1993, in a single question, as "
        "Article 173 and Article 280 of the Constitution provide. "
    ),
    2018: (
        "Read from the text layer of the Tribunal Supremo Electoral's memoria, which prints the results by "
        "department and municipality, with the Distrito Central apart. The ballot asked one question. The memoria "
        "also prints a column of invalid ballots (inválidos), which it adds into the votes cast but does not define, "
        "and which has no measure here; the stored figures are the votes for yes, no, null votes, blank votes, and "
        "the votes cast. "
    ),
}
SCAN_NOTE = (
    "Each stored number keeps its page and its box on the page. A municipality is stored only where its readings "
    "add up: yes and no make the valid votes, which with the null and blank votes make the votes cast. "
)


def _read(year: int) -> dict[str, Tally]:
    if year == 2018:
        return _read2018()
    return _scans().read(year)


@cache
def read(year: int) -> dict[str, Tally]:
    return _read(year)


def number(w: Word) -> int:
    return int(w.text.replace(",", ""))


def totals(tally: Tally) -> dict[str, int]:
    return {f: sum(number(c[f]) for c in tally.cells.values() if f in c) for f in (*FIELDS, "invalid")}


def compare(year: int) -> dict[str, object]:
    """Each question's municipal sums against the totals the memoria prints for the nation and its departments."""
    report: dict[str, object] = {}
    for name, tally in read(year).items():
        mine = totals(tally)
        diff = {f: (mine[f], v) for f, v in tally.declared.items() if mine.get(f) != v}
        deps: dict[int, dict[str, tuple[int, int]]] = {}
        for dep, printed in tally.departments.items():
            rows = [c for code, c in tally.cells.items() if (int(code[:2]) or 1) == dep and code != "0101"]
            sums = {f: sum(number(c[f]) for c in rows if f in c) for f in printed}
            off = {f: (sums[f], v) for f, v in printed.items() if sums[f] != v}
            if off:
                deps[dep] = off
        report[name] = {"nation": diff, "departments": deps, "left_out": tally.left_out, "units": len(tally.cells)}
    return report


# The packages -------------------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{SOURCE}/{m.day}" for m in MEMORIAS.values()]


def election(year: int) -> Package:
    m = MEMORIAS[year]
    tallies = read(year)
    p = Package(
        f"{SOURCE}/{m.day}",
        SOURCE,
        f"Guatemala's popular consultation of {m.day:%-d %B %Y} by municipality from the Tribunal Supremo Electoral's "
        "memoria",
        depends=["gt.tse/geo"],
    )
    p.external |= {GUATEMALA, COUNTRY}
    artifact = raw.cached(SOURCE, url(year))
    source = key("source", year)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Supremo Electoral",
        title=f"Memoria de la Consulta Popular {year}",
        url=url(year),
        license=LICENSE,
        license_url=f"{SITE}/informacion-publica",
        attribution="Tribunal Supremo Electoral (TSE), Guatemala",
    )
    artifact_id = p.artifact(artifact, source)
    english, spanish = ELECTION[year]
    election_id = key("election", m.day)
    p.add(
        "election",
        id=election_id,
        polity_id=GUATEMALA,
        kind="referendum",
        date_from=m.day,
        date_to=m.day,
        status="held",
    )
    p.name(election_id, english, "en", "official")
    p.name(election_id, spanish, "es", "native", False)
    path_ = f"gt/{m.day}-consulta-popular"
    p.add("slug", path=path_, entity_id=election_id, is_current=True)
    pages = {w.page for t in tallies.values() for c in t.cells.values() for w in c.values()}
    pages |= {w.page for t in tallies.values() for w in t.registered.values()}
    for page in sorted(pages):
        width, height = base.SIZES[(artifact.path, page)]
        p.add(
            "source_page",
            id=key("page", year, page),
            artifact_id=artifact_id,
            page_number=page,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{page}.png",
            width=width,
            height=height,
        )
    units: dict[str, str] = {}

    def unit(code: str) -> str:
        if code not in units:
            id = units[code] = key("unit", m.day, code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=GUATEMALA,
                scheme_code="gt.municipio",
                code=f"{m.day}:{code}",
                area_relation="equals",
                area_id=tse.area("gt.municipio", code),
                kind="aggregate",
            )
        return units[code]

    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    tally_count: Counter[str] = Counter()
    for name, english_title, spanish_title, slug, kind in QUESTIONS[year]:
        tally = tallies[name]
        if not tally.cells:
            continue
        contest = key("contest", m.day, name)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            district_area_id=COUNTRY,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, spanish_title, "es", "official")
        p.name(contest, english_title, "en", "official", False)
        p.add("slug", path=f"{path_}/{slug}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type=kind)
        result_set = key("result_set", m.day, name)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
        )
        note = METHOD[year] + (SCAN_NOTE if MEMORIAS[year].layout != "text" else "")
        if year in TEXTS:
            note += f" The question: {TEXTS[year]}"
        if tally.left_out:
            note += f" Left out because their readings do not add up: {', '.join(sorted(tally.left_out))}."
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        options = {}
        for position, (field_name, label) in enumerate((("yes", "SÍ"), ("no", "NO")), start=1):
            option = options[field_name] = key("option", m.day, name, field_name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        complete = not tally.left_out
        mine = totals(tally)
        off = {f: (mine[f], v) for f, v in tally.declared.items() if f != "invalid" and mine[f] != v}
        if tally.declared and not complete:
            printed = ", ".join(f"{f} {v:,}" for f, v in tally.declared.items() if f != "invalid")
            note_extra = f" The memoria prints national totals of {printed}; they are not stored as declared totals because the municipalities left out are part of them."
            p.add(
                "note",
                id=key("note", result_set, "national"),
                entity_id=result_set,
                kind="caveat",
                body_md=note_extra.strip(),
                lang="en",
            )
        for field_name, option in options.items():
            if field_name in tally.declared and complete:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option,
                    measure=VOTES,
                    value=tally.declared[field_name],
                )
        for field_name, measure in MEASURES.items():
            if field_name in tally.declared and complete:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=None,
                    measure=measure,
                    value=tally.declared[field_name],
                )
        if complete and off:
            p.accept(
                "accounting.declared",
                "The memoria's printed national totals differ from the sum of its own municipal rows ("
                + ", ".join(f"{f}: rows {a:,}, printed {b:,}" for f, (a, b) in off.items())
                + "); both are stored as printed, the totals as declared",
            )
            p.add(
                "note",
                id=key("note", result_set, "uneven"),
                entity_id=result_set,
                kind="caveat",
                body_md="The national totals the memoria prints for blank and cast votes are 172 below the sum of its own "
                "municipal rows, whose department totals they match. Both are stored as printed.",
                lang="en",
            )
        for code in sorted(tally.cells):
            unit_id = unit(code)
            for field_name, w in tally.cells[code].items():
                if field_name == "invalid":
                    continue
                option = options.get(field_name)
                measure = VOTES if option else MEASURES[field_name]
                values.append((result_set, unit_id, option, measure, number(w)))
                locators.append((result_set, unit_id, option, measure, key("page", year, w.page), *w.box))
            tally_count["units"] += 1
        for code, w in sorted(tally.registered.items()):
            unit_id = unit(code)
            values.append((result_set, unit_id, None, REGISTERED, number(w)))
            locators.append((result_set, unit_id, None, REGISTERED, key("page", year, w.page), *w.box))
    facts = pl.DataFrame(
        values,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
        orient="row",
    )
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="value",
        ),
    )
    located = pl.DataFrame(
        locators,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "page_id": pl.String,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    p.frame(
        "value_locator",
        located.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            page_id="page_id",
            source_id=pl.lit(source),
            x0="x0",
            y0="y0",
            x1="x1",
            y1="y1",
        ),
    )
    print(f"{m.day}: {facts.height:,} values, {len(units)} units", flush=True)
    return p


def parse() -> None:
    for year in MEMORIAS:
        for name, report in compare(year).items():
            print(f"{year} {name}: {report}", flush=True)
        election(year).write()


def emit() -> None:
    package.run("load", packages())
