from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, measures
from ea.store import DATA, connect

SOURCE = "us.measure_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIRST = 2000
LAST = 2025
ARCHIVE = "Precinct rows of the state's election statistics archive, which add up to its county totals for each answer."


def fetch() -> None:
    measures.fetch()


def parse() -> None:
    with connect() as conn:
        counties: dict[str, dict[str, tuple[str, str]]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            city = code[:2] == "51" and int(code[2:]) >= 500
            if int(code[2:]) >= 500:
                names[f"{elstats.plain(text)}city"] = (code, f"{text.upper()} CITY" if city else text.upper())
            names.setdefault(elstats.plain(text), (code, f"{text.upper()} CITY" if city else text.upper()))
    frames = []
    for state, district in measures.STATEWIDE.items():
        frame = (
            pl.scan_csv(
                raw.cached(measures.SOURCE, measures.url(elstats.TENANTS[state][0], district)).path,
                infer_schema=False,
            )
            .with_row_index("row")
            .filter(
                pl.col("retention_candidate_id").is_null(),
                pl.col("district_type") == "State",
                pl.col("election_date").str.slice(0, 4).cast(pl.Int32).is_between(FIRST, LAST),
            )
            .with_columns(
                top=pl.when(pl.col("division_type").is_in(["County", "Locality"])).then(
                    pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
                )
            )
            .with_columns(county=pl.col("top").forward_fill().over("contest_id", order_by="row"))
            .filter(
                pl.col("division_type") == "Precinct",
                pl.col("candidate_name").str.to_uppercase().str.strip_chars().is_in(["YES", "NO"]),
            )
            .collect()
        )
        if frame.height == 0:
            continue
        plain = (
            pl.col("county")
            .str.to_lowercase()
            .str.replace_all(r"saint ", "st ")
            .str.replace_all("&", "and")
            .str.replace_all(r"\b(county|parish|borough)\b", "")
            .str.replace_all(r"[^a-z]", "")
        )
        frames.append(
            frame.select(
                state=pl.lit(state),
                question=pl.col("contest_id"),
                printed=pl.col("election_date").str.slice(0, 10).str.to_date(),
                fips=plain.replace_strict({k: v[0] for k, v in counties[state].items()}, default=None),
                jurisdiction=plain.replace_strict({k: v[1] for k, v in counties[state].items()}, default=None),
                division=pl.col("division_id"),
                precinct=pl.col("division_name").str.replace_all(r"\s+", " ").str.strip_chars().str.to_uppercase(),
                answer=pl.col("candidate_name").str.to_uppercase().str.strip_chars(),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = pl.concat(frames).with_columns(
        day=pl.struct("state", "printed").map_elements(
            lambda row: measures.DATES.get((row["state"], row["printed"]), measures.election_day(row["printed"])),
            return_dtype=pl.Date,
        )
    )
    shared = (
        frame.group_by("state", "question", "fips", "precinct")
        .agg(pl.col("division").n_unique())
        .filter(pl.col("division") > 1)
    )
    if shared.height:
        print(f"{shared.height} precinct names shared by several divisions in a county, summed")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.group_by("state", "question", "day", "fips", "jurisdiction", "precinct", "answer")
        .agg(pl.col("votes").sum())
        .sort("state", "question", "fips", "precinct", "answer")
        .write_parquet(PARSED / "archives.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "archives.parquet").with_columns(
        key=pl.concat_str(pl.col("day").cast(pl.String), "state", pl.lit("question"), "question", separator=":")
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions by precinct 2000–2025 from the states' election statistics archives",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and rs.changeset_id not in
                       (select id from changeset where pipeline = %s)
                     and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [SOURCE],
            ).fetchall()
        }
        states = {
            iso.removeprefix("US-"): polity
            for iso, polity in conn.execute("select iso_code, id from polity where iso_code like 'US-%%'").fetchall()
        }
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        defaults = pl.DataFrame(
            conn.execute(
                """select cx.value, rs.election_id::text, rs.contest_id::text, rs.source_id::text,
                          upper(o.ballot_label), o.id::text, u.scheme_code, u.code, v.value::bigint
                   from external_id cx
                   join result_set rs on rs.contest_id = cx.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   join contest_option o on o.k = v.option_k
                   join reporting_unit u on u.k = v.unit_k
                   where cx.scheme = 'us.contest' and cx.value = any(%s)""",
                [frame["key"].unique().to_list()],
            ).fetchall(),
            schema=["key", "election", "contest", "source", "answer", "option", "scheme", "code", "total"],
            orient="row",
        )
        landed = skipped = 0
        minted: dict[str, tuple[str, str, date]] = {}
        values = []
        for (key, state), rows in frame.group_by("key", "state", maintain_order=True):
            official = defaults.filter(pl.col("key") == key)
            if official.height == 0:
                continue
            if key in filled:
                print(f"{key}: already has a precinct set")
                continue
            if rows["fips"].null_count():
                print(
                    f"{key}: {rows.filter(pl.col('fips').is_null())['votes'].sum()} votes in precincts with no county, skipped"
                )
                skipped += 1
                continue
            sums = rows.group_by("answer").agg(pl.col("votes").sum())
            totals = official.group_by("answer").agg(pl.col("total").sum())
            joined = totals.join(sums, on="answer", how="full", coalesce=True).fill_null(0)
            if (joined["votes"] != joined["total"]).any():
                print(f"{key}: precincts {sorted(sums.rows())} against totals {sorted(totals.rows())}, skipped")
                skipped += 1
                continue
            if official["scheme"].unique().to_list() == ["us.county"]:
                by_county = (
                    official.group_by("code", "answer")
                    .agg(pl.col("total").sum())
                    .join(
                        rows.group_by("fips", "answer").agg(pl.col("votes").sum()).rename({"fips": "code"}),
                        on=["code", "answer"],
                        how="full",
                        coalesce=True,
                    )
                    .fill_null(0)
                    .filter(pl.col("votes") != pl.col("total"))
                )
                if by_county.height:
                    print(f"{key}: county sums differ in {by_county['code'].n_unique()} counties, skipped")
                    skipped += 1
                    continue
            first = official.row(0, named=True)
            options = dict(official.select("answer", "option").unique().iter_rows())
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            if result_set not in cs.minted:
                continue
            day = rows["day"][0]
            cs.add(
                "result_set",
                id=result_set,
                election_id=UUID(first["election"]),
                contest_id=UUID(first["contest"]),
                stage="certified",
                kind="counted",
                status="active",
                source_id=UUID(first["source"]),
                is_default=False,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=ARCHIVE)
            codes = {}
            for fips, jurisdiction, precinct in rows.select("fips", "jurisdiction", "precinct").unique().iter_rows():
                code = f"{day.year}:{state}:{fips}:{jurisdiction}:{precinct}"
                codes[code] = str(cs.unit("us.precinct", code, on=day))
                if UUID(codes[code]) in cs.minted:
                    minted.setdefault(code, (state, fips, day))
            values.append(
                rows.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.concat_str(
                        pl.lit(f"{day.year}:{state}"), "fips", "jurisdiction", "precinct", separator=":"
                    ).replace_strict(codes),
                    option_id=pl.col("answer").replace_strict(options),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
            landed += 1
        for code, (state, fips, day) in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", code, on=day),
                polity_id=states[state],
                scheme_code="us.precinct",
                code=code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        if values:
            cs.frame("result_value", pl.concat(values))
        print(f"{landed} precinct sets, {skipped} skipped, {len(minted)} new precinct units")
        return cs.write()
