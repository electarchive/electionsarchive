import csv
import io
import re
from collections import defaultdict
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.nc_trial_2026"
VERSION = "1"
URL = "https://s3.amazonaws.com/dl.ncsbe.gov/Elections/2026/Candidate%20Filing/Candidate_Listing_2026.csv"
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "2026 Candidate Listing"
KINDS = {
    "DISTRICT ATTORNEY": ("da", "District Attorney", "Prosecutorial District"),
    "NC DISTRICT COURT JUDGE": ("district-court", "District Court Judge", "District Court District"),
    "NC SUPERIOR COURT JUDGE": ("superior-court", "Superior Court Judge", "Superior Court District"),
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "GRE": "green-party",
    "CST": "constitution-party",
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def emit() -> Path:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with raw.cached(SOURCE, URL).path.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(io.StringIO(handle.read())):
            contest = re.sub(r"\s+", " ", row["contest_name"]).strip()
            if row["election_dt"] == "11/03/2026" and re.match(
                r"(DISTRICT ATTORNEY|NC DISTRICT COURT JUDGE|NC SUPERIOR COURT JUDGE) DISTRICT", contest
            ):
                groups[contest].append(row)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina district attorney, district court, and superior court races on November 3, 2026, from the State Board of Elections candidate listing",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license=None)
        cs.artifact(raw.cached(SOURCE, URL), source)
        state_polity = cs.polity("US-NC")
        state_area = conn.execute(
            """select u.area_id from polity p
               join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-NC'"""
        ).fetchone()[0]
        election = cs.key("us.election", "2026-11-03:NC")
        fptp = cs.key("us.electoral_system", "fptp")
        made: set[object] = set()
        counts = {"contests": 0, "candidates": 0, "districts": 0}
        for contest_name, rows in groups.items():
            prefix = next(prefix for prefix in KINDS if contest_name.startswith(prefix))
            kind, title, district_title = KINDS[prefix]
            number = re.search(r"DISTRICT (\w+?)(?: SEAT (\d+))?(?: \(UNEXPIRED\))?$", contest_name)
            district, seat = number[1], number[2]
            unexpired = "(UNEXPIRED)" in contest_name
            code = f"NC:{kind}:{district.lower()}"
            label = f"{district_title} {district}"
            polity = cs.key("us.polity", f"judicial:{code}")
            area = cs.area("us.judicial_district", code)
            if polity not in made:
                made.add(polity)
                counties = sorted({row["county_name"].title() for row in rows})
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
                    name(polity, f"North Carolina {label}")
                    cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
                    cs.add("slug", path=f"us/nc/{slug(label)}", entity_id=polity)
                    cs.add("area", id=area, scheme_code="us.judicial_district")
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=state_area)
                    name(area, f"{label}, NC")
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
                    cs.add(
                        "note",
                        entity_id=polity,
                        kind="context",
                        body_md=f"Counties on the 2026 ballot: {', '.join(counties)}.",
                    )
                counts["districts"] += 1
            office = cs.key("us.office", f"{kind}:{code}")
            if office not in made:
                made.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    name(office, f"{title}, {label}")
            key = f"2026-11-03:{code}" + (f":seat-{seat}" if seat else "") + (":unexpired" if unexpired else "")
            contest = cs.key("us.contest", key)
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
                magnitude=int(rows[0]["vote_for"] or 1),
                term="remainder" if unexpired else "full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            heading = f"{title}, {label}" + (f", Seat {int(seat)}" if seat else "") + (" (Unexpired Term)" if unexpired else "")
            name(contest, f"2026 {heading} election")
            cs.add(
                "slug",
                path=f"us/nc/2026-11-03-general/{slug(heading)}",
                entity_id=contest,
            )
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            counts["contests"] += 1
            seen: set[str] = set()
            for row in rows:
                text = row["name_on_ballot"].strip()
                if slug(text) in seen:
                    continue
                seen.add(slug(text))
                person = cs.key(f"{SOURCE}.person", f"{key}:{slug(text)}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, text)
                partisan = row["is_partisan"] == "TRUE"
                party = PARTIES.get(row["party_candidate"]) if partisan else None
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
                    is_independent=partisan and row["party_candidate"] in ("UNA", "NON", ""),
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                counts["candidates"] += 1
        print(counts)
        return cs.write()
