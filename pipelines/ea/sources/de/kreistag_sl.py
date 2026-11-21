"""Kreistag elections of Saarland: the Kreistagswahlen of 2014 and 2019 in the Regionalverband Saarbrücken and the five Landkreise.

The Statistisches Amt Saarland prints the final results of the Europawahl and Kommunalwahlen of 26 May 2019 in its
Einzelschriften (no. 134), with the results of 2014 as the comparison. Tables 15 and 16 give, for every Kreis and the
Land, the registered voters, the valid votes, the votes of each list, and the seats of each list. Each voter has one
vote in the Kreistag election.
"""

import re
import subprocess
from datetime import date
from functools import cache

from ea import raw
from ea.sources.de.kreistag import SOURCE, Edition, Kreis, Land, Row, areas

SL = "01a13bb5-c563-741b-83f7-4b7e1c296ee6"
PAGE = "https://www.statistischebibliothek.de/mir/receive/SLSerie_mods_00000071"
REPORT = (
    "https://www.statistischebibliothek.de/mir/servlets/MCRFileNodeServlet/SLAusgabe_derivate_00000732/"
    "SL%20Einzelschriften%20134%20(Europawahl%20und%20Kommunalwahl%202019,%20endg.%20Ergebnisse).pdf"
)
DAYS = {2014: date(2014, 5, 25), 2019: date(2019, 5, 26)}
KREISE = {
    "Regionalverband Saarbrücken": "10041",
    "Landkreis Merzig-Wadern": "10042",
    "Landkreis Neunkirchen": "10043",
    "Landkreis Saarlouis": "10044",
    "Saarpfalz-Kreis": "10045",
    "Landkreis St. Wendel": "10046",
}
LISTS = [
    "CDU", "SPD", "DIE LINKE", "AfD", "GRÜNE", "FDP", "FAMILIE", "PIRATEN", "NPD", "FREIE WÄHLER",
    "FWG Saarpfalz-Kreis", "ÖDP", "SfA Saarbrücken", "Sonstige",
]  # fmt: skip
GROUPS = {"FWG Saarpfalz-Kreis", "SfA Saarbrücken", "Sonstige"}
LEFT = 4
GAPS = [
    (
        "2009 and earlier: the Statistisches Amt Saarland prints the earlier Kreistag results in separate "
        "Einzelschriften (no. 113 for 2004, 94 for 1994, 74 for 1989) whose per-Kreis tables are not yet read."
    ),
    "Gemeinde tables: the 2019 report also prints the Kreistag votes by Gemeinde; only the Kreis is stored.",
]
NOTE = (
    "Final results compiled by the Statistisches Amt Saarland. Each voter has one vote for a list of the "
    "Kreistag election, so the valid votes are the valid ballots. Registered voters are those entitled to vote. The "
    "source prints no count of voters or invalid ballots for the Kreistag elections, and none is stored. "
    "Sonstige groups all other lists."
)


def text() -> list[str]:
    path = raw.cached(SOURCE, REPORT).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


def cells(line: str) -> list[str]:
    return re.split(r"\s{2,}", line.strip())


def count(cell: str) -> int | None:
    return None if cell == "-" else int(cell.replace(" ", ""))


@cache
def tables() -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    """The left half of table 15, its right half, and table 16, each as the rows that carry figures."""
    lines = text()
    start = next(i for i, ln in enumerate(lines) if "15 Kreistagswahlen 2019 mit Vergleichs" in ln)
    seats = next(i for i, ln in enumerate(lines) if "16 Sitzverteilung bei den Kreistagswahlen 2019" in ln)
    right = next(i for i, ln in enumerate(lines) if i > seats and "zahlen 2014 nach Kreisen" in ln)
    end = next(i for i, ln in enumerate(lines) if i > right and "17 Veränderung der Mehrheiten" in ln)
    left_rows = [cells(ln) for ln in lines[start + 1 : seats] if re.search(r"\s20(14|19)\s", ln)]
    right_rows = [cells(ln) for ln in lines[right:end] if re.fullmatch(r"[\d\s-]+", ln.strip()) and ln.strip()]
    seat_rows = [cells(ln) for ln in lines[seats + 1 : right] if re.search(r"\s20(14|19)\s", ln)]
    return left_rows, right_rows, seat_rows[:14]


@cache
def kreise(year: int) -> dict[str, Kreis]:
    left, right, seat_rows = tables()
    if not (len(left) == len(right) == len(seat_rows) == 14):
        raise ValueError(f"unexpected table sizes {len(left)} {len(right)} {len(seat_rows)}")
    known = areas()
    found: dict[str, Kreis] = {}
    order = ["Saarland", *KREISE]
    for index, (lrow, rrow, srow) in enumerate(zip(left, right, seat_rows, strict=True)):
        name = order[index // 2]
        if str(year) not in lrow:
            continue
        year_at = lrow.index(str(year))
        figures = [count(c) for c in lrow[year_at + 1 :]]
        votes = figures[2:] + [count(c) for c in rrow]
        won = [count(c) for c in srow[srow.index(str(year)) + 1 :]]
        if len(figures) != 2 + LEFT or len(votes) != len(LISTS) or len(won) != len(LISTS):
            raise ValueError(f"{year} {name}: unexpected figure counts")
        registered, valid = figures[0], figures[1]
        rows = [
            Row(label, v, None, s or 0, label in GROUPS)
            for label, v, s in zip(LISTS, votes, won, strict=True)
            if v is not None or s
        ]
        if sum(r.votes or 0 for r in rows) != valid:
            raise ValueError(f"{year} {name}: list votes differ from the valid votes")
        if name == "Saarland":
            found[""] = Kreis("", name, registered, None, None, sum(r.seats or 0 for r in rows), rows, valid)
            continue
        ags = KREISE[name]
        if ags not in known:
            GAPS.append(f"{year} Kreis {ags} {name}: no area in the archive")
            continue
        found[ags] = Kreis(ags[2:], name, registered, None, None, sum(r.seats or 0 for r in rows), rows, valid)
    land = found.pop("")
    for field, total in (("registered", land.registered), ("valid", land.valid)):
        got = sum(getattr(k, field) or 0 for k in found.values())
        if got != total:
            raise ValueError(f"{year}: Kreise {field} {got} differ from the Land total {total}")
    return found


def editions() -> list[Edition]:
    return [
        Edition(
            year=year,
            day=DAYS[year],
            page=PAGE,
            title=f"Europawahl und Kommunalwahlen am 26. Mai 2019, endgültige Ergebnisse: Kreistagswahlen {year}",
            files=[REPORT],
            kreise=lambda year=year: kreise(year),
            note=NOTE,
        )
        for year in sorted(DAYS)
    ]


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


LAND = Land(
    key="sl",
    polity=SL,
    name="Saarland",
    publisher="Statistisches Amt Saarland",
    license="Vervielfältigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet",
    license_url=None,
    editions=editions,
    fetch=fetch,
)
