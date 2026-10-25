import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.wikipedia_guam_governors import count, fields_of, plain, templates_of
from ea.store import DATA, connect

SOURCE = "us.wikipedia_pr_commissioners"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "Puerto Rican general election articles, Resident Commissioner results"
NOTE = (
    "Island-wide votes for Resident Commissioner as tabulated in the English Wikipedia's article on each Puerto Rican "
    "general election, which cites Nolla Acosta's Puerto Rico Election Results, 1899-2012 and Nohlen's Elections in "
    "the Americas."
)
YEARS = [*range(1900, 1940, 2), 1917]
PARTIES = {
    "Union of Puerto Rico": "PR:unionist",
    "Coalition": "PR:coalitionist",
    "Partido Republicano Puertorriqueño": "PR:republican-party-of-puerto-rico",
}


def titles() -> list[str]:
    return [f"{year} Puerto Rican general election" for year in YEARS]


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
        }
    )


def fetch() -> None:
    raw.fetch(SOURCE, page(), suffix=".json")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for found in json.loads(raw.cached(SOURCE, page()).path.read_text())["query"]["pages"]:
        if found.get("missing"):
            continue
        text = re.sub(
            r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->",
            "",
            found["revisions"][0]["slots"]["main"]["content"],
            flags=re.DOTALL,
        )
        section = re.search(r"===\s*Resident [Cc]ommissioner\s*===", text)
        stamp = re.search(r"Start date\|(\d+)\|(\d+)\|(\d+)", text)
        if section is None or stamp is None:
            continue
        year = int(found["title"][:4])
        day = date(*map(int, stamp.groups()))
        _, fields = fields_of(templates_of(text[section.end() :])[0])
        index = 1
        while index < 14:
            if f"aspan{index}" in fields:
                span = int(fields[f"aspan{index}"])
                rows.append(
                    {
                        "year": year,
                        "day": day,
                        "position": index,
                        "candidate": plain(fields[f"cand{index}"]),
                        "party": re.sub(r" \(Puerto Rico\)$", "", plain(fields[f"alliance{index}"])),
                        "votes": count(fields[f"atotal{index + span - 1}"]),
                    }
                )
                index += span
                continue
            if f"cand{index}" in fields:
                rows.append(
                    {
                        "year": year,
                        "day": day,
                        "position": index,
                        "candidate": plain(fields[f"cand{index}"]),
                        "party": re.sub(
                            r" \(Puerto Rico\)$|–Unionist Majority$",
                            "",
                            plain(fields.get(f"party{index}") or fields.get(f"alliance{index}", "")),
                        ),
                        "votes": count(fields[f"votes{index}"]),
                    }
                )
            elif f"row{index}" in fields:
                rows.append(
                    {
                        "year": year,
                        "day": day,
                        "position": index,
                        "candidate": "Others",
                        "party": "",
                        "votes": count(fields[f"votes{index}"]),
                    }
                )
            index += 1
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico Resident Commissioner elections 1900–1936 from the English Wikipedia's election articles",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="Wikipedia",
                title=TITLE,
                url="https://en.wikipedia.org/wiki/1904_Puerto_Rican_general_election",
                license="CC BY-SA 4.0",
                notes=NOTE,
            )
        cs.artifact(raw.cached(SOURCE, page()), source)
        us = cs.polity("US")
        polity = cs.polity("US-PR")
        body = cs.key("us.body", "house")
        seat = cs.key("us.seat", "house:PR")
        fptp = cs.key("us.electoral_system", "fptp")
        if {body, seat, fptp} & cs.minted:
            raise ValueError("the House, its Puerto Rico seat, and plurality voting must already exist")
        unit, area, place = conn.execute(
            """select u.id, u.area_id, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'official'
               where u.scheme_code = 'us.cd' and u.code = '7298'"""
        ).fetchone()
        plans = conn.execute("select id, valid_from, valid_to from boundary_set where scheme_code = 'us.cd'").fetchall()
        holders = [
            (
                person,
                re.sub(
                    r"[^a-z ]", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
                ).split(),
                start,
                end,
            )
            for person, text, start, end in conn.execute(
                """select t.person_id, n.text, extract(year from t.from_date)::int,
                          coalesce(extract(year from t.to_date)::int, 9999)
                   from tenure t
                   join external_id x on x.entity_id = t.seat_id and x.scheme = 'us.seat'
                   join name n on n.entity_id = t.person_id and n.is_preferred
                   where x.value = 'house:PR'"""
            ).fetchall()
        ]
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        created: set[UUID] = set()
        for (year, day), rows in frame.group_by(["year", "day"], maintain_order=True):
            election = cs.key("us.election", f"{day}:PR")
            umbrella = None
            if day.month == 11:
                umbrella = cs.key("us.election", str(day))
                if umbrella in cs.minted and umbrella not in created:
                    created.add(umbrella)
                    cs.add(
                        "election",
                        id=umbrella,
                        polity_id=us,
                        kind="general",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(umbrella, f"{year} United States general election")
                    cs.add("slug", path=f"us/{day}-general", entity_id=umbrella)
            if election in cs.minted and election not in created:
                created.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=umbrella,
                    kind="general",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{year} Puerto Rico general election")
                cs.add("slug", path=f"us/pr/{day}-general", entity_id=election)
            on = day if day.month != 11 else date(year + 1, 3, 4) if year <= 1932 else date(year + 1, 1, 3)
            plan = next(id for id, start, end in plans if start <= on and (end is None or on < end))
            contest_key = f"{day}:PR:house:0"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                seat_id=seat,
                term="full",
                boundary_set_id=plan,
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} U.S. House election in {place}")
            cs.add("slug", path=f"us/pr/{day}-general/house-at-large", entity_id=contest)
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
                body_md=f"Island-wide votes from the English Wikipedia's article on the {year} Puerto Rican general election, citing Nolla Acosta's Puerto Rico Election Results, 1899-2012 and Nohlen's Elections in the Americas. Where the article groups candidates in an alliance, the votes are the alliance's printed total.",
            )
            leader: tuple[int, UUID] | None = None
            counted = 0
            for candidate, party, votes in rows.sort("position").select("candidate", "party", "votes").iter_rows():
                counted += votes
                token = slug(candidate)
                if candidate == "Others":
                    option = cs.key("us.option", f"{contest_key}:others")
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label=candidate)
                else:
                    party_id = None
                    if party and party != "Independent":
                        party_key = PARTIES.get(party, f"PR:{slug(party)}")
                        party_id = cs.key("us.party", party_key)
                        if party_key not in parties_known:
                            parties_known.add(party_key)
                            cs.add("party", id=party_id, kind="party", polity_id=polity)
                            name(party_id, party)
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=candidate,
                    )
                    words = [
                        word
                        for word in re.sub(
                            r"[^a-z ]",
                            " ",
                            unicodedata.normalize("NFKD", candidate).encode("ascii", "ignore").decode().lower(),
                        ).split()
                        if len(word) > 1 and word not in ("jr", "sr")
                    ]
                    person = next(
                        (
                            holder
                            for holder, tokens, start, end in holders
                            if words[-1] in tokens
                            and tokens[0][0] == candidate[0].lower()
                            and start - 4 <= year <= end + 4
                        ),
                        None,
                    )
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", f"PR:{token}")
                        if person in cs.minted and person not in created:
                            created.add(person)
                            cs.add("person", id=person)
                            name(person, candidate)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party_id is None,
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
                count_round=0,
                flags=1,
                value=counted,
            )
            cs.add("outcome", result_set_id=result_set, option_id=leader[1], kind="elected", seats=1)
        return cs.write()
