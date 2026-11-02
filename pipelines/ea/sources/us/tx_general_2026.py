import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.sld_primaries_2026 import cased
from ea.sources.us.tx_primaries_2026 import ordinal
from ea.store import DATA, connect
from ea.typed import one

SOURCE = "us.tx_general_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.sos.state.tx.us/elections/forms/2026-ballot-cert.pdf"
PARTIES = {"REP": "republican-party", "DEM": "democratic-party", "LIB": "libertarian-party", "GRE": "green-party"}
COA = re.compile(r"(CHIEF )?JUSTICE, (\d+)(?:ST|ND|RD|TH) COURT OF APPEALS DISTRICT(?:, PLACE (\d+))?")
TRIAL = re.compile(r"DISTRICT (JUDGE|ATTORNEY), (?:(\d+)(?:ST|ND|RD|TH) JUDICIAL DISTRICT|JUDICIAL DISTRICT (\w+))")
MULTICOUNTY = re.compile(r"(\d+)(?:ST|ND|RD|TH) MULTICOUNTY COURT AT LAW")
BOARD = re.compile(r"MEMBER, STATE BOARD OF EDUCATION, DISTRICT (\d+)")


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        held = raw.cached("us.judicial_2026", URL)
        raw.store(
            SOURCE,
            URL,
            held.path.read_bytes(),
            suffix=".pdf",
            retrieved_at=held.retrieved_at,
            media_type="application/pdf",
        )


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    rows = []
    county = title = None
    for line in text.splitlines():
        if found := re.match(r"County\s{2,}(\S.*\S)", line):
            county, title = found[1].title(), None
        elif re.match(r"\s*(08/28/2026|Texas Secretary|Ballot Certification|2026 NOVEMBER|November 03)", line):
            continue
        elif found := re.fullmatch(r" {5}(\S.*\S)", line):
            heading = found[1].replace(" (UNEXPIRED TERM)", "")
            title = (
                found[1]
                if any(pattern.fullmatch(heading) for pattern in (COA, TRIAL, MULTICOUNTY, BOARD))
                and not heading.startswith("CHIEF JUSTICE, 15TH")
                and not heading.startswith("JUSTICE, 15TH")
                else None
            )
        elif title and (found := re.fullmatch(r" {10}(\S.*?)\s{2,}(REP|DEM|LIB|GRE|IND)", line)):
            rows.append({"title": title, "county": county, "candidate": cased(found[1].strip()), "party": found[2]})
    frame = pl.DataFrame(rows).unique(maintain_order=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, "rows", frame["title"].n_unique(), "races")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas courts of appeals, district courts, district attorneys, and State Board of Education districts on November 3, 2026, with candidates from the Secretary of State's ballot certification",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state_polity = cs.polity("US-TX")
        (state_area,) = one(
            conn.execute(
                "select area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state_polity]
            )
        )
        title_text = "2026 general election ballot certification"
        source = cs.source("Texas Secretary of State", title_text)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Texas Secretary of State",
                title=title_text,
                url=URL,
                license=None,
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        election = cs.key("us.election", "2026-11-03:TX")
        assert election not in cs.minted
        board = cs.key("us.body", "TX:state-board-of-education")
        assert board not in cs.minted
        pool: dict[str, set[object]] = {}
        for person, text in conn.execute(
            """select distinct cy.person_id, cy.ballot_name from candidacy cy
               join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                and x.value ~ '^2026-0[35]-[0-9]+:TX:'"""
        ).fetchall():
            pool.setdefault(slug(text), set()).add(person)
        made: set[object] = set()
        counts = {"contests": 0, "candidates": 0, "matched": 0}

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

        def office_of(key: str, polity: object, text: str) -> object:
            office = cs.key("us.office", key)
            if office not in made:
                made.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    name(office, text)
            return office

        for heading, rows in frame.group_by("title", maintain_order=True):
            heading = heading[0]
            unexpired = heading.endswith(" (UNEXPIRED TERM)")
            heading = heading.removesuffix(" (UNEXPIRED TERM)")
            office = body = None
            if found := BOARD.fullmatch(heading):
                body = board
                tail, label = (
                    f"state-board-of-education:{found[1]}",
                    f"Texas State Board of Education, District {found[1]}",
                )
                code = f"48:state-board-of-education:district-{found[1]}"
                area = cs.area("us.local_district", code)
                if area in cs.minted and area not in made:
                    made.add(area)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=state_area)
                    name(area, label)
            elif found := COA.fullmatch(heading):
                number = int(found[2])
                court = f"{ordinal(number)} Court of Appeals District"
                polity, area = district_of(f"TX:coa-{number}", court)
                role = "Chief Justice" if found[1] else "Justice"
                office = office_of(f"{slug(role)}-of-the-court-of-appeals:TX:coa-{number}", polity, f"{role}, {court}")
                tail = f"{slug(role)}-of-the-court-of-appeals:coa-{number}" + (f":place-{found[3]}" if found[3] else "")
                label = f"{role}, {court}" + (f", Place {found[3]}" if found[3] else "")
            elif found := TRIAL.fullmatch(heading):
                code = found[2] or found[3].lower()
                court = f"{ordinal(int(found[2]))} Judicial District" if found[2] else f"Judicial District {found[3]}"
                polity, area = district_of(f"TX:district-{code}", court)
                role = f"District {found[1].title()}"
                office = office_of(f"{slug(role)}:TX:district-{code}", polity, f"{role}, {court}")
                tail, label = f"{slug(role)}:district-{code}", f"{role}, {court}"
            elif found := MULTICOUNTY.fullmatch(heading):
                court = f"{ordinal(int(found[1]))} Multicounty Court at Law"
                polity, area = district_of(f"TX:multicounty-court-at-law-{found[1]}", court)
                office = office_of(f"judge:TX:multicounty-court-at-law-{found[1]}", polity, f"Judge, {court}")
                tail, label = f"judge:multicounty-court-at-law-{found[1]}", f"Judge, {court}"
            else:
                raise ValueError(heading)
            if unexpired:
                tail, label = f"{tail}:remainder", f"{label}, unexpired term"
            key = f"2026-11-03:TX:{tail}"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if body else "office",
                office_id=office,
                body_id=body,
                district_area_id=area,
                seat_id=None,
                boundary_set_id=None,
                magnitude=1,
                term="remainder" if unexpired else "full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=cs.key("us.electoral_system", "fptp"),
                default_result_kind="counted",
            )
            name(contest, f"2026 {label if body else f'Texas {label}'} election")
            cs.add("slug", path=f"us/tx/2026-11-03-general/{slug(label)}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            counts["contests"] += 1
            for text, party in rows.select("candidate", "party").unique(maintain_order=True).iter_rows():
                found_people = pool.get(slug(text), set())
                if len(found_people) == 1:
                    person = next(iter(found_people))
                    counts["matched"] += 1
                else:
                    person = cs.key(f"{SOURCE}.person", f"TX:{slug(text)}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, text)
                option = cs.key("us.option", f"{key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", PARTIES[party]) if party in PARTIES else None,
                    ballot_label=text,
                )
                candidacy = cs.key("us.candidacy", f"{key}:{slug(text)}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=party == "IND",
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                counts["candidates"] += 1
        print(counts)
        return cs.write()
