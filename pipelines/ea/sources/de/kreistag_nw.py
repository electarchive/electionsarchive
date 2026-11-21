"""Kreistag elections of North Rhine-Westphalia, 2009 to 2025.

The Landeswahlleiterin publishes the final results of each Kreistag election on wahlergebnisse.nrw: for 2020 and 2025
text files with the votes and seats of every list in each Kreis, and for 2014 one page per Kreis that also prints
the 2009 results as the comparison.
"""

import re
from datetime import date
from functools import cache

from ea import raw
from ea.sources.de.gemeinderat_nrw import KREISFREI_2014, _Table, whole
from ea.sources.de.kreistag import SOURCE, Edition, Kreis, Land, Row, areas

BASE = "https://www.wahlergebnisse.nrw/kommunalwahlen"
NRW = "01a13bb5-c563-741b-83f7-4b74611ca2d9"
DAYS = {2009: date(2009, 8, 30), 2014: date(2014, 5, 25), 2020: date(2020, 9, 13), 2025: date(2025, 9, 14)}
FOLDERS = {2020: "KW20", 2025: "KW25"}
GAPS = [
    "Kreistag elections before 2009: the election index pages for 2009, 2004 and 1999 return 404, and the 2014 pages print only the 2009 comparison"
]
NOTE = (
    "Counted by the municipal election authorities and compiled by the Landeswahlleiterin of North Rhine-Westphalia. "
    "Each voter has one vote for a list of the Kreistag election. Registered voters are those entitled to vote, "
    "voters are those who took part, invalid and valid are the votes cast. Lists of voter groups "
    "(Wählergruppen) and independent candidates are numbered by the authority within each Kreis."
)
ALIAS = {
    "T I E R S C H U T Z": "TIERSCHUTZ hier!",
    "TIERSCHUTZ hier!": "TIERSCHUTZ hier!",
}


def text_url(year: int, kind: str) -> str:
    return f"{BASE}/{year}/{FOLDERS[year]}_parla_insgesamt_{kind}.txt"


def index_url(year: int) -> str:
    return f"{BASE}/{year}/index_obb_lr.shtml"


def page_url(code: str) -> str:
    return f"{BASE}/2014/aktuell/a{code}kw1400.html"


def fetch() -> None:
    for year in FOLDERS:
        for kind in ("stimmen", "sitze"):
            raw.fetch(SOURCE, text_url(year, kind), suffix=".txt")
    raw.fetch(SOURCE, index_url(2014), suffix=".html")
    for code in codes_2014(fetched=False):
        raw.fetch(SOURCE, page_url(code), suffix=".html")


def codes_2014(fetched: bool = True) -> list[str]:
    path = f"{BASE}/2014/index_obb_lr.shtml"
    text = (raw.cached(SOURCE, path) if fetched else raw.fetch(SOURCE, path, suffix=".html")).path.read_text(
        errors="replace"
    )
    found = set(re.findall(r"aktuell/a(\d{6})kw1400", text))
    return sorted(code for code in found if code not in KREISFREI_2014)


def label_of(label: str, group: bool = False) -> tuple[str, bool]:
    label = " ".join(label.split())
    if re.fullmatch(r"(WGR|Wählergruppe)\s*\d+", label):
        return "Wählergruppe " + re.sub(r"\D", "", label), True
    if label.startswith("Einzelbewerber"):
        return "Einzelbewerber/-innen", True
    return ALIAS.get(label, label), group


def ags_of(code: str) -> str:
    return "05" + code[:3]


def clean(name: str) -> str:
    return re.sub(r"^Krfr\. Stadt ", "", name).strip()


def kreise_text(year: int) -> dict[str, Kreis]:
    votes = rows_of_raw(text_url(year, "stimmen"))
    seats = rows_of_raw(text_url(year, "sitze"))
    start = next(i for i, row in enumerate(votes) if row and row[0].startswith("Verwaltungsbezirks-Nr"))
    head = votes[start]
    first = head.index("Gültige Stimmen") + 1
    names = {
        "registered": "Wahlberechtigte insgesamt",
        "voters": next(c for c in head if c.startswith("Wähler/-innen")),
        "invalid": "Ungültige Stimmen",
        "valid": "Gültige Stimmen",
    }
    labels = [label.strip() for label in head[first:]]
    s_start = next(i for i, row in enumerate(seats) if row and row[0].startswith("Verwaltungsbezirks-Nr"))
    s_head = seats[s_start]
    s_names = [s_head[2 + 3 * i].strip() for i in range((len(s_head) - 2) // 3)]
    by_code = {row[0]: row for row in seats[s_start + 2 :] if row and row[0].isdigit()}
    out: dict[str, Kreis] = {}
    for row in votes[start + 1 :]:
        if not row or not row[0].isdigit() or row[0] == "000000" or "Krfr." in row[1]:
            continue
        mine = by_code[row[0]]
        rows: list[Row] = []
        for index, label in enumerate(labels):
            if not label or label == "Wählergruppen zusammen":
                continue
            number = whole(row[first + index] if first + index < len(row) else None) or 0
            won = whole(mine[2 + 3 * s_names.index(label)]) or 0 if label in s_names else 0
            if number or won:
                name, group = label_of(label)
                rows.append(Row(name, number, None, won, group))
        out[ags_of(row[0])] = Kreis(
            ags_of(row[0]),
            clean(row[1]),
            whole(row[head.index(names["registered"])]) or 0,
            whole(row[head.index(names["voters"])]) or 0,
            whole(row[head.index(names["invalid"])]) or 0,
            whole(mine[2]) or 0,
            rows,
            whole(row[head.index(names["valid"])]),
        )
    return out


def rows_of_raw(url: str) -> list[list[str]]:
    import csv

    path = raw.cached(SOURCE, url).path
    return list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))


def pages_2014() -> dict[str, tuple[Kreis, Kreis]]:
    out: dict[str, tuple[Kreis, Kreis]] = {}
    for code in codes_2014():
        url = page_url(code)
        parser = _Table()
        parser.feed(raw.cached(SOURCE, url).path.read_text(errors="replace"))
        flat = " ".join(" ".join(parser.text).split())
        named = re.search(r"Ergebnis für:\s*(\d{6})\s+(.+?)\s+zur Übersicht", flat)
        if named is None or named.group(1) != code:
            raise ValueError(f"{url} names another Kreis")
        head = next(i for i, row in enumerate(parser.rows) if row and row[0] == "Merkmal")
        keys = {
            "Wahlberechtigte insgesamt": "registered",
            "Wähler/-innen": "voters",
            "Ungültige Stimmen": "invalid",
            "Gültige Stimmen": "valid",
        }
        figures: dict[str, tuple[int | None, int | None]] = {}
        seats = (0, 0)
        lists: tuple[list[Row], list[Row]] = ([], [])
        listing = False
        for row in parser.rows[head + 2 :]:
            label, cells = row[0], row[1:]
            if label in keys and len(cells) >= 11:
                figures[keys[label]] = (whole(cells[0]), whole(cells[2]))
            elif label == "Vertreter/-innen":
                seats = (whole(cells[1]) or 0, whole(cells[4]) or 0)
                listing = True
            elif listing and len(cells) == 11:
                if label == "Wählergruppen zusammen":
                    continue
                name, group = label_of(label)
                for index, (v, s) in enumerate(((0, 5), (2, 8))):
                    votes, won = whole(cells[v]) or 0, whole(cells[s]) or 0
                    if votes or won:
                        lists[index].append(Row(name, votes, None, won, group))
            elif listing and label.startswith("Download"):
                break
        name = clean(named.group(2))
        pair = []
        for index in (0, 1):
            pair.append(
                Kreis(
                    ags_of(code),
                    name,
                    figures["registered"][index] or 0,
                    figures["voters"][index] or 0,
                    figures["invalid"][index] or 0,
                    seats[index],
                    lists[index],
                    figures["valid"][index],
                )
            )
        out[ags_of(code)] = (pair[0], pair[1])
    return out


def usable(pool: dict[str, Kreis], year: int) -> dict[str, Kreis]:
    known = areas()
    for ags in sorted(set(pool) - set(known)):
        GAPS.append(f"{year} Kreis {ags} {pool[ags].name}: no area in the archive")
    return {ags: kreis for ags, kreis in pool.items() if ags in known}


def check(year: int, pool: dict[str, Kreis]) -> None:
    for ags, kreis in pool.items():
        if sum(r.seats or 0 for r in kreis.rows) != kreis.seats:
            raise ValueError(f"{year} {ags}: seats of the lists differ from the Kreistag's size")
        if kreis.valid is not None and sum(r.votes or 0 for r in kreis.rows) != kreis.valid:
            raise ValueError(f"{year} {ags}: list votes differ from the valid votes")


@cache
def kreise(year: int) -> dict[str, Kreis]:
    if year in FOLDERS:
        pool = kreise_text(year)
    else:
        pool = {ags: pair[0 if year == 2014 else 1] for ags, pair in pages_2014().items()}
    check(year, pool)
    return usable(pool, year)


def editions() -> list[Edition]:
    files = {year: [text_url(year, "stimmen"), text_url(year, "sitze")] for year in FOLDERS} | {
        year: [index_url(2014), *(page_url(code) for code in codes_2014())] for year in (2014, 2009)
    }
    return [
        Edition(
            year,
            DAYS[year],
            index_url(2014 if year == 2009 else year),
            f"Kommunalwahlen {year} in NRW: Endgültige Ergebnisse der Kreistagswahlen",
            files[year],
            lambda year=year: kreise(year),
            None,
            NOTE,
        )
        for year in sorted(DAYS)
    ]


LAND = Land(
    key="nw",
    polity=NRW,
    name="North Rhine-Westphalia",
    publisher="Landeswahlleiterin Nordrhein-Westfalen (IT.NRW)",
    license="Datenlizenz Deutschland, Zero, Version 2.0",
    license_url="https://www.govdata.de/dl-de/zero-2-0",
    editions=editions,
    fetch=fetch,
)
