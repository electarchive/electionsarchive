import re
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.de_senate_2010"
VERSION = "1"
URL = "https://elections.delaware.gov/elections/resultsarchive/elect10/elect10_General/data/stwoff_kns.txt"
KEY = "2010-11-02:DE:senate:special"
PUBLISHER = "Delaware Department of Elections"
TITLE = "Official Election Results, General Elections 11/02/10, Statewide Offices By County"
COUNTIES = ["10003", "10001", "10005"]


def emit() -> Path:
    artifact = raw.cached("us.de_doe", URL)
    lines = artifact.path.read_text(errors="replace").splitlines()
    start = next(n for n, line in enumerate(lines) if line.startswith("UNITED STATES SENATOR"))
    if lines[start - 1].split(";")[1:4] != [" Wilmington ", " Kent ", " Sussex "]:
        raise ValueError(f"unexpected header {lines[start - 1]!r}")
    rows: dict[str, list[int]] = {}
    for line in lines[start + 1 :]:
        cells = [cell.strip() for cell in line.split(";")]
        votes = [int(cell.replace(",", "")) for cell in cells[1:5]]
        if cells[0] == "Office Total":
            totals = votes
            break
        rows[cells[0].split()[0].upper()] = votes
    for index in range(4):
        if sum(votes[index] for votes in rows.values()) != totals[index]:
            raise ValueError(f"column {index} does not add up to the office total")
    for votes in rows.values():
        if sum(votes[:3]) != votes[3]:
            raise ValueError("a candidate's counties do not add up to the state total")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Delaware's 2010 U.S. Senate special election by county from the Department of Elections' official results",
            polity="US-DE",
        )
        contest, election = conn.execute(
            "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
            [KEY],
        ).fetchone()
        options = {
            label.split()[-2 if label.endswith("Jr.") else -1].upper(): id
            for label, id in conn.execute(
                "select ballot_label, id from contest_option where contest_id = %s", [contest]
            ).fetchall()
        }
        official = conn.execute(
            "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
            [contest],
        ).fetchone()[0]
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.delaware.gov/elections/resultsarchive/elect10/elect10_General/",
            license="Public record",
        )
        cs.artifact(artifact, source)
        result_set = cs.key(f"{SOURCE}.result_set", KEY)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=round(totals[3] / float(official), 4),
        )
        cs.add(
            "note",
            entity_id=result_set,
            kind="methodology",
            body_md="The Department of Elections' statewide offices by county; its first column, headed Wilmington, is New Castle County, since the three columns sum to the state total for every candidate.",
        )
        for surname, votes in rows.items():
            for code, value in zip(COUNTIES, votes[:3]):
                unit = cs.unit("us.county", code)
                if unit in cs.minted:
                    raise ValueError(f"county {code} does not exist")
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=options[re.sub(r"[^A-Z']", "", surname)],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
