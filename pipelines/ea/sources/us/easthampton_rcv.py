import io
import json
import re
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.easthampton_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Easthampton City Clerk"
VIEW = "https://www.easthamptonma.gov/DocumentCenter/View/{}"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_place_500k.zip"
GEOID = "2519370"
REPORTS = {
    "2021-11-02": ("4663", "Biennial City Election, November 2, 2021, results"),
    "2025-11-04": ("9639", "Unofficial election results (ranked-choice races), November 4, 2025"),
}
SEATS = {
    "MAYOR": "mayor",
    "DISTRICT 4 CITY COUNCILOR": "council:district-4",
    "DISTRICT 5 CITY COUNCILOR": "council:district-5",
}
NAMES = {
    "Berzins": "Eric Berzins",
    "LaChapelle": "Nicole LaChapelle",
    "Routhier": "Keith Routhier",
    "Torrey": "Donald Torrey",
    "LINDSI SEKULA": "Lindsi Sekula",
    "JASON JT TIRRELL": "Jason JT Tirrell",
    "SALEM DERBY": "Salem Derby",
    "ROBERT PEIRENT": "Robert Peirent",
    "AMANDA C. NEWTON": "Amanda C. Newton",
    "TAMARA L. SMITH": "Tamara L. Smith",
    "CATHERINE WAUCZINSKI": "Catherine Wauczinski",
}
METHOD = {
    "2021-11-02": "Transcribed from the city clerk's official results. Only the first-round count was published because no race needed a second round. Blank ballots are invalid. Donald Torrey was a write-in candidate; all other write-ins are grouped into a single line.",
    "2025-11-04": "Transcribed from the city clerk's unofficial election-night ranked-choice report. No official round-by-round count has been published. Blank ballots are invalid; ballots that stop counting in later rounds are exhausted.",
}


def fetch() -> None:
    for view, _ in REPORTS.values():
        raw.fetch(SOURCE, VIEW.format(view), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    texts = {
        day: subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, VIEW.format(view)).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for day, (view, _) in REPORTS.items()
    }
    contests = {}
    page = texts["2021-11-02"].split("\f")[0]
    if "Mayor - Ranked Choice Voting - First Round" not in page:
        raise ValueError("2021 mayoral page not found")
    (row,) = re.findall(r"(?m)^TOTALS\s+([\d ]+)$", page)
    figures = [int(cell) for cell in row.split()]
    labels = ["Berzins", "LaChapelle", "Routhier", "Torrey", "Write-ins", "Blanks"]
    totals = dict(zip(labels, figures, strict=True))
    (cast,) = re.findall(r"TOTAL VOTES CAST\s+(\d+)", page)
    ballots = int(cast)
    if sum(totals.values()) != ballots:
        raise ValueError("2021 mayoral totals do not add up")
    blanks = totals.pop("Blanks")
    contests["2021-11-02:mayor"] = {"ballots": ballots, "blanks": blanks, "rounds": [totals]}
    for block in re.split(r"RCV Detailed Report", texts["2025-11-04"])[1:]:
        (title,) = re.findall(r"(?m)^([A-Z][A-Z0-9 ]+)\nUnofficial results", block)
        rounds: list[dict[str, int]] = []
        exhausted: list[int] = []
        blanks = 0
        for chunk in re.split(r"(?m)^Round \d+\n", block)[1:]:
            tally = {}
            for line in chunk.splitlines()[1:]:
                match = re.fullmatch(r"(\S.*?)\s{2,}([\d,]+)\s+[\d.]+%", line.strip())
                if match is None:
                    break
                tally[match.group(1)] = int(match.group(2).replace(",", ""))
            continuing, blanks, gone = (
                int(value.replace(",", ""))
                for (value,) in (
                    re.findall(r"Continuing Ballots Total\s+([\d,]+)", chunk),
                    re.findall(r"(?m)^Blanks\s+([\d,]+)", chunk),
                    re.findall(r"(?m)^Exhausted\s+([\d,]+)", chunk),
                )
            )
            if sum(tally.values()) != continuing:
                raise ValueError(f"{title}: round {len(rounds) + 1} does not add up")
            exhausted.append(gone)
            rounds.append(tally)
        if not re.search(r"is elected because", block):
            raise ValueError(f"{title}: no winner")
        valid = sum(rounds[0].values()) + exhausted[0]
        contests[f"2025-11-04:{SEATS[title]}"] = {
            "ballots": valid + blanks,
            "blanks": blanks,
            "rounds": rounds,
            "exhausted": exhausted,
        }
    if len(contests) != 4:
        raise ValueError(f"expected 4 contests, found {len(contests)}")
    (PARSED / "contests.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "contests.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Easthampton, Massachusetts' ranked-choice races: the 2021 mayoral election from the city "
            "clerk's official results and the 2025 mayoral and district council elections from the clerk's "
            "unofficial ranked-choice report",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-MA")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if {state, census} & cs.minted:
            raise ValueError("Massachusetts and the Census boundary source must already exist")
        places = raw.cached("us.alged", PLACES)
        with zipfile.ZipFile(places.path) as archive:
            parts = {item.rsplit(".", 1)[1].lower(): archive.read(item) for item in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        (shape,) = [
            json.dumps(item.__geo_interface__)
            for record, item in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and item is not None and record["GEOID"] == GEOID
        ]
        geom, point = conn.execute(
            """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from (select st_multi(st_collectionextract(st_makevalid(st_transform(
                       st_setsrid(st_geomfromgeojson(%s), 4269), 4326)), 3)) as geom) shape""",
            [shape],
        ).fetchone() or (None, None)

        polity = cs.key("us.polity", f"place:{GEOID}")
        city = cs.area("us.place", GEOID)
        whole = cs.unit("us.place", GEOID)
        cs.add("polity", id=polity, kind="municipality", iso_code=None)
        name(polity, "Easthampton")
        cs.add("polity_parent", polity_id=polity, parent_id=state)
        cs.add("slug", path="us/ma/easthampton", entity_id=polity)
        cs.add("area", id=city, scheme_code="us.place")
        cs.add("area_code", area_id=city, code=GEOID, valid_from=None)
        cs.add("area_parent", area_id=city, parent_id=cs.area("us.state", "25"))
        name(city, "Easthampton, MA")
        cs.add("polity_territory", polity_id=polity, area_id=city)
        cs.artifact(places, census)
        cs.add(
            "area_geometry",
            area_id=city,
            valid_from=None,
            geom=geom,
            label_point=point,
            quality="official",
            source_id=census,
        )
        cs.add(
            "reporting_unit",
            id=whole,
            polity_id=polity,
            scheme_code="us.place",
            code=GEOID,
            area_relation="equals",
            area_id=city,
            kind="standard",
            valid_from=None,
        )
        mayor = cs.key("us.office", f"MA:{GEOID}:mayor")
        council = cs.key("us.body", f"MA:{GEOID}:council")
        cs.add("office", id=mayor, polity_id=polity, kind="mayor")
        name(mayor, "Mayor of Easthampton")
        cs.add("body", id=council, polity_id=polity, kind="council")
        name(council, "Easthampton City Council")
        irv = cs.key("us.electoral_system", "easthampton:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Easthampton ranked-choice voting")

        elections: dict[str, UUID] = {}
        sources: dict[str, UUID] = {}
        for day, (view, title) in REPORTS.items():
            ((parent,),) = conn.execute(
                """select e.id from election e join name n on n.entity_id = e.id
                   where e.date_from = %s and n.text = %s and e.polity_id = %s""",
                [date.fromisoformat(day), f"{day[:4]} Massachusetts local elections", state],
            ).fetchall()
            election = cs.key("us.election", f"{day}:MA:{GEOID}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind="local",
                date_from=date.fromisoformat(day),
                date_to=date.fromisoformat(day),
                date_precision="day",
                status="held",
            )
            name(election, f"{day[:4]} Easthampton local election")
            cs.add("slug", path=f"us/ma/easthampton/{day}-local", entity_id=election)
            elections[day] = election
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=VIEW.format(view),
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, VIEW.format(view)), source)
            sources[day] = source

        for key, record in contests.items():
            day, seat = key[:10], key[11:]
            area, top = city, whole
            if seat != "mayor":
                number = seat[-1]
                code = f"25:{GEOID}:{seat}"
                area, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                cs.add("area", id=area, scheme_code="us.local_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=city, valid_from=None)
                name(area, f"Easthampton City Council, District {number}")
                cs.add(
                    "reporting_unit",
                    id=top,
                    polity_id=polity,
                    scheme_code="us.local_district",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
            contest = cs.key("us.contest", f"{day}:MA:{GEOID}:{seat}")
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day],
                kind="office" if seat == "mayor" else "body_seats",
                office_id=mayor if seat == "mayor" else None,
                body_id=None if seat == "mayor" else council,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=irv,
                default_result_kind="counted",
                term="full",
            )
            heading = (
                f"{day[:4]} Easthampton mayoral election"
                if seat == "mayor"
                else f"{day[:4]} Easthampton City Council election, District {seat[-1]}"
            )
            name(contest, heading)
            cs.add(
                "slug",
                path=f"us/ma/easthampton/{day}-local/{seat.replace(':', '-')}",
                entity_id=contest,
            )
            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
                election_id=elections[day],
                contest_id=contest,
                stage="official" if day == "2021-11-02" else "preliminary",
                kind="counted",
                status="active",
                source_id=sources[day],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD[day])
            rounds = record["rounds"]
            exhausted = record.get("exhausted", [0])
            values: list[tuple[UUID | None, int, int, int]] = [
                (None, 3, 0, record["ballots"]),
                (None, 5, 0, record["blanks"]),
                (None, 4, 1, record["ballots"] - record["blanks"]),
            ]
            values += [(None, 21, number, votes) for number, votes in enumerate(exhausted, 1)]
            final = rounds[-1]
            winner = max(final, key=lambda label: final[label])
            for label in rounds[0]:
                if label == "Write-ins":
                    option = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    text = NAMES[label]
                    person = cs.key(f"{SOURCE}.person", text.casefold().replace(" ", "-").replace(".", ""))
                    cs.add("person", id=person)
                    name(person, text)
                    option = cs.key("us.option", f"{contest}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if label == "Torrey" else "candidate",
                        ballot_label=text,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=text,
                        member_of_party_id=None,
                        is_independent=False,
                        is_incumbent=None,
                    )
                for number, tally in enumerate(rounds, 1):
                    if number == 1 or rounds[number - 2][label]:
                        values.append((option, 1, number, tally[label]))
                if label == "Write-ins":
                    continue
                for outcome in ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=top,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=1 if measure == 4 or measure == (21 if day == "2021-11-02" else 3) else 0,
                    value=votes,
                )
        return cs.write()
