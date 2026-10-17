from pathlib import Path

from ea.changeset import Changeset
from ea.sources.tr.ysk import LICENSE as TURKISH_OFFICIAL_ACTS
from ea.store import connect

SOURCE = "licenses"
VERSION = "4"
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
ATTRIBUTIONS = {
    "City of Chicago": "This site provides applications using data that has been modified for use from its original source, "
    "www.cityofchicago.org, the official website of the City of Chicago. The City of Chicago makes no claims as to the "
    "content, accuracy, timeliness, or completeness of any of the data provided at this site. The data provided at this "
    "site is subject to change at any time. It is understood that the data provided at this site is being used at one's "
    "own risk.",
}
COLUMNS = ("id", "kind", "publisher", "title", "url", "license", "license_url", "attribution", "notes")


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path | None:
    """Correct the license on source rows the import pipelines published without one, and store the attribution
    notice a publisher's terms require."""
    with connect() as conn:
        changed = []
        for values in conn.execute(
            "select id, kind, publisher, title, url, license, license_url, attribution, notes from source "
            "where (publisher = any(%s) and (license is null or license like 'No license stated%%'))"
            " or publisher = any(%s)",
            [list(PUBLISHERS), list(ATTRIBUTIONS)],
        ).fetchall():
            row = dict(zip(COLUMNS, values, strict=True))
            fixed = {
                **row,
                "license": PUBLISHERS.get(values[2], values[5]),
                "attribution": ATTRIBUTIONS.get(values[2], values[7]),
            }
            if fixed != row:
                changed.append(fixed)
        if not changed:
            return None
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Record the license of sources published without one, and the attribution their terms require",
            polity=None,
            kind="correction",
        )
        for row in changed:
            cs.add("source", **row)
        return cs.write()
