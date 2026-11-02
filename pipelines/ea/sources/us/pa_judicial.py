import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import DATA, connect
from ea.typed import one, present, required

SOURCE = "us.pa_judicial"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-451"
URL = "https://www.electionreturns.pa.gov/Home/ReportCenter"
PUBLISHER = "Pennsylvania Department of State"
TITLE = "Election returns by county: municipal election judicial contests"
YEARS = {
    2001: date(2001, 11, 6),
    2003: date(2003, 11, 4),
    2005: date(2005, 11, 8),
    2007: date(2007, 11, 6),
    2009: date(2009, 11, 3),
    2011: date(2011, 11, 8),
    2015: date(2015, 11, 3),
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
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Green": "green-party",
    "Libertarian": "libertarian-party",
}
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


SUPREME = "Justice of the Supreme Court"
SUPERIOR = "Judge of the Superior Court"
COMMONWEALTH = "Judge of the Commonwealth Court"
NAMED: dict[tuple[int, str, str], list[tuple[str, int, int]]] = {
    (2001, SUPREME, "Democratic"): [("Kate Ford Elliott", 954053, 0)],
    (2001, SUPREME, "Republican"): [("Michael Eakin", 1051360, 0)],
    (2001, SUPREME, "Retention"): [("Stephen Zappala Sr", 978709, 320175)],
    (2001, SUPERIOR, "Retention"): [("Stephen J. McEwen Jr", 909377, 302669)],
    (2001, COMMONWEALTH, "Retention"): [("Shelly Friedman", 890799, 317068)],
    (2003, SUPREME, "Democratic"): [("Max Baer", 1284846, 0)],
    (2003, SUPREME, "Republican"): [("Joan Orie Melvin", 1192952, 0)],
    (2005, SUPREME, "Retention"): [
        ("Sandra Schultz Newman", 797465, 676848),
        ("Russell M. Nigro", 706251, 736036),
    ],
    (2007, SUPREME, "Republican"): [("Maureen Lally-Green", 994760, 0), ("Mike Krancer", 783635, 0)],
    (2007, SUPREME, "Retention"): [("Thomas G. Saylor", 1184546, 586330)],
    (2009, SUPREME, "Democratic"): [("Jack Panella", 815106, 0)],
    (2009, SUPREME, "Republican"): [("Joan Orie Melvin", 925459, 0)],
    (2009, SUPERIOR, "Libertarian"): [("Marakay J. Rogers", 127492, 0)],
    (2009, SUPERIOR, "Retention"): [("Kate Ford Elliott", 973175, 439272)],
    (2009, COMMONWEALTH, "Retention"): [("Dan Pellegrini", 948784, 461799)],
    (2011, SUPERIOR, "Democratic"): [("David Wecht", 1030004, 0)],
    (2011, SUPERIOR, "Republican"): [("Vic Stabile", 859484, 0)],
    (2011, COMMONWEALTH, "Democratic"): [("Kathryn Boockvar", 890831, 0)],
    (2011, COMMONWEALTH, "Republican"): [("Anne Covey", 968796, 0)],
    (2011, SUPREME, "Retention"): [("Michael Eakin", 1097368, 393728)],
    (2015, SUPREME, "Democratic"): [
        ("Kevin M. Dougherty", 1088716, 0),
        ("David Wecht", 1079452, 0),
        ("Christine Donohue", 1069161, 0),
    ],
    (2015, SUPREME, "Republican"): [
        ("Judith Ference Olson", 895741, 0),
        ("Michael George", 803748, 0),
        ("Anne Covey", 802891, 0),
    ],
    (2015, SUPREME, "Independent Judicial Alliance"): [("Paul Panepinto", 145190, 0)],
    (2015, SUPERIOR, "Democratic"): [("Alice Beck Dubow", 1058753, 0)],
    (2015, SUPERIOR, "Republican"): [("Emil Giordano", 933691, 0)],
    (2015, COMMONWEALTH, "Democratic"): [("Michael H. Wojcik", 1060314, 0)],
    (2015, COMMONWEALTH, "Republican"): [("Paul Lalley", 946079, 0)],
}


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
                column = required(re.match(r"[A-Z]+", cell.get("r") or ""))[0]
                text = present(value.text, f"the value of cell {column}")
                cells[column] = strings[int(text)] if cell.get("t") == "s" else text
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


def attributed(year: int, table: list[dict[str, str]]) -> list[dict[str, Any]]:
    slots: dict[tuple[str, str, str], int] = defaultdict(int)
    rows: list[tuple[tuple[str, str, int], dict[str, str]]] = []
    for cells in table[1:]:
        group = (cells["C"], cells.get("E", ""))
        county = (cells["B"],) + group
        rows.append(((*group, slots[county]), cells))
        slots[county] += 1
    sums: dict[tuple[str, str, int], list[int]] = defaultdict(lambda: [0, 0, 0])
    for slot, cells in rows:
        total = sums[slot]
        total[0] += number(cells.get("G"))
        total[1] += number(cells.get("H"))
        total[2] += number(cells.get("I"))
    chosen: dict[tuple[str, str, int], str] = {}
    groups: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for slot in sums:
        groups[slot[:2]].append(slot)
    for (office, party), members in groups.items():
        retention = office.endswith("(Retention)")
        title = office.replace(" (Retention)", "")
        names = NAMED.get((year, title, "Retention" if retention else party), [])
        if len(names) != len(members):
            print(f"skip {year} {office} {party}: {len(members)} slots, {len(names)} named", file=sys.stderr)
            continue
        pairs = {}
        for name, votes, against in names:
            ranked = sorted(
                members,
                key=lambda slot: abs((sums[slot][1] if retention else sums[slot][0]) - votes),
            )
            best = ranked[0]
            got = sums[best][1] if retention else sums[best][0]
            close = abs(got - votes) <= votes * 0.001
            if retention and against:
                close = close and abs(sums[best][2] - against) <= against * 0.01
            if close and best not in pairs:
                pairs[best] = name
        if len(pairs) != len(names):
            print(f"skip {year} {office} {party}: totals do not match the named candidates", file=sys.stderr)
            continue
        chosen.update(pairs)
    whole = {
        office
        for office, party in groups
        if not office.endswith("(Retention)")
        and all(
            slot in chosen for other, other_party in groups if other == office for slot in groups[(other, other_party)]
        )
    }
    found: list[dict[str, Any]] = []
    for slot, cells in rows:
        if slot not in chosen or (not slot[0].endswith("(Retention)") and slot[0] not in whole):
            continue
        found.append(
            {
                "day": YEARS[year],
                "office": COURTS[cells["C"].replace(" (Retention)", "")],
                "retention": cells["C"].endswith("(Retention)"),
                "candidate": chosen[slot],
                "party": cells.get("E", ""),
                "county": cells["B"],
                "votes": number(cells.get("G")),
                "yes": number(cells.get("H")),
                "no": number(cells.get("I")),
            }
        )
    return found


def lines() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for year, day in YEARS.items():
        table = sheet(raw.cached(SOURCE, f"{URL}#{year}").path)
        if year < 2017:
            found += attributed(year, table)
            continue
        for cells in table[1:]:
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
            summary="Pennsylvania statewide judicial elections 2001–2025 by county, from the Department of State's official county returns",
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
        state_area = one(
            conn.execute(
                """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-PA'"""
            )
        )[0]
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

        groups: dict[tuple[date, str, bool], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], str(row["office"]), bool(row["retention"]))].append(row)
        made = 0
        for (day, title, retention), group in sorted(groups.items()):
            if retention:
                by_judge: dict[str, list[dict[str, Any]]] = defaultdict(list)
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
                        candidacy_id=None,
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
                    candidacy_id=None,
                    kind="elected",
                    seats=1,
                )
            by_place: dict[str, int] = defaultdict(int)
            values: list[tuple[str, str, str | None, int, int, int, int, int]] = []
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
