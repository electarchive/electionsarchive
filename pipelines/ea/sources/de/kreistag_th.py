"""Kreistag elections of Thuringia: the Kreistagswahlen of 1994, 1999, 2004, 2009, 2014, 2019, and 2024, and the Wartburgkreis's of 2021.

The Thüringer Landesamt für Statistik publishes the final results of each Kommunalwahl in an online database. Its page
for each Landkreis and kreisfreie Stadt gives the registered voters, voters, invalid and valid ballots, valid votes,
the seats to fill, and, for every nomination, the votes and the seats. Each voter has three votes. The kreisfreien
Städte elect a Stadtrat, which the Landesamt lists with the Kreistage; each is one contest. Eisenach was a kreisfreie
Stadt until it joined the Wartburgkreis on 1 July 2021.
"""

import html
import re
from datetime import date
from functools import cache

from ea import raw
from ea.sources.de.kreistag import SOURCE, Edition, Kreis, Land, Row, areas

TH = "01a13bb5-c563-741b-83f7-4b8a6fb78423"
SITE = "https://wahlen.thueringen.de"
PAGE = f"{SITE}/kommunalwahlen/kw_wahlergebnisse_KW.asp"
DAYS = {
    1994: date(1994, 6, 12),
    1999: date(1999, 6, 13),
    2004: date(2004, 6, 27),
    2009: date(2009, 6, 7),
    2014: date(2014, 5, 25),
    2019: date(2019, 5, 26),
    2021: date(2021, 6, 20),
    2024: date(2024, 5, 26),
}
CODES = ["051", "052", "053", "054", "055", "056", *(f"{n:03d}" for n in range(61, 78))]
ONLY_2021 = ["063"]
PARTIES = {
    "CDU", "SPD", "PDS", "DIE LINKE", "LINKE", "F.D.P.", "FDP", "GRÜNE", "B90/Gr.", "BÜNDNIS 90/DIE GRÜNEN", "AfD",
    "NPD", "REP", "DVU", "PIRATEN", "Piraten", "ÖDP", "Die PARTEI", "Tierschutzpartei", "FREIE WÄHLER", "Volt",
    "DKP", "MLPD", "Bündnis 90/Grüne", "FAMILIE", "PRO DM", "BGE", "dieBasis", "Die Basis", "BSW", "HEIMAT",
    "Gartenpartei", "Tierschutzallianz", "III. Weg", "Partei der Vernunft", "Die Humanisten",
}  # fmt: skip
ACCEPT_REASON = (
    "Each voter has three votes, so the votes of the nominations exceed the valid ballots the Landesamt prints "
    "beside them; both figures are the Landesamt's own."
)
ACCEPTED = {year: ("accounting.votes_valid", ACCEPT_REASON) for year in DAYS}
GAPS = [
    (
        "Eisenach before 2021: the kreisfreie Stadt Eisenach (16056) elected a Stadtrat in 1999, 2004, 2009, 2014, "
        "and 2019 (the database's 1994 page for it is empty), and the archive holds no area for it since it joined the "
        "Wartburgkreis, so those five contests are not stored."
    ),
    (
        "1990: the Landesamt publishes the 6 May 1990 results of the Kreistage of the former districts as one "
        "workbook (Daten1990/KK90Kreise_Gemeinden.xlsx); the archive holds no areas for those districts, so none is stored."
    ),
    "Gemeinde and Wahlkreis pages: the database also gives the results by Gemeinde; only the Kreis is stored.",
]
NOTE = (
    "Final results as determined by the Kreiswahlausschüsse and published by the Thüringer Landesamt für Statistik. "
    "Each voter has three votes, which may be given to one candidate (cumulating) or spread across candidates "
    "(panachage), so a nomination's votes are not ballots. Registered voters are those entitled to vote, voters are "
    "those who took part, and invalid and valid are the ballot papers."
)


def url(year: int, code: str) -> str:
    return f"{SITE}/datenbank/wahl1/WAHL.asp?wahlart=KW&wJahr={year}&zeigeErg=WK&auswertung=1&wknr={code}&anzahlH=0"


def codes(year: int) -> list[str]:
    """The Kreis codes the database holds pages with results for in a year (Eisenach has none in 1994 or from 2021)."""
    if year == 2021:
        return ONLY_2021
    return [code for code in CODES if code != "056" or 1999 <= year <= 2019]


def fetch() -> None:
    urls = [url(year, code) for year in DAYS for code in codes(year)]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=4)


def whole(cell: str) -> int | None:
    cell = cell.replace("\xa0", "").replace(" ", "")
    return int(cell) if re.fullmatch(r"\d+", cell) else None


def count(pattern: str, flat: str) -> int | None:
    found = re.search(pattern, flat)
    if found is None:
        raise ValueError(f"no match for {pattern!r}")
    return whole(found.group(1))


def cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).replace("\xa0", " ").strip()
        for c in re.split(r"<td[^>]*>", row, flags=re.IGNORECASE)
    ]


def page(year: int, code: str) -> str:
    artifact = raw.cached(SOURCE, url(year, code))
    return artifact.path.read_bytes().decode("latin-1")


def label_of(label: str) -> tuple[str, bool]:
    return label, label.casefold() not in {p.casefold() for p in PARTIES}


@cache
def kreise(year: int) -> dict[str, Kreis]:
    known = areas()
    found: dict[str, Kreis] = {}
    for code in codes(year):
        ags = f"16{code}"
        text = page(year, code)
        flat = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " "))
        head = re.search(r"(Kreisfreie Stadt|Landkreis) (\d{3}) (.+?) Erfassungsstand", flat)
        if head is None or head.group(2) != code:
            raise ValueError(f"{year} {code}: the page names another Kreis")
        if ags not in known:
            GAPS.append(f"{year} Kreis {ags} {head.group(3)}: no area in the archive")
            continue

        registered = count(r"Wahlberechtigte ([\d ]+?) Wähler", flat)
        voters = count(r"Wähler ([\d ]+?) Wahlbeteiligung", flat)
        invalid = count(r"Ungültige Stimmabgaben ([\d ]+?) Gültige", flat)
        valid = count(r"Gültige Stimmabgaben ([\d ]+?) Gültige Stimmen", flat)
        votes_total = count(r"Gültige Stimmen ([\d ]+?) Zu vergebene", flat)
        seats_total = count(r"Zu vergebene Sitze (\d+)", flat)
        start = text.index("Wahlübersicht", text.index("Zu vergebene"))
        rows: list[Row] = []
        for tr in re.split(r"<tr[^>]*>", text[start:], flags=re.IGNORECASE):
            c = [x for x in cells(tr) if x != ""]
            if len(c) < 4 or not re.fullmatch(r"\d+", c[0]) or whole(c[2]) is None:
                continue
            label, group = label_of(c[1])
            seats = 0 if c[3] in ("-", "–") else whole(c[3])
            if seats is None:
                continue
            rows.append(Row(label, whole(c[2]), None, seats, group))
        if sum(r.votes or 0 for r in rows) != votes_total:
            raise ValueError(f"{year} {code}: nomination votes differ from the valid votes")
        if sum(r.seats or 0 for r in rows) != seats_total:
            raise ValueError(f"{year} {code}: seats of the nominations differ from the Kreistag's size")
        name = head.group(3)
        if head.group(1) == "Landkreis" and "kreis" not in name.casefold():
            name = f"Landkreis {name}"
        found[ags] = Kreis(code, name, registered, voters, invalid, seats_total, rows, valid)
    return found


def editions() -> list[Edition]:
    return [
        Edition(
            year=year,
            day=DAYS[year],
            page=PAGE,
            title=f"Kommunalwahlen in Thüringen am {DAYS[year].day}.{DAYS[year].month}.{year}: Kreistagswahlen, endgültige Ergebnisse",
            files=[url(year, code) for code in codes(year)],
            kreise=lambda year=year: kreise(year),
            note=NOTE,
        )
        for year in sorted(DAYS)
    ]


LAND = Land(
    key="th",
    polity=TH,
    name="Thuringia",
    publisher="Thüringer Landesamt für Statistik",
    license="Vervielfältigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet",
    license_url=None,
    editions=editions,
    fetch=fetch,
)
