import io
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import display
from ea.store import connect

SOURCE = "pl.kbw_prezydent"
VERSION = "1"
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze: wybory Prezydenta Rzeczypospolitej Polskiej"
PAGE = "https://danewyborcze.kbw.gov.pl/"
NOTICE_PUBLISHER = "Państwowa Komisja Wyborcza"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
BASE = "https://danewyborcze.kbw.gov.pl/dane"
ELI = "https://api.sejm.gov.pl/eli/acts/DU"
PARSED = raw.parsed(SOURCE)
ROUNDS = {
    (2025, 1): date(2025, 5, 18),
    (2025, 2): date(2025, 6, 1),
    (2020, 1): date(2020, 6, 28),
    (2020, 2): date(2020, 7, 12),
    (2015, 1): date(2015, 5, 10),
    (2015, 2): date(2015, 5, 24),
    (2010, 1): date(2010, 6, 20),
    (2010, 2): date(2010, 7, 4),
    (2005, 1): date(2005, 10, 9),
    (2005, 2): date(2005, 10, 23),
    (2000, 1): date(2000, 10, 8),
}
FILES = {
    (2025, 1): {
        "candidates": "2025/prezydent/1/kandydaci_csv.1752152970.zip",
        "units": "2025/prezydent/1/protokoly_po_obwodach_csv.1752152970.zip",
    },
    (2025, 2): {
        "candidates": "2025/prezydent/2/kandydaci_w_drugiej_turze_csv.1752152970.zip",
        "units": "2025/prezydent/2/protokoly_po_obwodach_w_drugiej_turze_csv.1752152970.zip",
    },
    (2020, 1): {
        "candidates": "2020/prezydent/1/kandydaci_csv.zip",
        "units": "2020/prezydent/1/wyniki_gl_na_kand_po_obwodach_csv.zip",
    },
    (2020, 2): {
        "candidates": "2020/prezydent/2/kandydaci_csv.zip",
        "units": "2020/prezydent/2/wyniki_gl_na_kand_po_obwodach_csv.zip",
    },
    (2015, 1): {"units": "2015/prezydent/1/prezydent_2015_tura1.zip"},
    (2015, 2): {"units": "2015/prezydent/2/wyniki_tura2.zip"},
    (2010, 1): {"units": "2010/prezydent/1/pzt2010-wyn-obw.zip"},
    (2010, 2): {"units": "2010/prezydent/2/pzt2010-wyn-obw.zip"},
    (2005, 1): {"units": "2005/prezydent/1/1456148660_37030.xls"},
    (2005, 2): {"units": "2005/prezydent/2/1456148721_37553.xls"},
    (2000, 1): {"units": "2000/prezydent/prez2000obw.xls"},
}
SHEETS = {(2005, 1): "obw1tura"}
ALIASES = {"KORWIN-MIKKE Janusz": "KORWIN-MIKKE Janusz Ryszard", "LEPPER Andrzej": "LEPPER Andrzej Zbigniew"}
NOTICES = {
    (2025, 1): (
        "2025/652",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 19 maja 2025 r. o wynikach głosowania i wyniku wyborów "
            "Prezydenta Rzeczypospolitej Polskiej zarządzonych na dzień 18 maja 2025 r."
        ),
        {"electors": 29252340, "cast": 19692324, "valid": 19603784},
        {
            "BARTOSZEWICZ Artur": 95640,
            "BIEJAT Magdalena Agnieszka": 829361,
            "BRAUN Grzegorz Michał": 1242917,
            "HOŁOWNIA Szymon Franciszek": 978901,
            "JAKUBIAK Marek": 150698,
            "MACIAK Maciej": 36371,
            "MENTZEN Sławomir Jerzy": 2902448,
            "NAWROCKI Karol Tadeusz": 5790804,
            "SENYSZYN Joanna": 214198,
            "STANOWSKI Krzysztof Jakub": 243479,
            "TRZASKOWSKI Rafał Kazimierz": 6147797,
            "WOCH Marek Marian": 18338,
            "ZANDBERG Adrian Tadeusz": 952832,
        },
    ),
    (2025, 2): (
        "2025/714",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 2 czerwca 2025 r. o wynikach ponownego głosowania i wyniku "
            "wyborów Prezydenta Rzeczypospolitej Polskiej"
        ),
        {"electors": 29363722, "cast": 21034880, "valid": 20844163},
        {"NAWROCKI Karol Tadeusz": 10606877, "TRZASKOWSKI Rafał Kazimierz": 10237286},
    ),
    (2020, 1): (
        "2020/1163",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 30 czerwca 2020 r. o wynikach głosowania i wyniku wyborów "
            "Prezydenta Rzeczypospolitej Polskiej, zarządzonych na dzień 28 czerwca 2020 r."
        ),
        {"electors": 30204684, "cast": 19486860, "valid": 19425459},
        {
            "BIEDROŃ Robert": 432129,
            "BOSAK Krzysztof": 1317380,
            "DUDA Andrzej Sebastian": 8450513,
            "HOŁOWNIA Szymon Franciszek": 2693397,
            "JAKUBIAK Marek": 33652,
            "KOSINIAK-KAMYSZ Władysław Marcin": 459365,
            "PIOTROWSKI Mirosław Mariusz": 21065,
            "TANAJNO Paweł Jan": 27909,
            "TRZASKOWSKI Rafał Kazimierz": 5917340,
            "WITKOWSKI Waldemar Włodzimierz": 27290,
            "ŻÓŁTEK Stanisław Józef": 45419,
        },
    ),
    (2020, 2): (
        "2020/1238",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 13 lipca 2020 r. o wynikach ponownego głosowania i wyniku "
            "wyborów Prezydenta Rzeczypospolitej Polskiej"
        ),
        {"electors": 30268460, "cast": 20638904, "valid": 20458911},
        {"DUDA Andrzej Sebastian": 10440648, "TRZASKOWSKI Rafał Kazimierz": 10018263},
    ),
    (2015, 1): (
        "2015/650",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 11 maja 2015 r. o wynikach głosowania i wyniku wyborów "
            "Prezydenta Rzeczypospolitej Polskiej, zarządzonych na dzień 10 maja 2015 r."
        ),
        {"electors": 30688570, "cast": 15023886, "valid": 14898934},
        {
            "BRAUN Grzegorz Michał": 124132,
            "DUDA Andrzej Sebastian": 5179092,
            "JARUBAS Adam Sebastian": 238761,
            "KOMOROWSKI Bronisław Maria": 5031060,
            "KORWIN-MIKKE Janusz Ryszard": 486084,
            "KOWALSKI Marian Janusz": 77630,
            "KUKIZ Paweł Piotr": 3099079,
            "OGÓREK Magdalena Agnieszka": 353883,
            "PALIKOT Janusz Marian": 211242,
            "TANAJNO Paweł Jan": 29785,
            "WILK Jacek": 68186,
        },
    ),
    (2015, 2): (
        "2015/725",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 25 maja 2015 r. o wynikach ponownego głosowania i wyniku "
            "wyborów Prezydenta Rzeczypospolitej Polskiej"
        ),
        {"electors": 30709281, "cast": 16993169, "valid": 16742938},
        {"DUDA Andrzej Sebastian": 8630627, "KOMOROWSKI Bronisław Maria": 8112311},
    ),
    (2010, 1): (
        "2010/746",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 21 czerwca 2010 r. o wynikach głosowania i wyniku wyborów "
            "Prezydenta Rzeczypospolitej Polskiej, zarządzonych na dzień 20 czerwca 2010 r."
        ),
        {"electors": 30813005, "cast": 16923832, "valid": 16806170},
        {
            "JUREK Marek": 177315,
            "KACZYŃSKI Jarosław Aleksander": 6128255,
            "KOMOROWSKI Bronisław Maria": 6981319,
            "KORWIN-MIKKE Janusz Ryszard": 416898,
            "LEPPER Andrzej Zbigniew": 214657,
            "MORAWIECKI Kornel Andrzej": 21596,
            "NAPIERALSKI Grzegorz Bernard": 2299870,
            "OLECHOWSKI Andrzej Marian": 242439,
            "PAWLAK Waldemar": 294273,
            "ZIĘTEK Bogusław Zbigniew": 29548,
        },
    ),
    (2010, 2): (
        "2010/828",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 5 lipca 2010 r. o wynikach ponownego głosowania i wyniku "
            "wyborów Prezydenta Rzeczypospolitej Polskiej"
        ),
        {"electors": 30833924, "cast": 17050417, "valid": 16853021},
        {"KACZYŃSKI Jarosław Aleksander": 7919134, "KOMOROWSKI Bronisław Maria": 8933887},
    ),
}
NOTICES |= {
    (2005, 1): (
        "2005/1647",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 10 października 2005 r. o wynikach głosowania i "
            "wyniku wyborów Prezydenta Rzeczypospolitej Polskiej, zarządzonych na dzień 9 października 2005 r."
        ),
        {"electors": 30260027, "cast": 14946689 + 99661, "valid": 14946689},
        {
            "BOCHNIARZ Henryka Teodora": 188598,
            "BOROWSKI Marek Stefan": 1544642,
            "BUBEL Leszek Henryk": 18828,
            "ILASZ Liwiusz Marian": 31691,
            "KACZYŃSKI Lech Aleksander": 4947927,
            "KALINOWSKI Jarosław": 269316,
            "KORWIN-MIKKE Janusz Ryszard": 214116,
            "LEPPER Andrzej Zbigniew": 2259094,
            "PYSZKO Jan": 10371,
            "SŁOMKA Adam Andrzej": 8895,
            "TUSK Donald Franciszek": 5429666,
            "TYMIŃSKI Stanisław": 23545,
        },
    ),
    (2005, 2): (
        "2005/1739",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 24 października 2005 r. o wynikach ponownego "
            "głosowania i wyniku wyborów Prezydenta Rzeczypospolitej Polskiej"
        ),
        {"electors": 30279209, "cast": 15279787 + 155233, "valid": 15279787},
        {"KACZYŃSKI Lech Aleksander": 8257468, "TUSK Donald Franciszek": 7022319},
    ),
    (2000, 1): (
        "2000/952",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 9 października 2000 r. o wynikach głosowania i "
            "wyniku wyborów Prezydenta Rzeczypospolitej Polskiej, zarządzonych na dzień 8 października 2000 r."
        ),
        {"electors": 29122304, "cast": 17789231, "valid": 17598919},
        {
            "GRABOWSKI Dariusz Maciej": 89002,
            "IKONOWICZ Piotr": 38672,
            "KALINOWSKI Jarosław": 1047949,
            "KORWIN-MIKKE Janusz": 252499,
            "KRZAKLEWSKI Marian": 2739621,
            "KWAŚNIEWSKI Aleksander": 9485224,
            "LEPPER Andrzej": 537570,
            "ŁOPUSZAŃSKI Jan": 139682,
            "OLECHOWSKI Andrzej Marian": 3044141,
            "PAWŁOWSKI Bogdan": 17164,
            "WAŁĘSA Lech": 178590,
            "WILECKI Tadeusz Adam": 28805,
        },
    ),
}
AMENDMENTS = {
    (2015, 1): (
        "2015/705",
        (
            "Obwieszczenie Państwowej Komisji Wyborczej z dnia 13 maja 2015 r. w sprawie zmiany obwieszczenia o wynikach "
            "głosowania i wyniku wyborów Prezydenta Rzeczypospolitej Polskiej, zarządzonych na dzień 10 maja 2015 r."
        ),
    )
}
INCUMBENTS = {2000: "KWAŚNIEWSKI Aleksander", 2015: "KOMOROWSKI Bronisław Maria", 2020: "DUDA Andrzej Sebastian"}
METHOD = (
    "National totals and the declared result as the Państwowa Komisja Wyborcza published them in its notice in the "
    "Dziennik Ustaw: voters entitled to vote, ballots cast (ballots taken from the urn), and valid votes for all "
    "candidates. Invalid is the ballots cast less the valid votes, which counts both invalid ballots and invalid votes "
    "on valid ballots."
)
GMINA_METHOD = (
    "Candidate votes and accounting summed from the polling districts of each gmina in the Krajowe Biuro Wyborcze's "
    "polling-district file, with votes from abroad and from ships each in one unit. Polling districts whose rows print "
    "no counts are left out."
)
OBWOD_METHOD = (
    "Candidate votes and accounting by polling district (obwód głosowania) as the Krajowe Biuro Wyborcze's file prints "
    "them. Polling districts are described by addresses, not mapped, so they have no area."
)
VOTES_CAST = (
    " For this election the notice and the file give votes cast (głosy oddane) rather than ballots taken from the urn, "
    "so ballots cast here are the valid ballots, and ballots found invalid as a whole are not counted."
)
SYSTEM_NOTE = (
    "The President is elected by direct, universal vote. A candidate who wins more than half of the valid votes is "
    "elected; otherwise the two candidates with the most votes face each other in a second ballot fourteen days later, "
    "and the one with more votes wins (Konstytucja, art. 127; Kodeks wyborczy, arts. 292-293). Candidates are "
    "nominated by committees of voters, each backed by 100,000 signatures."
)


def url(year: int, round_: int, kind: str) -> str:
    return f"{BASE}/{FILES[year, round_][kind]}"


def notice_url(eli: str) -> str:
    return f"{ELI}/{eli}/text.pdf"


def fetch() -> None:
    for (year, round_), files in FILES.items():
        for kind, path in files.items():
            raw.fetch(SOURCE, url(year, round_, kind), suffix=Path(path).suffix)
    for eli, _, _, _ in NOTICES.values():
        raw.fetch(SOURCE, notice_url(eli), suffix=".pdf")
    for eli, _ in AMENDMENTS.values():
        raw.fetch(SOURCE, notice_url(eli), suffix=".pdf")


def read(year: int, round_: int, kind: str) -> pl.DataFrame:
    path = raw.cached(SOURCE, url(year, round_, kind)).path
    if path.suffix in (".csv", ".xls"):
        data, member = path.read_bytes(), path.name
    else:
        archive = zipfile.ZipFile(path)
        members = [
            name for name in archive.namelist() if not name.startswith("__MACOSX") and name[-4:] in (".csv", ".xls")
        ]
        member = min(members, key=lambda name: not name.endswith(".csv"))
        data = archive.read(member)
    if member.endswith(".xls"):
        frame = pl.read_excel(io.BytesIO(data), sheet_name=SHEETS.get((year, round_)), infer_schema_length=0)
    else:
        encoding = "utf8" if "utf8" in member else "cp1250"
        frame = pl.read_csv(
            io.BytesIO(data),
            separator=";",
            infer_schema_length=0,
            encoding=encoding,
            quote_char='"' if year > 2010 else None,
        )
    frame = frame.rename({column: " ".join(column.lstrip("﻿").split()) for column in frame.columns})
    return frame.with_columns(pl.col(pl.String).str.strip_chars())


def count(column: str) -> pl.Expr:
    return pl.col(column).replace({"-": None, "": None}).cast(pl.Int64)


def gmina(code: str, place: str) -> pl.Expr:
    teryt = pl.col(code).str.zfill(6)
    lowered = pl.col(place).str.to_lowercase()
    return (
        pl.when(teryt.str.ends_with("9901") & teryt.str.starts_with("14"))
        .then(pl.lit("zagranica"))
        .when(teryt.str.ends_with("9901") | lowered.str.starts_with("stat"))
        .then(pl.lit("statki"))
        .when(lowered == "zagranica")
        .then(pl.lit("zagranica"))
        .otherwise(teryt)
        .alias("gmina")
    )


def surname_key(text: str) -> frozenset[str]:
    return frozenset(text.upper().replace("-", " ").split())


def match(headers: list[str], names: list[str]) -> dict[str, str]:
    keys = {surname_key(name): name for name in names}
    out = {}
    for header in headers:
        name = keys.get(surname_key(header))
        if name is None:
            raise ValueError(f"column {header!r} matches no candidate in the notice")
        out[header] = name
    if len(set(out.values())) != len(names):
        raise ValueError("candidate columns do not cover the notice's candidates")
    return out


def frames(year: int, round_: int) -> tuple[pl.DataFrame, list[str]]:
    data = read(year, round_, "units")
    if year == 2025:
        first = next(column for column in data.columns if column.startswith("Liczba głosów ważnych oddanych"))
        unit = [
            gmina("Teryt Gminy", "Gmina"),
            pl.col("Nr komisji").alias("commission"),
            pl.col("Siedziba").alias("seat"),
        ]
        electors = next(column for column in data.columns if column.startswith("Liczba wyborców uprawnionych"))
        cast, valid = "Liczba kart wyjętych z urny", first
    elif year == 2020:
        first = "Liczba głosów ważnych oddanych łącznie na wszystkich kandydatów"
        unit = [
            gmina("Kod TERYT", "Typ obszaru"),
            pl.col("Numer obwodu").alias("commission"),
            pl.col("Siedziba").alias("seat"),
        ]
        electors, cast, valid = "Liczba wyborców uprawnionych do głosowania", "Liczba kart wyjętych z urny", first
    elif year == 2015:
        first = "Liczba głosów ważnych" if round_ == 2 else "RAZEM"
        if round_ == 2:
            codes = read(year, 1, "units").select("Województwo", "Gmina", "Numer obwodu", "TERYT gminy")
            if codes.height != data.height or not codes.select("Województwo", "Gmina", "Numer obwodu").equals(
                data.select("Województwo", "Gmina", "Numer obwodu")
            ):
                raise ValueError("2015 second-round rows do not line up with the first round's")
            data = data.with_columns(codes["TERYT gminy"])
        unit = [
            gmina("TERYT gminy", "Gmina"),
            pl.col("Numer obwodu").alias("commission"),
            pl.lit(None, pl.String).alias("seat"),
        ]
        electors, cast, valid = (
            "Liczba wyborców uprawnionych do głosowania",
            "Liczba kart ważnych",
            "Liczba głosów ważnych",
        )
    elif year == 2010:
        first = "Głosy ważne"
        unit = [
            gmina("Kod gm.", "Gmina"),
            pl.col("Nr obwodu").alias("commission"),
            pl.col("Adres obwodu").alias("seat"),
        ]
        electors, cast, valid = "Uprawnieni do głosowania", "Głosy oddane", first
    elif year == 2005:
        data = data.filter(pl.col("TERYT").str.contains(r"^\d{6}$"))
        first = "Głosy nieważne" if round_ == 1 else "Głosy ważne"
        unit = [
            gmina("TERYT", "TERYT"),
            pl.col("Numer obwodu").alias("commission"),
            pl.col("Adres obwodu" if round_ == 1 else "Adres").alias("seat"),
        ]
        electors = "Uprawnieni do głosowania"
        cast = "Głosy oddane" if round_ == 1 else "Karty wyjęte (głosy oddane)"
        valid = "Głosy ważne"
    else:
        first = "Głosy ważne"
        unit = [gmina("Kod gminy", "Gmina"), pl.col("Nr obw.").alias("commission"), pl.col("Adres").alias("seat")]
        electors, cast, valid = "Uprawnieni", "Głosy oddane", first
    candidates = [column for column in data.columns[data.columns.index(first) + 1 :] if "UNNAMED" not in column]
    if candidates and candidates[0] == "RAZEM":
        candidates = candidates[1:]
    units = data.select(
        *unit,
        count(electors).alias("electors"),
        count(cast).alias("cast"),
        count(valid).alias("valid"),
        *(count(column).alias(column) for column in candidates),
    )
    return units, candidates


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    unit_frames, vote_frames, candidate_frames = [], [], []
    for (year, round_), day in ROUNDS.items():
        _, _, official, declared = NOTICES[year, round_]
        units, headers = frames(year, round_)
        names = match(headers, list(declared))
        if units.select("gmina", "commission").is_duplicated().any():
            raise ValueError(f"{year} round {round_}: two polling districts share a gmina and number")
        votes = (
            units.select("gmina", "commission", *headers)
            .unpivot(index=["gmina", "commission"], variable_name="header", value_name="votes")
            .filter(pl.col("votes").is_not_null())
            .with_columns(candidate=pl.col("header").replace_strict(names))
            .drop("header")
        )
        units = units.drop(headers)
        summed = votes.group_by("gmina", "commission").agg(got=pl.col("votes").sum())
        off = units.join(summed, on=["gmina", "commission"], how="left").filter(
            pl.col("valid").is_not_null() & (pl.col("got").fill_null(0) != pl.col("valid"))
        )
        if off.height:
            raise ValueError(f"{year} round {round_}: {off.height} polling districts whose votes differ from valid")
        blank = units.filter(pl.col("valid").is_null()).height
        print(f"{year} round {round_}: {units.height} polling districts, {blank} print no counts")
        for measure in ("electors", "cast", "valid"):
            got = int(units[measure].sum())
            if got != official[measure]:
                print(
                    f"  {measure}: polling districts {got:,}, notice {official[measure]:,} ({got - official[measure]:+,})"
                )
        by_candidate = dict(votes.group_by("candidate").agg(pl.col("votes").sum()).iter_rows())
        for name, total in declared.items():
            if by_candidate.get(name, 0) != total:
                print(f"  {name}: polling districts {by_candidate.get(name, 0):,}, notice {total:,}")
        tag = [pl.lit(year, pl.Int32).alias("year"), pl.lit(round_, pl.Int16).alias("round"), pl.lit(day).alias("day")]
        unit_frames.append(units.with_columns(*tag))
        vote_frames.append(votes.with_columns(*tag))
        details = pl.DataFrame({"candidate": list(declared), "notice_votes": list(declared.values())})
        if "candidates" in FILES[year, round_]:
            listed = read(year, round_, "candidates")
            if "Nazwisko i imiona" in listed.columns:
                given_surname = listed["Nazwisko i imiona"]
            else:
                given_surname = listed["Imiona"] + " " + listed["Nazwisko"]
            listed = listed.select(
                pl.Series("full", given_surname),
                pl.col("Płeć").alias("sex"),
                pl.col("Nazwa komitetu").alias("committee"),
                pl.col("Przynależność do partii").alias("membership"),
            )
            lookup = match(listed["full"].to_list(), list(declared))
            listed = listed.with_columns(candidate=pl.col("full").replace_strict(lookup)).drop("full")
            details = details.join(listed, on="candidate", how="left")
        candidate_frames.append(details.with_columns(*tag))
    for name, collected in (("units", unit_frames), ("votes", vote_frames), ("candidates", candidate_frames)):
        frame = pl.concat(collected, how="diagonal")
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


def given_first(name: str) -> str:
    surname, given = name.split(" ", 1)
    return f"{given} {surname}"


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Presidential elections of 2000 to 2025, every round, nationally from the Państwowa "
                "Komisja Wyborcza's notices and by gmina and polling district from the Krajowe Biuro Wyborcze's files"
            ),
            polity="PL",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        poland = cs.polity("PL")
        office = cs.key("pl.office", "prezydent")
        if office in cs.minted:
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
        for (year, round_), files in FILES.items():
            for kind in files:
                cs.artifact(raw.cached(SOURCE, url(year, round_, kind)), source)
        notices: dict[tuple[int, int], UUID] = {}
        for key, (eli, title, _, _) in NOTICES.items():
            notices[key] = cs.source(NOTICE_PUBLISHER, title)
            cs.add(
                "source",
                id=notices[key],
                kind="official_body",
                publisher=NOTICE_PUBLISHER,
                title=title,
                url=notice_url(eli),
                license="Public record: official legislation, free to reuse",
                license_url="https://isap.sejm.gov.pl/isap.nsf/home.xsp",
            )
            cs.artifact(raw.cached(SOURCE, notice_url(eli)), notices[key])
            if key in AMENDMENTS:
                cs.artifact(raw.cached(SOURCE, notice_url(AMENDMENTS[key][0])), notices[key])
        if conn.execute("select 1 from scheme where code = 'pl.obwod'").fetchone() is None:
            raise ValueError("publish pl.kbw_sejm first")

        system = cs.key("pl.electoral_system", "prezydent")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=poland,
            rules={"formula": "two_round", "majority": 0.5, "advance": 2},
            rules_version=1,
        )
        name(system, "Polish presidential two-round system")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

        unit_rows: dict[UUID, dict[str, str | None]] = {}
        values: list[tuple[str, str, str | None, int, int]] = []
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
        codes = conn.execute(
            "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.gmina'"
        ).fetchall()
        areas = {code[:6]: area for code, area in codes}
        gmina_units: dict[str, UUID] = {}

        def gmina_unit(code: str) -> UUID:
            if code not in gmina_units:
                unit = gmina_units[code] = cs.unit("pl.gmina", code)
                area = areas.get(code)
                unit_rows[unit] = {
                    "scheme_code": "pl.gmina",
                    "code": code,
                    "parent_id": None,
                    "area_relation": "equals" if area is not None else "unlocated",
                    "area_id": None if area is None else str(area),
                    "kind": "standard" if code.isdigit() else "aggregate",
                }
            return gmina_units[code]

        def put(result_set: UUID, unit: UUID, row: dict[str, int | None]) -> None:
            if row["valid"] is None:
                return
            for measure, value in (
                (REGISTERED, row["electors"]),
                (BALLOTS, row["cast"]),
                (VALID, row["valid"]),
                (INVALID, None if row["cast"] is None else row["cast"] - row["valid"]),
            ):
                if value is not None:
                    values.append((str(result_set), str(unit), None, measure, value))

        people: dict[str, UUID] = {}
        contests: dict[tuple[int, int], UUID] = {}
        for (year, round_), day in ROUNDS.items():
            key = f"{day}:prezydent"
            runoff = round_ == 2
            election = cs.key("pl.election", f"{day}:{'runoff' if runoff else 'presidential'}")
            cs.add(
                "election",
                id=election,
                polity_id=poland,
                kind="runoff" if runoff else "general",
                date_from=day,
                date_to=day,
                status="held",
            )
            label = f"{year} Polish presidential election{', second round' if runoff else ''}"
            name(election, label)
            name(
                election,
                f"Wybory Prezydenta Rzeczypospolitej Polskiej {year}{', ponowne głosowanie' if runoff else ''}",
                "pl",
                "native",
            )
            path = f"pl/{day}-presidential{'-runoff' if runoff else ''}"
            cs.add("slug", path=path, entity_id=election)
            contest = contests[year, round_] = cs.key(f"{SOURCE}.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                magnitude=1,
                round_number=round_,
                previous_round_id=contests[year, 1] if runoff else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"{year} Polish presidential election, {'second' if runoff else 'first'} round")
            cs.add("slug", path=f"{path}/president", entity_id=contest)

            _, _, official, declared = NOTICES[year, round_]
            ranked = sorted(declared, key=lambda candidate: -declared[candidate])
            winner = ranked[0] if declared[ranked[0]] * 2 > official["valid"] else None
            advanced = set() if winner is not None or runoff else set(ranked[:2])
            if runoff and winner is None:
                raise ValueError(f"{key}: no candidate has a majority in the second round")

            def make_set(label: str, default: bool, completeness: float, note: str, origin: UUID) -> UUID:
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified" if default else "official",
                    kind="counted",
                    status="active",
                    source_id=origin,
                    is_default=default,
                    completeness=completeness,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                return result_set

            extra = VOTES_CAST if year <= 2015 else ""
            national = make_set("national", True, 1, METHOD + extra, notices[year, round_])
            put(national, nation, dict(official))
            here = candidates.filter((pl.col("year") == year) & (pl.col("round") == round_))
            options: dict[str, UUID] = {}
            for position, row in enumerate(here.sort("candidate").iter_rows(named=True), 1):
                candidate = row["candidate"]
                ballot = given_first(candidate)
                option = options[candidate] = cs.key(f"{SOURCE}.option", f"{key}:{slug(candidate)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=display(ballot, "pl"),
                )
                identity = ALIASES.get(candidate, candidate)
                if identity not in people:
                    person = people[identity] = cs.key(f"{SOURCE}.person", slug(identity))
                    cs.add(
                        "person",
                        id=person,
                        gender={"Kobieta": "female", "Mężczyzna": "male"}.get(row.get("sex") or ""),
                    )
                    name(person, display(given_first(identity), "pl"))
                candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{slug(candidate)}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=people[identity],
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                    is_incumbent=True if INCUMBENTS.get(year) == candidate else None,
                )
                values.append((str(national), str(nation), str(option), VOTES, declared[candidate]))
                kind = (
                    "elected"
                    if candidate == winner
                    else "advanced"
                    if candidate in advanced
                    else "lost"
                    if runoff or winner is not None
                    else "eliminated"
                )
                cs.add("outcome", result_set_id=national, option_id=option, kind=kind)

            local = units.filter((pl.col("year") == year) & (pl.col("round") == round_))
            local_votes = votes.filter((pl.col("year") == year) & (pl.col("round") == round_))
            completeness = min(int(local_votes["votes"].sum()) / official["valid"], 1)
            gmina_set = make_set("gmina", False, completeness, GMINA_METHOD + extra, source)
            obwod_set = make_set("obwod", False, completeness, OBWOD_METHOD + extra, source)
            totals = (
                local.filter(pl.col("valid").is_not_null())
                .group_by("gmina")
                .agg(pl.col("electors", "cast", "valid").sum())
            )
            for row in totals.iter_rows(named=True):
                put(gmina_set, gmina_unit(row["gmina"]), row)
            for row in local_votes.group_by("gmina", "candidate").agg(pl.col("votes").sum()).iter_rows(named=True):
                values.append(
                    (str(gmina_set), str(gmina_unit(row["gmina"])), str(options[row["candidate"]]), VOTES, row["votes"])
                )
            lookup: dict[tuple[str, str], UUID] = {}
            for row in local.iter_rows(named=True):
                code = f"{day}:{row['gmina']}:{row['commission']}"
                unit = lookup[row["gmina"], row["commission"]] = cs.unit("pl.obwod", code)
                unit_rows[unit] = {
                    "scheme_code": "pl.obwod",
                    "code": code,
                    "parent_id": str(gmina_unit(row["gmina"])),
                    "area_relation": "unlocated",
                    "area_id": None,
                    "kind": "standard" if row["gmina"].isdigit() else "aggregate",
                }
                put(obwod_set, unit, row)
            for row in local_votes.iter_rows(named=True):
                values.append(
                    (
                        str(obwod_set),
                        str(lookup[row["gmina"], row["commission"]]),
                        str(options[row["candidate"]]),
                        VOTES,
                        row["votes"],
                    )
                )

        minted = [
            {"id": str(unit), "polity_id": str(poland), **row} for unit, row in unit_rows.items() if unit in cs.minted
        ]
        cs.frame(
            "reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String, "area_id": pl.String})
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
