from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "fr.area_dedupe"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="French circonscription areas recorded twice merged",
            polity="FR",
        )
        pairs = conn.execute(
            """select x.area_id, y.area_id,
                      (select count(*) from contest where district_area_id = x.area_id),
                      (select count(*) from contest where district_area_id = y.area_id)
               from area_code x
               join area_code y on x.code = y.code and x.area_id < y.area_id and x.valid && y.valid
               join area a on a.id = x.area_id and a.scheme_code = 'fr.circonscription'
               join area b on b.id = y.area_id and b.scheme_code = 'fr.circonscription'
               where not exists (select from entity_merge where entity_id in (x.area_id, y.area_id))
               group by x.area_id, y.area_id, x.code
               order by x.code"""
        ).fetchall()
        for first, second, first_contests, second_contests in pairs:
            if first_contests >= second_contests:
                cs.merge(second, first)
            else:
                cs.merge(first, second)
        return cs.write()
