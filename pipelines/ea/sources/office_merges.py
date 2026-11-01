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


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "One office recorded twice, by two pipelines that key it differently or by one whose key changed, "
                "merged into the record that carries the most contests, tenures, and governments; two seats merged "
                "because they shared a name are split back apart"
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
            if survivor is None or survivor == office:
                continue
            cs.add("office", id=office, polity_id=polity, kind=kind, body_id=body)
            cs.add("external_id", entity_id=office, scheme="us.office", value=value)
            name(office, split)
            name(survivor, kept)
        groups = 0
        for ranked in conn.execute(TWINS).fetchall():
            survivor, *duplicates = ranked[0]
            groups += 1
            for duplicate in duplicates:
                cs.merge(duplicate, survivor)
        print(f"{groups} duplicate office groups")
        return cs.write()
