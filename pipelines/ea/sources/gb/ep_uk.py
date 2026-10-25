from datetime import date
from pathlib import Path

import fastexcel

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb import ep_fptp
from ea.store import connect

SOURCE = "gb.ep_uk"
VERSION = "1"
DELEGATION = [
    (date(1979, 6, 7), date(1984, 6, 14), 81),
    (date(1984, 6, 14), date(1989, 6, 15), 81),
    (date(1989, 6, 15), date(1994, 6, 9), 81),
    (date(1994, 6, 9), date(1999, 6, 10), 87),
    (date(1999, 6, 10), date(2004, 6, 10), 87),
    (date(2004, 6, 10), date(2009, 6, 4), 78),
    (date(2009, 6, 4), date(2014, 5, 22), 72),
    (date(2014, 5, 22), date(2019, 5, 23), 73),
    (date(2019, 5, 23), date(2020, 2, 1), 73),
]
WESSEX = "gb/1979-06-07-european-parliament/wessex"
WESSEX_CAVEAT = (
    "Cadmus, Wikipedia's constituency article, and David Boothroyd's election results pages all give Labour 42,910 "
    "votes and the Liberals 31,220 here. The Commons Library's Great Britain totals for 1979 (CBP-7529), and "
    "Wikipedia's national totals, are 11,690 lower for Labour and 12,622 higher for the Liberals than the sum of the "
    "constituencies, close to what swapping these two figures would make. The figures stay as the constituency "
    "sources print them until a printed primary source is checked."
)


def fetch() -> None:
    raw.cached(ep_fptp.SOURCE, ep_fptp.BRIEFING)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The United Kingdom's seats in the European Parliament at each election from 1979 to 2019, from "
            "the Commons Library's CBP-7529, and a caveat on the 1979 Wessex result",
            polity="GB",
        )
        briefing = cs.source("House of Commons Library", ep_fptp.BRIEFING_TITLE)
        if briefing in cs.minted:
            raise ValueError("publish gb.ep_fptp first")
        sheet = raw.cached(ep_fptp.SOURCE, ep_fptp.BRIEFING)
        cs.artifact(sheet, briefing)
        rows = fastexcel.read_excel(sheet.path).load_sheet("1. EP-GB", header_row=None).to_polars().rows()
        column = rows[1].index("Total UK MEPs")
        printed = {int(row[0]): int(row[column]) for row in rows[2:] if row[0] is not None and row[0].isdigit()}
        expected = {start.year: seats for start, _, seats in DELEGATION}
        if printed != expected:
            raise ValueError(f"CBP-7529 gives {printed}, not {expected}")
        parliament = cs.key("eu.body", "european-parliament")
        kingdom = cs.area("gb.nation", "GBR")
        if parliament in cs.minted:
            raise ValueError("publish eu.ep_cadmus first")
        for start, end, seats in DELEGATION:
            cs.add(
                "body_apportionment",
                body_id=parliament,
                area_id=kingdom,
                seats=seats,
                valid_from=start,
                valid_to=end,
            )
        cs.add("citation", entity_id=parliament, source_id=briefing)
        (wessex,) = conn.execute(
            """select r.id from slug s join result_set r on r.contest_id = s.entity_id and r.is_default
               where s.path = %s""",
            [WESSEX],
        ).fetchone()
        cs.add("note", entity_id=wessex, kind="caveat", body_md=WESSEX_CAVEAT)
        cs.add("citation", entity_id=wessex, source_id=briefing)
        return cs.write()
