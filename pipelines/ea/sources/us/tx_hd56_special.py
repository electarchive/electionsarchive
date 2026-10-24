import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.tx_hd56_special"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-039"
BASE = "https://results.texas-election.com/static/data/Reports/50676/"
FILES = {
    "CountybyCountyCanvassReport.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "OfficialCanvassReport.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "tx_50676_County.json": "application/json",
}
PUBLISHER = "Texas Secretary of State"
TITLE = "Official canvass, 2024 Special Election House District 56"
CONTEST_KEY = "2024-11-05:TX:house:56:special"
RETRIEVED = datetime(2026, 10, 23, 19, 0, tzinfo=UTC)
CURRY, SHANK = "d7dbb741-0386-5bfd-9b2c-2aebd48c3710", "c8704886-300e-5be0-9c85-5498718542e9"


def fetch() -> None:
    for name, media_type in FILES.items():
        try:
            raw.cached(SOURCE, BASE + name)
        except LookupError:
            raw.store(
                SOURCE,
                BASE + name,
                (INBOX / name).read_bytes(),
                suffix=Path(name).suffix,
                retrieved_at=RETRIEVED,
                media_type=media_type,
            )


def parse() -> None:
    pass


def emit() -> Path:
    county = json.loads((INBOX / "tx_50676_County.json").read_text())["48309"]
    candidates = county["Races"]["2202"]["C"]
    votes = {entry["N"]: entry["V"] for entry in candidates.values()}
    if votes != {"PAT CURRY": 56001, "ERIN SHANK": 25762} or county["Races"]["2202"]["T"] != sum(votes.values()):
        raise ValueError(f"unexpected canvass {votes}")
    with connect() as conn:
        regular = conn.execute(
            """select c.id, c.election_id, c.body_id, c.district_area_id, c.electoral_system_id
               from contest c join slug s on s.entity_id = c.id
               where s.path = 'us/tx/2024-11-05-general/state-house-56' and s.is_current"""
        ).fetchone()
        assert regular is not None
        parties = dict(
            conn.execute(
                "select o.id::text, o.party_id from contest_option o where o.contest_id = %s", [regular[0]]
            ).fetchall()
        )
        persons = dict(
            conn.execute(
                "select o.id::text, c.person_id from candidacy c join contest_option o on o.id = c.option_id"
                " where o.contest_id = %s",
                [regular[0]],
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas House District 56's November 5, 2024 special election (Anderson's unexpired term), "
            "from the Secretary of State's canvass",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.texas-election.com/races",
            license="Public record",
            notes="Report files for election 50676 (2024 Special Election House District 56), saved from the "
            "results site in a browser on 2026-10-23 because it answers a Cloudflare challenge to scripts.",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        contest = cs.key("us.contest", CONTEST_KEY)
        cs.add(
            "contest",
            id=contest,
            election_id=regular[1],
            kind="body_seats",
            body_id=regular[2],
            district_area_id=regular[3],
            magnitude=1,
            term="remainder",
            round_number=1,
            electoral_system_id=regular[4],
            default_result_kind="counted",
        )
        cs.add(
            "name",
            entity_id=contest,
            text="2024 Texas House of Representatives special election, District 56",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        cs.add("slug", path="us/tx/2024-11-05-general/state-house-56-unexpired-term", entity_id=contest)
        result_set = cs.key(f"{SOURCE}.result_set", CONTEST_KEY)
        cs.add(
            "result_set",
            id=result_set,
            election_id=regular[1],
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
        )
        unit = cs.unit("us.county", "48309")
        values = []
        for ballot, regular_option, total in (("Curry", CURRY, 56001), ("Shank", SHANK, 25762)):
            option = cs.key("us.option", f"{CONTEST_KEY}:{ballot.lower()}")
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=parties[regular_option],
                ballot_label=ballot,
            )
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{CONTEST_KEY}:{ballot.lower()}:head"),
                person_id=persons[regular_option],
                option_id=option,
                role="head",
                ballot_name=ballot,
                is_independent=False,
            )
            values.append((str(result_set), str(unit), str(option), total))
            if ballot == "Curry":
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
