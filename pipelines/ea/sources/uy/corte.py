"""Uruguay's Corte Electoral: every election with open data at the circuit (comisión receptora de votos) level.

The Corte publishes, per election, a file of totals per circuit and a file of the votes each ballot sheet (hoja)
received in each circuit, on the national open-data catalog. A circuit is the finest reporting unit.
"""

import functools
import io
import json
import re
import subprocess
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "uy.corte"
CATALOG = "https://catalogodatos.gub.uy/dataset/"
URUGUAY = str(uuid5(package.NAMESPACE, "polity\x1fUY"))
LICENSE = "Licencia de Datos Abiertos de Uruguay"
LICENSE_URL = (
    "https://www.gub.uy/agencia-gobierno-electronico-sociedad-informacion-conocimiento/sites/"
    "agencia-gobierno-electronico-sociedad-informacion-conocimiento/files/documentos/publicaciones/"
    "licencia_de_datos_abiertos_0.pdf"
)

DEPARTMENTS = {
    "AR": "Artigas",
    "CA": "Canelones",
    "CL": "Cerro Largo",
    "CO": "Colonia",
    "DU": "Durazno",
    "FD": "Florida",
    "FS": "Flores",
    "LA": "Lavalleja",
    "MA": "Maldonado",
    "MO": "Montevideo",
    "PA": "Paysandú",
    "RN": "Río Negro",
    "RO": "Rocha",
    "RV": "Rivera",
    "SA": "Salto",
    "SJ": "San José",
    "SO": "Soriano",
    "TA": "Tacuarembó",
    "TT": "Treinta y Tres",
}

# Resource URLs on the open-data catalog, by election date.
# totals: ballots per circuit; breakdown: votes per ballot sheet and circuit; municipal: the municipal breakdown when
# the Corte publishes it as its own file.
FILES: dict[str, dict[str, str]] = {
    "2014-10-26": {
        "dataset": "corte-electoral-elecciones_nacionales_2014",
        "totals": "1d025bf7-5b0d-42c5-8a9a-0696e24f7634/resource/1e851dea-cd81-4ca5-82d2-55fa8920fc1f/download/totales-generales-por-comision-receptora-de-votos.csv",
        "breakdown": "1d025bf7-5b0d-42c5-8a9a-0696e24f7634/resource/91bc8eeb-227a-47a3-96e9-c2fe4e4a3e1c/download/desglose-de-votos.csv",
    },
    "2019-10-27": {
        "dataset": "corte-electoral-elecciones-nacionales-2019",
        "totals": "7c3caf30-b59e-43de-b928-547030cc682b/resource/4e6c2564-a178-4802-b7ed-59be68485146/download/totales-generales-por-comision-receptora-de-votos.csv",
        "breakdown": "7c3caf30-b59e-43de-b928-547030cc682b/resource/fae2be5e-44db-4763-8b32-9c18679d3aad/download/desglose-de-votos.csv",
    },
    "2024-10-27": {
        "dataset": "corte-electoral-elecciones-nacionales-2024",
        "totals": "7aaa1ff0-8ea5-4302-a3e6-c2d589b484a3/resource/0f149bb6-4fff-463b-bc3a-8ab671bcdf34/download/totales-generales-por-comision-receptora-de-votos-y-plebiscitos-constitucionales.csv",
        "breakdown": "7aaa1ff0-8ea5-4302-a3e6-c2d589b484a3/resource/f74aa981-47d3-4165-81be-5d2abcc5f6d5/download/desglose-de-votos.csv",
    },
    "2014-11-30": {
        "dataset": "corte-electoral-balotaje_2014",
        "totals": "6455b729-be2e-47d7-9205-b9355a593252/resource/5a0b3b75-890b-401e-a44a-08546a043576/download/balotaje-2014.csv",
    },
    "2019-11-24": {
        "dataset": "corte-electoral-balotaje_2019",
        "totals": "8a20dcc5-77c7-499d-9932-5d0ebea4df37/resource/a71df2ea-b075-40e7-80b5-1df4c781bb99/download/balotaje-2019.csv",
    },
    "2024-11-24": {
        "dataset": "corte-electoral-balotaje_2024",
        "totals": "2dc312e4-f3f7-41aa-b56d-74389083e7e1/resource/ac4cbd7c-dc15-4d3f-87ba-c00fb47604b7/download/balotaje-2024.csv",
    },
    "2019-06-30": {
        "dataset": "corte-electoral-elecciones_internas_2019",
        "totals": "e749ad5e-ef5a-4f56-9b16-96868cbfa460/resource/ec401b7f-bd99-43c7-aed8-e88fc3775923/download/totales-generales-por-comisin-receptora-de-votos.csv",
        "breakdown": "e749ad5e-ef5a-4f56-9b16-96868cbfa460/resource/912bcb58-3b3a-4a4c-aa5d-e633c87fcb97/download/desglose-de-votos-.csv",
    },
    "2024-06-30": {
        "dataset": "corte-electoral-elecciones-internas-de-los-partidos-politicos-2024",
        "totals": "48a4e7f5-6909-41a0-aa8a-95dc42cc201f/resource/f7dd08cf-5978-43c2-acc5-b2854d7029dd/download/totales-generales.csv",
        "breakdown": "48a4e7f5-6909-41a0-aa8a-95dc42cc201f/resource/dec32eb5-1754-4810-af7a-8c1121259206/download/desglose-de-votos.csv",
    },
    "2020-09-27": {
        "dataset": "corte-electoral-eleccionesdepartamentales2020",
        "totals": "e2ae6343-352d-4468-8343-12b7d8837056/resource/b195ebdb-23fa-40e1-8380-aa020d10011b/download/totales-generales-por-comisin-receptora-de-votos.csv",
        "breakdown": "e2ae6343-352d-4468-8343-12b7d8837056/resource/39ecc252-cd4f-49a6-9617-0863023a65cb/download/desglose-de-votos-eleccin-departamental.csv",
        "municipal": "e2ae6343-352d-4468-8343-12b7d8837056/resource/c61c004d-265a-4c1c-8106-c4ea12f1efa8/download/desglose-de-votos-eleccin-municipal.csv",
    },
    "2025-05-11": {
        "dataset": "corte-electoral-elecciones_departamentales_y_municipales_2025",
        "totals": "2d5eb299-0d95-478b-9271-c62f601e7bc1/resource/a27ceda4-4486-4329-9e67-b7c36e9dfa59/download/totales-generales-por-comision-receptora-de-votos.csv",
        "breakdown": "2d5eb299-0d95-478b-9271-c62f601e7bc1/resource/92d7ca7b-32f1-4cde-b607-d94aef825c4d/download/desglose-de-votos.csv",
    },
}
GUB = "https://www.gub.uy/corte-electoral/"


@dataclass(frozen=True)
class Export:
    """An election published on the Corte's own site as XLSX exports inside zip downloads.

    The exports hold the votes of each ballot sheet in each circuit. `members` names the files to read as
    (zip, file name inside it); `official` is the Corte's summary workbook of department and municipality totals.
    """

    pages: tuple[str, ...]
    zips: dict[str, str]
    members: dict[str, tuple[str, str]]


XLSX: dict[str, Export] = {
    "2015-05-10": Export(
        pages=(),
        zips={"local": f"{GUB}download/node/field_documento/683"},
        members={
            "breakdown": ("local", "RESULTADO DEPTAL 2015.xlsx"),
            "municipal": ("local", "resultado_por_crv_municipal_todo_el_pais CONVERT XLSX.xlsx"),
            "official": ("local", "Deptales 2015.xlsx"),
        },
    ),
    "2014-06-01": Export(
        pages=(
            f"{GUB}datos-y-estadisticas/estadisticas/resultados-elecciones-internas-del-ano-2014",
            f"{GUB}comunicacion/publicaciones/resultados-elecciones-internas-organo-deliberativo-nacional-circuito",
            f"{GUB}comunicacion/publicaciones/resultados-elecciones-internas-organo-deliberativo-departamental",
        ),
        zips={
            "odn": f"{GUB}download/node/field_documento/482",
            "odd": f"{GUB}download/node/field_documento/493",
        },
        members={},
    ),
}
NATIONAL = ["2014-10-26", "2019-10-27", "2024-10-27"]
RUNOFF = ["2014-11-30", "2019-11-24", "2024-11-24"]
PRIMARY = ["2014-06-01", "2019-06-30", "2024-06-30"]
LOCAL = ["2015-05-10", "2020-09-27", "2025-05-11"]
# The referendum against 135 articles of Law 19.889 (2022-03-27), whose circuit file is on the open-data catalog, and the
# 2019-08-04 act in which voters adhered to a referendum against Law 19.684, whose results the Corte's own site
# publishes as a zip holding a workbook per circuit and a PDF per department.
REFERENDUM_DAY = "2022-03-27"
ADHESION_DAY = "2019-08-04"
REFERENDUM = {
    "dataset": "corte-electoral-referendum2022",
    "circuits": "55b88af7-9364-4735-aad9-b5db5f49d828/resource/0e15bff1-67ae-48af-9e03-4880395ac59e/download/"
    "referndum-contra-135-artculos-de-la-ley-19.889.csv",
}
ADHESION = {
    "page": f"{GUB}comunicacion/publicaciones/acto-adhesion-interposicion-del-recurso-referendum-contra-ley-n-19684",
    "zip": f"{GUB}download/node/field_documento/503",
    "circuits": "adhesion_final_cto.xlsx",
    "departments": "areporteresultadosadhesion2019.pdf",
}
DAYS = sorted(FILES)
XDAYS = sorted(XLSX)
OTHER = [ADHESION_DAY, REFERENDUM_DAY]
ALL = sorted([*DAYS, *XDAYS, *OTHER])
KIND_PATH = {**dict.fromkeys(NATIONAL, "general"), **dict.fromkeys(RUNOFF, "runoff")}
KIND_PATH |= {ADHESION_DAY: "adhesion", REFERENDUM_DAY: "referendum"}
KIND_PATH |= {**dict.fromkeys(PRIMARY, "primary"), **dict.fromkeys(LOCAL, "local")}

# The two tickets in each second round: the totals column, the ticket's label, and the party that nominated it.
# `elected` is the ticket that the second round's own totals show ahead; it is checked against the totals when built.
RUNOFF_TICKETS: dict[str, tuple[list[tuple[str, str, str]], str]] = {
    "2014-11-30": (
        [
            ("TOTALVAZQUEZSENDIC", "Vázquez – Sendic", "FRENTE AMPLIO"),
            ("TOTALLACALLEPOULARRANAGA", "Lacalle Pou – Larrañaga", "NACIONAL"),
        ],
        "FRENTE AMPLIO",
    ),
    "2019-11-24": (
        [
            ("TOTALMARTINEZVILLAR", "Martínez – Villar", "FRENTE AMPLIO"),
            ("TOTALLACALLEPOUARGIMON", "Lacalle Pou – Argimón", "NACIONAL"),
        ],
        "NACIONAL",
    ),
    "2024-11-24": (
        [
            ("TOTALORSICOSSE", "Orsi – Cosse", "FRENTE AMPLIO"),
            ("TOTALDELGADORIPOLL", "Delgado – Ripoll", "NACIONAL"),
        ],
        "FRENTE AMPLIO",
    ),
}
# Files of the Corte's own statistics pages that count the "Papeleta por SI" ballots of the 2014 and 2019 plebiscites:
# the 2014 zip holds a workbook with the count per department, and the 2019 PDF has one row per circuit.
PLEBISCITE_FILES = {
    "2014-10-26": "https://www.gub.uy/corte-electoral/download/node/field_documento/852",
    "2019-10-27": "https://www.gub.uy/corte-electoral/sites/corte-electoral/files/2024-04/"
    "RESULTADOS_PLEBISCITO_27_10_2019_POR_CIRCUITO_compressed%20%281%29.pdf",
}
PLEBISCITE_SUFFIX = {"2014-10-26": ".zip", "2019-10-27": ".pdf"}
PLEBISCITE_TITLES = {
    "2014-10-26": (
        "Plebiscite on lowering the age of criminal responsibility",
        "Plebiscito por la baja de la edad de imputabilidad",
    ),
    "2019-10-27": (
        "Plebiscite on a constitutional reform on security",
        "Plebiscito de reforma constitucional en materia de seguridad",
    ),
}
# The 2024 constitutional plebiscites, by the totals column that counts their "Sí" ballots.
PLEBISCITES = {
    "2024-10-27": [
        (
            "SIART11",
            "Plebiscite on night-time raids (Article 11)",
            "Plebiscito sobre allanamientos nocturnos (art. 11)",
        ),
        ("SIART67", "Plebiscite on social security (Article 67)", "Plebiscito sobre seguridad social (art. 67)"),
    ]
}
SITE = "https://www.gub.uy/corte-electoral/sites/corte-electoral/files/"
# The Corte's "resultados y cargos" workbook of an election: the party totals and the seats each party won.
SUMMARY = {"2014-10-26": f"{SITE}2024-04/Nacionales%202014.xlsx"}
# The Corte's 2019 results: the zip of its results site's JSON files (the country totals per lema and the blank and
# annulled ballots), and the minutes in which it proclaimed the Senate and the Chamber of Representatives. The minutes
# are scanned documents, so the seats are transcribed in SEATS_2019 and checked against their own totals.
RESULTS_JSON = {"2019-10-27": (f"{GUB}download/node/field_documento/1141", "ResumenGeneral_D.json")}
PROCLAMATION = {
    "2019-10-27": {
        "senate": f"{GUB}download/node/field_documento/135",
        "chamber": f"{GUB}download/node/field_documento/137",
    }
}
OFFICIAL = sorted([*SUMMARY, *RESULTS_JSON])
# Seats of the 2019 Senate (Acta 10035) and, by department, of the Chamber of Representatives (Acta 10037): the seats the
# departmental boards gave by whole quotient plus the seats given by decreasing quotients, as the Corte proclaimed them.
FA, PN, PC, CA_, PI, PERI, PG = (
    "FRENTE AMPLIO",
    "NACIONAL",
    "COLORADO",
    "CABILDO ABIERTO",
    "INDEPENDIENTE",
    "ECOLOGISTA RADICAL INTRANSIGENTE",
    "DE LA GENTE",
)
SEATS_2019: tuple[dict[str, int], dict[str, dict[str, int]]] = (
    {FA: 13, PN: 10, PC: 4, CA_: 3},
    {
        "MO": {FA: 19, PN: 9, PC: 5, CA_: 4, PERI: 1, PI: 1},
        "CA": {FA: 7, PN: 4, PC: 2, CA_: 2},
        "MA": {FA: 1, PN: 2, PC: 1, CA_: 1},
        "RO": {FA: 1, PN: 1},
        "TT": {FA: 1, PN: 1},
        "CL": {FA: 1, PN: 1},
        "RV": {PN: 1, PC: 1, CA_: 1},
        "AR": {FA: 1, PN: 1},
        "SA": {FA: 1, PC: 2, CA_: 1},
        "PA": {FA: 1, PN: 1, PC: 1},
        "RN": {FA: 1, PN: 1},
        "SO": {FA: 1, PN: 1},
        "CO": {FA: 1, PN: 1, PC: 1, PG: 1},
        "SJ": {FA: 1, PN: 1, CA_: 1},
        "FS": {FA: 1, PN: 1},
        "FD": {FA: 1, PN: 1},
        "DU": {FA: 1, PN: 1},
        "LA": {FA: 1, PN: 1},
        "TA": {FA: 1, PN: 1, CA_: 1},
    },
)
# The lemas' seats in the Chamber, as the minutes total them.
CHAMBER_TOTALS_2019 = {FA: 42, PN: 30, PC: 13, CA_: 11, PI: 1, PERI: 1, PG: 1}
CHAMBER_SIZE = {"MO": 39, "CA": 15, "MA": 5, "SA": 4, "CO": 4, "RV": 3, "PA": 3, "SJ": 3, "TA": 3}
# The Corte's "integración de hojas" of the primaries: the precandidate and the members of every ballot sheet.
INTEGRATION = {
    "2019-06-30": "e749ad5e-ef5a-4f56-9b16-96868cbfa460/resource/edfdec16-cf84-4b34-a59f-31615228d3c6/download/"
    "integracin-de-las-hojas-de-votacin-.csv",
    "2024-06-30": "48a4e7f5-6909-41a0-aa8a-95dc42cc201f/resource/68123598-1699-4049-a510-d3aafbb112d3/download/"
    "integracion-hojas-de-votacion.xlsx",
}
# The party abbreviations of the summary workbook, by lema key.
ABBREVIATIONS = {
    "F.A.": "FRENTE AMPLIO",
    "P.N.": "NACIONAL",
    "P.C.": "COLORADO",
    "P.I.": "INDEPENDIENTE",
    "A.P.": "ASAMBLEA POPULAR",
    "P.T.": "DE LOS TRABAJADORES",
    "P.E.R.I.": "ECOLOGISTA RADICAL INTRANSIGENTE",
}
LOWER = {"de", "la", "los", "las", "del", "y", "el"}
BREAKDOWN_METHOD = (
    "Counted by the Corte Electoral per circuit (comisión receptora de votos), as published in the national open-data "
    "catalog. Each circuit is one reporting unit and sits in its department. The breakdown file counts the ballots "
    "of voters who voted in their own circuit; ballots cast in a circuit by voters registered elsewhere are 'observed' "
    "ballots, which the totals file counts but the breakdown does not split by ballot sheet. Registered voters are the "
    "electoral roll of the circuit, ballots cast are the ballots in the box (observed ones included), and observed, "
    "invalid, and blank ballots are stored as published. Valid votes are not published per circuit, so none are stored."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


UY_SOURCE = key("source", SOURCE)
NATION = key("area", "uy.nacion", "UY")
SENATE_BODY = key("body", "senate")
CHAMBER_BODY = key("body", "chamber")
PRESIDENCY = key("office", "president")
SYSTEM_PRESIDENT = key("electoral_system", "president", 1997)


def url(path: str) -> str:
    return path if path.startswith("https://") else f"{CATALOG}{path}"


def files(day: str) -> dict[str, str]:
    return {name: url(path) for name, path in FILES[day].items() if name != "dataset"}


def documents(day: str) -> dict[str, str]:
    """Every file an election's package cites: its data files, its seats workbook, and its sheet integration."""
    found = files(day)
    if day in SUMMARY:
        found["summary"] = url(SUMMARY[day])
    if day in RESULTS_JSON:
        found["results"] = RESULTS_JSON[day][0]
        found.update({f"proclamation-{name}": link for name, link in PROCLAMATION[day].items()})
    if day in INTEGRATION:
        found["integration"] = url(INTEGRATION[day])
    return found


def cited(day: str) -> list[str]:
    """Every raw file an election's package cites."""
    if day == REFERENDUM_DAY:
        return [f"{CATALOG}{REFERENDUM['dataset']}", url(REFERENDUM["circuits"])]
    if day == ADHESION_DAY:
        return [ADHESION["page"], ADHESION["zip"]]
    return xlsx_links(day) if day in XLSX else list(documents(day).values())


def packages() -> list[str]:
    return ["uy.corte/geo", "uy.corte/registry", *(f"uy.corte/{day}" for day in ALL)]


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, f"{CATALOG}{FILES[day]['dataset']}", suffix=".html")
        for link in documents(day).values():
            suffix = ".zip" if "/download/node/" in link else ".xlsx" if link.endswith(".xlsx") else ".csv"
            raw.fetch(SOURCE, link, suffix=suffix)
    for day, link in PLEBISCITE_FILES.items():
        raw.fetch(SOURCE, link, suffix=PLEBISCITE_SUFFIX[day])
    for day in XDAYS:
        fetch_xlsx(day)
    raw.fetch(SOURCE, f"{CATALOG}{REFERENDUM['dataset']}", suffix=".html")
    raw.fetch(SOURCE, url(REFERENDUM["circuits"]), suffix=".csv")
    raw.fetch(SOURCE, ADHESION["page"], suffix=".html")
    raw.fetch(SOURCE, ADHESION["zip"], suffix=".zip")


def parse() -> None:
    geo().write()
    registry().write()
    for day in ALL:
        election(day).write()


def xlsx_links(day: str) -> list[str]:
    """The pages and zip downloads that an XLSX election cites."""
    export = XLSX[day]
    return [*export.pages, *export.zips.values()]


def fetch_xlsx(day: str) -> None:
    export = XLSX[day]
    for link in export.pages:
        raw.fetch(SOURCE, link, suffix=".html")
    for link in export.zips.values():
        raw.fetch(SOURCE, link, suffix=".zip")


def zip_files(day: str, name: str) -> dict[str, bytes]:
    """The XLSX files inside one of an election's zips, by file name."""
    with zipfile.ZipFile(raw.cached(SOURCE, XLSX[day].zips[name]).path) as archive:
        return {
            member: archive.read(member) for member in sorted(archive.namelist()) if member.lower().endswith(".xlsx")
        }


def member(day: str, name: str) -> bytes:
    zip_name, file = XLSX[day].members[name]
    return zip_files(day, zip_name)[file]


def sheet(data: bytes) -> pl.DataFrame:
    """The first sheet of a Corte XLSX export below its header block, with every column as text."""
    book = fastexcel.read_excel(data)
    head = book.load_sheet_by_idx(0, header_row=None, n_rows=20, dtypes="string").to_polars()
    start = next(index for index, row in enumerate(head.iter_rows()) if row[0] == "ACTO")
    frame = book.load_sheet_by_idx(0, header_row=start, dtypes="string").to_polars()
    return frame.with_columns(pl.col(pl.String).str.strip_chars()).filter(pl.col("CIRCUITO").is_not_null())


def emit() -> None:
    package.run("load", packages())


def norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", text).upper())


def table(link: str) -> pl.DataFrame:
    data = raw.cached(SOURCE, link).path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    frame = pl.read_csv(io.StringIO(text), infer_schema_length=0)
    frame = frame.rename({column: norm(column) for column in frame.columns})
    return frame.with_columns(pl.col(pl.String).str.strip_chars())


def lema_key(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    plain = re.sub(r"\(.*?\)", "", plain)
    plain = re.sub(r"\s+-\s+[A-Z]+$", "", plain)
    plain = re.sub(r"^PARTIDO ", "", plain.strip())
    return " ".join(plain.split())


def lema_name(text: str) -> str:
    text = re.sub(r"\s+-\s+PDU$", "", text.strip())
    if text.isupper():
        text = " ".join(w.lower() if w.lower() in LOWER and i else w.capitalize() for i, w in enumerate(text.split()))
    return text


def circuit(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col("CRV").cast(pl.Int64).cast(pl.String))


def breakdown(day: str, name: str = "breakdown") -> pl.DataFrame:
    frame = circuit(table(files(day)[name]))
    return frame.with_columns(
        pl.col("CANTIDADVOTOS").cast(pl.Int64),
        pl.col("LEMA").map_elements(lema_key, return_dtype=pl.String).alias("LEMAKEY"),
    )


def totals(day: str) -> pl.DataFrame:
    return circuit(table(files(day)["totals"]))


# The XLSX exports of some elections lost their accented letters ("Paysand", "Tacuaremb"), so a department matches
# on its name with or without them.
DEPARTMENT_KEYS = {
    norm(variant): code for code, text in DEPARTMENTS.items() for variant in (text, re.sub(r"[^\x00-\x7f]", "", text))
}


def department(text: str) -> str:
    return DEPARTMENT_KEYS[norm(text)]


def listing(day: str, name: str = "breakdown") -> pl.DataFrame:
    """A departmental or municipal breakdown in the shape of the open-data CSVs: DEPARTAMENTO, CRV, TIPOREGISTRO,
    DESCRIPCION1 (the sheet), DESCRIPCION2 (the municipality), LEMA, LEMAKEY, and CANTIDADVOTOS."""
    if day not in XLSX:
        return breakdown(day, name)
    municipal = name == "municipal"
    frame = sheet(member(day, name))
    codes = {text: department(text) for text in frame["DEPTO"].unique().to_list()}
    return frame.select(
        DEPARTAMENTO=pl.col("DEPTO").replace_strict(codes),
        CRV=pl.col("CIRCUITO").cast(pl.Int64).cast(pl.String),
        TIPOREGISTRO=pl.lit("HOJA_EM" if municipal else "HOJA_ED"),
        DESCRIPCION1="HOJA",
        DESCRIPCION2=pl.col("MUNICIPIO") if municipal else pl.lit(None, pl.String),
        LEMA="LEMA",
        LEMAKEY=pl.col("LEMA").map_elements(lema_key, return_dtype=pl.String),
        CANTIDADVOTOS=pl.col("CNT_VOTOS").cast(pl.Int64),
    )


def has(day: str, name: str) -> bool:
    return name in (XLSX[day].members if day in XLSX else FILES[day])


# The 2014 primaries' party of each export, by the Corte's convocatoria with accents and punctuation dropped.
PRIMARY_LEMAS = {
    "FRENTEAMPLIO": "FRENTE AMPLIO",
    "NACIONAL": "NACIONAL",
    "COLORADO": "COLORADO",
    "ASAMBLEAPOPULAR": "ASAMBLEA POPULAR",
    "DELACONCERTACIN": "DE LA CONCERTACION",
    "DELACONCERTACION": "DE LA CONCERTACION",
    "INDEPENDIENTE": "INDEPENDIENTE",
    "DELOSTRABAJADORES": "DE LOS TRABAJADORES",
    "PERI": "ECOLOGISTA RADICAL INTRANSIGENTE",
    "UNIDOS": "UNIDOS",
    "UNIONPARAELCAMBIO": "UNION PARA EL CAMBIO",
}
# Parties that run only in the XLSX elections, as the Corte's own page names them.
XLSX_NAMES = {"UNIDOS": "Unidos", "UNION PARA EL CAMBIO": "Unión para el Cambio"}


@functools.cache
def names() -> dict[str, str]:
    found: dict[str, str] = {}
    for day in DAYS:
        if "breakdown" not in FILES[day]:
            continue
        for name in ("breakdown", "municipal"):
            if name in FILES[day]:
                for text in table(files(day)[name])["LEMA"].unique().to_list():
                    found[lema_key(text)] = lema_name(text)
    for tickets, _ in RUNOFF_TICKETS.values():
        for _, _, lema in tickets:
            found.setdefault(lema, lema_name(lema))
    for lema, text in XLSX_NAMES.items():
        found.setdefault(lema, text)
    return found


def party(lema: str) -> str:
    return key("party", lema)


def summary(day: str) -> list[list[str]]:
    """The cells of the Corte's results-and-seats workbook, row by row, without the empty ones."""
    path = raw.cached(SOURCE, url(SUMMARY[day])).path
    sheet = fastexcel.read_excel(str(path)).load_sheet(0, header_row=None, dtypes="string").to_polars()
    rows = ([cell.strip() for cell in row if cell is not None and cell.strip()] for row in sheet.iter_rows())
    return [row for row in rows if row]


def section(rows: list[list[str]], number: int) -> list[list[str]]:
    """The rows of one numbered section, after its heading and before the next one."""
    start = next(i for i, row in enumerate(rows) if row and row[0].startswith(f"{number})"))
    end = next((i for i in range(start + 1, len(rows)) if rows[i] and re.match(r"\d+\)", rows[i][0])), len(rows))
    return rows[start + 1 : end]


def seat_row(parties: list[str], cells: list[str]) -> dict[str, int]:
    """The seats of one row: a count per party and a final total that must match their sum."""
    seats = [int(cell) for cell in cells]
    if len(seats) != len(parties) + 1 or sum(seats[:-1]) != seats[-1]:
        raise ValueError(f"the seats {cells} do not add up across {parties}")
    return {ABBREVIATIONS[party]: n for party, n in zip(parties, seats, strict=False)}


def declared_json(day: str) -> tuple[dict[str, int], int, int]:
    """The country totals of the Corte's results site: the zip inside the download holds the departmental summary."""
    link, member = RESULTS_JSON[day]
    with zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, link).path.read_bytes())) as outer:
        inner = next(name for name in outer.namelist() if name.endswith(".zip"))
        with zipfile.ZipFile(io.BytesIO(outer.read(inner))) as zf:
            found = json.loads(zf.read(member))
    total = found[0]
    if total["DepId"] != "" or total["CircuitosEscrutados"] != total["CircuitosTotal"]:
        raise ValueError(f"{day}: the results file is not the complete count of the country")
    votes = {lema_key(row["LemaNombre"]): int(row["Total"]) for row in total["EleccionNacional"]}
    if sum(votes.values()) != total["TotalVotosALemas"]:
        raise ValueError(f"{day}: the declared lema votes do not add up to the declared total")
    return votes, int(total["TotalEnBlanco"]), int(total["TotalAnulados"])


def declared_votes(day: str) -> tuple[dict[str, int], int, int]:
    """The votes of each lema, and the blank and annulled ballots, that the Corte declares for the whole country."""
    if day in RESULTS_JSON:
        return declared_json(day)
    rows = section(summary(day), 4)
    header = next(row for row in rows if row[0] == "DEPARTAMENTO")
    total = next(row for row in rows if row[0] == "TOTALES")
    parties = [cell if cell.endswith(".") else f"{cell}." for cell in header[1:8]]
    if header[9] != "En Blanco" or header[10] != "Sobres con":
        raise ValueError(f"{day}: the declared totals have another layout: {header}")
    votes = {ABBREVIATIONS[party]: int(cell) for party, cell in zip(parties, total[1:8], strict=True)}
    return votes, int(total[9]), int(total[10])


def declared_seats(day: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """The Senate seats by lema key, and the Chamber seats by department code and lema key, as the Corte declares."""
    if day in RESULTS_JSON:
        return checked_seats(day, *SEATS_2019)
    rows = summary(day)
    senate = section(rows, 7)
    header = next(row for row in senate if row[0] in ABBREVIATIONS)
    total = next(row for row in senate if row[0] == "TOTALES")
    seats = seat_row(header[:-1], total[1:])
    chamber = section(rows, 8)
    header = next(row for row in chamber if row[0] == "DEPARTAMENTO")
    parties = header[1:-1]
    by_name = {norm(text): code for code, text in DEPARTMENTS.items()}
    districts: dict[str, dict[str, int]] = {}
    nation = None
    for row in chamber:
        if row[0] == "TOTALES":
            nation = seat_row(parties, row[1:])
        elif norm(row[0]) in by_name:
            districts[by_name[norm(row[0])]] = seat_row(parties, row[1:])
    if nation is None or set(districts) != set(DEPARTMENTS):
        raise ValueError(f"{day}: the Chamber seats miss a department or the total")
    for lema in nation:
        if sum(found.get(lema, 0) for found in districts.values()) != nation[lema]:
            raise ValueError(f"{day}: the Chamber seats of {lema} do not add up to the declared total")
    return seats, districts


def checked_seats(
    day: str, senate: dict[str, int], chamber: dict[str, dict[str, int]]
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """The transcribed seats, once they agree with the sizes of the bodies and with the minutes' own totals."""
    votes = declared_votes(day)[0]
    if sum(senate.values()) != 30 or set(chamber) != set(DEPARTMENTS):
        raise ValueError(f"{day}: the transcribed seats do not cover the Senate's 30 seats and every department")
    for dept, seats in chamber.items():
        if sum(seats.values()) != CHAMBER_SIZE.get(dept, 2):
            raise ValueError(f"{day}: the seats of {dept} do not add up to its delegation")
    nation = {lema: sum(seats.get(lema, 0) for seats in chamber.values()) for lema in CHAMBER_TOTALS_2019}
    if nation != CHAMBER_TOTALS_2019 or sum(nation.values()) != 99:
        raise ValueError(f"{day}: the Chamber seats do not add up to the proclaimed totals: {nation}")
    for lema in {*senate, *nation}:
        if lema not in votes:
            raise ValueError(f"{day}: {lema} won seats but has no declared votes")
    return senate, chamber


@functools.cache
def municipalities() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for day in LOCAL:
        for name in ("breakdown", "municipal"):
            if not has(day, name):
                continue
            frame = (listing(day, name) if day in XLSX else table(files(day)[name])).filter(
                pl.col("TIPOREGISTRO") == "HOJA_EM"
            )
            frame = frame.with_columns(pl.col("DESCRIPCION1").str.extract(r"-(\S+)$", 1).alias("LETTER"))
            for dept, letter, text in frame.select("DEPARTAMENTO", "LETTER", "DESCRIPCION2").unique().iter_rows():
                if letter:
                    found[(dept, letter)] = lema_name(text) if text.isupper() else text
    return found


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=UY_SOURCE,
        kind="official_body",
        publisher="Corte Electoral",
        title="Resultados electorales: totales y desglose de votos por circuito (catálogo nacional de datos abiertos)",
        url="https://www.gub.uy/corte-electoral/datos-y-estadisticas/datos-abiertos",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Corte Electoral de Uruguay",
    )


def geo() -> Package:
    p = Package(
        "uy.corte/geo",
        SOURCE,
        "Uruguay's 19 departments and the municipalities and circuits that the Corte Electoral reports votes for",
    )
    p.external.add(URUGUAY)
    for code, rank, kind, noun in (
        ("uy.nacion", 0, "administrative", "country"),
        ("uy.departamento", 1, "administrative", "department"),
        ("uy.municipio", 2, "administrative", "municipality"),
        ("uy.circuito", 3, "precinct", "circuit"),
    ):
        p.add("scheme", code=code, polity_id=URUGUAY, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NATION, scheme_code="uy.nacion")
    p.add("area_code", area_id=NATION, code="UY")
    p.name(NATION, "Uruguay", "es", "official")
    p.name(NATION, "Uruguay", "en", "official", False)
    for code, text in DEPARTMENTS.items():
        id = key("area", "uy.departamento", code)
        p.add("area", id=id, scheme_code="uy.departamento")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, text, "es", "official")
    for (dept, letter), text in sorted(municipalities().items()):
        id = key("area", "uy.municipio", dept, letter)
        p.add("area", id=id, scheme_code="uy.municipio")
        p.add("area_code", area_id=id, code=f"{dept}-{letter}")
        p.add("area_parent", area_id=id, parent_id=key("area", "uy.departamento", dept))
        p.name(id, text, "es", "official")
    return p


def registry() -> Package:
    p = Package(
        "uy.corte/registry",
        SOURCE,
        "Uruguayan lemas (parties), the Senate, the Chamber of Representatives, and the presidency from the Corte "
        "Electoral",
        depends=["uy.corte/geo"],
    )
    p.external.add(URUGUAY)
    source_row(p)
    for day in DAYS:
        p.artifact(raw.cached(SOURCE, f"{CATALOG}{FILES[day]['dataset']}"), UY_SOURCE)
        for link in documents(day).values():
            p.artifact(raw.cached(SOURCE, link), UY_SOURCE)
        if day in PLEBISCITE_FILES:
            p.artifact(raw.cached(SOURCE, PLEBISCITE_FILES[day]), UY_SOURCE)
    for day in XDAYS:
        for link in xlsx_links(day):
            p.artifact(raw.cached(SOURCE, link), UY_SOURCE)
    for code, text in (
        (CHAMBER_BODY, "Chamber of Representatives"),
        (SENATE_BODY, "Senate"),
    ):
        p.add("body", id=code, polity_id=URUGUAY, kind="lower_house" if code == CHAMBER_BODY else "upper_house")
        p.name(code, text, "en", "official")
    p.name(CHAMBER_BODY, "Cámara de Representantes", "es", "native", False)
    p.name(SENATE_BODY, "Cámara de Senadores", "es", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=URUGUAY, kind="head_of_state")
    p.name(PRESIDENCY, "President of Uruguay", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    for dept, text in DEPARTMENTS.items():
        id = key("office", "intendente", dept)
        p.add("office", id=id, polity_id=URUGUAY, kind="governor")
        p.name(id, f"Intendente of {text}", "en", "official")
        p.name(id, f"Intendente de {text}", "es", "native", False)
        council = key("body", "junta", dept)
        p.add("body", id=council, polity_id=URUGUAY, kind="council")
        p.name(council, f"Departmental Board of {text}", "en", "official")
        p.name(council, f"Junta Departamental de {text}", "es", "native", False)
    for (dept, letter), text in sorted(municipalities().items()):
        id = key("office", "alcalde", dept, letter)
        p.add("office", id=id, polity_id=URUGUAY, kind="mayor")
        p.name(id, f"Mayor of {text}, {DEPARTMENTS[dept]}", "en", "official")
        p.name(id, f"Alcalde de {text}, {DEPARTMENTS[dept]}", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM_PRESIDENT,
        polity_id=URUGUAY,
        valid_from=date(1997, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM_PRESIDENT),
        entity_id=SYSTEM_PRESIDENT,
        kind="methodology",
        body_md="A ticket that wins an absolute majority of valid votes in the national election is elected; "
        "otherwise the two leading tickets go to a second round in November.",
        lang="en",
    )
    for lema, text in sorted(names().items()):
        id = party(lema)
        p.add("party", id=id, kind="party", polity_id=URUGUAY)
        p.add("external_id", entity_id=id, scheme="uy.corte.lema", value=lema)
        p.name(id, text, "es", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------


class Builder:
    """One election's package, with its circuits as reporting units and its totals as unit accounting."""

    def __init__(self, day: str, summary: str, circuits: pl.DataFrame | None = None) -> None:
        self.day = day
        self.p = Package(f"uy.corte/{day}", SOURCE, summary, depends=["uy.corte/geo", "uy.corte/registry"])
        self.p.external.add(URUGUAY)
        for link in cited(day):
            if day in OTHER:
                self.p.artifact(raw.cached(SOURCE, link), UY_SOURCE)
            else:
                self.p.uses(raw.cached(SOURCE, link))
        if day in PLEBISCITE_FILES:
            self.p.uses(raw.cached(SOURCE, PLEBISCITE_FILES[day]))
        self.path = f"uy/{day}-{KIND_PATH[day]}"
        # The circuit totals; `measured` is false where the Corte publishes the votes of each sheet and no totals.
        self.measured = circuits is not None or day not in XLSX
        self.totals = (
            circuits
            if circuits is not None
            else totals(day)
            if day not in XLSX
            else pl.DataFrame(schema={"DEPARTAMENTO": pl.String, "CRV": pl.String})
        )
        self.units: dict[tuple[str, str], str] = {}
        self.sets: list[str] = []
        self.slugs: set[str] = set()

    def unit(self, dept: str, crv: str) -> str:
        found = self.units.get((dept, crv))
        if found is None:
            found = self.units[(dept, crv)] = key("unit", self.day, dept, crv)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=URUGUAY,
                scheme_code="uy.circuito",
                code=f"{self.day}:{dept}:{crv}",
                area_relation="within",
                area_id=key("area", "uy.departamento", dept),
                kind="standard",
            )
        return found

    def add_units(self, frame: pl.DataFrame) -> None:
        for dept, crv in frame.select("DEPARTAMENTO", "CRV").unique().sort("DEPARTAMENTO", "CRV").iter_rows():
            self.unit(dept, crv)

    def unit_frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            [(d, c, u) for (d, c), u in self.units.items()],
            schema={"DEPARTAMENTO": pl.String, "CRV": pl.String, "UNIT": pl.String},
            orient="row",
        )

    def accounting(self) -> pl.DataFrame:
        """The circuit totals in long form: DEPARTAMENTO, CRV, MEASURE, VALUE."""
        measures: dict[str, int] = {}
        for column in self.totals.columns:
            if column.startswith("TOTALHABILITADOS"):
                measures[column] = 2
            elif column == "TOTALVOTOSEMITIDOS":
                measures[column] = 3
            elif column == "TOTALVOTOSOBSERVADOS":
                measures[column] = 15
            elif column in ("TOTALANULADOS", "TOTALVOTOSANULADOS"):
                measures[column] = 5
            elif column in ("TOTALENBLANCO", "TOTALVOTOSENBLANCO"):
                measures[column] = 6
        found = self.totals.select(
            "DEPARTAMENTO",
            "CRV",
            *(pl.col(column).cast(pl.Int64).alias(str(measure)) for column, measure in measures.items()),
        )
        return found.unpivot(index=["DEPARTAMENTO", "CRV"], variable_name="MEASURE", value_name="VALUE").with_columns(
            pl.col("MEASURE").cast(pl.Int64)
        )

    def election(self, kind: str, en: str, es: str) -> str:
        election = key("election", self.day)
        day = date.fromisoformat(self.day)
        self.p.add("election", id=election, polity_id=URUGUAY, kind=kind, date_from=day, date_to=day, status="held")
        self.p.name(election, en, "en", "official")
        self.p.name(election, es, "es", "native", False)
        self.p.add("slug", path=self.path, entity_id=election, is_current=True)
        self.election_id = election
        return election

    def contest(
        self,
        tag: str,
        kind: str,
        title: str,
        slug: str,
        *,
        office: str | None = None,
        body: str | None = None,
        district: str = NATION,
        magnitude: int | None = None,
        completeness: float = 1.0,
        round_number: int | None = None,
        previous: str | None = None,
        system: str | None = None,
        es: str | None = None,
    ) -> tuple[str, str]:
        contest = key("contest", self.day, tag)
        self.p.add(
            "contest",
            id=contest,
            election_id=self.election_id,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=magnitude,
            round_number=round_number,
            previous_round_id=previous,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        self.p.name(contest, title, "en", "official")
        if es:
            self.p.name(contest, es, "es", "native", False)
        path = f"{self.path}/{changeset.slug(slug)}"
        if path in self.slugs:
            raise ValueError(f"duplicate contest slug {path}")
        self.slugs.add(path)
        self.p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = key("result_set", self.day, tag)
        self.sets.append(result_set)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=self.election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=UY_SOURCE,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        self.p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=BREAKDOWN_METHOD,
            lang="en",
        )
        return contest, result_set

    def option(
        self,
        contest: str,
        kind: str,
        label: str,
        *parts: object,
        lema: str | None = None,
        parent: str | None = None,
        position: int | None = None,
    ) -> str:
        option = key("option", self.day, contest, *parts)
        self.p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind=kind,
            parent_option_id=parent,
            party_id=party(lema) if lema is not None else None,
            ballot_position=position,
            ballot_label=label,
        )
        return option

    def values(self, result_set: str, frame: pl.DataFrame) -> None:
        """Stage values from a frame with UNIT, OPTION (null for unit accounting), MEASURE, and VALUE columns."""
        self.p.frame(
            "result_value",
            frame.group_by("UNIT", "OPTION", "MEASURE", maintain_order=True)
            .agg(pl.col("VALUE").sum())
            .select(
                result_set_id=pl.lit(result_set),
                unit_id="UNIT",
                option_id="OPTION",
                channel=pl.lit(0),
                measure="MEASURE",
                count_round=pl.lit(0),
                flags=pl.lit(0),
                value="VALUE",
            ),
        )

    def accounting_values(
        self, result_set: str, depts: set[str] | None = None, crvs: pl.DataFrame | None = None
    ) -> None:
        if not self.measured:
            return
        found = self.accounting()
        if depts is not None:
            found = found.filter(pl.col("DEPARTAMENTO").is_in(sorted(depts)))
        if crvs is not None:
            found = found.join(crvs.select("DEPARTAMENTO", "CRV").unique(), on=["DEPARTAMENTO", "CRV"], how="semi")
        found = found.join(self.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        self.values(result_set, found.select("UNIT", OPTION=pl.lit(None, pl.String), MEASURE="MEASURE", VALUE="VALUE"))

    def tree(
        self,
        contest: str,
        result_set: str,
        votes: pl.DataFrame,
        *,
        parts: tuple[object, ...] = (),
        lema_kind: str = "legend",
        leaf_kind: str = "list",
    ) -> dict[str, str]:
        """Options for each lema, each ballot sheet beneath it, and a residual option for votes that name no sheet.

        `votes` has DEPARTAMENTO, CRV, LEMAKEY, SHEET (null for votes for a lema alone), and VOTES. Returns the lema
        option of each lema key.
        """
        lemas: dict[str, str] = {}
        sheets: dict[tuple[str, str], str] = {}
        ordered = sorted(votes["LEMAKEY"].unique().to_list())
        labels = names()
        for position, lema in enumerate(ordered, 1):
            lemas[lema] = self.option(contest, lema_kind, labels[lema], *parts, lema, lema=lema, position=position)
        found = votes.filter(pl.col("SHEET").is_not_null()).select("LEMAKEY", "SHEET").unique()
        for lema, sheet in found.sort("LEMAKEY", "SHEET").iter_rows():
            sheets[(lema, sheet)] = self.option(
                contest, leaf_kind, f"List {sheet}", *parts, lema, sheet, lema=lema, parent=lemas[lema]
            )
        option_ids = pl.DataFrame(
            [(lema, sheet, option) for (lema, sheet), option in sheets.items()],
            schema={"LEMAKEY": pl.String, "SHEET": pl.String, "OPTION": pl.String},
            orient="row",
        )
        legend_ids = pl.DataFrame(
            [(lema, option) for lema, option in lemas.items()],
            schema={"LEMAKEY": pl.String, "LEGEND": pl.String},
            orient="row",
        )
        joined = votes.join(self.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        if joined["UNIT"].null_count():
            raise ValueError(f"{self.day}: breakdown rows for circuits missing from the totals")
        joined = joined.join(option_ids, on=["LEMAKEY", "SHEET"], how="left", nulls_equal=True).join(
            legend_ids, on="LEMAKEY", how="left"
        )
        sheet_votes = joined.filter(pl.col("SHEET").is_not_null())
        self.values(
            result_set,
            sheet_votes.select("UNIT", "OPTION", MEASURE=pl.lit(1), VALUE="VOTES"),
        )
        alone = joined.filter(pl.col("SHEET").is_null())
        if alone.height:
            self.values(
                result_set,
                alone.select("UNIT", OPTION="LEGEND", MEASURE=pl.lit(9), VALUE="VOTES"),
            )
        return lemas

    def declared(
        self, result_set: str, options: dict[str, str], votes: dict[str, int], blank: int, annulled: int
    ) -> None:
        """The country totals the Corte declares, for the counts to be checked against: each lema's votes, and the
        blank and annulled ballots."""
        for lema, n in sorted(votes.items()):
            if lema not in options:
                raise ValueError(f"{self.day}: the Corte declares votes for {lema}, which has no option")
            self.p.add("declared", result_set_id=result_set, option_id=options[lema], measure=1, value=n)
        self.p.add("declared", result_set_id=result_set, option_id=None, measure=6, value=blank)
        self.p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=annulled)

    def seats(self, result_set: str, options: dict[str, str], won: dict[str, int]) -> None:
        """The seats the Corte declares for each lema, on the lema's option; a lema that won none has no outcome."""
        for lema, n in sorted(won.items()):
            if n == 0:
                continue
            if lema not in options:
                raise ValueError(f"{self.day}: {lema} won seats but has no option in the contest")
            self.p.add("outcome", result_set_id=result_set, option_id=options[lema], kind="won", seats=n)

    def lema_totals(
        self,
        contest: str,
        result_set: str,
        votes: pl.DataFrame,
        kind: str,
        *,
        parts: tuple[object, ...] = (),
    ) -> dict[str, str]:
        """One option per lema, holding all the votes of its ballot sheets and of the lema alone."""
        labels = names()
        lemas = {
            lema: self.option(contest, kind, labels[lema], *parts, lema, lema=lema, position=position)
            for position, lema in enumerate(sorted(votes["LEMAKEY"].unique().to_list()), 1)
        }
        legend_ids = pl.DataFrame(list(lemas.items()), schema={"LEMAKEY": pl.String, "OPTION": pl.String}, orient="row")
        joined = votes.join(self.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left").join(
            legend_ids, on="LEMAKEY", how="left"
        )
        self.values(result_set, joined.select("UNIT", "OPTION", MEASURE=pl.lit(1), VALUE="VOTES"))
        return lemas

    def candidates(
        self, contest: str, result_set: str, votes: pl.DataFrame, lema: str, *, parts: tuple[object, ...] = ()
    ) -> None:
        """Options for each precandidate of one party and each ballot sheet beneath, from a frame with DEPARTAMENTO,
        CRV, CANDIDATE, SHEET (null for votes for the precandidate alone), and VOTES."""
        candidates: dict[str, str] = {}
        for position, name in enumerate(sorted(votes["CANDIDATE"].unique().to_list()), 1):
            candidates[name] = self.option(
                contest, "candidate", lema_name(name), *parts, name, lema=lema, position=position
            )
        found = votes.filter(pl.col("SHEET").is_not_null()).select("CANDIDATE", "SHEET").unique()
        sheets_by: dict[tuple[str, str], str] = {}
        for name, sheet_number in found.sort("CANDIDATE", "SHEET").iter_rows():
            sheets_by[(name, sheet_number)] = self.option(
                contest,
                "list",
                f"List {sheet_number}",
                *parts,
                name,
                sheet_number,
                lema=lema,
                parent=candidates[name],
            )
        ids = pl.DataFrame(
            [(name, number, option) for (name, number), option in sheets_by.items()],
            schema={"CANDIDATE": pl.String, "SHEET": pl.String, "OPTION": pl.String},
            orient="row",
        )
        candidate_ids = pl.DataFrame(
            list(candidates.items()), schema={"CANDIDATE": pl.String, "ALONE": pl.String}, orient="row"
        )
        joined = votes.join(self.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        if joined["UNIT"].null_count():
            raise ValueError(f"{self.day}: votes for circuits missing from the units")
        joined = joined.join(ids, on=["CANDIDATE", "SHEET"], how="left", nulls_equal=True).join(
            candidate_ids, on="CANDIDATE", how="left"
        )
        self.values(
            result_set,
            joined.select("UNIT", OPTION=pl.coalesce("OPTION", "ALONE"), MEASURE=pl.lit(1), VALUE="VOTES"),
        )


def sheets(frame: pl.DataFrame, record: str, alone: str) -> pl.DataFrame:
    """The votes of one kind of ballot, with the sheet number and null for votes for the lema alone."""
    sheet = frame.filter(pl.col("TIPOREGISTRO") == record).select(
        "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET=pl.col("DESCRIPCION1"), VOTES="CANTIDADVOTOS"
    )
    legend = frame.filter(pl.col("TIPOREGISTRO") == alone).select(
        "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET=pl.lit(None, pl.String), VOTES="CANTIDADVOTOS"
    )
    return pl.concat([sheet, legend])


def election(day: str) -> Package:
    if day in NATIONAL:
        p = national(day)
    elif day in RUNOFF:
        p = runoff(day)
    elif day in XLSX and day in PRIMARY:
        p = primary_xlsx(day)
    elif day in PRIMARY:
        p = primary(day)
    elif day == REFERENDUM_DAY:
        return referendum()
    elif day == ADHESION_DAY:
        return adhesion()
    else:
        p = local(day)
    if day in XLSX and day in LOCAL:
        p.accept(
            "accounting.declared",
            "The Corte's declared totals, from its summary workbook, are never below the sum of its circuit export "
            "and are 2.5% above it overall (1.1% above in the municipal elections). The export gives the votes of each ballot sheet in each circuit and no circuit totals, so "
            "the declared blank and annulled counts have no circuit values to compare with; both are stored as "
            "published, and each result set's completeness is its circuit votes over its declared votes",
        )
        return p
    reason = (
        "In some circuits the totals file counts more ballots than the circuit's roll lists. In every such circuit "
        "the excess is within the observed ballots (voters registered in another circuit), which the Corte counts "
        "in the circuit's ballots; stored as published"
    )
    if day == "2014-06-01":
        reason = (
            "In 3 circuits (Florida 110 and 147, Lavalleja 111) the ballots cast exceed the circuit's roll; in each "
            "the excess is within the observed ballots (voters registered in another circuit); stored as published"
        )
    if day == "2024-06-30":
        reason = (
            "In 82 circuits the Corte publishes a roll of zero with ballots cast, and in 7 more the excess is "
            "within the observed ballots (voters registered elsewhere); stored as published"
        )
    p.accept("accounting.turnout", reason)
    return p


def national(day: str) -> Package:
    year = day[:4]
    b = Builder(day, f"The {year} Uruguayan national election by circuit from the Corte Electoral")
    b.election(
        "general",
        f"{year} Uruguayan general election",
        f"Elecciones nacionales {year}",
    )
    frame = breakdown(day)
    b.add_units(b.totals)
    seats = declared_seats(day) if day in OFFICIAL else None
    votes = sheets(frame, "HOJA_EN", "VOTO_LEMA")
    lema_votes = votes.group_by("DEPARTAMENTO", "CRV", "LEMAKEY").agg(pl.col("VOTES").sum())
    contest, result_set = b.contest(
        "president",
        "office",
        f"{year} Uruguayan presidential election, first round",
        "president",
        office=PRESIDENCY,
        round_number=1,
        system=SYSTEM_PRESIDENT,
        es=f"Elección de Presidente de la República {year}, primera vuelta",
    )
    options = b.lema_totals(contest, result_set, lema_votes, "ticket")
    b.accounting_values(result_set)
    if day in OFFICIAL:
        b.declared(result_set, options, *declared_votes(day))
    tickets, _ = RUNOFF_TICKETS[next(d for d in RUNOFF if d[:4] == year)]
    ahead = {lema for _, _, lema in tickets}
    for lema, option in options.items():
        b.p.add(
            "outcome", result_set_id=result_set, option_id=option, kind="advanced" if lema in ahead else "eliminated"
        )
    contest, result_set = b.contest(
        "senate",
        "list_allocation",
        f"{year} Uruguayan Senate election",
        "senate",
        body=SENATE_BODY,
        magnitude=None if seats is None else sum(seats[0].values()),
        es=f"Elección de Senadores {year}",
    )
    lists = b.lema_totals(contest, result_set, lema_votes, "list")
    b.accounting_values(result_set)
    if day in OFFICIAL:
        b.declared(result_set, lists, *declared_votes(day))
    if seats is not None:
        b.seats(result_set, lists, seats[0])
    for dept, text in DEPARTMENTS.items():
        here = votes.filter(pl.col("DEPARTAMENTO") == dept)
        contest, result_set = b.contest(
            f"chamber:{dept}",
            "list_allocation",
            f"{year} Uruguayan Chamber of Representatives election, {text}",
            f"chamber-{text}",
            body=CHAMBER_BODY,
            district=key("area", "uy.departamento", dept),
            magnitude=None if seats is None else sum(seats[1][dept].values()),
            es=f"Elección de Representantes {year}, {text}",
        )
        lemas = b.tree(contest, result_set, here, parts=(dept,))
        b.accounting_values(result_set, {dept})
        if seats is not None:
            b.seats(result_set, lemas, seats[1][dept])
    for column, en, es in PLEBISCITES.get(day, []):
        contest, result_set = b.contest(
            f"plebiscite:{column}", "ballot_question", f"{year} {en}", en.split(" (")[0], es=es
        )
        b.p.add("ballot_question", contest_id=contest, type="plebiscite")
        yes = b.option(contest, "answer", "Sí", "yes", position=1)
        found = b.totals.select("DEPARTAMENTO", "CRV", VALUE=pl.col(column).cast(pl.Int64))
        found = found.join(b.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        b.values(result_set, found.select("UNIT", OPTION=pl.lit(yes), MEASURE=pl.lit(1), VALUE="VALUE"))
        b.accounting_values(result_set)
    if day in PLEBISCITE_FILES:
        plebiscite(b, day)
    b.p.add(
        "note",
        id=key("note", b.election_id, "solo-si"),
        entity_id=b.election_id,
        kind="caveat",
        body_md="The Senate and the Chamber of Representatives are elected with the same ballot sheets as the "
        "President, so each lema's votes are the same in every contest; the Chamber contests split them by sheet, "
        "the department's list. Votes counted for a lema alone, where a voter marked several sheets of one lema, "
        "are stored as legend votes on the lema option. The seats of the Senate and of each department's delegation "
        "are the Corte's declared distribution, where its results workbook or proclamation minutes are published.",
        lang="en",
    )
    if day in RESULTS_JSON:
        b.p.accept(
            "accounting.declared",
            "The Corte's declared blank votes include the envelopes holding only sheets for the plebiscite, which its "
            "circuit file counts apart; the circuit blank ballots plus those envelopes equal the declared count",
        )
    return b.p


def plebiscite_circuits() -> pl.DataFrame:
    """The 2019 plebiscite's "Papeleta por SI" ballots per circuit, read from the Corte's PDF."""
    path = raw.cached(SOURCE, PLEBISCITE_FILES["2019-10-27"]).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    row = re.compile(r"^\s*27/10/2019\s+Plebiscito\s+(.+?)\s+(\d+)\s+([A-Z]{3})\s+Departamental((?:\s+\d+){8})\s*$")
    codes = {text: code for code, text in DEPARTMENTS.items()}
    rows = []
    for line in text.splitlines():
        found = row.match(line)
        if found:
            numbers = [int(n) for n in found.group(4).split()]
            rows.append((codes[found.group(1)], str(int(found.group(2))), *numbers))
    names_ = ("DEPARTAMENTO", "CRV", "HAB", "NOOBS", "OBS", "EMITIDOS", "BLANCO", "ANULADOS", "SOLOSI", "SI")
    return pl.DataFrame(rows, schema=dict.fromkeys(names_, pl.String) | {n: pl.Int64 for n in names_[2:]}, orient="row")


def plebiscite_departments() -> pl.DataFrame:
    """The 2014 plebiscite's "Papeleta por SI" ballots per department, read from the workbook in the Corte's zip."""
    path = raw.cached(SOURCE, PLEBISCITE_FILES["2014-10-26"]).path
    with zipfile.ZipFile(path) as archive:
        data = archive.read("Nacionales 2014.xlsx")
    sheet = pl.read_excel(io.BytesIO(data), sheet_id=1, has_header=False)
    codes = {norm(text): code for code, text in DEPARTMENTS.items()}
    rows = []
    on = False
    for line in sheet.rows():
        cells = [str(c).strip() for c in line if c is not None and str(c).strip()]
        if cells and cells[0].startswith("5)"):
            on = True
        elif cells and cells[0][:2] == "6)":
            on = False
        elif on and cells and norm(cells[0]) in codes:
            rows.append((codes[norm(cells[0])], int(cells[1])))
    return pl.DataFrame(rows, schema={"DEPARTAMENTO": pl.String, "SI": pl.Int64}, orient="row")


def plebiscite(b: Builder, day: str) -> None:
    """The plebiscite's contest, with its "Sí" ballots per circuit (2019) or per department (2014)."""
    en, es = PLEBISCITE_TITLES[day]
    year = day[:4]
    contest, result_set = b.contest(f"plebiscite:{year}", "ballot_question", f"{year} {en}", en, es=f"{es} {year}")
    b.p.add("ballot_question", contest_id=contest, type="plebiscite")
    yes = b.option(contest, "answer", "Sí", "yes", position=1)
    if day == "2019-10-27":
        found = plebiscite_circuits()
        measures = b.accounting().select("DEPARTAMENTO", "CRV").unique()
        if found.height != measures.height or found.join(measures, on=["DEPARTAMENTO", "CRV"], how="anti").height:
            raise ValueError("the plebiscite PDF and the totals file list different circuits")
        found = found.join(b.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        b.values(result_set, found.select("UNIT", OPTION=pl.lit(yes), MEASURE=pl.lit(1), VALUE="SI"))
        b.accounting_values(result_set)
        text = (
            "The Corte publishes the plebiscite's results per circuit, and the 'Papeleta por SI' count, the ballots "
            "with the Sí sheet, is stored as the votes for Sí. Votes against are not published."
        )
    else:
        found = plebiscite_departments()
        units = []
        for dept in found["DEPARTAMENTO"]:
            unit = key("unit", day, "plebiscite", dept)
            b.p.add(
                "reporting_unit",
                id=unit,
                polity_id=URUGUAY,
                scheme_code="uy.departamento",
                code=f"{day}:plebiscite:{dept}",
                area_relation="equals",
                area_id=key("area", "uy.departamento", dept),
                kind="aggregate",
            )
            units.append(unit)
        b.values(result_set, found.select(UNIT=pl.Series(units), OPTION=pl.lit(yes), MEASURE=pl.lit(1), VALUE="SI"))
        text = (
            "The Corte's results workbook publishes the 'Papeleta por SI' ballots of this plebiscite per department "
            "only; the circuit files count only the ballots with the Sí sheet alone, so the count per circuit is not "
            "published. Votes against are not published."
        )
    b.p.add("note", id=key("note", result_set, "yes"), entity_id=result_set, kind="caveat", body_md=text, lang="en")


def runoff(day: str) -> Package:
    year = day[:4]
    first = next(d for d in NATIONAL if d[:4] == year)
    b = Builder(day, f"The {year} Uruguayan presidential runoff by circuit from the Corte Electoral")
    b.p.depends.append(f"uy.corte/{first}")
    b.election("runoff", f"{year} Uruguayan presidential runoff", f"Balotaje {year}")
    b.add_units(b.totals)
    contest, result_set = b.contest(
        "president",
        "office",
        f"{year} Uruguayan presidential election, second round",
        "president",
        office=PRESIDENCY,
        round_number=2,
        previous=key("contest", first, "president"),
        system=SYSTEM_PRESIDENT,
        es=f"Elección de Presidente de la República {year}, segunda vuelta",
    )
    tickets, elected = RUNOFF_TICKETS[day]
    sums = {column: int(b.totals[column].cast(pl.Int64).sum()) for column, _, _ in tickets}
    leader = max(tickets, key=lambda t: sums[t[0]])[2]
    if leader != elected:
        raise ValueError(f"{day}: the totals put {leader} ahead, not {elected}")
    if first in SUMMARY:
        b.p.uses(raw.cached(SOURCE, url(SUMMARY[first])))
        declared = norm(" ".join(cell for row in section(summary(first), 6) for cell in row))
        winner = next(label for _, label, lema in tickets if lema == elected)
        if norm(winner.split("–")[0]) not in declared:
            raise ValueError(f"{day}: the Corte declares another ticket than {winner}")
    unit = b.unit_frame()
    for position, (column, label, lema) in enumerate(tickets, 1):
        option = b.option(contest, "ticket", label, lema, lema=lema, position=position)
        found = b.totals.select("DEPARTAMENTO", "CRV", VALUE=pl.col(column).cast(pl.Int64)).join(
            unit, on=["DEPARTAMENTO", "CRV"], how="left"
        )
        b.values(result_set, found.select("UNIT", OPTION=pl.lit(option), MEASURE=pl.lit(1), VALUE="VALUE"))
        b.p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if lema == elected else "lost")
    b.accounting_values(result_set)
    return b.p


def referendum_circuits() -> pl.DataFrame:
    """The 2022 referendum's circuit file, in the shape of the totals files (DEPARTAMENTO, CRV, TOTAL columns)."""
    frame = circuit(table(url(REFERENDUM["circuits"])))
    return frame.select(
        "DEPARTAMENTO",
        "CRV",
        TOTALHABILITADOS="TOTALHABILITADOS",
        TOTALVOTOSEMITIDOS="TOTALVOTOSEMITIDOS",
        TOTALVOTOSOBSERVADOS="TOTALVOTOSOBSERVADOS",
        TOTALANULADOS="TOTALANULADOS",
        TOTALENBLANCO="TOTALENBLANCO",
        SI="TOTALSI",
        NO="TOTALNO",
    )


def referendum() -> Package:
    b = Builder(
        REFERENDUM_DAY,
        "The 2022 referendum against 135 articles of Law 19.889 by circuit from the Corte Electoral",
        referendum_circuits(),
    )
    b.election(
        "referendum",
        "2022 Uruguayan referendum against 135 articles of Law 19.889",
        "Referéndum contra 135 artículos de la Ley 19.889 (2022)",
    )
    b.add_units(b.totals)
    contest, result_set = b.contest(
        "luc",
        "ballot_question",
        "2022 referendum against 135 articles of Law 19.889 (Ley de Urgencia)",
        "ley-19889",
        es="Referéndum contra 135 artículos de la Ley 19.889 (Ley de Urgente Consideración)",
    )
    b.p.add("ballot_question", contest_id=contest, type="optional_referendum")
    unit = b.unit_frame()
    for position, (column, label, name) in enumerate((("SI", "Sí", "yes"), ("NO", "No", "no")), 1):
        option = b.option(contest, "answer", label, name, position=position)
        found = b.totals.select("DEPARTAMENTO", "CRV", VALUE=pl.col(column).cast(pl.Int64)).join(
            unit, on=["DEPARTAMENTO", "CRV"], how="left"
        )
        b.values(result_set, found.select("UNIT", OPTION=pl.lit(option), MEASURE=pl.lit(1), VALUE="VALUE"))
    b.accounting_values(result_set)
    b.p.add(
        "note",
        id=key("note", result_set, "counts"),
        entity_id=result_set,
        kind="caveat",
        body_md="Sí is the vote to repeal the 135 articles and No the vote to keep them. The circuit file counts the "
        "Sí and No ballots of voters who voted in their own circuit; the observed ballots, cast by voters registered "
        "elsewhere, are counted in the circuit's ballots cast and are not split between Sí and No. Valid votes are "
        "not published per circuit, so none are stored. The Corte publishes no declared national totals or outcome "
        "apart from the circuit file.",
        lang="en",
    )
    b.p.accept(
        "accounting.turnout",
        "In 88 circuits the ballots cast exceed the circuit's roll; in each the excess is within the observed ballots "
        "(voters registered in another circuit), which the Corte counts in the circuit's ballots; stored as published",
    )
    return b.p


def adhesion_circuits() -> pl.DataFrame:
    """The 2019 adhesion act's circuit workbook: DEPARTAMENTO, CRV, and the ballots of each circuit."""
    with zipfile.ZipFile(raw.cached(SOURCE, ADHESION["zip"]).path) as archive:
        data = archive.read(ADHESION["circuits"])
    book = fastexcel.read_excel(data)
    frame = book.load_sheet_by_idx(0, header_row=0, dtypes="string").to_polars()
    frame = frame.with_columns(pl.col(pl.String).str.strip_chars()).filter(pl.col("Circuito").is_not_null())
    return frame.select(
        DEPARTAMENTO=pl.col("Departamento").map_elements(department, return_dtype=pl.String),
        CRV=pl.col("Circuito").cast(pl.Int64).cast(pl.String),
        TOTALVOTOSOBSERVADOS=pl.col("Observados").cast(pl.Int64),
        TOTALENBLANCO=pl.col("Blancos").cast(pl.Int64),
        TOTALANULADOS=pl.col("Anulados").cast(pl.Int64),
        ADHESIONES=pl.col("Adhesiones").cast(pl.Int64),
    )


def adhesion_declared() -> tuple[dict[str, int], int]:
    """The national totals row of the Corte's department PDF, and the voters on the roll it states."""
    with zipfile.ZipFile(raw.cached(SOURCE, ADHESION["zip"]).path) as archive:
        data = archive.read(ADHESION["departments"])
    text = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True
    ).stdout.decode()
    row = re.search(r"^\s*TOTAL((?:\s+\d+){5})\s*$", text, re.MULTILINE)
    roll = re.search(r"Total de habilitados:\s*(\d+)", text)
    if row is None or roll is None:
        raise ValueError("the adhesion PDF has no totals row")
    adhesions, blank, annulled, observed, _ = (int(n) for n in row.group(1).split())
    return {"adhesions": adhesions, "blank": blank, "annulled": annulled, "observed": observed}, int(roll.group(1))


def adhesion() -> Package:
    b = Builder(
        ADHESION_DAY,
        "The 2019 act of adhesion to the referendum against Law 19.684 by circuit from the Corte Electoral",
        adhesion_circuits(),
    )
    b.election(
        "special",
        "2019 act of adhesion to a referendum against Law 19.684",
        "Acto de adhesión a la interposición del recurso de referéndum contra la Ley 19.684 (2019)",
    )
    b.add_units(b.totals)
    contest, result_set = b.contest(
        "adhesion",
        "ballot_question",
        "2019 adhesion to a referendum against Law 19.684 (comprehensive law for trans people)",
        "ley-19684",
        es="Adhesión a la interposición del recurso de referéndum contra la Ley 19.684",
    )
    b.p.add("ballot_question", contest_id=contest, type="referendum")
    option = b.option(contest, "answer", "Adhesión", "adhesion", position=1)
    found = b.totals.select("DEPARTAMENTO", "CRV", VALUE="ADHESIONES").join(
        b.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left"
    )
    b.values(result_set, found.select("UNIT", OPTION=pl.lit(option), MEASURE=pl.lit(1), VALUE="VALUE"))
    b.accounting_values(result_set)
    totals_, roll = adhesion_declared()
    b.p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=totals_["adhesions"])
    for measure, name in ((6, "blank"), (5, "annulled"), (15, "observed")):
        b.p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=totals_[name])
    b.p.add(
        "note",
        id=key("note", result_set, "counts"),
        entity_id=result_set,
        kind="caveat",
        body_md=f"Voters who adhered to the request for a referendum against the law signed a roll at the circuit; "
        f"the adhesions, blank and annulled ballots, and observed ballots (voters registered elsewhere) of each "
        f"circuit are stored as the Corte publishes them. The Corte states {roll:,} voters on the roll nationally "
        f"and {totals_['adhesions'] / roll:.2%} reached, against the 25% needed; the roll per circuit is not "
        f"published, and the workbook's own per-circuit total of voters is inconsistent in a few circuits, so it "
        f"is not stored.",
        lang="en",
    )
    return b.p


PRIMARY_RECORDS = {
    "ODN": ("HOJA_ODN", {"PREC_ODN", "SUBLEMA_ODN", "VOTOS_PREC"}),
    "ODD": ("HOJA_ODD", {"SUBLEMA_ODD"}),
}


def primary(day: str) -> Package:
    year = day[:4]
    b = Builder(day, f"The {year} Uruguayan party primaries by circuit from the Corte Electoral")
    b.election("primary", f"{year} Uruguayan party primaries", f"Elecciones internas {year}")
    b.add_units(b.totals)
    frame = breakdown(day)
    labels = names()
    for lema in sorted(frame["LEMAKEY"].unique().to_list()):
        for record, body_en, body_es in (
            ("ODN", "National Deliberative Body", "Órgano Deliberativo Nacional"),
            ("ODD", "Departmental Deliberative Body", "Órgano Deliberativo Departamental"),
        ):
            sheet_record, residual_records = PRIMARY_RECORDS[record]
            own = frame.filter(
                (pl.col("LEMAKEY") == lema) & pl.col("TIPOREGISTRO").is_in([sheet_record, *sorted(residual_records)])
            )
            for dept in [None] if record == "ODN" else sorted(DEPARTMENTS):
                found = own if dept is None else own.filter(pl.col("DEPARTAMENTO") == dept)
                if not found.height:
                    continue
                place = "" if dept is None else f", {DEPARTMENTS[dept]}"
                tag = f"{record}:{lema}" + ("" if dept is None else f":{dept}")
                contest, result_set = b.contest(
                    tag,
                    "delegate_allocation",
                    f"{year} {labels[lema]} primary, {body_en}{place}",
                    f"{record}-{labels[lema]}" + ("" if dept is None else f"-{DEPARTMENTS[dept]}"),
                    district=NATION if dept is None else key("area", "uy.departamento", dept),
                    es=f"Elecciones internas {year}, {labels[lema]}, {body_es}{place}",
                )
                sheet = found.filter(pl.col("TIPOREGISTRO") == sheet_record).select(
                    "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET="DESCRIPCION2", VOTES="CANTIDADVOTOS"
                )
                residual = found.filter(pl.col("TIPOREGISTRO") != sheet_record).select(
                    "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET=pl.lit(None, pl.String), VOTES="CANTIDADVOTOS"
                )
                b.tree(contest, result_set, pl.concat([sheet, residual]), parts=(tag,))
                b.accounting_values(result_set, None if dept is None else {dept}, found)
    precandidates(b, frame, day)
    b.p.add(
        "note",
        id=key("note", b.election_id, "primaries"),
        entity_id=b.election_id,
        kind="caveat",
        body_md="Each party's primary elects its deliberative bodies, and each ballot sheet names a presidential "
        "precandidate. Votes are stored per ballot sheet. Votes the Corte counts for a sublema, for a precandidate, "
        "or for several sheets without a single sheet are stored as legend votes on the party's option. The ballots "
        "in the unit accounting are the circuit's, shared by every party's primary. Each party's presidential "
        "precandidate totals are derived from the same counts, by precandidate; the Corte's proclamation of the "
        "nominees is not stored here.",
        lang="en",
    )
    return b.p


def primary_exports(day: str) -> list[tuple[str, str, pl.DataFrame]]:
    """The 2014 primaries' exports as (record, lema key, frame): ODN or ODD, the party, and its rows."""
    found: list[tuple[str, str, pl.DataFrame]] = []
    for record in ("odn", "odd"):
        for file, data in zip_files(day, record).items():
            frame = sheet(data)
            convocatoria = frame["CONVOCATORIA"].unique().to_list()
            if len(convocatoria) != 1:
                raise ValueError(f"{day}: {file} has convocatorias {convocatoria}")
            lema = PRIMARY_LEMAS[norm(re.sub(r"_-_O\.D\.[ND]\.$", "", convocatoria[0]))]
            found.append((record.upper(), lema, frame))
    return found


def primary_totals(frames: list[tuple[str, str, pl.DataFrame]]) -> pl.DataFrame:
    """The circuit totals, which the Corte publishes in the wide export of the Concertación's national body only:
    every circuit of the country with its roll, ballots cast, observed, blank, and annulled ballots."""
    wide = [
        frame
        for record, lema, frame in frames
        if record == "ODN" and "T_EMITIDOS" in frame.columns and lema == "DE LA CONCERTACION"
    ]
    if len(wide) != 1:
        raise ValueError("the 2014 primaries' circuit totals are not in one export")
    frame = wide[0]
    codes = {text: department(text) for text in frame["DEPTO"].unique().to_list()}
    return frame.select(
        DEPARTAMENTO=pl.col("DEPTO").replace_strict(codes),
        CRV=pl.col("CIRCUITO").cast(pl.Int64).cast(pl.String),
        TOTALHABILITADOS=pl.col("HABILITADO").cast(pl.Int64),
        TOTALVOTOSEMITIDOS=pl.col("T_EMITIDOS").cast(pl.Int64),
        TOTALVOTOSOBSERVADOS=pl.col("OBSERVADOS").cast(pl.Int64),
        TOTALANULADOS=pl.col("ANULADOS").cast(pl.Int64),
        TOTALENBLANCO=pl.col("EN_BLANCO").cast(pl.Int64),
    )


def primary_xlsx(day: str) -> Package:
    year = day[:4]
    frames = primary_exports(day)
    b = Builder(
        day, f"The {year} Uruguayan party primaries by circuit from the Corte Electoral", primary_totals(frames)
    )
    b.election("primary", f"{year} Uruguayan party primaries", f"Elecciones internas {year}")
    b.add_units(b.totals)
    labels = names()
    for record, lema, frame in sorted(frames, key=lambda item: (item[0], item[1])):
        body_en, body_es = {
            "ODN": ("National Deliberative Body", "Órgano Deliberativo Nacional"),
            "ODD": ("Departmental Deliberative Body", "Órgano Deliberativo Departamental"),
        }[record]
        codes = {text: department(text) for text in frame["DEPTO"].unique().to_list()}
        sheets_column = "HOJA" if "HOJA" in frame.columns else None
        if sheets_column is None:
            # The Concertación's national export has one column for its precandidate and no ballot sheets.
            candidate = frame.columns[-1]
            votes = frame.select(
                DEPARTAMENTO=pl.col("DEPTO").replace_strict(codes),
                CRV=pl.col("CIRCUITO").cast(pl.Int64).cast(pl.String),
                CANDIDATE=pl.lit(candidate),
                SHEET=pl.lit(None, pl.String),
                VOTES=pl.col(candidate).cast(pl.Int64),
            )
        else:
            votes = frame.select(
                DEPARTAMENTO=pl.col("DEPTO").replace_strict(codes),
                CRV=pl.col("CIRCUITO").cast(pl.Int64).cast(pl.String),
                CANDIDATE=pl.col("PRECANDIDATO") if record == "ODN" else pl.lit(None, pl.String),
                SHEET="HOJA",
                VOTES=pl.col("CNT_VOTOS").cast(pl.Int64),
            )
        b.add_units(votes)
        for dept in [None] if record == "ODN" else sorted(DEPARTMENTS):
            found = votes if dept is None else votes.filter(pl.col("DEPARTAMENTO") == dept)
            if not found.height:
                continue
            place = "" if dept is None else f", {DEPARTMENTS[dept]}"
            tag = f"{record}:{lema}" + ("" if dept is None else f":{dept}")
            contest, result_set = b.contest(
                tag,
                "delegate_allocation",
                f"{year} {labels[lema]} primary, {body_en}{place}",
                f"{record}-{labels[lema]}" + ("" if dept is None else f"-{DEPARTMENTS[dept]}"),
                district=NATION if dept is None else key("area", "uy.departamento", dept),
                es=f"Elecciones internas {year}, {labels[lema]}, {body_es}{place}",
            )
            if record == "ODN":
                b.candidates(contest, result_set, found, lema, parts=(tag,))
            else:
                b.tree(contest, result_set, found.with_columns(LEMAKEY=pl.lit(lema)), parts=(tag,))
            b.accounting_values(result_set, None if dept is None else {dept}, found)
    b.p.add(
        "note",
        id=key("note", b.election_id, "primaries"),
        entity_id=b.election_id,
        kind="caveat",
        body_md="Each party's primary elects its deliberative bodies. In the National Deliberative Body each ballot "
        "sheet names a presidential precandidate, so the sheets are stored beneath the precandidate's option; the "
        "Partido de la Concertación's export has one precandidate and no ballot sheets. The ballots in the unit "
        "accounting are the circuit's, shared by every party's primary; the Corte publishes them once, in the "
        "export of the Partido de la Concertación's national body, which lists every circuit of the country. In "
        "every department the ballots cast, less the blank and annulled ones, exceed the votes the parties' exports "
        "give per sheet, by between 8% and 22%; the Corte does not itemize the difference.",
        lang="en",
    )
    return b.p


OFFICIAL_PARTIES = {
    "FA": "FRENTE AMPLIO",
    "PN": "NACIONAL",
    "PC": "COLORADO",
    "PCONC": "DE LA CONCERTACION",
    "PCONCERT": "DE LA CONCERTACION",
    "PI": "INDEPENDIENTE",
    "AP": "ASAMBLEA POPULAR",
    "PERI": "ECOLOGISTA RADICAL INTRANSIGENTE",
    "PT": "DE LOS TRABAJADORES",
}
BLANK = "BLANK"
ANNULLED = "ANNULLED"
# Municipality names the Corte's summary workbook misspells, as the circuit export names them. Florida's lists
# "Fray Bentos", a city in Río Negro; Florida's third municipality, which the table lacks, is Fray Marcos.
OFFICIAL_ALIASES = {
    ("AR", "BLATASARBRUM"): "BALTASARBRUM",
    ("CA", "EMPLAMEOLMOS"): "EMPALMEOLMOS",
    ("FD", "FRAYBENTOS"): "FRAYMARCOS",
    ("SA", "VILLACONTITUCION"): "VILLACONSTITUCION",
}


def mkey(text: str) -> str:
    """A municipality's name as matched between files: Montevideo's are "Municipio A" in one and "A" in the other."""
    return norm(text).removeprefix("MUNICIPIO")


@dataclass
class Official:
    """The Corte's summary of a departmental and municipal election: votes by department and party, seats on each
    departmental board, the party of each elected intendente, and votes by municipality and party. A figure's column
    is a lema key, or BLANK or ANNULLED (envelopes whose every ballot was annulled)."""

    votes: dict[str, dict[str, int]] = field(default_factory=dict)
    seats: dict[str, dict[str, int]] = field(default_factory=dict)
    elected: dict[str, tuple[str, str]] = field(default_factory=dict)
    municipal: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)


def column(header: str) -> str | None:
    if header in OFFICIAL_PARTIES:
        return OFFICIAL_PARTIES[header]
    if header.startswith("ENBLANCO"):
        return BLANK
    if header.startswith("SOBRESC"):
        return ANNULLED
    return None


def official(day: str) -> Official:
    book = fastexcel.read_excel(member(day, "official"))
    rows = book.load_sheet_by_idx(0, header_row=None, dtypes="string").to_polars().iter_rows()
    found = Official()
    section = 0
    header: list[str] = []
    dept = ""
    for row in rows:
        first = (row[0] or "").strip()
        heading = re.match(r"(\d)\)", first)
        if heading:
            section, header = int(heading[1]), []
            continue
        if section not in (4, 5, 6, 7):
            continue
        if norm(first) == "DEPARTAMENTO":
            header = [norm(cell or "") for cell in row]
            continue
        if not header or norm(first) in ("TOTAL", "TOTALES", "PORCENTAJES") or (not first and section != 7):
            continue
        if section == 6:
            found.elected[department(first)] = (row[1] or "").strip(), lema_key(row[3] or "")
            continue
        if section == 7:
            if first:
                dept = department(first)
            if not (row[1] or "").strip():
                continue
            name = mkey(row[1] or "")
            place = found.municipal.setdefault((dept, OFFICIAL_ALIASES.get((dept, name), name)), {})
        else:
            place = (found.votes if section == 4 else found.seats).setdefault(department(first), {})
        for index in range(1 if section < 7 else 2, len(header)):
            lema = column(header[index])
            if lema is not None and row[index] is not None:
                place[lema] = int(float(row[index] or 0))
    return found


def share(votes: pl.DataFrame, figures: dict[str, int]) -> float:
    """The circuit votes of a result set over the Corte's declared votes for its lemas."""
    declared = sum(value for lema, value in figures.items() if lema not in (BLANK, ANNULLED))
    return min(1.0, round(int(votes["VOTES"].sum()) / declared, 6))


def declare(
    b: Builder, result_set: str, options: dict[str, str], figures: dict[str, int], what: str, blank: bool = True
) -> None:
    """The Corte's declared totals of a result set: votes of each lema and the blank and annulled ballots."""
    for lema, value in figures.items():
        if lema == BLANK and blank:
            b.p.add("declared", result_set_id=result_set, option_id=None, measure=6, value=value)
        elif lema == ANNULLED:
            b.p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=value)
        elif lema in options:
            b.p.add("declared", result_set_id=result_set, option_id=options[lema], measure=1, value=value)
        elif lema not in (BLANK, ANNULLED) and value:
            raise ValueError(
                f"{b.day}: the Corte declares {value} votes for {lema} in {what}, which has no such option"
            )


PRECANDIDATE_RECORDS = {"HOJA_ODN", "VOTOS_PREC", "PREC_ODN", "SUBLEMA_ODN"}
PRECANDIDATE_METHOD = (
    "Derived from the Corte Electoral's breakdown by circuit. Each National Deliberative Body ballot sheet names the "
    "precandidate it supports, and a precandidate's votes in a circuit are the votes of their ballot sheets there, plus "
    "the votes the Corte counts for the precandidate directly or through a sublema without a single sheet. Every "
    "sheet and precandidate pair was checked against the Corte's integration of the ballot sheets. A sheet number "
    "can be shared by several precandidates of one party, so the precandidate is read from the count itself. Votes for "
    "a party's sheets that the Corte does not attribute to a precandidate are not counted here."
)


def person_name(text: str) -> str:
    """The name of a person the Corte lists as `SURNAME, Given names`, in reading order."""
    surname, _, given = text.partition(",")
    return f"{given.strip()} {' '.join(w.capitalize() for w in surname.split())}".strip()


def integration(day: str) -> set[tuple[str, str, str]]:
    """The (lema key, sheet number, precandidate key) triples of the Corte's integration of the National sheets."""
    path = raw.cached(SOURCE, url(INTEGRATION[day])).path
    if path.suffix == ".xlsx":
        found = fastexcel.read_excel(str(path)).load_sheet_by_name("Datos", header_row=0, dtypes="string").to_polars()
    else:
        found = pl.read_csv(io.StringIO(path.read_bytes().decode("cp1252")), infer_schema_length=0)
    found = found.filter(pl.col("TipoHoja") == "ODN").select("Numero", "PartidoPolitico", "Precandidato").unique()
    return {
        (lema_key(party), number.strip(), norm(prec))
        for number, party, prec in found.iter_rows()
        if prec is not None and number is not None and party is not None
    }


def precandidates(b: Builder, frame: pl.DataFrame, day: str) -> None:
    """One presidential contest per party: its precandidates, with their votes by circuit."""
    year = day[:4]
    known = integration(day)
    rows = frame.filter(pl.col("TIPOREGISTRO").is_in(sorted(PRECANDIDATE_RECORDS)))
    pairs = rows.filter(pl.col("TIPOREGISTRO") == "HOJA_ODN").select("LEMAKEY", "DESCRIPCION1", "DESCRIPCION2").unique()
    missing = [
        (lema, number, prec) for lema, prec, number in pairs.iter_rows() if (lema, number, norm(prec)) not in known
    ]
    if missing:
        raise ValueError(f"{day}: sheets whose precandidate the integration does not list: {missing[:5]}")
    labels = names()
    for lema in sorted(rows["LEMAKEY"].unique().to_list()):
        found = rows.filter(pl.col("LEMAKEY") == lema)
        if (found["DESCRIPCION1"].str.to_lowercase() == "no aplica").any():
            raise ValueError(f"{day}: votes of {lema} without a precandidate")
        contest, result_set = b.contest(
            f"president:{lema}",
            "office",
            f"{year} {labels[lema]} primary, presidential precandidates",
            f"president-{labels[lema]}",
            office=PRESIDENCY,
            es=f"Elecciones internas {year}, {labels[lema]}, precandidatos a la Presidencia",
        )
        b.p.add(
            "note",
            id=key("note", result_set, "precandidates"),
            entity_id=result_set,
            kind="methodology",
            body_md=PRECANDIDATE_METHOD,
            lang="en",
        )
        names_by_key = {norm(text): text for text in found["DESCRIPCION1"].unique().to_list()}
        options: dict[str, str] = {}
        for position, tag in enumerate(sorted(names_by_key), 1):
            text = person_name(names_by_key[tag])
            option = options[tag] = b.option(
                contest, "candidate", text, "precandidate", lema, tag, lema=lema, position=position
            )
            person = key("person", day, lema, tag)
            b.p.add("person", id=person, birth_precision=None)
            b.p.name(person, text, "es", "official")
            b.p.add(
                "candidacy",
                id=key("candidacy", day, lema, tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=text,
                member_of_party_id=party(lema),
            )
        option_ids = pl.DataFrame(
            [(tag, option) for tag, option in options.items()],
            schema={"TAG": pl.String, "OPTION": pl.String},
            orient="row",
        )
        votes = (
            found.with_columns(TAG=pl.col("DESCRIPCION1").map_elements(norm, return_dtype=pl.String))
            .join(option_ids, on="TAG", how="left")
            .join(b.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        )
        if votes["UNIT"].null_count():
            raise ValueError(f"{day}: precandidate rows for circuits missing from the totals")
        b.values(result_set, votes.select("UNIT", "OPTION", MEASURE=pl.lit(1), VALUE="CANTIDADVOTOS"))
        b.accounting_values(result_set, None, found)


def letter(text: str) -> str | None:
    found = re.search(r"[^\s;-]+-([^\s;-]+)", text)
    return None if found is None else found.group(1)


def local(day: str) -> Package:
    year = day[:4]
    b = Builder(day, f"The {year} Uruguayan departmental and municipal elections by circuit from the Corte Electoral")
    b.election(
        "local",
        f"{year} Uruguayan departmental and municipal elections",
        f"Elecciones departamentales y municipales {year}",
    )
    frame = listing(day)
    municipal = listing(day, "municipal") if has(day, "municipal") else frame
    for found in (b.totals,) if b.measured else (frame, municipal):
        b.add_units(found)
    declared = official(day) if day in XLSX else None
    labels = municipalities()
    for dept, text in DEPARTMENTS.items():
        area = key("area", "uy.departamento", dept)
        here = sheets(frame.filter(pl.col("DEPARTAMENTO") == dept), "HOJA_ED", "VOTO_LEMA_ED")
        contest, result_set = b.contest(
            f"intendente:{dept}",
            "office",
            f"{year} election of the Intendente of {text}",
            f"intendente-{text}",
            office=key("office", "intendente", dept),
            district=area,
            magnitude=1 if declared else None,
            completeness=share(here, declared.votes[dept]) if declared else 1.0,
            es=f"Elección de Intendente de {text} {year}",
        )
        options = b.tree(contest, result_set, here, parts=(dept,))
        b.accounting_values(result_set, {dept})
        if declared is not None:
            declare(b, result_set, options, declared.votes[dept], f"the intendente election of {text}")
            winner = declared.elected[dept][1]
            for lema, option in options.items():
                b.p.add(
                    "outcome", result_set_id=result_set, option_id=option, kind="elected" if lema == winner else "lost"
                )
        contest, result_set = b.contest(
            f"junta:{dept}",
            "list_allocation",
            f"{year} election of the Departmental Board of {text}",
            f"junta-departamental-{text}",
            body=key("body", "junta", dept),
            district=area,
            magnitude=31 if declared else None,
            completeness=share(here, declared.votes[dept]) if declared else 1.0,
            es=f"Elección de la Junta Departamental de {text} {year}",
        )
        lemas = here.group_by("DEPARTAMENTO", "CRV", "LEMAKEY").agg(pl.col("VOTES").sum())
        options = b.lema_totals(contest, result_set, lemas, "list", parts=(dept,))
        b.accounting_values(result_set, {dept})
        if declared is not None:
            declare(b, result_set, options, declared.votes[dept], f"the departmental board of {text}")
            for lema, option in options.items():
                seats = declared.seats[dept].get(lema, 0)
                if seats:
                    b.p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
                else:
                    b.p.add("outcome", result_set_id=result_set, option_id=option, kind="lost")
            unplaced = {lema for lema, seats in declared.seats[dept].items() if seats and lema not in options}
            if unplaced:
                raise ValueError(f"{day}: seats for {sorted(unplaced)} in {text} with no votes")
    rows = municipal.filter(pl.col("TIPOREGISTRO").is_in(["HOJA_EM", "VOTO_LEMA_EM"])).with_columns(
        pl.col("DESCRIPCION1").map_elements(lambda s: letter(s) or "", return_dtype=pl.String).alias("LETTER")
    )
    if (rows["LETTER"] == "").any():
        raise ValueError(f"{day}: municipal rows without a municipality letter")
    matched: set[tuple[str, str]] = set()
    for (dept, mark), found in sorted(rows.group_by("DEPARTAMENTO", "LETTER"), key=lambda item: item[0]):
        text = labels[(dept, mark)]
        votes = sheets(found, "HOJA_EM", "VOTO_LEMA_EM")
        figures: dict[str, int] | None = None
        if declared is not None:
            names_here = {mkey(name) for name in found["DESCRIPCION2"].unique().to_list()}
            if len(names_here) != 1:
                raise ValueError(f"{day}: municipality {dept}-{mark} has names {sorted(names_here)}")
            place = (dept, names_here.pop())
            figures = declared.municipal[place]
            matched.add(place)
        contest, result_set = b.contest(
            f"alcalde:{dept}:{mark}",
            "office",
            f"{year} election of the Mayor of {text}, {DEPARTMENTS[dept]}",
            f"alcalde-{DEPARTMENTS[dept]}-{text}",
            office=key("office", "alcalde", dept, mark),
            district=key("area", "uy.municipio", dept, mark),
            completeness=share(votes, figures) if figures is not None else 1.0,
            es=f"Elección de Alcalde de {text}, {DEPARTMENTS[dept]} {year}",
        )
        options = b.tree(contest, result_set, votes, parts=(dept, mark))
        b.accounting_values(result_set, {dept}, found)
        if figures is not None:
            declare(b, result_set, options, figures, f"the municipality {text}")
        if b.measured:
            b.p.add(
                "note",
                id=key("note", result_set, "municipal"),
                entity_id=result_set,
                kind="caveat",
                body_md="The ballots, observed, invalid, and blank counts are the circuit's totals for the departmental "
                "ballot, as the Corte publishes them; the municipal ballot is counted on the same circuit.",
                lang="en",
            )
    if declared is not None:
        left = sorted(set(declared.municipal) - matched)
        if left:
            raise ValueError(f"{day}: official municipalities with no circuits: {left}")
        b.p.add(
            "note",
            id=key("note", b.election_id, "circuit-export"),
            entity_id=b.election_id,
            kind="caveat",
            body_md="The Corte's circuit export gives the votes of each ballot sheet in each circuit and no circuit "
            "totals, so registered voters, ballots cast, and blank and invalid ballots are not stored per circuit. "
            "Its votes fall short of the Corte's declared totals, by 2.5% overall in the departmental election, "
            "which appears to be the observed ballots of voters registered in another circuit; each result set's "
            "completeness is its circuit votes over its declared votes, and the declared figures are stored beside "
            "them. The declared outcomes are the party of each elected Intendente and the seats each party won on "
            "each departmental board.",
            lang="en",
        )
    return b.p
