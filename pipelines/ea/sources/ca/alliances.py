from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "ca.alliances"
VERSION = "1"
URL = "https://www.ourcommons.ca/procedure/procedure-and-practice-4/app12-e.html"
CAPTURE = f"https://web.archive.org/web/20251203032006if_/{URL}"
PUBLISHER = "House of Commons of Canada"
TITLE = (
    "House of Commons Procedure and Practice, Fourth Edition, 2025: Appendix 12, General Election Results Since 1867"
)
NOTES = (
    'Note 5 to the table: "Liberal-Conservative and Conservative members always sat together in the House of Commons '
    'and, if applicable, in the same government." The table lists Liberal-Conservative Members in every Parliament '
    "from the 1st (1867) to the 12th (1911). Retrieved from the Internet Archive's capture of December 3, 2025, "
    "because ourcommons.ca refuses automated requests."
)
CAUCUSES = [("Conservative", ["Conservative", "Liberal-Conservative"], 1867, 1911)]


def fetch() -> None:
    raw.fetch(SOURCE, CAPTURE, suffix=".html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Canadian caucuses whose members stood under more than one label: Conservatives and "
                "Liberal-Conservatives as one Conservative alliance at each general election from 1867 to 1911"
            ),
            polity="CA",
            kind="edit",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Speaker's permission to reproduce, non-commercial",
            license_url="https://www.ourcommons.ca/en/important-notices",
            notes=NOTES,
        )
        cs.artifact(raw.cached(SOURCE, CAPTURE), source)
        generals = conn.execute(
            "select split_part(value, ':', 1)::date, entity_id from external_id"
            " where scheme = 'ca.lop.election' and value like '%:general' order by 1"
        ).fetchall()
        for label, members, first, last in CAUCUSES:
            for day, election in generals:
                if not first <= day.year <= last:
                    continue
                alliance = cs.key(f"{SOURCE}.alliance", f"{day}:{label}")
                cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
                cs.add("name", entity_id=alliance, text=label, lang="en", kind="official", is_preferred=True)
                for member in members:
                    cs.add("alliance_member", alliance_id=alliance, party_id=cs.key("ca.lop.party", member), role=None)
        return cs.write()
