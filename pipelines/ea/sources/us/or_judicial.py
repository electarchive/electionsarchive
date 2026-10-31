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

SOURCE = "us.or_judicial"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-456"
URL = "https://sos.oregon.gov/elections/pages/historical-data.aspx"
PUBLISHER = "Oregon Secretary of State"
TITLE = "Abstract of votes: Supreme Court and Court of Appeals"
FILES = {
    "2010 General Election Official Results.PDF-(Preview).pdf": (date(2010, 11, 2), "general"),
    "2012 General Election Official Results.PDF-(Preview).pdf": (date(2012, 11, 6), "general"),
    "2014 Primary Election Official Results.PDF-(Preview).pdf": (date(2014, 5, 20), "primary"),
    "2018 Primary Election Official Results.PDF-(Preview).pdf": (date(2018, 5, 15), "primary"),
    "2018 General Election Official Results.PDF-(Preview).pdf": (date(2018, 11, 6), "general"),
    "2020 May Primary Election Official Results(2).PDF-(Preview).pdf": (date(2020, 5, 19), "primary"),
    "2020 November General Election Official Results(2).PDF-(Preview).pdf": (date(2020, 11, 3), "general"),
    "2022 May Primary Election Official Results(2).PDF-(Preview).pdf": (date(2022, 5, 17), "primary"),
    "2022 November General Election Official Results(2).PDF-(Preview).pdf": (date(2022, 11, 8), "general"),
    "2024 May Primary Election Official Results(2).PDF-(Preview).pdf": (date(2024, 5, 21), "primary"),
    "2024 November General Election Official Results(2).PDF-(Preview).pdf": (date(2024, 11, 5), "general"),
}
TITLES = {"supreme court": "Justice of the Supreme Court", "court of appeals": "Judge of the Court of Appeals"}
HEAD = re.compile(r"^\s*Judge of the (Supreme Court|Court of Appeals)(?:,\s*Position\s+(\d+))?\s*$", re.IGNORECASE)
POSITION = re.compile(r"^\s*Position\s+(\d+)\s*$", re.IGNORECASE)
ROW = re.compile(r"^\s*([A-Za-z][A-Za-z .'-]*?)\s{2,}((?:[\d,]+\s*)+)$")
NOTE = (
    "County totals from the Oregon Secretary of State's official abstract of votes. The abstract marks the winner; "
    "a candidate with a majority in the May primary wins the seat outright, and otherwise the top two meet in November."
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


def split(text: str) -> list[str]:
    return [part for part in re.split(r"\s{2,}", text.strip()) if part]


def contests(path: Path, swapped: bool) -> list[dict[str, object]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        head = HEAD.match(line)
        if head is None:
            continue
        position = head[2]
        cursor = index + 1
        while not lines[cursor].strip().startswith("County"):
            match = POSITION.match(lines[cursor])
            if match is not None:
                position = match[1]
            cursor += 1
        above = split(lines[cursor - 1])
        below = split(lines[cursor].strip()[len("County") :])
        if len(above) != len(below) + 1 or not above[-1].startswith("Misc"):
            raise ValueError(f"{path.name} {index}: {above} {below}")
        candidates = [
            {
                "name": f"{given.lstrip('*')} {family}" if swapped else f"{below[i]} {above[i].lstrip('*')}",
                "elected": above[i].startswith("**"),
            }
            for i, (given, family) in enumerate(zip(above, below, strict=False))
        ]
        candidates.append({"name": "Misc", "elected": False})
        rows: dict[str, list[int]] = {}
        cursor += 1
        while not lines[cursor].strip():
            cursor += 1
        while True:
            row = ROW.match(lines[cursor])
            if row is None:
                break
            if row[1].strip().lower() == "total":
                break
            rows[row[1].strip()] = [int(value.replace(",", "")) for value in row[2].split()]
            cursor += 1
        found.append(
            {
                "court": TITLES[head[1].lower()],
                "position": int(position),
                "candidates": candidates,
                "rows": rows,
            }
        )
    return found


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon Supreme Court and Court of Appeals elections 2010–2024 by county, "
            "from the Secretary of State's abstracts of votes",
            polity="US-OR",
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
                   where u.scheme_code = 'us.county' and u.code like '41%%'"""
            ).fetchall()
        }
        state_area, polity = conn.execute(
            """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-OR'"""
        ).fetchone()
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/or/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")

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
        made = 0
        for file, (day, stage) in FILES.items():
            for block in contests(raw.cached(SOURCE, f"{URL}#{file}").path, day.year == 2014):
                title = str(block["court"])
                position = int(block["position"])
                office = cs.key("us.office", f"{slug(title)}:OR")
                if office not in offices:
                    offices.add(office)
                    if office in cs.minted:
                        cs.add("office", id=office, polity_id=polity, kind="other")
                        cs.add(
                            "name",
                            entity_id=office,
                            text=f"{title} of Oregon",
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                election = cs.key("us.election", f"{day}:OR" if stage == "general" else f"{day}:OR:primary")
                contest_key = f"{day}:OR:{slug(title)}:position-{position}"
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
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} Oregon {title}, Position {position}")
                if contest in cs.minted:
                    base = f"us/or/{day}-{stage}/{slug(title)}-position-{position}"
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
                options: list[UUID] = []
                values = []
                by_place: dict[str, int] = defaultdict(int)
                rows = block["rows"]
                for column, candidate in enumerate(block["candidates"]):
                    label = str(candidate["name"])
                    option = cs.key("us.option", f"{contest_key}:{slug(label)}")
                    options.append(option)
                    if label == "Misc":
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="write_in",
                            ballot_position=column + 1,
                            ballot_label="Write-in",
                        )
                    else:
                        first, last = identity(label)
                        person_key = f"OR:{slug(first)}-{slug(last)}"
                        person = cs.key("us.retention.person", person_key)
                        if person_key not in persons:
                            persons.add(person_key)
                            if person in cs.minted:
                                cs.add("person", id=person)
                            name(person, display(label))
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            ballot_position=column + 1,
                            ballot_label=display(label),
                        )
                        candidacy = cs.key("us.candidacy", f"{contest_key}:{slug(label)}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=display(label),
                            is_independent=False,
                        )
                        if candidate["elected"]:
                            cs.add(
                                "outcome",
                                result_set_id=result,
                                option_id=option,
                                candidacy_id=candidacy,
                                kind="elected",
                                seats=1,
                            )
                    for county, numbers in rows.items():
                        unit = str(counties[flat(county)])
                        values.append((str(result), unit, str(option), 0, 1, 0, 0, numbers[column]))
                        by_place[unit] += numbers[column]
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
                made += 1
                print(contest_key, len(rows), sum(by_place.values()))
        print(made)
        return cs.write()
