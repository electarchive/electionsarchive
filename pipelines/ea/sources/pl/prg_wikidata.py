"""Wikidata items for the PRG voivodeships and gminy whose TERYT code (P1653) is missing on Wikidata.

`pl.prg` links each polity whose TERYT code appears on a Wikidata item through P1653. For the rest, mostly
urban-rural gminy whose items carry no code or one in another format, `prg_wikidata.tsv` lists the item
matched by label, the powiat it lies in (P131), and its class. This package adds those links to the
polities `pl.prg` already holds, and checks each item against a Wikidata query of its labels and classes.
"""

import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid5

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "pl.prg_wikidata"
KEY = f"{SOURCE}/links"
OVERRIDES = Path(__file__).with_name("prg_wikidata.tsv")
# The class each item must hold, by the TERYT code's length and last digit: voivodeship of Poland, and rural and
# urban-rural gmina.
CLASSES = {"voivodeship": "Q150093", "2": "Q3504085", "3": "Q3491915"}


def _rows() -> list[tuple[str, str]]:
    lines = OVERRIDES.read_text().splitlines()
    if lines[0] != "code\tqid":
        raise ValueError(f"{OVERRIDES.name} has header {lines[0]!r}")
    rows = [(code, qid) for code, qid in (line.split("\t") for line in lines[1:])]
    codes = [code for code, _ in rows]
    qids = [qid for _, qid in rows]
    if len(set(codes)) != len(codes) or len(set(qids)) != len(qids):
        raise ValueError(f"{OVERRIDES.name} repeats a code or an item")
    if any(len(code) not in (2, 7) or not code.isdigit() for code in codes):
        raise ValueError(f"{OVERRIDES.name} holds a code that is not a voivodeship or gmina TERYT code")
    return rows


def _query() -> str:
    values = " ".join(f"wd:{qid}" for _, qid in _rows())
    query = (
        "SELECT ?item ?label ?class ?code WHERE { VALUES ?item { " + values + " } "
        'OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label) = "pl") } '
        "OPTIONAL { ?item wdt:P31 ?class } OPTIONAL { ?item wdt:P1653 ?code } }"
    )
    return "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})


def _polity(code: str) -> str:
    # The ids pl.prg resolves: a voivodeship by its ISO code, a gmina by its TERYT code under pl.prg.polity.
    key = f"polity\x1fPL-{code}" if len(code) == 2 else f"external_id:pl.prg.polity\x1f{code}"
    return str(uuid5(NAMESPACE, key))


def fetch() -> None:
    raw.fetch(SOURCE, _query(), suffix=".json")


def parse() -> None:
    build().write()


def build() -> Package:
    rows = _rows()
    artifact = raw.cached(SOURCE, _query())
    seen: dict[str, dict[str, set[str]]] = {}
    for binding in json.loads(artifact.path.read_text())["results"]["bindings"]:
        item = seen.setdefault(binding["item"]["value"].rsplit("/", 1)[1], {"label": set(), "class": set()})
        # A TERYT code on the item, if any, is in another format (a 10-digit form, or the gmina's earlier type
        # digit), which is why pl.prg's exact match on P1653 missed it; the item's label and class decide.
        if "label" in binding:
            item["label"].add(binding["label"]["value"])
        if "class" in binding:
            item["class"].add(binding["class"]["value"].rsplit("/", 1)[1])
    p = Package(
        KEY,
        SOURCE,
        f"Wikidata items for {len(rows)} Polish voivodeships and gminy whose TERYT code is not on Wikidata",
    )
    source = p.id("source", "wikidata")
    p.add(
        "source",
        id=source,
        kind="wikidata",
        publisher="Wikidata",
        title="Wikidata items of Polish voivodeships and gminy without a TERYT code (P1653)",
        url="https://www.wikidata.org/wiki/Property:P1653",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        notes=(
            "Each item was matched to a PRG unit by its Polish label, the powiat it lies in (P131), and its class "
            "(voivodeship, or urban, rural, or urban-rural gmina); the query records each item's labels and classes."
        ),
    )
    p.artifact(artifact, source)
    for code, qid in rows:
        item = seen.get(qid)
        if item is None or not item["label"]:
            raise ValueError(f"{code}: {qid} is missing from Wikidata or has no Polish label")
        wanted = CLASSES["voivodeship" if len(code) == 2 else code[-1]]
        if wanted not in item["class"]:
            raise ValueError(f"{code}: {qid} is a {sorted(item['class'])}, not a {wanted}")
        polity = _polity(code)
        p.external.add(polity)
        p.add("external_id", entity_id=polity, scheme="wikidata", value=qid)
    return p


def emit() -> None:
    package.run("load", [KEY])
