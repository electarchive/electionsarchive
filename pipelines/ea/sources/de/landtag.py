import csv
import html
import io
import itertools
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.de.btw import CONSTITUENCIES
from ea.store import DATA, connect

SOURCE = "de.landtag"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BW = "https://wahlen.statistik-bw.de/ltw26"
MV = "https://www.laiv-mv.de/static/LAIV/Wahlen/2-Landtagswahlen/2026/Ergebnisse"
BE = "https://wahlen-berlin.de/wahlen/BE2026/Afspraes/AGH"
ST = "https://wahlergebnisse.sachsen-anhalt.de/wahlen/lt26/downloads"
RP = "https://www.wahlen.rlp.de/fileadmin/wahlen.rlp.de/dokumente-wahlen/ltw"
RP_PDF = f"{RP}/PDF/Bekanntmachung_Endergebnis_2026/LW2026_Bekanntmachung_Endergebnis"
URLS = {
    "BW": [f"{BW}/ltw26-ergebnisse.csv", f"{BW}/ltw26-gewaehlte.csv", f"{BW}/ltw26-sitzverteilung.pdf"],
    "MV": [f"{MV}/l_wahlkreise.csv", f"{MV}/l_mandate.csv"],
    "ST": [f"{ST}/Ergebnisse_Land_RKR_WKR_LT_2026.csv", f"{ST}/Sitzverteilung_LT_2026.csv"],
    "RP": [
        f"{RP}/Ergebnisdateien/2026/Endgueltiges_Ergebnis_LW_2026_Wahlkreise.xlsx",
        f"{RP_PDF}_UEbersicht_2.pdf",
        f"{RP_PDF}_UEbersicht_3.pdf",
        f"{RP_PDF}_UEbersicht_4.pdf",
        f"{RP_PDF}.pdf",
    ],
    "BE": [f"{BE}/DL/DL_BE_AGHBVV2026.xlsx", "https://www.wahlen-berlin.de/wahlen/BE2026/Afspraes/agh/gewaehlte.html"],
}
NOTE_BE = (
    "Voters have two votes. The second vote decides how many of the Abgeordnetenhaus's seats each party gets: at "
    "least 130 seats are shared by the Hare–Niemeyer largest remainder method among parties with 5 percent or more "
    "of the second votes. Each party's winners in the 78 constituencies take their seats first, and any remaining "
    "seats go to candidates on the party's Land list. Extra seats are added to balance out the overhang seats. The "
    "total came to 153."
)
BE_PARTIES = {"MIETERPARTEI", "MERA25", "DL", "DIE FRAUEN"}
NOTE_BW = (
    "Voters have two votes for the first time in 2026. The second vote decides how many of the Landtag's seats each "
    "party gets: at least 120 seats are shared by the Sainte-Laguë method among parties with 5 percent or more of the "
    "second votes. Each party's constituency winners take their seats first, and a party that wins more "
    "constituencies than its share keeps them. Extra seats go to the other parties until every party's seats match "
    "its share of the second votes."
)
NOTE_MV = (
    "Voters have two votes. The second vote decides how many of the Landtag's 71 seats each party gets: the seats are "
    "shared by the Hare–Niemeyer largest remainder method among parties with 5 percent or more of the second votes. "
    "Each party's winners in the 36 constituencies take their seats first, and any remaining seats go to candidates "
    "on the party's Land list. Extra seats are added to balance out the overhang seats."
)
NOTE_ST = (
    "Voters have two votes. The second vote decides how many of the Landtag's seats each party gets: at least 83 "
    "seats are shared by the Hare–Niemeyer largest remainder method among parties with 5 percent or more of the "
    "second votes. Each party's winners in the 41 constituencies take their seats first, and any remaining seats go "
    "to candidates on the party's Land list. If a party wins more constituencies than its share, it keeps them and "
    "the total number of seats grows by twice that surplus before the seats are divided again."
)
NOTE_RP = (
    "Voters have two votes. The second vote decides how many of the Landtag's 101 seats each party gets: the seats "
    "are shared by the Sainte-Laguë method among parties with 5 percent or more of the second votes. Each party's "
    "winners in the 52 constituencies take their seats first, and any remaining seats go to candidates on the "
    "party's Land list. A party that wins more constituencies than its share keeps them, and extra seats are added "
    "until every party's seats match its share of the second votes. The total came to 105 seats."
)
RP_LABELS = {"Bastian Ruhl": "Einzelbewerber Bastian Ruhl", "Yunus Emre": "Miteinander statt Gegeneinander"}
WINNERS_NOTE = (
    "The source names only the winners of each constituency. Other candidates are counted only in their party's "
    "vote totals."
)
WINNERS_NOTE_MV = "The source gives the party that won each constituency but not the winner's name."
STATES = {
    "BE": {
        "land": "11",
        "name": "Berlin",
        "native": "Wahl zum 20. Abgeordnetenhaus von Berlin 2026",
        "body": ("Abgeordnetenhaus of Berlin", "Abgeordnetenhaus von Berlin"),
        "day": date(2026, 9, 20),
        "rules": {"formula": "hare", "threshold": {"percent": 5, "level": "party", "scope": "contest"}},
        "note": NOTE_BE,
        "winners": WINNERS_NOTE,
        "publisher": "Amt für Statistik Berlin-Brandenburg",
        "title": "Wahl zum 20. Abgeordnetenhaus von Berlin am 20. September 2026, endgültiges Ergebnis",
        "url": f"{BE}/downloads.html",
    },
    "BW": {
        "land": "08",
        "name": "Baden-Württemberg",
        "native": "Landtagswahl in Baden-Württemberg 2026",
        "body": ("Landtag of Baden-Württemberg", "Landtag von Baden-Württemberg"),
        "day": date(2026, 3, 8),
        "rules": {"formula": "sainte_lague", "threshold": {"percent": 5, "level": "party", "scope": "contest"}},
        "note": NOTE_BW,
        "winners": WINNERS_NOTE,
        "publisher": "Statistisches Landesamt Baden-Württemberg",
        "title": "Landtagswahl 2026 in Baden-Württemberg, endgültiges Ergebnis",
        "url": f"{BW}/downloads.html",
    },
    "MV": {
        "land": "13",
        "name": "Mecklenburg-Vorpommern",
        "native": "Landtagswahl in Mecklenburg-Vorpommern 2026",
        "body": ("Landtag of Mecklenburg-Vorpommern", "Landtag Mecklenburg-Vorpommern"),
        "day": date(2026, 9, 20),
        "rules": {"formula": "hare", "threshold": {"percent": 5, "level": "party", "scope": "contest"}},
        "note": NOTE_MV,
        "winners": WINNERS_NOTE_MV,
        "publisher": "Statistisches Amt Mecklenburg-Vorpommern",
        "title": "Landtagswahl am 20. September 2026 in Mecklenburg-Vorpommern, endgültiges Ergebnis",
        "url": "https://www.laiv-mv.de/Wahlen/Landtagswahlen/2026/Ergebnisse/",
    },
    "RP": {
        "land": "07",
        "name": "Rhineland-Palatinate",
        "native": "Landtagswahl in Rheinland-Pfalz 2026",
        "body": ("Landtag of Rhineland-Palatinate", "Landtag Rheinland-Pfalz"),
        "day": date(2026, 3, 22),
        "rules": {"formula": "sainte_lague", "threshold": {"percent": 5, "level": "party", "scope": "contest"}},
        "note": NOTE_RP,
        "winners": WINNERS_NOTE,
        "publisher": "Landeswahlleiter Rheinland-Pfalz",
        "title": "Wahl zum 19. Landtag Rheinland-Pfalz am 22. März 2026, endgültiges Ergebnis",
        "url": "https://www.wahlen.rlp.de/landtagswahl/bekanntmachungen/bekanntmachung-endergebnis-lw-2026",
    },
    "ST": {
        "land": "15",
        "name": "Saxony-Anhalt",
        "native": "Landtagswahl in Sachsen-Anhalt 2026",
        "body": ("Landtag of Saxony-Anhalt", "Landtag von Sachsen-Anhalt"),
        "day": date(2026, 9, 6),
        "rules": {"formula": "hare", "threshold": {"percent": 5, "level": "party", "scope": "contest"}},
        "note": NOTE_ST,
        "winners": WINNERS_NOTE,
        "publisher": "Statistisches Landesamt Sachsen-Anhalt",
        "title": "Landtagswahl 2026 in Sachsen-Anhalt, endgültiges Ergebnis",
        "url": "https://wahlergebnisse.sachsen-anhalt.de/wahlen/lt26/downloads.html",
    },
}
BW_FIRST = {
    "D1": "GRÜNE",
    "D2": "CDU",
    "D3": "SPD",
    "D4": "FDP",
    "D5": "AfD",
    "D6": "Die Linke",
    "D7": "FREIE WÄHLER",
    "D8": "Die PARTEI",
    "D9": "dieBasis",
    "D11": "ÖDP",
    "D12": "Volt",
    "D13": "Bündnis C",
    "D16": "BSW",
    "D17": "Die Gerechtigkeitspartei",
    "D20": "Tierschutzpartei",
    "D21": "Werteunion",
    "D22": "Übrige",
}
BW_SECOND = {
    "F1": "GRÜNE",
    "F2": "CDU",
    "F3": "SPD",
    "F4": "FDP",
    "F5": "AfD",
    "F6": "Die Linke",
    "F7": "FREIE WÄHLER",
    "F8": "Die PARTEI",
    "F9": "dieBasis",
    "F10": "KlimalisteBW",
    "F11": "ÖDP",
    "F12": "Volt",
    "F13": "Bündnis C",
    "F14": "PdH",
    "F15": "Verjüngungsforschung",
    "F16": "BSW",
    "F17": "Die Gerechtigkeitspartei",
    "F18": "PDR",
    "F19": "PdF",
    "F20": "Tierschutzpartei",
    "F21": "Werteunion",
}
OTHERS = ("Übrige", "Einzelbewerber")
SCHEMA = {"state": pl.String, "wk": pl.String}


def number(text: str | None) -> int | None:
    text = (text or "").strip()
    return None if text in ("", "x", "X", "-", ".") else int(text.replace(".", "").replace(" ", ""))


def table(url: str, encodings: tuple[str, ...], skip_until: str | None = None, delimiter: str = ";") -> list[list[str]]:
    data = raw.cached("de.landtag", url).path.read_bytes()
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    if skip_until is not None:
        lines = lines[next(i for i, line in enumerate(lines) if line.startswith(skip_until)) :]
    return list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))


def fetch() -> None:
    for urls in URLS.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=f".{url.rsplit('.', 1)[1]}")


def parse() -> None:
    cons: list[dict[str, object]] = []
    votes: list[dict[str, object]] = []
    seats: list[dict[str, object]] = []
    members: list[dict[str, object]] = []

    def add_votes(state: str, wk: str, ballot: int, counts: dict[str, int | None]) -> None:
        votes.extend(
            {"state": state, "wk": wk, "ballot": ballot, "party": party, "votes": count}
            for party, count in counts.items()
            if count is not None
        )

    rows = table(URLS["BW"][0], ("utf-8", "cp1252"))
    header = rows[0]
    column = {name: header.index(name) for name in header if name}
    for row in rows[1:]:
        if row[column["Gebietsart"]] != "WAHLKREIS":
            continue
        wk = f"{int(row[column['Wahlkreisnummer']]):02d}"
        cons.append(
            {
                "state": "BW",
                "wk": wk,
                "name": re.sub(r"^\d+ - ", "", row[column["Gebietsname"]]),
                "registered": number(row[column["Wahlberechtigte gesamt (A)"]]),
                "voters": number(row[column["Waehler gesamt (B)"]]),
                "invalid1": number(row[column["Erststimmen ungueltige (C)"]]),
                "valid1": number(row[column["Erststimmen gueltige (D)"]]),
                "invalid2": number(row[column["Zweitstimmen ungueltige (E)"]]),
                "valid2": number(row[column["Zweitstimmen gueltige (F)"]]),
            }
        )
        add_votes("BW", wk, 1, {party: number(row[column[code]]) for code, party in BW_FIRST.items()})
        add_votes("BW", wk, 2, {party: number(row[column[code]]) for code, party in BW_SECOND.items()})
    chosen = table(URLS["BW"][1], ("utf-8", "cp1252"))
    for kind, number_, party, position, title, family, first in chosen[1:]:
        members.append(
            {
                "state": "BW",
                "kind": "direct" if kind == "WAHLKREIS" else "list",
                "wk": f"{int(number_):02d}" if kind == "WAHLKREIS" else None,
                "party": party,
                "family": family,
                "first": first,
                "title": title or None,
                "position": int(position) if position else None,
            }
        )

    rows = table(URLS["MV"][0], ("cp1252",), "Berechnungsdatum")
    header = rows[0]
    first_party = header.index("Gültige Stimmen") + 1
    for row in rows[1:]:
        if row[1] != "A" or row[2] == "99":
            continue
        wk = f"{int(row[2]):02d}"
        ballot = int(row[header.index("Erst-/Zweitstimme")])
        counts = {header[i]: number(row[i]) for i in range(first_party, len(header))}
        add_votes("MV", wk, ballot, counts)
        if ballot == 1:
            cons.append(
                {
                    "state": "MV",
                    "wk": wk,
                    "name": row[3],
                    "registered": number(row[header.index("Wahlberechtigte")]),
                    "voters": number(row[header.index("Wähler")]),
                    "invalid1": number(row[header.index("Ungültige Stimmen")]),
                    "valid1": number(row[header.index("Gültige Stimmen")]),
                    "invalid2": None,
                    "valid2": None,
                }
            )
        else:
            for entry in cons:
                if entry["state"] == "MV" and entry["wk"] == wk:
                    entry["invalid2"] = number(row[header.index("Ungültige Stimmen")])
                    entry["valid2"] = number(row[header.index("Gültige Stimmen")])
    rows = table(URLS["MV"][1], ("cp1252",), "Berechnungsdatum")
    header = rows[0]
    first_party = header.index("Mandatstyp") + 1
    totals: dict[str, int] = {}
    direct: dict[str, int] = {}
    for row in rows[1:]:
        for i in range(first_party, len(header)):
            count = number(row[i])
            if not count:
                continue
            if row[2] == "99" and row[8] == "Insgesamt":
                totals[header[i]] = count
            elif row[8] == "Direktmandate" and row[2] != "99":
                direct[header[i]] = direct.get(header[i], 0) + count
                members.append(
                    {
                        "state": "MV",
                        "kind": "direct",
                        "wk": f"{int(row[2]):02d}",
                        "party": header[i],
                        "family": None,
                        "first": None,
                        "title": None,
                        "position": None,
                    }
                )
    seats.extend(
        {"state": "MV", "party": party, "total": total, "direct": direct.get(party, 0)}
        for party, total in totals.items()
    )

    rows = table(URLS["ST"][0], ("utf-8",))
    header = rows[0]
    for row in rows[1:]:
        if row[2] != "WKR" or row[5] != "":
            continue
        wk = f"{int(row[3]):02d}"
        cons.append(
            {
                "state": "ST",
                "wk": wk,
                "name": row[4],
                "registered": number(row[header.index("A.Wahlberechtigte")]),
                "voters": number(row[header.index("B.Wähler")]),
                "invalid1": number(row[header.index("C.Ungültige.Erststimmen")]),
                "valid1": number(row[header.index("D.Gültige.Erststimmen")]),
                "invalid2": number(row[header.index("E.Ungültige.Zweitstimmen")]),
                "valid2": number(row[header.index("F.Gültige.Zweitstimmen")]),
            }
        )
        for ballot, prefix in ((1, "D"), (2, "F")):
            add_votes(
                "ST",
                wk,
                ballot,
                {
                    ("Einzelbewerber" if name.endswith(".EB") else name.split(".", 1)[1]): number(row[i])
                    for i, name in enumerate(header)
                    if re.fullmatch(prefix + r"\d\d\..+", name)
                },
            )
        winner = re.fullmatch(r"(.+), (.+) \((.+)\)", row[header.index("Gewählt.im.Wahlkreis")])
        if winner is None:
            raise ValueError(f"ST {wk}: no winner")
        members.append(
            {
                "state": "ST",
                "kind": "direct",
                "wk": wk,
                "party": winner[3],
                "family": winner[1],
                "first": winner[2],
                "title": None,
                "position": None,
            }
        )
    for _, party, total, direct_seats, _ in table(URLS["ST"][1], ("utf-8",))[1:]:
        if party != "Insgesamt":
            seats.append({"state": "ST", "party": party, "total": int(total), "direct": int(direct_seats)})

    sheets = {
        ballot: pl.read_excel(raw.cached(SOURCE, URLS["BE"][0]).path, sheet_name=sheet, infer_schema_length=0).rename(
            lambda column: column.replace("\n", "")
        )
        for ballot, sheet in ((1, "AGH_W1"), (2, "AGH_W2"))
    }
    be_parties = set(sheets[2].columns[18:]) | BE_PARTIES
    be_rows: dict[str, pl.DataFrame] = {}
    for ballot, frame in sheets.items():
        frame = frame.with_columns(
            wk=pl.col("Bezirksnummer") + pl.col("Abgeordneten-hauswahlkreis").str.zfill(2),
            district=pl.col("Bezirksname"),
        )
        be_rows[ballot] = frame
        for column in frame.columns[10:-2]:
            frame = frame.with_columns(pl.col(column).cast(pl.Int64))
        be_rows[ballot] = frame
        grouped = frame.group_by("wk").agg(pl.col("district").first(), *(pl.col(c).sum() for c in frame.columns[10:-2]))
        for row in grouped.iter_rows(named=True):
            counts = {
                (column if column in be_parties else f"Einzelbewerber {column}"): row[column]
                for column in frame.columns[18:-2]
                if row[column]
            }
            add_votes("BE", row["wk"], ballot, counts)
            if ballot == 1:
                cons.append(
                    {
                        "state": "BE",
                        "wk": row["wk"],
                        "name": f"{row['district']} {int(row['wk'][2:])}",
                        "registered": row["Wahlberechtigte insgesamt"],
                        "voters": row["Wählende"],
                        "invalid1": row["Ungültige Stimmen"],
                        "valid1": row["Gültige Stimmen"],
                        "invalid2": None,
                        "valid2": None,
                    }
                )
            else:
                for entry in cons:
                    if entry["state"] == "BE" and entry["wk"] == row["wk"]:
                        entry["invalid2"] = row["Ungültige Stimmen"]
                        entry["valid2"] = row["Gültige Stimmen"]
    page = raw.cached(SOURCE, URLS["BE"][1]).path.read_text(encoding="utf-8")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.DOTALL):
        cells = [
            text
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
            if (text := html.unescape(re.sub(r"<[^>]+>|\s+", " ", cell)).strip())
        ]
        if len(cells) != 3 or "," not in cells[1]:
            continue
        area, who, party = cells
        family, _, first = who.partition(",")
        title = re.match(r"((?:Dr\.|Prof\.)(?: (?:Dr\.|Prof\.))*) (.+)", family.strip())
        direct_area = re.fullmatch(r"(\d{4}) - .+", area)
        members.append(
            {
                "state": "BE",
                "kind": "direct" if direct_area else "list",
                "wk": direct_area[1] if direct_area else None,
                "party": party,
                "family": title[2] if title else family.strip(),
                "first": first.strip(),
                "title": title[1] if title else None,
                "position": None,
            }
        )

    sheet = pl.read_excel(raw.cached(SOURCE, URLS["RP"][0]).path, has_header=False, infer_schema_length=0).rows()
    labels, codes = sheet[2], sheet[3]
    column = {code: i for i, code in enumerate(codes) if code}
    for row in sheet[4:]:
        if row[0] is None or not re.fullmatch(r"\d \d{3}", row[0].strip()):
            continue
        wk = f"{int(row[0].strip()[2:]):02d}"
        cons.append(
            {
                "state": "RP",
                "wk": wk,
                "name": re.sub(r", WK$", "", row[2].strip()),
                "registered": number(row[column["A"]]),
                "voters": number(row[column["B"]]),
                "invalid1": number(row[column["C"]]),
                "valid1": number(row[column["D"]]),
                "invalid2": number(row[column["E"]]),
                "valid2": number(row[column["F"]]),
            }
        )
        for ballot, prefix in ((1, "D"), (2, "F")):
            add_votes(
                "RP",
                wk,
                ballot,
                {
                    RP_LABELS.get(labels[i], labels[i]): number(row[i])
                    for code, i in column.items()
                    if re.fullmatch(prefix + r"\d+", code)
                },
            )

    def pdf_lines(url: str) -> list[str]:
        return subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
        ).stdout.splitlines()

    for line in pdf_lines(URLS["RP"][2]):
        match = re.fullmatch(r"\s*(\d+)\s+(.+?), (.+?)\s{2,}(\S+)\s*", line)
        if match:
            title = re.fullmatch(r"((?:Prof\. )?Dr\.) (.+)", match[2])
            members.append(
                {
                    "state": "RP",
                    "kind": "direct",
                    "wk": f"{int(match[1]):02d}",
                    "party": match[4],
                    "family": title[2] if title else match[2],
                    "first": match[3].strip(),
                    "title": title[1] if title else None,
                    "position": None,
                }
            )
    party = None
    for line in pdf_lines(URLS["RP"][3]):
        heading = re.fullmatch(r"\s+[^\d].*\((\S+)\)\s*", line)
        if heading:
            party = heading[1]
            continue
        match = re.fullmatch(r"\s*\d+\s+(.+?), (.+?)(?: \(.+\))?\s{2,}(\d+)\s*", line)
        if match:
            title = re.fullmatch(r"((?:Prof\. )?Dr\.) (.+)", match[1])
            members.append(
                {
                    "state": "RP",
                    "kind": "list",
                    "wk": None,
                    "party": party,
                    "family": title[2] if title else match[1],
                    "first": match[2].strip(),
                    "title": title[1] if title else None,
                    "position": int(match[3]),
                }
            )
    party = None
    rp_seats: dict[tuple[str | None, str], int] = {}
    lines = [line for line in pdf_lines(URLS["RP"][1]) if line.strip()]
    for previous, line in itertools.pairwise(lines):
        if line.strip() == "Über die":
            party = previous.strip()
        match = re.fullmatch(r"Land\s+(\d+)\s+(\d+)\s+(\d+)\s*", line)
        if match:
            if int(match[1]) + int(match[2]) != int(match[3]):
                raise ValueError(f"RP {party}: seats do not add up")
            seats.append({"state": "RP", "party": party, "total": int(match[3]), "direct": int(match[1])})
            rp_seats[party, "direct"], rp_seats[party, "list"] = int(match[1]), int(match[2])

    member_frame = pl.DataFrame(members, infer_schema_length=None)
    for party, kind, count in member_frame.filter(pl.col("state") == "RP").group_by("party", "kind").len().iter_rows():
        if count != rp_seats.get((party, kind)):
            raise ValueError(f"RP {party} {kind}: {count} members against the declared seats")
    for state in ("BW", "BE"):
        counts = member_frame.filter(pl.col("state") == state).group_by("party", "kind").len()
        for party in counts["party"].unique():
            sub = counts.filter(pl.col("party") == party)
            seats.append(
                {
                    "state": state,
                    "party": party,
                    "total": int(sub["len"].sum()),
                    "direct": int(sub.filter(pl.col("kind") == "direct")["len"].sum()),
                }
            )
    constituencies = pl.DataFrame(cons, infer_schema_length=None)
    vote_frame = pl.DataFrame(votes, infer_schema_length=None)
    for state, expected in (("BW", 157), ("MV", 71), ("ST", 83), ("BE", 153), ("RP", 105)):
        total = sum(row["total"] for row in seats if row["state"] == state)
        if total != expected:
            raise ValueError(f"{state}: {total} seats, expected {expected}")
    for state, wks in constituencies.group_by("state").agg(pl.col("wk").n_unique()).iter_rows():
        valid = constituencies.filter(pl.col("state") == state)
        for ballot, field in ((1, "valid1"), (2, "valid2")):
            summed = (
                vote_frame.filter((pl.col("state") == state) & (pl.col("ballot") == ballot))
                .group_by("wk")
                .agg(pl.col("votes").sum())
                .join(valid.select("wk", field), on="wk")
                .filter(pl.col("votes") != pl.col(field))
            )
            if summed.height:
                raise ValueError(
                    f"{state} ballot {ballot}: party votes differ from valid votes in {summed.height} constituencies\n{summed}"
                )
        print(state, wks, "constituencies")
    PARSED.mkdir(parents=True, exist_ok=True)
    constituencies.write_parquet(PARSED / "constituencies.parquet")
    vote_frame.write_parquet(PARSED / "votes.parquet")
    pl.DataFrame(seats).write_parquet(PARSED / "seats.parquet")
    member_frame.write_parquet(PARSED / "members.parquet")


def emit() -> Path:
    constituencies = pl.read_parquet(PARSED / "constituencies.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="German state elections of 2026 (Baden-Württemberg, Berlin, Mecklenburg-Vorpommern, Rhineland-Palatinate, and Saxony-Anhalt) by constituency",
            polity="DE",
        )

        def name(entity: UUID, text: str, kind: str = "official", lang: str = "en") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        fptp = cs.key("de.electoral_system", CONSTITUENCIES[0])
        if fptp in cs.minted:
            raise ValueError("run de.btw first: the constituency system must exist")
        party_ids: dict[str, UUID] = {}
        for party in sorted(set(votes["party"]) | set(seats["party"]) | set(members["party"].drop_nulls())):
            if party in OTHERS or party.startswith("Einzelbewerber "):
                continue
            party_ids[party] = cs.key("de.party", party.upper())
            if party_ids[party] in cs.minted:
                cs.add("party", id=party_ids[party], kind="party", polity_id=germany)
                name(party_ids[party], party, "official", "de")

        def emit_state(abbreviation: str, state: dict[str, Any]) -> None:
            day: date = state["day"]
            land = cs.polity(f"DE-{abbreviation}")
            land_area = cs.area("de.land", state["land"])
            if {land, land_area} & cs.minted:
                raise ValueError(f"run de.bundeswahlleiterin first: {abbreviation} must exist")
            scheme = f"de.{abbreviation.lower()}_wahlkreis"
            cs.add("scheme", code=scheme, polity_id=land, kind="electoral_district", rank=2, noun="constituency")
            source = cs.source(state["publisher"], state["title"])
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=state["publisher"],
                title=state["title"],
                url=state["url"],
                license="Datenlizenz Deutschland – Namensnennung – Version 2.0",
                license_url="https://www.govdata.de/dl-de/by-2-0",
            )
            for url in URLS[abbreviation]:
                cs.artifact(raw.cached(SOURCE, url), source)

            state_seats = seats.filter(pl.col("state") == abbreviation)
            total = int(state_seats["total"].sum())
            body = cs.key(f"{SOURCE}.body", f"landtag:{abbreviation}")
            cs.add("body", id=body, polity_id=land, kind="unicameral")
            name(body, state["body"][0])
            name(body, state["body"][1], "native", "de")
            cs.add("body_size", body_id=body, seats=total, valid_from=day, valid_to=None)
            system = cs.key(f"{SOURCE}.electoral_system", abbreviation)
            cs.add("electoral_system", id=system, polity_id=land, rules=state["rules"], rules_version=1)
            name(system, f"{state['name']} Landtag seat allocation")
            cs.add("note", entity_id=system, kind="methodology", body_md=state["note"])

            election = cs.key(f"{SOURCE}.election", f"{abbreviation}:{day}")
            cs.add("election", id=election, polity_id=land, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} {state['name']} state election")
            name(election, state["native"], "native", "de")
            path = f"de/{abbreviation.lower()}/{day}-general"
            cs.add("slug", path=path, entity_id=election)

            def make_set(key: str, contest: UUID | None, election: UUID = election, source: UUID = source) -> UUID:
                set_id = cs.key(f"{SOURCE}.result_set", f"{abbreviation}:{day}:{key}")
                cs.add(
                    "result_set",
                    id=set_id,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                return set_id

            rows = constituencies.filter(pl.col("state") == abbreviation).sort("wk")
            units: dict[str, UUID] = {}
            areas: dict[str, UUID] = {}
            for wk, label in rows.select("wk", "name").iter_rows():
                areas[wk] = cs.area(scheme, wk, day)
                cs.add("area", id=areas[wk], scheme_code=scheme)
                cs.add("area_code", area_id=areas[wk], code=wk, valid_from=day, valid_to=None)
                cs.add("area_parent", area_id=areas[wk], parent_id=land_area, valid_from=day, valid_to=None)
                name(areas[wk], label, "official", "de")
                units[wk] = cs.unit(scheme, wk, day)
                cs.add(
                    "reporting_unit",
                    id=units[wk],
                    polity_id=land,
                    scheme_code=scheme,
                    code=wk,
                    area_relation="equals",
                    area_id=areas[wk],
                    kind="standard",
                    valid_from=day,
                    valid_to=None,
                )
            cs.add("boundary_set", scheme_code=scheme, vintage=str(day.year), valid_from=day, valid_to=None, as_of=day)

            accounting = make_set("election", None)
            sets_values: list[pl.DataFrame] = []

            def values(
                set_id: UUID, unit: str, option: UUID | None, measure: int, count: int | None
            ) -> dict[str, object]:
                return {
                    "result_set_id": str(set_id),
                    "unit_id": str(units[unit]),
                    "option_id": None if option is None else str(option),
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "flags": 0,
                    "value": count,
                }

            value_rows: list[dict[str, object]] = []
            for row in rows.iter_rows(named=True):
                value_rows.append(values(accounting, row["wk"], None, 2, row["registered"]))
                value_rows.append(values(accounting, row["wk"], None, 3, row["voters"]))

            root = cs.key(f"{SOURCE}.contest", f"{abbreviation}:{day}:list")
            cs.add(
                "contest",
                id=root,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=land_area,
                magnitude=total,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(root, f"{day.year} {state['name']} state election, seat allocation")
            cs.add("slug", path=f"{path}/landtag", entity_id=root)
            root_set = make_set("list", root)
            list_options: dict[str, UUID] = {}
            for party in sorted(
                set(votes.filter((pl.col("state") == abbreviation) & (pl.col("ballot") == 2))["party"])
            ):
                list_options[party] = cs.key(f"{SOURCE}.option", f"{abbreviation}:{day}:list:{party}")
                cs.add(
                    "contest_option",
                    id=list_options[party],
                    contest_id=root,
                    kind="others" if party in OTHERS else "list",
                    party_id=None if party in OTHERS else party_ids[party],
                    ballot_label=party,
                )
            for party, won in state_seats.select("party", "total").iter_rows():
                if won:
                    cs.add("outcome", result_set_id=root_set, option_id=list_options[party], kind="won", seats=won)
            for row in rows.iter_rows(named=True):
                value_rows.append(values(root_set, row["wk"], None, 5, row["invalid2"]))
                value_rows.append(values(root_set, row["wk"], None, 4, row["valid2"]))
            for wk, party, count in (
                votes.filter((pl.col("state") == abbreviation) & (pl.col("ballot") == 2))
                .select("wk", "party", "votes")
                .iter_rows()
            ):
                value_rows.append(values(root_set, wk, list_options[party], 1, count))

            persons: dict[str, UUID] = {}

            def person_for(member: dict[str, object]) -> UUID:
                key = f"{abbreviation}:{day}:{member['family']}|{member['first']}|{member['party']}"
                if key not in persons:
                    persons[key] = cs.key(f"{SOURCE}.person", key)
                    if persons[key] in cs.minted:
                        cs.add("person", id=persons[key])
                        name(persons[key], f"{member['first']} {member['family']}", "official", "de")
                return persons[key]

            direct = {
                row["wk"]: row
                for row in members.filter((pl.col("state") == abbreviation) & (pl.col("kind") == "direct")).iter_rows(
                    named=True
                )
            }
            for row in rows.iter_rows(named=True):
                wk = row["wk"]
                contest = cs.key(f"{SOURCE}.contest", f"{abbreviation}:{day}:{wk}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=areas[wk],
                    magnitude=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} {state['name']} state election, constituency {wk} ({row['name']})")
                cs.add("slug", path=f"{path}/wahlkreis-{wk}", entity_id=contest)
                cs.add("note", entity_id=contest, kind="context", body_md=state["winners"])
                contest_set = make_set(f"wk:{wk}", contest)
                value_rows.append(values(contest_set, wk, None, 5, row["invalid1"]))
                value_rows.append(values(contest_set, wk, None, 4, row["valid1"]))
                options: dict[str, UUID] = {}
                for party, count in (
                    votes.filter((pl.col("state") == abbreviation) & (pl.col("ballot") == 1) & (pl.col("wk") == wk))
                    .select("party", "votes")
                    .iter_rows()
                ):
                    options[party] = cs.key(f"{SOURCE}.option", f"{abbreviation}:{day}:{wk}:{party}")
                    cs.add(
                        "contest_option",
                        id=options[party],
                        contest_id=contest,
                        kind="others" if party in OTHERS else "candidate",
                        party_id=party_ids.get(party),
                        ballot_label=party,
                    )
                    value_rows.append(values(contest_set, wk, options[party], 1, count))
                winner = direct[wk]
                option = options[winner["party"]]
                candidacy = None
                if winner["family"] is not None:
                    candidacy = cs.key(f"{SOURCE}.candidacy", f"{abbreviation}:{day}:{wk}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person_for(winner),
                        option_id=option,
                        role="head",
                        ballot_name=f"{winner['first']} {winner['family']}",
                        member_of_party_id=party_ids.get(winner["party"]),
                    )
                cs.add(
                    "outcome",
                    result_set_id=contest_set,
                    option_id=option,
                    candidacy_id=candidacy,
                    kind="elected",
                    seats=1,
                )
            for member in members.filter((pl.col("state") == abbreviation) & (pl.col("kind") == "list")).iter_rows(
                named=True
            ):
                candidacy = cs.key(
                    f"{SOURCE}.candidacy",
                    f"{abbreviation}:{day}:list:{member['party']}:{member['position']}:{member['family']}:{member['first']}",
                )
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person_for(member),
                    option_id=list_options[member["party"]],
                    role="list_member",
                    list_position=member["position"],
                    ballot_name=f"{member['first']} {member['family']}",
                    member_of_party_id=party_ids[member["party"]],
                )
                cs.add(
                    "outcome",
                    result_set_id=root_set,
                    option_id=list_options[member["party"]],
                    candidacy_id=candidacy,
                    kind="elected",
                )
            sets_values.append(pl.DataFrame(value_rows, schema_overrides={"value": pl.Int64}, infer_schema_length=None))
            cs.frame(
                "result_value",
                pl.concat(sets_values)
                .drop_nulls("value")
                .with_columns(
                    channel=pl.col("channel").cast(pl.Int16),
                    measure=pl.col("measure").cast(pl.Int16),
                    count_round=pl.col("count_round").cast(pl.Int16),
                    flags=pl.col("flags").cast(pl.Int16),
                ),
            )

        for abbreviation, state in STATES.items():
            emit_state(abbreviation, state)
        return cs.write()
