import json
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.clerk import LEGISLATORS
from ea.store import connect

SOURCE = "us.legislators"
VERSION = "1"
PUBLISHER = "The @unitedstates project"
TITLE = "congress-legislators: members of Congress, 1789 to the present"
LANDING = "https://github.com/unitedstates/congress-legislators"
LABELS = {
    "Jackson": "Jacksonian",
    "Jackson Republican": "Jacksonian",
    "Jacksonian Republican": "Jacksonian",
    "Adams": "Anti-Jacksonian",
    "Anti Jacksonian": "Anti-Jacksonian",
    "Anti Jackson": "Anti-Jacksonian",
    "Adams Democrat": "Anti-Jacksonian",
    "Anti-administration": "Anti-Administration",
    "Pro-administration": "Pro-Administration",
    "Ind. Democrat": "Independent Democrat",
    "Ind. Republican": "Independent Republican",
    "Ind. Whig": "Independent Whig",
}
NATIONAL = {
    "Democrat": "democratic-party",
    "Whig": "whig-party",
    "Federalist": "federalist-party",
    "Democratic Republican": "democratic-republican-party",
    "American": "know-nothing-party",
    "Anti Masonic": "anti-masonic-party",
    "Anti Mason": "anti-masonic-party",
    "Nullifier": "nullifier-party",
    "Free Soil": "free-soil-party",
    "Liberty": "liberty-party",
    "Populist": "peoples-party",
    "National Greenbacker": "greenback-party",
    "Liberal Republican": "liberal-republican-party",
    "Constitutional Unionist": "constitutional-union-party",
    "Prohibitionist": "prohibition-party",
    "Libertarian": "libertarian-party",
    "Socialist": "socialist-party-of-america",
}
NAMES = {
    "free-soil-party": "Free Soil Party",
    "liberty-party": "Liberty Party",
    "greenback-party": "Greenback Party",
    "socialist-party-of-america": "Socialist Party of America",
}
UNAFFILIATED = {"Independent", "Nonpartisan", "Unknown", "AL", "Ind. Republican-Democrat"}
ADMITTED = {
    "VT": date(1791, 3, 4),
    "KY": date(1792, 6, 1),
    "TN": date(1796, 6, 1),
    "OH": date(1803, 3, 1),
    "LA": date(1812, 4, 30),
    "IN": date(1816, 12, 11),
    "MS": date(1817, 12, 10),
    "IL": date(1818, 12, 3),
    "AL": date(1819, 12, 14),
    "ME": date(1820, 3, 15),
    "MO": date(1821, 8, 10),
    "AR": date(1836, 6, 15),
    "MI": date(1837, 1, 26),
    "FL": date(1845, 3, 3),
    "TX": date(1845, 12, 29),
    "IA": date(1846, 12, 28),
    "WI": date(1848, 5, 29),
    "CA": date(1850, 9, 9),
    "MN": date(1858, 5, 11),
    "OR": date(1859, 2, 14),
    "KS": date(1861, 1, 29),
    "WV": date(1863, 6, 20),
    "NV": date(1864, 10, 31),
    "NE": date(1867, 3, 1),
    "CO": date(1876, 8, 1),
    "ND": date(1889, 11, 2),
    "SD": date(1889, 11, 2),
    "MT": date(1889, 11, 8),
    "WA": date(1889, 11, 11),
    "ID": date(1890, 7, 3),
    "WY": date(1890, 7, 10),
    "UT": date(1896, 1, 4),
    "OK": date(1907, 11, 16),
    "NM": date(1912, 1, 6),
    "AZ": date(1912, 2, 14),
    "AK": date(1959, 1, 3),
    "HI": date(1959, 8, 21),
    "DK": date(1889, 11, 2),
    "OL": date(1812, 4, 30),
    "PI": date(1946, 7, 4),
}
TERRITORIES = {
    "OH": "Delegate from the Northwest Territory",
    "TN": "Delegate from the Southwest Territory",
    "MS": "Delegate from the Mississippi Territory",
    "IN": "Delegate from the Indiana Territory",
    "OL": "Delegate from the Territory of Orleans",
    "MI": "Delegate from the Michigan Territory",
    "IL": "Delegate from the Illinois Territory",
    "AL": "Delegate from the Alabama Territory",
    "MO": "Delegate from the Missouri Territory",
    "AR": "Delegate from the Arkansas Territory",
    "FL": "Delegate from the Florida Territory",
    "WI": "Delegate from the Wisconsin Territory",
    "IA": "Delegate from the Iowa Territory",
    "MN": "Delegate from the Minnesota Territory",
    "OR": "Delegate from the Oregon Territory",
    "KS": "Delegate from the Kansas Territory",
    "NE": "Delegate from the Nebraska Territory",
    "NM": "Delegate from the New Mexico Territory",
    "UT": "Delegate from the Utah Territory",
    "WA": "Delegate from the Washington Territory",
    "CO": "Delegate from the Colorado Territory",
    "NV": "Delegate from the Nevada Territory",
    "DK": "Delegate from the Dakota Territory",
    "ID": "Delegate from the Idaho Territory",
    "MT": "Delegate from the Montana Territory",
    "AZ": "Delegate from the Arizona Territory",
    "WY": "Delegate from the Wyoming Territory",
    "OK": "Delegate from the Oklahoma Territory",
    "AK": "Delegate from the Alaska Territory",
    "HI": "Delegate from the Hawaii Territory",
    "PI": "Resident Commissioner of the Philippines",
}


def emit() -> Path:
    members = [
        person
        for kind in ("historical", "current")
        for person in json.loads(raw.cached("us.clerk", LEGISLATORS.format(kind=kind)).path.read_text())
    ]
    today = date.today()
    with connect() as conn:
        known: dict[str, dict[str, UUID]] = {}
        for scheme, value, entity in conn.execute(
            "select scheme, value, entity_id from external_id where scheme in ('fec.candidate', 'wikidata', 'bioguide')"
        ).fetchall():
            known.setdefault(scheme, {})[value] = entity
        identified = {(scheme, entity) for scheme, values in known.items() for entity in values.values()}
        won: dict[tuple[UUID, str], list[tuple[date, UUID]]] = {}
        for person, body, day, contest in conn.execute(
            """select distinct c.person_id, b.value, e.date_from, ct.id
               from outcome o
               join result_set r on r.id = o.result_set_id and r.is_default
               join contest ct on ct.id = r.contest_id
               join election e on e.id = ct.election_id
               join external_id b on b.entity_id = ct.body_id and b.scheme = 'us.body'
               join candidacy c on c.option_id = o.option_id and c.role = 'head'
               where o.kind = 'elected'"""
        ).fetchall():
            won.setdefault((person, body), []).append((day, contest))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House and Senate tenures, 1789 to the present, from congress-legislators",
            polity="US",
        )
        us = cs.polity("US")
        bodies = {kind: cs.key("us.body", kind) for kind in ("house", "senate")}
        if cs.minted:
            raise ValueError("the House and the Senate must already exist")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for kind in ("historical", "current"):
            cs.artifact(raw.cached("us.clerk", LEGISLATORS.format(kind=kind)), source)

        def named(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        created: set[UUID] = set()
        seated: set[UUID] = set()
        areas: dict[str, UUID] = {
            iso.removeprefix("US-"): area
            for iso, area in conn.execute(
                """select p.iso_code, u.area_id from reporting_unit u
                   join polity p on p.id = u.polity_id
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        districts: dict[tuple[str, str], UUID] = {
            (path.split("/")[1].upper(), path.rsplit("-", 1)[1]): area
            for path, area in conn.execute(
                """select s.path, c.district_area_id from contest c join slug s on s.entity_id = c.id
                   where s.path like 'us/%%/2024-11-05-general/house-%%' and c.district_area_id is not null"""
            ).fetchall()
        }
        for member in members:
            ids = member["id"]
            person = next(
                (known["fec.candidate"][fec] for fec in ids.get("fec", []) if fec in known["fec.candidate"]), None
            )
            person = (
                person
                or known.get("wikidata", {}).get(ids.get("wikidata", ""))
                or known.get("bioguide", {}).get(ids["bioguide"])
            )
            if person is None:
                person = (
                    cs.key("wikidata", ids["wikidata"]) if "wikidata" in ids else cs.key("bioguide", ids["bioguide"])
                )
            if person in cs.minted and person not in created:
                created.add(person)
                cs.add("person", id=person)
                named(
                    person,
                    member["name"].get("official_full")
                    or " ".join(
                        part
                        for part in (
                            member["name"]["first"],
                            member["name"].get("middle"),
                            member["name"]["last"],
                            member["name"].get("suffix"),
                        )
                        if part
                    ),
                )
            for scheme, value in (("wikidata", ids.get("wikidata")), ("bioguide", ids["bioguide"])):
                if (
                    value is not None
                    and value not in known.get(scheme, {})
                    and (scheme, person) not in identified
                    and (scheme, value) not in cs._keyed
                ):
                    cs.add("external_id", entity_id=person, scheme=scheme, value=value)
                    identified.add((scheme, person))
            for term in member["terms"]:
                chamber = "senate" if term["type"] == "sen" else "house"
                start, end = date.fromisoformat(term["start"]), date.fromisoformat(term["end"])
                seat = None
                if chamber == "senate":
                    seat = cs.key("us.seat", f"senate:{term['state']}:{term['class']}")
                elif term["state"] in ("AS", "DC", "GU", "MP", "PR", "VI"):
                    seat = cs.key("us.seat", f"house:{term['state']}")
                elif end <= ADMITTED.get(term["state"], date.min):
                    seat = cs.key("us.seat", f"house:{term['state']}:territory")
                    if seat not in seated:
                        seated.add(seat)
                        cs.add(
                            "seat",
                            id=seat,
                            body_id=bodies["house"],
                            district_area_id=areas.get(term["state"]),
                            label=TERRITORIES[term["state"]],
                            voting=False,
                            valid_from=None,
                            valid_to=ADMITTED[term["state"]],
                            valid_to_precision="day",
                        )
                if seat in cs.minted and seat not in seated:
                    raise ValueError(f"{ids['bioguide']}: no seat for {term}")
                window = (start - timedelta(days=800), start + timedelta(days=60))
                contest = max(
                    ((day, found) for day, found in won.get((person, chamber), []) if window[0] <= day <= window[1]),
                    default=(None, None),
                )[1]
                if chamber == "house" and seat is None:
                    district = term.get("district") or 0
                    if district < 0:
                        district = min(
                            (
                                (abs(date.fromisoformat(other["start"]) - start), other["district"])
                                for other in member["terms"]
                                if other["type"] == "rep"
                                and other["state"] == term["state"]
                                and (other.get("district") or 0) > 0
                            ),
                            default=(None, 0),
                        )[1]
                    seat = cs.key("us.seat", f"house:{term['state']}:{district}")
                    if seat not in seated:
                        seated.add(seat)
                        cs.add(
                            "seat",
                            id=seat,
                            body_id=bodies["house"],
                            district_area_id=districts.get((term["state"], str(district))),
                            label=f"{term['state']}-{district}" if district else f"{term['state']} at-large",
                            voting=True,
                            valid_from=None,
                            valid_to=None,
                            valid_to_precision="day",
                        )
                scheduled = end.year % 2 == 1 and (end.month, end.day) in ((3, 3), (3, 4), (1, 3))
                tenure = cs.key(f"{SOURCE}.tenure", f"{ids['bioguide']}:{chamber}:{term['start']}")
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=bodies[chamber],
                    seat_id=seat,
                    from_date=start,
                    from_precision="day",
                    to_date=None if end > today else end,
                    to_precision="day",
                    acquired_via="appointed" if term.get("how") == "appointment" else "elected",
                    contest_id=contest,
                    ended_via="term_end" if scheduled and end <= today else None,
                )
                affiliations = term.get("party_affiliations") or [
                    {"start": term["start"], "end": term["end"], "party": term.get("party")}
                ]
                for index, affiliation in enumerate(affiliations):
                    label = LABELS.get(affiliation["party"], affiliation["party"])
                    if label is None or label in UNAFFILIATED - {"Independent"}:
                        continue
                    since = date.fromisoformat(affiliation["start"])
                    if label == "Republican":
                        key = "republican-party" if since.year >= 1854 else "democratic-republican-party"
                    elif label == "Progressive":
                        key = (
                            "progressive-party-1912"
                            if since.year < 1923
                            else "progressive-party-1924"
                            if since.year < 1930
                            else f"{term['state']}:progressive-party"
                        )
                    else:
                        key = NATIONAL.get(label, f"{term['state']}:{slug(label)}")
                    party = None if label == "Independent" else cs.key("us.party", key)
                    if party is not None and party in cs.minted and party not in created:
                        created.add(party)
                        state = key.partition(":")[0] if ":" in key else None
                        cs.add("party", id=party, kind="party", polity_id=cs.polity(f"US-{state}") if state else us)
                        named(party, NAMES.get(key, label))
                    until = (
                        date.fromisoformat(affiliations[index + 1]["start"]) - timedelta(days=1)
                        if index + 1 < len(affiliations)
                        else date.fromisoformat(affiliation["end"])
                    )
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=party,
                        valid_from=since,
                        valid_from_precision="day",
                        valid_to=None if until > today else until + timedelta(days=1),
                        valid_to_precision="day",
                    )
        return cs.write()
