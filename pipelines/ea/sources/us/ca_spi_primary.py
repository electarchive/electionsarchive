import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import openelections
from ea.store import connect

SOURCE = "us.ca_spi_primary"
VERSION = "1"
DAY = date(2006, 6, 6)
URL = "https://elections.cdn.sos.ca.gov/sov/2006-primary/sov_detail_primary_spi.xls"
PUBLISHER, TITLE = "California Secretary of State", "Statement of Vote"
OFFICE = "Superintendent of Public Instruction"
CONTEST = f"{DAY}:CA:{slug(OFFICE)}"
METHOD = "County totals are the Secretary of State's statement of vote for the June 6, 2006 primary."
CONTEXT = (
    "The superintendent of public instruction is a nonpartisan office. A candidate who wins a majority in the "
    "primary is elected outright; Jack O'Connell won 52.4 percent, so no November runoff was held."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xls")


def emit() -> Path:
    sheet = pl.read_excel(raw.cached(SOURCE, URL).path, engine="calamine", has_header=False, infer_schema_length=0)
    candidates = [" ".join(text.removesuffix("*").split()) for text in sheet.row(0)[1:]]
    rows = [row for row in sheet.iter_rows() if row[0] is not None and not row[0].strip().startswith("Percent")]
    (state,) = [row[1:] for row in rows if row[0] == "State Totals"]
    counties = {row[0].strip(): [int(cell) for cell in row[1:]] for row in rows if row[0] != "State Totals"}
    totals = [sum(votes[index] for votes in counties.values()) for index in range(len(candidates))]
    if totals != [int(cell) for cell in state] or len(counties) != 58:
        raise ValueError(f"county sums {totals} differ from the state totals {state}")
    with connect() as conn:
        fips = openelections.counties(conn)["CA"]
        area = conn.execute(
            """select u.area_id from reporting_unit u where u.scheme_code = 'us.state' and u.code = '06'"""
        ).fetchone()[0]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's June 6, 2006 superintendent of public instruction primary by county, won outright "
            "by Jack O'Connell",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        election = cs.key("us.election", f"{DAY}:CA:primary")
        office = cs.key("us.office", f"{slug(OFFICE)}:CA")
        system = cs.key("us.electoral_system", "two_round")
        if cs.minted & {source, election, office, system}:
            raise ValueError("the source, election, office, and system must exist")
        cs.artifact(raw.cached(SOURCE, URL), source)
        contest = cs.key("us.contest", CONTEST)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office,
            district_area_id=area,
            term="full",
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, f"{DAY.year} California {OFFICE} election")
        cs.add("slug", path=f"us/ca/{DAY}-primary/{slug(OFFICE)}", entity_id=contest)
        result_set = cs.key(f"{SOURCE}.result_set", CONTEST)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=None,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
        cs.add("note", entity_id=result_set, kind="context", body_md=CONTEXT)
        values = []
        for index, candidate in enumerate(candidates):
            words = candidate.split()
            found = conn.execute(
                """select distinct cy.person_id from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join contest c on c.id = o.contest_id and c.office_id = %s
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where cy.role = 'head' and n.text ilike %s and n.text ~* %s""",
                [office, f"%{words[-1]}%", "|".join(re.escape(word) for word in words[:-1] if len(word) > 2)],
            ).fetchall()
            person = found[0][0] if len(found) == 1 else cs.key(f"{SOURCE}.person", f"CA:{slug(candidate)}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, candidate)
            option = cs.key("us.option", f"{CONTEST}:{slug(candidate)}")
            cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=candidate)
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{CONTEST}:{slug(candidate)}:head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                is_independent=False,
            )
            for county, votes in counties.items():
                plain = re.sub(r"[^a-z]", "", county.lower())
                values.append((str(result_set), str(cs.unit("us.county", fips[plain])), str(option), votes[index]))
            if totals[index] * 2 > sum(totals):
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
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
        return cs.write()
