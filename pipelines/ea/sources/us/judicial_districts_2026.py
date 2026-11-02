import csv
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.judicial_districts_2026"
VERSION = "1"
ROWS = Path(__file__).with_name("judicial_districts_2026.tsv")
PUBLISHERS = {
    "www.elections.il.gov": ("Illinois State Board of Elections", "Judicial Officers Book"),
    "electionresults.iowa.gov": ("Iowa Secretary of State", "2026 General Election Results, Detail XML"),
}


def read() -> list[dict[str, str]]:
    with ROWS.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def fetch() -> None:
    for url in sorted({row["source"] for row in read()}):
        raw.fetch(SOURCE, url, suffix=Path(urlparse(url).path).suffix or ".html")


def emit() -> Path:
    rows = read()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Circuit and district judges standing for retention on November 3, 2026, from each state's official judicial roster",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for url in sorted({row["source"] for row in rows}):
            publisher, title = PUBLISHERS[urlparse(url).netloc]
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
            area, polity = conn.execute(
                """select u.area_id, p.id from polity p
                   join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
                   where p.iso_code = %s""",
                [f"US-{state}"],
            ).fetchone()
            election = cs.key("us.election", f"2026-11-03:{state}")
            if election in cs.minted:
                raise LookupError(f"no 2026-11-03 election for {state}")
            states[state] = (area, polity, election)
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%%'").fetchall()}
        retention = cs.key("us.electoral_system", "retention")

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
            taken.add(path)
            cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
            name(polity, f"{row['label']}, {row['state']}")
            cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
            cs.add("slug", path=path, entity_id=polity)
            cs.add("area", id=area, scheme_code="us.judicial_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=districts[row["parent"]] if row["parent"] else state_area,
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
        for row in rows:
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
            while path in taken:
                count += 1
                path = f"{base}-{count}"
            taken.add(path)
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
