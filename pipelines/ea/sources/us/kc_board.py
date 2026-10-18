from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.kc_board"
VERSION = "4"
CAVEAT = (
    "MEDSL's Kansas City jurisdiction is the Kansas City Board of Election Commissioners, which runs elections only "
    "in the Jackson County part of the city; Clay, Platte, and Cass counties count their parts of Kansas City "
    "themselves. This unit lies within Kansas City and within Jackson County, and does not cover the whole city."
)


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas City, Missouri's election board units, filed within Missouri, move within Jackson "
            "County with a partial membership in Kansas City, so their rollups to the city are marked not exact, "
            "and the units under them move within Jackson County, since the board covers only the Jackson County "
            "part of the city",
            polity="US",
            kind="correction",
        )
        city, county, unit = conn.execute(
            """select (select a.id from area a join area_code c on c.area_id = a.id
                       where a.scheme_code = 'us.place' and c.code = '2938000'),
                      u.area_id, u.id
               from reporting_unit u where u.scheme_code = 'us.county' and u.code = '29095'"""
        ).fetchone() or (None, None, None)
        if city is None or county is None:
            raise ValueError("no Kansas City place area or Jackson County unit")
        moved = 0
        for row in conn.execute(
            """select id, polity_id, scheme_code, code, counted_with_id, kind, valid_from, valid_from_precision,
                      valid_to, valid_to_precision,
                      exists (select from note n where n.entity_id = u.id and n.kind = 'caveat' and n.body_md = %s)
               from reporting_unit u where code = '2938000' and scheme_code in ('us.place', 'us.aggregate')""",
            [CAVEAT],
        ).fetchall():
            id, polity, scheme, code, counted_with, kind, valid_from, from_precision, valid_to, to_precision, noted = (
                row
            )
            cs.add(
                "reporting_unit",
                id=id,
                polity_id=polity,
                scheme_code=scheme,
                code=code,
                parent_id=unit,
                counted_with_id=counted_with,
                area_relation="within",
                area_id=county,
                kind=kind,
                valid_from=valid_from,
                valid_from_precision=from_precision,
                valid_to=valid_to,
                valid_to_precision=to_precision,
            )
            cs.add(
                "reporting_unit_area", unit_id=id, area_id=city, valid_from=valid_from, valid_to=valid_to, partial=True
            )
            if not noted:
                cs.add("note", entity_id=id, kind="caveat", body_md=CAVEAT)
            moved += 1
        if moved != 2:
            raise ValueError(f"expected Kansas City's place and aggregate units, found {moved}")
        children = 0
        for row in conn.execute(
            """select c.id, c.polity_id, c.scheme_code, c.code, c.parent_id, c.counted_with_id, c.kind, c.valid_from,
                      c.valid_from_precision, c.valid_to, c.valid_to_precision
               from reporting_unit c join reporting_unit p on p.id = c.parent_id
               where p.scheme_code = 'us.place' and p.code = '2938000'"""
        ).fetchall():
            id, polity, scheme, code, parent, counted_with, kind, valid_from, from_precision, valid_to, to_precision = (
                row
            )
            cs.add(
                "reporting_unit",
                id=id,
                polity_id=polity,
                scheme_code=scheme,
                code=code,
                parent_id=parent,
                counted_with_id=counted_with,
                area_relation="within",
                area_id=county,
                kind=kind,
                valid_from=valid_from,
                valid_from_precision=from_precision,
                valid_to=valid_to,
                valid_to_precision=to_precision,
            )
            children += 1
        print(f"Kansas City units moved within Kansas City and Jackson County: {moved}")
        print(f"units under Kansas City's board moved within Jackson County: {children}")
        return cs.write()
