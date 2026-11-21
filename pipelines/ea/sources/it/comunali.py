import csv
import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import package, raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.it import interno, regionali
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES, proper
from ea.sources.it.storico import EXISTING
from ea.store import DATA, connect

SOURCE = "it.comunali"
VERSION = "1"
KEY = f"{SOURCE}/all"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://dait.interno.gov.it/documenti/opendata/comunali"
DAYS = [
    date(1964, 11, 22),
    date(1970, 6, 7),
    date(1975, 6, 15),
    date(1980, 6, 8),
    date(1985, 5, 12),
    date(1989, 1, 1),
    date(1989, 1, 8),
    date(1989, 1, 15),
    date(1989, 1, 22),
    date(1989, 1, 29),
    date(1989, 3, 5),
    date(1989, 3, 12),
    date(1989, 3, 19),
    date(1989, 4, 9),
    date(1989, 4, 23),
    date(1989, 5, 7),
    date(1989, 5, 28),
    date(1989, 6, 11),
    date(1989, 7, 2),
    date(1989, 7, 6),
    date(1989, 7, 9),
    date(1989, 7, 16),
    date(1989, 7, 23),
    date(1989, 7, 30),
    date(1989, 9, 3),
    date(1989, 9, 17),
    date(1989, 9, 24),
    date(1989, 10, 8),
    date(1989, 10, 15),
    date(1989, 10, 22),
    date(1989, 10, 29),
    date(1989, 11, 5),
    date(1989, 11, 12),
    date(1989, 11, 26),
    date(1989, 12, 3),
    date(1989, 12, 10),
    date(1990, 5, 6),
    date(1991, 1, 13),
    date(1991, 1, 20),
    date(1991, 2, 24),
    date(1991, 3, 10),
    date(1991, 3, 17),
    date(1991, 3, 24),
    date(1991, 3, 31),
    date(1991, 4, 7),
    date(1991, 4, 21),
    date(1991, 5, 12),
    date(1991, 5, 19),
    date(1991, 6, 2),
    date(1991, 6, 9),
    date(1991, 6, 16),
    date(1991, 6, 23),
    date(1991, 6, 30),
    date(1991, 7, 7),
    date(1991, 7, 14),
    date(1991, 7, 28),
    date(1991, 9, 22),
    date(1991, 9, 29),
    date(1991, 11, 24),
    date(1991, 12, 12),
    date(1991, 12, 15),
    date(1992, 6, 7),
    date(1992, 6, 27),
    date(1992, 7, 5),
    date(1992, 9, 26),
    date(1992, 9, 27),
    date(1992, 12, 13),
    date(1993, 6, 6),
    date(1993, 11, 21),
    date(1994, 6, 12),
    date(1994, 11, 20),
    date(1995, 4, 23),
    date(1995, 5, 28),
    date(1995, 11, 19),
    date(1996, 6, 2),
    date(1996, 6, 9),
    date(1996, 11, 17),
    date(1996, 12, 1),
    date(1997, 4, 27),
    date(1997, 11, 16),
    date(1997, 11, 30),
    date(1998, 5, 24),
    date(1998, 11, 29),
    date(1999, 6, 13),
    date(2000, 4, 16),
    date(2000, 4, 30),
    date(2000, 6, 4),
    date(2000, 11, 12),
    date(2001, 5, 13),
    date(2002, 5, 26),
    date(2003, 5, 25),
    date(2003, 6, 8),
    date(2003, 6, 22),
    date(2004, 6, 12),
    date(2004, 6, 26),
    date(2005, 4, 3),
    date(2005, 4, 17),
    date(2005, 5, 8),
    date(2005, 11, 27),
    date(2006, 5, 28),
    date(2006, 6, 4),
    date(2006, 6, 11),
    date(2006, 6, 18),
    date(2006, 7, 9),
    date(2006, 7, 16),
    date(2006, 10, 1),
    date(2007, 5, 27),
    date(2007, 6, 10),
    date(2007, 6, 17),
    date(2007, 7, 15),
    date(2008, 4, 13),
    date(2008, 4, 27),
    date(2008, 6, 8),
    date(2008, 6, 15),
    date(2009, 6, 7),
    date(2009, 11, 29),
    date(2010, 3, 28),
    date(2010, 4, 11),
    date(2010, 4, 18),
    date(2010, 5, 30),
    date(2010, 11, 28),
    date(2011, 5, 15),
    date(2011, 5, 22),
    date(2011, 11, 27),
    date(2012, 5, 6),
    date(2012, 6, 10),
    date(2012, 6, 17),
    date(2012, 6, 24),
    date(2012, 10, 28),
    date(2013, 5, 26),
    date(2013, 11, 17),
    date(2014, 5, 25),
    date(2014, 10, 26),
    date(2015, 5, 31),
    date(2015, 11, 15),
    date(2016, 6, 5),
    date(2016, 11, 13),
    date(2017, 6, 11),
    date(2017, 11, 5),
    date(2018, 6, 10),
    date(2018, 7, 29),
    date(2018, 10, 21),
    date(2019, 5, 26),
    date(2019, 6, 16),
    date(2019, 6, 23),
    date(2019, 7, 7),
    date(2019, 7, 14),
    date(2019, 11, 10),
    date(2020, 9, 20),
    date(2020, 10, 25),
    date(2021, 10, 3),
    date(2021, 10, 10),
    date(2021, 11, 7),
    date(2022, 6, 12),
    date(2022, 11, 27),
    date(2023, 5, 14),
    date(2023, 5, 28),
    date(2023, 10, 22),
    date(2024, 6, 9),
    date(2024, 7, 28),
    date(2024, 11, 17),
    date(2025, 5, 25),
    date(2025, 6, 8),
    date(2025, 11, 23),
    date(2026, 5, 24),
    date(2026, 6, 7),
]


def url(day: date) -> str:
    return f"{ARCHIVE}/comunali-{day:%Y%m%d}.zip"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".zip")
    raw.fetch(interno.SOURCE, interno.ISTAT_CURRENT, suffix=".xlsx")
    raw.fetch(interno.SOURCE, interno.ISTAT_CHANGES, suffix=".pdf")


REGIONS = {
    "PIEMONTE": "01",
    "VALLEDAOSTA": "02",
    "LOMBARDIA": "03",
    "TRENTINOALTOADIGE": "04",
    "VENETO": "05",
    "FRIULIVENEZIAGIULIA": "06",
    "LIGURIA": "07",
    "EMILIAROMAGNA": "08",
    "TOSCANA": "09",
    "UMBRIA": "10",
    "MARCHE": "11",
    "LAZIO": "12",
    "ABRUZZO": "13",
    "MOLISE": "14",
    "CAMPANIA": "15",
    "PUGLIA": "16",
    "BASILICATA": "17",
    "CALABRIA": "18",
    "SICILIA": "19",
    "SARDEGNA": "20",
}
COLUMNS = {
    "REGIONE": ("REGIONE", "REG", "DESCRREGIONE"),
    "PROVINCIA": ("PROVINCIA", "PROV", "DESCRPROVINCIA"),
    "COMUNE": ("COMUNE", "DESCRCOMUNE", "COMUNESUT"),
    "SEZIONE": ("SEZIONE",),
    "round": ("TURNO",),
    "registered": ("ELETTORITOTALI", "ELETTORI", "ELETTORITOT"),
    "voters": ("NUMVOTANTITOTALI", "VOTANTI", "VOTANTITOTALI", "VOTANTITOT"),
    "blank": ("SKBIANCHE", "SCHEDEBIANCHE", "SCHEDE_BIANCHE"),
    "null": ("SCHEDE_NULLE",),
    "contested": ("SCHEDE_CONTESTATE", "SCHEDECONTESTATE"),
    "list": ("DESCRLISTA", "LISTA"),
    "list_votes": ("TOTVOTI", "VOTI", "VOTILISTA", "VOTI_LISTA"),
    "seats": ("SEGGILISTA", "SEGGI", "SEGGI_LISTA"),
    "surname": ("COGNOME",),
    "forename": ("NOME",),
    "birth": ("DATANASCITA", "DATA_NASCITA"),
    "candidate_votes": (
        "VOTI_CANDIDATO",
        "VOTICANDIDSINDACO",
        "VOTICAND",
        "VOTICANDLEADER",
        "VOTICANDIDATO",
        "VOTI_CANDIDDATO",
    ),
    "elected": ("ELETTO", "CODTIPOELETTO"),
}
ALIASES = {
    ("01", "CERRETO LANGHE"): "Cerretto Langhe",
    ("01", "ALAGNA V.SA"): "Alagna Valsesia",
    ("01", "ALBANO V.SE"): "Albano Vercellese",
    ("01", "QUINTO V.SE"): "Quinto Vercellese",
    ("01", "SALI"): "Sali Vercellese",
    ("01", "LUSERNA"): "Luserna San Giovanni",
    ("01", "CANNERO"): "Cannero Riviera",
    ("02", "ALLAIN"): "Allein",
    ("02", "ARNAZ"): "Arnad",
    ("02", "AYMAVILLE"): "Aymavilles",
    ("02", "CHALLANT SAINT ANSELME"): "Challand-Saint-Anselme",
    ("02", "DONNAZ"): "Donnas",
    ("02", "PONT BOZET"): "Pontboset",
    ("02", "VALGRISANCHE"): "Valgrisenche",
    ("02", "VALSAVARANCHE"): "Valsavarenche",
    ("02", "VALTOURNANCHE"): "Valtournenche",
    ("03", "GAVERINA"): "Gaverina Terme",
    ("03", "TORRE D' BUSI"): "Torre de' Busi",
    ("03", "CALVAGESE D/RIVIERA"): "Calvagese della Riviera",
    ("03", "LIMONE S/GARDA"): "Limone sul Garda",
    ("03", "PAVONE MELLA"): "Pavone del Mella",
    ("03", "POLPENAZZE"): "Polpenazze del Garda",
    ("03", "PUEGNAGO"): "Puegnago del Garda",
    ("03", "SAN GERVASIO B/NO"): "San Gervasio Bresciano",
    ("03", "SAVIORE D/ADAMELLO"): "Saviore dell'Adamello",
    ("03", "VALLIO"): "Vallio Terme",
    ("03", "VILLANUOVA S/CLISI"): "Villanuova sul Clisi",
    ("03", "MEGREGLIO"): "Magreglio",
    ("03", "OLTRONA DI S. MAMETTE"): "Oltrona di San Mamette",
    ("03", "SAN BARTOLOMEO V. C."): "San Bartolomeo Val Cavargna",
    ("03", "SAN NAZZARO V. C."): "San Nazzaro Val Cavargna",
    ("03", "CASTELNUOVO B. D'ADDA"): "Castelnuovo Bocca d'Adda",
    ("03", "S. BENEDETTO PO"): "San Benedetto Po",
    ("03", "S. GIACOMO DELLE SEGNATE"): "San Giacomo delle Segnate",
    ("03", "S. GIORGIO DI MANTOVA"): "San Giorgio di Mantova",
    ("03", "S. GIOVANNI DEL DOSSO"): "San Giovanni del Dosso",
    ("03", "S. MARTINO DALL'ARGINE"): "San Martino dall'Argine",
    ("03", "CALGAGNANO"): "Galgagnano",
    ("03", "VALEGGIO LOMELLINA"): "Valeggio",
    ("03", "ARSAGO"): "Arsago Seprio",
    (
        "04",
        "APPIAINO SULLA STRADA DEL VINO/EPPAN AN DER WEINSTRASSE",
    ): "Appiano sulla strada del vino/Eppan an der Weinstraße",
    ("04", "AVALENGO/HAFLING"): "Avelengo/Hafling",
    ("04", "MONGUELFO/WELSBERG"): "021052",
    ("04", "MONTAGNA"): "021053",
    ("04", "SALORNO"): "021076",
    ("04", "TRODENA/TRUDEN"): "021102",
    ("04", "GARNICA"): "Garniga",
    ("04", "ROVERE' BELLA LUNA"): "Roverè della Luna",
    ("04", "SANT'ORSOLA"): "Sant'Orsola Terme",
    ("05", "CALZIGNANO"): "Galzignano Terme",
    ("05", "GALZIGNANO"): "Galzignano Terme",
    ("05", "S. MARTINO DI V."): "San Martino di Venezze",
    ("05", "CONCORDIA SAGITARIA"): "Concordia Sagittaria",
    ("05", "CASTELNUOVO DI VERONA"): "Castelnuovo del Garda",
    ("05", "SALOGNA"): "Solagna",
    ("06", "ARTA"): "Arta Terme",
    ("07", "ALBISOLA MARINA"): "Albissola Marina",
    ("08", "SAN CASARIO SUL PANARO"): "San Cesario sul Panaro",
    ("08", "SALSOMAGGIORE T.ME"): "Salsomaggiore Terme",
    ("08", "S.ARCANGELO  R."): "Santarcangelo di Romagna",
    ("09", "GAMBASSI"): "Gambassi Terme",
    ("09", "GREVE"): "Greve in Chianti",
    ("12", "SANTI COSIMA E DAMIANO"): "Santi Cosma e Damiano",
    ("12", "GREGGIO"): "Greccio",
    ("12", "MONTE SAN GIOVANNI"): "Monte San Giovanni in Sabina",
    ("14", "S. POLO MATESE"): "San Polo Matese",
    ("14", "S.GIULIANO DEL SANNIO"): "San Giuliano del Sannio",
    ("14", "SESSANO"): "Sessano del Molise",
    ("15", "GUARDIA DEI LOMBARDI"): "Guardia Lombardi",
    ("15", "CASTELLO D'ALIFE"): "Castello del Matese",
    ("15", "PIEDIMONTE D'ALIFE"): "Piedimonte Matese",
    ("15", "TRENTOLA-DUCENTINA"): "Trentola Ducenta",
    ("15", "FORIO D'ISCHIA"): "Forio",
    ("15", "RESINA"): "Ercolano",
    ("18", "ISOLA CAPO RIZZUTO"): "Isola di Capo Rizzuto",
    ("18", "ROSITO CAPO SPULICO"): "Roseto Capo Spulico",
    ("19", "BOMPERSIERE"): "Bompensiere",
    ("19", "S. CATERINA VILLARMOSA"): "Santa Caterina Villarmosa",
    ("19", "GIARDINI"): "Giardini-Naxos",
    ("19", "MOJO ALACANTARA"): "Moio Alcantara",
    ("19", "SANTO STEFANO CAMASTRA"): "Santo Stefano di Camastra",
    ("19", "SCALETTA ZANGLEA"): "Scaletta Zanclea",
    ("19", "CALATIFIMI"): "Calatafimi",
    ("20", "SANT'ANTONIO RUINAS"): "Villa Sant'Antonio",
    ("20", "GONNASFANADIGA"): "Gonnosfanadiga",
    ("20", "SAN FRANCESCO D'AGLIENTU"): "Aglientu",
}
IN_PROVINCE = {("BERGAMO", "CORTENOVA"): "Cortenuova"}
UNPLACED = {
    ("01", "VENALZIO"),
    ("03", "RIVIERA D'ADDA"),
    ("03", "CERNUSCO MONTEVECCHIA"),
    ("03", "CARONNO"),
    ("03", "OSMATE LENTATE"),
    ("03", "POZZUOLO MILANESE"),
    ("05", "VILLAFRANCA PADOVANA (GRANDE)"),
    ("05", "VILLAFRANCA PADOVANA (PICCOLO)"),
    ("05", "TRETTO"),
    ("07", "SAN BARTOLOMEO DEL CERVO"),
    ("18", "SAMBIASE"),
    ("18", "SANT'EUFEMIA LAMEZIA"),
}
REGION_POLITIES = {
    **{code: info[:3] for code, info in regionali.REGION_INFO.items()},
    "02": ("IT-23", "valle-d-aosta", "Valle d'Aosta"),
    "04": ("IT-32", "trentino-alto-adige", "Trentino-Alto Adige"),
    "06": ("IT-36", "friuli-venezia-giulia", "Friuli-Venezia Giulia"),
    "20": ("IT-88", "sardegna", "Sardegna"),
}
REGION_NAMES = {
    "IT-32": ("Trentino-Alto Adige/Südtirol", "Trentino-South Tyrol"),
    "IT-36": ("Friuli-Venezia Giulia", "Friuli-Venezia Giulia"),
    "IT-88": ("Sardegna", "Sardinia"),
}
MONTHS = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]
MAYOR = {"S", "SI"}
LARGEST_COUNCIL = 80
RUNOFF_GAP = timedelta(days=14)
COUNTED = (
    "Results are by municipality, from the Interior Ministry's open data file for this round of municipal elections; "
    "where the file reports polling sections, they are summed by municipality."
)
COUNCIL_SEATS = (
    "Seats are the council seats the open data gives each list. They do not include the mayor, or defeated candidates "
    "for mayor who took a seat on the council."
)
NO_SEATS = "Seats are not shown: the open data gives no seats to these lists, or more seats than any council has."
TURNOUT = "As published, the voters outnumber the registered electors."
OVER = "As published, the votes here add up to more than the number of voters."
MAYOR_FLAGS = "The winner is the candidate the open data marks as elected mayor."
NO_MAYOR = "No winner is shown: the open data does not mark exactly one candidate as elected mayor."
RUNOFF = (
    "No candidate won an absolute majority, so the top two went to a runoff on the second Sunday after "
    "(TUEL art. 72); the runoff date is that rule's, as the open data gives only the first round's date."
)
# The 9 June 2024 file as the ministry republished it in November 2026 prints #NOME? (a spreadsheet's error for text
# that starts with "+") for these lists, one per municipality; the file it replaced printed their labels.
MANGLED = "#NOME?"
LABELS = {
    (date(2024, 6, 9), "001078"): "+EUROPA",
    (date(2024, 6, 9), "024008"): "+FUTURO",
    (date(2024, 6, 9), "036022"): "+MIRANDOLA",
    (date(2024, 6, 9), "038008"): "+FERRARA IN EUROPA - PSI",
    (date(2024, 6, 9), "048017"): "+EUROPA",
    (date(2024, 6, 9), "061034"): "+VERDE CUORE AMBIENTALISTA",
    (date(2024, 6, 9), "063083"): "+EUROPA",
    (date(2024, 6, 9), "065079"): "+ RETE",
    (date(2024, 6, 9), "100005"): "+EUROPA-PARTITO REPUBBLICANO ITALIANO",
}
REGISTER = "https://dait.interno.gov.it/documenti/storico_amministratori_comuni31122024.csv"
NETTUNO = (
    "https://servizionline.comune.nettuno.roma.it/cmsnettuno/portale/delibere/deliberericerca.aspx?P=700"
    "#consiglio-2024-1/VERBALE_01_ODG.pdf"
)
# Council seats per list where the open data gives none, from the act that declares them. Nettuno's runoff file
# prints seats that overfill the council for every opposition list, so they are not read; Cosoleto and Sparanise
# had no runoff, and the first-round file has no seat column.
DECLARED: dict[tuple[date, str], tuple[str, dict[str, int]]] = {
    (date(2024, 11, 17), "058072"): (
        NETTUNO,
        {
            "PATTO PER NETTUNO": 1,
            "ANTONIO TAURELLI SINDACO": 1,
            "BURRINI SINDACO": 6,
            "LEALI A NETTUNO": 1,
            "PARTITO DEMOCRATICO": 8,
            "MOVIMENTO 5 STELLE": 0,
            "NOI MODERATI": 0,
            "FORZA ITALIA - PPE": 1,
            "FRATELLI D'ITALIA": 2,
            "LEGA SALVINI PREMIER": 0,
            "DIALOGO": 0,
            "NETTUNO PROGETTO COMUNE": 0,
            "SIMONA SANETTI SINDACO DI NETTUNO": 1,
        },
    ),
    (date(2024, 11, 17), "080030"): (REGISTER, {"PUNTO E A CAPO": 10}),
    (date(2024, 11, 17), "061089"): (REGISTER, {"CAMBIAMO SPARANISE": 8, "SPARANISE IN MOVIMENTO": 3}),
}
NETTUNO_SEATS = (
    "Seats are the council seats the central electoral office proclaimed for each list, as read into the minutes of "
    "the council's first sitting (resolution no. 1 of 21 December 2024, page 1); the open data's runoff file gives "
    "more seats than the council has. They do not include the mayor, or the three defeated candidates for mayor who "
    "took a seat on the council."
)
REGISTER_SEATS = (
    "Seats are the councillors of each list in the Interior Ministry's register of local administrators in office on "
    "31 December 2024, all in office since the election; the open data gives no seats for this election. They do not "
    "include the mayor, or defeated candidates for mayor who took a seat on the council."
)
CC_LICENSE = (
    "No license stated. Official acts of public administrations are excluded from copyright (art. 5 of Law 633/1941)."
)
COUNTS = ("registered", "voters", "blank", "null", "contested")
NUMBERS = (*COUNTS, "list_votes", "seats", "candidate_votes")
SKIPPED = re.compile(r"(?i)legenda|preferenze|votanti|livsez|_sez\.csv|candidati_sindaco")
HEADLESS = {
    date(2017, 11, 5): [
        "REGIONE",
        "PROVINCIA",
        "COMUNE",
        "ELETTORI",
        "VOTANTI",
        "SCHEDE_BIANCHE",
        "LISTA",
        "VOTI_LISTA",
        "SEGGI_LISTA",
        "COGNOME",
        "NOME",
        "DATANASCITA",
        "LUOGONASCITA",
        "SESSO",
        "ELETTO",
        "VOTI_CANDIDATO",
    ]
}


def decoded(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def birth(text: str) -> date | None:
    text = text.strip().split(" ")[0]
    if not text:
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"unreadable birth date {text!r}")


def region_code(text: str) -> str:
    folded = regionali.folded(text)
    found = {code for label, code in REGIONS.items() if label[:5] == folded[:5]}
    if len(found) != 1:
        raise ValueError(f"unknown region {text!r}")
    return found.pop()


def read(
    day: date,
    archive: zipfile.ZipFile,
    member: str,
    columns: dict[str, tuple[str, ...]] = COLUMNS,
    numbers: tuple[str, ...] = NUMBERS,
) -> pl.DataFrame:
    data = archive.read(member)
    if member.lower().endswith(".xlsx"):
        frame = pl.read_excel(io.BytesIO(data), infer_schema_length=0)
    else:
        text = decoded(data)
        header = HEADLESS.get(day)
        frame = pl.read_csv(
            io.StringIO(text),
            separator=";",
            infer_schema_length=0,
            quote_char='"',
            has_header=header is None,
            new_columns=header,
        )
    frame = frame.rename({column: column.strip().upper() for column in frame.columns})
    renamed: dict[str, str] = {}
    for canonical, aliases in columns.items():
        for alias in aliases:
            if alias in frame.columns:
                renamed[alias] = canonical
                break
    frame = frame.select(list(renamed)).rename(renamed)
    if "round" not in frame.columns:
        frame = frame.with_columns(round=pl.lit("2" if "secondo" in member.lower() else "1"))
    frame = frame.with_columns(
        [pl.col(c).str.strip_chars().fill_null("") for c in frame.columns if c not in numbers]
        + [
            pl.col(c).str.strip_chars().str.replace(r"[.,]0+$", "").replace("", None).cast(pl.Int64, strict=True)
            for c in frame.columns
            if c in numbers
        ]
    )
    if "SEZIONE" in frame.columns and (frame["SEZIONE"] == "").all():
        frame = frame.drop("SEZIONE")
    return frame.with_columns(
        day=pl.lit(day),
        round=pl.col("round").cast(pl.Int8),
        region=pl.col("REGIONE").map_elements(region_code, return_dtype=pl.String),
    )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames, unplaced = collect(DAYS)
    unplaced.write_parquet(PARSED / "unplaced.parquet")
    for stem, frame in frames.items():
        frame.write_parquet(PARSED / f"{stem}.parquet")
    build()


def emit() -> None:
    package.run("load", [KEY])


def collect(days: list[date]) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """The municipal accounting, list, and candidate rows of the given elections, each placed by ISTAT code, and the
    places no code was found for."""
    accounting: list[pl.DataFrame] = []
    lists: list[pl.DataFrame] = []
    candidates: list[pl.DataFrame] = []
    for day in days:
        archive = zipfile.ZipFile(raw.cached(SOURCE, url(day)).path)
        members = [m for m in archive.namelist() if not m.endswith("/") and not SKIPPED.search(m)]
        for member in members:
            frame = read(day, archive, member)
            place = ["day", "region", "PROVINCIA", "COMUNE", "round"]
            sections = ["SEZIONE"] if "SEZIONE" in frame.columns else []
            if "registered" in frame.columns:
                counts = [c for c in COUNTS if c in frame.columns]
                rows = frame.select(*place, *sections, *counts).unique()
                if rows.select(*place, *sections).is_duplicated().any():
                    raise ValueError(f"{day} {member}: accounting differs within a place")
                accounting.append(rows.group_by(place).agg([pl.col(c).sum() for c in counts]))
            if "list" in frame.columns:
                leader = ["surname", "forename"] if "surname" in frame.columns else []
                rows = frame.filter(pl.col("list") != "", pl.col("round") == 1).with_columns(
                    pl.col(c).alias(f"leader_{c}") for c in leader
                )
                if leader and not sections and "candidate_votes" in frame.columns:
                    leader.append("candidate_votes")
                    rows = rows.with_columns(leader_candidate_votes="candidate_votes")
                rows = rows.with_columns(
                    nth=pl.int_range(pl.len()).over(*place, *sections, *[f"leader_{c}" for c in leader], "list")
                )
                lists.append(
                    rows.group_by(*place, *[f"leader_{c}" for c in leader], "list", "nth").agg(
                        pl.col("list_votes").sum(), *([pl.col("seats").max()] if "seats" in rows.columns else [])
                    )
                )
            if "surname" in frame.columns and "candidate_votes" in frame.columns:
                extra = [c for c in ("birth", "elected") if c in frame.columns]
                rows = (
                    frame.filter(pl.col("surname") != "")
                    .select(*place, *sections, "surname", "forename", *extra, "candidate_votes")
                    .unique()
                )
                if sections:
                    if rows.select(*place, *sections, "surname", "forename").is_duplicated().any():
                        raise ValueError(f"{day} {member}: candidate votes differ within a place")
                    rows = rows.group_by(*place, "surname", "forename").agg(
                        pl.col("candidate_votes").sum(), *[pl.col(c).max() for c in extra]
                    )
                candidates.append(rows)
    frames, unplaced = locate(
        {
            "accounting": pl.concat(accounting, how="diagonal_relaxed"),
            "lists": pl.concat(lists, how="diagonal_relaxed"),
            "candidates": pl.concat(candidates, how="diagonal_relaxed"),
        }
    )
    mangled = frames["lists"].filter(pl.col("list") == MANGLED)
    if mangled.height and sorted(mangled.select("day", "code").iter_rows()) != sorted(LABELS):
        raise ValueError(f"lists printed as {MANGLED} differ from the labels known: {mangled.rows()}")
    if mangled.height:
        labels = pl.DataFrame(
            [(day, code, label) for (day, code), label in LABELS.items()],
            schema={"day": pl.Date, "code": pl.String, "label": pl.String},
            orient="row",
        )
        frames["lists"] = (
            frames["lists"]
            .join(labels, on=["day", "code"], how="left", maintain_order="left")
            .with_columns(list=pl.when(pl.col("list") == MANGLED).then(pl.col("label")).otherwise(pl.col("list")))
            .drop("label")
        )
    return frames, unplaced


def locate(frames: dict[str, pl.DataFrame]) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Each frame's rows with the ISTAT code of their municipality, and the places no code was found for."""
    resolve, _, _ = regionali.municipalities()

    def code_of(region: str, province: str, label: str, day: date) -> str | None:
        if (region, label) in UNPLACED:
            return None
        alias = IN_PROVINCE.get((province, label)) or ALIASES.get((region, label))
        if alias is not None and alias.isdigit():
            return alias
        return resolve(region, alias or label, day)

    place = ["day", "region", "PROVINCIA", "COMUNE"]
    located = (
        pl.concat([frame.select(place) for frame in frames.values()])
        .unique()
        .with_columns(
            code=pl.struct(place).map_elements(
                lambda row: code_of(row["region"], row["PROVINCIA"], row["COMUNE"], row["day"]),
                return_dtype=pl.String,
            )
        )
    )
    clashes = located.filter(pl.col("code").is_not_null(), pl.len().over("day", "code") > 1)
    if clashes.height:
        raise ValueError(f"municipalities named twice in one election: {clashes.sort('code').rows()}")
    placed = {
        stem: frame.join(located, on=place, how="left").filter(pl.col("code").is_not_null())
        for stem, frame in frames.items()
    }
    return placed, located.filter(pl.col("code").is_null())


def label_of(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def retrieved(conn: Connection) -> Callable[[raw.Artifact], raw.Artifact]:
    """A file the cache holds with the same bytes and address as a file the archive already cites keeps the
    retrieval time the archive recorded for it, so a fresh copy of the cache rebuilds the same citation."""
    stored: dict[tuple[str, str], datetime] = {
        (str(url), str(digest)): at
        for url, digest, at in conn.execute(
            """select a.url, a.sha256, a.retrieved_at from source_artifact a
               join changeset c on c.id = a.changeset_id where c.pipeline = %s""",
            [SOURCE],
        )
    }

    def pin(artifact: raw.Artifact) -> raw.Artifact:
        at = stored.get((artifact.url, artifact.sha256))
        return artifact if at is None else replace(artifact, retrieved_at=at)

    return pin


def registered(day: date, code: str, defeated: set[tuple[str, str]]) -> Counter[str]:
    """The councillors of each list in the ministry's register of administrators in office on 31 December 2024 for
    the council elected on the day, leaving out defeated candidates for mayor, who sit by that right and not on a
    list's seat. Every councillor must have taken office within a week of the election, so none is a substitute."""
    with raw.cached(SOURCE, REGISTER).path.open(encoding="utf-8", newline="") as file:
        rows = [
            row
            for row in csv.DictReader(file, delimiter=";")
            if row["ISTAT_CODICE_COMUNE"] == code
            and row["DATA_ELEZIONE"] == f"{day:%d/%m/%Y}"
            and row["DESCRIZIONE_CARICA"] == "Consigliere"
        ]
    late = [
        row["COGNOME"]
        for row in rows
        if datetime.strptime(row["DATA_INIZIO_CARICA"], "%d/%m/%Y").date() > day + timedelta(days=7)
    ]
    if not rows or late:
        raise ValueError(f"{code} {day}: the register lists no councillors, or substitutes ({late})")
    return Counter(
        row["LISTA_APPARTENENZA/COLLEGAMENTO"] for row in rows if (row["COGNOME"], row["NOME"]) not in defeated
    )


def build() -> Path:
    """Write the package it.comunali/all from the parsed tables. It reads the database, through Draft, so the rows
    it rebuilds keep the ids the changeset path gave them."""
    days = DAYS
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    current = {code: (row[0], row[5]) for code, row in interno.comuni().items()}
    _, extinct, _ = regionali.municipalities()
    ended = {code: (region, label, until) for code, label, region, until in extinct.iter_rows()}
    held = (
        pl.concat([frame.select("day", "code", "region") for frame in (lists, candidates)])
        .unique(["day", "code"])
        .sort("day", "code")
    )
    with connect() as conn:
        pin = retrieved(conn)
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Italian municipal councils and mayors elected from 1964 to 2026, from the Interior Ministry's open "
                "data, by municipality"
            ),
            polity="IT",
        )
        cs.accept(
            "accounting.turnout",
            "The open data prints more voters than registered electors for some municipalities; both are stored as "
            "printed, and the result set's note says so",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        # it.eligendo and it.eligendo_referendum hold sources under the same publisher and title, so the source is
        # it.interno's by its pipeline.
        found = conn.execute(
            """select s.id from source s join changeset c on c.id = s.changeset_id
               where c.pipeline = %s and s.publisher = %s and s.title = %s""",
            [interno.SOURCE, "Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data"],
        ).fetchone()
        if found is None:
            raise ValueError("publish it.interno before it.comunali")
        ministry: UUID = found[0]
        for day in days:
            cs.artifact(pin(raw.cached(SOURCE, url(day))), ministry)
        italy = cs.polity("IT")
        # A municipality's slug takes its code when another entity holds the plain path. The module's own
        # municipalities keep the slugs they hold, so a rebuild does not count a municipality's slug against itself.
        taken: set[str] = set()
        held_paths: dict[UUID, str] = {}
        for path, entity, in_use in conn.execute(
            "select path, entity_id, is_current from slug where path like 'it/%%'"
        ):
            taken.add(path)
            if in_use and entity in cs.own_entities:
                held_paths[entity] = path

        def declared_source(where: str) -> UUID:
            if where == NETTUNO:
                publisher, title, home, license = (
                    "Comune di Nettuno",
                    "Deliberazione del Consiglio comunale n. 1 del 21 dicembre 2024, con il verbale della seduta",
                    NETTUNO.split("#")[0],
                    CC_LICENSE,
                )
            else:
                publisher, title, home, license = (
                    "Ministero dell'Interno",
                    "Anagrafe degli amministratori locali: amministratori dei comuni in carica al 31 dicembre 2024",
                    REGISTER,
                    interno.INTERNO_LICENSE,
                )
            source = cs.source(publisher, title)
            if source in cs.minted or source in cs.own_entities:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=home,
                    license=license,
                )
            cs.artifact(pin(raw.cached(SOURCE, where)), source)
            return source

        istat: list[UUID] = []

        def istat_source() -> UUID:
            """it.interno's source for ISTAT's list of municipalities, citing the current spreadsheet and the note of
            changes that dates the municipalities created since the CSV list."""
            if not istat:
                found = conn.execute(
                    """select s.id from source s join changeset c on c.id = s.changeset_id
                       where c.pipeline = %s and s.publisher = %s and s.title = %s""",
                    [interno.SOURCE, "ISTAT", "Elenco dei comuni italiani"],
                ).fetchone()
                if found is None:
                    raise ValueError("publish it.interno before it.comunali")
                istat.append(found[0])
                for where in (interno.ISTAT_CURRENT, interno.ISTAT_CHANGES):
                    cs.artifact(pin(raw.cached(interno.SOURCE, where)), found[0])
            return istat[0]

        regions: dict[str, tuple[UUID, str]] = {}

        def region_of(code: str) -> tuple[UUID, str]:
            if code not in regions:
                iso, path, native = REGION_POLITIES[code]
                polity = cs.polity(iso)
                if polity in cs.minted or polity in cs.own_entities:
                    native, english = REGION_NAMES[iso]
                    cs.add("polity", id=polity, kind="region", iso_code=iso)
                    cs.add("slug", path=f"it/{path}", entity_id=polity)
                    cs.add("polity_parent", polity_id=polity, parent_id=italy)
                    cs.add("polity_territory", polity_id=polity, area_id=cs.area("it.regione", code))
                    name(polity, native, "it")
                    if english != native:
                        name(polity, english, "en", "english")
                regions[code] = (polity, f"it/{path}")
            return regions[code]

        towns: dict[str, tuple[UUID, str, str, UUID, UUID]] = {}
        offices: dict[str, UUID] = {}

        def town_of(code: str) -> tuple[UUID, str, str, UUID, UUID]:
            if code in towns:
                return towns[code]
            region, label, until = ended.get(code) or (*current[code], None)
            since = interno.CREATED.get(code)
            area = cs.area("it.comune", code)
            unit = cs.unit("it.comune", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="it.comune")
                cs.add("area_code", area_id=area, code=code, valid_from=since, valid_to=until)
                cs.add("area_parent", area_id=area, parent_id=cs.area("it.regione", region), valid_from=since)
                name(area, label, "it")
                if since is not None:
                    cs.add(
                        "citation",
                        entity_id=area,
                        source_id=istat_source(),
                        locator={"url": interno.ISTAT_CURRENT, "Codice Comune formato alfanumerico": code},
                    )
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=italy,
                    scheme_code="it.comune",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=since,
                    valid_to=until,
                )
                name(unit, label, "it")
            polity = cs.key(f"{SOURCE}.polity", code)
            if polity in cs.minted:
                parent, prefix = region_of(region)
                path = held_paths.get(polity) or f"{prefix}/{slug(label)}"
                if polity not in held_paths:
                    if path in taken:
                        path = f"{path}-{code}"
                    taken.add(path)
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, label, "it")
                cs.add("polity_parent", polity_id=polity, parent_id=parent)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add("slug", path=path, entity_id=polity)
            else:
                (path,) = conn.execute(
                    "select path from slug where entity_id = %s order by length(path) limit 1", [polity]
                ).fetchone() or (f"it/{code}",)
            body = cs.key(f"{SOURCE}.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="unicameral")
                name(body, f"{label} Municipal Council")
                name(body, f"Consiglio comunale di {label}", "it", "native")
            towns[code] = (polity, path, label, body, area)
            return towns[code]

        def office_of(code: str) -> UUID:
            if code not in offices:
                polity, _, label, _, _ = town_of(code)
                offices[code] = cs.key(f"{SOURCE}.office", code)
                if offices[code] in cs.minted:
                    cs.add("office", id=offices[code], polity_id=polity, kind="mayor")
                    name(offices[code], f"Mayor of {label}")
                    name(offices[code], f"Sindaco di {label}", "it", "native")
            return offices[code]

        parties: dict[tuple[str, str], UUID] = {}

        def party_of(label: str, day: date) -> UUID | None:
            spec = ("it.party", "forza-italia") if label == "FORZA ITALIA" and day.year >= 2014 else EXISTING.get(label)
            if spec is None:
                return None
            if spec not in parties:
                parties[spec] = cs.key(*spec)
                if parties[spec] in cs.minted:
                    raise ValueError(f"{spec} is not in the database")
            return parties[spec]

        persons: dict[str, UUID] = {}
        # One person can stand in files that give a birth date and files that don't. A person the archive holds
        # keeps the birth date it was published with; a new one takes the first date a file gives. A person whose
        # row another pipeline has since rewritten, as the crosswalk does for the people it merges, keeps that row.
        births: dict[UUID, date | None] = {}
        published: dict[UUID, date | None] = {}
        elsewhere: set[UUID] = set()
        for id, born, ours in conn.execute(
            """select p.id, p.birth_date, c.pipeline = %s from person p join changeset c on c.id = p.changeset_id
               join entity e on e.id = p.id join changeset m on m.id = e.changeset_id where m.pipeline = %s""",
            [SOURCE, SOURCE],
        ):
            if ours:
                published[id] = born
            else:
                elsewhere.add(id)

        def person_of(key: str, given: str, family: str, born: date | None) -> UUID:
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                id = persons[key]
                if id in cs.minted:
                    births[id] = published[id] if id in published else births.get(id) or born
                    name(id, f"{given} {family}".strip(), "it")
            return persons[key]

        def result_set(key: str, election: UUID, contest: UUID | None, notes: list[str]) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=ministry,
                is_default=True,
                completeness=1,
            )
            if notes:
                cs.add("note", entity_id=id, kind="caveat", body_md=" ".join(notes))
            return id

        values: list[dict[str, object]] = []

        def value(result: UUID, code: str, option: UUID | None, measure: int, count: int | None) -> None:
            if count is not None:
                values.append(
                    {
                        "result_set_id": str(result),
                        "unit_id": str(cs.unit("it.comune", code)),
                        "option_id": None if option is None else str(option),
                        "measure": measure,
                        "value": count,
                    }
                )

        by_place = {key: frame for key, frame in accounting.partition_by("day", "code", "round", as_dict=True).items()}
        lists_by = lists.partition_by("day", "code", as_dict=True)
        running_by = candidates.partition_by("day", "code", as_dict=True)
        for day in days:
            umbrella = cs.key(f"{SOURCE}.election", str(day))
            places = held.filter(pl.col("day") == day)
            runoffs = candidates.filter(pl.col("day") == day, pl.col("round") == 2)
            last = day + RUNOFF_GAP if runoffs.height else day
            cs.add("election", id=umbrella, polity_id=italy, kind="local", date_from=day, date_to=last, status="held")
            name(umbrella, f"{day:%B} {day.day}, {day.year} Italian municipal elections")
            name(umbrella, f"Elezioni comunali del {label_of(day)}", "it", "native")
            cs.add("slug", path=f"it/{day}-local", entity_id=umbrella)
            for code in places["code"].to_list():
                polity, path, label, body, area = town_of(code)
                running = running_by.get((day, code))
                first_round = None if running is None else running.filter(pl.col("round") == 1)
                rounds = sorted(set(running["round"])) if running is not None else [1]
                elections: dict[int, UUID] = {}
                for round_ in rounds:
                    on = day if round_ == 1 else day + RUNOFF_GAP
                    stage = "local" if round_ == 1 else "runoff"
                    elections[round_] = cs.key(f"{SOURCE}.election", f"{day}:{code}:{round_}")
                    cs.add(
                        "election",
                        id=elections[round_],
                        polity_id=polity,
                        parent_id=umbrella if round_ == 1 else elections[1],
                        kind="local" if round_ == 1 else "runoff",
                        date_from=on,
                        date_to=on,
                        status="held",
                    )
                    name(
                        elections[round_],
                        f"{day.year} {label} municipal election" + (", second round" if round_ == 2 else ""),
                    )
                    name(
                        elections[round_],
                        f"Elezioni comunali di {label} del {label_of(on)}" + (" (ballottaggio)" if round_ == 2 else ""),
                        "it",
                        "native",
                    )
                    cs.add("slug", path=f"{path}/{on}-{stage}", entity_id=elections[round_])
                    counts = by_place.get((day, code, round_))
                    if counts is not None:
                        row = counts.row(0, named=True)
                        accounting_set = result_set(
                            f"{day}:{code}:{round_}:election",
                            elections[round_],
                            None,
                            [TURNOUT] if row["voters"] > row["registered"] else [],
                        )
                        for measure, column in (
                            (REGISTERED, "registered"),
                            (VOTERS, "voters"),
                            (BLANK, "blank"),
                            (INVALID, "null"),
                        ):
                            value(accounting_set, code, None, measure, row.get(column))
                voters = by_place.get((day, code, 1))
                voted = None if voters is None else voters["voters"][0]
                slate = lists_by.get((day, code))
                declared = DECLARED.get((day, code))
                if slate is not None and declared is not None:
                    where, seats_of = declared
                    if sorted(slate["list"]) != sorted(seats_of):
                        raise ValueError(f"{code} {day}: the declared seats name other lists than the open data")
                    if where == REGISTER:
                        defeated = (
                            set() if first_round is None else set(first_round.select("surname", "forename").iter_rows())
                        )
                        found = registered(day, code, defeated)
                        if found != Counter({label: n for label, n in seats_of.items() if n}):
                            raise ValueError(f"{code} {day}: the register gives {dict(found)}, not {seats_of}")
                    slate = slate.with_columns(seats=pl.col("list").replace_strict(seats_of, return_dtype=pl.Int64))
                if slate is not None:
                    seats = slate["seats"].fill_null(0).sum()
                    most_voted_seats = slate.sort("list_votes", descending=True)["seats"][0]
                    seated = 0 < seats <= LARGEST_COUNCIL and slate["seats"].null_count() == 0 and most_voted_seats > 0
                    contest = cs.key(f"{SOURCE}.contest", f"{day}:{code}:council")
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=elections[1],
                        kind="list_allocation",
                        body_id=body,
                        district_area_id=area,
                        magnitude=seats if seated else None,
                        round_number=1,
                        default_result_kind="counted",
                    )
                    name(contest, f"{day.year} {label} municipal council election")
                    name(contest, f"Elezione del consiglio comunale di {label} del {label_of(day)}", "it", "native")
                    cs.add("slug", path=f"{path}/{day}-local/consiglio-comunale", entity_id=contest)
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="context",
                        body_md=" ".join(
                            [
                                COUNTED,
                                NO_SEATS
                                if not seated
                                else COUNCIL_SEATS
                                if declared is None
                                else NETTUNO_SEATS
                                if declared[0] == NETTUNO
                                else REGISTER_SEATS,
                            ]
                        ),
                    )
                    over = voted is not None and slate["list_votes"].sum() > voted
                    council_set = result_set(f"{day}:{code}:council", elections[1], contest, [OVER] if over else [])
                    if declared is not None:
                        cs.add(
                            "citation",
                            entity_id=council_set,
                            source_id=declared_source(declared[0]),
                            locator={"url": NETTUNO, "pdf_pages": [1]}
                            if declared[0] == NETTUNO
                            else {"url": REGISTER, "ISTAT_CODICE_COMUNE": code},
                        )
                    for row in slate.sort("list_votes", "list", descending=[True, False]).iter_rows(named=True):
                        leader = " ".join(
                            str(row.get(c) or "")
                            for c in ("leader_surname", "leader_forename", "leader_candidate_votes")
                        ).strip()
                        option = cs.key(f"{SOURCE}.option", f"{day}:{code}:list:{leader}:{row['list']}:{row['nth']}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="list",
                            party_id=party_of(row["list"], day),
                            ballot_label=row["list"],
                        )
                        value(council_set, code, option, VOTES, row["list_votes"])
                        if seated and row["seats"]:
                            cs.add(
                                "outcome", result_set_id=council_set, option_id=option, kind="won", seats=row["seats"]
                            )
                if running is None:
                    continue
                office = office_of(code)
                final = max(rounds)
                previous: UUID | None = None
                for round_ in rounds:
                    on = day if round_ == 1 else day + RUNOFF_GAP
                    stage = "local" if round_ == 1 else "runoff"
                    contest = cs.key(f"{SOURCE}.contest", f"{day}:{code}:mayor:{round_}")
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=elections[round_],
                        kind="office",
                        office_id=office,
                        district_area_id=area,
                        magnitude=1,
                        round_number=round_,
                        previous_round_id=previous,
                        default_result_kind="counted",
                    )
                    previous = contest
                    suffix = "" if final == 1 else f", {'first' if round_ == 1 else 'second'} round"
                    name(contest, f"{day.year} {label} mayoral election{suffix}")
                    name(
                        contest,
                        f"Elezione del sindaco di {label} del {label_of(on)}"
                        + (" (ballottaggio)" if round_ == 2 else ""),
                        "it",
                        "native",
                    )
                    cs.add("slug", path=f"{path}/{on}-{stage}/sindaco", entity_id=contest)
                    standing = running.filter(pl.col("round") == round_).sort(
                        "candidate_votes", "surname", "forename", descending=[True, False, False]
                    )
                    winners = (
                        standing.filter(pl.col("elected").is_in(list(MAYOR)))
                        if "elected" in standing.columns
                        else standing.clear()
                    )
                    notes = [COUNTED]
                    if round_ == final:
                        notes.append(MAYOR_FLAGS if winners.height == 1 else NO_MAYOR)
                    elif round_ == 1:
                        notes.append(RUNOFF)
                    cs.add("note", entity_id=contest, kind="context", body_md=" ".join(notes))
                    turnout = by_place.get((day, code, round_))
                    over = turnout is not None and standing["candidate_votes"].sum() > turnout["voters"][0]
                    mayor_set = result_set(
                        f"{day}:{code}:mayor:{round_}", elections[round_], contest, [OVER] if over else []
                    )
                    finalists = set(running.filter(pl.col("round") == 2).select("surname", "forename").iter_rows())
                    seen: dict[tuple[str, str], int] = {}
                    for row in standing.iter_rows(named=True):
                        who = (row["surname"], row["forename"])
                        nth = seen[who] = seen.get(who, -1) + 1
                        ballot = f"{row['surname']} {row['forename']}".strip()
                        option = cs.key(f"{SOURCE}.option", f"{day}:{code}:mayor:{round_}:{ballot}:{nth}")
                        cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                        person = person_of(
                            f"{code}|{ballot}" if nth == 0 else f"{day}:{code}|{ballot}|{nth}",
                            proper(row["forename"]),
                            proper(row["surname"]),
                            birth(row["birth"]) if row.get("birth") else None,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", f"{day}:{code}:mayor:{round_}:{ballot}:{nth}"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=ballot,
                        )
                        value(mayor_set, code, option, VOTES, row["candidate_votes"])
                        if round_ == final and winners.height == 1 and row["elected"] in MAYOR:
                            cs.add("outcome", result_set_id=mayor_set, option_id=option, kind="elected")
                        elif round_ < final and who in finalists:
                            cs.add("outcome", result_set_id=mayor_set, option_id=option, kind="advanced")
        for id, born in births.items():
            if id not in elsewhere:
                cs.add("person", id=id, birth_date=born, birth_precision="day" if born is not None else None)
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema_overrides={"value": pl.Int64, "option_id": pl.String}).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
