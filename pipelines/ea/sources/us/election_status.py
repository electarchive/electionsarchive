from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.election_status"
VERSION = "1"
PIPELINES = [
    "us.az_districts_2026",
    "us.az_fire_2026",
    "us.ca_districts_2026",
    "us.fl_districts_2026",
    "us.ia_districts_2026",
    "us.nc_swcd_2026",
    "us.ne_districts_2026",
    "us.nj_fire_2026",
    "us.nv_districts_2026",
    "us.oh_questions_2026",
    "us.or_swcd_2026",
    "us.sc_swcd_2026",
    "us.wa_districts_2026",
]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mark elections whose date has passed as held instead of scheduled, for the state district and measure pipelines",
            polity="US",
            kind="correction",
        )
        rows = conn.execute(
            """select e.id, e.polity_id, e.parent_id, e.kind, e.date_from, e.date_to, e.date_precision, e.original_calendar,
                      e.original_text, e.rerun_of_id
               from election e join changeset c on c.id = e.changeset_id
               where c.pipeline = any(%s) and e.status = 'scheduled' and e.date_from <= current_date
               order by e.id""",
            [PIPELINES],
        ).fetchall()
        for id, polity, parent, kind, date_from, date_to, precision, calendar, text, rerun in rows:
            cs.add(
                "election",
                id=id,
                polity_id=polity,
                parent_id=parent,
                kind=kind,
                date_from=date_from,
                date_to=date_to,
                date_precision=precision,
                original_calendar=calendar,
                original_text=text,
                rerun_of_id=rerun,
                status="held",
            )
        print(f"{len(rows)} elections")
        return cs.write()
