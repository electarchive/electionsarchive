from pathlib import Path

from ea.changeset import Changeset
from ea.sources.tr.ysk import LICENSE as TURKISH_OFFICIAL_ACTS
from ea.store import connect

SOURCE = "licenses"
VERSION = "3"
PUBLIC_RECORD = "Public record"
NEWS = "All rights reserved: a news article cited for one fact, not redistributed"
# Publishers whose source rows were recorded with no license: government results are public records, Turkish court
# decisions are free to reproduce once published, and news articles are cited for single facts, never redistributed.
PUBLISHERS = {
    "Cork Returning Officer": PUBLIC_RECORD,
    "Dublin County Returning Officer": PUBLIC_RECORD,
    "Galway Returning Officer": PUBLIC_RECORD,
    "Anayasa Mahkemesi": TURKISH_OFFICIAL_ACTS,
    "Washington Secretary of State": PUBLIC_RECORD,
    "Wisconsin Elections Commission": PUBLIC_RECORD,
    "Minnesota Legislative Reference Library": PUBLIC_RECORD,
    "New York City Department of City Planning": f"{PUBLIC_RECORD}; free download with DCP's disclaimer of warranty",
    "Conseil constitutionnel": "Freely reproducible with the Conseil constitutionnel's site cited as the source, without "
    "altering the text (legal notice)",
    "Hürriyet": NEWS,
    "Memurlar.net": NEWS,
    "Yeni Şafak": NEWS,
    "Son Dakika": NEWS,
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
        changed = [
            {**dict(zip(COLUMNS, values, strict=True)), "license": PUBLISHERS[values[2]]}
            for values in rows
            if PUBLISHERS[values[2]] != values[5]
        ]
        if not changed:
            return None
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Record the license of sources published without one",
            polity=None,
            kind="correction",
        )
        for row in changed:
            cs.add("source", **row)
        return cs.write()
