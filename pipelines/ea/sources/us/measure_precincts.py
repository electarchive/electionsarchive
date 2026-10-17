import io
import json
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import (
    county_measures,
    elstats,
    measures,
    medsl,
    medsl_measures,
    medsl_state,
    medsl_state_precincts,
    oe_precincts,
    openelections,
)
from ea.store import DATA, connect

SOURCE = "us.measure_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIRST = 2000
LAST = 2025
ANSWERS = {"YES": "YES", "NO": "NO", "FOR": "YES", "AGAINST": "NO", "APPROVE": "YES", "REJECT": "NO"}
ARCHIVE = "Precinct rows of the state's election statistics archive, which add up to its county totals for each answer."
MEDSL = (
    "Precinct rows of MEDSL's precinct returns, which compile each state's certified canvass. Each answer's precinct "
    "rows add up to the totals of this question's default results{counties}."
)

OPENELECTIONS = (
    "Precinct rows of OpenElections' volunteer-transcribed precinct files. Each answer's precinct rows add up to the "
    "totals of this question's default results{counties}."
)
COMMITS = openelections.COMMITS | county_measures.COMMITS
FILE = re.compile(
    r"(?:.*/)?(\d{8})__([a-z]{2})__((?:general|primary|special|runoff)(?:__(?:general|primary|runoff))?)(__[a-z_]+)?__precinct\.csv"
)


def fetch() -> None:
    measures.fetch()
    with connect() as conn:
        dates = conn.execute(
            """select distinct substr(p.iso_code, 4), e.date_from from contest c
               join election e on e.id = c.election_id
               join polity p on p.id = e.polity_id and p.iso_code ~ '^US-..$'
               where c.kind = 'ballot_question' and e.date_from between '2015-01-01' and %s
                 and not exists (select 1 from result_set rs join result_value v on v.result_set_k = rs.k
                                 join reporting_unit u on u.k = v.unit_k
                                 where rs.contest_id = c.id and u.scheme_code = 'us.precinct')
               order by 1, 2""",
            [date(LAST, 12, 31)],
        ).fetchall()
    trees: dict[str, list[str]] = {}
    for state, day in dates:
        if state not in COMMITS:
            continue
        if state not in trees:
            tree = raw.fetch(
                SOURCE, openelections.TREE.format(state=state.lower(), commit=COMMITS[state]), suffix=".json"
            )
            trees[state] = [
                entry["path"] for entry in json.loads(tree.path.read_text())["tree"] if entry["type"] == "blob"
            ]
        found = [
            (path, matched)
            for path in trees[state]
            if (matched := FILE.fullmatch(path)) and matched[1] == f"{day:%Y%m%d}" and "diff/" not in path
        ]
        whole = [path for path, matched in found if matched[4] is None]
        for path in whole or [path for path, _ in found]:
            raw.fetch(
                SOURCE,
                openelections.REPOSITORY.format(state=state.lower(), commit=COMMITS[state], path=path),
                suffix=".csv",
            )


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

    affirmative = pl.col("candidate").str.contains(medsl_measures.AFFIRMATIVE.pattern)
    negative = pl.col("candidate").str.contains(medsl_measures.NEGATIVE.pattern)
    frames = []
    for year, files in medsl_state_precincts.FILES.items():
        for source, url in files:
            path = raw.cached(source, url).path
            scan = pl.scan_csv(
                path,
                separator="\t" if "\t" in path.open(errors="replace").readline() else ",",
                infer_schema_length=0,
                encoding="utf8-lossy",
            )
            if year == 2016:
                scan = scan.rename(
                    {"state_postal": "state_po", "jurisdiction": "jurisdiction_name"}, strict=False
                ).with_columns(date=pl.lit("2016-11-08"))
            columns = scan.collect_schema().names()
            fips = pl.col("county_fips").str.replace(r"\.0$", "")
            frames.append(
                scan.with_columns(
                    pl.col("precinct", "mode").fill_null(""),
                    candidate=pl.col("candidate").fill_null("").str.to_uppercase().str.strip_chars(),
                    office=pl.col("office").fill_null("").str.to_uppercase().str.strip_chars(),
                    district=(pl.col("district") if "district" in columns else pl.lit(None, pl.String))
                    .fill_null("")
                    .str.to_uppercase(),
                    stage=(pl.col("stage") if "stage" in columns else pl.lit("GEN"))
                    .fill_null("GEN")
                    .str.to_uppercase(),
                )
                .filter(
                    pl.col("stage") == "GEN",
                    ~pl.col("office").str.contains("RETAIN|RETENTION|JUDGE|JUSTICE|COURT"),
                    ~pl.col("precinct").str.to_uppercase().str.contains(r"TOTALS$|^COUNTY TOTAL$|- TOTAL$"),
                    ~(
                        (pl.col("precinct").str.to_uppercase() == "TOTAL")
                        & (pl.col("mode").str.to_uppercase() == "TOTAL")
                    ),
                )
                .filter(
                    affirmative.any().over("state_po", "office", "district")
                    & negative.any().over("state_po", "office", "district"),
                    affirmative | negative,
                )
                .select(
                    year=pl.lit(year),
                    day=pl.col("date").replace({"11/8/22": "2022-11-08"}).str.to_date(),
                    state="state_po",
                    office="office",
                    district="district",
                    fips=pl.when(fips.str.contains(r"^\d{4,7}$")).then(fips.str.zfill(5).replace(medsl.RECODED)),
                    jurisdiction=pl.col("jurisdiction_name").fill_null("").str.to_uppercase().str.strip_chars(),
                    precinct=pl.col("precinct").str.strip_chars(),
                    mode=pl.col("mode").str.to_uppercase(),
                    answer=pl.when(affirmative).then(pl.lit("YES")).otherwise(pl.lit("NO")),
                    votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                )
                .collect()
            )
    precinct = ["year", "state", "office", "district", "fips", "jurisdiction", "precinct", "answer"]
    frame = pl.concat(frames)
    (
        frame.filter((pl.col("mode") == "TOTAL").any().over(precinct).not_() | (pl.col("mode") == "TOTAL"))
        .group_by("year", "day", *precinct[1:])
        .agg(pl.col("votes").sum())
        .sort(precinct)
        .write_parquet(PARSED / "medsl.parquet")
    )

    candidate = pl.col("candidate").str.to_uppercase().str.strip_chars()
    affirmative = candidate.str.contains(medsl_measures.AFFIRMATIVE.pattern + r"|^IN FAVOR\b")
    negative = candidate.str.contains(medsl_measures.NEGATIVE.pattern)
    (
        pl.scan_parquet(oe_precincts.PARSED / "lines.parquet")
        .with_columns(
            office=pl.col("office").str.to_uppercase().str.replace_all(r"[^A-Z0-9]+", " ").str.strip_chars(),
            district=pl.col("district").fill_null("").str.to_uppercase(),
        )
        .filter(
            ~pl.col("office").str.contains("RETAIN|RETENTION|JUDGE|JUSTICE|COURT"),
            affirmative.any().over("state", "year", "office", "district")
            & negative.any().over("state", "year", "office", "district"),
            affirmative | negative,
        )
        .group_by(
            "year",
            "state",
            "office",
            "district",
            "fips",
            pl.lit("").alias("jurisdiction"),
            "precinct",
            answer=pl.when(affirmative).then(pl.lit("YES")).otherwise(pl.lit("NO")),
        )
        .agg(pl.col("votes").sum())
        .sort("year", "state", "office", "district", "fips", "precinct", "answer")
        .collect()
        .write_parquet(PARSED / "openelections.parquet")
    )

    with connect() as conn:
        fips = openelections.counties(conn)
    for (state, typo), plain in oe_precincts.COUNTIES.items():
        if plain in fips.get(state, {}):
            fips[state][typo] = fips[state][plain]
    index = json.loads((DATA / "raw" / SOURCE / "index.json").read_text())
    frames = []
    for url in index:
        matched = FILE.fullmatch(url.rsplit("/", 1)[1])
        if not url.startswith("https://raw.githubusercontent.com/openelections/") or matched is None:
            continue
        state, day = matched[2].upper(), date(int(matched[1][:4]), int(matched[1][4:6]), int(matched[1][6:]))
        text = raw.cached(SOURCE, url).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            name = oe_precincts.COLUMNS.get(column.strip().lower(), column.strip().lower())
            if name not in renamed.values():
                renamed[column] = name
        frame = frame.rename(renamed)
        if "votes" not in frame.columns and "total" in frame.columns:
            frame = frame.rename({"total": "votes"})
        for column in ("vote_type", "votetype"):
            if column in frame.columns:
                frame = frame.filter(pl.col(column).fill_null("").str.to_lowercase() != "total")
        if "votes" not in frame.columns and any(column.endswith("_votes") for column in frame.columns):
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in frame.columns
                    if column.endswith("_votes")
                ).cast(pl.String)
            )
        if "county" not in frame.columns and matched[4]:
            frame = frame.with_columns(county=pl.lit(matched[4].strip("_").replace("_", " ")))
        if not {"county", "precinct", "office", "candidate", "votes"} <= set(frame.columns):
            print(f"{url}: columns {frame.columns}, skipped")
            continue
        frames.append(
            frame.select(
                state=pl.lit(state),
                day=pl.lit(day),
                fips=pl.col("county")
                .fill_null("")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all("&", "and")
                .str.replace_all(
                    r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                )
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips.get(state, {}), default=None),
                precinct=pl.col("precinct").fill_null("").str.strip_chars().str.replace_all(r"\s+", " "),
                office=pl.col("office")
                .fill_null("")
                .str.to_uppercase()
                .str.replace_all(r"[^A-Z0-9]+", " ")
                .str.strip_chars(),
                district=(pl.col("district") if "district" in frame.columns else pl.lit(None, pl.String))
                .fill_null("")
                .str.to_uppercase(),
                candidate=pl.col("candidate").fill_null(""),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    (
        pl.concat(frames)
        .filter(
            pl.col("votes").is_not_null(),
            pl.col("precinct") != "",
            ~pl.col("precinct").str.contains(oe_precincts.TOTALS),
            ~pl.col("office").str.contains("RETAIN|RETENTION|JUDGE|JUSTICE|COURT"),
            affirmative.any().over("state", "day", "office", "district")
            & negative.any().over("state", "day", "office", "district"),
            affirmative | negative,
        )
        .group_by(
            pl.col("day").dt.year().alias("year"),
            "day",
            "state",
            "office",
            "district",
            "fips",
            pl.lit("").alias("jurisdiction"),
            "precinct",
            answer=pl.when(affirmative).then(pl.lit("YES")).otherwise(pl.lit("NO")),
        )
        .agg(pl.col("votes").sum())
        .sort("day", "state", "office", "district", "fips", "precinct", "answer")
        .write_parquet(PARSED / "openelections_recent.parquet")
    )


def emit() -> Path:
    archives = pl.read_parquet(PARSED / "archives.parquet").with_columns(
        key=pl.concat_str(pl.col("day").cast(pl.String), "state", pl.lit("question"), "question", separator=":")
    )
    returns = pl.concat(
        [
            pl.read_parquet(PARSED / "medsl.parquet").with_columns(origin=pl.lit("medsl")),
            pl.read_parquet(PARSED / "openelections.parquet").with_columns(origin=pl.lit("openelections")),
            pl.read_parquet(PARSED / "openelections_recent.parquet").with_columns(origin=pl.lit("openelections")),
        ],
        how="diagonal_relaxed",
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions by precinct 2000–2025 from the states' election statistics archives "
            "and MEDSL's and OpenElections' precinct returns",
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
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        defaults = pl.DataFrame(
            conn.execute(
                """select cx.value, substr(p.iso_code, 4), e.date_from, rs.election_id::text, rs.contest_id::text,
                          rs.source_id::text, upper(o.ballot_label), o.id::text, u.scheme_code, u.code,
                          v.value::bigint
                   from contest c
                   join election e on e.id = c.election_id
                   join polity p on p.id = e.polity_id and p.iso_code ~ '^US-..$'
                   join external_id cx on cx.entity_id = c.id and cx.scheme = 'us.contest'
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   join contest_option o on o.k = v.option_k
                   join reporting_unit u on u.k = v.unit_k
                   where c.kind = 'ballot_question' and e.date_from between %s and %s""",
                [date(FIRST, 1, 1), date(LAST, 12, 31)],
            ).fetchall(),
            schema=[
                "key",
                "state",
                "day",
                "election",
                "contest",
                "source",
                "answer",
                "option",
                "scheme",
                "code",
                "total",
            ],
            orient="row",
        ).with_columns(answer=pl.col("answer").replace_strict(ANSWERS, default=None))
        totals = (
            defaults.group_by("key", "state", "day")
            .agg(
                yes=pl.col("total").filter(pl.col("answer") == "YES").sum(),
                no=pl.col("total").filter(pl.col("answer") == "NO").sum(),
            )
            .filter((pl.col("yes") > 0) & (pl.col("no") > 0))
        )
        sources: dict[tuple[str, str | int], tuple[UUID, str]] = {
            ("medsl", year): (cs.source(medsl_state.PUBLISHER, title), MEDSL)
            for year, (title, _) in medsl_state_precincts.TITLES.items()
        } | {
            ("openelections", state): (cs.source("OpenElections", f"{state} precinct-level results"), OPENELECTIONS)
            for state in returns.filter(pl.col("origin") == "openelections")["state"].unique().to_list()
        }

        candidates: list[tuple[str, str, UUID | None, str, pl.DataFrame]] = [
            (key, state, None, ARCHIVE, rows)
            for (key, state), rows in archives.group_by("key", "state", maintain_order=True)
        ]
        question = ["origin", "year", "day", "state", "office", "district"]
        questions = returns.group_by(question).agg(
            yes=pl.col("votes").filter(pl.col("answer") == "YES").sum(),
            no=pl.col("votes").filter(pl.col("answer") == "NO").sum(),
        )
        matched = (
            questions.join(
                totals.rename({"day": "held"}).with_columns(year=pl.col("held").dt.year()),
                on=["state", "year", "yes", "no"],
            )
            .filter(pl.col("day").is_null() | (pl.col("day") == pl.col("held")))
            .filter(pl.len().over(question) == 1)
        )
        for origin, key in matched.filter(pl.len().over("origin", "key") > 1).select("origin", "key").unique().rows():
            print(f"{key}: several {origin} questions share its totals, skipped")
        matched = matched.filter(pl.len().over("origin", "key") == 1).sort("origin")
        print(f"{matched.group_by('origin').len().rows()} questions match a statewide question's totals exactly")
        picked = returns.join(matched.select(*question, "key"), on=question, nulls_equal=True)
        del returns
        for (origin, year, state, key), rows in picked.group_by("origin", "year", "state", "key", maintain_order=True):
            source, method = sources[(str(origin), year if origin == "medsl" else state)]
            candidates.append((str(key), str(state), source, method, rows))

        landed: dict[str, int] = {}
        skipped = 0
        minted: dict[str, tuple[str, str, date]] = {}
        values = []
        for key, state, source, method, rows in candidates:
            official = defaults.filter(pl.col("key") == key)
            if official.height == 0:
                continue
            if key in filled:
                print(f"{key}: already has a precinct set")
                continue
            if rows.filter(pl.col("fips").is_null() | (pl.col("precinct") == ""))["votes"].sum():
                print(
                    f"{key}: {rows.filter(pl.col('fips').is_null() | (pl.col('precinct') == ''))['votes'].sum()} "
                    "votes outside any county's precincts, skipped"
                )
                skipped += 1
                continue
            rows = rows.filter(pl.col("fips").is_not_null(), pl.col("precinct") != "")
            sums = rows.group_by("answer").agg(pl.col("votes").sum())
            expected = official.group_by("answer").agg(pl.col("total").sum())
            joined = expected.join(sums, on="answer", how="full", coalesce=True).fill_null(0)
            if (joined["votes"] != joined["total"]).any():
                print(f"{key}: precincts {sorted(sums.rows())} against totals {sorted(expected.rows())}, skipped")
                skipped += 1
                continue
            by_county = official["scheme"].unique().to_list() == ["us.county"]
            if by_county:
                differ = (
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
                if differ.height:
                    print(f"{key}: county sums differ in {differ['code'].n_unique()} counties, skipped")
                    skipped += 1
                    continue
            if rows["fips"].is_in(list(counties)).not_().any():
                print(f"{key}: precincts in counties not on file, skipped")
                skipped += 1
                continue
            first = official.row(0, named=True)
            options = dict(official.select("answer", "option").unique().iter_rows())
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            if result_set not in cs.minted:
                continue
            day = first["day"]
            cs.add(
                "result_set",
                id=result_set,
                election_id=UUID(first["election"]),
                contest_id=UUID(first["contest"]),
                stage="certified",
                kind="counted",
                status="active",
                source_id=source or UUID(first["source"]),
                is_default=False,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=method.format(counties=" and to each county's" if by_county else ""),
            )
            rows = rows.with_columns(
                code=pl.concat_str(pl.lit(f"{day.year}:{state}"), "fips", "jurisdiction", "precinct", separator=":")
            )
            codes = {}
            for code, fips in rows.select("code", "fips").unique().iter_rows():
                codes[code] = str(cs.unit("us.precinct", code, on=day))
                if UUID(codes[code]) in cs.minted:
                    minted.setdefault(code, (state, fips, day))
            values.append(
                rows.group_by("code", "answer")
                .agg(pl.col("votes").sum())
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").replace_strict(codes),
                    option_id=pl.col("answer").replace_strict(options),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
            landed[state] = landed.get(state, 0) + 1
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
        print(
            f"{sum(landed.values())} precinct sets {sorted(landed.items())}, {skipped} skipped, {len(minted)} new units"
        )
        return cs.write()
