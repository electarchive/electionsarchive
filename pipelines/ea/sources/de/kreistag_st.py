"""Kreistag elections of Saxony-Anhalt: the Kreistagswahlen of 2019 and 2024 in the eleven Landkreise.

The Statistisches Landesamt Sachsen-Anhalt publishes the final results of each Kommunalwahl as semicolon-separated
files with a row for the Land, each Kreis, and each Gemeinde: registered voters, voters, invalid and valid ballots,
valid votes, the votes of every party, and the seats of every party. Each voter has three votes. The Landesamt gives
the votes and seats of the voter groups (Wählergruppen) and of the individual candidates only as sums in the Kreis
files, so each is stored as one row. The three kreisfreien Städte elect a Stadtrat, which the files carry beside the
Kreistage; each is one contest.
"""

import csv
import io
import re
from datetime import date
from functools import cache

from ea import raw
from ea.sources.de.kreistag import SOURCE, Edition, Kreis, Land, Row, areas

ST = "01a13bb5-c563-741b-83f7-4b88a681db04"
SITE = "https://wahlergebnisse.sachsen-anhalt.de/wahlen"
DAYS = {2019: date(2019, 5, 26), 2024: date(2024, 6, 9)}
PAGES = {2019: f"{SITE}/kw19/index.php", 2024: f"{SITE}/kw24/index.php"}
FILES = {2019: f"{SITE}/kw19/and/KW2019_LAN_KRS.csv", 2024: f"{SITE}/kw24/erg/csv/kw24dat2.csv"}
LABELS_2019 = {
    "D01": "CDU", "D02": "AfD", "D03": "DIE LINKE", "D04": "SPD", "D05": "GRÜNE", "D06": "FDP",
    "D07": "FREIE WÄHLER", "D08": "NPD", "D09": "Tierschutzpartei", "D11": "Tierschutzallianz",
    "D12": "Die PARTEI", "D13": "Gartenpartei", "D14": "FBM", "D16": "STATT Partei",
}  # fmt: skip
WG = "Wählergruppen"
EB = "Einzelbewerber"
GROUPS = {WG, EB}
ACCEPT_REASON = (
    "Each voter has three votes, so the votes of the nominations exceed the valid ballots the Landesamt prints "
    "beside them; both figures are the Landesamt's own."
)
ACCEPTED = {year: ("accounting.votes_valid", ACCEPT_REASON) for year in DAYS}
GAPS = [
    (
        "2014 and earlier (1994, 1999, 2004, 2007, 2009, 2014): the Landesamt's pages for these elections print the "
        "results of each Kreis as separate HTML tables (kw14, kw09, kw07, kw04, kw99, kw94 under /wahlen/) that are not "
        "yet read, and offer no file."
    ),
    (
        "Wählergruppen and Einzelbewerber: the Kreis files give each as one sum; the individual voter groups and "
        "candidates (46 groups and 13 candidates in 2024, 7 groups and 2 candidates in 2019) are named only on the "
        "Landesamt's Kreis pages and stored here as one row each."
    ),
    "Gemeinde and Wahllokal rows: the files also hold the votes by Gemeinde and by polling-place type; only the Kreis is stored.",
]
NOTE = (
    "Final results as determined by the Kreiswahlausschüsse and compiled by the Statistisches Landesamt "
    "Sachsen-Anhalt. Each voter has three votes, which may be given to one candidate (cumulating) or spread across "
    "candidates (panachage), so a nomination's votes are not ballots. Registered voters are those entitled to vote, "
    "voters are those who took part, and invalid and valid are the ballot papers. The source adds the votes and "
    "seats of all voter groups (Wählergruppen) and of all individual candidates, so each is stored as one row."
)


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".csv")


def whole(cell: str) -> int | None:
    cell = cell.strip()
    return int(cell) if cell else None


def table(year: int) -> tuple[list[str], list[list[str]]]:
    text = raw.cached(SOURCE, FILES[year]).path.read_bytes().decode("latin-1")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    return [cell.strip() for cell in rows[0]], rows[1:]


@cache
def kreise(year: int) -> dict[str, Kreis]:
    head, rows = table(year)
    sums = [i for i, h in enumerate(head) if h == "WG (Summe)"]
    seats_at = next(i for i, h in enumerate(head) if h == "S" or h.startswith("S - "))
    voted_at = next(i for i, h in enumerate(head) if h == "D" or h.startswith("D - "))
    first = [i for i in range(voted_at + 1, sums[0]) if head[i][:1] == "D"]
    parties = []
    for i in first:
        code = head[i].split(" - ")[0]
        label = LABELS_2019[code] if year == 2019 else head[i].split(" - ", 1)[1]
        if re.fullmatch(r"(WG|EB)\d+", label):
            continue
        parties.append((label, i, seats_at + 1 + (i - voted_at - 1)))
    for label, i, s in parties:
        code = head[i].split(" - ")[0]
        if not head[s].startswith(code.replace("D", "S")):
            raise ValueError(f"{year}: seat column {head[s]!r} does not match {head[i]!r}")
    kind = next(i for i, h in enumerate(head) if h in ("SART", "Satzart"))
    key = next(i for i, h in enumerate(head) if h in ("AGS", "Schlüsselnummer"))
    place = next((i for i, h in enumerate(head) if h == "Wahllokal"), None)
    known = areas()
    found: dict[str, Kreis] = {}
    for row in rows:
        if row[kind].strip() != "KRS" or (place is not None and row[place].strip()):
            continue
        ags = row[key].strip()
        if ags not in known:
            GAPS.append(f"{year} Kreis {ags} {row[key + 1].strip()}: no area in the archive")
            continue
        cells = [(label, whole(row[i]), whole(row[s])) for label, i, s in parties]
        cells.append((WG, whole(row[sums[0]]), whole(row[sums[1]])))
        cells.append((EB, whole(row[sums[0] + 1]), whole(row[sums[1] + 1])))
        out = [Row(label, v, None, s or 0, label in GROUPS) for label, v, s in cells if v or s]
        valid = whole(row[voted_at])
        if sum(r.votes or 0 for r in out) != valid:
            raise ValueError(f"{year} {ags}: nomination votes differ from the valid votes")
        total = whole(row[seats_at])
        if sum(r.seats or 0 for r in out) != total:
            raise ValueError(f"{year} {ags}: seats of the nominations differ from the Kreistag's size")
        name = row[key + 1].strip()
        found[ags] = Kreis(
            ags[2:],
            name,
            whole(row[voted_at - 4]),
            whole(row[voted_at - 3]),
            whole(row[voted_at - 2]),
            total,
            out,
            whole(row[voted_at - 1]),
        )
    return found


def editions() -> list[Edition]:
    return [
        Edition(
            year=year,
            day=DAYS[year],
            page=PAGES[year],
            title=f"Kommunalwahlen {year} in Sachsen-Anhalt: Kreistagswahlen am {DAYS[year].day}.{DAYS[year].month}.{year}, endgültige Ergebnisse",
            files=[FILES[year]],
            kreise=lambda year=year: kreise(year),
            note=NOTE,
        )
        for year in sorted(DAYS)
    ]


LAND = Land(
    key="st",
    polity=ST,
    name="Saxony-Anhalt",
    publisher="Statistisches Landesamt Sachsen-Anhalt",
    license="Vervielfältigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet",
    license_url=None,
    editions=editions,
    fetch=fetch,
)
