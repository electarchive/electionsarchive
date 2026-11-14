import os
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import display
from ea.sources.es.ine import COMMUNITIES, natural
from ea.sources.es.infoelectoral import APLIEXTR, LAYOUTS, PAGE, archive, processes, records
from ea.store import DATA, connect

SOURCE = "es.municipales"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales: elecciones municipales (ficheros de datos)"
FIRST = "201105"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
THRESHOLD = 0.05
ASSEMBLIES = {"51001": ("ES-CE", "Ceuta"), "52001": ("ES-ML", "Melilla")}
CLOSED = (
    "Municipalities of more than 250 residents elect their council from closed lists in a single municipal "
    "constituency. Seats are shared by the highest-average (D'Hondt) method among the lists with at least 5 percent "
    "of the valid votes, blank ballots included, and go to candidates in list order (Ley Orgánica 5/1985, articles "
    "179 and 180). Council size follows the municipality's population, from 7 seats up to 57 in Madrid; Ceuta and "
    "Melilla elect assemblies of 25."
)
OPEN = (
    "Municipalities of 100 to 250 residents elect 5 councillors and smaller ones 3, from open lists: each voter "
    "marks up to 4 candidates in the first case and up to 2 in the second, across any lists, and the candidates "
    "with the most votes are elected (Ley Orgánica 5/1985, article 184, as amended by Ley Orgánica 2/2011)."
)
OPEN_MAYOR = (
    "Municipalities under the open council (concejo abierto) regime elect no council: the residents elect the "
    "mayor directly, and the candidate with the most votes wins (Ley Orgánica 5/1985, article 179.2)."
)
MUNICIPALITY_METHOD = (
    "List votes, registered voters, blank ballots, and null ballots for the municipality from the Ministry of the "
    "Interior's data files. Valid votes here are the votes to candidacies; blank ballots, which the ministry counts "
    "as valid, are stored apart. Seats are the councillors the ministry declares for each list, and the elected "
    "councillors are the candidates it flags as elected."
)
OPEN_METHOD = (
    "Votes for each candidate, registered voters, blank ballots, and null ballots from the Ministry of the "
    "Interior's file for municipalities under 250 residents. Valid votes here are the ballots cast for candidacies; "
    "a voter may mark several candidates, so the candidates' votes add up to more than the ballots. The elected "
    "candidates are those the ministry flags as elected."
)
MESA_METHOD = (
    "List votes, registered voters, blank ballots, and null ballots by polling station (mesa) from the Ministry of "
    "the Interior's data files. A mesa sits on its census section where the section code is in the 2025 INE section "
    "map, otherwise on its municipality."
)
MESA_EXACT = " The mesa figures add up to the municipality's official totals exactly."
MESA_DIFFERS = (
    " The mesa figures add up to {mesa:,} list votes, against {official:,} in the municipality's official totals, "
    "so the municipality set is the default."
)
SEATS_CAVEAT = (
    "The ministry flags {flagged} candidates of {label} as elected, where the list won {seats} seats as declared."
)
DHONDT_CAVEAT = (
    "D'Hondt with a 5 percent threshold over the stored votes does not give the declared seats; the declared seats "
    "are stored as published."
)
OVERFLAGGED = (
    "The ministry flags {marked} candidates as elected for {seats} seats. The candidates with more than {cutoff} "
    "votes are stored as elected, since the most-voted candidates win the seats, and the flagged candidates with "
    "{cutoff} votes or fewer are stored as tied."
)
VACANT = "The lists won {filled} of the {seats} seats as declared; the ministry leaves the rest unfilled."
UNEVEN = (
    "The list votes add up to {summed:,}, against {official:,} votes to candidacies in the municipality's totals; "
    "both are stored as the file prints them."
)
OVER = "The ballots cast exceed the registered voters as the file prints them."
Row = dict[str, str | int | None]
SHIFTED = {"201505"}
UNMARKED = {"201105"}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", plain).split())


def section_of(field: str) -> str:
    if (found := re.fullmatch(r"(\d+)([A-Z]?)", field)) is None:
        raise ValueError(f"section {field!r}")
    return f"{int(found[1]):03d}{found[2]}"


def dhondt(votes: dict[int, int], base: int, seats: int) -> Counter[int]:
    eligible = {code: count for code, count in votes.items() if count >= THRESHOLD * base}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def fetch() -> None:
    pass


def extent(process: str) -> str:
    return "MESA" if f"{APLIEXTR}04{process}_MESA.zip" in raw.keys("es.infoelectoral") else "MUNI"


def parse() -> None:
    tables: dict[str, list[Row]] = defaultdict(list)
    for process in processes("04"):
        if process < FIRST:
            continue
        kind = extent(process)
        read = lambda number, layout=None: records("04", process, kind, number, layout)
        with zipfile.ZipFile(archive("04", process, kind)) as bundle:
            text = bundle.read(f"0204{process[2:]}.DAT").decode("latin-1")
        day = date(int(text[16:20]), int(text[14:16]), int(text[12:14]))
        tables["days"].append({"process": process, "day": day.isoformat()})
        for row in read("03"):
            tables["lists"].append(
                {
                    "process": process,
                    "list": row["list"],
                    "abbrev": " ".join(str(row["abbrev"]).split()),
                    "name": " ".join(str(row["name"]).split()),
                    "national_head": row["national_head"],
                }
            )
        for row in read("05"):
            if row["municipal_district"] != "99":
                continue
            tables["councils"].append(
                {
                    "process": process,
                    "code": f"{row['province']}{row['municipality']}",
                    "tier": "large",
                    "name": " ".join(str(row["name"]).split()),
                    "registered": row["census"],
                    "blank": row["blank"],
                    "invalid": row["invalid"],
                    "list_votes": row["list_votes"],
                    "seats": row["seats"],
                    "official": row["official"] == "S",
                }
            )
        for row in read("11"):
            tables["councils"].append(
                {
                    "process": process,
                    "code": f"{row['province']}{row['municipality']}",
                    "tier": "mayor" if row["seats"] == 1 else "open",
                    "name": " ".join(str(row["name"]).split()),
                    "registered": row["census"],
                    "blank": row["blank"],
                    "invalid": row["invalid"],
                    "list_votes": row["list_votes"],
                    "seats": row["seats"],
                    "official": row["official"] == "S",
                }
            )
        for row in read("06"):
            if row["municipal_district"] == "99":
                tables["list_votes"].append(
                    {
                        "process": process,
                        "code": f"{row['province']}{row['municipality']}",
                        "list": row["list"],
                        "votes": row["votes"],
                        "seats": row["seats"],
                    }
                )
        for row in read("04"):
            tables["candidates"].append(
                {
                    "process": process,
                    "code": f"{row['province']}{row['municipality']}",
                    "list": row["list"],
                    "position": row["position"],
                    "substitute": row["kind"] == "S" and process not in UNMARKED,
                    "given": row["given"],
                    "surname": row["surname"],
                    "second_surname": row["second_surname"],
                    "sex": row["sex"],
                    "votes": None,
                    "elected": row["elected"] == "S",
                }
            )
        order: Counter[tuple[str, int]] = Counter()
        small_lists: dict[tuple[str, int], Row] = {}
        layout = (
            tuple(
                (field, start - 1, end - 1) if start > 100 else (field, start, end)
                for field, start, end in LAYOUTS["12"]
                if field != "sex"
            )
            if process in SHIFTED
            else None
        )
        for row in read("12", layout):
            code = f"{row['province']}{row['municipality']}"
            order[code, int(row["list"])] += 1
            small_lists[code, int(row["list"])] = {
                "process": process,
                "code": code,
                "list": row["list"],
                "votes": row["list_votes"],
                "seats": row["seats"],
            }
            tables["candidates"].append(
                {
                    "process": process,
                    "code": code,
                    "list": row["list"],
                    "position": order[code, int(row["list"])],
                    "substitute": False,
                    "given": row["given"],
                    "surname": row["surname"],
                    "second_surname": row["second_surname"],
                    "sex": row.get("sex", ""),
                    "votes": row["votes"],
                    "elected": row["elected"] == "S",
                }
            )
        tables["list_votes"] += small_lists.values()
        for row in read("09"):
            tables["mesas"].append(
                {
                    "process": process,
                    "code": f"{row['province']}{row['municipality']}",
                    "district": row["municipal_district"],
                    "section": row["section"],
                    "mesa": row["mesa"],
                    "registered": row["census"],
                    "blank": row["blank"],
                    "invalid": row["invalid"],
                    "list_votes": row["list_votes"],
                    "official": row["official"] == "S",
                }
            )
        for row in read("10"):
            if row["votes"]:
                tables["mesa_votes"].append(
                    {
                        "process": process,
                        "code": f"{row['province']}{row['municipality']}",
                        "district": row["municipal_district"],
                        "section": row["section"],
                        "mesa": row["mesa"],
                        "list": row["list"],
                        "votes": row["votes"],
                    }
                )
        print(process, {name: len(rows) for name, rows in tables.items()}, flush=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / f"{name}.parquet")
    councils = pl.read_parquet(PARSED / "councils.parquet")
    if duplicates := councils.group_by("process", "code").len().filter(pl.col("len") > 1).height:
        raise ValueError(f"{duplicates} councils appear twice")


def emit() -> Path:
    process = os.environ["ES_MUNICIPALES"]
    days = {
        row["process"]: date.fromisoformat(row["day"]) for row in pl.read_parquet(PARSED / "days.parquet").to_dicts()
    }
    following = {
        stamp: min((other for other in processes("04") if other > stamp), default=None) for stamp in processes("04")
    }
    day = days[process]
    year = day.year
    pick = lambda name: pl.read_parquet(PARSED / f"{name}.parquet").filter(pl.col("process") == process)
    councils = pick("councils").sort("code")
    lists = {row["list"]: row for row in pick("lists").to_dicts()}
    list_votes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pick("list_votes").sort("votes", descending=True).to_dicts():
        list_votes[row["code"]].append(row)
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pick("candidates").sort("list", "substitute", "position").to_dicts():
        candidates[row["code"]].append(row)
    mesas = pick("mesas")
    mesa_votes = pick("mesa_votes")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Spain's municipal elections of {day:%-d %B %Y}: every council and open-council mayor by "
                "municipality, with mesa results where the ministry publishes them, every list and candidate, and "
                "the councillors declared elected, from the Ministry of the Interior's data files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain = cs.polity("ES")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the municipal elections")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Reuse under Ley 37/2007 with the source cited",
            attribution="Origen de los datos: Ministerio del Interior.",
        )
        cs.artifact(raw.cached("es.infoelectoral", f"{APLIEXTR}04{process}_{extent(process)}.zip"), source)

        systems: dict[str, UUID] = {}
        for key, rules, english, note in (
            (
                "municipal-closed-list",
                {
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD * 100},
                },
                "Spanish municipal closed-list D'Hondt system",
                CLOSED,
            ),
            ("municipal-open-list", {"formula": "limited"}, "Spanish municipal open-list limited vote", OPEN),
            ("concejo-abierto", {"formula": "fptp"}, "Spanish open-council mayoral plurality", OPEN_MAYOR),
        ):
            systems[key] = cs.key("es.electoral_system", key)
            if systems[key] in cs.minted:
                cs.add("electoral_system", id=systems[key], polity_id=spain, rules=rules, rules_version=1)
                name(systems[key], english)
                cs.add("note", entity_id=systems[key], kind="methodology", body_md=note)

        areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        area_names = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'es.municipio'"""
            ).fetchall()
        )
        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        communities = dict(
            conn.execute(
                """select pc.code, cc.code from area p join area_code pc on pc.area_id = p.id
                   join area_parent ap on ap.area_id = p.id join area c on c.id = ap.parent_id
                   join area_code cc on cc.area_id = c.id
                   where p.scheme_code = 'es.provincia' and c.scheme_code = 'es.comunidad'"""
            ).fetchall()
        )
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

        def party_for(number: int, code: str) -> UUID:
            entry = lists[number]
            label = str(entry["name"])
            folded = fold(label)
            local = folded.startswith(("AGRUPACION", "AGRUPACIO", "AGRUPAMENT", "HAUTESLE", "AE ")) or (
                "ELECTORES" in folded or "ELECTORS" in folded
            )
            key = f"m:{code}:{folded}" if local else f"n:{folded}"
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

        umbrella = cs.key("es.election", f"{day}:local")
        cs.add("election", id=umbrella, polity_id=spain, kind="local", date_from=day, date_to=day, status="held")
        name(umbrella, f"{year} Spanish municipal elections")
        name(umbrella, f"Elecciones municipales de {year}", "es", "native")
        cs.add("slug", path=f"es/{day}-local", entity_id=umbrella)

        paths: dict[str, str] = {}
        taken: Counter[str] = Counter()
        labels: dict[str, str] = {}
        for row in councils.iter_rows(named=True):
            code = row["code"]
            labels[code] = area_names.get(code) or natural(row["name"])
            if code in ASSEMBLIES:
                continue
            community = COMMUNITIES[communities[code[:2]]][1]
            paths[code] = f"es/{community}/{slug(labels[code])}"
            taken[paths[code]] += 1
        for code, path in paths.items():
            if taken[path] > 1:
                paths[code] = f"{path}-{code}"
        for code in ASSEMBLIES:
            paths[code] = f"es/{COMMUNITIES[communities[code[:2]]][1]}"

        unit_rows: dict[UUID, dict[str, str | None]] = {}
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(code: str) -> UUID:
            unit = municipality_units[code] = cs.unit("es.municipio", code)
            if unit in cs.minted and unit not in unit_rows:
                area = areas.get(code)
                unit_rows[unit] = {
                    "id": str(unit),
                    "polity_id": str(spain),
                    "scheme_code": "es.municipio",
                    "code": code,
                    "parent_id": str(cs.unit("es.provincia", code[:2])),
                    "area_relation": "unlocated" if area is None else "equals",
                    "area_id": None if area is None else str(area),
                    "kind": "standard",
                }
            return unit

        values: list[dict[str, object]] = []
        persons = 0

        def tally(result_set: UUID, unit: UUID, row: dict[str, object]) -> None:
            ballots = int(str(row["list_votes"])) + int(str(row["blank"])) + int(str(row["invalid"]))
            for measure, value in (
                (REGISTERED, row["registered"]),
                (BALLOTS, ballots),
                (VALID, row["list_votes"]),
                (INVALID, row["invalid"]),
                (BLANK, row["blank"]),
            ):
                if measure != REGISTERED or value:
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": None,
                            "measure": measure,
                            "value": value,
                        }
                    )

        def person_for(key: str, row: dict[str, Any]) -> UUID:
            nonlocal persons
            person = cs.key(f"{SOURCE}.person", key)
            if person in cs.minted:
                full = " ".join(str(row[field]) for field in ("given", "surname", "second_surname") if row[field])
                cs.add("person", id=person, gender={"F": "female", "M": "male"}.get(str(row["sex"])))
                name(person, display(full, "es"), "es")
                persons += 1
            return person

        mesa_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in mesas.iter_rows(named=True):
            mesa_rows[row["code"]].append(row)
        mesa_list_votes: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in mesa_votes.iter_rows(named=True):
            mesa_list_votes[row["code"]].append(row)
        stamp = process
        mismatched = []
        skipped: list[str] = []
        for row in councils.iter_rows(named=True):
            code, tier, label = row["code"], row["tier"], labels[row["code"]]
            if not row["list_votes"] and not any(entry["seats"] for entry in list_votes.get(code, [])):
                skipped.append(code)
                continue
            path = paths[code]
            area = areas.get(code)
            if code in ASSEMBLIES:
                polity = cs.polity(ASSEMBLIES[code][0])
            else:
                polity = cs.key("es.polity", f"municipio:{code}")
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="municipality", iso_code=None)
                    name(polity, label, "es")
                    cs.add(
                        "polity_parent",
                        polity_id=polity,
                        parent_id=cs.polity(f"ES-{COMMUNITIES[communities[code[:2]]][0]}"),
                    )
                    if area is not None:
                        cs.add("polity_territory", polity_id=polity, area_id=area)
                    cs.add("slug", path=path, entity_id=polity)
            election = cs.key("es.election", f"{day}:local:{code}")
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
            name(election, f"{year} {label} municipal election")
            cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
            seats = int(row["seats"])
            if tier == "mayor":
                office = cs.key("es.office", f"alcalde:{code}")
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="mayor", body_id=None)
                    name(office, f"Mayor of {label}")
                    name(office, f"Alcalde de {label}", "es", "native")
                body = None
            else:
                office = None
                body = cs.key("es.body", f"ayuntamiento:{code}")
                if body in cs.minted:
                    cs.add("body", id=body, polity_id=polity, kind="council")
                    if code in ASSEMBLIES:
                        name(body, f"Assembly of {label}")
                        name(body, f"Asamblea de {label}", "es", "native")
                    else:
                        name(body, f"Municipal council of {label}")
                        name(body, f"Ayuntamiento de {label}", "es", "native")
                until = following.get(process)
                cs.add(
                    "body_size",
                    body_id=body,
                    seats=seats,
                    valid_from=day,
                    valid_to=None if until is None or until not in days else days[until],
                )
            contest = cs.key(f"{SOURCE}.contest", f"{day}:{code}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind={"large": "list_allocation", "open": "body_seats", "mayor": "office"}[tier],
                body_id=body,
                office_id=office,
                district_area_id=area,
                magnitude=seats,
                electoral_system_id=systems[
                    {"large": "municipal-closed-list", "open": "municipal-open-list", "mayor": "concejo-abierto"}[tier]
                ],
                default_result_kind="counted",
            )
            name(contest, f"{year} {label} {'mayoral' if tier == 'mayor' else 'municipal'} election")
            cs.add(
                "slug", path=f"{path}/{day}-local/{'alcalde' if tier == 'mayor' else 'ayuntamiento'}", entity_id=contest
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{code}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official" if row["official"] else "preliminary",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=MUNICIPALITY_METHOD if tier == "large" else OPEN_METHOD,
            )
            unit = municipality_unit(code)
            tally(result_set, unit, row)
            if row["registered"] and row["list_votes"] + row["blank"] + row["invalid"] > row["registered"]:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=OVER)
            entries = list_votes.get(code, [])
            members = candidates.get(code, [])
            if tier == "large":
                options: dict[int, UUID] = {}
                for entry in entries:
                    number = int(entry["list"])
                    option = options[number] = cs.key(f"{SOURCE}.option", f"{day}:{code}:{number}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_for(number, code),
                        ballot_label=lists[number]["name"],
                    )
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": str(option),
                            "measure": VOTES,
                            "value": entry["votes"],
                        }
                    )
                    if entry["seats"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry["seats"])
                if (filled := sum(int(entry["seats"]) for entry in entries)) != seats:
                    cs.add(
                        "note", entity_id=result_set, kind="caveat", body_md=VACANT.format(filled=filled, seats=seats)
                    )
                if (summed := sum(int(entry["votes"]) for entry in entries)) != row["list_votes"]:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=UNEVEN.format(summed=summed, official=row["list_votes"]),
                    )
                won = dhondt(
                    {int(entry["list"]): int(entry["votes"]) for entry in entries},
                    int(row["list_votes"]) + int(row["blank"]),
                    seats,
                )
                if any(won[int(entry["list"])] != entry["seats"] for entry in entries):
                    mismatched.append(code)
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=DHONDT_CAVEAT)
                flagged: Counter[int] = Counter()
                for member in members:
                    number = int(member["list"])
                    if number not in options:
                        continue
                    kind = "S" if member["substitute"] else "T"
                    position = int(member["position"])
                    candidacy = cs.key(f"{SOURCE}.candidacy", f"{day}:{code}:{number}:{kind}{position}")
                    full = " ".join(
                        str(member[field]) for field in ("given", "surname", "second_surname") if member[field]
                    )
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person_for(f"{stamp}:{code}:{number}:{kind}{position}", member),
                        option_id=options[number],
                        role="substitute" if member["substitute"] else "head" if position == 1 else "list_member",
                        list_position=position,
                        ballot_name=full,
                    )
                    if member["elected"] and not member["substitute"]:
                        flagged[number] += 1
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=options[number],
                            candidacy_id=candidacy,
                            kind="elected",
                        )
                for entry in entries:
                    number = int(entry["list"])
                    if members and flagged[number] != entry["seats"]:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=SEATS_CAVEAT.format(
                                flagged=flagged[number], label=lists[number]["name"], seats=entry["seats"]
                            ),
                        )
                rows = mesa_rows.get(code, [])
                if rows:
                    total = sum(int(str(mesa["list_votes"])) for mesa in rows)
                    note = MESA_METHOD + (
                        MESA_EXACT
                        if total == row["list_votes"]
                        else MESA_DIFFERS.format(mesa=total, official=row["list_votes"])
                    )
                    mesa_set = cs.key(f"{SOURCE}.result_set", f"{day}:{code}:mesa")
                    cs.add(
                        "result_set",
                        id=mesa_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official" if all(mesa["official"] for mesa in rows) else "preliminary",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=False,
                        completeness=min(total / row["list_votes"], 1) if row["list_votes"] else 1,
                    )
                    cs.add("note", entity_id=mesa_set, kind="methodology", body_md=note)
                    for entry in entries:
                        if entry["seats"]:
                            cs.add(
                                "outcome",
                                result_set_id=mesa_set,
                                option_id=options[int(entry["list"])],
                                kind="won",
                                seats=entry["seats"],
                            )
                    mesa_units: dict[tuple[object, ...], UUID] = {}
                    for mesa in rows:
                        section = section_of(str(mesa["section"]))
                        mesa_code = f"{stamp}:{code}{mesa['district']}{section}{mesa['mesa']}"
                        mesa_unit = mesa_units[mesa["district"], mesa["section"], mesa["mesa"]] = cs.unit(
                            "es.mesa", mesa_code
                        )
                        if mesa_unit in cs.minted and mesa_unit not in unit_rows:
                            place = f"{code}{mesa['district']}{section}"
                            located = sections.get(place) or area
                            unit_rows[mesa_unit] = {
                                "id": str(mesa_unit),
                                "polity_id": str(spain),
                                "scheme_code": "es.mesa",
                                "code": mesa_code,
                                "parent_id": str(unit),
                                "area_relation": "unlocated" if located is None else "within",
                                "area_id": None if located is None else str(located),
                                "kind": "standard",
                            }
                        tally(mesa_set, mesa_unit, mesa)
                    for vote in mesa_list_votes.get(code, []):
                        number = int(str(vote["list"]))
                        if number not in options:
                            raise ValueError(f"{process} {code}: mesa votes for list {number} without a municipal row")
                        values.append(
                            {
                                "result_set_id": str(mesa_set),
                                "unit_id": str(mesa_units[vote["district"], vote["section"], vote["mesa"]]),
                                "option_id": str(options[number]),
                                "measure": VOTES,
                                "value": vote["votes"],
                            }
                        )
            else:
                elected = 0
                seats_by_list = {int(entry["list"]): int(entry["seats"]) for entry in entries}
                flagged = Counter()
                marked = sorted((int(member["votes"]) for member in members if member["elected"]), reverse=True)
                cutoff = marked[seats] if len(marked) > seats else -1
                for member in members:
                    number, position = int(member["list"]), int(member["position"])
                    full = " ".join(
                        str(member[field]) for field in ("given", "surname", "second_surname") if member[field]
                    )
                    option = cs.key(f"{SOURCE}.option", f"{day}:{code}:{number}:{position}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_for(number, code),
                        ballot_label=display(full, "es"),
                    )
                    candidacy = cs.key(f"{SOURCE}.candidacy", f"{day}:{code}:{number}:{position}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person_for(f"{stamp}:{code}:{number}:{position}", member),
                        option_id=option,
                        role="head",
                        ballot_name=full,
                    )
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": str(option),
                            "measure": VOTES,
                            "value": member["votes"],
                        }
                    )
                    if member["elected"]:
                        flagged[number] += 1
                        outcome = "elected" if int(member["votes"]) > cutoff else "tied"
                        elected += outcome == "elected"
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
                if len(marked) > seats:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=OVERFLAGGED.format(marked=len(marked), seats=seats, cutoff=cutoff),
                    )
                for number, count in seats_by_list.items():
                    if flagged[number] != count:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=SEATS_CAVEAT.format(
                                flagged=flagged[number], label=lists[number]["name"], seats=count
                            ),
                        )
                if elected < seats and len(marked) <= seats:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"The ministry flags {elected} candidates as elected for {seats} seats.",
                    )
        print(
            f"{process}: {councils.height} councils, skipped {skipped}, {persons} persons, D'Hondt differs in {len(mismatched)}: {mismatched[:20]}"
        )
        if unit_rows:
            cs.frame(
                "reporting_unit",
                pl.DataFrame(
                    list(unit_rows.values()),
                    schema_overrides={"parent_id": pl.String, "area_id": pl.String},
                ),
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
