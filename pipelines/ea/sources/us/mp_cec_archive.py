import html
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.mp_cec_archive"
VERSION = "1"
PUBLISHER = "Commonwealth Election Commission"
SITE = "https://www.votecnmi.gov.mp/"
ROUND_ONE = "https://www.votecnmi.gov.mp/election-results/2009-general-election-results.php"
RUNOFF = "https://www.votecnmi.gov.mp/archive/97-archive/election-results/122-2009-general-election-run-off-results"
PDFS = {
    date(2005, 11, 5): SITE + "downloads/2005eresults.pdf",
    date(2008, 11, 4): SITE + "downloads/2008eresults.pdf",
}
MUNICIPIO = {"ED6": "69120", "ED7": "69100", "ED4D": "69085"}


def fetch() -> None:
    for url in (*PDFS.values(), ROUND_ONE, RUNOFF):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-skL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            pdf = url.endswith(".pdf")
            raw.store(
                SOURCE,
                url,
                body,
                suffix=".pdf" if pdf else ".html",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf" if pdf else "text/html",
            )


def emit() -> Path:
    tables: list[tuple[date, str, int, str, list[tuple[str, dict[str, int]]]]] = []
    for day, url in PDFS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
        ).stdout
        lines = [
            line for line in text.splitlines() if not re.search(r"(?i)^\s*(login|home\s+about|search\.\.\.)", line)
        ]
        start = next(index for index, line in enumerate(lines) if re.search(r"Candidate\s+ED 1", line))
        header = [
            (found.group(), (found.start() + found.end()) / 2) for found in re.finditer(r"ED \d|ABS", lines[start])
        ]
        rows = []
        block = []
        for line in [*lines[start + 1 :], ""]:
            if re.search(r"Candidate\s+ED 1", line):
                break
            if line.strip():
                block.append(line)
                continue
            numbers = [
                (found, center)
                for row in block
                for found, center in [
                    (f, (f.start() + f.end()) / 2) for f in re.finditer(r"(?<![\w(])\d[\d,]*(?![\w)])", row)
                ]
            ]
            if len(numbers) >= 3:
                label = re.sub(r"(?<![\w(])\d[\d,]*(?![\w)])", " ", " ".join(block))
                columns = {}
                for found, center in numbers[:-1]:
                    columns[min(header, key=lambda item: abs(item[1] - center))[0].replace(" ", "")] = int(
                        found.group().replace(",", "")
                    )
                rows.append((re.sub(r"\s+", " ", label).strip(), columns))
            block = []
        tables.append((day, "governor" if day.year == 2005 else "house:0", 1, url, rows))
    for day, url, round_number in ((date(2009, 11, 7), ROUND_ONE, 1), (date(2009, 11, 23), RUNOFF, 2)):
        page = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
        grid = [
            [
                html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
            ]
            for row in re.findall(r"<tr.*?</tr>", page[page.find("Governor/Lt") :], flags=re.DOTALL)
        ]
        header_row = next(index for index, cells in enumerate(grid) if cells and cells[0] == "Candidate")
        names = grid[header_row][1:]
        rows = []
        for cells in grid[header_row + 1 :]:
            if not cells or "/" not in cells[0]:
                break
            columns = {}
            for name, cell in zip(names, cells[1:], strict=False):
                if name.upper().endswith("TOTAL") and name.upper() != "TOTAL":
                    continue
                code = re.match(r"ED \d[A-D]?", name)
                columns[code[0].replace(" ", "") if code else name.upper()] = (
                    int(cell.replace(",", "")) if cell.replace(",", "").isdigit() else 0
                )
            columns.pop("TOTAL", None)
            rows.append((cells[0], columns))
        tables.append((day, "governor", round_number, url, rows))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Mariana Islands governor elections 2005 and 2009 (with the runoff) and the 2008 delegate election by election district from the Commonwealth Election Commission's archive",
            polity="US-MP",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-MP'").fetchone()
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchone()
        municipios = {
            code: (unit, unit_area)
            for code, unit, unit_area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and polity_id = %s",
                [state],
            ).fetchall()
        }
        (office,) = conn.execute(
            """select c.office_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = '2014-11-04:MP:governor'"""
        ).fetchone()
        systems = {
            "fptp": cs.key("us.electoral_system", "fptp"),
            "two_round": cs.key("us.electoral_system", "two_round"),
        }
        elections = {}
        for day, kind, title in (
            (date(2005, 11, 5), "general", "2005 Northern Mariana Islands general election"),
            (date(2009, 11, 7), "general", "2009 Northern Mariana Islands general election"),
            (date(2009, 11, 23), "runoff", "2009 Northern Mariana Islands runoff election"),
        ):
            election = cs.key("us.election", f"{day}:MP")
            elections[day] = election
            cs.add(
                "election",
                id=election,
                polity_id=state,
                kind=kind,
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            cs.add("name", entity_id=election, text=title, lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"us/mp/{day}-{kind}", entity_id=election)
        persons = {}
        contests = {}
        units = {}
        for day, section, round_number, url, rows in tables:
            title = f"{day.year} {'runoff' if round_number == 2 else 'general'} election results"
            source = cs.source(PUBLISHER, title)
            if source in cs.minted and not any(r["id"] == source for r in cs.rows.get("source", [])):
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url), source)
            key = f"{day}:MP:{section}"
            governor = section == "governor"
            if governor:
                contest = cs.key("us.contest", key)
                election = elections[day]
                contests[day] = contest
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=round_number,
                    previous_round_id=contests.get(date(2009, 11, 7)) if round_number == 2 else None,
                    electoral_system_id=systems["fptp" if day.year == 2005 else "two_round"],
                    default_result_kind="counted",
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{day.year} Northern Mariana Islands governor election"
                    + (", round 2" if round_number == 2 else ""),
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add(
                    "slug",
                    path=f"us/mp/{day}-{'runoff' if round_number == 2 else 'general'}/governor",
                    entity_id=contest,
                )
                options = []
                for label, _ in rows:
                    ticket = re.sub(r"\s*\([A-Za-z]+\)\s*$", "", label)
                    names = [part.strip() for part in re.split(r"\s+&\s+|\s*/\s*", ticket) if part.strip()]
                    people = [
                        " ".join(reversed([piece.strip() for piece in name.split(",", 1)])).title() for name in names
                    ]
                    option = cs.key("us.option", f"{key}:{re.sub(r'[^a-z]+', '-', ' '.join(people).lower())}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_label=" and ".join(people),
                    )
                    for role, person_name in zip(("head", "running_mate"), people, strict=False):
                        if person_name not in persons:
                            existing = conn.execute(
                                "select p.id from person p join name n on n.entity_id = p.id and n.text = %s limit 1",
                                [person_name],
                            ).fetchone()
                            if existing:
                                persons[person_name] = existing[0]
                            else:
                                persons[person_name] = cs.key(
                                    f"{SOURCE}.person", re.sub(r"[^a-z]+", "-", person_name.lower())
                                )
                                cs.add("person", id=persons[person_name])
                                cs.add(
                                    "name",
                                    entity_id=persons[person_name],
                                    text=person_name,
                                    lang="en",
                                    kind="official",
                                    is_preferred=True,
                                )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{option}:{role}"),
                            person_id=persons[person_name],
                            option_id=option,
                            role=role,
                        )
                    options.append(option)
            else:
                contest, election = conn.execute(
                    "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                    [key],
                ).fetchone()
                labels = conn.execute(
                    "select id, lower(ballot_label) from contest_option where contest_id = %s", [contest]
                ).fetchall()
                options = []
                for label, _ in rows:
                    surname = label.split(",")[0].strip().lower()
                    given = label.split(",")[1].split()[0].lower()
                    found = [option for option, text in labels if surname in text and given in text]
                    if len(found) != 1:
                        raise ValueError(f"{key}: {len(found)} options for {label}")
                    options.append(found[0])
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:district")
            if governor:
                completeness = 1
            else:
                columns_set = (
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
                    f"select {', '.join(columns_set)} from result_set where contest_id = %s and is_default and id <> %s",
                    [contest, result_set],
                ).fetchall():
                    cs.add("result_set", **dict(zip(columns_set, prior, strict=True)), is_default=False)
                for option, outcome_kind, seats in conn.execute(
                    """select o.option_id, o.kind, o.seats from outcome o
                       join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
                    [contest, result_set],
                ).fetchall():
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
                official = conn.execute(
                    "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
                    [contest],
                ).fetchone()[0]
                completeness = round(float(sum(sum(columns.values()) for _, columns in rows)) / float(official), 4)
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
                completeness=completeness,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Votes by election district as published; absentee ballots are a separate aggregate unit.",
            )
            if governor and (round_number == 2 or day.year == 2005):
                totals = [sum(columns.values()) for _, columns in rows]
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[totals.index(max(totals))],
                    kind="elected",
                    seats=None,
                )
            for (_, columns), option in zip(rows, options, strict=True):
                for name, value in columns.items():
                    aggregate = name == "ABS"
                    code = f"MP:{day}:{name}" if aggregate else f"{day}:MP:{name}"
                    if code not in units:
                        units[code] = cs.unit("us.aggregate" if aggregate else "us.precinct", code)
                        municipio = None if aggregate else municipios[MUNICIPIO.get(name, "69110")]
                        cs.add(
                            "reporting_unit",
                            id=units[code],
                            polity_id=state,
                            scheme_code="us.aggregate" if aggregate else "us.precinct",
                            code=code,
                            parent_id=municipio[0] if municipio else parent,
                            area_relation="within",
                            area_id=municipio[1] if municipio else area,
                            kind="aggregate" if aggregate else "standard",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[code],
                        option_id=option,
                        channel=3 if aggregate else 0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
