import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import DATA, connect

SOURCE = "us.tn_judicial"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-452"
URL = "https://sos.tn.gov/elections/results"
PUBLISHER = "Tennessee Secretary of State, Division of Elections"
TITLE = "Judicial retention results by county"
FILES = {
    "jud-c.pdf": date(2000, 8, 3),
    "Retention.pdf": date(2006, 8, 3),
    "JudicialRetentions.pdf": date(2008, 8, 7),
    "CCStateGeneralCountyTotals.pdf": date(2010, 8, 5),
    "Judicial Certification County.pdf": date(2012, 8, 2),
    "20140807_JudicialRetentionCounty.pdf": date(2014, 8, 7),
    "5 Retention County Totals.pdf": date(2016, 8, 4),
    "Aug 2020 Judicial Retention County Totals.pdf": date(2020, 8, 6),
    "20220804JudicialRetentionbyCounty.pdf": date(2022, 8, 4),
    "20240801JudicialRetentionbyCounty.pdf": date(2024, 8, 1),
}
OFFICES = {
    "Supreme Court": "Justice of the Supreme Court",
    "Court of Appeals": "Judge of the Court of Appeals",
    "Court of Criminal Appeals": "Judge of the Court of Criminal Appeals",
}
MONTH = re.compile(
    r"^\s*(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\s*$"
)
ROW = re.compile(r"^([A-Za-z][A-Za-z .'-]*?)\s{2,}((?:[\d,]+\s*)+)$")
COLUMNS = re.compile(r"^\s*(?:\d+\s*-?\s*(?:Yes|No)?\s*)+$")
COURT = re.compile(r"(Supreme Court|Court of Criminal Appeals|Court of Appeals)\b")
GENERIC = re.compile(r"^(State of Tennessee|State General|General Election)$")
FOOTER = re.compile(r"Page \d+ of \d+|^\d{1,2}-[A-Za-z]{3}-\d{2}\s")
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers "
    "(Retain and Replace on the ballot from 2010 on). The judge keeps the seat when a majority votes Yes."
)
NOTE = (
    "County totals from the Tennessee Division of Elections' official county results for the August general election."
)


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def fetch() -> None:
    for name in FILES:
        try:
            raw.cached(SOURCE, f"{URL}#{name}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{name}",
                (INBOX / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def header(buffer: list[str]) -> tuple[str, str, list[tuple[int, str]]] | None:
    court = None
    division = ""
    judges: list[tuple[int, str]] = []
    for line in buffer:
        text = line.strip()
        numbered = re.match(r"^(\d+)\s*\.?\s*(.+)$", text)
        if numbered is not None:
            retain = re.match(r"^(?:Retain|Yes)\s*-\s*(.+)$", numbered[2])
            if retain is not None:
                judges.append((int(numbered[1]), retain[1]))
            elif not re.match(r"^(Replace|No|Retain|Yes)\b", numbered[2]):
                judges.append((int(numbered[1]), numbered[2]))
            continue
        text = re.sub(r"^(?:General )?Judicial Retention(?: for)?\s*", "", text)
        found = COURT.search(text)
        if found is not None and court is None:
            court = found[1]
        area = re.search(r"\b(Eastern|Middle|Western)\b", text)
        if area is not None:
            division = area[1]
        rest = COURT.sub("", text)
        rest = re.sub(r"\b(Eastern|Middle|Western)\b|\bDivision\b", "", rest).strip(" ,-")
        if rest and (found is not None or area is not None):
            judges.append((1, rest))
    if court is None or not judges:
        return None
    return court, division, judges


def blocks(path: Path) -> list[dict[str, object]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    found: dict[tuple[str, str, tuple[str, ...]], dict[str, object]] = {}
    buffer: list[str] = []
    state = "rows"
    current: dict[str, object] | None = None
    for line in text.splitlines():
        if not line.strip() or FOOTER.search(line) or MONTH.match(line):
            continue
        if GENERIC.match(line.strip()):
            continue
        if COLUMNS.match(line) and state == "header":
            state = "rows"
            parsed = header(buffer)
            current = None
            if parsed is not None:
                court, division, judges = parsed
                key = (court, division, tuple(name for _, name in judges))
                current = found.setdefault(
                    key,
                    {"court": court, "division": division, "judges": judges, "columns": line.split(), "rows": {}},
                )
            continue
        row = ROW.match(line)
        if row is not None and state == "rows":
            if re.match(r"(STATE|STATEWIDE|DISTRICT)\b", row[1].strip(), re.IGNORECASE):
                continue
            if current is not None:
                current["rows"][row[1].strip()] = [int(value.replace(",", "")) for value in row[2].split()]
            continue
        if re.match(r"(STATE|STATEWIDE|DISTRICT)\s", line.strip(), re.IGNORECASE) and state == "rows":
            continue
        if state == "rows":
            buffer = []
            state = "header"
        buffer.append(line)
    return list(found.values())


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for name, day in FILES.items():
        for block in blocks(raw.cached(SOURCE, f"{URL}#{name}").path):
            if not block["rows"]:
                continue
            judges = block["judges"]
            for index, (_, judge) in enumerate(judges):
                yes, no = index * 2, index * 2 + 1
                judge = re.sub(r"^([^,]+), ([A-Z][^,]*)$", r"\2 \1", judge) if day.year == 2000 else judge
                for county, values in block["rows"].items():
                    found.append(
                        {
                            "day": day,
                            "court": block["court"],
                            "division": block["division"],
                            "judge": judge,
                            "county": county,
                            "yes": values[yes],
                            "no": values[no],
                        }
                    )
    return found


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee appellate judicial retention elections 2000–2024 by county, "
            "from the Division of Elections' official results",
            polity="US-TN",
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
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, f"{URL}#{name}"), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '47%%'"""
            ).fetchall()
        }
        state_area, polity = conn.execute(
            """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-TN'"""
        ).fetchone()
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/tn/%%'").fetchall()}
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
        groups: dict[tuple[date, str, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], str(row["court"]), str(row["judge"]))].append(row)
        for (day, court, judge), group in sorted(groups.items()):
            title = OFFICES[court]
            office = cs.key("us.office", f"{slug(title)}:TN")
            if office not in offices:
                offices.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    cs.add(
                        "name",
                        entity_id=office,
                        text=f"{title} of Tennessee",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            first, last = identity(judge)
            person_key = f"TN:{slug(first)}-{slug(last)}"
            suffix = f"-retention-{slug(first)}-{slug(last)}"
            shown = display(judge)
            division = str(group[0]["division"])
            contest_key = f"{day}:TN:{slug(title)}{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:TN:primary")
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
            where = f" ({division} Division)" if division else ""
            name(contest, f"{day.year} Tennessee {title}{where} retention: {shown}")
            if contest in cs.minted:
                base = f"us/tn/{day}-primary/{slug(title)}{suffix}"
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
            by_unit: dict[str, dict[str, int]] = defaultdict(lambda: {"yes": 0, "no": 0})
            for row in group:
                unit = str(counties[flat(str(row["county"]))])
                by_unit[unit]["yes"] += int(row["yes"])
                by_unit[unit]["no"] += int(row["no"])
            yes = sum(counts["yes"] for counts in by_unit.values())
            no = sum(counts["no"] for counts in by_unit.values())
            retained = yes > no
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options["yes"],
                candidacy_id=candidacy,
                kind="elected" if retained else "lost",
                seats=1 if retained else None,
            )
            values = []
            for unit, counts in by_unit.items():
                values.append((str(result), unit, str(options["yes"]), 0, 1, 0, 0, counts["yes"]))
                values.append((str(result), unit, str(options["no"]), 0, 1, 0, 0, counts["no"]))
                values.append((str(result), unit, None, 0, 4, 0, 0, counts["yes"] + counts["no"]))
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
            print(contest_key, len(by_unit), yes, no)
        return cs.write()
