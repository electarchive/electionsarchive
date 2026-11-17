import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "sv.tse"
BASE = "https://transparencia.tse.gob.sv/2014/escrutiniofinal_1ray2davuelta"
EL_SALVADOR = str(uuid5(package.NAMESPACE, "polity\x1fSV"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fSLV"))
VOTES, VALID, INVALID, OBJECTED = 1, 4, 5, 15
ROUNDS = {1: ("pres1", date(2014, 2, 2)), 2: ("pres2", date(2014, 3, 9))}
PARTIES = ("FMLN", "UNIDAD", "FPS", "PSP", "ARENA")
PARTY_NAMES = {
    "FMLN": "Frente Farabundo Martí para la Liberación Nacional",
    "UNIDAD": "Unidad",
    "FPS": "Frente Parlamentario Salvadoreño",
    "PSP": "Partido Salvadoreño Progresista",
    "ARENA": "Alianza Republicana Nacionalista",
}
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fpresident"))
PRESIDENCY = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1foffice\x1fpresident"))
TSE_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1ftse"))
METHOD = (
    "Final scrutiny (escrutinio final) of the 2014 presidential election as published by the Tribunal Supremo "
    "Electoral, one row per vote-receiving board (junta receptora de votos, JRV) in each voting center. Votes are "
    "valid votes for each party's ticket, nulos are invalid ballots, and impugnados are ballots objected to. The "
    "source also lists abstentions, missing, surplus and unused ballot papers per board; they are not stored."
)


def page(round_number: int, name: str) -> str:
    return f"{BASE}/{ROUNDS[round_number][0]}/{name}.html"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def links(content: str) -> list[tuple[str, str]]:
    body = content[content.find('id="tabla"') :]
    return [
        (h, html.unescape(t).strip())
        for h, t in re.findall(r"<a href='((?:dep|mun|cen)\d+\.html)'[^>]*>([^<]*)</a>", body)
    ]


def _crawl(round_number: int, name: str) -> list[str]:
    for attempt in range(8):
        try:
            artifact = raw.fetch(SOURCE, page(round_number, name), suffix=".html")
            break
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429 or attempt == 7:
                raise
            time.sleep(15 * (attempt + 1))
    return [h.removesuffix(".html") for h, _ in links(artifact.path.read_text("utf-8"))]


def fetch() -> None:
    from ea.sources.sv import assembly, concejos, parlacen

    assembly.fetch()
    concejos.fetch()
    parlacen.fetch()
    for round_number in ROUNDS:
        _crawl(round_number, "pais")
        level = ["dep%02d" % n for n in range(1, 16)]
        while level:
            with ThreadPoolExecutor(2) as pool:
                children = list(pool.map(lambda n, r=round_number: _crawl(r, n), level))
            level = sorted({c for group in children for c in group if not c.startswith("dep")})


def packages() -> list[str]:
    from ea.sources.sv import assembly, concejos, parlacen

    return [
        "sv.tse/geo",
        "sv.tse/registry",
        *(f"sv.tse/pres-{day}" for _, day in ROUNDS.values()),
        *assembly.packages(),
        *concejos.packages(),
        *parlacen.packages(),
    ]


def build(name: str) -> Package:
    from ea.sources.sv import assembly, concejos, parlacen

    if name in parlacen.packages():
        return parlacen.build(name)
    if name in assembly.packages():
        return assembly.build(name)
    if name in concejos.packages():
        return concejos.build(name)
    return {"sv.tse/geo": geo, "sv.tse/registry": registry}.get(name, lambda n=name: president(n))()


def parse() -> None:
    for name in packages():
        build(name).write()


def emit() -> None:
    package.run("load", packages())


def text(round_number: int, name: str) -> str:
    return raw.cached(SOURCE, page(round_number, name)).path.read_text("utf-8")


def number(value: str) -> int:
    return int(re.sub(r"[^\d]", "", value) or 0)


def rows(content: str) -> list[tuple[str, str | None, list[int]]]:
    """Each table row as (label, link, numbers); the board-count cell of an area row is flattened to its three numbers."""
    out: list[tuple[str, str | None, list[int]]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", content[content.find('id="tabla"') :], re.DOTALL):
        if "<th" in row:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 2:
            continue
        link = re.search(r"href='([^']+\.html)'", cells[0])
        label = html.unescape(re.sub(r"<[^>]+>", " ", cells[0]))
        label = re.sub(r"\s+", " ", label.replace("[ACTA]", "")).strip()
        values = [number(re.sub(r"<[^>]+>", " ", c)) for c in cells[2:]] if "PROCESADA" in cells[1] else []
        if not values:
            values = [number(n) for n in re.findall(r">\s*([\d,]+)\s*<", cells[1])[:3]]
            values += [number(re.sub(r"<[^>]+>", "", c)) for c in cells[2:]]
        out.append((label, link.group(1) if link else None, values))
    return out


def columns(content: str) -> list[str]:
    heads = [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip() for h in re.findall(r"<th.*?</th>", content, re.DOTALL)
    ]
    return heads[heads.index("TOTALES PROCESADAS NO PROCESADAS") + 1 : heads.index("IMPUGNADOS")]


def tree(
    round_number: int, with_centers: bool = True
) -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Departments, municipalities, and voting centers as {code: (name, parent code)}."""
    departments: dict[str, str] = {}
    municipalities: dict[str, tuple[str, str]] = {}
    centers: dict[str, tuple[str, str]] = {}
    for label, link, _ in rows(text(round_number, "pais")):
        if link and link.startswith("dep"):
            code = link.removesuffix(".html")
            departments[code] = label
            for label, child, _ in rows(text(round_number, code)):
                if child and child.startswith("mun"):
                    municipalities[child.removesuffix(".html")] = (label, code)
    for code in municipalities if with_centers else ():
        for label, child, _ in rows(text(round_number, code)):
            if child and child.startswith("cen"):
                centers[child.removesuffix(".html")] = (label, code)
    return departments, municipalities, centers


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def geo() -> Package:
    p = Package(
        "sv.tse/geo",
        SOURCE,
        "El Salvador's 14 departments and 262 municipalities as the Tribunal Supremo Electoral lists them in its 2014 "
        "results, without boundaries",
    )
    p.external |= {EL_SALVADOR, TERRITORY}
    departments, municipalities, _ = tree(1, with_centers=False)
    for code, rank, kind, noun in (
        ("sv.departamento", 1, "administrative", "department"),
        ("sv.municipio", 2, "administrative", "municipality"),
        ("sv.centro", 3, "precinct", "voting center"),
        ("sv.jrv", 4, "precinct", "voting board"),
    ):
        p.add("scheme", code=code, polity_id=EL_SALVADOR, kind=kind, rank=rank, noun=noun)
    for code, label in departments.items():
        id = area("sv.departamento", code)
        p.add("area", id=id, scheme_code="sv.departamento")
        p.add("area_code", area_id=id, code=code.removeprefix("dep"))
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, label.title(), "es", "official")
    for code, (label, dep) in municipalities.items():
        id = area("sv.municipio", code)
        p.add("area", id=id, scheme_code="sv.municipio")
        p.add("area_code", area_id=id, code=code.removeprefix("mun"))
        p.add("area_parent", area_id=id, parent_id=area("sv.departamento", dep))
        p.name(id, label.title(), "es", "official")
    return p


def registry() -> Package:
    p = Package(
        "sv.tse/registry",
        SOURCE,
        "The parties of the 2014 Salvadoran presidential election, the presidency, and its electoral system",
        depends=["sv.tse/geo"],
    )
    p.external |= {EL_SALVADOR, TERRITORY}
    p.add(
        "source",
        id=TSE_SOURCE,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de El Salvador",
        title="Escrutinio final de las elecciones presidenciales 2014",
        url="https://transparencia.tse.gob.sv/2014/escrutiniofinal_1ray2davuelta/pres1/pais.html",
        license="Official public results of the Tribunal Supremo Electoral",
        license_url="https://transparencia.tse.gob.sv/",
        attribution="Tribunal Supremo Electoral de El Salvador",
    )
    for round_number in ROUNDS:
        departments, municipalities, centers = tree(round_number)
        names = ["pais", *departments, *municipalities, *centers]
        for name in names:
            p.artifact(raw.cached(SOURCE, page(round_number, name)), TSE_SOURCE)
    p.add("office", id=PRESIDENCY, polity_id=EL_SALVADOR, kind="head_of_state")
    p.name(PRESIDENCY, "President of El Salvador", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República de El Salvador", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=EL_SALVADOR,
        valid_from=date(1983, 12, 20),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "The president is elected directly for a single term. A ticket that wins more than half of the valid "
            "votes in the first round is elected; otherwise the two leading tickets go to a second round."
        ),
        lang="en",
    )
    for code in PARTIES:
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=EL_SALVADOR)
        p.add("external_id", entity_id=id, scheme="sv.tse.party", value=code)
        p.name(id, PARTY_NAMES[code], "es", "official")
        p.name(id, code, "es", "abbreviation", False)
    return p


def president(name: str) -> Package:
    day = date.fromisoformat(name.removeprefix("sv.tse/pres-"))
    round_number = 1 if day == ROUNDS[1][1] else 2
    p = Package(
        name,
        SOURCE,
        f"The {'second' if round_number == 2 else 'first'} round of the {day.year} Salvadoran presidential election, "
        "by voting board from the Tribunal Supremo Electoral",
        depends=["sv.tse/geo", "sv.tse/registry"] + (["sv.tse/pres-2014-02-02"] if round_number == 2 else []),
    )
    p.external |= {EL_SALVADOR, TERRITORY}
    departments, municipalities, centers = tree(round_number)
    for page_name in ("pais", *departments, *municipalities, *centers):
        p.uses(raw.cached(SOURCE, page(round_number, page_name)))
    election = key("election", day)
    runoff = round_number == 2
    p.add(
        "election",
        id=election,
        polity_id=EL_SALVADOR,
        kind="runoff" if runoff else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    path = f"sv/{day}-presidential"
    p.add("slug", path=path, entity_id=election, is_current=True)
    title = f"{day.year} Salvadoran presidential election" + (", second round" if runoff else "")
    p.name(election, title, "en", "official")
    p.name(
        election, f"Elección presidencial {day.year}" + (", segunda vuelta" if runoff else ""), "es", "native", False
    )
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=round_number,
        previous_round_id=key("contest", ROUNDS[1][1]) if runoff else None,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, title.replace("election", "election, first round") if not runoff else title, "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=TSE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    heads = columns(text(round_number, "pais"))
    width = len(heads)
    national = rows(text(round_number, "pais"))[0][2]
    totals = dict(zip(heads, national[3 : 3 + width], strict=True))
    running = [party for party in heads if totals[party] > 0]
    ranked = sorted(running, key=lambda party: -totals[party])
    options: dict[str, str] = {}
    for position, party in enumerate(heads, 1):
        if party not in running:
            continue
        option = options[party] = key("option", day, party)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            party_id=key("party", party),
            ballot_position=position,
            ballot_label=party,
        )
        if runoff:
            kind = "elected" if party == ranked[0] else "lost"
        else:
            kind = "advanced" if party in ranked[:2] else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=totals[party])
    p.add("declared", result_set_id=result_set, option_id=None, measure=OBJECTED, value=national[3 + width])
    p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=national[4 + width])
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=sum(totals.values()))
    facts: list[dict[str, object]] = []

    def value(unit: str, option: str | None, measure: int, amount: int) -> None:
        facts.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": 0,
                "value": amount,
            }
        )

    for code, (label, municipality) in centers.items():
        unit = key("unit", day, code)
        place = area("sv.municipio", municipality)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=EL_SALVADOR,
            scheme_code="sv.centro",
            code=f"{day}:{code.removeprefix('cen')}",
            area_relation="within",
            area_id=place,
            kind="aggregate",
        )
        p.name(unit, label, "es", "official")
        for board, _, numbers in rows(text(round_number, code))[1:]:
            board_unit = key("unit", day, code, board)
            p.add(
                "reporting_unit",
                id=board_unit,
                polity_id=EL_SALVADOR,
                scheme_code="sv.jrv",
                code=f"{day}:{code.removeprefix('cen')}:{board}",
                parent_id=unit,
                area_relation="within",
                area_id=place,
                kind="standard",
            )
            if len(numbers) < width + 2:
                continue
            votes = dict(zip(heads, numbers[:width], strict=True))
            for party, option in options.items():
                value(board_unit, option, VOTES, votes[party])
            value(board_unit, None, VALID, sum(votes[party] for party in options))
            value(board_unit, None, OBJECTED, numbers[width])
            value(board_unit, None, INVALID, numbers[width + 1])
    p.frame("result_value", pl.DataFrame(facts))
    return p
