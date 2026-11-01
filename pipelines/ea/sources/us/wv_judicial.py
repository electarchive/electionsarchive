import csv
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import DATA, connect

SOURCE = "us.wv_judicial"
VERSION = "1"
URL = "https://apps.sos.wv.gov/elections/results/readfile.aspx?eid={eid}&type=StateCountyTotals&format=csv"
PUBLISHER = "West Virginia Secretary of State"
TITLE = "Election results center"
TITLE_OFFICE = "Justice of the Supreme Court"
ELECTIONS = {
    date(2008, 11, 4): (4, f"{date(2008, 11, 4)}:WV", "general", 2),
    date(2010, 11, 2): (6, f"{date(2010, 11, 2)}:WV", "general", 1),
    date(2012, 11, 6): (13, f"{date(2012, 11, 6)}:WV", "general", 2),
    date(2016, 5, 10): (22, f"{date(2016, 5, 10)}:WV:primary", "primary", 1),
}
INBOX = DATA / "inbox" / "manual" / "MF-565"
CLARITY_URL = "https://sos.wv.gov/elections/Pages/ElectionResults.aspx#{day}"
CLARITY = {
    date(2018, 11, 6): (f"{date(2018, 11, 6)}:WV", "general", "2018-general/detailxls (1).zip"),
    date(2020, 6, 9): (f"{date(2020, 6, 9)}:WV:primary", "primary", "2020/detailxls (2).zip"),
    date(2024, 5, 14): (f"{date(2024, 5, 14)}:WV:primary", "primary", "2024/detailxls (3).zip"),
}
SHEET = "{urn:schemas-microsoft-com:office:spreadsheet}"
PARTIES = {"D": "democratic-party", "R": "republican-party"}
NOTE = (
    "County totals from the West Virginia Secretary of State's Election Results Center, state and county totals file."
)


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def fetch() -> None:
    for eid, *_ in ELECTIONS.values():
        raw.fetch(SOURCE, URL.format(eid=eid), suffix=".csv")
    for day, (_, _, file) in CLARITY.items():
        try:
            raw.cached(SOURCE, CLARITY_URL.format(day=day))
        except LookupError:
            raw.store(
                SOURCE,
                CLARITY_URL.format(day=day),
                (INBOX / file).read_bytes(),
                suffix=".zip",
                retrieved_at=datetime.fromtimestamp((INBOX / file).stat().st_mtime, UTC),
                media_type="application/zip",
            )


def sheets(day: date) -> list[tuple[str, list[list[str]]]]:
    with zipfile.ZipFile(raw.cached(SOURCE, CLARITY_URL.format(day=day)).path) as archive:
        tree = ET.fromstring(archive.read("detail.xls").decode("utf-8-sig"))
    found = []
    for sheet in tree.iter(SHEET + "Worksheet"):
        rows = []
        for row in sheet.iter(SHEET + "Row"):
            cells: list[str] = []
            for cell in row.findall(SHEET + "Cell"):
                index = cell.get(SHEET + "Index")
                while index is not None and len(cells) < int(index) - 1:
                    cells.append("")
                data = cell.find(SHEET + "Data")
                cells.append(data.text or "" if data is not None else "")
            rows.append(cells)
        found.append((sheet.get(SHEET + "Name") or "", rows))
    return found


def clarity_lines() -> list[dict[str, object]]:
    found = []
    for day in CLARITY:
        for _, rows in sheets(day):
            title = rows[0][0] if rows and rows[0] else ""
            if "JUSTICE OF THE SUPREME COURT OF APPEALS" not in title.upper():
                continue
            division = re.search(r"DIVISION (\d)", title.upper())
            if division is None:
                raise ValueError(title)
            names = [re.sub(r"\s+", " ", name).strip() for name in rows[1] if name]
            columns = [column for column, head in enumerate(rows[2]) if head == "Total Votes"]
            for row in rows[3:]:
                if row[0] == "Total:":
                    break
                for name, column in zip(names, columns, strict=True):
                    found.append(
                        {
                            "day": day,
                            "division": int(division[1]),
                            "remainder": "UNEXPIRED" in title.upper(),
                            "candidate": name,
                            "party": "",
                            "county": row[0],
                            "votes": int(row[column] or 0),
                        }
                    )
    return found


def lines() -> list[dict[str, object]]:
    found: dict[tuple[date, str, str], dict[str, object]] = {}
    for day, (eid, *_) in ELECTIONS.items():
        text = raw.cached(SOURCE, URL.format(eid=eid)).path.read_text(encoding="utf-8-sig")
        for line in csv.DictReader(io.StringIO(text)):
            if line["Type"] != "County" or line["OfficeDescription"] != "Justice of the Supreme Court of Appeals":
                continue
            key = (day, line["Name"], line["CountyName"])
            if key in found and line["OfficialResults"] != "Yes":
                continue
            found[key] = {
                "day": day,
                "division": 0,
                "remainder": False,
                "candidate": re.sub(r"\s+", " ", line["Name"]).strip(),
                "party": line["PartyCode"],
                "county": line["CountyName"],
                "votes": int(line["Votes"] or 0),
            }
    return [*found.values(), *clarity_lines()]


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia Supreme Court of Appeals elections 2008 to 2024 by county, "
            "from the Secretary of State's Election Results Center and results reports",
            polity="US-WV",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://apps.sos.wv.gov/elections/results/",
            license="Public record",
        )
        for eid, *_ in ELECTIONS.values():
            cs.artifact(raw.cached(SOURCE, URL.format(eid=eid)), source)
        for day in CLARITY:
            cs.artifact(raw.cached(SOURCE, CLARITY_URL.format(day=day)), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-WV'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/wv/%%'").fetchall()}
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}

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

        office_key = f"{slug(TITLE_OFFICE)}:WV"
        office = cs.key("us.office", office_key)
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-WV"), kind="other")
            cs.add(
                "name",
                entity_id=office,
                text=f"{TITLE_OFFICE} of West Virginia",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        groups: dict[tuple[date, int], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], row["division"])].append(row)
        persons: set[str] = set()
        for (day, division), group in sorted(groups.items()):
            if division:
                election_key, kind, _ = CLARITY[day]
                seats = 1
            else:
                _, election_key, kind, seats = ELECTIONS[day]
            title = f"{TITLE_OFFICE}, Division {division}" if division else TITLE_OFFICE
            title_slug = slug(title)
            election = cs.key("us.election", election_key)
            contest_key = f"{day}:WV:{title_slug}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=state_area,
                term="remainder" if group[0]["remainder"] else "full",
                magnitude=seats,
                round_number=1,
                electoral_system_id=systems["block" if seats > 1 else "fptp"],
                default_result_kind="counted",
            )
            name(contest, f"{day.year} West Virginia {title}")
            if contest in cs.minted:
                base = f"us/wv/{day}-{kind}/{title_slug}"
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
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            parties: dict[str, str] = {}
            for row in group:
                by_choice[str(row["candidate"])][str(row["county"])] += int(row["votes"])
                parties[str(row["candidate"])] = str(row["party"])
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, choice in enumerate(by_choice):
                shown = display(choice)
                first, last = identity(choice)
                person_key = f"WV:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, shown)
                party = PARTIES.get(parties[choice])
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "contest_option",
                    id=options[choice],
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_position=position + 1,
                    ballot_label=shown,
                )
                candidacies[choice] = cs.key("us.candidacy", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "candidacy",
                    id=candidacies[choice],
                    person_id=person,
                    option_id=options[choice],
                    role="head",
                    ballot_name=shown,
                    is_independent=False,
                )
            totals = {choice: sum(by_unit.values()) for choice, by_unit in by_choice.items()}
            for choice in sorted(totals, key=totals.__getitem__, reverse=True)[:seats]:
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
                    unit = counties[flat(place)]
                    by_place[str(unit)] += votes
                    values.append((str(result), str(unit), str(options[choice]), 0, 1, 0, 0, votes))
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
            print(contest_key, totals)
        return cs.write()
