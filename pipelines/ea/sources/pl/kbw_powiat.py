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
from ea.sources.pl import kbw_sejmik
from ea.sources.pl.kbw_sejm import ITEMS
from ea.sources.pl.prg import VOIVODESHIPS
from ea.store import connect

SOURCE = "pl.kbw_powiat"
VERSION = "1"
PUBLISHER = kbw_sejmik.PUBLISHER
TITLE = "Dane wyborcze: wybory do rad powiatów"
PAGE = kbw_sejmik.PAGE
BASE = kbw_sejmik.BASE
PARSED = raw.parsed(SOURCE)
ELECTIONS = {1998: date(1998, 10, 11)} | kbw_sejmik.ELECTIONS
OWN = {
    1998: "1998/1998-kand-rady.xlsx",
    2024: "2024/samorzad/kandydaci_rady_powiatow_csv.zip",
}
DISTRICTS_2024 = "2024/samorzad/okregi_rady_powiatow_csv.zip"
COLUMNS = kbw_sejmik.COLUMNS | {
    1998: {
        "unit": "Szczebel",
        "woj": "TERYT",
        "okr": "Nr okr.",
        "list": "Nr listy",
        "committee": "Komitet",
        "pos": "Poz.",
        "votes": "Gł. kand.",
        "seat": "Mand.",
        "sex": "Płeć",
        "family": "Nazwisko",
    }
}
GIVEN = kbw_sejmik.GIVEN | {1998: ("Imiona",)}
LEVEL = {1998: "powiat", 2002: "powiat", 2006: "PO", 2010: "powiat", 2014: "powiat", 2018: "powiat"}
ELECTED = ["W", "L", "B", "P", "G", "T"]
SEX = kbw_sejmik.SEX
DISSOLVED = {"1431"}


def artifact(year: int) -> raw.Artifact:
    if year in OWN:
        return raw.cached(SOURCE, f"{BASE}/{OWN[year]}")
    return raw.cached(kbw_sejmik.SOURCE, f"{BASE}/{kbw_sejmik.FILES[year]}")


def fetch() -> None:
    for path in [*OWN.values(), DISTRICTS_2024]:
        raw.fetch(SOURCE, f"{BASE}/{path}", suffix=Path(path).suffix)
    kbw_sejmik.fetch()


def _csv(url: str) -> pl.DataFrame:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    [member] = archive.namelist()
    frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0, encoding="utf8")
    return frame.rename({column: column.lstrip("﻿") for column in frame.columns})


def load(year: int) -> pl.DataFrame:
    if year == 2024:
        frame = _csv(f"{BASE}/{OWN[2024]}")
        return frame.select(
            teryt=pl.col("TERYT Powiatu").str.slice(0, 4),
            unit=pl.col("Powiat").str.strip_chars(),
            council=pl.col("Rada").str.strip_chars(),
            okr=pl.col("Nr okręgu").cast(pl.Int64),
            list=pl.col("Nr listy").cast(pl.Int64),
            committee=pl.col("Nazwa komitetu").str.strip_chars(),
            pos=pl.col("Pozycja na liście").cast(pl.Int64),
            name=pl.col("Nazwisko i imiona").str.strip_chars(),
            sex=pl.col("Płeć").replace_strict(SEX),
            votes=pl.col("Liczba głosów").cast(pl.Int64, strict=False),
            elected=pl.col("Czy uzyskał mandat") == "Tak",
            valid=pl.col("Liczba głosów ważnych oddanych w okręgu").cast(pl.Int64),
            electors=pl.lit(None, pl.Int64),
            cast=pl.lit(None, pl.Int64),
        )
    spec = COLUMNS[year]
    frame = pl.read_excel(artifact(year).path, infer_schema_length=0)
    frame = frame.rename({column: " ".join(column.split()) for column in frame.columns})
    frame = frame.filter(pl.col(spec["unit"]) == LEVEL[year])
    given = pl.concat_str([pl.col(c) for c in GIVEN[year]], separator=" ", ignore_nulls=True)
    printed = {1998: ("Gł. ważne", None, None), 2006: ("Ważne", "Upr.", "Odd."), 2014: ("Ważne", None, None)}
    valid, electors, cast = printed.get(year, (None, None, None))
    return frame.select(
        teryt=pl.col("TERYT").str.slice(0, 4),
        unit=pl.col("Jednostka").str.strip_chars(),
        council=pl.lit(None, pl.String),
        okr=pl.col(spec["okr"]).cast(pl.Int64),
        list=pl.col(spec["list"]).cast(pl.Int64),
        committee=pl.col(spec["committee"]).str.strip_chars(),
        pos=pl.col(spec["pos"]).cast(pl.Int64),
        name=pl.concat_str([pl.col(spec["family"]), given], separator=" ", ignore_nulls=True).str.strip_chars(),
        sex=pl.col(spec["sex"]).replace_strict(SEX, default=None),
        votes=pl.col(spec["votes"]).cast(pl.Int64),
        elected=pl.col(spec["seat"]).is_in(ELECTED),
        valid=pl.col(valid).cast(pl.Int64) if valid else pl.lit(None, pl.Int64),
        electors=pl.col(electors).cast(pl.Int64) if electors else pl.lit(None, pl.Int64),
        cast=pl.col(cast).cast(pl.Int64) if cast else pl.lit(None, pl.Int64),
    )


def parse() -> None:
    years = [int(y) for y in os.environ["POWIAT_YEAR"].split(",")] if "POWIAT_YEAR" in os.environ else list(ELECTIONS)
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in years:
        frame = load(year)
        if year == 2024:
            seats = _csv(f"{BASE}/{DISTRICTS_2024}").select(
                teryt=pl.col("TERYT Powiatu").str.slice(0, 4),
                okr=pl.col("Numer okręgu").cast(pl.Int64),
                seats=pl.col("Liczba mandatów").cast(pl.Int64),
                registered=pl.col("Wyborcy").cast(pl.Int64),
            )
            elected = frame.group_by("teryt", "okr").agg(pl.col("elected").sum())
            check = seats.join(elected, on=["teryt", "okr"], how="full", coalesce=True)
            wrong = check.filter(pl.col("seats") != pl.col("elected"))
            if wrong.height:
                print("2024 seats differ from the district file", wrong.rows())
            frame = frame.drop("electors").join(
                seats.select("teryt", "okr", electors="registered"), on=["teryt", "okr"], how="left"
            )
        frame.with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(ELECTIONS[year])).write_parquet(
            PARSED / f"{year}.parquet"
        )
        print(
            year,
            frame.height,
            frame["elected"].sum(),
            frame["teryt"].n_unique(),
            frame.select("teryt", "okr").n_unique(),
        )


VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
METHOD = (
    "List votes, declared seats, and elected members for one constituency of a county council from the Krajowe "
    "Biuro Wyborcze's candidate file for the local elections. Voters choose one candidate, so each list's votes are "
    "the sum of its candidates' votes; valid votes are the figure the file prints for the constituency where it "
    "prints one, else the sum of the candidates' votes. Each elected member is the candidate the file marks as "
    "elected."
)
TACIT = (
    "No vote was held in this constituency because no more candidates stood than there were seats, so the "
    "Krajowe Biuro Wyborcze's candidate file marks every candidate elected without a vote."
)
SYSTEM = (
    "Seats are shared in each constituency by the D'Hondt method among lists of committees that won 5 percent of "
    "the valid votes in the whole powiat (Ordynacja wyborcza do rad gmin, rad powiatów i sejmików województw of 16 "
    "July 1998, then Kodeks wyborczy, art. 416). Elected members are the candidates with the most votes on each "
    "list. The threshold counts votes across the powiat's constituencies, so it is stored with scope national, "
    "which here means all sibling constituencies of one council."
)
SYSTEM_2006 = (
    "Seats are shared in each constituency among lists of committees that won 5 percent of the valid votes in the "
    "whole powiat, with lists able to join in blocks that took seats as one list before sharing them (Ordynacja "
    "wyborcza do rad gmin, rad powiatów i sejmików województw, as amended on 6 September 2006). The file does not "
    "say which lists formed blocks, so the declared seats are stored as printed."
)


def emit() -> Path:
    year = int(os.environ["POWIAT_YEAR"])
    frame = pl.read_parquet(PARSED / f"{year}.parquet")
    day = ELECTIONS[year]
    councils = dict(
        pl.read_parquet(PARSED / "2024.parquet").select("teryt", "council").unique().iter_rows()
        if (PARSED / "2024.parquet").exists()
        else []
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"County council elections of {day.year} by constituency, with committees, candidates, declared "
                "seats, and elected members, from the Krajowe Biuro Wyborcze's candidate file"
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
        cs.artifact(artifact(year), source)
        if year == 2024:
            cs.artifact(raw.cached(SOURCE, f"{BASE}/{DISTRICTS_2024}"), source)
        if conn.execute("select 1 from scheme where code = 'pl.okreg_powiat'").fetchone() is None:
            cs.add(
                "scheme",
                code="pl.okreg_powiat",
                polity_id=poland,
                kind="electoral_district",
                rank=3,
                noun="county council constituency",
            )

        blocks = year == 2006
        system = cs.key("pl.electoral_system", "rada-powiatu-2006" if blocks else "rada-powiatu")
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
            "County council open-list D'Hondt system with blocks (2006)"
            if blocks
            else "County council open-list D'Hondt system",
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
        counties = dict(
            conn.execute(
                """select x.value, x.entity_id from external_id x join polity p on p.id = x.entity_id
                   where x.scheme = 'pl.prg.polity' and p.kind = 'county'"""
            ).fetchall()
        )
        powiat_areas = {
            row[0]
            for row in conn.execute(
                "select c.code from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.powiat'"
            )
        }
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
        tacit_count = 0
        for code in sorted(set(frame["teryt"]) - DISSOLVED):
            county = frame.filter(pl.col("teryt") == code)
            label = county["unit"][0]
            if code not in counties:
                raise ValueError(f"{year} powiat {code} {label} has no county polity")
            polity = counties[code]
            body = cs.key("pl.body", f"rada-powiatu-{code}")
            cs.add("body", id=body, polity_id=polity, kind="council")
            name(body, f"Council of powiat {label}")
            name(body, councils.get(code) or f"Rada Powiatu {label}", "pl", "native")
            cs.add("body_size", body_id=body, seats=int(county["elected"].sum()), valid_from=day)
            county_unit = cs.unit("pl.powiat", code)
            if code not in powiat_areas:
                raise ValueError(f"{year} powiat {code} {label} has no area")
            unit_rows[county_unit] = {
                "scheme_code": "pl.powiat",
                "code": code,
                "parent_id": None,
                "area_relation": "equals",
                "area_id": str(cs.area("pl.powiat", code)),
                "kind": "standard",
            }
            county_slug = f"{slug(VOIVODESHIPS[code[:2]][0])}/{slug(label)}"
            for okr in sorted(set(county["okr"])):
                part = county.filter(pl.col("okr") == okr)
                key = f"{day}:{code}:{okr}"
                seats = int(part["elected"].sum())
                counted = int(part["votes"].sum())
                tacit = counted == 0
                tacit_count += tacit
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    magnitude=seats,
                    electoral_system_id=system,
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"Council of powiat {label}, constituency {okr}")
                name(
                    contest, f"{councils.get(code) or f'Rada Powiatu {label}'}, okręg wyborczy nr {okr}", "pl", "native"
                )
                cs.add("slug", path=f"{path}/rada-powiatu/{county_slug}/{okr}", entity_id=contest)
                lists = (
                    part.group_by("list")
                    .agg(
                        pl.col("votes").sum(),
                        pl.col("elected").sum().alias("seats"),
                        pl.col("committee").first(),
                    )
                    .sort("votes", "list", descending=[True, False])
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
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                if tacit:
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=TACIT)
                else:
                    unit = cs.unit("pl.okreg_powiat", key)
                    unit_rows[unit] = {
                        "scheme_code": "pl.okreg_powiat",
                        "code": key,
                        "parent_id": str(county_unit),
                        "area_relation": "unlocated",
                        "area_id": None,
                        "kind": "standard",
                    }
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
                    for row in lists.iter_rows(named=True):
                        values.append((str(result_set), str(unit), str(options[row["list"]]), VOTES, row["votes"]))
                for row in lists.iter_rows(named=True):
                    if row["seats"]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=options[row["list"]],
                            kind="won",
                            seats=row["seats"],
                        )
                elected = Counter()
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
        print(year, "tacit constituencies", tacit_count)

        minted = [
            {"id": str(unit), "polity_id": str(poland), **row}
            for unit, row in unit_rows.items()
            if unit in cs.minted or row["scheme_code"] == "pl.okreg_powiat"
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
