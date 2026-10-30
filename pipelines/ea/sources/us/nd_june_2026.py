import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.nd_june_2026"
VERSION = "1"
DAY = date(2026, 6, 9)
API = "https://api.resultsnd.sos.nd.gov/Contest/GetContestResults?cId=north-dakota&electionID=346&contestType=SW"
PAGE = "https://resultsnd.sos.nd.gov/#election=346"
PUBLISHER = "North Dakota Secretary of State"
TITLE = "North Dakota June 9, 2026 primary election official results"
CONTEST = "1746"
NAME = "Constitutional Measure 1: single subject for constitutional amendments"


def fetch() -> None:
    try:
        raw.cached(SOURCE, API)
    except LookupError:
        raw.store(
            SOURCE,
            API,
            httpx.get(API, timeout=300).content,
            suffix=".json",
            retrieved_at=datetime.now(UTC),
            media_type="application/json",
        )


def emit() -> Path:
    cached = raw.cached(SOURCE, API)
    entry = json.loads(cached.path.read_text())["response"]["contests"][CONTEST]
    answers = {"9001": "Yes", "9002": "No"}
    totals = {choice["choiceID"]: choice["totalVotes"] for choice in entry["choices"]}
    by_county = {
        fips: {choice["choiceID"]: choice["totalVotes"] for choice in location["choices"]}
        for fips, location in entry["locations"].items()
    }
    for choice, total in totals.items():
        if sum(votes[choice] for votes in by_county.values()) != total:
            raise ValueError(f"county sums differ from the statewide total for choice {choice}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Dakota's June 9, 2026 Constitutional Measure 1 (single subject) by county",
            polity="US-ND",
        )
        election = conn.execute(
            "select entity_id from external_id where scheme = 'us.election' and value = '2026-06-09:ND:primary'"
        ).fetchone()[0]
        area, system = conn.execute(
            """select c.district_area_id, c.electoral_system_id from contest c join external_id x on x.entity_id = c.id
               and x.scheme = 'us.contest' where x.value = '2026-11-03:ND:question:constitutional-measure-no-1'"""
        ).fetchone()
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=PAGE, license="Public record")
        cs.artifact(cached, source)
        key = f"{DAY}:ND:question:constitutional-measure-no-1"
        contest = cs.key("us.contest", key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=area,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
        cs.add("name", entity_id=contest, text=f"2026 North Dakota primary {NAME}", lang="en", kind="official", is_preferred=True)
        cs.add("slug", path=f"us/nd/{DAY}-primary/constitutional-measure-1", entity_id=contest)
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
        for position, (choice, label) in enumerate(answers.items()):
            options[choice] = cs.key("us.option", f"{key}:{label.lower()}")
            cs.add(
                "contest_option",
                id=options[choice],
                contest_id=contest,
                kind="answer",
                ballot_position=position + 1,
                ballot_label=label,
            )
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options[choice],
                kind="won" if totals[choice] > totals["9002" if choice == "9001" else "9001"] else "lost",
            )
        rows = []
        for fips, votes in by_county.items():
            unit = str(cs.unit("us.county", fips))
            rows += [(str(result), unit, str(options[choice]), 0, 1, 0, 0, votes[choice]) for choice in answers]
            rows.append((str(result), unit, None, 0, 4, 0, 0, sum(votes.values())))
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
        print(totals, len(by_county))
        return cs.write()
