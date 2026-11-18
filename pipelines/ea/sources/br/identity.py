"""Exact-key identities of Brazilian candidates, read from the voter titles and tax numbers in the TSE rosters."""

import polars as pl

from ea.store import DATA

TITLES = DATA / "parsed" / "br.tse_people" / "titles.parquet"
CANDIDACY = ["year", "place", "candidate"]


def components(nodes: int, edges: pl.DataFrame) -> list[int]:
    """Label each node with the smallest node it connects to through the `node` and `root` columns of `edges`."""
    parent = list(range(nodes))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for node, root in edges.iter_rows():
        first, second = find(node), find(root)
        if first != second:
            parent[max(first, second)] = min(first, second)
    return [find(node) for node in range(nodes)]


def identities(titles: pl.DataFrame) -> pl.DataFrame:
    """Join candidacies into one identity by exact keys only.

    Two candidacies are one person when they carry the same voter title and the same tax number, the same voter
    title and the same name and birth date, or the same tax number and the same birth date. A title the rosters give
    at most one tax number then joins all its candidacies when each year's candidacies under it are already one
    identity, so a title that two people share in one year stays apart.
    """
    nodes = titles.select(CANDIDACY).unique().sort(CANDIDACY).with_row_index("node")
    rows = titles.join(nodes, on=CANDIDACY)

    def edges(frame: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
        return (
            frame.drop_nulls(keys)
            .select("node", root=pl.col("node").min().over(keys))
            .filter(pl.col("node") != pl.col("root"))
        )

    exact = pl.concat(
        [edges(rows, ["title", "cpf"]), edges(rows, ["title", "name", "birth"]), edges(rows, ["cpf", "birth"])]
    )
    label = pl.Series("identity", components(nodes.height, exact), pl.UInt32)
    rows = rows.with_columns(identity=label.gather(rows["node"]))
    split = rows.group_by("title", "year").agg(pl.col("identity").n_unique()).filter(pl.col("identity") > 1)
    single = (
        rows.group_by("title")
        .agg(pl.col("cpf").drop_nulls().n_unique())
        .filter(pl.col("cpf") <= 1)
        .join(split, on="title", how="anti")
        .select("title")
    )
    title = edges(rows.join(single, on="title"), ["title"])
    label = pl.Series("identity", components(nodes.height, pl.concat([exact, title])), pl.UInt32)
    return nodes.with_columns(identity=label.gather(nodes["node"])).drop("node")


def unambiguous(titles: pl.DataFrame) -> pl.DataFrame:
    """Drop the candidacies the rosters give two voter titles, which no exact key can place."""
    ambiguous = titles.group_by(CANDIDACY).agg(pl.col("title").n_unique()).filter(pl.col("title") > 1)
    return titles.join(ambiguous.select(CANDIDACY), on=CANDIDACY, how="anti")


def namesakes() -> dict[tuple[int, str, str], str]:
    """Rekey the namesake candidacies of br.municipais and br.tse_history.

    Both pipelines key a person by the name and birth date, and br.municipais reuses br.tse_history's persons, so
    their keys are read together.
    """
    from ea.sources.br import municipais, tse_history

    if not TITLES.exists():
        raise FileNotFoundError(f"{TITLES} is missing; run `ea parse br.tse_people` first")
    return rekey(pl.concat([municipais.person_keys(), tse_history.person_keys()]), pl.read_parquet(TITLES))


def rekey(keys: pl.DataFrame, titles: pl.DataFrame) -> dict[tuple[int, str, str], str]:
    """Rekey the candidacies of a person key that two exact-key identities hold, when they cannot be one person.

    `keys` gives each candidacy (`year`, `place`, `candidate`) the person key its pipeline builds. Two identities
    under one key are different people when they carry different tax numbers or stood in the same year. Taking the
    identities in the order they first stood, each joins the first group it does not conflict with, preferring one
    that stood in the same place; an identity that conflicts with every group starts a new one. The first group keeps
    the key, and each other group's candidacies move to the key followed by the voter title of its first candidacy.
    So a title that was mistyped or reissued in a year whose roster withholds tax numbers stays with its person.
    Candidacies with no valid title stay on the key. Returns the new key of each moved candidacy.
    """
    titles = unambiguous(titles)
    placed = (
        keys.select(pl.col("year").cast(pl.Int32), pl.col("place").cast(pl.String), "candidate", "key")
        .unique(CANDIDACY)
        .join(identities(titles), on=CANDIDACY)
    )
    split = placed.group_by("key").agg(pl.col("identity").n_unique()).filter(pl.col("identity") > 1).select("key")
    stood = (
        placed.join(split, on="key")
        .join(titles.select(*CANDIDACY, "title", "cpf"), on=CANDIDACY)
        .sort("key", "year", "title")
        .group_by("key", "identity", maintain_order=True)
        .agg(
            pl.col("title").first(),
            cpfs=pl.col("cpf").drop_nulls().unique(),
            years=pl.col("year").unique(),
            places=pl.col("place").unique(),
        )
    )
    group: dict[tuple[str, int], str] = {}
    for (key,), identities_ in stood.partition_by("key", as_dict=True, maintain_order=True).items():
        groups: list[tuple[str, set[str], set[int], set[str]]] = []
        for identity, title, cpfs, years, places in identities_.select(
            "identity", "title", "cpfs", "years", "places"
        ).iter_rows():
            fits = [
                held
                for held in groups
                if not (held[1] and cpfs and held[1].isdisjoint(cpfs)) and held[2].isdisjoint(years)
            ]
            chosen = next((held for held in fits if not held[3].isdisjoint(places)), fits[0] if fits else None)
            if chosen is None:
                chosen = (str(key) if not groups else f"{key}|title:{title}", set(), set(), set())
                groups.append(chosen)
            chosen[1].update(cpfs)
            chosen[2].update(years)
            chosen[3].update(places)
            group[(str(key), identity)] = chosen[0]
    moved = placed.join(split, on="key").filter(
        pl.struct("key", "identity").map_elements(
            lambda row: group[(row["key"], row["identity"])] != row["key"], return_dtype=pl.Boolean
        )
    )
    return {
        (year, place, candidate): group[(key, identity)]
        for year, place, candidate, key, identity in moved.select(*CANDIDACY, "key", "identity").iter_rows()
    }
