"""Kreistag elections of Mecklenburg-Vorpommern: the Kreistagswahlen of 2011, 2014, 2019, and 2024 in the six Landkreise and two kreisfreie Städte.

The Landesamt für innere Verwaltung publishes, for each Kommunalwahl, semicolon-separated files of the final results.
The Wahlkreis file gives, for each Landkreis and for the Stadtvertretung of Schwerin and the Bürgerschaft of Rostock,
the registered voters, voters, and the votes of every nomination; the Mandate file gives the seats of every nomination.
Each voter has three votes. The two kreisfreie Städte elect a council that the Land counts with the Kreistage; each is
one contest.
"""

import csv
import io
import re
from datetime import date
from functools import cache

from ea import raw
from ea.sources.de.kreistag import SOURCE, Edition, Kreis, Land, Row, areas

MV = "01a13bb5-c563-741b-83f7-4b845c3bd9f1"
SITE = "https://www.laiv-mv.de"
STATIC = f"{SITE}/static/LAIV/Wahlen"
DAYS = {2011: date(2011, 9, 4), 2014: date(2014, 5, 25), 2019: date(2019, 5, 26), 2024: date(2024, 6, 9)}
PAGES = {year: f"{SITE}/Wahlen/Kommunalwahlen/{year}/Ergebnisse/" for year in DAYS}
FILES = {
    2011: (
        f"{STATIC}/3-Kommunalwahlen/2011/Ergebnisse/K_Wahlkreise.csv",
        f"{STATIC}/3-Kommunalwahlen/2011/Ergebnisse/K_Mandate.csv",
    ),
    2014: (
        f"{STATIC}/3-Kommunalwahlen/2014/Ergebnisse/K_Wahlkreise.csv",
        f"{STATIC}/3-Kommunalwahlen/2014/Ergebnisse/K_Mandate.csv",
    ),
    2019: (
        f"{STATIC}/KW/KW%202019/Ergebnisse/kw2019k_wahlkreise.csv",
        f"{STATIC}/KW/KW%202019/Ergebnisse/kw2019k_mandate.csv",
    ),
    2024: (
        f"{STATIC}/3-Kommunalwahlen/2024/Ergebnisse/k_wahlkreise.csv",
        f"{STATIC}/3-Kommunalwahlen/2024/Ergebnisse/k_mandate.csv",
    ),
}
KREISE = {
    "3": "13003",
    "4": "13004",
    "71": "13071",
    "72": "13072",
    "73": "13073",
    "74": "13074",
    "75": "13075",
    "76": "13076",
}
PARTIES = {
    "CDU", "SPD", "DIE LINKE", "GRÜNE", "AfD", "FDP", "NPD", "PIRATEN", "FREIE WÄHLER", "HEIMAT", "Die PARTEI",
    "Tierschutzpartei", "BSW", "dieBasis", "DKP", "ÖDP", "Bündnis C", "BÜNDNIS DEUTSCHLAND", "MLPD", "Volt",
    "FAMILIE", "RENTNER", "AUF", "BFB",
}  # fmt: skip
EINZEL = "Einzelbewerber"
GAPS = [
    (
        "2009 and earlier (1990, 1994, 1999, 2004, 2009): the Landesamt's pages and files give the results of the "
        "Kreistage of the twelve Landkreise and six kreisfreie Städte that existed before the Kreisgebietsreform of "
        "4 September 2011; the archive holds no areas for those Kreise, so none is stored."
    ),
    (
        "Wahlbezirk and Gemeinde files: the Landesamt also publishes the votes by Gemeinde and Wahlbezirk; only the "
        "Kreis, the electorate of each Kreistag, is stored."
    ),
]
NOTE = (
    "Final results as determined by the Wahlausschüsse and published by the Landeswahlleiter of "
    "Mecklenburg-Vorpommern. Each voter has three votes, which may be given to one candidate (cumulating) or spread "
    "across candidates (panachage), so a nomination's votes are not ballots. Registered voters are those entitled to "
    "vote and voters are those who took part. The valid votes are the sum of the nominations' votes. The source "
    "prints a count of invalid votes, not of invalid ballots, and none is stored. The seats are the seats actually "
    "filled (including compensation seats), as the Mandate file prints them."
)


def fetch() -> None:
    for urls in FILES.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".csv")


def table(url: str) -> tuple[list[str], list[list[str]]]:
    path = raw.cached(SOURCE, url).path
    text = path.read_bytes().decode("latin-1")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if "Wahlbezirke insg." in line)
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=";")]
    return rows[0], rows[1:]


def whole(cell: str) -> int | None:
    if cell in ("", "x", "-", "."):
        return None
    return int(cell.replace(" ", ""))


def lower(cell: str) -> str:
    return cell.casefold().replace("ä", "ae").replace("ü", "ue")


def column(head: list[str], *names: str) -> int:
    wanted = {lower(n) for n in names}
    return next(i for i, h in enumerate(head) if lower(h) in wanted)


def label_of(label: str) -> tuple[str, bool]:
    label = " ".join(label.split())
    return label, label not in PARTIES


@cache
def kreise(year: int) -> dict[str, Kreis]:
    votes_url, seats_url = FILES[year]
    head, rows = table(votes_url)
    first = column(head, "gültige Stimmen", "gueltige Stimmen") + 1
    mhead, mrows = table(seats_url)
    mfirst = column(mhead, "Insgesamt") + 1
    mkreis = column(mhead, "Kreis", "Wahlkreis")
    ist = {
        r[mkreis]: r for r in mrows if len(r) > mfirst and r[column(mhead, "Mandate")] == "Ist" and r[mkreis] in KREISE
    }
    known = areas()
    found: dict[str, Kreis] = {}
    kreis_at = column(head, "Kreis", "Wahlkreis")
    for row in rows:
        if len(row) < first or row[column(head, "Ausgabe")] != "A" or row[kreis_at] not in KREISE:
            continue
        ags = KREISE[row[kreis_at]]
        if ags not in known:
            GAPS.append(f"{year} Kreis {ags} {row[kreis_at + 1]}: no area in the archive")
            continue
        seat_row = ist[row[kreis_at]]
        seats: dict[str, int] = {}
        votes: dict[str, int] = {}
        for i in range(first, len(head)):
            if not head[i]:
                continue
            label = " ".join(head[i].split())
            v = whole(row[i]) if i < len(row) else None
            s = whole(seat_row[mfirst + (i - first)]) if mfirst + (i - first) < len(seat_row) else None
            if mhead[mfirst + (i - first)].strip() != head[i].strip():
                raise ValueError(
                    f"{year} {ags}: seat column {i - first} is {mhead[mfirst + (i - first)]!r}, not {head[i]!r}"
                )
            if v:
                votes[label] = votes.get(label, 0) + v
            if s is not None:
                seats[label] = seats.get(label, 0) + s
        out = [Row(label, v, None, seats.get(label, 0), label_of(label)[1]) for label, v in votes.items()]
        for label, s in seats.items():
            if label not in votes and s:
                raise ValueError(f"{year} {ags}: {label} won seats without votes")
        valid = whole(row[column(head, "gültige Stimmen", "gueltige Stimmen")])
        if sum(r.votes or 0 for r in out) != valid:
            raise ValueError(f"{year} {ags}: nomination votes differ from the valid votes")
        total = whole(seat_row[mfirst - 1])
        if sum(r.seats or 0 for r in out) != total:
            raise ValueError(f"{year} {ags}: seats of the nominations differ from the Kreistag's size")
        name = re.sub(r"^Hansestadt ", "", row[kreis_at + 1]) if row[kreis_at] == "3" else row[kreis_at + 1]
        found[ags] = Kreis(
            ags[2:],
            name,
            whole(row[column(head, "Wahlberechtigte")]),
            whole(row[column(head, "Waehler", "Wähler")]),
            None,
            total,
            out,
            None,
        )
    return found


def editions() -> list[Edition]:
    return [
        Edition(
            year=year,
            day=DAYS[year],
            page=PAGES[year],
            title=f"Kommunalwahlen in Mecklenburg-Vorpommern am {DAYS[year].day}.{DAYS[year].month}.{year}: Endgültiges Ergebnis der Kreistage",
            files=list(FILES[year]),
            kreise=lambda year=year: kreise(year),
            note=NOTE,
        )
        for year in sorted(DAYS)
    ]


LAND = Land(
    key="mv",
    polity=MV,
    name="Mecklenburg-Vorpommern",
    publisher="Landesamt für innere Verwaltung Mecklenburg-Vorpommern",
    license="Vervielfältigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet",
    license_url=None,
    editions=editions,
    fetch=fetch,
)
