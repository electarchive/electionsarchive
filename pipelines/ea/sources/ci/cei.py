"""Côte d'Ivoire: presidential and legislative results published by the Commission Électorale Indépendante.

The commission publishes each election as a PDF table. Presidential results are listed by department (the
communes of Abidjan and each foreign country count as places of their own); legislative results are listed by
circumscription. Every package reads one table and keeps its finest places.
"""

import functools
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.ci import tables

SOURCE = "ci.cei"
IVORY_COAST = str(uuid5(package.NAMESPACE, "polity\x1fCI"))
SITE = "https://cei.ci"
PAGE = f"{SITE}/resultats-recents/"
UPLOADS = f"{SITE}/wp-content/uploads"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

FILES = {
    "epr2010-1": f"{UPLOADS}/2020/04/{quote('Résultats-du-premier-tour.pdf')}",
    "epr2010-2": f"{UPLOADS}/2020/04/{quote('Résultats-du-second-tour.pdf')}",
    "edan2011": f"{UPLOADS}/2020/04/{quote('Résultats-définitifs.pdf')}",
    "edan2013": f"{UPLOADS}/2020/04/{quote('Résultats-scrutins-partiels-du-03-février-2013.pdf')}",
    "edan2016": f"{UPLOADS}/2024/03/EDAN-2016-RESULTAT-NATIONAL-18122016-et-29122016-et-28012017.pdf".replace(
        "https://cei.ci", "https://www.cei.ci"
    ),
    "epr2020": "https://www.cei.ci/wp-content/uploads/2024/03/EPR_2020_EXCEL_NATIONAL_PAR_DEPARTEMENT_V4.pdf",
    "edan2021": "https://www.cei.ci/wp-content/uploads/2024/03/EDAN_2021_NATIONAL_V3.pdf",
    "epr2025": f"{UPLOADS}/2025/10/EPR_2025_RESULTAT_NATIONAL.pdf",
    "edan2025": f"{UPLOADS}/2025/12/EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf",
    "edan2026": f"{UPLOADS}/2026/02/REPRISE_EDAN_2025.pdf",
    "esen2023": f"{UPLOADS}/2023/12/RESULTATS_ESEN_SEPT_ET_DEC_2023.pdf",
}
PROCLAMATION_2015 = f"{SITE}/wp-content/uploads/2020/05/" + (
    "50-Proclammation-des-resultats-provisoires-de-lelection-du-president-de-la-republique-du-25-10-2015.pdf"
)
# The commission's department tables of 2015, one file per region (the communes of Abidjan have one each); the
# region of a file without a REGION line is Abidjan.
REGIONS_2015 = [
    "26-Resultats-worodougou",
    "27-Resultats-tonpki",
    "28-Resultats-tchologo",
    "29-Resultats-poro",
    "30-Resultats-loh-djiboua",
    "31-Resultats-la-me",
    "32-Resultats-haut-sassandra",
    "33-Resultats-hambol",
    "34-Resultats-grand-ponts",
    "35-Resultats-gontougo",
    "36-Resultats-gbeke",
    "37-Resultats-cavally",
    "38-Resultats-bounkani",
    "39-Resultats-bere",
    "40-Resultats-bagoue",
    "41-Resultats-bafing",
    "42-Resultats-agneby-tiassa",
    "43-Resultats-abidjan-yopougon",
    "44-Resultats-abidjan-plateau",
    "45-Resultats-abidjan-koumassi",
    "46-Resultats-abidjan-cocody",
    "47-Resultats-abidjan-adjame",
    "48-Resultats-abidjan-adjame",
    "49-Resultats-abidjan-abobo",
]
for _name in REGIONS_2015:
    FILES[f"epr2015-{_name[:2]}"] = f"{SITE}/wp-content/uploads/2020/05/{_name}.pdf"
FILES["epr2015"] = PROCLAMATION_2015
FILES2015 = [name for name in FILES if name.startswith("epr2015-")]
FILES["edan2016"] = (
    "https://www.cei.ci/wp-content/uploads/2024/03/EDAN-2016-RESULTAT-NATIONAL-18122016-et-29122016-et-28012017.pdf"
)


@dataclass(frozen=True)
class Plan:
    day: date
    kind: str  # presidential or legislative
    file: str
    layout: str
    main: date  # for a legislative file, the day most of its circumscriptions voted
    stage: str = "official"
    round: int = 1
    earlier: str | None = None


PLANS = [
    Plan(date(2010, 10, 31), "presidential", "epr2010-1", "2010-1", date(2010, 10, 31), stage="preliminary"),
    Plan(
        date(2010, 11, 28),
        "presidential",
        "epr2010-2",
        "2010-2",
        date(2010, 11, 28),
        stage="preliminary",
        round=2,
        earlier="2010-10-31",
    ),
    Plan(date(2011, 12, 11), "legislative", "edan2011", "2011", date(2011, 12, 11)),
    Plan(date(2012, 2, 26), "legislative", "edan2011", "2011", date(2011, 12, 11)),
    Plan(date(2013, 2, 3), "legislative", "edan2013", "2011", date(2013, 2, 3)),
    Plan(date(2015, 10, 25), "presidential", "epr2015", "2015", date(2015, 10, 25), stage="preliminary"),
    Plan(date(2016, 12, 18), "legislative", "edan2016", "2016", date(2016, 12, 18)),
    Plan(date(2016, 12, 29), "legislative", "edan2016", "2016", date(2016, 12, 18)),
    Plan(date(2017, 1, 28), "legislative", "edan2016", "2016", date(2016, 12, 18)),
    Plan(date(2020, 10, 31), "presidential", "epr2020", "2020", date(2020, 10, 31)),
    Plan(date(2021, 3, 6), "legislative", "edan2021", "2021", date(2021, 3, 6)),
    Plan(date(2021, 4, 24), "legislative", "edan2021", "2021", date(2021, 3, 6)),
    Plan(date(2021, 6, 12), "legislative", "edan2021", "2021", date(2021, 3, 6)),
    Plan(date(2025, 10, 25), "presidential", "epr2025", "2025", date(2025, 10, 25)),
    Plan(date(2025, 12, 27), "legislative", "edan2025", "2025", date(2025, 12, 27)),
    Plan(date(2026, 2, 21), "legislative", "edan2026", "2025", date(2026, 2, 21)),
]
BY_KEY = {str(plan.day): plan for plan in PLANS}

# Candidates are named in the table headers. The 2010 headers print no party; the other years print one.
PRESIDENTS: dict[str, list[tuple[str, str]]] = {
    "2010-1": [
        ("AKOTO YAO KOUADIO FELIX", ""),
        ("ANAKY KOBENA INNOCENT AUGUSTIN", ""),
        ("BEDIE KONAN HENRI AIME", ""),
        ("DOLO ADAMA", ""),
        ("ENOH AKA N'DOUBA", ""),
        ("GBAGBO LAURENT", ""),
        ("GNAMIEN KONAN", ""),
        ("KONAN KOUADIO SIMEON", ""),
        ("LOHOUES ANNE JACQUELINE EPOUSE OBLE", ""),
        ("MABRI TOIKEUSSE ALBERT", ""),
        ("OUATTARA ALASSANE", ""),
        ("TAGOUA NYNSEMON PASCAL", ""),
        ("TOHOU HENRI", ""),
        ("WODIE ROMAIN FRANCIS", ""),
    ],
    "2010-2": [("GBAGBO LAURENT", ""), ("OUATTARA ALASSANE", "")],
    "2015": [
        ("ALASSANE OUATTARA", "RHDP"),
        ("KONAN KOUADIO SIMEON", "INDEPENDANT"),
        ("LAGOU ADJOUA HENRIETTE", "RPC"),
        ("AFFI N'GUESSAN PASCAL", "FPI"),
        ("AMARA ESSY", "INDEPENDANT"),
        ("BANNY KONAN CHARLES", "INDEPENDANT"),
        ("MAMADOU KOULIBALY", "LIDER"),
        ("KOUADIO KONAN BERTIN", "INDEPENDANT"),
        ("KOUANGOUA JACQUELINE-CLAIRE", "INDEPENDANT"),
        ("GNANGBO KACOU", "INDEPENDANT"),
    ],
    "2020": [
        ("ALASSANE OUATTARA", "RHDP"),
        ("AFFI N'GUESSAN PASCAL", "FPI"),
        ("BEDIE KONAN AIME HENRI", "PDCI-RDA"),
        ("KOUADIO KONAN BERTIN", "INDEPENDANT"),
    ],
    "2025": [
        ("ALASSANE OUATTARA", "RHDP"),
        ("EHIVET SIMONE EPSE GBAGBO", "MGC"),
        ("LAGOU ADJOUA HENRIETTE", "GP-PAIX"),
        ("BILLON JEAN-LOUIS EUGENE", "CODE"),
        ("DON-MELLO SENIN AHOUA JACOB", "INDEPENDANT"),
    ],
}
UNOFFICIAL = {"INDEPENDANT", "INDEP.", "INDEP", "INDÉPENDANT", ""}

METHOD = {
    "presidential": (
        "Counted by the Commission Électorale Indépendante per department, as printed in its results table. "
        "The communes of the Abidjan autonomous district, and each foreign country, are places of their own. "
        "Registered voters are the electoral roll, ballots cast are the voters, invalid ballots are the null "
        "ballots, and blank ballots are counted apart from the valid votes for candidates."
    ),
    "legislative": (
        "Counted by the Commission Électorale Indépendante per circumscription, as printed in its results table. "
        "Registered voters are the electoral roll, ballots cast are the voters, and invalid ballots are the null "
        "ballots. Blank ballots, where the table prints them, are counted apart from the valid votes for "
        "candidates. The table prints no result below the circumscription."
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEI_SOURCE = key("source", "cei")
ASSEMBLY = key("body", "national-assembly")
SENATE = key("body", "senate")
PRESIDENCY = key("office", "president")
SYSTEM_PRESIDENT = key("electoral_system", "president", "two_round")
SYSTEM_SINGLE = key("electoral_system", "assembly", "fptp")
SYSTEM_LIST = key("electoral_system", "assembly", "winner_take_all")
SYSTEM_BLOCK = key("electoral_system", "assembly", "block")


def packages() -> list[str]:
    return ["ci.cei/registry", *(f"ci.cei/{plan.day}" for plan in PLANS)]


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    registry().write()
    for plan in PLANS:
        build(plan).write()


def emit() -> None:
    package.run("load", packages())


def text(name: str) -> str:
    path = raw.cached(SOURCE, FILES[name]).path
    if name == "edan2011":
        return tables.layout(str(path))
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return done.stdout


@functools.cache
def legislature(name: str, layout: str) -> tuple[list[tables.Circumscription], list[int]]:
    return tables.legislative(text(name), layout)


def file_text(name: str) -> str:
    path = raw.cached(SOURCE, FILES[name]).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def proclamation_2015() -> tables.Unit:
    """The national provisional figures proclaimed on 28 October 2015."""
    body = " ".join(file_text("epr2015").split())

    def number(label: str) -> int:
        found = re.search(rf"{label}\s*:\s*(\d{{1,3}}(?:\.\d{{3}})*)", body)
        if found is None:
            raise ValueError(f"proclamation: no {label}")
        return int(found.group(1).replace(".", ""))

    surnames = ["OUATTARA", "KONAN Kouadio", "LAGOU", "AFFI", "ESSY", "BANNY", "KOULIBALY", "KOUADIO Konan"]
    surnames += ["KOUANGOUA", "GNANGBO"]
    votes = []
    for surname in surnames:
        found = re.search(rf"{surname}[^:]*:\s*(\d{{1,3}}(?:\.\d{{3}})*)\s+soit", body)
        if found is None:
            raise ValueError(f"proclamation: no votes for {surname}")
        votes.append(int(found.group(1).replace(".", "")))
    figures = {"registered": number("Inscrits"), "cast": number("Votants"), "exprimes": number("Suffrages exprimés")}
    return tables.Unit("", "TOTAL", figures, votes)


@functools.cache
def president(name: str, layout: str) -> tables.Presidential:
    if layout == "2015":
        units: list[tables.Unit] = []
        for part in FILES2015:
            found, _ = tables.regional(file_text(part), "ABIDJAN")
            units.extend(found)
        return tables.Presidential(units, proclamation_2015())
    return tables.presidential(text(name), layout)


def circumscriptions(plan: Plan) -> list[tables.Circumscription]:
    circs, _ = legislature(plan.file, plan.layout)
    return [circ for circ in circs if (circ.day or plan.main) == plan.day and circ.candidates]


ALIASES = {
    "G P-PAIX": "GP-PAIX",
    "I CO N": "ICON",
    "INDEP .": "INDEP.",
    "PDCI-RDA.": "PDCI-RDA",
    "LA RENAISS.": "LA RENAISSANCE",
    "CRI PANAFRICAIN": "C.R.I - PANAFRICAIN",
    "PDCI-RDA - EDS": "PDCI-RDA / EDS",
}


def party_text(text: str) -> str:
    label = " ".join(text.upper().replace("É", "E").split())
    return ALIASES.get(label, label)


def parties() -> list[str]:
    found: set[str] = set()
    for candidates in PRESIDENTS.values():
        found |= {party_text(party) for _, party in candidates}
    for name, layout in {(p.file, p.layout) for p in PLANS if p.kind == "legislative"}:
        for circ in legislature(name, layout)[0]:
            found |= {party_text(c.party) for c in circ.candidates}
    from ea.sources.ci import senate  # the Senate table's lists add their labels to the registry

    found |= set(senate.party_labels())
    return sorted(found - UNOFFICIAL)


def party_id(text: str) -> str | None:
    label = party_text(text)
    return None if label in UNOFFICIAL else key("party", label)


def registry() -> Package:
    p = Package(
        "ci.cei/registry",
        SOURCE,
        "Ivorian parties, the National Assembly, the Senate, the presidency, and their electoral systems, from the "
        "Commission Électorale Indépendante's results tables",
    )
    p.external.add(IVORY_COAST)
    p.add(
        "source",
        id=CEI_SOURCE,
        kind="official_body",
        publisher="Commission Électorale Indépendante",
        title="Election results tables published by the Commission Électorale Indépendante",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Commission Électorale Indépendante de Côte d'Ivoire",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), CEI_SOURCE)
    p.add(
        "scheme",
        code="ci.circonscription",
        polity_id=IVORY_COAST,
        kind="electoral_district",
        rank=1,
        noun="circumscription",
    )
    p.add("scheme", code="ci.departement", polity_id=IVORY_COAST, kind="administrative", rank=2, noun="place")
    p.add("body", id=ASSEMBLY, polity_id=IVORY_COAST, kind="lower_house")
    p.name(ASSEMBLY, "National Assembly of Côte d'Ivoire", "en", "official")
    p.name(ASSEMBLY, "Assemblée nationale de Côte d'Ivoire", "fr", "native", False)
    p.add("body", id=SENATE, polity_id=IVORY_COAST, kind="upper_house")
    p.name(SENATE, "Senate of Côte d'Ivoire", "en", "official")
    p.name(SENATE, "Sénat de Côte d'Ivoire", "fr", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=IVORY_COAST, kind="head_of_state")
    p.name(PRESIDENCY, "President of Côte d'Ivoire", "en", "official")
    p.name(PRESIDENCY, "Président de la République de Côte d'Ivoire", "fr", "native", False)
    systems = (
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with an absolute majority of the valid votes is elected in the first round; otherwise "
                "the two leading candidates go to a second round."
            ),
        ),
        (
            SYSTEM_SINGLE,
            {"formula": "fptp"},
            "One deputy is elected in the circumscription by plurality in a single round.",
        ),
        (
            SYSTEM_LIST,
            {"formula": "winner_take_all"},
            "In a circumscription with several seats the list with the most votes wins every seat.",
        ),
        (SYSTEM_BLOCK, {"formula": "block"}, "Several deputies are elected in the circumscription by plurality."),
    )
    for id, rules, method in systems:
        p.add("electoral_system", id=id, polity_id=IVORY_COAST, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for label in parties():
        id = key("party", label)
        p.add("party", id=id, kind="bloc" if "/" in label else "party", polity_id=IVORY_COAST)
        p.name(id, label, "fr", "abbreviation")
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def accounting(p: Package, result_set: str, unit: str, figures: dict[str, int], valid: int) -> None:
    put(p, result_set, unit, None, REGISTERED, figures["registered"])
    put(p, result_set, unit, None, CAST, figures["cast"])
    put(p, result_set, unit, None, INVALID, figures["invalid"])
    if "blank" in figures:
        put(p, result_set, unit, None, BLANK, figures["blank"])
    put(p, result_set, unit, None, VALID, valid)


def build(plan: Plan) -> Package:
    depends = ["ci.cei/registry"]
    if plan.earlier:
        depends.append(f"ci.cei/{plan.earlier}")
    p = Package(
        f"ci.cei/{plan.day}",
        SOURCE,
        f"The Ivorian {'presidential' if plan.kind == 'presidential' else 'legislative'} election of {plan.day}"
        " by "
        + ("department" if plan.kind == "presidential" else "circumscription")
        + ", from the Commission Électorale Indépendante's results table",
        depends=depends,
    )
    p.external.add(IVORY_COAST)
    p.uses(raw.cached(SOURCE, FILES[plan.file]))
    if plan.kind == "presidential":
        _presidential(p, plan)
    else:
        _legislative(p, plan)
    return p


def _result_set(p: Package, election: str, contest: str, name: str, plan: Plan) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=plan.stage,
        kind="counted",
        source_id=CEI_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD[plan.kind],
        lang="en",
    )
    return result_set


def _presidential(p: Package, plan: Plan) -> None:
    year, runoff = plan.day.year, plan.round == 2
    table = president(plan.file, plan.layout)
    candidates = PRESIDENTS[plan.layout]
    if len(candidates) != len(table.units[0].votes):
        raise ValueError(f"{plan.layout}: {len(candidates)} candidates, {len(table.units[0].votes)} columns")
    election = key("election", "pres", plan.day)
    p.add(
        "election",
        id=election,
        polity_id=IVORY_COAST,
        kind="runoff" if runoff else "general",
        date_from=plan.day,
        date_to=plan.day,
        status="held",
    )
    path = f"ci/{plan.day}-presidential" + ("-runoff" if runoff else "")
    p.add("slug", path=path, entity_id=election, is_current=True)
    suffix = ", second round" if runoff else ""
    p.name(election, f"{year} Ivorian presidential election{suffix}", "en", "official")
    p.name(election, f"Élection présidentielle de {year}{', second tour' if runoff else ''}", "fr", "native", False)
    contest = key("contest", "pres", plan.day)
    previous = key("contest", "pres", plan.earlier) if plan.earlier else None
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=plan.round,
        previous_round_id=previous,
        electoral_system_id=SYSTEM_PRESIDENT,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Ivorian presidential election{', second round' if runoff else ''}", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = _result_set(p, election, contest, f"pres{plan.day}", plan)
    if plan.layout == "2015" and table.total:
        covered = sum(unit.figures["registered"] for unit in table.units) / table.total.figures["registered"]
        p.rows["result_set"][-1]["completeness"] = round(covered, 4)
    options: list[str] = []
    for position, (name, party) in enumerate(candidates, 1):
        person = key("person", "pres", year, name)
        if not runoff:
            p.add("person", id=person)
            p.name(person, name, "fr", "official")
        option = key("option", "pres", plan.day, position)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=name,
        )
        independent = party_text(party) in UNOFFICIAL and bool(party)
        p.add(
            "candidacy",
            id=key("candidacy", "pres", plan.day, position),
            person_id=person if not runoff else key("person", "pres", year, name),
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=party_id(party),
            is_independent=independent,
        )
    # the declared national row, where the table prints one, decides who advanced; a table that covers only part
    # of the country would otherwise be ranked on its own places
    base = table.total.votes if plan.layout == "2015" and table.total else None
    totals = base or [sum(unit.votes[i] for unit in table.units) for i in range(len(options))]
    order = sorted(range(len(options)), key=lambda i: -totals[i])
    majority = totals[order[0]] * 2 > sum(totals)
    for index, option in enumerate(options):
        if runoff:
            kind = "elected" if index == order[0] else "lost"
        elif majority:
            kind = "elected" if index == order[0] else "eliminated"
        else:
            kind = "advanced" if index in order[:2] else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    seen: set[str] = set()
    for unit in table.units:
        label = unit.name if not unit.region or unit.region == unit.name else f"{unit.name} ({unit.region})"
        code = f"{plan.day}:{unit.region}:{unit.name}"
        if code in seen:
            raise ValueError(f"duplicate place {code}")
        seen.add(code)
        id = key("unit", code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=IVORY_COAST,
            scheme_code="ci.departement",
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="abroad_mission" if unit.region.upper().startswith("DIASPORA") else "standard",
        )
        p.name(id, label, "fr", "official")
        figures = unit.figures
        valid = figures["exprimes"] - (0 if plan.layout == "2015" else figures.get("blank", 0))
        accounting(p, result_set, id, figures, valid)
        for option, value in zip(options, unit.votes, strict=True):
            put(p, result_set, id, option, VOTES, value)
    if table.total is not None:
        figures = table.total.figures
        declared = {
            REGISTERED: figures["registered"],
            CAST: figures["cast"],
            VALID: figures["exprimes"] - (0 if plan.layout == "2015" else figures.get("blank", 0)),
        }
        if "invalid" in figures:
            declared[INVALID] = figures["invalid"]
        if "blank" in figures and plan.layout != "2015":
            declared[BLANK] = figures["blank"]
        for measure, value in declared.items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, table.total.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    _presidential_notes(p, plan, result_set, table)


def _presidential_notes(p: Package, plan: Plan, result_set: str, table: tables.Presidential) -> None:
    figures = table.units
    if plan.layout == "2015":
        _notes_2015(p, result_set, table)
    if plan.layout == "2010-1":
        p.accept(
            "accounting.declared",
            "the printed national row leaves out the Bafing region's one department (Touba, Koro and Ouaninou), "
            "whose own row the table prints; every other figure of the national row equals the sum of the "
            "department rows",
        )
        p.add(
            "note",
            id=key("note", result_set, "touba"),
            entity_id=result_set,
            kind="caveat",
            body_md="The table's national row does not include the Touba, Koro and Ouaninou department of the "
            "Bafing region, although the table prints a row for it. The department's own figures are stored as "
            "printed.",
            lang="en",
        )
    if plan.layout == "2010-2":
        p.accept(
            "accounting.declared",
            "the printed national row counts 20,073 polling stations and an electorate of 5,725,721, while the "
            "department rows add up to 20,069 and 5,725,353; the table's ballots, valid votes, and candidate "
            "votes agree",
        )
    over = sum(1 for unit in figures if unit.figures["cast"] > unit.figures["registered"])
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} places print more ballots cast than registered voters, because the staff on duty vote at "
            "the polling station where they work and the table counts them apart from the roll; stored as printed",
        )
    if plan.layout in ("2020", "2025"):
        mismatch = [
            u.name for u in figures if sum(u.votes) + u.figures["blank"] + u.figures["invalid"] != u.figures["cast"]
        ]
        if mismatch:
            p.accept("accounting.ballots_sum", f"{len(mismatch)} places print ballot figures that do not add up")


def _notes_2015(p: Package, result_set: str, table: tables.Presidential) -> None:
    assert table.total is not None
    places = {unit.region for unit in table.units}
    regions = len(places - {"ABIDJAN"})
    registered = sum(unit.figures["registered"] for unit in table.units)
    p.accept(
        "accounting.declared",
        f"the department tables the commission published cover {regions} regions and 7 communes of Abidjan, "
        f"{registered:,} of the {table.total.figures['registered']:,} registered voters; the other regions, "
        "the other communes of Abidjan, and the voters abroad are in no table the commission published, so "
        "the proclaimed national figures exceed the sum of the places",
    )
    p.add(
        "note",
        id=key("note", result_set, "coverage"),
        entity_id=result_set,
        kind="caveat",
        body_md=f"Only {regions} of the country's regions and 7 communes of the Abidjan district have a published "
        "department table, so the places stored here do not add up to the national figures. The declared national "
        "figures are the provisional results proclaimed by the commission's president on 28 October 2015, "
        "which print no null or blank ballots. The commission's table for Anyama lists a special local "
        "commission (CESP) apart from the Anyama commission, stored as printed.",
        lang="en",
    )


def _legislative(p: Package, plan: Plan) -> None:
    year = plan.day.year
    circs = circumscriptions(plan)
    main = plan.day == plan.main
    kind = "general" if main else "by_election"
    election = key("election", "leg", plan.day)
    p.add(
        "election",
        id=election,
        polity_id=IVORY_COAST,
        kind=kind,
        date_from=plan.day,
        date_to=plan.day,
        status="held",
    )
    path = f"ci/{plan.day}-legislative"
    p.add("slug", path=path, entity_id=election, is_current=True)
    when = f"{plan.day.day} {plan.day.strftime('%B %Y')}"
    p.name(election, f"{year} Ivorian legislative election" + ("" if main else f", polls of {when}"), "en", "official")
    p.name(
        election,
        f"Élections législatives de {year}"
        + ("" if main else f", scrutin du {plan.day.day:02d}/{plan.day.month:02d}/{plan.day.year}"),
        "fr",
        "native",
        False,
    )
    accounting_issues: list[str] = []
    people: set[str] = set()
    for circ in circs:
        number = circ.number
        label = circ.name or f"Circonscription {number:03d}"
        elected = [c for c in circ.candidates if c.elected]
        lists = any(c.members for c in circ.candidates) or (circ.seats or 1) > 1
        seats = max(circ.seats or 1, len(elected[0].members) if lists and elected and elected[0].members else 1, 1)
        if not lists:
            seats = max(1, len(elected))
        system = SYSTEM_LIST if lists else (SYSTEM_SINGLE if seats == 1 else SYSTEM_BLOCK)
        contest = key("contest", "leg", plan.day, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            magnitude=seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Ivorian legislative election, circumscription {number}", "en", "official")
        p.name(contest, f"Circonscription {number:03d} : {label}", "fr", "native", False)
        p.add("slug", path=f"{path}/{number:03d}", entity_id=contest, is_current=True)
        result_set = _result_set(p, election, contest, f"leg{plan.day}:{number}", plan)
        unit = key("unit", f"{plan.day}:{number:03d}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IVORY_COAST,
            scheme_code="ci.circonscription",
            code=f"{plan.day}:{number:03d}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, f"Circonscription {number:03d} : {label}", "fr", "official")
        valid = circ.figures["exprimes"] - circ.figures.get("blank", 0)
        accounting(p, result_set, unit, circ.figures, valid)
        if circ.excess:
            accounting_issues.append(f"{number} ({circ.excess:+,})")
        for position, candidate in enumerate(circ.candidates, 1):
            option = key("option", "leg", plan.day, number, position)
            party = party_id(candidate.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list" if lists else "candidate",
                party_id=party if lists else None,
                ballot_position=position,
                ballot_label=candidate.name,
            )
            put(p, result_set, unit, option, VOTES, candidate.score)
            if not lists:
                person = key("person", "leg", plan.day, number, candidate.name)
                if person not in people:
                    people.add(person)
                    p.add("person", id=person)
                    p.name(person, candidate.name, "fr", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", "leg", plan.day, number, position),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate.name,
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if candidate.elected else "lost",
                )
                continue
            if candidate.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=seats)
            else:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="lost")
            for place, member in enumerate(candidate.members, 1):
                name = re.sub(r"^\d+ ?- ?", "", member).strip()
                person = key("person", "leg", plan.day, number, position, name)
                p.add("person", id=person)
                p.name(person, name, "fr", "official")
                candidacy = key("candidacy", "leg", plan.day, number, position, place)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=place,
                    ballot_name=name,
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                if candidate.elected:
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
    if accounting_issues:
        p.accept(
            "accounting.votes_valid",
            "in circumscription(s) "
            + ", ".join(accounting_issues)
            + " the table's candidate votes do not add up to its own valid votes (difference in brackets: candidate "
            "votes minus valid votes); stored as printed",
        )
    over = sum(1 for circ in circs if circ.figures["cast"] > circ.figures["registered"])
    if over:
        p.accept("accounting.turnout", f"{over} circumscriptions print more ballots cast than registered voters")


def totals(plan: Plan) -> str:
    """Compare a table's printed national row with the sum of its places, for the run report."""
    if plan.kind == "presidential":
        table = president(plan.file, plan.layout)
        if table.total is None:
            return f"{plan.day}: no national row printed"
        diffs = {k: sum(u.figures[k] for u in table.units) - v for k, v in table.total.figures.items()}
        diffs = {k: v for k, v in diffs.items() if v}
        votes = [sum(u.votes[i] for u in table.units) - v for i, v in enumerate(table.total.votes)]
        return f"{plan.day}: {'equal' if not diffs and not any(votes) else f'differs {diffs} votes {votes}'}"
    circs, total = legislature(plan.file, plan.layout)
    keys = tables.KEYS[plan.layout]
    printed = dict(zip(keys, total, strict=False))
    sums = Counter()
    for circ in circs:
        for k, v in circ.figures.items():
            sums[k] += v
    diffs = {k: sums[k] - v for k, v in printed.items() if k in sums and sums[k] != v}
    return f"{plan.file}: {len(circs)} circumscriptions; {'equal' if not diffs else f'differs {diffs}'}"
