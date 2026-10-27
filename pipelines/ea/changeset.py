import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import LiteralString, cast
from uuid import NAMESPACE_URL, UUID, uuid5, uuid7

import polars as pl
from psycopg import sql

from ea.checks import FACTS, KEYS
from ea.raw import Artifact
from ea.store import DATA, Connection

NAMESPACE = uuid5(NAMESPACE_URL, "https://electionsarchive.com/")
CHUNK = 200_000
LATE = ("name", "slug", "external_id", "note", "citation", "entity_merge", "entity_retirement")
UUID_TEXT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
TRANSLITERATION = str.maketrans(
    {
        "ß": "ss",
        "ẞ": "SS",
        "ı": "i",
        "ł": "l",
        "Ł": "L",
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
    }
)


def slug(text: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", re.sub(r"[‐-―]", "-", text).translate(TRANSLITERATION))
        .encode("ascii", "ignore")
        .decode()
        .lower(),
    ).strip("-")


class Changeset:
    def __init__(
        self, conn: Connection, *, pipeline: str, version: str, summary: str, polity: str | None, kind: str = "import"
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
        self.chunks: dict[str, list[pl.DataFrame]] = {}
        self.minted: set[UUID] = set()
        self.artifacts: set[UUID] = set()
        self._known: dict[str, dict[str, UUID]] = {}
        self._keyed: set[tuple[str, str]] = set()
        self.renamed: dict[str, str] = {}
        self.survivors: dict[UUID, UUID] = {}
        self.merged: dict[str, list[str]] = {}
        for entity, into in conn.execute("select entity_id, into_id from entity_merge"):
            self.merged.setdefault(str(into), []).append(str(entity))
            self.survivors[entity] = into
        for entity in self.survivors:
            while self.survivors[entity] in self.survivors:
                self.survivors[entity] = self.survivors[self.survivors[entity]]
        self.retired: dict[UUID, UUID] = dict(
            conn.execute("select id, retired_by from entity where retired_by is not null").fetchall()
        )
        constant = conn.execute(
            r"""select c.relname, a.attname, pg_get_expr(d.adbin, d.adrelid) from pg_attrdef d
               join pg_class c on c.oid = d.adrelid join pg_attribute a on a.attrelid = d.adrelid and a.attnum = d.adnum
               where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p') and not c.relispartition
                 and pg_get_expr(d.adbin, d.adrelid) !~ '\w\('"""
        ).fetchall()
        values = conn.execute(
            sql.SQL("select {}").format(
                sql.SQL(", ").join(sql.SQL(cast(LiteralString, default)) for _, _, default in constant)
            )
        ).fetchone()
        assert values is not None
        self.defaults: dict[str, dict[str, object]] = {}
        for (table, column, _), value in zip(constant, values, strict=True):
            self.defaults.setdefault(table, {})[column] = json.dumps(value) if isinstance(value, dict | list) else value

    def add(self, table: str, **row: object) -> None:
        rows = self.rows.setdefault(table, [])
        rows.append(row)
        self.frames.setdefault(table, [])
        if table in FACTS and len(rows) >= CHUNK:
            self._chunk(table)

    def _chunk(self, table: str) -> None:
        defaults = self.defaults.get(table, {})
        keys = {key for row in self.rows[table] for key in row}
        fill = {column: defaults[column] for column in keys & defaults.keys()}
        self.chunks.setdefault(table, []).append(
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
                    for row in (
                        row
                        if not fill or len(row) == len(keys)
                        else row | {column: value for column, value in fill.items() if column not in row}
                        for row in self.rows[table]
                    )
                ],
                infer_schema_length=None,
            )
        )
        self.rows[table].clear()

    def frame(self, table: str, frame: pl.DataFrame) -> None:
        self.rows.setdefault(table, [])
        self.frames.setdefault(table, []).append(frame)

    def _resolve(self, cache: str, key: str, query: LiteralString, params: Sequence[object] = ()) -> UUID:
        if cache not in self._known:
            self._known[cache] = {str(k): v for k, v in self.conn.execute(query, params).fetchall()}
        known = self._known[cache]
        if key not in known:
            known[key] = uuid5(NAMESPACE, f"{cache}\x1f{key}")
            if known[key] in self.retired:
                raise ValueError(f"{cache} {key} is {known[key]}, retired by changeset {self.retired[known[key]]}")
            self.minted.add(known[key])
        return self.survivors.get(known[key], known[key])

    def key(self, scheme: str, value: str) -> UUID:
        cache = f"external_id:{scheme}"
        id = self._resolve(cache, value, "select value, entity_id from external_id where scheme = %s", [scheme])
        if id in self.minted and (scheme, value) not in self._keyed:
            merged = [(new, old) for new in UUID_TEXT.findall(value) for old in self.merged.get(new, ())]
            for new, old in [*self.renamed.items(), *merged]:
                if new in value and (previous := self._known[cache].get(value.replace(new, old))) is not None:
                    self.minted.discard(id)
                    id = self._known[cache][value] = previous
                    break
            self._keyed.add((scheme, value))
            self.add("external_id", entity_id=self.survivors.get(id, id), scheme=scheme, value=value)
        return self.survivors.get(id, id)

    def merge(self, entity: UUID, into: UUID) -> None:
        into = self.survivors.get(into, into)
        if entity == into or entity in self.survivors:
            return
        self.add("entity_merge", entity_id=entity, into_id=into)
        self.survivors[entity] = into
        absorbed = self.merged.pop(str(entity), [])
        if absorbed:
            for row in self.rows.get("entity_merge", []):
                if row["into_id"] == entity:
                    row["into_id"] = into
            for earlier in absorbed:
                self.survivors[UUID(earlier)] = into
        self.merged.setdefault(str(into), []).extend([*absorbed, str(entity)])

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

    def scope_units(self, scheme: str, like: str) -> None:
        self._known[f"unit:{scheme}@None"] = {
            str(code): id
            for code, id in self.conn.execute(
                "select code, id from reporting_unit where scheme_code = %s and code like %s", [scheme, like]
            ).fetchall()
        }

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
        if id in self.artifacts:
            return id
        self.artifacts.add(id)
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

    def zeroed(self) -> set[UUID]:
        return {
            id
            for (id,) in self.conn.execute(
                """select rs.id from result_set rs join changeset c on c.id = rs.changeset_id
                   where c.pipeline = %s and rs.status = 'active' and rs.kind = 'counted'
                     and exists (select from result_value v where v.result_set_k = rs.k)
                     and not exists (select from result_value v where v.result_set_k = rs.k and v.value <> 0)""",
                [self.manifest["pipeline"]],
            )
        }

    def remainders(
        self,
        reason: str,
        pattern: str = r"^(under ?votes?|over ?votes?|blank( votes| ballots)?|spoiled( votes| ballots)?)$",
    ) -> set[UUID]:
        options = self.conn.execute(
            """select o.k, o.id, o.contest_id from contest_option o join changeset c on c.id = o.changeset_id
               where c.pipeline = %s and o.kind = 'candidate' and o.ballot_label ~* %s""",
            [self.manifest["pipeline"], pattern],
        ).fetchall()
        for (candidacy,) in self.conn.execute(
            "select id from candidacy where option_id = any(%s::uuid[])", [[id for _, id, _ in options]]
        ).fetchall():
            self.add("entity_retirement", entity_id=candidacy, reason=reason)
        for option, contest in self.conn.execute(
            """select o.id, o.contest_id from contest_option o
               where o.id = any(%s::uuid[])
                 and not exists (select from candidacy where option_id = o.id)
                 and not exists (select from outcome where option_id = o.id)
                 and not exists (select from result_set r join result_value v on v.result_set_k = r.k
                                 where r.contest_id = o.contest_id and v.option_k = o.k)""",
            [[id for _, id, _ in options]],
        ).fetchall():
            self.add("option_drop", option_id=option, contest_id=contest, reason=reason)
        for (person,) in self.conn.execute(
            """select p.id from person p join changeset c on c.id = p.changeset_id
               join name n on n.entity_id = p.id and n.is_preferred
               where c.pipeline = %s and n.text ~* %s and not exists (select from candidacy where person_id = p.id)""",
            [self.manifest["pipeline"], pattern],
        ).fetchall():
            self.add("entity_retirement", entity_id=person, reason=reason)
        return {
            id
            for (id,) in self.conn.execute(
                """select distinct r.id from result_set r join result_value v on v.result_set_k = r.k
                   where r.status = 'active' and r.contest_id = any(%s::uuid[]) and v.option_k = any(%s)""",
                [[contest for _, _, contest in options], [k for k, _, _ in options]],
            ).fetchall()
        }

    def pool(self, reason: str) -> None:
        pattern = r"^(write-?ins?|scattering|scattered)$"
        staged = {str(row["id"]) for row in self.rows.get("contest_option", [])} | {
            str(row["option_id"]) for row in self.rows.get("option_drop", [])
        }
        options = self.conn.execute(
            """select o.id, o.contest_id, o.parent_option_id, o.party_id, o.alliance_id, o.ballot_position, o.ballot_label
               from contest_option o join changeset c on c.id = o.changeset_id
               where c.pipeline = %s and o.kind = 'candidate' and o.ballot_label ~* %s""",
            [self.manifest["pipeline"], pattern],
        ).fetchall()
        for option, contest, parent, party, alliance, position, label in options:
            if str(option) not in staged:
                written = re.match(r"(?i)write", label) is not None
                self.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="write_in" if written else "others",
                    parent_option_id=parent,
                    party_id=party,
                    alliance_id=alliance,
                    ballot_position=position,
                    ballot_label="Write-ins" if written else "Others",
                )
        retired = {str(row["entity_id"]) for row in self.rows.get("entity_retirement", [])}
        for (candidacy,) in self.conn.execute(
            "select id from candidacy where option_id = any(%s::uuid[])", [[row[0] for row in options]]
        ).fetchall():
            if str(candidacy) not in retired:
                self.add("entity_retirement", entity_id=candidacy, reason=reason)
        for (person,) in self.conn.execute(
            """select p.id from person p join changeset c on c.id = p.changeset_id
               join name n on n.entity_id = p.id and n.is_preferred
               where c.pipeline = %s and n.text ~* %s and not exists (select from candidacy where person_id = p.id)""",
            [self.manifest["pipeline"], pattern],
        ).fetchall():
            if str(person) not in retired:
                self.add("entity_retirement", entity_id=person, reason=reason)

    def clear(self) -> None:
        pattern = r"^(under ?votes?|over ?votes?|blank( votes| ballots)?|spoiled( votes| ballots)?)$"
        listed = [
            id
            for (id,) in self.conn.execute(
                """select distinct r.id from result_set r join changeset w on w.id = r.changeset_id
                   join contest_option o on o.contest_id = r.contest_id and o.kind = 'candidate' and o.ballot_label ~* %s
                   where w.pipeline = %s
                     and (exists (select from result_value v where v.result_set_k = r.k and v.option_k = o.k)
                          or exists (select from outcome t where t.result_set_id = r.id and t.option_id = o.id))""",
                [pattern, self.manifest["pipeline"]],
            ).fetchall()
        ]
        if not listed:
            return
        staged = {
            table: {str(row["result_set_id"]) for row in self.rows.get(table, [])}
            | {
                str(id)
                for frame in [*self.chunks.get(table, []), *self.frames.get(table, [])]
                for id in frame["result_set_id"].to_list()
            }
            for table in ("result_value", "outcome")
        }
        copied: dict[tuple[UUID, UUID, UUID | None, int, int, int, int], int] = defaultdict(int)
        for result_set, unit, option, label, channel, measure, count_round, flags, value in self.conn.execute(
            """select r.id, u.id, o.id, o.ballot_label, v.channel, v.measure, v.count_round, v.flags, v.value
               from result_value v join result_set r on r.k = v.result_set_k join reporting_unit u on u.k = v.unit_k
               left join contest_option o on o.k = v.option_k and v.option_k <> 0
               where r.id = any(%s::uuid[])""",
            [[id for id in listed if str(id) not in staged["result_value"]]],
        ).fetchall():
            if label is not None and re.match(pattern, label, re.IGNORECASE) is not None:
                option, measure = None, 6 if re.match(r"(?i)(under|blank)", label) else 5
            copied[result_set, unit, option, channel, measure, count_round, flags] += value
        for (result_set, unit, option, channel, measure, count_round, flags), value in copied.items():
            self.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=channel,
                measure=measure,
                count_round=count_round,
                flags=flags,
                value=value,
            )
        for result_set, option, candidacy, kind, seats, notes in self.conn.execute(
            """select t.result_set_id, t.option_id, t.candidacy_id, t.kind::text, t.seats, t.notes
               from outcome t join changeset c on c.id = t.changeset_id join contest_option o on o.id = t.option_id
               where t.result_set_id = any(%s::uuid[]) and c.pipeline = %s and o.ballot_label !~* %s""",
            [[id for id in listed if str(id) not in staged["outcome"]], self.manifest["pipeline"], pattern],
        ).fetchall():
            self.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                candidacy_id=candidacy,
                kind=kind,
                seats=seats,
                notes=notes,
            )
        scope = self.manifest.get("scope")
        self.manifest["scope"] = {**(scope if isinstance(scope, dict) else {}), "clears": [str(id) for id in listed]}
        print(f"clears {len(listed)} result sets that still carry ballot remainders")

    def write(self) -> Path:
        folder = DATA / "changesets" / str(self.id)
        folder.mkdir(parents=True)
        tables: dict[str, pl.DataFrame] = {}
        for table, rows in self.rows.items():
            if rows:
                self._chunk(table)
            parts = [*self.chunks.pop(table, []), *self.frames[table]]
            defaults = self.defaults.get(table, {})
            columns = {column for part in parts for column in part.columns} & defaults.keys()
            tables[table] = pl.concat(
                [
                    part.with_columns(pl.lit(defaults[column]).alias(column) for column in columns - set(part.columns))
                    for part in parts
                ],
                how="diagonal_relaxed",
            )
        if "result_set" in tables and "result_value" in tables:
            sets, values = tables["result_set"], tables["result_value"]
            sets = sets.with_columns(
                pl.lit(self.defaults["result_set"].get(column)).alias(column)
                for column in ("kind", "completeness")
                if column not in sets.columns
            )
            counted = sets.filter(pl.col("kind") == "counted")["id"].cast(pl.String)
            zeroed = (
                values.lazy()
                .select(pl.col("result_set_id", "option_id").cast(pl.String), "value")
                .filter(pl.col("result_set_id").is_in(counted.implode()))
                .group_by("result_set_id")
                .agg(pl.col("value").abs().max().alias("most"), pl.col("option_id").unique().alias("options"))
                .filter(pl.col("most") == 0)
                .collect()
            )
            kinds: dict[str, str] = (
                dict(tables["contest_option"].select(pl.col("id", "kind").cast(pl.String)).iter_rows())
                if "contest_option" in tables
                else {}
            )
            unknown = [option for options in zeroed["options"] for option in options if option not in kinds]
            if unknown:
                kinds |= {
                    str(option): kind
                    for option, kind in self.conn.execute(
                        "select id, kind::text from contest_option where id = any(%s::uuid[])", [unknown]
                    )
                }
            declared: dict[str, set[str]] = {}
            if "outcome" in tables:
                for result_set, option in (
                    tables["outcome"]
                    .filter(pl.col("kind").is_in(["elected", "won"]))
                    .select(pl.col("result_set_id", "option_id").cast(pl.String))
                    .iter_rows()
                ):
                    declared.setdefault(result_set, set()).add(option)
            tacit, missing = [], []
            for result_set, options in zeroed.select("result_set_id", "options").iter_rows():
                named = {option for option in options if kinds.get(option) != "write_in"}
                if not named:
                    continue
                if named <= declared.get(result_set, set()):
                    tacit.append(result_set)
                else:
                    missing.append(result_set)
            if tacit or missing:
                identity = pl.col("id").cast(pl.String)
                tables["result_set"] = sets.with_columns(
                    kind=pl.when(identity.is_in(tacit)).then(pl.lit("tacit")).otherwise(pl.col("kind")),
                    completeness=pl.when(identity.is_in(tacit))
                    .then(None)
                    .when(identity.is_in(missing))
                    .then(0)
                    .otherwise(pl.col("completeness") if "completeness" in sets.columns else None),
                )
                if tacit:
                    tables["result_value"] = values.filter(~pl.col("result_set_id").cast(pl.String).is_in(tacit))
        for table, frame in tables.items():
            frame.write_parquet(folder / f"{table}.parquet")
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
