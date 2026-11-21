"""Chamber of Deputies 2003 and 2006 as packages: the 300 district contests by casilla from INE's district count
files, and the 200 list seats by circunscripción from the Consejo General's acuerdo. The rows production already
holds (the body, the 2006 election, parties, states, sections, and the 2006 casillas another pipeline wrote) are
referenced by id from the reference partition."""

import functools
import re
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.mx import diputados as d
from ea.sources.mx import ine
from ea.store import DATA

SOURCE = d.SOURCE
MEXICO = str(uuid5(package.NAMESPACE, "polity\x1fMX"))
REFERENCE = DATA / "reference" / "mx"
REGISTRY = f"{SOURCE}/registry"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = ine.VOTES, ine.REGISTERED, ine.BALLOTS, ine.VALID, ine.INVALID
# The parties a column or list names, by their Wikidata item in the parties mx.ine wrote.
HELD = {
    "PAN": "Q851087",
    "PRI": "Q682969",
    "PRD": "Q767010",
    "PT": "Q540320",
    "PVEM": "Q383718",
    "CONV": "Q10920094",
    "NA": "Q2080372",
    "PSD": "Q1803295",
    "PSN": "Q2173623",
    "PAS": "Q2016845",
}
# Parties no other pipeline holds; the 2003 file lists them.
NEW = {
    "MP": ("México Posible", "Mexico Possible"),
    "PLM": ("Partido Liberal Mexicano", "Mexican Liberal Party"),
    "FC": ("Fuerza Ciudadana", "Citizen Force"),
}
# Lists the acuerdo counts as one coalition line.
COALITIONS = {
    "APM": ("PRI", "PVEM"),
    "PBT": ("PRD", "PT", "CONV"),
    "APT": ("PRI", "PVEM"),
}
ALLIANCES: dict[tuple[int, tuple[str, ...]], tuple[str, str]] = {
    (2006, ("PRI", "PVEM")): ("Alianza por México", "Alliance for Mexico"),
    (2006, ("CONV", "PRD", "PT")): ("Por el Bien de Todos", "For the Good of All"),
    (2003, ("PRI", "PVEM")): ("Alianza para Todos", "Alliance for Everyone"),
}
# The five 2006 casillas whose printed total differs from the sum of their lines are in d.DIFFERS.
DIFFERENCE_NOTE = (
    " In five casillas the printed total differs from the sum of the party lines (sections 8 of Chiapas district 6, "
    "1238 of Chiapas district 6, 4452 of México district 35, 1119 of Tamaulipas district 2, and 699 of Zacatecas "
    "district 2); the votes stored are the lines as printed, and the ballots cast are their sum."
)
SOURCE_NOTE = (
    "Polling-station results from INE's district count file for the district. Each line is a column of the file: "
    "a party, or a combination of the parties of one coalition that a voter marked."
)
METHOD = d.MR_METHOD
ANNULLED = (
    "The Sala Superior of the electoral tribunal annulled this district's election ({ruling}), so no deputy was "
    "declared elected from the count of 6 July 2003 and an extraordinary election was ordered (acuerdo CG220/2003, "
    "considerando 31). INE's district count file has no rows for the district, so the contest has no results yet."
)
TURNOUT = d.TURNOUT
SEATS_ONLY = d.SEATS_ONLY
SYSTEM_TEXT = {
    "mr": d.MR_SYSTEM,
    "rp": (
        "The 200 list seats are filled from closed lists in five regional constituencies (circunscripciones "
        "plurinominales) of 40 seats each. A party takes part in the allocation if it reaches 2 percent of the "
        "national vote and runs district candidates in at least 200 districts. Seats go by the natural quotient and "
        "largest remainders on the national vote, subject to two limits: no party may hold more than 300 seats from "
        "both tiers, and none may hold a share of the Chamber more than 8 points above its share of the national "
        "vote. The model records the formula and the threshold; the two limits are in the declared seats."
    ),
}


def packaged() -> list[int]:
    return sorted(year for year, config in d.ELECTIONS.items() if config.get("packaged"))


def key_of(year: int) -> str:
    return f"{SOURCE}/{d.ELECTIONS[year]['day']}"


def packages() -> list[str]:
    return [REGISTRY, *(key_of(year) for year in packaged())]


def parse() -> None:
    registry().write()
    for year in packaged():
        build(year).write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def held() -> dict[str, object]:
    """The rows production holds that these packages attach to, read from the reference partition."""
    party = pl.read_parquet(REFERENCE / "party.parquet").filter(pl.col("pipeline") == "mx.ine")
    external = pl.read_parquet(REFERENCE / "external_id.parquet")
    wikidata = external.filter((pl.col("scheme") == "wikidata") & pl.col("entity_id").is_in(party["id"].to_list()))
    parties = {abbrev: wikidata.filter(pl.col("value") == item)["entity_id"].item() for abbrev, item in HELD.items()}
    body = pl.read_parquet(REFERENCE / "body.parquet").filter(pl.col("pipeline") == "mx.diputados")["id"].item()
    election = (
        pl.read_parquet(REFERENCE / "election.parquet")
        .filter((pl.col("pipeline") == "mx.ine") & (pl.col("date_from") == date(2006, 7, 2)))["id"]
        .item()
    )
    units = (
        pl.scan_parquet(REFERENCE / "reporting_unit.parquet")
        .filter(
            (pl.col("scheme_code").is_in(["mx.nation", "mx.entidad"]) & (pl.col("pipeline") == "mx.ine"))
            | (pl.col("scheme_code") == "mx.casilla") & pl.col("code").str.starts_with("2006:")
        )
        .select("id", "scheme_code", "code")
        .collect()
    )
    area = pl.read_parquet(REFERENCE / "area.parquet")
    codes = pl.read_parquet(REFERENCE / "area_code.parquet")
    coded = codes.join(area, left_on="area_id", right_on="id")
    towns = external.filter(pl.col("scheme") == "mx.ine.municipio").join(
        area.filter(pl.col("scheme_code") == "mx.municipio"), left_on="entity_id", right_on="id"
    )
    states = coded.filter(pl.col("scheme_code") == "mx.entidad")
    return {
        "parties": parties,
        "body": body,
        "election": election,
        "nation": units.filter(pl.col("scheme_code") == "mx.nation")["id"].item(),
        "state_units": dict(units.filter(pl.col("scheme_code") == "mx.entidad").select("code", "id").iter_rows()),
        "casillas": dict(units.filter(pl.col("scheme_code") == "mx.casilla").select("code", "id").iter_rows()),
        "sections": dict(coded.filter(pl.col("scheme_code") == "mx.seccion").select("code", "area_id").iter_rows()),
        "state_areas": dict(states.select("code", "area_id").iter_rows()),
        "state_names": dict(states.select("code", "name").iter_rows()),
        "towns": dict(towns.select("value", "entity_id").iter_rows()),
    }


def party_id(abbrev: str) -> str:
    parties = held()["parties"]
    assert isinstance(parties, dict)
    return parties[abbrev] if abbrev in parties else str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fparty\x1f{abbrev}"))


def siceef_source() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fsiceef-diputados"))


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Parties, electoral systems, and the district-count source of the 2003 and 2006 Chamber of Deputies",
    )
    p.external.add(MEXICO)
    source = siceef_source()
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=ine.INE,
        title=d.MR_TITLE,
        url=ine.INE_PAGE,
        license=ine.INE_LICENSE,
        license_url=ine.INE_PAGE,
        attribution="Instituto Nacional Electoral, cómputos distritales",
    )
    for abbrev, (native, english) in NEW.items():
        id = party_id(abbrev)
        p.add("party", id=id, kind="party", polity_id=MEXICO)
        p.add("external_id", entity_id=id, scheme="mx.diputados.party", value=abbrev)
        p.name(id, native, "es", "official")
        p.name(id, english, "en", "english", False)
        p.name(id, abbrev, "es", "abbreviation", False)
    for name, rules, english in (
        ("mr", d.MR_RULES, "Mexican federal district plurality system"),
        ("rp", d.RP_RULES, "Mexican federal regional closed-list proportional system"),
    ):
        id = system_id(name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=MEXICO,
            valid_from=date(1996, 1, 1),
            valid_to=date(2015, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.name(id, english, "en", "official")
        p.add("note", id=p.id("note", id), entity_id=id, kind="methodology", body_md=SYSTEM_TEXT[name], lang="en")
    return p


def system_id(name: str) -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fdeputies-{name}-1996"))


def line_members(abbrev: str) -> tuple[str, ...]:
    return COALITIONS.get(abbrev, (abbrev,))


def build(year: int) -> Package:
    config = d.ELECTIONS[year]
    day: date = config["day"]
    columns: dict[str, tuple[str, ...]] = config["columns"]
    url, title, pages = config["acuerdo"]
    ref = held()
    state_units, casilla_units, sections, towns = (
        ref["state_units"],
        ref["casillas"],
        ref["sections"],
        ref["towns"],
    )
    assert isinstance(state_units, dict) and isinstance(casilla_units, dict)
    assert isinstance(sections, dict) and isinstance(towns, dict)
    state_areas, state_names = ref["state_areas"], ref["state_names"]
    assert isinstance(state_areas, dict) and isinstance(state_names, dict)
    p = Package(
        key_of(year),
        SOURCE,
        f"The {year} Chamber of Deputies of Mexico: the 300 district contests by casilla from INE's district "
        "counts, and the 200 list seats by circunscripción from the Consejo General's acuerdo",
        depends=[REGISTRY],
    )
    for external in (
        MEXICO,
        "mx.casilla",
        "mx.distrito_federal",
        ref["body"],
        ref["nation"],
        *state_units.values(),
        *state_areas.values(),
    ):
        p.external.add(str(external))

    def name(entity: str, text: str, lang: str = "en", kind: str = "official") -> None:
        p.name(entity, text, lang, kind, kind == "official")

    def note(entity: str, text: str) -> None:
        p.add("note", id=p.id("note", entity, text[:40]), entity_id=entity, kind="methodology", body_md=text, lang="en")

    ine_source = siceef_source()
    for kind in d.kinds(year):
        p.artifact(raw.cached(d.RAW, d.file_url(kind, year)), ine_source)
    acuerdo = p.id("source", "acuerdo", year)
    p.add("source", id=acuerdo, kind="official_body", publisher=ine.INE, title=title, url=url, license=ine.PUBLIC)
    p.artifact(raw.cached(d.RAW, url), acuerdo)

    path = f"mx/{day}-general"
    if year == 2006:
        # The 2006 election is the one the presidential contest already belongs to.
        election = str(ref["election"])
        p.external.add(election)
    else:
        election = p.id("election", day)
        p.add("election", id=election, polity_id=MEXICO, kind="general", date_from=day, date_to=day, status="held")
        name(election, f"{year} Mexican general election")
        name(election, f"Elecciones federales de {year}", "es", "native")
        p.add("slug", path=path, entity_id=election, is_current=True)
    body = str(ref["body"])
    mr_system, rp_system = system_id("mr"), system_id("rp")

    alliances: dict[tuple[str, ...], str] = {}
    used_parties: set[str] = set()

    def party(abbrev: str) -> str:
        id = party_id(abbrev)
        if abbrev in HELD:
            used_parties.add(id)
        return id

    def alliance_of(members: tuple[str, ...]) -> str:
        members = tuple(sorted(members))
        if members not in alliances:
            alliances[members] = p.id("alliance", day, "-".join(members))
            native, english = ALLIANCES[(year, members)]
            p.add("alliance", id=alliances[members], kind="electoral_alliance", election_id=election)
            name(alliances[members], english)
            name(alliances[members], native, "es", "native")
            for member in members:
                p.add("alliance_member", alliance_id=alliances[members], party_id=party(member))
        return alliances[members]

    def make_set(
        contest: str, key: str, default: bool, source: str, text: str, completeness: float | None = 1.0
    ) -> str:
        result_set = p.id("result_set", f"{day}:dip:{key}")
        p.add(
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
        note(result_set, text)
        return result_set

    votes = pl.read_parquet(d.PARSED / f"mr_{year}_votes.parquet")
    casillas = pl.read_parquet(d.PARSED / f"mr_{year}_casillas.parquet")
    casillas = casillas.filter((pl.col("casilla") != "") | (pl.col("total") > 0))
    unknown = set(votes.filter(pl.col("kind") == "list")["option"]) - set(columns)
    if unknown:
        raise ValueError(f"{year}: columns without a party: {sorted(unknown)}")

    new_units: list[dict[str, object]] = []
    state_ids = {f"{int(code):02d}": str(unit) for code, unit in state_units.items()}
    areas: set[str] = set()
    reused = 0
    placed = []
    codes: set[str] = set()
    for row in casillas.iter_rows(named=True):
        state = f"{int(row['state']):02d}"
        district = int(row["district"]) if row["district"].isdigit() else 0
        section = int(row["section"])
        letter = row["casilla"][:1] or "X"
        code = (
            f"{year}:{state}-{district:02d}-{section:04d}-"
            f"{ine.casilla_code(year, re.sub(r'^S(?:MR|RP)', 'S', row['casilla']) or 'X')}"
        )
        if code in codes:
            raise ValueError(f"{year}: casilla {code} appears twice")
        codes.add(code)
        channel, kind, area = 0, "standard", None
        if letter in "MV" or section == 0:
            channel, kind = ine.ABROAD, "aggregate"
        elif letter == "A":
            channel, kind = ine.EARLY, "aggregate"
        elif letter == "P":
            channel, kind = ine.PRISON, "prison"
        else:
            area = sections.get(f"{state}{section:04d}")
            if area is None and row["municipality"] not in ("", "0"):
                area = towns.get(f"{state}{int(row['municipality']):03d}")
        if code in casilla_units:
            unit_id = str(casilla_units[code])
            p.external.add(unit_id)
            reused += 1
        else:
            unit_id = p.id("unit", "casilla", code)
            area_id = str(area or state_areas[state])
            areas.add(area_id)
            new_units.append(
                {
                    "id": unit_id,
                    "polity_id": MEXICO,
                    "scheme_code": "mx.casilla",
                    "code": code,
                    "parent_id": state_ids[state],
                    "area_relation": "within",
                    "area_id": area_id,
                    "kind": kind,
                }
            )
        placed.append((*(row[k] for k in ine.KEYS), unit_id, channel))
    p.external.update(areas)
    units = pl.DataFrame(
        placed,
        schema=[*((k, pl.String) for k in ine.KEYS), ("unit_id", pl.String), ("channel", pl.Int16)],
        orient="row",
    )
    located = casillas.join(units, on=ine.KEYS)
    joined = votes.join(units, on=ine.KEYS)
    totals = joined.filter(pl.col("kind") == "list").group_by("state", "district", "option").agg(pl.col("votes").sum())
    print(year, "casillas", units.height, "reused", reused, "new", len(new_units))

    annulled: dict[tuple[str, int], str] = config.get("annulled", {})
    winners: dict[str, int] = {}
    frames: list[pl.DataFrame] = []
    district_rows: list[dict[str, object]] = []
    by_district = {key: group for key, group in totals.group_by(["state", "district"])}
    seen_districts = set()
    for (state, district), group in sorted(by_district.items(), key=lambda item: (int(item[0][0]), int(item[0][1]))):
        ss, dd = f"{int(state):02d}", int(district)
        seen_districts.add((ss, dd))
        state_name = str(state_names[ss])
        contest = p.id("contest", f"{day}:dip:mr:{ss}{dd:02d}")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            magnitude=1,
            electoral_system_id=mr_system,
            default_result_kind="counted",
        )
        name(contest, f"{year} Chamber of Deputies, {state_name}, district {dd}")
        name(contest, f"Diputación federal {year}, {state_name}, distrito {dd}", "es", "native")
        p.add("slug", path=f"{path}/{slug(state_name)}-district-{dd}", entity_id=contest, is_current=True)
        totals_by = {r["option"]: r["votes"] for r in group.iter_rows(named=True) if r["votes"] > 0}
        standing = {tuple(sorted(g)): sum(totals_by[c] for c in g) for g in d.groups_of(columns, list(totals_by))}
        ordered = sorted(standing.items(), key=lambda item: (-item[1], item[0]))
        if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
            raise ValueError(f"{year} {state_name} {dd}: tie for first place")
        district_unit = p.id("unit", "district", year, ss, dd)
        district_rows.append(
            {
                "id": district_unit,
                "polity_id": MEXICO,
                "scheme_code": "mx.distrito_federal",
                "code": f"{year}:{ss}-{dd:02d}",
                "parent_id": state_ids[ss],
                "area_relation": "within",
                "area_id": str(state_areas[ss]),
                "kind": "aggregate",
            }
        )
        district_set = make_set(
            contest, f"mr:{ss}{dd:02d}:district", True, ine_source, METHOD + config.get("winners_note", "")
        )
        casilla_note = SOURCE_NOTE + (DIFFERENCE_NOTE if year == 2006 else "")
        casilla_set = make_set(contest, f"mr:{ss}{dd:02d}:casilla", False, ine_source, casilla_note)
        options: dict[str, str] = {}
        for position, (lines, _) in enumerate(ordered, 1):
            members = tuple(sorted({m for c in lines for m in columns[c]}))
            head = p.id("option", f"{day}:dip:mr:{ss}{dd:02d}:{'+'.join(lines)}")
            p.add(
                "contest_option",
                id=head,
                contest_id=contest,
                kind="candidate",
                party_id=party(members[0]) if len(members) == 1 else None,
                alliance_id=alliance_of(members) if len(members) > 1 else None,
                ballot_position=position,
                ballot_label="–".join(members),
            )
            if position == 1:
                p.add("outcome", result_set_id=district_set, option_id=head, kind="elected", seats=1)
                label = "–".join(members)
                winners[label] = winners.get(label, 0) + 1
            for column in lines:
                if len(lines) == 1:
                    options[column] = head
                    continue
                line = p.id("option", f"{day}:dip:mr:{ss}{dd:02d}:{'+'.join(lines)}:{column}")
                mine = columns[column]
                p.add(
                    "contest_option",
                    id=line,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=head,
                    party_id=party(mine[0]) if len(mine) == 1 else None,
                    alliance_id=alliance_of(members) if len(mine) > 1 else None,
                    ballot_label="–".join(mine),
                )
                options[column] = line
        write_in = p.id("option", f"{day}:dip:mr:{ss}{dd:02d}:unregistered")
        p.add(
            "contest_option",
            id=write_in,
            contest_id=contest,
            kind="write_in",
            ballot_label="Candidatos no registrados",
        )
        mine_votes = joined.filter((pl.col("state") == state) & (pl.col("district") == district))
        mine_units = located.filter((pl.col("state") == state) & (pl.col("district") == district))
        frames.append(
            d.district_frame(
                mine_votes, mine_units, options, write_in, str(casilla_set), str(district_unit), str(district_set)
            )
        )
    # An annulled district's count is not in INE's file: the contest keeps its place without results.
    for (ss, dd), ruling in annulled.items():
        if (ss, dd) in seen_districts:
            raise ValueError(f"{year}: annulled district {ss}-{dd} is in the file")
        seen_districts.add((ss, dd))
        state_name = str(state_names[ss])
        contest = p.id("contest", f"{day}:dip:mr:{ss}{dd:02d}")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            magnitude=1,
            electoral_system_id=mr_system,
            default_result_kind="counted",
        )
        name(contest, f"{year} Chamber of Deputies, {state_name}, district {dd}")
        name(contest, f"Diputación federal {year}, {state_name}, distrito {dd}", "es", "native")
        p.add("slug", path=f"{path}/{slug(state_name)}-district-{dd}", entity_id=contest, is_current=True)
        note(contest, ANNULLED.format(ruling=ruling))
        p.add(
            "citation",
            id=p.id("citation", contest, acuerdo),
            entity_id=contest,
            source_id=acuerdo,
            locator=d.locator(pages["annulled"]),
        )
    if len(seen_districts) != 300:
        raise ValueError(f"{year}: {len(seen_districts)} districts, not 300")

    # The list contests: the national vote and each circunscripción's seats.
    national = p.id("contest", f"{day}:dip:rp")
    p.add(
        "contest",
        id=national,
        election_id=election,
        kind="list_allocation",
        body_id=body,
        magnitude=200,
        electoral_system_id=rp_system,
        default_result_kind="counted",
    )
    name(national, f"{year} Chamber of Deputies, proportional representation lists")
    name(national, f"Diputaciones federales {year}, representación proporcional", "es", "native")
    p.add("slug", path=f"{path}/proportional", entity_id=national, is_current=True)
    declared = config["votes"]
    unregistered, null, total = declared["unregistered"], declared["null"], declared["total"]
    listed = {a: n for a, n in declared.items() if a not in ("unregistered", "null", "total")}
    if sum(listed.values()) + unregistered + null != total:
        raise ValueError(f"{year}: the declared figures do not add to {total}")

    def list_option(contest: str, label: str, abbrev: str, position: int) -> str:
        id = p.id("option", f"{day}:dip:rp:{label}")
        members = line_members(abbrev)
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="list",
            party_id=party(members[0]) if len(members) == 1 else None,
            alliance_id=alliance_of(members) if len(members) > 1 else None,
            ballot_position=position,
            ballot_label="–".join(members),
        )
        return id

    rp_options: dict[str, str] = {}
    for position, abbrev in enumerate(sorted(listed, key=lambda a: -listed[a]), 1):
        rp_options[abbrev] = list_option(national, abbrev, abbrev, position)
    national_set = make_set(
        national,
        "rp:national",
        True,
        acuerdo,
        config.get("national_method") or d.RP_NATIONAL_METHOD.format(page=pages["votes"]),
    )
    p.add(
        "citation",
        id=p.id("citation", national_set, acuerdo),
        entity_id=national_set,
        source_id=acuerdo,
        locator=d.locator(pages["votes"]),
    )
    nation = str(ref["nation"])
    values: list[tuple[str, str, str | None, int, int, int]] = []
    for abbrev, count in listed.items():
        values.append((national_set, nation, rp_options[abbrev], VOTES, count, 0))
    write_in = p.id("option", f"{day}:dip:rp:unregistered")
    p.add("contest_option", id=write_in, contest_id=national, kind="write_in", ballot_label="Candidatos no registrados")
    values.append((national_set, nation, write_in, VOTES, unregistered, 0))
    for measure, value in ((BALLOTS, total), (VALID, total - null), (INVALID, null)):
        values.append((national_set, nation, None, measure, value, 0))
    seat_total = 0
    for number in range(1, 6):
        contest = p.id("contest", f"{day}:dip:rp:{number}")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            magnitude=40,
            electoral_system_id=rp_system,
            allocation_parent_id=national,
            default_result_kind="counted",
        )
        name(contest, f"{year} Chamber of Deputies, proportional representation, circunscripción {number}")
        name(contest, f"Diputaciones federales {year}, circunscripción {number}", "es", "native")
        p.add("slug", path=f"{path}/proportional-{number}", entity_id=contest, is_current=True)
        note(contest, f"States: {d.CIRCUNSCRIPCIONES[number]}.")
        seat_set = make_set(
            contest,
            f"rp:{number}",
            True,
            acuerdo,
            config.get("seats_method") or d.RP_SEATS_METHOD.format(page=pages["seats"]),
            None,
        )
        p.add(
            "citation",
            id=p.id("citation", seat_set, acuerdo),
            entity_id=seat_set,
            source_id=acuerdo,
            locator=d.locator(pages["seats"]),
        )
        circ: dict[str, str] = {}
        for position, abbrev in enumerate(listed, 1):
            circ[abbrev] = list_option(contest, f"{number}:{abbrev}", abbrev, position)
        won = 0
        for abbrev, per in config["seats"].items():
            if per[number - 1]:
                won += per[number - 1]
                p.add("outcome", result_set_id=seat_set, option_id=circ[abbrev], kind="won", seats=per[number - 1])
        if won != 40:
            raise ValueError(f"{year}: circunscripción {number} seats add to {won}, not 40")
        seat_total += won
    print(year, "district winners", dict(sorted(winners.items(), key=lambda item: -item[1])), "list seats", seat_total)
    expected = config.get("district_winners")
    if expected and winners != expected:
        raise ValueError(f"{year}: district winners {winners} differ from the expected {expected}")

    p.frame("reporting_unit", pl.DataFrame([*district_rows, *new_units], schema_overrides={"parent_id": pl.String}))
    national_values = pl.DataFrame(
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
    )
    p.frame(
        "result_value",
        pl.concat([national_values, *(frame.select(national_values.columns) for frame in frames)]).with_columns(
            count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    p.external.update(used_parties)
    if year == 2006:
        p.accept("accounting.turnout", TURNOUT)
    return p
