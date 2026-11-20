"""French cantonal elections of 2001 and 2004 by canton, from the Interior Ministry's results workbooks on data.gouv.fr.

Each election is one package, `fr.departementales/cantonales-<year>`, with both rounds, every candidate, and those
elected. The cantons are the ones in use before the 2015 redistricting, so the packages point at the canton areas,
reporting units, departmental councils, and electoral system that `fr.departementales` already published for the
2008 and 2011 elections; those rows are read from the reference partition of France (`data/reference/fr/`).
Five cantons of 2001 and 2004 that no later election used (Marseille-3 and Marseille-8, and the three cantons of
Saint-Barthélemy and Saint-Martin) are added by `fr.departementales/geo-cantonales-early`.
"""

from dataclasses import dataclass
from datetime import date
from functools import cache
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.fr import departementales
from ea.sources.fr.names import person_name
from ea.store import DATA

SOURCE = departementales.SOURCE
REFERENCE = DATA / "reference" / "fr"
REDISTRICTING = departementales.REDISTRICTING
LICENSE = departementales.LICENSE
STATIC = "https://static.data.gouv.fr"
WORKBOOKS = {
    2004: f"{STATIC}/24/3e463e02def1d6b581092bf8c02783222832b4c2c2094cb2c23136ca10b350.xls",
    2001: f"{STATIC}/e8/880459ad582c32fbee23e037ef42feeb4da14cc03547b3083fdb843f8c5265.xls",
}
DATASETS = {
    2004: ("Elections cantonales 2004 – Résultats", "https://www.data.gouv.fr/datasets/53699382a3a729239d2041f3"),
    2001: ("Elections cantonales 2001 – Résultats", "https://www.data.gouv.fr/datasets/53699381a3a729239d2041f1"),
}
DAYS = {2004: (date(2004, 3, 21), date(2004, 3, 28)), 2001: (date(2001, 3, 11), date(2001, 3, 18))}
QUALIFY = 10
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fsingle-member-two-round-10"))
GEO = f"{SOURCE}/geo-cantonales-early"
REGISTRY = f"{SOURCE}/registry-cantonales-early"
NUANCES = {
    "COM": "Parti communiste français",
    "CPNT": "Chasse, pêche, nature et traditions",
    "DIV": "Divers",
    "DL": "Démocratie libérale",
    "DVD": "Divers droite",
    "DVG": "Divers gauche",
    "ECO": "Écologistes",
    "EXD": "Extrême droite",
    "EXG": "Extrême gauche",
    "FN": "Front national",
    "MDC": "Mouvement des citoyens",
    "MNR": "Mouvement national républicain",
    "PRG": "Parti radical de gauche",
    "RDG": "Parti radical de gauche",
    "REG": "Régionalistes",
    "RPF": "Rassemblement pour la France",
    "RPR": "Rassemblement pour la République",
    "SOC": "Parti socialiste",
    "UDF": "Union pour la démocratie française",
    "UMP": "Union pour un mouvement populaire",
    "VEC": "Les Verts",
}
# Cantons that no election after 2004 used, so the 2008 and 2011 packages never added them: code, department, name.
EXTRA = {
    "1315": ("13", "Marseille-3"),
    "1320": ("13", "Marseille-8"),
    "97125": ("971", "Saint-Barthélemy"),
    "97129": ("971", "Saint-Martin 1"),
    "97144": ("971", "Saint-Martin 2"),
}
# The workbook prints the national totals on its first sheet. Its 2001 first-round registered voters, 19,586,716, are
# 60 more than its canton rows add up to, and its candidates' votes add up to 126 fewer than its valid votes.
NATIONAL = {
    2004: {
        1: {"registered": 20012615, "voters": 12790670, "invalid": 525510, "valid": 12265160, "elected": 518},
        2: {"registered": 16620036, "voters": 11048621, "invalid": 649072, "valid": 10399549, "elected": 1516},
    },
    2001: {
        1: {"registered": 19586716, "voters": 12825656, "invalid": 614499, "valid": 12211157, "elected": 709},
        2: {"registered": 14251628, "voters": 8016689, "invalid": 476907, "valid": 7539782, "elected": 1288},
    },
}
NOTE = (
    "Until 2015 each canton elected one general councillor for six years, and each département renewed half of "
    "its cantons every three years. A candidate was elected in the first round with an absolute majority of the "
    "valid votes and a number of votes at least equal to a quarter of the registered voters. Otherwise a second "
    "round was held a week later among the candidates who won at least 10 percent of the registered voters "
    "(the top two if fewer qualified), and the candidate with the most votes was elected."
)
METHOD = (
    "Results by canton from the Interior Ministry's results workbook for the {year} cantonal elections on "
    "data.gouv.fr. The file reports blank and null ballots together, so they are stored as one invalid count."
)
ROUND_ONE = (
    "No candidate won both an absolute majority of the valid votes and a quarter of the registered voters, so a "
    "second round was held. Candidates who stood again are marked as advancing; candidates who won at least 10 "
    "percent of the registered voters but did not stand again are marked as withdrawn."
)
UNPROCLAIMED = (
    "The Interior Ministry's results workbook counts this second round but names no elected candidate for the "
    "canton, so the counts are stored as published and no outcome is recorded."
)
SHORT = (
    "The workbook gives {valid:,} valid votes for the canton but its candidates' votes add up to {found:,}, "
    "{gap:,} fewer. Both are stored as published."
)
EXTRA_NOTE = (
    "A canton in use before the 2015 redistricting that no election after {year} used, added from the {year} "
    "cantonal results. The Interior Ministry's file gives its code and name; no boundary is available for it."
)


@dataclass(frozen=True)
class Place:
    polity: str
    body: str
    path: str
    name: str


@dataclass(frozen=True)
class Reference:
    france: str
    places: dict[str, Place]
    units: dict[str, tuple[str, str, str]]
    """Each pre-2015 canton's reporting unit, area, and name, by department and canton number."""
    departments: dict[str, str]


def fetch() -> None:
    for url in WORKBOOKS.values():
        raw.fetch(SOURCE, url, suffix=".xls")


@cache
def reference() -> Reference:
    def table(name: str) -> pl.LazyFrame:
        return pl.scan_parquet(REFERENCE / f"{name}.parquet")

    france = table("polity").filter(pl.col("iso_code") == "FR").collect()["id"].item()
    keyed = (
        table("external_id")
        .filter(pl.col("scheme").is_in([f"{SOURCE}.polity", f"{SOURCE}.body", f"{SOURCE}.electoral_system"]))
        .collect()
    )

    def by(scheme: str) -> dict[str, str]:
        found = keyed.filter(pl.col("scheme") == f"{SOURCE}.{scheme}")
        return dict(zip(found["value"], found["entity_id"], strict=True))

    polities = by("polity")
    bodies = by("body")
    names = dict(table("polity").select("id", "name").collect().iter_rows())
    places = {
        dep: Place(polities[dep], bodies[dep], f"fr/departement-{slug(names[polities[dep]])}", names[polities[dep]])
        for dep in polities
        if dep in bodies and dep != "6AE"
    }
    area_names = dict(table("area").select("id", "name").collect().iter_rows())
    held = (
        table("reporting_unit")
        .filter(
            (pl.col("pipeline") == SOURCE)
            & (pl.col("scheme_code") == "fr.canton")
            & (pl.col("valid_to") == REDISTRICTING)
        )
        .collect()
    )
    units = {
        str(row["code"]): (str(row["id"]), str(row["area_id"]), area_names[row["area_id"]])
        for row in held.iter_rows(named=True)
    }
    areas = (
        table("area")
        .filter(pl.col("scheme_code") == "fr.departement")
        .join(table("area_code"), left_on="id", right_on="area_id")
        .filter(pl.col("valid_to").is_null())
        .collect()
    )
    return Reference(
        str(france),
        places,
        units,
        dict(zip(areas["code"], areas["id"], strict=True)),
    )


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def keys(year: int) -> str:
    return f"{SOURCE}/cantonales-{year}"


def packages() -> list[str]:
    return [GEO, REGISTRY, *(keys(year) for year in sorted(WORKBOOKS))]


def parse() -> None:
    held = reference()
    parsed = {year: departementales.cantonales(year, url) for year, url in WORKBOOKS.items()}
    extra = {code: year for year in sorted(parsed) for code in {str(t["canton"]) for t in parsed[year][0]}}
    missing = {code for code in extra if code not in held.units}
    if missing != set(EXTRA):
        raise ValueError(f"cantons absent from production {sorted(missing)}, expected {sorted(EXTRA)}")
    geo(extra).write()
    registry().write()
    for year, (totals, candidates) in parsed.items():
        election(year, pl.DataFrame(totals), pl.DataFrame(candidates)).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    held = reference()
    p = Package(
        REGISTRY,
        SOURCE,
        "The electoral system of the 2001 and 2004 French cantonal elections",
    )
    p.external.add(held.france)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=held.france,
        rules={
            "formula": "two_round",
            "minimum": {"share": 0.25, "of": "registered"},
            "qualify": {"share": 0.1, "of": "registered"},
        },
        rules_version=1,
    )
    p.name(SYSTEM, "French cantonal two-round system, 10 percent threshold", "en", "official")
    p.add("note", id=key("note", "system"), entity_id=SYSTEM, kind="methodology", body_md=NOTE, lang="en")
    return p


def extra_ids(code: str) -> tuple[str, str]:
    return key("area", code), key("unit", code)


def geo(years: dict[str, int]) -> Package:
    held = reference()
    p = Package(
        GEO,
        SOURCE,
        "Five cantons in use before 2015 that only the 2001 and 2004 cantonal elections used",
    )
    p.external.update({held.france, "fr.canton", *(held.departments[dep] for dep, _ in EXTRA.values())})
    for code, (dep, name) in EXTRA.items():
        area, unit = extra_ids(code)
        p.add("area", id=area, scheme_code="fr.canton")
        p.add("area_code", area_id=area, code=code, valid_from=None, valid_to=REDISTRICTING)
        p.add("area_parent", area_id=area, parent_id=held.departments[dep], valid_from=None, valid_to=REDISTRICTING)
        p.name(area, name, "fr", "official")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=held.france,
            scheme_code="fr.canton",
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=None,
            valid_to=REDISTRICTING,
        )
        p.name(unit, name, "fr", "official")
        p.add(
            "note",
            id=key("note", "canton", code),
            entity_id=area,
            kind="context",
            body_md=EXTRA_NOTE.format(year=years[code]),
            lang="en",
        )
    return p


def election(year: int, totals: pl.DataFrame, candidates: pl.DataFrame) -> Package:
    held = reference()
    days = DAYS[year]
    title, url = DATASETS[year]
    wanted = {str(code) for code in totals["canton"]}
    p = Package(
        keys(year),
        SOURCE,
        f"The {year} French cantonal elections, both rounds, by canton, with each candidate and those elected",
        depends=[REGISTRY, *([GEO] if wanted & set(EXTRA) else [])],
    )
    p.external.add(held.france)
    source = p.id("source", year)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Ministère de l'Intérieur",
        title=title,
        url=url,
        license=LICENSE[0],
        license_url=LICENSE[1],
    )
    p.artifact(raw.cached(SOURCE, WORKBOOKS[year]), source)
    sums = candidates.group_by("round", "canton").agg(pl.col("votes").sum().alias("found"))
    shortfalls = {
        (int(row["round"]), str(row["canton"])): (int(row["valid"]), int(row["found"]))
        for row in totals.join(sums, on=["round", "canton"]).iter_rows(named=True)
        if row["valid"] != row["found"]
    }
    umbrella = p.id("election", year)
    p.add(
        "election",
        id=umbrella,
        polity_id=held.france,
        kind="local",
        date_from=days[0],
        date_to=days[1],
        status="held",
    )
    p.name(umbrella, f"{year} French cantonal elections", "en", "official")
    p.name(umbrella, f"Élections cantonales de {year}", "fr", "native", False)
    p.add("slug", path=f"fr/{days[0]}-cantonal", entity_id=umbrella, is_current=True)
    nuances: dict[str, str] = {}
    for code in sorted(candidates["nuance"].unique()):
        nuances[code] = p.id("nuance", f"{year}:{code}")
        p.add("party", id=nuances[code], kind="nuance", polity_id=held.france)
        p.name(nuances[code], NUANCES[code], "fr", "official")
        p.name(nuances[code], code, "fr", "abbreviation", False)
    persons: dict[str, str] = {}
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    for code in wanted & set(held.units):
        p.external.update(held.units[code][:2])
    shortfall_votes = 0
    for dep in sorted(totals["dep"].unique()):
        place = held.places[dep]
        english = departementales.ENGLISH.get(place.name, place.name)
        p.external.update({place.polity, place.body})
        elections: dict[int, str] = {}
        dep_totals = totals.filter(pl.col("dep") == dep)
        for round_ in (1, 2):
            if not dep_totals.filter(pl.col("round") == round_).height:
                continue
            day = days[round_ - 1]
            elections[round_] = p.id("election", f"{year}:{dep}:{round_}")
            p.add(
                "election",
                id=elections[round_],
                polity_id=place.polity,
                parent_id=umbrella if round_ == 1 else elections[1],
                kind="general" if round_ == 1 else "runoff",
                date_from=day,
                date_to=day,
                status="held",
            )
            suffix = ", second round" if round_ == 2 else ""
            p.name(elections[round_], f"{year} {english} cantonal election{suffix}", "en", "official")
            p.add(
                "slug",
                path=f"{place.path}/{day}-{'general' if round_ == 1 else 'runoff'}",
                entity_id=elections[round_],
                is_current=True,
            )
        for code in sorted(dep_totals.filter(pl.col("round") == 1)["canton"]):
            if code in held.units:
                unit, area, label = held.units[code]
            else:
                area, unit = extra_ids(code)
                label = EXTRA[code][1]
            rounds = {
                round_: candidates.filter((pl.col("canton") == code) & (pl.col("round") == round_)).sort("order")
                for round_ in (1, 2)
            }
            held_rounds = [round_ for round_ in (1, 2) if rounds[round_].height]
            decisive = 2 if 2 in held_rounds else 1
            previous: str | None = None
            for round_ in held_rounds:
                day = days[round_ - 1]
                total = totals.filter((pl.col("canton") == code) & (pl.col("round") == round_)).row(0, named=True)
                entries = rounds[round_]
                elected = int(entries["elected"].sum())
                if elected > 1 or (elected == 0 and round_ == 1 and decisive == 1):
                    raise ValueError(f"{year} {code} round {round_}: {elected} elected")
                contest_key = f"{year}:{code}:{round_}"
                contest = p.id("contest", contest_key)
                p.add(
                    "contest",
                    id=contest,
                    election_id=elections[round_],
                    kind="body_seats",
                    body_id=place.body,
                    district_area_id=area,
                    magnitude=1,
                    round_number=round_,
                    previous_round_id=previous,
                    electoral_system_id=SYSTEM,
                    default_result_kind="counted",
                )
                word = "first" if round_ == 1 else "second"
                p.name(contest, f"{label} canton, {word} round", "en", "official")
                p.add(
                    "slug",
                    path=f"{place.path}/{day}-{'general' if round_ == 1 else 'runoff'}/{slug(label)}",
                    entity_id=contest,
                    is_current=True,
                )
                previous = contest
                result_set = p.id("result_set", contest_key)
                p.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[round_],
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=1.0,
                )
                p.add(
                    "note",
                    id=p.id("note", result_set, "methodology"),
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"{METHOD.format(year=year)} {NOTE}",
                    lang="en",
                )
                if round_ == 1 and decisive == 2:
                    p.add(
                        "note",
                        id=p.id("note", result_set, "context"),
                        entity_id=result_set,
                        kind="context",
                        body_md=ROUND_ONE,
                        lang="en",
                    )
                proclaimed = elected == 1
                if round_ == decisive and not proclaimed:
                    p.add(
                        "note",
                        id=p.id("note", result_set, "unproclaimed"),
                        entity_id=result_set,
                        kind="caveat",
                        body_md=UNPROCLAIMED,
                        lang="en",
                    )
                if (round_, code) in shortfalls:
                    valid, found = shortfalls[round_, code]
                    shortfall_votes += valid - found
                    p.add(
                        "note",
                        id=p.id("note", result_set, "short"),
                        entity_id=result_set,
                        kind="caveat",
                        body_md=SHORT.format(valid=valid, found=found, gap=valid - found),
                        lang="en",
                    )
                registered = int(total["registered"])
                runoff = {
                    (surname, forename)
                    for entry in rounds[2].iter_rows(named=True)
                    for _, surname, forename in departementales.people(entry)
                }
                qualified = {
                    entry["binome"] for entry in entries.iter_rows(named=True) if entry["votes"] * QUALIFY >= registered
                }
                if len(qualified) < 2:
                    qualified |= set(entries.sort("votes", descending=True).head(2)["binome"])
                for field, measure in (("registered", 2), ("voters", 14), ("valid", 4), ("invalid", 5)):
                    sets.append(result_set)
                    unit_ids.append(unit)
                    option_ids.append(None)
                    measures.append(measure)
                    values.append(int(total[field]))
                for entry in entries.iter_rows(named=True):
                    option = p.id("option", contest_key, entry["order"])
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=nuances[entry["nuance"]],
                        ballot_label=entry["binome"],
                    )
                    for gender, surname, forename in departementales.people(entry):
                        person_key = f"{year}:{code}:{surname}:{forename}"
                        if person_key not in persons:
                            persons[person_key] = p.id("person", person_key)
                            p.add("person", id=persons[person_key], gender=gender)
                            p.name(persons[person_key], person_name(forename, surname), "fr", "official")
                        p.add(
                            "candidacy",
                            id=p.id("candidacy", contest_key, entry["order"]),
                            person_id=persons[person_key],
                            option_id=option,
                            role="head",
                            ballot_name=" ".join(part for part in (surname, forename) if part),
                            is_independent=False,
                        )
                    kind: str | None
                    if round_ == decisive:
                        kind = ("elected" if entry["elected"] else "lost") if proclaimed else None
                    elif any((surname, forename) in runoff for _, surname, forename in departementales.people(entry)):
                        kind = "advanced"
                    elif entry["binome"] in qualified:
                        kind = "withdrew"
                    else:
                        kind = "eliminated"
                    if kind is not None:
                        p.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind=kind,
                            seats=1 if kind == "elected" else None,
                        )
                    sets.append(result_set)
                    unit_ids.append(unit)
                    option_ids.append(option)
                    measures.append(1)
                    values.append(int(entry["votes"]))
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
    for (round_, code), (valid, found) in sorted(shortfalls.items()):
        p.accept(
            "accounting.votes_valid",
            f"the workbook's own figures disagree: canton {code}, round {round_} of {year}, gives {valid:,} valid "
            f"votes but its candidates' votes add up to {found:,}; the national sheet adds up the candidates' votes "
            "and so carries the same gap",
        )
    compare(year, totals, candidates)
    return p


def compare(year: int, totals: pl.DataFrame, candidates: pl.DataFrame) -> dict[str, Any]:
    """Add the canton rows up and set them beside the national figures the workbook prints."""
    report: dict[str, Any] = {}
    for round_, printed in NATIONAL[year].items():
        rows = totals.filter(pl.col("round") == round_)
        added = {field: int(rows[field].sum()) for field in ("registered", "voters", "invalid", "valid")}
        added["elected"] = int(candidates.filter((pl.col("round") == round_) & pl.col("elected")).height)
        report[str(round_)] = {
            field: {"printed": printed[field], "cantons": added[field], "difference": added[field] - printed[field]}
            for field in printed
            if added[field] != printed[field]
        }
    print(f"{keys(year)}: national totals differ in {report}", flush=True)
    return report
