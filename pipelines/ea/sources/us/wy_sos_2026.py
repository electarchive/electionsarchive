import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import precinct_code, raw
from ea.changeset import Changeset, slug
from ea.sources.us.mp_cec_live import pick, words
from ea.sources.us.ok_pri_judicial import ordinal
from ea.sources.us.retention import display, identity
from ea.sources.us.wy_judicial import flat
from ea.store import DATA, connect
from ea.typed import one

SOURCE = "us.wy_sos_2026"
VERSION = "2"
DAY = date(2026, 11, 3)
SITE = "https://sos.wyo.gov/Elections/Docs/2026/Results/General/"
ZIP = SITE + "2026_Wyoming_General_Results.zip"
MINUTES = SITE + "2026_General_State_Canvassing_Board_Minutes.pdf"
SUMMARIES = "2026 General Results Summaries - OFFICIAL.xlsx"
PRECINCTS = "2026 County General PbP - OFFICIAL.xlsx"
BALLOTS = "2026 County General PbP TBC - OFFICIAL.xlsx"
MANGLED = re.compile(r"\d{4}-(\d\d)-(\d\d) 00:00:00")
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Wyoming Secretary of State"
TITLE = "2026 General Election official results, certified by the State Canvassing Board on November 10, 2026"
STATEWIDE = {
    "United States Senator": "senate",
    "United States Representative": "house:0",
    "Governor": "governor",
    "Secretary of State": "secretary-of-state",
    "State Auditor": "auditor",
    "State Treasurer": "treasurer",
    "Superintendent of Public Instruction": "superintendent-of-public-instruction",
}
QUESTION = "Statewide Ballot Proposition Proposed Initiative Proposition Number One"
ANSWERS = {"For": "Yes", "Against": "No"}
SEAT = re.compile(r"(Senate|House) District (\d+)")
COURT = re.compile(
    r"(Supreme Court Justice, Wyoming Supreme Court|Chancery Court Judge, Wyoming Chancery Court"
    r"|(District|Circuit) Court Judge, (\w+) Judicial District) (.+)"
)
ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth"]
SPOILED = {"Overvotes": 5, "Undervotes": 6}
NOTE = (
    "Precinct figures from the Wyoming Secretary of State's official county precinct-by-precinct summaries of the "
    "November 3, 2026 general election, which the State Canvassing Board certified on November 10, 2026. Overvotes "
    "are stored as invalid and undervotes as blank. Each column's precinct figures add up to the county figure in "
    "the official summaries, and the counties to the statewide total the summaries print. Ballots cast are the "
    "precinct's total ballots from the official total-ballots workbook."
)
RETENTION = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when a majority votes Yes."
)
INITIATIVE = (
    "An initiated measure passes only with a majority of all ballots cast in the election, not just of the votes on "
    "the measure."
)


def fetch() -> None:
    raw.fetch(SOURCE, MINUTES, suffix=".pdf")
    raw.fetch(SOURCE, ZIP, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw.cached(SOURCE, ZIP).path) as bundle:
        (PARSED / "summaries.xlsx").write_bytes(bundle.read(SUMMARIES))
        (PARSED / "precincts.xlsx").write_bytes(bundle.read(PRECINCTS))
        (PARSED / "ballots.xlsx").write_bytes(bundle.read(BALLOTS))


def ballots() -> int:
    rows = pl.read_excel(
        PARSED / "summaries.xlsx", sheet_name="Statewide Total Ballots Cast", has_header=False, infer_schema_length=0
    ).rows()
    return int(next(row[1] for row in rows if row[0] == "Total"))


def ballot_counties() -> dict[str, int]:
    rows = pl.read_excel(
        PARSED / "summaries.xlsx", sheet_name="Statewide Total Ballots Cast", has_header=False, infer_schema_length=0
    ).rows()
    return {row[0]: int(row[1]) for row in rows[2:] if row[0] and row[0] != "Total"}


def blocks() -> list[tuple[str, list[str], dict[str, list[int | None]]]]:
    found = []
    for sheet in (
        "Statewide Candidates",
        "Statewide Senate Odd",
        "Statewide House",
        "Statewide Judicial",
        "Statewide Ballot Issue",
    ):
        rows = pl.read_excel(PARSED / "summaries.xlsx", sheet_name=sheet, has_header=False, infer_schema_length=0)
        found += sheet_blocks(rows.rows())
    return found


def sheet_blocks(rows: list[tuple]) -> list[tuple[str, list[str], dict[str, list[int | None]]]]:
    heads, names = rows[1], rows[2]
    end = next(i for i, row in enumerate(rows) if row[0] == "Total")
    starts = [i for i, cell in enumerate(heads) if cell]
    found = []
    for at, start in enumerate(starts):
        stop = starts[at + 1] if at + 1 < len(starts) else len(heads)
        stop = next((i for i in range(start, stop) if not names[i]), stop)
        title = " ".join(str(heads[start]).split())
        counties = {
            row[0]: [None if row[i] in ("-", None) else int(row[i]) for i in range(start, stop)] for row in rows[3:end]
        }
        totals = [int(rows[end][i]) for i in range(start, stop)]
        sums = [sum(values[k] or 0 for values in counties.values()) for k in range(stop - start)]
        if sums != totals:
            raise ValueError(f"{title}: county sums {sums} differ from the printed totals {totals}")
        counties = {county: values for county, values in counties.items() if values[0] is not None}
        found.append((title, [" ".join(str(names[i]).split()) for i in range(start, stop)], counties))
    return found


def precinct_label(cell: object) -> str:
    text = " ".join(str(cell).split())
    mangled = MANGLED.fullmatch(text)
    return f"{mangled[1]}-{mangled[2]}" if mangled else text


def precinct_blocks(
    sheets: list[str],
) -> tuple[
    dict[str, dict[str, dict[str, list[int | None]]]], dict[str, dict[str, int]], dict[str, dict[str, list[str]]]
]:
    found: dict[str, dict[str, dict[str, list[int | None]]]] = {}
    cast: dict[str, dict[str, int]] = {}
    labels: dict[str, dict[str, list[str]]] = {}
    for sheet in sheets:
        rows = pl.read_excel(
            PARSED / "precincts.xlsx", sheet_name=sheet, has_header=False, infer_schema_length=0
        ).rows()
        heads, names = rows[1], rows[2]
        end = next(i for i, row in enumerate(rows) if row[0] == "Total")
        body = [row for row in rows[3:end] if not str(row[0]).startswith("Precincts Continue")]
        starts = [i for i, cell in enumerate(heads) if cell]
        for at, start in enumerate(starts):
            stop = starts[at + 1] if at + 1 < len(starts) else len(heads)
            stop = next((i for i in range(start, stop) if not names[i]), stop)
            title = " ".join(str(heads[start]).split())
            by_precinct = {
                precinct_label(row[0]): [None if row[i] in ("-", None) else int(row[i]) for i in range(start, stop)]
                for row in body
                if row[start] not in ("-", None)
            }
            if len(by_precinct) != sum(row[start] not in ("-", None) for row in body):
                raise ValueError(f"{sheet}: {title}: two precincts share a label")
            labels.setdefault(title, {})[sheet] = [" ".join(str(names[i]).split()) for i in range(start, stop)]
            found.setdefault(title, {})[sheet] = by_precinct
        tbc = pl.read_excel(PARSED / "ballots.xlsx", sheet_name=sheet, has_header=False, infer_schema_length=0).rows()
        stop = next(i for i, row in enumerate(tbc) if row[0] == "Total")
        cast[sheet] = {
            precinct_label(row[0]): int(row[1])
            for row in tbc[2:stop]
            if not str(row[0]).startswith("Precincts Continue")
        }
        if sum(cast[sheet].values()) != int(tbc[stop][1]):
            raise ValueError(f"{sheet}: precinct ballots differ from the printed total")
    return found, cast, labels


def emit() -> Path:
    contests = blocks()
    cast = ballots()
    by_title, in_precinct, columns = precinct_blocks(sorted({county for _, _, by in contests for county in by}))
    if {title for title, _, _ in contests} != set(by_title):
        raise ValueError("the precinct workbook and the summaries hold different contests")
    for sheet, precincts in in_precinct.items():
        if sum(precincts.values()) != ballot_counties()[sheet]:
            raise ValueError(f"{sheet}: precinct ballots differ from the summary")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wyoming November 3, 2026 general election, certified: U.S. Senate and House, statewide offices, "
            "state legislature, judicial retentions, and Initiative 1, by county",
            polity="US-WY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.wyo.gov/Elections/Docs/2026/2026GeneralResults.aspx",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, ZIP), source)
        cs.artifact(raw.cached(SOURCE, MINUTES), source)
        county_rows = {
            flat(text): (unit, area, code)
            for text, unit, area, code in conn.execute(
                """select n.text, u.id, u.area_id, u.code from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '56%%'"""
            ).fetchall()
        }
        counties = {name: row[0] for name, row in county_rows.items()}
        minted_units: set[UUID] = set()

        def precinct(county: str, label: str) -> str:
            parent, area, fips = county_rows[flat(county)]
            code = precinct_code.code(DAY.year, "WY", fips, label)
            unit = cs.unit("us.precinct", code)
            if unit not in minted_units:
                minted_units.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=state_polity,
                    scheme_code="us.precinct",
                    code=code,
                    parent_id=parent,
                    area_relation="within",
                    area_id=area,
                    kind="standard",
                    valid_from=DAY,
                    valid_to=DAY + timedelta(days=1),
                )
            return str(unit)

        state_area, state_polity = one(
            conn.execute(
                """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id
                   and u.scheme_code = 'us.state' where p.iso_code = 'US-WY'"""
            )
        )
        election = one(
            conn.execute(
                """select c.election_id from contest c join external_id x on x.entity_id = c.id
                   and x.scheme = 'us.contest' where x.value = %s""",
                [f"{DAY}:WY:governor"],
            )
        )[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/wy/%%'").fetchall()}
        retention = cs.key("us.electoral_system", "retention")
        fptp = cs.key("us.electoral_system", "fptp")
        districts: dict[int, tuple[UUID, UUID, frozenset[str]]] = {}
        offices: set[UUID] = set()

        def free(base: str) -> str:
            path, count = base, 1
            while path in taken:
                count += 1
                path = f"{base}-{count}"
            taken.add(path)
            return path

        def district(number: int, reach: frozenset[str]) -> tuple[UUID, UUID]:
            if number in districts:
                if districts[number][2] != reach:
                    raise ValueError(f"judicial district {number} covers different counties in different contests")
                return districts[number][:2]
            code = f"WY:jd-{number}"
            label = f"{ordinal(number)} Judicial District"
            area = cs.area("us.judicial_district", code)
            polity = cs.key("us.polity", f"judicial:{code}")
            districts[number] = (polity, area, reach)
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
                cs.add("name", entity_id=polity, text=f"Wyoming {label}", lang="en", kind="official", is_preferred=True)
                cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
                cs.add("slug", path=free(f"us/wy/{slug(label)}"), entity_id=polity)
                cs.add("area", id=area, scheme_code="us.judicial_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=state_area)
                cs.add("name", entity_id=area, text=f"{label}, WY", lang="en", kind="official", is_preferred=True)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add(
                    "reporting_unit",
                    id=cs.unit("us.judicial_district", code),
                    polity_id=polity,
                    scheme_code="us.judicial_district",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            for county in sorted(reach):
                cs.add(
                    "reporting_unit_area",
                    unit_id=counties[flat(county)],
                    area_id=area,
                    valid_from=DAY,
                    valid_to=DAY + timedelta(days=1),
                )
            return polity, area

        def lower_court(court: re.Match, judge: str, reach: frozenset[str]) -> tuple[str, list[tuple]]:
            shown = display(judge)
            if court[2]:
                number = ORDINALS.index(court[3]) + 1
                polity, area = district(number, reach)
                kind = f"{court[2].lower()}-court-judge"
                office_key = f"{kind}:WY:{number}"
                office_name = f"Wyoming {court[2]} Court Judge, {court[3]} Judicial District"
                path = f"{kind}-{number}-{slug(judge)}"
                label = f"{DAY.year} Wyoming {court[2]} Court Judge retention, {court[3]} Judicial District: {shown}"
            else:
                polity, area = state_polity, state_area
                kind = "chancery-court-judge"
                office_key = f"{kind}:WY"
                office_name = "Wyoming Chancery Court Judge"
                path = f"{kind}-{slug(judge)}"
                label = f"{DAY.year} Wyoming Chancery Court Judge retention: {shown}"
            key = f"{DAY}:WY:{path}"
            office = cs.key("us.office", office_key)
            if office not in offices:
                offices.add(office)
                cs.add("office", id=office, polity_id=polity, kind="other")
                if office in cs.minted:
                    cs.add("name", entity_id=office, text=office_name, lang="en", kind="official", is_preferred=True)
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=retention,
                default_result_kind="counted",
            )
            cs.add("name", entity_id=contest, text=label, lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=free(f"us/wy/{DAY}-general/{path}"), entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            first, last = identity(judge)
            person = cs.key("us.retention.person", f"WY:{slug(first)}-{slug(last)}")
            if person in cs.minted:
                cs.add("person", id=person)
                cs.add("name", entity_id=person, text=shown, lang="en", kind="official", is_preferred=True)
            options = []
            for position, answer in enumerate(("Yes", "No")):
                option = cs.key("us.option", f"{key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
                options.append((option, "answer", answer))
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{key}:yes:head"),
                person_id=person,
                option_id=options[0][0],
                role="head",
                ballot_name=judge,
                is_independent=False,
            )
            return key, options

        made = 0
        for title, names, by_county in contests:
            court = COURT.fullmatch(title)
            seat = SEAT.fullmatch(title)
            judge = court[4] if court else None
            minted: list[tuple] | None = None
            if title in STATEWIDE:
                key = f"{DAY}:WY:{STATEWIDE[title]}"
            elif seat:
                key = f"{DAY}:WY:{seat[1].lower()}:{seat[2]}:g"
            elif title == QUESTION:
                key = f"{DAY}:WY:question:proposed-initiative-proposition-number-one"
                names = [ANSWERS.get(name, name) for name in names]
            elif court and judge and court[1].startswith("Supreme"):
                key = f"{DAY}:WY:justice-of-the-supreme-court:{slug(judge)}"
            elif court and judge:
                key, minted = lower_court(court, judge, frozenset(by_county))
            else:
                raise ValueError(f"no contest for {title}")
            contest = cs.key("us.contest", key)
            if minted is None:
                found = conn.execute(
                    """select c.magnitude, c.electoral_system_id from contest c join external_id x
                       on x.entity_id = c.id and x.scheme = 'us.contest' where x.value = %s""",
                    [key],
                ).fetchall()
                if not found:
                    raise ValueError(f"{key} is not in the archive")
                magnitude = found[0][0] or 1
                if not court and title != QUESTION and found[0][1] != fptp:
                    raise ValueError(f"{key} is not a plurality contest")
                options = conn.execute(
                    """select o.id, o.kind, case when o.kind = 'candidate'
                       then coalesce(nullif(ca.ballot_name, ''), o.ballot_label) else o.ballot_label end
                       from contest_option o left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                       where o.contest_id = %s and o.parent_option_id is null""",
                    [contest],
                ).fetchall()
            else:
                magnitude, options = 1, minted
            labels = {option: words(label or "") for option, kind, label in options if kind != "write_in"}
            write_in = next((option for option, kind, _ in options if kind == "write_in"), None)
            chosen: dict[str, UUID] = {}
            for name in names:
                if name in SPOILED:
                    continue
                if name == "Write-Ins":
                    if write_in is None:
                        write_in = cs.key("us.option", f"{key}:write_in:scattered")
                        cs.add(
                            "contest_option",
                            id=write_in,
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Scattered",
                        )
                    chosen[name] = write_in
                    continue
                option = pick(re.sub(r"\s*\([A-Z]\)$", "", name), labels)
                if option is None or option in chosen.values():
                    raise ValueError(f"{key}: no single option for {name}")
                chosen[name] = option
            missing = set(labels) - set(chosen.values())
            if missing:
                raise ValueError(f"{key}: {len(missing)} options are not in the summary")
            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            body = f"{RETENTION} {NOTE}" if judge else f"{NOTE} {INITIATIVE}" if title == QUESTION else NOTE
            cs.add("note", entity_id=result, kind="methodology", body_md=body)
            totals = {name: sum(values[i] or 0 for values in by_county.values()) for i, name in enumerate(names)}
            if judge:
                kept = totals["Yes"] > totals["No"]
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=chosen["Yes"],
                    kind="elected" if kept else "lost",
                    seats=1 if kept else None,
                )
            elif title == QUESTION:
                passed = totals["Yes"] * 2 > cast
                cs.add("outcome", result_set_id=result, option_id=chosen["Yes" if passed else "No"], kind="won")
            else:
                ranked = sorted(chosen, key=lambda name: -totals[name])
                if "Write-Ins" in ranked[:magnitude] or (
                    len(ranked) > magnitude and totals[ranked[magnitude - 1]] == totals[ranked[magnitude]]
                ):
                    raise ValueError(f"{key}: no clear winner among {totals}")
                for name in ranked[:magnitude]:
                    cs.add("outcome", result_set_id=result, option_id=chosen[name], kind="elected", seats=1)
            values = []
            for county, counts in by_county.items():
                held = by_title[title][county]
                spelled = [ANSWERS.get(name, name) for name in columns[title][county]]
                if len(spelled) != len(names) or any(
                    (new in SPOILED or new == "Write-Ins") and new != old
                    for new, old in zip(spelled, names, strict=True)
                ):
                    raise ValueError(f"{key}: {county} precinct columns {spelled} differ from the summary {names}")
                sums = [sum(row[k] or 0 for row in held.values()) for k in range(len(names))]
                if sums != [count or 0 for count in counts]:
                    raise ValueError(f"{key}: {county} precinct sums {sums} differ from the county {counts}")
                for label, row in held.items():
                    unit = precinct(county, label)
                    values.append((str(result), unit, None, 0, 3, 0, 0, in_precinct[county][label]))
                    for name, count in zip(names, row, strict=True):
                        option = chosen.get(name)
                        values.append(
                            (
                                str(result),
                                unit,
                                str(option) if option else None,
                                0,
                                SPOILED.get(name, 1),
                                0,
                                0,
                                count or 0,
                            )
                        )
            cs.frame(
                "result_value",
                pl.DataFrame(
                    values,
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
            made += 1
            print(key, len(by_county), {name: total for name, total in totals.items() if name not in SPOILED})
        print(made)
        return cs.write()
