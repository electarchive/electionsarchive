import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.oe_primaries_2026 import BROWSER, OK_BASE, plain, title
from ea.store import connect

SOURCE = "us.ok_statewide_2026"
VERSION = "1"
PUBLISHER = "Oklahoma State Election Board"
PAGE = "https://results.okelections.gov/OKER/"
PRIMARY = date(2026, 6, 16)
RUNOFF = date(2026, 8, 25)
EXEC = {
    "LIEUTENANT GOVERNOR": ("lieutenant-governor", "lieutenant gubernatorial"),
    "ATTORNEY GENERAL": ("attorney-general", "attorney general"),
    "STATE TREASURER": ("treasurer", "treasurer"),
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": ("superintendent-of-public-instruction", "state superintendent"),
    "COMMISSIONER OF LABOR": ("commissioner-of-labor", "labor commissioner"),
    "INSURANCE COMMISSIONER": ("insurance-commissioner", "insurance commissioner"),
    "CORPORATION COMMISSIONER": ("corporation-commissioner", "corporation commissioner"),
}
RACES = {
    "20260616": (12002, 12004, 12005, 12006, 12007, 12008, 12009, 12040, 13006, 13009, 30001),
    "20260825": (12006, 12007, 12008, 12040, 30002, 30003),
}
QUESTIONS = {
    30001: ("initiative", "832"),
    30002: ("mandatory_referendum", "844"),
    30003: ("mandatory_referendum", "846"),
}
NOTE = (
    "Results by county from the Oklahoma State Election Board's official results. Each candidate's county votes add "
    "up to the statewide total the Board publishes for the race."
)


def url(day: str, race: str) -> str:
    return (
        f"{OK_BASE}enrapi/getelec/{day}/SW/xx" if race == "getelec" else f"{OK_BASE}enrapi/GetCntyResults/{day}/{race}"
    )


def fetch() -> None:
    token = httpx.put(
        OK_BASE + "enrapi/login/",
        json={"Username": "appuser", "Password": "X098de!22k098Mgfd"},
        headers=BROWSER,
        timeout=60,
    ).json()[1:]
    for day, races in RACES.items():
        for race in ("getelec", *map(str, races)):
            target = url(day, race)
            try:
                raw.cached(SOURCE, target)
            except LookupError:
                content = httpx.get(
                    target, headers={**BROWSER, "Authorization": f"Bearer {token}"}, timeout=300
                ).content
                raw.store(
                    SOURCE,
                    target,
                    content,
                    suffix=".json",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/json",
                )


def load(day: str) -> tuple[dict[int, dict], dict[int, dict]]:
    index = json.loads(raw.cached(SOURCE, url(day, "getelec")).path.read_text())
    meta = {entry["raceID"]: entry for entry in index["races"]}
    totals = {entry["raceID"]: entry for entry in index["results"]}
    return meta, totals


def counties(day: str, race: int, meta: dict, totals: dict) -> dict[str, dict[str, int]]:
    cached = raw.cached(SOURCE, url(day, str(race)))
    by_candidate: dict[str, dict[str, int]] = defaultdict(dict)
    names = [candidate["candName"] for candidate in meta["raceCandidates"]]
    for county in json.loads(cached.path.read_text())["summaryCandResults"]:
        fips = f"40{2 * int(county['countyID']) - 1:03}"
        for name, result in zip(names, county["candResults"], strict=True):
            by_candidate[name][fips] = int(result["totalVotes"])
    for name, result in zip(names, totals["candResults"], strict=True):
        if sum(by_candidate[name].values()) != int(result["totalVotes"]):
            raise ValueError(f"{day} race {race} {name}: county sum differs from the statewide {result['totalVotes']}")
    return by_candidate


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2026 statewide executive primaries and runoffs, the District 17 Senate special primary and runoff, and State Questions 832, 844, and 846, by county",
            polity="US-OK",
        )

        def template(text: str) -> tuple:
            return conn.execute(
                """select office_id, body_id, district_area_id, seat_id, boundary_set_id, magnitude, term, electoral_system_id
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' where x.value = %s""",
                [text],
            ).fetchone()

        elections = {
            PRIMARY: conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = '2026-06-16:OK:primary'"
            ).fetchone()[0],
            RUNOFF: conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = '2026-08-25:OK:runoff'"
            ).fetchone()[0],
        }
        scopes = {}
        for party in ("republican", "democratic"):
            scopes[party] = conn.execute(
                """select c.party_scope_id, (select o.party_id from contest_option o where o.contest_id = c.id limit 1)
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value = %s""",
                [f"2026-06-16:OK:governor:{party}"],
            ).fetchone()
        runoff_system = conn.execute(
            """select electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2026-08-25:OK:governor:republican'"""
        ).fetchone()[0]
        referendum_system = conn.execute(
            """select electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2026-11-03:OK:question:state-question-845'"""
        ).fetchone()[0]
        pool: dict[str, set[UUID]] = defaultdict(set)
        for person, text in conn.execute(
            """select cy.person_id, n.text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ '^20(1[4-9]|2[0-9])-[0-9-]+:OK:'"""
        ).fetchall():
            pool[plain(text)].add(person)

        def person_of(text: str) -> UUID:
            found = pool.get(plain(text), set())
            if len(found) == 1:
                return next(iter(found))
            id = cs.key(f"{SOURCE}.person", f"OK:{slug(text)}")
            if id in cs.minted:
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=text, lang="en", kind="official", is_preferred=True)
            return id

        sources: dict[date, UUID] = {}
        for day in (PRIMARY, RUNOFF):
            label = day.strftime("%B %-d, %Y")
            sources[day] = cs.source(PUBLISHER, f"Oklahoma {label} election results")
            cs.add(
                "source",
                id=sources[day],
                kind="official_body",
                publisher=PUBLISHER,
                title=f"Oklahoma {label} election results",
                url=PAGE,
                license="Public record",
            )
            index = raw.cached(SOURCE, url(day.strftime("%Y%m%d"), "getelec"))
            cs.artifact(index, sources[day])

        def frame(result: UUID, rows: list[tuple]) -> None:
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

        made = 0
        for day_text, races in RACES.items():
            day = date(int(day_text[:4]), int(day_text[4:6]), int(day_text[6:]))
            meta, totals = load(day_text)
            runoff = day == RUNOFF
            for race in races:
                entry = meta[race]
                header = entry["raceTitle"]
                party = {"Republican": "republican", "Democrat": "democratic"}.get(entry["raceTitle2"])
                question = QUESTIONS.get(race)
                senate = re.fullmatch(r"FOR STATE SENATOR DISTRICT (\d+) \(UNEXPIRED TERM\)", header)
                office = None if question is not None else re.fullmatch(r"FOR (.+)", header)[1]
                by_candidate = counties(day_text, race, entry, totals[race])
                if question is not None:
                    kind, number = question
                    tail = f"question:state-question-{number}"
                    contest_key = f"{day}:OK:{tail}"
                    contest = cs.key("us.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=elections[day],
                        kind="ballot_question",
                        district_area_id=template("2026-11-03:OK:question:state-question-845")[2],
                        round_number=1,
                        electoral_system_id=referendum_system,
                        default_result_kind="counted",
                    )
                    cs.add("ballot_question", contest_id=contest, type=kind)
                    cs.add(
                        "name",
                        entity_id=contest,
                        text=f"State Question {number}",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    if contest in cs.minted:
                        prefix = "2026-06-16-primary" if day == PRIMARY else "2026-08-25-primary-runoff"
                        cs.add("slug", path=f"us/ok/{prefix}/state-question-{number}", entity_id=contest)
                    cs.add("citation", entity_id=contest, source_id=sources[day])
                    answers = {name: ("Yes" if name.endswith("YES") else "No") for name in by_candidate}
                else:
                    if senate is not None:
                        district = senate[1]
                        shape = template(f"2026-11-03:OK:senate:{district}:gs")
                        contest_key = f"{day}:OK:senate:{district}:rp"
                        label = f"2026 Oklahoma Senate Republican primary{' runoff' if runoff else ''}, special, District {district}"
                        path = f"state-senate-{district}-special-republican"
                    else:
                        tail, phrase = EXEC[office]
                        shape = template(f"2026-11-03:OK:{tail}")
                        contest_key = f"{day}:OK:{tail}:{party}"
                        label = f"2026 Oklahoma {phrase} {party.capitalize()} primary{' runoff' if runoff else ''}"
                        path = f"{tail}-{party}"
                    contest = cs.key("us.contest", contest_key)
                    first = f"{PRIMARY}{contest_key[len(str(day)) :]}"
                    scope = scopes["republican" if party is None or senate else party]
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=elections[day],
                        kind="office" if senate is None else "body_seats",
                        office_id=shape[0],
                        body_id=shape[1],
                        district_area_id=shape[2],
                        seat_id=shape[3],
                        boundary_set_id=shape[4],
                        magnitude=shape[5],
                        party_scope_id=scope[0],
                        term=shape[6],
                        round_number=2 if runoff else 1,
                        previous_round_id=cs.key("us.contest", first) if runoff else None,
                        electoral_system_id=runoff_system,
                        default_result_kind="counted",
                    )
                    cs.add("name", entity_id=contest, text=label, lang="en", kind="official", is_preferred=True)
                    if contest in cs.minted:
                        prefix = "2026-08-25-primary-runoff" if runoff else "2026-06-16-primary"
                        cs.add("slug", path=f"us/ok/{prefix}/{path}", entity_id=contest)
                    cs.add("citation", entity_id=contest, source_id=sources[day])
                result = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result,
                    election_id=elections[day],
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[day],
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
                ranked = sorted(by_candidate, key=lambda name: -sum(by_candidate[name].values()))
                everyone = sum(sum(votes.values()) for votes in by_candidate.values())
                needs_runoff = (
                    question is None
                    and not runoff
                    and len(ranked) > 1
                    and sum(by_candidate[ranked[0]].values()) * 2 <= everyone
                )
                options = {}
                for position, name in enumerate(by_candidate):
                    label_text = answers[name] if question is not None else title(name)
                    options[name] = cs.key("us.option", f"{contest_key}:{slug(label_text)}")
                    cs.add(
                        "contest_option",
                        id=options[name],
                        contest_id=contest,
                        kind="answer" if question is not None else "candidate",
                        party_id=None if question is not None else scope[1],
                        ballot_position=position + 1 if question is not None else None,
                        ballot_label=label_text,
                    )
                    if question is None:
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{slug(label_text)}"),
                            person_id=person_of(label_text),
                            option_id=options[name],
                            role="head",
                            ballot_name=label_text,
                            is_independent=False,
                        )
                for rank, name in enumerate(ranked):
                    if question is not None:
                        outcome = "won" if rank == 0 else "lost"
                    elif needs_runoff:
                        outcome = "advanced" if rank < 2 else "lost"
                    else:
                        outcome = "won" if rank == 0 else "lost"
                    if question is None and outcome == "lost":
                        continue
                    cs.add("outcome", result_set_id=result, option_id=options[name], kind=outcome)
                by_place: dict[str, int] = defaultdict(int)
                rows = []
                for name, by_unit in by_candidate.items():
                    for fips, votes in by_unit.items():
                        by_place[fips] += votes
                        rows.append(
                            (str(result), str(cs.unit("us.county", fips)), str(options[name]), 0, 1, 0, 0, votes)
                        )
                rows += [
                    (str(result), str(cs.unit("us.county", fips)), None, 0, 4, 0, 0, votes)
                    for fips, votes in by_place.items()
                ]
                frame(result, rows)
                made += 1
                print(day, race, header, everyone)
        print(made)
        return cs.write()
