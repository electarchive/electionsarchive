from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "fr.people_merged"
VERSION = "1"
SURVIVOR = UUID("4d5dd7c8-e930-5abd-9f93-316e83a85322")
DUPLICATES = (UUID("2e42742c-8e72-5e32-8ce7-ab7b7ece3394"), UUID("8cb98311-ca77-58f4-a48b-3790becd8b07"))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Marine Le Pen's three person records merged into one",
            polity="FR",
        )
        for duplicate in DUPLICATES:
            if conn.execute("select from entity_merge where entity_id = %s", [duplicate]).fetchone() is None:
                cs.merge(duplicate, SURVIVOR)
        return cs.write()
