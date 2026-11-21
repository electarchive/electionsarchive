"""Kreistag elections of Brandenburg: the Kreistagswahlen of 2019 and 2024 in the fourteen Landkreise and four kreisfreie Städte.

The Amt für Statistik Berlin-Brandenburg publishes, for the Landeswahlleiter, the final results of each Kommunalwahl as
workbooks. For 2024 the Ergebnisbericht B VII 3 - 3 prints the seats of every nomination in each Landkreis and
kreisfreie Stadt, and a data workbook gives the registered voters, voters, invalid ballots, valid votes, and the votes of
every nomination. For 2019 the data workbook gives the votes of every nomination; the seats are printed in a report that
the page does not link, so the 2019 results carry votes and no seats. The kreisfreien Städte elect a
Stadtverordnetenversammlung, which the Land treats as the Kreistag counterpart; they are one contest each.
"""

import re
from datetime import date
from functools import cache

import fastexcel

from ea import raw
from ea.sources.de.kreistag import REFERENCE, SOURCE, Edition, Kreis, Land, Row

BB = "01a13bb5-c563-741b-83f7-4b829f1533bb"
PAGE = "https://www.statistik-berlin-brandenburg.de/kommunalwahlen-brandenburg"
DL = "https://download.statistik-berlin-brandenburg.de"
DAYS = {2019: date(2019, 5, 26), 2024: date(2024, 6, 9)}
VOTES = {
    2019: f"{DL}/636627044e1d256b/e24d4b6bcb3a/DL_BB_2_KW2019.xlsx",
    2024: f"{DL}/ce4c4adabd35888e/e21672e4094b/DL_BB_2_KW2024.xlsx",
}
REPORT_2024 = f"{DL}/5d572a6fc2f81449/259a5d34a4f4/SB_B07-03-03_2024j05_BB.xlsx"
LONG_SERIES = f"{DL}/69a244354cfe4f30/bd0af3b695b0/Kommunalwahlen_Lange-Reihe_2024_Brandenburg.xlsx"
PARTIES = {
    "CDU", "SPD", "AfD", "DIE LINKE", "GRÜNE/B 90", "FDP", "NPD", "HEIMAT", "PIRATEN", "Die PARTEI",
    "Tierschutzpartei", "III. Weg", "Volt", "BVB / FREIE WÄHLER", "BVB/FREIE WÄHLER", "FREIE WÄHLER",
}  # fmt: skip
GAPS = [
    (
        "2019: the Landesamt's page links no Ergebnisbericht with the seats of the nominations in each Kreis, "
        "only the votes, so the 2019 results carry votes and no seats."
    ),
    (
        "2014 and earlier (1993, 1998, 2003, 2008, 2014): the page's long series prints only Land totals "
        "(registered, voters, and the votes of the larger parties); no per-Kreis file is linked."
    ),
    (
        "Wahlbezirk, Amt, and Gemeinde sheets: the data workbooks also hold the votes by Amt, Gemeinde, and "
        "Wahlkreis; only the Landkreis or kreisfreie Stadt, the electorate of each Kreistag, is stored."
    ),
]
NOTE = (
    "Final results as determined by the Wahlausschüsse and compiled by the Amt für Statistik Berlin-Brandenburg for "
    "the Landeswahlleiter. Each voter has up to three votes, which may be given to one candidate (cumulating) or "
    "spread across candidates of several nominations (panachage), so a nomination's votes are not ballots. "
    "Registered voters are those entitled to vote, voters are those who took part, and invalid are the ballot "
    "papers that were invalid."
)


def fetch() -> None:
    for url in (*VOTES.values(), REPORT_2024, LONG_SERIES):
        raw.fetch(SOURCE, url, suffix=".xlsx")


def sheet(url: str, name: str) -> list[list[str]]:
    path = raw.cached(SOURCE, url).path
    frame = fastexcel.read_excel(str(path)).load_sheet_by_name(name, header_row=None).to_polars()
    return [["" if cell is None else str(cell).strip() for cell in row] for row in frame.iter_rows()]


def whole(cell: str) -> int | None:
    if cell in ("", "x", "-", "–", "."):
        return None
    return int(float(cell))


def party_label(label: str) -> tuple[str, bool]:
    label = " ".join(label.split())
    return label, label not in PARTIES


@cache
def names() -> dict[str, str]:
    """The name production holds for each Kreis of Brandenburg, by its AGS."""
    import polars as pl

    found = pl.read_parquet(REFERENCE / "area.parquet").join(
        pl.read_parquet(REFERENCE / "area_code.parquet"), left_on="id", right_on="area_id"
    )
    found = found.filter((pl.col("scheme_code") == "de.kreis") & pl.col("valid_to").is_null())
    found = found.filter(pl.col("code").str.starts_with("12"))
    return dict(zip(found["code"].to_list(), found["name"].to_list(), strict=True))


def full_name(code: str) -> str:
    name = names()[code]
    if name.endswith(", Stadt"):
        return f"Stadt {name[: -len(', Stadt')]}"
    return name if name.endswith("kreis") else f"Landkreis {name}"


def ags_of(code: str) -> str | None:
    """The AGS of a Kreis row such as KI6000 (Landkreis) or KI5100 (kreisfreie Stadt)."""
    found = re.fullmatch(r"KI(\d{2})00", code)
    return f"120{found.group(1)}" if found else None


@cache
def seats_2024() -> dict[str, tuple[int, dict[str, int]]]:
    """The seats of each Kreis and the seats each nomination won, by AGS, from the report's tables 4.1 to 4.18."""
    order = sorted(names())
    found: dict[str, tuple[int, dict[str, int]]] = {}
    for index, ags in enumerate(order, start=1):
        rows = sheet(REPORT_2024, f"Tabelle4.{index}")
        won: dict[str, int] = {}
        total = None
        for row in rows:
            if len(row) < 3 or row[1] in ("", "Merkmal") or not row[1] or row[1].startswith(("-", "_", "1 ")):
                continue
            if row[1] == "Insgesamt":
                total = whole(row[2])
                break
            if row[2] != "" and re.fullmatch(r"[\d–-]+", row[2]):
                won[" ".join(row[1].split())] = whole(row[2]) or 0
        if total is None or sum(won.values()) != total:
            raise ValueError(f"2024 {ags}: nomination seats differ from the Kreistag's size")
        found[ags] = (total, won)
    return found


@cache
def kreise(year: int) -> dict[str, Kreis]:
    if year == 2024:
        rows = sheet(VOTES[year], "Brandenburg_KW_A")
        head = rows[0]
        start = head.index("Stimmen nach Wahlvorschlägen") + 1
        lists = [i for i in range(start, len(head)) if head[i] and not head[i].endswith(" in Prozent")]
        at = {"registered": 4, "voters": 11, "invalid": 15, "valid": 17}
        seats = seats_2024()
        data = [(ags_of(r[0]), r) for r in rows[1:]]
    else:
        rows = sheet(VOTES[year], "Ergebnis_2")
        head = rows[0]
        lists = [i for i in range(13, len(head), 2) if head[i]]
        at = {"registered": 6, "voters": 9, "invalid": 11, "valid": 12}
        seats = {}
        data = [(f"120{r[1][3:5]}" if re.fullmatch(r"120\d\d000", r[1] or "") else None, r) for r in rows[3:]]
    found: dict[str, Kreis] = {}
    for ags, row in data:
        if ags is None or ags not in names():
            continue
        merged: dict[str, int] = {}
        for i in lists:
            votes = whole(row[i])
            label, _ = party_label(head[i])
            if votes:
                if merged.get(label):
                    raise ValueError(f"{year} {ags}: {label} has votes in two columns")
                merged[label] = votes
            elif year == 2024 and label in seats[ags][1] and label not in merged:
                merged[label] = 0
        out = [
            Row(label, votes, None, seats[ags][1].get(label, 0) if year == 2024 else None, party_label(label)[1])
            for label, votes in merged.items()
        ]
        valid = whole(row[at["valid"]])
        if sum(r.votes or 0 for r in out) != valid:
            raise ValueError(f"{year} {ags}: nomination votes differ from the valid votes")
        total = None
        if year == 2024:
            total = seats[ags][0]
            if sum(r.seats or 0 for r in out) != total:
                raise ValueError(f"{year} {ags}: seats of the nominations differ from the Kreistag's size")
        found[ags] = Kreis(
            ags[2:],
            full_name(ags),
            whole(row[at["registered"]]),
            whole(row[at["voters"]]),
            whole(row[at["invalid"]]),
            total,
            out,
            None,
        )
    missing = set(names()) - set(found) - {"12000"}
    if missing:
        raise ValueError(f"{year}: no results for {sorted(missing)}")
    return found


def editions() -> list[Edition]:
    return [
        Edition(
            year=year,
            day=DAYS[year],
            page=PAGE,
            title=f"Kommunalwahlen im Land Brandenburg am {DAYS[year].day}.{DAYS[year].month}.{year}: Endgültige Ergebnisse der Kreistage",
            files=[VOTES[year], *([REPORT_2024] if year == 2024 else [])],
            kreise=lambda year=year: kreise(year),
            note=NOTE,
        )
        for year in sorted(DAYS)
    ]


LAND = Land(
    key="bb",
    polity=BB,
    name="Brandenburg",
    publisher="Amt für Statistik Berlin-Brandenburg",
    license="Creative Commons Namensnennung 3.0 Deutschland",
    license_url="https://creativecommons.org/licenses/by/3.0/de/",
    editions=editions,
    fetch=fetch,
)
