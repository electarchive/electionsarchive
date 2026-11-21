"""Kreistag elections of Lower Saxony: the Kreiswahlen of 2011, 2016, and 2021 in the Landkreise and the Region Hannover.

The Landesamt für Statistik Niedersachsen (LSN) publishes the final result of every Kommunalwahl in its WahlServer
as a CSV file with one row per Wahlkreis (the eight kreisfreie Städte, the 36 Landkreise, and the Region Hannover):
registered voters, voters, valid ballots, valid votes, seats, and the votes and seats of every list. The Kreistage
of the Landkreise and the Regionsversammlung of the Region Hannover are the Kreiswahlen; the kreisfreie Städte
elect their Räte in the Gemeindewahlen, so their rows are left out. Each voter has as many votes as the body has
seats and may cumulate up to three on one candidate.
"""

import csv
from datetime import date
from functools import cache

import polars as pl

from ea import raw
from ea.sources.de.kreistag import REFERENCE, SOURCE, Edition, Kreis, Land, Row

NI = "01a13bb5-c563-741b-83f7-4b701b06def3"
WAHLSERVER = "https://wahlen.statistik.niedersachsen.de"
PAGE = "https://www.statistik.niedersachsen.de/themen/Kommunalwahlen-niedersachsen"
DAYS = {2011: date(2011, 9, 11), 2016: date(2016, 9, 11), 2021: date(2021, 9, 12)}
KREISFREI = {"101", "102", "103", "401", "402", "403", "404", "405"}
#: Kreise whose territory changed so that no area production holds fits the earlier election.
PRE_MERGER = {2011: {"152": "Göttingen", "156": "Osterode am Harz"}}
LABELS = {
    "GRÜNE": "GRÜNE",
    "DIE LINKE.": "Die Linke",
    "AfD Niedersachsen": "AfD",
    "ALFA Niedersachsen": "ALFA",
    "dieBasis LV Niedersachsen": "dieBasis",
    "DiB Niedersachsen": "DiB",
    "du. - LV Niedersachsen": "du.",
    "LKR Niedersachsen": "LKR",
    "FREIE WÄHLER": "FREIE WÄHLER",
    "ÖDP": "ÖDP",
    "PIRATEN Niedersachsen": "PIRATEN",
    "Die PARTEI Niedersachsen": "Die PARTEI",
    "PARTEI DER VERNUNFT": "Partei der Vernunft",
    "Neue Liberale - Die Sozialliberalen- Niedersachsen": "Neue Liberale",
    "Tierschutzpartei": "Tierschutzpartei",
}
GROUPS = {"WGR": "Wählergruppen", "EB": "Einzelbewerber"}
GAPS = [
    "2026: the WahlServer prints only the provisional result (13 September 2026); no final result is published yet.",
    (
        "2006 and earlier: the LSN prints the earlier Kreiswahlen only in its Statistische Berichte (B VII 3.4); "
        "the 2006 volume is a table PDF without a result file."
    ),
    (
        "2011: Landkreis Göttingen and Landkreis Osterode am Harz elected separate Kreistage in the territories they "
        "had before the merger of 1 November 2016; production holds only the merged Landkreis Göttingen."
    ),
]


def url(year: int) -> str:
    return f"{WAHLSERVER}/KW{year}/kreiswahlergebnis.csv"


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".csv")


@cache
def names() -> dict[str, str]:
    """The name production holds for each Kreis, by its AGS."""
    found = pl.read_parquet(REFERENCE / "area.parquet").join(
        pl.read_parquet(REFERENCE / "area_code.parquet"), left_on="id", right_on="area_id"
    )
    found = found.filter((pl.col("scheme_code") == "de.kreis") & pl.col("valid_to").is_null())
    found = found.filter(pl.col("code").str.starts_with("03"))
    return dict(zip(found["code"].to_list(), found["name"].to_list(), strict=True))


def whole(cell: str) -> int:
    cell = cell.strip()
    return int(cell) if cell else 0


def kreise(year: int) -> dict[str, Kreis]:
    path = raw.cached(SOURCE, url(year)).path
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    head = rows[0]
    lists = [(i, c[: -len(" Stimmen")]) for i, c in enumerate(head) if c.endswith(" Stimmen") and i > 4]
    pool = names()
    found: dict[str, Kreis] = {}
    for row in rows[1:]:
        code = row[0].strip()
        if code in KREISFREI or code in PRE_MERGER.get(year, {}):
            continue
        ags = "03" + code
        name = "Region Hannover" if code == "241" else f"Landkreis {pool[ags]}"
        figures = {c: whole(row[i]) for i, c in enumerate(head[:6])}
        voters = figures["Wähler"]
        total = figures["Gültige Stimmen"]
        seats = figures["Sitze"]
        out: list[Row] = []
        for i, label in lists:
            votes, won = whole(row[i]), whole(row[i + 1])
            if not votes and not won:
                continue
            group = label in GROUPS
            out.append(Row(GROUPS.get(label, LABELS.get(label, label)), votes, None, won, group))
        if sum(r.votes or 0 for r in out) != total:
            raise ValueError(f"{year} {code}: list votes differ from the valid votes")
        if sum(r.seats or 0 for r in out) != seats:
            raise ValueError(f"{year} {code}: list seats differ from the seats")
        found[ags] = Kreis(ags[2:], name, figures["Wahlberechtigte"], voters, None, seats, out)
    return found


NOTE = (
    "Final results of the Kreiswahlen as determined by the Kreiswahlausschüsse and compiled by the Landesamt für "
    "Statistik Niedersachsen. Each voter has as many votes as the body has seats and may give up to three to one "
    "candidate (cumulating) or spread them across lists (panachage), so a list's votes are not ballots. Registered "
    "voters are those entitled to vote, voters are those who took part, and the source declares no invalid ballots here. It counts no valid "
    "ballots by votes, so none is stored. "
    "Seats are those won; the source lists the seats a list could not fill separately."
)


def editions() -> list[Edition]:
    return [
        Edition(
            year=year,
            day=day,
            page=f"{WAHLSERVER}/KW{year}/",
            title=f"Kommunalwahlen in Niedersachsen am {day.day}.{day.month}.{year}: Endgültiges Ergebnis der Kreiswahlen",
            files=[url(year)],
            kreise=lambda year=year: kreise(year),
            note=NOTE,
        )
        for year, day in sorted(DAYS.items())
    ]


LAND = Land(
    key="ni",
    polity=NI,
    name="Lower Saxony",
    publisher="Landesamt für Statistik Niedersachsen",
    license="Vervielfältigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet",
    license_url=None,
    editions=editions,
    fetch=fetch,
)
