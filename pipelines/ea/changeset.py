import json
import re
import unicodedata
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import LiteralString
from uuid import UUID, uuid7

import polars as pl

from ea.checks import FACTS, KEYS
from ea.raw import Artifact
from ea.store import DATA, Connection

LATE = ("name", "slug", "external_id", "note", "citation", "entity_merge")


def slug(text: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", re.sub(r"[‐-―]", "-", text)).encode("ascii", "ignore").decode().lower(),
    ).strip("-")


class Changeset:
    def __init__(
        self, conn: Connection, *, pipeline: str, version: str, summary: str, polity: str, kind: str = "import"
    ) -> None:
        self.id = uuid7()
        self.conn = conn
        self.manifest: dict[str, object] = {
            "id": str(self.id),
            "author_kind": "pipeline",
            "pipeline": pipeline,
            "pipeline_version": version,
            "kind": kind,
            "summary": summary,
            "scope": {"polity": polity},
        }
        self.rows: dict[str, list[dict[str, object]]] = {}
        self.frames: dict[str, list[pl.DataFrame]] = {}
        self.minted: set[UUID] = set()
        self._known: dict[str, dict[str, UUID]] = {}
        self._keyed: set[tuple[str, str]] = set()
        self.renamed: dict[str, str] = {}
        self.merged = [
            (str(into), str(entity)) for entity, into in conn.execute("select entity_id, into_id from entity_merge")
        ]

    def add(self, table: str, **row: object) -> None:
        self.rows.setdefault(table, []).append(row)
        self.frames.setdefault(table, [])

    def frame(self, table: str, frame: pl.DataFrame) -> None:
        self.rows.setdefault(table, [])
        self.frames.setdefault(table, []).append(frame)

    def _resolve(self, cache: str, key: str, query: LiteralString, params: Sequence[object] = ()) -> UUID:
        if cache not in self._known:
            self._known[cache] = {str(k): v for k, v in self.conn.execute(query, params).fetchall()}
        known = self._known[cache]
        if key not in known:
            known[key] = uuid7()
            self.minted.add(known[key])
        return known[key]

    def key(self, scheme: str, value: str) -> UUID:
        cache = f"external_id:{scheme}"
        id = self._resolve(cache, value, "select value, entity_id from external_id where scheme = %s", [scheme])
        if id in self.minted and (scheme, value) not in self._keyed:
            for new, old in [*self.renamed.items(), *self.merged]:
                if new in value and (previous := self._known[cache].get(value.replace(new, old))) is not None:
                    self.minted.discard(id)
                    id = self._known[cache][value] = previous
                    break
            self._keyed.add((scheme, value))
            self.add("external_id", entity_id=id, scheme=scheme, value=value)
        return id

    def polity(self, iso: str) -> UUID:
        return self._resolve("polity", iso, "select iso_code, id from polity where iso_code is not null")

    def area(self, scheme: str, code: str, on: date | None = None) -> UUID:
        return self._resolve(
            f"area:{scheme}@{on}",
            code,
            "select c.code, a.id from area a join area_code c on c.area_id = a.id"
            " where a.scheme_code = %s and (%s::date is null or c.valid @> %s::date)",
            [scheme, on, on],
        )

    def unit(self, scheme: str, code: str, on: date | None = None) -> UUID:
        return self._resolve(
            f"unit:{scheme}@{on}",
            code,
            "select code, id from reporting_unit where scheme_code = %s and (%s::date is null or valid @> %s::date)",
            [scheme, on, on],
        )

    def source(self, publisher: str, title: str) -> UUID:
        return self._resolve(
            "source", f"{publisher}\x1f{title}", "select publisher || chr(31) || title, id from source"
        )

    def artifact(self, artifact: Artifact, source_id: UUID) -> UUID:
        id = self._resolve(
            "artifact",
            f"{source_id}\x1f{artifact.storage_key}",
            "select source_id || chr(31) || storage_key, id from source_artifact",
        )
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
        return id

    def page(self, artifact_id: UUID, number: int) -> UUID:
        return self._resolve(
            "page", f"{artifact_id}:{number}", "select artifact_id || ':' || page_number, id from source_page"
        )

    def write(self) -> Path:
        folder = DATA / "changesets" / str(self.id)
        folder.mkdir(parents=True)
        for table, rows in self.rows.items():
            frames = list(self.frames[table])
            if rows:
                frames.insert(
                    0,
                    pl.DataFrame(
                        [
                            {
                                column: str(value)
                                if isinstance(value, UUID)
                                else value.isoformat()
                                if isinstance(value, datetime)
                                else json.dumps(value)
                                if isinstance(value, dict | list)
                                else value
                                for column, value in row.items()
                            }
                            for row in rows
                        ],
                        infer_schema_length=None,
                    ),
                )
            pl.concat(frames, how="diagonal_relaxed").write_parquet(folder / f"{table}.parquet")
        edges = self.conn.execute(
            """select conrelid::regclass::text, confrelid::regclass::text from pg_constraint
               where contype = 'f' and connamespace = 'public'::regnamespace and conrelid <> confrelid"""
        ).fetchall() + [(fact, parent) for fact in FACTS for parent in KEYS.values()]
        remaining = [table for table in self.rows if table not in LATE]
        ordered: list[str] = []
        while remaining:
            roots = [
                table
                for table in remaining
                if not any(child == table and parent in remaining for child, parent in edges)
            ]
            assert roots, remaining
            ordered += roots
            remaining = [table for table in remaining if table not in roots]
        self.manifest["tables"] = ordered + [table for table in LATE if table in self.rows]
        (folder / "manifest.json").write_text(json.dumps(self.manifest, indent=2))
        return folder
