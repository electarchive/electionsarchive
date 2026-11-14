from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import display
from ea.sources.es import infoelectoral
from ea.sources.es.municipales import fold, section_of
from ea.store import DATA, connect

SOURCE = "es.cabildos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KIND = "06"
THRESHOLD = 0.05
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales: elecciones a cabildos insulares (ficheros de datos)"
DAYS = {
    1987: date(1987, 6, 10),
    1991: date(1991, 5, 26),
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
SECTIONS_FROM = 2011
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
SYSTEM = (
    "Each of the seven Canary Islands elects its cabildo (island council) as a single constituency, on the day of "
    "the municipal elections and by the procedure for municipal councils: closed lists, seats shared by the "
    "highest-average (D'Hondt) method among the lists with at least 5 percent of the valid votes, blank ballots "
    "included, and filled in list order (Ley Orgánica 5/1985, articles 180 and 201). The council's size follows the "
    "island's population."
)
ISLAND_METHOD = (
    "Registered voters, blank ballots, null ballots, list votes, and seats for the island from the Ministry of the "
    "Interior's data files (files 07 and 08). Valid votes here are the votes to candidacies; blank ballots, which the "
    "ministry counts as valid, are stored apart. The elected councillors are the candidates the ministry flags as "
    "elected (file 04)."
)
MESA_METHOD = (
    "List votes, registered voters, blank ballots, and null ballots by polling station (mesa) from the Ministry of "
    "the Interior's data files (files 09 and 10). They add up to the island's official totals exactly. {where}"
)
MESA_SECTION = (
    "A mesa sits on its census section where the section code is in the 2025 INE section map, otherwise on its "
    "municipality."
)
MESA_MUNICIPALITY = "Each mesa sits on its municipality, since the INE section map held is the 2025 one."
MUNICIPALITY_METHOD = (
    "List votes by municipality from the Ministry of the Interior's data file 06, which add up to the island's "
    "official list votes exactly. The ministry publishes no municipality totals of registered voters, blank, or null "
    "ballots for this election, and its mesa files add up to 390 more list votes than the municipality's in Mogán and "
    "318 more in Santa Brígida, so the municipality is the finest unit kept."
)
NO_CANDIDATES = (
    "The ministry's files for this election hold no candidate lists, so the seats each list won are stored without "
    "the names of the councillors elected."
)
Row = dict[str, str | int]


def fetch() -> None:
    infoelectoral.fetch()


def _island(row: Row) -> str:
    return f"{row['province']}{row['district']}"


def _dhondt(votes: dict[int, int], valid: int, seats: int) -> Counter[int]:
    eligible = {code: count for code, count in votes.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def _processes() -> list[str]:
    return [
        process
        for process in infoelectoral.processes(KIND)
        if f"{infoelectoral.APLIEXTR}{KIND}{process}_MESA.zip" in raw.keys(infoelectoral.SOURCE)
    ]


def _islands_of(processes: list[str]) -> dict[tuple[str | int, str | int], str]:
    return {
        (row["province"], row["municipality"]): _island(row)
        for process in processes
        for row in infoelectoral.records(KIND, process, "MESA", "05")
        if row["municipal_district"] == "99"
    }


def _ties(votes: list[Row], islands: list[Row], units: list[Row], tallies: list[Row]) -> bool:
    lists: Counter[tuple[str | int, str | int]] = Counter()
    for row in tallies:
        lists[row["island"], row["list"]] += int(row["votes"])
    totals: dict[str | int, Counter[str]] = defaultdict(Counter)
    for row in units:
        for field in ("blank", "invalid", "list_votes"):
            totals[row["island"]][field] += int(row[field])
    return (
        bool(tallies)
        and all(lists[row["island"], row["list"]] == row["votes"] for row in votes)
        and all(
            not units or totals[row["island"]][field] == row[field]
            for row in islands
            for field in ("blank", "invalid", "list_votes")
        )
    )


def _process(process: str, islands_of: dict[tuple[str | int, str | int], str]) -> dict[str, list[Row]]:
    year, month = int(process[:4]), int(process[4:])
    stamp: Row = {"year": year, "month": month}
    read = lambda number: infoelectoral.records(KIND, process, "MESA", number)
    lists = {row["list"]: row for row in read("03")}
    islands = [{**stamp, "island": _island(row), **row} for row in read("07") if row["district"] != "9"]
    names = {row["island"] for row in islands}
    votes = [
        {
            **stamp,
            "island": _island(row),
            "list": row["list"],
            "abbrev": lists[row["list"]]["abbrev"],
            "name": lists[row["list"]]["name"],
            "votes": row["votes"],
            "seats": row["seats"],
        }
        for row in read("08")
        if _island(row) in names
    ]
    candidates = [{**stamp, "island": _island(row), **row} for row in read("04")]
    located = lambda rows: [
        {**stamp, "island": islands_of[row["province"], row["municipality"]], **row} for row in rows
    ]
    municipalities = [row for row in located(read("05")) if row["municipal_district"] == "99"]
    municipality_votes = [row for row in located(read("06")) if row["municipal_district"] == "99"]
    mesas, mesa_votes = located(read("09")), located(read("10"))
    _check(process, islands, votes, candidates)
    election: Row = {
        **stamp,
        "municipalities_tie": _ties(votes, islands, municipalities, municipality_votes),
        "mesas_tie": _ties(votes, islands, mesas, mesa_votes),
    }
    return {
        "elections": [election],
        "islands": islands,
        "lists": votes,
        "candidates": candidates,
        "municipalities": municipalities,
        "municipality_votes": municipality_votes,
        "mesas": mesas,
        "mesa_votes": mesa_votes,
    }


def _check(process: str, islands: list[Row], votes: list[Row], candidates: list[Row]) -> None:
    problems = []
    elected = Counter((row["island"], row["list"]) for row in candidates if row["elected"] == "S")
    for island in islands:
        key, seats = island["island"], int(island["seats"])
        rows = [row for row in votes if row["island"] == key]
        if sum(int(row["seats"]) for row in rows) != seats:
            problems.append(f"{key}: list seats do not sum to {seats}")
        if sum(int(row["votes"]) for row in rows) != island["list_votes"]:
            problems.append(f"{key}: list votes do not sum to {island['list_votes']}")
        problems += [
            f"{key} list {row['list']}: {elected[key, row['list']]} elected against {row['seats']}"
            for row in rows
            if candidates and elected[key, row["list"]] != row["seats"]
        ]
        won = _dhondt(
            {int(row["list"]): int(row["votes"]) for row in rows},
            int(island["list_votes"]) + int(island["blank"]),
            seats,
        )
        if any(won[int(row["list"])] != row["seats"] for row in rows):
            print(f"{process} {key}: D'Hondt with a 5% threshold does not give the declared seats")
    if problems:
        raise ValueError(f"{process}: " + "; ".join(problems[:20]))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    processes = _processes()
    islands_of = _islands_of(processes)
    tables: dict[str, list[Row]] = defaultdict(list)
    for process in processes:
        for name, rows in _process(process, islands_of).items():
            tables[name] += rows
    for name, rows in tables.items():
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {len(rows)} rows")
    print(pl.DataFrame(tables["elections"]))


def emit() -> Path:
    read = lambda name: pl.read_parquet(PARSED / f"{name}.parquet")
    elections = {row["year"]: row for row in read("elections").to_dicts()}
    group = lambda name: {
        key: frame.to_dicts() for key, frame in read(name).partition_by("year", "island", as_dict=True).items()
    }
    islands = {(row["year"], row["island"]): row for row in read("islands").to_dicts()}
    lists, candidates = group("lists"), group("candidates")
    municipality_votes, mesas, mesa_votes = group("municipality_votes"), group("mesas"), group("mesa_votes")
    years = sorted(DAYS)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Canary Islands cabildo (island council) elections of 1987 to 2023: votes and seats by list for each "
                "island, the councillors declared elected, and results by mesa or municipality where they add up to "
                "the island totals, from the Ministry of the Interior's data files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, canarias = cs.polity("ES"), cs.polity("ES-CN")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=infoelectoral.PAGE,
            license="Reuse under Ley 37/2007 with the source cited",
            attribution="Origen de los datos: Ministerio del Interior.",
        )
        for year in years:
            process = f"{year}{DAYS[year].month:02d}"
            cs.artifact(raw.cached(infoelectoral.SOURCE, f"{infoelectoral.APLIEXTR}{KIND}{process}_MESA.zip"), source)

        system = cs.key("es.electoral_system", "cabildo-closed-list")
        if system in cs.minted:
            cs.add(
                "electoral_system",
                id=system,
                polity_id=spain,
                rules={
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD * 100},
                },
                rules_version=1,
            )
            name(system, "Canarian cabildo closed-list D'Hondt system")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        lookup = lambda scheme: dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = %s",
                (scheme,),
            ).fetchall()
        )
        island_areas, municipality_areas, sections = lookup("es.isla"), lookup("es.municipio"), lookup("es.seccion")
        taken = {row[0] for row in conn.execute("select path from slug where path like 'es/canarias/%%'").fetchall()}
        party_names: dict[str, set[UUID]] = defaultdict(set)
        linked = {
            row[0]
            for row in conn.execute(
                """select p.id from party p join external_id x on x.entity_id = p.id and x.scheme = 'wikidata'
                   where p.polity_id = %s""",
                (spain,),
            ).fetchall()
        }
        for text, party in conn.execute(
            """select n.text, p.id from party p join name n on n.entity_id = p.id
               where p.polity_id = %s and n.kind <> 'abbreviation'
               and not exists (select from entity_merge m where m.entity_id = p.id)""",
            (spain,),
        ).fetchall():
            party_names[fold(text)].add(party)
        parties: dict[str, UUID] = {}

        def party_for(entry: dict[str, Any], island: str) -> UUID:
            label = str(entry["name"])
            folded = fold(label)
            local = folded.startswith(("AGRUPACION", "AE ")) or "ELECTORES" in folded
            key = f"i:{island}:{folded}" if local else f"n:{folded}"
            if key in parties:
                return parties[key]
            matches = party_names.get(folded, set())
            if len(matches) > 1:
                matches &= linked
            if not local and len(matches) == 1:
                parties[key] = next(iter(matches))
                return parties[key]
            party = parties[key] = cs.key("es.party", key)
            if party in cs.minted:
                cs.add("party", id=party, kind="independent_group" if local else "party", polity_id=spain)
                name(party, label, "es")
                if abbreviation := str(entry["abbrev"]):
                    name(party, abbreviation, "es", "abbreviation")
            return party

        unit_rows: dict[UUID, dict[str, str | None]] = {}

        def unit_for(scheme: str, code: str, parent: UUID, area: UUID | None, relation: str) -> UUID:
            unit = cs.unit(scheme, code)
            if unit in cs.minted and unit not in unit_rows:
                unit_rows[unit] = {
                    "id": str(unit),
                    "polity_id": str(spain),
                    "scheme_code": scheme,
                    "code": code,
                    "parent_id": str(parent),
                    "area_relation": "unlocated" if area is None else relation,
                    "area_id": None if area is None else str(area),
                    "kind": "standard",
                }
            return unit

        values: list[dict[str, object]] = []

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, count: object) -> None:
            values.append(
                {
                    "result_set_id": str(result_set),
                    "unit_id": str(unit),
                    "option_id": None if option is None else str(option),
                    "measure": measure,
                    "value": count,
                }
            )

        def tally(result_set: UUID, unit: UUID, row: dict[str, Any]) -> None:
            for measure, count in (
                (REGISTERED, row["census"]),
                (BALLOTS, row["list_votes"] + row["blank"] + row["invalid"]),
                (VALID, row["list_votes"]),
                (INVALID, row["invalid"]),
                (BLANK, row["blank"]),
            ):
                if measure != REGISTERED or count:
                    value(result_set, unit, None, measure, count)

        polities: dict[str, tuple[UUID, UUID, str, str]] = {}
        for code in sorted({island for _, island in islands}):
            label = islands[years[-1], code]["name"]
            path = f"es/canarias/{slug(label)}"
            polity = cs.key("es.polity", f"isla:{code}")
            body = cs.key("es.body", f"cabildo:{code}")
            if polity in cs.minted:
                if path in taken:
                    raise ValueError(f"{path} is taken")
                cs.add("polity", id=polity, kind="county", iso_code=None)
                name(polity, label, "es")
                cs.add("polity_parent", polity_id=polity, parent_id=canarias)
                cs.add("polity_territory", polity_id=polity, area_id=island_areas[code])
                cs.add("slug", path=path, entity_id=polity)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
                name(body, f"Cabildo of {label}")
                name(body, f"Cabildo de {label}", "es", "native")
            polities[code] = polity, body, label, path

        persons = 0
        for year in years:
            day, election_row = DAYS[year], elections[year]
            stamp = f"{year}{day.month:02d}"
            following = next((DAYS[other] for other in years if other > year), None)
            umbrella = cs.key("es.election", f"{day}:local")
            if umbrella in cs.minted:
                cs.add(
                    "election", id=umbrella, polity_id=spain, kind="local", date_from=day, date_to=day, status="held"
                )
                name(umbrella, f"{year} Spanish municipal elections")
                name(umbrella, f"Elecciones municipales de {year}", "es", "native")
                cs.add("slug", path=f"es/{day}-local", entity_id=umbrella)
            for code, (polity, body, label, path) in polities.items():
                row = islands[year, code]
                seats = int(row["seats"])
                cs.add("body_size", body_id=body, seats=seats, valid_from=day, valid_to=following)
                election = cs.key("es.election", f"{day}:cabildo:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=umbrella,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} {label} cabildo election")
                name(election, f"Elecciones al Cabildo de {label} de {year}", "es", "native")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                contest = cs.key(f"{SOURCE}.contest", f"{day}:{code}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    office_id=None,
                    district_area_id=island_areas[code],
                    magnitude=seats,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {label} cabildo election")
                cs.add("slug", path=f"{path}/{day}-local/cabildo", entity_id=contest)
                stage = "official" if row["official"] == "S" else "preliminary"
                result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{code}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage=stage,
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=ISLAND_METHOD)
                island_unit = unit_for("es.isla", code, cs.unit("es.provincia", code[:2]), island_areas[code], "equals")
                tally(result_set, island_unit, row)
                entries = sorted(lists[year, code], key=lambda entry: -int(entry["votes"]))
                options: dict[int, UUID] = {}
                for entry in entries:
                    number = int(entry["list"])
                    option = options[number] = cs.key(f"{SOURCE}.option", f"{day}:{code}:{number}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_for(entry, code),
                        ballot_label=entry["name"],
                    )
                    value(result_set, island_unit, option, VOTES, entry["votes"])
                    if entry["seats"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry["seats"])
                members = candidates.get((year, code), [])
                if not members:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=NO_CANDIDATES)
                for member in sorted(members, key=lambda member: (member["list"], member["kind"], member["position"])):
                    number, position, kind = int(member["list"]), int(member["position"]), member["kind"]
                    full = " ".join(
                        str(member[field]) for field in ("given", "surname", "second_surname") if member[field]
                    )
                    person = cs.key(f"{SOURCE}.person", f"{stamp}:{code}:{number}:{kind}{position}")
                    if person in cs.minted:
                        cs.add("person", id=person, gender={"F": "female", "M": "male"}.get(str(member["sex"])))
                        name(person, display(full, "es"), "es")
                        persons += 1
                    candidacy = cs.key(f"{SOURCE}.candidacy", f"{day}:{code}:{number}:{kind}{position}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=options[number],
                        role="substitute" if kind == "S" else "head" if position == 1 else "list_member",
                        list_position=position,
                        ballot_name=full,
                    )
                    if member["elected"] == "S" and kind == "T":
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=options[number],
                            candidacy_id=candidacy,
                            kind="elected",
                        )

                def finer(key: str, note: str) -> UUID:
                    finer_set = cs.key(f"{SOURCE}.result_set", f"{day}:{code}:{key}")
                    cs.add(
                        "result_set",
                        id=finer_set,
                        election_id=election,
                        contest_id=contest,
                        stage=stage,
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=False,
                        completeness=1,
                    )
                    cs.add("note", entity_id=finer_set, kind="methodology", body_md=note)
                    for entry in entries:
                        if entry["seats"]:
                            cs.add(
                                "outcome",
                                result_set_id=finer_set,
                                option_id=options[int(entry["list"])],
                                kind="won",
                                seats=entry["seats"],
                            )
                    return finer_set

                municipality_unit = lambda town: unit_for(
                    "es.municipio", town, cs.unit("es.provincia", town[:2]), municipality_areas.get(town), "equals"
                )
                if election_row["mesas_tie"]:
                    mesa_set = finer(
                        "mesa",
                        MESA_METHOD.format(where=MESA_SECTION if year >= SECTIONS_FROM else MESA_MUNICIPALITY),
                    )
                    mesa_units: dict[tuple[str, str, str, str], UUID] = {}
                    for mesa in mesas[year, code]:
                        town = f"{mesa['province']}{mesa['municipality']}"
                        section = section_of(str(mesa["section"]))
                        mesa_code = f"{stamp}:{town}{mesa['municipal_district']}{section}{mesa['mesa']}"
                        located = (
                            sections.get(f"{town}{mesa['municipal_district']}{section}")
                            if year >= SECTIONS_FROM
                            else None
                        ) or municipality_areas.get(town)
                        mesa_unit = mesa_units[town, mesa["municipal_district"], mesa["section"], mesa["mesa"]] = (
                            unit_for("es.mesa", mesa_code, municipality_unit(town), located, "within")
                        )
                        tally(mesa_set, mesa_unit, mesa)
                    for vote in mesa_votes[year, code]:
                        town = f"{vote['province']}{vote['municipality']}"
                        value(
                            mesa_set,
                            mesa_units[town, vote["municipal_district"], vote["section"], vote["mesa"]],
                            options[int(vote["list"])],
                            VOTES,
                            vote["votes"],
                        )
                elif election_row["municipalities_tie"]:
                    municipality_set = finer("municipio", MUNICIPALITY_METHOD)
                    for vote in municipality_votes[year, code]:
                        town = f"{vote['province']}{vote['municipality']}"
                        value(
                            municipality_set, municipality_unit(town), options[int(vote["list"])], VOTES, vote["votes"]
                        )
        print(f"{len(years)} elections, {len(polities)} islands, {persons} persons, {len(values)} values")
        if unit_rows:
            cs.frame(
                "reporting_unit",
                pl.DataFrame(list(unit_rows.values()), schema_overrides={"parent_id": pl.String, "area_id": pl.String}),
            )
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema_overrides={"value": pl.Int64}, infer_schema_length=None).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
