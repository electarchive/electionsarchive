import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "wikidata_people"
VERSION = "1"
TITLE = "English labels of people"
LABELS = """SELECT ?item ?label WHERE {{ VALUES ?item {{ {items} }} ?item rdfs:label ?label . FILTER(LANG(?label) = "en") }}"""
CHUNK = 400


def _urls() -> list[str]:
    with connect() as conn:
        items = sorted(
            {
                value
                for (value,) in conn.execute(
                    "select x.value from external_id x join person p on p.id = x.entity_id where x.scheme = 'wikidata'"
                ).fetchall()
            },
            key=lambda item: int(item[1:]),
        )
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": LABELS.format(items=" ".join(f"wd:{item}" for item in items[start : start + CHUNK])),
                "format": "json",
            }
        )
        for start in range(0, len(items), CHUNK)
    ]


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "-".join(re.findall(r"[a-z0-9]+", "".join(c for c in folded if not unicodedata.combining(c))))


def fetch() -> None:
    for url in _urls():
        raw.fetch(SOURCE, url, suffix=".json")


def emit() -> Path:
    labels: dict[str, str] = {}
    urls = _urls()
    for url in urls:
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            labels[binding["item"]["value"].rsplit("/", 1)[1]] = binding["label"]["value"]
    with connect() as conn:
        people: dict[str, list[tuple[str, set[str]]]] = {}
        for entity, item, texts in conn.execute(
            """
            select p.id, x.value, array_agg(n.text) filter (where s.title is distinct from %s)
            from person p
            join external_id x on x.entity_id = p.id and x.scheme = 'wikidata'
            join name n on n.entity_id = p.id
            left join source s on s.id = n.source_id
            where not exists (select from entity_merge m where m.entity_id = p.id)
            group by p.id, x.value
            """,
            [TITLE],
        ).fetchall():
            people.setdefault(item, []).append((str(entity), {_fold(text) for text in texts or []}))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "English names from Wikidata for people whose archive name differs from their Wikidata label, "
                "so a person's common name finds them"
            ),
            polity=None,
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Help:Label",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in urls:
            cs.artifact(raw.cached(SOURCE, url), source)
        added = 0
        for item, label in labels.items():
            for entity, folded in people.get(item, []):
                if _fold(label) and _fold(label) not in folded:
                    cs.add(
                        "name",
                        entity_id=entity,
                        text=label,
                        lang="en",
                        kind="english",
                        is_preferred=False,
                        source_id=source,
                    )
                    added += 1
        print(f"{added} English names for {len(labels)} labeled items")
        return cs.write()
