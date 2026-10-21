import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wikipedia_governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "Gubernatorial election articles, results boxes"
NOTE = (
    "Statewide votes as tabulated in the results box of the English Wikipedia's article on each gubernatorial "
    "election, which cites the state Division of Elections' official results."
)
ELECTIONS = {
    1974: date(1974, 11, 5),
    1978: date(1978, 11, 7),
    1982: date(1982, 11, 2),
    1986: date(1986, 11, 4),
    1990: date(1990, 11, 6),
    1994: date(1994, 11, 8),
    1998: date(1998, 11, 3),
    2002: date(2002, 11, 5),
    2014: date(2014, 11, 4),
}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican Party": "republican-party",
    "Libertarian": "libertarian-party",
    "Green": "green-party",
    "Constitution": "constitution-party",
    "Alaskan Independence": "AK:alaskan-independence-party",
}


def page(titles: list[str]) -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": "|".join(titles),
            "format": "json",
            "formatversion": 2,
        }
    )


def titles() -> list[str]:
    return [f"{year} Alaska gubernatorial election" for year in ELECTIONS]


def fetch() -> None:
    raw.fetch(SOURCE, page(titles()), suffix=".json")


def parse() -> None:
    texts = {
        found["title"]: found["revisions"][0]["slots"]["main"]["content"]
        for found in json.loads(raw.cached(SOURCE, page(titles())).path.read_text())["query"]["pages"]
    }
    rows: list[dict[str, object]] = []
    for year, day in ELECTIONS.items():
        title = f"{year} Alaska gubernatorial election"
        text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", texts[title], flags=re.DOTALL)
        templates: list[str] = []
        depth, start = 0, 0
        for match in re.finditer(r"\{\{|\}\}", text):
            if match[0] == "{{":
                if depth == 0:
                    start = match.start()
                depth += 1
            elif depth:
                depth -= 1
                if depth == 0:
                    templates.append(text[start : match.end()])
        box: list[dict[str, str]] | None = None
        for template in templates:
            parts, level, current = [], 0, ""
            for index, character in enumerate(template[2:-2]):
                pair = template[2 + index : 4 + index]
                if pair in ("{{", "[["):
                    level += 1
                elif pair in ("}}", "]]") and level:
                    level -= 1
                if character == "|" and level == 0:
                    parts.append(current)
                    current = ""
                else:
                    current += character
            parts.append(current)
            kind = parts[0].strip().lower()
            fields = {key.strip().lower(): value.strip() for key, _, value in (p.partition("=") for p in parts[1:])}
            if kind.startswith("election box begin"):
                label = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", fields.get("title", ""))
                box = [] if label.strip() == title else None
            elif box is not None and kind.startswith("election box") and re.search(r"candidate", kind):
                box.append(fields)
            elif box is not None and kind.startswith("election box end"):
                break
        if not box:
            raise ValueError(f"{title}: no results box")
        for position, entry in enumerate(box):
            label = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", entry.get("candidate", ""))
            label = re.sub(r"\{\{[^{}]*\}\}|'{2,}|<[^>]+>", "", label)
            label = re.sub(r"\((?:incumbent|inc\.|write-in)\)", "", label, flags=re.IGNORECASE)
            label = re.sub(r"\s+", " ", label).strip(" ,;")
            party = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", entry.get("party", "")).strip()
            votes = re.sub(r"[^\d]", "", re.sub(r"\{\{[^{}]*\}\}|'{2,}", "", entry.get("votes", "")))
            if not votes:
                raise ValueError(f"{title}: no votes for {label!r}")
            named = label != "" and not re.fullmatch(r"(?i)others?|write-ins?", label)
            rows.append(
                {
                    "year": year,
                    "day": day,
                    "position": position,
                    "candidate": label,
                    "party": party,
                    "pooled": not named,
                    "independent": party.startswith("Independent"),
                    "votes": int(votes),
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
            summary="Alaska governor general elections 1974–2002 and 2014 from the English Wikipedia's election articles",
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
            url="https://en.wikipedia.org/wiki/2014_Alaska_gubernatorial_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, page(titles())), source)
        fptp = cs.key("us.electoral_system", "fptp")
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-AK' and u.scheme_code = 'us.state'"""
        ).fetchone()
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        held = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join contest c on c.id = x.entity_id
                   join result_set r on r.contest_id = c.id
                   where x.scheme = 'us.contest' and x.value like '%%:AK:governor'"""
            ).fetchall()
        }
        office = cs.key("us.office", "governor:AK")
        if office in cs.minted:
            raise ValueError("the Alaska governor office must exist")
        persons: dict[str, UUID] = {}
        for (year, day), rows in frame.group_by(["year", "day"], maintain_order=True):
            contest_key = f"{day}:AK:governor"
            if contest_key in held:
                continue
            election = cs.key("us.election", f"{day}:AK")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} Alaska governor election")
            cs.add("slug", path=f"us/ak/{day}-general/governor", entity_id=contest)
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
                body_md=f"Statewide votes from the results box of the English Wikipedia's article on the {year} Alaska gubernatorial election.",
            )
            ranked: list[tuple[int, UUID]] = []
            for candidate, party, pooled, independent, votes in (
                rows.sort("position").select("candidate", "party", "pooled", "independent", "votes").iter_rows()
            ):
                if pooled:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    if option in cs.minted:
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                else:
                    token = slug(candidate)
                    if token not in persons:
                        persons[token] = cs.key(f"{SOURCE}.person", f"AK:{token}")
                        if persons[token] in cs.minted:
                            cs.add("person", id=persons[token])
                            name(persons[token], candidate)
                    person = persons[token]
                    party_key = next((value for label, value in PARTIES.items() if label in party), None)
                    if party_key is None and not independent and party and "Write-in" not in party:
                        party_key = f"AK:{slug(re.sub(r' of Alaska| \(United States\)', '', party))}"
                    party_id = None
                    if party_key is not None:
                        party_id = cs.key("us.party", party_key)
                        if party_key not in parties_known:
                            parties_known.add(party_key)
                            cs.add("party", id=party_id, kind="party", polity_id=cs.polity("US-AK"))
                            name(party_id, re.sub(r" of Alaska", "", party))
                    option = cs.key("us.option", f"{contest_key}:{token}")
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
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=independent or party_id is None,
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
            cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
