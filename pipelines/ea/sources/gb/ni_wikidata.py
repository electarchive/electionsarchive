import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.msp_wikidata import seat
from ea.sources.gb.ni_assembly import DAYS
from ea.sources.gb.ni_mlas import squash
from ea.store import DATA, connect

SOURCE = "gb.ni_wikidata"
VERSION = "1"
TITLE = "Members of the Northern Ireland Assembly (P39) on Wikidata"
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?name ?start ?districtLabel WHERE {
?pos wdt:P279* wd:Q3272410 . ?item p:P39 ?held . ?held ps:P39 ?pos .
OPTIONAL { ?held pq:P580 ?start } OPTIONAL { ?held pq:P768 ?district } OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = "en") }
SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }""",
        "format": "json",
    }
)
UNMATCHED = DATA / "tmp" / "uk-dev" / "ni_wikidata_unmatched.tsv"
ELECTIONS = sorted(DAYS.values())


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")


def emit() -> Path:
    offered: dict[str, set[tuple[date, str, str]]] = defaultdict(set)
    labels: dict[str, str] = {}
    for binding in json.loads(raw.cached(SOURCE, SERVICE).path.read_text(encoding="utf-8"))["results"]["bindings"]:
        start = binding.get("start", {}).get("value", "")[:10]
        if not start[:1].isdigit():
            continue
        if date.fromisoformat(start) < ELECTIONS[0]:
            continue
        item = binding["item"]["value"].rsplit("/", 1)[1]
        labels[item] = binding.get("name", {}).get("value", item)
        term = max(election for election in ELECTIONS if election <= date.fromisoformat(start))
        offered[item].add((term, binding.get("districtLabel", {}).get("value", ""), start))
    live = "not exists (select from entity_merge m where m.entity_id = x.entity_id)"
    with connect() as conn:
        held: dict[str, set[UUID]] = defaultdict(set)
        schemes: dict[UUID, set[str]] = defaultdict(set)
        for person, value in conn.execute(
            f"select x.entity_id, x.value from external_id x join person p on p.id = x.entity_id where x.scheme = 'wikidata' and {live}"
        ).fetchall():
            held[value].add(person)
            schemes[person].add(value)
        body = conn.execute(
            "select b.id from body b join name n on n.entity_id = b.id and n.is_preferred where n.text = 'Northern Ireland Assembly'"
        ).fetchone()[0]
        names: dict[UUID, set[str]] = defaultdict(set)
        for person, text in conn.execute(
            "select entity_id, text from name where entity_id in (select person_id from tenure where body_id = %s)",
            [body],
        ).fetchall():
            names[person].add(squash(text))
        holders: dict[tuple[date, tuple[str, ...]], set[UUID]] = defaultdict(set)
        for person, start, district in conn.execute(
            """select t.person_id, t.from_date, an.text from tenure t join seat s on s.id = t.seat_id
               join name an on an.entity_id = s.district_area_id and an.is_preferred where t.body_id = %s""",
            [body],
        ).fetchall():
            holders[(max(election for election in ELECTIONS if election <= start), seat(district))].add(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the Northern Ireland Assembly given the Wikidata item whose Assembly position matches their term, constituency, and surname",
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
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, SERVICE), source)
        unmatched: list[tuple[str, str]] = []
        linked = joined = 0
        surname = lambda label: squash(label.split(",")[0].split()[-1])
        for item, rows in sorted(offered.items()):
            found: set[UUID] = set()
            for term, district, _ in rows:
                pool = [
                    person
                    for person in holders.get((term, seat(district)), set())
                    if any(known.endswith(surname(labels[item])) for known in names[person])
                ]
                if len(pool) == 1:
                    found.add(pool[0])
            if len(found) != 1:
                unmatched.append((labels[item], f"{item}: {len(found)} members match its terms and constituencies"))
                continue
            person = next(iter(found))
            if schemes[person] - {item}:
                unmatched.append((labels[item], f"{item}: the member already has {sorted(schemes[person])}"))
                continue
            owners = held.get(item, set()) - {person}
            if len(owners) > 1:
                unmatched.append((labels[item], f"{item} is held by {len(owners)} people"))
            elif owners:
                owner = next(iter(owners))
                if owner in cs.survivors or person in cs.survivors:
                    unmatched.append((labels[item], f"{item}: merge already staged"))
                    continue
                cs.merge(owner, person)
                held[item].add(person)
                joined += 1
            elif item not in schemes[person]:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                held[item].add(person)
                linked += 1
        UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
        UNMATCHED.write_text("\n".join(f"{name}\t{why}" for name, why in sorted(unmatched)) + "\n", encoding="utf-8")
        print(
            f"{len(offered)} Wikidata members: {linked} linked, {joined} joined to an existing holder, {len(unmatched)} unmatched (listed in {UNMATCHED})"
        )
        return cs.write()
