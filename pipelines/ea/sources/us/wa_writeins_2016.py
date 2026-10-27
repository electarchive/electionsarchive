import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.wa_writeins_2016"
VERSION = "1"
PUBLISHER = "Washington county auditors and OpenElections"
TITLE = "2016 General Election presidential precinct results with write-ins"
OPENELECTIONS = "fded9d4e-adb2-584b-ba41-0d39d3447398"
PIERCE = (
    "https://web.archive.org/web/20250329193147id_/https://www.piercecountywa.gov/DocumentCenter/View/45035/Precinct-Canvass-Excel?bidId=",
    ".xls",
    "application/vnd.ms-excel",
)
CLARK = ("https://clark.wa.gov/sites/default/files/fileuploads/Elections/2016/09/canvass_-_11-8-2016.xls", ".xls", "application/vnd.ms-excel")
THURSTON = (
    "https://s3.us-west-2.amazonaws.com/thurstoncountywa.gov.if-us-west-2/s3fs-public/2023-02/Auditor_Results%20by%20Precinct_Nov%202016.xlsx",
    ".xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
NAMES = ("Hillary Clinton", "Donald J. Trump", "Alyson Kennedy", "Gloria Estela La Riva", "Jill Stein", "Darrell L. Castle", "Gary Johnson")
CLALLAM = ("https://www.clallamcountywa.gov/DocumentCenter/View/4212/Precinct-2016-General-PDF", ".pdf", "application/pdf")


def fetch() -> None:
    for url, suffix, media_type in (PIERCE, CLALLAM, CLARK, THURSTON):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfL", "-m", "300", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type)


def emit() -> Path:
    pierce = {}
    for name, sheet in pl.read_excel(raw.cached(SOURCE, PIERCE[0]).path, has_header=False, sheet_id=0).items():
        if name == "Document map":
            continue
        rows = sheet.rows()
        for index, row in enumerate(rows):
            if any(cell is not None and "Hillary" in str(cell) for cell in row):
                column = [str(cell) for cell in row].index("Write-In")
                for data in rows[index + 1 :]:
                    if data[0] is not None and data[1] == "All Tally Types" and data[column] != "***":
                        pierce[f"2016:WA:53053::{data[0]}"] = int(data[column] or 0)
                break
    for sheet in pl.read_excel(raw.cached(SOURCE, CLARK[0]).path, has_header=False, sheet_id=0).values():
        rows = sheet.rows()
        for index, row in enumerate(rows):
            if any(cell is not None and "Hillary" in str(cell) for cell in row):
                column = [str(cell).strip() for cell in row].index("Write-Ins")
                for data in rows[index + 1 :]:
                    if data[0] is None or not str(data[0]).isdigit():
                        break
                    pierce[f"2016:WA:53011::{data[0]}"] = int(data[column])
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CLALLAM[0]).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    clallam = {}
    precinct = None
    section = None
    for line in text.splitlines():
        if found := re.search(r"Precinct (\d+) \(Ballots Cast", line):
            precinct = int(found[1])
            section = None
        elif re.match(r"\s+President / Vice President", line):
            section = {}
            clallam[precinct] = section
        elif re.match(r"\s+\S.*, Vote For", line):
            section = None
        elif section is not None and (found := re.match(r"\s+(Hillary Clinton|Donald J\. Trump|Alyson Kennedy|Gloria Estela La Riva|Jill Stein|Darrell L\. Castle|Gary Johnson|Unresolved Write-Ins)\D+?(\d+)", line)):
            section[found[1]] = int(found[2])
    with connect() as conn:
        contest, election = conn.execute(
            "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = '2016-11-08:WA:president'"
        ).fetchone()
        option = conn.execute("select id from contest_option where contest_id = %s and ballot_label = 'Scattered'", [contest]).fetchone()[0]
        values = conn.execute(
            """select u.code, u.id, o.id, v.value from result_value v
               join result_set r on r.k = v.result_set_k join reporting_unit u on u.k = v.unit_k join contest_option o on o.k = v.option_k
               where r.id = %s and v.measure = 1""",
            [OPENELECTIONS],
        ).fetchall()
        units = {}
        tallies = {}
        for code, unit, option_id, value in values:
            units[code] = unit
            tallies.setdefault(code, {})[option_id] = value
        labels = dict(conn.execute("select id, ballot_label from contest_option where contest_id = %s", [contest]).fetchall())
        by_votes = {}
        for code, tally in tallies.items():
            if code.startswith("2016:WA:53009:"):
                key = tuple(int(next(v for o, v in tally.items() if labels[o].startswith(name))) for name in NAMES)
                by_votes.setdefault(key, []).append(code)
        folded = {}
        for code in tallies:
            if code.startswith("2016:WA:53067:"):
                folded[re.sub(r"[^a-z0-9]", "", code.split("::")[1].lower())] = code
        by_tuple = {}
        for code, tally in tallies.items():
            if code.startswith("2016:WA:53067:"):
                by_tuple.setdefault(tuple(int(next(v for o, v in tally.items() if labels[o].startswith(name))) for name in NAMES), []).append(code)
        writeins = dict(pierce)
        thurston = list(pl.read_excel(raw.cached(SOURCE, THURSTON[0]).path, has_header=False, sheet_id=0).values())[0].rows()
        start = next(index for index, row in enumerate(thurston) if any(cell is not None and "Hillary" in str(cell) for cell in row))
        for row in thurston[start + 1 :]:
            if row[0] is None or row[0] == "Precincts" or row[0] == "TOTALS":
                break
            votes = tuple(int(row[index]) for index in range(5, 12))
            stem = re.sub(r"\s+\d{3}$", "", row[0])
            names = {re.sub(r"[^a-z0-9]", "", name.lower()) for name in (row[0], stem, re.sub(r"\s*#\d+", "", stem), row[0].replace("#", ""))}
            exact = [folded[name] for name in names if name in folded and by_tuple.get(votes) is not None and folded[name] in by_tuple[votes]]
            if exact:
                writeins[exact[0]] = int(row[12])
            elif len(by_tuple.get(votes, [])) == 1:
                writeins[by_tuple[votes][0]] = int(row[12])
        for number, section in clallam.items():
            matches = by_votes.get(tuple(section[name] for name in NAMES), [])
            if len(matches) != 1:
                raise ValueError(f"Clallam precinct {number}: {len(matches)} matches")
            writeins[matches[0]] = section["Unresolved Write-Ins"]
        if len(set(writeins) - units.keys()) or len(clallam) != len(by_votes):
            raise ValueError("write-in precincts do not match the OpenElections units")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2016 presidential precinct results with the Pierce, Clark, Thurston, and Clallam county write-in lines added",
            polity="US-WA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url="https://www.piercecountywa.gov/", license="Public record")
        cs.artifact(raw.cached(SOURCE, PIERCE[0]), source)
        cs.artifact(raw.cached(SOURCE, CLALLAM[0]), source)
        cs.artifact(raw.cached(SOURCE, CLARK[0]), source)
        result_set = cs.key(f"{SOURCE}.result_set", "2016-11-08:WA:president")
        official = conn.execute(
            """select sum(v.value) from result_set r join source s on s.id = r.source_id join result_value v on v.result_set_k = r.k
               where r.contest_id = %s and r.id <> %s and v.measure = 1 group by r.id, s.publisher order by (s.publisher = 'Federal Election Commission') desc, 1 desc limit 1""",
            [contest, result_set],
        ).fetchone()[0]
        columns = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")
        for prior in conn.execute(
            f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s", [contest, result_set]
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False)
        for outcome_option, kind, seats in conn.execute(
            """select o.option_id, o.kind, o.seats from outcome o
               join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
            [contest, result_set],
        ).fetchall():
            cs.add("outcome", result_set_id=result_set, option_id=outcome_option, kind=kind, seats=seats)
        counted = sum(sum(tally.values()) for tally in tallies.values()) + sum(writeins.values())
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=round(float(counted) / float(official), 4),
        )
        cs.add(
            "note",
            entity_id=result_set,
            kind="methodology",
            body_md=f"OpenElections' precinct results, which include King County's write-ins, with the write-in line added for {len(writeins)} precincts from the precinct canvasses of Pierce, Clark, Thurston, and Clallam counties. The Secretary of State's precinct files omit write-ins. Pierce suppresses small cells in 13 precincts, so those carry no write-in value, and its published precinct write-ins sum to 11,531 of its 11,532 county total. Thurston combines and zeroes small precincts for privacy, so three of its precincts, with 33 write-ins, are not loaded.",
        )
        for code, tally in tallies.items():
            for option_id, value in tally.items():
                cs.add("result_value", result_set_id=result_set, unit_id=units[code], option_id=option_id, channel=0, measure=1, count_round=0, flags=0, value=value)
        for code, value in writeins.items():
            cs.add("result_value", result_set_id=result_set, unit_id=units[code], option_id=option, channel=0, measure=1, count_round=0, flags=0, value=value)
        return cs.write()
