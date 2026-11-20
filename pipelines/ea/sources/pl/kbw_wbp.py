import io
import os
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.names import display
from ea.package import Draft
from ea.sources.pl.prg import VOIVODESHIPS
from ea.store import connect

SOURCE = "pl.kbw_wbp"
VERSION = "1"
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze: wybory wójtów, burmistrzów i prezydentów miast"
PAGE = "https://danewyborcze.kbw.gov.pl/"
BASE = "https://danewyborcze.kbw.gov.pl/dane"
PARSED = raw.parsed(SOURCE)
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
ELECTIONS = {
    2024: (date(2024, 4, 7), date(2024, 4, 21)),
    2018: (date(2018, 10, 21), date(2018, 11, 4)),
    2014: (date(2014, 11, 16), date(2014, 11, 30)),
    2010: (date(2010, 11, 21), date(2010, 12, 5)),
    2006: (date(2006, 11, 12), date(2006, 11, 26)),
    2002: (date(2002, 10, 27), date(2002, 11, 10)),
}
FILES = {
    2024: {
        "candidates1": "2024/samorzad/kandydaci_wbp_csv.zip",
        "candidates2": "2024/samorzad/kandydaci_wbp2_csv.zip",
        "units1": "2024/samorzad/protokoly_po_obwodach_wbp_csv.zip",
        "units2": "2024/samorzad/protokoly_po_obwodach_wbp2_csv.zip",
    },
    2018: {
        "candidates": "2018/samorzad/2018-wybbp.zip",
        "units1": "2018/samorzad/wbp/Itura.xlsx",
        "units2": "2018/samorzad/wbp/IItura.xlsx",
    },
    2014: {
        "candidates": "2014/samorzad/1579520736_samo2014_kandydaci_na_WBP.xls",
        "turnout": "2014/samorzad/samo2014-statystyki-wed%c5%82ug-gmin.zip",
    },
    2010: {"candidates": "2010/samo2010/2010-WBP.xls", "turnout": "2010/2010-frekw-wbp-gm.xls"},
    2006: {"units": "2006/wbp/samo2006-wbp-obw.xlsx"},
    2002: {"candidates": "2002/wbp/wbp2002.zip"},
}
OFFICES = {"W": "wojt", "B": "burmistrz", "P": "prezydent"}
TITLES = {"wojt": "Wójt", "burmistrz": "Burmistrz", "prezydent": "Prezydent Miasta"}
SEX = {"K": "female", "M": "male", "Kobieta": "female", "Mężczyzna": "male"}
CANDIDATE = re.compile(
    r"^Głosy (?:na kandydata nr (?P<pos>\d+)|(?P<side>za wyborem|przeciw wyborowi) kandydata) - (?P<name>.+?) "
    r"zarejestrowanego przez"
)
CANDIDATES_SCHEMA: dict[str, pl.DataType] = {
    "round": pl.Int8(),
    "teryt": pl.String(),
    "office": pl.String(),
    "title": pl.String(),
    "label": pl.String(),
    "pos": pl.Int64(),
    "name": pl.String(),
    "committee": pl.String(),
    "sex": pl.String(),
    "votes": pl.Int64(),
    "against": pl.Int64(),
    "valid": pl.Int64(),
    "electors": pl.Int64(),
    "cast": pl.Int64(),
    "elected": pl.Boolean(),
    "advanced": pl.Boolean(),
    "day": pl.Date(),
}


def url(year: int, kind: str) -> str:
    return f"{BASE}/{FILES[year][kind]}"


def fetch() -> None:
    for year, files in FILES.items():
        for kind, path in files.items():
            raw.fetch(SOURCE, url(year, kind), suffix=Path(path).suffix)


def tidy(frame: pl.DataFrame) -> pl.DataFrame:
    frame = frame.rename({column: " ".join(column.lstrip("﻿").split()) for column in frame.columns})
    return frame.with_columns(pl.col(pl.String).str.strip_chars())


def members(year: int, kind: str) -> list[pl.DataFrame]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url(year, kind)).path)
    out = []
    for member in sorted(archive.namelist()):
        if member.endswith(".csv"):
            out.append(
                tidy(
                    pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0, quote_char='"')
                )
            )
    return out


def excel(path: Path | bytes) -> pl.DataFrame:
    return tidy(pl.read_excel(io.BytesIO(path) if isinstance(path, bytes) else path, infer_schema_length=0))


def number(column: str) -> pl.Expr:
    return pl.col(column).replace({"-": None, "": None, "XXXXX": None}).cast(pl.Float64).cast(pl.Int64)


def office_of(title: str) -> str:
    return "wojt" if title.startswith("Wójt") else "burmistrz" if title.startswith("Burmistrz") else "prezydent"


ACCOUNTING = (
    number("Liczba wyborców uprawnionych do głosowania").alias("electors"),
    number("Liczba kart wyjętych z urny").alias("cast"),
    number("Liczba głosów ważnych oddanych łącznie na wszystkich kandydatów").alias("valid"),
)


def year_2024() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    candidates, units, votes = [], [], []
    for round_ in (1, 2):
        day = ELECTIONS[2024][round_ - 1]
        [frame] = members(2024, f"candidates{round_}")
        advanced = pl.col("Czy uzyskał prawo kandydowania w drugiej turze") == "Tak" if round_ == 1 else pl.lit(False)
        candidates.append(
            frame.select(
                round=pl.lit(round_, pl.Int8),
                teryt=pl.col("TERYT").str.zfill(6),
                office=pl.col("Urząd").map_elements(office_of, return_dtype=pl.String),
                title=pl.col("Urząd"),
                label=pl.col("Obszar"),
                pos=pl.col("Numer na karcie do głosowania").cast(pl.Int64),
                name=pl.col("Nazwisko i imiona"),
                committee=pl.col("Nazwa komitetu"),
                sex=pl.col("Płeć").replace_strict(SEX, default=None),
                votes=number("Liczba głosów"),
                against=pl.lit(None, pl.Int64),
                valid=number("Liczba głosów ważnych na wszystkich kandydatów"),
                elected=pl.col("Czy uzyskał mandat") == "Tak",
                advanced=advanced,
                day=pl.lit(day),
            )
        )
        for frame in members(2024, f"units{round_}"):
            base = frame.select(
                pl.lit(round_, pl.Int8).alias("round"),
                pl.col("Teryt Gminy").str.zfill(6).alias("teryt"),
                pl.col("Nr komisji").alias("commission"),
                *ACCOUNTING,
                seat=pl.col("Siedziba"),
            )
            units.append(base)
            columns = [column for column in frame.columns if CANDIDATE.match(column)]
            long = (
                frame.select(
                    pl.col("Teryt Gminy").str.zfill(6).alias("teryt"),
                    pl.col("Nr komisji").alias("commission"),
                    *map(pl.col, columns),
                )
                .unpivot(index=["teryt", "commission"], variable_name="column", value_name="value")
                .filter(pl.col("value").is_not_null() & (pl.col("value") != ""))
            )
            parts = long["column"].str.extract_groups(CANDIDATE.pattern)
            votes.append(
                long.with_columns(
                    round=pl.lit(round_, pl.Int8),
                    pos=parts.struct.field("pos").cast(pl.Int64),
                    side=parts.struct.field("side"),
                    name=parts.struct.field("name"),
                    value=pl.col("value").cast(pl.Int64),
                ).drop("column")
            )
    candidate_frame = pl.concat(candidates)
    vote_frame = pl.concat(votes).with_columns(warsaw("teryt"))
    single = vote_frame.filter(pl.col("side").is_not_null())
    positions = candidate_frame.select("round", "teryt", "name", "pos")
    single = single.drop("pos").join(positions, on=["round", "teryt", "name"], how="left")
    if single["pos"].null_count():
        raise ValueError("2024: single-candidate columns that match no candidate")
    vote_frame = pl.concat(
        [
            vote_frame.filter(pl.col("side").is_null()).select(
                "round", "teryt", "commission", "pos", votes=pl.col("value"), against=pl.lit(None, pl.Int64)
            ),
            single.group_by("round", "teryt", "commission", "pos").agg(
                votes=pl.col("value").filter(pl.col("side") == "za wyborem").sum(),
                against=pl.col("value").filter(pl.col("side") == "przeciw wyborowi").sum(),
            ),
        ]
    )
    against = vote_frame.group_by("round", "teryt", "pos").agg(
        pl.when(pl.col("against").is_not_null().any()).then(pl.col("against").sum()).alias("against")
    )
    candidate_frame = candidate_frame.drop("against").join(against, on=["round", "teryt", "pos"], how="left")
    return candidate_frame, pl.concat(units).with_columns(warsaw("teryt")), vote_frame


def warsaw(column: str) -> pl.Expr:
    return (
        pl.when(pl.col(column).str.starts_with("1465")).then(pl.lit("146501")).otherwise(pl.col(column)).alias(column)
    )


def zipped(year: int, name: str) -> pl.DataFrame:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url(year, "candidates")).path)
    return excel(archive.read(name))


def year_2018() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    candidates, units, votes = [], [], []
    for round_, member in ((1, "2018-kand-wbp-I-tura.xlsx"), (2, "2018-kand-wbp-II-tura.xlsx")):
        frame = zipped(2018, member)
        committee = "Komitet" if "Komitet" in frame.columns else "Komitet wyborczy"
        candidates.append(
            frame.select(
                round=pl.lit(round_, pl.Int8),
                teryt=pl.col("TERYT").str.zfill(6),
                office=pl.col("Urząd").replace_strict(OFFICES),
                title=pl.lit(None, pl.String),
                label=pl.col("Gmina"),
                pos=pl.lit(None, pl.Int64),
                name=pl.concat_str(["Nazwisko", "Imię", "Drugie imię"], separator=" ", ignore_nulls=True),
                committee=pl.col(committee),
                sex=pl.col("Płeć").replace_strict(SEX, default=None),
                votes=number('Głosy "za"'),
                against=number('Głosy "przeciw"'),
                valid=number("Gł. ważne"),
                elected=pl.col("Wybrany") == "Tak",
                advanced=pl.lit(False),
                day=pl.lit(ELECTIONS[2018][round_ - 1]),
            )
        )
        frame = excel(raw.cached(SOURCE, url(2018, f"units{round_}")).path)
        key = [
            pl.lit(round_, pl.Int8).alias("round"),
            pl.col("TERYT").str.zfill(6).alias("teryt"),
            pl.col("Nr obwodu głosowania").alias("commission"),
        ]
        units.append(
            frame.select(
                *key,
                *ACCOUNTING,
                seat=pl.col("Siedziba Obwodowej Komisji Wyborczej"),
            ).unique(["round", "teryt", "commission"], keep="first", maintain_order=True)
        )
        votes.append(
            frame.select(
                *key,
                pos=pl.col("Pozycja" if "Pozycja" in frame.columns else "Poz.").cast(pl.Int64),
                name=pl.col("Nazwisko i imiona"),
                votes=number("Głosy za"),
                against=number("Głosy przeciw"),
            )
        )
    candidate_frame = pl.concat(candidates)
    vote_frame = pl.concat(votes).with_columns(warsaw("teryt"))
    positions = vote_frame.select("round", "teryt", "name", "pos").unique()
    candidate_frame = candidate_frame.drop("pos").join(positions, on=["round", "teryt", "name"], how="left")
    second = candidate_frame.filter(pl.col("round") == 2).select("teryt", "name")
    candidate_frame = candidate_frame.with_columns(
        advanced=(pl.col("round") == 1)
        & pl.concat_str("teryt", "name").is_in(pl.concat_str(second["teryt"], second["name"]).implode())
    )
    units_frame = pl.concat(units).with_columns(warsaw("teryt"))
    return candidate_frame, units_frame, vote_frame.drop("name")


def year_2014() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    frame = excel(raw.cached(SOURCE, url(2014, "candidates")).path).slice(1)
    rounds = []
    for round_, (valid, votes, against, seat) in enumerate(
        (
            ("I tura", "__UNNAMED__18", "__UNNAMED__19", "__UNNAMED__21"),
            ("II tura", "__UNNAMED__23", "__UNNAMED__24", "__UNNAMED__26"),
        ),
        1,
    ):
        rounds.append(
            frame.filter(pl.col(votes).is_not_null()).select(
                round=pl.lit(round_, pl.Int8),
                teryt=pl.col("TERYT").str.zfill(6),
                office=pl.col("Urząd").replace_strict(OFFICES),
                title=pl.lit(None, pl.String),
                label=pl.col("Gmina"),
                pos=pl.lit(None, pl.Int64),
                name=pl.concat_str([pl.col("Nazwisko").str.to_uppercase(), "Imiona"], separator=" "),
                committee=pl.col("Komitet"),
                sex=pl.col("Płeć").replace_strict(SEX, default=None),
                votes=number(votes),
                against=number(against),
                valid=number(valid),
                elected=pl.col(seat) == "T",
                advanced=pl.col("__UNNAMED__23").is_not_null() if round_ == 1 else pl.lit(False),
                day=pl.lit(ELECTIONS[2014][round_ - 1]),
            )
        )
    candidates = pl.concat(rounds)
    archive = zipfile.ZipFile(raw.cached(SOURCE, url(2014, "turnout")).path)
    turnout = pl.concat(
        [
            excel(archive.read(member)).select(
                round=pl.lit(round_, pl.Int8),
                teryt=pl.col("TERYT").str.zfill(6),
                electors=number("Liczba wyborców"),
                cast=number("Karty ważne"),
                printed=number("Głosy ważne"),
                invalid=number("Głosy nieważne"),
            )
            for round_, member in ((1, "samo2014 WBP I tura w gminach.xls"), (2, "samo2014 WBP II tura w gminach.xls"))
        ]
    )
    return accounted(2014, candidates, turnout), empty_units(), empty_votes()


def accounted(year: int, candidates: pl.DataFrame, turnout: pl.DataFrame) -> pl.DataFrame:
    """Join a gmina turnout table to the candidate rows, checking that every contest has one row, that valid and
    invalid votes add up to the ballots, and that the table's valid votes equal the ones the candidates carry."""
    if turnout.select("round", "teryt").is_duplicated().any():
        raise ValueError(f"{year}: a gmina is listed twice in the turnout table")
    off = turnout.filter(pl.col("printed") + pl.col("invalid") != pl.col("cast"))
    if off.height:
        raise ValueError(f"{year}: valid and invalid votes do not add up to the ballots in {off.height} gminas\n{off}")
    contests = candidates.select("round", "teryt").unique()
    missing = contests.join(turnout, on=["round", "teryt"], how="anti")
    extra = turnout.join(contests, on=["round", "teryt"], how="anti")
    if missing.height or extra.height:
        raise ValueError(f"{year}: {missing.height} contests without turnout, {extra.height} turnout rows without one")
    joined = candidates.join(turnout.drop("invalid"), on=["round", "teryt"], how="left")
    if "valid" in candidates.columns:
        differs = joined.filter(pl.col("valid") != pl.col("printed")).select("round", "teryt").unique()
        if differs.height:
            raise ValueError(f"{year}: valid votes differ from the turnout table in {differs.height} contests")
        joined = joined.drop("printed")
    else:
        joined = joined.rename({"printed": "valid"})
    return joined


def year_2010() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    frame = excel(raw.cached(SOURCE, url(2010, "candidates")).path).slice(1)
    turnout = excel(raw.cached(SOURCE, url(2010, "turnout")).path).slice(1)
    turnout = pl.concat(
        [
            turnout.select(
                round=pl.lit(round_, pl.Int8),
                teryt=pl.col("Gmina").str.zfill(6),
                electors=number(columns[0]),
                cast=number(columns[1]),
                printed=number(columns[2]),
                invalid=number(columns[3]),
            ).filter(pl.col("printed").is_not_null())
            for round_, columns in (
                (1, ("I tura", "__UNNAMED__7", "__UNNAMED__8", "__UNNAMED__9")),
                (2, ("II tura", "__UNNAMED__15", "__UNNAMED__16", "__UNNAMED__17")),
            )
        ]
    )
    rounds = []
    for round_, (flag, votes) in enumerate((("I tura", "__UNNAMED__21"), ("II tura", "__UNNAMED__24")), 1):
        day = pl.col("__UNNAMED__26").str.slice(0, 10).str.to_date() if round_ == 2 else pl.lit(ELECTIONS[2010][0])
        rounds.append(
            frame.filter(pl.col(votes).is_not_null()).select(
                round=pl.lit(round_, pl.Int8),
                teryt=pl.col("TERYT").str.zfill(6),
                office=pl.col("Urząd").replace_strict(OFFICES),
                title=pl.lit(None, pl.String),
                label=pl.col("Gmina"),
                pos=pl.col("Pozycja").cast(pl.Int64),
                name=pl.concat_str([pl.col("Nazwisko").str.to_uppercase(), "Imiona"], separator=" "),
                committee=pl.col("Komitet"),
                sex=pl.col("Płec").replace_strict(SEX, default=None),
                votes=number(votes),
                against=pl.lit(None, pl.Int64),
                elected=pl.col(flag) == "T",
                advanced=pl.col(flag) == "2",
                day=day,
            )
        )
    return accounted(2010, pl.concat(rounds), turnout), empty_units(), empty_votes()


def year_2006() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    frame = excel(raw.cached(SOURCE, url(2006, "units")).path)
    rows = frame.select(
        round=pl.col("Tura").cast(pl.Int8),
        teryt=pl.col("TERYT").str.zfill(6),
        commission=pl.col("Nr obw."),
        office=pl.col("Urząd").replace_strict(OFFICES),
        label=pl.col("Gmina"),
        pos=pl.col("Nr kand.").cast(pl.Int64),
        name=pl.concat_str([pl.col("Nazwisko").str.to_uppercase(), "Imiona"], separator=" "),
        committee=pl.col("nazwa kom."),
        sex=pl.col("Płeć").replace_strict(SEX, default=None),
        electors=number("L. upr."),
        cast=number("Gł. odd."),
        valid=number("Ważne"),
        votes=number("Gł. na kand."),
        against=number("Gł. przeciw"),
        seat=pl.col("Mandat"),
    )
    units = rows.select(
        "round", "teryt", "commission", pl.lit(None, pl.String).alias("seat"), "electors", "cast", "valid"
    ).unique(["round", "teryt", "commission"], keep="first", maintain_order=True)
    votes = rows.select("round", "teryt", "commission", "pos", "votes", "against")
    totals = units.group_by("round", "teryt").agg(pl.col("valid").sum())
    candidates = (
        rows.group_by("round", "teryt", "pos")
        .agg(
            pl.col("office", "label", "name", "committee", "sex", "seat").first(),
            pl.col("votes").sum(),
            pl.when(pl.col("against").is_not_null().any()).then(pl.col("against").sum()).alias("against"),
        )
        .join(totals, on=["round", "teryt"])
        .with_columns(
            title=pl.lit(None, pl.String),
            elected=(pl.col("seat") == "T"),
            advanced=(pl.col("seat") == "2") & (pl.col("round") == 1),
            day=pl.when(pl.col("round") == 1).then(pl.lit(ELECTIONS[2006][0])).otherwise(pl.lit(ELECTIONS[2006][1])),
        )
    )
    return candidates, units, votes


def year_2002() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    frame = zipped(2002, "wojt2002.xls")
    rows = frame.select(
        round=pl.col("Tura").cast(pl.Int8),
        teryt=pl.col("TERYT").str.zfill(6),
        office=pl.col("Urząd").replace_strict(OFFICES),
        title=pl.lit(None, pl.String),
        label=pl.col("Gmina"),
        pos=pl.col("Nr poz.").cast(pl.Int64),
        name=pl.concat_str(
            [pl.col("Nazwisko").str.to_uppercase(), "1. Imie", "2. Imie"], separator=" ", ignore_nulls=True
        ),
        committee=pl.col("Komitet wyborczy"),
        sex=pl.col("Plec").replace_strict(SEX, default=None),
        votes=number("Gł. na kand."),
        against=pl.lit(None, pl.Int64),
        valid=number("Ważne"),
        electors=number("Upr."),
        cast=number("Odd."),
        elected=pl.col("Wybrany") == "T",
        day=pl.when(pl.col("Tura") == "1").then(pl.lit(ELECTIONS[2002][0])).otherwise(pl.lit(ELECTIONS[2002][1])),
    )
    second = rows.filter(pl.col("round") == 2)
    rows = rows.with_columns(
        advanced=(pl.col("round") == 1)
        & pl.concat_str("teryt", "name").is_in(pl.concat_str(second["teryt"], second["name"]).implode())
    )
    return rows, empty_units(), empty_votes()


def empty_units() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "round": pl.Int8,
            "teryt": pl.String,
            "commission": pl.String,
            "seat": pl.String,
            "electors": pl.Int64,
            "cast": pl.Int64,
            "valid": pl.Int64,
        }
    )


def empty_votes() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "round": pl.Int8,
            "teryt": pl.String,
            "commission": pl.String,
            "pos": pl.Int64,
            "votes": pl.Int64,
            "against": pl.Int64,
        }
    )


READERS = {
    2024: year_2024,
    2018: year_2018,
    2014: year_2014,
    2010: year_2010,
    2006: year_2006,
    2002: year_2002,
}


def parse() -> None:
    """Parse the years in WBP_YEAR (every year when it is unset), then build the package pl.kbw_wbp/all from every
    year's parsed tables."""
    years = [int(y) for y in os.environ["WBP_YEAR"].split(",")] if "WBP_YEAR" in os.environ else list(READERS)
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in years:
        candidates, units, votes = READERS[year]()
        candidates = candidates.with_columns(
            pl.lit(None, pl.Int64).alias(column) for column in ("electors", "cast") if column not in candidates.columns
        )
        candidates = candidates.select(list(CANDIDATES_SCHEMA)).cast(pl.Schema(CANDIDATES_SCHEMA))
        candidates.write_parquet(PARSED / f"{year}-candidates.parquet")
        units.write_parquet(PARSED / f"{year}-units.parquet")
        votes.write_parquet(PARSED / f"{year}-votes.parquet")
        contests = candidates.select("round", "teryt").unique()
        print(
            year,
            "contests",
            contests.filter(pl.col("round") == 1).height,
            contests.filter(pl.col("round") == 2).height,
            "candidates",
            candidates.height,
            "elected",
            int(candidates["elected"].sum()),
            "units",
            units.height,
            "votes",
            votes.height,
        )
    print(build())


METHOD = (
    "Candidate votes for the gmina as the Krajowe Biuro Wyborcze's candidate file prints them, with valid votes as "
    "the file prints them for the gmina."
)
UNITS_METHOD = (
    " Polling-district (obwód głosowania) figures are as the Krajowe Biuro Wyborcze's polling-district file prints "
    "them, and the gmina's electors and ballots are their sums; ballots are those taken from the ballot box, and "
    "invalid is the ballots less the valid votes. Polling districts are described by addresses, not mapped, so they "
    "have no area."
)
SYSTEM = (
    "The wójt, burmistrz, or prezydent miasta is elected by direct vote. A candidate who wins more than half of the "
    "valid votes is elected; otherwise the two candidates with the most votes face each other in a second ballot "
    "fourteen days later, and the one with more votes wins (Ustawa o bezpośrednim wyborze wójta, burmistrza i "
    "prezydenta miasta of 20 June 2002, then Kodeks wyborczy, arts. 473-474)."
)
SINGLE = (
    "Where only one candidate stands, voters answer for or against that candidate, who is elected on more than half "
    "of the valid votes; otherwise the gmina council chooses the head (Kodeks wyborczy, art. 474 § 2)."
)
GMINA_ACCOUNTING = (
    " Electors, ballots cast (głosy oddane), and valid votes are as the file prints them for the gmina, and invalid is "
    "the ballots cast less the valid votes."
)
TURNOUT = {
    2014: (
        " Electors, ballots, and valid votes come from the Krajowe Biuro Wyborcze's gmina statistics for the round "
        "(samo2014-statystyki-według-gmin.zip), whose valid votes equal the candidate file's; ballots are the valid "
        "ballot papers taken from the box (karty ważne), and invalid is the ballots less the valid votes, which is "
        "the invalid votes the table prints."
    ),
    2010: (
        " Electors (upr.), ballots cast (gł. odd.), and valid votes come from the Krajowe Biuro Wyborcze's gmina "
        "turnout file (2010-frekw-wbp-gm.xls), and invalid is the ballots cast less the valid votes, which is the "
        "invalid votes the file prints."
    ),
}
NO_AGAINST = " The candidate file prints only the votes for the sole candidate, so votes against are not stored."


def given_first(text: str) -> str:
    tokens = text.split()
    cut = next((index for index, token in enumerate(tokens) if not token.isupper()), len(tokens))
    if cut in (0, len(tokens)):
        return text
    return " ".join(tokens[cut:] + tokens[:cut])


def bare(label: str) -> str:
    text = re.sub(r"^(m\. st\.|m\.|gm\.|miasto|gmina)\s+", "", label.strip(), flags=re.IGNORECASE)
    return re.sub(r",\s*(m\.|gm\.)$", "", text).strip()


def local(committee: str) -> bool:
    upper = committee.upper()
    return upper.startswith(("KOMITET WYBORCZY WYBORCÓW", "KWW"))


KEY = f"{SOURCE}/all"
SUMMARY = (
    "Direct elections of wójts, burmistrzes, and city presidents, 2002 to 2024, both rounds, by gmina and (2006, 2018, "
    "2024) polling district, with candidates, committees, and the elected heads, from the Krajowe Biuro Wyborcze's "
    "files"
)


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package pl.kbw_wbp/all from every year's parsed tables. It reads the database, through Draft, so the
    rows it rebuilds keep the ids the changeset path gave them."""
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="PL")
        cs.accept(
            "accounting.votes_valid",
            "In the single-candidate contests of 2002 (96) and 2010 (303) the candidate file prints only the votes for "
            "the sole candidate, not those against, so the votes stored fall short of the valid votes, which count "
            "both; both are stored as printed.",
        )

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
        for year, files in FILES.items():
            for kind in files:
                cs.artifact(raw.cached(SOURCE, url(year, kind)), source)
        if conn.execute("select 1 from scheme where code = 'pl.obwod'").fetchone() is None:
            raise ValueError("publish pl.kbw_sejm first")

        systems = {
            "two_round": cs.key("pl.electoral_system", "wbp"),
            "single": cs.key("pl.electoral_system", "wbp-single"),
        }
        cs.add(
            "electoral_system",
            id=systems["two_round"],
            polity_id=poland,
            rules={"formula": "two_round", "majority": 0.5, "advance": 2},
            rules_version=1,
        )
        name(systems["two_round"], "Polish municipal head two-round system")
        cs.add("note", entity_id=systems["two_round"], kind="methodology", body_md=SYSTEM)
        cs.add(
            "electoral_system", id=systems["single"], polity_id=poland, rules={"formula": "referendum"}, rules_version=1
        )
        name(systems["single"], "Polish municipal head vote on a sole candidate")
        cs.add("note", entity_id=systems["single"], kind="methodology", body_md=SINGLE)

        prg: dict[str, tuple[UUID, str, str]] = {}
        for code, area, text in conn.execute(
            """select c.code, a.id, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred
               where a.scheme_code = 'pl.gmina'"""
        ).fetchall():
            prg[code[:6]] = (area, text, code[6:])
        # Gminas the PRG lacked when this pipeline first wrote them (dissolved ones, such as Ostrowice) keep the
        # polities it minted for them then, with no district area, rather than move to the PRG's later outline.
        for teryt in [teryt for teryt in prg if ("pl.polity", f"gmina:{teryt}") in cs.own_keys]:
            del prg[teryt]
        taken = {
            path: entity for path, entity in conn.execute("select path, entity_id from slug where path like 'pl/%%'")
        }
        paths = {entity: path for path, entity in taken.items()}

        unit_rows: dict[UUID, dict[str, str | None]] = {}
        values: list[tuple[str, str, str | None, int, int]] = []
        gmina_units: dict[str, UUID] = {}

        def gmina_unit(code: str) -> UUID:
            if code not in gmina_units:
                unit = gmina_units[code] = cs.unit("pl.gmina", code)
                area = prg.get(code, (None,))[0]
                unit_rows[unit] = {
                    "scheme_code": "pl.gmina",
                    "code": code,
                    "parent_id": None,
                    "area_relation": "equals" if area is not None else "unlocated",
                    "area_id": None if area is None else str(area),
                    "kind": "standard",
                }
            return gmina_units[code]

        def put(result_set: UUID, unit: UUID, option: UUID | None, measure: int, value: int | None) -> None:
            if value is not None:
                values.append(
                    (str(result_set), str(unit), None if option is None else str(option), measure, int(value))
                )

        # Each entity is written once, from the first year (newest first, the order the changeset path wrote them
        # in) that names it, so a party shared by several years keeps the label that first named it.
        added: set[UUID] = set()
        parties: dict[tuple[str, str], UUID] = {}

        def party_for(committee: str, teryt: str, polity: UUID) -> UUID:
            label = " ".join(committee.split())
            scoped = local(label)
            tag = (teryt if scoped else "", label)
            if tag in parties:
                return parties[tag]
            party = parties[tag] = cs.key("pl.party", f"{teryt}:{slug(label)}" if scoped else slug(label))
            if party in cs.minted and party not in added:
                added.add(party)
                cs.add(
                    "party",
                    id=party,
                    kind="independent_group" if scoped else "party",
                    polity_id=polity if scoped else poland,
                )
                name(party, label, "pl")
            return party

        polities: dict[str, tuple[UUID, str, str]] = {}

        def polity_for(teryt: str, label: str) -> tuple[UUID, str, str]:
            if teryt in polities:
                return polities[teryt]
            known = prg.get(teryt)
            polity = (
                cs.key("pl.prg.polity", teryt + known[2])
                if known is not None
                else cs.key("pl.polity", f"gmina:{teryt}")
            )
            town = known[1] if known is not None else bare(label)
            urban = known[2] == "1" if known is not None else label.startswith("m.") or label.endswith(", m.")
            english = town if urban else f"Gmina {town}"
            # The English name of a gmina the PRG lacks comes from each year's label, as it did on the changeset
            # path, so it names that year's elections; the polity itself is written once, from the newest year.
            if polity in cs.minted and polity not in added:
                added.add(polity)
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, english)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"PL-{teryt[:2]}"))
                if known is not None:
                    cs.add("polity_territory", polity_id=polity, area_id=known[0])
                path = f"pl/{slug(VOIVODESHIPS[teryt[:2]][0])}/{slug(english)}"
                if taken.get(path, polity) != polity:
                    path = f"{path}-{teryt}"
                taken[path] = polity
                paths[polity] = path
                cs.add("slug", path=path, entity_id=polity)
            else:
                path = paths[polity]
            polities[teryt] = (polity, path, english)
            return polities[teryt]

        for year in READERS:
            candidates = pl.read_parquet(PARSED / f"{year}-candidates.parquet")
            units = pl.read_parquet(PARSED / f"{year}-units.parquet")
            votes = pl.read_parquet(PARSED / f"{year}-votes.parquet")
            first_day = ELECTIONS[year][0]
            polities.clear()
            contests: dict[str, UUID] = {}
            rounds1: dict[str, tuple[UUID, str]] = {}
            umbrella = cs.key("pl.election", f"{first_day}:local")
            if umbrella in cs.minted:
                raise ValueError(f"publish the {year} pl.kbw_sejmik changeset first")
            totals = units.group_by("round", "teryt").agg(pl.col("electors", "cast", "valid").sum())
            accounts = {(row["round"], row["teryt"]): row for row in totals.iter_rows(named=True)}
            unit_votes: dict[tuple[int, str], list[dict]] = {}
            for row in votes.iter_rows(named=True):
                unit_votes.setdefault((row["round"], row["teryt"]), []).append(row)
            unit_lists: dict[tuple[int, str], list[dict]] = {}
            for row in units.iter_rows(named=True):
                unit_lists.setdefault((row["round"], row["teryt"]), []).append(row)
            contest_count = {1: 0, 2: 0}
            for (round_, teryt), group in sorted(
                candidates.group_by("round", "teryt", maintain_order=True), key=lambda item: (item[0][0], item[0][1])
            ):
                round_, teryt = int(round_), str(teryt)
                rows = group.sort("pos", "name", nulls_last=True).to_dicts()
                day = rows[0]["day"]
                polity, polity_path, english = polity_for(teryt, rows[0]["label"])
                office = cs.key("pl.office", f"wbp:{teryt}")
                if office in cs.minted and office not in added:
                    added.add(office)
                    cs.add("office", id=office, polity_id=polity, kind="mayor")
                    name(office, f"Mayor of {english}")
                    if rows[0]["title"] is not None:
                        name(office, rows[0]["title"], "pl", "native")
                if round_ == 1:
                    election = cs.key("pl.election", f"{day}:local:{teryt}")
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=umbrella,
                        kind="local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{year} {english} local election")
                    election_path = f"{polity_path}/{day}-local"
                    rounds1[teryt] = (election, election_path)
                else:
                    parent, _ = rounds1[teryt]
                    election = cs.key("pl.election", f"{day}:runoff:{teryt}")
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=parent,
                        kind="runoff",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{year} {english} mayoral election, second round")
                    election_path = f"{polity_path}/{day}-runoff"
                cs.add("slug", path=election_path, entity_id=election)
                standing = [row for row in rows if row["votes"] is not None]
                single = len(standing) == 1
                key = f"{day}:{teryt}"
                contest = contests[f"{round_}:{teryt}"] = cs.key(f"{SOURCE}.contest", key)
                contest_count[round_] += 1
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=prg[teryt][0] if teryt in prg else None,
                    magnitude=1,
                    round_number=round_,
                    previous_round_id=contests.get(f"1:{teryt}") if round_ == 2 else None,
                    electoral_system_id=systems["single" if single else "two_round"],
                    default_result_kind="counted",
                )
                name(contest, f"{year} {english} mayoral election" + (", second round" if round_ == 2 else ""))
                cs.add("slug", path=f"{election_path}/mayor", entity_id=contest)
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
                note = METHOD + (UNITS_METHOD if (round_, teryt) in accounts else "")
                if (round_, teryt) not in accounts and rows[0]["electors"] is not None and year not in TURNOUT:
                    note += GMINA_ACCOUNTING
                note += TURNOUT.get(year, "")
                if single and standing[0]["against"] is None:
                    note += NO_AGAINST
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                gmina = gmina_unit(teryt)
                put(result_set, gmina, None, VALID, rows[0]["valid"])
                account = accounts.get((round_, teryt)) or (rows[0] if rows[0]["electors"] is not None else None)
                if account is not None:
                    put(result_set, gmina, None, REGISTERED, account["electors"])
                    put(result_set, gmina, None, BALLOTS, account["cast"])
                    if account["cast"] is not None and rows[0]["valid"] is not None:
                        put(result_set, gmina, None, INVALID, account["cast"] - rows[0]["valid"])
                by_pos: dict[int, tuple[UUID, UUID | None]] = {}
                winners = 0
                for position, row in enumerate(rows, 1):
                    tag = f"{key}:{row['pos'] if row['pos'] is not None else slug(row['name'])}"
                    ballot = display(given_first(row["name"]), "pl")
                    person = cs.key(f"{SOURCE}.person", f"{year}:{teryt}:{slug(row['name'])}")
                    if person in cs.minted and person not in added:
                        added.add(person)
                        cs.add("person", id=person, gender=row["sex"])
                        name(person, ballot)
                    party = party_for(row["committee"], teryt, polity) if row["committee"] else None
                    if single and row["votes"] is not None:
                        yes = cs.key(f"{SOURCE}.option", f"{key}:yes")
                        no = cs.key(f"{SOURCE}.option", f"{key}:no")
                        cs.add(
                            "contest_option",
                            id=yes,
                            contest_id=contest,
                            kind="answer",
                            party_id=party,
                            ballot_position=1,
                            ballot_label="Tak",
                        )
                        cs.add(
                            "contest_option",
                            id=no,
                            contest_id=contest,
                            kind="answer",
                            ballot_position=2,
                            ballot_label="Nie",
                        )
                        option, against = yes, no
                    else:
                        option = cs.key(f"{SOURCE}.option", tag)
                        against = None
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=party,
                            ballot_position=row["pos"] if row["pos"] is not None else position,
                            ballot_label=ballot,
                        )
                    if row["pos"] is not None:
                        by_pos[row["pos"]] = (option, against)
                    candidacy = cs.key(f"{SOURCE}.candidacy", tag)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        is_independent=False,
                    )
                    put(result_set, gmina, option, VOTES, row["votes"])
                    if against is not None:
                        put(result_set, gmina, against, VOTES, row["against"])
                    if row["votes"] is None:
                        kind = "withdrew"
                    elif row["elected"]:
                        kind = "elected"
                        winners += 1
                    elif row["advanced"]:
                        kind = "advanced"
                    elif round_ == 1 and not single and any(other["advanced"] for other in rows):
                        kind = "eliminated"
                    else:
                        kind = "lost"
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind=kind,
                        seats=1 if kind == "elected" else None,
                    )
                if winners > 1 or (winners == 0 and not single and round_ == 2):
                    raise ValueError(f"{key}: {winners} heads elected")
                for unit_row in unit_lists.get((round_, teryt), []):
                    code = f"{day}:{teryt}:{unit_row['commission']}"
                    unit = cs.unit("pl.obwod", code)
                    unit_rows[unit] = {
                        "scheme_code": "pl.obwod",
                        "code": code,
                        "parent_id": str(gmina),
                        "area_relation": "unlocated",
                        "area_id": None,
                        "kind": "standard",
                    }
                    put(result_set, unit, None, REGISTERED, unit_row["electors"])
                    put(result_set, unit, None, BALLOTS, unit_row["cast"])
                    put(result_set, unit, None, VALID, unit_row["valid"])
                    if unit_row["cast"] is not None and unit_row["valid"] is not None:
                        put(result_set, unit, None, INVALID, unit_row["cast"] - unit_row["valid"])
                for vote in unit_votes.get((round_, teryt), []):
                    if vote["pos"] not in by_pos:
                        if vote["votes"] or vote["against"]:
                            raise ValueError(f"{key}: votes for candidate {vote['pos']} not in the candidate file")
                        continue
                    option, against = by_pos[vote["pos"]]
                    unit = cs.unit("pl.obwod", f"{day}:{teryt}:{vote['commission']}")
                    put(result_set, unit, option, VOTES, vote["votes"])
                    if against is not None:
                        put(result_set, unit, against, VOTES, vote["against"])
            print(year, "contests", contest_count)

        minted = [
            {"id": str(unit), "polity_id": str(poland), **row} for unit, row in unit_rows.items() if unit in cs.minted
        ]
        if minted:
            cs.frame(
                "reporting_unit",
                pl.DataFrame(minted, schema_overrides={"parent_id": pl.String, "area_id": pl.String}),
            )
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
        return cs.write()
