import re
import subprocess
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.ak_judicial import COLUMNS, INBOX, PUBLISHER, TITLE, URL
from ea.sources.us.retention import display
from ea.store import connect

SOURCE = "us.ak_trial"
VERSION = "1"
FILES = {
    date(2000, 11, 7): "MF-471/2000/results.txt",
    date(2002, 11, 5): "MF-471/2002/results.txt",
    date(2004, 11, 2): "MF-454/2004/results (3).pdf",
    date(2006, 11, 7): "MF-471/2006/results.htm",
    date(2008, 11, 4): "MF-471/2008/results (1).htm",
    date(2010, 11, 2): "MF-454/2010/resultsOF.pdf",
    date(2012, 11, 6): "MF-454/2012/results (2).pdf",
    date(2014, 11, 4): "MF-454/2014/results (1).pdf",
    date(2016, 11, 8): "MF-454/2016/results.pdf",
    date(2018, 11, 6): "MF-454/2018/results18.pdf",
    date(2020, 11, 3): "MF-454/2020/ElectionSummaryReportRPT24.pdf",
    date(2022, 11, 8): "MF-454/2022/ElectionSummaryReportRPT.pdf",
    date(2024, 11, 5): "MF-454/2024/ElectionSummaryReport.pdf",
}
MEDIA = {".txt": "text/plain", ".htm": "text/html", ".pdf": "application/pdf"}
COURTS = {"S": "Superior Court", "D": "District Court"}
OFFICES = {"S": "Judge of the Superior Court", "D": "Judge of the District Court"}
DISTRICTS = {"1": "First", "2": "Second", "3": "Third", "4": "Fourth"}
TITLE_PATTERNS = (
    re.compile(r'"JD\s*(\d)\s+(SUPERIOR|DISTRICT)\s*-\s*([^"]+)"', re.IGNORECASE),
    re.compile(
        r"""^[\d\s]*"?(SUPERIOR|DISTRICT|SUP|DIST|DIS)\.?\s+(?:COURT|CT)\s+JD\s*(\d)\s*[,\-]?\s*([^"]+?)"?(?:\s+\d+\s+\d+.*)?\s*$""",
        re.IGNORECASE,
    ),
)
VOTES = re.compile(r"^\s*(YES|NO)\s+([\d,]+)\s+[\d.]+\s*%\s*$")
TEXT_VOTES = re.compile(r'^\s+\d+\s+\d+\s+"(YES|NO)"\s+""\s+\d+\s+(\d+)\s*$')
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when a majority votes Yes. Alaska's trial judges stand for retention within their "
    "judicial district only, so the totals are for the judge's district, recorded against the state."
)
NOTE = (
    "Totals for the judicial district from the Division of Elections' official election summary report for the "
    "general election. The report prints the judge's surname only."
)


def fetch() -> None:
    for day, name in FILES.items():
        try:
            raw.cached(SOURCE, f"{URL}#{day}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{day}",
                (INBOX / name).read_bytes(),
                suffix=Path(name).suffix,
                retrieved_at=datetime.now(UTC),
                media_type=MEDIA[Path(name).suffix],
            )


def text_of(path: Path, year: int) -> list[str]:
    if path.suffix == ".txt":
        return path.read_text(errors="replace").splitlines()
    if path.suffix == ".htm":
        html = re.sub(
            r"</tr>|</th>|<br>", "\n", path.read_text(errors="replace").replace("\n", " "), flags=re.IGNORECASE
        )
        html = re.sub(r"<[^>]+>", " ", html)
        return [re.sub(r"\s+", " ", line).strip() for line in html.splitlines()]
    if year >= 2020:
        return subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
    found: list[str] = []
    for x, width in COLUMNS:
        found.extend(
            subprocess.run(
                ["pdftotext", "-layout", "-x", str(x), "-y", "0", "-W", str(width), "-H", "800", str(path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
        )
    return found


def title_of(line: str) -> tuple[str, str, str] | None:
    pattern = TITLE_PATTERNS[0].search(line)
    if pattern is not None:
        return pattern[2][0].upper(), pattern[1], pattern[3]
    pattern = TITLE_PATTERNS[1].match(line)
    if pattern is not None:
        return pattern[1][0].upper(), pattern[2], pattern[3]
    return None


def lines() -> list[dict[str, object]]:
    found: dict[tuple[date, str, str, str], dict[str, object]] = {}
    for day in FILES:
        path = raw.cached(SOURCE, f"{URL}#{day}").path
        current: dict[str, object] | None = None
        for line in text_of(path, day.year):
            title = title_of(line)
            if title is not None:
                court, district, judge = title
                judge = re.sub(r"\s*-\s*$", "", judge.strip())
                current = {"day": day, "court": court, "district": district, "judge": judge}
                continue
            if current is None:
                continue
            votes = VOTES.match(line)
            if votes is not None:
                current[votes[1].lower()] = int(votes[2].replace(",", ""))
            else:
                votes = TEXT_VOTES.match(line)
                if votes is not None:
                    current[votes[1].lower()] = int(votes[2])
            if "yes" in current and "no" in current:
                key = (day, str(current["court"]), str(current["district"]), slug(str(current["judge"])))
                if key in found and (found[key]["yes"], found[key]["no"]) != (current["yes"], current["no"]):
                    raise ValueError(f"conflicting rows for {key}")
                found[key] = current
                current = None
    return list(found.values())


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska superior and district court retention elections 2000–2024, by judicial district, "
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
                court, district = str(row["court"]), str(row["district"])
                office_title = OFFICES[court]
                shown = display(str(row["judge"]))
                office = cs.key("us.office", f"{slug(office_title)}:AK")
                if office not in offices:
                    offices.add(office)
                    if office in cs.minted:
                        cs.add("office", id=office, polity_id=polity, kind="other")
                        cs.add(
                            "name",
                            entity_id=office,
                            text=f"{office_title} of Alaska",
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                person_key = f"AK:{court}{district}:{slug(shown)}"
                suffix = f"{slug(COURTS[court])}-jd{district}-retention-{slug(shown)}"
                contest_key = f"{day}:AK:{suffix}"
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
                name(
                    contest,
                    f"{day.year} Alaska {COURTS[court]}, {DISTRICTS[district]} Judicial District, retention: {shown}",
                )
                if contest in cs.minted:
                    base = f"us/ak/{day}-general/{suffix}"
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
        return cs.write()
