import csv
import io
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.wv_judicial"
VERSION = "1"
URL = "https://apps.sos.wv.gov/elections/results/readfile.aspx?eid={eid}&type=StateCountyTotals&format=csv"
PUBLISHER = "West Virginia Secretary of State"
TITLE = "Election results center"
TITLE_OFFICE = "Justice of the Supreme Court"
ELECTIONS = {
    date(2008, 11, 4): (4, f"{date(2008, 11, 4)}:WV", "general", 2),
    date(2012, 11, 6): (13, f"{date(2012, 11, 6)}:WV", "general", 2),
    date(2016, 5, 10): (22, f"{date(2016, 5, 10)}:WV:primary", "primary", 1),
}
PARTIES = {"D": "democratic-party", "R": "republican-party"}
NOTE = (
    "County totals from the West Virginia Secretary of State's Election Results Center, state and county totals file."
)


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def fetch() -> None:
    for eid, *_ in ELECTIONS.values():
        raw.fetch(SOURCE, URL.format(eid=eid), suffix=".csv")


def lines() -> list[dict[str, object]]:
    found: dict[tuple[date, str, str], dict[str, object]] = {}
    for day, (eid, *_) in ELECTIONS.items():
        text = raw.cached(SOURCE, URL.format(eid=eid)).path.read_text(encoding="utf-8-sig")
        for line in csv.DictReader(io.StringIO(text)):
            if line["Type"] != "County" or line["OfficeDescription"] != "Justice of the Supreme Court of Appeals":
                continue
            key = (day, line["Name"], line["CountyName"])
            if key in found and line["OfficialResults"] != "Yes":
                continue
            found[key] = {
                "day": day,
                "candidate": re.sub(r"\s+", " ", line["Name"]).strip(),
                "party": line["PartyCode"],
                "county": line["CountyName"],
                "votes": int(line["Votes"] or 0),
            }
    return list(found.values())


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia Supreme Court of Appeals elections 2008, 2012, and 2016 by county, "
            "from the Secretary of State's Election Results Center",
            polity="US-WV",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://apps.sos.wv.gov/elections/results/",
            license="Public record",
        )
        for eid, *_ in ELECTIONS.values():
            cs.artifact(raw.cached(SOURCE, URL.format(eid=eid)), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-WV'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/wv/%%'").fetchall()}
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}

        def name(entity: UUID, text: str) -> None:
            found = conn.execute(
                """select n.id, n.text, c.pipeline from name n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.is_preferred""",
                [entity],
            ).fetchone()
            if found is not None and (found[1] == text or found[2] != SOURCE):
                return
            if found is not None:
                cs.add("name_drop", name_id=found[0], entity_id=entity, text=found[1], reason="Name restyled")
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        office_key = f"{slug(TITLE_OFFICE)}:WV"
        office = cs.key("us.office", office_key)
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-WV"), kind="other")
            cs.add(
                "name",
                entity_id=office,
                text=f"{TITLE_OFFICE} of West Virginia",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        groups: dict[date, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[row["day"]].append(row)
        persons: set[str] = set()
        for day, group in sorted(groups.items()):
            _, election_key, kind, seats = ELECTIONS[day]
            election = cs.key("us.election", election_key)
            contest_key = f"{day}:WV:{slug(TITLE_OFFICE)}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=state_area,
                term="full",
                magnitude=seats,
                round_number=1,
                electoral_system_id=systems["block" if seats > 1 else "fptp"],
                default_result_kind="counted",
            )
            name(contest, f"{day.year} West Virginia {TITLE_OFFICE}")
            if contest in cs.minted:
                base = f"us/wv/{day}-{kind}/{slug(TITLE_OFFICE)}"
                path, count = base, 1
                while path in taken:
                    count += 1
                    path = f"{base}-{count}"
                taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", contest_key)
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
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            parties: dict[str, str] = {}
            for row in group:
                by_choice[str(row["candidate"])][str(row["county"])] += int(row["votes"])
                parties[str(row["candidate"])] = str(row["party"])
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, choice in enumerate(by_choice):
                shown = display(choice)
                first, last = identity(choice)
                person_key = f"WV:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, shown)
                party = PARTIES.get(parties[choice])
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "contest_option",
                    id=options[choice],
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_position=position + 1,
                    ballot_label=shown,
                )
                candidacies[choice] = cs.key("us.candidacy", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "candidacy",
                    id=candidacies[choice],
                    person_id=person,
                    option_id=options[choice],
                    role="head",
                    ballot_name=shown,
                    is_independent=False,
                )
            totals = {choice: sum(by_unit.values()) for choice, by_unit in by_choice.items()}
            for choice in sorted(totals, key=totals.__getitem__, reverse=True)[:seats]:
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options[choice],
                    candidacy_id=candidacies[choice],
                    kind="elected",
                    seats=1,
                )
            by_place: dict[str, int] = defaultdict(int)
            values = []
            for choice, by_unit in by_choice.items():
                for place, votes in by_unit.items():
                    unit = counties[flat(place)]
                    by_place[str(unit)] += votes
                    values.append((str(result), str(unit), str(options[choice]), 0, 1, 0, 0, votes))
            values += [(str(result), unit, None, 0, 4, 0, 0, votes) for unit, votes in by_place.items()]
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
            print(contest_key, totals)
        return cs.write()
