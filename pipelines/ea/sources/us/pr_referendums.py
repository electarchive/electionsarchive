import html
import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.pr_referendums"
VERSION = "1"
PUBLISHER = "Puerto Rico State Elections Commission"
TITLE = "Plebiscite and referendum canvass by municipality, 1967-2012"
SITE = "https://electionspuertorico.org/cgi-bin/events.cgi"

OPTION, BLANK, VOID, VALID, ADDIN = "option", "blank", "void", "valid", "addin"

EVENTS = [
    {
        "query": "evento=1967",
        "day": date(1967, 7, 23),
        "kind": "referendum",
        "type": "plebiscite",
        "slug": "status-plebiscite",
        "name": "1967 Puerto Rico status plebiscite",
        "layout": [
            ("Commonwealth", OPTION, True),
            ("Statehood", OPTION, True),
            ("Independence", OPTION, True),
            ("Void", VOID, False),
        ],
        "note": "Voters chose among commonwealth, statehood, and independence.",
    },
    {
        "query": "evento=1991",
        "day": date(1991, 12, 8),
        "kind": "referendum",
        "type": "referendum",
        "slug": "claim-of-democratic-rights",
        "name": "1991 Puerto Rico referendum on the Claim of Democratic Rights",
        "layout": [
            ("No", OPTION, True),
            ("Yes", OPTION, True),
            ("Blank", BLANK, False),
            ("Void", VOID, False),
            ("Add-ins", ADDIN, False),
        ],
        "note": "The Elections Commission's table lists add-in ballots, where voters wrote an addition to the printed question, in a column of their own.",
    },
    {
        "query": "evento=1993",
        "day": date(1993, 11, 14),
        "kind": "referendum",
        "type": "plebiscite",
        "slug": "status-plebiscite",
        "name": "1993 Puerto Rico status plebiscite",
        "layout": [
            ("Commonwealth", OPTION, True),
            ("Statehood", OPTION, True),
            ("Independence", OPTION, True),
            ("Void", VOID, True),
            ("Blank", BLANK, True),
        ],
        "note": "Voters chose among commonwealth, statehood, and independence.",
    },
    {
        "query": "evento=1994&voto=1",
        "day": date(1994, 11, 6),
        "kind": "referendum",
        "type": "mandatory_referendum",
        "slug": "constitutional-amendment-bail",
        "name": "1994 Puerto Rico constitutional amendment referendum: bail",
        "layout": [("No", OPTION, True), ("Yes", OPTION, True), ("Blank", BLANK, True), ("Void", VOID, True)],
        "note": "The amendment changed the Constitution's bail provision.",
    },
    {
        "query": "evento=1994&voto=2",
        "day": date(1994, 11, 6),
        "kind": "referendum",
        "type": "mandatory_referendum",
        "slug": "constitutional-amendment-supreme-court",
        "name": "1994 Puerto Rico constitutional amendment referendum: Supreme Court",
        "layout": [("No", OPTION, True), ("Yes", OPTION, True), ("Blank", BLANK, True), ("Void", VOID, True)],
        "note": "The amendment concerned the Supreme Court.",
    },
    {
        "query": "evento=1998",
        "day": date(1998, 12, 13),
        "kind": "referendum",
        "type": "plebiscite",
        "slug": "status-plebiscite",
        "name": "1998 Puerto Rico status plebiscite",
        "layout": [
            ("None of the above", OPTION, True),
            ("Statehood", OPTION, True),
            ("Independence", OPTION, True),
            ("Free Association", OPTION, True),
            ("Territorial Commonwealth", OPTION, True),
            ("Void", VOID, True),
            ("Blank", BLANK, True),
        ],
        "positions": {"Territorial Commonwealth": 1, "Free Association": 2, "Statehood": 3, "Independence": 4},
        "note": "The numbers the Elections Commission prints over four of the options are their ballot columns.",
    },
    {
        "query": "evento=2005",
        "day": date(2005, 7, 10),
        "kind": "referendum",
        "type": "referendum",
        "slug": "legislative-assembly",
        "name": "2005 Puerto Rico referendum on the structure of the Legislative Assembly",
        "layout": [
            ("Unicameral Legislative Assembly", OPTION, True),
            ("Bicameral Legislative Assembly", OPTION, True),
            ("Blank", BLANK, True),
            ("Void", VOID, True),
        ],
        "positions": {"Unicameral Legislative Assembly": 1, "Bicameral Legislative Assembly": 2},
        "note": "Voters chose between a one-chamber and a two-chamber Legislative Assembly.",
    },
    {
        "query": "evento=2012&voto=1",
        "day": date(2012, 8, 19),
        "kind": "referendum",
        "type": "mandatory_referendum",
        "slug": "constitutional-amendment-legislative-reform",
        "name": "2012 Puerto Rico constitutional amendment referendum: legislative reform",
        "layout": [
            ("No", OPTION, True),
            ("Yes", OPTION, True),
            ("Valid", VALID, False),
            ("Blank", BLANK, True),
            ("Void", VOID, True),
        ],
        "note": "The amendment concerned the Legislative Assembly.",
    },
    {
        "query": "evento=2012&voto=2",
        "day": date(2012, 8, 19),
        "kind": "referendum",
        "type": "mandatory_referendum",
        "slug": "constitutional-amendment-bail",
        "name": "2012 Puerto Rico constitutional amendment referendum: bail",
        "layout": [
            ("No", OPTION, True),
            ("Yes", OPTION, True),
            ("Valid", VALID, False),
            ("Blank", BLANK, True),
            ("Void", VOID, True),
        ],
        "note": "The amendment concerned the right to bail.",
    },
    {
        "query": "evento=2012&voto=p",
        "day": date(2012, 11, 6),
        "kind": "general",
        "type": "plebiscite",
        "slug": "status-plebiscite-question-1",
        "name": "2012 Puerto Rico status plebiscite, question 1: present form of territorial status",
        "layout": [
            ("No", OPTION, True),
            ("Yes", OPTION, True),
            ("Valid", VALID, False),
            ("Blank", BLANK, True),
            ("Void", VOID, True),
        ],
        "note": "The first question asked whether the voter agreed that Puerto Rico should continue its present form of territorial status.",
    },
    {
        "query": "evento=2012&voto=o",
        "day": date(2012, 11, 6),
        "kind": "general",
        "type": "plebiscite",
        "slug": "status-plebiscite-question-2",
        "name": "2012 Puerto Rico status plebiscite, question 2: non-territorial options",
        "layout": [
            ("Statehood", OPTION, True),
            ("Sovereign Commonwealth", OPTION, True),
            ("Independence", OPTION, True),
            ("Valid", VALID, False),
            ("Blank", BLANK, True),
            ("Void", VOID, True),
        ],
        "note": "The second question asked the voter to choose among statehood, sovereign commonwealth, and independence whatever the answer to the first. Blank ballots on this question include voters who left it unmarked.",
    },
]


def url(event: dict) -> str:
    return f"{SITE}?{event['query']}"


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def fetch() -> None:
    for event in EVENTS:
        try:
            raw.cached(SOURCE, url(event))
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfL", "--compressed", "-A", raw.USER_AGENT, url(event)],
                capture_output=True,
                check=True,
            ).stdout
            raw.store(
                SOURCE,
                url(event),
                body,
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )


def number(text: str) -> int:
    return int(text.replace(",", ""))


def read(event: dict) -> tuple[dict[str, dict[str, int]], dict[str, int], dict[str, int]]:
    text = raw.cached(SOURCE, url(event)).path.read_text(encoding="latin-1")
    layout = event["layout"]
    width = sum(2 if pct else 1 for _, _, pct in layout) + 1
    rows: dict[str, dict[str, int]] = {}
    total: dict[str, int] = {}
    absentee: dict[str, int] = {}
    for row in re.findall(r"<TR.*?</TR>", text, re.DOTALL | re.IGNORECASE):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
            for cell in re.findall(r"<T[DH][^>]*>(.*?)</T[DH]>", row, re.DOTALL | re.IGNORECASE)
        ]
        cells = [cell for cell in cells if cell]
        if cells and cells[0].startswith("Absentee") and len(cells) == width:
            cells.insert(-1, "0")
        if len(cells) != width + 1 or not re.fullmatch(r"[\d,]+", cells[1]):
            continue
        values: dict[str, int] = {}
        at = 1
        for label, _, pct in layout:
            values[label] = number(cells[at])
            at += 2 if pct else 1
        values["Total"] = number(cells[at])
        if cells[0].startswith("Total"):
            total = values
        elif cells[0].startswith("Absentee"):
            absentee = values
        else:
            rows[cells[0]] = values
    if not total or len(rows) != (76 if absentee else 78):
        raise ValueError(f"{event['query']}: {len(rows)} municipios")
    for label in [*(label for label, _, _ in layout), "Total"]:
        if sum(row[label] for row in [*rows.values(), absentee] if row) != total[label]:
            raise ValueError(f"{event['query']} {label}: municipios do not sum to the printed total")
    short: dict[str, int] = {}
    for name, row in rows.items():
        valid = row.get("Valid")
        options = sum(row[label] for label, role, _ in layout if role == OPTION)
        if valid is not None and valid != options:
            raise ValueError(f"{event['query']} {name}: valid {valid} against options {options}")
        gap = row["Total"] - sum(v for k, v in row.items() if k not in ("Total", "Valid"))
        if gap:
            if abs(gap) > row["Total"] / 100:
                raise ValueError(f"{event['query']} {name}: ballots differ from the printed total by {gap}")
            short[name] = gap
    return rows, absentee, short


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico status plebiscites and constitutional amendment referendums 1967-2012 by municipio",
            polity="US-PR",
        )
        polity = cs.polity("US-PR")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        system = cs.key("us.electoral_system", "referendum")
        units = {
            fold(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '72%%'"""
            ).fetchall()
        }
        if len(units) != 78:
            raise ValueError("Puerto Rico needs 78 municipios")
        island = cs.unit("us.state", "72")
        made: set[UUID] = set()
        for event in EVENTS:
            day = event["day"]
            rows, absentee, short = read(event)
            cs.artifact(raw.cached(SOURCE, url(event)), source)
            election = cs.key("us.election", f"{day}:PR")
            if election in cs.minted and election not in made:
                made.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=event["kind"],
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{day.year} Puerto Rico {'plebiscite' if event['type'] == 'plebiscite' else 'referendum'}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/pr/{day}-referendum", entity_id=election)
            contest_key = f"{day}:PR:question:{event['slug']}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=event["type"])
            cs.add(
                "name",
                entity_id=contest,
                text=event["name"],
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                folder = "general" if event["kind"] == "general" else "referendum"
                cs.add("slug", path=f"us/pr/{day}-{folder}/{event['slug']}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            options: dict[str, UUID] = {}
            positions = event.get("positions", {})
            for at, (label, role, _) in enumerate(event["layout"]):
                if role not in (OPTION, ADDIN):
                    continue
                option = cs.key("us.option", f"{contest_key}:{slug(label)}")
                options[label] = option
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer" if role == OPTION else "others",
                    ballot_position=positions.get(label) if positions else at + 1,
                    ballot_label=label,
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=event["note"])
            if short:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="In the Elections Commission's table, the printed ballots total differs from the sum of its columns in "
                    + ", ".join(f"{name} (by {gap})" for name, gap in short.items())
                    + ", and the printed figures are kept.",
                )
            lines = [(units[fold(name)], row) for name, row in rows.items()]
            if absentee:
                lines.append((island, absentee))
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="The Elections Commission's table reports the absentee ballots on a line of their own, which is recorded against the island as a whole rather than any municipio.",
                )
            for unit, row in lines:
                for label, role, _ in event["layout"]:
                    if role in (OPTION, ADDIN):
                        option, measure = options[label], 1
                    else:
                        option, measure = None, {BLANK: 6, VOID: 5, VALID: 4}[role]
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=row[label],
                    )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=3,
                    count_round=0,
                    flags=0,
                    value=row["Total"],
                )
        return cs.write()
