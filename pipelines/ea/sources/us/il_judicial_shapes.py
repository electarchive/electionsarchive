from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.il_judicial_shapes"
VERSION = "1"
URL = "https://illinoiscourts.gov/public/illinois-judicial-redistricting"

OLD = {
    2: "Boone, Carroll, DeKalb, DuPage, Jo Daviess, Kane, Kendall, Lake, Lee, McHenry, Ogle, Stephenson, Winnebago",
    3: "Bureau, Fulton, Grundy, Hancock, Henderson, Henry, Iroquois, Kankakee, Knox, LaSalle, Marshall, McDonough, Mercer, Peoria, Putnam, Rock Island, Stark, Tazewell, Warren, Whiteside, Will",
    4: "Adams, Brown, Calhoun, Cass, Champaign, Clark, Coles, Cumberland, De Witt, Douglas, Edgar, Ford, Greene, Jersey, Livingston, Logan, Macon, Macoupin, Mason, McLean, Menard, Morgan, Moultrie, Piatt, Pike, Sangamon, Schuyler, Scott, Vermilion, Woodford",
    5: "Alexander, Bond, Christian, Clay, Clinton, Crawford, Edwards, Effingham, Fayette, Franklin, Gallatin, Hamilton, Hardin, Jackson, Jasper, Jefferson, Johnson, Lawrence, Madison, Marion, Massac, Monroe, Montgomery, Perry, Pope, Pulaski, Randolph, Richland, St. Clair, Saline, Shelby, Union, Wabash, Washington, Wayne, White, Williamson",
}
NEW = {
    2: "DeKalb, Kane, Kendall, Lake, McHenry",
    3: "Bureau, DuPage, Grundy, Iroquois, Kankakee, LaSalle, Will",
    4: "Adams, Brown, Calhoun, Carroll, Cass, Ford, Fulton, Greene, Hancock, Henderson, Henry, Jersey, Jo Daviess, Knox, Lee, Livingston, Logan, Macoupin, Marshall, Mason, McDonough, McLean, Menard, Mercer, Morgan, Ogle, Peoria, Pike, Putnam, Rock Island, Sangamon, Schuyler, Scott, Stark, Stephenson, Tazewell, Warren, Whiteside, Winnebago, Woodford, Boone",
    5: "Alexander, Bond, Champaign, Christian, Clark, Clay, Clinton, Coles, Crawford, Cumberland, De Witt, Douglas, Edgar, Edwards, Effingham, Fayette, Franklin, Gallatin, Hamilton, Hardin, Jackson, Jasper, Jefferson, Johnson, Lawrence, Macon, Madison, Marion, Massac, Monroe, Montgomery, Moultrie, Perry, Piatt, Pope, Pulaski, Randolph, Richland, Saline, Shelby, St. Clair, Union, Vermilion, Wabash, Washington, Wayne, White, Williamson",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois appellate judicial districts before and after Public Act 102-0011, shaped as unions of whole counties",
            polity="US",
        )
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        courts = cs.source("Illinois Supreme Court", "Illinois judicial redistricting")
        cs.add(
            "source",
            id=courts,
            kind="official_body",
            publisher="Illinois Supreme Court",
            title="Illinois judicial redistricting",
            url=URL,
            license="Public record",
        )
        counties = {
            text.removesuffix(" County, IL"): code
            for code, text in conn.execute(
                """select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
                   join name n on n.entity_id = a.id and n.text like '%% County, IL'
                   where a.scheme_code = 'us.county' and exists (select 1 from area_geometry g where g.area_id = a.id)"""
            ).fetchall()
        }
        areas = dict(
            conn.execute(
                """select split_part(ac.code, '-', 2)::int, a.id::text from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code = 'us.judicial_district' and ac.code like 'IL:judicial-%%'"""
            ).fetchall()
        )
        for valid_from, valid_to, plan in [(None, date(2022, 1, 1), OLD), (date(2022, 1, 1), None, NEW)]:
            for number in [1, *plan]:
                names = ["Cook"] if number == 1 else plan[number].split(", ")
                codes = [counties[name] for name in names]
                geom, point = conn.execute(
                    """with latest as (
                           select distinct on (ac.code) ac.code, g.geom from area_geometry g
                           join area_code ac on ac.area_id = g.area_id
                           where ac.code = any(%s) order by ac.code, g.valid_from desc nulls last)
                       select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))), 'hex'),
                              encode(st_asewkb(st_pointonsurface(st_makevalid(st_union(geom)))), 'hex') from latest""",
                    [codes],
                ).fetchone()
                cs.add(
                    "area_geometry",
                    area_id=areas[number],
                    valid_from=valid_from,
                    valid_from_precision="day" if valid_from else "unknown",
                    valid_to=valid_to,
                    valid_to_precision="day",
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=census,
                )
        return cs.write()
