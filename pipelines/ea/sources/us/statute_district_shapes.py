from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.statute_district_shapes"
VERSION = "1"

FL_CIRCUITS = {
    1: "Escambia, Okaloosa, Santa Rosa, Walton",
    2: "Franklin, Gadsden, Jefferson, Leon, Liberty, Wakulla",
    3: "Columbia, Dixie, Hamilton, Lafayette, Madison, Suwannee, Taylor",
    4: "Clay, Duval, Nassau",
    5: "Citrus, Hernando, Lake, Marion, Sumter",
    6: "Pasco, Pinellas",
    7: "Flagler, Putnam, St. Johns, Volusia",
    8: "Alachua, Baker, Bradford, Gilchrist, Levy, Union",
    9: "Orange, Osceola",
    10: "Hardee, Highlands, Polk",
    11: "Miami-Dade",
    12: "DeSoto, Manatee, Sarasota",
    13: "Hillsborough",
    14: "Bay, Calhoun, Gulf, Holmes, Jackson, Washington",
    15: "Palm Beach",
    16: "Monroe",
    17: "Broward",
    18: "Brevard, Seminole",
    19: "Indian River, Martin, Okeechobee, St. Lucie",
    20: "Charlotte, Collier, Glades, Hendry, Lee",
}
FL_BEFORE_2023 = {
    1: [1, 2, 3, 4, 8, 14],
    2: [6, 10, 12, 13, 20],
    3: [11, 16],
    4: [15, 17, 19],
    5: [5, 7, 9, 18],
}
FL_FROM_2023 = {
    1: [1, 2, 3, 8, 14],
    2: [6, 12, 13],
    3: [11, 16],
    4: [15, 17, 19],
    5: [4, 5, 7, 18],
    6: [9, 10, 20],
}
MD = {
    1: "Caroline, Cecil, Dorchester, Kent, Queen Anne's, Somerset, Talbot, Wicomico, Worcester",
    2: "Baltimore, Harford",
    3: "Allegany, Carroll, Frederick, Garrett, Howard, Washington",
    4: "Prince George's",
    5: "Anne Arundel, Calvert, Charles, St. Mary's",
    6: "Baltimore city",
    7: "Montgomery",
}
AZ = {
    "coa-1-outside-maricopa": "Yuma, La Paz, Mohave, Coconino, Yavapai, Navajo, Apache",
    "coa-2-outside-pima": "Pinal, Cochise, Santa Cruz, Greenlee, Graham, Gila",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida appellate districts, Maryland appellate circuits, and Arizona Court of Appeals divisions as defined by statute and constitution, shaped as unions of whole counties",
            polity="US",
        )
        sources = {}
        for state, publisher, title, url in [
            (
                "FL",
                "Florida Legislature",
                "Florida Statutes, chapters 26 and 35 (2021 and current)",
                "http://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0000-0099/0035/0035ContentsIndex.html",
            ),
            (
                "MD",
                "Maryland State Archives",
                "Maryland Constitution, Article IV, Section 14",
                "https://msa.maryland.gov/msa/mdmanual/43const/html/04art4.html",
            ),
            (
                "AZ",
                "Arizona Legislature",
                "Arizona Revised Statutes 12-120 and 12-120.02",
                "https://www.azleg.gov/ars/12/00120.htm",
            ),
        ]:
            sources[state] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[state],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public record",
            )
        counties = {}
        for code, text in conn.execute(
            """select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
               join name n on n.entity_id = a.id and n.text ~ '^[^,]+ (County|city), (FL|MD|AZ)$'
               where a.scheme_code = 'us.county' and exists (select 1 from area_geometry g where g.area_id = a.id)"""
        ).fetchall():
            name, state = text.rsplit(", ", 1)
            counties[(state, name.removesuffix(" County"))] = code
        counties[("FL", "Miami-Dade")] = counties[("FL", "Dade")]
        areas = dict(
            conn.execute(
                """select ac.code, a.id::text from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code = 'us.judicial_district'
                     and ac.code ~ '^(FL:dca-|MD:appellate-|AZ:coa-)'"""
            ).fetchall()
        )
        jobs = []
        for plan, valid_from, valid_to in [
            (FL_BEFORE_2023, None, date(2023, 1, 1)),
            (FL_FROM_2023, date(2023, 1, 1), None),
        ]:
            for district, circuits in plan.items():
                names = [name for circuit in circuits for name in FL_CIRCUITS[circuit].split(", ")]
                jobs.append(("FL", f"FL:dca-{district}", names, valid_from, valid_to))
        jobs += [("MD", f"MD:appellate-{number}", names.split(", "), None, None) for number, names in MD.items()]
        jobs += [("AZ", f"AZ:{code}", names.split(", "), None, None) for code, names in AZ.items()]
        for state, code, names, valid_from, valid_to in jobs:
            members = [counties[(state, name)] for name in names]
            geom, point = conn.execute(
                """with latest as (
                       select distinct on (ac.code) ac.code, g.geom from area_geometry g
                       join area_code ac on ac.area_id = g.area_id
                       where ac.code = any(%s) order by ac.code, g.valid_from desc nulls last)
                   select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))), 'hex'),
                          encode(st_asewkb(st_pointonsurface(st_makevalid(st_union(geom)))), 'hex') from latest""",
                [members],
            ).fetchone()
            cs.add(
                "area_geometry",
                area_id=areas[code],
                valid_from=valid_from,
                valid_from_precision="day" if valid_from else "unknown",
                valid_to=valid_to,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[state],
            )
        return cs.write()
