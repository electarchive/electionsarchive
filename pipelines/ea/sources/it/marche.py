"""Marche's regional election of 20-21 September 2020 by municipality, from the Marche Region's own spreadsheets.

`it.regionali` holds this election from the region's results site as regional totals only: its archived results
files give the region-wide count, and the archive holds no municipal results from that site. The region also
published two spreadsheets of the final count by municipality, one for the candidates for president and one for the
lists, both covering all 1,576 polling sections in the 227 municipalities of 2020.

The package adds a municipal result set beside each of the three regional ones (turnout and ballots, council lists,
and president), on the same election, contests, and options. The regional sets stay the default and keep the
outcomes, as for any count held at two grains; the municipal sets add up exactly to them, which `parse` checks
against the regional results files. `marche_comuni.tsv` lists the municipal reporting units production holds,
Montecopiolo and Sassofeltrio under the codes they took when they moved to Emilia-Romagna in 2021.
"""

import csv
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.it import regionali
from ea.sources.it.interno import BLANK, INVALID, VOTERS, VOTES

SOURCE = "it.marche"
# The files were fetched into the regional pipeline's cache, beside the region's other results files.
CACHE = regionali.SOURCE
DAY = date(2020, 9, 20)
KEY = f"{SOURCE}/{DAY}"
FOLDER = "https://www.regione.marche.it/portals/0/Elezioni_2020"
CANDIDATES = f"https://web.archive.org/web/20250927102527id_/{FOLDER}/coalizioni_totali_x_comune.xlsx"
LISTS = f"https://web.archive.org/web/20250927102534id_/{FOLDER}/liste_totali_x_comuni.xlsx"
SECTIONS = 1576
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
COUNTED = (
    "Votes by municipality are from the Marche Region's spreadsheets of the final count of all 1,576 polling "
    "sections, one for the candidates for president and one for the lists, as archived in September 2025. They add "
    "up exactly to the regional totals of the default result set. The spreadsheets give no registered voters by "
    "municipality."
)
COMUNI = Path(__file__).with_name("marche_comuni.tsv")

# Rows production holds: the election, its two contests, and their options, all written by it.regionali.
ELECTION = "f58fcd48-5836-5127-b525-f3102374e3c6"
COUNCIL = "852d8af7-c85e-5f33-9954-7bde80b6f677"
PRESIDENCY = "5bfc2243-81f0-5931-aa6d-4073373586c3"
LIST_OPTIONS = {
    "CIVITAS CIVICI": "0881c3e0-3e07-5b10-acf1-dc6e930c800c",
    "COMUNISTA!": "4c6daa0e-e593-5ea8-bd32-f8c933da75b5",
    "DIPENDE DA NOI": "3de1b3e3-c6ef-53a9-8fb0-7f1af0a714e4",
    "FORZA ITALIA - BERLUSCONI - CIVICI PER LE MARCHE": "85ee9d12-d67d-5132-b7ca-5d1bdf72b58b",
    "GIORGIA MELONI per ACQUAROLI - FRATELLI D'ITALIA": "ebdd1b19-e0c6-5e38-89f7-e22da20f7e39",
    "ITALIA VIVA": "17b1b2a5-ff92-518c-acf2-68f6bc4d775c",
    "LEGA SALVINI MARCHE": "f2cbea03-4130-5d43-af09-aa5146339238",
    "LE NOSTRE MARCHE e IL CENTRO": "2074a955-7e63-5bc0-a536-d78b0d1ae9eb",
    "MANGIALARDI PRESIDENTE": "e4d56378-39e4-594b-a0ae-d2f9024373ce",
    "MARCHE CORAGGIOSE": "12a81a1a-3175-56d6-8be5-fb0ba9e977a2",
    "MOVIMENTO 3V LIBERTA' DI SCELTA": "433087d0-43ef-54c7-8551-421e440cfddf",
    "MOVIMENTO 5 STELLE": "bf115faf-c251-5371-8c8c-4bfd83308647",
    "MOVIMENTO PER LE MARCHE": "3d91491b-1a02-5565-9ba7-136e7e066f1d",
    "PARTITO DEMOCRATICO": "36194ffe-e82a-5c00-ad05-91f7ff42b14e",
    "POPOLARI MARCHE - UNIONE DI CENTRO": "7c17d74c-c0f9-565c-9e8b-2ae21540dd04",
    "RICONQUISTARE L'ITALIA": "18a5a79c-5b43-5516-9be8-0b96150e73ae",
    "RINASCI MARCHE": "a7bcb48f-5dc3-5b8d-ba46-6a8ea22cf7ba",
    "VOX ITALIA MARCHE": "3ba9d688-a0cb-5bd8-b1a7-c6036dc98b15",
}
CANDIDATE_OPTIONS = {
    "ACQUAROLI FRANCESCO": "f43cf756-3773-563a-9e0e-9015566980b0",
    "BANZATO SABRINA PAOLA": "57c8ec49-2a37-5f24-afac-4175b1438393",
    "CONTIGIANI ALESSANDRA": "d240943c-5868-55b7-8d7f-42554dbdb7e4",
    "IANNETTI ANNA RITA": "8d65b532-b9db-5cb5-b661-d5249f87dd50",
    "MANCINI ROBERTO": "26e9c135-2c8d-56b3-b6f4-7602b66e1b17",
    "MANGIALARDI MAURIZIO": "024e0b74-88e7-5c4b-a5a9-442c7258474e",
    "MERCORELLI GIAN-MARIO": "3f0ce010-168a-5617-a3b6-44a01187b06e",
    "PASQUINELLI FABIO": "c663d65c-7807-54ab-908f-558e317f2b05",
}
# The spreadsheet's short list names, by the full names on the ballot that the regional results files give.
SHORT = {
    "FORZA ITALIA - BERLUSCONI": "FORZA ITALIA - BERLUSCONI - CIVICI PER LE MARCHE",
    "FRATELLI D'ITALIA": "GIORGIA MELONI per ACQUAROLI - FRATELLI D'ITALIA",
    "MOVIMENTO 3V": "MOVIMENTO 3V LIBERTA' DI SCELTA",
    "POPOLARI MARCHE - U.D.C.": "POPOLARI MARCHE - UNIONE DI CENTRO",
}
ACCOUNTING = {"Votanti": VOTERS, "Bianche": BLANK, "Nulle": INVALID}


def packages() -> list[str]:
    return [KEY]


def fetch() -> None:
    for url in (CANDIDATES, LISTS):
        raw.fetch(CACHE, url, suffix=".xlsx")


def parse() -> None:
    build().write()


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
    if len(units) != len(rows) or len(rows) != 227:
        raise ValueError(f"{COMUNI.name}: expected 227 municipalities with distinct names")
    return units


def sheet(url: str) -> tuple[list[str], dict[str, dict[str, int]], dict[str, int]]:
    """The sheet's columns, its counts by municipality name, and its TOTALE row."""
    rows = pl.read_excel(raw.cached(CACHE, url).path, has_header=False, infer_schema_length=0).rows()
    if rows[0][0] != "REGIONALI 2020" or f"VERIFICATE {SECTIONS} SEZIONI SU {SECTIONS}" not in str(rows[2][0]):
        raise ValueError(f"{url}: not the final 2020 count: {rows[:3]}")
    header, body = rows[3], rows[4:]
    if header[0] is not None or body[-1][0] != "TOTALE":
        raise ValueError(f"{url}: unexpected layout")
    columns = [str(cell).strip() for cell in header[1:]]

    def counts(row: tuple[str | None, ...]) -> dict[str, int]:
        if any(cell is None or not re.fullmatch(r"\d+", cell.strip()) for cell in row[1:]):
            raise ValueError(f"{url}: {row[0]} has a count that is not a number")
        return {column: int(str(cell)) for column, cell in zip(columns, row[1:], strict=True)}

    by_place: dict[str, dict[str, int]] = {}
    for row in body[:-1]:
        name = str(row[0]).strip()
        if name in by_place:
            raise ValueError(f"{url}: {name} repeated")
        by_place[name] = counts(row)
    total = counts(body[-1])
    for column in columns:
        if sum(place[column] for place in by_place.values()) != total[column]:
            raise ValueError(f"{url}: {column} by municipality does not add up to TOTALE")
    return columns, by_place, total


def ballot(name: str) -> str:
    """A candidate's ballot label, surname first, matched by the words of the spreadsheet's forename-first name."""
    words = sorted(name.upper().split())
    found = [label for label in CANDIDATE_OPTIONS if sorted(label.split()) == words]
    if len(found) != 1:
        raise ValueError(f"no single candidate for president named {name!r}")
    return found[0]


def read() -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int], dict[tuple[str, int], int]]:
    """Candidate votes, list votes, and turnout and ballots, each keyed by (unit id, option label or measure)."""
    units = comuni()
    head = ["Votanti", "Voti validi", "Schede contestate", "Bianche", "Nulle"]
    columns, coalitions, coalition_total = sheet(CANDIDATES)
    if columns[:5] != head:
        raise ValueError(f"candidates sheet columns {columns[:5]}")
    names = {name: ballot(name) for name in columns[5:]}
    if set(names.values()) != set(CANDIDATE_OPTIONS):
        raise ValueError("the candidates sheet does not list every candidate for president")
    columns, lists, list_total = sheet(LISTS)
    if columns[:6] != ["Votanti", "Voti validi", "Voti solo Pres.", "Schede contestate", "Bianche", "Nulle"]:
        raise ValueError(f"lists sheet columns {columns[:6]}")
    labels = {name: SHORT.get(name, name) for name in columns[6:]}
    if sorted(labels.values()) != sorted(LIST_OPTIONS):
        raise ValueError(f"the lists sheet's lists differ from the ballot: {set(labels.values()) ^ set(LIST_OPTIONS)}")
    if {fold(name) for name in coalitions} != set(units) or {fold(name) for name in lists} != set(units):
        raise ValueError("the sheets' municipalities differ from the 2020 municipalities")

    candidates: dict[tuple[str, str], int] = {}
    by_list: dict[tuple[str, str], int] = {}
    accounting: dict[tuple[str, int], int] = {}
    for name, row in coalitions.items():
        unit = units[fold(name)]
        other = lists[name]
        for column in ("Votanti", "Schede contestate", "Bianche", "Nulle"):
            if row[column] != other[column]:
                raise ValueError(f"{name}: {column} differs between the two sheets")
        if row["Voti validi"] + row["Schede contestate"] + row["Bianche"] + row["Nulle"] != row["Votanti"]:
            raise ValueError(f"{name}: valid, contested, blank, and null ballots do not add up to voters")
        if sum(row[column] for column in names) != row["Voti validi"]:
            raise ValueError(f"{name}: candidate votes do not add up to valid votes")
        if sum(other[column] for column in labels) != other["Voti validi"]:
            raise ValueError(f"{name}: list votes do not add up to valid list votes")
        if other["Voti validi"] + other["Voti solo Pres."] != row["Voti validi"]:
            raise ValueError(f"{name}: list votes and votes for a candidate alone do not add up to valid votes")
        for column, label in names.items():
            candidates[unit, label] = row[column]
        for column, label in labels.items():
            by_list[unit, label] = other[column]
        for column, measure in ACCOUNTING.items():
            accounting[unit, measure] = row[column]

    # The regional results files that it.regionali loaded hold the same final count.
    totals, list_rows, running, _ = regionali.regional("11", DAY, regionali.REGIONAL["11", DAY])
    regional = totals.row(0, named=True)
    pairs = (("Votanti", "voters"), ("Bianche", "blank"), ("Nulle", "null"), ("Schede contestate", "contested"))
    for column, field in pairs:
        if coalition_total[column] != regional[field]:
            raise ValueError(f"{column}: {coalition_total[column]} by municipality, {regional[field]} for the region")
    expected = {f"{row['surname']} {row['forename']}": row["candidate_votes"] for row in running.iter_rows(named=True)}
    if {names[c]: coalition_total[c] for c in names} != expected:
        raise ValueError("candidate votes by municipality do not add up to the regional totals")
    expected = dict(list_rows.select("list", "list_votes").iter_rows())
    if {labels[c]: list_total[c] for c in labels} != expected:
        raise ValueError("list votes by municipality do not add up to the regional totals")
    return candidates, by_list, accounting


def build() -> Package:
    candidates, lists, accounting = read()
    p = Package(
        KEY,
        SOURCE,
        "Marche's 2020 regional election by municipality, for the council lists and the president, from the Marche "
        "Region's own spreadsheets",
    )
    units = sorted({unit for unit, _ in accounting})
    p.external.update([ELECTION, COUNCIL, PRESIDENCY, *LIST_OPTIONS.values(), *CANDIDATE_OPTIONS.values(), *units])
    source = p.id("source", DAY)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Regione Marche",
        title="Elezioni regionali 2020: totali per comune (regione.marche.it)",
        url=f"{FOLDER}/coalizioni_totali_x_comune.xlsx",
        license=LICENSE,
    )
    for url in (CANDIDATES, LISTS):
        p.artifact(raw.cached(CACHE, url), source)

    def result_set(contest: str | None) -> str:
        id = p.id("result_set", contest or "election")
        p.add(
            "result_set",
            id=id,
            election_id=ELECTION,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=False,
            completeness=1.0,
        )
        p.add("note", id=p.id("note", id), entity_id=id, kind="methodology", body_md=COUNTED, lang="en")
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

    turnout = result_set(None)
    for (unit, measure), amount in sorted(accounting.items()):
        value(turnout, unit, None, measure, amount)
    council = result_set(COUNCIL)
    for (unit, label), amount in sorted(lists.items()):
        value(council, unit, LIST_OPTIONS[label], VOTES, amount)
    race = result_set(PRESIDENCY)
    for (unit, label), amount in sorted(candidates.items()):
        value(race, unit, CANDIDATE_OPTIONS[label], VOTES, amount)
    p.frame("result_value", pl.DataFrame(values, schema_overrides={"option_id": pl.String}))
    return p
