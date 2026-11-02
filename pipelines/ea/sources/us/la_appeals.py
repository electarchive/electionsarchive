import csv
import io
import json
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import la_sos
from ea.sources.us.oe_primaries_2026 import plain
from ea.store import connect

SOURCE = "us.la_appeals"
VERSION = "1"
OFFICE = "Judge of a Court of Appeal"
FIRST, LAST = date(2000, 1, 1), date(2025, 12, 31)


def races(day: date) -> list[dict[str, Any]]:
    listed = json.loads(raw.cached(la_sos.SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm")).path.read_text())
    return [
        race
        for race in la_sos.many((listed.get("Races") or {}).get("Race"))
        if race["GeneralTitle"] == OFFICE and race.get("IsMultiParish") == "1"
    ]


def base(text: str) -> str:
    return plain(re.sub(r"\([A-Z]+\)\s*$", "", text))


def days() -> list[date]:
    return [day for day in la_sos.days() if FIRST <= day <= LAST and races(day)]


def link(day: date, race: dict[str, object]) -> str:
    return la_sos.blob(day, f"csv/ByParish_{race['ID']}.csv")


def fetch() -> None:
    client = httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": raw.USER_AGENT})

    def get(url: str, suffix: str) -> None:
        try:
            raw.cached(SOURCE, url)
            return
        except LookupError:
            pass
        for attempt in range(8):
            response = client.get(url)
            if response.status_code not in (403, 429):
                break
            time.sleep(2**attempt)
        response.raise_for_status()
        raw.store(
            SOURCE,
            url,
            response.content,
            suffix=suffix,
            retrieved_at=datetime.now(UTC),
            media_type=response.headers.get("content-type", "application/octet-stream"),
        )

    for day in days():
        get(la_sos.blob(day, "Votes_Multiparish.htm"), ".json")
        for race in races(day):
            get(link(day, race), ".csv")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana Court of Appeal elections by parish, 2000–2025, with their open primaries and runoffs",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(la_sos.PUBLISHER, la_sos.TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=la_sos.PUBLISHER,
                title=la_sos.TITLE,
                url="https://voterportal.sos.la.gov/static/",
                license="Public record",
            )
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-LA'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        parishes = {
            plain(text): unit
            for unit, text in conn.execute(
                """select u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '22%%'"""
            ).fetchall()
        }
        if len(parishes) != 64:
            raise ValueError("Louisiana needs 64 parishes")
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        two_round = cs.key("us.electoral_system", "two_round")
        parties = {label: cs.key("us.party", key) for label, key in la_sos.PARTIES.items()}
        if cs.minted & {two_round, *parties.values()}:
            raise ValueError("the two-round system and the national parties must exist")
        office = cs.key("us.office", "judge-of-a-court-of-appeal:LA")
        if "judge-of-a-court-of-appeal:LA" not in {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")
        }:
            cs.add("office", id=office, polity_id=cs.polity("US-LA"), kind="other")
            name(office, "Judge of a Court of Appeal of Louisiana")
        runoffs: dict[str, tuple[date, UUID]] = {}
        counts = {"contests": 0, "values": 0, "skipped": 0}
        for day in days():
            totals = {
                race["ID"]: {
                    choice["ID"]: (int(choice["VoteTotal"]), choice["Outcome"])
                    for choice in la_sos.many(race["Choice"])
                }
                for race in la_sos.many(
                    json.loads(raw.cached(SOURCE, la_sos.blob(day, "Votes_Multiparish.htm")).path.read_text())["Races"][
                        "Race"
                    ]
                )
            }
            cs.artifact(raw.cached(la_sos.SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm")), source)
            cs.artifact(raw.cached(SOURCE, la_sos.blob(day, "Votes_Multiparish.htm")), source)
            for race in races(day):
                title = re.sub(r"\s+", " ", str(race["SpecificTitle"]).split("--", 1)[1]).strip()
                scope = slug(title)
                pending = runoffs.get(scope)
                round_number = 2 if pending is not None and 0 < (day - pending[0]).days <= 70 else 1
                kind = "runoff" if round_number == 2 else "general"
                election_key = f"{day}:LA" + (":runoff" if round_number == 2 else "")
                election = cs.key("us.election", election_key)
                if election_key not in known:
                    known.add(election_key)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=cs.polity("US-LA"),
                        parent_id=cs.key("us.election", str(day)) if kind == "general" and str(day) in known else None,
                        kind=kind,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(
                        election,
                        f"{day.year} Louisiana {'runoff' if kind == 'runoff' else 'election'} ({day:%B} {day.day})",
                    )
                    cs.add("slug", path=f"us/la/{day}-{kind}", entity_id=election)
                remainder = bool(re.search(r"(?i)unexpired|special", title))
                contest_key = f"{day}:LA:court-of-appeal:{scope}"
                contest = cs.key("us.contest", contest_key)
                text = raw.cached(SOURCE, link(day, race)).path.read_text(encoding="utf-8-sig")
                rows = list(csv.reader(io.StringIO(text)))
                header, body = rows[0], rows[1:]
                choices = la_sos.many(race["Choice"])
                columns = {base(cell): index for index, cell in enumerate(header[2:])}
                if sorted(columns) != sorted(base(choice["Desc"]) for choice in choices):
                    raise ValueError(f"{day} {title}: columns {header[2:]} don't match the listed candidates")
                for choice in choices:
                    if (
                        sum(int(row[2 + columns[base(choice["Desc"])]]) for row in body)
                        != totals[race["ID"]][choice["ID"]][0]
                    ):
                        raise ValueError(f"{day} {title}: {choice['Desc']}'s parishes don't sum to the total")
                outcomes = [totals[race["ID"]][choice["ID"]][1] for choice in choices]
                if "Runoff" in outcomes:
                    runoffs[scope] = (day, contest)
                cs.artifact(raw.cached(SOURCE, link(day, race)), source)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    term="remainder" if remainder else "full",
                    magnitude=int(race["NumberToBeElected"] or 1),
                    round_number=round_number,
                    previous_round_id=pending[1] if round_number == 2 and pending is not None else None,
                    electoral_system_id=two_round,
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"{day.year} Louisiana Court of Appeal, {title} "
                    + ("runoff" if round_number == 2 else "special election" if remainder else "election"),
                )
                cs.add("slug", path=f"us/la/{day}-{kind}/court-of-appeal-{scope}", entity_id=contest)
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
                if round_number == 1:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="context",
                        body_md=la_sos.RUNOFF.replace("statewide officers", "judges of the Courts of Appeal"),
                    )
                counts["contests"] += 1
                for index, choice in enumerate(choices):
                    found = re.match(r"^(.*?)\s*\(([A-Z]+)\)$", choice["Desc"].strip())
                    candidate = (found[1] if found else choice["Desc"]).strip()
                    label = found[2] if found else None
                    person = cs.key(f"{la_sos.SOURCE}.person", f"LA:{slug(candidate)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, candidate)
                    option = cs.key("us.option", f"{contest_key}:{person}")
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
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=label in la_sos.INDEPENDENT,
                    )
                    for row in body:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=parishes[plain(row[1])],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=int(row[2 + columns[base(choice["Desc"])]]),
                        )
                        counts["values"] += 1
                    outcome = outcomes[index]
                    if outcome in ("Elected", "Runoff"):
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="elected" if outcome == "Elected" else "advanced",
                            seats=1 if outcome == "Elected" else None,
                        )
        print(counts)
        return cs.write()
