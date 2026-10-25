from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "fr.lineage"
VERSION = "1"
SOCIALIST_PARTY = UUID("7957faec-7911-5ed2-ae79-fc05d2fe7940")
PARTI_SOCIALISTE = UUID("8788ac83-2ebc-584d-9382-d46868efd147")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="French party duplicates merged",
            polity="FR",
        )
        if conn.execute("select from entity_merge where entity_id = %s", [SOCIALIST_PARTY]).fetchone() is None:
            cs.merge(SOCIALIST_PARTY, PARTI_SOCIALISTE)
        if conn.execute("select from name where entity_id = %s and lang = 'en'", [PARTI_SOCIALISTE]).fetchone() is None:
            cs.add(
                "name",
                entity_id=PARTI_SOCIALISTE,
                text="Socialist Party",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        return cs.write()
