import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import la_sos
from ea.store import DATA, connect

SOURCE = "us.la_congress"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIRSTS = {
    date(1986, 9, 27): date(1986, 11, 4),
    date(1988, 10, 1): date(1988, 11, 8),
    date(1990, 10, 6): date(1990, 11, 6),
    date(1992, 10, 3): date(1992, 11, 3),
}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}


def races(day: date) -> list[dict[str, object]]:
    listed = json.loads(raw.cached(la_sos.SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm")).path.read_text())
    return [
        race
        for race in la_sos.many((listed.get("Races") or {}).get("Race"))
        if race["GeneralTitle"] in ("U.S. Representative", "U.S. Senator")
    ]


def chamber(race: dict[str, object]) -> str:
    found = re.search(r"(\d+)(?:st|nd|rd|th) Congressional", str(race["SpecificTitle"]))
    return "senate" if found is None else f"house:{int(found[1])}"


def fetch() -> None:
    for day in FIRSTS:
        for name in ["Votes_Multiparish.htm"] + [f"VotesParish/Votes_{parish}.htm" for parish in la_sos.PARISHES]:
            for attempt in range(8):
                try:
                    raw.fetch(SOURCE, la_sos.blob(day, name), suffix=".json")
                    break
                except httpx.HTTPStatusError as error:
                    if error.response.status_code not in (403, 429) or attempt == 7:
                        raise
                    time.sleep(2**attempt)


def parse() -> None:
    with connect() as conn:
        runoffs = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value ~ '^19\\d\\d-..-..:LA:(house:\\d+|senate)$'
                   and c.round_number = 2 and c.previous_round_id is null"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    for day, second in FIRSTS.items():
        wanted = {race["ID"]: race for race in races(day) if f"{second}:LA:{chamber(race)}" in runoffs}
        totals = {
            race["ID"]: {
                choice["ID"]: (int(choice["VoteTotal"]), choice["Outcome"]) for choice in la_sos.many(race["Choice"])
            }
            for race in la_sos.many(
                json.loads(raw.cached(SOURCE, la_sos.blob(day, "Votes_Multiparish.htm")).path.read_text())["Races"][
                    "Race"
                ]
            )
            if race["ID"] in wanted
        }
        counted: dict[tuple[str, str], int] = {}
        described = {
            (race["ID"], choice["ID"]): str(choice["Desc"]).strip()
            for race in wanted.values()
            for choice in la_sos.many(race["Choice"])
        }
        for parish in la_sos.PARISHES:
            listed = json.loads(
                raw.cached(SOURCE, la_sos.blob(day, f"VotesParish/Votes_{parish}.htm")).path.read_text()
            )
            for race in la_sos.many((listed.get("Races") or {}).get("Race")):
                if race["ID"] not in wanted:
                    continue
                for choice in la_sos.many(race["Choice"]):
                    found = re.match(r"^(.*?)\s*\(([A-Z]+)\)$", described[(race["ID"], choice["ID"])])
                    votes = int(choice["VoteTotal"])
                    counted[(race["ID"], choice["ID"])] = counted.get((race["ID"], choice["ID"]), 0) + votes
                    rows.append(
                        {
                            "day": day,
                            "second": second,
                            "chamber": chamber(wanted[race["ID"]]),
                            "choice": choice["ID"],
                            "candidate": (found[1] if found else described[(race["ID"], choice["ID"])]).strip(),
                            "party": found[2] if found else None,
                            "outcome": totals[race["ID"]][choice["ID"]][1],
                            "parish": parish,
                            "votes": votes,
                        }
                    )
        for (race, choice), votes in counted.items():
            if votes != totals[race][choice][0]:
                raise ValueError(f"{day} {wanted[race]['SpecificTitle']}: {choice}'s parishes don't sum to the total")
        missing = {f"{second}:LA:{chamber(race)}" for race in wanted.values()} ^ {
            key for key in runoffs if key.startswith(str(second))
        }
        if missing:
            raise ValueError(f"{day}: no first round for {sorted(missing)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's 1986–1992 congressional open primaries by parish, chained to the FEC's runoffs",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(la_sos.PUBLISHER, la_sos.TITLE)
        for day in FIRSTS:
            cs.artifact(raw.cached(la_sos.SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm")), source)
            for name_ in ["Votes_Multiparish.htm"] + [f"VotesParish/Votes_{parish}.htm" for parish in la_sos.PARISHES]:
                cs.artifact(raw.cached(SOURCE, la_sos.blob(day, name_)), source)
        parishes: dict[str, tuple[str, UUID, UUID]] = {
            f"{index + 1:02}": (code, unit, area)
            for index, (code, unit, area) in enumerate(
                conn.execute(
                    """select u.code, u.id, u.area_id from reporting_unit u
                       join name n on n.entity_id = u.area_id and n.kind = 'short'
                       where u.scheme_code = 'us.county' and u.code like '22%' order by n.text"""
                ).fetchall()
            )
        }
        louisiana = cs.polity("US-LA")
        cs.add("scheme", code="us.county_part", polity_id=cs.polity("US"), kind="synthetic", rank=3, noun="county part")
        if len(parishes) != 64:
            raise ValueError("Louisiana needs 64 parishes")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & set(parties.values()):
            raise ValueError("the national parties must exist")
        columns = [
            "id",
            "election_id",
            "kind",
            "body_id",
            "office_id",
            "district_area_id",
            "seat_id",
            "term",
            "magnitude",
            "boundary_set_id",
            "round_number",
            "party_scope_id",
            "previous_round_id",
            "electoral_system_id",
            "allocation_parent_id",
            "default_result_kind",
            "rules_override",
        ]
        for (day, second, house), lines in frame.group_by("day", "second", "chamber", maintain_order=True):
            runoff_key = f"{second}:LA:{house}"
            key = f"{day}:LA:{house}"
            ((row),) = conn.execute(
                f"select {', '.join(f'c.{column}' for column in columns)} from contest c "
                "join external_id x on x.entity_id = c.id where x.scheme = 'us.contest' and x.value = %s",
                [runoff_key],
            ).fetchall()
            runoff = dict(zip(columns, row, strict=True))
            election = cs.key("us.election", f"{day}:LA")
            if election in cs.minted:
                raise ValueError(f"{day}: the primary's election must exist")
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                **{
                    **runoff,
                    "id": contest,
                    "election_id": election,
                    "round_number": 1,
                    "previous_round_id": None,
                },
            )
            cs.add("contest", **{**runoff, "previous_round_id": contest})
            place = "Louisiana" if house == "senate" else f"Louisiana's {house.split(':')[1]}"
            district = int(house.split(":")[1]) if house != "senate" else 0
            ordinal = {1: "st", 2: "nd", 3: "rd"}.get(district if district < 20 else district % 10, "th")
            name(
                contest,
                f"{day.year} U.S. Senate election in Louisiana"
                if house == "senate"
                else f"{day.year} U.S. House election in {place}{ordinal} congressional district",
            )
            cs.add("slug", path=f"us/la/{day}-general/{house.replace(':', '-')}", entity_id=contest)
            known = conn.execute(
                """select cy.person_id, n.text from contest_option o
                   join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where o.contest_id = %s""",
                [runoff["id"]],
            ).fetchall()
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
                kind="context",
                body_md=la_sos.RUNOFF.replace("statewide officers", "members of Congress"),
            )
            units: dict[str, UUID] = {}
            for parish in lines["parish"].unique().sort():
                code, unit, area = parishes[parish]
                if house == "senate":
                    units[parish] = unit
                    continue
                part = f"{day.year}:{code}:{house.split(':')[1]}"
                units[parish] = cs.unit("us.county_part", part)
                cs.add(
                    "reporting_unit",
                    id=units[parish],
                    polity_id=louisiana,
                    scheme_code="us.county_part",
                    code=part,
                    parent_id=unit,
                    area_relation="within",
                    area_id=area,
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                cs.add(
                    "reporting_unit_area",
                    unit_id=units[parish],
                    area_id=runoff["district_area_id"],
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
            for (choice, candidate, label, outcome), cells in lines.group_by(
                "choice", "candidate", "party", "outcome", maintain_order=True
            ):
                surname = [word for word in slug(candidate).split("-") if word not in ("jr", "sr", "ii", "iii")][-1]
                matches = [person for person, text in known if surname in slug(text).split("-")]
                if len(matches) == 1:
                    person = matches[0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"LA:{slug(candidate)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, candidate)
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties.get(label or ""),
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                )
                for parish, votes in cells.select("parish", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[parish],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                if outcome == "Runoff":
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
        return cs.write()
