"""Elections to the Cortes de Castilla y León, 1987 to 2026, by province, municipality, and polling station.

The province results and seats come from the Junta Electoral de Castilla y León's results notices, as the Junta
Electoral Central republishes them: a model read of each page, deduplicated, with the cells the read lost or misplaced
transcribed from the page image (`TRANSCRIBED`, `FIXES`), and every province's lists checked against the votes to
candidacies the notice prints. The procuradores elected come from the same notices for 2019, 2022, and 2026, the
only ones that name them. The municipality and polling-station results come from the Junta de Castilla y León's
open-data file, each of its party labels paired with the notice's list for that province. The 1983 election, whose
results survive only as each province's scrutiny record in the Boletín Oficial del Estado, is not built yet.
"""

import base64
import io
import json
import re
import subprocess
import unicodedata
from collections import Counter
from datetime import date
from uuid import uuid5

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA, connect

SOURCE = "es.cyl"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://analisis.datosabiertos.jcyl.es/api/explore/v2.1/catalog/datasets/"
RESULTS = API + "resultados-electorales-1983-actualidad/exports/csv?delimiter=%3B"
BASICS = API + "datos-basicos-de-elecciones-a-las-cortes/exports/csv?delimiter=%3B"
# The datasets' catalog records, which give their license (CC BY 4.0).
METADATA = (API + "resultados-electorales-1983-actualidad", API + "datos-basicos-de-elecciones-a-las-cortes")
JEC = "https://www.juntaelectoralcentral.es/cs/jec/documentos/"
PROVINCES = {
    "05": "Ávila",
    "09": "Burgos",
    "24": "León",
    "34": "Palencia",
    "37": "Salamanca",
    "40": "Segovia",
    "42": "Soria",
    "47": "Valladolid",
    "49": "Zamora",
}
ELECTIONS = {
    1983: (date(1983, 5, 8), "CASTILLA_Y_LEON_1983_Resultados.pdf", "BOE 258, 28 October 1983"),
    1987: (date(1987, 6, 10), "CASTILLA_Y_LEON_1987_Resultados.pdf", "BOCyL"),
    1991: (date(1991, 5, 26), "CASTILLA_Y_LEON_1991_Resultados.pdf", "BOCyL 126, 3 July 1991"),
    1995: (date(1995, 5, 28), "CASTILLA_Y_LEON_1995_Resultados.pdf", "BOCyL"),
    1999: (date(1999, 6, 13), "CASTILLA_Y_LEON_1999_Resultados.pdf", "BOCyL"),
    2003: (date(2003, 5, 25), "CASTILLA_Y_LEON_2003_Resutados.pdf", "BOCyL"),
    2007: (date(2007, 5, 27), "CASTILLA_Y_LEON_2007_Resultados.pdf", "BOCyL"),
    2011: (date(2011, 5, 22), "CASTILLA_Y_LEON_2011_Resultados.pdf", "BOCyL 153, 9 August 2011"),
    2015: (date(2015, 5, 24), "CASTILLA_Y_LEON_2015_Resultados.pdf", "BOCyL 128, 6 July 2015"),
    2019: (date(2019, 5, 26), "CASTILLA_Y_LEON_2019_Resultados.pdf", "BOCyL 117, 20 June 2019"),
    2022: (date(2022, 2, 13), "BOCYL-D-09032022-13.pdf", "BOCyL 46, 9 March 2022"),
    2026: (date(2026, 3, 15), "CYL-2026-Resultados-definitivos-y-CERA.pdf", "BOCyL 80, 29 April 2026"),
}
LAST_PAGE = {2026: 10}
MODEL = "claude-haiku-5-5"
PROMPT = "1"
LONGEST = 1568
READ_SYSTEM = (
    "You read one page of an official Spanish bulletin (Boletín Oficial de Castilla y León, or the Boletín Oficial del "
    "Estado) that publishes the results of an election to the Cortes de Castilla y León, by province (circunscripción "
    "or Junta Electoral Provincial: Ávila, Burgos, León, Palencia, Salamanca, Segovia, Soria, Valladolid, Zamora). "
    "Copy only what is printed on this page; when a text layer is given with the image, prefer its digits and spelling "
    "where they are legible, and use the image to tell which column or row a figure belongs to. Write numbers exactly "
    "as printed, keeping thousands dots. Give the province as printed, or TOTAL for a totals row.\n"
    "summary: one row per province (and the totals row) from any general summary table or provincial scrutiny record: "
    "electores (registered voters), votantes (ballots cast, when printed), votos válidos total, votos a candidaturas, "
    "votos en blanco, and votos nulos; null for a figure that is not printed.\n"
    "lists: one row per party, federation, coalition, or group of electors and province where it has a figure: its full "
    "name as printed, its abbreviation as printed (usually in parentheses; null when none), the seats (escaños) printed "
    "for it in that province (0 when the table or record shows none, null when the page gives no seats at all), and "
    "its votes. Skip cells printed as a dash or left empty. Include the totals row of each table with province TOTAL.\n"
    "elected: every procurador proclaimed elected on this page, with the province, the list (abbreviation or name as "
    "printed), and the person's name exactly as printed, in the printed order.\n"
    "Return empty arrays when the page holds none of these."
)
FIELD = {"type": ["string", "null"]}


def table(keys: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {key: FIELD for key in keys},
            "required": list(keys),
            "additionalProperties": False,
        },
    }


SUMMARY_KEYS = ("province", "electors", "voters", "valid", "candidacies", "blank", "invalid")
LIST_KEYS = ("province", "name", "abbreviation", "seats", "votes")
ELECTED_KEYS = ("province", "list", "name")
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"summary": table(SUMMARY_KEYS), "lists": table(LIST_KEYS), "elected": table(ELECTED_KEYS)},
    "required": ["summary", "lists", "elected"],
    "additionalProperties": False,
}


def bulletin(year: int) -> str:
    return JEC + ELECTIONS[year][1]


def read_key(sha256: str, page: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{sha256}/{page}"


def page_count(artifact: raw.Artifact) -> int:
    info = subprocess.run(["pdfinfo", str(artifact.path)], check=True, capture_output=True, text=True).stdout
    return int(next(line.split()[-1] for line in info.splitlines() if line.startswith("Pages:")))


def text_layer(artifact: raw.Artifact, page: int) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(artifact.path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def pages(year: int) -> list[int]:
    artifact = raw.cached(SOURCE, bulletin(year))
    return list(range(1, min(page_count(artifact), LAST_PAGE.get(year, 99)) + 1))


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS, suffix=".csv")
    raw.fetch(SOURCE, BASICS, suffix=".csv")
    for url in METADATA:
        raw.fetch(SOURCE, url, suffix=".json")
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for year in ELECTIONS:
        artifact = raw.fetch(SOURCE, bulletin(year), suffix=".pdf")
        for page in pages(year):
            png = subprocess.run(
                ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                + [str(artifact.path)],
                check=True,
                capture_output=True,
            ).stdout
            image = Image.open(io.BytesIO(png))
            image.thumbnail((LONGEST, LONGEST))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            text = text_layer(artifact, page)
            prompt = f"Election of {ELECTIONS[year][0].isoformat()}, page {page}."
            if sum(char.isdigit() for char in text) > 20:
                prompt += "\nText layer of this page:\n" + text
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=16000,
                system=READ_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            ImageBlockParam(
                                type="image",
                                source={
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64.b64encode(buffer.getvalue()).decode(),
                                },
                            ),
                            TextBlockParam(type="text", text=prompt),
                        ],
                    }
                ],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
            )
            wanted[read_key(artifact.sha256, page)] = (
                params,
                ((image.width * image.height / 750 + len(prompt) / 3) * price_in + 6000 * price_out) / 1e6,
            )
    print(batch.ensure(SOURCE, "bocyl", wanted)[1])


def read(year: int, page: int) -> dict[str, list[dict[str, str | None]]]:
    artifact = raw.cached(SOURCE, bulletin(year))
    message = json.loads(raw.cached(SOURCE, read_key(artifact.sha256, page)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in plain.lower() if char.isalnum())


PROVINCE_CODES = {fold(name): code for code, name in PROVINCES.items()}


def figure(text: str | None) -> int | None:
    if text is None or not text.strip():
        return None
    digits = re.sub(r"[.\s]", "", text)
    if not digits.isdigit():
        raise ValueError(f"figure {text!r}")
    return int(digits)


def printed(year: int) -> tuple[dict[str, dict[str, int | None]], list[dict], list[dict]]:
    """The bulletin's province summaries, list rows, and members, as read, deduplicated, and corrected."""
    summary: dict[str, dict[str, int | None]] = {}
    lists: dict[tuple[str, str], dict] = {}
    members: list[dict] = []
    for page in pages(year):
        found = read(year, page)
        if page in TRANSCRIBED.get(year, {}):
            found["lists"] = [
                {
                    "province": PROVINCES[code],
                    "name": label.split(" (")[0],
                    "abbreviation": label.split(" (")[1][:-1] if " (" in label else None,
                    "seats": "0",
                    "votes": str(votes),
                }
                for label, cells in TRANSCRIBED[year][page].items()
                for code, votes in cells.items()
            ]
        for row in found["summary"]:
            code = PROVINCE_CODES.get(
                fold(row["province"] or ""), "TOTAL" if fold(row["province"] or "") == "total" else None
            )
            figures = {key: figure(row[key]) for key in SUMMARY_KEYS[1:]}
            if code is None or not any(figures.values()):
                continue
            summary.setdefault(code, figures)
        for row in found["lists"]:
            code = PROVINCE_CODES.get(fold(row["province"] or ""))
            if code is None:
                continue
            entry = {
                "province": code,
                "name": " ".join((row["name"] or "").split()),
                "abbreviation": row["abbreviation"],
                "seats": figure(row["seats"]),
                "votes": figure(row["votes"]),
                "page": page,
            }
            lists.setdefault((code, fold(entry["name"])), entry)
        for row in found["elected"]:
            code = PROVINCE_CODES.get(fold(row["province"] or ""))
            if code is None:
                raise ValueError(f"{year} page {page}: member in {row['province']!r}")
            members.append({"province": code, "list": row["list"], "name": row["name"], "page": page})
    for (code, name), fix in FIXES.get(year, {}).items():
        if fix is None:
            del lists[code, fold(name)]
            continue
        entry = lists.setdefault(
            (code, fold(name)), {"province": code, "name": name, "abbreviation": None, "seats": 0, "page": None}
        )
        entry.update(fix)
    return summary, list(lists.values()), members


SMALL_WORDS = {"de", "del", "la", "las", "los", "el", "y", "e", "por", "en", "con"}


def akin(label: str, entry: dict) -> int:
    """How surely a mesa file's party label names a bulletin list: 3 by its abbreviation or name, 2 by its initials,
    1 by the letters of its name in order, 0 not at all."""
    key = fold(label)
    if not key:
        return 0
    name, short = fold(entry["name"]), fold(entry["abbreviation"] or "")
    if key in (name, short) or (len(key) > 4 and key in name) or (len(short) > 2 and (short in key or key in short)):
        return 3
    names = [name]
    words = [fold(word) for word in re.findall(r"\w+", entry["name"]) if word.lower() not in SMALL_WORDS]
    initials = "".join(word[0] for word in words if word)
    if len(key) > 1 and len(initials) > 1 and (key == initials or key in initials or initials in key):
        return 2
    rest = iter(names[0])
    return int(len(key) > 1 and all(char in rest for char in key))


# Mesa file labels whose list the names alone don't tell.
SAME = {"ADEIZA-UPZ": "Agrupación de Electores Zamoranos-Unión del Pueblo Zamorano"}


def pair(labels: dict[str, int], entries: list[dict], exact: bool) -> dict[str, dict]:
    """Each mesa label's bulletin list in one province: a namesake, then a list named by its letters whose votes fit,
    then the closest fit by votes."""
    candidates = []
    for label, votes in labels.items():
        for number, entry in enumerate(entries):
            official = entry["votes"] or 0
            gap = official - votes
            near = abs(gap) <= max(30, 0.2 * official) if not exact else gap == 0
            close = abs(gap) <= max(5, 0.02 * official) if not exact else gap == 0
            named = akin(label, entry)
            if named == 3 or (named == 2 and abs(gap) <= max(50, 0.4 * official)) or (named and near) or close:
                candidates.append((-named, abs(gap) / max(official, 1), label, number))
    found: dict[str, dict] = {}
    taken: set[int] = set()
    for label in labels:
        target = SAME.get(label)
        for number, entry in enumerate(entries):
            if entry["name"] == target:
                found[label] = entry
                taken.add(number)
    for _, _, label, number in sorted(candidates):
        if label not in found and number not in taken:
            found[label] = entries[number]
            taken.add(number)
    return found


ORDER = tuple(PROVINCES)


def columns(*cells: int | None) -> dict[str, int]:
    """A list's votes in a table whose rows run Ávila to Zamora, empty cells as None."""
    return {code: value for code, value in zip(ORDER, cells, strict=True) if value is not None}


_ = None
# Tables transcribed by hand from the page image where the page read lost or misplaced rows; each column adds up to
# the TOTALES row the page prints, and each province's lists add up to its votes to candidacies.
TRANSCRIBED: dict[int, dict[int, dict[str, dict[str, int]]]] = {
    1987: {
        4: {
            "Coalición Izquierda Unida (I.U.)": columns(2606, 7573, 11009, 4679, 6606, 6104, 1454, 11853, 2792),
            "Partido de los Trabajadores de España-Unidad Comunista (PTE-UC)": columns(
                387, 1227, 1475, 777, 1021, 402, 265, 5834, 555
            ),
            "Partido Unión Leonesista (U.N.L.E.)": columns(_, _, 7443, 184, 372, 134, 116, _, 711),
            "Partido del Bierzo (P.B.)": columns(_, _, 5387, _, _, _, _, _, _),
            "Partido Nacionalista de Castilla y León (PNCL-PANCAL)": columns(_, 1416, _, 1102, _, 326, 391, 1511, 444),
            "Partido Regionalista del País Leonés (PREPAL)": columns(_, _, 2078, _, 1012, _, _, _, 1000),
            "Coalición Plataforma Humanista (P.H.)": columns(_, 964, 640, 296, 842, 259, _, 933, _),
            "Partido Liberal (PL)": columns(_, _, 2213, _, _, _, _, _, _),
            "Falange Española de las JONS (F.E. de las JONS)": columns(_, _, 510, _, 495, _, _, 823, _),
            "Partido Ruralista Español (P.R.E.)": columns(_, _, _, _, _, _, _, 749, _),
        }
    },
    1999: {
        3: {
            "Tierra Comunera-Partido Nacionalista Castellano": columns(453, _, 157, 996, 575, 503, 424, 2190, 197),
            "Partido de los Autónomos de España y de las Agrupaciones Independientes Españolas": columns(
                565, _, _, _, _, _, _, _, _
            ),
            "Partido de El Bierzo": columns(_, _, 3851, _, _, _, _, _, _),
            "Partido Demócrata Español": columns(_, 1725, 432, _, 504, 576, _, _, _),
            "Partido Regionalista del País Leonés": columns(_, _, 558, _, 1073, _, _, _, 1412),
            "Partido Humanista": columns(_, 709, _, 338, 342, _, _, 807, 137),
            "Unidad Regionalista de Castilla y León": columns(495, 981, _, 1419, 4939, 230, 362, 2459, 310),
            "Los Verdes Grupo Verde": columns(_, _, _, _, _, 1383, _, _, _),
            "Unión Centrista-Centro Democrático y Social": columns(_, 1344, 836, _, _, 1356, _, 1584, 5302),
            "Falange Española de las J.O.N.S.": columns(478, _, 331, _, _, 203, _, _, _),
            "Los Verdes Confederación": columns(_, _, _, _, _, _, _, _, 791),
            "Candidatura Independiente de Valladolid": columns(_, _, _, _, _, _, _, 6784, _),
            "Izquierda Unida de Castilla y León": columns(6801, 13508, 11693, 6323, 8013, 5082, 2949, _, 3650),
            "Unidad Salmantina Independiente": columns(_, _, _, _, 1851, _, _, _, _),
            "Partido Nacionalista de Castilla y León": columns(_, _, _, _, _, 276, _, _, _),
            "Unión del Pueblo Zamorano": columns(_, _, _, _, _, _, _, _, 1556),
        }
    },
    2003: {
        3: {
            "Izquierda Unida de Castilla y León": columns(4422, 8905, 9767, 3672, 4462, 3601, 1155, 14777, 3324),
            "Tierra Comunera-Partido Nacionalista Castellano": columns(
                1343, 9789, 122, 2439, 298, 1001, 306, 3087, 210
            ),
            "Candidatura Independiente-El Partido de Castilla y León": columns(_, _, _, _, _, _, _, 11180, _),
            "Unión del Pueblo Salmantino": columns(_, _, _, _, 6630, _, _, _, _),
            "Unidad Regionalista de Castilla y León": columns(_, _, _, _, 4039, _, _, 1284, _),
            "Coalición Electoral Los Verdes-Foro de Izquierdas": columns(_, _, _, _, 4130, _, _, _, _),
            "Izquierda Castellana": columns(293, 743, _, 257, 335, 304, 142, 1744, 154),
            "Centro Democrático y Social": columns(_, 764, _, _, _, 1034, _, 1218, _),
            "Zamora Unida": columns(_, _, _, _, _, _, _, _, 2579),
            "Izquierda Republicana": columns(285, 519, 386, 208, 173, 170, 162, 377, 140),
            "Los Verdes-Grupo Verde": columns(_, _, _, _, _, 2196, _, _, _),
            "Partido de El Bierzo": columns(_, _, 2286, _, _, _, _, _, _),
            "Partido Humanista": columns(293, 565, _, 257, _, 125, _, 798, _),
            "Ciudadanos Independientes Unidos Leoneses": columns(_, _, 2051, _, _, _, _, _, _),
            "Unión del Pueblo Zamorano": columns(_, _, _, _, _, _, _, _, 1998),
            "Iniciativa por el Desarrollo de Soria": columns(_, _, _, _, _, _, 1908, _, _),
            "Los Verdes": columns(_, _, 1835, _, _, _, _, _, _),
            "Partido Regionalista del País Leonés": columns(_, _, 394, _, 513, _, _, _, 713),
            "Los Verdes-Cives": columns(_, _, _, 1459, _, _, _, _, _),
            "Alternativa Segoviana": columns(_, _, _, _, _, 1314, _, _, _),
            "La Falange": columns(303, _, 321, _, 377, _, _, _, 196),
            "Partido Regionalista de El Bierzo": columns(_, _, 1041, _, _, _, _, _, _),
            "Falange Española Independiente-Falange 2000": columns(_, _, _, _, _, _, _, 556, _),
            "Partido Demócrata Español": columns(_, 465, _, _, _, _, _, _, _),
            "Falange Auténtica": columns(243, _, _, _, _, _, _, _, _),
            "Unión del Pueblo Leonés": columns(_, _, _, _, _, _, _, _, 3942),
            "Unión Centrista Liberal": columns(_, _, _, _, _, _, _, _, 652),
        }
    },
}
# Cells the page read lost or misread, each checked against the page and proven by the province's printed votes to
# candidacies.
FIXES: dict[int, dict[tuple[str, str], dict | None]] = {
    1991: {
        ("37", "Democracia Regionalista de Castilla y León"): {"votes": 762},
        ("34", "Coalición de Los Verdes Ecologistas-Humanistas"): None,
        ("34", "Partido Comunista de los Pueblos de España"): {"votes": 353, "page": 2},
    },
    2011: {("47", "Democracia Nacional"): {"abbreviation": "DN", "votes": 651, "page": 3}},
}


def mesas() -> tuple[pl.DataFrame, pl.DataFrame]:
    frame = pl.read_csv(raw.cached(SOURCE, RESULTS).path, separator=";", infer_schema_length=0)
    parts = pl.col("codigo_de_mesa").str.split("-")
    frame = frame.with_columns(
        year=pl.col("ano").cast(pl.Int32),
        province=parts.list.get(0),
        mun=parts.list.get(1),
        district=parts.list.get(2).str.zfill(2),
        section=parts.list.get(3).str.slice(-3).str.zfill(3),
        letter=parts.list.get(4),
        party=pl.col("partido").str.strip_chars(),
        votes=pl.col("no_votos").cast(pl.Int64),
        electors=pl.col("censo").cast(pl.Int64),
        voters=pl.col("total_votantes").cast(pl.Int64),
        invalid=pl.col("votos_nulos").cast(pl.Int64),
        blank=pl.col("votos_blanco").cast(pl.Int64),
    ).with_columns(
        section_code=pl.col("province") + pl.col("mun") + pl.col("district") + pl.col("section"),
        mesa=pl.col("codigo_de_mesa"),
    )
    keys = ["year", "mesa"]
    units = (
        frame.group_by(keys)
        .agg(
            pl.col("province", "mun", "district", "section", "section_code", "letter").first(),
            pl.col("municipio").first().alias("name"),
            pl.max_horizontal(pl.col(field).n_unique() for field in ("electors", "voters", "invalid", "blank")).alias(
                "variants"
            ),
            pl.col("electors", "voters", "invalid", "blank").first(),
        )
        .sort(keys)
    )
    votes = frame.group_by([*keys, "province", "party"]).agg(pl.col("votes").sum(), pl.len().alias("rows"))
    return units, votes


def read_mesas() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    units, votes = mesas()
    if (bad := units.filter(pl.col("variants") > 1)).height:
        raise ValueError(f"{bad.height} mesas with differing accounting across their rows")
    if (twice := votes.filter(pl.col("rows") > 1)).height:
        raise ValueError(f"{twice.height} mesa and party pairs printed more than once")
    candidacies = votes.group_by(["year", "mesa"]).agg(cand=pl.col("votes").sum())
    units = units.drop("variants").join(candidacies, on=["year", "mesa"])
    units.write_parquet(PARSED / "mesas.parquet")
    votes.drop("rows").write_parquet(PARSED / "mesa_votes.parquet")


# The archive's rows for Castilla y León that the packages attach to: the INE's provinces, municipalities, and census
# sections, and the reporting units other sources already hold. Read once from the database and kept beside the
# parsed files, so a rebuild gives the same packages; delete the file to read them again.
REFERENCE = PARSED / "reference.json"


def reference() -> dict[str, dict[str, str]]:
    if REFERENCE.exists():
        return json.loads(REFERENCE.read_text())
    stamps = [stamp(year) for year in ELECTIONS]
    provinces = list(PROVINCES)
    out: dict[str, dict[str, str]] = {}
    with connect() as conn:
        for scheme in ("es.provincia", "es.municipio"):
            out[f"area:{scheme}"] = {
                code: str(id)
                for code, id in conn.execute(
                    "select c.code, a.id from area a join area_code c on c.area_id = a.id"
                    " where a.scheme_code = %s and left(c.code, 2) = any(%s)",
                    (scheme, provinces),
                ).fetchall()
            }
        for year, (day, *_) in ELECTIONS.items():
            out[f"area:es.seccion@{year}"] = {
                code: str(id)
                for code, id in conn.execute(
                    "select c.code, a.id from area a join area_code c on c.area_id = a.id"
                    " where a.scheme_code = 'es.seccion' and left(c.code, 2) = any(%s) and c.valid @> %s::date",
                    (provinces, day),
                ).fetchall()
            }
        for scheme in ("es.provincia", "es.municipio"):
            out[f"unit:{scheme}"] = {
                code: str(id)
                for code, id in conn.execute(
                    "select code, id from reporting_unit where scheme_code = %s and left(code, 2) = any(%s)"
                    " and valid_to is null",
                    (scheme, provinces),
                ).fetchall()
            }
        out["unit:es.mesa"] = {
            code: str(id)
            for code, id in conn.execute(
                "select code, id from reporting_unit where scheme_code = 'es.mesa' and left(code, 6) = any(%s)"
                " and substr(code, 8, 2) = any(%s) and valid_to is null",
                (stamps, provinces),
            ).fetchall()
        }
    REFERENCE.write_text(json.dumps(out, sort_keys=True))
    return out


def stamp(year: int) -> str:
    return f"{ELECTIONS[year][0]:%Y%m}"


SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-CL"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 3
YEARS = [year for year in ELECTIONS if year != 1983]
PUBLISHER = "Junta de Castilla y León"
TITLE = "Resultados electorales a las Cortes de Castilla y León (1983 - actualidad), datos abiertos"
PORTAL = "https://analisis.datosabiertos.jcyl.es/explore/dataset/resultados-electorales-1983-actualidad/"
BOARD = "Junta Electoral de Castilla y León"
BOARD_TITLE = (
    "Acuerdos de la Junta Electoral de Castilla y León sobre los resultados de las elecciones a las Cortes de "
    "Castilla y León y los procuradores proclamados electos (Boletín Oficial de Castilla y León)"
)
STATUTE = "Ley Orgánica 4/1983, de 25 de febrero, de Estatuto de Autonomía de Castilla y León"
LAW = "Ley 3/1987, de 30 de marzo, Electoral de Castilla y León"
SYSTEM = (
    "Closed party lists in nine constituencies, the provinces of Ávila, Burgos, León, Palencia, Salamanca, Segovia, "
    "Soria, Valladolid, and Zamora. Each province elects three procuradores plus one more for every 45,000 "
    "inhabitants or fraction above 22,500, so the size of the Cortes follows the population (84 seats in 1983 to "
    "1995 and in 2011 and 2015, and between 81 and 83 in the other years). Seats go by the D'Hondt method to the "
    "lists with at least 3% of the valid votes in the province, blank ballots counted as valid ("
    + LAW
    + "; "
    + STATUTE
    + "). The Junta Electoral de Castilla y León publishes the results and the procuradores the provincial boards "
    "proclaimed in the Boletín Oficial de Castilla y León, and those are the declared seats stored here."
)
PROVINCE_METHOD = (
    "Electors, list votes, null and blank ballots, and seats by province as the Junta Electoral de Castilla y León "
    "published them ({bulletin}), votes of residents abroad (CERA) included. The figures were read from the page "
    "images of the bulletin and each province's lists checked against the votes to candidacies the bulletin "
    "prints. Valid ballots here are the votes to candidacies, with blank ballots stored apart.{extra}"
)
PRINTED_DIFFERS = (
    " The bulletin's list votes add up to {lists:,} in this province, {how} the {printed:,} its summary table gives "
    "as votes to candidacies (valid votes less blank ballots); the list votes are stored."
)
MESA_METHOD = (
    "List votes and ballot counts by polling station (mesa) from the Junta de Castilla y León's open-data results "
    "file, each mesa on the INE census section of the same code where the archive holds the section map in force "
    "on election day, and otherwise on its municipality. {abroad} The mesa figures add up to {votes:,} list votes, "
    "against {official:,} in the province results.{extra}"
)
MUNICIPIO_METHOD = (
    "List votes and ballot counts by municipality, the sums of the Junta de Castilla y León's open-data results by "
    "polling station. {abroad} The municipality figures add up to {votes:,} list votes, against {official:,} in the "
    "province results.{extra}"
)
WITH_ABROAD = "The votes of residents abroad (CERA) are one row of the province, on the abroad channel."
WITHOUT_ABROAD = "The file leaves out the votes of residents abroad (CERA)."
OFF_LIST = (
    " The file also gives {votes:,} votes to {label}, a label the bulletin lists no candidacy for in this province; "
    "they are stored under that label."
)


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {key: count for key, count in votes.items() if count * 100 >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, key) for key, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(key for *_, key in quotients[:seats])


def party_key(entry: dict) -> str:
    return fold(entry["name"]) or fold(entry["abbreviation"] or "")


def honorless(name: str) -> str:
    return re.sub(r"^(Don|Doña|D\.|Dña\.)\s+", "", " ".join(name.split()))


def member_list(member: dict, entries: list[dict]) -> dict:
    """The list a proclaimed member stood on, from the list label the proclamation prints."""
    label = member["list"]
    inner = re.findall(r"\(([^()]+)\)\s*$", label)
    outer = re.sub(r"\s*\([^()]+\)\s*$", "", label)
    for entry in entries:
        if fold(outer) == fold(entry["name"]) or fold(label) == fold(entry["name"]):
            return entry
    for entry in entries:
        if inner and entry["abbreviation"] and fold(inner[0]) == fold(entry["abbreviation"]):
            return entry
        if entry["abbreviation"] and fold(label) == fold(entry["abbreviation"]):
            return entry
    best = max(entries, key=lambda entry: (akin(outer, entry), entry["seats"] or 0))
    if not akin(outer, best) or not best["seats"]:
        raise ValueError(f"member {member['name']!r}: no list for {label!r}")
    return best


class Election:
    """One election's bulletin figures, checked against the printed totals, and its mesa file rows."""

    def __init__(self, year: int) -> None:
        self.year = year
        self.day = ELECTIONS[year][0]
        summary, lists, members = printed(year)
        self.summary = summary
        self.lists = [entry for entry in lists if entry["votes"] is not None]
        self.members = members
        self.differs: dict[str, tuple[int, int]] = {}
        for code in PROVINCES:
            entries = self.entries(code)
            row = summary[code]
            printed_cand = row["candidacies"]
            if printed_cand is None:
                printed_cand = (row["valid"] or 0) - (row["blank"] or 0)
            listed = sum(entry["votes"] for entry in entries)
            if listed != printed_cand:
                if (year, code) not in DIFFERS:
                    raise ValueError(f"{year} {code}: lists add up to {listed}, the bulletin prints {printed_cand}")
                self.differs[code] = (listed, printed_cand)
            elif (year, code) in DIFFERS:
                raise ValueError(f"{year} {code}: lists now agree with the bulletin; drop the DIFFERS entry")
            if len({party_key(entry) for entry in entries}) != len(entries):
                raise ValueError(f"{year} {code}: two lists share a name")
        if members:
            for code in PROVINCES:
                entries = self.entries(code)
                counts = Counter(party_key(member_list(m, entries)) for m in members if m["province"] == code)
                for entry in entries:
                    if counts[party_key(entry)] != (entry["seats"] or 0):
                        raise ValueError(f"{year} {code}: {entry['name']} has {entry['seats']} seats, members differ")
        frame = pl.read_parquet(PARSED / "mesas.parquet").filter(pl.col("year") == year)
        votes = pl.read_parquet(PARSED / "mesa_votes.parquet").filter(pl.col("year") == year)
        self.units = frame
        self.labels: dict[tuple[str, str], str] = {}
        self.off_list: dict[str, dict[str, int]] = {}
        for code in PROVINCES:
            labels = dict(
                votes.filter(pl.col("province") == code).group_by("party").agg(pl.col("votes").sum()).iter_rows()
            )
            found = pair(labels, self.entries(code), year == 2026)
            for label, count in labels.items():
                if label in found:
                    self.labels[code, label] = party_key(found[label])
                elif count:
                    self.labels[code, label] = "~" + fold(label)
                    self.off_list.setdefault(code, {})[label] = count
        self.votes = votes.with_columns(
            key=pl.struct("province", "party").map_elements(
                lambda row: self.labels.get((row["province"], row["party"])), return_dtype=pl.String
            )
        ).filter(pl.col("key").is_not_null())

    def entries(self, code: str) -> list[dict]:
        return [entry for entry in self.lists if entry["province"] == code]

    def seats(self, code: str) -> int:
        return sum(entry["seats"] or 0 for entry in self.entries(code))


# Provinces whose list votes differ from the votes to candidacies the bulletin's own summary prints: each list column
# adds up to the TOTALES row the bulletin prints, and so does each summary column, so the difference is the
# bulletin's own.
DIFFERS = {
    (1987, "05"),
    (1987, "09"),
    (1987, "34"),
    (1987, "37"),
    (1987, "42"),
    (1987, "47"),
    (1995, "49"),
    (1999, "34"),
    (1999, "40"),
}


def registry(elections: list[Election]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Cortes de Castilla y León, their electoral system and size, the Junta de Castilla y León's results file "
        "and the Junta Electoral de Castilla y León's bulletins as sources, and the candidacies of 1987 to 2026",
    )
    p.external |= {REGION}
    p.add(
        "source",
        id=p.id("source", "results"),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PORTAL,
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/deed.es_ES",
        attribution="Fuente: Junta de Castilla y León, datos abiertos (CC BY 4.0).",
    )
    for url in METADATA:
        p.uses(raw.cached(SOURCE, url))
    p.add(
        "source",
        id=p.id("source", "bulletin"),
        kind="official_body",
        publisher=BOARD,
        title=BOARD_TITLE,
        url="https://www.juntaelectoralcentral.es/cs/jec/elecciones/autonomicas",
        attribution="Fuente: Junta Electoral de Castilla y León, Boletín Oficial de Castilla y León.",
    )
    body = p.id("body", "cortes")
    p.add("body", id=body, polity_id=REGION, kind="unicameral")
    p.name(body, "Cortes of Castile and León", "en", "official")
    p.name(body, "Cortes de Castilla y León", "es", "native")
    system = p.id("electoral_system", "cortes")
    p.add(
        "electoral_system",
        id=system,
        polity_id=REGION,
        rules={
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD},
        },
        rules_version=1,
    )
    p.name(
        system, "Cortes de Castilla y León closed-list D'Hondt system with a 3% provincial threshold", "en", "official"
    )
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM, lang="en")
    for number, election in enumerate(elections):
        later = elections[number + 1].day if number + 1 < len(elections) else None
        seats = sum(election.seats(code) for code in PROVINCES)
        p.add("body_size", body_id=body, seats=seats, valid_from=election.day, valid_to=later)
    names: dict[str, str] = {}
    for election in elections:
        for entry in election.lists:
            names.setdefault(party_key(entry), entry["name"])
        for labels in election.off_list.values():
            for label in labels:
                names.setdefault("~" + fold(label), label)
    for key, text in sorted(names.items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, text, "es", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=key)
    return p


def municipio_code(province: str, mun: str) -> str:
    return province + ("999" if mun.startswith("99") else mun)


def geo(elections: list[Election]) -> Package:
    """The municipality units the archive lacks, one per code across all elections."""
    ref = reference()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Reporting units for the municipalities of Castilla y León, and for the votes of residents abroad by "
        "province, that the Junta de Castilla y León's results file uses and the archive did not yet hold",
    )
    p.external |= {SPAIN, "es.municipio"}
    codes = {(row["province"], row["mun"]) for election in elections for row in election.units.iter_rows(named=True)}
    seen: set[str] = set()
    for province, mun in sorted(codes):
        code = municipio_code(province, mun)
        if code in ref["unit:es.municipio"] or code in seen:
            continue
        seen.add(code)
        if mun.startswith("99"):
            area, relation, kind = ref["area:es.provincia"][province], "within", "aggregate"
        elif code in ref["area:es.municipio"]:
            area, relation, kind = ref["area:es.municipio"][code], "equals", "standard"
        else:
            area, relation, kind = ref["area:es.provincia"][province], "within", "standard"
        p.external.add(area)
        p.add(
            "reporting_unit",
            id=unit_id("es.municipio", code),
            polity_id=SPAIN,
            scheme_code="es.municipio",
            code=code,
            parent_id=None,
            area_relation=relation,
            area_id=area,
            kind=kind,
        )
    return p


def unit_id(scheme: str, code: str) -> str:
    """The id the archive gives a unit by its scheme and code, so a later source that resolves the code finds it."""
    return str(uuid5(package.NAMESPACE, f"unit:{scheme}@None\x1f{code}"))


def build(election: Election) -> Package:
    ref = reference()
    year, day = election.year, election.day
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} election to the Cortes de Castilla y León by province, municipality, and polling station, with "
        "the seats each list won",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {SPAIN, REGION}
    results, bulletin_source = p.id("source", "results"), p.id("source", "bulletin")
    p.artifact(raw.cached(SOURCE, RESULTS), results)
    p.artifact(raw.cached(SOURCE, bulletin(year)), bulletin_source)
    for page in pages(year):
        p.uses(raw.cached(SOURCE, read_key(raw.cached(SOURCE, bulletin(year)).sha256, page)))
    body, system = p.id("body", "cortes"), p.id("electoral_system", "cortes")
    eid = p.id("election", day)
    p.add("election", id=eid, polity_id=REGION, kind="general", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Castilian-Leonese regional election", "en", "official")
    p.name(eid, f"Elecciones a las Cortes de Castilla y León de {year}", "es", "native")
    path = f"es/cl/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    values: list[tuple[str, str, str | None, int, int, int]] = []

    def account(result_set: str, unit: str, row: dict, channel: int = 0) -> None:
        for measure, field in ((REGISTERED, "electors"), (BALLOTS, "voters"), (VALID, "cand"), (INVALID, "invalid")):
            if row.get(field) is not None:
                values.append((result_set, unit, None, measure, row[field], channel))
        if row.get("blank") is not None:
            values.append((result_set, unit, None, BLANK, row["blank"], channel))

    def result_set(key: str, label: str, default: bool, source: str, completeness: float, note: str) -> str:
        id = p.id("result_set", key, label)
        p.add(
            "result_set",
            id=id,
            election_id=eid,
            contest_id=p.id("contest", key),
            stage="official",
            kind="counted",
            status="active",
            source_id=source,
            is_default=default,
            completeness=completeness,
        )
        p.add("note", id=p.id("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
        return id

    units = election.units
    mesa_votes = election.votes
    accepted: dict[str, list[str]] = {}
    for code, province_name in PROVINCES.items():
        key = f"{day}:{code}"
        entries = election.entries(code)
        contest = p.id("contest", key)
        area = ref["area:es.provincia"][code]
        p.external.add(area)
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="list_allocation",
            body_id=body,
            district_area_id=area,
            magnitude=election.seats(code),
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, province_name, "es", "official")
        p.add("slug", path=f"{path}/{slug(province_name)}", entity_id=contest, is_current=True)
        options: dict[str, str] = {}
        ordered = sorted(entries, key=lambda entry: (-entry["votes"], entry["name"]))
        for entry in ordered:
            options[party_key(entry)] = p.id("option", key, party_key(entry))
            p.add(
                "contest_option",
                id=options[party_key(entry)],
                contest_id=contest,
                kind="list",
                party_id=p.id("party", party_key(entry)),
                ballot_label=entry["name"]
                + (f" ({entry['abbreviation']})" if entry["abbreviation"] and len(entry["abbreviation"]) < 30 else ""),
            )
        for label in election.off_list.get(code, {}):
            options["~" + fold(label)] = p.id("option", key, "~" + fold(label))
            p.add(
                "contest_option",
                id=options["~" + fold(label)],
                contest_id=contest,
                kind="list",
                party_id=p.id("party", "~" + fold(label)),
                ballot_label=label,
            )
        official = sum(entry["votes"] for entry in entries)
        summary = election.summary[code]
        extra = ""
        if code in election.differs:
            listed, printed_cand = election.differs[code]
            extra = PRINTED_DIFFERS.format(
                lists=listed, how="above" if listed > printed_cand else "below", printed=printed_cand
            )
        province_set = result_set(
            key, "province", True, bulletin_source, 1, PROVINCE_METHOD.format(bulletin=ELECTIONS[year][2], extra=extra)
        )
        unit = ref["unit:es.provincia"][code]
        p.external.add(unit)
        account(
            province_set,
            unit,
            {
                "electors": summary["electors"],
                "cand": official,
                "invalid": summary["invalid"],
                "blank": summary["blank"],
            },
        )
        for entry in ordered:
            values.append((province_set, unit, options[party_key(entry)], VOTES, entry["votes"], 0))
            if entry["seats"]:
                p.add(
                    "outcome",
                    result_set_id=province_set,
                    option_id=options[party_key(entry)],
                    kind="won",
                    seats=entry["seats"],
                )
        seen: Counter[str] = Counter()
        for member in (m for m in election.members if m["province"] == code):
            entry = member_list(member, entries)
            name = honorless(member["name"])
            tag = f"{key}:{party_key(entry)}:{fold(name)}"
            seen[tag] += 1
            if seen[tag] > 1:
                tag += f":{seen[tag]}"
            person = p.id("person", tag)
            p.add("person", id=person)
            p.name(person, name, "es", "official")
            candidacy = p.id("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[party_key(entry)],
                role="list_member",
                ballot_name=name,
                member_of_party_id=p.id("party", party_key(entry)),
                is_independent=False,
            )
            p.add(
                "outcome",
                result_set_id=province_set,
                option_id=options[party_key(entry)],
                candidacy_id=candidacy,
                kind="elected",
            )
        won = dhondt(
            {party_key(e): e["votes"] for e in entries}, official + (summary["blank"] or 0), election.seats(code)
        )
        wrong = sorted(k for k in options if won[k] != next((e["seats"] or 0 for e in entries if party_key(e) == k), 0))
        print(f"{year} {code} seats={election.seats(code)} lists={official} dhondt_differs={wrong}", flush=True)

        here = units.filter(pl.col("province") == code)
        if here.is_empty():
            continue
        here_votes = mesa_votes.filter(pl.col("province") == code)
        got = int(here_votes["votes"].sum())
        abroad_note = WITH_ABROAD if here["mun"].str.starts_with("99").any() else WITHOUT_ABROAD
        off = "".join(
            OFF_LIST.format(votes=count, label=label) for label, count in election.off_list.get(code, {}).items()
        )
        completeness = min(got / official, 1.0)
        mesa_set = result_set(
            key,
            "mesa",
            False,
            results,
            completeness,
            MESA_METHOD.format(abroad=abroad_note, votes=got, official=official, extra=off),
        )
        municipio_set = result_set(
            key,
            "municipio",
            False,
            results,
            completeness,
            MUNICIPIO_METHOD.format(abroad=abroad_note, votes=got, official=official, extra=off),
        )
        sections = ref[f"area:es.seccion@{year}"]
        mesa_units: dict[str, tuple[str, int]] = {}
        for row in here.iter_rows(named=True):
            abroad = row["mun"].startswith("99")
            channel = ABROAD if abroad else 0
            mcode = municipio_code(code, row["mun"])
            parent = ref["unit:es.municipio"].get(mcode) or unit_id("es.municipio", mcode)
            if mcode in ref["unit:es.municipio"]:
                p.external.add(parent)
            ucode = f"{stamp(year)}:{code}{row['mun']}{row['district']}{row['section']}{row['letter']}"
            if ucode in ref["unit:es.mesa"]:
                uid = ref["unit:es.mesa"][ucode]
                p.external.add(uid)
            else:
                uid = unit_id("es.mesa", ucode)
                if abroad:
                    area, kind = ref["area:es.provincia"][code], "aggregate"
                else:
                    area = sections.get(row["section_code"]) or ref["area:es.municipio"].get(mcode)
                    area, kind = area or ref["area:es.provincia"][code], "standard"
                p.external |= {area, "es.mesa"}
                p.add(
                    "reporting_unit",
                    id=uid,
                    polity_id=SPAIN,
                    scheme_code="es.mesa",
                    code=ucode,
                    parent_id=parent,
                    area_relation="within",
                    area_id=area,
                    kind=kind,
                )
            mesa_units[row["mesa"]] = (uid, channel)
            account(mesa_set, uid, row, channel)
        for row in here_votes.iter_rows(named=True):
            uid, channel = mesa_units[row["mesa"]]
            values.append((mesa_set, uid, options[row["key"]], VOTES, row["votes"], channel))
        here = here.with_columns(
            mcode=pl.struct("mun").map_elements(lambda r: municipio_code(code, r["mun"]), return_dtype=pl.String)
        )
        places = here.group_by("mcode").agg(pl.col("electors", "voters", "cand", "invalid", "blank").sum())
        for row in places.iter_rows(named=True):
            uid = ref["unit:es.municipio"].get(row["mcode"]) or unit_id("es.municipio", row["mcode"])
            account(municipio_set, uid, row, ABROAD if row["mcode"].endswith("999") else 0)
        place_votes = (
            here_votes.join(here.select("mesa", "mcode"), on="mesa").group_by("mcode", "key").agg(pl.col("votes").sum())
        )
        for row in place_votes.iter_rows(named=True):
            uid = ref["unit:es.municipio"].get(row["mcode"]) or unit_id("es.municipio", row["mcode"])
            channel = ABROAD if row["mcode"].endswith("999") else 0
            values.append((municipio_set, uid, options[row["key"]], VOTES, row["votes"], channel))
        bad = here.filter(pl.col("voters") != pl.col("cand") + pl.col("invalid") + pl.col("blank"))
        if bad.height:
            accepted.setdefault("accounting.ballots_sum", []).append(f"{mesas_in(bad.height)} {province_name}")
        over = here.filter(pl.col("voters") > pl.col("electors"))
        if over.height:
            accepted.setdefault("accounting.turnout", []).append(f"{mesas_in(over.height)} {province_name}")
    for code, where in accepted.items():
        what = {
            "accounting.ballots_sum": "ballots cast differ from the sum of list votes, null, and blank ballots",
            "accounting.turnout": "ballots cast exceed the electors",
        }[code]
        p.accept(
            code,
            f"In the Junta de Castilla y León's {year} results file, {what} in " + ", ".join(where) + "; the "
            "figures are stored as published, and the municipality sums carry the same differences.",
        )
    p.frame(
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
    return p


def mesas_in(count: int) -> str:
    return f"{count} mesa in" if count == 1 else f"{count} mesas in"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    read_mesas()
    reference()
    elections = [Election(year) for year in YEARS]
    registry(elections).write()
    geo(elections).write()
    for election in elections:
        build(election).write()


def emit() -> None:
    package.run("load", packages())
