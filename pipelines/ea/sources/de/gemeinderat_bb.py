"""Municipal council elections in Brandenburg, 2019 and 2024, by municipality.

The Amt für Statistik Berlin-Brandenburg publishes the final result of each Gemeindevertretung and
Stadtverordnetenversammlung election as a workbook with one row per municipality and list, holding the list's votes
and seats, and the registered voters, voters, and invalid ballots of the municipality.
"""

from datetime import date

import polars as pl

from ea import package, raw
from ea.sources.de import gemeinderat as common
from ea.sources.de.gemeinderat import Entry, Land, Result, Vote, count

SOURCE = "de.gemeinderat_bb"
BASE = "https://download.statistik-berlin-brandenburg.de"
FILES = {
    2019: (date(2019, 5, 26), f"{BASE}/0f35d2839699a5ef/ebb64921486a/DL_BB_GV2019.xlsx", "BB_GVW2019"),
    2024: (date(2024, 6, 9), f"{BASE}/a2e0e827d982e4cb/066f096a4e6c/DL_BB_GVW2024.xlsx", "BB_GVW2024"),
}
LAND = Land(
    source=SOURCE,
    code="12",
    slug="brandenburg",
    name="Brandenburg",
    body="Gemeindevertretung",
    publisher="Amt für Statistik Berlin-Brandenburg",
    title="Kommunalwahlen im Land Brandenburg: Endgültige Ergebnisse der Gemeindevertretungswahlen",
    url="https://www.statistik-berlin-brandenburg.de/kommunalwahlen-brandenburg",
    license="Creative Commons Namensnennung 3.0 Deutschland (CC BY 3.0 DE)",
    license_url="https://creativecommons.org/licenses/by/3.0/de/",
    attribution="Amt für Statistik Berlin-Brandenburg",
    method=(
        "Counted by the Amt für Statistik Berlin-Brandenburg and published for each municipality. Each voter has "
        "three votes, may give a candidate up to three of them, and may spread them over several lists, so a list's "
        "votes are the sum of its candidates' votes and are up to three times the number of voters. Registered "
        "voters are the electoral roll, voters are those who took part, and invalid ballots are the ballot papers "
        "that were invalid. The source prints the valid votes but no count of valid ballots, and none is stored. "
        "Seats are as declared by the source."
    ),
    system_note=(
        "Each voter has three votes, up to three for one candidate, which may be spread over several lists "
        "(panachage). Seats are shared among the lists by the largest-remainder method of Hare and Niemeyer on the "
        "sum of their candidates' votes, with no threshold, and a list's seats go to its candidates in the order of "
        "their votes. A list can win more seats than its share when direct-mandate candidates are seated, so the "
        "declared seats are stored as published."
    ),
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in FILES)]


def fetch() -> None:
    for _, url, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")


def votes() -> dict[int, Vote]:
    return {year: vote(year) for year in FILES}


def vote(year: int) -> Vote:
    day, url, sheet = FILES[year]
    artifact = raw.cached(SOURCE, url)
    rows = pl.read_excel(artifact.path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()[1:]
    header = pl.read_excel(artifact.path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()[0]
    ars = "ARS" in str(header[1])
    grouped: dict[str, list[tuple[object, ...]]] = {}
    for row in rows:
        ags = str(row[1])
        ags = ags[:5] + ags[9:12] if ars else ags[:8]
        grouped.setdefault(ags, []).append(row)
    results: list[Result] = []
    for ags, group in sorted(grouped.items()):
        facts: dict[str, int | None] = {}
        entries: list[Entry] = []
        for row in group:
            kind = str(row[5] or "")
            short = str(row[6]).strip()
            if kind in ("P", "PV", "WG", "LV", "EB"):
                entries.append(Entry(common.label(short), kind, count(row[8]), count(row[10])))
            else:
                facts[short] = count(row[8])
                if short == "Sitze":
                    facts["Sitze_seats"] = count(row[8])
        valid_votes = facts.get("Gültig", facts.get("Gueltig"))
        total = sum(entry.votes or 0 for entry in entries)
        if valid_votes is not None and total != valid_votes:
            raise ValueError(f"{year} {ags}: list votes {total} differ from the printed valid votes {valid_votes}")
        seats = facts.get("Sitze")
        if seats is not None and sum(entry.seats or 0 for entry in entries) != seats:
            raise ValueError(f"{year} {ags}: list seats differ from the printed {seats}")
        results.append(
            Result(
                ags=ags,
                name=str(group[0][4]).strip(),
                registered=facts.get("Wahlberechtigt"),
                voters=facts.get("Wähler"),
                invalid=facts.get("Ungültig", facts.get("Ungueltig")),
                valid_ballots=None,
                seats=seats,
                entries=entries,
            )
        )
    note = None
    if year == 2024:
        note = (
            "The Stadtverordnetenversammlung election in Cottbus was declared invalid in voting district 3101 and "
            "repeated there on September 22, 2024; the figures include the repeat."
        )
    return Vote(day, "hare", results, [artifact], note)


def parse() -> None:
    all_votes = votes()
    common.geo(LAND, all_votes.values()).write()
    common.registry(LAND, all_votes.values()).write()
    for year, item in all_votes.items():
        common.election(LAND, item, str(year)).write()


def emit() -> None:
    package.run("load", packages())
