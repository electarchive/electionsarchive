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

SOURCE = "us.wikipedia_as_governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "American Samoa general election articles, governor results"
NOTE = (
    "Territory-wide votes for governor as tabulated in the English Wikipedia's article on each American Samoa "
    "general election, which cites the American Samoa Election Office."
)
ELECTIONS = {
    2000: (date(2000, 11, 7), None),
    2004: (date(2004, 11, 2), date(2004, 11, 16)),
    2008: (date(2008, 11, 4), date(2008, 11, 18)),
    2012: (date(2012, 11, 6), date(2012, 11, 20)),
    2016: (date(2016, 11, 8), None),
}


def titles() -> list[str]:
    return [f"{year} American Samoan general election" for year in ELECTIONS]


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
        text = texts[f"{year} American Samoan general election"]
        _, fields = fields_of(templates_of(text[text.index("{{Election results") :])[0])
        for index in range(1, 9):
            if f"cand{index}" not in fields:
                break
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
                            "votes": int(re.sub(r"[^\d]", "", fields[key])),
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
            summary="American Samoa governor elections 2000–2016 and runoffs from the English Wikipedia's election articles",
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
            url="https://en.wikipedia.org/wiki/2012_American_Samoan_general_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, page()), source)
        two_round = cs.key("us.electoral_system", "two_round")
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-AS' and u.scheme_code = 'us.state'"""
        ).fetchone()
        polity = cs.polity("US-AS")
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        held = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:AS:governor'"
            ).fetchall()
        }
        office = cs.key("us.office", "governor:AS")
        if office in cs.minted:
            raise ValueError("the American Samoa governor office must exist")
        persons: dict[str, UUID] = {}
        generals: dict[int, UUID] = {}
        for (year, day, stage), rows in frame.group_by(["year", "day", "stage"], maintain_order=True):
            runoff = stage == "runoff"
            contest_key = f"{day}:AS:governor"
            if contest_key in held:
                continue
            election_key = f"{day}:AS"
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
                name(election, f"{year} American Samoa {stage} election")
                cs.add("slug", path=f"us/as/{day}-{stage}", entity_id=election)
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
            name(contest, f"{year} American Samoa governor {'runoff' if runoff else 'election'}")
            cs.add("slug", path=f"us/as/{day}-{stage}/governor", entity_id=contest)
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
                body_md=f"Territory-wide votes from the English Wikipedia's article on the {year} American Samoa general election, citing the American Samoa Election Office.",
            )
            ranked: list[tuple[int, UUID]] = []
            for candidate, mate, votes in rows.sort("position").select("candidate", "running_mate", "votes").iter_rows():
                token = slug(f"{candidate} {mate}")
                option = cs.key("us.option", f"{contest_key}:{token}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_label=f"{candidate} and {mate}" if mate else candidate,
                )
                for role, label in (("head", candidate), ("running_mate", mate)):
                    if not label:
                        continue
                    key = slug(label)
                    if key not in persons:
                        persons[key] = cs.key(f"{SOURCE}.person", f"AS:{key}")
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
                        is_independent=True,
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
            if not (year in (2004, 2008, 2012) and not runoff):
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
