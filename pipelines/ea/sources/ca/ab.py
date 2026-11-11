import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid5

import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.sources.licenses import PUBLIC_RECORD, UNLICENSED
from ea.store import DATA, connect

SOURCE = "ca.ab"
VERSION = "2"
DAY = date(2026, 10, 19)
PAGE = "https://www.elections.ab.ca/elections/referendum/"
ORDERS = {
    109: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_109.pdf",
    110: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_110.pdf",
    160: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_160.pdf",
}
RESULTS = {
    number: f"https://officialresults.elections.ab.ca/orEDResultsReferendum2026.cfm?EventId=2026{number:02d}&MODE=exED"
    for number in range(1, 11)
}
BOUNDARIES = "https://www.elections.ab.ca/wp-content/uploads/2019Boundaries_ED-Shapefiles.zip"
FIRST = date(2019, 4, 16)
RENAMED = {"17": "Calgary-Bhullar-McCall"}
PARSED = DATA / "parsed" / SOURCE


def order(number: int) -> int:
    return 110 if number <= 5 else 109 if number <= 9 else 160


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for url in ORDERS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in RESULTS.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    if not (PARSED / "questions.json").exists():
        (PARSED / "questions.json").write_text((DATA / "tmp" / "nh-ab" / "questions.json").read_text())
    rows = []
    for number, url in RESULTS.items():
        sheet = pl.read_excel(raw.cached(SOURCE, url).path, has_header=False, infer_schema_length=0)
        divisions = sheet.filter(pl.col("column_0").str.contains(r"^\d{2}$"))
        total = sheet.filter(pl.col("column_0") == "Total Valid Vote").row(0)
        if divisions.height != 87:
            raise ValueError(f"question {number}: {divisions.height} divisions")
        for column in ("column_3", "column_4", "column_5"):
            if divisions[column].cast(pl.Int64).sum() != int(total[int(column[-1])]):
                raise ValueError(f"question {number}: {column} does not sum to the total")
        rows.extend(
            {
                "question": number,
                "division": int(code),
                "name": name,
                "electors": int(electors),
                "first": int(first),
                "second": int(second),
            }
            for code, name, _, electors, first, second, _ in divisions.iter_rows()
        )
    results = pl.DataFrame(rows)
    with zipfile.ZipFile(raw.cached(SOURCE, BOUNDARIES).path) as z:
        parts = {
            name.rsplit(".", 1)[1].lower(): io.BytesIO(z.read(name))
            for name in z.namelist()
            if name.rsplit(".", 1)[1].lower() in ("shp", "shx", "dbf")
        }
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    records = [
        (str(record["EDNumber20"]), record["EDName2017"], json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        if record is not None and shape is not None
    ]
    with connect() as conn:
        shapes = pl.DataFrame(
            conn.execute(
                """with ed as (
                     select code, name, st_multi(st_collectionextract(st_makevalid(st_transform(
                              st_setsrid(st_geomfromgeojson(geojson), 3401), 4326)), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson))
                   select code, name, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex'),
                          st_x(st_centroid(geom)), st_y(st_centroid(geom))
                   from ed order by code::int""",
                [list(column) for column in zip(*records, strict=True)],
            ).fetchall(),
            schema=["code", "name", "geom", "label_point", "x", "y"],
            orient="row",
        )
    if not shapes.filter(
        (pl.col("x") < -120.1) | (pl.col("x") > -109.9) | (pl.col("y") < 49) | (pl.col("y") > 60)
    ).is_empty():
        raise ValueError("division centroids fall outside Alberta")
    names = dict(results.select(pl.col("division").cast(pl.String), "name").unique().iter_rows())
    shapes = shapes.with_columns(
        former=pl.when(pl.col("code").is_in(list(RENAMED))).then(pl.col("name")),
        name=pl.col("code").replace_strict(RENAMED, default=pl.col("name"), return_dtype=pl.String),
    )
    for code, name in shapes.select("code", "name").iter_rows():
        if re.sub(r"\W", "", names[code]).upper() != re.sub(r"\W", "", name).upper():
            raise ValueError(f"division {code}: {name} in the boundaries, {names[code]} in the results")
    results.write_parquet(PARSED / "results.parquet")
    shapes.drop("x", "y").write_parquet(PARSED / "shapes.parquet")
    print(f"{results.height} question rows, {shapes.height} divisions")


def emit() -> Path:
    questions = json.loads((PARSED / "questions.json").read_text())["questions"]
    results = pl.read_parquet(PARSED / "results.parquet")
    shapes = pl.read_parquet(PARSED / "shapes.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 Alberta referendum: ten questions with their official wording and official results by electoral division",
            polity="CA-AB",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        alberta = cs.polity("CA-AB")
        if alberta in cs.minted:
            raise ValueError("run ca.elections first: Alberta must exist")
        elections = cs.source("Elections Alberta", "2026 Alberta Referendum")
        cs.add(
            "source",
            id=elections,
            kind="official_body",
            publisher="Elections Alberta",
            title="2026 Alberta Referendum",
            url=PAGE,
            license=PUBLIC_RECORD,
        )
        cs.artifact(raw.cached(SOURCE, PAGE), elections)
        orders = {}
        for number, url in ORDERS.items():
            orders[number] = cs.source("Alberta King's Printer", f"Order in Council {number}/2026")
            cs.add(
                "source",
                id=orders[number],
                kind="official_body",
                publisher="Alberta King's Printer",
                title=f"Order in Council {number}/2026",
                url=url,
                license=UNLICENSED,
            )
            cs.artifact(raw.cached(SOURCE, url), orders[number])
        official = cs.source("Elections Alberta", "2026 Referendum Official Results by Electoral Division")
        cs.add(
            "source",
            id=official,
            kind="official_body",
            publisher="Elections Alberta",
            title="2026 Referendum Official Results by Electoral Division",
            url="https://officialresults.elections.ab.ca/",
            license=PUBLIC_RECORD,
        )
        for url in RESULTS.values():
            cs.artifact(raw.cached(SOURCE, url), official)
        boundaries = cs.source("Elections Alberta", "2019 Electoral Division Boundaries (shapefiles)")
        cs.add(
            "source",
            id=boundaries,
            kind="official_body",
            publisher="Elections Alberta",
            title="2019 Electoral Division Boundaries (shapefiles)",
            url="https://open.alberta.ca/opendata/d2eff235-4c11-416f-b4e2-791c305594b1",
            license="Open Government Licence – Alberta",
            license_url="https://open.alberta.ca/licence",
        )
        cs.artifact(raw.cached(SOURCE, BOUNDARIES), boundaries)
        province = cs.area("ca.province", "48")
        if province in cs.minted:
            raise ValueError("run ca.elections first: Alberta's province area must exist")
        cs.add("scheme", code="ca.ab", polity_id=alberta, kind="electoral_district", rank=2, noun="electoral division")
        cs.add("boundary_set", scheme_code="ca.ab", vintage="2017", valid_from=FIRST, valid_to=None, as_of=FIRST)
        areas = {}
        units = {}
        for code, division, geom, label_point, former in shapes.iter_rows():
            area = areas[int(code)] = cs.area("ca.ab", code, FIRST)
            cs.add("area", id=area, scheme_code="ca.ab")
            cs.add("area_code", area_id=area, code=code, valid_from=FIRST, valid_to=None)
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, division)
            if former:
                cs.add("name", entity_id=area, text=former, lang="en", kind="historical", is_preferred=False)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=FIRST,
                valid_to=None,
                geom=geom,
                label_point=label_point,
                quality="official",
                source_id=boundaries,
            )
            units[int(code)] = cs.unit("ca.ab", f"{DAY}:{code}", DAY)
            cs.add(
                "reporting_unit",
                id=units[int(code)],
                polity_id=alberta,
                scheme_code="ca.ab",
                code=f"{DAY}:{code}",
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
        system = cs.key(f"{SOURCE}.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=alberta, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Alberta referendum")
        election = cs.key(f"{SOURCE}.election", str(DAY))
        cs.add("election", id=election, polity_id=alberta, kind="referendum", date_from=DAY, date_to=DAY, status="held")
        name(election, "2026 Alberta referendum")
        cs.add("slug", path=f"ca/ab/{DAY}-referendum", entity_id=election)
        for question in questions:
            number = question["number"]
            contest = cs.key(f"{SOURCE}.contest", f"{DAY}:{number}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="optional_referendum")
            name(contest, f"Question {number}")
            cs.add("slug", path=f"ca/ab/{DAY}-referendum/question-{number}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", f"{DAY}:{number}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=official,
                status="active",
                is_default=True,
                completeness=1,
            )
            rows = results.filter(pl.col("question") == number)
            totals = {"first": rows["first"].sum(), "second": rows["second"].sum()}
            values = [
                {"unit": units[division], "option": None, "measure": 2, "value": electors}
                for division, electors in rows.select("division", "electors").iter_rows()
            ]
            for position, (label, column) in enumerate(zip(question["options"], totals, strict=True), 1):
                option = cs.key(f"{SOURCE}.option", f"{DAY}:{number}:{position}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="won" if totals[column] == max(totals.values()) else "lost",
                )
                values.extend(
                    {"unit": units[division], "option": option, "measure": 1, "value": votes}
                    for division, votes in rows.select("division", column).iter_rows()
                )
            cs.frame(
                "result_value",
                pl.DataFrame(
                    {
                        "result_set_id": [str(result_set)] * len(values),
                        "unit_id": [str(v["unit"]) for v in values],
                        "option_id": [None if v["option"] is None else str(v["option"]) for v in values],
                        "channel": [0] * len(values),
                        "measure": [v["measure"] for v in values],
                        "count_round": [0] * len(values),
                        "flags": [0] * len(values),
                        "value": [v["value"] for v in values],
                    },
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
                ),
            )
            for kind, body, url, source in (
                ("text", question["text"], PAGE, elections),
                ("link", None, ORDERS[order(number)], orders[order(number)]),
            ):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"{SOURCE}\x1fballot_question_text\x1f{contest}\x1f{kind}"),
                    contest_id=contest,
                    kind=kind,
                    lang="en",
                    body=body,
                    url=url,
                    source_id=source,
                )
        return cs.write()
