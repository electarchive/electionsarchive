from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import package
from ea.changeset import Changeset
from ea.sources.br.identity import components
from ea.sources.cz import volby_kv
from ea.sources.cz.volby import SOURCE as VOLBY
from ea.store import connect

SOURCE = "cz.match"
VERSION = "1"
NEAR = 12
MOST = 200_000


def candidates() -> pl.DataFrame:
    """Every valid municipal candidacy with its council, folded name, birth year from the stated age, and record id."""
    frames = []
    for year in sorted(volby_kv.MUNICIPAL):
        day = pl.col("DATUMVOLEB")
        frames.append(
            volby_kv._load(year)["kvrk"]
            .filter(pl.col("PLATNOST") == "A")
            .select(
                day=day,
                council=pl.col("KODZASTUP"),
                name=pl.concat_str(
                    pl.col("JMENO").str.strip_chars(), pl.col("PRIJMENI").str.strip_chars(), separator=" "
                ).str.to_lowercase(),
                born=day.str.slice(0, 4).cast(pl.Int32) - pl.col("VEK").cast(pl.Int32, strict=False),
                tag=pl.format(
                    "kv:{}-{}-{}:{}:{}:{}:{}",
                    day.str.slice(0, 4),
                    day.str.slice(4, 2),
                    day.str.slice(6, 2),
                    "KODZASTUP",
                    "COBVODU",
                    "POR_STR_HL",
                    "PORCISLO",
                ),
            )
        )
    return (
        pl.concat(frames)
        .drop_nulls(["born", "name"])
        .with_columns(
            candidacy=pl.col("tag").map_elements(
                lambda tag: str(uuid5(package.NAMESPACE, f"{VOLBY}\x1fcandidacy\x1f{tag}")),
                return_dtype=pl.String,
            )
        )
    )


def plan(rows: pl.DataFrame, items: dict[str, str]) -> pl.DataFrame:
    """Group the persons of one council who share a name and a birth year, survivor first.

    Two candidacies join when they bear the same full name in the same municipal council, at elections at most NEAR
    years apart, and their stated ages put their births within a year of each other. A name borne by two candidates
    of one council in one election joins nothing there. A group whose births span more than a year, or whose persons
    hold two Wikidata items, stays apart.
    """
    lone = rows.join(
        rows.group_by("council", "day", "name").agg(n=pl.len()).filter(pl.col("n") == 1).drop("n"),
        on=["council", "day", "name"],
    )
    nodes = lone.select("person").unique().sort("person").with_row_index("node")
    lone = lone.join(nodes, on="person")
    edges = (
        lone.join(lone, on=["council", "name"], suffix="_b")
        .filter(
            (pl.col("day") < pl.col("day_b"))
            & (pl.col("day_b").str.slice(0, 4).cast(pl.Int32) - pl.col("day").str.slice(0, 4).cast(pl.Int32) <= NEAR)
            & ((pl.col("born") - pl.col("born_b")).abs() <= 1)
            & (pl.col("node") != pl.col("node_b"))
        )
        .select("node", root="node_b")
    )
    label = pl.Series("group", components(nodes.height, edges), pl.UInt32)
    grouped = (
        lone.with_columns(group=label.gather(lone["node"]))
        .with_columns(item=pl.col("person").replace_strict(items, default=None))
        .group_by("group")
        .agg(
            persons=pl.col("person").n_unique(),
            span=pl.col("born").max() - pl.col("born").min(),
            items=pl.col("item").drop_nulls().n_unique(),
        )
    )
    kept = grouped.filter((pl.col("persons") > 1) & (pl.col("span") <= 1) & (pl.col("items") <= 1))
    print(
        f"candidacies {rows.height}, groups {grouped.filter(pl.col('persons') > 1).height}, "
        f"kept {kept.height}, births spanning over a year {grouped.filter(pl.col('span') > 1).height}, "
        f"two Wikidata items {grouped.filter(pl.col('items') > 1).height}"
    )
    return (
        lone.with_columns(group=label.gather(lone["node"]))
        .join(kept.select("group"), on="group")
        .with_columns(linked=pl.col("person").is_in(list(items)))
        .group_by("group", "person")
        .agg(
            pl.col("linked").first(),
            first=pl.col("day").min(),
            name=pl.col("name").first(),
            council=pl.col("council").first(),
        )
        .sort("group", "linked", "first", "person", descending=[False, True, False, False])
    )


def emit() -> Path:
    rows = candidates()
    with connect() as conn:
        conn.execute("set statement_timeout = 0")
        conn.execute("create temp table wanted (id uuid primary key) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy wanted (id) from stdin") as copy:
            for candidacy in rows["candidacy"].unique().to_list():
                copy.write_row((candidacy,))
        found = pl.DataFrame(
            conn.execute(
                "select c.id::text, c.person_id::text from wanted w join candidacy c on c.id = w.id"
            ).fetchall(),
            schema={"candidacy": pl.String, "person": pl.String},
            orient="row",
        )
        rows = rows.join(found, on="candidacy")
        items = dict(
            conn.execute(
                """select x.entity_id::text, min(x.value) from wanted w join candidacy c on c.id = w.id
                   join external_id x on x.entity_id = c.person_id and x.scheme = 'wikidata' group by 1"""
            ).fetchall()
        )
        groups = plan(rows, items)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Czech municipal candidates merged when they bear the same full name in the same council at "
                f"elections at most {NEAR} years apart and their stated ages put their births within a year"
            ),
            polity="CZ",
            kind="match",
        )
        merges = []
        for group in groups.partition_by("group", maintain_order=True):
            if len(merges) + group.height - 1 > MOST:
                break
            survivor, *duplicates = group["person"].to_list()
            for duplicate in duplicates:
                cs.merge(UUID(duplicate), UUID(survivor))
                merges.append((duplicate, survivor, group["name"][0], group["council"][0]))
        print(f"merged {len(merges)} of {groups.height - groups['group'].n_unique()}")
        folder = cs.write()
        pl.DataFrame(merges, schema=["duplicate", "survivor", "name", "council"], orient="row").write_csv(
            folder / "merges.tsv.gz", separator="\t", compression="gzip", check_extension=False
        )
        return folder
