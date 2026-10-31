import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.ut_judicial"
VERSION = "1"
PUBLISHER = "Utah Lieutenant Governor's Office of Elections"
TITLE = "Official canvass by county"
TITLE_OFFICE = "Justice of the Supreme Court"
SOS = "us.ut_sos"
CANVASS = "us.measure_canvass"
WAYBACK = "https://web.archive.org/web/{}id_/{}"
PDFS = {
    date(2004, 11, 2): (
        SOS,
        WAYBACK.format(
            "20110927154944", "http://elections.utah.gov/Media/Default/Documents/Election_Results/General/2004Gen.pdf"
        ),
        ["Christine M. Durham", "Matthew B. Durrant"],
    ),
    date(2006, 11, 7): (
        SOS,
        WAYBACK.format(
            "20110927154942", "http://elections.utah.gov/Media/Default/Documents/Election_Results/General/2006Gen.pdf"
        ),
        ["Ronald E. Nehring", "Jill N. Parrish"],
    ),
}
SHEETS = {
    date(2014, 11, 4): (
        WAYBACK.format(
            "20151220071902",
            "http://elections.utah.gov/Media/Default/2014%20Election/2014%20Final%20General%20Canvass%20Report.xlsx",
        ),
        [("Christine M. Durham", 4), ("Matthew B. Durrant", 6), ("Thomas R. Lee", 8)],
    ),
    date(2018, 11, 6): (
        WAYBACK.format(
            "2025", "https://elections.utah.gov/Media/Default/2018%20Election/2018%20General%20Election%20Canvass.xlsx"
        ),
        [("Constandinos Himonas", 1)],
    ),
    date(2020, 11, 3): (
        WAYBACK.format(
            "2025", "https://vote.utah.gov/wp-content/uploads/2021/02/2020-General-Election-Statewide-Canvass.xlsx"
        ),
        [("John A. Pearce", 1)],
    ),
}
COUNTY_NAMES = "Beaver,Box Elder,Cache,Carbon,Daggett,Davis,Duchesne,Emery,Garfield,Grand,Iron,Juab,Kane,Millard,Morgan,Piute,Rich,Salt Lake,San Juan,Sanpete,Sevier,Summit,Tooele,Uintah,Utah,Wasatch,Washington,Wayne,Weber"
ROW = re.compile(
    r"^([A-Z][A-Za-z ]*?)\s+([\d,]+)\s+([\d,]+)\s+(?:[\d.]+%\s+)?([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)"
)
METHOD = (
    "A Utah judge keeps the seat when a majority votes Yes in the retention election, "
    "with Yes and No as the only answers."
)
NOTE = "County totals from the Utah Lieutenant Governor's official canvass of the general election."


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


COUNTIES = {flat(name) for name in COUNTY_NAMES.split(",")}


def number(text: object) -> int:
    return int(str(text).replace(",", ""))


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day, (source, url, judges) in PDFS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(source, url).path), "-"], check=True, capture_output=True, text=True
        ).stdout
        text = text[text.index("Supreme Court") :]
        seen: set[str] = set()
        for line in text.splitlines():
            match = ROW.match(line.strip())
            if match is None or match[1] in seen or match[1] in ("Total", "TOTAL"):
                continue
            seen.add(match[1])
            values = [number(match[i]) for i in range(2, 8)]
            for position, judge in enumerate(judges):
                found.append(
                    {
                        "day": day,
                        "judge": judge,
                        "county": match[1],
                        "yes": values[2 + 2 * position],
                        "no": values[3 + 2 * position],
                    }
                )
        if len(seen) != 29:
            raise ValueError(f"{day}: {len(seen)} counties")
    for day, (url, judges) in SHEETS.items():
        frame = (
            fastexcel.read_excel(str(raw.cached(CANVASS, url).path))
            .load_sheet_by_name("Judicial", header_row=None)
            .to_polars()
        )
        count = 0
        for row in frame.iter_rows():
            county = str(row[0]).strip()
            if county in ("None", "County", "COUNTY", "TOTAL", "Total", "") or flat(county) not in COUNTIES:
                continue
            count += 1
            for judge, column in judges:
                found.append(
                    {
                        "day": day,
                        "judge": judge,
                        "county": county,
                        "yes": number(row[column]),
                        "no": number(row[column + 1]),
                    }
                )
        if count != 29:
            raise ValueError(f"{day}: {count} counties")
    return found


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah Supreme Court retention elections 2004–2020 by county, from the Lieutenant Governor's official canvasses",
            polity="US-UT",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://vote.utah.gov/historical-election-results/",
            license="Public record",
        )
        for artifact_source, url, _ in PDFS.values():
            cs.artifact(raw.cached(artifact_source, url), source)
        for url, _ in SHEETS.values():
            cs.artifact(raw.cached(CANVASS, url), source)
        counties = {
            flat(text): unit
            for text, unit, _ in conn.execute(
                """select n.text, u.id, u.code from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '49%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-UT'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ut/%%'").fetchall()}
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

        office_key = f"{slug(TITLE_OFFICE)}:UT"
        office = cs.key("us.office", office_key)
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-UT"), kind="other")
            cs.add(
                "name",
                entity_id=office,
                text=f"{TITLE_OFFICE} of Utah",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        groups: dict[tuple[date, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], str(row["judge"]))].append(row)
        persons: set[str] = set()
        made = 0
        for (day, judge), group in sorted(groups.items()):
            first, last = identity(judge)
            person_key = f"UT:{slug(first)}-{slug(last)}"
            suffix = person_key.split(":")[1]
            shown = display(judge)
            contest_key = f"{day}:UT:{slug(TITLE_OFFICE)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:UT")
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
            name(contest, f"{day.year} Utah {TITLE_OFFICE} retention: {shown}")
            if contest in cs.minted:
                base = f"us/ut/{day}-general/{slug(TITLE_OFFICE)}-{suffix}"
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
                place = str(row["county"])
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
