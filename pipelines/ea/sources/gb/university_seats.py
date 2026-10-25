from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.university_seats"
VERSION = "2"
UNION = date(1801, 1, 1)
PARTITION = date(1922, 12, 6)
REFORM_1868 = date(1868, 11, 17)
REFORM_1918 = date(1918, 12, 14)
ABOLITION = date(1950, 2, 23)
SEATS = {
    "eng-oxford-university": (
        "Oxford University",
        UNION,
        ABOLITION,
        (
            "Returned members to the Commons of the United Kingdom from the Union of 1801 (the Union with Ireland Act "
            "1800) until the Representation of the People Act 1948 abolished the university seats at the 1950 general "
            "election."
        ),
    ),
    "eng-cambridge-university": (
        "Cambridge University",
        UNION,
        ABOLITION,
        (
            "Returned members to the Commons of the United Kingdom from the Union of 1801 (the Union with Ireland Act "
            "1800) until the Representation of the People Act 1948 abolished the university seats at the 1950 general "
            "election."
        ),
    ),
    "irl-dublin-university": (
        "Dublin University",
        UNION,
        PARTITION,
        (
            "Returned members to the Commons of the United Kingdom from the Union of 1801 (the Union with Ireland Act "
            "1800) until the Irish Free State left the United Kingdom on 6 December 1922."
        ),
    ),
    "eng-london-university": (
        "London University",
        REFORM_1868,
        ABOLITION,
        (
            "Created by the Representation of the People Act 1867, first returned at the 1868 general election, and "
            "abolished by the Representation of the People Act 1948 at the 1950 general election."
        ),
    ),
    "sct-edinburgh-and-st-andrew-s-universities": (
        "Edinburgh and St Andrew's Universities",
        REFORM_1868,
        REFORM_1918,
        (
            "Created by the Representation of the People (Scotland) Act 1868 and merged into the Combined Scottish "
            "Universities by the Representation of the People Act 1918."
        ),
    ),
    "sct-aberdeen-and-glasgow-universities": (
        "Aberdeen and Glasgow Universities",
        REFORM_1868,
        REFORM_1918,
        (
            "Created by the Representation of the People (Scotland) Act 1868 and merged into the Combined Scottish "
            "Universities by the Representation of the People Act 1918."
        ),
    ),
    "sct-combined-scottish-universities": (
        "Combined Scottish Universities",
        REFORM_1918,
        ABOLITION,
        (
            "Created by the Representation of the People Act 1918 and abolished by the Representation of the People "
            "Act 1948 at the 1950 general election."
        ),
    ),
    "eng-combined-english-universities": (
        "Combined English Universities",
        REFORM_1918,
        ABOLITION,
        (
            "Created by the Representation of the People Act 1918 and abolished by the Representation of the People "
            "Act 1948 at the 1950 general election."
        ),
    ),
    "wls-university-of-wales": (
        "University of Wales",
        REFORM_1918,
        ABOLITION,
        (
            "Created by the Representation of the People Act 1918 and abolished by the Representation of the People "
            "Act 1948 at the 1950 general election."
        ),
    ),
    "irl-national-university-of-ireland": (
        "National University of Ireland",
        REFORM_1918,
        PARTITION,
        (
            "Created by the Representation of the People Act 1918; it left the Commons of the United Kingdom with the "
            "Irish Free State on 6 December 1922."
        ),
    ),
    "irl-queen-s-university-of-belfast": (
        "Queen's University of Belfast",
        REFORM_1918,
        PARTITION,
        (
            "Created by the Representation of the People Act 1918. From 6 December 1922 the seat continues under "
            "Northern Ireland."
        ),
    ),
    "nir-queen-s-university-of-belfast": (
        "Queen's University of Belfast",
        PARTITION,
        ABOLITION,
        (
            "The Representation of the People Act 1918 seat after the partition of 6 December 1922, abolished by the "
            "Representation of the People Act 1948 at the 1950 general election."
        ),
    ),
}
COUNTRIES = {"eng": "ENG", "sct": "SCT", "wls": "WLS", "irl": "IRL", "nir": "NIR"}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The Commons university constituencies as areas without shapes, from the Union of 1801 to their "
            "abolition in 1950",
            polity="GB",
        )
        for code, (label, start, end, body) in SEATS.items():
            area = cs.area("gb.pcon", code, on=start)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.pcon")
            cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
            cs.add("name", entity_id=area, text=label, lang="en", kind="official", is_preferred=True)
            cs.add("note", entity_id=area, kind="context", body_md=body)
            parent = cs.area("gb.country", COUNTRIES[code[:3]], on=start)
            if parent in cs.minted:
                raise ValueError(f"no gb.country {COUNTRIES[code[:3]]} on {start}")
            cs.add("area_parent", area_id=area, parent_id=parent, valid_from=start, valid_to=end)
        return cs.write()
