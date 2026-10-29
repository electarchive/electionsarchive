import csv
import html
import io
import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats
from ea.store import DATA, connect

SOURCE = "us.ma_council"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SHAPES = "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/GOVCOUNCIL2021.zip"
SHAPES_PAGE = "https://www.mass.gov/info-details/massgis-data-massachusetts-governors-council-districts-2021"
CANDIDATES = (
    "https://www.sec.state.ma.us/divisions/elections/research-and-statistics/2026-state-election-candidates.htm"
)
INBOX = DATA / "inbox" / "y26" / "ne" / "ma_2026-state-election-candidates.html"
LISTING = f"{elstats.MASSACHUSETTS}search/year_from:2022/year_to:2024/office_id:529/stage:General"
DAYS = {2022: date(2022, 11, 8), 2024: date(2024, 11, 5), 2026: date(2026, 11, 3)}
PARTIES = {"Democratic": "democratic-party", "Republican": "republican-party", "Unenrolled": None}
ORDINALS = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5, "Sixth": 6, "Seventh": 7, "Eighth": 8}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def download(election: str) -> str:
    return f"{elstats.MASSACHUSETTS}download/{election}/"


def fetch() -> None:
    raw.fetch(SOURCE, SHAPES, suffix=".zip")
    page = raw.fetch(SOURCE, LISTING, suffix=".html").path.read_text()
    for election, *_ in elstats.MA_ROW.findall(page):
        raw.fetch(SOURCE, download(election), suffix=".csv")
    try:
        raw.cached(SOURCE, CANDIDATES)
    except LookupError:
        raw.store(
            SOURCE,
            CANDIDATES,
            INBOX.read_bytes(),
            suffix=".html",
            retrieved_at=datetime.now(UTC),
            media_type="text/html",
        )


def parse() -> None:
    results = []
    page = raw.cached(SOURCE, LISTING).path.read_text()
    for election, year, district, _ in elstats.MA_ROW.findall(page):
        lines = list(csv.reader(raw.cached(SOURCE, download(election)).path.read_text().splitlines()))
        names = lines[0][3:]
        parties = lines[1][3:] + [""] * len(names)
        towns = [line for line in lines[2:] if line[0] and line[0] != "TOTALS"]
        totals = [sum(int(line[3 + column].replace(",", "")) for line in towns) for column in range(len(names))]
        number = int(re.match(r"\d+", district.strip())[0])
        for column, (candidate, party) in enumerate(zip(names, parties[: len(names)], strict=False)):
            if candidate in ("All Others", "Blanks", "Total Votes Cast"):
                kind = {"All Others": "write_in", "Blanks": "blank", "Total Votes Cast": "total"}[candidate]
            else:
                kind = "candidate"
                if party not in PARTIES:
                    raise ValueError(f"{year} district {number}: {candidate} is {party!r}")
            results.append(
                {
                    "year": int(year),
                    "district": number,
                    "candidate": candidate,
                    "party": party,
                    "kind": kind,
                    "votes": totals[column],
                }
            )
    page = raw.cached(SOURCE, CANDIDATES).path.read_text()
    section = page.split('<h2 id="councillor"', 1)[1].split("<h2", 1)[0]
    ballot = []
    for district, block in re.findall(r"<h3>(\w+) District</h3>(.*?)(?=<h3>|$)", section, re.DOTALL):
        for text in re.findall(r"<p>(.*?)</p>", block, re.DOTALL):
            line = " ".join(html.unescape(text).split())
            found = re.fullmatch(r"(.+?), (\d.+?),? (Democratic|Republican)", line)
            if found is None:
                raise ValueError(f"{district} District: {line!r}")
            ballot.append({"district": ORDINALS[district], "candidate": found[1], "party": found[3]})
    if len({row["district"] for row in ballot}) != 8:
        raise ValueError("expected candidates in all eight districts")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(results).write_parquet(PARSED / "results.parquet")
    pl.DataFrame(ballot).write_parquet(PARSED / "ballot.parquet")


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    ballot = pl.read_parquet(PARSED / "ballot.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts Governor's Council: the eight districts of the 2021 map with MassGIS shapes, the 2022 and 2024 generals, and the 2026 general candidates",
            polity="US-MA",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-MA")
        state_area = cs.area("us.state", "25")
        system = cs.key("us.electoral_system", "fptp")
        for key in PARTIES.values():
            if key is not None and cs.key("us.party", key) in cs.minted:
                raise ValueError(f"party {key} must already exist")
        elections = {}
        for year, day in DAYS.items():
            elections[year] = cs.key("us.election", f"{day}:MA")
            if elections[year] in cs.minted:
                raise ValueError(f"the {day} Massachusetts election must exist")

        shapes_artifact = raw.cached(SOURCE, SHAPES)
        mass_gis = cs.source("MassGIS", "Massachusetts Governor's Council Districts (2021)")
        if mass_gis in cs.minted:
            cs.add(
                "source",
                id=mass_gis,
                kind="official_body",
                publisher="MassGIS",
                title="Massachusetts Governor's Council Districts (2021)",
                url=SHAPES_PAGE,
                license="Public record",
            )
        cs.artifact(shapes_artifact, mass_gis)
        with zipfile.ZipFile(shapes_artifact.path) as archive:
            parts = {
                item.rsplit(".", 1)[1].lower(): archive.read(item)
                for item in archive.namelist()
                if item.upper().endswith(("POLY.SHP", "POLY.SHX", "POLY.DBF"))
            }
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        shapes = {
            int(record["DIST_NUM"]): json.dumps(item.__geo_interface__)
            for record, item in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        }
        if sorted(shapes) != list(range(1, 9)):
            raise ValueError("expected eight Governor's Council districts")

        body = cs.key("us.body", "MA:governors-council")
        cs.add("body", id=body, polity_id=state, kind="council")
        name(body, "Massachusetts Governor's Council")
        districts = {}
        for number, shape in shapes.items():
            code = f"25:council:{number}"
            area = cs.area("us.local_district", code)
            unit = cs.unit("us.local_district", code)
            cs.add("area", id=area, scheme_code="us.local_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=state_area, valid_from=None)
            name(area, f"Massachusetts Governor's Council District {number}")
            geom, point = conn.execute(
                """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(%s), 26986), 4326)), 3)) as geom) shape""",
                [shape],
            ).fetchone()
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=date(2022, 1, 1),
                geom=geom,
                label_point=point,
                quality="official",
                source_id=mass_gis,
            )
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=state,
                scheme_code="us.local_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            districts[number] = (area, unit)

        sec = cs.source(elstats.MA_PUBLISHER, "Massachusetts election statistics")
        if sec in cs.minted:
            cs.add(
                "source",
                id=sec,
                kind="official_body",
                publisher=elstats.MA_PUBLISHER,
                title="Massachusetts election statistics",
                url=elstats.MASSACHUSETTS,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, LISTING), sec)
        candidates = cs.source(elstats.MA_PUBLISHER, "2026 State Election Candidates")
        if candidates in cs.minted:
            cs.add(
                "source",
                id=candidates,
                kind="official_body",
                publisher=elstats.MA_PUBLISHER,
                title="2026 State Election Candidates",
                url=CANDIDATES,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, CANDIDATES), candidates)

        def person_for(candidate: str) -> object:
            tokens = [token for token in slug(candidate).split("-") if token not in SUFFIXES]
            person = cs.key(f"{SOURCE}.person", f"{tokens[0]}-{tokens[-1]}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, candidate)
            return person

        counts = {"contests": 0, "candidates": 0, "result sets": 0}
        for year, day in DAYS.items():
            rows = results.filter(pl.col("year") == year) if year != 2026 else None
            for number, (area, unit) in districts.items():
                contest_key = f"{day}:MA:governors-council:{number}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[year],
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=1,
                    term="full",
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} Massachusetts Governor's Council election, District {number}")
                cs.add("slug", path=f"us/ma/{day}-general/governors-council-{number}", entity_id=contest)
                counts["contests"] += 1
                if rows is None:
                    district_rows = [
                        (row["candidate"], row["party"], "candidate", None)
                        for row in ballot.filter(pl.col("district") == number).iter_rows(named=True)
                    ]
                    cs.add("citation", entity_id=contest, source_id=candidates, locator=None)
                else:
                    district_rows = [
                        (row["candidate"], row["party"], row["kind"], row["votes"])
                        for row in rows.filter(pl.col("district") == number).iter_rows(named=True)
                    ]
                options: dict[str, object] = {}
                for candidate, party, kind, _ in district_rows:
                    if kind in ("blank", "total"):
                        continue
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    options[candidate] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if kind == "write_in" else "candidate",
                        party_id=cs.key("us.party", PARTIES[party])
                        if kind == "candidate" and PARTIES[party] is not None
                        else None,
                        ballot_label="Write-in" if kind == "write_in" else candidate,
                    )
                    if kind == "write_in":
                        continue
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}"),
                        person_id=person_for(candidate),
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=PARTIES[party] is None,
                    )
                    counts["candidates"] += 1
                if rows is None:
                    continue
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[year],
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sec,
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="District totals added up from the town-by-town returns on the Secretary of the Commonwealth's election statistics site.",
                )
                tally = {}
                for candidate, _, kind, votes in district_rows:
                    if kind == "total":
                        measure, option = 3, None
                    elif kind == "blank":
                        measure, option = 6, None
                    else:
                        measure, option = 1, options[candidate]
                        if kind == "candidate":
                            tally[candidate] = (votes, option)
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
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
                    count_round=0,
                    flags=0,
                    value=sum(votes for _, _, kind, votes in district_rows if kind in ("candidate", "write_in")),
                )
                ranked = sorted(tally.values(), key=lambda entry: -entry[0])
                if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
                    cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won", seats=None)
                counts["result sets"] += 1
        print(counts)
        return cs.write()
