import io
import os
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import display
from ea.sources.pl.kbw_sejm import ITEMS
from ea.sources.pl.prg import VOIVODESHIPS
from ea.store import connect

SOURCE = "pl.kbw_sejmik"
VERSION = "1"
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


def fetch() -> None:
    for path in [*FILES.values(), *UNIT_FILES.values()]:
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


def emit() -> Path:
    year = int(os.environ["SEJMIK_YEAR"])
    frame = pl.read_parquet(PARSED / f"{year}.parquet")
    units = pl.read_parquet(PARSED / f"{year}-units.parquet") if year in UNIT_FILES else None
    day = ELECTIONS[year]
    native = sejmik_names()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Voivodeship assembly elections of {day.year} by constituency, with committees, candidates, "
                "declared seats, and elected members, from the Krajowe Biuro Wyborcze's candidate file"
            ),
            polity="PL",
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
        cs.artifact(raw.cached(SOURCE, f"{BASE}/{FILES[year]}"), source)
        if units is not None:
            cs.artifact(raw.cached(SOURCE, f"{BASE}/{UNIT_FILES[year]}"), source)
        if conn.execute("select 1 from scheme where code = 'pl.okreg_sejmik'").fetchone() is None:
            cs.add(
                "scheme",
                code="pl.okreg_sejmik",
                polity_id=poland,
                kind="electoral_district",
                rank=2,
                noun="voivodeship assembly constituency",
            )

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
        existing = {row[0] for row in conn.execute("select id from party where polity_id = %s", (poland,))}
        parties: dict[str, UUID] = {}

        def party_for(committee: str) -> UUID:
            label = " ".join(committee.split())
            if label in parties:
                return parties[label]
            item = next((code for text, code in ITEMS.items() if label.upper().rstrip('"').endswith(text)), None)
            if item is not None and item in by_item:
                party = parties[label] = by_item[item]
                return party
            party = parties[label] = cs.key("pl.party", slug(label))
            if party not in existing:
                cs.add("party", id=party, kind="party", polity_id=poland)
                name(party, label, "pl")
                existing.add(party)
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
        methods: dict[UUID, str] = {}
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
                unit_rows[unit] = {
                    "scheme_code": "pl.okreg_sejmik",
                    "code": key,
                    "parent_id": str(region_unit),
                    "area_relation": "unlocated",
                    "area_id": None,
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
                methods[result_set] = note
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

        for note, entity, body in conn.execute(
            """select n.id, n.entity_id, n.body_md from note n
               join changeset w on w.id = n.changeset_id and w.pipeline = %s
               where n.kind = 'methodology' and n.entity_id = any(%s)""",
            [SOURCE, list(methods)],
        ).fetchall():
            if body != methods[entity]:
                cs.add(
                    "note_drop",
                    note_id=note,
                    entity_id=entity,
                    body_md=body,
                    reason="the result set's votes are now summed from the polling district protocols",
                )
        minted = [
            {"id": str(unit), "polity_id": str(poland), **row}
            for unit, row in unit_rows.items()
            if unit in cs.minted or row["scheme_code"] == "pl.okreg_sejmik"
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
