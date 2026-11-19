from datetime import date
from pathlib import Path
from uuid import uuid7

from ea.changeset import Changeset, slug
from ea.sources.tr.reforms import ILCE_CHANGES
from ea.sources.tr.ysk import PROVINCES
from ea.store import connect

SOURCE = "tr.belde_ilce"
VERSION = "2"


def emit() -> Path | None:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="End the belde territory and parent of beldes that became ilçes in 2014 on the day of the split",
            polity="TR",
            kind="correction",
        )
        changed = 0
        for day, _, _, kind, plate, source, _, targets, _ in ILCE_CHANGES:
            for town in targets if kind == "split" else []:
                if town == source:
                    continue
                province = PROVINCES[int(plate) - 1]
                key = "-".join([slug(province), "merkez" if source == "Merkez" else slug(source), slug(town)])
                found = conn.execute(
                    """select (select entity_id from external_id where scheme = 'tr.belde' and value = %(key)s),
                              (select entity_id from external_id where scheme = 'tr.polity' and value = %(key)s)""",
                    {"key": key},
                ).fetchone()
                if found is None or None in found:
                    continue
                area, polity = found
                end = date.fromisoformat(day)
                if (
                    conn.execute(
                        "select from polity_territory where polity_id = %s and valid && daterange(null, %s)",
                        [polity, end],
                    ).fetchone()
                    is None
                ):
                    cs.add("polity_territory", polity_id=polity, area_id=area, valid_to=end)
                    changed += 1
                for parent, start, start_precision, columns in conn.execute(
                    """select parent_id, valid_from, valid_from_precision,
                              to_jsonb(p) - 'changeset_id' - 'recorded_from'
                       from area_parent p where area_id = %s and valid @> %s""",
                    [area, end],
                ).fetchall():
                    cs.add(
                        "row_drop",
                        id=uuid7(),
                        reason=f"{town} became an ilçe on {day}, so its belde area ends then",
                        relation="area_parent",
                        key=columns,
                    )
                    cs.add(
                        "area_parent",
                        area_id=area,
                        parent_id=parent,
                        valid_from=start,
                        valid_from_precision=start_precision,
                        valid_to=end,
                    )
                    changed += 1
        print(f"{changed} rows")
        return cs.write() if changed else None
