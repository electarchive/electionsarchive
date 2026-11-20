"""Municipal council elections in Saxony-Anhalt, 1994 to 2024, by municipality.

The Statistisches Landesamt Sachsen-Anhalt publishes the results of every Gemeinderat election since 1994 in one
workbook (1994 to 2019, one sheet per period) and the 2024 results as a CSV file. Each row is a municipality with the
registered voters, voters, invalid and valid ballots, valid votes, and each party's votes and seats. Voters' groups
and independent candidates are printed as one sum each.
"""

import csv
import re
from datetime import date, datetime

import polars as pl

from ea import package, raw
from ea.sources.de import gemeinderat as common
from ea.sources.de.gemeinderat import Entry, Land, Result, Vote, count

SOURCE = "de.gemeinderat_st"
BASE = "https://wahlergebnisse.sachsen-anhalt.de/wahlen"
WORKBOOK = f"{BASE}/gwgw/Gemeinderat_Stimmen_Sitze_1994_2019.xlsx"
CSV_2024 = f"{BASE}/gw24/erg/csv/gw24_endg_20250910.csv"
# sheet: (general election day, whether each row names its own day)
SHEETS = {
    "1994": (date(1994, 6, 12), False),
    "1999": (date(1999, 6, 13), False),
    "2001-2004": (date(2004, 6, 13), True),
    "2005-2010": (date(2009, 6, 7), True),
    "2014-2015": (date(2014, 5, 25), False),
    "2019": (date(2019, 5, 26), False),
}
# Rows the sheets file under the general election day that were held on another day: (sheet, AGS, occurrence in sheet).
MOVED = {
    ("2019", "15082256", 1): date(2019, 9, 22),
    ("2019", "15083440", 2): date(2019, 11, 10),
    ("2014-2015", "15084355", 1): date(2014, 6, 15),
    ("2014-2015", "15090535", 1): date(2015, 6, 21),
}
# The seat formula each sheet's seats follow, found by recomputing every municipality's seats from its votes.
FORMULAS = {
    1994: "hare",
    1999: "hare",
    2001: "hare",
    2004: "hare",
    2009: "hare",
    2014: "hare",
    2019: "hare",
    2024: "hare",
}
LAND = Land(
    source=SOURCE,
    code="15",
    slug="saxony-anhalt",
    name="Saxony-Anhalt",
    body="Gemeinderat",
    publisher="Statistisches Landesamt Sachsen-Anhalt",
    title="Kommunalwahlen in Sachsen-Anhalt: Gemeinderatswahlen",
    url="https://wahlergebnisse.sachsen-anhalt.de/",
    license="Datenlizenz Deutschland, Namensnennung, Version 2.0",
    license_url="https://www.govdata.de/dl-de/by-2-0",
    attribution="Statistisches Landesamt Sachsen-Anhalt",
    method=(
        "Counted by the Statistisches Landesamt Sachsen-Anhalt and published for each municipality. Each voter has "
        "as many votes as the council has seats, may give a candidate up to three of them, and may spread them over "
        "several lists, so a list's votes are the sum of its candidates' votes and are many times the number of "
        "voters. Registered voters are the electoral roll, voters are those who took part, valid and invalid "
        "ballots are the ballot papers counted as such, and the votes of a list are as printed. Voters' groups and "
        "independent candidates are printed as one sum each for the municipality, not by name. Seats are as "
        "declared by the source."
    ),
    system_note=(
        "Each voter has as many votes as the council has seats, up to three for one candidate, which may be spread "
        "over several lists (panachage). Seats are shared among the lists by a proportional method on the sum of "
        "their candidates' votes, and a list's seats go to its candidates in the order of their votes. The declared "
        "seats are stored as published."
    ),
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in votes())]


def fetch() -> None:
    raw.fetch(SOURCE, WORKBOOK, suffix=".xlsx")
    raw.fetch(SOURCE, CSV_2024, suffix=".csv")


def _name(day: date) -> str:
    return f"{day:%Y-%m-%d}"


def votes() -> dict[str, Vote]:
    found: list[Vote] = []
    for sheet in SHEETS:
        found += workbook(sheet)
    found += csv_2024()
    return {_name(item.day): item for item in sorted(found, key=lambda item: item.day)}


def _row(row: tuple[object, ...], parties: list[tuple[int, str]], dated: bool) -> tuple[date | None, Result]:
    shift = 1 if dated else 0
    day = datetime.fromisoformat(str(row[2])).date() if dated else None
    entries: list[Entry] = []
    for column, label in parties:
        text = " ".join(label.split())
        kind = "OW" if text.startswith("Wählergruppen") else "OE" if text.startswith("Einzelbew") else "P"
        entries.append(Entry(text, kind, count(row[column]), count(row[column + 2])))
    result = Result(
        ags=str(row[0]),
        name=" ".join(str(row[1]).split()),
        registered=count(row[2 + shift]),
        voters=count(row[3 + shift]),
        invalid=count(row[5 + shift] if not dated else row[6]),
        valid_ballots=count(row[7 + shift] if not dated else row[8]),
        seats=count(row[10 + shift] if not dated else row[11]),
        entries=entries,
    )
    return day, result


def workbook(sheet: str) -> list[Vote]:
    general, dated = SHEETS[sheet]
    artifact = raw.cached(SOURCE, WORKBOOK)
    rows = pl.read_excel(artifact.path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()
    first, second = rows[1], rows[2]
    start = 13 if dated else 11
    if sheet == "2019":
        start = 11
    parties = [
        (i, str(first[i]))
        for i in range(start, len(first))
        if first[i] and second[i] == "absolut" and not str(first[i]).startswith("Sitze")
    ]
    by_day: dict[date, list[Result]] = {}
    seen: dict[str, int] = {}
    for row in rows[3:]:
        if not re.fullmatch(r"15\d{6}", str(row[0] or "")):
            continue
        day, result = _row(row, parties, dated)
        seen[result.ags] = seen.get(result.ags, 0) + 1
        day = MOVED.get((sheet, result.ags, seen[result.ags]), day)
        printed_votes = count(row[9 + (1 if dated else 0)])
        total = sum(entry.votes or 0 for entry in result.entries)
        if result.seats is not None and total != printed_votes:
            raise ValueError(f"{sheet} {result.ags}: list votes {total} differ from the printed valid votes")
        by_day.setdefault(day or general, []).append(result)
    out: list[Vote] = []
    for day, results in sorted(by_day.items()):
        for result in results:
            printed = [entry for entry in result.entries if entry.votes is not None]
            if result.seats is not None and sum(entry.seats or 0 for entry in printed) != result.seats:
                raise ValueError(f"{sheet} {result.ags}: list seats differ from the printed total {result.seats}")
        formula = FORMULAS.get(day.year, "hare")
        out.append(Vote(day, formula, results, [artifact], None))
    return out


def csv_2024() -> list[Vote]:
    artifact = raw.cached(SOURCE, CSV_2024)
    rows = list(csv.reader(artifact.path.read_text(encoding="latin-1").splitlines(), delimiter=";"))
    header = rows[0]
    parties = [
        (i, header[i].split(" - ", 1)[-1] if " - " in header[i] else header[i])
        for i in range(13, header.index("S - Sitze"))
    ]
    seats_at = {label: header.index(f"S{i + 1:02d} - {label}") for i, (_, label) in enumerate(parties[:-2])}
    by_day: dict[date, list[Result]] = {}
    for row in rows[1:]:
        if row[5] != "GEM":
            continue
        entries: list[Entry] = []
        for i, (column, label) in enumerate(parties):
            if label.startswith("WG"):
                kind, text, seat_column = "OW", "Wählergruppen (Summe)", header.index("S WG (Summe)")
            elif label.startswith("EB"):
                kind, text, seat_column = "OE", "Einzelbewerber/-innen (Summe)", header.index("S EB (Summe)")
            else:
                kind, text, seat_column = "P", label, seats_at[label]
            entries.append(Entry(text, kind, count(row[column]), count(row[seat_column])))
        day = datetime.strptime(row[1], "%d.%m.%Y").date()
        result = Result(
            ags=row[6],
            name=" ".join(row[7].split()),
            registered=count(row[8]),
            voters=count(row[9]),
            invalid=count(row[10]),
            valid_ballots=count(row[11]),
            seats=count(row[header.index("S - Sitze")]),
            entries=entries,
        )
        if sum(entry.votes or 0 for entry in entries) != count(row[12]):
            raise ValueError(f"2024 {result.ags}: list votes differ from the printed valid votes {row[12]}")
        if sum(entry.seats or 0 for entry in entries) != result.seats:
            raise ValueError(f"2024 {result.ags}: list seats differ from the printed total {result.seats}")
        by_day.setdefault(day, []).append(result)
    return [
        Vote(day, FORMULAS[2024], results, [artifact], None if day == date(2024, 6, 9) else REPEAT)
        for day, results in sorted(by_day.items())
    ]


REPEAT = "This election day holds the repeat or supplementary elections of a few municipalities."


def parse() -> None:
    all_votes = votes()
    common.geo(LAND, all_votes.values()).write()
    common.registry(LAND, all_votes.values()).write()
    for name, item in all_votes.items():
        common.election(LAND, item, name).write()


def emit() -> None:
    package.run("load", packages())
