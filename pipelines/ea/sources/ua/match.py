import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea.changeset import Changeset
from ea.sources.br.identity import components
from ea.store import connect

SOURCE = "ua.match"
VERSION = "1"
MOST = 200_000
LOOKALIKES = str.maketrans("aceiopxyk’ʼ`", "асеіорхук'''")
ROWS = """select cy.person_id::text, coalesce(cy.ballot_name, n.text), e.date_from, p.birth_date,
                 (select min(x.value) from external_id x where x.entity_id = cy.person_id and x.scheme = 'wikidata')
          from candidacy cy
          join changeset c on c.id = cy.changeset_id
          join person p on p.id = cy.person_id
          join contest_option o on o.id = cy.option_id
          join contest k on k.id = o.contest_id
          join election e on e.id = k.election_id
          left join lateral (select text from name where entity_id = cy.person_id and is_preferred limit 1) n on true
          where c.pipeline like 'ua.%' and p.birth_precision = 'day' and e.date_from is not null"""


def fold(name: str | None) -> str | None:
    """The words of a full name in Cyrillic, lowercased and sorted, or None for a name with an initial."""
    if name is None:
        return None
    words = re.sub(r"\s*-\s*", "-", name.lower().translate(LOOKALIKES)).split()
    if len(words) < 2 or any(len(word.strip(".")) < 2 or word.endswith(".") for word in words):
        return None
    return " ".join(sorted(words))


def plan(rows: pl.DataFrame) -> pl.DataFrame:
    """Group the persons who bear the same full name and the same birth date, survivor first.

    A group stays apart when two of its persons stood on the same day or hold two Wikidata items.
    """
    named = rows.with_columns(key=pl.col("name").map_elements(fold, return_dtype=pl.String)).drop_nulls("key")
    nodes = named.select("person").unique().sort("person").with_row_index("node")
    named = named.join(nodes, on="person")
    edges = (
        named.group_by("key", "birth")
        .agg(pl.col("node").unique().sort())
        .filter(pl.col("node").list.len() > 1)
        .select(pl.col("node").list.first().alias("root"), "node")
        .explode("node")
        .select("node", "root")
    )
    label = pl.Series("group", components(nodes.height, edges), pl.UInt32)
    labeled = named.with_columns(group=label.gather(named["node"]))
    crowded = (
        labeled.group_by("group", "day")
        .agg(pl.col("person").n_unique().alias("people"))
        .filter(pl.col("people") > 1)
        .select("group")
        .unique()
    )
    grouped = labeled.group_by("group").agg(
        persons=pl.col("person").n_unique(), items=pl.col("item").drop_nulls().n_unique()
    )
    kept = grouped.filter(pl.col("persons") > 1).join(crowded, on="group", how="anti").filter(pl.col("items") <= 1)
    print(
        f"candidacies {rows.height}, groups {grouped.filter(pl.col('persons') > 1).height}, kept {kept.height}, "
        f"stood on the same day {crowded.height}, two Wikidata items {grouped.filter(pl.col('items') > 1).height}"
    )
    return (
        labeled.join(kept.select("group"), on="group")
        .with_columns(linked=pl.col("item").is_not_null())
        .group_by("group", "person")
        .agg(
            pl.col("linked").any(),
            first=pl.col("day").min(),
            name=pl.col("name").first(),
            birth=pl.col("birth").first(),
        )
        .sort("group", "linked", "first", "person", descending=[False, True, False, False])
    )


def emit() -> Path:
    with connect() as conn:
        conn.execute("set statement_timeout = 0")
        rows = pl.DataFrame(
            conn.execute(ROWS).fetchall(),
            schema={"person": pl.String, "name": pl.String, "day": pl.Date, "birth": pl.Date, "item": pl.String},
            orient="row",
        )
        groups = plan(rows)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ukrainian candidates merged when they bear the same full name and the same birth date",
            polity="UA",
            kind="match",
        )
        merges = []
        for group in groups.partition_by("group", maintain_order=True):
            if len(merges) + group.height - 1 > MOST:
                break
            survivor, *duplicates = group["person"].to_list()
            for duplicate in duplicates:
                cs.merge(UUID(duplicate), UUID(survivor))
                merges.append((duplicate, survivor, group["name"][0], group["birth"][0]))
        print(f"merged {len(merges)} of {groups.height - groups['group'].n_unique()}")
        folder = cs.write()
        pl.DataFrame(merges, schema=["duplicate", "survivor", "name", "birth"], orient="row").write_csv(
            folder / "merges.tsv.gz", separator="\t", compression="gzip", check_extension=False
        )
        return folder
