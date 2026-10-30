import csv
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.oe_primaries_2026 import plain
from ea.store import connect

SOURCE = "us.la_judicial_2026"
VERSION = "1"
BASE = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20261103/"
LISTING = BASE + "RacesCandidates_Multiparish.htm"
PUBLISHER = "Louisiana Secretary of State"
TITLE = "Election Results Data: November 3, 2026 races and candidates"
SITE = "https://voterportal.sos.la.gov/ElectionResults/"
RACE = re.compile(
    r"(District Judge|District Attorney|Judge|Magistrate Judge|Magistrate|City Judge, City Court|City Marshal)"
    r"\s*--\s*(.+)"
)
DISTRICT = re.compile(r"(\d+)(?:st|nd|rd|th) Judicial District Court(?:, (?:ES (\d+), )?(?:Div(?:ision|\.) (\w+)))?")
PARTIES = {"DEM": "democratic-party", "REP": "republican-party", "LBT": "libertarian-party"}
TITLES = {
    "District Judge": "District Judge",
    "District Attorney": "District Attorney",
    "Judge": "Judge",
    "Magistrate Judge": "Magistrate Judge",
    "Magistrate": "Magistrate",
    "City Judge, City Court": "City Judge",
    "City Marshal": "City Marshal",
}


def fetch() -> None:
    client = httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": "electionsarchive.com"})
    listing = client.get(LISTING).content
    raw.store(SOURCE, LISTING, listing, suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")
    for race in json.loads(listing)["Races"]["Race"]:
        if RACE.fullmatch(race["SpecificTitle"].strip()):
            url = f"{BASE}csv/ByParish_{race['ID']}.csv"
            raw.store(
                SOURCE,
                url,
                client.get(url).content,
                suffix=".csv",
                retrieved_at=datetime.now(UTC),
                media_type="text/csv",
            )


def classify(title: str) -> tuple[str, str, str, str] | None:
    found = RACE.fullmatch(title.strip())
    if found is None:
        return None
    office, rest = found[1], re.sub(r"\s+", " ", found[2]).strip()
    district = DISTRICT.fullmatch(rest)
    if district:
        number, es, division = district.groups()
        seat = (f":es-{es}" if es else "") + (f":div-{slug(division)}" if division else "")
        if office == "District Attorney":
            return office, f"LA:da:{number}", f"District Attorney, {number} Judicial District", "district"
        if office == "Magistrate Judge":
            return office, f"LA:magistrate-judge:{number}", f"Magistrate Judge, {number} Judicial District", "district"
        label = f"District Judge, {number} Judicial District" + (f", Division {division}" if division else "")
        return office, f"LA:district-court:{number}{seat}", label, "district"
    if "Criminal District Court" in rest or "Civil District Court" in rest:
        court = "criminal-district-court" if "Criminal" in rest else "civil-district-court"
        seat = rest.split("Court,", 1)[1].strip() if "Court," in rest else "magistrate"
        return office, f"LA:{court}:{slug(seat)}", f"{office}, {rest}", "orleans"
    city = re.search(r"City of (\w+)$", rest)
    if city:
        detail = re.sub(r",? ?City of \w+$", "", rest).removeprefix("City Court").strip(" ,-")
        return (
            office,
            f"LA:city-court:{slug(city[1])}:{slug(office)}" + (f":{slug(detail)}" if detail else ""),
            f"{TITLES[office]}, {city[1]}" + (f" ({detail})" if detail else ""),
            city[1],
        )
    return None


def emit() -> Path:
    listing = json.loads(raw.cached(SOURCE, LISTING).path.read_bytes())["Races"]["Race"]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana district court, district attorney, magistrate, and city court races on November 3, 2026, from the Secretary of State's race and candidate list",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=SITE, license=None)
        cs.artifact(raw.cached(SOURCE, LISTING), source)
        state_polity = cs.polity("US-LA")
        state_area = conn.execute(
            """select u.area_id from polity p
               join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-LA'"""
        ).fetchone()[0]
        parish_area = {
            plain(text): area
            for area, text in conn.execute(
                """select a.id, n.text from area a join area_code ac on ac.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'short'
                   where a.scheme_code = 'us.county' and ac.code like '22%'"""
            ).fetchall()
        }
        place_area = {
            slug(text.removeprefix("2026 ").removesuffix(" mayoral election")): area
            for area, text in conn.execute(
                """select c.district_area_id, n.text from contest c join name n on n.entity_id = c.id
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value like '2026-11-03:LA:%' and n.text like '2026 % mayoral election'"""
            ).fetchall()
        }
        election = cs.key("us.election", "2026-11-03:LA")
        fptp = cs.key("us.electoral_system", "fptp")
        made: set[object] = set()
        counts = {"contests": 0, "candidates": 0, "districts": 0, "skipped": 0}
        for race in listing:
            parsed = classify(race["SpecificTitle"])
            if parsed is None:
                continue
            _, tail, heading, scope = parsed
            parishes: list[str] = []
            csv_path = raw.cached(SOURCE, f"{BASE}csv/ByParish_{race['ID']}.csv").path
            for row in list(csv.reader(io.StringIO(csv_path.read_text(encoding="utf-8-sig"))))[1:]:
                parishes.append(row[1])
            if scope == "district":
                code = tail.rsplit(":div-", 1)[0].rsplit(":es-", 1)[0]
                code = re.sub(r"^LA:(?:da|magistrate-judge|district-court):", "LA:judicial-district:", code)
                label = re.search(r"(\d+) Judicial District", heading)[0]
                polity = cs.key("us.polity", f"judicial:{code}")
                area = cs.area("us.judicial_district", code)
                if polity not in made:
                    made.add(polity)
                    if polity in cs.minted:
                        cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
                        name(polity, f"Louisiana {label}")
                        cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
                        cs.add("slug", path=f"us/la/{slug(label)}", entity_id=polity)
                        cs.add("area", id=area, scheme_code="us.judicial_district")
                        cs.add("area_code", area_id=area, code=code, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=state_area)
                        name(area, f"{label}, LA")
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
                            body_md=f"Parishes on the 2026 ballot: {', '.join(sorted(set(parishes)))}.",
                        )
                    counts["districts"] += 1
                office_polity = polity
            else:
                area = parish_area["orleans"] if scope == "orleans" else place_area.get(slug(scope))
                office_polity = state_polity
                if area is None:
                    print(f"skipped {race['SpecificTitle']}: no area for {scope}")
                    counts["skipped"] += 1
                    continue
            office = cs.key("us.office", tail)
            if office not in made:
                made.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=office_polity, kind="other")
                    name(office, heading)
            key = f"2026-11-03:{tail}"
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
                magnitude=int(race["NumberToBeElected"] or 1),
                term="full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"2026 {heading} election")
            cs.add("slug", path=f"us/la/2026-11-03-general/{slug(heading)}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            counts["contests"] += 1
            seen: set[str] = set()
            for choice in race["Choice"]:
                found = re.fullmatch(r"(.*?)\s*\((\w+)\)", choice["Desc"].strip())
                text, code = (found[1], found[2]) if found else (choice["Desc"].strip(), "")
                if slug(text) in seen:
                    continue
                seen.add(slug(text))
                person = cs.key(f"{SOURCE}.person", f"{key}:{slug(text)}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, text)
                party = PARTIES.get(code)
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
                    is_independent=code == "NOPTY",
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                counts["candidates"] += 1
        print(counts)
        return cs.write()
