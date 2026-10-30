from datetime import date
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea.changeset import Changeset, slug
from ea.sources.us import ceda, govmint


def counties(conn: Connection, state_fips: str) -> dict[str, tuple[UUID, UUID, str, str]]:
    return {
        ceda._core(text): (polity, area, path, code)
        for polity, area, text, path, code in conn.execute(
            """select t.polity_id, a.id, n.text, s.path, c.code from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = t.polity_id and s.is_current
               where a.scheme_code = 'us.county' and left(c.code, 2) = %s""",
            [state_fips],
        ).fetchall()
    }


def races(
    conn: Connection,
    cs: Changeset,
    pipeline: str,
    state: str,
    day: date,
    frame: pl.DataFrame,
    source: UUID,
    state_fips: str,
) -> int:
    held = counties(conn, state_fips)
    slugs = dict(
        conn.execute("select path, entity_id from slug where path like %s", [f"us/{state.lower()}/%"]).fetchall()
    )
    system = cs.key("us.electoral_system", "fptp")
    parent = cs.key("us.election", f"{day}:{state}")
    if parent in cs.minted:
        raise SystemExit("state election missing")
    made: dict[tuple[str, str], tuple[UUID, UUID, str, str, str] | None] = {}
    elections: set[UUID] = set()
    kept = 0
    for (county, district, seat_label), people in frame.group_by(["county", "district", "seat"], maintain_order=True):
        first = people.row(0, named=True)
        home = held.get(ceda._core(f"{county} County"))
        if home is None:
            print("no county:", county)
            continue
        if (county, district) not in made:
            special = govmint.special(conn, cs, state, home, district, slugs)
            if special is None:
                print("no district:", district)
                continue
            made[(county, district)] = special
            rival = conn.execute(
                """select x.value from contest c join election e on e.id = c.election_id
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where e.polity_id = %s and e.date_from = %s""",
                [special[0], day],
            ).fetchall()
            if any(not value.startswith(f"{day}:{state}:{special[4]}:") for (value,) in rival):
                print("held elsewhere:", district)
                made[(county, district)] = None
        chosen = made[(county, district)]
        if chosen is None:
            continue
        polity, area, place, path, code = chosen
        election = cs.key("us.election", f"{day}:{state}:{code}")
        if election in cs.minted and election not in elections:
            elections.add(election)
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind="general",
                date_from=day,
                date_to=day,
                date_precision="day",
                status="scheduled",
            )
            cs.add(
                "name",
                entity_id=election,
                text=f"{day.year} {place} general election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"{path}/{day}-general", entity_id=election)
        role = first["role"]
        title = f"{place} {role} {seat_label}".strip()
        stem = f"{slug(role)}-{slug(seat_label)}".strip("-")
        owner = cs.key("us.office", f"{state}:{code}:{stem}")
        contest_key = f"{day}:{state}:{code}:{stem}"
        contest = cs.key("us.contest", contest_key)
        contest_path = f"{path}/{day}-general/{stem}"
        if slugs.get(contest_path, contest) != contest:
            print("slug taken:", contest_path)
            continue
        slugs[contest_path] = contest
        if owner in cs.minted:
            cs.add("office", id=owner, polity_id=polity, kind="other")
            cs.add("name", entity_id=owner, text=title, lang="en", kind="official", is_preferred=True)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=owner,
            district_area_id=area,
            magnitude=first["seats"],
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
            term="remainder" if first["unexpired"] else "full",
        )
        cs.add(
            "name",
            entity_id=contest,
            text=f"{day.year} {title} election",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        if contest in cs.minted:
            cs.add("slug", path=contest_path, entity_id=contest)
        cs.add("citation", entity_id=contest, source_id=source, locator=None)
        for row in people.iter_rows(named=True):
            ballot = row["name"]
            token = slug(ballot)
            person = cs.key(f"{pipeline}.person", f"{contest_key}:{token}")
            option = cs.key("us.option", f"{contest_key}:{token}")
            if person in cs.minted:
                cs.add("person", id=person)
                cs.add("name", entity_id=person, text=ballot, lang="en", kind="official", is_preferred=True)
            cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
            candidacy = cs.key("us.candidacy", f"{contest_key}:{token}:head")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=ballot,
                is_independent=True,
            )
            cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
            kept += 1
    return kept
