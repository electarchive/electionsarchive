import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.fr.match import FOLD, region_departements, words
from ea.sources.fr.regionales import OLD_REGIONS
from ea.store import DATA, connect

SOURCE = "fr.wikidata"
VERSION = "1"
TITLE = "Regional councillors and presidents of regional councils in France (P39)"
REGIONS = {"Q1137": "41", "Q1142": "42", "Q16394": "43", "Q2518570": "82"}
QUERY = """SELECT ?item ?jur ?reg ?dep ?name WHERE {
?pos wdt:P279* wd:Q16886136 . ?item p:P39 ?s ; wdt:P31 wd:Q5 . ?s ps:P39 ?pos .
OPTIONAL { ?pos wdt:P1001 ?pj } OPTIONAL { ?s pq:P1001|pq:P768|pq:P642 ?qj } BIND(COALESCE(?qj, ?pj) AS ?jur)
FILTER(BOUND(?jur)) OPTIONAL { ?jur wdt:P2585 ?reg } OPTIONAL { ?jur wdt:P2586 ?dep }
?item rdfs:label|skos:altLabel ?name . FILTER(LANG(?name) IN ("fr", "en", "mul")) }"""
URL = "https://query.wikidata.org/sparql?" + urlencode({"query": QUERY, "format": "json"})


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".json")


def _departements(jur: str, reg: str, dep: str) -> set[str]:
    reg = REGIONS.get(jur, reg)
    if dep:
        return {"2A", "2B"} if dep == "20" else {dep}
    if reg in OLD_REGIONS:
        return set(OLD_REGIONS[reg][1].split())
    return set()


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    found: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
        item = binding["item"]["value"].rsplit("/", 1)[1]
        jur = binding["jur"]["value"].rsplit("/", 1)[1]
        area = _departements(jur, binding.get("reg", {}).get("value", ""), binding.get("dep", {}).get("value", ""))
        found[" ".join(words(binding["name"]["value"]))][item] |= area
    with connect() as conn:
        heads: dict[UUID, set[str]] = defaultdict(set)
        for person, scheme, region in conn.execute(
            """select distinct cy.person_id, a.scheme_code, ac.code from candidacy cy
               join changeset c on c.id = cy.changeset_id and c.pipeline = 'fr.regionales'
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join area a on a.id = k.district_area_id
               join area_code ac on ac.area_id = a.id
               where not exists (select from external_id z where z.entity_id = cy.person_id and z.scheme = 'wikidata')"""
        ).fetchall():
            heads[person] |= region_departements(scheme, region)
        texts: dict[UUID, set[str]] = defaultdict(set)
        for person, text in conn.execute(
            f"select entity_id, {FOLD.format('text')} from name where entity_id = any(%s)", [list(heads)]
        ).fetchall():
            texts[person].add(text)
        held = dict(conn.execute("select value, entity_id from external_id where scheme = 'wikidata'").fetchall())
        matches: dict[UUID, set[str]] = {}
        for person, area in heads.items():
            items = {item for text in texts[person] for item, places in found.get(text, {}).items() if places & area}
            if items:
                matches[person] = items
        claimants: dict[str, set[UUID]] = defaultdict(set)
        for person, items in matches.items():
            for item in items:
                claimants[item].add(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Wikidata items for heads of list in the French regional elections whose item's label is their name "
                "and who held a seat on, or the presidency of, the regional council of a region they ran in; "
                "heads of list who take the same item, or whose item another person already holds, are merged"
            ),
            polity="FR",
            kind="match",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Q16886136",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(artifact, source)
        linked = ambiguous = 0
        joined: list[str] = []
        for person, items in sorted(matches.items()):
            item = next(iter(items))
            if len(items) > 1 or any(len(matches[other]) > 1 for other in claimants[item]):
                ambiguous += 1
                continue
            holder = held.get(item, min(claimants[item]))
            holder = cs.survivors.get(holder, holder)
            if holder == person:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                linked += 1
            elif person not in cs.survivors:
                cs.merge(person, holder)
                joined.append(f"{person}\t{holder}\t{item}\t{' | '.join(sorted(texts[person]))}\n")
        (DATA / "logs" / "fr_wikidata_joined.tsv").write_text("".join(joined), encoding="utf-8")
        print(
            f"{len(heads)} regional heads without an item, {len(found)} Wikidata names: "
            f"{linked} linked, {ambiguous} ambiguous, {len(joined)} merged into the person holding or taking the item"
        )
        return cs.write()
