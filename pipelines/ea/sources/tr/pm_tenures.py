from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.offices import SOURCE as OFFICES_SOURCE
from ea.sources.gb.offices import query, terms
from ea.sources.gb.tenures import ENDED
from ea.store import connect

SOURCE = "tr.pm_tenures"
VERSION = "1"
TITLE = "Prime Ministers of Turkey (P39 Q2430415)"
URL = query("Q2430415")


def fetch() -> None:
    raw.fetch(OFFICES_SOURCE, URL, suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Prime Ministers of Turkey, 1920 to 2018, from Wikidata",
            polity="TR",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Q2430415",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(OFFICES_SOURCE, URL), source)
        office = cs.key("tr.office", "prime-minister")
        for item, name, start, end, cause in terms(URL):
            person: UUID = cs.key("wikidata", item)
            if person in cs.minted:
                cs.add("person", id=person)
                cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"{item}:{start}"),
                person_id=person,
                office_id=office,
                body_id=None,
                from_date=start,
                from_precision="day",
                to_date=end,
                to_precision="day",
                acquired_via="appointed",
                ended_via=None if end is None else ENDED.get(cause or "") or "term_end",
            )
        return cs.write()
