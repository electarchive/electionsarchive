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
VERSION = "4"
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
KEYS = {"KPD": "KPD:1990"}
HISTORY = "https://www.bundeswahlleiterin.de/dam/jcr/a333e523-0717-42ad-a772-d5ad7e7e97cc/ltw_erg_gesamt.pdf"
EARLY_BY = "https://statistik.bayern.de/mam/produkte/jahrbuch/jb2024_08_wahlen.pdf"
EARLY_BY_SEATS = "https://www.bayern.landtag.de/fileadmin/scripts/get_file/Fraktionsstaerken_seit_1946.pdf"
EARLY_BUND = "https://www.statistischebibliothek.de/mir/servlets/MCRFileNodeServlet/DEHeft_derivate_00021350/StatBer-8-05-010.pdf"
EARLY_BB = "https://www.statistischebibliothek.de/mir/servlets/MCRFileNodeServlet/BBHeft_derivate_00014662/SB_B07-03-03_1993j05_BBa.pdf"
EARLY_BB_SEATS = "https://www.chronik-der-mauer.de/system/files/dokument_pdf/58719_cdm-901014-landtagswahlen.pdf"
EARLY_SOURCES = {
    "BY": (
        "Bayerisches Landesamt für Statistik",
        "Bayern in Zahlen: Statistisches Jahrbuch für Bayern 2024, VIII. Wahlen, C. Landtagswahl",
        EARLY_BY,
        None,
    ),
    "HE": (
        "Statistisches Bundesamt",
        (
            "Die Bundestagswahlen 1949 und 1953 und die Landtagswahlen 1946 bis 1956 (Statistische Berichte "
            "VIII/5/10), compiled from the statistical offices of the Länder"
        ),
        EARLY_BUND,
        "Nachdruck, auch auszugsweise, nur mit Quellenangabe gestattet",
    ),
    "BB": (
        "Landesamt für Datenverarbeitung und Statistik Brandenburg",
        (
            "Statistischer Bericht B VII 3-3: Kommunalwahl 1993 im Land Brandenburg, with the results of the 1990 "
            "Landtag election in table 2"
        ),
        EARLY_BB,
        None,
    ),
}
EARLY_SOURCES["HH"] = EARLY_SOURCES["HE"]
EARLY = (
    {
        "state": "BY",
        "day": date(1946, 12, 1),
        "registered": 4210636,
        "voters": 3189552,
        "invalid": 141215,
        "valid": 3048337,
        "seats": 180,
        "parties": (
            ("CSU", 1593908, 104),
            ("SPD", 871760, 54),
            ("FDP", 172242, 9),
            ("KPD", 185023, 0),
            ("WAV", 225404, 13),
        ),
        "extra": (
            "The voters, invalid, and valid figures are those of the Bayerisches Landesamt für Statistik. The party "
            f"votes are from the Statistisches Bundesamt's compilation ({EARLY_BUND}), and the seats are from the "
            f"Bavarian Landtag ({EARLY_BY_SEATS})."
        ),
    },
    {
        "state": "HE",
        "day": date(1946, 12, 1),
        "registered": 2380109,
        "voters": 1741416,
        "invalid": 132028,
        "valid": 1609388,
        "seats": 90,
        "parties": (("CDU", 498158, 28), ("SPD", 687431, 38), ("LDP", 252207, 14), ("KPD", 171592, 10)),
        "extra": (
            "The source compiles the figures from the Hessisches Statistisches Landesamt. Voters also adopted the "
            "constitution of Hesse in a referendum on the same day."
        ),
    },
    {
        "state": "HH",
        "day": date(1946, 10, 13),
        "registered": 968454,
        "voters": 765008,
        "invalid": 29415,
        "valid": 735593,
        "seats": 110,
        "parties": (
            ("CDU", 196069, 16),
            ("SPD", 317215, 83),
            ("FDP", 133393, 7),
            ("KPD", 76509, 4),
            ("Sonstige", 12407, 0),
        ),
        "extra": (
            "The source compiles the figures from the Statistisches Landesamt Hamburg. The other parties' row adds "
            "the 2,519 votes of the DKP/DRP to the 9,888 votes of other parties and independents."
        ),
    },
    {
        "state": "BB",
        "day": date(1990, 10, 14),
        "registered": 1963926,
        "voters": 1317288,
        "invalid": 38533,
        "valid": 1278755,
        "seats": 88,
        "parties": (
            ("SPD", 488585, 36),
            ("PDS", 171391, 13),
            ("CDU", 376612, 27),
            ("FDP", 84743, 6),
            ("GRÜNE/B 90", 118195, 6),
            ("Sonstige", 39229, 0),
        ),
        "extra": (
            "Each voter had two votes. The votes shown are second votes (Zweitstimmen), which decide the seats. The "
            "source prints the row GRÜNE/B90, and the seats are from the official transcript of the final result "
            f"({EARLY_BB_SEATS}), which names the list that won six seats Bündnis 90."
        ),
    },
)
HISTORY_PUBLISHER = "Die Bundeswahlleiterin"
HISTORY_TITLE = "Ergebnisse früherer Landtagswahlen, Stand: 13. Oktober 2026"
LAENDER = {
    "Schleswig-Holstein": "SH",
    "Hamburg": "HH",
    "Niedersachsen": "NI",
    "Bremen": "HB",
    "Nordrhein-Westfalen": "NW",
    "Hessen": "HE",
    "Rheinland-Pfalz": "RP",
    "Baden-Württemberg": "BW",
    "Bayern": "BY",
    "Saarland": "SL",
    "Berlin": "BE",
    "Brandenburg": "BB",
    "Mecklenburg-Vorpommern": "MV",
    "Sachsen": "SN",
    "Sachsen-Anhalt": "ST",
    "Thüringen": "TH",
}
LAND_INFO = {
    "SH": (
        "01",
        "Schleswig-Holstein",
        ("Landtag of Schleswig-Holstein", "Schleswig-Holsteinischer Landtag"),
        "Landtagswahl in Schleswig-Holstein",
    ),
    "HH": ("02", "Hamburg", ("Hamburg Parliament", "Hamburgische Bürgerschaft"), "Bürgerschaftswahl in Hamburg"),
    "NI": (
        "03",
        "Lower Saxony",
        ("Landtag of Lower Saxony", "Niedersächsischer Landtag"),
        "Landtagswahl in Niedersachsen",
    ),
    "HB": ("04", "Bremen", ("Bremen Parliament", "Bremische Bürgerschaft"), "Bürgerschaftswahl in Bremen"),
    "NW": (
        "05",
        "North Rhine-Westphalia",
        ("Landtag of North Rhine-Westphalia", "Landtag Nordrhein-Westfalen"),
        "Landtagswahl in Nordrhein-Westfalen",
    ),
    "HE": ("06", "Hesse", ("Landtag of Hesse", "Hessischer Landtag"), "Landtagswahl in Hessen"),
    "RP": ("07", "Rhineland-Palatinate", None, "Landtagswahl in Rheinland-Pfalz"),
    "BW": ("08", "Baden-Württemberg", None, "Landtagswahl in Baden-Württemberg"),
    "BY": ("09", "Bavaria", ("Landtag of Bavaria", "Bayerischer Landtag"), "Landtagswahl in Bayern"),
    "SL": ("10", "Saarland", ("Landtag of Saarland", "Landtag des Saarlandes"), "Landtagswahl im Saarland"),
    "BE": ("11", "Berlin", None, "Wahl zum Abgeordnetenhaus von Berlin"),
    "BB": ("12", "Brandenburg", ("Landtag of Brandenburg", "Landtag Brandenburg"), "Landtagswahl in Brandenburg"),
    "MV": ("13", "Mecklenburg-Vorpommern", None, "Landtagswahl in Mecklenburg-Vorpommern"),
    "SN": ("14", "Saxony", ("Landtag of Saxony", "Sächsischer Landtag"), "Landtagswahl in Sachsen"),
    "ST": ("15", "Saxony-Anhalt", None, "Landtagswahl in Sachsen-Anhalt"),
    "TH": ("16", "Thuringia", ("Landtag of Thuringia", "Thüringer Landtag"), "Landtagswahl in Thüringen"),
}
HISTORY_OTHERS = ("Wgr./Einzelbew.", "Sonstige")
HISTORY_RELABELED = {
    ("BY", date(1950, 11, 26)): (
        ("WAV", "Wirtschaftliche Aufbau-Vereinigung"),
        ("KPD", "Kommunistische Partei Deutschlands"),
        ("Der Deutsche Block", "Der Deutsche Block"),
        ("Königspartei", "Königspartei"),
        ("WKHE", "Wahlblock der Kriegsgeschädigten, Heimatvertriebenen und Entrechteten"),
        ("BdH", "Block der Heimatvertriebenen"),
        ("VWPE", "Vereinigung wirtschaftlich und politisch Entrechteter"),
        ("UBN", "Unpolitische Bäuerliche Notgemeinschaft – Hilfe für den Bayerischen Wald"),
    ),
    ("BY", date(1978, 10, 15)): (
        ("KBW", "Kommunistischer Bund Westdeutschland"),
        ("UWV", "UWV"),
        ("EAP", "Europäische Arbeiterpartei"),
    ),
}
HISTORY_SKIP = {("BE", date(1946, 10, 20))}
HISTORY_NOTES = {
    "Stand nach der Wiederholungswahl am 12.03.1961.": "The figures include the repeat vote of 12 March 1961.",
    "Stand nach der Wiederholungswahl am 25.01.1976.": "The figures include the repeat vote of 25 January 1976.",
    "Stand nach der Wiederholungswahl am 15.05.2011 im Wahlkreis 17. Jeder Wählende hatte 5 Stimmen.": (
        "The figures include the repeat vote of 15 May 2011 in constituency 17."
    ),
    "Einbeziehung des Ergebnisses der Wiederholungswahl am 6. Juli 2008 im Wahlbezirk 132/02.": (
        "The figures include the repeat vote of 6 July 2008 in polling district 132/02."
    ),
    "Einschließlich Nachwahl am 18.09.2011 im Wahlkreis 33 Rügen I.": (
        "The figures include the delayed vote of 18 September 2011 in constituency 33 (Rügen I)."
    ),
    "Berichtigung und Neufeststellung des Ergebnisses durch den Landtag am 28.01.2010.": (
        "The Landtag corrected the result and declared it again on 28 January 2010."
    ),
    "Korrektur der Zahl der Wahlberechtigten im Wege des Wahlprüfungsverfahrens am 1. August 2008.": (
        "The number of registered voters was corrected in the election review of 1 August 2008."
    ),
    "Jeder Wählende konnte bis zu 4 Stimmen abgeben.": "Each voter could cast up to four votes.",
    "SpB: Nur im Wahlbereich der Stadt Bremen.": "SpB stood only in the city of Bremen.",
    "WI Europ. Arbeiterp.: Nur im Wahlbereich der Stadt Bremen.": "WI Europ. Arbeiterp. stood only in the city of Bremen.",
    "DKP (1946-47): Deutsche Konservative Partei.": "DKP (1946-47) is the Deutsche Konservative Partei.",
}
HISTORY_IGNORED = (
    "Ab der Wahl am",
    "Jeder Wählende hatte 5 Stimmen",
    "Stand nach der Wiederholungswahl am 12.02.2023.",
)
PARTY_ALIASES = {
    "PDS": "DIE LINKE",
    "LL/PDS": "DIE LINKE",
    "LL-PDS": "DIE LINKE",
    "PDS/LL": "DIE LINKE",
    "PDS HAMBURG": "DIE LINKE",
    "DIE LINKE.": "DIE LINKE",
    "GB/BHE": "GB/BHE",
    "ZENTRUM": "ZENTRUM",
    "DP": "DP",
    "PRO DM": "PRO DM",
}
PARTY_STOPWORDS = {"der", "die", "das", "und", "für", "e", "v", "in", "des", "landesverband"}
HISTORY_ROWS = {
    "Wahlberechtigte": "registered",
    "Wählende": "voters",
    "Wähler": "voters",
    "Gesamtstimmen": "cast",
    "Ungültige Stimmen": "invalid",
    "Ungültige Stimmzettel": "invalid",
    "Gültige Stimmzettel": "valid_ballots",
    "Ausgefallene Stimmen": "lost",
    "Gültige Stimmen": "valid",
}
FOOTNOTED = ("DKP (1946-47)", "SpB", "WI Europ. Arbeiterp.")
SUPERSCRIPTS = str.maketrans("¹²³", "123")
SCHEDULE = (
    ("BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV"),
    ("NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH"),
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
    raw.fetch(SOURCE, HISTORY, suffix=".pdf")
    for url in (EARLY_BY, EARLY_BY_SEATS, EARLY_BUND, EARLY_BB, EARLY_BB_SEATS):
        raw.fetch(SOURCE, url, suffix=".pdf")


def history_count(text: str) -> int | None:
    return None if text in ("X", "–", ".") else int(text.replace(".", ""))


def parse_history() -> None:
    path = str(raw.cached(SOURCE, HISTORY).path)
    pages = int(
        re.search(
            r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", path], check=True, capture_output=True, text=True).stdout
        )[1]
    )

    def text(page: int, *crop: str) -> str:
        return subprocess.run(
            ["pdftotext", "-layout", "-f", str(page), "-l", str(page), *crop, path, "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    scheduled: set[tuple[str, date]] = set()
    block = -1
    anchors: list[int] = []
    for line in text(5).splitlines():
        if "Tag der Wahl in" in line:
            block += 1
        row = re.match(r"\s*(\d+)\.\s", line)
        if row is None:
            continue
        found = [(match.start(), match[0]) for match in re.finditer(r"\d\d\.\d\d\.\d{4}", line)]
        if row[1] == "1":
            pending_first = found
            continue
        if row[1] == "2":
            anchors = [start for start, _ in found]
            found = found + pending_first
        for start, day in found:
            column = min(range(len(anchors)), key=lambda i: abs(anchors[i] - start))
            scheduled.add((SCHEDULE[block][column], date(int(day[6:]), int(day[3:5]), int(day[:2]))))
    elections: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    glossary: list[dict[str, object]] = []
    for page in range(1, pages + 1):
        full = text(page)
        lines = full.splitlines()
        if not lines or not re.match(
            r"3\.\d+\s+Wahlberechtigte, Wähle(?:nde|r), Stimmabgabe und Sitzverteilung in den Ländern", lines[0]
        ):
            continue
        state = LAENDER[lines[1].strip()]
        footnotes: dict[str, str] = {}
        pending = None
        for line in lines:
            if pending is not None and line.strip():
                footnotes[pending] = line.strip()
                pending = None
            elif re.fullmatch(r"\s*([\d¹²³])\s*", line):
                pending = line.strip().translate(SUPERSCRIPTS)
            elif match := re.fullmatch(r"\s{0,3}([\d¹²³]) {0,3}([A-ZÄÖÜ].+)", line):
                footnotes[match[1].translate(SUPERSCRIPTS)] = match[2].strip()
        cut = next((i for i, line in enumerate(lines) if "Volle Bezeichnung des Parteinamens" in line), len(lines))
        if cut < len(lines):
            entry = None
            for line in lines[next(i for i in range(cut, len(lines)) if "Abkürzung" in lines[i]) + 1 :]:
                if line.startswith("Informationen der Bundeswahlleiterin") or re.match(r"\s{0,3}\d ", line):
                    break
                match = re.fullmatch(r"(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s*", line)
                if match:
                    entry = {"state": state, "label": match[1], "name": match[2], "remark": match[3]}
                    glossary.append(entry)
                elif entry is not None and line.strip():
                    parts = re.split(r"\s{2,}", line.strip())
                    column = len(line) - len(line.lstrip())
                    if column < 60:
                        entry["name"] = f"{entry['name']} {parts[0]}"
                        parts = parts[1:]
                    if parts:
                        entry["remark"] = f"{entry['remark']} {' '.join(parts)}"
        current: list[dict[str, object] | None] = [None, None]
        positions = [0, 0]
        split: int | None = None
        for line in lines[:cut]:
            headings = list(re.finditer(r"(?:^|\s{3})(Wahl\b.*?)am (\d\d)\.(\d\d)\.(\d{4})([\d¹²³]?)", line))
            for heading in headings:
                column = int(heading.start(1) > 60)
                mark = heading[5].translate(SUPERSCRIPTS)
                current[column] = {
                    "index": len(elections),
                    "state": state,
                    "day": date(int(heading[4]), int(heading[3]), int(heading[2])),
                    "heading": f"{heading[1]}am {heading[2]}.{heading[3]}.{heading[4]}".strip(),
                    "notes": [footnotes[mark]] if mark in footnotes else [],
                    "page": page,
                }
                elections.append(current[column])
                positions[column] = 0
            if headings:
                if len(headings) == 1 and headings[0].start(1) <= 60:
                    current[1] = None
                continue
            if "Kurzbezeichnung" in line:
                found = [match.start() for match in re.finditer("Kurzbezeichnung", line)]
                split = found[1] - 3 if len(found) > 1 else None
                continue
            for column, part in enumerate((line, "") if split is None else (line[:split], line[split:])):
                row = re.fullmatch(r"\s*(\S.*?)\s+([\d.]+|–|X|\.)(?:\s+([\d,]+|X|–|\.))?\s+(\d+|–|X|\.)\s*", part)
                election = current[column]
                if election is None or row is None:
                    continue
                label = row[1]
                mark = label[-1].translate(SUPERSCRIPTS)
                if (
                    re.search(r"[^\d\s][\d¹²³]$", label)
                    and mark in footnotes
                    and label[:-1] in {*HISTORY_ROWS, *FOOTNOTED}
                ):
                    note = footnotes[mark]
                    label = label[:-1]
                    election["notes"].append(note if label in HISTORY_ROWS else f"{label}: {note}")
                if label in HISTORY_ROWS:
                    election[HISTORY_ROWS[label]] = history_count(row[2])
                    if label == "Gültige Stimmen":
                        election["seats"] = history_count(row[4])
                    continue
                positions[column] += 1
                results.append(
                    {
                        "index": election["index"],
                        "position": positions[column],
                        "label": label,
                        "votes": history_count(row[2]),
                        "share": float(row[3].replace(",", ".")) if row[3] and row[3][0].isdigit() else None,
                        "seats": history_count(row[4]) or 0,
                    }
                )
    for election in elections:
        labels = {row["label"] for row in results if row["index"] == election["index"]}
        if election["state"] == "HH" and election["day"] == date(1991, 6, 2) and "STATT Partei" in labels:
            election["day"] = date(1993, 9, 19)
            election["heading"] = "Wahl am 19.09.1993"
        if election["state"] == "HH" and election["seats"] == 360:
            election["seats"] = sum(row["seats"] for row in results if row["index"] == election["index"])
        rows = sorted((row for row in results if row["index"] == election["index"]), key=lambda row: row["position"])
        seen: set[str] = set()
        election["folded"] = None
        election["relabeled"] = None
        for row in rows:
            if row["label"] in seen:
                election["folded"] = row["position"]
                break
            seen.add(row["label"])
        relabels = HISTORY_RELABELED.get((election["state"], election["day"]))
        if relabels and election["folded"] is not None:
            tail = [row for row in rows if row["position"] >= election["folded"]]
            if len(tail) != len(relabels):
                raise ValueError(f"{election['state']} {election['day']}: {len(tail)} repeated rows")
            for row, (label, name_) in zip(tail, relabels, strict=True):
                row["label"] = label
                row["seats"] = 0
                glossary.append({"state": election["state"], "label": label, "name": name_, "remark": ""})
            election["seats"] = sum(row["seats"] for row in results if row["index"] == election["index"])
            election["relabeled"] = election["folded"]
            election["folded"] = None
        if election["folded"] is not None:
            tail = [row for row in rows if row["position"] >= election["folded"]]
            results = [row for row in results if not any(row is other for other in tail)]
            results.append(
                {
                    "index": election["index"],
                    "position": election["folded"],
                    "label": "Sonstige",
                    "votes": sum(row["votes"] for row in tail),
                    "share": None,
                    "seats": 0,
                }
            )
            election["seats"] = sum(row["seats"] for row in results if row["index"] == election["index"])
    for early in EARLY:
        index = len(elections)
        elections.append(
            {
                "index": index,
                "state": early["state"],
                "day": early["day"],
                "heading": f"Wahl am {early['day']:%d.%m.%Y}",
                "notes": [],
                "page": None,
                "registered": early["registered"],
                "voters": early["voters"],
                "invalid": early["invalid"],
                "valid": early["valid"],
                "seats": early["seats"],
                "folded": None,
                "extra": early["extra"],
                "source": early["state"],
            }
        )
        for position, (label, votes, seats) in enumerate(early["parties"], 1):
            results.append(
                {"index": index, "position": position, "label": label, "votes": votes, "share": None, "seats": seats}
            )
    election_frame = pl.DataFrame(elections, infer_schema_length=None)
    result_frame = pl.DataFrame(results, infer_schema_length=None)
    problems = []
    for election in election_frame.iter_rows(named=True):
        rows = result_frame.filter(pl.col("index") == election["index"])
        votes = rows["votes"].drop_nulls().sum() if rows["votes"].null_count() < rows.height else None
        seats = rows["seats"].sum()
        if votes is not None and election["valid"] is not None and votes != election["valid"]:
            problems.append(
                f"{election['state']} {election['day']}: party votes {votes} against valid {election['valid']}"
            )
        if seats != election["seats"]:
            problems.append(
                f"{election['state']} {election['day']}: party seats {seats} against total {election['seats']}"
            )
        accounted = sum(
            election[field] or 0
            for field in ("invalid", "lost", "valid_ballots" if election["valid_ballots"] else "valid")
        )
        if election["voters"] is not None and accounted != election["voters"]:
            problems.append(f"{election['state']} {election['day']}: voters {election['voters']} against {accounted}")
    shares = result_frame.join(election_frame.select("index", "valid"), on="index").filter(
        ((pl.col("votes") / pl.col("valid") * 100).round(1) - pl.col("share")).abs() > 0.15
    )
    for row in shares.iter_rows(named=True):
        problems.append(f"share mismatch {row}")
    held = set(election_frame.select("state", "day").iter_rows())
    for state, day in sorted(scheduled - held):
        problems.append(f"{state} {day}: scheduled but not in the tables")
    for state, day in sorted(held - scheduled):
        problems.append(f"{state} {day}: in the tables but not scheduled")
    for problem in problems:
        print(problem)
    print(election_frame.height, "elections,", result_frame.height, "party rows,", len(glossary), "glossary entries")
    PARSED.mkdir(parents=True, exist_ok=True)
    election_frame.write_parquet(PARSED / "history_elections.parquet")
    result_frame.write_parquet(PARSED / "history_results.parquet")
    pl.DataFrame(glossary).write_parquet(PARSED / "history_glossary.parquet")


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
    parse_history()


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
            summary="German state elections: 2026 (Baden-Württemberg, Berlin, Mecklenburg-Vorpommern, Rhineland-Palatinate, and Saxony-Anhalt) by constituency, and every earlier Landtag election since 1946 by Land from the Bundeswahlleiterin and the Länder statistical offices",
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
            party_ids[party] = cs.key("de.party", KEYS.get(party, party.upper()))
            if party_ids[party] in cs.minted:
                cs.add("party", id=party_ids[party], kind="party", polity_id=germany)
                name(party_ids[party], party, "official", "de")
        cs.merge(party_ids["KPD"], cs.key("de.party", "KPD (OST)"))

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
                    candidacy_id=None,
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

        history_elections = pl.read_parquet(PARSED / "history_elections.parquet")
        history_results = pl.read_parquet(PARSED / "history_results.parquet")
        glossary = pl.read_parquet(PARSED / "history_glossary.parquet")
        emit_history(cs, conn, germany, history_elections, history_results, glossary)
        return cs.write()


def party_words(text: str) -> set[str]:
    return (
        set(re.findall(r"\w+", text.casefold()))
        - PARTY_STOPWORDS
        - {word.casefold() for land in LAENDER for word in re.findall(r"\w+", land)}
    )


def similar(a: str, b: str) -> bool:
    left, right = party_words(a), party_words(b)
    return bool(left and right) and len(left & right) / min(len(left), len(right)) >= 0.75


def history_parties(
    cs: Changeset, conn: Any, germany: UUID, results: pl.DataFrame, glossary: pl.DataFrame
) -> dict[tuple[str, str], UUID]:
    full: dict[tuple[str, str], str] = {}
    for state, label, name_ in glossary.select("state", "label", "name").iter_rows():
        full.setdefault((state, label), name_)
    anywhere = {label: name_ for (_, label), name_ in full.items()}
    folded = {(state, label.casefold()): name_ for (state, label), name_ in full.items()}
    known: dict[str, list[str]] = {}
    for value, text_ in conn.execute(
        "select e.value, n.text from external_id e join name n on n.entity_id = e.entity_id where e.scheme = 'de.party'"
    ).fetchall():
        known.setdefault(value, []).append(text_)
    used = sorted(set(results.filter(~pl.col("label").is_in(HISTORY_OTHERS)).select("state", "label").iter_rows()))
    clusters: dict[str, list[dict[str, Any]]] = {}
    for state, label in used:
        name_ = full.get((state, label)) or folded.get((state, label.casefold())) or anywhere.get(label) or label
        group = clusters.setdefault(PARTY_ALIASES.get(label.upper(), label.upper()), [])
        for cluster in group:
            if similar(cluster["name"], name_):
                cluster["members"].append(state)
                break
        else:
            group.append({"name": name_, "label": label, "members": [state]})
    by_name: dict[str, set[str]] = {}
    for value, texts in known.items():
        for text_ in texts:
            by_name.setdefault(text_.casefold(), set()).add(value)
    ids: dict[tuple[str, str], UUID] = {}
    for key, group in clusters.items():
        for cluster in group:
            names = known.get(key, [])
            bare = all(text_.upper() == key for text_ in names)
            if names and (
                key in PARTY_ALIASES.values()
                or any(similar(text_, cluster["name"]) for text_ in names)
                or (bare and len(group) == 1)
            ):
                party = cs.key("de.party", key)
            elif len(by_name.get(cluster["name"].casefold(), ())) == 1:
                party = cs.key("de.party", next(iter(by_name[cluster["name"].casefold()])))
            else:
                minted_key = key if len(group) == 1 and not names else f"{key}|{cluster['name']}"
                by_name.setdefault(cluster["name"].casefold(), set()).add(minted_key)
                party = cs.key("de.party", minted_key)
                if party in cs.minted:
                    states = set(cluster["members"])
                    polity = germany if len(states) > 1 else cs.polity(f"DE-{next(iter(states))}")
                    cs.add("party", id=party, kind="party", polity_id=polity)
                    cs.add("name", entity_id=party, text=cluster["name"], lang="de", kind="official", is_preferred=True)
                    if cluster["label"] != cluster["name"]:
                        cs.add(
                            "name",
                            entity_id=party,
                            text=cluster["label"],
                            lang="de",
                            kind="abbreviation",
                            is_preferred=False,
                        )
            for state in cluster["members"]:
                for state_, label in used:
                    if (
                        state_ == state
                        and PARTY_ALIASES.get(label.upper(), label.upper()) == key
                        and (state, label) not in ids
                    ):
                        name_ = (
                            full.get((state, label))
                            or folded.get((state, label.casefold()))
                            or anywhere.get(label)
                            or label
                        )
                        if similar(name_, cluster["name"]) or name_ == cluster["name"]:
                            ids[state, label] = party
    missing = [pair for pair in used if pair not in ids]
    if missing:
        raise ValueError(f"unresolved parties: {missing}")
    return ids


def emit_history(
    cs: Changeset,
    conn: Any,
    germany: UUID,
    elections: pl.DataFrame,
    results: pl.DataFrame,
    glossary: pl.DataFrame,
) -> None:
    held = {(abbreviation, state["day"]) for abbreviation, state in STATES.items()}
    elections = elections.filter(
        ~pl.struct("state", "day").map_elements(
            lambda row: (row["state"], row["day"]) in held | HISTORY_SKIP, return_dtype=pl.Boolean
        )
        & (~pl.col("heading").str.contains("ehemaligen Land") | (pl.col("day") == date(1956, 3, 4)))
    ).sort("state", "day")
    results = results.join(elections.select("index", "state", "day"), on="index")
    party_ids = history_parties(cs, conn, germany, results, glossary)
    source = cs.source(HISTORY_PUBLISHER, HISTORY_TITLE)
    cs.add(
        "source",
        id=source,
        kind="official_body",
        publisher=HISTORY_PUBLISHER,
        title=HISTORY_TITLE,
        url=HISTORY,
        license="Vervielfältigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet",
        license_url=None,
    )
    cs.artifact(raw.cached(SOURCE, HISTORY), source)
    early_sources: dict[str, UUID] = {}
    for abbreviation, (publisher, title, url, license_) in EARLY_SOURCES.items():
        early_sources[abbreviation] = cs.source(publisher, title)
        if list(early_sources.values()).count(early_sources[abbreviation]) == 1:
            cs.add(
                "source",
                id=early_sources[abbreviation],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=license_,
                license_url=None,
            )
            cs.artifact(raw.cached(SOURCE, url), early_sources[abbreviation])
    second_from: dict[str, date] = {}
    for state, notes in elections.select("state", "notes").iter_rows():
        for note in notes:
            match = re.match(r"Ab der Wahl am (\d\d)\.(\d\d)\.(\d{4}) Zweitstimmen", note)
            if match:
                second_from[state] = date(int(match[3]), int(match[2]), int(match[1]))
    days: dict[str, list[date]] = {}
    for state, day in elections.select("state", "day").iter_rows():
        days.setdefault(state, []).append(day)
    for abbreviation, state in STATES.items():
        days.setdefault(abbreviation, []).append(state["day"])
    units: dict[str, UUID] = {}
    for abbreviation in sorted(days):
        code, english, body_names, native_stem = LAND_INFO[abbreviation]
        land = cs.polity(f"DE-{abbreviation}")
        land_area = cs.area("de.land", code)
        if {land, land_area} & cs.minted:
            raise ValueError(f"run de.bundeswahlleiterin first: {abbreviation} must exist")
        units[abbreviation] = cs.unit("de.land", code)
        cs.add(
            "reporting_unit",
            id=units[abbreviation],
            polity_id=land,
            scheme_code="de.land",
            code=code,
            area_relation="equals",
            area_id=land_area,
            kind="standard",
            valid_from=None,
            valid_to=None,
        )
        body = cs.key(f"{SOURCE}.body", f"landtag:{abbreviation}")
        if body_names is not None:
            cs.add("body", id=body, polity_id=land, kind="unicameral")
            cs.add("name", entity_id=body, text=body_names[0], lang="en", kind="official", is_preferred=True)
            cs.add("name", entity_id=body, text=body_names[1], lang="de", kind="native", is_preferred=False)
        ordered = sorted(days[abbreviation])
        rows = elections.filter(pl.col("state") == abbreviation).iter_rows(named=True)
        for row in rows:
            printed: date = row["day"]
            day = date(2023, 2, 12) if (abbreviation, printed) == ("BE", date(2021, 9, 26)) else printed
            following = ordered[ordered.index(printed) + 1] if ordered.index(printed) + 1 < len(ordered) else None
            if following == date(2021, 9, 26) and abbreviation == "BE":
                following = date(2023, 2, 12)
            cs.add("body_size", body_id=body, seats=row["seats"], valid_from=day, valid_to=following)
            same_year = [other for other in ordered if other.year == day.year]
            label = f"{day:%B} {day.year}" if len(same_year) > 1 else str(day.year)
            heading: str = row["heading"]
            if day == date(2023, 2, 12):
                english_name = "2023 Berlin repeat state election"
                native = "Wiederholungswahl zum 19. Abgeordnetenhaus von Berlin 2023"
            elif "Stadtverordnetenversammlung" in heading:
                english_name = f"{label} Berlin city assembly election"
                native = re.sub(r" am \S+$", f" {day.year}", heading)
            elif "verfassungsgebenden" in heading:
                english_name = f"{label} Baden-Württemberg constituent assembly election"
                native = f"Wahl zur Verfassunggebenden Landesversammlung Baden-Württemberg {day.year}"
            else:
                english_name = f"{label} {english} state election"
                native = f"{native_stem} {day.year}"
            election = cs.key(f"{SOURCE}.election", f"{abbreviation}:{day}")
            cs.add("election", id=election, polity_id=land, kind="general", date_from=day, date_to=day, status="held")
            cs.add("name", entity_id=election, text=english_name, lang="en", kind="official", is_preferred=True)
            cs.add("name", entity_id=election, text=native, lang="de", kind="native", is_preferred=False)
            path = f"de/{abbreviation.lower()}/{day}-general"
            cs.add("slug", path=path, entity_id=election)

            row_source = early_sources[abbreviation] if row["source"] is not None else source

            def make_set(
                key: str,
                contest: UUID | None,
                election: UUID = election,
                day: date = day,
                row_source: UUID = row_source,
            ) -> UUID:
                set_id = cs.key(f"{SOURCE}.result_set", f"{abbreviation}:{day}:{key}")
                cs.add(
                    "result_set",
                    id=set_id,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=row_source,
                    is_default=True,
                    completeness=1,
                )
                return set_id

            contest = cs.key(f"{SOURCE}.contest", f"{abbreviation}:{day}:list")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=land_area,
                magnitude=row["seats"],
                electoral_system_id=None,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{english_name}, seat allocation",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"{path}/landtag", entity_id=contest)
            multiple = row["cast"] is not None or row["valid_ballots"] is not None
            notes: list[str] = []
            if day == date(2023, 2, 12):
                notes.append(
                    "The Constitutional Court of Berlin declared the election of 26 September 2021 invalid, and it was "
                    "held again in full on 12 February 2023. These are the results of the repeat election."
                )
            if abbreviation == "BE" and day.year < 1990:
                notes.append("Only the western sectors of Berlin voted, so the figures cover West Berlin.")
            if abbreviation in second_from and day >= second_from[abbreviation]:
                notes.append(
                    "Each voter has two votes. The votes shown are second votes (Zweitstimmen), which decide the seats."
                )
            if row["cast"] is not None:
                notes.append(
                    "Each voter has two votes, and both count toward a party's seats, so the votes shown are first and "
                    f"second votes added together. The source counts {row['voters']:,} voters and {row['cast']:,} votes."
                )
            if row["valid_ballots"] is not None:
                notes.append(
                    "Each voter had five votes. The invalid figure counts ballot papers, and the valid figure counts "
                    f"votes; the source counts {row['valid_ballots']:,} valid ballot papers."
                )
            if (abbreviation, day) == ("HB", date(1946, 10, 13)):
                notes.append(
                    "The first Bürgerschaft was elected as a municipal election under conditions the source calls not "
                    "comparable. Each voter had three to five votes, and only valid ballot papers were counted, so "
                    "the numbers of voters and invalid votes are not known."
                )
            if "ehemaligen Land" in heading or (heading.startswith("Wahl in West-Berlin") and day.year == 1990):
                notes.append(f"The source heads this table \u201c{heading}\u201d; its figures are for the whole Land.")
            if (abbreviation, day) == ("HH", date(1993, 9, 19)):
                notes.append(
                    "The source prints the date of this election as 2 June 1991; its figures are those of 19 September 1993."
                )
            if abbreviation == "HH" and day.year in (1957, 1961):
                notes.append("The source prints 360 as the total of seats; the parties' seats add up to 120.")
            if row["extra"] is not None:
                notes.append(row["extra"])
            if row["relabeled"] is not None:
                notes.append(
                    f"From row {row['relabeled']} on, the source repeats party abbreviations from above and prints "
                    "seat numbers that do not add up. The parties and seats shown are those of the Bavarian "
                    "statistical office's report on the election (Beiträge zur Statistik Bayerns, Heft "
                    f"{163 if day.year == 1950 else 373})."
                )
            if row["folded"] is not None:
                notes.append(
                    f"From row {row['folded']} on, the source repeats party abbreviations from above and prints seat "
                    "numbers that do not add up, so those rows are shown together as other parties without seats."
                )
            if row["lost"]:
                notes.append(
                    f"The source also counts {row['lost']:,} lost votes (ausgefallene Stimmen), which are in neither "
                    "the valid nor the invalid total."
                )
            for note in row["notes"]:
                if note in HISTORY_NOTES:
                    notes.append(HISTORY_NOTES[note])
                elif not note.startswith(HISTORY_IGNORED):
                    raise ValueError(f"{abbreviation} {day}: untranslated note {note}")
            if notes:
                cs.add("note", entity_id=contest, kind="context", body_md=" ".join(notes))
            accounting = make_set("election", None)
            root = make_set("list", contest)
            unit = str(units[abbreviation])
            values: list[tuple[UUID, UUID | None, int, int | None]] = [
                (accounting, None, 2, row["registered"]),
                (accounting, None, 14 if multiple else 3, row["voters"]),
                (root, None, 5, row["invalid"]),
                (root, None, 4, row["valid"]),
            ]
            for party_row in results.filter(pl.col("index") == row["index"]).sort("position").iter_rows(named=True):
                label_ = party_row["label"]
                option = cs.key(f"{SOURCE}.option", f"{abbreviation}:{day}:list:{label_}")
                others = label_ in HISTORY_OTHERS
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others" if others else "list",
                    party_id=None if others else party_ids[abbreviation, label_],
                    ballot_label=label_,
                )
                values.append((root, option, 1, party_row["votes"]))
                if party_row["seats"]:
                    cs.add("outcome", result_set_id=root, option_id=option, kind="won", seats=party_row["seats"])
            cs.frame(
                "result_value",
                pl.DataFrame(
                    [
                        {
                            "result_set_id": str(set_id),
                            "unit_id": unit,
                            "option_id": None if option is None else str(option),
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "flags": 0,
                            "value": value,
                        }
                        for set_id, option, measure, value in values
                        if value is not None
                    ],
                    schema_overrides={"value": pl.Int64},
                ).with_columns(
                    channel=pl.col("channel").cast(pl.Int16),
                    measure=pl.col("measure").cast(pl.Int16),
                    count_round=pl.col("count_round").cast(pl.Int16),
                    flags=pl.col("flags").cast(pl.Int16),
                ),
            )
