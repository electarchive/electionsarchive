import io
import itertools
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import numpy
import shapefile
from PIL import Image, ImageOps

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.eastpointe_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Macomb County Clerk"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_place_500k.zip"
GEOID = "2624290"
WAYBACK = "https://web.archive.org/web/{}id_/https://electionresults.macombgov.org/{}"
REPORTS = {
    "2020-11-03": (
        WAYBACK.format("20210713160254", "m31/eastpointe_results_final.png"),
        "https://electionresults.macombgov.org/m31/eastpointe.html",
        "Election results, November 3, 2020: Eastpointe City Council, partial term (official)",
        1,
        ["Sylvia L. Moore"],
        {
            "Sylvia L. Moore": ["5177", "7525", "7525", "11602"],
            "Rob Baker": ["4756", "5651", "5651", "0"],
            "Tonia Gladney": ["3989", "0", "0", "0"],
            "Inactive ballots": ["4171", "4917", "4917", "6491"],
        },
    ),
    "2021-11-02": (
        WAYBACK.format("20211129200643", "m33/eastpointe_results_2021_final.png"),
        "https://electionresults.macombgov.org/m33/eastpointe.html",
        "Election results, November 2, 2021: Eastpointe City Council (official)",
        2,
        ["Cardi DeMonaco Jr.", "Rob Baker"],
        {
            "Cardi DeMonaco Jr.": ["1805", "1268", "1268", "1268", "1268", "1268"],
            "Rob Baker": ["705", "919.2", "949.34", "1056.8175", "1281.0125", "1268"],
            "Shenita Lloyd": ["479", "510.535", "572.27", "724.195", "899.3675", "902.0631"],
            "Sylvia L. Moore": ["347", "422.8625", "468.515", "549.8775", "0", "0"],
            "Michael Jones": ["298", "361.3675", "400.5325", "0", "0", "0"],
            "Tonia Gladney": ["169", "203.2125", "0", "0", "0", "0"],
            "Inactive ballots": ["113", "230.81", "257.33", "317.0975", "467.6075", "477.8454"],
            "Residual surplus": ["0", "0.0125", "0.0125", "0.0125", "0.0125", "0.0915"],
        },
    ),
}
METHOD = {
    1: "Transcribed from the county clerk's official ranked-choice results, which were published only as an image. Each number was confirmed by OCR. Round 1 inactive ballots had no valid first choice and are counted as invalid; ballots that became inactive in later rounds are counted as exhausted. The county kept tabulating after the winner passed the threshold in round 2, and rounds 3 and 4 are shown as published.",
    2: "Transcribed from the county clerk's official ranked-choice results, which were published only as an image. Each number was confirmed by OCR. Two seats were filled by single transferable vote: a candidate who reached the threshold was elected, and their surplus moved to the next choices at a fractional value. Round 1 inactive ballots had no valid first choice and are counted as invalid; ballots that became inactive later, along with the rounding remainders the county lists as residual surplus, are counted as exhausted.",
}


def fetch() -> None:
    for image, *_ in REPORTS.values():
        raw.fetch(SOURCE, image, suffix=".png")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables = {}
    for day, (url, _, _, _, winners, transcribed) in REPORTS.items():
        image = Image.open(raw.cached(SOURCE, url).path).convert("RGB")
        dark = numpy.asarray(image).astype(int).max(axis=2) < 110
        height, width = dark.shape
        ink = Image.fromarray(numpy.where(dark, 0, 255).astype(numpy.uint8))

        lines: list[list[tuple[int, int]]] = []
        for size, masks in ((width * 0.5, dark), (height * 0.25, dark.T)):
            spans: list[tuple[int, int]] = []
            for index, mask in enumerate(masks):
                edges = numpy.flatnonzero(numpy.diff(numpy.concatenate(([0], mask.astype(int), [0]))))
                if numpy.diff(edges)[::2].max(initial=0) > size:
                    if spans and index - spans[-1][1] <= 2:
                        spans[-1] = (spans[-1][0], index)
                    else:
                        spans.append((index, index))
            lines.append(spans)
        horizontal, vertical = lines
        grid: dict[str, list[tuple[list[str], list[float]]]] = {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cell.png"
            for (_, top), (bottom, _) in itertools.pairwise(horizontal):
                cells: list[tuple[list[str], list[float]]] = []
                for (_, left), (right, _) in itertools.pairwise(vertical):
                    box = (left + 2, top + 2, right - 1, bottom - 1)
                    numeric = bool(cells) and cells[0][0][0].replace(" ", "") in {
                        label.replace(" ", "") for label in transcribed
                    }
                    readings = []
                    for source, mode in ((image, "7"), (ink, "7"), (ink, "8"))[: 3 if numeric else 1]:
                        crop = source.crop(box)
                        ImageOps.expand(
                            crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS),
                            border=40,
                            fill="white",
                        ).save(path)
                        command = ["tesseract", str(path), "-", "--psm", mode]
                        if numeric:
                            command += ["-c", "tessedit_char_whitelist=0123456789."]
                        readings.append(
                            subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()
                        )
                    inked = numpy.argwhere(dark[box[1] : box[3], box[0] : box[2]])
                    if len(inked):
                        (y0, x0), (y1, x1) = inked.min(axis=0), inked.max(axis=0) + 1
                        box = (box[0] + int(x0), box[1] + int(y0), box[0] + int(x1), box[1] + int(y1))
                    cells.append((readings, [box[0] / width, box[1] / height, box[2] / width, box[3] / height]))
                grid[cells[0][0][0].replace(" ", "")] = cells[1:]
        rows = {}
        for label, expected in transcribed.items():
            if label.replace(" ", "") not in grid:
                raise ValueError(f"{day}: no row {label} among {list(grid)}")
            cells = grid[label.replace(" ", "")]
            if any(text not in readings for text, (readings, _) in zip(expected, cells, strict=True)):
                raise ValueError(f"{day} {label}: OCR read {[readings for readings, _ in cells]}, not {expected}")
            rows[label] = [{"votes": float(text), "box": box} for text, (_, box) in zip(expected, cells, strict=True)]
        if list(grid)[:2] != ["Rounds", "Eliminated"]:
            raise ValueError(f"{day}: unexpected table layout {list(grid)}")
        marked = list(grid.values())[2]
        named = {readings[0].replace(" ", "") for readings, _ in marked}
        if any(name.replace(" ", "") not in named for name in winners):
            raise ValueError(f"{day}: elected row reads {named}")
        columns = list(zip(*transcribed.values(), strict=True))
        if len({round(sum(float(text) for text in column), 3) for column in columns}) != 1:
            raise ValueError(f"{day}: rounds do not sum to the same ballots")
        tables[day] = rows
    (PARSED / "tables.json").write_text(json.dumps(tables))


def emit() -> Path:
    tables = json.loads((PARSED / "tables.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Eastpointe, Michigan's ranked-choice city council elections of 2020 and 2021, digitized from "
            "the Macomb County Clerk's official result images",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-MI")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if {state, census} & cs.minted:
            raise ValueError("Michigan and the Census boundary source must already exist")
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
        name(polity, "Eastpointe")
        cs.add("polity_parent", polity_id=polity, parent_id=state)
        cs.add("slug", path="us/mi/eastpointe", entity_id=polity)
        cs.add("area", id=city, scheme_code="us.place")
        cs.add("area_code", area_id=city, code=GEOID, valid_from=None)
        cs.add("area_parent", area_id=city, parent_id=cs.area("us.state", "26"))
        name(city, "Eastpointe, MI")
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
        council = cs.key("us.body", f"MI:{GEOID}:council")
        cs.add("body", id=council, polity_id=polity, kind="council")
        name(council, "Eastpointe City Council")
        systems = {}
        for seats, rules, label in (
            (1, {"formula": "irv"}, "Eastpointe ranked-choice voting"),
            (2, {"formula": "stv", "quota": "droop"}, "Eastpointe proportional ranked-choice voting"),
        ):
            systems[seats] = cs.key("us.electoral_system", f"eastpointe:{rules['formula']}")
            cs.add("electoral_system", id=systems[seats], polity_id=polity, rules=rules, rules_version=1)
            name(systems[seats], label)

        people: dict[str, UUID] = {}
        for day, (url, page_url, title, seats, winners, _) in REPORTS.items():
            when = date.fromisoformat(day)
            parent_name = (
                "2020 Michigan general election" if day.startswith("2020") else "2021 Michigan local elections"
            )
            ((parent,),) = conn.execute(
                """select e.id from election e join name n on n.entity_id = e.id
                   where e.date_from = %s and n.text = %s and e.polity_id = %s""",
                [when, parent_name, state],
            ).fetchall()
            election = cs.key("us.election", f"{day}:MI:{GEOID}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind="local",
                date_from=when,
                date_to=when,
                date_precision="day",
                status="held",
            )
            name(election, f"{day[:4]} Eastpointe local election")
            cs.add("slug", path=f"us/mi/eastpointe/{day}-local", entity_id=election)
            contest = cs.key("us.contest", f"{day}:MI:{GEOID}:council")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                office_id=None,
                body_id=council,
                district_area_id=city,
                magnitude=seats,
                round_number=1,
                electoral_system_id=systems[seats],
                default_result_kind="counted",
                term="remainder" if seats == 1 else "full",
            )
            name(contest, f"{day[:4]} Eastpointe City Council election")
            cs.add("slug", path=f"us/mi/eastpointe/{day}-local/council", entity_id=contest)

            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=page_url,
                license="Public record",
            )
            png = raw.cached(SOURCE, url)
            artifact = cs.artifact(png, source)
            image = PAGES / f"{png.sha256}-1.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(png.path, image)
            with Image.open(image) as opened:
                width, height = opened.size
            page = cs.page(artifact, 1)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=1,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )

            result = cs.key(f"{SOURCE}.result_set", day)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD[seats])
            rows = dict(tables[day])
            inactive = rows.pop("Inactive ballots")
            residual = rows.pop("Residual surplus", [{"votes": 0.0, "box": None}] * len(inactive))
            ballots = round(sum(cells[0]["votes"] for cells in rows.values()) + inactive[0]["votes"])
            valid = ballots - round(inactive[0]["votes"])
            values: list[tuple[UUID | None, int, int, float, list[float] | None]] = [
                (None, 3, 0, ballots, None),
                (None, 5, 0, inactive[0]["votes"], inactive[0]["box"]),
                (None, 4, 1, valid, None),
            ]
            for number in range(len(inactive)):
                tally = sum(cells[number]["votes"] for cells in rows.values())
                values.append((None, 21, number + 1, round(valid - tally, 4), None))
            if any(cell["votes"] for cell in residual) and round(valid - tally, 4) != round(
                inactive[-1]["votes"] - inactive[0]["votes"] + residual[-1]["votes"], 4
            ):
                raise ValueError(f"{day}: exhausted does not reconcile with inactive and residual surplus")
            final = {label: cells[-1]["votes"] for label, cells in rows.items()}
            for label, cells in rows.items():
                if label not in people:
                    people[label] = cs.key(f"{SOURCE}.person", label.casefold().replace(".", "").replace(" ", "-"))
                    cs.add("person", id=people[label])
                    name(people[label], label)
                person = people[label]
                option = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
                for number, cell in enumerate(cells, 1):
                    if number == 1 or cells[number - 2]["votes"]:
                        values.append((option, 1, number, cell["votes"], cell["box"]))
                won = label in winners
                for outcome in ("won", "elected") if won else ("lost" if final[label] else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            for option, measure, number, votes, box in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=whole,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=8 if box is not None else 1,
                    value=votes,
                )
                if box is not None:
                    x0, y0, x1, y1 = box
                    cs.add(
                        "value_locator",
                        result_set_id=result,
                        unit_id=whole,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=number,
                        page_id=page,
                        source_id=None,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                    )
        return cs.write()
