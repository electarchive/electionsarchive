import io
import itertools
import json
import re
import unicodedata
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.it.interno import MANDATES, proper
from ea.sources.it.interno import PARTIES as INTERNO_PARTIES
from ea.store import DATA, connect

SOURCE = "it.storico"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://dait.interno.gov.it/documenti/opendata/camera"
ELIGENDO = "https://elezionistorico.interno.gov.it/index.php?tpel=C&dtel={day:%d/%m/%Y}&tpa=I&tpe=A&lev0=0&levsut0=0&es0=S&ms=S"
RIPARTIZIONE = (
    "https://elezionistorico.interno.gov.it/index.php?tpel=C&dtel={day:%d/%m/%Y}&tpa=E&tpe=T&lev0=0&levsut0=0"
    "&lev1={n}&levsut1=1&ne1={n}&es0=S&es1=S&ms=S"
)
RIPARTIZIONI = {
    "EUROPA": ("europe", "Europe"),
    "AMERICA MERIDIONALE": ("south-america", "South America"),
    "AMERICA SETTENTRIONALE E CENTRALE": ("north-and-central-america", "North and Central America"),
    "AFRICA ASIA OCEANIA ANTARTIDE": ("africa-asia-oceania-antarctica", "Africa, Asia, Oceania, and Antarctica"),
}
DAYS = [
    date(1948, 4, 18),
    date(1953, 6, 7),
    date(1958, 5, 25),
    date(1963, 4, 28),
    date(1968, 5, 19),
    date(1972, 5, 7),
    date(1976, 6, 20),
    date(1979, 6, 3),
    date(1983, 6, 26),
    date(1987, 6, 14),
    date(1992, 4, 5),
    date(1994, 3, 27),
    date(1996, 4, 21),
    date(2001, 5, 13),
    date(2006, 4, 9),
    date(2008, 4, 13),
    date(2013, 2, 24),
    date(2018, 3, 4),
]
ALIASES = {"ASSOCIAZIONE PER TRIESTE": "LISTA PER TRIESTE"}
MATTARELLA = date(1994, 3, 27)
CALDEROLI = date(2006, 4, 9)
ROSATO = date(2018, 3, 4)
NEXT = date(2022, 9, 25)
SIZES = [
    (574, date(1948, 5, 8)),
    (590, date(1953, 6, 25)),
    (596, date(1958, 6, 12)),
    (630, date(1963, 5, 16)),
    (400, date(2022, 10, 13)),
]
REGISTERED, VOTERS, VALID, BLANK, VOTES = 2, 14, 4, 6, 1
REGIONS = {
    "01": ("TORINO", "VERCELLI", "NOVARA", "CUNEO", "ASTI", "ALESSANDRIA", "BIELLA", "VERBANO-CUSIO-OSSOLA"),
    "02": ("AOSTA",),
    "03": (
        "VARESE",
        "COMO",
        "SONDRIO",
        "MILANO",
        "BERGAMO",
        "BRESCIA",
        "PAVIA",
        "CREMONA",
        "MANTOVA",
        "LECCO",
        "LODI",
        "MONZA",
        "MONZA E DELLA BRIANZA",
    ),
    "04": ("BOLZANO", "TRENTO"),
    "05": ("VERONA", "VICENZA", "BELLUNO", "TREVISO", "VENEZIA", "PADOVA", "ROVIGO"),
    "06": ("UDINE", "GORIZIA", "TRIESTE", "PORDENONE"),
    "07": ("IMPERIA", "SAVONA", "GENOVA", "LA SPEZIA"),
    "08": (
        "PIACENZA",
        "PARMA",
        "REGGIO EMILIA",
        "REGGIO NELL'EMILIA",
        "MODENA",
        "BOLOGNA",
        "FERRARA",
        "RAVENNA",
        "FORLI'",
        "FORLI'-CESENA",
        "RIMINI",
    ),
    "09": (
        "MASSA CARRARA",
        "MASSA-CARRARA",
        "LUCCA",
        "PISTOIA",
        "FIRENZE",
        "LIVORNO",
        "PISA",
        "AREZZO",
        "SIENA",
        "GROSSETO",
        "PRATO",
    ),
    "10": ("PERUGIA", "TERNI"),
    "11": ("PESARO E URBINO", "ANCONA", "MACERATA", "ASCOLI PICENO", "FERMO"),
    "12": ("VITERBO", "RIETI", "ROMA", "LATINA", "FROSINONE"),
    "13": ("L'AQUILA", "TERAMO", "PESCARA", "CHIETI"),
    "14": ("CAMPOBASSO", "ISERNIA"),
    "15": ("CASERTA", "BENEVENTO", "NAPOLI", "AVELLINO", "SALERNO"),
    "16": ("FOGGIA", "BARI", "TARANTO", "BRINDISI", "LECCE", "BARLETTA-ANDRIA-TRANI"),
    "17": ("POTENZA", "MATERA"),
    "18": ("COSENZA", "CATANZARO", "REGGIO CALABRIA", "CROTONE", "VIBO VALENTIA"),
    "19": ("TRAPANI", "PALERMO", "MESSINA", "AGRIGENTO", "CALTANISSETTA", "ENNA", "CATANIA", "RAGUSA", "SIRACUSA"),
    "20": (
        "SASSARI",
        "NUORO",
        "CAGLIARI",
        "ORISTANO",
        "OLBIA-TEMPIO",
        "OGLIASTRA",
        "MEDIO CAMPIDANO",
        "CARBONIA-IGLESIAS",
        "CARBONIA-IGLES.",
    ),
}
PROVINCES = {province: code for code, provinces in REGIONS.items() for province in provinces}
CIRCOSCRIZIONI = {
    "PIEMONTE": "01",
    "VALLE D'AOSTA": "02",
    "LOMBARDIA": "03",
    "TRENTINO ALTO ADIGE": "04",
    "VENETO": "05",
    "FRIULI VENEZIA GIULIA": "06",
    "LIGURIA": "07",
    "EMILIA ROMAGNA": "08",
    "TOSCANA": "09",
    "UMBRIA": "10",
    "MARCHE": "11",
    "LAZIO": "12",
    "ABRUZZO": "13",
    "ABRUZZI": "13",
    "MOLISE": "14",
    "CAMPANIA": "15",
    "PUGLIA": "16",
    "BASILICATA": "17",
    "CALABRIA": "18",
    "SICILIA": "19",
    "SARDEGNA": "20",
}
EXISTING = {
    "DC": ("wikidata", "Q815348"),
    "PCI": ("wikidata", "Q461886"),
    "PSI": ("wikidata", "Q590750"),
    "PSDI": ("wikidata", "Q665335"),
    "PLI": ("wikidata", "Q508733"),
    "PRI": ("wikidata", "Q767560"),
    "P.RAD": ("wikidata", "Q2704736"),
    "FEDERAZIONE DEI VERDI": ("wikidata", "Q662502"),
    "LEGA NORD": ("wikidata", "Q47750"),
    "FORZA ITALIA": ("wikidata", "Q215350"),
    "ALLEANZA NAZIONALE": ("wikidata", "Q662849"),
    "RIFONDAZIONE COMUNISTA": ("wikidata", "Q780997"),
    "RINNOVAMENTO IT-DINI": ("wikidata", "Q1960994"),
    "POP-SVP-PRI-UD-PRODI": ("wikidata", "Q3908389"),
    "L'ULIVO": ("wikidata", "Q1134161"),
    "DEMOCRATICI SINISTRA": ("wikidata", "Q541679"),
    "COMUNISTI ITALIANI": ("wikidata", "Q1428001"),
    "LISTA DI PIETRO": ("wikidata", "Q47768"),
    "DI PIETRO ITALIA DEI VALORI": ("wikidata", "Q47768"),
    "U.D.EUR POPOLARI": ("wikidata", "Q1546314"),
    "UNIONE DI CENTRO": ("wikidata", "Q47781"),
    "NUOVO PSI": ("wikidata", "Q1631143"),
    "IL POPOLO DELLA LIBERTA'": ("wikidata", "Q47720"),
    "PARTITO DEMOCRATICO": ("wikidata", "Q47729"),
    "SINISTRA ECOLOGIA LIBERTA'": ("wikidata", "Q286140"),
    "MOVIMENTO 5 STELLE BEPPEGRILLO.IT": ("wikidata", "Q47817"),
    "MOVIMENTO 5 STELLE": ("wikidata", "Q47817"),
    "SCELTA CIVICA CON MONTI PER L'ITALIA": ("wikidata", "Q2792033"),
    "FRATELLI D'ITALIA": ("wikidata", "Q1757843"),
    "FRATELLI D'ITALIA CON GIORGIA MELONI": ("wikidata", "Q1757843"),
    "+EUROPA": ("wikidata", "Q47090559"),
    "LEGA": ("wikidata", "Q85776185"),
    "SVP - PATT": ("it.party", "sudtiroler-volkspartei-patt"),
}
OVERRIDES = {
    ("FORZA ITALIA", 2018): ("it.party", "forza-italia"),
    ("LEGA LOMBARDA", 1992): ("wikidata", "Q47750"),
    ("CCD-CDU", 2001): ("wikidata", "Q855423"),
}
NEW = {
    "FR.DEMOCR.POPOLARE": ("Fronte Democratico Popolare", "FDP", "list_vehicle"),
    "UNITA' SOCIALISTA": ("Unità Socialista", "US", "list_vehicle"),
    "BLOCCO NAZIONALE": ("Blocco Nazionale", "BN", "list_vehicle"),
    "P.NAZ.MON.ALL.D.LAV.": ("Partito Nazionale Monarchico", "PNM", "party"),
    "P.NAZ.MONARCHICO": ("Partito Nazionale Monarchico", "PNM", "party"),
    "P.MONARCHICO POP.": ("Partito Monarchico Popolare", "PMP", "party"),
    "PDIUM": ("Partito Democratico Italiano di Unità Monarchica", "PDIUM", "party"),
    "MSI": ("Movimento Sociale Italiano", "MSI", "party"),
    "MSI-DN": ("Movimento Sociale Italiano", "MSI", "party"),
    "PSU": ("Partito Socialista Unificato", "PSU", "party"),
    "PSIUP": ("Partito Socialista Italiano di Unità Proletaria", "PSIUP", "party"),
    "PRI-P.RAD.": ("Partito Repubblicano Italiano – Partito Radicale", None, "list_vehicle"),
    "PPST": ("Südtiroler Volkspartei", "SVP", "party"),
    "PPST-PPTT": ("Südtiroler Volkspartei", "SVP", "party"),
    "SVP": ("Südtiroler Volkspartei", "SVP", "party"),
    "UV": ("Union Valdôtaine", "UV", "party"),
    "COMUNITA'": ("Movimento Comunità", None, "party"),
    "DEM.PROL": ("Democrazia Proletaria", "DP", "party"),
    "PDUP": ("Partito di Unità Proletaria per il Comunismo", "PdUP", "party"),
    "MANIFESTO": ("Il Manifesto", None, "list_vehicle"),
    "NUOVA SIN.UNITA": ("Nuova Sinistra Unita", "NSU", "list_vehicle"),
    "DN-CD": ("Democrazia Nazionale", "DN", "party"),
    "UNITA'POPOLARE": ("Unità Popolare", None, "party"),
    "LISTA VERDE": ("Liste Verdi", None, "list_vehicle"),
    "LEGA LOMBARDA": ("Lega Lombarda", None, "party"),
    "LIGA VENETA": ("Liga Veneta", None, "party"),
    "LIGA VENETA-PU": ("Liga Veneta", None, "party"),
    "PS D'AZ.": ("Partito Sardo d'Azione", "PSd'Az", "party"),
    "PDS": ("Partito Democratico della Sinistra", "PDS", "party"),
    "LA RETE-MOV.DEM.": ("La Rete", None, "party"),
    "LISTA PANNELLA": ("Lista Pannella", None, "list_vehicle"),
    "PANNELLA-SGARBI": ("Lista Pannella–Sgarbi", None, "list_vehicle"),
    "PANNELLA-BONINO": ("Lista Pannella–Bonino", None, "list_vehicle"),
    "P.POPOLARE ITALIANO": ("Partito Popolare Italiano", "PPI", "party"),
    "PATTO SEGNI": ("Patto Segni", None, "party"),
    "ALLEANZA DEMOCRATICA": ("Alleanza Democratica", "AD", "party"),
    "CCD-CDU": ("CCD–CDU", None, "list_vehicle"),
    "MOV.SOC.TRICOLORE": ("Movimento Sociale Fiamma Tricolore", "MS-FT", "party"),
    "FIAMMA TRICOLORE": ("Movimento Sociale Fiamma Tricolore", "MS-FT", "party"),
    "LA MARGHERITA": ("Democrazia è Libertà – La Margherita", "DL", "party"),
    "IL GIRASOLE": ("Il Girasole", None, "list_vehicle"),
    "DEMOCRAZIA EUROPEA": ("Democrazia Europea", "DE", "party"),
    "LA ROSA NEL PUGNO": ("Rosa nel Pugno", "RnP", "list_vehicle"),
    "PARTITO PENSIONATI": ("Partito Pensionati", None, "party"),
    "DEM.CRIST.-NUOVO PSI": ("Democrazia Cristiana per le Autonomie – Nuovo PSI", None, "list_vehicle"),
    "ALTERNATIVA SOCIALE MUSSOLINI": ("Alternativa Sociale", None, "list_vehicle"),
    "LA SINISTRA L'ARCOBALENO": ("La Sinistra l'Arcobaleno", None, "list_vehicle"),
    "LA DESTRA - FIAMMA TRICOLORE": ("La Destra – Fiamma Tricolore", None, "list_vehicle"),
    "LA DESTRA": ("La Destra", None, "party"),
    "MOVIMENTO PER L'AUTONOMIA ALL.PER IL SUD": ("Movimento per le Autonomie", "MpA", "party"),
    "PARTITO SOCIALISTA": ("Partito Socialista", None, "party"),
    "RIVOLUZIONE CIVILE": ("Rivoluzione Civile", None, "list_vehicle"),
    "FARE PER FERMARE IL DECLINO": ("Fare per Fermare il Declino", None, "party"),
    "FUTURO E LIBERTA'": ("Futuro e Libertà per l'Italia", "FLI", "party"),
    "LIBERI E UGUALI": ("Liberi e Uguali", "LeU", "list_vehicle"),
    "NOI CON L'ITALIA - UDC": ("Noi con l'Italia – UDC", None, "list_vehicle"),
    "POTERE AL POPOLO!": ("Potere al Popolo!", "PaP", "list_vehicle"),
    "CASAPOUND ITALIA": ("CasaPound Italia", None, "party"),
    "IL POPOLO DELLA FAMIGLIA": ("Il Popolo della Famiglia", None, "party"),
    "ITALIA EUROPA INSIEME": ("Italia Europa Insieme", None, "list_vehicle"),
    "CIVICA POPOLARE LORENZIN": ("Civica Popolare", None, "list_vehicle"),
    "PARTITO COMUNISTA": ("Partito Comunista", None, "party"),
    "VALLEE D'AOSTE": ("Vallée d'Aoste", None, "list_vehicle"),
}
NEW_OVERRIDES = {("CENTRO DEMOCRATICO", 2013): ("Centro Democratico", "CD", "party")}
SYMBOLS = {
    "PROGRESSISTI": ("Progressives", "Progressisti"),
    "PATTO PER L'ITALIA": ("Pact for Italy", "Patto per l'Italia"),
    "L'ULIVO": ("The Olive Tree", "L'Ulivo"),
    "POLO PER LE LIBERTA'": ("Pole for Freedoms", "Polo per le Libertà"),
    "CASA DELLE LIBERTA'": ("House of Freedoms", "Casa delle Libertà"),
}
NORTH = ("Pole of Freedoms", "Polo delle Libertà")
SOUTH = ("Pole of Good Government", "Polo del Buon Governo")
CENTER_LEFT = ("Center-left coalition", "Coalizione di centrosinistra")
CENTER_RIGHT = ("Center-right coalition", "Coalizione di centrodestra")
MEMBERS = {
    ("Pole of Freedoms", 1994): {"FORZA ITALIA", "LEGA NORD", "CENTRO CRIST.DEM.", "LISTA PANNELLA"},
    ("Pole of Good Government", 1994): {"FORZA ITALIA", "ALLEANZA NAZIONALE", "CENTRO CRIST.DEM."},
    ("Progressives", 1994): {
        "PDS",
        "RIFONDAZIONE COMUNISTA",
        "FEDERAZIONE DEI VERDI",
        "PSI",
        "LA RETE-MOV.DEM.",
        "ALLEANZA DEMOCRATICA",
        "RINASCITA SOCIALISTA",
    },
    ("Pact for Italy", 1994): {"P.POPOLARE ITALIANO", "PATTO SEGNI"},
    ("Pole for Freedoms", 1996): {"FORZA ITALIA", "ALLEANZA NAZIONALE", "CCD-CDU"},
    ("The Olive Tree", 1996): {"PDS", "POP-SVP-PRI-UD-PRODI", "RINNOVAMENTO IT-DINI", "FEDERAZIONE DEI VERDI"},
    ("House of Freedoms", 2001): {"FORZA ITALIA", "ALLEANZA NAZIONALE", "CCD-CDU", "LEGA NORD", "NUOVO PSI"},
    ("The Olive Tree", 2001): {"DEMOCRATICI SINISTRA", "LA MARGHERITA", "IL GIRASOLE", "COMUNISTI ITALIANI"},
}
SYSTEMS = {
    "camera-proportional-1948": (
        {"formula": "imperiali_quota", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        "Chamber of Deputies proportional representation, 1948–1993",
        (
            "Deputies were elected by open-list proportional representation in about thirty multi-member constituencies. "
            "Seats were first shared out in each constituency with an Imperiali quota, and the remaining votes and seats "
            "were pooled in a national college (Collegio Unico Nazionale) and shared by largest remainders. Voters could "
            "cast preference votes for candidates on the list they chose. Valle d'Aosta elected one deputy by plurality."
        ),
    ),
    "camera-single-member-1993": (
        {"formula": "fptp"},
        "Chamber of Deputies single-member colleges, 1993–2005",
        None,
    ),
    "camera-proportional-1993": (
        {
            "formula": "hare",
            "threshold": {"percent": 4, "level": "list", "scope": "national"},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        "Chamber of Deputies proportional seats, 1993–2005",
        (
            "Under the 1993 electoral law, 475 deputies were elected by plurality in single-member colleges and 155 by "
            "closed-list proportional representation among lists with at least 4% of the national vote. Votes that had "
            "already elected a single-member deputy were partly subtracted from the linked list (scorporo)."
        ),
    ),
    "camera-proportional-2005": (
        {"formula": "hare", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        "Chamber of Deputies proportional representation with majority bonus, 2005–2015",
        (
            "Under the 2005 electoral law, 617 deputies were elected in Italy by closed-list proportional representation. "
            "The coalition or list with the most votes nationally received at least 340 seats. Coalitions needed 10% of "
            "the vote, lists inside them 2%, and lists running alone 4%. Valle d'Aosta elected one deputy by plurality and "
            "Italians abroad elected twelve."
        ),
    ),
    "camera-valle-d-aosta": ({"formula": "fptp"}, "Chamber of Deputies, Valle d'Aosta single-member seat", None),
    "camera-estero": (
        {"formula": "hare", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        "Chamber of Deputies, constituency abroad",
        (
            "Since 2006, Italians resident abroad elect twelve deputies in four ripartizioni: Europe; South America; "
            "North and Central America; and Africa, Asia, Oceania, and Antarctica. Seats in each ripartizione go to "
            "lists by the largest remainder of a Hare quota, and to candidates by preference votes."
        ),
    ),
}
ABROAD_ELECTED = (
    "List votes are the Interior Ministry's archive totals by country. The archive gives no seats or preference "
    "votes for the constituency abroad, so the elected deputies and their lists come from the Chamber's record of "
    "its members, and each list's seats are its elected deputies."
)
EIGHT_ABROAD = (
    "Since the constitutional reform of 2020 (constitutional law 1/2020), Italians abroad elect eight deputies, not "
    "twelve: three in Europe, two in South America, two in North and Central America, and one in Africa, Asia, "
    "Oceania, and Antarctica."
)
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
ELECTION_NOTE = (
    "List votes are the Interior Ministry's archive totals by municipality, summed into today's regions. Seats are "
    "the deputies the archive marks as elected on each list."
)
NATIONAL_SEATS = (
    "Seats are each list's national total from Eligendo's historical results, which count the deputies elected from "
    "the national remainder college. The open-data archive marks only {elected} of the {seats} deputies as elected."
)
MIXED_SEATS = (
    "These are the {seats} proportional seats. The single-member deputies are counted in their own college contests."
)
ABROAD = (
    "Valle d'Aosta's single seat and the twelve seats elected by Italians abroad are counted in their own contests."
)
ABROAD_SEATS = (
    "List votes are the Interior Ministry's archive totals by country. Seats are each list's seats in this "
    "ripartizione, {source}. Deputies abroad were elected by preference votes within each list; who they were is "
    "not loaded here."
)
MISSING_VOTES = (
    "The archive's candidate votes add up to {share} of this college's valid votes, because rows for part of its "
    "territory are missing. The winner shown is the candidate with the most recorded votes."
)
DERIVED_WINNERS = (
    "The archive has no winner flag for single-member colleges, so each college's winner here is its candidate with "
    "the most votes. Candidates ran under coalition symbols, which are stored as alliances when the symbol names one."
)


GROUPS_QUERY = """
PREFIX ocd: <http://dati.camera.it/ocd/>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX bio: <http://purl.org/vocab/bio/0.1/>
SELECT DISTINCT ?d ?cognome ?nome ?nascita ?titolo ?inizio WHERE {{
  ?d a ocd:deputato ; ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_{legislature}> ;
     foaf:surname ?cognome ; foaf:firstName ?nome ; ocd:rif_mandatoCamera ?m ; ocd:aderisce ?a .
  ?m ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_{legislature}> ; ocd:rif_elezione ?e .
  ?e ocd:tipoElezione ?tipo . FILTER(STRSTARTS(?tipo, "maggioritario"))
  ?a ocd:rif_gruppoParlamentare ?g ; ocd:startDate ?inizio . ?g dc:title ?titolo
  BIND(IRI(CONCAT("http://dati.camera.it/ocd/persona.rdf/p", STRBEFORE(STRAFTER(STR(?d), "deputato.rdf/d"), "_"))) AS ?p)
  OPTIONAL {{ ?p bio:Birth ?b . ?b bio:date ?nascita }}
}}
"""
LEGISLATURES = {1994: 12, 1996: 13, 2001: 14, 2018: 18}
GROUPS = {
    year: "https://dati.camera.it/sparql?"
    + urlencode({"query": GROUPS_QUERY.format(legislature=legislature), "format": "application/sparql-results+json"})
    for year, legislature in LEGISLATURES.items()
}
GROUP_LABELS = {
    1994: {
        "LEGA NORD": "LEGA NORD",
        "FORZA ITALIA": "FORZA ITALIA",
        "ALLEANZA NAZIONALE": "ALLEANZA NAZIONALE",
        "CENTRO CRISTIANO DEMOCRATICO": "CENTRO CRIST.DEM.",
        "RIFONDAZIONE COMUNISTA": "RIFONDAZIONE COMUNISTA",
        "PARTITO POPOLARE ITALIANO": "P.POPOLARE ITALIANO",
    },
    1996: {
        "DEMOCRATICI DI SINISTRA": "PDS",
        "POPOLARI DEMOCRATICI": "P.POPOLARE ITALIANO",
        "FORZA ITALIA": "FORZA ITALIA",
        "ALLEANZA NAZIONALE": "ALLEANZA NAZIONALE",
        "LEGA NORD": "LEGA NORD",
        "RINNOVAMENTO ITALIANO": "RINNOVAMENTO IT-DINI",
        "CENTRO CRISTIANO DEMOCRATICO": "CCD-CDU",
    },
    2001: {
        "FORZA ITALIA": "FORZA ITALIA",
        "DEMOCRATICI DI SINISTRA": "DEMOCRATICI SINISTRA",
        "ALLEANZA NAZIONALE": "ALLEANZA NAZIONALE",
        "MARGHERITA": "LA MARGHERITA",
        "UDC": "CCD-CDU",
        "LEGA NORD": "LEGA NORD",
        "RIFONDAZIONE COMUNISTA": "RIFONDAZIONE COMUNISTA",
    },
    2018: {
        "MOVIMENTO 5 STELLE": "MOVIMENTO 5 STELLE",
        "LEGA": "LEGA",
        "FORZA ITALIA": "FORZA ITALIA",
        "PARTITO DEMOCRATICO": "PARTITO DEMOCRATICO",
        "FRATELLI D'ITALIA": "FRATELLI D'ITALIA CON GIORGIA MELONI",
        "LIBERI E UGUALI": "LIBERI E UGUALI",
    },
}


def fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", folded)


def url(day: date) -> str:
    return f"{ARCHIVE}/camera-{day:%Y%m%d}.zip"


def count(column: str) -> pl.Expr:
    return pl.col(column).str.replace(r",00$", "").cast(pl.Int64)


def region_of(circ: str) -> str:
    return CIRCOSCRIZIONI[re.sub(r"[\s\d]+$", "", circ.upper().replace("-", " ").split("/")[0]).strip()]


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".zip")
        if day < MATTARELLA:
            raw.fetch(SOURCE, ELIGENDO.format(day=day), suffix=".html")
        if CALDEROLI <= day < ROSATO:
            for n in range(1, 5):
                raw.fetch(SOURCE, RIPARTIZIONE.format(day=day, n=n), suffix=".html")
    for groups_url in GROUPS.values():
        raw.fetch(SOURCE, groups_url, suffix=".json")
    raw.fetch(SOURCE, url(NEXT), suffix=".zip")


def parse() -> None:
    lists: list[pl.DataFrame] = []
    accounting: list[pl.DataFrame] = []
    seats: list[pl.DataFrame] = []
    singles: list[pl.DataFrame] = []
    single_accounting: list[pl.DataFrame] = []
    abroad: list[pl.DataFrame] = []
    abroad_accounting: list[pl.DataFrame] = []
    abroad_seats: list[pl.DataFrame] = []
    for day in DAYS:
        with zipfile.ZipFile(raw.cached(SOURCE, url(day)).path) as archive:
            names = archive.namelist()

            def read(pattern: str, names: list[str] = names) -> pl.DataFrame:
                member = next(name for name in names if re.search(pattern, name, re.IGNORECASE))
                return pl.read_csv(archive.read(member), separator=";", encoding="latin1", infer_schema=False)

            if day < ROSATO:
                votes = read(
                    r"_proporzionale\.txt$" if MATTARELLA <= day < CALDEROLI else r"^camera(_italia)?-\d{8}\.txt$"
                ).rename({"VOTILISTA": "VOTI_LISTA"}, strict=False)
                place = "PROVINCIA" if "PROVINCIA" in votes.columns else "COLLEGIO"
                votes = votes.with_columns(
                    region=pl.col("PROVINCIA").replace_strict(PROVINCES)
                    if place == "PROVINCIA"
                    else pl.col("CIRCOSCRIZIONE").map_elements(region_of, return_dtype=pl.String)
                )
                comuni = votes.select(
                    "region",
                    "CIRCOSCRIZIONE",
                    place,
                    "COMUNE",
                    registered=count("ELETTORI"),
                    voters=count("VOTANTI"),
                    blank=count("SCHEDE_BIANCHE"),
                ).unique()
                if comuni.select("CIRCOSCRIZIONE", place, "COMUNE").is_duplicated().any():
                    raise ValueError(f"{day}: conflicting turnout rows for one municipality")
                totals = comuni.group_by("region").agg(pl.col("registered", "voters", "blank").sum())
                votes = votes.select("region", label=pl.col("LISTA"), votes=count("VOTI_LISTA"))
                elected = read(r"_preferenze\.txt$" if day < MATTARELLA else r"_candidatilista\.txt$").filter(
                    pl.col("CODTIPOELETTO") == "E"
                )
                won = elected.group_by(label=pl.col("descrlista").replace(ALIASES)).agg(seats=pl.len().cast(pl.Int64))
            else:
                votes = read(r"Liste_Italia_Liv_Circ\.txt$").select(
                    region=pl.col("CIRCOSCRIZIONE").map_elements(region_of, return_dtype=pl.String),
                    label=pl.col("LISTA"),
                    votes=count("VOTILISTA"),
                )
                totals = (
                    read(r"Scrutini_Italia_Liv_Circ\.txt$")
                    .select(
                        region=pl.col("CIRCOSCR").map_elements(region_of, return_dtype=pl.String),
                        registered=count("NUMELETTORITOTALI"),
                        voters=count("NUMVOTANTITOTALI"),
                        blank=count("SKBIANCHE"),
                    )
                    .group_by("region")
                    .agg(pl.col("registered", "voters", "blank").sum())
                )
                won = read(r"Liste_Italia_Liv_Naz\.txt$").select(
                    label=pl.col("LISTA"), seats=count("SEGGILISTA").fill_null(0)
                )
            votes = votes.group_by("region", "label").agg(pl.col("votes").sum())
            missing = set(won.filter(pl.col("seats") > 0)["label"]) - set(votes["label"])
            if missing:
                raise ValueError(f"{day}: elected lists without votes: {missing}")
            lists.append(votes.with_columns(day=pl.lit(day)))
            won = won.with_columns(archived=pl.col("seats"))
            if day < MATTARELLA:
                page = raw.cached(SOURCE, ELIGENDO.format(day=day)).path.read_text(encoding="utf-8")
                national = dict(votes.group_by("label").agg(pl.col("votes").sum()).iter_rows())
                printed = []
                for label, total, seated in re.findall(
                    r'<th id="lista\d+"[^>]*>([^<]+)</th><td[^>]*>([\d.]+)</td><td[^>]*>[^<]*</td><td[^>]*>(\d+)</td>',
                    page,
                ):
                    if seated == "0":
                        continue
                    label, total = label.replace("&#039;", "'").strip(), int(total.replace(".", ""))
                    matched = (
                        label
                        if label in national
                        else min(national, key=lambda known: abs(national[known] - total), default=None)
                    )
                    if matched is None or (matched != label and abs(national[matched] - total) > 0.05 * total):
                        raise ValueError(f"{day}: Eligendo list {label} with {total} votes is not in the archive")
                    printed.append((matched, int(seated)))
                checked = pl.DataFrame(printed, schema={"label": pl.String, "seats": pl.Int64}, orient="row")
                magnitude = {1948: 574, 1953: 590, 1958: 596}.get(day.year, 630)
                if checked["seats"].sum() != magnitude:
                    raise ValueError(f"{day}: Eligendo seats add up to {checked['seats'].sum()}, not {magnitude}")
                won = checked.join(won.select("label", "archived"), on="label", how="full", coalesce=True)
                if won.filter(pl.col("archived").fill_null(0) > pl.col("seats").fill_null(0)).height:
                    raise ValueError(f"{day}: lists with more elected deputies than national seats")
                won = won.with_columns(pl.col("seats", "archived").fill_null(0))
            seats.append(won.filter(pl.col("seats") > 0).with_columns(day=pl.lit(day)))
            totals = totals.join(votes.group_by("region").agg(valid=pl.col("votes").sum()), on="region")
            accounting.append(totals.with_columns(day=pl.lit(day)))

            if day >= CALDEROLI:
                estero = [name for name in names if re.search(r"estero", name, re.IGNORECASE)]

                def sheet(pattern: str, estero: list[str] = estero) -> pl.DataFrame:
                    member = next(name for name in estero if re.search(pattern, name, re.IGNORECASE))
                    if member.endswith(".xlsx"):
                        return pl.read_excel(io.BytesIO(archive.read(member)), infer_schema_length=0)
                    return pl.read_csv(archive.read(member), separator=";", encoding="latin1", infer_schema=False)

                listed = sheet(r"Liste_(Estero_)?Liv_?Naz")
                abroad.append(
                    listed.select(
                        ripartizione=pl.col("RIPARTIZIONE").str.strip_chars(),
                        country=pl.col("NAZIONE").str.strip_chars(),
                        label=pl.col("LISTA" if "LISTA" in listed.columns else "DESCRLISTA").str.strip_chars(),
                        votes=count("TOTVOTI"),
                    ).with_columns(day=pl.lit(day))
                )
                scrutini = sheet(r"Scrutini_(Estero_)?Liv_?Naz")
                abroad_accounting.append(
                    scrutini.select(
                        ripartizione=pl.col("RIPARTIZIONE").str.strip_chars(),
                        country=pl.col("NAZIONE").str.strip_chars(),
                        registered=count(
                            "NUMELETTORITOTALI" if "NUMELETTORITOTALI" in scrutini.columns else "ELETTORITOTALI"
                        ),
                        voters=count("NUMVOTANTITOTALI"),
                        blank=count("SKBIANCHE"),
                        valid=count("VOTIVALIDI"),
                    ).with_columns(day=pl.lit(day))
                )
                if day >= ROSATO:
                    seated = sheet(r"Liste_Estero_Liv_Ripart").select(
                        ripartizione=pl.col("RIPARTIZIONE").str.strip_chars(),
                        label=pl.col("LISTA").str.strip_chars(),
                        seats=count("SEGGI"),
                    )
                else:
                    printed = []
                    for n, ripartizione in enumerate(RIPARTIZIONI, 1):
                        page = raw.cached(SOURCE, RIPARTIZIONE.format(day=day, n=n)).path.read_text(encoding="utf-8")
                        if f"Ripartizione {ripartizione}" not in re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)):
                            raise ValueError(f"{day}: Eligendo page {n} is not {ripartizione}")
                        for row, cell in re.findall(r'id="lista(\d+)"[^>]*>(.*?)</th>', page, re.DOTALL):
                            seats_ = re.search(rf'headers="hseggi lista{row}">(\d*)</td>', page)
                            label = re.sub(r"<[^>]+>", "", cell).replace("&#039;", "'").strip()
                            printed.append((ripartizione, label, int(seats_.group(1) or 0) if seats_ else 0))
                    seated = pl.DataFrame(
                        printed, schema={"ripartizione": pl.String, "label": pl.String, "seats": pl.Int64}, orient="row"
                    )
                seated = seated.filter(pl.col("seats") > 0)
                unknown = seated.join(abroad[-1], on=["ripartizione", "label"], how="anti")
                if unknown.height or seated["seats"].sum() != 12:
                    raise ValueError(f"{day}: abroad seats {seated['seats'].sum()}, unmatched {unknown}")
                abroad_seats.append(seated.with_columns(day=pl.lit(day)))
            if day < MATTARELLA:
                continue
            if day < CALDEROLI:
                rows = read(r"Uninom_Cand&Contr\.txt$").with_columns(
                    votes=count("TOTVOTI"),
                    birth=pl.col("datanascita").str.strptime(pl.Date, "%d/%m/%Y %H:%M:%S", strict=False),
                    college=pl.col("circ") + " - " + pl.col("coll"),
                )
                people = ["college", "circ", "coll", "cognome", "nome", "birth"]
                candidates = (
                    rows.select(*people, "comune", "votes", "sesso")
                    .unique()
                    .group_by(people)
                    .agg(pl.col("votes").sum(), pl.col("sesso").first())
                    .join(
                        rows.group_by(people).agg(symbols=pl.col("descrcontrass").unique().sort()),
                        on=people,
                        nulls_equal=True,
                    )
                    .select(
                        "college",
                        name=pl.col("coll"),
                        region=pl.col("circ").map_elements(region_of, return_dtype=pl.String),
                        surname=pl.col("cognome"),
                        forename=pl.col("nome"),
                        birth="birth",
                        sex=pl.col("sesso").replace("", None),
                        votes="votes",
                        symbols="symbols",
                        elected=pl.lit(None, pl.Boolean),
                    )
                )
                turnout = (
                    read(r"Uninom_Scrutini\.txt$")
                    .group_by(college=pl.col("circ") + " - " + pl.col("coll"))
                    .agg(
                        registered=count("elettoritot").sum(),
                        voters=count("NUMVOTANTITOTALI").sum(),
                        blank=count("SKBIANCHE").sum(),
                        valid=count("VOTIVALIDI").sum(),
                    )
                )
            elif day < ROSATO:
                rows = read(r"^camera_vaosta-\d{8}\.txt$")
                candidates = (
                    rows.group_by(surname=pl.col("COGNOME"), forename=pl.col("NOME"))
                    .agg(
                        votes=count("VOTI_LISTA").sum(),
                        sex=pl.col("SESSO").first(),
                        symbols=pl.col("LISTA").unique().sort(),
                        elected=(pl.col("ELETTO") == "E").any(),
                    )
                    .with_columns(
                        college=pl.lit("VALLE D'AOSTA"),
                        name=pl.lit("Valle d'Aosta"),
                        region=pl.lit("02"),
                        birth=pl.lit(None, pl.Date),
                    )
                )
                turnout = (
                    rows.select(
                        "COMUNE",
                        registered=count("ELETTORI_TOTALI"),
                        voters=count("VOTANTI_TOTALI"),
                        blank=count("SCHEDE_BIANCHE"),
                    )
                    .unique()
                    .select(
                        college=pl.lit("VALLE D'AOSTA"),
                        registered=pl.col("registered").sum(),
                        voters=pl.col("voters").sum(),
                        blank=pl.col("blank").sum(),
                        valid=pl.lit(candidates["votes"].sum(), pl.Int64),
                    )
                )
            else:
                number = pl.col("COLLEGIOUNINOM").str.extract(r"^(\d+)").cast(pl.Int64)
                code = pl.col("circ") + pl.format(" - U{}", number.cast(pl.String).str.zfill(2))
                place = pl.col("COLLEGIOUNINOM").str.replace(r"^\d+\s*-?\s*", "").str.to_titlecase()
                rows = read(r"Liste&CandidCollUninom\.txt$").rename({"CIRCOCRIZIONE": "circ"})
                aosta = read(r"VAosta_CandidCollUninom\.txt$").with_columns(
                    circ=pl.lit("VALLE D'AOSTA"),
                    VOTICAND=pl.col("TOTVOTI"),
                    COLLEGIOUNINOM=pl.col("COLLEGIOUNINOM").str.extract(r"(\d+)$"),
                )
                people = ["college", "name", "circ", "COGNOME", "NOME", "DATANASCITA", "SESSO"]
                candidates = (
                    pl.concat(
                        [
                            frame.select(
                                *people[2:],
                                "CODTIPOELETTO",
                                "VOTICAND",
                                "LISTA",
                                college=code,
                                name=pl.col("circ").str.to_titlecase()
                                + pl.format(" - U{} (", number.cast(pl.String).str.zfill(2))
                                + place
                                + ")",
                            )
                            for frame in (rows, aosta)
                        ]
                    )
                    .group_by(people)
                    .agg(
                        votes=count("VOTICAND").first(),
                        symbols=pl.col("LISTA").unique().sort(),
                        elected=(pl.col("CODTIPOELETTO") == "E").any(),
                    )
                    .select(
                        "college",
                        "name",
                        region=pl.col("circ").map_elements(region_of, return_dtype=pl.String),
                        surname=pl.col("COGNOME"),
                        forename=pl.col("NOME"),
                        birth=pl.col("DATANASCITA")
                        .str.split(" ")
                        .list.first()
                        .str.strptime(pl.Date, "%d/%m/%Y", strict=False),
                        sex=pl.col("SESSO"),
                        votes="votes",
                        symbols="symbols",
                        elected="elected",
                    )
                )
                turnout = pl.concat(
                    [
                        read(r"Scrutini_Italia_CollUninom\.txt$")
                        .rename({"CIRCOSCRIZIONE": "circ"})
                        .select(
                            college=code,
                            registered=count("ELETTORITOTALI"),
                            voters=count("NUMVOTANTITOTALI"),
                            blank=count("SKBIANCHE"),
                            valid=count("VOTIVALIDICAND"),
                        ),
                        read(r"Scrutini_VAosta_LivCollUninom\.txt$")
                        .rename({"COLLEGIO": "COLLEGIOUNINOM"})
                        .with_columns(
                            circ=pl.lit("VALLE D'AOSTA"),
                            COLLEGIOUNINOM=pl.col("COLLEGIOUNINOM").str.extract(r"(\d+)$"),
                        )
                        .select(
                            college=code,
                            registered=count("NUMELETTORITOTALI"),
                            voters=count("NUMVOTANTITOTALI"),
                            blank=count("SKBIANCHE"),
                            valid=count("VOTIVALIDI"),
                        ),
                    ]
                )
            if candidates["elected"].null_count() > 0:
                ranked = candidates.sort("votes", "birth", descending=[True, False], nulls_last=True)
                top = ranked.group_by("college", maintain_order=True).head(2)
                tied = top.group_by("college").agg(
                    pl.col("votes").n_unique().alias("distinct"), pl.col("birth").null_count().alias("unknown")
                )
                if tied.filter((pl.col("distinct") == 1) & (pl.col("unknown") > 0)).height:
                    raise ValueError(f"{day}: tied single-member college without birth dates")
                first = ranked.group_by("college", maintain_order=True).head(1).select("college", "surname", "forename")
                candidates = (
                    candidates.drop("elected")
                    .join(first.with_columns(elected=pl.lit(True)), on=["college", "surname", "forename"], how="left")
                    .with_columns(pl.col("elected").fill_null(False))
                )
            if candidates.group_by("college").agg(pl.col("elected").sum())["elected"].max() != 1 or (
                candidates.group_by("college").agg(pl.col("elected").sum())["elected"].min() != 1
            ):
                raise ValueError(f"{day}: a single-member college without exactly one winner")
            singles.append(candidates.with_columns(day=pl.lit(day)))
            single_accounting.append(turnout.with_columns(day=pl.lit(day)))

    with zipfile.ZipFile(raw.cached(SOURCE, url(NEXT)).path) as archive:
        listed = pl.read_excel(io.BytesIO(archive.read("camera2022_EsteroListe_livNaz.xlsx")), infer_schema_length=0)
        scrutini = pl.read_excel(
            io.BytesIO(archive.read("camera2022_EsteroScrutini_livNaz.xlsx")), infer_schema_length=0
        )
    abroad.append(
        listed.select(
            ripartizione=pl.col("RIPARTIZIONE").str.strip_chars(),
            country=pl.col("NAZIONE").str.strip_chars(),
            label=pl.col("DESCRLISTA").str.strip_chars(),
            votes=count("TOTVOTI"),
        ).with_columns(day=pl.lit(NEXT))
    )
    abroad_accounting.append(
        scrutini.select(
            ripartizione=pl.col("RIPARTIZIONE").str.strip_chars(),
            country=pl.col("NAZIONE").str.strip_chars(),
            registered=count("ELETTORITOTALI"),
            voters=count("NUMVOTANTITOTALI"),
            blank=count("SKBIANCHE"),
            valid=count("VOTIVALIDI"),
        ).with_columns(day=pl.lit(NEXT))
    )
    members = pl.DataFrame(
        [
            {
                "day": NEXT,
                "ripartizione": binding["collegio"]["value"],
                "label": binding["lista"]["value"],
                "deputy": binding["d"]["value"].rsplit("/d", 1)[1].removesuffix("_19"),
                "surname": binding["cognome"]["value"],
                "forename": binding["nome"]["value"],
            }
            for binding in json.loads(raw.cached("it.interno", MANDATES).path.read_text())["results"]["bindings"]
            if binding.get("collegio", {}).get("value") in RIPARTIZIONI and binding["inizio"]["value"] <= "20221013"
        ]
    )
    seated = members.group_by("ripartizione", "label").agg(seats=pl.len().cast(pl.Int64))
    if seated.join(abroad[-1], on=["ripartizione", "label"], how="anti").height or seated["seats"].sum() != 8:
        raise ValueError(f"{NEXT}: abroad seats do not match the lists: {seated}")
    abroad_seats.append(seated.with_columns(day=pl.lit(NEXT)))

    PARSED.mkdir(parents=True, exist_ok=True)
    members.write_parquet(PARSED / "abroad_members.parquet")
    pl.concat(lists).write_parquet(PARSED / "lists.parquet")
    pl.concat(accounting, how="diagonal").write_parquet(PARSED / "accounting.parquet")
    pl.concat(seats).write_parquet(PARSED / "seats.parquet")
    pl.concat(abroad).write_parquet(PARSED / "abroad.parquet")
    pl.concat(abroad_accounting).write_parquet(PARSED / "abroad_accounting.parquet")
    pl.concat(abroad_seats).write_parquet(PARSED / "abroad_seats.parquet")
    pl.concat(singles, how="diagonal").write_parquet(PARSED / "singles.parquet")
    pl.DataFrame(
        [
            (
                year,
                binding["d"]["value"].rsplit("/", 1)[1],
                binding["cognome"]["value"],
                binding["nome"]["value"],
                datetime.strptime(binding["nascita"]["value"], "%Y%m%d").date() if "nascita" in binding else None,
                binding["titolo"]["value"],
                binding["inizio"]["value"],
            )
            for year, groups_url in GROUPS.items()
            for binding in json.loads(raw.cached(SOURCE, groups_url).path.read_text())["results"]["bindings"]
        ],
        schema=["year", "deputy", "surname", "forename", "birth", "group", "start"],
        orient="row",
    ).write_parquet(PARSED / "deputy_groups.parquet")
    pl.concat(single_accounting, how="diagonal").write_parquet(PARSED / "single_accounting.parquet")


def emit() -> Path:
    lists = pl.read_parquet(PARSED / "lists.parquet")
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    abroad = pl.read_parquet(PARSED / "abroad.parquet")
    abroad_accounting = pl.read_parquet(PARSED / "abroad_accounting.parquet")
    abroad_seats = pl.read_parquet(PARSED / "abroad_seats.parquet")
    abroad_members = pl.read_parquet(PARSED / "abroad_members.parquet")
    singles = pl.read_parquet(PARSED / "singles.parquet")
    single_accounting = pl.read_parquet(PARSED / "single_accounting.parquet")
    deputy_spells: dict[int, dict[str, list[tuple[str, str]]]] = {}
    surnames: dict[tuple[int, str], dict[str, tuple[str, date | None]]] = {}
    for year, deputy, surname, forename, birth, group, start in (
        pl.read_parquet(PARSED / "deputy_groups.parquet").sort("start").iter_rows()
    ):
        deputy_spells.setdefault(year, {}).setdefault(deputy, []).append((group, start))
        surnames.setdefault((year, fold(surname)), {})[deputy] = (fold(forename), birth)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian Chamber of Deputies elections 1948–2018: list votes by region, seats, single-member colleges, and the constituency abroad, with the 2022 constituency abroad",
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def existing(scheme: str, value: str) -> UUID:
            entity = cs.key(scheme, value)
            if entity in cs.minted:
                raise ValueError(f"{scheme}:{value} is not in the database")
            return entity

        interno = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        if interno in cs.minted:
            raise ValueError("publish it.interno before it.storico")
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), interno)
            if day < MATTARELLA:
                cs.artifact(raw.cached(SOURCE, ELIGENDO.format(day=day)), interno)
            if CALDEROLI <= day < ROSATO:
                for n in range(1, 5):
                    cs.artifact(raw.cached(SOURCE, RIPARTIZIONE.format(day=day, n=n)), interno)
        cs.artifact(raw.cached(SOURCE, url(NEXT)), interno)
        chamber_record = cs.source("Camera dei deputati", "Mandati dei deputati della XIX legislatura")
        if chamber_record in cs.minted:
            raise ValueError("publish it.interno before it.storico")
        cs.artifact(raw.cached("it.interno", MANDATES), chamber_record)
        minted_countries: set[str] = set()

        short = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'caveat'
                     and (n.body_md like 'The archive marks %%' or n.body_md like 'Valle d''Aosta''s single seat is %%')""",
                [SOURCE],
            ).fetchall()
        )
        italy = cs.polity("IT")
        nation = cs.area("it.nation", "IT")
        region_areas = {code: cs.area("it.regione", code) for code in REGIONS}
        if nation in cs.minted or any(area in cs.minted for area in region_areas.values()):
            raise ValueError("publish it.istat before it.storico")
        region_names: dict[str, str] = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id
                   where a.scheme_code = 'it.regione' and n.is_preferred"""
            ).fetchall()
        )
        region_units: dict[str, UUID] = {}
        for code, area in region_areas.items():
            region_units[code] = cs.unit("it.regione", code)
            if region_units[code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=region_units[code],
                    polity_id=italy,
                    scheme_code="it.regione",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
                name(region_units[code], region_names[code], "it")

        schemes = {code for (code,) in conn.execute("select code from scheme where code like 'it.%'").fetchall()}
        for code in ("it.collegio_camera_1993", "it.collegio_camera_2017"):
            if code not in schemes:
                cs.add(
                    "scheme",
                    code=code,
                    polity_id=italy,
                    kind="electoral_district",
                    rank=3,
                    noun="single-member college",
                )

        chamber = existing("it.body", "chamber-of-deputies")
        sized = {row[0] for row in conn.execute("select valid_from from body_size where body_id = %s", [chamber])}
        for (size, start), (_, end) in itertools.pairwise(SIZES):
            if start not in sized:
                cs.add("body_size", body_id=chamber, seats=size, valid_from=start, valid_to=end)

        systems: dict[str, UUID] = {}
        for code, (rules, label, methodology) in SYSTEMS.items():
            systems[code] = cs.key("it.electoral_system", code)
            if systems[code] in cs.minted:
                cs.add("electoral_system", id=systems[code], polity_id=italy, rules=rules, rules_version=1)
                name(systems[code], label)
                if methodology is not None:
                    cs.add("note", entity_id=systems[code], kind="methodology", body_md=methodology)
        systems["camera-single-member"] = existing("it.electoral_system", "camera-single-member")
        systems["camera-proportional"] = existing("it.electoral_system", "camera-proportional")

        parties: dict[tuple[str, str], UUID] = {}

        def party_of(label: str, year: int) -> UUID:
            spec = OVERRIDES.get((label, year)) or EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = existing(*spec)
                return parties[spec]
            full, abbreviation, kind = (
                NEW_OVERRIDES.get((label, year)) or NEW.get(label) or (label, None, "list_vehicle")
            )
            known = (label, year) in NEW_OVERRIDES or label in NEW
            key = ("it.party", slug(full)) if known else ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    name(parties[key], full, "it")
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
            return parties[key]

        def value_row(result_set: UUID, unit: UUID, option: UUID | None, measure: int, value: int) -> None:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )

        def result_set(key: str, election: UUID, contest: UUID, completeness: float = 1) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=interno,
                is_default=True,
                completeness=completeness,
            )
            return id

        colleges: dict[str, tuple[UUID, UUID]] = {}

        def college(code: str, label: str, region: str, day: date) -> tuple[UUID, UUID]:
            if region == "02":
                return region_areas[region], region_units[region]
            if code in colleges:
                return colleges[code]
            scheme, start, end = (
                ("it.collegio_camera_1993", MATTARELLA, CALDEROLI)
                if day < CALDEROLI
                else ("it.collegio_camera_2017", ROSATO, NEXT)
            )
            area = cs.area(scheme, code, on=start)
            unit = cs.unit(scheme, code, on=start)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                cs.add("area_parent", area_id=area, parent_id=region_areas[region], valid_from=start, valid_to=end)
                name(area, label, "it")
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=italy,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=start,
                    valid_to=end,
                )
                name(unit, label, "it")
            colleges[code] = (area, unit)
            return colleges[code]

        persons: dict[tuple[str, str], UUID] = {}

        def person_of(scheme: str, key: str, display: str, sex: str | None, birth: date | None) -> UUID:
            if (scheme, key) not in persons:
                persons[scheme, key] = cs.key(scheme, key)
                if persons[scheme, key] in cs.minted:
                    cs.add(
                        "person",
                        id=persons[scheme, key],
                        birth_date=birth,
                        birth_precision=None if birth is None else "day",
                        gender={"M": "male", "F": "female"}.get(sex or ""),
                    )
                    name(persons[scheme, key], display, "it")
            return persons[scheme, key]

        def abroad_contests(day: date, election: UUID, path: str, year: int) -> None:
            day_abroad = abroad.filter(pl.col("day") == day)
            for ripartizione, (code, english) in RIPARTIZIONI.items() if day >= CALDEROLI else ():
                abroad_key = f"{day}:camera:estero:{code}"
                abroad_contest = cs.key("it.contest", abroad_key)
                abroad_won = dict(
                    abroad_seats.filter((pl.col("day") == day) & (pl.col("ripartizione") == ripartizione))
                    .select("label", "seats")
                    .iter_rows()
                )
                cs.add(
                    "contest",
                    id=abroad_contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=chamber,
                    magnitude=sum(abroad_won.values()),
                    electoral_system_id=systems["camera-estero"],
                    default_result_kind="counted",
                )
                name(abroad_contest, f"Chamber of Deputies, abroad: {english}")
                cs.add("slug", path=f"{path}/chamber/abroad-{code}", entity_id=abroad_contest)
                abroad_set = result_set(abroad_key, election, abroad_contest)
                cs.add(
                    "note",
                    entity_id=abroad_set,
                    kind="methodology",
                    body_md=ABROAD_ELECTED
                    if day == NEXT
                    else ABROAD_SEATS.format(
                        source="as the archive gives them"
                        if day >= ROSATO
                        else "from Eligendo's historical results for the ripartizione"
                    ),
                )
                if day == NEXT:
                    cs.add("note", entity_id=abroad_contest, kind="context", body_md=EIGHT_ABROAD)
                rows = day_abroad.filter(pl.col("ripartizione") == ripartizione)
                turnout_abroad = abroad_accounting.filter(
                    (pl.col("day") == day) & (pl.col("ripartizione") == ripartizione)
                )
                units: dict[str, UUID] = {}
                for country in sorted(set(rows["country"]) | set(turnout_abroad["country"])):
                    units[country] = cs.unit("it.estero", country)
                    if units[country] in cs.minted and country not in minted_countries:
                        minted_countries.add(country)
                        cs.add(
                            "reporting_unit",
                            id=units[country],
                            polity_id=italy,
                            scheme_code="it.estero",
                            code=country,
                            area_relation="unlocated",
                            kind="abroad_mission",
                        )
                        name(units[country], country.title().replace(" E ", " e ").replace(" Di ", " di "), "it")
                fresh_abroad = abroad_set in cs.minted
                if fresh_abroad:
                    for country, registered, voters, blank, valid in (
                        turnout_abroad.select("country", "registered", "voters", "blank", "valid")
                        .sort("country")
                        .iter_rows()
                    ):
                        if registered >= voters:
                            value_row(abroad_set, units[country], None, REGISTERED, registered)
                        value_row(abroad_set, units[country], None, VOTERS, voters)
                        value_row(abroad_set, units[country], None, BLANK, blank)
                        value_row(abroad_set, units[country], None, VALID, valid)
                for label in sorted(set(rows.filter(pl.col("votes") > 0)["label"])):
                    option = cs.key("it.option", f"{abroad_key}:{slug(label)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=abroad_contest,
                        kind="list",
                        party_id=existing("it.party", slug(INTERNO_PARTIES[label][0]))
                        if day == NEXT and label in INTERNO_PARTIES
                        else party_of(label, year),
                        ballot_label=label,
                    )
                    if label in abroad_won:
                        cs.add(
                            "outcome", result_set_id=abroad_set, option_id=option, kind="won", seats=abroad_won[label]
                        )
                    for deputy, surname, forename in (
                        abroad_members.filter(
                            (pl.col("day") == day)
                            & (pl.col("ripartizione") == ripartizione)
                            & (pl.col("label") == label)
                        )
                        .select("deputy", "surname", "forename")
                        .iter_rows()
                    ):
                        display = f"{proper(forename)} {proper(surname)}"
                        candidacy = cs.key("it.candidacy", f"{abroad_key}:{deputy}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person_of("it.camera.persona", deputy, display, None, None),
                            option_id=option,
                            role="list_member",
                            ballot_name=f"{surname} {forename}",
                        )
                        cs.add(
                            "outcome",
                            result_set_id=abroad_set,
                            option_id=option,
                            candidacy_id=candidacy,
                            kind="elected",
                        )
                    if fresh_abroad:
                        for country, votes in (
                            rows.filter((pl.col("label") == label) & (pl.col("votes") > 0))
                            .group_by("country")
                            .agg(pl.col("votes").sum())
                            .sort("country")
                            .iter_rows()
                        ):
                            value_row(abroad_set, units[country], option, VOTES, votes)

        for day in DAYS:
            year = day.year
            election = cs.key("it.election", f"{day}:general")
            path = f"it/{day}-general"
            if election in cs.minted:
                cs.add(
                    "election", id=election, polity_id=italy, kind="general", date_from=day, date_to=day, status="held"
                )
                name(election, f"{year} Italian general election")
                name(election, f"Elezioni politiche del {year}", "it", "native")
                cs.add("slug", path=path, entity_id=election)
                cs.add(
                    "note",
                    entity_id=election,
                    kind="context",
                    body_md=ELECTION_NOTE
                    if day < MATTARELLA or day >= CALDEROLI
                    else f"{ELECTION_NOTE} {DERIVED_WINNERS}",
                )
            if day < MATTARELLA:
                system, magnitude = "camera-proportional-1948", {1948: 574, 1953: 590, 1958: 596}.get(year, 630)
            elif day < CALDEROLI:
                system, magnitude = "camera-proportional-1993", 155
            elif day < ROSATO:
                system, magnitude = "camera-proportional-2005", 617
            else:
                system, magnitude = "camera-proportional", 386
            contest_key = f"{day}:camera"
            contest = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=chamber,
                district_area_id=nation,
                magnitude=magnitude,
                electoral_system_id=systems[system],
                default_result_kind="counted",
            )
            name(contest, "Chamber of Deputies, national seat allocation")
            cs.add("slug", path=f"{path}/chamber", entity_id=contest)
            counted = result_set(contest_key, election, contest)
            fresh = counted in cs.minted
            day_seats = seats.filter(pl.col("day") == day)
            won = dict(day_seats.select("label", "seats").iter_rows())
            if day < MATTARELLA and day_seats["archived"].sum() < magnitude:
                cs.add(
                    "note",
                    **({"id": short[counted]} if counted in short else {}),
                    entity_id=counted,
                    kind="methodology",
                    body_md=NATIONAL_SEATS.format(elected=day_seats["archived"].sum(), seats=magnitude),
                )
            if day >= CALDEROLI and counted in short:
                cs.add("note", id=short[counted], entity_id=counted, kind="caveat", body_md=ABROAD)
            if fresh:
                notes = []
                if MATTARELLA <= day:
                    notes.append(MIXED_SEATS.format(seats=magnitude) if day < CALDEROLI else ABROAD)
                if day >= ROSATO:
                    notes.append(MIXED_SEATS.format(seats=magnitude))
                for body in notes:
                    cs.add("note", entity_id=counted, kind="caveat", body_md=body)
                for region, registered, voters, blank, valid in (
                    accounting.filter(pl.col("day") == day)
                    .select("region", "registered", "voters", "blank", "valid")
                    .sort("region")
                    .iter_rows()
                ):
                    if registered >= voters:
                        value_row(counted, region_units[region], None, REGISTERED, registered)
                    value_row(counted, region_units[region], None, VOTERS, voters)
                    value_row(counted, region_units[region], None, BLANK, blank)
                    value_row(counted, region_units[region], None, VALID, valid)
            day_lists = lists.filter(pl.col("day") == day)
            for (label,) in (
                day_lists.group_by("label")
                .agg(pl.col("votes").sum())
                .filter(pl.col("votes") > 0)
                .select("label")
                .sort("label")
                .iter_rows()
            ):
                option = cs.key("it.option", f"{contest_key}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party_of(label, year),
                    ballot_label=label,
                )
                if label in won:
                    cs.add("outcome", result_set_id=counted, option_id=option, kind="won", seats=won[label])
                if fresh:
                    for region, votes in (
                        day_lists.filter((pl.col("label") == label) & (pl.col("votes") > 0))
                        .select("region", "votes")
                        .sort("region")
                        .iter_rows()
                    ):
                        value_row(counted, region_units[region], option, VOTES, votes)

            abroad_contests(day, election, path, year)

            day_singles = singles.filter(pl.col("day") == day)
            if day_singles.height == 0:
                continue
            alliances: dict[tuple[str, str], UUID] = {}
            members: dict[tuple[str, str], set[str]] = {}
            single_system = (
                systems["camera-single-member-1993"]
                if day < CALDEROLI
                else systems["camera-valle-d-aosta"]
                if day < ROSATO
                else systems["camera-single-member"]
            )
            turnout = {
                row[0]: row[1:]
                for row in single_accounting.filter(pl.col("day") == day)
                .select("college", "registered", "voters", "blank", "valid")
                .iter_rows()
            }
            for code, label, region in (
                day_singles.select("college", "name", "region").unique().sort("college").iter_rows()
            ):
                if region == "02":
                    label = "Valle d'Aosta"
                area, unit = college(code, label, region, day)
                single_key = f"{day}:camera:{code}"
                single = cs.key("it.contest", single_key)
                cs.add(
                    "contest",
                    id=single,
                    election_id=election,
                    kind="body_seats",
                    body_id=chamber,
                    district_area_id=area,
                    magnitude=1,
                    electoral_system_id=single_system,
                    default_result_kind="counted",
                )
                name(single, f"Chamber of Deputies, {label}")
                cs.add("slug", path=f"{path}/chamber/{slug(code)}", entity_id=single)
                counted_votes = day_singles.filter(pl.col("college") == code)["votes"].sum()
                share = 1 if code not in turnout else min(1, counted_votes / turnout[code][3])
                declared = result_set(single_key, election, single, round(share, 4))
                fresh = declared in cs.minted
                if fresh and share < 1:
                    cs.add(
                        "note",
                        entity_id=declared,
                        kind="caveat",
                        body_md=MISSING_VOTES.format(share=f"{share:.0%}"),
                    )
                if fresh and code in turnout:
                    registered, voters, blank, valid = turnout[code]
                    if registered >= voters:
                        value_row(declared, unit, None, REGISTERED, registered)
                    value_row(declared, unit, None, VOTERS, voters)
                    value_row(declared, unit, None, BLANK, blank)
                    value_row(declared, unit, None, VALID, valid)
                for surname, forename, birth, sex, votes, symbols, elected in (
                    day_singles.filter(pl.col("college") == code)
                    .select("surname", "forename", "birth", "sex", "votes", "symbols", "elected")
                    .sort("surname", "forename")
                    .iter_rows()
                ):
                    if birth is None:
                        scheme, key = f"{SOURCE}.person", f"{code}:{surname}:{forename}"
                    else:
                        scheme, key = "it.interno.person", f"{surname}:{forename}:{birth}"
                    person = person_of(scheme, key, f"{forename} {surname}".title(), sex, birth)
                    held = set(symbols)
                    coalition = next((SYMBOLS[symbol] for symbol in symbols if symbol in SYMBOLS), None)
                    if coalition is None and {"FORZA ITALIA", "LEGA NORD"} <= held:
                        coalition = NORTH
                    elif coalition is None and {"FORZA ITALIA", "ALLEANZA NAZIONALE"} <= held:
                        coalition = SOUTH
                    elif coalition is None and day >= ROSATO and len(held) > 1:
                        coalition = CENTER_LEFT if "PARTITO DEMOCRATICO" in held else CENTER_RIGHT
                    alliance = None
                    if coalition is not None:
                        if coalition not in alliances:
                            alliances[coalition] = cs.key("it.alliance", f"{day}:{slug(coalition[0])}")
                            members[coalition] = set()
                            if alliances[coalition] in cs.minted:
                                cs.add(
                                    "alliance", id=alliances[coalition], kind="electoral_alliance", election_id=election
                                )
                                name(alliances[coalition], coalition[0])
                                name(alliances[coalition], coalition[1], "it", "native")
                        alliance = alliances[coalition]
                        members[coalition] |= held if day >= ROSATO else MEMBERS.get((coalition[0], year), set())
                    party = party_of(symbols[0], year) if coalition is None and len(held) == 1 else None
                    ballot = f"{surname} {forename}"
                    option_key = f"{single_key}:{surname}:{forename}"
                    option = cs.key("it.option", option_key)
                    member = None
                    if elected and year in LEGISLATURES:
                        found = surnames.get((year, fold(surname.split(" IN ")[0])), {})
                        if len(found) > 1 and birth is not None:
                            found = {d: v for d, v in found.items() if v[1] == birth} or found
                        if len(found) > 1:
                            found = {d: v for d, v in found.items() if v[0][:3] == fold(forename)[:3]} or found
                        if len(found) == 1:
                            for group, _ in deputy_spells[year][next(iter(found))]:
                                label = next(
                                    (label for prefix, label in GROUP_LABELS[year].items() if group.startswith(prefix)),
                                    None,
                                )
                                if label is not None:
                                    member = party_of(label, year)
                                    break
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=single,
                        kind="candidate",
                        party_id=party,
                        alliance_id=alliance,
                        ballot_label=ballot,
                    )
                    candidacy = cs.key("it.candidacy", option_key)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        member_of_party_id=member,
                        ballot_name=ballot,
                        is_independent=False,
                    )
                    for symbol in dict.fromkeys(symbols):
                        if symbol not in SYMBOLS:
                            cs.add(
                                "candidacy_nomination",
                                candidacy_id=candidacy,
                                party_id=party_of(symbol, year),
                                kind="nominated",
                            )
                    if elected:
                        cs.add("outcome", result_set_id=declared, option_id=option, kind="elected")
                    if fresh:
                        value_row(declared, unit, option, VOTES, votes)
            for coalition, labels in members.items():
                if alliances[coalition] in cs.minted or (coalition[0], year) in MEMBERS:
                    for label in sorted(labels):
                        cs.add("alliance_member", alliance_id=alliances[coalition], party_id=party_of(label, year))
        abroad_contests(NEXT, existing("it.election", f"{NEXT}:general"), f"it/{NEXT}-general", NEXT.year)
        return cs.write()
