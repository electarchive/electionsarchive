import re
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.al_special_2026"
VERSION = "1"
DAY = date(2026, 8, 11)
URL = "https://www.sos.alabama.gov/sites/default/files/election-data/2026-09/Republication%20Results%20From%20The%20Special%20Congressional%20Election.xlsx"
PUBLISHER = "Alabama Secretary of State"
TITLE = "Republication of results from the August 11, 2026 special congressional primary election"
PAGE = "https://www.sos.alabama.gov/alabama-votes/voter/election-data"
NOTE = (
    "Republican primary results by county from the Secretary of State's republication of the special congressional "
    "primary election, held under the congressional map the courts restored after the May 19 primary. The Democratic "
    "nominees in Districts 1 and 2 were unopposed. In Washington County, the republication prints Ammie Akin's votes "
    "as text rather than a number; her zero is the difference between the county's total and David Perry's votes."
)
NULLIFIED = (
    "The court-ordered congressional map nullified the May 19 nominations for Congressional Districts 1, 2, 6, and 7. "
    "The nomination was run again in the [August 11, 2026 special primary](/us/al/2026-08-11-special-primary). This "
    "set is the count of the May 19 contest as it was held, and its outcomes stay as they were declared."
)
MAY = {1: "2026-05-19:AL:house:1:republican", 6: "2026-05-19:AL:house:6:republican"}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xlsx")


def blocks(sheet: list[list[str | None]]) -> list[tuple[int, str, dict[str, int | None]]]:
    found = []
    for r, row in enumerate(sheet):
        for c, cell in enumerate(row):
            district = re.search(r"Congressional District (\d)", cell or "")
            if district is None or sheet[r + 2][c] != "Candidate":
                continue
            votes: dict[str, int | None] = {}
            for line in sheet[r + 3 :]:
                if line[c] is None or line[c] == "Total Votes":
                    break
                votes[line[c]] = int(line[c + 1]) if (line[c + 1] or "").isdigit() else None
            total = next(int(line[c + 1]) for line in sheet[r + 3 :] if line[c] == "Total Votes")
            if None in votes.values():
                votes = {
                    name: (total - sum(v for v in votes.values() if v is not None) if v is None else v)
                    for name, v in votes.items()
                }
            found.append((int(district[1]), sheet[r + 1][c] or "", votes))
    return found


def words(text: str) -> list[str]:
    plain = re.sub(r'"[^"]*"|\([^)]*\)', "", text).casefold()
    return [word for word in re.findall(r"[a-z'-]+", plain) if word not in ("jr", "sr", "ii", "iii", "iv")]


def emit() -> Path:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, URL).path))
    sheets = {
        name: [
            [None if cell is None else str(cell).strip() for cell in row]
            for row in book.load_sheet_by_name(name, header_row=None).to_polars().iter_rows()
        ]
        for name in book.sheet_names
    }
    totals = {district: votes for district, _, votes in blocks(sheets.pop("Summary"))}
    counties: dict[int, dict[str, dict[str, int]]] = {}
    for name, sheet in sheets.items():
        for district, _, votes in blocks(sheet):
            counties.setdefault(district, {})[name] = {
                candidate: count for candidate, count in votes.items() if count is not None
            }
    for district, expected in totals.items():
        summed: dict[str, int] = {}
        for votes in counties[district].values():
            for candidate, count in votes.items():
                summed[candidate] = summed.get(candidate, 0) + count
        if summed != expected:
            raise ValueError(f"district {district}: county sums {summed} differ from the district totals {expected}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's August 11, 2026 special congressional primary (Republican, Districts 1, 2, 6, and 7) by county, and caveats on the nullified May 19 District 1 and 6 contests",
            polity="US-AL",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-AL")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        election = cs.key("us.election", f"{DAY}:AL:special-primary")
        cs.add(
            "election",
            id=election,
            polity_id=state,
            parent_id=None,
            kind="primary",
            date_from=DAY,
            date_to=DAY,
            status="held",
        )
        name(election, "2026 Alabama special congressional primary election")
        if election in cs.minted:
            cs.add("slug", path=f"us/al/{DAY}-special-primary", entity_id=election)
        may = conn.execute(
            """select c.electoral_system_id, c.party_scope_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = %s""",
            [MAY[1]],
        ).fetchone()
        republican = conn.execute(
            """select distinct o.party_id from contest_option o join contest c on c.id = o.contest_id
               join external_id x on x.entity_id = c.id where x.value = %s""",
            [MAY[1]],
        ).fetchone()[0]
        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s and c.role = 'head'""",
            [state],
        ).fetchall():
            spelled = words(text)
            if spelled:
                people.setdefault((spelled[0], spelled[-1]), set()).add(person)
        fips = {
            re.sub(r" County, AL$", "", label): code
            for label, code in conn.execute(
                """select n.text, c.code from area_code c join area a on a.id = c.area_id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'us.county' and c.code like '01%%'"""
            ).fetchall()
        }
        for district, candidates in counties.items():
            key = f"{DAY}:AL:house:{district}:republican"
            contest = cs.key("us.contest", key)
            template = conn.execute(
                """select kind, body_id, district_area_id, boundary_set_id, magnitude, term from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' where x.value = %s""",
                [f"2026-11-03:AL:house:{district}"],
            ).fetchone()
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind=template[0],
                body_id=template[1],
                district_area_id=template[2],
                boundary_set_id=template[3],
                magnitude=template[4],
                term=template[5],
                party_scope_id=may[1],
                round_number=1,
                previous_round_id=None,
                electoral_system_id=may[0],
                default_result_kind="counted",
            )
            name(contest, f"2026 U.S. House special Republican primary in Alabama's Congressional District {district}")
            if contest in cs.minted:
                cs.add("slug", path=f"us/al/{DAY}-special-primary/house-{district}-republican", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", key)
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
            options: dict[str, UUID] = {}
            winner = max(totals[district], key=lambda candidate: totals[district][candidate])
            for candidate in totals[district]:
                spelled = words(candidate)
                matches = people.get((spelled[0], spelled[-1]), set())
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", f"{key}:{'-'.join(spelled)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, candidate)
                option = options[candidate] = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=republican,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                    is_incumbent=False,
                )
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=option,
                    kind="advanced" if candidate == winner else "lost",
                )
            rows = []
            for county, votes in candidates.items():
                unit = cs.unit("us.county", fips[county])
                for candidate, count in votes.items():
                    rows.append((str(result), str(unit), str(options[candidate]), 0, 1, 0, 0, count))
                rows.append((str(result), str(unit), None, 0, 4, 0, 0, sum(votes.values())))
            cs.frame(
                "result_value",
                pl.DataFrame(
                    rows,
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
            print(district, sum(totals[district].values()), winner)
        for district, value in MAY.items():
            cs.add("note", entity_id=cs.key("us.contest", value), kind="caveat", body_md=NULLIFIED)
        return cs.write()
