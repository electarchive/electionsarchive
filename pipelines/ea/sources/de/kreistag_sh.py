"""Kreistag elections of Schleswig-Holstein: the Kreiswahlen of the 11 Kreise, 2008 to 2023.

The Statistisches Amt für Hamburg und Schleswig-Holstein (Statistikamt Nord) prints the final results for the
Landeswahlleitung. The 2013 report holds the votes of every list in each Kreis for 2013 and, as the comparison, 2008;
the 2018 results come as a workbook with a row per Kreis; for 2023 the Amt publishes the seats each list won in each
Kreis and no votes. The reports give the seats of the large parties for each Kreis, and the seats of all voters'
groups together and of all other lists together. A seat total of such a group is stored on a list only where one
list of that group stood in the Kreis with votes.
"""

import re
import subprocess
from datetime import date
from functools import cache
from typing import cast

import polars as pl

from ea import raw
from ea.sources.de.kreistag import Edition, Kreis, Land, Row

SOURCE = "de.kreistag"
SCHLESWIG_HOLSTEIN = "01a13bb5-c55d-7311-a43d-bf02e77f66c9"
SITE = "https://www.statistik-nord.de"
PAGE = f"{SITE}/wahlen/wahlen-in-schleswig-holstein/kommunalwahlen"
BASE = f"{SITE}/fileadmin/Dokumente/Wahlen/Schleswig-Holstein"
F2023 = f"{BASE}/Kommunalwahlen/2023/Endg%C3%BCltige_Ergebnisse/e_Kommunalwahlen_Kreise_kf-St%C3%A4dte_Sitzverteilung_nach_Geschlecht_KMWSH-2023.xlsx"
F2018 = f"{BASE}/Kommunalwahlen/2018/SH-Kreise-Kommunalwahl-2018-endgueltige-Ergebnisse.xlsx"
R2018 = f"{BASE}/Kommunalwahlen/2018/KW_2018_Endgueltiger_Bericht_Korrektur3.pdf"
R2013 = f"{BASE}/Kommunalwahlen/2013/SH_KW_2013_Endg_Bericht_Internet.pdf"
R2008 = f"{BASE}/Statistische_Berichte/B_VII_3/B_VII_3_5_08_S_Korrektur.pdf"
LICENSE = "Reproduction and distribution permitted with the source acknowledged"
PUBLISHER = "Statistisches Amt für Hamburg und Schleswig-Holstein"
DAYS = {2008: date(2008, 5, 25), 2013: date(2013, 5, 26), 2018: date(2018, 5, 6), 2023: date(2023, 5, 14)}
GAPS = [
    "1998 and 2003: Statistikamt Nord prints only Land totals for them in its 2008 report; no per-Kreis result",
]
NAMES = {
    "01051": "Dithmarschen",
    "01053": "Herzogtum Lauenburg",
    "01054": "Nordfriesland",
    "01055": "Ostholstein",
    "01056": "Pinneberg",
    "01057": "Plön",
    "01058": "Rendsburg-Eckernförde",
    "01059": "Schleswig-Flensburg",
    "01060": "Segeberg",
    "01061": "Steinburg",
    "01062": "Stormarn",
}
PARTIES = {
    "CDU", "SPD", "GRÜNE", "FDP", "AfD", "DIE LINKE", "SSW", "PIRATEN", "FAMILIE", "FREIE WÄHLER", "Die PARTEI",
    "LKR", "DKP", "NPD", "dieBasis", "Volt", "Tierschutzpartei", "Die Humanisten", "DEMOKRATEN", "ZENTRUM", "BP",
    "ödp", "Schill", "STATT Partei", "NATURGESETZ", "VERBRAUCHERSCHUTZPARTEI", "WSDV", "REP", "PDS",
}  # fmt: skip
# The columns of each year's seat table, besides the voters' groups and the others.
MAJOR = {
    2008: ["CDU", "SPD", "FDP", "GRÜNE", "SSW", "DIE LINKE"],
    2013: ["CDU", "SPD", "GRÜNE", "FDP", "PIRATEN", "SSW", "DIE LINKE"],
    2018: ["CDU", "SPD", "GRÜNE", "FDP", "AfD", "DIE LINKE", "SSW"],
}
NOTE = (
    "Counted by the Gemeinde and Kreis election authorities and compiled by the Statistisches Amt für Hamburg und "
    "Schleswig-Holstein for the Landeswahlleitung. Each voter has as many votes as the Kreistag has seats and may "
    "give several to one candidate or spread them, so the votes of a list are not ballots; the source's valid and "
    "invalid figures count votes as well. Registered voters are those entitled to vote and voters those who took "
    "part. The source prints the seats of the large parties in each Kreis and the seats of all voters' groups "
    "together and of all other lists together; a total of that kind is stored on a list only where a single list "
    "of the group stood with votes, so the seats of other lists are not all stored."
)
NOTE_2023 = (
    "The source for 2023 prints the seats each party and voters' group won in each Kreis, and the share won by "
    "women. It prints no votes, no registered voters, and no turnout figures in absolute numbers, so the contests "
    "hold seat outcomes only."
)
AMBIGUOUS: list[str] = []
# The findings each edition's package accepts: its source gives seat totals of a group of lists, not of each list.
ACCEPT_REASON = (
    "The source prints the seats of all voters' groups and of all other lists together; the seats of lists in a "
    "group of several are not stored, so a Kreis's stored seats fall short."
)
ACCEPTED = {year: ("outcomes.seats_magnitude", ACCEPT_REASON) for year in (2008, 2013, 2018)}


def fetch() -> None:
    for url in (F2023, F2018):
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for url in (R2018, R2013, R2008):
        raw.fetch(SOURCE, url, suffix=".pdf")


def name_of(code: str) -> str:
    return f"Kreis {NAMES[code]}"


def clean(cell: str) -> str:
    """A header cell with its line-break hyphens joined: Wähler- gemein- schaft is Wählergemeinschaft."""
    joined = re.sub(r"(?<=\w)- (?=[a-zäöüß])", "", cell)
    joined = re.sub(r"(?<=\w)- (?=[A-ZÄÖÜ])", "-", joined).replace("/ ", "/")
    return re.sub(r"\s+-(?=\S)", " - ", joined)


def whole(token: str) -> int | None:
    token = token.replace("\xa0", "").replace(" ", "").replace("r", "")
    if token in ("–", "-", "X", "x", "×", ""):
        return None
    return int(token.replace(".", ""))


def individual(label: str) -> bool:
    return "," in label


def is_group(label: str) -> bool:
    return label not in PARTIES


def category(year: int, label: str) -> str:
    if label in MAJOR[year]:
        return label
    return "Wählergruppen" if is_group(label) and not individual(label) else "Andere"


def text(url: str) -> list[str]:
    done = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    )
    return done.stdout.splitlines()


def spread(year: int, rows: list[Row], shares: dict[str, int | None], where: str) -> list[Row]:
    """Store the seats of a seat table on the lists: columns of large parties per list, the others as a total."""
    out = {row.label: row for row in rows}
    for cat, seats in shares.items():
        if not seats:
            continue
        members = [row for row in rows if category(year, row.label) == cat and (row.votes or row.seats)]
        if cat in MAJOR[year]:
            if cat not in out:
                raise ValueError(f"{where}: {cat} won {seats} seats and has no votes")
            out[cat] = out[cat]._replace(seats=seats)
        elif len(members) == 1:
            out[members[0].label] = members[0]._replace(seats=seats)
        else:
            AMBIGUOUS.append(f"{year} {where}: {seats} {cat} seats among {[m.label for m in members]}")
    return list(out.values())


def seat_table(lines: list[str], columns: list[str]) -> dict[str, tuple[int, dict[str, int | None]]]:
    """The rows of 3.2 of a report: total seats, then one cell per column."""
    start = max(i for i, line in enumerate(lines) if re.match(r"^\s*3\.2 Sitzverteilung", line))
    found: dict[str, tuple[int, dict[str, int | None]]] = {}
    for line in lines[start:]:
        for code, name in NAMES.items():
            if line.startswith(name):
                tokens = [t for t in line[len(name) :].split() if t != "r"]
                tokens = [t.removeprefix("r") for t in tokens]
                if len(tokens) != len(columns) + 1:
                    raise ValueError(f"bad seat row {line!r}")
                cells = [whole(t) for t in tokens]
                found[code] = (cells[0] or 0, dict(zip(columns, cells[1:], strict=True)))
    if len(found) != len(NAMES):
        raise ValueError("a Kreis is missing from the seat table")
    return found


def share_columns(year: int) -> list[str]:
    return [*MAJOR[year], "Wählergruppen", "Andere"]


@cache
def report_2013() -> dict[str, tuple[Kreis, Kreis]]:
    """The 2013 report: for each Kreis the results of 2013 and of 2008 (before their seats)."""
    lines = text(R2013)
    start = next(i for i, line in enumerate(lines) if re.match(r"^\s*3\. Kreisergebnisse", line))
    end = max(i for i, line in enumerate(lines) if re.match(r"^\s*3\.2 Sitzverteilung", line))
    by_name = {name: code for code, name in NAMES.items()}
    heads = [(i, by_name[line.strip()]) for i, line in enumerate(lines[start:end], start) if line.strip() in by_name]
    keys = {
        "Wahlberechtigte": "registered",
        "Wählerinnen/Wähler": "voters",
        "Ungültige Stimmen": "invalid",
        "Gültige Stimmen": "valid",
    }
    out: dict[str, tuple[Kreis, Kreis]] = {}
    for (at, code), nxt in zip(heads, [*heads[1:], (end, "")], strict=True):
        figures: dict[str, tuple[int | None, int | None]] = {}
        lists: list[tuple[str, int | None, int | None]] = []
        listing = False
        for line in lines[at + 1 : nxt[0]]:
            parts = re.split(r"\s{2,}", line.strip())
            if parts[0].startswith("davon entfallen"):
                listing = True
                continue
            if len(parts) != 6 or parts[0].startswith("Anzahl"):
                continue
            cells = [whole(p) for p in (parts[1], parts[3])]
            if parts[0] in keys and not listing:
                figures[keys[parts[0]]] = (cells[0], cells[1])
            elif listing and parts[0] not in keys:
                lists.append((parts[0], cells[0], cells[1]))
        pair: list[Kreis] = []
        for column in (0, 1):
            rows = [
                Row(label, v[column + 1], None, None, is_group(label))
                for label, *v in ((x[0], None, x[1], x[2]) for x in lists)
                if v[column + 1] is not None
            ]
            got = {k: v[column] for k, v in figures.items()}
            pair.append(Kreis(code, name_of(code), cast(int, got["registered"]), cast(int, got["voters"]),
                              cast(int, got["invalid"]), 0, rows, got["valid"]))  # fmt: skip
        out[code] = (pair[0], pair[1])
    if len(out) != len(NAMES):
        raise ValueError("a Kreis is missing from the 2013 report")
    return out


def kreise_2013() -> dict[str, Kreis]:
    table = seat_table(text(R2013), share_columns(2013))
    out: dict[str, Kreis] = {}
    for code, (now, _) in report_2013().items():
        seats, shares = table[code]
        out[code] = now._replace(seats=seats, rows=spread(2013, now.rows, shares, code))
    return out


def seats_2008() -> dict[str, tuple[int, dict[str, int | None]]]:
    """The 2008 report, table 2.2: the total seats of each Kreis and its seats per column."""
    lines = text(R2008)
    start = next(i for i, line in enumerate(lines) if "Sitze insgesamt" in line)
    order = [["total"], ["FDP"], ["CDU", "SPD"], ["GRÜNE", "SSW"], ["DIE LINKE"], ["Wählergruppen", "Andere"]]
    tables: list[dict[str, list[int | None]]] = []
    current: dict[str, list[int | None]] = {}
    for line in lines[start:]:
        narrow = re.match(r"^\s*(5\d|6[0-2])\s+\S.*?\s{2,}([–\d][–\d\s]*)$", line)
        wide = re.match(r"^\s*([–\d][–\d\s]*?)\s{2,}(5\d|6[0-2])\s*$", line)
        if narrow:
            code, cells = narrow.group(1), narrow.group(2).split()
            if len(cells) == 9:
                current["010" + code] = [whole(c) for c in cells]
        elif wide:
            cells = wide.group(1).split()
            if len(cells) == 18:
                current["010" + wide.group(2)] = [whole(c) for c in cells]
        else:
            continue
        if "01062" in current:
            tables.append(current)
            current = {}
    if len(tables) != len(order):
        raise ValueError(f"expected {len(order)} seat tables in the 2008 report, found {len(tables)}")
    out: dict[str, tuple[int, dict[str, int | None]]] = {}
    for code in NAMES:
        total = 0
        shares: dict[str, int | None] = {}
        for names, table in zip(order, tables, strict=True):
            for index, label in enumerate(names):
                value = table[code][index * 9] or 0
                if label == "total":
                    total = value
                else:
                    shares[label] = value
        out[code] = (total, shares)
    return out


def kreise_2008() -> dict[str, Kreis]:
    table = seats_2008()
    out: dict[str, Kreis] = {}
    for code, (_, before) in report_2013().items():
        seats, shares = table[code]
        out[code] = before._replace(seats=seats, rows=spread(2008, before.rows, shares, code))
    return out


@cache
def workbook_2018() -> dict[str, Kreis]:
    rows = pl.read_excel(raw.cached(SOURCE, F2018).path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    header = [" ".join(str(c or "").split()) for c in rows[3]]
    lists = [(i, clean(header[i])) for i in range(11, 54)]
    out: dict[str, Kreis] = {}
    for row in rows[6:]:
        match = re.match(r"^(5\d|6[0-2]) ", str(row[0] or ""))
        if not match:
            continue
        code = "010" + match.group(1)
        found = []
        for at, label in lists:
            votes = int(row[at] or 0)
            if votes:
                found.append(Row(label, votes, None, None, is_group(label)))
        out[code] = Kreis(
            code, name_of(code), int(row[4] or 0), int(row[8] or 0), int(row[9] or 0), 0, found, int(row[10] or 0)
        )
    return out


def kreise_2018() -> dict[str, Kreis]:
    table = seat_table(text(R2018), share_columns(2018))
    out: dict[str, Kreis] = {}
    for code, kreis in workbook_2018().items():
        seats, shares = table[code]
        out[code] = kreis._replace(seats=seats, rows=spread(2018, kreis.rows, shares, code))
    return out


def kreise_2023() -> dict[str, Kreis]:
    rows = pl.read_excel(raw.cached(SOURCE, F2023).path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    found: dict[str, list[Row]] = {}
    for row in rows[5:]:
        code = str(row[0] or "")
        if code not in NAMES:
            continue
        label = str(row[2])
        group = is_group(label)
        found.setdefault(code, []).append(Row(label, None, None, int(str(row[4])), group))
    out: dict[str, Kreis] = {}
    none = None  # the source prints no registered voters, voters, or invalid votes for 2023
    for code, listed in found.items():
        out[code] = Kreis(code, name_of(code), none, none, none, sum(r.seats or 0 for r in listed), listed)
    return out


def editions() -> list[Edition]:
    return [
        Edition(2008, DAYS[2008], PAGE, "Kommunalwahlen in Schleswig-Holstein am 25. Mai 2008: Endgültiges Ergebnis",
                [R2008, R2013], kreise_2008, None, NOTE),
        Edition(2013, DAYS[2013], PAGE, "Kommunalwahlen in Schleswig-Holstein am 26. Mai 2013: Endgültiges Ergebnis",
                [R2013], kreise_2013, None, NOTE),
        Edition(2018, DAYS[2018], PAGE, "Kommunalwahlen in Schleswig-Holstein am 6. Mai 2018: Endgültiges Ergebnis",
                [F2018, R2018], kreise_2018, None, NOTE),
        Edition(2023, DAYS[2023], PAGE, "Kommunalwahlen in Schleswig-Holstein am 14. Mai 2023: Endgültiges Ergebnis",
                [F2023], kreise_2023, None, NOTE_2023),
    ]  # fmt: skip


LAND = Land(
    key="sh",
    polity=SCHLESWIG_HOLSTEIN,
    name="Schleswig-Holstein",
    publisher=PUBLISHER,
    license=LICENSE,
    license_url=None,
    editions=editions,
    fetch=fetch,
)
