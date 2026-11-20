import io
import os
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapely

from ea import package, raw
from ea.changeset import slug
from ea.names import display
from ea.package import Draft
from ea.sources.pl.kbw_sejm import ITEMS
from ea.sources.pl.prg import VOIVODESHIPS
from ea.store import connect

SOURCE = "pl.kbw_sejmik"
KEY = f"{SOURCE}/all"
VERSION = "1"
SUMMARY = (
    "Voivodeship assembly elections of 2002-2024 by constituency, gmina, and polling district, with committees, "
    "candidates, declared seats, elected members, and each constituency's outline, from the Krajowe Biuro "
    "Wyborcze's data files"
)
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze: wybory do sejmików województw"
PAGE = "https://danewyborcze.kbw.gov.pl/"
BASE = "https://danewyborcze.kbw.gov.pl/dane"
PARSED = raw.parsed(SOURCE)
ELECTIONS = {
    2002: date(2002, 10, 27),
    2006: date(2006, 11, 12),
    2010: date(2010, 11, 21),
    2014: date(2014, 11, 16),
    2018: date(2018, 10, 21),
    2024: date(2024, 4, 7),
}
FILES = {
    2002: "2002/2002-kand-rady.xlsx",
    2006: "2006/2006-kand-rady-new.xlsx",
    2010: "2010/samo2010/2010-kand-rady.xlsx",
    2014: "2014/samorzad/2014-kand-rady.xlsx",
    2018: "2018/samorzad/2018-kand-rady.xlsx",
    2024: "2024/samorzad/kandydaci_sejmiki_wojewodztw_csv.zip",
}
UNIT_FILES = {
    2006: "2006/sejmik/2006.zip",
    2010: "2010/sejmik/2010-sejmiki-po-obwodach.xls",
    2014: "2014/samorzad/1461141888_eff5f2b2b2ba3a9d3c47ea6c8e600e98.zip",
    2018: "2018/samorzad/2018-sejmiki.zip",
    2024: "2024/samorzad/protokoly_po_obwodach_sejmik_wojewodztwa_csv.zip",
}
UNIT_MEMBERS = {2006: "2006-sejkomobw.xls", 2014: ".xls", 2018: "po-obwodach.xlsx", 2024: ".csv"}
UNIT_COLUMNS = {
    2006: ("TERYT\ngminy", "Okręg", "Nr\nobw.", "Upr.", "Odd.", "Ważne"),
    2010: ("TERYT_2", "Numer okręgu", "Nr obw.", "Liczba wyborców", "Liczba głosów oddanych", "Liczba głosów ważnych"),
    2014: ("Teryt", "Nr\nokr.", "Nr obw", "Liczba wyborców", "Karty wyjęte z urny", "Głosy ważne"),
    2018: (
        "TERYT\ngminy",
        "Nr okr.",
        "Nr obw.",
        "Liczba wyborców uprawnionych do głosowania",
        "Liczba kart wyjętych z urny",
        "Liczba głosów ważnych",
    ),
    2024: (
        "Teryt Gminy",
        "Nr okręgu",
        "Nr komisji",
        "Liczba wyborców uprawnionych do głosowania",
        "Liczba kart wyjętych z urny",
        "Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów",
    ),
}
COLUMNS = {
    2002: {
        "unit": "Szczebel",
        "woj": "TERYT",
        "okr": "Okręg",
        "list": "Nr listy",
        "committee": "Komitet",
        "pos": "Nr pozycji",
        "votes": "Głosy",
        "seat": "Mandat",
        "sex": "Płeć",
        "family": "Nazwisko",
    },
    2006: {
        "unit": "Szczebel",
        "woj": "TERYT",
        "okr": "Okręg",
        "list": "Lista",
        "committee": "Komitet",
        "pos": "Pozycja",
        "votes": "Gł. na kand.",
        "seat": "Mandat",
        "sex": "Płeć",
        "family": "Nazwisko",
    },
    2010: {
        "unit": "Szczebel",
        "woj": "TERYT",
        "okr": "Okręg",
        "list": "Nr listy",
        "committee": "Komitet",
        "pos": "Pozycja",
        "votes": "L. głosów",
        "seat": "Wybrany",
        "sex": "Plec",
        "family": "Nazwisko",
    },
    2014: {
        "unit": "Szczebel",
        "woj": "TERYT",
        "okr": "Nr okr.",
        "list": "Nr listy",
        "committee": "Komitet",
        "pos": "Poz.",
        "votes": "Głosy",
        "seat": "Mandat",
        "sex": "Płeć",
        "family": "Nazwisko",
    },
    2018: {
        "unit": "Szczebel",
        "woj": "TERYT",
        "okr": "Nr okr.",
        "list": "Nr listy",
        "committee": "Komitet",
        "pos": "Poz.",
        "votes": "Liczba głosów",
        "seat": "Mandat",
        "sex": "Płeć",
        "family": "Nazwisko",
    },
}
GIVEN = {
    2002: ("1. imię", "2. imię"),
    2006: ("Imiona",),
    2010: ("Imiona",),
    2014: ("Imiona",),
    2018: ("Imię", "Drugie imię"),
}
LEVEL = {2002: "województwo", 2006: "WO", 2010: "sejmik", 2014: "województwo", 2018: "województwo"}
SEX = {"K": "female", "M": "male", "Kobieta": "female", "Mężczyzna": "male"}
# KBW's lists of constituencies with their boundaries, described by powiat (and by district in Warsaw).
DESCRIPTIONS = {
    2018: ("2018/samorzad/okregi.zip", "okregi.csv", "TERYT"),
    2024: (
        "2024/samorzad/okregi_sejmiki_wojewodztw_csv.zip",
        "okregi_sejmiki_wojewodztw_utf8.csv",
        "TERYT Województwa",
    ),
}
# Votes by gmina in 2002, which print no constituency; they check that 2002 used the constituencies of 2006.
TURNOUT_2002 = "2002/2002-frekwencja-i-niewa%c5%bcne-gminy.xls"
# Wałbrzych (powiat 0265 since 2013) was a gmina of powiat wałbrzyski (0221) from 2003 to 2012, and a city with
# powiat rights coded 0263 in 2002.
WALBRZYCH, WALBRZYCH_RURAL, WALBRZYCH_2002 = "0265", "0221", "0263"
WARSAW = "1465"
# Names in the descriptions that differ from the PRG's: powiat jeleniogórski became karkonoski in 2021, and one
# description misspells myślenicki.
ALIASES = {("02", "jeleniogórski"): "0206", ("12", "myslenicki"): "1209"}
LABELS = re.compile(
    r"(?i)\b(miast[oa](\s+na\s+prawach\s+powiatu)?|m\.|powiaty|powiat|pow\.|dzielnice\s+m\.\s*st\.\s*warszawy)\s*:?"
)


def fetch() -> None:
    for path in [*FILES.values(), *UNIT_FILES.values(), *(d[0] for d in DESCRIPTIONS.values()), TURNOUT_2002]:
        raw.fetch(SOURCE, f"{BASE}/{path}", suffix=Path(path).suffix)


def unit_tables(year: int) -> list[pl.DataFrame]:
    path = raw.cached(SOURCE, f"{BASE}/{UNIT_FILES[year]}").path
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        bodies = [archive.read(m) for m in sorted(archive.namelist()) if m.endswith(UNIT_MEMBERS[year])]
    else:
        bodies = [path.read_bytes()]
    frames = []
    for body in bodies:
        if year == 2024:
            frame = pl.read_csv(io.BytesIO(body), separator=";", infer_schema_length=0)
        else:
            frame = pl.read_excel(io.BytesIO(body), infer_schema_length=0)
        frame = frame.rename({column: column.lstrip("\ufeff") for column in frame.columns})
        frames.append(frame.with_columns(pl.col(pl.String).str.strip_chars().replace("", None)))
    return frames


def list_columns(frame: pl.DataFrame, year: int) -> list[str]:
    valid = UNIT_COLUMNS[year][5]
    columns = frame.columns[frame.columns.index(valid) + 1 :]
    if year == 2024:
        return [c for c in columns if c.startswith("Głosy na listę nr ")]
    return [c for c in columns if not c.startswith("Pkt ")]


def assign(sums: pl.DataFrame, lists: pl.DataFrame, columns: list[str], woj: str) -> dict[str, int]:
    wanted = {
        number: dict(part.select("okr", "votes").iter_rows())
        for (number,), part in lists.filter(pl.col("woj") == woj).group_by("list")
    }
    got = {c: dict(sums.select("okr", c).iter_rows()) for c in columns if sums[c].sum() > 0}

    def distance(column: str, number: int) -> int:
        okregi = set(got[column]) | set(wanted[number])
        return sum(abs(got[column].get(o, 0) - wanted[number].get(o, 0)) for o in okregi)

    mapping: dict[str, int] = {}
    for column in sorted(got, key=lambda c: min(distance(c, n) for n in wanted)):
        free = [n for n in wanted if n not in mapping.values()]
        if not free:
            raise ValueError(f"{woj}: column {column!r} has votes and no list is left")
        number = min(free, key=lambda n: distance(column, n))
        if distance(column, number) > 0.3 * sum(got[column].values()):
            raise ValueError(f"{woj}: column {column!r} matches no list")
        mapping[column] = number
    return mapping


def load_units(year: int, candidates: pl.DataFrame) -> pl.DataFrame:
    gmina, okr, commission, electors, cast, valid = UNIT_COLUMNS[year]
    lists = candidates.group_by("woj", "okr", "list").agg(pl.col("votes").sum())
    pieces = []
    for frame in unit_tables(year):
        frame = frame.filter(pl.col(gmina).is_not_null() & pl.col(commission).is_not_null())
        columns = list_columns(frame, year)
        base = frame.select(
            gmina=pl.col(gmina).str.zfill(6),
            okr=pl.col(okr).cast(pl.Int64),
            commission=pl.col(commission).cast(pl.Int64),
            electors=pl.col(electors).cast(pl.Int64),
            cast=pl.col(cast).cast(pl.Int64),
            valid=pl.col(valid).cast(pl.Int64),
            **{c: pl.col(c).cast(pl.Int64, strict=False).fill_null(0) for c in columns},
        ).with_columns(woj=pl.col("gmina").str.slice(0, 2))
        for (woj,), part in base.group_by("woj"):
            if year == 2024:
                mapping = {c: int(c.removeprefix("Głosy na listę nr ").split(" ", 1)[0]) for c in columns}
            else:
                sums = part.group_by("okr").agg(pl.col(c).sum() for c in columns)
                mapping = assign(sums, lists, columns, str(woj))
            long = part.select("woj", "okr", "gmina", "commission", "electors", "cast", "valid", *mapping).unpivot(
                index=["woj", "okr", "gmina", "commission", "electors", "cast", "valid"],
                variable_name="column",
                value_name="votes",
            )
            pieces.append(
                long.with_columns(list=pl.col("column").replace_strict(mapping, return_dtype=pl.Int64)).drop("column")
            )
    units = pl.concat(pieces).filter(pl.col("votes") > 0)
    known = candidates.select("woj", "okr", "list").unique()
    stray = units.join(known, on=["woj", "okr", "list"], how="anti")
    if stray.height:
        raise ValueError(f"{year}: votes for lists with no candidates: {stray.head(5)}")
    return units


def load(year: int) -> pl.DataFrame:
    path = raw.cached(SOURCE, f"{BASE}/{FILES[year]}").path
    if year == 2024:
        archive = zipfile.ZipFile(path)
        [member] = archive.namelist()
        frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0, encoding="utf8")
        frame = frame.rename({column: column.lstrip("﻿") for column in frame.columns})
        return frame.select(
            woj=pl.col("TERYT Województwa").str.slice(0, 2),
            okr=pl.col("Nr okręgu").cast(pl.Int64),
            list=pl.col("Nr listy").cast(pl.Int64),
            committee=pl.col("Nazwa komitetu").str.strip_chars(),
            pos=pl.col("Pozycja na liście").cast(pl.Int64),
            name=pl.col("Nazwisko i imiona").str.strip_chars(),
            sex=pl.col("Płeć").replace_strict(SEX),
            votes=pl.col("Liczba głosów").cast(pl.Int64, strict=False),
            elected=pl.col("Czy uzyskał mandat") == "Tak",
            seat=pl.col("Czy uzyskał mandat"),
            valid=pl.col("Liczba głosów ważnych oddanych w okręgu").cast(pl.Int64),
            electors=pl.lit(None, pl.Int64),
            cast=pl.lit(None, pl.Int64),
        )
    spec = COLUMNS[year]
    frame = pl.read_excel(path, infer_schema_length=0)
    frame = frame.rename({column: " ".join(column.split()) for column in frame.columns})
    frame = frame.filter(pl.col(spec["unit"]) == LEVEL[year])
    given = pl.concat_str([pl.col(c) for c in GIVEN[year]], separator=" ", ignore_nulls=True)
    out = frame.select(
        woj=pl.col(spec["woj"]).str.slice(0, 2),
        okr=pl.col(spec["okr"]).cast(pl.Int64),
        list=pl.col(spec["list"]).cast(pl.Int64),
        committee=pl.col(spec["committee"]).str.strip_chars(),
        pos=pl.col(spec["pos"]).cast(pl.Int64),
        name=pl.concat_str([pl.col(spec["family"]), given], separator=" ", ignore_nulls=True).str.strip_chars(),
        sex=pl.col(spec["sex"]).replace_strict(SEX, default=None),
        votes=pl.col(spec["votes"]).cast(pl.Int64),
        seat=pl.col(spec["seat"]),
    )
    if year == 2006:
        out = out.with_columns(
            valid=frame["Ważne"].cast(pl.Int64),
            electors=frame["Upr."].cast(pl.Int64),
            cast=frame["Odd."].cast(pl.Int64),
        )
    elif year == 2014:
        out = out.with_columns(
            valid=frame["Ważne"].cast(pl.Int64), electors=pl.lit(None, pl.Int64), cast=pl.lit(None, pl.Int64)
        )
    else:
        out = out.with_columns(
            valid=pl.lit(None, pl.Int64), electors=pl.lit(None, pl.Int64), cast=pl.lit(None, pl.Int64)
        )
    return out.with_columns(elected=pl.col("seat").is_in(["T", "B", "G", "L"]))


def parse() -> None:
    """Parse the years in SEJMIK_YEAR (every year when it is unset), then build the package pl.kbw_sejmik/all from
    every year's parsed tables."""
    years = [int(y) for y in os.environ["SEJMIK_YEAR"].split(",")] if "SEJMIK_YEAR" in os.environ else list(ELECTIONS)
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in years:
        frame = load(year)
        frame.with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(ELECTIONS[year])).write_parquet(
            PARSED / f"{year}.parquet"
        )
        print(year, frame.height, frame["elected"].sum())
        if year in UNIT_FILES:
            units = load_units(year, frame)
            units.write_parquet(PARSED / f"{year}-units.parquet")
            listed = frame.group_by("woj", "okr", "list").agg(pl.col("votes").sum().alias("listed"))
            summed = units.group_by("woj", "okr", "list").agg(pl.col("votes").sum())
            off = summed.join(listed, on=["woj", "okr", "list"], how="full", coalesce=True).filter(
                pl.col("votes").fill_null(0) != pl.col("listed").fill_null(0)
            )
            print(year, units.select("gmina", "commission").n_unique(), "polling districts;", off.height, "lists off")
            if off.height:
                print(off.sort("woj", "okr", "list"))
    print(build())


VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
METHOD = (
    "List votes, declared seats, and elected members for one constituency of a voivodeship assembly from the "
    "Krajowe Biuro Wyborcze's candidate file for the local elections. Voters choose one candidate, so each list's "
    "votes are the sum of its candidates' votes; valid votes are the figure the file prints for the constituency "
    "where it prints one, else the sum of the candidates' votes. Each elected member is the candidate the file "
    "marks as elected."
)
UNIT_METHOD = (
    "List votes, declared seats, and elected members for one constituency of a voivodeship assembly from the "
    "Krajowe Biuro Wyborcze's files for the local elections. List votes and accounting are summed from the "
    "polling district protocols, which count every vote cast for a list, including votes for candidates struck "
    "off it; seats and elected members are the candidates the candidate file marks as elected."
)
GMINA_METHOD = (
    "List votes and accounting summed from the polling district protocols of each gmina in the constituency, "
    "from the Krajowe Biuro Wyborcze's file of results by polling district."
)
OBWOD_METHOD = (
    "List votes and accounting by polling district (obwód głosowania) as the Krajowe Biuro Wyborcze's file "
    "prints them. Polling districts are described by streets and addresses, so they have no mapped area and are "
    "placed in their gmina."
)
SYSTEM = (
    "Seats are shared in each constituency by the D'Hondt method among lists of committees that won 5 percent of "
    "the valid votes in the whole voivodeship (Ordynacja wyborcza do rad gmin, rad powiatów i sejmików województw "
    "of 16 July 1998, then Kodeks wyborczy, art. 418). Elected members are the candidates with the most votes on "
    "each list. The threshold counts votes across the voivodeship's constituencies, so it is stored with scope "
    "national, which here means all sibling constituencies of one assembly."
)
SYSTEM_2006 = (
    "Seats are shared in each constituency among lists of committees that won 5 percent of the valid votes in the "
    "whole voivodeship, with lists able to join in blocks that took seats as one list before sharing them "
    "(Ordynacja wyborcza do rad gmin, rad powiatów i sejmików województw, as amended on 6 September 2006). The "
    "file does not say which lists formed blocks, so the declared seats are stored as printed and D'Hondt "
    "reproduces them in only 32 of the 88 constituencies."
)


def sejmik_names() -> dict[str, str]:
    path = raw.cached(SOURCE, f"{BASE}/{FILES[2024]}").path
    archive = zipfile.ZipFile(path)
    [member] = archive.namelist()
    frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0)
    frame = frame.rename({column: column.lstrip("\ufeff") for column in frame.columns})
    return {
        row["TERYT Województwa"][:2]: row["Sejmik"]
        for row in frame.select("TERYT Województwa", "Sejmik").unique().iter_rows(named=True)
    }


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package pl.kbw_sejmik/all from the parsed tables. It reads the database, through Draft, so the rows
    it rebuilds keep the ids the changeset path gave them."""
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="PL")
        cs.accept(
            "accounting.turnout",
            "Polling district 34 of gmina 160301 in 2018 prints 46 ballots taken from the urn against 45 voters "
            "entitled to vote; both are stored as printed.",
        )
        areas = build_okregi(cs)
        # People of this pipeline whose row another pipeline has since written (a crosswalk match that added a
        # gender, say) keep that row.
        taken = {
            id
            for (id,) in conn.execute(
                """select p.id from person p join changeset c on c.id = p.changeset_id
                   join entity e on e.id = p.id join changeset o on o.id = e.changeset_id
                   where o.pipeline = %s and c.pipeline <> %s""",
                [SOURCE, SOURCE],
            )
        }
        written: set[UUID] = set()
        # Years go newest first, the order the changeset path first wrote them in, so a party shared by several
        # years is named from the year that first wrote it.
        for year in sorted(ELECTIONS, reverse=True):
            build_year(cs, year, written, taken, areas)
        return cs.write()


def piece(gmina: str) -> str:
    """The PRG unit a gmina code falls in: its powiat, or in Warsaw its district."""
    return gmina[:6] if gmina.startswith(WARSAW) else gmina[:4]


def folded(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").replace(".", " ").split())


def described(text: str, woj: str, names: dict[tuple[str, str], str]) -> set[str]:
    """The pieces a KBW boundary description names, such as 'miasto Radom; powiaty: białobrzeski, grójecki'."""
    found = set()
    for token in re.split(r"[,;:.]|\s+i\s+", LABELS.sub(",", text)):
        token = folded(token)
        if not token:
            continue
        if (woj, token) not in names:
            raise ValueError(f"{woj}: no powiat or district named {token!r} in {text!r}")
        found.add(names[(woj, token)])
    return found


def memberships(cs: Draft, pieces: set[str]) -> dict[int, dict[tuple[str, int], set[str]]]:
    """The pieces (powiats, and districts in Warsaw) of each constituency in each year. 2006-2024 come from the
    polling district files, checked against KBW's boundary descriptions for 2018 and 2024. KBW publishes no
    constituencies for 2002, so 2002 takes those of 2006, checked against its votes by gmina."""
    found: dict[int, dict[tuple[str, int], set[str]]] = {}
    for year in UNIT_FILES:
        members = found[year] = {}
        rows = pl.read_parquet(PARSED / f"{year}-units.parquet").select("woj", "okr", "gmina").unique()
        for row in rows.iter_rows(named=True):
            members.setdefault((row["woj"], row["okr"]), set()).add(piece(row["gmina"]))
        if not any(WALBRZYCH in held for held in members.values()):
            for held in members.values():
                if WALBRZYCH_RURAL in held:
                    held.add(WALBRZYCH)
        held = set().union(*members.values())
        if held != pieces:
            raise ValueError(f"{year}: pieces {sorted(held - pieces)} have no shape, {sorted(pieces - held)} no vote")
    names = {
        (code[:2], folded(text.removeprefix("powiat "))): piece(code)
        for code, text in cs.conn.execute(
            """select c.code, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred and n.lang = 'pl'
               where a.scheme_code in ('pl.powiat', 'pl.dzielnica_warszawy') and c.code <> %s""",
            [WARSAW],
        ).fetchall()
    } | ALIASES
    for year, (path, member, column) in DESCRIPTIONS.items():
        archive = zipfile.ZipFile(raw.cached(SOURCE, f"{BASE}/{path}").path)
        frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0)
        frame = frame.rename({c: c.lstrip("﻿") for c in frame.columns})
        if "Szczebel" in frame.columns:
            frame = frame.filter(pl.col("Szczebel") == "Sejmik województwa")
        listed = {
            (row[column][:2], int(row["Numer okręgu"])): described(row["Opis granic"], row[column][:2], names)
            for row in frame.iter_rows(named=True)
        }
        if listed != found[year]:
            off = sorted(k for k in listed.keys() | found[year].keys() if listed.get(k) != found[year].get(k))
            raise ValueError(f"{year}: KBW's descriptions differ from the polling districts in {off}")
    found[2002] = {key: set(held) for key, held in found[2006].items()}
    check_2002(found[2002])
    return dict(sorted(found.items()))


def check_2002(members: dict[tuple[str, int], set[str]]) -> None:
    """Each constituency's valid votes by gmina in 2002 must cover its candidates' votes, and exceed them by no more
    than the 2 percent that candidates struck off the lists could hold, when 2002 used the constituencies of 2006.
    Constituencies that share a powiat (Łódź) are compared together."""
    path = raw.cached(SOURCE, f"{BASE}/{TURNOUT_2002}").path
    frame = pl.read_excel(path, infer_schema_length=0)
    column = frame.columns[frame.columns.index("Sejmik województwa") + 3]
    if frame[column][0] != "gł. ważne":
        raise ValueError(f"2002: column {column} is {frame[column][0]!r}, not valid votes")
    votes = Counter[str]()
    for gmina, valid in frame.slice(1).select("Gmina", column).iter_rows():
        if gmina is not None and re.fullmatch(r"\d{6}", gmina) and valid is not None:
            code = piece(gmina)
            votes[WALBRZYCH if code == WALBRZYCH_2002 else code] += int(valid)
    groups: list[tuple[set[tuple[str, int]], set[str]]] = []
    for key, held in members.items():
        joined = [g for g in groups if g[1] & held]
        keys, pieces = {key}, set(held)
        for g in joined:
            groups.remove(g)
            keys |= g[0]
            pieces |= g[1]
        groups.append((keys, pieces))
    listed = pl.read_parquet(PARSED / "2002.parquet").group_by("woj", "okr").agg(pl.col("votes").sum())
    candidates = {(row["woj"], row["okr"]): row["votes"] for row in listed.iter_rows(named=True)}
    if set(candidates) != set(members):
        raise ValueError("2002 has other constituencies than 2006")
    for keys, pieces in groups:
        printed = sum(votes[p] for p in pieces)
        counted = sum(candidates[k] for k in keys)
        if not counted <= printed <= counted * 1.02:
            raise ValueError(f"2002 {sorted(keys)}: {printed:,} valid votes by gmina, {counted:,} by candidate")


def build_okregi(cs: Draft) -> dict[tuple[int, str, int], UUID]:
    """The constituencies as areas, unions of the PRG's powiats (and Warsaw's districts). A constituency keeps one
    area while its pieces stay the same from one election to the next, dated from the first election it served to
    the next that redrew it. A constituency that shares a powiat with another (Łódź's two in 2002-2010, split by
    the city's own districts, which the PRG leaves out) has no area."""
    conn = cs.conn
    poland = cs.polity("PL")
    cs.add(
        "scheme",
        code="pl.okreg_sejmik",
        polity_id=poland,
        kind="electoral_district",
        rank=2,
        noun="voivodeship assembly constituency",
    )
    source = cs.source(PUBLISHER, TITLE)
    for path in [*(d[0] for d in DESCRIPTIONS.values()), TURNOUT_2002]:
        cs.artifact(raw.cached(SOURCE, f"{BASE}/{path}"), source)
    rows = conn.execute(
        """select a.scheme_code, c.code, ST_AsBinary(g.geom), g.source_id from area a
           join area_code c on c.area_id = a.id join area_geometry g on g.area_id = a.id
           where a.scheme_code in ('pl.powiat', 'pl.dzielnica_warszawy') and c.code <> %s""",
        [WARSAW],
    ).fetchall()
    shapes = {piece(code): shapely.from_wkb(geom) for _, code, geom, _ in rows}
    sources = {piece(code): source_id for _, code, _, source_id in rows}
    members = memberships(cs, set(shapes))
    native = sejmik_names()
    stored = {
        (area, start): text
        for area, start, text in conn.execute(
            """select g.area_id, g.valid_from, g.geom::text from area_geometry g join area a on a.id = g.area_id
               where a.scheme_code = 'pl.okreg_sejmik'"""
        ).fetchall()
    }
    years = list(members)
    areas: dict[tuple[int, str, int], UUID] = {}
    runs: dict[tuple[str, int], list[list[int]]] = {}
    for i, year in enumerate(years):
        shared = Counter(p for held in members[year].values() for p in held)
        for key, held in sorted(members[year].items()):
            if any(shared[p] > 1 for p in held):
                continue
            spans = runs.setdefault(key, [])
            if spans and spans[-1][-1] == years[i - 1] and members[spans[-1][0]][key] == held:
                spans[-1].append(year)
            else:
                spans.append([year])
    for (woj, okr), spans in sorted(runs.items()):
        english = VOIVODESHIPS[woj][1].replace(" Voivodeship", "")
        for span in spans:
            start = ELECTIONS[span[0]]
            after = years.index(span[-1]) + 1
            end = ELECTIONS[years[after]] if after < len(years) else None
            code = f"{woj}:{okr}"
            area = cs.area("pl.okreg_sejmik", code, start)
            for year in span:
                areas[(year, woj, okr)] = area
            held = sorted(members[span[0]][(woj, okr)])
            [prg] = {sources[p] for p in held}
            shape = shapely.set_srid(shapely.union_all([shapes[p] for p in held], grid_size=1e-7), 4326)
            served = str(span[0]) if len(span) == 1 else f"{span[0]}–{span[-1]}"
            cs.add("area", id=area, scheme_code="pl.okreg_sejmik")
            cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=cs.area("pl.wojewodztwo", woj),
                valid_from=start,
                valid_to=end,
            )
            for text, lang, kind in (
                (f"{native[woj]}, okręg wyborczy nr {okr} ({served})", "pl", "official"),
                (f"{english} assembly constituency {okr} ({served})", "en", "english"),
            ):
                cs.add("name", entity_id=area, text=text, lang=lang, kind=kind, is_preferred=kind == "official")
            # An outline equal to the stored one keeps the stored bytes: a union can start its rings elsewhere.
            same = (area, start) in stored and shapely.equals(shapely.from_wkb(stored[(area, start)]), shape)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_to=end,
                geom=stored[(area, start)] if same else shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(max(shapely.get_parts(shape), key=shapely.area).representative_point(), 4326),
                    hex=True,
                    include_srid=True,
                ),
                quality="official",
                source_id=prg,
            )
    for year, after in zip(years, [*years[1:], None]):
        cs.add(
            "boundary_set",
            scheme_code="pl.okreg_sejmik",
            vintage=str(year),
            valid_from=ELECTIONS[year],
            valid_to=None if after is None else ELECTIONS[after],
            as_of=ELECTIONS[year],
        )
    print(
        f"{len({a for a in areas.values()})} constituency areas for {len(areas)} of "
        f"{sum(len(m) for m in members.values())} constituencies",
        flush=True,
    )
    return areas


def build_year(
    cs: Draft, year: int, written: set[UUID], taken: set[UUID], areas: dict[tuple[int, str, int], UUID]
) -> None:
    conn = cs.conn
    frame = pl.read_parquet(PARSED / f"{year}.parquet")
    units = pl.read_parquet(PARSED / f"{year}-units.parquet") if year in UNIT_FILES else None
    day = ELECTIONS[year]
    native = sejmik_names()

    def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
        cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

    poland = cs.polity("PL")
    source = cs.source(PUBLISHER, TITLE)
    cs.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license="Official public record, no license stated",
        attribution="Źródło danych: Państwowa Komisja Wyborcza, Krajowe Biuro Wyborcze.",
    )
    cs.artifact(raw.cached(SOURCE, f"{BASE}/{FILES[year]}"), source)
    if units is not None:
        cs.artifact(raw.cached(SOURCE, f"{BASE}/{UNIT_FILES[year]}"), source)
    blocks = year == 2006
    system = cs.key("pl.electoral_system", "sejmik-2006" if blocks else "sejmik")
    rules: dict[str, object] = {
        "formula": "dhondt",
        "lists": {"order": "open", "vacancy": "next_on_list"},
        "threshold": {"level": "list", "scope": "national", "percent": 5},
    }
    if blocks:
        rules["pooling"] = "alliance"
    cs.add("electoral_system", id=system, polity_id=poland, rules=rules, rules_version=1)
    name(
        system,
        "Voivodeship assembly open-list D'Hondt system with blocks (2006)"
        if blocks
        else "Voivodeship assembly open-list D'Hondt system",
    )
    cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_2006 if blocks else SYSTEM)

    by_item = dict(
        conn.execute(
            """select x.value, x.entity_id from external_id x join party p on p.id = x.entity_id
               where x.scheme = 'wikidata' and p.polity_id = %s
               and not exists (select from entity_merge m where m.entity_id = p.id)""",
            (poland,),
        ).fetchall()
    )
    parties: dict[str, UUID] = {}

    def party_for(committee: str) -> UUID:
        label = " ".join(committee.split())
        if label in parties:
            return parties[label]
        # A committee the pipeline already keyed keeps its party, so a Wikidata party loaded later does not move
        # its lists.
        item = next((code for text, code in ITEMS.items() if label.upper().rstrip('"').endswith(text)), None)
        if item is not None and item in by_item and ("pl.party", slug(label)) not in cs.own_keys:
            party = parties[label] = by_item[item]
            return party
        party = parties[label] = cs.key("pl.party", slug(label))
        if party in cs.minted and party not in written:
            cs.add("party", id=party, kind="party", polity_id=poland)
            name(party, label, "pl")
            written.add(party)
        return party

    election = cs.key("pl.election", f"{day}:local")
    cs.add("election", id=election, polity_id=poland, kind="local", date_from=day, date_to=day, status="held")
    name(election, f"{day.year} Polish local elections")
    name(election, f"Wybory samorządowe {day.year}", "pl", "native")
    path = f"pl/{day}-local"
    cs.add("slug", path=path, entity_id=election)

    sizes = {
        (ELECTIONS[held], row["woj"]): row["elected"]
        for held in ELECTIONS
        if (PARSED / f"{held}.parquet").exists()
        for row in pl.read_parquet(PARSED / f"{held}.parquet")
        .group_by("woj")
        .agg(pl.col("elected").sum())
        .iter_rows(named=True)
    }
    days = sorted({start for start, _ in sizes})
    unit_rows: dict[UUID, dict[str, str | None]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    gminas = Counter[str]()
    gmina_areas: dict[str, UUID] = {}
    for code, area in conn.execute(
        "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.gmina'"
    ).fetchall():
        gminas[code[:6]] += 1
        gmina_areas[code[:6]] = area
    gmina_units: dict[str, UUID] = {}

    def gmina_unit(code: str) -> UUID:
        if code not in gmina_units:
            unit = gmina_units[code] = cs.unit("pl.gmina", code)
            area = gmina_areas.get(code) if gminas[code] == 1 else None
            unit_rows[unit] = {
                "scheme_code": "pl.gmina",
                "code": code,
                "parent_id": None,
                "area_relation": "equals" if area is not None else "unlocated",
                "area_id": None if area is None else str(area),
                "kind": "standard",
            }
        return gmina_units[code]

    def side_set(contest: UUID, key: str, label: str, note: str) -> UUID:
        result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=1,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
        return result_set

    def accounting(result_set: UUID, unit: UUID, rows: pl.DataFrame) -> None:
        put(result_set, unit, rows["electors"].to_list(), rows["cast"].to_list(), rows["valid"].to_list())

    def put(result_set: UUID, unit: UUID, *columns: list[int | None]) -> None:
        electors, cast, valid = (None if None in c else sum(v or 0 for v in c) for c in columns)
        if valid is None:
            raise ValueError(f"{unit}: a polling district prints no valid votes")
        values.append((str(result_set), str(unit), None, VALID, valid))
        if electors is not None:
            values.append((str(result_set), str(unit), None, REGISTERED, electors))
        if cast is not None:
            values.append((str(result_set), str(unit), None, BALLOTS, cast))
            values.append((str(result_set), str(unit), None, INVALID, cast - valid))

    for code in sorted(set(frame["woj"])):
        region = cs.polity(f"PL-{code}")
        english = VOIVODESHIPS[code][1]
        region_slug = slug(VOIVODESHIPS[code][0])
        body = cs.key("pl.body", f"sejmik-{code}")
        cs.add("body", id=body, polity_id=region, kind="council")
        name(body, f"Assembly of {english.replace(' Voivodeship', '')} Voivodeship")
        name(body, native[code], "pl", "native")
        for start, end in zip(days, [*days[1:], None]):
            cs.add("body_size", body_id=body, seats=sizes[(start, code)], valid_from=start, valid_to=end)
        region_unit = cs.unit("pl.wojewodztwo", code)
        unit_rows[region_unit] = {
            "scheme_code": "pl.wojewodztwo",
            "code": code,
            "parent_id": None,
            "area_relation": "equals",
            "area_id": str(cs.area("pl.wojewodztwo", code)),
            "kind": "standard",
        }
        for okr in sorted(set(frame.filter(pl.col("woj") == code)["okr"])):
            part = frame.filter((pl.col("woj") == code) & (pl.col("okr") == okr))
            key = f"{day}:{code}:{okr}"
            seats = int(part["elected"].sum())
            contest = cs.key(f"{SOURCE}.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                magnitude=seats,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"{english.replace(' Voivodeship', '')} assembly, constituency {okr}")
            name(contest, f"{native[code]}, okręg wyborczy nr {okr}", "pl", "native")
            cs.add("slug", path=f"{path}/sejmik/{region_slug}/{okr}", entity_id=contest)
            lists = (
                part.group_by("list")
                .agg(
                    pl.col("votes").sum(),
                    pl.col("elected").sum().alias("seats"),
                    pl.col("committee").first(),
                )
                .sort("votes", descending=True)
            )
            local = None
            if units is not None:
                local = units.filter((pl.col("woj") == code) & (pl.col("okr") == okr))
                listed = lists.select("list", listed="votes")
                lists = (
                    lists.drop("votes")
                    .join(local.group_by("list").agg(pl.col("votes").sum()), on="list", how="left")
                    .with_columns(pl.col("votes").fill_null(0))
                    .join(listed, on="list")
                    .sort("votes", descending=True)
                )
            options = {}
            for row in lists.iter_rows(named=True):
                option = options[row["list"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['list']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party_for(row["committee"]),
                    ballot_label=" ".join(row["committee"].split()),
                )
            unit = cs.unit("pl.okreg_sejmik", key)
            area = areas.get((year, code, okr))
            unit_rows[unit] = {
                "scheme_code": "pl.okreg_sejmik",
                "code": key,
                "parent_id": str(region_unit),
                "area_relation": "unlocated" if area is None else "equals",
                "area_id": None if area is None else str(area),
                "kind": "standard",
            }
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            counted = int(lists["votes"].sum())
            if local is None:
                valid = part["valid"][0] if part["valid"][0] is not None else counted
                note = METHOD
                if valid != counted:
                    note += (
                        f" The file prints {valid:,} valid votes for the constituency and its candidates' votes "
                        f"sum to {counted:,}; both are stored as printed."
                    )
                figures = [(VALID, valid)]
                if part["electors"][0] is not None:
                    figures.append((REGISTERED, part["electors"][0]))
                if part["cast"][0] is not None:
                    figures.extend([(BALLOTS, part["cast"][0]), (INVALID, part["cast"][0] - valid)])
                for measure, value in figures:
                    values.append((str(result_set), str(unit), None, measure, int(value)))
            else:
                note = UNIT_METHOD
                struck = lists.filter(pl.col("votes") != pl.col("listed"))
                if struck.height:
                    note += " The candidates' votes in the candidate file fall short of the list's votes for " + (
                        ", ".join(
                            f"list {row['list']} ({row['votes']:,} against {row['listed']:,})"
                            for row in struck.sort("list").iter_rows(named=True)
                        )
                        + "."
                    )
                stations = local.select("gmina", "commission", "electors", "cast", "valid").unique()
                if stations.select("gmina", "commission").is_duplicated().any():
                    raise ValueError(f"{key}: a polling district has two accounting rows")
                if int(stations["valid"].sum()) != counted:
                    raise ValueError(f"{key}: valid votes {stations['valid'].sum()} for list votes {counted}")
                accounting(result_set, unit, stations)
                gmina_set = side_set(contest, key, "gmina", GMINA_METHOD)
                obwod_set = side_set(contest, key, "obwod", OBWOD_METHOD)
                for (gmina,), at in stations.group_by("gmina"):
                    accounting(gmina_set, gmina_unit(str(gmina)), at)
                for row in local.group_by("gmina", "list").agg(pl.col("votes").sum()).iter_rows(named=True):
                    values.append(
                        (
                            str(gmina_set),
                            str(gmina_unit(row["gmina"])),
                            str(options[row["list"]]),
                            VOTES,
                            row["votes"],
                        )
                    )
                obwody: dict[tuple[str, int], UUID] = {}
                for row in stations.iter_rows(named=True):
                    tag = f"{day}:{row['gmina']}:{row['commission']}"
                    station = obwody[(row["gmina"], row["commission"])] = cs.unit("pl.obwod", tag)
                    unit_rows[station] = {
                        "scheme_code": "pl.obwod",
                        "code": tag,
                        "parent_id": str(gmina_unit(row["gmina"])),
                        "area_relation": "unlocated",
                        "area_id": None,
                        "kind": "standard",
                    }
                    put(obwod_set, station, [row["electors"]], [row["cast"]], [row["valid"]])
                for row in local.iter_rows(named=True):
                    values.append(
                        (
                            str(obwod_set),
                            str(obwody[(row["gmina"], row["commission"])]),
                            str(options[row["list"]]),
                            VOTES,
                            row["votes"],
                        )
                    )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            elected = Counter()
            for row in lists.iter_rows(named=True):
                values.append((str(result_set), str(unit), str(options[row["list"]]), VOTES, row["votes"]))
                if row["seats"]:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[row["list"]],
                        kind="won",
                        seats=row["seats"],
                    )
            for person_row in part.sort("list", "pos").iter_rows(named=True):
                tag = f"{key}:{person_row['list']}:{person_row['pos']}"
                person = cs.key(f"{SOURCE}.person", tag)
                if person not in taken:
                    cs.add("person", id=person, gender=person_row["sex"])
                name(person, display(person_row["name"], "pl"))
                candidacy = cs.key(f"{SOURCE}.candidacy", tag)
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=options[person_row["list"]],
                    role="list_member",
                    list_position=person_row["pos"],
                    ballot_name=person_row["name"],
                    is_independent=False,
                )
                if person_row["elected"]:
                    elected[person_row["list"]] += 1
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[person_row["list"]],
                        candidacy_id=candidacy,
                        kind="elected",
                    )
            if sum(elected.values()) != seats:
                raise ValueError(f"{key}: elected count mismatch")

    minted = [
        {"id": str(unit), "polity_id": str(poland), **row}
        for unit, row in unit_rows.items()
        if unit in cs.minted or row["scheme_code"] == "pl.okreg_sejmik"
    ]
    cs.frame("reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String, "area_id": pl.String}))
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
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
