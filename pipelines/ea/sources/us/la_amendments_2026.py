import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import oe_primaries_2026 as oe
from ea.store import DATA, connect

SOURCE = "us.la_amendments_2026"
VERSION = "1"
PAGE = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data"
URL = f"{PAGE}#MF-408"
FILE = DATA / "inbox" / "manual" / "MF-408" / "Election+Results+(05-16-2026).xlsx"
SHEET = "Multi-Parish(Parish)"
PUBLISHER = "Louisiana Secretary of State"
TITLE = "Louisiana May 16, 2026 election results"
ELECTION = "2026-05-16:LA:primary"
HEADING = re.compile(r"^CA No\. (\d+) \(")
NOTE = "County totals by parish from the Secretary of State's election results export; the parish sums equal the statewide Total Votes row of each amendment."


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            FILE.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def amendments() -> dict[int, tuple[dict[str, int], dict[str, dict[str, int]]]]:
    sheet = fastexcel.read_excel(str(raw.cached(SOURCE, URL).path)).load_sheet_by_name(SHEET, header_row=None)
    lines = sheet.to_polars().rows()
    found: dict[int, tuple[dict[str, int], dict[str, dict[str, int]]]] = {}
    index = 0
    while index < len(lines):
        heading = HEADING.match(str(lines[index][0] or ""))
        if heading is None:
            index += 1
            continue
        number = int(heading.group(1))
        if (lines[index + 1][1], lines[index + 1][2]) != ("YES", "NO") or lines[index + 2][0] != "Total Votes":
            raise ValueError(f"Amendment {number} header is not as expected")
        totals = {"YES": int(lines[index + 2][1]), "NO": int(lines[index + 2][2])}
        parishes: dict[str, dict[str, int]] = {}
        index += 3
        while index < len(lines) and lines[index][1] is not None and lines[index][0] is not None:
            parishes[str(lines[index][0])] = {"YES": int(lines[index][1]), "NO": int(lines[index][2])}
            index += 1
        for answer, total in totals.items():
            if sum(votes[answer] for votes in parishes.values()) != total:
                raise ValueError(f"Amendment {number} {answer}: parishes differ from the Total Votes row")
        found[number] = (totals, parishes)
    return found


def emit() -> Path:
    data = amendments()
    if sorted(data) != [1, 2, 3, 4, 5]:
        raise ValueError(f"amendments found: {sorted(data)}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's five constitutional amendments on the May 16, 2026 ballot: the statewide totals and the parish totals from the Secretary of State's election results export",
            polity="US-LA",
        )
        election = conn.execute(
            "select entity_id from external_id where scheme = 'us.election' and value = %s", [ELECTION]
        ).fetchone()[0]
        state_unit, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and code = '22'"
        ).fetchone()
        system, default_kind = conn.execute(
            """select ct.electoral_system_id, ct.default_result_kind from external_id e
               join contest ct on ct.id = e.entity_id
               where e.scheme = 'us.contest' and e.value = '2026-11-03:LA:question:proposed-amendment-no-1'"""
        ).fetchone()
        units: dict[str, UUID] = {}
        for short, id in conn.execute(
            """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ '^22'"""
        ).fetchall():
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
        cs.artifact(raw.cached(SOURCE, URL), source)
        rows: list[tuple[str, str, str | None, int, int, int, int, int]] = []
        for number, (totals, parishes) in data.items():
            key = f"2026-05-16:LA:question:amendment-{number}"
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
            cs.add(
                "name",
                entity_id=contest,
                text=f"Constitutional Amendment No. {number}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=f"us/la/2026-05-16-primary/amendment-{number}", entity_id=contest)
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
            by_parish = cs.key(f"{SOURCE}.result_set", f"{key}:parish")
            for id, default in ((official, True), (by_parish, False)):
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
            cs.add("note", entity_id=by_parish, kind="methodology", body_md=NOTE)
            cs.add(
                "outcome",
                result_set_id=official,
                option_id=options["YES" if totals["YES"] > totals["NO"] else "NO"],
                kind="won",
            )
            for answer, votes in totals.items():
                rows.append((str(official), str(state_unit), str(options[answer]), 0, 1, 0, 0, votes))
            rows.append((str(official), str(state_unit), None, 0, 4, 0, 0, sum(totals.values())))
            for parish, votes in parishes.items():
                unit = units[oe.plain(parish)]
                for answer in ("YES", "NO"):
                    rows.append((str(by_parish), str(unit), str(options[answer]), 0, 1, 0, 0, votes[answer]))
                rows.append((str(by_parish), str(unit), None, 0, 4, 0, 0, sum(votes.values())))
            print(number, totals, len(parishes))
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
