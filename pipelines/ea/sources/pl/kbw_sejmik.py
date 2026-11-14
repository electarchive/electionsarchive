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
    for path in FILES.values():
        raw.fetch(SOURCE, f"{BASE}/{path}", suffix=Path(path).suffix)


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


VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
METHOD = (
    "List votes, declared seats, and elected members for one constituency of a voivodeship assembly from the "
    "Krajowe Biuro Wyborcze's candidate file for the local elections. Voters choose one candidate, so each list's "
    "votes are the sum of its candidates' votes; valid votes are the figure the file prints for the constituency "
    "where it prints one, else the sum of the candidates' votes. Each elected member is the candidate the file "
    "marks as elected."
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

        unit_rows: dict[UUID, dict[str, str | None]] = {}
        values: list[tuple[str, str, str | None, int, int]] = []
        for code in sorted(set(frame["woj"])):
            region = cs.polity(f"PL-{code}")
            english = VOIVODESHIPS[code][1]
            region_slug = slug(VOIVODESHIPS[code][0])
            body = cs.key("pl.body", f"sejmik-{code}")
            cs.add("body", id=body, polity_id=region, kind="council")
            name(body, f"Assembly of {english.replace(' Voivodeship', '')} Voivodeship")
            name(body, native[code], "pl", "native")
            seats_total = int(frame.filter(pl.col("woj") == code)["elected"].sum())
            cs.add("body_size", body_id=body, seats=seats_total, valid_from=day)
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
                valid = part["valid"][0] if part["valid"][0] is not None else counted
                note = METHOD
                if valid != counted:
                    note += (
                        f" The file prints {valid:,} valid votes for the constituency and its candidates' votes "
                        f"sum to {counted:,}; both are stored as printed."
                    )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                figures = [(VALID, valid)]
                if part["electors"][0] is not None:
                    figures.append((REGISTERED, part["electors"][0]))
                if part["cast"][0] is not None:
                    figures.extend([(BALLOTS, part["cast"][0]), (INVALID, part["cast"][0] - valid)])
                for measure, value in figures:
                    values.append((str(result_set), str(unit), None, measure, int(value)))
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
