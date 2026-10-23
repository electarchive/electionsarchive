import csv
import re
import subprocess
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.legislators import ADMITTED, RATIFIED
from ea.store import connect

SOURCE = "us.apportionment"
VERSION = "1"
HISTORIAN = "https://foxx.house.gov/uploadedfiles/state_apportionment.pdf"
CENSUS = "https://www2.census.gov/programs-surveys/decennial/2020/data/apportionment/apportionment.csv"
FOUNDED = date(1789, 3, 4)
EFFECTIVE = [
    FOUNDED,
    *(date(year + 3, 3, 4) for year in range(1790, 1920, 10)),
    date(1933, 3, 4),
    *(date(year + 3, 1, 3) for year in range(1940, 2030, 10)),
]
EARLY = {"VT", "KY", "MO", "CA", "KS", "NM", "AZ"}
SPLITS = {"ME": "MA", "WV": "VA"}


def fetch() -> None:
    raw.fetch(SOURCE, HISTORIAN, suffix=".pdf")
    raw.fetch(SOURCE, CENSUS, suffix=".csv")


def emit() -> Path:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, HISTORIAN).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    with connect() as conn:
        names = dict(
            conn.execute(
                """select n.text, p.iso_code from reporting_unit u join polity p on p.id = u.polity_id
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        )
        codes = {name: iso.removeprefix("US-") for name, iso in names.items()}
        counts: dict[str, list[int]] = {}
        for line in text.splitlines():
            found = re.match(r"^([A-Z][a-z]+(?: [A-Z][a-z]+)*)(?: \[\d\])*\s{2,}([\d\s]+)$", line.rstrip())
            if found and found[1] in codes:
                numbers = [int(value) for value in found[2].split()]
                counts[codes[found[1]]] = [0] * (len(EFFECTIVE) - 1 - len(numbers)) + numbers
        for row in csv.DictReader(raw.cached(SOURCE, CENSUS).path.open(encoding="utf-8-sig")):
            if row["Year"] == "2020" and row["Name"] in codes and row["Number of Representatives"]:
                counts[codes[row["Name"]]].append(int(row["Number of Representatives"]))
        if len(counts) != 50 or any(len(values) != len(EFFECTIVE) for values in counts.values()):
            raise ValueError(f"parsed {len(counts)} states")
        totals = [sum(values[column] for values in counts.values()) for column in range(len(EFFECTIVE))]
        if totals[0] != 65 or totals[-1] != 435:
            raise ValueError(f"totals {totals}")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Seats each state was entitled to in the House after every census, and two per state in the Senate",
            polity="US",
        )
        bodies = {kind: cs.key("us.body", kind) for kind in ("house", "senate")}
        if cs.minted:
            raise ValueError("the House and the Senate must already exist")
        historian = cs.source(
            "Office of the Historian, U.S. House of Representatives", "Representatives Apportioned to Each State"
        )
        cs.add(
            "source",
            id=historian,
            kind="official_body",
            publisher="Office of the Historian, U.S. House of Representatives",
            title="Representatives Apportioned to Each State",
            url="https://history.house.gov/Institution/Apportionment/State-Apportionment/",
        )
        cs.artifact(raw.cached(SOURCE, HISTORIAN), historian)
        census = cs.source("U.S. Census Bureau", "Historical Apportionment Data (1910-2020)")
        cs.add(
            "source",
            id=census,
            kind="official_body",
            publisher="U.S. Census Bureau",
            title="Historical Apportionment Data (1910-2020)",
            url="https://www.census.gov/data/tables/time-series/dec/apportionment-data-text.html",
        )
        cs.artifact(raw.cached(SOURCE, CENSUS), census)
        areas = {
            iso.removeprefix("US-"): area
            for iso, area in conn.execute(
                """select p.iso_code, u.area_id from reporting_unit u join polity p on p.id = u.polity_id
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        joined = {state: ADMITTED.get(state, RATIFIED.get(state, FOUNDED)) for state in counts}
        interim: dict[str, int] = {}
        for state, values in counts.items():
            first = next(column for column, seats in enumerate(values) if seats)
            if state not in EARLY and joined[state] < EFFECTIVE[first]:
                interim[state] = sum(
                    count
                    for (count,) in conn.execute(
                        """select count(distinct t.seat_id) from tenure t
                       join seat s on s.id = t.seat_id and s.voting
                       join external_id e on e.entity_id = s.id and e.scheme = 'us.seat' and e.value like %s
                       join external_id b on b.entity_id = t.body_id and b.scheme = 'us.body' and b.value = 'house'
                       where t.from_date >= %s and t.from_date < %s""",
                        [f"house:{state}:%", joined[state], EFFECTIVE[first]],
                    )
                )
        spans: dict[str, list[tuple[date, date | None, int]]] = {}
        for state, values in counts.items():
            first = next(column for column, seats in enumerate(values) if seats)
            rows = spans.setdefault(state, [])
            if interim.get(state):
                rows.append((joined[state], EFFECTIVE[first], interim[state]))
            for column in range(first, len(EFFECTIVE)):
                start = EFFECTIVE[column] if column > first or state not in EARLY else joined[state]
                rows.append(
                    (
                        max(start, joined[state]),
                        EFFECTIVE[column + 1] if column + 1 < len(EFFECTIVE) else None,
                        values[column],
                    )
                )
        for child, parent in SPLITS.items():
            cut = joined[child]
            taken = next(seats for start, _, seats in spans[child] if start == cut)
            reshaped = []
            for start, end, seats in spans[parent]:
                if start < cut < (end or date.max):
                    reshaped += [(start, cut, seats), (cut, end, seats - taken)]
                else:
                    reshaped.append((start, end, seats))
            spans[parent] = reshaped
        for state, rows in spans.items():
            for start, end, seats in rows:
                if seats and (end is None or start < end):
                    cs.add(
                        "body_apportionment",
                        body_id=bodies["house"],
                        area_id=areas[state],
                        seats=seats,
                        valid_from=start,
                        valid_to=end,
                    )
            cs.add(
                "body_apportionment", body_id=bodies["senate"], area_id=areas[state], seats=2, valid_from=joined[state]
            )
        return cs.write()
