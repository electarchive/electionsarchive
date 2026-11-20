"""Municipal council elections in Mecklenburg-Vorpommern, 2004 to 2024, by municipality.

The Landesamt für innere Verwaltung publishes the final result of each Gemeindevertretung election of the
kreisangehörige Gemeinden as a statistical report with one row per municipality: the registered voters, voters, invalid
and valid votes, and each party's votes and seats. Voters' groups and independent candidates are printed as one sum
each, and in 2004 the smaller parties as one sum.
"""

import re
from datetime import date
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.sources.de import gemeinderat as common
from ea.sources.de.gemeinderat import Entry, Land, Result, Vote, count

SOURCE = "de.gemeinderat_mv"
BASE = "https://www.laiv-mv.de/static/LAIV/Wahlen"


class Report(NamedTuple):
    day: date
    url: str
    suffix: str
    sheet: str
    first: int  # column of the first party's votes
    n: int  # number of party columns
    seats_total: int | None  # column of the seats total, if the report has one
    key_column: int  # column that holds the AGS
    name_column: int
    voters: int = 4  # column of the voters
    valid: int = 6  # column of the valid votes
    anzahl: bool = False  # rows come in pairs of counts and percentages, named in column 2


REPORTS = {
    2004: Report(
        date(2004, 6, 13),
        f"{BASE}/Dateien/Publikationen/B731/B734GV%202004%2001.xls",
        ".xls",
        "gem-mandate",
        7,
        8,
        15,
        1,
        2,
    ),
    2009: Report(
        date(2009, 6, 7),
        f"{BASE}/Dateien/Publikationen/B731/B734GV%202009%2001.xls",
        ".xls",
        "gem-mandate",
        7,
        8,
        15,
        1,
        2,
    ),
    2014: Report(
        date(2014, 5, 25),
        f"{BASE}/Dateien/Publikationen/B731/B734GV%202014%2001.xlsx",
        ".xlsx",
        "Gem-Mandate",
        7,
        8,
        15,
        1,
        2,
    ),
    2019: Report(
        date(2019, 5, 26),
        f"{BASE}/3-Kommunalwahlen/2019/Ergebnisse/B731GV%202019%2001-online.xlsx",
        ".xlsx",
        "Gem-Mandate",
        7,
        17,
        None,
        1,
        2,
    ),
    2024: Report(
        date(2024, 6, 9),
        f"{BASE}/3-Kommunalwahlen/2024/Ergebnisse/zur%20Wahl/B731GV%202024%2005-online.xlsx",
        ".xlsx",
        "Gem-Mandate",
        7,
        19,
        26,
        0,
        1,
        anzahl=True,
    ),
}
LAND = Land(
    source=SOURCE,
    code="13",
    slug="mecklenburg-vorpommern",
    name="Mecklenburg-Vorpommern",
    body="Gemeindevertretung",
    publisher="Landesamt für innere Verwaltung Mecklenburg-Vorpommern",
    title="Kommunalwahlen in Mecklenburg-Vorpommern: Gemeindevertretungen der kreisangehörigen Gemeinden",
    url="https://www.laiv-mv.de/Wahlen/Kommunalwahlen/",
    license="Datenlizenz Deutschland, Namensnennung, Version 2.0",
    license_url="https://www.govdata.de/dl-de/by-2-0",
    attribution="Landesamt für innere Verwaltung Mecklenburg-Vorpommern",
    method=(
        "Counted by the Landesamt für innere Verwaltung and published for each municipality of a district "
        "(kreisangehörige Gemeinde). Each voter has as many votes as the council has seats, may give a candidate up "
        "to three of them, and may spread them over several lists, so a list's votes are the sum of its candidates' "
        "votes and are many times the number of voters. Registered voters are the electoral roll and voters are "
        "those who took part. The source prints invalid and valid votes, not ballots, so neither is stored. "
        "Voters' groups and independent candidates are printed as one sum each, not by name, and in 2004 the smaller "
        "parties as one sum. In a few municipalities the source withholds a list's votes (printed x) but still prints its seats, "
        "and those seats are stored without votes. Seats are as declared by the source."
    ),
    system_note=(
        "Each voter has as many votes as the council has seats, up to three for one candidate, which may be spread "
        "over several lists (panachage). Seats are shared among the lists by a proportional method on the sum of "
        "their candidates' votes, and a list's seats go to its candidates in the order of their votes. The declared "
        "seats are stored as published."
    ),
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in REPORTS)]


def fetch() -> None:
    for report in REPORTS.values():
        raw.fetch(SOURCE, report.url, suffix=report.suffix)


def votes() -> dict[int, Vote]:
    return {year: vote(year) for year in REPORTS}


LABELS = {
    2004: ["CDU", "SPD", "PDS", "FDP", "GRÜNE", "Sonstige", "Wählergruppen", "Einzelbewerber"],
    2009: ["CDU", "DIE LINKE", "SPD", "FDP", "GRÜNE", "NPD", "Wählergruppen", "Einzelbewerber"],
    2014: ["CDU", "DIE LINKE", "SPD", "FDP", "GRÜNE", "NPD", "Wählergruppen", "Einzelbewerber"],
    2019: [
        "CDU",
        "SPD",
        "DIE LINKE",
        "FDP",
        "GRÜNE",
        "NPD",
        "AfD",
        "Bündnis C",
        "Die Achtsamen",
        "Die PARTEI",
        "DKP",
        "FREIE WÄHLER",
        "PIRATEN",
        "FPA",
        "Tierschutzpartei",
        "Wählergruppen",
        "Einzelbewerber",
    ],
    2024: [
        "CDU",
        "DIE LINKE",
        "SPD",
        "AfD",
        "GRÜNE",
        "FDP",
        "Die PARTEI",
        "HEIMAT",
        "FREIE WÄHLER",
        "Tierschutzpartei",
        "PIRATEN",
        "DKP",
        "dieBasis",
        "BÜNDNIS DEUTSCHLAND",
        "BSW",
        "LfK",
        "Volt",
        "Wählergruppen",
        "Einzelbewerbende",
    ],
}


def vote(year: int) -> Vote:
    report = REPORTS[year]
    artifact = raw.cached(SOURCE, report.url)
    rows = pl.read_excel(artifact.path, sheet_name=report.sheet, has_header=False, infer_schema_length=0).rows()
    labels = LABELS[year]
    if len(labels) != report.n:
        raise ValueError(f"{year}: {len(labels)} labels for {report.n} party columns")
    header = 0
    results: list[Result] = []
    for row in rows[header:]:
        ags = str(row[report.key_column] or "").strip()
        if not re.fullmatch(r"13\d{6}", ags):
            continue
        if report.anzahl and str(row[2]).strip() != "Anzahl":
            continue
        entries: list[Entry] = []
        for i, text in enumerate(labels):
            low = text.lower()
            if low.startswith("wähler"):
                kind, clean = "OW", "Wählergruppen (Summe)"
            elif low.startswith("einzel"):
                kind, clean = "OE", "Einzelbewerber/-innen (Summe)"
            elif low.startswith("sonst"):
                kind, clean = "OS", "Sonstige Parteien (Summe)"
            else:
                kind, clean = "P", text
            seat_column = report.first + report.n + (1 if report.seats_total is not None else 0) + i
            entries.append(Entry(clean, kind, count(row[report.first + i]), count(row[seat_column])))
        seats = count(row[report.seats_total]) if report.seats_total is not None else sum(e.seats or 0 for e in entries)
        valid = count(row[report.valid])
        total = sum(entry.votes or 0 for entry in entries)
        if valid is not None and total != valid:
            raise ValueError(f"{year} {ags}: list votes {total} differ from the printed valid votes {valid}")
        if sum(entry.seats or 0 for entry in entries) != seats:
            raise ValueError(f"{year} {ags}: list seats differ from the printed total {seats}")
        results.append(
            Result(
                ags=ags,
                name=" ".join(str(row[report.name_column]).split()),
                registered=count(row[3]),
                voters=count(row[report.voters]),
                invalid=None,
                valid_ballots=None,
                seats=seats,
                entries=entries,
            )
        )
    return Vote(report.day, "hare", results, [artifact], None)


def parse() -> None:
    all_votes = votes()
    common.geo(LAND, all_votes.values()).write()
    common.registry(LAND, all_votes.values()).write()
    for year, item in all_votes.items():
        common.election(LAND, item, str(year)).write()


def emit() -> None:
    package.run("load", packages())
