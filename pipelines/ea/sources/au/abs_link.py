"""Link the Australian Bureau of Statistics' states and electoral divisions to the AEC's areas.

`au.abs/geo` mints its own `au.state` areas (coded by abbreviation) and `au.division` areas (coded by division name)
because it was built without the AEC's areas. `au.aec/geo` holds the same places, with divisions coded by the AEC's
DivisionID. This changeset merges each ABS area into the AEC area for the same state or division, so both sources'
results sit on one area. A division is matched by state and name in the AEC's 2016 enrolment file, the last general
election before the survey's divisions were fixed on 24 August 2017.
"""

from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.au import abs as survey
from ea.sources.au import aec
from ea.store import connect

SOURCE = "au.abs_link"
VERSION = "1"
EVENT = next(event for event in aec.EVENTS if event.id == 20499)
URL = aec.download(EVENT, "GeneralEnrolmentByDivisionDownload")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def pairs() -> list[tuple[str, str]]:
    """(ABS area id, AEC area id) for every state and division of the survey."""
    found = [(survey.area("au.state", code), aec.area("au.state", code)) for code in survey.STATES.values()]
    rows = aec._table(aec._text(raw.cached(SOURCE, URL).path.read_bytes()))
    ids = {(aec.field(r, "StateAb"), aec.field(r, "DivisionNm").lower()): r["DivisionID"] for r in rows}
    missing = []
    for state, name, _ in survey.divisions():
        division = ids.get((state, name.lower()))
        if division is None:
            missing.append((state, name))
        else:
            found.append((survey.area("au.division", name), aec.area("au.division", division)))
    if missing:
        raise ValueError(f"divisions with no AEC division of the same state and name: {missing}")
    if len({aec_id for _, aec_id in found}) != len(found):
        raise ValueError("two ABS areas map to one AEC area")
    return found


def parse() -> None:
    found = pairs()
    if len(found) != 8 + 150:
        raise ValueError(f"expected 8 states and 150 divisions, found {len(found)}")


def emit() -> Path:
    found = pairs()
    with connect() as conn:
        ids = [UUID(i) for pair in found for i in pair]
        held = {
            str(i): scheme
            for i, scheme in conn.execute(
                """select a.id, a.scheme_code from area a where a.id = any(%s)
                   and not exists (select from entity_merge m where m.entity_id = a.id)""",
                [ids],
            ).fetchall()
        }
        if gone := {i for pair in found for i in pair} - held.keys():
            raise ValueError(f"areas missing or already merged: {sorted(gone)}")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "The 8 states and 150 electoral divisions of the Australian Marriage Law Postal Survey merged into "
                "the Australian Electoral Commission's areas for the same states and divisions"
            ),
            polity=None,
            kind="match",
        )
        source = cs.source("Australian Electoral Commission", "General enrolment by division, 2016 federal election")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Australian Electoral Commission",
            title="General enrolment by division, 2016 federal election",
            url=URL,
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="© Commonwealth of Australia (Australian Electoral Commission)",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for duplicate, survivor in found:
            cs.merge(UUID(duplicate), UUID(survivor))
        return cs.write()
