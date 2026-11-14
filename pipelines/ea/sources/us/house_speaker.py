import html
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.house_speaker"
VERSION = "1"
CRS_URL = "https://www.congress.gov/crs_external_products/RL/PDF/RL30857/RL30857.29.pdf"
CRS_ARCHIVED = f"https://web.archive.org/web/20260109185605id_/{CRS_URL}"
CRS_TITLE = "Speakers of the House: Elections, 1913-2023 (RL30857, updated November 6, 2023)"
CRS_PUBLISHER = "Congressional Research Service"
CLERK_TITLE = "Roll Call Votes, U.S. House of Representatives"
CLERK_PUBLISHER = "Clerk of the House of Representatives"
ROLL = "https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"
SESSIONS = "https://history.house.gov/Institution/Session-Dates/All/"
LEGISLATORS = "https://unitedstates.github.io/congress-legislators/legislators-{kind}.json"
VOTES, ABSTENTIONS = 1, 6
CLERK_FROM = 1991
ROLLS = {
    **{(year, 1): [2] for year in range(1991, 2026, 2) if year != 1997},
    (1997, 1): [3],
    (2015, 2): [581],
    (2023, 1): [2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 15, 16, 18, 20],
    (2023, 2): [521, 523, 525, 527],
}
SPECIAL = {
    "June 4": date(1936, 6, 4),
    "Sept. 16": date(1940, 9, 16),
    "Jan. 10": date(1962, 1, 10),
    "June 6": date(1989, 6, 6),
}
RESOLUTIONS = {
    date(1936, 6, 4): ("William B. Bankhead", "AL", "H.Res. 543"),
    date(1940, 9, 16): ("Sam Rayburn", "TX", "H.Res. 602"),
}
MISPRINTED = {(2023, "ballot #15")}
DAYS_1923 = [date(1923, 12, 3)] * 4 + [date(1923, 12, 4)] * 4 + [date(1923, 12, 5)]
OUTSIDERS = {
    "Allen West": ("bioguide", "W000807"),
    "Colin Powell": ("wikidata", "Q150851"),
    "David Walker": ("wikidata", "Q5236974"),
    "Donald J. Trump": ("wikidata", "Q22686"),
    "Hon. Jeff Sessions": ("bioguide", "S001141"),
    "Hon. Rand Paul": ("bioguide", "P000603"),
    "Hon. Tammy Duckworth": ("bioguide", "D000622"),
    "Duckworth": ("bioguide", "D000622"),
    "Joseph Biden": ("bioguide", "B000444"),
    "Stacey Abrams": ("wikidata", "Q7595813"),
    "Zeldin": ("bioguide", "Z000017"),
    "Boehner": ("bioguide", "B000589"),
    "Miller, Candice": ("bioguide", "M001150"),
    "Michel": ("bioguide", "M000692"),
    "Walker": ("bioguide", "W000068"),
}
FULL = {
    "Q150851": "Colin Powell",
    "Q5236974": "David M. Walker",
    "Q22686": "Donald J. Trump",
    "Q7595813": "Stacey Abrams",
}
METHODOLOGY = (
    "The House elects its Speaker at the start of each Congress, and again when the office falls vacant, from candidates "
    "nominated on the floor. Since 1839 the vote is viva voce: each Member-elect answers the roll with a name, and a "
    "candidate needs a majority of the votes cast for a person by name, so Members answering present lower the "
    "threshold. Ballots are repeated until someone wins that majority. Answers of present are stored as blank ballots."
)
CRS_NOTE = (
    "Counts for 1913 to 1989 are from the Congressional Research Service's table of votes cast on ballots electing the "
    "Speaker, which it compiled from the House Journal and the Congressional Record. Answers of present are given only "
    "for 1923."
)
CLERK_NOTE = (
    "Counts are the Clerk of the House's roll call totals by candidate, which also list the Members answering present."
)
RESOLUTION_NOTE = "The House elected the Speaker by adopting {resolution}, without a roll call between nominees."


def fetch() -> None:
    raw.fetch(SOURCE, CRS_ARCHIVED, suffix=".pdf")
    raw.fetch(SOURCE, SESSIONS, suffix=".html")
    for kind in ("historical", "current"):
        raw.fetch(SOURCE, LEGISLATORS.format(kind=kind), suffix=".json")
    for (year, _), rolls in ROLLS.items():
        for roll in rolls:
            url = ROLL.format(year=year, roll=roll)
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.fetch(SOURCE, url, suffix=".xml")
                time.sleep(1)


def openings() -> dict[int, date]:
    text = raw.cached(SOURCE, SESSIONS).path.read_text()
    out: dict[int, date] = {}
    for row in re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL):
        cells = [
            html.unescape(re.sub(r"<[^>]+>|\s+", " ", c)).strip() for c in re.findall(r"<td.*?</td>", row, re.DOTALL)
        ]
        if len(cells) > 2 and cells[0] and cells[1] == "1":
            day = datetime.strptime(re.sub(r"(\w{3})\w*\.? ", r"\1 ", cells[2]), "%b %d, %Y").date()
            out.setdefault(day.year, day)
    return out


def legislators() -> list[dict[str, Any]]:
    return [
        person
        for kind in ("historical", "current")
        for person in json.loads(raw.cached(SOURCE, LEGISLATORS.format(kind=kind)).path.read_text())
    ]


def member(people: list[dict[str, Any]], name: str, state: str, day: date) -> str:
    last = re.sub(r"\s*\(.*?\)", "", name).split()[-1]
    found = {
        person["id"]["bioguide"]
        for person in people
        if person["name"]["last"] == last
        and (name == last or person["name"]["first"][0] == name[0])
        and any(
            term["type"] == "rep"
            and term["state"] == state
            and date.fromisoformat(term["start"]) <= date.fromordinal(day.toordinal() + 60)
            and date.fromisoformat(term["end"]) >= day
            for term in person["terms"]
        )
    }
    if len(found) != 1:
        raise ValueError(f"{name} ({state}) on {day}: {sorted(found)}")
    return found.pop()


def crs() -> list[dict[str, Any]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CRS_ARCHIVED).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if "Table 1. Votes Cast on Ballots" in line and "...." not in line)
    rows: list[dict[str, Any]] = []
    for line in lines[start:]:
        if line.strip().startswith("Source:"):
            break
        cells = re.split(r"\s{2,}", line.strip())
        head = re.fullmatch(r"(\d{4})(?: \((.*?)\))?[a-d]?", cells[0])
        if head:
            rows.append({"year": int(head[1]), "label": head[2] or "", "cells": cells[1:]})
        elif re.fullmatch(r"\(ballot #\d+\)", cells[0]):
            rows.append({"year": rows[-1]["year"], "label": cells[0][1:-1], "cells": cells[1:]})
        elif rows and len(cells) == 2 and re.fullmatch(r"\d+", cells[1]):
            rows[-1]["cells"] += cells
    elections: list[dict[str, Any]] = []
    for row in rows:
        cells = row["cells"]
        if "voice vote" in cells:
            continue
        candidates = []
        for name, votes in zip(cells[::2], cells[1::2], strict=True):
            found = re.fullmatch(r"(?:(?:Sen|Rep)\. )?(.*?)(?: \((?:[\w-]+-)?([A-Z]{2})\)[a-d]?|[a-d])", name)
            if not found:
                raise ValueError(f"{row['year']}: {name}")
            candidates.append({"name": found[1], "state": found[2], "votes": int(votes)})
        day = SPECIAL.get(row["label"])
        elections.append(
            {
                "year": row["year"],
                "label": row["label"],
                "day": day,
                "ballots": [{"candidates": candidates, "present": None}],
            }
        )
    return elections


def appendix_1923(elections: list[dict[str, Any]]) -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CRS_ARCHIVED).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    block = text[text.rindex("Table A-1. 1923") :]
    block = block[: block.index("Source:")]
    target = next(e for e in elections if e["year"] == 1923)
    names = {c["name"].split()[-1]: c for c in target["ballots"][0]["candidates"]}
    ballots: list[dict[str, Any]] = [{"candidates": [], "present": None} for _ in range(9)]
    for line in block.split("\n"):
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) != 10:
            continue
        label = re.sub(r" \([RD]\)$", "", cells[0])
        counts = [0 if cell == "-" else int(cell) for cell in cells[1:]]
        for ballot, count in zip(ballots, counts, strict=True):
            if label == "Present":
                ballot["present"] = count
            elif count:
                ballot["candidates"].append({**names[label], "votes": count})
    first = {c["name"]: c["votes"] for c in ballots[0]["candidates"]}
    if first != {c["name"]: c["votes"] for c in target["ballots"][0]["candidates"]}:
        raise ValueError("1923 ballot 1 differs between Table 1 and Table A-1")
    target["ballots"] = ballots
    target["days"] = DAYS_1923


def clerk(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bioguides = {person["id"]["bioguide"]: person for person in people}
    elections = []
    for (year, _), rolls in ROLLS.items():
        ballots = []
        days = []
        for roll in rolls:
            url = ROLL.format(year=year, roll=roll)
            root = ET.parse(raw.cached(SOURCE, url).path).getroot()
            meta = root.find("vote-metadata")
            if meta is None:
                raise ValueError(url)
            days.append(datetime.strptime(meta.findtext("action-date", "").title(), "%d-%b-%Y").date())
            seats = {(seat.text or "").strip(): seat for seat in root.iter("legislator")}
            candidates = []
            present = None
            for total in meta.iter("totals-by-candidate"):
                label = total.findtext("candidate", "")
                count = int(total.findtext("candidate-total", "0"))
                if label == "Present":
                    present = count
                elif label != "Not Voting" and count:
                    seat = seats.get(label)
                    scheme, value = OUTSIDERS.get(label) or (
                        "bioguide",
                        seat is not None
                        and seat.get("name-id")
                        or member(
                            people, label.split(" (")[0], seat.get("state", "") if seat is not None else "", days[-1]
                        ),
                    )
                    if scheme == "bioguide":
                        name = bioguides[value]["name"]
                        full = name.get("official_full") or f"{name['first']} {name['last']}"
                    else:
                        full = FULL[value]
                    last = bioguides[value]["name"]["last"] if scheme == "bioguide" else full.split()[-1]
                    candidates.append({"name": full, "last": last, "id": (scheme, value), "votes": count})
            ballots.append({"candidates": candidates, "present": present, "url": url})
        elections.append({"year": year, "day": days[0], "days": days, "ballots": ballots, "clerk": True})
    return elections


def parse() -> list[dict[str, Any]]:
    people = legislators()
    opened = openings()
    rows = crs()
    elections = [row for row in rows if row["year"] < CLERK_FROM and row["label"] != "ballot #9"]
    appendix_1923(elections)
    for election in elections:
        election["day"] = election["day"] or opened[election["year"]]
        election.setdefault("days", [election["day"]] * len(election["ballots"]))
        for ballot in election["ballots"]:
            for candidate in ballot["candidates"]:
                candidate["id"] = ("bioguide", member(people, candidate["name"], candidate["state"], election["day"]))
    for day, (name, state, resolution) in RESOLUTIONS.items():
        elections.append(
            {
                "year": day.year,
                "day": day,
                "days": [day],
                "resolution": resolution,
                "winner": {"name": name, "id": ("bioguide", member(people, name, state, day))},
                "ballots": [],
            }
        )
    checked = sorted(clerk(people), key=lambda e: e["day"])
    current = -1
    for row in rows:
        if row["year"] < CLERK_FROM:
            continue
        index = 0
        if re.fullmatch(r"ballot #\d+", row["label"]):
            index = int(row["label"][8:]) - 1
        else:
            current = next(i for i, e in enumerate(checked) if i > current and e["year"] == row["year"])
        ballot = checked[current]["ballots"][index]
        printed = sorted((c["name"].split(",")[0].split()[-1], c["votes"]) for c in row["ballots"][0]["candidates"])
        counted = sorted((c["last"], c["votes"]) for c in ballot["candidates"])
        if printed != counted and (row["year"], row["label"]) not in MISPRINTED:
            raise ValueError(f"{row['year']} {row['label']}: CRS {printed} against the Clerk {counted}")
        for candidate in ballot["candidates"]:
            same = [
                c for c in row["ballots"][0]["candidates"] if c["name"].split(",")[0].split()[-1] == candidate["last"]
            ]
            if len(same) > 1:
                same = [c for c in same if c["votes"] == candidate["votes"]]
            if len(same) == 1:
                candidate["name"] = same[0]["name"]
    elections += checked
    for election in elections:
        if election.get("clerk") and election["day"].year != election["year"]:
            raise ValueError(f"{election['year']}: roll call dated {election['day']}")
        for index, ballot in enumerate(election["ballots"], 1):
            votes = sorted((c["votes"] for c in ballot["candidates"]), reverse=True)
            won = votes[0] * 2 > sum(votes)
            if won != (index == len(election["ballots"])):
                raise ValueError(f"{election['day']} ballot {index}: majority {won}")
    elections.sort(key=lambda e: e["day"])
    return elections


def emit() -> Path:
    elections = parse()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Elections of the Speaker of the U.S. House of Representatives, 1913–2025, with every ballot",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        sources = {}
        for publisher, title, url in (
            (CRS_PUBLISHER, CRS_TITLE, CRS_URL),
            (CLERK_PUBLISHER, CLERK_TITLE, "https://clerk.house.gov/Votes"),
        ):
            sources[publisher] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[publisher],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public domain",
            )
        cs.artifact(raw.cached(SOURCE, CRS_ARCHIVED), sources[CRS_PUBLISHER])
        cs.artifact(raw.cached(SOURCE, SESSIONS), sources[CRS_PUBLISHER])
        house = cs.key("us.body", "house")
        office = cs.key("us.office", "speaker")
        cs.add("office", id=office, polity_id=us, body_id=house, kind="speaker")
        if office in cs.minted:
            name(office, "Speaker of the United States House of Representatives")
        unit = cs.unit("us.assembly", "house")
        system = cs.key("us.electoral_system", "house_speaker")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=us,
            rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
            rules_version=1,
        )
        name(system, "Election of the Speaker by the House of Representatives")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)

        persons: dict[tuple[str, str], UUID] = {}

        def person(id: tuple[str, str], full: str) -> UUID:
            if id not in persons:
                persons[id] = cs.key(*id)
                if persons[id] in cs.minted:
                    cs.add("person", id=persons[id])
                    name(persons[id], full)
            return persons[id]

        years: dict[int, int] = {}
        for election in elections:
            years[election["year"]] = years.get(election["year"], 0) + 1
        values = []
        for election in elections:
            day = election["day"]
            source = sources[CLERK_PUBLISHER if election.get("clerk") else CRS_PUBLISHER]
            key = f"{day}:speaker"
            election_id = cs.key("us.election", key)
            cs.add(
                "election",
                id=election_id,
                polity_id=us,
                kind="indirect",
                date_from=day,
                date_to=election["days"][-1],
                status="held",
            )
            label = f"{day:%B} {day.year}" if years[election["year"]] > 1 else str(day.year)
            name(election_id, f"{label} election of the Speaker of the House")
            path = f"us/{day}-speaker"
            cs.add("slug", path=path, entity_id=election_id)
            if "resolution" in election:
                contest = cs.key("us.contest", f"{key}:1")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="office",
                    office_id=office,
                    body_id=house,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="tacit",
                )
                name(contest, f"{day.year} Speaker vote")
                cs.add("slug", path=f"{path}/vote", entity_id=contest)
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="methodology",
                    body_md=RESOLUTION_NOTE.format(resolution=election["resolution"]),
                )
                winner = election["winner"]
                option = cs.key("us.option", f"{key}:1:{slug(winner['name'])}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=winner["name"])
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:1:{slug(winner['name'])}"),
                    person_id=person(winner["id"], winner["name"]),
                    option_id=option,
                )
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:1")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election_id,
                    contest_id=contest,
                    stage="official",
                    kind="tacit",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                continue
            previous = None
            ballots = election["ballots"]
            for index, ballot in enumerate(ballots, 1):
                contest = cs.key("us.contest", f"{key}:{index}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="office",
                    office_id=office,
                    body_id=house,
                    magnitude=1,
                    round_number=index,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                previous = contest
                name(contest, f"Ballot {index}" if len(ballots) > 1 else f"{day.year} Speaker vote")
                cs.add("slug", path=f"{path}/ballot-{index}" if len(ballots) > 1 else f"{path}/vote", entity_id=contest)
                if index == 1:
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="methodology",
                        body_md=CLERK_NOTE if election.get("clerk") else CRS_NOTE,
                    )
                if "url" in ballot:
                    cs.artifact(raw.cached(SOURCE, ballot["url"]), source)
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{index}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election_id,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )

                def value(measure: int, option: UUID | None, count: int) -> None:
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": None if option is None else str(option),
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "value": count,
                            "flags": 0,
                        }
                    )

                top = max(ballot["candidates"], key=lambda c: c["votes"])
                for candidate in ballot["candidates"]:
                    option = cs.key("us.option", f"{key}:{index}:{slug(candidate['name'])}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_label=candidate["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{index}:{slug(candidate['name'])}"),
                        person_id=person(candidate["id"], candidate["name"]),
                        option_id=option,
                    )
                    value(VOTES, option, candidate["votes"])
                    if index == len(ballots) and candidate is top:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if ballot["present"] is not None:
                    value(ABSTENTIONS, None, ballot["present"])
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        return cs.write()
