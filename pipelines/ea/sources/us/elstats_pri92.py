import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats
from ea.store import DATA, connect

SOURCE = "us.elstats_pri92"
VERSION = "1"
YEAR = 1992
GENERAL = "1992-11-03"
PARTIES = {"democratic": "democratic-party", "republican": "republican-party", "liberty-union": "VT:liberty-union-party"}
LATIN = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")
METHOD = (
    "The FEC's book for 1992 prints presidential primaries only, so this contest is built from the state's own "
    "election archive. District totals are the sums of the archive's county rows (the archive's own total-votes-cast "
    "lines, which leave out blank votes and undervotes, agree with them). Names are as the archive spells them."
)


def emit() -> Path:
    frame = (
        pl.read_parquet(DATA / "parsed" / elstats.SOURCE / "counties.parquet")
        .filter(
            (pl.col("year") == YEAR)
            & pl.col("chamber").str.contains(":")
            & ~pl.col("state").is_in(list(elstats.HELD))
            & (pl.col("measure") == 1)
            & ~pl.col("candidate").str.contains(r"^(BLANK|UNDERVOTE|OVERVOTE|TOTAL)")
        )
        .with_columns(
            office=pl.col("chamber").str.extract(r"^(\w+):"),
            party=pl.col("chamber").str.extract(r":(.+)$"),
            candidate=pl.col("candidate").map_elements(lambda text: text.translate(LATIN), return_dtype=pl.String),
            district=pl.col("district").fill_null(0),
        )
        .group_by("state", "day", "office", "party", "district", "candidate", "writein")
        .agg(pl.col("votes").sum())
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="1992 House and Senate primaries in Colorado, Idaho, Virginia, and Vermont from the states' election "
            "archives",
            polity="US",
        )
        bodies = {"house": cs.key("us.body", "house"), "senate": cs.key("us.body", "senate")}
        sources: dict[str, UUID] = {}
        for state in frame["state"].unique().sort():
            tenant, publisher, _ = elstats.TENANTS[state]
            sources[state] = cs.source(publisher, elstats.TITLE)
            if sources[state] in cs.minted:
                raise ValueError(f"{state}: the archive's source must already exist")
            cs.artifact(raw.cached(elstats.SOURCE, elstats.url(tenant, YEAR)), sources[state])
        elections: set[UUID] = set()
        for (state, day, office, party, district), lines in frame.sort("state", "office", "party", "district").group_by(
            "state", "day", "office", "party", "district", maintain_order=True
        ):
            state_name = conn.execute(
                "select n.text from polity p join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en' "
                "where p.iso_code = %s",
                [f"US-{state}"],
            ).fetchone()[0]
            general = conn.execute(
                """select c.id, c.district_area_id, c.seat_id, c.boundary_set_id, c.electoral_system_id, c.term,
                          (select n.text from name n where n.entity_id = c.district_area_id and n.kind = 'official'
                           limit 1),
                          (select array_agg(distinct u.id) from result_set rs
                           join result_value v on v.result_set_k = rs.k and v.measure = 4
                           join reporting_unit u on u.k = v.unit_k where rs.contest_id = c.id and rs.is_default)
                   from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"{GENERAL}:{state}:senate" if office == "senate" else f"{GENERAL}:{state}:house:{district}"],
            ).fetchone()
            if general is None or len(general[7]) != 1:
                raise ValueError(f"{state} {office} {district}: no general contest with one district unit")
            contest_id, area, seat, plan, system, term, place, (unit,) = general
            known: dict[str, tuple[UUID, bool]] = {
                slug(text): (person, bool(incumbent))
                for text, person, incumbent in conn.execute(
                    """select n.text, y.person_id, y.is_incumbent from contest_option o
                       join candidacy y on y.option_id = o.id and y.role = 'head'
                       join name n on n.entity_id = y.person_id and n.is_preferred where o.contest_id = %s""",
                    [contest_id],
                ).fetchall()
            }
            surnames: dict[str, list[str]] = {}
            for text in known:
                surnames.setdefault(text.split("-")[-1], []).append(text)

            label = party.replace("-", " ").title()
            part = "senate" if office == "senate" else f"house-{district}" if district else "house-at-large"
            election = cs.key("us.election", f"{day}:{state}:primary")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{YEAR} {state_name} primary election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-primary", entity_id=election)
            key = f"{day}:{state}:{office}" + ("" if office == "senate" else f":{district}") + f":{party}"
            contest = cs.key("us.contest", key)
            if contest not in cs.minted:
                continue
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=bodies[office],
                district_area_id=area,
                seat_id=seat,
                term=term,
                boundary_set_id=plan,
                party_scope_id=cs.key("us.party", PARTIES[party]),
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{YEAR} U.S. {office.title()} {label} primary in {place if office == 'house' else state_name}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"us/{state.lower()}/{day}-primary/{part}-{party}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)

            ranked = lines.filter(~pl.col("writein")).sort("votes", descending=True)
            if ranked.height > 1 and ranked["votes"][0] == ranked["votes"][1]:
                raise ValueError(f"{key}: tied leaders")
            cells: list[tuple[UUID | None, int]] = []
            for name, votes in ranked.select("candidate", "votes").iter_rows():
                text = re.sub(
                    r"\b(Ii|Iii|Iv)\b",
                    lambda match: match[0].upper(),
                    re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), name.title()),
                )
                found = known.get(slug(text))
                if found is None and len(options := surnames.get(slug(text).split("-")[-1], [])) == 1:
                    found = known[options[0]]
                person = found[0] if found else cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                if found is None:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", PARTIES[party]),
                    ballot_label=text,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=False,
                    is_incumbent=found[1] if found else False,
                )
                if votes == ranked["votes"][0]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won")
                cells.append((option, votes))
            written = int(lines.filter(pl.col("writein"))["votes"].sum())
            if written:
                option = cs.key("us.option", f"{key}:write_in")
                cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                cells.append((option, written))
            cells.append((None, sum(votes for _, votes in cells)))
            for option, votes in cells:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1 if option else 4,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
