import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import connect
from ea.typed import one

SOURCE = "us.oh_appeals"
VERSION = "1"
PUBLISHER = "Ohio Secretary of State"
PAGE = "https://www.ohiosos.gov/elections/election-results-and-data/"
BOOKS = {
    date(
        2024, 11, 5
    ): "https://web.archive.org/web/20241207004035id_/https://www.ohiosos.gov/globalassets/elections/2024/gen/official/statewide-race-summary.xlsx",
}
SHEET = "Judge of Court of Appeals"
TITLE = "Judge of the Court of Appeals"
HEADING = re.compile(
    r"Judge of the Court of Appeals - District (\d+)\s+(Term Commencing|Unexpired Term Ending) (\d\d/\d\d/\d{4})"
)
CANDIDATE = re.compile(r"^(.+?)\s*\(([A-Z]+)\)$")
PARTIES = {"D": "democratic-party", "R": "republican-party"}
NOTE = (
    "County totals from the Ohio Secretary of State's official canvass of the general election, "
    "Judge of the Court of Appeals sheet. The district's voters elect one judge for each term on the ballot."
)


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def ordinal(number: int) -> str:
    return f"{number}{'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')}"


def fetch() -> None:
    for url in BOOKS.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")


def races() -> list[dict[str, Any]]:
    found = []
    for day, url in BOOKS.items():
        rows = pl.read_excel(
            raw.cached(SOURCE, url).path, sheet_name=SHEET, has_header=False, infer_schema_length=0
        ).rows()
        titles = {i: " ".join(str(v).split()) for i, v in enumerate(rows[0]) if v is not None and i > 5}
        names = {i: " ".join(str(v).split()) for i, v in enumerate(rows[1]) if v is not None and i > 5}
        total = next(row for row in rows if str(row[0]).strip().lower() == "total")
        body = [row for row in rows[2:] if row[0] is not None and row[1] is not None]
        starts = sorted(titles)
        for position, start in enumerate(starts):
            heading = HEADING.fullmatch(titles[start])
            if heading is None:
                raise ValueError(titles[start])
            end = starts[position + 1] if position + 1 < len(starts) else max(names) + 1
            candidates = []
            for i in range(start, end):
                match = CANDIDATE.fullmatch(names[i])
                if match is None:
                    raise ValueError(names[i])
                places = {str(row[0]).strip(): int(str(row[i])) for row in body}
                if sum(places.values()) != int(str(total[i])):
                    raise ValueError(f"{names[i]}: counties do not add to the total")
                candidates.append((match[1], match[2], places))
            when = datetime.strptime(heading[3], "%m/%d/%Y").date()
            found.append(
                {
                    "day": day,
                    "url": url,
                    "district": int(heading[1]),
                    "remainder": heading[2] != "Term Commencing",
                    "when": when,
                    "candidates": candidates,
                }
            )
    return found


def emit() -> Path:
    rows = races()
    members: dict[int, set[str]] = defaultdict(set)
    for race in rows:
        for _, _, places in race["candidates"]:
            members[int(race["district"])].update(place for place, votes in places.items() if votes > 0)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio Court of Appeals general election contests of 2024 by county and district, "
            "from the Secretary of State's official canvass",
            polity="US-OH",
        )
        sources: dict[str, UUID] = {}
        for day, url in BOOKS.items():
            title = f"{day.year} general election official canvass: statewide-race-summary.xlsx"
            sources[url] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGE,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        county_units = {}
        county_codes = {}
        for text, unit, code in conn.execute(
            """select n.text, u.id, u.code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '39%%'"""
        ).fetchall():
            county_units[flat(text)] = unit
            county_codes[flat(text)] = code
        state_area, state_polity = one(
            conn.execute(
                """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-OH'"""
            )
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/oh/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")

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

        def claim(base: str) -> str:
            path, count = base, 1
            while path in taken:
                count += 1
                path = f"{base}-{count}"
            taken.add(path)
            return path

        areas: dict[int, UUID] = {}
        for number in sorted(members):
            code = f"OH:appellate-{number}"
            label = f"{ordinal(number)} Court of Appeals District"
            area = cs.area("us.judicial_district", code)
            areas[number] = area
            polity = cs.key("us.polity", f"judicial:{code}")
            if area not in cs.minted:
                continue
            cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
            name(polity, f"Ohio {label}")
            cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
            cs.add("slug", path=claim(f"us/oh/{slug(label)}"), entity_id=polity)
            cs.add("area", id=area, scheme_code="us.judicial_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=state_area)
            name(area, f"{label}, OH")
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
            codes = [county_codes[flat(place)] for place in sorted(members[number])]
            geom, point = one(
                conn.execute(
                    """with latest as (
                       select distinct on (ac.code) ac.code, g.geom from area_geometry g
                       join area_code ac on ac.area_id = g.area_id
                       where ac.code = any(%s) order by ac.code, g.valid_from desc nulls last)
                   select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))), 'hex'),
                          encode(st_asewkb(st_pointonsurface(st_makevalid(st_union(geom)))), 'hex') from latest""",
                    [codes],
                )
            )
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                valid_from_precision="unknown",
                valid_to=None,
                valid_to_precision="unknown",
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )

        office = cs.key("us.office", f"{slug(TITLE)}:OH")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-OH"), kind="other")
            name(office, f"{TITLE}, OH")
        persons: set[str] = set()
        made = 0
        for race in sorted(rows, key=lambda item: (item["day"], item["district"], item["remainder"], item["when"])):
            day, district, when, remainder = race["day"], int(race["district"]), race["when"], bool(race["remainder"])
            term = f"unexpired-term-ending-{when}" if remainder else f"term-from-{when}"
            contest_key = f"{day}:OH:{slug(TITLE)}:{district}:{term}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:OH")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=areas[district],
                term="remainder" if remainder else "full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            spelled = f"{when:%B} {when.day}, {when.year}"
            name(
                contest,
                f"{day.year} Ohio Court of Appeals, {ordinal(district)} District "
                + (f"(unexpired term ending {spelled})" if remainder else f"(term from {spelled})"),
            )
            if contest in cs.minted:
                cs.add(
                    "slug",
                    path=claim(
                        f"us/oh/{day}-general/judge-of-the-court-of-appeals-{ordinal(district)}-district-{term}"
                    ),
                    entity_id=contest,
                )
            cs.add("citation", entity_id=contest, source_id=sources[str(race["url"])])
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[str(race["url"])],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            totals: dict[str, int] = {}
            values: list[tuple[str, str, str | None, int, int, int, int, int]] = []
            by_place: dict[str, int] = defaultdict(int)
            for position, (choice, code, places) in enumerate(race["candidates"]):
                first, last = identity(choice)
                person_key = f"OH:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, display(choice))
                party = PARTIES.get(code)
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "contest_option",
                    id=options[choice],
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
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
                totals[choice] = sum(places.values())
                for place, votes in places.items():
                    if place not in members[district]:
                        if votes:
                            raise ValueError(f"{place} votes outside district {district}")
                        continue
                    unit = county_units[flat(place)]
                    by_place[str(unit)] += votes
                    values.append((str(result), str(unit), str(options[choice]), 0, 1, 0, 0, votes))
            values += [(str(result), unit, None, 0, 4, 0, 0, votes) for unit, votes in by_place.items()]
            winner = max(totals, key=totals.__getitem__)
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options[winner],
                candidacy_id=None,
                kind="elected",
                seats=1,
            )
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
            print(contest_key, sorted(totals.items(), key=lambda item: -item[1]))
        print(made)
        return cs.write()
