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
VERSION = "5"
FIPS = "42101"
PLACE = "4260000"
SPECIAL_DAYS = {
    date(2011, 2, 1),
    date(2015, 3, 24),
    date(2015, 8, 11),
    date(2016, 3, 15),
    date(2017, 3, 21),
    date(2019, 3, 12),
    date(2024, 2, 13),
}
TRUNCATED = {date(2011, 2, 1): "185"}
STATE_HOUSE = re.compile(r"^(?:SPECIAL ELECTION - )?REPRESENTATIVE IN THE GENERAL ASSEMBLY ?-? ?(\d+)")
QUESTION = re.compile(r"^(?:PROPOSED )?(CHARTER CHANGE QUESTION|CITY BOND QUESTION|QUESTION)(?: ?#(\d+))?")
WRITE_IN = re.compile(r"(?i)write.?in")
ORDINAL = re.compile(r"(\d+)(?:ST|ND|RD|TH)")
RETENTION = re.compile(r"^(.*?) (YES SI|NO NO)$")
OFFICES = [
    (
        r"^SPECIAL ELECTION - COUNCIL AT[- ]?LARGE",
        "council-at-large-special",
        "Philadelphia City Council, at-large special election",
        PLACE,
        1,
    ),
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
    "COMMON PLEAS COURT": "common-pleas",
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
    "LIBERTARIAN": "libertarian-party",
    "LIB": "libertarian-party",
    "GREEN": "green-party",
    "GRN": "green-party",
    "CST": "constitution-party",
    "CONSTITUTION": "constitution-party",
    "REP": "republican-party",
    "R": "republican-party",
}
NOTE = (
    METHOD
    + " Write-in votes are not included. The file prints no winners, so no outcomes are recorded, and the seat count "
    "is stored only where Philadelphia's charter fixes it."
)


def tidy(text: str) -> str:
    if text.isupper():
        text = re.sub(
            r"[A-Za-z']+",
            lambda m: m[0] if m[0] in ("II", "III", "IV") else m[0][0] + m[0][1:].lower(),
            text,
        )
        text = re.sub(r"\b(Mc|O'|D')([a-z])", lambda m: m[1] + m[2].upper(), text)
    return re.sub(r"\b([A-Z])\b(?![.'])", r"\1.", text)


def office_of(
    day: date, contest: str, candidate: str
) -> tuple[str, str | None, str | None, str | None, str | None] | None:
    if day in SPECIAL_DAYS:
        found = STATE_HOUSE.match(contest)
        if found is not None:
            return "state-house", TRUNCATED.get(day, found[1]), None, None, None
    asked = QUESTION.match(contest)
    if asked is not None:
        kind = {"CHARTER CHANGE QUESTION": "charter-change", "CITY BOND QUESTION": "city-bond"}.get(
            asked[1], "question"
        )
        number = asked[2] or (re.search(r"#(\\d+)", contest) or [None, None])[1]
        answer = (
            "Yes"
            if re.match(r"(?i)yes|s[ií]\\b|for\\b", candidate)
            else "No"
            if re.match(r"(?i)no\\b", candidate)
            else None
        )
        return None if answer is None else ("question", None, None, answer, f"{kind}-{number}" if number else kind)
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
        answer = (
            "Yes"
            if re.search(r"\bYES\b", candidate.upper())
            else "No"
            if re.search(r"\bNO\b", candidate.upper())
            else None
        )
        return None if key is None or answer is None else (key, None, judge.strip(), answer, None)
    for pattern, key, _, _, _ in OFFICES:
        if re.match(pattern, contest):
            if key == "council":
                found = ORDINAL.search(contest)
                return None if found is None else (key, f"district-{int(found[1])}", None, None, None)
            vote = RETENTION.match(candidate)
            if vote is not None and key in COURTS.values():
                return key, None, vote[1], "Yes" if vote[2].startswith("YES") else "No", None
            return key, None, None, None, None
    return None


def emit() -> Path:
    tallies: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    labels: dict[tuple, dict[str, str | None]] = defaultdict(dict)
    refs: dict[tuple, dict[str, tuple[str, str | None, str]]] = defaultdict(dict)
    urls: dict[date, str] = {}
    frame = pl.read_parquet(PARSED / "rows.parquet")
    printed = {
        (day, contest, ballot, candidate): (total, other)
        for day, contest, ballot, candidate, total, other in pl.read_parquet(PARSED / "printed.parquet").iter_rows()
    }
    summaries: dict[tuple[date, str], list[int]] = defaultdict(list)
    for day, candidate, total in pl.read_parquet(PARSED / "summaries.parquet").iter_rows():
        summaries[(day, re.sub(r"[^A-Z0-9]", "", candidate.upper()))].append(total)
    for day, url, contest, precinct, candidate, votes, party, ballot in frame.select(
        "day", "url", "contest", "precinct", "candidate", "votes", "party", "ballot"
    ).iter_rows():
        name = contest.partition("|")[0]
        found = office_of(day, name, candidate)
        if found is None:
            continue
        office, seat, judge, answer, qkey = found
        retention = answer is not None
        write_in = WRITE_IN.match(candidate) is not None
        label = "Write-ins" if write_in else answer or candidate
        sheet = (day, office, seat, BALLOTS.get(ballot or ""), judge and slug(judge), qkey)
        tallies[sheet][label][precinct] += votes
        refs[sheet].setdefault(label, (name, ballot, candidate))
        if not retention:
            labels[sheet][label] = PARTIES.get((party or ballot or "").upper())
        elif judge:
            labels[sheet]["judge"] = judge
        if qkey:
            labels[sheet]["name"] = name
        urls[day] = url
    nominees: dict[tuple, set[str]] = defaultdict(set)
    for sheet, options in tallies.items():
        if sheet[3] is not None:
            for candidate in options:
                nominees[(sheet[0].year, sheet[1], sheet[2], slug(candidate))].add(sheet[3])
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Philadelphia's city and county elections, questions, and special elections by ward and division, 2007-2026",
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
        parties = {name: cs.key("us.party", name) for name in sorted(set(PARTIES.values()))}
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
        statewide_questions = {
            day
            for (day,) in conn.execute(
                """select e.date_from from contest k join election e on e.id = k.election_id
                   join slug s on s.entity_id = e.polity_id and s.path = 'us/pa'
                   where k.kind = 'ballot_question'"""
            ).fetchall()
        }
        held = {
            path for (path,) in conn.execute("select path from slug where path like 'us/pa/philadelphia/%%'").fetchall()
        }
        made = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^[0-9-]+:PA:(42101|4260000|house):'"
            ).fetchall()
        }
        own = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = %s", [f"{SOURCE}.result_set"]
            ).fetchall()
        }
        elections: dict[tuple[date, bool], UUID] = {}
        owners: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}
        districts: set[str] = set()
        minted: dict[str, date] = {}
        report: dict[str, int] = defaultdict(int)

        names = dict(
            conn.execute(
                """select n.entity_id, n.id from name n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.is_preferred and n.lang = 'en'""",
                [SOURCE],
            ).fetchall()
        )
        names.update(
            {
                entity: name_id
                for entity, name_id, text in conn.execute(
                    """select n.entity_id, n.id, n.text from name n
                       join external_id x on x.entity_id = n.entity_id and x.scheme = %s
                       where n.is_preferred and n.lang = 'en'""",
                    [f"{SOURCE}.person"],
                ).fetchall()
                if text.isupper()
            }
        )

        def name(entity: UUID, text: str) -> None:
            cs.add(
                "name", id=names.get(entity), entity_id=entity, text=text, lang="en", kind="official", is_preferred=True
            )

        for sheet, options in sorted(tallies.items(), key=lambda item: (item[0][0], item[0][1], str(item[0][2:]))):
            day, office, seat, ballot, judge, qkey = sheet
            question = office == "question"
            house = office == "state-house"
            if house:
                title, code, seats = f"Pennsylvania House of Representatives, district {seat}", "house", 1
            elif question:
                title, code, seats = "Philadelphia ballot question", PLACE, None
            else:
                title, code, seats = next((t, c, s) for _, k, t, c, s in OFFICES if k == office)
            if question and qkey.startswith("question-") and day in statewide_questions:
                report["statewide question"] += 1
                continue
            if any(hint in text for text in existing[day] for hint in [COVERED.get(office, "\0")]):
                report["held by another pipeline"] += 1
                continue
            polity = state if house else polities[code][0]
            civic = (day, house)
            if civic not in elections:
                if house:
                    found = conn.execute(
                        """select e.id from election e join slug s on s.entity_id = e.id
                           where e.date_from = %s and s.path = %s limit 1""",
                        [day, f"us/pa/{day}-special"],
                    ).fetchone()
                else:
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
                    elections[civic] = found[0]
                else:
                    kind = (
                        "special"
                        if day in SPECIAL_DAYS
                        else "primary"
                        if ballot or day.month < 7
                        else "general"
                        if day.year % 2 == 0
                        else "local"
                    )
                    elections[civic] = cs.key("us.election", f"{day}:PA:{'house' if house else PLACE}")
                    cs.add(
                        "election",
                        id=elections[civic],
                        polity_id=state if house else polities[PLACE][0],
                        parent_id=None if house or umbrella is None else umbrella[0],
                        kind=kind,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(
                        elections[civic],
                        f"{day.year} Pennsylvania special election ({day:%B} {day.day})"
                        if house
                        else f"{day.year} Philadelphia {kind} election",
                    )
                    cs.add(
                        "slug",
                        path=f"us/pa/{day}-special" if house else f"{polities[PLACE][2]}/{day}-{kind}",
                        entity_id=elections[civic],
                    )
            election = elections[civic]
            folder = conn.execute("select path from slug where entity_id = %s", [election]).fetchone()
            folder = folder[0] if folder is not None else f"{polities[PLACE][2]}/{day}-local"
            retention = judge is not None
            parts = (seat, qkey, f"retention-{judge}" if retention else None, ballot)
            if house:
                path = f"{folder}/state-house-{seat}-special-election"
                contest_key = f"{day}:PA:house:{seat}:s"
            else:
                path = f"{folder}/{office}" + "".join(f"-{part}" for part in parts if part)
                contest_key = f"{day}:PA:{code}:{office}" + "".join(f":{part}" for part in parts if part)
            if (path in held or contest_key in made) and contest_key not in own:
                report["held by another pipeline"] += 1
                continue
            owner_key = f"PA:{code}:{office}" + (f":retention:{judge}" if retention else "")
            if not house and not question and owner_key not in owners:
                owners[owner_key] = cs.key("us.office", owner_key)
                cs.add(
                    "office",
                    id=owners[owner_key],
                    polity_id=polity,
                    kind="mayor" if office == "mayor" else "other",
                )
                judge_name = labels[sheet].get("judge") if retention else None
                name(owners[owner_key], f"{title}, retention of {tidy(judge_name)}" if retention else title)
            area = place_area if code == PLACE else county_area
            if house:
                area = cs.area("us.sldl", f"PA:{seat}")
            elif seat is not None:
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
                kind="ballot_question" if question else "body_seats" if house else "office",
                office_id=None if house or question else owners[owner_key],
                district_area_id=area,
                magnitude=None if question else 1 if retention or house else seats,
                round_number=1,
                electoral_system_id=systems[
                    "referendum" if retention or question else "fptp" if seats == 1 else "block"
                ],
                default_result_kind="counted",
                term="remainder" if house or office == "council-at-large-special" else None if question else "full",
            )
            if question:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            if house:
                label = f"{day.year} Pennsylvania House, district {seat}, special election"
            elif question:
                label = f"{day.year} Philadelphia: {labels[sheet]['name'].title()}"
            elif retention:
                label = f"{day.year} {title}, retention of {tidy(labels[sheet]['judge'])}"
            else:
                label = (
                    f"{day.year} {title}"
                    + (f", {seat.replace('-', ' ')}" if seat else "")
                    + (f", {ballot.title()} primary" if ballot else "")
                )
            name(contest, label)
            cs.add("slug", path=path, entity_id=contest)
            if seat is not None and not house:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md="The district is a label without a boundary here; division results are not assigned to it.",
                )
            sums = {candidate: sum(by_precinct.values()) for candidate, by_precinct in options.items()}
            checked, differs, unchecked = 0, [], 0
            gap = 0
            for candidate, total in sums.items():
                if candidate == "judge":
                    continue
                reference = refs[sheet].get(candidate)
                if reference is None:
                    continue
                exact = printed.get((day, reference[0], reference[1], reference[2]))
                if exact is not None:
                    agrees = total + exact[1] == exact[0]
                    expected = exact[0] - exact[1]
                else:
                    entries = summaries.get((day, re.sub(r"[^A-Z0-9]", "", reference[2].upper())))
                    if not entries or WRITE_IN.match(candidate):
                        unchecked += 1
                        continue
                    agrees = total in entries or total == sum(entries)
                    expected = total if agrees else entries[0]
                if agrees:
                    checked += 1
                else:
                    differs.append(f"{candidate.title()}: divisions {total:,}, printed {expected:,}")
                    gap += max(expected - total, 0)
            if differs:
                tie = "Division totals differ from the file's printed totals for " + "; ".join(differs) + "."
            elif checked and not unchecked:
                tie = (
                    "Division totals agree with the City Commissioners' printed totals for every candidate in the "
                    "workbook; the certified canvass itself is not linked on the archive page."
                )
            elif checked:
                tie = (
                    f"Division totals agree with the City Commissioners' printed totals for {checked} of "
                    f"{checked + unchecked} options; the rest have no printed total in the workbook."
                )
            else:
                tie = (
                    "The file prints no totals, so the division sums are not checked against a certified count "
                    "(contests also held at county level elsewhere in the archive are checked there)."
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            total_votes = sum(sums.values())
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
                completeness=None if house else round(total_votes / (total_votes + gap), 4) if gap else 1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=f"{NOTE} {tie}")
            if differs:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=tie)
            if house:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The file covers Philadelphia's divisions only; a district that reaches past the county line is incomplete here.",
                )
            cells: dict[tuple[UUID, UUID], int] = {}
            borrowed = 0
            for candidate, by_precinct in options.items():
                if candidate == "judge":
                    continue
                if question or retention:
                    option = cs.key("us.option", f"{contest_key}:{candidate.lower()}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if candidate == "Write-ins" else "answer",
                        ballot_position=(1 if candidate == "Yes" else 2) if question else None,
                        ballot_label=candidate,
                    )
                    if retention and candidate == "Yes":
                        if judge not in persons:
                            persons[judge] = cs.key(f"{SOURCE}.person", judge)
                            cs.add("person", id=persons[judge])
                            name(persons[judge], tidy(labels[sheet]["judge"]))
                        person = persons[judge]
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=tidy(labels[sheet]["judge"]),
                            is_independent=False,
                        )
                elif candidate == "Write-ins":
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    person_key = slug(candidate)
                    if person_key not in persons:
                        persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                        cs.add("person", id=persons[person_key])
                        name(persons[person_key], tidy(candidate))
                    person = persons[person_key]
                    option = cs.key("us.option", f"{contest_key}:{person}")
                    party = labels[sheet].get(candidate)
                    if party is None and ballot is None:
                        ballots = nominees.get((day.year, office, seat, slug(candidate)), set())
                        if len(ballots) == 1:
                            party = PARTIES[next(iter(ballots)).upper()]
                            borrowed += 1
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party),
                        ballot_label=tidy(candidate),
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=tidy(candidate),
                        is_independent=False,
                    )
                for precinct, votes in by_precinct.items():
                    code_ = f"{day}:PA:{FIPS}:PHILADELPHIA:{precinct}"
                    target = cs.unit("us.precinct", code_)
                    if target in cs.minted and code_ not in minted:
                        minted[code_] = day
                    cells[(target, option)] = cells.get((target, option), 0) + votes
            if borrowed:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=(
                        "The workbook prints no party for the general election; a candidate's party here is the "
                        "primary ballot they appear on in the Commissioners' files for the same office and year."
                    ),
                )
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
