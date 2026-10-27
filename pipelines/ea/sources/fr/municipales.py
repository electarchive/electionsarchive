import os
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr.interieur import ACCOUNTING
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.municipales"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ROUNDS = {1: date(2026, 3, 15), 2: date(2026, 3, 22)}
STATIC = "https://static.data.gouv.fr/resources"
T1 = f"{STATIC}/elections-municipales-2026-resultats-du-premier-tour"
T2 = f"{STATIC}/elections-municipales-2026-resultats-du-scond-tour"
C1 = f"{STATIC}/elections-municipales-2026-listes-candidates-au-premier-tour"
C2 = f"{STATIC}/elections-municipales-2026-listes-candidates-au-second-tour"
PLM = "conseils-darrondissement-paris-lyon-marseille-2026"
FILES = {
    (1, "communes"): f"{T1}/20260320-164339/municipales-2026-resultats-communes-2026-03-20.csv",
    (1, "elected"): f"{T1}/20260320-164100/municipales-2026-candidats-elus-france-entiere-tour-1-2026-03-20.csv",
    (1, "candidates"): f"{C1}/20260313-152615/municipales-2026-candidatures-france-entiere-tour-1-2026-03-13.csv",
    (1, "sectors"): f"{T1}/20260316-160627/{PLM}-resultats-secteurs-2026-03-16.csv",
    (1, "sector_elected"): f"{T1}/20260316-162449/{PLM}-candidats-elus-tour-1-2026-03-16.csv",
    (1, "sector_candidates"): f"{C1}/20260313-151835/{PLM}-candidatures-france-entiere-tour-1-2026-03-13.csv",
    (2, "communes"): f"{T2}/20260323-180124/municipales-2026-resultats-communes-2026-03-23-16h14.csv",
    (2, "elected"): f"{T2}/20260323-180122/municipales-2026-candidats-elus-france-entiere-tour-2-2026-03-23.csv",
    (2, "candidates"): f"{C2}/20260320-141955/municipales-2026-candidatures-france-entiere-tour-2-2026-03-20.csv",
    (
        2,
        "sectors",
    ): f"{T2}/20260323-180123/conseils-d-39-arrondissement-paris-lyon-marseille-2026-resultats-secteurs-2026-03-23-16h22.csv",
    (2, "sector_elected"): f"{T2}/20260323-180119/{PLM}-candidats-elus-france-entiere-tour-2-2026-03-23.csv",
    (2, "sector_candidates"): f"{C2}/20260318-184818/{PLM}-candidatures-france-entiere-tour-2-2026-03-18.csv",
}
SEATS = (
    f"{STATIC}/elections-municipales-2026-nombre-de-sieges-a-pourvoir-aux-conseils-munciipaux-et-conseils-communautaires"
    "/20260228-002140/27-02-2026-mun-26-referentiels-nombre-de-cm-et-cc-a-pourvoir.xlsx"
)
DATASETS = (
    "https://www.data.gouv.fr/datasets/elections-municipales-2026-resultats-du-premier-tour",
    "https://www.data.gouv.fr/datasets/elections-municipales-2026-resultats-du-second-tour",
)
CITIES = {"75056", "69123", "13055"}
NOTE = (
    "Each commune elects its municipal council from closed lists, in up to two rounds. A list that wins an absolute "
    "majority of the valid votes in the first round, or the most votes in the second, receives half of the seats, "
    "rounded up when more than four seats are at stake and down when fewer than four are. The remaining seats are "
    "shared by the highest-average (D'Hondt) method among all lists with at least 5 percent of the valid votes, the "
    "leading list included, and go to candidates in list order. Only lists with at least 10 percent of the "
    "first-round vote may stand in the second round, and they may merge with lists that took at least 5 percent. "
    "From 2026 the same system applies in communes of fewer than 1,000 inhabitants, where a list may have up to two "
    "fewer candidates than there are seats; seats that a list cannot fill stay vacant."
)
NOTE_CITIES = (
    "Since the 2025 reform, the Council of Paris and the municipal councils of Lyon and Marseille are elected "
    "citywide, on a ballot separate from the sector councils. A list that wins an absolute majority of the valid "
    "votes in the first round, or the most votes in the second, receives a quarter of the seats. The remaining seats "
    "are shared by the highest-average (D'Hondt) method among all lists with at least 5 percent of the valid votes, "
    "the leading list included. Only lists with at least 10 percent of the first-round vote may stand in the second "
    "round."
)
SEATS_CAVEAT = (
    "The ministry's results file gives this list {seats} seats, but its file of elected councilors names {named} of "
    "the list's candidates as elected."
)
OVER_CAVEAT = (
    "The ministry's files give {lists} {total} seats and name {named} candidates as elected, more than the "
    "council's {size} seats."
)
OVER_SINGLE = (
    " As the only list, it holds every seat, so the archive records {size} seats and the first {size} candidates on "
    "the list as elected."
)
OVER_SEVERAL = " The archive records no seats or elected councilors for this round until a corrected return is found."
TURNOUT_CAVEAT = (
    "The ministry's results file reports more voters ({voters}) than registered voters ({registered}) in this commune."
)
RULES = {
    "formula": "dhondt",
    "tieBreak": "votes",
    "threshold": {"percent": 5, "level": "list", "scope": "contest"},
    "lists": {"order": "closed", "exhausted": "vacant", "vacancy": "next_on_list"},
}
BONUS = {"level": "list", "share": {"percent": 50, "strict": True, "rounds": [1]}}
HALF = {"kind": "fraction", "fraction": 0.5, "rounding": "up", "below": {"seats": 4, "rounding": "down"}}
QUARTER = {"kind": "fraction", "fraction": 0.25, "rounding": "up"}


def fetch() -> None:
    for url in [*FILES.values(), SEATS]:
        raw.fetch(SOURCE, url, suffix=f".{url.rsplit('.', 1)[1]}")


def parse() -> None:
    accounting: list[pl.DataFrame] = []
    lists: list[pl.DataFrame] = []
    members: list[pl.DataFrame] = []
    for round_ in ROUNDS:
        for kind, unit_column, seats_column in (
            ("communes", "Code commune", "Sièges au CM"),
            ("sectors", "Code secteur", "Sièges"),
        ):
            frame = pl.read_csv(
                raw.cached(SOURCE, FILES[round_, kind]).path,
                separator=";",
                infer_schema_length=0,
                truncate_ragged_lines=True,
                null_values=[""],
            ).rename({unit_column: "unit", unit_column.replace("Code", "Libellé"): "label"})
            index = [
                pl.lit(round_).alias("round"),
                pl.lit(kind == "sectors").alias("sector"),
                pl.col("unit"),
                pl.col("label"),
                pl.col("Code département").alias("department"),
            ]
            accounting.append(
                frame.select(*index, *(pl.col(field).cast(pl.Int64).alias(field) for field in ACCOUNTING))
            )
            count = max(int(column.rsplit(" ", 1)[1]) for column in frame.columns if column.startswith("Voix "))
            lists.append(
                pl.concat(
                    frame.select(
                        *index[:3],
                        panel=pl.coalesce(pl.col(f"Numéro de panneau {i}").cast(pl.Int64), pl.lit(i)),
                        printed=pl.col(f"Numéro de panneau {i}").cast(pl.Int64),
                        nuance=pl.col(f"Nuance liste {i}"),
                        short=pl.col(f"Libellé abrégé de liste {i}"),
                        list_label=pl.col(f"Libellé de liste {i}"),
                        votes=pl.col(f"Voix {i}").cast(pl.Int64),
                        seats=pl.col(f"{seats_column} {i}").cast(pl.Int64),
                    )
                    for i in range(1, count + 1)
                ).drop_nulls("votes")
            )
            prefix = "sector_" if kind == "sectors" else ""
            key = ["unit", "surname", "forename", "sex"]
            candidates = (
                pl.scan_csv(
                    raw.cached(SOURCE, FILES[round_, f"{prefix}candidates"]).path,
                    separator=";",
                    infer_schema_length=0,
                    null_values=[""],
                )
                .select(
                    unit=pl.col("Code circonscription"),
                    panel=pl.col("Numéro de panneau").cast(pl.Int64),
                    list_label=pl.col("Libellé de la liste"),
                    position=pl.col("Ordre").cast(pl.Int64),
                    surname=pl.col("Nom sur le bulletin de vote"),
                    forename=pl.col("Prénom sur le bulletin de vote"),
                    sex=pl.col("Sexe"),
                    nuance_label=pl.col("Nuance de liste"),
                )
                .collect()
                .join(
                    lists[-1]
                    .select("unit", "list_label", by_label="panel")
                    .unique(["unit", "list_label"], keep="none"),
                    on=["unit", "list_label"],
                    how="left",
                )
                .with_columns(panel=pl.coalesce("panel", "by_label"))
                .join(lists[-1].select("unit", "panel", "seats"), on=["unit", "panel"], how="left")
            )
            elected = pl.read_csv(
                raw.cached(SOURCE, FILES[round_, f"{prefix}elected"]).path,
                separator=";",
                infer_schema_length=0,
                null_values=[""],
            ).select(
                unit=pl.col("CODSECT" if prefix else "CODCOM"),
                surname=pl.col("NOMPSN"),
                forename=pl.col("PREPSN"),
                sex=pl.col("SEXPSN"),
                legal=pl.col("EFFECTIF_LEGAL").cast(pl.Int64),
            )
            print(
                "candidates without a list:",
                candidates.filter(pl.col("panel").is_null()).group_by("unit", "list_label").len().rows(),
            )
            candidates = candidates.drop_nulls("panel")
            wanted = elected.group_by(key).agg(wanted=pl.len())
            hits = candidates.join(wanted, on=key).with_columns(
                rows=pl.len().over(key),
                fits=(pl.col("position") <= pl.col("seats").fill_null(0)).sum().over(key),
            )
            chosen = pl.concat(
                [
                    hits.filter(pl.col("rows") == pl.col("wanted")),
                    hits.filter(
                        (pl.col("rows") != pl.col("wanted"))
                        & (pl.col("fits") == pl.col("wanted"))
                        & (pl.col("position") <= pl.col("seats").fill_null(0))
                    ),
                ]
            )
            unresolved = wanted.join(chosen.select(key).unique(), on=key, how="anti")
            print(f"round {round_} {kind}: {elected.height} elected, {unresolved.height} names unresolved")
            print(unresolved.head(20))
            legal = elected.unique("unit").select("unit", "legal")
            members.append(
                pl.concat(
                    [
                        chosen.join(legal, on="unit", how="left").select(
                            pl.lit(round_).alias("round"),
                            "unit",
                            "panel",
                            "position",
                            "surname",
                            "forename",
                            "sex",
                            "legal",
                            elected=pl.lit(True),
                        ),
                        candidates.filter(pl.col("position") == 1)
                        .join(chosen, on=["unit", "panel", "position"], how="anti")
                        .select(
                            pl.lit(round_).alias("round"),
                            "unit",
                            "panel",
                            "position",
                            "surname",
                            "forename",
                            "sex",
                            legal=pl.lit(None, pl.Int64),
                            elected=pl.lit(False),
                        ),
                    ]
                )
            )
            lists[-1] = lists[-1].join(
                candidates.unique(["unit", "panel"]).select("unit", "panel", "nuance_label"),
                on=["unit", "panel"],
                how="left",
            )
    accounting_frame = pl.concat(accounting)
    list_frame = pl.concat(lists)
    member_frame = pl.concat(members)
    won = member_frame.filter("elected").group_by("round", "unit", "panel").len()
    short = list_frame.join(won, on=["round", "unit", "panel"], how="left").filter(
        pl.col("seats") != pl.col("len").fill_null(0)
    )
    print("lists whose elected members differ from their seats:", short.height)
    print(short.head(10))
    referential = (
        pl.read_excel(raw.cached(SOURCE, SEATS).path, infer_schema_length=0)
        .filter(pl.col("CODE_COM_ASSOCIEE").is_null())
        .select(
            unit=pl.col("CODE_COMMUNE").str.zfill(5),
            population=pl.col("POPULATION").cast(pl.Int64),
            magnitude=pl.col("NBRE_SAP_COM").cast(pl.Int64),
        )
    )
    legal = member_frame.drop_nulls("legal").unique(["round", "unit"]).select("unit", "legal")
    differ = referential.join(legal, on="unit").filter(pl.col("magnitude") != pl.col("legal"))
    if differ.height:
        raise ValueError(
            f"{differ.height} communes whose seats differ from the elected file's legal size\n{differ.head()}"
        )
    print(accounting_frame.group_by("round", "sector").len().sort("round", "sector"))
    PARSED.mkdir(parents=True, exist_ok=True)
    accounting_frame.write_parquet(PARSED / "accounting.parquet")
    list_frame.write_parquet(PARSED / "lists.parquet")
    member_frame.write_parquet(PARSED / "members.parquet")
    referential.join(legal.unique("unit"), on="unit", how="full", coalesce=True).write_parquet(PARSED / "seats.parquet")


def emit() -> Path:
    scope = os.environ["MUN_SCOPE"]
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    communes = accounting.filter(~pl.col("sector")).unique("unit", keep="first").sort("unit")
    base = communes.with_columns(base=pl.col("label").map_elements(slug, return_dtype=pl.String))
    base = base.with_columns(
        path=pl.when(pl.col("base").is_duplicated())
        .then(pl.col("base") + "-" + pl.col("department").str.to_lowercase())
        .otherwise("base")
    ).with_columns(
        path=pl.when(pl.col("path").is_duplicated()).then(pl.col("base") + "-" + pl.col("unit")).otherwise("path")
    )
    paths = dict(base.select("unit", "path").iter_rows())
    population = dict(seats.select("unit", "population").iter_rows())
    magnitude = dict(seats.select("unit", pl.coalesce("magnitude", "legal")).iter_rows())
    large = {code for code in paths if code in CITIES or (population.get(code) or 0) >= 1000}
    chosen = sorted(large if scope == "large" else set(paths) - large)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"2026 French municipal elections, both rounds, by commune ({scope} communes)",
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        france = cs.polity("FR")
        source = cs.source("Ministère de l'Intérieur", "Élections municipales des 15 et 22 mars 2026")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Ministère de l'Intérieur",
            title="Élections municipales des 15 et 22 mars 2026",
            url=DATASETS[0],
            license="Licence Ouverte v2.0",
            license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        )
        for url in [*FILES.values(), SEATS]:
            cs.artifact(raw.cached(SOURCE, url), source)

        nuances: dict[str, UUID] = {}
        for code, label in (
            lists.drop_nulls("nuance").unique("nuance").sort("nuance").select("nuance", "nuance_label").iter_rows()
        ):
            nuances[code] = cs.key(f"{SOURCE}.nuance", f"2026:{code}")
            cs.add("party", id=nuances[code], kind="nuance", polity_id=france)
            name(nuances[code], label or code)
            name(nuances[code], code, "fr", "abbreviation")
        systems = {}
        for key, english, note, size in (
            ("list-2025", "French municipal list system", NOTE, HALF),
            ("cities-2025", "Paris, Lyon, and Marseille council list system", NOTE_CITIES, QUARTER),
        ):
            systems[key] = cs.key(f"{SOURCE}.electoral_system", key)
            rules = {**RULES, "bonus": {**BONUS, "size": size}}
            cs.add("electoral_system", id=systems[key], polity_id=france, rules=rules, rules_version=1)
            name(systems[key], english, "en")
            cs.add("note", entity_id=systems[key], kind="methodology", body_md=note)
        umbrella = cs.key(f"{SOURCE}.election", "2026")
        cs.add(
            "election",
            id=umbrella,
            polity_id=france,
            kind="local",
            date_from=ROUNDS[1],
            date_to=ROUNDS[2],
            status="held",
        )
        name(umbrella, "2026 French municipal elections", "en")
        name(umbrella, "Élections municipales de 2026", "fr", "native")
        cs.add("slug", path="fr/2026-03-15-local", entity_id=umbrella)

        totals = accounting.filter(pl.col("unit").is_in(chosen) | pl.col("unit").str.slice(0, 5).is_in(chosen))
        by_unit = {(row["round"], row["unit"]): row for row in totals.iter_rows(named=True)}
        list_rows: dict[tuple[int, str], list[dict[str, object]]] = {}
        for row in lists.filter(pl.col("unit").str.slice(0, 5).is_in(chosen)).sort("panel").iter_rows(named=True):
            list_rows.setdefault((row["round"], row["unit"]), []).append(row)
        member_rows: dict[tuple[int, str], list[dict[str, object]]] = {}
        for row in members.filter(pl.col("unit").str.slice(0, 5).is_in(chosen)).sort("position").iter_rows(named=True):
            member_rows.setdefault((row["round"], row["unit"]), []).append(row)
        persons: dict[str, UUID] = {}
        namesakes = set(
            members.group_by("round", "unit", "surname", "forename")
            .len()
            .filter(pl.col("len") > 1)
            .select("unit", "surname", "forename")
            .iter_rows()
        )
        values: list[dict[str, object]] = []
        cleared: list[UUID] = []

        def person_for(commune: str, row: dict[str, object]) -> UUID:
            key = f"2026:{commune}:{row['surname']}:{row['forename']}"
            if (row["unit"], row["surname"], row["forename"]) in namesakes:
                key += f":{row['unit']}:{row['round']}:{row['panel']}:{row['position']}"
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key], gender={"F": "female", "M": "male"}.get(str(row["sex"])))
                    name(persons[key], person_name(str(row["forename"]), str(row["surname"])))
            return persons[key]

        def contest_for(
            commune: str,
            unit_code: str,
            scheme: str,
            election: UUID,
            round_: int,
            body: UUID,
            area: UUID,
            size: int,
            system: UUID,
            title: str,
            path: str,
            previous: UUID | None,
        ) -> UUID:
            key = f"2026:{unit_code}:{round_}"
            contest = cs.key(f"{SOURCE}.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area,
                magnitude=size,
                round_number=round_,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, title, "en")
            cs.add("slug", path=path, entity_id=contest)
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
            unit = cs.unit(scheme, unit_code)
            row = by_unit[round_, unit_code]
            if row["Votants"] > row["Inscrits"]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=TURNOUT_CAVEAT.format(voters=row["Votants"], registered=row["Inscrits"]),
                )
            for field, measure in ACCOUNTING.items():
                values.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": None,
                        "measure": measure,
                        "value": row[field],
                    }
                )
            options: dict[int, UUID] = {}
            named: dict[object, int] = {}
            for member in member_rows.get((round_, unit_code), []):
                if member["elected"]:
                    named[member["panel"]] = named.get(member["panel"], 0) + 1
            entries = list_rows.get((round_, unit_code), [])
            total = sum(int(str(entry["seats"] or 0)) for entry in entries)
            over = total > size
            if over and len(entries) != 1:
                cleared.append(result_set)
            if over:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=OVER_CAVEAT.format(
                        lists="this list" if len(entries) == 1 else "these lists",
                        total=total,
                        named=sum(named.values()),
                        size=size,
                    )
                    + (OVER_SINGLE.format(size=size) if len(entries) == 1 else OVER_SEVERAL),
                )
            for entry in entries:
                panel = int(str(entry["panel"]))
                options[panel] = cs.key(f"{SOURCE}.option", f"{key}:{panel}")
                cs.add(
                    "contest_option",
                    id=options[panel],
                    contest_id=contest,
                    kind="list",
                    party_id=nuances.get(str(entry["nuance"])),
                    ballot_position=entry["printed"],
                    ballot_label=entry["list_label"],
                )
                values.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": str(options[panel]),
                        "measure": 1,
                        "value": entry["votes"],
                    }
                )
                if over:
                    if len(entries) == 1:
                        cs.add("outcome", result_set_id=result_set, option_id=options[panel], kind="won", seats=size)
                    continue
                if entry["seats"]:
                    cs.add(
                        "outcome", result_set_id=result_set, option_id=options[panel], kind="won", seats=entry["seats"]
                    )
                if named.get(panel, 0) != (entry["seats"] or 0):
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=SEATS_CAVEAT.format(seats=entry["seats"], named=named.get(panel, 0)),
                    )
            for member in member_rows.get((round_, unit_code), []):
                position = int(str(member["position"]))
                candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{member['panel']}:{position}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person_for(commune, member),
                    option_id=options[int(str(member["panel"]))],
                    role="head" if position == 1 else "list_member",
                    list_position=position,
                    ballot_name=f"{member['surname']} {member['forename']}",
                )
                if member["elected"] and (not over or len(entries) == 1 and position <= size):
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[int(str(member["panel"]))],
                        candidacy_id=candidacy,
                        kind="elected",
                    )
            return contest

        if CITIES & set(chosen):
            cs.add("scheme", code="fr.secteur", polity_id=france, kind="electoral_district", rank=3, noun="sector")
            cs.add(
                "boundary_set",
                scheme_code="fr.secteur",
                vintage="2026",
                valid_from=ROUNDS[1],
                valid_to=None,
                as_of=ROUNDS[1],
            )
        for code in chosen:
            label = by_unit[1, code]["label"]
            department = by_unit[1, code]["department"]
            path = f"fr/{paths[code]}"
            area = cs.area("fr.commune", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="fr.commune")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=cs.area("fr.departement", department),
                    valid_from=None,
                    valid_to=None,
                )
                name(area, label)
            unit = cs.unit("fr.commune", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=france,
                    scheme_code="fr.commune",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                    valid_to=None,
                )
            polity = cs.key(f"{SOURCE}.polity", code)
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, label)
            cs.add("polity_parent", polity_id=polity, parent_id=france)
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add("slug", path=path, entity_id=polity)
            body = cs.key(f"{SOURCE}.body", code)
            cs.add("body", id=body, polity_id=polity, kind="unicameral")
            if code == "75056":
                name(body, "Council of Paris", "en")
                name(body, "Conseil de Paris", "fr", "native")
            else:
                name(body, f"Municipal council of {label}", "en")
                name(body, f"Conseil municipal de {label}", "fr", "native")
            size = magnitude[code]
            cs.add("body_size", body_id=body, seats=size, valid_from=ROUNDS[1], valid_to=None)
            system = systems["cities-2025" if code in CITIES else "list-2025"]
            elections: dict[int, UUID] = {}
            for round_, day in ROUNDS.items():
                if (round_, code) not in by_unit:
                    continue
                elections[round_] = cs.key(f"{SOURCE}.election", f"2026:{code}:{round_}")
                cs.add(
                    "election",
                    id=elections[round_],
                    polity_id=polity,
                    parent_id=umbrella if round_ == 1 else elections[1],
                    kind="local" if round_ == 1 else "runoff",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    elections[round_],
                    f"2026 {label} municipal election" + (", second round" if round_ == 2 else ""),
                    "en",
                )
                cs.add("slug", path=f"{path}/{day}-{'local' if round_ == 1 else 'runoff'}", entity_id=elections[round_])
            previous = None
            for round_, election in elections.items():
                previous = contest_for(
                    code,
                    code,
                    "fr.commune",
                    election,
                    round_,
                    body,
                    area,
                    size,
                    system,
                    f"2026 {label} municipal election, {'first' if round_ == 1 else 'second'} round",
                    f"{path}/{ROUNDS[round_]}-{'local' if round_ == 1 else 'runoff'}/conseil-municipal",
                    previous,
                )
            if code not in CITIES:
                continue
            sectors = sorted({unit for (_, unit) in by_unit if unit.startswith(f"{code}SR")})
            for sector in sectors:
                sector_label = by_unit[1, sector]["label"]
                sector_area = cs.area("fr.secteur", sector)
                cs.add("area", id=sector_area, scheme_code="fr.secteur")
                cs.add("area_code", area_id=sector_area, code=sector, valid_from=ROUNDS[1], valid_to=None)
                cs.add("area_parent", area_id=sector_area, parent_id=area, valid_from=ROUNDS[1], valid_to=None)
                name(sector_area, sector_label)
                sector_unit = cs.unit("fr.secteur", sector)
                cs.add(
                    "reporting_unit",
                    id=sector_unit,
                    polity_id=france,
                    scheme_code="fr.secteur",
                    code=sector,
                    area_relation="equals",
                    area_id=sector_area,
                    kind="standard",
                    valid_from=ROUNDS[1],
                    valid_to=None,
                )
                sector_body = cs.key(f"{SOURCE}.body", sector)
                cs.add("body", id=sector_body, polity_id=polity, kind="unicameral")
                name(sector_body, f"{sector_label} council", "en")
                name(sector_body, f"Conseil d'arrondissement, {sector_label}", "fr", "native")
                sector_size = next(
                    int(str(row["legal"]))
                    for round_ in ROUNDS
                    for row in member_rows.get((round_, sector), [])
                    if row["legal"] is not None
                )
                cs.add("body_size", body_id=sector_body, seats=sector_size, valid_from=ROUNDS[1], valid_to=None)
                previous = None
                for round_, election in elections.items():
                    if (round_, sector) not in by_unit:
                        continue
                    previous = contest_for(
                        code,
                        sector,
                        "fr.secteur",
                        election,
                        round_,
                        sector_body,
                        sector_area,
                        sector_size,
                        systems["list-2025"],
                        f"2026 {label} municipal election, {sector_label} council, {'first' if round_ == 1 else 'second'} round",
                        f"{path}/{ROUNDS[round_]}-{'local' if round_ == 1 else 'runoff'}/secteur-{sector[-2:]}",
                        previous,
                    )
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema_overrides={"value": pl.Int64}, infer_schema_length=None)
            .drop_nulls("value")
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        scope_manifest = cs.manifest.get("scope")
        cs.manifest["scope"] = {
            **(scope_manifest if isinstance(scope_manifest, dict) else {}),
            "clears": [str(id) for id in cleared],
        }
        return cs.write()
