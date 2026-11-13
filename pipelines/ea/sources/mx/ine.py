import csv
import io
import zipfile

import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "mx.ine"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

SICEEF = "http://siceef.ine.mx/BD/"
COMPUTOS = {
    2018: ("https://computos2018.ine.mx/20180708_2130_CW.zip", "20180708_2130_CW_presidencia.zip"),
    2024: ("https://computos2024.ine.mx/20240608_2030_COMPUTOS.zip", "20240608_2030_COMPUTOS_PRES.zip"),
}
SICEEF_YEARS = (1994, 2000, 2006, 2012)
TOTAL_GAPS = {(2012, "12", "7", "52", "1994", "B"): 1, (2012, "14", "3", "94", "2375", "B"): -2}

KEYS = ("state", "district", "municipality", "section", "casilla")
NONVOTES = {"NUM_VOTOS_CAN_NREG", "CNR", "CANDIDATO/A NO REGISTRADO/A", "NUM_VOTOS_NULOS", "VN", "VOTOS NULOS"}
UNREGISTERED = {"NUM_VOTOS_CAN_NREG", "CNR", "CANDIDATO/A NO REGISTRADO/A"}
TRAILING = {
    "TOTAL_VOTOS",
    "TOTAL_VOTOS_CALCULADOS",
    "LISTA_NOMINAL",
    "LISTA_NOMINAL_CASILLA",
    "ESTATUS_ACTA",
    "TEPJF",
    "OBSERVACIONES",
    "RUTA_ACTA",
    "MECANISMOS_TRASLADO",
    "FECHA_HORA",
    "",
}


def siceef_url(year: int) -> str:
    return f"{SICEEF}Presidente{year}.csv"


def fetch() -> None:
    for year in SICEEF_YEARS:
        raw.fetch(SOURCE, siceef_url(year), suffix=".csv")
    for url, _ in COMPUTOS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def clean(value: str) -> str:
    return value.strip().removeprefix("=").strip("'\"")


def number(value: str) -> int:
    value = clean(value).replace(",", "")
    return int(value) if value not in {"", "-", "N/A"} else 0


def siceef(year: int) -> tuple[list[str], list[dict[str, str]]]:
    text = raw.cached(SOURCE, siceef_url(year)).path.read_bytes().decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    rows = [
        {
            "state": row["ID_ESTADO"],
            "district": row["ID_DISTRITO"],
            "municipality": row["ID_MUNICIPIO"],
            "section": row["SECCION"],
            "casilla": row["CASILLA"],
            **row,
        }
        for row in reader
    ]
    columns = list(reader.fieldnames or [])
    return columns[columns.index("CASILLA") + 1 :], rows


def computos(year: int) -> tuple[list[str], list[dict[str, str]], int]:
    url, inner = COMPUTOS[year]
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as outer, zipfile.ZipFile(io.BytesIO(outer.read(inner))) as pres:
        name = next(n for n in pres.namelist() if n.upper().endswith((f"PRES_{year}.CSV", "PRESIDENCIA.CSV")))
        lines = pres.read(name).decode("latin-1").splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("CLAVE_CASILLA"))
    summary = next(i for i, line in enumerate(lines) if line.startswith("ACTAS_ESPERADAS"))
    printed = dict(zip(lines[summary].split("|"), lines[summary + 1].split("|"), strict=True))
    reader = csv.DictReader(io.StringIO("\n".join(lines[head:])), delimiter="|")
    columns = list(reader.fieldnames or [])
    state = "ID_ESTADO" if "ID_ESTADO" in columns else "ID_ENTIDAD"
    district = "ID_DISTRITO" if "ID_DISTRITO" in columns else "ID_DISTRITO_FEDERAL"
    rows = []
    for row in reader:
        rows.append(
            {
                "state": clean(row[state]),
                "district": clean(row[district]),
                "municipality": "",
                "section": clean(row["SECCION"]),
                "casilla": clean(row["TIPO_CASILLA"]) + clean(row["ID_CASILLA"]) + clean(row["EXT_CONTIGUA"]),
                **row,
            }
        )
    start = columns.index("TIPO_ACTA" if "TIPO_ACTA" in columns else "NUM_ACTA_IMPRESO") + 1
    return columns[start:], rows, number(printed["TOTAL_VOTOS"])


def table(year: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    printed = None
    if year in COMPUTOS:
        columns, rows, printed = computos(year)
    else:
        columns, rows = siceef(year)
    options = [c for c in columns if c not in TRAILING]
    total = "TOTAL_VOTOS" if "TOTAL_VOTOS" in columns else "TOTAL_VOTOS_CALCULADOS"
    roll = next((c for c in ("LISTA_NOMINAL", "LISTA_NOMINAL_CASILLA") if c in columns), None)
    units, votes = [], []
    for row in rows:
        counts = {option: number(row[option]) for option in options}
        gap = TOTAL_GAPS.get((year, *(clean(row[k]) for k in KEYS)), 0)
        if sum(counts.values()) - number(row[total]) != gap:
            raise ValueError(
                f"{year} {[row[k] for k in KEYS]}: options add to {sum(counts.values())}, total {row[total]}"
            )
        unit = {k: clean(row[k]).lstrip("0") or "0" if k != "casilla" else clean(row[k]) for k in KEYS}
        units.append(
            unit
            | {
                "total": sum(counts.values()),
                "printed_total": number(row[total]),
                "registered": number(row[roll]) if roll else None,
                "status": clean(row.get("ESTATUS_ACTA") or row.get("OBSERVACIONES") or ""),
                "tepjf": clean(row.get("TEPJF") or ""),
            }
        )
        for option, value in counts.items():
            votes.append(
                unit
                | {
                    "option": option,
                    "kind": "unregistered" if option in UNREGISTERED else "invalid" if option in NONVOTES else "list",
                    "votes": value,
                }
            )
    units_frame = pl.DataFrame(units, schema_overrides={"registered": pl.Int64})
    duplicates = units_frame.group_by(KEYS).len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"{year}: duplicate casillas {duplicates.head()}")
    if printed is not None and printed != units_frame["printed_total"].sum():
        raise ValueError(f"{year}: casillas add to {units_frame['total'].sum()}, the file prints {printed}")
    return units_frame, pl.DataFrame(votes)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in (*SICEEF_YEARS, *COMPUTOS):
        units, votes = table(year)
        units.write_parquet(PARSED / f"presidente_{year}_casillas.parquet")
        votes.write_parquet(PARSED / f"presidente_{year}_votes.parquet")
        national = votes.group_by("option").agg(pl.col("votes").sum()).sort("votes", descending=True)
        print(year, units.height, "casillas", units["total"].sum(), "votes", dict(national.rows()))
