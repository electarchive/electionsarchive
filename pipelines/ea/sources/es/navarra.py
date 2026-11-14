import csv
import io
import re
import subprocess
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "es.navarra"
VERSION = "1"
PUBLISHER = "Gobierno de Navarra"
TITLE = "Resultados de las elecciones al Parlamento de Navarra agrupados por municipios (datosabiertos.navarra.es)"
PORTAL = "https://datosabiertos.navarra.es/es/dataset/resultados-de-las-elecciones-al-parlamento-de-navarra-agrupados-por-municipios"
BOE = "Boletín Oficial del Estado"
BOE_TITLE = "Acta de escrutinio general de las elecciones al Parlamento de Navarra de 1983 (Resolución de la Junta Electoral Central de 12 de septiembre de 1983)"
PARLIAMENT = "Parlamento de Navarra"
PARLIAMENT_TITLE = (
    "Resultados electorales y composición del Parlamento de Navarra, y relación de parlamentarios forales electos"
)
PARLIAMENT_PORTAL = "https://parlamentodenavarra.es/es/resultados_electorales"
LAW = "Ley Foral 16/1986, de 17 de noviembre, Electoral de Navarra"
STATUTE = "Ley Orgánica 13/1982, de 10 de agosto, de Reintegración y Amejoramiento del Régimen Foral de Navarra"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = {1983: 0.05}
SEATS = 50
CKAN = "https://datosabiertos.navarra.es/dataset/fa95fdf0-4aa1-4194-84b3-2779c9bc8e5e/resource/"
WAYBACK = "https://web.archive.org/web/{stamp}id_/https://parlamentodenavarra.es/"
SITE = "sites/default/files/"
LEGACY = WAYBACK.format(stamp="20231118044955") + "sites/all/themes/parlanav_tema/images/legislaturas/elec.pdf"
ELECTIONS = {
    1983: (date(1983, 5, 8), "5a502d9d-d3f1-4f0f-bcc8-11e0620fe205", "resultadosparlamentopormunicipio1983.csv"),
    1987: (date(1987, 6, 10), "198ccaea-2e5b-42bd-a2e2-1b061bebc665", "resultadosparlamentopormunicipio1987.csv"),
    1991: (date(1991, 5, 26), "cb192f08-9c2a-4fe7-ba81-0db5075f0120", "resultadosparlamentopormunicipio1991.csv"),
    1995: (date(1995, 5, 28), "ccad11ed-3d6d-4862-ace7-c712a3d1115a", "resultadosparlamentopormunicipio1995.csv"),
    1999: (date(1999, 6, 13), "2b3ec10a-4a69-43be-8a1d-650a090e2c54", "resultadosparlamentopormunicipio1999.csv"),
    2003: (date(2003, 5, 25), "37a71d61-6a6e-4333-92db-8f5f478a0719", "resultadosparlamentopormunicipio2003.csv"),
    2007: (date(2007, 5, 27), "1aebd513-7e45-4fef-826d-0dee260f20da", "resultadosparlamentopormunicipio2007.csv"),
    2011: (date(2011, 5, 22), "92523e94-1a43-409b-84ec-e3f01e1c8bd4", "resultadosparlamentopormunicipio2011.csv"),
    2015: (date(2015, 5, 24), "647d4944-c275-4d03-8f6e-49092288be4b", "escrutinio_parlamento_2015.csv"),
    2019: (date(2019, 5, 26), "9fc75e6f-9b57-450c-82ac-a04143f25ebc", "escrutinio_parlamento_2019.csv"),
    2023: (date(2023, 5, 28), "f555f0ff-a6f1-420b-bd25-caf979f26d86", "escrutinio_parlamento_2023.csv"),
}
TABLES = {
    **dict.fromkeys(range(1983, 2008, 4), (LEGACY, "Resultados electorales y composición, 1983-2007")),
    2011: (WAYBACK.format(stamp="2") + SITE + "misc/Elec2011.pdf", "Resultados electorales, VIII Legislatura"),
    2015: (WAYBACK.format(stamp="2") + SITE + "misc/Elecciones%202015.pdf", "Resultados electorales, IX Legislatura"),
    2019: (
        WAYBACK.format(stamp="20230617092519") + "es/contenido/x-legislatura",
        "Resultados electorales, X Legislatura",
    ),
    2023: (
        WAYBACK.format(stamp="20250125013811") + "es/contenido/xi-legislatura",
        "Resultados electorales, XI Legislatura",
    ),
}
BOE_1983 = "https://www.boe.es/boe/dias/1983/10/28/pdfs/A29343-29344.pdf"
PROCLAMATION = {
    2015: (WAYBACK.format(stamp="2") + "sites/default/files/diarios-sesiones/Plen9001.pdf", ".pdf"),
    2019: (WAYBACK.format(stamp="2") + "sites/default/files/diarios-sesiones/Plen10001.pdf", ".pdf"),
    2023: (
        WAYBACK.format(stamp="2") + SITE + "contenido-estatico-archivos/Acta%20de%20proclamaci%C3%B3n%202023.pdf",
        ".pdf",
    ),
}
MEMBERS_2023 = {
    "upn": [
        "José Javier Esparza Abaurrea",
        "Yolanda Ibáñez Pérez",
        "Marta Álvarez Alonso",
        "María Jesús Valdemoros Erro",
        "Fco. Javier Trigo Oubiña",
        "Félix Zapatero Soria",
        "Juan Luis Sánchez de Muniáin Lacasia",
        "Leticia San Martín Rodríguez",
        "Pedro José González Felipe",
        "Ana M.ª Elizalde Urmeneta",
        "Iñaki Iriarte López",
        "Ángel Ansa Echegaray",
        "Cristina López Manero",
        "Miguel Bujanda Cirauqui",
        "Raquel Garbayo Berdonces",
    ],
    "psn": [
        "María Victoria Chivite Navascués",
        "Ramón Alzórriz Goñi",
        "Ainhoa Unzu Garate",
        "Jorge Aguirre Oviedo",
        "María Inmaculada Jurío Macaya",
        "Antonio Javier Lecumberri Urabayen",
        "Olga Chueca Chueca",
        "Jesús María Rodríguez Gómez",
        "María Teresa Esporrín Las Heras",
        "Kevin Lucero Domingues",
        "María Aranzazu Biurrun Urpegui",
    ],
    "bildu": [
        "Laura Aznal Sagasti",
        "Adolfo Araiz Flamarique",
        "Eneka Maiz Ulaiar",
        "Severiano Txoperena Matxikote",
        "Javier Arza Porras",
        "Aranzazu Izurdiaga Osinaga",
        "Domingo González Martínez",
        "Irati Jiménez Aragón",
        "Mikel Zabaleta Aramendia",
    ],
    "geroa": [
        "Uxue Barkos Berruezo",
        "Unai Hualde Iglesias",
        "Pablo Azcona Molinet",
        "María Roncesvalles Solana Arana",
        "Javier Arakama Urtiaga",
        "Mikel Asiain Torres",
        "Blanca Regúlez Álvarez",
    ],
    "pp": ["Javier García Jiménez", "Irene Royo Ortín", "Maribel García Malo"],
    "contigo": ["Begoña Alfaro García", "Carlos Guzmán Pérez", "Daniel López Córdoba"],
    "vox": ["María Teresa Nosti Izquierdo", "Emilio Jiménez Román"],
}
MEMBERS_1983 = {
    "psn": [
        "Gabriel Urralburu Tainta",
        "Víctor Manuel Arbeloa Muru",
        "Antonio Aragón Elizalde",
        "José Ramón Zabala Urra",
        "Miguel Ángel Ancízar Eceolaza",
        "Javier Asiáin Ayala",
        "José María Ezpeleta Martínez",
        "Camino Oslé Guerendiain",
        "Javier Otano Cid",
        "José Antonio Ruiz Amatria",
        "Rafael Pérez Rivas",
        "Arsenio Lebrero Ochoa",
        "Andrés de Miguel Torrano",
        "Emilio Boulandier Maiza",
        "Francisco Javier Lora Jaunsarás",
        "Ramiro Martínez Lerga",
        "María Soledad Elizari Garayoa",
        "Pedro Ardaiz Egüés",
        "Juan José Paredes Pérez",
        "Francisco Javier Vital Sevillano",
    ],
    "upn": [
        "Balbino Bados Astiz",
        "Albito Viguria Caparroso",
        "Javier del Castillo Bandrés",
        "Rafael Gurrea Induráin",
        "Antonio Andía Ustárroz",
        "José Ángel Zubiaur Alegre",
        "Juan Cruz Alli Aranguren",
        "Luis Fernando Medrano Blasco",
        "Andrés Basterra Layana",
        "José Javier Gortari Beiner",
        "José Javier Viñes Rueda",
        "Andrés Luis Escribano Boldoba",
        "José María Los Arcos Martínez",
    ],
    "ap": [
        "José Luis Monge Recalde",
        "Pedro Pegenaute Garde",
        "José Javier Catalán Ríos",
        "Pablo José de Miguel Adrián",
        "Jaime Ignacio del Burgo Tajadura",
        "José Ignacio López Borderías",
        "Calixto Ayesa Dianda",
        "Lucio Jiménez Guerrero",
    ],
    "hb": [
        "José Ignacio Aldecoa Azarloza",
        "Francisco Javier Ilundáin Guillén",
        "Francisco Javier Arce Goñi",
        "Ángel García de Dios",
        "Jesús María Iraeta Echebeste",
        "Antonino Alemán Abaurrea",
    ],
    "pnv": ["Iñaki Cabasés Hita", "Fermín Ciáurriz Gómez", "Pedro Manuel Barbería Echarri"],
}
HEADINGS = {
    "geroabai": "geroa",
    "uniondelpueblonavarro": "upn",
    "partidosocialistadenavarra": "psn",
    "partidosocialistadenavarrapsoe": "psn",
    "euskalherriabildu": "bildu",
    "izquierdaezkerra": "ie",
    "partidopopular": "pp",
    "podemosahaldugu": "podemos",
    "podemos": "podemos",
    "navarrasuma": "navarra_suma",
}
PARTIES = {
    "psn": ("Partido Socialista de Navarra (PSN-PSOE)", "Socialist Party of Navarre"),
    "upn": ("Unión del Pueblo Navarro (UPN)", "Navarrese People's Union"),
    "ap": ("Alianza Popular-Partido Demócrata Popular-Unión Liberal (AP-PDP-UL)", "AP-PDP-UL"),
    "hb": ("Herri Batasuna (HB)", "Herri Batasuna"),
    "pnv": ("Partido Nacionalista Vasco (EAJ-PNV)", "Basque Nationalist Party"),
    "auzolan": ("Agrupación de Electores Auzolan", "Auzolan Electors' Group"),
    "carlista": ("Partido Carlista", "Carlist Party"),
    "ee": ("Euskadiko Ezkerra (EE)", "Basque Country Left"),
    "pce": ("Partido Comunista de Euskadi (PCE)", "Communist Party of the Basque Country"),
    "lc": ("Liga Comunista (LC)", "Communist League"),
    "cds": ("Centro Democrático y Social (CDS)", "Democratic and Social Center"),
    "ea": ("Eusko Alkartasuna (EA)", "Eusko Alkartasuna"),
    "udf": ("Unión Demócrata Foral (UDF)", "Foral Democratic Union"),
    "fap": ("Federación de Partidos de Alianza Popular (FAP)", "Federation of Popular Alliance Parties"),
    "batzarre": ("Batzarre-Asamblea de Izquierdas de Navarra", "Batzarre"),
    "iu": ("Izquierda Unida de Navarra (IU-EB, IUN-NEB)", "United Left of Navarre"),
    "pte": ("Partido de los Trabajadores de España-Unidad Comunista (PTE-UCE)", "PTE-UCE"),
    "pag": ("Partido Agrícola Ganadero (PAG)", "Farmers' and Ranchers' Party"),
    "cdn": ("Convergencia de Demócratas de Navarra (CDN)", "Convergence of Navarrese Democrats"),
    "na": ("Coalición Nacionalistas de Navarra", "Navarrese Nationalists Coalition"),
    "pie": ("Federación Plataforma de los Independientes de España", "Platform of Independents of Spain"),
    "eh": ("Euskal Herritarrok (EH)", "Euskal Herritarrok"),
    "eapnv": ("Coalición EA / EAJ-PNV", "EA / EAJ-PNV coalition"),
    "in": ("Independientes de Navarra (IN)", "Independents of Navarre"),
    "aralar": ("Aralar", "Aralar"),
    "ph": ("Partido Humanista (PH)", "Humanist Party"),
    "nabai": ("Nafarroa Bai (NA-BAI)", "Nafarroa Bai"),
    "rcn": (
        "Representación Cannábica de Navarra / Nafarroako Ordezkaritza Kanabikoa",
        "Cannabis Representation of Navarre",
    ),
    "bildu": ("Bildu / EH Bildu", "EH Bildu"),
    "pp": ("Partido Popular (PP)", "People's Party"),
    "ie": ("Izquierda-Ezkerra (I-E)", "Left (Izquierda-Ezkerra)"),
    "ecolo": ("Verdes de Navarra / Nafarroako Berdeak", "Greens of Navarre"),
    "upyd": ("Unión Progreso y Democracia (UPyD)", "Union, Progress and Democracy"),
    "sain": ("Solidaridad y Autogestión Internacionalista (SAIn)", "Solidarity and Internationalist Self-Management"),
    "ixn": ("Iniciativa por Navarra (IxN)", "Initiative for Navarre"),
    "dne": ("Derecha Navarra y Española (DNE)", "Navarrese and Spanish Right"),
    "geroa": ("Geroa Bai", "Geroa Bai"),
    "podemos": ("Podemos-Ahal Dugu", "Podemos-Ahal Dugu"),
    "ciudadanos": ("Ciudadanos-Partido de la Ciudadanía", "Citizens"),
    "ln": ("Libertad Navarra / Libertate Navarra", "Libertad Navarra"),
    "equo": ("EQUO-Partido Verde Europeo", "Equo"),
    "pacma": ("Partido Animalista contra el Maltrato Animal (PACMA)", "Animalist Party"),
    "navarra_suma": ("Navarra Suma (NA+)", "Navarra Suma"),
    "vox": ("VOX", "Vox"),
    "contigo": ("Contigo-Zurekin", "Contigo-Zurekin"),
    "pmj": ("Por un mundo más justo / Bidezko mundurantz", "For a Fairer World"),
    "eguzkilore": ("Eguzkilore", "Eguzkilore"),
    "voluntad": ("Voluntad Foral", "Voluntad Foral"),
}
ALIASES = {
    "PSOE": "psn",
    "PSN-PSOE": "psn",
    "PSN": "psn",
    "AP-PDP-UL": "ap",
    "PNV": "pnv",
    "EAJ-PNV": "pnv",
    "P_CARL": "carlista",
    "P_CARLISTA": "carlista",
    "EKA": "carlista",
    "IU": "iu",
    "IU-EB": "iu",
    "IUN_NEB": "iu",
    "IUN-NEB": "iu",
    "PTE-UCE": "pte",
    "EA_EAJ-PNV": "eapnv",
    "NA-BAI": "nabai",
    "NA-BAI2011": "nabai",
    "RCN-NOK": "rcn",
    "BILDU": "bildu",
    "EH Bildu": "bildu",
    "EH_BILDU": "bildu",
    "PP": "pp",
    "PPN": "pp",
    "IZQ-EZK": "ie",
    "I-E": "ie",
    "I-E (n)": "ie",
    "ECOLO": "ecolo",
    "SAIn": "sain",
    "SAIN": "sain",
    "GBAI": "geroa",
    "Geroa Bai": "geroa",
    "GEROA_BAI": "geroa",
    "Podemos": "podemos",
    "PODEMOS": "podemos",
    "Ciudadanos": "ciudadanos",
    "CIUDADANOS": "ciudadanos",
    "LN": "ln",
    "Ln": "ln",
    "Equo": "equo",
    "EQUO": "equo",
    "PACMA": "pacma",
    "NA+": "navarra_suma",
    "VOX": "vox",
    "CONTIGO_ZUREKIN": "contigo",
    "PUM_MAS_J": "pmj",
    "EGUZKILORE": "eguzkilore",
    "VOLUNTAD_FORAL": "voluntad",
}
OFFICIAL = {
    1983: (
        (379692, 269042, 3135, 1826),
        {
            "PSOE": (94737, 20),
            "UPN": (62072, 13),
            "AP-PDP-UL": (37554, 8),
            "HB": (28055, 6),
            "PNV": (18161, 3),
            "AUZOLAN": (8356, 0),
            "P_CARL": (6733, 0),
            "EE": (6292, 0),
            "PCE": (1712, 0),
            "LC": (409, 0),
        },
    ),
    1987: (
        (393326, 286722, 3327, 3950),
        {
            "PSN-PSOE": (78453, 15),
            "UPN": (69419, 14),
            "HB": (38138, 7),
            "CDS": (21022, 4),
            "EA": (19840, 4),
            "UDF": (17663, 3),
            "FAP": (11985, 2),
            "EE": (9618, 1),
            "BATZARRE": (5880, 0),
            "IU": (3802, 0),
            "EAJ-PNV": (2661, 0),
            "PTE-UCE": (964, 0),
        },
    ),
    1991: (
        (414913, 276773, 2091, 3637),
        {
            "UPN": (96005, 20),
            "PSN-PSOE": (91645, 19),
            "HB": (30762, 6),
            "EA": (15170, 3),
            "IU": (11167, 2),
            "BATZARRE": (6543, 0),
            "EE": (5824, 0),
            "CDS": (5650, 0),
            "PAG": (3855, 0),
            "EAJ-PNV": (3071, 0),
            "P_CARL": (1353, 0),
        },
    ),
    1995: (
        (437797, 299545, 2366, 5761),
        {
            "UPN": (93163, 17),
            "PSN-PSOE": (62021, 11),
            "CDN": (55153, 10),
            "IU-EB": (27773, 5),
            "HB": (27404, 5),
            "EA": (13568, 2),
            "BATZARRE": (6509, 0),
            "NA": (2943, 0),
            "PIE": (2041, 0),
            "P_CARLISTA": (843, 0),
        },
    ),
    1999: (
        (461729, 305880, 2539, 7126),
        {
            "UPN": (125497, 22),
            "PSN-PSOE": (61531, 11),
            "EH": (47271, 8),
            "IU-EB": (20879, 3),
            "CDN": (20821, 3),
            "EA_EAJ-PNV": (16512, 3),
            "IN": (2835, 0),
            "EKA": (869, 0),
        },
    ),
    2003: (
        (464826, 328609, 21292, 7304),
        {
            "UPN": (127460, 23),
            "PSOE": (65003, 11),
            "IUN_NEB": (26962, 4),
            "ARALAR": (24068, 4),
            "CDN": (23516, 4),
            "EA_EAJ-PNV": (22824, 4),
            "BATZARRE": (7873, 0),
            "PH": (1290, 0),
            "EKA": (1017, 0),
        },
    ),
    2007: (
        (471653, 348039, 18301, 4581),
        {
            "UPN": (139132, 22),
            "NA-BAI": (77872, 12),
            "PSN-PSOE": (74158, 12),
            "CDN": (14412, 2),
            "IUN-NEB": (14337, 2),
            "RCN-NOK": (4705, 0),
            "EKA": (541, 0),
        },
    ),
    2011: (
        (485386, 327281, 4027, 8161),
        {
            "UPN": (111474, 19),
            "PSN-PSOE": (51238, 9),
            "NA-BAI2011": (49827, 8),
            "BILDU": (42916, 7),
            "PP": (23551, 4),
            "IZQ-EZK": (18457, 3),
            "ECOLO": (4235, 0),
            "UPyD": (2212, 0),
            "SAIn": (1054, 0),
            "CDN": (4654, 0),
            "IXN": (1332, 0),
            "RCN-NOK": (3166, 0),
            "DNE": (977, 0),
        },
    ),
    2015: (
        (501267, 342173, 4280, 6610),
        {
            "Geroa Bai": (53497, 9),
            "UPN": (92705, 15),
            "PSN": (45164, 7),
            "EH Bildu": (48166, 8),
            "UPyD": (1740, 0),
            "I-E": (12482, 2),
            "PP": (13289, 2),
            "Podemos": (46207, 7),
            "Ciudadanos": (9993, 0),
            "LN": (955, 0),
            "Equo": (2170, 0),
            "PACMA": (2304, 0),
            "SAIn": (880, 0),
            "RCN-NOK": (1733, 0),
        },
    ),
    2019: (
        (485405, 350370, 2169, 2731),
        {
            "NA+": (127346, 20),
            "PSN-PSOE": (71838, 11),
            "GBAI": (60323, 9),
            "EH Bildu": (50631, 7),
            "PODEMOS": (16518, 2),
            "I-E (n)": (10472, 1),
            "VOX": (4546, 0),
            "EQUO": (1597, 0),
            "RCN-NOK": (1251, 0),
            "Ln": (502, 0),
            "SAIN": (438, 0),
        },
    ),
    2023: (
        (518998, 334493, 4632, 5860),
        {
            "UPN": (92392, 15),
            "PSN-PSOE": (68247, 11),
            "EH_BILDU": (56535, 9),
            "GEROA_BAI": (43660, 7),
            "PPN": (24019, 3),
            "CONTIGO_ZUREKIN": (20095, 3),
            "VOX": (14197, 2),
            "PUM_MAS_J": (1740, 0),
            "CIUDADANOS": (1273, 0),
            "EGUZKILORE": (1261, 0),
            "VOLUNTAD_FORAL": (582, 0),
        },
    ),
}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in plain.lower() if char.isalnum())


def party_key(label: str) -> str:
    key = ALIASES.get(label, label.lower())
    if key not in PARTIES:
        raise KeyError(f"{label!r} has no party")
    return key


def csv_url(year: int) -> str:
    _, resource, name = ELECTIONS[year]
    return f"{CKAN}{resource}/download/{name}"


def fetch() -> None:
    for year in ELECTIONS:
        raw.fetch(SOURCE, csv_url(year), suffix=".csv")
    for link, _ in TABLES.values():
        raw.fetch(SOURCE, link, suffix=".html" if "contenido" in link else ".pdf")
    for link, suffix in PROCLAMATION.values():
        raw.fetch(SOURCE, link, suffix=suffix)
    raw.fetch(SOURCE, BOE_1983, suffix=".pdf")


def number(text: str) -> int:
    text = text.strip()
    return int(text) if text else 0


def read(year: int) -> tuple[list[dict], list[dict]]:
    content = raw.cached(SOURCE, csv_url(year)).path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text), delimiter=";" if text.count(";") > text.count(",") else ","))
    header = [fold(cell) for cell in rows[0]]
    fixed = {
        "electors": ("censototal", "censo"),
        "voters": ("votostotales", "votos", "participacion"),
        "invalid": ("votosnulos", "nulos"),
        "blank": ("votosblancos", "blancos", "blanco"),
        "valid": ("votosvalidos", "validos"),
        "cand": ("votoscandidaturas", "acandid", "candidaturas"),
    }
    columns = {
        name: next(header.index(key) for key in keys if key in header) if any(key in header for key in keys) else None
        for name, keys in fixed.items()
    }
    code = 0 if "codigo" in header else header.index("codmun") if "codmun" in header else header.index("cmunicipio")
    name = header.index("municipio")
    skip = {
        "codcir",
        "codmun",
        "municipio",
        "censo",
        "certifalta",
        "censototal",
        "votostotales",
        "votosnulos",
        "votosblancos",
    }
    skip |= {"abstencion", "abstenciones", "votosvalidos", "votoscandidaturas", "votos", "nulos", "validos", "blancos"}
    skip |= {"blanco", "participacion", "acandid", "candidaturas", "codigo", "cmunicipio"}
    first = max(index for index, key in enumerate(header) if key in skip) + 1
    units: list[dict] = []
    votes: list[dict] = []
    for cells in rows[1:]:
        cells = [cell.strip() for cell in cells]
        if not cells or not cells[code].isdigit():
            continue
        mun = int(cells[code])
        if mun > 910:
            mun = 999
        unit = {"year": year, "mun": f"{mun:03d}", "name": cells[name]}
        for field, index in columns.items():
            unit[field] = None if index is None else number(cells[index])
        lists = [
            (rows[0][index].strip(), number(cells[index]))
            for index in range(first, len(header))
            if rows[0][index].strip()
        ]
        unit["list_votes"] = sum(count for _, count in lists)
        units.append(unit)
        for label, count in lists:
            votes.append({"year": year, "mun": unit["mun"], "label": label, "votes": count})
    return units, votes


def text_of(year: int) -> str:
    path = raw.cached(SOURCE, PROCLAMATION[year][0]).path
    return subprocess.run(["pdftotext", "-raw", str(path), "-"], capture_output=True, text=True, check=True).stdout


def members(year: int) -> list[dict]:
    text = text_of(year)
    start = text.index("relación remitida a la Cámara") if year != 2023 else 0
    text = text[start:]
    stop = min(index for marker in ("Recursos contencioso", "Así mismo") if (index := text.find(marker)) > 0)
    lines = [
        line
        for line in text[:stop].splitlines()
        if not line.startswith(("D.S. del Parlamento", "(1) La traducción")) and not line.strip().isdigit()
    ]
    body = re.sub(r"-\n(?=[a-zñ])", "", "\n".join(lines))
    body = re.sub(r"^tinuación de cada párrafo\.", "", body, flags=re.MULTILINE)
    body = " ".join(body.split())
    body = re.sub(r"(?<=[a-zñáéíóú])(?=[A-ZÁÉÍÓÚ][a-zñáéíóú])", " ", body.replace("doñaAin", "doña Ain"))
    out: list[dict] = []
    parts = re.split(r"Por la candidatura (?:de |del )?", body)[1:]
    for part in parts:
        match = re.match(r"(.+?)[:,] (.+)", part)
        if match is None:
            raise ValueError(f"{year}: {part[:80]!r}")
        key = HEADINGS.get(fold(match[1].replace("de la ", "").strip()))
        if key is None:
            key = HEADINGS[fold(re.sub(r"^(de |del )", "", match[1]))]
        names = re.sub(r"^han sido proclamados los siguientes: ", "", re.sub(r"\.\s*$", "", match[2])).replace(
            "Barcos", "Barkos"
        )
        for position, name in enumerate(re.split(r",\s+| y (?=(?:don |doña )?[A-ZÁÉÍÓÚ])", names), 1):
            name = re.sub(r"^(?:y )?(?:don|doña) ", "", name.strip())
            out.append({"year": year, "key": key, "position": position, "name": name})
    return out


def dhondt(votes: dict[str, int], valid: int, year: int) -> Counter[str]:
    eligible = {code: count for code, count in votes.items() if count >= THRESHOLD.get(year, 0.03) * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, SEATS + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:SEATS])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    units: list[dict] = []
    votes: list[dict] = []
    seats: list[dict] = []
    people: list[dict] = []
    for year in ELECTIONS:
        year_units, year_votes = read(year)
        units += year_units
        votes += year_votes
        totals: Counter[str] = Counter()
        for row in year_votes:
            totals[row["label"]] += row["votes"]
        (_, voters, _, blank), lists = OFFICIAL[year]
        listed = sum(count for count, _ in lists.values())
        valid = listed if year == 1983 else listed + blank
        print(year, "units", len(year_units), "voters", sum(row["voters"] for row in year_units), voters)
        print(year, "lists", sum(totals.values()), listed, "seats", sum(seat for _, seat in lists.values()))
        won = dhondt({label: count for label, (count, _) in lists.items()}, valid, year)
        print(year, "dhondt differs", sorted(label for label, (_, seat) in lists.items() if won[label] != seat))
        for label, (count, seat) in lists.items():
            seats.append({"year": year, "label": label, "key": party_key(label), "votes": count, "seats": seat})
            if label in totals and totals[label] != count:
                print(year, label, "file", totals[label], "official", count)
        if year in PROCLAMATION and year != 2023:
            found = members(year)
            people += found
            per = Counter(row["key"] for row in found)
            declared = Counter({party_key(label): seat for label, (_, seat) in lists.items() if seat})
            print(year, "members", len(found), "match", per == declared)
    for year, table in ((1983, MEMBERS_1983), (2023, MEMBERS_2023)):
        for key, names in table.items():
            people += [
                {"year": year, "key": key, "position": index, "name": name} for index, name in enumerate(names, 1)
            ]
    for year in (1983, 2015, 2019, 2023):
        per = Counter(row["key"] for row in people if row["year"] == year)
        declared = Counter({party_key(label): seat for label, (_, seat) in OFFICIAL[year][1].items() if seat})
        if per != declared:
            raise ValueError(f"{year}: members {dict(per)} against seats {dict(declared)}")
    for name, rows in {"unit": units, "votes": votes, "seats": seats, "people": people}.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


SYSTEM_1983 = (
    "Closed party lists in a single constituency, the whole of Navarre, electing 50 members, under the Foral Decree "
    "8/1983 of 9 March 1983 that called the first election to the Parliament of Navarre. Seats went by the D'Hondt "
    "method to the lists with at least 5% of the valid votes, which that acta counts without blank ballots, so the "
    "threshold was 13,204 of 264,081 votes. The Junta Electoral Provincial de Navarra proclaimed the members."
)
SYSTEM = (
    "Closed party lists in a single constituency, the whole of Navarre, electing 50 members. Seats go by the D'Hondt "
    "method to the lists with at least 3% of the valid votes, blank ballots counted as valid ("
    + LAW
    + "; "
    + STATUTE
    + "). The Junta Electoral Provincial de Navarra proclaims the members, and the seats stored here are those "
    "declared in the Parlamento de Navarra's results pages."
)
NAVARRA_METHOD = (
    "Ballot counts and list votes for the whole of Navarre as the Parlamento de Navarra publishes them for the "
    "{legislature}, with the seats each list was declared to have won. Valid ballots here are the votes to candidacies, "
    "with blank ballots stored apart. The municipality files of the Gobierno de Navarra add up to {file:,} list votes, "
    "against {official:,} here.{differs}"
)
DIFFERS = " The file's total differs from the Parlamento's for {labels}."
CORRECTED = {
    1987: (
        " The Parliament's table prints 38,111 votes for Herri Batasuna, which does not add up to its 279,445 votes to "
        "candidacies; the municipality file's 38,138 does, and is stored."
    )
}
MEMBERS_NOTE = (
    " The elected members are those the Junta Electoral Provincial de Navarra proclaimed, as read to the Chamber at "
    "its constitutive session."
)
MUNICIPIO_METHOD = (
    "List votes and ballot counts by municipality from the Gobierno de Navarra's open-data results file, with the "
    "votes of residents abroad (CERA) on a separate row. Municipalities sit on the INE municipality codes. The "
    "municipality figures add up to {votes:,} list votes, against {official:,} in the Navarre result."
)


def emit() -> Path:
    units = pl.read_parquet(PARSED / "unit.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    people = pl.read_parquet(PARSED / "people.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parlamento de Navarra elections of 1983 to 2023 by municipality, with the seats each list was "
                "declared to have won and the members proclaimed elected in 1983, 2015, 2019, and 2023, from the Gobierno de "
                "Navarra's results files and the Parlamento de Navarra's results pages"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, navarre = cs.polity("ES"), cs.polity("ES-NC")
        for scheme in ("es.provincia", "es.municipio"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine before the Parlamento de Navarra")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license="Creative Commons Attribution 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="Fuente: Gobierno de Navarra, datosabiertos.navarra.es (CC BY 4.0).",
        )
        parliament = cs.source(PARLIAMENT, PARLIAMENT_TITLE)
        cs.add(
            "source",
            id=parliament,
            kind="official_body",
            publisher=PARLIAMENT,
            title=PARLIAMENT_TITLE,
            url=PARLIAMENT_PORTAL,
            license=None,
            license_url=None,
            attribution="Fuente: Parlamento de Navarra.",
        )
        boe = cs.source(BOE, BOE_TITLE)
        cs.add(
            "source",
            id=boe,
            kind="official_body",
            publisher=BOE,
            title=BOE_TITLE,
            url=BOE_1983,
            license=None,
            license_url=None,
            attribution="Fuente: Boletín Oficial del Estado.",
        )
        cs.artifact(raw.cached(SOURCE, BOE_1983), boe)
        for year in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, csv_url(year)), source)
        for link, _ in TABLES.values():
            cs.artifact(raw.cached(SOURCE, link), parliament)
        for link, _ in PROCLAMATION.values():
            cs.artifact(raw.cached(SOURCE, link), parliament)

        parties: dict[str, UUID] = {}
        for key, (native, english) in PARTIES.items():
            parties[key] = cs.key(f"{SOURCE}.party", key)
            cs.add("party", id=parties[key], kind="party", polity_id=navarre)
            name(parties[key], native, "es", "official")
            if english != native:
                name(parties[key], english, "en", "english")

        body = cs.key("es.body", "navarre-parliament")
        cs.add("body", id=body, polity_id=navarre, kind="unicameral")
        name(body, "Parliament of Navarre")
        name(body, "Parlamento de Navarra", "es", "native")
        systems: dict[int, UUID] = {}
        for percent, label, text in ((3, "navarre-parliament", SYSTEM), (5, "navarre-parliament-1983", SYSTEM_1983)):
            system = systems[percent] = cs.key("es.electoral_system", label)
            cs.add(
                "electoral_system",
                id=system,
                polity_id=navarre,
                rules={
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": percent},
                },
                rules_version=1,
            )
            name(system, f"Parliament of Navarre closed-list D'Hondt system with a {percent}% threshold")
            cs.add("note", entity_id=system, kind="methodology", body_md=text)
        cs.add("body_size", body_id=body, seats=SEATS, valid_from=ELECTIONS[1983][0], valid_to=None)

        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province = cs.unit("es.provincia", "31")
        unit_rows[province] = (
            province,
            "es.provincia",
            "31",
            None,
            "equals",
            cs.area("es.provincia", "31"),
            "standard",
        )
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(mun: str) -> UUID:
            code = "31" + mun
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if mun == "999":
                    area, relation, kind = cs.area("es.provincia", "31"), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province, relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int | None], channel: int = 0) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "cand"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, channel))

        for year, (day, *_) in ELECTIONS.items():
            election = cs.key("es.election", f"navarra:{day}")
            cs.add(
                "election", id=election, polity_id=navarre, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, f"{year} Navarrese regional election")
            name(election, f"Elecciones al Parlamento de Navarra de {year}", "es", "native")
            path = f"es/na/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            key = str(day)
            contest = cs.key(f"{SOURCE}.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=cs.area("es.provincia", "31"),
                magnitude=SEATS,
                electoral_system_id=systems[5 if year == 1983 else 3],
                default_result_kind="counted",
            )
            name(contest, "Navarre")
            cs.add("slug", path=f"{path}/navarra", entity_id=contest)
            official = seats.filter(pl.col("year") == year).sort("votes", "label", descending=[True, False])
            options: dict[str, UUID] = {}
            for entry in official.iter_rows(named=True):
                option = options[entry["key"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['key']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=parties[entry["key"]],
                    ballot_label=PARTIES[entry["key"]][0],
                )

            (electors, voters, invalid, blank), _ = OFFICIAL[year]
            listed = int(official["votes"].sum())
            place = units.filter(pl.col("year") == year)
            place_votes = votes.filter(pl.col("year") == year).with_columns(
                key=pl.col("label").map_elements(party_key, return_dtype=pl.String)
            )
            got = int(place_votes["votes"].sum())
            by_label = place_votes.group_by("label").agg(pl.col("votes").sum())
            file_total = dict(by_label.iter_rows())
            labels = [
                PARTIES[party_key(row["label"])][0]
                for row in official.iter_rows(named=True)
                if file_total.get(row["label"], row["votes"]) != row["votes"]
            ]
            _, table = TABLES[year]
            note = NAVARRA_METHOD.format(
                legislature=table.split(", ")[-1] if year > 2007 else "Parlamento's results pages",
                file=got,
                official=listed,
                differs=DIFFERS.format(labels=", ".join(labels)) if labels else "",
            )
            note += CORRECTED.get(year, "")
            declared = people.filter(pl.col("year") == year)
            if not declared.is_empty():
                note += MEMBERS_NOTE

            def make_set(label: str, default: bool, completeness: float, text: str, origin: UUID) -> UUID:
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=origin,
                    is_default=default,
                    completeness=completeness,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=text)
                return result_set

            navarre_set = make_set("navarra", True, 1, note, boe if year == 1983 else parliament)
            accounting(
                navarre_set,
                province,
                {"electors": electors, "voters": voters, "cand": listed, "invalid": invalid, "blank": blank},
            )
            for entry in official.iter_rows(named=True):
                values.append((str(navarre_set), str(province), str(options[entry["key"]]), VOTES, entry["votes"], 0))
                if entry["seats"]:
                    cs.add(
                        "outcome",
                        result_set_id=navarre_set,
                        option_id=options[entry["key"]],
                        kind="won",
                        seats=entry["seats"],
                    )
            for member in declared.iter_rows(named=True):
                person_key = f"{key}:{member['key']}:{fold(member['name'])}"
                person = cs.key(f"{SOURCE}.person", person_key)
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, member["name"], "es")
                party = parties[member["key"]]
                option = cs.key(f"{SOURCE}.option", person_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=options[member["key"]],
                    party_id=party,
                    ballot_label=member["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", person_key),
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=member["name"],
                    member_of_party_id=party,
                    is_independent=False,
                )
                cs.add("outcome", result_set_id=navarre_set, option_id=option, kind="elected")

            municipio_set = make_set(
                "municipio",
                False,
                min(got / listed, 1),
                MUNICIPIO_METHOD.format(votes=got, official=listed),
                source,
            )
            for row in place.iter_rows(named=True):
                accounting(
                    municipio_set,
                    municipality_unit(row["mun"]),
                    {**row, "cand": row["list_votes"]},
                    ABROAD if row["mun"] == "999" else 0,
                )
            for row in place_votes.iter_rows(named=True):
                values.append(
                    (
                        str(municipio_set),
                        str(municipality_unit(row["mun"])),
                        str(options[row["key"]]),
                        VOTES,
                        row["votes"],
                        ABROAD if row["mun"] == "999" else 0,
                    )
                )

        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(unit),
                        "polity_id": str(spain),
                        "scheme_code": scheme,
                        "code": code,
                        "parent_id": None if parent is None else str(parent),
                        "area_relation": relation,
                        "area_id": None if area is None else str(area),
                        "kind": kind,
                    }
                    for unit, scheme, code, parent, relation, area, kind in unit_rows.values()
                    if unit in cs.minted
                ],
                schema_overrides={"parent_id": pl.String, "area_id": pl.String},
            ),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                    "channel": pl.Int16,
                },
                orient="row",
            ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
