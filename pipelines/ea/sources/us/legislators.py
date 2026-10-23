import html
import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid5

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.us.clerk import LEGISLATORS
from ea.sources.us.secession import MONTHS as FULL
from ea.sources.us.secession import STATES
from ea.store import connect

SOURCE = "us.legislators"
VERSION = "5"
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?bioguide ?start ?startPrecision ?end ?endPrecision ?cause WHERE {
?person p:P39 ?held . ?held ps:P39 wd:Q4416090 . ?person wdt:P1157 ?bioguide .
OPTIONAL { ?held pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }
OPTIONAL { ?held pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }
OPTIONAL { ?held pq:P1534 ?cause } }""",
        "format": "json",
    }
)
CAUSES = {"Q796919": "resigned", "Q5247364": "died", "Q4": "died"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
CONGRESSES = {37: "37th", 38: "38th", 39: "39th", 40: "40th", 41: "41st"}
CONGRESS = "https://en.wikipedia.org/w/index.php?title={ordinal}_United_States_Congress&action=raw"
EXPULSIONS = "https://www.senate.gov/about/powers-procedures/expulsion/{page}_expulsion.htm"
EXPELLED = {
    "Blount": [("Blount", "TN")],
    "036CivilWarCases1": [
        ("Mason", "VA"),
        ("Hunter", "VA"),
        ("Clingman", "NC"),
        ("Bragg", "NC"),
        ("Chesnut", "SC"),
        ("Nicholson", "TN"),
        ("Sebastian", "AR"),
        ("Mitchel", "AR"),
        ("Hemphill", "TX"),
        ("Wigfall", "TX"),
    ],
    "038Breckinridge": [("Breckinridge", "KY")],
    "039CivilWarCases": [("Polk", "MO"), ("Johnson", "MO")],
    "040JesseBright": [("Bright", "IN")],
}
DIVISIONS = [
    "https://www.senate.gov/history/partydiv.htm",
    "https://www.senate.gov/senators/SenatorsWhoChangedPartiesDuringSenateService.htm",
    "https://www.senate.gov/about/parties-leadership/presidents-death-eases-senate-deadlock.htm",
]
AMENDED = {
    "M000067": [
        {"start": "1881-03-04", "end": "1885-03-03", "party": "Readjuster", "caucus": "Republican"},
        {"start": "1885-03-04", "end": "1887-03-03", "party": "Republican"},
    ],
    "S000369": [
        {"start": "1923-03-04", "end": "1935-01-02", "party": "Farmer-Labor"},
        {"start": "1935-01-03", "end": "1941-01-02", "party": "Farmer-Labor", "caucus": "Republican"},
        {"start": "1941-01-03", "end": "1947-01-03", "party": "Republican"},
    ],
    "B001026": [{"start": "1971-01-21", "end": "1977-01-03", "party": "Conservative", "caucus": "Republican"}],
}
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
RATIFIED = {"NC": date(1789, 11, 21), "RI": date(1790, 5, 29)}
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


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")
    for page in EXPELLED:
        raw.fetch(SOURCE, EXPULSIONS.format(page=page), suffix=".html")
    for ordinal in CONGRESSES.values():
        raw.fetch(SOURCE, CONGRESS.format(ordinal=ordinal), suffix=".wiki")
    for page in DIVISIONS:
        raw.fetch(SOURCE, page, suffix=".html")


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
        cs.manifest["scope"] = {"polity": "US", "replaces": {"tenure_affiliation": ["us.lineage"]}}
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
        wikidata = cs.source("Wikidata", "Positions held in the United States Senate")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Positions held in the United States Senate",
            url="https://www.wikidata.org/wiki/Q4416090",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, SERVICE), wikidata)
        held: dict[str, list[dict[str, str]]] = {}
        for binding in json.loads(raw.cached(SOURCE, SERVICE).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            held.setdefault(binding["bioguide"]["value"], []).append(
                {key: value["value"].rsplit("/", 1)[-1] for key, value in binding.items()}
            )
        senate = cs.source("United States Senate", "Expulsion cases")
        cs.add(
            "source",
            id=senate,
            kind="official_body",
            publisher="United States Senate",
            title="Expulsion cases",
            url="https://www.senate.gov/about/powers-procedures/expulsion.htm",
        )
        divisions = cs.source("United States Senate", "Party Division")
        cs.add(
            "source",
            id=divisions,
            kind="official_body",
            publisher="United States Senate",
            title="Party Division",
            url=DIVISIONS[0],
        )
        for page in DIVISIONS:
            cs.artifact(raw.cached(SOURCE, page), divisions)
        expelled: dict[tuple[str, str], date] = {}
        for page, names in EXPELLED.items():
            artifact = raw.cached(SOURCE, EXPULSIONS.format(page=page))
            cs.artifact(artifact, senate)
            text = re.sub(
                r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", artifact.path.read_text(encoding="utf-8")))
            )
            month, day, year = re.findall(r"Senate vote ?: ?([A-Z][a-z]+)\.? (\d+), (\d{4})", text)[0]
            for name in names:
                expelled[name] = date(int(year), MONTHS.index(month[:3]) + 1, int(day))
        withdrawn: dict[str, tuple[date, date | None]] = {
            iso.removeprefix("US-"): (start, end)
            for iso, start, end in conn.execute(
                """select p.iso_code, g.valid_from, g.valid_to from representation_gap g
                   join reporting_unit u on u.area_id = g.area_id and u.scheme_code = 'us.state'
                   join polity p on p.id = u.polity_id
                   join external_id b on b.entity_id = g.body_id and b.scheme = 'us.body' and b.value = 'senate'
                   where g.reason = 'secession'"""
            ).fetchall()
        }
        if not withdrawn:
            raise ValueError("run us.secession first")
        wikipedia = cs.source("Wikipedia", "Changes in membership, 37th to 41st United States Congresses")
        cs.add(
            "source",
            id=wikipedia,
            kind="community",
            publisher="Wikipedia",
            title="Changes in membership, 37th to 41st United States Congresses",
            url="https://en.wikipedia.org/wiki/37th_United_States_Congress#Changes_in_membership",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        )
        installed: dict[tuple[str, int], list[tuple[str, date]]] = {}
        for ordinal in CONGRESSES.values():
            artifact = raw.cached(SOURCE, CONGRESS.format(ordinal=ordinal))
            cs.artifact(artifact, wikipedia)
            table = artifact.path.read_text(encoding="utf-8").split("{{Ordinal US Congress change|House}}", 1)[1]
            carried: dict[int, tuple[int, str]] = {}
            for row in table.split("\n|}", 1)[0].split("\n|-")[1:]:
                cells = [cell.strip() for cell in row.strip().removeprefix("|").split("\n|")]
                columns: list[str] = []
                for column in range(5):
                    span, value = carried.get(column, (0, ""))
                    if span > 0:
                        carried[column] = (span - 1, value)
                    elif cells:
                        value = cells.pop(0)
                        spanned = re.match(r"rowspan=\"?(\d+)\"?[^|\[{]*\|\s*(.*)", value, re.DOTALL)
                        if spanned:
                            value = spanned[2]
                            carried[column] = (int(spanned[1]) - 1, value)
                        elif value.startswith(("nowrap", "style")) and " | " in value:
                            value = value.split(" | ", 1)[1]
                    columns.append(value)
                district = re.search(r"\{\{ushr\|([^|]+)\|([^|}]+)", columns[0])
                successor = re.search(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", columns[3])
                day = re.search(r"(" + "|".join(FULL) + r") (\d+), (\d{4})", columns[4])
                if district and successor and day and district[1] in STATES:
                    installed.setdefault(
                        (STATES[district[1]], 0 if district[2] == "AL" else int(district[2])), []
                    ).append((successor[1], date(int(day[3]), FULL.index(day[1]) + 1, int(day[2]))))
            for state, place, name, month, day, year in re.findall(
                r"\{\{ushr\|([^|]+)\|([^|}]+)\|E\}\}\. \[\[(?:[^|\]]*\|)?([^\]]+)\]\][^,\n]*, from ("
                + "|".join(FULL)
                + r") (\d+), (\d{4})",
                artifact.path.read_text(encoding="utf-8"),
            ):
                if state in STATES:
                    installed.setdefault((STATES[state], 0 if place == "AL" else int(place)), []).append(
                        (name, date(int(year), FULL.index(month) + 1, int(day)))
                    )
        if not installed:
            raise ValueError("no seats installed in the changes tables")
        existing = dict(
            conn.execute("select value, entity_id from external_id where scheme = %s", [f"{SOURCE}.tenure"]).fetchall()
        )
        unseated: list[UUID] = []
        states: dict[UUID, str] = {}

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
                scheduled = end.year % 2 == 1 and (end.month, end.day) in ((3, 3), (3, 4), (1, 3))
                began = ended = "day"
                cause = vote = None
                seat = None
                district = 0
                if chamber == "senate":
                    seat = cs.key("us.seat", f"senate:{term['state']}:{term['class']}")
                    states[seat] = term["state"]
                    statements = held.get(ids["bioguide"], [])
                    if (start.month, start.day) == (1, 1):
                        starts = [
                            date.fromisoformat(entry["start"][:10])
                            for entry in statements
                            if entry.get("startPrecision") == "11" and entry["start"][:4] == term["start"][:4]
                        ]
                        start, began = (min(starts), "day") if starts else (start, "year")
                    if (end.month, end.day) == (12, 31):
                        ends = sorted(
                            (date.fromisoformat(entry["end"][:10]), CAUSES.get(entry.get("cause", ""), ""))
                            for entry in statements
                            if entry.get("endPrecision") == "11"
                            and entry["end"][:4] == term["end"][:4]
                            and entry["end"][:10] > start.isoformat()
                        )
                        if ends:
                            end, cause = ends[-1][0], ends[-1][1] or None
                        else:
                            ended = "year"
                    due = date.fromisoformat(term["end"])
                    gap = withdrawn.get(term["state"])
                    if gap is not None and start < gap[0] and -60 <= (end - gap[0]).days <= 180:
                        left = [
                            date.fromisoformat(entry["end"][:10])
                            for entry in statements
                            if entry.get("endPrecision") == "11"
                            and gap[0] - timedelta(days=60) <= date.fromisoformat(entry["end"][:10]) < end
                        ]
                        if left and cause is None:
                            end = max(left)
                    else:
                        gap = None
                    vote = expelled.get((member["name"]["last"], term["state"]))
                    final = term is [other for other in member["terms"] if other["type"] == "sen"][-1]
                    if vote is not None and not start <= vote <= due + timedelta(days=365 if final else 0):
                        vote = None
                    if vote is not None:
                        end, cause = min(end, vote), "expelled"
                    elif cause is None and gap is not None and (end < due or not scheduled):
                        cause = "withdrew"
                elif term["state"] in ("AS", "DC", "GU", "MP", "PR", "VI"):
                    seat = cs.key("us.seat", f"house:{term['state']}")
                elif end <= ADMITTED.get(term["state"], date.min) or term["state"] not in areas:
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
                            apportioned_area_id=areas.get(term["state"]),
                            valid_from=None,
                            valid_to=None,
                            valid_to_precision="day",
                        )
                gap = withdrawn.get(term["state"]) if chamber == "house" else None
                if gap is not None and gap[0] <= start < (gap[1] or date.max):
                    seating = [
                        day
                        for (state, place), entries in installed.items()
                        if state == term["state"] and place in (district, 0)
                        for name, day in entries
                        if member["name"]["last"].lower() in name.lower() and start < day < end
                    ]
                    if seating:
                        start = min(seating)
                    elif (start.month, start.day) in ((3, 4), (1, 3)):
                        known_tenure = existing.get(f"{ids['bioguide']}:{chamber}:{term['start']}")
                        if known_tenure is not None:
                            unseated.append(known_tenure)
                        continue
                tenure = cs.key(f"{SOURCE}.tenure", f"{ids['bioguide']}:{chamber}:{term['start']}")
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=bodies[chamber],
                    seat_id=seat,
                    from_date=start,
                    from_precision=began,
                    to_date=None if end > today else end,
                    to_precision=ended,
                    acquired_via="appointed" if term.get("how") == "appointment" else "elected",
                    contest_id=contest,
                    ended_via=cause or ("term_end" if scheduled and end <= today else None),
                )
                if vote is not None:
                    cs.add(
                        "tenure_event",
                        id=uuid5(NAMESPACE, f"{SOURCE}.tenure_event\x1f{ids['bioguide']}:{term['start']}:expelled"),
                        tenure_id=tenure,
                        kind="expelled",
                        date=vote,
                        actor="United States Senate",
                        source_id=senate,
                    )
                affiliations = [
                    {**span, "start": max(span["start"], term["start"]), "end": min(span["end"], term["end"])}
                    for span in AMENDED.get(ids["bioguide"], [])
                    if span["start"] <= term["end"] and span["end"] >= term["start"]
                ] or (
                    term.get("party_affiliations")
                    or [
                        {
                            "start": term["start"],
                            "end": term["end"],
                            "party": term.get("party"),
                            "caucus": term.get("caucus"),
                        }
                    ]
                )
                for index, affiliation in enumerate(affiliations):
                    label = LABELS.get(affiliation["party"], affiliation["party"])
                    if label is None or label in UNAFFILIATED - {"Independent"}:
                        continue
                    since = max(start, date.fromisoformat(affiliation["start"]))
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
                        else min(end, date.fromisoformat(affiliation["end"]))
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
                    caucus = affiliation.get("caucus")
                    if caucus is not None:
                        cs.add(
                            "tenure_affiliation",
                            tenure_id=tenure,
                            kind="parliamentary_group",
                            party_id=cs.key(
                                "us.party", "republican-party" if caucus == "Republican" else NATIONAL[caucus]
                            ),
                            valid_from=since,
                            valid_from_precision="day",
                            valid_to=None if until > today else until + timedelta(days=1),
                            valid_to_precision="day",
                        )
        for seat, label, area in conn.execute(
            "select id, label, district_area_id from seat where id = any(%s::uuid[])", [list(states)]
        ).fetchall():
            cs.add(
                "seat",
                id=seat,
                body_id=bodies["senate"],
                district_area_id=area,
                label=label,
                voting=True,
                apportioned_area_id=area,
                valid_from=ADMITTED.get(states[seat], RATIFIED.get(states[seat], date(1789, 3, 4))),
                valid_to=None,
                valid_to_precision="day",
            )
        for tenure in unseated:
            cs.add("entity_retirement", entity_id=tenure, reason="Elected but never seated in this Congress")
        for (seat,) in conn.execute(
            """select s.id from seat s
               join changeset c on c.id = s.changeset_id and c.pipeline = %s
               join entity e on e.id = s.id and e.retired_by is null
               where not exists (select from tenure t where t.seat_id = s.id)
                 and not exists (select from contest k where k.seat_id = s.id)""",
            [SOURCE],
        ).fetchall():
            if seat not in seated and seat not in states:
                cs.add("entity_retirement", entity_id=seat, reason="No member of Congress held this seat")
        return cs.write()
