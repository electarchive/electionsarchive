import re
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.shelby_tn_2026"
VERSION = "1"
DAY = date(2026, 8, 6)
URL = "https://www.electionsshelbytn.gov/wp-content/uploads/2026/08/Shelby-County-Election-Commission-8.6.2026-Tabulation-of-Votes-Recorded-FINAL.pdf"
PUBLISHER = "Shelby County Election Commission"
TITLE = "Tabulation of Votes Recorded, August 6, 2026 (final)"
CODES = {
    "Shelby County": "4700148",
    "City of Bartlett": "4700153",
    "Town of Collierville": "4700149",
    "City of Germantown": "4700151",
    "City of Lakeland": "4700154",
    "City of Millington": "4700150",
    "Town of Arlington": "4700152",
}
SEATS = {"4700154": 3}
ROW = re.compile(r"^(?P<name>\S.*?)\s{2,}(?P<votes>(?:[\d,]+|-)(?:\s+(?:[\d,]+|-)){4})\s+[\d.]+%$")
HEADING = re.compile(r"^(?P<who>Shelby County|(?:City|Town) of \w+) School Board,?(?:\s+(?P<seat>District \d+))?$")
METHOD = (
    "Votes are the Shelby County Election Commission's final tabulation for August 6, 2026, summed across early, "
    "absentee, polling-place, and provisional ballots and across the party-primary ballots on which the municipal "
    "school board races also appeared. The tabulation gives one total per race, with no precinct split."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def emit() -> Path:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    races: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    who = seat = None
    for line in text.splitlines():
        stripped = line.strip()
        if found := HEADING.match(stripped):
            who, seat = found["who"], found["seat"]
        elif who is not None and re.match(r"^(?:Position|District) \d+$", stripped):
            seat = stripped
        elif who is not None and (row := ROW.match(stripped)):
            total = int(row["votes"].split()[-1].replace(",", "").replace("-", "0"))
            races[(who, seat or "")][row["name"].strip()] = races[(who, seat or "")].get(row["name"].strip(), 0) + total
        elif stripped.startswith(("TABULATION", "See the accompanying")) or re.match(r"^[A-Z][A-Z ]+ (?:PRIMARY|GENERAL)", stripped):
            who = seat = None
    races = {key: votes for key, votes in races.items() if any(name != "Write-In Candidates" for name in votes)}
    with connect() as conn:
        paths = dict(
            conn.execute(
                """select u.code, s.path from reporting_unit u join slug s on s.entity_id = u.polity_id and s.is_current
                   where u.scheme_code = 'us.school_district' and u.code = any(%s)""",
                [list(CODES.values())],
            ).fetchall()
        )
        names = dict(
            conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.polity_id and n.is_preferred
                   where u.scheme_code = 'us.school_district' and u.code = any(%s)""",
                [list(CODES.values())],
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Shelby County, Tennessee school board races on August 6, 2026, from the Election Commission's final tabulation",
            polity="US",
        )

        def name(entity: object, label: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license=None)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:TN")
        if parent in cs.minted:
            cs.add(
                "election",
                id=parent,
                polity_id=cs.polity("US-TN"),
                kind="local",
                date_from=DAY,
                date_to=DAY,
                date_precision="day",
                status="held",
            )
            name(parent, "2026 Tennessee August county general election")
            cs.add("slug", path=f"us/tn/{DAY}-local", entity_id=parent)
        fptp, block = cs.key("us.electoral_system", "fptp"), cs.key("us.electoral_system", "block")
        elections: set[object] = set()
        districts: set[str] = set()
        bodies: set[object] = set()
        values: list[tuple[str, str, str, int]] = []
        counts = {"contests": 0, "candidates": 0}
        for (who, seat), votes in races.items():
            code = CODES[who]
            place, path = names[code], paths[code]
            owner_polity = conn.execute(
                "select polity_id from reporting_unit where scheme_code = 'us.school_district' and code = %s", [code]
            ).fetchone()[0]
            election = cs.key("us.election", f"{DAY}:TN:{code}")
            if election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=owner_polity,
                    parent_id=parent,
                    kind="local",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="held",
                )
                name(election, f"2026 {place} election")
                cs.add("slug", path=f"{path}/{DAY}-local", entity_id=election)
            body =cs.key("us.body", f"TN:{code}:board-of-education")
            if body not in bodies:
                bodies.add(body)
                if body in cs.minted:
                    cs.add("body", id=body, polity_id=owner_polity, kind="council")
                    name(body, f"{place} Board of Education")
            whole, unit = cs.area("us.school_district", code), cs.unit("us.school_district", code)
            area = whole
            if seat.startswith("District"):
                district = f"TN:{code}:board-of-education:{slug(seat)}"
                area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{place} Board of Education, {seat}")
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=owner_polity,
                        scheme_code="us.local_district",
                        code=district,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                    )
            seats = SEATS.get(code, 1)
            key = f"{DAY}:TN:{code}:board-of-education" + (f":{slug(seat)}" if seat else "")
            contest = cs.key("us.contest", key)
            heading = f"{place} Board of Education" + (f", {seat}" if seat else "")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=seats,
                round_number=1,
                electoral_system_id=fptp if seats == 1 else block,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"2026 {heading} election")
            cs.add(
                "slug", path=f"{path}/{DAY}-local/board-of-education" + (f"-{slug(seat)}" if seat else ""), entity_id=contest
            )
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            ranked: list[tuple[int, object]] = []
            for label, total in votes.items():
                if label == "Write-In Candidates":
                    option = cs.key("us.option", f"{key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    person = cs.key(f"{SOURCE}.person", f"{code}:{slug(label)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, label)
                    option = cs.key("us.option", f"{key}:{slug(label)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=None, ballot_label=label)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{slug(label)}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                    )
                    ranked.append((total, option))
                    counts["candidates"] += 1
                values.append((str(result_set), str(unit), str(option), total))
            ranked.sort(key=lambda pair: -pair[0])
            if len(ranked) <= seats or ranked[seats - 1][0] != ranked[seats][0]:
                for _, option in ranked[:seats]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            counts["contests"] += 1
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        print(counts)
        return cs.write()
