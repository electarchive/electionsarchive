import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import date
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.wy_judicial"
VERSION = "1"
SITE = "https://sos.wyo.gov/Elections/Docs/"
PUBLISHER = "Wyoming Secretary of State"
TITLE = "Statewide judicial retention summaries by county"
FILES = {
    date(2000, 11, 7): "2000/00Results/00General/00_General_SW_Candidate_Judicial_Issues.pdf",
    date(2002, 11, 5): "2002/02Results/02General/02_General%20Election_SW_Candidates.pdf",
    date(2006, 11, 7): "2006/06Results/06General/SW_Retention_Summary.pdf",
    date(2008, 11, 4): "2008/08Results/General/SW-JudicialRetentions.pdf",
    date(2010, 11, 2): "2010/Results/General/2010_Statewide_Judicial_Summary.pdf",
    date(2014, 11, 4): "2014/Results/General/2014_Statewide_Judicial_Summary.pdf",
    date(2016, 11, 8): "2016/Results/General/2016_General_Statewide_Judicial_Summary.pdf",
}
FIXED = {
    date(2000, 11, 7): [("William U. Hill", 0)],
    date(2002, 11, 5): [("Marilyn S. Kite", 2), ("Barton Voigt", 4)],
}
TITLE_OFFICE = "Justice of the Supreme Court"
METHOD = (
    "A retention election asks whether a sitting justice should stay in office, with Yes and No as the only answers. "
    "The justice keeps the seat when a majority votes Yes."
)
NOTE = (
    "County totals from the Wyoming Secretary of State's official statewide summary for the general election. "
    "Rows for undervotes and overvotes are left out, so the set holds only the Yes and No votes."
)
DASHES = "-‐‑‒–"
NUMBER = re.compile(rf"^[\d,]+$|^[{DASHES}]$|^NA$")


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix=".pdf")


def number(text: str) -> int:
    return 0 if text[0] in DASHES or text == "NA" else int(text.replace(",", ""))


def row(line: str) -> tuple[str, list[int]] | None:
    parts = re.split(r"\s{2,}", line.strip())
    if len(parts) < 2 or not parts[0] or parts[0][0].isdigit():
        return None
    values = " ".join(parts[1:]).split()
    if not values or not all(NUMBER.match(value) for value in values):
        return None
    return parts[0], [number(value) for value in values]


def table(lines: list[str], start: int) -> tuple[dict[str, list[int]], list[int]]:
    counties: dict[str, list[int]] = {}
    for index in range(start, len(lines)):
        text = lines[index].strip()
        if re.match(r"^(Official\s+)?Total", text):
            parsed = row(re.sub(r"^(Official\s+)?Total", "Total", text, count=1))
            return counties, parsed[1] if parsed is not None else []
        found = row(lines[index])
        if found is not None:
            counties[found[0]] = found[1]
    return counties, []


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day, path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, SITE + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if day in FIXED:
            start = next(i for i, line in enumerate(text) if "Supreme Court" in line)
            counties, totals = table(text, start)
            people = FIXED[day]
        else:
            people = []
            counties, totals = {}, []
            starts = [i for i, line in enumerate(text) if TITLE_OFFICE in line]
            blocks = []
            for start in starts:
                at = next(i for i in range(start + 1, len(text)) if text[i].strip())
                names = re.split(r"\s{2,}", text[at].strip())
                supreme = text[start].count(TITLE_OFFICE)
                columns, total = table(text, at + 1)
                blocks.append((names, supreme, columns, total))
            for names, supreme, columns, total in blocks:
                stride = len(total) // len(names)
                for position, name in enumerate(names[:supreme]):
                    found.extend(
                        {
                            "day": day,
                            "judge": name,
                            "county": county,
                            "yes": values[position * stride],
                            "no": values[position * stride + 1],
                        }
                        for county, values in columns.items()
                    )
                    sums = [sum(values[position * stride + k] for values in columns.values()) for k in (0, 1)]
                    assert sums == total[position * stride : position * stride + 2], (day, name, sums, total)
            continue
        for name, offset in people:
            found.extend(
                {"day": day, "judge": name, "county": county, "yes": values[offset], "no": values[offset + 1]}
                for county, values in counties.items()
            )
            sums = [sum(values[offset + k] for values in counties.values()) for k in (0, 1)]
            assert sums == totals[offset : offset + 2], (day, name, sums, totals)
    return found


def emit():
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wyoming Supreme Court justice retention elections 2000–2016 by county, "
            "from the Secretary of State's official statewide summaries",
            polity="US-WY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.wyo.gov/Elections/ElectionHistory.aspx",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '56%%'"""
            ).fetchall()
        }
        state_area, polity = conn.execute(
            """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-WY'"""
        ).fetchone()
        retention_system = cs.key("us.electoral_system", "retention")
        office = cs.key("us.office", f"{slug(TITLE_OFFICE)}:WY")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=polity, kind="other")
            cs.add(
                "name",
                entity_id=office,
                text=f"{TITLE_OFFICE} of Wyoming",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        persons: set[str] = set()
        groups: dict[tuple[date, str], list[dict[str, object]]] = defaultdict(list)
        for found in rows:
            groups[(found["day"], str(found["judge"]))].append(found)
        for (day, judge), group in sorted(groups.items()):
            first, last = identity(judge)
            person_key = f"WY:{slug(first)}-{slug(last)}"
            suffix = f"{slug(first)}-{slug(last)}"
            shown = display(judge)
            contest_key = f"{day}:WY:{slug(TITLE_OFFICE)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:WY")
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
            title = f"{day.year} Wyoming {TITLE_OFFICE} retention: {shown}"
            cs.add("name", entity_id=contest, text=title, lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"us/wy/{day}-general/{slug(TITLE_OFFICE)}-{suffix}", entity_id=contest)
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
            by_unit: dict[str, dict[str, int]] = defaultdict(lambda: {"yes": 0, "no": 0})
            for found in group:
                unit = str(counties[flat(str(found["county"]))])
                by_unit[unit]["yes"] += int(found["yes"])
                by_unit[unit]["no"] += int(found["no"])
            yes = sum(counts["yes"] for counts in by_unit.values())
            no = sum(counts["no"] for counts in by_unit.values())
            retained = yes > no
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options["yes"],
                candidacy_id=None,
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
