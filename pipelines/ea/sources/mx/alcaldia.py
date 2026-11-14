import json
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.mx import ine
from ea.sources.mx.ine import BALLOTS, INVALID, REGISTERED, VALID, VOTES
from ea.store import DATA, connect

SOURCE = "mx.alcaldia"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

IECM = "Instituto Electoral de la Ciudad de México (IECM)"
ARCHIVE = "https://web.archive.org/web/2id_/"
BOOKS = "https://www.iecm.mx/www/sites/biblioteca_electronica/estadisticas_y_estudios_electorales/"
BOOK_PAGE = "https://www.iecm.mx/biblioteca-electronica-del-iecm/estadisticas-y-estudios-electorales/"
WINNERS_2021 = "https://www.iecm.mx/wp-content/uploads/2010/07/Ganadores-Alcaldias-2021.xlsx"
CANDIDATES_2021 = "https://www.iecm.mx/wp-content/uploads/2010/07/CANDIDATURAS_ALCALDIAS_2021.xlsx"
LICENSE = "Public information the IECM publishes for download; no license stated"

BOROUGHS = (
    "Azcapotzalco",
    "Coyoacán",
    "Cuajimalpa de Morelos",
    "Gustavo A. Madero",
    "Iztacalco",
    "Iztapalapa",
    "La Magdalena Contreras",
    "Milpa Alta",
    "Álvaro Obregón",
    "Tláhuac",
    "Tlalpan",
    "Xochimilco",
    "Benito Juárez",
    "Cuauhtémoc",
    "Miguel Hidalgo",
    "Venustiano Carranza",
)
CODES = {name: f"09{index:03d}" for index, name in enumerate(BOROUGHS, 2)}

LOCAL_PARTIES = {
    "RSP": ("Redes Sociales Progresistas", "Social Networks Progressive Party"),
    "FXM": ("Fuerza por México", "Force for Mexico"),
    "ELIGE": ("Equidad, Libertad y Género", "Equity, Freedom, and Gender"),
    "PH": ("Partido Humanista", "Humanist Party"),
}
ALLIANCES = {
    "PT-MORENA-PES": "Candidatura común Juntos Haremos Historia",
    "PAN-PRD-MC": "Candidatura común Por México al Frente",
    "PAN-PRI-PRD": "Candidatura común PAN-PRI-PRD",
    "PRI-PRD": "Candidatura común PRI-PRD",
    "PT-MORENA": "Candidatura común Juntos Hacemos Historia en la Ciudad de México (PT-Morena)",
    "MORENA-PT-PVEM": "Candidatura común Juntos Hacemos Historia en la Ciudad de México (Morena-PT-PVEM)",
}

ELECTIONS: dict[int, dict] = {
    2018: {
        "day": date(2018, 7, 1),
        "book": BOOKS
        + "estadisticas_de_procesos_electorales_locales/2018_estadistica_elecciones_locales_resultados.pdf",
        "title": "Estadística de las elecciones locales 2018. Resultados",
        "pages": (147, 148),
        "winners_page": 146,
        "forces": [
            ["PRI", "PVEM", "NA", "PH", "PT-MORENA-PES", "PAN-PRD-MC"],
            [
                "IND:Vecinos Independientes",
                "IND:Gabriel",
                "IND:Candidatura independiente",
                "IND:Dr. Fox",
                "IND:Democracia al Máximo",
            ],
        ],
        "winners": {
            "Azcapotzalco": "Vidal Llerenas Morales",
            "Coyoacán": "Manuel Negrete Arias",
            "Cuajimalpa de Morelos": "Adrián Rubalcava Suárez",
            "Gustavo A. Madero": "Francisco Chiguil Figueroa",
            "Iztacalco": "Raúl Armando Quintero Martínez",
            "Iztapalapa": "Clara Marina Brugada Molina",
            "La Magdalena Contreras": "Patricia Jimena Ortiz Couturier",
            "Milpa Alta": "José Octavio Rivero Villaseñor",
            "Álvaro Obregón": "Layda Sansores San Román",
            "Tláhuac": "Raymundo Martínez Vite",
            "Tlalpan": "Patricia Elena Aceves Pastrana",
            "Xochimilco": "José Carlos Acosta Ruiz",
            "Benito Juárez": "Santiago Taboada Cortina",
            "Cuauhtémoc": "Néstor Núñez López",
            "Miguel Hidalgo": "Víctor Hugo Romo de Vivar Guerra",
            "Venustiano Carranza": "Julio Cesar Moreno Rivera",
        },
        "note": "",
    },
    2021: {
        "day": date(2021, 6, 6),
        "book": BOOKS
        + "estadisticas_de_procesos_electorales_locales/2021_Estadistica_elecciones_locales_resultados.pdf",
        "title": "Estadística de las elecciones locales 2021. Resultados",
        "pages": (184, 185),
        "winners_page": None,
        "forces": [
            [
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
                "PRI-PRD",
                "MORENA-PT-PVEM",
            ],
            [
                "PT-MORENA",
                "IND:Raúl Ojeda Parada",
                "IND:Héctor Armando Almazán Cravioto",
                "IND:Pablo Raúl Moreno Carrión",
                "IND:Eduardo Contro Rodríguez",
                "IND:Eduardo Virgilio Farah Arelle",
                "IND:Ángel García Pérez",
                "IND:Rafael Guarneros Saldaña",
            ],
        ],
        "note": (
            "The book's counts leave out the 12 casillas whose votes the courts annulled in the borough mayoral "
            "election, with their nominal list."
        ),
    },
    2024: {
        "day": date(2024, 6, 2),
        "book": BOOKS
        + "estadisticas_de_procesos_electorales_locales/estadistica_elecciones_locales_resultados2024.pdf",
        "title": "Estadística de las elecciones locales 2024. Resultados",
        "pages": (192,),
        "winners_page": None,
        "forces": [["PVEM", "PT", "MC", "MORENA", "IND:Por Iztacalco", "MORENA-PT-PVEM", "PAN-PRI-PRD"]],
        "note": "",
    },
}
SYSTEM = (
    "Each of the 16 alcaldías (boroughs) of Mexico City elects its mayor (alcalde or alcaldesa) by plurality for "
    "a three-year term, and may reelect once. The mayor heads a council (concejo) whose concejales are elected on "
    "the same ballot; the concejales are not part of this contest. Votes marked for two or more parties behind "
    "one candidatura común count for that candidate."
)
METHOD = (
    "Votes by alcaldía (demarcación territorial) as printed in the IECM's statistical volume on the {year} local "
    "elections, which sums the district councils' cómputos: {votes:,} ballots in the 16 contests. Each candidacy "
    "is a party, a candidatura común, or an independent; counts by casilla or section are not published in the "
    "volume."
)
UNNAMED = (
    "The volume names only the mayor elected in each alcaldía; the other candidacies are held as the party, "
    "candidatura común, or emblem they ran under."
)


def page_text(path: Path, page: int) -> str:
    return subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def numbers(text: str, participation: bool) -> list[list[int]]:
    rows = []
    for line in text.split("\n"):
        tokens = re.split(r"\s{2,}", line.strip())
        if len(tokens) < 4 or tokens[0] == "(%)":
            continue
        if not re.fullmatch(r"[\d\s.-]+", tokens[0]):
            tokens = tokens[1:]
        if "100" in tokens or any(not re.fullmatch(r"-|\d+(?: \d{3})*|\d+\.\d+", t) for t in tokens):
            continue
        if participation:
            tokens = tokens[:-1]
        rows.append([0 if t == "-" else int(t.replace(" ", "")) for t in tokens])
    return rows


def fold(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(c))
    return " ".join(plain.replace(".", "").split())


def proper(text: str) -> str:
    words = " ".join(text.split()).title().split(" ")
    return " ".join(w.lower() if i and w in {"De", "La", "Del", "Los", "Las", "Y"} else w for i, w in enumerate(words))


def fetch() -> None:
    for config in ELECTIONS.values():
        raw.fetch(SOURCE, ARCHIVE + config["book"], suffix=".pdf")
    raw.fetch(SOURCE, ARCHIVE + WINNERS_2021, suffix=".xlsx")
    raw.fetch(SOURCE, ARCHIVE + CANDIDATES_2021, suffix=".xlsx")


def sheet(url: str) -> list[tuple]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, ARCHIVE + url).path))
    frame = book.load_sheet_by_name(book.sheet_names[0], header_row=None).to_polars()
    return list(frame.iter_rows())


def winners_2024(path: Path) -> dict[str, tuple[str, int]]:
    found = {}
    for page in range(190, 286):
        text = page_text(path, page)
        figure = re.search(r"Figura (\d+)\. Conformación del concejo de la alcaldía", text)
        if figure is None:
            continue
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if re.fullmatch(r"\s*Alcalde(sa)?\s*", line):
                match = re.search(r"\)\s{2,}(\S.*)$", lines[index + 1])
                found[BOROUGHS[int(figure.group(1)) - 1]] = (
                    (match.group(1) if match else lines[index + 1]).strip(),
                    page,
                )
                break
    return found


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, config in ELECTIONS.items():
        path = raw.cached(SOURCE, ARCHIVE + config["book"]).path
        parts = [
            numbers(page_text(path, page), index == len(config["pages"]) - 1)
            for index, page in enumerate(config["pages"])
        ]
        if {len(rows) for rows in parts} != {17}:
            raise ValueError(f"{year}: rows per page {[len(rows) for rows in parts]}")
        forces = [f for group in config["forces"] for f in group]
        records: list[dict[str, Any]] = []
        for index, name in enumerate([*BOROUGHS, "Total"]):
            first = parts[0][index]
            rest = parts[1][index] if len(parts) > 1 else []
            row = first + rest
            sections, casillas, *values = row
            expected = len(forces) + 4
            if len(values) != expected:
                raise ValueError(f"{year} {name}: {len(values)} values, expected {expected}")
            votes = dict(zip(forces, values[: len(forces)], strict=True))
            unregistered, null, total, listed = values[len(forces) :]
            if sum(votes.values()) + unregistered + null != total:
                raise ValueError(f"{year} {name}: votes do not add to {total}")
            records.append(
                {
                    "borough": name,
                    "sections": sections,
                    "casillas": casillas,
                    "votes": votes,
                    "unregistered": unregistered,
                    "null": null,
                    "total": total,
                    "registered": listed,
                }
            )
        body, total = records[:16], records[16]
        for key in ("sections", "casillas", "unregistered", "null", "total", "registered"):
            if sum(r[key] for r in body) != total[key]:
                raise ValueError(f"{year}: {key} does not add to the printed total {total[key]}")
        for force in forces:
            if sum(r["votes"][force] for r in body) != total["votes"][force]:
                raise ValueError(f"{year}: {force} does not add to the printed total")
        winners: dict[str, dict] = {}
        if year == 2018:
            winners = {b: {"name": n, "page": config["winners_page"]} for b, n in config["winners"].items()}
        elif year == 2024:
            found = winners_2024(path)
            if len(found) != 16:
                raise ValueError(f"2024 winners found: {sorted(found)}")
            winners = {b: {"name": n, "page": p} for b, (n, p) in found.items()}
        candidates: dict[str, dict[str, str]] = {}
        if year == 2021:
            for row in sheet(WINNERS_2021)[2:]:
                if row[0] is None:
                    continue
                borough = next(b for b in BOROUGHS if fold(b) == fold(row[5]))
                winners[borough] = {"name": f"{row[2].strip()} {row[1].strip()}", "page": None}
            for row in sheet(CANDIDATES_2021)[2:]:
                if row[0] is None:
                    continue
                borough = next(b for b in BOROUGHS if fold(b) == fold(row[5]))
                party = " ".join(str(row[4]).split())
                if party == "Sin Partido":
                    force = next(
                        f
                        for f in config["forces"][1]
                        if f.startswith("IND:") and fold(row[0]) in fold(f.split(":", 1)[1])
                    )
                else:
                    force = {
                        "Candidatura Común PAN PRI PRD": "PAN-PRI-PRD",
                        "Candidatura Común PRI PRD": "PRI-PRD",
                        "Candidatura Común Juntos hacemos historia Ciudad de México PT MORENA": "PT-MORENA",
                        "Candidatura Común Juntos hacemos historia Ciudad de México MORENA PT PVEM": "MORENA-PT-PVEM",
                        "Equidad Libertad y Género": "ELIGE",
                        "Fuerza por México": "FXM",
                        "Movimiento Ciudadano": "MC",
                        "Partido Acción Nacional": "PAN",
                        "Partido Encuentro Solidario": "PES",
                        "Partido Revolucionario Institucional": "PRI",
                        "Partido Verde Ecologista de México": "PVEM",
                        "Partido de la Revolución Democrática": "PRD",
                        "Redes Sociales Progresistas": "RSP",
                    }[party]
                slot = candidates.setdefault(borough, {})
                if force in slot:
                    raise ValueError(f"duplicate candidacy {borough} {force}")
                slot[force] = proper(f"{row[2]} {row[0]} {row[1]}")
            for borough, winner in winners.items():
                leader = max(
                    next(r for r in body if r["borough"] == borough)["votes"].items(), key=lambda item: item[1]
                )[0]
                seen, listed = fold(candidates[borough][leader]).split(), fold(winner["name"]).split()
                if (seen[0], seen[-1]) != (listed[0], listed[-1]):
                    raise ValueError(f"2021 {borough}: {winner['name']} is not the leading candidacy {leader}")
        if len(winners) != 16:
            raise ValueError(f"{year}: {len(winners)} winners")
        for record in body:
            leader = max(record["votes"], key=lambda f: record["votes"][f])
            record["leader"] = leader
        (PARSED / f"{year}.json").write_text(
            json.dumps({"rows": records, "winners": winners, "candidates": candidates}, ensure_ascii=False, indent=1)
        )
        print(year, "votes", total["total"], {r["borough"]: r["leader"] for r in body})


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mexico City borough mayor (alcaldía) elections of 2018, 2021, and 2024 by alcaldía from the "
            "IECM's statistical volumes, with the mayors elected as declared",
            polity="MX",
        )
        offices: set[UUID] = set()
        for scheme in ("mx.entidad", "mx.municipio"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.alcaldia")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        cmx = cs.polity("MX-CMX")
        system = cs.key("mx.electoral_system", "alcaldia")
        if system in cs.minted:
            cs.add("electoral_system", id=system, polity_id=mexico, rules={"formula": "fptp"}, rules_version=1)
            name(system, "Mexico City borough mayor plurality system")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties: dict[str, UUID] = {}

        def party(abbrev: str) -> UUID:
            if abbrev not in parties:
                if abbrev in ine.PARTIES:
                    item, native, english = ine.PARTIES[abbrev]
                    parties[abbrev] = cs.key("mx.party", item)
                    owner = mexico
                else:
                    item = None
                    native, english = LOCAL_PARTIES[abbrev]
                    parties[abbrev] = cs.key("mx.party", f"MX-CMX:{abbrev}")
                    owner = cmx
                if parties[abbrev] in cs.minted:
                    cs.add("party", id=parties[abbrev], kind="party", polity_id=owner)
                    name(parties[abbrev], native, "es")
                    name(parties[abbrev], english, "en", "english")
                    name(parties[abbrev], abbrev, "es", "abbreviation")
                    if item is not None:
                        cs.add("external_id", entity_id=parties[abbrev], scheme="wikidata", value=item)
            return parties[abbrev]

        persons: dict[str, UUID] = {}

        def person(year: int, full: str) -> UUID:
            key = f"{year}:alcaldia:{full}"
            if key not in persons:
                persons[key] = cs.key("mx.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], full)
            return persons[key]

        names = dict(
            conn.execute(
                "select n.kind::text, n.text from area a join area_code c on c.area_id = a.id "
                "join name n on n.entity_id = a.id where a.scheme_code = 'mx.entidad' and c.code = '09'"
            ).fetchall()
        )
        spanish, english = names["official"], names.get("english") or names["official"]
        state_unit = cs.unit("mx.entidad", "09")
        unit_rows: dict[UUID, tuple[str, UUID, UUID]] = {}
        frames: list[pl.DataFrame] = []

        for year, config in ELECTIONS.items():
            day = config["day"]
            data = json.loads((PARSED / f"{year}.json").read_text())
            source = cs.source(IECM, config["title"])
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=IECM,
                title=config["title"],
                url=BOOK_PAGE,
                license=LICENSE,
            )
            cs.artifact(raw.cached(SOURCE, ARCHIVE + config["book"]), source)
            winner_source = source
            if year == 2021:
                title = "Listado de Alcaldes(as) ganadores(as) por partido político, 2021-2024"
                winner_source = cs.source(IECM, title)
                cs.add(
                    "source",
                    id=winner_source,
                    kind="official_body",
                    publisher=IECM,
                    title=title,
                    url=WINNERS_2021,
                    license=LICENSE,
                )
                cs.artifact(raw.cached(SOURCE, ARCHIVE + WINNERS_2021), winner_source)
                listing = "Listado de Candidaturas a Titular de Alcaldías, 2021"
                candidate_source = cs.source(IECM, listing)
                cs.add(
                    "source",
                    id=candidate_source,
                    kind="official_body",
                    publisher=IECM,
                    title=listing,
                    url=CANDIDATES_2021,
                    license=LICENSE,
                )
                cs.artifact(raw.cached(SOURCE, ARCHIVE + CANDIDATES_2021), candidate_source)

            election = cs.key("mx.election", f"{day}:CMX")
            path = f"mx/cmx/{day}-general"
            if election in cs.minted:
                cs.add(
                    "election", id=election, polity_id=cmx, kind="general", date_from=day, date_to=day, status="held"
                )
                name(election, f"{year} {english} state election")
                name(election, f"Elecciones locales de {spanish} de {year}", "es", "native")
                cs.add("slug", path=path, entity_id=election)

            alliances: dict[str, UUID] = {}

            def alliance(force: str) -> UUID:
                if force not in alliances:
                    alliances[force] = cs.key("mx.alliance", f"{day}:CMX:alcaldia:{force}")
                    if alliances[force] in cs.minted:
                        cs.add("alliance", id=alliances[force], kind="electoral_alliance", election_id=election)
                        name(alliances[force], ALLIANCES[force], "es")
                        for member in force.split("-"):
                            cs.add("alliance_member", alliance_id=alliances[force], party_id=party(member))
                return alliances[force]

            total_votes = sum(r["total"] for r in data["rows"][:16])
            for record in data["rows"][:16]:
                borough = record["borough"]
                code = CODES[borough]
                slug = re.sub(r"[^a-z0-9]+", "-", fold(borough)).strip("-")
                office = cs.key("mx.office", f"alcalde:{code}")
                if office in cs.minted and office not in offices:
                    offices.add(office)
                    cs.add("office", id=office, polity_id=cmx, kind="mayor")
                    name(office, f"Mayor of {borough}")
                    name(office, f"Alcaldía de {borough}", "es", "native")
                contest = cs.key("mx.contest", f"{day}:CMX:alcaldia:{code}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=cs.area("mx.municipio", code),
                    magnitude=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {borough} borough mayoral election")
                name(contest, f"Elección de la Alcaldía de {borough} de {year}", "es", "native")
                cs.add("slug", path=f"{path}/alcaldia-{slug}", entity_id=contest)

                options: dict[str, UUID] = {}
                winner = data["winners"][borough]
                leader = record["leader"]
                candidates = data["candidates"].get(borough, {})
                position = 0
                for force, count in record["votes"].items():
                    if count == 0:
                        continue
                    position += 1
                    option = options[force] = cs.key("mx.option", f"{day}:CMX:alcaldia:{code}:{force}")
                    independent = force.startswith("IND:")
                    members = [] if independent else force.split("-")
                    label = force.split(":", 1)[1] if independent else candidates.get(force)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party(force) if len(members) == 1 else None,
                        alliance_id=alliance(force) if len(members) > 1 else None,
                        ballot_position=position,
                        ballot_label=label if independent else "-".join(members),
                    )
                    named = winner["name"] if force == leader else candidates.get(force)
                    if named is None:
                        continue
                    candidacy = cs.key("mx.candidacy", f"{day}:CMX:alcaldia:{code}:{force}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person(year, named),
                        option_id=option,
                        role="head",
                        ballot_name=named,
                        is_independent=independent,
                    )
                    for member in members:
                        cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party(member), kind="nominated")
                    if year == 2021:
                        cs.add(
                            "citation",
                            entity_id=candidacy,
                            source_id=candidate_source,
                            locator={"sheet": "ALCALDIAS_2021"},
                        )
                write_in = cs.key("mx.option", f"{day}:CMX:alcaldia:{code}:unregistered")
                cs.add(
                    "contest_option",
                    id=write_in,
                    contest_id=contest,
                    kind="write_in",
                    ballot_label="Candidaturas no registradas",
                )
                options["UNREGISTERED"] = write_in

                result_set = cs.key("mx.result_set", f"{day}:CMX:alcaldia:{code}")
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
                method = METHOD.format(year=year, votes=total_votes) + " " + UNNAMED * (year != 2021)
                cs.add("note", entity_id=result_set, kind="methodology", body_md=method.strip())
                if config["note"]:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=config["note"])
                locator = (
                    {"page": winner["page"], "section": "Alcaldes y alcaldesas electos"}
                    if winner["page"]
                    else {"sheet": "Gana Alcaldias_DemT"}
                )
                cs.add("outcome", result_set_id=result_set, option_id=options[leader], kind="elected", seats=1)
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=winner_source,
                    locator=locator,
                )

                unit = cs.unit("mx.municipio", code)
                unit_rows[unit] = (code, state_unit, cs.area("mx.municipio", code))
                values = [
                    (REGISTERED, None, record["registered"]),
                    (BALLOTS, None, record["total"]),
                    (VALID, None, record["total"] - record["null"]),
                    (INVALID, None, record["null"]),
                    (VOTES, write_in, record["unregistered"]),
                    *((VOTES, options[force], count) for force, count in record["votes"].items() if count),
                ]
                frames.append(
                    pl.DataFrame(
                        {
                            "result_set_id": [str(result_set)] * len(values),
                            "unit_id": [str(unit)] * len(values),
                            "option_id": [None if o is None else str(o) for _, o, _ in values],
                            "measure": [m for m, _, _ in values],
                            "value": [v for _, _, v in values],
                        },
                        schema_overrides={"option_id": pl.String},
                    )
                )
            print(year, "votes", total_votes)

        minted = [
            {
                "id": str(unit),
                "polity_id": str(mexico),
                "scheme_code": "mx.municipio",
                "code": code,
                "parent_id": str(parent),
                "area_relation": "equals",
                "area_id": str(area),
                "kind": "standard",
            }
            for unit, (code, parent, area) in unit_rows.items()
            if unit in cs.minted
        ]
        if minted:
            cs.frame("reporting_unit", pl.DataFrame(minted))
        cs.frame(
            "result_value",
            pl.concat(frames).with_columns(
                measure=pl.col("measure").cast(pl.Int16),
                value=pl.col("value").cast(pl.Int64),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
