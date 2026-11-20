"""Referendums on the boundaries of the German Länder under Articles 29 and 118 of the Basic Law.

The 1951 vote on the Südweststaat (Article 118), the 1956 Volksbegehren in Baden, the 1970 Volksentscheid in Baden,
the 1975 Volksentscheid in the Regierungsbezirk Montabaur, and the Berlin and Brandenburg votes of 5 May 1996.
Each vote is read from the report of the office that published it: the Statistisches Landesamt Baden-Württemberg
(the 1956 and 1970 reports and the 2011 monthly bulletin that prints the 1951 table), the Bundesminister des Innern's
announcement of the Montabaur result as the Bundestag printed it, and the Landesabstimmungsleiter's report for 1996.

Every row must add up (valid and invalid votes to the participants, the options to the valid votes), and the units must
add up to the total the report prints. The 1951 and 1996 tables are keyed in here from the printed pages (the 1996
page with the counts is a scan; its columns are proven by the ballot sums and by the unit-level invalid counts of the
text page that follows it).
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "de.neugliederung"
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
NATION_AREA = "01a13bb5-c55d-7311-a43d-bf016ca0f93c"
BW_AREA = "01a13bb5-c563-741b-83f7-4b7b14460236"
RP_AREA = "01a13bb5-c563-741b-83f7-4b794fcd0912"
BE_AREA = "01a13bb5-c563-741b-83f7-4b815208f13d"
BB_AREA = "01a13bb5-c563-741b-83f7-4b836b8e1aa9"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
SCHEME = "de.neugliederung"
SB = "https://www.statistischebibliothek.de/mir/servlets/MCRFileNodeServlet"
URL_1951 = f"{SB}/BWMonografie_derivate_00000993/Beitrag11_11_08.pdf"
URL_1956 = f"{SB}/BWHeft_derivate_00032086/4271_56001.pdf"
URL_1970 = f"{SB}/BWHeft_derivate_00032083/4271_70001.pdf"
URL_1975 = "https://dserver.bundestag.de/btd/07/037/0703751.pdf"
URL_1996 = f"{SB}/BBHeft_derivate_00021610/Heft4-Volksabstimmung1996.pdf"
LICENSE = "Public report of the issuing office; reuse with attribution"
BW = "Statistisches Landesamt Baden-Württemberg"
BBSTAT = "Amt für Statistik Berlin-Brandenburg"

# A unit's figures: eligible, participants (None when not printed), invalid (None when not printed), option votes.
Row = tuple[int, int | None, int | None, tuple[int, ...]]


@dataclass
class Question:
    slug: str
    title: str
    native: str
    text_de: str
    text_en: str
    area: str
    options: tuple[tuple[str, str, str], ...]  # code, ballot label, English name
    rows: dict[str, Row]
    total: Row
    winners: tuple[str, ...]
    note: str
    kind: str = "referendum"


@dataclass
class Vote:
    day: date
    slug: str
    title: str
    native: str
    url: str
    source_title: str
    publisher: str
    units: dict[str, str]  # unit code -> official name
    questions: list[Question] = field(default_factory=list)
    shared: str = ""
    election_kind: str = "referendum"


# ---------------------------------------------------------------------------------------------------------------
# 1951: the Südweststaat vote, by Abstimmungsbezirk (Statistisches Monatsheft Baden-Württemberg 11/2011, table T2).
# ---------------------------------------------------------------------------------------------------------------
ROWS_1951: dict[str, Row] = {
    "NW": (1655825, 834653, 11351, (769869, 53433)),
    "NB": (1004931, 677096, 7507, (382018, 287571)),
    "SB": (883502, 622530, 5830, (233255, 383445)),
    "SW": (778271, 406254, 8726, (363349, 34179)),
}
TOTAL_1951: Row = (4322529, 2540533, 33414, (1748491, 758628))

# 1956: the Volksbegehren in the former Freistaat Baden. Eligible voters (4 March 1956) and valid entries, by Kreis.
ROWS_1956: dict[str, tuple[int, int]] = {
    "NB-S-KA": (158489, 30127),
    "NB-S-HD": (88387, 3620),
    "NB-S-MA": (202984, 9653),
    "NB-S-PF": (49832, 929),
    "NB-L-BR": (71658, 13811),
    "NB-L-BU": (40511, 4945),
    "NB-L-HD": (86923, 6308),
    "NB-L-KA": (98886, 12805),
    "NB-L-MA": (98162, 3065),
    "NB-L-MOS": (40007, 1760),
    "NB-L-PF": (40336, 1220),
    "NB-L-SIN": (49969, 1336),
    "NB-L-TBB": (49748, 4048),
    "NB-SCH": (1100, 1),
    "SB-S-FR": (94403, 23197),
    "SB-S-BB": (28473, 4226),
    "SB-L-BUE": (49117, 20794),
    "SB-L-DON": (39327, 5798),
    "SB-L-EM": (60939, 13249),
    "SB-L-FR": (43303, 13135),
    "SB-L-KEH": (33277, 6158),
    "SB-L-KN": (95401, 16233),
    "SB-L-LAH": (49666, 10349),
    "SB-L-LOE": (80248, 11381),
    "SB-L-MUE": (32852, 6135),
    "SB-L-NEU": (25602, 7370),
    "SB-L-OFF": (60807, 19665),
    "SB-L-RA": (69554, 19832),
    "SB-L-SAE": (37244, 5304),
    "SB-L-STO": (28006, 5229),
    "SB-L-UEB": (33940, 6257),
    "SB-L-VS": (47309, 3846),
    "SB-L-WT": (38224, 8737),
    "SB-L-WOL": (31850, 9302),
}
NAMES_1956 = {
    "NB-S-KA": "Karlsruhe (Stadtkreis)",
    "NB-S-HD": "Heidelberg (Stadtkreis)",
    "NB-S-MA": "Mannheim (Stadtkreis)",
    "NB-S-PF": "Pforzheim (Stadtkreis)",
    "NB-L-BR": "Bruchsal",
    "NB-L-BU": "Buchen",
    "NB-L-HD": "Heidelberg (Landkreis)",
    "NB-L-KA": "Karlsruhe (Landkreis)",
    "NB-L-MA": "Mannheim (Landkreis)",
    "NB-L-MOS": "Mosbach",
    "NB-L-PF": "Pforzheim (Landkreis)",
    "NB-L-SIN": "Sinsheim",
    "NB-L-TBB": "Tauberbischofsheim",
    "NB-SCH": "Schluchtern",
    "SB-S-FR": "Freiburg im Breisgau (Stadtkreis)",
    "SB-S-BB": "Baden-Baden (Stadtkreis)",
    "SB-L-BUE": "Bühl",
    "SB-L-DON": "Donaueschingen",
    "SB-L-EM": "Emmendingen",
    "SB-L-FR": "Freiburg (Landkreis)",
    "SB-L-KEH": "Kehl",
    "SB-L-KN": "Konstanz",
    "SB-L-LAH": "Lahr",
    "SB-L-LOE": "Lörrach",
    "SB-L-MUE": "Müllheim",
    "SB-L-NEU": "Neustadt (Hochschwarzwald)",
    "SB-L-OFF": "Offenburg",
    "SB-L-RA": "Rastatt",
    "SB-L-SAE": "Säckingen",
    "SB-L-STO": "Stockach",
    "SB-L-UEB": "Überlingen",
    "SB-L-VS": "Villingen",
    "SB-L-WT": "Waldshut",
    "SB-L-WOL": "Wolfach",
}
TOTAL_1956 = (2056534, 309825)

# 1970: Volksentscheid in Baden, by Regierungsbezirk (eligible, participants, invalid, restore Baden, stay).
ROWS_1970: dict[str, Row] = {
    "NB": (1327466, 807074, 4033, (122914, 680127)),
    "SB": (1239180, 799428, 3991, (165899, 629538)),
}
TOTAL_1970: Row = (2566646, 1606502, 8024, (288813, 1309665))

# 1975: Volksentscheid in the former Regierungsbezirk Montabaur (Landesabstimmungsausschuss, 24 January 1975).
ROWS_1975: dict[str, Row] = {"MT": (205696, 95644, 424, (65774, 29446))}

# 1996: (eligible, participants, Ja, Nein) from the scanned table; invalid votes and the Zusatzfrage come from the
# text page. The postal-vote count each row also prints is not stored.
BERLIN_1996 = {
    "B01": ("Mitte", 59032, 32793, 14250, 18358),
    "B02": ("Tiergarten", 58337, 31333, 17643, 13483),
    "B03": ("Wedding", 98566, 50083, 26401, 23338),
    "B04": ("Prenzlauer Berg", 110598, 55618, 25390, 29904),
    "B05": ("Friedrichshain", 80132, 40724, 17627, 22895),
    "B06": ("Kreuzberg", 84421, 38811, 20045, 18486),
    "B07": ("Charlottenburg", 126786, 74576, 45471, 28716),
    "B08": ("Spandau", 160906, 98226, 58764, 38872),
    "B09": ("Wilmersdorf", 106815, 65694, 40745, 24584),
    "B10": ("Zehlendorf", 75471, 53537, 36353, 16957),
    "B11": ("Schöneberg", 99559, 56913, 33454, 23140),
    "B12": ("Steglitz", 145213, 95469, 59533, 35423),
    "B13": ("Tempelhof", 145790, 93335, 54397, 38440),
    "B14": ("Neukölln", 212185, 119819, 65707, 53418),
    "B15": ("Treptow", 85305, 50170, 24428, 25479),
    "B16": ("Köpenick", 87673, 51189, 26254, 24684),
    "B17": ("Lichtenberg", 125877, 69753, 30871, 38530),
    "B18": ("Weißensee", 44691, 24881, 12381, 12324),
    "B19": ("Pankow", 88051, 49356, 24069, 25020),
    "B20": ("Reinickendorf", 192337, 121031, 71072, 49287),
    "B21": ("Marzahn", 113855, 61758, 24179, 37271),
    "B22": ("Hohenschönhausen", 82042, 44190, 16268, 27723),
    "B23": ("Hellersdorf", 92082, 49009, 20300, 28508),
}
BRANDENBURG_1996 = {
    "K01": ("Brandenburg an der Havel", 67758, 42587, 15991, 26346),
    "K02": ("Cottbus", 93545, 59755, 22159, 37291),
    "K03": ("Frankfurt (Oder)", 61164, 39995, 12956, 26766),
    "K04": ("Potsdam", 104324, 79580, 27325, 51880),
    "K05": ("Barnim", 117833, 76804, 27993, 48279),
    "K06": ("Dahme-Spreewald", 112949, 82035, 29809, 51563),
    "K07": ("Elbe-Elster", 106418, 63552, 23595, 39394),
    "K08": ("Havelland", 102203, 68578, 26480, 41663),
    "K09": ("Märkisch-Oderland", 132017, 90430, 32853, 56930),
    "K10": ("Oberhavel", 133906, 91432, 34369, 56532),
    "K11": ("Oberspreewald-Lausitz", 120922, 74889, 27326, 46852),
    "K12": ("Oder-Spree", 146356, 98920, 35708, 62488),
    "K13": ("Ostprignitz-Ruppin", 87229, 56490, 20027, 36017),
    "K14": ("Potsdam-Mittelmark", 139262, 101507, 40097, 60521),
    "K15": ("Prignitz", 78585, 47086, 15916, 30827),
    "K16": ("Spree-Neiße", 117109, 72935, 24877, 47550),
    "K17": ("Teltow-Fläming", 114753, 80470, 31281, 48655),
    "K18": ("Uckermark", 121091, 72379, 26446, 45382),
}
# The Lfd. Nr. of each unit in the report's tables: Berlin 1-23 (total 24), Brandenburg 27-44 (total 45).
LFD_1996 = {code: n for n, code in enumerate(BERLIN_1996, 1)} | {code: n for n, code in enumerate(BRANDENBURG_1996, 27)}
TOTAL_1996 = {
    "BE": (2475724, 1428268, 765602, 654840, 7826, 557337, 442600, 428331),
    "BB": (1957424, 1299424, 475208, 814936, 9280, 343764, 368714, 586946),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for url in (URL_1951, URL_1956, URL_1970, URL_1975, URL_1996):
        raw.fetch(SOURCE, url, suffix=".pdf", check=_is_pdf)


def _is_pdf(path: object) -> None:
    from pathlib import Path

    if Path(str(path)).read_bytes()[:5] != b"%PDF-":
        raise ValueError(f"{path} is not a PDF")


def page_text(url: str, page: int) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


PAIR = re.compile(r"(\d{1,3}(?: \d{3})+|\d+)\s+\d+,\d+")


def read_1996() -> dict[str, tuple[int, int, int, int, int, int, int, int]]:
    """Per unit and for the two Länder: eligible, participants, Ja, Nein, invalid, then the Zusatzfrage's 1999, 2002,
    and invalid votes, checked against the ballots."""
    pairs: dict[int, list[int]] = {}
    for line in page_text(URL_1996, 11).splitlines():
        found = [int(m.replace(" ", "")) for m in PAIR.findall(line)]
        if len(found) >= 5:
            pairs[len(pairs) + 1] = found
    if len(pairs) != 46:
        raise ValueError(f"expected 46 rows on the text page of the 1996 report, found {len(pairs)}")
    out: dict[str, tuple[int, int, int, int, int, int, int, int]] = {}
    for table, number in ((BERLIN_1996, "BE"), (BRANDENBURG_1996, "BB")):
        for code, (_, eligible, cast, ja, nein) in table.items():
            invalid, z99, z02, zinv = pairs[LFD_1996[code]][:4]
            if ja + nein + invalid != cast or z99 + z02 + zinv != cast:
                raise ValueError(f"1996 {code}: the votes do not add up to the participants")
            out[code] = (eligible, cast, ja, nein, invalid, z99, z02, zinv)
        rows = [out[code] for code in table]
        total = pairs[24 if number == "BE" else 45][:4]
        want = TOTAL_1996[number]
        got = tuple(sum(row[i] for row in rows) for i in range(8))
        if got != want or (total[0], total[1], total[2], total[3]) != (want[4], want[5], want[6], want[7]):
            raise ValueError(f"1996 {number}: the units {got} differ from the printed total {want}")
    return out


def check(vote: Vote) -> None:
    for question in vote.questions:
        for code, (eligible, cast, invalid, votes) in question.rows.items():
            if cast is not None and invalid is not None and sum(votes) + invalid != cast:
                raise ValueError(f"{vote.day} {code}: votes and invalid votes differ from the participants")
            if cast is not None and cast > eligible:
                raise ValueError(f"{vote.day} {code}: more participants than eligible voters")
        rows = list(question.rows.values())
        if sum(r[0] for r in rows) != question.total[0]:
            raise ValueError(f"{vote.day} {question.slug}: the eligible voters of the units differ from the total")
        for label, column in (("participants", [r[1] for r in rows]), ("invalid votes", [r[2] for r in rows])):
            known = [value for value in column if value is not None]
            total = question.total[1] if label == "participants" else question.total[2]
            if len(known) == len(rows) and sum(known) != total:
                raise ValueError(f"{vote.day} {question.slug}: the {label} of the units differ from the total")
        for n in range(len(question.options)):
            if sum(r[3][n] for r in rows) != question.total[3][n]:
                raise ValueError(f"{vote.day} {question.slug}: option {n} of the units differs from the total")


def votes_list() -> list[Vote]:
    yes_no = (("yes", "Ja", "Yes"), ("no", "Nein", "No"))
    quorum_1996 = (
        "The treaty needed a majority of the participants that also made up at least a quarter of those entitled to "
        "vote in each Land; the Zusatzfrage counted only if it passed."
    )
    result = read_1996()
    out: list[Vote] = []

    v = Vote(
        date(1951, 12, 9),
        "suedweststaat",
        "Merger of Baden, Württemberg-Baden, and Württemberg-Hohenzollern (Südweststaat)",
        "Volksabstimmung über die Neugliederung des Südwestdeutschen Raumes",
        URL_1951,
        "Volksabstimmung über die Neugliederung des Südwestdeutschen Raumes am 9. Dezember 1951 (Statistisches Monatsheft Baden-Württemberg 11/2011, Tabelle T2)",
        BW,
        {
            "NW": "Nordwürttemberg (Landesbezirk Württemberg)",
            "NB": "Nordbaden (Landesbezirk Baden)",
            "SB": "Südbaden (Land Baden)",
            "SW": "Südwürttemberg-Hohenzollern (Land Württemberg-Hohenzollern)",
        },
    )
    v.questions.append(
        Question(
            "",
            "Merger of Baden, Württemberg-Baden, and Württemberg-Hohenzollern",
            "Vereinigung der Länder Baden, Württemberg-Baden und Württemberg-Hohenzollern zu einem Bundesland oder "
            "Wiederherstellung der alten Länder Baden und Württemberg",
            "Vereinigung der Länder Baden, Württemberg-Baden und Württemberg-Hohenzollern zu einem Bundesland oder "
            "Wiederherstellung der alten Länder Baden und Württemberg?",
            "Should the states of Baden, Württemberg-Baden, and Württemberg-Hohenzollern merge into one state, or "
            "should the old states of Baden and Württemberg be restored?",
            NATION_AREA,
            (("merge", "Südweststaat", "Merge into one state"), ("restore", "Alte Länder", "Restore the old states")),
            ROWS_1951,
            TOTAL_1951,
            ("merge",),
            "Four voting areas (Abstimmungsbezirke) voted. The merger needed a majority of the valid votes in the whole "
            "area and in at least three of the four; Nordwürttemberg, Nordbaden, and Südwürttemberg-Hohenzollern "
            "voted for it and Südbaden for the old states, so the merger carried (Article 118 of the Basic Law).",
        )
    )
    out.append(v)

    v = Vote(
        date(1956, 9, 3),
        "volksbegehren-baden",
        "Volksbegehren to restore the Land Baden",
        "Volksbegehren auf Wiederherstellung des Landes Baden",
        URL_1956,
        "Das Volksbegehren vom 3. - 16. September 1956: Ergebnisse in den Gemeinden des früheren Freistaates Baden (B III 3)",
        BW,
        NAMES_1956,
        election_kind="referendum",
    )
    v.questions.append(
        Question(
            "",
            "Restoration of the Land Baden",
            "Wiederherstellung des alten Landes Baden als selbständiges Bundesland",
            "Wiederherstellung des alten Landes Baden als selbständiges Bundesland",
            "Restoration of the old Land Baden as a state of its own",
            BW_AREA,
            (("signed", "Eintragung", "Entry in the petition"),),
            {code: (eligible, None, None, (signed,)) for code, (eligible, signed) in ROWS_1956.items()},
            (TOTAL_1956[0], None, None, (TOTAL_1956[1],)),
            ("signed",),
            "Entry period 3 to 16 September 1956 in the former Freistaat Baden, on the Federal Constitutional Court's "
            "order of 30 May 1956. The figures are the valid entries in the lists against the voters entitled to vote "
            "for the Landtag on 4 March 1956; 10 percent of them were needed. The 1951 comparison columns of the "
            "report are not stored.",
            kind="petition",
        )
    )
    out.append(v)

    v = Vote(
        date(1970, 6, 7),
        "baden",
        "Restoration of the Land Baden",
        "Volksentscheid im Gebietsteil Baden des Landes Baden-Württemberg",
        URL_1970,
        "Volksentscheid 1970 in Baden (Statistischer Bericht B III 4)",
        BW,
        {"NB": "Regierungsbezirk Nordbaden", "SB": "Regierungsbezirk Südbaden"},
    )
    v.questions.append(
        Question(
            "",
            "Restoration of the Land Baden",
            "Wiederherstellung des früheren Landes Baden oder Verbleib beim Land Baden-Württemberg",
            "Soll das frühere Land Baden als selbständiges Land wiederhergestellt werden oder soll das Gebiet des "
            "früheren Landes Baden beim Land Baden-Württemberg verbleiben?",
            "Should the former Land Baden be restored as a state of its own, or should the area stay with "
            "Baden-Württemberg?",
            BW_AREA,
            (
                ("restore", "Wiederherstellung Badens", "Restore Baden"),
                ("stay", "Verbleib bei Baden-Württemberg", "Stay"),
            ),
            ROWS_1970,
            TOTAL_1970,
            ("stay",),
            "Held under Article 29(3) of the Basic Law in the former Land Baden (the Regierungsbezirke Nordbaden and "
            "Südbaden, with the municipalities that took part in the 1956 Volksbegehren). Restoration needed a "
            "majority that was also at least a quarter of those entitled to vote; it did not come about.",
        )
    )
    out.append(v)

    v = Vote(
        date(1975, 1, 19),
        "montabaur",
        "Regierungsbezirk Montabaur: union with Hesse",
        "Volksentscheid im früheren Regierungsbezirk Montabaur",
        URL_1975,
        "Antrag des Ausschusses für Wahlprüfung, Immunität und Geschäftsordnung, Drucksache 7/3751 (Gesamtergebnis des "
        "Landesabstimmungsausschusses Montabaur)",
        "Deutscher Bundestag (announcement of the Bundesminister des Innern)",
        {"MT": "Regierungsbezirk Montabaur"},
    )
    v.questions.append(
        Question(
            "",
            "Montabaur: stay in Rhineland-Palatinate or join Hesse",
            "Verbleib beim Land Rheinland-Pfalz oder Angliederung an das Land Hessen",
            "Verbleib beim Land Rheinland-Pfalz oder Angliederung an das Land Hessen",
            "Stay with Rhineland-Palatinate or join Hesse",
            RP_AREA,
            (
                ("stay", "Verbleib bei Rheinland-Pfalz", "Stay in Rhineland-Palatinate"),
                ("join", "Angliederung an Hessen", "Join Hesse"),
            ),
            ROWS_1975,
            ROWS_1975["MT"],
            ("stay",),
            "Held under Article 29(3) of the Basic Law. Joining Hesse needed a majority that was also at least 51,424 "
            "votes (a quarter of those entitled to vote); the 29,446 votes for it (14.3 percent of those entitled) "
            "did not reach it. The Landesabstimmungsausschuss fixed the result on 24 January 1975.",
        )
    )
    out.append(v)

    v = Vote(
        date(1996, 5, 5),
        "berlin-brandenburg",
        "Merger of Berlin and Brandenburg",
        "Volksabstimmungen in den Ländern Berlin und Brandenburg über den Neugliederungs-Vertrag",
        URL_1996,
        "Volksabstimmungen in den Ländern Berlin und Brandenburg am 5. Mai 1996 über den Neugliederungs-Vertrag, "
        "endgültiges Ergebnis (Statistischer Bericht)",
        f"{BBSTAT}, Landesabstimmungsleiter Berlin and Brandenburg",
        {c: name for c, (name, *_) in (BERLIN_1996 | BRANDENBURG_1996).items()},
    )
    for land, label, table, area in (
        ("BE", "Berlin", BERLIN_1996, BE_AREA),
        ("BB", "Brandenburg", BRANDENBURG_1996, BB_AREA),
    ):
        win_q = "yes" if land == "BE" else "no"
        win_z = "1999" if land == "BE" else "2002"
        v.questions.append(
            Question(
                f"{label.lower()}",
                f"{label}: treaty on forming a joint Land",
                "Stimmen Sie dem Vertrag der Länder Berlin und Brandenburg über die Bildung eines gemeinsamen "
                "Bundeslandes zu?",
                "Stimmen Sie dem Vertrag der Länder Berlin und Brandenburg über die Bildung eines gemeinsamen "
                "Bundeslandes zu?",
                "Do you approve the treaty of the Länder Berlin and Brandenburg on forming a joint Land?",
                area,
                yes_no,
                {c: (e, cast, result[c][4], (result[c][2], result[c][3])) for c, (_, e, cast, _j, _n) in table.items()},
                (
                    TOTAL_1996[land][0],
                    TOTAL_1996[land][1],
                    TOTAL_1996[land][4],
                    (TOTAL_1996[land][2], TOTAL_1996[land][3]),
                ),
                (win_q,),
                quorum_1996,
            )
        )
        v.questions.append(
            Question(
                f"{label.lower()}-zusatz",
                f"{label}: year of the merger",
                "Soll das gemeinsame Land gebildet werden im Jahr 1999 oder im Jahr 2002?",
                "Soll das gemeinsame Land gebildet werden im Jahr 1999 oder im Jahr 2002?",
                "Should the joint Land be formed in 1999 or in 2002?",
                area,
                (("1999", "1999", "1999"), ("2002", "2002", "2002")),
                {c: (e, cast, result[c][7], (result[c][5], result[c][6])) for c, (_, e, cast, _j, _n) in table.items()},
                (
                    TOTAL_1996[land][0],
                    TOTAL_1996[land][1],
                    TOTAL_1996[land][7],
                    (TOTAL_1996[land][5], TOTAL_1996[land][6]),
                ),
                (win_z,),
                "The supplementary question was independent of the first. Ballots that left it unanswered or marked "
                "both years count as invalid for it.",
            )
        )
    out.append(v)
    for vote in out:
        check(vote)
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{vote.day}" for vote in votes_list())]


def parse() -> None:
    registry().write()
    for vote in votes_list():
        build(vote).write()


def emit() -> None:
    package.run("load", packages())


SYSTEM = key("electoral_system", "referendum")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system of referendums on the boundaries of the Länder: a question answered by the voters",
    )
    p.external.add(GERMANY)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=GERMANY,
        kind="administrative",
        rank=50,
        noun="count unit",
        code_system=None,
    )
    p.add("electoral_system", id=SYSTEM, polity_id=GERMANY, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "A question put to the voters of the areas concerned under Article 29 or Article 118 of the Basic Law. "
            "The law of each vote fixed the majority and the share of those entitled to vote an answer needed."
        ),
        lang="en",
    )
    return p


def build(vote: Vote) -> Package:
    day = vote.day.isoformat()
    source_id = key("source", day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day} {vote.title} ({'Volksbegehren' if vote.questions[0].kind == 'petition' else 'referendum'}) "
        "from the issuing office's report",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {GERMANY, *{q.area for q in vote.questions}}
    p.add(
        "source",
        id=source_id,
        kind="official_body",
        publisher=vote.publisher,
        title=vote.source_title,
        url=vote.url,
        license=LICENSE,
        license_url=None,
        attribution=vote.publisher,
    )
    p.artifact(raw.cached(SOURCE, vote.url), source_id)
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=GERMANY,
        kind="referendum",
        date_from=vote.day,
        date_to=vote.day,
        status="held",
    )
    kind_name = "petition" if vote.questions[0].kind == "petition" else "referendum"
    p.name(election, f"{vote.day.year} German {kind_name}: {vote.title}", "en", "official")
    p.name(election, vote.native, "de", "native", False)
    path = f"de/{day}-neugliederung-{vote.slug}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    units = {}
    for code, name in vote.units.items():
        unit = key("unit", day, code)
        units[code] = unit
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=GERMANY,
            scheme_code=SCHEME,
            code=f"{day}-{code}",
            area_relation="unlocated",
            kind="standard",
            valid_from=vote.day,
            valid_to=vote.day + timedelta(days=1),
        )
        p.name(unit, name, "de", "official")
    for number, question in enumerate(vote.questions, 1):
        suffix = f"/{question.slug}" if question.slug else "/question"
        contest = key("contest", day, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=question.area,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, f"{vote.day.year}: {question.title}", "en", "official")
        p.name(contest, question.native, "de", "native", False)
        p.add("slug", path=f"{path}{suffix}", entity_id=contest, is_current=True)
        for lang, body in (("de", question.text_de), ("en", question.text_en)):
            p.add(
                "ballot_question_text",
                id=key("ballot_question_text", contest, lang),
                contest_id=contest,
                kind="text",
                lang=lang,
                body=body,
                url=vote.url,
                source_id=source_id,
            )
        result_set = key("result_set", day, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=question.note,
            lang="en",
        )
        options = {}
        for position, (code, label, english) in enumerate(question.options, 1):
            options[code] = key("option", day, number, code)
            p.add(
                "contest_option",
                id=options[code],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(options[code], english, "en", "english")
        option_ids = [options[code] for code, _, _ in question.options]
        values: list[tuple[str, str | None, int, int]] = []
        for code, (eligible, cast, invalid, votes) in question.rows.items():
            unit = units[code]
            values.append((unit, None, REGISTERED, eligible))
            values += [(unit, option, VOTES, count) for option, count in zip(option_ids, votes, strict=True)]
            if cast is not None and invalid is not None:
                values += [(unit, None, CAST, cast), (unit, None, VALID, sum(votes)), (unit, None, INVALID, invalid)]
        p.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": [result_set] * len(values),
                    "unit_id": [v[0] for v in values],
                    "option_id": [v[1] for v in values],
                    "channel": [0] * len(values),
                    "measure": [v[2] for v in values],
                    "count_round": [0] * len(values),
                    "flags": [0] * len(values),
                    "value": [v[3] for v in values],
                },
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
            ),
        )
        for option, count in zip(option_ids, question.total[3], strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=count)
        for code, option in options.items():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if code in question.winners else "lost",
            )
    print(f"{p.key}: {len(vote.questions)} contest(s)", flush=True)
    return p
