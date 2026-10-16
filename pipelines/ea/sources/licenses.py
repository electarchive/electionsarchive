from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "licenses"
VERSION = "1"
PUBLIC_RECORD = "Public record"
# Government publishers whose source rows were recorded with no license; their results are public records.
PUBLISHERS = {
    "Washington Secretary of State": PUBLIC_RECORD,
    "Wisconsin Elections Commission": PUBLIC_RECORD,
    "Minnesota Legislative Reference Library": PUBLIC_RECORD,
    "New York City Department of City Planning": f"{PUBLIC_RECORD}; free download with DCP's disclaimer of warranty",
}
COLUMNS = ("id", "kind", "publisher", "title", "url", "license", "license_url", "attribution", "notes")


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path | None:
    """Correct the license on source rows the import pipelines published without one."""
    with connect() as conn:
        rows = conn.execute(
            "select id, kind, publisher, title, url, license, license_url, attribution, notes from source "
            "where publisher = any(%s) and (license is null or license like 'No license stated%%')",
            [list(PUBLISHERS)],
        ).fetchall()
        if not rows:
            return None
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Record government sources published without a license as public records",
            polity=None,
            kind="correction",
        )
        for values in rows:
            row = dict(zip(COLUMNS, values, strict=True))
            row["license"] = PUBLISHERS[str(row["publisher"])]
            cs.add("source", **row)
        return cs.write()
