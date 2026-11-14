import csv
import gzip
import hashlib
import json
import os
import resource
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from psycopg import Connection, sql

from ea.cache import _class
from ea.changeset import Changeset
from ea.store import DATA, connect

RELEASES = DATA / "releases"
GAPS = Path(__file__).parent / "coverage_inputs" / "coverage-gaps.tsv"
GAP_COLUMNS = [
    "polity",
    "state",
    "level",
    "stage",
    "years",
    "grain",
    "label",
    "ids",
    "category",
    "reason",
    "sources",
    "checked",
]
SHARED = ("ATTRIBUTION.md", "manifest.json", "electionsarchive.sql", "gaps.csv")
BUNDLES = {
    "cc-by": (
        "CC BY 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
        "Facts from public-record, open, attribution, and no-license-stated sources",
    ),
    "cc-by-sa": (
        "CC BY-SA 4.0",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        "Facts from share-alike sources (Wikipedia, Andrew Teale, and other CC BY-SA sources)",
    ),
    "odbl": (
        "ODbL 1.0",
        "https://opendatacommons.org/licenses/odbl/1-0/",
        "Facts and shapes from Open Database License sources",
    ),
}
HELD_PUBLISHERS = ("THEA",)
ENTITIES = [
    "entity",
    "polity",
    "polity_parent",
    "polity_relation",
    "polity_period",
    "scheme",
    "area",
    "area_code",
    "area_parent",
    "area_event",
    "area_figure",
    "boundary_set",
    "reporting_unit",
    "reporting_unit_area",
    "election",
    "contest",
    "ballot_question",
    "ballot_question_text",
    "contest_option",
    "candidacy",
    "candidacy_nomination",
    "measure",
    "channel",
    "electoral_system",
    "party",
    "party_color",
    "party_founding",
    "party_relation",
    "party_affiliation",
    "alliance",
    "alliance_member",
    "person",
    "person_party_membership",
    "body",
    "body_size",
    "office",
    "seat",
    "tenure",
    "name",
    "external_id",
    "citation",
    "census_tier",
    "census_tier_count",
    "census_class",
    "census_match",
    "census_date",
]
BY_RESULT_SET = {"result_set": "k", "outcome": "result_set_id", "value_locator": "result_set_k"}
SOURCE_COLUMNS = ["id", "kind", "publisher", "title", "url", "license", "license_url", "attribution"]
CHANGESET_COLUMNS = ["id", "pipeline", "pipeline_version", "kind", "summary", "created_at", "published_at"]
TYPES: dict[str, pl.DataType] = {
    "integer": pl.Int32(),
    "smallint": pl.Int16(),
    "bigint": pl.Int64(),
    "numeric": pl.Decimal(38, 18),
    "real": pl.Float32(),
    "double precision": pl.Float64(),
    "boolean": pl.Boolean(),
    "date": pl.Date(),
    "timestamp with time zone": pl.Datetime("us"),
}
CAST = {"USER-DEFINED", "ARRAY", "jsonb", "boolean"}
MIN_ROWS = 2_000_000
GEOMETRY_SOFT = 256 << 20
PART_BYTES = 512 << 20
MAX_BYTES = 2 << 30
MAX_FILES = 1000


def bundle(license: str | None, publisher: str | None, *, shapes: bool = False) -> str | None:
    kind = _class(license)
    if kind in {"restricted", "non-commercial", "revocable"} or (publisher or "").startswith(HELD_PUBLISHERS):
        return None
    if kind == "share-alike":
        return "odbl" if "ODbL" in (license or "") or "Open Database" in (license or "") else "cc-by-sa"
    if shapes and kind == "unknown":
        return None
    return "cc-by"


def _columns(
    conn: Connection, table: str, keep: list[str] | None = None
) -> list[tuple[str, sql.Composable, pl.DataType]]:
    out = []
    for name, kind, udt in conn.execute(
        "select column_name, data_type, udt_name from information_schema.columns "
        "where table_schema = 'public' and table_name = %s order by ordinal_position",
        [table],
    ).fetchall():
        if (keep is not None and name not in keep) or kind in {"daterange", "tstzrange", "tsvector", "bytea"}:
            continue
        if udt in {"geometry", "geography"}:
            continue
        column = sql.Identifier(name)
        if kind == "timestamp with time zone":
            expr = sql.SQL("to_char({} at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US')").format(column)
        elif kind in CAST:
            expr = sql.SQL("{}::text").format(column)
        else:
            expr = column
        out.append((name, expr, TYPES.get(kind, pl.String())))
    return out


def _drop(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def _mem(label: str) -> None:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 10
    cgroup = ""
    try:
        cgroup = f" cgroup={int(Path('/sys/fs/cgroup/memory.current').read_text()) >> 20}M"
    except OSError:
        pass
    print(f"mem {label} peak_rss={peak}M{cgroup}", flush=True)


class Writer:
    def __init__(self, conn: Connection, folder: Path) -> None:
        self.conn = conn
        self.folder = folder
        self.files: list[dict[str, object]] = []
        self.stems: dict[str, int] = defaultdict(int)

    def _record(self, path: Path, bundle: str, table: str, part: str, rows: int) -> None:
        size = path.stat().st_size
        if size >= MAX_BYTES:
            raise RuntimeError(f"{path.name} is {size} bytes, over the 2 GiB asset limit")
        digest = hashlib.sha256()
        with path.open("rb") as file:
            while chunk := file.read(1 << 24):
                digest.update(chunk)
        _drop(path)
        self.files.append(
            {
                "file": path.name,
                "bundle": bundle,
                "table": table,
                "part": part,
                "rows": rows,
                "bytes": size,
                "sha256": digest.hexdigest(),
            }
        )

    def table(
        self,
        bundle: str,
        table: str,
        part: str,
        columns: list[tuple[str, sql.Composable, pl.DataType]],
        query: sql.Composable,
    ) -> int:
        stem = f"{bundle}_{table}" + (f"_{part}" if part else "")
        select = sql.SQL("copy (select {} from ({}) q) to stdout (format csv, header)").format(
            sql.SQL(", ").join(sql.SQL("{} as {}").format(expr, sql.Identifier(name)) for name, expr, _ in columns),
            query,
        )
        csv = self.folder / f"{stem}.csv.gz"
        pieces: list[Path] = []
        header = b""
        size = 0
        file = None
        with gzip.open(csv, "wb", compresslevel=6) as target, self.conn.cursor().copy(select) as copy:
            for chunk in copy:
                data = bytes(chunk)
                target.write(data)
                if not header:
                    header = data
                    continue
                if file is None or size >= PART_BYTES:
                    if file is not None:
                        file.close()
                    pieces.append(self.folder / f".{stem}.{len(pieces) + 1}.csv")
                    file = pieces[-1].open("wb")
                    file.write(header)
                    size = 0
                file.write(data)
                size += len(data)
        if file is not None:
            file.close()
        if not pieces:
            csv.unlink()
            return 0
        schema = {name: kind for name, _, kind in columns}
        total = 0
        for number, piece in enumerate(pieces, 1):
            name = part if len(pieces) == 1 else f"{part}_{number}" if part else str(number)
            parquet = self.folder / (f"{bundle}_{table}" + (f"_{name}" if name else "") + ".parquet")
            frame = pl.read_csv(piece, schema=schema).with_columns(
                pl.col(n).dt.replace_time_zone("UTC") for n, _, kind in columns if kind == pl.Datetime("us")
            )
            frame.write_parquet(parquet, compression="zstd", statistics=True)
            rows = frame.height
            del frame
            piece.unlink()
            self._record(parquet, bundle, table, name, rows)
            total += rows
        _mem(stem)
        self._record(csv, bundle, table, part, total)
        rows = total
        return rows

    def geometry(self, bundle: str, scheme: str, years: tuple[str, str], rows: list[tuple]) -> None:
        part = scheme + "_" + (years[0] if years[0] == years[1] else f"{years[0]}-{years[1]}")
        stem = f"{bundle}_geometry_{part}"
        self.stems[stem] += 1
        if self.stems[stem] > 1:
            stem += f"_{self.stems[stem]}"
        frame = pl.DataFrame(
            rows,
            schema={
                "area_id": pl.String,
                "scheme_code": pl.String,
                "valid_from": pl.Date,
                "valid_to": pl.Date,
                "quality": pl.String,
                "source_id": pl.String,
                "changeset_id": pl.String,
                "geometry": pl.Binary,
                "label_point": pl.Binary,
            },
            orient="row",
        )
        rows.clear()
        geo = {
            "version": "1.1.0",
            "primary_column": "geometry",
            "columns": {
                "geometry": {"encoding": "WKB", "geometry_types": []},
                "label_point": {"encoding": "WKB", "geometry_types": ["Point"]},
            },
        }
        path = self.folder / f"{stem}.parquet"
        frame.write_parquet(path, compression="zstd", metadata={"geo": json.dumps(geo)})
        self._record(path, bundle, "geometry", part, frame.height)
        _mem(stem)


def _walk(
    polities: dict[str, tuple[str, str | None]], parents: dict[str, str], polity: str
) -> tuple[str | None, str | None]:
    state = None
    seen = set()
    current: str | None = polity
    while current is not None and current not in seen:
        seen.add(current)
        kind, iso = polities[current]
        if state is None and iso and iso.startswith("US-"):
            state = iso
        if kind in {"sovereign_state", "supranational"}:
            return iso, state
        current = parents.get(current)
    return None, state


def _key(polities: dict[str, tuple[str, str | None]], parents: dict[str, str], polity: str) -> str:
    country, state = _walk(polities, parents, polity)
    if country == "US" and state:
        return state.lower()
    return (country or "world").lower()


def summary(folder: Path, files: list[dict[str, object]]) -> dict[str, object]:
    paths: dict[str, list[str]] = defaultdict(list)
    tables: dict[str, dict[str, int]] = defaultdict(lambda: {"rows": 0, "bytes": 0, "files": 0})
    for entry in files:
        table, name = str(entry["table"]), str(entry["file"])
        tables[table]["bytes"] += int(str(entry["bytes"]))
        tables[table]["files"] += 1
        if name.endswith(".parquet"):
            paths[table].append(str(folder / name))
            tables[table]["rows"] += int(str(entry["rows"]))

    def scan(table: str, *columns: str) -> pl.LazyFrame:
        return pl.scan_parquet(paths[table], missing_columns="insert").select(columns)

    def year(column: str) -> pl.Expr:
        return pl.col(column).dt.year()

    def span(column: str = "year") -> list[pl.Expr]:
        return [pl.col(column).min().alias("first"), pl.col(column).max().alias("last")]

    def nested(frame: pl.DataFrame, sort: list[str], descending: list[bool]) -> dict[str, list[dict[str, object]]]:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in frame.sort(sort, descending=descending, nulls_last=True).to_dicts():
            grouped[str(row.pop("country"))].append(row)
        return grouped

    polity = scan("polity", "id", "kind", "iso_code").collect()
    polities = {row[0]: (row[1], row[2]) for row in polity.iter_rows()}
    parents: dict[str, str] = {}
    for child, parent in (
        scan("polity_parent", "polity_id", "parent_id", "valid_from")
        .sort("valid_from", nulls_last=False)
        .select("polity_id", "parent_id")
        .collect()
        .iter_rows()
    ):
        parents[child] = parent
    countries = {identifier: _walk(polities, parents, identifier)[0] or "world" for identifier in polities}
    places = pl.DataFrame(
        {
            "polity_id": list(countries),
            "country": list(countries.values()),
            "level": [polities[identifier][0] for identifier in countries],
        }
    ).lazy()
    roots = {identifier for identifier, (kind, iso) in polities.items() if iso in set(countries.values())}
    labels: dict[str, str] = {}
    for entity, text, lang in (
        scan("name", "entity_id", "text", "lang", "kind", "is_preferred")
        .filter(pl.col("entity_id").is_in(list(roots)) & (pl.col("kind") == "official"))
        .sort(pl.col("lang") != "en", pl.col("is_preferred").not_())
        .select("entity_id", "text", "lang")
        .collect()
        .iter_rows()
    ):
        iso = polities[entity][1]
        if iso is not None and iso not in labels:
            labels[iso] = text

    election = (
        scan("election", "id", "polity_id", "kind", "date_from")
        .join(places, on="polity_id", how="left")
        .with_columns(year("date_from").alias("year"))
        .collect()
    )
    result_set = (
        scan("result_set", "k", "election_id", "contest_id", "stage")
        .join(
            election.lazy().select(pl.col("id").alias("election_id"), "country", "year"), on="election_id", how="left"
        )
        .collect()
    )
    formulas = {
        identifier: json.loads(rules).get("formula") if rules else None
        for identifier, rules in scan("electoral_system", "id", "rules").collect().iter_rows()
    }
    contest = (
        scan("contest", "id", "election_id", "kind", "electoral_system_id")
        .join(
            election.lazy().select(pl.col("id").alias("election_id"), "country", "level", "year"),
            on="election_id",
            how="left",
        )
        .with_columns(
            pl.col("electoral_system_id")
            .replace_strict(formulas, default=None, return_dtype=pl.String)
            .alias("formula")
        )
        .collect()
    )
    schemes = scan("scheme", "code", "noun", "rank", "polity_id").collect()
    rows = (
        scan("results", "result_set_k", "unit_k", "measure", "channel")
        .join(
            scan("reporting_unit", "k", "scheme_code").rename({"k": "unit_k"}),
            on="unit_k",
            how="left",
        )
        .group_by("result_set_k", "scheme_code", "measure", "channel")
        .agg(pl.len().alias("rows"))
        .collect(engine="streaming")
    )
    per_set = (
        rows.group_by("result_set_k", "scheme_code")
        .agg(pl.col("rows").sum())
        .join(result_set.select(pl.col("k").alias("result_set_k"), "country", "year"), on="result_set_k", how="left")
    )
    question = (
        scan("ballot_question", "contest_id", "type")
        .join(contest.lazy().select(pl.col("id").alias("contest_id"), "country", "year"), on="contest_id", how="left")
        .collect()
    )
    shapes = (
        scan("geometry", "scheme_code", "area_id", "valid_from", "valid_to")
        .group_by("scheme_code")
        .agg(
            pl.len().alias("shapes"),
            pl.col("area_id").n_unique().alias("areas"),
            year("valid_from").min().alias("first"),
            year("valid_to").max().alias("last"),
        )
        .join(schemes.lazy().rename({"code": "scheme_code"}), on="scheme_code", how="left")
        .join(places.select("polity_id", "country"), on="polity_id", how="left")
        .select(pl.col("scheme_code").alias("scheme"), "noun", "country", "shapes", "areas", "first", "last")
        .sort("scheme")
        .collect()
    )

    held = election.join(result_set.select(pl.col("election_id").alias("id")).unique(), on="id", how="semi")
    election_kinds = election.group_by("country", "kind").agg(pl.len().alias("count"), *span())
    ballot_questions = question.group_by("country", "type").agg(
        pl.len().alias("count"), pl.col("year").min().alias("first")
    )
    contest_formulas = contest.drop_nulls("formula").group_by("country", "formula").agg(pl.len().alias("contests"))
    by_country = {
        "levels": nested(
            contest.group_by("country", "level").agg(pl.len().alias("contests"), *span()), ["contests"], [True]
        ),
        "units": nested(
            per_set.group_by("country", "scheme_code")
            .agg(
                pl.col("rows").sum().alias("results"),
                pl.col("result_set_k").n_unique().alias("result_sets"),
                *span(),
            )
            .join(schemes.select(pl.col("code").alias("scheme_code"), "noun", "rank"), on="scheme_code", how="left")
            .rename({"scheme_code": "scheme"}),
            ["rank", "results"],
            [False, True],
        ),
        "election_kinds": nested(election_kinds, ["count"], [True]),
        "ballot_questions": nested(ballot_questions, ["count"], [True]),
        "formulas": nested(contest_formulas, ["contests"], [True]),
    }
    for units in by_country["units"].values():
        for unit in units:
            unit.pop("rank")
    totals = (
        election.group_by("country")
        .agg(pl.len().alias("elections"))
        .join(held.group_by("country").agg(*span()), on="country", how="left")
        .join(contest.group_by("country").agg(pl.len().alias("contests")), on="country", how="left")
        .join(
            per_set.group_by("country").agg(
                pl.col("result_set_k").n_unique().alias("result_sets"), pl.col("rows").sum().alias("results")
            ),
            on="country",
            how="left",
        )
        .with_columns(pl.col("contests", "result_sets", "results").fill_null(0))
        .sort("results", "contests", descending=True)
    )

    def count(frame: pl.DataFrame, column: str, *extra: pl.Expr) -> list[dict[str, object]]:
        return frame.group_by(column).agg(pl.len().alias("count"), *extra).sort("count", descending=True).to_dicts()

    def coded(table: str, column: str) -> list[dict[str, object]]:
        codes = dict(scan(table, "id", "code").collect().iter_rows())
        return [
            {"code": codes.get(row[column], str(row[column])), "rows": row["rows"]}
            for row in rows.group_by(column).agg(pl.col("rows").sum()).sort("rows", descending=True).to_dicts()
        ]

    first, last = held.select(pl.col("date_from").min().alias("first"), pl.col("date_from").max().alias("last")).row(0)
    return {
        "totals": {
            label: tables[table]["rows"] if table in tables else 0
            for label, table in {
                "results": "results",
                "result_sets": "result_set",
                "contests": "contest",
                "elections": "election",
                "polities": "polity",
                "reporting_units": "reporting_unit",
                "candidacies": "candidacy",
                "people": "person",
                "parties": "party",
                "tenures": "tenure",
                "shapes": "geometry",
                "sources": "source",
                "changesets": "changeset",
            }.items()
        },
        "span": {"first": first.isoformat() if first else None, "last": last.isoformat() if last else None},
        "countries": [
            {
                "code": row["country"],
                "name": labels.get(row["country"], row["country"]),
                **{key: value for key, value in row.items() if key != "country"},
                **{key: groups.get(row["country"], []) for key, groups in by_country.items()},
            }
            for row in totals.to_dicts()
        ],
        "election_kinds": count(election, "kind", *span()),
        "contest_kinds": count(contest, "kind"),
        "ballot_questions": count(question, "type", pl.col("year").min().alias("first")),
        "formulas": [
            {"formula": row["formula"], "contests": row["count"]}
            for row in count(contest.drop_nulls("formula"), "formula")
        ],
        "measures": coded("measure", "measure"),
        "channels": coded("channel", "channel"),
        "stages": [{"stage": row["stage"], "count": row["count"]} for row in count(result_set, "stage")],
        "shapes": shapes.to_dicts(),
        "tables": [
            {"table": table, **sizes} for table, sizes in sorted(tables.items(), key=lambda item: -item[1]["rows"])
        ],
    }


def _chunks(decades: dict[int, int]) -> list[tuple[str, list[int]]]:
    if sum(decades.values()) < 2 * MIN_ROWS:
        return [("all", sorted(decades))]
    chunks: list[list[int]] = []
    rows = 0
    for decade in sorted(decades):
        if not chunks or rows >= MIN_ROWS:
            chunks.append([])
            rows = 0
        chunks[-1].append(decade)
        rows += decades[decade]
    if len(chunks) > 1 and rows < MIN_ROWS:
        chunks[-2].extend(chunks.pop())

    def label(chunk: list[int]) -> str:
        if chunk == [-1]:
            return "undated"
        dated = [decade for decade in chunk if decade >= 0]
        return f"{dated[0]}-{dated[-1] + 9}" + ("_undated" if -1 in chunk else "")

    return [(label(chunk), chunk) for chunk in chunks]


def _select(table: str, columns: list[tuple[str, sql.Composable, pl.DataType]]) -> sql.Composable:
    query = sql.SQL("select * from {} t").format(sql.Identifier(table))
    if any(name == "changeset_id" for name, _, _ in columns):
        query = sql.SQL("{} where t.changeset_id in (select id from release_published)").format(query)
    return query


def build(calver: str | None = None) -> str:
    calver = calver or datetime.now(UTC).strftime("%Y.%m.%d")
    folder = RELEASES / calver
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    with connect() as conn:
        conn.execute("set transaction isolation level repeatable read")
        conn.execute("set local work_mem = '512MB'")
        watermark, published_at = conn.execute(
            "select id::text, published_at from changeset where status = 'published' order by published_at desc limit 1"
        ).fetchone() or (None, None)
        conn.execute(
            "create temp table release_published as select id from changeset where status = 'published'; "
            "alter table release_published add primary key (id)"
        )
        sources = {
            row[0]: row
            for row in conn.execute(
                "select id::text, publisher, title, url, license, license_url, attribution from source"
            )
        }
        facts = {source: bundle(row[4], row[1]) for source, row in sources.items()}
        shapes = {source: bundle(row[4], row[1], shapes=True) for source, row in sources.items()}
        conn.execute("create temp table release_source (id uuid primary key, bundle text, shapes text)")
        with conn.cursor().copy("copy release_source from stdin") as copy:
            for source in sources:
                copy.write_row((source, facts[source], shapes[source]))
        polities = {
            row[0]: (row[1], row[2]) for row in conn.execute("select id::text, kind::text, iso_code from polity")
        }
        parents = dict(
            conn.execute(
                "select distinct on (polity_id) polity_id::text, parent_id::text from polity_parent "
                "order by polity_id, valid_from desc nulls last"
            ).fetchall()
        )
        counts = dict(conn.execute("select result_set_k, count(*) from result_value group by 1").fetchall())
        sets = conn.execute(
            "select rs.k, rs.source_id::text, (extract(year from e.date_from)::int / 10) * 10, e.polity_id::text "
            "from result_set rs join election e on e.id = rs.election_id "
            "where rs.changeset_id in (select id from release_published)"
        ).fetchall()
        groups: dict[tuple[str, str], dict[int, int]] = defaultdict(lambda: defaultdict(int))
        placed = []
        for k, source, decade, polity in sets:
            target = facts.get(source) if source else "cc-by"
            if target is None:
                continue
            key = _key(polities, parents, polity)
            decade = -1 if decade is None else decade
            groups[(target, key)][decade] += counts.get(k, 0)
            placed.append((k, target, key, decade))
        parts = {
            (target, key, decade): label
            for (target, key), decades in groups.items()
            for label, chunk in _chunks(decades)
            for decade in chunk
        }
        conn.execute("create temp table release_set (k int primary key, bundle text, part text)")
        with conn.cursor().copy("copy release_set from stdin") as copy:
            for k, target, key, decade in placed:
                copy.write_row((k, target, f"{key}_{parts[(target, key, decade)]}"))
        conn.execute("create index on release_set (bundle, part); analyze release_set")
        result_parts = sorted(
            {(target, f"{key}_{label}") for (target, key, _), label in parts.items()},
        )
        planned = 2 * (len(result_parts) + len(ENTITIES) + len(BY_RESULT_SET) + 2)
        if planned > MAX_FILES:
            raise RuntimeError(f"{planned} files planned, over the {MAX_FILES}-file release limit")

        writer = Writer(conn, folder)
        value_columns = _columns(conn, "result_value")
        for target, part in result_parts:
            writer.table(
                target,
                "results",
                part,
                value_columns,
                sql.SQL(
                    "select v.* from result_value v join release_set s on s.k = v.result_set_k "
                    "where s.bundle = {} and s.part = {} "
                    "order by v.result_set_k, v.unit_k, v.option_k, v.measure, v.channel, v.count_round"
                ).format(sql.Literal(target), sql.Literal(part)),
            )
            print(f"results {target} {part}", flush=True)
        for table, column in BY_RESULT_SET.items():
            columns = _columns(conn, table)
            join = "k" if column.endswith("_k") or column == "k" else "id"
            for target in BUNDLES:
                writer.table(
                    target,
                    table,
                    "",
                    columns,
                    sql.SQL(
                        "select t.* from {} t join result_set rs on rs.{} = t.{} "
                        "join release_set s on s.k = rs.k where s.bundle = {}"
                    ).format(sql.Identifier(table), sql.SQL(join), sql.Identifier(column), sql.Literal(target)),
                )
            print(table, flush=True)
        cited = ["result_set"]
        for table in ENTITIES:
            columns = _columns(conn, table)
            query = _select(table, columns)
            if any(name == "source_id" for name, _, _ in columns):
                cited.append(table)
                for target in BUNDLES:
                    writer.table(
                        target,
                        table,
                        "",
                        columns,
                        sql.SQL(
                            "select q.* from ({}) q left join release_source r on r.id = q.source_id "
                            "where coalesce(r.bundle, case when q.source_id is null then 'cc-by' end) = {}"
                        ).format(query, sql.Literal(target)),
                    )
            else:
                writer.table("cc-by", table, "", columns, query)
            print(table, flush=True)
        used = {
            row[0]
            for row in conn.execute(
                sql.SQL(" union ").join(
                    sql.SQL("select distinct source_id::text from {}").format(sql.Identifier(table)) for table in cited
                )
            )
            if row[0] is not None and facts.get(row[0]) is not None
        }
        used |= {
            row[0]
            for row in conn.execute(
                "select distinct g.source_id::text from area_geometry g join release_source r on r.id = g.source_id "
                "where r.shapes is not null"
            )
        }
        writer.table(
            "cc-by",
            "source",
            "",
            _columns(conn, "source", SOURCE_COLUMNS),
            sql.SQL("select * from source where id = any({})").format(sql.Literal(sorted(used))),
        )
        writer.table(
            "cc-by",
            "changeset",
            "",
            _columns(conn, "changeset", CHANGESET_COLUMNS),
            sql.SQL("select * from changeset where status = 'published'"),
        )
        _geometry(conn, writer)
    if len(writer.files) + len(SHARED) + 1 > MAX_FILES:
        raise RuntimeError(f"{len(writer.files) + len(SHARED) + 1} files, over the {MAX_FILES}-file release limit")

    lines = [
        f"# Attribution\n\nelectionsarchive.com release {calver}. Each bundle carries the license below; credit these sources when you use it.\n"
    ]
    for target, (name, url, scope) in BUNDLES.items():
        lines.append(f"\n## {target}: {name}\n\n{scope}. License: {url}\n")
        for source in sorted(
            (sources[source] for source in used if target in (facts[source], shapes[source])),
            key=lambda row: ((row[1] or "").casefold(), (row[2] or "").casefold()),
        ):
            _, publisher, title, url, license, _, attribution = source
            credit = attribution or ", ".join(part for part in (publisher, title) if part)
            lines.append(f"- {credit}" + (f", {url}" if url else "") + (f" ({license})" if license else ""))
    (folder / "ATTRIBUTION.md").write_text("\n".join(lines) + "\n")
    manifest = {
        "release": calver,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "watermark_changeset_id": watermark,
        "watermark_published_at": published_at.isoformat() if published_at else None,
        "bundles": {
            target: {"license": name, "license_url": url, "scope": scope}
            for target, (name, url, scope) in BUNDLES.items()
        },
        "held": [
            "sources classed restricted, non-commercial, or revocable",
            "THEA's sets, until THEA grants permission",
            "shapes from sources that state no license (IBGE census tracts and neighborhoods, TKGM neighborhoods)",
            "rows written by changesets not yet published",
        ],
        "gaps": gaps(folder),
        "summary": summary(folder, writer.files),
        "files": writer.files,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    tables: dict[str, list[str]] = defaultdict(list)
    for entry in writer.files:
        if str(entry["file"]).endswith(".parquet"):
            tables[str(entry["table"])].append(str(entry["file"]))
    (folder / "electionsarchive.sql").write_text(
        "".join(
            f'create or replace view "{table}" as select * from read_parquet(['
            + ", ".join(f"'{name}'" for name in sorted(names))
            + "], union_by_name = true);\n"
            for table, names in sorted(tables.items())
        )
        + "create or replace view gaps as select * from read_csv('gaps.csv', all_varchar = true);\n"
    )
    sums = [f"{entry['sha256']}  {entry['file']}" for entry in writer.files]
    for name in SHARED:
        sums.append(f"{hashlib.sha256((folder / name).read_bytes()).hexdigest()}  {name}")
    (folder / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    size = sum(int(str(entry["bytes"])) for entry in writer.files)
    return f"{folder}: {len(writer.files) + len(SHARED) + 1} files, {size / (1 << 30):.2f} GiB"


def gaps(folder: Path) -> dict[str, object]:
    with GAPS.open(newline="") as handle:
        rows = [
            row
            for row in csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t")
            if row.get("polity")
        ]
    with (folder / "gaps.csv").open("w", newline="") as handle:
        out = csv.DictWriter(handle, fieldnames=GAP_COLUMNS, extrasaction="ignore", lineterminator="\n")
        out.writeheader()
        out.writerows(rows)
    categories: dict[str, int] = defaultdict(int)
    polities: dict[str, int] = defaultdict(int)
    for row in rows:
        categories[row["category"]] += 1
        polities[row["polity"].upper()] += 1
    return {
        "file": "gaps.csv",
        "rows": len(rows),
        "categories": dict(sorted(categories.items(), key=lambda item: -item[1])),
        "polities": dict(sorted(polities.items(), key=lambda item: -item[1])),
    }


def record(calver: str | None = None) -> str:
    calver = calver or datetime.now(UTC).strftime("%Y.%m.%d")
    manifest = json.loads((RELEASES / calver / "manifest.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline="release",
            version="1",
            summary=f"Record release {calver} at watermark {manifest['watermark_changeset_id']}",
            polity=None,
            kind="release",
        )
        cs.add(
            "release",
            calver=manifest["release"],
            watermark_changeset_id=manifest["watermark_changeset_id"],
            manifest=manifest,
        )
        return str(cs.write())


def _geometry(conn: Connection, writer: Writer) -> None:
    query = (
        "select g.area_id::text, a.scheme_code, g.valid_from, g.valid_to, g.quality::text, g.source_id::text, "
        "g.changeset_id::text, st_asbinary(g.geom), st_asbinary(g.label_point), r.shapes "
        "from area_geometry g join area a on a.id = g.area_id "
        "left join release_source r on r.id = g.source_id "
        "where g.changeset_id in (select id from release_published) "
        "and coalesce(r.shapes, case when g.source_id is null then 'cc-by' end) is not null "
        "order by r.shapes, a.scheme_code, g.valid_from nulls first, g.area_id"
    )
    batch: list[tuple] = []
    size = 0
    current = ("", "")
    years = ("", "")
    with conn.cursor("release_geometry", binary=True) as cursor:
        cursor.itersize = 20_000
        cursor.execute(query)
        for *row, target in cursor:
            target = target or "cc-by"
            year = str(row[2].year) if row[2] else "undated"
            group = (target, row[1])
            if batch and (group != current or size >= GEOMETRY_SOFT):
                writer.geometry(current[0], current[1], years, batch)
                print(f"geometry {current[0]} {current[1]} {years[0]}-{years[1]}", flush=True)
                batch, size = [], 0
            if not batch:
                current, years = group, (year, year)
            years = (years[0], year)
            batch.append(tuple(row[:7]) + (bytes(row[7]), bytes(row[8]) if row[8] is not None else None))
            size += len(row[7])
        if batch:
            writer.geometry(current[0], current[1], years, batch)
