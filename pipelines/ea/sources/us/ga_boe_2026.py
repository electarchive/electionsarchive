import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.ga_boe_2026"
VERSION = "1"
DAY = date(2026, 5, 19)
RUNOFF = date(2026, 6, 16)
INBOX = Path(__file__).resolve().parents[4] / "data" / "inbox" / "manual" / "MF-341"
COUNTIES = [
    (
        "1302550",
        "Gwinnett County Board of Elections and Registration",
        "Gwinnett/official-complete-election-summary-report-may-19-general-primary-nonpartisan-general-election.pdf",
        DAY,
        "local",
    ),
    (
        "1301740",
        "DeKalb County Board of Registration and Elections",
        "DeKalb/ElectionSummaryReportRPT_05_23_19_37_OFFICIAL.pdf",
        DAY,
        "local",
    ),
    (
        "1301740",
        "DeKalb County Board of Registration and Elections",
        "DeKalb/ElectionSummaryReportRPT-06-23-09-09-OFFICIAL.pdf",
        RUNOFF,
        "runoff",
    ),
    (
        "1301290",
        "Cobb County Elections and Registration",
        "Cobb/ElectionSummaryReportRPT_0.pdf",
        DAY,
        "primary",
    ),
    (
        "1301020",
        "Chatham County Board of Elections",
        "Chatham/official-and-complete-election-summary-report-05-19-2026.pdf",
        DAY,
        "local",
    ),
]
HEADING = re.compile(
    r"^(?:County )?(?:BOE|Board of Education)(?: - District (?P<seat>\d+)| (?P<top>President))(?: \(\w[\w -]*\))?(?:/.*?)?(?: - (?P<party>Dem|Rep))? \(Vote for (?P<seats>\d+)\)$"
)
PARTIES = {"Dem": "democratic", "Rep": "republican"}
ROW = re.compile(r"^(?P<name>[A-Z].*?)\s{2,}(?:[\d,]+\s+)*(?P<total>[\d,]+)(?:\s+[\d.]+%)?$")


def fetch() -> None:
    for _, _, path, _, _ in COUNTIES:
        raw.store(
            SOURCE,
            f"manual://MF-341/{path}",
            (INBOX / path).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def emit() -> Path:
    with connect() as conn:
        parents = {
            day: conn.execute("select entity_id from slug where path = %s", [path]).fetchone()[0]
            for day, path in ((DAY, "us/ga/2026-05-19-primary"), (RUNOFF, "us/ga/2026-06-16-runoff"))
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia county board of education races on May 19, 2026, from Gwinnett and DeKalb's official summary reports",
            polity="US",
        )
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}
        values: list[tuple[str, str, str, int]] = []
        contests = 0
        districts: set[str] = set()
        for code, publisher, path, day, kind in COUNTIES:
            title = f"Official election summary report, {day:%B} {day.day}, 2026"
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=None, license=None)
            cs.artifact(raw.cached(SOURCE, f"manual://MF-341/{path}"), source)
            polity, place, base = conn.execute(
                """select u.polity_id, n.text, s.path from reporting_unit u
                   join name n on n.entity_id = u.polity_id and n.is_preferred
                   join slug s on s.entity_id = u.polity_id and s.is_current
                   where u.scheme_code = 'us.school_district' and u.code = %s""",
                [code],
            ).fetchone()
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, f"manual://MF-341/{path}").path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            races: dict[tuple[str, str | None], tuple[int, dict[str, int]]] = {}
            current: dict[str, int] | None = None
            for line in text.splitlines():
                stripped = line.strip()
                if found := HEADING.match(stripped):
                    current = races.setdefault((found["seat"] or found["top"], found["party"]), (int(found["seats"]), {}))[1]
                elif stripped.startswith("Total Votes"):
                    current = None
                elif current is not None and (row := ROW.match(stripped)):
                    current[row["name"].removesuffix("(I)").strip()] = int(row["total"].replace(",", ""))
            election = cs.key("us.election", f"{day}:GA:{code}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parents[day],
                kind=kind,
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            cs.add("name", entity_id=election, text=f"2026 {place} " + (f"{kind} election" if kind != "local" else "election"), lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"{base}/{day}-{kind}", entity_id=election)
            body = cs.key("us.body", f"GA:{code}:board-of-education")
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
                cs.add(
                    "name", entity_id=body, text=f"{place} Board of Education", lang="en", kind="official", is_preferred=True
                )
            whole = cs.area("us.school_district", code)
            for (seat, party), (magnitude, votes) in races.items():
                suffix = f"-{PARTIES[party]}" if party else ""
                label = f"district-{seat}" if seat.isdigit() else slug(seat)
                district = f"GA:{code}:board-of-education:{label}"
                area, unit = (
                    (cs.area("us.local_district", district), cs.unit("us.local_district", district))
                    if seat.isdigit()
                    else (whole, cs.unit("us.school_district", code))
                )
                if seat.isdigit() and area in cs.minted and district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    cs.add(
                        "name",
                        entity_id=area,
                        text=f"{place} Board of Education, District {seat}",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=district,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                    )
                key = f"{day}:GA:{code}:board-of-education:{label}{suffix}"
                previous = cs.key("us.contest", f"{DAY}:GA:{code}:board-of-education:{label}{suffix}") if day == RUNOFF else None
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=magnitude,
                    round_number=2 if previous else 1,
                    previous_round_id=previous,
                    electoral_system_id=systems["fptp"] if magnitude == 1 else systems["block"],
                    default_result_kind="counted",
                    term="full",
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"2026 {place} Board of Education, {seat if not seat.isdigit() else f"District {seat}"} election" + (f" ({party} primary)".replace("Dem", "Democratic").replace("Rep", "Republican") if party else "") + (" (runoff)" if previous else ""),
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{base}/{day}-{kind}/board-of-education-{label}{suffix}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"District totals from the {publisher}'s official summary report: election day, advance voting, absentee by mail, and provisional ballots combined. The report's candidate rows exclude unresolved write-ins.",
                )
                ranked: list[tuple[int, object]] = []
                for label, total in votes.items():
                    person = cs.key(f"{SOURCE}.person", f"{code}:{slug(label)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                    option = cs.key("us.option", f"{key}:{slug(label)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=cs.key("us.party", f"{PARTIES[party]}-party") if party else None, ballot_label=label)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{slug(label)}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                    )
                    ranked.append((total, option))
                    values.append((str(result_set), str(unit), str(option), total))
                ranked.sort(key=lambda pair: -pair[0])
                if ranked[0][0] * 2 <= sum(votes.values()):
                    for _, option in ranked[:2]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                else:
                    for _, option in ranked[:magnitude]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won" if party else "elected", seats=1)
                contests += 1
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        print({"contests": contests})
        return cs.write()
