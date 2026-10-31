import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ak_gov_precincts"
VERSION = "1"
PUBLISHER = "Alaska Division of Elections"
URL = "https://elections.alaska.gov/results/22GENR/Results_per_Precinct_2022_11_30T14_17_52_To%20Excel.txt"
DAY = date(2022, 11, 8)
KEY = "2022-11-08:AK:governor"
KINDS = {"absentee": "absentee", "early voting": "early", "question": "question"}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        body = subprocess.run(["curl", "-skL", "-m", "120", URL], capture_output=True, check=True).stdout
        raw.store(SOURCE, URL, body, suffix=".txt", retrieved_at=datetime.now(UTC), media_type="text/plain")


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    lines = artifact.path.read_bytes().decode("utf-16").replace("\r", "").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Governor / Lieutenant Governor"))
    header = lines[start + 1].split("\t")
    rows: dict[str, list[int]] = {}
    printed: list[int] = []
    for line in lines[start + 2 :]:
        cells = line.split("\t")
        if cells[0] == "Total":
            printed = [int(cell) for cell in cells[1 : len(header)]]
            break
        if len(cells) >= len(header) and cells[1].strip().isdigit():
            rows[cells[0].strip()] = [int(cell) for cell in cells[1 : len(header)]]
    sums = [sum(votes[i] for votes in rows.values()) for i in range(len(header) - 1)]
    if sums != printed:
        raise ValueError(f"sums {sums} do not tie to the printed totals {printed}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's 2022 governor election by precinct from the Division of Elections' certified results per precinct",
            polity="US-AK",
        )
        contest, election = conn.execute(
            "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
            [KEY],
        ).fetchone()
        labels = conn.execute(
            "select id, lower(ballot_label) from contest_option where contest_id = %s", [contest]
        ).fetchall()
        options = []
        for name in header[1:]:
            if name == "Write-in":
                option = cs.key("us.option", f"{contest}:write-in")
                cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-in")
            else:
                surname = name.split("/")[0].strip().lower()
                found = [option for option, label in labels if label.endswith(surname)]
                if len(found) != 1:
                    raise ValueError(f"{len(found)} options for {name}")
                option = found[0]
            options.append(option)
        source_title = "2022 General Election results per precinct"
        source = cs.source(PUBLISHER, source_title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=source_title,
            url="https://www.elections.alaska.gov/election-results/e/?id=22GENR",
            license="Public record",
        )
        cs.artifact(artifact, source)
        result_set = cs.key(f"{SOURCE}.result_set", KEY)
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "status",
            "source_id",
            "as_of",
            "completeness",
            "supersedes_id",
        )
        for prior in conn.execute(
            f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s",
            [contest, result_set],
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False)
        for option, outcome_kind, seats in conn.execute(
            """select o.option_id, o.kind, o.seats from outcome o
               join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
            [contest, result_set],
        ).fetchall():
            cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
        official = conn.execute(
            "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and r.id <> %s and v.measure = 1",
            [contest, result_set],
        ).fetchone()[0]
        counted = sum(sum(votes[:-1]) for votes in rows.values())
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
            body_md="Votes by precinct from the Division of Elections' certified results per precinct. Absentee, early, and questioned ballots are reported by House district.",
        )
        for place, votes in rows.items():
            if re.match(r"\d\d-\d{3} ", place):
                unit = cs.unit("us.precinct", f"2022:AK:{place[:6]}")
            else:
                found = re.fullmatch(r"District (\d+)\s+- (.+)", place)
                unit = cs.unit("us.aggregate", f"AK:2022:{int(found[1]):02}:{KINDS[found[2].lower()]}")
            if unit in cs.minted:
                raise ValueError(f"unit for {place} does not exist")
            for option, count in zip(options, votes, strict=True):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=count,
                )
        return cs.write()
