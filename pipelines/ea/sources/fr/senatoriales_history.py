import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.senatoriales_history"
PIPELINE = "fr.senatoriales"
VERSION = "h1"
PARSED = DATA / "parsed" / SOURCE
STATIC = "https://static.data.gouv.fr/resources"
DATASETS = "https://www.data.gouv.fr/datasets"
SENAT = "https://data.senat.fr/data/senateurs"
DAYS = {
    2011: date(2011, 9, 25),
    2014: date(2014, 9, 28),
    2017: date(2017, 9, 24),
    2020: date(2020, 9, 27),
    2023: date(2023, 9, 24),
}
YEARS: dict[int, dict[str, str]] = {
    2011: {
        "dataset": f"{DATASETS}/elections-senatoriales-2011-resultats-572109",
        "results": "https://static.data.gouv.fr/6a/11effa497299567a95fc6b4da265ae95dbfbba45b048562eb7b727674f2957.xls",
        "suffix": ".xls",
    },
    2014: {
        "dataset": f"{DATASETS}/elections-senatoriales-2014-resultats",
        "results": f"{STATIC}/Senatoriales_2014.xlsx",
        "suffix": ".xlsx",
    },
    2017: {
        "dataset": f"{DATASETS}/elections-senatoriales-2017-resultats",
        "results": f"{STATIC}/elections-senatoriales-2017-resultats/20170925-092543/Senatoriales_2017_resultats.xlsx",
        "candidates": f"{STATIC}/elections-senatoriales-du-24-septembre-2017-liste-des-candidats/20170918-115341/Senatoriales_2017_candidatures_18_09_2017.xlsx",
        "suffix": ".xlsx",
    },
    2020: {
        "dataset": f"{DATASETS}/senatoriales-2020-resultats",
        "results": f"{STATIC}/senatoriales-2020-resultats/20200929-104851/2020-09-28-resultats-avec-elus.xlsx",
        "candidates": f"{STATIC}/senatoriales-2020-candidatures-au-t1/20200918-184808/livre-des-listes-et-candidats-2020-t1-france-entiere-data.xlsx",
        "suffix": ".xlsx",
    },
    2023: {
        "dataset": f"{DATASETS}/elections-senatoriales-2023-resultats",
        "results": f"{STATIC}/elections-senatoriales-2023-resultats/20230928-104908/senatoriales-2023-resultats-publication.xlsx",
        "candidates": f"{STATIC}/elections-senatoriales-2023-candidatures-t1/20230918-172919/senatoriales-2023-candidatures-18-09-23-publication.xlsx",
        "suffix": ".xlsx",
    },
}
SENATE_BASE = {"mandates": f"{SENAT}/ODSEN_ELUSEN.csv", "general": f"{SENAT}/ODSEN_GENERAL.csv"}
SENATE_DATASET = "https://www.data.gouv.fr/datasets/les-senateurs"
NOTE = (
    "Senators are elected for six years by an electoral college in each department, made up of the department's "
    "deputies, senators, regional and departmental councilors, and delegates of the municipal councils. Half of the "
    "348 seats are renewed every three years. On {day} {seats} senators were elected."
)
ACCOUNTING = {"registered": 2, "voters": 14, "valid": 4, "blank": 6, "null": 5, "blank_null": 5}
FIELDS = {
    "registered": ("inscrits",),
    "voters": ("votants",),
    "valid": ("exprimés",),
    "blank": ("blancs",),
    "null": ("nuls",),
    "blank_null": ("blancs et nuls",),
}
BLOCK = {
    "deposit": ("n°dépôt", "n° dépôt", "code dépôt"),
    "nuance_list": ("nuance liste", "nuance liste"),
    "nuance": ("nuance", "nuance candidat"),
    "sex": ("sexe", "sexe candidat"),
    "surname": ("nom", "nom candidat"),
    "forename": ("prénom", "prénom candidat"),
    "list_label": ("libellé liste", "libellé de la liste", "libellé abrégé liste", "libellé de liste"),
    "head": ("nom tête de liste",),
    "votes": ("voix",),
    "result": ("résultat candidat", "elu"),
    "seats": ("sièges",),
}
STARTS = {
    "n°dépôt",
    "n° dépôt",
    "code dépôt",
    "sexe",
    "nuance liste",
    "nuance candidat",
}
SEX = {"M": "male", "F": "female", "MASCULIN": "male", "FEMININ": "female"}
SHEETS = {
    2011: {"maj1": "Maj T1", "maj2": "Maj T2", "prop": "Prop", "elected": "Elus"},
    2014: {"maj1": "Maj T1", "maj2": "Maj T2", "prop": "Prop", "elected": "Elus"},
    2017: {"maj1": "MAJ T1", "maj2": "MAJ T2", "prop": "PROP", "elected": "Elus"},
    2020: {"maj1": "MAJ 1", "maj2": "MAJ 2", "prop": "PROP"},
    2023: {"maj1": "MAJ - T1", "maj2": "MAJ - T2", "prop": "PROP", "elected": "Liste des élus"},
}
CANDIDATE_SHEETS = {
    2017: {"prop": "Srutin proportionnel", "maj": "Scrutin majoritaire"},
    2020: {"prop": "Scrutin proportionnel", "maj": "Scrutin majoritaire"},
    2023: {"prop": "Scrutin proportionnel", "maj": "Scrutin majoritaire"},
}
UNIT_ALIASES = {
    "ZA": "971",
    "ZB": "972",
    "ZC": "973",
    "ZD": "974",
    "ZS": "975",
    "ZM": "976",
    "ZY": "977",
    "ZT": "978",
    "ZW": "986",
    "ZP": "987",
    "ZN": "988",
}


def norm(text: object) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", stripped.casefold())


def text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def number(value: object) -> int | None:
    return None if text(value) is None else int(float(str(value).replace(",", ".")))


def day(value: object) -> date | None:
    cleaned = text(value)
    if cleaned is None:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", cleaned):
        return datetime.fromisoformat(cleaned[:10]).date()
    return datetime.strptime(cleaned[:10], "%d/%m/%Y").date()


def unit_code(value: object) -> str:
    code = str(text(value)).upper()
    if code.isdigit() and len(code) < 3:
        code = code.zfill(2)
    return UNIT_ALIASES.get(code, code)


def split_name(designation: str) -> tuple[str | None, str, str]:
    parts = designation.split()
    sex = None
    if parts[0] in ("M.", "Mme"):
        sex = "male" if parts.pop(0) == "M." else "female"
    surname: list[str] = []
    while parts and (parts[0].isupper() or (surname and parts[0].casefold() in ("de", "du", "d'", "la", "le"))):
        surname.append(parts.pop(0))
    if not surname and parts:
        surname.append(parts.pop(0))
    return sex, " ".join(surname), " ".join(parts)


def fetch() -> None:
    for config in YEARS.values():
        for key in ("results", "candidates"):
            if key in config:
                raw.fetch(SOURCE, config[key], suffix=config["suffix"])
    for url in SENATE_BASE.values():
        raw.fetch(SOURCE, url, suffix=".csv")


def workbook(year: int, key: str) -> dict[str, list[list[object]]]:
    path = raw.cached(SOURCE, YEARS[year][key]).path
    frames = pl.read_excel(path, sheet_id=0, has_header=False, raise_if_empty=False, infer_schema_length=0)
    return {name: [list(row) for row in frame.rows()] for name, frame in frames.items()}


def read_sheet(rows: list[list[object]], round_: int, proportional: bool) -> tuple[list[dict], list[dict]]:
    start = next(i for i, row in enumerate(rows) if any(str(c).strip() == "Inscrits" for c in row if c is not None))
    header = [str(c).strip() if c is not None else "" for c in rows[start]]
    lowered = [re.sub(r" \d+$", "", h.casefold()) for h in header]
    first = next(i for i, h in enumerate(lowered) if h in STARTS and i > lowered.index("inscrits"))
    width = next((j - first for j in range(first + 1, len(lowered)) if lowered[j] == lowered[first]), None)
    width = width or max(i for i, h in enumerate(lowered) if h) + 1 - first
    block = lowered[first : first + width]
    fixed = lowered[:first]

    def find(names: tuple[str, ...], columns: list[str], offset: int = 0) -> int | None:
        for name in names:
            if name in columns:
                return offset + columns.index(name)
        return None

    unit_col = find(("code département", "code du département", "code du niveau"), fixed)
    label_col = find(("libellé département", "libellé du département", "libellé du niveau"), fixed)
    assert unit_col is not None and label_col is not None
    accounting_cols = {field: find(names, fixed) for field, names in FIELDS.items()}
    accounting: list[dict] = []
    options: list[dict] = []
    for row in rows[start + 1 :]:
        if row[unit_col] is None or text(row[unit_col]) is None:
            continue
        unit = unit_code(row[unit_col])
        accounting.append(
            {
                "round": round_,
                "unit": unit,
                "label": text(row[label_col]),
                **{field: (number(row[col]) if col is not None else None) for field, col in accounting_cols.items()},
            }
        )
        index = 0
        for begin in range(first, len(row), width):
            cell: dict[str, object] = {}
            for field, names in BLOCK.items():
                at = find(names, block, begin)
                cell[field] = row[at] if at is not None else None
            votes = number(cell["votes"])
            if votes is None:
                continue
            index += 1
            options.append(
                {
                    "round": round_,
                    "unit": unit,
                    "index": index,
                    "deposit": number(cell["deposit"]),
                    "nuance": text(cell["nuance_list"]) or text(cell["nuance"]),
                    "sex": text(cell["sex"]),
                    "surname": text(cell["surname"]),
                    "forename": text(cell["forename"]),
                    "list_label": text(cell["list_label"]) if proportional else None,
                    "head": text(cell["head"]),
                    "votes": votes,
                    "result": text(cell["result"]),
                    "seats": number(cell["seats"]),
                }
            )
    return accounting, options


def read_elected(year: int, sheets: dict[str, list[list[object]]]) -> pl.DataFrame:
    rows = sheets[SHEETS[year]["elected"]]
    header = [str(c).strip().casefold() if c is not None else "" for c in rows[0]]
    records = []
    for row in rows[1:]:
        cells = dict(zip(header, row))
        if text(cells.get("code dpt") or cells.get("dpt")) is None:
            continue
        unit = unit_code(cells.get("code dpt") or cells.get("dpt"))
        if "désignation" in cells:
            sex, surname, forename = split_name(str(cells["désignation"]))
        else:
            sex, surname, forename = SEX.get(str(cells["sexe"])), str(cells["nom"]), str(cells["prenom"])
        tour = str(cells.get("tour") or cells.get("tour_election"))
        records.append({"unit": unit, "surname": surname, "forename": forename, "round": int(tour[-1]), "sex": sex})
    return pl.DataFrame(records, schema_overrides={"round": pl.Int64})


def read_candidates(year: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    sheets = workbook(year, "candidates")
    members: list[dict] = []
    singles: list[dict] = []
    for kind, name in CANDIDATE_SHEETS[year].items():
        rows = sheets[name]
        top = next(i for i, row in enumerate(rows) if sum(c is not None for c in row) > 3)
        header = [str(c).strip().casefold() if c is not None else "" for c in rows[top]]
        for row in rows[top + 1 :]:
            cells = dict(zip(header, row))
            unit_value = cells.get("code du département") or cells.get("code département")
            if text(unit_value) is None:
                continue

            def pick(*names: str) -> object:
                for column in names:
                    if cells.get(column) is not None:
                        return cells[column]
                return None

            record = {
                "unit": unit_code(unit_value),
                "deposit": number(pick("n° dépôt liste", "code dépôt liste", "n° dépôt", "n° de dépôt du candidat")),
                "list_label": text(pick("libellé de la liste")),
                "nuance": text(pick("nuance de liste", "code nuance de liste", "nuance candidat", "code nuance")),
                "position": number(pick("n° d'ordre dans la liste", "ordre dans la liste")),
                "sex": text(pick("sexe candidat", "sexe du candidat")),
                "surname": text(pick("nom candidat", "nom du candidat")),
                "forename": text(pick("prénom candidat", "prénom du candidat")),
                "born": day(
                    pick("date naissance candidat", "date de naissance candidat", "date de naissance du candidat")
                ),
                "substitute_sex": text(pick("sexe supp.", "sexe suppléant")),
                "substitute_surname": text(pick("nom supp.", "nom suppléant")),
                "substitute_forename": text(pick("prénom supp.", "prénom suppléant")),
                "substitute_born": day(pick("date naiss. supp.", "date de naissance suppléant")),
            }
            (members if kind == "prop" else singles).append(record)
    schema = {"deposit": pl.Int64, "position": pl.Int64, "born": pl.Date, "substitute_born": pl.Date}
    return (
        pl.DataFrame(members, schema_overrides=schema, infer_schema_length=None),
        pl.DataFrame(singles, schema_overrides=schema, infer_schema_length=None),
    )


def senate_elected(year: int, members: pl.DataFrame, singles: pl.DataFrame) -> pl.DataFrame:
    when = DAYS[year]
    mandates = pl.read_csv(
        raw.cached(SOURCE, SENATE_BASE["mandates"]).path,
        comment_prefix="%",
        encoding="latin1",
        infer_schema_length=0,
    )
    general = pl.read_csv(
        raw.cached(SOURCE, SENATE_BASE["general"]).path, comment_prefix="%", encoding="latin1", infer_schema_length=0
    )
    chosen = mandates.filter(pl.col("Date d'élection").str.slice(0, 10) == str(when)).join(
        general.select("Matricule", born="Date naissance"), on="Matricule", how="left"
    )
    chosen = chosen.with_columns(born=pl.col("born").str.slice(0, 10).str.to_date(strict=False))
    people = pl.concat(
        [
            members.select("unit", "surname", "forename", "born", round=pl.lit(1, pl.Int64)),
            singles.select("unit", "surname", "forename", "born", round=pl.lit(0, pl.Int64)),
        ]
    ).filter(pl.col("born").is_not_null())
    key = lambda s, f, b: (norm(s), norm(f), b)
    by_identity: dict[tuple[str, str, date], dict] = {}
    for row in people.iter_rows(named=True):
        by_identity[key(row["surname"], row["forename"], row["born"])] = row
    found, missing = [], []
    for row in chosen.iter_rows(named=True):
        match = by_identity.get(key(row["Nom usuel"], row["Prénom usuel"], row["born"]))
        if match is None:
            usual = key(row["Nom usuel"], row["Prénom usuel"], row["born"])
            loose = [v for k, v in by_identity.items() if k[1:] == usual[1:] and usual[0].startswith(k[0])]
            match = loose[0] if len(loose) == 1 else None
        if match is None:
            missing.append((row["Nom usuel"], row["Prénom usuel"], row["born"]))
        else:
            found.append({"unit": match["unit"], "surname": match["surname"], "forename": match["forename"]})
    print(year, "senate mandates", chosen.height, "matched", len(found), "unmatched", missing)
    return pl.DataFrame(found, schema={"unit": pl.String, "surname": pl.String, "forename": pl.String})


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        sheets = workbook(year, "results")
        accounting: list[dict] = []
        options: list[dict] = []
        for round_, key, proportional in ((1, "maj1", False), (2, "maj2", False), (1, "prop", True)):
            acc, opt = read_sheet(sheets[SHEETS[year][key]], round_, proportional)
            accounting += [{**a, "proportional": proportional} for a in acc]
            options += [{**o, "proportional": proportional} for o in opt]
        members = singles = pl.DataFrame()
        if year in CANDIDATE_SHEETS:
            members, singles = read_candidates(year)
        if "elected" in SHEETS[year]:
            elected = read_elected(year, sheets)
        else:
            chosen = senate_elected(year, members, singles)
            elected = chosen.with_columns(round=pl.lit(None, pl.Int64), sex=pl.lit(None, pl.String))
        folder = PARSED / str(year)
        folder.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(accounting, infer_schema_length=None).unique(maintain_order=True).write_parquet(
            folder / "accounting.parquet"
        )
        pl.DataFrame(options, infer_schema_length=None).unique(maintain_order=True).write_parquet(
            folder / "options.parquet"
        )
        members.write_parquet(folder / "members.parquet")
        singles.write_parquet(folder / "singles.parquet")
        elected.write_parquet(folder / "elected.parquet")
        print(year, "accounting", len(accounting), "options", len(options), "elected", elected.height)


def emit() -> Path:
    year = int(os.environ["SEN_YEAR"])
    config = YEARS[year]
    when = DAYS[year]
    folder = PARSED / str(year)
    accounting = pl.read_parquet(folder / "accounting.parquet")
    options = pl.read_parquet(folder / "options.parquet")
    members = pl.read_parquet(folder / "members.parquet")
    singles = pl.read_parquet(folder / "singles.parquet")
    elected = pl.read_parquet(folder / "elected.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=PIPELINE,
            version=VERSION,
            summary=f"{year} French Senate election, by department",
            polity="FR",
        )

        def name(entity: UUID, label: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=label, lang=lang, kind=kind, is_preferred=kind == "official")

        france = cs.polity("FR")
        source = cs.source(
            "Ministère de l'Intérieur", f"Élections sénatoriales du {when.day} {MONTHS[when.month]} {year}"
        )
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Ministère de l'Intérieur",
            title=f"Élections sénatoriales du {when.day} {MONTHS[when.month]} {year}",
            url=config["dataset"],
            license="Licence Ouverte v2.0",
            license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        )
        for key in ("results", "candidates"):
            if key in config:
                cs.artifact(raw.cached(SOURCE, config[key]), source)
        if "elected" not in SHEETS[year]:
            senate_source = cs.source("Sénat", "Base Sénateurs")
            cs.add(
                "source",
                id=senate_source,
                kind="official_body",
                publisher="Sénat",
                title="Base Sénateurs",
                url=SENATE_DATASET,
                license="Licence Ouverte v2.0",
                license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
            )
            for url in SENATE_BASE.values():
                cs.artifact(raw.cached(SOURCE, url), senate_source)

        nuances: dict[str, UUID] = {}
        for code in sorted(set(options["nuance"].drop_nulls())):
            nuances[code] = cs.key("fr.senatoriales.nuance", f"{year}:{code}")
            if nuances[code] in cs.minted:
                cs.add("party", id=nuances[code], kind="nuance", polity_id=france)
                name(nuances[code], code)
                name(nuances[code], code, "fr", "abbreviation")

        labels = dict(
            conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id"
                " join name n on n.entity_id = a.id and n.is_preferred"
                " where a.scheme_code = 'fr.departement' and c.valid @> %s::date",
                [when],
            ).fetchall()
        )
        senate = cs.key("fr.body", "senate")
        systems = {
            "senate-majority": cs.key("fr.electoral_system", "senate-majority"),
            "senate-list": cs.key("fr.electoral_system", "senate-list"),
        }
        election = cs.key("fr.election", f"{when}:senate")
        cs.add("election", id=election, polity_id=france, kind="indirect", date_from=when, date_to=when, status="held")
        name(election, f"{year} French Senate election", "en")
        name(election, f"Élections sénatoriales de {year}", "fr", "native")
        cs.add("slug", path=f"fr/{when}-senate", entity_id=election)
        seats = dict(elected.group_by("unit").len().iter_rows())
        cs.add(
            "note",
            entity_id=election,
            kind="context",
            body_md=NOTE.format(day=f"{when:%-d} {MONTHS_EN[when.month]} {when.year}", seats=elected.height),
        )
        runoff = cs.key("fr.election", f"{when}:senate:2")
        cs.add(
            "election",
            id=runoff,
            polity_id=france,
            parent_id=election,
            kind="runoff",
            date_from=when,
            date_to=when,
            status="held",
        )
        name(runoff, f"{year} French Senate election, second round", "en")
        cs.add("slug", path=f"fr/{when}-senate-runoff", entity_id=runoff)

        persons: dict[str, UUID] = {}

        def person_for(unit: str, surname: str, forename: str | None, sex: str | None, born: date | None) -> UUID:
            key = (
                f"{unit}:{norm(surname)}:{norm(forename or '')}:{born}"
                if born
                else f"{year}:{unit}:{norm(surname)}:{norm(forename or '')}"
            )
            if key not in persons:
                persons[key] = cs.key("fr.senatoriales.person", key)
                if persons[key] in cs.minted:
                    cs.add(
                        "person",
                        id=persons[key],
                        gender=SEX.get((sex or "").upper(), sex),
                        birth_date=born,
                        birth_precision="day" if born else None,
                    )
                    name(persons[key], person_name(forename, surname))
            return persons[key]

        won = {
            (row["unit"], norm(row["surname"]), norm(row["forename"])): row["round"]
            for row in elected.iter_rows(named=True)
        }
        by_roster: dict[tuple[str, str], list[dict]] = {}
        for row in members.sort("position").iter_rows(named=True) if members.height else []:
            by_roster.setdefault(
                (row["unit"], str(row["deposit"]) if row["deposit"] is not None else norm(row["list_label"])), []
            ).append(row)
        roster_by_label = {
            (row["unit"], norm(row["list_label"])): (
                row["unit"],
                str(row["deposit"]) if row["deposit"] is not None else norm(row["list_label"]),
            )
            for row in members.iter_rows(named=True)
        }
        single_rows = {
            (row["unit"], norm(row["surname"]), norm(row["forename"])): row for row in singles.iter_rows(named=True)
        }
        two_rounds = set(accounting.filter(pl.col("round") == 2).select("unit").to_series())
        values: list[dict[str, object]] = []
        first_rounds: dict[str, UUID] = {}
        problems: list[str] = []
        for row in accounting.sort("round", "unit", "proportional").iter_rows(named=True):
            code, round_, proportional = row["unit"], row["round"], row["proportional"]
            rows = options.filter(
                (pl.col("unit") == code) & (pl.col("round") == round_) & (pl.col("proportional") == proportional)
            ).sort("index")
            is_abroad = code == "ZZ"
            key = f"{when}:{code}:{round_}"
            contest = cs.key("fr.senatoriales.contest", key)
            area = None if is_abroad else cs.area("fr.departement", code, when)
            unit = cs.unit("fr.departement", code, when)
            title = "French citizens abroad" if is_abroad else labels[code]
            magnitude = seats.get(code)
            cs.add(
                "contest",
                id=contest,
                election_id=runoff if round_ == 2 else election,
                kind="list_allocation" if proportional else "body_seats",
                body_id=senate,
                district_area_id=area,
                magnitude=magnitude,
                round_number=round_,
                previous_round_id=first_rounds.get(code) if round_ == 2 else None,
                electoral_system_id=systems["senate-list" if proportional else "senate-majority"],
                default_result_kind="counted",
            )
            if round_ == 1:
                first_rounds[code] = contest
            suffix = ""
            if not proportional and code in two_rounds:
                suffix = {1: ", first round", 2: ", second round"}[round_]
            name(contest, f"{title}{suffix}", "en")
            cs.add(
                "slug",
                path=f"fr/{when}-senate/{slug(title)}" + ("-second-round" if round_ == 2 else ""),
                entity_id=contest,
            )
            result_set = cs.key("fr.senatoriales.result_set", key)
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
            for field, measure in ACCOUNTING.items():
                if row.get(field) is not None:
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": None,
                            "measure": measure,
                            "value": row[field],
                        }
                    )
            total = row["valid"] or 0
            if (proportional or magnitude == 1) and sum(rows["votes"]) != total:
                problems.append(f"{key}: votes {sum(rows['votes'])} != valid {total}")
            for entry in rows.iter_rows(named=True):
                option = cs.key("fr.senatoriales.option", f"{key}:{entry['index']}")
                values.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": str(option),
                        "measure": 1,
                        "value": entry["votes"],
                    }
                )
                if proportional:
                    head_sex, head_surname, head_forename = (
                        split_name(entry["head"])
                        if entry["head"]
                        else (entry["sex"], entry["surname"], entry["forename"])
                    )
                    if entry["surname"] is not None:
                        head_surname, head_forename = entry["surname"], entry["forename"]
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=nuances.get(entry["nuance"]),
                        ballot_position=entry["deposit"],
                        ballot_label=entry["list_label"],
                    )
                    roster = by_roster.get(
                        (code, str(entry["deposit"]))
                        if entry["deposit"] is not None
                        else roster_by_label.get((code, norm(entry["list_label"])), ("", "")),
                        [],
                    )
                    listed = [
                        member for member in roster if (code, norm(member["surname"]), norm(member["forename"])) in won
                    ]
                    list_seats = entry["seats"] if entry["seats"] is not None else len(listed)
                    if list_seats:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=list_seats)
                    if roster:
                        if len(listed) != list_seats:
                            problems.append(
                                f"{key}:{entry['index']}: {len(listed)} named winners for {list_seats} seats"
                            )
                        for member in roster:
                            candidacy = cs.key(
                                "fr.senatoriales.candidacy", f"{key}:{entry['index']}:{member['position']}"
                            )
                            cs.add(
                                "candidacy",
                                id=candidacy,
                                person_id=person_for(
                                    code, member["surname"], member["forename"], member["sex"], member["born"]
                                ),
                                option_id=option,
                                role="head" if member["position"] == 1 else "list_member",
                                list_position=member["position"],
                                ballot_name=f"{member['surname']} {member['forename']}",
                            )
                            if member in listed:
                                cs.add(
                                    "outcome",
                                    result_set_id=result_set,
                                    option_id=option,
                                    candidacy_id=candidacy,
                                    kind="elected",
                                )
                    else:
                        candidacy = cs.key("fr.senatoriales.candidacy", f"{key}:{entry['index']}:1")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person_for(code, head_surname, head_forename, head_sex, None),
                            option_id=option,
                            role="head",
                            list_position=1,
                            ballot_name=f"{head_surname} {head_forename}",
                        )
                        if (code, norm(head_surname), norm(head_forename)) in won and list_seats:
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=option,
                                candidacy_id=candidacy,
                                kind="elected",
                            )
                    continue
                identity = (code, norm(entry["surname"]), norm(entry["forename"]))
                roster_row = single_rows.get(identity)
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
                    id=cs.key("fr.senatoriales.candidacy", f"{key}:{entry['index']}"),
                    person_id=person_for(
                        code,
                        entry["surname"],
                        entry["forename"],
                        entry["sex"],
                        roster_row["born"] if roster_row else None,
                    ),
                    option_id=option,
                    role="head",
                    ballot_name=f"{entry['surname']} {entry['forename']}",
                )
                if roster_row is not None and roster_row["substitute_surname"] is not None:
                    cs.add(
                        "candidacy",
                        id=cs.key("fr.senatoriales.candidacy", f"{key}:{entry['index']}:substitute"),
                        person_id=person_for(
                            code,
                            roster_row["substitute_surname"],
                            roster_row["substitute_forename"],
                            roster_row["substitute_sex"],
                            roster_row["substitute_born"],
                        ),
                        option_id=option,
                        role="substitute",
                        ballot_name=f"{roster_row['substitute_surname']} {roster_row['substitute_forename']}",
                    )
                declared = (entry["result"] or "").casefold() in ("elu", "élu")
                if "elected" in SHEETS[year] or entry["result"] is not None:
                    flagged = declared
                else:
                    flagged = won.get(identity) is not None and (
                        (round_ == 2)
                        or (code not in two_rounds)
                        or identity not in second_round_identities(options, code)
                    )
                if flagged:
                    if identity not in won:
                        problems.append(f"{key}: {identity} is marked elected but missing from the elected list")
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        if problems:
            print(*problems, sep="\n")
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


def second_round_identities(options: pl.DataFrame, code: str) -> set[tuple[str, str, str]]:
    return {
        (code, norm(row["surname"]), norm(row["forename"]))
        for row in options.filter((pl.col("unit") == code) & (pl.col("round") == 2)).iter_rows(named=True)
    }


MONTHS = {9: "septembre"}
MONTHS_EN = {9: "September"}
