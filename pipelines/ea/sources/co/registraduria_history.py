"""Colombia's territorial elections of 1997 to 2015 from the Registraduría's statistics CDs.

The Observatorio of the Registraduría Nacional del Estado Civil republishes the statistics CDs it issued after each
territorial election, each a ZIP around Windows installers. The 1997 CD holds FoxPro (dBASE) tables as plain files;
the 2000 to 2007 CDs pack them in Setup Wizard and InstallShield cabinets, and the 2011 and 2015 CDs pack a SQLite
database in an Inno Setup installer. The 1997 and 2000 CDs give results by municipality (and Bogotá's local boards by
polling place in 2000); the 2003 CD by polling table (mesa) for six departments and by municipality for all; and the
2007 to 2015 CDs by polling table. This module unpacks the installers with 7-Zip and innoextract, reads the tables,
and builds one package per election day, split by department, on top of the geography and registry of
`co.registraduria`.
"""

import re
import shutil
import struct
import subprocess
import time
import zipfile
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.co import registraduria as rnec
from ea.store import DATA

SOURCE = "co.registraduria_history"
RAW = rnec.SOURCE
PARSED = DATA / "parsed" / SOURCE
SCRATCH = DATA / "tmp" / SOURCE
DAYS = {
    1997: date(1997, 10, 26),
    2000: date(2000, 10, 29),
    2003: date(2003, 10, 26),
    2007: date(2007, 10, 28),
    2011: date(2011, 10, 30),
    2015: date(2015, 10, 25),
}
GOVERNOR, ASSEMBLY, MAYOR, COUNCIL, BOARD = rnec.GOVERNOR, rnec.ASSEMBLY, rnec.MAYOR, rnec.COUNCIL, rnec.BOARD
BLANK, NULL, UNMARKED, CAST = "996", "997", "998", "999"
SPECIALS = (BLANK, NULL, UNMARKED, CAST)
VOTES = {
    "corp": pl.String,
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
    "district": pl.String,
    "party": pl.String,
    "cand": pl.String,
    "party_code": pl.String,
    "party_name": pl.String,
    "cand_name": pl.String,
    "position": pl.Int32,
    "elected": pl.Boolean,
}
SEATS = {"corp": pl.String, "district": pl.String, "party": pl.String, "seats": pl.Int32}
PLACES = {"dep": pl.String, "mun": pl.String, "zona": pl.String, "puesto": pl.String, "name": pl.String}
NAMES = {"scheme": pl.String, "code": pl.String, "name": pl.String}


def url(year: int) -> str:
    return rnec.url(rnec.HISTORIC[year])


def _zip_check(path: Path) -> None:
    with path.open("rb") as file:
        if file.read(2) != b"PK":
            raise ValueError(f"{path}: not a zip archive")


def fetch() -> None:
    """Download each CD; the Observatorio's server often drops long transfers, so each is retried."""
    for year in DAYS:
        for attempt in range(8):
            try:
                artifact = raw.fetch(RAW, url(year), suffix=".zip", check=_zip_check)
            except httpx.HTTPError as error:
                print(f"{year}: attempt {attempt + 1}: {error!r}", flush=True)
                time.sleep(15 * (attempt + 1))
                continue
            print(f"{year}: {artifact.bytes:,} bytes", flush=True)
            break
        else:
            raise SystemExit(f"{year}: the download kept failing")


def _artifact(year: int) -> raw.Artifact:
    return raw.cached(RAW, url(year))


# Unpacking


def _seven(archive: Path, out: Path) -> None:
    subprocess.run(["7z", "x", "-y", f"-o{out}", str(archive)], check=True, capture_output=True)


def _unpack(year: int) -> Path:
    """Unpack a CD into the scratch folder: the ZIP, then every installer cabinet or MSI package inside it."""
    out = SCRATCH / str(year)
    done = out / ".done"
    if done.exists():
        return out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with zipfile.ZipFile(_artifact(year).path) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.flag_bits & 0x800 == 0:
                name = name.encode("cp437").decode("cp850")
            target = out / "zip" / name
            if info.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, 1 << 24)
    for index, cabinet in enumerate(sorted((out / "zip").rglob("*"))):
        lower = cabinet.name.lower()
        if not lower.endswith((".cab", ".msi")) or lower == "setup2.cab":
            continue
        folder = out / f"cab{index:02d}"
        _seven(cabinet, folder)
        inf = cabinet.parent / "setup.inf"
        if inf.exists():
            _rename(inf, folder)
    done.touch()
    return out


def _rename(inf: Path, folder: Path) -> None:
    """The Setup Wizard of Visual FoxPro 6 stores files as F0000001 and up; its setup.inf maps them to names."""
    for line in inf.read_text("latin-1").splitlines():
        found = re.match(r'"[^"]+"=\d+,(F\d+),.*?,[^,<]*<([^>]+)>', line)
        if not found:
            continue
        stored = next((p for p in folder.iterdir() if p.name.upper() == found.group(1)), None)
        if stored is not None:
            stored.rename(folder / found.group(2).replace("\\", "_"))


def _find(root: Path, name: str, within: str = "") -> Path:
    found = sorted(
        p for p in root.rglob("*") if p.is_file() and p.name.lower() == name.lower() and within in p.as_posix()
    )
    if not found:
        raise LookupError(f"{name} not found under {root}")
    return found[0]


# dBASE tables


def dbf(path: Path) -> pl.DataFrame:
    """Read a dBASE III or Visual FoxPro table as strings, skipping deleted records."""
    data = path.read_bytes()
    count, start, size = struct.unpack("<IHH", data[4:12])
    fields: list[tuple[str, str, int, int]] = []
    pos, offset = 32, 1
    while data[pos] != 0x0D:
        name = data[pos : pos + 11].split(b"\0")[0].decode("ascii")
        kind = chr(data[pos + 11])
        length = data[pos + 16]
        fields.append((name, kind, offset, length))
        offset += length
        pos += 32
    count = min(count, (len(data) - start) // size)
    records = np.frombuffer(data, dtype=np.uint8, count=count * size, offset=start).reshape(count, size)
    records = records[records[:, 0] != ord("*")]
    columns: dict[str, list[str] | np.ndarray] = {}
    for name, kind, at, length in fields:
        if kind in "0MGP" or name.startswith("_"):
            continue
        cells = np.ascontiguousarray(records[:, at : at + length]).view(f"S{length}").ravel()
        if cells.size and int(records[:, at : at + length].max(initial=0)) < 128:
            columns[name] = np.char.strip(cells.astype(f"U{length}"))
        else:
            columns[name] = [cell.decode("cp1252", "replace").strip() for cell in cells]
    return pl.DataFrame({name: pl.Series(name, values, pl.String) for name, values in columns.items()})


def _int(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().replace("", None).cast(pl.Int64, strict=False)


def _code(column: str, width: int) -> pl.Expr:
    return _int(column).cast(pl.String).str.zfill(width)


def _num(column: str) -> pl.Expr:
    return _int(column).cast(pl.String)


def _name(*columns: str) -> pl.Expr:
    return pl.concat_str([pl.col(c).fill_null("") for c in columns], separator=" ").str.replace_all(r"\s+", " ")


def _write(year: int, votes: pl.DataFrame, labels: pl.DataFrame, seats: pl.DataFrame, places: pl.DataFrame) -> None:
    folder = PARSED / str(DAYS[year])
    folder.mkdir(parents=True, exist_ok=True)
    votes = votes.select([pl.col(c).cast(t) for c, t in VOTES.items()]).filter(pl.col("votes") > 0)
    votes.write_parquet(folder / "votes.parquet")
    labels.select([pl.col(c).cast(t) for c, t in LABELS.items()]).unique().write_parquet(folder / "labels.parquet")
    seats.select([pl.col(c).cast(t) for c, t in SEATS.items()]).unique().write_parquet(folder / "seats.parquet")
    places.select([pl.col(c).cast(t) for c, t in PLACES.items()]).unique().write_parquet(folder / "places.parquet")
    print(f"{year}: {votes.height:,} vote rows, {votes['votes'].sum():,} votes", flush=True)


def _specials(frame: pl.DataFrame, codes: dict[str, tuple[int, int, int, int]]) -> pl.DataFrame:
    """Map each office's own codes for blank, null, unmarked, and total ballots to 996 to 999."""
    expr = pl.col("cand")
    for corp, (blank, null, unmarked, cast) in codes.items():
        for code, target in ((blank, BLANK), (null, NULL), (unmarked, UNMARKED), (cast, CAST)):
            expr = (
                pl.when((pl.col("corp") == corp) & (pl.col("cand") == str(code))).then(pl.lit(target)).otherwise(expr)
            )
    frame = frame.with_columns(cand=expr)
    return frame.with_columns(
        party=pl.when(pl.col("cand").is_in(SPECIALS)).then(pl.lit("0")).otherwise(pl.col("party"))
    )


# 1997 and 2000: results by municipality, with the lists of the old electoral system


def _coded(frame: pl.DataFrame, corp: str, specials: dict[str, str], lists: bool) -> pl.DataFrame:
    """Split a raw code into party and candidate: blank, null, unmarked, and total ballots, a list, or a candidate."""
    code = pl.col("code")
    special = code.replace_strict(specials, default=None)
    return frame.with_columns(
        corp=pl.lit(corp),
        cand=pl.when(special.is_not_null()).then(special).when(lists).then(pl.lit("0")).otherwise(code),
        party=pl.when(special.is_not_null()).then(pl.lit("0")).when(lists).then(code).otherwise(pl.col("party")),
    )


def _districted(frame: pl.DataFrame) -> pl.DataFrame:
    if "district" not in frame.columns:
        frame = frame.with_columns(district=pl.lit(None, pl.String))
    return frame.with_columns(
        district=pl.when(pl.col("corp").is_in([GOVERNOR, ASSEMBLY]))
        .then(pl.col("dep"))
        .when(pl.col("corp").is_in([MAYOR, COUNCIL]))
        .then(pl.col("dep") + ":" + pl.col("mun"))
        .otherwise(pl.col("district"))
    )


def _empty(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


def _seats(labels: pl.DataFrame) -> pl.DataFrame:
    lists = labels.filter(pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD]) & (pl.col("cand") != "0"))
    return lists.group_by("corp", "district", "party").agg(seats=pl.col("elected").sum().cast(pl.Int32))


def parse_1997() -> None:
    root = _unpack(1997)

    def table(name: str) -> pl.DataFrame:
        return dbf(_find(root, name, "CD 1997"))

    def place(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.with_columns(
            dep=_code("CODIDEPA", 2),
            mun=(_int("CODIMUNI") % 1000).cast(pl.String).str.zfill(3) if "CODIMUNI" in frame.columns else None,
        )

    parties = dict(table("PARTIDO.DBF").select(_num("CODIPART"), "NOMBPART").iter_rows())
    results = [
        ("resugobe.dbf", GOVERNOR, "CODICAND", "VOTOGOBE", (46, 47, 48, 49), False),
        ("RESUASAM.DBF", ASSEMBLY, "CODILIST", "VOTOASAM", (296, 297, 298, 299), True),
        ("RESUALCA.DBF", MAYOR, "CODICAND", "VOTOALCA", (996, 997, 998, 999), False),
        ("Resuconc.dbf", COUNCIL, "CODILIST", "VOTOCONC", (496, 497, 498, 499), True),
    ]
    heads = {
        GOVERNOR: place(table("gobernad.dbf")).select(
            "dep", mun=pl.lit(None, pl.String), code=_num("CODICAND"), party=_num("CODIPART")
        ),
        MAYOR: place(table("Alcalde.dbf")).select("dep", "mun", code=_num("CODICAND"), party=_num("CODIPART")),
    }
    votes = []
    for name, corp, code, column, specials, lists in results:
        frame = place(table(name)).select("dep", "mun", code=_num(code), votes=_int(column))
        if corp in heads:
            keys = ["dep", "code"] if corp == GOVERNOR else ["dep", "mun", "code"]
            frame = frame.join(heads[corp].select(*keys, "party"), on=keys, how="left").with_columns(
                party=pl.col("party").fill_null("0")
            )
        else:
            frame = frame.with_columns(party=pl.lit(None, pl.String))
        mapped = dict(zip((str(s) for s in specials), SPECIALS, strict=True))
        votes.append(_coded(frame, corp, mapped, lists))
    votes_frame = _districted(pl.concat(votes, how="diagonal_relaxed")).with_columns(
        zona=pl.lit(None, pl.String), puesto=pl.lit(None, pl.String), mesa=pl.lit(None, pl.String)
    )
    labels = []
    for name, corp, who in (("gobernad.dbf", GOVERNOR, "NOMBGOBE"), ("Alcalde.dbf", MAYOR, "NOMBALCA")):
        frame = place(table(name))
        labels.append(
            frame.select(
                "dep",
                "mun" if corp == MAYOR else pl.lit(None, pl.String).alias("mun"),
                corp=pl.lit(corp),
                party=_num("CODIPART"),
                cand=_num("CODICAND"),
                party_code=_num("CODIPART"),
                cand_name=pl.col(who),
                position=pl.lit(None, pl.Int32),
                elected=pl.col("ELEGIDO") == "T",
            )
        )
    for name, corp, who in (("LISTAASA.DBF", ASSEMBLY, "NOMBASAM"), ("listacon.dbf", COUNCIL, "NOMBCONC")):
        frame = place(table(name))
        members = frame.select(
            "dep",
            "mun" if corp == COUNCIL else pl.lit(None, pl.String).alias("mun"),
            corp=pl.lit(corp),
            party=_num("CODILIST"),
            cand=_num("SECULIST"),
            party_code=_num("CODIPART"),
            cand_name=pl.col(who),
            position=_int("SECULIST").cast(pl.Int32),
            elected=pl.col("ELEGIDO") == "T",
        )
        heads_of_lists = members.sort("position").unique(["dep", "mun", "party"], keep="first")
        labels.append(members)
        labels.append(heads_of_lists.with_columns(cand=pl.lit("0"), position=None, elected=pl.lit(False)))
    label_frame = _districted(pl.concat(labels, how="diagonal_relaxed")).with_columns(
        party_name=pl.col("party_code").replace_strict(parties, default=None)
    )
    towns = table("MUNICIPI.DBF")
    places = place(towns).select(
        "dep", "mun", zona=pl.lit(None, pl.String), puesto=pl.lit(None, pl.String), name="NOMBMUNI"
    )
    _write(1997, votes_frame, label_frame, _seats(label_frame), places)


SPECIAL_WORDS = {"BLANCO": BLANK, "BLANCOS": BLANK, "NULOS": NULL, "NO MARCADA": UNMARKED, "NO MARCADAS": UNMARKED}


def _worded(frame: pl.DataFrame) -> pl.Expr:
    """In 2000 the special rows are named: VOTOS BLANCO, VOTOS NULOS, TARJETAS NO MARCADA, and VOTOS TOTALES."""
    last = pl.col("APELLCAN").str.strip_chars()
    return (
        pl.when(last.is_in(list(SPECIAL_WORDS)))
        .then(last.replace_strict(SPECIAL_WORDS, default=None))
        .when((last == "TOTALES") | (pl.col("NOMCAN").str.strip_chars() == "TOTAL"))
        .then(pl.lit(CAST))
        .otherwise(None)
    )


def parse_2000() -> None:
    root = _unpack(2000)

    def table(name: str) -> pl.DataFrame:
        return dbf(_find(root, name, "cab"))

    def place(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.with_columns(dep=_code("CODEPA", 2), mun=_code("CODMUN", 3))

    votes = []
    for name, corp, column, lists in (
        ("GOBMUNI.DBF", GOVERNOR, "VOTOSCAN", False),
        ("ASAMUNI.DBF", ASSEMBLY, "VOTOSCAN", True),
        ("ALCAMUNI.DBF", MAYOR, "VOTOS", False),
        ("CONMUNI.DBF", COUNCIL, "VOTOS", True),
    ):
        frame = place(table(name))
        special = _worded(frame)
        votes.append(
            frame.select(
                "dep",
                "mun",
                corp=pl.lit(corp),
                party=pl.when(special.is_not_null())
                .then(pl.lit("0"))
                .when(lists)
                .then(_num("CODCAN"))
                .otherwise(_num("CODPAR")),
                cand=pl.when(special.is_not_null())
                .then(special)
                .when(lists)
                .then(pl.lit("0"))
                .otherwise(_num("CODCAN")),
                votes=_int(column),
            )
        )
    jal = table("JALPTOS.DBF")
    special = _worded(jal)
    votes.append(
        jal.select(
            dep=_code("CODEPA", 2),
            mun=_code("CODMUN", 3),
            zona=_code("ZONA", 2),
            puesto=_code("PTO", 2),
            corp=pl.lit(BOARD),
            party=pl.when(special.is_not_null()).then(pl.lit("0")).otherwise(_num("CODCAN")),
            cand=pl.when(special.is_not_null()).then(special).otherwise(pl.lit("0")),
            votes=_int("VOTOS"),
        ).with_columns(district=pl.col("dep") + ":" + pl.col("mun") + ":" + pl.col("zona"))
    )
    votes_frame = _districted(pl.concat(votes, how="diagonal_relaxed")).with_columns(mesa=pl.lit(None, pl.String))
    labels = []
    for name, corp in (("GOBMUNI.DBF", GOVERNOR), ("ALCAMUNI.DBF", MAYOR)):
        frame = place(table(name))
        frame = frame.filter(_worded(frame).is_null())
        labels.append(
            frame.select(
                "dep",
                "mun",
                corp=pl.lit(corp),
                party=_num("CODPAR"),
                cand=_num("CODCAN"),
                party_code=_num("CODPAR"),
                party_name="NOMPAR",
                cand_name=_name("NOMCAN", "APELLCAN"),
                position=pl.lit(None, pl.Int32),
                elected=pl.col("ELEGIDO") == "S" if "ELEGIDO" in frame.columns else pl.lit(False),
            ).unique(["dep", "mun", "cand"] if corp == MAYOR else ["dep", "cand"], keep="first")
        )
    labels[0] = labels[0].with_columns(mun=pl.lit(None, pl.String)).unique(["dep", "cand"])
    winners = table("ELEGOB.DBF").select(dep=_code("CODEPA", 2), cand=_num("CODCAN"), won=pl.lit(True))
    labels[0] = (
        labels[0]
        .join(winners, on=["dep", "cand"], how="left")
        .with_columns(elected=pl.col("won").fill_null(False))
        .drop("won")
    )
    for name, corp, zone in (
        ("ASAMELE.DBF", ASSEMBLY, None),
        ("CONCELE.DBF", COUNCIL, None),
        ("JALELE.DBF", BOARD, "ZONA"),
    ):
        frame = table(name).with_row_index("row")
        frame = frame.with_columns(dep=_code("CODEPA", 2), mun=_code("CODMUN", 3))
        if corp == ASSEMBLY:
            frame = frame.with_columns(mun=pl.lit(None, pl.String))
        keys = ["dep", "mun", "list"] if zone is None else ["dep", "mun", "zona", "list"]
        frame = frame.with_columns(
            list=_num("CODCAN"), zona=_code(zone, 2) if zone else pl.lit(None, pl.String)
        ).with_columns(position=(pl.col("row").rank("ordinal").over(keys)).cast(pl.Int32))
        members = frame.select(
            "dep",
            "mun",
            "zona",
            "position",
            corp=pl.lit(corp),
            party="list",
            cand=pl.col("position").cast(pl.String),
            party_code=_num("CODPAR"),
            party_name="NOMPAR",
            cand_name=_name("NOMCAN", "APELLCAN"),
            elected=pl.col("ELEGIDO") == "S",
        )
        labels.append(members)
        labels.append(
            members.sort("position")
            .unique(["dep", "mun", "zona", "party"], keep="first")
            .with_columns(cand=pl.lit("0"), position=None, elected=pl.lit(False))
        )
    label_frame = _districted(
        pl.concat(labels, how="diagonal_relaxed").with_columns(
            district=pl.when(pl.col("corp") == BOARD)
            .then(pl.col("dep") + ":" + pl.col("mun") + ":" + pl.col("zona"))
            .otherwise(None)
        )
    )
    towns = place(table("DIVISE~1.DBF")).select(
        "dep", "mun", zona=pl.lit(None, pl.String), puesto=pl.lit(None, pl.String), name="NOMMUN"
    )
    puestos = table("DIVIBOG.DBF").select(
        dep=pl.lit("16"), mun=pl.lit("001"), zona=_code("ZONA", 2), puesto=_code("PUESTO", 2), name="NOMPUES"
    )
    zones = table("DIVIBOG.DBF").select(
        dep=pl.lit("16"), mun=pl.lit("001"), zona=_code("ZONA", 2), puesto=pl.lit(None, pl.String), name="NOMZONA"
    )
    _write(2000, votes_frame, label_frame, _seats(label_frame), pl.concat([towns, puestos, zones]))


# 2003 to 2015: results by polling table where the CD has them, otherwise by municipality


def _normal(frame: pl.DataFrame) -> pl.DataFrame:
    """Codes as plain numbers; the blank, null, unmarked, and total rows (96 to 99 or 996 to 999) as 996 to 999."""
    frame = frame.with_columns(party=_num("party"), cand=_num("cand"))
    special = pl.col("cand").is_in(["96", "97", "98", "99", *SPECIALS]) & pl.col("party").is_in(
        ["0", "96", "97", "98", "99", *SPECIALS]
    )
    return frame.with_columns(
        cand=pl.when(special)
        .then(pl.col("cand").str.zfill(2).str.slice(-2).map_elements(lambda c: f"9{c}", pl.String))
        .otherwise(pl.col("cand")),
        party=pl.when(special).then(pl.lit("0")).otherwise(pl.col("party")),
    )


def _finest(corp: str, totals: pl.DataFrame, tables: pl.DataFrame | None) -> pl.DataFrame:
    """Per municipality (or local board), the polling-table rows when they add up to its totals, else the totals."""
    key = ["dep", "mun", "jal"] if corp == BOARD else ["dep", "mun"]
    totals = _normal(totals).with_columns(corp=pl.lit(corp))
    if tables is None or tables.is_empty():
        return totals
    tables = _normal(tables).with_columns(corp=pl.lit(corp))
    options = ["party", "cand"]
    left = totals.group_by(*key, *options).agg(pl.col("votes").sum())
    right = tables.group_by(*key, *options).agg(pl.col("votes").sum().alias("summed"))
    joined = left.join(right, on=[*key, *options], how="full", coalesce=True).with_columns(
        same=pl.col("votes").fill_null(0) == pl.col("summed").fill_null(0)
    )
    agree = joined.group_by(key).agg(pl.col("same").all(), pl.col("summed").is_not_null().any().alias("has"))
    fine = agree.filter(pl.col("same") & pl.col("has")).select(key)
    coarse = agree.filter(~(pl.col("same") & pl.col("has"))).select(key)
    print(f"  {corp}: {fine.height:,} places by polling table, {coarse.height:,} by their totals", flush=True)
    return pl.concat(
        [tables.join(fine, on=key, how="semi"), totals.join(coarse, on=key, how="semi")], how="diagonal_relaxed"
    )


def _modern_districts(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        district=pl.when(pl.col("corp").is_in([GOVERNOR, ASSEMBLY]))
        .then(pl.col("dep"))
        .when(pl.col("corp").is_in([MAYOR, COUNCIL]))
        .then(pl.col("dep") + ":" + pl.col("mun"))
        .otherwise(pl.col("dep") + ":" + pl.col("mun") + ":" + pl.col("jal"))
    )


CORPS_2003 = {"05": GOVERNOR, "06": ASSEMBLY, "08": MAYOR, "09": COUNCIL, "10": BOARD}
SHORT_2003 = {"GO": GOVERNOR, "AS": ASSEMBLY, "AL": MAYOR, "CO": COUNCIL, "JA": BOARD}


def parse_2003() -> None:
    root = _unpack(2003)

    def table(name: str) -> pl.DataFrame:
        return dbf(_find(root, f"DATOS_{name}.DBF", "cab"))

    mesas = table("MESAS").select(
        dep="CODI_DEP", mun="CODI_MUN", zona="CODI_ZON", puesto="CODI_PTO", mesa=_num("NUME_MES"), jal="CODI_JAL"
    )
    votes = []
    for corp, total, fine in (
        (GOVERNOR, "CAND_GOB", "CAND_GOM"),
        (ASSEMBLY, "CAND_ASA", "CAND_ASM"),
        (MAYOR, "CAND_ALC", "CAND_ALM"),
        (COUNCIL, "CAND_COC", "CAND_COM"),
        (BOARD, "CAND_JAL", "CAND_JAM"),
    ):
        totals = table(total).select(
            dep="CODI_DEP",
            mun="CODI_MUN",
            jal=pl.col("CODI_JAL") if corp == BOARD else pl.lit(None, pl.String),
            party="CODI_PAR",
            cand="CODI_CAN",
            votes=_int("VOTA_CAN"),
        )
        tables = table(fine).select(
            dep="CODI_DEP",
            mun="CODI_MUN",
            zona="CODI_ZON",
            puesto="CODI_PTO",
            mesa=_num("NUME_MES"),
            party="CODI_PAR",
            cand="CODI_CAN",
            votes=_int("VOTA_CAN"),
        )
        if corp == BOARD:
            tables = tables.join(mesas, on=["dep", "mun", "zona", "puesto", "mesa"], how="left")
        votes.append(_finest(corp, totals, tables))
    votes_frame = _modern_districts(pl.concat(votes, how="diagonal_relaxed"))
    listas = table("LISTAS").with_columns(corp=pl.col("CODI_COR").replace_strict(CORPS_2003, default=None))
    labels = _normal(
        listas.select(
            "corp",
            dep="CODI_DEP",
            mun="CODI_MUN",
            jal="CODI_JAL",
            party="CODI_PAR",
            cand="CODI_CAN",
            party_name="MOVI_POL",
            cand_name=_name("NOMB_CAN", "APEL_CAN"),
        )
    ).with_columns(party_code=pl.col("party"), position=_int("cand").cast(pl.Int32))
    labels = labels.with_columns(
        cand_name=pl.when((pl.col("cand") == "0") & pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD]))
        .then(None)
        .otherwise(pl.col("cand_name")),
        position=pl.when(pl.col("cand") == "0").then(None).otherwise(pl.col("position")),
    )
    winners = []
    for name in ("ELEG_ASA", "ELEG_CON", "ELEG_JAL"):
        frame = table(name)
        winners.append(
            _normal(
                frame.select(
                    corp=pl.col("CODI_COR").replace_strict(SHORT_2003, default=None),
                    dep="CODI_DEP",
                    mun=pl.col("CODI_MUN") if "CODI_MUN" in frame.columns else pl.lit("000"),
                    jal=pl.col("CODI_JAL") if "CODI_JAL" in frame.columns else pl.lit("00"),
                    party="CODI_PAR",
                    cand="CODI_CAN",
                )
            )
        )
    heads = _normal(
        table("ELE_MOVI")
        .filter(pl.col("CODI_COR").is_in(["GO", "AL"]))
        .select(
            corp=pl.col("CODI_COR").replace_strict(SHORT_2003, default=None),
            dep="CODI_DEP",
            mun=pl.when(pl.col("CODI_COR") == "GO").then(pl.lit("000")).otherwise(pl.col("CODI_MUN")),
            jal=pl.lit("00"),
            party="CODI_PAR",
            cand="CODI_CAN",
        )
    )
    won = pl.concat([*winners, heads]).unique().with_columns(won=pl.lit(True))
    labels = labels.join(won, on=["corp", "dep", "mun", "jal", "party", "cand"], how="left").with_columns(
        elected=pl.col("won").fill_null(False)
    )
    labels = _modern_districts(labels)
    seats = _modern_districts(
        pl.concat(
            [
                table(name).select(
                    corp=pl.col("CODI_COR").replace_strict(SHORT_2003, default=None),
                    dep="CODI_DEP",
                    mun=pl.col("CODI_MUN") if "CODI_MUN" in table(name).columns else pl.lit("000"),
                    jal=pl.lit("00"),
                    party=_num("CODI_PAR"),
                    seats=_int("CURU_PAR").cast(pl.Int32),
                )
                for name in ("ELEG_ASA", "ELEG_CON")
            ]
        )
    )
    board_seats = _seats(labels.filter(pl.col("corp") == BOARD).with_columns(elected=pl.col("elected")))
    places = pl.concat(
        [
            table("PUESTOS").select(
                dep="CODI_DEP", mun="CODI_MUN", zona="CODI_ZON", puesto="CODI_PTO", name="NOMB_PTO"
            ),
            table("MUNICIPIOS").select(
                dep="CODI_DEP",
                mun="CODI_MUN",
                zona=pl.lit(None, pl.String),
                puesto=pl.lit(None, pl.String),
                name="NOMB_MUN",
            ),
        ]
    )
    comunas = table("COMUNAS").select(
        scheme=pl.lit("co.comuna"),
        code=pl.concat_str("CODI_DEP", "CODI_MUN", "CODI_JAL", separator=":"),
        name="NOMB_JAL",
    )
    _write(2003, votes_frame, labels, pl.concat([seats.select(*SEATS), board_seats.select(*SEATS)]), places)
    comunas.write_parquet(PARSED / str(DAYS[2003]) / "names.parquet")


CORPS = {"05": GOVERNOR, "06": ASSEMBLY, "08": MAYOR, "09": COUNCIL, "10": BOARD}


def _place_key(frame: pl.DataFrame) -> pl.DataFrame:
    """Department-wide rows carry municipality 000 and no board; give every row a municipality and a board code."""
    return frame.with_columns(
        mun=pl.when(pl.col("corp").is_in([GOVERNOR, ASSEMBLY])).then(pl.lit("000")).otherwise(pl.col("mun")),
        jal=pl.when(pl.col("corp") == BOARD).then(pl.col("jal")).otherwise(pl.lit("00")),
    )


def _elected(curules: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The elected candidates and the seats of each list, from a table of seats (curules) with one row per winner.

    Contests the count did not reach carry rows with no votes for every option; those, and contests with more than
    one winner for a single seat, declare nothing.
    """
    frame = _place_key(
        _normal(
            curules.select(
                corp=pl.col("CODI_COR").replace_strict(CORPS, default=None),
                dep="CODI_DEP",
                mun=pl.col("CODI_MUN").replace("", "000"),
                jal=pl.col("CODI_JAL").replace("", "00"),
                party="CODI_PAR",
                cand="CODI_CAN",
                seats=_int("CURU_PAR").cast(pl.Int32),
                votes=_int("VOTA_PAR"),
            )
        )
    ).filter(~pl.col("cand").is_in(SPECIALS) & (pl.col("votes").fill_null(0) > 0))
    single = frame.filter(pl.col("corp").is_in([GOVERNOR, MAYOR]))
    single = single.filter(pl.len().over("corp", "dep", "mun", "jal") == 1)
    lists = frame.filter(pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD]))
    won = pl.concat([single, lists]).select("corp", "dep", "mun", "jal", "party", "cand").unique()
    seats = lists.group_by("corp", "dep", "mun", "jal", "party").agg(pl.col("seats").max())
    return won, seats


def parse_2007() -> None:
    root = _unpack(2007)

    def table(name: str) -> pl.DataFrame:
        return dbf(_find(root, name, "cab"))

    totals_all = table("tb_votacion.dbf").with_columns(corp=pl.col("CODI_COR").replace_strict(CORPS, default=None))
    votes = []
    for code, corp in CORPS.items():
        totals = totals_all.filter(pl.col("corp") == corp).select(
            dep="CODI_DEP",
            mun="CODI_MUN",
            jal=pl.col("CODI_JAL") if corp == BOARD else pl.lit(None, pl.String),
            party="CODI_PAR",
            cand="CODI_CAN",
            votes=_int("VOTA_CAN"),
        )
        tables = table(f"tb_votacionz{code}.dbf").select(
            dep="CODI_DEP",
            mun="CODI_MUN",
            jal=pl.col("CODI_JAL") if corp == BOARD else pl.lit(None, pl.String),
            zona="CODI_ZON",
            puesto="CODI_PTO",
            mesa=_num("NUME_MES"),
            party="CODI_PAR",
            cand="CODI_CAN",
            votes=_int("VOTA_CAN"),
        )
        if corp in (GOVERNOR, ASSEMBLY):
            totals = totals.filter(pl.col("mun") != "000")
        votes.append(_finest(corp, totals, tables))
    votes_frame = _modern_districts(pl.concat(votes, how="diagonal_relaxed"))
    labels = _labels(table("listas.dbf"))
    won, seats = _elected(table("tb_curules.dbf"))
    places, names = _places(table("PUESTOS.DBF"), table("municipios.dbf"), table("comunas.dbf"))
    _finish_labels(2007, votes_frame, labels, won, seats, places, names)


def _labels(listas: pl.DataFrame) -> pl.DataFrame:
    frame = _normal(
        listas.select(
            corp=pl.col("CODI_COR").replace_strict(CORPS, default=None),
            dep="CODI_DEP",
            mun=pl.col("CODI_MUN").replace("", "000"),
            jal=pl.col("CODI_JAL").replace("", "00"),
            party="CODI_PAR",
            cand="CODI_CAN",
            party_name="MOVI_POL",
            cand_name=_name("NOMB_CAN", "APEL_CAN"),
        ).filter(pl.col("corp").is_not_null())
    )
    lists = pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD])
    return _place_key(frame).with_columns(
        party_code=pl.col("party"),
        position=pl.when(lists & (pl.col("cand") != "0")).then(_int("cand").cast(pl.Int32)).otherwise(None),
        cand_name=pl.when(lists & (pl.col("cand") == "0")).then(None).otherwise(pl.col("cand_name")),
    )


def _places(puestos: pl.DataFrame, towns: pl.DataFrame, boards: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    places = pl.concat(
        [
            puestos.select(dep="CODI_DEP", mun="CODI_MUN", zona="CODI_ZON", puesto="CODI_PTO", name="NOMB_PTO"),
            towns.select(
                dep="CODI_DEP",
                mun="CODI_MUN",
                zona=pl.lit(None, pl.String),
                puesto=pl.lit(None, pl.String),
                name="NOMB_MUN",
            ),
        ]
    )
    names = boards.select(
        scheme=pl.lit("co.comuna"),
        code=pl.concat_str("CODI_DEP", "CODI_MUN", "CODI_JAL", separator=":"),
        name="NOMB_JAL",
    )
    return places, names


def _finish_labels(
    year: int,
    votes: pl.DataFrame,
    labels: pl.DataFrame,
    won: pl.DataFrame,
    seats: pl.DataFrame,
    places: pl.DataFrame,
    names: pl.DataFrame,
) -> None:
    keys = ["corp", "dep", "mun", "jal", "party", "cand"]
    labels = labels.join(won.with_columns(won=pl.lit(True)), on=keys, how="left").with_columns(
        elected=pl.col("won").fill_null(False)
    )
    missing = won.join(labels, on=keys, how="anti")
    if "cand_name" in won.columns and missing.height:
        # Members of closed lists appear only among the winners, with their names.
        lists = pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD])
        groups = labels.select(*keys[:-1], "party_name").unique(keys[:-1])
        added = (
            missing.filter(lists & pl.col("cand_name").is_not_null())
            .join(groups, on=keys[:-1], how="left")
            .with_columns(party_code=pl.col("party"), position=_int("cand").cast(pl.Int32), elected=pl.lit(True))
        )
        labels = pl.concat([labels, added], how="diagonal_relaxed")
        missing = won.join(labels, on=keys, how="anti")
    if missing.height:
        print(f"  {missing.height} winners are not on the lists: {missing.head(3).rows()}", flush=True)
    _write(year, votes, _modern_districts(labels), _modern_districts(seats).select(*SEATS), places)
    names.write_parquet(PARSED / str(DAYS[year]) / "names.parquet")


# 2011 and 2015: an Inno Setup installer around a SQLite database with one table of polling-table rows per department

SQL_CORPS = {1: GOVERNOR, 2: ASSEMBLY, 3: MAYOR, 4: COUNCIL, 5: BOARD}


def _database(year: int) -> Path:
    out = _unpack(year) / "inno"
    found = out / "app" / "db" / "Estadisticas.db"
    if not found.exists():
        installer = next(p for p in (_unpack(year) / "zip").rglob("*.exe") if p.name.lower().startswith("setup"))
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["innoextract", "-q", "-I", "app/db/Estadisticas.db", "-d", str(out), str(installer)], check=True
        )
    return found


def parse_sqlite(year: int) -> None:
    import sqlite3

    connection = sqlite3.connect(f"file:{_database(year)}?mode=ro", uri=True)
    tables = [
        name
        for (name,) in connection.execute(
            "select name from sqlite_master where type = 'table' and name like 'votacion\\_%' escape '\\' order by name"
        )
    ]
    frames = []
    for name in tables:
        frame = pl.read_database(
            f"select corp, depto, mpio, zona, desc_zona, puesto, desc_puesto, desc_mpio, mesa, partido, desc_partido,"
            f" candidato, desc_candidato, votos, comuna from {name}",
            connection,
            schema_overrides={"puesto": pl.String, "desc_partido": pl.String, "desc_candidato": pl.String},
        )
        if frame.height:
            frames.append(frame)
    raw_rows = pl.concat(frames, how="vertical_relaxed").with_columns(
        corp=pl.col("corp").replace_strict(SQL_CORPS, default=None),
        dep=pl.col("depto").cast(pl.String).str.zfill(2),
        mun=pl.col("mpio").cast(pl.String).str.zfill(3),
        jal=pl.when(pl.col("comuna") >= 0).then(pl.col("comuna").cast(pl.String).str.zfill(2)).otherwise(None),
        zona=pl.col("zona").cast(pl.String).str.zfill(2),
        puesto=pl.col("puesto").str.strip_chars().str.zfill(2),
        mesa=pl.col("mesa").cast(pl.String),
        party=pl.col("partido").cast(pl.String),
        cand=pl.col("candidato").cast(pl.String),
        votes=pl.col("votos").cast(pl.Int64),
    )
    votes = _normal(raw_rows.select("corp", "dep", "mun", "jal", "zona", "puesto", "mesa", "party", "cand", "votes"))
    votes_frame = _modern_districts(votes)
    named = _normal(
        raw_rows.select(
            "corp",
            "dep",
            "mun",
            "jal",
            "party",
            "cand",
            party_name=pl.col("desc_partido").str.strip_chars(),
            cand_name=pl.col("desc_candidato").str.strip_chars().str.strip_chars_end(" ."),
        ).unique(["corp", "dep", "mun", "jal", "party", "cand"])
    ).filter(~pl.col("cand").is_in(SPECIALS))
    lists = pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD])
    labels = _place_key(named).with_columns(
        party_code=pl.col("party"),
        position=pl.when(lists & (pl.col("cand") != "0")).then(_int("cand").cast(pl.Int32)).otherwise(None),
        cand_name=pl.when(lists & (pl.col("cand") == "0")).then(None).otherwise(pl.col("cand_name")),
    )
    elegidos = pl.read_database(
        "select corporacion, departamento, municipio, comuna, partido, candidato, nombre from elegidos", connection
    )
    won = _place_key(
        _normal(
            elegidos.select(
                corp=pl.col("corporacion").replace_strict(SQL_CORPS, default=None),
                dep=pl.col("departamento").cast(pl.String).str.zfill(2),
                mun=pl.when(pl.col("municipio") >= 0)
                .then(pl.col("municipio").cast(pl.String).str.zfill(3))
                .otherwise(pl.lit("000")),
                jal=pl.when(pl.col("comuna") >= 0)
                .then(pl.col("comuna").cast(pl.String).str.zfill(2))
                .otherwise(pl.lit("00")),
                party=pl.col("partido").cast(pl.String),
                cand=pl.when(pl.col("candidato") // 1000 == pl.col("partido"))
                .then(pl.col("candidato") % 1000)
                .otherwise(pl.col("candidato"))
                .cast(pl.String),
                cand_name=pl.col("nombre").str.strip_chars(),
            )
        )
    ).unique(["corp", "dep", "mun", "jal", "party", "cand"])
    won = won.filter(~pl.col("cand").is_in(SPECIALS))
    single = won.filter(pl.col("corp").is_in([GOVERNOR, MAYOR]))
    won = pl.concat(
        [
            single.filter(pl.len().over("corp", "dep", "mun", "jal") == 1),
            won.filter(pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD]) & (pl.col("cand") != "0")),
        ]
    )
    seats = (
        won.filter(pl.col("corp").is_in([ASSEMBLY, COUNCIL, BOARD]))
        .group_by("corp", "dep", "mun", "jal", "party")
        .agg(seats=pl.len().cast(pl.Int32))
    )
    places = pl.concat(
        [
            raw_rows.select("dep", "mun", "zona", "puesto", name=pl.col("desc_puesto").str.strip_chars()),
            raw_rows.select(
                "dep",
                "mun",
                zona=pl.lit(None, pl.String),
                puesto=pl.lit(None, pl.String),
                name=pl.col("desc_mpio").str.strip_chars(),
            ),
        ]
    ).unique(["dep", "mun", "zona", "puesto"])
    boards = pl.read_database(
        "select distinct depto, mpio, comuna, desc_comuna from x_resum_comuna_01 union "
        + " union ".join(
            f"select distinct depto, mpio, comuna, desc_comuna from {t}"
            for (t,) in connection.execute(
                "select name from sqlite_master where type = 'table' and name like 'x_resum_comuna_%'"
            )
        ),
        connection,
    )
    names = boards.select(
        scheme=pl.lit("co.comuna"),
        code=pl.concat_str(
            pl.col("depto").cast(pl.String).str.zfill(2),
            pl.col("mpio").cast(pl.String).str.zfill(3),
            pl.col("comuna").cast(pl.String).str.zfill(2),
            separator=":",
        ),
        name=pl.col("desc_comuna").cast(pl.String).str.strip_chars(),
    )
    _finish_labels(year, votes_frame, labels, won, seats, places, names)


def parse() -> None:
    parse_1997()
    parse_2000()
    parse_2003()
    parse_2007()
    for year in (2011, 2015):
        parse_sqlite(year)


# Packages


def key(kind: str, *parts: object) -> str:
    return package.Package(SOURCE, SOURCE, "").id(kind, *parts)


COLOMBIA = rnec.COLOMBIA
COUNTRY = rnec.COUNTRY
SOURCE_ID = key("source", SOURCE)
BASE_GEO, BASE_REGISTRY = f"{rnec.SOURCE}/geo", f"{rnec.SOURCE}/registry"
GEO, REGISTRY = f"{SOURCE}/geo", f"{SOURCE}/registry"
SINGLE = {GOVERNOR, MAYOR}
LISTS = {ASSEMBLY, COUNCIL, BOARD}
REFORM = date(2003, 7, 3)
"""Acto Legislativo 01 de 2003: one list per party, D'Hondt with a threshold, and an optional preference vote."""
MEASURES = {NULL: 5, UNMARKED: 6, CAST: 3}
SYSTEMS = {
    "hare": (
        {"formula": "hare"},
        (
            "Before the 2003 political reform, each party could run several lists in a district. Seats went to the "
            "lists by the electoral quota (cuociente electoral, the valid votes divided by the seats) and the largest "
            "remainders (residuos), and each list was closed: its seats went to its candidates in list order."
        ),
    ),
    "plurality": (
        {"formula": "fptp"},
        "The candidate with the most votes is elected.",
    ),
}
METHOD = (
    "From the statistics CD the Registraduría Nacional del Estado Civil issued after this election, as republished by "
    "its Observatorio. {grain} The blank vote (voto en blanco) is stored as an option; null votes (votos nulos) are "
    "stored as invalid ballots, unmarked ballots (tarjetas no marcadas) as blank ballots, and the CD's total of "
    "ballots, where it prints one, as ballots cast. Rows with zero votes are left out."
)
GRAIN = {
    1997: "The CD gives results by municipality.",
    2000: "The CD gives results by municipality, and Bogotá's local boards by polling place.",
    2003: (
        "The CD gives results by polling table (mesa) for some departments and by municipality (or local board) for "
        "all; a municipality is stored by polling table where its tables add up to its totals, otherwise by its "
        "totals."
    ),
    2007: (
        "The CD gives results by polling table (mesa) and by municipality; a municipality is stored by polling table "
        "where its tables add up to its totals, otherwise by its totals."
    ),
    2011: "The CD gives results by polling table (mesa).",
    2015: "The CD gives results by polling table (mesa).",
}
LIST_NOTE = (
    " Votes for a list alone are stored on a legend line under the list and preference votes on candidate lines; a "
    "list without preference votes holds its votes itself."
)
OLD_LIST_NOTE = (
    " Each list is a ballot line of its own, labeled by its head candidate, and its candidates are listed in order."
)


def _base(name: str) -> pl.DataFrame:
    return pl.read_parquet(package.folder(name) / "build" / f"{'area' if name == BASE_GEO else 'body'}.parquet")


def _ids(name: str, table: str) -> set[str]:
    path = package.folder(name) / "build" / f"{table}.parquet"
    return set(pl.read_parquet(path)["id"]) if path.exists() else set()


def _names(name: str) -> dict[str, str]:
    frame = pl.read_parquet(package.folder(name) / "build" / "name.parquet")
    return dict(frame.filter(pl.col("is_preferred")).select("entity_id", "text").iter_rows())


def _days() -> list[int]:
    return [year for year, day in DAYS.items() if (PARSED / str(day) / "votes.parquet").exists()]


def _read(year: int, name: str) -> pl.DataFrame:
    return pl.read_parquet(PARSED / str(DAYS[year]) / f"{name}.parquet")


def _parts(year: int) -> list[str]:
    return sorted(_read(year, "votes")["dep"].unique())


def packages() -> list[str]:
    return [GEO, REGISTRY, *(k for y in _days() for k in [_key(y), *(_key(y, part) for part in _parts(y))])]


def _key(year: int, part: str | None = None) -> str:
    return f"{SOURCE}/{DAYS[year]}" + (f"-{part}" if part else "")


def _boards() -> pl.DataFrame:
    """Every local-board district (comuna or corregimiento) the elections use, with its best name."""
    frames = []
    for year in _days():
        districts = _read(year, "votes").filter(pl.col("corp") == BOARD).select(code="district").unique()
        names_path = PARSED / str(DAYS[year]) / "names.parquet"
        names = pl.read_parquet(names_path).select("code", "name") if names_path.exists() else None
        if names is not None:
            districts = districts.join(names.unique("code"), on="code", how="left")
        else:
            districts = districts.with_columns(name=pl.lit(None, pl.String))
        frames.append(districts.with_columns(year=pl.lit(year)))
    frame = pl.concat(frames).sort("year", descending=True)
    named = frame.filter(pl.col("name").is_not_null() & (pl.col("name") != "")).unique("code", keep="first")
    return frame.select("code").unique().join(named.select("code", "name"), on="code", how="left").sort("code")


def _board_label(code: str, text: str | None) -> str:
    comuna = code.split(":")[2]
    if not text:
        return f"Comuna {comuna}"
    text = re.sub(r"\s*\(.*\)\s*$", "", rnec._clean(text))
    return rnec.title(text) or f"Comuna {comuna}"


def board_area(code: str, known: set[str]) -> str:
    own = rnec.area("co.comuna", code)
    return own if own in known else key("area", "co.comuna", code)


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The local-board districts (comunas and corregimientos) of Colombia's territorial elections of 1997 to 2015 that "
        "the Registraduría's later elections no longer use",
        depends=[BASE_GEO],
    )
    known = _ids(BASE_GEO, "area")
    for code, text in _boards().iter_rows():
        if rnec.area("co.comuna", code) in known:
            continue
        dep, mun, _ = code.split(":")
        town = rnec.area("co.municipio", f"{dep}:{mun}")
        if town not in known:
            continue
        id = key("area", "co.comuna", code)
        p.add("area", id=id, scheme_code="co.comuna")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=town)
        p.name(id, _board_label(code, text), "es", "official")
    return p


def _fold(text: str) -> str:
    return rnec._fold(text)


def _party_names() -> dict[str, str]:
    """Each party's name as the CDs print it, keyed by its folded form."""
    names: dict[str, str] = {}
    for year in _days():
        for text in _read(year, "labels")["party_name"].drop_nulls().unique().sort():
            text = rnec._clean(text)
            folded = _fold(text)
            if folded and folded not in names:
                names[folded] = text
    return names


def party(name: str | None) -> str | None:
    folded = _fold(rnec._clean(name)) if name else ""
    return key("party", folded) if folded else None


def _contests(year: int) -> pl.DataFrame:
    return _read(year, "votes").select("corp", "district", "dep").unique()


def body_id(corp: str, district: str, known: set[str]) -> str | None:
    if corp not in LISTS:
        return None
    own = rnec.key("body", corp, district)
    return own if own in known else key("body", corp, district)


def office_id(corp: str, district: str, known: set[str]) -> str | None:
    if corp not in SINGLE:
        return None
    own = rnec.key("office", corp, district)
    return own if own in known else key("office", corp, district)


def system(year: int, corp: str) -> str:
    if DAYS[year] > REFORM:
        return rnec.system("plurality" if corp in SINGLE else "lists")
    return key("electoral_system", "plurality" if corp in SINGLE else "hare")


class Places:
    """Names of departments, municipalities, and local boards, from the base geography and this pipeline's own."""

    def __init__(self) -> None:
        self.names = _names(BASE_GEO)
        self.boards = dict(_boards().iter_rows())
        self.known = _ids(BASE_GEO, "area")

    def department(self, dep: str) -> str:
        return rnec.DEPARTMENTS.get(dep) or self.names.get(rnec.area("co.departamento", dep), dep)

    def town(self, dep: str, mun: str) -> str:
        return self.names.get(rnec.area("co.municipio", f"{dep}:{mun}"), f"Municipio {mun}")

    def district(self, corp: str, district: str) -> str:
        parts = district.split(":")
        dep = self.department(parts[0])
        if len(parts) == 1:
            return dep
        town = f"{self.town(parts[0], parts[1])} ({dep})"
        if len(parts) == 2:
            return town
        return f"{_board_label(district, self.boards.get(district))}, {town}"

    def area(self, corp: str, district: str) -> str:
        parts = district.split(":")
        if len(parts) == 1:
            id = rnec.area("co.departamento", district)
        elif len(parts) == 2:
            id = rnec.area("co.municipio", district)
        else:
            id = board_area(district, self.known)
            if (
                id != rnec.area("co.comuna", district)
                and rnec.area("co.municipio", ":".join(parts[:2])) not in self.known
            ):
                id = rnec.area("co.municipio", ":".join(parts[:2]))
        return id if id in self.known or corp == BOARD else COUNTRY


NOUNS = {
    ASSEMBLY: "Asamblea Departamental de {}",
    COUNCIL: "Concejo Municipal de {}",
    BOARD: "Junta Administradora Local de {}",
    GOVERNOR: "Gobernador de {}",
    MAYOR: "Alcalde de {}",
}


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The statistics CDs of Colombia's territorial elections of 1997 to 2015 as a source, with the parties, "
        "movements, councils, local boards, and executive offices they name and the electoral systems before 2003",
        depends=[GEO, BASE_GEO, BASE_REGISTRY],
    )
    p.external.add(COLOMBIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Registraduría Nacional del Estado Civil",
        title="Estadísticas electorales: elecciones territoriales de 1997 a 2015 (CD)",
        url=rnec.CATALOG,
        license=rnec.LICENSE,
        license_url="https://www.registraduria.gov.co/Politicas-de-privacidad.html",
        attribution="Registraduría Nacional del Estado Civil, Observatorio en Democracia y Asuntos Electorales",
    )
    for year in _days():
        p.artifact(_artifact(year), SOURCE_ID)
    known_bodies, known_offices = _ids(BASE_REGISTRY, "body"), _ids(BASE_REGISTRY, "office")
    places = Places()
    seen: set[str] = set()
    for year in _days():
        for corp, district, _ in _contests(year).sort("corp", "district").iter_rows():
            id = body_id(corp, district, known_bodies) or office_id(corp, district, known_offices)
            if id is None or id in seen or id in known_bodies or id in known_offices:
                continue
            seen.add(id)
            if corp in LISTS:
                p.add("body", id=id, polity_id=COLOMBIA, kind="council")
            else:
                p.add("office", id=id, polity_id=COLOMBIA, kind="governor" if corp == GOVERNOR else "mayor")
            p.name(id, NOUNS[corp].format(places.district(corp, district)), "es", "official")
    for name, (rules, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add("electoral_system", id=id, polity_id=COLOMBIA, rules=rules, rules_version=1, valid_to=REFORM)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for folded, text in sorted(_party_names().items()):
        id = key("party", folded)
        p.add("party", id=id, kind="party", polity_id=COLOMBIA)
        p.name(id, text, "es", "official")
    return p


ELECTION = ("{year} Colombian regional and local elections", "Elecciones territoriales de {year}")
CONTESTS = {
    GOVERNOR: "Gobernación de {}",
    ASSEMBLY: "Asamblea de {}",
    MAYOR: "Alcaldía de {}",
    COUNCIL: "Concejo de {}",
    BOARD: "Junta Administradora Local de {}",
}


def election_id(year: int) -> str:
    return key("election", DAYS[year], "local")


def election_day(year: int, part: str | None = None) -> Package:
    """One package per election day: a base package with the election, and one per department with its contests."""
    day = DAYS[year]
    depends = [GEO, REGISTRY, BASE_GEO, BASE_REGISTRY] + ([_key(year)] if part else [])
    summary = f"Colombia's territorial elections of {day:%-d %B %Y} from the Registraduría's statistics CD"
    if part:
        summary += f", {rnec.DEPARTMENTS.get(part, part)}"
    p = Package(_key(year, part), SOURCE, summary, depends)
    p.external.add(COLOMBIA)
    p.uses(_artifact(year))
    election = election_id(year)
    if part is None:
        p.add("election", id=election, polity_id=COLOMBIA, kind="local", date_from=day, date_to=day, status="held")
        p.name(election, ELECTION[0].format(year=year), "en", "official")
        p.name(election, ELECTION[1].format(year=year), "es", "native", False)
        p.add("slug", path=f"co/{day}-local", entity_id=election, is_current=True)
        return p
    votes = _read(year, "votes").filter(pl.col("dep") == part)
    labels = _read(year, "labels").filter(pl.col("district").str.slice(0, 2) == part)
    seats = _read(year, "seats").filter(pl.col("district").str.slice(0, 2) == part)
    places = Places()
    known_bodies, known_offices = _ids(BASE_REGISTRY, "body"), _ids(BASE_REGISTRY, "office")
    known_areas = places.known | _ids(GEO, "area")
    method = METHOD.format(grain=GRAIN[year])
    options = votes.group_by("corp", "district", "party", "cand").agg(pl.col("votes").sum())
    names = {
        (c, d, pa, ca): (pn, cn, pc, pos, el)
        for c, d, pa, ca, pn, cn, pc, pos, el in labels.select(
            "corp", "district", "party", "cand", "party_name", "cand_name", "party_code", "position", "elected"
        ).iter_rows()
    }
    groups: dict[tuple[str, str, str], str] = {}
    for c, d, pa, pn in (
        labels.filter(pl.col("party_name").is_not_null()).select("corp", "district", "party", "party_name").iter_rows()
    ):
        groups.setdefault((c, d, pa), pn)
    seat_map = {(c, d, pa): n for c, d, pa, n in seats.iter_rows()}
    mapping: list[dict[str, object]] = []
    sets: dict[str, str] = {}
    paths: set[str] = set()
    base = f"co/{day}-local"
    for corp, district in options.select("corp", "district").unique().sort("corp", "district").iter_rows():
        ck = f"{corp}:{district}"
        contest = key("contest", day, ck)
        place = places.area(corp, district)
        if place not in known_areas:
            place = places.area(MAYOR, ":".join(district.split(":")[:2]))
        text = CONTESTS[corp].format(places.district(corp, district))
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office" if corp in SINGLE else "list_allocation",
            office_id=office_id(corp, district, known_offices),
            body_id=body_id(corp, district, known_bodies),
            district_area_id=place,
            magnitude=1 if corp in SINGLE else None,
            round_number=1,
            electoral_system_id=system(year, corp),
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
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        note = method + ((LIST_NOTE if DAYS[year] > REFORM else OLD_LIST_NOTE) if corp in LISTS else "")
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        here = options.filter((pl.col("corp") == corp) & (pl.col("district") == district))
        voted = {(pa, ca) for pa, ca in here.select("party", "cand").iter_rows()}
        listed = {(pa, ca) for (c, d, pa, ca) in names if c == corp and d == district}
        pairs = sorted(voted | listed, key=lambda pair: (int(pair[0]), int(pair[1])))
        outcomes = _contest(p, year, corp, district, ck, result_set, pairs, voted, names, groups, seat_map, mapping)
        for row in outcomes:
            p.add("outcome", result_set_id=result_set, **row)
    units = _units(p, year, votes, known_areas)
    frame = (
        votes.with_columns(ck=pl.concat_str("corp", "district", separator=":"), code=_unit_code(year))
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


def _contest(
    p: Package,
    year: int,
    corp: str,
    district: str,
    ck: str,
    result_set: str,
    pairs: list[tuple[str, str]],
    voted: set[tuple[str, str]],
    names: dict[tuple[str, str, str, str], tuple[str | None, str | None, str | None, int | None, bool | None]],
    groups: dict[tuple[str, str, str], str],
    seats: dict[tuple[str, str, str], int],
    mapping: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Add one contest's options and candidacies, map its codes to options and measures, and return its outcomes."""
    day = DAYS[year]
    contest = key("contest", day, ck)
    outcomes: list[dict[str, object]] = []
    old = DAYS[year] < REFORM
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
        head = names.get((corp, district, pa, "0"))
        own = names.get((corp, district, pa, ca))
        group = (head[0] if head else None) or (own[0] if own else None) or groups.get((corp, district, pa))
        party_id = party(group)
        if corp in SINGLE:
            option = key("option", day, ck, pa, ca)
            label = rnec.title(own[1] if own and own[1] else None) or rnec.title(group) or f"Candidato {ca}"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_position=int(ca) or None,
                ballot_label=label,
            )
            _person(p, day, ck, pa, ca, option, label, "head", None, None)
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": option, "measure": 1})
            if own and own[4]:
                outcomes.append({"option_id": option, "kind": "elected"})
            continue
        if pa not in lists:
            lists[pa] = key("option", day, ck, pa, "list")
            if old:
                first = names.get((corp, district, pa, "1"))
                leader = rnec.title(first[1]) if first and first[1] else ""
                label = f"{leader} ({rnec.title(group)})" if leader and group else leader or group or f"Lista {pa}"
            else:
                label = group or f"Lista {pa}"
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
            mapping.append({"ck": ck, "party": pa, "cand": ca, "option_id": lists[pa], "measure": 1})
            continue
        if ca == "0":
            option = key("option", day, ck, pa, ca)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="legend",
                parent_option_id=lists[pa],
                party_id=party_id,
                ballot_label=f"{group or f'Lista {pa}'} (solo por la lista)",
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
        count = seats.get((corp, district, pa))
        if count:
            outcomes.append({"option_id": option, "kind": "won", "seats": count})
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


def _unit_code(year: int) -> pl.Expr:
    """A unit's code: the polling table, else the polling place, else the local board, else the municipality."""
    day = pl.lit(str(DAYS[year]))
    town = pl.concat_str(day, "dep", "mun", separator=":")
    return (
        pl.when(pl.col("mesa").is_not_null())
        .then(pl.concat_str(town, "zona", "puesto", "mesa", separator=":"))
        .when(pl.col("puesto").is_not_null())
        .then(pl.concat_str(town, "zona", "puesto", separator=":"))
        .when((pl.col("corp") == BOARD) & (pl.col("district").str.count_matches(":") == 2))
        .then(pl.concat_str(day, "district", separator=":"))
        .otherwise(town)
    )


def _units(p: Package, year: int, votes: pl.DataFrame, known: set[str]) -> pl.DataFrame:
    day = DAYS[year]
    places = _read(year, "places")
    puesto_names = {
        (d, m, z, pu): n for d, m, z, pu, n in places.filter(pl.col("puesto").is_not_null()).iter_rows() if n
    }
    frame = (
        votes.with_columns(code=_unit_code(year))
        .select("code", "corp", "district", "dep", "mun", "zona", "puesto", "mesa")
        .unique("code")
        .sort("code")
    )
    rows: list[dict[str, object]] = []
    added: set[str] = set()
    codes, ids = [], []

    def unit(scheme: str, code: str, relation: str, area: str, parent: str | None) -> str:
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
                    "kind": "standard",
                }
            )
        return id

    for code, corp, district, dep, mun, zona, puesto, mesa in frame.iter_rows():
        town = rnec.area("co.municipio", f"{dep}:{mun}")
        if town not in known:
            town = rnec.area("co.departamento", dep) if rnec.area("co.departamento", dep) in known else COUNTRY
        if puesto is not None:
            parent_code = f"{day}:{dep}:{mun}:{zona}:{puesto}"
            parent = unit("co.puesto", parent_code, "within", town, None)
            id = parent if mesa is None else unit("co.mesa", code, "within", town, parent)
        elif corp == BOARD and code.count(":") == 3:
            area = board_area(district, known)
            id = unit("co.comuna", code, "equals" if area in known else "within", area if area in known else town, None)
        else:
            id = unit(
                "co.municipio",
                code,
                "equals" if town == rnec.area("co.municipio", f"{dep}:{mun}") else "within",
                town,
                None,
            )
        codes.append(code)
        ids.append(id)
    for (dep, mun, zona, puesto), text in puesto_names.items():
        parent_code = f"{day}:{dep}:{mun}:{zona}:{puesto}"
        if parent_code in added:
            p.name(key("unit", "co.puesto", parent_code), rnec.title(text), "es", "official")
    p.frame("reporting_unit", pl.DataFrame(rows, infer_schema_length=None))
    return pl.DataFrame({"code": codes, "unit_id": ids})


def build() -> None:
    geo().write()
    registry().write()
    for year in _days():
        election_day(year).write()
        for part in _parts(year):
            election_day(year, part).write()


def emit() -> None:
    package.run("load", packages())
