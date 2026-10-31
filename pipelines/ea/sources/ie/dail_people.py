import itertools
from collections import defaultdict
from pathlib import Path
from uuid import UUID

import polars as pl

from ea.changeset import Changeset
from ea.sources.ie.dail import PARSED as DAIL
from ea.sources.ie.dail import fold
from ea.store import connect

SOURCE = "ie.dail_people"
VERSION = "1"


def emit() -> Path:
    roster = pl.read_parquet(DAIL / "roster.parquet")
    years = sorted(roster["year"].unique())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Dáil candidates joined across the 2011 to 2024 general elections on Oireachtas member codes, or on the same constituency, name, and party in consecutive elections",
            polity="IE",
            kind="match",
        )
        people: dict[tuple[int, str], UUID] = {}
        for year, key in roster.select("year", "person").iter_rows():
            people[year, key] = cs.key("ie.dail.person", key)
        parent: dict[UUID, UUID] = {}

        def find(person: UUID) -> UUID:
            while parent.setdefault(person, person) != person:
                parent[person] = parent[parent[person]]
                person = parent[person]
            return person

        by_code: dict[str, list[tuple[int, UUID]]] = defaultdict(list)
        for row in roster.filter(pl.col("code").is_not_null()).iter_rows(named=True):
            by_code[row["code"]].append((row["year"], people[row["year"], row["person"]]))
        for found in by_code.values():
            found.sort(key=lambda entry: entry[0])
            for (_, before), (_, after) in itertools.pairwise(found):
                parent[find(before)] = find(after)
        solo = roster.filter(pl.col("code").is_null()).with_columns(
            place=pl.col("constituency").map_elements(fold, return_dtype=pl.String),
            owner=pl.col("party").map_elements(fold, return_dtype=pl.String),
        )
        for before_year, after_year in itertools.pairwise(years):
            ahead = solo.filter(pl.col("year") == after_year)
            for row in solo.filter(pl.col("year") == before_year).iter_rows(named=True):
                keys = ["name", "place", "owner"]
                same = [r for r in ahead.iter_rows(named=True) if all(r[k] == row[k] for k in keys)]
                earlier = solo.filter(
                    (pl.col("year") == before_year)
                    & (pl.col("name") == row["name"])
                    & (pl.col("place") == row["place"])
                    & (pl.col("owner") == row["owner"])
                ).height
                if len(same) == 1 and earlier == 1:
                    parent[find(people[before_year, row["person"]])] = find(people[after_year, same[0]["person"]])
        merged = 0
        for person in list(parent):
            survivor = find(person)
            if survivor != person:
                cs.merge(person, survivor)
                merged += 1
        print(f"{merged} people merged")
        return cs.write()
