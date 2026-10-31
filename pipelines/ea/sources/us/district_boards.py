from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import govmint
from ea.store import connect

DAY = date(2026, 11, 3)


def parent_for(conn: Connection, path: str) -> tuple[UUID, UUID, str, str] | None:
    row = conn.execute(
        """select t.polity_id, t.area_id, c.code from polity_territory t
           join slug s on s.entity_id = t.polity_id and s.path = %s
           join area_code c on c.area_id = t.area_id limit 1""",
        [path],
    ).fetchone()
    return None if row is None else (row[0], row[1], path, row[2])


def emit(
    *,
    source_id: str,
    version: str,
    state: str,
    summary: str,
    sources: dict[str, tuple[str, str]],
    frame: pl.DataFrame,
) -> Path:
    lower = state.lower()
    with connect() as conn:
        top = parent_for(conn, f"us/{lower}")
        assert top is not None
        existing: dict[str, tuple[UUID, UUID, str, str]] = {}
        for polity, area, text, path, code in conn.execute(
            """select p.id, t.area_id, n.text, s.path, c.code from polity p
               join polity_territory t on t.polity_id = p.id
               join area_code c on c.area_id = t.area_id
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where p.kind = 'special_district' and s.path like %s""",
            [f"us/{lower}/%"],
        ).fetchall():
            existing[govmint.fold(text)] = (polity, area, path, code)
        slugs = dict(conn.execute("select path, entity_id from slug where path like %s", [f"us/{lower}/%"]).fetchall())
        cs = Changeset(conn, pipeline=source_id, version=version, summary=summary, polity=f"US-{state}")
        system = cs.key("us.electoral_system", "fptp")
        source_ids: dict[str, UUID] = {}
        for url, (publisher, title) in sources.items():
            source_ids[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=source_ids[url],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(source_id, url), source_ids[url])
        parent = cs.key("us.election", f"{DAY}:{state}")
        made: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        elections: set[UUID] = set()
        kept = 0
        keys = ["district", "seat", "remainder", "role", "url"]
        for group_key, people in frame.group_by(keys, maintain_order=True):
            district, seat, remainder, word, _ = group_key
            first = people.row(0, named=True)
            if district not in made:
                twin = existing.get(govmint.fold(district))
                if twin is not None:
                    made[district] = (twin[0], twin[1], district, twin[2], twin[3])
                else:
                    home = top
                    if first["county"] is not None:
                        home = None
                        for guess in (slug(first["county"]), slug(f"{first['county']} County")):
                            home = parent_for(conn, f"us/{lower}/{guess}")
                            if home is not None:
                                break
                        if home is None:
                            print("no county:", first["county"])
                            continue
                    special = govmint.special(conn, cs, state, home, district, slugs, first["function"])
                    if special is None:
                        print("no district:", district)
                        continue
                    made[district] = special
            polity, area, place, path, code = made[district]
            election = cs.key("us.election", f"{DAY}:{state}:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = f"{seat} (unexpired term)" if remainder else seat
            name = f"{place} {word} {label}".replace("  ", " ").strip()
            token = "-".join(part for part in (slug(word), slug(seat)) if part) + ("-unexpired" if remainder else "")
            owner = cs.key("us.office", f"{state}:{code}:{token}")
            contest_key = f"{DAY}:{state}:{code}:{token}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{token}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
            if owner in cs.minted:
                cs.add("office", id=owner, polity_id=polity, kind="other")
                cs.add("name", entity_id=owner, text=name, lang="en", kind="official", is_preferred=True)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                district_area_id=area,
                magnitude=first["vote"],
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder" if remainder else "full",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {name} election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            source = source_ids[first["url"]]
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            for person_row in people.iter_rows(named=True):
                ballot = person_row["name"]
                who = slug(ballot)
                person = cs.key(f"{source_id}.person", f"{contest_key}:{who}")
                option = cs.key("us.option", f"{contest_key}:{who}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=ballot, lang="en", kind="official", is_preferred=True)
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                candidacy = cs.key("us.candidacy", f"{contest_key}:{who}:head")
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
        print(f"{kept} candidates")
        return cs.write()
