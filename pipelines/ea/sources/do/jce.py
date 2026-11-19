import functools
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter
from datetime import date
from typing import Any, NamedTuple
from uuid import uuid5

import fastexcel
import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "do.jce"
SITE = "https://jce.gob.do"
REPOSITORY = f"{SITE}/portaltransparencia/Repositorio"
BULLETINS = "http://resultadoselectorales.jce.gob.do/boletines"
DOMINICAN = str(uuid5(package.NAMESPACE, "polity\x1fDO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fDOM"))
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1frepositorio"))
LICENSE = (
    "Public information of the Junta Central Electoral, published in its transparency portal under the Ley General "
    "de Libre Acceso a la Información Pública (Ley 200-04); reuse with the source acknowledged"
)

VOTES, REGISTERED, CAST, VALID, INVALID, OBJECTED = 1, 2, 3, 4, 5, 15
MEASURES = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "null": INVALID, "observed": OBJECTED}
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/DOM"
GB_PROVINCES = f"{GEOBOUNDARIES}/ADM1/geoBoundaries-DOM-ADM1.geojson"
GB_MUNICIPALITIES = f"{GEOBOUNDARIES}/ADM2/geoBoundaries-DOM-ADM2.geojson"
COD_URL = (
    "https://data.humdata.org/dataset/1b26ea94-4d28-4167-9209-4984ab087973/resource/"
    "66c7a7c9-5be1-4b1a-8b5a-a559e0c970a8/download/dom_admin_boundaries.geojson.zip"
)
"""The Oficina Nacional de Estadística's administrative boundaries, as OCHA publishes them (CC BY-IGO), valid June 2021."""
COD_FROM = date(2021, 6, 29)
SHAPES_FROM = date(2020, 1, 1)
MUNICIPALITY_COD = {
    1: "SANTO DOMINGO DE GUZMAN",
    12: "SAN JUAN",
    22: "NEIBA",
    31: "SANTIAGO",
    41: "MONTE CRISTI",
    96: "VILLA BISONO NAVARRETE",
    155: "LA MATA",
}
"""Municipalities, by the Board's code, that the ONE boundaries name otherwise than the Board does."""
PROMOTED = {149: 149, 156: 156, 161: 161}
"""Municipalities the Board codes with the number of the municipal district they grew out of; the ONE boundaries still
draw each as that district."""
DISTRICT_COD = {
    141: "LAS LAGUNAS DE NISIBON",
    163: "TIREO",
    195: "JAIBON PUEBLO NUEVO",
    199: "VILLA DE SONADOR",
    208: "JUAN LOPEZ",
    236: "LAS LAGUNAS",
    237: "EL CEDRO",
    252: "SAN JOSE DEL PUERTO",
    253: "PALO VERDE",
    260: "GANADERO",
    268: "LA CUESTA",
    270: "JAMAO AFUERA",
    273: "EL HIGUERITO",
    275: "LA ORTEGA",
    297: "YAQUE",
    298: "MONSERRATE MONTSERRAT",
    299: "BOYA",
    301: "BAORUCO",
    302: "CATALINA",
    312: "PALMAREJO VILLA LINDA",
    317: "DERRUMBADERO",
    334: "PUERTO VIEJO",
    340: "DONA EMMA BALAGUER VIUDA VALLEJO",
    341: "CLAVELLINA",
    348: "JORJILLO",
    352: "CANABACOA",
    357: "LAS CHARCAS DE MARIA NOVA",
    360: "CALETA",
    375: "EL ESTRECHO DE LUPERON OMAR BROSS",
    376: "PRESIDENTE DON ANTONIO GUZMAN FERNANDEZ",
    378: "SANTA LUCIA",
    385: "VERON PUNTA CANA",
    390: "LAS ZANJAS",
}
"""Municipal districts, by the Board's code, that the ONE boundaries name otherwise than the Board does."""
PROVINCE_SHAPES = {"LA ESTRELLETA": "ELIAS PINA", "EL SEYBO": "EL SEIBO", "HERMANAS": "HERMANAS MIRABAL"}
"""Province names the geoBoundaries extract spells otherwise than the Board does."""
MUNICIPALITY_SHAPES = {
    "AZUA DE COMPOSTELA": "AZUA",
    "VILLA TABARA ARRIBA": "TABARA ARRIBA",
    "SANTA CRUZ DE BARAHONA": "BARAHONA",
    "HOSTOS": "EUGENIO MARIA DE HOSTOS",
    "VILLA RIVAS": "VILLA RIVA",
    "SANTA CRUZ DEL SEYBO": "EL SEIBO",
    "HATO MAYOR DEL REY": "HATO MAYOR",
    "SALVALEON DE HIGUEY": "HIGUEY",
    "CONCEPCION DE LA VEGA": "LA VEGA",
    "CASTANUELA": "CASTANUELAS",
    "SAN FERNANDO DE MONTE CRISTI": "MONTECRISTI",
    "VILLA VAZQUEZ": "VILLA VASQUEZ",
    "ESPERALVILLO": "PERALVILLO",
    "LA ISABELA": "VILLA ISABELA",
    "MONTELLANO": "VILLA MONTELLANO",
    "SAN FELIPE DE PUERTO PLATA": "PUERTO PLATA",
    "LA MATA": "VILLA LA MATA",
    "SANTA BARBARA DE SAMANA": "SAMANA",
    "CAMBITA GARABITO": "CAMBITA GARABITOS",
    "NIGUA": "SAN GREGORIO DE NIGUA",
    "SAN GREGORIO DE YAGUATE": "YAGUATE",
    "QUISQUELLA": "QUISQUEYA",
    "LOS ALMACIGOS": "VILLA LOS ALMACIGOS",
    "VILLA BISONO": "VILLA BISONO NAVARRETE",
    "GUERRA": "SAN ANTONIO DE GUERRA",
}
"""Municipality names the geoBoundaries extract spells otherwise than the Board does."""
LOCAL_2024 = "2024-02-18"
LOCAL_SUMMARIES = frozenset({"local", "mayors", "directors"})
"""The kinds of printed municipal summaries, which are not presidential summaries and are read apart."""
ADJUSTMENT = "9999"
"""The college code of the rows in which the Board redistributes a municipality's null and observed ballots."""
ABROAD = 60
"""Province codes from 60 up are the countries where Dominicans abroad vote."""


def entry(number: int) -> str:
    return f"{REPOSITORY}?EntryId={number}&Command=Core_Download"


class Sheet(NamedTuple):
    level: str
    url: str
    suffix: str
    layout: str
    sheet: str | None = None
    filter: str | None = None


class Day(NamedTuple):
    key: str
    day: date
    kind: str
    word: str
    english: str
    spanish: str
    sheets: tuple[Sheet, ...]
    totals: tuple[tuple[str, str, str], ...] = ()


DAYS = (
    Day(
        "2000-05-16",
        date(2000, 5, 16),
        "general",
        "presidential",
        "2000 Dominican Republic presidential election",
        "Elecciones presidenciales de 2000",
        (Sheet("president", entry(1863), ".xlsx", "2000"),),
        (("president", f"{BULLETINS}/2000/resultadopresidencialdel2000porprovincia.htm", ".html"),),
    ),
    Day(
        "2002-05-16",
        date(2002, 5, 16),
        "general",
        "congressional-municipal",
        "2002 Dominican Republic congressional and municipal elections",
        "Elecciones congresuales y municipales de 2002",
        (Sheet("congress", entry(1864), ".xls", "2002"), Sheet("municipal", entry(1865), ".xls", "2002")),
    ),
    Day(
        "2004-05-16",
        date(2004, 5, 16),
        "general",
        "presidential",
        "2004 Dominican Republic presidential election",
        "Elecciones presidenciales de 2004",
        (Sheet("president", entry(1868), ".xls", "2004"),),
        (("president", f"{BULLETINS}/2004/Boletines2004/TotalN.asp?boletin=10", ".html"),),
    ),
    Day(
        "2006-05-16",
        date(2006, 5, 16),
        "general",
        "congressional-municipal",
        "2006 Dominican Republic congressional and municipal elections",
        "Elecciones congresuales y municipales de 2006",
        (Sheet("congress", entry(1870), ".xls", "2006"), Sheet("municipal", entry(1871), ".xls", "2006")),
    ),
    Day(
        "2008-05-16",
        date(2008, 5, 16),
        "general",
        "presidential",
        "2008 Dominican Republic presidential election",
        "Elecciones presidenciales de 2008",
        (Sheet("president", entry(1873), ".xls", "2008"),),
    ),
    Day(
        "2010-05-16",
        date(2010, 5, 16),
        "general",
        "congressional-municipal",
        "2010 Dominican Republic congressional and municipal elections",
        "Elecciones congresuales y municipales de 2010",
        (
            Sheet("congress", entry(1875), ".xls", "2010", filter="1"),
            Sheet("municipal", entry(1875), ".xls", "2010", filter="2"),
        ),
    ),
    Day(
        "2012-05-20",
        date(2012, 5, 20),
        "general",
        "general",
        "2012 Dominican Republic general election",
        "Elecciones generales de 2012",
        (Sheet("president", entry(1878), ".xls", "2012"), Sheet("abroad", entry(1877), ".xls", "2012")),
    ),
    Day(
        "2016-05-15",
        date(2016, 5, 15),
        "general",
        "general",
        "2016 Dominican Republic general election",
        "Elecciones generales de 2016",
        (
            Sheet("president", entry(5415), ".xlsx", "2016"),
            Sheet("congress", entry(5417), ".xlsx", "2016"),
            Sheet("municipal", entry(5416), ".xlsx", "2016"),
        ),
        (("president", entry(5397), ".pdf"), ("congress", entry(5401), ".pdf")),
    ),
    Day(
        "2020-03-15",
        date(2020, 3, 15),
        "local",
        "municipal",
        "2020 Dominican Republic municipal elections",
        "Elecciones municipales de 2020",
        (Sheet("mayors", entry(17210), ".xlsx", "2020"),),
        (
            ("local", entry(17678), ".pdf"),
            ("mayors", entry(16972), ".pdf"),
            ("directors", entry(16971), ".pdf"),
        ),
    ),
    Day(
        "2020-07-05",
        date(2020, 7, 5),
        "general",
        "general",
        "2020 Dominican Republic general election",
        "Elecciones generales de 2020",
        (
            Sheet("president", entry(18735), ".xlsx", "2016", "Presidente"),
            Sheet("senate", entry(18735), ".xlsx", "2016", "Senadores"),
            Sheet("deputies", entry(18735), ".xlsx", "2016", "Diputados"),
            Sheet("abroad", entry(18735), ".xlsx", "2016", "Diputados Ultramar"),
        ),
        (
            ("president", entry(18728), ".pdf"),
            ("senate", entry(18730), ".pdf"),
            ("deputies", entry(18721), ".pdf"),
            ("abroad", entry(18723), ".pdf"),
        ),
    ),
    Day(
        "2024-02-18",
        date(2024, 2, 18),
        "local",
        "municipal",
        "2024 Dominican Republic municipal elections",
        "Elecciones municipales de 2024",
        (Sheet("local", entry(29920), ".xlsx", "local"),),
        (("local", entry(29222), ".pdf"),),
    ),
    Day(
        "2024-05-19",
        date(2024, 5, 19),
        "general",
        "general",
        "2024 Dominican Republic general election",
        "Elecciones generales de 2024",
        (
            Sheet("president", entry(30612), ".xlsx", "2024"),
            Sheet("senate", entry(30617), ".pdf", "relation"),
            Sheet("deputies", entry(30613), ".pdf", "relation"),
            Sheet("abroad", entry(30614), ".pdf", "relation"),
        ),
        (("president", entry(30616), ".pdf"),),
    ),
)

WINNERS = {
    "2016-05-15": entry(5404),
    "2020-07-05": entry(18724),
    "2024-05-19": entry(30611),
}
"""The Board's lists of the elected, by election day: the winners of the presidency, the Senate, and the Chamber."""


FIELDS = ("prov", "prov_name", "mun", "mun_name", "dm", "dm_name", "circ", "col", "letter", "level")
LAYOUTS: dict[str, tuple[dict[str, tuple[str, ...]], frozenset[str]]] = {
    "2000": (
        {
            "prov": ("prov",),
            "mun": ("mun",),
            "col": ("colegion",),
            "letter": ("colegioh",),
            "registered": ("inscritos",),
            "cast": ("emitidos",),
            "valid": ("validos",),
            "null": ("nulos",),
            "observed": ("observados",),
        },
        frozenset({"no_boletin", "miembros de la mesa"}),
    ),
    "2002": (
        {
            "prov": ("prov",),
            "prov_name": ("provincia",),
            "mun": ("mun",),
            "mun_name": ("municipio",),
            "col": ("colegio",),
            "circ": ("circuscripcion",),
            "cast": ("vemitidos",),
            "valid": ("vvalidos",),
        },
        frozenset({"cod_recinto", "descripcion", "direccion"}),
    ),
    "2004": (
        {
            "prov": ("cod_provincia",),
            "prov_name": ("provincia",),
            "mun": ("cod_municipio",),
            "mun_name": ("municipio",),
            "col": ("colegio",),
            "circ": ("circuscripcion",),
            "cast": ("emitidos",),
            "valid": ("validos",),
            "null": ("nulos",),
            "observed": ("obs",),
        },
        frozenset({"cod_recinto", "descripcion", "direccion", "miembros"}),
    ),
    "2006": (
        {
            "prov": ("prov",),
            "prov_name": ("desc_prov",),
            "mun": ("mun",),
            "mun_name": ("desc_mun",),
            "col": ("colegio",),
            "circ": ("circunscripcion",),
            "registered": ("inscritos",),
            "cast": ("vemitidos",),
            "valid": ("validos",),
        },
        frozenset({"cod_recinto", "descripcion", "direccion"}),
    ),
    "2008": (
        {
            "prov_name": ("provincia",),
            "mun_name": ("municipio",),
            "col": ("colegio",),
            "registered": ("inscritos",),
            "cast": ("emitidos",),
            "valid": ("validos",),
            "null": ("nulos",),
            "observed": ("obs",),
        },
        frozenset({"cod_recinto", "descripcion", "direccion"}),
    ),
    "2010": (
        {
            "prov": ("cod_prov",),
            "prov_name": ("desc_prov",),
            "mun": ("cod_mun",),
            "mun_name": ("desc_mun",),
            "circ": ("cod_circ",),
            "dm": ("cod_dm",),
            "dm_name": ("desc_dm",),
            "col": ("cod_col",),
            "level": ("cod_nivel",),
        },
        frozenset({"letra"}),
    ),
    "2012": (
        {
            "prov": ("cod. provincia",),
            "prov_name": ("provincia",),
            "mun": ("cod. municipio",),
            "mun_name": ("municipio",),
            "circ": ("circunscripcion", "circuscripcion"),
            "dm": ("cod. distrito municipal", "cod. distrito municipa"),
            "dm_name": ("distrito municipal",),
            "col": ("colegio",),
            "registered": ("tot. inscritos",),
            "cast": ("votos emitidos",),
            "valid": ("votos validos",),
            "null": ("votos nulos",),
            "observed": ("votos observados",),
        },
        frozenset({"cod. recinto", "recinto electoral", "miembros"}),
    ),
    "2016": (
        {
            "prov": ("cod_prov",),
            "prov_name": ("desc_prov",),
            "mun": ("cod_mun",),
            "mun_name": ("desc_mun",),
            "circ": ("cod_circ",),
            "dm": ("cod_dm",),
            "dm_name": ("desc_dm",),
            "col": ("cod_col",),
            "registered": ("tot_inscritos",),
            "cast": ("emitidos",),
            "valid": ("validos",),
            "null": ("nulos",),
            "observed": ("obs",),
        },
        frozenset({"cod_nivel", "letra", "desc_nivel", "miembros", "no_bol_jme", "anulado"}),
    ),
    "2020": (
        {
            "prov": ("cod_prov",),
            "prov_name": ("desc_prov",),
            "mun": ("cod_mun",),
            "mun_name": ("desc_mun",),
            "circ": ("cod_circ",),
            "dm": ("cod_dm",),
            "dm_name": ("desc_dm",),
            "col": ("cod_col",),
            "level": ("cod_nivel",),
            "registered": ("tot_inscritos",),
            "cast": ("emitidos",),
            "valid": ("validos",),
            "null": ("nulos",),
            "observed": ("obs",),
        },
        frozenset({"letra", "desc_nivel", "miembros", "no_bol_jme", "anulado"}),
    ),
    "2024": (
        {
            "prov": ("cod_prov",),
            "prov_name": ("provincia",),
            "mun": ("cod_mun",),
            "mun_name": ("desc_mun",),
            "circ": ("cod_circ",),
            "dm": ("cod_dm",),
            "col": ("cod_col",),
            "cast": ("emitidos",),
            "valid": ("validos",),
            "null": ("nulos",),
            "observed": ("obs",),
        },
        frozenset({"cod_nivel", "desc_nivel"}),
    ),
}
ALIASES = {"F.AMPLIO": "FRENTE AMPLIO", "F. AMPLIO": "FRENTE AMPLIO", "PQD": "PQDC", "PUD O UD": "UD"}
"""Spellings of one party's acronym across the Board's files and summaries."""
SMALL = {"de", "del", "la", "las", "los", "y", "el", "en", "por", "a", "al", "e", "o"}

METHOD = (
    "Counted by the Junta Central Electoral per electoral college (colegio electoral), as published in the "
    "definitive results tables (relación general definitiva del cómputo electoral) of its transparency portal. Each "
    "party column is the votes the college gave that party's ballot line, whether the party ran alone or in an "
    "alliance. Null ballots are stored as invalid ballots and observed (objected) ballots as objected ballots; the "
    "Board counts observed ballots in ballots cast apart from valid and null ballots. A party whose column is zero in "
    "every college of a contest is left out of that contest. Colleges abroad and in prisons are placed in no area."
)
RELATION = (
    "Counted by the Junta Central Electoral and published in the definitive results tables (relación general "
    "definitiva del cómputo electoral) of its transparency portal, one table per province for the Senate and one per "
    "circumscription for the deputies, with no figures per electoral college. Each party's votes are those of its "
    "ballot line, whether it ran alone or in an alliance; the alliance totals the Board prints are not stored. Null "
    "ballots are stored as invalid ballots and observed (objected) ballots as objected ballots. The preferential "
    "votes the deputies tables print by candidate number, without names, are not stored."
)
ADJUSTED = (
    " For each municipality the Board also prints a row with college code 9999, the distribution of null and "
    "observed ballots (distribución de nulos y observados del municipio): the ballots its review moved between null, "
    "observed, and the parties after the count. Each such row is stored as an aggregate unit in its municipality with "
    "its party votes and its valid, null, and observed figures where they are not negative; its registered voters and "
    "ballots cast, printed as zero, are not stored."
)
CONGRESS = (
    " The Board publishes one count per party for the congressional ballot, which elected the province's senator and "
    "its deputies together; the same counts are stored for the province's Senate contest and for the deputies "
    "contest of each circumscription, while colleges abroad count only toward the deputies abroad."
)
MUNICIPAL = (
    " The Board publishes one count per party for the municipal ballot, which elected the mayor (síndico or alcalde) "
    "and the council (regidores) of a municipality, or the director and members (vocales) of a municipal district; "
    "each college counts toward the ballot of the municipal district it lies in, or of its municipality otherwise."
)
LOCAL = (
    " The municipal elections file prints each college's count once per office: mayor (alcalde) and council "
    "(regidores) by municipality, the council by municipal circumscription where a municipality has several, and "
    "director and members (vocales) by municipal district. The preferential votes for council and district candidates "
    "are printed by candidate number without names and are not stored."
)
MAYORS = (
    " The college file prints each college once, under the mayor's ballot (alcalde) of its municipality or the "
    "director's ballot of the municipal district it lies in, and none of the council (regidores) or district members "
    "(vocales) ballots, whose votes the Board prints only by candidate number and in the definitive tables by "
    "circumscription. Colleges marked annulled print no votes and are stored as printed."
)
LISTS = (
    "Counted by the Junta Central Electoral and published in the definitive results tables (relación general "
    "definitiva del cómputo electoral) of its transparency portal, one table of seats per ballot: each municipality's "
    "council (regidores), each circumscription of a municipality that has several, and each municipal district's "
    "members (vocales). Each option is a ballot line, a party or an alliance led by it, with the votes the Board "
    "totals for it and the seats it prints from the D'Hondt quotients; the votes of the alliance's member parties "
    "are not printed per ballot and are stored under the lead party. The Board prints no registered voters, ballots "
    "cast, valid, or null figures for these ballots, which the college files also leave out. The preferential votes "
    "its candidate tables print by candidate number, without names, are not stored."
)
NO_NAME_2008 = (
    " The 2008 file names provinces and municipalities without codes; they are matched to the Board's codes in its "
    "2006 and 2010 files by name."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", " ".join(str(text or "").split())).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


def title(text: str) -> str:
    words = " ".join(text.split()).lower().split(" ")
    return " ".join(
        word if index and word in SMALL else word[:1].upper() + word[1:] for index, word in enumerate(words) if word
    )


def place_name(text: str) -> str:
    return title(re.sub(r"\s*\(DM\)\s*$", "", " ".join(text.split()), flags=re.IGNORECASE))


def sigla(column: str) -> str:
    text = " ".join(column.strip().rstrip("_").split()).upper()
    return ALIASES.get(text, text)


def fetch() -> None:
    raw.fetch(SOURCE, GB_PROVINCES, suffix=".geojson")
    raw.fetch(SOURCE, GB_MUNICIPALITIES, suffix=".geojson")
    raw.fetch(SOURCE, COD_URL, suffix=".zip")
    for d in DAYS:
        for s in d.sheets:
            raw.fetch(SOURCE, s.url, suffix=s.suffix)
        for _, url, suffix in d.totals:
            raw.fetch(SOURCE, url, suffix=suffix)
        if d.key in WINNERS:
            raw.fetch(SOURCE, WINNERS[d.key], suffix=".xlsx")
    for url in LISTS_2020:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in ELECTED_FILES_2020:
        raw.fetch(SOURCE, url, suffix=".xlsx")
    from ea.sources.do import jce_parlacen

    jce_parlacen.fetch()


@functools.cache
def workbook(url: str) -> fastexcel.ExcelReader:
    return fastexcel.read_excel(raw.cached(SOURCE, url).path)


def _sheet_frame(s: Sheet) -> pl.DataFrame:
    book = workbook(s.url)
    return book.load_sheet_by_name(s.sheet or book.sheet_names[0], dtypes="string").to_polars()


def _number(column: str) -> pl.Expr:
    text = pl.col(column).str.strip_chars()
    return (
        pl.when(text.is_in(["NULL", ""]) | text.is_null())
        .then(None)
        .otherwise(text)
        .cast(pl.Float64, strict=True)
        .cast(pl.Int64, strict=True)
    )


def _text(column: str) -> pl.Expr:
    text = pl.col(column).str.strip_chars()
    return pl.when(text.is_in(["NULL", ""])).then(None).otherwise(text)


class Count(NamedTuple):
    """One results sheet, normalized: a row per college with its place, accounting, and party columns."""

    sheet: Sheet
    rows: pl.DataFrame
    parties: tuple[tuple[str, str], ...]


@functools.cache
def count(s: Sheet) -> Count:
    if s.layout == "relation":
        return relation_count(s)
    frame = _sheet_frame(s)
    fields, skip = LAYOUTS[s.layout]
    lower = {c.strip().lower(): c for c in frame.columns}
    used: dict[str, str] = {}
    for name, options in fields.items():
        found = next((lower[o] for o in options if o in lower), None)
        if found is None:
            raise ValueError(f"{s.url} {s.sheet}: no column for {name} among {frame.columns}")
        used[name] = found
    rest = [c for c in frame.columns if c not in used.values() and c.strip().lower() not in skip]
    if s.filter is not None:
        frame = frame.filter(pl.col(used["level"]).str.strip_chars() == s.filter)
    parties: list[tuple[str, str]] = []
    columns: list[pl.Expr] = []
    for column in rest:
        values = frame.select(_number(column)).to_series()
        code = sigla(column)
        if values.null_count() == frame.height or (s.layout == "2020" and not values.sum()):
            continue
        if re.fullmatch(r"P\d+", code):
            if values.sum():
                raise ValueError(f"{s.url}: placeholder column {column} holds votes")
            continue
        if any(code == p for p, _ in parties):
            raise ValueError(f"{s.url}: two columns for {code}")
        parties.append((code, " ".join(column.split())))
        columns.append(_number(column).alias(f"p:{code}"))
    col = pl.col(used["col"]).str.strip_chars().str.to_uppercase()
    if "letter" in used:
        col = pl.format(
            "{}{}",
            col.cast(pl.Int64).cast(pl.String).str.zfill(4),
            pl.col(used["letter"]).fill_null("").str.strip_chars(),
        )
    rows = frame.select(
        *(
            (_number(used[f]) if f in ("prov", "mun", "dm", "circ") else _text(used[f])).alias(f)
            if f in used
            else pl.lit(None, pl.Int64 if f in ("prov", "mun", "dm", "circ") else pl.String).alias(f)
            for f in FIELDS
            if f not in ("col", "letter", "level")
        ),
        col.alias("col"),
        (_text(used["level"]) if "level" in used else pl.lit(None, pl.String)).alias("level"),
        *(_number(used[m]).alias(m) if m in used else pl.lit(None, pl.Int64).alias(m) for m in MEASURES),
        *columns,
    )
    if s.layout == "2008":
        rows = _coded(rows)
    if s.layout == "2020":
        rows = rows.with_columns(pl.col("level").replace_strict({"1": "mayor", "3": "director"}))
    rows = rows.with_columns(
        grain=pl.lit(None, pl.String),
        adjustment=pl.col("col") == ADJUSTMENT,
        where=pl.when(pl.col("prov_name").fill_null("").str.contains("PENITENCIARIO"))
        .then(pl.lit("prison"))
        .when(pl.col("prov") >= ABROAD)
        .then(pl.lit("abroad"))
        .otherwise(pl.lit("home")),
    ).with_columns(
        dm=pl.when(
            (pl.col("where") != "home")
            | pl.col("dm").is_in([0, 999])
            | (pl.col("dm") == pl.col("mun"))
            | (pl.lit("dm_name" in used) & pl.col("dm_name").is_null())
        )
        .then(None)
        .otherwise(pl.col("dm")),
    )
    if s.layout == "2020" and rows.filter((pl.col("level") == "director") != pl.col("dm").is_not_null()).height:
        raise ValueError(f"{s.url}: a director row without a district, or a mayor row with one")
    return Count(s, rows, tuple(parties))


# --- Provincial and circumscription tables of 2024: a row per province or circumscription, and party.

RELATION_LABELS = (
    ("TOTAL DE INSCRITOS", "registered"),
    ("VOTOS VALIDOS", "valid"),
    ("VOTOS NULOS", "null"),
    ("VOTOS OBSERVADOS", "observed"),
    ("VOTOS EMITIDOS", "cast"),
)
PARTY_LINE = re.compile(r"^(?P<indent> *)(?P<body>\S.*?)\s{2,}(?P<votes>\d[\d,]*)(?P<rest>(?:\s+[\d.,%]+)*)\s*$")


def _relation_page(text: str, level: str) -> tuple[tuple[int, int | None], dict[str, int], list[tuple[str, int, bool]]]:
    """One page of a definitive table: its place, accounting figures, and party lines as (acronym, votes, alliance)."""
    place = re.search(r"Provincia\s*:\s*(\d+)", text)
    circ = re.search(r"Circunscripci\S+\s*:\s*(\d+)", text)
    if level == "abroad":
        if circ is None:
            raise ValueError("abroad page without a circumscription")
        prov, number = ABROAD, int(circ[1])
    elif place is None:
        raise ValueError(f"{level} page without a province: {text[:200]!r}")
    else:
        prov, number = int(place[1]), (int(circ[1]) if circ else 1 if level == "deputies" else None)
    figures: dict[str, int] = {}
    for label, field in RELATION_LABELS:
        found = re.search(rf"{label}\s*:\s*([\d,]+)", text)
        if found is None:
            raise ValueError(f"{level} {prov} {number}: no {label}")
        figures[field] = _int(found[1])
    lines: list[tuple[int, str, int]] = []
    started = False
    for line in text.splitlines():
        if "ORGANIZACI" in line:
            started = True
            continue
        if not started:
            continue
        indent = len(line) - len(line.lstrip())
        if level == "senate":
            cells = re.split(r"\s{2,}", line.strip())
            if len(cells) == 4 and re.fullmatch(r"[\d.]+%", cells[3]) and re.fullmatch(r"[\d,]+", cells[2]):
                lines.append((indent, sigla(cells[1]), _int(cells[2])))
            continue
        found = PARTY_LINE.match(line)
        if found is None:
            continue
        parts = found["body"].split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"\d\d", parts[0]):
            lines.append((indent, sigla(parts[1]), _int(found["votes"])))
    if not lines:
        raise ValueError(f"{level} {prov} {number}: no party lines")
    floor = min(indent for indent, _, _ in lines)
    parties: list[tuple[str, int, bool]] = []
    index = 0
    while index < len(lines):
        indent, code, votes = lines[index]
        members: list[tuple[str, int]] = []
        index += 1
        while index < len(lines) and lines[index][0] > floor:
            members.append((lines[index][1], lines[index][2]))
            index += 1
        if indent != floor:
            raise ValueError(f"{level} {prov} {number}: {code} is indented without an alliance")
        if members:
            parties.extend((c, v, True) for c, v in members)
        else:
            parties.append((code, votes, False))
    return (prov, number), figures, parties


@functools.cache
def relation_count(s: Sheet) -> Count:
    """A definitive table by province or circumscription, one page each, as a Count with a row per place."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, s.url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    pages = [page for page in text.split("\f") if "RELACION GENERAL DEFINITIVA" in page]
    seen: dict[tuple[int, int | None], tuple[dict[str, int], list[tuple[str, int, bool]]]] = {}
    for page in pages:
        place, figures, parties = _relation_page(page, s.level)
        if place in seen:
            raise ValueError(f"{s.url}: two pages for {place}")
        if sum(v for _, v, _ in parties) != figures["valid"]:
            raise ValueError(
                f"{s.url} {place}: parties sum to {sum(v for _, v, _ in parties)}, valid {figures['valid']}"
            )
        seen[place] = (figures, parties)
    codes = list(dict.fromkeys(code for _, parties in seen.values() for code, _, _ in parties))
    records: list[dict[str, object]] = []
    for (prov, circ), (figures, parties) in sorted(seen.items(), key=lambda item: (item[0][0], item[0][1] or 0)):
        grain = f"x:{circ}" if prov == ABROAD else f"c:{prov:02d}:{circ}" if circ is not None else f"p:{prov:02d}"
        votes = {code: v for code, v, _ in parties}
        records.append(
            {
                "prov": prov,
                "prov_name": None,
                "mun": None,
                "mun_name": None,
                "dm": None,
                "dm_name": None,
                "circ": circ,
                "col": "",
                "letter": None,
                "level": None,
                **{m: figures.get(m) for m in MEASURES},
                **{f"p:{code}": votes.get(code) for code in codes},
                "grain": grain,
                "adjustment": False,
                "where": "abroad" if prov == ABROAD else "home",
            }
        )
    rows = pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col("prov", "circ", "mun", "dm", *MEASURES).cast(pl.Int64),
        pl.col("prov_name", "mun_name", "dm_name", "letter", "level", "col").cast(pl.String),
    )
    return Count(s, rows, tuple((code, code) for code in codes))


# --- Council and district-member tables of 2020: a page per municipality, circumscription, or municipal district.

DAY_2020 = "2020-03-15"
COUNCIL_TABLE = entry(16974)
VOCALES_TABLE = entry(16973)
SEATS_TABLE = entry(16970)
LISTS_2020 = (COUNCIL_TABLE, VOCALES_TABLE, SEATS_TABLE)
ELECTED_2020 = entry(16975)
SUBSTITUTES_2020 = entry(17351)
ELECTED_FILES_2020 = (ELECTED_2020, SUBSTITUTES_2020)
ALLIED = re.compile(r"\s+Y\s+ALIADOS$")
CANDIDATE_LINE = re.compile(
    r"^(?P<indent> *)(?P<number>\d\d)\s+(?P<name>[A-Z][A-Z. ]*?)\s{2,}(?P<votes>\d[\d,]*(?:\s+\d[\d,]*)*)\s*$"
)
SEAT_LINE = re.compile(
    r"^(?P<number>\d\d)\s+(?P<name>[^\d]+?)\s+(?P<figures>\d[\d,]*(?:\s+\d[\d,]*)*)\s+(?P<seats>\d+|-+)\s*$"
)


class Page(NamedTuple):
    """One page of the 2020 council or district-member tables, and the place it counts."""

    prov: int
    mun: int
    circ: int | None
    dm: int | None
    dm_name: str | None
    lines: list[tuple[int, str, str, list[int]]]


class Ballot(NamedTuple):
    """One council or district-member ballot of 2020: each ballot line's total votes and its seats."""

    level: str
    prov: int
    mun: int
    circ: int | None
    dm: int | None
    votes: dict[str, int]
    labels: dict[str, str]
    seats: dict[str, int]


def _pages(url: str) -> list[str]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    return [page for page in text.split("\f") if "RELACION GENERAL DEFINITIVA" in page]


def _header(page: str, where: str) -> tuple[int, int, int | None]:
    prov, mun = re.search(r"Provincia\s*:\s*(\d+)", page), re.search(r"Municipio\s*:\s*(\d+)", page)
    circ = re.search(r"Circunscripci\S+\s*:\s*(\d+)", page)
    if prov is None or mun is None:
        raise ValueError(f"{where}: a page without a province or municipality: {page[:300]!r}")
    return int(prov[1]), int(mun[1]), int(circ[1]) if circ else None


@functools.cache
def candidate_pages(url: str) -> list[Page]:
    """The candidate-number tables of the council (regidores) or the district members (vocales), a page each."""
    pages: list[Page] = []
    for page in _pages(url):
        prov, mun, circ = _header(page, url)
        dm = re.search(r"Distrito Municipal\s*:\s*(\d+)\s+(.+?)\s*$", page, flags=re.MULTILINE)
        if (url == VOCALES_TABLE) != (dm is not None):
            raise ValueError(f"{url} {prov}/{mun}: a page with the wrong kind of place")
        lines: list[tuple[int, str, str, list[int]]] = []
        started = False
        for line in page.splitlines():
            if line.startswith("Agrupaci"):
                started = True
                continue
            found = CANDIDATE_LINE.match(line) if started else None
            if found:
                votes = [_int(n) for n in found["votes"].split()]
                lines.append((len(found["indent"]), found["number"], found["name"].strip(), votes))
        if not lines:
            raise ValueError(f"{url} {prov}/{mun}: no party lines")
        pages.append(Page(prov, mun, circ, int(dm[1]) if dm else None, dm[2] if dm else None, lines))
    return pages


def _lead_code(name: str) -> tuple[str, bool]:
    plain = ALLIED.sub("", sigla(name))
    return sigla(plain), plain != sigla(name)


@functools.cache
def party_numbers() -> dict[str, str]:
    """The Board's number of each party, from the candidate tables: the acronym of each ballot line's lead party."""
    found: dict[str, str] = {}
    for url in (COUNCIL_TABLE, VOCALES_TABLE):
        for page in candidate_pages(url):
            floor = min(indent for indent, _, _, _ in page.lines)
            for indent, number, name, _ in page.lines:
                if indent != floor:
                    continue
                code, _ = _lead_code(name)
                if found.setdefault(number, code) != code:
                    raise ValueError(f"party {number} prints as {found[number]} and {code}")
    return found


def _check_seats(seats: dict[str, int], quotients: dict[str, list[int]], where: str) -> None:
    """Checks that the printed seats are the highest quotients of the printed D'Hondt table, a tie at the last seat aside."""
    total = sum(seats.values())
    pool = sorted(((q, code) for code, qs in quotients.items() for q in qs), reverse=True)
    if not 0 < total <= len(pool):
        raise ValueError(f"{where}: {total} seats from {len(pool)} quotients")
    edge = pool[total - 1][0]
    sure = Counter(code for q, code in pool if q > edge)
    tied = Counter(code for q, code in pool if q == edge)
    for code, n in seats.items():
        if not sure[code] <= n <= sure[code] + tied[code]:
            raise ValueError(
                f"{where}: {code} prints {n} seats; its quotients allow {sure[code]} to {sure[code] + tied[code]}"
            )


@functools.cache
def ballots_2020() -> tuple[Ballot, ...]:
    """Every ballot of the seat tables: alliance votes and printed seats, in the place the candidate tables give."""
    numbers = party_numbers()
    council_pages = {(p.prov, p.mun, p.circ): p for p in candidate_pages(COUNCIL_TABLE)}
    vocales_pages = {(p.mun, fold(p.dm_name)): p for p in candidate_pages(VOCALES_TABLE)}
    if len(council_pages) != len(candidate_pages(COUNCIL_TABLE)) or len(vocales_pages) != len(
        candidate_pages(VOCALES_TABLE)
    ):
        raise ValueError("two candidate pages for one place")
    out: list[Ballot] = []
    seen: set[tuple[str, int, int | None, int | None]] = set()
    for page in _pages(SEATS_TABLE):
        level = "council" if "REGIDORES" in page else "vocales" if "VOCALES" in page else None
        if level is None:
            raise ValueError(f"seat page of neither kind: {page[:300]!r}")
        prov, mun, circ = _header(page, SEATS_TABLE)
        where = f"seats {level} {prov}/{mun}/{circ}"
        if level == "council":
            match = council_pages.get((prov, mun, circ))
        else:
            name = re.search(r"Distrito Municipal\s*:\s*(.+?)\s*$", page, flags=re.MULTILINE)
            if name is None:
                raise ValueError(f"{where}: no district")
            where += f" {name[1]}"
            match = vocales_pages.get((mun, fold(name[1])))
        if match is None or match.prov != prov:
            raise ValueError(f"{where}: no candidate table")
        votes: dict[str, int] = {}
        labels: dict[str, str] = {}
        seats: dict[str, int] = {}
        quotients: dict[str, list[int]] = {}
        started = False
        for line in page.splitlines():
            if line.startswith("Partidos"):
                started = True
                continue
            found = SEAT_LINE.match(line) if started else None
            if found is None:
                continue
            if found["number"] not in numbers:
                raise ValueError(f"{where}: party {found['number']} is on no candidate table")
            code = numbers[found["number"]]
            figures = [_int(n) for n in found["figures"].split()]
            if len(figures) < 2 or figures[0] != figures[1]:
                raise ValueError(f"{where}: {found['name']} total {figures[:2]} does not open its quotients")
            if code in votes:
                raise ValueError(f"{where}: {code} printed twice")
            votes[code] = figures[0]
            labels[code] = f"{code} Y ALIADOS" if _lead_code(found["name"])[1] else code
            seats[code] = 0 if found["seats"].startswith("-") else int(found["seats"])
            quotients[code] = figures[1:]
        if not votes:
            raise ValueError(f"{where}: no party lines")
        _check_seats(seats, quotients, where)
        place = (level, mun, circ, match.dm)
        if place in seen:
            raise ValueError(f"{where}: two seat pages")
        seen.add(place)
        out.append(Ballot(level, prov, mun, circ, match.dm, votes, labels, seats))
    if len(out) != len(council_pages) + len(vocales_pages):
        raise ValueError(
            f"{len(out)} seat pages for {len(council_pages)} council and {len(vocales_pages)} district tables"
        )
    return tuple(out)


@functools.cache
def coded_names() -> dict[tuple[str, str], tuple[int, int]]:
    """Province and municipality codes by folded name, from the files of 2006 and 2010, for the uncoded 2008 file."""
    found: dict[tuple[str, str], tuple[int, int]] = {}
    for d in DAYS:
        if d.day.year not in (2006, 2010):
            continue
        for s in d.sheets:
            rows = count(s).rows
            for prov, prov_name, mun, mun_name in rows.select("prov", "prov_name", "mun", "mun_name").unique().rows():
                found[(fold(prov_name), fold(mun_name))] = (prov, mun)
    return found


NAMES_2008 = {
    ("MONTE CRISTI", "MONTE CRISTI"): ("MONTECRISTI", "MONTECRISTI"),
}
"""Names in the 2008 file spelled otherwise in the 2006 and 2010 files."""


def _coded(rows: pl.DataFrame) -> pl.DataFrame:
    known = coded_names()
    home = {fold(name) for name, _ in known}
    provs: list[int | None] = []
    muns: list[int | None] = []
    missing: set[tuple[str, str]] = set()
    for prov_name, mun_name in rows.select("prov_name", "mun_name").iter_rows():
        pair = (fold(prov_name), fold(mun_name))
        pair = NAMES_2008.get(pair, pair)
        if pair in known:
            provs.append(known[pair][0])
            muns.append(known[pair][1])
        elif pair[0] in home:
            missing.add(pair)
            provs.append(None)
            muns.append(None)
        else:
            provs.append(ABROAD)
            muns.append(None)
    if missing:
        raise ValueError(f"2008 places without a code: {sorted(missing)}")
    return rows.with_columns(prov=pl.Series(provs, dtype=pl.Int64), mun=pl.Series(muns, dtype=pl.Int64))


def unit_code(day: Day) -> pl.Expr:
    """A college's code in the Board's files: its municipality and college, abroad also its country."""
    abroad_place = pl.when(pl.col("mun").is_null()).then(
        pl.col("prov_name").map_elements(fold, return_dtype=pl.String)
        + pl.lit(":")
        + pl.col("mun_name").map_elements(fold, return_dtype=pl.String)
    )
    place = (
        pl.when(pl.col("where") == "abroad")
        .then(abroad_place.otherwise(pl.col("prov").cast(pl.String) + pl.lit(":") + pl.col("mun").cast(pl.String)))
        .otherwise(pl.col("mun").cast(pl.String))
    )
    extra = pl.when(pl.col("adjustment")).then(
        pl.lit(":")
        + pl.col("circ").fill_null(0).cast(pl.String)
        + pl.lit(":")
        + pl.col("dm").fill_null(0).cast(pl.String)
        + pl.lit(":")
        + pl.col("level").fill_null("")
    )
    college = pl.lit(f"{day.key}:") + place + pl.lit(":") + pl.col("col") + extra.otherwise(pl.lit(""))
    return pl.when(pl.col("grain").is_not_null()).then(pl.lit(f"{day.key}:") + pl.col("grain")).otherwise(college)


# --- Local elections of 2024: a row per college, office, and party.

LOCAL_OFFICES = {"ALCALDE(SA)": "mayor", "REGIDOR(A)": "council", "DIRECTOR(A)": "director", "VOCAL": "vocales"}


@functools.cache
def local_count() -> Count:
    d = next(d for d in DAYS if d.key == LOCAL_2024)
    s = d.sheets[0]
    book = workbook(s.url)
    frames = [
        book.load_sheet_by_name(name, dtypes="string")
        .to_polars()
        .filter(pl.col("DescripcionCargo").is_in(list(LOCAL_OFFICES)))
        for name in book.sheet_names
        if name.startswith("Prov")
    ]
    long = pl.concat(frames).select(
        prov=_number("IdProvincia"),
        prov_name=_text("Provincia"),
        mun=_number("IdMunicipio"),
        mun_name=_text("DescripcionMunicipio"),
        dm=_number("IdDM"),
        dm_name=_text("DescripcionDM"),
        circ=_number("Circunscripcion"),
        col=pl.col("ColegioLetra").str.strip_chars().str.to_uppercase(),
        level=pl.col("DescripcionCargo").replace_strict(LOCAL_OFFICES),
        registered=pl.lit(None, pl.Int64),
        cast=_number("Emitidos"),
        valid=_number("Validos"),
        null=_number("Nulos"),
        observed=_number("Observados"),
        party=pl.col("Siglas").map_elements(sigla, return_dtype=pl.String),
        label=pl.col("Siglas").str.strip_chars(),
        name=pl.col("DescripcionEntidad").str.strip_chars(),
        votes=_number("Votos"),
    )
    order = long.select("party", "label").unique(maintain_order=True)
    if order["party"].is_duplicated().any():
        raise ValueError("2024 local: a party prints under two acronyms")
    index = ["prov", "prov_name", "mun", "mun_name", "dm", "dm_name", "circ", "col", "level", *MEASURES]
    if long.select(index).unique().group_by("mun", "col", "level", "dm").len()["len"].max() != 1:
        raise ValueError("2024 local: a college prints two accounting rows for one office")
    rows = (
        long.select(*index, party=pl.lit("p:") + pl.col("party"), votes="votes")
        .pivot(on="party", index=index, values="votes")
        .with_columns(adjustment=pl.lit(False), where=pl.lit("home"), grain=pl.lit(None, pl.String))
    )
    homes = rows.filter(pl.col("dm").is_not_null()).select("mun", "col", home_dm="dm", home_dm_name="dm_name").unique()
    if homes.select("mun", "col").is_duplicated().any():
        raise ValueError("2024 local: a college lies in two municipal districts")
    rows = rows.join(homes, on=["mun", "col"], how="left").with_columns(
        dm=pl.col("home_dm"), dm_name=pl.col("home_dm_name")
    )
    return Count(s, rows.drop("home_dm", "home_dm_name"), tuple(order.iter_rows()))


# --- Places.


class Places(NamedTuple):
    provinces: dict[int, str]
    municipalities: dict[int, tuple[int, str]]
    districts: dict[int, tuple[int, str]]
    circs: dict[tuple[int, int], str]
    abroad: set[int]
    city_circs: set[tuple[int, int]]


def counts() -> list[Count]:
    found: list[Count] = []
    for d in DAYS:
        for s in d.sheets:
            found.append(local_count() if s.level == "local" else count(s))
    return found


@functools.cache
def places() -> Places:
    """Every place the files name, with the latest name and parent the Board gives it."""
    provinces: dict[int, str] = {}
    municipalities: dict[int, tuple[int, str]] = {}
    districts: dict[int, tuple[int, str]] = {}
    circs: dict[tuple[int, int], str] = {}
    abroad: set[int] = set()
    city_circs: set[tuple[int, int]] = set()
    seen_muns: set[int] = set()
    for c in counts():
        rows = c.rows
        home = rows.filter((pl.col("where") == "home") & ~pl.col("adjustment"))
        seen_muns |= set(rows.filter(pl.col("where") == "home")["mun"].drop_nulls().to_list())
        for prov, prov_name, mun, mun_name in home.select("prov", "prov_name", "mun", "mun_name").unique().rows():
            if prov_name is not None:
                provinces[prov] = place_name(prov_name)
            if mun_name is not None:
                municipalities[mun] = (prov, place_name(mun_name))
        for mun, dm, dm_name in home.filter(pl.col("dm").is_not_null()).select("mun", "dm", "dm_name").unique().rows():
            if dm_name is not None or dm not in districts:
                districts[dm] = (mun, place_name(dm_name or f"Distrito municipal {dm}"))
        if c.sheet.level in ("congress", "deputies"):
            for prov, circ in home.select("prov", "circ").unique().rows():
                circs[(prov, circ)] = ""
        if c.sheet.level in ("congress", "abroad"):
            abroad |= set(rows.filter(pl.col("where") == "abroad")["circ"].drop_nulls().to_list())
        if c.sheet.level == "local":
            council = home.filter(pl.col("level") == "council").select("mun", "circ").unique()
            several = council.group_by("mun").len().filter(pl.col("len") > 1)["mun"].to_list()
            city_circs |= {(m, c_) for m, c_ in council.rows() if m in several}
    for page in candidate_pages(COUNCIL_TABLE):
        if page.circ is not None:
            city_circs.add((page.mun, page.circ))
    missing = seen_muns - set(municipalities)
    if missing:
        raise ValueError(f"municipalities without a name: {sorted(missing)}")
    if set(provinces) != set(range(1, 33)):
        raise ValueError(f"provinces: {sorted(provinces)}")
    unnamed = [dm for dm, (_, text) in districts.items() if text.startswith("Distrito municipal ")]
    if unnamed:
        raise ValueError(f"municipal districts without a name: {unnamed}")
    for prov, circ in circs:
        circs[(prov, circ)] = f"Circunscripción {circ}, {provinces[prov]}"
    return Places(provinces, municipalities, districts, circs, abroad, city_circs)


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Junta Central Electoral",
        title="Junta Central Electoral, Portal de Transparencia: resultados electorales por colegio",
        url=f"{SITE}/portaltransparencia",
        license=LICENSE,
        license_url=f"{SITE}/portaltransparencia",
        attribution="Junta Central Electoral (JCE), República Dominicana",
    )


def _shape(feature: dict[str, object]) -> shapely.Geometry:
    found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))  # type: ignore[arg-type]
    polygons = [
        polygon
        for part in shapely.get_parts(found)
        if shapely.get_type_id(part) in (3, 6)
        for polygon in shapely.get_parts(part)
    ]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


@functools.cache
def shapes() -> tuple[dict[int, shapely.Geometry], dict[int, shapely.Geometry]]:
    """The shapes of provinces by province code and of municipalities by municipality code, as geoBoundaries draws them."""
    found = places()
    by_province = {fold(text): code for code, text in found.provinces.items()}
    by_municipality = {fold(text): code for code, (_, text) in found.municipalities.items()}
    provinces: dict[int, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, GB_PROVINCES).path.read_bytes())["features"]:
        name = fold(feature["properties"]["shapeName"])
        code = by_province.get(PROVINCE_SHAPES.get(name, name))
        if code is None or code in provinces:
            raise ValueError(f"province shape {name} matches no province or a second time")
        provinces[code] = _shape(feature)
    municipalities: dict[int, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, GB_MUNICIPALITIES).path.read_bytes())["features"]:
        name = fold(feature["properties"]["shapeName"])
        code = by_municipality.get(MUNICIPALITY_SHAPES.get(name, name))
        if code is None:
            continue  # La Laguna de Nisibon, which the Board's files do not code as a municipality
        if code in municipalities:
            raise ValueError(f"municipality shape {name} matches a second time")
        municipalities[code] = _shape(feature)
    return provinces, municipalities


def _cod_name(text: str) -> str:
    return re.sub(r"\b(D M|MUNICIPIO)\b", "", fold(text)).replace("-", " ").strip()


@functools.cache
def cod_shapes() -> tuple[dict[int, shapely.Geometry], dict[int, shapely.Geometry]]:
    """The shapes the ONE boundaries add: municipal districts by district code, and municipalities that
    geoBoundaries lacks by municipality code."""
    found = places()
    _, drawn = shapes()
    with zipfile.ZipFile(raw.cached(SOURCE, COD_URL).path) as archive:
        level3: list[dict[str, Any]] = json.loads(archive.read("dom_admin3.geojson"))["features"]
        level4: list[dict[str, Any]] = json.loads(archive.read("dom_admin4.geojson"))["features"]
    seats = {_cod_name(f["properties"]["adm3_name"]): f for f in level3}

    def municipality_feature(mun: int) -> dict[str, Any] | None:
        return seats.get(MUNICIPALITY_COD.get(mun, _cod_name(found.municipalities[mun][1])))

    by_municipality: dict[str, dict[str, dict[str, Any]]] = {}
    for f in level4:
        if f["properties"]["adm4_pcode"].endswith("01"):
            continue  # the municipality's seat, drawn again as the municipality itself
        by_municipality.setdefault(f["properties"]["adm3_pcode"], {})[_cod_name(f["properties"]["adm4_name"])] = f
    districts: dict[int, shapely.Geometry] = {}
    used: set[str] = set()
    for dm, (mun, text) in sorted(found.districts.items()):
        parent = municipality_feature(mun)
        if parent is None:
            raise ValueError(f"municipality {mun} is not in the ONE boundaries")
        feature = by_municipality.get(parent["properties"]["adm3_pcode"], {}).get(DISTRICT_COD.get(dm, _cod_name(text)))
        if feature is None:
            continue  # created after the 2021 boundaries
        code = feature["properties"]["adm4_pcode"]
        if code in used:
            raise ValueError(f"municipal district shape {code} matches a second time")
        used.add(code)
        districts[dm] = _shape(feature)
    municipalities: dict[int, shapely.Geometry] = {}
    for mun in sorted(set(found.municipalities) - set(drawn)):
        if mun in PROMOTED:
            if PROMOTED[mun] not in districts:
                raise ValueError(f"municipality {mun} has no district shape")
            municipalities[mun] = districts[PROMOTED[mun]]
        else:
            feature = municipality_feature(mun)
            if feature is None:
                raise ValueError(f"municipality {mun} is not in the ONE boundaries")
            municipalities[mun] = _shape(feature)
    return districts, municipalities


def unshaped() -> list[str]:
    """The municipalities, each with its province, that geoBoundaries does not draw."""
    found = places()
    _, drawn = shapes()
    _, more = cod_shapes()
    return [
        f"{text} ({found.provinces[prov]})"
        for mun, (prov, text) in sorted(found.municipalities.items())
        if mun not in drawn and mun not in more
    ]


def unshaped_districts() -> list[str]:
    """The municipal districts, each with its municipality, that no boundaries draw."""
    found = places()
    drawn, _ = cod_shapes()
    return [
        f"{text} ({found.municipalities[mun][1]})"
        for dm, (mun, text) in sorted(found.districts.items())
        if dm not in drawn
    ]


def geo() -> Package:
    found = places()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Dominican Republic's provinces, municipalities, municipal districts, and congressional and municipal "
        "circumscriptions as the Junta Central Electoral codes them in its results files",
    )
    p.external |= {DOMINICAN, COUNTRY}
    _sources(p)
    province_source, municipality_source = key("source", "geoboundaries-adm1"), key("source", "geoboundaries-adm2")
    one_source = key("source", "one-cod-ab")
    for id, title_, license_, license_url, notes in (
        (
            province_source,
            "geoBoundaries gbOpen: Dominican Republic ADM1 (provinces)",
            "Public domain (Natural Earth)",
            "https://www.naturalearthdata.com/about/terms-of-use/",
            "Boundary year 2022, built December 2023.",
        ),
        (
            municipality_source,
            "geoBoundaries gbOpen: Dominican Republic ADM2 (municipalities)",
            "Creative Commons Attribution 4.0 International",
            "https://creativecommons.org/licenses/by/4.0/",
            (
                "Boundary year 2020 from the Caribbean GeoPortal, built December 2023. It draws 154 of the Board's 158 "
                "municipalities; the Oficina Nacional de Estadística's boundaries draw the other four."
            ),
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab)",
            title=title_,
            url="https://www.geoboundaries.org/",
            license=license_,
            license_url=license_url,
            attribution="geoBoundaries (William & Mary geoLab)",
            notes=notes,
        )
    p.add(
        "source",
        id=one_source,
        kind="official_body",
        publisher="Oficina Nacional de Estadística (ONE), República Dominicana, through OCHA",
        title="Dominican Republic: Subnational Administrative Boundaries (COD-AB), municipalities and municipal districts",
        url="https://data.humdata.org/dataset/cod-ab-dom",
        license="Creative Commons Attribution for Intergovernmental Organisations (CC BY-IGO)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="Oficina Nacional de Estadística (ONE) and OCHA, Dominican Republic COD-AB",
        notes=(
            "The ONE's cartography as OCHA's Field Information Services Section publishes it, valid 2021-06-29. It "
            "draws 155 municipalities and 231 municipal districts; the three municipalities newer than that plan are "
            "still drawn as the districts they grew out of."
        ),
    )
    p.artifact(raw.cached(SOURCE, COD_URL), one_source)
    p.artifact(raw.cached(SOURCE, GB_PROVINCES), province_source)
    p.artifact(raw.cached(SOURCE, GB_MUNICIPALITIES), municipality_source)
    for code, rank, kind, noun in (
        ("do.nacion", 0, "administrative", "nation"),
        ("do.provincia", 1, "administrative", "province"),
        ("do.municipio", 2, "administrative", "municipality"),
        ("do.distrito_municipal", 3, "administrative", "municipal district"),
        ("do.circunscripcion", 2, "electoral_district", "circumscription"),
        ("do.circunscripcion_municipal", 3, "electoral_district", "municipal circumscription"),
        ("do.colegio", 4, "precinct", "electoral college"),
    ):
        p.add("scheme", code=code, polity_id=DOMINICAN, kind=kind, rank=rank, noun=noun, code_system="jce")

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "es", "official")

    for prov, text in sorted(found.provinces.items()):
        place("do.provincia", f"{prov:02d}", text, COUNTRY)
    for mun, (prov, text) in sorted(found.municipalities.items()):
        place("do.municipio", str(mun), text, area("do.provincia", f"{prov:02d}"))
    for dm, (mun, text) in sorted(found.districts.items()):
        place("do.distrito_municipal", str(dm), text, area("do.municipio", str(mun)))
    for (prov, circ), text in sorted(found.circs.items()):
        place("do.circunscripcion", f"{prov:02d}:{circ}", text, area("do.provincia", f"{prov:02d}"))
    for circ in sorted(found.abroad):
        place("do.circunscripcion", f"exterior:{circ}", f"Circunscripción exterior {circ}", COUNTRY)
    for mun, circ in sorted(found.city_circs):
        text = f"Circunscripción {circ}, {found.municipalities[mun][1]}"
        place("do.circunscripcion_municipal", f"{mun}:{circ}", text, area("do.municipio", str(mun)))
    drawn_provinces, drawn_municipalities = shapes()
    drawn_districts, extra_municipalities = cod_shapes()
    for scheme, vintage, start in (
        ("do.provincia", "2022", SHAPES_FROM),
        ("do.municipio", "2020", SHAPES_FROM),
        ("do.municipio", "2021", COD_FROM),
        ("do.distrito_municipal", "2021", COD_FROM),
    ):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, vintage),
            scheme_code=scheme,
            vintage=vintage,
            valid_from=start,
            as_of=start,
        )
    for scheme, drawn, source_id, quality, start in (
        (
            "do.provincia",
            {f"{c:02d}": g for c, g in drawn_provinces.items()},
            province_source,
            "academic",
            SHAPES_FROM,
        ),
        (
            "do.municipio",
            {str(c): g for c, g in drawn_municipalities.items()},
            municipality_source,
            "academic",
            SHAPES_FROM,
        ),
        (
            "do.municipio",
            {str(c): g for c, g in extra_municipalities.items()},
            one_source,
            "official",
            COD_FROM,
        ),
        (
            "do.distrito_municipal",
            {str(c): g for c, g in drawn_districts.items()},
            one_source,
            "official",
            COD_FROM,
        ),
    ):
        for code, shape in sorted(drawn.items()):
            p.add(
                "area_geometry",
                area_id=area(scheme, code),
                valid_from=start,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality=quality,
                source_id=source_id,
            )
    return p


# --- Registry.

PRESIDENCY = key("office", "president")
SENATE = key("body", "senate")
CHAMBER = key("body", "chamber")
PARLACEN = key("body", "parlacen")
SYSTEMS = {
    "parlacen": (
        {"formula": "dhondt"},
        (
            "The twenty Dominican deputies to the Central American Parliament, with their alternates, are allotted "
            "nationally among the parties by D'Hondt from each party's national total of votes, from closed lists."
        ),
    ),
    "president": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "The President and Vice President are elected together by the national vote. A ticket that wins more "
            "than half of the valid votes is elected; otherwise the two leading tickets go to a second round. A "
            "party's votes count toward the ticket it backs, alone or in an alliance."
        ),
    ),
    "senate": ({"formula": "fptp"}, "Each province and the Distrito Nacional elects one senator by plurality."),
    "deputies": (
        {"formula": "dhondt"},
        (
            "Deputies are elected from party lists in each province or circumscription, and since 2012 in three "
            "circumscriptions abroad; seats are allocated by D'Hondt, with preferential votes ordering each list's "
            "candidates where the law provides them."
        ),
    ),
    "municipal": (
        {"formula": "dhondt"},
        (
            "One party vote elected the municipality's mayor, by plurality, and its council (regidores), by D'Hondt "
            "among the lists; in a municipal district the same vote elected the director and the members (vocales)."
        ),
    ),
    "mayor": ({"formula": "fptp"}, "The candidate with the most votes is elected."),
    "council": (
        {"formula": "dhondt"},
        (
            "Council members (regidores, or vocales in a municipal district) are elected from party lists; seats are "
            "allocated by D'Hondt."
        ),
    ),
}


def system(name: str) -> str:
    return key("electoral_system", name)


def council(scheme: str, code: str) -> str:
    return key("body", "council", scheme, code)


def mayor(scheme: str, code: str) -> str:
    return key("office", "mayor", scheme, code)


def party(code: str) -> str:
    return key("party", code)


@functools.cache
def party_names() -> dict[str, str]:
    """Each acronym's full name, from the Board's national summaries, the latest printing first."""
    found: dict[str, str] = {}
    for d in reversed(DAYS):
        for level, url, suffix in d.totals:
            if suffix == ".pdf" and level not in LOCAL_SUMMARIES:
                for name, code, _ in summary_pdf(url)[1]:
                    found.setdefault(code, name)
            if url.endswith("TotalN.asp?boletin=10"):
                for name, code, _ in summary_2004()[1]:
                    found.setdefault(code, name)
    return found


def used_parties() -> dict[str, str]:
    """Every party acronym on a results file, with the label it last printed under."""
    found: dict[str, str] = {}
    for c in counts():
        for code, label in c.parties:
            found[code] = label
    return found


def registry() -> Package:
    found = places()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Dominican presidency, Senate, and Chamber of Deputies, the mayors and councils of the municipalities and "
        "municipal districts, their electoral systems, and the parties on the Junta Central Electoral's results files",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {DOMINICAN}
    p.add("office", id=PRESIDENCY, polity_id=DOMINICAN, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Dominican Republic", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    for id, kind, english, spanish in (
        (SENATE, "upper_house", "Senate of the Dominican Republic", "Senado de la República"),
        (CHAMBER, "lower_house", "Chamber of Deputies of the Dominican Republic", "Cámara de Diputados"),
    ):
        p.add("body", id=id, polity_id=DOMINICAN, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    p.add("body", id=PARLACEN, polity_id=DOMINICAN, kind="council")
    p.name(PARLACEN, "Dominican deputies to the Central American Parliament", "en", "official")
    p.name(PARLACEN, "Diputados al Parlamento Centroamericano (PARLACEN)", "es", "native", False)
    for mun, (_, text) in sorted(found.municipalities.items()):
        p.add("body", id=council("do.municipio", str(mun)), polity_id=DOMINICAN, kind="council")
        p.name(council("do.municipio", str(mun)), f"Concejo Municipal de {text}", "es", "official")
        p.add("office", id=mayor("do.municipio", str(mun)), polity_id=DOMINICAN, kind="mayor")
        p.name(mayor("do.municipio", str(mun)), f"Alcalde de {text}", "es", "official")
    for dm, (_, text) in sorted(found.districts.items()):
        p.add("body", id=council("do.distrito_municipal", str(dm)), polity_id=DOMINICAN, kind="council")
        p.name(council("do.distrito_municipal", str(dm)), f"Junta de Distrito Municipal de {text}", "es", "official")
        p.add("office", id=mayor("do.distrito_municipal", str(dm)), polity_id=DOMINICAN, kind="mayor")
        p.name(mayor("do.distrito_municipal", str(dm)), f"Director del Distrito Municipal de {text}", "es", "official")
    for name, (rules, method) in SYSTEMS.items():
        p.add("electoral_system", id=system(name), polity_id=DOMINICAN, rules=rules, rules_version=1)
        p.add(
            "note", id=key("note", system(name)), entity_id=system(name), kind="methodology", body_md=method, lang="en"
        )
    names = party_names()
    for code, label in sorted(used_parties().items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=DOMINICAN)
        full = names.get(code)
        if full is not None:
            p.name(id, title(full), "es", "official")
            p.name(id, label, "es", "abbreviation", False)
        else:
            p.name(id, label, "es", "official")
    return p


# --- National summaries.


def _int(text: str) -> int:
    return int(text.replace(",", "").split(".")[0])


@functools.cache
def summary_pdf(url: str) -> tuple[dict[str, int], list[tuple[str, str, int]]]:
    """The accounting totals and the per-party votes a definitive national summary prints, alliance totals left out."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    totals: dict[str, int] = {}
    for label, field in (
        ("TOTAL DE INSCRITOS", "registered"),
        ("VOTOS VALIDOS", "valid"),
        ("VOTOS NULOS", "null"),
        ("VOTOS OBSERVADOS", "observed"),
        ("VOTOS EMITIDOS", "cast"),
    ):
        found = re.search(rf"{label}\s*:\s*([\d,]+)", text)
        if found is None:
            raise ValueError(f"{url}: no {label}")
        totals[field] = _int(found[1])
    parties: list[tuple[str, str, int]] = []
    for line in text.splitlines():
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) < 4 or not re.fullmatch(r"[\d.]+\s*%", cells[-1]) or not re.fullmatch(r"[\d,]+", cells[-2]):
            continue
        name = re.sub(r"^\d\d\s+", "", cells[0])
        if name.endswith("Y ALIADOS") or not re.search(r"[A-Z]", cells[-3]):
            continue
        parties.append((name, sigla(cells[-3]), _int(cells[-2])))
    return totals, parties


@functools.cache
def local_relation(url: str, unit: str) -> dict[int, tuple[dict[str, int], dict[str, int]]]:
    """A definitive table of 2020 by municipality or municipal district: its accounting figures and each party's votes.

    A page's alliance lines, which print the sum of their members, are left out; a municipality's circumscription
    pages are left out as well.
    """
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    label = "Municipio" if unit == "mun" else "Distrito Municipal"
    found: dict[int, tuple[dict[str, int], dict[str, int]]] = {}
    for page in text.split("\f"):
        if "RELACION GENERAL DEFINITIVA" not in page or re.search(r"Circunscripci\S+\s*:", page):
            continue
        place = re.search(rf"{label}\s*:\s*(\d+)", page)
        if place is None:
            raise ValueError(f"{url}: a page without a {label.lower()}: {page[:300]!r}")
        figures: dict[str, int] = {}
        for text_label, field in RELATION_LABELS:
            number = re.search(rf"{text_label}\s*:\s*([\d,]+)", page)
            if number is None:
                raise ValueError(f"{url} {place[1]}: no {text_label}")
            figures[field] = _int(number[1])
        lines: list[tuple[int, str, int]] = []
        started = False
        for line in page.splitlines():
            if "PARTIDOS POLITICOS" in line:
                started = True
                continue
            cells = re.split(r"\s{2,}", line.strip())
            if (
                started
                and len(cells) == 4
                and re.fullmatch(r"[\d.]+\s*%", cells[3])
                and re.fullmatch(r"[\d,]+", cells[2])
            ):
                lines.append((len(line) - len(line.lstrip()), sigla(cells[1]), _int(cells[2])))
        if not lines:
            raise ValueError(f"{url} {place[1]}: no party lines")
        parties = _members(lines, f"{url} {place[1]}")
        if sum(parties.values()) != figures["valid"]:
            raise ValueError(f"{url} {place[1]}: parties sum to {sum(parties.values())}, valid {figures['valid']}")
        if int(place[1]) in found:
            raise ValueError(f"{url}: two pages for {place[1]}")
        found[int(place[1])] = (figures, parties)
    return found


def _members(lines: list[tuple[int, str, int]], where: str) -> dict[str, int]:
    """Each party's own votes from indented lines: an alliance's line is followed by its more deeply indented members."""
    floor = min(indent for indent, _, _ in lines)
    votes: dict[str, int] = {}
    index = 0
    while index < len(lines):
        indent, code, total = lines[index]
        members: list[tuple[str, int]] = []
        index += 1
        while index < len(lines) and lines[index][0] > floor:
            members.append((lines[index][1], lines[index][2]))
            index += 1
        if indent != floor:
            raise ValueError(f"{where}: {code} is indented without an alliance")
        for member, value in members or [(code, total)]:
            if member in votes:
                raise ValueError(f"{where}: {member} printed twice")
            votes[member] = value
    return votes


@functools.cache
def local_summary(url: str) -> tuple[dict[str, int], dict[str, int]]:
    """The national accounting figures and each party's votes of the Board's consolidated summary of 2020."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    totals: dict[str, int] = {}
    for label, field in (
        ("ELECTORES INSCRITOS", "registered"),
        ("VOTOS EMITIDOS", "cast"),
        ("VOTOS V[ÁA]LIDOS", "valid"),
        ("VOTOS NULOS", "null"),
    ):
        found = re.search(rf"^\s*{label}\s+([\d,]+)", text, flags=re.MULTILINE)
        if found is None:
            raise ValueError(f"{url}: no {label}")
        totals[field] = _int(found[1])
    parties: dict[str, int] = {}
    section = text[text.index("VOTOS VÁLIDOS POR PARTIDOS") : text.index("CARGOS OBTENIDOS")]
    for line in section.splitlines():
        found = re.match(r"^ {0,12}([A-Z][A-Z. ]*?)\s{2,}([\d,]+)\s+[\d.]+%", line)
        if found is None or found[1].startswith("VOTOS") or found[1].endswith(" ALIADOS"):
            continue
        code = sigla(found[1])
        if code in parties:
            raise ValueError(f"{url}: {code} printed twice")
        parties[code] = _int(found[2])
    return totals, parties


def _bulletin_text(url: str) -> str:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ")
    return " ".join(text.split())


@functools.cache
def summary_2004() -> tuple[dict[str, int], list[tuple[str, str, int]]]:
    text = _bulletin_text(f"{BULLETINS}/2004/Boletines2004/TotalN.asp?boletin=10")
    head = re.search(r"VOTOS EMITIDOS : ([\d,]+) ([\d,]+) ([\d,]+) ([\d,]+)", text)
    roll = re.search(r"COLEGIOS FALTANTES : ([\d,]+) ([\d,]+)", text)
    if head is None or roll is None:
        raise ValueError("2004 bulletin: no totals")
    totals = {
        "registered": _int(roll[1]),
        "valid": _int(head[1]),
        "null": _int(head[2]),
        "observed": _int(head[3]),
        "cast": _int(head[4]),
    }
    body = text[text.index("TOTAL NACIONAL") :]
    parties = [
        (name.strip(), sigla(code), _int(votes))
        for votes, name, code in re.findall(
            r"([\d,]+) [\d.]+ \d\d ([A-ZÑ ]+?) ([A-Z]+)(?= [\d,]+ [\d.]+ \d\d | ?$)", body
        )
        if not name.strip().endswith("Y ALIADOS")
    ]
    return totals, parties


@functools.cache
def summary_2000() -> tuple[dict[str, int], list[tuple[str, str, int]]]:
    text = _bulletin_text(f"{BULLETINS}/2000/resultadopresidencialdel2000porprovincia.htm")
    header = re.search(r"VOTOS NULOS ((?:[A-Z]+ )+?)AZUA", text)
    if header is None:
        raise ValueError("2000 bulletin: no party header")
    codes = header[1].replace("PUD O UD", "PUD_O_UD").split()
    tail = text.split()[-(5 + len(codes)) :]
    if not all(re.fullmatch(r"[\d,]+", n) for n in tail):
        raise ValueError(f"2000 bulletin: no national row in {tail}")
    _, registered, cast, valid, null, *votes = [_int(n) for n in tail]
    if len(votes) != len(codes):
        raise ValueError(f"2000 bulletin: {len(codes)} parties, {len(votes)} numbers")
    totals = {"registered": registered, "cast": cast, "valid": valid, "null": null}
    return totals, [(code, sigla(code.replace("_", " ")), v) for code, v in zip(codes, votes, strict=True)]


def declared_totals(d: Day, level: str) -> tuple[dict[str, int], dict[str, int]] | None:
    for kind, url, suffix in d.totals:
        if kind != level or kind in LOCAL_SUMMARIES:
            continue
        if d.day.year == 2000:
            totals, parties = summary_2000()
        elif d.day.year == 2004:
            totals, parties = summary_2004()
        elif suffix == ".pdf":
            totals, parties = summary_pdf(url)
        else:
            return None
        return totals, {code: votes for _, code, votes in parties}
    return None


# --- Contests.


class Contest(NamedTuple):
    key: str
    kind: str
    text: str
    slug: str
    area: str
    system: str
    office: str | None
    body: str | None
    option_kind: str
    level: str
    rows: pl.DataFrame
    parties: tuple[tuple[str, str], ...]
    seats: dict[str, int] | None = None


def _slugs(texts: dict[int, str]) -> dict[int, str]:
    plain = {k: changeset.slug(v) for k, v in texts.items()}
    counts_ = Counter(plain.values())
    return {k: v if counts_[v] == 1 else f"{v}-{k}" for k, v in plain.items()}


def contests(d: Day) -> list[Contest]:
    found = places()
    prov_slug = _slugs(found.provinces)
    mun_slug = _slugs({m: t for m, (_, t) in found.municipalities.items()})
    dm_slug = _slugs({m: t for m, (_, t) in found.districts.items()})
    out: list[Contest] = []
    for s in d.sheets:
        c = local_count() if s.level == "local" else count(s)
        rows = c.rows
        home = rows.filter(pl.col("where") == "home")
        if s.level == "president":
            out.append(
                Contest(
                    "president",
                    "office",
                    "Presidente de la República",
                    "presidente",
                    COUNTRY,
                    system("president"),
                    PRESIDENCY,
                    None,
                    "ticket",
                    s.level,
                    rows,
                    c.parties,
                )
            )
        if s.level in ("congress", "senate"):
            for (prov,), part in sorted(home.partition_by("prov", as_dict=True).items()):
                assert isinstance(prov, int)
                out.append(
                    Contest(
                        f"senate:{prov:02d}",
                        "body_seats",
                        f"Senador, {found.provinces[prov]}",
                        f"senador-{prov_slug[prov]}",
                        area("do.provincia", f"{prov:02d}"),
                        system("senate"),
                        None,
                        SENATE,
                        "list",
                        s.level,
                        part,
                        c.parties,
                    )
                )
        if s.level in ("congress", "deputies"):
            for (prov, circ), part in sorted(home.partition_by("prov", "circ", as_dict=True).items()):
                assert isinstance(prov, int) and isinstance(circ, int)
                out.append(
                    Contest(
                        f"deputies:{prov:02d}:{circ}",
                        "list_allocation",
                        f"Diputados, {found.circs[(prov, circ)]}",
                        f"diputados-{prov_slug[prov]}-{circ}",
                        area("do.circunscripcion", f"{prov:02d}:{circ}"),
                        system("deputies"),
                        None,
                        CHAMBER,
                        "list",
                        s.level,
                        part,
                        c.parties,
                    )
                )
        if s.level in ("congress", "abroad"):
            for (circ,), part in sorted(
                rows.filter(pl.col("where") == "abroad").partition_by("circ", as_dict=True).items()
            ):
                assert isinstance(circ, int)
                out.append(
                    Contest(
                        f"abroad:{circ}",
                        "list_allocation",
                        f"Diputados, circunscripción exterior {circ}",
                        f"diputados-exterior-{circ}",
                        area("do.circunscripcion", f"exterior:{circ}"),
                        system("deputies"),
                        None,
                        CHAMBER,
                        "list",
                        s.level,
                        part,
                        c.parties,
                    )
                )
        if s.level == "municipal":
            ballot = home.with_columns(seat=pl.coalesce(pl.col("dm"), -pl.col("mun")))
            for (seat,), part in sorted(ballot.partition_by("seat", as_dict=True).items()):
                assert isinstance(seat, int)
                if seat < 0:
                    mun = -seat
                    text = f"Boleta municipal, {found.municipalities[mun][1]}"
                    slug, scheme, code = f"municipal-{mun_slug[mun]}", "do.municipio", str(mun)
                else:
                    mun = found.districts[seat][0]
                    text = f"Boleta del distrito municipal, {found.districts[seat][1]}"
                    slug, scheme, code = f"distrito-municipal-{dm_slug[seat]}", "do.distrito_municipal", str(seat)
                out.append(
                    Contest(
                        f"municipal:{scheme}:{code}",
                        "list_allocation",
                        text,
                        slug,
                        area(scheme, code),
                        system("municipal"),
                        None,
                        council(scheme, code),
                        "list",
                        s.level,
                        part.drop("seat"),
                        c.parties,
                    )
                )
        if s.level in ("local", "mayors"):
            out.extend(_local_contests(c, found, mun_slug, dm_slug, s.level))
    if d.key == DAY_2020:
        out.extend(_list_contests(found, mun_slug, dm_slug))
    keys = Counter(contest.key for contest in out)
    slugs = Counter(contest.slug for contest in out)
    if max(keys.values()) > 1 or max(slugs.values()) > 1:
        raise ValueError(f"{d.key}: repeated contest keys or slugs")
    return out


def _list_contests(found: Places, mun_slug: dict[int, str], dm_slug: dict[int, str]) -> list[Contest]:
    """The council and district-member contests of 2020, a row for each ballot's total, from the seat tables."""
    out: list[Contest] = []
    for b in ballots_2020():
        if b.level == "council":
            several = b.circ is not None
            text = found.municipalities[b.mun][1]
            code = f"{b.mun}:{b.circ}" if several else str(b.mun)
            grain = f"mc:{code}" if several else f"m:{b.mun}"
            contest_key = f"council:{code}"
            title_ = f"Regidores, {text}" + (f", circunscripción {b.circ}" if several else "")
            slug = f"regidores-{mun_slug[b.mun]}" + (f"-{b.circ}" if several else "")
            where = area("do.circunscripcion_municipal", code) if several else area("do.municipio", str(b.mun))
            body = council("do.municipio", str(b.mun))
        else:
            assert b.dm is not None
            grain, contest_key = f"d:{b.dm}", f"vocales:{b.dm}"
            title_ = f"Vocales del distrito municipal, {found.districts[b.dm][1]}"
            slug = f"vocales-{dm_slug[b.dm]}"
            where = area("do.distrito_municipal", str(b.dm))
            body = council("do.distrito_municipal", str(b.dm))
        record: dict[str, object] = {
            "prov": b.prov,
            "prov_name": None,
            "mun": b.mun,
            "mun_name": None,
            "dm": b.dm,
            "dm_name": None,
            "circ": b.circ,
            "col": "",
            "letter": None,
            "level": b.level,
            **dict.fromkeys(MEASURES),
            **{f"p:{code}": v for code, v in b.votes.items()},
            "grain": grain,
            "adjustment": False,
            "where": "home",
        }
        rows = pl.DataFrame([record], infer_schema_length=None).with_columns(
            pl.col("prov", "mun", "dm", "circ", *MEASURES).cast(pl.Int64),
            pl.col("prov_name", "mun_name", "dm_name", "letter", "col", "level").cast(pl.String),
        )
        out.append(
            Contest(
                contest_key,
                "list_allocation",
                title_,
                slug,
                where,
                system("council"),
                None,
                body,
                "list",
                "lists",
                rows,
                tuple(b.labels.items()),
                b.seats,
            )
        )
    return out


def _local_contests(
    c: Count, found: Places, mun_slug: dict[int, str], dm_slug: dict[int, str], origin: str
) -> list[Contest]:
    out: list[Contest] = []
    rows = c.rows
    for (mun,), part in sorted(rows.filter(pl.col("level") == "mayor").partition_by("mun", as_dict=True).items()):
        assert isinstance(mun, int)
        text = found.municipalities[mun][1]
        out.append(
            Contest(
                f"mayor:{mun}",
                "office",
                f"Alcalde, {text}",
                f"alcalde-{mun_slug[mun]}",
                area("do.municipio", str(mun)),
                system("mayor"),
                mayor("do.municipio", str(mun)),
                None,
                "ticket",
                origin,
                part,
                c.parties,
            )
        )
    for (mun, circ), part in sorted(
        rows.filter(pl.col("level") == "council").partition_by("mun", "circ", as_dict=True).items()
    ):
        assert isinstance(mun, int) and isinstance(circ, int)
        text = found.municipalities[mun][1]
        several = (mun, circ) in found.city_circs
        out.append(
            Contest(
                f"council:{mun}:{circ}" if several else f"council:{mun}",
                "list_allocation",
                f"Regidores, {text}" + (f", circunscripción {circ}" if several else ""),
                f"regidores-{mun_slug[mun]}" + (f"-{circ}" if several else ""),
                area("do.circunscripcion_municipal", f"{mun}:{circ}") if several else area("do.municipio", str(mun)),
                system("council"),
                None,
                council("do.municipio", str(mun)),
                "list",
                origin,
                part,
                c.parties,
            )
        )
    for level, word, kind, rules in (
        ("director", "Director", "office", "mayor"),
        ("vocales", "Vocales", "list_allocation", "council"),
    ):
        for (dm,), part in sorted(rows.filter(pl.col("level") == level).partition_by("dm", as_dict=True).items()):
            assert isinstance(dm, int)
            code = str(dm)
            out.append(
                Contest(
                    f"{level}:{dm}",
                    kind,
                    f"{word} del distrito municipal, {found.districts[dm][1]}",
                    f"{level}-{dm_slug[dm]}",
                    area("do.distrito_municipal", code),
                    system(rules),
                    mayor("do.distrito_municipal", code) if kind == "office" else None,
                    council("do.distrito_municipal", code) if kind != "office" else None,
                    "ticket" if kind == "office" else "list",
                    origin,
                    part,
                    c.parties,
                )
            )
    return out


# --- Election packages.


def _units(p: Package, d: Day, contest_list: list[Contest]) -> None:
    found = places()
    rows = (
        pl.concat(
            [
                c.rows.select(
                    "prov", "prov_name", "mun", "mun_name", "dm", "col", "circ", "level", "adjustment", "where", "grain"
                )
                for c in contest_list
            ],
            how="vertical_relaxed",
        )
        .with_columns(code=unit_code(d))
        .sort("code", pl.col("dm").is_null())
        .unique("code", keep="first", maintain_order=True)
    )
    units: list[dict[str, object]] = []
    for code, where, adjustment, mun, dm, prov_name, mun_name, grain, prov, circ in rows.select(
        "code", "where", "adjustment", "mun", "dm", "prov_name", "mun_name", "grain", "prov", "circ"
    ).iter_rows():
        id = key("unit", code)
        if grain is not None:
            if grain.startswith("m:"):
                scheme, area_code = "do.municipio", str(mun)
            elif grain.startswith("mc:"):
                scheme, area_code = "do.circunscripcion_municipal", f"{mun}:{circ}"
            elif grain.startswith("d:"):
                scheme, area_code = "do.distrito_municipal", str(dm)
            elif grain.startswith("p:"):
                scheme, area_code = "do.provincia", f"{prov:02d}"
            else:
                scheme, area_code = (
                    "do.circunscripcion",
                    f"exterior:{circ}" if grain.startswith("x:") else f"{prov:02d}:{circ}",
                )
            units.append(
                {
                    "id": id,
                    "polity_id": DOMINICAN,
                    "scheme_code": scheme,
                    "code": code,
                    "area_relation": "equals",
                    "area_id": area(scheme, area_code),
                    "kind": "aggregate",
                }
            )
            continue
        if where == "home":
            place = area("do.distrito_municipal", str(dm)) if dm is not None else area("do.municipio", str(mun))
            if dm is not None and dm not in found.districts:
                raise ValueError(f"{code}: unknown municipal district {dm}")
        else:
            place = None
        units.append(
            {
                "id": id,
                "polity_id": DOMINICAN,
                "scheme_code": "do.colegio",
                "code": code,
                "area_relation": "within" if place else "unlocated",
                "area_id": place,
                "kind": "aggregate"
                if adjustment
                else {"home": "standard", "abroad": "abroad_mission", "prison": "prison"}[where],
            }
        )
        college = code.rsplit(":", 1)[1] if not adjustment else ADJUSTMENT
        if adjustment:
            within = (
                found.districts[dm][1]
                if dm is not None
                else found.municipalities[mun][1]
                if where == "home"
                else place_name(mun_name or "")
            )
            p.name(id, f"Distribución de nulos y observados, {within}", "es", "official")
        elif where != "home":
            p.name(
                id, f"Colegio {college}, {place_name(mun_name or '')}, {place_name(prov_name or '')}", "es", "official"
            )
    p.frame("reporting_unit", pl.DataFrame(units, infer_schema_length=None))


class Tally(NamedTuple):
    ballots_sum: int
    turnout: int
    votes_valid: int
    dropped: Counter[str]


def _values(c: Contest, d: Day, result_set: str, options: dict[str, str]) -> tuple[pl.DataFrame, Tally]:
    rows = c.rows.with_columns(
        unit_id=unit_code(d).map_elements(lambda code: key("unit", code), return_dtype=pl.String)
    )
    votes = rows.select("unit_id", *(pl.col(f"p:{code}").alias(options[code]) for code in options)).unpivot(
        index="unit_id", variable_name="option_id", value_name="value"
    )
    accounting = pl.concat(
        [
            rows.select(
                "unit_id",
                "adjustment",
                value=pl.col(field),
                measure=pl.lit(measure, pl.Int16),
            )
            for field, measure in MEASURES.items()
        ]
    ).filter(~(pl.col("adjustment") & pl.col("measure").is_in([REGISTERED, CAST])))
    dropped: Counter[str] = Counter()
    for field, measure in MEASURES.items():
        negative = accounting.filter((pl.col("measure") == measure) & (pl.col("value") < 0))
        if negative.height:
            dropped[field] += -int(negative["value"].sum())
    if (
        votes.filter(pl.col("value") < 0).height
        or accounting.filter((pl.col("value") < 0) & ~pl.col("adjustment")).height
    ):
        raise ValueError(f"{d.key} {c.key}: negative values outside the adjustment rows")
    frame = pl.concat(
        [
            votes.with_columns(measure=pl.lit(VOTES, pl.Int16)),
            accounting.filter(pl.col("value") >= 0).select(
                "unit_id", option_id=pl.lit(None, pl.String), value="value", measure="measure"
            ),
        ]
    ).filter(pl.col("value").is_not_null())
    wide = rows.select(
        "registered",
        "cast",
        "valid",
        "null",
        "adjustment",
        votes=pl.sum_horizontal(*(pl.col(f"p:{code}") for code in options)) if options else pl.lit(0),
    )
    real = wide.filter(~pl.col("adjustment"))
    ballots_sum = real.filter(
        pl.col("cast").is_not_null()
        & pl.col("valid").is_not_null()
        & pl.when(pl.col("null").is_null())
        .then(pl.col("valid") > pl.col("cast"))
        .otherwise(pl.col("valid") + pl.col("null") != pl.col("cast"))
    ).height
    turnout = real.filter(pl.col("cast") > pl.col("registered")).height
    checked = c.kind == "list_allocation" or c.kind == "office" or c.kind == "body_seats"
    votes_valid = (
        wide.filter(
            pl.col("valid").is_not_null()
            & (pl.col("valid").clip(lower_bound=0) != pl.col("votes").fill_null(0))
            & ~(pl.col("adjustment") & (pl.col("valid") < 0))
        ).height
        if checked
        else 0
    )
    return frame.with_columns(result_set_id=pl.lit(result_set)), Tally(ballots_sum, turnout, votes_valid, dropped)


class Winner(NamedTuple):
    contest: str
    party: str
    own_party: str | None
    name: str
    role: str
    sex: str | None
    born: date | None


def _alliance_lead(text: str) -> str:
    """The party that presented a list, from the Board's "PRM Y ALIADOS" spelling."""
    return sigla(re.sub(r"\s+Y\s+ALIADOS\s*$", "", text.strip(), flags=re.IGNORECASE))


def _circ(text: str | None) -> int:
    found = re.search(r"\d+", text or "")
    return max(int(found[0]), 1) if found else 1


def _sex(text: str | None) -> str | None:
    return {"M": "male", "F": "female"}.get((text or "").strip().upper())


def _born(text: str | None) -> date | None:
    found = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (text or "").strip())
    return date(int(found[3]), int(found[2]), int(found[1])) if found else None


def _post(cargo: str, prov: int | None, circ: str | None) -> tuple[str, str] | None:
    """The contest key and candidacy role of a winner's post, or None for posts without a contest here."""
    if prov is None and cargo in ("SENADOR", "DIPUTADO"):
        raise ValueError(f"a {cargo.lower()} winner names no province")
    match cargo:
        case "PRESIDENTE":
            return "president", "head"
        case "VICEPRESIDENTE":
            return "president", "running_mate"
        case "SENADOR":
            return f"senate:{prov:02d}", "list_member"
        case "DIPUTADO":
            return f"deputies:{prov:02d}:{_circ(circ)}", "list_member"
        case "DIPUTADO DE ULTRAMAR" | "DIPUTADO EXTERIOR":
            return f"abroad:{_circ(circ)}", "list_member"
    return None


def winners(d: Day) -> tuple[list[Winner], Counter[str]]:
    """The Board's declared winners of the presidency, the Senate, and the Chamber, and the posts left out."""
    book = workbook(WINNERS[d.key])
    provinces = {fold(name): code for code, name in places().provinces.items()}
    out: list[Winner] = []
    left: Counter[str] = Counter()

    def rows(sheet: str) -> list[dict[str, str | None]]:
        frame = book.load_sheet_by_name(sheet, dtypes="string").to_polars()
        return [
            {k: (v.strip() if v is not None and v.strip() not in ("", "NULL") else None) for k, v in row.items()}
            for row in frame.iter_rows(named=True)
        ]

    def add(r: dict[str, str | None], cargo: str, columns: dict[str, str]) -> None:
        name = r[columns["name"]] or ""
        prov: int | None = None
        if columns.get("prov") and r[columns["prov"]]:
            prov = provinces.get(fold(r[columns["prov"]]))
            if prov is None:
                raise ValueError(f"{d.key}: winners file names an unknown province {r[columns['prov']]!r}")
        post = _post(cargo, prov, r[columns["circ"]] if "circ" in columns else None)
        if post is None:
            left[cargo.capitalize()] += 1
            return
        own = r[columns["own"]] if "own" in columns else None
        lead = r[columns["party"]] or ""
        out.append(
            Winner(
                post[0],
                _alliance_lead(lead),
                sigla(own) if own else None,
                name,
                post[1],
                _sex(r[columns["sex"]]) if "sex" in columns else None,
                _born(r[columns["born"]]) if "born" in columns else None,
            )
        )

    if d.key == "2024-05-19":
        party_column = "PARTIDO Y/O ALIANZAS DE PARTIDOS"
        for r in rows("PRESIDENTE"):
            add(r, r["CARGO"] or "", {"name": "NOMBRE", "party": party_column, "sex": "SEXO"})
        for r in rows("SENADO"):
            add(r, "SENADOR", {"name": "NOMBRE", "party": party_column, "sex": "SEXO", "prov": "PROVINCIA"})
        for r in rows("DIPUTADO"):
            add(
                r,
                "DIPUTADO",
                {"name": "NOMBRE", "party": party_column, "sex": "SEXO", "prov": "PROVINCIA", "circ": "CIRC"},
            )
        for r in rows("DIPUTADO EXTERIOR"):
            add(r, "DIPUTADO EXTERIOR", {"name": "NOMBRE", "party": party_column, "sex": "SEXO", "circ": "CIRC"})
        left["Diputado nacional"] += len(rows("DIPUTADO NACIONAL"))
        left["Parlacen"] += len(rows("PARLACEN"))
    elif d.key == "2020-07-05":
        for r in rows("Sheet1"):
            add(
                r,
                r["CARGO"] or "",
                {
                    "name": "NOMBRE COMPLETO",
                    "party": "SIGLAS",
                    "own": "PARTIDO DEL CANDIDATO",
                    "sex": "SEXO",
                    "born": "FECHA NACIMIENTO",
                    "prov": "PROVINCIA",
                    "circ": "CIRCUNSCRIPCION",
                },
            )
    elif d.key == "2016-05-15":
        for r in rows("Sheet2"):
            if r["NIVEL"] == "MUNICIPAL":
                left["Municipal posts"] += 1
                continue
            add(
                r,
                (r["CARGO"] or "").upper(),
                {
                    "name": "NOMBRE",
                    "party": "PARTIDO",
                    "own": "PARTIDO_CANDIDATO",
                    "prov": "PROVINCIA",
                    "circ": "CIRCUNSCRIPCION",
                },
            )
    return out, left


class LocalWinner(NamedTuple):
    contest: str
    party: str
    own_party: str | None
    name: str
    role: str
    sex: str | None
    position: int | None
    cargo: str


LOCAL_CARGOS = {
    "ALCALDE(SA)": "head",
    "DIRECTOR(A)": "head",
    "VICEALCALDE": "running_mate",
    "SUBDIRECTOR": "running_mate",
    "REGIDOR": "list_member",
    "VOCAL": "list_member",
    "SUPLENTE REGIDOR(A)": "substitute",
}
ELECTED_SHEETS = (
    (ELECTED_2020, ("Todas",)),
    (SUBSTITUTES_2020, ("VICEALCALDE y SUBDIRECTOR", "SUPLENTES REGIDORES")),
)


UNLISTED_NAMES = {
    "PARTIDO MOVIMIENTO DEMOCRATICO ALTERNATIVO": "MODA",
    "MOVIMIENTO INDEPENDIENTE UNIDAD Y PROGRESO": "MIUP",
}
"""Parties the elected files spell out whose full name no national summary prints."""


@functools.cache
def _spelled() -> dict[str, str]:
    return {fold(name): code for code, name in party_names().items()} | UNLISTED_NAMES


def acronym(text: str) -> str:
    """A party's acronym from the elected files, which spell most parties out in full."""
    lead = _alliance_lead(text)
    code = _spelled().get(fold(lead), lead)
    if code not in used_parties():
        raise ValueError(f"the elected files name {text!r}, which no results file counts")
    return code


def local_winners(found: list[Contest]) -> tuple[list[LocalWinner], Counter[str]]:
    """The elected mayors, directors, council members, and district members of 2020, with the vice mayors,
    subdirectors, and substitute councillors printed beside them, and the rows left out."""
    keys = {c.key for c in found}
    out: list[LocalWinner] = []
    left: Counter[str] = Counter()
    for url, sheets in ELECTED_SHEETS:
        book = workbook(url)
        for sheet in sheets:
            rows = book.load_sheet_by_name(sheet, dtypes="string", header_row=None).to_polars().rows()
            header = [(h or "").strip() for h in rows[2]]
            for values in rows[3:]:
                if not values[0]:
                    continue
                r = {h: (v.strip() if v and v.strip() not in ("", "NULL") else None) for h, v in zip(header, values)}
                cargo = r["CARGO"]
                if cargo is None:
                    left["Substitute slots printed without a candidate"] += 1
                    continue
                role = LOCAL_CARGOS[cargo]
                mun, dm, circ = int(r["MUN"] or 0), int(r["DM"] or 0), int(r["CIRC"] or 0)
                contest = {
                    "ALCALDE(SA)": f"mayor:{mun}",
                    "VICEALCALDE": f"mayor:{mun}",
                    "DIRECTOR(A)": f"director:{dm}",
                    "SUBDIRECTOR": f"director:{dm}",
                    "VOCAL": f"vocales:{dm}",
                }.get(cargo, f"council:{mun}:{circ}" if circ else f"council:{mun}")
                if contest not in keys:
                    left[f"{cargo.title()} of a contest with no results"] += 1
                    continue
                own = r["PARTIDO DEL CANDIDATO"]
                position = r.get("POSICION BOLETA")
                out.append(
                    LocalWinner(
                        contest,
                        acronym(r["PARTIDO ENCABEZA"] or ""),
                        acronym(own) if own else None,
                        r["NOMBRE"] or "",
                        role,
                        _sex(r["SEXO"]),
                        int(position) if position and int(position) else None,
                        cargo,
                    )
                )
    return out, left


def _elected_local(
    p: Package, c: Contest, result_set: str, options: dict[str, str], found: list[LocalWinner]
) -> Counter[str]:
    """Stages the elected of one local contest as people, candidacies, and declared outcomes; the seats a party won
    on a council are stored once, from the Board's seat table. Returns the council members named for each party."""
    count: Counter[tuple[str, str]] = Counter()
    for w in found:
        if w.party not in options:
            raise ValueError(f"{DAY_2020} {c.key}: {w.cargo} {w.name} of {w.party} has no option")
        count[w.party, w.cargo] += 1
        tag = f"{DAY_2020}:{c.key}:{w.cargo}:{w.party}:{count[w.party, w.cargo]}"
        person, candidacy = key("person", tag), key("candidacy", tag)
        p.add("person", id=person, gender=w.sex)
        p.name(person, title(w.name), "es", "official")
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[w.party],
            role=w.role,
            list_position=w.position,
            ballot_name=title(w.name),
            member_of_party_id=party(w.own_party) if w.own_party else None,
            is_independent=False,
        )
        if c.option_kind == "list" and w.role == "list_member":
            p.add(
                "outcome", result_set_id=result_set, option_id=options[w.party], candidacy_id=candidacy, kind="elected"
            )
    if c.option_kind == "ticket":
        # The ticket of a mayor or director wins as a whole, as a presidential ticket does.
        for code in {w.party for w in found if w.role == "head"}:
            p.add("outcome", result_set_id=result_set, option_id=options[code], kind="elected", seats=1)
    return Counter({code: n for (code, cargo), n in count.items() if cargo in ("REGIDOR", "VOCAL")})


def _elected(p: Package, d: Day, c: Contest, result_set: str, options: dict[str, str], found: list[Winner]) -> None:
    """Stages the Board's winners of one contest as people, candidacies, and declared outcomes."""
    seats: Counter[str] = Counter()
    for w in found:
        if w.party not in options:
            raise ValueError(f"{d.key} {c.key}: winner {w.name} of {w.party} has no option")
        option = options[w.party]
        tag = f"{d.key}:{c.key}:{w.role}:{w.party}:{seats[w.party] + 1}"
        person = key("person", tag)
        candidacy = key("candidacy", tag)
        p.add("person", id=person, gender=w.sex, birth_date=w.born, birth_precision="day" if w.born else None)
        p.name(person, title(w.name), "es", "official")
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role=w.role,
            ballot_name=title(w.name),
            member_of_party_id=party(w.own_party) if w.own_party else None,
            is_independent=False,
        )
        if c.key != "president":
            seats[w.party] += 1
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
    if c.key == "president":
        lead = {w.party for w in found}
        if len(lead) != 1:
            raise ValueError(f"{d.key}: the winning ticket names several parties")
        p.add("outcome", result_set_id=result_set, option_id=options[lead.pop()], kind="elected", seats=1)
        return
    for code, n in seats.items():
        p.add("outcome", result_set_id=result_set, option_id=options[code], kind="won", seats=n)


def election(d: Day) -> Package:
    p = Package(
        f"{SOURCE}/{d.key}",
        SOURCE,
        f"The Dominican Republic's elections of {d.day:%-d %B %Y} by electoral college from the Junta Central "
        "Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {DOMINICAN, COUNTRY}
    cited: set[str] = set()
    for s in d.sheets:
        if s.url not in cited:
            p.artifact(raw.cached(SOURCE, s.url), SOURCE_ID)
            cited.add(s.url)
    for _, url, _ in d.totals:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    if d.key == DAY_2020:
        for url in LISTS_2020:
            p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    elected: dict[str, list[Winner]] = {}
    if d.key in WINNERS:
        p.artifact(raw.cached(SOURCE, WINNERS[d.key]), SOURCE_ID)
        for w in winners(d)[0]:
            elected.setdefault(w.contest, []).append(w)
    local: dict[str, list[LocalWinner]] = {}
    if d.key == DAY_2020:
        for url in ELECTED_FILES_2020:
            p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    election_id = key("election", d.key)
    p.add(
        "election",
        id=election_id,
        polity_id=DOMINICAN,
        kind=d.kind,
        date_from=d.day,
        date_to=d.day,
        status="held",
    )
    p.name(election_id, d.english, "en", "official")
    p.name(election_id, d.spanish, "es", "native", False)
    base = f"do/{d.key}-{d.word}"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    found = contests(d)
    if d.key == DAY_2020:
        listed, skipped = local_winners(found)
        for w in listed:
            local.setdefault(w.contest, []).append(w)
        print(f"{d.key}: {len(listed)} elected and substitutes; left out {dict(skipped)}", flush=True)
    _units(p, d, found)
    values: list[pl.DataFrame] = []
    tallies: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    declared_diffs: list[str] = []
    for c in found:
        contest = key("contest", d.key, c.key)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind=c.kind,
            office_id=c.office,
            body_id=c.body,
            district_area_id=c.area,
            magnitude=1 if c.kind in ("office", "body_seats") else None,
            round_number=1,
            electoral_system_id=c.system,
            default_result_kind="counted",
        )
        p.name(contest, c.text, "es", "official")
        p.add("slug", path=f"{base}/{c.slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", d.key, c.key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        adjusted = bool(c.rows["adjustment"].any())
        method = (
            (LISTS if c.level == "lists" else RELATION if c.rows["grain"].is_not_null().any() else METHOD)
            + (ADJUSTED if adjusted else "")
            + (CONGRESS if c.level == "congress" else "")
            + (MUNICIPAL if c.level == "municipal" else "")
            + (LOCAL if c.level == "local" else "")
            + (MAYORS if c.level == "mayors" else "")
            + (NO_NAME_2008 if d.day.year == 2008 else "")
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options: dict[str, str] = {}
        sums = c.rows.select(*(pl.col(f"p:{code}").sum().alias(code) for code, _ in c.parties)).row(0, named=True)
        position = 0
        for code, label in c.parties:
            if not sums[code]:
                continue
            position += 1
            option = options[code] = key("option", d.key, c.key, code)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=c.option_kind,
                party_id=party(code),
                ballot_position=position,
                ballot_label=label,
            )
        if c.key in elected:
            _elected(p, d, c, result_set, options, elected.pop(c.key))
        for code, n in (c.seats or {}).items():
            if n:
                p.add("outcome", result_set_id=result_set, option_id=options[code], kind="won", seats=n)
        if c.key in local:
            named = _elected_local(p, c, result_set, options, local.pop(c.key))
            if c.seats and {k: v for k, v in c.seats.items() if v} != dict(named):
                print(f"{d.key} {c.key}: elected {dict(named)} against seats {c.seats}", flush=True)
        frame, tally = _values(c, d, result_set, options)
        values.append(frame)
        tallies["ballots_sum"] += tally.ballots_sum
        tallies["turnout"] += tally.turnout
        tallies["votes_valid"] += tally.votes_valid
        dropped += tally.dropped
        if tally.dropped:
            parts = ", ".join(f"{n:,} {field}" for field, n in sorted(tally.dropped.items()))
            p.add(
                "note",
                id=key("note", result_set, "adjustment"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"The Board's distribution rows (college 9999) for this contest print {parts} ballots as "
                "negative adjustments, which are not stored, so the stored null and observed ballots exceed the "
                "Board's totals by those amounts.",
                lang="en",
            )
        printed = declared_totals(d, c.level) if c.key == "president" else _declared_local(d, c)
        if printed is not None:
            totals, parties = printed
            summed = frame.group_by("option_id", "measure").agg(pl.col("value").sum())
            got = {(o, m): v for o, m, v in summed.iter_rows()}
            for field, value in totals.items():
                p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES[field], value=value)
                if got.get((None, MEASURES[field])) != value:
                    declared_diffs.append(f"{field} printed {value:,}, files {got.get((None, MEASURES[field])) or 0:,}")
            for code, value in parties.items():
                if code not in options:
                    if value:
                        declared_diffs.append(f"{code} printed {value:,} with no column in the file")
                    continue
                p.add("declared", result_set_id=result_set, option_id=options[code], measure=VOTES, value=value)
                if got.get((options[code], VOTES)) != value:
                    declared_diffs.append(f"{code} printed {value:,}, files {got.get((options[code], VOTES)) or 0:,}")
    from ea.sources.do import jce_parlacen

    if d.key in jce_parlacen.DAYS:
        _, parlacen_values = jce_parlacen.add(p, d, election_id, base)
        print(f"{d.key}: PARLACEN contest with {parlacen_values} party totals", flush=True)
    if local:
        print(f"{d.key}: left out, elected of contests with no results: {sorted(local)}", flush=True)
    if elected:
        # The 2016 congressional file prints no ballots cast abroad, so those deputies have no contest to join.
        print(f"{d.key}: left out, winners of contests with no results: {sorted(elected)}", flush=True)
    facts = pl.concat(values)
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    _accept(
        p,
        tallies,
        dropped,
        declared_diffs,
        "figures the Board prints for its mayors' and directors' tables"
        if d.kind == "local"
        else "national figures the Board prints for the presidency",
    )
    print(
        f"{d.key}: {len(found)} contests, {facts.height:,} values, {dict(tallies)}, dropped {dict(dropped)}", flush=True
    )
    return p


def _declared_local(d: Day, c: Contest) -> tuple[dict[str, int], dict[str, int]] | None:
    """The figures the Board prints for one mayor's or director's ballot, with each party's own votes."""
    if c.level != "mayors":
        return None
    level, number = c.key.split(":")
    kind = f"{level}s"
    url = next(url for k, url, _ in d.totals if k == kind)
    figures, parties = local_relation(url, "mun" if level == "mayor" else "dm")[int(number)]
    return figures, parties


def _accept(
    p: Package,
    tallies: Counter[str],
    dropped: Counter[str],
    declared: list[str],
    what: str = "national figures the Board prints for the presidency",
) -> None:
    if tallies["ballots_sum"]:
        p.accept(
            "accounting.ballots_sum",
            f"{tallies['ballots_sum']:,} college rows print ballots cast that differ from valid plus null ballots; "
            "the Board counts observed (objected) ballots in ballots cast apart from both, a measure the check does "
            "not add, and the figures are stored as printed",
        )
    if tallies["turnout"]:
        p.accept(
            "accounting.turnout",
            f"{tallies['turnout']:,} college rows print more ballots cast than registered voters, since colleges also "
            "receive the votes of their table officials and of voters added to the roll; stored as printed",
        )
    if tallies["votes_valid"]:
        p.accept(
            "accounting.votes_valid",
            f"{tallies['votes_valid']:,} college rows print a valid-votes figure that differs from the sum of the party "
            "columns; the columns and the figure are stored as printed",
        )
    if declared:
        shown = "; ".join(declared[:12]) + ("; and more" if len(declared) > 12 else "")
        p.accept(
            "accounting.declared",
            f"{len(declared)} {what} differ from the sum of its college "
            f"rows ({shown}); both are stored as published"
            + (
                ", and the negative null and observed adjustments of its distribution rows are not stored"
                if dropped
                else ""
            ),
        )


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d.key}" for d in DAYS)]


def compare() -> list[str]:
    """Each level's national sums from the college files against the totals the Board prints."""
    report: list[str] = []
    for d in DAYS:
        for s in d.sheets:
            printed = declared_totals(d, s.level)
            if printed is None:
                continue
            c = local_count() if s.level == "local" else count(s)
            rows = c.rows
            sums = rows.select(
                *(pl.col(f).filter(pl.col(f) > 0).sum().alias(f) for f in MEASURES),
                *(pl.col(f"p:{code}").sum().alias(code) for code, _ in c.parties),
            ).row(0, named=True)
            totals, parties = printed
            diffs = [
                f"{field} {value:,} vs {sums[field] or 0:,}" for field, value in totals.items() if sums[field] != value
            ]
            diffs += [
                f"{code} {value:,} vs {sums.get(code) or 0:,}"
                for code, value in parties.items()
                if sums.get(code) != value
            ]
            matched = sum(1 for code, value in parties.items() if sums.get(code) == value)
            report.append(
                f"{d.key} {s.level}: valid printed {totals['valid']:,}, files {sums['valid'] or 0:,}; "
                f"{matched} of {len(parties)} party totals match; differences (printed vs files): "
                f"{'; '.join(diffs) if diffs else 'none'}"
            )
    return report


def compare_local() -> list[str]:
    """The 2024 municipal files' sums for mayors and district directors against the Board's summary of the next day."""
    d = next(d for d in DAYS if d.key == LOCAL_2024)
    url = next(url for level, url, _ in d.totals if level == "local")
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    printed: dict[str, tuple[int, int]] = {}
    for label, field in (
        ("Votos Emitidos", "cast"),
        ("Votos validos", "valid"),
        ("Votos nulos", "null"),
        ("Votos observados", "observed"),
    ):
        found = re.search(rf"^{label}\s+([\d,.]+)\s+([\d,.]+)", text, flags=re.MULTILINE)
        if found is None:
            raise ValueError(f"{url}: no {label}")
        printed[field] = (_int(found[1]), _int(found[2]))
    missing = re.search(r"^Colegios faltantes\s+(\d+)\s+(\d+)", text, flags=re.MULTILINE)
    rows = local_count().rows
    report: list[str] = []
    for index, level in enumerate(("mayor", "director")):
        sums = rows.filter(pl.col("level") == level).select(*(pl.col(f).sum() for f in printed)).row(0, named=True)
        diffs = [f"{f} {v[index]:,} vs {sums[f]:,}" for f, v in printed.items() if v[index] != sums[f]]
        report.append(
            f"{d.key} {level}: the Board's summary of 19 February 2024 "
            f"({missing[index + 1] if missing else '?'} colleges still missing) vs the college file: "
            f"{'; '.join(diffs) if diffs else 'all equal'}"
        )
    return report


def compare_2020() -> list[str]:
    """The college file's net sums, with the Board's adjustment rows, for each ballot against its printed tables and summary."""
    d = next(d for d in DAYS if d.key == "2020-03-15")
    c = count(d.sheets[0])
    url = {kind: url for kind, url, _ in d.totals}
    rows = c.rows
    report: list[str] = []
    for level, unit, place in (("mayor", "mun", "mun"), ("director", "dm", "dm")):
        printed = local_relation(url[f"{level}s"], unit)
        ballots = c.rows.filter(pl.col("level") == level)
        sums = {
            key_[0]: row
            for key_, row in (
                (
                    k,
                    part.select(
                        *(pl.col(f).sum().alias(f) for f in MEASURES),
                        *(pl.col(f"p:{code}").sum().alias(code) for code, _ in c.parties),
                    ).row(0, named=True),
                )
                for k, part in ballots.partition_by(place, as_dict=True).items()
            )
        }
        diffs = [f"{level} {k}: no table" for k in sorted(set(sums) - set(printed))]
        diffs += [f"{level} {k}: no college rows" for k in sorted(set(printed) - set(sums))]
        for k in sorted(set(sums) & set(printed)):
            figures, parties = printed[k]
            diffs += [
                f"{level} {k} {field}: printed {value:,}, files {sums[k][field] or 0:,}"
                for field, value in figures.items()
                if sums[k][field] != value
            ]
            diffs += [
                f"{level} {k} {code}: printed {parties.get(code, 0):,}, files {sums[k].get(code) or 0:,}"
                for code in sorted(set(parties) | {c_ for c_, _ in c.parties})
                if (sums[k].get(code) or 0) != parties.get(code, 0)
            ]
        report.append(
            f"{d.key} {level}s: {len(set(sums) & set(printed))} of {len(printed)} printed tables compared with the "
            f"college file; differences: {'; '.join(diffs[:20]) if diffs else 'none'}"
            + (f" and {len(diffs) - 20} more" if len(diffs) > 20 else "")
        )
    totals, parties = local_summary(url["local"])
    sums = rows.select(
        *(pl.col(f).sum().alias(f) for f in MEASURES),
        *(pl.col(f"p:{code}").sum().alias(code) for code, _ in c.parties),
    ).row(0, named=True)
    diffs = [f"{field} printed {v:,}, files {sums[field] or 0:,}" for field, v in totals.items() if sums[field] != v]
    diffs += [
        f"{code} printed {v:,}, files {sums.get(code) or 0:,}" for code, v in parties.items() if sums.get(code) != v
    ]
    matched = sum(1 for code, v in parties.items() if sums.get(code) == v)
    report.append(
        f"{d.key} national: valid printed {totals['valid']:,}, files {sums['valid'] or 0:,}; {matched} of "
        f"{len(parties)} party totals match; differences: {'; '.join(diffs) if diffs else 'none'}"
    )
    return report


def compare_lists() -> list[str]:
    """The seats of the 2020 council and district-member tables against the national seats the Board's summary prints."""
    url = next(url for kind, url, _ in next(d for d in DAYS if d.key == DAY_2020).totals if kind == "local")
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    block = text[text.index("REGIDORES/AS") : text.index("REGIDORES Y VOCALES ELECTOS")]
    printed: dict[str, dict[str, int]] = {"council": {}, "vocales": {}}
    for line in block.splitlines():
        cells = re.findall(r"([A-Z][A-Z. ]*?)\s{2,}([\d,]+)(?=\s|$)", line)
        if len(cells) == 2:
            for level, (label, n) in zip(("council", "vocales"), cells, strict=True):
                code = _lead_code(label)[0]
                printed[level][code] = printed[level].get(code, 0) + _int(n)
    report: list[str] = []
    for level in ("council", "vocales"):
        sums: Counter[str] = Counter()
        for b in ballots_2020():
            if b.level == level:
                for code, n in b.seats.items():
                    if n:
                        sums[code] += n
        total = printed[level].pop("TOTAL")
        diffs = [
            f"{label} printed {printed[level].get(label, 0):,}, tables {sums.get(label, 0):,}"
            for label in sorted(set(sums) | set(printed[level]))
            if sums.get(label, 0) != printed[level].get(label, 0)
        ]
        report.append(
            f"{DAY_2020} {level} seats: summary prints {total:,}, tables {sum(sums.values()):,} to {len(sums)} ballot "
            f"lines; differences: {'; '.join(diffs) if diffs else 'none'}"
        )
    return report


def parse() -> None:
    for line in compare_lists():
        print(line, flush=True)
    for line in compare_local():
        print(line, flush=True)
    for line in compare_2020():
        print(line, flush=True)
    for line in compare():
        print(line, flush=True)
    from ea.sources.do import jce_parlacen

    for day in jce_parlacen.DAYS:
        for line in jce_parlacen.check(day):
            print(line, flush=True)
    geo().write()
    parties = registry()
    held = {str(row["id"]) for row in parties.rows["party"]}
    built = [election(d) for d in DAYS]
    for p in built:
        for table, column in (("contest_option", "party_id"), ("candidacy", "member_of_party_id")):
            missing = {str(row[column]) for row in p.rows.get(table, []) if row.get(column)} - held
            if missing:
                raise ValueError(f"{p.key}: {table} names parties the registry lacks: {sorted(missing)}")
    parties.write()
    for p in built:
        p.write()


def emit() -> None:
    package.run("load", packages())
