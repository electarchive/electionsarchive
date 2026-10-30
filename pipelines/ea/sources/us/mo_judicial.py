import re
import subprocess
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

SOURCE = "us.mo_judicial"
VERSION = "1"
BASE = "https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
FILES = {
    date(2012, 11, 6): "OfficialResults11-6-12.pdf",
    date(2014, 11, 4): "OfficialResults11-4-14.pdf",
    date(2016, 11, 8): "ActualResults-November82016-GeneralElection.pdf",
    date(2018, 11, 6): "ActualResults-November62018-FINAL.pdf",
    date(2020, 11, 3): "ActualResults-November32020.pdf",
    date(2022, 11, 8): "ActualResults-November82022.pdf",
    date(2024, 11, 5): "ActualResults-November52024.pdf",
}
PUBLISHER = "Missouri Secretary of State"
TITLE = "Official election results"
TITLE_OFFICE = "Justice of the Supreme Court"
ROW = re.compile(r"^([A-Za-z][A-Za-z.'\- ]*?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$")
METHOD = (
    "Under the Missouri Plan a judge keeps the seat when a majority votes Yes in the retention election, "
    "with Yes and No as the only answers."
)
NOTE = (
    "County totals from the Missouri Secretary of State's official results for the general election; "
    "the Kansas City Board of Election Commissioners' line is added to Jackson County."
)


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def fetch() -> None:
    for name in FILES.values():
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def halve(text: str) -> str:
    words = text.split()
    half = len(words) // 2
    if len(words) % 2 == 0 and words[:half] == words[half:]:
        return " ".join(words[:half])
    return text


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day, name in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        state = "idle"
        buffer: list[str] = []
        judge = ""
        for line in text.splitlines():
            stripped = line.strip()
            if "Missouri Supreme Court" in stripped:
                state, buffer, judge = "name", [], ""
                continue
            if state == "name":
                if not stripped:
                    continue
                head = re.split(r"\s{2,}", stripped)[0]
                if re.search(r"\bYES\b", stripped):
                    before = re.split(r"\s+YES\b", stripped)[0].strip()
                    judge = " ".join(filter(None, [*buffer, before]))
                    state = "rows"
                elif re.match(r"Yes votes", stripped):
                    judge = " ".join(buffer)
                    state = "rows"
                else:
                    buffer.append(halve(head) if re.search(r"\s{2,}", stripped) or halve(stripped) != stripped else stripped)
                continue
            if state == "rows":
                match = ROW.match(stripped)
                if match is None:
                    state = "idle"
                    continue
                if match[1] == "Total":
                    continue
                found.append(
                    {
                        "day": day,
                        "judge": re.sub(r"\s+", " ", judge).strip(),
                        "county": match[1].strip(),
                        "yes": int(match[2].replace(",", "")),
                        "no": int(match[3].replace(",", "")),
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
            summary="Missouri Supreme Court retention elections 2012–2024 by county, from the Secretary of State's official results",
            polity="US-MO",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.mo.gov/elections/results",
            license="Public record",
        )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        counties = {
            ("stlouiscity" if code == "29510" else flat(text)): unit
            for text, unit, code in conn.execute(
                """select n.text, u.id, u.code from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-MO'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/mo/%%'").fetchall()}
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

        office_key = f"{slug(TITLE_OFFICE)}:MO"
        office = cs.key("us.office", office_key)
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-MO"), kind="other")
            cs.add("name", entity_id=office, text=f"{TITLE_OFFICE} of Missouri", lang="en", kind="official", is_preferred=True)
        groups: dict[tuple[date, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], str(row["judge"]))].append(row)
        persons: set[str] = set()
        made = 0
        for (day, judge), group in sorted(groups.items()):
            first, last = identity(judge)
            person_key = f"MO:{slug(first)}-{slug(last)}"
            suffix = person_key.split(":")[1]
            shown = display(judge)
            contest_key = f"{day}:MO:{slug(TITLE_OFFICE)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:MO")
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
            name(contest, f"{day.year} Missouri {TITLE_OFFICE} retention: {shown}")
            if contest in cs.minted:
                base = f"us/mo/{day}-general/{slug(TITLE_OFFICE)}-{suffix}"
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
            yes = sum(int(row["yes"]) for row in group)
            no = sum(int(row["no"]) for row in group)
            retained = yes > no
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options["yes"],
                candidacy_id=candidacy,
                kind="elected" if retained else "lost",
                seats=1 if retained else None,
            )
            by_unit: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for row in group:
                place = "Jackson" if row["county"] == "Kansas City" else str(row["county"])
                by_unit[str(counties[flat(place)])][0] += int(row["yes"])
                by_unit[str(counties[flat(place)])][1] += int(row["no"])
            values = []
            for unit, (yes_votes, no_votes) in by_unit.items():
                values.append((str(result), unit, str(options["yes"]), 0, 1, 0, 0, yes_votes))
                values.append((str(result), unit, str(options["no"]), 0, 1, 0, 0, no_votes))
                values.append((str(result), unit, None, 0, 4, 0, 0, yes_votes + no_votes))
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
            made += 1
            print(contest_key, yes, no, len(group))
        print(made)
        return cs.write()
