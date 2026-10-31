from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ca_pres_minor"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://elections.cdn.sos.ca.gov/sov/2024-primary/sov/25-presidential-formatted.xlsx"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote"
DAY = date(2024, 3, 5)
PARTIES = {
    "AI": ("american-independent", "American Independent", "CA:american-independent-party"),
    "GRN": ("green", "Green", "green-party"),
    "LIB": ("libertarian", "Libertarian", "libertarian-party"),
    "PF": ("peace-and-freedom", "Peace and Freedom", "CA:peace-and-freedom-party"),
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xlsx")


def parse() -> None:
    sheet = (
        fastexcel.read_excel(raw.cached(SOURCE, URL).path).load_sheet(0, header_row=None, dtypes="string").to_polars()
    )
    header, parties, *body = sheet.iter_rows()
    rows = []
    for row in body:
        county = row[0]
        if county is None or county.strip().startswith(("Percent", "State Total")) or str(row[1]).endswith("%"):
            continue
        for column in range(1, len(header)):
            party = str(parties[column]).removesuffix(" (W/I)")
            if party in PARTIES:
                rows.append(
                    (
                        county,
                        party,
                        " ".join(str(header[column]).split()),
                        str(parties[column]).endswith("(W/I)"),
                        int(str(row[column]).replace(",", "")),
                    )
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["county", "party", "name", "write_in", "votes"], orient="row").write_parquet(
        PARSED / "rows.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 2024 American Independent, Green, Libertarian, and Peace and Freedom presidential primaries by county",
            polity="US-CA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError("the Secretary of State's Statement of Vote must already be a source")
        cs.artifact(raw.cached(SOURCE, URL), source)
        office, system = conn.execute(
            """select c.office_id, c.electoral_system_id from contest c
               join slug s on s.entity_id = c.id where s.path = 'us/ca/2024-03-05-primary/president-democratic'"""
        ).fetchone()
        area = cs.area("us.state", "06")
        primary = cs.key("us.election", f"{DAY}:CA:primary")
        units = dict(
            conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%'"""
            ).fetchall()
        )
        known: dict[str, set[UUID]] = {}
        for text, person in conn.execute(
            """select distinct n.text, cy.person_id from contest c
               join contest_option o on o.contest_id = c.id
               join candidacy cy on cy.option_id = o.id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where c.office_id = %s""",
            [office],
        ).fetchall():
            known.setdefault(slug(text), set()).add(person)
        persons: dict[str, UUID] = {}
        for text in frame["name"].unique().sort():
            if (found := known.get(slug(text))) is not None:
                if len(found) > 1:
                    raise ValueError(f"two presidential candidates are named {text}")
                persons[text] = next(iter(found))
            else:
                persons[text] = cs.key(f"{SOURCE}.person", slug(text))
                cs.add("person", id=persons[text])
                name(persons[text], text)
        for party, (key, label, party_key) in PARTIES.items():
            lines = frame.filter(pl.col("party") == party)
            contest_key = f"{DAY}:CA:president:{key}"
            contest = cs.key("us.contest", contest_key)
            party_id = cs.key("us.party", party_key)
            if party_id in cs.minted:
                raise ValueError(f"no party {party_key}")
            cs.add(
                "contest",
                id=contest,
                election_id=primary,
                kind="delegate_allocation",
                office_id=office,
                party_scope_id=party_id,
                district_area_id=area,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"2024 California {label} presidential primary")
            cs.add("slug", path=f"us/ca/{DAY}-primary/president-{key}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=primary,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            options: dict[str, UUID] = {}
            for text, write_in in lines.select("name", "write_in").unique(maintain_order=True).iter_rows():
                option_key = f"{contest_key}:{persons[text]}"
                options[text] = cs.key("us.option", option_key)
                cs.add(
                    "contest_option",
                    id=options[text],
                    contest_id=contest,
                    kind="write_in" if write_in else "candidate",
                    party_id=None if write_in else party_id,
                    ballot_label=text,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{option_key}:head"),
                    person_id=persons[text],
                    option_id=options[text],
                    role="head",
                    ballot_name=text,
                    is_independent=False,
                )
            if result_set in cs.minted:
                for county, text, votes in lines.select("county", "name", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[county],
                        option_id=options[text],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        return cs.write()
