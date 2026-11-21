"""Kreistag elections of Saxony: the Kreistagswahlen of 2019 and 2024 in the ten Landkreise.

The Statistisches Landesamt of the Free State of Saxony publishes the final results of each Kreistag election as a
workbook with a row per Landkreis (and finer sheets by Wahlkreis and Gemeinde): registered voters, voters, invalid and
valid ballots, valid votes, and the votes of every nomination, with the voter groups (Wählervereinigungen) added
together. For 2024 a second workbook gives the seats each nomination won in each Landkreis; for 2019 the Landesamt
publishes only the Land totals of the seats, so the 2019 results carry votes and no seats.
"""

from datetime import date
from functools import cache

import fastexcel

from ea import raw
from ea.sources.de.kreistag import SOURCE, Edition, Kreis, Land, Row, areas

SN = "01a13bb5-c563-741b-83f7-4b86faa1ed93"
SITE = "https://wahlen.sachsen.de"
DOWNLOADS = f"{SITE}/download/Kreistag"
DAYS = {2019: date(2019, 5, 26), 2024: date(2024, 6, 9)}
PAGES = {
    2019: f"{SITE}/kreistagswahlen-2019-informationen-und-downloads.html",
    2024: f"{SITE}/kreistagswahlen-2024-informationen-und-downloads.html",
}
VOTES = {
    2019: f"{DOWNLOADS}/KT19_EE_SN_WK_LK_GE_TG.xlsx",
    2024: f"{DOWNLOADS}/statistik-sachsen_KT24_EE_SN_LK_WK_GE_TG.xlsx",
}
SEATS_2024 = f"{DOWNLOADS}/statistik-sachsen_KT24_EE_Sitze.xlsx"
TOTALS_2019 = f"{DOWNLOADS}/2019_KT_Sachsen_endgueltig.xlsx"
ACCEPT_REASON = (
    "Each voter has three votes, so the votes of the nominations exceed the valid ballots the Landesamt prints "
    "beside them; both figures are the Landesamt's own."
)
ACCEPTED = {year: ("accounting.votes_valid", ACCEPT_REASON) for year in (2019, 2024)}
WV = "Wählervereinigungen"
LABELS = {"Die Partei": "Die PARTEI", "III. Weg": "III. Weg", "WV insgesamt": WV, "Wählervereinigungen insgesamt": WV}
GAPS = [
    (
        "2019: the Landesamt publishes the seats of the nominations only as Land totals (902 seats); "
        "no per-Landkreis seat table is among its downloads, so the 2019 results carry votes and no seats."
    ),
    (
        "2014: the Landesamt's 2014 pages publish the results only in an interactive viewer; "
        "no download file is linked, so the 2014 election has no rows here."
    ),
    "2008 and earlier: the Landesamt's Kreistagswahlen pages cover 2014 to 2024 only.",
    (
        "Wahlkreis and Gemeinde sheets: the workbooks also hold the votes by Wahlkreis (117 in 2024, 123 in 2019) "
        "and by Gemeinde; only the Landkreis, the electorate of each Kreistag, is stored."
    ),
]
NOTE = (
    "Final results as determined by the Kreiswahlausschüsse and compiled by the Statistisches Landesamt des "
    "Freistaates Sachsen. Each voter has three votes, which may be given to one candidate (cumulating) or spread across "
    "candidates of several nominations (panachage), so a nomination's votes are not ballots. Registered voters are "
    "those entitled to vote, voters are those who took part, and invalid and valid are the ballot papers. "
    "The source adds the votes of all voter groups (Wählervereinigungen) together, so they are stored as one row."
)


def fetch() -> None:
    for url in (*VOTES.values(), SEATS_2024, TOTALS_2019):
        raw.fetch(SOURCE, url, suffix=".xlsx")


def sheet(url: str, name: str) -> list[list[str]]:
    path = raw.cached(SOURCE, url).path
    frame = fastexcel.read_excel(str(path)).load_sheet_by_name(name, header_row=None).to_polars()
    return [["" if cell is None else str(cell).strip() for cell in row] for row in frame.iter_rows()]


def whole(cell: str) -> int | None:
    if cell in ("", "x", "-", "."):
        return None
    return int(float(cell))


def flat(cell: str) -> str:
    return " ".join(cell.replace("SZ_", "").split()).replace("- ", "")


@cache
def seats_2024() -> dict[str, tuple[int, dict[str, int]]]:
    """The seats of each Landkreis and the seats each nomination won, by Landkreis code."""
    rows = sheet(SEATS_2024, "KT24_Sitze")
    names = [LABELS.get(flat(cell), flat(cell)) for cell in rows[1]]
    found: dict[str, tuple[int, dict[str, int]]] = {}
    for row in rows[3:]:
        if row[3] != "LK":
            continue
        won = {names[i]: whole(row[i]) or 0 for i in range(8, 20) if names[i]}
        total = whole(row[7])
        if total is None:
            raise ValueError(f"2024 {row[4]}: no seats")
        if sum(won.values()) != total:
            raise ValueError(f"2024 {row[4]}: nomination seats differ from the Kreistag's size")
        found[row[4]] = (total, won)
    return found


@cache
def names() -> dict[str, str]:
    """The name production holds for each Landkreis, by its AGS."""
    import polars as pl

    from ea.sources.de.kreistag import REFERENCE

    found = pl.read_parquet(REFERENCE / "area.parquet").join(
        pl.read_parquet(REFERENCE / "area_code.parquet"), left_on="id", right_on="area_id"
    )
    found = found.filter((pl.col("scheme_code") == "de.kreis") & pl.col("valid_to").is_null())
    found = found.filter(pl.col("code").str.starts_with("14"))
    return dict(zip(found["code"].to_list(), found["name"].to_list(), strict=True))


def full_name(code: str) -> str:
    name = names()[code]
    return name if name.endswith("kreis") else f"Landkreis {name}"


@cache
def kreise(year: int) -> dict[str, Kreis]:
    rows = sheet(VOTES[year], "SN_LK")
    head = rows[0]
    end = head.index("")
    lists = list(range(14, end))
    known = areas()
    seats = seats_2024() if year == 2024 else {}
    found: dict[str, Kreis] = {}
    for row in rows[1:]:
        if row[1] != "LK":
            continue
        code = row[2]
        if code not in known:
            GAPS.append(f"{year} Landkreis {code} {row[3]}: no area in the archive")
            continue
        out: list[Row] = []
        for i in lists:
            votes = whole(row[i])
            if votes is None:
                continue
            label = LABELS.get(head[i], head[i])
            won = seats[code][1].get(label, 0) if year == 2024 else None
            out.append(Row(label, votes, None, won, label == WV))
        valid_votes = whole(row[13])
        if sum(r.votes or 0 for r in out) != valid_votes:
            raise ValueError(f"{year} {code}: nomination votes differ from the valid votes")
        total = None
        if year == 2024:
            total = seats[code][0]
            if sum(r.seats or 0 for r in out) != total:
                raise ValueError(f"{year} {code}: seats of the nominations differ from the Kreistag's size")
        found[code] = Kreis(
            code[2:], full_name(code), whole(row[7]), whole(row[8]), whole(row[11]), total, out, whole(row[12])
        )
    return found


def editions() -> list[Edition]:
    return [
        Edition(
            year=year,
            day=DAYS[year],
            page=PAGES[year],
            title=f"Kreistagswahlen in Sachsen am {DAYS[year].day}.{DAYS[year].month}.{year}: Endgültige Ergebnisse",
            files=[VOTES[year], *([SEATS_2024] if year == 2024 else [])],
            kreise=lambda year=year: kreise(year),
            note=NOTE,
        )
        for year in sorted(DAYS)
    ]


LAND = Land(
    key="sn",
    polity=SN,
    name="Saxony",
    publisher="Statistisches Landesamt des Freistaates Sachsen",
    license="Vervielfältigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet",
    license_url=None,
    editions=editions,
    fetch=fetch,
)
