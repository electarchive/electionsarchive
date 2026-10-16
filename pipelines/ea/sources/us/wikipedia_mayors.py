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

SOURCE = "us.wikipedia_mayors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "Mayoral election articles, results boxes"
NOTE = (
    "Votes as tabulated in the results boxes of the English Wikipedia's articles on each city's mayoral election, "
    "which cite the county or city election office's canvass."
)
METHOD = (
    "Citywide votes from the results box of the English Wikipedia's article on the {year} {city} mayoral election. "
    "The round, its date, and who advanced or won follow the article."
)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
PARTIES = {"Democratic": "democratic-party", "Republican": "republican-party", "Libertarian": "libertarian-party"}
STATES = {
    "AK": "Alaska",
    "AZ": "Arizona",
    "CA": "California",
    "CO": "Colorado",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "IL": "Illinois",
    "IN": "Indiana",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "MA": "Massachusetts",
    "MD": "Maryland",
    "MI": "Michigan",
    "MO": "Missouri",
    "NC": "North Carolina",
    "NE": "Nebraska",
    "NM": "New Mexico",
    "NV": "Nevada",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "PA": "Pennsylvania",
    "TN": "Tennessee",
    "TX": "Texas",
    "VA": "Virginia",
    "WA": "Washington",
    "WI": "Wisconsin",
}
CITIES = {
    "Albuquerque": ("NM", "3502000", None),
    "Anchorage": ("AK", "0203000", None),
    "Atlanta": ("GA", "1304000", None),
    "Austin": ("TX", "4805000", None),
    "Baltimore": ("MD", "24510", None),
    "Boston": ("MA", "2507000", None),
    "Charlotte": ("NC", "3712000", None),
    "Chicago": ("IL", "1714000", None),
    "Cincinnati": ("OH", "3915000", None),
    "Cleveland": ("OH", "3916000", None),
    "Colorado Springs": ("CO", "0816000", None),
    "Columbus, Ohio": ("OH", "3918000", None),
    "Dallas": ("TX", "4819000", None),
    "Denver": ("CO", "0820000", None),
    "Detroit": ("MI", "2622000", None),
    "El Paso": ("TX", "4824000", None),
    "Fort Worth": ("TX", "4827000", None),
    "Fresno": ("CA", "0627000", None),
    "Henderson, Nevada": ("NV", "3231900", None),
    "Honolulu": ("HI", "15003", "HI:15003:county-executive"),
    "Houston": ("TX", "4835000", None),
    "Indianapolis": ("IN", "18097", None),
    "Jacksonville": ("FL", "1235000", None),
    "Las Vegas": ("NV", "3240000", None),
    "Lexington, Kentucky": ("KY", "2146027", None),
    "Lincoln, Nebraska": ("NE", "3128000", None),
    "Long Beach, California": ("CA", "0643000", None),
    "Los Angeles": ("CA", "0644000", None),
    "Louisville": ("KY", "2148000", None),
    "Memphis": ("TN", "4748000", None),
    "Miami": ("FL", "1245000", None),
    "Milwaukee": ("WI", "5553000", None),
    "Nashville": ("TN", "4752006", None),
    "New Orleans": ("LA", "2255000", None),
    "North Las Vegas": ("NV", "3251800", None),
    "Oklahoma City": ("OK", "4055000", None),
    "Omaha": ("NE", "3137000", None),
    "Orlando": ("FL", "1253000", None),
    "Philadelphia": ("PA", "4260000", None),
    "Phoenix": ("AZ", "0455000", None),
    "Pittsburgh": ("PA", "4261000", None),
    "Raleigh": ("NC", "3755000", None),
    "Richmond, Virginia": ("VA", "51760", None),
    "Sacramento": ("CA", "0664000", None),
    "San Antonio": ("TX", "4865000", None),
    "San Diego": ("CA", "0666000", None),
    "San Jose": ("CA", "0668000", None),
    "Seattle": ("WA", "5363000", None),
    "St. Louis": ("MO", "29510", None),
    "Tampa": ("FL", "1271000", None),
    "Toledo, Ohio": ("OH", "3977000", None),
    "Tucson": ("AZ", "0477000", None),
    "Tulsa": ("OK", "4075000", None),
    "Washington, D.C.": ("DC", "1150000", None),
}
ARTICLES: dict[tuple[int, str], tuple[str, ...]] = {
    (2022, "Austin"): ("2022-11-08 first", "2022-12-13 runoff"),
    (2022, "Charlotte"): ("2022-05-17 Democratic", "2022-05-17 Republican", "2022-07-26 won"),
    (2022, "Henderson, Nevada"): ("2022-06-14 primary-won",),
    (2022, "Lexington, Kentucky"): ("2022-05-17 primary", "2022-11-08 won"),
    (2022, "Long Beach, California"): ("2022-06-07 primary", "2022-11-08 won"),
    (2022, "Los Angeles"): ("2022-06-07 primary", "2022-11-08 won"),
    (2022, "Louisville"): ("2022-05-17 Democratic", "2022-05-17 Republican", "2022-11-08 won"),
    (2022, "North Las Vegas"): ("2022-06-14 primary", "2022-11-08 won"),
    (2022, "Oklahoma City"): ("2022-02-08 primary-won",),
    (2022, "Raleigh"): ("2022-11-08 won",),
    (2022, "San Jose"): ("2022-06-07 primary", "2022-11-08 won"),
    (2022, "Washington, D.C."): ("2022-06-21 Democratic", "2022-06-21 Republican", "2022-11-08 won"),
    (2023, "Charlotte"): ("2023-09-12 Democratic", "2023-11-07 won"),
    (2023, "Chicago"): ("2023-02-28 first", "2023-04-04 runoff"),
    (2023, "Colorado Springs"): ("2023-04-04 first", "2023-05-16 runoff"),
    (2023, "Columbus, Ohio"): ("2023-11-07 won",),
    (2023, "Dallas"): ("2023-05-06 won",),
    (2023, "Denver"): ("2023-04-04 first", "2023-06-06 runoff"),
    (2023, "Fort Worth"): ("2023-05-06 won",),
    (2023, "Houston"): ("2023-11-07 first", "2023-12-09 runoff"),
    (2023, "Indianapolis"): ("2023-05-02 Democratic", "2023-05-02 Republican", "2023-11-07 won"),
    (2023, "Jacksonville"): ("2023-03-21 first", "2023-05-16 runoff"),
    (2023, "Lincoln, Nebraska"): ("2023-04-04 primary", "2023-05-02 won"),
    (2023, "Memphis"): ("2023-10-05 won",),
    (2023, "Nashville"): ("2023-08-03 first", "2023-09-14 runoff"),
    (2023, "Orlando"): ("2023-11-07 won",),
    (2023, "Philadelphia"): ("2023-05-16 Democratic", "2023-05-16 Republican", "2023-11-07 won"),
    (2023, "San Antonio"): ("2023-05-06 won",),
    (2023, "Tampa"): ("2023-03-07 won",),
    (2023, "Tucson"): ("2023-08-01 Democratic", "2023-08-01 Republican", "2023-08-01 Libertarian", "2023-11-07 won"),
    (2024, "Anchorage"): ("2024-04-02 first", "2024-05-14 runoff"),
    (2024, "Austin"): ("2024-11-05 won",),
    (2024, "Baltimore"): ("2024-05-14 Democratic", "2024-05-14 Republican", "2024-11-05 won"),
    (2024, "El Paso"): ("2024-11-05 first", "2024-12-14 runoff"),
    (2024, "Fresno"): ("2024-03-05 primary-won",),
    (2024, "Honolulu"): ("2024-08-10 primary-won",),
    (2024, "Las Vegas"): ("2024-06-11 primary", "2024-11-05 won"),
    (2024, "Milwaukee"): ("2024-02-20 primary", "2024-04-02 won"),
    (2024, "Phoenix"): ("2024-11-05 won",),
    (2024, "Raleigh"): ("2024-11-05 won",),
    (2024, "Richmond, Virginia"): ("2024-11-05 won",),
    (2024, "Sacramento"): ("2024-03-05 primary", "2024-11-05 won"),
    (2024, "San Diego"): ("2024-03-05 primary", "2024-11-05 won"),
    (2024, "San Jose"): ("2024-03-05 primary-won",),
    (2024, "Tulsa"): ("2024-08-27 first", "2024-11-05 runoff"),
    (2025, "Albuquerque"): ("2025-11-04 first", "2025-12-09 runoff"),
    (2025, "Atlanta"): ("2025-11-04 won",),
    (2025, "Boston"): ("2025-09-09 primary", "2025-11-04 won"),
    (2025, "Charlotte"): ("2025-09-09 Democratic", "2025-11-04 won"),
    (2025, "Cincinnati"): ("2025-05-06 primary", "2025-11-04 won"),
    (2025, "Cleveland"): ("2025-11-04 won",),
    (2025, "Detroit"): ("2025-08-05 primary", "2025-11-04 won"),
    (2025, "Fort Worth"): ("2025-05-03 won",),
    (2025, "Miami"): ("2025-11-04 first", "2025-12-09 runoff"),
    (2025, "New Orleans"): ("2025-10-11 primary-won",),
    (2025, "Omaha"): ("2025-04-01 primary", "2025-05-13 won"),
    (2025, "Pittsburgh"): ("2025-05-20 Democratic", "2025-05-20 Republican", "2025-11-04 won"),
    (2025, "San Antonio"): ("2025-05-03 first", "2025-06-07 runoff"),
    (2025, "Seattle"): ("2025-08-05 primary", "2025-11-04 won"),
    (2025, "St. Louis"): ("2025-03-04 primary", "2025-04-08 won"),
    (2025, "Toledo, Ohio"): ("2025-05-06 primary", "2025-11-04 won"),
}
UNSTATED = {(2022, "North Las Vegas"), (2022, "Washington, D.C."), (2024, "Baltimore"), (2024, "Milwaukee")}
TITLES = {
    (2022, "Washington, D.C."): "2022 Washington, D.C., mayoral election",
    (2022, "Long Beach, California"): "2022 Long Beach, California, mayoral election",
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


def pages() -> list[list[str]]:
    titles = sorted(TITLES.get(key, f"{key[0]} {key[1]} mayoral election") for key in ARTICLES)
    return [titles[start : start + 40] for start in range(0, len(titles), 40)]


def fetch() -> None:
    for batch in pages():
        raw.fetch(SOURCE, page(batch), suffix=".json")


def parse() -> None:
    texts: dict[str, str] = {}
    for batch in pages():
        for found in json.loads(raw.cached(SOURCE, page(batch)).path.read_text())["query"]["pages"]:
            texts[found["title"]] = found["revisions"][0]["slots"]["main"]["content"]
    rows: list[dict[str, object]] = []
    for (year, city), rounds in ARTICLES.items():
        text = texts[TITLES.get((year, city), f"{year} {city} mayoral election")]
        text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", text, flags=re.DOTALL)
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
        boxes: list[list[dict[str, str]]] = []
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
                boxes.append([])
            elif boxes and kind.startswith("election box") and re.search(r"candidate|write-in|hold", kind):
                if fields.get("candidate") and fields.get("candidate") in [e.get("candidate") for e in boxes[-1]]:
                    boxes.append([])
                boxes[-1].append({**fields, "kind": kind})
        if len(boxes) != len(rounds):
            raise ValueError(f"{year} {city}: {len(boxes)} results boxes for {len(rounds)} rounds")
        for box, spec in zip(boxes, rounds, strict=True):
            day, stage = date.fromisoformat(spec[:10]), spec[11:]
            if (year, city) not in UNSTATED and not re.search(
                rf"{MONTHS[day.month - 1]} 0?{day.day}\b|\|{day.year}\|0?{day.month}\|0?{day.day}\b", text
            ):
                raise ValueError(f"{year} {city}: {day} is not in the article")
            for position, entry in enumerate(box):
                label = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", entry.get("candidate", ""))
                label = re.sub(r"\{\{[^{}]*\}\}|'{2,}|<[^>]+>", "", label)
                label = re.sub(
                    r"\((?:incumbent|inc\.|write-in|withdrawn)\)|\bwrite-in votes.*$", "", label, flags=re.IGNORECASE
                )
                label = re.sub(r"\s+", " ", label).strip(" ,;")
                votes = re.sub(r"[^\d]", "", re.sub(r"\{\{[^{}]*\}\}|'{2,}", "", entry.get("votes", "")))
                if re.fullmatch(r"(?i)(?:over|under)votes?|blank.*|spoiled.*", label):
                    continue
                if not votes and label == "":
                    continue
                if not votes:
                    raise ValueError(f"{year} {city} {day}: no votes for {label!r}")
                party = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", entry.get("party", ""))
                rows.append(
                    {
                        "year": year,
                        "city": city,
                        "day": day,
                        "stage": stage,
                        "position": position,
                        "candidate": label,
                        "write_in": "write-in" in entry["kind"]
                        or label == ""
                        or bool(re.search(r"(?i)write-?ins?\b|scattering", label)),
                        "party": next((key for key in PARTIES if key in party), None),
                        "independent": "Independent" in party,
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
            summary="Mayoral elections in large cities, 2022–2025, from the English Wikipedia's election articles",
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
            url="https://en.wikipedia.org/wiki/2023_Chicago_mayoral_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        for batch in pages():
            cs.artifact(raw.cached(SOURCE, page(batch)), source)
        fptp = cs.key("us.electoral_system", "fptp")
        polities = {
            code: (polity, path)
            for code, polity, path in conn.execute(
                """select c.code, t.polity_id, s.path from area a join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join slug s on s.entity_id = t.polity_id and s.is_current
                   where a.scheme_code in ('us.county', 'us.place') and c.code = any(%s)""",
                [[code for _, code, _ in CITIES.values()]],
            ).fetchall()
        }
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        held = set(
            conn.execute(
                """select x.value, e.date_from from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join source s on s.id = r.source_id
                   join external_id x on x.entity_id = c.office_id and x.scheme = 'us.office'
                   where s.title <> %s and x.value like any(%s)""",
                [TITLE, [f"{state}:{code}:%" for state, code, _ in CITIES.values()]],
            ).fetchall()
        )
        mine = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        elections = dict(
            conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election'"""
            ).fetchall()
        )
        paths: dict[UUID, str] = {}
        seen: set[UUID] = set()

        def place(entity: UUID, base: str) -> str:
            if entity in paths:
                return paths[entity]
            path = mine.get(entity)
            if path is None:
                path, n = base, 1
                while path in taken:
                    n += 1
                    path = f"{base}-{n}"
                taken.add(path)
            paths[entity] = path
            cs.add("slug", path=path, entity_id=entity)
            return path

        skipped = 0
        for (year, city, day, stage), rows in frame.group_by(["year", "city", "day", "stage"], maintain_order=True):
            state, code, owner_key = CITIES[city]
            owner_key = owner_key or f"{state}:{code}:mayor"
            if (owner_key, day) in held:
                skipped += 1
                continue
            display = re.sub(r", (?:[A-Z][a-z]+(?: [A-Z][a-z]+)?|D\.C\.)$", "", city)
            polity, polity_path = polities[code]
            owner = cs.key("us.office", owner_key)
            if owner in cs.minted and owner not in seen:
                seen.add(owner)
                cs.add("office", id=owner, polity_id=polity, kind="mayor")
                name(owner, f"Mayor of {display}")
            party = stage if stage in PARTIES else None
            primary = party is not None or stage.startswith("primary")
            kind = (
                "primary"
                if primary
                else "runoff"
                if stage == "runoff"
                else "general"
                if day.month == 11 and day.year % 2 == 0
                else "local"
            )
            parent_key = f"{day}:{state}" + (":primary" if primary else "")
            parent = cs.key("us.election", parent_key)
            if parent_key not in known and parent not in paths:
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity(f"US-{state}"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} {STATES[state]} {kind} elections")
                place(parent, f"us/{state.lower()}/{day}-{kind}")
            election_key = f"{day}:{state}:{code}" + (":primary" if primary else "")
            election = cs.key("us.election", election_key)
            if election in cs.minted and election not in paths:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {display} {kind} election")
                elections[election_key] = place(election, f"{polity_path}/{day}-{kind}")
            contest_key = f"wikipedia:{day}:{state}:{code}:mayor" + (f":{slug(party)}" if party else "")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                party_scope_id=cs.key("us.party", PARTIES[party]) if party else None,
                district_area_id=cs.area("us.county" if len(code) == 5 else "us.place", code),
                magnitude=1,
                round_number=2 if stage == "runoff" else 1,
                electoral_system_id=fptp,
                default_result_kind="counted",
                term="full",
            )
            name(
                contest,
                f"{day.year} {display} mayoral "
                + (
                    f"election, {party} primary"
                    if party
                    else {"runoff": "runoff"}.get(stage, "primary" if primary else "election")
                ),
            )
            place(
                contest,
                f"{elections[election_key]}/mayor" + (f"-{slug(party)}" if party else ""),
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
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD.format(year=year, city=display))
            unit = cs.unit("us.county" if len(code) == 5 else "us.place", code)
            ranked: list[tuple[int, UUID]] = []
            write_ins = 0
            for candidate, write_in, label, independent, votes in rows.select(
                "candidate", "write_in", "party", "independent", "votes"
            ).iter_rows():
                if write_in:
                    write_ins += votes
                    continue
                token = slug(candidate)
                person = cs.key(f"{SOURCE}.person", f"{state}:{code}:{token}")
                if person in cs.minted and person not in seen:
                    seen.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                option = cs.key("us.option", f"{contest_key}:{token}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", PARTIES[label]) if label else None,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=independent,
                )
                ranked.append((votes, option))
                if result_set in cs.minted:
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
            if write_ins:
                option = cs.key("us.option", f"{contest_key}:write-ins")
                cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                if result_set in cs.minted:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=write_ins,
                    )
            ranked.sort(key=lambda pair: -pair[0])
            advancing = stage in ("primary", "first")
            for _, option in ranked[: 2 if advancing else 1]:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="advanced" if advancing else "won" if party else "elected",
                    seats=None,
                )
        print(f"skipped {skipped} rounds another source already holds")
        return cs.write()
