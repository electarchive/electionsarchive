from datetime import timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.medsl_precincts"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
PRESIDENT = ("13730901", "XDJYKC")
PRESIDENT_TITLE = "U.S. President Precinct-Level Returns 2024"
METHOD = (
    "Precinct returns as compiled by the MIT Election Data and Science Lab from the states' and counties' official "
    "results. Absentee, early, and provisional ballots that a county reported only countywide stay in their own "
    "countywide rows, so those votes are not spread among the precincts. Precincts take their shape from the "
    "state's or county's official precinct file where one matches the precinct's county and code or name; "
    "the others are placed within their county."
)

LINES = {
    "democratic": ("Democratic", "democratic-party"),
    "democrat": ("Democratic", "democratic-party"),
    "republican": ("Republican", "republican-party"),
    "conservative": ("Conservative", "NY:conservative-party"),
    "constitution": ("Conservative", "NY:conservative-party"),
    "working families": ("Working Families", "NY:working-families-party"),
    "independence": ("Independence", "NY:independence-party"),
    "independent": ("Independence", "NY:independence-party"),
    "women's equality": ("Women's Equality", "NY:women-s-equality-party"),
    "reform": ("Reform", "NY:reform-party"),
    "green": ("Green", "green-party"),
    "libertarian": ("Libertarian", "libertarian-party"),
    "people's choice": ("People's Choice", "NY:people-s-choice"),
    "upstate jobs": ("Upstate Jobs", "NY:upstate-jobs"),
    "harris bhatti": ("Haris Bhatti", "NY:haris-bhatti"),
    "blue lives matter": ("Blue Lives Matter", "NY:blue-lives-matter"),
    "transparent government": ("Transparent Government", "NY:transparent-govt"),
    "stop the iran deal": ("Stop the Iran Deal", "NY:stop-the-iran-deal"),
    "friends of diane neal": ("Friends of Diane Neal", "NY:friends-of-diane-neal"),
    "frd": ("Friends of Diane Neal", "NY:friends-of-diane-neal"),
    "fri": ("Friends of Diane Neal", "NY:friends-of-diane-neal"),
}

RESTAGED = [
    "AL",
    "AZ",
    "FL",
    "GA",
    "ID",
    "IL",
    "IN",
    "KS",
    "LA",
    "MI",
    "MO",
    "MS",
    "NC",
    "NE",
    "NJ",
    "NM",
    "NV",
    "NY",
    "OH",
    "OR",
    "PA",
    "SC",
    "TN",
    "TX",
    "UT",
    "VA",
]


def fetch() -> None:
    medsl.fetch()
    try:
        raw.cached(medsl.SOURCE, medsl.DATAVERSE + PRESIDENT[0])
    except LookupError:
        raw.fetch(medsl.SOURCE, medsl.DATAVERSE + PRESIDENT[0], suffix=".csv")


def parse() -> None:
    congress = pl.read_parquet(medsl.PARSED / "precincts.parquet").filter(
        (pl.col("chamber") == "house") | (pl.col("year") >= 2022)
    )
    president = medsl.precincts({("president", 2024): raw.cached(medsl.SOURCE, medsl.DATAVERSE + PRESIDENT[0]).path})
    matched = [
        pl.col("jurisdiction").str.strip_chars().str.to_uppercase().alias("match_jurisdiction"),
        pl.col("precinct").str.strip_chars().str.to_uppercase().alias("match_precinct"),
    ]
    districts = (
        congress.filter((pl.col("year") == 2024) & (pl.col("chamber") == "house") & pl.col("state").is_in(["ME", "NE"]))
        .with_columns(matched)
        .group_by("state", "code", "match_jurisdiction", "match_precinct")
        .agg(pl.col("district").unique())
        .filter(pl.col("district").list.len() == 1)
        .select("state", "code", "match_jurisdiction", "match_precinct", pl.col("district").list.first())
    )
    counties = (
        districts.group_by("state", "code")
        .agg(pl.col("district").unique())
        .filter(pl.col("district").list.len() == 1)
        .select("state", "code", county_district=pl.col("district").list.first())
    )
    split = (
        president.filter(pl.col("state").is_in(["ME", "NE"]))
        .drop("district")
        .with_columns(matched)
        .join(districts, on=["state", "code", "match_jurisdiction", "match_precinct"], how="left")
        .join(counties, on=["state", "code"], how="left")
        .with_columns(district=pl.coalesce("district", "county_district"))
        .drop("county_district", "match_jurisdiction", "match_precinct")
    )
    if split["district"].null_count():
        print(
            f"{split.filter(pl.col('district').is_null())['votes'].sum()} ME and NE presidential votes with no district"
        )
    frame = (
        pl.concat([congress, president, split.filter(pl.col("district").is_not_null())], how="diagonal_relaxed")
        .with_columns(
            precinct=pl.col("precinct").str.strip_chars(),
            jurisdiction=pl.col("jurisdiction").str.strip_chars(),
            line=pl.when(
                (pl.col("state") == "NY") & (pl.col("chamber") == "house") & pl.col("year").is_in([2016, 2018])
            ).then(
                pl.col("party")
                .str.to_lowercase()
                .replace_strict({label: line for label, (line, _) in LINES.items()}, default=None)
            ),
        )
        .with_columns(
            line=pl.when(
                (
                    pl.col("line").is_in(["Democratic", "Republican"])
                    & (pl.col("candidate").n_unique().over("year", "chamber", "state", "district", "fips", "line") > 1)
                )
                .any()
                .over("year", "chamber", "state", "district", "fips")
                .not_()
            ).then("line")
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
    frame.filter(pl.col("kind") == "precinct").group_by("code", "day").agg(
        pl.col("state").first(), pl.col("county").first()
    ).sort("code", "day").write_parquet(PARSED / "units.parquet")
    returns = frame.group_by(
        "year",
        "chamber",
        "day",
        "special",
        "state",
        "district",
        "kind",
        "code",
        "candidate",
        "line",
        "measure",
        maintain_order=True,
    ).agg(pl.col("writein").any(), pl.col("votes").sum())
    key = ["year", "chamber", "special", "state", "code"]
    returns = (
        returns.with_columns(
            district_votes=pl.col("votes").sum().over(*key, "district"),
            code_votes=pl.col("votes").sum().over(*key),
        )
        .filter(~((pl.col("kind") == "precinct") & (pl.col("district_votes") == 0) & (pl.col("code_votes") > 0)))
        .drop("district_votes", "code_votes")
    )
    returns.write_parquet(PARSED / "returns.parquet")
    print(f"{frame.filter(pl.col('kind') == 'precinct')['code'].n_unique()} precincts")


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet").filter(pl.col("state").is_in(RESTAGED))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House results by precinct 2016–2024, Senate results by precinct 2022–2024, and presidential results by precinct 2024, from the MIT Election Data and Science Lab",
            polity="US",
        )
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        areas: dict[str, str] = {
            code: str(area)
            for code, area in conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct'"""
            ).fetchall()
        }
        shaped = {code: areas[code] for code in units["code"] if code in areas} | {
            unit: areas[area]
            for links in ("us.precinct_shapes", "us.cook_precincts", "us.swdb_precincts", "us.held_precincts")
            for unit, area in pl.read_parquet(raw.parsed(links) / "links.parquet").iter_rows()
            if area in areas
        }
        missing = set(units["county"]) - set(counties)
        if missing:
            raise ValueError(f"no county units for {sorted(missing)}")
        existing = pl.DataFrame(
            conn.execute(
                """select u.code, u.valid_from from reporting_unit u join changeset c on c.id = u.changeset_id
                   where u.scheme_code = 'us.precinct' and u.code ~ '^20(16|18|20|22|24):' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall(),
            schema={"code": pl.String, "day": pl.Date},
            orient="row",
        )
        units = units.join(existing, on=["code", "day"], how="anti")
        cs.frame(
            "reporting_unit",
            units.select(
                id=pl.struct("code", "day").map_elements(
                    lambda row: str(cs.unit("us.precinct", row["code"], row["day"])), return_dtype=pl.String
                ),
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
            title = (
                f"U.S. {'Senate' if chamber == 'senate' else 'House of Representatives'} Precinct-Level Returns {year}"
            )
            sources[f"{chamber}:{year}"] = cs.source("MIT Election Data and Science Lab", title)
            if sources[f"{chamber}:{year}"] in cs.minted:
                raise ValueError(f"{title} must already be a source")
            cs.artifact(raw.cached(medsl.SOURCE, medsl.DATAVERSE + file), sources[f"{chamber}:{year}"])
        sources["president:2024"] = cs.source("MIT Election Data and Science Lab", PRESIDENT_TITLE)
        cs.add(
            "source",
            id=sources["president:2024"],
            kind="academic",
            publisher="MIT Election Data and Science Lab",
            title=PRESIDENT_TITLE,
            url=f"https://doi.org/10.7910/DVN/{PRESIDENT[1]}",
            license="CC0 1.0",
        )
        cs.artifact(raw.cached(medsl.SOURCE, medsl.DATAVERSE + PRESIDENT[0]), sources["president:2024"])
        parties = dict(
            conn.execute(
                "select value, entity_id::text from external_id where scheme = 'us.party' and value = any(%s)",
                [[party for _, party in LINES.values()]],
            ).fetchall()
        )
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "returns.parquet")
            .filter(pl.col("state").is_in(RESTAGED))
            .with_columns(
                source=pl.col("chamber") + ":" + pl.col("year").cast(pl.String),
                line_party=pl.col("line").replace_strict(
                    {line: parties.get(party) for line, party in LINES.values()}, default=None, return_dtype=pl.String
                ),
            ),
            sources,
            restage=True,
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
