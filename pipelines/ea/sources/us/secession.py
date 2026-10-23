import re
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.secession"
VERSION = "1"
TITLE = "Confederate States of America"
PAGE = "https://en.wikipedia.org/w/index.php?title=Confederate_States_of_America&action=raw"
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
STATES = {
    "South Carolina": "SC",
    "Mississippi": "MS",
    "Florida": "FL",
    "Alabama": "AL",
    "Georgia": "GA",
    "Louisiana": "LA",
    "Texas": "TX",
    "Virginia": "VA",
    "Arkansas": "AR",
    "North Carolina": "NC",
    "Tennessee": "TN",
}


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".wiki")


def emit() -> Path:
    text = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf-8")
    seceded: dict[str, date] = {}
    year = 1860
    for state, month, day, written in re.findall(
        r"^:\d+\. \[\[[^|\]]+ in the American Civil War\|([^\]]+)\]\] \((\w+) (\d+)(?:, (\d{4}))?", text, re.MULTILINE
    ):
        year = int(written or year)
        seceded[STATES[state]] = date(year, MONTHS.index(month) + 1, int(day))
    readmitted = {
        STATES[state]: date(int(year), MONTHS.index(month) + 1, int(day))
        for state, month, day, year in re.findall(
            r"= (?:[^<\n]*\{\{!\}\})?([A-Z][a-z]+(?: [A-Z][a-z]+)?)<!-- Readmitted on (\w+) (\d+), (\d{4}) -->", text
        )
    }
    if set(seceded) != set(STATES.values()) or set(readmitted) != set(STATES.values()):
        raise ValueError(f"seceded {sorted(seceded)}, readmitted {sorted(readmitted)}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The eleven states that seceded, without seats in Congress from secession to readmission",
            polity="US",
        )
        bodies = [cs.key("us.body", kind) for kind in ("house", "senate")]
        if cs.minted:
            raise ValueError("the House and the Senate must already exist")
        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/Confederate_States_of_America",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        )
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        areas = {
            iso.removeprefix("US-"): area
            for iso, area in conn.execute(
                """select p.iso_code, u.area_id from reporting_unit u join polity p on p.id = u.polity_id
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        for body in bodies:
            for state, start in seceded.items():
                cs.add(
                    "representation_gap",
                    body_id=body,
                    area_id=areas[state],
                    reason="secession",
                    valid_from=start,
                    valid_to=readmitted[state],
                )
        return cs.write()
