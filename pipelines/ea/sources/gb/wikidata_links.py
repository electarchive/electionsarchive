import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.wikidata_links"
VERSION = "1"
TITLE = "UK Parliament member IDs (P10428) on Wikidata"
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?mnis ?label WHERE { ?item wdt:P10428 ?mnis .
OPTIONAL { ?item rdfs:label ?label FILTER(lang(?label) = "en") } }""",
        "format": "json",
    }
)

CANDIDACIES = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?day ?districtLabel WHERE { ?item p:P3602 ?held . ?held ps:P3602 ?election .
?election wdt:P31/wdt:P279* wd:Q15283424 ; wdt:P585 ?day .
OPTIONAL { ?held pq:P768 ?district . ?district rdfs:label ?districtLabel FILTER(lang(?districtLabel) = "en") } }""",
        "format": "json",
    }
)


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")
    raw.fetch(SOURCE, CANDIDACIES, suffix=".json")


def emit() -> Path:
    live = "not exists (select from entity_merge m where m.entity_id = x.entity_id)"
    with connect() as conn:
        held: dict[str, dict[str, set[UUID]]] = {"wikidata": defaultdict(set), "mnis": defaultdict(set)}
        schemes: dict[UUID, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for person, scheme, value in conn.execute(
            f"""select x.entity_id, x.scheme, x.value from external_id x join person p on p.id = x.entity_id
                where x.scheme in ('wikidata', 'mnis') and {live}"""
        ).fetchall():
            held[scheme][value].add(person)
            schemes[person][scheme].add(value)
        words: dict[UUID, set[str]] = defaultdict(set)
        for person, text in conn.execute(
            f"""select x.entity_id, n.text from external_id x join name n on n.entity_id = x.entity_id
                where x.scheme = 'mnis' and {live}"""
        ).fetchall():
            words[person] |= set(re.findall(r"[a-z]{2,}", text.lower()))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Members of the UK Parliament joined to the Wikidata person whose UK Parliament member ID matches theirs, "
                "where the surnames agree"
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
            url="https://www.wikidata.org/wiki/Property:P10428",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, SERVICE), source)
        numbers: dict[str, set[str]] = defaultdict(set)
        labels: dict[str, str] = {}
        for binding in json.loads(raw.cached(SOURCE, SERVICE).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            item = binding["item"]["value"].rsplit("/", 1)[1]
            numbers[item].add(binding["mnis"]["value"])
            labels[item] = binding.get("label", {}).get("value", "")
        joined = linked = conflicts = 0
        for item, values in sorted(numbers.items()):
            if len(values) != 1:
                conflicts += 1
                continue
            people = held["mnis"].get(next(iter(values)), set())
            surname = re.findall(r"[a-z]{2,}", re.sub(r"\(.*?\)|,.*", "", labels[item].lower()))[-1:]
            if len(people) != 1 or not surname or surname[0] not in words[next(iter(people))]:
                conflicts += bool(people)
                continue
            person = next(iter(people))
            owners = held["wikidata"].get(item, set())
            if schemes[person]["wikidata"] - {item} or len(owners) > 1:
                conflicts += 1
                continue
            if not owners:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                linked += 1
                continue
            owner = next(iter(owners))
            if owner == person:
                continue
            if schemes[owner]["mnis"] - schemes[person]["mnis"] or owner in cs.survivors or person in cs.survivors:
                conflicts += 1
                continue
            cs.merge(owner, person)
            joined += 1
        print(
            f"{joined} Wikidata records joined to their MP, {linked} MPs given their Wikidata item, {conflicts} left apart"
        )
        return cs.write()
