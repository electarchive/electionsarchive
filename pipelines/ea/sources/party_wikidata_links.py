"""Wikidata items for parties that won at least 1% of a legislative election's vote without a seat.

The `wikidata` pipeline links a party to an item by name, and it leaves a party out when no item scores well
enough, when two items tie, or when the party is an electoral committee or label rather than a party.
`party_wikidata_links.tsv` lists, for such parties, the item matched by hand from its label, a former name, or
the Wikipedia page that Party Facts cites for it, with the years the party stood. This package adds those links,
and checks each item against a Wikidata query of its labels, classes, country, and founding and dissolution
dates: the item must exist, sit in the party's country when it names one, and exist in the years the party stood.
No other party may hold the item, so the `partyfacts` pipeline can then match each party's Party Facts ID.
"""

import csv
import json
from pathlib import Path
from urllib.parse import urlencode

from ea import package, raw
from ea.package import Package

SOURCE = "party_wikidata_links"
KEY = f"{SOURCE}/links"
LINKS = Path(__file__).with_name("party_wikidata_links.tsv")
HEADER = ["party", "polity", "name", "years", "wikidata", "evidence"]
TITLE = "Wikidata items of parties without a seat, matched by hand"


def _rows() -> list[dict[str, str]]:
    with LINKS.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames != HEADER:
            raise ValueError(f"{LINKS.name} has header {reader.fieldnames!r}")
        rows = list(reader)
    for field in ("party", "wikidata"):
        values = [row[field] for row in rows]
        if len(set(values)) != len(values):
            raise ValueError(f"{LINKS.name} repeats a {field}")
    return rows


def _query() -> str:
    values = " ".join(f"wd:{row['wikidata']}" for row in _rows())
    query = (
        "SELECT ?item ?label ?class ?country ?inception ?dissolved WHERE { VALUES ?item { " + values + " } "
        "OPTIONAL { ?item rdfs:label ?label } OPTIONAL { ?item wdt:P31 ?class } "
        "OPTIONAL { ?item wdt:P17 ?place . ?place wdt:P297 ?country } "
        "OPTIONAL { ?item wdt:P571 ?inception } OPTIONAL { ?item wdt:P576 ?dissolved } }"
    )
    return "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})


def fetch() -> None:
    raw.fetch(SOURCE, _query(), suffix=".json")


def parse() -> None:
    build().write()


def _year(value: str) -> int | None:
    return int(value[:4]) if value[:4].isdigit() and value[4:5] == "-" else None


def build() -> Package:
    rows = _rows()
    artifact = raw.cached(SOURCE, _query())
    items: dict[str, dict[str, set[str]]] = {}
    for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
        item = items.setdefault(
            binding["item"]["value"].rsplit("/", 1)[1],
            {"label": set(), "class": set(), "country": set(), "inception": set(), "dissolved": set()},
        )
        for field, values in item.items():
            if field in binding:
                values.add(binding[field]["value"])
    p = Package(KEY, SOURCE, f"Wikidata items for {len(rows)} parties that won at least 1% of a vote without a seat")
    source = p.id("source", "wikidata")
    p.add(
        "source",
        id=source,
        kind="wikidata",
        publisher="Wikidata",
        title=TITLE,
        url="https://www.wikidata.org",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        notes=(
            "Each item was matched to a party by its label or a former name within the party's country, or through "
            "the Wikipedia page Party Facts cites for the party; the query records each item's labels, classes, "
            "country, and founding and dissolution dates."
        ),
    )
    p.artifact(artifact, source)
    for row in rows:
        qid = row["wikidata"]
        item = items.get(qid)
        if item is None or not item["label"] or not item["class"]:
            raise ValueError(f"{row['name']}: {qid} is missing from Wikidata, a redirect, or has no class")
        country = row["polity"].split("-")[0]
        if item["country"] and country not in item["country"]:
            raise ValueError(f"{row['name']}: {qid} lies in {sorted(item['country'])}, not {country}")
        first, last = (int(year) for year in row["years"].split("-"))
        began = min(filter(None, map(_year, item["inception"])), default=None)
        ended = max(filter(None, map(_year, item["dissolved"])), default=None)
        if (began is not None and began > last + 1) or (ended is not None and ended < first - 1):
            raise ValueError(f"{row['name']}: {qid} spans {began} to {ended}, outside {row['years']}")
        party = row["party"]
        p.external.add(party)
        p.add("external_id", entity_id=party, scheme="wikidata", value=qid)
        p.add(
            "citation",
            id=p.id("citation", party, qid),
            entity_id=party,
            source_id=source,
            locator={"item": qid, "matched": row["evidence"]},
        )
    return p


def emit() -> None:
    package.run("load", [KEY])
