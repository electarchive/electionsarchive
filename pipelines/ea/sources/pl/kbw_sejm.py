import io
import zipfile
from collections import Counter
from datetime import date

import polars as pl

from ea import raw

SOURCE = "pl.kbw_sejm"
BASE = "https://danewyborcze.kbw.gov.pl/dane"
PARSED = raw.parsed(SOURCE)
ELECTIONS = {2023: date(2023, 10, 15)}
FILES = {
    2023: {
        "districts": "sejmsenat/okregi_sejm_csv.zip",
        "lists": "sejmsenat/wykaz_list_sejm_csv.zip",
        "candidates": "sejmsenat/kandydaci_sejm_csv.zip",
        "units": "sejmsenat/obwody_glosowania_csv.zip",
        "unit_lists": "sejmsenat/wyniki_gl_na_listy_po_obwodach_sejm_csv.zip",
        "unit_candidates": "sejmsenat/wyniki_gl_na_kandydatow_po_obwodach_sejm_csv.zip",
        "district_lists": "sejmsenat/wyniki_gl_na_listy_po_okregach_sejm_csv.zip",
    }
}
ACCOUNTING = {
    "Liczba wyborców uprawnionych do głosowania": "electors",
    "Liczba wyborców, którym wydano karty do głosowania w lokalu wyborczym oraz w głosowaniu korespondencyjnym (łącznie)": "issued",
    "Liczba kart wyjętych z urny": "cast",
    "Liczba kart nieważnych": "invalid_ballots",
    "Liczba kart ważnych": "valid_ballots",
    "Liczba głosów nieważnych": "invalid",
    "Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów": "valid",
}


def url(year: int, kind: str) -> str:
    return f"{BASE}/{year}/{FILES[year][kind]}"


def fetch() -> None:
    for year in ELECTIONS:
        for kind in FILES[year]:
            raw.fetch(SOURCE, url(year, kind), suffix=".zip")


def tables(year: int, kind: str) -> dict[str, pl.DataFrame]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url(year, kind)).path)
    frames = {}
    for member in archive.namelist():
        if not member.endswith(".csv"):
            continue
        frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0)
        frame = frame.rename({column: column.lstrip("﻿") for column in frame.columns})
        frames[member] = frame.with_columns(pl.col(pl.String).str.strip_chars())
    return frames


def table(year: int, kind: str) -> pl.DataFrame:
    [frame] = tables(year, kind).values()
    return frame


def count(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Int64)


def gmina(column: str) -> pl.Expr:
    return pl.coalesce(pl.col(column).str.zfill(6), pl.col("Powiat")).alias("gmina")


def dhondt(votes: dict[int, int], seats: int) -> Counter[int]:
    quotients = sorted(
        ((count / divisor, number) for number, count in votes.items() for divisor in range(1, seats + 1)), reverse=True
    )
    return Counter(number for _, number in quotients[:seats])


def parse_2023() -> dict[str, pl.DataFrame]:
    year = 2023
    unit = [gmina("TERYT Gminy"), count("Nr komisji").alias("commission")]
    results = table(year, "unit_lists")
    committees = results.columns[
        results.columns.index("Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów") + 1 :
    ]
    units = results.select(
        *unit,
        count("Nr okręgu").alias("district"),
        pl.col("Siedziba").alias("seat"),
        *(count(column).alias(name) for column, name in ACCOUNTING.items()),
    )
    if units.select("gmina", "commission").is_duplicated().any():
        raise ValueError("two polling districts share a gmina and number")
    if (units["valid"] + units["invalid"] != units["valid_ballots"]).any():
        raise ValueError("valid and invalid votes do not add up to the valid ballots")
    lists = table(year, "lists").select(
        count("Numer okręgu").alias("district"),
        count("Nr listy").alias("list"),
        pl.col("Nazwa Komitetu").alias("committee"),
        count("Liczba głosów").alias("votes"),
        (pl.col("Udział w podziale mandatów") == "Tak").alias("qualified"),
        count("Liczba mandatów").alias("seats"),
    )
    if lists.select("district", "committee").is_duplicated().any():
        raise ValueError("a committee holds two lists in one district")
    unit_votes = (
        results.select(*unit, *(count(committee) for committee in committees))
        .unpivot(index=["gmina", "commission"], variable_name="committee", value_name="votes")
        .filter(pl.col("votes").is_not_null())
        .join(units.select("gmina", "commission", "district"), on=["gmina", "commission"])
    )
    stray = unit_votes.join(lists, on=["district", "committee"], how="anti")
    if stray.height:
        raise ValueError(f"{stray.height} polling-district votes for a committee without a list in the district")
    unit_votes = unit_votes.join(lists.select("district", "committee", "list"), on=["district", "committee"]).select(
        "gmina", "commission", "list", "votes"
    )
    summed = unit_votes.group_by("gmina", "commission").agg(pl.col("votes").sum())
    off = units.join(summed, on=["gmina", "commission"]).filter(pl.col("votes") != pl.col("valid"))
    if off.height:
        raise ValueError(f"{off.height} polling districts: list votes differ from the valid votes")
    by_district = (
        unit_votes.join(units.select("gmina", "commission", "district"), on=["gmina", "commission"])
        .group_by("district", "list")
        .agg(got=pl.col("votes").sum())
    )
    off = lists.join(by_district, on=["district", "list"], how="full", coalesce=True).filter(
        pl.col("votes").fill_null(0) != pl.col("got").fill_null(0)
    )
    blank = units.filter(pl.col("valid").is_null())
    for row in blank.iter_rows(named=True):
        print(
            f"{year} district {row['district']}: polling district {row['gmina']} {row['commission']} prints no counts"
        )
    gap = off.filter(~pl.col("district").is_in(blank["district"].implode()))
    if gap.height:
        raise ValueError(f"{gap.height} district lists differ from the polling-district sums:\n{gap}")
    if off.height:
        print(f"{year}: the blank polling districts hold {(off['votes'] - off['got']).sum()} list votes")
    districts = table(year, "districts").select(
        count("Numer okręgu").alias("district"),
        count("Liczba mandatów").alias("magnitude"),
        pl.col("Siedziba OKW").alias("seat"),
        pl.col("Opis granic").alias("boundary"),
    )
    if districts["magnitude"].sum() != 460 or lists["seats"].sum() != 460:
        raise ValueError("seats do not sum to 460")
    for row in districts.iter_rows(named=True):
        part = lists.filter(pl.col("district") == row["district"], pl.col("qualified"))
        won = dhondt(dict(zip(part["list"], part["votes"])), row["magnitude"])
        if any(won[list_] != seats for list_, seats in zip(part["list"], part["seats"])):
            print(f"{year} district {row['district']}: D'Hondt does not give the declared seats")
    candidates = table(year, "candidates").select(
        count("Nr okręgu").alias("district"),
        count("Nr listy").alias("list"),
        count("Pozycja na liście").alias("position"),
        pl.col("Nazwisko i imiona").alias("name"),
        pl.col("Płeć").alias("sex"),
        pl.col("Zawód").alias("occupation"),
        pl.col("Miejsce zamieszkania").alias("residence"),
        pl.col("Przynależność do partii").alias("membership"),
        pl.col("Poparcie").alias("support"),
        count("Liczba głosów").alias("votes"),
        (pl.col("Czy przyznano mandat") == "Tak").alias("elected"),
    )
    seated = candidates.group_by("district", "list").agg(pl.col("elected").sum().alias("won"))
    off = lists.filter(pl.col("seats") > 0).join(seated, on=["district", "list"], how="left")
    off = off.filter(pl.col("won").fill_null(0) != pl.col("seats"))
    if off.height or candidates["elected"].sum() != 460:
        raise ValueError(f"elected candidates differ from list seats:\n{off}")
    listed = candidates.group_by("district", "list").agg(
        pl.col("votes").sum(), struck=pl.col("position").max() - pl.len()
    )
    off = lists.join(listed, on=["district", "list"], how="left").filter(pl.col("votes") != pl.col("votes_right"))
    if off.filter((pl.col("votes") < pl.col("votes_right")) | (pl.col("struck") == 0)).height:
        raise ValueError(f"candidate votes differ from list votes:\n{off}")
    for row in off.iter_rows(named=True):
        residual = row["votes"] - row["votes_right"]
        print(f"{year} district {row['district']} list {row['list']}: {residual} votes for struck candidates")
    pieces = []
    for member, frame in tables(year, "unit_candidates").items():
        district = int(member.rsplit("okreg_", 1)[1].split("_", 1)[0])
        ballot = candidates.filter(pl.col("district") == district).sort("list", "position")
        names = frame.columns[frame.columns.index("Liczba uwzględnionych komisji") + 1 :]
        if len(names) != ballot.height:
            raise ValueError(f"district {district}: {len(names)} candidate columns for {ballot.height} candidates")
        for header, row in zip(names, ballot.iter_rows(named=True)):
            surname, given = header.split(" - ", 1)[0].split(" ", 1)[0], row["name"].split(" ")[0]
            if surname.split("-")[0] not in row["name"] or given not in header:
                raise ValueError(f"district {district}: column {header!r} is not {row['name']!r}")
        piece = frame.select(*unit, *(count(header).alias(str(i)) for i, header in enumerate(names)))
        piece = piece.unpivot(index=["gmina", "commission"], variable_name="i", value_name="votes")
        keys = ballot.select("list", "position").with_row_index("i").with_columns(pl.col("i").cast(pl.String))
        pieces.append(piece.join(keys, on="i").drop("i").with_columns(district=pl.lit(district, pl.Int64)))
    candidate_votes = pl.concat(pieces).filter(pl.col("votes").is_not_null())
    got = candidate_votes.group_by("district", "list", "position").agg(got=pl.col("votes").sum())
    off = candidates.join(got, on=["district", "list", "position"], how="left").filter(
        pl.col("votes") != pl.col("got").fill_null(0)
    )
    if off.filter(~pl.col("district").is_in(blank["district"].implode())).height:
        raise ValueError(f"{off.height} candidates: polling-district votes differ from their totals")
    got = candidate_votes.group_by("gmina", "commission", "list").agg(got=pl.col("votes").sum())
    off = unit_votes.join(got, on=["gmina", "commission", "list"], how="left").filter(
        pl.col("votes") != pl.col("got").fill_null(0)
    )
    if off.height:
        print(f"{year}: {off.height} polling-district lists whose candidate votes differ from the list votes")
    registry = table(year, "units").select(
        gmina("TERYT gminy"),
        count("Numer").alias("commission"),
        pl.col("Typ obwodu").alias("kind"),
        pl.col("Typ obszaru").alias("area"),
        pl.col("Opis granic").alias("boundary"),
    )
    units = units.join(registry, on=["gmina", "commission"], how="left")
    return {
        "districts": districts,
        "lists": lists,
        "candidates": candidates,
        "units": units,
        "unit_votes": unit_votes,
        "candidate_votes": candidate_votes,
    }


PARSERS = {2023: parse_2023}


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[pl.DataFrame]] = {}
    for year, day in ELECTIONS.items():
        for name, frame in PARSERS[year]().items():
            out.setdefault(name, []).append(frame.with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(day)))
    for name, frames in out.items():
        frame = pl.concat(frames, how="diagonal")
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")
