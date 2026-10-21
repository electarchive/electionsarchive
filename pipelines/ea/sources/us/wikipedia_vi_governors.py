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

SOURCE = "us.wikipedia_vi_governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "United States Virgin Islands general election articles, governor results"
NOTE = (
    "Territory-wide votes for governor as tabulated in the English Wikipedia's article on each Virgin Islands "
    "general election."
)
ELECTIONS = {
    1974: (date(1974, 11, 5), date(1974, 11, 19)),
    1982: (date(1982, 11, 2), None),
    1986: (date(1986, 11, 4), date(1986, 11, 18)),
    1990: (date(1990, 11, 6), None),
    1994: (date(1994, 11, 8), date(1994, 11, 22)),
    1998: (date(1998, 11, 3), None),
    2002: (date(2002, 11, 5), None),
    2006: (date(2006, 11, 7), date(2006, 11, 21)),
}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Independent Citizens": "VI:independent-citizens-movement",
}


def titles() -> list[str]:
    return [f"{year} United States Virgin Islands general election" for year in ELECTIONS]


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
    texts = {
        found["title"]: re.sub(
            r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->",
            "",
            found["revisions"][0]["slots"]["main"]["content"],
            flags=re.DOTALL,
        )
        for found in json.loads(raw.cached(SOURCE, page()).path.read_text())["query"]["pages"]
    }
    rows: list[dict[str, object]] = []
    for year, (first, second) in ELECTIONS.items():
        text = texts[f"{year} United States Virgin Islands general election"]
        if year == 1974:
            _, fields = fields_of(templates_of(text[text.index("{{Infobox election") :])[0])
            for index in range(1, 4):
                for stage, day in (("general", first), ("runoff", second)):
                    found = re.search(rf"([\d,]+)'*\s*\{{\{{small\|{stage}\}}\}}", fields[f"popular_vote{index}"])
                    if found is not None:
                        rows.append(
                            {
                                "year": year,
                                "day": day,
                                "stage": stage,
                                "position": index,
                                "candidate": plain(fields[f"nominee{index}"]),
                                "running_mate": plain(fields[f"running_mate{index}"]),
                                "party": plain(fields[f"party{index}"]),
                                "votes": count(found[1]),
                            }
                        )
            continue
        for template in templates_of(text[text.index("==", text.index("{{Infobox election")) :]):
            _, fields = fields_of(template)
            if "vp1" in fields:
                break
        for index in range(1, 12):
            if f"cand{index}" in fields:
                for day, key in ((first, f"votes{index}"), (second, f"votes{index}_2")):
                    if day is not None and key in fields:
                        rows.append(
                            {
                                "year": year,
                                "day": day,
                                "stage": "general" if day == first else "runoff",
                                "position": index,
                                "candidate": plain(fields[f"cand{index}"]),
                                "running_mate": plain(fields.get(f"vp{index}", "")),
                                "party": plain(fields.get(f"party{index}", "")),
                                "votes": count(fields[key]),
                            }
                        )
            elif f"row{index}" in fields:
                rows.append(
                    {
                        "year": year,
                        "day": first,
                        "stage": "general",
                        "position": index,
                        "candidate": "Write-ins",
                        "running_mate": "",
                        "party": "",
                        "votes": count(fields[f"votes{index}"]),
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virgin Islands governor elections 1974–2006 and runoffs from the English Wikipedia's election articles, 1978 excepted",
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
            url="https://en.wikipedia.org/wiki/2006_United_States_Virgin_Islands_general_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, page()), source)
        two_round = cs.key("us.electoral_system", "two_round")
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-VI' and u.scheme_code = 'us.state'"""
        ).fetchone()
        polity = cs.polity("US-VI")
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        held = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:VI:governor'"
            ).fetchall()
        }
        office = cs.key("us.office", "governor:VI")
        if office in cs.minted:
            raise ValueError("the Virgin Islands governor office must exist")
        persons: dict[str, UUID] = {}
        generals: dict[int, UUID] = {}
        for (year, day, stage), rows in frame.group_by(["year", "day", "stage"], maintain_order=True):
            runoff = stage == "runoff"
            contest_key = f"{day}:VI:governor"
            if contest_key in held:
                continue
            election_key = f"{day}:VI:runoff" if f"{day}:VI:runoff" in known else f"{day}:VI"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{year} Virgin Islands {stage} election")
                cs.add("slug", path=f"us/vi/{day}-{stage}", entity_id=election)
            contest = cs.key("us.contest", contest_key)
            if not runoff:
                generals[year] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=generals[year] if runoff else None,
                electoral_system_id=two_round,
                default_result_kind="counted",
            )
            name(contest, f"{year} Virgin Islands governor {'runoff' if runoff else 'election'}")
            cs.add("slug", path=f"us/vi/{day}-{stage}/governor", entity_id=contest)
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
                body_md=f"Territory-wide votes from the English Wikipedia's article on the {year} Virgin Islands general election.",
            )
            ranked: list[tuple[int, UUID]] = []
            for candidate, mate, party, votes in (
                rows.sort("position").select("candidate", "running_mate", "party", "votes").iter_rows()
            ):
                if candidate == "Write-ins":
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
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
                    continue
                party_key = next((value for label, value in PARTIES.items() if party.startswith(label)), None)
                party_id = None
                if party_key is not None:
                    party_id = cs.key("us.party", party_key)
                    if party_key not in parties_known:
                        parties_known.add(party_key)
                        cs.add("party", id=party_id, kind="party", polity_id=polity)
                        name(party_id, party)
                token = slug(f"{candidate} {mate}")
                option = cs.key("us.option", f"{contest_key}:{token}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=f"{candidate} and {mate}" if mate else candidate,
                )
                for role, label in (("head", candidate), ("running_mate", mate)):
                    if not label:
                        continue
                    key = slug(label)
                    if key not in persons:
                        persons[key] = cs.key(f"{SOURCE}.person", f"VI:{key}")
                        if persons[key] in cs.minted:
                            cs.add("person", id=persons[key])
                            name(persons[key], label)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:{role}"),
                        person_id=persons[key],
                        option_id=option,
                        role=role,
                        ballot_name=label if role == "head" else None,
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
            if runoff or ELECTIONS[year][1] is None:
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
