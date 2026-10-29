import csv
from pathlib import Path
from urllib.parse import urlparse

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.judicial_2026"
VERSION = "1"
ROWS = Path(__file__).with_name("judicial_2026.tsv")
PARTIES = {"Democrat": "democratic-party", "Democratic": "democratic-party", "Republican": "republican-party"}


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
            summary="Statewide supreme and appellate court races on November 3, 2026, with candidates from each state's official candidate list or sample ballot",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {}
        for url in sorted({row["source"] for row in rows}):
            host = urlparse(url).netloc.removeprefix("www.")
            sources[url] = cs.source(host, f"2026 general election judicial candidates ({url})")
            if sources[url] in cs.minted:
                cs.add(
                    "source",
                    id=sources[url],
                    kind="official_body",
                    publisher=host,
                    title=f"2026 general election judicial candidates ({url})",
                    url=url,
                    license=None,
                )
            try:
                cs.artifact(raw.cached(SOURCE, url), sources[url])
            except LookupError:
                print(f"not fetched: {url}")

        offices: set[object] = set()
        known: dict[str, dict[str, set[object]]] = {}
        contests: set[object] = set()
        counts = {"contests": 0, "candidates": 0, "matched": 0}
        for row in rows:
            state = row["state"]
            if state not in known:
                known[state] = {}
                for person, text in conn.execute(
                    """select distinct cy.person_id, n.text from candidacy cy
                       join name n on n.entity_id = cy.person_id and n.is_preferred and n.lang = 'en'
                       join contest_option o on o.id = cy.option_id
                       join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                        and x.value ~ %s""",
                    [rf":{state}:[a-z-]*(supreme|appeal)"],
                ).fetchall():
                    known[state].setdefault(slug(text), set()).add(person)
            title = row["office"]
            seat = row["seat"]
            retention = row["kind"] == "retention"
            stub = f"{slug(title)}{f':{slug(seat)}' if seat else ''}"
            key = f"2026-11-03:{state}:{stub}" + (f":{slug(row['candidate'])}" if retention else "")
            polity = cs.polity(f"US-{state}")
            area = conn.execute(
                """select u.area_id from polity p
                   join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
                   where p.iso_code = %s""",
                [f"US-{state}"],
            ).fetchone()[0]
            election = cs.key("us.election", f"2026-11-03:{state}")
            if election in cs.minted:
                raise LookupError(f"no 2026-11-03 election for {state}")
            office = cs.key("us.office", f"{slug(title)}:{state}")
            if office in cs.minted and office not in offices:
                cs.add("office", id=office, polity_id=polity, kind="other")
                name(office, f"{title}, {state}")
            offices.add(office)
            contest = cs.key("us.contest", key)
            if contest not in contests:
                contests.add(contest)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    body_id=None,
                    district_area_id=area,
                    seat_id=None,
                    boundary_set_id=None,
                    magnitude=1,
                    term="full",
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=cs.key("us.electoral_system", "retention" if retention else "fptp"),
                    default_result_kind="counted",
                )
                label = f"{title}{f', {seat}' if seat else ''}"
                name(contest, f"2026 {state} {label}{' retention election' if retention else ' election'}")
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/2026-11-03-general/{slug(label)}"
                    + (f"-{slug(row['candidate'])}" if retention else ""),
                    entity_id=contest,
                )
                cs.add("citation", entity_id=contest, source_id=sources[row["source"]], locator=None)
                counts["contests"] += 1
            text = row["candidate"]
            pool = known[state].get(slug(text), set())
            if len(pool) == 1:
                person = next(iter(pool))
                counts["matched"] += 1
            else:
                person = cs.key(f"{SOURCE}.person", f"{key}:{slug(text)}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, text)
            party = PARTIES[row["party"]] if row["party"] else None
            option = cs.key("us.option", f"{key}:{slug(text)}")
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=cs.key("us.party", party) if party else None,
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
                is_independent=False,
            )
            cs.add("citation", entity_id=candidacy, source_id=sources[row["source"]], locator=None)
            counts["candidates"] += 1
        print(counts)
        return cs.write()
