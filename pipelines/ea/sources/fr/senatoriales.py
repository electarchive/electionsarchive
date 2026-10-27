from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr.interieur import ACCOUNTING
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.senatoriales"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 9, 27)
STATIC = "https://static.data.gouv.fr/resources"
RESULTS = f"{STATIC}/elections-senatoriales-2026-resultats/20260927-235109"
CANDIDATES = f"{STATIC}/elections-senatoriales-2026-candidatures-aux-scrutins-majoritaire-tour-1-et-proportionnel"
FILES = {
    "r1": f"{RESULTS}/senatoriales-2026-resultats-t1-scrutin-majoritaire-et-scrutin-proportionnel.csv",
    "r2": f"{RESULTS}/senatoriales-2026-resultats-t2-scrutin-majoritaire.csv",
    "elected": f"{RESULTS}/senatoriales-2026-candidats-elus.csv",
    "lists": f"{CANDIDATES}/20260918-152435/senatoriales-2026-candidatures-listes-scrutin-proportionnel.csv",
    "candidates": f"{CANDIDATES}/20260918-152457/senatoriales-2026-candidatures-individuelles-scrutin-majoritaire.csv",
}
DATASET = "https://www.data.gouv.fr/datasets/elections-senatoriales-2026-resultats"
NEW_AREAS = {"977": "Saint-Barthélemy", "978": "Saint-Martin"}
NOTE = (
    "Senators are elected for six years by an electoral college in each department, made up of the department's "
    "deputies, senators, regional and departmental councilors, and delegates of the municipal councils. Half of the "
    "348 seats are renewed every three years. On 27 September 2026 the second series elected 178 senators."
)
MAJORITY_NOTE = (
    "Departments that elect one or two senators vote by a two-round majority system, and electors may vote for as "
    "many candidates as there are seats. A candidate is elected in the first round with an absolute majority of the "
    "valid votes and votes from at least a quarter of the electors; seats still unfilled go to a second round held "
    "the same day, where the most votes win. Each candidate stands with a substitute."
)
LIST_NOTE = (
    "Departments that elect three or more senators, and the senators for French citizens abroad, vote by closed-list "
    "proportional representation in a single round. Seats are shared by the highest-average (D'Hondt) method, with "
    "no threshold, and go to candidates in list order."
)
SEX = {"MASCULIN": "male", "FEMININ": "female", "M": "male", "F": "female"}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".csv")


def read(kind: str) -> pl.DataFrame:
    return pl.read_csv(raw.cached(SOURCE, FILES[kind]).path, separator=";", infer_schema_length=0, null_values=[""])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    accounting: list[pl.DataFrame] = []
    options: list[pl.DataFrame] = []
    for round_ in (1, 2):
        frame = read(f"r{round_}").rename({"Code département": "unit", "Libellé département": "label"})
        accounting.append(
            frame.select(
                pl.lit(round_, pl.Int64).alias("round"),
                "unit",
                "label",
                *(pl.col(field).cast(pl.Int64) for field in ACCOUNTING),
            )
        )
        count = max(int(column.rsplit(" ", 1)[1]) for column in frame.columns if column.startswith("Voix "))
        options.append(
            pl.concat(
                frame.select(
                    pl.lit(round_, pl.Int64).alias("round"),
                    "unit",
                    index=pl.lit(i, pl.Int64),
                    nuance=pl.coalesce(
                        pl.col(f"Nuance liste {i}") if round_ == 1 else pl.lit(None, pl.String),
                        pl.col(f"Nuance candidat {i}"),
                    ),
                    surname=pl.col(f"Nom candidat {i}"),
                    forename=pl.col(f"Prénom candidat {i}"),
                    sex=pl.col(f"Sexe candidat {i}"),
                    list_label=pl.col(f"Libellé de liste {i}") if round_ == 1 else pl.lit(None, pl.String),
                    votes=pl.col(f"Voix {i}").cast(pl.Int64),
                    elected=pl.col(f"Elu {i}") == "élu",
                    seats=pl.col(f"Sièges {i}").cast(pl.Int64) if round_ == 1 else pl.lit(None, pl.Int64),
                )
                for i in range(1, count + 1)
            ).drop_nulls("votes")
        )
    options_frame = pl.concat(options, how="diagonal_relaxed")
    lists = read("lists").select(
        unit=pl.col("Code circonscription"),
        deposit=pl.col("N° dépôt").cast(pl.Int64),
        list_label=pl.col("Libellé de la liste"),
        nuance=pl.col("Code nuance de liste"),
        nuance_label=pl.col("Nuance de liste"),
        position=pl.col("Ordre").cast(pl.Int64),
        sex=pl.col("Sexe"),
        surname=pl.col("Nom sur le bulletin de vote"),
        forename=pl.col("Prénom sur le bulletin de vote"),
        born=pl.col("Date de naissance").str.to_date(),
    )
    candidates = read("candidates").select(
        unit=pl.col("Code circonscription"),
        deposit=pl.col("N° dépôt").cast(pl.Int64),
        sex=pl.col("Sexe du candidat"),
        surname=pl.col("Nom du candidat"),
        forename=pl.col("Prénom du candidat"),
        born=pl.col("Date de naissance du candidat").str.to_date(),
        nuance=pl.col("Code nuance"),
        nuance_label=pl.col("Nuance du candidat"),
        substitute_sex=pl.col("Sexe remplaçant"),
        substitute_surname=pl.col("Nom remplaçant"),
        substitute_forename=pl.col("Prénom remplaçant"),
        substitute_born=pl.col("Date de naissance remplaçant").str.to_date(),
    )
    deposits = lists.unique(["unit", "deposit"]).select("unit", "deposit", "list_label")
    options_frame = (
        options_frame.join(deposits, on=["unit", "list_label"], how="left")
        .join(
            candidates.select("unit", "surname", "forename", candidate_deposit="deposit"),
            on=["unit", "surname", "forename"],
            how="left",
        )
        .with_columns(deposit=pl.coalesce("deposit", "candidate_deposit"))
        .drop("candidate_deposit")
    )
    print("lists without a deposit:", options_frame.filter(pl.col("list_label").is_not_null() & pl.col("deposit").is_null()).rows())
    print("candidates without a deposit:", options_frame.filter(pl.col("list_label").is_null() & pl.col("deposit").is_null()).rows())
    elected = read("elected").select(
        unit=pl.when(pl.col("CODDPT").str.contains(r"^\d$")).then(pl.col("CODDPT").str.zfill(2)).otherwise("CODDPT"),
        surname=pl.col("NOMPSN"),
        forename=pl.col("PREPSN"),
        round=pl.col("TOUR_ELECTION").str.slice(-1).cast(pl.Int64),
    )
    seats = options_frame.filter(pl.col("seats") > 0).select("unit", "deposit", "seats")
    winners = lists.join(seats, on=["unit", "deposit"]).filter(pl.col("position") <= pl.col("seats"))
    majority = options_frame.filter(pl.col("elected")).select("unit", "surname", "forename", "round")
    named = pl.concat([winners.select("unit", "surname", "forename", round=pl.lit(1, pl.Int64)), majority])
    print("elected not derived:", elected.join(named, on=["unit", "surname", "forename", "round"], how="anti").rows())
    print("derived not elected:", named.join(elected, on=["unit", "surname", "forename", "round"], how="anti").rows())
    pl.concat(accounting).write_parquet(PARSED / "accounting.parquet")
    options_frame.write_parquet(PARSED / "options.parquet")
    lists.write_parquet(PARSED / "lists.parquet")
    candidates.write_parquet(PARSED / "candidates.parquet")
    elected.write_parquet(PARSED / "elected.parquet")


def emit() -> Path:
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    options = pl.read_parquet(PARSED / "options.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    elected = pl.read_parquet(PARSED / "elected.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 French Senate election (second series), by department",
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        france = cs.polity("FR")
        source = cs.source("Ministère de l'Intérieur", "Élections sénatoriales du 27 septembre 2026")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Ministère de l'Intérieur",
            title="Élections sénatoriales du 27 septembre 2026",
            url=DATASET,
            license="Licence Ouverte v2.0",
            license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)

        labels = dict(
            pl.concat(
                [
                    lists.select("nuance", "nuance_label"),
                    candidates.select("nuance", "nuance_label"),
                ]
            )
            .drop_nulls()
            .unique("nuance", keep="first")
            .iter_rows()
        )
        nuances: dict[str, UUID] = {}
        for code in sorted(set(options["nuance"].drop_nulls())):
            nuances[code] = cs.key(f"{SOURCE}.nuance", f"2026:{code}")
            cs.add("party", id=nuances[code], kind="nuance", polity_id=france)
            name(nuances[code], labels.get(code, code))
            name(nuances[code], code, "fr", "abbreviation")

        senate = cs.key("fr.body", "senate")
        cs.add("body", id=senate, polity_id=france, kind="upper_house")
        name(senate, "Senate", "en")
        name(senate, "Sénat", "fr", "native")
        cs.add("body_size", body_id=senate, seats=348, valid_from=date(2011, 10, 1))
        systems = {}
        for key, english, rules, note in (
            (
                "senate-majority",
                "French Senate two-round majority system",
                {
                    "formula": "repeated_ballot",
                    "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}, {"fromRound": 2, "share": 0}],
                },
                MAJORITY_NOTE,
            ),
            (
                "senate-list",
                "French Senate list system",
                {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
                LIST_NOTE,
            ),
        ):
            systems[key] = cs.key("fr.electoral_system", key)
            cs.add("electoral_system", id=systems[key], polity_id=france, rules=rules, rules_version=1)
            name(systems[key], english, "en")
            cs.add("note", entity_id=systems[key], kind="methodology", body_md=note)
        election = cs.key("fr.election", f"{DAY}:senate")
        cs.add("election", id=election, polity_id=france, kind="indirect", date_from=DAY, date_to=DAY, status="held")
        name(election, "2026 French Senate election", "en")
        name(election, "Élections sénatoriales de 2026", "fr", "native")
        cs.add("slug", path=f"fr/{DAY}-senate", entity_id=election)
        cs.add("note", entity_id=election, kind="context", body_md=NOTE)
        runoff = cs.key("fr.election", f"{DAY}:senate:2")
        cs.add(
            "election",
            id=runoff,
            polity_id=france,
            parent_id=election,
            kind="runoff",
            date_from=DAY,
            date_to=DAY,
            status="held",
        )
        name(runoff, "2026 French Senate election, second round", "en")
        cs.add("slug", path=f"fr/{DAY}-senate-runoff", entity_id=runoff)

        nation = cs.area("fr.nation", "FR")
        for code, label in NEW_AREAS.items():
            area = cs.area("fr.departement", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="fr.departement")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None, valid_to=None)
                name(area, label)
            unit = cs.unit("fr.departement", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=france,
                    scheme_code="fr.departement",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                    valid_to=None,
                )

        persons: dict[str, UUID] = {}

        def person_for(unit: str, surname: str, forename: str, sex: str | None, born: date | None) -> UUID:
            key = f"2026:{unit}:{surname}:{forename}:{born}"
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add(
                        "person",
                        id=persons[key],
                        gender=SEX.get(sex or ""),
                        birth_date=born,
                        birth_precision="day" if born else None,
                    )
                    name(persons[key], person_name(forename, surname))
            return persons[key]

        born = {
            (unit, surname, forename): day
            for unit, surname, forename, day in candidates.select("unit", "surname", "forename", "born").iter_rows()
        }
        substitutes = {
            (row["unit"], row["surname"], row["forename"]): row
            for row in candidates.filter(pl.col("substitute_surname").is_not_null()).iter_rows(named=True)
        }
        members: dict[tuple[str, int], list[dict[str, object]]] = {}
        for row in lists.sort("position").iter_rows(named=True):
            members.setdefault((row["unit"], row["deposit"]), []).append(row)
        winners = {(unit, surname, forename) for unit, surname, forename in elected.select("unit", "surname", "forename").iter_rows()}
        seats = dict(elected.group_by("unit").len().iter_rows())
        values: list[dict[str, object]] = []
        first_rounds: dict[str, UUID] = {}
        for row in accounting.sort("round", "unit").iter_rows(named=True):
            unit_code, round_, label = row["unit"], row["round"], row["label"]
            rows = options.filter((pl.col("unit") == unit_code) & (pl.col("round") == round_)).sort("index")
            proportional = rows["list_label"].is_not_null().any()
            key = f"{DAY}:{unit_code}:{round_}"
            contest = cs.key(f"{SOURCE}.contest", key)
            area = None if unit_code == "ZZ" else cs.area("fr.departement", unit_code)
            title = "French citizens abroad" if unit_code == "ZZ" else label
            cs.add(
                "contest",
                id=contest,
                election_id=runoff if round_ == 2 else election,
                kind="list_allocation" if proportional else "body_seats",
                body_id=senate,
                district_area_id=area,
                magnitude=seats[unit_code],
                round_number=round_,
                previous_round_id=first_rounds.get(unit_code) if round_ == 2 else None,
                electoral_system_id=systems["senate-list" if proportional else "senate-majority"],
                default_result_kind="counted",
            )
            if round_ == 1:
                first_rounds[unit_code] = contest
            two_rounds = not proportional and (2, unit_code) in set(accounting.select("round", "unit").iter_rows())
            suffix = {1: ", first round", 2: ", second round"}[round_] if two_rounds else ""
            name(contest, f"{title}{suffix}", "en")
            cs.add(
                "slug",
                path=f"fr/{DAY}-senate/{slug(title)}" + ("-second-round" if round_ == 2 else ""),
                entity_id=contest,
            )
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=runoff if round_ == 2 else election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            unit = cs.unit("fr.departement", unit_code)
            for field, measure in ACCOUNTING.items():
                values.append(
                    {"result_set_id": str(result_set), "unit_id": str(unit), "option_id": None, "measure": measure, "value": row[field]}
                )
            for entry in rows.iter_rows(named=True):
                position = entry["deposit"] or entry["index"]
                option = cs.key(f"{SOURCE}.option", f"{key}:{position}")
                values.append(
                    {"result_set_id": str(result_set), "unit_id": str(unit), "option_id": str(option), "measure": 1, "value": entry["votes"]}
                )
                if proportional:
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=nuances.get(entry["nuance"]),
                        ballot_position=entry["deposit"],
                        ballot_label=entry["list_label"],
                    )
                    if entry["seats"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry["seats"])
                    for member in members.get((unit_code, entry["deposit"]), []):
                        candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{entry['deposit']}:{member['position']}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person_for(unit_code, member["surname"], member["forename"], member["sex"], member["born"]),
                            option_id=option,
                            role="head" if member["position"] == 1 else "list_member",
                            list_position=member["position"],
                            ballot_name=f"{member['surname']} {member['forename']}",
                        )
                        if member["position"] <= (entry["seats"] or 0):
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=option,
                                candidacy_id=candidacy,
                                kind="elected",
                            )
                    continue
                identity = (unit_code, entry["surname"], entry["forename"])
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=nuances.get(entry["nuance"]),
                    ballot_position=entry["deposit"],
                    ballot_label=f"{entry['surname']} {entry['forename']}",
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{key}:{position}"),
                    person_id=person_for(unit_code, entry["surname"], entry["forename"], entry["sex"], born.get(identity)),
                    option_id=option,
                    role="head",
                    ballot_name=f"{entry['surname']} {entry['forename']}",
                )
                if (substitute := substitutes.get(identity)) is not None:
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{key}:{position}:substitute"),
                        person_id=person_for(
                            unit_code,
                            substitute["substitute_surname"],
                            substitute["substitute_forename"],
                            substitute["substitute_sex"],
                            substitute["substitute_born"],
                        ),
                        option_id=option,
                        role="substitute",
                        ballot_name=f"{substitute['substitute_surname']} {substitute['substitute_forename']}",
                    )
                if entry["elected"]:
                    if identity not in winners:
                        raise ValueError(f"{identity} is marked elected but missing from the elected file")
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
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
        return cs.write()
