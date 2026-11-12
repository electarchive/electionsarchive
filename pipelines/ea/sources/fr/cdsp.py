import html
import json
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.cdsp"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "legi": "https://static.data.gouv.fr/resources/elections-legislatives-1958-2012/community/20150206-172137/LEGISLATIVES_1958-2012-csv.zip",
    "presi": "https://static.data.gouv.fr/resources/elections-presidentielles-1965-2012-1/community/20150209-113725/PRESIDENTIELLES_1965-2012-csv.zip",
}
LANDING = {
    "legi": "https://www.data.gouv.fr/datasets/elections-legislatives-1958-2012",
    "presi": "https://www.data.gouv.fr/datasets/elections-presidentielles-1965-2012-1",
}
TITLES = {"legi": "Élections législatives 1958-2012", "presi": "Élections présidentielles 1965-2012"}
AGGREGATED = {
    "general": "https://data-pipeline-open.s3.sbg.io.cloud.ovh.net/elections/general_results.parquet",
    "candidates": "https://data-pipeline-open.s3.sbg.io.cloud.ovh.net/elections/candidats_results.parquet",
}
AGGREGATED_TITLE = "Données des élections agrégées"
COUNCIL = {
    1965: ("1965/656pdr", "1965/6510pdr"),
    1969: ("1969/6920PDR", "1969/6922pdr"),
    1974: ("1974/7430pdr", "1974/7432PDR"),
    1981: ("1981/8145pdr", "1981/8147pdr"),
    1988: ("1988/8856pdr", "1988/8860PDR"),
    1995: ("1995/9579pdr", "1995/9581PDR"),
    2002: ("2002/2002109pdr", "2002/2002111PDR"),
}
COUNCIL_URL = "https://www.conseil-constitutionnel.fr/decision/{}.htm"
COUNCIL_PUBLISHER = "Conseil constitutionnel"
COUNCIL_TITLE = "Décisions PDR : résultats de l'élection présidentielle"
AGGREGATED_YEAR = 2017
PUBLISHER = "Centre de données socio-politiques, Sciences Po"
REGISTERED, VALID, INVALID, BLANK, VOTERS, VOTES = 2, 4, 5, 6, 14, 1
ACCOUNTING = {"Inscrits": REGISTERED, "Votants": VOTERS, "Exprimés": VALID, "Blancs et nuls": INVALID}
SKIPPED = {"élu premier tour", "Taux de participation (%)"}
PRESIDENTIAL = {
    1965: (date(1965, 12, 5), date(1965, 12, 19)),
    1969: (date(1969, 6, 1), date(1969, 6, 15)),
    1974: (date(1974, 5, 5), date(1974, 5, 19)),
    1981: (date(1981, 4, 26), date(1981, 5, 10)),
    1988: (date(1988, 4, 24), date(1988, 5, 8)),
    1995: (date(1995, 4, 23), date(1995, 5, 7)),
    2002: (date(2002, 4, 21), date(2002, 5, 5)),
    2007: (date(2007, 4, 22), date(2007, 5, 6)),
    2012: (date(2012, 4, 22), date(2012, 5, 6)),
    2017: (date(2017, 4, 23), date(2017, 5, 7)),
    2022: (date(2022, 4, 10), date(2022, 4, 24)),
}
LEGISLATIVE = {
    1958: (date(1958, 11, 23), date(1958, 11, 30)),
    1962: (date(1962, 11, 18), date(1962, 11, 25)),
    1967: (date(1967, 3, 5), date(1967, 3, 12)),
    1968: (date(1968, 6, 23), date(1968, 6, 30)),
    1973: (date(1973, 3, 4), date(1973, 3, 11)),
    1978: (date(1978, 3, 12), date(1978, 3, 19)),
    1981: (date(1981, 6, 14), date(1981, 6, 21)),
    1988: (date(1988, 6, 5), date(1988, 6, 12)),
    1993: (date(1993, 3, 21), date(1993, 3, 28)),
    1997: (date(1997, 5, 25), date(1997, 6, 1)),
    2002: (date(2002, 6, 9), date(2002, 6, 16)),
    2007: (date(2007, 6, 10), date(2007, 6, 17)),
    2012: (date(2012, 6, 10), date(2012, 6, 17)),
    2017: (date(2017, 6, 11), date(2017, 6, 18)),
    2022: (date(2022, 6, 12), date(2022, 6, 19)),
}
MAPS = [
    (date(1958, 11, 23), date(1967, 3, 5)),
    (date(1967, 3, 5), date(1986, 3, 16)),
    (date(1988, 6, 5), date(2012, 6, 10)),
    (date(2012, 6, 10), None),
]
DEPARTEMENTS_SPLIT = date(1968, 1, 1)
CORSICA_SPLIT = date(1976, 1, 1)
OLD = {"75": "Seine", "78": "Seine-et-Oise", "20": "Corse"}
OVERSEAS = {
    "GUADELOUPE": "971",
    "MARTINIQUE": "972",
    "GUYANE": "973",
    "LA REUNION": "974",
    "SAINT PIERRE ET MIQUELON": "975",
    "MAYOTTE": "976",
    "WALLIS ET FUTUNA": "986",
    "POLYNESIE FRANCAISE": "987",
    "NOUVELLE CALEDONIE": "988",
    "SAINT BARTHELEMY ET SAINT MARTIN": "ZX",
    "SAINT MARTIN SAINT BARTHELEMY": "ZX",
    "FRANCAIS DE L ETRANGER": "ZZ",
}
CANDIDATES = {
    "MITTERRAND": "François Mitterrand",
    "LECANUET": "Jean Lecanuet",
    "DE GAULLE": "Charles de Gaulle",
    "MARCILHACY": "Pierre Marcilhacy",
    "TIXIER-VIGNANCOUR": "Jean-Louis Tixier-Vignancour",
    "BARBU": "Marcel Barbu",
    "KRIVINE": "Alain Krivine",
    "ROCARD": "Michel Rocard",
    "DUCLOS": "Jacques Duclos",
    "DEFFERRE": "Gaston Defferre",
    "POMPIDOU": "Georges Pompidou",
    "POHER": "Alain Poher",
    "DUCATEL": "Louis Ducatel",
    "LAGUILLER": "Arlette Laguiller",
    "MULLER": "Émile Muller",
    "DUMONT": "René Dumont",
    "GISCARD D'ESTAING": "Valéry Giscard d'Estaing",
    "CHABAN-DELMAS": "Jacques Chaban-Delmas",
    "RENOUVIN": "Bertrand Renouvin",
    "ROYER": "Jean Royer",
    "LE PEN": "Jean-Marie Le Pen",
    "HERAUD": "Guy Héraud",
    "SEBAG": "Jean-Claude Sebag",
    "BOUCHARDEAU": "Huguette Bouchardeau",
    "MARCHAIS": "Georges Marchais",
    "CREPEAU": "Michel Crépeau",
    "LALONDE": "Brice Lalonde",
    "CHIRAC": "Jacques Chirac",
    "DEBRE": "Michel Debré",
    "GARAUD": "Marie-France Garaud",
    "BOUSSEL": "Pierre Boussel",
    "LAJOINIE": "André Lajoinie",
    "JUQUIN": "Pierre Juquin",
    "WAECHTER": "Antoine Waechter",
    "BARRE": "Raymond Barre",
    "HUE": "Robert Hue",
    "JOSPIN": "Lionel Jospin",
    "VOYNET": "Dominique Voynet",
    "BALLADUR": "Édouard Balladur",
    "DE VILLIERS": "Philippe de Villiers",
    "CHEMINADE": "Jacques Cheminade",
    "GLUCKSTEIN": "Daniel Gluckstein",
    "BESANCENOT": "Olivier Besancenot",
    "TAUBIRA": "Christiane Taubira",
    "CHEVENEMENT": "Jean-Pierre Chevènement",
    "MAMERE": "Noël Mamère",
    "LEPAGE": "Corinne Lepage",
    "BAYROU": "François Bayrou",
    "MADELIN": "Alain Madelin",
    "BOUTIN": "Christine Boutin",
    "MEGRET": "Bruno Mégret",
    "SAINT-JOSSE": "Jean Saint-Josse",
    "SCHIVARDI": "Gérard Schivardi",
    "BOVE": "José Bové",
    "BUFFET": "Marie-George Buffet",
    "ROYAL": "Ségolène Royal",
    "SARKOZY": "Nicolas Sarkozy",
    "NIHOUS": "Frédéric Nihous",
    "Nathalie ARTHAUD": "Nathalie Arthaud",
    "Philippe POUTOU": "Philippe Poutou",
    "Jean-Luc MELENCHON": "Jean-Luc Mélenchon",
    "François HOLLANDE": "François Hollande",
    "Eva JOLY": "Eva Joly",
    "François BAYROU": "François Bayrou",
    "Nicolas SARKOZY": "Nicolas Sarkozy",
    "Nicolas DUPONT-AIGNAN": "Nicolas Dupont-Aignan",
    "Marine LE PEN": "Marine Le Pen",
    "Jacques CHEMINADE": "Jacques Cheminade",
}
LATER_PARTIES = {
    "Emmanuel Macron": "LREM",
    "Marine Le Pen": "FN",
    "François Fillon": "LR",
    "Jean-Luc Mélenchon": "LFI",
    "Benoît Hamon": "PS",
    "Nicolas Dupont-Aignan": "DLF",
    "Jean Lassalle": "RES",
    "Philippe Poutou": "NPA",
    "François Asselineau": "UPR",
    "Nathalie Arthaud": "LO",
    "Jacques Cheminade": "SP",
    "Éric Zemmour": "REC",
    "Valérie Pécresse": "LR",
    "Yannick Jadot": "EELV",
    "Fabien Roussel": "PCF",
    "Anne Hidalgo": "PS",
}
RENAMED = date(2018, 6, 1)
PARTIES = {
    "MRP": ("wikidata", "Q1236315"),
    "UNR": ("parlgov", "1094"),
    "UDR": ("parlgov", "1094"),
    "PCF": ("wikidata", "Q192821"),
    "PC": ("wikidata", "Q192821"),
    "CD": ("wikidata", "Q2037506"),
    "PS": ("wikidata", "Q170972"),
    "RI": ("wikidata", "Q2300622"),
    "UDF": ("wikidata", "Q827415"),
    "RPR": ("wikidata", "Q1052584"),
    "MRG": ("wikidata", "Q427965"),
    "PRG": ("wikidata", "Q427965"),
    "V": ("wikidata", "Q743390"),
    "VERTS": ("wikidata", "Q743390"),
    "UMP": ("wikidata", "Q173152"),
    "MODEM": ("wikidata", "Q587370"),
    "LREM": ("wikidata", "Q23731823"),
    "LR": ("wikidata", "Q20012759"),
}
NEW_PARTIES = {
    "CIR": "Convention des institutions républicaines",
    "LCR": "Ligue communiste révolutionnaire",
    "PSU": "Parti socialiste unifié",
    "SFIO": "Section française de l'Internationale ouvrière",
    "LO": "Lutte ouvrière",
    "MDSR": "Mouvement démocrate socialiste de France",
    "NAR": "Nouvelle Action royaliste",
    "FN": "Front national",
    "MPT": "Mouvement pour un parti des travailleurs",
    "MPF": "Mouvement pour la France",
    "POE": "Parti ouvrier européen",
    "PT": "Parti des travailleurs",
    "P. REP": "Pôle républicain",
    "CAP 21": "Citoyenneté, action, participation pour le 21e siècle",
    "DL": "Démocratie libérale",
    "MNR": "Mouvement national républicain",
    "CNPT": "Chasse, pêche, nature et traditions",
    "FG": "Front de gauche",
    "EELV": "Europe Écologie Les Verts",
    "NPA": "Nouveau Parti anticapitaliste",
    "DLR": "Debout la République",
    "SP": "Solidarité et progrès",
    "LFI": "La France insoumise",
    "DLF": "Debout la France",
    "RES": "Résistons !",
    "UPR": "Union populaire républicaine",
    "REC": "Reconquête",
}
TENDENCIES_1981 = {
    "COM": "Parti communiste",
    "EXG": "Extrême gauche",
    "SOC": "Parti socialiste",
    "RADICAUX GAUCHE": "Mouvement des radicaux de gauche",
    "DVG": "Divers gauche",
    "UDF": "Union pour la démocratie française",
    "RPR": "Rassemblement pour la République",
    "DVD": "Divers droite",
    "UDF-RPR": "Union pour la nouvelle majorité",
    "FRN": "Front national",
    "EXD": "Extrême droite",
    "ECO": "Écologistes",
    "REG": "Régionalistes",
}
NUANCES = {
    "COM": "Parti communiste français",
    "SOC": "Parti socialiste",
    "RPR": "Rassemblement pour la République",
    "UDF": "Union pour la démocratie française",
    "FRN": "Front national",
    "DVD": "Divers droite",
    "DVG": "Divers gauche",
    "EXG": "Extrême gauche",
    "EXD": "Extrême droite",
    "ECO": "Écologistes",
    "REG": "Régionalistes",
    "RDG": "Mouvement des radicaux de gauche",
    "MAJ": "Majorité présidentielle",
    "EXO": "Autres",
    "DIV": "Divers",
    "VEC": "Les Verts",
    "GEC": "Génération écologie",
    "PRG": "Parti radical de gauche",
    "UMP": "Union pour un mouvement populaire",
    "MODEM": "Mouvement démocrate",
    "PSLE": "Nouveau Centre",
    "FDG": "Front de gauche",
    "NouvC": "Nouveau Centre",
    "PRV": "Parti radical",
    "MODEM-": "Centristes",
    "MPF": "Mouvement pour la France",
}
LATER_NUANCES = {
    2017: {
        "REM": "La République en marche",
        "FI": "La France insoumise",
        "FN": "Front national",
        "LR": "Les Républicains",
        "DLF": "Debout la France",
        "MDM": "Mouvement démocrate",
        "UDI": "Union des démocrates et indépendants",
        "RDG": "Parti radical de gauche",
    },
    2022: {
        "ENS": "Ensemble",
        "NUP": "Nouvelle Union populaire écologique et sociale",
        "RN": "Rassemblement national",
        "REC": "Reconquête",
        "LR": "Les Républicains",
        "DSV": "Droite souverainiste",
        "DXG": "Divers extrême gauche",
        "DXD": "Divers extrême droite",
        "DVC": "Divers centre",
        "UDI": "Union des démocrates et indépendants",
        "RDG": "Parti radical de gauche",
    },
}
NUANCE_FIXES = {
    1988: {
        "SCO": "MAJ",
        "SDC": "MAJ",
        "EXE": "EXO",
        "RFN": "FRN",
        "RRN": "FRN",
        "UOF": "UDF",
        "UDP": "UDF",
        "UDR": "UDF",
        "RDC": "RDG",
        "PRP": "RPR",
        "ECI": "ECO",
        "ROG": "DIV",
    }
}
VOTE_FIXES = {
    (1993, 1, "1801"): {"CAMUZAT MAXIME": 7716},
    (1993, 1, "1903"): {"CHIRAC JACQUES": 25951},
    (1993, 1, "3303"): {"COYCAUT MARIE-JOELLE": 841},
    (1993, 1, "3310"): {"ARCARAZ JEAN-LOUIS": 2767},
    (1993, 1, "5605"): {"LE DRIAN JEAN-YVES": 10385, "LE BAIL CYRIL": 776},
    (1993, 1, "7608"): {"FOUCHE-SAILLENFEST PHILIPPE": 5929},
    (1993, 1, "8403"): {"BOUYAUD GEORGES": 1307, "MACARY GUY": 11182},
    (1997, 1, "1310"): {"BARILLER DAMIEN": 15818},
    (1997, 1, "3502"): {"JAMBOIS STEPHANE": 2790, "MATHIEU BERTRAND": 1194},
    (2007, 1, "6904"): {
        "ARTHAUD NATHALIE": 266,
        "GAGLIONE RENAUD": 789,
        "PIERRON SYLVIE": 652,
        "VALLAUD-BELKACEM NAJAT": 11571,
        "ODIARD PATRICK": 1340,
        "VENDITELLI CLAUDETTE": 220,
        "LALLEMENT STEPHANE": 68,
        "CHINAL MARC": 5,
        "GAY ANNE-FRANCOISE": 275,
    },
    (1993, 2, "2501"): {"GIRARD CLAUDE": 22514},
    (1993, 2, "6701"): {"KOEHL EMILE": 7258},
    (1993, 2, "6902"): {"MERIEUX ALAIN": 15244},
    (1997, 2, "8402"): {"BOREL ANDRE": 31614, "ROUSSET ROUARD YVES": 29030, "REMOND SIMONE": 12514},
    (2007, 2, "0103"): {"RIGAUD Francoise": 17438, "BLANC Etienne": 26378},
    (2007, 2, "6202"): {"GENISSON Catherine": 27635, "ZIOLKOWSKI Michel": 21615},
    (2007, 2, "6203"): {"LEROY Jean-Claude": 30004, "MAGNIER Helene": 22553},
    (2007, 2, "6213"): {"DELCOURT Guy": 23574, "PERMUY Beatrice": 13137},
    (2007, 2, "8005"): {"KUMM Valerie": 18459, "DEMILLY Stephane": 22995},
    (2007, 2, "8404"): {"MEFFRE Pierre": 18835, "MARIANI Thierry": 28462},
    (2012, 1, "1403"): {
        "MUSSLE CHRISTOPHE": 272,
        "LETEURTRE CLAUDE": 15656,
        "ANNOOT CHRISTINE": 1063,
        "LIMAL CHRISTIAN": 0,
    },
    (2012, 1, "4903"): {
        "BENARD NATHALIE": 1029,
        "BERNARD CLAUDE": 459,
    },
    (2012, 1, "5105"): {
        "MONDOT DAMIEN": 176,
        "MAYANCE NATHALIE": 1387,
        "BITTERLIN CATHERINE": 178,
        "MELIN CLAUDE": 154,
        "ROMA LOUIS": 296,
        "PROCQUEZ CHRISTIAN": 0,
    },
    (2012, 1, "5919"): {
        "BOUCOT JACKY": 334,
        "ADAMS RAYMOND": 225,
        "LEFEBVRE MICHEL": 10137,
        "MARCHAND MARIE-CLAUDE": 1067,
        "HERBOMEL MANUEL": 361,
        "CAPRON OLIVIER": 6185,
        "PACHOLSKI MONIQUE": 0,
        "THOMES SERGE": 730,
        "DRICI DJEMAÏ": 387,
    },
    (2012, 1, "6709"): {
        "DELAYE JEAN-MICHEL": 229,
        "DIZDAREVIC GEORGES": 124,
        "CLAUS JEAN-MARC": 888,
        "CHABOD CLARISSE": 9196,
        "WERCKMANN FRANÇOISE": 1211,
        "THOMAS NICOLE": 11906,
        "MARKERT ARNAUD": 447,
        "STURNI CLAUDE": 14042,
        "ALTHERR JEAN-CLAUDE": 8437,
        "LUDWIG FRANÇOIS": 467,
        "DIARI MOUNIR": 75,
        "MEYER BENOÎT": 0,
    },
    (2012, 1, "6905"): {
        "BUGNI JIM": 143,
        "CHIAVAZZA VÉRONIQUE": 1945,
        "TOUTANT VÉRONIQUE": 2098,
        "DARNE JACKY": 14034,
        "RUSDIKIAN ERIC": 2,
        "SAVOYE-DI SPIRITO JOANNE": 903,
        "PROUHET KIM": 298,
        "BOUVAT CYRILLE": 414,
        "SARAFIAN LOUISE": 0,
        "CHEVALIER ALAIN": 5835,
        "COCHET PHILIPPE": 23486,
        "EKOBE ELEAZARD": 0,
    },
    (2012, 1, "9209"): {
        "JANISSET HÉLÈNE": 37,
        "KHALIL MINA": 28,
        "PINEAU DOROTHÉE": 1691,
        "GUEANT CLAUDE": 10978,
        "MARCEL JULIEN": 1076,
        "SOLERE THIERRY": 9709,
        "CRETEGNY GUILLAUME": 0,
    },
    (2012, 1, "9307"): {
        "XARDEL DAVID": 711,
        "COUËDIC MARC": 211,
    },
    (2012, 1, "9309"): {
        "AMZEL GEORGES": 687,
    },
    (2012, 2, "0101"): {"BRETON XAVIER": 24233},
    (2012, 2, "0102"): {"RAYMOND MICHEL": 19529},
    (2012, 2, "0103"): {"FOGNINI JEAN-MARC": 15653, "BLANC ETIENNE": 19266},
    (2012, 2, "0104"): {"VOISIN MICHEL": 26175},
    (2012, 2, "0105"): {"EXPOSITO JOSIANE": 17012},
    (2012, 2, "0203"): {"MEURA FRÉDÉRIC": 18301},
    (2012, 2, "0205"): {"BRIFFAUT FRANCK": 10205},
    (2012, 2, "0303"): {"CHARASSE GÉRARD": 26301},
    (2012, 2, "0401"): {"SAUVAN GILBERT": 20785},
    (2012, 2, "0605"): {"ESTROSI CHRISTIAN": 28019},
    (2012, 2, "0701"): {"GRANGIS CHRISTIAN": 13463},
    (2012, 2, "0702"): {"DUSSOPT OLIVIER": 28990},
    (2012, 2, "1001"): {"GAUDOT RENÉ": 12407, "SUBTIL BRUNO": 9226},
    (2012, 2, "1102"): {"FABRE MARIE-HÉLÈNE": 26262},
    (2012, 2, "1103"): {"DUPRE JEAN-PAUL": 32520, "BRESSON EMMANUEL": 19141},
    (2012, 2, "1203"): {"FOULQUIER MARIE-THÉRÈSE": 21302, "MARC ALAIN": 24962},
    (2012, 2, "1304"): {"AUCOUTURIER MARIE-CLAUDE": 8015},
    (2012, 2, "1305"): {"CARLOTTI MARIE-ARLETTE": 20212},
    (2012, 2, "1306"): {"TEISSIER GUY": 17101},
    (2012, 2, "1307"): {"MARANDAT BERNARD": 10292},
    (2012, 2, "1308"): {"ISNARD NICOLAS": 22013},
    (2012, 2, "1309"): {"DEFLESSELLES BERNARD": 25001},
    (2012, 2, "1401"): {"BRUNEAU JOËL": 17026},
    (2012, 2, "1403"): {"VALTER CLOTILDE": 23010},
    (2012, 2, "1404"): {"LE MARREC CLÉMENTINE": 25891},
    (2012, 2, "1405"): {"ATTARD ISABELLE": 25683},
    (2012, 2, "1406"): {"TOURRET ALAIN": 29570},
    (2012, 2, "1803"): {"GALUT YANN": 27646},
    (2012, 2, "2103"): {"BOUZIANE KHEIRA": 19948, "CARAVEL PASCALE": 17660},
    (2012, 2, "2104"): {"MOLINOZ PATRICK": 20273, "SAUVADET FRANÇOIS": 22900},
    (2012, 2, "2105"): {"SUGUENOT ALAIN": 26874},
    (2012, 2, "2301"): {"VERGNIER MICHEL": 35464},
    (2012, 2, "2501"): {"ROMAGNAN BARBARA": 22925, "BRANGET FRANÇOISE": 18964},
    (2012, 2, "2503"): {"MARTHEY ARNAUD": 18512},
    (2012, 2, "2504"): {"DEMOUGE CHARLES": 10260},
    (2012, 2, "2505"): {"LUCCHESI LILIANE": 15966},
    (2012, 2, "2601"): {"LABAUNE PATRICK": 21510},
    (2012, 2, "2602"): {"COUTARD CATHERINE": 24397, "REYNIER FRANCK": 25187},
    (2012, 2, "2604"): {"NIESON NATHALIE": 26953, "THORAVAL MARIE-HÉLÈNE": 24192},
    (2012, 2, "2801"): {"LEBON DAVID": 25012},
    (2012, 2, "2802"): {"MARLEIX OLIVIER": 20402},
    (2012, 2, "2905"): {"GUITTET CHANTAL": 30781},
    (2012, 2, "2A02"): {"DE ROCCA SERRA CAMILLE": 15983},
    (2012, 2, "3102"): {"BAPT GÉRARD": 32025},
    (2012, 2, "3103"): {"MOUDENC JEAN-LUC": 21653},
    (2012, 2, "3109"): {"BORGEL CHRISTOPHE": 24901},
    (2012, 2, "3110"): {"ARIF KADER": 30901},
    (2012, 2, "3301"): {"DOUCET SANDRINE": 24714, "BOURRAGUE CHANTAL": 23370},
    (2012, 2, "3305"): {"GORDON-KRIEF DAVID": 21201},
    (2012, 2, "3306"): {"MILLET THIERRY": 18947},
    (2012, 2, "3404"): {"LECOU ROBERT": 26021},
    (2012, 2, "3506"): {"FEUVRIER LOUIS": 24669, "BENOIT THIERRY": 25850},
    (2012, 2, "3701"): {"GILLE JEAN-PATRICK": 20310},
    (2012, 2, "3801"): {"FIORASO GENEVIÈVE": 26402},
    (2012, 2, "3802"): {"VICENTE MAGALIE": 13155},
    (2012, 2, "3805"): {"BERNARD MICHEL": 20205},
    (2012, 2, "3808"): {"BINET ERWANN": 20902},
    (2012, 2, "3901"): {"BRULEBOIS DANIELLE": 19193, "PELISSARD JACQUES": 20912},
    (2012, 2, "3902"): {"DALLOZ MARIE-CHRISTINE": 16915},
    (2012, 2, "3903"): {"LAROCHE SYLVIE": 19641},
    (2012, 2, "4001"): {"DUDON ALAIN": 22015},
    (2012, 2, "4101"): {"PERRUCHOT NICOLAS": 22216},
    (2012, 2, "4401"): {"DE RUGY FRANÇOIS": 23922, "PINTE FRANÇOIS": 16661},
    (2012, 2, "4402"): {"CLERGEAU MARIE-FRANÇOISE": 26208},
    (2012, 2, "4405"): {"MENARD MICHEL": 36042, "PERRION MAURICE": 23797},
    (2012, 2, "4406"): {"HUNAULT MICHEL": 29566},
    (2012, 2, "4410"): {"ERRANTE SOPHIE": 33582},
    (2012, 2, "4501"): {"GRAND JEAN-PHILIPPE": 20298},
    (2012, 2, "4503"): {"FROMENT PHILIPPE": 19170, "DE GANAY CLAUDE": 21378},
    (2012, 2, "4504"): {"GABORET JALILA": 17133},
    (2012, 2, "4506"): {"CORRE VALÉRIE": 19541},
    (2012, 2, "4601"): {"ORLIAC DOMINIQUE": 25847},
    (2012, 2, "4702"): {"FEKL MATTHIAS": 25049, "DIEFENBACHER MICHEL": 21641},
    (2012, 2, "4703"): {"CAHUZAC JÉRÔME": 28309, "COSTES JEAN-LOUIS": 17734},
    (2012, 2, "4801"): {"MOREL-A-L'HUISSIER PIERRE": 20138},
    (2012, 2, "4904"): {"SARAMITO SOPHIE": 18604},
    (2012, 2, "4907"): {"LAFFINEUR MARC": 21885},
    (2012, 2, "5001"): {"GOSSELIN PHILIPPE": 27049},
    (2012, 2, "5002"): {"HUET GUÉNHAËL": 31814},
    (2012, 2, "5103"): {"MARTIN PHILIPPE": 23135},
    (2012, 2, "5104"): {"NAMUR RUDY": 20260},
    (2012, 2, "5201"): {"ANDRIOT PATRICIA": 19879},
    (2012, 2, "5202"): {"MAILLOT DENIS": 13390, "CORNUT-GENTILLE FRANÇOIS": 20922},
    (2012, 2, "5605"): {"ROUILLARD GWENDAL": 26905, "MÉLIN BRIGITTE": 16246},
    (2012, 2, "5702"): {"TOULOUZE JEAN-MICHEL": 18875},
    (2012, 2, "5703"): {"PALLEZ CHRISTIANE": 17380, "ZIMMERMANN MARIE-JO": 20200},
    (2012, 2, "5705"): {"LETT CÉLESTE": 21721},
    (2012, 2, "5709"): {"MERTZ BERTRAND": 23449},
    (2012, 2, "5802"): {"BOUCOMONT MICHÈLE": 16538},
    (2012, 2, "5901"): {"ROMAN BERNARD": 17390, "MORELLE HERVÉ-MARIE": 9388},
    (2012, 2, "5903"): {"PAUVROS RÉMI": 23533, "MARIN CHRISTINE": 21569},
    (2012, 2, "5906"): {"DEFFONTAINE ANGÉLIQUE": 22148, "LAZARO THIERRY": 27050},
    (2012, 2, "5908"): {"BAERT DOMINIQUE": 18110},
    (2012, 2, "5909"): {"GERARD BERNARD": 26377},
    (2012, 2, "5917"): {"DOLEZ MARC": 19763},
    (2012, 2, "6001"): {"DASSAULT OLIVIER": 26702},
    (2012, 2, "6303"): {"AUROI DANIELLE": 26958, "GISCARD D'ESTAING LOUIS": 25979},
    (2012, 2, "6305"): {"CHASSAIGNE ANDRÉ": 37257, "COSTILHES MAXIME": 17947},
    (2012, 2, "6401"): {"LIGNIERES-CASSOU MARTINE": 20861, "PATRIARCHE NICOLAS": 15331},
    (2012, 2, "6402"): {"CHABANNE NATHALIE": 20090, "SAUBATTE ERIC": 12700},
    (2012, 2, "6405"): {"CAPDEVIELLE COLETTE": 27067, "GRENET JEAN": 20864},
    (2012, 2, "6406"): {"ALAUX SYLVIANE": 27648, "ALLIOT-MARIE MICHÈLE": 25908},
    (2012, 2, "6501"): {"GLAVANY JEAN": 33413, "TREMEGE GÉRARD": 16523},
    (2012, 2, "6707"): {"CARBIENER THIERRY": 16026},
    (2012, 2, "6709"): {"THOMAS NICOLE": 14002},
    (2012, 2, "6902"): {"MUET PIERRE-ALAIN": 23517, "HAMELIN EMMANUEL": 16924},
    (2012, 2, "6904"): {"BRUGNERA ANNE": 20173},
    (2012, 2, "6907"): {"COMPAN YANN": 11501},
    (2012, 2, "6909"): {"MEYER VINCENT": 16813, "PERRUT BERNARD": 27244},
    (2012, 2, "7001"): {"CHAUVELOT-DUBAN CLAUDY": 25962},
    (2012, 2, "7102"): {"GUEUGNEAU EDITH": 25124, "NESME JEAN-MARC": 22443},
    (2012, 2, "7104"): {"UNTERMAIER CÉCILE": 23810, "DANJEAN ARNAUD": 23431},
    (2012, 2, "7203"): {"PAVY-MORANCAIS BÉATRICE": 23256},
    (2012, 2, "7204"): {"LE FOLL STÉPHANE": 26019},
    (2012, 2, "7402"): {"DUPERTHUY DENIS": 20297},
    (2012, 2, "7506"): {"BOHBOT JACK-YVES": 10797},
    (2012, 2, "7508"): {"MAZETIER SANDRINE": 28473, "BEIGBEDER CHARLES": 17749},
    (2012, 2, "7509"): {"LE GUEN JEAN-MARIE": 24902},
    (2012, 2, "7511"): {"CHERKI PASCAL": 23901},
    (2012, 2, "7512"): {"EDOU CAPUCINE": 16697, "GOUJON PHILIPPE": 25420},
    (2012, 2, "7515"): {"FANFANT NATHALIE": 10247},
    (2012, 2, "7516"): {"CAMBADELIS JEAN-CHRISTOPHE": 22735},
    (2012, 2, "7517"): {"VAILLANT DANIEL": 19153},
    (2012, 2, "7518"): {"BOURNAZEL PIERRE-YVES": 10888},
    (2012, 2, "7603"): {"PANE LUCE": 19813},
    (2012, 2, "7702"): {"SARKISSIAN ROSELINE": 20150},
    (2012, 2, "7703"): {"INGHELBRECHT PATRICIA": 19202},
    (2012, 2, "7708"): {"BRUNEL CHANTAL": 20141},
    (2012, 2, "7801"): {"THIS SAINT-JEAN ISABELLE": 20230},
    (2012, 2, "7802"): {"LOLLIOZ JACQUES": 20212},
    (2012, 2, "7807"): {"RODES ESTELLE": 21146},
    (2012, 2, "7809"): {"SATOURI MOUNIR": 20192},
    (2012, 2, "7810"): {"POURSINOFF ANNY": 24224, "POISSON JEAN-FRÉDÉRIC": 28043},
    (2012, 2, "8306"): {"PONS JOSETTE": 29027},
    (2012, 2, "8501"): {"CHANTECAILLE MARTINE": 26402, "LEBOEUF ALAIN": 30005},
    (2012, 2, "8503"): {"GUEDON LOUIS": 22260},
    (2012, 2, "8505"): {"SARLOT JOËL": 23348},
    (2012, 2, "8602"): {"COUTELLE CATHERINE": 26485},
    (2012, 2, "8603"): {"CLEMENT JEAN-MICHEL": 26333, "DELANNOY ENGUERRAND": 16644},
    (2012, 2, "8604"): {"MASSONNEAU VÉRONIQUE": 21330},
    (2012, 2, "8802"): {"LANG JACK": 21480, "CHERPION GÉRARD": 22257},
    (2012, 2, "8803"): {"VANNSON FRANÇOIS": 20742},
    (2012, 2, "8804"): {"FRANQUEVILLE CHRISTIAN": 20699, "GAULTIER JEAN-JACQUES": 20393},
    (2012, 2, "9002"): {"BUTZBACH ETIENNE": 12886, "ZUMKELLER MICHEL": 13741},
    (2012, 2, "9103"): {"POUZOL MICHEL": 27202},
    (2012, 2, "9106"): {"LAMY FRANÇOIS": 23941},
    (2012, 2, "9109"): {"MANDON THIERRY": 23011},
    (2012, 2, "9210"): {"SANTINI ANDRÉ": 22202},
    (2012, 2, "9212"): {"GERMAIN JEAN-MARC": 28027},
    (2012, 2, "9305"): {"LATRECHE MILOUDA": 12301},
    (2012, 2, "9307"): {"HAMMADI RAZZY": 16122},
    (2012, 2, "9406"): {"ABEILLE LAURENCE": 22602},
    (2012, 2, "9502"): {"VUILLETET GUILLAUME": 20155},
    (2012, 2, "9504"): {"SEBAOUN GÉRARD": 20122},
    (2012, 2, "9508"): {"BLANCHET MARIE-FRANCE": 7201},
    (2012, 2, "9510"): {"LEFEBVRE DOMINIQUE": 19011},
    (2012, 2, "97101"): {"JALTON ERIC": 16027},
    (2012, 2, "97102"): {"LOUIS-CARABIN GABRIELLE": 20147},
    (2012, 2, "97201"): {"MANSCOUR LOUIS-JOSEPH": 13826},
    (2012, 2, "97202"): {"AZEROT BRUNO-NESTOR": 16821},
    (2012, 2, "97203"): {"CAROLE FRANCIS": 6437},
    (2012, 2, "97204"): {"NILOR JEAN-PHILIPPE": 20204},
    (2012, 2, "97403"): {"THIEN AH KOON ANDRÉ": 18012},
    (2012, 2, "98701"): {"FRITCH EDOUARD": 21921, "FREBAULT PIERRE": 12752},
    (2012, 2, "98702"): {"NEUFFER PHILIPPE": 14311},
}
ACCOUNTING_FIXES = {
    (1993, 1, "6402"): {VALID: 43265},
    (2012, 1, "9307"): {VALID: 35414, INVALID: 606},
    (2012, 1, "9309"): {VOTERS: 33192, VALID: 32566, INVALID: 626},
    (2012, 2, "0101"): {VALID: 46976, INVALID: 924},
    (2012, 2, "0102"): {REGISTERED: 86937, VOTERS: 50936, VALID: 50386, INVALID: 550},
    (2012, 2, "0103"): {VOTERS: 35809, INVALID: 890},
    (2012, 2, "0104"): {VALID: 45955, INVALID: 1250},
    (2012, 2, "0105"): {VOTERS: 40322, VALID: 39020, INVALID: 1302},
    (2012, 2, "0203"): {REGISTERED: 70285},
    (2012, 2, "0302"): {REGISTERED: 86702},
    (2012, 2, "0401"): {VALID: 35469, INVALID: 1882},
    (2012, 2, "0502"): {INVALID: 1022},
    (2012, 2, "0602"): {VALID: 44702},
    (2012, 2, "0701"): {REGISTERED: 76625, VOTERS: 44472, VALID: 41044},
    (2012, 2, "0702"): {REGISTERED: 90825, VALID: 54337, INVALID: 1335},
    (2012, 2, "0703"): {REGISTERED: 76071, VOTERS: 51077, INVALID: 1232},
    (2012, 2, "0801"): {VOTERS: 42301, VALID: 41026},
    (2012, 2, "0802"): {INVALID: 1025},
    (2012, 2, "0902"): {VOTERS: 35230, INVALID: 1535},
    (2012, 2, "1001"): {VOTERS: 39478, VALID: 38755, INVALID: 723},
    (2012, 2, "1003"): {VOTERS: 36707, INVALID: 1190},
    (2012, 2, "1102"): {VOTERS: 49044, VALID: 46215},
    (2012, 2, "1103"): {REGISTERED: 87655, VOTERS: 54333, INVALID: 2672},
    (2012, 2, "1203"): {VALID: 46264, INVALID: 1832},
    (2012, 2, "1301"): {VOTERS: 40240},
    (2012, 2, "1305"): {VOTERS: 40226, VALID: 39011},
    (2012, 2, "1306"): {VALID: 40281},
    (2012, 2, "1309"): {VOTERS: 45860, INVALID: 5896},
    (2012, 2, "1310"): {REGISTERED: 100187, VOTERS: 60217},
    (2012, 2, "1312"): {VALID: 50224},
    (2012, 2, "1315"): {REGISTERED: 98288, INVALID: 802},
    (2012, 2, "1401"): {REGISTERED: 70198, VALID: 39537, INVALID: 1395},
    (2012, 2, "1403"): {REGISTERED: 78453, VOTERS: 46359, INVALID: 1415},
    (2012, 2, "1404"): {REGISTERED: 97731, VOTERS: 56317, VALID: 54962, INVALID: 1355},
    (2012, 2, "1405"): {REGISTERED: 86425, VOTERS: 52179, VALID: 50643, INVALID: 1536},
    (2012, 2, "1406"): {VALID: 55510, INVALID: 1479},
    (2012, 2, "1501"): {INVALID: 1102},
    (2012, 2, "1702"): {VOTERS: 57702},
    (2012, 2, "1704"): {VOTERS: 52897, INVALID: 1617},
    (2012, 2, "1705"): {VOTERS: 62167, INVALID: 1604},
    (2012, 2, "1802"): {REGISTERED: 70120},
    (2012, 2, "1803"): {VALID: 49850, INVALID: 2368},
    (2012, 2, "2102"): {REGISTERED: 66189},
    (2012, 2, "2103"): {REGISTERED: 69194, VOTERS: 38866, VALID: 37608, INVALID: 1258},
    (2012, 2, "2104"): {REGISTERED: 69041, VOTERS: 44602, VALID: 43173, INVALID: 1429},
    (2012, 2, "2105"): {REGISTERED: 82948, VOTERS: 49056, VALID: 47782},
    (2012, 2, "2201"): {VOTERS: 52742, INVALID: 2004},
    (2012, 2, "2202"): {VOTERS: 60154, INVALID: 1819},
    (2012, 2, "2203"): {REGISTERED: 86353, INVALID: 1011},
    (2012, 2, "2204"): {VOTERS: 49986, INVALID: 2573},
    (2012, 2, "2205"): {REGISTERED: 102336, VOTERS: 63379, INVALID: 1918},
    (2012, 2, "2301"): {REGISTERED: 96734, VOTERS: 64218, VALID: 61621, INVALID: 2597},
    (2012, 2, "2401"): {INVALID: 1802},
    (2012, 2, "2402"): {VOTERS: 50101},
    (2012, 2, "2501"): {REGISTERED: 73014, VALID: 41889, INVALID: 1172},
    (2012, 2, "2503"): {REGISTERED: 65449, VALID: 37782, INVALID: 1568},
    (2012, 2, "2504"): {VOTERS: 39852, INVALID: 700},
    (2012, 2, "2505"): {VALID: 42617, INVALID: 1387},
    (2012, 2, "2601"): {VOTERS: 42802, VALID: 41467, INVALID: 1335},
    (2012, 2, "2602"): {VALID: 49584, INVALID: 1734},
    (2012, 2, "2603"): {REGISTERED: 102603},
    (2012, 2, "2604"): {REGISTERED: 90017, VALID: 51145, INVALID: 1378},
    (2012, 2, "2701"): {REGISTERED: 85364},
    (2012, 2, "2705"): {VOTERS: 43582, VALID: 36902, INVALID: 6680},
    (2012, 2, "2801"): {REGISTERED: 87301},
    (2012, 2, "2903"): {REGISTERED: 86265, VOTERS: 53021},
    (2012, 2, "2905"): {VOTERS: 55241, VALID: 53888},
    (2012, 2, "2A02"): {REGISTERED: 53733, VOTERS: 32333, VALID: 30104, INVALID: 2229},
    (2012, 2, "2B01"): {VOTERS: 35837, INVALID: 906},
    (2012, 2, "2B02"): {REGISTERED: 63936},
    (2012, 2, "3103"): {VALID: 42953, INVALID: 1194},
    (2012, 2, "3105"): {REGISTERED: 90350},
    (2012, 2, "3106"): {VOTERS: 57964, INVALID: 2361},
    (2012, 2, "3109"): {VALID: 38490, INVALID: 1876},
    (2012, 2, "3202"): {REGISTERED: 70130},
    (2012, 2, "3301"): {REGISTERED: 86026},
    (2012, 2, "3304"): {REGISTERED: 86704, VOTERS: 42715, INVALID: 1852},
    (2012, 2, "3305"): {REGISTERED: 102509},
    (2012, 2, "3306"): {VALID: 51810, INVALID: 1370},
    (2012, 2, "3308"): {VOTERS: 60603, INVALID: 1810},
    (2012, 2, "3309"): {REGISTERED: 85657},
    (2012, 2, "3310"): {REGISTERED: 78529},
    (2012, 2, "3311"): {REGISTERED: 88185},
    (2012, 2, "3312"): {REGISTERED: 78612},
    (2012, 2, "3401"): {VOTERS: 44690, INVALID: 2070},
    (2012, 2, "3402"): {REGISTERED: 58306, INVALID: 1012},
    (2012, 2, "3404"): {VOTERS: 60679, INVALID: 2352},
    (2012, 2, "3407"): {VOTERS: 62234, INVALID: 1076},
    (2012, 2, "3408"): {VOTERS: 45733, INVALID: 1993},
    (2012, 2, "3409"): {VOTERS: 43214, INVALID: 1674},
    (2012, 2, "3503"): {VOTERS: 48607, INVALID: 1336},
    (2012, 2, "3504"): {VOTERS: 46611, INVALID: 1523},
    (2012, 2, "3506"): {VALID: 50519, INVALID: 1801},
    (2012, 2, "3507"): {VOTERS: 57649, INVALID: 1528},
    (2012, 2, "3601"): {REGISTERED: 82183},
    (2012, 2, "3701"): {VALID: 34896, INVALID: 1093},
    (2012, 2, "3703"): {INVALID: 2002},
    (2012, 2, "3802"): {REGISTERED: 75160, VALID: 37960, INVALID: 1138},
    (2012, 2, "3805"): {REGISTERED: 97930, VOTERS: 54331, INVALID: 1364},
    (2012, 2, "3806"): {VOTERS: 40712, INVALID: 1138},
    (2012, 2, "3809"): {REGISTERED: 93414, VOTERS: 52233, INVALID: 1820},
    (2012, 2, "3810"): {REGISTERED: 87761, VOTERS: 45979, INVALID: 1479},
    (2012, 2, "3901"): {REGISTERED: 65053, VOTERS: 41305, VALID: 40105, INVALID: 1200},
    (2012, 2, "3902"): {VALID: 30975, INVALID: 961},
    (2012, 2, "3903"): {VALID: 41944, INVALID: 1423},
    (2012, 2, "4101"): {REGISTERED: 80494, VALID: 46188, INVALID: 1503},
    (2012, 2, "4102"): {REGISTERED: 80287, VOTERS: 48379, INVALID: 1614},
    (2012, 2, "4205"): {REGISTERED: 101386},
    (2012, 2, "4301"): {REGISTERED: 95799},
    (2012, 2, "4302"): {REGISTERED: 80283},
    (2012, 2, "4401"): {VALID: 40583, INVALID: 935},
    (2012, 2, "4402"): {VALID: 42119, INVALID: 932},
    (2012, 2, "4405"): {VALID: 59839, INVALID: 1423},
    (2012, 2, "4406"): {VOTERS: 64431, VALID: 62488, INVALID: 1943},
    (2012, 2, "4407"): {REGISTERED: 102050},
    (2012, 2, "4409"): {INVALID: 2010},
    (2012, 2, "4410"): {VALID: 63944, INVALID: 1391},
    (2012, 2, "4501"): {REGISTERED: 73258},
    (2012, 2, "4502"): {VOTERS: 48702},
    (2012, 2, "4504"): {REGISTERED: 75006, VALID: 40611, INVALID: 1678},
    (2012, 2, "4505"): {VALID: 40183},
    (2012, 2, "4506"): {VALID: 38971, INVALID: 1346},
    (2012, 2, "4601"): {REGISTERED: 70187, VOTERS: 45010, VALID: 43189, INVALID: 1821},
    (2012, 2, "4701"): {REGISTERED: 86731},
    (2012, 2, "4702"): {REGISTERED: 77682, VALID: 46690, INVALID: 2170},
    (2012, 2, "4703"): {VOTERS: 47871, VALID: 46043, INVALID: 1828},
    (2012, 2, "4801"): {VOTERS: 41416, VALID: 39864},
    (2012, 2, "4902"): {REGISTERED: 87426},
    (2012, 2, "4904"): {VOTERS: 42701, VALID: 41498, INVALID: 1203},
    (2012, 2, "4906"): {REGISTERED: 92553},
    (2012, 2, "4907"): {VALID: 43685, INVALID: 1150},
    (2012, 2, "5001"): {VALID: 51489, INVALID: 1121},
    (2012, 2, "5002"): {VALID: 52400, INVALID: 2488},
    (2012, 2, "5003"): {REGISTERED: 105839},
    (2012, 2, "5101"): {REGISTERED: 69250},
    (2012, 2, "5102"): {REGISTERED: 72605},
    (2012, 2, "5103"): {REGISTERED: 81307, VOTERS: 42979, VALID: 41477, INVALID: 1502},
    (2012, 2, "5104"): {REGISTERED: 80933, VOTERS: 43967, INVALID: 1325},
    (2012, 2, "5105"): {REGISTERED: 78225, VOTERS: 44122, INVALID: 1230},
    (2012, 2, "5201"): {VALID: 44225, INVALID: 1367},
    (2012, 2, "5202"): {VALID: 34312, INVALID: 1133},
    (2012, 2, "5301"): {VALID: 40281},
    (2012, 2, "5402"): {VALID: 40261},
    (2012, 2, "5602"): {VOTERS: 62937, INVALID: 1365},
    (2012, 2, "5604"): {REGISTERED: 102159, VOTERS: 63206, INVALID: 1945},
    (2012, 2, "5605"): {VOTERS: 44846, VALID: 43151, INVALID: 1695},
    (2012, 2, "5702"): {REGISTERED: 76230, VALID: 38409, INVALID: 1348},
    (2012, 2, "5703"): {VALID: 37580, INVALID: 1205},
    (2012, 2, "5704"): {REGISTERED: 81175, VOTERS: 47300, INVALID: 731},
    (2012, 2, "5705"): {VALID: 36055, INVALID: 1785},
    (2012, 2, "5707"): {REGISTERED: 91559, VOTERS: 49638, INVALID: 696},
    (2012, 2, "5708"): {REGISTERED: 91179},
    (2012, 2, "5709"): {REGISTERED: 97022, VALID: 49968, INVALID: 1216},
    (2012, 2, "5802"): {VOTERS: 50106, VALID: 48009, INVALID: 2097},
    (2012, 2, "5903"): {VOTERS: 47873, VALID: 45102, INVALID: 2771},
    (2012, 2, "5906"): {REGISTERED: 84777, VOTERS: 50851, INVALID: 1653},
    (2012, 2, "5908"): {VALID: 26029, INVALID: 2037},
    (2012, 2, "5909"): {VALID: 43095, INVALID: 1086},
    (2012, 2, "5912"): {VOTERS: 51017},
    (2012, 2, "5914"): {REGISTERED: 98795},
    (2012, 2, "5917"): {VOTERS: 26896, VALID: 19763},
    (2012, 2, "5918"): {REGISTERED: 92017},
    (2012, 2, "5919"): {VOTERS: 26726, INVALID: 6853},
    (2012, 2, "6001"): {VALID: 46001},
    (2012, 2, "6005"): {VALID: 38601},
    (2012, 2, "6102"): {VOTERS: 40208},
    (2012, 2, "6201"): {VALID: 60195},
    (2012, 2, "6206"): {REGISTERED: 89021, VALID: 50287},
    (2012, 2, "6212"): {INVALID: 3302},
    (2012, 2, "6302"): {REGISTERED: 86222},
    (2012, 2, "6303"): {REGISTERED: 86560, VOTERS: 54203, INVALID: 1266},
    (2012, 2, "6305"): {REGISTERED: 100234, VOTERS: 57275, VALID: 55204, INVALID: 2071},
    (2012, 2, "6401"): {VOTERS: 38483, VALID: 36192, INVALID: 2291},
    (2012, 2, "6501"): {REGISTERED: 88431, VOTERS: 52140, VALID: 49936, INVALID: 2204},
    (2012, 2, "6603"): {REGISTERED: 80123},
    (2012, 2, "6707"): {INVALID: 3019},
    (2012, 2, "6709"): {INVALID: 3201},
    (2012, 2, "6801"): {REGISTERED: 74091},
    (2012, 2, "6802"): {REGISTERED: 91136},
    (2012, 2, "6804"): {REGISTERED: 99346},
    (2012, 2, "6806"): {REGISTERED: 92477},
    (2012, 2, "6902"): {VALID: 40441, INVALID: 810},
    (2012, 2, "6905"): {REGISTERED: 80551},
    (2012, 2, "6906"): {VOTERS: 38069, INVALID: 1228},
    (2012, 2, "6909"): {VALID: 44057, INVALID: 1234},
    (2012, 2, "6911"): {VOTERS: 41247, INVALID: 6649},
    (2012, 2, "6912"): {VOTERS: 43759, INVALID: 898},
    (2012, 2, "6913"): {VOTERS: 43525, INVALID: 1233},
    (2012, 2, "6914"): {REGISTERED: 70147},
    (2012, 2, "7001"): {VALID: 55650, INVALID: 2124},
    (2012, 2, "7101"): {REGISTERED: 72448},
    (2012, 2, "7102"): {REGISTERED: 81295},
    (2012, 2, "7103"): {REGISTERED: 83724, VOTERS: 50145, INVALID: 1956},
    (2012, 2, "7104"): {REGISTERED: 81128, VALID: 47241, INVALID: 1530},
    (2012, 2, "7105"): {INVALID: 1902},
    (2012, 2, "7203"): {VOTERS: 50476, VALID: 48897, INVALID: 1579},
    (2012, 2, "7204"): {VOTERS: 45181, INVALID: 1413},
    (2012, 2, "7205"): {VOTERS: 52474, VALID: 51001, INVALID: 1473},
    (2012, 2, "7301"): {REGISTERED: 82250},
    (2012, 2, "7303"): {VOTERS: 41475, VALID: 40297, INVALID: 1178},
    (2012, 2, "7304"): {VOTERS: 39011},
    (2012, 2, "7401"): {REGISTERED: 93143},
    (2012, 2, "7402"): {REGISTERED: 90269},
    (2012, 2, "7405"): {REGISTERED: 89907, INVALID: 1601},
    (2012, 2, "7505"): {VOTERS: 40108},
    (2012, 2, "7506"): {VALID: 38806, INVALID: 1725},
    (2012, 2, "7507"): {VALID: 41029},
    (2012, 2, "7509"): {VALID: 36155, INVALID: 1238},
    (2012, 2, "7511"): {VALID: 42322, INVALID: 922},
    (2012, 2, "7512"): {VALID: 42117, INVALID: 674},
    (2012, 2, "7516"): {VALID: 32460, INVALID: 1326},
    (2012, 2, "7517"): {VALID: 26293, INVALID: 819},
    (2012, 2, "7518"): {VALID: 35356, INVALID: 875},
    (2012, 2, "7601"): {REGISTERED: 64760},
    (2012, 2, "7602"): {REGISTERED: 91508},
    (2012, 2, "7603"): {REGISTERED: 69260, VOTERS: 25327, VALID: 19813, INVALID: 5514},
    (2012, 2, "7605"): {REGISTERED: 94283, VOTERS: 51704, VALID: 50102, INVALID: 1602},
    (2012, 2, "7606"): {REGISTERED: 110291},
    (2012, 2, "7609"): {REGISTERED: 92401},
    (2012, 2, "7610"): {REGISTERED: 105877, VOTERS: 67210, INVALID: 1715},
    (2012, 2, "7707"): {VOTERS: 44301},
    (2012, 2, "7803"): {VOTERS: 45716, INVALID: 1501},
    (2012, 2, "7807"): {VOTERS: 43682, VALID: 42727},
    (2012, 2, "7810"): {REGISTERED: 86779, VALID: 52267, INVALID: 1249},
    (2012, 2, "7812"): {REGISTERED: 66387, VOTERS: 39052, INVALID: 1096},
    (2012, 2, "8004"): {VALID: 50146},
    (2012, 2, "8102"): {REGISTERED: 102421},
    (2012, 2, "8103"): {REGISTERED: 98450},
    (2012, 2, "8305"): {REGISTERED: 91027},
    (2012, 2, "8307"): {REGISTERED: 101226},
    (2012, 2, "8402"): {REGISTERED: 80412},
    (2012, 2, "8403"): {VOTERS: 45699, INVALID: 753},
    (2012, 2, "8404"): {VOTERS: 52978, INVALID: 2379},
    (2012, 2, "8501"): {REGISTERED: 102173, VALID: 56407, INVALID: 1536},
    (2012, 2, "8503"): {VALID: 48619, INVALID: 7592},
    (2012, 2, "8505"): {VALID: 48081, INVALID: 1517},
    (2012, 2, "8601"): {VALID: 40302},
    (2012, 2, "8602"): {REGISTERED: 75571, VALID: 41877, INVALID: 1413},
    (2012, 2, "8603"): {VOTERS: 44565, VALID: 42977, INVALID: 1588},
    (2012, 2, "8604"): {REGISTERED: 75944, VALID: 42099, INVALID: 1695},
    (2012, 2, "8802"): {VALID: 43737, INVALID: 1971},
    (2012, 2, "8803"): {VOTERS: 40703, VALID: 39364},
    (2012, 2, "8804"): {VALID: 41092, INVALID: 1794},
    (2012, 2, "8902"): {REGISTERED: 77710, VOTERS: 46013, INVALID: 1689},
    (2012, 2, "9002"): {REGISTERED: 47774, VOTERS: 28139, VALID: 26627, INVALID: 1512},
    (2012, 2, "9101"): {REGISTERED: 70356},
    (2012, 2, "9102"): {VOTERS: 49221, INVALID: 1277},
    (2012, 2, "9104"): {VOTERS: 58301, INVALID: 1314},
    (2012, 2, "9106"): {VALID: 41441, INVALID: 1427},
    (2012, 2, "9107"): {VOTERS: 39000, INVALID: 1274},
    (2012, 2, "9108"): {VOTERS: 43078, INVALID: 747},
    (2012, 2, "9109"): {VOTERS: 42415, INVALID: 1868},
    (2012, 2, "9201"): {VOTERS: 20173},
    (2012, 2, "9205"): {VOTERS: 40190},
    (2012, 2, "9208"): {REGISTERED: 67019},
    (2012, 2, "9209"): {REGISTERED: 61020},
    (2012, 2, "9213"): {VOTERS: 55019},
    (2012, 2, "9302"): {REGISTERED: 51767},
    (2012, 2, "9304"): {REGISTERED: 60162},
    (2012, 2, "9306"): {VOTERS: 18002},
    (2012, 2, "9307"): {REGISTERED: 71209, VALID: 16122, INVALID: 6401},
    (2012, 2, "9308"): {REGISTERED: 60128},
    (2012, 2, "9310"): {VOTERS: 32593, INVALID: 931},
    (2012, 2, "9311"): {VOTERS: 20402},
    (2012, 2, "9410"): {REGISTERED: 63302},
    (2012, 2, "9501"): {VOTERS: 45951, INVALID: 1117},
    (2012, 2, "9502"): {VOTERS: 41906, VALID: 41018, INVALID: 888},
    (2012, 2, "9503"): {REGISTERED: 88016, VOTERS: 47777, INVALID: 1314},
    (2012, 2, "9504"): {REGISTERED: 72614, VOTERS: 41014},
    (2012, 2, "9510"): {VOTERS: 31615, INVALID: 974},
    (2012, 2, "97201"): {VALID: 29064, INVALID: 1987},
    (2012, 2, "97202"): {REGISTERED: 81694, VALID: 30317, INVALID: 2570},
    (2012, 2, "97203"): {REGISTERED: 65816, VALID: 21625, INVALID: 1565},
    (2012, 2, "97204"): {REGISTERED: 79003},
    (2012, 2, "97601"): {REGISTERED: 35601},
    (2012, 2, "97602"): {REGISTERED: 42001},
    (2012, 2, "98701"): {REGISTERED: 67136, VOTERS: 36059, VALID: 34673, INVALID: 1386},
    (2012, 2, "98702"): {VOTERS: 31635, VALID: 30724},
    (2012, 2, "ZZ03"): {INVALID: 502},
}
EXTRA_CANDIDATES = {
    (1997, 1, "7502"): [
        ("CARPENTIER", "ANNE", "F", "DIV", 27, 299),
        ("FUR", "ALEXANDRE", "M", "DIV", 28, 14),
        ("LANCON", "CHRISTIAN", "M", "DIV", 29, 1),
    ],
    (2002, 1, "7501"): [("OUANEZAR", "SOUAD", "F", "DIV", 27, 229)],
    (2012, 1, "7508"): [("HILLEREAU", "LILIANE", "F", "DIV", 23, 74)],
}
RELABELS = {
    (1993, 2, "2901", "RAINERO PIERO"): ("POIGNANT", "BERNARD", "SOC"),
}
CORRECTED = {key[:3] for key in (*VOTE_FIXES, *ACCOUNTING_FIXES, *EXTRA_CANDIDATES, *RELABELS)}
NOTES = {
    "legi": (
        "Results by constituency, compiled by the Banque de données socio-politiques in Grenoble and Sciences Po's "
        "CDSP from the Interior Ministry's printed returns. Before 1988 votes are grouped by political tendency "
        "rather than by candidate, and before 1988 the files cover metropolitan France only. The 1986 election, held "
        "by list proportional representation in each département, is not included."
    ),
    "presi": (
        "Results by constituency, compiled by the Banque de données socio-politiques in Grenoble and Sciences Po's "
        "CDSP. Until 1995 the files cover metropolitan France only; 2002 adds the overseas départements and "
        "territories, and 2007 and 2012 add voters abroad. Here they are summed by département."
    ),
    "aggregated": (
        "Results by polling station for every election since 1999, harmonized by data.gouv.fr from the Interior "
        "Ministry's files. Here the 2017 and 2022 presidential and legislative elections are summed by département "
        "and by constituency."
    ),
}
WIKIDATA_TERMS = {1997: "Q3570394", 2002: "Q3570376"}
WIKIDATA_QUERY = (
    "https://query.wikidata.org/sparql?format=json&query="
    "SELECT%20%3Fp%20%3FpLabel%20%3Fparty%20%3FpartyLabel%20WHERE%20%7B%20%3Fp%20p%3AP39%20%3Fs%20.%20"
    "%3Fs%20ps%3AP39%20wd%3AQ3044918%20%3B%20pq%3AP2937%20wd%3A{term}%20.%20"
    "OPTIONAL%7B%3Fp%20wdt%3AP102%20%3Fparty%7D%20"
    "SERVICE%20wikibase%3Alabel%20%7B%20bd%3AserviceParam%20wikibase%3Alanguage%20%22fr%2Cen%22.%20%7D%20%7D"
)
WIKIDATA_TITLE = "Wikidata: deputies of the 11th and 12th legislatures and their parties"
ASSOCIATION_CAVEAT = (
    "Candidates who ran under the joint label of the Socialist Party and the Radical Party of the Left are coded as "
    "Socialist, except winners whom Wikidata's records name as a member of the Radical Party of the Left alone; the "
    "source does not separate the two parties for the label's losing candidates."
)
TENDENCY_CAVEAT = (
    "The source groups votes by political tendency and does not name the candidates. Where two candidates of one "
    "tendency stood in a constituency, their votes are combined, and the winner is the tendency with the most votes."
)
METROPOLITAN_CAVEAT = "Metropolitan France only: the overseas départements and territories are not in the source."
UNSETTLED_CAVEAT = "No candidate won a majority, and the source has no second round for this constituency."
NO_ABROAD_CAVEAT = "French voters abroad are not in the source."
COUNCIL_METHOD = (
    "National totals as the Constitutional Council declared them, covering metropolitan France, the overseas "
    "départements and territories, and from 1981 French voters abroad: the first round from its declaration of the "
    "first-round results, the second from its proclamation of the result. Invalid ballots are voters minus valid "
    "votes. The département breakdown is CDSP's, which leaves out the votes outside metropolitan France before 2002 "
    "and voters abroad in 2002."
)
PRESIDENT = (
    "The president is elected by direct universal suffrage. A candidate who wins more than half of the valid votes in "
    "the first round is elected; otherwise the two leading candidates go to a second round two weeks later, and the "
    "one with more votes wins."
)
FIRST_ROUND = (
    "A candidate wins in the first round with more than half of the valid votes, provided that is at least a quarter "
    "of registered voters."
)
SECOND_ROUND = "The candidate with the most votes in the second round wins; in a tie, the older candidate wins."
THRESHOLDS = {
    1958: "Any first-round candidate with at least 5 percent of the valid votes could stand in the second round.",
    1967: "Any first-round candidate with at least 10 percent of registered voters could stand in the second round.",
    1978: "Any first-round candidate with at least 12.5 percent of registered voters could stand in the second round.",
}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]+", " ", plain.upper()).strip()


def fetch() -> None:
    for term in WIKIDATA_TERMS.values():
        raw.fetch(SOURCE, WIKIDATA_QUERY.format(term=term), suffix=".json")
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    for url in AGGREGATED.values():
        raw.fetch(SOURCE, url, suffix=".parquet")
    for decisions in COUNCIL.values():
        for decision in decisions:
            raw.fetch(SOURCE, COUNCIL_URL.format(decision), suffix=".html")


def parse() -> None:
    accounting: list[pl.DataFrame] = []
    options: list[pl.DataFrame] = []
    for kind, url in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            members = [
                (member, match)
                for member in sorted(archive.namelist())
                if (match := re.search(r"cdsp_(?:legi|presi)(\d{4})t([12])_circ\.csv$", member))
            ]
            for member, match in members:
                year, number = int(match[1]), int(match[2])
                frame = pl.read_csv(archive.read(member), infer_schema=False, truncate_ragged_lines=True)
                columns = frame.columns
                abbreviations = frame.row(0) if frame[columns[0]][0] is None else None
                frame = frame.rename({columns[0]: "dept", columns[1]: "dept_label", columns[2]: "circ"}).filter(
                    pl.col("dept").str.strip_chars().fill_null("") != ""
                )
                if "élu premier tour" in frame.columns:
                    frame = frame.filter(pl.col("élu premier tour") == "N")
                places = {
                    f"{code}|{label}": OVERSEAS[fold(label)]
                    if code.strip()[:2] in ("97", "98", "99")
                    else code.strip().zfill(2)
                    for code, label in frame.select("dept", "dept_label").unique().iter_rows()
                }
                frame = frame.with_columns(
                    dept=pl.concat_str("dept", pl.lit("|"), "dept_label").replace_strict(places),
                    circ=pl.col("circ").str.strip_chars(),
                ).with_columns(
                    circ=pl.when(pl.lit(kind == "presi"))
                    .then(pl.lit(None, pl.String))
                    .otherwise(pl.col("dept") + pl.col("circ").str.zfill(2))
                )
                keys = [
                    pl.lit(kind).alias("kind"),
                    pl.lit(year).alias("year"),
                    pl.lit(number).alias("round"),
                    "dept",
                    "circ",
                ]
                present = [column for column in ACCOUNTING if column in frame.columns]
                accounting.append(
                    frame.unpivot(on=present, index=["dept", "circ"], variable_name="field").select(
                        *keys,
                        measure=pl.col("field").replace_strict(ACCOUNTING, return_dtype=pl.Int16),
                        value=pl.col("value").str.replace_all(r"\s", "").cast(pl.Int64),
                    )
                )
                slots = sorted({int(c.split(" ")[0]) for c in frame.columns if re.fullmatch(r"\d+ Nom candidat", c)})
                if slots:
                    blocks = []
                    for slot in slots:
                        blocks.append(
                            frame.select(
                                *keys,
                                slot=pl.lit(slot),
                                sex=pl.col(f"{slot} Sexe candidat")
                                if f"{slot} Sexe candidat" in frame.columns
                                else pl.lit(None, pl.String),
                                surname=pl.col(f"{slot} Nom candidat").str.strip_chars(),
                                forename=pl.col(f"{slot} Prénom candidat").str.strip_chars(),
                                etiquette=pl.col(f"{slot} Etiquette liste").str.strip_chars(),
                                nuance=pl.col(f"{slot} nuance").str.strip_chars(),
                                votes=pl.col(f"{slot} voix").str.replace_all(r"\s", "").cast(pl.Int64),
                                elected=pl.col(f"{slot} Elu") == "O"
                                if f"{slot} Elu" in frame.columns
                                else pl.lit(None, pl.Boolean),
                            ).filter(pl.col("surname").fill_null("") != "")
                        )
                    options.append(
                        pl.concat(blocks).with_columns(
                            nuance=pl.col("nuance").replace(NUANCE_FIXES.get(year, {})),
                            label=pl.col("surname") + " " + pl.col("forename").fill_null(""),
                        )
                    )
                    continue
                first = (
                    columns.index("Blancs et nuls") + 1
                    if "Blancs et nuls" in columns
                    else columns.index("Exprimés") + 1
                )
                rows = []
                for position, column in enumerate(columns[first:], start=first):
                    if column in SKIPPED:
                        continue
                    if kind == "presi":
                        if "(" in column:
                            person, party = column.rsplit("(", 1)
                        elif column.endswith(")"):
                            person, party = column.rsplit(" ", 1)
                        else:
                            person, party = column, ""
                        label, abbreviation = CANDIDATES[person.strip()], party.strip(" )")
                    elif abbreviations is not None:
                        label, abbreviation = column.strip(), str(abbreviations[position]).strip()
                    else:
                        label, abbreviation = TENDENCIES_1981[column], column
                    rows.append(
                        frame.select(
                            *keys,
                            slot=pl.lit(position),
                            label=pl.lit(label),
                            abbreviation=pl.lit(abbreviation),
                            votes=pl.col(column).str.replace_all(r"\s", "").cast(pl.Int64),
                        )
                    )
                options.append(pl.concat(rows))
    elections = {
        f"{year}_{'pres' if kind == 'presi' else kind}_t{number}": (kind, year, number)
        for kind in ("legi", "presi")
        for year in (2017, 2022)
        for number in (1, 2)
    }
    general = (
        pl.scan_parquet(raw.cached(SOURCE, AGGREGATED["general"]).path)
        .filter(pl.col("id_election").is_in(list(elections)))
        .collect()
    )
    candidates = (
        pl.scan_parquet(raw.cached(SOURCE, AGGREGATED["candidates"]).path)
        .filter(pl.col("id_election").is_in(list(elections)))
        .collect()
    )
    codes = {
        "ZA": "971",
        "ZB": "972",
        "ZC": "973",
        "ZD": "974",
        "ZS": "975",
        "ZM": "976",
        "ZW": "986",
        "ZP": "987",
        "ZN": "988",
    }
    general = general.with_columns(pl.col("code_departement").replace(codes))
    candidates = candidates.with_columns(pl.col("code_departement").replace(codes))
    circs = general.select(
        "id_election", "id_brut_miom", circ=pl.col("code_departement") + pl.col("code_circonscription")
    )
    for election, (kind, year, number) in elections.items():
        keys = [
            pl.lit(kind).alias("kind"),
            pl.lit(year).alias("year"),
            pl.lit(number).alias("round"),
            pl.col("code_departement").alias("dept"),
            (pl.lit(None, pl.String) if kind == "presi" else pl.col("circ")).alias("circ"),
        ]
        rows = general.filter(pl.col("id_election") == election).with_columns(
            circ=pl.col("code_departement") + pl.col("code_circonscription")
        )
        accounting.append(
            rows.unpivot(
                on=["inscrits", "votants", "exprimes", "blancs", "nuls"],
                index=["code_departement", "circ"],
                variable_name="field",
            ).select(
                *keys,
                measure=pl.col("field").replace_strict(
                    {"inscrits": REGISTERED, "votants": VOTERS, "exprimes": VALID, "blancs": BLANK, "nuls": INVALID},
                    return_dtype=pl.Int16,
                ),
                value=pl.col("value").cast(pl.Int64),
            )
        )
        lines = (
            candidates.filter(pl.col("id_election") == election)
            .join(circs, on=["id_election", "id_brut_miom"], how="left")
            .with_columns(display=pl.col("prenom") + " " + pl.col("nom").str.to_titlecase())
        )
        options.append(
            lines.select(
                *keys,
                slot=pl.col("no_panneau").cast(pl.Int32),
                label=pl.col("display") if kind == "presi" else pl.col("nom") + " " + pl.col("prenom"),
                abbreviation=pl.col("display").replace_strict(LATER_PARTIES, default=None)
                if kind == "presi"
                else pl.lit(None, pl.String),
                sex="sexe",
                surname="nom",
                forename="prenom",
                etiquette=pl.lit(None, pl.String),
                nuance=pl.col("nuance") if kind == "legi" else pl.lit(None, pl.String),
                votes=pl.col("voix").cast(pl.Int64),
                elected=pl.lit(None, pl.Boolean),
            )
        )
    fixed = pl.DataFrame(
        [
            (year, number, circ, measure, value)
            for (year, number, circ), fix in ACCOUNTING_FIXES.items()
            for measure, value in fix.items()
        ],
        schema={"year": pl.Int32, "round": pl.Int32, "circ": pl.String, "measure": pl.Int16, "fixed": pl.Int64},
        orient="row",
    )
    values = (
        pl.concat(accounting)
        .join(fixed, on=["year", "round", "circ", "measure"], how="left")
        .with_columns(value=pl.coalesce("fixed", "value"))
        .drop("fixed")
    )
    standing = pl.concat(options, how="diagonal")
    fixes = pl.DataFrame(
        [
            (year, number, circ, label, votes)
            for (year, number, circ), fix in VOTE_FIXES.items()
            for label, votes in fix.items()
        ],
        schema={"year": pl.Int32, "round": pl.Int32, "circ": pl.String, "label": pl.String, "fixed": pl.Int64},
        orient="row",
    )
    missing = fixes.join(standing, on=["year", "round", "circ", "label"], how="anti")
    if missing.height:
        raise ValueError(f"vote fixes without a candidate: {missing.rows()}")
    standing = (
        standing.join(fixes, on=["year", "round", "circ", "label"], how="left")
        .with_columns(votes=pl.coalesce("fixed", "votes"))
        .drop("fixed")
    )
    extras = pl.DataFrame(
        [
            (
                "legi",
                year,
                number,
                circ[:2],
                circ,
                f"{surname} {forename}",
                slot,
                votes,
                sex,
                surname,
                forename,
                nuance,
            )
            for (year, number, circ), people in EXTRA_CANDIDATES.items()
            for surname, forename, sex, nuance, slot, votes in people
        ],
        schema={
            "kind": pl.String,
            "year": pl.Int32,
            "round": pl.Int32,
            "dept": pl.String,
            "circ": pl.String,
            "label": pl.String,
            "slot": pl.Int32,
            "votes": pl.Int64,
            "sex": pl.String,
            "surname": pl.String,
            "forename": pl.String,
            "nuance": pl.String,
        },
        orient="row",
    )
    standing = pl.concat([standing, extras], how="diagonal")
    for (year, number, circ, label), (surname, forename, nuance) in RELABELS.items():
        hit = (
            (pl.col("year") == year)
            & (pl.col("round") == number)
            & (pl.col("circ") == circ)
            & (pl.col("label") == label)
        )
        if standing.filter(hit).height != 1:
            raise ValueError(f"relabel without a candidate: {label}")
        standing = standing.with_columns(
            surname=pl.when(hit).then(pl.lit(surname)).otherwise("surname"),
            forename=pl.when(hit).then(pl.lit(forename)).otherwise("forename"),
            nuance=pl.when(hit).then(pl.lit(nuance)).otherwise("nuance"),
            label=pl.when(hit).then(pl.lit(f"{surname} {forename}")).otherwise("label"),
        )
    values = values.group_by("kind", "year", "round", "dept", "circ", "measure").agg(pl.col("value").sum())
    voters = values.filter(pl.col("measure") == VOTERS).select("kind", "year", "round", "dept", "circ", voters="value")
    values = (
        values.join(voters, on=["kind", "year", "round", "dept", "circ"], how="left", nulls_equal=True)
        .filter((pl.col("measure") != REGISTERED) | pl.col("voters").is_null() | (pl.col("value") >= pl.col("voters")))
        .drop("voters")
    )
    standing = (
        standing.group_by("kind", "year", "round", "dept", "circ", "label", maintain_order=True)
        .agg(
            pl.col("slot").first(),
            pl.col("votes").sum(),
            *(
                pl.col(c).first()
                for c in ("abbreviation", "sex", "surname", "forename", "etiquette", "nuance", "elected")
            ),
        )
        .filter(pl.col("votes") > 0)
    )

    keys = ["kind", "year", "round", "dept", "circ"]
    totals = values.filter(pl.col("measure") == VALID).select(*keys, valid="value")
    sums = standing.group_by(keys).agg(pl.col("votes").sum().alias("sum"))
    mismatched = totals.join(sums, on=keys, how="full", coalesce=True, nulls_equal=True).filter(
        pl.col("valid").ne_missing(pl.col("sum"))
    )
    print(mismatched.group_by("kind", "year", "round").len().sort("kind", "year", "round").rows())

    contest = ["kind", "year", "round", "circ"]
    legi = standing.filter(pl.col("kind") == "legi")
    registered = values.filter(pl.col("measure") == REGISTERED).select(*keys, registered="value")
    ranked = (
        legi.join(totals, on=keys, how="left", nulls_equal=True)
        .join(registered, on=keys, how="left", nulls_equal=True)
        .with_columns(
            top=pl.col("votes") == pl.col("votes").max().over(contest),
            ties=(pl.col("votes") == pl.col("votes").max().over(contest)).sum().over(contest),
        )
    )
    runoffs = legi.filter(pl.col("round") == 2).select("year", "circ", "label").unique()
    first = ranked.filter(pl.col("round") == 1).join(
        runoffs.select("year", "circ").unique().with_columns(runoff=pl.lit(True)), on=["year", "circ"], how="left"
    )
    unsettled = first.filter(pl.col("runoff").is_null() & pl.col("top") & (pl.col("votes") * 2 <= pl.col("valid")))
    won = first.filter(pl.col("runoff").is_null() & pl.col("top") & (pl.col("votes") * 2 > pl.col("valid")))
    second = ranked.filter((pl.col("round") == 2) & pl.col("top"))
    ties = pl.concat([won, second], how="diagonal").filter(pl.col("ties") > 1)
    if ties.height:
        raise ValueError(f"tied winners: {ties.head(5)}")
    advanced = first.filter(pl.col("runoff").fill_null(False)).join(runoffs, on=["year", "circ", "label"], how="semi")
    presi = standing.filter(pl.col("kind") == "presi")
    national = presi.group_by("year", "round", "label").agg(pl.col("votes").sum())
    leaders = national.with_columns(rank=pl.col("votes").rank("ordinal", descending=True).over("year", "round"))
    outcomes = pl.concat(
        [
            won.select(*contest, "label", outcome=pl.lit("elected")),
            second.select(*contest, "label", outcome=pl.lit("elected")),
            advanced.select(*contest, "label", outcome=pl.lit("advanced")),
            leaders.filter((pl.col("round") == 1) & (pl.col("rank") <= 2)).select(
                pl.lit("presi").alias("kind"),
                "year",
                "round",
                pl.lit(None, pl.String).alias("circ"),
                "label",
                outcome=pl.lit("advanced"),
            ),
            leaders.filter((pl.col("round") == 2) & (pl.col("rank") == 1)).select(
                pl.lit("presi").alias("kind"),
                "year",
                "round",
                pl.lit(None, pl.String).alias("circ"),
                "label",
                outcome=pl.lit("elected"),
            ),
        ]
    )
    seats = (
        outcomes.filter(pl.col("outcome") == "elected", pl.col("kind") == "legi").group_by("year").len().sort("year")
    )
    contests = legi.filter(pl.col("round") == 1).group_by("year").agg(pl.col("circ").n_unique()).sort("year")
    print(seats.join(contests, on="year").rows())

    PARSED.mkdir(parents=True, exist_ok=True)
    values.write_parquet(PARSED / "values.parquet")
    unsettled.select("year", "circ").write_parquet(PARSED / "unsettled.parquet")
    standing.join(outcomes, on=[*contest, "label"], how="left", nulls_equal=True).write_parquet(
        PARSED / "options.parquet"
    )

    council: list[dict[str, object]] = []
    for year, decisions in COUNCIL.items():
        for number, decision in enumerate(decisions, start=1):
            page = raw.cached(SOURCE, COUNCIL_URL.format(decision)).path.read_text(errors="replace")
            text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.DOTALL)))
            text = re.sub(r"\s+", " ", text)
            start = re.search(r"[EÉ]lecteurs inscrits", text)
            if start is None:
                raise ValueError(f"no results in {decision}")
            block = text[start.start() :]
            block = re.split(r"Article 2|Qu.ainsi", block)[0]
            labels = sorted(
                set(
                    standing.filter(
                        (pl.col("kind") == "presi") & (pl.col("year") == year) & (pl.col("round") == number)
                    )["label"]
                )
            )
            matched: dict[str, int] = {}
            measures: dict[int, int] = {}
            for head, figure in re.findall(r"(\D+?)(\d[\d .]*\d|\d)", block):
                value = int(re.sub(r"\D", "", figure))
                if re.search(r":\s*l\s*$", head):
                    value = int(f"1{value}")
                folded = fold(head)
                if "INSCRITS" in folded:
                    measures[REGISTERED] = value
                elif "VOTANTS" in folded:
                    measures[VOTERS] = value
                elif "EXPRIMES" in folded:
                    measures[VALID] = value
                elif "MAJORITE" in folded:
                    continue
                else:
                    found = [
                        label
                        for label in labels
                        if any(
                            word == fold(label).split()[-1]
                            or (len(word) > 5 and fold(label).split()[-1].startswith(word))
                            for word in folded.split()
                        )
                    ]
                    if len(found) != 1:
                        raise ValueError(f"{year} round {number}: {head!r} matches {found}")
                    matched[found[0]] = value
            if set(matched) != set(labels) or len(measures) != 3:
                raise ValueError(f"{year} round {number}: {sorted(set(labels) - set(matched))} unmatched, {measures}")
            if sum(matched.values()) != measures[VALID]:
                raise ValueError(f"{year} round {number}: candidates {sum(matched.values())} != {measures[VALID]}")
            measures[INVALID] = measures[VOTERS] - measures[VALID]
            council += [
                {"year": year, "round": number, "label": None, "measure": measure, "value": value}
                for measure, value in measures.items()
            ]
            council += [
                {"year": year, "round": number, "label": label, "measure": VOTES, "value": value}
                for label, value in matched.items()
            ]
    pl.DataFrame(council).write_parquet(PARSED / "council.parquet")


def emit() -> Path:
    values = pl.read_parquet(PARSED / "values.parquet")
    options = pl.read_parquet(PARSED / "options.parquet")
    council = pl.read_parquet(PARSED / "council.parquet")
    unsettled = set(pl.read_parquet(PARSED / "unsettled.parquet").iter_rows())
    prg_winners: set[tuple[int, str, str]] = set()
    for year, term in WIKIDATA_TERMS.items():
        members: dict[str, dict[str, set[str]]] = {}
        for row in json.loads(raw.cached(SOURCE, WIKIDATA_QUERY.format(term=term)).path.read_text())["results"][
            "bindings"
        ]:
            member = members.setdefault(row["p"]["value"], {"names": {fold(row["pLabel"]["value"])}, "parties": set()})
            if "party" in row:
                member["parties"].add(row["partyLabel"]["value"])
        by_name: dict[str, list[str]] = {}
        for item, member in members.items():
            for name in member["names"]:
                by_name.setdefault(name, []).append(item)
        for row in options.filter(
            (pl.col("kind") == "legi")
            & (pl.col("year") == year)
            & pl.col("elected")
            & pl.col("etiquette").str.to_uppercase().str.contains("^(ASSOCIATION PARTI SOCIALISTE|ASSOCIATION PS )")
        ).iter_rows(named=True):
            hits = by_name.get(fold(f"{row['forename'] or ''} {row['surname']}"), [])
            if len(hits) == 1:
                parties = members[hits[0]]["parties"]
                if "Parti radical de gauche" in parties and "Parti socialiste" not in parties:
                    prg_winners.add((year, row["circ"], fold(row["surname"])))
    caveated: set[UUID] = set()
    with connect() as conn:
        labels: dict[str, str] = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id
                   where a.scheme_code = 'fr.departement' and n.is_preferred"""
            ).fetchall()
        )
        labels["ZZ"] = "Français établis hors de France"
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="French presidential elections 1965–2022 by département and legislative elections 1958–2022 by constituency",
            polity="FR",
        )
        empty = {
            str(result_set)
            for (result_set,) in conn.execute(
                """select r.id from result_set r join changeset c on c.id = r.changeset_id
                   where c.pipeline = %s and r.status = 'active'
                   and not exists (select from result_value v where v.result_set_k = r.k)""",
                [SOURCE],
            ).fetchall()
        }

        def name(
            entity: UUID,
            text: str,
            lang: str = "fr",
            kind: str = "official",
            start: date | None = None,
            end: date | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                valid_from=start,
                valid_to=end,
            )

        def existing(scheme: str, value: str) -> UUID:
            entity = cs.key(scheme, value)
            if entity in cs.minted:
                raise ValueError(f"{scheme}:{value} is not in the database")
            return entity

        sources: dict[str, UUID] = {}
        for kind, url in FILES.items():
            sources[kind] = cs.source(PUBLISHER, TITLES[kind])
            cs.add(
                "source",
                id=sources[kind],
                kind="academic",
                publisher=PUBLISHER,
                title=TITLES[kind],
                url=LANDING[kind],
                license="ODbL 1.0",
                license_url="https://opendatacommons.org/licenses/odbl/1-0/",
                attribution="Centre de données socio-politiques (CDSP), Sciences Po, via data.gouv.fr",
                notes=NOTES[kind],
            )
            cs.artifact(raw.cached(SOURCE, url), sources[kind])
        sources["aggregated"] = cs.source("data.gouv.fr", AGGREGATED_TITLE)
        cs.add(
            "source",
            id=sources["aggregated"],
            kind="official_body",
            publisher="data.gouv.fr",
            title=AGGREGATED_TITLE,
            url="https://www.data.gouv.fr/datasets/donnees-des-elections-agregees",
            license="Licence Ouverte v2.0",
            license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
            notes=NOTES["aggregated"],
        )
        for url in AGGREGATED.values():
            cs.artifact(raw.cached(SOURCE, url), sources["aggregated"])
        sources["wikidata"] = cs.source("Wikidata", WIKIDATA_TITLE)
        if sources["wikidata"] in cs.minted:
            cs.add(
                "source",
                id=sources["wikidata"],
                kind="wikidata",
                publisher="Wikidata",
                title=WIKIDATA_TITLE,
                url="https://www.wikidata.org",
                license="CC0 1.0",
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            )
        for term in WIKIDATA_TERMS.values():
            cs.artifact(raw.cached(SOURCE, WIKIDATA_QUERY.format(term=term)), sources["wikidata"])
        sources["council"] = cs.source(COUNCIL_PUBLISHER, COUNCIL_TITLE)
        cs.add(
            "source",
            id=sources["council"],
            kind="official_body",
            publisher=COUNCIL_PUBLISHER,
            title=COUNCIL_TITLE,
            url="https://www.conseil-constitutionnel.fr/decisions/pdr",
            license="Freely reproducible with the Conseil constitutionnel's site cited as the source, without altering the text (legal notice)",
        )
        for decisions in COUNCIL.values():
            for decision in decisions:
                cs.artifact(raw.cached(SOURCE, COUNCIL_URL.format(decision)), sources["council"])

        france = cs.polity("FR")
        nation = cs.area("fr.nation", "FR")
        departements = {code: cs.area("fr.departement", code) for code in labels if code != "ZZ"}
        if nation in cs.minted or any(area in cs.minted for area in departements.values()):
            raise ValueError("publish fr.interieur before fr.cdsp")
        whole = cs.unit("fr.nation", "FR")
        if whole in cs.minted:
            cs.add(
                "reporting_unit",
                id=whole,
                polity_id=france,
                scheme_code="fr.nation",
                code="FR",
                area_relation="equals",
                area_id=nation,
                kind="aggregate",
            )
            name(whole, "France", "en")
        assembly = existing("fr.body", "national-assembly")
        legislative = existing("fr.electoral_system", "legislative-two-round")
        earlier: dict[int, UUID] = {}
        for since, until, qualify in ((1958, 1966, (0.05, "valid")), (1967, 1973, (0.1, "registered"))):
            earlier[since] = cs.key("fr.electoral_system", f"legislative-two-round-{since}")
            cs.add(
                "electoral_system",
                id=earlier[since],
                polity_id=france,
                rules={
                    "formula": "two_round",
                    "minimum": {"share": 0.25, "of": "registered"},
                    "qualify": {"share": qualify[0], "of": qualify[1]},
                },
                rules_version=1,
            )
            name(earlier[since], f"French legislative two-round system ({since}–{until})", "en")
            cs.add(
                "note",
                entity_id=earlier[since],
                kind="methodology",
                body_md=f"{FIRST_ROUND} {THRESHOLDS[since].replace('could', 'may')} {SECOND_ROUND}",
            )
        office = cs.key("fr.office", "president")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=france, kind="head_of_state")
            name(office, "President of France", "en")
            name(office, "Président de la République", "fr", "native")
        presidential = cs.key("fr.electoral_system", "presidential-two-round")
        if presidential in cs.minted:
            cs.add(
                "electoral_system", id=presidential, polity_id=france, rules={"formula": "two_round"}, rules_version=1
            )
            name(presidential, "French presidential two-round system", "en")
            cs.add("note", entity_id=presidential, kind="methodology", body_md=PRESIDENT)

        def label_of(code: str, day: date) -> str:
            if code == "20" or (code in ("75", "78") and day < MAPS[1][0]):
                return OLD[code]
            return labels[code]

        def ordinal(number: int) -> str:
            return f"{number}{'re' if number == 1 else 'e'} circonscription"

        units: dict[tuple[str, date | None], UUID] = {}

        def department_unit(code: str, day: date) -> UUID:
            old = code == "20" or (code in ("75", "78") and day < DEPARTEMENTS_SPLIT)
            start = (
                DEPARTEMENTS_SPLIT
                if not old and code in ("75", "78", "91", "92", "93", "94", "95")
                else CORSICA_SPLIT
                if code in ("2A", "2B")
                else None
            )
            end = (CORSICA_SPLIT if code == "20" else DEPARTEMENTS_SPLIT) if old else None
            key = (code, start or date(1958, 1, 1))
            if key not in units:
                units[key] = cs.unit("fr.departement", code, on=key[1])
                if units[key] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=units[key],
                        polity_id=france,
                        scheme_code="fr.departement",
                        code=code,
                        area_relation="unlocated" if code == "ZZ" else "within" if old else "equals",
                        area_id=nation if old else departements.get(code),
                        kind="abroad_mission" if code == "ZZ" else "standard",
                        valid_from=start,
                        valid_to=end,
                    )
                    name(units[key], label_of(code, day))
            return units[key]

        legi = options.filter(pl.col("kind") == "legi")
        first_seen = (
            legi.with_columns(
                day=pl.struct("year", "round").map_elements(
                    lambda row: LEGISLATIVE[row["year"]][row["round"] - 1], return_dtype=pl.Date
                )
            )
            .group_by(
                "circ",
                pl.col("year")
                .map_elements(
                    lambda year: next(
                        i
                        for i, (start, end) in enumerate(MAPS)
                        if start <= LEGISLATIVE[year][0] and (end is None or LEGISLATIVE[year][0] < end)
                    ),
                    return_dtype=pl.Int64,
                )
                .alias("era"),
            )
            .agg(pl.col("day").min(), pl.col("dept").first())
        )
        current_areas = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'fr.circonscription' and c.valid_from = %s and c.valid_to is null
                     and not exists (select from area_code o where o.area_id = a.id and o.valid_from < %s)""",
                [MAPS[3][0], MAPS[3][0]],
            ).fetchall()
        )
        circ_areas: dict[tuple[str, int], UUID] = {}
        circ_units: dict[tuple[str, int], UUID] = {}
        for circ, era, seen, dept in first_seen.sort("era", "circ").iter_rows():
            if era == 3:
                circ_units[(circ, era)] = cs.unit("fr.circonscription", circ, on=MAPS[3][0])
                circ_areas[(circ, era)] = current_areas.get(circ) or cs.area("fr.circonscription", circ, on=MAPS[3][0])
                if circ_units[(circ, era)] in cs.minted or circ_areas[(circ, era)] in cs.minted:
                    raise ValueError(f"constituency {circ} is not in the database")
                continue
            start = CORSICA_SPLIT if era == 1 and dept in ("2A", "2B") else seen
            end = CORSICA_SPLIT if era == 1 and dept == "20" else MAPS[era][1]
            area = circ_areas[(circ, era)] = cs.area("fr.circonscription", circ, on=start)
            unit = circ_units[(circ, era)] = cs.unit("fr.circonscription", circ, on=start)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="fr.circonscription")
                cs.add("area_code", area_id=area, code=circ, valid_from=start, valid_to=end)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=nation if dept == "20" or (era == 0 and dept in ("75", "78")) else departements[dept],
                    valid_from=start,
                    valid_to=end,
                )
                name(area, f"{label_of(dept, start)}, {ordinal(int(circ[-2:]))}")
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=france,
                    scheme_code="fr.circonscription",
                    code=circ,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=start,
                    valid_to=end,
                )
                name(unit, f"{label_of(dept, start)}, {ordinal(int(circ[-2:]))}")

        parties: dict[str, UUID] = {}

        def party_of(scheme: str, value: str, label: str, abbreviation: str | None, kind: str) -> UUID:
            key = f"{scheme}:{value}"
            if key not in parties:
                parties[key] = cs.key(scheme, value)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=france)
                    name(parties[key], label)
                    if abbreviation:
                        name(parties[key], abbreviation, "fr", "abbreviation")
            return parties[key]

        def presidential_party(abbreviation: str) -> UUID | None:
            if abbreviation in PARTIES:
                return existing(*PARTIES[abbreviation])
            if abbreviation in NEW_PARTIES:
                return party_of(
                    "fr.party", slug(NEW_PARTIES[abbreviation]), NEW_PARTIES[abbreviation], abbreviation, "party"
                )
            return None

        front = parties[f"fr.party:{slug(NEW_PARTIES['FN'])}"] = cs.key("fr.party", slug(NEW_PARTIES["FN"]))
        if front in cs.minted:
            cs.add("party", id=front, kind="party", polity_id=france)
            name(front, "FN", "fr", "abbreviation", end=RENAMED)
            name(front, "RN", "fr", "abbreviation", start=RENAMED)
        named = conn.execute(
            "select id from name where entity_id = %s and text = 'Front national' and kind = 'official'", [front]
        ).fetchone()
        cs.add(
            "name",
            id=named[0] if named else None,
            entity_id=front,
            text="Front national",
            lang="fr",
            kind="official",
            is_preferred=True,
            valid_from=None,
            valid_to=RENAMED,
        )
        cs.add(
            "name",
            entity_id=front,
            text="Rassemblement national",
            lang="fr",
            kind="official",
            is_preferred=True,
            valid_from=RENAMED,
            valid_to=None,
        )

        persons: dict[str, UUID] = {}
        displays: dict[UUID, set[str]] = {}

        def person_of(key: str, display: str, sex: str | None) -> UUID:
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key], gender={"F": "female", "M": "male"}.get(sex or ""))
                displays.setdefault(persons[key], set()).add(display)
            return persons[key]

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

        for kind, schedule in (("presi", PRESIDENTIAL), ("legi", LEGISLATIVE)):
            for year, days in schedule.items():
                noun = "presidential" if kind == "presi" else "legislative"
                cycle = cs.key("fr.election", f"{days[0]}:{noun}")
                cs.add(
                    "election",
                    id=cycle,
                    polity_id=france,
                    kind="general",
                    date_from=days[0],
                    date_to=days[1],
                    status="held",
                )
                name(cycle, f"{year} French {noun} election", "en")
                name(
                    cycle,
                    f"Élection présidentielle de {year}" if kind == "presi" else f"Élections législatives de {year}",
                    "fr",
                    "native",
                )
                cs.add("slug", path=f"fr/{days[0]}-{noun}", entity_id=cycle)
                if kind == "legi" and year < 1978:
                    cs.add("note", entity_id=cycle, kind="context", body_md=THRESHOLDS[1958 if year < 1967 else 1967])
                previous: dict[str, UUID] = {}
                for number, day in enumerate(days, start=1):
                    stage = "general" if number == 1 else "runoff"
                    ordinal_word = "first" if number == 1 else "second"
                    election = cs.key("fr.election", str(day))
                    cs.add(
                        "election",
                        id=election,
                        polity_id=france,
                        parent_id=cycle,
                        kind=stage,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{year} French {noun} election, {ordinal_word} round", "en")
                    cs.add("slug", path=f"fr/{day}-{stage}", entity_id=election)
                    held = options.filter(
                        (pl.col("kind") == kind) & (pl.col("year") == year) & (pl.col("round") == number)
                    )
                    counted = values.filter(
                        (pl.col("kind") == kind) & (pl.col("year") == year) & (pl.col("round") == number)
                    )
                    if kind == "presi":
                        contest_key = f"{day}:president"
                        contest = cs.key("fr.contest", contest_key)
                        cs.add(
                            "contest",
                            id=contest,
                            election_id=election,
                            kind="office",
                            office_id=office,
                            district_area_id=nation,
                            magnitude=1,
                            round_number=number,
                            previous_round_id=previous.get("president"),
                            electoral_system_id=presidential,
                            default_result_kind="counted",
                        )
                        previous["president"] = contest
                        name(contest, f"{year} French presidential election, {ordinal_word} round", "en")
                        cs.add("slug", path=f"fr/{day}-{stage}/president", entity_id=contest)
                        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                        partial = year <= 2002
                        cs.add(
                            "result_set",
                            id=result_set,
                            election_id=election,
                            contest_id=contest,
                            stage="official",
                            kind="counted",
                            status="active",
                            source_id=sources["aggregated" if year >= AGGREGATED_YEAR else kind],
                            is_default=year not in COUNCIL,
                            completeness=None if partial else 1,
                        )
                        fresh = result_set in cs.minted or str(result_set) in empty
                        declared = council.filter((pl.col("year") == year) & (pl.col("round") == number))
                        official = cs.key(f"{SOURCE}.result_set", f"{contest_key}:council") if year in COUNCIL else None
                        if official is not None:
                            cs.add(
                                "result_set",
                                id=official,
                                election_id=election,
                                contest_id=contest,
                                stage="official",
                                kind="counted",
                                status="active",
                                source_id=sources["council"],
                                is_default=True,
                                completeness=1,
                            )
                            cs.add("note", entity_id=official, kind="methodology", body_md=COUNCIL_METHOD)
                            if official in cs.minted:
                                for measure, value in (
                                    declared.filter(pl.col("label").is_null()).select("measure", "value").iter_rows()
                                ):
                                    value_row(official, whole, None, measure, value)
                        if fresh and partial:
                            cs.add(
                                "note",
                                entity_id=result_set,
                                kind="caveat",
                                body_md=METROPOLITAN_CAVEAT if year < 2002 else NO_ABROAD_CAVEAT,
                            )
                        if fresh:
                            for dept, measure, value in counted.select("dept", "measure", "value").iter_rows():
                                value_row(result_set, department_unit(dept, day), None, measure, value)
                        for label, abbreviation, outcome in (
                            held.group_by("label", maintain_order=True)
                            .agg(pl.col("abbreviation").first(), pl.col("outcome").first())
                            .iter_rows()
                        ):
                            party = presidential_party(abbreviation)
                            option_key = f"{contest_key}:{slug(label)}"
                            option = cs.key("fr.option", option_key)
                            cs.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                kind="candidate",
                                party_id=party,
                                ballot_label=label,
                            )
                            cs.add(
                                "candidacy",
                                id=cs.key("fr.candidacy", option_key),
                                person_id=person_of(f"president:{slug(label)}", label, None),
                                option_id=option,
                                role="head",
                                ballot_name=label,
                                member_of_party_id=party,
                                is_independent=party is None,
                            )
                            if outcome is not None:
                                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
                                if official is not None:
                                    cs.add("outcome", result_set_id=official, option_id=option, kind=outcome)
                            if official is not None and official in cs.minted:
                                value_row(
                                    official,
                                    whole,
                                    option,
                                    VOTES,
                                    declared.filter(pl.col("label") == label)["value"].item(),
                                )
                            if fresh:
                                for dept, votes in (
                                    held.filter(pl.col("label") == label).select("dept", "votes").iter_rows()
                                ):
                                    value_row(result_set, department_unit(dept, day), option, VOTES, votes)
                        continue
                    era = next(
                        i for i, (start, end) in enumerate(MAPS) if start <= days[0] and (end is None or days[0] < end)
                    )
                    grouped = {circ: frame for (circ,), frame in counted.group_by("circ")}
                    for (circ,), lines in sorted(held.group_by("circ"), key=lambda group: group[0]):
                        dept = lines["dept"][0]
                        district = label_of(dept, day)
                        ordinal_number = int(circ[-2:])
                        contest_key = f"{day}:{circ}"
                        contest = cs.key("fr.contest", contest_key)
                        cs.add(
                            "contest",
                            id=contest,
                            election_id=election,
                            kind="body_seats",
                            body_id=assembly,
                            district_area_id=circ_areas[(circ, era)],
                            magnitude=1,
                            round_number=number,
                            previous_round_id=previous.get(circ),
                            electoral_system_id=legislative if year >= 1978 else earlier[1958 if year < 1967 else 1967],
                            default_result_kind="counted",
                        )
                        previous[circ] = contest
                        name(contest, f"{district}, constituency {ordinal_number}, {ordinal_word} round", "en")
                        cs.add("slug", path=f"fr/{day}-{stage}/{slug(district)}-{ordinal_number}", entity_id=contest)
                        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                        cs.add(
                            "result_set",
                            id=result_set,
                            election_id=election,
                            contest_id=contest,
                            stage="official",
                            kind="counted",
                            status="active",
                            source_id=sources["aggregated" if year >= AGGREGATED_YEAR else kind],
                            is_default=True,
                            completeness=1,
                        )
                        fresh = result_set in cs.minted or str(result_set) in empty
                        unit = circ_units[(circ, era)]
                        tendencies = year < 1988
                        if fresh and tendencies:
                            cs.add("note", entity_id=result_set, kind="caveat", body_md=TENDENCY_CAVEAT)
                        if fresh and number == 1 and (year, circ) in unsettled:
                            cs.add("note", entity_id=result_set, kind="caveat", body_md=UNSETTLED_CAVEAT)
                        if (fresh or (year, number, circ) in CORRECTED) and circ in grouped:
                            for measure, value in grouped[circ].select("measure", "value").iter_rows():
                                value_row(result_set, unit, None, measure, value)
                        for line in lines.sort("slot").iter_rows(named=True):
                            option_key = f"{contest_key}:{line['slot']}"
                            option = cs.key("fr.option", option_key)
                            if tendencies:
                                party = party_of(
                                    f"{SOURCE}.tendency",
                                    f"{year}:{slug(line['label'])}",
                                    line["label"],
                                    line["abbreviation"],
                                    "nuance",
                                )
                                cs.add(
                                    "contest_option",
                                    id=option,
                                    contest_id=contest,
                                    kind="candidate",
                                    party_id=party,
                                    ballot_label=line["label"],
                                )
                            else:
                                nuances = LATER_NUANCES.get(year, {}) | (
                                    NUANCES
                                    if year < AGGREGATED_YEAR
                                    else {
                                        code: label
                                        for code, label in NUANCES.items()
                                        if code in ("COM", "SOC", "DVD", "DVG", "EXG", "EXD", "ECO", "REG", "DIV")
                                    }
                                )
                                nuance = line["nuance"]
                                if nuance == "PRG" and (line["etiquette"] or "").startswith(
                                    ("ASSOCIATION PARTI SOCIALISTE", "ASSOCIATION PS ")
                                ):
                                    if fresh and result_set not in caveated:
                                        caveated.add(result_set)
                                        cs.add("note", entity_id=result_set, kind="caveat", body_md=ASSOCIATION_CAVEAT)
                                    nuance = "PRG" if (year, circ, fold(line["surname"])) in prg_winners else "SOC"
                                if nuance is not None and nuance not in nuances:
                                    raise ValueError(f"unknown nuance {nuance} in {year}")
                                party = (
                                    None
                                    if nuance is None
                                    else party_of(
                                        "fr.interieur.nuance",
                                        f"{year}:{nuance}",
                                        nuances[nuance],
                                        nuance,
                                        "nuance",
                                    )
                                )
                                ballot = f"{line['surname']} {line['forename'] or ''}".strip()
                                cs.add(
                                    "contest_option",
                                    id=option,
                                    contest_id=contest,
                                    kind="candidate",
                                    party_id=party,
                                    ballot_label=ballot,
                                )
                                person = person_of(
                                    f"{dept}:{fold(line['surname'])}:{fold(line['forename'] or '')}",
                                    person_name(line["forename"], line["surname"]),
                                    line["sex"],
                                )
                                cs.add(
                                    "candidacy",
                                    id=cs.key("fr.candidacy", option_key),
                                    person_id=person,
                                    option_id=option,
                                    role="head",
                                    ballot_name=ballot,
                                    is_independent=False,
                                )
                            if line["outcome"] is not None:
                                cs.add("outcome", result_set_id=result_set, option_id=option, kind=line["outcome"])
                            if fresh or (year, number, circ) in CORRECTED:
                                value_row(result_set, unit, option, VOTES, line["votes"])
        for person, texts in displays.items():
            stripped = {
                unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
                for text in texts
                if not text.isascii()
            }
            for text in sorted(texts - stripped):
                name(person, text)
        return cs.write()
