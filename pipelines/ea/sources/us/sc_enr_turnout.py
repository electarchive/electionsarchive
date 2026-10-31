import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid5
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.sc_enr_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.enr-scvotes.org/SC/"
PUBLISHER = "South Carolina State Election Commission"
TITLE = "Election night reporting: detail XML, voter turnout by county"
ELECTIONS = {
    (date(2010, 6, 22), "runoff"): "16213/29430",
    (date(2012, 1, 21), "primary"): "36831/139730",
    (date(2012, 6, 26), "runoff"): "40277/114489",
    (date(2014, 6, 24), "runoff"): "52366/135097",
    (date(2016, 2, 20), "primary"): "59148/160299",
    (date(2016, 2, 27), "primary"): "59277/162732",
    (date(2016, 6, 28), "runoff"): "62799/173206",
    (date(2018, 6, 26), "runoff"): "76440/207826",
    (date(2020, 2, 29), "primary"): "100517/244259",
    (date(2020, 6, 23), "runoff"): "104240/256940",
    (date(2022, 6, 28), "runoff"): "115187/300622",
    (date(2024, 2, 3), "primary"): "119283/327921",
    (date(2024, 2, 24), "primary"): "119816/330796",
    (date(2024, 6, 25), "runoff"): "122026/344536",
}
RUNOFF = (
    "Registered voters counts only the precincts that held a runoff, as the State Election Commission's report gives it, "
    "so in a county where only some precincts voted it is not the county's full registration."
)


def address(path: str) -> str:
    return f"{SITE}{path}/reports/detailxml.zip"


def fetch() -> None:
    for path in ELECTIONS.values():
        raw.fetch(SOURCE, address(path), suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    rows = []
    for (day, kind), path in ELECTIONS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, address(path)).path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        floor: dict[str, int] = {}
        for contest in root.iter("Contest"):
            if contest.get("text", "").startswith("Ballots Cast"):
                continue
            totals: dict[str, int] = {}
            for county in contest.iter("County"):
                name = re.sub(r"[^a-z]", "", county.get("name", "").lower())
                totals[name] = totals.get(name, 0) + int(county.get("votes", "0"))
            for name, votes in totals.items():
                floor[name] = max(floor.get(name, 0), votes)
        units = [
            *root.findall("ElectionVoterTurnout/Counties/County"),
            *root.findall("VoterTurnout/Precincts/Precinct"),
        ]
        kept = 0
        for unit in units:
            name = re.sub(r"[^a-z]", "", unit.get("name", "").lower())
            registered, ballots = int(unit.get("totalVoters", "0")), int(unit.get("ballotsCast", "0"))
            if ballots == 0 and floor.get(name, 0) == 0:
                continue
            if not floor.get(name, 0) <= ballots <= registered:
                print(
                    f"{day} {unit.get('name')}: ballots {ballots}, registered {registered}, largest contest {floor.get(name, 0)}, left out"
                )
                continue
            kept += 1
            rows.append(
                {"day": day, "kind": kind, "code": counties[name], "registered": registered, "ballots": ballots}
            )
        print(day, kind, f"{kept} of {len(units)} counties")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.group_by("day", "kind").agg(pl.len(), pl.col("registered", "ballots").sum()).sort("day"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's registered voters and ballots cast by county for its primary runoffs 2010-2024 and presidential "
            "preference primaries 2012-2024, from the State Election Commission's election night reporting detail XML",
            polity="US",
        )
        elections = {
            value: entity_id
            for value, entity_id in conn.execute(
                """select x.value, x.entity_id from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:SC:(primary|runoff)$'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=SITE,
                license="Public record",
            )
        for path in ELECTIONS.values():
            cs.artifact(raw.cached(SOURCE, address(path)), source)
        for (day, kind), rows in frame.group_by("day", "kind", maintain_order=True):
            value_key = f"{day}:SC:{kind}"
            if value_key not in elections:
                print(f"{value_key}: no election")
                continue
            election_id = elections[value_key]
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=round(rows.height / 46, 4),
            )
            if kind == "runoff":
                cs.add(
                    "note", id=uuid5(result_set, "registered"), entity_id=result_set, kind="methodology", body_md=RUNOFF
                )
            for code, registered, ballots in rows.select("code", "registered", "ballots").iter_rows():
                for measure, value in ((2, registered), (3, ballots)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
