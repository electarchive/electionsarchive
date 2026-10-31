import re
import subprocess
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ok_pri_pdf
from ea.sources.us.oe_primaries_2026 import plain, title
from ea.store import DATA, connect

SOURCE = "us.ok_pri_state"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-334"
PUBLISHER = ok_pri_pdf.PUBLISHER
TITLE = ok_pri_pdf.TITLE
PARTIES = {
    "democratic": ("democratic", "dp", "Democratic"),
    "republican": ("republican", "rp", "Republican"),
    "libertarian": ("libertarian", "lp", "Libertarian"),
}
OFFICES = {
    "GOVERNOR": "governor",
    "LIEUTENANT GOVERNOR": "lieutenant-governor",
    "ATTORNEY GENERAL": "attorney-general",
    "STATE AUDITOR AND INSPECTOR": "auditor-and-inspector",
    "STATE TREASURER": "treasurer",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "superintendent-of-public-instruction",
    "COMMISSIONER OF LABOR": "commissioner-of-labor",
    "INSURANCE COMMISSIONER": "insurance-commissioner",
    "CORPORATION COMMISSIONER": "corporation-commissioner",
    "CORPORATION COMMISSIONER (SHORT TERM)": "corporation-commissioner",
}
NOTE = (
    "Results by county from the Oklahoma State Election Board's primary results. The statewide total for each "
    "candidate is the sum of the county votes, which equals the printed state total."
)
OFFICE_LINE = re.compile(r"^\s{10,}([A-Z][A-Z ,.&()\-]+?)(?:, DISTRICT (\d+))?\s*$")
ELECTION_LINE = re.compile(
    r"(Democratic|Republican|Libertarian)\s+(?:Runoff Primary|Primary|Runoff)?\s*Election\s+\W+\s+(\w+) (\d+), (\d{4})"
)
COUNTY_ROW = re.compile(r"^([A-Za-z][A-Za-z .']*?):?\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$")
RACE_2010 = re.compile(r"^Race:\s+FOR (.+?)\s*$")
ROW_2010 = re.compile(r"^([A-Za-z][A-Za-z .']*?):?\s{2,}(?=[\d,])")
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}
ALIASES = {"democrat": "democratic"}
YEARS = (2000, 2002, 2004, 2006, 2008)


def files() -> list[tuple[str, Path, bool]]:
    found = []
    for day, path in ok_pri_pdf.FILES:
        name = Path(path).name
        if day.year in YEARS and re.fullmatch(r"\d\d(dem|rep)(pri|run)\.pdf", name):
            found.append((ok_pri_pdf.BASE + path, INBOX / name, name.endswith("run.pdf")))
        elif day.year == 2010:
            found.append((ok_pri_pdf.BASE + path, INBOX / name, name.startswith("runoff")))
    link = ok_pri_pdf.BASE + "2000-election-results/2000-primary-election-results/00libpri.pdf"
    found.append((link, INBOX / "00libpri.pdf", False))
    return found


def fetch() -> None:
    for link, file, _ in files():
        try:
            raw.cached(SOURCE, link)
        except LookupError:
            raw.store(
                SOURCE,
                link,
                file.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def pdf_text(link: str) -> list[str]:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, link).path), "-"], capture_output=True, check=True, text=True
    ).stdout.splitlines()


def spans_of(line: str) -> list[tuple[int, int]]:
    return [match.span() for match in re.finditer(r"=+", line)]


def place_cells(line: str, start: int, ends: list[int]) -> list[int]:
    found = list(re.finditer(r"[\d,]+", line[start:]))
    if len(found) == len(ends):
        return [int(match[0].replace(",", "")) for match in found]
    cells = [0] * len(ends)
    for match in found:
        column = min(range(len(ends)), key=lambda i: abs(ends[i] - (match.end() + start)))
        cells[column] = int(match[0].replace(",", ""))
    return cells


def old_header(lines: list[str], at: int) -> list[str]:
    spans = spans_of(lines[at])
    upper = []
    row = at - 1
    while row >= 0 and lines[row].strip() and not ELECTION_LINE.search(lines[row]):
        upper.append(lines[row])
        row -= 1
    names = []
    for a, b in spans:
        parts = []
        for line in upper[::-1]:
            for phrase in re.finditer(r"\S+(?: \S+)*", line):
                if a - 3 <= (phrase.start() + phrase.end()) / 2 <= b + 3:
                    parts.append(phrase[0])
        names.append(" ".join(parts))
    return names


def blocks_old(link: str) -> dict[tuple[str, int | None, str, date], dict]:
    lines = pdf_text(link)
    found: dict[tuple[str, int | None, str, date], dict] = {}
    key = None
    names: list[str] | None = None
    ends: list[int] = []
    for at, line in enumerate(lines):
        header = ELECTION_LINE.search(line)
        office = OFFICE_LINE.match(lines[at - 1]) if header and at else None
        if office:
            day = date(int(header[4]), MONTHS[header[2]], int(header[3]))
            key = (office[1].strip(), int(office[2]) if office[2] else None, header[1].lower(), day)
            found.setdefault(key, {"names": None, "rows": [], "total": None})
            names = None
        elif key is None:
            continue
        elif line.strip().startswith("====") and names is None:
            names = old_header(lines, at)
            ends = [b for _, b in spans_of(line)]
            found[key]["names"] = found[key]["names"] or names
        elif (row := COUNTY_ROW.match(line)) and names is not None:
            cells = place_cells(line, len(row[1]) + 1, ends)
            if row[1].upper().startswith("STATE TOTAL"):
                found[key]["total"] = cells
                key = None
            else:
                found[key]["rows"].append((row[1], cells))
    return found


def blocks_2010(link: str, day: date) -> dict[tuple[str, int | None, str, date], dict]:
    lines = pdf_text(link)
    chunks: dict[tuple[str, int | None, str], list[dict]] = defaultdict(list)
    key = None
    pending: tuple[str, int | None] = ("", None)
    current: dict = {}
    for at, line in enumerate(lines):
        if race := RACE_2010.match(line):
            district = re.match(r"(.+?), DISTRICT NO\. (\d+)$", race[1])
            pending = (district[1], int(district[2])) if district else (race[1], None)
            key = None
        elif line.startswith("Party:"):
            key = (*pending, line.split(":", 1)[1].strip().lower())
            current = {"names": None, "rows": {}, "total": None}
            chunks[key].append(current)
        elif key is None:
            continue
        elif line.startswith("=====") and current["names"] is None:
            spans = spans_of(line)[1:]
            current["names"] = [lines[at - 1][a:b].strip() for a, b in spans]
            current["ends"] = [b for _, b in spans]
            prior = chunks[key][:-1]
            if prior and prior[-1]["names"] == current["names"] and prior[-1]["total"] is None:
                chunks[key].pop()
                current = prior[-1]
        elif (row := ROW_2010.match(line)) and current["names"] is not None and re.search(r"\d", line):
            cells = place_cells(line, row.end(), current["ends"])
            if row[1].upper().startswith("STATE TOTAL"):
                current["total"] = cells
                key = None
            else:
                current["rows"][row[1]] = cells
    found: dict[tuple[str, int | None, str, date], dict] = {}
    for (office, district, party), parts in chunks.items():
        names: list[str] = []
        total: list[int] = []
        rows: dict[str, list[int]] = {}
        for part in parts:
            if part["total"] is None:
                raise ValueError(f"{office} {district}: no state total")
            names += part["names"]
            total += part["total"]
            for county, cells in part["rows"].items():
                rows.setdefault(county, []).extend(cells)
        found[(office, district, ALIASES.get(party, party), day)] = {
            "names": names,
            "rows": list(rows.items()),
            "total": total,
        }
    return found


def races() -> dict[tuple[str, int | None, str, date], dict]:
    found: dict[tuple[str, int | None, str, date], dict] = {}
    for link, _, _ in files():
        name = Path(link).name
        if name.endswith("-20100727.pdf"):
            part = blocks_2010(link, date(2010, 7, 27))
        elif name.endswith("-20100824.pdf"):
            part = blocks_2010(link, date(2010, 8, 24))
        else:
            part = blocks_old(link)
        found.update(part)
    kept = {}
    for key, block in found.items():
        office, _, party, _ = key
        if party not in PARTIES or not (office in OFFICES or office in ("STATE REPRESENTATIVE", "STATE SENATOR")):
            continue
        names = list(block["names"])
        total = block["total"]
        if names[-1].upper().endswith("VOTES"):
            names.pop()
            totals = total[:-1]
            if sum(totals) != total[-1]:
                raise ValueError(f"{key}: candidates sum to {sum(totals)}, printed {total[-1]}")
            cells = [(county, values[:-1]) for county, values in block["rows"]]
        else:
            totals = total
            cells = block["rows"]
        sums = [sum(values[i] for _, values in cells) for i in range(len(names))]
        if not all(names) or len(names) != len(totals) or sums != totals or any(len(v) != len(names) for _, v in cells):
            raise ValueError(f"{key}: counties sum to {sums}, printed {totals}, names {names}")
        kept[key] = {name: {county: values[i] for county, values in cells} for i, name in enumerate(names)}
    return kept


def emit() -> Path:
    loaded = races()
    days = sorted({key[3] for key in loaded})
    primaries = {day.year: min(d for d in days if d.year == day.year) for day in days}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2000 to 2010 state legislative and statewide primaries and runoffs by county from the State Election Board's results PDFs",
            polity="US-OK",
        )
        counties = {
            plain(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }

        def shape(day: date, office: str, district: int | None) -> tuple:
            if office in OFFICES:
                pattern = rf"^\d{{4}}-11-\d\d:OK:{OFFICES[office]}$"
            else:
                chamber = "house" if office == "STATE REPRESENTATIVE" else "senate"
                pattern = rf"^\d{{4}}-11-\d\d:OK:{chamber}:{district}:gs?$"
            row = conn.execute(
                """select c.office_id, c.body_id, c.district_area_id, c.seat_id, c.boundary_set_id, c.magnitude, c.term, c.kind
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value ~ %s order by abs(left(x.value, 4)::int - %s), x.value desc limit 1""",
                [pattern, day.year],
            ).fetchone()
            if row is None:
                raise ValueError(f"no general template for {office} {district}")
            return row

        scope: dict[str, tuple] = {}
        for party, key in (
            ("democratic", "2018-06-26:OK:house:1:democratic"),
            ("republican", "2018-06-26:OK:house:1:republican"),
            ("libertarian", "2000-08-22:OK:house:1:libertarian"),
        ):
            scope[party] = conn.execute(
                """select c.party_scope_id, (select o.party_id from contest_option o where o.contest_id = c.id limit 1)
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value = %s""",
                [key],
            ).fetchone()
        system = conn.execute(
            """select c.electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2014-06-24:OK:house:1:rpfsettled'"""
        ).fetchone()[0]
        pool: dict[str, set[UUID]] = defaultdict(set)
        for person, text in conn.execute(
            """select cy.person_id, n.text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ '^20(0[0-9]|1[0-9]|2[0-9])-[0-9-]+:OK:'"""
        ).fetchall():
            pool[plain(text)].add(person)

        def person_of(text: str) -> UUID:
            found = pool.get(plain(text), set())
            if len(found) == 1:
                return next(iter(found))
            id = cs.key(f"{SOURCE}.person", f"OK:{slug(text)}")
            if id in cs.minted and all(row["id"] != id for row in cs.rows.get("person", [])):
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=text, lang="en", kind="official", is_preferred=True)
            return id

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://oklahoma.gov/elections/elections-results/election-results.html",
                license="Public record",
            )
        for link, _, _ in files():
            cs.artifact(raw.cached(SOURCE, link), source)
        elections: dict[date, UUID] = {}
        for day in days:
            kind = "primary" if primaries[day.year] == day else "runoff"
            election = elections[day] = cs.key("us.election", f"{day}:OK:{kind}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-OK"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{day:%B} {day.day}, {day.year} Oklahoma primary {'runoff ' if kind == 'runoff' else ''}election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/ok/{day}-{kind}", entity_id=election)

        made = 0
        for (office, district, party, day), by_candidate in loaded.items():
            first = primaries[day.year]
            runoff = day != first
            word, etype, label = PARTIES[party]
            if office in OFFICES:
                tail = slug(office.removeprefix("STATE "))
                contest_key = f"{day}:OK:{tail}:{party}"
                previous = f"{first}:OK:{tail}:{party}" if runoff else None
                name = f"{day.year} Oklahoma {office.removeprefix('STATE ').title()} {label} primary{' runoff' if runoff else ''}"
                path = f"{tail}-{word}-primary" + ("-runoff" if runoff else "")
            else:
                chamber = "house" if office == "STATE REPRESENTATIVE" else "senate"
                contest_key = f"{day}:OK:{chamber}:{district}:{etype}" + ("runoff" if runoff else "")
                previous = f"{first}:OK:{chamber}:{district}:{etype}" if runoff else None
                body = "House of Representatives" if chamber == "house" else "Senate"
                name = f"{day.year} Oklahoma {body} {label} primary{' runoff' if runoff else ''}, District {district}"
                path = f"state-{chamber}-{district}-{word}-primary" + ("-runoff" if runoff else "")
            if runoff and (office, district, party, first) not in loaded:
                raise ValueError(f"{contest_key}: no first round in {first}")
            shaped = shape(day, office, district)
            advanced = {
                candidate
                for (o, d, p, other), found in loaded.items()
                if (o, d, p) == (office, district, party) and other != day and other.year == day.year and not runoff
                for candidate in found
            }
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day],
                kind=shaped[7],
                office_id=shaped[0],
                body_id=shaped[1],
                district_area_id=shaped[2],
                seat_id=shaped[3],
                boundary_set_id=shaped[4],
                magnitude=shaped[5],
                party_scope_id=scope[party][0],
                term=shaped[6],
                round_number=2 if runoff else 1,
                previous_round_id=cs.key("us.contest", previous) if runoff else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add("name", entity_id=contest, text=name, lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=f"us/ok/{day}-{'runoff' if runoff else 'primary'}/{path}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=elections[day],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            ranked = sorted(by_candidate, key=lambda candidate: -sum(by_candidate[candidate].values()))
            options = {}
            for candidate in ranked:
                text = title(candidate)
                options[candidate] = cs.key("us.option", f"{contest_key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=options[candidate],
                    contest_id=contest,
                    kind="candidate",
                    party_id=scope[party][1],
                    ballot_label=text,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(text)}"),
                    person_id=person_of(text),
                    option_id=options[candidate],
                    role="head",
                    ballot_name=text,
                    is_independent=False,
                )
            for rank, candidate in enumerate(ranked):
                if candidate in advanced:
                    kind = "advanced"
                elif rank == 0 and not advanced:
                    kind = "won"
                else:
                    continue
                cs.add("outcome", result_set_id=result, option_id=options[candidate], kind=kind)
            by_place: dict[str, int] = defaultdict(int)
            rows = []
            for candidate, by_county in by_candidate.items():
                for county, votes in by_county.items():
                    fips = counties[plain(county)]
                    by_place[fips] += votes
                    rows.append(
                        (str(result), str(cs.unit("us.county", fips)), str(options[candidate]), 0, 1, 0, 0, votes)
                    )
            rows += [
                (str(result), str(cs.unit("us.county", fips)), None, 0, 4, 0, 0, votes)
                for fips, votes in by_place.items()
            ]
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
            made += 1
        print(made)
        return cs.write()
