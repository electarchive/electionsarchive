from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "fr.lineage"
VERSION = "2"
API = "https://fr.wikipedia.org/w/api.php?"
NFP_PAGE = "Nouveau Front populaire"
SOCIALIST_PARTY = UUID("7957faec-7911-5ed2-ae79-fc05d2fe7940")
PARTI_SOCIALISTE = UUID("8788ac83-2ebc-584d-9382-d46868efd147")
NUPES = UUID("9ed0f330-8a0d-5913-8a2f-c13b9327399c")
NFP = UUID("01a13b8a-b313-74c1-b228-4bfb1f3e4ece")


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
    )


def fetch() -> None:
    raw.fetch(SOURCE, page(NFP_PAGE), suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="French party duplicates merged, and the New Popular Front as successor to NUPES",
            polity="FR",
        )
        if conn.execute("select from entity_merge where entity_id = %s", [SOCIALIST_PARTY]).fetchone() is None:
            cs.merge(SOCIALIST_PARTY, PARTI_SOCIALISTE)
        if conn.execute("select from name where entity_id = %s and lang = 'en'", [PARTI_SOCIALISTE]).fetchone() is None:
            cs.add(
                "name",
                entity_id=PARTI_SOCIALISTE,
                text="Socialist Party",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        if (
            conn.execute(
                "select from party_relation where from_party_id = %s and to_party_id = %s", [NFP, NUPES]
            ).fetchone()
            is None
        ):
            source = cs.source("Wikipedia (fr)", NFP_PAGE)
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="Wikipedia (fr)",
                title=NFP_PAGE,
                url=f"https://fr.wikipedia.org/wiki/{quote(NFP_PAGE.replace(' ', '_'))}",
                license="CC BY-SA 4.0",
            )
            cs.artifact(raw.cached(SOURCE, page(NFP_PAGE)), source)
            cs.add(
                "party_relation",
                from_party_id=NFP,
                to_party_id=NUPES,
                kind="successor_of",
                date=date(2024, 6, 10),
                date_precision="day",
                source_id=source,
            )
        return cs.write()
