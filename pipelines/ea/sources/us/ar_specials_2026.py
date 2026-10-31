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
from ea.sources.us import oe_primaries_2026 as oe
from ea.store import connect

SOURCE = "us.ar_specials_2026"
VERSION = "1"
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Arkansas 2026 election night reporting results"
PAGE = "https://arkansas.tally-enr.com/"
HEADERS = {**oe.BROWSER, "Origin": "https://arkansas.tally-enr.com"}
LISTS = "https://enr-results-api.totalresults.com/Contest/GetContestSearchList?cid=arkansas&electionID={}"
FILES = "https://results.tally-enr.com/results/arkansas/{}/FullDataFile.json"
PRELIMINARY = (
    "The results site marks these totals unofficial. The certified canvass has been requested, and the totals will be "
    "replaced if it differs."
)
VIN_VIO = (
    "The results site files two contests for this election under the labels VIN and VIO. VIO is the special primary "
    "for the remainder of Stan Berry's term: the June 30 runoff and the August 4 special general carry the same label, "
    "and Teeter's 31 percent and Phillips's 26 percent match Fox16's report of the special primary. VIN is the "
    "primary for the Republican nomination to the full term that begins in 2027."
)
PLURALITY_NOTE = (
    "Bill Teeter was reported as the Republican nominee on June 2 with a plurality, and no runoff was held for this "
    "contest."
)

ELECTIONS: dict[str, tuple[str, str, date, str | None, str, str]] = {
    "0106": (
        "2026-01-06:AR:special-primary",
        "primary",
        date(2026, 1, 6),
        None,
        "2026-01-06-special-primary",
        "2026 Arkansas special primary election, January 6",
    ),
    "0203": (
        "2026-02-03:AR:special-runoff",
        "runoff",
        date(2026, 2, 3),
        "0106",
        "2026-02-03-special-runoff",
        "2026 Arkansas special primary runoff election, February 3",
    ),
    "0303": ("2026-03-03:AR:primary", "", date(2026, 3, 3), None, "", ""),
    "0331": ("2026-03-31:AR:runoff", "", date(2026, 3, 31), None, "", ""),
    "0602": (
        "2026-06-02:AR:special-primary",
        "primary",
        date(2026, 6, 2),
        None,
        "2026-06-02-special-primary",
        "2026 Arkansas special primary election, June 2",
    ),
    "0630": (
        "2026-06-30:AR:special-runoff",
        "runoff",
        date(2026, 6, 30),
        "0602",
        "2026-06-30-special-runoff",
        "2026 Arkansas special primary runoff election, June 30",
    ),
    "0804": (
        "2026-08-04:AR:special-general",
        "general",
        date(2026, 8, 4),
        None,
        "2026-08-04-special-general",
        "2026 Arkansas special general election, August 4",
    ),
    "0818": (
        "2026-08-18:AR:special-primary",
        "primary",
        date(2026, 8, 18),
        None,
        "2026-08-18-special-primary",
        "2026 Arkansas special primary election, August 18",
    ),
}
IDS = {
    "0106": "232c8b44-41a9-48d0-99bd-68eeb1bd6692",
    "0203": "d70c4503-44b5-4942-ba21-814aacb4caee",
    "0303": "7f77a178-af02-40ec-92db-c5cc50882c68",
    "0331": "b412bdef-f97a-45bc-b3ec-6761d28caf9e",
    "0602": "4dfe2063-3126-4eb4-ae59-1468c9d6c9cd",
    "0630": "fefa84d5-388a-4de9-9ade-acd50e0a8b1d",
    "0804": "e7ecbe00-7138-4d7d-b740-8b51e25b32e8",
    "0818": "4b025e66-db9f-4e01-a7b8-3d06d87bccda",
}
SENATE = r"^20\d\d-\d\d-\d\d:AR:senate:{}:"
HOUSE = r"^20\d\d-\d\d-\d\d:AR:house:{}:"
CONTESTS: list[tuple[str, str, str, str, str | None, str, str, bool, str | None]] = [
    (
        "0106",
        "REP State Senate District 26",
        "2026-01-06:AR:senate:26:s:rp",
        SENATE.format(26),
        None,
        "2026 Arkansas Senate Republican special primary, District 26",
        "state-senate-26-republican",
        False,
        None,
    ),
    (
        "0203",
        "REP State Senate District 26",
        "2026-02-03:AR:senate:26:s:rp",
        SENATE.format(26),
        "2026-01-06:AR:senate:26:s:rp",
        "2026 Arkansas Senate Republican special primary runoff, District 26",
        "state-senate-26-republican",
        False,
        None,
    ),
    (
        "0303",
        "REP Secretary of State",
        "2026-03-03:AR:secretary-of-state:republican",
        "^2026-11-03:AR:secretary-of-state$",
        None,
        "2026 Arkansas Secretary of State Republican primary",
        "secretary-of-state-republican",
        False,
        None,
    ),
    (
        "0303",
        "REP Comm. of State Lands",
        "2026-03-03:AR:commissioner-of-state-lands:republican",
        "^2026-11-03:AR:commissioner-of-state-lands$",
        None,
        "2026 Arkansas Commissioner of State Lands Republican primary",
        "commissioner-of-state-lands-republican",
        False,
        None,
    ),
    (
        "0331",
        "REP Secretary of State",
        "2026-03-31:AR:secretary-of-state:republican",
        "^2026-11-03:AR:secretary-of-state$",
        "2026-03-03:AR:secretary-of-state:republican",
        "2026 Arkansas Secretary of State Republican primary runoff",
        "secretary-of-state-republican",
        False,
        None,
    ),
    (
        "0602",
        "REP State Representative Dist. 44 VIN",
        "2026-06-02:AR:house:44:rp",
        HOUSE.format(44),
        None,
        "2026 Arkansas House Republican primary for the full term, District 44",
        "state-house-44-republican",
        True,
        VIN_VIO + " " + PLURALITY_NOTE,
    ),
    (
        "0602",
        "REP State Representative Dist. 44 VIO",
        "2026-06-02:AR:house:44:s:rp",
        HOUSE.format(44),
        None,
        "2026 Arkansas House Republican special primary, District 44",
        "state-house-44-republican-special",
        False,
        VIN_VIO,
    ),
    (
        "0630",
        "REP VIO State Representative Dist. 44",
        "2026-06-30:AR:house:44:s:rp",
        HOUSE.format(44),
        "2026-06-02:AR:house:44:s:rp",
        "2026 Arkansas House Republican special primary runoff, District 44",
        "state-house-44-republican-special",
        False,
        None,
    ),
    (
        "0804",
        "VIO State Representative District 44",
        "2026-08-04:AR:house:44:s",
        HOUSE.format(44),
        None,
        "2026 Arkansas House special general election, District 44",
        "state-house-44-special",
        True,
        None,
    ),
    (
        "0818",
        "REP State Senate District 1",
        "2026-08-18:AR:senate:1:s:rp",
        SENATE.format(1),
        None,
        "2026 Arkansas Senate Republican special primary, District 1",
        "state-senate-1-republican",
        False,
        None,
    ),
]
TITLES = re.compile(
    r"^(REP|DEM|School Board Director|State Representative|State Senator|Representative|Senator|Sen\.|Rep\.)\s+"
)


def fetch() -> None:
    for election in IDS.values():
        for url in (LISTS.format(election), FILES.format(election)):
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.store(
                    SOURCE,
                    url,
                    httpx.get(url, headers=HEADERS, timeout=600).content,
                    suffix=".json",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/json",
                )


def emit() -> Path:
    data = {
        code: (
            json.loads(raw.cached(SOURCE, LISTS.format(id)).path.read_text())["response"]["contests"],
            json.loads(raw.cached(SOURCE, FILES.format(id)).path.read_text()),
        )
        for code, id in IDS.items()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's 2026 special primaries, runoffs, and general (State Senate 26 and 1, House 44) and the Republican Secretary of State and Commissioner of State Lands primaries and runoff, by county",
            polity="US-AR",
        )
        state = cs.polity("US-AR")

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        for id in IDS.values():
            for url in (LISTS.format(id), FILES.format(id)):
                cs.artifact(raw.cached(SOURCE, url), source)

        elections: dict[str, UUID] = {}
        for code, (key, kind, day, parent, path, label) in ELECTIONS.items():
            known = conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = %s", [key]
            ).fetchone()
            if known is not None:
                elections[code] = known[0]
                continue
            election = elections[code] = cs.key("us.election", key)
            cs.add(
                "election",
                id=election,
                polity_id=state,
                parent_id=elections.get(parent) if parent is not None else None,
                kind=kind,
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, label)
            if election in cs.minted:
                cs.add("slug", path=f"us/ar/{path}", entity_id=election)
        election_slugs = dict(
            conn.execute(
                "select e.id, s.path from election e join slug s on s.entity_id = e.id where e.polity_id = %s",
                [state],
            ).fetchall()
        )

        def shape(pattern: str) -> tuple:
            return conn.execute(
                """select c.kind, c.office_id, c.body_id, c.district_area_id, c.seat_id, c.boundary_set_id, c.magnitude, c.term
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value ~ %s order by x.value desc limit 1""",
                [pattern],
            ).fetchone()

        scope, runoff_system = conn.execute(
            """select c.party_scope_id, c.electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2026-03-03:AR:house:1:rp'"""
        ).fetchone()
        plurality = conn.execute(
            """select c.electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2026-03-03:AR:senate:26:s'"""
        ).fetchone()[0]
        pool: dict[str, set[UUID]] = defaultdict(set)
        for person, text in conn.execute(
            """select cy.person_id, n.text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ '^20(1[4-9]|2[0-9])-[0-9-]+:AR:'"""
        ).fetchall():
            pool[oe.plain(text)].add(person)
        made: set[UUID] = set()

        def person_of(text: str) -> UUID:
            found = pool.get(oe.plain(text), set())
            if len(found) == 1:
                return next(iter(found))
            id = cs.key(f"{SOURCE}.person", f"AR:{slug(text)}")
            if id not in made:
                made.add(id)
                if id in cs.minted:
                    cs.add("person", id=id)
                    name(id, text)
            return id

        contests: dict[str, UUID] = {}
        for code, enr, key, pattern, previous, label, path, single_round, note in CONTESTS:
            contests[key] = contest = cs.key("us.contest", key)
            meta, results = data[code]
            enr_id = next(id for id, entry in meta.items() if entry["contestName"] == enr)
            body = next(entry for entry in results["ContestData"] if entry["ContestID"] == enr_id)
            official = results["ElectionData"]["IsOfficial"]
            kind, office, body_id, area, seat, boundaries, magnitude, term = shape(pattern)
            general = ELECTIONS[code][1] == "general"
            cs.add(
                "contest",
                id=contest,
                election_id=elections[code],
                kind=kind,
                office_id=office,
                body_id=body_id,
                district_area_id=area,
                seat_id=seat,
                boundary_set_id=boundaries,
                magnitude=magnitude,
                party_scope_id=None if general else scope,
                term=term,
                round_number=2 if previous is not None else 1,
                previous_round_id=contests[previous] if previous is not None else None,
                electoral_system_id=plurality if single_round or general else runoff_system,
                default_result_kind="counted",
            )
            name(contest, label)
            if contest in cs.minted:
                cs.add(
                    "slug",
                    path=f"{election_slugs.get(elections[code], 'us/ar/' + ELECTIONS[code][4])}/{path}",
                    entity_id=contest,
                )
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
                election_id=elections[code],
                contest_id=contest,
                stage="official" if official else "preliminary",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if not official:
                cs.add("note", entity_id=result, kind="caveat", body_md=PRELIMINARY)
            if note is not None:
                cs.add("note", entity_id=result, kind="methodology", body_md=note)
            options: dict[str, UUID] = {}
            totals: dict[str, int] = {}
            for choice in sorted(meta[enr_id]["choices"].values(), key=lambda c: c["order"]):
                text = TITLES.sub("", choice["name"]).strip()
                write_in = choice["isWriteIn"]
                option = options[choice["id"]] = cs.key("us.option", f"{key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="write_in" if write_in else "candidate",
                    party_id=None if write_in else scope,
                    ballot_label=text,
                )
                if not write_in:
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{slug(text)}"),
                        person_id=person_of(text),
                        option_id=option,
                        role="head",
                        ballot_name=text,
                        is_independent=False,
                    )
                totals[choice["id"]] = 0
            rows = []
            for fips, location in body["Locations"].items():
                unit = cs.unit("us.county", fips)
                total = 0
                for choice in location["Choices"]:
                    totals[choice["ChoiceID"]] += choice.get("TotalVotes", 0)
                    total += choice.get("TotalVotes", 0)
                    rows.append(
                        (
                            str(result),
                            str(unit),
                            str(options[choice["ChoiceID"]]),
                            0,
                            1,
                            0,
                            0,
                            choice.get("TotalVotes", 0),
                        )
                    )
                rows.append((str(result), str(unit), None, 0, 4, 0, 0, total))
            stated = {choice["ChoiceID"]: choice.get("TotalVotes", 0) for choice in body["Choices"]}
            if totals != stated:
                raise ValueError(f"{key}: county sums {totals} differ from the contest totals {stated}")
            ranked = sorted(totals, key=lambda id: -totals[id])
            leader = totals[ranked[0]] / max(sum(totals.values()), 1)
            for rank, id in enumerate(ranked):
                if rank == 0 and (single_round or general or previous is not None or leader > 0.5 or len(ranked) == 1):
                    cs.add("outcome", result_set_id=result, option_id=options[id], kind="won")
                elif rank < 2 and not (single_round or general or previous is not None or leader > 0.5):
                    cs.add("outcome", result_set_id=result, option_id=options[id], kind="advanced")
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
            print(key, official, {meta[enr_id]["choices"][id]["name"]: votes for id, votes in totals.items()})
        return cs.write()
