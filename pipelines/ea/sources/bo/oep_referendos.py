"""Bolivia's referendums and recalls, by municipality and by polling table, from the Órgano Electoral Plurinacional.

The Atlas Electoral de Bolivia, volume II (Tribunal Supremo Electoral and UNDP, 2012), prints every municipality's
results for the referendums of 2004 to 2009 in tables with a text layer. This module reads each table from the word
boxes of its PDF, joins the municipalities that the tables name differently, and checks every department's rows against
the department total the same table prints, and every national sum against the national total. The shapes of the
departments and municipalities are the GeoBolivia boundaries as redistributed by geoBoundaries (public domain).

The digital Atlas Electoral publishes one file per process with the counts of every polling table (mesa). Where a
process has one, each question also gets a second, finer result set by polling table; the municipal set stays the
default and holds the outcome.
"""

import csv
import functools
import io
import json
import re
from datetime import date
from typing import Any, NamedTuple
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.bo import atlas, bulletins
from ea.sources.bo.places import NAMES, Places, department, fold, variants

SOURCE = "bo.oep_referendos"
GEOSOURCE = "bo.geoboundaries"
BOLIVIA = str(uuid5(package.NAMESPACE, "polity\x1fBO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBOL"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
_IDS = Package(SOURCE, SOURCE, "ids")
WAYBACK = "https://web.archive.org/web/2026id_/https://web.oep.org.bo/wp-content/uploads/"
ATLAS1 = WAYBACK + "2017/03/Atlas_Electoral_tomo-II_Cap_I-II.pdf"
ATLAS2 = WAYBACK + "2017/03/Atlas_Electoral_tomo-II_Cap_III-IV-V-VI.pdf"
B2015 = WAYBACK + "2017/01/referendo_aprobatorio_2015.pdf"
B2016 = (
    "https://web.archive.org/web/20260325224312id_/https://web.oep.org.bo/wp-content/uploads/2017/01/"
    "referendo_constitucional_2016.pdf"
)
S2016 = WAYBACK + "2016/12/separata_resultados.pdf"
R2017 = WAYBACK + "2017/07/resultados_reaco_2017.pdf"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/BOL/"
ADM1 = GEOBOUNDARIES + "ADM1/geoBoundaries-BOL-ADM1_simplified.geojson"
ADM3 = GEOBOUNDARIES + "ADM3/geoBoundaries-BOL-ADM3_simplified.geojson"
LICENSE = (
    "No license stated: official publication of the Órgano Electoral Plurinacional de Bolivia; "
    "facts published with attribution"
)
ATTRIBUTION = "Órgano Electoral Plurinacional de Bolivia (Tribunal Supremo Electoral), Atlas Electoral de Bolivia"
DIGITAL = "https://atlaselectoral.oep.org.bo/"
# The digital Atlas's process number of each section that it publishes by polling table.
PROCESSES = {
    2: "2008-president",
    42: "2009-autonomy",
    41: "2009-aioc",
    34: "2009-regional",
    43: "2015-statutes",
    35: "2015-eta",
    44: "2015-eta",
    38: "2016-constitutional",
    36: "2016-eta",
    39: "2016-eta",
    45: "2016-eta",
    37: "2017-eta",
    40: "2017-eta",
}
COLUMNS = {
    "yes": "SÍ",
    "no": "NO",
    "valid": "VALIDOS",
    "blank": "BLANCOS",
    "null": "NULOS",
    "cast": "EMITIDOS",
    "registered": "INSCRITOS",
}
# Municipalities, by the code the digital Atlas gives them, whose names there differ from the printed Atlas's.
TOWNS = {
    "010602": "1:yamparaez",
    "030802": "3:toco",
    "051102": "5:caiza-d",
    "051302": "5:acasio",
    "070404": "7:colonia-japonesa-san-juan",
    "071002": "7:agustin-saavedra",
    "071501": "7:ascension-de-guarayos",
    "080401": "8:santa-ana-del-yacuma",
    "090303": "9:sena",
    "090401": "9:santa-rosa-del-abuna",
}
# Municipalities created after the printed Atlas: the digital Atlas's code, then the code, name, and outline here.
NEW_TOWNS = {
    "010704": ("1:villa-charcas", "Villa Charcas", "1:Villa Charcas"),
    "020203": ("2:chua-cocani", "Chua Cocani", "2:Chua Cocani"),
    "020206": ("2:huatajata", "Huatajata", "2:Huatajata"),
}
RULES = {"formula": "referendum"}
YES_NO = (("Sí", "Yes"), ("No", "No"))


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SYSTEM = key("electoral_system", "referendum")
GEO_SOURCE = key("source", "geoboundaries")


class Contest(NamedTuple):
    """One question: the rows of a section of the Atlas, narrowed to a department, or to a municipality."""

    id: str
    section: str
    where: str | None  # department code, municipality name, or none for the whole country
    es: str
    en: str
    question: str
    options: tuple[tuple[str, str], tuple[str, str]]
    kind: str = "referendum"


class Spec(NamedTuple):
    day: date
    slug: str
    es: str
    en: str
    kind: str
    pdf: str
    title: str
    contests: tuple[Contest, ...]
    method: str


class Section(NamedTuple):
    pdf: str
    pages: tuple[int, ...]
    filter: str = ""


SECTIONS = {
    **{f"2004-q{n}": Section(ATLAS1, tuple(range(21, 131)), f"Pregunta {n}") for n in range(1, 6)},
    "2006": Section(ATLAS1, tuple(range(135, 157))),
    "2008-president": Section(ATLAS2, tuple(range(5, 29))),
    "2008-prefect": Section(ATLAS2, tuple(range(29, 49))),
    "2009-dirimidor": Section(ATLAS2, tuple(range(52, 75))),
    "2009-constitution": Section(ATLAS2, tuple(range(75, 98))),
    "2009-autonomy": Section(ATLAS2, tuple(range(103, 114))),
    "2009-aioc": Section(ATLAS2, (119,)),
    "2009-regional": Section(ATLAS2, (129,)),
}
# The Atlas prints the twelve municipalities that voted on indigenous autonomy with their department only on the
# pages of their localities.
AIOC = {
    "Mojocoya": "1",
    "Tarabuco": "1",
    "Huacaya": "1",
    "Jesús de Machaca": "2",
    "Charazani": "2",
    "Curahuara de Carangas": "4",
    "Pampa Aullagas": "4",
    "Salinas de Garci Mendoza": "4",
    "Chipaya": "4",
    "San Pedro de Totora": "4",
    "Chayanta": "5",
    "Charagua": "7",
}
# Outlines whose names the Atlas spells differently, by department and the outline's name: the municipality's code.
CURATED = {
    "1:Villa Ricardo Mugia - Icla": "1:icla",
    "1:Villa Vaca Guzmán": "1:muyupampa",
    "1:Huacareta": "1:san-pablo-de-huacareta",
    "1:Sopachui": "1:sopachuy",
    "2:Villa Ancoraimes": "2:ancoraimes",
    "2:Puerto Mayor de Carabuco": "2:carabuco",
    "2:Corocoro": "2:coro-coro",
    "2:Puerto Mayor de Guaqui": "2:guaqui",
    "2:Jesús de Machaka": "2:jesus-de-machaca",
    "2:Villa Libertad Licoma": "2:licoma-pampa",
    "2:Sicasica": "2:sica-sica",
    "2:La (Marka) San Andrés de Mach": "2:san-andres-de-machaca",
    "3:Sipesipe": "3:sipe-sipe",
    "4:Choquecota": "4:choque-cota",
    "5:Chuquihuta Ayllu Jucumani": "5:chuquihuta",
    "5:San Pablo": "5:san-pablo-de-lipez",
    "5:San Pedro": "5:san-pedro-de-buena-vista",
    "5:Vitiche": "5:vitichi",
    "6:San Lorenzo": "6:villa-san-lorenzo",
    "6:Villa Montes": "6:villamontes",
    "4:Paria": "4:soracachi",
    "7:General Saavedra": "7:agustin-saavedra",
    "7:Ascención de Guarayos": "7:ascension-de-guarayos",
    "7:Moromoro": "7:moro-moro",
    "7:Postrervalle": "7:postrer-valle",
    "7:San Ignacio": "7:san-ignacio-de-velasco",
    "7:San José": "7:san-jose-de-chiquitos",
    "7:San Juan": "7:colonia-japonesa-san-juan",
    "7:San Miguel": "7:san-miguel-de-velasco",
    "7:Santa Rosa": "7:santa-rosa-del-sara",
    "8:Guayaramerín": "8:puerto-guayaramerin",
    "8:Puerto Menor de Rurrenabaque": "8:puerto-rurrenabaque",
    "8:Santa Ana": "8:santa-ana-del-yacuma",
    "9:Santa Rosa": "9:santa-rosa-del-abuna",
}
AIOC_QUESTION = (
    "¿Está de acuerdo con que su municipio adopte la condición de autonomía indígena originario campesina de acuerdo "
    "con los alcances y preceptos establecidos en la Constitución Política del Estado?"
)
HYDROCARBONS = (
    (
        "¿Está usted de acuerdo con la abrogación de la Ley de Hidrocarburos 1689 promulgada por Gonzalo Sánchez de "
        "Lozada?"
    ),
    (
        "¿Está usted de acuerdo con la recuperación de la propiedad de todos los hidrocarburos en boca de pozo para el "
        "Estado boliviano?"
    ),
    (
        "¿Está usted de acuerdo con refundar Yacimientos Petrolíferos Fiscales Bolivianos (YPFB) recuperando la "
        "propiedad estatal de las acciones de las bolivianas y los bolivianos en las empresas petroleras capitalizadas, "
        "de manera que pueda participar en toda la cadena productiva de hidrocarburos?"
    ),
    (
        "¿Está usted de acuerdo con la política del presidente Carlos Mesa de utilizar el gas como recurso estratégico "
        "para el logro de una salida útil y soberana al Océano Pacífico?"
    ),
    (
        "¿Está usted de acuerdo con que Bolivia exporte gas en el marco de una política nacional que cubra el consumo de "
        "gas de las bolivianas y bolivianos, fomente la industrialización del gas en territorio nacional, cobre "
        "impuestos y/o regalías a las empresas petroleras llegando al 50 por ciento del valor de la producción del gas "
        "y el petróleo en favor del país; destine los recursos de la exportación e industrialización del gas "
        "principalmente para educación, salud, caminos y empleos?"
    ),
)
AUTONOMY_2006 = (
    "¿Está usted de acuerdo, en el marco de la unidad nacional, en dar a la Asamblea Constituyente el mandato "
    "vinculante para establecer un régimen de autonomía departamental, aplicable inmediatamente después de la "
    "promulgación de la nueva Constitución Política del Estado en los departamentos donde este referéndum tenga "
    "mayoría, de manera que sus autoridades sean elegidas directamente por los ciudadanos y reciban del Estado "
    "nacional, competencias ejecutivas, atribuciones normativas, administrativas y los recursos económico "
    "financieros que les asigne la nueva Constitución Política del Estado y las leyes?"
)
AUTONOMY_2009 = (
    "¿Está usted de acuerdo que su departamento ingrese al régimen de la autonomía departamental, en el marco de las "
    "competencias y atribuciones establecidas por la Constitución Política del Estado, constituyéndose un gobierno "
    "autónomo departamental que ejerza facultades legislativas, reglamentarias, fiscalizadoras y ejecutivas?"
)
RECALL_PRESIDENT = (
    "¿Usted está de acuerdo con la continuidad del proceso de cambio liderizado por el presidente Evo Morales Ayma y "
    "el vicepresidente Álvaro García Linera?"
)
RECALL_PREFECT = (
    "¿Usted está de acuerdo con la continuidad de las políticas, las acciones y la gestión del prefecto del "
    "departamento?"
)
DIRIMIDOR = (
    "Referéndum dirimidor sobre la extensión máxima de la propiedad agraria, artículo 398 del proyecto de "
    "Constitución Política del Estado: la propuesta de minoría fija el límite en diez mil hectáreas y la de mayoría "
    "en cinco mil."
)
REGIONAL = "¿Está usted de acuerdo que su provincia ingrese al régimen de la autonomía regional?"
METHOD = (
    "Counts per municipality from the tables of the Atlas Electoral de Bolivia, volume II, which the Tribunal Supremo "
    "Electoral and the United Nations Development Programme published in 2012 from the electoral organ's official "
    "counts. Registered voters are the voters on the roll, valid votes are the votes for the options, and ballots "
    "cast are the valid, blank, and null ballots together. The same tables print each department's and the country's "
    "totals, and every department's municipalities add up to them. The Atlas names municipalities in several forms, "
    "such as the capital town, the official name in parentheses, or with the word Ciudad, and the package gives each "
    "municipality one unit under all of its names."
)

CHARTER = ("Carta Orgánica Municipal de {}", "Municipal charter of {}")
STATUTE = ("Estatuto de la autonomía indígena originario campesina de {}", "Indigenous autonomy statute of {}")
CONVERSION = (
    "Conversión del municipio de {} en autonomía indígena originario campesina",
    "Conversion of {} to indigenous autonomy",
)
# Autonomous territorial entities by the name the bulletin prints: department, kind of vote, scheme of the entity.
ETA_2016 = {
    "sucre": ("1", CHARTER),
    "mojocoya": ("1", STATUTE),
    "viacha": ("2", CHARTER),
    "arque": ("3", CHARTER),
    "vinto": ("3", CHARTER),
    "totora": ("3", CHARTER),
    "raqaypampa": ("3", STATUTE),
    "chipaya": ("4", STATUTE),
    "gran chaco": ("6", ("Estatuto autonómico regional del {}", "Regional autonomy statute of the {}")),
    "el torno": ("7", CHARTER),
    "buena vista": ("7", CHARTER),
    "yapacani": ("7", CHARTER),
    "gutierrez": ("7", CONVERSION),
    "cuatro canadas": ("7", CHARTER),
    "el puente": ("7", CHARTER),
}
ETA_2017 = {
    "huacaya": (
        "1",
        ("Estatuto autonómico indígena guaraní chaqueño de {}", "Guaraní indigenous autonomy statute of {}"),
    ),
    "machareti": ("1", CONVERSION),
    "achocalla": ("2", CHARTER),
    "alto beni": ("2", CHARTER),
    "coroico": ("2", CHARTER),
    "laja": ("2", CHARTER),
    "shinahota": ("3", CHARTER),
    "sicaya": ("3", CHARTER),
    "puerto villarroel": ("3", CHARTER),
    "antequera": ("4", CHARTER),
    "puna": ("5", CHARTER),
    "uriondo": ("6", CHARTER),
    "postrervalle": ("7", CHARTER),
    "vallegrande": ("7", CHARTER),
}
ETA_2015 = {
    "cocapata": ("3", CHARTER),
    "tacopaya": ("3", CHARTER),
    "huanuni": ("4", CHARTER),
    "totora marka": ("4", ("Estatuto autonómico originario de {}", "Indigenous autonomy statute of {}")),
    "charagua": ("7", ("Estatuto autonómico originario de {}", "Indigenous autonomy statute of {}")),
}
DISPLAY = {
    "yapacani": "Yapacaní",
    "gutierrez": "Gutiérrez",
    "cuatro canadas": "Cuatro Cañadas",
    "machareti": "Macharetí",
}


def eta_name(key: str) -> str:
    return DISPLAY.get(key, key.title())


# Entities that are not a municipality: the region of the Gran Chaco and the indigenous territory of Raqaypampa.
ETA_AREAS = {"gran chaco": ("6", "Gran Chaco"), "raqaypampa": ("3", "Raqaypampa")}
# The text layer scrambles the Uruguay row of the table of votes abroad with the page footer; its counts are the
# ones the line still prints (28 Sí and 53 No, which add up to the 81 valid votes, with 129 registered voters).
URUGUAY = {"yes": 28, "no": 53, "valid": 81, "blank": 0, "null": 0, "cast": 81, "registered": 129}
BULLETINS = {
    "2015-statutes": B2015,
    "2015-eta": B2015,
    "2016-constitutional": B2016,
    "2016-eta": S2016,
    "2017-eta": R2017,
}


BULLETIN_METHOD = (
    "Counts from the results bulletin of the Servicio Intercultural de Fortalecimiento Democrático of the Tribunal "
    "Supremo Electoral, at the finest level that the bulletin prints as text: the department, the municipality, or the "
    "country of a vote cast abroad. The bulletin's tables add up to the totals it prints. The municipal tables of "
    "the bulletins and the lists of polling places are not read here."
)
ETA_METHOD = (
    "Counts of each autonomous territorial entity as the Órgano Electoral Plurinacional's official results report "
    "prints them. Registered voters are the people entitled to vote, valid votes are the votes for Sí and No, and "
    "ballots cast are the valid, blank, and null ballots together. The report's total of all entities is the sum "
    "of these rows."
)


def contests_2004() -> tuple[Contest, ...]:
    return tuple(
        Contest(f"q{n}", f"2004-q{n}", None, f"Pregunta {n}", f"Question {n}", HYDROCARBONS[n - 1], YES_NO)
        for n in range(1, 6)
    )


def contests_2006() -> tuple[Contest, ...]:
    return tuple(
        Contest(
            f"{code}",
            "2006",
            code,
            f"Autonomía departamental en {name}",
            f"Departmental autonomy in {name}",
            AUTONOMY_2006,
            YES_NO,
        )
        for code, name in NAMES.items()
    )


def contests_2008() -> tuple[Contest, ...]:
    options = (("Sí, continuidad", "Yes, continue"), ("No, revocatoria", "No, recall"))
    return (
        Contest(
            "president",
            "2008-president",
            None,
            "Revocatoria de mandato del presidente y del vicepresidente",
            "Recall of the President and Vice President",
            RECALL_PRESIDENT,
            options,
            "recall_question",
        ),
        *(
            Contest(
                f"prefect-{code}",
                "2008-prefect",
                code,
                f"Revocatoria de mandato del prefecto de {name}",
                f"Recall of the prefect of {name}",
                RECALL_PREFECT,
                options,
                "recall_question",
            )
            for code, name in NAMES.items()
            if code != "1"
        ),
    )


def contests_2009a() -> tuple[Contest, ...]:
    return (
        Contest(
            "constitution",
            "2009-constitution",
            None,
            "Referéndum constituyente",
            "Constitutional referendum",
            "Referéndum constituyente para la aprobación del proyecto de nueva Constitución Política del Estado.",
            YES_NO,
        ),
        Contest(
            "land",
            "2009-dirimidor",
            None,
            "Referéndum dirimidor",
            "Tie-breaking referendum on the largest landholding",
            DIRIMIDOR,
            (("Diez mil hectáreas", "Ten thousand hectares"), ("Cinco mil hectáreas", "Five thousand hectares")),
        ),
    )


def contests_2009b() -> tuple[Contest, ...]:
    return (
        *(
            Contest(
                f"departmental-{code}",
                "2009-autonomy",
                code,
                f"Autonomía departamental en {NAMES[code]}",
                f"Departmental autonomy in {NAMES[code]}",
                AUTONOMY_2009,
                YES_NO,
            )
            for code in ("1", "2", "3", "4", "5")
        ),
        *(
            Contest(
                f"indigenous-{name}",
                "2009-aioc",
                name,
                f"Autonomía indígena originario campesina de {name}",
                f"Indigenous autonomy of {name}",
                AIOC_QUESTION,
                YES_NO,
            )
            for name in AIOC
        ),
        Contest(
            "regional-gran-chaco",
            "2009-regional",
            None,
            "Autonomía regional del Gran Chaco",
            "Regional autonomy of the Gran Chaco",
            REGIONAL,
            YES_NO,
        ),
    )


def contests_2015() -> tuple[Contest, ...]:
    return (
        *(
            Contest(
                f"departmental-{code}",
                "2015-statutes",
                code,
                f"Estatuto autonómico departamental de {NAMES[code]}",
                f"Departmental autonomy statute of {NAMES[code]}",
                "",
                YES_NO,
            )
            for code in ("1", "2", "3", "4", "5")
        ),
        *eta_contests("2015-eta", ETA_2015),
    )


def eta_contests(section: str, table: dict[str, tuple[str, tuple[str, str]]]) -> tuple[Contest, ...]:
    out = []
    for key, (_, (es, en)) in table.items():
        name = ETA_AREAS[key][1] if key in ETA_AREAS else eta_name(key)
        out.append(Contest(re.sub(r"\W+", "-", key), section, name, es.format(name), en.format(name), "", YES_NO))
    return tuple(out)


def contests_2016() -> tuple[Contest, ...]:
    return (
        Contest(
            "reform",
            "2016-constitutional",
            None,
            "Reforma del artículo 168 de la Constitución Política del Estado",
            "Reform of Article 168 of the Constitution",
            (
                "Consulta sobre la modificación parcial del artículo 168 de la Constitución Política del Estado, "
                "referido a la reelección de la Presidencia y la Vicepresidencia por dos veces consecutivas."
            ),
            YES_NO,
        ),
    )


ELECTIONS: dict[date, Spec] = {
    date(2004, 7, 18): Spec(
        date(2004, 7, 18),
        "hydrocarbons",
        "Referéndum sobre la política de hidrocarburos de 2004",
        "2004 Bolivian hydrocarbons referendum",
        "referendum",
        ATLAS1,
        "Atlas Electoral de Bolivia, tomo II, capítulos I y II (referéndum sobre hidrocarburos, 2004)",
        contests_2004(),
        METHOD,
    ),
    date(2006, 7, 2): Spec(
        date(2006, 7, 2),
        "departmental-autonomy",
        "Referéndum sobre autonomías departamentales de 2006",
        "2006 Bolivian departmental autonomy referendum",
        "referendum",
        ATLAS1,
        "Atlas Electoral de Bolivia, tomo II, capítulos I y II (referéndum sobre autonomías departamentales, 2006)",
        contests_2006(),
        METHOD,
    ),
    date(2008, 8, 10): Spec(
        date(2008, 8, 10),
        "recall",
        "Referéndum revocatorio de mandato popular de 2008",
        "2008 Bolivian recall referendums",
        "recall",
        ATLAS2,
        "Atlas Electoral de Bolivia, tomo II, capítulos III a VI (referéndum revocatorio, 2008)",
        contests_2008(),
        METHOD,
    ),
    date(2009, 1, 25): Spec(
        date(2009, 1, 25),
        "constitution",
        "Referéndum constituyente y dirimidor de 2009",
        "2009 Bolivian constitutional and tie-breaking referendums",
        "referendum",
        ATLAS2,
        "Atlas Electoral de Bolivia, tomo II, capítulos III a VI (referéndum constituyente y dirimidor, 2009)",
        contests_2009a(),
        METHOD,
    ),
    date(2009, 12, 6): Spec(
        date(2009, 12, 6),
        "autonomy",
        "Referéndums de autonomías de 2009",
        "2009 Bolivian autonomy referendums",
        "referendum",
        ATLAS2,
        "Atlas Electoral de Bolivia, tomo II, capítulos III a VI (referéndums de autonomías, 2009)",
        contests_2009b(),
        METHOD,
    ),
    date(2015, 9, 20): Spec(
        date(2015, 9, 20),
        "statutes-and-charters",
        "Referendo de estatutos autonómicos, cartas orgánicas y conversión a autonomía indígena de 2015",
        "2015 Bolivian referendums on autonomy statutes, municipal charters, and indigenous autonomy",
        "referendum",
        B2015,
        "Boletín estadístico, referendo de aprobación de estatutos autonómicos, cartas orgánicas y AIOC, 2015",
        contests_2015(),
        BULLETIN_METHOD,
    ),
    date(2016, 2, 21): Spec(
        date(2016, 2, 21),
        "constitutional",
        "Referendo constitucional de 2016",
        "2016 Bolivian constitutional referendum",
        "referendum",
        B2016,
        "Boletín estadístico, referendo constitucional 2016",
        contests_2016(),
        BULLETIN_METHOD,
    ),
    date(2016, 11, 20): Spec(
        date(2016, 11, 20),
        "statutes-and-charters",
        "Referendo de estatutos autonómicos y cartas orgánicas de 2016",
        "2016 Bolivian referendums on autonomy statutes and municipal charters",
        "referendum",
        S2016,
        "Resultados oficiales, referendo de estatutos autonómicos y cartas orgánicas, 20 de noviembre de 2016",
        eta_contests("2016-eta", ETA_2016),
        ETA_METHOD,
    ),
    date(2017, 7, 9): Spec(
        date(2017, 7, 9),
        "statutes-and-charters",
        "Referendo de estatutos autonómicos y cartas orgánicas de 2017",
        "2017 Bolivian referendums on autonomy statutes and municipal charters",
        "referendum",
        R2017,
        "Resultados oficiales, referendo de estatutos autonómicos y cartas orgánicas, 9 de julio de 2017",
        eta_contests("2017-eta", ETA_2017),
        ETA_METHOD,
    ),
}
FILES = dict.fromkeys((ATLAS1, ATLAS2, B2015, B2016, S2016, R2017), ".pdf") | {
    f"{DIGITAL}descarga/{n}/votos_totales.csv": ".csv" for n in PROCESSES
}
MESA_METHOD = (
    "Counts of each polling table (mesa) from the digital Atlas Electoral's votos_totales file of the process. "
    "Registered voters are the table's roll where the file gives it, ballots cast are the ballots issued, and valid "
    "votes are the votes for the options; blank and null ballots are counted apart. The municipal result set from the "
    "printed tables stays the default; this set's completeness is its valid votes over that set's valid votes."
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in ELECTIONS)]


def fetch() -> None:
    for url, suffix in FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)
    for url in (ADM1, ADM3):
        raw.fetch(GEOSOURCE, url, suffix=".geojson")


# Reading the Atlas.


DISCREPANCIES: list[str] = []


class Part(NamedTuple):
    dept: str
    name: str
    counts: dict[str, int]
    page: int
    scheme: str = "bo.municipio"


class Result(NamedTuple):
    parts: list[Part]
    totals: dict[str, list[dict[str, int]]]  # "country" or a department code -> the totals the tables print
    pages: list[int]


@functools.cache
def pages(pdf: str) -> list[atlas.Row]:
    out: list[atlas.Row] = []
    for number, words in enumerate(atlas.pages(raw.cached(SOURCE, pdf).path), 1):
        out += atlas.table(number, words)
    return out


def add(total: dict[str, int], more: dict[str, int]) -> dict[str, int]:
    return {name: total.get(name, 0) + more[name] for name in more}


@functools.cache
def section(name: str) -> Result:
    if name in BULLETINS:
        return bulletin(name)
    spec = SECTIONS[name]
    rows = [r for r in pages(spec.pdf) if r.page in spec.pages and (not spec.filter or spec.filter in r.heading)]
    parts: list[Part] = []
    totals: dict[str, list[dict[str, int]]] = {}
    pending: list[atlas.Row] = []
    for r in rows:
        label = " ".join(t for t in r.names if t)
        if r.level in {"total", "department"}:
            where = "country" if label in {"Bolivia", "TOTAL"} else department(label)
            if where is None:
                raise ValueError(f"{name}: page {r.page} total of {label!r}")
            if where != "country" and r.level == "total":
                parts += [Part(where, p.names[-1], p.counts, p.page) for p in pending]
                pending = []
            if r.counts not in totals.setdefault(where, []):
                totals[where].append(r.counts)
            continue
        dept = department(r.department) if r.department else None
        if name == "2009-aioc":
            dept = AIOC[r.names[-1]]
        if name == "2009-regional":
            dept = "6"
        if dept is None:
            pending.append(r)
        else:
            parts.append(Part(dept, r.names[-1], r.counts, r.page))
    if pending:
        raise ValueError(f"{name}: {len(pending)} rows with no department, from page {pending[0].page}")
    return Result(parts, totals, sorted({r.page for r in rows}))


def check(name: str) -> None:
    """Every row adds up, every department adds up to its printed total, and so does the country."""
    result = section(name)
    sums: dict[str, dict[str, int]] = {}
    for part in result.parts:
        c = part.counts
        if c["yes"] + c["no"] != c["valid"]:
            raise ValueError(f"{name}: {part.name} on page {part.page} does not add up: {c}")
        sums[part.dept] = add(sums.get(part.dept, {}), c)
    country: dict[str, int] = {}
    for dept, total in sums.items():
        country = add(country, total)
        if dept in result.totals and total not in result.totals[dept]:
            raise ValueError(f"{name}: department {dept} adds up to {total}, not the printed {result.totals[dept]}")
        for printed in result.totals.get(dept, []):
            if printed != total:
                DISCREPANCIES.append(f"{name}: a table prints {printed} for department {dept}, not the sum {total}")
    for printed in result.totals.get("country", []):
        if printed != country:
            raise ValueError(f"{name}: municipalities add up to {country}, not the printed {printed}")


# Reading the bulletins.


def named(label: str) -> str:
    return fold(label.replace("Í", "I"))


def eta_part(text_name: str, table: dict[str, tuple[str, tuple[str, str]]], counts: dict[str, int]) -> Part:
    key = fold(text_name)
    dept, _ = table[key]
    if key in ETA_AREAS:
        return Part(dept, ETA_AREAS[key][1], counts, 0, "bo.eta")
    return Part(dept, eta_name(key), counts, 0)


@functools.cache
def bulletin(name: str) -> Result:
    text = bulletins.lines(raw.cached(SOURCE, BULLETINS[name]).path)
    parts: list[Part] = []
    totals: dict[str, list[dict[str, int]]] = {}
    if name == "2015-statutes":
        table = bulletins.rows(text, bulletins.after(text, r"SEGÚN DEPARTAMENTO\. 2015"))
        totals["country"] = [table.pop("Total")]
        for label, counts in table.items():
            code = department(label)
            if code is None:
                raise ValueError(f"{name}: {label}")
            parts.append(Part(code, label, counts, 0, "bo.departamento"))
    elif name == "2015-eta":
        for key, (dept, _) in ETA_2015.items():
            parts.append(Part(dept, eta_name(key), bulletins.locality_total(text, key), 0))
    elif name == "2016-constitutional":
        table = bulletins.rows(text, bulletins.after(text, r"SEGÚN RESIDENCIA\. REFERENDO"))
        totals["country"] = [table["Total"]]
        inside = bulletins.rows(text, bulletins.after(text, r"SEGÚN DEPARTAMENTO\. 2016"))
        inside.pop("Total")
        for label, counts in inside.items():
            code = department(label)
            if code is None:
                raise ValueError(f"{name}: {label}")
            parts.append(Part(code, label, counts, 0, "bo.departamento"))
        abroad = bulletins.rows(text, bulletins.after(text, r"SEGÚN PAÍS EN LA CONSULTA"), r"Gráfico N")
        abroad.pop("Total")
        abroad["Uruguay"] = URUGUAY
        parts += [Part("abroad", label, counts, 0, "bo.exterior") for label, counts in abroad.items()]
        if summed(parts[9:]) != table["Extranjero"]:
            raise ValueError(f"{name}: the countries add up to {summed(parts[9:])}, not {table['Extranjero']}")
        if summed(parts[:9]) != table["Bolivia"]:
            raise ValueError(f"{name}: the departments add up to {summed(parts[:9])}, not {table['Bolivia']}")
    else:
        table = ETA_2016 if name == "2016-eta" else ETA_2017
        found = bulletins.eta_rows(text)
        for label, counts in found:
            if label == "Total":
                totals["country"] = [counts]
            else:
                parts.append(eta_part("Puerto Villarroel" if label == "PUERTO" else label, table, counts))
        if name == "2016-eta":
            totals["country"] = [bulletins.eta_total(text)]
        if len(parts) != len(table):
            raise ValueError(f"{name}: {len(parts)} entities, not {len(table)}")
    return Result(parts, totals, [])


@functools.cache
def places() -> Places:
    found = Places()
    for name in (*SECTIONS, *BULLETINS):
        for part in section(name).parts:
            if part.scheme == "bo.municipio":
                found.add(part.dept, part.name, name)
    found.settle()
    return found


# Reading the digital Atlas's polling tables.


class Mesa(NamedTuple):
    process: int
    code: str
    dept: str
    town: str  # the digital Atlas's municipality code
    name: str  # the municipality as the file names it
    counts: dict[str, int]


def process_url(n: int) -> str:
    return f"{DIGITAL}descarga/{n}/votos_totales.csv"


@functools.cache
def mesas(n: int) -> list[Mesa]:
    text = raw.cached(SOURCE, process_url(n)).path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter="|"))
    header = rows[0]
    out = []
    for row in rows[1:]:
        if not row:
            continue
        if len(row) == len(header) - 4 and row[3] == "RAQAYPAMPA":
            row = row[:2] + ["03", "COCHABAMBA", "", ""] + row[2:]  # the territory has no department columns
        if len(row) != len(header):
            raise ValueError(f"process {n}: {len(row)} columns in {row[:12]}")
        cells = dict(zip(header, row, strict=True))
        counts = {name: int(cells[column]) for name, column in COLUMNS.items() if column in cells}
        if counts["yes"] + counts["no"] != counts["valid"]:
            raise ValueError(f"process {n}: mesa {cells['Codigo MESA']} does not add up: {counts}")
        town = cells["Codigo MUNICIPIO"]
        out.append(
            Mesa(
                n, cells["Codigo MESA"], str(int(cells["Codigo DEPARTAMENTO"])), town, cells["Nombre MUNICIPIO"], counts
            )
        )
    return out


def mesa_area(mesa: Mesa, found: Places) -> str:
    if mesa.name == "RAQAYPAMPA":
        return area("bo.eta", "Raqaypampa")
    if mesa.town in NEW_TOWNS:
        return area("bo.municipio", NEW_TOWNS[mesa.town][0])
    if mesa.town in TOWNS:
        return area("bo.municipio", TOWNS[mesa.town])
    assert found.settled is not None
    codes = {found.settled[(mesa.dept, name)] for name in variants(mesa.name) if (mesa.dept, name) in found.settled}
    if len(codes) != 1:
        raise ValueError(f"process {mesa.process}: {mesa.name} in department {mesa.dept} is {codes or 'unknown'}")
    return area("bo.municipio", codes.pop())


def district(contest: Contest, found: Places) -> str:
    """The area a question was put to."""
    if contest.where is None:
        return COUNTRY
    if contest.where in NAMES:
        return area("bo.departamento", contest.where)
    first = select(contest)[0][0]
    if first.scheme == "bo.eta":
        return area("bo.eta", first.name)
    return area("bo.municipio", found.code(first.dept, first.name))


def contest_mesas(contest: Contest, found: Places) -> list[Mesa]:
    """The polling tables of a question, from the files of its section."""
    rows = [mesa for n, name in PROCESSES.items() if name == contest.section for mesa in mesas(n)]
    if contest.where is None:
        return rows
    if contest.where in NAMES:
        return [mesa for mesa in rows if mesa.dept == contest.where]
    target = district(contest, found)
    return [mesa for mesa in rows if mesa_area(mesa, found) == target]


def check_mesas() -> None:
    """Every polling table of every file belongs to exactly one question."""
    found = places()
    used: dict[tuple[int, str], int] = {}
    for spec in ELECTIONS.values():
        for contest in spec.contests:
            for mesa in contest_mesas(contest, found):
                used[(mesa.process, mesa.code)] = used.get((mesa.process, mesa.code), 0) + 1
    for n in PROCESSES:
        for mesa in mesas(n):
            if used.get((n, mesa.code)) != 1:
                raise ValueError(
                    f"process {n}: mesa {mesa.code} of {mesa.name} is in {used.get((n, mesa.code))} questions"
                )


# Geography.


def unit(code: str) -> str:
    return key("unit", "bo.municipio", code)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit_of(part: Part, found: Places) -> str:
    if part.scheme == "bo.municipio":
        return unit(found.code(part.dept, part.name))
    if part.scheme == "bo.departamento":
        return key("unit", part.scheme, part.dept)
    return key("unit", part.scheme, part.name)


def abroad() -> list[str]:
    return [part.name for part in section("2016-constitutional").parts if part.scheme == "bo.exterior"]


def shape(geometry: dict[str, Any]) -> shapely.MultiPolygon:
    polygon = shapely.make_valid(shapely.geometry.shape(geometry))
    parts = [part for part in shapely.get_parts(polygon) if part.geom_type in {"Polygon", "MultiPolygon"}]
    flat = [g for part in parts for g in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(flat), 4326)


def outline(p: Package, id: str, geometry: shapely.MultiPolygon) -> None:
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=date(2015, 1, 1),
        geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=GEO_SOURCE,
    )


def boundaries(url: str) -> list[dict[str, Any]]:
    features = json.loads(raw.cached(GEOSOURCE, url).path.read_text(encoding="utf-8"))["features"]
    return [{"name": f["properties"]["shapeName"], "shape": shape(f["geometry"])} for f in features]


class Geo(NamedTuple):
    shapes: dict[str, shapely.MultiPolygon]  # department code -> outline
    towns: dict[str, shapely.MultiPolygon]  # municipality code -> outline
    unmatched: list[str]  # municipality codes without an outline
    spare: list[str]  # outlines that no municipality takes


@functools.cache
def geometry() -> Geo:
    depts: dict[str, shapely.MultiPolygon] = {}
    for b in boundaries(ADM1):
        code = department(b["name"])
        if code is None:
            raise ValueError(f"unknown department {b['name']}")
        depts[code] = b["shape"]
    found = places()
    wanted: dict[str, set[str]] = {}
    assert found.settled is not None
    for (dept, name), code in found.settled.items():
        wanted.setdefault(code, set()).add(name)
    by_variant: dict[tuple[str, str], list[str]] = {}
    for code, names in wanted.items():
        dept = code.split(":")[0]
        for name in names:
            by_variant.setdefault((dept, name), []).append(code)
    towns: dict[str, list[shapely.MultiPolygon]] = {}
    spare: list[str] = []
    for b in boundaries(ADM3):
        middle = shapely.point_on_surface(b["shape"])
        dept = next((code for code, outline_ in depts.items() if outline_.contains(middle)), None)
        if dept is None:
            spare.append(b["name"])
            continue
        hits = {code for variant in variants(b["name"]) for code in by_variant.get((dept, variant), [])}
        if not hits and f"{dept}:{b['name']}" in CURATED:
            hits = {CURATED[f"{dept}:{b['name']}"]}
        if not hits:
            hits = {code for code, _, label in NEW_TOWNS.values() if label == f"{dept}:{b['name']}"}
        if len(hits) == 1:
            towns.setdefault(hits.pop(), []).append(b["shape"])
        else:
            spare.append(f"{dept}:{b['name']}")
    one = {code: parts[0] for code, parts in towns.items() if len(parts) == 1}
    spare += [f"{code}:{len(parts)} outlines" for code, parts in towns.items() if len(parts) > 1]
    return Geo(depts, one, sorted(set(wanted) - set(one)), sorted(spare))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bolivia's nine departments and its municipalities as the Atlas Electoral de Bolivia names them, with the "
        "GeoBolivia outlines of 2015 where a municipality's name matches one",
    )
    p.external |= {BOLIVIA, COUNTRY}
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (GeoBolivia boundaries)",
        title="geoBoundaries gbOpen Bolivia ADM1 and ADM3, 2015",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0 (geoBoundaries); the GeoBolivia layer is public domain",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), derived from GeoBolivia",
    )
    for url in (ADM1, ADM3):
        p.artifact(raw.cached(GEOSOURCE, url), GEO_SOURCE)
    p.add("scheme", code="bo.departamento", polity_id=BOLIVIA, kind="administrative", rank=1, noun="department")
    p.add("scheme", code="bo.municipio", polity_id=BOLIVIA, kind="administrative", rank=2, noun="municipality")
    p.add(
        "scheme", code="bo.eta", polity_id=BOLIVIA, kind="administrative", rank=2, noun="autonomous territorial entity"
    )
    p.add("scheme", code="bo.exterior", polity_id=BOLIVIA, kind="precinct", rank=3, noun="country of the vote abroad")
    p.add("scheme", code="bo.mesa", polity_id=BOLIVIA, kind="precinct", rank=3, noun="polling table")
    for scheme in ("bo.departamento", "bo.municipio"):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "2015"),
            scheme_code=scheme,
            vintage="2015",
            valid_from=date(2015, 1, 1),
            as_of=date(2015, 1, 1),
        )
    shapes = geometry()
    for code, name in NAMES.items():
        id = area("bo.departamento", code)
        p.add("area", id=id, scheme_code="bo.departamento")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "es", "official")
        outline(p, id, shapes.shapes[code])
        p.add(
            "reporting_unit",
            id=key("unit", "bo.departamento", code),
            polity_id=BOLIVIA,
            scheme_code="bo.departamento",
            code=code,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
    for dept, name in ETA_AREAS.values():
        id = area("bo.eta", name)
        p.add("area", id=id, scheme_code="bo.eta")
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=area("bo.departamento", dept))
        p.name(id, name, "es", "official")
        p.add(
            "reporting_unit",
            id=key("unit", "bo.eta", name),
            polity_id=BOLIVIA,
            scheme_code="bo.eta",
            code=name,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
    for country in abroad():
        p.add(
            "reporting_unit",
            id=key("unit", "bo.exterior", country),
            polity_id=BOLIVIA,
            scheme_code="bo.exterior",
            code=country,
            area_relation="unlocated",
            kind="abroad_mission",
        )
    found = places()
    for code, (name, others) in sorted(found.names().items()):
        id = area("bo.municipio", code)
        dept = code.split(":")[0]
        p.add("area", id=id, scheme_code="bo.municipio")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("bo.departamento", dept))
        p.name(id, name, "es", "official")
        for other in others:
            p.name(id, other, "es", "historical", False)
        if code in shapes.towns:
            outline(p, id, shapes.towns[code])
        p.add(
            "reporting_unit",
            id=unit(code),
            polity_id=BOLIVIA,
            scheme_code="bo.municipio",
            code=code,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
    for code, name, _ in NEW_TOWNS.values():
        id = area("bo.municipio", code)
        p.add("area", id=id, scheme_code="bo.municipio")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("bo.departamento", code.split(":")[0]))
        p.name(id, name, "es", "official")
        outline(p, id, shapes.towns[code])
    return p


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The electoral system of Bolivia's referendums and recalls")
    p.external |= {BOLIVIA}
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=BOLIVIA,
        valid_from=date(2004, 7, 6),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "The option with more valid votes carries a question. The law of the 2008 recall set thresholds for the "
            "President and for each prefect, which this record does not model."
        ),
        lang="en",
    )
    return p


# The elections.


def build(day: date) -> Package:
    spec = ELECTIONS[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{spec.en} by municipality, from the Atlas Electoral de Bolivia",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BOLIVIA, COUNTRY}
    source = p.id("source", spec.pdf)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de Bolivia",
        title=spec.title,
        url=spec.pdf,
        license=LICENSE,
        attribution=ATTRIBUTION,
    )
    p.artifact(raw.cached(SOURCE, spec.pdf), source)
    election = p.id("election", day)
    p.add(
        "election",
        id=election,
        polity_id=BOLIVIA,
        kind=spec.kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election, spec.en, "en", "official")
    p.name(election, spec.es, "es", "native", False)
    base = f"bo/{day}-{spec.slug}"
    p.add("slug", path=base, entity_id=election, is_current=True)
    sections = {contest.section for contest in spec.contests}
    files = [n for n, name in PROCESSES.items() if name in sections]
    digital = p.id("source", DIGITAL)
    if files:
        p.add(
            "source",
            id=digital,
            kind="official_body",
            publisher="Órgano Electoral Plurinacional de Bolivia",
            title="Atlas Electoral de Bolivia (digital), counts by polling table",
            url=DIGITAL,
            license=LICENSE,
            attribution=ATTRIBUTION,
        )
        for n in files:
            p.artifact(raw.cached(SOURCE, process_url(n)), digital)
    found = places()
    over = []
    short = []
    tables: list[str] = []
    units: set[str] = set()
    for contest in spec.contests:
        cid = add_contest(p, election, base, source, contest, found, spec.method)
        tables += add_mesas(p, day, election, cid, digital, contest, found, units)
        short += [
            f"{part.name}: valid {part.counts['valid']:,} + blank {part.counts['blank']:,} + null {part.counts['null']:,} "
            f"is {part.counts['valid'] + part.counts['blank'] + part.counts['null']:,}, not the {part.counts['cast']:,} printed"
            for part in select(contest)[0]
            if part.counts["valid"] + part.counts["blank"] + part.counts["null"] != part.counts["cast"]
        ]
        over += [
            f"{part.name} (page {part.page}): {part.counts['cast']:,} ballots cast, {part.counts['registered']:,} registered"
            for part in select(contest)[0]
            if part.counts["cast"] > part.counts["registered"]
        ]
    if short:
        p.accept(
            "accounting.ballots_sum",
            "The bulletin prints ballots cast that differ from the valid, blank, and null ballots it prints for "
            + "; ".join(sorted(set(short)))
            + ". The figures are as the bulletin prints them.",
        )
    reasons = []
    if over:
        reasons.append(
            "The Atlas prints more ballots cast than registered voters for "
            + "; ".join(sorted(set(over)))
            + ". The department's printed total is the sum of these printed rows, so the figures are the source's own."
        )
    if tables:
        reasons.append(
            f"The digital Atlas's file gives more ballots cast than registered voters at {len(tables)} polling tables ("
            + ", ".join(sorted(tables))
            + "); the file's figures are stored as published."
        )
    if reasons:
        p.accept("accounting.turnout", " ".join(reasons))
    return p


def summed(parts: list[Part]) -> dict[str, int]:
    total: dict[str, int] = {}
    for part in parts:
        total = add(total, part.counts)
    return total


def exact(printed: list[dict[str, int]], parts: list[Part]) -> dict[str, int] | None:
    """The printed total that the municipalities add up to."""
    return next((t for t in printed if t == summed(parts)), None)


def select(contest: Contest) -> tuple[list[Part], dict[str, int], bool]:
    """The parts of a contest, its total, and whether the source prints that total."""
    result = section(contest.section)
    if contest.where is None:
        parts = result.parts
        total = exact(result.totals.get("country", []), parts)
    elif contest.where in NAMES:
        parts = [part for part in result.parts if part.dept == contest.where]
        total = exact(result.totals.get(contest.where, []), parts)
    else:
        parts = [part for part in result.parts if part.name == contest.where]
        total = None
    return parts, total or summed(parts), total is not None


def add_contest(p: Package, election: str, base: str, source: str, contest: Contest, found: Places, method: str) -> str:
    parts, total, printed = select(contest)
    cid = p.id("contest", election, contest.id)
    p.add(
        "contest",
        id=cid,
        election_id=election,
        kind="ballot_question",
        district_area_id=district(contest, found),
        electoral_system_id=SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=cid, type=contest.kind)
    p.name(cid, contest.es, "es", "official")
    p.name(cid, contest.en, "en", "english", False)
    p.add("slug", path=f"{base}/{slug(contest.id)}", entity_id=cid, is_current=True)
    if contest.question:
        p.add(
            "note",
            id=p.id("note", cid, "question"),
            entity_id=cid,
            kind="context",
            body_md=contest.question,
            lang="es",
        )
    options = []
    for position, (es, en) in enumerate(contest.options, 1):
        option = p.id("option", cid, position)
        options.append(option)
        p.add("contest_option", id=option, contest_id=cid, kind="answer", ballot_position=position, ballot_label=es)
        p.name(option, es, "es", "ballot")
        p.name(option, en, "en", "english", False)
    result_set = p.id("result_set", cid)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    pages = sorted({part.page for part in parts if part.page})
    p.add(
        "citation",
        id=p.id("citation", result_set),
        entity_id=result_set,
        source_id=source,
        locator={"pages": pages, "table": "Resultados por municipio"} if pages else {"table": contest.section},
    )
    winner = 0 if total["yes"] > total["no"] else 1
    p.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won")
    rows: list[tuple[str, str | None, int, int]] = []
    seen: set[str] = set()
    for part in parts:
        id = unit_of(part, found)
        if id in seen:
            raise ValueError(f"{contest.id}: {part.name} appears twice")
        seen.add(id)
        c = part.counts
        rows += [
            (id, options[0], VOTES, c["yes"]),
            (id, options[1], VOTES, c["no"]),
            (id, None, REGISTERED, c["registered"]),
            (id, None, BALLOTS, c["cast"]),
            (id, None, VALID, c["valid"]),
            (id, None, INVALID, c["null"]),
            (id, None, BLANK, c["blank"]),
        ]
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    if printed:
        for option, name in zip(options, ("yes", "no"), strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=total[name])
        for measure, name in (
            (REGISTERED, "registered"),
            (BALLOTS, "cast"),
            (VALID, "valid"),
            (INVALID, "null"),
            (BLANK, "blank"),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total[name])
    return cid


def add_mesas(
    p: Package, day: date, election: str, cid: str, source: str, contest: Contest, found: Places, units: set[str]
) -> list[str]:
    """The finer result set of a question by polling table; returns the tables with more ballots than voters."""
    rows = contest_mesas(contest, found)
    if not rows:
        return []
    options = [p.id("option", cid, position) for position in (1, 2)]
    official = select(contest)[1]
    valid = sum(mesa.counts["valid"] for mesa in rows)
    result_set = p.id("result_set", cid, "mesa")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=False,
        completeness=round(valid / official["valid"], 6),
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=MESA_METHOD, lang="en")
    p.add(
        "citation",
        id=p.id("citation", result_set),
        entity_id=result_set,
        source_id=source,
        locator={"files": sorted({process_url(mesa.process) for mesa in rows})},
    )
    values: list[tuple[str, str | None, int, int]] = []
    over = []
    for mesa in rows:
        unit_id = key("unit", "bo.mesa", f"{day}:{mesa.code}")
        if unit_id not in units:
            units.add(unit_id)
            p.add(
                "reporting_unit",
                id=unit_id,
                polity_id=BOLIVIA,
                scheme_code="bo.mesa",
                code=f"{day}:{mesa.code}",
                area_relation="within",
                area_id=mesa_area(mesa, found),
                kind="standard",
            )
        c = mesa.counts
        values += [(unit_id, options[0], VOTES, c["yes"]), (unit_id, options[1], VOTES, c["no"])]
        values += [
            (unit_id, None, measure, c[name])
            for measure, name in (
                (REGISTERED, "registered"),
                (BALLOTS, "cast"),
                (VALID, "valid"),
                (INVALID, "null"),
                (BLANK, "blank"),
            )
            if name in c
        ]
        if "registered" in c and c["cast"] > c["registered"]:
            over.append(f"{mesa.code} with {c['cast']} ballots and {c['registered']} voters")
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return over


def compare() -> list[str]:
    """Each question's polling tables summed against its municipal result set, as lines of text."""
    found = places()
    out = []
    for day, spec in ELECTIONS.items():
        for contest in spec.contests:
            rows = contest_mesas(contest, found)
            if not rows:
                continue
            official = select(contest)[1]
            summed_rows = {name: sum(mesa.counts.get(name, 0) for mesa in rows) for name in official}
            if not all("registered" in mesa.counts for mesa in rows):
                summed_rows.pop("registered")
            diff = {
                name: summed_rows[name] - official[name] for name in summed_rows if summed_rows[name] != official[name]
            }
            out.append(f"{day} {contest.id}: {len(rows)} mesas; differences from the municipal set {diff or 'none'}")
    return out


def parse() -> None:
    for name in SECTIONS:
        check(name)
    check_mesas()
    geo().write()
    registry().write()
    for day in ELECTIONS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())
