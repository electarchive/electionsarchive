import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_boe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Board of Elections in the City of New York"
TITLE = "2021 Primary: Official Ranked Choice Rounds, DEM Mayor Citywide"
RCV = "https://www.vote.nyc/sites/default/files/pdf/election_results/2021/20210622Primary%20Election/rcv/024306_1.html"
PRIMARY = date(2021, 6, 22)
COUNTIES = ("36005", "36047", "36061", "36081", "36085")
WRITE_INS = "Write-ins"
PERSONS = {"Andrew Yang": ("fec.candidate", "P00006486")}


def fetch() -> None:
    raw.fetch(SOURCE, RCV, suffix=".html")


def parse() -> None:
    document = raw.cached(SOURCE, RCV).path.read_text(encoding="utf-8")
    table = [
        [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", document, re.DOTALL)
    ]
    header = next(index for index, row in enumerate(table) if row and row[0] == "Votes in this round")
    rounds = table[header].count("# votes")
    counts: list[tuple[str, int, int]] = []
    for label, *cells in table[header + 1 :]:
        if label.startswith("*"):
            break
        for number in range(rounds):
            cell = cells[3 * number].replace(",", "") if 3 * number < len(cells) else ""
            if cell.isdigit():
                counts.append((label, number + 1, int(cell)))
    frame = pl.DataFrame(counts, schema=["label", "round", "votes"], orient="row")
    totals = frame.group_by("round").agg(pl.col("votes").sum()).sort("round")["votes"].to_list()
    final = frame.filter((pl.col("round") == rounds) & (pl.col("label") != "Inactive ballots")).sort(
        "votes", descending=True
    )
    winner = re.search(r"([^<>\n]+?) won the DEM Mayor Citywide Primary with\s+([\d,]+)", document)
    if (
        len(set(totals)) != 1
        or winner is None
        or final.row(0) != (winner[1].strip(), rounds, int(winner[2].replace(",", "")))
    ):
        raise ValueError(f"rounds do not conserve ballots ({totals}) or the winner differs from {winner}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "mayor.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "mayor.parquet")
    rounds = frame["round"].max()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's 2021 Democratic mayoral primary, ranked-choice rounds",
            polity="US",
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
            url=RCV,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, RCV), source)

        city = cs.key("us.polity", "new-york-city")
        cs.add("polity", id=city, kind="municipality")
        name(city, "New York City")
        cs.add("polity_parent", polity_id=city, parent_id=cs.polity("US-NY"))
        cs.add("slug", path="us/ny/new-york-city", entity_id=city)
        cs.add("external_id", entity_id=city, scheme="wikidata", value="Q60")
        cs.add("scheme", code="us.place", polity_id=cs.polity("US"), kind="administrative", rank=3, noun="place")
        area = cs.area("us.place", "3651000")
        cs.add("area", id=area, scheme_code="us.place")
        cs.add("area_code", area_id=area, code="3651000", valid_from=None)
        cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "36"))
        name(area, "New York City")
        cs.add("polity_territory", polity_id=city, area_id=area)
        geom, point = conn.execute(
            """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from (select st_multi(st_union(g.geom)) as geom from area_geometry g
                     join area_code c on c.area_id = g.area_id
                     join area a on a.id = g.area_id and a.scheme_code = 'us.county'
                     where c.code = any(%s)) city""",
            [list(COUNTIES)],
        ).fetchone() or (None, None)
        cs.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            geom=geom,
            label_point=point,
            quality="official",
            source_id=cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000"),
        )
        unit = cs.unit("us.place", "3651000")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=city,
            scheme_code="us.place",
            code="3651000",
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=None,
        )

        mayor = cs.key("us.office", "new-york-city:mayor")
        cs.add("office", id=mayor, polity_id=city, kind="mayor")
        name(mayor, "Mayor of New York City")
        irv = cs.key("us.electoral_system", "new-york-city:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=city,
            rules={"formula": "irv", "maxRankings": 5, "batchElimination": True},
            rules_version=1,
        )
        name(irv, "New York City ranked-choice voting")
        election = cs.key("us.election", f"{PRIMARY}:new-york-city:primary")
        cs.add(
            "election", id=election, polity_id=city, kind="primary", date_from=PRIMARY, date_to=PRIMARY, status="held"
        )
        name(election, "2021 New York City primary election")
        cs.add("slug", path=f"us/ny/new-york-city/{PRIMARY}-primary", entity_id=election)
        democrats = cs.key("us.party", "democratic-party")
        if cs.minted & {democrats}:
            raise ValueError("the Democratic Party must already exist")
        contest_key = f"{PRIMARY}:new-york-city:mayor:democratic"
        contest = cs.key("us.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=mayor,
            party_scope_id=democrats,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=irv,
            default_result_kind="counted",
        )
        name(contest, "2021 Democratic primary for Mayor of New York City")
        cs.add("slug", path=f"us/ny/new-york-city/{PRIMARY}-primary/mayor-democratic", entity_id=contest)
        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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

        options: dict[str, UUID] = {}
        lasts = dict(frame.group_by("label").agg(pl.col("round").max()).iter_rows())
        for label in frame.filter(pl.col("round") == 1).sort("votes", descending=True)["label"]:
            if label == "Inactive ballots":
                continue
            option_key = f"{contest_key}:{slug(label)}"
            options[label] = cs.key("us.option", option_key)
            cs.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind="write_in" if label == WRITE_INS else "candidate",
                party_id=None if label == WRITE_INS else democrats,
                ballot_label=label,
            )
            if label in PERSONS:
                person = cs.key(*PERSONS[label])
                if person in cs.minted:
                    raise ValueError(f"{label} must already exist")
            elif label != WRITE_INS:
                person = cs.key(f"{SOURCE}.person", slug(label))
                cs.add("person", id=person)
                name(person, label)
            if label != WRITE_INS:
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{option_key}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    is_independent=False,
                )
        finalists = frame.filter((pl.col("round") == rounds) & (pl.col("label") != "Inactive ballots")).sort(
            "votes", descending=True
        )["label"]
        for label, option in options.items():
            if label != WRITE_INS:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="won" if label == finalists[0] else "lost" if lasts[label] == rounds else "eliminated",
                )
        if result_set in cs.minted:
            for label, count_round, votes in frame.iter_rows():
                inactive = label == "Inactive ballots"
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None if inactive else options[label],
                    channel=0,
                    measure=21 if inactive else 1,
                    count_round=count_round,
                    flags=0,
                    value=votes,
                )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=1,
                flags=0,
                value=frame.filter(pl.col("round") == 1)["votes"].sum(),
            )
        return cs.write()
