import gzip
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import suppress
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import LiteralString, NamedTuple, NotRequired, TypedDict, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import duckdb
import polars as pl
from psycopg import Connection, errors, sql

from ea import covers, search, store, touched
from ea.apply import DERIVED_IDS, MERGE_LOCK, _follow, _not_null, _refuse_merged, _rollup, _survivors
from ea.changeset import Changeset, Snapshot
from ea.checks import MERGE_DELETES, RULES, merged
from ea.raw import Artifact
from ea.store import DATA

NAMESPACE = uuid5(NAMESPACE_URL, "https://electionsarchive.com/")
ROOT = DATA / "packages"
CHECK_MEMORY = "1GB"
CHUNK_BYTES = 2 << 20
SERVER_LIMIT = 3072
ROLLUP_VALUES = 500_000
LOAD_CHUNK = 20
ROLLED = {"election", "contest", "reporting_unit", "result_set"}
SCALE = 9

U = pl.String()
D = pl.Date()
B = pl.Boolean()
I16 = pl.Int16()
I32 = pl.Int32()
I64 = pl.Int64()
F = pl.Float64()

TABLES: dict[str, tuple[tuple[str, ...] | None, dict[str, pl.DataType]]] = {
    "source": (
        ("id",),
        {
            "id": U,
            "kind": U,
            "publisher": U,
            "title": U,
            "url": U,
            "license": U,
            "license_url": U,
            "attribution": U,
            "notes": U,
        },
    ),
    "source_artifact": (
        ("id",),
        {
            "id": U,
            "source_id": U,
            "url": U,
            "sha256": U,
            "retrieved_at": pl.Datetime("us", "UTC"),
            "storage_key": U,
            "media_type": U,
            "bytes": I64,
        },
    ),
    "source_page": (
        ("id",),
        {"id": U, "artifact_id": U, "page_number": I32, "image_key": U, "width": I32, "height": I32},
    ),
    "polity": (("id",), {"id": U, "kind": U, "iso_code": U}),
    "polity_parent": (None, {"polity_id": U, "parent_id": U, "valid_from": D, "valid_to": D}),
    "polity_relation": (
        ("id",),
        {
            "id": U,
            "from_polity_id": U,
            "to_polity_id": U,
            "kind": U,
            "date": D,
            "date_precision": U,
            "source_id": U,
        },
    ),
    "scheme": (
        ("code",),
        {"code": U, "polity_id": U, "kind": U, "rank": I16, "noun": U, "kinds": U, "code_system": U},
    ),
    "area": (("id",), {"id": U, "scheme_code": U}),
    "area_code": (None, {"area_id": U, "code": U, "valid_from": D, "valid_to": D}),
    "area_kind": (None, {"area_id": U, "kind": U, "valid_from": D, "valid_to": D, "source_id": U}),
    "area_parent": (None, {"area_id": U, "parent_id": U, "valid_from": D, "valid_to": D}),
    "area_membership": (
        None,
        {"area_id": U, "parent_id": U, "partial": B, "valid_from": D, "valid_to": D},
    ),
    "area_geometry": (
        None,
        {
            "area_id": U,
            "valid_from": D,
            "valid_to": D,
            "geom": U,
            "label_point": U,
            "quality": U,
            "source_id": U,
        },
    ),
    "boundary_set": (
        ("id",),
        {"id": U, "scheme_code": U, "vintage": U, "valid_from": D, "valid_to": D, "as_of": D},
    ),
    "polity_territory": (None, {"polity_id": U, "area_id": U, "valid_from": D, "valid_to": D}),
    "area_event": (
        ("id",),
        {"id": U, "kind": U, "date": D, "date_precision": U, "source_id": U, "notes": U},
    ),
    "area_event_member": (None, {"event_id": U, "area_id": U, "role": U}),
    "body": (("id",), {"id": U, "polity_id": U, "kind": U}),
    "office": (("id",), {"id": U, "polity_id": U, "body_id": U, "kind": U}),
    "body_size": (None, {"body_id": U, "seats": I32, "valid_from": D, "valid_to": D}),
    "electoral_system": (
        ("id",),
        {"id": U, "polity_id": U, "valid_from": D, "valid_to": D, "rules": U, "rules_version": I32},
    ),
    "party": (("id",), {"id": U, "kind": U, "polity_id": U}),
    "party_color": (None, {"party_id": U, "color": U, "valid_from": D, "valid_to": D, "source_id": U}),
    "person": (
        ("id",),
        {
            "id": U,
            "birth_date": D,
            "birth_precision": U,
            "death_date": D,
            "death_precision": U,
            "gender": U,
        },
    ),
    "election": (
        ("id",),
        {
            "id": U,
            "polity_id": U,
            "parent_id": U,
            "kind": U,
            "date_from": D,
            "date_to": D,
            "date_precision": U,
            "original_calendar": U,
            "original_text": U,
            "status": U,
            "rerun_of_id": U,
        },
    ),
    "alliance": (("id",), {"id": U, "kind": U, "election_id": U}),
    "alliance_member": (None, {"alliance_id": U, "party_id": U, "role": U}),
    "contest": (
        ("id",),
        {
            "id": U,
            "election_id": U,
            "kind": U,
            "office_id": U,
            "body_id": U,
            "district_area_id": U,
            "magnitude": I32,
            "round_number": I16,
            "previous_round_id": U,
            "electoral_system_id": U,
            "default_result_kind": U,
            "term": U,
            "party_scope_id": U,
            "seat_id": U,
            "boundary_set_id": U,
            "allocation_parent_id": U,
        },
    ),
    "contest_option": (
        ("id",),
        {
            "id": U,
            "contest_id": U,
            "kind": U,
            "parent_option_id": U,
            "party_id": U,
            "ballot_position": I32,
            "ballot_label": U,
            "alliance_id": U,
        },
    ),
    "candidacy": (
        ("id",),
        {
            "id": U,
            "person_id": U,
            "option_id": U,
            "role": U,
            "list_position": I32,
            "ballot_name": U,
            "member_of_party_id": U,
            "is_independent": B,
            "is_incumbent": B,
            "ballot_lines": I16,
            "preference_option_id": U,
        },
    ),
    "reporting_unit": (
        ("id",),
        {
            "id": U,
            "polity_id": U,
            "scheme_code": U,
            "code": U,
            "parent_id": U,
            "area_relation": U,
            "area_id": U,
            "kind": U,
            "counted_with_id": U,
            "valid_from": D,
            "valid_from_precision": U,
            "valid_to": D,
            "valid_to_precision": U,
        },
    ),
    "result_set": (
        ("id",),
        {
            "id": U,
            "election_id": U,
            "contest_id": U,
            "stage": U,
            "kind": U,
            "source_id": U,
            "status": U,
            "is_default": B,
            "completeness": F,
            "as_of": pl.Datetime("us", "UTC"),
            "supersedes_id": U,
        },
    ),
    "outcome": (None, {"result_set_id": U, "option_id": U, "candidacy_id": U, "kind": U, "seats": I32}),
    "tenure": (
        ("id",),
        {
            "id": U,
            "person_id": U,
            "office_id": U,
            "seat_id": U,
            "body_id": U,
            "from_date": D,
            "from_precision": U,
            "to_date": D,
            "to_precision": U,
            "acquired_via": U,
            "contest_id": U,
            "ended_via": U,
            "resumes_tenure_id": U,
            "succeeds_tenure_id": U,
        },
    ),
    "name": (
        ("id",),
        {
            "id": U,
            "entity_id": U,
            "text": U,
            "lang": U,
            "script": U,
            "kind": U,
            "valid_from": D,
            "valid_from_precision": U,
            "valid_to": D,
            "valid_to_precision": U,
            "is_preferred": B,
            "source_id": U,
        },
    ),
    "note": (("id",), {"id": U, "entity_id": U, "kind": U, "body_md": U, "lang": U}),
    "slug": (("path",), {"path": U, "entity_id": U, "is_current": B}),
    "external_id": (None, {"entity_id": U, "scheme": U, "value": U}),
    "citation": (("id",), {"id": U, "entity_id": U, "source_id": U, "locator": U}),
    "reporting_unit_area": (
        None,
        {
            "unit_id": U,
            "area_id": U,
            "valid_from": D,
            "valid_from_precision": U,
            "valid_to": D,
            "valid_to_precision": U,
            "approximate": B,
            "partial": B,
            "method": U,
        },
    ),
    "party_affiliation": (
        None,
        {
            "party_id": U,
            "parent_party_id": U,
            "kind": U,
            "valid_from": D,
            "valid_from_precision": U,
            "valid_to": D,
            "valid_to_precision": U,
        },
    ),
    "ballot_question": (None, {"contest_id": U, "type": U}),
    "ballot_question_text": (
        ("id",),
        {"id": U, "contest_id": U, "kind": U, "lang": U, "body": U, "url": U, "source_id": U, "locator": U},
    ),
    "entity_retirement": (None, {"entity_id": U, "reason": U}),
}
FACT = ("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round")
FACT_KEY: dict[str, tuple[str, ...]] = {
    "result_value": FACT,
    "value_locator": FACT,
    "result_flow": ("result_set_id", "unit_id", "from_option_id", "option_id", "channel", "measure", "count_round"),
}
# A fact column that names a row of another table, and the integer key the database stores for it. An option left
# empty is stored as key 0.
FACT_REF = {
    "result_set_id": "result_set_k",
    "unit_id": "unit_k",
    "option_id": "option_k",
    "from_option_id": "from_option_k",
}
FACTS: dict[str, dict[str, pl.DataType]] = {
    "result_value": {
        "result_set_id": U,
        "unit_id": U,
        "option_id": U,
        "channel": I16,
        "measure": I16,
        "count_round": I16,
        "flags": I16,
        "value": I64,
    },
    "value_locator": {
        "result_set_id": U,
        "unit_id": U,
        "option_id": U,
        "channel": I16,
        "measure": I16,
        "count_round": I16,
        "page_id": U,
        "source_id": U,
        "x0": F,
        "y0": F,
        "x1": F,
        "y1": F,
    },
    "result_flow": {
        "result_set_id": U,
        "unit_id": U,
        "from_option_id": U,
        "option_id": U,
        "channel": I16,
        "measure": I16,
        "count_round": I16,
        "flags": I16,
        "value": I64,
    },
    "declared": {"result_set_id": U, "option_id": U, "measure": I16, "value": I64},
}
FACT_LIKE: dict[str, LiteralString] = {
    "result_value": "result_set_id uuid, unit_id uuid, option_id uuid, channel smallint, measure smallint,"
    " count_round smallint, flags smallint, value numeric",
    "value_locator": "result_set_id uuid, unit_id uuid, option_id uuid, channel smallint, measure smallint,"
    " count_round smallint, page_id uuid, source_id uuid, x0 real, y0 real, x1 real, y1 real",
    "result_flow": "result_set_id uuid, unit_id uuid, from_option_id uuid, option_id uuid, channel smallint,"
    " measure smallint, count_round smallint, flags smallint, value numeric",
}
JSON_COLUMNS = {"kinds", "rules", "locator"}
# Columns added to the format after packages were already built. A writer leaves one out of the Parquet file when
# every row leaves it empty, so earlier packages and their digests stay valid, and every reader takes a missing one
# as null.
OPTIONAL: dict[str, frozenset[str]] = {
    "candidacy": frozenset({"preference_option_id"}),
    "contest": frozenset({"term", "party_scope_id", "seat_id", "boundary_set_id", "allocation_parent_id"}),
    "contest_option": frozenset({"alliance_id"}),
}


def _empty(table: str, frame: pl.LazyFrame) -> list[str]:
    """The optional columns of a table that no row of the frame fills."""
    names = [column for column in OPTIONAL.get(table, ()) if column in frame.collect_schema().names()]
    if not names:
        return []
    counts = frame.select(pl.col(column).is_not_null().sum() for column in names).collect().row(0)
    return [column for column, n in zip(names, counts, strict=True) if not n]


def _trim(table: str, file: Path) -> None:
    """Rewrite a Parquet file without the optional columns no row fills."""
    empty = _empty(table, pl.scan_parquet(file))
    if empty:
        pl.read_parquet(file).drop(empty).write_parquet(file, statistics=False)


def _present(table: str, schema: dict[str, pl.DataType], file: Path) -> dict[str, pl.DataType]:
    """The schema's columns a Parquet file holds; only an optional column may be missing."""
    found = set(pl.read_parquet_schema(file))
    missing = [column for column in schema if column not in found and column not in OPTIONAL.get(table, ())]
    if missing:
        raise ValueError(f"{file} lacks the columns {missing}")
    return {column: kind for column, kind in schema.items() if column in found}


def _duck_select(table: str, schema: dict[str, pl.DataType], file: Path) -> str:
    """A DuckDB select of the schema's columns from a Parquet file, with missing optional columns as nulls."""
    found = _present(table, schema, file)
    return ", ".join(
        f'"{column}"' if column in found else f'null::{_duck(kind)} as "{column}"' for column, kind in schema.items()
    )


def folder(key: str) -> Path:
    return ROOT / key


def _schema(table: str, file: Path | None = None) -> dict[str, pl.DataType]:
    schema = TABLES[table][1] if table in TABLES else FACTS[table]
    if table in FACTS and "value" in schema and file is not None and file.exists():
        kind = pl.read_parquet_schema(file)["value"]
        if isinstance(kind, pl.Decimal):
            return {**schema, "value": kind}
    return schema


def _exact(value: object) -> int | Decimal | None:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, float):
        number = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError(f"value {value!r} is not a number") from None
    else:
        raise TypeError(f"value {value!r} is not a number; give an int, a Decimal, or a decimal string")
    if not number.is_finite():
        raise ValueError(f"value {value!r} is not a finite number")
    return int(number) if number == number.to_integral_value() else number


def _series(name: str, values: Iterable[object]) -> pl.Series:
    exact = [_exact(value) for value in values]
    places = [-int(number.as_tuple().exponent) for number in exact if isinstance(number, Decimal)]
    if not places:
        return pl.Series(name, exact, dtype=I64)
    return pl.Series(name, [None if n is None else Decimal(n) for n in exact], dtype=pl.Decimal(38, max(places)))


def _numbers(series: pl.Series) -> pl.Series:
    if series.dtype.is_integer() or isinstance(series.dtype, pl.Decimal):
        return series
    return _series(series.name, series.to_list())


def _settle(pieces: list[pl.LazyFrame], table: str) -> pl.DataType:
    kinds = [piece.collect_schema()["value"] for piece in pieces]
    odd = [kind for kind in kinds if not (kind.is_integer() or kind == pl.Null or isinstance(kind, pl.Decimal))]
    if odd:
        raise ValueError(f"{table}.value is {odd[0]}; counts must be integers or exact decimals, never floats or text")
    top = max((kind.scale for kind in kinds if isinstance(kind, pl.Decimal)), default=0)
    if not top:
        return I64
    wide = pl.concat([piece.select(pl.col("value").cast(pl.Decimal(38, top))) for piece in pieces])
    fits = wide.select(
        (pl.col("value").cast(pl.Decimal(38, places)) == pl.col("value")).all().alias(str(places))
        for places in range(top + 1)
    ).collect()
    places = next(places for places, fit in enumerate(fits.row(0)) if fit)
    if places > SCALE:
        raise ValueError(
            f"{table}.value needs {places} decimal places, more than the {SCALE} the package format holds; "
            "round to the precision the source publishes"
        )
    return I64 if places == 0 else pl.Decimal(38, places)


def _conform(frame: pl.LazyFrame, schema: dict[str, pl.DataType], exact: bool) -> pl.LazyFrame:
    present = set(frame.collect_schema().names())
    return frame.select(
        (pl.col(column) if exact and column == "value" else pl.col(column).cast(kind))
        if column in present
        else pl.lit(None, kind).alias(column)
        for column, kind in schema.items()
    )


def canonical(value: object) -> str:
    parsed = json.loads(value) if isinstance(value, str) else value
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Package:
    def __init__(self, key: str, pipeline: str, summary: str, depends: Iterable[str] = ()) -> None:
        self.key = key
        self.pipeline = pipeline
        self.summary = summary
        self.depends = list(depends)
        self.rows: dict[str, list[dict[str, object]]] = {}
        self.frames: dict[str, list[pl.DataFrame]] = {}
        self.parts: dict[str, list[Path]] = {}
        self.sources: list[dict[str, object]] = []
        self.external: set[str] = set()
        self.accepted: dict[str, str] = {}
        self.adopts: list[str] = []

    def id(self, kind: str, *parts: object) -> str:
        return str(uuid5(NAMESPACE, "\x1f".join([self.pipeline, kind, *(str(part) for part in parts)])))

    def add(self, table: str, **row: object) -> None:
        self.rows.setdefault(table, []).append(row)

    def frame(self, table: str, frame: pl.DataFrame) -> None:
        """Stage many rows at once; the frame's columns must be in the table's schema, and missing ones are null."""
        self.frames.setdefault(table, []).append(frame)

    def part(self, table: str, path: Path) -> None:
        """Stage a Parquet file of rows for a table too large to hold in memory; the file is streamed in on write."""
        self.parts.setdefault(table, []).append(path)

    def name(self, entity: str, text: str, lang: str, kind: str, preferred: bool = True) -> None:
        self.add(
            "name",
            id=self.id("name", entity, lang, kind, text),
            entity_id=entity,
            text=text,
            lang=lang,
            kind=kind,
            is_preferred=preferred,
        )

    def artifact(self, artifact: Artifact, source_id: str) -> str:
        id = str(uuid5(NAMESPACE, f"source_artifact\x1f{source_id}\x1f{artifact.storage_key}"))
        self.add(
            "source_artifact",
            id=id,
            source_id=source_id,
            url=artifact.url,
            sha256=artifact.sha256,
            retrieved_at=artifact.retrieved_at,
            storage_key=artifact.storage_key,
            media_type=artifact.media_type,
            bytes=artifact.bytes,
        )
        self.uses(artifact)
        return id

    def accept(self, code: str, reason: str) -> None:
        self.accepted[code] = reason

    def uses(self, artifact: Artifact) -> None:
        self.sources.append({"url": artifact.url, "sha256": artifact.sha256, "storage_key": artifact.storage_key})

    def write(self) -> Path:
        out = folder(self.key) / "build"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        counts: dict[str, int] = {}
        for table in sorted({*self.rows, *self.frames, *self.parts}):
            rows = self.rows.get(table, [])
            schema = _schema(table)
            exact = table in FACTS and "value" in schema
            staged = self.frames.get(table, [])
            unknown = {column for row in rows for column in row} | {c for f in staged for c in f.columns}
            if unknown - set(schema):
                raise ValueError(f"{table} has columns outside the package format: {sorted(unknown - set(schema))}")
            pieces: list[pl.LazyFrame] = []
            if rows:
                plain = {column: kind for column, kind in schema.items() if not (exact and column == "value")}
                frame = pl.DataFrame(
                    [
                        {
                            column: canonical(row[column])
                            if column in JSON_COLUMNS and row.get(column) is not None
                            else row.get(column)
                            for column in plain
                        }
                        for row in rows
                    ],
                    schema=plain,
                    orient="row",
                )
                if exact:
                    frame = frame.with_columns(_series("value", [row.get("value") for row in rows]))
                pieces.append(frame.lazy())
            for f in staged:
                if exact and "value" in f.columns:
                    f = f.with_columns(_numbers(f["value"]))
                pieces.append(_conform(f.lazy(), schema, exact))
            pieces += [_conform(pl.scan_parquet(path), schema, exact) for path in self.parts.get(table, [])]
            if exact:
                kind = _settle(pieces, table)
                pieces = [piece.with_columns(pl.col("value").cast(kind)) for piece in pieces]
            combined = pl.concat(pieces).select(list(schema))
            empty = _empty(table, combined)
            if empty:
                combined = combined.drop(empty)
            file = out / f"{table}.parquet"
            if table in self.parts:
                combined.sink_parquet(file, statistics=False)
                counts[table] = int(pl.scan_parquet(file).select(pl.len()).collect().item())
            else:
                written = combined.collect()
                written.write_parquet(file, statistics=False)
                counts[table] = written.height
        commit = store.checkout()
        manifest = {
            "key": self.key,
            "pipeline": self.pipeline,
            "summary": self.summary,
            "depends": self.depends,
            "external": sorted(self.external),
            "parser_commit": commit["sha"],
            "built_at": datetime.now(UTC).isoformat(),
            "sources": self.sources,
            "accepted": self.accepted,
            "tables": counts,
            "digest": _digest(out, counts),
        }
        if self.adopts:
            manifest["adopts"] = sorted(self.adopts)
        (out / "package.json").write_text(json.dumps(manifest, indent=1) + "\n")
        print(f"{self.key}: " + ", ".join(f"{table} {n:,}" for table, n in counts.items()), flush=True)
        return out


class Manifest(TypedDict):
    key: str
    pipeline: str
    summary: str
    depends: list[str]
    external: list[str]
    parser_commit: str | None
    built_at: str
    sources: list[dict[str, str]]
    accepted: dict[str, str]
    tables: dict[str, int]
    digest: str
    adopts: NotRequired[list[str]]


def _manifest(path: Path) -> Manifest:
    return cast(Manifest, json.loads((path / "package.json").read_text()))


def _resolve(key: str) -> Path:
    name, _, version = key.partition("@")
    path = folder(name) / (version or "build")
    if not (path / "package.json").exists():
        raise LookupError(f"package {key} is not built")
    return path


def _views(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    manifest = _manifest(path)
    paths = [path, *(_resolve(str(key)) for key in manifest["depends"])]
    for table, (_, schema) in [*TABLES.items(), *((t, (None, s)) for t, s in FACTS.items())]:
        types = ", ".join(f'null::{_duck(kind)} as "{column}"' for column, kind in schema.items())
        for prefix, scope in (("", [path]), ("all_", paths)):
            files = [p / f"{table}.parquet" for p in scope if (p / f"{table}.parquet").exists()]
            body = (
                " union all ".join(
                    f"select {_duck_select(table, schema, file)} from read_parquet('{file}')" for file in files
                )
                if files
                else f"select {types} where false"
            )
            con.execute(f'create or replace view "{prefix}{table}" as {body}')
    for table in OUTSIDE:
        for prefix in ("", "all_"):
            con.execute(f'create or replace view "{prefix}{table}" as select null::varchar as id where false')
    con.execute(
        "create or replace table external as select unnest(?::varchar[]) as id",
        [list(manifest["external"])],
    )
    return [str(key) for key in manifest["depends"]]


def _duck(kind: pl.DataType) -> str:
    if isinstance(kind, pl.Decimal):
        return f"decimal({kind.precision},{kind.scale})"
    text = str(kind)
    if text.startswith("Datetime"):
        return "timestamptz"
    return {"String": "varchar", "Date": "date", "Boolean": "boolean", "Float64": "double"}.get(text, "bigint")


def _digest(out: Path, tables: Iterable[str], con: duckdb.DuckDBPyConnection | None = None) -> str:
    own = con is None
    con = con or duckdb.connect(config={"memory_limit": CHECK_MEMORY, "threads": 4})
    digest = hashlib.sha256()
    for table in sorted(tables):
        schema = _present(table, _schema(table, out / f"{table}.parquet"), out / f"{table}.parquet")
        columns = ", ".join(f'"{c}"::{_duck(kind)} as "{c}"' for c, kind in schema.items())
        found = con.execute(
            f"select sum(hash(t))::varchar from (select {columns} from read_parquet('{out / table}.parquet')) t"
        ).fetchone()
        digest.update(f"{table}\x1f{None if found is None else found[0]}\n".encode())
    if own:
        con.close()
    return digest.hexdigest()


REFERENCES = [
    ("source_artifact", "source_id", "source"),
    ("polity_parent", "polity_id", "polity"),
    ("polity_parent", "parent_id", "polity"),
    ("scheme", "polity_id", "polity"),
    ("area", "scheme_code", "scheme"),
    ("area_code", "area_id", "area"),
    ("area_kind", "area_id", "area"),
    ("area_parent", "area_id", "area"),
    ("area_parent", "parent_id", "area"),
    ("area_membership", "area_id", "area"),
    ("area_membership", "parent_id", "area"),
    ("area_geometry", "area_id", "area"),
    ("area_geometry", "source_id", "source"),
    ("polity_territory", "polity_id", "polity"),
    ("polity_territory", "area_id", "area"),
    ("area_event", "source_id", "source"),
    ("area_event_member", "event_id", "area_event"),
    ("area_event_member", "area_id", "area"),
    ("boundary_set", "scheme_code", "scheme"),
    ("body", "polity_id", "polity"),
    ("office", "body_id", "body"),
    ("electoral_system", "polity_id", "polity"),
    ("election", "polity_id", "polity"),
    ("contest", "election_id", "election"),
    ("contest", "office_id", "office"),
    ("contest", "body_id", "body"),
    ("contest", "district_area_id", "area"),
    ("contest", "previous_round_id", "contest"),
    ("contest", "electoral_system_id", "electoral_system"),
    ("contest", "party_scope_id", "party"),
    ("contest", "seat_id", "seat"),
    ("contest", "boundary_set_id", "boundary_set"),
    ("contest", "allocation_parent_id", "contest"),
    ("contest_option", "contest_id", "contest"),
    ("contest_option", "parent_option_id", "contest_option"),
    ("contest_option", "party_id", "party"),
    ("contest_option", "alliance_id", "alliance"),
    ("alliance", "election_id", "election"),
    ("alliance_member", "alliance_id", "alliance"),
    ("alliance_member", "party_id", "party"),
    ("party_color", "party_id", "party"),
    ("party_color", "source_id", "source"),
    ("candidacy", "person_id", "person"),
    ("candidacy", "option_id", "contest_option"),
    ("candidacy", "member_of_party_id", "party"),
    ("candidacy", "preference_option_id", "contest_option"),
    ("reporting_unit", "polity_id", "polity"),
    ("reporting_unit", "scheme_code", "scheme"),
    ("reporting_unit", "area_id", "area"),
    ("reporting_unit", "parent_id", "reporting_unit"),
    ("result_set", "election_id", "election"),
    ("result_set", "contest_id", "contest"),
    ("result_set", "source_id", "source"),
    ("outcome", "result_set_id", "result_set"),
    ("outcome", "option_id", "contest_option"),
    ("outcome", "candidacy_id", "candidacy"),
    ("result_value", "result_set_id", "result_set"),
    ("result_value", "unit_id", "reporting_unit"),
    ("result_value", "option_id", "contest_option"),
    ("result_flow", "result_set_id", "result_set"),
    ("result_flow", "unit_id", "reporting_unit"),
    ("result_flow", "option_id", "contest_option"),
    ("result_flow", "from_option_id", "contest_option"),
    ("declared", "result_set_id", "result_set"),
    ("declared", "option_id", "contest_option"),
    ("reporting_unit", "counted_with_id", "reporting_unit"),
    ("result_set", "supersedes_id", "result_set"),
    ("reporting_unit_area", "unit_id", "reporting_unit"),
    ("reporting_unit_area", "area_id", "area"),
    ("party_affiliation", "party_id", "party"),
    ("party_affiliation", "parent_party_id", "party"),
    ("ballot_question", "contest_id", "contest"),
    ("ballot_question_text", "contest_id", "contest"),
    ("ballot_question_text", "source_id", "source"),
    ("body_size", "body_id", "body"),
    ("tenure", "person_id", "person"),
    ("tenure", "office_id", "office"),
    ("tenure", "seat_id", "seat"),
    ("tenure", "body_id", "body"),
    ("tenure", "contest_id", "contest"),
    ("tenure", "resumes_tenure_id", "tenure"),
    ("tenure", "succeeds_tenure_id", "tenure"),
    ("polity_relation", "from_polity_id", "polity"),
    ("polity_relation", "to_polity_id", "polity"),
    ("polity_relation", "source_id", "source"),
]
TARGET_KEY = {"scheme": "code", "polity": "id"}
ENTITY_TABLES = (
    "polity", "area", "reporting_unit", "election", "contest", "contest_option", "result_set", "electoral_system",
    "party", "alliance", "person", "candidacy", "body", "office", "seat", "tenure", "source",
)  # fmt: skip
OUTSIDE = ("seat",)

CHECKS = {
    "unique.fact": """with repeated as (
                        select hash(result_set_id, unit_id, option_id, channel, measure, count_round) as h from result_value
                        group by h having count(*) > 1)
                      select result_set_id, unit_id, option_id, channel, measure, count_round, count(*) as n from result_value
                      where hash(result_set_id, unit_id, option_id, channel, measure, count_round) in (select h from repeated)
                      group by all having count(*) > 1""",
    "unique.flow": """select result_set_id, unit_id, from_option_id, option_id, channel, measure, count_round, count(*) as n
                      from result_flow group by all having count(*) > 1""",
    "unique.external_id": """select scheme, value, count(*) as n from (
                               select distinct entity_id, scheme, value from all_external_id)
                             group by all having count(*) > 1""",
    "unique.area_code": """with c as (select area_id, code, valid_from, valid_to, row_number() over () as n from area_code)
                           select a.area_id, a.code, a.valid_from, a.valid_to, b.code, b.valid_from, b.valid_to
                           from c a join c b on a.area_id = b.area_id and a.n < b.n
                           where coalesce(a.valid_from, date '0001-01-01') < coalesce(b.valid_to, date '9999-12-31')
                             and coalesce(b.valid_from, date '0001-01-01') < coalesce(a.valid_to, date '9999-12-31')""",
    "unique.area_geometry": """with o as (select area_id, valid_from, valid_to from area_geometry)
                               select o.area_id, o.valid_from, o.valid_to, count(*) as n
                               from o join all_area_geometry b on b.area_id = o.area_id
                                and coalesce(o.valid_from, date '0001-01-01') < coalesce(b.valid_to, date '9999-12-31')
                                and coalesce(b.valid_from, date '0001-01-01') < coalesce(o.valid_to, date '9999-12-31')
                               group by all having count(*) > 1""",
    "unique.unit_code": """with u as (select id, scheme_code, code, valid_from, valid_to, row_number() over () as n
                                           from reporting_unit)
                                 select a.id, b.id, a.scheme_code, a.code, a.valid_from, a.valid_to
                                 from u a join u b on a.scheme_code = b.scheme_code and a.code = b.code and a.n < b.n
                                 where coalesce(a.valid_from, date '0001-01-01') < coalesce(b.valid_to, date '9999-12-31')
                                   and coalesce(b.valid_from, date '0001-01-01') < coalesce(a.valid_to, date '9999-12-31')""",
    "fact.negative": "select * from result_value where value < 0",
    "fact.flow_negative": "select * from result_flow where value < 0",
    "fact.flow_self": "select * from result_flow where from_option_id = option_id",
    "fact.flow_option_contest": """with pairs as (
                                     select result_set_id, option_id from result_flow
                                     union select result_set_id, from_option_id from result_flow
                                     where from_option_id is not null)
                                   select p.result_set_id, p.option_id from pairs p
                                   join all_contest_option o on o.id = p.option_id
                                   where exists (select 1 from all_result_set r where r.id = p.result_set_id)
                                     and not exists (select 1 from all_result_set r
                                                     where r.id = p.result_set_id and r.contest_id = o.contest_id)""",
    "fact.option_contest": """with pairs as (select distinct result_set_id, option_id from result_value)
                              select p.result_set_id, p.option_id from pairs p
                              join all_contest_option o on o.id = p.option_id
                              where exists (select 1 from all_result_set r where r.id = p.result_set_id)
                                and not exists (select 1 from all_result_set r
                                                where r.id = p.result_set_id and r.contest_id = o.contest_id)""",
    "fact.party_color": "select party_id, color from party_color where not regexp_full_match(color, '#[0-9a-f]{6}')",
    "fact.note_kind": """select id, kind from note
                         where kind not in ('context', 'caveat', 'methodology', 'editorial', 'retired')""",
    "fact.scheme_code": """select code from scheme
                           where not regexp_full_match(code, '([a-z]{2,3}(-[a-z0-9]+)?|world)\\.[a-z0-9_]+')""",
    "fact.scheme_code_system": """select code, code_system from scheme
                                  where code_system is not null
                                    and not regexp_full_match(code_system, '[a-z][a-z0-9_]*')""",
    "accounting.votes_valid": """with v as (
                                   select v.result_set_id, v.unit_id,
                                          sum(value) filter (where measure = 1 and option_id is not null) as votes,
                                          sum(value) filter (where measure = 4 and option_id is null) as valid
                                   from result_value v where count_round = 0 group by all)
                                 select v.* from v join all_result_set r on r.id = v.result_set_id
                                 join all_contest c on c.id = r.contest_id
                                 where (c.magnitude = 1 or c.kind = 'list_allocation')
                                   and v.valid is not null and coalesce(v.votes, 0) <> v.valid""",
    "accounting.ballots_sum": """with v as (
                                   select result_set_id, unit_id,
                                          sum(value) filter (where measure = 3) as ballots_cast,
                                          sum(value) filter (where measure = 4) as valid,
                                          sum(value) filter (where measure = 5) as invalid,
                                          sum(value) filter (where measure = 6) as blank
                                   from result_value where option_id is null and count_round = 0 group by all)
                                 select * from v where ballots_cast is not null and valid is not null
                                   and case when invalid is null and blank is null then valid > ballots_cast
                                            else valid + coalesce(invalid, 0) + coalesce(blank, 0) <> ballots_cast end""",
    "accounting.turnout": """with v as (
                               select result_set_id, unit_id,
                                      sum(value) filter (where measure = 2) as registered,
                                      coalesce(sum(value) filter (where measure = 3),
                                               sum(value) filter (where measure = 14)) as ballots_cast
                               from result_value where option_id is null and count_round = 0 group by all)
                             select * from v where ballots_cast > registered""",
    "accounting.declared": """with got as (
                                select result_set_id, option_id, measure, sum(value) as summed from result_value
                                where count_round = 0 group by all)
                              select d.*, g.summed from declared d left join got g
                                on g.result_set_id = d.result_set_id and g.option_id is not distinct from d.option_id
                               and g.measure = d.measure
                              where g.summed is distinct from d.value""",
    "outcomes.seats_magnitude": """with s as (
                                     select result_set_id, sum(seats) as seats from outcome
                                     where candidacy_id is null and seats is not null group by all)
                                   select s.*, c.magnitude from s join all_result_set r on r.id = s.result_set_id
                                   join all_contest c on c.id = r.contest_id
                                   where c.magnitude is not null and s.seats <> c.magnitude""",
    "outcomes.list_members": """with m as (
                                  select result_set_id, option_id, count(*) as members from outcome
                                  where candidacy_id is not null and kind = 'elected' group by all)
                                select m.*, w.seats from m join outcome w on w.result_set_id = m.result_set_id
                                  and w.option_id = m.option_id and w.candidacy_id is null and w.seats is not null
                                where m.members <> w.seats""",
    "outcomes.member_off_list": """select o.* from outcome o join all_contest_option p on p.id = o.option_id
                                   where o.candidacy_id is not null
                                     and p.kind not in ('list', 'independents', 'others')""",
    "outcomes.single_winner": """with w as (
                                   select o.result_set_id, count(*) as winners from outcome o
                                   join all_result_set r on r.id = o.result_set_id
                                   join all_contest c on c.id = r.contest_id
                                   where c.magnitude = 1 and o.kind = 'elected' group by all)
                                 select * from w where winners > 1""",
    "fact.alliance_election": """select o.id, o.alliance_id, a.election_id, c.election_id as contest_election
                                 from all_contest_option o join all_alliance a on a.id = o.alliance_id
                                 join all_contest c on c.id = o.contest_id
                                 where a.election_id is distinct from c.election_id""",
    "unique.preference_option": """select preference_option_id, count(*) as n from all_candidacy
                                   where preference_option_id is not null group by all having count(*) > 1""",
    "fact.preference_option": """select k.id, k.option_id, k.preference_option_id, l.kind, o.kind, o.parent_option_id
                                 from candidacy k join all_contest_option o on o.id = k.preference_option_id
                                 join all_contest_option l on l.id = k.option_id
                                 where o.kind <> 'candidate' or o.parent_option_id is distinct from k.option_id
                                   or o.contest_id <> l.contest_id
                                   or l.kind not in ('list', 'independents', 'others')""",
    "fact.preference_measure": """select v.result_set_id, v.option_id, v.measure, count(*) as n from result_value v
                                  where v.measure <> 7 and v.option_id in (
                                    select preference_option_id from all_candidacy
                                    where preference_option_id is not null)
                                  group by all""",
    "accounting.preference_over_list": """select p.result_set_id, p.unit_id, p.option_id, p.channel, p.count_round,
                                                 p.value as preference_votes, l.value as list_votes, k.ballot_lines
                                          from result_value p join all_candidacy k on k.preference_option_id = p.option_id
                                          join result_value l on l.result_set_id = p.result_set_id
                                           and l.unit_id = p.unit_id and l.option_id = k.option_id
                                           and l.channel = p.channel and l.count_round = p.count_round and l.measure = 1
                                          where p.measure = 7 and p.value > l.value * coalesce(k.ballot_lines, 1)""",
    "result_set.default": """select election_id, contest_id, stage, count(*) as n from all_result_set where is_default
                             group by all having count(*) > 1""",
    "result_set.empty": """select r.id from result_set r where r.completeness > 0
                           and not exists (select 1 from result_value v where v.result_set_id = r.id)""",
    "unit.unlocated_area": """select id, code from reporting_unit
                              where (area_relation = 'unlocated') <> (area_id is null)""",
}


def _rss(pid: int | str, field: str) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(f"{field}:"):
                return int(line.split()[1]) >> 10
    except OSError:
        pass
    return 0


class Meter:
    def __init__(self, conn: Connection | None = None) -> None:
        self.conn = conn
        self.backend = None if conn is None else conn.info.backend_pid
        self.server = 0
        self.tripped = 0
        self.done = threading.Event()
        self.reset()
        if self.backend is not None:
            threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self) -> None:
        shell = os.environ.get("EA_DB_SHELL")
        if shell is None:
            while not self.done.wait(0.2):
                self._seen(_rss(str(self.backend), "RssAnon"))
            return
        script = f"while kill -0 {self.backend} 2>/dev/null; do awk '/^RssAnon:/ {{print $2}}' /proc/{self.backend}/status; sleep 0.2; done"
        with subprocess.Popen(
            [*shlex.split(shell), f"sh -c {shlex.quote(script)}"], stdout=subprocess.PIPE, text=True
        ) as remote:
            assert remote.stdout is not None
            for line in remote.stdout:
                if self.done.is_set():
                    remote.terminate()
                    break
                if line.strip().isdigit():
                    self._seen(int(line) >> 10)

    def _seen(self, mb: int) -> None:
        self.server = max(self.server, mb)
        if self.server > SERVER_LIMIT and not self.tripped and self.conn is not None:
            self.tripped = self.server
            self.conn.cancel_safe()

    def reset(self) -> None:
        with suppress(OSError):
            Path("/proc/self/clear_refs").write_text("5")
        self.server = 0
        self.began = time.monotonic()

    def step(self, label: str) -> str:
        if self.backend is not None:
            self.server = max(self.server, _rss(str(self.backend), "RssAnon"))
        server = f", server {self.server:,} MB" if self.backend is not None else ""
        text = f"{label} {time.monotonic() - self.began:.1f}s (client peak {_rss('self', 'VmHWM'):,} MB{server})"
        self.reset()
        return text

    def close(self) -> None:
        self.done.set()


def check(path: Path) -> list[tuple[str, int, list[tuple[object, ...]]]]:
    spill = ROOT / ".duckdb"
    spill.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"memory_limit": CHECK_MEMORY, "threads": 4, "temp_directory": str(spill)})
    _views(con, path)
    findings: list[tuple[str, int, list[tuple[object, ...]]]] = []
    for table, (key, schema) in TABLES.items():
        if key is None:
            continue
        columns = ", ".join(key)
        rows = con.execute(f'select {columns}, count(*) from "all_{table}" group by all having count(*) > 1').fetchall()
        if rows:
            findings.append((f"unique.{table}", len(rows), rows[:5]))
    for table, column, target in REFERENCES:
        target_key = TARGET_KEY.get(target, "id")
        known = f'select "{target_key}" as v from "all_{target}" union all select id as v from external'
        rows = con.execute(
            f'select distinct "{column}" from "{table}" where "{column}" is not null '
            f'and not exists (select 1 from ({known}) k where k.v = "{table}"."{column}")'
        ).fetchall()
        if rows:
            findings.append((f"reference.{table}.{column}", len(rows), rows[:5]))
    entities = " union all ".join(
        f'select id as v from "all_{t}"' for t in ENTITY_TABLES if t in TABLES or t in OUTSIDE
    )
    rows = con.execute(
        f"select distinct entity_id from note where entity_id is not null and entity_id not in "
        f"({entities} union all select id as v from external)"
    ).fetchall()
    if rows:
        findings.append(("reference.note.entity_id", len(rows), rows[:5]))
    for (rules,) in con.execute("select rules from electoral_system").fetchall():
        errors = [error.message for error in RULES.iter_errors(json.loads(rules))]
        if errors:
            findings.append(("rules.schema", len(errors), [(rules, errors[0])]))
    bad: list[tuple[object, ...]] = [
        (code, kinds)
        for code, kinds in con.execute("select code, kinds from scheme where kinds is not null").fetchall()
        if not isinstance(json.loads(kinds), dict)
    ]
    if bad:
        findings.append(("fact.scheme_kinds", len(bad), bad[:5]))
    for code, query in CHECKS.items():
        rows = con.execute(query).fetchall()
        if rows:
            findings.append((code, len(rows), rows[:5]))
    return findings


def _chunks(file: Path, columns: list[str]) -> Iterator[bytes]:
    rows = int(pl.scan_parquet(file).select(pl.len()).collect().item())
    size = max(1, rows * CHUNK_BYTES // max(1, file.stat().st_size))
    decimals = [c for c, kind in pl.read_parquet_schema(file).items() if c in columns and isinstance(kind, pl.Decimal)]
    scan = pl.scan_parquet(file).select(columns)
    if decimals:
        scan = scan.with_columns(pl.col(c).cast(pl.String).str.replace(r"\.?0+$", "") for c in decimals)
    for frame in scan.collect_batches(chunk_size=max(1_000, min(200_000, size))):
        buffer = BytesIO()
        frame.write_csv(buffer, include_header=False, null_value="", quote_style="non_numeric")
        yield buffer.getvalue()


def _defaults(conn: Connection, table: str, columns: list[str]) -> list[tuple[str, LiteralString]]:
    rows = conn.execute(
        """select a.attname, pg_get_expr(d.adbin, d.adrelid) from pg_attribute a
           join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
           where a.attrelid = %s::regclass and a.attnotnull and a.attname = any(%s)""",
        [f"public.{table}", columns],
    ).fetchall()
    return [(str(column), cast(LiteralString, str(default))) for column, default in rows if "(" not in str(default)]


def _stage(conn: Connection, table: str, columns: list[str], file: Path, like: LiteralString | None = None) -> int:
    name = f"pkg_{table}"
    conn.execute(sql.SQL("drop table if exists {}").format(sql.Identifier(name)))
    if like is None:
        conn.execute(
            sql.SQL("create temp table {} on commit drop as select {} from public.{} limit 0").format(
                sql.Identifier(name), sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(table)
            )
        )
    else:
        conn.execute(sql.SQL("create temp table {} ({}) on commit drop").format(sql.Identifier(name), sql.SQL(like)))
    with (
        conn.cursor() as cursor,
        cursor.copy(
            sql.SQL("copy {} ({}) from stdin (format csv)").format(
                sql.Identifier(name), sql.SQL(", ").join(map(sql.Identifier, columns))
            )
        ) as copy,
    ):
        for chunk in _chunks(file, columns):
            copy.write(chunk)
    for column, default in _defaults(conn, table, columns):
        conn.execute(
            sql.SQL("update {t} set {c} = {d} where {c} is null").format(
                t=sql.Identifier(name), c=sql.Identifier(column), d=sql.SQL(default)
            )
        )
    conn.execute(sql.SQL("analyze {}").format(sql.Identifier(name)))
    found = conn.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(name))).fetchone()
    return 0 if found is None else int(found[0])


def _hash(alias: str, columns: list[str]) -> sql.Composable:
    return sql.SQL("md5(row({})::text)").format(
        sql.SQL(", ").join(sql.SQL("{}.{}").format(sql.Identifier(alias), sql.Identifier(c)) for c in columns)
    )


class Loaded(NamedTuple):
    key: str
    version: int
    changeset_id: str
    path: Path
    moved: list[str]
    repointed: list[str]
    sets: dict[int, int]
    counts: dict[str, list[int]]
    owned: list[str]
    deferred: list[str]
    retire: dict[str, tuple[str, list[str]]]


def _missing(conn: Connection, external: object) -> list[str]:
    return [
        str(value)
        for (value,) in conn.execute(
            """with x as (
                 select v, case when v ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then v::uuid end as id
                 from unnest(%s::text[]) v
               )
               select v from x
               where not exists (select from entity e where e.id = x.id)
                 and not exists (select from polity p where p.id = x.id)
                 and not exists (select from scheme s where s.code = x.v)""",
            [external],
        ).fetchall()
    ]


def _vacate_units(conn: Connection, key: str, pipeline: str, batch: frozenset[str]) -> int:
    """Free the scheme, code, and validity that a package's staged units take over from older packages of the run.

    A new build may move a unit into another package of the same pipeline (an election package's units into the
    geo package) and widen its validity. The insert comes before the old package's turn to drop the unit, and the
    old row (a different id) would block it. Such a row of a package in this run is renamed, so the key is free;
    the old package's own turn deletes it, since its new version no longer declares it. If the old package still
    declares the row, its upsert restores the code and the duplicate shows up in `ea package ready`.
    """
    return conn.execute(
        """update reporting_unit t set code = t.code || '~' || t.id::text
           where t.id not in (select id from pkg_reporting_unit)
             and exists (select from changeset c where c.id = t.changeset_id and c.pipeline = %s
                         and c.scope->>'package' <> %s and c.scope->>'package' = any(%s))
             and exists (select from pkg_reporting_unit s where s.scheme_code = t.scheme_code and s.code = t.code
                         and daterange(s.valid_from, s.valid_to, '[)') && t.valid)""",
        [pipeline, key, sorted(batch)],
    ).rowcount


def _drop(conn: Connection, table: str, staged: dict[str, tuple[str, list[str]]], owned: list[str]) -> int:
    key_columns = TABLES[table][0]
    if table not in staged:
        return conn.execute(
            sql.SQL("delete from {} where changeset_id = any(%s::uuid[])").format(sql.Identifier(table)),
            [owned],
        ).rowcount
    if key_columns is not None:
        return conn.execute(
            sql.SQL(
                "delete from {t} t where t.changeset_id = any(%s::uuid[]) and ({k}) not in (select {k} from {s})"
            ).format(
                t=sql.Identifier(table),
                k=sql.SQL(", ").join(map(sql.Identifier, key_columns)),
                s=sql.Identifier(staged[table][0]),
            ),
            [owned],
        ).rowcount
    columns = staged[table][1]
    return conn.execute(
        sql.SQL(
            "delete from {t} t where t.changeset_id = any(%s::uuid[]) and {h} not in (select {sh} from {s} s)"
        ).format(
            t=sql.Identifier(table),
            h=_hash("t", columns),
            sh=_hash("s", columns),
            s=sql.Identifier(staged[table][0]),
        ),
        [owned],
    ).rowcount


LATER = ("certified", "court_revised", "recount")
STAGES = ("live", "preliminary", "official", *LATER)
GRAIN: LiteralString = """coalesce((select u.scheme_code from (select distinct v.unit_k from result_value v
                                where v.result_set_k = r.k) v join reporting_unit u on u.k = v.unit_k
                              group by u.scheme_code order by count(*) desc, u.scheme_code limit 1), 'declared')"""


def _grains(conn: Connection, path: Path, sets: list[str]) -> dict[str, str]:
    grains = dict.fromkeys(sets, "declared")
    values = path / "result_value.parquet"
    if not values.exists():
        return grains
    pairs = (
        pl.scan_parquet(values)
        .filter(pl.col("result_set_id").is_in(sets))
        .select("result_set_id", "unit_id")
        .unique()
        .collect()
    )
    units = path / "reporting_unit.parquet"
    schemes = (
        pl.read_parquet(units, columns=["id", "scheme_code"])
        if units.exists()
        else pl.DataFrame(schema={"id": U, "scheme_code": U})
    )
    outside = sorted(set(pairs["unit_id"].to_list()) - set(schemes["id"].to_list()))
    if outside:
        rows = conn.execute(
            "select id::text, scheme_code from reporting_unit where id = any(%s::uuid[])", [outside]
        ).fetchall()
        schemes = pl.concat([schemes, pl.DataFrame(rows, schema={"id": U, "scheme_code": U}, orient="row")])
    counted = (
        pairs.join(schemes, left_on="unit_id", right_on="id")
        .group_by("result_set_id", "scheme_code")
        .len()
        .sort(["result_set_id", "len", "scheme_code"], descending=[False, True, False])
        .unique("result_set_id", keep="first", maintain_order=True)
    )
    for row in counted.iter_rows(named=True):
        grains[str(row["result_set_id"])] = str(row["scheme_code"])
    return grains


def _demote(conn: Connection, path: Path, pipeline: str, owned: list[str]) -> list[str]:
    file = path / "result_set.parquet"
    if not file.exists():
        return []
    later = (
        pl.read_parquet(file, columns=["id", "election_id", "contest_id", "stage", "is_default"])
        .filter(pl.col("is_default") & pl.col("stage").is_in(LATER) & pl.col("contest_id").is_not_null())
        .to_dicts()
    )
    if not later:
        return []
    grains = _grains(conn, path, [str(row["id"]) for row in later])
    found = conn.execute(
        f"""select r.id::text, r.election_id::text, r.contest_id::text, r.stage::text, {GRAIN}
            from result_set r join changeset c on c.id = r.changeset_id
            where r.is_default and c.pipeline <> %s and r.changeset_id <> all(%s::uuid[])
              and r.stage::text = any(%s) and (r.election_id, r.contest_id) in (
                select * from unnest(%s::uuid[], %s::uuid[]))""",
        [
            pipeline,
            owned,
            list(STAGES),
            [str(row["election_id"]) for row in later],
            [str(row["contest_id"]) for row in later],
        ],
    ).fetchall()
    demoted = sorted(
        {
            str(id)
            for id, election, contest, stage, grain in found
            for row in later
            if (str(row["election_id"]), str(row["contest_id"])) == (election, contest)
            and STAGES.index(stage) <= STAGES.index(str(row["stage"]))
            and grains[str(row["id"])] == grain
        }
    )
    if demoted:
        conn.execute("update result_set set is_default = false where id = any(%s::uuid[])", [demoted])
    return demoted


def _unit_clashes(conn: Connection, path: Path, pipeline: str) -> list[str]:
    file = path / "reporting_unit.parquet"
    if not file.exists():
        return []
    units = pl.read_parquet(file, columns=["id", "scheme_code", "code", "valid_from", "valid_to"])
    if units.is_empty():
        return []
    rows = conn.execute(
        """select s.scheme_code, s.code, t.id::text, c.pipeline
           from unnest(%s::uuid[], %s::text[], %s::text[], %s::date[], %s::date[]) s(id, scheme_code, code, f, u)
           join reporting_unit t on t.scheme_code = s.scheme_code and t.code = s.code
             and t.valid && daterange(s.f, s.u, '[)') and t.id <> s.id
           join changeset c on c.id = t.changeset_id and c.pipeline <> %s""",
        [
            [str(value) for value in units["id"]],
            units["scheme_code"].to_list(),
            units["code"].to_list(),
            units["valid_from"].to_list(),
            units["valid_to"].to_list(),
            pipeline,
        ],
    ).fetchall()
    if not rows:
        return []
    return [
        (
            f"ready.unit: {len(rows)} reporting units take the scheme and code of a unit another pipeline holds for"
            f" an overlapping period; reuse that unit's id instead of minting one: {rows[:5]}"
        )
    ]


def _clashes(conn: Connection, path: Path, pipeline: str) -> list[str]:
    file = path / "result_set.parquet"
    if not file.exists():
        return []
    columns = ["id", "election_id", "contest_id", "stage", "is_default"]
    if "supersedes_id" in pl.read_parquet_schema(file):
        columns.append("supersedes_id")
    defaults = (
        pl.read_parquet(file, columns=columns)
        .filter(pl.col("is_default") & pl.col("contest_id").is_not_null())
        .to_dicts()
    )
    if not defaults:
        return []
    grains = _grains(conn, path, [str(row["id"]) for row in defaults])
    held = conn.execute(
        f"""select r.election_id::text, r.contest_id::text, r.stage::text, {GRAIN}, c.pipeline, r.id::text
            from result_set r join changeset c on c.id = r.changeset_id
            where r.is_default and c.pipeline <> %s and (r.election_id, r.contest_id) in (
              select * from unnest(%s::uuid[], %s::uuid[]))""",
        [pipeline, [str(row["election_id"]) for row in defaults], [str(row["contest_id"]) for row in defaults]],
    ).fetchall()
    clashing = [
        f"{row['id']} ({row['stage']}, {grains[str(row['id'])]}) against {owner} ({grain})"
        for row in defaults
        for election, contest, stage, grain, owner, held_id in held
        if (election, contest, stage) == (str(row["election_id"]), str(row["contest_id"]), str(row["stage"]))
        and not (stage in LATER and grain == grains[str(row["id"])])
        and str(row.get("supersedes_id")) != held_id
    ]
    if not clashing:
        return []
    return [
        (
            f"ready.default: {len(clashing)} default sets would take the default another pipeline holds for the same"
            f" contest and stage without being a later official count at its grain or naming that set in supersedes_id; leave them non-default: {clashing[:5]}"
        )
    ]


def _supersede(conn: Connection, staged: dict[str, tuple[str, list[str]]], owned: list[str]) -> list[str]:
    """Settle the default of sets that one pipeline's set replaces for another's.

    A default set that names another pipeline's set in `supersedes_id` takes that set's place: the older set is
    marked not default and superseded, and only those columns change. The reverse holds when the older package is
    reloaded: its staged rows keep the demoted state instead of flipping the default back. Returns the ids of
    default sets demoted, which the changeset records so a later version can restore them.
    """
    if "result_set" not in staged or "supersedes_id" not in staged["result_set"][1]:
        return []
    name = sql.Identifier(staged["result_set"][0])
    # An older package reloaded after its successor: keep its sets demoted.
    conn.execute(
        sql.SQL(
            """update {s} x set is_default = false, status = 'superseded'
               where exists (select from result_set n where n.supersedes_id = x.id and n.is_default
                               and n.changeset_id <> all(%s::uuid[]))
                 and (x.is_default or x.status <> 'superseded')"""
        ).format(s=name),
        [owned],
    )
    # A package's default sets that replace sets already held.
    held = conn.execute(
        sql.SQL(
            """select r.id::text, r.is_default from result_set r
               where r.id in (select n.supersedes_id from {s} n where n.is_default and n.supersedes_id is not null)
                 and r.id not in (select id from {s}) and (r.is_default or r.status <> 'superseded')"""
        ).format(s=name)
    ).fetchall()
    if held:
        conn.execute(
            "update result_set set is_default = false, status = 'superseded' where id = any(%s::uuid[])",
            [[id for id, _ in held]],
        )
    return [id for id, was_default in held if was_default]


def _restore(conn: Connection, key: str, changeset_id: str, demoted: list[str]) -> list[str]:
    earlier = {
        str(id)
        for (ids,) in conn.execute(
            "select scope->'demoted' from changeset where scope->>'package' = %s and id <> %s", [key, changeset_id]
        ).fetchall()
        for id in ids or []
    }
    restored: list[str] = []
    for id in sorted(earlier - set(demoted)):
        if conn.execute(
            """update result_set r set is_default = true, status = case when status = 'superseded' then 'active' else status end where r.id = %s and not r.is_default
                 and not exists (select from result_set o where o.is_default and o.election_id = r.election_id
                                   and o.contest_id is not distinct from r.contest_id and o.stage = r.stage)
               returning r.id""",
            [id],
        ).fetchone():
            restored.append(id)
    return restored


def _facts(
    conn: Connection,
    path: Path,
    accepted: dict[str, object],
    referencing: dict[tuple[str, str], list[list[str]]],
    batch: frozenset[str] = frozenset(),
) -> Loaded | None:
    manifest = _manifest(path)
    key = str(manifest["key"])
    missing = _missing(conn, manifest["external"])
    if missing:
        raise ValueError(f"{key} references ids missing from the database: {missing[:5]}")
    for depend in manifest["depends"]:
        # A dependency earlier in this run counts as loaded: the run takes its packages in dependency order inside
        # one transaction, so its rows are already written when this package comes up.
        if depend not in batch and (
            conn.execute("select 1 from changeset where scope->>'package' = %s", [depend]).fetchone() is None
        ):
            raise ValueError(f"{key} depends on {depend}, which is not loaded")
    prior = conn.execute(
        """select id, scope->>'digest', (scope->>'version')::int, scope->'adopts' from changeset
           where scope->>'package' = %s order by created_at""",
        [key],
    ).fetchall()
    files = {table: path / f"{table}.parquet" for table in [*TABLES, *FACTS] if (path / f"{table}.parquet").exists()}
    if prior and prior[-1][1] == manifest["digest"] and not any(merged(conn, str(row[0]), set(files)) for row in prior):
        print(f"{key}: version {prior[-1][2]} already loaded, unchanged", flush=True)
        return None
    adopts = sorted({str(id) for row in prior for id in row[3] or []} | set(manifest.get("adopts", [])))
    owned = [str(row[0]) for row in prior] + adopts
    version = len(prior) + 1
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", [f"package:{key}"])
    inserted = conn.execute(
        """insert into changeset (author_kind, pipeline, pipeline_version, kind, status, summary, scope,
                                  payload_kind, artifact_keys, checks, published_at)
           values ('pipeline', %s, %s, 'import', 'published', %s, %s, 'artifacts', %s, %s, now()) returning id::text""",
        [
            manifest["pipeline"],
            manifest["parser_commit"],
            f"{manifest['summary']} (package {key} v{version})",
            json.dumps(
                {
                    "package": key,
                    "version": version,
                    "digest": manifest["digest"],
                    "adopts": adopts,
                    "depends": manifest["depends"],
                    "polity": key.split(".")[0].upper(),
                }
            ),
            [str(source["storage_key"]) for source in manifest["sources"]],
            json.dumps({"package": "passed", "accepted": accepted or {}}),
        ],
    ).fetchone()
    if inserted is None:
        raise RuntimeError(f"{key}: no changeset row")
    changeset_id = str(inserted[0])
    conn.execute("select set_config('ea.changeset_id', %s, true)", [changeset_id])
    sets_before = [
        k for (k,) in conn.execute("select k from result_set where changeset_id = any(%s::uuid[])", [owned]).fetchall()
    ]
    staged: dict[str, tuple[str, list[str]]] = {}
    for table, file in files.items():
        if table in TABLES:
            columns = list(_present(table, TABLES[table][1], file))
            _stage(conn, table, columns, file)
            for column in OPTIONAL.get(table, ()):
                if column not in columns and column in _columns(conn, table):
                    conn.execute(
                        sql.SQL("alter table {s} add column {c} {k}").format(
                            s=sql.Identifier(f"pkg_{table}"),
                            c=sql.Identifier(column),
                            k=sql.SQL(_type(conn, table, column)),
                        )
                    )
                    columns.append(column)
            _follow(
                conn,
                table,
                sql.Identifier(f"pkg_{table}"),
                columns,
                referencing,
                sql.SQL("x.changeset_id = any({}::uuid[])").format(sql.Literal(owned)),
            )
            staged[table] = (f"pkg_{table}", columns)
    demoted = sorted({*_supersede(conn, staged, owned), *_demote(conn, path, str(manifest["pipeline"]), owned)})
    if demoted:
        conn.execute(
            "update changeset set scope = scope || jsonb_build_object('demoted', %s::jsonb) where id = %s",
            [json.dumps(demoted), changeset_id],
        )
    moved = [
        str(id)
        for (id,) in conn.execute(
            sql.SQL("select p.area_id from area_parent p where p.changeset_id = any(%s::uuid[]){kept}").format(
                kept=sql.SQL(" and {h} not in (select {s} from pkg_area_parent s)").format(
                    h=_hash("p", staged["area_parent"][1]), s=_hash("s", staged["area_parent"][1])
                )
                if "area_parent" in staged
                else sql.SQL("")
            ),
            [owned],
        ).fetchall()
    ]
    dropped_sets = [
        k
        for (k,) in conn.execute(
            "select k from result_set where changeset_id = any(%s::uuid[]) and id not in (select id from pkg_result_set)"
            if "result_set" in staged
            else "select k from result_set where changeset_id = any(%s::uuid[])",
            [owned],
        ).fetchall()
    ]
    if dropped_sets:
        for table in ("result_rollup_sum", "result_rollup_unit", "rollup_coverage", "area_day_set"):
            conn.execute(
                sql.SQL("delete from {} where result_set_k = any(%s)").format(sql.Identifier(table)), [dropped_sets]
            )
        conn.execute(
            "delete from flag where result_set_id in (select id from result_set where k = any(%s))", [dropped_sets]
        )
    counts: dict[str, list[int]] = {}
    touched_ids: dict[str, list[str]] = {}
    deferred: list[str] = []
    polity = key.split(".")[0].upper()
    claim = sql.SQL(
        "exists (select from changeset c where c.id = t.changeset_id and c.scope->>'polity' = {p} "
        "and c.scope->>'package' <> {k})"
    ).format(p=sql.Literal(polity), k=sql.Literal(key))
    for table in reversed(TABLES):
        try:
            with conn.transaction():
                counts.setdefault(table, [0, 0])[1] = _drop(conn, table, staged, owned)
        except errors.ForeignKeyViolation:
            deferred.append(table)
    if "reporting_unit" in staged:
        _vacate_units(conn, key, str(manifest["pipeline"]), batch)
    for table in TABLES:
        if table not in staged:
            continue
        name, columns = staged[table]
        key_columns = TABLES[table][0]
        listed = sql.SQL(", ").join(map(sql.Identifier, columns))
        if key_columns is not None:
            rest = [c for c in columns if c not in key_columns]
            query = sql.SQL(
                """insert into {t} as t ({c}) select {c} from {s}
                   on conflict ({k}) do {action}"""
            ).format(
                t=sql.Identifier(table),
                c=listed,
                s=sql.Identifier(name),
                k=sql.SQL(", ").join(map(sql.Identifier, key_columns)),
                action=sql.SQL(
                    "update set ({r}) = row({x}), changeset_id = case when {claim} then {now} else t.changeset_id end "
                    "where ({tr}) is distinct from ({x}) or {claim}"
                ).format(
                    r=sql.SQL(", ").join(map(sql.Identifier, rest)),
                    x=sql.SQL(", ").join(sql.SQL("excluded.{}").format(sql.Identifier(c)) for c in rest),
                    tr=sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(c)) for c in rest),
                    claim=claim,
                    now=sql.SQL("current_setting('ea.changeset_id')::uuid"),
                )
                if rest
                else sql.SQL("update set changeset_id = {now} where {claim}").format(
                    now=sql.SQL("current_setting('ea.changeset_id')::uuid"), claim=claim
                ),
            )
            if table in ROLLED:
                returned = conn.execute(
                    sql.SQL("with w as ({} returning t.id, xmax = 0 as fresh) select id, fresh from w").format(query)
                ).fetchall()
                touched_ids[table] = [str(id) for id, fresh in returned if table != "reporting_unit" or not fresh]
                written = len(returned)
            else:
                written = conn.execute(query).rowcount
        else:
            lead = columns[0]
            written = conn.execute(
                sql.SQL(
                    """insert into {t} ({c}) select {c} from {s} s
                       where {sh} not in (select {h} from {t} t where t.{lead} in (select {lead} from {s}))"""
                ).format(
                    t=sql.Identifier(table),
                    c=listed,
                    s=sql.Identifier(name),
                    sh=_hash("s", columns),
                    h=_hash("t", columns),
                    lead=sql.Identifier(lead),
                )
            ).rowcount
            # Identical rows another package of the polity wrote stay out of the insert, so they would stay
            # owned by that package and go when its next version drops them. The newest package to declare a
            # row owns it.
            conn.execute(
                sql.SQL(
                    """update {t} t set changeset_id = current_setting('ea.changeset_id')::uuid
                       where {claim} and t.{lead} in (select {lead} from {s})
                         and {h} in (select {sh} from {s} s)"""
                ).format(
                    t=sql.Identifier(table),
                    claim=claim,
                    lead=sql.Identifier(lead),
                    s=sql.Identifier(name),
                    h=_hash("t", columns),
                    sh=_hash("s", columns),
                )
            )
        counts.setdefault(table, [0, 0])[0] = written
    retire: dict[str, tuple[str, list[str]]] = {}
    for table in deferred:
        if table in staged:
            name = f"retire_{changeset_id.replace('-', '')}_{table}"
            conn.execute(
                sql.SQL("alter table {} rename to {}").format(sql.Identifier(staged[table][0]), sql.Identifier(name))
            )
            retire[table] = (name, staged[table][1])
    mine = sorted(
        {
            *sets_before,
            *(k for (k,) in conn.execute("select k from result_set where changeset_id = %s", [changeset_id])),
        }
    )
    sizes, changed = _merge(conn, "result_value", files.get("result_value"), mine, counts, referencing)
    for table in ("value_locator", "result_flow"):
        _merge(conn, table, files.get(table), mine, counts, referencing)
    _refuse_merged(conn, changeset_id, set(files))
    restored = _restore(conn, key, changeset_id, demoted)
    if demoted or restored:
        print(f"{key}: demoted {len(demoted)} default sets of other pipelines, restored {len(restored)}", flush=True)
    current = conn.execute(
        """select k from result_set where id = any(%(sets)s::uuid[]) or contest_id = any(%(contests)s::uuid[])
           or election_id = any(%(elections)s::uuid[])""",
        {
            "sets": [*touched_ids.get("result_set", []), *demoted, *restored],
            "contests": touched_ids.get("contest", []),
            "elections": touched_ids.get("election", []),
        },
    ).fetchall()
    changed |= {k for (k,) in current}
    changed -= set(dropped_sets)
    rolled = {k: sizes.get(k, 0) for k in changed}
    return Loaded(
        key,
        version,
        changeset_id,
        path,
        moved,
        touched_ids.get("reporting_unit", []),
        rolled,
        counts,
        owned,
        deferred,
        retire,
    )


def _retire(conn: Connection, loaded: list[Loaded]) -> None:
    """Delete the rows whose drop a foreign key blocked, once every package of the run has written its rows.

    A package's rows may be pointed at by rows of a package loaded after it (a geo package's dropped areas, still
    used by contests until the run's election packages re-point them), so the retirements run after all inserts and
    re-points, dependents first. A drop that still fails waits for the others to clear what blocks it; one that
    fails with nothing left to clear means a package outside the run still uses the rows.
    """
    pending = [(item, table) for item in reversed(loaded) for table in item.deferred]
    while pending:
        failed: list[tuple[Loaded, str, errors.ForeignKeyViolation]] = []
        for item, table in pending:
            try:
                with conn.transaction():
                    item.counts.setdefault(table, [0, 0])[1] = _drop(conn, table, item.retire, item.owned)
            except errors.ForeignKeyViolation as error:
                failed.append((item, table, error))
        if len(failed) == len(pending):
            item, table, error = failed[0]
            raise ValueError(
                f"{item.key} v{item.version} would delete {table} rows that other packages still use "
                f"({error.diag.message_detail}); a package must keep every row other packages reference, "
                "so build it from a full parse of its pipeline"
            ) from None
        pending = [(item, table) for item, table, _ in failed]


def _merge(
    conn: Connection,
    table: str,
    file: Path | None,
    before: list[int],
    counts: dict[str, list[int]],
    referencing: dict[tuple[str, str], list[list[str]]],
) -> tuple[dict[int, int], set[int]]:
    schema = FACTS[table]
    rest = [c for c in schema if c not in FACT_KEY[table]]
    refs = [c for c in FACT_KEY[table] if c in FACT_REF]
    plain = [c for c in FACT_KEY[table] if c not in FACT_REF]
    keys = [FACT_REF[c] for c in refs] + plain
    resolved = sql.Identifier(f"pkg_{table}_k")
    conn.execute(sql.SQL("drop table if exists {}").format(resolved))
    if file is None:
        conn.execute(
            sql.SQL("create temp table {} on commit drop as select {} from public.{} limit 0").format(
                resolved, sql.SQL(", ").join(map(sql.Identifier, [*keys, *rest])), sql.Identifier(table)
            )
        )
    else:
        rows = _stage(conn, table, list(schema), file, like=FACT_LIKE[table])
        _follow(conn, table, sql.Identifier(f"pkg_{table}"), list(schema), referencing, sql.SQL("false"))
        made = conn.execute(
            sql.SQL(
                """create temp table {k} on commit drop as
                   select r.k as result_set_k, u.k as unit_k, {options}, {fact}, {rest}
                   from {s} v join result_set r on r.id = v.result_set_id
                   join reporting_unit u on u.id = v.unit_id {joins}"""
            ).format(
                k=resolved,
                options=sql.SQL(", ").join(
                    sql.SQL("coalesce({a}.k, 0) as {k}").format(
                        a=sql.Identifier(f"o_{c}"), k=sql.Identifier(FACT_REF[c])
                    )
                    for c in refs[2:]
                ),
                joins=sql.SQL(" ").join(
                    sql.SQL("left join contest_option {a} on {a}.id = v.{c}").format(
                        a=sql.Identifier(f"o_{c}"), c=sql.Identifier(c)
                    )
                    for c in refs[2:]
                ),
                fact=sql.SQL(", ").join(sql.SQL("v.{}").format(sql.Identifier(c)) for c in plain),
                rest=sql.SQL(", ").join(sql.SQL("v.{}").format(sql.Identifier(c)) for c in rest),
                s=sql.Identifier(f"pkg_{table}"),
            )
        ).rowcount
        if made != rows:
            raise ValueError(f"{table}: {rows:,} staged, {made:,} resolved")
        conn.execute(sql.SQL("analyze {}").format(resolved))
    listed = sql.SQL(", ").join(map(sql.Identifier, [*keys, *rest]))
    gone = dict(
        conn.execute(
            sql.SQL(
                """with d as (
                     delete from {t} t where t.result_set_k = any(%s) and not exists (
                       select from {k} f where {same} and ({fr}) is not distinct from ({tr}))
                     returning t.result_set_k)
                   select result_set_k, count(*) from d group by 1"""
            ).format(
                t=sql.Identifier(table),
                k=resolved,
                same=sql.SQL(" and ").join(sql.SQL("f.{c} = t.{c}").format(c=sql.Identifier(c)) for c in keys),
                fr=sql.SQL(", ").join(sql.SQL("f.{}").format(sql.Identifier(c)) for c in rest),
                tr=sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(c)) for c in rest),
            ),
            [before],
        ).fetchall()
    )
    put = dict(
        conn.execute(
            sql.SQL(
                """with w as (insert into {t} ({c}) select {c} from {k} on conflict do nothing returning result_set_k)
                   select result_set_k, count(*) from w group by 1"""
            ).format(t=sql.Identifier(table), c=listed, k=resolved)
        ).fetchall()
    )
    if gone or put:
        counts[table] = [sum(put.values()), sum(gone.values())]
    sizes = dict(conn.execute(sql.SQL("select result_set_k, count(*) from {} group by 1").format(resolved)).fetchall())
    return sizes, set(gone) | set(put)


def _derive(conn: Connection, loaded: list[Loaded], meter: Meter) -> list[str]:
    sizes: dict[int, int] = {}
    for item in loaded:
        sizes.update(item.sets)
    moved = sorted({area for item in loaded for area in item.moved})
    repointed = sorted({unit for item in loaded for unit in item.repointed})
    batches: list[list[int]] = [[]]
    total = 0
    for k, n in sorted(sizes.items()):
        if batches[-1] and total + n > ROLLUP_VALUES:
            batches.append([])
            total = 0
        batches[-1].append(k)
        total += n
    for index, batch in enumerate(batches):
        _rollup(conn, None, moved if index == 0 else [], repointed if index == 0 else [], batch)
    steps = [_step(meter, f"rollups ({len(batches)} passes)")]
    covers.uncover(conn, [item.changeset_id for item in loaded], moved, repointed, sets=sorted(sizes))
    for item in loaded:
        touched.recorded(conn, item.changeset_id)
    steps.append(_step(meter, "covers"))
    search.scope(conn, [item.changeset_id for item in loaded])
    search.refresh(conn)
    steps.append(_step(meter, "search"))
    return steps


def _step(meter: Meter, label: str) -> str:
    text = meter.step(label)
    print(f"step {text}", flush=True)
    return text


def _verify(conn: Connection, loaded: list[Loaded]) -> None:
    problems: list[str] = []
    for item in loaded:
        for table, (key_columns, _) in TABLES.items():
            file = item.path / f"{table}.parquet"
            if key_columns != ("id",) or not file.exists():
                continue
            ids = pl.read_parquet(file, columns=["id"])["id"].unique().to_list()
            found = conn.execute(
                sql.SQL(
                    """select count(*) from unnest(%s::uuid[]) i(id)
                       where exists (select from {} t where t.id = i.id){}"""
                ).format(
                    sql.Identifier(table),
                    sql.SQL(" or exists (select from entity_merge m where m.entity_id = i.id)")
                    if table in MERGE_DELETES
                    else sql.SQL(""),
                ),
                [ids],
            ).fetchone()
            present = 0 if found is None else int(found[0])
            if present != len(ids):
                problems.append(f"{item.key}: {table} holds {present:,} of the package's {len(ids):,} rows")
        sets_file = item.path / "result_set.parquet"
        sets = set(pl.read_parquet(sets_file, columns=["id"])["id"].to_list()) if sets_file.exists() else set()
        for table, measure, query in (
            (
                "result_value",
                "value",
                """select r.id::text, count(*), coalesce(sum(v.value), 0) from result_set r
                   join result_value v on v.result_set_k = r.k where r.id = any(%s::uuid[]) group by r.id""",
            ),
            (
                "result_flow",
                "value",
                """select r.id::text, count(*), coalesce(sum(v.value), 0) from result_set r
                   join result_flow v on v.result_set_k = r.k where r.id = any(%s::uuid[]) group by r.id""",
            ),
            (
                "outcome",
                "seats",
                """select result_set_id::text, count(*), coalesce(sum(seats), 0) from outcome
                   where result_set_id = any(%s::uuid[]) group by result_set_id""",
            ),
        ):
            file = item.path / f"{table}.parquet"
            expected: dict[str, tuple[int, Decimal]] = {}
            if file.exists():
                grouped = (
                    pl.scan_parquet(file)
                    .group_by("result_set_id")
                    .agg(pl.len(), pl.col(measure).fill_null(0).sum())
                    .collect()
                )
                expected = {str(rs): (int(n), Decimal(total)) for rs, n, total in grouped.iter_rows()}
            ids = sorted(sets | set(expected))
            actual = {str(rs): (int(n), Decimal(total)) for rs, n, total in conn.execute(query, [ids]).fetchall()}
            none = (0, Decimal(0))
            for rs in ids:
                got, built = actual.get(rs, none), expected.get(rs, none)
                if got != built:
                    problems.append(
                        f"{item.key}: {table} for result set {rs} holds (rows, {measure}) ({got[0]}, {got[1]}),"
                        f" the package ({built[0]}, {built[1]})"
                    )
    if problems:
        raise ValueError(f"load verification failed, rolled back ({len(problems):,}): " + "; ".join(problems[:10]))


def _minted(paths: Iterable[Path]) -> set[str]:
    minted: set[str] = set()
    for path in paths:
        for table, (key, _) in TABLES.items():
            file = path / f"{table}.parquet"
            if key in (("id",), ("code",)) and file.exists():
                minted |= {str(value) for value in pl.read_parquet(file, columns=list(key)).to_series().drop_nulls()}
    return minted


def _ordered(
    conn: Connection, items: list[tuple[Path, dict[str, object]]]
) -> list[list[tuple[Path, dict[str, object]]]]:
    """Group the packages that must load together, each group in dependency order.

    Packages that depend on each other, or that use ids another package of the run mints, share a group: a group
    loads in one transaction, so a dependency counts as loaded and a package's dropped rows are deleted only after
    the group's other packages stop using them.
    """
    keyed = {str(_manifest(path)["key"]): (path, accepted) for path, accepted in items}
    mints = {key: _minted([path]) for key, (path, _) in keyed.items()}
    externals = {key: {str(value) for value in _manifest(path)["external"]} for key, (path, _) in keyed.items()}
    absent = set(_missing(conn, sorted(set().union(*externals.values()))))
    needs: dict[str, set[str]] = {}
    for key, (path, _) in keyed.items():
        manifest = _manifest(path)
        external = externals[key] & absent
        needs[key] = {str(d) for d in manifest["depends"] if d in keyed} | {
            other for other, ids in mints.items() if other != key and not ids.isdisjoint(external)
        }
    placed: list[str] = []
    while len(placed) < len(keyed):
        ready = [key for key in keyed if key not in placed and needs[key] <= set(placed)]
        if not ready:
            raise ValueError(f"packages depend on each other in a cycle: {sorted(set(keyed) - set(placed))}")
        placed += ready
    parent = {key: key for key in keyed}

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for key, wanted in needs.items():
        for other in wanted:
            parent[root(key)] = root(other)
    groups: dict[str, list[tuple[Path, dict[str, object]]]] = {}
    for key in placed:
        groups.setdefault(root(key), []).append(keyed[key])
    return list(groups.values())


def load(conn: Connection, items: list[tuple[Path, dict[str, object]]], meter: Meter) -> list[str]:
    done: list[str] = []
    batch = frozenset(str(_manifest(path)["key"]) for path, _ in items)
    chunks: list[list[tuple[Path, dict[str, object]]]] = [[]]
    for group in _ordered(conn, items):
        if chunks[-1] and len(chunks[-1]) + len(group) > LOAD_CHUNK:
            chunks.append([])
        chunks[-1] += group
    for number, chunk in enumerate(chunks, 1):
        print(f"chunk {number} of {len(chunks)}: {len(chunk)} packages", flush=True)
        with conn.transaction():
            conn.execute("select pg_advisory_xact_lock_shared(hashtext('ea.search'))")
            conn.execute("select pg_advisory_xact_lock_shared(%s)", [MERGE_LOCK])
            referencing = _survivors(conn)
            loaded = [
                item
                for path, accepted in chunk
                if (item := _facts(conn, path, accepted, referencing, batch)) is not None
            ]
            steps = [_step(meter, "facts")]
            if loaded:
                _retire(conn, loaded)
                steps.append(_step(meter, "retire"))
                steps += _derive(conn, loaded, meter)
                _verify(conn, loaded)
                steps.append(_step(meter, "verify"))
        for item in loaded:
            copy = folder(item.key) / f"v{item.version}"
            if copy.exists():
                shutil.rmtree(copy)
            shutil.copytree(item.path, copy)
            changed = ", ".join(f"{t} +{a:,}/-{d:,}" for t, (a, d) in item.counts.items() if a or d)
            print(f"{item.key} v{item.version} as {item.changeset_id}: {changed}", flush=True)
        if loaded:
            print(f"{len(loaded)} packages: {', '.join(steps)}", flush=True)
        done += [item.changeset_id for item in loaded]
    return done


READY_COLUMNS = """select a.attname, a.attnotnull,
       coalesce(pg_get_expr(d.adbin, d.adrelid) not like '%%(%%', false),
       array(select e.enumlabel::text from pg_enum e where e.enumtypid = a.atttypid)
   from pg_attribute a left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
   where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped"""


Columns = dict[str, tuple[bool, bool, list[str]]]
# Fact tables whose columns `ready` checks against the database. A snapshot written before one was listed here leaves
# its columns unchecked.
FLOW_TABLES = ("result_flow",)
SNAPSHOT_IDS = """select id from polity
union select area_id from polity_territory
union select id from area where scheme_code like 'world.%' or scheme_code like 'eu.%'"""


PART: LiteralString = "coalesce(nullif(upper(split_part(coalesce(scope->>'polity', ''), '-', 1)), ''), '_')"
LABEL: LiteralString = "(select l.text from ref_name l where l.entity_id = {}.id)"
PUBLISH: dict[str, tuple[LiteralString, dict[str, pl.DataType]]] = {
    "polity": (
        f"select t.id::text, t.iso_code, t.kind::text, {LABEL.format('t')}, c.pipeline from polity t",
        {"id": U, "iso_code": U, "kind": U, "name": U, "pipeline": U},
    ),
    "area": (
        f"select t.id::text, t.scheme_code, {LABEL.format('t')}, c.pipeline from area t",
        {"id": U, "scheme_code": U, "name": U, "pipeline": U},
    ),
    "area_code": (
        "select t.area_id::text, t.code, t.valid_from, t.valid_to, c.pipeline from area_code t",
        {"area_id": U, "code": U, "valid_from": D, "valid_to": D, "pipeline": U},
    ),
    "boundary_set": (
        "select t.id::text, t.scheme_code, t.valid_from, t.valid_to, c.pipeline from boundary_set t",
        {"id": U, "scheme_code": U, "valid_from": D, "valid_to": D, "pipeline": U},
    ),
    "body": (
        f"select t.id::text, t.polity_id::text, t.kind::text, {LABEL.format('t')}, c.pipeline from body t",
        {"id": U, "polity_id": U, "kind": U, "name": U, "pipeline": U},
    ),
    "office": (
        f"""select t.id::text, t.polity_id::text, t.body_id::text, t.kind::text, {LABEL.format("t")}, c.pipeline
            from office t""",
        {"id": U, "polity_id": U, "body_id": U, "kind": U, "name": U, "pipeline": U},
    ),
    "party": (
        f"select t.id::text, t.polity_id::text, t.kind::text, {LABEL.format('t')}, c.pipeline from party t",
        {"id": U, "polity_id": U, "kind": U, "name": U, "pipeline": U},
    ),
    "person": (
        f"select t.id::text, {LABEL.format('t')}, t.birth_date, c.pipeline from person t",
        {"id": U, "name": U, "birth_date": D, "pipeline": U},
    ),
    "election": (
        f"""select t.id::text, t.polity_id::text, t.kind::text, t.date_from, t.parent_id::text,
                   {LABEL.format("t")}, c.pipeline
            from election t""",
        {
            "id": U,
            "polity_id": U,
            "kind": U,
            "date_from": D,
            "parent_id": U,
            "name": U,
            "pipeline": U,
        },
    ),
    "contest": (
        f"""select t.id::text, t.election_id::text, t.kind::text, t.office_id::text, t.body_id::text,
                   t.district_area_id::text, t.magnitude, t.round_number, {LABEL.format("t")}, c.pipeline
            from contest t""",
        {
            "id": U,
            "election_id": U,
            "kind": U,
            "office_id": U,
            "body_id": U,
            "district_area_id": U,
            "magnitude": I32,
            "round_number": I16,
            "name": U,
            "pipeline": U,
        },
    ),
    "contest_option": (
        """select t.id::text, t.contest_id::text, t.kind::text, t.party_id::text, t.ballot_position,
                  t.ballot_label, c.pipeline
           from contest_option t""",
        {
            "id": U,
            "contest_id": U,
            "kind": U,
            "party_id": U,
            "ballot_position": I32,
            "ballot_label": U,
            "pipeline": U,
        },
    ),
    "candidacy": (
        """select t.id::text, t.option_id::text, t.person_id::text, t.ballot_name, t.role::text, c.pipeline
           from candidacy t""",
        {"id": U, "option_id": U, "person_id": U, "ballot_name": U, "role": U, "pipeline": U},
    ),
    "reporting_unit": (
        """select t.id::text, t.polity_id::text, t.scheme_code, t.code, t.parent_id::text, t.area_id::text,
                  t.kind::text, t.valid_from, t.valid_to, c.pipeline
           from reporting_unit t""",
        {
            "id": U,
            "polity_id": U,
            "scheme_code": U,
            "code": U,
            "parent_id": U,
            "area_id": U,
            "kind": U,
            "valid_from": D,
            "valid_to": D,
            "pipeline": U,
        },
    ),
    "result_set": (
        """select t.id::text, t.election_id::text, t.contest_id::text, t.stage::text, t.kind::text,
                  t.status::text, t.is_default, t.as_of, c.pipeline
           from result_set t""",
        {
            "id": U,
            "election_id": U,
            "contest_id": U,
            "stage": U,
            "kind": U,
            "status": U,
            "is_default": B,
            "as_of": pl.Datetime("us", "UTC"),
            "pipeline": U,
        },
    ),
    "external_id": (
        "select t.entity_id::text, t.scheme, t.value, c.pipeline from external_id t",
        {"entity_id": U, "scheme": U, "value": U, "pipeline": U},
    ),
    "slug": (
        "select t.path, t.entity_id::text, t.is_current, c.pipeline from slug t",
        {"path": U, "entity_id": U, "is_current": B, "pipeline": U},
    ),
}
REFRESH = 86400
REBUILD = 3600


class Reference(NamedTuple):
    ids: frozenset[str]
    packages: frozenset[str]
    columns: dict[str, Columns]
    root: Path | None = None


def _columns(conn: Connection, table: str) -> Columns:
    return {
        str(name): (bool(required), bool(fixed), [str(label) for label in labels])
        for name, required, fixed, labels in conn.execute(READY_COLUMNS, [f"public.{table}"]).fetchall()
    }


def _type(conn: Connection, table: str, column: str) -> LiteralString:
    found = conn.execute(
        """select format_type(a.atttypid, a.atttypmod) from pg_attribute a
           where a.attrelid = %s::regclass and a.attname = %s and not a.attisdropped""",
        [f"public.{table}", column],
    ).fetchone()
    if found is None:
        raise LookupError(f"{table}.{column} does not exist in the database")
    return cast(LiteralString, str(found[0]))


def snapshot(target: Path) -> None:
    with store.connect() as conn:
        ids = [str(value) for (value,) in conn.execute(SNAPSHOT_IDS).fetchall()]
        ids += [str(code) for (code,) in conn.execute("select code from scheme").fetchall()]
        packages = [
            str(key)
            for (key,) in conn.execute(
                "select distinct scope->>'package' from changeset where scope ? 'package'"
            ).fetchall()
        ]
        columns = {table: _columns(conn, table) for table in [*TABLES, *FLOW_TABLES]}
    body = {
        "created": datetime.now(UTC).isoformat(),
        "ids": sorted(ids),
        "packages": sorted(packages),
        "columns": columns,
    }
    partial = target.with_name(f".{target.name}.part")
    partial.write_bytes(gzip.compress(json.dumps(body).encode()))
    partial.replace(target)
    print(f"{target}: {len(ids):,} ids, {len(packages):,} packages", flush=True)


def _partition(conn: Connection, target: Path, part: str) -> dict[str, int]:
    folder = target / ("other" if part == "_" else part.lower())
    partial = target / f".{folder.name}.part"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    conn.execute("drop table if exists ref_changeset")
    conn.execute(
        sql.SQL("create temp table ref_changeset as select id, pipeline from changeset where {} = %s").format(
            sql.SQL(PART)
        ),
        [part],
    )
    conn.execute("analyze ref_changeset")
    conn.execute("drop table if exists ref_name")
    conn.execute(
        """create temp table ref_name as select distinct on (n.entity_id) n.entity_id, n.text
           from name n join ref_changeset c on c.id = n.changeset_id
           order by n.entity_id, n.is_preferred desc, n.lang = 'en' desc, n.text"""
    )
    conn.execute("create index on ref_name (entity_id)")
    conn.execute("analyze ref_name")
    counts: dict[str, int] = {}
    with _connect() as con:
        for table, (query, schema) in PUBLISH.items():
            joined = sql.SQL("{} join ref_changeset c on c.id = t.changeset_id").format(sql.SQL(query))
            counts[table] = _dump(conn, con, joined, schema, partial / f"{table}.parquet")
    if folder.exists():
        shutil.rmtree(folder)
    partial.replace(folder)
    return counts


def publish(target: Path) -> None:
    index_file = target / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    partitions: dict[str, dict[str, object]] = index.get("partitions", {})
    previous: str | None = index.get("full")
    began = datetime.now(UTC)
    full = previous is None or (began - datetime.fromisoformat(previous)).total_seconds() > REFRESH

    def save(full_at: str | None) -> None:
        partial = target / ".index.json.part"
        partial.write_text(json.dumps({"full": full_at, "partitions": partitions}, indent=1, sort_keys=True))
        partial.replace(index_file)

    with store.connect() as conn:
        latest = conn.execute(
            sql.SQL("select {}, max(coalesce(published_at, created_at)) from changeset group by 1").format(
                sql.SQL(PART)
            )
        ).fetchall()
        stale = sorted(
            str(part)
            for part, when in latest
            if full
            or part not in partitions
            or (
                when > datetime.fromisoformat(str(partitions[part]["built"]))
                and (began - datetime.fromisoformat(str(partitions[part]["built"]))).total_seconds() > REBUILD
            )
        )
        for part in stale:
            built = datetime.now(UTC)
            counts = _partition(conn, target, part)
            partitions[part] = {
                "folder": "other" if part == "_" else part.lower(),
                "built": built.isoformat(),
                "rows": counts,
            }
            save(previous)
            print(f"{part}: {sum(counts.values()):,} rows", flush=True)
    save(began.isoformat() if full else previous)
    print(f"{target}: {len(stale)} partitions rebuilt", flush=True)


def _reference(path: Path) -> Reference:
    body = json.loads(gzip.decompress(path.read_bytes()))
    columns = {
        table: {name: (bool(rule[0]), bool(rule[1]), [str(label) for label in rule[2]]) for name, rule in found.items()}
        for table, found in body["columns"].items()
    }
    ids = set(body["ids"])
    for folder in path.parent.iterdir():
        if folder.is_dir() and not folder.name.startswith("."):
            for table in PUBLISH:
                file = folder / f"{table}.parquet"
                if table not in ("area_code", "external_id", "slug") and file.exists():
                    ids |= set(pl.read_parquet(file, columns=["id"])["id"].to_list())
    return Reference(frozenset(ids), frozenset(body["packages"]), columns, path.parent)


def _ready(source: Connection | Reference, key: str, batch: set[str], minted: set[str]) -> list[str]:
    path = _resolve(key)
    manifest = _manifest(path)
    found: list[str] = []
    external = [str(value) for value in manifest["external"] if str(value) not in minted]
    if isinstance(source, Reference):
        missing = [value for value in external if value not in source.ids]
        where = (
            "the reference snapshot, the reference partitions downloaded beside it (reference/<cc>/, by the country"
            " of the pipeline that wrote the rows), or this batch"
        )
    else:
        missing = _missing(source, external)
        where = "the database and this batch"
    if missing:
        found.append(f"ready.external: {len(missing)} referenced ids are missing from {where}: {missing[:5]}")
    for depend in manifest["depends"]:
        loaded = (
            depend in source.packages
            if isinstance(source, Reference)
            else source.execute("select 1 from changeset where scope->>'package' = %s", [depend]).fetchone() is not None
        )
        if depend not in batch and not loaded:
            found.append(
                f"ready.depends: {depend} is neither loaded nor in this batch; build it and list it in run.json"
            )
    if not isinstance(source, Reference):
        found += _clashes(source, path, str(manifest["pipeline"]))
        found += _unit_clashes(source, path, str(manifest["pipeline"]))
        found += _given_up(source, manifest, batch)
    found += _held(source, path, str(manifest["pipeline"]))
    geometry = path / "area_geometry.parquet"
    if not isinstance(source, Reference) and geometry.exists():
        rows = pl.read_parquet(geometry, columns=["area_id", "valid_from", "valid_to"])
        taken = source.execute(
            """select g.area_id::text, g.valid_from, g.valid_to
               from unnest(%s::uuid[], %s::date[], %s::date[]) as n(area_id, valid_from, valid_to)
               join area_geometry g on g.area_id = n.area_id
                and daterange(g.valid_from, g.valid_to, '[)') && daterange(n.valid_from, n.valid_to, '[)')
               join changeset c on c.id = g.changeset_id
               where c.scope->>'package' is distinct from %s and c.id <> all(%s::uuid[])""",
            [
                rows["area_id"].to_list(),
                rows["valid_from"].to_list(),
                rows["valid_to"].to_list(),
                key,
                # The changesets a package adopts hand their rows to it, so their outlines are its own.
                list(manifest.get("adopts", [])),
            ],
        ).fetchall()
        if taken:
            found.append(
                f"ready.area_geometry: {len(taken)} outlines overlap the validity of one another package already "
                f"loaded for the same area: {taken[:5]}"
            )
    for table, (_, schema) in TABLES.items():
        file = path / f"{table}.parquet"
        if not file.exists():
            continue
        present = set(pl.read_parquet_schema(file))
        frame = pl.scan_parquet(file)
        rows = frame.select(pl.len()).collect().item()
        known = source.columns.get(table, {}) if isinstance(source, Reference) else _columns(source, table)
        for column in schema:
            if column not in present and column in OPTIONAL.get(table, ()):
                continue
            if column not in known:
                found.append(f"ready.column: {table}.{column} does not exist in the database")
                continue
            required, fixed, labels = known[column]
            nulls = rows if column not in present else frame.select(pl.col(column).null_count()).collect().item()
            if required and not fixed and nulls:
                found.append(f"ready.null: {table}.{column} is required and {nulls:,} rows leave it empty")
            if labels and column in present:
                bad = [
                    str(value)
                    for value in frame.select(pl.col(column).cast(pl.Utf8).unique()).collect().to_series().to_list()
                    if value is not None and value not in labels
                ]
                if bad:
                    found.append(f"ready.enum: {table}.{column} has values the database does not allow: {bad[:5]}")
    for table in FLOW_TABLES:
        known = source.columns.get(table) if isinstance(source, Reference) else _columns(source, table)
        if (path / f"{table}.parquet").exists() and known is not None:
            wanted = [FACT_REF.get(column, column) for column in FACTS[table]]
            found += [
                f"ready.column: {table}.{column} does not exist in the database"
                for column in wanted
                if column not in known
            ]
    return found


def _uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _given_up(conn: Connection, manifest: Manifest, batch: set[str]) -> list[str]:
    """Rows a package lists as external that its loaded versions hold in the database.

    A load deletes the rows its earlier versions hold and the new build no longer declares before it reads the
    build's values, so a reporting unit handed to another package that way is gone when the values that reference it
    resolve. The rows stay only when a package this one depends on declares them in the same run and loads first,
    taking them over; a package of the run whose build is unchanged is skipped and takes nothing.
    """
    external = sorted({str(value) for value in manifest["external"] if _uuid(str(value))})
    if not external:
        return []
    owned = [
        str(id)
        for (id,) in conn.execute("select id from changeset where scope->>'package' = %s", [manifest["key"]]).fetchall()
    ] + [str(id) for id in manifest.get("adopts", [])]
    if not owned:
        return []
    held: dict[str, str] = {}
    for table, (key_columns, _) in TABLES.items():
        if key_columns != ("id",) or "changeset_id" not in _columns(conn, table):
            continue
        rows = conn.execute(
            sql.SQL("select id::text from {} where id = any(%s::uuid[]) and changeset_id = any(%s::uuid[])").format(
                sql.Identifier(table)
            ),
            [external, owned],
        ).fetchall()
        held |= {str(id): table for (id,) in rows}
    for depend in manifest["depends"]:
        if depend not in batch or not held:
            continue
        other = _resolve(depend)
        loaded = conn.execute(
            "select scope->>'digest' from changeset where scope->>'package' = %s order by created_at desc limit 1",
            [depend],
        ).fetchone()
        if loaded is None or loaded[0] != _manifest(other)["digest"]:
            taken = _minted([other])
            held = {id: table for id, table in held.items() if id not in taken}
    if not held:
        return []
    tables = sorted(set(held.values()))
    problem = (
        f"ready.given_up: {len(held):,} rows ({', '.join(tables)}) this package lists as external are held in the "
        "database by its loaded version, so its load would delete them before resolving the values that use them: "
        f"{sorted(held)[:5]}; keep declaring them here and reference them from the other package instead"
    )
    return [problem]


def _declared_twice(keys: list[str]) -> list[str]:
    """Reporting units that two rows of the batch declare for one scheme, code, and overlapping validity.

    The database allows one unit per scheme and code at a time, so a unit two packages of a run both emit (or one
    package twice, under two ids) fails the load; each unit must come from one package, and the others list it as
    external.
    """
    frames = []
    for key in keys:
        file = _resolve(key) / "reporting_unit.parquet"
        if file.exists():
            present = set(pl.read_parquet_schema(file))
            frame = pl.read_parquet(
                file, columns=[c for c in ("id", "scheme_code", "code", "valid_from", "valid_to") if c in present]
            )
            for column in ("valid_from", "valid_to"):
                if column not in frame.columns:
                    frame = frame.with_columns(pl.lit(None, pl.Date).alias(column))
            frames.append(frame.with_columns(pl.lit(key).alias("package")))
    if not frames:
        return []
    units = pl.concat(frames, how="vertical_relaxed").with_row_index("n")
    low, high = date(1, 1, 1), date(9999, 12, 31)
    units = units.with_columns(pl.col("valid_from").fill_null(low), pl.col("valid_to").fill_null(high))
    repeated = units.filter(pl.len().over("scheme_code", "code") > 1)
    pairs = (
        repeated.join(repeated, on=["scheme_code", "code"], suffix="_b")
        .filter(
            (pl.col("n") < pl.col("n_b"))
            & (pl.col("valid_from") < pl.col("valid_to_b"))
            & (pl.col("valid_from_b") < pl.col("valid_to"))
        )
        .sort("scheme_code", "code", "n")
    )
    return [
        f"{row['package']}: ready.unit_twice: reporting unit {row['scheme_code']} {row['code']} for overlapping "
        f"validity is declared again by {row['package_b']}"
        + (" under another id" if row["id"] != row["id_b"] else "")
        + "; emit it from one package and list it as external in the others"
        for row in pairs.head(20).iter_rows(named=True)
    ] + ([f"ready.unit_twice: {pairs.height:,} repeated units in all"] if pairs.height > 20 else [])


def _rows(path: Path, table: str, columns: list[str]) -> pl.DataFrame:
    """A package table's columns, or an empty frame when the package holds no such table."""
    file = path / f"{table}.parquet"
    if not file.exists():
        return pl.DataFrame(schema={c: pl.Boolean if c == "is_current" else pl.Utf8 for c in columns})
    present = set(pl.read_parquet_schema(file))
    frame = pl.read_parquet(file, columns=[c for c in columns if c in present])
    return frame.with_columns(
        *(pl.lit(None, pl.Boolean if c == "is_current" else pl.Utf8).alias(c) for c in columns if c not in present)
    ).select(columns)


def _stored(source: Connection | Reference, table: str, columns: list[str], pairs: pl.DataFrame) -> pl.DataFrame:
    """The database's (or reference partitions') rows of external_id or slug that share a key column value with
    `pairs`, each with the pipeline of its changeset."""
    keys = ["scheme", "value"]
    wanted = [*columns, "pipeline"]
    if pairs.is_empty():
        return pl.DataFrame(schema={c: pl.Boolean if c == "is_current" else pl.Utf8 for c in wanted})
    if isinstance(source, Reference):
        if source.root is None:
            return pl.DataFrame(schema={c: pl.Boolean if c == "is_current" else pl.Utf8 for c in wanted})
        files = [
            file
            for folder in source.root.iterdir()
            if folder.is_dir() and not folder.name.startswith(".")
            for file in [folder / f"{table}.parquet"]
            if file.exists()
        ]
        if not files:
            return pl.DataFrame(schema={c: pl.Boolean if c == "is_current" else pl.Utf8 for c in wanted})
        held = pl.concat([pl.scan_parquet(file).select(wanted) for file in files], how="vertical_relaxed")
        if table == "slug":
            return (
                pl.concat(
                    [
                        held.join(pairs.select("path").unique().lazy(), on="path"),
                        held.join(pairs.select("entity_id").unique().lazy(), on="entity_id"),
                    ]
                )
                .unique()
                .collect()
            )
        return held.join(pairs.select(keys).unique().lazy(), on=keys).collect()
    if table == "external_id":
        rows = source.execute(
            """select e.entity_id::text, e.scheme, e.value, c.pipeline
               from unnest(%s::text[], %s::text[]) as n(scheme, value)
               join external_id e on e.scheme = n.scheme and e.value = n.value
               join changeset c on c.id = e.changeset_id""",
            [pairs["scheme"].to_list(), pairs["value"].to_list()],
        ).fetchall()
    else:
        rows = source.execute(
            """select s.path, s.entity_id::text, s.is_current, c.pipeline from slug s
               join changeset c on c.id = s.changeset_id
               where s.path = any(%s::text[]) or s.entity_id = any(%s::uuid[])""",
            [pairs["path"].to_list(), pairs["entity_id"].drop_nulls().unique().to_list()],
        ).fetchall()
    schema = {c: pl.Boolean if c == "is_current" else pl.Utf8 for c in wanted}
    return pl.DataFrame(rows, schema=schema, orient="row")


def _held(source: Connection | Reference, path: Path, pipeline: str) -> list[str]:
    """External ids and slugs a package adds that clash with rows another pipeline already holds.

    `external_id` is keyed by (scheme, value) and a slug by its path, and an entity has one current slug, so the load
    refuses any of these. Rows the same pipeline wrote are left out: a re-emit replaces them. Run against the
    database or against the reference partitions downloaded beside the snapshot (`external_id.parquet`,
    `slug.parquet`); a partition the reference lacks cannot be checked.
    """
    found: list[str] = []
    ids = _rows(path, "external_id", ["entity_id", "scheme", "value"]).filter(pl.col("value").is_not_null())
    held = _stored(source, "external_id", ["entity_id", "scheme", "value"], ids).filter(pl.col("pipeline") != pipeline)
    taken = held.join(ids.select("scheme", "value").unique(), on=["scheme", "value"])
    if taken.height:
        found.append(
            f"ready.external_id: {taken.height:,} external ids (scheme, value) are held under another pipeline's "
            f"changeset, so the load refuses them; list the entity as external and drop the row: "
            f"{[(r['scheme'], r['value'], r['pipeline']) for r in taken.head(5).iter_rows(named=True)]}"
        )
    slugs = _rows(path, "slug", ["path", "entity_id", "is_current"]).filter(pl.col("path").is_not_null())
    held = _stored(source, "slug", ["path", "entity_id", "is_current"], slugs).filter(pl.col("pipeline") != pipeline)
    owned = held.join(slugs.select("path", "entity_id").rename({"entity_id": "mine"}), on="path").filter(
        pl.col("entity_id") != pl.col("mine")
    )
    if owned.height:
        found.append(
            f"ready.slug: {owned.height:,} slug paths are held for another entity: "
            f"{[(r['path'], r['entity_id'], r['pipeline']) for r in owned.head(5).iter_rows(named=True)]}"
        )
    current = slugs.filter(pl.col("is_current").fill_null(True))
    second = (
        held.filter(pl.col("is_current").fill_null(False))
        .join(current.select("path", "entity_id").rename({"path": "mine"}), on="entity_id")
        .filter(pl.col("path") != pl.col("mine"))
    )
    if second.height:
        found.append(
            f"ready.slug: {second.height:,} entities already hold a current slug under another pipeline and get a "
            f"second one: {[(r['entity_id'], r['path'], r['mine']) for r in second.head(5).iter_rows(named=True)]}"
        )
    return found


def _repeated(keys: list[str]) -> list[str]:
    """What two packages of one run add twice, or one entity with two current slugs across the run.

    The load refuses an external id (scheme, value) written by two packages, a slug path for two entities, a second
    current slug for an entity, and an entity id two packages both add (`Package.id` hashes the pipeline, so
    packages of one pipeline derive the same ids for the same thing). Each entity comes from one package, and the
    others list it as external.
    """
    found: list[str] = []
    external, slug, entity = [], [], []
    for key in keys:
        path = _resolve(key)
        external.append(
            _rows(path, "external_id", ["entity_id", "scheme", "value"]).with_columns(pl.lit(key).alias("package"))
        )
        slug.append(_rows(path, "slug", ["path", "entity_id", "is_current"]).with_columns(pl.lit(key).alias("package")))
        for table in ENTITY_TABLES:
            if table in TABLES and TABLES[table][0] == ("id",):
                ids = _rows(path, table, ["id"]).drop_nulls().unique()
                entity.append(ids.with_columns(pl.lit(key).alias("package"), pl.lit(table).alias("table")))
    both = pl.concat(external).filter(pl.col("value").is_not_null()).unique(["package", "scheme", "value", "entity_id"])
    twice = (
        both.group_by("scheme", "value")
        .agg(pl.col("package").unique().sort(), pl.col("entity_id").n_unique().alias("n"))
        .filter(pl.col("package").list.len() > 1)
    )
    found += [
        f"ready.external_id: external id {row['scheme']} {row['value']} is added by {', '.join(row['package'])}; "
        "the load refuses a second row for one (scheme, value)"
        for row in twice.sort("scheme", "value").head(20).iter_rows(named=True)
    ]
    rows = pl.concat(slug).filter(pl.col("path").is_not_null()).unique(["package", "path", "entity_id"])
    split = (
        rows.group_by("path")
        .agg(pl.col("entity_id").n_unique().alias("n"), pl.col("package").unique().sort())
        .filter(pl.col("n") > 1)
    )
    found += [
        f"ready.slug: path {row['path']} is added for {row['n']} entities by {', '.join(row['package'])}"
        for row in split.sort("path").head(20).iter_rows(named=True)
    ]
    current = rows.filter(pl.col("is_current").fill_null(True))
    more = (
        current.group_by("entity_id")
        .agg(pl.col("path").unique().sort().alias("paths"), pl.col("package").unique().sort())
        .filter(pl.col("paths").list.len() > 1)
    )
    found += [
        f"ready.slug: entity {row['entity_id']} gets {len(row['paths'])} current slugs ({', '.join(row['paths'][:3])}) "
        f"from {', '.join(row['package'])}; an entity has one"
        for row in more.sort("entity_id").head(20).iter_rows(named=True)
    ]
    added = pl.concat(entity, how="vertical_relaxed").unique(["package", "table", "id"])
    dup = (
        added.group_by("id")
        .agg(pl.col("package").unique().sort(), pl.col("table").first())
        .filter(pl.col("package").list.len() > 1)
    )
    found += [
        f"ready.entity_twice: {row['table']} {row['id']} is added by {', '.join(row['package'])}; emit it from "
        "one package and list it as external in the others"
        for row in dup.sort("id").head(20).iter_rows(named=True)
    ]
    return found


def readiness(keys: list[str], reference: Path | None = None) -> None:
    minted = _minted(_resolve(key) for key in keys)
    if reference is not None:
        source = _reference(reference)
        found = [f"{key}: {problem}" for key in keys for problem in _ready(source, key, set(keys), minted)]
    else:
        with store.connect() as conn:
            found = [f"{key}: {problem}" for key in keys for problem in _ready(conn, key, set(keys), minted)]
    found += _declared_twice(keys)
    found += _repeated(keys)
    for line in found:
        print(line, flush=True)
    if found:
        raise SystemExit(1)
    print(f"{len(keys)} packages ready to load", flush=True)


def run(command: str, keys: list[str], reference: Path | None = None) -> None:
    if command == "snapshot":
        snapshot(Path(keys[0]))
        return
    if command == "reference":
        publish(Path(keys[0]))
        return
    if command == "export":
        for pipeline in keys:
            export(pipeline)
        return
    if command == "ready":
        readiness(keys, reference)
        return
    failed = False
    ready: list[tuple[Path, dict[str, object]]] = []
    for key in keys:
        path = _resolve(key)
        reasons = _manifest(path).get("accepted", {})
        meter = Meter()
        findings = check(path)
        checked = meter.step("checks")
        accepted: dict[str, object] = {}
        blocking = False
        for code, n, sample in findings:
            if code in reasons:
                accepted[code] = {"n": n, "reason": reasons[code], "sample": [list(map(str, row)) for row in sample]}
                print(f"{key}: {code} {n:,} accepted: {reasons[code]}", flush=True)
            else:
                blocking = True
                print(f"{key}: {code} {n:,} {sample}", flush=True)
        if blocking:
            failed = True
            continue
        print(f"{key}: checks passed, {checked}", flush=True)
        ready.append((path, accepted))
    if command == "load" and ready and not failed:
        with store.connect(autocommit=True) as conn:
            meter = Meter(conn)
            try:
                load(conn, ready, meter)
            except Exception:
                if meter.tripped:
                    raise MemoryError(
                        f"the database backend reached {meter.tripped:,} MB, over {SERVER_LIMIT:,} MB, "
                        "so the load was canceled and its open chunk rolled back"
                    ) from None
                raise
            finally:
                meter.close()
    if failed:
        raise SystemExit(1)


EXPORT_REASON = "published through the changeset path before this export"
KEPT = {"changeset_id", "recorded_from", "valid"}


def _longest(path: Path) -> int:
    """The length of the longest line in a file, in bytes."""
    longest = start = offset = 0
    with path.open("rb") as file:
        while chunk := file.read(1 << 24):
            at = chunk.find(b"\n")
            while at >= 0:
                longest = max(longest, offset + at - start)
                start = offset + at + 1
                at = chunk.find(b"\n", at + 1)
            offset += len(chunk)
    return max(longest, offset - start)


def _select(schema: dict[str, pl.DataType], absent: Iterable[str] = ()) -> sql.Composable:
    return sql.SQL(", ").join(
        sql.SQL("null")
        if c in absent
        else sql.SQL("t.{}::text").format(sql.Identifier(c))
        if kind == U
        else sql.SQL("t.{}").format(sql.Identifier(c))
        for c, kind in schema.items()
    )


def _dump(
    conn: Connection, con: duckdb.DuckDBPyConnection, query: sql.Composable, schema: dict[str, pl.DataType], out: Path
) -> int:
    staging = out.with_suffix(".csv")
    with (
        staging.open("wb") as sink,
        conn.cursor() as cursor,
        cursor.copy(sql.SQL("copy ({}) to stdout (format csv)").format(query)) as copy,
    ):
        for block in copy:
            sink.write(block)
    if staging.stat().st_size == 0:
        staging.unlink()
        return 0
    columns = ", ".join(f"'{c}': '{_duck(kind)}'" for c, kind in schema.items())

    def copy(options: str = "", parquet: str = "") -> None:
        con.execute(
            f"copy (select * from read_csv('{staging}', header = false, delim = ',', quote = '\"', escape = '\"',"
            f" allow_quoted_nulls = false{options}, columns = {{{columns}}})) to '{out}' (format parquet{parquet})"
        )

    try:
        copy()
    except duckdb.InvalidInputException as error:
        # A detailed outline is one line of several megabytes of hex, past DuckDB's default 2 MB line limit.
        if "Maximum line size" not in str(error):
            raise
        # One thread holds one buffer of that size, and small row groups keep the writer from holding hundreds of
        # megabytes of outlines, so the copy stays within the memory limit.
        line = _longest(staging) + 2
        found = con.execute("select current_setting('threads')").fetchone()
        con.execute("set threads = 1")
        try:
            copy(f", max_line_size = {line}, buffer_size = {line}", ", row_group_size 1024")
        finally:
            con.execute(f"set threads = {4 if found is None else int(found[0])}")
    staging.unlink()
    found = con.execute(f"select count(*) from read_parquet('{out}')").fetchone()
    rows = 0 if found is None else int(found[0])
    if rows == 0:
        out.unlink()
    return rows


def _connect() -> duckdb.DuckDBPyConnection:
    spill = ROOT / ".duckdb"
    spill.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(config={"memory_limit": CHECK_MEMORY, "threads": 4, "temp_directory": str(spill)})


def _adopted(conn: Connection, pipeline: str) -> tuple[list[str], list[str]]:
    row = conn.execute(
        """select coalesce(array_agg(distinct c.id::text), '{}'),
                  coalesce(array_agg(distinct k) filter (where k is not null), '{}')
           from changeset c left join lateral unnest(c.artifact_keys) k on true
           where c.pipeline = %s and c.status = 'published' and not c.scope ? 'package'""",
        [pipeline],
    ).fetchone()
    if row is None:
        return [], []
    return sorted(row[0]), sorted(row[1])


def _cited(conn: Connection, adopted: list[str], new: pl.DataFrame | None) -> list[dict[str, str]]:
    """Every file a Draft package cites, with its url and sha256, as the inbox verifies each one by sha256: the
    artifacts its own rows record, then any source file its adopted changesets cite. The changesets' own parquets
    are left out; they are not sources, the package's rows replace them, and old ones are pruned from data/."""
    cited: dict[str, dict[str, str]] = {}
    if new is not None:
        for row in new.select("storage_key", "url", "sha256").unique("storage_key").iter_rows(named=True):
            cited[str(row["storage_key"])] = {
                "url": str(row["url"] or ""),
                "sha256": str(row["sha256"]),
                "storage_key": str(row["storage_key"]),
            }
    rest = [storage for storage in adopted if storage not in cited and not storage.startswith("changesets/")]
    for storage, url, digest in conn.execute(
        """select distinct on (storage_key) storage_key, url, sha256
           from source_artifact where storage_key = any(%s) order by storage_key, retrieved_at desc""",
        [rest],
    ).fetchall():
        cited[storage] = {"url": str(url or ""), "sha256": str(digest), "storage_key": storage}
    missing = sorted(set(rest) - set(cited))
    if missing:
        raise ValueError(
            f"{len(missing)} files the adopted changesets cite have no source_artifact row, such as {missing[0]}"
        )
    return [cited[storage] for storage in sorted(cited)]


def _canonicalize(file: Path, columns: list[str]) -> None:
    pl.read_parquet(file).with_columns(
        pl.col(column).map_elements(canonical, return_dtype=pl.String) for column in columns
    ).write_parquet(file, statistics=False)


def _seal(
    key: str,
    pipeline: str,
    summary: str,
    out: Path,
    counts: dict[str, int],
    adopts: list[str],
    sources: list[dict[str, str]],
    reason: str | dict[str, str] | None,
) -> Manifest:
    con = _connect()
    for table, (_, schema) in [*TABLES.items(), *((t, (None, s)) for t, s in FACTS.items())]:
        file = out / f"{table}.parquet"
        types = ", ".join(f'null::{_duck(kind)} as "{c}"' for c, kind in schema.items())
        body = (
            f"select {_duck_select(table, schema, file)} from read_parquet('{file}')"
            if file.exists()
            else f"select {types} where false"
        )
        con.execute(f'create or replace view "{table}" as {body}')
    for table in OUTSIDE:
        con.execute(f'create or replace view "{table}" as select null::varchar as id where false')
    external: set[str] = set()
    for table, column, target in REFERENCES:
        if table not in counts:
            continue
        known = f' and "{column}" not in (select "{TARGET_KEY.get(target, "id")}" from "{target}")'
        rows = con.execute(
            f'select distinct "{column}"::varchar from "{table}" where "{column}" is not null{known}'
        ).fetchall()
        external |= {str(row[0]) for row in rows}
    digest = _digest(out, counts, con)
    con.close()
    manifest: Manifest = {
        "key": key,
        "pipeline": pipeline,
        "summary": summary,
        "depends": [],
        "external": sorted(external),
        "parser_commit": store.checkout()["sha"],
        "built_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "accepted": {},
        "tables": counts,
        "digest": digest,
        "adopts": adopts,
    }
    (out / "package.json").write_text(json.dumps(manifest, indent=1) + "\n")
    for code, n, _ in check(out):
        if code.startswith(("unique.", "reference.")):
            raise ValueError(f"{key}: the build fails {code} ({n:,})")
        why = reason.get(code) if isinstance(reason, dict) else reason
        if why is None:
            print(f"{key}: {code} {n:,} is not accepted", flush=True)
        else:
            manifest["accepted"][code] = why
    (out / "package.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def export(pipeline: str) -> Path:
    key = f"{pipeline}/all"
    out = folder(key) / "build"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    con = _connect()
    meter = Meter()
    with store.connect() as conn:
        conn.execute("set transaction isolation level repeatable read, read only")
        owned, artifacts = _adopted(conn, pipeline)
        if not owned:
            raise LookupError(f"{pipeline} has no published changesets outside packages")
        mine = sql.SQL("t.changeset_id = any({}::uuid[])").format(sql.Literal(owned))
        counts: dict[str, int] = {}
        for table, (_, schema) in TABLES.items():
            absent = [column for column in OPTIONAL.get(table, ()) if column not in _columns(conn, table)]
            query = sql.SQL("select {} from {} t where {}").format(_select(schema, absent), sql.Identifier(table), mine)
            counts[table] = _dump(conn, con, query, schema, out / f"{table}.parquet")
        sets = sql.SQL("r.changeset_id = any({}::uuid[])").format(sql.Literal(owned))
        places = 0
        for table in ("result_value", "result_flow"):
            found = conn.execute(
                sql.SQL(
                    """select coalesce(max(min_scale(v.value)), 0) from result_set r
                       cross join lateral (select v.value from {} v where v.result_set_k = r.k offset 0) v
                       where {}"""
                ).format(sql.Identifier(table), sets)
            ).fetchone()
            places = max(places, 0 if found is None else int(found[0]))
        if places > SCALE:
            raise ValueError(
                f"{pipeline}: values need {places} decimal places, more than the {SCALE} the package format holds"
            )
        value = "v.flags, v.value" if places else "v.flags, v.value::int8"
        for table, rest, schema in (
            ("result_value", value, {**FACTS["result_value"], "value": pl.Decimal(38, places) if places else I64}),
            ("result_flow", value, {**FACTS["result_flow"], "value": pl.Decimal(38, places) if places else I64}),
            ("value_locator", "v.page_id::text, v.source_id::text, v.x0, v.y0, v.x1, v.y1", FACTS["value_locator"]),
        ):
            options = (
                "(select o.id::text from contest_option o where o.k = v.from_option_k and v.from_option_k <> 0),"
                if table == "result_flow"
                else ""
            )
            query = sql.SQL(
                """select r.id::text, (select u.id::text from reporting_unit u where u.k = v.unit_k), {options}
                          (select o.id::text from contest_option o where o.k = v.option_k and v.option_k <> 0),
                          v.channel, v.measure, v.count_round, {rest}
                   from result_set r cross join lateral (select * from {t} v where v.result_set_k = r.k offset 0) v
                   where {sets}"""
            ).format(options=sql.SQL(options), rest=sql.SQL(rest), t=sql.Identifier(table), sets=sets)
            counts[table] = _dump(conn, con, query, schema, out / f"{table}.parquet")
        outside = conn.execute(
            """select c.relname from pg_attribute a join pg_class c on c.oid = a.attrelid
               where c.relnamespace = 'public'::regnamespace and a.attname = 'changeset_id' and not a.attisdropped
                 and c.relkind in ('r', 'p') and not c.relispartition and c.relname not like '%%\\_history'
                 and c.relname <> all(%s) order by 1""",
            [[*TABLES, "entity", "changeset"]],
        ).fetchall()
        left: list[str] = []
        for (table,) in outside:
            found = conn.execute(
                sql.SQL("select count(*) from {} t where {}").format(sql.Identifier(table), mine)
            ).fetchone()
            if found is not None and found[0]:
                left.append(f"{table} {found[0]:,}")
        dropped: list[str] = []
        for table, (_, schema) in TABLES.items():
            if not counts[table]:
                continue
            extra = conn.execute(
                """select a.attname, pg_get_expr(d.adbin, d.adrelid) from pg_attribute a
                   left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
                   where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped and a.attgenerated = ''
                     and a.attidentity = '' and a.attname <> all(%s) order by a.attnum""",
                [table, [*schema, *KEPT]],
            ).fetchall()
            for column, default in extra:
                differs = (
                    sql.SQL("t.{} is not null").format(sql.Identifier(column))
                    if default is None or "(" in default
                    else sql.SQL("t.{} is distinct from {}").format(sql.Identifier(column), sql.SQL(default))
                )
                found = conn.execute(
                    sql.SQL("select count(*) from {} t where {} and {}").format(sql.Identifier(table), mine, differs)
                ).fetchone()
                if found is not None and found[0]:
                    dropped.append(f"{table}.{column} {found[0]:,}")
    exported = meter.step("export")
    counts = {table: rows for table, rows in counts.items() if rows}
    con.close()
    for table in counts:
        if table in OPTIONAL:
            _trim(table, out / f"{table}.parquet")
        columns = [column for column in TABLES[table][1] if column in JSON_COLUMNS] if table in TABLES else []
        if columns:
            _canonicalize(out / f"{table}.parquet", columns)
    _seal(
        key,
        pipeline,
        f"{pipeline}, exported from the changeset path",
        out,
        counts,
        owned,
        [{"storage_key": artifact} for artifact in artifacts],
        EXPORT_REASON,
    )
    print(f"{key}: {', '.join(f'{t} {n:,}' for t, n in counts.items())}; {len(owned):,} changesets adopted", flush=True)
    print(f"{key}: {exported}, then {meter.step('external and checks')}", flush=True)
    if left:
        print(f"{key}: rows left on the changeset path: {', '.join(left)}", flush=True)
    if dropped:
        print(f"{key}: columns outside the package format, kept in place: {', '.join(dropped)}", flush=True)
    return out


def _column(frame: pl.DataFrame, column: str, kind: pl.DataType, default: object | None) -> pl.Expr:
    if column not in frame.columns:
        return pl.lit(default, dtype=kind).alias(column)
    values = pl.col(column)
    dtype = frame.schema[column]
    if isinstance(kind, pl.Datetime):
        if dtype == pl.String:
            values = values.str.to_datetime(time_unit="us", time_zone="UTC")
        elif isinstance(dtype, pl.Datetime) and dtype.time_zone is None:
            values = values.dt.replace_time_zone("UTC")
    return values.cast(kind).alias(column)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict | list):
        return json.dumps(value)
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, float):
        return repr(value)
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


# Merges turn the names and slugs of the entities they fold into redirects on the survivor, so a Draft carries those
# rows on its own survivors as they stand. It also carries the pipeline's own non-preferred names: the changeset path
# keeps a name a later emit demoted as an alias rather than deleting it.
REDIRECTED = ("name", "slug")
GEOMETRY_COLUMNS = ("geom", "label_point")


class Draft(Changeset):
    def __init__(
        self,
        conn: Connection,
        *,
        pipeline: str,
        version: str,
        summary: str,
        polity: str | None,
        kind: str = "import",
        snapshot: Snapshot | None = None,
    ) -> None:
        super().__init__(
            conn, pipeline=pipeline, version=version, summary=summary, polity=polity, kind=kind, snapshot=snapshot
        )
        self.own_entities: set[UUID] = {
            id
            for (id,) in conn.execute(
                "select e.id from entity e join changeset c on c.id = e.changeset_id where c.pipeline = %s", [pipeline]
            )
        }
        self.own_keys: set[tuple[str, str]] = {
            (str(scheme), str(value))
            for scheme, value in conn.execute(
                """select x.scheme, x.value from external_id x join changeset c on c.id = x.changeset_id
                   where c.pipeline = %s and c.status = 'published'""",
                [pipeline],
            )
        }
        self.accepted: dict[str, str] = {}
        # Changesets the package takes over besides the pipeline's published ones the database lists, so a build
        # without a database still names them and `ready.area_geometry` counts their outlines as its own.
        self.adopts: list[str] = []
        self.parts: dict[str, list[Path]] = {}
        self.named: set[str] = set()

    def accept(self, code: str, reason: str) -> None:
        self.accepted[code] = reason

    def names_in_full(self, entities: Iterable[UUID]) -> None:
        """The module writes every name these entities carry from this pipeline, merge survivors included: their stored
        names, preferred or not, are not carried over, so the build's names replace them."""
        self.named |= {str(entity) for entity in entities}

    def part(self, table: str, path: Path) -> None:
        """Stage a Parquet file of fact rows too large to hold in memory; write streams it into the build. Parts skip
        the changeset path's tacit check, which marks a counted result set whose values are all zero as tacit, so a
        module that stages parts must not depend on it."""
        if table not in FACTS:
            raise ValueError(f"{table} is not a fact table; stage its rows with add or frame")
        self.parts.setdefault(table, []).append(path)

    def key(self, scheme: str, value: str) -> UUID:
        pair = (scheme, value)
        if pair not in self._keyed and pair not in self.own_keys:
            known = self.snapshot.table(
                self.conn,
                f"external_id:{scheme}",
                "select value, entity_id from external_id where scheme = %s",
                [scheme],
            )
            if known.get(value) is not None:
                self._keyed.add(pair)
        id = super().key(scheme, value)
        if pair not in self._keyed:
            self._keyed.add(pair)
            stored = self.snapshot.tables[f"external_id:{scheme}"].get(value)
            self.add("external_id", entity_id=id if stored is None else stored, scheme=scheme, value=value)
        if id in self.own_entities:
            self.minted.add(id)
        return id

    def area(self, scheme: str, code: str, on: date | None = None) -> UUID:
        id = super().area(scheme, code, on)
        if id in self.own_entities:
            self.minted.add(id)
        return id

    def unit(self, scheme: str, code: str, on: date | None = None) -> UUID:
        id = super().unit(scheme, code, on)
        if id in self.own_entities:
            self.minted.add(id)
        return id

    def absorbed(self) -> list[str]:
        """The pipeline's own entities that a merge folded others into. A merge turns the names and slugs of the
        entities it folds into redirects on the survivor, so the survivor's rows carry over as they stand."""
        return sorted({str(into) for into in self.survivors.values() if into in self.own_entities})

    def foreign(self, scheme: str, like: str = "%") -> set[str]:
        return {
            str(value)
            for value, entity in self.conn.execute(
                "select value, entity_id from external_id where scheme = %s and value like %s", [scheme, like]
            )
            if entity not in self.own_entities
        }

    def _settle(self, table: str, frame: pl.DataFrame) -> pl.DataFrame:
        if frame.height == 0:
            return frame
        keyed = None if table in DERIVED_IDS or table not in TABLES else TABLES[table][0]
        minted = table in DERIVED_IDS or keyed == ("id",) and "id" not in frame.columns
        if minted:
            keyed = None
        elif keyed is not None and not set(keyed) <= set(frame.columns):
            return frame
        columns = [column for column in frame.columns if column != "id" or keyed is not None]
        frame = frame.unique(subset=columns, maintain_order=True)
        required = _not_null(self.conn, table)
        types: dict[str, str] = dict(
            self.conn.execute(
                """select a.attname, format_type(a.atttypid, a.atttypmod) from pg_attribute a
                   where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped""",
                [f"public.{table}"],
            ).fetchall()
        )
        staged = sql.SQL(
            "(select r.draft_row - 1 as draft_row, {} from unnest({}) with ordinality as r({}, draft_row))"
        ).format(
            sql.SQL(", ").join(
                sql.SQL("r.{0}::{1} as {0}").format(sql.Identifier(column), sql.SQL(cast(LiteralString, types[column])))
                for column in columns
            ),
            sql.SQL(", ").join(sql.SQL("%s::text[]") for _ in columns),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        arrays: list[object] = [[_text(value) for value in frame[column].to_list()] for column in columns]
        pipeline = str(self.manifest["pipeline"])
        if keyed is not None:
            found = self.conn.execute(
                sql.SQL(
                    """select s.draft_row, null, case when ({p}) is not distinct from ({s}) then c.pipeline end
                       from {staged} s left join public.{table} p on {on}
                       left join changeset c on c.id = p.changeset_id order by s.draft_row"""
                ).format(
                    p=sql.SQL(", ").join(sql.SQL("p.{}").format(sql.Identifier(c)) for c in columns),
                    s=sql.SQL(", ").join(sql.SQL("s.{}").format(sql.Identifier(c)) for c in columns),
                    staged=staged,
                    table=sql.Identifier(table),
                    on=sql.SQL(" and ").join(sql.SQL("p.{0} = s.{0}").format(sql.Identifier(c)) for c in keyed),
                ),
                arrays,
            ).fetchall()
        else:
            same = sql.SQL(" and ").join(
                sql.SQL("p.{0} = s.{0}" if column in required else "p.{0} is not distinct from s.{0}").format(
                    sql.Identifier(column)
                )
                for column in columns
            )
            derived = sql.SQL(
                "overlay(overlay(md5(concat_ws(chr(31), {}, {}, {})) placing '8' from 13 for 1)"
                " placing '8' from 17 for 1)"
            ).format(
                sql.Literal(table),
                sql.Literal(pipeline),
                sql.SQL(", ").join(
                    sql.SQL("{}, coalesce(s.{}::text, chr(30))").format(sql.Literal(column), sql.Identifier(column))
                    for column in sorted(columns)
                ),
            )
            found = self.conn.execute(
                sql.SQL(
                    """select s.draft_row, {id}, m.pipeline
                       from {staged} s left join lateral (
                         select {existing} as id, c.pipeline from public.{table} p
                         join changeset c on c.id = p.changeset_id
                         where {same} order by c.pipeline = %s desc limit 1) m on true
                       order by s.draft_row"""
                ).format(
                    id=sql.SQL("coalesce(m.id, {})").format(derived) if minted else sql.SQL("null"),
                    existing=sql.SQL("p.id::text") if minted else sql.SQL("null::text"),
                    staged=staged,
                    table=sql.Identifier(table),
                    same=same,
                ),
                [*arrays, pipeline],
            ).fetchall()
        keep = [owner in (None, pipeline) for _, _, owner in found]
        if minted:
            frame = frame.with_columns(pl.Series("id", [str(id) for _, id, _ in found], dtype=pl.String))
        return frame.filter(pl.Series(keep))

    def _carry(self, table: str, schema: dict[str, pl.DataType]) -> pl.DataFrame | None:
        if table not in TABLES:
            return None
        key = TABLES[table][0]
        column = "entity_id" if "entity_id" in schema else "id" if key == ("id",) else None
        if column is None:
            return None
        absent = [name for name in OPTIONAL.get(table, ()) if name not in _columns(self.conn, table)]
        pipeline = str(self.manifest["pipeline"])
        foreign = (
            sql.SQL(
                """ or not exists (select from entity e join changeset o on o.id = e.changeset_id
                                   where e.id = t.entity_id and o.pipeline = {})"""
            ).format(sql.Literal(pipeline))
            if column == "entity_id"
            else sql.SQL("")
        )
        redirected = sql.SQL(
            " or t.entity_id = any(%s::uuid[]) or not t.is_preferred"
            if table == "name"
            else " or t.entity_id = any(%s::uuid[])"
            if table in REDIRECTED
            else ""
        )
        named = table == "name" and bool(self.named)
        rows = self.conn.execute(
            sql.SQL(
                """select {} from {} t join changeset c on c.id = t.changeset_id
                   where c.pipeline = %s and c.status = 'published'
                     and (t.{} in (select entity_id from entity_merge){}{}){}"""
            ).format(
                _select(schema, absent),
                sql.Identifier(table),
                sql.Identifier(column),
                foreign,
                redirected,
                sql.SQL(" and t.entity_id <> all(%s::uuid[])" if named else ""),
            ),
            [
                pipeline,
                *([self.absorbed()] if table in REDIRECTED else []),
                *([sorted(self.named)] if named else []),
            ],
        ).fetchall()
        if not rows:
            return None
        frame = pl.DataFrame(
            [[json.dumps(value) if isinstance(value, dict | list) else value for value in row] for row in rows],
            schema=list(schema),
            orient="row",
            infer_schema_length=None,
        )
        return frame.select(_column(frame, name, kind, None) for name, kind in schema.items()).with_columns(
            pl.col(name).map_elements(canonical, return_dtype=pl.String) for name in schema if name in JSON_COLUMNS
        )

    def write(self) -> Path:
        pipeline = str(self.manifest["pipeline"])
        key = f"{pipeline}/all"
        scope = self.manifest.get("scope")
        extra = sorted(set(scope) - {"polity"}) if isinstance(scope, dict) else []
        if extra:
            raise ValueError(f"{pipeline} sets changeset scope {extra}, which a package cannot express")
        tables = self._tables()
        for table, frame in list(tables.items()):
            if table in TABLES or table in DERIVED_IDS:
                tables[table] = self._settle(table, frame)
        out = folder(key) / "build"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        counts: dict[str, int] = {}
        for table, frame in tables.items():
            schema = TABLES[table][1] if table in TABLES else FACTS.get(table)
            if schema is None:
                raise ValueError(f"{pipeline} writes {table}, which is outside the package format in ea/package.py")
            unknown = sorted(set(frame.columns) - set(schema))
            if unknown:
                raise ValueError(f"{pipeline} writes {table} columns outside the package format: {unknown}")
            if frame.height == 0:
                continue
            if table in FACTS and "value" in frame.columns:
                frame = frame.with_columns(_numbers(frame["value"]))
                schema = {**schema, "value": _settle([frame.lazy()], table)}
            defaults = self.defaults.get(table, {})
            final = frame.select(
                _column(frame, column, kind, defaults.get(column)) for column, kind in schema.items()
            ).with_columns(
                pl.col(column).map_elements(canonical, return_dtype=pl.String)
                for column in schema
                if column in JSON_COLUMNS
            )
            reals = [
                column for column, kind in schema.items() if kind == F and _type(self.conn, table, column) == "real"
            ]
            final = final.with_columns(pl.col(column).cast(pl.Float32).cast(U).cast(F) for column in reals)
            # The database gives geometry back as upper-case hex EWKB, so a module's lower-case hex is written the
            # same way and its rows match the export's.
            final = final.with_columns(
                pl.col(column).str.to_uppercase() for column in GEOMETRY_COLUMNS if column in final.columns
            )
            if table in REDIRECTED:
                redirects = [entity for entity in self.absorbed() if table != "name" or entity not in self.named]
                final = final.filter(~pl.col("entity_id").is_in(redirects))
            carried = self._carry(table, schema)
            if carried is not None:
                kept = TABLES[table][0]
                final = pl.concat(
                    [final, carried.join(final, on=list(kept), how="anti") if kept else carried],
                    how="vertical_relaxed",
                ).unique(maintain_order=True)
            final.write_parquet(out / f"{table}.parquet", statistics=False)
            if table in OPTIONAL:
                _trim(table, out / f"{table}.parquet")
            counts[table] = final.height
        for table, paths in self.parts.items():
            schema = FACTS[table]
            defaults = self.defaults.get(table, {})
            pieces = [pl.scan_parquet(path) for path in paths]
            if table in counts:
                pieces.insert(0, pl.scan_parquet(out / f"{table}.parquet"))
            pieces = [
                _conform(
                    piece.with_columns(
                        pl.lit(defaults[column]).alias(column)
                        for column in schema
                        if column in defaults and column not in piece.collect_schema().names()
                    ),
                    schema,
                    True,
                )
                for piece in pieces
            ]
            kind = _settle(pieces, table)
            staged = out / f".{table}.parquet"
            pl.concat([piece.with_columns(pl.col("value").cast(kind)) for piece in pieces]).sink_parquet(
                staged, statistics=False
            )
            staged.replace(out / f"{table}.parquet")
            counts[table] = int(pl.scan_parquet(out / f"{table}.parquet").select(pl.len()).collect().item())
        for table, (_, schema) in TABLES.items():
            if table in counts:
                continue
            carried = self._carry(table, schema)
            if carried is not None and carried.height:
                carried.write_parquet(out / f"{table}.parquet", statistics=False)
                if table in OPTIONAL:
                    _trim(table, out / f"{table}.parquet")
                counts[table] = carried.height
        owned, artifacts = _adopted(self.conn, pipeline)
        owned = sorted({*owned, *self.adopts})
        new = tables.get("source_artifact")
        _seal(
            key,
            pipeline,
            str(self.manifest["summary"]),
            out,
            counts,
            owned,
            _cited(self.conn, artifacts, new),
            self.accepted,
        )
        print(
            f"{key}: {', '.join(f'{t} {n:,}' for t, n in counts.items())}; {len(owned):,} changesets adopted",
            flush=True,
        )
        return out
