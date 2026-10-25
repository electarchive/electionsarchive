import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb import mnis as gb_mnis
from ea.store import DATA, connect

SOURCE = "gb.tenures"
VERSION = "5"
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?name ?pos ?start ?end ?cause ?district ?group ?mnis WHERE {
?pos wdt:P279 wd:Q16707842 . ?item p:P39 ?held . ?held ps:P39 ?pos .
OPTIONAL { ?held pq:P580 ?start } OPTIONAL { ?held pq:P582 ?end } OPTIONAL { ?held pq:P1534 ?cause }
OPTIONAL { ?held pq:P768 ?district } OPTIONAL { ?held pq:P4100 ?group } OPTIONAL { ?item wdt:P10428 ?mnis }
OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = "en") } }""",
        "format": "json",
    }
)
ENDED = {
    "Q5247364": "died",
    "Q796919": "resigned",
    "Q13634247": "annulled",
    "Q54365895": "expelled",
    "Q40430404": "disqualified",
    "Q152074": "disqualified",
    "Q63418438": "removed",
    "Q36717319": "elevated",
    "Q46992747": "elevated",
    "Q4021063": "elevated",
    "Q46993416": "appointed",
    "Q52112831": "appointed",
    "Q50393121": "chose_another_seat",
    "Q51212178": "appointed",
}
VACATED = set(ENDED)
UNMATCHED = {(date(1838, 5, 21), "hope"), (date(1847, 1, 30), "grosvenor"), (date(1867, 2, 12), "morris")}
DOUBLE = re.compile(
    r"also (?:being )?(?:elected|returned)|deci(?:ding|sion) to sit|option to sit|opting to sit|opted to sit"
)
NAMED = re.compile(r"due to (?!the )([A-Z][\w'’-]*(?: [A-Z][\w'’-]*)*) ")
CAUSES = (
    ("died", re.compile(r"death|died")),
    ("elevated", re.compile(r"elevat|peerage|succession to")),
    ("resigned", re.compile(r"resign|chiltern|northstead")),
    ("appointed", re.compile(r"appoint")),
    ("annulled", re.compile(r"void|petition|unseated")),
    ("expelled", re.compile(r"expel")),
    ("disqualified", re.compile(r"bankrupt|disqualif|lunacy|insan")),
    ("removed", re.compile(r"recall")),
)
STEWARDS = "https://researchbriefings.files.parliament.uk/documents/SN04731/SN04731.xlsx"
STEWARDS_TITLE = "Appointments to the Chiltern Hundreds and Manor of Northstead Stewardships since 1880 (SN04731)"
DELIVERED = DATA / "inbox" / "manual" / "MF-071" / "SN04731.xlsx"
TITLES = {"sir", "lord", "dr", "hon", "rev", "captain", "major", "colonel", "general", "admiral", "viscount", "earl"}


def fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.split(",")[0]).encode("ascii", "ignore").decode().lower()
    return [word for word in re.findall(r"[a-z]+", plain.replace("'", "")) if word not in TITLES]


def vacated(cause: str) -> str | None:
    if DOUBLE.search(cause):
        return "double"
    return next((kind for kind, pattern in CAUSES if pattern.search(cause.lower())), None)


PREDECESSORS = {
    "8fba0e65-95fa-5a23-98c9-ce4d27a7a3a3": "littleton",
    "80a16c12-2c8f-56b2-83ac-df935bde0ddf": "hobhouse",
    "49f7a127-489b-553c-b514-2b15beb7b5a3": "hobhouse",
    "3288414d-cc61-5599-9807-b89dfdc4063c": "oconor",
    "0ba00cd8-2b96-5405-b1aa-7f8208297477": "rothschild",
    "699e929a-bc86-5e3c-bdbd-d68b2dc460dc": "busfeild",
    "15f28d13-a211-5581-a805-a8bae5f4916d": "disraeli",
    "24dfe67a-f588-5745-accc-fa48fbb23e0e": "hervey",
    "f481b2c9-edb4-5159-9aac-0e3f14ff8246": "fortescue",
    "f6c06bcd-a9ce-5948-84f2-b2ed77b8d476": "molesworth",
    "455959ff-204f-524f-9950-9073cf49f84e": "rothschild",
    "f28fd5a6-4964-5dd4-9c46-4b9eb74b4cd0": "cairns",
    "8093770e-61c6-5b8e-8cbd-bc95af54e366": "bridgeman",
    "ed8c2817-e8a0-5625-8488-124f48537088": "heathcote",
    "f164e122-e965-5e50-b445-d9087783359c": "cecil",
    "5e0ebb7e-287e-564d-a22a-43b4cb40b089": "manners",
    "2d44f67e-f38a-51f5-8f3b-7e79984771d2": "thynne",
    "515a0943-43f6-5441-ba56-3e29408f8e21": "disraeli",
    "5e292f86-dbb4-578c-b62f-a59179f0afe5": "corbett",
    "be858a6f-57cb-56e6-b48a-6b79edd1c961": "harcourt",
    "02fe3b9b-700e-59c5-9f33-bf94bdda1bfa": "holmes",
    "6c827f18-83ff-5aad-ae8c-eda25417325f": "madden",
    "681b7ebb-8eb0-5a08-976a-1a3a7389fcbe": "morley",
    "739e12aa-76d2-5255-b688-39b175f14719": "carson",
}


def cause_name(cause: str) -> list[str] | None:
    found = NAMED.search(cause)
    return fold(re.sub(r"['’]s?$", "", found.group(1)))[-1:] if found else None


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")
    try:
        raw.cached(SOURCE, STEWARDS)
    except LookupError:
        raw.store(
            SOURCE,
            STEWARDS,
            DELIVERED.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime.fromtimestamp(DELIVERED.stat().st_mtime, UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def emit() -> Path:
    artifact = raw.cached(SOURCE, SERVICE)
    statements: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    names: dict[str, str] = {}
    numbers: dict[str, str] = {}
    for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
        row = {
            key: value["value"].rsplit("/", 1)[-1] if key != "name" else value["value"]
            for key, value in binding.items()
        }
        if "start" not in row:
            continue
        names[row["item"]] = row.get("name", "")
        if "mnis" in row:
            numbers[row["item"]] = row["mnis"]
        statements[(row["item"], row["pos"], row.get("district", ""))].append(row)
    chains: dict[str, list[dict[str, object]]] = defaultdict(list)
    for (item, pos, district), rows in statements.items():
        spans: dict[str, dict[str, str]] = {}
        for row in rows:
            spans.setdefault(row["start"], row)
        current: dict[str, object] | None = None
        for start in sorted(spans):
            row = spans[start]
            begin = date.fromisoformat(start[:10])
            until = date.fromisoformat(row["end"][:10]) if "end" in row else None
            piece = (begin, until, row.get("group"))
            if current is not None and (current["end"] is None or begin <= current["end"] + timedelta(days=1)):
                current["pieces"].append(piece)
                if current["end"] is not None and (until is None or until > current["end"]):
                    current["end"], current["cause"] = until, row.get("cause")
                continue
            current = {
                "item": item,
                "district": district,
                "start": begin,
                "end": until,
                "cause": row.get("cause"),
                "pieces": [piece],
            }
            chains[pos].append(current)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Commons tenures for every Parliament from 1832 to the present: general election winners, "
            "members who joined between elections, and when and why each member left, from Wikidata",
            polity="GB",
        )
        cs.manifest["scope"] = {"polity": "GB", "replaces": {"tenure_affiliation": ["gb.members", "identity"]}}
        commons = cs.key("gb.body", "house-of-commons")
        if commons in cs.minted:
            raise ValueError("the House of Commons must already exist")
        wikidata = cs.source("Wikidata", "Members of the Parliaments of the United Kingdom")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of the Parliaments of the United Kingdom",
            url="https://www.wikidata.org/wiki/Q16707842",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(artifact, wikidata)
        library = cs.source("House of Commons Library", STEWARDS_TITLE)
        cs.add(
            "source",
            id=library,
            kind="official_body",
            publisher="House of Commons Library",
            title=STEWARDS_TITLE,
            url="https://commonslibrary.parliament.uk/research-briefings/sn04731/",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="The date each member who resigned was appointed to a stewardship, from the Treasury Register; it is "
            "the day the seat fell vacant.",
        )
        cs.artifact(raw.cached(SOURCE, STEWARDS), library)
        stewards: list[tuple[date, list[str], set[str]]] = []
        for _, appointed, member, _, _, place, *_ in pl.read_excel(
            raw.cached(SOURCE, STEWARDS).path, sheet_name="DATA", read_options={"header_row": 2}
        ).iter_rows():
            if member is None or appointed is None or place is None:
                continue
            text = appointed.removesuffix(" 00:00:00").strip()
            day = (
                date.fromisoformat(text)
                if text[:4].isdigit()
                else datetime.strptime(text, "%d %B %Y" if len(text.split()[1]) > 3 else "%d %b %Y").date()
            )
            stewards.append((day, fold(member), set(re.findall(r"[a-z]+", place.lower())) - {"and", "the", "of"}))
        resigned = 0
        elections: list[tuple[UUID, date]] = conn.execute(
            """select distinct e.id, e.date_from from election e join contest c on c.election_id = e.id
               where c.body_id = %s and e.kind = 'general' order by e.date_from""",
            [commons],
        ).fetchall()
        following = {election: elections[index + 1][1] for index, (election, _) in enumerate(elections[:-1])}
        dated = {day: election for election, day in elections}
        seated = {
            election
            for (election,) in conn.execute(
                """select distinct c.election_id from tenure t join contest c on c.id = t.contest_id
                   where t.body_id = %s
                     and not exists (select from external_id x where x.entity_id = t.id and x.scheme = %s)""",
                [commons, f"{SOURCE}.tenure"],
            ).fetchall()
        }
        parties = dict(
            conn.execute(
                "select e.value, e.entity_id from external_id e join party p on p.id = e.entity_id where e.scheme = 'wikidata'"
            ).fetchall()
        )
        items: dict[UUID, set[str]] = defaultdict(set)
        linked: dict[str, UUID] = {}
        for scheme, value, person in conn.execute(
            """select e.scheme, e.value, e.entity_id from external_id e join person p on p.id = e.entity_id
               where e.scheme in ('wikidata', 'mnis')
                 and not exists (select from entity_merge m where m.entity_id = e.entity_id)"""
        ).fetchall():
            items[person].add(f"{scheme}:{value}")
            linked[f"{scheme}:{value}"] = person
        winners: dict[UUID, list[tuple[UUID, UUID, list[str], str, UUID | None, UUID | None]]] = defaultdict(list)
        for election, contest, person, party, person_name, contest_name, area in conn.execute(
            """select e.id, c.id, cy.person_id, o.party_id,
                      (select text from name where entity_id = cy.person_id and is_preferred limit 1),
                      (select text from name where entity_id = c.id and is_preferred limit 1), c.district_area_id
               from contest c
               join election e on e.id = c.election_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join outcome x on x.result_set_id = rs.id and x.kind = 'elected'
               join contest_option o on o.id = x.option_id
               join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               where c.body_id = %s and e.kind = 'general'
               order by c.id, cy.person_id""",
            [commons],
        ).fetchall():
            winners[election].append((contest, person, fold(person_name or ""), contest_name or "", party, area))
        by_elections: list[tuple[date, UUID, UUID | str, str, list[tuple[UUID, list[str], UUID | None]]]] = []
        places: dict[UUID, set[str]] = {}
        for day, contest, area, place, cause, people in conn.execute(
            """select e.date_from, c.id, c.district_area_id,
                      (select text from name where entity_id = c.id and is_preferred limit 1),
                      (select body_md from note where entity_id = c.id and kind = 'context' limit 1),
                      array_agg(array[cy.person_id::text, coalesce((select text from name
                        where entity_id = cy.person_id and is_preferred limit 1), ''), o.party_id::text]
                        order by cy.person_id)
               from contest c
               join election e on e.id = c.election_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join outcome x on x.result_set_id = rs.id and x.kind = 'elected'
               join contest_option o on o.id = x.option_id
               join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               where c.body_id = %s and e.kind = 'by_election'
               group by 1, 2, 3, 4 order by 1, 2""",
            [commons],
        ).fetchall():
            places[contest] = set(re.findall(r"[a-z]+", (place or "").partition(" in ")[2].lower())) - {
                "and",
                "the",
                "of",
            }
            by_elections.append(
                (
                    day,
                    contest,
                    area or (place or "").partition(" in ")[2],
                    cause or "",
                    [(UUID(person), fold(text), UUID(party) if party else None) for person, text, party in people],
                )
            )
        held: dict[tuple[UUID, UUID], UUID] = {}
        keyed: set[UUID] = set()
        joiners: dict[str, UUID] = {}
        for entity, contest, person, value in conn.execute(
            """select t.id, t.contest_id, t.person_id, x.value from external_id x
               join entity e on e.id = x.entity_id and e.retired_by is null
               join tenure t on t.id = x.entity_id where x.scheme = %s""",
            [f"{SOURCE}.tenure"],
        ).fetchall():
            keyed.add(entity)
            joiners[value] = person
            if contest is not None:
                held[(contest, person)] = entity
        emitted: set[UUID] = set()
        sitting_elsewhere: dict[tuple[UUID, UUID], list[tuple[date, date | None]]] = defaultdict(list)
        for person, area, begin, stop in conn.execute(
            """select t.person_id, c.district_area_id, t.from_date, t.to_date from tenure t
               join contest c on c.id = t.contest_id join changeset s on s.id = t.changeset_id
               join entity e on e.id = t.id and e.retired_by is null
               where s.pipeline <> %s and c.district_area_id is not null""",
            [SOURCE],
        ).fetchall():
            sitting_elsewhere[(person, area)].append((begin, stop))
        continued = 0
        split: list[tuple[date, UUID, UUID, UUID]] = []
        matched = joined = left = ended = unresolved = declined = dead = unmatched = 0
        assigned: dict[UUID, list[tuple[date, list[dict[str, object]]]]] = defaultdict(list)
        for members in chains.values():
            modal = Counter(member["start"] for member in members).most_common(1)[0][0]
            near = [day for day in dated if abs((day - modal).days) <= 40]
            if near:
                assigned[dated[min(near, key=lambda day: abs((day - modal).days))]].append((modal, members))
        for election, start in elections:
            own = election not in seated
            end = following.get(election)
            general = [
                member
                for modal, members in assigned[election]
                for member in members
                if member["start"] <= modal + timedelta(days=45)
            ]
            later = [
                member
                for modal, members in assigned[election]
                for member in members
                if member["start"] > modal + timedelta(days=7)
            ]
            sitting = [b for b in by_elections if b[0] > start and (end is None or b[0] < end)]
            returned = Counter(person for _, person, _, _, _, _ in winners[election])
            open_seats: dict[UUID | str, list[dict[str, object]]] = defaultdict(list)
            records: list[dict[str, object]] = []

            def pick(pool: list[dict[str, object]], person: UUID, words: list[str]) -> dict[str, object] | None:
                hits = [
                    member
                    for member in pool
                    if "tenure" not in member
                    and {f"wikidata:{member['item']}", f"mnis:{numbers.get(str(member['item']))}"} & items[person]
                ] or [
                    member
                    for member in pool
                    if "tenure" not in member and words and fold(names[str(member["item"])])[-1:] == words[-1:]
                ]
                if len(hits) > 1:
                    hits = [member for member in hits if fold(names[str(member["item"])])[:1] == words[:1]] or hits
                return hits[0] if len(hits) == 1 else None

            for contest, person, words, contest_name, party, area in winners[election] if own else []:
                seat = area or contest_name.partition(" in ")[2]
                if returned[person] > 1:
                    choice = next(
                        (
                            b
                            for b in sitting
                            if b[2] == seat and DOUBLE.search(b[3]) and (cause_name(b[3]) in (None, words[-1:]))
                        ),
                        None,
                    )
                    if choice is not None:
                        others = [
                            name.partition(" in ")[2]
                            for other, someone, _, name, _, _ in winners[election]
                            if someone == person and other != contest
                        ]
                        cs.add(
                            "note",
                            entity_id=contest,
                            kind="context",
                            body_md=f"Also returned for {' and '.join(others)}, where the member chose to sit; this seat "
                            f"was filled at a by-election on {choice[0]:%B %-d, %Y}.",
                        )
                        declined += 1
                        continue
                tenure = held.get((contest, person)) or cs.key(f"{SOURCE}.tenure", f"{contest}:{person}")
                member = pick(general, person, words)
                record: dict[str, object] = {
                    "tenure": tenure,
                    "person": person,
                    "words": words,
                    "from": start,
                    "contest": contest,
                    "party": party,
                    "stop": end,
                    "cause": None if end is None else "term_end",
                    "spans": None,
                    "succeeds": None,
                }
                if member is not None:
                    member["tenure"] = tenure
                    matched += 1
                    if (
                        member["cause"] in VACATED
                        and member["end"] is not None
                        and (end is None or member["end"] < end - timedelta(days=1))
                    ):
                        record["stop"], record["cause"] = member["end"], ENDED.get(str(member["cause"]))
                        left += 1
                records.append(record)
                open_seats[seat].append(record)
            for day, contest, area, cause, people in sitting:
                kind = vacated(cause)
                if kind == "double":
                    previous: list[dict[str, object]] = []
                else:
                    pool = [record for record in open_seats[area] if record["from"] < day and "successor" not in record]
                    named = [PREDECESSORS[str(contest)]] if str(contest) in PREDECESSORS else cause_name(cause)
                    if named is not None and not own and not any(record["words"][-1:] == named for record in pool):
                        pool = []
                    if len(pool) > len(people) and named is not None:
                        pool = [record for record in pool if record["words"][-1:] == named] or pool
                    if len(pool) > len(people):
                        early = [
                            record
                            for record in pool
                            if record["stop"] is not None and record["stop"] <= day and record["cause"] != "term_end"
                        ]
                        pool = early if len(early) == len(people) else pool
                    previous = pool if len(pool) == len(people) else []
                    if not previous and own:
                        unresolved += 1
                        print(
                            f"UNRESOLVED {day} {contest} {named} {len(people)} of {len(pool)}: "
                            f"{[record['words'] for record in pool]} | {cause}"
                        )
                for record in previous:
                    record["successor"] = True
                    if record["stop"] is None or record["stop"] > day or record["cause"] == "term_end":
                        record["stop"], record["cause"] = day, kind
                        ended += 1
                    if kind == "resigned":
                        appointed = [
                            when
                            for when, surname, seat in stewards
                            if timedelta(0) <= day - when <= timedelta(days=400)
                            and surname[-1:] == record["words"][-1:]
                            and seat & places[contest]
                            and (seat <= places[contest] or places[contest] <= seat)
                        ]
                        stop = record["stop"]
                        if len(appointed) == 1 and isinstance(stop, date) and appointed[0] < stop:
                            record["stop"], record["steward"] = appointed[0], True
                            resigned += 1
                for index, (person, words, party) in enumerate(people):
                    if kind == "appointed" and any(
                        begin <= day and (stop is None or day < stop)
                        for begin, stop in sitting_elsewhere.get((person, area), [])
                    ):
                        continued += 1
                        continue
                    if kind == "died" and any(record["person"] == person for record in previous):
                        print(f"REVIEW {day} {contest}: the winner is the member whose death caused the by-election")
                        dead += 1
                        continue
                    member = (
                        None
                        if (day, "".join(words[-1:])) in UNMATCHED
                        else pick(
                            [member for member in later if abs((member["start"] - day).days) <= 60], person, words
                        )
                    )
                    tenure = held.get((contest, person)) or cs.key(
                        f"{SOURCE}.tenure",
                        f"{member['item']}:{member['start']}" if member is not None else f"{contest}:{person}",
                    )
                    holder = person
                    if member is not None:
                        ids = {f"wikidata:{member['item']}", f"mnis:{numbers.get(str(member['item']))}"}
                        known = next((linked[id] for id in sorted(ids) if id in linked), None)
                        if not ids & items[person] and known is not None and known != person:
                            holder = known
                            split.append((day, contest, person, known))
                    record = {
                        "tenure": tenure,
                        "person": holder,
                        "words": words,
                        "from": day,
                        "contest": contest,
                        "party": party,
                        "stop": end,
                        "cause": None if end is None else "term_end",
                        "spans": None,
                        "succeeds": previous[index]["tenure"] if len(previous) == len(people) else None,
                    }
                    if member is not None:
                        member["tenure"] = tenure
                        spans = [
                            (since, until, parties.get(str(group)) if group is not None else None)
                            for since, until, group in member["pieces"]
                        ]
                        if any(group is not None for _, _, group in spans):
                            record["spans"] = spans
                        if (
                            member["cause"] in VACATED
                            and member["end"] is not None
                            and (end is None or member["end"] < end - timedelta(days=1))
                        ):
                            record["stop"], record["cause"] = member["end"], ENDED.get(str(member["cause"]))
                            left += 1
                    records.append(record)
                    open_seats[area].append(record)
                    joined += 1
            for member in later if own else []:
                if "tenure" in member or (end is not None and member["start"] >= end):
                    continue
                item = str(member["item"])
                surname = fold(names[item])[-1:]
                if any(
                    record["contest"] is not None
                    and record["from"] > start
                    and abs((record["from"] - member["start"]).days) <= 90
                    and record["words"][-1:] == surname
                    for record in records
                ):
                    continue
                holder = (
                    linked.get(f"wikidata:{item}")
                    or linked.get(f"mnis:{numbers.get(item)}")
                    or joiners.get(f"{item}:{member['start']}")
                )
                if holder is None:
                    continue
                record = {
                    "tenure": cs.key(f"{SOURCE}.tenure", f"{item}:{member['start']}"),
                    "person": holder,
                    "words": surname,
                    "from": member["start"],
                    "contest": None,
                    "party": None,
                    "stop": end,
                    "cause": None if end is None else "term_end",
                    "spans": [
                        (since, until, parties.get(str(group)) if group is not None else None)
                        for since, until, group in member["pieces"]
                    ],
                    "succeeds": None,
                }
                if (
                    member["cause"] in VACATED
                    and member["end"] is not None
                    and (end is None or member["end"] < end - timedelta(days=1))
                ):
                    record["stop"], record["cause"] = member["end"], ENDED.get(str(member["cause"]))
                member["tenure"] = record["tenure"]
                print(f"REVIEW {member['start']} {names[item]} ({item}): joined with no by-election winner to match")
                records.append(record)
                unmatched += 1
            mnis: dict[UUID, list[tuple[date, date | None, UUID]]] = {}
            for person, number in gb_mnis.members():
                try:
                    artifact = raw.cached(gb_mnis.SOURCE, gb_mnis.BIOGRAPHY.format(number))
                except LookupError:
                    continue
                for affiliation in json.loads(artifact.path.read_text(encoding="utf-8"))["value"]["partyAffiliations"]:
                    if affiliation["house"] != 1 or not affiliation["startDate"]:
                        continue
                    since = date.fromisoformat(affiliation["startDate"][:10])
                    key = gb_mnis.PARTIES.get(affiliation["name"]) or next(
                        (key for before, key in gb_mnis.DATED.get(affiliation["name"], ()) if since < before), None
                    )
                    if key is None:
                        continue
                    group = cs.key(*key)
                    if group in cs.minted:
                        raise ValueError(f"no party {key}")
                    until = date.fromisoformat(affiliation["endDate"][:10]) if affiliation["endDate"] else None
                    mnis.setdefault(person, []).append((since, until, group))
                mnis.get(person, []).sort()
            floor_crossings = 0
            for record in records:
                tenure, stop = record["tenure"], record["stop"]
                emitted.add(tenure)
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=record["person"],
                    body_id=commons,
                    from_date=record["from"],
                    from_precision="day",
                    to_date=stop,
                    to_precision="day",
                    acquired_via="elected",
                    contest_id=record["contest"],
                    ended_via=record["cause"],
                    succeeds_tenure_id=record["succeeds"],
                )
                if record.get("steward"):
                    cs.add("citation", entity_id=tenure, source_id=library)
                spans = record["spans"] or (
                    [(record["from"], stop, record["party"])] if record["party"] is not None else []
                )
                crossed = [
                    (max(since, record["from"]), until, group)
                    for since, until, group in mnis.get(record["person"], [])
                    if (stop is None or since < stop) and (until is None or until > record["from"])
                ]
                if len({group for *_, group in crossed}) > 1 and all(
                    until is not None and until <= after for (_, until, _), (after, _, _) in pairwise(crossed)
                ):
                    spans = crossed
                    floor_crossings += 1
                for index, (since, until, group) in enumerate(spans):
                    if group is None:
                        continue
                    upto = spans[index + 1][0] if index + 1 < len(spans) else stop
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=group,
                        valid_from=max(since, record["from"]),
                        valid_from_precision="day",
                        valid_to=upto if upto is None or stop is None else min(upto, stop),
                        valid_to_precision="day",
                    )
        stale = sorted(keyed - emitted, key=str)
        for tenure in stale:
            cs.add(
                "entity_retirement",
                entity_id=tenure,
                reason="A Commons tenure this pipeline no longer emits: a seat the member declined after a double "
                "return, a member joining between elections now held on the by-election contest, or a minister's "
                "re-election that continues the tenure from the general election",
            )
        print(
            f"{matched} general winners matched, {joined} by-election winners, {left} left early per Wikidata, "
            f"{ended} predecessors ended by a by-election, {resigned} on the day they took a stewardship, {unresolved} by-elections with no predecessor found, "
            f"{declined} declined seats, {len(stale)} tenures retired, {len(split)} by-election winners held apart "
            f"from their Wikidata person, {dead} by-election winners who caused the vacancy by dying, {continued} ministers re-elected "
            f"within a tenure, {unmatched} "
            f"joiners with no by-election winner, {floor_crossings} tenures split at a party change in MNIS"
        )
        return cs.write()
