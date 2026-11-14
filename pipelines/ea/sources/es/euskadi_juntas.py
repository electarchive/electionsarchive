import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.es import euskadi
from ea.sources.es.euskadi import (
    BALLOTS,
    BLANK,
    INVALID,
    PORTAL,
    PUBLISHER,
    REGISTERED,
    VALID,
    VOTES,
    dhondt,
    fold,
    results,
    sheets,
    tidy,
)
from ea.store import DATA, connect

SOURCE = "es.euskadi_juntas"
PARSED = DATA / "parsed" / SOURCE
ELECTIONS = {
    1979: date(1979, 4, 3),
    1983: date(1983, 5, 8),
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
TERRITORIES = {"01": "Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
NATIVE = {"01": "Araba/Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
TITLE = "Resultados de las elecciones a Juntas Generales (euskadi.eus)"
KEYWORDS = (
    "ayala",
    "aiara",
    "vitoria",
    "zuya",
    "cuadrillas",
    "bilbo",
    "bilbao",
    "busturia",
    "durango",
    "encartaciones",
    "enkarterriak",
    "bidasoa",
    "deba",
    "donostialdea",
    "oria",
)
SAME = {"aiara": "ayala", "bilbo": "bilbao", "enkarterriak": "encartaciones", "cuadrillas": "zuya"}


def url(year: int) -> str:
    return f"{euskadi.BASE}J{year % 100:02d}_c.zip"


def fetch() -> None:
    for year in ELECTIONS:
        raw.fetch(SOURCE, url(year), suffix=".zip")


def circ_key(text: str) -> str:
    return fold(re.sub(r"\(?\bJ\d\d\)?\s*$", "", text.strip()))


def family(text: str) -> str:
    key = fold(text)
    for word in KEYWORDS:
        if word in key:
            return SAME.get(word, word)
    return key


def elected_rows(rows: list[list[str]], year: int) -> list[dict]:
    start = next(index for index, row in enumerate(rows) if "partido" in map(fold, row))
    header = [fold(cell) for cell in rows[start]]
    th, party = header.index("th"), header.index("partido")
    circ = next(index for index, key in enumerate(header) if key.startswith("circ"))
    code = next((index for index, key in enumerate(header) if key in ("codcom", "codigocomarca")), None)
    out = []
    for row in rows[start + 1 :]:
        if len(row) <= party + 1 or not row[th]:
            continue
        name = ", ".join(part.strip() for part in row[-1].strip("'\" ").split(","))
        out.append(
            {
                "year": year,
                "th": euskadi.territory(row[th]),
                "circ": circ_key(row[circ]),
                "comarca": int(row[code]) if code is not None and row[code].strip() else None,
                "party": row[party],
                "name": name,
            }
        )
    return out


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = defaultdict(list)
    placed: dict[tuple[str, str], str] = {}
    for year in ELECTIONS:
        files = sheets(year, SOURCE, url(year))
        units, votes = tidy(results(files["cir"]), year, ("name",))
        for unit in units:
            unit["circ"] = circ_key(unit["name"])
            unit["label"] = re.sub(r"\s*\(?\bJ\d\d\)?\s*$", "", unit.pop("name")).strip()
        for row in votes:
            row["circ"] = circ_key(row.pop("name"))
        tables["circ"].extend(units)
        tables["circ_votes"].extend(votes)
        known = {(unit["th"], unit["circ"]) for unit in units}
        ran = defaultdict(set)
        for row in votes:
            ran[row["th"], row["circ"]].add(row["party"])
        members = elected_rows(files["elec"], year)
        for member in members:
            key = (member["th"], member["circ"])
            if key not in known:
                if (year, member["th"]) != (1979, "01"):
                    raise ValueError((year, key))
            elif member["party"] not in ran[key]:
                (member["party"],) = [
                    p for p in ran[key] if p.startswith(member["party"]) or member["party"].startswith(p)
                ]
        tables["elected"].extend(members)
        codes = {
            (member["th"], member["comarca"]): member["circ"] for member in members if member["comarca"] is not None
        }
        mun = results(files["mun"])
        has_comarca = any(row.get("comarca") for row in mun)
        mun_units, mun_votes = tidy(mun, year, ("mun", "name") + (("comarca",) if has_comarca else ()))
        by_family = {(th, family(circ)): circ for th, circ in known}
        for unit in mun_units:
            if has_comarca:
                unit["circ"] = codes[unit["th"], int(unit.pop("comarca"))]
            else:
                unit["circ"] = by_family[unit["th"], family(placed[unit["th"], unit["mun"]])]
            placed[unit["th"], unit["mun"]] = unit["circ"]
        tables["mun"].extend(mun_units)
        tables["mun_votes"].extend(mun_votes)
        mesa_units, mesa_votes = tidy(results(files["mes"]), year, ("mun", "district", "section", "mesa", "name"))
        tables["mesa"].extend(mesa_units)
        tables["mesa_votes"].extend(mesa_votes)
    for name, rows in tables.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")
    check()


def threshold_fit(votes: dict[str, int], valid: int, seats: Counter[str], seat_count: int) -> list[float]:
    return [
        share
        for share in (0.0, 0.03, 0.05)
        if all(dhondt(votes, valid, seat_count, share)[party] == seats[party] for party in set(votes) | set(seats))
    ]


def check() -> None:
    circ = pl.read_parquet(PARSED / "circ.parquet")
    cir_votes = pl.read_parquet(PARSED / "circ_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    mun = pl.read_parquet(PARSED / "mun.parquet")
    mun_votes = pl.read_parquet(PARSED / "mun_votes.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    for row in circ.iter_rows(named=True):
        where = (pl.col("year") == row["year"]) & (pl.col("th") == row["th"]) & (pl.col("circ") == row["circ"])
        part = {r["party"]: r["votes"] for r in cir_votes.filter(where).iter_rows(named=True)}
        seats = Counter(members.filter(where)["party"].to_list())
        total = sum(seats.values())
        fit = threshold_fit(part, row["cand"] + row["blank"], seats, total)
        mine = mun.filter(where)
        got = int(mun_votes.join(mine.select("year", "th", "mun"), on=["year", "th", "mun"])["votes"].sum())
        print(
            row["year"],
            row["th"],
            row["circ"][:30],
            f"seats={total}",
            f"lists={sum(part.values()) == row['cand']}",
            f"fit={fit}",
            f"mun={got - sum(part.values())}",
        )
    for year in ELECTIONS:
        print(
            year,
            "mesa",
            mesa_votes.filter(pl.col("year") == year)["votes"].sum(),
            cir_votes.filter(pl.col("year") == year)["votes"].sum(),
        )


def threshold(year: int) -> float:
    return 0.05 if year <= 1987 else 0.03


LAW = (
    "Ley 1/1987, de 27 de marzo, de Elecciones para las Juntas Generales de los Territorios Históricos de Álava, "
    "Bizkaia y Gipuzkoa"
)
SYSTEM = (
    "Closed party lists in the circunscripciones of each historic territory, which elect the Juntas Generales of "
    "Álava, Bizkaia, and Gipuzkoa. Seats go by the D'Hondt method within each circunscripción to the lists with at "
    "least {share}% of its valid votes, and blank ballots count as valid votes. The threshold is the one that "
    "reproduces the declared seats of the circunscripciones in the elections it covers; the publisher's files do not "
    "state it. The Basque Government publishes the elected members of each list, and those are the declared seats "
    "stored here (" + LAW + ")."
)
CIRC_METHOD = (
    "List votes, seats, registered voters, ballots cast, blank ballots, and null ballots by circunscripción from the "
    "Basque Government's results files. Valid ballots here are the votes to candidacies, with blank ballots stored "
    "apart. The seats are the number of members the Elec file names for each list."
)
MISMATCH = (
    " The file names {declared} members for this circunscripción, and a D'Hondt allocation of {declared} seats at the "
    "{share}% threshold gives a different split ({computed}); the declared members are stored as published."
)
MUNICIPALITY_METHOD = (
    "List votes and ballot counts by municipality from the Basque Government's results files, for the municipalities "
    "the file assigns to this circunscripción. The municipal figures add up to {votes:,} list votes, against "
    "{official:,} in the circunscripción results."
)
MESA_METHOD = (
    "List votes and ballot counts by polling station (mesa) from the Basque Government's results files, each mesa on "
    "its census section where the 2025 INE section map has the same code, and otherwise on its municipality. The mesa "
    "figures add up to {votes:,} list votes, against {official:,} in the circunscripción results."
)
LISTS_DIFFER = (
    " In the source, the list votes of this circunscripción add up to {lists:,}, against {candidacies:,} votes to "
    "candidacies in its own totals."
)
SMALL = {"de", "del", "la", "las", "los", "y", "e"}


def title(text: str) -> str:
    text = re.sub(r"\s*;\s*|\s{2,}", ", ", text.strip())
    words = text.lower().split(" ")
    return " ".join(w if w in SMALL else w[:1].upper() + w[1:] for w in words)


def emit() -> Path:
    circ = pl.read_parquet(PARSED / "circ.parquet")
    circ_votes = pl.read_parquet(PARSED / "circ_votes.parquet")
    muns = pl.read_parquet(PARSED / "mun.parquet")
    mun_votes = pl.read_parquet(PARSED / "mun_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesa.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version="1",
            summary=(
                "Juntas Generales of Álava, Bizkaia, and Gipuzkoa, 1979 to 2023, by circunscripción, municipality, "
                "and polling station, with the elected members, from the Basque Government's results files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, basque = cs.polity("ES"), cs.polity("ES-PV")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Juntas Generales")
        scheme = "es.junta_circ"
        if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
            cs.add(
                "scheme",
                code=scheme,
                polity_id=spain,
                kind="electoral_district",
                rank=3,
                noun="Juntas Generales circunscripción",
            )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license=None,
            license_url=None,
            attribution="Fuente: Gobierno Vasco, euskadi.eus.",
        )
        for year in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)

        parties: dict[str, UUID] = {}
        for label in sorted(circ_votes["party"].unique()):
            parties[label] = cs.key(f"{euskadi.SOURCE}.party", label)
            if parties[label] in cs.minted:
                cs.add("party", id=parties[label], kind="party", polity_id=basque)
                name(parties[label], label, "es")

        bodies: dict[str, UUID] = {}
        for code, label in TERRITORIES.items():
            body = bodies[code] = cs.key("es.body", f"juntas-generales-{slug(label)}")
            cs.add("body", id=body, polity_id=basque, kind="council")
            name(body, f"Juntas Generales of {label}")
            name(body, f"Juntas Generales de {NATIVE[code]}", "es", "native")
        systems: dict[float, UUID] = {}
        for share in sorted({threshold(year) for year in ELECTIONS}):
            percent = int(share * 100)
            system = systems[share] = cs.key("es.electoral_system", f"juntas-generales-{percent}")
            cs.add(
                "electoral_system",
                id=system,
                polity_id=basque,
                rules={
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": percent},
                },
                rules_version=1,
            )
            name(system, f"Juntas Generales closed-list D'Hondt system with a {percent}% threshold")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM.format(share=percent))

        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        section_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}
        for code in TERRITORIES:
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(province: str, mun: str) -> UUID:
            code = province + mun
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                area = municipality_areas.get(code)
                relation = "equals" if area is not None else "unlocated"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[province], relation, area, "standard")
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int | None]) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "cand"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, 0))

        sizes = (
            members.filter(~((pl.col("year") == 1979) & (pl.col("th") == "01")))
            .group_by("year", "th")
            .agg(seats=pl.len())
        )
        for code, body in bodies.items():
            previous: tuple[date, int] | None = None
            for year, _, seats in sizes.filter(pl.col("th") == code).sort("year").iter_rows():
                day = ELECTIONS[year]
                if previous is not None and previous[1] != seats:
                    cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=day)
                    previous = None
                if previous is None:
                    previous = (day, seats)
            if previous is not None:
                cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=None)

        for year, day in ELECTIONS.items():
            election = cs.key("es.election", f"juntas:{day}")
            cs.add("election", id=election, polity_id=basque, kind="local", date_from=day, date_to=day, status="held")
            name(election, f"{year} Juntas Generales elections")
            name(election, f"Elecciones a Juntas Generales de {year}", "es", "native")
            path = f"es/pv/{day}-juntas-generales"
            cs.add("slug", path=path, entity_id=election)
            stamp = f"{year}{day.month:02d}"
            for entry in circ.filter(pl.col("year") == year).iter_rows(named=True):
                province, key_circ = entry["th"], entry["circ"]
                where = (pl.col("year") == year) & (pl.col("th") == province) & (pl.col("circ") == key_circ)
                rows = circ_votes.filter(where).sort("votes", descending=True)
                declared = members.filter(where)
                seats_by_party = Counter(declared["party"].to_list())
                key = f"{day}:{province}:{key_circ}"
                label = title(entry["label"])
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=bodies[province],
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=declared.height,
                    electoral_system_id=systems[threshold(year)],
                    default_result_kind="counted",
                )
                name(contest, f"{TERRITORIES[province]}: {label}")
                name(contest, f"{NATIVE[province]}: {entry['label']}", "es", "native")
                cs.add("slug", path=f"{path}/{slug(TERRITORIES[province])}/{slug(label)}", entity_id=contest)
                options: dict[str, UUID] = {}
                for vote in rows.iter_rows(named=True):
                    option = options[vote["party"]] = cs.key(f"{SOURCE}.option", f"{key}:{vote['party']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[vote["party"]],
                        ballot_label=vote["party"],
                    )
                official = int(rows["votes"].sum())

                def make_set(label: str, default: bool, completeness: float, note: str) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                note = CIRC_METHOD
                if official != entry["cand"]:
                    note += LISTS_DIFFER.format(lists=official, candidacies=entry["cand"])
                part = {r["party"]: r["votes"] for r in rows.iter_rows(named=True)}
                computed = dhondt(part, entry["cand"] + entry["blank"], declared.height, threshold(year))
                if computed != seats_by_party:
                    split = ", ".join(f"{p} {n}" for p, n in sorted(computed.items()))
                    note += MISMATCH.format(declared=declared.height, share=int(threshold(year) * 100), computed=split)
                circ_set = make_set("circ", True, 1, note)
                circ_unit = cs.unit(scheme, f"{stamp}:{province}:{key_circ}")
                unit_rows[circ_unit] = (
                    circ_unit,
                    scheme,
                    f"{stamp}:{province}:{key_circ}",
                    province_units[province],
                    "unlocated",
                    None,
                    "standard",
                )
                accounting(circ_set, circ_unit, entry)
                for vote in rows.iter_rows(named=True):
                    values.append((str(circ_set), str(circ_unit), str(options[vote["party"]]), VOTES, vote["votes"], 0))
                    if count := seats_by_party.get(vote["party"], 0):
                        cs.add(
                            "outcome",
                            result_set_id=circ_set,
                            option_id=options[vote["party"]],
                            kind="won",
                            seats=count,
                        )
                seen: Counter[str] = Counter()
                for member in declared.iter_rows(named=True):
                    person_key = f"{key}:{member['party']}:{fold(member['name'])}"
                    seen[person_key] += 1
                    if seen[person_key] > 1:
                        person_key += f":{seen[person_key]}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, member["name"], "es")
                    party = parties[member["party"]]
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["party"]],
                        party_id=party,
                        ballot_label=member["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", person_key),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=member["name"],
                        member_of_party_id=party,
                        is_independent=False,
                    )
                    cs.add("outcome", result_set_id=circ_set, option_id=option, kind="elected")

                inside = (pl.col("year") == year) & (pl.col("th") == province)
                places = muns.filter(inside & (pl.col("circ") == key_circ))
                place_codes = places["mun"].to_list()
                place_votes = mun_votes.filter(inside & pl.col("mun").is_in(place_codes))
                got = int(place_votes["votes"].sum())
                municipality_set = make_set(
                    "municipality",
                    False,
                    min(got / official, 1),
                    MUNICIPALITY_METHOD.format(votes=got, official=official),
                )
                for row in places.iter_rows(named=True):
                    accounting(municipality_set, municipality_unit(province, row["mun"]), row)
                for row in place_votes.iter_rows(named=True):
                    values.append(
                        (
                            str(municipality_set),
                            str(municipality_unit(province, row["mun"])),
                            str(options[row["party"]]),
                            VOTES,
                            row["votes"],
                            0,
                        )
                    )

                units = mesas.filter(inside & pl.col("mun").is_in(place_codes))
                unit_votes = mesa_votes.filter(inside & pl.col("mun").is_in(place_codes))
                got = int(unit_votes["votes"].sum())
                mesa_set = make_set(
                    "mesa", False, min(got / official, 1), MESA_METHOD.format(votes=got, official=official)
                )
                mesa_units: dict[tuple[str, str, str, str], UUID] = {}
                for row in units.iter_rows(named=True):
                    section = f"{province}{row['mun']}{row['district']}{row['section']}"
                    code = f"{stamp}:{section}{row['mesa']}"
                    unit = cs.unit("es.mesa", code)
                    area = (section_areas.get(section) if year >= 2020 else None) or municipality_areas.get(
                        province + row["mun"]
                    )
                    unit_rows[unit] = (
                        unit,
                        "es.mesa",
                        code,
                        municipality_unit(province, row["mun"]),
                        "within" if area is not None else "unlocated",
                        area,
                        "standard",
                    )
                    mesa_units[row["mun"], row["district"], row["section"], row["mesa"]] = unit
                    accounting(mesa_set, unit, row)
                for row in unit_votes.iter_rows(named=True):
                    unit = mesa_units[row["mun"], row["district"], row["section"], row["mesa"]]
                    values.append((str(mesa_set), str(unit), str(options[row["party"]]), VOTES, row["votes"], 0))

        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(unit),
                        "polity_id": str(spain),
                        "scheme_code": scheme_code,
                        "code": code,
                        "parent_id": None if parent is None else str(parent),
                        "area_relation": relation,
                        "area_id": None if area is None else str(area),
                        "kind": kind,
                    }
                    for unit, scheme_code, code, parent, relation, area, kind in unit_rows.values()
                    if unit in cs.minted
                ],
                schema_overrides={"parent_id": pl.String, "area_id": pl.String},
            ),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                    "channel": pl.Int16,
                },
                orient="row",
            ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
