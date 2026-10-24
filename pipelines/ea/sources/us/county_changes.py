import csv
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID, uuid7

import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.county_changes"
VERSION = "2"
REGIONS = "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_county_500k.zip"
NOTICE = "https://www2.census.gov/geo/pdfs/reference/ct_county_equiv_change.pdf"
CROSSWALK = "https://www2.census.gov/geo/docs/reference/ct_change/ct_cou_to_cousub_crosswalk.txt"
HISTORY = "https://www.census.gov/programs-surveys/geography/technical-documentation/county-changes.html"
REGIONS_FROM = date(2022, 1, 1)
STARTS = {
    "02068": date(1990, 12, 7),
    "02282": date(1992, 9, 22),
    "02230": date(2007, 6, 20),
    "02105": date(2007, 6, 20),
    "02198": date(2008, 5, 19),
    "02195": date(2008, 6, 1),
    "02275": date(2008, 6, 1),
    "02158": date(2015, 7, 1),
    "02063": date(2019, 1, 2),
    "02066": date(2019, 1, 2),
}
FORMER = {
    "02231": ("Skagway-Yakutat-Angoon Census Area", date(1992, 9, 22)),
    "02232": ("Skagway-Hoonah-Angoon Census Area", date(2007, 6, 20)),
    "02201": ("Prince of Wales-Outer Ketchikan Census Area", date(2008, 5, 19)),
    "02280": ("Wrangell-Petersburg Census Area", date(2008, 6, 1)),
    "02270": ("Wade Hampton Census Area", date(2015, 7, 1)),
    "02261": ("Valdez-Cordova Census Area", date(2019, 1, 2)),
}
MERGED = {"46001": "Armstrong County", "46131": "Washabaugh County"}


def ct_regions() -> dict[str, str]:
    text = raw.cached(SOURCE, CROSSWALK).path.read_text(encoding="utf-8-sig")
    return {
        re.sub(r"[^a-z]", "", re.sub(r" town$", "", row[8].lower())): f"09{row[3]}"
        for row in csv.reader(io.StringIO(text), delimiter="|")
        if len(row) > 8 and row[3].isdigit()
    }


def ct_cousub_regions() -> dict[str, str]:
    text = raw.cached(SOURCE, CROSSWALK).path.read_text(encoding="utf-8-sig")
    return {
        row[6]: f"09{row[3]}"
        for row in csv.reader(io.StringIO(text), delimiter="|")
        if len(row) > 6 and row[3].isdigit()
    }


def fetch() -> None:
    raw.fetch(SOURCE, REGIONS, suffix=".zip")
    raw.fetch(SOURCE, NOTICE, suffix=".pdf")
    raw.fetch(SOURCE, HISTORY, suffix=".html")
    raw.fetch(SOURCE, CROSSWALK, suffix=".txt")


def emit() -> Path:
    with zipfile.ZipFile(raw.cached(SOURCE, REGIONS).path) as z:
        parts = {name.rsplit(".", 1)[1].lower(): z.read(name) for name in z.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    regions = [
        (record.as_dict(), json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        if record["STATEFP"] == "09"
    ]
    if len(regions) != 9:
        raise ValueError(f"expected nine planning regions, found {len(regions)}")

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Dated county-equivalents: Connecticut's nine planning regions from 2022, and Alaska's census area and borough changes since 1990, "
            "and South Dakota's Armstrong (merged into Dewey) and Washabaugh (merged into Jackson) counties",
            polity="US",
        )
        census = cs.source("U.S. Census Bureau", "Changes to Counties and County Equivalent Entities")
        cs.add(
            "source",
            id=census,
            kind="official_body",
            publisher="U.S. Census Bureau",
            title="Changes to Counties and County Equivalent Entities",
            url=HISTORY,
            license="Public domain",
        )
        for url in (REGIONS, NOTICE, HISTORY, CROSSWALK):
            cs.artifact(raw.cached(SOURCE, url), census)

        ct_state_area = cs.area("us.state", "09")
        ct_state_unit = cs.unit("us.state", "09")
        ct_polity = cs.polity("US-CT")
        ak_state_area = cs.area("us.state", "02")
        ak_state_unit = cs.unit("us.state", "02")
        ak_polity = cs.polity("US-AK")

        shapes = dict(
            conn.execute(
                """select c, encode(st_asewkb(geom), 'hex') || '|' || encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select c, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(g), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[]) as f(c, g)) s""",
                [[str(r["GEOID"]) for r, _ in regions], [g for _, g in regions]],
            ).fetchall()
        )
        events: list[tuple[str, date, list[tuple[UUID, str]]]] = []

        existing = {
            code: row
            for code, *row in conn.execute(
                """select code, id, polity_id, parent_id, counted_with_id, area_relation::text, area_id, kind::text
                   from reporting_unit where scheme_code = 'us.county' and code ~ '^(02|09)'"""
            ).fetchall()
        }

        def restage(code: str, valid_from: date | None, valid_to: date | None) -> None:
            id, polity_id, parent_id, counted_with_id, relation, area_id, kind = existing[code]
            cs.add(
                "reporting_unit",
                id=id,
                polity_id=polity_id,
                scheme_code="us.county",
                code=code,
                parent_id=parent_id,
                counted_with_id=counted_with_id,
                area_relation=relation,
                area_id=area_id,
                kind=kind,
                valid_from=valid_from,
                valid_to=valid_to,
            )

        dissolved: list[tuple[UUID, str]] = []
        for code in sorted(existing):
            if code.startswith("090"):
                restage(code, None, REGIONS_FROM)
                dissolved.append((existing[code][5], "from"))
            elif code in STARTS:
                restage(code, STARTS[code], None)

        created: list[tuple[UUID, str]] = []
        for record, _ in sorted(regions, key=lambda r: r[0]["GEOID"]):
            code = str(record["GEOID"])
            area = cs.area("us.county", code)
            cs.add("area", id=area, scheme_code="us.county")
            cs.add("area_code", area_id=area, code=code, valid_from=REGIONS_FROM)
            cs.add("area_parent", area_id=area, parent_id=ct_state_area, valid_from=REGIONS_FROM)
            cs.add(
                "name",
                entity_id=area,
                text=f"{record['NAMELSAD']}, CT",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("name", entity_id=area, text=record["NAME"], lang="en", kind="short", is_preferred=False)
            geom, point = shapes[code].split("|")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=REGIONS_FROM,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.county", code),
                polity_id=ct_polity,
                scheme_code="us.county",
                code=code,
                parent_id=ct_state_unit,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=REGIONS_FROM,
            )
            created.append((area, "to"))
        events.append(("created", REGIONS_FROM, created + dissolved))

        for code, (label, ended) in FORMER.items():
            unit = cs.unit("us.former_county", f"AK:{code}")
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=ak_polity,
                scheme_code="us.former_county",
                code=f"AK:{code}",
                parent_id=ak_state_unit,
                area_relation="within",
                area_id=ak_state_area,
                kind="standard",
                valid_from=None,
                valid_to=ended,
            )
            cs.add("name", entity_id=unit, text=label, lang="en", kind="official", is_preferred=True)

        sd_state_unit = cs.unit("us.state", "46")
        for code, valid_from, valid_to, area in conn.execute(
            """select c.code, c.valid_from, c.valid_to, c.area_id from area_code c join area a on a.id = c.area_id
               where a.scheme_code = 'us.county' and c.code = any(%s) and c.valid_to is not null""",
            [list(MERGED)],
        ).fetchall():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.county", code),
                polity_id=cs.polity("US-SD"),
                scheme_code="us.county",
                code=code,
                parent_id=sd_state_unit,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=valid_from,
                valid_to=valid_to,
            )
            print(f"{MERGED[code]}: {code} {valid_from} to {valid_to}")

        region_of = ct_regions()
        unmatched: list[str] = []
        for code, unit in conn.execute(
            """select u.code, u.id from reporting_unit u join polity p on p.id = u.polity_id
               where p.iso_code = 'US-CT' and u.scheme_code = 'us.return_unit' and u.code like 'CT:%:town:%'"""
        ).fetchall():
            region = region_of.get(code.rsplit(":", 1)[1])
            if region is None:
                unmatched.append(code)
                continue
            cs.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=cs.area("us.county", region),
                valid_from=REGIONS_FROM,
                approximate=False,
                partial=False,
            )
        if unmatched:
            print(f"unmatched towns: {unmatched}")

        for kind, day, members in events:
            event = uuid7()
            cs.add("area_event", id=event, kind=kind, date=day, source_id=census, notes=None)
            for area, role in members:
                cs.add("area_event_member", event_id=event, area_id=area, role=role)
        return cs.write()
