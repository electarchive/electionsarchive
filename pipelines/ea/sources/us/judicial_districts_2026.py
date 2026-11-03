import csv
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect
from ea.typed import one

SOURCE = "us.judicial_districts_2026"
VERSION = "1"
ROWS = Path(__file__).with_name("judicial_districts_2026.tsv")
PUBLISHERS = {
    "www.elections.il.gov": ("Illinois State Board of Elections", "Judicial Officers Book"),
    "electionresults.iowa.gov": ("Iowa Secretary of State", "2026 General Election Results, Detail XML"),
    "judicialperformance.colorado.gov": (
        "Colorado Office of Judicial Performance Evaluation",
        "2026 Judicial Performance Evaluations, Full List",
    ),
    "sos.nebraska.gov": ("Nebraska Secretary of State", "List of Judges Who Filed for Retention, November 3, 2026"),
    "vote.utah.gov": ("Utah Elections Office", "2026 Candidate Filings"),
    "www.nvsos.gov": ("Nevada Secretary of State", "2026 General Election: Statewide Certified List of Candidates"),
    "www.sos.mo.gov": ("Missouri Secretary of State", "2026 General Election Certification of Candidates"),
    "web.sos.ky.gov": ("Kentucky Secretary of State", "Candidate Filings, 2026 General Election"),
    "sos.ks.gov": ("Kansas Secretary of State", "2026 General Election Candidate List"),
    "knowyourjudges.utah.gov": (
        "Utah Judicial Performance Evaluation Commission",
        "Judges Standing for Retention in 2026",
    ),
}
PAGES = {
    "13": "Commonwealth's Attorney",
    "15": "Judge of the Court of Appeals",
    "16": "Circuit Judge",
    "17": "District Judge",
    "20": "Circuit Judge, Family Court",
}
TITLES = {
    "https://www.elections.il.gov/ElectionOperations/EOPDFViewer.aspx?ElectionID=sejIrI%2BQmww%3D&QueryType=xF443FTCAJbIL3atac%2FUjEg7Y4yklgT1&Status=P2wRQXkiFoo%3D": "Website Candidate List, General Election, November 3, 2026",
}
BORROWED = {"www.nvsos.gov": "us.nv_districts_2026"}


def read() -> list[dict[str, str]]:
    with ROWS.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def fetch() -> None:
    for url in sorted({row["source"] for row in read()}):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            if (host := urlparse(url).netloc) in BORROWED:
                held = raw.cached(BORROWED[host], url)
                raw.store(
                    SOURCE,
                    url,
                    held.path.read_bytes(),
                    suffix=held.path.suffix,
                    retrieved_at=held.retrieved_at,
                    media_type=held.media_type or "application/pdf",
                )
            else:
                raw.fetch(SOURCE, url, suffix=Path(urlparse(url).path).suffix or ".html")


def emit() -> Path:
    rows = read()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Circuit and district judges standing for retention or election on November 3, 2026, from each state's official judicial roster",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for url in sorted({row["source"] for row in rows}):
            publisher, title = PUBLISHERS[urlparse(url).netloc]
            title = TITLES.get(url, title)
            if page := PAGES.get(url.rpartition("id=")[2]):
                title = f"{title}: {page}"
            sources[url] = cs.source(publisher, title)
            if sources[url] in cs.minted:
                cs.add(
                    "source",
                    id=sources[url],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license=None,
                )
            try:
                cs.artifact(raw.cached(SOURCE, url), sources[url])
            except LookupError:
                print(f"not fetched: {url}")

        states: dict[str, tuple[UUID, UUID, UUID]] = {}
        for state in sorted({row["state"] for row in rows}):
            area, polity = one(
                conn.execute(
                    """select u.area_id, p.id from polity p
                   join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
                   where p.iso_code = %s""",
                    [f"US-{state}"],
                )
            )
            election = cs.key("us.election", f"2026-11-03:{state}")
            if election in cs.minted:
                raise LookupError(f"no 2026-11-03 election for {state}")
            states[state] = (area, polity, election)
        taken = dict(conn.execute("select path, entity_id from slug where path like 'us/%%'").fetchall())
        retention = cs.key("us.electoral_system", "retention")
        fptp = cs.key("us.electoral_system", "fptp")

        districts: dict[str, UUID] = {}
        for row in rows:
            code = row["district"]
            if code in districts:
                continue
            state_area, state_polity, _ = states[row["state"]]
            area = cs.area("us.judicial_district", code)
            polity = cs.key("us.polity", f"judicial:{code}")
            districts[code] = area
            if polity not in cs.minted:
                continue
            path = f"us/{row['state'].lower()}/{slug(row['label'])}"
            if path in taken:
                raise ValueError(f"{row['label']} would take {path}")
            taken[path] = polity
            cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
            name(polity, f"{row['label']}, {row['state']}")
            cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
            cs.add("slug", path=path, entity_id=polity)
            cs.add("area", id=area, scheme_code="us.judicial_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=districts.get(row["parent"], state_area),
            )
            name(area, f"{row['label']}, {row['state']}")
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

        offices: dict[tuple[str, str], UUID] = {}
        contests = 0
        seats: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            if row["system"] == "fptp":
                key = f"2026-11-03:{row['state']}:{slug(row['office'])}:{slug(row['label'])}"
                seats.setdefault(key + (f":{slug(row['seat'])}" if row["seat"] else ""), []).append(row)
        for key, people in seats.items():
            row = people[0]
            state, title = row["state"], row["office"]
            _, state_polity, election = states[state]
            office = offices.get((state, title))
            if office is None:
                office = offices[(state, title)] = cs.key("us.office", f"{slug(title)}:{state}")
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=state_polity, kind="other")
                    name(office, f"{title}, {state}")
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=districts[row["district"]],
                seat_id=None,
                boundary_set_id=None,
                magnitude=1,
                term="full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            label = ", ".join(part for part in (title, row["label"], row["seat"]) if part)
            name(contest, f"2026 {state} {label} election")
            path = f"us/{state.lower()}/2026-11-03-general/{slug(label)}"
            if taken.get(path, contest) != contest:
                raise ValueError(f"{label} would take {path}")
            taken[path] = contest
            cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[row["source"]], locator=None)
            for each in people:
                who = f"{key}:{slug(each['candidate'])}"
                option = cs.key("us.option", who)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", each["party"]) if each["party"] else None,
                    ballot_label=each["candidate"],
                )
                person = cs.key(f"{SOURCE}.person", who)
                if person in cs.minted:
                    cs.add("person", id=person)
                name(person, each["candidate"])
                candidacy = cs.key("us.candidacy", who)
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=each["candidate"],
                    is_independent=False,
                )
                cs.add("citation", entity_id=candidacy, source_id=sources[each["source"]], locator=None)
            contests += 1
        for row in rows:
            if row["system"] != "retention":
                continue
            state = row["state"]
            _, state_polity, election = states[state]
            title = row["office"]
            office = offices.get((state, title))
            if office is None:
                office = offices[(state, title)] = cs.key("us.office", f"{slug(title)}-retention:{state}")
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=state_polity, kind="other")
                    name(office, f"{title} retention, {state}")
            key = f"2026-11-03:{state}:{slug(title)}:{slug(row['label'])}:{slug(row['candidate'])}"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=districts[row["district"]],
                seat_id=None,
                boundary_set_id=None,
                magnitude=1,
                term="full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=retention,
                default_result_kind="counted",
            )
            label = f"{title}, {row['label']}"
            name(contest, f"2026 {state} {label} retention: {row['candidate']}")
            base = f"us/{state.lower()}/2026-11-03-general/{slug(label)}-{slug(row['candidate'])}"
            path, count = base, 1
            while taken.get(path, contest) != contest:
                count += 1
                path = f"{base}-{count}"
            taken[path] = contest
            cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[row["source"]], locator=None)
            yes = cs.key("us.option", f"{key}:yes")
            cs.add("contest_option", id=yes, contest_id=contest, kind="answer", party_id=None, ballot_label="Yes")
            cs.add(
                "contest_option",
                id=cs.key("us.option", f"{key}:no"),
                contest_id=contest,
                kind="answer",
                party_id=None,
                ballot_label="No",
            )
            person = cs.key(f"{SOURCE}.person", key)
            if person in cs.minted:
                cs.add("person", id=person)
            name(person, row["candidate"])
            candidacy = cs.key("us.candidacy", key)
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=yes,
                role="head",
                ballot_name=row["candidate"],
                is_independent=False,
            )
            cs.add("citation", entity_id=candidacy, source_id=sources[row["source"]], locator=None)
            contests += 1
        print(contests, "contests,", len(districts), "districts")
        return cs.write()
