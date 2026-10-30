import csv
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.ak_leg import KINDS
from ea.store import DATA, connect

SOURCE = "us.ak_primary_2026"
VERSION = "1"
DAY = date(2026, 8, 18)
INBOX = DATA / "inbox" / "manual" / "MF-394"
BASE = "https://www.elections.alaska.gov/results/26PRIM/"
PRECINCTS = "GA_ENR_Precinct_State_of_Alaska.csv"
SUMMARY = "ElectionSummaryReportRPT.pdf"
PUBLISHER = "Alaska Division of Elections"
FILES = {
    PRECINCTS: ("2026 Primary Election results by precinct", "text/csv"),
    SUMMARY: ("2026 Primary Election summary report, official results", "application/pdf"),
}
CONTESTS = {
    "U.S. Senator": ("senate", "senate", "2026 U.S. Senate primary in Alaska"),
    "U.S. Representative": (
        "house-at-large",
        "house:0",
        "2026 U.S. House primary in Alaska's at-large congressional district",
    ),
    "Governor / Lieutenant Governor": ("governor", "governor", "2026 gubernatorial primary in Alaska"),
}
PARTIES = {
    "REP": "Republican Party",
    "DEM": "Democratic Party",
    "LIB": "Libertarian Party",
    "GRN": "Green Party",
    "RAP": "Republican Moderate Party",
}
NOTE = (
    "Top-four primary: the four candidates with the most votes advance to the November 3 general election, and a "
    "candidate marked as advancing here is one on that ballot. Votes are the Division of Elections' official precinct "
    "totals for the August 18, 2026 primary. Absentee, early, and questioned ballots are reported by House district "
    "and overseas federal ballots statewide."
)
MEASURE = "BM#1 23RCF2"
TICKET = " The Division prints each governor and lieutenant governor ticket by surname only."


def fetch() -> None:
    for name, (_, media) in FILES.items():
        path = INBOX / name
        raw.store(
            SOURCE,
            BASE + name,
            path.read_bytes(),
            suffix=path.suffix,
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            media_type=media,
        )


def words(text: str) -> list[str]:
    plain = re.sub(r'"[^"]*"|“[^”]*”', "", text).casefold()
    return [word for word in re.findall(r"[a-z'-]+", plain) if word not in ("jr", "sr", "ii", "iii", "iv")]


def spell(text: str) -> str:
    last, _, given = text.partition(", ")
    if not given:
        return last
    suffix = re.search(r" (Jr\.|Sr\.|II|III|IV)$", given)
    given = given if suffix is None else given[: suffix.start()]
    return f"{given} {last}" + ("" if suffix is None else f" {suffix[1]}")


def emit() -> Path:
    tallies: dict[str, dict[str, dict[str, int]]] = {}
    codes: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(raw.cached(SOURCE, BASE + PRECINCTS).path.read_text(encoding="utf-8-sig").splitlines()):
        title = row["Contest_title"]
        if (title not in CONTESTS and title != MEASURE) or not row["total_votes"]:
            continue
        place, name = row["Precinct_name"].strip(), row["candidate_name"].strip()
        if precinct := re.match(r"(\d\d-\d\d\d) ", place):
            unit = f"p:{precinct[1]}"
        elif aggregate := re.fullmatch(r"District (\d+)\s*- (.*)", place):
            unit = f"a:{int(aggregate[1]):02}:{KINDS[aggregate[2]]}"
        elif place.startswith("HD99"):
            unit = "a:99"
        else:
            raise ValueError(f"unknown place {place}")
        by_unit = tallies.setdefault(title, {}).setdefault(name, {})
        by_unit[unit] = by_unit.get(unit, 0) + int(row["total_votes"])
        codes.setdefault(title, {})[name] = row["Party_Code"].partition("/")[0]
    answers = tallies.pop(MEASURE)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's August 18, 2026 top-four primary for U.S. Senate, U.S. House, and governor, by precinct",
            polity="US-AK",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-AK")
        state_area = cs.area("us.state", "02")
        parent = cs.unit("us.state", "02")
        sources = {}
        for file, (title, _) in FILES.items():
            sources[file] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[file],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=BASE + file,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, BASE + file), sources[file])
        source = sources[PRECINCTS]
        top_four = conn.execute(
            """select electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2022-08-16:AK:senate'"""
        ).fetchone()[0]
        election = cs.key("us.election", f"{DAY}:AK:primary")
        cs.add(
            "election",
            id=election,
            polity_id=state,
            parent_id=None,
            kind="primary",
            date_from=DAY,
            date_to=DAY,
            status="held",
        )
        name(election, "2026 Alaska primary election")
        if election in cs.minted:
            cs.add("slug", path=f"us/ak/{DAY}-primary", entity_id=election)
        parties = {
            code: party
            for code, text in PARTIES.items()
            for (party,) in conn.execute(
                """select distinct o.party_id from contest_option o join name n on n.entity_id = o.party_id
                   join contest k on k.id = o.contest_id join election e on e.id = k.election_id
                   where e.polity_id = %s and n.text = %s and n.is_preferred""",
                [state, text],
            ).fetchall()
        }
        if len(parties) != len(PARTIES):
            raise ValueError(f"parties {sorted(parties)} only")
        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s and c.role = 'head'""",
            [state],
        ).fetchall():
            spelled = words(text)
            if spelled:
                people.setdefault((spelled[0], spelled[-1]), set()).add(person)
        surnames: dict[str, set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               where x.value like '%%:AK:governor' and c.role = 'head'"""
        ).fetchall():
            spelled = words(text)
            if spelled:
                surnames.setdefault(spelled[-1], set()).add(person)
        units: dict[str, UUID] = {}

        def unit_of(code: str) -> UUID:
            if code in units:
                return units[code]
            if code.startswith("p:"):
                full, scheme, kind, home = f"{DAY.year}:AK:{code[2:]}", "us.precinct", "standard", code[2:4]
            else:
                full, scheme, kind, home = f"AK:{DAY.year}:{code[2:]}", "us.aggregate", "aggregate", code[2:4]
            units[code] = cs.unit(scheme, full)
            if units[code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=state,
                    scheme_code=scheme,
                    code=full,
                    parent_id=parent,
                    area_relation="within",
                    area_id=state_area if home == "99" else cs.area("us.sldl", f"AK:{int(home)}"),
                    kind=kind,
                    valid_from=DAY,
                    valid_to=DAY + timedelta(days=1),
                )
            return units[code]

        for title, (tail, general, label) in CONTESTS.items():
            key = f"{DAY}:AK:{general}"
            contest = cs.key("us.contest", key)
            template = conn.execute(
                """select kind, body_id, seat_id, office_id, district_area_id, boundary_set_id, magnitude, term, id
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value = %s""",
                [f"2026-11-03:AK:{general}"],
            ).fetchone()
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind=template[0],
                body_id=template[1],
                seat_id=template[2],
                office_id=template[3],
                district_area_id=template[4],
                boundary_set_id=template[5],
                magnitude=template[6],
                term=template[7],
                party_scope_id=None,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=top_four,
                default_result_kind="counted",
            )
            name(contest, label)
            if contest in cs.minted:
                cs.add("slug", path=f"us/ak/{DAY}-primary/{tail}", entity_id=contest)
            ballot = {
                person
                for (person,) in conn.execute(
                    """select c.person_id from candidacy c join contest_option o on o.id = c.option_id
                       where o.contest_id = %s and c.role = 'head'""",
                    [template[8]],
                ).fetchall()
            }
            options: dict[str, UUID] = {}
            persons: dict[str, UUID] = {}
            for candidate in tallies[title]:
                if tail == "governor":
                    text = candidate.partition("/")[0].strip()
                    matches = surnames.get(words(text)[-1], set())
                else:
                    text = spell(candidate)
                    spelled = words(text)
                    matches = people.get((spelled[0], spelled[-1]), set())
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", f"{key}:{slug(candidate)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, text)
                persons[candidate] = person
                option = options[candidate] = cs.key("us.option", f"{key}:{person}")
                party = parties.get(codes[title][candidate])
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=candidate if tail == "governor" else text,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=party is None,
                    is_incumbent=False,
                )
            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
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
                entity_id=result,
                kind="methodology",
                body_md=NOTE + (TICKET if tail == "governor" else ""),
            )
            for candidate, option in options.items():
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=option,
                    kind="advanced" if persons[candidate] in ballot else "lost",
                )
            by_place: dict[str, int] = {}
            rows = []
            for candidate, by_unit in tallies[title].items():
                for code, votes in by_unit.items():
                    by_place[code] = by_place.get(code, 0) + votes
                    rows.append((str(result), str(unit_of(code)), str(options[candidate]), 0, 1, 0, 0, votes))
            rows += [(str(result), str(unit_of(code)), None, 0, 4, 0, 0, votes) for code, votes in by_place.items()]
            cs.frame(
                "result_value",
                pl.DataFrame(
                    rows,
                    schema={
                        "result_set_id": pl.String,
                        "unit_id": pl.String,
                        "option_id": pl.String,
                        "channel": pl.Int16,
                        "measure": pl.Int16,
                        "count_round": pl.Int16,
                        "flags": pl.Int16,
                        "value": pl.Int64,
                    },
                    orient="row",
                ),
            )
            print(title, sum(by_place.values()))
        key = f"{DAY}:AK:question:ballot-measure-1-23rcf2"
        contest = cs.key("us.contest", key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=state_area,
            round_number=1,
            electoral_system_id=conn.execute(
                """select electoral_system_id from contest c join external_id x on x.entity_id = c.id
                   where x.scheme = 'us.contest' and x.value like '2024-11-05:AK:question:%%' limit 1"""
            ).fetchone()[0],
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="initiative")
        name(contest, "2026 Alaska primary Ballot Measure 1 (23RCF2)")
        if contest in cs.minted:
            cs.add("slug", path=f"us/ak/{DAY}-primary/ballot-measure-1-23rcf2", entity_id=contest)
        cs.add("citation", entity_id=contest, source_id=source)
        result = cs.key(f"{SOURCE}.result_set", key)
        cs.add(
            "result_set",
            id=result,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
        )
        options = {}
        for position, answer in enumerate(("YES", "NO")):
            options[answer] = cs.key("us.option", f"{key}:{answer.lower()}")
            cs.add(
                "contest_option",
                id=options[answer],
                contest_id=contest,
                kind="answer",
                ballot_position=position + 1,
                ballot_label=answer.title(),
            )
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options[answer],
                kind="won"
                if sum(answers[answer].values()) > sum(answers["NO" if answer == "YES" else "YES"].values())
                else "lost",
            )
        by_place = {}
        rows = []
        for answer, by_unit in answers.items():
            for code, votes in by_unit.items():
                by_place[code] = by_place.get(code, 0) + votes
                rows.append((str(result), str(unit_of(code)), str(options[answer]), 0, 1, 0, 0, votes))
        rows += [(str(result), str(unit_of(code)), None, 0, 4, 0, 0, votes) for code, votes in by_place.items()]
        cs.frame(
            "result_value",
            pl.DataFrame(
                rows,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ),
        )
        print(MEASURE, sum(by_place.values()))
        return cs.write()
