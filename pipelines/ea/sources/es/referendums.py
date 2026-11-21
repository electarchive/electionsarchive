import io
import json
import re
import subprocess
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.es.infoelectoral import APLIEXTR, DOCXL, PAGE, ministry, retrieved
from ea.store import DATA, connect

SOURCE = "es.referendums"
VERSION = "1"
KEY = f"{SOURCE}/all"
MINISTRY = (
    *(f"{APLIEXTR}01{stamp}_TOTA.zip" for stamp in ("197812", "198603", "200502")),
    *(f"{APLIEXTR}01{stamp}_MESA.zip" for stamp in ("198603", "200502")),
    DOCXL + "PROV_01_197612_1.zip",
)
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales: referendos (ficheros de datos)"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
REFERENDUMS = {
    date(1976, 12, 15): (
        "197612",
        "Law for Political Reform",
        "Ley para la Reforma Política",
        "mandatory_referendum",
        "Referéndum sobre la Ley para la Reforma Política",
        (
            "Voters approved or rejected the Law for Political Reform, the last of the Fundamental Laws of the Francoist "
            "state, which opened the way to the 1977 general election. The ministry publishes it by province only."
        ),
    ),
    date(1978, 12, 6): (
        "197812",
        "Spanish Constitution",
        "Constitución Española",
        "mandatory_referendum",
        "Referéndum sobre la Constitución Española",
        "Voters ratified the Constitution passed by the Constituent Cortes. The ministry publishes it by province.",
    ),
    date(1986, 3, 12): (
        "198603",
        "Spain's membership of the North Atlantic Treaty Organization",
        "Permanencia de España en la Alianza Atlántica",
        "optional_referendum",
        "Referéndum sobre la permanencia de España en la OTAN",
        (
            "A consultative referendum under article 92 of the Constitution on whether Spain should remain in the "
            "Atlantic Alliance on the terms the Government set out."
        ),
    ),
    date(2005, 2, 20): (
        "200502",
        "Treaty establishing a Constitution for Europe",
        "Tratado por el que se establece una Constitución para Europa",
        "optional_referendum",
        "Referéndum sobre el Tratado por el que se establece una Constitución para Europa",
        (
            "A consultative referendum under article 92 of the Constitution on the Treaty establishing a Constitution "
            "for Europe."
        ),
    ),
}
ENGLISH = {
    date(1976, 12, 15): "December 1976 Spanish referendum on the Law for Political Reform",
    date(1978, 12, 6): "December 1978 Spanish constitutional referendum",
    date(1986, 3, 12): "March 1986 Spanish referendum on NATO membership",
    date(2005, 2, 20): "February 2005 Spanish referendum on the European Constitution",
}
SYSTEM = (
    "Voters answer Yes or No to one question. The ministry records Yes votes, No votes, blank ballots, and null "
    "ballots; blank ballots count as valid. The answer with more votes carries."
)
PROVINCE_METHOD = (
    "Yes and No votes, registered voters, blank ballots, and null ballots by province from the Ministry of the "
    "Interior's data files. The province rows include the votes of residents abroad (CERA). Registered voters are "
    "the count used in the tally. Valid votes here are Yes and No together; blank ballots, which the ministry counts "
    "as valid, are stored apart. The national row, where the "
    "file has one, equals the sum of the provinces."
)
PROVINCE_ONLY = (
    "The ministry's 1976 file gives results by province only, with no national row; the national figures are the "
    "sums of the provinces."
)
MESA_METHOD = (
    "Yes and No votes, registered voters, blank ballots, and null ballots by polling station (mesa) from the "
    "Ministry of the Interior's data files, with the votes of residents abroad (CERA) as one unit per province "
    "counted on the abroad channel. A mesa sits on its census section where the section code is in the 2025 INE "
    "section map, otherwise on its municipality."
)
MESA_EXACT = " The mesa figures add up to the province totals exactly."
MESA_PROVISIONAL = (
    "The ministry flags the 1986 mesa figures as not official: they come from the provisional count and exceed the "
    "official province totals by {yes:,} Yes and {no:,} No votes, so the official province set is the default."
)


BOE = "Boletín Oficial del Estado"
BOE_LICENSE = "https://www.boe.es/informacion/aviso_legal/index.php"
GAZETTE = "https://www.boe.es/boe/dias/"
# The decree that put each question to the voters, as the BOE printed it, and the question it set, read from the
# decree's page; the build checks that the page carries the question.
QUESTIONS: dict[str, tuple[str, str, str]] = {
    "1976-12-15:referendum": (
        (
            "Real Decreto 2635/1976, de 24 de noviembre, por el que se somete a Referéndum de la Nación el Proyecto de Ley "
            "para la Reforma Política (BOE 282, 24 November 1976)"
        ),
        GAZETTE + "1976/11/24/pdfs/A23350-23351.pdf",
        "¿Aprueba el Proyecto de Ley para la Reforma Política?",
    ),
    "1978-12-06:referendum": (
        (
            "Real Decreto 2560/1978, de 3 de noviembre, por el que se somete a Referéndum de la Nación el proyecto de "
            "Constitución (BOE 265, 6 November 1978)"
        ),
        GAZETTE + "1978/11/06/pdfs/A25365-25382.pdf",
        "¿Aprueba el proyecto de Constitución?",
    ),
    "1986-03-12:referendum": (
        (
            "Real Decreto 214/1986, de 6 de febrero, por el que se somete a referéndum de la Nación la decisión política "
            "del Gobierno en relación con la Alianza Atlántica (BOE 33, 7 February 1986)"
        ),
        GAZETTE + "1986/02/07/pdfs/A05072-05073.pdf",
        (
            "¿Considera conveniente para España permanecer en la Alianza Atlántica en los términos acordados por el "
            "Gobierno de la Nación?"
        ),
    ),
    "2005-02-20:referendum": (
        (
            "Real Decreto 5/2005, de 14 de enero, por el que se somete a referéndum consultivo de la Nación la decisión "
            "política de ratificar el Tratado por el que se establece una Constitución para Europa (BOE 13, 15 January "
            "2005)"
        ),
        GAZETTE + "2005/01/15/pdfs/A01627-01628.pdf",
        "¿Aprueba usted el Tratado por el que se establece una Constitución para Europa?",
    ),
    "1979-10-25:referendum:basque-country": (
        (
            "Real Decreto-ley 13/1979, de 14 de septiembre, por el que se somete a referéndum el proyecto de Estatuto de "
            "autonomía para el País Vasco (BOE 229, 24 September 1979)"
        ),
        GAZETTE + "1979/09/24/pdfs/A22237-22243.pdf",
        "¿Aprueba el proyecto de Estatuto de autonomía para el País Vasco?",
    ),
    "1980-02-28:referendum:andalusia-initiative": (
        (
            "Real Decreto 145/1980, de 26 de enero, por el que se convoca a referéndum de las provincias de Almería, Cádiz, "
            "Córdoba, Granada, Huelva, Jaén, Málaga y Sevilla en cumplimiento de lo dispuesto en el artículo 151 de la "
            "Constitución (BOE 24, 28 January 1980)"
        ),
        GAZETTE + "1980/01/28/pdfs/A02084-02085.pdf",
        (
            "¿Da usted su acuerdo a la ratificación de la iniciativa prevista en el artículo ciento cincuenta y uno de la "
            "Constitución a efectos de su tramitación por el procedimiento establecido en dicho artículo?"
        ),
    ),
    "1980-12-21:referendum:galicia": (
        (
            "Real Decreto 2400/1980, de 7 de noviembre, por el que se somete a referéndum el proyecto de Estatuto de "
            "Autonomía para Galicia (BOE 269, 8 November 1980)"
        ),
        GAZETTE + "1980/11/08/pdfs/A24949-24955.pdf",
        "¿Aprueba el proyecto de Estatuto de Autonomía para Galicia?",
    ),
    "1981-10-20:referendum:andalusia": (
        (
            "Real Decreto 1835/1981, de 20 de agosto, por el que se somete a referéndum el proyecto de Estatuto de "
            "Autonomía para Andalucía (BOE 204, 26 August 1981)"
        ),
        GAZETTE + "1981/08/26/pdfs/A19596-19602.pdf",
        "¿Aprueba el proyecto de Estatuto de Autonomía para Andalucía?",
    ),
    "2007-02-18:referendum:andalusia-reform": (
        (
            "Decreto del Presidente 2/2007, de 16 de enero, por el que se somete a referéndum el Proyecto de Reforma del "
            "Estatuto de Autonomía para Andalucía (BOE 15, 17 January 2007)"
        ),
        GAZETTE + "2007/01/17/pdfs/A02318-02357.pdf",
        "¿Aprueba el proyecto de Estatuto de Autonomía para Andalucía?",
    ),
}
# The question of 1986 refers to the terms of the Government's decision, which article 1 of the decree prints in
# full as the decision put to the vote.
DECISIONS = {
    "1986-03-12:referendum": (
        "El Gobierno considera conveniente, para los intereses nacionales, que España permanezca en la Alianza "
        "Atlántica, y acuerda que dicha permanencia se establezca en los siguientes términos:\n\n"
        "1.° La participación de España en la Alianza Atlántica no incluirá su incorporación a la estructura militar "
        "integrada.\n\n"
        "2.° Se mantendrá la prohibición de instalar, almacenar o introducir armas nucleares en territorio español.\n\n"
        "3.° Se procederá a la reducción progresiva de la presencia militar de los Estados Unidos en España."
    ),
}
# Where the BOE prints each national referendum's result: the Junta Electoral Central's declaration from 1978, and
# for 1976, when no Junta's declaration appeared in the BOE, the law the referendum ratified. Each comes with the
# national electors, Yes, No, blank, and null ballots it prints, which the build checks against the ministry's
# provinces, and a note on how the two agree.
PROCLAMATIONS: dict[date, tuple[tuple[tuple[str, str], ...], tuple[int, int, int, int, int] | None, str]] = {
    date(1976, 12, 15): (
        (
            (
                "Ley 1/1977, de 4 de enero, para la Reforma Política (BOE 4, 5 January 1977)",
                GAZETTE + "1977/01/05/pdfs/A00170-00171.pdf",
            ),
        ),
        None,
        (
            "No declaration of the result by a Junta appears in the BOE. Ley 1/1977, the Law for Political Reform as "
            "the King enacted it on 4 January 1977, records that the project was put to the Nation and ratified by a "
            "majority of votes in the referendum of 15 December 1976, and the outcome is stored as it records it."
        ),
    ),
    date(1978, 12, 6): (
        (
            (
                (
                    "Acuerdo de la Junta Electoral Central por el que se hace público el resumen de la votación en el "
                    "Referéndum Constitucional (BOE 305, 22 December 1978)"
                ),
                GAZETTE + "1978/12/22/pdfs/A28934-28934.pdf",
            ),
        ),
        (26632180, 15706078, 1400505, 632902, 133786),
        (
            "The outcome is the one the Junta Electoral Central's summary of the vote made public (BOE 305, 22 "
            "December 1978). In every province it prints the electors, Yes, No, blank, and null ballots of the "
            "ministry's file. Its voters differ from the sum of those ballots in six provinces (Barcelona +58, Ciudad "
            "Real −24, Cuenca −1, Gerona +6, Palencia −7, Segovia −2), so its national total of 17,873,301 voters is 30 "
            "more than the ballots cast stored here."
        ),
    ),
    date(1986, 3, 12): (
        (
            (
                (
                    "Resolución de 26 de marzo de 1986 por la que se declaran los resultados oficiales definitivos del "
                    "Referéndum convocado por Real Decreto 214/1986, de 6 de febrero (BOE 79, 2 April 1986)"
                ),
                GAZETTE + "1986/04/02/pdfs/A11631-11632.pdf",
            ),
            (
                (
                    "Rectificación de errores materiales en la Resolución de 26 de marzo de 1986 por la que se declaran los "
                    "resultados oficiales definitivos del referéndum convocado por Real Decreto 214/1986, de 6 de febrero "
                    "(BOE 87, 11 April 1986)"
                ),
                GAZETTE + "1986/04/11/pdfs/A12801-12801.pdf",
            ),
        ),
        (29024494, 9054509, 6872421, 1127673, 191849),
        (
            "The outcome is the one the Junta Electoral Central declared on 26 March 1986 (BOE 79, 2 April 1986). With "
            "the correction it published on 11 April 1986, which revised the electors of Oviedo, the voters of "
            "Castellón, and the null ballots of La Rioja, its national electors, Yes, No, blank, and null ballots equal "
            "the ministry's. The voters it prints are more than the sum of those ballots stored here as ballots cast."
        ),
    ),
    date(2005, 2, 20): (
        (
            (
                (
                    "Resolución de 7 de abril de 2005, de la Junta Electoral Central, de declaración oficial de los "
                    "resultados del referéndum consultivo por el que se somete a la Nación la decisión política del "
                    "Gobierno de ratificar el Tratado por el que se establece una Constitución para Europa (BOE 84, 8 April "
                    "2005)"
                ),
                GAZETTE + "2005/04/08/pdfs/A12308-12309.pdf",
            ),
        ),
        (34692491, 11057563, 2453002, 856664, 124523),
        (
            "The outcome is the one the Junta Electoral Central declared official on 7 April 2005 (BOE 84, 8 April "
            "2005). In every province its electors, Yes, No, blank, and null ballots equal the ministry's, and its "
            "voters equal the sum of those ballots."
        ),
    ),
}
ARE = "https://hauteskundeak.euskadi.eus/ab12aAREWar/resultado/search"
ARE_PAGE = "https://hauteskundeak.euskadi.eus/webelec00-app/es/ab12aAREWar/?locale=es"
ARE_TITLE = "Archivo de resultados electorales (ARE): Referéndum Estatuto de Autonomía 1979"
ARE_BODY: dict[str, object] = {"idEleccion": "37", "idDatosBase": "2", "nivelDetalle": "5", "indMarcarTodos": 1}
VALID_MAJORITY = (
    "The statute carried only if Yes won a majority of the valid votes in each province, under article 151.2 of the "
    "Constitution and Organic Law 2/1980."
)
STATUTES: dict[
    str, tuple[date, str, str, str, str, str, str, str, str, bool, tuple[tuple[str, int, int, int, int, int, int], ...]]
] = {
    "basque-country": (
        date(1979, 10, 25),
        "16",
        "1979 Basque Statute of Autonomy referendum",
        "Referéndum del Proyecto de Estatuto de Autonomía para el País Vasco de 1979",
        "Statute of Autonomy for the Basque Country",
        "Proyecto de Estatuto de Autonomía para el País Vasco",
        "Acuerdo de la Junta Electoral Central por el que se hace público el resumen de la votación en el Referéndum del Proyecto de Estatuto de Autonomía para el País Vasco (BOE 269, 9 November 1979)",
        "https://www.boe.es/boe/dias/1979/11/09/pdfs/A26027-26027.pdf",
        "Voters in Álava, Guipúzcoa, and Vizcaya approved or rejected the draft Statute of Autonomy (the Statute of "
        "Gernika), put to them by Real Decreto-ley 13/1979. " + VALID_MAJORITY,
        True,
        (
            ("01", 174930, 110609, 92535, 10023, 6363, 1688),
            ("20", 507002, 302847, 278399, 12290, 9018, 3140),
            ("48", 883609, 507980, 460905, 25216, 16038, 5821),
        ),
    ),
    "andalusia-initiative": (
        date(1980, 2, 28),
        "01",
        "1980 Andalusian autonomy initiative referendum",
        "Referéndum de iniciativa autonómica de Andalucía de 1980",
        "Ratification of the autonomy initiative under article 151 of the Constitution",
        "Ratificación de la iniciativa autonómica prevista en el artículo 151 de la Constitución",
        "Acuerdo de 24 de abril de 1980, de la Junta Electoral Central, por el que se hacen públicos los resultados del referéndum de iniciativa autonómica de Andalucía (BOE 115, 13 May 1980)",
        "https://www.boe.es/boe/dias/1980/05/13/pdfs/A10346-10346.pdf",
        (
            "Voters in the eight Andalusian provinces ratified or rejected the autonomy initiative of article 151.1 of the "
            "Constitution, convened by Real Decreto 145/1980. The initiative needed Yes from an absolute majority of the "
            "registered voters in every province. Yes reached 42.31 percent of the registered voters in Almería, so the "
            "Junta Electoral Central declared the ratification rejected, although Yes led everywhere. Organic Law 12/1980 "
            "later let the Cortes substitute the initiative in Almería."
        ),
        False,
        (
            ("04", 279300, 142769, 118186, 11092, 12307, 1184),
            ("11", 664109, 407493, 367065, 13412, 24147, 2869),
            ("14", 521027, 362616, 312419, 20339, 27528, 2355),
            ("18", 535926, 335060, 283777, 20491, 29012, 1780),
            ("21", 307943, 186748, 165976, 6808, 12897, 1067),
            ("23", 468804, 296417, 234746, 29610, 29876, 2304),
            ("29", 661825, 392379, 346819, 20822, 22020, 2718),
            ("41", 991422, 720338, 643299, 29864, 42423, 4750),
        ),
    ),
    "galicia": (
        date(1980, 12, 21),
        "12",
        "1980 Galician Statute of Autonomy referendum",
        "Referéndum sobre el proyecto de Estatuto de Autonomía para Galicia de 1980",
        "Statute of Autonomy for Galicia",
        "Proyecto de Estatuto de Autonomía para Galicia",
        "Acuerdo de 8 de enero de 1981, de la Junta Electoral Central, por el que se hacen públicos los resultados oficiales del referéndum sobre el proyecto de Estatuto de Autonomía para Galicia (BOE 9, 10 January 1981)",
        "https://www.boe.es/boe/dias/1981/01/10/pdfs/A00573-00573.pdf",
        "Voters in A Coruña, Lugo, Ourense, and Pontevedra approved or rejected the draft Statute of Autonomy put to "
        "them by Real Decreto 2400/1980. " + VALID_MAJORITY,
        True,
        (
            ("15", 844268, 270827, 196736, 56102, 12068, 5921),
            ("27", 334412, 65716, 46981, 13588, 3105, 2042),
            ("32", 355397, 75226, 58265, 12056, 2945, 1960),
            ("36", 638821, 202449, 148574, 39702, 10263, 3910),
        ),
    ),
    "andalusia": (
        date(1981, 10, 20),
        "01",
        "1981 Andalusian Statute of Autonomy referendum",
        "Referéndum sobre el proyecto de Estatuto de Autonomía para Andalucía de 1981",
        "Statute of Autonomy for Andalusia",
        "Proyecto de Estatuto de Autonomía para Andalucía",
        "Acuerdo de 16 de diciembre de 1981, de la Junta Electoral Central, por el que se hacen públicos los resultados oficiales del referéndum sobre el proyecto de Estatuto de Autonomía para Andalucía (BOE 302, 18 December 1981)",
        "https://www.boe.es/boe/dias/1981/12/18/pdfs/A29661-29661.pdf",
        "Voters in the eight Andalusian provinces approved or rejected the draft Statute of Autonomy (the Statute of "
        "Carmona) put to them by Real Decreto 1835/1981. " + VALID_MAJORITY,
        True,
        (
            ("04", 285699, 126775, 109426, 12228, 4141, 980),
            ("11", 689367, 344537, 314679, 16842, 9957, 3059),
            ("14", 531456, 329055, 294850, 23101, 9437, 1667),
            ("18", 547285, 280213, 243890, 25809, 8084, 2430),
            ("21", 299136, 155959, 142072, 7891, 5086, 910),
            ("23", 481521, 277287, 239077, 28760, 7618, 1832),
            ("29", 684292, 354375, 316210, 25160, 9880, 3125),
            ("41", 1025080, 562402, 512373, 30399, 15670, 3960),
        ),
    ),
    "andalusia-reform": (
        date(2007, 2, 18),
        "01",
        "2007 Andalusian Statute of Autonomy referendum",
        "Referéndum sobre la reforma del Estatuto de Autonomía de Andalucía de 2007",
        "Reform of the Statute of Autonomy for Andalusia",
        "Reforma del Estatuto de Autonomía para Andalucía",
        "Resolución de 8 de marzo de 2007, de la Junta Electoral Central, de declaración oficial de los resultados del referéndum sobre reforma del Estatuto de Autonomía de Andalucía (BOE 64, 15 March 2007)",
        "https://www.boe.es/boe/dias/2007/03/15/pdfs/A11237-11238.pdf",
        (
            "Voters in Andalusia ratified or rejected the reform of the Statute of Autonomy passed by the Cortes Generales, "
            "put to them by Decreto 2/2007 of the Junta de Andalucía. The reform carried with a majority of the valid votes."
        ),
        True,
        (
            ("04", 444202, 140985, 119707, 16108, 4372, 798),
            ("11", 952169, 291127, 251467, 28901, 8801, 1958),
            ("14", 639725, 257816, 220793, 26337, 8507, 2179),
            ("18", 711533, 256885, 222716, 24301, 8014, 1854),
            ("21", 384488, 134458, 120844, 9163, 3805, 646),
            ("23", 529033, 230614, 206047, 17904, 5637, 1026),
            ("29", 1065869, 341146, 295214, 33707, 10522, 1703),
            ("41", 1459053, 564802, 484156, 51534, 18195, 10917),
        ),
    ),
}
STATUTE_TOTALS = {
    "basque-country": (1565541, 921436, 831839, 47529, 31419, 10649),
    "galicia": (2172898, 614218, 450556, 121448, 28381, 13833),
    "andalusia": (4543836, 2430603, 2172577, 170190, 69873, 17963),
    "andalusia-reform": (6186072, 2217833, 1920944, 207955, 67853, 21081),
}
STATUTE_METHOD = (
    "Registered voters, voters, Yes and No votes, blank ballots, and null ballots by province as the Junta Electoral "
    "Central declared them in the Boletín Oficial del Estado, read from the gazette page and checked against its "
    "printed totals. Ballots cast are the voters the gazette prints. Valid votes here are Yes and No together; blank "
    "ballots are stored apart."
)
STATUTE_UNEVEN = (
    " The gazette prints no total row, and in {provinces} the printed voters differ from the sum of Yes, No, blank, "
    "and null ballots ({gaps}); the figures are stored as printed."
)
ARE_METHOD = (
    "Registered voters, voters, Yes and No votes, blank ballots, and null ballots by municipality from the Basque "
    "Government's electoral results archive (ARE), which names each municipality as it is today. The municipalities "
    "add up exactly to the Junta Electoral Central's province figures."
)
ARE_UNEVEN = (
    " In {count} municipalities the archive's voters differ from the sum of Yes, No, blank, and null ballots; the "
    "figures are stored as the archive prints them."
)


def folded(text: str) -> str:
    """Text without case, punctuation, spacing, or the hyphens that break a word at a line's end, so a question
    reads the same in the decree's text layer, whose scanned pages carry stray marks."""
    return re.sub(r"[\W_]+", "", re.sub("[­-]\\s*\n\\s*", "", text).lower())


def printed(artifact: raw.Artifact, text: str) -> int:
    """The page of the gazette file that prints the text."""
    pages = subprocess.run(
        ["pdftotext", str(artifact.path), "-"], check=True, capture_output=True, text=True
    ).stdout.split("\f")
    for number, page in enumerate(pages, 1):
        if folded(text) in folded(page):
            return number
    raise ValueError(f"{artifact.url} does not print {text}")


def zipped(url: str, name: str) -> bytes:
    with zipfile.ZipFile(ministry(url).path) as archive:
        return archive.read(name)


def lines(content: bytes) -> list[str]:
    return [line for line in content.decode("latin-1").split("\n") if line]


def section_of(field: str, year: int) -> str:
    if year < 2000:
        return f"{int(field):03d}"
    return field[:3] + field[3].strip()


def counts(yes: str, no: str, cand: str) -> tuple[int, int]:
    if int(cand) not in (0, int(yes) + int(no)):
        raise ValueError("Yes and No differ from the votes to candidacies")
    return int(yes), int(no)


def mesa(line: str, year: int) -> dict[str, int | str | bool]:
    cera = line[13:16] == "999"
    yes, no = counts(line[86:93], line[93:100], line[79:86])
    return {
        "yyyymm": line[2:8],
        "prov": line[11:13],
        "muni": line[13:16],
        "district": line[16:18],
        "section": "0000" if cera else section_of(line[18:22], year),
        "mesa": line[22],
        "registered": int(line[30:37]),
        "blank": int(line[65:72]),
        "null": int(line[72:79]),
        "yes": yes,
        "no": no,
        "cera": cera,
        "official": line[100] == "S",
    }


def fetch() -> None:
    # The ministry's files sit in es.infoelectoral's cache, beside the other files of its data portal. The portal
    # refuses some requests for the 1976 file, whose Internet Archive copy the cache may hold instead.
    for url in MINISTRY:
        try:
            raw.fetch("es.infoelectoral", url, suffix=".zip")
        except httpx.HTTPStatusError:
            ministry(url)
    for spec in STATUTES.values():
        raw.fetch(SOURCE, spec[7], suffix=".pdf")
    for _, url, _ in QUESTIONS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for gazettes, _, _ in PROCLAMATIONS.values():
        for _, url in gazettes:
            raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, ARE, suffix=".json", body=ARE_BODY)


def parse() -> None:
    provinces, mesas = [], []
    for day, (stamp, *_rest) in REFERENDUMS.items():
        if stamp == "197612":
            frame = pl.read_excel(
                io.BytesIO(zipped(DOCXL + "PROV_01_197612_1.zip", "PROV_01_197612_1.xlsx")),
                engine="calamine",
                has_header=False,
            ).slice(2)
            for row in frame.iter_rows():
                code = row[1].strip().zfill(2)
                blank, null = int(row[9]), int(row[10])
                yes, no = int(row[11]), int(row[12])
                if yes + no != int(row[8]) or yes + no + blank != int(row[7]):
                    raise ValueError(f"1976 province {code}: the file's figures do not add up")
                provinces.append((day, code, int(row[5]), yes, no, blank, null))
            continue
        year = int(stamp[:4])
        totals = lines(zipped(f"{APLIEXTR}01{stamp}_TOTA.zip", f"0701{stamp[2:]}.DAT"))
        for line in totals:
            yes, no = counts(line[155:163], line[163:171], line[141:149])
            if line[9:13] == "9999":
                prov = "ES"
            elif line[11:13] != "99":
                prov = line[11:13]
            else:
                continue
            provinces.append((day, prov, int(line[85:93]), yes, no, int(line[125:133]), int(line[133:141])))
        if stamp in {"198603", "200502"}:
            for line in lines(zipped(f"{APLIEXTR}01{stamp}_MESA.zip", f"0901{stamp[2:]}.DAT")):
                if line[9:11] == "99" or line[11:13] == "99":
                    continue
                mesas.append({"day": day, **mesa(line, year)})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        provinces, schema=["day", "unit", "registered", "yes", "no", "blank", "null"], orient="row"
    ).write_parquet(PARSED / "provinces.parquet")
    pl.DataFrame(mesas).write_parquet(PARSED / "mesas.parquet")
    statutes = []
    for key, spec in STATUTES.items():
        for prov, registered, voters, yes, no, blank, null in spec[10]:
            statutes.append((key, prov, registered, voters, yes, no, blank, null))
        if key in STATUTE_TOTALS:
            sums = tuple(sum(int(row[i]) for row in spec[10]) for i in range(1, 7))
            if sums != STATUTE_TOTALS[key]:
                raise ValueError(f"{key}: the provinces do not add up to the gazette's totals")
    pl.DataFrame(
        statutes, schema=["statute", "unit", "registered", "voters", "yes", "no", "blank", "null"], orient="row"
    ).write_parquet(PARSED / "statutes.parquet")
    municipalities = []
    for row in json.loads(raw.cached(SOURCE, ARE, ARE_BODY).path.read_bytes())["resultadosPorNivelDetalle"]:
        overall = row["resultadoGlobal"]
        votes = {party["siglas"]: party["votosPartido"] for party in row["resultadosPorPartido"]}
        if votes["SI"] + votes["NO"] != overall["votosCandidatura"]:
            raise ValueError(f"{row['desc']}: Yes and No differ from the votes to candidacies")
        municipalities.append(
            (
                row["desc"],
                overall["censoElectoral"],
                overall["totalVotos"],
                votes["SI"],
                votes["NO"],
                overall["votosBlancos"],
                overall["votosNulos"],
            )
        )
    pl.DataFrame(
        municipalities, schema=["name", "registered", "voters", "yes", "no", "blank", "null"], orient="row"
    ).write_parquet(PARSED / "basque_1979.parquet")
    build()


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package es.referendums/all from the parsed tables. It reads the database, through Draft, so the rows
    it rebuilds keep the ids the changeset path gave them."""
    provinces = pl.read_parquet(PARSED / "provinces.parquet")
    mesas = pl.read_parquet(PARSED / "mesas.parquet")
    with connect() as conn:
        pin = retrieved(conn, SOURCE)
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Spain's national referendums of 1976, 1978, 1986, and 2005 by province and, for 1986 and 2005, by "
                "mesa, from the Ministry of the Interior's data files, and the Basque, Galician, and Andalusian statute "
                "referendums of 1979 to 2007 by province from the Junta Electoral Central's declarations in the BOE, "
                "with each question as the decree calling the referendum set it and the declarations of the national "
                "results"
            ),
            polity="ES",
        )
        cs.accept(
            "accounting.ballots_sum",
            "the Junta Electoral Central's declaration of the 1980 Andalusian initiative prints voters in Córdoba, Jaén, "
            "and Sevilla, and the Basque Government's archive prints voters in three municipalities in 1979, that "
            "differ from the sum of Yes, No, blank, and null ballots; they are stored as printed, and each set's note "
            "says so",
        )
        cs.accept(
            "accounting.turnout",
            "the ministry's 1986 mesa file prints more ballots than registered voters in eight mesas; they are stored "
            "as printed, and the set's note counts them",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def gazette(title: str, url: str) -> tuple[UUID, raw.Artifact]:
            """The BOE item as a source, citing its file."""
            boe = cs.source(BOE, title)
            cs.add(
                "source",
                id=boe,
                kind="official_body",
                publisher=BOE,
                title=title,
                url=url,
                license="Public record: official gazette, free to reuse",
                license_url=BOE_LICENSE,
                attribution="Fuente: Boletín Oficial del Estado.",
            )
            artifact = pin(raw.cached(SOURCE, url))
            cs.artifact(artifact, boe)
            return boe, artifact

        def question(key: str, contest: UUID) -> None:
            """The question the decree put to the voters, and for 1986 the decision it asked them about."""
            title, url, text = QUESTIONS[key]
            decree, artifact = gazette(title, url)
            for kind, body in (("text", text), ("summary", DECISIONS.get(key))):
                if body is not None:
                    cs.add(
                        "ballot_question_text",
                        contest_id=contest,
                        kind=kind,
                        lang="es",
                        body=body,
                        url=url,
                        source_id=decree,
                        locator={"page": printed(artifact, body)},
                    )

        spain = cs.polity("ES")
        if conn.execute("select 1 from scheme where code = 'es.seccion'").fetchone() is None:
            raise ValueError("publish es.ine before the referendums")
        cs.add("scheme", code="es.mesa", polity_id=spain, kind="precinct", rank=5, noun="polling station")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Reuse under Ley 37/2007 with the source cited",
            attribution="Origen de los datos: Ministerio del Interior.",
        )
        for url in MINISTRY:
            cs.artifact(pin(ministry(url)), source)

        system = cs.key("es.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=spain, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Spanish national referendum")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        # Mesas sit on the 2025 section map; es.ine also holds earlier sections under the same codes, dated to end
        # when the map redrew them.
        sections = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'es.seccion' and c.valid @> date '2025-01-01'"""
            ).fetchall()
        )
        municipalities = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'es.municipio' and c.valid @> date '2025-01-01'"""
            ).fetchall()
        )
        unit_rows = {}
        nation_area = cs.area("es.nation", "ES")
        nation = cs.unit("es.nation", "ES")
        unit_rows[nation] = (nation, "es.nation", "ES", None, "equals", nation_area, "standard")
        province_units = {}
        for code in sorted(set(provinces["unit"]) - {"ES"}):
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, nation, "equals", cs.area("es.provincia", code), "standard")

        values = []
        for day, (stamp, english, native, kind, native_title, about) in REFERENDUMS.items():
            key = f"{day}:referendum"
            election = cs.key("es.election", key)
            cs.add(
                "election", id=election, polity_id=spain, kind="referendum", date_from=day, date_to=day, status="held"
            )
            name(election, ENGLISH[day])
            name(election, f"{native_title} de {day.year}", "es", "native")
            path = f"es/{day}-referendum"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("es.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=kind)
            question(key, contest)
            name(contest, english)
            name(contest, native, "es", "native")
            cs.add("slug", path=f"{path}/{slug(english)}", entity_id=contest)
            options = {}
            for position, (answer, label) in enumerate((("yes", "Sí"), ("no", "No")), 1):
                options[answer] = cs.key("es.option", f"{key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
            rows = provinces.filter(pl.col("day") == day)
            units_rows = rows.filter(pl.col("unit") != "ES")
            yes, no = int(units_rows["yes"].sum()), int(units_rows["no"].sum())
            national = rows.filter(pl.col("unit") == "ES")
            if national.height and (national["yes"][0], national["no"][0]) != (yes, no):
                raise ValueError(f"{day}: the national row differs from the sum of the provinces")
            gazettes, proclaimed, declared = PROCLAMATIONS[day]
            sums = tuple(int(units_rows[column].sum()) for column in ("registered", "yes", "no", "blank", "null"))
            if proclaimed is not None and sums != proclaimed:
                raise ValueError(f"{day}: the provinces add up to {sums}, not the {proclaimed} the BOE prints")
            # Every national referendum carried, as its declaration or the law it ratified records.
            if yes <= no:
                raise ValueError(f"{day}: Yes carried, as the BOE records, but does not lead the count")
            winner = "yes"
            proclamations = [gazette(title, url)[0] for title, url in gazettes]
            sets = {f"{key}:provinces": (rows, True, "province")}
            day_mesas = mesas.filter(pl.col("day") == day)
            if day_mesas.height:
                sets[f"{key}:mesas"] = (day_mesas, False, "mesa")
            for set_key, (rows_in, default, level) in sets.items():
                result_set = cs.key(f"{SOURCE}.result_set", set_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official" if default or day_mesas["official"].all() else "preliminary",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if level == "province":
                    note = PROVINCE_METHOD + (PROVINCE_ONLY if not national.height else "")
                else:
                    mesa_yes, mesa_no = int(rows_in["yes"].sum()), int(rows_in["no"].sum())
                    if (mesa_yes, mesa_no) == (yes, no):
                        note = MESA_METHOD + MESA_EXACT
                    else:
                        note = MESA_METHOD + " " + MESA_PROVISIONAL.format(yes=mesa_yes - yes, no=mesa_no - no)
                over = rows_in.filter(
                    pl.col("yes") + pl.col("no") + pl.col("blank") + pl.col("null") > pl.col("registered")
                ).height
                if over:
                    note += f" In {over} units the ballots cast exceed the registered voters, as the file prints them."
                cs.add("note", entity_id=result_set, kind="methodology", body_md=f"{note} {declared}")
                cs.add("note", entity_id=result_set, kind="context", body_md=about)
                for proclamation in proclamations:
                    cs.add("citation", entity_id=result_set, source_id=proclamation, locator={"page": 1})
                for answer, option in options.items():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won" if answer == winner else "lost",
                    )
                for row in rows_in.iter_rows(named=True):
                    channel = 0
                    if level == "province":
                        unit = nation if row["unit"] == "ES" else province_units[row["unit"]]
                    else:
                        cera = row["cera"]
                        yyyymm = row["yyyymm"]
                        code = f"{yyyymm}:{row['prov']}{row['muni']}{row['district']}{row['section']}{row['mesa']}"
                        unit = cs.unit("es.mesa", code)
                        if unit not in unit_rows:
                            section = f"{row['prov']}{row['muni']}{row['district']}{row['section']}"
                            if cera:
                                area, relation, channel = cs.area("es.provincia", row["prov"]), "within", ABROAD
                            elif section in sections:
                                area, relation = sections[section], "within"
                            elif row["prov"] + row["muni"] in municipalities:
                                area, relation = municipalities[row["prov"] + row["muni"]], "within"
                            else:
                                area, relation = None, "unlocated"
                            unit_rows[unit] = (
                                unit,
                                "es.mesa",
                                code,
                                province_units[row["prov"]],
                                relation,
                                area,
                                "aggregate" if cera else "standard",
                            )
                        elif cera:
                            channel = ABROAD
                    valid = row["yes"] + row["no"]
                    for option, measure, value in (
                        (options["yes"], VOTES, row["yes"]),
                        (options["no"], VOTES, row["no"]),
                        (None, REGISTERED, row["registered"]),
                        (None, BALLOTS, valid + row["blank"] + row["null"]),
                        (None, VALID, valid),
                        (None, INVALID, row["null"]),
                        (None, BLANK, row["blank"]),
                    ):
                        values.append(
                            (
                                str(result_set),
                                str(unit),
                                None if option is None else str(option),
                                measure,
                                value,
                                channel,
                            )
                        )
        statutes = pl.read_parquet(PARSED / "statutes.parquet")
        basque = pl.read_parquet(PARSED / "basque_1979.parquet")
        are = cs.source("Gobierno Vasco", ARE_TITLE)
        cs.add(
            "source",
            id=are,
            kind="official_body",
            publisher="Gobierno Vasco",
            title=ARE_TITLE,
            url=ARE_PAGE,
            license=None,
            license_url=None,
            attribution="Fuente: Gobierno Vasco, euskadi.eus.",
        )
        cs.artifact(pin(raw.cached(SOURCE, ARE, ARE_BODY)), are)
        statute_system = cs.key("es.electoral_system", "statute-referendum")
        cs.add("electoral_system", id=statute_system, polity_id=spain, rules={"formula": "referendum"}, rules_version=1)
        name(statute_system, "Spanish statute of autonomy referendum")
        cs.add("note", entity_id=statute_system, kind="methodology", body_md=SYSTEM)

        def fold(text: str) -> str:
            text = "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))
            return re.sub(r"[^A-Z]", "", text)

        basque_codes: dict[str, set[str]] = {}
        for code, text in conn.execute(
            "select c.code, n.text from area a join area_code c on c.area_id = a.id join name n on n.entity_id = a.id"
            " where a.scheme_code = 'es.municipio' and left(c.code, 2) in ('01', '20', '48')"
        ).fetchall():
            for part in {text, *re.split(r"[/-]", text)}:
                basque_codes.setdefault(fold(part), set()).add(code)

        def basque_code(label: str) -> str:
            for part in (label, *re.split(r"[/-]", label)):
                found = basque_codes.get(fold(part), set())
                if len(found) == 1:
                    return next(iter(found))
            raise ValueError(f"no single Basque municipality is named {label}")

        for statute, (
            day,
            community,
            english_title,
            native_title,
            english,
            native,
            declaration,
            declaration_url,
            about,
            carried,
            _rows,
        ) in STATUTES.items():
            key = f"{day}:referendum:{statute}"
            boe, _ = gazette(declaration, declaration_url)
            election = cs.key("es.election", key)
            cs.add(
                "election", id=election, polity_id=spain, kind="referendum", date_from=day, date_to=day, status="held"
            )
            name(election, english_title)
            name(election, native_title, "es", "native")
            path = f"es/{day}-referendum-{statute.split('-')[0]}"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("es.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=statute_system,
                district_area_id=cs.area("es.comunidad", community),
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            question(key, contest)
            name(contest, english)
            name(contest, native, "es", "native")
            cs.add("slug", path=f"{path}/{slug(english)}", entity_id=contest)
            options = {}
            for position, (answer, label) in enumerate((("yes", "Sí"), ("no", "No")), 1):
                options[answer] = cs.key("es.option", f"{key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
            rows = statutes.filter(pl.col("statute") == statute)
            uneven = rows.filter(pl.col("yes") + pl.col("no") + pl.col("blank") + pl.col("null") != pl.col("voters"))
            province_note = STATUTE_METHOD
            if uneven.height:
                names = {
                    code: text
                    for code, text in conn.execute(
                        "select c.code, n.text from area a join area_code c on c.area_id = a.id"
                        " join name n on n.entity_id = a.id and n.is_preferred"
                        " where a.scheme_code = 'es.provincia' order by n.lang = 'en'"
                    ).fetchall()
                }
                gaps = [
                    f"{names[row['unit']]} {row['voters'] - row['yes'] - row['no'] - row['blank'] - row['null']:+,}"
                    for row in uneven.iter_rows(named=True)
                ]
                province_note += STATUTE_UNEVEN.format(
                    provinces=", ".join(names[code] for code in uneven["unit"]), gaps=", ".join(gaps)
                )
            sets = {f"{key}:provinces": (rows, True, province_note, boe)}
            if statute == "basque-country":
                off = basque.filter(
                    pl.col("yes") + pl.col("no") + pl.col("blank") + pl.col("null") != pl.col("voters")
                ).height
                sets[f"{key}:municipalities"] = (
                    basque.with_columns(unit=pl.col("name").map_elements(basque_code, return_dtype=pl.String)),
                    False,
                    ARE_METHOD + (ARE_UNEVEN.format(count=off) if off else ""),
                    are,
                )
            for set_key, (rows_in, default, note, set_source) in sets.items():
                result_set = cs.key(f"{SOURCE}.result_set", set_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=set_source,
                    is_default=default,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                cs.add("note", entity_id=result_set, kind="context", body_md=about)
                for answer, option in options.items():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won" if (answer == "yes") == carried else "lost",
                    )
                for row in rows_in.iter_rows(named=True):
                    code = row["unit"]
                    if default:
                        unit = province_units.get(code)
                        if unit is None:
                            unit = province_units[code] = cs.unit("es.provincia", code)
                            area = cs.area("es.provincia", code)
                            unit_rows[unit] = (unit, "es.provincia", code, nation, "equals", area, "standard")
                    else:
                        unit = cs.unit("es.municipio", code)
                        if unit not in unit_rows:
                            area = municipalities.get(code)
                            unit_rows[unit] = (
                                unit,
                                "es.municipio",
                                code,
                                province_units[code[:2]],
                                "unlocated" if area is None else "equals",
                                area,
                                "standard",
                            )
                    for option, measure, value in (
                        (options["yes"], VOTES, row["yes"]),
                        (options["no"], VOTES, row["no"]),
                        (None, REGISTERED, row["registered"]),
                        (None, BALLOTS, row["voters"]),
                        (None, VALID, row["yes"] + row["no"]),
                        (None, INVALID, row["null"]),
                        (None, BLANK, row["blank"]),
                    ):
                        values.append(
                            (str(result_set), str(unit), None if option is None else str(option), measure, value, 0)
                        )
        minted = [row for row in unit_rows.values() if row[0] in cs.minted]
        if minted:
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
                        for unit, scheme, code, parent, relation, area, kind in minted
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
