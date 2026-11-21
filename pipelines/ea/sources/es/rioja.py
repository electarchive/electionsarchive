import csv
import io
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl
import pymupdf
from psycopg import Connection

from ea import package, raw
from ea.package import Draft
from ea.store import DATA, connect

SOURCE = "es.rioja"
KEY = f"{SOURCE}/all"
VERSION = "1"
PUBLISHER = "Gobierno de La Rioja"
TITLE = "Elecciones autonómicas: resultados por municipio y reparto de escaños (datos abiertos de La Rioja)"
PORTAL = "https://web.larioja.org/dato-abierto/datoabierto?n=opd-392"
MUNICIPALITIES = "https://ias1.larioja.org/opendata/download?r=Y2Q9MzczfGNmPTAz"
SEATS = "https://ias1.larioja.org/opendata/download?r=Y2Q9MzkyfGNmPTAz"
JEC = "https://www.juntaelectoralcentral.es/cs/jec/documentos/"
JUNTA = "Junta Electoral de la Comunidad Autónoma de La Rioja"
# The members the Junta Electoral proclaimed elected, from the copies the Junta Electoral Central keeps of each
# publication (and for 2019, which the bulletin's results notice leaves out, the acta the Parliament keeps), as
# (address, publisher, title, where it was printed).
PROCLAMATIONS = {
    1983: (
        JEC + "LA_RIOJA_1983_Resultados.pdf",
        "Junta Electoral Provincial de La Rioja",
        (
            "Acta de escrutinio general de las elecciones a la Diputación General de La Rioja y relación de candidatos "
            "proclamados en las elecciones autonómicas de La Rioja"
        ),
        "BOE núm. 258, 28 de octubre de 1983",
    ),
    1987: (
        JEC + "LA_RIOJA_1987_Electos.pdf",
        JUNTA,
        (
            "Elecciones a la Diputación General de La Rioja 1987. Acta de proclamación de electos celebrada el día 22 de "
            "junio de 1987"
        ),
        "BOR núm. 79, 4 de julio de 1987",
    ),
    1991: (
        JEC + "LA_RIOJA_1991_Electos.pdf",
        JUNTA,
        "Proclamación oficial de candidatos electos en las Elecciones Autonómicas de 1991",
        "BOR núm. 74, 13 de junio de 1991",
    ),
    1995: (
        JEC + "LA_RIOJA_1995_Electos.pdf",
        JUNTA,
        "Acuerdo de remisión del acta de proclamación de electos",
        "BOR núm. 73, 13 de junio de 1995",
    ),
    1999: (
        JEC + "LA_RIOJA_1999_Electos.pdf",
        JUNTA,
        (
            "Certificación del acta de proclamación de electos en las elecciones al Parlamento de La Rioja convocadas "
            "mediante Decreto 1/1999, de 19 de abril"
        ),
        "BOR núm. 78, 26 de junio de 1999",
    ),
    2003: (
        JEC + "LA_RIOJA_2003_Electos.pdf",
        JUNTA,
        (
            "Certificación del acta de proclamación de electos en las elecciones al Parlamento de La Rioja convocadas "
            "mediante Decreto 1/2003, de 31 de marzo"
        ),
        "BOR núm. 78, 24 de junio de 2003",
    ),
    2007: (
        JEC + "LA_RIOJA_2007_Electos.pdf",
        JUNTA,
        "Acta de proclamación oficial de candidatos electos en las Elecciones Autonómicas",
        "BOR núm. 84, 23 de junio de 2007",
    ),
    2011: (
        JEC + "LA_RIOJA_2011_Resultados.pdf",
        JUNTA,
        (
            "Resultados de las elecciones al Parlamento de La Rioja 2011 y acta de proclamación oficial de candidatos "
            "electos en las Elecciones Autonómicas"
        ),
        "BOR núm. 73, 8 de junio de 2011",
    ),
    2015: (
        JEC + "LA_RIOJA_2015_Resultados.pdf",
        JUNTA,
        (
            "Resultados de las Elecciones al Parlamento de La Rioja 2015 y acta de proclamación oficial de candidatos "
            "electos en las Elecciones Autonómicas 2015"
        ),
        "BOR núm. 73, 5 de junio de 2015",
    ),
    2019: (
        (
            "https://www.parlamento-larioja.org/ficheros/junta-electoral-car/acuerdos2019/"
            "jecar-2019-acta-proclamacion-de-candidatos"
        ),
        JUNTA,
        (
            "Acta de proclamación de candidatos electos al Parlamento de La Rioja en las elecciones celebradas el día 26 "
            "de mayo de 2019"
        ),
        "acta de 31 de mayo de 2019",
    ),
    2023: (
        JEC + "Publicación%20acta%20candidatos%20electos.pdf",
        JUNTA,
        (
            "Publicación de los datos del acta de proclamación oficial de candidatos electos en las elecciones al "
            "Parlamento de La Rioja 2023"
        ),
        "BOR núm. 125, 20 de junio de 2023",
    ),
}
# The 2019 acta names the members without their lists, so each is found on the list the Junta proclaimed.
CANDIDACIES_2019 = (
    JEC + "LA_RIOJA_2019_Candidaturas%20proclamadas.pdf",
    JUNTA,
    "Proclamación de candidaturas - Elecciones Autonómicas 2019",
    "BOR núm. 51, 30 de abril de 2019",
)
SIGLAS_2019 = {"Cs": "cs", "PSOE": "psoe", "PODEMOS-IU-EQUO": "podemos-iu-equo", "PR+": "pr", "PP": "pp"}
# The lists in the text of each proclamation, as each year prints them.
LABELS = {
    "P.P.": "pp",
    "PP": "pp",
    "PSOE": "psoe",
    "PR": "pr",
    "PARTIDO RIOJANO": "pr",
    "C’S": "cs",
    "C'S": "cs",
    "PODEMOS": "podemos",
    "PODEMOS-I.U.": "podemos-iu",
    "VOX": "vox",
}
MEMBER = r"(?:D\.ª|Dª|D\.)?\s*([A-Za-zÀ-ÿª’'\- ]+?)\s*\(({labels})\)"
# Letters the text layer of a proclamation dropped or misprinted, fixed against the other years' proclamations.
CORRECTIONS = {
    (2003, "José Tóledo Sobrón"): "José Toledo Sobrón",
    (2011, "esús María García García"): "Jesús María García García",
}
# The scanned proclamations of 1983 to 1995, read from their page images: (page, {list: members in printed order}).
# 1983 follows the acta's proclamation, with the spellings of the list of members printed after it where the acta
# misprints a name (Zueco, Lasierra, Pedro Benito, Miguel Angel Valoria), and otherwise the acta's (Neftalí, as the
# acta's list of those present also prints it, Zárate, Bernadó); 1987 prints "Féliz" and "Josá".
SCANNED: dict[int, tuple[int, dict[str, list[str]]]] = {
    1983: (
        2,
        {
            "psoe": [
                "José María de Miguel Gil",
                "Félix Palomo Saavedra",
                "José María Buzarra Cano",
                "María del Carmen Valle de Juan",
                "José Ignacio Pérez Sáenz",
                "Benito Hermosilla Aguillo",
                "Mario Fraile Ruiz",
                "Carlos Esteve Blasco",
                "Teódulo Lorenzo Zárate Ibarra",
                "Angel Sáinz de Azuelo Caro",
                "Luis María Miguel Calavia",
                "José Luis Calahorra Gil",
                "Jesús Casero Beltrán",
                "Ignacio Díez González",
                "Hilario Cereceda Alonso",
                "Antonio Martínez Tricio",
                "Raúl Miguel Sanz Jiménez",
                "Pedro Luis Arpón Rubio",
            ],
            "cp": [
                "Joaquín Espert Pérez-Caballero",
                "Ignacio Javier Becerra Guibert",
                "Jesús Zueco Ruiz",
                "Neftalí Isasi Gómez",
                "María Pilar Salarrullana de Verda",
                "Pedro Benito Urbina",
                "José Arrieta San Miguel",
                "Joaquín Bernadó Valmaseda",
                "Juan Gimeno San Juan",
                "Joaquín Lasierra Cirujeda",
                "Tomás Moreno Orio",
                "Miguel Angel Valoria Escalona",
                "Emilio Carreras Castellet",
                "Pedro López de Heredia Ugalde",
                "María del Carmen de Miguel Cordón",
            ],
            "pr": ["Luis Javier Rodríguez Moroy", "Francisco Díaz Yubero"],
        },
    ),
    1987: (
        1,
        {
            "psoe": [
                "Alicia Izaguirre Albiztur",
                "Félix Palomo Saavedra",
                "José María Buzarra Cano",
                "Mario Fraile Ruiz",
                "Pablo Miguel Rubio Medrano",
                "José Ignacio Pérez Sáenz",
                "José Julián Medrano Martín",
                "Carmelo Fernández Herrero",
                "Eduardo Peche Echevarria",
                "Mª Antonia San Felipe Adán",
                "Santiago Orio Pérez",
                "Benito Hermosilla Aguillo",
                "Julio Romera Huerta",
                "Ana Isabel Leiva Díez",
            ],
            "ap": [
                "Joaquín Espert Pérez-Caballero",
                "Fausto Vadillo Arnáez",
                "Jesús Zueco Ruiz",
                "José Antonio Elguea Nalda",
                "José Antonio González Garnica",
                "Carlos Benito Benito",
                "Luis Angel Alegre Galilea",
                "Alberto Olarte Arce",
                "Angel López Martínez",
                "Ricardo Achútegui Antoñanzas",
                "Pedro Benito Urbina",
                "Tomás Moreno Orio",
                "Mª del Carmen De Miguel Cordón",
            ],
            "cds": [
                "Manuel Mª Fernández Ilarraza",
                "Luis Fernández Rodríguez",
                "Tomás Valdivieso Tejeiro",
                "Pedro Antonio Marín Gil",
            ],
            "pr": ["Luis Javier Rodríguez Moroy", "Leopoldo Virosta Garoz"],
        },
    ),
    1991: (
        1,
        {
            "psoe": [
                "José Ignacio Pérez Sáenz",
                "Félix Palomo Saavedra",
                "Mario Fraile Ruiz",
                "Pablo Miguel Rubio Medrano",
                "Mª Carmen Valle de Juan",
                "José Julián Medrano Martín",
                "Eduardo Peche Echeverría",
                "María Antonia San Felipe Adán",
                "Miguel Angel Prieto Echegaray",
                "Francisco Javier Ruiz Aznárez",
                "José Francisco Ruiz Pérez",
                "Santiago Orio Pérez",
                "Julián Angel Jiménez Velilla",
                "José María Rodríguez Andrés",
                "Enrique Luis Aldonza Martínez",
                "Ana Isabel Leiva Díez",
            ],
            "pp": [
                "Joaquín Espert Pérez-Caballero",
                "Julio Luis Fernández Sevilla",
                "Mª del Carmen Las Heras Pérez-Caballero",
                "Antonio Herreros Hernández",
                "Pedro María Sanz Alonso",
                "Mª del Carmen Gómez Bozalongo",
                "Juan José Irastorza Aldasoro",
                "Francisco Narciso Jiménez Jiménez",
                "Martín Alberto Torroba Terroba",
                "Alberto Olarte Arce",
                "Damián Sáez Angulo",
                "Mª Aránzazu Vallejo Fernández",
                "Amando González Sáenz",
                "Raimundo Bustillo Cañas",
                "Mª José Palacios Anguiano",
            ],
            "pr": ["Leopoldo Virosta Garoz", "Miguel Angel Ropero Sáez"],
        },
    ),
    1995: (
        1,
        {
            "pp": [
                "Pedro María Sanz Alonso",
                "Mª del Carmen Las Heras Pérez-Caballero",
                "José Ignacio Ceniceros González",
                "Mª Aránzazu Vallejo Fernández",
                "Luis Fernández Rodríguez",
                "Conrado Escobar Las Heras",
                "Luis Torres Sáez Benito",
                "Mª del Carmen Gómez Bozalongo",
                "Alberto Olarte Arce",
                "José María Ruiz-Alejos Herrero",
                "David Isasi García",
                "Amando González Sáenz",
                "Raimundo Bustillo Cañas",
                "Damián Sáez Angulo",
                "Clemente José Bea Segura",
                "Luis Martínez Portillo Subero",
                "María Cristina Sáenz de Pipaón Ibáñez",
            ],
            "psoe": [
                "José Ignacio Pérez Sáenz",
                "Ana Isabel Leiva Díez",
                "Pablo Miguel Rubio Medrano",
                "Mario Fraile Ruiz",
                "José Antonio Ulecia Rodríguez",
                "María Carmen Valle de Juan",
                "José Julián Medrano Martín",
                "César de Marcos Hornos",
                "María Antonia San Felipe Adán",
                "Hilario Antonio García Aparicio",
                "Angel Ledesma Orive",
                "José Mª Bayo Moreno",
            ],
            "iu": ["Jesús Rodríguez Rubio", "Vicente Pascual Ocio"],
            "pr": ["Leopoldo Virosta Garoz", "Miguel Mª González de Legarra"],
        },
    ),
}
# One person printed two ways across the proclamations, keyed by the folded form of the other printing.
SAME_PERSON = {
    "maria del carmen valle de juan": "maria carmen valle de juan",
    "eduardo peche echevarria": "eduardo peche echeverria",
    "inmaculada ortega martinez": "maria inmaculada ortega martinez",
    "maria solana gimenez": "maria basilisa solana gimenez",
    "jose angel lacalzada esquibel": "jose angel lacalzada esquivel",
}
PARTICLES = {"de", "del", "la", "las", "los", "y"}
LAW = "Ley 3/1991, de 21 de marzo, de Elecciones a la Diputación General de La Rioja"
STATUTE = "Ley Orgánica 3/1982, de 9 de junio, de Estatuto de Autonomía de La Rioja"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 0.05
PARSED = DATA / "parsed" / SOURCE
DAYS = {
    1983: date(1983, 5, 8),
    1987: date(1987, 6, 10),
    1991: date(1991, 5, 26),
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
KEYS = {
    "AP": "ap",
    "PR": "pr",
    "PR+": "pr",
    "PR+E": "pr",
    "I.U.": "iu",
    "IU": "iu",
    "AR.": "ar",
    "IZQ.": "izq",
    "C's": "cs",
    "Cs": "cs",
    "UPYD": "upyd",
    "UPyD": "upyd",
    "P.C.P.E.": "pcpe",
    "PCPE": "pcpe",
    "PUM+J": "pumj",
    "ESCAÑOS EN BLANCO": "eb",
    "EB": "eb",
    "PODEMOS-I.U.": "podemos-iu",
    "PODEMOS-IU-EQUO": "podemos-iu-equo",
}
PARTIES = {
    "psoe": ("Partido Socialista Obrero Español", "Q138198"),
    "pp": ("Partido Popular", "Q185088"),
    "ap": ("Alianza Popular", "Q2838243"),
    "cp": ("Coalición Popular", "Q3393021"),
    "pr": ("Partido Riojano", None),
    "cds": ("Centro Democrático y Social", "Q788345"),
    "pce": ("Partido Comunista de España", "Q478811"),
    "pdp": ("Partido Demócrata Popular", None),
    "pte-uc": ("Partido de los Trabajadores de España-Unidad Comunista", "Q3133165"),
    "iu": ("Izquierda Unida de La Rioja", None),
    "ar": ("Alternativa Riojana", None),
    "verdes": ("Los Verdes de La Rioja", None),
    "mash": ("Movimiento al Socialismo Humanista", None),
    "izq": ("Coalición Electoral IZQ: Izquierda Unida, Iniciativa Ciudadana, Los Verdes", None),
    "upyd": ("Unión Progreso y Democracia", "Q1144342"),
    "ecolo-verde": ("Coalición Verdes de La Rioja", None),
    "pumj": ("Por un Mundo Más Justo", None),
    "pacma": ("Partido Animalista con el Medio Ambiente", "Q82558"),
    "pcpe": ("Partido Comunista de los Pueblos de España", None),
    "podemos": ("Podemos", "Q16059622"),
    "cs": ("Ciudadanos-Partido de la Ciudadanía", "Q1393123"),
    "cr-iu-equo": ("Cambia La Rioja-IU-Equo", None),
    "eb": ("Escaños en Blanco", None),
    "podemos-iu-equo": ("Unidas Podemos Izquierda Unida-Equo", None),
    "vox": ("Vox", "Q15630787"),
    "podemos-iu": ("Podemos-Izquierda Unida", None),
    "plri": ("Por La Rioja", None),
    "vinea": ("Vinea La Rioja", None),
}
SYSTEM = (
    "Closed party lists in a single constituency, the whole of La Rioja. Seats go by the D'Hondt method to the lists "
    "with at least 5% of the valid votes cast in the community, blank ballots counted as valid. The Diputación General "
    "(the Parliament of La Rioja) had 35 members in 1983 and has had 33 since 1987 (" + STATUTE + "; " + LAW + ")."
)
RIOJA_METHOD = (
    "List votes, registered voters, ballots cast, blank ballots, and null ballots for the whole of La Rioja from the "
    "Government of La Rioja's open-data results by municipality (the community row), with the seats each list won "
    "from its open-data seat allocation, whose list votes are the same. Valid ballots here are the votes to "
    "candidacies, with blank ballots stored apart."
)
MUNICIPIO_METHOD = (
    "List votes, registered voters, ballots cast, blank ballots, and null ballots by municipality from the Government "
    "of La Rioja's open-data results, on the INE municipality codes. {abroad}The municipality figures add up to "
    "{votes:,} list votes, against {official:,} in the La Rioja result."
)
ABROAD_ROW = "From 2007 the votes of residents abroad (CERA) are on a separate row. "
DHONDT_DIFFERS = (
    " D'Hondt with a 5% threshold applied to these votes gives a different split from the declared seats, which are "
    "stored as published."
)
MEMBERS_NOTE = (
    " The members elected are those the {publisher} proclaimed ({printed}){copy}, and each list's members equal its "
    "seats."
)
JEC_COPY = ", from the copy the Junta Electoral Central keeps"
MEMBERS_EXTRA = {
    1983: (
        " The proclamation is in the acta of the general count, printed in the Boletín Oficial del Estado with the "
        "list of the members it proclaimed, which gives each member's place on the candidacy. Where the two spell a "
        "name differently, the archive keeps the list's Zueco, Lasierra, Pedro Benito, and Miguel Angel Valoria and the "
        "acta's Neftalí Isasi, Teódulo Lorenzo Zárate, and Joaquín Bernadó."
    ),
    1987: (
        " The proclamation, a scan, was read from its page image; it misprints Félix Palomo and José María Buzarra as "
        '"Féliz" and "Josá".'
    ),
    1991: " The proclamation, a scan, was read from its page image.",
    1995: " The proclamation, a scan, was read from its page image.",
    2019: (
        " For 2019 the results notice in the Boletín Oficial de La Rioja (núm. 68, 5 June 2019) leaves the members "
        "out, so they come from the acta of proclamation the Parliament of La Rioja keeps, which names them in the "
        "order seats were won but not their lists; each member is found on the candidacy the Junta proclaimed (BOR "
        "núm. 51, 30 April 2019), which gives the list and place, and every member holds one of the first places of "
        "a list that won that many seats."
    ),
}


def party_key(year: int, siglas: str) -> str:
    if siglas == "AP" and year == 1983:
        return "cp"
    key = KEYS.get(siglas, siglas.lower())
    if key not in PARTIES:
        raise KeyError(f"{year} {siglas!r} has no party")
    return key


def fetch() -> None:
    raw.fetch(SOURCE, MUNICIPALITIES, suffix=".csv")
    raw.fetch(SOURCE, SEATS, suffix=".csv")
    for link, *_ in [*PROCLAMATIONS.values(), CANDIDACIES_2019]:
        raw.fetch(SOURCE, link, suffix=".pdf")


def expand(text: str) -> str:
    return " ".join(re.sub(r"\b[Mm]\.?ª", "María", text).split())


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", expand(text)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z]+", " ", plain).split())


def person_key(name: str) -> str:
    """Members are one person across elections when their names fold alike, or are two printings of one name."""
    return SAME_PERSON.get(fold(name), fold(name))


def display(printed: str) -> str:
    """A name as the proclamation prints it, with María written out and names printed in capitals in title case."""
    if printed.replace("ª", "").upper() != printed.replace("ª", ""):
        return expand(printed)
    words = expand(printed.lower()).split()
    return " ".join(
        word if i and word in PARTICLES else "-".join(part[:1].upper() + part[1:] for part in word.split("-"))
        for i, word in enumerate(words)
    )


def pages(link: str) -> list[str]:
    with pymupdf.open(raw.cached(SOURCE, link).path) as doc:
        return [" ".join(str(page.get_text()).replace("￿", " ").split()) for page in doc]


def printed_members(year: int) -> list[dict[str, str | int | None]]:
    """The members a proclamation's text names, each with the list in parentheses after the name."""
    labels = "|".join(re.escape(label) for label in sorted(LABELS, key=len, reverse=True))
    pattern = re.compile(MEMBER.format(labels=labels), re.IGNORECASE)
    found: list[dict[str, str | int | None]] = []
    for number, text in enumerate(pages(PROCLAMATIONS[year][0]), 1):
        for match in pattern.finditer(text):
            name = re.sub(r"^(?:Dª|D)\s+", "", match.group(1).strip(" -'’"))
            name = CORRECTIONS.get((year, name), name)
            found.append(
                {"year": year, "key": LABELS[match.group(2).upper()], "name": name, "position": None, "page": number}
            )
    return found


def members_2019() -> list[dict[str, str | int | None]]:
    """The 2019 acta numbers the members without their lists, so each is found on the list proclaimed for it."""
    lists: dict[str, tuple[str, int]] = {}
    with pymupdf.open(raw.cached(SOURCE, CANDIDACIES_2019[0]).path) as doc:
        sigla, substitutes = None, False
        for page in doc:
            for line in str(page.get_text()).replace("￿", " ").splitlines():
                if match := re.match(r"\s*Sigla:\s*(\S+)", line):
                    sigla, substitutes = match.group(1), False
                elif line.strip().startswith("Suplentes"):
                    substitutes = True
                elif sigla in SIGLAS_2019 and not substitutes and (match := re.match(r"\s*(\d{1,2})\s+(\S.*)$", line)):
                    name = re.split(r"\s{2,}", match.group(2).strip())[0]
                    lists[fold(name)] = (SIGLAS_2019[sigla], int(match.group(1)))
    (text,) = [page for page in pages(PROCLAMATIONS[2019][0]) if "se proclama electos" in page]
    text = text.split("siguientes candidatos:")[1].split("Reclamaciones")[0]
    names = [name.strip() for name in re.split(r"\b\d{1,2}\.?\s+", text) if name.strip()]
    found: list[dict[str, str | int | None]] = []
    for name in names:
        if fold(name) not in lists:
            raise ValueError(f"2019: {name} is on no proclaimed list")
        key, position = lists[fold(name)]
        found.append({"year": 2019, "key": key, "name": name, "position": position, "page": 2})
    return found


def members() -> list[dict[str, str | int | None]]:
    found: list[dict[str, str | int | None]] = []
    for year in PROCLAMATIONS:
        if year in SCANNED:
            page, lists = SCANNED[year]
            for key, names in lists.items():
                for i, name in enumerate(names, 1):
                    position = i if year == 1983 else None
                    found.append({"year": year, "key": key, "name": name, "position": position, "page": page})
        elif year == 2019:
            found += members_2019()
        else:
            found += printed_members(year)
    return found


def rows(link: str) -> list[dict[str, str]]:
    text = raw.cached(SOURCE, link).path.read_bytes().decode("cp1252")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {key: count for key, count in votes.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, key) for key, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(key for *_, key in quotients[:seats])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    declared: dict[int, list[dict[str, int | str]]] = {}
    for row in rows(SEATS):
        year = int(row["ANIO_ELECTORAL"])
        declared.setdefault(year, []).append(
            {
                "year": year,
                "key": party_key(year, row["SIGLAS_PARTIDO"]),
                "label": " ".join(row["DENOMINACION_PARTIDO"].split()),
                "votes": int(row["NUMERO_VOTOS"]),
                "seats": int(row["NUMERO_ESCANOS"]),
            }
        )
    units: list[dict[str, int | str]] = []
    votes: list[dict[str, int | str]] = []
    table = rows(MUNICIPALITIES)
    fields = list(table[0])
    columns = fields[fields.index("Porc_Escrutado") + 1 :]
    for year, lists in sorted(declared.items()):
        year_rows = [row for row in table if int(row["Anio_Electoral"]) == year]
        (total,) = [row for row in year_rows if row["Codigo_Municipio"] == "0"]
        by_votes = {int(entry["votes"]): str(entry["key"]) for entry in lists}
        if len(by_votes) != len(lists):
            raise ValueError(f"{year}: two lists with the same votes")
        mapping = {column: by_votes[int(total[column])] for column in columns if total[column]}
        if sorted(mapping.values()) != sorted(str(entry["key"]) for entry in lists):
            raise ValueError(f"{year}: the community row's lists differ from the seat allocation")
        seats = sum(int(str(entry["seats"])) for entry in lists)
        if seats != (35 if year == 1983 else 33):
            raise ValueError(f"{year}: {seats} seats")
        valid = int(total["Votos_Validos"])
        won = dhondt({str(e["key"]): int(str(e["votes"])) for e in lists}, valid, seats)
        if any(won[str(e["key"])] != e["seats"] for e in lists):
            print(f"{year}: D'Hondt with a 5% threshold does not give the declared seats")
        sums: Counter[str] = Counter()
        for row in year_rows:
            code = row["Codigo_Municipio"]
            unit = {
                "year": year,
                "mun": "" if code == "0" else f"{int(code):03d}",
                "name": row["Municipio"],
                "electors": int(row["Censo"]),
                "voters": int(row["Votos_Emitidos"]),
                "invalid": int(row["Votos_Nulos"]),
                "blank": int(row["Votos_en_blanco"]),
                "cand": int(row["Votos_Validos"]) - int(row["Votos_en_blanco"]),
            }
            units.append(unit)
            for column, key in mapping.items():
                if row[column]:
                    votes.append({"year": year, "mun": unit["mun"], "key": key, "votes": int(row[column])})
                    if code != "0":
                        sums[key] += int(row[column])
            stray = [column for column in columns if row[column] and column not in mapping]
            if stray:
                raise ValueError(f"{year} {code}: votes in {stray}")
        for entry in lists:
            if sums[str(entry["key"])] != entry["votes"]:
                print(f"{year} {entry['key']}: municipalities {sums[str(entry['key'])]:,} of {entry['votes']:,}")
    for name, frame in {
        "unit": pl.DataFrame(units),
        "votes": pl.DataFrame(votes),
        "seats": pl.DataFrame([entry for lists in declared.values() for entry in lists]),
    }.items():
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")
    elected = members()
    for year, lists in sorted(declared.items()):
        named = Counter(str(member["key"]) for member in elected if member["year"] == year)
        seats = {str(entry["key"]): int(str(entry["seats"])) for entry in lists if entry["seats"]}
        if named != seats:
            raise ValueError(f"{year}: the proclamation names {dict(named)} against the seats {seats}")
        if year == 2019:
            beyond = [
                m["name"] for m in elected if m["year"] == year and int(str(m["position"])) > seats[str(m["key"])]
            ]
            if beyond:
                raise ValueError(f"2019: {beyond} stand below their list's seats")
        print(f"{year}: {sum(named.values())} members proclaimed, {dict(named)}")
    folded = Counter((member["year"], person_key(str(member["name"]))) for member in elected)
    if max(folded.values()) > 1:
        raise ValueError(f"one name twice in a year: {[key for key, n in folded.items() if n > 1]}")
    frame = pl.DataFrame(elected, schema_overrides={"position": pl.Int32})
    frame.write_parquet(PARSED / "members.parquet")
    print(f"members: {frame.height} rows, {frame['name'].map_elements(fold, return_dtype=pl.String).n_unique()} names")
    build()


def emit() -> None:
    package.run("load", [KEY])


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


def build() -> Path:
    """Write the package es.rioja/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them."""
    units = pl.read_parquet(PARSED / "unit.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    # A member's name is the one the latest proclamation printed in capitals and small letters.
    names: dict[str, str] = {}
    for member in members.sort("year").iter_rows(named=True):
        shown = display(member["name"])
        if member["name"].replace("ª", "").upper() != member["name"].replace("ª", ""):
            names[person_key(member["name"])] = shown
        else:
            names.setdefault(person_key(member["name"]), shown)
    with connect() as conn:
        pin = retrieved(conn)
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parliament of La Rioja elections of 1983 to 2023 by municipality, with the seats per list, from the "
                "Government of La Rioja's open data, and the members the Junta Electoral proclaimed elected"
            ),
            polity="ES",
        )
        cs.accept(
            "accounting.votes_valid",
            "The Government of La Rioja's results by municipality give list votes that differ from the same row's votes "
            "to candidacies in Logroño in 1991 (55,299 against 55,297) and Lardero in 2003 (2,757 against 2,758); both "
            "are stored as published.",
        )
        cs.accept(
            "accounting.ballots_sum",
            "In the Government of La Rioja's results by municipality, Logroño's 1991 ballots cast (57,125) exceed its "
            "votes to candidacies, blank, and null ballots (57,123) by 2; stored as published.",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, rioja = cs.polity("ES"), cs.polity("ES-RI")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license="Reuse of public-sector information (Ley 37/2007), Government of La Rioja open data",
            license_url=None,
            attribution="Fuente: Gobierno de La Rioja, datos abiertos.",
        )
        cs.artifact(pin(raw.cached(SOURCE, MUNICIPALITIES)), source)
        cs.artifact(pin(raw.cached(SOURCE, SEATS)), source)

        def gazette(link: str, publisher: str, title: str, printed: str) -> UUID:
            item = cs.source(publisher, f"{title} ({printed})")
            cs.add(
                "source",
                id=item,
                kind="official_body",
                publisher=publisher,
                title=f"{title} ({printed})",
                url=link,
                license=None,
                license_url=None,
                attribution=f"Fuente: {publisher} ({printed}), copia de la Junta Electoral Central."
                if link.startswith(JEC)
                else f"Fuente: {publisher}, Parlamento de La Rioja.",
            )
            cs.artifact(pin(raw.cached(SOURCE, link)), item)
            return item

        proclamations = {year: gazette(*entry) for year, entry in PROCLAMATIONS.items()}
        lists_2019 = gazette(*CANDIDACIES_2019)

        known = dict(
            conn.execute(
                "select e.value, e.entity_id from external_id e join party p on p.id = e.entity_id "
                "where e.scheme = 'wikidata' and e.value = any(%s)",
                ([qid for _, qid in PARTIES.values() if qid],),
            ).fetchall()
        )
        parties: dict[str, UUID] = {}
        for key, (native, qid) in PARTIES.items():
            if qid is not None and qid in known:
                parties[key] = known[qid]
                continue
            parties[key] = cs.key(f"{SOURCE}.party", key)
            # A party merged into another pipeline's party is that pipeline's row; only the pipeline's own are written.
            if parties[key] in cs.minted:
                cs.add("party", id=parties[key], kind="party", polity_id=rioja)
                name(parties[key], native, "es")

        body = cs.key("es.body", "rioja-parliament")
        cs.add("body", id=body, polity_id=rioja, kind="unicameral")
        name(body, "Parliament of La Rioja")
        name(body, "Parlamento de La Rioja", "es", "native")
        cs.add("body_size", body_id=body, seats=35, valid_from=DAYS[1983], valid_to=DAYS[1987])
        cs.add("body_size", body_id=body, seats=33, valid_from=DAYS[1987], valid_to=None)
        system = cs.key("es.electoral_system", "rioja-parliament")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=rioja,
            rules={
                "formula": "dhondt",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD * 100},
            },
            rules_version=1,
        )
        name(system, "Parliament of La Rioja closed-list D'Hondt system")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province = cs.unit("es.provincia", "26")
        unit_rows[province] = (
            province,
            "es.provincia",
            "26",
            None,
            "equals",
            cs.area("es.provincia", "26"),
            "standard",
        )
        municipality_units: dict[str, UUID] = {}

        def unit_of(mun: str) -> UUID:
            if not mun:
                return province
            code = "26" + mun
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if mun == "200":
                    area, relation, kind = cs.area("es.provincia", "26"), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province, relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []
        people: set[UUID] = set()

        for year, day in DAYS.items():
            election = cs.key("es.election", f"rioja:{day}")
            cs.add("election", id=election, polity_id=rioja, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} La Rioja regional election")
            name(election, f"Elecciones al Parlamento de La Rioja de {year}", "es", "native")
            path = f"es/ri/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            lists = seats.filter(pl.col("year") == year).sort("votes", descending=True)
            magnitude = int(lists["seats"].sum())
            contest = cs.key(f"{SOURCE}.contest", str(day))
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=cs.area("es.provincia", "26"),
                magnitude=magnitude,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, "La Rioja")
            cs.add("slug", path=f"{path}/la-rioja", entity_id=contest)
            options: dict[str, UUID] = {}
            for entry in lists.iter_rows(named=True):
                option = options[entry["key"]] = cs.key(f"{SOURCE}.option", f"{day}:{entry['key']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=parties[entry["key"]],
                    ballot_label=entry["label"],
                )
            year_units = units.filter(pl.col("year") == year)
            year_votes = votes.filter(pl.col("year") == year)
            official = int(lists["votes"].sum())
            got = int(year_votes.filter(pl.col("mun") != "")["votes"].sum())
            total = year_units.filter(pl.col("mun") == "").row(0, named=True)
            note = RIOJA_METHOD
            won = dhondt(dict(zip(lists["key"], lists["votes"])), total["cand"] + total["blank"], magnitude)
            if any(won[key] != count for key, count in zip(lists["key"], lists["seats"])):
                note += DHONDT_DIFFERS
            link, publisher, _, printed = PROCLAMATIONS[year]
            note += MEMBERS_NOTE.format(
                publisher=publisher, printed=printed, copy=JEC_COPY if link.startswith(JEC) else ""
            )
            note += MEMBERS_EXTRA.get(year, "")
            for label, default, completeness, body_md, level in (
                ("rioja", True, 1.0, note, "community"),
                (
                    "municipio",
                    False,
                    min(got / official, 1),
                    MUNICIPIO_METHOD.format(abroad=ABROAD_ROW if year >= 2007 else "", votes=got, official=official),
                    "municipality",
                ),
            ):
                result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{label}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=completeness,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=body_md)
                keep = (pl.col("mun") == "") if level == "community" else (pl.col("mun") != "")
                for row in year_units.filter(keep).iter_rows(named=True):
                    unit, channel = unit_of(row["mun"]), ABROAD if row["mun"] == "200" else 0
                    for measure, field in (
                        (REGISTERED, "electors"),
                        (BALLOTS, "voters"),
                        (VALID, "cand"),
                        (INVALID, "invalid"),
                        (BLANK, "blank"),
                    ):
                        values.append((str(result_set), str(unit), None, measure, row[field], channel))
                for row in year_votes.filter(keep).iter_rows(named=True):
                    values.append(
                        (
                            str(result_set),
                            str(unit_of(row["mun"])),
                            str(options[row["key"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["mun"] == "200" else 0,
                        )
                    )
                if default:
                    for entry in lists.iter_rows(named=True):
                        if entry["seats"]:
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=options[entry["key"]],
                                kind="won",
                                seats=entry["seats"],
                            )
                    elected = members.filter(pl.col("year") == year)
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=proclamations[year],
                        locator={"page": min(elected["page"].to_list())},
                    )
                    for member in elected.sort("key", "position", "name").iter_rows(named=True):
                        key, printed = member["key"], member["name"]
                        person = cs.key(f"{SOURCE}.person", person_key(printed))
                        if person not in people:
                            people.add(person)
                            if person in cs.minted:
                                cs.add("person", id=person)
                                name(person, names[person_key(printed)], "es")
                        member_key = f"{day}:{key}:{person_key(printed)}"
                        option = cs.key(f"{SOURCE}.option", member_key)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            parent_option_id=options[key],
                            party_id=parties[key],
                            ballot_label=display(printed),
                        )
                        candidacy = cs.key(f"{SOURCE}.candidacy", member_key)
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person,
                            option_id=option,
                            role="list_member",
                            list_position=member["position"],
                            ballot_name=printed,
                            member_of_party_id=parties[key],
                            is_independent=False,
                        )
                        cs.add(
                            "citation",
                            entity_id=candidacy,
                            source_id=proclamations[year],
                            locator={"page": member["page"]},
                        )
                        if year == 2019:
                            cs.add("citation", entity_id=candidacy, source_id=lists_2019, locator={"list": key})
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")

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
