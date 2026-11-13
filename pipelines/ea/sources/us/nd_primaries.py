import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.nd_sos import MANUALS
from ea.sources.us.nd_sos import SOURCE as CACHE
from ea.store import DATA, connect

SOURCE = "us.nd_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Dakota Secretary of State"
MANUAL = MANUALS[(1918, "treasurer")]
DAYS = {1914: date(1914, 6, 24), 1916: date(1916, 6, 28), 1918: date(1918, 6, 26)}
GENERALS = {1914: date(1914, 11, 3), 1916: date(1916, 11, 7), 1918: date(1918, 11, 5)}
PARTIES = {
    "republican": ("Republican", "republican-party"),
    "democratic": ("Democratic", "democratic-party"),
    "socialist": ("Socialist", "socialist-party-of-america"),
}
TITLES = {
    "senate": "U.S. Senate",
    "governor": "governor",
    "lieutenant-governor": "lieutenant governor",
    "secretary-of-state": "secretary of state",
    "auditor": "state auditor",
    "treasurer": "state treasurer",
    "attorney-general": "attorney general",
    "insurance-commissioner": "insurance commissioner",
    "commissioner-of-agriculture": "commissioner of agriculture and labor",
    "railroad-commissioner": "railroad commissioner",
    **{f"house:{n}": f"U.S. House District {n}" for n in range(1, 4)},
    "public-service-commissioner": "railroad commissioner",
}
SAME = {"H. Halverson": "Halvor Halvorson", "Frank Milhollan": "Frank Milholland"}
ERRATA = {
    (1914, "republican", "Thomas Hall"),
    (1914, "democratic", "M. F. Hegge"),
    (1914, "democratic", "Scott Cameron"),
    (1914, "democratic", "Sam A. Hall"),
    (1914, "democratic", "H. Halverson"),
    (1914, "socialist", "J. A. Williams"),
    (1914, "socialist", "J. E. Kulstad"),
    (1914, "socialist", "J. L. Koeppler"),
    (1916, "republican", "A. T. Kraabel"),
    (1916, "republican", "Oscar J. Sorlie"),
    (1916, "republican", "Thomas Hall"),
    (1916, "republican", "Carl O. Jorgenson"),
    (1916, "republican", "Carl R. Kositzky"),
    (1916, "republican", "M. P. Johnson"),
    (1916, "republican", "W. H. Mann"),
    (1916, "democratic", "G. S. Wooledge"),
    (1916, "democratic", "John Harvey"),
    (1916, "democratic", "Charles Hein"),
    (1916, "socialist", "John Fleckten"),
    (1918, "republican", "Lyman D. Page"),
    (1918, "republican", "Geo. P. Hommes"),
    (1918, "republican", "John O. Hanchett"),
    (1918, "democratic", "Fred Bartholomew"),
}


def plain(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def parse() -> None:
    frame = pl.read_csv(Path(__file__).with_name("nd_primary_reads.tsv"), separator="\t")
    totals = frame.filter(pl.col("county") == "Total")
    counted = frame.filter(pl.col("county") != "Total")
    for year, party, office, candidate, printed in totals.select(
        "year", "party", "office", "candidate", "votes"
    ).iter_rows():
        found = counted.filter(
            (pl.col("year") == year)
            & (pl.col("party") == party)
            & (pl.col("office") == office)
            & (pl.col("candidate") == candidate)
        )["votes"].sum()
        if found != printed:
            print(f"{year} {party} {office} {candidate}: counties sum to {found}, printed total {printed}")
            if abs(found - printed) > max(20, printed // 100) and (year, party, candidate) not in ERRATA:
                raise ValueError(f"{year} {party} {candidate}: counties sum to {found}, the Manual prints {printed}")
    with connect() as conn:
        codes = {
            plain(name): code
            for name, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '38%%'"""
            ).fetchall()
        }
    frame = counted.with_columns(
        day=pl.col("year").replace_strict(DAYS, return_dtype=pl.Date),
        county=pl.col("county").map_elements(lambda c: codes[plain(c)], return_dtype=pl.String),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("year", "party", "office", maintain_order=True).agg(pl.col("votes").sum()).rows())


def fetch() -> None:
    raw.fetch(CACHE, MANUAL[1], suffix=".pdf")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Dakota's 1914, 1916, and 1918 statewide and congressional party primaries by county "
            "from the 1919 Legislative Manual",
            polity="US",
        )

        def surname(value: str) -> str:
            words = [word for word in value.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, MANUAL[0])
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=MANUAL[0],
            url=MANUAL[2],
            license="Public domain",
        )
        cs.artifact(raw.cached(CACHE, MANUAL[1]), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '38%%'"
            ).fetchall()
        )
        if len(counties) != 53:
            raise ValueError("North Dakota needs 53 counties")
        dakota = cs.polity("US-ND")
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-ND'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        people: dict[str, set[UUID]] = {}
        years: dict[UUID, list[int]] = {}
        for person, text, spans in conn.execute(
            """select cd.person_id, n.text, array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:ND:([a-z-]+|house:\\d)(:[a-z]+)?$'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if text.split():
                people.setdefault(plain(text), set()).add(person)
                people.setdefault(f"{plain(text.split()[0])[:1]}|{surname(text)}", set()).add(person)
                years[person] = spans
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {key: cs.key("us.party", slug_) for key, (_, slug_) in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':ND:'")
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':ND$'")
        )
        made: set[UUID] = set()
        chosen: dict[str, UUID] = {}
        for (day, party, office), lines in frame.sort("day").group_by("day", "party", "office", maintain_order=True):
            year = day.year
            label, _ = PARTIES[party]
            key = f"{day}:ND:{office}:{party}"
            election = elections.get(f"{day}:ND:primary")
            if election is None:
                election = cs.key("us.election", f"{day}:ND:primary")
                elections[f"{day}:ND:primary"] = election
                cs.add(
                    "election",
                    id=election,
                    polity_id=dakota,
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} North Dakota primary election")
                cs.add("slug", path=f"us/nd/{day}-primary", entity_id=election)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            if contest is None:
                general = contests[f"{GENERALS[year]}:ND:{office}"]
                ((office_id, body_id, seat_id, district, term, magnitude, system),) = conn.execute(
                    """select office_id, body_id, seat_id, district_area_id, term, magnitude, electoral_system_id
                       from contest where id = %s""",
                    [general],
                ).fetchall()
                contest = cs.key("us.contest", key)
                contests[key] = contest
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    body_id=body_id,
                    seat_id=seat_id,
                    district_area_id=district if office.startswith("house") else state,
                    term=term,
                    magnitude=magnitude,
                    round_number=1,
                    party_scope_id=parties[party],
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(
                    contest, f"{year} {TITLES[office][:1].upper() + TITLES[office][1:]} {label} primary in North Dakota"
                )
                cs.add("slug", path=f"us/nd/{day}-primary/{office.replace(':', '-')}-{party}", entity_id=contest)
            else:
                ((magnitude,),) = conn.execute("select magnitude from contest where id = %s", [contest]).fetchall()
            options = conn.execute(
                """select id, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind = 'candidate'""",
                [contest],
            ).fetchall()
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for candidate, votes in (
                lines.group_by("candidate", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [option for option, text in options if (text or "") == candidate]
                if matches:
                    option = matches[0]
                else:
                    known_as = SAME.get(candidate, candidate)
                    known = {
                        one
                        for one in people.get(plain(known_as), set())
                        | people.get(f"{plain(known_as.split()[0])[:1]}|{surname(known_as)}", set())
                        if one in made or min((abs(other - year) for other in years[one]), default=99) <= 30
                    }
                    person = chosen.get(plain(candidate)) or (
                        min(
                            known,
                            key=lambda one: min((abs(other - year) for other in years.get(one, [year])), default=0),
                        )
                        if known
                        else cs.key("us.nd_sos.person", f"ND:{slug(candidate)}")
                    )
                    chosen[plain(candidate)] = person
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                    elif person not in made:
                        print(f"{key}: {candidate} is joined to an existing person")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties[party],
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                if option in totals:
                    raise ValueError(f"{key}: two columns share an option with {candidate}")
                totals[option] = votes
                picked[candidate] = option
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            for county, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[county],
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            for winner in sorted(totals, key=totals.__getitem__, reverse=True)[: magnitude or 1]:
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=None)
        return cs.write()
