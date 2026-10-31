import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import mi_cenr_judicial as base
from ea.sources.us import mi_cenr_statewide
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.mi_cenr_appeals"
VERSION = "1"
TITLE = "Judge of Court of Appeals"
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
ERAS = [(None, date(2002, 1, 1)), (date(2002, 1, 1), date(2012, 1, 1)), (date(2012, 1, 1), None)]
SEATS = re.compile(r"\((\d+)\) Position")
NOTE = (
    "County totals from the Michigan Department of State's official general election results by county, "
    "summed over the counties of the district as it stood that year. Candidates are nominated by party conventions "
    "but stand on the nonpartisan section of the ballot, and the district's voters choose as many candidates as "
    "there are seats; the winners are the candidates with the most votes."
)


def era(day: date) -> int:
    return 0 if day.year < 2002 else 1 if day.year < 2012 else 2


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for year in [*base.CACHED, *base.FETCHED]:
        frame = pl.read_csv(base.path(year), separator="\t", infer_schema=False, quote_char=None).filter(
            (pl.col("OfficeCode(Text)") == "14") & pl.col("CandidateVotes").is_not_null()
        )
        for row in frame.iter_rows(named=True):
            if (row["WriteIn(W)/Uncommitted(Z)"] or "").strip() == "W":
                continue
            name = " ".join(
                part.strip()
                for part in (row["CandidateFirstName"], row["CandidateMiddleName"], row["CandidateLastName"])
                if part is not None and part.strip()
            )
            description = row["OfficeDescription"]
            partial = base.PARTIAL.search(description)
            found.append(
                {
                    "day": date.fromisoformat(row["ElectionDate"][:10]),
                    "district": int(row["DistrictCode(Text)"]) // 100,
                    "ends": int(partial[3]) if partial else None,
                    "description": description,
                    "candidate": re.sub(r"\s+", " ", name).upper(),
                    "county": row["CountyName"],
                    "votes": int(row["CandidateVotes"]),
                }
            )
    return found


def emit() -> Path:
    rows = lines()
    members: dict[tuple[int, int], set[str]] = defaultdict(set)
    for row in rows:
        members[(row["district"], era(row["day"]))].add(base.flat(str(row["county"])))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan Court of Appeals general election contests 1998–2020 by county and district, "
            "from the Department of State's official results",
            polity="US-MI",
        )
        source = cs.source(mi_cenr_statewide.PUBLISHER, mi_cenr_statewide.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=mi_cenr_statewide.PUBLISHER,
            title=mi_cenr_statewide.TITLE,
            url="https://miboecfr.nictusa.com/election/results/",
            license="Public record",
        )
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for year in [*base.CACHED, *base.FETCHED]:
            raw_source = mi_cenr_statewide.SOURCE if year in base.CACHED else base.SOURCE
            cs.artifact(raw.cached(raw_source, base.url(year)), source)
        county_units = {}
        county_areas = {}
        for text, unit, area_id, code in conn.execute(
            """select n.text, u.id, u.area_id, u.code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '26%%'"""
        ).fetchall():
            county_units[base.flat(text)] = unit
            county_areas[base.flat(text)] = code
        state_area, state_polity = conn.execute(
            """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-MI'"""
        ).fetchone()
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/mi/%%'").fetchall()}
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}

        def name(entity: UUID, text: str) -> None:
            found = conn.execute(
                """select n.id, n.text, c.pipeline from name n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.is_preferred""",
                [entity],
            ).fetchone()
            if found is not None and (found[1] == text or found[2] != SOURCE):
                return
            if found is not None:
                cs.add("name_drop", name_id=found[0], entity_id=entity, text=found[1], reason="Name restyled")
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        areas: dict[int, UUID] = {}
        for number, ordinal in ORDINALS.items():
            code = f"MI:appellate-{number}"
            label = f"{ordinal} Court of Appeals District"
            area = cs.area("us.judicial_district", code)
            areas[number] = area
            polity = cs.key("us.polity", f"judicial:{code}")
            if area in cs.minted:
                cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
                name(polity, f"Michigan {label}")
                cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
                path = f"us/mi/{slug(label)}"
                if path in taken:
                    raise ValueError(f"{label} would take {path}")
                taken.add(path)
                cs.add("slug", path=path, entity_id=polity)
                cs.add("area", id=area, scheme_code="us.judicial_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=state_area)
                name(area, f"{label}, MI")
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add(
                    "reporting_unit",
                    id=cs.unit("us.judicial_district", code),
                    polity_id=polity,
                    scheme_code="us.judicial_district",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                for index, (start, end) in enumerate(ERAS):
                    codes = [county_areas[flat] for flat in sorted(members[(number, index)])]
                    geom, point = conn.execute(
                        """with latest as (
                               select distinct on (ac.code) ac.code, g.geom from area_geometry g
                               join area_code ac on ac.area_id = g.area_id
                               where ac.code = any(%s) order by ac.code, g.valid_from desc nulls last)
                           select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))), 'hex'),
                                  encode(st_asewkb(st_pointonsurface(st_makevalid(st_union(geom)))), 'hex') from latest""",
                        [codes],
                    ).fetchone()
                    cs.add(
                        "area_geometry",
                        area_id=area,
                        valid_from=start,
                        valid_from_precision="year" if start else "unknown",
                        valid_to=end,
                        valid_to_precision="year",
                        geom=geom,
                        label_point=point,
                        quality="official",
                        source_id=census,
                    )

        groups: dict[tuple[date, int, int | None], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], row["district"], row["ends"])].append(row)
        office = cs.key("us.office", f"{slug(TITLE)}:MI")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-MI"), kind="other")
            name(office, f"{TITLE}, MI")
        persons: set[str] = set()
        made = 0
        for (day, district, ends), group in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or 0)
        ):
            descriptions = {str(row["description"]) for row in group}
            magnitude = sum(int(SEATS.search(text)[1]) for text in descriptions)
            ordinal = ORDINALS[district]
            suffix = f"{district}" + (f"-partial-term-ending-{ends}" if ends else "")
            contest_key = f"{day}:MI:{slug(TITLE)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:MI")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=areas[district],
                term="remainder" if ends else "full",
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp"] if magnitude == 1 else systems["block"],
                default_result_kind="counted",
            )
            name(
                contest,
                f"{day.year} Michigan Court of Appeals, {ordinal} District"
                + (f" (term ending {ends})" if ends else ""),
            )
            if contest in cs.minted:
                path_base = f"us/mi/{day}-general/judge-of-court-of-appeals-{ordinal}-district" + (
                    f"-partial-term-ending-{ends}" if ends else ""
                )
                path, count = path_base, 1
                while path in taken:
                    count += 1
                    path = f"{path_base}-{count}"
                taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for row in group:
                by_choice[str(row["candidate"])][str(row["county"])] += int(row["votes"])
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, choice in enumerate(by_choice):
                first, last = identity(choice)
                person_key = f"MI:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, display(choice))
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "contest_option",
                    id=options[choice],
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position + 1,
                    ballot_label=display(choice),
                )
                candidacies[choice] = cs.key("us.candidacy", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "candidacy",
                    id=candidacies[choice],
                    person_id=person,
                    option_id=options[choice],
                    role="head",
                    ballot_name=display(choice),
                    is_independent=False,
                )
            totals = {choice: sum(by_unit.values()) for choice, by_unit in by_choice.items()}
            for winner in sorted(totals, key=totals.__getitem__, reverse=True)[:magnitude]:
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options[winner],
                    candidacy_id=candidacies[winner],
                    kind="elected",
                    seats=1,
                )
            by_place: dict[str, int] = defaultdict(int)
            values = []
            for choice, by_unit in by_choice.items():
                for place, votes in by_unit.items():
                    unit = county_units[base.flat(place)]
                    by_place[str(unit)] += votes
                    values.append((str(result), str(unit), str(options[choice]), 0, 1, 0, 0, votes))
            values += [(str(result), unit, None, 0, 4, 0, 0, votes) for unit, votes in by_place.items()]
            cs.frame(
                "result_value",
                pl.DataFrame(
                    values,
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
                    orient="row",
                ),
            )
            made += 1
            print(contest_key, sorted(totals.items(), key=lambda item: -item[1])[:3])
        print(made, office)
        return cs.write()
