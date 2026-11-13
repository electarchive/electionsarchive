from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.id.kpu_pilpres import PROVINCES
from ea.store import DATA, connect

SOURCE = "id.sage_pilpres"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CACHE = "sage"
URL = "https://storage.googleapis.com/sage-archive/parquet/country=Indonesia/year=2019/part-0.parquet"
DOI = "https://doi.org/10.7910/DVN/YGJR1L"
DAY = date(2019, 4, 17)
CONTEST = f"{DAY}:presiden"
TICKETS = {"WIDODO, Joko": 1, "SUBIANTO, Prabowo": 2}
OFFICIAL = {1: 85607362, 2: 68650239}
NOTE = (
    "Votes for each ticket at {units:,} polling stations (tempat pemungutan suara, TPS) in the 34 provinces of 2019, "
    "from the 2019 Indonesia partition of SAGE (Small-Area Global Elections), which names the Komisi Pemilihan Umum "
    "as its upstream and gives no per-file URL or retrieval date. The stations' votes sum to {votes:,} against the "
    "{official:,} that KPU's certificate declares, so {missing:,} votes are missing, and there are no abroad "
    "stations. SAGE's registered voters (which sum to {registered:,}, against the certificate's 192,770,611) are not "
    "stored, and its valid-vote totals equal the two tickets' sum at every station, so none is added. Station "
    "codes are SAGE's own sequence numbers, not KPU identifiers. The names are SAGE's. Points are SAGE's geocodes "
    "of the village name, not surveyed locations, and are blank where SAGE found none. This is a supplement to the "
    "official provincial set, never the default, and carries no seats or winners."
)


def fetch() -> None:
    raw.fetch(CACHE, URL, suffix=".parquet")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.scan_parquet(raw.cached(CACHE, URL).path)
        .filter(pl.col("election_type") == "Presidential")
        .select(
            "NAME1", "NAME2", "NAME3", "NAME4", "NAME5", "party", "votes", "reg", "total_votes", "latitude", "longitude"
        )
        .collect()
    )
    if set(frame["party"].unique()) != set(TICKETS):
        raise ValueError(f"unexpected tickets {frame['party'].unique().to_list()}")
    stations = frame.group_by("NAME5").agg(
        province=pl.col("NAME1").first(),
        regency=pl.col("NAME2").first(),
        district=pl.col("NAME3").first(),
        village=pl.col("NAME4").first(),
        sum=pl.col("votes").sum(),
        total=pl.col("total_votes").first(),
        n=pl.len(),
        longitude=pl.col("longitude").first(),
        latitude=pl.col("latitude").first(),
    )
    if (stations["n"] != 2).any() or (stations["sum"] != stations["total"]).any():
        raise ValueError("every station needs both tickets and a total equal to their sum")
    unknown = set(stations["province"].str.to_uppercase()) - set(PROVINCES)
    if unknown:
        raise ValueError(f"unknown provinces {unknown}")
    frame.with_columns(ticket=pl.col("party").replace_strict(TICKETS)).write_parquet(PARSED / "votes.parquet")
    stations.write_parquet(PARSED / "stations.parquet")
    print(f"{stations.height} stations")


def emit() -> Path:
    votes = pl.read_parquet(PARSED / "votes.parquet")
    stations = pl.read_parquet(PARSED / "stations.parquet")
    total = votes["votes"].sum()
    note = NOTE.format(
        units=stations.height,
        votes=total,
        official=sum(OFFICIAL.values()),
        missing=sum(OFFICIAL.values()) - total,
        registered=votes.unique("NAME5")["reg"].sum(),
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indonesia's 2019 presidential vote by polling station from SAGE, a non-default supplement",
            polity="ID",
        )
        indonesia = cs.polity("ID")
        cs.add("scheme", code="id.tps", polity_id=indonesia, kind="precinct", rank=3, noun="polling station")
        source = cs.source("Noah Dasanaike, Small-Area Global Elections (SAGE)", "SAGE, Indonesia")
        cs.add(
            "source",
            id=source,
            kind="compendium",
            publisher="Noah Dasanaike, Small-Area Global Elections (SAGE)",
            title="SAGE, Indonesia 2019 presidential election by polling station",
            url=DOI,
            license="CC0-1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            notes="The deposit is CC0-1.0; the SAGE site declares CC BY 4.0. Upstream: Komisi Pemilihan Umum Republik Indonesia.",
        )
        cs.artifact(raw.cached(CACHE, URL), source)

        election = cs.key("id.election", str(DAY))
        contest = cs.key("id.contest", CONTEST)
        result_set = cs.key(f"{SOURCE}.result_set", CONTEST)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=None,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
        options = {ticket: str(cs.key("id.option", f"{CONTEST}:{ticket}")) for ticket in TICKETS.values()}

        province = {
            name: (str(cs.area("id.provinsi", code)), str(cs.key("id.kpu_pilpres.unit", f"id.provinsi:{code}")))
            for name, code in PROVINCES.items()
        }
        unit_ids = {code: str(cs.unit("id.tps", code)) for code in stations["NAME5"]}
        located = pl.col("longitude").is_not_null()
        stations = stations.with_columns(
            id=pl.col("NAME5").replace_strict(unit_ids),
            area=pl.col("province").str.to_uppercase().replace_strict({k: v[0] for k, v in province.items()}),
            parent=pl.col("province").str.to_uppercase().replace_strict({k: v[1] for k, v in province.items()}),
        )
        cs.frame(
            "reporting_unit",
            stations.select(
                id="id",
                polity_id=pl.lit(str(indonesia)),
                scheme_code=pl.lit("id.tps"),
                code="NAME5",
                parent_id="parent",
                area_relation=pl.lit("within"),
                area_id="area",
                location=pl.when(located).then(pl.format("SRID=4326;POINT({} {})", "longitude", "latitude")),
                kind=pl.lit("standard"),
            ),
        )
        cs.frame(
            "name",
            stations.select(
                entity_id="id",
                text=pl.concat_str("village", "district", "regency", "province", separator=", ", ignore_nulls=True),
                lang=pl.lit("id"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )
        cs.frame(
            "result_value",
            votes.select(
                result_set_id=pl.lit(str(result_set)),
                unit_id=pl.col("NAME5").replace_strict(unit_ids),
                option_id=pl.col("ticket").replace_strict({k: v for k, v in options.items()}),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value=pl.col("votes").cast(pl.Int64),
            ),
        )
        cs.frame(
            "value_locator",
            votes.select(
                result_set_id=pl.lit(str(result_set)),
                unit_id=pl.col("NAME5").replace_strict(unit_ids),
                option_id=pl.col("ticket").replace_strict({k: v for k, v in options.items()}),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                page_id=pl.lit(None, pl.String),
                source_id=pl.lit(str(source)),
            ),
        )
        return cs.write()
