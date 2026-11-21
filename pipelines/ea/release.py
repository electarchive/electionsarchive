import csv
import gzip
import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
from psycopg import Connection, sql

from ea.cache import _class
from ea.changeset import Changeset
from ea.completeness import ATTESTATIONS, FORMAT, INPUTS, TARGETS, US_FEDERAL, compute
from ea.store import DATA, checkout, connect

RELEASES = DATA / "releases"
DATAVERSE = INPUTS / "dataverse.json"
VERSION = re.compile(r"^\d{4}\.(0[1-9]|1[0-2])(\.[1-9]\d*)?(-rc\.[1-9]\d*)?$")
REPOSITORY = "electarchive/electionsarchive"
BOX = "storagebox:releases"
SITE = "https://electionsarchive.com/"
KEEP = ("plan.json", "review.json")
FREE = 40 << 30
EXPECTED = 20 << 30
VERDICTS = ("pass", "fail")
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
SHARED = ("ATTRIBUTION.md", "manifest.json", "electionsarchive.sql", "gaps.csv", "CHANGES.md")
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
    "census_world",
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
        order: list[str],
    ) -> int:
        stem = f"{bundle}_{table}" + (f"_{part}" if part else "")
        select = sql.SQL("copy (select {} from ({}) q order by {}) to stdout (format csv, header)").format(
            sql.SQL(", ").join(sql.SQL("{} as {}").format(expr, sql.Identifier(name)) for name, expr, _ in columns),
            query,
            sql.SQL(", ").join(sql.SQL("q.{}").format(sql.Identifier(name)) for name in order),
        )
        csv = self.folder / f"{stem}.csv.gz"
        pieces: list[Path] = []
        header = b""
        size = 0
        file = None
        with (
            csv.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0) as target,
            self.conn.cursor().copy(select) as copy,
        ):
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


def _order(conn: Connection, table: str, columns: list[tuple[str, sql.Composable, pl.DataType]]) -> list[str]:
    keys = [
        row[0]
        for row in conn.execute(
            "select a.attname from pg_index i "
            "join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey::int2[]) "
            "where i.indrelid = %s::regclass and i.indisprimary "
            "order by array_position(i.indkey::int2[], a.attnum)",
            [table],
        )
    ]
    exported = {name for name, _, _ in columns}
    return keys if keys and set(keys) <= exported else [name for name, _, _ in columns]


def _select(table: str, columns: list[tuple[str, sql.Composable, pl.DataType]]) -> sql.Composable:
    query = sql.SQL("select * from {} t").format(sql.Identifier(table))
    if any(name == "changeset_id" for name, _, _ in columns):
        query = sql.SQL("{} where t.changeset_id in (select id from release_published)").format(query)
    return query


PREVIOUS_SQL = """
select r.calver, r.watermark_changeset_id::text, w.published_at,
    coalesce(jsonb_array_length(r.manifest -> 'files'), 0)::int,
    coalesce((select sum((f ->> 'bytes')::bigint) from jsonb_array_elements(r.manifest -> 'files') f), 0)::bigint
from release r
join changeset w on w.id = r.watermark_changeset_id
order by r.created_at desc
limit 1
"""
CHANGES_SQL = """
select coalesce(cs.pipeline, cs.kind::text), count(distinct cs.id)::int, count(rs.k)::int, count(rs.supersedes_id)::int
from changeset cs
left join result_set rs on rs.changeset_id = cs.id
where cs.status = 'published' and cs.published_at > coalesce(%(since)s::timestamptz, '-infinity')
group by 1
order by 2 desc, 1
"""
KINDS_SQL = """
select kind::text, count(*)::int
from changeset
where status = 'published' and published_at > coalesce(%(since)s::timestamptz, '-infinity')
group by 1
order by 2 desc, 1
"""
SAMPLE_SQL = """
with picked as (
    select lower(p.iso_code) as code, c.id, c.election_id, k.key, rs.source_id, e.date_from,
        max(e.date_from) over (partition by k.id) as latest,
        k.valid_to is null or k.valid_to > current_date as current
    from contest_class cc
    join census_class k on k.id = cc.class_id
    join polity p on p.id = k.polity_id
    join contest c on c.id = cc.contest_id
    join election e on e.id = c.election_id
    join lateral (
        select source_id from result_set
        where contest_id = c.id and is_default and status = 'active'
        order by k
        limit 1
    ) rs on true
    where lower(p.iso_code) = any(%(codes)s)
        and (k.goal = 'national' or (p.iso_code = 'US' and k.key = any(%(federal)s)))
),
ranked as (
    select *, row_number() over (partition by code order by md5(%(version)s || id::text)) as n
    from picked
    where (date_from = latest and current) or not code = any(%(latest)s)
)
select x.code, ea_public_id('contest'::entity_type, x.id), x.key, x.date_from,
    coalesce(
        (select s.path from slug s where s.entity_id = x.id and s.is_current order by s.path limit 1),
        (select s.path from slug s where s.entity_id = x.election_id and s.is_current order by s.path limit 1)
    ),
    src.url, coalesce(src.attribution, src.publisher, src.title)
from ranked x
join source src on src.id = x.source_id
where x.n <= %(per)s
order by x.code, x.n
"""
MIGRATION_SQL = "select name from migrations order by applied_at desc nulls last, id desc limit 1"
SAMPLE = 3


def _kind(version: str) -> tuple[str, Path]:
    if not VERSION.match(version):
        raise ValueError(f"{version}: expected YYYY.MM, YYYY.MM.N for a fix, or either with -rc.N for a candidate")
    base = version.split("-")[0]
    return ("rc" if "-rc." in version else "fix" if base.count(".") == 2 else "release"), RELEASES / version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 24):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, object]:
    found = json.loads(path.read_text())
    if not isinstance(found, dict):
        raise TypeError(f"{path}: expected a JSON object")
    return found


def _write(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n")
    temporary.replace(path)


def _list(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sections(conn: Connection, version: str, kind: str, folder: Path) -> dict[str, object]:
    if conn.execute("select from release where calver = %s", [version]).fetchone() is not None:
        raise ValueError(f"{version} is already recorded in the release table; labels are never reused")
    found = compute(conn)
    previous = conn.execute(PREVIOUS_SQL).fetchone()
    since = previous[2] if previous is not None else None
    entries = _list(found["polities"])
    rising = [
        str(entry["code"])
        for entry in entries
        if (tier := _int(entry["tier"])) is not None and tier >= 1 and tier > (_int(entry["released_tier"]) or 0)
    ]
    latest = [str(entry["code"]) for entry in entries if str(entry["code"]) in rising and entry["tier"] == 1]
    answered: dict[str, dict[str, object]] = {}
    old: dict[str, object] = {}
    if (folder / "review.json").exists():
        old = _json(folder / "review.json")
        answered = {str(item["contest"]): item for item in _list(old.get("items"))}
    items: list[dict[str, object]] = []
    for code, contest, key, held_on, path, url, source in conn.execute(
        SAMPLE_SQL,
        {"version": version, "codes": rising, "latest": latest, "federal": sorted(US_FEDERAL), "per": SAMPLE},
    ).fetchall():
        before = answered.get(contest, {})
        items.append(
            {
                "polity": code,
                "contest": contest,
                "class": key,
                "date": held_on.isoformat() if held_on is not None else None,
                "page": SITE + path if path else None,
                "source": source,
                "source_url": url,
                "verdict": before.get("verdict"),
                "note": before.get("note"),
            }
        )
    review = {"version": version, "by": old.get("by"), "on": old.get("on"), "items": items}
    states: dict[str, str] = {}
    for code in rising:
        verdicts = [item["verdict"] for item in items if item["polity"] == code]
        states[code] = (
            "fail" if "fail" in verdicts else "pass" if verdicts and all(v == "pass" for v in verdicts) else "pending"
        )
    polities: list[dict[str, object]] = []
    changed: list[dict[str, object]] = []
    for entry in entries:
        code = str(entry["code"])
        computed = _int(entry["tier"]) or 0
        before = _int(entry["released_tier"])
        state = states.get(code)
        held = state == "fail" or (state == "pending" and kind != "rc")
        tier = (before if before is not None else 0) if held else computed
        polities.append(
            {
                **{key: value for key, value in entry.items() if key != "released_tier"},
                "tier": tier,
                "computed_tier": computed,
                "previous_tier": before,
                "provisional": tier < 1,
                "review": state,
            }
        )
        if before is not None and before != tier:
            changed.append({"code": code, "name": entry["name"], "from": before, "to": tier})
    final = {str(entry["code"]): int(str(entry["tier"])) for entry in polities}
    target = found["target"]
    assert isinstance(target, dict)
    reached = [
        {**item, "reached": final.get(str(item["polity"]), -1) >= int(str(item["tier"]))}
        for item in _list(target["items"])
    ]
    pipelines = [
        {"pipeline": pipeline, "changesets": count, "result_sets": sets, "superseding": superseding}
        for pipeline, count, sets, superseding in conn.execute(CHANGES_SQL, {"since": since}).fetchall()
    ]
    return {
        "format": FORMAT,
        "version": version,
        "kind": kind,
        "computed_at": found["computed_at"],
        "coverage_built_at": found["coverage_built_at"],
        "watermark": found["watermark"],
        "previous": {
            "version": previous[0],
            "watermark_changeset_id": previous[1],
            "watermark_published_at": previous[2].isoformat(),
            "files": previous[3],
            "bytes": previous[4],
        }
        if previous is not None
        else None,
        "tiers": found["tiers"],
        "target": {**target, "items": reached, "met": all(item["reached"] for item in reached)},
        "polities": polities,
        "changes": {
            "since": previous[0] if previous is not None else None,
            "baseline": previous is not None and all(entry["released_tier"] is None for entry in entries),
            "changesets": sum(int(row["changesets"]) for row in pipelines),
            "result_sets": sum(int(row["result_sets"]) for row in pipelines),
            "superseding": sum(int(row["superseding"]) for row in pipelines),
            "kinds": dict(conn.execute(KINDS_SQL, {"since": since}).fetchall()),
            "tiers": changed,
            "pipelines": pipelines,
        },
        "review": review,
    }


def plan(version: str) -> str:
    kind, folder = _kind(version)
    with connect() as conn:
        conn.execute("set transaction isolation level repeatable read, read only")
        sections = _sections(conn, version, kind, folder)
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / "plan.json", {**sections, "planned_at": datetime.now(UTC).isoformat(timespec="seconds")})
    _write(folder / "review.json", sections["review"])
    return _report(folder, sections)


def _report(folder: Path, sections: dict[str, object]) -> str:
    target = sections["target"]
    changes = sections["changes"]
    review = sections["review"]
    assert isinstance(target, dict) and isinstance(changes, dict) and isinstance(review, dict)
    tiers: dict[int, int] = defaultdict(int)
    for entry in _list(sections["polities"]):
        tiers[int(str(entry["tier"]))] += 1
    ladder = ", ".join(f"T{tier}: {tiers[tier]}" for tier in range(6) if tiers[tier])
    goal = ", ".join(
        f"{item['polity']} T{item['tier']} {'reached' if item['reached'] else 'missed'}"
        for item in _list(target["items"])
    )
    return (
        f"{folder}: {ladder}, not surveyed: {tiers[-1]}; target {target.get('version')} "
        f"{'met' if target['met'] else 'partial'} ({goal}); {changes['changesets']:,} changesets and "
        f"{changes['result_sets']:,} result sets since {changes['since']}; "
        f"{len(_list(review['items']))} contests to review"
    )


def _names(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def _changes(manifest: dict[str, object]) -> str:
    target = manifest["target"]
    changes = manifest["changes"]
    watermark = manifest["watermark"]
    assert isinstance(target, dict) and isinstance(changes, dict) and isinstance(watermark, dict)
    polities = _list(manifest["polities"])
    names = {str(entry["code"]): str(entry["name"]) for entry in polities}
    previous = str(changes["since"]) if changes["since"] is not None else "none (first release with tiers)"
    items = _list(target["items"])
    by_tier: dict[int, list[str]] = defaultdict(list)
    for item in items:
        by_tier[int(str(item["tier"]))].append(names.get(str(item["polity"]), str(item["polity"]).upper()))
    wanted = "; ".join(f"{_names(group)} at T{tier}" for tier, group in sorted(by_tier.items()))
    missed = _names(
        [
            f"{names.get(str(item['polity']), str(item['polity']).upper())} reached T"
            + str(next((entry["tier"] for entry in polities if entry["code"] == item["polity"]), -1))
            for item in items
            if not item["reached"]
        ]
    )
    goal = (
        f"Target met: {wanted}."
        if target["met"]
        else f"Partial release. The target was {wanted}; {missed}."
        if items
        else ""
    )
    status = {
        "rc": f"Release candidate for {target.get('version')}. A candidate is for checking only, and the next scheduled release replaces it.",
        "fix": f"Fix for {str(manifest['version']).split('-')[0].rsplit('.', 1)[0]}: {manifest.get('reason')}.",
    }.get(str(manifest["kind"]), "")
    lines = [
        f"# electionsarchive.com data {manifest['version']}",
        "",
        "\n\n".join(line for line in (status, goal) if line),
        "",
        (
            f"This release holds every result the archive had published by {str(watermark['published_at'])[:10]} "
            f"(changeset `{watermark['changeset_id']}`). Data format: {manifest['format']}. Previous release: {previous}."
        ),
        "",
        "## Completeness by country",
        "",
        "Each country has a tier, computed from the coverage census. A country reaches a tier only when it meets every tier below it.",
        "",
        "- T0, surveyed: the census lists the country's national offices and bodies.",
        "- T1, latest national: the archive holds the latest national election of every office and body the census lists.",
        "- T2, national history: the archive holds every national election back to the first one, and a person has checked the census against the official record.",
        "- T3, finest unit: national results reach the finest unit the official source publishes.",
        "- T4, subnational: state, province, and regional elections, referendums, and European Parliament elections are complete.",
        "- T5, local: local elections are complete.",
        "",
        "A contest counts as done when the archive holds it at the required level of detail, or when the gap register explains why no one can get it. Countries below T1 are provisional.",
        "",
    ]
    for tier in range(5, -1, -1):
        group = sorted(str(entry["name"]) for entry in polities if entry["tier"] == tier)
        if group:
            lines.append(f"- T{tier} ({len(group)}): {', '.join(group)}")
    lines += ["", f"## Tier changes since {previous}", ""]
    moved = _list(changes["tiers"])
    if changes["baseline"]:
        lines.append(f"{previous} had no tiers, so this release sets the baseline.")
    elif moved:
        lines += [f"- {row['name']}: T{row['from']} to T{row['to']}." for row in moved]
    else:
        lines.append("No country changed tier.")
    pipelines = _list(changes["pipelines"])
    lines += [
        "",
        f"## Changes since {previous}",
        "",
        (
            f"{int(str(changes['changesets'])):,} changesets from {len(pipelines):,} pipelines were published since {previous}. "
            f"They added {int(str(changes['result_sets'])):,} result sets, {int(str(changes['superseding'])):,} of which replace an earlier set."
        ),
        "",
    ]
    files = _list(manifest["files"])
    size = sum(int(str(entry["bytes"])) for entry in files)
    lines += [
        "## Files",
        "",
        (
            f"{len(files) + len(SHARED) + 1:,} files, {size / (1 << 30):.1f} GiB in total, in three license bundles. "
            "ATTRIBUTION.md credits each source. SHA256SUMS lists a checksum for every file, and manifest.json describes each file, table, and country."
        ),
    ]
    return "\n".join(lines) + "\n"


def build(version: str, reason: str | None = None) -> str:
    kind, folder = _kind(version)
    if kind == "fix" and not reason:
        raise ValueError(f"{version} is a fix release; give --reason for the changelog")
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.iterdir():
        if path.name in KEEP:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    with connect() as conn:
        conn.execute("set transaction isolation level repeatable read")
        conn.execute("set local work_mem = '512MB'")
        sections = _sections(conn, version, kind, folder)
        previous = sections["previous"]
        expected = int(str(previous["bytes"])) if isinstance(previous, dict) and previous["bytes"] else EXPECTED
        free = shutil.disk_usage(folder).free
        if free < FREE + expected:
            raise RuntimeError(
                f"{free / (1 << 30):.0f} GiB free; a build needs {(FREE + expected) / (1 << 30):.0f} GiB "
                f"(40 GiB to spare plus about {expected / (1 << 30):.0f} GiB)"
            )
        watermark = sections["watermark"]
        assert isinstance(watermark, dict)
        migration = conn.execute(MIGRATION_SQL).fetchone()
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
        value_order = _order(conn, "result_value", value_columns)
        for target, part in result_parts:
            writer.table(
                target,
                "results",
                part,
                value_columns,
                sql.SQL(
                    "select v.* from result_value v join release_set s on s.k = v.result_set_k "
                    "where s.bundle = {} and s.part = {}"
                ).format(sql.Literal(target), sql.Literal(part)),
                value_order,
            )
            print(f"results {target} {part}", flush=True)
        for table, column in BY_RESULT_SET.items():
            columns = _columns(conn, table)
            order = _order(conn, table, columns)
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
                    order,
                )
            print(table, flush=True)
        cited = ["result_set"]
        for table in ENTITIES:
            columns = _columns(conn, table)
            order = _order(conn, table, columns)
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
                        order,
                    )
            else:
                writer.table("cc-by", table, "", columns, query, order)
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
            ["id"],
        )
        writer.table(
            "cc-by",
            "changeset",
            "",
            _columns(conn, "changeset", CHANGESET_COLUMNS),
            sql.SQL("select * from changeset where status = 'published'"),
            ["id"],
        )
        _geometry(conn, writer)
    if len(writer.files) + len(SHARED) + 1 > MAX_FILES:
        raise RuntimeError(f"{len(writer.files) + len(SHARED) + 1} files, over the {MAX_FILES}-file release limit")

    lines = [
        f"# Attribution\n\nelectionsarchive.com release {version}. Each bundle carries the license below; credit these sources when you use it.\n"
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
        "format": FORMAT,
        "version": version,
        "release": version,
        "kind": kind,
        "reason": reason,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_commit": checkout()["sha"],
        "migration": migration[0] if migration is not None else None,
        "inputs": {path.name: _sha256(path) for path in (TARGETS, ATTESTATIONS)},
        "watermark_changeset_id": watermark["changeset_id"],
        "watermark_published_at": watermark["published_at"],
        **{
            key: sections[key]
            for key in (
                "watermark",
                "previous",
                "coverage_built_at",
                "tiers",
                "target",
                "polities",
                "changes",
                "review",
            )
        },
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
    (folder / "CHANGES.md").write_text(_changes(manifest))
    _write(folder / "review.json", sections["review"])
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
    return f"{_report(folder, sections)}; {len(writer.files) + len(SHARED) + 1} files, {size / (1 << 30):.2f} GiB"


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


def _sums(folder: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        digest, _, name = line.partition("  ")
        found[name] = digest
    return found


def check(version: str) -> str:
    kind, folder = _kind(version)
    manifest = _json(folder / "manifest.json")
    checks: list[dict[str, object]] = []

    def note(name: str, problems: list[str]) -> None:
        checks.append({"check": name, "ok": not problems, "problems": problems[:50], "count": len(problems)})

    required = ("format", "version", "kind", "code_commit", "watermark", "tiers", "target", "polities", "changes")
    note(
        "manifest",
        [f"missing {key}" for key in required if key not in manifest]
        + ([f"format {manifest.get('format')}, expected {FORMAT}"] if manifest.get("format") != FORMAT else [])
        + ([f"version {manifest.get('version')}"] if manifest.get("version") != version else [])
        + ([f"kind {manifest.get('kind')}, expected {kind}"] if manifest.get("kind") != kind else []),
    )
    files = _list(manifest.get("files"))
    sums = _sums(folder)
    named = {str(entry["file"]): str(entry["sha256"]) for entry in files}
    present = {path.name for path in folder.iterdir() if path.is_file()}
    problems = [f"{name} is listed but missing" for name in sorted(set(sums) - present)]
    problems += [f"{name} is not in SHA256SUMS" for name in sorted(set(named) | set(SHARED)) if name not in sums]
    problems += [
        f"{name} is in the folder but not in SHA256SUMS"
        for name in sorted(present - set(sums) - {"SHA256SUMS", "check.json", *KEEP})
    ]
    problems += [
        f"{name}: manifest and SHA256SUMS differ" for name, digest in named.items() if sums.get(name) != digest
    ]
    problems += [
        f"{name}: checksum does not match"
        for name, digest in sums.items()
        if name in present and _sha256(folder / name) != digest
    ]
    note("checksums", problems)
    note(
        "limits",
        ([f"{len(sums) + 1} files, over {MAX_FILES}"] if len(sums) + 1 > MAX_FILES else [])
        + [f"{name} is over 2 GiB" for name in sorted(present) if (folder / name).stat().st_size >= MAX_BYTES],
    )
    parquet = {
        str(entry["file"]): int(str(entry["rows"])) for entry in files if str(entry["file"]).endswith(".parquet")
    }
    note(
        "rows",
        [
            f"{name}: {rows:,} rows, manifest says {expected:,}"
            for name, expected in parquet.items()
            if (folder / name).exists()
            and (rows := int(pl.scan_parquet(folder / name).select(pl.len()).collect().item())) != expected
        ],
    )
    sets = [folder / name for name in parquet if "_result_set" in name and name.endswith("_result_set.parquet")]
    ids = pl.concat([pl.read_parquet(path, columns=["k", "source_id"]) for path in sets]) if sets else None
    duplicates = 0 if ids is None else ids.height - ids["k"].n_unique()
    note("bundles", [f"{duplicates:,} result sets sit in more than one bundle"] if duplicates else [])
    sources = pl.read_parquet(folder / "cc-by_source.parquet") if (folder / "cc-by_source.parquet").exists() else None
    held = []
    if sources is None:
        held.append("cc-by_source.parquet is missing")
    else:
        held += [
            f"source {row['id']} ({row['publisher']}) is held by license"
            for row in sources.iter_rows(named=True)
            if bundle(row["license"], row["publisher"]) is None
        ]
        if ids is not None:
            listed = set(sources["id"].to_list())
            held += [
                f"result sets cite source {source}, which is not in the source table"
                for source in sorted(set(ids["source_id"].drop_nulls().unique().to_list()) - listed)
            ]
    note("licenses", held)
    inputs = manifest.get("inputs")
    note(
        "inputs",
        [
            f"{path.name} changed after the build"
            for path in (TARGETS, ATTESTATIONS)
            if not isinstance(inputs, dict) or inputs.get(path.name) != _sha256(path)
        ],
    )
    review = manifest.get("review")
    unanswered = []
    if kind != "rc":
        if not isinstance(review, dict) or not review.get("by") or not review.get("on"):
            unanswered.append("the review has no reviewer or date")
        unanswered += [
            f"{item['polity']} {item['contest']} has no verdict"
            for item in _list(review.get("items") if isinstance(review, dict) else None)
            if item.get("verdict") not in VERDICTS
        ]
    note("review", unanswered)
    ok = all(entry["ok"] for entry in checks)
    _write(
        folder / "check.json",
        {
            "version": version,
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "ok": ok,
            "sums": _sha256(folder / "SHA256SUMS"),
            "failures": [f"{entry['check']}: {problem}" for entry in checks for problem in _strings(entry["problems"])],
            "checks": checks,
        },
    )
    failed = [f"{entry['check']} ({entry['count']})" for entry in checks if not entry["ok"]]
    return f"{folder}: {'passed' if ok else 'failed: ' + ', '.join(failed)}"


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args, "-R", REPOSITORY], check=True, capture_output=True, text=True, timeout=3600
    ).stdout


def _github(folder: Path, version: str, kind: str, names: list[str]) -> None:
    tag = f"data-{version}"
    seen = subprocess.run(
        ["gh", "release", "view", tag, "-R", REPOSITORY, "--json", "isDraft,assets"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    uploaded: set[str] = set()
    if seen.returncode == 0:
        found = json.loads(seen.stdout)
        if not found["isDraft"]:
            raise RuntimeError(f"{tag} is already published")
        uploaded = {str(asset["name"]) for asset in found["assets"]}
    else:
        _gh(
            "release",
            "create",
            tag,
            "--draft",
            "--title",
            f"Data {version}",
            "--notes-file",
            str(folder / "CHANGES.md"),
            *(["--prerelease"] if kind == "rc" else []),
        )
    waiting = [folder / name for name in names if name not in uploaded]
    for start in range(0, len(waiting), 20):
        _gh("release", "upload", tag, *(str(path) for path in waiting[start : start + 20]))
        print(f"uploaded {min(start + 20, len(waiting))} of {len(waiting)}", flush=True)
    _gh("release", "edit", tag, "--draft=false", *(["--latest=false"] if kind == "rc" else ["--latest"]))


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as file:
        while chunk := file.read(1 << 24):
            digest.update(chunk)
    return digest.hexdigest()


def _dataverse(folder: Path, manifest: dict[str, object], version: str) -> str:
    config = _json(DATAVERSE)
    datasets = config.get("datasets")
    token = os.environ.get("DATAVERSE_TOKEN")
    if not isinstance(datasets, dict) or any(not isinstance(pid, str) for pid in datasets.values()) or not token:
        return "Dataverse skipped: dataverse.json has no dataset ids, or DATAVERSE_TOKEN is unset"
    shared = [*SHARED, "SHA256SUMS"]
    done = []
    with httpx.Client(base_url=str(config["server"]), headers={"X-Dataverse-key": token}, timeout=3600) as client:

        def wait(pid: str) -> None:
            for _ in range(240):
                locks = client.get("/api/datasets/:persistentId/locks", params={"persistentId": pid})
                locks.raise_for_status()
                if not locks.json()["data"]:
                    return
                time.sleep(5)
            raise RuntimeError(f"{pid} stayed locked for 20 minutes")

        for target, pid in datasets.items():
            wanted = {
                name: _md5(folder / name)
                for name in [
                    *(str(entry["file"]) for entry in _list(manifest["files"]) if entry["bundle"] == target),
                    *shared,
                ]
                if name.endswith(".parquet") or name in shared
            }
            listed = client.get("/api/datasets/:persistentId/versions/:latest/files", params={"persistentId": pid})
            listed.raise_for_status()
            have = {
                str(row["dataFile"]["filename"]): (
                    row["dataFile"]["id"],
                    row["dataFile"].get("md5") or row["dataFile"].get("checksum", {}).get("value"),
                )
                for row in listed.json()["data"]
            }
            stale = [(name, identifier) for name, (identifier, md5) in have.items() if wanted.get(name) != md5]
            fresh = [name for name, md5 in wanted.items() if have.get(name, (None, None))[1] != md5]
            for name, identifier in stale:
                client.delete(f"/api/files/{identifier}").raise_for_status()
            for name in fresh:
                with (folder / name).open("rb") as handle:
                    client.post(
                        "/api/datasets/:persistentId/add",
                        params={"persistentId": pid},
                        files={"file": (name, handle)},
                        data={
                            "jsonData": json.dumps(
                                {"description": f"electionsarchive.com data {version}", "tabIngest": False}
                            )
                        },
                    ).raise_for_status()
                wait(pid)
            if stale or fresh:
                wait(pid)
                client.post(
                    "/api/datasets/:persistentId/actions/:publish", params={"persistentId": pid, "type": "major"}
                ).raise_for_status()
            done.append(
                f"{target}: {len(fresh)} files added, {len(stale)} removed, {len(wanted) - len(fresh)} carried over"
            )
    return "Dataverse " + "; ".join(done)


def publish(version: str) -> str:
    kind, folder = _kind(version)
    checked = _json(folder / "check.json") if (folder / "check.json").exists() else None
    if checked is None or not checked.get("ok") or checked.get("sums") != _sha256(folder / "SHA256SUMS"):
        raise RuntimeError(f"{version} has no passing check of its current files; run ea release check {version}")
    manifest = _json(folder / "manifest.json")
    names = [*_sums(folder), "SHA256SUMS"]
    _github(folder, version, kind, names)
    if kind == "rc":
        return f"data-{version}: published on GitHub as a prerelease"
    subprocess.run(["rsync", "-a", "--partial", f"{folder}/", f"{BOX}/{version}/"], check=True)
    deposited = _dataverse(folder, manifest, version)
    recorded = record(version, manifest)
    for entry in _list(manifest["files"]):
        (folder / str(entry["file"])).unlink(missing_ok=True)
    return f"data-{version}: on GitHub and the Storage Box; {deposited}; release changeset {recorded}"


def record(version: str, manifest: dict[str, object]) -> str:
    from ea.apply import apply

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline="release",
            version="1",
            summary=f"Record release {version} at watermark {manifest['watermark_changeset_id']}",
            polity=None,
            kind="release",
        )
        cs.add("release", calver=version, watermark_changeset_id=manifest["watermark_changeset_id"], manifest=manifest)
        folder = cs.write()
    return f"{folder.name} {apply(folder)}"


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
