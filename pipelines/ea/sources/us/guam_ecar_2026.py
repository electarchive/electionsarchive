"""Guam's 2026 general election, official results by precinct, from the Election Commission.

The six contests, their options, and the Nov 3 election already exist in the archive; this package
adds one certified result set for each, valued per precinct, and attaches to the existing rows by id.
"""

import csv
import io
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from ea import package, raw
from ea.package import Package
from ea.sources.us import guam_live
from ea.store import DATA

SOURCE = "us.guam_ecar"
KEY = "us.guam_ecar/general-2026"
DAY = date(2026, 11, 3)
PAGE = "https://gec.guam.gov/2026-general-election-official-results/"
DRIVE = "https://drive.google.com/uc?export=download&id="
SUMMARY = DRIVE + "11zpfHu5YdG9WMtTiWRTV1cTimD-jQpq-"
CSV = DRIVE + "1yhawYqOqiEooYNxBv1G2tnSD27MF5QRR"
REFERENCE = Path(os.environ.get("EA_REFERENCE", DATA / "reference")) / "us"
GUAM = "01a13a9c-7915-7638-aa6d-42bf43253a4b"
STATE_UNIT = "01a13a9c-7915-7638-aa6d-42c1113cd27c"
TERRITORY = "01a13a9c-7915-7638-aa6d-42c0f7deb6d2"
# Seats each contest fills; a contest with no declared winner and no published majority rule is left without outcomes.
ELECTED = {
    "2026-11-03:GU:governor": 1,
    "2026-11-03:GU:house:0": 1,
    "2026-11-03:GU:66:legislature": 15,
    "2026-11-03:GU:66:consolidated-commission-on-utilities": 3,
    "2026-11-03:GU:66:education-board": 4,
}
METHOD = (
    "Official results certified by the Guam Election Commission on 2026-11-19, by precinct, from the Commission's CSV "
    "export. Votes for a ballot line include a Write-in line where the Commission reports one. Registered voters, "
    "ballots cast, and blank ballots are the precinct's totals for the whole ballot. Over votes and under votes are "
    "left out. The territory totals in the Commission's summary report are stored as declared totals."
)


def fetch() -> None:
    for url in (SUMMARY, CSV):
        raw.fetch(SOURCE, url, suffix=".pdf" if url == SUMMARY else ".csv")


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    general().write()


def emit() -> None:
    package.run("load", packages())


def reference() -> dict[str, tuple[str, str, list[tuple[str, str]]]]:
    """Each contest's key to its id, its election, and its options' ids and ballot labels in the reference rows."""
    con = duckdb.connect()
    found = {}
    for key in guam_live.CONTESTS.values():
        rows = con.execute(
            f"""select c.id, c.election_id, o.id, o.ballot_label from '{REFERENCE}/contest.parquet' c
                join '{REFERENCE}/external_id.parquet' e on e.entity_id = c.id and e.scheme = 'us.contest' and e.value = ?
                join '{REFERENCE}/contest_option.parquet' o on o.contest_id = c.id where o.ballot_label is not null""",
            [key],
        ).fetchall()
        if not rows:
            raise ValueError(f"the reference rows hold no contest {key}")
        found[key] = (rows[0][0], rows[0][1], [(row[2], row[3]) for row in rows])
    return found


def sheet(text: str) -> tuple[list[tuple[str, str]], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(text)))
    columns = list(zip(rows[0], rows[1], strict=True))
    return columns, [row for row in rows[2:] if row and any(row)]


def number(value: str) -> int:
    return int(value.replace(",", "").strip())


def general() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "Guam 2026 general election, official results by precinct, from the Election Commission",
    )
    summary = raw.cached(SOURCE, SUMMARY)
    sheet_file = raw.cached(SOURCE, CSV)
    known = reference()
    source = p.id("source", "general-2026")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Guam Election Commission",
        title="2026 General Election Official Results",
        url=PAGE,
        license="Public record",
    )
    for artifact in (summary, sheet_file):
        p.artifact(artifact, source)
    p.external.update({GUAM, STATE_UNIT, TERRITORY, "us.precinct"})
    columns, rows = sheet(sheet_file.path.read_text(encoding="utf-8-sig"))
    precincts = [row for row in rows if row[1].startswith("PRECINCT ")]
    totals = next(row for row in rows if row[1] == "COUNTY TOTALS")
    if len(precincts) + 1 != len(rows):
        raise ValueError("unexpected rows in the precinct sheet")
    units = {}
    for row in precincts:
        code = f"{DAY.year}:GU:{row[1].removeprefix('PRECINCT ').strip()}"
        units[code] = p.id("unit", code)
        p.add(
            "reporting_unit",
            id=units[code],
            polity_id=GUAM,
            scheme_code="us.precinct",
            code=code,
            parent_id=STATE_UNIT,
            area_relation="within",
            area_id=TERRITORY,
            kind="standard",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
    stamp, official = guam_live.read(summary.path)
    stated = datetime.strptime(stamp or "", "%Y-%m-%dT%H:%M:%S%z").astimezone(UTC) if stamp else None
    as_of = datetime(2026, 11, 19, tzinfo=UTC) if stated is None else stated
    by_title: dict[str, list[tuple[int, str, str]]] = {}
    for index, (title, label) in enumerate(columns):
        if title in guam_live.CONTESTS:
            by_title.setdefault(title, []).append((index, label, title))
    for title, cells in by_title.items():
        key = guam_live.CONTESTS[title]
        contest, election, options = known[key]
        keyed = {guam_live.label_key(label): option for option, label in options}
        result_set = p.id("result_set", key, "official")
        p.external.update({contest, election, *(option for option, _ in options)})
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1,
            as_of=as_of,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        column_option: dict[int, str] = {}
        for index, label, _ in cells:
            if label in ("OVER VOTES", "UNDER VOTES"):
                continue
            if label == "Write-in":
                column_option[index] = p.id("option", key, "write-in")
                p.add(
                    "contest_option",
                    id=column_option[index],
                    contest_id=contest,
                    kind="write_in",
                    ballot_label="Write-in",
                )
                continue
            name = re.sub(r"^(?:REP|DEM)\s+", "", label)
            last, given = guam_live.name_key(name)
            hits = [o for (l, g), o in keyed.items() if l == last and (not given or g == given)]
            if len(hits) != 1:
                raise ValueError(f"{key}: no single option for {label}")
            column_option[index] = hits[0]
        if sorted(column_option.values()) != sorted(set(column_option.values())):
            raise ValueError(f"{key}: two columns map to one option")
        missing = {option for option, _ in options} - set(column_option.values())
        if missing:
            raise ValueError(f"{key}: options with no column: {sorted(missing)}")
        for row in precincts:
            unit = units[f"{DAY.year}:GU:{row[1].removeprefix('PRECINCT ').strip()}"]
            for measure, column in ((2, 2), (3, 3), (6, 4)):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=number(row[column]),
                )
            for index, option in column_option.items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=number(row[index]),
                )
        votes = {option: sum(number(row[index]) for row in precincts) for index, option in column_option.items()}
        for index, option in column_option.items():
            if votes[option] != number(totals[index]):
                raise ValueError(f"{key}: precinct sum differs from the sheet's totals for column {index}")
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=number(totals[index]))
        for measure, column in ((2, 2), (3, 3), (6, 4)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=number(totals[column]))
        printed = official.get(title, [])
        by_option = {guam_live.label_key(label): option for option, label in options}
        for name, count in printed:
            last, given = guam_live.name_key(name)
            hit = [o for (l, g), o in by_option.items() if l == last and (not given or g == given)]
            if len(hit) != 1 or votes[hit[0]] != count:
                raise ValueError(f"{key}: the summary's {name} ({count}) differs from the precinct sum")
        seats = ELECTED.get(key)
        if seats is not None:
            ranked = sorted((o for o in votes if o not in {p.id("option", key, "write-in")}), key=lambda o: -votes[o])
            if len(ranked) > seats and votes[ranked[seats - 1]] == votes[ranked[seats]]:
                raise ValueError(f"{key}: tie at the last seat")
            for option in ranked[:seats]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        print(f"{key}: {sum(votes.values())} votes in {len(precincts)} precincts")
    return p
