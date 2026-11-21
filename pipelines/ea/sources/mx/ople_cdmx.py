"""Congress of Mexico City: district contests of 2018, 2021, and 2024 and the 2021 list contest, from the
Instituto Electoral de la Ciudad de México's volumes on the local elections' results."""

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import TypedDict
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.mx.ine import BALLOTS, INVALID, REGISTERED, VALID, VOTES

SOURCE = "mx.ople_cdmx"
IECM = "Instituto Electoral de la Ciudad de México (IECM)"
LICENSE = "Public information the IECM publishes for download; no license stated"
PAGE = "https://www.iecm.mx/elecciones/historial-de-elecciones/"
BOOKS = "https://www.iecm.mx/www/sites/biblioteca_electronica/estadisticas_y_estudios_electorales/"

COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fMX"))
CMX = str(uuid5(package.NAMESPACE, "polity\x1fMX-CMX"))
ENTIDAD_AREA = "370ed930-ba1a-58e3-b7a7-7e4750abd976"
ENTIDAD_UNIT = "bd0ea933-645f-5f3e-a503-bc28ef364fdc"

# Rows production already holds: the state elections (written with the borough mayors), the 33 districts, the
# parties of the national registry, and the four parties the borough pipeline minted for Mexico City.
ELECTIONS = {
    2018: "abea24d3-2b30-5bca-ba1f-8f39f399c49c",
    2021: "23519157-8b40-5433-be63-b9608add4d22",
    2024: "eadb9c05-1ee5-5ddc-8514-c0859856c611",
}
PARTIES = {
    "PAN": "c7f7de92-2021-5182-a538-50e6dc2c64fa",
    "PRI": "ded3d18b-d315-5f42-b202-cc7f502b38d3",
    "PRD": "15180a59-5f2e-51a2-89c5-4e4d2bd91c16",
    "PVEM": "d6d1266b-9019-54a9-9683-e15da7432c95",
    "PT": "bbe36184-ecc1-5cba-837a-8b0ba40b0773",
    "MC": "d53bdfc2-8d0e-51e3-977d-80a7af9a612f",
    "MORENA": "2faabb19-0945-5f15-a06d-d037e5682638",
    "NA": "3f771538-711a-584e-a4b1-537e3d6dfc64",
    "PES": "9bed45cf-265d-5bc9-8a18-fb6b30e87378",
    "PH": "f44cdaf5-04cb-511b-a1ce-52d71bf8238f",
    "ELIGE": "6a957c2d-c737-5e4c-ade9-ee3f4a9fa96d",
    "RSP": "dfcd36a3-cfee-5ebb-a7dc-e9bdac5feba8",
    "FXM": "bc06ddd9-9d4a-5ba6-a2e5-de360dcd47d3",
}
ORDER = list(PARTIES)

BOOK_2018 = BOOKS + "estadisticas_de_procesos_electorales_locales/2018_estadistica_elecciones_locales_resultados.pdf"
BOOK_2021 = "https://www.iecm.mx/www/docs/Estadistica-de-las-elecciones-locales-2021-resultados.pdf"
BOOK_2024 = BOOKS + "estadisticas_de_procesos_electorales_locales/estadistica_elecciones_locales_resultados2024.pdf"

# Columns of each book's table "Resultados de la votación por distrito electoral, según fuerza política": the
# pages that hold the districts' left halves (district, sections, casillas, then the forces) and, beside each,
# the right half (independent candidacies, unregistered, null, total, nominal list, turnout).
YEARS: dict[int, dict] = {
    2018: {
        "day": date(2018, 7, 1),
        "book": BOOK_2018,
        "title": "Estadística de las elecciones locales 2018. Resultados",
        "forces": ["PAN", "PRI", "PVEM", "NA", "PH", "PT-MORENA-PES", "PAN-PRD-MC", "PRD-MC"],
        "left": [73, 75],
        "right": [74, 76],
        "independents": 10,
    },
    2021: {
        "day": date(2021, 6, 6),
        "book": BOOK_2021,
        "title": "Estadística de las elecciones locales 2021. Resultados",
        "forces": [
            "PAN",
            "PRI",
            "PRD",
            "PVEM",
            "MC",
            "ELIGE",
            "PES",
            "RSP",
            "FXM",
            "PAN-PRI-PRD",
            "PAN-PRD",
            "PRI-PRD",
            "PVEM-PT-MORENA",
            "PT-MORENA",
        ],
        "left": [34, 36],
        "right": [35, 37],
        "independents": 9,
    },
    2024: {
        "day": date(2024, 6, 2),
        "book": BOOK_2024,
        "title": "Estadística de las elecciones locales 2024. Resultados",
        "forces": ["MC", "MORENA-PT-PVEM", "PAN-PRI-PRD"],
        "left": [46, 47],
        "right": [],
        "independents": 0,
    },
}
# The statewide counts the books print for the whole contest, to check the district sums against.
STATEWIDE = {
    2018: {"sections": 5536, "casillas": 12976},
    2021: {"registered": 7722542, "null": 123832, "unregistered": 5568, "total": 4029984},
    2024: {
        "sections": 5568,
        "casillas": 13431,
        "registered": 7898982,
        "null": 98956,
        "unregistered": 4840,
        "total": 5505165,
    },
}
FORCES = {
    2018: {
        "PAN": 38449,
        "PRI": 512967,
        "PVEM": 226922,
        "NA": 80934,
        "PH": 107365,
        "PT-MORENA-PES": 2645148,
        "PAN-PRD-MC": 1498734,
        "PRD-MC": 16820,
    },
    2024: {"MC": 406445, "MORENA-PT-PVEM": 2868193, "PAN-PRI-PRD": 2126731},
}
WINNERS_2021 = {"PAN-PRI-PRD": 8, "PT-MORENA": 18, "PVEM-PT-MORENA": 1, "PRI-PRD": 1, "PAN": 5}

# The 2021 list contest, from the book's statewide table of the list election (with the votes from abroad) and
# its table of seats as the electoral tribunal revised them (SUP-REC-1423-2021 and joined cases).


class ListFigures(TypedDict):
    parties: list[str]
    votes: list[int]
    independents: list[int]
    unregistered: int
    null: int
    total: int
    registered: int
    seats: dict[str, int]


RP_2021: ListFigures = {
    "parties": ["PAN", "PRI", "PRD", "PVEM", "PT", "MC", "MORENA", "ELIGE", "PES", "RSP", "FXM"],
    "votes": [1033651, 581198, 207596, 125245, 85850, 140172, 1513772, 33242, 72546, 29475, 79926],
    "independents": [1560, 2034, 1146, 1785, 1778, 1104, 1435, 1124, 941],
    "unregistered": 5606,
    "null": 124180,
    "total": 4045366,
    "registered": 7734769,
    "seats": {"PAN": 10, "PRI": 5, "PRD": 2, "PVEM": 1, "PT": 0, "MC": 1, "MORENA": 14},
}

ALLIANCE_NAMES = {
    "PAN-PRI-PRD": "Candidatura común PAN-PRI-PRD",
    "PAN-PRD": "Candidatura común PAN-PRD",
    "PRI-PRD": "Candidatura común PRI-PRD",
    "PVEM-PT-MORENA": "Candidatura común Morena-PT-PVEM",
    "PT-MORENA": "Candidatura común PT-Morena",
    "PT-MORENA-PES": "Coalición Juntos Haremos Historia",
    "PAN-PRD-MC": "Coalición Por México al Frente",
    "PRD-MC": "Candidatura común PRD-MC",
    "MORENA-PT-PVEM": "Coalición Sigamos Haciendo Historia en la Ciudad de México",
    "PAN-PRI-PRD@2024": "Coalición Va por la Ciudad de México",
}
MR_RULES = {"formula": "fptp"}
RP_RULES = {
    "formula": "hare",
    "threshold": {"percent": 3, "level": "party", "scope": "contest"},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
MR_SYSTEM = (
    "Each of the 33 single-member local districts of Mexico City elects one deputy by plurality of the votes cast, "
    "with no runoff. The other 33 seats of the Congress of Mexico City go to proportional representation from "
    "closed lists (Constitución Política de la Ciudad de México, art. 29)."
)
RP_SYSTEM = (
    "Thirty-three seats are filled from closed lists of the parties for the whole city. A party takes part when it "
    "wins 3 percent of the valid vote and seats go by the natural quotient and largest remainders, subject to the "
    "limit on overrepresentation in the federal constitution (art. 116 II). The model records the formula and the "
    "threshold; the limit is in the declared seats."
)
MR_METHOD = (
    "District counts as printed in the IECM's statistical volume on the {year} local elections, which sums the "
    "district councils' cómputos: {votes:,} ballots in the 33 contests. Each candidacy is a party, or the "
    "candidatura común or coalition that a voter marked as one; votes marked for several parties of one candidatura "
    "count for its candidate. The volume prints the independent candidacies in one column each without naming them "
    "in text, so they are held as one option per district. The winner is the candidacy with the most votes, which "
    "is the one the district council declared."
)
RP_METHOD = (
    "Statewide votes for the 2021 list election as printed in the IECM's statistical volume, with the votes cast "
    "abroad, and the seats as the Sala Superior of the electoral tribunal revised them. The volume prints the "
    "independent candidacies in separate columns without naming them in text, so they are held as one option."
)
CAVEAT_2021 = (
    "The volume's district counts leave out the 83 casillas whose votes the courts annulled in this election, with "
    "their nominal list."
)


def book_path(year: int) -> Path:
    return raw.cached(SOURCE, YEARS[year]["book"]).path


def fetch() -> None:
    for config in YEARS.values():
        raw.fetch(SOURCE, config["book"], suffix=".pdf")


NUMBER = re.compile(r"-|\d+(?: \d{3})*|\d+\.\d+")


def page_rows(path: Path, page: int) -> list[list[str]]:
    text = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = []
    for line in text.split("\n"):
        tokens = [t for t in re.split(r"\s{2,}", line.strip()) if t]
        if len(tokens) >= 4 and tokens[0] != "(%)" and all(NUMBER.fullmatch(t) for t in tokens):
            rows.append(tokens)
    return rows


def count(token: str) -> int:
    return 0 if token == "-" else int(token.replace(" ", ""))


def parse_year(year: int) -> dict[int, dict]:
    """District figures: sections, casillas, force votes, independents, unregistered, null, total, nominal list."""
    config = YEARS[year]
    path = book_path(year)
    forces: list[str] = config["forces"]
    width = 3 + len(forces)
    districts: dict[int, dict] = {}
    left_pages, right_pages = config["left"], config["right"]
    lefts: list[list[str]] = []
    for page in left_pages:
        lefts += [r for r in page_rows(path, page) if len(r) >= width and re.fullmatch(r"\d{1,2}", r[0])]
    if year == 2024:
        # One page holds each district's whole row; the participation column may print without decimals.
        for row in lefts:
            if len(row) != width + 5:
                continue
            number = int(row[0])
            districts[number] = {
                "sections": count(row[1]),
                "casillas": count(row[2]),
                "votes": {f: count(v) for f, v in zip(forces, row[3 : 3 + len(forces)], strict=True)},
                "independents": 0,
                "unregistered": count(row[width]),
                "null": count(row[width + 1]),
                "total": count(row[width + 2]),
                "registered": count(row[width + 3]),
            }
        return districts
    rights: list[list[str]] = []
    k = config["independents"]
    for page in right_pages:
        for row in page_rows(path, page):
            if len(row) != k + 5 or (row[-1] == "-" and row[-3] == "100"):
                continue
            rights.append(row)
    ordinary = [row for row in lefts if len(row) == width]
    printed = len(rights) == len(ordinary) + 1
    if not printed and len(rights) != len(ordinary):
        raise ValueError(f"{year}: {len(ordinary)} district rows against {len(rights)} right-hand rows")
    for left, right in zip(ordinary, rights[: len(ordinary)], strict=True):
        districts[int(left[0])] = {
            "sections": count(left[1]),
            "casillas": count(left[2]),
            "votes": {f: count(v) for f, v in zip(forces, left[3:], strict=True)},
            "independents": sum(count(v) for v in right[:k]),
            "unregistered": count(right[k]),
            "null": count(right[k + 1]),
            "total": count(right[k + 2]),
            "registered": count(right[k + 3]),
        }
    if not printed:
        return districts
    totals = rights[-1]
    districts[0] = {
        "independents": sum(count(v) for v in totals[:k]),
        "unregistered": count(totals[k]),
        "null": count(totals[k + 1]),
        "total": count(totals[k + 2]),
        "registered": count(totals[k + 3]),
        "votes": {},
    }
    return districts


def check_year(year: int, districts: dict[int, dict]) -> None:
    if sorted(n for n in districts if n) != list(range(1, 34)):
        raise ValueError(f"{year}: districts {sorted(districts)}")
    forces = YEARS[year]["forces"]
    for number, d in districts.items():
        if not number:
            continue
        if sum(d["votes"].values()) + d["independents"] + d["unregistered"] + d["null"] != d["total"]:
            raise ValueError(f"{year} district {number}: the figures do not sum to the printed total")
    if 0 in districts:
        printed = districts[0]
        for field in ("independents", "unregistered", "null", "total", "registered"):
            summed = sum(d[field] for n, d in districts.items() if n)
            if summed != printed[field]:
                raise ValueError(f"{year} {field}: districts sum to {summed:,}, the volume prints {printed[field]:,}")
    known = STATEWIDE[year]
    for field, value in known.items():
        if value is not None:
            summed = sum(d[field] for n, d in districts.items() if n)
            if summed != value:
                raise ValueError(f"{year} {field}: districts sum to {summed:,}, the volume prints {value:,}")
    for force, printed in FORCES.get(year, {}).items():
        summed = sum(d["votes"][force] for n, d in districts.items() if n)
        if summed != printed:
            raise ValueError(f"{year} {force}: districts sum to {summed:,}, the volume prints {printed:,}")
    if year == 2021:
        wins: dict[str, int] = {}
        for n, d in districts.items():
            if n:
                best = max(d["votes"], key=lambda f: d["votes"][f])
                wins[best] = wins.get(best, 0) + 1
        if wins != WINNERS_2021:
            raise ValueError(f"2021 winners {wins} differ from the volume's {WINNERS_2021}")
    del forces


def key(p: Package, kind: str, *parts: object) -> str:
    return p.id(kind, p.key, *parts)


def members(force: str) -> list[str]:
    return force.split("-")


def alliance_name(year: int, force: str) -> str:
    if year == 2024 and force == "PAN-PRI-PRD":
        return ALLIANCE_NAMES["PAN-PRI-PRD@2024"]
    return ALLIANCE_NAMES[force]


def source_row(p: Package, year: int, source: str) -> None:
    config = YEARS[year]
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=IECM,
        title=config["title"],
        url=PAGE,
        license=LICENSE,
        license_url=PAGE,
        attribution=IECM,
    )
    p.artifact(raw.cached(SOURCE, config["book"]), source)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Congress of Mexico City and the electoral systems of its district and list seats",
    )
    p.external |= {CMX}
    body = key(p, "body", "congreso")
    p.add("body", id=body, polity_id=CMX, kind="unicameral")
    p.name(body, "Congress of Mexico City", "en", "official")
    p.name(body, "Congreso de la Ciudad de México", "es", "native", False)
    p.add("body_size", body_id=body, seats=66, valid_from=date(2018, 9, 17))
    for name, rules, text, label in (
        ("mr", MR_RULES, MR_SYSTEM, "Mexico City local district plurality system"),
        ("rp", RP_RULES, RP_SYSTEM, "Mexico City closed-list proportional system"),
    ):
        system = key(p, "electoral_system", name)
        p.add("electoral_system", id=system, polity_id=CMX, rules=rules, rules_version=1)
        p.name(system, label, "en", "official")
        p.add("note", id=key(p, "note", system), entity_id=system, kind="methodology", body_md=text, lang="en")
    return p


def election(year: int) -> Package:
    config = YEARS[year]
    day: date = config["day"]
    districts = parse_year(year)
    check_year(year, districts)
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Congress of Mexico City election by district from the IECM's statistical volume",
        depends=[f"{SOURCE}/registry"],
    )
    reg = Package(f"{SOURCE}/registry", SOURCE, "")
    body = key(reg, "body", "congreso")
    systems = {"mr": key(reg, "electoral_system", "mr"), "rp": key(reg, "electoral_system", "rp")}
    state = ELECTIONS[year]
    p.external |= {COUNTRY, CMX, state, ENTIDAD_UNIT, ENTIDAD_AREA, "mx.distrito_local", *PARTIES.values()}
    source = key(p, "source", "volume")
    source_row(p, year, source)
    path = f"mx/cmx/{day}-general"
    alliances: dict[str, str] = {}

    def alliance(force: str) -> str:
        if force not in alliances:
            alliances[force] = key(p, "alliance", force)
            p.add("alliance", id=alliances[force], kind="electoral_alliance", election_id=state)
            p.name(alliances[force], alliance_name(year, force), "es", "native")
            for member in members(force):
                p.add("alliance_member", alliance_id=alliances[force], party_id=PARTIES[member])
        return alliances[force]

    total_votes = sum(d["total"] for n, d in districts.items() if n)
    forces: list[str] = config["forces"]
    for number in range(1, 34):
        d = districts[number]
        area = DISTRICT_AREAS[number]
        p.external.add(area)
        contest = key(p, "contest", "mr", number)
        p.add(
            "contest",
            id=contest,
            election_id=state,
            kind="body_seats",
            body_id=body,
            magnitude=1,
            electoral_system_id=systems["mr"],
            district_area_id=area,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Congress of Mexico City, district {number}", "en", "official")
        p.name(contest, f"Diputación local {year}, Ciudad de México, distrito {number}", "es", "native", False)
        p.add("slug", path=f"{path}/congress-district-{number}", entity_id=contest, is_current=True)
        result_set = key(p, "result_set", "mr", number)
        p.add(
            "result_set",
            id=result_set,
            election_id=state,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
        )
        p.add(
            "note",
            id=key(p, "note", result_set, "method"),
            entity_id=result_set,
            kind="methodology",
            body_md=MR_METHOD.format(year=year, votes=total_votes),
            lang="en",
        )
        if year == 2021:
            p.add(
                "note",
                id=key(p, "note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=CAVEAT_2021,
                lang="en",
            )
        order = sorted((f for f in forces if d["votes"][f]), key=lambda f: -d["votes"][f])
        options: dict[str, str] = {}
        for position, force in enumerate(order, 1):
            option = options[force] = key(p, "option", "mr", number, force)
            parts = members(force)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=PARTIES[parts[0]] if len(parts) == 1 else None,
                alliance_id=alliance(force) if len(parts) > 1 else None,
                ballot_position=position,
                ballot_label="–".join(parts),
            )
        if d["independents"]:
            option = options["IND"] = key(p, "option", "mr", number, "independents")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=len(options),
                ballot_label="Candidaturas independientes",
            )
        write_in = key(p, "option", "mr", number, "unregistered")
        p.add(
            "contest_option",
            id=write_in,
            contest_id=contest,
            kind="write_in",
            ballot_label="Candidaturas no registradas",
        )
        p.add("outcome", result_set_id=result_set, option_id=options[order[0]], kind="elected", seats=1)
        unit = key(p, "unit", "district", number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=COUNTRY,
            scheme_code="mx.distrito_local",
            code=f"{year}:09-{number:02d}",
            parent_id=ENTIDAD_UNIT,
            area_id=area,
            area_relation="equals",
            kind="standard",
        )
        values: list[tuple[int, str | None, int]] = [
            (REGISTERED, None, d["registered"]),
            (BALLOTS, None, d["total"]),
            (VALID, None, d["total"] - d["null"]),
            (INVALID, None, d["null"]),
            (VOTES, write_in, d["unregistered"]),
            *((VOTES, options[f], d["votes"][f]) for f in order),
        ]
        if d["independents"]:
            values.append((VOTES, options["IND"], d["independents"]))
        for measure, option_id, value in values:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option_id,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
    if year == 2021:
        rp_2021(p, state, body, systems["rp"], path)
    print(year, "ballots", f"{total_votes:,}")
    return p


def rp_2021(p: Package, state: str, body: str, system: str, path: str) -> None:
    data = RP_2021
    year = 2021
    config = YEARS[year]
    source = key(p, "source", "volume")
    summed = sum(data["votes"]) + sum(data["independents"]) + data["unregistered"] + data["null"]
    if summed != data["total"]:
        raise ValueError(f"2021 list votes sum to {summed:,}, the volume prints {data['total']:,}")
    contest = key(p, "contest", "rp")
    p.add(
        "contest",
        id=contest,
        election_id=state,
        kind="list_allocation",
        body_id=body,
        magnitude=33,
        electoral_system_id=system,
        district_area_id=ENTIDAD_AREA,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Congress of Mexico City, proportional representation", "en", "official")
    p.name(
        contest, f"Diputaciones locales {year}, Ciudad de México, representación proporcional", "es", "native", False
    )
    p.add("slug", path=f"{path}/congress-proportional", entity_id=contest, is_current=True)
    result_set = key(p, "result_set", "rp")
    p.add(
        "result_set",
        id=result_set,
        election_id=state,
        contest_id=contest,
        stage="official",
        kind="counted",
        status="active",
        source_id=source,
        is_default=True,
        completeness=1,
    )
    p.add(
        "note",
        id=key(p, "note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=RP_METHOD,
        lang="en",
    )
    order = sorted(zip(data["parties"], data["votes"], strict=True), key=lambda item: -item[1])
    options: dict[str, str] = {}
    for position, (party, _) in enumerate(order, 1):
        options[party] = key(p, "option", "rp", party)
        p.add(
            "contest_option",
            id=options[party],
            contest_id=contest,
            kind="list",
            party_id=PARTIES[party],
            ballot_position=position,
            ballot_label=party,
        )
    independents = key(p, "option", "rp", "independents")
    p.add(
        "contest_option",
        id=independents,
        contest_id=contest,
        kind="independents",
        ballot_position=len(order) + 1,
        ballot_label="Candidaturas sin partido",
    )
    write_in = key(p, "option", "rp", "unregistered")
    p.add(
        "contest_option",
        id=write_in,
        contest_id=contest,
        kind="write_in",
        ballot_label="Candidaturas no registradas",
    )
    for party, seats in data["seats"].items():
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=options[party], kind="won", seats=seats)
    values: list[tuple[int, str | None, int]] = [
        (REGISTERED, None, data["registered"]),
        (BALLOTS, None, data["total"]),
        (VALID, None, data["total"] - data["null"]),
        (INVALID, None, data["null"]),
        (VOTES, write_in, data["unregistered"]),
        (VOTES, independents, sum(data["independents"])),
        *((VOTES, options[party], votes) for party, votes in order),
    ]
    for measure, option_id, value in values:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=ENTIDAD_UNIT,
            option_id=option_id,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )
    del config


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# The 33 districts of Mexico City, as the district pipeline loaded them.
DISTRICT_AREAS = {
    1: "c2a7b78e-9c56-580b-862a-2a1606d056f9",
    2: "61d5292b-299d-54cd-939c-ec14ce452b52",
    3: "68e98699-98dd-5a0d-8270-e940f4b3829b",
    4: "9de3fc25-e938-581f-9c2a-abbed49da5d0",
    5: "559f548e-4fd9-53cc-a8cb-47cbca9a68f4",
    6: "8e5bcbd4-3390-54fd-8a2d-ad8660286070",
    7: "a3874f46-d0ba-5840-8b35-6059491889f1",
    8: "4e57d1dd-a41f-5c0c-aa8c-5f2313ad1a7b",
    9: "f9d9423d-6e1f-57af-b5eb-75bd9f02c2d3",
    10: "24893514-7c0f-5c3d-9414-169b3e58cba9",
    11: "ee01c02f-f344-5d90-8145-8255e0eedc48",
    12: "566aae1b-0daa-581e-becf-05c2b2c33b9b",
    13: "4d2b0bb9-1e64-5e22-a94b-a8941138e72b",
    14: "b40f552e-45a9-5bf7-8809-365fa9718bf0",
    15: "d46694df-b0bc-5b9f-8ad1-f351e8eda37c",
    16: "06aa5c4c-e058-5760-8dfb-1654a1660519",
    17: "89aae36a-8a0a-5747-8302-45dd35260ec3",
    18: "d34bdf15-75de-5494-8b66-2ffcf9dc3dff",
    19: "b2de3738-a26e-56b0-9701-d8bb62ade9d7",
    20: "e3fa9db2-5873-5187-bce4-74eb28ff7a34",
    21: "fda8185d-20f1-5115-b6d3-e14c78be354f",
    22: "675b4ae8-32f6-5df7-b90c-5230d8d0d15d",
    23: "43b51a80-9ff0-5901-ad1c-259e2bbe7698",
    24: "4b68e4a3-b7c6-5a36-910a-29ade5bfc4b8",
    25: "55fca33c-2e76-512f-9046-b1d6f5964c67",
    26: "6c2b69f5-47ad-507c-9d6e-b1f9327afa48",
    27: "346b6a68-f01a-54a1-b763-f0a2d274b256",
    28: "16a0a4c2-bfd6-581b-97a9-8d9e33440ffa",
    29: "8ccf0cf0-020f-55c0-8a05-0a3acf2180fb",
    30: "0c18d6fe-f2df-5421-aa07-98317187c71d",
    31: "2bc92008-75af-52dc-87ea-9391956a8d94",
    32: "f15f3220-0f3d-56a0-b9f9-3fb29fad8a85",
    33: "be1d1824-a497-5842-97b5-b8d9ebdb45e3",
}
