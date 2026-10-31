import csv
import html
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.oe_primaries_2026 import plain, title
from ea.store import DATA, connect

SOURCE = "us.tx_primaries_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = DATA / "inbox" / "manual"
FILES = {
    ("2026-03-03", "republican"): (
        "https://www.sos.state.tx.us/elections/historical/#republican/CountyByCountyCanvassReport.pdf",
        MANUAL / "MF-061" / "republican" / "CountyByCountyCanvassReport.pdf",
    ),
    ("2026-03-03", "democratic"): (
        "https://www.sos.state.tx.us/elections/historical/#democratic/CountyByCountyCanvassReport.pdf",
        MANUAL / "MF-061" / "democratic" / "CountyByCountyCanvassReport.pdf",
    ),
    ("2026-05-26", "republican"): (
        "https://goelect.txelections.civixapps.com/ivis-enr-ui#CountyByCountyCanvassReport.pdf",
        MANUAL / "MF-152" / "CountyByCountyCanvassReport.pdf",
    ),
    ("2026-05-26", "democratic"): (
        "https://goelect.txelections.civixapps.com/ivis-enr-ui#CountyByCountyCanvassReport (1).pdf",
        MANUAL / "MF-152" / "CountyByCountyCanvassReport (1).pdf",
    ),
}
OE = "https://raw.githubusercontent.com/openelections/openelections-data-tx/master/2026/20260303__tx__primary__county.csv"
HEADING = re.compile(
    r"U\. S\. (?:SENATOR|REPRESENTATIVE DISTRICT \d+)|GOVERNOR|LIEUTENANT GOVERNOR|ATTORNEY GENERAL"
    r"|COMPTROLLER OF PUBLIC ACCOUNTS|COMMISSIONER OF (?:AGRICULTURE|THE GENERAL LAND OFFICE)|RAILROAD COMMISSIONER"
    r"|(?:CHIEF )?JUSTICE, .+|(?:PRESIDING )?JUDGE, COURT OF CRIMINAL APPEALS.*|MEMBER, STATE BOARD OF EDUCATION, DISTRICT \d+"
    r"|STATE (?:SENATOR|REPRESENTATIVE).*|(?:CRIMINAL )?DISTRICT (?:JUDGE|ATTORNEY).+|PROPOSITION \d+"
    r"|\d+(?:ST|ND|RD|TH) MULTICOUNTY COURT AT LAW"
)
MASTHEAD = re.compile(
    r"Texas Secretary of State|County by County Canvass Report|\d{4} (?:REPUBLICAN|DEMOCRATIC) PRIMARY.*"
    r"|(?:March|May) \d\d, 2026|.*\bPage \d+ of \d+"
)
NUMBER = re.compile(r"[\d,]+")
UNMATCHED = {
    "steve-burgess",
    "kevin-jackson",
    "greg-travis",
    "gerald-fowler",
    "noe-garza",
    "oscar-salinas",
    "robert-johnson",
    "ryan-hill",
    "greg-hill",
    "josh-hill",
    "david-hall",
    "david-klein",
    "brian-willett",
    "john-ball",
    "josh-tetens",
    "jamie-kaye-jordan",
    "michael-de-leon",
    "adam-khan",
    "hector-garza",
}
STATEWIDE = {
    "LIEUTENANT GOVERNOR": ("lieutenant-governor", "Lieutenant Governor"),
    "ATTORNEY GENERAL": ("attorney-general", "Attorney General"),
    "COMPTROLLER OF PUBLIC ACCOUNTS": ("comptroller", "Comptroller of Public Accounts"),
    "COMMISSIONER OF THE GENERAL LAND OFFICE": ("land-commissioner", "Commissioner of the General Land Office"),
    "COMMISSIONER OF AGRICULTURE": ("commissioner-of-agriculture", "Commissioner of Agriculture"),
    "RAILROAD COMMISSIONER": ("railroad-commissioner", "Railroad Commissioner"),
}


def fetch() -> None:
    for url, path in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 27, tzinfo=UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    records = []
    totals = []
    for (day, party), (url, _) in FILES.items():
        document = subprocess.run(
            ["pdftotext", "-bbox-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True
        ).stdout.decode()
        contest = None
        panel, row, order, header = 0, 0, [], []
        columns: list[tuple[float, str, bool]] = []
        for page in document.split("<page ")[1:]:
            lines: dict[float, list[tuple[float, float, str]]] = {}
            for left, top, right, word in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)"[^>]*>([^<]*)</word>', page
            ):
                key = next((line for line in lines if abs(line - float(top)) < 2), float(top))
                lines.setdefault(key, []).append((float(left), float(right), html.unescape(word)))
            header = []
            for top in sorted(lines):
                words = sorted(lines[top])
                text = " ".join(word for *_, word in words)
                if MASTHEAD.fullmatch(text):
                    continue
                if HEADING.fullmatch(text):
                    contest, panel, row, order, header, columns = text, 0, 0, [], [], []
                    continue
                if contest is None:
                    continue
                numbers = [((left + right) / 2, word) for left, right, word in words if NUMBER.fullmatch(word)]
                if not numbers:
                    header += [(top, (left + right) / 2, word) for left, right, word in words]
                    continue
                if header:
                    centers = [center for center, _ in numbers]
                    letters = [""] * len(centers)
                    marks = [False] * len(centers)
                    total = [False] * len(centers)
                    for _, center, word in sorted(header):
                        column = min(range(len(centers)), key=lambda index: abs(centers[index] - center))
                        if word in ("Total", "Votes"):
                            total[column] = True
                        elif word == "(I)":
                            marks[column] = True
                        elif not re.fullmatch(r"County|\[[A-Z]\]", word):
                            letters[column] += f" {word}"
                    columns = [
                        (centers[index], letters[index].strip(), marks[index])
                        for index in range(len(centers))
                        if not total[index]
                    ]
                    header = []
                county = " ".join(word for _, _, word in words if not NUMBER.fullmatch(word))
                if not columns:
                    continue
                values = {}
                for center, word in numbers:
                    column = min(range(len(columns)), key=lambda index: abs(columns[index][0] - center))
                    if abs(columns[column][0] - center) < 40:
                        values[column] = int(word.replace(",", ""))
                if county == "TOTAL VOTES":
                    totals.extend(
                        {"day": day, "party": party, "contest": contest, "candidate": columns[index][1], "votes": votes}
                        for index, votes in values.items()
                    )
                    panel, row = panel + 1, 0
                    continue
                if county:
                    if panel == 0:
                        order.append(county)
                elif row < len(order):
                    county = order[row]
                row += 1
                records.extend(
                    {
                        "day": day,
                        "party": party,
                        "contest": contest,
                        "candidate": columns[index][1],
                        "incumbent": columns[index][2],
                        "county": county,
                        "votes": votes,
                    }
                    for index, votes in values.items()
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(records)
    summed = frame.group_by("day", "party", "contest", "candidate").agg(pl.col("votes").sum())
    check = pl.DataFrame(totals).join(summed, on=["day", "party", "contest", "candidate"], how="full", suffix="_rows")
    bad = check.filter(
        pl.col("votes").is_null() | pl.col("votes_rows").is_null() | (pl.col("votes") != pl.col("votes_rows"))
    )
    print(frame.group_by("day", "party").agg(pl.col("contest").n_unique(), pl.len()))
    print("total mismatches", bad.height)
    print(bad.head(30))
    frame.write_parquet(PARSED / "rows.parquet")


def ordinal(number: int) -> str:
    return f"{number}{'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')}"


def letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's 2026 primaries for statewide offices other than governor, the State Board of Education, the courts, and district attorneys, by county, and every May 26 primary runoff for those offices, the U.S. Senate, and the U.S. House, from the Secretary of State's county-by-county canvass reports",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state_polity = cs.polity("US-TX")
        (state_area,) = conn.execute(
            "select area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state_polity]
        ).fetchone()
        counties: dict[str, object] = {}
        county_areas: dict[str, tuple[str, object]] = {}
        for code, text, unit, area in conn.execute(
            """select u.code, n.text, u.id, u.area_id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ '^48'"""
        ).fetchall():
            counties[plain(text)] = unit
            county_areas[plain(text)] = (code, area, text)
        held = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join contest c on c.id = x.entity_id
                   join changeset k on k.id = c.changeset_id
                   where x.scheme = 'us.contest' and x.value ~ '^2026-0[35]-[0-9]+:TX:' and k.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        federal = {}
        for value, *row in conn.execute(
            """select x.value, c.kind, c.office_id, c.body_id, c.district_area_id, c.seat_id, c.boundary_set_id,
                      c.magnitude, c.term, c.party_scope_id, c.default_result_kind
               from external_id x join contest c on c.id = x.entity_id
               where x.scheme = 'us.contest' and x.value ~ '^2026-03-03:TX:(house:[0-9]+|senate):[a-z]+$'"""
        ).fetchall():
            federal[value] = row
        finalists: dict[str, list[tuple[str, object]]] = defaultdict(list)
        for value, label, person in conn.execute(
            """select x.value, o.ballot_label, cy.person_id from external_id x
               join contest_option o on o.contest_id = x.entity_id join candidacy cy on cy.option_id = o.id
               join entity en on en.id = cy.id and en.retired_by is null
               where x.scheme = 'us.contest' and x.value ~ '^2026-03-03:TX:(house:[0-9]+|senate):[a-z]+$'"""
        ).fetchall():
            finalists[value].append((label, person))
        pool: dict[str, set[object]] = defaultdict(set)
        for person, text in conn.execute(
            """select distinct cy.person_id, n.text from candidacy cy
               join name n on n.entity_id = cy.person_id and n.is_preferred and n.lang = 'en'
               join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest' and x.value ~ '^[0-9-]+:TX:'
               join entity en on en.id = cy.person_id and en.retired_by is null"""
        ).fetchall():
            pool[slug(text)].add(person)
        for slugged in UNMATCHED:
            pool.pop(slugged, None)
        pool["jim-wright"] = {
            person
            for (person,) in conn.execute(
                """select cy.person_id from external_id x join contest_option o on o.contest_id = x.entity_id
                   join candidacy cy on cy.option_id = o.id
                   where x.scheme = 'us.contest' and x.value = '2020-11-03:TX:railroad-commissioner:place-1'
                     and o.ballot_label = 'Wright'"""
            ).fetchall()
        }
        spelled: dict[tuple[str, str], list[str]] = defaultdict(list)
        with raw.cached("us.oe_primaries_2026", OE).path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                party = {"REP": "republican", "DEM": "democratic"}.get(row["party"])
                if party and row["candidate"] and letters(row["candidate"]) not in spelled[(party, "")]:
                    spelled[(party, "")].append(letters(row["candidate"]))
                    spelled[(party, letters(row["candidate"]))].append(row["candidate"])

        sources = {}
        for (day, party), (url, _) in FILES.items():
            runoff = day == "2026-05-26"
            text = (
                f"2026 {party.title()} primary{' runoff' if runoff else ''} election: county by county canvass report"
            )
            sources[(day, party)] = cs.source("Texas Secretary of State", text)
            if sources[(day, party)] in cs.minted:
                cs.add(
                    "source",
                    id=sources[(day, party)],
                    kind="official_body",
                    publisher="Texas Secretary of State",
                    title=text,
                    url=url.split("#")[0],
                    license=None,
                )
            cs.artifact(raw.cached(SOURCE, url), sources[(day, party)])
        elections = {
            "2026-03-03": cs.key("us.election", "2026-03-03:TX:primary"),
            "2026-05-26": cs.key("us.election", "2026-05-26:TX:runoff"),
        }
        assert not set(elections.values()) & cs.minted
        two_round = cs.key("us.electoral_system", "two_round")
        parties = {party: cs.key("us.party", f"{party}-party") for party in ("republican", "democratic")}
        made: set[object] = set()
        persons: dict[str, object] = {}
        named: dict[str, list[tuple[str, object]]] = defaultdict(list)
        counts: dict[str, int] = defaultdict(int)

        def office_of(key: str, polity: object, text: str) -> object:
            office = cs.key("us.office", key)
            if office not in made:
                made.add(office)
                assert text or office not in cs.minted, key
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    name(office, text)
            return office

        def district_of(base: str, label: str) -> tuple[object, object]:
            polity = cs.key("us.polity", f"judicial:{base}")
            area = cs.area("us.judicial_district", base)
            if polity not in made:
                made.add(polity)
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
                    name(polity, f"Texas {label}")
                    cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
                    cs.add("slug", path=f"us/tx/{slug(label)}", entity_id=polity)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="us.judicial_district")
                    cs.add("area_code", area_id=area, code=base, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=state_area)
                    name(area, f"{label}, TX")
                    cs.add("polity_territory", polity_id=polity, area_id=area)
            return polity, area

        board = cs.key("us.body", "TX:state-board-of-education")
        if board in cs.minted:
            cs.add("body", id=board, polity_id=state_polity, kind="council")
            name(board, "Texas State Board of Education")
        for day in ("2026-03-03", "2026-05-26"):
            runoff = day == "2026-05-26"
            for (party, heading), rows in sorted(
                frame.filter(pl.col("day") == day).group_by("party", "contest", maintain_order=True),
                key=lambda entry: entry[0],
            ):
                unexpired = heading.endswith(" - UNEXPIRED TERM")
                heading = heading.removesuffix(" - UNEXPIRED TERM")
                frame_row = None
                if found := re.fullmatch(r"U\. S\. (?:SENATOR|REPRESENTATIVE DISTRICT (\d+))", heading):
                    if not runoff:
                        continue
                    tail = f"house:{int(found[1])}" if found[1] else "senate"
                    frame_row = federal[f"2026-03-03:TX:{tail}:{party}"]
                    label = f"Texas's {ordinal(int(found[1]))} congressional district" if found[1] else "Texas"
                elif heading in STATEWIDE:
                    tail, label = STATEWIDE[heading]
                    office, area = office_of(f"{tail}:TX", state_polity, ""), state_area
                    label = f"Texas {label}"
                elif found := re.fullmatch(r"(CHIEF )?JUSTICE, SUPREME COURT(?:, PLACE (\d+))?", heading):
                    tail = "chief-justice-of-the-supreme-court" if found[1] else "justice-of-the-supreme-court"
                    office, area = office_of(f"{tail}:TX", state_polity, ""), state_area
                    label = ("Chief Justice" if found[1] else "Justice") + " of the Supreme Court of Texas"
                    if found[2]:
                        tail, label = f"{tail}:place-{found[2]}", f"{label}, Place {found[2]}"
                elif found := re.fullmatch(r"JUDGE, COURT OF CRIMINAL APPEALS, PLACE (\d+)", heading):
                    tail = "judge-of-the-court-of-criminal-appeals"
                    office, area = office_of(f"{tail}:TX", state_polity, ""), state_area
                    tail, label = (
                        f"{tail}:place-{found[1]}",
                        f"Judge of the Texas Court of Criminal Appeals, Place {found[1]}",
                    )
                elif found := re.fullmatch(r"MEMBER, STATE BOARD OF EDUCATION, DISTRICT (\d+)", heading):
                    tail, label = (
                        f"state-board-of-education:{found[1]}",
                        f"Texas State Board of Education, District {found[1]}",
                    )
                    office, area = (
                        None,
                        cs.area("us.local_district", f"48:state-board-of-education:district-{found[1]}"),
                    )
                    if area not in made:
                        made.add(area)
                        if area in cs.minted:
                            cs.add("area", id=area, scheme_code="us.local_district")
                            cs.add(
                                "area_code",
                                area_id=area,
                                code=f"48:state-board-of-education:district-{found[1]}",
                                valid_from=None,
                            )
                            cs.add("area_parent", area_id=area, parent_id=state_area)
                            name(area, label)
                elif found := re.fullmatch(
                    r"(CHIEF )?JUSTICE, (\d+)(?:ST|ND|RD|TH) COURT OF APPEALS DISTRICT(?:, PLACE (\d+))?", heading
                ):
                    number = int(found[2])
                    court = f"{ordinal(number)} Court of Appeals District"
                    polity, area = district_of(f"TX:coa-{number}", court)
                    title_text = "Chief Justice" if found[1] else "Justice"
                    office = office_of(
                        f"{slug(title_text)}-of-the-court-of-appeals:TX:coa-{number}", polity, f"{title_text}, {court}"
                    )
                    tail = f"{slug(title_text)}-of-the-court-of-appeals:coa-{number}" + (
                        f":place-{found[3]}" if found[3] else ""
                    )
                    label = f"{title_text}, {court}" + (f", Place {found[3]}" if found[3] else "")
                elif found := re.fullmatch(
                    r"DISTRICT (JUDGE|ATTORNEY), (?:(\d+)(?:ST|ND|RD|TH) JUDICIAL DISTRICT|JUDICIAL DISTRICT (\w+))",
                    heading,
                ):
                    code = found[2] or found[3].lower()
                    court = (
                        f"{ordinal(int(found[2]))} Judicial District" if found[2] else f"Judicial District {found[3]}"
                    )
                    polity, area = district_of(f"TX:district-{code}", court)
                    title_text = f"District {found[1].title()}"
                    office = office_of(f"{slug(title_text)}:TX:district-{code}", polity, f"{title_text}, {court}")
                    tail, label = f"{slug(title_text)}:district-{code}", f"{title_text}, {court}"
                elif found := re.fullmatch(r"(\d+)(?:ST|ND|RD|TH) MULTICOUNTY COURT AT LAW", heading):
                    court = f"{ordinal(int(found[1]))} Multicounty Court at Law"
                    polity, area = district_of(f"TX:multicounty-court-at-law-{found[1]}", court)
                    office = office_of(f"judge:TX:multicounty-court-at-law-{found[1]}", polity, f"Judge, {court}")
                    tail, label = f"judge:multicounty-court-at-law-{found[1]}", f"Judge, {court}"
                elif found := re.fullmatch(
                    r"CRIMINAL DISTRICT (ATTORNEY|JUDGE),? (?:#(\d+) )?(.+?) COUNTY(?: NUMBER (\d+))?", heading
                ):
                    fips, area, county = county_areas[plain(found[3])]
                    polity = cs.key("us.polity", f"county:{fips}")
                    assert polity not in cs.minted
                    number = found[2] or found[4]
                    if found[1] == "ATTORNEY":
                        office = office_of(
                            f"TX:{fips}:criminal-district-attorney",
                            polity,
                            f"{county} County Criminal District Attorney",
                        )
                        tail, label = (
                            f"{fips}:criminal-district-attorney",
                            f"{county} County Criminal District Attorney",
                        )
                    else:
                        office = office_of(
                            f"TX:{fips}:criminal-district-court",
                            polity,
                            f"Judge, Criminal District Court, {county} County",
                        )
                        tail = f"{fips}:criminal-district-court" + (f":court-{number}" if number else "")
                        label = f"Judge, {county} County Criminal District Court" + (f" No. {number}" if number else "")
                else:
                    continue
                if unexpired:
                    tail, label = f"{tail}:remainder", f"{label}, unexpired term"
                key = f"{day}:TX:{tail}:{party}"
                first = f"2026-03-03:TX:{tail}:{party}"
                if key in held:
                    counts["held"] += 1
                    continue
                round_word = "primary runoff" if runoff else "primary"
                contest = cs.key("us.contest", key)
                if frame_row is not None:
                    kind, office, body, area, seat, plan, magnitude, term, _, result_kind = frame_row
                    chamber = "U.S. House" if tail.startswith("house") else "U.S. Senate"
                    title_text = f"2026 {chamber} {party.title()} primary runoff in {label}"
                    path = f"us/tx/{day}-runoff/{tail.replace(':', '-')}-{party}"
                else:
                    board_seat = tail.startswith("state-board")
                    kind, body, seat, plan, magnitude, result_kind = (
                        "body_seats" if board_seat else "office",
                        board if board_seat else None,
                        None,
                        None,
                        1,
                        "counted",
                    )
                    term = "remainder" if unexpired else "full"
                    title_text = f"2026 {party.title()} {round_word} for {label}"
                    path = f"us/tx/{day}-{'runoff' if runoff else 'primary'}/{slug(label)}-{party}"
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[day],
                    kind=kind,
                    office_id=office,
                    body_id=body,
                    district_area_id=area,
                    seat_id=seat,
                    boundary_set_id=plan,
                    magnitude=magnitude,
                    party_scope_id=parties[party],
                    term=term,
                    round_number=2 if runoff else 1,
                    previous_round_id=cs.key("us.contest", first) if runoff else None,
                    electoral_system_id=two_round,
                    default_result_kind=result_kind,
                )
                name(contest, title_text)
                cs.add("slug", path=path, entity_id=contest)
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="methodology",
                    body_md=f"Results by county from the Texas Secretary of State's county-by-county canvass report of the 2026 {party.title()} {round_word}. "
                    + (
                        "The report wraps long names across lines, so each candidate is named as in the March 3 primary."
                        if runoff
                        else "The report wraps long names across lines, so names are spelled as in OpenElections' transcription of the same report."
                    ),
                )
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[day],
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[(day, party)],
                    is_default=True,
                    completeness=1,
                )
                standing = []
                for (header, incumbent), by_county in rows.group_by("candidate", "incumbent", maintain_order=True):
                    if runoff:
                        known = finalists[first] or named[first]
                        match = next((entry for entry in known if letters(entry[0]) == letters(header)), None) or next(
                            (entry for entry in known if letters(entry[0].split()[-1]) in letters(header)), None
                        )
                        if match is None:
                            print("no finalist", key, header, [entry[0] for entry in known])
                            counts["unmatched"] += 1
                            continue
                        text, person = match
                    else:
                        oe = spelled.get((party, letters(header)))
                        text = title(oe[0]) if oe else title(header)
                        if not oe:
                            print("not in OpenElections", key, header)
                            counts["unspelled"] += 1
                        if slug(text) not in persons:
                            found_people = pool.get(slug(text), set())
                            if len(found_people) == 1:
                                persons[slug(text)] = next(iter(found_people))
                                counts["matched"] += 1
                                print("matched", key, text, persons[slug(text)])
                            else:
                                persons[slug(text)] = cs.key(f"{SOURCE}.person", f"TX:{slug(text)}")
                                if persons[slug(text)] in cs.minted:
                                    cs.add("person", id=persons[slug(text)])
                                    name(persons[slug(text)], text)
                        person = persons[slug(text)]
                        named[key].append((text, person))
                    option = cs.key("us.option", f"{key}:{slug(text)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties[party],
                        ballot_label=text,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{slug(text)}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=text,
                        member_of_party_id=parties[party],
                        is_independent=False,
                        is_incumbent=bool(incumbent),
                    )
                    for county, votes in by_county.select("county", "votes").iter_rows():
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=counties[plain(county)],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=votes,
                        )
                    standing.append((int(by_county["votes"].sum()), option))
                standing.sort(key=lambda entry: -entry[0])
                everyone = sum(votes for votes, _ in standing)
                if not runoff and len(standing) > 1 and standing[0][0] * 2 <= everyone:
                    if len(standing) > 2 and standing[1][0] == standing[2][0]:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md="A tie for the last advancing place leaves the outcome unrecorded.",
                        )
                    else:
                        for _, option in standing[:2]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                        counts["advanced"] += 1
                elif standing and (len(standing) == 1 or standing[0][0] != standing[1][0]):
                    cs.add("outcome", result_set_id=result_set, option_id=standing[0][1], kind="won", seats=None)
                counts["runoffs" if runoff else "contests"] += 1
        print(dict(counts))
        return cs.write()
