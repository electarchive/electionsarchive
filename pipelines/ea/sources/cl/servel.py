import functools
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import NamedTuple
from uuid import uuid5

import fastexcel
import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "cl.servel"
UPLOADS = "https://www.servel.cl/wp-content/uploads/"
PARSED = DATA / "parsed" / SOURCE
TERRITORIES = "2026/08/Territorios-Electorales-de-Chile.xlsx"

COLUMNS = (
    "region",
    "comuna",
    "circ",
    "mesa",
    "tipo",
    "cs",
    "distrito",
    "circ_prov",
    "electores",
    "voto",
    "lista",
    "pacto",
    "subpacto",
    "partido",
    "candidato",
    "pueblo",
    "mesa_code",
    "elected",
    "votos",
)
HEADERS = {
    "regionn": "region",
    "regionno": "region",
    "numeroregion": "region",
    "pueblo": "pueblo",
    "resultadostricel": "votos",
    "resultadoster": "votos",
    "nroregion": "region",
    "regionid": "region",
    "region": "region_any",
    "regionnombre": "region_name",
    "circsenatorial": "cs",
    "cs": "cs",
    "circunscripcionsenatorial": "cs",
    "distrito": "distrito",
    "comuna": "comuna",
    "comunanombre": "comuna",
    "paisnombre": "pais",
    "pais": "pais",
    "circelectoral": "circ",
    "circunscripcion": "circ",
    "circunscripcionelectoral": "circ",
    "circelenombre": "circ",
    "circprovincial": "circ_prov",
    "circunscripcionprovincial": "circ_prov",
    "nromesa": "mesa",
    "nmesa": "mesa",
    "nomesa": "mesa",
    "mesan": "mesa",
    "mesano": "mesa",
    "mesa": "mesa",
    "mesanumero": "mesa",
    "tipomesa": "tipo",
    "tipo": "tipo",
    "tipodemesa": "tipo",
    "electores": "electores",
    "mesaelectores": "electores",
    "nroenvoto": "voto",
    "nenvoto": "voto",
    "noenvoto": "voto",
    "nrovoto": "voto",
    "nvoto": "voto",
    "lista": "lista",
    "pacto": "pacto",
    "subpacto": "subpacto",
    "partido": "partido",
    "partidopolitico": "partido",
    "candidato": "candidato",
    "candidatos": "candidato",
    "opcion": "candidato",
    "votostricel": "votos",
    "vototricel": "votos",
    "votoster": "votos",
    "votos": "votos",
    "votospreliminares": "votos",
    "votosprovisorio": "votos",
    "codmesa": "mesa_code",
    "letrapacto": "lista",
    "nombrecandidato": "candidato",
    "cantelectores": "electores",
    "electonominado": "elected",
}


def fold(text: object) -> str:
    """Upper-case ASCII with single spaces, the form used to match names across files."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


REGIONS = {
    1: "Tarapacá",
    2: "Antofagasta",
    3: "Atacama",
    4: "Coquimbo",
    5: "Valparaíso",
    6: "Libertador General Bernardo O'Higgins",
    7: "Maule",
    8: "Biobío",
    9: "La Araucanía",
    10: "Los Lagos",
    11: "Aysén del General Carlos Ibáñez del Campo",
    12: "Magallanes y de la Antártica Chilena",
    13: "Metropolitana de Santiago",
    14: "Los Ríos",
    15: "Arica y Parinacota",
    16: "Ñuble",
}
REGION_WORDS = (
    ("ARICA", 15),
    ("TARAPACA", 1),
    ("ANTOFAGASTA", 2),
    ("ATACAMA", 3),
    ("COQUIMBO", 4),
    ("VALPARAISO", 5),
    ("HIGGINS", 6),
    ("MAULE", 7),
    ("NUBLE", 16),
    ("BIOBIO", 8),
    ("ARAUCANIA", 9),
    ("LOS RIOS", 14),
    ("LOS LAGOS", 10),
    ("AISEN", 11),
    ("AYSEN", 11),
    ("MAGALLANES", 12),
    ("METROPOLITANA", 13),
)


def region_number(name: str) -> str | None:
    folded = fold(name)
    for word, number in REGION_WORDS:
        if word in folded:
            return str(number)
    return None


def _header(text: object) -> str:
    return re.sub(r"[^a-z]", "", fold(text).lower())


def fetch_one(url: str) -> raw.Artifact:
    suffix = "." + url.rsplit(".", 1)[1].lower()
    for _ in range(3):
        try:
            return raw.fetch(SOURCE, url, suffix=suffix)
        except httpx.TransportError as failure:
            print(f"{url}: {failure}; retrying", flush=True)
    # Servel's server drops long transfers, so resume with ranges and store the whole file.
    buffer = bytearray()
    media_type = None
    for _ in range(40):
        try:
            with httpx.stream(
                "GET",
                url,
                headers={"User-Agent": raw.USER_AGENT, "Range": f"bytes={len(buffer)}-"},
                follow_redirects=True,
                timeout=600,
            ) as response:
                response.raise_for_status()
                if response.status_code != 206:
                    buffer.clear()
                media_type = response.headers.get("content-type")
                total = int(response.headers.get("content-range", "/0").rsplit("/", 1)[1] or 0)
                for chunk in response.iter_bytes():
                    buffer.extend(chunk)
            if total and len(buffer) >= total or not total:
                break
        except httpx.TransportError as failure:
            print(f"{url}: {failure} at {len(buffer):,} bytes; resuming", flush=True)
    else:
        raise RuntimeError(f"{url}: download did not complete")
    return raw.store(SOURCE, url, bytes(buffer), suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type)


def workbooks(url: str) -> Iterator[tuple[str, bytes]]:
    """Each workbook a cached file holds: the file itself, or every workbook inside a zip."""
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        for member in sorted(archive.namelist()):
            if member.lower().endswith((".xlsx", ".xls", ".csv")) and not member.startswith("__MACOSX"):
                yield member, archive.read(member)
    else:
        yield path.name, path.read_bytes()


def _frame(data: bytes, name: str) -> Iterator[tuple[str, pl.DataFrame]]:
    if name.lower().endswith(".csv"):
        text = data.decode("utf-8-sig") if _utf8(data) else data.decode("cp1252")
        line = text.split("\n", 1)[0]
        separator = ";" if line.count(";") > line.count(",") else ","
        yield name, pl.read_csv(io.StringIO(text), separator=separator, infer_schema_length=0, has_header=False)
        return
    book = fastexcel.read_excel(data)
    for sheet in book.sheet_names:
        yield sheet, book.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()


def _utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


CSV_COLUMNS = {
    "NOMBRE_REGION": "region_name",
    "NOMBRE_CIRC_SENATORIAL": "cs",
    "NOMBRE_DISTRITO": "distrito",
    "NOMBRE_COMUNA": "comuna",
    "NOMBRE_CIRC_ELECTORAL": "circ",
    "COD_MESA": "mesa_code",
    "NUMERO_MESA": "mesa",
    "TOTAL_ELECTORES": "electores",
    "LISTA": "lista",
    "PACTO": "pacto",
    "PARTIDO_POLITICO": "partido",
    "NUMERO_VOTO": "voto",
    "TIPO_VOTACION": "candidato",
    "VOTOS_PRELIMINAR": "votos",
    "ELECTO": "elected",
}


def _csv(path: str, member: str) -> pl.DataFrame:
    """A zipped semicolon file of padded columns, too large to read in memory as text."""
    PARSED.mkdir(parents=True, exist_ok=True)
    target = PARSED / ".extract.csv"
    with zipfile.ZipFile(path) as archive, archive.open(member) as source, target.open("wb") as sink:
        while chunk := source.read(1 << 24):
            sink.write(chunk)
    frame = (
        pl.scan_csv(target, separator=";", infer_schema=False, quote_char=None)
        .select(pl.col(column).str.strip_chars().alias(name) for column, name in CSV_COLUMNS.items())
        .with_columns(
            pl.col("cs", "distrito").str.extract(r"(\d+)"),
            pl.col("votos").cast(pl.Int64, strict=False),
            total=pl.lit(False),
        )
        # Tables with no preliminary count print an empty figure.
        .filter(pl.col("votos").is_not_null())
        .collect()
    )
    target.unlink()
    names = frame["region_name"].unique().to_list()
    frame = frame.with_columns(region=pl.col("region_name").replace_strict({n: region_number(n) for n in names}))
    for column in COLUMNS:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, pl.String).alias(column))
    return frame.select(*COLUMNS, "total")


def read(url: str) -> pl.DataFrame:
    """Every results sheet of a cached file in the long one-line-per-mesa-and-option form, with canonical columns."""
    frames = []
    cached = raw.cached(SOURCE, url).path
    if cached.suffix == ".zip":
        with zipfile.ZipFile(cached) as archive:
            large = [m for m in archive.infolist() if m.filename.lower().endswith(".csv") and m.file_size > 1 << 30]
        if large:
            return pl.concat(
                [_csv(str(cached), m.filename).with_columns(file=pl.lit(m.filename), sheet=pl.lit("")) for m in large]
            )
    for member, data in workbooks(url):
        for sheet, grid in _frame(data, member):
            if _header(grid.row(0)[0]) == "eleccion" and "codmesa" in [_header(cell) for cell in grid.row(0)]:
                frames.append(_listed_mesas(grid, member, sheet))
                continue
            frame = _sheet(grid)
            if frame is None:
                continue
            prefix = re.match(r"(?:.*/)?(\d{1,2})[_ ]", member) or re.match(r"(\d{1,2})_", url.rsplit("/", 1)[1])
            if prefix is not None:
                frame = frame.with_columns(pl.col("region").fill_null(str(int(prefix[1]))))
            frames.append(frame.with_columns(file=pl.lit(member), sheet=pl.lit(sheet)))
    if not frames:
        raise ValueError(f"{url} holds no results sheet")
    return pl.concat(frames, how="diagonal_relaxed")


def _listed_mesas(grid: pl.DataFrame, member: str, sheet: str) -> pl.DataFrame:
    """The 2024 primaries file: one row per table and option, with the election named on every row, and with the
    table's blank, null, emitted, and calculated totals as options numbered from 900. The totals are checked against
    the options and left out."""
    names = [_header(cell) for cell in grid.row(0)]
    long = pl.DataFrame(
        {name: grid.to_series(i).slice(1).str.strip_chars() for i, name in enumerate(names) if name}
    ).with_columns(
        pl.col("canorden").cast(pl.Float64).cast(pl.Int32),
        votos=pl.col("votosprovisorio").cast(pl.Float64).cast(pl.Int64),
        mesa_code=pl.col("codmesa").cast(pl.Float64).cast(pl.Int64).cast(pl.String),
    )
    tables = long.filter(pl.col("canorden") < 900).group_by("eleccion", "mesa_code").agg(pl.col("votos").sum())
    for number, label in ((904, "total-emitted"), (905, "calculated-sum")):
        printed = long.filter(pl.col("canorden") == number).select("eleccion", "mesa_code", printed=pl.col("votos"))
        sums = long.filter(pl.col("canorden") < 904).group_by("eleccion", "mesa_code").agg(pl.col("votos").sum())
        bad = sums.join(printed, on=["eleccion", "mesa_code"]).filter(pl.col("votos") != pl.col("printed"))
        print(
            f"{member}: {bad.height:,} of {printed.height:,} tables print a {label} figure unlike the sum of their options"
        )
    print(f"{member}: {tables.height:,} election-and-table pairs, {tables['votos'].sum():,} votes for candidates")
    frame = long.filter(pl.col("canorden") < 904).select(
        region=pl.col("region").map_elements(region_number, return_dtype=pl.String, skip_nulls=True),
        comuna="comuna",
        circ="circunscripcion",
        mesa="mesa",
        electores=pl.col("mesaelectores").cast(pl.Float64).cast(pl.Int64).cast(pl.String),
        voto=pl.col("canorden").cast(pl.String),
        candidato="candidato",
        votos="votos",
        mesa_code="mesa_code",
        total=pl.lit(False),
        file="eleccion",
        sheet=pl.lit(sheet),
    )
    for column in COLUMNS:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, pl.String).alias(column))
    return frame.select(*COLUMNS, "total", "file", "sheet")


def _sheet(grid: pl.DataFrame) -> pl.DataFrame | None:
    for index, row in enumerate(grid.head(12).iter_rows()):
        names = [
            HEADERS.get(_header(cell)) or ("candidato" if _header(cell).startswith("opcion") else None) for cell in row
        ]
        if "votos" in names and "mesa" in names:
            break
    else:
        return None
    columns: dict[str, pl.Series] = {}
    for position, name in enumerate(names):
        if name is not None and name not in columns:
            columns[name] = grid.to_series(position).slice(index + 1).str.strip_chars()
    frame = pl.DataFrame(columns)
    if "region_any" in frame.columns:
        numeric = frame["region_any"].drop_nulls().str.contains(r"^\d+$").all()
        if "region" not in frame.columns and numeric:
            frame = frame.rename({"region_any": "region"})
        else:
            frame = frame.rename({"region_any": "region_name"})
    if "region" not in frame.columns and "region_name" in frame.columns:
        frame = frame.with_columns(
            region=pl.col("region_name").map_elements(region_number, return_dtype=pl.String, skip_nulls=True)
        )
    if "pais" in frame.columns and "comuna" not in frame.columns:
        frame = frame.rename({"pais": "comuna"}).with_columns(region=pl.lit("X"))
    frame = frame.filter(pl.col("votos").str.contains(r"^\d+(\.0+)?$").fill_null(False))
    if (
        "tipo" in frame.columns
        and 2 * frame["mesa"].drop_nulls().str.contains(r"^[MVX]$").sum() > frame["mesa"].drop_nulls().len()
    ):
        # A sheet that prints the type of table under Mesa and the number under the type.
        frame = frame.with_columns(mesa=pl.col("tipo"), tipo=pl.col("mesa"))
    for column in ("cs", "distrito", "region"):
        if column in frame.columns:
            frame = frame.with_columns(pl.col(column).str.extract(r"(\d+|X)").alias(column))
    for column in COLUMNS:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, pl.String).alias(column))
    return frame.select(
        *(pl.col(c) for c in COLUMNS if c != "votos"),
        pl.col("votos").str.replace(r"\.0+$", "").cast(pl.Int64),
        total=pl.col("mesa").is_null() & pl.col("comuna").is_null(),
    )


FILES = (
    "2022/12/01_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/01_Resultados_Mesa_Alcaldes_Ter-1.xlsx",
    "2022/12/01_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/01_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/01_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/01_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/01_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/01_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/01_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/02_Resultado_Mesa_Senadores_Tricel.xlsx",
    "2022/12/02_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/02_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/02_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/02_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/02_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/02_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/02_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/02_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/02_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/03_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/03_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/03_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/03_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/03_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/03_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/03_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/03_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/03_Resultados_Mesa_Presidente_Tricel1-2.xlsx",
    "2022/12/04_Resultado_Mesa_Senadores_Tricel.xlsx",
    "2022/12/04_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/04_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/04_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/04_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/04_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/04_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/04_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/04_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/04_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/05_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/05_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/05_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/05_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/05_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/05_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/05_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/05_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/05_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/06_Resultado_Mesa_Senadores_Tricel.xlsx",
    "2022/12/06_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/06_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/06_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/06_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/06_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/06_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/06_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/06_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/06_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/07_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/07_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/07_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/07_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/07_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/07_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/07_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/07_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/07_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/08_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/08_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/08_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/08_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/08_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/08_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/08_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/08_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/08_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/09_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/09_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/09_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/09_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/09_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/09_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/09_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/09_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/09_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/10_Resultado_Mesa_Senadores_Tricel.xlsx",
    "2022/12/10_Resultados_Mesa_Alcaldes_TER-1.xlsx",
    "2022/12/10_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/10_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/10_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/10_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/10_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/10_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/10_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/10_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/11_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/11_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/11_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/11_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/11_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/11_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/11_Resultados_Mesa_Presidente_2v_Tricel-1.xlsx",
    "2022/12/11_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/11_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/12_Resultado_Mesa_Senadores_Tricel.xlsx",
    "2022/12/12_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/12_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/12_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/12_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/12_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/12_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/12_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/12_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/12_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/13_Resultado_Mesa_Senadores_Tricel.xlsx",
    "2022/12/13_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/13_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/13_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/13_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/13_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/13_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/13_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/13_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/13_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/14_Resultado_Mesa_Senadores_Tricel.xlsx",
    "2022/12/14_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/14_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/14_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/14_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/14_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/14_Resultados_Mesa_Diputados_Tricel.xlsx",
    "2022/12/14_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/14_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/14_Resultados_Mesa_Presidente_Tricel1-1.xlsx",
    "2022/12/15_Resultados_Mesa_Alcaldes_TER.xlsx",
    "2022/12/15_Resultados_Mesa_Alcaldes_Ter.xlsx",
    "2022/12/15_Resultados_Mesa_Cores_Ter-1.xlsx",
    "2022/12/15_Resultados_Mesa_Cores_Ter.xlsx",
    "2022/12/15_Resultados_Mesa_DIPUTADOS_Tricel.xlsx",
    "2022/12/15_Resultados_Mesa_Diputados_Tricel-1.xlsx",
    "2022/12/15_Resultados_Mesa_Presidente_2v_Tricel.xlsx",
    "2022/12/15_Resultados_Mesa_Presidente_Tricel.xlsx",
    "2022/12/15_Resultados_Mesa_Presidente_Tricel1-2.xlsx",
    "2022/12/2021_Primarias_Presidenciales.zip",
    "2022/12/2_CC_Pueblos.zip",
    "2022/12/3.GORE_.zip",
    "2022/12/5_Concejales_1.zip",
    "2022/12/CORE-1.zip",
    "2022/12/Definitivo_CCG.zip",
    "2022/12/Definitivos_Alcalde_2021.zip",
    "2022/12/Diputados-1.zip",
    "2022/12/Primarias_2020_Alcalde-GORE.zip",
    "2022/12/Resultados-Plebiscito-Constitucion-Politica-2020.xlsx",
    "2022/12/Resultados_Diputados_Primarias2017_Tricel.xlsx",
    "2022/12/Resultados_Mesa_PRESIDENCIAL_Tricel_1v.xlsx",
    "2022/12/Resultados_Mesa_PRESIDENCIAL_Tricel_2v.xlsx",
    "2022/12/Resultados_Mesa_Presidenciales_1v_2021-1.xlsx",
    "2022/12/Resultados_Pimarias_Alcalde_2016.xlsx",
    "2022/12/Resultados_Plebiscito_Tipo_Organo_2020.zip",
    "2022/12/Resultados_Presidente_Primarias2017_Tricel_extranjero.xlsx",
    "2022/12/Resultados_Presidente_Primarias2017_Tricel_nacional.xlsx",
    "2022/12/Resultados_mesa_presidencial_TRICEL_2v_2021-1.xlsx",
    "2022/12/Segunda_Votacion__de__GORE_2021.zip",
    "2022/12/Senadores-1.zip",
    "2022/12/resultados_elecciones_alcaldes_ce_2004_2016.xlsx",
    "2022/12/resultados_elecciones_concejales_ce_1992_al_2000.xlsx",
    "2022/12/resultados_elecciones_concejales_ce_2004_2016-1.xlsx",
    "2022/12/resultados_elecciones_core_ce_2013_2017-1.xlsx",
    "2022/12/resultados_elecciones_diputados_ce_1989_2017.xlsx",
    "2022/12/resultados_elecciones_presidenciales_ce_1989_2017_Chile.xlsx",
    "2022/12/resultados_elecciones_presidenciales_ce_2017_Extranjero.xlsx",
    "2022/12/resultados_elecciones_primarias_alcalde_ce_2016.xlsx",
    "2022/12/resultados_elecciones_primarias_diputados_ce_2013_2017-1.xlsx",
    "2022/12/resultados_elecciones_primarias_presidente_ce_2013_2017_Chile-1.xlsx",
    "2022/12/resultados_elecciones_primarias_presidente_ce_2017_Extranjero.xlsx",
    "2022/12/resultados_elecciones_senadores_ce_1989_2017.xlsx",
    "2023/01/Resultados-Plebiscito-Constitucional-2022.xlsx",
    "2023/05/20230507CA.zip",
    "2023/06/01_Resultados_Mesa_Concejales_TER-IQUIQUE.xlsx",
    "2023/06/01_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/02_Resultados_Mesa_Concejales_TER-ANTOFA.xlsx",
    "2023/06/02_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/03_Resultados_Mesa_Concejales_TERCOPIAPO.xlsx",
    "2023/06/03_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/04_Resultados_Mesa_Concejales_TERCOQUIMBO.xlsx",
    "2023/06/04_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/05_Resultados_Mesa_Concejales_TERVALPO.xlsx",
    "2023/06/05_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/06_Resultados_Mesa_Concejales_TER-OHIGGINS.xlsx",
    "2023/06/06_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/07_Resultados_Mesa_Concejales_TER-MAULE.xlsx",
    "2023/06/07_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/08_Resultados_Mesa_Concejales_TER-BIOBIO.xlsx",
    "2023/06/08_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/09_Resultados_Mesa_Concejales_TER-LA-ARAUCANIA.xlsx",
    "2023/06/09_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/10_Resultados_Mesa_Concejales_TER-LOS-LAGOS.xlsx",
    "2023/06/10_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/11_Resultados_Mesa_Concejales_TER-AISEN.xlsx",
    "2023/06/11_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/12_Resultados_Mesa_Concejales_TER-MAGALLANES.xlsx",
    "2023/06/12_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/13_Resultados_Mesa_Concejales_TER_1-METRO-.xlsx",
    "2023/06/13_Resultados_Mesa_Concejales_TER_2-METRO-2.xlsx",
    "2023/06/13_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/14_Resultados_Mesa_Concejales_TER-LOS-RIOS.xlsx",
    "2023/06/14_Resultados_Mesa_Concejales_Ter.xlsx",
    "2023/06/15_Resultados_Mesa_Concejales_TERARICA.xlsx",
    "2023/06/15_Resultados_Mesa_Concejales_Ter.xlsx",
    "2024/06/Primarias2024_Resultados_Provisorios.zip",
    "2025/07/Resultados_Preliminares_Primarias_Presidencial_CL_20250701.xlsx",
    "2025/07/Resultados_Preliminares_Primarias_Presidencial_EXT_20250701.xlsx",
    "2025/11/PRELIMINARES_DIPUTADOS.zip",
    "2025/11/PRELIMINARES_PRESIDENTE_DE_LA_REPUBLICA.zip",
    "2025/11/PRELIMINARES_SENADORES_CIRCUNSCRIPCION.zip",
    "2025/12/Datos_SegundaVotacion-1.zip",
    "2026/08/Territorios-Electorales-de-Chile.xlsx",
)


def url(path: str) -> str:
    return UPLOADS + path


def fetch() -> None:
    for path in FILES:
        fetch_one(url(path))


class Group(NamedTuple):
    kind: str
    pattern: str
    member: str = ""


class Election(NamedTuple):
    day: date
    last: date
    kind: str
    word: str
    en: str
    es: str
    stage: str
    groups: tuple[Group, ...]


def _election(
    day: date,
    kind: str,
    word: str,
    en: str,
    es: str,
    *groups: Group,
    stage: str = "certified",
    last: date | None = None,
) -> Election:
    return Election(day, last or day, kind, word, en, es, stage, groups)


ELECTIONS = (
    _election(
        date(2012, 10, 28),
        "local",
        "municipal",
        "2012 Chilean municipal elections",
        "Elecciones municipales 2012",
        Group("mayor", r"2022/12/\d\d_Resultados_Mesa_Alcaldes_Ter(-1)?\.xlsx"),
        Group("council", r"2023/06/\d\d_Resultados_Mesa_Concejales_Ter\.xlsx"),
    ),
    _election(
        date(2013, 6, 30),
        "primary",
        "primaries",
        "2013 Chilean presidential and parliamentary primaries",
        "Primarias presidenciales y parlamentarias 2013",
        Group("president_primary", r"2022/12/\d\d_Resultados_Mesa_Presidente_Tricel1(-\d)?\.xlsx"),
        # Servel publishes the deputies primaries of 2013 only by circunscripcion electoral.
        Group(
            "deputies_primary",
            r"2022/12/resultados_elecciones_primarias_diputados_ce_2013_2017-1\.xlsx",
            "2013-06-30:1",
        ),
    ),
    _election(
        date(2013, 11, 17),
        "general",
        "general",
        "2013 Chilean general election",
        "Elecciones presidencial, parlamentarias y de consejeros regionales 2013",
        Group("president", r"2022/12/\d\d_Resultados_Mesa_Presidente_Tricel\.xlsx"),
        Group("senators", r"2022/12/\d\d_Resultado_Mesa_Senadores_Tricel\.xlsx"),
        # The court's table files miss two of the ten constituencies; those come by circunscripcion electoral.
        Group("senators", r"2022/12/resultados_elecciones_senadores_ce_1989_2017\.xlsx", "2013-11-17:1"),
        Group("deputies", r"2022/12/\d\d_Resultados_Mesa_Diputados_Tricel(-1)?\.xlsx"),
        Group("core", r"2022/12/\d\d_Resultados_Mesa_Cores_Ter\.xlsx"),
    ),
    _election(
        date(2013, 12, 15),
        "runoff",
        "presidential-runoff",
        "2013 Chilean presidential election, second round",
        "Segunda votación presidencial 2013",
        Group("president", r"2022/12/\d\d_Resultados_Mesa_Presidente_2v_Tricel(-1)?\.xlsx"),
    ),
    _election(
        date(2016, 6, 19),
        "primary",
        "mayoral-primaries",
        "2016 Chilean mayoral primaries",
        "Primarias de alcaldes 2016",
        Group("mayor_primary", r"2022/12/Resultados_Pimarias_Alcalde_2016\.xlsx"),
    ),
    _election(
        date(2016, 10, 23),
        "local",
        "municipal",
        "2016 Chilean municipal elections",
        "Elecciones municipales 2016",
        Group("mayor", r"2022/12/\d\d_Resultados_Mesa_Alcaldes_TER(-1)?\.xlsx"),
        Group("council", r"2023/06/\d\d_Resultados_Mesa_Concejales_TER.+\.xlsx"),
    ),
    _election(
        date(2017, 7, 2),
        "primary",
        "primaries",
        "2017 Chilean presidential and parliamentary primaries",
        "Primarias presidenciales y parlamentarias 2017",
        Group("president_primary", r"2022/12/Resultados_Presidente_Primarias2017_Tricel_(nacional|extranjero)\.xlsx"),
        Group("deputies_primary", r"2022/12/Resultados_Diputados_Primarias2017_Tricel\.xlsx"),
    ),
    _election(
        date(2017, 11, 19),
        "general",
        "general",
        "2017 Chilean general election",
        "Elecciones presidencial, parlamentarias y de consejeros regionales 2017",
        Group("president", r"2022/12/Resultados_Mesa_PRESIDENCIAL_Tricel_1v\.xlsx"),
        Group("deputies", r"2022/12/\d\d_Resultados_Mesa_DIPUTADOS_Tricel\.xlsx"),
        Group("senators", r"2022/12/resultados_elecciones_senadores_ce_1989_2017\.xlsx", "2017-11-19:1"),
        Group("core", r"2022/12/\d\d_Resultados_Mesa_Cores_Ter-1\.xlsx"),
    ),
    _election(
        date(2017, 12, 17),
        "runoff",
        "presidential-runoff",
        "2017 Chilean presidential election, second round",
        "Segunda votación presidencial 2017",
        Group("president", r"2022/12/Resultados_Mesa_PRESIDENCIAL_Tricel_2v\.xlsx"),
    ),
    _election(
        date(2020, 10, 25),
        "referendum",
        "plebiscite",
        "2020 Chilean national plebiscite",
        "Plebiscito nacional 2020",
        Group("plebiscite", r"2022/12/Resultados-Plebiscito-Constitucion-Politica-2020\.xlsx"),
        Group("plebiscite_body", r"2022/12/Resultados_Plebiscito_Tipo_Organo_2020\.zip"),
    ),
    _election(
        date(2020, 11, 29),
        "primary",
        "primaries",
        "2020 Chilean regional governor and mayoral primaries",
        "Primarias de gobernadores regionales y alcaldes 2020",
        Group("governor_primary", r"2022/12/Primarias_2020_Alcalde-GORE\.zip", "GORE"),
        Group("mayor_primary", r"2022/12/Primarias_2020_Alcalde-GORE\.zip", "Alcaldes"),
    ),
    _election(
        date(2024, 6, 9),
        "primary",
        "primaries",
        "2024 Chilean regional governor and mayoral primaries",
        "Primarias de gobernadores regionales y alcaldes 2024",
        Group("governor_primary", r"2024/06/Primarias2024_Resultados_Provisorios\.zip", "Gobernadores"),
        Group("mayor_primary", r"2024/06/Primarias2024_Resultados_Provisorios\.zip", "Alcaldes"),
        stage="official",
    ),
    _election(
        date(2021, 5, 15),
        "general",
        "constitutional-regional-municipal",
        "2021 Chilean constitutional convention, regional governor, and municipal elections",
        "Elecciones de convencionales constituyentes, gobernadores regionales, alcaldes y concejales 2021",
        Group("convention", r"2022/12/Definitivo_CCG\.zip"),
        Group("convention_indigenous", r"2022/12/2_CC_Pueblos\.zip"),
        Group("governor", r"2022/12/3\.GORE_\.zip"),
        Group("mayor", r"2022/12/Definitivos_Alcalde_2021\.zip"),
        Group("council", r"2022/12/5_Concejales_1\.zip"),
        last=date(2021, 5, 16),
    ),
    _election(
        date(2021, 6, 13),
        "runoff",
        "governor-runoff",
        "2021 Chilean regional governor elections, second round",
        "Segunda votación de gobernadores regionales 2021",
        Group("governor", r"2022/12/Segunda_Votacion__de__GORE_2021\.zip"),
    ),
    _election(
        date(2021, 7, 18),
        "primary",
        "presidential-primaries",
        "2021 Chilean presidential primaries",
        "Primarias presidenciales 2021",
        Group("president_primary", r"2022/12/2021_Primarias_Presidenciales\.zip"),
    ),
    _election(
        date(2021, 11, 21),
        "general",
        "general",
        "2021 Chilean general election",
        "Elecciones presidencial, parlamentarias y de consejeros regionales 2021",
        Group("president", r"2022/12/Resultados_Mesa_Presidenciales_1v_2021-1\.xlsx"),
        Group("senators", r"2022/12/Senadores-1\.zip"),
        Group("deputies", r"2022/12/Diputados-1\.zip"),
        Group("core", r"2022/12/CORE-1\.zip"),
    ),
    _election(
        date(2021, 12, 19),
        "runoff",
        "presidential-runoff",
        "2021 Chilean presidential election, second round",
        "Segunda votación presidencial 2021",
        Group("president", r"2022/12/Resultados_mesa_presidencial_TRICEL_2v_2021-1\.xlsx"),
    ),
    _election(
        date(2022, 9, 4),
        "referendum",
        "plebiscite",
        "2022 Chilean constitutional plebiscite",
        "Plebiscito constitucional 2022",
        Group("plebiscite_exit", r"2023/01/Resultados-Plebiscito-Constitucional-2022\.xlsx"),
    ),
    _election(
        date(2023, 5, 7),
        "general",
        "constitutional-council",
        "2023 Chilean Constitutional Council election",
        "Elección de consejeros constitucionales 2023",
        Group("consejo", r"2023/05/20230507CA\.zip", "ConsejoConsitucional_"),
        Group("consejo_indigenous", r"2023/05/20230507CA\.zip", "ConsitucionalPIng"),
        stage="preliminary",
    ),
    _election(
        date(2025, 6, 29),
        "primary",
        "presidential-primary",
        "2025 Chilean presidential primary",
        "Primaria presidencial 2025",
        Group("president_primary", r"2025/07/Resultados_Preliminares_Primarias_Presidencial_(CL|EXT)_20250701\.xlsx"),
        stage="preliminary",
    ),
    _election(
        date(2025, 11, 16),
        "general",
        "general",
        "2025 Chilean general election",
        "Elecciones presidencial y parlamentarias 2025",
        Group("president", r"2025/11/PRELIMINARES_PRESIDENTE_DE_LA_REPUBLICA\.zip"),
        Group("senators", r"2025/11/PRELIMINARES_SENADORES_CIRCUNSCRIPCION\.zip"),
        Group("deputies", r"2025/11/PRELIMINARES_DIPUTADOS\.zip"),
        stage="preliminary",
    ),
    _election(
        date(2025, 12, 14),
        "runoff",
        "presidential-runoff",
        "2025 Chilean presidential election, second round",
        "Segunda votación presidencial 2025",
        Group("president", r"2025/12/Datos_SegundaVotacion-1\.zip"),
        stage="preliminary",
    ),
)


CE_PRESIDENT = "2022/12/resultados_elecciones_presidenciales_ce_1989_2017_Chile.xlsx"
CE_SENATE = "2022/12/resultados_elecciones_senadores_ce_1989_2017.xlsx"
CE_CHAMBER = "2022/12/resultados_elecciones_diputados_ce_1989_2017.xlsx"
CE_MAYOR = "2022/12/resultados_elecciones_alcaldes_ce_2004_2016.xlsx"
CE_COUNCIL_OLD = "2022/12/resultados_elecciones_concejales_ce_1992_al_2000.xlsx"
CE_COUNCIL = "2022/12/resultados_elecciones_concejales_ce_2004_2016-1.xlsx"
SEX = {"HOMBRE": "V", "VARONES": "V", "MUJER": "M", "MUJERES": "M", "MIXTA": "X"}


@functools.cache
def _ce_book(path: str) -> pl.DataFrame:
    book = fastexcel.read_excel(raw.cached(SOURCE, url(path)).path)
    return book.load_sheet(0, dtypes="string").to_polars()


def ce_read(path: str, member: str) -> pl.DataFrame:
    """Rows for one election day (and presidential round) of a workbook by circunscripcion electoral and sex of table."""
    day, _, round_text = member.partition(":")
    frame = _ce_book(path)
    columns = {_header(c): c for c in frame.columns}
    stamp = next(c for c in frame.columns if c.startswith("Fecha"))
    frame = frame.filter(pl.col(stamp).str.slice(0, 10) == day)
    if "votacionpresidencial" in columns:
        wanted = "SEGUNDA" if round_text == "2" else "PRIMERA|UNICA"
        frame = frame.filter(
            pl.col(columns["votacionpresidencial"]).map_elements(fold, return_dtype=pl.String).str.contains(wanted)
        )
    if frame.is_empty():
        raise ValueError(f"{path} has no rows for {member}")

    def column(name: str) -> pl.Expr:
        return pl.col(columns[name]).str.strip_chars() if name in columns else pl.lit(None, pl.String)

    circ = (
        pl.concat_str(column("circunscripcionelectoral"), column("nombrecircunscripcionelectoral"), separator=" ")
        if "nombrecircunscripcionelectoral" in columns
        else column("circunscripcionelectoral")
    )
    elected = next((c for c in columns if c.startswith("electo")), None)
    lista = column("lista")
    if "nominadoa" in columns:
        # A primaries workbook leaves the list blank on its blank and null rows; each district held one list's primary.
        lista = lista.fill_null(lista.drop_nulls().first().over(column("distrito")))
    return frame.select(
        region=column("idregion"),
        comuna=column("comuna"),
        circ=circ,
        mesa=pl.lit(""),
        tipo=column("sexomesa").map_elements(lambda text: SEX.get(fold(text), ""), return_dtype=pl.String),
        cs=column("circunscripcionsenatorial").str.extract(r"(\d+)"),
        distrito=column("distrito").str.extract(r"(\d+)"),
        circ_prov=pl.lit(None, pl.String),
        electores=pl.lit(None, pl.String),
        voto=pl.lit(None, pl.String),
        lista=lista,
        pacto=pl.lit(None, pl.String),
        subpacto=pl.lit(None, pl.String),
        partido=column("partido"),
        candidato=column("candidatoa"),
        pueblo=pl.lit(None, pl.String),
        mesa_code=pl.lit(None, pl.String),
        elected=column(elected) if elected else pl.lit(None, pl.String),
        votos=pl.col(frame.columns[-1]).str.replace(r"\.0+$", "").cast(pl.Int64),
        total=pl.lit(False),
        file=pl.lit(path),
        sheet=pl.lit(member),
    ).filter(pl.col("votos").is_not_null())


def _ce(day: date, *groups: tuple[str, str], round_number: int = 1) -> tuple[Group, ...]:
    member = f"{day}:{round_number}"
    return tuple(Group(kind, re.escape(path), member) for kind, path in groups)


def _ce_elections() -> tuple[Election, ...]:
    def general(day: date, president: bool, title: str, es: str) -> Election:
        groups = [("senators", CE_SENATE), ("deputies", CE_CHAMBER)]
        if president:
            groups.insert(0, ("president", CE_PRESIDENT))
        return _election(day, "general", "general", title, es, *_ce(day, *groups))

    def runoff(day: date, year: int) -> Election:
        return _election(
            day,
            "runoff",
            "presidential-runoff",
            f"{year} Chilean presidential election, second round",
            f"Segunda votación presidencial {year}",
            Group("president", re.escape(CE_PRESIDENT), f"{day}:2"),
        )

    def municipal(day: date, council: str, mayor: bool, stamp: date | None = None) -> Election:
        groups = [("council", council)] + ([("mayor", CE_MAYOR)] if mayor else [])
        return _election(
            day,
            "local",
            "municipal",
            f"{day.year} Chilean municipal elections",
            f"Elecciones municipales {day.year}",
            *_ce(stamp or day, *groups),
        )

    return (
        general(
            date(1989, 12, 14), True, "1989 Chilean general election", "Elecciones presidencial y parlamentarias 1989"
        ),
        # The workbook dates the election of 28 June 1992 to 28 October.
        municipal(date(1992, 6, 28), CE_COUNCIL_OLD, False, date(1992, 10, 28)),
        general(
            date(1993, 12, 11), True, "1993 Chilean general election", "Elecciones presidencial y parlamentarias 1993"
        ),
        municipal(date(1996, 10, 27), CE_COUNCIL_OLD, False),
        general(date(1997, 12, 11), False, "1997 Chilean parliamentary election", "Elecciones parlamentarias 1997"),
        _election(
            date(1999, 12, 12),
            "general",
            "presidential",
            "1999 Chilean presidential election",
            "Elección presidencial 1999",
            *_ce(date(1999, 12, 12), ("president", CE_PRESIDENT)),
        ),
        runoff(date(2000, 1, 16), 1999),
        municipal(date(2000, 10, 29), CE_COUNCIL_OLD, False),
        general(date(2001, 12, 16), False, "2001 Chilean parliamentary election", "Elecciones parlamentarias 2001"),
        municipal(date(2004, 10, 31), CE_COUNCIL, True),
        general(
            date(2005, 12, 11), True, "2005 Chilean general election", "Elecciones presidencial y parlamentarias 2005"
        ),
        runoff(date(2006, 1, 15), 2005),
        municipal(date(2008, 10, 26), CE_COUNCIL, True),
        general(
            date(2009, 12, 13), True, "2009 Chilean general election", "Elecciones presidencial y parlamentarias 2009"
        ),
        runoff(date(2010, 1, 17), 2009),
    )


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CHILE = "01016eed-79a5-5393-a8a1-e2f5adb6103c"
# Contests of 1989-2010 whose candidate totals differ from the ones the historical results site prints on its own
# district and national pages. `cl.historial` publishes its commune counts, which equal that site's printed totals,
# as the default result set of each, superseding this one, which stays as the non-default set.
SUPERSEDED = frozenset(
    {
        (date(1989, 12, 14), "deputies", "45"),
        (date(1989, 12, 14), "senators", "4"),
        (date(1989, 12, 14), "senators", "9"),
        (date(1989, 12, 14), "senators", "12"),
        (date(1989, 12, 14), "senators", "17"),
        (date(1993, 12, 11), "president", ""),
        (date(1997, 12, 11), "deputies", "33"),
        (date(1999, 12, 12), "president", ""),
    }
)
SERVEL = key("source", SOURCE)
PRESIDENCY = key("office", "president")
CHAMBER = key("body", "chamber")
SENATE = key("body", "senate")
CONVENTION = key("body", "convention")
CONSEJO = key("body", "consejo")
SPECIAL = {"VOTOS NULOS": 5, "NULOS": 5, "VOTO NULO": 5, "VOTOS EN BLANCO": 6, "BLANCOS": 6, "VOTO EN BLANCO": 6}
NO_PARTY = re.compile(r"^(INDEPENDIENTES?( .*)?|IND|CANDIDATURA INDEPENDIENTE|)$")
LISTED = {"deputies", "senators", "core", "council", "convention", "consejo"}
# Senate circunscripciones since 2017 follow the regions from north to south.
CS_REGION = {
    1: 15,
    2: 1,
    3: 2,
    4: 3,
    5: 4,
    6: 5,
    7: 13,
    8: 6,
    9: 7,
    10: 16,
    11: 8,
    12: 9,
    13: 14,
    14: 10,
    15: 11,
    16: 12,
}
REFORM = date(2017, 1, 1)
METHOD = (
    "Votes per voting table (mesa) as the Servicio Electoral de Chile (Servel) publishes them in its results "
    "spreadsheets{court}. A row of the source is one option at one table; tables merged on election day (mesas "
    "fusionadas) report once under the table that counted them. Null and blank votes are stored as invalid and blank "
    "ballots, and the table's electoral roll, where the file prints it, as registered voters. Tables abroad are "
    "placed in no area."
)
COURT = {
    "certified": ", with the counts as qualified by the electoral court (Tribunal Calificador de Elecciones or the "
    "regional electoral court)",
    "official": ", with the provisional counts of the scrutiny colleges",
    "preliminary": ", with the preliminary counts reported on election night",
}
SYSTEMS: dict[str, tuple[date, date | None, dict[str, object], str]] = {
    "president": (
        date(1989, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate with an absolute majority of valid votes is elected; otherwise the two leading candidates meet in "
            "a second round four weeks later."
        ),
    ),
    "binomial": (
        date(1989, 1, 1),
        REFORM,
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}},
        (
            "Two seats per district, divided among lists by D'Hondt; within a list the seats go to its most voted "
            "candidates (the binomial system)."
        ),
    ),
    "proportional": (
        REFORM,
        None,
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}},
        (
            "Seats divided among lists by D'Hondt, then within each pact among subpacts and parties, and within each "
            "list to its most voted candidates."
        ),
    ),
    "council": (
        date(1992, 1, 1),
        None,
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}},
        "Seats divided among lists by D'Hondt (the cifra repartidora); within each list to its most voted candidates.",
    ),
    "mayor": (date(2004, 1, 1), None, {"formula": "fptp"}, "The candidate with the most votes is elected."),
    "governor": (
        date(2021, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.4, "advance": 2},
        (
            "A candidate with at least 40% of valid votes is elected; otherwise the two leading candidates meet in a "
            "second round."
        ),
    ),
    "plebiscite": (date(1988, 1, 1), None, {"formula": "referendum"}, "The option with most valid votes carries."),
}
KINDS: dict[str, tuple[str, str | None]] = {
    "president": ("office", "president"),
    "president_primary": ("office", None),
    "deputies": ("list_allocation", "deputies"),
    "deputies_primary": ("body_seats", None),
    "senators": ("list_allocation", "deputies"),
    "core": ("list_allocation", "council"),
    "mayor": ("office", "mayor"),
    "mayor_primary": ("office", None),
    "council": ("list_allocation", "council"),
    "governor": ("office", "governor"),
    "governor_primary": ("office", None),
    "convention": ("list_allocation", "proportional"),
    "convention_indigenous": ("body_seats", None),
    "consejo": ("list_allocation", "proportional"),
    "consejo_indigenous": ("body_seats", None),
    "plebiscite": ("ballot_question", "plebiscite"),
    "plebiscite_body": ("ballot_question", "plebiscite"),
    "plebiscite_exit": ("ballot_question", "plebiscite"),
}
QUESTIONS = {
    "plebiscite": "¿Quiere usted una Nueva Constitución?",
    "plebiscite_body": "¿Qué tipo de órgano debiera redactar la Nueva Constitución?",
    "plebiscite_exit": "¿Aprueba usted el texto de Nueva Constitución propuesto por la Convención Constitucional?",
}


def system(kind: str, day: date) -> str | None:
    name = KINDS[kind][1]
    if name == "deputies":
        name = "binomial" if day < REFORM else "proportional"
    return None if name is None else key("electoral_system", name)


def office(kind: str, code: str) -> str:
    return key("office", kind, code)


def body(kind: str, code: str) -> str:
    return key("body", kind, code)


def party(name: str) -> str:
    return key("party", name)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def elections() -> list[Election]:
    return sorted([*_ce_elections(), *ELECTIONS], key=lambda election: election.day)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{election.day}" for election in elections())]


def paths(group: Group) -> list[str]:
    found = [path for path in FILES if re.fullmatch(group.pattern, path)]
    if not found:
        raise LookupError(f"no file matches {group.pattern}")
    return found


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for election in elections():
        out = PARSED / f"{election.day}.parquet"
        if out.exists():
            continue
        frames = []
        for group in election.groups:
            for path in paths(group):
                if "_ce_" in path:
                    frame = ce_read(path, group.member)
                else:
                    frame = read(url(path))
                    if group.member:
                        frame = frame.filter(pl.col("file").str.contains(group.member))
                _totals(path, frame)
                frames.append(frame.filter(~pl.col("total")).with_columns(kind=pl.lit(group.kind), path=pl.lit(path)))
        rows = pl.concat(frames, how="diagonal_relaxed")
        rows.write_parquet(out)
        print(f"{election.day}: {rows.height:,} rows, {rows['votos'].sum():,} votes", flush=True)
    registry().write()
    for election in elections():
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def _totals(path: str, frame: pl.DataFrame) -> None:
    """Compare each sheet's printed grand total, where it has one, with the sum of its rows."""
    for (member, sheet), part in frame.group_by("file", "sheet"):
        printed = part.filter(pl.col("total"))["votos"]
        if printed.len() == 0:
            continue
        counted = int(part.filter(~pl.col("total"))["votos"].sum())
        if max(int(value) for value in printed.to_list()) != counted:
            # Some sheets carry a stray figure below the rows with no table or commune; when it does not equal the
            # rows' sum it is not a total, and it is left out.
            print(f"{path} {member} {sheet}: rows sum to {counted:,}; a row without a table prints {printed.to_list()}")


def folded(frame: pl.DataFrame, column: str) -> pl.Expr:
    """fold() of a column, computed once per distinct value."""
    values = frame[column].drop_nulls().unique().to_list()
    return pl.col(column).replace_strict({v: fold(v) for v in values}, default=None, return_dtype=pl.String)


def rows(election: Election) -> pl.DataFrame:
    frame = pl.read_parquet(PARSED / f"{election.day}.parquet")
    frame = frame.with_columns(
        comuna_fold=folded(frame, "comuna"),
        circ_fold=folded(frame, "circ"),
        pacto_fold=folded(frame, "pacto"),
        pueblo_fold=folded(frame, "pueblo"),
        name_fold=folded(frame, "candidato"),
        party_fold=folded(frame, "partido"),
        circ_prov_fold=folded(frame, "circ_prov"),
        tipo=pl.col("tipo").fill_null("").str.to_uppercase(),
        region=pl.col("region").fill_null("X"),
    )
    return frame.with_columns(special=pl.col("name_fold").replace_strict(SPECIAL, default=None, return_dtype=pl.Int16))


# Older spellings in Servel's files of the communes on its current list.
ALIASES = {
    "AISEN": "AYSEN",
    "COIHAIQUE": "COYHAIQUE",
    "MARCHIGUE": "MARCHIHUE",
    "LLAY LLAY": "LLAILLAY",
    "CALERA": "LA CALERA",
    "PAIGUANO": "PAIHUANO",
    "OHIGGINS": "O HIGGINS",
    "NAVARINO": "CABO DE HORNOS",
    "CABO DE HORNOS EX NAVARINO": "CABO DE HORNOS",
    "TREHUACO": "TREGUACO",
    # Antártica has no municipality of its own; Cabo de Hornos governs it.
    "CABO DE HORNOS Y ANTARTICA": "CABO DE HORNOS",
    "CABO DE HORNOS EX NAVARINO Y ANTARTICA": "CABO DE HORNOS",
    "TIL TIL": "TILTIL",
}


@functools.cache
def territories() -> dict[str, tuple[str, int, int]]:
    """Servel's list of the 346 communes: folded name to (name, region, 2017 district)."""
    book = fastexcel.read_excel(raw.cached(SOURCE, url(TERRITORIES)).path)
    grid = book.load_sheet(0, dtypes="string").to_polars()
    found: dict[str, tuple[str, int, int]] = {}
    for cs, district, _, name in grid.iter_rows():
        found[fold(name)] = (str(name).strip(), CS_REGION[int(cs)], int(district))
    return found


def comuna(name: str | None) -> str | None:
    folded = fold(name)
    folded = ALIASES.get(folded, folded)
    return folded if folded in territories() else None


def _comunas(frame: pl.DataFrame) -> pl.DataFrame:
    names = frame["comuna_fold"].unique().to_list()
    mapping = {name: comuna(name) for name in names if name is not None}
    return frame.with_columns(
        comuna_code=pl.col("comuna_fold").replace_strict(mapping, default=None, return_dtype=pl.String)
    )


def _headed(head: pl.Expr, pact: pl.Expr) -> pl.Expr:
    """A primary's contest code, the place and the pact; a file that names no pact gives the place alone."""
    return pl.when(pact == "").then(head).otherwise(pl.concat_str(head, pact, separator=":"))


def _contest_code(kind: str) -> pl.Expr:
    pact = pl.coalesce(pl.col("lista"), pl.col("pacto_fold"), pl.lit(""))
    district = pl.col("distrito").cast(pl.Int32, strict=False).cast(pl.String)
    cs = pl.col("cs").cast(pl.Int32, strict=False).cast(pl.String)
    match kind:
        case "president" | "plebiscite" | "plebiscite_body" | "plebiscite_exit" | "consejo_indigenous":
            return pl.lit("")
        case "president_primary":
            return pact
        case "governor_primary":
            return _headed(pl.col("region"), pact)
        case "mayor_primary":
            return _headed(pl.col("comuna_code"), pact)
        case "deputies_primary":
            return pl.concat_str(district, pact, separator=":")
        case "deputies" | "convention":
            return district
        case "senators" | "consejo":
            return cs
        case "core":
            return pl.concat_str(pl.col("region"), pl.col("circ_prov_fold"), separator=":")
        case "mayor" | "council":
            return pl.col("comuna_code")
        case "governor":
            return pl.col("region")
        case "convention_indigenous":
            return pl.col("pueblo_fold")
    raise ValueError(kind)


def _unit_code(day: date) -> pl.Expr:
    table = pl.concat_str(
        pl.lit(str(day)),
        pl.col("region"),
        pl.col("comuna_fold"),
        pl.col("circ_fold").fill_null(""),
        pl.concat_str(pl.col("mesa"), pl.col("tipo")),
        separator=":",
    )
    return (
        pl.when(pl.col("mesa_code").is_not_null()).then(pl.concat_str(pl.lit(f"{day}:"), "mesa_code")).otherwise(table)
    )


def prepared(election: Election) -> pl.DataFrame:
    frame = _comunas(rows(election))
    frame = pl.concat(
        [
            part.with_columns(contest=_contest_code(kind))
            for (kind,), part in frame.group_by("kind", maintain_order=True)
        ]
    )
    coarse = pl.col("path").str.contains("_ce_")
    fine = frame.filter(~coarse).select("kind", "contest").unique()
    frame = pl.concat([frame.filter(~coarse), frame.filter(coarse).join(fine, on=["kind", "contest"], how="anti")])
    frame = frame.with_columns(unit=_unit_code(election.day))
    missing = frame.filter(pl.col("contest").is_null())
    if missing.height:
        raise ValueError(f"{election.day}: {missing.height:,} rows have no contest: {missing.head(3).rows()}")
    return frame


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Chile's regions, communes, electoral districts, offices, bodies, electoral systems, and parties from Servel",
    )
    p.external.add(CHILE)
    p.add(
        "source",
        id=SERVEL,
        kind="official_body",
        publisher="Servicio Electoral de Chile (Servel)",
        title="Resultados electorales por mesa",
        url="https://www.servel.cl/biblioteca-de-documentos/resultados-electorales-historicos/",
        license="No license stated: official results published by Servel; facts published with attribution",
        license_url=None,
        attribution="Servicio Electoral de Chile (Servel)",
    )
    for path in FILES:
        p.artifact(raw.cached(SOURCE, url(path)), SERVEL)
    for code, rank, kind, noun in (
        ("cl.region", 1, "administrative", "region"),
        ("cl.comuna", 3, "administrative", "commune"),
        ("cl.distrito", 2, "electoral_district", "electoral district"),
        ("cl.distrito_1989", 2, "electoral_district", "electoral district (1989–2013)"),
        ("cl.circunscripcion", 2, "electoral_district", "senatorial constituency"),
        ("cl.circunscripcion_1989", 2, "electoral_district", "senatorial constituency (1989–2013)"),
        ("cl.circ_provincial", 2, "electoral_district", "provincial constituency"),
        ("cl.mesa", 4, "precinct", "voting table"),
        ("cl.circ_electoral", 4, "precinct", "electoral subdivision, by sex of table"),
    ):
        p.add("scheme", code=code, polity_id=CHILE, kind=kind, rank=rank, noun=noun, code_system=None)

    def place(scheme: str, code: str, text: str, parent: str | None) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        if parent is not None:
            p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "es", "official")

    for number, text in REGIONS.items():
        place("cl.region", str(number), f"Región de {text}" if number != 13 else "Región Metropolitana", None)
        p.add("office", id=office("governor", str(number)), polity_id=CHILE, kind="governor")
        p.name(office("governor", str(number)), f"Gobernador regional de {text}", "es", "official")
        p.add("body", id=body("core", str(number)), polity_id=CHILE, kind="council")
        p.name(body("core", str(number)), f"Consejo Regional de {text}", "es", "official")
    for code, (text, region, _) in sorted(territories().items()):
        place("cl.comuna", code, text, area("cl.region", str(region)))
        p.add("office", id=office("mayor", code), polity_id=CHILE, kind="mayor")
        p.name(office("mayor", code), f"Alcalde de {text}", "es", "official")
        p.add("body", id=body("council", code), polity_id=CHILE, kind="council")
        p.name(body("council", code), f"Concejo Municipal de {text}", "es", "official")
    for number in range(1, 29):
        place("cl.distrito", str(number), f"Distrito {number}", None)
    for number in range(1, 61):
        place("cl.distrito_1989", str(number), f"Distrito {number} (1989–2013)", None)
    for number in range(1, 17):
        region = CS_REGION[number]
        place("cl.circunscripcion", str(number), f"Circunscripción Senatorial {number}", area("cl.region", str(region)))
    for number in range(1, 20):
        place("cl.circunscripcion_1989", str(number), f"Circunscripción Senatorial {number} (1989–2013)", None)
    provincial: dict[str, str] = {}
    parties: dict[str, str] = {}
    for election in elections():
        frame = pl.read_parquet(PARSED / f"{election.day}.parquet", columns=["kind", "region", "circ_prov", "partido"])
        for region, name in frame.filter(pl.col("kind") == "core").select("region", "circ_prov").unique().iter_rows():
            provincial.setdefault(f"{region}:{fold(name)}", str(name).strip().title())
        for (name,) in frame.select("partido").unique().iter_rows():
            if name is not None and not NO_PARTY.match(fold(name)):
                parties.setdefault(fold(name), " ".join(str(name).split()))
    for code, text in sorted(provincial.items()):
        region = code.split(":")[0]
        place("cl.circ_provincial", code, f"{text} ({REGIONS[int(region)]})", area("cl.region", region))
    for name, text in sorted(parties.items()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=CHILE)
        p.add("external_id", entity_id=id, scheme="cl.servel.party", value=name)
        p.name(id, text, "es", "official")
    p.add("office", id=PRESIDENCY, polity_id=CHILE, kind="head_of_state")
    p.name(PRESIDENCY, "Presidente de la República de Chile", "es", "official")
    p.name(PRESIDENCY, "President of Chile", "en", "official", False)
    for id, kind, es, en in (
        (CHAMBER, "lower_house", "Cámara de Diputadas y Diputados", "Chamber of Deputies"),
        (SENATE, "upper_house", "Senado", "Senate"),
        (CONVENTION, "constituent_assembly", "Convención Constitucional", "Constitutional Convention"),
        (CONSEJO, "constituent_assembly", "Consejo Constitucional", "Constitutional Council"),
    ):
        p.add("body", id=id, polity_id=CHILE, kind=kind)
        p.name(id, es, "es", "official")
        p.name(id, en, "en", "official", False)
    for name, (since, until, rules, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add(
            "electoral_system", id=id, polity_id=CHILE, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return p


def _district(kind: str, day: date, code: str) -> str | None:
    old = day < REFORM
    match kind:
        case "deputies" | "convention":
            return area("cl.distrito_1989" if old else "cl.distrito", code)
        case "deputies_primary":
            return area("cl.distrito_1989" if old else "cl.distrito", code.split(":")[0])
        case "senators" | "consejo":
            return area("cl.circunscripcion_1989" if old else "cl.circunscripcion", code)
        case "core":
            return area("cl.circ_provincial", code)
        case "mayor" | "council":
            return area("cl.comuna", code)
        case "mayor_primary":
            return area("cl.comuna", code.split(":")[0])
        case "governor" | "governor_primary":
            return area("cl.region", code.split(":")[0])
    return None


def _title(kind: str, day: date, code: str, pact: str | None) -> str:
    head = code.split(":")[0]
    places = territories()
    match kind:
        case "president":
            return "Presidente de la República"
        case "president_primary":
            return f"Primaria presidencial, {pact}"
        case "deputies" | "convention":
            return f"Distrito {code}"
        case "deputies_primary":
            return f"Primaria de diputados, distrito {head}, {pact}"
        case "senators" | "consejo":
            return f"Circunscripción Senatorial {code}"
        case "consejo_indigenous":
            return "Escaño de pueblos indígenas"
        case "core":
            region, name = code.split(":", 1)
            return f"Consejo Regional, circunscripción {name.title()} ({REGIONS[int(region)]})"
        case "mayor":
            return f"Alcalde de {places[code][0]}"
        case "council":
            return f"Concejo Municipal de {places[code][0]}"
        case "mayor_primary":
            return f"Primaria de alcalde de {places[head][0]}" + (f", {pact}" if pact else "")
        case "governor":
            return f"Gobernador regional de {REGIONS[int(code)]}"
        case "governor_primary":
            return f"Primaria de gobernador regional de {REGIONS[int(head)]}" + (f", {pact}" if pact else "")
        case "convention_indigenous":
            return f"Escaño reservado, pueblo {code.title()}"
    return QUESTIONS[kind]


def _seat(kind: str, code: str) -> tuple[str | None, str | None]:
    head = code.split(":")[0]
    match kind:
        case "president" | "president_primary":
            return PRESIDENCY, None
        case "deputies" | "deputies_primary":
            return None, CHAMBER
        case "senators":
            return None, SENATE
        case "core":
            return None, body("core", head)
        case "mayor" | "mayor_primary":
            return office("mayor", head), None
        case "council":
            return None, body("council", head)
        case "governor" | "governor_primary":
            return office("governor", head), None
        case "convention" | "convention_indigenous":
            return None, CONVENTION
        case "consejo" | "consejo_indigenous":
            return None, CONSEJO
    return None, None


def _previous(election: Election, kind: str) -> Election | None:
    if election.kind != "runoff":
        return None
    earlier = [e for e in elections() if e.day < election.day and any(g.kind == kind for g in e.groups)]
    return earlier[-1] if earlier else None


def build(election: Election) -> Package:
    day = election.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{election.en} by voting table from Servel",
        depends=[
            f"{SOURCE}/registry",
            *sorted({f"{SOURCE}/{e.day}" for g in election.groups if (e := _previous(election, g.kind)) is not None}),
        ],
    )
    p.external.add(CHILE)
    for group in election.groups:
        for path in paths(group):
            p.uses(raw.cached(SOURCE, url(path)))
    frame = prepared(election)
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=CHILE,
        kind=election.kind,
        date_from=day,
        date_to=election.last,
        date_precision="day",
        status="held",
    )
    p.name(election_id, election.en, "en", "official")
    p.name(election_id, election.es, "es", "native", False)
    base = f"cl/{day}-{election.word}"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    method = METHOD.format(court=COURT[election.stage])

    units = frame.select("unit", "region", "comuna_code", "mesa").unique("unit", keep="first")
    p.frame(
        "reporting_unit",
        units.select(
            id=pl.col("unit").map_elements(lambda code: key("unit", code), return_dtype=pl.String),
            polity_id=pl.lit(CHILE),
            scheme_code=pl.when(pl.col("mesa") == "").then(pl.lit("cl.circ_electoral")).otherwise(pl.lit("cl.mesa")),
            code=pl.col("unit"),
            area_relation=pl.when(pl.col("region") == "X").then(pl.lit("unlocated")).otherwise(pl.lit("within")),
            area_id=pl.when(pl.col("comuna_code").is_not_null())
            .then(pl.concat_str(pl.lit("cl.comuna:"), "comuna_code"))
            .when(pl.col("region") != "X")
            .then(pl.concat_str(pl.lit("cl.region:"), "region"))
            .otherwise(None)
            .map_elements(lambda text: area(*text.split(":", 1)), return_dtype=pl.String),
            kind=pl.when(pl.col("region") == "X").then(pl.lit("abroad_mission")).otherwise(pl.lit("standard")),
        ),
    )
    unit_ids = dict(zip(units["unit"], (key("unit", code) for code in units["unit"]), strict=True))

    slugs: set[str] = set()
    values: list[pl.DataFrame] = []
    for (kind, code), part in frame.group_by("kind", "contest", maintain_order=True):
        kind, code = str(kind), str(code)
        contest = key("contest", day, kind, code)
        result_set = key("result_set", day, kind, code)
        contest_kind, _ = KINDS[kind]
        tail = code.partition(":")[2] if kind in ("mayor_primary", "governor_primary") else code.split(":")[-1]
        pact = next((str(v) for v in part["pacto"].drop_nulls().head(1)), None) or (tail or None)
        title = _title(kind, day, code, pact)
        office_id, body_id = _seat(kind, code)
        previous = _previous(election, kind)
        seats = None if contest_kind in ("list_allocation", "body_seats") else 1
        if contest_kind == "ballot_question":
            seats = None
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind=contest_kind,
            office_id=office_id,
            body_id=body_id,
            district_area_id=_district(kind, day, code),
            magnitude=seats,
            round_number=2 if previous is not None else 1,
            previous_round_id=None if previous is None else key("contest", previous.day, kind, code),
            electoral_system_id=system(kind, day),
            default_result_kind="counted",
        )
        p.name(contest, title, "es", "official")
        path = f"{base}/{changeset.slug(title)}"
        if path in slugs:
            path = f"{path}-{changeset.slug(code)}"
        slugs.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        if contest_kind == "ballot_question":
            p.add("ballot_question", contest_id=contest, type="plebiscite")
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage=election.stage,
            kind="counted",
            source_id=SERVEL,
            status="active",
            is_default=(day, kind, code) not in SUPERSEDED,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        if (day, kind, code) in SUPERSEDED:
            p.add(
                "note",
                id=key("note", result_set, "superseded"),
                entity_id=result_set,
                kind="caveat",
                body_md="Some candidates' totals here differ slightly from the totals the Ministry of the Interior's "
                "historical results site prints for this contest; its commune counts, which add up to those printed "
                "totals, are the default result set.",
                lang="en",
            )
        if pact is None and kind in ("mayor_primary", "governor_primary"):
            p.add(
                "note",
                id=key("note", result_set, "pact"),
                entity_id=result_set,
                kind="caveat",
                body_md="The source does not name the pact of each primary. Every candidate listed for the place is "
                "shown in this one contest, so a commune where more than one pact held a primary appears as a single "
                "contest. The source's own per-table totals disagree with the sum of its options at some tables.",
                lang="en",
            )
        values.append(_options(p, election, kind, code, contest, result_set, part, unit_ids))
    facts = pl.concat(values)
    duplicated = facts.filter(pl.struct("result_set_id", "unit_id", "option_id", "measure").is_duplicated())
    if duplicated.height:
        raise ValueError(f"{day}: {duplicated.height:,} repeated facts, such as {duplicated.head(3).rows()}")
    p.frame("result_value", facts)
    return p


def _options(
    p: Package,
    election: Election,
    kind: str,
    code: str,
    contest: str,
    result_set: str,
    part: pl.DataFrame,
    unit_ids: dict[str, str],
) -> pl.DataFrame:
    day = election.day
    listed = kind in LISTED
    answers = KINDS[kind][0] == "ballot_question"
    candidates = part.filter(pl.col("special").is_null())
    lists: dict[str, str] = {}
    if listed:
        letters = candidates.filter(pl.col("lista").is_not_null()).group_by("lista").agg(pl.col("pacto").first())
        for position, (letter, pact) in enumerate(sorted(letters.iter_rows(), key=lambda r: (len(r[0]), r[0])), 1):
            option = lists[letter] = key("option", day, kind, code, "list", letter)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                ballot_position=position,
                ballot_label=f"{letter}. {' '.join(str(pact or letter).split())}",
            )
    choices = candidates.group_by("lista", "name_fold").agg(
        pl.col("candidato").first(), pl.col("party_fold").first(), pl.col("voto").cast(pl.Int32, strict=False).min()
    )
    options: dict[tuple[str | None, str], str] = {}
    for letter, folded, text, party_fold, number in choices.sort("voto", "name_fold").iter_rows():
        option = options[(letter, folded)] = key("option", day, kind, code, letter or "", folded)
        label = " ".join(str(text).split())
        member = None if party_fold is None or NO_PARTY.match(party_fold) else party(party_fold)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer" if answers else "candidate",
            parent_option_id=lists.get(letter) if listed and letter is not None else None,
            party_id=member,
            ballot_position=number,
            ballot_label=label,
        )
        if answers:
            continue
        person = key("person", day, kind, code, folded)
        p.add("person", id=person)
        p.name(person, label, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", day, kind, code, letter or "", folded),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
            member_of_party_id=member,
            is_independent=member is None,
        )
    declared = candidates.filter(pl.col("path").str.contains("_ce_"), pl.col("elected").is_not_null())
    for letter, folded, flag in declared.select("lista", "name_fold", "elected").unique().iter_rows():
        text = fold(flag)
        outcome = "advanced" if "VOTACION" in text else None if text in ("", "NO") else "elected"
        if outcome is not None and (letter, folded) in options:
            p.add("outcome", result_set_id=result_set, option_id=options[(letter, folded)], kind=outcome)
    option_map = pl.DataFrame(
        {
            "lista": [k[0] for k in options],
            "name_fold": [k[1] for k in options],
            "option_id": list(options.values()),
        },
        schema={"lista": pl.String, "name_fold": pl.String, "option_id": pl.String},
    )
    unit = pl.col("unit").replace_strict(unit_ids, return_dtype=pl.String)
    votes = (
        candidates.join(option_map, on=["lista", "name_fold"], how="left", nulls_equal=True)
        .group_by("unit", "option_id")
        .agg(pl.col("votos").sum(), pl.len().alias("n"))
    )
    blank = (
        part.filter(pl.col("special").is_not_null()).group_by("unit", "special").agg(pl.col("votos").sum(), pl.len())
    )
    repeated = votes.filter(pl.col("n") > 1)["unit"].n_unique()
    if repeated:
        p.add(
            "note",
            id=key("note", result_set, "repeated"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"For {repeated:,} reporting unit{'s' if repeated > 1 else ''}, the source lists the same unit more "
            "than once with different figures (rows split by district, or two tables printed under one number); "
            "the figures are added together.",
            lang="en",
        )
    roll = (
        part.filter(pl.col("electores").is_not_null())
        .group_by("unit")
        .agg(pl.col("electores").cast(pl.Int64, strict=False).first())
    )
    return pl.concat(
        [
            votes.select(
                result_set_id=pl.lit(result_set),
                unit_id=unit,
                option_id="option_id",
                measure=pl.lit(1, pl.Int16),
                value="votos",
            ),
            blank.select(
                result_set_id=pl.lit(result_set),
                unit_id=unit,
                option_id=pl.lit(None, pl.String),
                measure=pl.col("special").cast(pl.Int16),
                value="votos",
            ),
            roll.select(
                result_set_id=pl.lit(result_set),
                unit_id=unit,
                option_id=pl.lit(None, pl.String),
                measure=pl.lit(2, pl.Int16),
                value="electores",
            ),
        ]
    ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))
