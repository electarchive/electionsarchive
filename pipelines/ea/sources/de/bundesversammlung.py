import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.licenses import UNLICENSED
from ea.store import connect

SOURCE = "de.bundesversammlung"
VERSION = "1"
URL = "https://www.btg-bestellservice.de/pdf/20205000.pdf"
TITLE = "Stichwort: Die Bundesversammlung. Die Wahl der Bundespräsidentin oder des Bundespräsidenten"
PUBLISHER = "Deutscher Bundestag"
MONTHS = {
    "Januar": 1,
    "Februar": 2,
    "März": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}
VOTES, REGISTERED, CAST, INVALID, ABSTENTIONS = 1, 2, 3, 5, 6
WINNERS = {
    1: "Theodor Heuss",
    2: "Theodor Heuss",
    3: "Heinrich Lübke",
    4: "Heinrich Lübke",
    5: "Gustav Heinemann",
    6: "Walter Scheel",
    7: "Karl Carstens",
    8: "Richard von Weizsäcker",
    9: "Richard von Weizsäcker",
    10: "Roman Herzog",
    11: "Johannes Rau",
    12: "Horst Köhler",
    13: "Horst Köhler",
    14: "Christian Wulff",
    15: "Joachim Gauck",
    16: "Frank-Walter Steinmeier",
    17: "Frank-Walter Steinmeier",
}
TERMS = {
    1: (date(1949, 9, 13), date(1954, 9, 12), "term_end"),
    2: (date(1954, 9, 13), date(1959, 9, 12), "term_end"),
    3: (date(1959, 9, 13), date(1964, 9, 12), "term_end"),
    4: (date(1964, 9, 13), date(1969, 6, 30), "resigned"),
    5: (date(1969, 7, 1), date(1974, 6, 30), "term_end"),
    6: (date(1974, 7, 1), date(1979, 6, 30), "term_end"),
    7: (date(1979, 7, 1), date(1984, 6, 30), "term_end"),
    8: (date(1984, 7, 1), date(1989, 6, 30), "term_end"),
    9: (date(1989, 7, 1), date(1994, 6, 30), "term_end"),
    10: (date(1994, 7, 1), date(1999, 6, 30), "term_end"),
    11: (date(1999, 7, 1), date(2004, 6, 30), "term_end"),
    12: (date(2004, 7, 1), date(2009, 6, 30), "term_end"),
    13: (date(2009, 7, 1), date(2010, 5, 31), "resigned"),
    14: (date(2010, 7, 1), date(2012, 2, 17), "resigned"),
    15: (date(2012, 3, 18), date(2017, 3, 18), "term_end"),
    16: (date(2017, 3, 19), date(2022, 3, 18), "term_end"),
    17: (date(2022, 3, 19), None, None),
}
CAVEATS = {
    (14, 1): (
        "The printed figures for the first ballot (600, 499, 126, and 3 votes, 9 abstentions, and 1 invalid vote) add up "
        "to 1,238 against the 1,242 ballots the same table gives as cast. They are stored as printed."
    ),
    (16, 1): (
        "The printed 1,239 ballots cast equal the votes for candidates plus the 103 abstentions, so the 14 invalid votes "
        "are not part of it. All three figures are stored as printed."
    ),
}
METHODOLOGY = (
    "The Federal Convention, which meets only to elect the Federal President, is made up of the members of the Bundestag "
    "and an equal number of members chosen by the Land parliaments (Basic Law art. 54). Election is by secret ballot "
    "without debate. A candidate who wins the votes of a majority of the members is elected in the first or second "
    "ballot; in a third ballot, the candidate with the most votes is elected. Abstentions are stored as blank ballots, "
    "and the registered figure is the number of members. Sixteen Berlin members without a vote in 1949 are not counted."
)
NOTE = (
    "Counts are transcribed from the Bundestag's own publication on the Federal Convention, which prints each ballot's "
    "table and its summary line. Each ballot's candidate votes, abstentions, and invalid votes were checked against the "
    "ballots cast it prints."
)
LINE = re.compile(r"(\S.*?)\s{2,}(?:(.*?)\s{2,})?([\d.]+)\s+(\d+,\d+)\s*$")
GAUCK = re.compile(r"(Joachim Gauck) (.*?)\s{2,}([\d.]+)\s+(\d+,\d+)\s*$")
NEXT = re.compile(r"Mitglieder;|^Enthaltungen|^ungültige|^abgegebene|^nicht abgegebene|Nein-Stimmen")
DONE = re.compile(r"(?<!nicht )abgegebene Stimmen [\d.]+ \(")


def number(text: str) -> int:
    return int(text.replace(".", ""))


def figure(tail: str, label: str) -> int | None:
    found = re.search(rf"(?<!nicht ){label} ([\d.]+)", tail)
    return number(found[1]) if found else None


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> list[dict[str, Any]]:
    path = raw.cached(SOURCE, URL).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    lines = [line.lstrip("\f").replace(" ", " ") for line in text.split("\n")]
    start = next(i for i, line in enumerate(lines) if line.startswith("1. Bundesversammlung vom"))
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    ballot: dict[str, Any] | None = None
    for line in lines[start:]:
        head = re.match(r"(\d+)\. Bundesversammlung vom (\d+)\. (\w+) (\d{4}) in (\w+)", line)
        if head:
            current = {
                "number": int(head[1]),
                "date": date(int(head[4]), MONTHS[head[3]], int(head[2])),
                "city": head[5],
                "ballots": [],
            }
            out.append(current)
            ballot = None
            continue
        if current is None:
            continue
        round_head = re.match(r"(\d)\. Wahlgang\s*$", line)
        if round_head:
            ballot = {"candidates": [], "tail": ""}
            current["ballots"].append(ballot)
            continue
        if ballot is None:
            continue
        if not ballot["tail"]:
            row = GAUCK.match(line) or (LINE.match(line) if "Mitglieder" not in line else None)
            if row:
                ballot["candidates"].append((re.sub(r"\s*\(.*\)$", "", row[1]).strip(), number(row[3])))
                continue
        if NEXT.search(line.strip()) or (ballot["tail"] and re.match(r"\s*(ungültige|abgegebene|Enthaltungen)", line)):
            ballot["tail"] += " " + line.strip()
            if DONE.search(ballot["tail"]):
                ballot = None
    for election in out:
        for ballot in election["ballots"]:
            tail = ballot["tail"]
            members = re.search(r"([\d.]+) Mitglieder", tail)
            ballot["members"] = number(members[1]) if members else None
            ballot["required"] = figure(tail, "absolute Mehrheit")
            ballot["abstentions"] = figure(tail, "Enthaltungen")
            ballot["invalid"] = figure(tail, "ungültige Stimmen")
            ballot["no"] = figure(tail, "Nein-Stimmen")
            ballot["cast"] = figure(tail, "abgegebene Stimmen")
    if len(out) != 17:
        raise ValueError(f"expected 17 conventions, found {len(out)}")
    for election in out:
        for index, ballot in enumerate(election["ballots"], 1):
            counted = sum(votes for _, votes in ballot["candidates"])
            total = counted + sum(ballot[k] or 0 for k in ("abstentions", "invalid", "no"))
            if total != ballot["cast"] and (election["number"], index) not in CAVEATS:
                raise ValueError(f"convention {election['number']} ballot {index}: {total} against {ballot['cast']}")
    return out


def emit() -> Path:
    conventions = parse()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Federal Conventions electing the Federal President of Germany, 1949–2022, with every ballot",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license=UNLICENSED,
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        office = cs.key("de.office", "president")
        cs.add("office", id=office, polity_id=germany, kind="head_of_state")
        name(office, "Federal President of Germany")
        name(office, "Bundespräsident", "de", "native")
        body = cs.key("de.body", "bundesversammlung")
        cs.add("body", id=body, polity_id=germany, kind="electoral_college")
        name(body, "Federal Convention")
        name(body, "Bundesversammlung", "de", "native")
        cs.add("scheme", code="de.assembly", polity_id=germany, kind="electoral_district", rank=0, noun="assembly")
        unit = cs.unit("de.assembly", "bundesversammlung")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=germany,
            scheme_code="de.assembly",
            code="bundesversammlung",
            area_relation="unlocated",
            kind="aggregate",
        )
        name(unit, "Federal Convention in session")
        system = cs.key("de.electoral_system", "bundespraesident")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=germany,
            rules={
                "formula": "repeated_ballot",
                "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}, {"fromRound": 3, "share": 0}],
            },
            rules_version=1,
        )
        name(system, "Election of the Federal President by the Federal Convention")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)

        persons: dict[str, UUID] = {}

        def person(full: str) -> UUID:
            if full not in persons:
                persons[full] = cs.key(f"{SOURCE}.person", slug(full))
                if persons[full] in cs.minted:
                    cs.add("person", id=persons[full])
                    name(persons[full], full, "de")
            return persons[full]

        values = []
        for convention in conventions:
            no = convention["number"]
            day = convention["date"]
            election = cs.key("de.election", f"{day}:bundespraesident")
            cs.add(
                "election",
                id=election,
                polity_id=germany,
                kind="indirect",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day.year} German presidential election")
            path = f"de/{day}-president"
            cs.add("slug", path=path, entity_id=election)
            previous = None
            contest = None
            ballots = convention["ballots"]
            required = ballots[0]["required"]
            for index, ballot in enumerate(ballots, 1):
                key = f"{day}:bundespraesident:{index}"
                contest = cs.key("de.contest", key)
                override = {"required": required} if index < 3 and required else {}
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    body_id=body,
                    magnitude=1,
                    round_number=index,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    rules_override=override,
                    default_result_kind="counted",
                )
                label = f"Ballot {index}" if len(ballots) > 1 else f"{day.year} presidential vote"
                name(contest, label)
                cs.add("slug", path=f"{path}/ballot-{index}" if len(ballots) > 1 else f"{path}/vote", entity_id=contest)
                if index == 1:
                    cs.add("note", entity_id=contest, kind="methodology", body_md=NOTE)
                if (no, index) in CAVEATS:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=CAVEATS[no, index])
                previous = contest
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=0,
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

                won = None
                for candidate, votes in ballot["candidates"]:
                    option = cs.key("de.option", f"{key}:{slug(candidate)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=candidate)
                    cs.add(
                        "candidacy",
                        id=cs.key("de.candidacy", f"{key}:{slug(candidate)}"),
                        person_id=person(candidate),
                        option_id=option,
                    )
                    value(VOTES, option, votes)
                    if candidate == WINNERS[no]:
                        won = option
                if ballot["no"]:
                    option = cs.key("de.option", f"{key}:no")
                    cs.add(
                        "contest_option", id=option, contest_id=contest, kind="none_of_the_above", ballot_label="Nein"
                    )
                    value(VOTES, option, ballot["no"])
                if index == 1 and ballot["members"]:
                    value(REGISTERED, None, ballot["members"])
                value(CAST, None, ballot["cast"])
                value(ABSTENTIONS, None, ballot["abstentions"] or 0)
                if ballot["invalid"] is not None:
                    value(INVALID, None, ballot["invalid"])
                if index == len(ballots):
                    if won is None:
                        raise ValueError(f"convention {no}: {WINNERS[no]} not on the last ballot")
                    cs.add("outcome", result_set_id=result_set, option_id=won, kind="elected")
            start, end, via = TERMS[no]
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"{day}:{slug(WINNERS[no])}"),
                person_id=person(WINNERS[no]),
                office_id=office,
                from_date=start,
                to_date=end,
                acquired_via="indirect",
                ended_via=via,
                contest_id=contest,
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        return cs.write()
