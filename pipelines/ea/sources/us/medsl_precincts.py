from datetime import timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.medsl_precincts"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
METHOD = (
    "Precinct returns as compiled by the MIT Election Data and Science Lab from the states' and counties' official "
    "results. Absentee, early, and provisional ballots that a county reported only countywide stay in their own "
    "countywide rows, so those votes are not spread among the precincts. Precincts take their shape from the "
    "state's or county's official 2022 precinct file where one matches the precinct's county and code or name; "
    "the others are placed within their county."
)


def fetch() -> None:
    medsl.fetch()


def parse() -> None:
    frame = (
        pl.read_parquet(medsl.PARSED / "precincts.parquet")
        .with_columns(
            precinct=pl.col("precinct").str.strip_chars(),
            jurisdiction=pl.col("jurisdiction").str.strip_chars(),
        )
        .with_columns(
            kind=pl.when(pl.col("kind").is_in(["county", "place"]) & (pl.col("precinct") != ""))
            .then(pl.lit("precinct"))
            .otherwise("kind"),
        )
        .with_columns(
            county=pl.when(pl.col("kind") == "precinct").then("code"),
            code=pl.when(pl.col("kind") == "precinct")
            .then(pl.concat_str("year", "state", "code", "jurisdiction", "precinct", separator=":"))
            .otherwise("code"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("kind") == "precinct").group_by("code").agg(
        pl.col("state").first(), pl.col("county").first(), pl.col("day").min()
    ).sort("code").write_parquet(PARSED / "units.parquet")
    frame.group_by(
        "year",
        "chamber",
        "day",
        "special",
        "state",
        "district",
        "kind",
        "code",
        "candidate",
        "measure",
        maintain_order=True,
    ).agg(pl.col("writein").any(), pl.col("votes").sum()).write_parquet(PARSED / "congress.parquet")
    print(f"{frame.filter(pl.col('kind') == 'precinct')['code'].n_unique()} precincts")


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House and Senate results by precinct for 2022, from the MIT Election Data and Science Lab",
            polity="US",
        )
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        shaped: dict[str, str] = {
            code: str(area)
            for code, area in conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct' and c.code like '2022:%%'"""
            ).fetchall()
        }
        missing = set(units["county"]) - set(counties)
        if missing:
            raise ValueError(f"no county units for {sorted(missing)}")
        cs.frame(
            "reporting_unit",
            units.select(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String),
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.when(pl.col("code").is_in(list(shaped)))
                .then(pl.lit("equals"))
                .otherwise(pl.lit("within")),
                area_id=pl.col("code").replace_strict(
                    shaped,
                    default=pl.col("county").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                ),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        sources = {}
        for (chamber, year), (file, _) in medsl.PRECINCTS.items():
            if year != 2022:
                continue
            title = (
                f"U.S. {'Senate' if chamber == 'senate' else 'House of Representatives'} Precinct-Level Returns {year}"
            )
            sources[f"{chamber}:{year}"] = cs.source("MIT Election Data and Science Lab", title)
            if sources[f"{chamber}:{year}"] in cs.minted:
                raise ValueError(f"{title} must already be a source")
            cs.artifact(raw.cached(medsl.SOURCE, medsl.DATAVERSE + file), sources[f"{chamber}:{year}"])
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "congress.parquet").with_columns(
                source=pl.col("chamber") + ":" + pl.col("year").cast(pl.String)
            ),
            sources,
        )
        notes = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'methodology'""",
                [SOURCE],
            ).fetchall()
        )
        for result_set in {*notes, *(row["id"] for row in cs.rows.get("result_set", []))}:
            cs.add(
                "note",
                id=notes.get(result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD,
                valid_from_precision="day",
                valid_to_precision="day",
            )
        return cs.write()
