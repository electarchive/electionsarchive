import re
import subprocess
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import oe_primaries_2026 as oe
from ea.store import DATA, connect

SOURCE = "us.mt_primary_2026"
VERSION = "1"
DAY = date(2026, 6, 2)
FILE = DATA / "inbox" / "manual" / "MF-406" / "Statewide Results.xlsx"
PUBLISHER = "Montana Secretary of State"
PAGE = "https://electionresults.mt.gov/"
URL = f"{PAGE}#MF-406"
CANVASS = "https://sosmt.gov/wp-content/uploads/wpfd/preview_files/2026-Primary-State-Canvass(f0627306086b974f9ac2e416bb8125c9).pdf"
TITLE = "Montana June 2, 2026 primary election results"
NOTE = (
    "County totals from the Secretary of State's statewide export by county, with all 728 precincts reporting. Every "
    "race's statewide totals match the Secretary of State's 2026 primary state canvass, and the county sums were "
    "checked against the TOTALS row of each race."
)
PARTIES = {
    "Republican": ("republican", "2026-06-02:MT:senate:republican"),
    "Democrat": ("democratic", "2024-06-04:MT:senate:democratic"),
    "Libertarian": ("libertarian", "2024-06-04:MT:house:1:libertarian"),
}
SHEETS = (
    ("02597DEM UNITED STATES SENATOR", "senate", ""),
    ("02597LIB UNITED STATES SENATOR", "senate", ""),
    ("02598REP UNITED STATES REPRESE", "house", "1"),
    ("02598DEM UNITED STATES REPRESE", "house", "1"),
    ("02598LIB UNITED STATES REPRESE", "house", "1"),
    ("02599REP UNITED STATES REPRESE", "house", "2"),
    ("02599DEM UNITED STATES REPRESE", "house", "2"),
    ("02599LIB UNITED STATES REPRESE", "house", "2"),
    ("02602NON SUPREME COURT JUSTICE", "court", "4"),
)


def fetch() -> None:
    raw.fetch(SOURCE, CANVASS, suffix=".pdf")
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            FILE.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def sheet(book: fastexcel.ExcelReader, name: str) -> tuple[list[tuple[str, str]], dict[str, list[int]], list[int]]:
    rows = book.load_sheet_by_name(name, header_row=None).to_polars().rows()
    start = next(index for index, row in enumerate(rows) if row[1] == "County")
    candidates = []
    for cell in rows[start][2:]:
        label, _, party = (cell or "").partition("\n")
        candidates.append((label.strip(), party.strip()))
    by_county: dict[str, list[int]] = {}
    totals: list[int] = []
    for row in rows[start + 1 :]:
        if row[1] is None:
            continue
        values = [int(str(cell).replace(",", "") or 0) for cell in row[2:]]
        if str(row[1]).strip().upper() == "TOTALS":
            totals = values
        else:
            by_county[str(row[1]).strip()] = values
    for index, (label, _) in enumerate(candidates):
        if sum(values[index] for values in by_county.values()) != totals[index]:
            raise ValueError(f"{name} {label}: counties differ from the TOTALS row")
    return candidates, by_county, totals


def canvass_totals() -> set[tuple[int, ...]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CANVASS).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    return {
        tuple(int(cell) for cell in line.split()[1:])
        for line in text.splitlines()
        if re.fullmatch(r"Total(\s+\d+)+", line.strip())
    }


def emit() -> Path:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, URL).path))
    official = canvass_totals()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Montana's June 2, 2026 primary: the U.S. Senate Democratic and Libertarian primaries, both U.S. House districts for each party, and the Supreme Court Justice #4 primary, by county",
            polity="US-MT",
        )

        def template(text: str) -> tuple:
            return conn.execute(
                """select c.office_id, c.body_id, c.district_area_id, c.seat_id, c.boundary_set_id, c.magnitude, c.term,
                          c.electoral_system_id, c.party_scope_id
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' where x.value = %s""",
                [text],
            ).fetchone()

        election = conn.execute(
            "select entity_id from external_id where scheme = 'us.election' and value = '2026-06-02:MT:primary'"
        ).fetchone()[0]
        scopes = {label: template(key)[8] for label, (_, key) in PARTIES.items()}
        top_two = cs.key("us.electoral_system", "top_two")
        counties: dict[str, UUID] = {}
        for code, text, id in conn.execute(
            """select u.code, n.text, u.id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ '^30'"""
        ).fetchall():
            counties[oe.plain(text)] = id
        pool: dict[str, set[UUID]] = defaultdict(set)
        for person, text in conn.execute(
            """select cy.person_id, n.text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ '^20(1[4-9]|2[0-9])-[0-9-]+:MT:'"""
        ).fetchall():
            pool[oe.plain(text)].add(person)

        def person_of(text: str) -> UUID:
            found = pool.get(oe.plain(text), set())
            if len(found) == 1:
                return next(iter(found))
            id = cs.key(f"{SOURCE}.person", f"MT:{slug(text)}")
            if id in cs.minted:
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=text, lang="en", kind="official", is_preferred=True)
            return id

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
        for url in (URL, CANVASS):
            cs.artifact(raw.cached(SOURCE, url), source)
        made = 0
        for name, kind, number in SHEETS:
            candidates, by_county, totals = sheet(book, name)
            if tuple(totals) not in official:
                raise ValueError(f"{name}: totals {totals} are not in the state canvass")
            party_label = candidates[0][1]
            nonpartisan = kind == "court"
            if nonpartisan:
                shape = template("2026-11-03:MT:justice-of-the-supreme-court:seat-4")
                party, scope, party_id = None, None, None
                contest_key = f"{DAY}:MT:justice-of-the-supreme-court:seat-{number}"
                label = "2026 Montana Supreme Court Justice #4 primary"
                path = "justice-of-the-supreme-court-seat-4"
                system = top_two
            else:
                party = PARTIES[party_label][0]
                shape = template(f"2026-11-03:MT:{kind}{f':{number}' if number else ''}")
                scope = scopes[party_label]
                party_id = scope
                tail = f"house:{number}:{party}" if kind == "house" else f"senate:{party}"
                contest_key = f"{DAY}:MT:{tail}"
                label = (
                    f"2026 U.S. House {party.capitalize()} primary in Montana's {'1st' if number == '1' else '2nd'} congressional district"
                    if kind == "house"
                    else f"2026 U.S. Senate {party.capitalize()} primary in Montana"
                )
                path = tail.replace(":", "-")
                system = template("2026-06-02:MT:senate:republican")[7]
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if nonpartisan else "body_seats",
                office_id=shape[0],
                body_id=shape[1],
                district_area_id=shape[2],
                seat_id=shape[3],
                boundary_set_id=shape[4],
                magnitude=shape[5],
                party_scope_id=scope,
                term=shape[6],
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add("name", entity_id=contest, text=label, lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=f"us/mt/2026-06-02-primary/{path}", entity_id=contest)
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
            order = sorted(range(len(candidates)), key=lambda index: -totals[index])
            options: dict[int, UUID] = {}
            for index, (candidate, _) in enumerate(candidates):
                text = oe.title(candidate)
                options[index] = cs.key("us.option", f"{contest_key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=options[index],
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=text,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(text)}"),
                    person_id=person_of(text),
                    option_id=options[index],
                    role="head",
                    ballot_name=text,
                    is_independent=False,
                )
            for rank, index in enumerate(order):
                if nonpartisan:
                    cs.add(
                        "outcome",
                        result_set_id=result,
                        option_id=options[index],
                        kind="advanced" if rank < 2 else "lost",
                    )
                elif rank == 0:
                    cs.add("outcome", result_set_id=result, option_id=options[index], kind="won")
            rows = []
            for county, values in by_county.items():
                unit = counties[oe.plain(county.replace("&", "and"))]
                for index, votes in enumerate(values):
                    rows.append((str(result), str(unit), str(options[index]), 0, 1, 0, 0, votes))
                rows.append((str(result), str(unit), None, 0, 4, 0, 0, sum(values)))
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
            made += 1
            print(contest_key, dict(zip([c[0] for c in candidates], totals, strict=True)))
        print(made)
        return cs.write()
