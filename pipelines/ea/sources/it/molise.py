"""Molise's regional elections of 22 April 2018 and 25-26 June 2023, from the Molise Region's own results pages.

The Interior Ministry's open data, which `it.regionali` reads for the other regional elections, has no file for
either. The region published its own counts instead: for 2018, a PDF of list votes by municipality, a PDF of the
candidates for president's regional totals, and the prefect's notice of who was proclaimed elected; for 2023, the
spreadsheets its results portal exports, with candidate and list votes by municipality. The region calls both counts
unofficial, and neither gives registered voters, voters, or blank and null ballots.

The elections attach to the region, its council, and its presidency, and the results to the municipal reporting
units that production already holds; `molise_comuni.tsv` lists those units' ids.
"""

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.it.storico import cased

SOURCE = "it.molise"
# The files were fetched into the regional pipeline's cache, beside the other regions' own results.
CACHE = "it.regionali"
VOTES, VALID = 1, 4
# Rows production holds: the Molise polity (IT-67), its region area and reporting unit, and the council and
# presidency that it.regionali loaded for the earlier Molise elections.
MOLISE = "5e46075a-0f8b-5079-8181-31f24d520d96"
REGION_AREA = "01a13bf7-2f89-7329-a8e6-bc0268b4ca5e"
REGION_UNIT = "82ff279a-7b3e-51c1-b1d1-f075dcaffe6d"
COUNCIL = "525ce966-5bd3-5c06-a66e-f750cc29ca43"
PRESIDENCY = "591a0937-09b4-563d-b482-b5b8f77d8df2"
COMUNI = Path(__file__).with_name("molise_comuni.tsv")
ARCHIVE_2018 = "https://archivioelezioni2018.regione.molise.it/sites/archivioelezioni2018.regione.molise.it/files"
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
# The council has 21 members: the president, the runner-up for president, and 19 councillors elected by list.
SIZE = 20
DIRECT = (
    "The candidate with the most votes is elected president. Candidates' votes include those cast for them alone, "
    "without a list."
)


@dataclass(frozen=True)
class Held:
    day: date
    title: str
    url: str
    candidates: str
    lists: str
    counted: str
    allocated: str
    proclamation: str | None = None
    seats: dict[str, int] = field(default_factory=dict)
    elected: str | None = None
    until: date | None = None


HELD = {
    2018: Held(
        day=date(2018, 4, 22),
        title="Elezioni regionali 2018: risultati (archivioelezioni2018.regione.molise.it)",
        url="https://archivioelezioni2018.regione.molise.it/node/68",
        candidates=f"https://web.archive.org/web/20230515062220id_/{ARCHIVE_2018}/Riepilogo%20Candidati%20Presidenti.pdf",
        lists=(
            "https://web.archive.org/web/20230514001014id_/"
            f"{ARCHIVE_2018}/Voti%20liste%2C%20riepilogo%20totale%20e%20divisi%20per%20comune.pdf"
        ),
        proclamation=f"https://web.archive.org/web/20240831161153id_/{ARCHIVE_2018}/Manifesto%20proclamati%20eletti.pdf",
        counted=(
            "Results are the Molise Region's summary of the count in all 136 municipalities, which it compiled from "
            "the figures the municipalities entered and calls unofficial. List votes are by municipality, and the "
            "valid votes are the region's total of list votes in each. The candidates for president's votes are the "
            "region's totals only, and the region gives no registered voters, voters, or blank and null ballots."
        ),
        allocated=(
            "Seats are those of the prefect of Campobasso's notice of the councillors proclaimed elected on 10 May "
            "2018: 12 from the lists linked to Donato Toma, the president elected, and 7 from the other lists. The "
            "seat of Andrea Greco, the runner-up for president, is counted on his only list, Movimento 5 Stelle. The "
            "president also sits on the 21-member council."
        ),
        seats={
            "FORZA ITALIA": 3,
            "ORGOGLIO MOLISE": 2,
            "LEGA SALVINI MOLISE": 2,
            "POPOLARI PER L'ITALIA": 2,
            "UNIONE DI CENTRO": 1,
            "FRATELLI D'ITALIA": 1,
            "IORIO PER IL MOLISE": 1,
            "MOVIMENTO CINQUE STELLE": 6,
            "PARTITO DEMOCRATICO": 2,
        },
        elected="TOMA DONATO",
    ),
    2023: Held(
        day=date(2023, 6, 25),
        until=date(2023, 6, 26),
        title="Elezioni regionali 2023: risultati (elezioni2023.regione.molise.it)",
        url="https://elezioni2023.regione.molise.it/",
        candidates="https://elezioni2023.regione.molise.it/export_stat_presidente.php",
        lists="https://elezioni2023.regione.molise.it/export_stat_liste.php",
        counted=(
            "Results are the Molise Region's count by municipality from its results portal, which it calls "
            "unofficial, with voting on 25 and 26 June 2023. Valid votes are the portal's valid votes for the "
            "candidates for president and its total of list votes in each municipality. The portal gives no "
            "registered voters, voters, or blank and null ballots."
        ),
        allocated=(
            "Seats are not shown: the region's results portal does not give the seat allocation, and the proclamation "
            "of the councillors elected is not in the archive. The council has 21 members, the president among them."
        ),
    ),
}
# Spellings in the region's files for municipalities whose ISTAT name differs.
ALIASES: dict[str, str] = {}
# List names that title-casing the printed capitals gets wrong.
LIST_NAMES = {
    "CASAPOUND ITALIA": "CasaPound Italia",
    "IL MOLISE CHE VOGLIAMO": "Il Molise che vogliamo",
    "IO NON VOTO (I SOLITI…NOTI)": "Io non voto (I soliti… noti)",
}


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{held.day}" for held in HELD.values())]


def links(held: Held) -> list[str]:
    return [held.candidates, held.lists, *([held.proclamation] if held.proclamation else [])]


def fetch() -> None:
    for held in HELD.values():
        for url in links(held):
            raw.fetch(CACHE, url, suffix=".pdf" if url.endswith(".pdf") else ".xlsx")


def parse() -> None:
    registry().write()
    for year in HELD:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def comuni() -> dict[str, str]:
    """Each municipality's reporting unit id, keyed by its folded name."""
    with COMUNI.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file, delimiter="\t"))
    units = {fold(row["name"]): row["unit_id"] for row in rows}
    if len(units) != len(rows) or len(rows) != 136:
        raise ValueError(f"{COMUNI.name}: expected 136 municipalities with distinct names")
    return units


def unit_of(units: dict[str, str], name: str) -> str:
    folded = fold(ALIASES.get(name, name))
    if folded not in units:
        raise ValueError(f"no Molise municipality named {name!r}")
    return units[folded]


def number(text: str) -> int:
    if not re.fullmatch(r"\d{1,3}(?:[.,]\d{3})*|\d+", text):
        raise ValueError(f"{text!r} is not a count")
    return int(re.sub(r"[.,]", "", text))


def label(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().upper()


@dataclass
class Count:
    """One election's figures: list and candidate votes by unit, valid totals by unit, and regional totals."""

    lists: dict[tuple[str, str], int] = field(default_factory=dict)
    list_valid: dict[str, int] = field(default_factory=dict)
    list_totals: dict[str, int] = field(default_factory=dict)
    candidates: dict[tuple[str, str], int] = field(default_factory=dict)
    candidate_valid: dict[str, int] = field(default_factory=dict)
    candidate_totals: dict[str, int] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


def _pdf_lines(url: str) -> list[list[str]]:
    with pymupdf.open(raw.cached(CACHE, url).path) as document:
        return [[line.strip() for line in page.get_text().splitlines() if line.strip()] for page in document]


def read_2018(held: Held) -> Count:
    units = comuni()
    count = Count()
    pages = _pdf_lines(held.lists)
    summary = pages[0]
    if summary[:2] != ["Lista", "Voti"]:
        raise ValueError(f"2018 list summary starts {summary[:2]}")
    printed = {label(summary[at]): number(summary[at + 1]) for at in range(2, len(summary), 2)}
    current: str | None = None
    grand = 0
    lines = [line for page in pages[1:] for line in page]
    at = 0
    while at < len(lines):
        if lines[at] == "Lista":
            if lines[at + 1 : at + 4] != ["Tot. voti liste", "Voti", "%"]:
                raise ValueError(f"2018 list header {lines[at : at + 4]}")
            current = label(lines[at + 4])
            grand = number(lines[at + 5])
            if number(lines[at + 6]) != printed[current]:
                raise ValueError(f"2018 {current}: header votes differ from the summary")
            count.order.append(current)
            at += 8
            continue
        if lines[at] == "Comune":
            at += 4
            continue
        if current is None:
            raise ValueError(f"2018 list rows before a list: {lines[at]}")
        name, total, votes = lines[at], number(lines[at + 1]), number(lines[at + 2])
        unit = unit_of(units, name)
        if count.list_valid.setdefault(unit, total) != total:
            raise ValueError(f"2018 {name}: list totals differ between lists")
        if (unit, current) in count.lists:
            raise ValueError(f"2018 {name} {current}: repeated")
        count.lists[unit, current] = votes
        at += 4
    if set(count.order) != set(printed) or len(count.order) != len(printed):
        raise ValueError("2018: the lists by municipality differ from the summary")
    for name in count.order:
        rows = {unit for unit, list_ in count.lists if list_ == name}
        if len(rows) != len(units):
            raise ValueError(f"2018 {name}: {len(rows)} municipalities, not {len(units)}")
        if sum(votes for (_, list_), votes in count.lists.items() if list_ == name) != printed[name]:
            raise ValueError(f"2018 {name}: municipalities do not add up to the list's total")
    for unit, total in count.list_valid.items():
        if sum(votes for (where, _), votes in count.lists.items() if where == unit) != total:
            raise ValueError(f"2018 unit {unit}: list votes do not add up to the municipality's total")
    if sum(count.list_valid.values()) != grand or sum(printed.values()) != grand:
        raise ValueError("2018: list votes do not add up to the region's total of list votes")
    count.list_totals = printed

    headers = {"Candidati alla Carica di", "Presidente della Regione", "Coalizione", "Voti Validi"}
    for page in _pdf_lines(held.candidates):
        rest = [line for line in page if line not in headers]
        n = len(rest) // 3
        if len(rest) != 3 * n or not all(re.fullmatch(r"[\d.,]+", line) for line in rest[2 * n :]):
            raise ValueError(f"2018 candidates page: {page}")
        for name, votes in zip(rest[:n], rest[2 * n :], strict=True):
            forename, surname = name.split(" ", 1)
            ballot = label(f"{surname} {forename}")
            count.candidate_totals[ballot] = number(votes)
            count.candidates[REGION_UNIT, ballot] = number(votes)
    return count


def _sheets(url: str) -> dict[str, list[tuple[str | None, ...]]]:
    sheets = pl.read_excel(raw.cached(CACHE, url).path, sheet_id=0, has_header=False, infer_schema_length=0)
    return {name: frame.rows() for name, frame in sheets.items()}


def _details(
    sheets: dict[str, list[tuple[str | None, ...]]], prefix: str, width: int, units: dict[str, str]
) -> list[tuple[str, int, dict[str, tuple[int, int]]]]:
    """Each detail sheet's option label, regional total, and (valid total, votes) by unit."""
    found = []
    for name, rows in sheets.items():
        if not name.startswith(prefix):
            continue
        head = rows[1]
        if rows[2][0] != "Comune":
            raise ValueError(f"2023 {name}: no municipality header")
        option = label(" ".join(str(part) for part in head[: width - 3]))
        total = number(str(head[width - 2]))
        by_unit: dict[str, tuple[int, int]] = {}
        for row in rows[3:]:
            unit = unit_of(units, str(row[0]))
            if unit in by_unit:
                raise ValueError(f"2023 {name}: {row[0]} repeated")
            by_unit[unit] = (number(str(row[-3])), number(str(row[-2])))
        if len(by_unit) != len(units):
            raise ValueError(f"2023 {name}: {len(by_unit)} municipalities, not {len(units)}")
        if sum(votes for _, votes in by_unit.values()) != total:
            raise ValueError(f"2023 {name}: municipalities do not add up to the regional total")
        found.append((option, total, by_unit))
    return found


def read_2023(held: Held) -> Count:
    units = comuni()
    count = Count()
    candidates = _sheets(held.candidates)
    summary = candidates["Preferenze Candidati Presidente"]
    if [str(cell) for cell in summary[0]] != ["Cognome", "Nome", "Voti"]:
        raise ValueError(f"2023 candidate summary header {summary[0]}")
    count.candidate_totals = {
        label(f"{surname} {forename}"): number(str(votes)) for surname, forename, votes in summary[1:]
    }
    for option, total, by_unit in _details(candidates, "Dettagli Candidato", 5, units):
        if count.candidate_totals.get(option) != total:
            raise ValueError(f"2023 {option}: detail total differs from the summary")
        for unit, (valid, votes) in by_unit.items():
            if count.candidate_valid.setdefault(unit, valid) != valid:
                raise ValueError(f"2023 unit {unit}: valid votes differ between candidates")
            count.candidates[unit, option] = votes
    lists = _sheets(held.lists)
    summary = lists["Liste"]
    if [str(cell) for cell in summary[0]] != ["Lista", "Voti"]:
        raise ValueError(f"2023 list summary header {summary[0]}")
    count.list_totals = {label(str(name)): number(str(votes)) for name, votes in summary[1:]}
    for option, total, by_unit in _details(lists, "Dettagli Lista", 4, units):
        if count.list_totals.get(option) != total:
            raise ValueError(f"2023 {option}: detail total differs from the summary")
        count.order.append(option)
        for unit, (valid, votes) in by_unit.items():
            if count.list_valid.setdefault(unit, valid) != valid:
                raise ValueError(f"2023 unit {unit}: list totals differ between lists")
            count.lists[unit, option] = votes
    if set(count.order) != set(count.list_totals):
        raise ValueError("2023: the list detail sheets differ from the summary")
    if {option for _, option in count.candidates} != set(count.candidate_totals):
        raise ValueError("2023: the candidate detail sheets differ from the summary")
    for unit, valid in count.candidate_valid.items():
        if sum(votes for (where, _), votes in count.candidates.items() if where == unit) != valid:
            raise ValueError(f"2023 unit {unit}: candidate votes do not add up to valid votes")
    for unit, valid in count.list_valid.items():
        if sum(votes for (where, _), votes in count.lists.items() if where == unit) != valid:
            raise ValueError(f"2023 unit {unit}: list votes do not add up to valid list votes")
    return count


def read(year: int) -> Count:
    return read_2018(HELD[2018]) if year == 2018 else read_2023(HELD[2023])


def party(name: str) -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fparty\x1f{name}"))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The lists on the ballot in Molise's 2018 and 2023 regional elections",
    )
    p.external.add(MOLISE)
    names: dict[str, None] = {}
    for year, held in HELD.items():
        for url in links(held):
            p.uses(raw.cached(CACHE, url))
        names.update(dict.fromkeys(read(year).list_totals))
    for name in sorted(names):
        p.add("party", id=party(name), kind="party", polity_id=MOLISE)
        p.name(party(name), LIST_NAMES.get(name) or cased(name), "it", "official")
    return p


def election(year: int) -> Package:
    held = HELD[year]
    count = read(year)
    p = Package(
        f"{SOURCE}/{held.day}",
        SOURCE,
        f"Molise's {year} regional election, for the council and the president, from the Molise Region's own results",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update(
        [
            MOLISE,
            REGION_AREA,
            COUNCIL,
            PRESIDENCY,
            *{unit for unit, _ in count.lists},
            *{u for u, _ in count.candidates},
        ]
    )
    source = p.id("source", year)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Regione Molise",
        title=held.title,
        url=held.url,
        license=LICENSE,
    )
    for url in links(held):
        p.artifact(raw.cached(CACHE, url), source)

    election = p.id("election", year)
    p.add(
        "election",
        id=election,
        polity_id=MOLISE,
        kind="general",
        date_from=held.day,
        date_to=held.until or held.day,
        status="held",
    )
    p.name(election, f"{year} Molise regional election", "en", "official")
    p.name(election, f"Elezioni regionali in Molise del {year}", "it", "native")
    path = f"it/molise/{held.day}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)

    def result_set(contest: str, note: str) -> str:
        id = p.id("result_set", contest)
        p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=p.id("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
        return id

    values: list[dict[str, object]] = []

    def value(result: str, unit: str, option: str | None, measure: int, amount: int) -> None:
        values.append(
            {
                "result_set_id": result,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": 0,
                "value": amount,
            }
        )

    council = p.id("contest", "council")
    p.add(
        "contest",
        id=council,
        election_id=election,
        kind="list_allocation",
        body_id=COUNCIL,
        district_area_id=REGION_AREA,
        magnitude=SIZE,
        default_result_kind="counted",
    )
    p.name(council, f"{year} Molise regional election, council lists", "en", "official")
    p.add("slug", path=f"{path}/consiglio", entity_id=council, is_current=True)
    p.add("note", id=p.id("note", council), entity_id=council, kind="context", body_md=held.allocated, lang="en")
    lists = result_set(council, held.counted)
    if held.seats and (sum(held.seats.values()) != SIZE or not set(held.seats) <= set(count.list_totals)):
        raise ValueError(f"{year}: seats do not match the lists or the council")
    for position, name in enumerate(sorted(count.list_totals, key=lambda n: (-count.list_totals[n], n)), 1):
        option = p.id("option", "list", name)
        p.add("contest_option", id=option, contest_id=council, kind="list", party_id=party(name), ballot_label=name)
        if held.seats.get(name):
            p.add("outcome", result_set_id=lists, option_id=option, kind="won", seats=held.seats[name])
        for (unit, list_), votes in count.lists.items():
            if list_ == name:
                value(lists, unit, option, VOTES, votes)
    for unit, valid in count.list_valid.items():
        value(lists, unit, None, VALID, valid)

    race = p.id("contest", "president")
    p.add(
        "contest",
        id=race,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=REGION_AREA,
        magnitude=1,
        default_result_kind="counted",
    )
    p.name(race, f"{year} Molise regional election, President", "en", "official")
    p.add("slug", path=f"{path}/presidente", entity_id=race, is_current=True)
    p.add("note", id=p.id("note", race), entity_id=race, kind="context", body_md=DIRECT, lang="en")
    running = result_set(race, held.counted)
    top = max(count.candidate_totals.values())
    winners = [name for name, votes in count.candidate_totals.items() if votes == top]
    if len(winners) != 1 or (held.elected is not None and winners != [held.elected]):
        raise ValueError(f"{year}: the candidate with the most votes is {winners}, not {held.elected}")
    for name in sorted(count.candidate_totals, key=lambda n: (-count.candidate_totals[n], n)):
        option = p.id("option", "president", name)
        p.add("contest_option", id=option, contest_id=race, kind="candidate", ballot_label=name)
        person = p.id("person", name)
        p.add("person", id=person)
        surname, forename = SPLIT.get(name) or name.split(" ", 1)
        p.name(person, f"{cased(forename)} {cased(surname)}", "it", "official")
        candidacy = p.id("candidacy", "president", name)
        p.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", ballot_name=name)
        if name == winners[0]:
            p.add("outcome", result_set_id=running, option_id=option, kind="elected")
        for (unit, who), votes in count.candidates.items():
            if who == name:
                value(running, unit, option, VOTES, votes)
    for unit, valid in count.candidate_valid.items():
        value(running, unit, None, VALID, valid)
    p.frame("result_value", pl.DataFrame(values, schema_overrides={"option_id": pl.String}))
    return p


# Ballot labels whose surname has more than one word.
SPLIT = {"DI GIACOMO AGOSTINO": ("DI GIACOMO", "AGOSTINO")}
