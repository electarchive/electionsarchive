import csv
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ny_pres_primary_2000"
VERSION = "1"
DAY = date(2000, 3, 7)
PAGE = "https://results.elections.ny.gov/contest/4994"
FILE = DATA / "inbox" / "manual" / "MF-166" / "4994_table.csv"
CITY = "https://web.archive.org/web/20050530142050id_/http://www.vote.nyc.ny.us/pdf/results/2000/presPrimary/AllGreenPP2000.pdf"
CITY_DEM = "https://web.archive.org/web/20040101060122id_/http://www.vote.nyc.ny.us/pdf/results/2000/presPrimary/AllDemPP2000.pdf"
BOROUGHS = {"Bronx": "36005", "Kings": "36047", "New York": "36061", "Queens": "36081", "Richmond": "36085"}
GREEN = [("Ralph Nader", 117), ("Joel Kovel", 28), ("Stephen Gaskin", 7), ("Jello Biafra", 33)]
COUNTIES = {
    "Albany": "36001",
    "Allegany": "36003",
    "Bronx": "36005",
    "Broome": "36007",
    "Cattaraugus": "36009",
    "Cayuga": "36011",
    "Chautauqua": "36013",
    "Chemung": "36015",
    "Chenango": "36017",
    "Clinton": "36019",
    "Columbia": "36021",
    "Cortland": "36023",
    "Delaware": "36025",
    "Dutchess": "36027",
    "Erie": "36029",
    "Essex": "36031",
    "Franklin": "36033",
    "Fulton": "36035",
    "Genesee": "36037",
    "Greene": "36039",
    "Hamilton": "36041",
    "Herkimer": "36043",
    "Jefferson": "36045",
    "Kings": "36047",
    "Lewis": "36049",
    "Livingston": "36051",
    "Madison": "36053",
    "Monroe": "36055",
    "Montgomery": "36057",
    "Nassau": "36059",
    "New York": "36061",
    "Niagara": "36063",
    "Oneida": "36065",
    "Onondaga": "36067",
    "Ontario": "36069",
    "Orange": "36071",
    "Orleans": "36073",
    "Oswego": "36075",
    "Otsego": "36077",
    "Putnam": "36079",
    "Queens": "36081",
    "Rensselaer": "36083",
    "Richmond": "36085",
    "Rockland": "36087",
    "St. Lawrence": "36089",
    "Saratoga": "36091",
    "Schenectady": "36093",
    "Schoharie": "36095",
    "Schuyler": "36097",
    "Seneca": "36099",
    "Steuben": "36101",
    "Suffolk": "36103",
    "Sullivan": "36105",
    "Tioga": "36107",
    "Tompkins": "36109",
    "Ulster": "36111",
    "Warren": "36113",
    "Washington": "36115",
    "Wayne": "36117",
    "Westchester": "36119",
    "Wyoming": "36121",
    "Yates": "36123",
}


def fetch() -> None:
    raw.store(
        SOURCE,
        PAGE,
        FILE.read_bytes(),
        suffix=".csv",
        retrieved_at=datetime(2026, 10, 28, tzinfo=UTC),
        media_type="text/csv",
    )


def emit() -> Path:
    table = list(csv.reader(FILE.open(encoding="utf-8")))
    names = [re.sub(r"Larouche, Jr\.", "LaRouche Jr.", label) for label in table[0][2:5]]
    counties = {row[1]: [int(value) for value in row[2:5]] for row in table[2:] if row[0] == "County"}
    state = next(row for row in table if row[0] == "State")
    if set(counties) != set(COUNTIES) or [sum(votes[i] for votes in counties.values()) for i in range(3)] != [
        int(value) for value in state[2:5]
    ]:
        raise ValueError("the county rows do not match the state row")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's 2000 presidential primaries (Democratic by county, Green in New York City)",
            polity="US-NY",
        )

        def name(entity: object, label: str) -> None:
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        board = cs.source("New York State Board of Elections", "2000 Democratic presidential primary, by county")
        cs.add(
            "source",
            id=board,
            kind="official_body",
            publisher="New York State Board of Elections",
            title="2000 Democratic presidential primary, by county",
            url=PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PAGE), board)
        city = cs.source("Board of Elections in the City of New York", "2000 presidential primary, Green Party")
        cs.add(
            "source",
            id=city,
            kind="official_body",
            publisher="Board of Elections in the City of New York",
            title="2000 presidential primary, Green Party",
            url=CITY,
            license="Public record",
        )
        cs.artifact(raw.cached("us.nyc_recap_wayback", CITY), city)
        city_dem = cs.source(
            "Board of Elections in the City of New York", "2000 presidential primary, Democratic Party"
        )
        cs.add(
            "source",
            id=city_dem,
            kind="official_body",
            publisher="Board of Elections in the City of New York",
            title="2000 presidential primary, Democratic Party",
            url=CITY_DEM,
            license="Public record",
        )
        pdf = raw.cached("us.nyc_recap_wayback", CITY_DEM)
        cs.artifact(pdf, city_dem)
        parts = []
        for line in subprocess.run(
            ["pdftotext", "-layout", str(pdf.path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines():
            found = re.match(
                r"(Bronx|Kings|New York|Queens|Richmond)\s+(\d+)(?:ST|ND|RD|TH)\s+\*\s+\d+\s+\d+\s+\d+\s+\*\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
                line,
            )
            if found:
                votes = [int(found[i]) for i in range(3, 6)]
                if sum(votes) != int(found[6]):
                    raise ValueError(f"{line}: the candidates do not add up to the total")
                parts.append((found[1], int(found[2]), votes))
        for county in BOROUGHS:
            if [sum(votes[i] for name, _, votes in parts if name == county) for i in range(3)] != [
                counties[county][1],
                counties[county][0],
                counties[county][2],
            ]:
                raise ValueError(f"{county}: the congressional district rows do not match the county row")
        owner = cs.key("us.office", "president")
        system = cs.key("us.electoral_system", "fptp")
        election = cs.key("us.election", f"{DAY}:NY:primary")
        cs.add(
            "election",
            id=election,
            polity_id=cs.polity("US-NY"),
            kind="primary",
            date_from=DAY,
            date_to=DAY,
            date_precision="day",
            status="held",
        )
        name(election, "2000 New York primary elections")
        cs.add("slug", path=f"us/ny/{DAY}-primary", entity_id=election)
        for party_name, party_key, candidates, source, units in (
            (
                "Democratic",
                "democratic-party",
                [(label, [votes[index] for votes in counties.values()]) for index, label in enumerate(names)],
                board,
                [cs.unit("us.county", COUNTIES[county]) for county in counties],
            ),
            (
                "Green",
                "green-party",
                [(label, [votes]) for label, votes in GREEN],
                city,
                [cs.unit("us.place", "3651000")],
            ),
        ):
            key = f"{DAY}:NY:president:{party_name.lower()}"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                party_scope_id=cs.key("us.party", party_key),
                district_area_id=cs.area("us.state", "36")
                if party_name == "Democratic"
                else cs.area("us.place", "3651000"),
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"2000 New York {party_name} presidential primary")
            cs.add("slug", path=f"us/ny/{DAY}-primary/president-{party_name.lower()}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
                completeness=1,
            )
            ranked = sorted(candidates, key=lambda pair: -sum(pair[1]))
            winner = ranked[0][0]
            for label, values in ranked:
                token = slug(label)
                person = cs.key(f"{SOURCE}.person", token)
                cs.add("person", id=person)
                name(person, label)
                option = cs.key("us.option", f"{key}:{token}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party_key),
                    ballot_label=label,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{token}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                    is_independent=False,
                )
                for unit, votes in zip(units, values, strict=True):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            if party_name == "Democratic":
                by_part = cs.key(f"{SOURCE}.result_set", f"{key}:city-board")
                cs.add(
                    "result_set",
                    id=by_part,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=city_dem,
                    is_default=False,
                    completeness=1,
                )
                order = [
                    next(label for label in names if surname in label.lower())
                    for surname in ("bradley", "gore", "larouche")
                ]
                for borough, district, votes in parts:
                    code = f"{DAY}:{BOROUGHS[borough]}:{district}"
                    unit = cs.unit("us.county_part", code)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=cs.polity("US-NY"),
                        scheme_code="us.county_part",
                        code=code,
                        parent_id=cs.unit("us.county", BOROUGHS[borough]),
                        area_relation="within",
                        area_id=cs.area("us.county", BOROUGHS[borough]),
                        kind="standard",
                        valid_from=DAY,
                        valid_to=DAY + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=unit,
                        area_id=cs.area("us.cd", f"36{district:02d}"),
                        valid_from=DAY,
                        valid_to=DAY + timedelta(days=1),
                    )
                    for label, count in zip(order, votes, strict=True):
                        cs.add(
                            "result_value",
                            result_set_id=by_part,
                            unit_id=unit,
                            option_id=cs.key("us.option", f"{key}:{slug(label)}"),
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=count,
                        )
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=cs.key("us.option", f"{key}:{slug(winner)}"),
                kind="won",
                seats=None,
            )
        return cs.write()
