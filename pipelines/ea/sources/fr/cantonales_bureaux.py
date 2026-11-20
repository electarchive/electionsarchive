"""French cantonal elections of 2008 and 2011 by commune and polling station.

The Interior Ministry's files by polling station name no canton, so each polling station is placed in the canton whose
candidates it lists. The packages add two result sets to each canton contest that `fr.departementales` published, one by
commune and one by polling station, and leave the canton result sets as they are. The contests, options, and commune
units they point at are read from the reference partition of France (`data/reference/fr/`), because production already
holds them.
"""

from datetime import timedelta
from functools import cache
from typing import Any

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.fr import departementales
from ea.sources.fr.regionales_communes import fold
from ea.store import DATA

SOURCE = "fr.cantonales_bureaux"
REFERENCE = DATA / "reference" / "fr"
STATIC = "https://static.data.gouv.fr/resources"
STATIONS = {
    2008: f"{STATIC}/elections-cantonales-2008-resultats-par-bureaux-de-vote/20151001-154651/CN08_BVot_T1T2.txt",
    2011: f"{STATIC}/elections-cantonales-2011-resultats-par-bureaux-de-vote/20150925-104805/CN11_BVot_T1T2.txt",
}
DATASETS = {
    2008: "https://www.data.gouv.fr/datasets/elections-cantonales-2008-resultats-par-bureaux-de-vote",
    2011: "https://www.data.gouv.fr/datasets/elections-cantonales-2011-resultats-par-bureaux-de-vote",
}
LICENSE = departementales.LICENSE
COLUMNS = [
    "round",
    "dep",
    "commune",
    "label",
    "bureau",
    "registered",
    "voters",
    "valid",
    "panel",
    "surname",
    "forename",
    "nuance",
    "votes",
]
MEASURES = {"registered": 2, "voters": 14, "valid": 4}
GRAINS = {"communes": "commune", "stations": "polling station"}
METHOD = {
    "communes": (
        "Results by commune, added up from the Interior Ministry's results by polling station for the cantonal "
        "elections of {year} on data.gouv.fr. That file names no canton, so each polling station is placed in the "
        "canton whose candidates it lists. A commune split between cantons appears in each with only its polling "
        "stations in that canton. The communes of a canton add up to the canton's result set."
    ),
    "stations": (
        "Results by polling station from the Interior Ministry's file for the cantonal elections of {year} on "
        "data.gouv.fr. The file names no canton, so each polling station is placed in the canton whose candidates it "
        "lists. A polling station's number is its number within its commune. The polling stations of a canton add up "
        "to the canton's result set. The file does not give blank and null ballots separately, and no outcome is "
        "repeated here: the outcomes are on the canton result set."
    ),
}
REPEATS = {
    2011: (
        " The file lists a second-round polling station's candidate lines twice in many cantons; each repeated line "
        "is counted once, which makes the polling stations add up to the canton."
    )
}
DRIFT = (
    "The ministry's files by polling station and by canton are separate publications and differ slightly: no total "
    "or candidate's votes here differs from the canton result set by more than {votes}."
)


def fetch() -> None:
    for url in STATIONS.values():
        raw.fetch(SOURCE, url, suffix=".txt")
    for url in departementales.CANTONALES.values():
        raw.fetch(departementales.SOURCE, url, suffix=".xls")


def packages() -> list[str]:
    return [f"{SOURCE}/cantonales-{year}" for year in STATIONS]


def parse() -> None:
    for year in STATIONS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


def insee(dep: str, commune: str) -> str:
    if dep == "ZM":
        return f"976{commune[1:]}"
    if dep in departementales.OVERSEAS:
        return f"97{commune}"
    return f"{dep}{commune}"


def stations(year: int) -> pl.DataFrame:
    path = raw.cached(SOURCE, STATIONS[year]).path
    rows = [
        line.rstrip("\r\n").split(";")
        for line in path.read_text(encoding="latin-1").splitlines()
        if line.strip() and not line.startswith("--")
    ]
    if {len(row) for row in rows} != {len(COLUMNS)}:
        raise ValueError(f"{year}: unexpected columns in the polling station file")
    frame = pl.DataFrame(rows, schema=dict.fromkeys(COLUMNS, pl.String), orient="row").unique(maintain_order=True)
    return frame.with_columns(
        pl.col("round", "registered", "voters", "valid", "votes").cast(pl.Int64),
        commune_code=pl.struct("dep", "commune").map_elements(
            lambda r: insee(r["dep"], r["commune"]), return_dtype=pl.String
        ),
        dep=pl.col("dep").replace(departementales.OVERSEAS),
        name=pl.struct("surname", "forename").map_elements(
            lambda r: fold(f"{r['surname']} {r['forename']}"), return_dtype=pl.String
        ),
    )


def placed(year: int) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """The file's rows with the canton and option order of each, the canton totals, and the canton candidates."""
    totals, candidates = departementales.cantonales(year)
    cantons = pl.DataFrame(candidates).with_columns(
        dep=pl.col("canton").str.head(-2),
        name=pl.col("binome").map_elements(fold, return_dtype=pl.String),
    )
    rows = stations(year)
    keys = ["round", "dep", "name"]
    possible = (
        rows.join(
            cantons.group_by(keys).agg(pl.col("canton").unique().alias("canton")),
            on=keys,
            how="left",
        )
        .with_row_index("row")
        .explode("canton")
    )
    if possible["canton"].null_count():
        raise ValueError(f"{year}: {possible['canton'].null_count()} polling station rows match no candidate")
    group = ["round", "dep", "commune", "bureau"]
    size = rows.group_by(group).len().rename({"len": "size"})
    agreed = (
        possible.group_by([*group, "canton"])
        .agg(pl.col("row").n_unique().alias("seen"))
        .join(size, on=group)
        .filter(pl.col("seen") == pl.col("size"))
    )
    if agreed.group_by(group).len().filter(pl.col("len") != 1).height or agreed.height != size.height:
        raise ValueError(f"{year}: a polling station fits no canton, or more than one")
    placed_rows = rows.join(agreed.select(*group, "canton"), on=group).join(
        cantons.select("round", "canton", "name", "order"), on=["round", "canton", "name"], how="left"
    )
    if placed_rows["order"].null_count():
        raise ValueError(f"{year}: candidates without an option")
    return placed_rows, pl.DataFrame(totals), cantons


@cache
def reference() -> dict[str, Any]:
    external = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value"])
    contests = pl.read_parquet(REFERENCE / "contest.parquet", columns=["id", "election_id"])
    units = pl.read_parquet(
        REFERENCE / "reporting_unit.parquet", columns=["id", "scheme_code", "code", "area_id", "pipeline"]
    ).filter(pl.col("scheme_code") == "fr.commune")
    polity = pl.read_parquet(REFERENCE / "polity.parquet", columns=["id", "iso_code"]).filter(
        pl.col("iso_code") == "FR"
    )
    return {
        "france": polity["id"].item(),
        "contest": external.filter(pl.col("scheme") == f"{departementales.SOURCE}.contest")
        .select(key="value", contest_id="entity_id")
        .join(contests, left_on="contest_id", right_on="id"),
        "option": external.filter(pl.col("scheme") == f"{departementales.SOURCE}.option").select(
            key="value", option_id="entity_id"
        ),
        "commune": units.select("code", unit_id="id", area_id="area_id", pipeline="pipeline"),
    }


def build(year: int) -> Package:
    first, second = departementales.ROUNDS[year]
    p = Package(
        f"{SOURCE}/cantonales-{year}",
        SOURCE,
        f"French cantonal elections of {year}, both rounds, by commune and polling station",
    )
    known = reference()
    france = known["france"]
    p.external |= {france, "fr.bureau"}
    source = p.id("source", year)
    title = f"Elections cantonales {year} – Résultats par bureaux de vote"
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Ministère de l'Intérieur",
        title=title,
        url=DATASETS[year],
        license=LICENSE[0],
        license_url=LICENSE[1],
    )
    p.artifact(raw.cached(SOURCE, STATIONS[year]), source)
    cantonal = p.id("source", f"{year}:cantons")
    p.add(
        "source",
        id=cantonal,
        kind="official_body",
        publisher="Ministère de l'Intérieur",
        title=f"Elections cantonales {year} – Résultats",
        url=departementales.DATASETS[year][1],
        license=LICENSE[0],
        license_url=LICENSE[1],
    )
    p.artifact(raw.cached(departementales.SOURCE, departementales.CANTONALES[year]), cantonal)

    rows, totals, _ = placed(year)
    communes = known["commune"].sort("pipeline").unique("code", keep="first")
    if rows.join(communes, left_on="commune_code", right_on="code", how="anti").height:
        raise ValueError(f"{year}: communes without a unit")
    rows = rows.join(communes, left_on="commune_code", right_on="code", how="left")
    contest_key = pl.lit(f"{year}:") + pl.col("canton") + pl.lit(":") + pl.col("round").cast(pl.String)
    rows = (
        rows.with_columns(contest_key=contest_key)
        .with_columns(option_key=pl.col("contest_key") + pl.lit(":") + pl.col("order").cast(pl.String))
        .join(known["contest"].rename({"key": "contest_key"}), on="contest_key", how="left")
        .join(known["option"].rename({"key": "option_key"}), on="option_key", how="left")
    )
    if rows["contest_id"].null_count() or rows["option_id"].null_count():
        raise ValueError(f"{year}: contests or options the archive does not hold")
    p.external |= set(rows["contest_id"].unique()) | set(rows["option_id"].unique())
    p.external |= set(rows["unit_id"].unique()) | set(rows["area_id"].unique())
    p.external |= set(rows["election_id"].unique())

    bureaux = (
        rows.unique(["commune_code", "bureau"])
        .select("commune_code", "bureau", "label", "area_id")
        .with_columns(
            id=pl.struct("commune_code", "bureau").map_elements(
                lambda r: p.id("unit", year, r["commune_code"], r["bureau"]), return_dtype=pl.String
            ),
            code=pl.col("commune_code") + pl.lit(":") + pl.col("bureau"),
        )
    )
    p.frame(
        "reporting_unit",
        bureaux.select(
            "id",
            "code",
            "area_id",
            polity_id=pl.lit(france),
            scheme_code=pl.lit("fr.bureau"),
            area_relation=pl.lit("within"),
            kind=pl.lit("standard"),
            valid_from=pl.lit(first),
            valid_to=pl.lit(second + timedelta(days=1)),
        ),
    )
    p.frame(
        "name",
        bureaux.select(
            pl.struct("id", "label", "bureau")
            .map_elements(lambda r: p.id("name", r["id"], "fr", "official"), return_dtype=pl.String)
            .alias("id"),
            entity_id="id",
            text=pl.col("label") + pl.lit(", bureau ") + pl.col("bureau"),
            lang=pl.lit("fr"),
            kind=pl.lit("official"),
            is_preferred=pl.lit(True),
        ),
    )
    unit_of = bureaux.select("commune_code", "bureau", station_unit="id")
    rows = rows.join(unit_of, on=["commune_code", "bureau"])

    keys = ["round", "canton", "contest_key", "contest_id", "election_id"]
    drift: dict[tuple[int, str], int] = {}
    sets: list[dict[str, object]] = []
    values: list[pl.DataFrame] = []
    canton_votes = (
        pl.DataFrame(departementales.cantonales(year)[1])
        .select("round", "canton", "order", "votes")
        .rename({"votes": "expected"})
    )
    for grain in GRAINS:
        unit = "station_unit" if grain == "stations" else "unit_id"
        accounting = rows.unique([*keys, "commune_code", "bureau"]).select(
            *keys, "unit_id", "station_unit", "registered", "voters", "valid"
        )
        if grain == "communes":
            accounting = accounting.group_by([*keys, unit]).agg(
                pl.col("registered").sum(), pl.col("voters").sum(), pl.col("valid").sum()
            )
        sets_here = accounting.select(*keys).unique()
        votes = rows.group_by([*keys, unit, "option_id", "order"]).agg(pl.col("votes").sum())
        tally = (
            votes.group_by([*keys, "order"])
            .agg(pl.col("votes").sum())
            .join(canton_votes, on=["round", "canton", "order"], how="left")
        )
        gap = (
            tally.with_columns(delta=(pl.col("votes") - pl.col("expected").fill_null(0)).abs())
            .group_by("round", "canton")
            .agg(pl.col("delta").max())
        )
        sums = accounting.group_by("round", "canton").agg(
            pl.col("registered").sum(), pl.col("voters").sum(), pl.col("valid").sum()
        )
        check = (
            sums.join(
                totals.select("round", "canton", "registered", "voters", "valid"), on=["round", "canton"], suffix="_c"
            )
            .join(gap, on=["round", "canton"])
            .with_columns(
                moved=pl.max_horizontal(
                    (pl.col("registered") - pl.col("registered_c")).abs(),
                    (pl.col("voters") - pl.col("voters_c")).abs(),
                    (pl.col("valid") - pl.col("valid_c")).abs(),
                    pl.col("delta"),
                )
            )
        )
        for row in check.iter_rows(named=True):
            drift[row["round"], row["canton"]] = max(drift.get((row["round"], row["canton"]), 0), row["moved"])
        set_ids = {
            (row["round"], row["canton"]): p.id("result_set", year, row["canton"], row["round"], grain)
            for row in sets_here.iter_rows(named=True)
        }
        for row in sets_here.iter_rows(named=True):
            sets.append(
                {
                    "id": set_ids[row["round"], row["canton"]],
                    "election_id": row["election_id"],
                    "contest_id": row["contest_id"],
                    "grain": grain,
                    "canton": row["canton"],
                    "round": row["round"],
                }
            )
        mapping = pl.DataFrame([{"round": r, "canton": c, "result_set_id": i} for (r, c), i in set_ids.items()])
        values.append(
            accounting.join(mapping, on=["round", "canton"])
            .select("result_set_id", unit_id=unit, **{field: pl.col(field) for field in MEASURES})
            .unpivot(index=["result_set_id", "unit_id"], variable_name="field", value_name="value")
            .select(
                "result_set_id",
                "unit_id",
                option_id=pl.lit(None, pl.String),
                measure=pl.col("field").replace_strict(MEASURES, return_dtype=pl.Int16),
                value=pl.col("value"),
            )
        )
        values.append(
            votes.join(mapping, on=["round", "canton"]).select(
                "result_set_id", unit_id=unit, option_id="option_id", measure=pl.lit(1, pl.Int16), value="votes"
            )
        )
    for entry in sets:
        result_set = str(entry["id"])
        p.add(
            "result_set",
            id=result_set,
            election_id=entry["election_id"],
            contest_id=entry["contest_id"],
            stage="official",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=1,
        )
        p.add(
            "note",
            id=p.id("note", result_set, "methodology"),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD[str(entry["grain"])].format(year=year) + REPEATS.get(year, ""),
            lang="en",
        )
        moved = drift[int(str(entry["round"])), str(entry["canton"])]
        if moved:
            p.add(
                "note",
                id=p.id("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=DRIFT.format(votes=moved),
                lang="en",
            )
    p.frame(
        "result_value",
        pl.concat(values).with_columns(
            channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    worst = max(drift.values(), default=0)
    print(
        f"{year}: {len(sets)} result sets, {bureaux.height} polling stations, {len(drift)} cantons-rounds, "
        f"largest gap to the canton totals {worst}"
    )
    return p
