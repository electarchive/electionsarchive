"""French territorial status consultations, 2003-2013.

The Interior Ministry's published files cover one of the consultations by commune:

- 7 April 2013, Alsace: the referendum on a single territorial collectivity of Alsace, from the ministry's
  dataset on data.gouv.fr (https://www.data.gouv.fr/datasets/referendum-du-7-avril-2013-alsace-resultats-572264).
  The workbook has the region total and a sheet each for départements, legislative constituencies, cantons, and
  communes.

The other consultations (Corsica 2003, the Antilles 2003, Mayotte 2009, Martinique and Guyane 2010) have no file on
data.gouv.fr. Their pages on the ministry's site and the Journal officiel proclamations are not reachable from the
build environment, so they are not built here.

Package `fr.consultations/registry` holds the source and the electoral system. Each consultation is a package
`fr.consultations/<name>-<date>` that attaches to the commune and département areas production already holds.
"""

import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "fr.consultations"
# France as production holds it: the id of the FR row in the reference partition, not the uuid5 of its ISO code.
FRANCE = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
ALSACE_URL = "https://static.data.gouv.fr/5b/cb5f907d61ecd1036720dd4be906ddb46431e90a88e3259b145759eb0bfdec.xls"
DATASET_URL = "https://www.data.gouv.fr/datasets/referendum-du-7-avril-2013-alsace-resultats-572264"
DAY = date(2013, 4, 7)
REGISTRY = f"{SOURCE}/registry"
ALSACE = f"{SOURCE}/alsace-{DAY}"
MEASURES = {"registered": 2, "voters": 14, "invalid": 5, "valid": 4}
RESOURCES = [(ALSACE_URL, ".xls")]
# The reference snapshot holds these rows from the ministry's earlier pipeline: the area ids of Alsace's
# communes and départements, found by code at build time.
REFERENCE = "../data/reference/fr"

DEPARTMENTS = {"67": "Bas-Rhin", "68": "Haut-Rhin"}
QUESTION = (
    "Approuvez-vous le projet de création d'une collectivité territoriale d'Alsace par fusion du conseil régional "
    "d'Alsace, du conseil général du Bas-Rhin et du conseil général du Haut-Rhin ?"
)
METHOD_SYSTEM = (
    "A consultation of the voters of a collectivity under article 72-1 of the Constitution. The proposal carries "
    "only if, in each département concerned, the Yes votes are a majority of valid votes and at least a quarter of "
    "the registered voters."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "consultation")


def fetch() -> None:
    for url, suffix in RESOURCES:
        raw.fetch(SOURCE, url, suffix=suffix)


def packages() -> list[str]:
    return [REGISTRY, ALSACE]


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Interior Ministry's consultation results as a source, and the electoral system of local consultations",
    )
    p.external.add(FRANCE)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Ministère de l'Intérieur",
        title="Résultats des consultations territoriales",
        url=DATASET_URL,
        license="Licence Ouverte v1.0",
        license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        attribution="Ministère de l'Intérieur",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=FRANCE,
        valid_from=date(2003, 3, 28),
        valid_to=None,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.name(SYSTEM, "Local consultation of the voters", "en", "official")
    p.name(SYSTEM, "Consultation des électeurs d'une collectivité", "fr", "native", False)
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD_SYSTEM, lang="en")
    return p


@dataclass
class Counts:
    registered: int
    voters: int
    invalid: int
    valid: int
    yes: int
    no: int

    def check(self, where: str) -> None:
        if self.yes + self.no != self.valid or self.valid + self.invalid != self.voters:
            raise ValueError(f"{where}: the figures do not add up: {self}")
        if self.voters > self.registered:
            raise ValueError(f"{where}: more voters than registered voters")


def number(text: object) -> int:
    return int(re.sub(r"[\s\xa0]", "", str(text)))


def sheet(name: str) -> pl.DataFrame:
    return pl.read_excel(raw.cached(SOURCE, ALSACE_URL).path, sheet_name=name, engine="calamine", has_header=False)


def region() -> Counts:
    rows = {str(r[0]).strip().lower(): r[1] for r in sheet("Région").iter_rows() if r[0] is not None}
    return Counts(
        number(rows["inscrits"]),
        number(rows["votants"]),
        number(rows["blancs et nuls"]),
        number(rows["exprimés"]),
        number(rows["oui"]),
        number(rows["non"]),
    )


def table(name: str, width: int) -> dict[tuple[str, ...], tuple[str, Counts]]:
    """Rows of a sheet: the leading code columns, the label, and the counts."""
    frame = sheet(name)
    found: dict[tuple[str, ...], tuple[str, Counts]] = {}
    for row in frame.iter_rows():
        if not re.fullmatch(r"\d+", str(row[0]).strip()):
            continue
        codes = tuple(str(row[i]).strip() for i in range(0, width * 2, 2))
        label = str(row[width * 2 - 1]).strip()
        rest = row[width * 2 :]
        counts = Counts(
            number(rest[0]),
            number(rest[3]),
            number(rest[5]),
            number(rest[8]),
            number(rest[12]),
            number(rest[16]),
        )
        counts.check(f"{name} {codes}")
        if codes in found:
            raise ValueError(f"{name}: {codes} repeats")
        found[codes] = (label, counts)
    return found


def areas() -> dict[tuple[str, str], str]:
    """The ids of the commune and département areas production holds, by scheme and code."""
    base = pl.read_parquet(f"{REFERENCE}/area.parquet").select("id", "scheme_code")
    codes = pl.read_parquet(f"{REFERENCE}/area_code.parquet").select("area_id", "code")
    joined = codes.join(base, left_on="area_id", right_on="id").filter(
        pl.col("scheme_code").is_in(["fr.commune", "fr.departement"])
    )
    return {(r["scheme_code"], r["code"]): r["area_id"] for r in joined.iter_rows(named=True)}


def alsace() -> Package:
    p = Package(
        ALSACE,
        SOURCE,
        "The 7 April 2013 Alsace referendum on a single territorial collectivity, by commune",
        depends=[REGISTRY],
    )
    p.external.add(FRANCE)
    p.artifact(raw.cached(SOURCE, ALSACE_URL), SOURCE_ID)
    held = areas()
    total = region()
    total.check("region")
    departments = table("Département", 1)
    communes = table("Communes", 2)
    sums = {name: sum(getattr(c, name) for _, c in communes.values()) for name in vars(total)}
    for name, value in vars(total).items():
        if sums[name] != value:
            raise ValueError(f"communes sum to {sums[name]} for {name}, the region total is {value}")
    for (code,), (_, counts) in departments.items():
        part = [c for (d, _), (_, c) in communes.items() if d == code]
        for name in vars(counts):
            if sum(getattr(c, name) for c in part) != getattr(counts, name):
                raise ValueError(f"communes of {code} do not add up to the département's {name}")

    election = key("election", DAY, "alsace")
    p.add("election", id=election, polity_id=FRANCE, kind="referendum", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "2013 Alsace referendum", "en", "official")
    p.name(election, "Référendum du 7 avril 2013 en Alsace", "fr", "native", False)
    path = f"fr/{DAY}-referendum-alsace"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", DAY, "alsace")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.add(
        "ballot_question_text",
        id=key("ballot_question_text", contest, "fr"),
        contest_id=contest,
        kind="text",
        lang="fr",
        body=QUESTION,
        source_id=SOURCE_ID,
    )
    p.name(contest, "Single territorial collectivity of Alsace", "en", "official")
    p.name(contest, "Collectivité territoriale d'Alsace", "fr", "native", False)
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    options = {}
    for position, (answer, label) in enumerate((("yes", "Oui"), ("no", "Non")), start=1):
        options[answer] = key("option", DAY, "alsace", answer)
        p.add(
            "contest_option",
            id=options[answer],
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )

    # Reporting units: each département and each commune equals its area.
    units: dict[tuple[str, ...], str] = {}

    def unit(scheme: str, code: str, name: str, area: str | None, kind: str = "standard") -> str:
        id = key("unit", DAY, scheme, code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=FRANCE,
            scheme_code=scheme,
            code=f"{DAY}:{code}",
            area_relation="equals" if area else "unlocated",
            area_id=area,
            kind=kind,
        )
        p.external.add(scheme)
        if area:
            p.external.add(area)
        p.name(id, name, "fr", "official")
        return id

    for (code,) in departments:
        units[(code,)] = unit("fr.departement", code, DEPARTMENTS[code], held[("fr.departement", code)])
    for (dept, number_), (label, _) in communes.items():
        code = f"{dept}{int(number_):03d}"
        if ("fr.commune", code) not in held:
            raise LookupError(f"no commune area for {code} ({label})")
        units[(dept, number_)] = unit("fr.commune", code, label, held[("fr.commune", code)])

    def result_set(name: str, rows: dict[tuple[str, ...], tuple[str, Counts]], default: bool, grain: str) -> None:
        id = key("result_set", DAY, name)
        p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=default,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="methodology",
            body_md=(
                f"Published by the Interior Ministry by {grain}. Voters are those who voted, invalid ballots are "
                "blank and spoiled ballots together, and valid votes are the ballots expressed. The region total "
                "in the same file is the declared figure. The ministry's file declares no outcome. The Yes votes "
                "were a majority in Bas-Rhin and the No votes a majority in Haut-Rhin, and the Yes votes were "
                "below a quarter of the registered voters in Bas-Rhin, so the proposal did not carry."
            ),
            lang="en",
        )
        for name_, measure in MEASURES.items():
            p.add("declared", result_set_id=id, option_id=None, measure=measure, value=getattr(total, name_))
        for answer, option in options.items():
            p.add("declared", result_set_id=id, option_id=option, measure=1, value=getattr(total, answer))
        sets, unit_ids, option_ids, measures, values = [], [], [], [], []
        for code, (_, counts) in rows.items():
            for name_, measure in MEASURES.items():
                sets.append(id)
                unit_ids.append(units[code])
                option_ids.append(None)
                measures.append(measure)
                values.append(getattr(counts, name_))
            for answer, option in options.items():
                sets.append(id)
                unit_ids.append(units[code])
                option_ids.append(option)
                measures.append(1)
                values.append(getattr(counts, answer))
        size = len(values)
        p.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": sets,
                    "unit_id": unit_ids,
                    "option_id": option_ids,
                    "channel": [0] * size,
                    "measure": measures,
                    "count_round": [0] * size,
                    "flags": [0] * size,
                    "value": values,
                },
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
            ),
        )

    result_set("communes", communes, True, "commune, the finest level it published")
    result_set("departements", departments, False, "département")
    return p


def parse() -> None:
    registry().write()
    alsace().write()
    print(f"Alsace region total: {region()}")
