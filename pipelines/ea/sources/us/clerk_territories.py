import re
import struct
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import clerk
from ea.store import DATA, connect

SOURCE = "us.clerk_territories"
VERSION = "1"
PAGES = DATA / "pages" / clerk.SOURCE
DERIVED = 1
DIGITIZED = 8
DELEGATES: dict[tuple[int, int], list[tuple[int, int, str, str]]] = {
    (1972, 51): [(10, 14, "DC", "general"), (15, 17, "GU", "general"), (18, 19, "VI", "general")],
    (1974, 42): [(28, 28, "GU", "general")],
    (1974, 43): [(0, 0, "VI", "general")],
    (1976, 55): [(10, 15, "DC", "general"), (16, 17, "GU", "general"), (18, 19, "VI", "general")],
    (1978, 41): [(6, 12, "DC", "general"), (13, 15, "GU", "general"), (16, 19, "VI", "general")],
    (1980, 70): [(10, 13, "AS", "general"), (14, 16, "AS", "runoff")],
    (1980, 71): [(0, 4, "DC", "general"), (5, 7, "GU", "general"), (8, 10, "VI", "general")],
}
DISTRICTS = {"PR": "7298", "DC": "1198", "GU": "6698", "VI": "7898", "AS": "6098"}
RUNOFF_DAY = date(1980, 11, 18)
NATIONAL = {
    "Democrat": ("democratic-party", "Democratic Party"),
    "Republican": ("republican-party", "Republican Party"),
    "Socialist Workers": ("socialist-workers-party", "Socialist Workers Party"),
}
LOCAL = {
    "Popular": "Popular Democratic Party",
    "Popular Democrat": "Popular Democratic Party",
    "Popular Democratic Party": "Popular Democratic Party",
    "New Progressive": "New Progressive Party",
    "Puerto Rico Independence": "Puerto Rican Independence Party",
    "Independentista Party": "Puerto Rican Independence Party",
    "Puerto Rican Independentist Party": "Puerto Rican Independence Party",
    "Coalitionist": "Coalitionist",
    "Unificacion": "Unificación",
    "United Parties": "United Parties",
    "Autentico": "Autentico Party",
    "Proletario": "Proletarian Party",
    "Estadista, Socialist, and Reformista Parties": "Estadista, Socialist, and Reformista Parties",
    "Puerto Rican Statehood Party": "Statehood Republican Party",
    "Statehood Republican Party": "Statehood Republican Party",
    "Statehood Republican": "Statehood Republican Party",
    "Republican Statehood": "Statehood Republican Party",
    "Puerto Rican Socialist Party": "Socialist Party",
    "Christian Action": "Christian Action Party",
    "Christian Action Party": "Christian Action Party",
    "People's": "People's Party",
    "Sovereigns Authentic": "Sovereigns Authentic",
    "Puerto Rico Union": "Puerto Rico Union",
    "Statehood": "Statehood",
    "D.C. Statehood": "D.C. Statehood",
    "Workers Party": "Workers",
    "U.S. Labor": "U.S. Labor",
    "Independent Citizens Movement": "Independent Citizens Movement",
}
ALIASES = {"hunkin": "faleomavaega"}
NOTES = {
    (1976, "DC"): (
        "caveat",
        (
            "The Clerk's book prints 12,149 votes for Walter E. Fauntroy and a few hundred to a few thousand for each "
            "other candidate, while the same page gives the District's presidential vote as about 168,000. The figures "
            "are stored as printed."
        ),
    ),
    (1980, "AS"): (
        "context",
        "No candidate won a majority on November 4, 1980, so the top two went to a runoff on November 18, 1980.",
    ),
}


def fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z ]", " ", plain.lower().replace("'", "")).split()
    return [token for token in tokens if len(token) > 1 and token not in ("jr", "sr")]


def emit() -> Path:
    lines = pl.read_parquet(raw.parsed(clerk.SOURCE) / "lines.parquet").filter(pl.col("state").is_null())
    races: dict[tuple[int, str, str], list[dict]] = {}
    for row in lines.filter(pl.col("heading") == "For Resident Commissioner").sort("year", "page", "line").to_dicts():
        if row["kind"] in ("candidate", "scattering"):
            races.setdefault((row["year"], "PR", "general"), []).append(row)
    for (year, page), spans in DELEGATES.items():
        for first, last, territory, stage in spans:
            for row in lines.filter(
                (pl.col("year") == year) & (pl.col("page") == page) & pl.col("line").is_between(first, last)
            ).to_dicts():
                if row["kind"] in ("candidate", "scattering"):
                    races.setdefault((year, territory, stage), []).append(row)
    for rows in races.values():
        for row in rows:
            if row["status"] not in ("agreed", "confirmed", "settled", "balanced", "disputed"):
                raise ValueError(f"{row['year']} page {row['page']} line {row['line']}: {row['status']}")
    with connect() as conn:
        seats = {
            value.removeprefix("house:"): entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'us.seat' and value = any(%s)",
                [[f"house:{code}" for code in DISTRICTS]],
            ).fetchall()
        }
        holders: dict[str, list[tuple[UUID, list[str]]]] = {code: [] for code in DISTRICTS}
        for code, person, text in conn.execute(
            """select x.value, t.person_id, n.text from tenure t
               join external_id x on x.entity_id = t.seat_id and x.scheme = 'us.seat'
               join name n on n.entity_id = t.person_id and n.is_preferred
               where x.value = any(%s)""",
            [[f"house:{code}" for code in DISTRICTS]],
        ).fetchall():
            holders[code.removeprefix("house:")].append((person, fold(text)))
        areas = {
            code: (unit, area, text)
            for code in DISTRICTS
            for unit, area, text in conn.execute(
                """select u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official'
                   where u.scheme_code = 'us.cd' and u.code = %s""",
                [DISTRICTS[code]],
            ).fetchall()
        }
        places = dict(
            conn.execute(
                """select p.iso_code, n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code = any(%s)""",
                [[f"US-{code}" for code in DISTRICTS]],
            ).fetchall()
        )
        plans = conn.execute("select id, valid_from, valid_to from boundary_set where scheme_code = 'us.cd'").fetchall()
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico Resident Commissioner 1940–1980 and the District of Columbia, Guam, Virgin Islands, "
            "and American Samoa delegates 1972–1980, digitized from the Clerk's statistics",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        body = cs.key("us.body", "house")
        fptp = cs.key("us.electoral_system", "fptp")
        two_round = cs.key("us.electoral_system", "two_round")
        if cs.minted:
            raise ValueError("the House and its electoral systems must already exist")
        sources: dict[int, UUID] = {}
        artifacts: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[int, int]] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for year, page in sorted({(row["year"], row["page"]) for rows in races.values() for row in rows}):
            day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
            book = raw.cached(clerk.SOURCE, clerk.BOOK.format(year=year))
            if year not in sources:
                title = (
                    f"Statistics of the {'Presidential and ' if year % 4 == 0 else ''}Congressional Election of "
                    f"{day:%B %-d, %Y}"
                )
                sources[year] = cs.source(clerk.PUBLISHER, title)
                if sources[year] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[year],
                        kind="official_body",
                        publisher=clerk.PUBLISHER,
                        title=title,
                        url=clerk.BOOK.format(year=year),
                        license="Public domain",
                    )
                artifacts[year] = cs.artifact(book, sources[year])
            image = PAGES / f"{book.sha256}-{page}.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                    + [str(book.path), str(image.with_suffix(""))],
                    check=True,
                )
            pages[(year, page)] = struct.unpack(">II", image.read_bytes()[16:24])
            page_id = cs.page(artifacts[year], page)
            if page_id in cs.minted:
                cs.add(
                    "source_page",
                    id=page_id,
                    artifact_id=artifacts[year],
                    page_number=page,
                    image_key=f"pages/{clerk.SOURCE}/{book.sha256}-{page}.png",
                    width=pages[(year, page)][0],
                    height=pages[(year, page)][1],
                )

        parties: dict[str, UUID | None] = {}
        created: set[UUID] = set()
        prior = dict(
            conn.execute("select value, entity_id from external_id where scheme = %s", [f"{SOURCE}.person"]).fetchall()
        )
        for (year, territory, stage), rows in sorted(races.items()):
            day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
            held = RUNOFF_DAY if stage == "runoff" else day
            unit, area, place = areas[territory]
            polity = cs.polity(f"US-{territory}")
            umbrella = cs.key("us.election", str(day))
            if umbrella in cs.minted:
                raise ValueError(f"no United States general election on {day}")
            parent = cs.key("us.election", f"{day}:{territory}")
            if parent in cs.minted and parent not in created:
                created.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=polity,
                    parent_id=umbrella,
                    kind="general",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{year} {places[f'US-{territory}']} general election")
                cs.add("slug", path=f"us/{territory.lower()}/{day}-general", entity_id=parent)
            election = parent
            if stage == "runoff":
                election = cs.key("us.election", f"{held}:{territory}:runoff")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="runoff",
                    date_from=held,
                    date_to=held,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{year} {places[f'US-{territory}']} runoff election")
                cs.add("slug", path=f"us/{territory.lower()}/{held}-runoff", entity_id=election)
            on = date(year + 1, 1, 3)
            plan = next(id for id, start, end in plans if start <= on and (end is None or on < end))
            contest_key = f"{held}:{territory}:house:0"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                seat_id=seats[territory],
                term="full",
                boundary_set_id=plan,
                magnitude=1,
                round_number=2 if stage == "runoff" else 1,
                previous_round_id=cs.key("us.contest", f"{day}:{territory}:house:0") if stage == "runoff" else None,
                electoral_system_id=two_round if territory == "AS" else fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} U.S. House {'runoff' if stage == 'runoff' else 'election'} in {place}")
            cs.add(
                "slug",
                path=f"us/{territory.lower()}/{held}-{'runoff' if stage == 'runoff' else 'general'}/house-at-large",
                entity_id=contest,
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=1,
            )
            if (year, territory) in NOTES and stage == "general":
                kind, text = NOTES[(year, territory)]
                cs.add("note", entity_id=result_set, kind=kind, body_md=text)
            counted = 0
            leader: tuple[int, UUID] | None = None
            for row in rows:
                votes = int(row["votes"])
                counted += votes
                token = slug(str(row["name"]))
                if row["kind"] == "scattering":
                    option = cs.key("us.option", f"{contest_key}:write-in")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in",
                        ballot_label=row["name"],
                    )
                else:
                    label = str(row["party"])
                    if label not in parties:
                        scope = us
                        if label.startswith("Independent") and label != "Independent Citizens Movement":
                            party, text = None, label
                        elif label in NATIONAL:
                            key, text = NATIONAL[label]
                            party = cs.key("us.party", key)
                        elif label in LOCAL:
                            text, scope = LOCAL[label], polity
                            party = cs.key("us.party", f"{territory}:{slug(text)}")
                        else:
                            raise ValueError(f"unmapped party {label!r}")
                        parties[label] = party
                        if party is not None and party in cs.minted and party not in created:
                            created.add(party)
                            cs.add("party", id=party, kind="party", polity_id=scope)
                            name(party, text)
                    party = parties[label]
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=row["name"],
                    )
                    words = [ALIASES.get(word, word) for word in fold(str(row["name"]))]
                    person = next(
                        (
                            holder
                            for holder, tokens in holders[territory]
                            if words[-1] in tokens or "".join(tokens).endswith(words[-1])
                            if tokens[0][0] == unicodedata.normalize("NFKD", str(row["name"]))[0].lower()
                        ),
                        None,
                    )
                    if person is not None and (old := prior.get(f"{territory}:{token}")) not in (None, person):
                        cs.merge(old, person)
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", f"{territory}:{token}")
                        if person in cs.minted and person not in created:
                            created.add(person)
                            cs.add("person", id=person)
                            name(person, str(row["name"]))
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        is_independent=party is None,
                    )
                    if leader is None or votes > leader[0]:
                        leader = (votes, option)
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=DIGITIZED,
                    value=votes,
                )
                if row["box"] is not None:
                    width, height = pages[(row["year"], row["page"])]
                    box = list(row["box"])
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        page_id=cs.page(artifacts[year], row["page"]),
                        source_id=None,
                        x0=box[0] / width / 3,
                        y0=box[1] / height / 3,
                        x1=box[2] / width / 3,
                        y1=box[3] / height / 3,
                    )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=0,
                flags=DERIVED,
                value=counted,
            )
            if leader is None or (territory == "AS" and stage == "general"):
                continue
            cs.add("outcome", result_set_id=result_set, option_id=leader[1], kind="elected", seats=1)
        return cs.write()
