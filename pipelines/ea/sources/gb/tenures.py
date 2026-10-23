import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.tenures"
VERSION = "2"
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
}
VACATED = {
    *ENDED,
    "Q52112831",
    "Q51212178",
    "Q46993416",
    "Q36717319",
    "Q46992747",
    "Q50393121",
    "Q4021063",
}
TITLES = {"sir", "lord", "dr", "hon", "rev", "captain", "major", "colonel", "general", "admiral", "viscount", "earl"}


def fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.split(",")[0]).encode("ascii", "ignore").decode().lower()
    return [word for word in re.findall(r"[a-z]+", plain.replace("'", "")) if word not in TITLES]


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")


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
        elections: list[tuple[UUID, date]] = conn.execute(
            """select distinct e.id, e.date_from from election e join contest c on c.election_id = e.id
               where c.body_id = %s and e.kind = 'general' order by e.date_from""",
            [commons],
        ).fetchall()
        following = {election: elections[index + 1][1] for index, (election, _) in enumerate(elections[:-1])}
        dated = {day: election for election, day in elections}
        days = dict(elections)
        seated = {
            election
            for (election,) in conn.execute(
                """select distinct c.election_id from tenure t join contest c on c.id = t.contest_id
                   join changeset s on s.id = t.changeset_id
                   where t.body_id = %s and s.pipeline not in (%s, 'identity')""",
                [commons, SOURCE],
            ).fetchall()
        }
        parties = dict(
            conn.execute(
                "select e.value, e.entity_id from external_id e join party p on p.id = e.entity_id where e.scheme = 'wikidata'"
            ).fetchall()
        )
        linked: dict[str, UUID] = {}
        for scheme, value, person in conn.execute(
            """select e.scheme, e.value, e.entity_id from external_id e join person p on p.id = e.entity_id
               where e.scheme in ('wikidata', 'mnis')"""
        ).fetchall():
            linked[f"{scheme}:{value}"] = person
        winners: dict[UUID, list[tuple[UUID, UUID, list[str], str, UUID | None]]] = defaultdict(list)
        for election, contest, person, party, person_name, contest_name in conn.execute(
            """select e.id, c.id, cy.person_id, o.party_id,
                      (select text from name where entity_id = cy.person_id and is_preferred limit 1),
                      (select text from name where entity_id = c.id and is_preferred limit 1)
               from contest c
               join election e on e.id = c.election_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join outcome x on x.result_set_id = rs.id and x.kind = 'elected'
               join contest_option o on o.id = x.option_id
               join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               where c.body_id = %s and e.kind = 'general'""",
            [commons],
        ).fetchall():
            winners[election].append((contest, person, fold(person_name or ""), contest_name or "", party))
        stood: dict[tuple[UUID, str], list[tuple[UUID, list[str], UUID | None]]] = defaultdict(list)
        for election, contest_name, person, person_name, party in conn.execute(
            """select e.id, (select text from name where entity_id = c.id and is_preferred limit 1), cy.person_id,
                      (select text from name where entity_id = cy.person_id and is_preferred limit 1), o.party_id
               from contest c join election e on e.id = c.election_id
               join contest_option o on o.contest_id = c.id
               join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               where c.body_id = %s and e.kind = 'general'""",
            [commons],
        ).fetchall():
            place = (contest_name or "").partition(" in ")[2]
            stood[(election, place)].append((person, fold(person_name or ""), party))
        found: dict[str, UUID] = {}
        places: dict[tuple[str, str], str] = {}
        tenures: dict[str, list[dict[str, object]]] = defaultdict(list)
        matched = joined = left = created = orphaned = 0
        for pos, members in chains.items():
            modal = Counter(member["start"] for member in members).most_common(1)[0][0]
            near = [day for day in dated if abs((day - modal).days) <= 40]
            if not near:
                continue
            election = dated[min(near, key=lambda day: abs((day - modal).days))]
            if election in seated:
                continue
            end = following.get(election)
            general = [member for member in members if member["start"] <= modal + timedelta(days=45)]
            taken: set[UUID] = set()
            for contest, person, words, contest_name, party in winners[election]:
                if not words:
                    continue
                pool = [
                    member
                    for member in general
                    if "tenure" not in member and fold(names[member["item"]])[-1:] == words[-1:]
                ]
                if len(pool) > 1:
                    pool = [member for member in pool if fold(names[member["item"]])[:1] == words[:1]] or pool
                if len(pool) != 1 or contest in taken:
                    continue
                member = pool[0]
                taken.add(contest)
                member["tenure"] = cs.key(f"{SOURCE}.tenure", f"{contest}:{person}")
                member["person"], member["party"] = person, party
                found.setdefault(member["item"], person)
                places[(pos, str(member["district"]))] = contest_name.partition(" in ")[2]
                matched += 1
            for contest, person, _, _, party in winners[election]:
                tenure = cs.key(f"{SOURCE}.tenure", f"{contest}:{person}")
                member = next((member for member in general if member.get("tenure") == tenure), None)
                stop, cause = end, None
                if (
                    member is not None
                    and member["cause"] in VACATED
                    and member["end"] is not None
                    and (end is None or member["end"] < end - timedelta(days=1))
                ):
                    stop, cause = member["end"], ENDED.get(str(member["cause"]), None)
                    left += 1
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=commons,
                    from_date=days[election],
                    from_precision="day",
                    to_date=stop,
                    to_precision="day",
                    acquired_via="elected",
                    contest_id=contest,
                    ended_via=cause if stop != end else (None if end is None else "term_end"),
                )
                if party is not None:
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=party,
                        valid_from=days[election],
                        valid_from_precision="day",
                        valid_to=stop,
                        valid_to_precision="day",
                    )
                if member is not None:
                    tenures[(pos, str(member["district"]))].append({**member, "stop": stop})
            for member in sorted(members, key=lambda member: member["start"]):
                if "tenure" in member or member["start"] <= modal + timedelta(days=7):
                    continue
                if end is not None and member["start"] >= end:
                    continue
                before = [
                    other
                    for other in tenures[(pos, str(member["district"]))]
                    if other["stop"] is not None and other["stop"] <= member["start"] and "successor" not in other
                ]
                predecessor = max(before, key=lambda other: other["stop"], default=None)
                if predecessor is None:
                    orphaned += 1
                    continue
                item = str(member["item"])
                place = places.get((pos, str(member["district"])), "")
                person = found.get(item) or linked.get(f"wikidata:{item}") or linked.get(f"mnis:{numbers.get(item)}")
                words = fold(names[item])
                party = None
                for neighbor in (election, next((e for e, d in elections if end is not None and d == end), None)):
                    for candidate, candidate_words, candidate_party in stood.get((neighbor, place), []):
                        if candidate_words[-1:] == words[-1:] and candidate_words[:1] == words[:1]:
                            person = person or candidate
                            party = party or candidate_party
                if person is None:
                    person = cs.key(f"{SOURCE}.person", item)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add(
                            "name",
                            entity_id=person,
                            text=names[item].split(",")[0],
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                        cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                        if item in numbers:
                            cs.add("external_id", entity_id=person, scheme="mnis", value=numbers[item])
                        created += 1
                found.setdefault(item, person)
                tenure = cs.key(f"{SOURCE}.tenure", f"{item}:{member['start']}")
                stop, cause = end, "term_end" if end is not None else None
                if (
                    member["cause"] in VACATED
                    and member["end"] is not None
                    and (end is None or member["end"] < end - timedelta(days=1))
                ):
                    stop, cause = member["end"], ENDED.get(str(member["cause"]))
                    left += 1
                predecessor["successor"] = tenure
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=commons,
                    from_date=member["start"],
                    from_precision="day",
                    to_date=stop,
                    to_precision="day",
                    acquired_via="elected",
                    ended_via=cause,
                    succeeds_tenure_id=predecessor["tenure"],
                )
                spans = [
                    (since, until, parties.get(str(group)) if group is not None else None)
                    for since, until, group in member["pieces"]
                ]
                if all(group is None for _, _, group in spans) and party is not None:
                    spans = [(member["start"], stop, party)]
                for index, (since, until, group) in enumerate(spans):
                    if group is None:
                        continue
                    upto = spans[index + 1][0] if index + 1 < len(spans) else stop
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=group,
                        valid_from=since,
                        valid_from_precision="day",
                        valid_to=upto if upto is None or stop is None else min(upto, stop),
                        valid_to_precision="day",
                    )
                tenures[(pos, str(member["district"]))].append({**member, "tenure": tenure, "stop": stop})
                joined += 1
        print(
            f"{matched} general winners matched, {joined} joined between elections, {left} left early, {created} new people, {orphaned} entrants with no departed predecessor left out"
        )
        return cs.write()
