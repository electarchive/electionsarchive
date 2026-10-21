import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.wikipedia_guam_governors import count, fields_of, plain, templates_of
from ea.store import DATA, connect

SOURCE = "us.wikipedia_house_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "United States House special election articles, results boxes"
NOTE = (
    "District totals as tabulated in the results box of the English Wikipedia's article on each special election "
    "to the United States House of Representatives, which cites the state's certified returns."
)
STATES = {
    "AL": ("Alabama", "01"),
    "AR": ("Arkansas", "05"),
    "AZ": ("Arizona", "04"),
    "CA": ("California", "06"),
    "CO": ("Colorado", "08"),
    "FL": ("Florida", "12"),
    "GA": ("Georgia", "13"),
    "HI": ("Hawaii", "15"),
    "IN": ("Indiana", "18"),
    "KY": ("Kentucky", "21"),
    "LA": ("Louisiana", "22"),
    "MN": ("Minnesota", "27"),
    "MO": ("Missouri", "29"),
    "MS": ("Mississippi", "28"),
    "NJ": ("New Jersey", "34"),
    "NV": ("Nevada", "32"),
    "NY": ("New York", "36"),
    "OH": ("Ohio", "39"),
    "OR": ("Oregon", "41"),
    "SC": ("South Carolina", "45"),
    "TX": ("Texas", "48"),
    "VA": ("Virginia", "51"),
    "WI": ("Wisconsin", "55"),
}
CONTESTS = [
    ("AL", 1, "2013 Alabama's 1st congressional district special election", 3, date(2013, 12, 17), "special"),
    ("AR", 3, "2001 Arkansas's 3rd congressional district special election", 4, date(2001, 11, 20), "special"),
    ("AZ", 8, "2012 Arizona's 8th congressional district special election", 2, date(2012, 6, 12), "special"),
    ("AZ", 8, "2018 Arizona's 8th congressional district special election", 2, date(2018, 4, 24), "special"),
    ("CA", 32, "2001 California's 32nd congressional district special election", 0, date(2001, 4, 10), "first"),
    ("CA", 32, "2001 California's 32nd congressional district special election", 1, date(2001, 6, 5), "runoff"),
    ("CA", 42, "1999 California's 42nd congressional district special election", 1, date(1999, 11, 16), "special"),
    ("CA", 5, "2005 California's 5th congressional district special election", 0, date(2005, 3, 8), "special"),
    ("CA", 50, "2006 California's 50th congressional district special election", 0, date(2006, 4, 11), "first"),
    ("CA", 50, "2006 California's 50th congressional district special election", 1, date(2006, 6, 6), "runoff"),
    ("CA", 37, "2007 California's 37th congressional district special election", 0, date(2007, 6, 26), "first"),
    ("CA", 37, "2007 California's 37th congressional district special election", 1, date(2007, 8, 21), "runoff"),
    ("CA", 12, "2008 California's 12th congressional district special election", 0, date(2008, 4, 8), "special"),
    ("CA", 10, "2009 California's 10th congressional district special election", 0, date(2009, 9, 1), "first"),
    ("CA", 10, "2009 California's 10th congressional district special election", 1, date(2009, 11, 3), "runoff"),
    ("CA", 32, "2009 California's 32nd congressional district special election", 0, date(2009, 5, 19), "first"),
    ("CA", 32, "2009 California's 32nd congressional district special election", 1, date(2009, 7, 14), "runoff"),
    ("CA", 36, "2011 California's 36th congressional district special election", 0, date(2011, 5, 17), "first"),
    ("CA", 36, "2011 California's 36th congressional district special election", 1, date(2011, 7, 12), "runoff"),
    ("CA", 34, "2017 California's 34th congressional district special election", 0, date(2017, 4, 4), "first"),
    ("CA", 34, "2017 California's 34th congressional district special election", 1, date(2017, 6, 6), "runoff"),
    ("CA", 20, "2024 California's 20th congressional district special election", 0, date(2024, 3, 19), "first"),
    ("CA", 20, "2024 California's 20th congressional district special election", 1, date(2024, 5, 21), "runoff"),
    ("CA", 1, "2026 California's 1st congressional district special election", 0, date(2026, 6, 2), "special"),
    ("CA", 14, "2026 California's 14th congressional district special election", 0, date(2026, 6, 16), "first"),
    ("CA", 14, "2026 California's 14th congressional district special election", 1, date(2026, 8, 18), "runoff"),
    ("CO", 4, "2024 Colorado's 4th congressional district special election", 0, date(2024, 6, 25), "special"),
    ("FL", 1, "2001 Florida's 1st congressional district special election", 2, date(2001, 10, 16), "special"),
    ("FL", 19, "2010 Florida's 19th congressional district special election", 2, date(2010, 4, 13), "special"),
    ("FL", 13, "2014 Florida's 13th congressional district special election", 1, date(2014, 3, 11), "special"),
    ("FL", 19, "2014 Florida's 19th congressional district special election", 1, date(2014, 6, 24), "special"),
    ("GA", 5, "2020 Georgia's 5th congressional district special election", 0, date(2020, 9, 29), "first"),
    ("HI", 1, "2010 Hawaii's 1st congressional district special election", 0, date(2010, 5, 22), "special"),
    ("IN", 7, "2008 Indiana's 7th congressional district special election", 0, date(2008, 3, 11), "special"),
    ("KY", 6, "2004 Kentucky's 6th congressional district special election", 0, date(2004, 2, 17), "special"),
    ("LA", 1, "1999 Louisiana's 1st congressional district special election", 0, date(1999, 5, 1), "first"),
    ("LA", 1, "1999 Louisiana's 1st congressional district special election", 1, date(1999, 5, 29), "runoff"),
    ("LA", 1, "2008 Louisiana's 1st congressional district special election", 3, date(2008, 5, 3), "special"),
    ("LA", 6, "2008 Louisiana's 6th congressional district special election", 4, date(2008, 5, 3), "special"),
    ("MN", 1, "2022 Minnesota's 1st congressional district special election", 4, date(2022, 8, 9), "special"),
    ("MO", 8, "2013 Missouri's 8th congressional district special election", 0, date(2013, 6, 4), "special"),
    ("MS", 1, "2008 Mississippi's 1st congressional district special election", 4, date(2008, 4, 22), "first"),
    ("MS", 1, "2008 Mississippi's 1st congressional district special election", 5, date(2008, 5, 13), "runoff"),
    ("NJ", 10, "2024 New Jersey's 10th congressional district special election", 2, date(2024, 9, 18), "special"),
    ("NJ", 11, "2026 New Jersey's 11th congressional district special election", 3, date(2026, 4, 16), "special"),
    ("NV", 2, "2011 Nevada's 2nd congressional district special election", 0, date(2011, 9, 13), "special"),
    ("NY", 23, "2009 New York's 23rd congressional district special election", 0, date(2009, 11, 3), "special"),
    ("NY", 9, "2011 New York's 9th congressional district special election", 0, date(2011, 9, 13), "special"),
    ("OH", 2, "2005 Ohio's 2nd congressional district special election", 2, date(2005, 8, 2), "special"),
    ("OH", 5, "2007 Ohio's 5th congressional district special election", 2, date(2007, 12, 11), "special"),
    ("OH", 11, "2008 Ohio's 11th congressional district special election", 0, date(2008, 11, 18), "special"),
    ("OR", 1, "2012 Oregon's 1st congressional district special election", 3, date(2012, 1, 31), "special"),
    ("SC", 2, "2001 South Carolina's 2nd congressional district special election", 0, date(2001, 12, 18), "special"),
    ("SC", 1, "2013 South Carolina's 1st congressional district special election", 3, date(2013, 5, 7), "special"),
    ("SC", 5, "2017 South Carolina's 5th congressional district special election", 3, date(2017, 6, 20), "special"),
    ("TX", 3, "1991 Texas's 3rd congressional district special election", 0, date(1991, 5, 4), "first"),
    ("TX", 3, "1991 Texas's 3rd congressional district special election", 1, date(1991, 5, 18), "runoff"),
    ("TX", 19, "2003 Texas's 19th congressional district special election", 0, date(2003, 5, 3), "first"),
    ("TX", 19, "2003 Texas's 19th congressional district special election", 1, date(2003, 6, 3), "runoff"),
    ("TX", 6, "2021 Texas's 6th congressional district special election", 0, date(2021, 5, 1), "first"),
    ("TX", 6, "2021 Texas's 6th congressional district special election", 1, date(2021, 7, 27), "runoff"),
    ("TX", 34, "2022 Texas's 34th congressional district special election", 0, date(2022, 6, 14), "special"),
    ("TX", 18, "2024 Texas's 18th congressional district special election", 0, date(2024, 11, 5), "special"),
    ("VA", 7, "1991 Virginia's 7th congressional district special election", 0, date(1991, 11, 5), "special"),
    ("VA", 4, "2001 Virginia's 4th congressional district special election", 2, date(2001, 6, 19), "special"),
    ("VA", 1, "2007 Virginia's 1st congressional district special election", 0, date(2007, 12, 11), "special"),
    ("WI", 7, "2020 Wisconsin's 7th congressional district special election", 2, date(2020, 5, 12), "special"),
]
PARTIES = {
    "Democratic Party": "democratic-party",
    "Republican Party": "republican-party",
    "Libertarian Party": "libertarian-party",
    "Green Party": "green-party",
    "Reform Party": "reform-party",
}
CLEAN = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", re.DOTALL)


def pages() -> list[str]:
    titles = sorted({article for _, _, article, _, _, _ in CONTESTS})
    return [
        API
        + urlencode(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content|ids",
                "rvslots": "main",
                "titles": "|".join(titles[start : start + 20]),
                "format": "json",
                "formatversion": 2,
                "redirects": 1,
            }
        )
        for start in range(0, len(titles), 20)
    ]


def fetch() -> None:
    for link in pages():
        raw.fetch(SOURCE, link, suffix=".json")


def parse() -> None:
    texts: dict[str, str] = {}
    for link in pages():
        query = json.loads(raw.cached(SOURCE, link).path.read_text())["query"]
        for found in query["pages"]:
            texts[found["title"]] = CLEAN.sub("", found["revisions"][0]["slots"]["main"]["content"])
        for moved in query.get("redirects", []):
            texts[moved["from"]] = texts[moved["to"]]
    rows: list[dict[str, object]] = []
    for state, district, article, index, day, _ in CONTESTS:
        text = texts[article]
        begins = list(re.finditer(r"\{\{Election box begin", text, flags=re.IGNORECASE))
        end = re.search(r"\{\{Election box end", text[begins[index].start() :], flags=re.IGNORECASE)
        stop = begins[index + 1].start() if index + 1 < len(begins) else len(text)
        section = text[begins[index].start() : min(begins[index].start() + end.start(), stop)]
        position = 0
        total = None
        for member in templates_of(section)[1:]:
            name, fields = fields_of(member)
            if name.startswith("election box total"):
                total = count(fields["votes"])
            if "candidate" in name or "write-in" in name:
                candidate = plain(fields.get("candidate", ""))
                if candidate == "" or candidate.lower().startswith("write-in"):
                    candidate = "Write-ins"
                candidate = re.sub(r" \([^)]*\)$", "", candidate)
                if plain(fields.get("party", "")) in ("Total", "Blank votes", "Over votes"):
                    continue
                rows.append(
                    {
                        "state": state,
                        "district": district,
                        "day": day,
                        "position": position,
                        "candidate": candidate,
                        "party": plain(fields.get("party", "")),
                        "votes": count(fields["votes"]),
                        "total": None,
                    }
                )
                position += 1
        if total is not None:
            for row in rows[-position:]:
                row["total"] = total
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="U.S. House special elections as district totals from the English Wikipedia's election articles",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/List_of_special_elections_to_the_United_States_House_of_Representatives",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        for link in pages():
            cs.artifact(raw.cached(SOURCE, link), source)
        house = cs.key("us.body", "house")
        fptp = cs.key("us.electoral_system", "fptp")
        two_round = cs.key("us.electoral_system", "two_round")
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        elections: set[UUID] = set()
        contests: dict[tuple[str, int, date], UUID] = {}
        persons: dict[str, UUID] = {}
        winners: dict[tuple[str, int], tuple[str, date]] = {}
        for state, district, _, _, day, stage in sorted(CONTESTS, key=lambda entry: entry[4]):
            if stage != "first":
                top = frame.filter(
                    (pl.col("state") == state) & (pl.col("district") == district) & (pl.col("day") == day)
                )
                top = (
                    top.group_by("candidate").agg(pl.col("votes").sum()).sort("votes", descending=True)["candidate"][0]
                )
                winners[(state, district)] = (top, day)
        for state, district, article, _, day, stage in CONTESTS:
            label, fips = STATES[state]
            rows = frame.filter(
                (pl.col("state") == state) & (pl.col("district") == district) & (pl.col("day") == day)
            ).sort("position")
            if rows.is_empty():
                raise ValueError(f"{day} {state}-{district}: no rows")
            printed = rows["total"][0]
            rows = (
                rows.group_by("candidate", maintain_order=True)
                .agg(pl.col("party").first(), pl.col("votes").sum(), pl.col("position").first())
                .sort("position")
            )
            listed = rows["votes"].sum()
            if printed is None:
                detail = "The box prints no total, so the total is the candidates' sum."
            elif printed == listed:
                detail = "The box prints its own total, which the candidates' votes sum to."
            elif printed > listed:
                detail = f"The box prints a total of {printed:,} against {listed:,} for the candidates it lists; the difference is votes for candidates the box omits, not credited to anyone."
            else:
                detail = f"The box prints a total of {printed:,} but its candidates sum to {listed:,}; the sum is kept as the total."
            general = conn.execute(
                """select ct.district_area_id, ct.boundary_set_id from external_id e
                   join contest ct on ct.id = e.entity_id
                   where e.scheme = 'us.contest' and e.value ~ %s and left(e.value, 10) < %s
                   order by e.value desc limit 1""",
                [f"^\\d{{4}}-11-\\d\\d:{state}:house:{district}$", str(day)],
            ).fetchone()
            if general is None:
                raise ValueError(f"{day} {state}-{district}: no earlier general for the district")
            runoff = stage == "runoff"
            election_stage = "runoff" if runoff else "special"
            key = f"{day}:{state}:house:{district}:special" + (":runoff" if runoff else "")
            election = cs.key("us.election", f"{day}:{state}:{election_stage}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind=election_stage,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election,
                    f"{day:%B} {day.day}, {day.year} {label} special {'runoff' if runoff else 'election'}",
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-{election_stage}", entity_id=election)
            contest = cs.key("us.contest", key)
            contests[(state, district, day)] = contest
            ordinal = f"{district}{'th' if district % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(district % 10, 'th')}"
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=house,
                district_area_id=general[0],
                seat_id=None,
                term="remainder",
                boundary_set_id=general[1],
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=next(
                    (
                        contests[(state, district, earlier[4])]
                        for earlier in CONTESTS
                        if earlier[:2] == (state, district) and earlier[5] == "first" and earlier[4] < day
                    ),
                    None,
                )
                if runoff
                else None,
                electoral_system_id=fptp if stage == "special" else two_round,
                default_result_kind="counted",
            )
            name(
                contest,
                f"{day.year} U.S. House special {'runoff' if runoff else 'election'} in {label}'s {ordinal} congressional district",
            )
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{day}-{election_stage}/house-{district}-special",
                entity_id=contest,
            )
            known = {
                slug(text): person
                for text, person in conn.execute(
                    """select distinct n.text, c.person_id from candidacy c
                       join contest_option o on o.id = c.option_id join contest ct on ct.id = o.contest_id
                       join name n on n.entity_id = c.person_id and n.is_preferred
                       where ct.district_area_id = %s""",
                    [general[0]],
                ).fetchall()
            }
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=(
                    f'District total from the results box of the English Wikipedia\'s article "{article}", a non-official source: '
                    f"no county or finer returns from {label}'s election authority were available to this archive. "
                    + detail
                ),
            )
            unit = cs.unit("us.cd", f"{fips}{district:02d}", day)
            nextbox = next(
                (
                    set(
                        frame.filter(
                            (pl.col("state") == state)
                            & (pl.col("district") == district)
                            & (pl.col("day") == upcoming[4])
                        )["candidate"]
                    )
                    for upcoming in CONTESTS
                    if upcoming[:2] == (state, district) and upcoming[5] == "runoff" and upcoming[4] > day
                ),
                set(),
            )
            ranked: list[tuple[int, UUID, str]] = []
            for candidate, party, votes in rows.select("candidate", "party", "votes").iter_rows():
                if candidate == "Write-ins":
                    option = cs.key("us.option", f"{key}:write_in")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    party_key = next((value for label_, value in PARTIES.items() if party.startswith(label_)), None)
                    if party_key is None and party and not party.startswith("Independent"):
                        party_key = f"{state}:{slug(re.sub(r' \([^)]*\)$', '', party))}"
                    party_id = None
                    if party_key is not None:
                        party_id = cs.key("us.party", party_key)
                        if party_key not in parties_known:
                            parties_known.add(party_key)
                            cs.add("party", id=party_id, kind="party", polity_id=cs.polity(f"US-{state}"))
                            name(party_id, re.sub(r" \([^)]*\)$", "", party))
                    token = slug(candidate)
                    found = None
                    if candidate == winners.get((state, district), ("",))[0]:
                        found = conn.execute(
                            """select t.person_id from tenure t join name n on n.entity_id = t.person_id and n.is_preferred
                               where t.body_id = %s and t.from_date >= %s::date - 60 and t.from_date < %s::date + 200
                               and n.text ilike %s order by t.from_date limit 1""",
                            [
                                house,
                                winners[(state, district)][1],
                                winners[(state, district)][1],
                                f"%{candidate.split()[-1].removesuffix(' Jr.').removesuffix(' Sr.')}%",
                            ],
                        ).fetchone()
                        if found is None and day.year < 2026:
                            raise ValueError(f"{key}: no House tenure for {candidate}")
                    if found is not None:
                        person = found[0]
                    else:
                        person = (
                            known.get(token) or persons.get(token) or cs.key(f"{SOURCE}.person", f"{state}:{token}")
                        )
                        if person in cs.minted and token not in persons:
                            cs.add("person", id=person)
                            name(person, candidate)
                    persons[token] = person
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party_id is None,
                        is_incumbent=False,
                    )
                    ranked.append((votes, option, candidate))
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=1,
                    flags=0,
                    value=votes,
                )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=1,
                flags=0,
                value=printed if printed is not None and printed >= listed else listed,
            )
            ranked.sort(key=lambda entry: -entry[0])
            for place, (_, option, candidate) in enumerate(ranked):
                if stage == "first":
                    kind = "advanced" if candidate in nextbox or not nextbox and place < 2 else "lost"
                else:
                    kind = "elected" if place == 0 else "lost"
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=None)
        return cs.write()
