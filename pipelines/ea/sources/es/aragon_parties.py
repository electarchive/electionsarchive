from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "es.aragon_parties"
VERSION = "1"
TITLE = "Wikidata items of the Aragonese party records"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
MERGES = {
    "33d804a6": "b2c2eac8",
    "093a1efc": "bf18cd7d",
    "7ff530ae": "c32a1183",
    "af699dd4": "c32a1183",
    "4ced101d": "c32a1183",
    "06faf766": "9c522dcf",
    "03fa4765": "9ba1a1e7",
    "e1c45e91": "a60a4258",
    "0c741870": "a60a4258",
    "f5fd89e9": "ffbf3435",
    "9cc99370": "5fd20900",
    "57501b98": "5fd20900",
}
ITEMS = {
    "c32a1183": "Q3572827",
    "5faf2bce": "Q3573249",
    "d19404c0": "Q77038417",
    "78d94b8e": "Q6063929",
    "a5a02482": "Q5857408",
    "8122c425": "Q3752709",
    "63f8453d": "Q62600823",
    "4f5d5854": "Q3099344",
    "25cfe8d4": "Q3322758",
    "3806cb89": "Q138035138",
    "99668350": "Q3327144",
    "4d5f9265": "Q9056341",
    "53c13c3e": "Q119686409",
    "a60a4258": "Q3752721",
}


def fetch() -> None:
    for item in sorted(set(ITEMS.values())):
        raw.fetch(SOURCE, ENTITY.format(item), suffix=".json")


def _full(conn, prefix: str) -> UUID:
    rows = conn.execute(
        """select p.id from party p join slug s on s.entity_id = p.polity_id and s.is_current
           where s.path = 'es/aragon' and p.id::text like %s""",
        [prefix + "%"],
    ).fetchall()
    assert len(rows) == 1, prefix
    return rows[0][0]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Aragonese party records of one party under several years' names merged (Partido Aragonés, "
                "Chunta Aragonesista, Izquierda Unida, Ciudadanos, Escaños en Blanco, Podemos, and the animalist "
                "party), and Aragon-only parties linked to their Wikidata items"
            ),
            polity=None,
            kind="correction",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for entity, into in MERGES.items():
            cs.merge(_full(conn, entity), _full(conn, into))
        taken = {value for (value,) in conn.execute("select value from external_id where scheme = 'wikidata'")}
        for prefix, item in ITEMS.items():
            if item in taken:
                continue
            party = _full(conn, prefix)
            cs.artifact(raw.cached(SOURCE, ENTITY.format(item)), source)
            cs.add("external_id", entity_id=party, scheme="wikidata", value=item)
            cs.add("citation", entity_id=party, source_id=source, locator={"item": item})
        return cs.write()
