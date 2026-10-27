import re
import subprocess
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.tx_house_canvass"
VERSION = "1"
GALVESTON = "https://election.lab.ufl.edu/data-downloads/localelections/TX/Houston-The%20Woodlands-Sugar%20Land%20MSA/Galveston%20ISD%20%5BGalveston%20County%5D/11-08-2022/Canvass%20Results11212022%2002.pdf"
TX14 = "2022-11-08:TX:house:14"
GALVESTON_TOTALS = {"Randy Weber": 71257, "Mikal Williams": 38998}


def fetch() -> None:
    raw.fetch(SOURCE, GALVESTON, suffix=".pdf")


def emit() -> Path:
    pages = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, GALVESTON).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\f")
    galveston: dict[str, dict[str, int]] = {}
    totals: dict[str, int] | None = None
    rows = 0
    for page in pages:
        if "U. S. Representative District 14" not in page:
            continue
        for label, weber, williams, cast in re.findall(
            r"^\s*(\d{3}(?:\.\d)?)(?: - \S+)?\s{3,}([\d,]+)\s+([\d,]+)\s+([\d,]+)\s", page, re.MULTILINE
        ):
            counts = [int(n.replace(",", "")) for n in (weber, williams, cast)]
            if counts[0] + counts[1] != counts[2]:
                raise ValueError(f"Galveston {label}: {counts[0]} + {counts[1]} is not {counts[2]}")
            precinct = galveston.setdefault(label, {name: 0 for name in GALVESTON_TOTALS})
            precinct["Randy Weber"] += counts[0]
            precinct["Mikal Williams"] += counts[1]
            rows += 1
        if found := re.search(r"^\s*Totals\s+([\d,]+)\s+([\d,]+)\s", page, re.MULTILINE):
            totals = dict(zip(GALVESTON_TOTALS, (int(n.replace(",", "")) for n in found.groups()), strict=True))
    sums = {name: sum(counts[name] for counts in galveston.values()) for name in GALVESTON_TOTALS}
    if totals != GALVESTON_TOTALS or sums != GALVESTON_TOTALS:
        raise ValueError(f"Galveston: {rows} rows sum to {sums}, canvass totals {totals}")

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's U.S. House election in the 14th District in 2022 by precinct, from OpenElections outside "
            "Galveston County and the Galveston County Clerk's official canvass inside it",
            polity="US-TX",
        )
        contest_id, election = conn.execute(
            "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
            [TX14],
        ).fetchone()
        options = dict(
            conn.execute(
                """select n.text, o.id from contest_option o join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   join name n on n.entity_id = cy.person_id and n.is_preferred where o.contest_id = %s""",
                [contest_id],
            ).fetchall()
        )
        official = float(
            conn.execute(
                "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
                [contest_id],
            ).fetchone()[0]
        )
        base, base_source = conn.execute(
            """select r.k, r.source_id from result_set r join changeset c on c.id = r.changeset_id
               where r.contest_id = %s and r.status = 'active' and c.pipeline = 'us.oe_federal'""",
            [contest_id],
        ).fetchone()
        values = [
            (unit, option, int(value))
            for code, unit, option, value in conn.execute(
                """select u.code, u.id, o.id, v.value from result_value v join reporting_unit u on u.k = v.unit_k
                   join contest_option o on o.k = v.option_k
                   where v.result_set_k = %s and v.measure = 1 and v.channel = 0 and v.count_round = 0""",
                [base],
            ).fetchall()
            if not code.startswith("2022:TX:48167:")
        ]
        publisher = "Galveston County Clerk, Elections Division"
        title = "General Election, November 8, 2022, canvass report by precinct, Galveston County, Texas"
        source = cs.source(publisher, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url="https://www.galvestonvotes.org/election-information/past-election-results",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, GALVESTON), source)
        county_unit, county_area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = '48167'"
        ).fetchone()
        polity = cs.polity("US-TX")
        for label, counts in galveston.items():
            unit = cs.unit("us.precinct", f"2022:TX:48167::{label}")
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="us.precinct",
                    code=f"2022:TX:48167::{label}",
                    parent_id=county_unit,
                    area_relation="within",
                    area_id=county_area,
                    kind="standard",
                    valid_from="2022-11-08",
                    valid_to="2022-11-09",
                )
            values.extend((unit, options[name], count) for name, count in counts.items())
        counted = sum(value for _, _, value in values)
        result_set = cs.key(f"{SOURCE}.result_set", TX14)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest_id,
            stage="certified",
            kind="counted",
            status="active",
            source_id=base_source,
            is_default=False,
            completeness=round(counted / official, 4),
        )
        cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
        for entity, kind, body in (
            (
                result_set,
                "methodology",
                "Votes by precinct in Brazoria, Jefferson, and Orange counties from OpenElections' Texas precinct file, as in the OpenElections set for this contest, whose county sums equal the Secretary of State's, and in Galveston County from the Galveston County Clerk's official canvass report (run November 21, 2022, as mirrored by the University of Florida Election Lab), its ballot-style rows summed to each precinct. Chambers County cast no votes in this district.",
            ),
            (
                result_set,
                "caveat",
                f"Galveston County's canvass gives Mikal Williams 38,998 votes in the county, in its precinct rows, its totals row, and its cumulative report; the Secretary of State's county return and the FEC's district total carry 33,998 there, so this set counts {counted:,} votes against the earlier default's {int(official):,}.",
            ),
            (
                contest_id,
                "caveat",
                "Galveston County's official canvass gives Mikal Williams 38,998 votes in the county. The Secretary of State's county return and the FEC's district total carry 33,998 there, a 5,000-vote transcription error, so the official district total understates Williams by 5,000 votes (68,606, not 63,606). The precinct set from the county's canvass counts the corrected figure.",
            ),
        ):
            if not conn.execute(
                "select 1 from note where entity_id = %s and body_md = %s", [entity, body]
            ).fetchone():
                cs.add("note", entity_id=entity, kind=kind, body_md=body)
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
