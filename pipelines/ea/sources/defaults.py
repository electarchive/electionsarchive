import json
from pathlib import Path

from ea.apply import defaults
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "defaults"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def emit() -> Path:
    with connect() as conn:
        flipped, skipped = defaults(conn, None)
        PARSED.mkdir(parents=True, exist_ok=True)
        (PARSED / "skipped.json").write_text(json.dumps(skipped, indent=1))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="An official canvass becomes the default over a community, academic, or compendium set at the same "
            "or a later stage, taking its declared outcomes",
            polity=None,
            kind="correction",
        )
        for new, option, candidacy, kind, seats, notes in conn.execute(
            """select f.new, x.option_id, x.candidacy_id, x.kind, x.seats, x.notes
               from unnest(%s::uuid[], %s::uuid[]) f(old, new) join outcome x on x.result_set_id = f.old
               where not exists (select from outcome y where y.result_set_id = f.new and y.option_id = x.option_id
                                   and y.kind = x.kind)""",
            [[old for old, _, _ in flipped], [new for _, new, _ in flipped]],
        ).fetchall():
            cs.add(
                "outcome",
                result_set_id=new,
                option_id=option,
                candidacy_id=candidacy,
                kind=kind,
                seats=seats,
                notes=notes,
            )
        print(f"{len(flipped)} defaults to flip, {len(skipped)} skipped")
        return cs.write()
