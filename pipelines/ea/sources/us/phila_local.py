import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.phila_returns import LISTING, METHOD, PARSED, PUBLISHER, TITLE
from ea.sources.us.phila_returns import SOURCE as RETURNS
from ea.store import connect

SOURCE = "us.phila_local"
VERSION = "1"
FIPS = "42101"
PLACE = "4260000"
ORDINAL = re.compile(r"(\d+)(?:ST|ND|RD|TH)")
RETENTION = re.compile(r"^(.*?) (YES SI|NO NO)$")
OFFICES = [
    (r"^MAYOR", "mayor", "Mayor of Philadelphia", PLACE, 1),
    (r"^COUNCIL AT[- ]?LARGE", "council-at-large", "Philadelphia City Council, at-large seats", PLACE, 7),
    (r"^DISTRICT COUNCIL", "council", "Philadelphia City Council", PLACE, 1),
    (r"^CITY COMMISSIONERS?", "city-commissioners", "Philadelphia City Commissioners", FIPS, 3),
    (r"^(?:CITY )?CONTROLLER", "controller", "Philadelphia City Controller", PLACE, 1),
    (r"^DISTRICT ATTORNEY", "district-attorney", "Philadelphia District Attorney", FIPS, 1),
    (r"^SHERIFF", "sheriff", "Philadelphia Sheriff", FIPS, 1),
    (r"^REGISTER OF WILLS", "register-of-wills", "Philadelphia Register of Wills", FIPS, 1),
    (r"^CLERK OF (?:QUARTER SESSIONS|COURTS)", "clerk-of-courts", "Philadelphia Clerk of Quarter Sessions", FIPS, 1),
    (r"^JUDGE OF THE COURT OF COMMON PL", "common-pleas", "Judge of the Court of Common Pleas", FIPS, None),
    (r"^JUDGE OF THE MUNICIPAL COURT", "municipal-court", "Judge of the Philadelphia Municipal Court", FIPS, None),
    (r"^JUDGE OF THE TRAFFIC COURT", "traffic-court", "Judge of the Philadelphia Traffic Court", FIPS, None),
]
COURTS = {
    "COURT OF COMMON PLEAS": "common-pleas",
    "MUNICIPAL COURT": "municipal-court",
    "TRAFFIC COURT": "traffic-court",
}
COVERED = {
    "mayor": "mayor",
    "council-at-large": "city council",
    "sheriff": "sheriff",
    "district-attorney": "prosecutor",
}
BALLOTS = {"D": "democratic", "DEM": "democratic", "R": "republican", "REP": "republican"}
PARTIES = {
    "DEMOCRATIC": "democratic-party",
    "DEM": "democratic-party",
    "D": "democratic-party",
    "REPUBLICAN": "republican-party",
    "REP": "republican-party",
    "R": "republican-party",
}
NOTE = (
    METHOD
    + " Write-in votes are not included. The file prints no winners, so no outcomes are recorded, and the seat count "
    "is stored only where Philadelphia's charter fixes it."
)


def office_of(contest: str, candidate: str) -> tuple[str, str | None, str | None, str | None] | None:
    judge = None
    if contest.startswith("RETENTION - "):
        court, _, judge = contest.removeprefix("RETENTION - ").partition(" - ")
        court = court.removeprefix("JUDGE OF THE ")
    elif contest.endswith(" RETENTION"):
        court = contest.removesuffix(" RETENTION")
        judge, _, candidate = candidate.rpartition("-")
    else:
        court = None
    if court is not None:
        key = COURTS.get(court)
        return None if key is None else (key, None, judge, "Yes" if candidate.upper().startswith("YES") else "No")
    for pattern, key, _, _, _ in OFFICES:
        if re.match(pattern, contest):
            if key == "council":
                found = ORDINAL.search(contest)
                return None if found is None else (key, f"district-{int(found[1])}", None, None)
            vote = RETENTION.match(candidate)
            if vote is not None and key in COURTS.values():
                return key, None, vote[1], "Yes" if vote[2].startswith("YES") else "No"
            return key, None, None, None
    return None


def emit() -> Path:
    tallies: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    labels: dict[tuple, dict[str, str | None]] = defaultdict(dict)
    urls: dict[date, str] = {}
    frame = pl.read_parquet(PARSED / "rows.parquet")
    for day, url, contest, precinct, candidate, votes, party, ballot in frame.select(
        "day", "url", "contest", "precinct", "candidate", "votes", "party", "ballot"
    ).iter_rows():
        name = contest.partition("|")[0]
        found = office_of(name, candidate)
        if found is None:
            continue
        office, seat, judge, answer = found
        retention = answer is not None
        label = answer or candidate
        sheet = (day, office, seat, BALLOTS.get(ballot or ""), judge and slug(judge))
        tallies[sheet][label][precinct] += votes
        if not retention:
            labels[sheet][label] = PARTIES.get((party or ballot or "").upper())
        elif judge:
            labels[sheet]["judge"] = judge
        urls[day] = url
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Philadelphia's city and county elections by ward and division, 2007-2020",
            polity="US-PA",
        )
        cs.scope_units("us.precinct", f"%:PA:{FIPS}:%")
        state = cs.polity("US-PA")
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LISTING,
                license="Public record",
            )
        for url in sorted(set(urls.values())):
            cs.artifact(raw.cached(RETURNS, url), source)
        polities = {
            code: (polity, text, path)
            for code, polity, text, path in conn.execute(
                """select c.code, t.polity_id, n.text, s.path from area_code c
                   join polity_territory t on t.area_id = c.area_id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where c.code in (%s, %s)""",
                [PLACE, FIPS],
            ).fetchall()
        }
        county_unit, county_area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()
        place_area = cs.area("us.place", PLACE)
        systems = {name: cs.key("us.electoral_system", name) for name in ("fptp", "block", "referendum")}
        parties = {name: cs.key("us.party", name) for name in ("democratic-party", "republican-party")}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the electoral systems and parties must already exist")
        existing = defaultdict(set)
        for day, value in conn.execute(
            """select e.date_from, lower(x.value) from contest k join election e on e.id = k.election_id
               join external_id x on x.entity_id = k.id and x.scheme = 'us.contest'
               where k.district_area_id in (%s, %s)""",
            [place_area, county_area],
        ).fetchall():
            existing[day].add(value)
        held = {
            path for (path,) in conn.execute("select path from slug where path like 'us/pa/philadelphia/%%'").fetchall()
        }
        made = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^[0-9-]+:PA:(42101|4260000):'"
            ).fetchall()
        }
        elections: dict[date, UUID] = {}
        owners: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}
        districts: set[str] = set()
        minted: dict[str, date] = {}
        report: dict[str, int] = defaultdict(int)

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        for sheet, options in sorted(tallies.items(), key=lambda item: (item[0][0], item[0][1], str(item[0][2:]))):
            day, office, seat, ballot, judge = sheet
            title, code, seats = next((t, c, s) for _, k, t, c, s in OFFICES if k == office)
            if any(hint in text for text in existing[day] for hint in [COVERED.get(office, "\0")]):
                report["held by another pipeline"] += 1
                continue
            polity = polities[code][0]
            if day not in elections:
                found = conn.execute(
                    """select e.id from election e where e.date_from = %s and e.polity_id = any(%s) limit 1""",
                    [day, [polities[PLACE][0], polities[FIPS][0]]],
                ).fetchone()
                umbrella = conn.execute(
                    """select e.id from election e join slug s on s.entity_id = e.polity_id and s.path = 'us/pa'
                       where e.date_from = %s limit 1""",
                    [day],
                ).fetchone()
                if found is not None:
                    elections[day] = found[0]
                else:
                    kind = "primary" if ballot else "general" if day.year % 2 == 0 else "local"
                    elections[day] = cs.key("us.election", f"{day}:PA:{PLACE}")
                    cs.add(
                        "election",
                        id=elections[day],
                        polity_id=polities[PLACE][0],
                        parent_id=None if umbrella is None else umbrella[0],
                        kind=kind,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(elections[day], f"{day.year} Philadelphia {kind} election")
                    cs.add("slug", path=f"{polities[PLACE][2]}/{day}-{kind}", entity_id=elections[day])
            election = elections[day]
            folder = conn.execute("select path from slug where entity_id = %s", [election]).fetchone()
            folder = folder[0] if folder is not None else f"{polities[PLACE][2]}/{day}-local"
            retention = judge is not None
            path = f"{folder}/{office}" + "".join(
                f"-{part}" for part in (seat, f"retention-{judge}" if retention else None, ballot) if part
            )
            contest_key = f"{day}:PA:{code}:{office}" + "".join(
                f":{part}" for part in (seat, f"retention-{judge}" if retention else None, ballot) if part
            )
            if path in held or contest_key in made:
                report["held by another pipeline"] += 1
                continue
            owner_key = f"PA:{code}:{office}" + (f":retention:{judge}" if retention else "")
            if owner_key not in owners:
                owners[owner_key] = cs.key("us.office", owner_key)
                cs.add(
                    "office",
                    id=owners[owner_key],
                    polity_id=polity,
                    kind="mayor" if office == "mayor" else "other",
                )
                judge_name = labels[sheet].get("judge") if retention else None
                name(owners[owner_key], f"{title}, retention of {judge_name.title()}" if retention else title)
            area = place_area if code == PLACE else county_area
            if seat is not None:
                district = f"PA:{PLACE}:council:{seat}"
                area = cs.area("us.local_district", district)
                if district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=place_area, valid_from=None)
                    name(area, f"Philadelphia City Council, {seat.replace('-', ' ')}")
                    cs.add(
                        "reporting_unit",
                        id=cs.unit("us.local_district", district),
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=district,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                    )
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owners[owner_key],
                district_area_id=area,
                magnitude=1 if retention else seats,
                round_number=1,
                electoral_system_id=systems["referendum" if retention else "fptp" if seats == 1 else "block"],
                default_result_kind="counted",
                term="full",
            )
            label = (
                f"{day.year} {title}"
                + (f", {seat.replace('-', ' ')}" if seat else "")
                + (f" ({ballot.title()} primary)" if ballot else "")
            )
            name(contest, f"{day.year} {title}, retention of {labels[sheet]['judge'].title()}" if retention else label)
            cs.add("slug", path=path, entity_id=contest)
            if seat is not None:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md="The district is a label without a boundary here; division results are not assigned to it.",
                )
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
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            cells: dict[tuple[UUID, UUID], int] = {}
            for candidate, by_precinct in options.items():
                if candidate == "judge":
                    continue
                if retention:
                    option = cs.key("us.option", f"{contest_key}:{candidate.lower()}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="answer", ballot_label=candidate)
                    if candidate == "Yes":
                        if judge not in persons:
                            persons[judge] = cs.key(f"{SOURCE}.person", judge)
                            cs.add("person", id=persons[judge])
                            name(persons[judge], labels[sheet]["judge"].title())
                        person = persons[judge]
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=labels[sheet]["judge"].title(),
                            is_independent=False,
                        )
                else:
                    person_key = slug(candidate)
                    if person_key not in persons:
                        persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                        cs.add("person", id=persons[person_key])
                        name(persons[person_key], candidate)
                    person = persons[person_key]
                    option = cs.key("us.option", f"{contest_key}:{person}")
                    party = labels[sheet].get(candidate)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party),
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                for precinct, votes in by_precinct.items():
                    code_ = f"{day}:PA:{FIPS}:PHILADELPHIA:{precinct}"
                    target = cs.unit("us.precinct", code_)
                    if target in cs.minted and code_ not in minted:
                        minted[code_] = day
                    cells[(target, option)] = cells.get((target, option), 0) + votes
            for (target, option), votes in cells.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=target,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            report[str(day.year)] += 1
        for code_, day in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", code_),
                polity_id=state,
                scheme_code="us.precinct",
                code=code_,
                parent_id=county_unit,
                area_relation="within",
                area_id=county_area,
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        print(dict(sorted(report.items())))
        return cs.write()
