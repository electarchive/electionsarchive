"""Colombia's presidential, congressional, and Andean Parliament elections of 1998 to 2014 from the statistics CDs.

The Observatorio of the Registraduría Nacional del Estado Civil republishes the statistics CDs it issued after each
national election year, each a ZIP around Windows installers. The 1997-1998 CD holds FoxPro (dBASE) tables as plain
files and gives results by municipality; the 2002 CD packs a Microsoft Access database, also by municipality; the 2006
CD packs FoxPro tables by polling table (mesa) in an InstallShield cabinet; the 2010 CD packs one Access database per
department, by polling table, in an MSI package; and the 2014 CD packs SQLite databases, by polling table, in an Inno
Setup installer. This module unpacks the installers with 7-Zip, msiextract, and innoextract, reads the tables with
mdbtools where they are Access, and builds one package per election day on top of the geography and registry of
`co.registraduria`.
"""

import contextlib
import csv
import io
import re
import shutil
import sqlite3
import subprocess
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.co import registraduria as rnec
from ea.sources.co.registraduria_history import dbf
from ea.store import DATA

SOURCE = "co.registraduria_national"
RAW = rnec.SOURCE
PARSED = DATA / "parsed" / SOURCE
SCRATCH = DATA / "tmp" / SOURCE
YEARS = (1998, 2002, 2006, 2010, 2014)
PRESIDENT, SENATE, HOUSE, ANDEAN = "presidente", "senado", "camara", "parlamento_andino"
NATIONAL, TERRITORIAL, INDIGENOUS, AFRO, ABROAD = "nacional", "territorial", "indigena", "afro", "exterior"
BLANK, NULL, UNMARKED, CAST = "996", "997", "998", "999"
SPECIALS = (BLANK, NULL, UNMARKED, CAST)
ABROAD_DEP = "88"


@dataclass(frozen=True)
class Day:
    day: date
    family: str
    round_number: int = 1

    @property
    def year(self) -> int:
        return 1998 if self.day.year == 1998 else self.day.year

    @property
    def cd(self) -> int:
        return 1997 if self.day.year == 1998 else self.day.year

    @property
    def key(self) -> str:
        return f"{SOURCE}/{self.day}"


DAYS = [
    Day(date(1998, 3, 8), "congress"),
    Day(date(1998, 5, 31), "presidential"),
    Day(date(1998, 6, 21), "presidential", 2),
    Day(date(2002, 3, 10), "congress"),
    Day(date(2002, 5, 26), "presidential"),
    Day(date(2006, 3, 12), "congress"),
    Day(date(2006, 5, 28), "presidential"),
    Day(date(2010, 3, 14), "congress"),
    Day(date(2010, 5, 30), "presidential"),
    Day(date(2010, 6, 20), "presidential", 2),
    Day(date(2014, 3, 9), "congress"),
    Day(date(2014, 5, 25), "presidential"),
    Day(date(2014, 6, 15), "presidential", 2),
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
    "votes": pl.Int64,
}
LABELS = {
    "corp": pl.String,
    "circ": pl.String,
    "district": pl.String,
    "party": pl.String,
    "cand": pl.String,
    "party_name": pl.String,
    "cand_name": pl.String,
    "mate": pl.String,
    "position": pl.Int32,
    "elected": pl.Boolean,
}
PLACES = {"dep": pl.String, "mun": pl.String, "zona": pl.String, "puesto": pl.String, "name": pl.String}


def url(cd: int) -> str:
    return rnec.url(rnec.HISTORIC[cd])


def _zip_check(path: Path) -> None:
    with path.open("rb") as file:
        if file.read(2) != b"PK":
            raise ValueError(f"{path}: not a zip archive")


def fetch() -> None:
    """Download each CD; the Observatorio's server often drops long transfers, so each is retried."""
    for cd in sorted({d.cd for d in DAYS}):
        for attempt in range(8):
            try:
                artifact = raw.fetch(RAW, url(cd), suffix=".zip", check=_zip_check)
            except httpx.HTTPError as error:
                print(f"{cd}: attempt {attempt + 1}: {error!r}", flush=True)
                time.sleep(15 * (attempt + 1))
                continue
            print(f"{cd}: {artifact.bytes:,} bytes", flush=True)
            break
        else:
            raise SystemExit(f"{cd}: the download kept failing")


def _artifact(cd: int) -> raw.Artifact:
    return raw.cached(RAW, url(cd))


# Unpacking

SKIP = (".avi", ".pdf", ".tif", ".lnk", ".cdx")


def _unpack(cd: int) -> Path:
    """Unpack a CD into the scratch folder: the ZIP, then the installer around its data."""
    out = SCRATCH / str(cd)
    done = out / ".done"
    if done.exists():
        return out
    shutil.rmtree(out, ignore_errors=True)
    (out / "zip").mkdir(parents=True)
    with zipfile.ZipFile(_artifact(cd).path) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.flag_bits & 0x800 == 0:
                name = name.encode("cp437").decode("cp850")
            if info.is_dir() or name.lower().endswith(SKIP) or re.search(r"DotNet|Crystal|jre-|Adobe", name):
                continue
            target = out / "zip" / Path(name).name
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, 1 << 24)
    files = {p.name.lower(): p for p in (out / "zip").iterdir()}
    if cd == 2002:
        _run(["7z", "x", "-y", f"-o{out / 'data'}", str(files["estad2002.cab"])])
    elif cd == 2006:
        _run(["7z", "x", "-y", f"-o{out / 'data'}", str(files["data1.cab"])])
    elif cd == 2010:
        _run(["msiextract", "-C", str(out / "data"), str(files["convotoest.msi"])])
    elif cd == 2014:
        installer = next(p for name, p in files.items() if name.startswith("setupestadisticas"))
        _run(["innoextract", "-q", "-I", "app/db", "-d", str(out / "data"), str(installer)])
    else:
        (out / "data").mkdir()
        for name, path in files.items():
            if name.endswith(".dbf"):
                path.rename(out / "data" / name)
    shutil.rmtree(out / "zip")
    done.touch()
    return out


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def _find(root: Path, name: str) -> Path:
    found = sorted(p for p in root.rglob("*") if p.is_file() and p.name.lower() == name.lower())
    if not found:
        raise LookupError(f"{name} not found under {root}")
    return found[0]


def _mdb(path: Path, table: str) -> pl.DataFrame:
    """One table of an Access database as strings, through mdbtools."""
    out = subprocess.run(["mdb-export", str(path), table], check=True, capture_output=True).stdout
    rows = list(csv.reader(io.StringIO(out.decode("utf-8", "replace"))))
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows[1:], schema=dict.fromkeys(rows[0], pl.String), orient="row")


# Normalized tables


def _int(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strip_chars().replace("", None).cast(pl.Int64, strict=False)


def _code(column: str, width: int) -> pl.Expr:
    return _int(column).cast(pl.String).str.zfill(width)


def _num(column: str) -> pl.Expr:
    return _int(column).cast(pl.String)


def _clean(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.replace_all(r"\s+", " ").str.strip_chars(" .")


def _folder(d: Day) -> Path:
    return PARSED / str(d.day)


def _write(d: Day, votes: pl.DataFrame, labels: pl.DataFrame, places: pl.DataFrame) -> None:
    folder = _folder(d)
    folder.mkdir(parents=True, exist_ok=True)
    for column in VOTES:
        if column not in votes.columns:
            votes = votes.with_columns(pl.lit(None, pl.String).alias(column))
    votes = votes.select([pl.col(c).cast(t) for c, t in VOTES.items()]).filter(pl.col("votes") > 0)
    table = pl.col("mesa").is_not_null()
    votes = votes.with_columns(
        mun=pl.col("mun").fill_null("000"),
        zona=pl.when(table).then(pl.col("zona").fill_null("00")).otherwise(pl.col("zona")),
        puesto=pl.when(table).then(pl.col("puesto").fill_null("00")).otherwise(pl.col("puesto")),
    )
    votes = votes.group_by([c for c in VOTES if c != "votes"]).agg(pl.col("votes").sum())
    votes.sort([c for c in VOTES if c != "votes"]).write_parquet(folder / "votes.parquet")
    for column in LABELS:
        if column not in labels.columns:
            labels = labels.with_columns(pl.lit(None).alias(column))
    labels = labels.select([pl.col(c).cast(t) for c, t in LABELS.items()]).with_columns(
        [pl.col(c).map_elements(_repair, pl.String) for c in ("party_name", "cand_name", "mate")]
    )
    labels = labels.sort("elected", nulls_last=True, descending=True).unique(
        ["corp", "circ", "district", "party", "cand"], keep="first"
    )
    labels.sort("corp", "circ", "district", "party", "cand").write_parquet(folder / "labels.parquet")
    places = places.select([pl.col(c).cast(t) for c, t in PLACES.items()]).filter(pl.col("name").is_not_null())
    places = places.with_columns(pl.col("name").map_elements(_repair, pl.String))
    places.unique(["dep", "mun", "zona", "puesto"]).write_parquet(folder / "places.parquet")
    summary = votes.group_by("corp", "circ").agg(pl.col("votes").sum(), pl.len()).sort("corp", "circ")
    print(f"{d.day}: {votes.height:,} rows; {summary.rows()}", flush=True)


def _repair(text: str) -> str:
    """Undo the CDs' broken encodings: UTF-8 read as Windows-1252 (2014) and Ñ printed as a question mark (2006).

    The 2014 CD lost the second byte of Á and Í alike (Ã¿), so those take Í before G or A (INDÍGENA, AMAZONÍA) and
    Á otherwise (DEMOCRÁTICO).
    """
    text = re.sub(r"Ã¿(?=[GA])", "Í", text)
    for broken, fixed in (("Ã¿", "Á"), ("Ã“", "Ó"), ("Ã\x1c", "Ó"), ("Ã‘", "Ñ"), ("Ã‰", "É"), ("Ã0", "É")):
        text = text.replace(broken, fixed)
    if "Ã" in text:
        with contextlib.suppress(UnicodeError):
            text = text.encode("cp1252").decode("utf-8")
    text = re.sub(r"[\x00-\x1f]", "", text)
    return re.sub(r"(?<=[A-ZÁÉÍÓÚ])\?(?=[A-ZÁÉÍÓÚ])", "Ñ", text).strip()


def _specials(frame: pl.DataFrame, codes: dict[str, str]) -> pl.DataFrame:
    """Recode a source's own codes for blank, null, unmarked, and total ballots to 996 to 999 with party 0."""
    cand = pl.col("cand").replace(codes)
    return frame.with_columns(cand=cand).with_columns(
        party=pl.when(pl.col("cand").is_in(SPECIALS)).then(pl.lit("0")).otherwise(pl.col("party"))
    )


# 1998: dBASE tables by municipality


def _dbf(cd: int, name: str) -> pl.DataFrame:
    return dbf(_find(_unpack(cd) / "data", name))


def parse_1998() -> None:
    def town(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.with_columns(dep=_code("CODIDEPA", 2), mun=(_int("CODIMUNI") % 1000).cast(pl.String).str.zfill(3))

    parties = dict(_dbf(1997, "PARTIDO.DBF").select(_num("CODIPART"), _clean("NOMBPART")).iter_rows())
    towns = town(_dbf(1997, "MUNICIPI.DBF")).select(
        "dep", "mun", zona=pl.lit(None, pl.String), puesto=pl.lit(None, pl.String), name=_clean("NOMBMUNI")
    )
    president = town(_dbf(1997, "resupres.dbf"))
    heads = _dbf(1997, "PRESIDEN.DBF")
    for d, column in ((DAYS[1], "VOTOPRIM"), (DAYS[2], "VOTOSEGU")):
        votes = president.select(
            "dep",
            "mun",
            corp=pl.lit(PRESIDENT),
            circ=pl.lit(NATIONAL),
            district=pl.lit("CO"),
            party=pl.lit(None, pl.String),
            cand=_num("CODICAND"),
            votes=_int(column),
        )
        codes = {"96": BLANK, "97": NULL, "98": UNMARKED, "99": CAST}
        votes = _specials(votes, codes).with_columns(party=pl.col("party").fill_null(pl.col("cand")))
        labels = heads.filter(_int("CODICAND") < 90).select(
            corp=pl.lit(PRESIDENT),
            circ=pl.lit(NATIONAL),
            district=pl.lit("CO"),
            party=_num("CODICAND"),
            cand=_num("CODICAND"),
            party_name=_num("CODIPART").replace_strict(parties, default=None),
            cand_name=_name_last_first("NOMBPRES"),
            mate=_name_last_first("NOMBVICE"),
            elected=(pl.col("ELEGIDO") == "T") & pl.lit(d.round_number == 2),
        )
        _write(d, votes, labels, towns)
    votes, labels = [], []
    for corp, table, lists, column, codes in (
        (SENATE, "resusena.dbf", "LISTASEN.DBF", "VOTOSENA", {"897": BLANK, "898": NULL, "899": UNMARKED, "900": CAST}),
        (HOUSE, "RESUCAMA.DBF", "LISTACAM.DBF", "VOTOCAMA", {"297": BLANK, "298": NULL, "299": UNMARKED, "300": CAST}),
    ):
        frame = town(_dbf(1997, table)).select(
            "dep", "mun", corp=pl.lit(corp), party=_num("CODILIST"), cand=_num("CODILIST"), votes=_int(column)
        )
        frame = _specials(frame, codes).with_columns(
            cand=pl.when(pl.col("cand").is_in(SPECIALS)).then(pl.col("cand")).otherwise(pl.lit("0"))
        )
        district = pl.lit("CO") if corp == SENATE else pl.col("dep")
        votes.append(frame.with_columns(circ=pl.lit(NATIONAL if corp == SENATE else TERRITORIAL), district=district))
        members = _dbf(1997, lists).filter(_int("CODILIST") < 800 if corp == SENATE else _int("CODILIST") < 297)
        members = members.with_columns(dep=_code("CODIDEPA", 2))
        rows = members.select(
            corp=pl.lit(corp),
            circ=pl.lit(NATIONAL if corp == SENATE else TERRITORIAL),
            district=pl.lit("CO") if corp == SENATE else pl.col("dep"),
            party=_num("CODILIST"),
            cand=_num("SECULIST"),
            party_name=_num("CODIPART").replace_strict(parties, default=None),
            cand_name=_name_last_first("NOMBSENA" if corp == SENATE else "NOMBCAMA"),
            position=_int("SECULIST").cast(pl.Int32),
            elected=pl.col("ELEGIDO") == "T",
        )
        labels.append(rows)
        labels.append(
            rows.sort("position")
            .unique(["district", "party"], keep="first")
            .with_columns(cand=pl.lit("0"), cand_name=None, position=None, elected=pl.lit(False))
        )
    _write(DAYS[0], pl.concat(votes, how="diagonal_relaxed"), pl.concat(labels, how="diagonal_relaxed"), towns)


def _name_last_first(column: str) -> pl.Expr:
    """The 1998 CD prints names surname first (BETANCUR AGUILAR JORGE HERNAN); keep them as printed."""
    return _clean(column)


# 2002: an Access database by municipality


def _access_2002(table: str) -> pl.DataFrame:
    return _mdb(_find(_unpack(2002) / "data", "Estadistico2002.mdb"), table)


CIRCS_2002 = {"0": NATIONAL, "1": TERRITORIAL, "3": INDIGENOUS, "4": AFRO, "5": ABROAD}


def parse_2002() -> None:
    def town(frame: pl.DataFrame, dep: str = "cod_dpto") -> pl.DataFrame:
        return frame.with_columns(dep=_code(dep, 2), mun=_code("cod_mpio", 3))

    codes = {"996": BLANK, "997": NULL, "998": UNMARKED, "999": CAST}
    names = []
    for table, column in (("votos_presidente", "nom_mpio"), ("votos_senado", "nom_mpio"), ("votos_camara", "nom_mpio")):
        frame = town(_access_2002(table))
        names.append(frame.select("dep", "mun", name=_clean(column)).unique(["dep", "mun"]))
    towns = (
        pl.concat(names)
        .unique(["dep", "mun"])
        .with_columns(zona=pl.lit(None, pl.String), puesto=pl.lit(None, pl.String))
    )
    frame = town(_access_2002("votos_presidente"))
    votes = _specials(
        frame.select(
            "dep",
            "mun",
            corp=pl.lit(PRESIDENT),
            circ=pl.lit(NATIONAL),
            district=pl.lit("CO"),
            party=_num("cod_candidato"),
            cand=_num("cod_candidato"),
            votes=_int("votos"),
        ),
        codes,
    )
    tickets = _access_2002("listas_presidente")
    heads = tickets.filter(_int("renglon") == 0)
    mates = tickets.filter(_int("renglon") == 1).select(code=_num("codigo"), mate=_full("nombres", "apellidos"))
    won = _access_2002("elegidos_presidente").select(code=_num("codigo"), elected=pl.col("elegido") == "S")
    labels = (
        heads.select(code=_num("codigo"), party_name=_clean("movimiento"), cand_name=_full("nombres", "apellidos"))
        .join(mates, on="code", how="left")
        .join(won, on="code", how="left")
        .select(
            "party_name",
            "cand_name",
            "mate",
            "elected",
            corp=pl.lit(PRESIDENT),
            circ=pl.lit(NATIONAL),
            district=pl.lit("CO"),
            party="code",
            cand="code",
        )
    )
    _write(DAYS[4], votes, labels, towns)
    votes, labels = [], []
    senate = town(_access_2002("votos_senado"))
    votes.append(
        _specials(
            senate.select(
                "dep",
                "mun",
                corp=pl.lit(SENATE),
                circ=pl.col("circunscripcion").replace_strict(CIRCS_2002),
                district=pl.lit("CO"),
                party=_num("cod_candidato"),
                cand=pl.lit("0"),
                code=_num("cod_candidato"),
                votes=_int("votos"),
            ).with_columns(cand=pl.when(pl.col("code").is_in(SPECIALS)).then("code").otherwise("cand")),
            {},
        ).drop("code")
    )
    house_lists = _access_2002("listas_camara").with_columns(dep=_code("cod_dpto", 2), code=_num("codigo"))
    house_circ = house_lists.select("dep", "code", circ=pl.col("circunscripcion").replace_strict(CIRCS_2002)).unique()
    house = town(_access_2002("votos_camara"), "cod_dpto").with_columns(code=_num("cod_candidato"))
    house = house.join(house_circ, on=["dep", "code"], how="left").with_columns(
        circ=pl.when(pl.col("code").is_in(SPECIALS)).then(pl.lit(TERRITORIAL)).otherwise(pl.col("circ"))
    )
    if house["circ"].null_count():
        raise ValueError(f"2002 House: {house['circ'].null_count()} rows of unknown lists")
    votes.append(
        house.select(
            "dep",
            "mun",
            "circ",
            corp=pl.lit(HOUSE),
            district=pl.when(pl.col("circ") == TERRITORIAL).then(pl.col("dep")).otherwise(pl.lit("CO")),
            party=pl.when(pl.col("code").is_in(SPECIALS)).then(pl.lit("0")).otherwise(pl.col("code")),
            cand=pl.when(pl.col("code").is_in(SPECIALS)).then(pl.col("code")).otherwise(pl.lit("0")),
            votes=_int("votos"),
        )
    )
    for corp, lists, elected in (
        (SENATE, "listas_senado", "elegidos_senado"),
        (HOUSE, "listas_camara", "elegidos_camara"),
    ):
        frame = _access_2002(lists)
        dep = _code("cod_dpto", 2) if corp == HOUSE else pl.lit("00")
        circ = pl.col("circunscripcion").replace_strict(CIRCS_2002)
        party_code = "codpartido" if corp == SENATE else "cod_partido"
        rows = frame.select(
            corp=pl.lit(corp),
            circ=circ,
            dep=dep,
            party=_num("codigo"),
            cand=(_int("renglon") + 1).cast(pl.String),
            party_code=_num(party_code),
            party_name=_clean("movimiento"),
            cand_name=_full("nombres", "apellidos"),
            position=(_int("renglon") + 1).cast(pl.Int32),
        )
        # The winners of the special House lists carry department 300, 400, or 500 instead of a real department.
        won = _access_2002(elected).select(
            district=pl.when(_int("cod_dpto") < 100).then(_code("cod_dpto", 2)).otherwise(pl.lit("CO"))
            if corp == HOUSE
            else pl.lit("CO"),
            party=_num("codigo"),
            cand=(_int("renglon") + 1).cast(pl.String),
            elected=pl.col("elegido") == "S",
        )
        rows = rows.with_columns(
            district=pl.when(pl.col("circ") == TERRITORIAL).then(pl.col("dep")).otherwise(pl.lit("CO"))
        ).join(won, on=["district", "party", "cand"], how="left")
        rows = rows.sort("elected", descending=True, nulls_last=True).unique(
            ["corp", "circ", "district", "party", "cand"], keep="first"
        )
        labels.append(rows)
        labels.append(
            rows.sort("position")
            .unique(["corp", "circ", "district", "party"], keep="first")
            .with_columns(cand=pl.lit("0"), cand_name=None, position=None, elected=pl.lit(False))
        )
    _write(DAYS[3], pl.concat(votes, how="diagonal_relaxed"), pl.concat(labels, how="diagonal_relaxed"), towns)


def _full(first: str, last: str) -> pl.Expr:
    return pl.concat_str([pl.col(first).fill_null(""), pl.col(last).fill_null("")], separator=" ").pipe(
        lambda e: e.str.replace_all(r"\s+", " ").str.strip_chars(" .")
    )


# 2006: FoxPro tables by polling table

CORPS_2006 = {"01": SENATE, "02": HOUSE, "03": PRESIDENT}
CIRCS_2006 = {"0": NATIONAL, "1": TERRITORIAL, "3": INDIGENOUS, "4": AFRO, "5": ABROAD}


def _circ_district(frame: pl.DataFrame) -> pl.DataFrame:
    """Votes abroad for the House count in the international circunscription; national districts are CO."""
    return frame.with_columns(
        circ=pl.when((pl.col("corp") == HOUSE) & (pl.col("dep") == ABROAD_DEP) & (pl.col("circ") == TERRITORIAL))
        .then(pl.lit(ABROAD))
        .otherwise(pl.col("circ"))
    ).with_columns(
        district=pl.when(pl.col("circ") == TERRITORIAL).then(pl.col("dep")).otherwise(pl.lit("CO")),
    )


def _special_codes(frame: pl.DataFrame) -> pl.DataFrame:
    """Blank, null, unmarked, and total ballots are 996 to 999, or 896 to 899 on a special circunscription's line."""
    code = pl.col("party")
    special = code.is_in(["896", "897", "898", "899", *SPECIALS])
    return frame.with_columns(
        cand=pl.when(special).then(pl.lit("9") + code.str.slice(1)).otherwise(pl.col("cand")),
        party=pl.when(special).then(pl.lit("0")).otherwise(code),
    )


def parse_2006() -> None:
    def table(name: str) -> pl.DataFrame:
        return dbf(_find(_unpack(2006) / "data", name))

    frame = table("tb_votacionz.dbf")
    votes = _special_codes(
        frame.select(
            corp=pl.col("CODI_COR").replace_strict(CORPS_2006),
            circ=pl.col("CODI_CIR").replace_strict(CIRCS_2006),
            dep=_code("CODI_DEP", 2),
            mun=_code("CODI_MUN", 3),
            zona=_code("CODI_ZON", 2),
            puesto=_code("CODI_PTO", 2),
            mesa=_num("NUME_MES"),
            party=_num("CODI_PAR"),
            cand=_num("CODI_CAN"),
            votes=_int("VOTA_CAN"),
        )
    )
    votes = _circ_district(votes)
    listas = table("listas.dbf")
    labels = _special_codes(
        listas.select(
            corp=pl.col("CODI_COR").replace_strict(CORPS_2006, default=None),
            circ=pl.col("CODI_CIR").replace_strict(CIRCS_2006, default=None),
            dep=_code("CODI_DEP", 2),
            party=_num("CODI_PAR"),
            cand=_num("CODI_CAN"),
            party_name=_clean("MOVI_POL"),
            cand_name=_full("NOMB_CAN", "APEL_CAN"),
        ).filter(pl.col("corp").is_not_null())
    ).filter(~pl.col("cand").is_in(SPECIALS))
    labels = labels.with_columns(
        cand_name=pl.when((pl.col("cand") == "0") & (pl.col("corp") != PRESIDENT)).then(None).otherwise("cand_name"),
        position=pl.when(pl.col("cand") == "0").then(None).otherwise(_int("cand")).cast(pl.Int32),
    )
    won = table("tb_curules.dbf").select(
        corp=pl.col("CODI_COR").replace_strict(CORPS_2006),
        circ=pl.col("CODI_CIR").replace_strict(CIRCS_2006),
        dep=_code("CODI_DEP", 2),
        party=_num("CODI_PAR"),
        cand=_num("CODI_CAN"),
        elected=pl.lit(True),
    )
    won = _circ_district(won).drop("dep")
    labels = _circ_district(labels).join(won, on=["corp", "circ", "district", "party", "cand"], how="left")
    missing = won.join(labels, on=["corp", "circ", "district", "party", "cand"], how="anti")
    if missing.height:
        print(f"2006: {missing.height} winners are not on the lists: {missing.head(3).rows()}", flush=True)
    labels = labels.with_columns(
        elected=pl.when(pl.col("corp") == PRESIDENT).then(pl.col("party") == "3").otherwise(pl.col("elected"))
    )
    places = table("puestos.dbf").select(
        dep=_code("CODI_DEP", 2),
        mun=_code("CODI_MUN", 3),
        zona=_code("CODI_ZON", 2),
        puesto=_code("CODI_PTO", 2),
        name=_clean("NOMB_PTO"),
    )
    towns = table("municipios.dbf").select(
        dep=_code("CODI_DEP", 2),
        mun=_code("CODI_MUN", 3),
        zona=pl.lit(None, pl.String),
        puesto=pl.lit(None, pl.String),
        name=_clean("NOMB_MUN"),
    )
    places = pl.concat([places, towns])
    for d in (DAYS[5], DAYS[6]):
        president = pl.col("corp") == PRESIDENT
        mask = president if d.family == "presidential" else ~president
        _write(d, votes.filter(mask), labels.filter(mask), places)


# 2010: one Access database per department, by polling table

CORPS_2010 = {"1": SENATE, "2": HOUSE, "3": ANDEAN, "4": PRESIDENT, "9": PRESIDENT}
CIRCS_2010 = {"0": NATIONAL, "1": TERRITORIAL, "2": INDIGENOUS, "3": AFRO, "4": ABROAD}


def _split_code(frame: pl.DataFrame, column: str, party: pl.Expr | None = None) -> pl.DataFrame:
    """A candidate code is the party's code times 1000 plus the candidate's; 996 to 998 are the special lines."""
    code = _int(column)
    special = code.is_between(990, 999)
    split = pl.lit(True) if party is None else (code // 1000 == party.cast(pl.Int64))
    return frame.with_columns(
        party=pl.when(special)
        .then(pl.lit("0"))
        .when(split)
        .then((code // 1000).cast(pl.String))
        .otherwise(party.cast(pl.String) if party is not None else None),
        cand=pl.when(special)
        .then(code.cast(pl.String))
        .when(split)
        .then((code % 1000).cast(pl.String))
        .otherwise(code.cast(pl.String)),
    )


def parse_2010() -> None:
    folder = _find(_unpack(2010) / "data", "ConvotoEs.mdb").parent
    national = folder / "ConvotoEs.mdb"
    frames, places = [], []
    for path in sorted(folder.glob("ConvotoEs_*.mdb")):
        deps = _mdb(path, "DEPARTAMENTOS").select(dep_id="ID", dep=_code("CODIGO", 2))
        towns = _mdb(path, "MUNICIPIOS").select(
            mun_id="ID", dep_id="DEPARTAMENTO", mun=_code("CODIGO", 3), town=_clean("DESCRIPCION")
        )
        zones = _mdb(path, "ZONAS").select(zona_id="ID", mun_id="MUNICIPIO", zona=_code("CODIGO", 2))
        posts = _mdb(path, "PUESTOS").select(
            puesto_id="ID", zona_id="ZONA", puesto=_code("CODIGO", 2), name=_clean("DESCRIPCION")
        )
        mesas = _mdb(path, "MESAS").select(mesa_id="ID", puesto_id="PUESTO", mesa=_num("CODIGO"))
        where = (
            mesas.join(posts, on="puesto_id").join(zones, on="zona_id").join(towns, on="mun_id").join(deps, on="dep_id")
        )
        rows = _mdb(path, "RESUMEN_MESAS").select(
            corp_code="CORPORACION", circ_code="CIRCUNS", code="CANDIDATO", mesa_id="MESA", votes=_int("VOTACION")
        )
        joined = rows.join(where, on="mesa_id", how="left")
        lost = joined.filter(pl.col("dep").is_null())
        if lost.height:
            raise ValueError(f"{path.name}: {lost.height} rows of unknown polling tables")
        frames.append(joined.drop("mesa_id", "puesto_id", "zona_id", "mun_id", "dep_id", "name", "town"))
        places.append(where.select("dep", "mun", "zona", "puesto", "name").unique())
        places.append(
            where.select(
                "dep", "mun", zona=pl.lit(None, pl.String), puesto=pl.lit(None, pl.String), name="town"
            ).unique()
        )
        print(f"  {path.name}: {rows.height:,} rows", flush=True)
    frame = pl.concat(frames)
    frame = _split_code(frame, "code").with_columns(
        corp=pl.col("corp_code").replace_strict(CORPS_2010),
        circ=pl.col("circ_code").replace_strict(CIRCS_2010),
    )
    votes = _circ_district(frame)
    places_frame = pl.concat(places)
    dep_ids = dict(_mdb(national, "DEPARTAMENTOS").select("ID", _code("CODIGO", 2)).iter_rows())
    candidates = _mdb(national, "CANDIDATOS").with_columns(dep=pl.col("DIVIPOL").replace_strict(dep_ids, default="00"))
    parties = dict(_mdb(national, "PARTIDOS").select(_num("CODIGO"), _clean("NOMBRE")).iter_rows())
    labels = (
        _split_code(candidates, "CODIGO")
        .select(
            "party",
            "cand",
            "dep",
            corp_code="CORPORACION",
            corp=pl.col("CORPORACION").replace_strict(CORPS_2010),
            circ=pl.col("CIRCUNS").replace_strict(CIRCS_2010),
            cand_name=_clean("NOMBRE"),
        )
        .filter(~pl.col("cand").is_in(SPECIALS))
    )
    labels = labels.with_columns(
        party_name=pl.col("party").replace_strict(parties, default=None),
        cand_name=pl.when((pl.col("cand") == "0") & (pl.col("corp") != PRESIDENT)).then(None).otherwise("cand_name"),
        position=pl.when(pl.col("cand") == "0").then(None).otherwise(_int("cand")).cast(pl.Int32),
    )
    won = []
    for table in ("CONGRESO", "PARLAMENTO"):
        elected = _mdb(national, table)
        won.append(
            _split_code(elected, "COD_CANDIDATO", _int("COD_PARTIDO")).select(
                "party",
                "cand",
                corp=pl.col("CORPORACION").replace_strict(CORPS_2010),
                circ=pl.col("CIRCUNSC").replace_strict(CIRCS_2010),
                dep=pl.col("DPTO").replace_strict(dep_ids, default="00"),
                cand_name=_full("NOMB_CANDIDATO", "APEL_CANDIDATO"),
                elected=pl.lit(True),
            )
        )
    won_frame = _circ_district(pl.concat(won))
    labels = _circ_district(labels)
    keys = ["corp", "circ", "district", "party", "cand"]
    labels = labels.join(won_frame.select(*keys, "elected"), on=keys, how="left")
    missing = won_frame.join(labels, on=keys, how="anti")
    if missing.height:
        heads = labels.filter(pl.col("cand") == "0").select(*keys[:-1], "party_name").unique(keys[:-1])
        added = missing.join(heads, on=keys[:-1], how="left").with_columns(position=_int("cand").cast(pl.Int32))
        print(f"2010: {added.height} elected members of closed lists added", flush=True)
        labels = pl.concat([labels, added], how="diagonal_relaxed")
    winner = (pl.col("corp_code") == "9") & (pl.col("cand_name").str.contains("SANTOS"))
    labels = labels.with_columns(elected=pl.when(pl.col("corp") == PRESIDENT).then(winner).otherwise(pl.col("elected")))
    labels = labels.with_columns(corp_code=pl.col("corp_code").fill_null("1"))
    labels = labels.with_columns(corp_code=pl.col("corp_code").fill_null("1"))
    for d, corps in ((DAYS[7], ["1", "2", "3"]), (DAYS[8], ["4"]), (DAYS[9], ["9"])):
        mask = pl.col("corp_code").is_in(corps)
        _write(d, votes.filter(mask), labels.filter(mask), places_frame)


# 2014: SQLite databases with one table of polling-table rows per department

CORPS_2014 = {1: SENATE, 2: HOUSE, 3: ANDEAN}
CIRCS_2014 = {0: NATIONAL, 1: TERRITORIAL, 4: INDIGENOUS, 5: AFRO}


def _sqlite(name: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_find(_unpack(2014) / 'data', name)}?mode=ro", uri=True)


def _sqlite_votes(connection: sqlite3.Connection, congress: bool) -> pl.DataFrame:
    tables = [
        name
        for (name,) in connection.execute(
            "select name from sqlite_master where type = 'table' and name like 'votacion\\_%' escape '\\' order by name"
        )
    ]
    circ = "circunscripcion" if congress else "0 as circunscripcion"
    frames = [
        pl.read_database(
            f"select corp, depto, mpio, zona, puesto, desc_puesto, desc_mpio, mesa, partido, desc_partido, candidato,"
            f" desc_candidato, votos, {circ} from {name}",
            connection,
            schema_overrides={"puesto": pl.String, "desc_partido": pl.String, "desc_candidato": pl.String},
        )
        for name in tables
    ]
    frame = pl.concat([f for f in frames if f.height], how="vertical_relaxed")
    special = pl.col("candidato").is_between(990, 999)
    return frame.with_columns(
        corp=pl.col("corp").replace_strict(CORPS_2014) if congress else pl.lit(PRESIDENT),
        circ=pl.col("circunscripcion").replace_strict(CIRCS_2014),
        dep=pl.col("depto").cast(pl.String).str.zfill(2),
        mun=pl.col("mpio").cast(pl.String).str.zfill(3),
        zona=pl.col("zona").cast(pl.String).str.zfill(2),
        puesto=pl.col("puesto").str.strip_chars().str.zfill(2),
        mesa=pl.col("mesa").cast(pl.String),
        party=pl.when(special).then(pl.lit("0")).otherwise(pl.col("partido").cast(pl.String)),
        cand=pl.col("candidato").cast(pl.String),
        votes=pl.col("votos").cast(pl.Int64),
    )


def parse_2014() -> None:
    for d, name, congress in (
        (DAYS[10], "EstadisticasCon.db", True),
        (DAYS[11], "Estadisticas1.db", False),
        (DAYS[12], "Estadisticas2.db", False),
    ):
        connection = _sqlite(name)
        rows = _circ_district(_sqlite_votes(connection, congress))
        named = rows.filter(~pl.col("cand").is_in(SPECIALS)).unique(["corp", "circ", "district", "party", "cand"])
        lists = pl.col("corp") != PRESIDENT
        labels = named.select(
            "corp",
            "circ",
            "district",
            "party",
            "cand",
            party_name=_clean("desc_partido"),
            cand_name=pl.when(lists & (pl.col("cand") == "0")).then(None).otherwise(_clean("desc_candidato")),
            position=pl.when(lists & (pl.col("cand") != "0")).then(pl.col("cand").cast(pl.Int32)).otherwise(None),
        )
        parties = dict(
            pl.read_database("select papocopa, paponomb from partpoli", connection)
            .select(pl.col("papocopa").cast(pl.String), _clean("paponomb"))
            .iter_rows()
        )
        labels = labels.with_columns(
            party_name=pl.col("party").replace_strict(parties, default=None).fill_null(pl.col("party_name"))
        )
        heads = labels.filter(pl.col("cand") == "0").select("corp", "circ", "district", "party", "party_name")
        if congress:
            elected = pl.read_database(
                "select corporacion, departamento, partido, candidato, nombre, circunscripcion from elegidos",
                connection,
            ).select(
                corp=pl.col("corporacion").replace_strict(CORPS_2014),
                circ=pl.col("circunscripcion").replace_strict(CIRCS_2014),
                dep=pl.col("departamento").cast(pl.String).str.zfill(2),
                party=pl.col("partido").cast(pl.String),
                cand=pl.col("candidato").cast(pl.String),
                cand_name=_clean("nombre"),
                elected=pl.lit(True),
            )
            elected = _circ_district(elected)
            keys = ["corp", "circ", "district", "party", "cand"]
            labels = labels.join(elected.select(*keys, "elected"), on=keys, how="left")
            added = (
                elected.join(labels, on=keys, how="anti")
                .join(heads, on=keys[:-1], how="left")
                .with_columns(position=pl.col("cand").cast(pl.Int32))
            )
            print(f"{d.day}: {added.height} elected members of closed lists added", flush=True)
            labels = pl.concat([labels, added], how="diagonal_relaxed")
        else:
            winner = pl.lit(d.round_number == 2) & (pl.col("cand_name").str.contains("SANTOS"))
            labels = labels.with_columns(elected=winner)
        places = pl.concat(
            [
                rows.select("dep", "mun", "zona", "puesto", name=_clean("desc_puesto")),
                rows.select(
                    "dep",
                    "mun",
                    zona=pl.lit(None, pl.String),
                    puesto=pl.lit(None, pl.String),
                    name=_clean("desc_mpio"),
                ),
            ]
        ).unique(["dep", "mun", "zona", "puesto"])
        _write(d, rows, labels, places)


def parse() -> None:
    parse_1998()
    parse_2002()
    parse_2006()
    parse_2010()
    parse_2014()


# Packages


def key(kind: str, *parts: object) -> str:
    return package.Package(SOURCE, SOURCE, "").id(kind, *parts)


COLOMBIA = rnec.COLOMBIA
COUNTRY = rnec.COUNTRY
SOURCE_ID = key("source", SOURCE)
BASE_GEO, BASE_REGISTRY = f"{rnec.SOURCE}/geo", f"{rnec.SOURCE}/registry"
REGISTRY = f"{SOURCE}/registry"
ANDEAN_BODY = key("body", "andean_parliament")
HARE = key("electoral_system", "hare")
REFORM = date(2003, 7, 3)
"""Acto Legislativo 01 de 2003: one list per party, D'Hondt with a threshold, and an optional preference vote."""
HARE_METHOD = (
    "Before the 2003 political reform, each party could run several lists for the Senate and the House. Seats went "
    "to the lists by the electoral quota (cuociente electoral, the valid votes divided by the seats) and the largest "
    "remainders (residuos), and each list was closed: its seats went to its candidates in list order."
)
METHOD = (
    "From the statistics CD the Registraduría Nacional del Estado Civil issued after this election, as republished by "
    "its Observatorio. {grain} The blank vote (voto en blanco) is stored as an option; null votes (votos nulos) are "
    "stored as invalid ballots, unmarked ballots (tarjetas no marcadas) as blank ballots, and the CD's total of "
    "ballots, where it prints one, as ballots cast. Rows with zero votes are left out."
)
GRAIN = {
    1998: "The CD gives results by municipality.",
    2002: "The CD gives results by municipality.",
    2006: "The CD gives results by polling table (mesa).",
    2010: "The CD gives results by polling table (mesa).",
    2014: "The CD gives results by polling table (mesa).",
}
LIST_NOTE = (
    " Votes for a list alone are stored on a legend line under the list and preference votes on candidate lines; a "
    "list without preference votes holds its votes itself."
)
OLD_LIST_NOTE = " Each list is a ballot line of its own, labeled by its head candidate."
SHARED_NOTE = (
    "Voters chose either a list of this circunscription or one of the {main} on the same ballot, so the null and "
    "unmarked ballots{blank} of that ballot are stored with the {main} contest, not here."
)
SENATE_1998 = (
    "The 1998 CD does not mark which Senate lists ran in the special indigenous circunscription (two seats), so all "
    "Senate lists are stored in one contest. It lists only the head of most lists, so elected candidates are stored "
    "as the CD flags them and seat totals per list are not."
)
MEASURES = {NULL: 5, UNMARKED: 6, CAST: 3}
PRELIMINARY = {
    2014: (
        " The 2014 CD holds the preconteo, the count the polling-table juries report on election night: its national "
        "totals run a little above those of the escrutinio (the official canvass), so this result set is stored as "
        "preliminary."
    )
}
CIRC_NAMES = {
    INDIGENOUS: "Circunscripción Especial Indígena",
    AFRO: "Circunscripción Especial Afrodescendiente",
    ABROAD: "Circunscripción Internacional",
}


def _days() -> list[Day]:
    return [d for d in DAYS if (_folder(d) / "votes.parquet").exists()]


def _read(d: Day, name: str) -> pl.DataFrame:
    return pl.read_parquet(_folder(d) / f"{name}.parquet")


def packages() -> list[str]:
    return [REGISTRY, *(d.key for d in _days())]


def _ids(name: str, table: str) -> set[str]:
    path = package.folder(name) / "build" / f"{table}.parquet"
    return set(pl.read_parquet(path)["id"]) if path.exists() else set()


def _names(name: str) -> dict[str, str]:
    frame = pl.read_parquet(package.folder(name) / "build" / "name.parquet")
    return dict(frame.filter(pl.col("is_preferred")).select("entity_id", "text").iter_rows())


def _fold(text: str) -> str:
    return rnec._fold(text)


def party(name: str | None) -> str | None:
    folded = _fold(rnec._clean(name)) if name else ""
    return key("party", folded) if folded else None


def _party_names() -> dict[str, str]:
    """Each party's name as the CDs print it, keyed by its folded form; the latest spelling wins."""
    names: dict[str, str] = {}
    for d in _days():
        for text in _read(d, "labels")["party_name"].drop_nulls().unique().sort():
            text = rnec._clean(text)
            folded = _fold(text)
            if folded:
                names[folded] = text
    return names


def _cds() -> list[int]:
    return sorted({d.cd for d in _days()})


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The statistics CDs of Colombia's presidential, congressional, and Andean Parliament elections of 1998 to 2014 "
        "as a source, with the parties and movements they name, the Andean Parliament, and the list system before 2003",
        depends=[BASE_GEO, BASE_REGISTRY],
    )
    p.external.add(COLOMBIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Registraduría Nacional del Estado Civil",
        title="Estadísticas electorales: elecciones de Presidente y Congreso de 1998 a 2014 (CD)",
        url=rnec.CATALOG,
        license=rnec.LICENSE,
        license_url="https://www.registraduria.gov.co/Politicas-de-privacidad.html",
        attribution="Registraduría Nacional del Estado Civil, Observatorio en Democracia y Asuntos Electorales",
    )
    for cd in _cds():
        p.artifact(_artifact(cd), SOURCE_ID)
    p.add("body", id=ANDEAN_BODY, polity_id=COLOMBIA, kind="unicameral")
    p.name(ANDEAN_BODY, "Andean Parliament (representatives of Colombia)", "en", "official")
    p.name(ANDEAN_BODY, "Parlamento Andino (representantes de Colombia)", "es", "native", False)
    p.add("electoral_system", id=HARE, polity_id=COLOMBIA, rules={"formula": "hare"}, rules_version=1, valid_to=REFORM)
    p.add("note", id=key("note", HARE), entity_id=HARE, kind="methodology", body_md=HARE_METHOD, lang="en")
    for folded, text in sorted(_party_names().items()):
        id = key("party", folded)
        p.add("party", id=id, kind="party", polity_id=COLOMBIA)
        p.name(id, text, "es", "official")
    return p


ELECTIONS = {
    "presidential": ("presidential", "{year} Colombian presidential election", "Elecciones presidenciales de {year}"),
    "congress": ("congress", "{year} Colombian parliamentary election", "Elecciones legislativas de {year}"),
    "andean": (
        "andean-parliament",
        "{year} Andean Parliament election in Colombia",
        "Elección de representantes al Parlamento Andino de {year}",
    ),
}


def _family(d: Day, corp: str) -> str:
    return "andean" if corp == ANDEAN else d.family


class Places:
    def __init__(self) -> None:
        self.names = _names(BASE_GEO)
        self.known = _ids(BASE_GEO, "area")

    def department(self, dep: str) -> str:
        return rnec.DEPARTMENTS.get(dep) or self.names.get(rnec.area("co.departamento", dep), dep)


def _contest_name(corp: str, circ: str, district: str, places: Places, year: int) -> str:
    if corp == PRESIDENT:
        return "Presidente de la República"
    if corp == ANDEAN:
        return "Parlamento Andino"
    if corp == SENATE:
        if year == 1998:
            return "Senado"
        return f"Senado, {CIRC_NAMES.get(circ, 'Circunscripción Nacional')}"
    if circ in CIRC_NAMES:
        return f"Cámara, {CIRC_NAMES[circ]}"
    return f"Cámara, {places.department(district)}"


def _system(d: Day, corp: str) -> str:
    if corp == PRESIDENT:
        return rnec.system("president")
    return HARE if d.day < REFORM else rnec.system("lists")


def election_day(d: Day) -> Package:
    day = d.day
    year = d.year
    depends = [REGISTRY, BASE_GEO, BASE_REGISTRY]
    first: Day | None = None
    if d.round_number == 2:
        first = next(o for o in DAYS if o.family == d.family and o.day.year == day.year and o.round_number == 1)
        depends.append(first.key)
    kind = "presidential election" if d.family == "presidential" else "congressional elections"
    if d.round_number == 2:
        kind = "presidential runoff"
    summary = f"Colombia's {kind} of {day:%-d %B %Y} from the Registraduría's statistics CD"
    p = Package(d.key, SOURCE, summary, depends)
    p.external.add(COLOMBIA)
    p.uses(_artifact(d.cd))
    votes = _read(d, "votes")
    labels = _read(d, "labels")
    places = Places()
    known_parties = _ids(REGISTRY, "party")
    options = votes.group_by("corp", "circ", "district", "party", "cand").agg(pl.col("votes").sum())
    names = {
        (c, ci, di, pa, ca): (pn, cn, mt, pos, el)
        for c, ci, di, pa, ca, pn, cn, mt, pos, el in labels.select(
            "corp", "circ", "district", "party", "cand", "party_name", "cand_name", "mate", "position", "elected"
        ).iter_rows()
    }
    groups: dict[tuple[str, str, str, str], str] = {}
    for c, ci, di, pa, pn in (
        labels.filter(pl.col("party_name").is_not_null())
        .sort("cand")
        .select("corp", "circ", "district", "party", "party_name")
        .iter_rows()
    ):
        groups.setdefault((c, ci, di, pa), pn)
    contests = options.select("corp", "circ", "district").unique().sort("corp", "circ", "district")
    shared = {
        (SENATE, INDIGENOUS): ("Senate's national circunscription", NATIONAL, "CO"),
        (HOUSE, INDIGENOUS): ("House's departmental circunscriptions", TERRITORIAL, None),
        (HOUSE, AFRO): ("House's departmental circunscriptions", TERRITORIAL, None),
    }
    elections: dict[str, str] = {}
    mapping: list[dict[str, object]] = []
    sets: dict[str, str] = {}
    paths: set[str] = set()
    method = METHOD.format(grain=GRAIN[year])
    old = day < REFORM
    for corp, circ, district in contests.iter_rows():
        family = _family(d, corp)
        word, english, spanish = ELECTIONS[family]
        runoff = d.round_number == 2
        if family not in elections:
            election = elections[family] = key("election", day, family)
            p.add(
                "election",
                id=election,
                polity_id=COLOMBIA,
                kind="runoff" if runoff else "general",
                date_from=day,
                date_to=day,
                status="held",
            )
            suffix = (", second round", ", segunda vuelta") if runoff else ("", "")
            p.name(election, english.format(year=day.year) + suffix[0], "en", "official")
            p.name(election, spanish.format(year=day.year) + suffix[1], "es", "native", False)
            p.add("slug", path=f"co/{day}-{word}" + ("-runoff" if runoff else ""), entity_id=election, is_current=True)
        election = elections[family]
        base = f"co/{day}-{word}" + ("-runoff" if runoff else "")
        ck = f"{corp}:{circ}:{district}"
        contest = key("contest", day, ck)
        single = corp == PRESIDENT
        text = _contest_name(corp, circ, district, places, year)
        place = COUNTRY
        if circ == TERRITORIAL:
            dep_area = rnec.area("co.departamento", district)
            place = dep_area if dep_area in places.known else COUNTRY
        here_labels = labels.filter(
            (pl.col("corp") == corp) & (pl.col("circ") == circ) & (pl.col("district") == district)
        )
        elected_members = here_labels.filter(pl.col("elected").fill_null(False) & (pl.col("cand") != "0")).height
        counts_seats = not single and year != 1998 and elected_members > 0
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office" if single else "list_allocation",
            office_id=rnec.PRESIDENCY if single else None,
            body_id=None
            if single
            else ANDEAN_BODY
            if corp == ANDEAN
            else rnec.SENATE_BODY
            if corp == SENATE
            else rnec.HOUSE_BODY,
            district_area_id=place,
            magnitude=1 if single else elected_members if counts_seats else None,
            round_number=d.round_number,
            previous_round_id=key("contest", first.day, ck) if first is not None else None,
            electoral_system_id=_system(d, corp),
            default_result_kind="counted",
        )
        p.name(contest, text, "es", "official")
        slug = changeset.slug(text) or district
        path = f"{base}/{slug}"
        if path in paths:
            path = f"{base}/{slug}-{changeset.slug(ck)}"
        paths.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = sets[ck] = key("result_set", day, ck)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="preliminary" if year in PRELIMINARY else "official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        note = method + ((OLD_LIST_NOTE if old else LIST_NOTE) if not single else "")
        if year in PRELIMINARY:
            note += PRELIMINARY[year]
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        here = options.filter((pl.col("corp") == corp) & (pl.col("circ") == circ) & (pl.col("district") == district))
        voted = {(pa, ca) for pa, ca in here.select("party", "cand").iter_rows()}
        if (corp, circ) in shared or (corp == HOUSE and circ == ABROAD and year == 2002):
            main = shared.get((corp, circ), ("House's departmental circunscriptions", TERRITORIAL, None))[0]
            has_blank = any(ca == BLANK for _, ca in voted)
            caveat = SHARED_NOTE.format(main=main, blank="" if has_blank else ", and its blank votes,")
            p.add(
                "note",
                id=key("note", result_set, "shared"),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        if corp == SENATE and year == 1998:
            p.add(
                "note",
                id=key("note", result_set, "1998"),
                entity_id=result_set,
                kind="caveat",
                body_md=SENATE_1998,
                lang="en",
            )
        listed = set() if single else {(pa, ca) for pa, ca in here_labels.select("party", "cand").iter_rows()}
        pairs = sorted(voted | listed, key=lambda pair: (int(pair[0]), int(pair[1])))
        outcomes = _contest(
            p, d, corp, circ, district, ck, pairs, voted, names, groups, known_parties, counts_seats, mapping
        )
        for row in outcomes:
            p.add("outcome", result_set_id=result_set, **row)
    units = _units(p, d, votes, places.known)
    frame = (
        votes.with_columns(ck=pl.concat_str("corp", "circ", "district", separator=":"), code=_unit_code(d))
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
            ),
            on=["ck", "party", "cand"],
            how="left",
        )
        .join(pl.DataFrame({"ck": list(sets), "result_set_id": list(sets.values())}), on="ck")
        .join(units, on="code")
        .group_by("result_set_id", "unit_id", "option_id", "measure")
        .agg(pl.col("votes").sum(), pl.len().alias("rows"))
    )
    if frame["rows"].sum() != votes.height or frame["measure"].null_count():
        raise ValueError(f"{p.key}: {votes.height:,} rows, {frame['rows'].sum():,} mapped")
    p.frame(
        "result_value",
        frame.select(
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
    return p


Label = tuple[str | None, str | None, str | None, int | None, bool | None]


def _contest(
    p: Package,
    d: Day,
    corp: str,
    circ: str,
    district: str,
    ck: str,
    pairs: list[tuple[str, str]],
    voted: set[tuple[str, str]],
    names: dict[tuple[str, str, str, str, str], Label],
    groups: dict[tuple[str, str, str, str], str],
    known_parties: set[str],
    counts_seats: bool,
    mapping: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Add one contest's options and candidacies, map its codes to options and measures, and return its outcomes."""
    day = d.day
    contest = key("contest", day, ck)
    outcomes: list[dict[str, object]] = []
    old = day < REFORM
    open_lists = {pa for pa, ca in voted if ca != "0" and ca not in SPECIALS}
    lists: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for pa, ca in pairs:
        if ca == BLANK:
            option = key("option", day, ck, "blank")
            p.add(
                "contest_option", id=option, contest_id=contest, kind="none_of_the_above", ballot_label="Voto en blanco"
            )
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
            continue
        if ca in MEASURES:
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": None, "measure": MEASURES[ca]})
            continue
        head = names.get((corp, circ, district, pa, "0"))
        own = names.get((corp, circ, district, pa, ca))
        group = (head[0] if head else None) or (own[0] if own else None) or groups.get((corp, circ, district, pa))
        party_id = party(group)
        if party_id not in known_parties:
            party_id = None
        if corp == PRESIDENT:
            option = key("option", day, ck, pa, ca)
            label = rnec.title(own[1] if own and own[1] else None) or rnec.title(group) or f"Candidato {ca}"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_position=int(ca) if int(ca) < 1000 else None,
                ballot_label=label,
            )
            _person(p, day, ck, pa, ca, option, label, "head", None, None)
            if own and own[2]:
                mate = rnec.title(own[2])
                _person(p, day, ck, pa, f"{ca}:mate", option, mate, "running_mate", None, None)
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
            if own and own[4]:
                outcomes.append({"option_id": option, "kind": "elected"})
            continue
        if pa not in lists:
            lists[pa] = key("option", day, ck, pa, "list")
            if old:
                first = names.get((corp, circ, district, pa, "1"))
                leader = rnec.title(first[1]) if first and first[1] else ""
                name = rnec.title(group) if group else ""
                label = f"{leader} ({name})" if leader and name else leader or name or f"Lista {pa}"
            else:
                label = (rnec.title(group) if group else "") or f"Lista {pa}"
            p.add(
                "contest_option",
                id=lists[pa],
                contest_id=contest,
                kind="list",
                party_id=party_id,
                ballot_position=int(pa) if old else None,
                ballot_label=label,
            )
            members[pa] = []
        if ca == "0" and pa not in open_lists:
            if (pa, ca) in voted:
                mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": lists[pa], "measure": 1})
            continue
        if ca == "0":
            option = key("option", day, ck, pa, ca)
            name = (rnec.title(group) if group else "") or f"Lista {pa}"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="legend",
                parent_option_id=lists[pa],
                party_id=party_id,
                ballot_label=f"{name} (solo por la lista)",
            )
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
            continue
        label = rnec.title(own[1] if own and own[1] else None) or f"Candidato {ca}"
        position = own[3] if own and own[3] is not None else int(ca)
        if pa in open_lists and (pa, ca) in voted:
            option = key("option", day, ck, pa, ca)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=lists[pa],
                ballot_position=int(ca),
                ballot_label=label,
            )
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
        candidacy = _person(p, day, ck, pa, ca, lists[pa], label, "list_member", position, party_id)
        if own and own[4]:
            members[pa].append(candidacy)
    for pa, option in lists.items():
        won = members.get(pa, [])
        for candidacy in won:
            outcomes.append({"option_id": option, "candidacy_id": candidacy, "kind": "elected"})
        if counts_seats and won:
            outcomes.append({"option_id": option, "kind": "won", "seats": len(won)})
    return outcomes


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
    member: str | None,
) -> str:
    tag = f"{day}:{ck}:{pa}:{ca}"
    person = key("person", tag)
    p.add("person", id=person)
    p.name(person, label, "es", "official")
    candidacy = key("candidacy", tag)
    p.add(
        "candidacy",
        id=candidacy,
        person_id=person,
        option_id=option,
        role=role,
        list_position=position,
        ballot_name=label,
        member_of_party_id=member,
    )
    return candidacy


def _unit_code(d: Day) -> pl.Expr:
    """A unit's code: the polling table where the CD has them, else the municipality."""
    day = pl.lit(str(d.day))
    town = pl.concat_str(day, "dep", "mun", separator=":")
    return (
        pl.when(pl.col("mesa").is_not_null())
        .then(pl.concat_str(town, "zona", "puesto", "mesa", separator=":"))
        .otherwise(town)
    )


def _units(p: Package, d: Day, votes: pl.DataFrame, known: set[str]) -> pl.DataFrame:
    day = d.day
    places = _read(d, "places")
    puesto_names = {
        (de, m, z, pu): n for de, m, z, pu, n in places.filter(pl.col("puesto").is_not_null()).iter_rows() if n
    }
    town_names = {(de, m): n for de, m, z, pu, n in places.filter(pl.col("puesto").is_null()).iter_rows() if n}
    frame = (
        votes.with_columns(code=_unit_code(d))
        .select("code", "dep", "mun", "zona", "puesto", "mesa")
        .unique("code")
        .sort("code")
    )
    rows: list[dict[str, object]] = []
    added: set[str] = set()
    codes, ids = [], []

    def unit(scheme: str, code: str, relation: str, area: str | None, kind: str, parent: str | None) -> str:
        id = key("unit", scheme, code)
        if code not in added:
            added.add(code)
            rows.append(
                {
                    "id": id,
                    "polity_id": COLOMBIA,
                    "scheme_code": scheme,
                    "code": code,
                    "parent_id": parent,
                    "area_relation": relation,
                    "area_id": area,
                    "kind": kind,
                }
            )
        return id

    for code, dep, mun, zona, puesto, mesa in frame.iter_rows():
        town = rnec.area("co.municipio", f"{dep}:{mun}")
        if dep == ABROAD_DEP:
            relation, area, kind = "unlocated", None, "abroad_mission"
        elif town in known:
            relation, area, kind = "within", town, "standard"
        elif rnec.area("co.departamento", dep) in known:
            relation, area, kind = "within", rnec.area("co.departamento", dep), "standard"
        else:
            relation, area, kind = "within", COUNTRY, "standard"
        if mesa is not None:
            parent_code = f"{day}:{dep}:{mun}:{zona}:{puesto}"
            parent = unit("co.puesto", parent_code, relation, area, kind, None)
            id = unit("co.mesa", code, relation, area, kind, parent)
        else:
            if area == town:
                relation = "equals"
            id = unit("co.municipio", code, relation, area, kind, None)
        codes.append(code)
        ids.append(id)
    for (dep, mun, zona, puesto), text in puesto_names.items():
        parent_code = f"{day}:{dep}:{mun}:{zona}:{puesto}"
        if parent_code in added:
            p.name(key("unit", "co.puesto", parent_code), rnec.title(text), "es", "official")
    for (dep, mun), text in town_names.items():
        code = f"{day}:{dep}:{mun}"
        if code in added:
            p.name(key("unit", "co.municipio", code), rnec.title(text), "es", "official")
    p.frame("reporting_unit", pl.DataFrame(rows, infer_schema_length=None))
    return pl.DataFrame({"code": codes, "unit_id": ids})


def build() -> None:
    registry().write()
    for d in _days():
        election_day(d).write()


def emit() -> None:
    package.run("load", packages())
