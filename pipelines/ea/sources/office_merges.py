from pathlib import Path
from uuid import UUID, uuid5

from ea.changeset import NAMESPACE, Changeset
from ea.offices import TWINS
from ea.store import connect

SOURCE = "office_merges"
VERSION = "1"

SPLITS = {
    "OR:41041:commissioner-position-1": (
        "Lincoln County Commissioner, Position 1",
        "Lincoln County Commissioner, Position 2",
    ),
    "MO:29127:clerk-of-the-circuit-court-district-ii": (
        "Marion County Clerk of the Circuit Court, District II",
        "Marion County Clerk of the Circuit Court, District I",
    ),
}

NAMES = {
    "OR:41059:commissioner-position-1": "Umatilla County Commissioner, Position 1",
    "OR:41059:commissioner-position-2": "Umatilla County Commissioner, Position 2",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "One office recorded twice, by two pipelines that key it differently or by one whose key changed, "
                "merged into the record that carries the most contests, tenures, and governments; seats merged, or "
                "about to be merged, because they shared a name are kept apart under their full titles"
            ),
            polity=None,
            kind="correction",
        )

        def name(entity: UUID, text: str) -> None:
            stored = conn.execute(
                """select id, text, lang, script, kind, source_id from name
                   where entity_id = %s and lang = 'en' and valid_from is null and valid_to is null""",
                [entity],
            ).fetchall()
            for id, held, lang, script, kind, source in stored:
                cs.add(
                    "name",
                    id=id,
                    entity_id=entity,
                    text=held,
                    lang=lang,
                    script=script,
                    kind=kind,
                    source_id=source,
                    is_preferred=held == text,
                )
            if all(held != text for _, held, *_ in stored):
                cs.add(
                    "name",
                    id=None,
                    entity_id=entity,
                    text=text,
                    lang="en",
                    script=None,
                    kind="official",
                    source_id=None,
                    is_preferred=True,
                )

        for value, (split, kept) in SPLITS.items():
            office = uuid5(NAMESPACE, f"office_merges\x1f{value}")
            survivor, polity, kind, body = conn.execute(
                """select f.id, f.polity_id, f.kind::text, f.body_id from external_id x join office f on f.id = x.entity_id
                   where x.scheme = 'us.office' and x.value = %s""",
                [value],
            ).fetchone() or (None, None, None, None)
            if survivor is None:
                continue
            if survivor != office:
                cs.add("office", id=office, polity_id=polity, kind=kind, body_id=body)
                cs.add("external_id", entity_id=office, scheme="us.office", value=value)
                name(office, split)
                name(survivor, kept)
            moved = conn.execute(
                """select k.* from contest k join external_id x on x.entity_id = k.id and x.scheme = 'us.contest'
                   where regexp_replace(x.value, '^[0-9-]+:', '') = %s and k.office_id is distinct from %s""",
                [value, office],
            )
            columns = [column.name for column in moved.description or ()]
            for row in moved.fetchall():
                contest = dict(zip(columns, row, strict=True))
                del contest["changeset_id"], contest["recorded_from"]
                cs.add("contest", **contest | {"office_id": office})
        renamed = set()
        for value, text in NAMES.items():
            entity = cs.key("us.office", value)
            if entity not in cs.minted:
                renamed.add(entity)
                name(entity, text)
        groups = 0
        for ranked in conn.execute(TWINS).fetchall():
            members = [entity for entity in ranked[0] if entity not in renamed]
            if len(members) < 2:
                continue
            survivor, *duplicates = members
            groups += 1
            for duplicate in duplicates:
                cs.merge(duplicate, survivor)
        print(f"{groups} duplicate office groups")
        return cs.write()
