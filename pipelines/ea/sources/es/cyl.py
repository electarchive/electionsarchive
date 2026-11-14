import base64
import io
import json
import subprocess
from datetime import date

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "es.cyl"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://analisis.datosabiertos.jcyl.es/api/explore/v2.1/catalog/datasets/"
RESULTS = API + "resultados-electorales-1983-actualidad/exports/csv?delimiter=%3B"
BASICS = API + "datos-basicos-de-elecciones-a-las-cortes/exports/csv?delimiter=%3B"
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


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    units, votes = mesas()
    if (bad := units.filter(pl.col("variants") > 1)).height:
        print(f"{bad.height} mesas with differing accounting across their rows")
    if (twice := votes.filter(pl.col("rows") > 1)).height:
        print(f"{twice.height} mesa and party pairs printed more than once, summed")
    candidacies = votes.group_by(["year", "mesa"]).agg(cand=pl.col("votes").sum())
    units = units.drop("variants").join(candidacies, on=["year", "mesa"])
    units.write_parquet(PARSED / "mesas.parquet")
    votes.drop("rows").write_parquet(PARSED / "mesa_votes.parquet")
    print(
        units.group_by("year", "province")
        .agg(pl.len().alias("mesas"), pl.col("electors", "voters", "cand", "blank", "invalid").sum())
        .sort("year", "province")
    )
