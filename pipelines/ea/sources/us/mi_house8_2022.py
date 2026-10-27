import json
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.mi_house8_2022"
VERSION = "1"
KEY = "2022-11-08:MI:house:8"
EXPORT = "https://app.enhancedvoting.com/cdn/results/midland-county-mi/export-November2022GeneralElection.json"
PUBLISHER = "Midland County Clerk"
TITLE = "November 2022 General Election official results by precinct, Midland County, Michigan"
STATE = {"Daniel T. Kildee": 16621, "Paul Junge": 18305, "David Canny": 588, "Kathy Goodwin": 881}


def fetch() -> None:
    raw.cached("us.enr_ev", EXPORT)


def emit() -> Path:
    export = json.loads(raw.cached("us.enr_ev", EXPORT).path.read_bytes())
    (item,) = [
        item for item in export["results"]["ballotItems"] if item["name"] == "Representative in Congress 8th District"
    ]
    midland = {
        option["name"]: {place["name"]: int(place["voteCount"] or 0) for place in option["precinctResults"]}
        for option in item["ballotOptions"]
    }
    for name, total in STATE.items():
        if sum(midland[name].values()) != total:
            raise ValueError(
                f"Midland {name}: {sum(midland[name].values())} does not tie to the state's county total {total}"
            )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan's 2022 U.S. House election in the 8th District by precinct: OpenElections' precincts for Bay, Genesee, Saginaw, and Tuscola counties and the Midland County Clerk's official precinct results",
            polity="US-MI",
        )
        contest, election = conn.execute(
            "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
            [KEY],
        ).fetchone()
        base, base_source = conn.execute(
            """select r.k, r.source_id from result_set r join changeset c on c.id = r.changeset_id
               where r.contest_id = %s and r.status = 'active' and c.pipeline = 'us.oe_federal'""",
            [contest],
        ).fetchone()
        options = dict(
            conn.execute("select ballot_label, id from contest_option where contest_id = %s", [contest]).fetchall()
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://app.enhancedvoting.com/results/public/midland-county-mi/November2022GeneralElection",
            license="Public record",
        )
        cs.artifact(raw.cached("us.enr_ev", EXPORT), source)
        result_set = cs.key(f"{SOURCE}.result_set", KEY)
        rows = conn.execute(
            """select u.id, o.ballot_label, o.id, v.value from result_value v join reporting_unit u on u.k = v.unit_k
               join contest_option o on o.k = v.option_k
               where v.result_set_k = %s and v.measure = 1 and v.channel = 0 and v.count_round = 0 and split_part(u.code, ':', 3) <> '26111'""",
            [base],
        ).fetchall()
        values = [(unit, option, value) for unit, _, option, value in rows]
        options["Write-In"] = {option for _, label, option, _ in rows if label == "Write-ins"}.pop()
        for name, places in midland.items():
            option = options.get(name)
            if option is None:
                raise ValueError(f"no option for {name}")
            for place, count in places.items():
                code = f"2022:MI:26111:MIDLAND:{place}"
                unit = cs.unit("us.precinct", code)
                if unit in cs.minted:
                    raise ValueError(f"unit {code} does not exist")
                values.append((unit, option, count))
        official = conn.execute(
            "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
            [contest],
        ).fetchone()[0]
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=base_source,
            is_default=False,
            completeness=round(float(sum(value for _, _, value in values)) / float(official), 4),
        )
        cs.add(
            "note",
            entity_id=result_set,
            kind="methodology",
            body_md="Votes by precinct in Bay, Genesee, Saginaw, and Tuscola counties from OpenElections' Michigan precinct file, as in the OpenElections set for this contest, and in Midland County from the Midland County Clerk's official results export (Enhanced Voting), whose precinct sums equal the Secretary of State's county totals. OpenElections' Michigan file has no Midland County rows for this district.",
        )
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
        return cs.write()
