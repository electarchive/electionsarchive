import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.sw_judicial"
VERSION = "1"
CALIFORNIA = {
    date(2002, 11, 5): ("https://elections.cdn.sos.ca.gov/sov/2002-general/courts.xls", ".xls"),
    date(2006, 11, 7): ("https://elections.cdn.sos.ca.gov/sov/2006-general/complete_sov.pdf", ".pdf"),
    date(2022, 11, 8): ("https://elections.cdn.sos.ca.gov/sov/2022-general/sov/06-summary.pdf", ".pdf"),
}
INDIANA = "https://indianavoters.in.gov/ENRHistorical/GetReferendums"
INDIANA_YEARS = range(2002, 2023, 2)
PAGES = {
    "CA": (
        "California Secretary of State",
        "Statement of Vote: judicial retention",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    "IN": (
        "Indiana Election Division",
        "Historical election results: judicial retention questions",
        "https://indianavoters.in.gov/ENRHistorical",
    ),
}
TITLES = {
    "supreme": "Justice of the Supreme Court",
    "chief": "Chief Justice of the Supreme Court",
    "tax": "Judge of the Tax Court",
    "appeals": "Judge of the Court of Appeals",
}
STATEWIDE = {"Statewide", "Fifth District"}
HELD_OUT = {(2010, "Fifth District")}
ORDINALS = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5}
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when a majority votes Yes."
)
NOTES = {
    "CA": "Statewide totals from the Secretary of State's official Statement of Vote. Votes not cast are left out, so "
    "the set holds only the Yes and No votes.",
    "IN": "Statewide totals from the Indiana Election Division's published results for the general election.",
}
HEADING = re.compile(r"Supreme Court (Chief|Associate) Justice(?: Seat (\d+))?")


class Judge(NamedTuple):
    state: str
    day: date
    title: str
    name: str
    yes: int
    no: int
    url: str
    district: str = ""


def fetch() -> None:
    for url, suffix in CALIFORNIA.values():
        raw.fetch(SOURCE, url, suffix=suffix)
    with httpx.Client(
        headers={"User-Agent": raw.USER_AGENT, "X-Requested-With": "XMLHttpRequest"}, timeout=120
    ) as client:
        client.get("https://indianavoters.in.gov/ENRHistorical")
        for year in INDIANA_YEARS:
            page, pages = 1, 1
            while page <= pages:
                key = f"{INDIANA}#{year}-{page}"
                try:
                    rows = json.loads(raw.cached(SOURCE, key).path.read_text())
                except LookupError:
                    response = client.post(
                        INDIANA, data={"year": year, "pageNumber": page, "sortBy": "", "sortDir": ""}
                    )
                    response.raise_for_status()
                    rows = response.json()
                    raw.store(
                        SOURCE,
                        key,
                        response.content,
                        suffix=".json",
                        retrieved_at=datetime.now(UTC),
                        media_type="application/json",
                    )
                pages = max([int(row["pagesCount"]) for row in rows] or [0])
                page += 1


def california_excel(day: date, path: Path) -> list[Judge]:
    frame = next(iter(pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0).values()))
    found: list[Judge] = []
    live = False
    seat = "supreme"
    for label, yes, no, _ in frame.iter_rows():
        label = (label or "").strip()
        if label.upper() == "STATE SUPREME COURT":
            live = True
        elif live and label.upper().startswith("COURTS OF APPEAL"):
            break
        elif live and label == "Chief Justice":
            seat = "chief"
        elif live and label == "Associate Justice":
            seat = "supreme"
        elif live and label and yes is not None and yes.isdigit():
            found.append(Judge("CA", day, TITLES[seat], label, int(yes), int(no or 0), CALIFORNIA[day][0]))
    return found


def california_text(day: date, path: Path) -> list[Judge]:
    lines = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    found: list[Judge] = []
    for index, line in enumerate(lines):
        for heading in HEADING.finditer(line):
            start = max(0, heading.start() - 1)
            judge: str | None = None
            answers: dict[str, int] = {}
            for follow in lines[index + 1 : index + 9]:
                cell = follow[start:].strip()
                if not cell:
                    continue
                if judge is None:
                    judge = re.split(r"\s{2,}", cell)[0]
                elif answer := re.match(r"(Yes|No)\s+([\d,]+)\s+[\d.]+%", cell):
                    answers[answer[1].lower()] = int(answer[2].replace(",", ""))
                if len(answers) == 2:
                    break
            if judge is None or len(answers) < 2:
                raise ValueError(f"{day} {heading[0]}: no answers")
            found.append(
                Judge(
                    "CA",
                    day,
                    TITLES["chief" if heading[1] == "Chief" else "supreme"],
                    judge,
                    answers["yes"],
                    answers["no"],
                    CALIFORNIA[day][0],
                )
            )
    return found


def california() -> list[Judge]:
    found: list[Judge] = []
    for day, (url, suffix) in CALIFORNIA.items():
        path = raw.cached(SOURCE, url).path
        found.extend(california_excel(day, path) if suffix == ".xls" else california_text(day, path))
    return found


def indiana() -> list[Judge]:
    found: list[Judge] = []
    for year in INDIANA_YEARS:
        page, pages = 1, 1
        while page <= pages:
            key = f"{INDIANA}#{year}-{page}"
            rows = json.loads(raw.cached(SOURCE, key).path.read_text())
            pages = max([int(row["pagesCount"]) for row in rows] or [0])
            page += 1
            for row in rows:
                kind = row["ReferendumType"] or ""
                district = re.sub(r"[-\s]+$", "", (row["District"] or "").strip())
                appeals = "Appeals" in kind
                if not appeals and (district != "Statewide" or not re.search(r"Supreme|Tax Court", kind)):
                    continue
                if (year, district) in HELD_OUT:
                    continue
                judge = re.search(
                    r"Shall (?:Justice|Judge) (.+?),? be retained", row["Question"].strip('" '), re.IGNORECASE
                )
                if judge is None:
                    raise ValueError(row["Question"])
                name = judge[1].strip().strip('"')
                if name.isupper():
                    name = name.title()
                day = datetime.strptime(" ".join(row["ElectionType"].split()[:3]), "%B %d, %Y").date()
                found.append(
                    Judge(
                        "IN",
                        day,
                        TITLES["appeals" if appeals else "tax" if "Tax" in kind else "supreme"],
                        name,
                        int(row["PositiveResult"]),
                        int(row["NegativeResult"]),
                        key,
                        "" if district in STATEWIDE or not appeals else district,
                    )
                )
    return found


def emit() -> Path:
    rows = [*california(), *indiana()]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide judicial retention elections from official results: California Supreme Court 2002, "
            "2006, and 2022, and Indiana Supreme Court, Tax Court, and Court of Appeals 2002 to 2022",
            polity="US",
        )
        states = {
            abbreviation: (unit, area)
            for abbreviation, unit, area in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        state_names = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%%'"""
            ).fetchall()
        )
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.path ~ '^us/(ca|in)/' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        sources: dict[str, UUID] = {}
        for state, (publisher, title, page) in PAGES.items():
            sources[state] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[state],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=page,
                license="Public record",
            )
        districts: dict[str, UUID] = {}

        def electorate(state: str, label: str) -> UUID:
            if not label:
                return states[state][1]
            if label.split()[0] in ORDINALS:
                code = f"IN:coa-{ORDINALS[label.split()[0]]}-before-2018"
                text = f"{label} of the Court of Appeals before 2018"
            else:
                counties = label.split(", ")
                code = f"IN:coa-{slug(counties[0])}-{slug(counties[-1])}-{len(counties)}"
                text = f"Court of Appeals district of {len(counties)} counties, {counties[0]} to {counties[-1]}"
            if code not in districts:
                districts[code] = cs.area("us.judicial_district", code)
                if districts[code] in cs.minted:
                    cs.add("area", id=districts[code], scheme_code="us.judicial_district")
                    cs.add("area_code", area_id=districts[code], code=code, valid_from=None)
                    cs.add("area_parent", area_id=districts[code], parent_id=states[state][1])
                    cs.add(
                        "name",
                        entity_id=districts[code],
                        text=f"{text}, {state_names[state]}",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            return districts[code]

        artifacts: set[str] = set()
        persons: set[str] = set()
        offices: set[str] = set()
        system = cs.key("us.electoral_system", "retention")
        for row in sorted(rows, key=lambda item: (item.state, item.day, item.name)):
            state, day = row.state, row.day
            if row.url not in artifacts:
                artifacts.add(row.url)
                cs.artifact(raw.cached(SOURCE, row.url), sources[state])
            office_key = f"{slug(row.title)}:{state}"
            office = cs.key("us.office", office_key)
            if office_key not in offices:
                offices.add(office_key)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=cs.polity(f"US-{state}"), kind="other")
                    cs.add(
                        "name",
                        entity_id=office,
                        text=f"{row.title} of {state_names[state]}",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            first, last = identity(row.name)
            suffix = f"{slug(first)}-{slug(last)}"
            shown = display(row.name)
            election = cs.key("us.election", f"{day}:{state}")
            if election in cs.minted:
                raise ValueError(f"{day}:{state} has no election")
            contest_key = f"{day}:{state}:{slug(row.title)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=electorate(state, row.district),
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day.year} {state_names[state]} {row.title} retention: {shown}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            base = f"us/{state.lower()}/{day}-general/{slug(row.title)}-{suffix}"
            path, count = base, 1
            while path in taken:
                count += 1
                path = f"{base}-{count}"
            taken.add(path)
            cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[state])
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=f"{METHOD} {NOTES[state]}")
            person_key = f"{state}:{suffix}"
            person = cs.key("us.retention.person", person_key)
            if person_key not in persons:
                persons.add(person_key)
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=shown, lang="en", kind="official", is_preferred=True)
            options: dict[str, UUID] = {}
            for position, answer in enumerate(("yes", "no")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
                )
            candidacy = cs.key("us.candidacy", f"{contest_key}:yes:head")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options["yes"],
                role="head",
                ballot_name=shown,
                is_independent=False,
            )
            retained = row.yes > row.no
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options["yes"],
                candidacy_id=None,
                kind="elected" if retained else "lost",
                seats=1 if retained else None,
            )
            unit = str(states[state][0])
            cs.frame(
                "result_value",
                pl.DataFrame(
                    [
                        (str(result), unit, str(options["yes"]), 0, 1, 0, 0, row.yes),
                        (str(result), unit, str(options["no"]), 0, 1, 0, 0, row.no),
                        (str(result), unit, None, 0, 4, 0, 0, row.yes + row.no),
                    ],
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
            print(contest_key, row.yes, row.no)
        return cs.write()
