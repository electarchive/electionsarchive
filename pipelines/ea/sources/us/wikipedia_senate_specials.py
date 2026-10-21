import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.wikipedia_guam_governors import fields_of, plain, templates_of
from ea.store import DATA, connect

SOURCE = "us.wikipedia_senate_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "United States Senate special election articles, results boxes"
NOTE = (
    "Statewide votes as tabulated in the results box of the English Wikipedia's article on each special election "
    "to the United States Senate, which cites state returns, the Clerk of the House's statistics, and CQ's Guide to U.S. Elections."
)
CONTESTS = [
    (date(1937, 11, 15), "AR", 2, 1937, "Arkansas", "Arkansas special senate election, 1937", False),
    (date(1941, 6, 28), "TX", 2, 1941, "Texas", "1941 U.S. Senate special election", False),
    (date(1941, 9, 23), "MS", 2, 1941, "Mississippi", "1941 U.S. Senate special election in Mississippi", False),
    (date(1941, 11, 5), "SC", 3, 1941, "South Carolina", "1941 U.S. Senate special election in South Carolina", False),
    (date(1949, 11, 8), "NY", 3, 1949, "New York", "1949 U.S. Senate special election in New York", False),
    (date(1946, 6, 25), "ND", 3, 1946, "North Dakota", "1946 United States Senate special election in North Dakota", False),
    (date(1936, 11, 3), "MN", 2, 1936, "Minnesota", "Special election results", False),
    (date(1957, 4, 2), "TX", 2, 1957, "Texas", "1957 U.S. Senate special election", False),
    (date(1957, 8, 27), "WI", 1, 1957, "Wisconsin", "1957 United States Senate special election in Wisconsin", False),
    (date(1960, 6, 28), "ND", 1, 1960, "North Dakota", "1960 United States Senate special election, North Dakota", False),
    (date(1961, 4, 4), "TX", 2, 1961, "Texas", "1961 U.S. Senate special election primary", False),
    (date(1961, 5, 27), "TX", 2, 1961, "Texas", "1961 U.S. Senate special election", True),
    (date(1972, 1, 7), "VT", 1, 1972, "Vermont", "United States Senate special election in Vermont, 1972", False),
    (date(1975, 9, 16), "NH", 3, 1975, "New Hampshire", "1975 United States senate Special election in New Hampshire", False),
    (date(1983, 11, 8), "WA", 1, 1983, "Washington", "1983 United States Senate special election in Washington", False),
    (date(1991, 11, 5), "PA", 1, 1991, "Pennsylvania", "1991 U.S. Senate election in Pennsylvania", False),
    (date(1992, 12, 4), "ND", 1, 1992, "North Dakota", "General election results", False),
    (date(1996, 1, 30), "OR", 3, 1996, "Oregon", "General election results", False),
    (date(2010, 1, 19), "MA", 1, 2010, "Massachusetts", "2010 Massachusetts U.S. Senate special election", False),
    (date(2013, 6, 25), "MA", 2, 2013, "Massachusetts", "2013 U.S. Senate special election in Massachusetts", False),
    (date(2013, 10, 16), "NJ", 2, 2013, "New Jersey", "United States Senate special election in New Jersey, 2013", False),
    (date(2017, 12, 12), "AL", 2, 2017, "Alabama", "2017 United States Senate special election in Alabama", False),
]
PARTIES = {
    "Democratic Party": "democratic-party",
    "Republican Party": "republican-party",
    "Libertarian Party": "libertarian-party",
    "Socialist Labor Party": "socialist-labor-party-of-america",
}
OTHERS = ("Minor candidates", "All others", "Scattering")
LEHMAN = "Herbert H. Lehman"


def titles() -> list[str]:
    return sorted(
        {f"{year} United States Senate special election in {place}" for _, _, _, year, place, _, _ in CONTESTS}
    )


def page() -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": "|".join(titles()),
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        }
    )


def fetch() -> None:
    raw.fetch(SOURCE, page(), suffix=".json")


def parse() -> None:
    query = json.loads(raw.cached(SOURCE, page()).path.read_text())["query"]
    texts = {
        found["title"]: re.sub(
            r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->",
            "",
            found["revisions"][0]["slots"]["main"]["content"],
            flags=re.DOTALL,
        )
        for found in query["pages"]
    }
    for moved in query.get("redirects", []):
        texts[moved["from"]] = texts[moved["to"]]
    rows: list[dict[str, object]] = []
    for day, state, _, year, place, anchor, _ in CONTESTS:
        text = texts[f"{year} United States Senate special election in {place}"]
        for begin in re.finditer(r"\{\{Election box begin", text, flags=re.IGNORECASE):
            end = re.search(r"\{\{Election box end", text[begin.start() :], flags=re.IGNORECASE)
            section = text[begin.start() : begin.start() + (end.start() if end else 6000)]
            members = templates_of(section)
            if plain(fields_of(members[0])[1].get("title", "")) != anchor:
                continue
            position = 0
            for member in members[1:]:
                name, fields = fields_of(member)
                if "candidate" in name or "write-in" in name:
                    candidate = plain(fields.get("candidate", ""))
                    if "write-in" in name or candidate.lower().startswith("write-in") or candidate == "" and "write" in fields.get("party", "").lower():
                        candidate = "Write-ins"
                    elif candidate == "":
                        candidate = "Scattering"
                    party = plain(fields.get("party", ""))
                    if state == "NY" and day.year == 1949:
                        if party != "Total" and candidate == LEHMAN:
                            continue
                        party = "Democratic Party (United States)" if candidate == LEHMAN else party
                    elif party == "Total":
                        continue
                    candidate = re.sub(r" \((?:politician|[A-Za-z ]+ politician)\)$", "", candidate)
                    rows.append(
                        {
                            "day": day,
                            "state": state,
                            "position": position,
                            "candidate": candidate,
                            "party": party,
                            "votes": int(re.sub(r"[^\d]", "", fields["votes"])),
                        }
                    )
                    position += 1
            break
        else:
            raise ValueError(f"{day} {state}: no box titled {anchor!r}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="United States Senate special elections 1937–2017 from the English Wikipedia's election articles",
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
            url="https://en.wikipedia.org/wiki/List_of_special_elections_to_the_United_States_Senate",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, page()), source)
        senate = cs.key("us.body", "senate")
        systems = {code: cs.key("us.electoral_system", code) for code in ("fptp", "two_round")}
        if cs.minted & {senate, *systems.values()}:
            raise ValueError("the Senate and its electoral systems must already exist")
        states = {
            iso.removeprefix("US-"): (unit, area, text)
            for text, iso, unit, area in conn.execute(
                """select n.text, p.iso_code, u.id, u.area_id from reporting_unit u
                   join polity p on p.id = u.polity_id
                   join name n on n.entity_id = p.id and n.is_preferred
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        persons: dict[str, UUID] = {}
        first_rounds: dict[int, UUID] = {}
        for day, state, seat_class, year, place, _, runoff in CONTESTS:
            contest_key = f"{day}:{state}:senate:special"
            unit, area, label = states[state]
            polity = cs.polity(f"US-{state}")
            two_round = year == 1961
            election_key = f"{day}:{state}:runoff" if runoff else f"{day}:{state}:special"
            election = cs.key("us.election", election_key)
            kind = "runoff" if runoff else "special"
            if election_key not in known:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day:%B} {day.day}, {day.year} {label} {'special runoff' if runoff else 'special'} election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=election)
            seat = cs.key("us.seat", f"senate:{state}:{seat_class}")
            if seat in cs.minted:
                raise ValueError(f"{state}: no Senate class {seat_class} seat")
            contest = cs.key("us.contest", contest_key)
            if not runoff:
                first_rounds[year] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=senate,
                district_area_id=area,
                seat_id=seat,
                term="remainder",
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=first_rounds[year] if runoff else None,
                electoral_system_id=systems["two_round" if two_round else "fptp"],
                default_result_kind="counted",
            )
            name(
                contest,
                f"{year} U.S. Senate special election{' runoff' if runoff else ''} in {label}",
            )
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{day}-{kind}/senate-class-{seat_class}-special",
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
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=f"Statewide votes from the English Wikipedia's article on the {year} United States Senate special election in {label}.",
            )
            rows = frame.filter((pl.col("day") == day) & (pl.col("state") == state)).sort("position")
            ranked: list[tuple[int, UUID]] = []
            for candidate, party, votes in rows.select("candidate", "party", "votes").iter_rows():
                if candidate in OTHERS or candidate == "Write-ins":
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if candidate == "Write-ins" else "others",
                        ballot_label=candidate,
                    )
                else:
                    party_key = next((value for label_, value in PARTIES.items() if party.startswith(label_)), None)
                    if party_key is None and party and not party.startswith("Independent"):
                        party_key = f"{state}:{slug(re.sub(r' \([^)]*\)$', '', party))}"
                    party_id = None
                    if party_key is not None:
                        party_id = cs.key("us.party", party_key)
                        if party_key not in parties_known:
                            parties_known.add(party_key)
                            cs.add("party", id=party_id, kind="party", polity_id=polity)
                            name(party_id, re.sub(r" \([^)]*\)$", "", party))
                    token = slug(candidate)
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=candidate,
                    )
                    if token not in persons:
                        persons[token] = cs.key(f"{SOURCE}.person", f"{state}:{token}")
                        if persons[token] in cs.minted:
                            cs.add("person", id=persons[token])
                            name(persons[token], candidate)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=persons[token],
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party_id is None,
                    )
                    ranked.append((votes, option))
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            ranked.sort(key=lambda pair: -pair[0])
            if two_round and not runoff:
                for _, option in ranked[:2]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
            else:
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
