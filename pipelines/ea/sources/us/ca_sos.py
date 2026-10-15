from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ca_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SOV = "https://elections.cdn.sos.ca.gov/sov/"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote"
FILES = {
    (date(2024, 3, 5), False): "2024-primary/sov/64-uss-ft.xlsx",
    (date(2024, 3, 5), True): "2024-primary/sov/76-uss-pt.xlsx",
    (date(2024, 11, 5), False): "2024-general/sov/19-uss-ft.xlsx",
    (date(2024, 11, 5), True): "2024-general/sov/22-uss-pt.xlsx",
}
PRIMARY = date(2024, 3, 5)
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "GRN": "green-party",
    "PF": "CA:peace-and-freedom-party",
    "AI": "CA:american-independent-party",
}
TOP_TWO = (
    "California uses a top-two primary. All candidates appear on one ballot, and the two with the most votes advance "
    "to the general election regardless of party. That means two candidates from the same party can face each other "
    "in November. The party shown is the preference each candidate stated when filing; it is not a party nomination."
)


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SOV + path, suffix=".xlsx")


def parse() -> None:
    rows: list[tuple[date, bool, int, str, str, str, bool, int]] = []
    for (day, special), path in FILES.items():
        sheet = (
            fastexcel.read_excel(raw.cached(SOURCE, SOV + path).path)
            .load_sheet(0, header_row=None, dtypes="string")
            .to_polars()
        )
        header, preferences, *body = sheet.iter_rows()
        candidates = [
            (" ".join(str(name).split()), str(preference).removesuffix(" (W/I)"), str(preference).endswith("(W/I)"))
            for name, preference in zip(header[1:], preferences[1:], strict=True)
        ]
        printed: list[int] = []
        counted = [0] * len(candidates)
        counties = 0
        for label, *cells in body:
            if label is None or label.strip() == "Percent":
                continue
            if label == "State Totals":
                printed = [int(cell or 0) for cell in cells]
                continue
            counties += 1
            for index, ((name, preference, write_in), cell) in enumerate(zip(candidates, cells, strict=True)):
                counted[index] += int(cell or 0)
                rows.append((day, special, index, label, name, preference, write_in, int(cell or 0)))
        if counted != printed or counties != 58:
            raise ValueError(f"{path}: {counties} counties sum to {counted}, the sheet prints {printed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "special": pl.Boolean,
            "position": pl.Int64,
            "county": pl.String,
            "name": pl.String,
            "preference": pl.String,
            "write_in": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "senate.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "senate.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 2024 U.S. Senate primary and general elections by county",
            polity="US-CA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, SOV + path), source)

        california = cs.polity("US-CA")
        area = cs.area("us.state", "06")
        units: dict[str, UUID] = dict(
            conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%'"""
            ).fetchall()
        )
        known: dict[str, UUID] = {}
        for text, person in conn.execute(
            """select distinct n.text, cy.person_id from external_id k
               join contest_option o on o.contest_id = k.entity_id
               join candidacy cy on cy.option_id = o.id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where k.scheme = 'us.contest' and k.value like '%:CA:%'"""
        ).fetchall():
            if known.setdefault(slug(text), person) != person:
                raise ValueError(f"two California candidates are named {text}")
        senate, seat, top_two = (
            cs.key("us.body", "senate"),
            cs.key("us.seat", "senate:CA:1"),
            cs.key("us.electoral_system", "top_two"),
        )
        general = cs.key("us.election", "2024-11-05:CA")
        if cs.minted & {senate, seat, top_two, general}:
            raise ValueError("the Senate, its California Class I seat, the top-two system, and the general must exist")
        primary = cs.key("us.election", f"{PRIMARY}:CA:primary")
        cs.add(
            "election",
            id=primary,
            polity_id=california,
            kind="primary",
            date_from=PRIMARY,
            date_to=PRIMARY,
            status="held",
        )
        name(primary, "2024 California primary election")
        cs.add("slug", path=f"us/ca/{PRIMARY}-primary", entity_id=primary)

        persons: dict[str, UUID] = {}
        for text in frame["name"].unique().sort():
            if slug(text) in known:
                persons[text] = known[slug(text)]
                continue
            persons[text] = cs.key(f"{SOURCE}.person", slug(text))
            cs.add("person", id=persons[text])
            name(persons[text], text)

        for special in (False, True):
            previous = None
            for (day,), lines in (
                frame.filter(pl.col("special") == special).sort("day").group_by("day", maintain_order=True)
            ):
                stage = "primary" if day == PRIMARY else "general"
                contest_key = f"{day}:CA:senate" + (":special" if special else "")
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=primary if day == PRIMARY else general,
                    kind="body_seats",
                    body_id=senate,
                    district_area_id=area,
                    seat_id=seat,
                    magnitude=1,
                    round_number=1 if day == PRIMARY else 2,
                    previous_round_id=previous,
                    electoral_system_id=top_two,
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"2024 U.S. Senate {'special election ' if special else ''}"
                    f"{'primary' if day == PRIMARY else 'election'} in California",
                )
                cs.add("slug", path=f"us/ca/{day}-{stage}/senate{'-special' if special else ''}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=primary if day == PRIMARY else general,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                if day == PRIMARY:
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=TOP_TWO)

                options: dict[str, UUID] = {}
                totals: dict[str, int] = dict(lines.group_by("name").agg(pl.col("votes").sum()).iter_rows())
                for text, preference, write_in in (
                    lines.select("name", "preference", "write_in").unique(maintain_order=True).iter_rows()
                ):
                    option_key = f"{contest_key}:{persons[text]}"
                    options[text] = cs.key("us.option", option_key)
                    party = None if preference == "NPP" else cs.key("us.party", PARTIES[preference])
                    if party in cs.minted:
                        raise ValueError(f"{contest_key}: no party for {preference}")
                    cs.add(
                        "contest_option",
                        id=options[text],
                        contest_id=contest,
                        kind="write_in" if write_in else "candidate",
                        party_id=party,
                        ballot_label=text,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{option_key}:head"),
                        person_id=persons[text],
                        option_id=options[text],
                        role="head",
                        ballot_name=text,
                        is_independent=preference == "NPP",
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
                ranked = sorted(totals, key=totals.__getitem__, reverse=True)
                advancing = 2 if day == PRIMARY else 1
                if len(ranked) > advancing and totals[ranked[advancing - 1]] == totals[ranked[advancing]]:
                    raise ValueError(f"{contest_key}: a tie at {ranked[advancing - 1]}")
                for text in ranked[:advancing]:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[text],
                        kind="advanced" if day == PRIMARY else "elected",
                        seats=None if day == PRIMARY else 1,
                    )
                previous = contest
        return cs.write()
