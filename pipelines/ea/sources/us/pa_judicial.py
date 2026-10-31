import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import DATA, connect

SOURCE = "us.pa_judicial"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-451"
URL = "https://www.electionreturns.pa.gov/Home/ReportCenter"
PUBLISHER = "Pennsylvania Department of State"
TITLE = "Election returns by county: municipal election judicial contests"
YEARS = {
    2017: date(2017, 11, 7),
    2019: date(2019, 11, 5),
    2021: date(2021, 11, 2),
    2023: date(2023, 11, 7),
    2025: date(2025, 11, 4),
}
COURTS = {
    "Justice of the Supreme Court": "Justice of the Supreme Court",
    "Judge of the Superior Court": "Judge of the Superior Court",
    "Judge of the Commonwealth Court": "Judge of the Commonwealth Court",
}
PARTIES = {"Democratic": "democratic-party", "Republican": "republican-party", "Green": "green-party"}
MAJOR = ("Democratic", "Republican")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when a majority votes Yes."
)
NOTE = "County totals from the Pennsylvania Department of State's official county returns export for the municipal election."
SEATS = (
    "Voters may choose up to as many candidates as there are seats; the seats go to the highest vote-getters statewide, "
    "regardless of party."
)


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def sheet(path: Path) -> list[dict[str, str]]:
    archive = zipfile.ZipFile(path)
    strings = [
        "".join(t.text or "" for t in si.iter(NS + "t"))
        for si in ElementTree.fromstring(archive.read("xl/sharedStrings.xml").decode("utf-8-sig")).iter(NS + "si")
    ]
    table = []
    for row in ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml")).iter(NS + "row"):
        cells = {}
        for cell in row.iter(NS + "c"):
            value = cell.find(NS + "v")
            if value is not None:
                column = re.match(r"[A-Z]+", cell.get("r"))[0]
                cells[column] = strings[int(value.text)] if cell.get("t") == "s" else value.text
        table.append(cells)
    return table


def number(text: str | None) -> int:
    return int((text or "0").replace(",", ""))


def fetch() -> None:
    for year in YEARS:
        path = next((INBOX / str(year)).glob("*.xlsx"))
        try:
            raw.cached(SOURCE, f"{URL}#{year}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{year}",
                path.read_bytes(),
                suffix=".xlsx",
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for year, day in YEARS.items():
        for cells in sheet(raw.cached(SOURCE, f"{URL}#{year}").path)[1:]:
            office = cells["C"].replace(" (Retention)", "")
            retention = cells["C"].endswith("(Retention)")
            found.append(
                {
                    "day": day,
                    "office": COURTS[office],
                    "retention": retention,
                    "candidate": re.sub(r"\s+", " ", cells["F"]).strip(),
                    "party": cells.get("E", ""),
                    "county": cells["B"],
                    "votes": number(cells.get("G")),
                    "yes": number(cells.get("H")),
                    "no": number(cells.get("I")),
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
            summary="Pennsylvania statewide judicial elections 2017–2025 by county, from the Department of State's official county returns",
            polity="US-PA",
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
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, f"{URL}#{year}"), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '42%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-PA'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/pa/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")
        block = cs.key("us.electoral_system", "block")
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

        def office_for(title: str) -> UUID:
            office = cs.key("us.office", f"{slug(title)}:PA")
            if office not in offices:
                offices.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=cs.polity("US-PA"), kind="other")
                    cs.add(
                        "name",
                        entity_id=office,
                        text=f"{title} of Pennsylvania",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            return office

        def person_for(choice: str) -> UUID:
            first, last = identity(choice)
            person_key = f"PA:{slug(first)}-{slug(last)}"
            person = cs.key("us.retention.person", person_key)
            if person_key not in persons:
                persons.add(person_key)
                if person in cs.minted:
                    cs.add("person", id=person)
                name(person, display(choice))
            return person

        def open_contest(
            day: date, title: str, suffix: str, label: str, system: UUID, seats: int
        ) -> tuple[UUID, UUID, str]:
            contest_key = f"{day}:PA:{slug(title)}{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:PA")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_for(title),
                district_area_id=state_area,
                term="full",
                magnitude=seats,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Pennsylvania {label}")
            if contest in cs.minted:
                base = f"us/pa/{day}-local/{slug(title)}{suffix}"
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
            return contest, result, contest_key

        def values_frame(values: list[tuple]) -> None:
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

        groups: dict[tuple[date, str, bool], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], str(row["office"]), bool(row["retention"]))].append(row)
        made = 0
        for (day, title, retention), group in sorted(groups.items()):
            if retention:
                by_judge: dict[str, list[dict[str, object]]] = defaultdict(list)
                for row in group:
                    by_judge[str(row["candidate"])].append(row)
                for judge, judge_rows in by_judge.items():
                    shown = display(judge)
                    first, last = identity(judge)
                    suffix = f"-retention-{slug(first)}-{slug(last)}"
                    contest, result, contest_key = open_contest(
                        day, title, suffix, f"{title} retention: {shown}", retention_system, 1
                    )
                    cs.add("note", entity_id=result, kind="methodology", body_md=f"{METHOD} {NOTE}")
                    options = {}
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
                        person_id=person_for(judge),
                        option_id=options["yes"],
                        role="head",
                        ballot_name=shown,
                        is_independent=False,
                    )
                    by_unit: dict[str, dict[str, int]] = defaultdict(lambda: {"yes": 0, "no": 0})
                    for row in judge_rows:
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
                    values_frame(values)
                    made += 1
                    print(contest_key, yes, no)
                continue
            parties: dict[str, str] = {}
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for row in group:
                by_choice[str(row["candidate"])][str(row["county"])] += int(row["votes"])
                parties[str(row["candidate"])] = str(row["party"])
            seats = max(sum(1 for party in parties.values() if party == major) for major in MAJOR)
            contest, result, contest_key = open_contest(day, title, "", title, fptp if seats == 1 else block, seats)
            cs.add("note", entity_id=result, kind="methodology", body_md=f"{NOTE}" + (f" {SEATS}" if seats > 1 else ""))
            totals = {choice: sum(by_unit.values()) for choice, by_unit in by_choice.items()}
            ranked = sorted(totals, key=lambda choice: -totals[choice])
            options = {}
            candidacies = {}
            for position, choice in enumerate(by_choice):
                party = PARTIES.get(parties[choice])
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "contest_option",
                    id=options[choice],
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_position=position + 1,
                    ballot_label=display(choice),
                )
                candidacies[choice] = cs.key("us.candidacy", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "candidacy",
                    id=candidacies[choice],
                    person_id=person_for(choice),
                    option_id=options[choice],
                    role="head",
                    ballot_name=display(choice),
                    is_independent=False,
                )
            for choice in ranked[:seats]:
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options[choice],
                    candidacy_id=candidacies[choice],
                    kind="elected",
                    seats=1,
                )
            by_place: dict[str, int] = defaultdict(int)
            values = []
            for choice, by_unit in by_choice.items():
                for place, votes in by_unit.items():
                    unit = str(counties[flat(place)])
                    by_place[unit] += votes
                    values.append((str(result), unit, str(options[choice]), 0, 1, 0, 0, votes))
            values += [(str(result), unit, None, 0, 4, 0, 0, votes) for unit, votes in by_place.items()]
            values_frame(values)
            made += 1
            print(contest_key, seats, {choice: totals[choice] for choice in ranked})
        print(made, file=sys.stderr)
        return cs.write()
