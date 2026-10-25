import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ea import names, raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "wikidata_people"
VERSION = "3"
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
    urls = _urls()
    failed = 0
    for url in urls:
        try:
            raw.fetch(SOURCE, url, suffix=".json")
        except httpx.HTTPError:
            failed += 1
    print(f"{len(urls) - failed} of {len(urls)} batches fetched; rerun to retry the rest")


def emit() -> Path:
    labels: dict[str, str] = {}
    artifacts: list[raw.Artifact] = []
    for url in _urls():
        try:
            artifact = raw.cached(SOURCE, url)
        except LookupError:
            continue
        artifacts.append(artifact)
        for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
            labels[binding["item"]["value"].rsplit("/", 1)[1]] = binding["label"]["value"]
    with connect() as conn:
        people: dict[str, list[tuple[str, list[str]]]] = {}
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
            people.setdefault(item, []).append((str(entity), texts or []))
        countries: dict[str, set[str]] = {}
        for entity, codes in conn.execute(
            """
            select cy.person_id, array_agg(distinct split_part(s.path, '/', 1))
            from candidacy cy
            join contest_option o on o.id = cy.option_id
            join contest c on c.id = o.contest_id
            join slug s on s.entity_id = c.election_id and s.is_current
            where exists (select from external_id x where x.entity_id = cy.person_id and x.scheme = 'wikidata')
            group by cy.person_id
            """
        ).fetchall():
            countries[str(entity)] = set(codes)
        stored = {
            (str(entity), text): name
            for name, entity, text in conn.execute(
                "select n.id, n.entity_id, n.text from name n join source s on s.id = n.source_id where s.title = %s",
                [TITLE],
            ).fetchall()
        }
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
        for artifact in artifacts:
            cs.artifact(artifact, source)
        added = 0
        held: list[tuple[str, str, str, str]] = []
        for item, label in labels.items():
            text = re.sub(
                r"^Sir |\s*\(.*?\)\s*$|,.*\b(Baron|Baroness|Baronet|Viscount|Viscountess|Earl|Countess|Marquess|Duke|Lord|Lady)\b.*$",
                "",
                label,
            ).strip()
            words = [
                word for word in re.findall(r"[^\s,.]+", text.lower()) if word not in {"jr", "sr", "ii", "iii", "iv"}
            ]
            if not words:
                continue
            for entity, texts in people.get(item, []):
                if (
                    not texts
                    or text in texts
                    or (
                        _fold(text) in {_fold(name) for name in texts}
                        and names.PARTICLE.sub(lambda match: match[0].lower(), text)
                        not in {names.PARTICLE.sub(lambda match: match[0].lower(), name) for name in texts}
                    )
                ):
                    continue
                codes = countries.get(entity, set())
                if "tr" in codes:
                    continue
                if not all("LATIN" in unicodedata.name(c, "") for c in text if c.isalpha()):
                    continue
                abroad = codes != {"us"}
                surnames = (
                    {word for name in texts for word in re.findall(r"[^\s,.]+", name.lower())}
                    if abroad
                    else {word for name in texts for word in _fold(name).split("-")}
                )
                if (words[-1] if abroad else _fold(words[-1])) not in surnames:
                    held.append((entity, item, text, " | ".join(sorted(set(texts)))))
                    continue
                cs.add(
                    "name",
                    id=stored.get((entity, text)),
                    entity_id=entity,
                    text=text,
                    lang="en",
                    script="Latn",
                    kind="english",
                    is_preferred=False,
                    source_id=source,
                )
                added += 1
        log = DATA / "logs" / "wikidata_people_held.tsv"
        log.write_text("".join("\t".join(row) + "\n" for row in sorted(held)), encoding="utf-8")
        print(f"{added} English names for {len(labels)} labeled items, {len(held)} held for a surname check in {log}")
        return cs.write()
