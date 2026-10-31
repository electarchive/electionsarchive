import re
import subprocess
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display
from ea.store import DATA, connect

SOURCE = "us.ak_judicial"
VERSION = "2"
INBOX = DATA / "inbox" / "manual" / "MF-454"
URL = "https://www.elections.alaska.gov/results/"
PUBLISHER = "Alaska Division of Elections"
TITLE = "Official election summary report: judicial retention"
FILES = {
    date(2010, 11, 2): "2010/resultsOF.pdf",
    date(2012, 11, 6): "2012/results (2).pdf",
    date(2014, 11, 4): "2014/results (1).pdf",
    date(2016, 11, 8): "2016/results.pdf",
    date(2020, 11, 3): "2020/ElectionSummaryReportRPT24.pdf",
    date(2022, 11, 8): "2022/ElectionSummaryReportRPT.pdf",
    date(2024, 11, 5): "2024/ElectionSummaryReport.pdf",
}
UNNAMED = {(2010, "Supreme Court"): "Fabe", (2010, "Court of Appeals"): "Mannheimer"}
COLUMNS = ((0, 300), (300, 312))
JUDGES = {
    "Fabe": "Dana Fabe",
    "Mannheimer": "David Mannheimer",
    "Winfree": "Daniel Winfree",
    "Bolger": "Joel Bolger",
    "Stowers": "Craig Stowers",
    "Maassen": "Peter Maassen",
    "Carney": "Susan Carney",
    "Wollenberg": "Tracey Wollenberg",
    "Harbison": "Bethany Harbison",
    "Borghesan": "Dario Borghesan",
    "Henderson": "Jennifer Henderson",
    "Allard": "Marjorie Allard",
    "Terrell": "Timothy Terrell",
}
OFFICES = {"Supreme Court": "Justice of the Supreme Court", "Court of Appeals": "Judge of the Court of Appeals"}
BLOCK = re.compile(
    r"^\s*(Supreme Court|Supreme Crt|Court of Appeals)(?:\s*-\s*(?:Justice|Judge)\s+(\S+))?\s*$", re.IGNORECASE
)
VOTES = re.compile(r"^(YES|NO)\s+([\d,]+)\s+[\d.]+%$")
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when a majority votes Yes."
)
NOTE = "Statewide totals from the Division of Elections' official election summary report for the general election."


def fetch() -> None:
    for day, name in FILES.items():
        try:
            raw.cached(SOURCE, f"{URL}#{day}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{day}",
                (INBOX / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def pages(path: Path, year: int) -> list[str]:
    if year >= 2020:
        return [
            subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
        ]
    return [
        subprocess.run(
            ["pdftotext", "-layout", "-x", str(x), "-y", "0", "-W", str(width), "-H", "800", str(path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for x, width in COLUMNS
    ]


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day in FILES:
        path = raw.cached(SOURCE, f"{URL}#{day}").path
        for text in pages(path, day.year):
            current: dict[str, object] | None = None
            for line in text.splitlines():
                block = BLOCK.match(line)
                if block is not None:
                    court = "Supreme Court" if block[1].lower().startswith("supreme") else "Court of Appeals"
                    surname = block[2] or UNNAMED[(day.year, court)]
                    current = {"day": day, "court": court, "surname": surname}
                    continue
                if current is None:
                    continue
                votes = VOTES.match(line.strip())
                if votes is not None:
                    current[votes[1].lower()] = int(votes[2].replace(",", ""))
                    if "yes" in current and "no" in current:
                        found.append(current)
                        current = None
    return found


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska Supreme Court and Court of Appeals retention elections 2010–2024, statewide, "
            "from the Division of Elections' official summary reports",
            polity="US-AK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        for day in FILES:
            cs.artifact(raw.cached(SOURCE, f"{URL}#{day}"), source)
        state_unit, state_area, polity = conn.execute(
            """select u.id, u.area_id, p.id from polity p
               join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-AK'"""
        ).fetchone()
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ak/%%'").fetchall()}
        retention_system = cs.key("us.electoral_system", "retention")

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

        offices: set[UUID] = set()
        persons: set[str] = set()
        ordered = defaultdict(list)
        for row in rows:
            ordered[row["day"]].append(row)
        for day, group in sorted(ordered.items()):
            election = cs.key("us.election", f"{day}:AK")
            for row in group:
                title = OFFICES[str(row["court"])]
                judge = JUDGES[str(row["surname"])]
                office = cs.key("us.office", f"{slug(title)}:AK")
                if office not in offices:
                    offices.add(office)
                    if office in cs.minted:
                        cs.add("office", id=office, polity_id=polity, kind="other")
                        cs.add(
                            "name",
                            entity_id=office,
                            text=f"{title} of Alaska",
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                person_key = f"AK:{slug(judge)}"
                suffix = f"-retention-{slug(judge)}"
                contest_key = f"{day}:AK:{slug(title)}{suffix}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=state_area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=retention_system,
                    default_result_kind="counted",
                )
                shown = display(judge)
                name(contest, f"{day.year} Alaska {title} retention: {shown}")
                if contest in cs.minted:
                    base = f"us/ak/{day}-general/{slug(title)}{suffix}"
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
                cs.add("note", entity_id=result, kind="methodology", body_md=f"{METHOD} {NOTE}")
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, shown)
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
                yes, no = int(row["yes"]), int(row["no"])
                retained = yes > no
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options["yes"],
                    candidacy_id=candidacy,
                    kind="elected" if retained else "lost",
                    seats=1 if retained else None,
                )
                values = [
                    (str(result), str(state_unit), str(options["yes"]), 0, 1, 0, 0, yes),
                    (str(result), str(state_unit), str(options["no"]), 0, 1, 0, 0, no),
                    (str(result), str(state_unit), None, 0, 4, 0, 0, yes + no),
                ]
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
                print(contest_key, yes, no)
        return cs.write()
