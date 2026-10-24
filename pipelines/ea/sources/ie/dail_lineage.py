from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea.changeset import NAMESPACE, Changeset
from ea.sources.ie.dail import ACTS, ELECTIONS
from ea.store import connect

SOURCE = "ie.dail_lineage"
VERSION = "1"
VINTAGES = ["2013", "2017", "2023"]
SLIVER = 0.0005
SAME = 0.95
LINKED = 0.05
MAJOR = 0.25


def emit() -> Path:
    signed = {str(act): day for _, act, day, _ in ELECTIONS.values()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Dáil constituency lineage and estimated crosswalks between the boundary sets of the 2013, 2017, and 2023 Acts, from the overlap of their official shapes",
            polity="IE",
        )
        sets = {
            vintage: id
            for id, vintage in conn.execute("select id, vintage from boundary_set where scheme_code = 'ie.dail'").fetchall()
        }
        conn.execute("create temp table side (plan text, id uuid, k int, label text, geom geometry) on commit drop")
        for vintage in VINTAGES:
            conn.execute(
                """insert into side
                   select %(vintage)s, a.id, a.k, n.text, st_makevalid(st_transform(g.geom, 2157))
                   from area a join area_code c on c.area_id = a.id and c.code like %(prefix)s
                   join area_geometry g on g.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code = 'ie.dail'""",
                {"vintage": vintage, "prefix": f"{vintage}:%"},
            )
        conn.execute("create index on side using gist (geom)")
        for earlier, later in pairwise(VINTAGES):
            overlaps = pl.DataFrame(
                conn.execute(
                    """select f.id::text, f.k, f.label, t.id::text, t.k, t.label,
                              st_area(st_intersection(f.geom, t.geom)) / st_area(f.geom),
                              st_area(st_intersection(f.geom, t.geom)) / st_area(t.geom)
                       from side f join side t on st_intersects(f.geom, t.geom)
                       where f.plan = %s and t.plan = %s""",
                    [earlier, later],
                ).fetchall(),
                schema=["from_id", "from_k", "from_label", "to_id", "to_k", "to_label", "of_from", "of_to"],
                orient="row",
            ).filter((pl.col("of_from") >= SLIVER) | (pl.col("of_to") >= SLIVER))
            for source, target, key, other, share in (
                (earlier, later, "from_k", "to_k", "of_from"),
                (later, earlier, "to_k", "from_k", "of_to"),
            ):
                weights = overlaps.select(
                    from_k=pl.col(key),
                    to_k=pl.col(other),
                    weight=pl.col(share) / pl.col(share).sum().over(key),
                ).filter(pl.col("weight") >= SLIVER)
                crosswalk = uuid5(NAMESPACE, f"{SOURCE}\x1f{sets[source]}\x1f{sets[target]}\x1f{VERSION}")
                cs.add(
                    "crosswalk",
                    id=crosswalk,
                    from_kind="boundary_set",
                    from_boundary_set_id=sets[source],
                    to_boundary_set_id=sets[target],
                    method="area",
                )
                cs.frame(
                    "crosswalk_weight",
                    weights.select(
                        crosswalk_id=pl.lit(str(crosswalk)),
                        from_k="from_k",
                        to_k="to_k",
                        weight=pl.col("weight").round(6).cast(pl.Decimal(10, 6)),
                        exact=pl.lit(False),
                    ),
                )
            linked = overlaps.filter((pl.col("of_from") >= LINKED) | (pl.col("of_to") >= LINKED))
            day = signed[later]
            for target in linked.select("to_id", "to_label").unique().sort("to_label").iter_rows(named=True):
                parts = linked.filter((pl.col("to_id") == target["to_id"]) & (pl.col("of_to") >= LINKED)).sort("of_to", descending=True)
                if parts.is_empty():
                    continue
                whole = parts.row(0, named=True)
                if (
                    parts.height == 1
                    and whole["of_from"] >= SAME
                    and whole["of_to"] >= SAME
                    and whole["from_label"] == target["to_label"]
                ):
                    continue
                if parts.height > 1 and parts.row(1, named=True)["of_to"] >= MAJOR:
                    kind = "merged"
                elif whole["from_label"] != target["to_label"] and whole["of_from"] >= SAME and whole["of_to"] >= SAME:
                    kind = "renamed"
                else:
                    kind = "boundary_changed"
                origins = ", ".join(f"{r['from_label']} ({r['of_to']:.0%})" for r in parts.iter_rows(named=True))
                event = uuid5(NAMESPACE, f"area_event\x1f{SOURCE}\x1f{later}\x1f{target['to_id']}")
                cs.add(
                    "area_event",
                    id=event,
                    kind=kind,
                    date=day,
                    notes=(
                        f"Under the {ACTS[int(later)]}, {target['to_label']} takes in {origins} of the {earlier} constituencies, "
                        f"by share of its area, and keeps {whole['of_from']:.0%} of the {whole['from_label']} area, "
                        "estimated from the overlap of the official boundary shapes."
                    ),
                )
                for r in parts.iter_rows(named=True):
                    cs.add("area_event_member", event_id=event, area_id=UUID(r["from_id"]), role="from")
                cs.add("area_event_member", event_id=event, area_id=UUID(target["to_id"]), role="to")
            for gone in (
                linked.select("from_id", "from_label").unique().sort("from_label").iter_rows(named=True)
            ):
                if linked.filter((pl.col("from_id") == gone["from_id"]) & (pl.col("of_from") >= LINKED)).is_empty():
                    continue
                heirs = linked.filter((pl.col("from_id") == gone["from_id"]) & (pl.col("of_from") >= LINKED)).sort("of_from", descending=True)
                if heirs.height < 2 or heirs.row(1, named=True)["of_from"] < MAJOR:
                    continue
                event = uuid5(NAMESPACE, f"area_event\x1f{SOURCE}\x1f{later}\x1fsplit\x1f{gone['from_id']}")
                cs.add(
                    "area_event",
                    id=event,
                    kind="split",
                    date=day,
                    notes=(
                        f"Under the {ACTS[int(later)]}, the {earlier} constituency of {gone['from_label']} passes to "
                        + ", ".join(f"{r['to_label']} ({r['of_from']:.0%})" for r in heirs.iter_rows(named=True))
                        + ", by share of its area, estimated from the overlap of the official boundary shapes."
                    ),
                )
                cs.add("area_event_member", event_id=event, area_id=UUID(gone["from_id"]), role="from")
                for r in heirs.iter_rows(named=True):
                    cs.add("area_event_member", event_id=event, area_id=UUID(r["to_id"]), role="to")
        return cs.write()
