from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "br.lineage"
VERSION = "1"
API = "https://pt.wikipedia.org/w/api.php?"
PL = UUID("01a13ba1-d3a6-7084-b7c4-4e61cf17fdcb")
PR = UUID("898c5d63-a24d-511c-8a0a-3a9628301986")
REPUBLICANOS = UUID("01a13ba1-d3a6-7084-b7c4-4e5670d90be6")
PRB = UUID("ab49dfc5-c1bd-5894-985b-272a9ccb0ca6")
MDB = UUID("01a13ba1-d3a6-7084-b7c4-4e5b69af0a9d")
PMDB = UUID("bc0a81e7-6f81-5d6d-ae14-17cac2b4156c")
DEM = UUID("047942f1-3645-53eb-b2af-e248d22ff95c")
PFL = UUID("ef31efea-21fb-54d5-83dd-ef0eab65db4c")
PSL = UUID("a32698f3-392d-5bad-882c-b48702ce4df4")
UNIAO = UUID("01a13ba1-d3a6-7084-b7c4-4e6cb40d9817")
RELATIONS = (
    ("Partido Liberal (2006)", PL, PR, "successor_of", date(2019, 5, 7), "day"),
    ("Republicanos (partido político)", REPUBLICANOS, PRB, "successor_of", date(2019, 8, 15), "day"),
    ("Movimento Democrático Brasileiro (1980)", MDB, PMDB, "successor_of", date(2017, 1, 1), "year"),
    ("Democratas (Brasil)", DEM, PFL, "successor_of", date(2007, 3, 28), "day"),
    ("União Brasil", DEM, UNIAO, "merged_into", date(2022, 2, 8), "day"),
    ("União Brasil", PSL, UNIAO, "merged_into", date(2022, 2, 8), "day"),
)


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
    )


def fetch() -> None:
    for title in dict.fromkeys(row[0] for row in RELATIONS):
        raw.fetch(SOURCE, page(title), suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Brazilian party lineage: PR renamed PL, PRB renamed Republicanos, PMDB renamed MDB, PFL renamed "
                "Democratas, and DEM and PSL merged into União Brasil"
            ),
            polity="BR",
        )
        sources = {}
        for title in dict.fromkeys(row[0] for row in RELATIONS):
            sources[title] = cs.source("Wikipedia (pt)", title)
            cs.add(
                "source",
                id=sources[title],
                kind="community",
                publisher="Wikipedia (pt)",
                title=title,
                url=f"https://pt.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                license="CC BY-SA 4.0",
            )
            cs.artifact(raw.cached(SOURCE, page(title)), sources[title])
        for title, origin, target, kind, day, precision in RELATIONS:
            cs.add(
                "party_relation",
                from_party_id=origin,
                to_party_id=target,
                kind=kind,
                date=day,
                date_precision=precision,
                source_id=sources[title],
            )
        return cs.write()
