"""Municipal council elections in Saxony, 2024, by municipality.

The Statistisches Landesamt des Freistaates Sachsen publishes the final result of the June 9, 2024 Gemeinderat
elections as two workbooks, one with votes and one with seats, each with a sheet of municipalities and one of
electoral districts. This module reads the municipality sheets.
"""

import re
from datetime import date

import polars as pl

from ea import package, raw
from ea.sources.de import gemeinderat as common
from ea.sources.de.gemeinderat import Entry, Land, Result, Vote, count

SOURCE = "de.gemeinderat_sn"
BASE = "https://wahlen.sachsen.de/download/Gemeinderat"
VOTES_2024 = f"{BASE}/statistik-sachsen_GR24_EE_SN_LK_KS_GE.xlsx"
SEATS_2024 = f"{BASE}/statistik-sachsen_GR24_Sitze_SN_LK_KS_GE.xlsx"
SHEET = "GR24_EE_KS_GE"
PARTIES = [
    "CDU",
    "AfD",
    "DIE LINKE",
    "GRÜNE",
    "SPD",
    "FDP",
    "Die PARTEI",
    "HEIMAT",
    "PIRATEN",
    "DSU",
    "dieBasis",
    "FREIE SACHSEN",
    "BSW",
    "BÜNDNIS DEUTSCHLAND",
    "Volt",
]
LAND = Land(
    source=SOURCE,
    code="14",
    slug="saxony",
    name="Saxony",
    body="Gemeinderat",
    publisher="Statistisches Landesamt des Freistaates Sachsen",
    title="Gemeinderatswahlen 2024 im Freistaat Sachsen: Endgültige Ergebnisse",
    url="https://wahlen.sachsen.de/gemeinderatswahlen-2024.html",
    license="Datenlizenz Deutschland, Namensnennung, Version 2.0",
    license_url="https://www.govdata.de/dl-de/by-2-0",
    attribution="Statistisches Landesamt des Freistaates Sachsen",
    method=(
        "Counted by the Statistisches Landesamt des Freistaates Sachsen and published for each municipality. Each "
        "voter has as many votes as the council has seats, may give a candidate up to three of them, and may spread "
        "them over several lists, so a list's votes are the sum of its candidates' votes and are many times the "
        "number of voters. Registered voters are the electoral roll, voters are those who took part, and valid and "
        "invalid ballots are the ballot papers counted as such. Voters' associations and individual proposals "
        "(independent candidates) are printed as one sum each for the municipality, not by name. Seats are as "
        "declared by the source."
    ),
    system_note=(
        "Each voter has as many votes as the council has seats, up to three for one candidate, which may be spread "
        "over several lists (panachage). Seats are shared among the lists by the Sainte-Laguë/Schepers method on "
        "the sum of their candidates' votes, and a list's seats go to its candidates in the order of their votes. "
        "The declared seats are stored as published."
    ),
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2024"]


def fetch() -> None:
    raw.fetch(SOURCE, VOTES_2024, suffix=".xlsx")
    raw.fetch(SOURCE, SEATS_2024, suffix=".xlsx")


def votes() -> dict[str, Vote]:
    votes_file = raw.cached(SOURCE, VOTES_2024)
    seats_file = raw.cached(SOURCE, SEATS_2024)
    ballots = pl.read_excel(votes_file.path, sheet_name=SHEET, has_header=False, infer_schema_length=0).rows()
    seats = pl.read_excel(seats_file.path, sheet_name=SHEET, has_header=False, infer_schema_length=0).rows()
    seated = {str(row[5]): row for row in seats if str(row[5] or "").strip() and re.fullmatch(r"14\d{6}", str(row[5]))}
    results: list[Result] = []
    for row in ballots:
        ags = str(row[5] or "").strip()
        if row[4] not in ("KS", "GE") or not re.fullmatch(r"14\d{6}", ags):
            continue
        won = seated[ags]
        entries = [Entry(label, "P", count(row[17 + i]), count(won[10 + i])) for i, label in enumerate(PARTIES)]
        entries.append(Entry("Wählervereinigungen (Summe)", "OW", count(row[32]), count(won[25])))
        entries.append(Entry("Einzelvorschläge (Summe)", "OE", count(row[33]), count(won[26])))
        total = sum(entry.votes or 0 for entry in entries)
        if total != count(row[16]):
            raise ValueError(f"{ags}: list votes {total} differ from the printed valid votes {row[16]}")
        given = count(won[8])
        if sum(entry.seats or 0 for entry in entries) != given:
            raise ValueError(f"{ags}: list seats differ from the printed total {given}")
        results.append(
            Result(
                ags=ags,
                name=" ".join(str(row[6]).split()),
                registered=count(row[10]),
                voters=count(row[11]),
                invalid=count(row[14]),
                valid_ballots=count(row[15]),
                seats=given,
                entries=entries,
            )
        )
    return {"2024": Vote(date(2024, 6, 9), "sainte_lague_schepers", results, [votes_file, seats_file], None)}


def parse() -> None:
    all_votes = votes()
    common.geo(LAND, all_votes.values()).write()
    common.registry(LAND, all_votes.values()).write()
    for name, item in all_votes.items():
        common.election(LAND, item, name).write()


def emit() -> None:
    package.run("load", packages())
