import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.lineage"
VERSION = "2"
TITLE = "Democratic Party (United States)"
PAGE = "https://en.wikipedia.org/wiki/{}"
WIKITEXT = "https://en.wikipedia.org/w/index.php?title={}&action=raw"
LICENSE = "Creative Commons Attribution-ShareAlike 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
FOUNDED = date(1828, 1, 8)
JACKSONIAN = "^[A-Z]{2}:(jacksonian|andrew-jackson)$"


def fetch() -> None:
    raw.fetch(SOURCE, WIKITEXT.format(quote(TITLE.replace(" ", "_"))), suffix=".wiki")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The states' Jacksonian parties as one Jacksonian party, merged into the Democratic Party at its founding in 1828",
            polity="US",
            kind="edit",
        )
        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url=PAGE.format(quote(TITLE.replace(" ", "_"))),
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(raw.cached(SOURCE, WIKITEXT.format(quote(TITLE.replace(" ", "_")))), source)
        known = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.party'").fetchall())
        jacksonian = cs.key("us.party", "jacksonian")
        if jacksonian in cs.minted:
            cs.add("party", id=jacksonian, kind="party", polity_id=cs.polity("US"))
            cs.add("name", entity_id=jacksonian, text="Jacksonian", lang="en", kind="official", is_preferred=True)
        for key, party in sorted(known.items()):
            if re.fullmatch(JACKSONIAN, key):
                cs.merge(party, jacksonian)
        cs.add(
            "party_relation",
            from_party_id=jacksonian,
            to_party_id=known["democratic-party"],
            kind="merged_into",
            date=FOUNDED,
            date_precision="day",
            source_id=source,
        )
        return cs.write()
