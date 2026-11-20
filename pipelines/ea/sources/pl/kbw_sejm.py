import html
import io
import os
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

import polars as pl
import shapely

from ea import package, raw
from ea.changeset import slug
from ea.names import display
from ea.package import Draft
from ea.store import connect

SOURCE = "pl.kbw_sejm"
KEY = f"{SOURCE}/all"
VERSION = "1"
SUMMARY = (
    "Sejm elections of 1991-2023 by constituency, gmina, and polling district, with committees, candidates, declared "
    "seats, elected members, and the 2001 constituency outlines, from the Krajowe Biuro Wyborcze's data files"
)
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze: wybory do Sejmu"
PAGE = "https://danewyborcze.kbw.gov.pl/"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, PREFERENCE = 1, 2, 3, 4, 5, 7
ITEMS = {
    "PRAWO I SPRAWIEDLIWOŚĆ": "Q156874",
    "KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ": "Q61568052",
    "PLATFORMA OBYWATELSKA RP": "Q156868",
    "WYBORCÓW PLATFORMA OBYWATELSKA": "Q156868",
    "„KUKIZ'15”": "Q20740347",
    "POLSKIE STRONNICTWO LUDOWE": "Q218477",
    "UNIA WOLNOŚCI": "Q2307945",
    "UNIA POLITYKI REALNEJ": "Q1270771",
}
METHOD = (
    "List votes, declared seats, and elected members by constituency from the Krajowe Biuro Wyborcze's data files "
    "for the Sejm. Accounting is summed from the polling districts, whose counts the file prints: voters entitled to "
    "vote, ballots taken from the urn, and valid votes for lists; invalid is the ballots cast less the valid votes "
    "for lists, which counts both invalid ballots and invalid votes on valid ballots. Seats go to lists that passed "
    "the threshold (5 percent for a committee, 8 percent for a coalition, none for national minorities) by the "
    "D'Hondt method, and each elected member is the candidate the file marks as elected."
)
GMINA_METHOD = (
    "List votes and accounting summed from the polling districts of each gmina, with votes from abroad and ships "
    "each in one unit. Polling districts whose file rows print no counts are left out."
)
OBWOD_METHOD = (
    "List votes and accounting by polling district (obwód głosowania) as the file prints them. Polling districts "
    "are described by streets and addresses, not mapped, so they have no area."
)
SUMMED = (
    " The files print no list votes by constituency for this election, so they are summed from the polling "
    "districts; they agree with the candidate totals the candidate file prints, apart from votes for candidates "
    "struck from a list."
)
SUMMED_YEARS = {2007, 2011}
EARLY_METHOD = (
    "List votes, declared seats, and elected members by constituency from the Krajowe Biuro Wyborcze's data files "
    "for the Sejm. Accounting is summed from the polling districts, whose counts the file prints: voters entitled to "
    "vote, votes cast, and valid votes; invalid is the votes cast less the valid votes. Each elected member is the "
    "candidate the file marks as elected in the constituency."
)
NATION_METHOD = (
    "Votes for each national list are the votes its committee's constituency lists won across the country, summed "
    "from the constituency totals the file prints, with accounting summed from the polling districts. Declared seats "
    "are the national list members the candidate file marks as elected from the national list, and positions are "
    "the national list positions it prints."
)
LINKED = (
    " For 1991 only the constituency lists linked to the national list count, which the file shows as the "
    "constituency lists with a candidate on the national list; these sums equal the Państwowa Komisja Wyborcza's "
    "national list figures (Monitor Polski 1991 nr 41 poz. 288)."
)
BASE = "https://danewyborcze.kbw.gov.pl/dane"
NATIONAL_BLOCS_1991 = {
    41: "41-49-58-62",
    49: "41-49-58-62",
    58: "41-49-58-62",
    62: "41-49-58-62",
    20: "20-48",
    48: "20-48",
}
MINORITIES_1991 = {20, 48, 55}
PARSED = raw.parsed(SOURCE)
ELECTIONS = {
    1991: date(1991, 10, 27),
    1993: date(1993, 9, 19),
    1997: date(1997, 9, 21),
    2001: date(2001, 9, 23),
    2005: date(2005, 9, 25),
    2007: date(2007, 10, 21),
    2011: date(2011, 10, 9),
    2015: date(2015, 10, 25),
    2019: date(2019, 10, 13),
    2023: date(2023, 10, 15),
}
FILES: dict[int, dict[str, str | tuple[str, ...]]] = {
    1991: {
        "districts": "sejm/okreg1991sejm.xls",
        "lists": "sejm/listyokr1991.xls",
        "candidates": "sejm/kandsejm1991kom.xls",
        "unit_lists": "sejm/1991-listy-obwody.xls",
        "district_lists": "sejm/sejm1991-lis-okr.xls",
    },
    1993: {
        "districts": "sejm/okregi1993.xls",
        "committees": "sejm/kom-glosy-1993.xls",
        "candidates": "sejm/kandsejm1993kom.xls",
        "unit_lists": "sejm/1993-listy-obw.zip",
        "district_lists": "sejm/sejm1993lis-okr.xls",
    },
    1997: {
        "districts": "sejm/sejm1997okregi.xls",
        "candidates": "sejm/kandsejm1997kom.xls",
        "units": "sejm/sejm1997obw.xls",
        "district_lists": "sejm/sejm1997lisokr.zip",
        "lists": "sejm/listy-kandydaci.zip",
    },
    2001: {
        "districts": "sejm/okr%c4%99gi2001sejm.xls",
        "candidates": "sejm/kandsejm2001kom.xls",
        "unit_lists": "sejm/sejm2001-lis-obw.xls",
        "district_lists": "sejm/okr-lis-2001.xls",
    },
    2005: {
        "districts": "sejm/okregi2005sejm.xls",
        "candidates": "sejm/kandsejm2005kom.xls",
        "unit_lists": "sejm/sejm2005-lis-obw.zip",
        "district_lists": "sejm/sejm2005-lis-okr.xls",
    },
    2007: {
        "districts": "sejm/okr%c4%99gi2007sejm.xls",
        "candidates": "sejm/kandsejm2007.xls",
        "unit_lists": "sejm/sejm2007-obw-listy.xls",
    },
    2011: {
        "districts": "sejmsenat/parl2011okr%c4%99gi.xlsx",
        "candidates": "sejmsenat/2011-kand-sejm.xls",
        "unit_lists": "sejmsenat/2011-sejm-gl-lis-obw.xls",
    },
    2015: {
        "candidates": "sejm/2015-kand-sejm.xls",
        "units": "obwody2015parl.xlsx",
        "unit_lists": "sejm/2015-gl-lis-obw.zip",
        "unit_candidates": tuple(f"sejm/wyniki_okr_sejm/{district:02}.xlsx" for district in range(1, 42)),
        "district_lists": "sejm/2015-gl-lis-okr.zip",
    },
    2019: {
        "districts": "sejmsenat/okregi_sejm_csv.zip",
        "lists": "sejmsenat/wykaz_list_sejm_csv.zip",
        "candidates": "sejmsenat/kandydaci_sejm_csv.zip",
        "units": "sejmsenat/obwody_glosowania_csv.zip",
        "unit_lists": "sejmsenat/wyniki_gl_na_listy_po_obwodach_sejm_csv.zip",
        "unit_candidates": "sejmsenat/wyniki_gl_na_kand_po_obwodach_sejm_csv.zip",
        "district_lists": "sejmsenat/wyniki_gl_na_listy_po_okregach_sejm_csv.zip",
    },
    2023: {
        "districts": "sejmsenat/okregi_sejm_csv.zip",
        "lists": "sejmsenat/wykaz_list_sejm_csv.zip",
        "candidates": "sejmsenat/kandydaci_sejm_csv.zip",
        "units": "sejmsenat/obwody_glosowania_csv.zip",
        "unit_lists": "sejmsenat/wyniki_gl_na_listy_po_obwodach_sejm_csv.zip",
        "unit_candidates": "sejmsenat/wyniki_gl_na_kandydatow_po_obwodach_sejm_csv.zip",
        "district_lists": "sejmsenat/wyniki_gl_na_listy_po_okregach_sejm_csv.zip",
    },
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
VALID_COLUMN = "Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów"
# The Państwowa Komisja Wyborcza's notice of the results in Dziennik Ustaw, as the Sejm's ELI service gives its text.
# It prints the national and constituency counts, every list's votes, and every candidate's votes, which the parse
# checks the data files against.
NOTICES = {2023: "https://api.sejm.gov.pl/eli/acts/DU/2023/2234/text.html"}
NOTICE_TITLES = {
    2023: (
        "Obwieszczenie Państwowej Komisji Wyborczej z dnia 17 października 2023 r. o wynikach wyborów do Sejmu "
        "Rzeczypospolitej Polskiej przeprowadzonych w dniu 15 października 2023 r."
    )
}
NOTICE_PUBLISHER = "Państwowa Komisja Wyborcza"
NOTICE_COUNTS = {
    "electors": "Liczba osób uprawnionych do głosowania wyniosła",
    "cast": "Liczba kart do głosowania wyjętych z urn wyniosła",
    "invalid_ballots": "Liczba kart nieważnych wyniosła",
    "valid_ballots": "Liczba kart ważnych wyniosła",
    "invalid": "Liczba głosów nieważnych wyniosła",
    "valid": "Liczba głosów ważnych wyniosła",
}
PREFERENCE_YEARS = {2023}
PREFERENCE_METHOD = (
    " Each candidate's votes are stored as preference votes (measure 7) on a candidate option under the candidate's "
    "list; they are part of the list's votes, not added to them. The file prints every candidate's votes by polling "
    "district, and the constituency totals are the candidate file's, which equal the Państwowa Komisja Wyborcza's "
    "notice of the results (Dz.U. 2023 poz. 2234) for every candidate."
)


def urls(year: int, kind: str) -> list[str]:
    files = FILES[year][kind]
    return [f"{BASE}/{year}/{file}" for file in ([files] if isinstance(files, str) else files)]


def fetch() -> None:
    for year in ELECTIONS:
        for kind in FILES[year]:
            for address in urls(year, kind):
                raw.fetch(SOURCE, address, suffix=Path(address).suffix)
    for address in NOTICES.values():
        raw.fetch(SOURCE, address, suffix=".html")


def number(text: str) -> int:
    return int(text.replace(" ", ""))


def folded(names: pl.Expr) -> pl.Expr:
    """Upper case without diacritics, since the notice drops or adds some of them."""
    plain = names.str.to_uppercase().str.normalize("NFKD").str.replace_all(r"\p{Mn}", "")
    return plain.str.replace_all("Ł", "L", literal=True)


def notice(year: int) -> dict[str, pl.DataFrame]:
    """The notice's counts for the country and each constituency, its list votes, and its candidate votes."""
    text = raw.cached(SOURCE, NOTICES[year]).path.read_text(encoding="utf-8")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    parts = re.split(r" Okręg wyborczy nr (\d+) Oddział 1\. Dane ogólne ", text)
    if len(parts) != 2 * 41 + 1:
        raise ValueError(f"{year} notice: {len(parts) // 2} constituency chapters, not 41")
    counts, lists, candidates = [], [], []

    def figures(scope: int, chunk: str) -> None:
        row: dict[str, int] = {"district": scope}
        for column, label in NOTICE_COUNTS.items():
            found = re.search(re.escape(label) + r" (\d{1,3}(?: \d{3})*)\b", chunk)
            if found is None:
                raise ValueError(f"{year} notice, {scope or 'country'}: no {column!r}")
            row[column] = number(found[1])
        counts.append(row)

    figures(0, parts[0][: parts[0].index("Rozdział 3.")])
    for chapter, chunk in zip(parts[1::2], parts[2::2]):
        district = int(chapter)
        figures(district, chunk[: chunk.index("Oddział 2.")])
        votes = chunk[chunk.index("Oddział 2.") : chunk.index("Oddział 3.")]
        blocks = re.split(
            r" \d+\. Na listę nr (\d+) zgłoszoną przez (.+?) od ?dano (\d{1,3}(?: \d{3})*) głos\w*\. ", votes
        )
        if len(blocks) < 4:
            raise ValueError(f"{year} notice, constituency {district}: no list votes")
        for list_, committee, total, names in zip(blocks[1::4], blocks[2::4], blocks[3::4], blocks[4::4]):
            lists.append({"district": district, "list": int(list_), "committee": committee, "votes": number(total)})
            names = names.removeprefix("Kandydaci z tej listy otrzymali następujące liczby głosów: ")
            rows = re.findall(r"(\d+)\) (\D+?) (\d[\d ]*?|kandydat skreślony)(?= \d+\) | ?$)", names.strip())
            if [int(position) for position, _, _ in rows] != list(range(1, len(rows) + 1)):
                raise ValueError(f"{year} notice, constituency {district} list {list_}: candidates out of order")
            candidates.extend(
                {
                    "district": district,
                    "list": int(list_),
                    "position": int(p),
                    "name": n,
                    "votes": None if v == "kandydat skreślony" else number(v),
                }
                for p, n, v in rows
            )
    national = re.findall(
        r"na list(?:y|ę) nr (\d+) zgłoszon[eą] przez (.+?) od ?dano (\d{1,3}(?: \d{3})*) głos",
        parts[0][parts[0].index("Rozdział 3.") :],
    )
    return {
        "counts": pl.DataFrame(counts),
        "nation": pl.DataFrame(
            [{"list": int(n), "committee": c, "votes": number(v)} for n, c, v in national],
            schema={"list": pl.Int64, "committee": pl.String, "votes": pl.Int64},
        ),
        "lists": pl.DataFrame(lists),
        "candidates": pl.DataFrame(candidates),
    }


def tables(year: int, kind: str) -> dict[str, pl.DataFrame]:
    frames = {}
    for address in urls(year, kind):
        path = raw.cached(SOURCE, address).path
        if path.suffix != ".zip":
            members = {Path(address).name: path.read_bytes()}
        else:
            archive = zipfile.ZipFile(path)
            members = {member: archive.read(member) for member in archive.namelist()}
        for member, body in members.items():
            if member.endswith(".csv"):
                frame = pl.read_csv(io.BytesIO(body), separator=";", infer_schema_length=0)
            elif member.endswith((".xls", ".xlsx")):
                frame = pl.read_excel(io.BytesIO(body), infer_schema_length=0)
            else:
                continue
            frame = frame.rename({column: column.lstrip("\ufeff") for column in frame.columns})
            frames[member] = frame.with_columns(pl.col(pl.String).str.strip_chars().replace("", None))
    return frames


def table(year: int, kind: str) -> pl.DataFrame:
    [frame] = tables(year, kind).values()
    return frame


def count(column: str) -> pl.Expr:
    return pl.col(column).replace(["-", "x"], None).cast(pl.Int64)


PERSON = (
    pl.col("Płeć").alias("sex"),
    pl.col("Zawód").alias("occupation"),
    pl.col("Miejsce zamieszkania").alias("residence"),
    pl.col("Przynależność do partii").alias("membership"),
    pl.col("Poparcie").alias("support"),
    count("Liczba głosów").alias("votes"),
    (pl.col("Czy przyznano mandat") == "Tak").alias("elected"),
)


def gmina(column: str) -> pl.Expr:
    return pl.coalesce(pl.col(column).str.zfill(6), pl.col("Powiat")).alias("gmina")


def dhondt(votes: dict[int, int], seats: int, divisors: list[float] | None = None) -> Counter[int]:
    divisors = divisors or list(range(1, seats + 1))
    quotients = sorted(
        ((count / divisor, number) for number, count in votes.items() for divisor in divisors), reverse=True
    )
    return Counter(number for _, number in quotients[:seats])


def hare(votes: dict[int, int], seats: int) -> Counter[int]:
    total = sum(votes.values())
    won = Counter({number: count * seats // total for number, count in votes.items()})
    remainders = sorted(((count * seats % total, number) for number, count in votes.items()), reverse=True)
    won.update(number for _, number in remainders[: seats - sum(won.values())])
    return won


def pooled(
    votes: dict[int, int], blocs: dict[int, str], seats: int, divisors: list[float] | None = None
) -> Counter[str]:
    groups = Counter()
    for number, count in votes.items():
        groups[f"bloc {blocs[number]}" if blocs.get(number) else f"list {number}"] += count
    names = list(groups)
    won = (
        dhondt(dict(enumerate(groups.values())), seats, divisors)
        if divisors
        else hare(dict(enumerate(groups.values())), seats)
    )
    return Counter({names[group]: share for group, share in won.items()})


def bloc_seats(seats: dict[int, int], blocs: dict[int, str]) -> Counter[str]:
    won = Counter()
    for number, count in seats.items():
        won[f"bloc {blocs[number]}" if blocs.get(number) else f"list {number}"] += count
    return won


def load_2023() -> dict[str, pl.DataFrame]:
    year = 2023
    unit = [gmina("TERYT Gminy"), count("Nr komisji").alias("commission")]
    results = table(year, "unit_lists")
    committees = results.columns[results.columns.index(VALID_COLUMN) + 1 :]
    results = results.select(
        *unit,
        count("Nr okręgu").alias("district"),
        pl.col("Siedziba").alias("seat"),
        *(count(column).alias(name) for column, name in ACCOUNTING.items()),
        *(count(committee) for committee in committees),
    )
    lists = table(year, "lists").select(
        count("Numer okręgu").alias("district"),
        count("Nr listy").alias("list"),
        pl.col("Nazwa Komitetu").alias("committee"),
        count("Liczba głosów").alias("votes"),
        (pl.col("Udział w podziale mandatów") == "Tak").alias("qualified"),
        count("Liczba mandatów").alias("seats"),
    )
    districts = table(year, "districts").select(
        count("Numer okręgu").alias("district"),
        count("Liczba mandatów").alias("magnitude"),
        pl.col("Siedziba OKW").alias("seat"),
        pl.col("Opis granic").alias("boundary"),
    )
    candidates = table(year, "candidates").select(
        count("Nr okręgu").alias("district"),
        count("Nr listy").alias("list"),
        count("Pozycja na liście").alias("position"),
        pl.col("Nazwisko i imiona").alias("name"),
        *PERSON,
    )
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
    registry = table(year, "units").select(
        gmina("TERYT gminy"),
        count("Numer").alias("commission"),
        pl.col("Typ obwodu").alias("kind"),
        pl.col("Typ obszaru").alias("area"),
        pl.col("Opis granic").alias("boundary"),
    )
    return {
        "results": results,
        "lists": lists,
        "districts": districts,
        "candidates": candidates,
        "candidate_votes": pl.concat(pieces),
        "registry": registry,
    }


def teryt(column: str) -> pl.Expr:
    code = pl.col(column)
    return (
        pl.when(code.is_null() | (code == "149901"))
        .then(pl.lit("zagranica"))
        .otherwise(code.str.zfill(6))
        .alias("gmina")
    )


def numbered(column: str) -> str:
    return column.split(" - ", 1)[1]


def seated(lists: pl.DataFrame, candidates: pl.DataFrame) -> pl.DataFrame:
    seats = candidates.group_by("district", "list").agg(pl.col("elected").sum().cast(pl.Int64).alias("seats"))
    lists = lists.join(seats, on=["district", "list"], how="left").with_columns(pl.col("seats").fill_null(0))
    winners = lists.group_by("committee").agg(pl.col("seats").sum() > 0).filter(pl.col("seats"))["committee"]
    named = candidates.select("district", "list", "committee").unique()
    if named.join(lists, on=["district", "list", "committee"], how="anti").height:
        raise ValueError("candidate committees do not match the constituency lists")
    return lists.with_columns(pl.col("committee").is_in(winners.implode()).alias("qualified")).select(
        "district", "list", "committee", "votes", "qualified", "seats"
    )


def okregi(year: int) -> pl.DataFrame:
    sizes = table(year, "districts")
    first, seat, boundary, magnitude = sizes.columns[0], sizes.columns[1], sizes.columns[2], sizes.columns[-1]
    return sizes.filter(pl.col(first).str.contains(r"^\d+$")).select(
        count(first).alias("district"),
        count(magnitude).alias("magnitude"),
        pl.col(seat).alias("seat"),
        pl.col(boundary).alias("boundary"),
    )


def load_2001() -> dict[str, pl.DataFrame]:
    year = 2001
    gender = {"K": "Kobieta", "M": "Mężczyzna"}
    candidates = table(year, "candidates").select(
        count("Okręg").alias("district"),
        count("Lista").alias("list"),
        count("Pozycja").alias("position"),
        pl.concat_str(pl.col("1. imię"), pl.col("2. imię"), pl.col("Nazwisko"), separator=" ", ignore_nulls=True)
        .str.replace_all(r"\s+", " ")
        .alias("name"),
        pl.col("Płeć").replace_strict(gender).alias("sex"),
        pl.col("Zawód").alias("occupation"),
        pl.col("Miejsce\nzamieszkania").alias("residence"),
        pl.col("Partia").alias("membership"),
        pl.lit(None, pl.String).alias("support"),
        count("Głosy").alias("votes"),
        (pl.col("Mandat") == "T").alias("elected"),
        pl.col("Komitet wyborczy").alias("committee"),
    )
    names = {(d, n): c for d, n, c in candidates.select("district", "list", "committee").unique().iter_rows()}

    def label(row: dict[str, object]) -> str | None:
        return names.get((row["district"], int(str(row["column"]).split(" - ", 1)[0])))

    def committees(frame: pl.DataFrame, index: list[str], columns: list[str]) -> pl.DataFrame:
        return (
            frame.select(*index, "district", *(count(column) for column in columns))
            .unpivot(index=[*index, "district"], variable_name="column", value_name="votes")
            .filter(pl.col("votes").is_not_null())
            .with_columns(pl.struct("district", "column").map_elements(label, pl.String).alias("committee"))
            .filter(pl.col("committee").is_not_null() | (pl.col("votes") != 0))
            .group_by(*index, "district", "committee")
            .agg(pl.col("votes").sum())
        )

    results = table(year, "unit_lists")
    columns = results.columns[results.columns.index("Głosy ważne z kart ważnych") + 1 :]
    registry = results.select(
        teryt("Kod gminy"),
        count("Nr obw.").alias("commission"),
        pl.col("Typ").alias("kind"),
        pl.col("Teren").alias("area"),
        pl.lit(None, pl.String).alias("boundary"),
    )
    results = results.with_columns(
        teryt("Kod gminy"), count("Nr obw.").alias("commission"), count("Nr okr.").alias("district")
    )
    unit = ["gmina", "commission"]
    long = committees(results, unit, columns)
    results = results.select(
        *unit,
        "district",
        pl.col("Adres").alias("seat"),
        count("Uprawnieni do głosowania").alias("electors"),
        count("Wydane karty (frekwencja)").alias("issued"),
        count("Karty wyjęte z urny").alias("cast"),
        count("Karty nieważne").alias("invalid_ballots"),
        count("Karty ważne").alias("valid_ballots"),
        count("Głosy nieważne z kart ważnych").alias("invalid"),
        count("Głosy ważne z kart ważnych").alias("valid"),
    ).join(long.pivot("committee", index=unit, values="votes", aggregate_function="first"), on=unit, how="left")
    totals = table(year, "district_lists").with_columns(count("Nr okr.").alias("district"))
    lists = committees(totals, [], [column for column in totals.columns if re.match(r"\d+ - ", column)])
    lists = lists.with_columns(
        pl.struct("district", "committee")
        .map_elements(
            lambda row: next(n for (d, n), c in names.items() if d == row["district"] and c == row["committee"]),
            pl.Int64,
        )
        .alias("list")
    )
    lists = seated(lists, candidates)
    sizes = table(year, "districts")
    districts = sizes.filter(pl.col("Nr\nokręgu").str.contains(r"^\d+$")).select(
        count("Nr\nokręgu").alias("district"),
        count("Liczba\nmandatów").alias("magnitude"),
        pl.col("Siedziba Okręgowej Komisji Wyborczej").alias("seat"),
        pl.lit(None, pl.String).alias("boundary"),
    )
    off = districts.join(lists.group_by("district").agg(pl.col("seats").sum()), on="district")
    if off.height != 41 or (off["seats"] != off["magnitude"]).any():
        raise ValueError("elected members differ from the constituency sizes")
    return {
        "results": results,
        "lists": lists,
        "districts": districts,
        "candidates": candidates.drop("committee"),
        "registry": registry,
    }


class Early(NamedTuple):
    position: str
    given: tuple[str, ...]
    votes: str
    units: str
    gmina: str
    commission: str
    address: str
    kind: str | None
    area: str | None
    accounting: dict[str, str]
    first: str


EARLY = {
    1991: Early(
        position="Nr\nkand.",
        given=("Umiona",),
        votes="L. głosów",
        units="unit_lists",
        gmina="Kod gminy (cyfry 1-5)",
        commission="Numer obwodu",
        address="Adres komisji obwodowej",
        kind=None,
        area=None,
        accounting={
            "Liczba uprawnionych": "electors",
            "Karty wydane": "issued",
            "Głosy oddane": "cast",
            "Głosy nieważne": "invalid_ballots",
            "Głosy ważne": "valid",
        },
        first="1",
    ),
    1993: Early(
        position="Nr\nna liście",
        given=("Imiona",),
        votes="L. gł.",
        units="unit_lists",
        gmina="Kod gm.",
        commission="Nr obw.",
        address="Adres",
        kind="Typ",
        area="Teren",
        accounting={"Upr.": "electors", "Odd.": "cast", "Ważne": "valid"},
        first="Lista nr 1",
    ),
    1997: Early(
        position="Lp",
        given=("1. imię", "2. Imię"),
        votes="Głosy",
        units="units",
        gmina="TERYT\ngminy",
        commission="Nr obw.",
        address="Adres",
        kind="Typ obw.",
        area=None,
        accounting={
            "Uprawnieni do głosowania": "electors",
            "Karty wydane": "issued",
            "Głosy oddane (frekwencja)": "cast",
            "Głosy nieważne": "invalid_ballots",
            "Głosy ważne": "valid",
        },
        first="1 - Unia Pracy",
    ),
}


def listed(column: str) -> int:
    return int(re.findall(r"\d+", column)[0])


def load_early(year: int) -> dict[str, pl.DataFrame]:
    spec = EARLY[year]
    gender = {"K": "Kobieta", "M": "Mężczyzna"}
    frame = table(year, "candidates")
    district_column, list_column = frame.columns[0], frame.columns[1]
    national_column = next(column for column in frame.columns if "oglnp" in column)
    candidates = frame.select(
        count(district_column).alias("district"),
        count(list_column).alias("list"),
        count(spec.position).alias("position"),
        pl.concat_str(*spec.given, pl.col("Nazwisko"), separator=" ", ignore_nulls=True)
        .str.replace_all(r"\s+", " ")
        .alias("name"),
        pl.col("Płeć").replace_strict(gender).alias("sex"),
        pl.col("Zawód").alias("occupation"),
        pl.col(next(c for c in frame.columns if c.startswith(("Miejsce", "Miejscowość")))).alias("residence"),
        pl.lit(None, pl.String).alias("membership"),
        pl.lit(None, pl.String).alias("support"),
        count(spec.votes).alias("votes"),
        (pl.col("Mandat") == "O").alias("elected"),
        pl.col("Komitet wyborczy").str.replace_all(r"\s+", " ").alias("committee"),
        count(national_column).alias("national"),
        (pl.col("Mandat") == "K").alias("national_elected"),
    )
    if year == 1997:
        headers = Counter(listed(column) for column in table(year, "units").columns if re.match(r"\d+ - ", column))
        ballot = {
            listed(column): numbered(column)
            for column in table(year, "units").columns
            if re.match(r"\d+ - ", column) and headers[listed(column)] == 1
        }
        candidates = candidates.with_columns(
            pl.col("list")
            .replace_strict(ballot, default=pl.col("committee"), return_dtype=pl.String)
            .alias("committee")
        )
    names = {(d, n): c for d, n, c in candidates.select("district", "list", "committee").unique().iter_rows()}

    def committees(frame: pl.DataFrame, index: list[str], columns: list[str]) -> pl.DataFrame:
        long = (
            frame.select(*index, "district", *(count(column) for column in columns))
            .unpivot(index=[*index, "district"], variable_name="column", value_name="votes")
            .filter(pl.col("votes").is_not_null())
            .with_columns(pl.col("column").map_elements(listed, pl.Int64).alias("list"))
        )
        long = long.with_columns(
            pl.struct("district", "list")
            .map_elements(lambda row: names.get((row["district"], row["list"])), pl.String)
            .alias("committee")
        )
        stray = long.filter(pl.col("committee").is_null() & (pl.col("votes") != 0))
        if stray.height:
            raise ValueError(f"{year}: votes for a list without candidates:\n{stray}")
        return (
            long.filter(pl.col("committee").is_not_null())
            .group_by(*index, "district", "list", "committee")
            .agg(pl.col("votes").sum())
        )

    results = table(year, spec.units)
    columns = results.columns[results.columns.index(spec.first) :]
    results = results.with_columns(
        pl.col(spec.gmina).alias("gmina"),
        count(spec.commission).alias("commission"),
        count(results.columns[0]).alias("district"),
    )
    unit = ["gmina", "commission"]
    registry = results.select(
        *unit,
        (pl.col(spec.kind) if spec.kind else pl.lit(None, pl.String)).alias("kind"),
        (pl.col(spec.area) if spec.area else pl.lit(None, pl.String)).alias("area"),
        pl.lit(None, pl.String).alias("boundary"),
    )
    long = committees(results, unit, columns)
    results = results.select(
        *unit,
        "district",
        pl.col(spec.address).alias("seat"),
        *(count(column).alias(name) for column, name in spec.accounting.items()),
    ).join(long.pivot("committee", index=unit, values="votes", aggregate_function="first"), on=unit, how="left")
    if year == 1991:
        printed = table(year, "lists").select(
            count("Nr okr.").alias("district"),
            count("Nr listy").alias("list"),
            count("L. glosow").alias("votes"),
            count("L. mand.").alias("printed"),
            pl.col("Blok").alias("bloc"),
        )
        lists = printed.with_columns(
            pl.struct("district", "list")
            .map_elements(lambda row: names.get((row["district"], row["list"])), pl.String)
            .alias("committee")
        )
        if lists["committee"].null_count():
            raise ValueError("a constituency list has no candidates")
    else:
        totals = next(frame for member, frame in tables(year, "district_lists").items() if "proc" not in member)
        totals = totals.with_columns(count(totals.columns[0]).alias("district"))
        lists = committees(totals, [], [column for column in totals.columns if re.match(r"\d+ - ", column)])
    lists = seated(lists, candidates.drop("national", "national_elected"))
    if year == 1991:
        lists = lists.join(printed.select("district", "list", "printed", "bloc"), on=["district", "list"])
        if (lists["printed"] != lists["seats"]).any():
            raise ValueError("elected members differ from the printed list seats")
        lists = lists.drop("printed").with_columns(qualified=pl.lit(True))
    sizes = table(year, "districts")
    if year == 1997:
        sizes = sizes.filter(pl.col(sizes.columns[0]).str.contains(r"^\d+$")).select(
            count(sizes.columns[0]).alias("district"),
            count(sizes.columns[3]).alias("magnitude"),
            pl.col(sizes.columns[1]).alias("seat"),
            pl.col(sizes.columns[2]).alias("boundary"),
        )
    else:
        boundary = next(c for c in sizes.columns if c.startswith(("Obszar", "Granice")))
        sizes = sizes.select(
            count(sizes.columns[0]).alias("district"),
            count("L. mand.").alias("magnitude"),
            pl.col("Siedziba OKW").alias("seat"),
            pl.col(boundary).alias("boundary"),
        )
    national = candidates.filter(pl.col("national").is_not_null())
    labels = national.group_by("list").agg(pl.col("committee").mode().first())
    for row in national.filter(pl.struct("list", "national").is_duplicated()).iter_rows(named=True):
        print(f"{year} national list {row['list']}: {row['name']} shares position {row['national']}")
    if candidates.filter(pl.col("national_elected") & pl.col("national").is_null()).height:
        raise ValueError("a national list member has no national list position")
    linked = (
        lists.join(national.select("district", "list").unique(), on=["district", "list"]) if year == 1991 else lists
    )
    nation = (
        linked.group_by("list")
        .agg(pl.col("votes").sum(), (pl.col("seats") > 0).sum().alias("districts"))
        .join(labels, on="list")
        .join(national.group_by("list").agg(pl.col("national_elected").sum().cast(pl.Int64).alias("seats")), on="list")
    )
    if year == 1993:
        printed = table(year, "committees").select(count("Nr listy").alias("list"), count("Głosy").alias("printed"))
        off = nation.join(printed, on="list", how="left").filter(pl.col("votes") != pl.col("printed"))
        if off.height:
            raise ValueError(f"national list votes differ from the printed committee totals:\n{off}")
    national = national.select("list", "national", "district", pl.col("list").alias("district_list"), "position")
    if year == 1997:
        on_file = []
        for member, sheet in tables(year, "lists").items():
            number = int(re.findall(r"(\d+)\.xlsx$", member)[0])
            given = next(c for c in sheet.columns if c.startswith("Imi"))
            on_file.append(
                sheet.select(
                    pl.lit(number, pl.Int64).alias("list"),
                    count(sheet.columns[0]).alias("national"),
                    count("Okręg").alias("district"),
                    pl.concat_str(given, "Nazwisko", separator=" ").str.replace_all(r"\s+", " ").alias("name"),
                    pl.col("Nazwisko").alias("surname"),
                )
            )
        on_file = pl.concat(on_file)
        surnames = national.join(
            frame.select(
                count(district_column).alias("district"),
                count(list_column).alias("district_list"),
                count(spec.position).alias("position"),
                pl.col("Nazwisko").alias("surname"),
            ),
            on=["district", "district_list", "position"],
        )
        missing = on_file.join(surnames, on=["list", "district", "surname"], how="anti")
        extra = surnames.join(on_file, on=["list", "district", "surname"], how="anti")
        missing, extra = (
            missing.join(extra, on=["list", "national", "district"], how="anti").drop("surname"),
            extra.join(missing, on=["list", "national", "district"], how="anti"),
        )
        if missing.height:
            print(f"{year}: {missing.height} national list candidates stood in no constituency:\n{missing}")
        if extra.height:
            raise ValueError(f"national list candidates not on the printed national lists:\n{extra}")
        national = pl.concat(
            [
                national.join(
                    candidates.select("district", "list", "position", "name"),
                    left_on=["district", "district_list", "position"],
                    right_on=["district", "list", "position"],
                ),
                missing.select(
                    "list",
                    "national",
                    "district",
                    pl.lit(None, pl.Int64).alias("district_list"),
                    pl.lit(None, pl.Int64).alias("position"),
                    "name",
                ),
            ]
        )
    else:
        national = national.join(
            candidates.select("district", "list", "position", "name"),
            left_on=["district", "district_list", "position"],
            right_on=["district", "list", "position"],
        )
    national = national.join(
        candidates.select("district", pl.col("list").alias("district_list"), "position", "national_elected"),
        on=["district", "district_list", "position"],
        how="left",
    ).with_columns(pl.col("national_elected").fill_null(False).alias("elected"))
    return {
        "results": results,
        "lists": lists,
        "districts": sizes,
        "candidates": candidates.drop("committee", "national", "national_elected"),
        "registry": registry,
        "nation": nation,
        "national": national.drop("national_elected"),
    }


def load_2005() -> dict[str, pl.DataFrame]:
    year = 2005
    gender = {"K": "Kobieta", "M": "Mężczyzna"}
    candidates = table(year, "candidates").select(
        count("Nr okr.").alias("district"),
        count("Nr listy").alias("list"),
        count("Pozycja").alias("position"),
        pl.concat_str(pl.col("Imiona"), pl.col("Nazwisko"), separator=" ").alias("name"),
        pl.col("Płeć").replace_strict(gender).alias("sex"),
        pl.col("Zawód").alias("occupation"),
        pl.col("Miejsce zamieszkania").alias("residence"),
        pl.lit(None, pl.String).alias("membership"),
        pl.lit(None, pl.String).alias("support"),
        count("Głosy").alias("votes"),
        (pl.col("Mandat") == "T").alias("elected"),
        pl.col("Komitet  wyborczy").alias("committee"),
    )
    names = {(d, n): c for d, n, c in candidates.select("district", "list", "committee").unique().iter_rows()}
    results = table(year, "unit_lists")
    columns = results.columns[results.columns.index("Głosy ważne") + 1 :]
    registry = results.select(
        teryt("TERYT"),
        count("Numer obwodu").alias("commission"),
        pl.col("Typ obwodu").alias("kind"),
        pl.col("7-ma cyfra TERYT").alias("area"),
        pl.lit(None, pl.String).alias("boundary"),
    )
    results = results.select(
        teryt("TERYT"),
        count("Numer obwodu").alias("commission"),
        count("Nr\nokręgu").alias("district"),
        pl.col("Adres obwodu").alias("seat"),
        count("Uprawnieni do głosowania").alias("electors"),
        count("Karty wydane (frekwencja)").alias("issued"),
        count("Karty ważne (głosy oddane)").alias("cast"),
        count("Karty ważne (głosy oddane)").alias("valid_ballots"),
        count("Głosy nieważne").alias("invalid"),
        count("Głosy ważne").alias("valid"),
        *(count(column) for column in columns),
    )
    unit = ["gmina", "commission"]
    long = (
        results.select(*unit, "district", *columns)
        .unpivot(index=[*unit, "district"], variable_name="column", value_name="votes")
        .filter(pl.col("votes").is_not_null())
    )
    long = long.with_columns(
        pl.struct("district", "column")
        .map_elements(lambda row: names[(row["district"], int(row["column"].split(" - ", 1)[0]))], pl.String)
        .alias("committee")
    )
    results = results.drop(columns).join(
        long.group_by(*unit, "committee")
        .agg(pl.col("votes").sum())
        .pivot("committee", index=unit, values="votes", aggregate_function="first"),
        on=unit,
        how="left",
    )
    totals = table(year, "district_lists")
    numbered_columns = [column for column in totals.columns if re.match(r"\d+ - ", column)]
    lists = (
        totals.filter(pl.col("Okręg wyborczy").str.contains(r"^\d+ - "))
        .select(
            pl.col("Okręg wyborczy").str.split(" - ").list.first().cast(pl.Int64).alias("district"),
            *(count(column) for column in numbered_columns),
        )
        .unpivot(index="district", variable_name="list", value_name="votes")
        .filter(pl.col("votes").is_not_null())
        .with_columns(pl.col("list").str.split(" - ").list.first().cast(pl.Int64))
        .group_by("district", "list")
        .agg(pl.col("votes").sum())
        .with_columns(
            pl.struct("district", "list")
            .map_elements(lambda row: names[(row["district"], row["list"])], pl.String)
            .alias("committee")
        )
    )
    lists = seated(lists, candidates)
    districts = okregi(year)
    off = districts.join(lists.group_by("district").agg(pl.col("seats").sum()), on="district")
    if off.height != 41 or (off["seats"] != off["magnitude"]).any():
        raise ValueError("elected members differ from the constituency sizes")
    return {
        "results": results,
        "lists": lists,
        "districts": districts,
        "candidates": candidates.drop("committee"),
        "registry": registry,
    }


def load_2007() -> dict[str, pl.DataFrame]:
    year = 2007
    gender = {"K": "Kobieta", "M": "Mężczyzna"}
    candidates = table(year, "candidates").select(
        count("Okręg").alias("district"),
        count("Nr listy").alias("list"),
        count("Nr kand.").alias("position"),
        pl.concat_str(pl.col("Imiona"), pl.col("Nazwisko"), separator=" ").alias("name"),
        pl.col("Płeć").replace_strict(gender).alias("sex"),
        pl.col("Zawód").alias("occupation"),
        pl.col("Miejsce zamieszkania").alias("residence"),
        pl.col("Partia").alias("membership"),
        pl.col("Poparcie").alias("support"),
        count("Głosy").alias("votes"),
        (pl.col("Mandat") == "T").alias("elected"),
        pl.col("Komitet wyborczy").alias("committee"),
    )
    names = dict(candidates.select("list", "committee").unique().iter_rows())
    if len(names) != candidates.select("committee").n_unique():
        raise ValueError("a list number carries two committees")
    results = table(year, "unit_lists")
    columns = results.columns[results.columns.index("Ważne") + 1 :]
    numbers = {column: int(column.split(" - ", 1)[0]) for column in columns}
    registry = results.select(
        teryt("Kod gm."),
        count("Nr obw.").alias("commission"),
        pl.col("Typ").alias("kind"),
        pl.col("Teren").alias("area"),
        pl.lit(None, pl.String).alias("boundary"),
    )
    results = results.select(
        teryt("Kod gm."),
        count("Nr obw.").alias("commission"),
        count("Okręg").alias("district"),
        pl.col("Adres").alias("seat"),
        count("Upr.").alias("electors"),
        count("Frekw.").alias("issued"),
        count("Odd.").alias("cast"),
        count("Ważne").alias("valid"),
        *(count(column).alias(names[number]) for column, number in numbers.items()),
    )
    lists = (
        results.select("district", *names.values())
        .unpivot(index="district", variable_name="committee", value_name="votes")
        .filter(pl.col("votes").is_not_null())
        .group_by("district", "committee")
        .agg(pl.col("votes").sum())
        .join(pl.DataFrame({"committee": list(names.values()), "list": list(names.keys())}), on="committee")
    )
    lists = seated(lists, candidates)
    districts = okregi(year)
    off = districts.join(lists.group_by("district").agg(pl.col("seats").sum()), on="district")
    if off.height != 41 or (off["seats"] != off["magnitude"]).any():
        raise ValueError("elected members differ from the constituency sizes")
    return {
        "results": results,
        "lists": lists,
        "districts": districts,
        "candidates": candidates.drop("committee"),
        "registry": registry,
    }


def load_2011() -> dict[str, pl.DataFrame]:
    year = 2011
    gender = {"K": "Kobieta", "M": "Mężczyzna"}
    candidates = table(year, "candidates").select(
        count("Okręg").alias("district"),
        count("Nr listy").alias("list"),
        count("Poz.").alias("position"),
        pl.concat_str(pl.col("Imiona"), pl.col("Nazwisko"), separator=" ").alias("name"),
        pl.col("Płeć").replace_strict(gender).alias("sex"),
        pl.col("Zawód").alias("occupation"),
        pl.col("Miejsce zamieszkania").alias("residence"),
        pl.col("Partia").alias("membership"),
        pl.lit(None, pl.String).alias("support"),
        count("Gł.").alias("votes"),
        (pl.col("Mandat") == "T").alias("elected"),
        pl.col("Komitet").alias("committee"),
        pl.col("Siedziba OKW").alias("seat"),
    )
    results = table(year, "unit_lists")
    accounting = {
        "Liczba uprawnionych do głosowania": "electors",
        "Liczba kart wydanych": "issued",
        "Liczba oddanych kart": "cast",
        "Karty nieważne": "invalid_ballots",
        "Karty ważne": "valid_ballots",
        "Głosy nieważne": "invalid",
        "Głosy ważne": "valid",
    }
    columns = results.columns[results.columns.index("Liczba kopert zwrotnych wrzuconych do urn") + 1 :]
    registry = results.select(
        teryt("TERYT"),
        count("Nr obw.").alias("commission"),
        pl.col("Typ obw.").alias("kind"),
        pl.col("Teren obw.").alias("area"),
        pl.lit(None, pl.String).alias("boundary"),
    )
    results = results.select(
        teryt("TERYT"),
        count("Nr obw.").alias("commission"),
        count("Okręg").alias("district"),
        pl.col("Adres").alias("seat"),
        *(count(column).alias(name) for column, name in accounting.items()),
        *(count(column).alias(numbered(column)) for column in columns),
    )
    lists = (
        results.select("district", *(numbered(column) for column in columns))
        .unpivot(index="district", variable_name="committee", value_name="votes")
        .filter(pl.col("votes").is_not_null())
        .group_by("district", "committee")
        .agg(pl.col("votes").sum())
        .join(
            pl.DataFrame(
                [(numbered(column), int(column.split(" - ", 1)[0])) for column in columns],
                schema={"committee": pl.String, "list": pl.Int64},
                orient="row",
            ),
            on="committee",
        )
    )
    lists = seated(lists, candidates)
    sizes = table(year, "districts").filter(pl.col("Izba") == "Sejm")
    districts = (
        sizes.select(
            count("Nr okręgu").alias("district"),
            count("Liczba mandatów").alias("magnitude"),
            pl.col("Granice okręgu wyborczego").alias("boundary"),
        )
        .join(candidates.select("district", "seat").unique(), on="district")
        .select("district", "magnitude", "seat", "boundary")
    )
    off = districts.join(lists.group_by("district").agg(pl.col("seats").sum()), on="district")
    if (off["seats"] != off["magnitude"]).any():
        raise ValueError("elected members differ from the constituency sizes")
    return {
        "results": results,
        "lists": lists,
        "districts": districts,
        "candidates": candidates.drop("committee", "seat"),
        "registry": registry,
    }


def load_2015() -> dict[str, pl.DataFrame]:
    year = 2015
    gender = {"K": "Kobieta", "M": "Mężczyzna"}
    candidates = table(year, "candidates").select(
        count("Nr okr.").alias("district"),
        count("Nr listy").alias("list"),
        count("Poz.").alias("position"),
        pl.concat_str(pl.col("Imiona"), pl.col("Nazwisko"), separator=" ").alias("name"),
        pl.col("Płeć").replace_strict(gender).alias("sex"),
        pl.col("Zawód").alias("occupation"),
        pl.col("Miejsce zam.").alias("residence"),
        pl.col("Przynależność do partii politycznej").alias("membership"),
        pl.lit(None, pl.String).alias("support"),
        count("Głosy").alias("votes"),
        (pl.col("Mandat") == "T").alias("elected"),
        pl.col("Komitet").alias("committee"),
    )
    by_district = table(year, "district_lists")
    columns = by_district.columns[by_district.columns.index("Głosy ważne") + 1 :]
    lists = (
        by_district.select(count("Nr okr.").alias("district"), *(count(column) for column in columns))
        .unpivot(index="district", variable_name="column", value_name="votes")
        .filter(pl.col("votes").is_not_null())
        .with_columns(
            pl.col("column").str.split(" - ").list.first().cast(pl.Int64).alias("list"),
            pl.col("column").map_elements(numbered, return_dtype=pl.String).alias("committee"),
        )
    )
    lists = seated(lists, candidates)
    candidates = candidates.drop("committee")
    districts = by_district.select(
        count("Nr okr.").alias("district"),
        pl.col("Siedziba OKW").alias("seat"),
        pl.lit(None, pl.String).alias("boundary"),
    ).join(lists.group_by("district").agg(pl.col("seats").sum().alias("magnitude")), on="district")
    results = table(year, "unit_lists")
    accounting = {
        "Liczba wyborców": "electors",
        "Wydane karty": "issued",
        "Karty wyjęte z urny": "cast",
        "Karty nieważne": "invalid_ballots",
        "Karty ważne": "valid_ballots",
        "Głosy nieważne": "invalid",
        "Głosy ważne": "valid",
    }
    unit = [teryt("TERYT"), count("Nr obw.").alias("commission")]
    results = results.select(
        *unit,
        count("Nr okr.\nSejm").alias("district"),
        pl.col("Adres").alias("seat"),
        *(count(column).alias(name) for column, name in accounting.items()),
        *(
            count(column).alias(numbered(column))
            for column in results.columns[results.columns.index("Głosy ważne") + 1 :]
        ),
    )
    pieces, struck = [], []
    for member, frame in tables(year, "unit_candidates").items():
        district = int(member.split(".")[0])
        ballot = candidates.filter(pl.col("district") == district)
        lookup = {" ".join(row["name"].split()): (row["list"], row["position"]) for row in ballot.iter_rows(named=True)}
        if len(lookup) != ballot.height:
            raise ValueError(f"district {district}: two candidates share a name")
        names = frame.columns[frame.columns.index(f"Sejm - {VALID_COLUMN}") + 1 :]
        keys, block, at, blanked = [], None, 0, False
        for header in names:
            if header.startswith("Razem "):
                if blanked and keys and keys[-1][1] is not None:
                    struck.append((district, keys[-1][1]))
                block, blanked = None, False
            elif block is None:
                block, at = header, 0
            elif " ".join(header.split()) not in lookup:
                if frame[header].drop_nulls().is_in(["XXXXX"]).all():
                    blanked = True
                    print(f"{year} district {district}: {header!r} of {block!r} is struck")
                else:
                    raise ValueError(f"district {district}: column {header!r} is not a listed candidate")
            else:
                list_, position = lookup[" ".join(header.split())]
                if position <= at or (keys and at and keys[-1][1] != list_):
                    raise ValueError(f"district {district}: {header!r} is out of list order")
                at = position
                keys.append((header, list_, position))
        if len(keys) != ballot.height:
            raise ValueError(f"district {district}: {len(keys)} candidate columns for {ballot.height} candidates")
        piece = frame.select(
            teryt("TERYT gminy"),
            count("Numer obwodu").alias("commission"),
            *(count(header).alias(str(i)) for i, (header, _, _) in enumerate(keys)),
        )
        piece = piece.unpivot(index=["gmina", "commission"], variable_name="i", value_name="votes")
        index = pl.DataFrame(
            [(str(i), list_, position) for i, (_, list_, position) in enumerate(keys)],
            schema={"i": pl.String, "list": pl.Int64, "position": pl.Int64},
            orient="row",
        )
        pieces.append(piece.join(index, on="i").drop("i").with_columns(district=pl.lit(district, pl.Int64)))
    registry = table(year, "units").select(
        teryt("TERYT"),
        count("Nr obw.").alias("commission"),
        pl.col("Typ_1").alias("kind"),
        pl.col("Teren").alias("area"),
        pl.col("Granice").alias("boundary"),
    )
    return {
        "results": results,
        "lists": lists,
        "districts": districts.select("district", "magnitude", "seat", "boundary"),
        "candidates": candidates,
        "candidate_votes": pl.concat(pieces),
        "registry": registry,
        "struck": pl.DataFrame(struck, schema={"district": pl.Int64, "list": pl.Int64}, orient="row"),
    }


def load_2019() -> dict[str, pl.DataFrame]:
    year = 2019
    unit = [teryt("Kod TERYT"), count("Numer").alias("commission")]
    results = table(year, "unit_lists")
    columns = results.columns[results.columns.index(VALID_COLUMN) + 1 :]
    accounting = {**ACCOUNTING, "Liczba wyborców, którym wydano karty do głosowania": "issued"}
    results = results.select(
        *unit,
        count("Okręg").alias("district"),
        pl.col("Siedziba").alias("seat"),
        *(count(column).alias(name) for column, name in accounting.items() if column in results.columns),
        *(count(column).alias(re.sub(r" - ZPOW-\d+-\d+/\d+$", "", column)) for column in columns),
    )
    lists = table(year, "lists").select(
        count("Numer okręgu").alias("district"),
        count("Numer listy").alias("list"),
        pl.col("Nazwa").alias("committee"),
        count("Liczba głosów").alias("votes"),
        (pl.col("Udział w podziale mandatów") == "Tak").alias("qualified"),
        count("Liczba mandatów").alias("seats"),
    )
    districts = table(year, "districts").select(
        count("Numer okręgu").alias("district"),
        count("Liczba mandatów").alias("magnitude"),
        pl.col("Siedziba OKW").alias("seat"),
        pl.col("Opis granic").alias("boundary"),
    )
    candidates = table(year, "candidates").select(
        count("Nr okręgu").alias("district"),
        count("Nr listy (po nadaniu)").alias("list"),
        count("Pozycja na liście").alias("position"),
        pl.concat_str(pl.col("Imiona"), pl.col("Nazwisko"), separator=" ").alias("name"),
        *PERSON,
    )
    pieces, struck = [], []
    for member, frame in tables(year, "unit_candidates").items():
        district = int(member.rsplit("okr_", 1)[1].split(".", 1)[0])
        names = frame.columns[frame.columns.index(VALID_COLUMN) + 1 :]
        keys, current = [], None
        for header in names:
            if found := re.match(r"Lista nr (\d+) - ", header):
                current = int(found[1])
            elif found := re.search(r" - nr na liście (\d+)$", header):
                keys.append((header, current, int(found[1])))
            else:
                raise ValueError(f"district {district}: unknown column {header!r}")
        ballot = candidates.filter(pl.col("district") == district)
        lookup = {(row["list"], row["position"]): row["name"] for row in ballot.iter_rows(named=True)}
        for header, list_, position in keys:
            if (list_, position) not in lookup:
                print(f"{year} district {district}: column {header!r} is a candidate struck from list {list_}")
            elif lookup[(list_, position)] != header.rsplit(" - nr na liście", 1)[0]:
                raise ValueError(f"district {district}: column {header!r} is not {lookup[(list_, position)]!r}")
        struck.extend((district, list_) for _, list_, position in keys if (list_, position) not in lookup)
        keys = [key for key in keys if key[1:] in lookup]
        if len(keys) != ballot.height:
            raise ValueError(f"district {district}: {len(keys)} candidate columns for {ballot.height} candidates")
        piece = frame.select(*unit, *(count(header).alias(str(i)) for i, (header, _, _) in enumerate(keys)))
        piece = piece.unpivot(index=["gmina", "commission"], variable_name="i", value_name="votes")
        index = pl.DataFrame(
            [(str(i), list_, position) for i, (_, list_, position) in enumerate(keys)],
            schema={"i": pl.String, "list": pl.Int64, "position": pl.Int64},
            orient="row",
        )
        pieces.append(piece.join(index, on="i").drop("i").with_columns(district=pl.lit(district, pl.Int64)))
    registry = table(year, "units").select(
        teryt("TERYT gminy"),
        count("Numer").alias("commission"),
        pl.col("Typ obwodu").alias("kind"),
        pl.col("Typ obszaru").alias("area"),
        pl.col("Opis granic").alias("boundary"),
    )
    return {
        "results": results,
        "lists": lists,
        "districts": districts,
        "candidates": candidates,
        "candidate_votes": pl.concat(pieces),
        "registry": registry,
        "struck": pl.DataFrame(struck, schema={"district": pl.Int64, "list": pl.Int64}, orient="row"),
    }


def check(year: int, loaded: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    results, lists, districts, candidates = (loaded[k] for k in ("results", "lists", "districts", "candidates"))
    names = [name for name in ACCOUNTING.values() if name in results.columns]
    committees = [c for c in results.columns if c not in {"gmina", "commission", "district", "seat", *names}]
    units = results.select("gmina", "commission", "district", "seat", *names)
    if units.select("gmina", "commission").is_duplicated().any():
        raise ValueError("two polling districts share a gmina and number")
    if "invalid" in units.columns and (units["valid"] + units["invalid"] != units["valid_ballots"]).any():
        raise ValueError("valid and invalid votes do not add up to the valid ballots")
    if lists.select("district", "committee").is_duplicated().any():
        raise ValueError("a committee holds two lists in one district")
    unit_votes = (
        results.select("gmina", "commission", *committees)
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
    total = 391 if "nation" in loaded else 460
    if districts["magnitude"].sum() != total or lists["seats"].sum() != total:
        raise ValueError(f"seats do not sum to {total}")
    for row in districts.iter_rows(named=True):
        part = lists.filter(pl.col("district") == row["district"], pl.col("qualified"))
        divisors = [1.4, *range(3, 2 * row["magnitude"], 2)] if year == 2001 else None
        if year == 1991:
            blocs = dict(zip(part["list"], part["bloc"]))
            declared = bloc_seats(dict(zip(part["list"], part["seats"])), blocs)
            if pooled(dict(zip(part["list"], part["votes"])), blocs, row["magnitude"]) != declared:
                print(f"{year} district {row['district']}: Hare-Niemeyer does not give the declared bloc seats")
            continue
        won = dhondt(dict(zip(part["list"], part["votes"])), row["magnitude"], divisors)
        if any(won[list_] != seats for list_, seats in zip(part["list"], part["seats"])):
            print(f"{year} district {row['district']}: the divisor method does not give the declared seats")
    seated = candidates.group_by("district", "list").agg(pl.col("elected").sum().alias("won"))
    off = lists.filter(pl.col("seats") > 0).join(seated, on=["district", "list"], how="left")
    off = off.filter(pl.col("won").fill_null(0) != pl.col("seats"))
    if off.height or candidates["elected"].sum() != total:
        raise ValueError(f"elected candidates differ from list seats:\n{off}")
    listed = candidates.group_by("district", "list").agg(
        pl.col("votes").sum(), struck=pl.col("position").max() - pl.len()
    )
    off = lists.join(listed, on=["district", "list"], how="left").filter(pl.col("votes") != pl.col("votes_right"))
    if "struck" in loaded:
        marked = loaded["struck"].with_columns(marked=pl.lit(1))
        off = off.join(marked, on=["district", "list"], how="left").with_columns(
            struck=pl.col("struck") + pl.col("marked").fill_null(0)
        )
    if off.filter((pl.col("votes") < pl.col("votes_right")) | (pl.col("struck") == 0)).height:
        raise ValueError(f"candidate votes differ from list votes:\n{off}")
    for row in off.iter_rows(named=True):
        residual = row["votes"] - row["votes_right"]
        print(f"{year} district {row['district']} list {row['list']}: {residual} votes for struck candidates")
    out = {}
    if "candidate_votes" in loaded:
        candidate_votes = out["candidate_votes"] = loaded["candidate_votes"].filter(pl.col("votes").is_not_null())
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
    if "nation" in loaded:
        nation, national = loaded["nation"], loaded["national"]
        if nation["seats"].sum() != 69 or national["elected"].sum() != 69:
            raise ValueError("national list seats do not sum to 69")
        valid = nation["votes"].sum()
        if year == 1991:
            valid = lists["votes"].sum()
            group = pl.col("list").replace_strict(NATIONAL_BLOCS_1991, default=pl.col("list").cast(pl.String))
            wide = nation.with_columns(
                pooled=pl.col("votes").sum().over(group), won=pl.col("districts").sum().over(group)
            ).filter(
                (pl.col("pooled") * 100 >= valid * 5)
                | (pl.col("won") >= 5)
                | pl.col("list").is_in(list(MINORITIES_1991))
            )
            won = pooled(dict(zip(wide["list"], wide["votes"])), NATIONAL_BLOCS_1991, 69, [1.4, *range(3, 138, 2)])
            declared = bloc_seats(dict(zip(nation["list"], nation["seats"])), NATIONAL_BLOCS_1991)
            for group in sorted(set(won) | set(declared)):
                if won[group] != declared[group]:
                    print(f"{year} national {group}: {won[group]} by the formula, {declared[group]} declared")
        else:
            wide = nation.filter(pl.col("votes") * 100 >= valid * 7)
            won = dhondt(dict(zip(wide["list"], wide["votes"])), 69)
            for row in nation.iter_rows(named=True):
                if won[row["list"]] != row["seats"]:
                    print(
                        f"{year} national list {row['list']}: {won[row['list']]} by the formula, {row['seats']} declared"
                    )
    if year in NOTICES:
        pkw(year, lists, candidates, units, blank)
    units = units.join(loaded["registry"], on=["gmina", "commission"], how="left")
    return {
        "districts": districts,
        "lists": lists,
        "candidates": candidates,
        "units": units,
        "unit_votes": unit_votes,
        **out,
        **{name: loaded[name] for name in ("nation", "national") if name in loaded},
    }


def pkw(year: int, lists: pl.DataFrame, candidates: pl.DataFrame, units: pl.DataFrame, blank: pl.DataFrame) -> None:
    """Check the data files against the notice: every constituency list's votes and every candidate's votes must
    equal it, the committees' national totals must equal it, and the counts summed from the polling districts may
    fall short of it only in constituencies with a polling district that prints no counts."""
    printed = notice(year)
    keys = ["district", "list"]
    off = lists.join(printed["lists"], on=keys, how="full", coalesce=True).filter(
        pl.col("votes").is_null() | pl.col("votes_right").is_null() | (pl.col("votes") != pl.col("votes_right"))
    )
    if off.height:
        raise ValueError(f"{year}: {off.height} constituency lists differ from the notice:\n{off}")
    shown = printed["candidates"].filter(pl.col("votes").is_not_null())
    keys = ["district", "list", "position"]
    off = candidates.join(shown, on=keys, how="full", coalesce=True).filter(
        pl.col("votes").is_null() | pl.col("votes_right").is_null() | (pl.col("votes") != pl.col("votes_right"))
    )
    if off.height:
        raise ValueError(f"{year}: {off.height} candidates differ from the notice:\n{off}")
    surname = shown.select(*keys, folded(pl.col("name").str.split(" ").list.first()).alias("surname"))
    named = candidates.join(surname, on=keys).filter(~folded(pl.col("name")).str.contains(pl.col("surname")))
    for row in named.iter_rows(named=True):
        print(f"{year} district {row['district']} list {row['list']}: the notice spells {row['name']!r} otherwise")
    # A committee's national total is keyed by its list number, or by its name where committees of a single
    # constituency share a number (11 in 2023). The notice spaces and misprints names from one chapter to another,
    # so names are compared by their letters and digits.
    shared = printed["nation"].filter(pl.col("list").is_duplicated())["list"].implode()
    label = (
        pl.when(pl.col("list").is_in(shared))
        .then(folded(pl.col("committee")).str.replace_all(r"[^0-9A-Z]", ""))
        .otherwise(pl.col("list").cast(pl.String))
        .alias("label")
    )
    nation = (
        lists.join(printed["lists"].select("district", "list", label), on=["district", "list"])
        .group_by("label")
        .agg(pl.col("votes").sum())
        .join(printed["nation"].select(label, "votes"), on="label", how="full", coalesce=True)
    )
    off = nation.filter(pl.col("votes").is_null() | (pl.col("votes") != pl.col("votes_right")))
    if off.height or nation.height != printed["nation"].height:
        raise ValueError(f"{year}: committee totals differ from the notice:\n{off}")
    columns = list(NOTICE_COUNTS)
    summed = units.group_by("district").agg(pl.col(column).sum() for column in columns)
    counts = printed["counts"].filter(pl.col("district") > 0)
    gap = summed.join(counts, on="district", suffix="_notice").with_columns(
        pl.col(f"{column}_notice") - pl.col(column) for column in columns
    )
    gap = gap.filter(pl.any_horizontal(pl.col(f"{column}_notice") != 0 for column in columns))
    if gap.filter(~pl.col("district").is_in(blank["district"].implode())).height:
        raise ValueError(f"{year}: constituency counts differ from the notice:\n{gap}")
    country = printed["counts"].filter(pl.col("district") == 0).row(0, named=True)
    if any(int(counts[column].sum()) != country[column] for column in columns):
        raise ValueError(f"{year}: the notice's constituency counts do not add up to its national counts")
    for row in gap.iter_rows(named=True):
        missing = ", ".join(f"{column} {row[f'{column}_notice']:,}" for column in columns if row[f"{column}_notice"])
        print(f"{year} district {row['district']}: the notice counts {missing} more than the polling districts")
    print(
        f"{year}: the notice agrees on {lists.height} constituency lists, {candidates.height} candidates, "
        f"{nation.height} committee totals ({int(lists['votes'].sum()):,} valid votes), and "
        f"{counts.height - gap.height} of {counts.height} constituency counts"
    )


LOADERS = {
    1991: lambda: load_early(1991),
    1993: lambda: load_early(1993),
    1997: lambda: load_early(1997),
    2001: load_2001,
    2005: load_2005,
    2007: load_2007,
    2011: load_2011,
    2015: load_2015,
    2019: load_2019,
    2023: load_2023,
}


def parse() -> None:
    """Parse the years in SEJM_YEAR (every year when it is unset), then build the package pl.kbw_sejm/all from
    every year's parsed tables."""
    years = [int(year) for year in os.environ["SEJM_YEAR"].split(",")] if "SEJM_YEAR" in os.environ else ELECTIONS
    for year in years:
        day = ELECTIONS[year]
        folder = PARSED / str(year)
        folder.mkdir(parents=True, exist_ok=True)
        for name, frame in check(year, LOADERS[year]()).items():
            frame.with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(day)).write_parquet(folder / f"{name}.parquet")
            print(f"{year} {name}: {frame.height} rows")
    print(build())


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package pl.kbw_sejm/all from the parsed tables. It reads the database, through Draft, so the rows
    it rebuilds keep the ids the changeset path gave them. Years go newest first, the order the changeset path first
    wrote them in, so a party shared by several years is named from the year that first wrote it."""
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="PL")
        cs.accept(
            "accounting.votes_valid",
            "Constituency 19 (Warsaw and abroad) in 2019 and 2023 has polling districts abroad whose rows print no "
            "counts, so the valid votes summed from the polling districts fall short of the declared list votes, as "
            "the notes say; and a 1991-1997 national list's votes are those of its committee's constituency lists, "
            "not every valid vote cast.",
        )
        build_okregi(cs)
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
        for year in sorted(ELECTIONS, reverse=True):
            build_year(cs, year, written, taken)
        return cs.write()


def build_okregi(cs: Draft) -> None:
    conn = cs.conn
    districts = pl.read_parquet(PARSED / "2023" / "districts.parquet")
    members = (
        pl.read_parquet(PARSED / "2023" / "units.parquet")
        .filter(pl.col("gmina").str.contains(r"^\d{6}$"))
        .select(pl.col("gmina").str.slice(0, 4).alias("powiat"), "district")
        .unique()
    )
    if members.group_by("powiat").len()["len"].max() != 1:
        raise ValueError("a powiat spans two constituencies")
    law = cs.source(PUBLISHER, TITLE)
    cs.add(
        "source",
        id=law,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license="Official public record, no license stated",
        attribution="Źródło danych: Państwowa Komisja Wyborcza, Krajowe Biuro Wyborcze.",
    )
    for address in urls(2023, "districts"):
        cs.artifact(raw.cached(SOURCE, address), law)
    rows = conn.execute(
        "select c.code, ST_AsBinary(g.geom), g.source_id from area a join area_code c on c.area_id = a.id"
        " join area_geometry g on g.area_id = a.id where a.scheme_code = 'pl.powiat'"
    ).fetchall()
    shapes = {code: shapely.from_wkb(geom) for code, geom, _ in rows}
    [prg] = {source for _, _, source in rows}
    missing = set(members["powiat"]) - set(shapes)
    if missing:
        raise ValueError(f"powiats without a shape: {sorted(missing)}")
    if set(shapes) - set(members["powiat"]):
        raise ValueError(f"powiats in no constituency: {sorted(set(shapes) - set(members['powiat']))}")
    # An outline equal to the stored one keeps the stored bytes: a union can start its rings at another vertex.
    stored = {
        area: text
        for area, text in conn.execute(
            "select g.area_id, g.geom::text from area_geometry g join area a on a.id = g.area_id"
            " where a.scheme_code = 'pl.okreg_sejm' and g.valid_from is null"
        ).fetchall()
    }
    for entry in districts.sort("district").iter_rows(named=True):
        number = entry["district"]
        parts = members.filter(pl.col("district") == number)["powiat"].to_list()
        regions = {code[:2] for code in parts}
        if len(regions) != 1:
            raise ValueError(f"constituency {number} spans voivodeships {sorted(regions)}")
        shape = shapely.set_srid(shapely.union_all([shapes[code] for code in parts], grid_size=1e-7), 4326)
        area = cs.area("pl.okreg_sejm", str(number))
        cs.add("area", id=area, scheme_code="pl.okreg_sejm")
        cs.add("area_code", area_id=area, code=str(number), valid_from=None)
        cs.add("area_parent", area_id=area, parent_id=cs.area("pl.wojewodztwo", regions.pop()), valid_from=None)
        for text, lang, kind in (
            (f"Okręg wyborczy nr {number} ({entry['seat']})", "pl", "official"),
            (f"Sejm constituency {number} ({entry['seat']})", "en", "english"),
        ):
            cs.add("name", entity_id=area, text=text, lang=lang, kind=kind, is_preferred=kind == "official")
        same = area in stored and shapely.equals(shapely.from_wkb(stored[area]), shape)
        cs.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            geom=stored[area] if same else shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(max(shapely.get_parts(shape), key=shapely.area).representative_point(), 4326),
                hex=True,
                include_srid=True,
            ),
            quality="official",
            source_id=prg,
        )
    cs.add(
        "boundary_set",
        scheme_code="pl.okreg_sejm",
        vintage="2001",
        valid_from=None,
        valid_to=None,
        as_of=ELECTIONS[2023],
    )


def build_year(cs: Draft, year: int, written: set[UUID], taken: set[UUID]) -> None:
    conn = cs.conn
    frames = {
        name: pl.read_parquet(PARSED / str(year) / f"{name}.parquet")
        for name in ("districts", "lists", "candidates", "units", "unit_votes")
    }
    districts, lists, candidates, units, unit_votes = (
        frames[name] for name in ("districts", "lists", "candidates", "units", "unit_votes")
    )
    [day] = units["day"].unique()
    early = day.year < 2001
    if early:
        nation_lists, national = (
            pl.read_parquet(PARSED / str(year) / f"{name}.parquet") for name in ("nation", "national")
        )
    preferences = pl.read_parquet(PARSED / str(year) / "candidate_votes.parquet") if year in PREFERENCE_YEARS else None
    preference_values: list[pl.DataFrame] = []

    def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
        cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

    poland = cs.polity("PL")
    sejm = cs.key("pl.body", "sejm")
    if sejm in cs.minted:
        raise ValueError("publish pl.parlament first")
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
    for kind in FILES[day.year]:
        for address in urls(day.year, kind):
            cs.artifact(raw.cached(SOURCE, address), source)
    if year in NOTICES:
        printed = cs.source(NOTICE_PUBLISHER, NOTICE_TITLES[year])
        cs.add(
            "source",
            id=printed,
            kind="official_body",
            publisher=NOTICE_PUBLISHER,
            title=NOTICE_TITLES[year],
            url=NOTICES[year],
            license="Official act, excluded from copyright (Prawo autorskie, art. 4)",
        )
        cs.artifact(raw.cached(SOURCE, NOTICES[year]), printed)
    for code, kind, rank, noun in (
        ("pl.okreg_sejm", "electoral_district", 2, "Sejm constituency"),
        ("pl.obwod", "precinct", 4, "polling district"),
    ):
        cs.add("scheme", code=code, polity_id=poland, kind=kind, rank=rank, noun=noun)

    national_system = None
    if day.year == 1991:
        system = cs.key("pl.electoral_system", "sejm-1991")
        rules = {
            "formula": "hare",
            "pooling": "alliance",
            "lists": {"order": "open", "vacancy": "next_on_list"},
        }
        label = "Sejm open-list Hare-Niemeyer system (1991)"
        body = (
            "391 of the 460 members in 37 constituencies of 7 to 17 seats, by the Hare-Niemeyer method with no "
            "threshold, with voters choosing one candidate on one list (Ordynacja wyborcza do Sejmu of 28 June "
            "1991, arts. 93 to 96). Lists could be joined in a bloc within a constituency, which took seats as "
            "one list; the bloc's seats went to its lists in proportion to their votes unless the committees' "
            "declaration set another split (art. 75), and in several constituencies it did. The other 69 "
            "members came from national lists."
        )
        national_system = cs.key("pl.electoral_system", "sejm-1991-national")
        national_rules = {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.4,
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "pooling": "alliance",
            "threshold": {"level": "list", "scope": "contest", "percent": 5, "constituencies": 5},
        }
        national_label = "Sejm national list modified Sainte-Laguë system (1991)"
        national_body = (
            "69 members from national lists, shared by the modified Sainte-Laguë method (divisors 1.4, 3, 5, and "
            "so on) and filled in list order, skipping candidates who won a constituency seat (Ordynacja wyborcza "
            "do Sejmu of 28 June 1991, arts. 100 and 101). A national list's votes are those of the constituency "
            "lists linked to it. Lists shared seats if their linked lists won seats in at least 5 constituencies "
            "or 5 percent of the votes for all constituency lists, and national minority committees always "
            "did. National lists could be joined in a bloc that took seats as one list and split them by the "
            "committees' declaration: in 1991 the Konfederacja Polski Niepodległej with the Blok "
            "Ludowo-Chrześcijański, the Polski Związek Zachodni, and the Polska Partia Ekologiczna - Zieloni, "
            "and the Mniejszość Niemiecka with the Białoruski Komitet Wyborczy (Monitor Polski 1991 nr 41 poz. "
            "288)."
        )
    elif early:
        system = cs.key("pl.electoral_system", "sejm-1993")
        rules = {
            "formula": "dhondt",
            "lists": {"order": "open", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "national", "percent": 5},
        }
        label = "Sejm open-list D'Hondt system (1993)"
        body = (
            "391 of the 460 members in 52 constituencies of 3 to 17 seats, by the D'Hondt method over lists of "
            "committees that won 5 percent of the votes nationally, or 8 percent for a coalition (national "
            "minority committees are exempt), with voters choosing one candidate on one list (Ordynacja wyborcza "
            "do Sejmu of 28 May 1993). The threshold stored is the committee figure. The other 69 members came "
            "from national lists."
        )
        national_system = cs.key("pl.electoral_system", "sejm-1993-national")
        national_rules = {
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": 7},
        }
        national_label = "Sejm national list D'Hondt system (1993)"
        national_body = (
            "69 members from national lists, shared by the D'Hondt method among committees that won 7 percent "
            "of the votes nationally, by the votes their constituency lists won, and filled in list order "
            "(Ordynacja wyborcza do Sejmu of 28 May 1993)."
        )
    elif day.year == 2001:
        system = cs.key("pl.electoral_system", "sejm-2001")
        rules = {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.4,
            "lists": {"order": "open", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": 5},
        }
        label = "Sejm open-list modified Sainte-Laguë system (2001)"
        body = (
            "460 members in 41 constituencies of 7 to 19 seats, by the modified Sainte-Laguë method (divisors "
            "1.4, 3, 5, and so on) over lists that pass a national threshold of 5 percent for a committee and "
            "8 percent for a coalition (national minority committees are exempt), with voters choosing one "
            "candidate on one list (Ordynacja wyborcza do Sejmu i Senatu of 12 April 2001). The threshold "
            "stored is the committee figure."
        )
    else:
        system = cs.key("pl.electoral_system", "sejm")
        rules = {
            "formula": "dhondt",
            "lists": {"order": "open", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": 5},
        }
        label = "Sejm open-list D'Hondt system"
        body = (
            "460 members in 41 constituencies of 7 to 20 seats, by the D'Hondt method over lists that pass a "
            "national threshold of 5 percent for a committee and 8 percent for a coalition (national minority "
            "committees are exempt), with voters choosing one candidate on one list (Kodeks wyborczy, arts. "
            "196-206). The threshold stored is the committee figure."
        )
    cs.add("electoral_system", id=system, polity_id=poland, rules=rules, rules_version=1)
    name(system, label)
    cs.add("note", entity_id=system, kind="methodology", body_md=body)
    if national_system is not None:
        cs.add("electoral_system", id=national_system, polity_id=poland, rules=national_rules, rules_version=1)
        name(national_system, national_label)
        cs.add("note", entity_id=national_system, kind="methodology", body_md=national_body)

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

    election = cs.key("pl.election", f"{day}:general")
    cs.add("election", id=election, polity_id=poland, kind="general", date_from=day, date_to=day, status="held")
    name(election, f"{day.year} Polish parliamentary election")
    name(election, f"Wybory parlamentarne {day.year}", "pl", "native")
    path = f"pl/{day}-general"
    cs.add("slug", path=path, entity_id=election)

    accounting = units.filter(pl.col("valid").is_not_null()).with_columns(
        ballots=pl.col("cast"), unusable=pl.col("cast") - pl.col("valid")
    )
    figures = (
        (REGISTERED, "electors"),
        (BALLOTS, "cast"),
        (VALID, "valid"),
        (INVALID, "unusable"),
    )
    unit_rows: dict[UUID, dict[str, str | None]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    areas = {
        code[:6]: area
        for code, area in conn.execute(
            "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.gmina'"
        ).fetchall()
    }
    counts = Counter(
        code[:6]
        for (code,) in conn.execute(
            "select c.code from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.gmina'"
        ).fetchall()
    )
    gmina_units: dict[str, UUID] = {}

    def gmina_unit(code: str) -> UUID:
        if code not in gmina_units:
            unit = gmina_units[code] = cs.unit("pl.gmina", code)
            area = areas.get(code) if counts[code] == 1 else None
            unit_rows[unit] = {
                "scheme_code": "pl.gmina",
                "code": code,
                "parent_id": None,
                "area_relation": "equals" if area is not None else "unlocated",
                "area_id": None if area is None else str(area),
                "kind": "standard" if code.isdigit() else "aggregate",
            }
        return gmina_units[code]

    def put(result_set: UUID, unit: UUID, row: dict[str, int]) -> None:
        for measure, column in figures:
            if row[column] is not None:
                values.append((str(result_set), str(unit), None, measure, row[column]))

    def make_set(contest: UUID, key: str, label: str, default: bool, completeness: float, note: str) -> UUID:
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
            is_default=default,
            completeness=completeness,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
        return result_set

    for entry in districts.sort("district").iter_rows(named=True):
        number = entry["district"]
        key = f"{day}:{number}"
        part = lists.filter(pl.col("district") == number).sort("votes", descending=True)
        contest = cs.key(f"{SOURCE}.contest", key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=sejm,
            magnitude=entry["magnitude"],
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, f"Constituency {number} ({entry['seat']})")
        name(contest, f"Okręg wyborczy nr {number} ({entry['seat']})", "pl", "native")
        cs.add("slug", path=f"{path}/sejm/{number}", entity_id=contest)
        options = {}
        blocs: dict[str, UUID] = {}
        for row in part.iter_rows(named=True):
            option = options[row["list"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['list']}")
            bloc = row.get("bloc")
            if bloc is not None and bloc not in blocs:
                alliance = blocs[bloc] = cs.key("pl.alliance", f"{key}:bloc:{bloc}")
                cs.add("alliance", id=alliance, kind="apparentement", election_id=election)
                members = part.filter(pl.col("bloc") == bloc)
                name(
                    alliance,
                    f"Bloc of lists {', '.join(str(n) for n in sorted(members['list']))} in constituency {number}",
                )
                for member in sorted({party_for(committee) for committee in members["committee"]}):
                    cs.add("alliance_member", alliance_id=alliance, party_id=member, role=None)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_for(row["committee"]),
                alliance_id=None if bloc is None else blocs[bloc],
                ballot_label=" ".join(row["committee"].split()),
            )
        district_unit = cs.unit("pl.okreg_sejm", key)
        unit_rows[district_unit] = {
            "scheme_code": "pl.okreg_sejm",
            "code": key,
            "parent_id": None,
            "area_relation": "unlocated" if early else "equals",
            "area_id": None if early else str(cs.area("pl.okreg_sejm", str(number))),
            "kind": "standard",
        }
        here = accounting.filter(pl.col("district") == number)
        total = {column: int(here[column].sum()) for _, column in figures}
        official = int(part["votes"].sum())
        note = EARLY_METHOD if early else METHOD + (SUMMED if day.year in SUMMED_YEARS else "")
        if total["valid"] != official:
            note += (
                f" The polling districts that print counts hold {total['valid']:,} list votes against the "
                f"constituency total of {official:,}; the missing votes are in polling districts whose rows print "
                "no counts. Both are stored as printed."
            )
        if preferences is not None:
            note += PREFERENCE_METHOD
        district_set = make_set(contest, key, "district", True, 1, note)
        put(district_set, district_unit, total)
        for row in part.iter_rows(named=True):
            values.append((str(district_set), str(district_unit), str(options[row["list"]]), VOTES, row["votes"]))
            if row["seats"]:
                cs.add(
                    "outcome",
                    result_set_id=district_set,
                    option_id=options[row["list"]],
                    kind="won",
                    seats=row["seats"],
                )
        elected = Counter()
        for person_row in candidates.filter(pl.col("district") == number).iter_rows(named=True):
            tag = f"{key}:{person_row['list']}:{person_row['position']}"
            person = cs.key(f"{SOURCE}.person", tag)
            if person not in taken:
                cs.add("person", id=person, gender={"Kobieta": "female", "Mężczyzna": "male"}.get(person_row["sex"]))
            name(person, display(person_row["name"], "pl"))
            candidacy = cs.key(f"{SOURCE}.candidacy", tag)
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[person_row["list"]],
                role="list_member",
                list_position=person_row["position"],
                ballot_name=person_row["name"],
                is_independent=False,
            )
            if person_row["elected"]:
                elected[person_row["list"]] += 1
                cs.add(
                    "outcome",
                    result_set_id=district_set,
                    option_id=options[person_row["list"]],
                    candidacy_id=candidacy,
                    kind="elected",
                )
        for row in part.iter_rows(named=True):
            if elected[row["list"]] != row["seats"]:
                raise ValueError(f"{key}: list {row['list']} has {elected[row['list']]} elected for {row['seats']}")
        choices: dict[tuple[int, int], UUID] = {}
        if preferences is not None:
            for person_row in candidates.filter(pl.col("district") == number).iter_rows(named=True):
                list_, position = person_row["list"], person_row["position"]
                choice = choices[(list_, position)] = cs.key(f"{SOURCE}.option", f"{key}:{list_}:{position}")
                cs.add(
                    "contest_option",
                    id=choice,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=options[list_],
                    ballot_position=position,
                    ballot_label=person_row["name"],
                )
                values.append((str(district_set), str(district_unit), str(choice), PREFERENCE, person_row["votes"]))

        local = units.filter(pl.col("district") == number)
        votes = unit_votes.filter(
            pl.col("gmina").is_in(local["gmina"].implode()) & pl.col("commission").is_in(local["commission"].implode())
        ).join(local.select("gmina", "commission"), on=["gmina", "commission"])
        got = int(votes["votes"].sum())
        completeness = min(got / official, 1) if official else 1
        summed, printed_note = "", ""
        if preferences is not None:
            summed = " Candidates' preference votes (measure 7) are summed the same way."
            printed_note = " Candidates' preference votes (measure 7) are stored as the file prints them."
        gmina_set = make_set(contest, key, "gmina", False, completeness, GMINA_METHOD + summed)
        obwod_set = make_set(contest, key, "obwod", False, completeness, OBWOD_METHOD + printed_note)
        for code in sorted(set(local["gmina"])):
            at = accounting.filter((pl.col("district") == number) & (pl.col("gmina") == code))
            put(gmina_set, gmina_unit(code), {c: int(at[c].sum()) for _, c in figures})
        for row in votes.group_by("gmina", "list").agg(pl.col("votes").sum()).iter_rows(named=True):
            values.append(
                (str(gmina_set), str(gmina_unit(row["gmina"])), str(options[row["list"]]), VOTES, row["votes"])
            )
        for row in local.iter_rows(named=True):
            unit = cs.unit("pl.obwod", f"{day}:{row['gmina']}:{row['commission']}")
            unit_rows[unit] = {
                "scheme_code": "pl.obwod",
                "code": f"{day}:{row['gmina']}:{row['commission']}",
                "parent_id": str(gmina_unit(row["gmina"])),
                "area_relation": "unlocated",
                "area_id": None,
                "kind": "aggregate" if not row["gmina"].isdigit() else "standard",
            }
            if row["valid"] is not None:
                put(obwod_set, unit, {**row, "unusable": row["cast"] - row["valid"]})
        lookup = {
            (row["gmina"], row["commission"]): cs.unit("pl.obwod", f"{day}:{row['gmina']}:{row['commission']}")
            for row in local.iter_rows(named=True)
        }
        for row in votes.iter_rows(named=True):
            values.append(
                (
                    str(obwod_set),
                    str(lookup[(row["gmina"], row["commission"])]),
                    str(options[row["list"]]),
                    VOTES,
                    row["votes"],
                )
            )
        if preferences is not None:
            chosen = pl.DataFrame(
                [(list_, position, str(choice)) for (list_, position), choice in choices.items()],
                schema={"list": pl.Int64, "position": pl.Int64, "option_id": pl.String},
                orient="row",
            )
            placed = pl.DataFrame(
                [(gmina, commission, str(unit)) for (gmina, commission), unit in lookup.items()],
                schema={"gmina": pl.String, "commission": pl.Int64, "unit_id": pl.String},
                orient="row",
            )
            marked = preferences.filter(pl.col("district") == number).select(
                "gmina", "commission", "list", "position", "votes"
            )
            found = marked.join(chosen, on=["list", "position"]).join(placed, on=["gmina", "commission"])
            if found.height != marked.height:
                raise ValueError(f"{key}: {marked.height - found.height} candidate votes without a candidate or unit")
            gminas = pl.DataFrame(
                {"gmina": list(gmina_units), "gmina_unit": [str(unit) for unit in gmina_units.values()]}
            )
            for result_set, frame in (
                (obwod_set, found.select("unit_id", "option_id", "votes")),
                (
                    gmina_set,
                    found.group_by("gmina", "option_id")
                    .agg(pl.col("votes").sum())
                    .join(gminas, on="gmina")
                    .select(pl.col("gmina_unit").alias("unit_id"), "option_id", "votes"),
                ),
            ):
                preference_values.append(
                    frame.select(
                        pl.lit(str(result_set)).alias("result_set_id"),
                        "unit_id",
                        "option_id",
                        pl.lit(PREFERENCE, pl.Int16).alias("measure"),
                        pl.col("votes").cast(pl.Int64).alias("value"),
                    )
                )

    if early:
        [(nation_area,)] = conn.execute("select id from area where scheme_code = 'pl.nation'").fetchall()
        nation = cs.unit("pl.nation", "PL")
        unit_rows[nation] = {
            "scheme_code": "pl.nation",
            "code": "PL",
            "parent_id": None,
            "area_relation": "equals",
            "area_id": str(nation_area),
            "kind": "standard",
        }
        key = f"{day}:kraj"
        contest = cs.key(f"{SOURCE}.contest", key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=sejm,
            magnitude=69,
            electoral_system_id=national_system,
            default_result_kind="counted",
        )
        name(contest, "National list")
        name(contest, "Lista ogólnopolska", "pl", "native")
        cs.add("slug", path=f"{path}/sejm/kraj", entity_id=contest)
        nation_set = make_set(contest, key, "nation", True, 1, NATION_METHOD + (LINKED if day.year == 1991 else ""))
        put(nation_set, nation, {column: int(accounting[column].sum()) for _, column in figures})
        options = {}
        blocs = {}
        for bloc in sorted(set(NATIONAL_BLOCS_1991.values()) if day.year == 1991 else ()):
            alliance = blocs[bloc] = cs.key("pl.alliance", f"{key}:bloc:{bloc}")
            cs.add("alliance", id=alliance, kind="apparentement", election_id=election)
            members = nation_lists.filter(pl.col("list").replace_strict(NATIONAL_BLOCS_1991, default=None) == bloc)
            lists_named = ", ".join(str(n) for n in sorted(members["list"]))
            name(alliance, f"Bloc of national lists {lists_named}")
            for member in sorted({party_for(committee) for committee in members["committee"]}):
                cs.add("alliance_member", alliance_id=alliance, party_id=member, role=None)
        for row in nation_lists.sort("votes", descending=True).iter_rows(named=True):
            option = options[row["list"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['list']}")
            bloc = NATIONAL_BLOCS_1991.get(row["list"]) if day.year == 1991 else None
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_for(row["committee"]),
                alliance_id=None if bloc is None else blocs[bloc],
                ballot_label=" ".join(row["committee"].split()),
            )
            values.append((str(nation_set), str(nation), str(option), VOTES, row["votes"]))
            if row["seats"]:
                cs.add("outcome", result_set_id=nation_set, option_id=option, kind="won", seats=row["seats"])
        elected = Counter()
        for row in national.sort("list", "national", "district", "position").iter_rows(named=True):
            if row["district_list"] is None:
                person = cs.key(f"{SOURCE}.person", f"{key}:{row['list']}:{row['national']}")
                if person not in taken:
                    cs.add("person", id=person, gender=None)
                name(person, display(row["name"], "pl"))
                tag = f"{key}:{row['list']}:{row['national']}"
            else:
                tag = f"{day}:{row['district']}:{row['district_list']}:{row['position']}"
                person = cs.key(f"{SOURCE}.person", tag)
                tag = f"{key}:{tag}"
            candidacy = cs.key(f"{SOURCE}.candidacy", tag)
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[row["list"]],
                role="list_member",
                list_position=row["national"],
                ballot_name=row["name"],
                is_independent=False,
            )
            if row["elected"]:
                elected[row["list"]] += 1
                cs.add(
                    "outcome",
                    result_set_id=nation_set,
                    option_id=options[row["list"]],
                    candidacy_id=candidacy,
                    kind="elected",
                )
        for row in nation_lists.iter_rows(named=True):
            if elected[row["list"]] != row["seats"]:
                raise ValueError(f"national list {row['list']} has {elected[row['list']]} elected")

    minted = [
        {"id": str(unit), "polity_id": str(poland), **row}
        for unit, row in unit_rows.items()
        if unit in cs.minted or row["scheme_code"] == "pl.okreg_sejm"
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
    if preference_values:
        cs.frame(
            "result_value",
            pl.concat(preference_values).with_columns(
                channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
            ),
        )
