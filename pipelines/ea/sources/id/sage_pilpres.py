import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.id import big, big_desa
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
SPLIT = {"93": "91", "94": "91", "95": "91", "96": "92"}
RENAMED = {
    "Maluku Tenggara Barat": "Kepulauan Tanimbar",
    "Toba Samosir": "Toba",
    "Mamuju Utara": "Pasangkayu",
    "Pahuwato": "Pohuwato",
    "Kota Padang Sidempuan": "Kota Padangsidimpuan",
}
NOTE = (
    "Votes for each ticket at {units:,} polling stations (tempat pemungutan suara, TPS) in the 34 provinces of 2019, "
    "from the 2019 Indonesia partition of SAGE (Small-Area Global Elections), which names the Komisi Pemilihan Umum "
    "as its upstream and gives no per-file URL or retrieval date. The stations' votes sum to {votes:,} against the "
    "{official:,} that KPU's certificate declares, so {missing:,} votes are missing, and there are no abroad "
    "stations. SAGE's registered voters (which sum to {registered:,}, against the certificate's 192,770,611) are not "
    "stored, and its valid-vote totals equal the two tickets' sum at every station, so none is added. Station "
    "codes are SAGE's own sequence numbers, not KPU identifiers. The names are SAGE's. Points are SAGE's geocodes "
    "of the village name, not surveyed locations, and are blank where SAGE found none. Each station is placed in "
    "the village (BIG, 2026 boundaries) whose regency, district, and village names match SAGE's, or whose regency "
    "and village names match when that is unique: {villages:,} stations; {regencies:,} more sit in their regency "
    "only, and {provinces:,} (most of Jawa Timur, where SAGE gives no names) in their province. This is a supplement to the "
    "official provincial set, never the default, and carries no seats or winners."
)


def _bare(text: str | None) -> str:
    text = re.sub(r"^(KABUPATEN|KOTA)( ADMINISTRASI)?\s+|^(DESA|KELURAHAN)\s+", "", (text or "").upper())
    return re.sub(r"[^A-Z0-9]", "", text)


def _place(stations: pl.DataFrame) -> pl.DataFrame:
    regencies: dict[tuple[str, bool, str], list[str]] = {}
    for code, text in (
        pl.read_parquet(big.PARSED / "areas.parquet").filter(pl.col("layer") == "kabkota").select("code", "name").rows()
    ):
        regencies.setdefault((SPLIT.get(code[:2], code[:2]), text.startswith("Kota"), _bare(text)), []).append(code)
    full: dict[tuple[str, str, str], list[str]] = {}
    loose: dict[tuple[str, str], list[str]] = {}
    for code, parent, text, district in (
        pl.read_parquet(big_desa.PARSED / "villages.parquet").select("code", "parent", "name", "district").rows()
    ):
        full.setdefault((parent, _bare(district), _bare(text)), []).append(code)
        loose.setdefault((parent, _bare(text)), []).append(code)
    placed = []
    for province, regency, district, village in (
        stations.select("province", "regency", "district", "village").unique().rows()
    ):
        kabkota = desa = None
        if regency is not None:
            label = RENAMED.get(regency, regency)
            if province.upper() == "DKI JAKARTA" and label.startswith("Jakarta"):
                label = f"Kota {label}"
            found = regencies.get((PROVINCES[province.upper()], label.startswith("Kota"), _bare(label)), [])
            kabkota = found[0] if len(found) == 1 else None
        if kabkota is not None and village is not None:
            found = full.get((kabkota, _bare(district), _bare(village)), [])
            if len(found) != 1:
                found = loose.get((kabkota, _bare(village)), [])
            desa = found[0] if len(found) == 1 else None
        placed.append((province, regency, district, village, kabkota, desa))
    return stations.join(
        pl.DataFrame(placed, schema=["province", "regency", "district", "village", "kabkota", "desa"], orient="row"),
        on=["province", "regency", "district", "village"],
        how="left",
        nulls_equal=True,
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
    stations = _place(stations)
    stations.write_parquet(PARSED / "stations.parquet")
    print(
        f"{stations.height} stations: {stations['desa'].count()} in villages, "
        f"{stations['kabkota'].count() - stations['desa'].count()} in regencies only"
    )


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
        villages=stations["desa"].count(),
        regencies=stations["kabkota"].count() - stations["desa"].count(),
        provinces=stations["kabkota"].null_count(),
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
            area=pl.coalesce(
                pl.col("desa").replace_strict(
                    {code: str(cs.area("id.desa", code)) for code in stations["desa"].drop_nulls().unique()},
                    default=None,
                ),
                pl.col("kabkota").replace_strict(
                    {code: str(cs.area("id.kabkota", code)) for code in stations["kabkota"].drop_nulls().unique()},
                    default=None,
                ),
                pl.col("province").str.to_uppercase().replace_strict({k: v[0] for k, v in province.items()}),
            ),
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
