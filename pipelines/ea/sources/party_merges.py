import csv
from collections import Counter
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import parties
from ea.changeset import Changeset
from ea.sources import wikidata
from ea.store import Connection, connect

SOURCE = "party_merges"
VERSION = "8"
SAME = Path(__file__).with_name("party_same.tsv")
APART = Path(__file__).with_name("party_apart.tsv")
COLUMNS = (
    "outcome",
    "reason",
    "polity",
    "name",
    "party",
    "survivor",
    "kind",
    "source",
    "wikidata",
    "elections",
    "years",
)


def _pairs(path: Path, one: str, two: str) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            {**entry, "pair": frozenset((entry[one], entry[two]))} for entry in csv.DictReader(handle, delimiter="\t")
        ]


def plan(conn: Connection) -> tuple[list[tuple[str, str]], list[tuple[object, ...]]]:
    busy = {
        pipeline
        for (pipeline,) in conn.execute(
            "select distinct pipeline from changeset where status in ('draft', 'pending_review')"
        ).fetchall()
    }
    related = {
        frozenset((str(a), str(b)))
        for a, b in conn.execute(
            """select coalesce(x.into_id, r.from_party_id), coalesce(y.into_id, r.to_party_id) from party_relation r
               left join entity_merge x on x.entity_id = r.from_party_id
               left join entity_merge y on y.entity_id = r.to_party_id where r.to_party_id is not null"""
        ).fetchall()
    }
    apart = {entry["pair"] for entry in _pairs(APART, "one", "two")}
    found = parties.groups(parties.live(conn))
    parties.place(conn, [p for members in found for p in members])
    spans = wikidata.spans()
    labels = dict(
        conn.execute(
            """select distinct on (entity_id) entity_id::text, text from name where entity_id = any(%s)
               order by entity_id, is_preferred desc, lang <> 'en'""",
            [[UUID(p.id) for members in found for p in members]],
        ).fetchall()
    )
    merges: list[tuple[str, str]] = []
    rows: list[tuple[object, ...]] = []
    for members in found:
        outcome, reason = parties.judge(members, spans, related, apart)
        if outcome == "merge" and any(p.pipeline in busy for p in members):
            outcome, reason = "next run", "a member's pipeline has a changeset waiting"
        into = parties.survivor(members)
        if outcome == "merge":
            merges += [(p.id, into.id) for p in members if p is not into]
        for p in members:
            years = f"{p.first}-{p.last}" if p.first is not None else ""
            rows.append(
                (
                    outcome,
                    reason,
                    p.polity,
                    labels.get(p.id, ""),
                    p.id,
                    into.id,
                    p.kind,
                    p.unit,
                    ",".join(sorted(p.wikidata)),
                    p.elections,
                    years,
                )
            )
    return merges, rows


def emit() -> Path:
    with connect() as conn:
        conn.execute("set statement_timeout = 0")
        merges, rows = plan(conn)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "One party recorded by several sources or packages, merged into the record that carries its Wikidata "
                "item or the most elections, where the same name in the same polity meets every rule in "
                "docs/pipelines.md (Parties), plus the pairs reviewed by hand in party_same.tsv"
            ),
            polity=None,
            kind="match",
        )
        for duplicate, survivor in merges:
            cs.merge(UUID(duplicate), UUID(survivor))
        for entry in _pairs(SAME, "duplicate", "survivor"):
            duplicate, survivor = UUID(entry["duplicate"]), UUID(entry["survivor"])
            if cs.survivors.get(duplicate, duplicate) != cs.survivors.get(survivor, survivor):
                cs.merge(duplicate, survivor)
                rows.append(("merged by hand", entry["evidence"], "", entry["name"], str(duplicate), str(survivor)))
        print(
            f"{len(merges)} parties merged by rule; groups by outcome: "
            f"{dict(Counter(row[0] for row in rows if row[4] == row[5]))}"
        )
        folder = cs.write()
        pl.DataFrame(
            [tuple(str(value) for value in row) + ("",) * (len(COLUMNS) - len(row)) for row in rows],
            schema=dict.fromkeys(COLUMNS, pl.String),
            orient="row",
        ).write_csv(folder / "groups.tsv.gz", separator="\t", compression="gzip", check_extension=False)
        return folder
