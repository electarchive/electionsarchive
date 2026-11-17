import json
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset
from ea.sources.fr.match import FOLD, region_departements, words
from ea.sources.fr.regionales import OLD_REGIONS
from ea.store import DATA, connect

SOURCE = "fr.wikidata"
VERSION = "2"
TITLE = "Regional councillors and presidents of regional councils in France (P39)"
EXTRA_TITLE = "Deputies, senators, and mayors in France (P39)"
REGIONS = {"Q1137": "41", "Q1142": "42", "Q16394": "43", "Q2518570": "82"}
QUERY = """SELECT ?item ?jur ?reg ?dep ?name WHERE {
?pos wdt:P279* wd:Q16886136 . ?item p:P39 ?s ; wdt:P31 wd:Q5 . ?s ps:P39 ?pos .
OPTIONAL { ?pos wdt:P1001 ?pj } OPTIONAL { ?s pq:P1001|pq:P768|pq:P642 ?qj } BIND(COALESCE(?qj, ?pj) AS ?jur)
FILTER(BOUND(?jur)) OPTIONAL { ?jur wdt:P2585 ?reg } OPTIONAL { ?jur wdt:P2586 ?dep }
?item rdfs:label|skos:altLabel ?name . FILTER(LANG(?name) IN ("fr", "en", "mul")) }"""
# Deputies and senators who sat for a département, through a position tied to it ("député du Nord") or through the
# statement's electoral district, and mayors of a commune, each with the département it lies in and keeping only
# people alive in 1986, the year of the first regional election. The queries are split by route and, for mayors, by
# the first character of the commune's INSEE code, so each stays well under the endpoint's 60-second limit.
NAMED = """?item rdfs:label|skos:altLabel ?name . FILTER(LANG(?name) IN ("fr", "en", "mul"))
OPTIONAL { ?item wdt:P570 ?died } FILTER(!BOUND(?died) || ?died >= "1986-01-01"^^xsd:dateTime) }"""
POSITION = """SELECT DISTINCT ?item ?dep ?name WHERE {{
?pos wdt:P279* wd:{root} ; wdt:P1001 ?j . ?j wdt:P2586 ?dep . ?item p:P39/ps:P39 ?pos .
"""
DISTRICT = """SELECT DISTINCT ?item ?dep ?name WHERE {{
?item p:P39 ?s . ?s ps:P39 wd:{generic} ; pq:P768 ?j .
{{ ?j wdt:P2586 ?dep }} UNION {{ ?j wdt:P131 ?d . ?d wdt:P2586 ?dep }}
"""
MAYOR = """SELECT DISTINCT ?item ?insee ?name WHERE {{
{{ ?s ps:P39 wd:Q30185 ; pq:P642|pq:P1001 ?c }}
UNION {{ VALUES ?mayor {{ wd:Q30185 wd:Q382617 }} ?pos wdt:P279 ?mayor ; wdt:P1001 ?c . ?s ps:P39 ?pos }}
?c wdt:P374 ?insee . FILTER(STRSTARTS(?insee, "{digit}")) ?item p:P39 ?s .
"""
EXTRA = {
    "deputies-position": POSITION.format(root="Q3044918"),
    "deputies-district": DISTRICT.format(generic="Q3044918"),
    "senators-position": POSITION.format(root="Q24931583"),
    "senators-district": DISTRICT.format(generic="Q14828018"),
    **{f"mayors-{digit}": MAYOR.format(digit=digit) for digit in "0123456789"},
}
EXTRA = {key: query + NAMED for key, query in EXTRA.items()}


def _url(query: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})


URL = _url(QUERY)
EXTRA_URLS = {key: _url(query) for key, query in EXTRA.items()}


def _bindings(path: Path) -> list[dict[str, dict[str, str]]]:
    return json.loads(path.read_text(encoding="utf-8"))["results"]["bindings"]


def _fetch(url: str) -> bool:
    for attempt in range(6):
        try:
            raw.fetch(SOURCE, url, suffix=".json", check=_bindings)
            return True
        except (httpx.HTTPStatusError, httpx.TransportError, ValueError, KeyError) as error:
            print(f"{SOURCE}: query attempt {attempt + 1} failed: {error}"[:300], flush=True)
            time.sleep(70 * (attempt + 1))
    return False


def fetch() -> None:
    failed = [url for url in [URL, *EXTRA_URLS.values()] if not _fetch(url)]
    if failed:
        raise RuntimeError(f"{len(failed)} Wikidata queries failed or came back truncated; run the fetch again")


def _insee(code: str) -> set[str]:
    dep = code[:3] if code.startswith("97") else code[:2]
    return {"2A", "2B"} if dep == "20" else {dep}


def _departements(jur: str, reg: str, dep: str) -> set[str]:
    reg = REGIONS.get(jur, reg)
    if dep:
        return {"2A", "2B"} if dep == "20" else {dep}
    if reg in OLD_REGIONS:
        return set(OLD_REGIONS[reg][1].split())
    return set()


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    extra = {office: raw.cached(SOURCE, url) for office, url in EXTRA_URLS.items()}
    found: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for binding in _bindings(artifact.path):
        item = binding["item"]["value"].rsplit("/", 1)[1]
        jur = binding["jur"]["value"].rsplit("/", 1)[1]
        area = _departements(jur, binding.get("reg", {}).get("value", ""), binding.get("dep", {}).get("value", ""))
        found[" ".join(words(binding["name"]["value"]))][item] |= area
    regional = len(found)
    for key, cached in extra.items():
        for binding in _bindings(cached.path):
            item = binding["item"]["value"].rsplit("/", 1)[1]
            if key.startswith("mayors"):
                area = _insee(binding["insee"]["value"])
            else:
                dep = binding["dep"]["value"]
                area = {"2A", "2B"} if dep == "20" else {dep}
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
                "and who held a seat on, or the presidency of, the regional council of a region they ran in, or sat as a "
                "deputy or senator for, or was mayor of a commune in, a département of that region; "
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
        evidence = cs.source("Wikidata", EXTRA_TITLE)
        cs.add(
            "source",
            id=evidence,
            kind="wikidata",
            publisher="Wikidata",
            title=EXTRA_TITLE,
            url="https://www.wikidata.org/wiki/Q3044918",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for cached in extra.values():
            cs.artifact(cached, evidence)
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
            f"{len(heads)} regional heads without an item, {regional} regional and {len(found)} total Wikidata names: "
            f"{linked} linked, {ambiguous} ambiguous, {len(joined)} merged into the person holding or taking the item"
        )
        return cs.write()
