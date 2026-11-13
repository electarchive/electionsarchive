from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.fr import departementales
from ea.sources.fr.cdsp import AGGREGATED, AGGREGATED_TITLE
from ea.sources.fr.cdsp import SOURCE as CDSP
from ea.sources.fr.regionales_communes import fold
from ea.store import DATA, connect

SOURCE = "fr.departementales_communes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (2015, 2021)
MEASURES = {"registered": 2, "voters": 14, "valid": 4, "null": 5, "blank": 6}
METHOD = (
    "Results by commune, added up from the Interior Ministry's results by polling station on data.gouv.fr. A commune "
    "split between cantons appears in each with only its polling stations in that canton. The communes of a canton "
    "add up to the canton's result set."
)
DRIFT = (
    "The ministry's files by polling station and by canton are separate publications and differ slightly: no total "
    "or binôme's votes here differs from the canton result set by more than {votes}."
)


def fetch() -> None:
    for url in AGGREGATED.values():
        raw.fetch(CDSP, url, suffix=".parquet")


def parse() -> None:
    wanted = [f"{year}_dpmt_t{round_}" for year in YEARS for round_ in (1, 2)]
    general = (
        pl.scan_parquet(raw.cached(CDSP, AGGREGATED["general"]).path)
        .filter(pl.col("id_election").is_in(wanted))
        .select(
            "id_election",
            "id_brut_miom",
            "code_departement",
            "code_canton",
            "code_commune",
            "inscrits",
            "votants",
            "exprimes",
            "blancs",
            "nuls",
        )
        .collect()
    )
    candidates = (
        pl.scan_parquet(raw.cached(CDSP, AGGREGATED["candidates"]).path)
        .filter(pl.col("id_election").is_in(wanted))
        .select("id_election", "id_brut_miom", "binome", "voix")
        .collect()
    )
    keyed = general.with_columns(
        year=pl.col("id_election").str.slice(0, 4).cast(pl.Int64),
        round=pl.col("id_election").str.slice(-1).cast(pl.Int64),
        canton=pl.col("code_departement").replace(departementales.OVERSEAS) + pl.col("code_canton"),
    )
    totals = keyed.group_by("year", "round", "canton", pl.col("code_commune").alias("commune")).agg(
        pl.col("inscrits").sum().alias("registered"),
        pl.col("votants").sum().alias("voters"),
        pl.col("exprimes").sum().alias("valid"),
        pl.col("blancs").sum().alias("blank"),
        pl.col("nuls").sum().alias("null"),
    )
    lumped = pl.col("blank").sum().over("year", "round") == 0
    totals = totals.with_columns(
        blank=pl.when(lumped).then(None).otherwise(pl.col("blank")),
        null=pl.when(lumped).then(None).otherwise(pl.col("null")),
    )
    where = keyed.select(
        "id_election", "id_brut_miom", "year", "round", "canton", pl.col("code_commune").alias("commune")
    )
    votes = (
        candidates.join(where, on=["id_election", "id_brut_miom"], how="left")
        .group_by(
            "year",
            "round",
            "canton",
            "commune",
            pl.col("binome").map_elements(fold, return_dtype=pl.String).alias("binome"),
        )
        .agg(pl.col("voix").sum().alias("votes"))
    )
    if votes["canton"].null_count():
        raise ValueError("candidate rows without a polling station")
    PARSED.mkdir(parents=True, exist_ok=True)
    totals.write_parquet(PARSED / "totals.parquet")
    votes.write_parquet(PARSED / "votes.parquet")


def emit() -> Path:
    totals = pl.read_parquet(PARSED / "totals.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    canton_totals = pl.read_parquet(departementales.PARSED / "totals.parquet")
    canton_binomes = pl.read_parquet(departementales.PARSED / "binomes.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "French departmental elections of 2015 and 2021, both rounds, by commune within each canton, added "
                "up from the Interior Ministry's results by polling station"
            ),
            polity="FR",
        )
        aggregated = cs.source("data.gouv.fr", AGGREGATED_TITLE)
        if aggregated in cs.minted:
            raise ValueError("aggregated source is not in the database")
        known = {
            code
            for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'fr.commune'").fetchall()
        }
        unit_ids = {code: str(cs.unit("fr.commune", code)) for code in sorted(known)}
        unit_map = pl.DataFrame({"commune": list(unit_ids), "unit_id": list(unit_ids.values())})
        keys = ["year", "round", "canton"]
        options = pl.DataFrame(
            [
                {
                    **{key: row[key] for key in keys},
                    "binome": fold(row["binome"]),
                    "option_id": str(
                        cs.key(
                            f"{departementales.SOURCE}.option",
                            f"{row['year']}:{row['canton']}:{row['round']}:{int(row['panel'])}",
                        )
                    ),
                    "expected": row["votes"],
                }
                for row in canton_binomes.filter(pl.col("year").is_in(YEARS)).iter_rows(named=True)
            ]
        )
        stray = votes.select(*keys, "binome").unique().join(options, on=[*keys, "binome"], how="anti")
        spare = options.join(votes.select(*keys, "binome").unique(), on=[*keys, "binome"], how="anti")
        pairs = (
            stray.group_by(keys)
            .agg(pl.col("binome").alias("stray"))
            .join(spare.group_by(keys).agg(pl.col("binome").alias("spare")), on=keys)
        )
        renames = {
            (row["year"], row["round"], row["canton"], row["stray"][0]): row["spare"][0]
            for row in pairs.iter_rows(named=True)
            if len(row["stray"]) == 1 and len(row["spare"]) == 1
        }
        votes = votes.with_columns(
            binome=pl.struct(*keys, "binome").map_elements(
                lambda r: renames.get((r["year"], r["round"], r["canton"], r["binome"]), r["binome"]),
                return_dtype=pl.String,
            )
        )
        matched = votes.join(options, on=[*keys, "binome"], how="left").join(unit_map, on="commune", how="left")
        problems: list[str] = []
        if matched["option_id"].null_count():
            problems.append(f"{matched['option_id'].null_count()} commune rows match no binôme")
        if matched["unit_id"].null_count() or totals.join(unit_map, on="commune", how="anti").height:
            problems.append("communes without a unit")
        checked = (
            totals.group_by(keys)
            .agg(pl.col(field).sum() for field in ("registered", "voters", "valid"))
            .join(
                canton_totals.select(
                    *keys, *[pl.col(field).alias(f"{field}_c") for field in ("registered", "voters", "valid")]
                ),
                on=keys,
                how="left",
            )
        )
        drift = checked.with_columns(
            drift=pl.max_horizontal(*[(pl.col(f) - pl.col(f"{f}_c")).abs() for f in ("voters", "valid")]),
            registered_drift=(pl.col("registered") - pl.col("registered_c")).abs(),
        ).select(*keys, "drift", "registered_drift", "registered_c", "valid_c")
        by_panel = (
            options.join(
                matched.group_by([*keys, "binome"]).agg(pl.col("votes").sum()), on=[*keys, "binome"], how="left"
            )
            .with_columns(delta=(pl.col("expected") - pl.col("votes").fill_null(0)).abs())
            .group_by(keys)
            .agg(pl.col("delta").max())
        )
        drift = drift.join(by_panel, on=keys, how="left").with_columns(
            drift=pl.max_horizontal("drift", pl.col("delta").fill_null(0))
        )
        if drift["valid_c"].null_count():
            problems.append("communes in cantons without a canton contest")
        for row in drift.iter_rows(named=True):
            if row["valid_c"] is not None and (
                row["drift"] * 100 > row["valid_c"] + 100 or row["registered_drift"] * 30 > row["registered_c"]
            ):
                problems.append(f"{row['year']}:{row['canton']}:{row['round']}: differs by up to {row['drift']} votes")
        if problems:
            raise ValueError("commune results do not match:\n" + "\n".join(problems[:50]))
        sets: dict[tuple[int, str, int], str] = {}
        for row in drift.iter_rows(named=True):
            year, canton, round_ = row["year"], row["canton"], row["round"]
            contest_key = f"{year}:{canton}:{round_}"
            contest = cs.key(f"{departementales.SOURCE}.contest", contest_key)
            canton_set = cs.key(f"{departementales.SOURCE}.result_set", contest_key)
            election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()
            if election is None:
                raise ValueError(f"{contest_key}: contest not in the database")
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            sets[year, canton, round_] = str(result_set)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election[0],
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=aggregated,
                is_default=False,
                completeness=1,
            )
            for option, kind, seats in conn.execute(
                "select option_id, kind, seats from outcome where result_set_id = %s", [canton_set]
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            if row["drift"] or row["registered_drift"]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=DRIFT.format(votes=max(row["drift"], row["registered_drift"])),
                )
        set_map = pl.DataFrame(
            [{"year": y, "canton": c, "round": r, "result_set_id": rs} for (y, c, r), rs in sets.items()]
        )
        accounting = (
            totals.join(set_map, on=keys)
            .join(unit_map, on="commune")
            .select("result_set_id", "unit_id", "registered", "voters", "valid", "null", "blank")
            .unpivot(index=["result_set_id", "unit_id"], variable_name="field", value_name="value")
            .drop_nulls("value")
            .select(
                "result_set_id",
                "unit_id",
                option_id=pl.lit(None, pl.String),
                measure=pl.col("field").replace_strict(MEASURES, return_dtype=pl.Int16),
                value=pl.col("value").cast(pl.Int64),
            )
        )
        option_rows = matched.join(set_map, on=keys).select(
            "result_set_id",
            "unit_id",
            "option_id",
            measure=pl.lit(1, pl.Int16),
            value=pl.col("votes").cast(pl.Int64),
        )
        rows = [accounting, option_rows]
        cs.frame(
            "result_value",
            pl.concat(rows).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
