from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.offices import SOURCE as OFFICES_SOURCE
from ea.sources.gb.offices import query, terms
from ea.sources.gb.tenures import ENDED
from ea.store import connect

SOURCE = "gb.first_ministers"
VERSION = "1"
TITLE = "First Ministers of Scotland (P39 Q1362216) and Wales (P39 Q18996)"
OFFICES = (
    ("scotland", "GB-SCT", "First Minister of Scotland", query("Q1362216"), "Scottish Parliament"),
    ("wales", "GB-WLS", "First Minister of Wales", query("Q18996"), "Senedd Cymru"),
)
LEFT = {"Q333158": "died"}
RESIGNED = {"Q333820", "Q10652", "Q19397", "Q333141", "Q111840", "Q6767407", "Q7917285"}
ACTING = {"Q333807"}


def fetch() -> None:
    for _, _, _, url, _ in OFFICES:
        raw.fetch(OFFICES_SOURCE, url, suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="First Ministers of Scotland since 1999 and of Wales since 1999, with the deputy who acted in between",
            polity="GB",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Q1362216",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for code, polity, label, url, _ in OFFICES:
            cs.artifact(raw.cached(OFFICES_SOURCE, url), source)
            office = cs.key("gb.office", f"first-minister-{code}")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=cs.polity(polity), body_id=None, kind="head_of_government")
                cs.add("name", entity_id=office, text=label, lang="en", kind="official", is_preferred=True)
            for item, name, start, end, cause in terms(url):
                person: UUID = cs.key("wikidata", item)
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "tenure",
                    id=cs.key(f"{SOURCE}.tenure", f"{code}:{item}:{start}"),
                    person_id=person,
                    office_id=office,
                    body_id=None,
                    from_date=start,
                    from_precision="day",
                    to_date=end,
                    to_precision="day",
                    acquired_via="acting" if item in ACTING else "appointed",
                    ended_via=None
                    if end is None
                    else ENDED.get(cause or "") or LEFT.get(item) or ("resigned" if item in RESIGNED else "term_end"),
                )
        return cs.write()
