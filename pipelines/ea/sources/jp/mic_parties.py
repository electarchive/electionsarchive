from pathlib import Path

from ea.changeset import Changeset
from ea.sources.jp import mic
from ea.store import connect

SOURCE = "jp.mic_parties"
VERSION = "1"
SCHEME = f"{mic.SOURCE}.party"


def emit() -> Path:
    """Merge each party the ministry's results minted by name into the namesake party that holds a Wikidata item.

    A party's namesake is the party of Japan that carries a Wikidata item and a Japanese name equal to the
    ministry's label, in any period. A party named by several items, or by none, stays as it is.
    """
    with connect() as conn:
        minted = conn.execute(
            """select e.value, e.entity_id from external_id e
               where e.scheme = %s and not exists (select 1 from entity_merge m where m.entity_id = e.entity_id)""",
            [SCHEME],
        ).fetchall()
        holders: dict[str, set] = {}
        for text, entity in conn.execute(
            """select n.text, p.id from party p
               join polity y on y.id = p.polity_id
               join external_id w on w.entity_id = p.id and w.scheme = 'wikidata'
               join name n on n.entity_id = p.id and n.lang = 'ja'
               where y.iso_code = 'JP'
                 and not exists (select 1 from entity_merge m where m.entity_id = p.id)"""
        ):
            holders.setdefault(text, set()).add(entity)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Japanese parties minted from the Ministry of Internal Affairs and Communications results merged "
                "into the namesake parties that hold their Wikidata items"
            ),
            polity=None,
            kind="correction",
        )
        for text, entity in minted:
            found = holders.get(mic.party_label(text), set())
            if len(found) == 1:
                cs.merge(entity, next(iter(found)))
        return cs.write()
