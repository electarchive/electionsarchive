import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import oe_primaries_2026 as oe
from ea.store import connect

SOURCE = "us.judicial_past_2026"
VERSION = "1"
OE_DATA = "https://raw.githubusercontent.com/openelections/openelections-data-{}/master/2026/{}"
FILES = {
    "OR": (date(2026, 5, 19), "20260519__or__primary__county.csv", "2026-05-19:OR:primary", "primary"),
    "TN": (date(2026, 8, 6), "20260801__tn__primary__county.csv", "2026-08-06:TN", "general"),
}
TITLES = {
    "Judge of the Supreme Court": "Justice of the Supreme Court",
    "Supreme Court, At Large": "Justice of the Supreme Court",
    "Judge of the Court of Appeals": "Judge of the Court of Appeals",
    "Court of Appeals - Eastern Division": "Judge of the Court of Appeals",
    "Court of Appeals - Western Division": "Judge of the Court of Appeals",
    "Court of Criminal Appeals - Eastern Division": "Judge of the Court of Criminal Appeals",
}
STATES = {"AR": "Arkansas", "OR": "Oregon", "TN": "Tennessee"}
AR_DAY = date(2026, 3, 3)
AR_PAGE = "https://arkansas.tally-enr.com/"
OE_PAGE = "https://github.com/openelections"
NOTE = (
    "County totals as published by the state's election authority; the county sums were checked against the statewide totals "
    "where the source gives them."
)
WRITE_INS = {"Write-ins", "Write-in", "Scattering"}


def url(state: str) -> str:
    return OE_DATA.format(state.lower(), FILES[state][1])


def fetch() -> None:
    for state in FILES:
        raw.fetch(SOURCE, url(state), suffix=".csv")


def ar_rows() -> list[dict[str, str | int]]:
    names = json.loads(raw.cached(oe.SOURCE, oe.url("ar", "contests")).path.read_bytes())["response"]["contests"]
    data = json.loads(raw.cached(oe.SOURCE, oe.url("ar", "data")).path.read_bytes())["ContestData"]
    rows: list[dict[str, str | int]] = []
    for contest in data:
        meta = names.get(contest["ContestID"])
        if meta is None or meta["contestName"] != "State Sup. Ct. Assoc. Justice Pos. 3":
            continue
        by_candidate: dict[str, int] = defaultdict(int)
        for fips, location in contest["Locations"].items():
            for choice in location["Choices"]:
                name = meta["choices"][choice["ChoiceID"]]["name"]
                by_candidate[name] += choice["TotalVotes"]
                rows.append(
                    {"state": "AR", "title": "Justice of the Supreme Court", "seat": "Position 3", "county": fips, "answer": "", "candidate": name, "votes": choice["TotalVotes"]}
                )
        statewide = {choice["ChoiceID"]: choice["TotalVotes"] for choice in contest["Choices"]}
        for choice_id, total in statewide.items():
            name = meta["choices"][choice_id]["name"]
            if by_candidate[name] != total:
                raise ValueError(f"AR {name}: counties {by_candidate[name]} differ from statewide {total}")
    return rows


def oe_rows(state: str) -> list[dict[str, str | int]]:
    path = raw.cached(SOURCE, url(state)).path
    rows: list[dict[str, str | int]] = []
    with path.open(newline="") as handle:
        for line in csv.DictReader(handle):
            title = TITLES.get(line["office"])
            if title is None:
                continue
            candidate = line["candidate"]
            answer = ""
            if state == "TN":
                verb, _, candidate = candidate.partition(" - ")
                answer = {"Retain": "Yes", "Replace": "No"}[verb]
            rows.append(
                {
                    "state": state,
                    "title": title,
                    "seat": line["district"] if state == "OR" else "",
                    "county": line["county"],
                    "answer": answer,
                    "candidate": candidate,
                    "votes": int(line["votes"] or 0),
                }
            )
    return rows


def emit() -> Path:
    rows = [*ar_rows(), *oe_rows("OR"), *oe_rows("TN")]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide judicial contests held before November 3, 2026: the Arkansas Supreme Court race, the Oregon Supreme Court and Court of Appeals races, and the Tennessee appellate retention questions, by county",
            polity="US",
        )
        counties: dict[tuple[str, str], UUID] = {}
        for code, text, id in conn.execute(
            """select u.code, n.text, u.id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ '^(05|41|47)'"""
        ).fetchall():
            counties.setdefault((code[:2], oe.plain(text)), id)
            counties[(code[:2], code)] = id
        areas = {
            state: conn.execute(
                """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
                   where p.iso_code = %s""",
                [f"US-{state}"],
            ).fetchone()[0]
            for state in STATES
        }
        pool: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        for state, person, text in conn.execute(
            """select split_part(x.value, ':', 2), cy.person_id, n.text
               from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ ':(AR|OR|TN):[a-z-]*(justice|judge)'"""
        ).fetchall():
            pool[(state, slug(text))].add(person)
        election_keys = {"AR": "2026-03-03:AR:primary", "OR": FILES["OR"][2], "TN": FILES["TN"][2]}
        elections = {
            state: conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = %s", [key]
            ).fetchone()[0]
            for state, key in election_keys.items()
        }
        days = {"AR": AR_DAY, "OR": FILES["OR"][0], "TN": FILES["TN"][0]}
        sources: dict[str, UUID] = {}
        for state in STATES:
            official = state == "AR"
            publisher = "Arkansas Secretary of State" if official else "OpenElections"
            title = f"{STATES[state]} {days[state]:%B %-d, %Y} election results"
            sources[state] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[state],
                kind="official_body" if official else "community",
                publisher=publisher,
                title=title,
                url=AR_PAGE if official else OE_PAGE,
                license="Public record" if official else None,
            )
            artifact = (
                raw.cached(oe.SOURCE, oe.url("ar", "data")) if official else raw.cached(SOURCE, url(state))
            )
            cs.artifact(artifact, sources[state])

        def person_of(state: str, text: str) -> UUID:
            found = pool.get((state, slug(text)), set())
            if len(found) == 1:
                return next(iter(found))
            id = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
            if id in cs.minted:
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=text, lang="en", kind="official", is_preferred=True)
            return id

        retention_system = cs.key("us.electoral_system", "retention")
        two_round = cs.key("us.electoral_system", "two_round")
        offices: set[UUID] = set()
        groups: dict[tuple[str, str, str, str], list[dict[str, str | int]]] = defaultdict(list)
        for row in rows:
            judge = ""
            if row["answer"]:
                judge = str(row["candidate"])
            groups[(str(row["state"]), str(row["title"]), str(row["seat"]), judge)].append(row)
        made = 0
        for (state, title, seat, judge), group in groups.items():
            retention = judge != ""
            seat_slug = slug(judge) if retention else (slug(seat) if seat else "")
            day = days[state]
            contest_key = f"{day}:{state}:{slug(title)}:{seat_slug}"
            office = cs.key("us.office", f"{slug(title)}:{state}")
            if office not in offices:
                offices.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=cs.polity(f"US-{state}"), kind="other")
                    cs.add("name", entity_id=office, text=f"{title} of {STATES[state]}", lang="en", kind="official", is_preferred=True)
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[state],
                kind="office",
                office_id=office,
                district_area_id=areas[state],
                magnitude=1,
                term="full",
                round_number=1,
                electoral_system_id=retention_system if retention else two_round,
                default_result_kind="counted",
            )
            label = f"{title}{f', {seat}' if seat else ''}"
            if retention:
                label = f"{title} retention: {judge}"
            cs.add("name", entity_id=contest, text=f"{days[state].year} {STATES[state]} {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                stage = "primary" if state != "TN" else "general"
                cs.add("slug", path=f"us/{state.lower()}/{day}-{stage}/{slug(title)}-{seat_slug}".rstrip("-"), entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[state])
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=elections[state],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for row in group:
                choice = str(row["answer"]) if retention else str(row["candidate"])
                by_choice[choice][str(row["county"])] += int(row["votes"])
            totals = {name: sum(by_unit.values()) for name, by_unit in by_choice.items()}
            ranked = sorted(totals, key=lambda name: -totals[name])
            options: dict[str, UUID] = {}
            for position, name in enumerate(by_choice):
                options[name] = cs.key("us.option", f"{contest_key}:{slug(name)}")
                is_answer = retention
                is_writein = name in WRITE_INS
                cs.add(
                    "contest_option",
                    id=options[name],
                    contest_id=contest,
                    kind="answer" if is_answer else "write_in" if is_writein else "candidate",
                    ballot_position=position + 1 if is_answer else None,
                    ballot_label=name,
                )
                if not is_answer and not is_writein:
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(name)}"),
                        person_id=person_of(state, name),
                        option_id=options[name],
                        role="head",
                        ballot_name=name,
                        is_independent=False,
                    )
            if retention:
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:yes:head"),
                    person_id=person_of(state, judge),
                    option_id=options["Yes"],
                    role="head",
                    ballot_name=judge,
                    is_independent=False,
                )
            contenders = [name for name in ranked if name not in WRITE_INS]
            winner = contenders[0]
            if retention:
                cs.add("outcome", result_set_id=result, option_id=options[winner], kind="won")
            elif totals[winner] * 2 > sum(totals.values()) or len(contenders) == 1:
                cs.add("outcome", result_set_id=result, option_id=options[winner], kind="won")
            by_place: dict[str, int] = defaultdict(int)
            values = []
            for name, by_unit in by_choice.items():
                for place, votes in by_unit.items():
                    unit = counties[(oe.FIPS[state], place if place.isdigit() else oe.plain(place))]
                    by_place[unit] += votes
                    values.append((str(result), str(unit), str(options[name]), 0, 1, 0, 0, votes))
            values += [(str(result), str(unit), None, 0, 4, 0, 0, votes) for unit, votes in by_place.items()]
            cs.frame(
                "result_value",
                pl.DataFrame(
                    values,
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
            made += 1
            print(contest_key, totals)
        print(made)
        return cs.write()
