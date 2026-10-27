import re
import subprocess
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ny_house_canvass"
VERSION = "1"
ESSEX = "https://www.co.essex.ny.us/ElectionArchive/General20161108.pdf"
NASSAU = "https://github.com/openelections/openelections-sources-ny/raw/master/2018/Nassau%20NY%202018%20General.pdf"
NY21 = "2016-11-08:NY:house:21"
NY4 = "2018-11-06:NY:house:4"
ESSEX_LINES = {
    "A": ("Mike Derrick", 6056),
    "E": ("Mike Derrick", 519),
    "B": ("Elise M. Stefanik", 8147),
    "C": ("Elise M. Stefanik", 694),
    "F": ("Elise M. Stefanik", 385),
    "H": ("Elise M. Stefanik", 60),
    "D": ("Matthew J. Funiciello", 691),
}
NASSAU_COLUMNS = [
    "Kathleen M. Rice",
    "Ameer N. Benno",
    "Ameer N. Benno",
    "Kathleen M. Rice",
    "Ameer N. Benno",
    "Scattered",
]
NASSAU_TOTALS = {"Kathleen M. Rice": 159535, "Ameer N. Benno": 100571, "Scattered": 100}


def fetch() -> None:
    raw.fetch(SOURCE, ESSEX, suffix=".pdf")
    raw.fetch(SOURCE, NASSAU, suffix=".pdf")


def text(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\f")


def emit() -> Path:
    essex: dict[tuple[str, str], dict[str, int]] = {}
    for page in text(ESSEX):
        header = re.search(r"^\s*2016\s+([A-Z][A-Z .]+?)\s+(\d)\s+Assy Dist \d+th - Cong Dist 21st", page, re.MULTILINE)
        if header is None:
            continue
        place = (re.sub(r"[^a-z]", "", header[1].lower()).replace("schroon", "shroon"), header[2])
        if place in essex:
            raise ValueError(f"Essex {place} appears twice")
        essex[place] = {}
        for line, count in re.findall(r"(?<![0-9])4([A-H]):\s+\S.*?\s{2,}(\d+)", page):
            name = ESSEX_LINES[line][0]
            essex[place][name] = essex[place].get(name, 0) + int(count)
        lines = re.findall(r"(?<![0-9])4([A-H]):", page)
        if sorted(lines) != sorted(ESSEX_LINES):
            raise ValueError(f"Essex {place}: congress lines {lines}")
    totals: dict[str, int] = {}
    for counts in essex.values():
        for name, count in counts.items():
            totals[name] = totals.get(name, 0) + count
    expected: dict[str, int] = {}
    for name, total in ESSEX_LINES.values():
        expected[name] = expected.get(name, 0) + total
    if totals != expected:
        raise ValueError(f"Essex district sums {totals} do not tie to the state's county totals {expected}")

    nassau: dict[str, list[int]] = {}
    for page in text(NASSAU):
        if "CANVASS REPORT" not in page or "Representative in Congress 4th District" not in page:
            continue
        for town, ad, ed, numbers in re.findall(
            r"^\s*([A-Z]{2}) (\d\d) (\d{3})((?:\s+[\d,]+){10})\s*$", page, re.MULTILINE
        ):
            code = f"{ad}{ed}" if town == "LB" else f"{town} {ad} {ed}"
            if code in nassau:
                raise ValueError(f"Nassau {code} appears twice")
            nassau[code] = [int(n.replace(",", "")) for n in numbers.split()][2:8]
    sums = {name: 0 for name in NASSAU_TOTALS}
    for counts in nassau.values():
        for name, count in zip(NASSAU_COLUMNS, counts):
            sums[name] += count
    if len(nassau) != 652 or sums != NASSAU_TOTALS:
        raise ValueError(
            f"Nassau: {len(nassau)} districts, sums {sums} do not tie to the canvass totals {NASSAU_TOTALS}"
        )

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's U.S. House elections in the 21st District in 2016 and the 4th District in 2018 by election district, from the Essex and Nassau county boards' official canvasses",
            polity="US-NY",
        )

        def contest(key: str) -> tuple[object, object, dict[str, object], float]:
            id, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            options = dict(
                conn.execute("select ballot_label, id from contest_option where contest_id = %s", [id]).fetchall()
            )
            official = conn.execute(
                "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
                [id],
            ).fetchone()[0]
            return id, election, options, float(official)

        def publish(
            key: str,
            contest_id: object,
            election: object,
            source: object,
            official: float,
            values: list[tuple[object, object, int]],
            note: str,
        ) -> None:
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest_id,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(sum(value for _, _, value in values) / official, 4),
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            for unit, option, value in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=value,
                )

        def unit(code: str) -> object:
            id = cs.unit("us.precinct", code)
            if id in cs.minted:
                raise ValueError(f"unit {code} does not exist")
            return id

        contest_id, election, options, official = contest(NY21)
        base, base_source = conn.execute(
            """select r.k, r.source_id from result_set r join changeset c on c.id = r.changeset_id
               where r.contest_id = %s and r.status = 'active' and c.pipeline = 'us.oe_federal'""",
            [contest_id],
        ).fetchone()
        rows = conn.execute(
            """select u.code, u.id, v.option_k, o.id, v.value from result_value v join reporting_unit u on u.k = v.unit_k
               join contest_option o on o.k = v.option_k
               where v.result_set_k = %s and v.measure = 1 and v.channel = 0 and v.count_round = 0""",
            [base],
        ).fetchall()
        values = [
            (unit_id, option, int(value))
            for code, unit_id, _, option, value in rows
            if not code.startswith("2016:NY:36031:")
        ]
        units = {
            (re.sub(r"[^a-z]", "", code.split(":")[-1].rsplit(" ", 1)[0].lower()), code.rsplit(" ", 1)[1]): code
            for code, *_ in rows
            if code.startswith("2016:NY:36031:")
        }
        if set(units) != set(essex):
            raise ValueError(f"Essex districts differ: {set(units) ^ set(essex)}")
        publisher = "Essex County Board of Elections"
        title = "General Election November 8th, 2016, official results, Essex County, New York"
        source = cs.source(publisher, title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=publisher,
            title=title,
            url=ESSEX,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, ESSEX), source)
        for place, counts in essex.items():
            for name, count in counts.items():
                values.append((unit(units[place]), options[name], count))
        publish(
            NY21,
            contest_id,
            election,
            base_source,
            official,
            values,
            "Votes by election district in Clinton, Franklin, Fulton, Hamilton, Herkimer, Jefferson, Lewis, St. Lawrence, Saratoga, Warren, and Washington counties from OpenElections' New York precinct file, as in the OpenElections set for this contest, and in Essex County from the Essex County Board of Elections' official results by election district, whose sums equal the State Board's county totals on every party line. OpenElections' file has no Democratic line for Mike Derrick in Essex County.",
        )

        contest_id, election, options, official = contest(NY4)
        publisher = "Nassau County Board of Elections"
        title = "Official Canvass and Statement of Votes Cast of the General Election, November 6, 2018, Nassau County"
        source = cs.source(publisher, title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=publisher,
            title=title,
            url="https://github.com/openelections/openelections-sources-ny/blob/master/2018/Nassau%20NY%202018%20General.pdf",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, NASSAU), source)
        values = []
        for code, counts in nassau.items():
            id = unit(f"2018:NY:36059::{code}")
            per: dict[str, int] = {}
            for name, count in zip(NASSAU_COLUMNS, counts):
                per[name] = per.get(name, 0) + count
            values.extend((id, options[name], count) for name, count in per.items())
        publish(
            NY4,
            contest_id,
            election,
            source,
            official,
            values,
            "Votes by election district from the Nassau County Board of Elections' official canvass (pages 417 to 434), each candidate's party lines summed and write-ins as scattered votes; the districts sum to the canvass's race totals. OpenElections' file for this contest shifts Long Beach's 24 districts by a column.",
        )
        return cs.write()
