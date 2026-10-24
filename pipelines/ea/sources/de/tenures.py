import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "de.tenures"
VERSION = "1"
ELECTION = date(2021, 9, 26)
TERM_START = date(2021, 10, 26)
TERM_END = date(2025, 3, 25)
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?name ?birth ?bp ?gender ?start ?end ?cause ?districtName ?party ?ps ?pe WHERE {
?item p:P39 ?held . ?held ps:P39 wd:Q1939555 ; pq:P2937 wd:Q33091469 .
OPTIONAL { ?held pq:P580 ?start } OPTIONAL { ?held pq:P582 ?end } OPTIONAL { ?held pq:P1534 ?cause }
OPTIONAL { ?held pq:P768 ?district . ?district rdfs:label ?districtName FILTER(LANG(?districtName) = "en") }
OPTIONAL { ?item p:P102 ?m . ?m ps:P102 ?party . OPTIONAL { ?m pq:P580 ?ps } OPTIONAL { ?m pq:P582 ?pe } }
OPTIONAL { ?item p:P569 [ psv:P569 [ wikibase:timeValue ?birth ; wikibase:timePrecision ?bp ] ] }
OPTIONAL { ?item wdt:P21 ?gender }
OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = "de") } }""",
        "format": "json",
    }
)
GENDERS = {"Q6581097": "male", "Q6581072": "female", "Q1097630": "diverse", "Q48270": "diverse"}
ENDED = {
    "Q5247364": "died",
    "Q796919": "resigned",
    "Q13634247": "annulled",
    "Q54365895": "expelled",
    "Q40430404": "disqualified",
    "Q152074": "disqualified",
    "Q63418438": "removed",
}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("ß", "ss")))


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")


def emit() -> Path:
    artifact = raw.cached(SOURCE, SERVICE)
    members: dict[str, dict[str, object]] = {}
    for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
        row = {
            key: value["value"] if key in ("name", "districtName") else value["value"].rsplit("/", 1)[-1]
            for key, value in binding.items()
        }
        for key in ("start", "end", "ps", "pe", "birth"):
            if key in row and not row[key][:4].isdigit():
                del row[key]
        member = members.setdefault(row["item"], {"spans": {}, "parties": {}})
        for key in ("name", "birth", "bp", "gender"):
            if key in row:
                member[key] = row[key]
        if "start" in row:
            member["spans"][row["start"]] = row
        if "party" in row:
            member["parties"][(row["party"], row.get("ps"), row.get("pe"))] = None
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the 20th Bundestag with their tenures from Wikidata, and the constituency candidacies "
            "of members elected in 2021 whose district and party match the declared winner",
            polity="DE",
        )
        bundestag = cs.key("de.body", "bundestag")
        wikidata = cs.source("Wikidata", "Members of the 20th German Bundestag")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of the 20th German Bundestag",
            url="https://www.wikidata.org/wiki/Q33091469",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(artifact, wikidata)
        parties = dict(
            conn.execute(
                "select e.value, e.entity_id from external_id e join party p on p.id = e.entity_id where e.scheme = 'wikidata'"
            ).fetchall()
        )
        linked: dict[str, UUID] = dict(
            conn.execute(
                """select e.value, e.entity_id from external_id e join person p on p.id = e.entity_id
                   where e.scheme = 'wikidata' and not exists (select from entity_merge m where m.entity_id = p.id)"""
            ).fetchall()
        )
        german: dict[tuple[str, str], list[UUID]] = defaultdict(list)
        for person, text, born in conn.execute(
            """select p.id, n.text, left(p.birth_date::text, 4) from person p
               join name n on n.entity_id = p.id
               where p.birth_date is not null
               and not exists (select from entity_merge m where m.entity_id = p.id)
               and not exists (select from external_id w where w.entity_id = p.id and w.scheme = 'wikidata')
               and exists (select from external_id d where d.entity_id = p.id and d.scheme like 'de.%%')"""
        ).fetchall():
            german[(fold(text), born)].append(person)
        review: list[str] = []
        districts: dict[str, list[tuple[UUID, UUID, UUID | None]]] = defaultdict(list)
        for contest, name, option, party in conn.execute(
            """select c.id, (select text from name where entity_id = c.id and is_preferred limit 1), o.id, o.party_id
               from contest c join election e on e.id = c.election_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join outcome x on x.result_set_id = rs.id and x.kind = 'elected'
               join contest_option o on o.id = x.option_id
               where e.date_from = %s and c.body_id = %s and c.kind = 'body_seats'""",
            [ELECTION, bundestag],
        ).fetchall():
            if name is not None and name.startswith("Constituency "):
                districts[fold(name.partition(", ")[2])].append((contest, option, party))
        matched = skipped = created = tenures = resolved = 0
        reasons: dict[str, int] = defaultdict(int)
        for item, member in sorted(members.items()):
            spans = sorted(member["spans"].items())
            if not spans or "name" not in member:
                continue
            person = linked.get(item)
            if person is None and "birth" in member:
                candidates = set(german.get((fold(member["name"]), member["birth"][:4]), []))
                if len(candidates) > 1:
                    review.append(f"{item} {member['name']} {member['birth'][:4]}: {len(candidates)} existing persons")
                    continue
                if candidates:
                    person = candidates.pop()
                    cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                    resolved += 1
            if person is None:
                person = cs.key(f"{SOURCE}.person", item)
                cs.add(
                    "person",
                    id=person,
                    gender=GENDERS.get(member.get("gender", "")),
                    birth_date=member["birth"][:10] if "birth" in member else None,
                    birth_precision={"11": "day", "10": "month", "9": "year"}.get(member.get("bp", ""), None)
                    if "birth" in member
                    else None,
                )
                cs.add("name", entity_id=person, text=member["name"], lang="de", kind="official", is_preferred=True)
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                created += 1
            affiliation = {
                parties[party]
                for party, since, until in member["parties"]
                if party in parties
                and (since is None or date.fromisoformat(since[:10]) <= ELECTION)
                and (until is None or date.fromisoformat(until[:10]) >= ELECTION)
            }
            for start, row in spans:
                begin = date.fromisoformat(start[:10])
                until = date.fromisoformat(row["end"][:10]) if "end" in row else None
                tenure = cs.key(f"{SOURCE}.tenure", f"{item}:{start[:10]}")
                contest = None
                if begin == TERM_START and "districtName" in row:
                    place = fold(re.sub(r"^constituency for the Bundestag election\s*", "", row["districtName"]))
                    found = districts.get(place, [])
                    if len(found) != 1:
                        reasons["district does not resolve to one Wahlkreis"] += 1
                        skipped += 1
                    else:
                        contest_id, option, winner = found[0]
                        if winner is None or winner not in affiliation:
                            reasons["party differs from the declared winner's"] += 1
                            skipped += 1
                        else:
                            contest = contest_id
                            cs.add(
                                "candidacy",
                                id=cs.key("de.candidacy", f"{SOURCE}:{contest_id}:{item}"),
                                person_id=person,
                                option_id=option,
                                role="head",
                                ballot_name=member["name"],
                                member_of_party_id=winner,
                            )
                            matched += 1
                cause = "term_end" if until == TERM_END else ENDED.get(row.get("cause", ""))
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=bundestag,
                    from_date=begin,
                    from_precision="day",
                    to_date=until,
                    to_precision="day",
                    acquired_via="elected" if begin == TERM_START else "list_replacement",
                    contest_id=contest,
                    ended_via=cause,
                )
                tenures += 1
                for party in sorted(affiliation, key=str) if until is None or until > begin else ():
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=party,
                        valid_from=begin,
                        valid_from_precision="day",
                        valid_to=until,
                        valid_to_precision="day",
                    )
        for line in review:
            print(line, file=sys.stderr)
        print(
            f"{tenures} tenures, {matched} constituency candidacies, {skipped} skipped {dict(reasons)}, {created} new people, {resolved} resolved to existing people, {len(review)} for review"
        )
        return cs.write()
