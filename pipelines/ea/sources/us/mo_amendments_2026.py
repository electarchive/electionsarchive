import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import oe_primaries_2026 as oe
from ea.store import DATA, connect

SOURCE = "us.mo_amendments_2026"
VERSION = "1"
BASE = "https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
COUNTIES = BASE + "ActualResults-August42026.pdf"
TOTALS = BASE + "2026PrimaryElection.pdf"
PAGE = "https://www.sos.mo.gov/elections/results"
INBOX = DATA / "inbox" / "manual" / "MF-407"
PUBLISHER = "Missouri Secretary of State"
TITLE = "Missouri August 4, 2026 primary election results"
AMENDMENTS = (1, 2, 4, 5)
ST_LOUIS_CITY = "29510"
ST_LOUIS_COUNTY = "29189"


def fetch() -> None:
    for url in (COUNTIES, TOTALS):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / url.rsplit("/", 1)[1]).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
                media_type="application/pdf",
            )


def text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def statewide() -> dict[int, dict[str, int]]:
    body = text(TOTALS)
    found: dict[int, dict[str, int]] = {}
    for number in AMENDMENTS:
        block = body.partition(f"Constitutional Amendment No. {number} ")[2].partition("Total Votes")[0]
        found[number] = {
            answer.upper(): int(votes.replace(",", ""))
            for answer, votes in re.findall(r"(Yes|No) Votes\s+([\d,]+)", block)
        }
        if set(found[number]) != {"YES", "NO"}:
            raise ValueError(f"Amendment {number} totals not found: {found[number]}")
    return found


def counties() -> tuple[dict[int, dict[str, tuple[int, int]]], dict[int, tuple[int, int]]]:
    rows: dict[int, dict[str, tuple[int, int]]] = {}
    totals: dict[int, tuple[int, int]] = {}
    current = 0
    for line in text(COUNTIES).splitlines():
        header = re.match(r"^Amendment (\d+)\s+YES\s+NO\s+Total", line)
        if header:
            current = int(header.group(1))
            rows.setdefault(current, {})
            continue
        row = re.match(r"^(.+?)\s+(\d+)\s+(\d+)\s+(\d+)$", line.strip())
        if current == 0 or row is None:
            continue
        name, yes, no, total = row.group(1), int(row.group(2)), int(row.group(3)), int(row.group(4))
        if yes + no != total:
            raise ValueError(f"Amendment {current} {name} does not add up")
        if name == "Total":
            totals[current] = (yes, no)
        elif name == "Kansas City":
            jackson = rows[current]["Jackson"]
            rows[current]["Jackson"] = (jackson[0] + yes, jackson[1] + no)
        else:
            rows[current][name] = (yes, no)
    return rows, totals


def emit() -> Path:
    by_county, county_totals = counties()
    totals = statewide()
    for number in AMENDMENTS:
        if len(by_county[number]) != 115:
            raise ValueError(f"Amendment {number} has {len(by_county[number])} counties")
        if county_totals[number] != (totals[number]["YES"], totals[number]["NO"]):
            raise ValueError(f"Amendment {number} county sum differs from the statewide totals")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's four constitutional amendments on the August 4, 2026 primary ballot: the official statewide totals and the county totals from the Secretary of State's official results",
            polity="US-MO",
        )
        election = conn.execute(
            "select entity_id from external_id where scheme = 'us.election' and value = '2026-08-04:MO:primary'"
        ).fetchone()[0]
        state_unit, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and code = '29'"
        ).fetchone()
        system, default_kind = conn.execute(
            """select ct.electoral_system_id, ct.default_result_kind from external_id e
               join contest ct on ct.id = e.entity_id
               where e.scheme = 'us.contest' and e.value = '2026-11-03:MO:question:amendment-3'"""
        ).fetchone()
        units: dict[str, UUID] = {}
        for code, short, id in conn.execute(
            """select u.code, n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ '^29'"""
        ).fetchall():
            if code == ST_LOUIS_CITY:
                units["St. Louis City"] = id
            elif code == ST_LOUIS_COUNTY:
                units["St. Louis"] = id
            else:
                units[oe.plain(short)] = id
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        for url in (COUNTIES, TOTALS):
            cs.artifact(raw.cached(SOURCE, url), source)
        rows: list[tuple[str, str, str | None, int, int, int, int, int]] = []
        for number in AMENDMENTS:
            key = f"2026-08-04:MO:question:amendment-{number}"
            sums = {"YES": county_totals[number][0], "NO": county_totals[number][1]}
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                round_number=1,
                electoral_system_id=system,
                default_result_kind=default_kind,
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=f"Amendment {number}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=f"us/mo/2026-08-04-primary/amendment-{number}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            options = {}
            for position, answer in enumerate(("YES", "NO")):
                options[answer] = cs.key("us.option", f"{key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
                )
            official = cs.key(f"{SOURCE}.result_set", f"{key}:official")
            county_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            cs.add(
                "note",
                entity_id=county_set,
                kind="caveat",
                body_md="The Secretary of State reports the Kansas City Board of Election Commissioners apart from Jackson County; its totals are added into Jackson County here, though the city also reaches into Clay and Platte counties.",
            )
            for id, default in ((official, True), (county_set, False)):
                cs.add(
                    "result_set",
                    id=id,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
            cs.add(
                "outcome",
                result_set_id=official,
                option_id=options["YES" if totals[number]["YES"] > totals[number]["NO"] else "NO"],
                kind="won",
            )
            for answer, votes in totals[number].items():
                rows.append((str(official), str(state_unit), str(options[answer]), 0, 1, 0, 0, votes))
            rows.append((str(official), str(state_unit), None, 0, 4, 0, 0, sum(totals[number].values())))
            for county, (yes, no) in by_county[number].items():
                unit = units[county if county.startswith("St. Louis") else oe.plain(county)]
                rows.append((str(county_set), str(unit), str(options["YES"]), 0, 1, 0, 0, yes))
                rows.append((str(county_set), str(unit), str(options["NO"]), 0, 1, 0, 0, no))
                rows.append((str(county_set), str(unit), None, 0, 4, 0, 0, yes + no))
            print(number, totals[number], sums)
        cs.frame(
            "result_value",
            pl.DataFrame(
                rows,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ),
        )
        return cs.write()
