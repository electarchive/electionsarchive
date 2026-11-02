import csv
from datetime import date
from functools import cache
from pathlib import Path
from uuid import UUID, uuid5

from ea.changeset import NAMESPACE, Changeset, slug
from ea.store import connect

SOURCE = "tr.names"
VERSION = "1"
RENAMES = Path(__file__).with_name("names.tsv")
LICENSE = "Laws and official texts may be freely reproduced (Law 5846 art. 31)."

Segment = tuple[date | None, date | None, str]


def instant(text: str, precision: str) -> date:
    return date.fromisoformat(f"{text}-01-01" if precision == "year" else text)


@cache
def rows() -> dict[int, list[dict[str, str]]]:
    by_plate: dict[int, list[dict[str, str]]] = {}
    with RENAMES.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            by_plate.setdefault(int(row["plate"]), []).append(row)
    for changes in by_plate.values():
        changes.sort(key=lambda row: instant(row["date"], row["precision"]))
    return by_plate


def current(plate: int) -> str:
    from ea.sources.tr.ysk import PROVINCES

    return PROVINCES[plate - 1]


def timeline(plate: int) -> list[Segment]:
    changes = rows().get(plate, [])
    if not changes:
        return [(None, None, current(plate))]
    starts = [instant(row["date"], row["precision"]) for row in changes]
    segments: list[Segment] = [(None, starts[0], changes[0]["old"])]
    for index, row in enumerate(changes):
        end = starts[index + 1] if index + 1 < len(changes) else None
        segments.append((starts[index], end, row["new"]))
    return segments


def province_name(plate: int, day: date | None = None) -> str:
    if day is None:
        return current(plate)
    for start, end, text in timeline(plate):
        if (start is None or start <= day) and (end is None or day < end):
            return text
    return current(plate)


def emit() -> Path:
    from ea.sources.licenses import UNLICENSED

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "The names Turkey's provinces carried before their current ones, with the law or decree that renamed "
                "each: dated name versions on the province areas and their municipality pages, and a renamed event "
                "for each change"
            ),
            polity="TR",
        )
        hubs = dict(conn.execute("select value, entity_id from external_id where scheme = 'tr.polity'").fetchall())
        added: set[UUID] = set()
        wanted: set[tuple[UUID, str, date | None, date | None]] = set()
        for plate, changes in sorted(rows().items()):
            present = current(plate)
            area = cs.area("tr.il", f"{plate:02}")
            hub = hubs.get(slug(present))
            targets = [area] if hub is None else [area, hub]
            sources: list[UUID] = []
            for row in changes:
                source = cs.source(row["publisher"], row["law"])
                sources.append(source)
                if source in cs.minted and source not in added:
                    added.add(source)
                    cs.add(
                        "source",
                        id=source,
                        kind=row["kind"],
                        publisher=row["publisher"],
                        title=row["law"],
                        url=row["url"],
                        license=LICENSE if row["kind"] == "official_body" else UNLICENSED,
                        license_url=None,
                        attribution=row["publisher"],
                        notes=row["note"] or None,
                    )
                day = instant(row["date"], row["precision"])
                event = uuid5(NAMESPACE, f"{SOURCE}.event\x1frenamed:{day}:{area}")
                cs.add(
                    "area_event",
                    id=event,
                    kind="renamed",
                    date=day,
                    date_precision=row["precision"],
                    source_id=source,
                    notes=f"{row['old']} renamed {row['new']}." + (f" {row['note']}" if row["note"] else ""),
                )
                cs.add("area_event_member", event_id=event, area_id=area, role="from")
                cs.add("area_event_member", event_id=event, area_id=area, role="to")
            versions = [(None, None, changes[0], changes[0]["old"], sources[0])]
            for index, row in enumerate(changes):
                versions.append(
                    (row, changes[index + 1] if index + 1 < len(changes) else None, row, row["new"], sources[index])
                )
            for start, end, row, text, source in versions:
                if text == present:
                    continue
                bound = end if start is not None else row
                begins = None if start is None else instant(start["date"], start["precision"])
                ends = None if bound is None else instant(bound["date"], bound["precision"])
                for entity in targets:
                    wanted.add((entity, text, begins, ends))
                    cs.add(
                        "name",
                        entity_id=entity,
                        text=text,
                        lang="tr",
                        kind="historical",
                        is_preferred=False,
                        valid_from=begins,
                        valid_from_precision="day" if start is None else start["precision"],
                        valid_to=ends,
                        valid_to_precision="day" if bound is None else bound["precision"],
                        source_id=source,
                    )
        for id, entity, text, begins, ends in conn.execute(
            """select n.id, n.entity_id, n.text, n.valid_from, n.valid_to from name n
               join changeset c on c.id = n.changeset_id where c.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if (entity, text, begins, ends) not in wanted:
                cs.add(
                    "name_drop",
                    name_id=id,
                    entity_id=entity,
                    text=text,
                    reason="Replaced by a dated name version with a sourced start.",
                )
        return cs.write()
