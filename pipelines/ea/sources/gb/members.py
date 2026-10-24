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

SOURCE = "gb.members"
VERSION = "1"
TITLE = "Members of the Parliaments of the United Kingdom, with constituency and election (P39, P768, P2715)"
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?name ?pos ?start ?district ?districtLabel ?electedLabel ?mnis WHERE {
?pos wdt:P279 wd:Q16707842 . ?item p:P39 ?held . ?held ps:P39 ?pos .
OPTIONAL { ?held pq:P580 ?start } OPTIONAL { ?held pq:P768 ?district } OPTIONAL { ?held pq:P2715 ?elected }
OPTIONAL { ?item wdt:P10428 ?mnis } OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = "en") }
SERVICE wikibase:label { bd:serviceParam wikibase:language "en". ?district rdfs:label ?districtLabel .
?elected rdfs:label ?electedLabel . } }""",
        "format": "json",
    }
)
PEOPLE = {
    "Q8016": tuple(
        f"{unit}:churchill-winston-leonard-spencer"
        for unit in ("eng-oldham", "eng-manchester-north-west", "sct-dundee", "eng-leicester-west", "eng-epping")
    )
    + (
        "eng-woodford:churchill-winston-leonard-spencer",
        "eng-woodford:spencer-churchill-winston-leonard",
        "eng-woodford:churchill-sir-winston-leonard-spencer",
    ),
    "Q160852": tuple(
        f"{unit}:gladstone-william-ewart"
        for unit in (
            "eng-newark",
            "eng-manchester",
            "eng-oxford-university",
            "eng-south-lancashire",
            "eng-greenwich",
            "sct-midlothian",
            "eng-leeds",
            "sct-leith-burghs",
        )
    ),
    "Q181875": ("eng-tamworth:peel-robert@1788",),
    "Q193656": ("eng-south-hampshire:temple-henry-john", "eng-tiverton:temple-henry-john"),
    "Q82006": tuple(
        f"{unit}:disraeli-benjamin"
        for unit in ("eng-wycombe", "eng-maidstone", "eng-shrewsbury", "eng-buckinghamshire")
    ),
}
TITLES = {
    "sir",
    "lord",
    "dr",
    "hon",
    "rev",
    "captain",
    "major",
    "colonel",
    "general",
    "admiral",
    "viscount",
    "earl",
    "the",
    "of",
    "baron",
    "bt",
    "mp",
    "lieutenant",
    "commander",
    "dame",
    "lady",
}


def words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("'", "")
    return [word for word in re.findall(r"[a-z]+", plain) if word not in TITLES]


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")


def emit() -> Path:
    artifact = raw.cached(SOURCE, SERVICE)
    members: dict[tuple[str, str, str, str | None], dict[str, str]] = {}
    for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
        start = binding.get("start", {}).get("value", "")[:10]
        if not start[:1].isdigit():
            continue
        item = binding["item"]["value"].rsplit("/", 1)[1]
        pos = binding["pos"]["value"].rsplit("/", 1)[1]
        district = binding.get("district", {}).get("value")
        members[(item, pos, start, district)] = {
            "item": item,
            "pos": pos,
            "start": start,
            "district": binding.get("districtLabel", {}).get("value", ""),
            "name": binding.get("name", {}).get("value", ""),
            "elected": binding.get("electedLabel", {}).get("value", "").lower(),
        }
    live = "not exists (select from entity_merge m where m.entity_id = x.entity_id)"
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Candidates returned at UK general elections joined across constituencies and elections to the Wikidata "
                "member of that Parliament for their constituency; Sinn Féin in the UK joined to the Irish party record"
            ),
            polity="GB",
            kind="match",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Q16707842",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(artifact, source)
        commons = cs.key("gb.body", "house-of-commons")
        if commons in cs.minted:
            raise ValueError("the House of Commons must already exist")
        held: dict[UUID, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        owners: dict[str, set[UUID]] = defaultdict(set)
        for person, scheme, value in conn.execute(
            f"""select x.entity_id, x.scheme, x.value from external_id x join person p on p.id = x.entity_id
                where x.scheme in ('wikidata', 'mnis') and {live}"""
        ).fetchall():
            held[person][scheme].add(value)
            if scheme == "wikidata":
                owners[value].add(person)
        stood: dict[date, list[tuple[UUID, list[str], list[str], set[str], bool]]] = defaultdict(list)
        for day, place, person, ballot, elected in conn.execute(
            """select e.date_from, (select text from name where entity_id = c.id and is_preferred limit 1),
                      cy.person_id, cy.ballot_name,
                      exists (select from result_set rs join outcome x on x.result_set_id = rs.id
                              where rs.contest_id = c.id and rs.is_default and x.option_id = o.id and x.kind = 'elected')
               from contest c join election e on e.id = c.election_id
               join contest_option o on o.contest_id = c.id
               join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               where c.body_id = %s and e.kind = 'general'""",
            [commons],
        ).fetchall():
            ballot = ballot or ""
            surname, _, given = ballot.partition(",") if "," in ballot else (ballot.rsplit(" ", 1)[-1], "", ballot)
            place = set(words((place or "").partition(" in ")[2]))
            stood[day].append((person, words(surname), words(given), place, elected))
        days = sorted(stood)
        positions: dict[str, list[dict[str, str]]] = defaultdict(list)
        for member in members.values():
            positions[member["pos"]].append(member)
        found: dict[str, set[UUID]] = defaultdict(set)
        claims: dict[UUID, set[str]] = defaultdict(set)
        placed = unplaced = 0
        for chain in positions.values():
            modal = date.fromisoformat(Counter(member["start"] for member in chain).most_common(1)[0][0])
            near = [day for day in days if abs((day - modal).days) <= 40]
            if not near:
                continue
            day = min(near, key=lambda day: abs((day - modal).days))
            for member in chain:
                if (
                    date.fromisoformat(member["start"]) > modal + timedelta(days=45)
                    or "by-election" in member["elected"]
                ):
                    continue
                name = words(member["name"].split(",")[0])
                if not name:
                    continue
                label = set(words(member["district"]))
                hits = [
                    row for row in stood[day] if row[1] and (name[-len(row[1]) :] == row[1] or row[1][-1] == name[-1])
                ]

                scores = [len(label & row[3]) / max(len(label | row[3]), 1) for row in hits]
                if label and hits and max(scores) > 0:
                    hits = [row for row, score in zip(hits, scores, strict=True) if score == max(scores)]
                if len(hits) > 1:
                    hits = [row for row in hits if row[2] and row[2][0][:1] == name[0][:1]] or hits
                if len(hits) > 1:
                    hits = [row for row in hits if row[4]] or hits
                if len({row[0] for row in hits}) != 1:
                    unplaced += 1
                    continue
                person = hits[0][0]
                found[member["item"]].add(person)
                claims[person].add(member["item"])
                placed += 1
        merged = linked = conflicts = 0
        chosen: set[UUID] = set()
        for item, keys in PEOPLE.items():
            survivor = cs.key("wikidata", item)
            if survivor in cs.minted:
                raise ValueError(f"no one holds {item}")
            chosen.add(survivor)
            for key in keys:
                person = cs.key("gb.umit.person", key)
                if person in cs.minted:
                    raise ValueError(f"{key} is not a gb.umit person")
                chosen.add(person)
                if person != survivor:
                    cs.merge(person, survivor)
                    merged += 1
        for item, people in sorted(found.items()):
            group = people | owners.get(item, set())
            if item in PEOPLE or group & chosen:
                continue
            if any(len(claims[person]) > 1 or held[person]["wikidata"] - {item} for person in group):
                conflicts += 1
                continue
            if len(set().union(*(held[person]["mnis"] for person in group))) > 1:
                conflicts += 1
                continue
            if any(person in cs.survivors for person in group):
                conflicts += 1
                continue
            survivor = min(
                group,
                key=lambda person: (item not in held[person]["wikidata"], not held[person]["mnis"], str(person)),
            )
            if item not in held[survivor]["wikidata"]:
                cs.add("external_id", entity_id=survivor, scheme="wikidata", value=item)
                linked += 1
            for person in sorted(group - {survivor}, key=str):
                cs.merge(person, survivor)
                merged += 1
        sinn_fein = cs.key("gb.party", "sinn-fein")
        irish = cs.key("ie.party", "SF")
        if sinn_fein in cs.minted or irish in cs.minted:
            raise ValueError("both Sinn Féin records must already exist")
        if sinn_fein != irish:
            cs.merge(sinn_fein, irish)
        print(
            f"{placed} members placed, {unplaced} unplaced; {merged} records merged, {linked} given their Wikidata item, "
            f"{conflicts} items left apart"
        )
        return cs.write()
