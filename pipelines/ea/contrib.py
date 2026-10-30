import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Literal, LiteralString, TypedDict, cast
from uuid import UUID, uuid7

import regex
from psycopg import sql

from ea.changeset import Changeset, bcp47
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
        "lang": True,
        "valid_from": False,
        "valid_from_precision": False,
        "valid_to": False,
        "valid_to_precision": False,
    },
}

CITED = {"name": True, "note": False}

PROVENANCE = {"name": "source_id"}

NOTE_LIMIT = 10_000

ORIGINS = ("human", "machine", "official")


class Evidence(TypedDict, total=False):
    source: str
    locator: object
    page: int
    region: list[float]
    quote: str


class Of(TypedDict):
    table: str
    id: str


class Op(TypedDict, total=False):
    op: str
    table: str
    id: str
    ref: str
    values: dict[str, object]
    evidence: list[str]
    of: Of
    lang: str
    body: str
    origin: str


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


def _graphemes(text: str) -> int:
    return len(regex.findall(r"\X", text))


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _lang(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{where}: expected a BCP 47 language tag, got {value!r}")
    try:
        return bcp47(value)
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None


def _translate(conn: Connection, cs: Changeset, op: Op, where: str, source: UUID | None) -> None:
    of = op.get("of") or {}
    if of.get("table") != "note":
        raise ValueError(f"{where}: only notes can be translated yet")
    note = _uuid(of.get("id"), f"{where}.of.id")
    current = conn.execute("select body_md, lang from note where id = %s", [note]).fetchone()
    if current is None:
        raise ValueError(f"{where}: note {note} does not exist")
    original, source_lang = cast(tuple[str, str], current)
    lang = _lang(op.get("lang"), f"{where}.lang")
    if lang == source_lang:
        raise ValueError(f"{where}: note {note} is already in {lang}")
    origin = op.get("origin")
    if origin not in ORIGINS:
        raise ValueError(f"{where}: origin must be one of {', '.join(ORIGINS)}")
    if (origin == "official") != (source is not None):
        raise ValueError(f"{where}: an official translation needs evidence, and only an official one takes it")
    body = unicodedata.normalize("NFC", str(op.get("body") or "")).strip()
    if not body:
        raise ValueError(f"{where}: body is empty")
    if _graphemes(body) > max(NOTE_LIMIT, 2 * _graphemes(original)):
        raise ValueError(f"{where}: body is longer than the limit for this note")
    held = conn.execute(
        "select origin::text from translation where source_md5 = %s and lang = %s", [_md5(original), lang]
    ).fetchone()
    if origin == "machine" and held is not None and held[0] != "machine":
        raise ValueError(f"{where}: a machine translation cannot replace the {held[0]} one in {lang}")
    cs.add(
        "translation",
        source_md5=_md5(original),
        lang=lang,
        source_lang=source_lang,
        body=body,
        origin=origin,
        source_id=source,
        stale_from=None,
    )


def _carry(conn: Connection, cs: Changeset, before: str, after: str, lang: str) -> None:
    for target, body, origin, source, stale in conn.execute(
        """select t.lang, t.body, t.origin::text, t.source_id, coalesce(t.stale_from, t.source_md5)
           from translation t
           where t.source_md5 = %s and t.lang <> %s
             and not exists (select from translation n where n.source_md5 = %s and n.lang = t.lang)""",
        [_md5(before), lang, _md5(after)],
    ).fetchall():
        cs.add(
            "translation",
            source_md5=_md5(after),
            lang=target,
            source_lang=lang,
            body=body,
            origin=origin,
            source_id=source,
            stale_from=None if stale == _md5(after) else stale,
        )


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
        cited = op.get("evidence") or []
        missing = [key for key in cited if key not in sources]
        if missing:
            raise ValueError(f"{where}: unknown evidence {', '.join(missing)}")
        if op.get("op") == "translate":
            note = str((op.get("of") or {}).get("id"))
            if any(
                other.get("op") == "set" and other.get("table") == "note" and other.get("id") == note for other in ops
            ):
                raise ValueError(f"{where}: translate note {note} in a separate proposal from its edit")
            _translate(conn, cs, op, where, sources[cited[0]] if cited else None)
            continue
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
            elif column == "lang":
                values[column] = _lang(value, f"{where}.values.lang")
            elif isinstance(value, str):
                values[column] = unicodedata.normalize("NFC", value).strip()
        if table == "note" and _graphemes(str(values.get("body_md") or "")) > NOTE_LIMIT:
            raise ValueError(f"{where}: a note is at most {NOTE_LIMIT:,} characters")
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
                if table == "note" and "body_md" in changed:
                    _carry(conn, cs, str(before["body_md"]), str(row["body_md"]), str(row["lang"]))
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
