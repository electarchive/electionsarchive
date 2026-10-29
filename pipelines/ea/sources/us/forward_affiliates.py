from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.forward_affiliates"
VERSION = "1"
TITLE = "Forward Party (United States)"
PAGE = "https://en.wikipedia.org/wiki/Forward_Party_(United_States)"
LICENSE = "Creative Commons Attribution-ShareAlike 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
STATES = ("CO", "PA", "VA")


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")


def emit() -> Path:
    artifact = raw.cached(SOURCE, PAGE)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The Forward Party's Colorado, Pennsylvania, and Virginia entities as regional branches of the national party",
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
            url=PAGE,
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(artifact, source)
        parent = cs.key("us.party", "forward-party")
        for state in STATES:
            cs.add(
                "party_affiliation",
                party_id=cs.key("us.party", f"{state}:forward-party"),
                parent_party_id=parent,
                kind="regional_branch",
                valid_from=None,
            )
        return cs.write()
