import json
from pathlib import Path
from typing import Literal, LiteralString, TypedDict, cast
from uuid import UUID, uuid7

from psycopg import sql

from ea.changeset import Changeset
from ea.store import Connection

VERSION = "1"

AuthorKind = Literal["user", "agent"]
Kind = Literal["edit", "correction", "digitization"]

EDITABLE: dict[str, dict[str, bool]] = {
    "name": {
        "entity_id": True,
        "text": True,
        "lang": True,
        "script": False,
        "kind": True,
        "valid_from": False,
        "valid_from_precision": False,
        "valid_to": False,
        "valid_to_precision": False,
        "is_preferred": False,
    },
    "note": {
        "entity_id": True,
        "kind": True,
        "body_md": True,
        "lang": False,
        "valid_from": False,
        "valid_from_precision": False,
        "valid_to": False,
        "valid_to_precision": False,
    },
}

CITED = {"name": True, "note": False}

PROVENANCE = {"name": "source_id"}


class Evidence(TypedDict, total=False):
    source: str
    locator: object
    page: int
    region: list[float]
    quote: str


class Op(TypedDict, total=False):
    op: str
    table: str
    id: str
    ref: str
    values: dict[str, object]
    evidence: list[str]


class Payload(TypedDict, total=False):
    summary: str
    rationale: str
    polity: str
    ops: list[Op]
    evidence: dict[str, Evidence]


def _uuid(value: object, where: str) -> UUID:
    if not isinstance(value, str):
        raise TypeError(f"{where}: expected an id, got {value!r}")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"{where}: {value!r} is not a UUID") from None


def compile(
    conn: Connection,
    payload: Payload,
    author: UUID,
    *,
    author_kind: AuthorKind = "user",
    kind: Kind = "edit",
    proposal: UUID | None = None,
) -> Path:
    summary = payload.get("summary", "").strip()
    if not summary:
        raise ValueError("summary is required")
    ops = payload.get("ops") or []
    if not ops:
        raise ValueError("ops is empty")
    if conn.execute("select from users where id = %s", [author]).fetchone() is None:
        raise ValueError(f"author {author} is not a user")
    evidence = payload.get("evidence") or {}
    sources: dict[str, UUID] = {}
    for key, item in evidence.items():
        if "source" not in item:
            raise ValueError(f"evidence {key}: only existing sources are accepted until URL and upload evidence lands")
        source = _uuid(item["source"], f"evidence {key}.source")
        if conn.execute("select from source where id = %s", [source]).fetchone() is None:
            raise ValueError(f"evidence {key}: source {source} does not exist")
        sources[key] = source

    cs = Changeset(conn, pipeline="contrib", version=VERSION, summary=summary, polity=payload.get("polity"), kind=kind)
    cs.manifest["author_kind"] = author_kind
    cs.manifest["author_user_id"] = str(author)
    if payload.get("rationale"):
        cs.manifest["rationale"] = payload.get("rationale")
    if proposal is not None:
        cast(dict[str, object], cs.manifest["scope"])["proposal"] = str(proposal)

    refs: dict[str, UUID] = {}
    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        if "ref" in op:
            if op.get("op") != "add":
                raise ValueError(f"{where}: only add takes a ref")
            if op["ref"] in refs:
                raise ValueError(f"{where}: ref {op['ref']!r} is used twice")
            refs[op["ref"]] = uuid7()

    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        table = op.get("table", "")
        if table not in EDITABLE:
            raise ValueError(f"{where}: table {table!r} is not editable yet")
        columns = EDITABLE[table]
        values = dict(op.get("values") or {})
        unknown = values.keys() - columns.keys()
        if unknown:
            raise ValueError(f"{where}: {table} has no editable column {', '.join(sorted(unknown))}")
        for column, value in values.items():
            if isinstance(value, str) and value.startswith("$"):
                if value[1:] not in refs:
                    raise ValueError(f"{where}.{column}: unknown ref {value}")
                values[column] = refs[value[1:]]
        cited = op.get("evidence") or []
        missing = [key for key in cited if key not in sources]
        if missing:
            raise ValueError(f"{where}: unknown evidence {', '.join(missing)}")
        if CITED[table] and not cited:
            raise ValueError(f"{where}: a change to {table} needs evidence")

        match op.get("op"):
            case "add":
                absent = [column for column, required in columns.items() if required and values.get(column) is None]
                if absent:
                    raise ValueError(f"{where}: add {table} needs {', '.join(absent)}")
                row: dict[str, object] = {"id": refs.get(op.get("ref", "")) or uuid7(), **values}
                changed = [column for column in values if column != "entity_id"]
            case "set":
                id = _uuid(op.get("id"), f"{where}.id")
                current = conn.execute(
                    sql.SQL(
                        "select to_jsonb(t) - 'changeset_id' - 'recorded_from' - 'valid' from public.{} t where id = %s"
                    ).format(sql.Identifier(table)),
                    [id],
                ).fetchone()
                if current is None:
                    raise ValueError(f"{where}: {table} {id} does not exist")
                before = cast(dict[str, object], current[0])
                if not values:
                    raise ValueError(f"{where}: set needs values")
                changed = [column for column, value in values.items() if str(before.get(column)) != str(value)]
                if not changed:
                    raise ValueError(f"{where}: set changes nothing")
                row = before | values
            case other:
                raise ValueError(f"{where}: op {other!r} is not supported yet")

        entity = _uuid(str(row["entity_id"]), f"{where}.entity_id")
        entity = cs.survivors.get(entity, entity)
        if entity in cs.retired:
            raise ValueError(f"{where}: entity {entity} is retired")
        if conn.execute("select from entity where id = %s", [entity]).fetchone() is None:
            raise ValueError(f"{where}: entity {entity} does not exist")
        row["entity_id"] = entity
        if table in PROVENANCE and cited and (op.get("op") == "add" or "text" in changed):
            row[PROVENANCE[table]] = sources[cited[0]]
        cs.add(table, **row)
        for key in cited:
            item = evidence[key]
            for column in changed:
                cs.add(
                    "citation",
                    id=uuid7(),
                    entity_id=entity,
                    source_id=sources[key],
                    locator={
                        "table": table,
                        "row": str(row["id"]),
                        "column": column,
                        **{field: item[field] for field in ("page", "region", "quote", "locator") if field in item},
                    },
                )
    return cs.write()


def compile_file(conn: Connection, path: Path, author: UUID, author_kind: AuthorKind) -> Path:
    return compile(conn, cast(Payload, json.loads(path.read_text())), author, author_kind=author_kind)


__all__: list[LiteralString] = ["compile", "compile_file"]
