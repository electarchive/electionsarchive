import io
import re
import shutil
import unicodedata
import zipfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "co.registraduria"
BASE = "https://observatorio.registraduria.gov.co/"
CATALOG = f"{BASE}views/electoral/historicos-resultados.php"
PARSED = DATA / "parsed" / SOURCE
SCRATCH = DATA / "tmp" / SOURCE
FILES = {
    "presidente-2026-1": "anexos/MMV_Presidente1V_2026.zip",
    "presidente-2026-2": "anexos/MMV_Presidente2V_2026.zip",
    "congreso-2026": "anexos/MMV_CONGRESO_2026.zip",
    "presidente-2022-1": "anexos/MMV_NACIONAL_PRESIDENTE_2022_1v.zip",
    "presidente-2022-2": "anexos/MMV_NACIONAL_PRESIDENTE_2022_2v.zip",
    "presidente-2018-1": "anexos/MMV_NACIONAL_PRESIDENTE_2018_1v.zip",
    "presidente-2018-2": "anexos/MMV_NACIONAL_PRESIDENTE_2018_2v.zip",
    "territoriales-2023": "comprimidos/MMV_TERRITORIALES2023_COLOMBIA.zip",
    "territoriales-2019": "comprimidosTwo/MMV_TERRITORIALES2019_COLOMBIA.zip",
}
# The statistics CDs of 1997 to 2015: installers (CAB, MSI, and EXE) around per-year databases. The territorial
# elections among them are parsed by co.registraduria_history; the congressional and presidential ones are not yet.
HISTORIC = {
    1997: "comprimidoThree/1997-1998_ELECCIONES_TERRITORIALES_CONGREGRESO_DE_LA_REPUBLICA_Y_PRESIDENTE.zip",
    2000: "comprimidoThree/2000_ELECCIONES_TERRITORIALES.zip",
    2002: "comprimidoThree/2002_ELECCIONES_PRESIDENTE_DE_LA_REPÚBLICA_Y_CONGRESO_DE_LA_REPÚBLICA.zip",
    2003: "comprimidoThree/2003_ELECCIONES_TERRITORIALES.zip",
    2006: "comprimidoThree/2006_ELECCIONES_PRESIDENTE_DE_LA_REPÚBLICA_Y_CONGRESO_DE_LA_REPÚBLICA.zip",
    2007: "comprimidoThree/2007_ELECCIONES_TERRITORIALES.zip",
    2010: "comprimidoThree/2010_ELECCIONES_PRESIDENTE_DE_LA_REPÚBLICA_Y_CONGRESO_DE_LA_REPÚBLICA.zip",
    2011: "comprimidoThree/2011_ELECCIONES_TERRITORIALES.zip",
    2014: "comprimidoThree/2014_ELECCIONES_PRESIDENTE_DE_LA_REPÚBLICA_Y_CONGRESO_DE_LA_REPÚBLICA.zip",
    2015: "comprimidoThree/2015_ELECCIONES_TERRITORIALES.zip",
}
CONGRESS_2022 = [
    "AMAZONAS", "ANTIOQUIA", "ARAUCA", "ATLANTICO", "BOGOTA", "BOLIVAR", "BOYACA", "CALDAS", "CAQUETA", "CASANARE",
    "CESAR", "CHOCO", "CONSULADOS", "CORDOBA", "CUNDINAMARCA", "GUAINIA", "GUAVIARE", "HUILA", "LA_GUAJIRA",
    "MAGDALENA", "META", "NARINO", "NORTE_DE_SANTANDER", "PUTUMAYO", "QUINDIO", "RISARALDA", "SAN_ANDRES",
    "SANTANDER", "SUCRE", "TOLIMA", "VALLE", "VAUPES", "VICHADA",
]  # fmt: skip
CONGRESS_2022_CAUCA = "congreso22/MMV_CONGRESAO_2022_CAUCA.zip"


def url(path: str) -> str:
    return BASE + quote(path)


def congress_2022() -> list[str]:
    return sorted([*(f"congreso22/MMV_CONGRESO_2022_{name}.zip" for name in CONGRESS_2022), CONGRESS_2022_CAUCA])


def fetch() -> None:
    raw.fetch(SOURCE, CATALOG, suffix=".html")
    failed = []
    for path in [*FILES.values(), *congress_2022()]:
        try:
            artifact = raw.fetch(SOURCE, url(path), suffix=".zip")
        except httpx.HTTPError as error:
            print(f"{path}: {error!r}", flush=True)
            failed.append(path)
            continue
        with artifact.path.open("rb") as file:
            magic = file.read(2)
        if magic != b"PK":
            print(f"{path}: not a zip archive", flush=True)
            failed.append(path)
            continue
        print(f"{path}: {artifact.bytes:,} bytes", flush=True)
    if failed:
        raise SystemExit(f"{len(failed)} files failed: {failed}")


# Elections and normalized tables

PRESIDENT, SENATE, HOUSE, PRIMARY, PEACE = "presidente", "senado", "camara", "consulta", "citrep"
GOVERNOR, ASSEMBLY, MAYOR, COUNCIL, BOARD = "gobernador", "asamblea", "alcalde", "concejo", "jal"
SINGLE = {PRESIDENT, PRIMARY, GOVERNOR, MAYOR}
NATIONAL, TERRITORIAL, INDIGENOUS, AFRO, ABROAD_SEAT = "nacional", "territorial", "indigena", "afro", "internacional"
BLANK, NULL, UNMARKED = "996", "997", "998"
ABROAD = "88"
BOGOTA = ("16", "001")


class Day:
    def __init__(self, day: date, family: str, round_number: int = 1) -> None:
        self.day = day
        self.family = family
        self.round_number = round_number

    @property
    def key(self) -> str:
        return f"{SOURCE}/{self.day}"


DAYS = [
    Day(date(2018, 5, 27), "presidential"),
    Day(date(2018, 6, 17), "presidential", 2),
    Day(date(2019, 10, 27), "local"),
    Day(date(2022, 3, 13), "congress"),
    Day(date(2022, 5, 29), "presidential"),
    Day(date(2022, 6, 19), "presidential", 2),
    Day(date(2023, 10, 29), "local"),
    Day(date(2026, 3, 8), "congress"),
    Day(date(2026, 5, 31), "presidential"),
    Day(date(2026, 6, 21), "presidential", 2),
]
VOTES = {
    "corp": pl.String,
    "circ": pl.String,
    "district": pl.String,
    "dep": pl.String,
    "mun": pl.String,
    "zona": pl.String,
    "puesto": pl.String,
    "mesa": pl.String,
    "party": pl.String,
    "cand": pl.String,
    "stage": pl.String,
    "votes": pl.Int64,
}


def _artifact(path: str) -> raw.Artifact:
    return raw.cached(SOURCE, url(path))


def _zip(path: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(_artifact(path).path)


def _member(archive: zipfile.ZipFile, pattern: str) -> str:
    found = [name for name in archive.namelist() if re.search(pattern, name, re.IGNORECASE)]
    if len(found) != 1:
        raise LookupError(f"{pattern}: {found}")
    return found[0]


@contextmanager
def _extracted(archive: zipfile.ZipFile, member: str, encoding: str = "utf-8") -> Generator[Path]:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    out = SCRATCH / Path(member).name.replace(" ", "_")
    with archive.open(member) as source, out.open("wb") as sink:
        if encoding == "utf-8":
            shutil.copyfileobj(source, sink, 1 << 24)
        else:
            text = io.TextIOWrapper(source, encoding=encoding, newline="")
            while chunk := text.read(1 << 22):
                sink.write(chunk.encode())
    try:
        yield out
    finally:
        out.unlink(missing_ok=True)


def _code(column: str, width: int) -> pl.Expr:
    return pl.col(column).str.strip_chars().cast(pl.Int64).cast(pl.String).str.zfill(width)


def _number(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().cast(pl.Int64).cast(pl.String)


def _specials(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.with_columns(
        party=pl.when(pl.col("cand").is_in([BLANK, NULL, UNMARKED])).then(pl.lit("0")).otherwise(pl.col("party"))
    )


def _clean(text: str | None) -> str:
    return " ".join((text or "").replace("�", "").split())


def _district(frame: pl.LazyFrame) -> pl.LazyFrame:
    dep, mun = pl.col("dep"), pl.col("dep") + ":" + pl.col("mun")
    return frame.with_columns(
        district=pl.when(pl.col("corp") == PRIMARY)
        .then(pl.col("party"))
        .when((pl.col("corp") == PRESIDENT) | pl.col("circ").is_in([NATIONAL, INDIGENOUS, AFRO]))
        .then(pl.lit("CO"))
        .when(pl.col("corp") == PEACE)
        .then(pl.col("district"))
        .when(pl.col("corp").is_in([HOUSE, GOVERNOR, ASSEMBLY]))
        .then(dep)
        .when(pl.col("corp").is_in([MAYOR, COUNCIL]))
        .then(mun)
        .otherwise(mun + ":" + pl.col("district"))
    )


def _finish(frame: pl.LazyFrame, stage: str = "official") -> pl.LazyFrame:
    frame = _district(_specials(frame)).with_columns(stage=pl.lit(stage)).filter(pl.col("votes") > 0)
    return frame.select([pl.col(c).cast(t) for c, t in VOTES.items()])


# Named-column CSV: 2019 and 2023 local elections, 2022 congress

NAMED = {
    "Código Departamento": "dep",
    "Nombre Departamento": "dep_name",
    "Código Municipio": "mun",
    "Nombre Municipio": "mun_name",
    "Código Zona": "zona",
    "Código Puesto": "puesto",
    "Nombre Puesto": "puesto_name",
    "Mesa": "mesa",
    "Código Comuna": "comuna",
    "Nombre Comuna": "comuna_name",
    "Código Corporación": "corp_code",
    "Nombre Corporación": "corp_name",
    "Código Circunscripción": "circ_code",
    "Nombre Circunscripción": "circ_name",
    "Código CITREP": "citrep",
    "Nombre CITREP": "citrep_name",
    "Código Partido": "party",
    "Nombre Partido": "party_name",
    "Código Candidato": "cand",
    "Nombre Candidato": "cand_name",
    "Total Votos": "votes",
}
CORPORATIONS = {
    "PRESIDENTE": PRESIDENT,
    "SENADO": SENATE,
    "CAMARA": HOUSE,
    "GOBERNADOR": GOVERNOR,
    "ASAMBLEA": ASSEMBLY,
    "ALCALDE": MAYOR,
    "CONCEJO": COUNCIL,
    "JAL": BOARD,
}
CIRCUMSCRIPTIONS = {
    "NACIONAL": NATIONAL,
    "DEPARTAMENTAL": TERRITORIAL,
    "TERRITORIAL": TERRITORIAL,
    "INDIGENAS": INDIGENOUS,
    "AFRO-DESCENDIENTES": AFRO,
    "INTERNACIONAL": ABROAD_SEAT,
    "CITREP": "citrep",
}


def _named(path: Path) -> pl.LazyFrame:
    return _named_frame(pl.scan_csv(path, infer_schema=False))


def _named_frame(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    frame = frame.lazy().rename(lambda c: NAMED[c.strip().lstrip("\ufeff")])
    names = frame.collect_schema().names()
    corp = pl.col("corp_name").str.strip_chars().replace_strict(CORPORATIONS)
    circ = (
        pl.col("circ_name").str.strip_chars().replace_strict(CIRCUMSCRIPTIONS)
        if "circ_name" in names
        else pl.lit(TERRITORIAL)
    )
    if "circ_name" in names:
        corp = pl.when(pl.col("circ_name").str.strip_chars() == "CITREP").then(pl.lit(PEACE)).otherwise(corp)
    return frame.with_columns(
        corp=corp,
        circ=circ,
        district=_code("comuna", 2) if "citrep" not in names else _code("citrep", 2),
        dep=_code("dep", 2),
        mun=_code("mun", 3),
        zona=_code("zona", 2),
        puesto=pl.col("puesto").str.strip_chars().str.zfill(2),
        mesa=_number("mesa"),
        party=_number("party"),
        cand=_number("cand"),
        votes=pl.col("votes").cast(pl.Int64),
    )


def _folded(column: str) -> pl.Expr:
    text = pl.col(column).str.to_uppercase()
    for plain, marked in (("A", "ÁÀÄ"), ("E", "ÉÈË"), ("I", "ÍÌÏ"), ("O", "ÓÒÖ"), ("U", "ÚÙÜ"), ("N", "Ñ?")):
        text = text.str.replace_all(f"[{marked}]", plain)
    return text.str.replace_all(r"[^A-Z0-9]", "")


CANDIDATE_KEYS = ["corp", "circ", "dep", "mun", "district", "party", "cand"]
PLACE_KEYS = ["dep", "mun", "zona", "puesto"]


def _disambiguate(frame: pl.LazyFrame, reference: pl.DataFrame | None = None) -> pl.LazyFrame:
    """Recode the candidates and polling places that share a code with another of a different name.

    A few lists print two candidates under one code, and the 2022 congress files print every rural polling place of
    some municipalities as place 00. A shared candidate code keeps its first name (in alphabetical order) and the next
    names take the code plus 1000, 2000, and so on. A shared place code takes the code the same name carries in the
    reference polling-place list, else X1, X2, and so on.
    """
    names = (
        frame.filter(~pl.col("cand").is_in(["0", BLANK, NULL, UNMARKED]))
        .select(*CANDIDATE_KEYS, "cand_name")
        .unique()
        .collect()
    )
    shared = names.filter(pl.len().over(CANDIDATE_KEYS) > 1).with_columns(
        rank=pl.col("cand_name").rank("dense").over(CANDIDATE_KEYS).cast(pl.Int64)
    )
    shared = shared.with_columns(
        recoded=(pl.col("cand").cast(pl.Int64) + 1000 * (pl.col("rank") - 1)).cast(pl.String)
    ).select(*CANDIDATE_KEYS, "cand_name", "recoded")
    places = frame.select(*PLACE_KEYS, "puesto_name").unique().collect()
    clashing = places.filter(pl.len().over(PLACE_KEYS) > 1).with_columns(
        rank=pl.col("puesto_name").rank("dense").over(PLACE_KEYS).cast(pl.Int64)
    )
    if reference is not None:
        known = reference.select("dep", "mun", "zona", folded=_folded("puesto_name"), code="puesto").unique(
            ["dep", "mun", "zona", "folded"], keep="first"
        )
        clashing = clashing.with_columns(folded=_folded("puesto_name")).join(
            known, on=["dep", "mun", "zona", "folded"], how="left"
        )
    else:
        clashing = clashing.with_columns(code=pl.lit(None, pl.String))
    clashing = clashing.with_columns(placed=pl.coalesce("code", pl.lit("X") + pl.col("rank").cast(pl.String))).select(
        *PLACE_KEYS, "puesto_name", "placed"
    )
    if shared.height or clashing.height:
        print(
            f"recoded {shared.height:,} shared candidate codes and {clashing.height:,} shared place codes", flush=True
        )
    return (
        frame.join(shared.lazy(), on=[*CANDIDATE_KEYS, "cand_name"], how="left")
        .join(clashing.lazy(), on=[*PLACE_KEYS, "puesto_name"], how="left")
        .with_columns(cand=pl.coalesce("recoded", "cand"), puesto=pl.coalesce("placed", "puesto"))
        .drop("recoded", "placed")
    )


def _named_labels(frame: pl.LazyFrame) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    places = frame.select(
        "dep", "mun", "zona", "puesto", "comuna", "dep_name", "mun_name", "puesto_name", "comuna_name"
    ).unique(["dep", "mun", "zona", "puesto", "comuna"])
    labels = (
        _district(_specials(frame))
        .select("corp", "circ", "district", "party", "cand", "party_name", "cand_name")
        .unique(["corp", "circ", "district", "party", "cand"])
    )
    return places, labels


def _write(
    key: str, votes: Iterable[pl.LazyFrame], places: Iterable[pl.LazyFrame], labels: Iterable[pl.LazyFrame]
) -> None:
    out = PARSED / key
    out.mkdir(parents=True, exist_ok=True)
    pl.concat(list(votes)).sink_parquet(out / "votes.parquet", engine="streaming")
    pl.concat([p.select(PLACE_COLUMNS) for p in places]).unique(["dep", "mun", "zona", "puesto", "comuna"]).collect(
        engine="streaming"
    ).write_parquet(out / "places.parquet")
    pl.concat([lb.select(LABEL_COLUMNS) for lb in labels]).unique(
        ["corp", "circ", "district", "party", "cand"]
    ).collect(engine="streaming").write_parquet(out / "labels.parquet")
    counted = pl.scan_parquet(out / "votes.parquet").select(pl.len(), pl.col("votes").sum()).collect()
    print(f"{key}: {counted.item(0, 0):,} rows, {counted.item(0, 1):,} votes", flush=True)


PLACE_COLUMNS = [
    pl.col(c).cast(pl.String)
    for c in ("dep", "mun", "zona", "puesto", "comuna", "dep_name", "mun_name", "puesto_name", "comuna_name")
]
LABEL_COLUMNS = [
    pl.col(c).cast(pl.String) for c in ("corp", "circ", "district", "party", "cand", "party_name", "cand_name")
]


def parse_local(year: int) -> None:
    path = FILES[f"territoriales-{year}"]
    archive = _zip(path)
    with _extracted(archive, _member(archive, r"\.csv$")) as csv:
        frame = _disambiguate(_named(csv))
        places, labels = _named_labels(frame)
        _write(str(_local_day(year)), [_finish(frame)], [places], [labels])


def _local_day(year: int) -> date:
    return next(d.day for d in DAYS if d.family == "local" and d.day.year == year)


def parse_congress_2022() -> None:
    votes, places, labels = [], [], []
    reference = pl.read_parquet(PARSED / "2022-05-29" / "places.parquet")
    for path in congress_2022():
        archive = _zip(path)
        member = _member(archive, r"\.csv$")
        data = archive.read(member)
        text = data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
        head = text.split("\n", 1)[0]
        separator = ";" if head.count(";") > head.count(",") else ","
        frame = _disambiguate(
            _named_frame(pl.read_csv(io.StringIO(text), infer_schema=False, separator=separator)), reference
        )
        votes.append(_finish(frame).collect().lazy())
        place, label = _named_labels(frame)
        places.append(place.collect().lazy())
        labels.append(label.collect().lazy())
    _write("2022-03-13", votes, places, labels)


# Semicolon CSV with a header: the 2022 presidential election

PRESIDENT_2022 = {
    "DEP": "dep",
    "DEPNOMBRE": "dep_name",
    "MUN": "mun",
    "MUNNOMBRE": "mun_name",
    "ZONA": "zona",
    "PUESTO": "puesto",
    "PUESNOMBRE": "puesto_name",
    "MESA": "mesa",
    "CORCODIGO": "corp_code",
    "CORNOMBRE": "corp_name",
    "CIR": "circ_code",
    "PAR": "party",
    "PARNOMBRE": "party_name",
    "CAN": "cand",
    "CANNOMBRE": "cand_name",
    "VOTOS": "votes",
}


def parse_president_2022(round_number: int) -> None:
    archive = _zip(FILES[f"presidente-2022-{round_number}"])
    text = archive.read(_member(archive, r"\.csv$")).decode("cp1252")
    raw_frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema=False, quote_char=None).rename(
        lambda c: PRESIDENT_2022[c.strip()]
    )
    frame = _president_frame(raw_frame.lazy())
    places = frame.with_columns(comuna=pl.lit(None, pl.String), comuna_name=pl.lit(None, pl.String))
    _write(
        str(_president_day(2022, round_number)),
        [_finish(frame)],
        [places],
        [_district(_specials(frame)).select(LABEL_COLUMNS).unique()],
    )


def _president_frame(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.with_columns(
        corp=pl.lit(PRESIDENT),
        circ=pl.lit(NATIONAL),
        district=pl.lit("CO"),
        dep=_code("dep", 2),
        mun=_code("mun", 3),
        zona=_code("zona", 2),
        puesto=pl.col("puesto").str.strip_chars().str.zfill(2),
        mesa=_number("mesa"),
        party=_number("party"),
        cand=_number("cand"),
        votes=pl.col("votes").str.strip_chars().cast(pl.Int64),
    )


def _president_day(year: int, round_number: int) -> date:
    return next(
        d.day for d in DAYS if d.family == "presidential" and d.day.year == year and d.round_number == round_number
    )


# Excel workbooks: the 2018 presidential election


def _sheets(path: str) -> dict[str, pl.DataFrame]:
    archive = _zip(path)
    data = archive.read(_member(archive, r"\.xlsx$"))
    return pl.read_excel(io.BytesIO(data), sheet_id=0, infer_schema_length=0)


def parse_president_2018(round_number: int) -> None:
    first = _sheets(FILES["presidente-2018-1"])
    divipol = first["DIVIPOL"].rename(lambda c: c.strip())
    places = divipol.lazy().select(
        dep=_code("DEP", 2),
        mun=_code("MUN", 3),
        zona=_code("ZONA", 2),
        puesto=pl.col("PSTO").str.strip_chars().str.zfill(2),
        comuna=pl.lit(None, pl.String),
        dep_name=pl.col("DEPARTAMENTO"),
        mun_name=pl.col("MUNICIPIO"),
        puesto_name=pl.col("PUESTO"),
        comuna_name=pl.lit(None, pl.String),
    )
    if round_number == 1:
        sheet = first["MMV ESCRUTINIO FINAL"].rename(lambda c: c.strip())
        sheet = sheet.rename({"PSTO": "PUESTO", "CODCAN": "CAN", "PARTIDO": "PARTIDO_NOMBRE"})
    else:
        sheet = next(iter(_sheets(FILES["presidente-2018-2"]).values())).rename(lambda c: c.strip())
        full = (
            first["MMV ESCRUTINIO FINAL"]
            .rename(lambda c: c.strip())
            .select(PAR=_number("PAR"), FULL="CANDIDATO")
            .unique("PAR", keep="first")
        )
        sheet = (
            sheet.with_columns(PAR=_number("PAR"))
            .join(full, on="PAR", how="left")
            .with_columns(CANDIDATO=pl.coalesce("FULL", "CANDIDATO"))
            .drop("FULL")
        )
    frame = _president_frame(
        sheet.lazy()
        .rename({"DEP": "dep", "MUN": "mun", "ZONA": "zona", "PUESTO": "puesto", "MESA": "mesa", "PAR": "party"})
        .rename({"CAN": "cand", "CANDIDATO": "cand_name", "VOTOS": "votes"})
    ).with_columns(party_name=pl.lit(None, pl.String))
    if "PARTIDO_NOMBRE" in sheet.columns:
        frame = frame.with_columns(party_name=pl.col("PARTIDO_NOMBRE"))
    _write(
        str(_president_day(2018, round_number)),
        [_finish(frame)],
        [places],
        [_district(_specials(frame)).select(LABEL_COLUMNS).unique()],
    )


# Fixed layouts of 2026: escrutinio rows separated by semicolons, preconteo rows of fixed width, and the basic files

ESCRUTINIO = ["k", "dep", "mun", "zona", "puesto", "mesa", "comuna", "corp_code", "circ_code", "party", "cand", "votes"]
CORPORATIONS_2026 = {"001": PRESIDENT}
CONGRESS_2026 = {"001": SENATE, "002": HOUSE, "006": PRIMARY, "007": PEACE}
CIRCUMSCRIPTIONS_2026 = {"0": NATIONAL, "1": TERRITORIAL, "4": INDIGENOUS, "5": AFRO, "9": "citrep"}


def _escrutinio(path: Path, corporations: dict[str, str]) -> pl.LazyFrame:
    return (
        pl.scan_csv(
            path,
            has_header=False,
            separator=";",
            new_columns=[*ESCRUTINIO, "end"],
            schema_overrides={c: pl.String for c in ESCRUTINIO},
        )
        .filter(pl.col("votes").cast(pl.Int64) > 0)
        .with_columns(
            corp=pl.col("corp_code").replace_strict(corporations),
            circ=pl.col("circ_code").replace_strict(CIRCUMSCRIPTIONS_2026),
            district=pl.lit(None, pl.String),
            dep=_code("dep", 2),
            mun=_code("mun", 3),
            zona=_code("zona", 2),
            puesto=pl.col("puesto").str.zfill(2),
            mesa=_number("mesa"),
            party=_number("party"),
            cand=_number("cand"),
            votes=pl.col("votes").cast(pl.Int64),
        )
    )


def _preconteo(path: Path, corp: str) -> pl.LazyFrame:
    line = pl.col("line")
    return (
        pl.scan_csv(
            path, has_header=False, new_columns=["line"], separator="\x1f", schema_overrides={"line": pl.String}
        )
        .filter(line.str.len_chars() >= 38)
        .select(
            corp=pl.lit(corp),
            circ=pl.lit(NATIONAL if corp == PRIMARY else "citrep"),
            district=line.str.slice(15, 2),
            dep=line.str.slice(0, 2),
            mun=line.str.slice(2, 3),
            zona=line.str.slice(5, 2).cast(pl.Int64).cast(pl.String).str.zfill(2),
            puesto=line.str.slice(7, 2),
            mesa=line.str.slice(9, 6).cast(pl.Int64).cast(pl.String),
            party=line.str.slice(22, 5).cast(pl.Int64).cast(pl.String),
            cand=line.str.slice(27, 3).cast(pl.Int64).cast(pl.String),
            votes=line.str.slice(30, 8).cast(pl.Int64),
        )
    )


def _fixed(text: str, widths: list[tuple[str, int]]) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row, at = {}, 0
        for name, width in widths:
            row[name] = line[at : at + width].strip()
            at += width
        rows.append(row)
    return rows


DIVIPOL = [
    ("dep", 2),
    ("mun", 3),
    ("zona", 2),
    ("puesto", 2),
    ("dep_name", 12),
    ("mun_name", 30),
    ("puesto_name", 40),
    ("kind", 1),
    ("men", 8),
    ("women", 8),
    ("tables", 6),
    ("comuna", 2),
    ("comuna_name", 30),
]
PARTIES = [("party", 5), ("party_name", 200)]
CANDIDATES = [
    ("corp_code", 3),
    ("circ_code", 1),
    ("dep", 2),
    ("mun", 3),
    ("comuna", 2),
    ("party", 5),
    ("cand", 3),
    ("preferential", 1),
    ("given", 50),
    ("surname", 50),
]


def _basic(archive: zipfile.ZipFile, pattern: str, widths: list[tuple[str, int]]) -> list[dict[str, str]]:
    data = archive.read(_member(archive, pattern))
    return _fixed(data.decode("cp1252"), widths)


def _basics(archive: zipfile.ZipFile, corporations: dict[str, str]) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    places = pl.DataFrame(_basic(archive, r"DIVIPOL_[^/]*\.TXT$", DIVIPOL)).lazy()
    places = places.select(
        dep=_code("dep", 2),
        mun=_code("mun", 3),
        zona=_code("zona", 2),
        puesto=pl.col("puesto").str.zfill(2),
        comuna=pl.col("comuna"),
        dep_name="dep_name",
        mun_name="mun_name",
        puesto_name="puesto_name",
        comuna_name="comuna_name",
    )
    parties = {str(int(row["party"])): _clean(row["party_name"]) for row in _basic(archive, r"PARTIDOS_", PARTIES)}
    labels = []
    for row in _basic(archive, r"CANDIDATOS_", CANDIDATES):
        corp = corporations.get(row["corp_code"])
        if corp is None:
            continue
        circ = CIRCUMSCRIPTIONS_2026[row["circ_code"]]
        party, cand = str(int(row["party"])), str(int(row["cand"]))
        if corp == PEACE:
            district = row["comuna"].zfill(2)
        elif corp == PRIMARY:
            district = party
        elif corp == PRESIDENT or circ in (NATIONAL, INDIGENOUS, AFRO):
            district = "CO"
        else:
            district = row["dep"].zfill(2)
        name = _clean(f"{row['given']} {row['surname']}")
        labels.append(
            {
                "corp": corp,
                "circ": circ,
                "district": district,
                "party": party,
                "cand": cand,
                "party_name": parties.get(party, name if cand == "0" else None),
                "cand_name": name,
            }
        )
    return places, pl.DataFrame(labels).lazy()


def parse_president_2026(round_number: int) -> None:
    outer = _zip(FILES[f"presidente-2026-{round_number}"])
    basics = zipfile.ZipFile(io.BytesIO(outer.read(_member(outer, r"Basicos[^/]*\.zip$"))))
    places, labels = _basics(basics, CORPORATIONS_2026)
    with _extracted(outer, _member(outer, r"_ficheros_MMV_[^/]*ESCRUTINIO\.csv$")) as csv:
        frame = _escrutinio(csv, CORPORATIONS_2026)
        _write(str(_president_day(2026, round_number)), [_finish(frame)], [places], [labels])
    novelty = zipfile.ZipFile(io.BytesIO(outer.read(_member(outer, r"Novedad\d+\.zip$"))))
    lines = novelty.read(_member(novelty, r"\.csv$")).decode("utf-8", "replace").splitlines()
    separator = ";" if lines[0].count(";") > lines[0].count(",") else ","
    header = lines[0].split(separator)
    tail = header[-16:]
    records = []
    for line in lines[1:]:
        parts = line.split(separator)
        if len(parts) < len(header):
            continue
        record = dict(zip(tail, parts[-16:], strict=True))
        record["id"] = parts[0]
        records.append(record)
    rows = pl.DataFrame(records)
    rows.select(
        dep=pl.col("id").str.slice(2, 2),
        mun=pl.col("id").str.slice(4, 3),
        zona=pl.col("id").str.slice(7, 2),
        puesto=pl.col("id").str.slice(9, 2),
        mesa=pl.col("id").str.slice(11).cast(pl.Int64).cast(pl.String),
        registered=pl.col("potencial").cast(pl.Int64),
        voters=pl.col("sufragantesSegunE11").cast(pl.Int64),
        cast=pl.col("votosUrna").cast(pl.Int64),
        burned=pl.col("votosIncinerados").cast(pl.Int64),
        counted=pl.col("totalVotos").cast(pl.Int64),
    ).write_parquet(PARSED / str(_president_day(2026, round_number)) / "tables.parquet")


def parse_congress_2026() -> None:
    outer = _zip(FILES["congreso-2026"])
    basics = zipfile.ZipFile(io.BytesIO(outer.read(_member(outer, r"Basicos[^/]*\.zip$"))))
    places, labels = _basics(basics, CONGRESS_2026)
    inner = zipfile.ZipFile(outer.open(_member(outer, r"mmvESCRUTINIO[^/]*\.zip$")))
    with (
        _extracted(inner, _member(inner, r"MMV_9999\.csv$")) as csv,
        _extracted(outer, _member(outer, r"CNS_MMV_9999\.txt$")) as primaries,
        _extracted(outer, _member(outer, r"CTP_MMV_9999\.txt$")) as peace,
    ):
        _write(
            "2026-03-08",
            [
                _finish(_escrutinio(csv, CONGRESS_2026)),
                _finish(_preconteo(primaries, PRIMARY), "preliminary"),
                _finish(_preconteo(peace, PEACE), "preliminary"),
            ],
            [places],
            [labels],
        )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for round_number in (1, 2):
        parse_president_2018(round_number)
        parse_president_2022(round_number)
        parse_president_2026(round_number)
    parse_congress_2022()
    parse_congress_2026()
    for year in (2019, 2023):
        parse_local(year)
    build()


# Packages


def key(kind: str, *parts: object) -> str:
    return package.Package(SOURCE, SOURCE, "").id(kind, *parts)


COLOMBIA = str(package.uuid5(package.NAMESPACE, "polity\x1fCO"))
SOURCE_ID = key("source", SOURCE)
COUNTRY = key("area", "co.pais", "CO")
SENATE_BODY = key("body", "senate")
HOUSE_BODY = key("body", "house")
PRESIDENCY = key("office", "president")
MINOR = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "por", "a", "al", "o"}
LICENSE = (
    "Public information published by the Registraduría Nacional del Estado Civil under Colombia's transparency law "
    "(Ley 1712 de 2014); reuse requires citing the source"
)
METHOD = (
    "Counted per polling table (mesa) as published by the Registraduría Nacional del Estado Civil in its "
    "Observatorio's mesa-by-mesa files (archivos de votación mesa a mesa). Votes for a list alone (votos por la "
    "agrupación) are stored on a legend line under the list, and preference votes on candidate lines under it; a "
    "list without preference votes holds its votes itself. The blank vote (voto en blanco) is a valid ballot option "
    "in Colombia and is stored as an option. Null votes (votos nulos) are stored as invalid ballots and unmarked "
    "ballots (votos no marcados) as blank ballots. Rows with zero votes are left out."
)
PRELIMINARY_METHOD = (
    "Counted per polling table (mesa) from the Registraduría Nacional del Estado Civil's preconteo, the count the "
    "polling-table juries report on election night, as published in its Observatorio's mesa-by-mesa files. The "
    "Observatorio publishes the escrutinio (the official canvass) of this election day only for the Senate and the "
    "House, so this contest holds the preliminary count. Null votes are stored as invalid ballots and unmarked "
    "ballots as blank ballots. Rows with zero votes are left out."
)
TABLES_METHOD = (
    " Voters are the voters signed in on the table's E-11 roll and ballots cast are the ballots found in the box, from "
    "the Registraduría's file of polling-table reports (novedades). That file's potencial is the most voters a table "
    "may hold, not its roll, so it is not stored."
)


def title(text: str | None) -> str:
    words = _clean(text).lower().split(" ")
    return " ".join(
        word if index and word in MINOR else word.upper() if "." in word.rstrip(".") else word[:1].upper() + word[1:]
        for index, word in enumerate(words)
        if word
    )


def _days() -> list[Day]:
    return [d for d in DAYS if (PARSED / str(d.day) / "votes.parquet").exists()]


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(key for d in _days() for key in [d.key, *(f"{d.key}-{part}" for part in _parts(d))]),
    ]


def _votes(d: Day) -> pl.LazyFrame:
    return pl.scan_parquet(PARSED / str(d.day) / "votes.parquet")


def _places() -> pl.DataFrame:
    frames = []
    for d in _days():
        frame = pl.read_parquet(PARSED / str(d.day) / "places.parquet").with_columns(day=pl.lit(d.day))
        frames.append(frame)
    return pl.concat(frames, how="diagonal_relaxed")


def _best(frame: pl.DataFrame, keys: list[str], column: str) -> dict[tuple[str, ...], str]:
    ranked = (
        frame.filter(pl.col(column).is_not_null() & (pl.col(column).str.strip_chars() != ""))
        .with_columns(
            broken=pl.col(column).str.contains(r"[?\x{FFFD}]"),
            accents=pl.col(column).str.contains(r"[ÁÉÍÓÚÑáéíóúñ]"),
            length=pl.col(column).str.strip_chars().str.len_chars(),
        )
        .sort("broken", "length", "accents", "day", descending=[True, False, False, False])
        .unique(keys, keep="last")
    )
    return {tuple(row[:-1]): str(row[-1]) for row in ranked.select(*keys, column).iter_rows()}


def _comuna_name(code: str, text: str) -> str:
    text = _clean(text)
    return text[len(code) :].strip() if text.startswith(code) and len(text) > len(code) else text


DEPARTMENTS = {
    "01": "Antioquia", "03": "Atlántico", "05": "Bolívar", "07": "Boyacá", "09": "Caldas", "11": "Cauca",
    "12": "Cesar", "13": "Córdoba", "15": "Cundinamarca", "16": "Bogotá D.C.", "17": "Chocó", "19": "Huila",
    "21": "Magdalena", "23": "Nariño", "24": "Risaralda", "25": "Norte de Santander", "26": "Quindío",
    "27": "Santander", "28": "Sucre", "29": "Tolima", "31": "Valle del Cauca", "40": "Arauca", "44": "Caquetá",
    "46": "Casanare", "48": "La Guajira", "50": "Guainía", "52": "Meta", "54": "Guaviare",
    "56": "San Andrés, Providencia y Santa Catalina", "60": "Amazonas", "64": "Putumayo", "68": "Vaupés",
    "72": "Vichada", "88": "Consulados",
}  # fmt: skip
"""The Registraduría prints department names cut to 12 letters and without accents; these are their full names."""


def geo_names() -> tuple[
    dict[tuple[str, ...], str], dict[tuple[str, ...], str], dict[tuple[str, ...], str], dict[tuple[str, ...], str]
]:
    places = _places()
    deps = _best(places, ["dep"], "dep_name") | {(code,): text for code, text in DEPARTMENTS.items()}
    muns = _best(places, ["dep", "mun"], "mun_name")
    puestos = _best(places, ["dep", "mun", "zona", "puesto"], "puesto_name")
    comunas = _best(places.filter(pl.col("comuna").is_not_null()), ["dep", "mun", "comuna"], "comuna_name")
    return deps, muns, puestos, comunas


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def _contests() -> pl.DataFrame:
    return pl.concat(
        [
            _votes(d).select("corp", "circ", "district").unique().collect().with_columns(day=pl.lit(d.day))
            for d in _days()
        ]
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Colombia's departments, municipalities, and local-board districts (comunas and corregimientos) as the "
        "Registraduría Nacional del Estado Civil codes them, and the 16 special peace districts of the House",
    )
    p.external.add(COLOMBIA)
    deps, muns, _, comunas = geo_names()
    for code, rank, kind, noun in (
        ("co.pais", 0, "administrative", "country"),
        ("co.departamento", 1, "administrative", "department"),
        ("co.municipio", 2, "administrative", "municipality"),
        ("co.comuna", 3, "administrative", "comuna or corregimiento"),
        ("co.citrep", 1, "electoral_district", "special peace district"),
        ("co.puesto", 4, "precinct", "polling place"),
        ("co.mesa", 5, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=COLOMBIA, kind=kind, rank=rank, noun=noun, code_system="rnec")

    def place(scheme: str, code: str, text: str, parent: str | None) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        if parent is not None:
            p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "es", "official")

    place("co.pais", "CO", "Colombia", None)
    for (dep,), text in sorted(deps.items()):
        if dep != ABROAD:
            place("co.departamento", dep, title(text), COUNTRY)
    for (dep, mun), text in sorted(muns.items()):
        if dep != ABROAD:
            place("co.municipio", f"{dep}:{mun}", title(text), area("co.departamento", dep))
    boards = _contests().filter(pl.col("corp") == BOARD).select("district").unique()
    for (district,) in sorted(boards.iter_rows()):
        dep, mun, comuna = district.split(":")
        text = comunas.get((dep, mun, comuna))
        label = _comuna_name(comuna, text) if text else f"Comuna {comuna}"
        place("co.comuna", district, title(label), area("co.municipio", f"{dep}:{mun}"))
    peace = _contests().filter(pl.col("corp") == PEACE).select("district").unique()
    for (district,) in sorted(peace.iter_rows()):
        place("co.citrep", district, f"Circunscripción Transitoria Especial de Paz {int(district)}", COUNTRY)
    return p


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.upper().replace("?", "N").replace("Ñ", "N"))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text.encode("ascii", "ignore").decode()).split())


def _labels() -> pl.DataFrame:
    return pl.concat(
        [pl.read_parquet(PARSED / str(d.day) / "labels.parquet").with_columns(day=pl.lit(d.day)) for d in _days()]
    )


def party_names() -> dict[str, str]:
    """The latest clean name each party code carries, to name parties a file prints only by code."""
    labels = _labels().filter((pl.col("party") != "0") & pl.col("party_name").is_not_null())
    return {str(k[0]): v for k, v in _best(labels, ["party"], "party_name").items()}


def party(code: str, name: str) -> str:
    return key("party", code, _fold(name))


def _party_name(code: str, name: str | None, fallback: dict[str, str]) -> str | None:
    if code == "0":
        return None
    text = _clean(name)
    if not text or _fold(text) in ("N A", "CANDIDATOS TOTALES"):
        text = fallback.get(code, "")
    return text or None


SYSTEMS = {
    "president": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate who wins more than half of the valid votes, the blank vote included, is elected; otherwise the "
            "two leading candidates go to a second round three weeks later. If the blank vote wins a majority in the "
            "first round, the election is held again with new candidates."
        ),
    ),
    "plurality": (
        {"formula": "fptp"},
        (
            "The candidate with the most votes is elected. If the blank vote wins a majority of the valid votes, the "
            "election is held again with new candidates."
        ),
    ),
    "lists": (
        {"formula": "dhondt"},
        (
            "Seats are allocated by D'Hondt (cifra repartidora) among the lists that pass the threshold. A list may be "
            "closed, or open to a preference vote that orders its candidates; voters may mark only the list. If the "
            "blank vote wins a majority of the valid votes, the election is held again."
        ),
    ),
}


def system(name: str) -> str:
    return key("electoral_system", name)


def _body(corp: str, district: str) -> str | None:
    if corp == SENATE:
        return SENATE_BODY
    if corp in (HOUSE, PEACE):
        return HOUSE_BODY
    if corp in (ASSEMBLY, COUNCIL, BOARD):
        return key("body", corp, district)
    return None


def _office(corp: str, district: str) -> str | None:
    if corp in (PRESIDENT, PRIMARY):
        return PRESIDENCY
    if corp in (GOVERNOR, MAYOR):
        return key("office", corp, district)
    return None


def _district_name(corp: str, district: str, names: tuple[dict[tuple[str, ...], str], ...]) -> str:
    deps, muns, _, comunas = names
    if district == "CO":
        return "Colombia"
    if corp == PEACE:
        return f"Circunscripción Transitoria Especial de Paz {int(district)}"
    parts = tuple(district.split(":"))
    if len(parts) == 1:
        return "Colombianos en el exterior" if parts[0] == ABROAD else title(deps.get(parts, parts[0]))
    dep = title(deps.get(parts[:1], parts[0]))
    mun = title(muns.get(parts[:2], parts[1]))
    if len(parts) == 2:
        return f"{mun} ({dep})"
    text = comunas.get(parts)
    return f"{title(_comuna_name(parts[2], text)) if text else f'Comuna {parts[2]}'}, {mun} ({dep})"


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Colombia's parties and movements, the Congress, departmental assemblies, municipal councils, local boards, "
        "and executive offices as the Registraduría Nacional del Estado Civil names them, with their electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(COLOMBIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Registraduría Nacional del Estado Civil",
        title="Observatorio de la Registraduría: histórico de resultados electorales, votación mesa a mesa",
        url=CATALOG,
        license=LICENSE,
        license_url="https://www.registraduria.gov.co/Politicas-de-privacidad.html",
        attribution="Registraduría Nacional del Estado Civil, Observatorio en Democracia y Asuntos Electorales",
    )
    p.artifact(raw.cached(SOURCE, CATALOG), SOURCE_ID)
    for path in _paths():
        p.artifact(_artifact(path), SOURCE_ID)
    names = geo_names()
    p.add("body", id=SENATE_BODY, polity_id=COLOMBIA, kind="upper_house")
    p.name(SENATE_BODY, "Senate of the Republic", "en", "official")
    p.name(SENATE_BODY, "Senado de la República", "es", "native", False)
    p.add("body", id=HOUSE_BODY, polity_id=COLOMBIA, kind="lower_house")
    p.name(HOUSE_BODY, "House of Representatives", "en", "official")
    p.name(HOUSE_BODY, "Cámara de Representantes", "es", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=COLOMBIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Colombia", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    nouns = {
        ASSEMBLY: "Asamblea Departamental de {}",
        COUNCIL: "Concejo Municipal de {}",
        BOARD: "Junta Administradora Local de {}",
        GOVERNOR: "Gobernador de {}",
        MAYOR: "Alcalde de {}",
    }
    seen: set[tuple[str, str]] = set()
    for corp, district in _contests().select("corp", "district").unique().sort(pl.all()).iter_rows():
        if corp not in nouns or (corp, district) in seen:
            continue
        seen.add((corp, district))
        text = nouns[corp].format(_district_name(corp, district, names))
        if corp in (ASSEMBLY, COUNCIL, BOARD):
            id = _body(corp, district)
            p.add("body", id=id, polity_id=COLOMBIA, kind="council")
        else:
            id = _office(corp, district)
            p.add("office", id=id, polity_id=COLOMBIA, kind="governor" if corp == GOVERNOR else "mayor")
        p.name(str(id), text, "es", "official")
    for name, (rules, method) in SYSTEMS.items():
        p.add("electoral_system", id=system(name), polity_id=COLOMBIA, rules=rules, rules_version=1)
        p.add(
            "note", id=key("note", system(name)), entity_id=system(name), kind="methodology", body_md=method, lang="en"
        )
    fallback = party_names()
    seen_parties: set[str] = set()
    labels = _labels().sort("day")
    for code, text in labels.select("party", "party_name").unique(maintain_order=True).iter_rows():
        name = _party_name(code, text, fallback)
        if name is None:
            continue
        id = party(code, name)
        if id in seen_parties:
            continue
        seen_parties.add(id)
        p.add("party", id=id, kind="party", polity_id=COLOMBIA)
        p.add("external_id", entity_id=id, scheme="co.registraduria.party", value=f"{code}:{_fold(name)}")
        p.name(id, name, "es", "official")
    return p


def _paths() -> list[str]:
    paths = [FILES[name] for name in FILES]
    return [*paths, *congress_2022()]


def _primaries(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.with_columns(
        district=pl.when(pl.col("corp") == PRIMARY).then(pl.col("party")).otherwise(pl.col("district"))
    )


def _contest_name(
    corp: str,
    circ: str,
    district: str,
    names: tuple[dict[tuple[str, ...], str], ...],
    labels: dict[tuple[str, str, str], str],
) -> str:
    place = _district_name(corp, district, names)
    special = {
        INDIGENOUS: "Circunscripción Especial Indígena",
        AFRO: "Circunscripción Especial Afrodescendiente",
    }
    if corp == PRESIDENT:
        return "Presidente de la República"
    if corp == PRIMARY:
        return title(labels.get((corp, district, district), f"Consulta {district}"))
    if corp == SENATE:
        return f"Senado, {special.get(circ, 'Circunscripción Nacional')}"
    if corp == HOUSE:
        if circ in special:
            return f"Cámara, {special[circ]}"
        return "Cámara, Circunscripción Internacional" if district == ABROAD else f"Cámara, {place}"
    if corp == PEACE:
        return f"Cámara, {place}"
    return {
        GOVERNOR: "Gobernación de {}",
        ASSEMBLY: "Asamblea de {}",
        MAYOR: "Alcaldía de {}",
        COUNCIL: "Concejo de {}",
        BOARD: "Junta Administradora Local de {}",
    }[corp].format(place)


ELECTIONS = {
    "presidential": ("presidential", "{year} Colombian presidential election", "Elecciones presidenciales de {year}"),
    "congress": ("congress", "{year} Colombian parliamentary election", "Elecciones legislativas de {year}"),
    "primary": ("primaries", "{year} Colombian inter-party consultations", "Consultas interpartidistas de {year}"),
    "local": ("local", "{year} Colombian regional and local elections", "Elecciones territoriales de {year}"),
}


def _family(d: Day, corp: str) -> str:
    return "primary" if corp == PRIMARY else d.family


def _known_areas() -> set[str]:
    path = package.folder(f"{SOURCE}/geo") / "build" / "area.parquet"
    return set(pl.read_parquet(path)["id"]) if path.exists() else set()


def _known_parties() -> set[str]:
    path = package.folder(f"{SOURCE}/registry") / "build" / "party.parquet"
    return set(pl.read_parquet(path)["id"]) if path.exists() else set()


def _parts(d: Day) -> list[str]:
    if d.family != "local":
        return []
    return sorted(_votes(d).select("dep").unique().collect()["dep"])


def election_day(d: Day, part: str | None = None) -> Package:
    """One package per election day; a local election day is a base package with the election and one per department.

    The package check joins every fact's result set and option, and DuckDB plans that join as a cross product of
    result sets and options, which a day of some 3,000 local contests and 140,000 options cannot hold.
    """
    day = d.day
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    split = d.family == "local"
    if part is not None:
        depends.append(d.key)
    first: Day | None = None
    if d.round_number == 2:
        first = next(o for o in DAYS if o.family == d.family and o.day.year == day.year and o.round_number == 1)
        depends.append(first.key)
    summary = f"Colombia's elections of {day:%-d %B %Y} by polling table from the Registraduría"
    if part is not None:
        summary = f"{summary}, {DEPARTMENTS.get(part, part)}"
    p = Package(d.key if part is None else f"{d.key}-{part}", SOURCE, summary, depends)
    p.external.add(COLOMBIA)
    folder = PARSED / str(day)
    for path in _day_paths(d):
        p.uses(_artifact(path))
    names = geo_names()
    known_areas, known_parties = _known_areas(), _known_parties()
    fallback = party_names()
    votes = _primaries(_votes(d))
    if part is not None:
        votes = votes.filter(pl.col("dep") == part)
    elif split:
        votes = votes.filter(pl.lit(False))
        word, english, spanish = ELECTIONS[d.family]
        election = key("election", day, d.family)
        p.add("election", id=election, polity_id=COLOMBIA, kind="local", date_from=day, date_to=day, status="held")
        p.name(election, english.format(year=day.year), "en", "official")
        p.name(election, spanish.format(year=day.year), "es", "native", False)
        p.add("slug", path=f"co/{day}-{word}", entity_id=election, is_current=True)
        return p
    label_frame = _primaries(pl.scan_parquet(folder / "labels.parquet")).collect()
    labels = {
        (c, dist, pa): n
        for c, dist, pa, n in label_frame.filter(pl.col("cand") == "0")
        .select("corp", "district", "party", "party_name")
        .iter_rows()
        if n
    }
    cand_names = {
        (c, ci, dist, pa, ca): n
        for c, ci, dist, pa, ca, n in label_frame.select(
            "corp", "circ", "district", "party", "cand", "cand_name"
        ).iter_rows()
        if n
    }
    party_labels = {
        (c, ci, dist, pa): n
        for c, ci, dist, pa, n in label_frame.select("corp", "circ", "district", "party", "party_name").iter_rows()
        if n
    }
    dropped = (
        votes.filter((pl.col("corp") == PRIMARY) & (pl.col("district") == "0"))
        .select(pl.col("votes").sum())
        .collect()
        .item()
    )
    votes = votes.filter(~((pl.col("corp") == PRIMARY) & (pl.col("district") == "0")))
    options = votes.group_by("corp", "circ", "district", "stage", "party", "cand").agg(pl.col("votes").sum()).collect()
    registered = label_frame.filter(~pl.col("cand").is_in([BLANK, NULL, UNMARKED]))
    elections: dict[str, str] = {}
    contests = options.select("corp", "circ", "district", "stage").unique().sort("corp", "circ", "district")
    paths: set[str] = set()
    mapping: list[dict[str, object]] = []
    sets: dict[str, str] = {}
    noted: list[str] = []
    for corp, circ, district, stage in contests.iter_rows():
        family = _family(d, corp)
        if family not in elections and part is not None:
            elections[family] = key("election", day, family)
        if family not in elections:
            word, english, spanish = ELECTIONS[family]
            election = elections[family] = key("election", day, family)
            runoff = d.round_number == 2
            p.add(
                "election",
                id=election,
                polity_id=COLOMBIA,
                kind={"local": "local", "primary": "primary"}.get(family, "runoff" if runoff else "general"),
                date_from=day,
                date_to=day,
                status="held",
            )
            suffix = (", second round", ", segunda vuelta") if runoff else ("", "")
            p.name(election, english.format(year=day.year) + suffix[0], "en", "official")
            p.name(election, spanish.format(year=day.year) + suffix[1], "es", "native", False)
            p.add("slug", path=f"co/{day}-{word}" + ("-runoff" if runoff else ""), entity_id=election, is_current=True)
        election = elections[family]
        word = ELECTIONS[family][0]
        base = f"co/{day}-{word}" + ("-runoff" if d.round_number == 2 else "")
        ck = f"{corp}:{circ}:{district}"
        contest = key("contest", day, ck)
        single = corp in SINGLE
        text = _contest_name(corp, circ, district, names, labels)
        if corp in (PRESIDENT, PRIMARY):
            place = COUNTRY
        elif corp == PEACE:
            place = area("co.citrep", district)
        elif corp == BOARD:
            place = area("co.comuna", district)
        elif district == "CO" or district == ABROAD:
            place = COUNTRY
        elif ":" in district:
            place = area("co.municipio", district)
        else:
            place = area("co.departamento", district)
        if place not in known_areas:
            place = COUNTRY
        previous = key("contest", first.day, ck) if first is not None else None
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office" if single else "list_allocation",
            office_id=_office(corp, district),
            body_id=_body(corp, district),
            district_area_id=place,
            magnitude=1 if single or corp == PEACE else None,
            round_number=d.round_number,
            previous_round_id=previous,
            electoral_system_id=system("president" if corp == PRESIDENT else "plurality" if single else "lists"),
            default_result_kind="counted",
        )
        p.name(contest, text, "es", "official")
        slug = changeset.slug(text) or district
        path = f"{base}/{slug}"
        if path in paths:
            path = f"{base}/{slug}-{changeset.slug(district)}"
        paths.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = sets[ck] = key("result_set", day, ck)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage=stage,
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = PRELIMINARY_METHOD if stage == "preliminary" else METHOD
        if (folder / "tables.parquet").exists():
            method += TABLES_METHOD
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        if corp == PRIMARY and dropped:
            noted.append(result_set)
        here = options.filter((pl.col("corp") == corp) & (pl.col("circ") == circ) & (pl.col("district") == district))
        listed = registered.filter(
            (pl.col("corp") == corp) & (pl.col("circ") == circ) & (pl.col("district") == district)
        )
        pairs = sorted(
            {(pa, ca) for pa, ca in here.select("party", "cand").iter_rows()}
            | {(pa, ca) for pa, ca in listed.select("party", "cand").iter_rows()},
            key=lambda pair: (int(pair[0]), int(pair[1])),
        )
        open_lists = {pa for pa, ca in pairs if ca != "0" and ca not in (BLANK, NULL, UNMARKED)}
        lists: dict[str, str] = {}
        for pa, ca in pairs:
            if ca == BLANK:
                option = key("option", day, ck, "blank")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="none_of_the_above",
                    ballot_label="Voto en blanco",
                )
                mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
                continue
            if ca in (NULL, UNMARKED):
                mapping.append(
                    {"ck": ck, "party": pa, "cand": ca, "option_id": None, "measure": 5 if ca == NULL else 6}
                )
                continue
            group = _party_name(
                pa, party_labels.get((corp, circ, district, pa)) or labels.get((corp, district, pa)), fallback
            )
            party_id = party(pa, group) if group is not None and party(pa, group) in known_parties else None
            if single:
                option = key("option", day, ck, pa, ca)
                label = title(cand_names.get((corp, circ, district, pa, ca)) or group or f"Candidato {ca}")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if corp == PRIMARY else party_id,
                    ballot_position=int(ca) or None,
                    ballot_label=label,
                )
                _person(p, day, ck, pa, ca, option, label, "head", None)
                mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
                continue
            if pa not in lists:
                lists[pa] = key("option", day, ck, pa, "list")
                p.add(
                    "contest_option",
                    id=lists[pa],
                    contest_id=contest,
                    kind="list",
                    party_id=party_id,
                    ballot_label=group or f"Lista {pa}",
                )
            if pa not in open_lists:
                mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": lists[pa], "measure": 1})
                continue
            option = key("option", day, ck, pa, ca)
            if ca == "0":
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="legend",
                    parent_option_id=lists[pa],
                    party_id=party_id,
                    ballot_label=f"{group or f'Lista {pa}'} (solo por la lista)",
                )
            else:
                label = title(cand_names.get((corp, circ, district, pa, ca)) or f"Candidato {ca}")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=lists[pa],
                    ballot_position=int(ca),
                    ballot_label=label,
                )
                _person(p, day, ck, pa, ca, lists[pa], label, "list_member", int(ca), member=party_id)
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
    for result_set in noted:
        p.add(
            "note",
            id=key("note", result_set, "shared"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The Registraduría reports the null and unmarked ballots of this day's consultations together, "
            f"not per consultation, so they are not stored here ({dropped:,} ballots in all).",
            lang="en",
        )
    units = _units(p, d, names, known_areas, part)
    values = (
        votes.with_columns(ck=pl.concat_str("corp", "circ", "district", separator=":"), code=_mesa_code(day))
        .join(
            pl.DataFrame(
                mapping,
                schema={
                    "ck": pl.String,
                    "party": pl.String,
                    "cand": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                },
            ).lazy(),
            on=["ck", "party", "cand"],
            how="left",
        )
        .join(pl.DataFrame({"ck": list(sets), "result_set_id": list(sets.values())}).lazy(), on="ck")
        .join(units.lazy(), on="code")
        .group_by("result_set_id", "unit_id", "option_id", "measure")
        .agg(pl.col("votes").sum(), pl.len().alias("rows"))
        .collect()
    )
    total = votes.select(pl.len()).collect().item()
    if values["rows"].sum() != total or values["measure"].null_count():
        raise ValueError(f"{d.key}: {total:,} rows, {values['rows'].sum():,} mapped")
    repeated = values.filter(pl.col("rows") > 1).height
    if repeated:
        print(f"{d.key}: {repeated:,} facts sum repeated source rows", flush=True)
    p.frame(
        "result_value",
        values.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="votes",
        ),
    )
    tables = folder / "tables.parquet"
    if tables.exists():
        _tables(p, d, tables, units, sets)
    return p


def _mesa_code(day: date) -> pl.Expr:
    return pl.concat_str(pl.lit(str(day)), "dep", "mun", "zona", "puesto", "mesa", separator=":")


def _person(
    p: Package,
    day: date,
    ck: str,
    pa: str,
    ca: str,
    option: str,
    label: str,
    role: str,
    position: int | None,
    member: str | None = None,
) -> None:
    tag = f"{day}:{ck}:{pa}:{ca}"
    person = key("person", tag)
    p.add("person", id=person)
    p.name(person, label, "es", "official")
    p.add(
        "candidacy",
        id=key("candidacy", tag),
        person_id=person,
        option_id=option,
        role=role,
        list_position=position,
        ballot_name=label,
        member_of_party_id=member,
    )


def _units(
    p: Package, d: Day, names: tuple[dict[tuple[str, ...], str], ...], known: set[str], part: str | None
) -> pl.DataFrame:
    folder = PARSED / str(d.day)
    columns = ["dep", "mun", "zona", "puesto", "mesa"]
    mesas = _votes(d).filter(pl.lit(True) if part is None else pl.col("dep") == part).select(columns).unique().collect()
    if (folder / "tables.parquet").exists():
        mesas = pl.concat([mesas, pl.read_parquet(folder / "tables.parquet").select(columns)]).unique()
    mesas = mesas.sort(columns)
    puestos = names[2]
    rows: list[dict[str, object]] = []
    places: set[tuple[str, str, str, str]] = set()
    codes, ids = [], []
    for dep, mun, zona, puesto, mesa in mesas.iter_rows():
        town = area("co.municipio", f"{dep}:{mun}")
        if dep == ABROAD:
            relation, place, kind = "unlocated", None, "abroad_mission"
        elif town in known:
            relation, place, kind = "within", town, "standard"
        elif area("co.departamento", dep) in known:
            relation, place, kind = "within", area("co.departamento", dep), "standard"
        else:
            relation, place, kind = "within", COUNTRY, "standard"
        parent_code = f"{d.day}:{dep}:{mun}:{zona}:{puesto}"
        parent = key("unit", "co.puesto", parent_code)
        if (dep, mun, zona, puesto) not in places:
            places.add((dep, mun, zona, puesto))
            rows.append(
                {
                    "id": parent,
                    "polity_id": COLOMBIA,
                    "scheme_code": "co.puesto",
                    "code": parent_code,
                    "area_relation": relation,
                    "area_id": place,
                    "kind": kind,
                }
            )
            text = puestos.get((dep, mun, zona, puesto))
            if text:
                p.name(parent, title(text), "es", "official")
        code = f"{parent_code}:{mesa}"
        unit = key("unit", "co.mesa", code)
        codes.append(code)
        ids.append(unit)
        rows.append(
            {
                "id": unit,
                "polity_id": COLOMBIA,
                "scheme_code": "co.mesa",
                "code": code,
                "parent_id": parent,
                "area_relation": relation,
                "area_id": place,
                "kind": kind,
            }
        )
    p.frame("reporting_unit", pl.DataFrame(rows, infer_schema_length=None))
    return pl.DataFrame({"code": codes, "unit_id": ids})


def _tables(p: Package, d: Day, path: Path, units: pl.DataFrame, sets: dict[str, str]) -> None:
    (result_set,) = sets.values()
    frame = (
        pl.read_parquet(path)
        .with_columns(code=_mesa_code(d.day))
        .join(units, on="code")
        .unpivot(on=["voters", "cast"], index="unit_id", variable_name="what", value_name="value")
        .filter(pl.col("value").is_not_null())
    )
    p.frame(
        "result_value",
        frame.select(
            result_set_id=pl.lit(result_set),
            unit_id="unit_id",
            option_id=pl.lit(None, pl.String),
            channel=pl.lit(0),
            measure=pl.col("what").replace_strict({"voters": 14, "cast": 3}, return_dtype=pl.Int16),
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )


def _day_paths(d: Day) -> list[str]:
    if d.day == date(2022, 3, 13):
        return congress_2022()
    if d.family == "local":
        return [FILES[f"territoriales-{d.day.year}"]]
    if d.family == "congress":
        return [FILES[f"congreso-{d.day.year}"]]
    return [FILES[f"presidente-{d.day.year}-{d.round_number}"]]


def build() -> None:
    geo().write()
    registry().write()
    for d in _days():
        election_day(d).write()
        for part in _parts(d):
            election_day(d, part).write()


def emit() -> None:
    package.run("load", packages())
