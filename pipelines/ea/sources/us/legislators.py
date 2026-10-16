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
                if seat in cs.minted:
                    raise ValueError(f"{ids['bioguide']}: no seat for {term}")
                window = (start - timedelta(days=800), start + timedelta(days=60))
                contest = max(
                    ((day, found) for day, found in won.get((person, chamber), []) if window[0] <= day <= window[1]),
                    default=(None, None),
                )[1]
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
                    if label is None or label in UNAFFILIATED:
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
                    party = cs.key("us.party", key)
                    if party in cs.minted and party not in created:
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
