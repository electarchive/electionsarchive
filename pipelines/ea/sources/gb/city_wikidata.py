import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset
from ea.sources.wikidata_match import QUERY, _literal, _words
from ea.store import Connection, connect

SOURCE = "gb.city_wikidata"
VERSION = "1"
TITLE = "Positions held (P39) of people named like City of London councilmen and aldermen"
BATCH = 60
BODY = "a78f6e0c-3da5-5982-8c1d-d519d8457048"


def _targets(conn: Connection) -> dict[UUID, set[str]]:
    people = [
        person
        for (person,) in conn.execute(
            """
            select distinct cy.person_id
            from candidacy cy
            join contest_option co on co.id = cy.option_id
            join contest c on c.id = co.contest_id
            left join office o on o.id = c.office_id
            where (c.body_id = %s or o.polity_id = (select polity_id from body where id = %s))
              and not exists (select from external_id z where z.entity_id = cy.person_id and z.scheme = 'wikidata')
            """,
            (BODY, BODY),
        ).fetchall()
    ]
    targets: dict[UUID, set[str]] = defaultdict(set)
    for person, text in conn.execute(
        "select entity_id, text from name where entity_id = any(%s) and kind in ('official', 'english')", (people,)
    ).fetchall():
        targets[person].add(text)
    return targets


def _urls(targets: dict[UUID, set[str]]) -> list[str]:
    spellings = sorted({text for texts in targets.values() for text in texts})
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": QUERY.format(
                    names=" ".join(_literal(text, "en") for text in spellings[start : start + BATCH])
                ),
                "format": "json",
            }
        )
        for start in range(0, len(spellings), BATCH)
    ]


def _city_office(label: str) -> bool:
    words = _words(label)
    if "london" not in words:
        return False
    if "city" in words:
        return bool(words & {"alderman", "aldermen", "councillor", "councilman", "council"})
    return words >= {"lord", "mayor"} and not words & {"greater", "borough", "deputy"}


def fetch() -> None:
    with connect() as conn:
        urls = _urls(_targets(conn))
    failed = 0
    for url in urls:
        try:
            raw.fetch(SOURCE, url, suffix=".json")
        except httpx.HTTPError:
            failed += 1
    print(f"{len(urls) - failed} of {len(urls)} queries fetched; rerun to retry the rest")


def emit() -> Path:
    with connect() as conn:
        targets = _targets(conn)
        urls = _urls(targets)
        found: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        fetched: list[str] = []
        for url in urls:
            artifact = raw.cached(SOURCE, url)
            fetched.append(url)
            for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
                if _city_office(binding["posLabel"]["value"]):
                    found[binding["name"]["value"]][binding["item"]["value"].rsplit("/", 1)[1]].add(
                        binding["posLabel"]["value"]
                    )
        held = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'wikidata'"
            ).fetchall()
        }
        matches: dict[UUID, set[str]] = {}
        for person, texts in targets.items():
            items = {item for text in texts for item in found.get(text, {})}
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
                "Wikidata items for City of London councilmen and aldermen who have none, where the item's label is "
                "the person's name and it holds a City of London council, aldermanic, or Lord Mayor position (P39)"
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
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in fetched:
            cs.artifact(raw.cached(SOURCE, url), source)
        linked = skipped = 0
        for person, items in sorted(matches.items()):
            item = next(iter(items))
            if len(items) > 1 or len(claimants[item]) > 1 or item in held:
                skipped += 1
                print(f"skipped\t{person}\t{sorted(items)}\t{sorted(targets[person])}")
                continue
            cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
            linked += 1
            print(f"linked\t{person}\t{item}\t{sorted(targets[person])}")
        print(f"{len(targets)} people without an item: {linked} linked, {skipped} skipped")
        return cs.write()
