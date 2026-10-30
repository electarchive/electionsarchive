import hashlib
import json
import shlex
import subprocess
import traceback
import unicodedata
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal, LiteralString, TypedDict, cast
from uuid import UUID, uuid7

import polars as pl
import regex
from psycopg import sql
from psycopg.types.json import Jsonb

from ea import raw
from ea.apply import _stage, apply, publish, revert
from ea.capture import capture, resume
from ea.changeset import Changeset, bcp47
from ea.store import DATA, ROOT, Connection, connect
from ea.uploads import process, sweep

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
    "external_id": {"entity_id": True, "scheme": True, "value": True},
}

CITED = {"name": True, "note": False, "external_id": False}

REFERENCES = {
    "wikidata": regex.compile(r"Q[1-9][0-9]*"),
    "bioguide": regex.compile(r"[A-Z][0-9]{6}"),
    "mnis": regex.compile(r"[1-9][0-9]*"),
    "parlgov": regex.compile(r"[1-9][0-9]*"),
}

DROPS = {"name": ("name_drop", "name_id", "text"), "note": ("note_drop", "note_id", "body_md")}

ROW_DROPS: dict[str, str | None] = {
    "citation": "entity_id",
    "label": "entity_id",
    "party_color": "party_id",
    "party_image": "party_id",
    "person_image": "person_id",
    "translation": None,
    "alliance_member": "alliance_id",
    "candidacy_nomination": "candidacy_id",
    "government_party": "government_id",
    "tenure_affiliation": "tenure_id",
    "area_event_member": "area_id",
    "party_relation": "from_party_id",
    "party_affiliation": "party_id",
}

PROVENANCE = {"name": "source_id"}

NOTE_LIMIT = 10_000

ORIGINS = ("human", "machine", "official")

HEAVY = 50_000

SHOWN = 2_000

TIERS = {
    **dict.fromkeys(
        (
            "note",
            "citation",
            "source",
            "source_artifact",
            "source_page",
            "external_id",
            "name",
            "label",
            "party_color",
            "translation",
            "name_drop",
            "note_drop",
            "external_id_drop",
        ),
        1,
    ),
    **dict.fromkeys(("result_value", "result_flow", "outcome", "seat", "body_size"), 3),
}

KEYED = {"translation": ("source_md5", "lang"), "external_id": ("scheme", "value")}

COLOR = regex.compile(r"#[0-9a-f]{6}")

ROW_DROP_TIERS = {"label": 1}


SOURCE_KINDS = ("official_body", "aggregator", "compendium", "news_agency", "academic", "community")


class Evidence(TypedDict, total=False):
    source: str
    url: str
    capture: str
    upload: str
    title: str
    publisher: str
    kind: str
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
    key: dict[str, object]
    reason: str
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


def _day(value: object, where: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{where}: expected a YYYY-MM-DD date, got {value!r}") from None


def _color(conn: Connection, cs: Changeset, op: Op, where: str, source: UUID) -> tuple[UUID, str, list[str]]:
    values = dict(op.get("values") or {})
    kind = op.get("op")
    allowed = {"add": {"party_id", "color", "valid_from", "valid_to"}, "set": {"color"}, "end": {"valid_to"}}
    if kind not in allowed:
        raise ValueError(f"{where}: a party color is added, set, ended, or dropped")
    if unknown := values.keys() - allowed[kind]:
        raise ValueError(f"{where}: {kind} party_color does not take {', '.join(sorted(unknown))}")
    precision = {"valid_from": "day", "valid_to": "day"}
    if kind == "add":
        party = _uuid(values.get("party_id"), f"{where}.values.party_id")
        color = values.get("color")
        start, end = (
            _day(values.get("valid_from"), f"{where}.valid_from"),
            _day(values.get("valid_to"), f"{where}.valid_to"),
        )
        changed = ["color"]
    else:
        key = op.get("key") or {}
        party = _uuid(key.get("party_id"), f"{where}.key.party_id")
        start = _day(key.get("valid_from"), f"{where}.key.valid_from")
        current = conn.execute(
            """select color, valid_to, valid_from_precision::text, valid_to_precision::text from party_color
               where party_id = %s and valid_from is not distinct from %s""",
            [party, start],
        ).fetchone()
        if current is None:
            raise ValueError(f"{where}: party {party} has no color from {start or 'the start'}")
        precision = {"valid_from": current[2], "valid_to": current[3]}
        color, end = (values.get("color"), current[1]) if kind == "set" else (current[0], None)
        if kind == "end":
            end = _day(values.get("valid_to"), f"{where}.values.valid_to")
            if end is None:
                raise ValueError(f"{where}: end needs valid_to")
            precision["valid_to"] = "day"
        if (color, end) == (current[0], current[1]):
            raise ValueError(f"{where}: {kind} changes nothing")
        changed = ["color"] if kind == "set" else ["valid_to"]
    if conn.execute("select from party where id = %s", [party]).fetchone() is None:
        raise ValueError(f"{where}: party {party} does not exist")
    color = str(color or "").strip().lower()
    if not COLOR.fullmatch(color):
        raise ValueError(f"{where}: color must look like #1a2b3c, got {color!r}")
    if start is not None and end is not None and start >= end:
        raise ValueError(f"{where}: valid_to must come after valid_from")
    for held_from, from_precision, held_to, to_precision, held_color, held_source in conn.execute(
        """select valid_from, valid_from_precision::text, valid_to, valid_to_precision::text, color, source_id
           from party_color where party_id = %s and valid && daterange(%s, %s, '[)')""",
        [party, start, end],
    ).fetchall():
        if kind == "end" and held_from == start:
            continue
        remainders = [
            *(
                [(held_from, from_precision, start, "day")]
                if start is not None and (held_from is None or held_from < start)
                else []
            ),
            *([(end, "day", held_to, to_precision)] if end is not None and (held_to is None or held_to > end) else []),
        ]
        for left, left_precision, right, right_precision in remainders:
            cs.add(
                "party_color",
                party_id=party,
                color=held_color,
                valid_from=left,
                valid_from_precision=left_precision,
                valid_to=right,
                valid_to_precision=right_precision,
                source_id=held_source,
            )
    cs.add(
        "party_color",
        party_id=party,
        color=color,
        valid_from=start,
        valid_from_precision=precision["valid_from"],
        valid_to=end,
        valid_to_precision=precision["valid_to"],
        source_id=source,
    )
    return party, f"{party}:{start or ''}", changed


def _cite(
    cs: Changeset,
    entity: UUID,
    source: UUID,
    item: Evidence,
    proof: dict[str, str],
    locator: dict[str, object],
) -> None:
    cs.add(
        "citation",
        id=uuid7(),
        entity_id=entity,
        source_id=source,
        locator={
            **locator,
            **{field: item[field] for field in ("page", "region", "quote", "locator") if field in item},
            **proof,
        },
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


def _source(cs: Changeset, item: Evidence, where: str, added: set[UUID], url: str | None) -> UUID:
    title = unicodedata.normalize("NFC", str(item.get("title") or "")).strip()
    publisher = unicodedata.normalize("NFC", str(item.get("publisher") or "")).strip()
    if not title or not publisher:
        raise ValueError(f"{where}: a URL or an upload needs a title and a publisher")
    kind = item.get("kind")
    if kind not in SOURCE_KINDS:
        raise ValueError(f"{where}: kind must be one of {', '.join(SOURCE_KINDS)}")
    source = cs.source(publisher, title)
    if source in cs.minted and source not in added:
        added.add(source)
        cs.add("source", id=source, kind=kind, publisher=publisher, title=title, url=url)
    return source


def _linked(
    conn: Connection, cs: Changeset, item: Evidence, author: UUID, where: str, added: set[UUID]
) -> tuple[UUID, dict[str, str]]:
    capture = _uuid(item.get("capture"), f"{where}.capture")
    row = conn.execute(
        """select url, final_url, raw_sha256, raw_key, media_type, bytes, captured_at, wayback_url from archive_capture
           where id = %s and status = 'captured' and owner_user_id = %s""",
        [capture, author],
    ).fetchone()
    if row is None:
        raise ValueError(f"{where}: capture {capture} is not an archived copy of this author's")
    url, final_url, sha256, key, media_type, size, captured_at, wayback = row
    if url != item.get("url"):
        raise ValueError(f"{where}: capture {capture} archived {url}, not {item.get('url')}")
    source = _source(cs, item, where, added, url)
    cs.artifact(
        raw.Artifact(
            source="contrib",
            url=final_url,
            sha256=sha256,
            path=DATA / key,
            retrieved_at=captured_at,
            media_type=media_type,
            bytes=size,
        ),
        source,
    )
    return source, {"sha256": sha256} | ({} if wayback is None else {"wayback": wayback})


def _uploaded(
    conn: Connection, cs: Changeset, item: Evidence, author: UUID, where: str, added: set[UUID]
) -> tuple[UUID, dict[str, str]]:
    upload = _uuid(item.get("upload"), f"{where}.upload")
    row = conn.execute(
        """select sha256, storage_key, media_type, bytes, created_at, pages from upload
           where id = %s and status = 'ready' and owner_user_id = %s""",
        [upload, author],
    ).fetchone()
    if row is None:
        raise ValueError(f"{where}: upload {upload} is not a ready upload of this author's")
    sha256, key, media_type, size, created_at, pages = row
    source = _source(cs, item, where, added, None)
    artifact = cs.artifact(
        raw.Artifact(
            source="contrib",
            url=f"upload:{sha256}",
            sha256=sha256,
            path=DATA / key,
            retrieved_at=created_at,
            media_type=media_type,
            bytes=size,
        ),
        source,
    )
    proof = {"sha256": sha256}
    number = item.get("page")
    if number is None:
        if "region" in item:
            raise ValueError(f"{where}: a region needs a page")
        return source, proof
    if pages is None or not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= pages:
        raise ValueError(f"{where}: page must be between 1 and {pages or 0}")
    region = item.get("region")
    if region is not None and not (
        isinstance(region, list)
        and len(region) == 4
        and all(isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1 for value in region)
        and region[0] < region[2]
        and region[1] < region[3]
    ):
        raise ValueError(f"{where}: region must be [x0, y0, x1, y1] fractions of the page, with x0 < x1 and y0 < y1")
    image = conn.execute(
        "select image_key, width, height from upload_page where upload_id = %s and page_number = %s", [upload, number]
    ).fetchone()
    if image is None:
        raise ValueError(f"{where}: upload {upload} has no page {number}")
    page = cs.page(artifact, number)
    if page not in added:
        added.add(page)
        cs.add(
            "source_page",
            id=page,
            artifact_id=artifact,
            page_number=number,
            image_key=image[0],
            width=image[1],
            height=image[2],
        )
    return source, proof | {"page_id": str(page)}


def _columns(conn: Connection, table: str) -> dict[str, str]:
    return dict(
        conn.execute(
            """select attname, format_type(atttypid, atttypmod) from pg_attribute
               where attrelid = %s::regclass and attnum > 0 and not attisdropped and attgenerated = ''
                 and attname not in ('changeset_id', 'recorded_from') order by attnum""",
            [f"public.{table}"],
        ).fetchall()
    )


def _snapshot(conn: Connection, table: str, match: dict[str, object], where: str) -> dict[str, object]:
    kinds = _columns(conn, table)
    if not match or match.keys() - kinds.keys():
        raise ValueError(f"{where}: a {table} key takes {', '.join(kinds)}")
    rows = conn.execute(
        sql.SQL("select jsonb_build_object({}) from public.{} p where {} limit 2").format(
            sql.SQL(", ").join(
                sql.SQL("{}, p.{}").format(sql.Literal(column), sql.Identifier(column)) for column in kinds
            ),
            sql.Identifier(table),
            sql.SQL(" and ").join(
                [
                    *(
                        sql.SQL("p.{} is not distinct from ({})").format(
                            sql.Identifier(column),
                            sql.SQL("%(key)s -> {}").format(sql.Literal(column))
                            if kinds[column] == "jsonb"
                            else sql.SQL("(%(key)s ->> {})::{}").format(
                                sql.Literal(column), sql.SQL(cast(LiteralString, kinds[column]))
                            ),
                        )
                        for column in match
                    ),
                    *(
                        sql.SQL("p.{} = (%(key)s ->> {})::uuid").format(sql.Identifier(column), sql.Literal(column))
                        for column in match
                        if kinds[column] == "uuid" and match[column] is not None
                    ),
                ]
            ),
        ),
        {"key": Jsonb(match)},
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{where}: {'more than one' if rows else 'no'} {table} row matches that key")
    return cast(dict[str, object], rows[0][0])


def _drop(conn: Connection, cs: Changeset, op: Op, where: str, reason: str) -> tuple[UUID | None, str]:
    table = op.get("table", "")
    if table in DROPS:
        drop, column, text = DROPS[table]
        id = _uuid(op.get("id"), f"{where}.id")
        current = conn.execute(
            sql.SQL("select entity_id, {} from public.{} where id = %s").format(
                sql.Identifier(text), sql.Identifier(table)
            ),
            [id],
        ).fetchone()
        if current is None:
            raise ValueError(f"{where}: {table} {id} does not exist")
        cs.add(drop, **{column: id, "entity_id": current[0], text: current[1], "reason": reason})
        return current[0], str(id)
    if table == "external_id":
        key = op.get("key") or {}
        scheme, value = key.get("scheme"), key.get("value")
        if scheme not in REFERENCES:
            raise ValueError(f"{where}: only {', '.join(REFERENCES)} IDs can be dropped")
        current = conn.execute(
            "select entity_id from external_id where scheme = %s and value = %s", [scheme, value]
        ).fetchone()
        if current is None:
            raise ValueError(f"{where}: no entity has {scheme} ID {value}")
        cs.add("external_id_drop", entity_id=current[0], scheme=scheme, value=value, reason=reason)
        return current[0], f"{scheme}:{value}"
    if table in ROW_DROPS:
        match = {"id": str(_uuid(op.get("id"), f"{where}.id"))} if "id" in op else dict(op.get("key") or {})
        row = _snapshot(conn, table, match, where)
        cs.add("row_drop", id=uuid7(), relation=table, key=row, reason=reason)
        owner = ROW_DROPS[table]
        label = str(row["id"]) if "id" in row else _dumps({column: row[column] for column in match})
        return None if owner is None else _uuid(row[owner], f"{where}.{owner}"), label
    raise ValueError(f"{where}: {table!r} rows cannot be dropped")


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
    cs = Changeset(conn, pipeline="contrib", version=VERSION, summary=summary, polity=payload.get("polity"), kind=kind)
    added: set[UUID] = set()
    proofs: dict[str, dict[str, str]] = {}
    for key, item in evidence.items():
        if "source" in item:
            source = _uuid(item["source"], f"evidence {key}.source")
            if conn.execute("select from source where id = %s", [source]).fetchone() is None:
                raise ValueError(f"evidence {key}: source {source} does not exist")
            sources[key] = source
        elif "url" in item:
            sources[key], proofs[key] = _linked(conn, cs, item, author, f"evidence {key}", added)
        elif "upload" in item:
            sources[key], proofs[key] = _uploaded(conn, cs, item, author, f"evidence {key}", added)
        else:
            raise ValueError(f"evidence {key}: needs a source, a url, or an upload")

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

    targets: set[tuple[object, str]] = set()
    colored: set[UUID] = set()
    dropped = {
        (str((op.get("key") or {}).get("scheme")), str((op.get("key") or {}).get("value")))
        for op in ops
        if op.get("op") == "drop" and op.get("table") == "external_id"
    }
    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        cited = op.get("evidence") or []
        missing = [key for key in cited if key not in sources]
        if missing:
            raise ValueError(f"{where}: unknown evidence {', '.join(missing)}")
        target = (op.get("table"), str(op.get("id") or _dumps(op.get("key"))))
        if op.get("op") in ("set", "drop"):
            if target in targets:
                raise ValueError(f"{where}: {target[0]} {target[1]} is changed twice")
            targets.add(target)
        if op.get("op") == "drop":
            reason = unicodedata.normalize("NFC", str(op.get("reason") or summary)).strip()
            owner, label = _drop(conn, cs, op, where, reason)
            for key in cited:
                if owner is not None:
                    _cite(
                        cs,
                        owner,
                        sources[key],
                        evidence[key],
                        proofs.get(key, {}),
                        {"table": op.get("table"), "row": label, "op": "drop"},
                    )
            continue
        if op.get("table") == "party_color":
            if not cited:
                raise ValueError(f"{where}: a change to party_color needs evidence")
            party, label, changed = _color(conn, cs, op, where, sources[cited[0]])
            if party in colored:
                raise ValueError(f"{where}: change one color period per party in a proposal")
            colored.add(party)
            for key in cited:
                for column in changed:
                    _cite(
                        cs,
                        party,
                        sources[key],
                        evidence[key],
                        proofs.get(key, {}),
                        {"table": "party_color", "row": label, "column": column, "op": op.get("op")},
                    )
            continue
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
        if table == "external_id":
            if op.get("op") != "add":
                raise ValueError(f"{where}: an external ID is added or dropped, never changed")
            scheme, value = values.get("scheme"), str(values.get("value") or "")
            pattern = REFERENCES.get(str(scheme))
            if pattern is None:
                raise ValueError(f"{where}: only {', '.join(REFERENCES)} IDs can be added")
            if not pattern.fullmatch(value):
                raise ValueError(f"{where}: {value!r} is not a {scheme} ID")
            held = conn.execute(
                "select entity_id from external_id where scheme = %s and value = %s", [scheme, value]
            ).fetchone()
            if held is not None:
                raise ValueError(f"{where}: {scheme} ID {value} is already on {held[0]}")
            other = conn.execute(
                "select value from external_id where scheme = %s and entity_id = %s",
                [scheme, _uuid(str(values.get("entity_id")), f"{where}.entity_id")],
            ).fetchone()
            if other is not None and (str(scheme), other[0]) not in dropped:
                raise ValueError(
                    f"{where}: this entity already has {scheme} ID {other[0]}; drop it in the same proposal"
                )

        match op.get("op"):
            case "add":
                absent = [column for column, required in columns.items() if required and values.get(column) is None]
                if absent:
                    raise ValueError(f"{where}: add {table} needs {', '.join(absent)}")
                row: dict[str, object] = (
                    values if table in KEYED else {"id": refs.get(op.get("ref", "")) or uuid7(), **values}
                )
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
            for column in changed:
                _cite(
                    cs,
                    entity,
                    sources[key],
                    evidence[key],
                    proofs.get(key, {}),
                    {
                        "table": table,
                        "row": str(row["id"]) if "id" in row else ":".join(str(row[c]) for c in KEYED[table]),
                        "column": column,
                    },
                )
    return cs.write()


def compile_file(conn: Connection, path: Path, author: UUID, author_kind: AuthorKind) -> Path:
    return compile(conn, cast(Payload, json.loads(path.read_text())), author, author_kind=author_kind)


def _dumps(value: object) -> str:
    return json.dumps(value, default=str)


def _rows(folder: Path) -> int:
    return sum(pl.scan_parquet(path).select(pl.len()).collect().item() for path in folder.glob("*.parquet"))


def _gated(*args: str) -> None:
    subprocess.run(
        [
            "bash",
            str(DATA / "logs" / "gate.sh"),
            "systemd-run",
            "--user",
            "--wait",
            "--collect",
            "-p",
            "MemoryHigh=5G",
            "-p",
            "MemoryMax=6G",
            "-p",
            f"WorkingDirectory={ROOT / 'pipelines'}",
            "bash",
            "-lc",
            shlex.join(["uv", "run", "ea", *args]),
        ],
        check=True,
    )


def _dropped(conn: Connection, table: str, staged: sql.Composable) -> list[tuple[str, dict[str, object], str | None]]:
    if table == "row_drop":
        return [
            (relation, key, None if ROW_DROPS[relation] is None else str(key[ROW_DROPS[relation]]))
            for relation, key in conn.execute(
                sql.SQL("select relation, key from {} order by id").format(staged)
            ).fetchall()
        ]
    relation = table.removesuffix("_drop")
    return [
        (
            relation,
            {
                column: value
                for column, value in row.items()
                if column not in ("reason", "changeset_id", "recorded_from")
            },
            str(row["entity_id"]),
        )
        for (row,) in conn.execute(sql.SQL("select to_jsonb(s) from {} s").format(staged)).fetchall()
    ]


def _periods(conn: Connection, staged: sql.Composable) -> list[dict[str, object]]:
    compared = ("color", "valid_from", "valid_from_precision", "valid_to", "valid_to_precision", "source_id")
    after = [
        cast(dict[str, object], row)
        for (row,) in conn.execute(sql.SQL("select to_jsonb(s) from {} s").format(staged)).fetchall()
    ]
    before = [
        cast(dict[str, object], row)
        for (row,) in conn.execute(
            sql.SQL(
                """select distinct to_jsonb(p) from public.party_color p
                   join {} s on p.party_id = s.party_id and p.valid && daterange(s.valid_from, s.valid_to, '[)')"""
            ).format(staged)
        ).fetchall()
    ]

    def same(row: dict[str, object]) -> tuple[object, ...]:
        return (row["party_id"], *(row.get(column) for column in compared))

    kept = {same(row) for row in before} & {same(row) for row in after}
    after = [row for row in after if same(row) not in kept]
    replaced = {(row["party_id"], row["valid_from"]): row for row in before if same(row) not in kept}
    rows: list[dict[str, object]] = []
    for row in after:
        old = replaced.pop((row["party_id"], row["valid_from"]), None)
        rows.append(
            {
                "table": "party_color",
                "key": {
                    "party_id": row["party_id"],
                    "valid_from": row["valid_from"],
                    "valid_from_precision": row["valid_from_precision"],
                },
                "entity_id": row["party_id"],
                "op": "add" if old is None else "change",
                "fields": {
                    column: [None if old is None else old.get(column), row.get(column)]
                    for column in compared
                    if old is None or old.get(column) != row.get(column)
                },
            }
        )
    rows.extend(
        {
            "table": "party_color",
            "key": {
                "party_id": row["party_id"],
                "valid_from": row["valid_from"],
                "valid_from_precision": row["valid_from_precision"],
            },
            "entity_id": row["party_id"],
            "op": "drop",
            "fields": {column: [row.get(column), None] for column in compared if row.get(column) is not None},
        }
        for row in replaced.values()
    )
    return rows


def _review(conn: Connection, changeset_id: str) -> tuple[int, dict[str, object], object]:
    stage = _stage(conn, changeset_id)
    shown: list[dict[str, object]] = []
    entities: set[str] = set()
    truncated = False
    tiers = [TIERS.get(table, 2) for table in stage.tables if table != "row_drop"]
    for table, columns in stage.tables.items():
        staged = stage.staged(table)
        if table in ("row_drop", "name_drop", "note_drop", "external_id_drop"):
            for relation, key, owner in _dropped(conn, table, staged):
                if table == "row_drop":
                    tiers.append(ROW_DROP_TIERS.get(relation, 2))
                if owner is not None:
                    entities.add(owner)
                if len(shown) == SHOWN:
                    truncated = True
                    continue
                shown.append(
                    {
                        "table": relation,
                        "key": key,
                        "entity_id": owner,
                        "op": "drop",
                        "fields": {
                            column: [value, None]
                            for column, value in key.items()
                            if value is not None and column not in ("entity_id", "name_id", "note_id")
                        },
                    }
                )
            continue
        if table == "party_color":
            for row in _periods(conn, staged):
                entities.add(str(row["entity_id"]))
                if len(shown) == SHOWN:
                    truncated = True
                    break
                shown.append(row)
            continue
        if "entity_id" in columns:
            entities.update(
                str(entity)
                for (entity,) in conn.execute(sql.SQL("select distinct entity_id from {}").format(staged)).fetchall()
            )
        key = ("id",) if "id" in columns else KEYED.get(table, ())
        compared = [column for column in columns if column not in key]
        rows = conn.execute(
            sql.SQL("select to_jsonb(s), {} from {} s {} limit %s").format(
                sql.SQL("to_jsonb(p)") if key else sql.SQL("null::jsonb"),
                staged,
                sql.SQL("left join public.{} p using ({})").format(
                    sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, key))
                )
                if key
                else sql.SQL(""),
            ),
            [SHOWN - len(shown) + 1],
        ).fetchall()
        for after, before in rows:
            fields = {
                column: [None if before is None else before.get(column), after.get(column)]
                for column in compared
                if before is None or before.get(column) != after.get(column)
            }
            if not fields:
                continue
            if len(shown) == SHOWN:
                truncated = True
                break
            shown.append(
                {
                    "table": table,
                    "key": {column: after[column] for column in key},
                    "entity_id": after.get("entity_id"),
                    "op": "add" if before is None else "change",
                    "fields": fields,
                }
            )
    checks = conn.execute("select checks from changeset where id = %s", [changeset_id]).fetchone()
    return max(tiers), {"rows": shown, "truncated": truncated, "entities": sorted(entities)}, checks and checks[0]


def _apply(folder: Path) -> str:
    if _rows(folder) <= HEAVY:
        return apply(folder)
    _gated("apply", str(folder))
    with connect() as conn:
        status = conn.execute("select status::text from changeset where id = %s", [folder.name]).fetchone()
    return "unchanged" if status is None else str(status[0])


def _supersede(conn: Connection, changeset_id: str) -> None:
    conn.execute(
        sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(f"cs_{changeset_id.replace('-', '')}"))
    )
    conn.execute(
        "update changeset set status = 'superseded' where id = %s and status in ('draft', 'pending_review')",
        [changeset_id],
    )
    folder = DATA / "changesets" / changeset_id
    if folder.exists():
        folder.rename(DATA / "retired" / "changesets" / changeset_id)


def _activity(
    conn: Connection, proposal: UUID, kind: str, actor: UUID | None, changeset_id: object, summary: str
) -> None:
    conn.execute(
        """insert into activity (actor_user_id, agent_key_id, kind, proposal_id, changeset_id, entity_ids, polity_ids, summary)
           select coalesce(%s, p.author_user_id), case when %s::uuid is null then p.agent_key_id end, %s, p.id, %s,
                  coalesce((select array_agg(e::uuid) from proposal_revision r,
                                   jsonb_array_elements_text(r.diff -> 'entities') e
                            where r.proposal_id = p.id and r.changeset_id = %s), '{}'),
                  array_remove(array[p.polity_id], null), format(%s, p.number)
           from proposal p where p.id = %s""",
        [actor, actor, kind, changeset_id, changeset_id, summary, proposal],
    )


def _compiled(conn: Connection, proposal: UUID, payload: object) -> Path:
    row = conn.execute(
        "select author_user_id, agent_key_id is not null from proposal where id = %s", [proposal]
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"proposal {proposal} has no author")
    folder = compile(conn, cast(Payload, payload), row[0], author_kind="agent" if row[1] else "user", proposal=proposal)
    conn.commit()
    return folder


def _payload(conn: Connection, proposal: UUID, n: int | None) -> object:
    row = conn.execute(
        "select payload from proposal_revision where proposal_id = %s and n = %s"
        if n is not None
        else "select draft from proposal where id = %s",
        [proposal, n] if n is not None else [proposal],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"proposal {proposal} has no {'revision ' + str(n) if n is not None else 'draft'}")
    return row[0]


def _captured(conn: Connection, proposal: UUID, n: int | None) -> object:
    payload = cast(Payload, _payload(conn, proposal, n))
    pending = {
        key: item for key, item in (payload.get("evidence") or {}).items() if "url" in item and "capture" not in item
    }
    if not pending:
        return payload
    found = conn.execute("select author_user_id from proposal where id = %s", [proposal]).fetchone()
    author = None if found is None else found[0]
    for key, item in pending.items():
        id, status, error = capture(conn, item["url"], author)
        conn.commit()
        if status != "captured":
            raise ValueError(f"evidence {key}: {item['url']} could not be archived: {error}")
        item["capture"] = str(id)
    conn.execute(
        "update proposal_revision set payload = %s where proposal_id = %s and n = %s"
        if n is not None
        else "update proposal set draft = %s where id = %s",
        [Jsonb(payload), proposal, n] if n is not None else [Jsonb(payload), proposal],
    )
    conn.commit()
    return payload


def _check(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
    with connect() as conn:
        folder = _compiled(conn, proposal, _captured(conn, proposal, n))
        if _apply(folder) == "unchanged":
            return {"status": "unchanged"}
        tier, diff, checks = _review(conn, folder.name)
        conn.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(f"cs_{folder.name.replace('-', '')}"))
        )
        conn.execute("delete from changeset where id = %s and status = 'pending_review'", [folder.name])
    folder.rename(DATA / "retired" / "changesets" / folder.name)
    return {"status": "checked", "tier": tier, "diff": diff, "checks": checks}


def _submit(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
    if n is None:
        raise ValueError("a submit job needs revision_n")
    with connect() as conn:
        folder = _compiled(conn, proposal, _captured(conn, proposal, n))
        if _apply(folder) == "unchanged":
            conn.execute(
                "update proposal_revision set checks = %s where proposal_id = %s and n = %s",
                [Jsonb({"unchanged": True}), proposal, n],
            )
            return {"status": "unchanged"}
        changeset_id = folder.name
        tier, diff, checks = _review(conn, changeset_id)
        conn.execute(
            "update proposal_revision set changeset_id = %s, diff = %s, checks = %s where proposal_id = %s and n = %s",
            [changeset_id, Jsonb(diff, dumps=_dumps), Jsonb(checks, dumps=_dumps), proposal, n],
        )
        head = conn.execute("select head_changeset_id from proposal where id = %s", [proposal]).fetchone()
        previous = None if head is None else head[0]
        if previous is not None:
            _supersede(conn, str(previous))
        conn.execute(
            """update proposal set head_changeset_id = %s, tier = %s, state = 'open',
                      submitted_at = coalesce(submitted_at, now()), updated_at = now()
               where id = %s""",
            [changeset_id, tier, proposal],
        )
        _activity(
            conn,
            proposal,
            "proposal_submitted" if previous is None else "proposal_revised",
            None,
            changeset_id,
            "Proposal %s submitted" if previous is None else "Proposal %s revised",
        )
    return {"status": "pending_review", "changeset_id": changeset_id, "tier": tier}


def _decided(conn: Connection, proposal: UUID, requested: UUID | None, states: tuple[str, ...]) -> tuple[str, str]:
    if requested is None:
        raise ValueError("a decision needs requested_by")
    row = conn.execute(
        """select p.head_changeset_id::text, p.state::text, p.tier,
                  exists (select from user_role r where r.user_id = %s and r.role = 'admin'),
                  exists (select from user_role r where r.user_id = %s and r.role = 'reviewer'
                          and (r.polity_scope_id is null or r.polity_scope_id = p.polity_id))
           from proposal p where p.id = %s""",
        [requested, requested, proposal],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"proposal {proposal} has no submitted revision")
    head, state, tier, admin, reviewer = row
    if state not in states:
        raise ValueError(f"proposal {proposal} is {state}, not {' or '.join(states)}")
    if not (admin or (reviewer and tier == 1)):
        raise ValueError(f"user {requested} may not decide a tier {tier} proposal")
    return head, state


def _close(conn: Connection, proposal: UUID, state: str, requested: UUID, reason: object) -> None:
    conn.execute(
        """update proposal set state = %s, decided_by = %s, decided_at = now(),
                  decision_reason = coalesce(%s, decision_reason), updated_at = now()
           where id = %s""",
        [state, requested, reason, proposal],
    )


def _publish(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
    with connect() as conn:
        head, _ = _decided(conn, proposal, requested, ("open", "approved"))
        if _rows(DATA / "changesets" / head) > HEAVY:
            conn.commit()
            _gated("publish", head, "--reviewer", str(requested))
        else:
            publish(conn, head, str(requested))
        _close(conn, proposal, "published", cast(UUID, requested), output.get("reason"))
        _activity(conn, proposal, "proposal_published", requested, head, "Proposal %s published")
    return {"status": "published", "changeset_id": head}


def _reverse(state: str, accepted: tuple[str, ...], activity: str, summary: str) -> Callable[..., dict[str, object]]:
    def run(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
        with connect() as conn:
            head, _ = _decided(conn, proposal, requested, accepted)
            stored = conn.execute("select decision_reason from proposal where id = %s", [proposal]).fetchone()
            reason = output.get("reason") or (stored and stored[0])
            if not reason:
                raise ValueError(f"a {state} decision needs a reason")
            if _rows(DATA / "changesets" / head) > HEAVY:
                conn.commit()
                _gated("revert", head, str(reason), "--reviewer", str(requested))
            else:
                revert(conn, head, str(reason), str(requested))
            _close(conn, proposal, state, cast(UUID, requested), reason)
            _activity(conn, proposal, activity, requested, head, summary)
        return {"status": state, "changeset_id": head}

    return run


JOBS: dict[str, Callable[[UUID, int | None, UUID | None, dict[str, object]], dict[str, object]]] = {
    "check": _check,
    "submit": _submit,
    "publish": _publish,
    "reject": _reverse(
        "rejected", ("open", "changes_requested", "approved"), "proposal_rejected", "Proposal %s rejected"
    ),
    "revert": _reverse("reverted", ("published",), "proposal_reverted", "Proposal %s reverted"),
}


def _notify(conn: Connection, job: object, proposal: object, kind: str, status: str) -> None:
    conn.execute(
        "select pg_notify('proposal_job', %s)",
        [_dumps({"id": job, "proposal_id": proposal, "kind": kind, "status": status})],
    )


def work() -> bool:
    with connect() as conn:
        claimed = conn.execute(
            """update proposal_job set status = 'running', started_at = now()
               where id = (select id from proposal_job where status = 'queued'
                           order by created_at for update skip locked limit 1)
               returning id, proposal_id, revision_n, kind::text, requested_by, coalesce(output, '{}'::jsonb)"""
        ).fetchone()
        if claimed is None:
            return False
        job, proposal, n, kind, requested, output = claimed
        _notify(conn, job, proposal, kind, "running")
    try:
        result = {**output, **JOBS[kind](proposal, n, requested, output)}
        status = "done"
    except Exception as error:  # noqa: BLE001 - a failed job is recorded and the worker carries on
        traceback.print_exc()
        result = {**output, "error": f"{type(error).__name__}: {error}"}
        status = "failed"
    with connect() as conn:
        conn.execute(
            "update proposal_job set status = %s, finished_at = now(), output = %s where id = %s",
            [status, Jsonb(result, dumps=_dumps), job],
        )
        _notify(conn, job, proposal, kind, status)
    print(f"{kind} {proposal} {status}", flush=True)
    return True


def serve() -> None:
    with connect(autocommit=True) as listener:
        listener.execute(
            """update proposal_job set status = 'failed', finished_at = now(),
                      output = coalesce(output, '{}'::jsonb) || '{"error": "interrupted by a worker restart"}'
               where status = 'running'"""
        )
        listener.execute("listen proposal_job")
        resume(listener)
        while True:
            sweep()
            while work() or process():
                pass
            for _ in listener.notifies(timeout=60, stop_after=1):
                pass


__all__: list[LiteralString] = ["compile", "compile_file", "serve", "work"]
